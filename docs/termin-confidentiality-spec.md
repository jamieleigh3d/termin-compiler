# Termin Confidentiality System — Technical Specification

**Author:** Jamie-Leigh Blake & Claude Anthropic
**Version:** 1.0.0
**Date:** April 2026
**Status:** Draft
**Implements:** termin-confidentiality-brd.md

---

## 1. DSL Syntax

### 1.1 Field-Level Confidentiality

```termin
Content called "employees":
  Each employee has a name which is text, required
  Each employee has a department which is text
  Each employee has a salary which is currency, confidentiality is "access_salary"
  Each employee has a ssn which is text, confidentiality is "access_pii"
  Anyone with "view_team" can view employees
  Anyone with "manage_hr" can create or update employees
```

The `confidentiality is "scope_name"` clause declares that the field value is only visible to identities holding the named scope. Multiple fields can share the same confidentiality scope.

### 1.2 Content-Level Confidentiality

```termin
Content called "medical records":
  Scoped to "access_medical"
  Each record has a patient which is text, required
  Each record has a diagnosis which is text
  Each record has a billing code which is text, confidentiality is "access_billing"
```

`Scoped to "scope_name"` sets a default confidentiality for all fields in the Content. Individual fields inherit this scope. A field-level `confidentiality is` can narrow the scope further but never widen beyond the Content scope.

In this example:
- `patient` and `diagnosis` inherit `access_medical` from the Content
- `billing_code` requires BOTH `access_medical` (from Content) AND `access_billing` (from field)

### 1.3 Compute Scope Requirements

```termin
Compute called "Calculate Bonus Pool":
  Transform: takes employees, produces bonus_summary
  Requires "access_salary"
  [bonus_pool = sum(employees.salary * bonus_rate)]
```

The `Requires "scope_name"` clause declares that this Compute accesses fields gated by the named scope. The compiler verifies this declaration matches the actual field access in the CEL body. Missing or incorrect declarations are compile errors.

### 1.4 Service Identity Declaration

```termin
Compute called "Calculate Bonus Pool":
  Transform: takes employees, produces bonus_summary
  Identity: service
  Requires "access_salary"
  Output confidentiality: "view_team_metrics"
  [bonus_pool = sum(employees.salary * bonus_rate)]
```

`Identity: service` declares that this Compute runs with elevated privileges (its own service identity, not the caller's). The service identity must hold the **union** of all `Requires` scopes AND all `Output confidentiality` scopes.

`Output confidentiality: "scope_name"` is an explicit **reclassification**. It declares that the output carries a different confidentiality scope than the inputs. The system does not rank scopes as higher or lower — they are simply different. Without this declaration, the entire output inherits the input taint scope and is only accessible to identities holding that scope.

### 1.5 Default Identity: Delegate

All Compute modules default to `Identity: delegate`. In delegate mode:
- The Compute runs with the caller's identity
- The caller's scopes are checked against the Compute's `Requires` at the Channel boundary
- The output inherits the caller's identity for downstream confidentiality checks

---

## 2. Intermediate Representation

### 2.1 FieldSpec Changes

```python
@dataclass(frozen=True)
class FieldSpec:
    name: str
    display_name: str
    business_type: str = "text"
    column_type: FieldType = FieldType.TEXT
    required: bool = False
    unique: bool = False
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    enum_values: tuple[str, ...] = ()
    foreign_key: Optional[str] = None
    is_auto: bool = False
    list_type: Optional[str] = None
    # NEW: confidentiality
    confidentiality_scope: Optional[str] = None  # scope required to see this field
```

### 2.2 ContentSchema Changes

```python
@dataclass(frozen=True)
class ContentSchema:
    name: QualifiedName
    fields: tuple[FieldSpec, ...]
    storage_intent: str = "auto"
    has_state_machine: bool = False
    initial_state: Optional[str] = None
    # NEW: content-level confidentiality
    confidentiality_scope: Optional[str] = None  # inherited by fields without their own
```

### 2.3 ComputeSpec Changes

```python
@dataclass(frozen=True)
class ComputeSpec:
    name: QualifiedName
    shape: ComputeShape
    input_content: tuple[str, ...]
    output_content: tuple[str, ...]
    body_lines: tuple[str, ...] = ()
    required_scope: Optional[str] = None
    required_role: Optional[str] = None
    input_params: tuple[ComputeParamSpec, ...] = ()
    output_params: tuple[ComputeParamSpec, ...] = ()
    # NEW: confidentiality
    identity_mode: str = "delegate"              # "delegate" or "service"
    required_confidentiality_scopes: tuple[str, ...] = ()  # scopes for input fields
    output_confidentiality_scope: Optional[str] = None     # explicit reclassification
    field_dependencies: tuple[FieldDependency, ...] = ()   # compiler-resolved
```

### 2.4 New IR Types

```python
@dataclass(frozen=True)
class FieldDependency:
    """A resolved field access in a Compute body, with confidentiality metadata."""
    content_name: str                  # "employees"
    field_name: str                    # "salary"
    confidentiality_scope: Optional[str] = None  # "access_salary"

@dataclass(frozen=True)
class ReclassificationPoint:
    """Records an explicit confidentiality downgrade for audit."""
    compute_name: str                  # "Calculate Bonus Pool"
    input_scopes: tuple[str, ...]      # ("access_salary",)
    output_scope: str                  # "view_team_metrics"
```

### 2.5 AppSpec Changes

```python
@dataclass(frozen=True)
class AppSpec:
    # ... existing fields ...
    # NEW:
    reclassification_points: tuple[ReclassificationPoint, ...] = ()
```

---

## 3. Compiler Changes

### 3.1 PEG Grammar Additions

```peg
# In field_clause constraints:
constraint
    = 'required'                             #Required
    | 'unique'                               #Unique
    | 'minimum' val:number                   #Minimum
    | 'maximum' val:number                   #Maximum
    | 'confidentiality' 'is' scope:quoted_string  #Confidentiality
    ;

# Content-level scope:
content_scope_line
    = 'Scoped' 'to' scope:quoted_string $
    ;

# Compute identity and output confidentiality:
compute_identity_line
    = 'Identity:' mode:('delegate'|'service') $
    ;

compute_output_conf_line
    = 'Output' 'confidentiality:' scope:quoted_string $
    ;
```

### 3.2 AST Node Changes

```python
@dataclass
class TypeExpr:
    # ... existing fields ...
    confidentiality_scope: Optional[str] = None  # NEW

@dataclass
class Content:
    # ... existing fields ...
    confidentiality_scope: Optional[str] = None  # NEW

@dataclass
class ComputeNode:
    # ... existing fields ...
    identity_mode: str = "delegate"              # NEW
    output_confidentiality: Optional[str] = None # NEW
```

### 3.3 Static Field Dependency Analysis

The compiler performs a resolution pass over Compute CEL bodies:

**Step 1: Extract field references.** Parse each CEL expression to identify property access patterns. `employees.salary` → `(content: "employees", field: "salary")`. Nested access like `order.customer.email` → `(content: "customers", field: "email")` via reference chain resolution.

**Step 2: Resolve confidentiality.** For each field reference, look up the field's `confidentiality_scope` (or the Content's if the field doesn't override).

**Step 3: Build dependency set.** Collect all unique confidentiality scopes across all field references in the Compute body.

**Step 4: Validate declarations.** Compare the dependency set against the Compute's `Requires` declarations. Any scope in the dependency set that is not in `Requires` is a compile error:

```
Error: Compute "Calculate Bonus Pool" accesses field "employees.salary"
which requires scope "access_salary", but the Compute does not declare
Requires "access_salary".
```

**Step 5: Validate service identity scopes.** If `Identity: service`, verify the service identity's scopes include the union of `Requires` scopes AND `Output confidentiality` scope. If not:

```
Error: Compute "Calculate Bonus Pool" with Identity: service requires
scopes ["access_salary", "view_team_metrics"] but service identity
does not declare all of them.
```

**Step 6: Record reclassification.** If `Output confidentiality` is declared and is narrower than the input confidentiality, emit a `ReclassificationPoint` in the IR.

### 3.4 Taint Propagation Rule

**Default:** Output confidentiality = max(input field confidentialities). "Max" means the most restrictive — if any input is `access_pii`, the output is `access_pii`.

**Explicit reclassification:** `Output confidentiality: "other_scope"` overrides the default. This is recorded as a `ReclassificationPoint` for audit.

**No implicit reclassification.** A Compute cannot produce output at a different confidentiality scope than its inputs unless explicitly declared. The compiler enforces this.

---

## 4. Runtime Changes

### 4.1 Field Redaction at Channel Boundaries

When a record dict is about to cross a Channel boundary (API response, WebSocket push, Compute input, Presentation rendering), the runtime applies redaction:

```python
def redact_record(record: dict, schema: ContentSchema, caller_scopes: set[str]) -> dict:
    """Replace restricted field values with redaction markers."""
    result = {}
    content_scope = schema.confidentiality_scope
    for key, value in record.items():
        field_spec = schema.fields_by_name.get(key)
        if field_spec is None:
            result[key] = value  # non-schema fields pass through (id, status)
            continue

        required_scope = field_spec.confidentiality_scope or content_scope
        if required_scope and required_scope not in caller_scopes:
            result[key] = {"__redacted": True, "scope": required_scope}
        else:
            result[key] = value
    return result
```

### 4.2 Compute Invocation Gate

Before any Compute executes, the Channel enforcer checks:

```python
def check_compute_access(compute_spec, caller_identity):
    """Reject Compute invocation if caller lacks required scopes."""
    if compute_spec.identity_mode == "service":
        # Service Compute — caller just needs invoke permission, not field scopes
        return True

    # Delegate mode — caller must have all required confidentiality scopes
    for scope in compute_spec.required_confidentiality_scopes:
        if scope not in caller_identity["scopes"]:
            raise TerminError(
                source=compute_spec.name.display,
                kind="confidentiality",
                message=f"Requires scope '{scope}' — caller identity lacks it"
            )
    return True
```

### 4.3 CEL Redaction Guard

The Expression Evaluator wraps property access to detect redaction markers:

```python
class RedactionAwareEvaluator:
    def evaluate(self, expression, context):
        result = cel.eval(expression, context)
        # Check if any intermediate value was a redaction marker
        # that leaked into the result
        self._check_for_redacted(result)
        return result

    def _check_for_redacted(self, value):
        if isinstance(value, dict) and value.get("__redacted"):
            raise RedactedFieldAccess(
                field=value.get("field"),
                scope=value.get("scope"),
            )
```

`RedactedFieldAccess` errors route through TerminAtor and are logged for audit.

### 4.4 Output Redaction for Service Compute

When a service-identity Compute completes, its output is checked against the **caller's** confidentiality:

```python
def apply_output_confidentiality(output, compute_spec, delegate_scopes):
    """Ensure service Compute output respects taint propagation.

    The entire output is tainted at the max input confidentiality.
    No field-by-field tracing — the whole blob carries the taint.
    """
    output_scope = compute_spec.output_confidentiality_scope
    if output_scope:
        # Explicit reclassification — output carries the declared scope
        if output_scope not in delegate_scopes:
            raise TerminError(
                source=compute_spec.name.display,
                kind="confidentiality",
                message=f"Reclassified output requires scope '{output_scope}'"
            )
        return output  # Delegate has the reclassified scope

    # No reclassification — entire output is at input taint scope
    for scope in compute_spec.required_confidentiality_scopes:
        if scope not in delegate_scopes:
            # Delegate lacks the input taint scope and no
            # reclassification was declared — block entire output
            raise TerminError(
                source=compute_spec.name.display,
                kind="confidentiality",
                message=f"Output tainted by scope '{scope}' — "
                        f"declare Output confidentiality to reclassify"
            )
    return output
```

### 4.5 Presentation Rendering of Redacted Fields

The component tree renderer handles redacted values gracefully:

- **data_table cells:** Show `[REDACTED]` in gray italic text
- **text components:** Show `[REDACTED]` if the expression result is redacted
- **form field_inputs:** Omit the field entirely (don't show a redacted input)
- **aggregations:** Show `[RESTRICTED]` if the source data requires unavailable scopes

---

## 5. Redaction Marker Format

### 5.1 In API Responses (JSON)

```json
{
  "id": 42,
  "name": "Alice Chen",
  "department": "Engineering",
  "salary": { "__redacted": true, "scope": "access_salary" },
  "ssn": { "__redacted": true, "scope": "access_pii" }
}
```

### 5.2 In WebSocket Push Frames

Same format. The frame payload includes redacted markers. The client runtime recognizes `__redacted` and renders accordingly.

### 5.3 Detection Rules

A value is redacted if and only if:
- It is a dict/object
- It has a `__redacted` key with value `true`

The `scope` key is informational (for error messages and audit). The runtime never makes access decisions based on the scope key in a redacted marker — those decisions happen at Channel boundaries before the marker is created.

---

## 6. Compile-Time Dependency Graph

The compiler produces a `confidentiality_graph` section in the IR:

```json
{
  "confidentiality_graph": {
    "fields": {
      "employees.salary": { "scope": "access_salary" },
      "employees.ssn": { "scope": "access_pii" },
      "medical_records.*": { "scope": "access_medical" }
    },
    "computes": {
      "calculate_bonus_pool": {
        "reads": ["employees.salary"],
        "required_scopes": ["access_salary"],
        "identity_mode": "service",
        "output_scope": "view_team_metrics",
        "declassifies": true
      }
    },
    "reclassification_points": [
      {
        "compute": "calculate_bonus_pool",
        "input_scopes": ["access_salary"],
        "output_scope": "view_team_metrics"
      }
    ]
  }
}
```

This graph is:
- Used by the runtime for fast scope checking (no CEL re-analysis needed)
- Queryable via Reflection for operational visibility
- Reviewable by AppSec teams to audit all confidentiality decisions
- Exportable for compliance documentation

---

## 7. Security Properties

### 7.1 Guaranteed (Tier 1)

1. **No field leakage through API.** Every API response passes through `redact_record`. Fields with confidentiality scopes not held by the caller are redacted.

2. **No field leakage through Compute.** Compute modules cannot access redacted fields at runtime. Compile-time analysis catches missing scope declarations. Runtime CEL guards catch anything the compiler missed.

3. **No implicit reclassification.** Entire Compute output inherits input taint scope. Producing output at a different scope requires explicit DSL declaration.

4. **No scope escalation through service identity.** Service identity Compute requires the union of input scopes and output scopes. The compiler verifies this.

### 7.2 Not Guaranteed

1. **Side-channel inference.** A user who can see `bonus_pool` for a team of one can infer the individual's salary. Taint propagation ensures `bonus_pool` inherits `access_salary` scope by default, but explicit reclassification bypasses this. The reclassification is a business decision, not a technical guarantee.

2. **Provider behavior.** Custom Compute providers (Tier 2/3) execute opaque code. The runtime validates inputs and outputs but cannot inspect what happens inside `execute()`.

3. **Timing attacks.** Redacted vs. non-redacted responses may differ in size or timing. Mitigating this is a future concern.
