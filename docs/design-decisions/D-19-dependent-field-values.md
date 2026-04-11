# D-19: Dependent Field Values & Unified `is one of` Constraint

**Status:** DECIDED 2026-04-10
**Decided by:** JL + Claude
**Affects:** Grammar, parser, AST, IR, lowering, analyzer, runtime (validation + presentation)

---

## Decision

### 1. `is one of` is a constraint, not a type

Every field has a base type (`text`, `number`, `whole number`, `currency`, `enum`, etc.). `is one of` is an optional constraint that restricts allowed values. It works with any base type:

```
Each order has a size which is an enum, is one of: "14-inch", "16-inch"
Each order has a ram which is a whole number, is one of: 8, 16, 24, 32, 48
Each order has a color which is text, is one of: "silver", "space gray"
```

**Shorthand:** `which is one of: "x", "y"` desugars to `which is an enum, is one of: "x", "y"`.

The literal list must match the base type — numbers for number types, strings for text/enum. No mixing. Compiler validates consistency.

### 2. Dependent values with `When` clauses

Conditional constraints declared inside Content blocks, after field definitions:

```
Content called "laptop orders":
  Each order has a size which is an enum, is one of: "14-inch", "16-inch"
  Each order has a ram which is a whole number
  Each order has a storage which is a whole number
  Anyone with "orders.write" can create laptop orders

  When `size == "14-inch"`, ram must be one of: 8, 16, 24
  When `size == "16-inch"`, ram must be one of: 16, 32, 48
  When `size == "16-inch"`, color defaults to "space gray"
  When `tier == "free"`, max instances must be 1
```

### 3. Three verbs

| Verb | What it does | `then` value |
|------|-------------|-------------|
| `must be one of:` | Constrains to a list | Literal list (numbers or strings) |
| `must be` | Forces a single value | Single literal |
| `defaults to` | Sets initial value (overridable) | Single literal or CEL expression |

### 4. Unconditional constraints

`must be one of` works without a `When` clause as a field-level constraint, equivalent to `is one of` on the field definition:

```
ram must be one of: 8, 16, 24, 32, 48
```

### 5. Evaluation timing

- **Client-side (form interaction):** Conditions evaluated on every field change. UI dynamically updates dropdowns, disables invalid options.
- **Server-side (create/update):** All matching conditions evaluated. Request rejected if constraint violated.
- Declared once in DSL, compiled to IR, used by both layers.

### 6. Ordering

Declaration order does not matter. Rules are a bag, not a sequence. All matching conditions are evaluated — same input always produces same result.

### 7. Exhaustiveness warnings

Compiler warns when an enum field has `When` clauses that don't cover all enum values. Example: `size` has three values but only two have `When` rules for `ram` — the third value has no constraint, which may be intentional or a bug. Warning, not error. Only applies to simple equality on enum fields — complex CEL conditions are not checked.

---

## IR Shape

```json
{
  "dependent_values": [
    {
      "when": "size == \"14-inch\"",
      "field": "ram",
      "constraint": "one_of",
      "values": [8, 16, 24]
    },
    {
      "when": "region == \"EU\"",
      "field": "compliance",
      "constraint": "default",
      "value": "GDPR"
    },
    {
      "when": "tier == \"free\"",
      "field": "max_instances",
      "constraint": "equals",
      "value": 1
    }
  ]
}
```

The `when` field is a CEL expression string (same format as event conditions and preconditions). The runtime evaluates it against the record's current field values.

## Grammar Changes

Field type expression becomes: base type + optional comma-separated modifiers:

```
field_type_expr = base_type {',' modifier}* ;
modifier = 'required' | 'is one of:' literal_list | 'minimum' number | 'maximum' number | ... ;
```

Content-level `When` clauses:

```
content_when_line = 'When' condition:expr ',' field:word verb:constraint_verb values:literal_or_list $ ;
constraint_verb = 'must be one of:' | 'must be' | 'defaults to' ;
```
