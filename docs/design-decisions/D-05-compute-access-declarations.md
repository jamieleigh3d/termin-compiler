# D-05: Compute Access Declarations

**Status:** DECIDED 2026-04-10
**Decided by:** JL + Claude
**Affects:** Grammar, parser, AST, IR, lowering, analyzer, runtime

---

## Decision

Every Compute declares what Content types it accesses via a required `Accesses` line. This serves three purposes:

1. **Boundary declaration** — defines the data scope of the Compute
2. **Compiler cross-check** — validates that field wiring, CEL body references, and agent tool calls only touch declared content
3. **Runtime enforcement** — for agents, the ComputeContext restricts tool calls to declared content types

### Syntax

Simple (same-application content):
```
Accesses messages
Accesses messages and findings
Accesses messages, findings, and scan runs
```

With type alias (for clarity when content name differs from how it's used):
```
Accesses employee : employees
```

Future — cross-boundary path (v1.0, multi-module):
```
Accesses employees : ../hr-module/employees
Accesses config : /org/settings
```

The path syntax is accepted by the grammar from v0.5 but the compiler only resolves local content names until multi-file composition is implemented.

### Required for All Providers

`Accesses` is required on every Compute, regardless of provider. This is a universal concept, not provider-specific.

| Provider | What `Accesses` enables |
|----------|------------------------|
| `"cel"` (default) | Compiler validates that CEL body expressions only reference declared content types. Error if body references undeclared content. |
| `"llm"` | Compiler validates that `Input from field` and `Output into field` references are within declared content. Cross-check at compile time. |
| `"ai-agent"` | Runtime restricts ComputeContext tools (content.query, content.create, etc.) to declared content types. Agent cannot touch undeclared content even if its role scopes would allow it. |

### Relationship to Input/Output Field Wiring

For Level 1 LLM, the `Accesses` line and the field wiring are cross-checked:

```
Compute called "complete":
  Provider is "llm"
  Accesses completions
  Input from field completion.prompt
  Output into field completion.response
  Trigger on event "completion.created"
  Directive is ```
    You are a helpful assistant.
  ```
  Objective is ```
    Answer the user's prompt.
  ```
```

The compiler verifies:
- `completion.prompt` references the `completions` content type (via singular "completion") — OK, declared in Accesses
- `completion.response` references `completions` — OK
- If a field reference pointed to `tickets.title`, compiler error: "Compute 'complete' references 'tickets' which is not in its Accesses declaration"

### Relationship to CEL Body

For CEL Computes, the compiler scans the CEL body for content references and validates against Accesses:

```
Compute called "calculate bonus":
  Accesses employees and salary bands
  Transform: takes employee : employees, produces employee : employees
  `employee.bonus = salary_bands.find(b, b.level == employee.level).rate * employee.salary`
```

The CEL body references `salary_bands` — the compiler checks that `salary bands` (which snake-cases to `salary_bands`) is in the Accesses list. If not, compiler error.

### Relationship to Agent Scope

For agents, Accesses is the least-privilege boundary. Even with `Mode is "service"` (all scopes), the agent can only touch what Accesses declares:

```
Compute called "scanner":
  Provider is "ai-agent"
  Accesses findings and scan runs
  Identity: service
  Trigger on schedule every 1 hour
  ...
```

The scanner has service identity (all scopes) but can only query/create/update findings and scan runs. It cannot touch employees, salary data, or anything else. Accesses is the inner boundary; role scopes are the outer boundary. Both must allow the operation.

### State Machines

Accessing a content type implies access to its state machines. If you declare `Accesses findings`, the agent can call `state.transition("findings", id, "analyzing")` — provided the state machine's transition rules allow it for the agent's role.

---

## Examples

### Level 1 LLM — single content
```
Compute called "complete":
  Provider is "llm"
  Accesses completions
  Input from field completion.prompt
  Output into field completion.response
  Trigger on event "completion.created"
  Directive is ```
    You are a helpful assistant.
  ```
  Objective is ```
    Answer the user's prompt.
  ```
  Anyone with "agent.use" can execute this
```

### Level 3 Agent — multiple content types
```
Compute called "reply":
  Provider is "ai-agent"
  Accesses messages
  Trigger on event "message.created" where `message.role == "user"`
  Directive is ```
    You are a conversational assistant.
  ```
  Objective is ```
    Reply to the user's latest message. Load history with
    content.query("messages"). Create your reply with
    content.create("messages", {"role": "assistant", "body": your_reply}).
  ```
  Anyone with "chat.use" can execute this
```

### CEL Transform — cross-content reference
```
Compute called "enrich order":
  Accesses orders and products
  Transform: takes order : orders, produces order : orders
  `order.product_name = products.find(p, p.id == order.product_id).name`
  Anyone with "orders.write" can execute this
```

### Future — cross-boundary path
```
Compute called "sync employee":
  Provider is "ai-agent"
  Accesses employees : ../hr-module/employees
  Accesses local copy : employees
  ...
```

---

## Grammar Changes

New line type:
```
compute_accesses_line = 'Accesses' items:access_list $ ;
access_list = first:access_item {',' access_item}* ['and' access_item] ;
access_item = [alias:word ':'] ref:access_ref ;
access_ref = path:(/[\w.\/]+/) ;
```

The `access_ref` allows dotted paths and slash-separated module paths for future cross-boundary resolution. For v0.5, only local content names (single word or multi-word) are resolved.

## IR Changes

New field on ComputeSpec:
```python
accesses: tuple[ComputeAccessSpec, ...] = ()
```

Where:
```python
@dataclass(frozen=True)
class ComputeAccessSpec:
    content_ref: str          # resolved snake_case content name (local)
    alias: str = ""           # optional alias for the Compute's scope
    path: str = ""            # future: boundary path (../module/content)
```

## Analyzer Changes

- Validate that every content name in `Accesses` resolves to a declared Content type
- Cross-check `Input from field` / `Output into field` references against Accesses
- Cross-check CEL body content references against Accesses (when body analysis is implemented)
- Error if a Compute has no `Accesses` line

## Transform Shapes Superseded

The five Compute shapes (Transform, Reduce, Expand, Correlate, Route) are superseded by the combination of `Accesses`, `Input from field`, `Output into field`, and `Output creates`. The shapes attempted to describe data flow patterns that are now expressed more precisely by explicit field wiring:

| Old shape | New equivalent |
|-----------|---------------|
| `Transform: takes X, produces X` | `Accesses X` + `Input from field X.a` + `Output into field X.b` |
| `Reduce: takes Xs, produces Y` | `Accesses Xs and Ys` + `Output into field Y.total` |
| `Expand: takes X, produces Ys` | `Accesses X and Ys` + `Input from field X.a` + `Output creates Y` |
| `Correlate: takes Xs and Ys, produces Zs` | `Accesses Xs, Ys, and Zs` + `Output creates Z` |
| `Route: takes X, produces one of Y or Z` | `Accesses X, Y, and Z` + CEL body + conditional output |

The old `Transform:` line is retained in the grammar for backward compatibility during the pre-v1.0 period but is no longer required. New examples should use `Accesses` + field wiring instead. The `Transform:` line will be removed before v1.0.
