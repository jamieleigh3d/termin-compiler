# Confidentiality System — Reference Runtime Technical Design

**Author:** Claude Anthropic & Jamie-Leigh Blake
**Version:** 1.0.0
**Date:** April 2026
**Implements:** termin-confidentiality-brd.md, termin-confidentiality-spec.md
**Target:** termin_runtime package (reference runtime)

---

## 1. Scope

This document describes the implementation plan for the confidentiality system in the **reference runtime** (`termin_runtime/`). It covers:

- IR dataclass changes (compiler-side, `termin/ir.py`)
- PEG grammar and parser additions (compiler-side)
- Analyzer static analysis pass (compiler-side)
- Lowering pass changes (compiler-side)
- Runtime field redaction (runtime-side)
- Compute invocation gating (runtime-side)
- CEL redaction guard (runtime-side)
- Output taint enforcement (runtime-side)
- Presentation rendering of redacted values (runtime-side)
- API response redaction (runtime-side)
- WebSocket frame redaction (runtime-side)

---

## 2. Design Decisions (Requiring Confirmation)

### D1: Content + Field Scope Intersection

**Proposed:** When a Content has `Scoped to "access_medical"` and a field has `confidentiality is "access_billing"`, the field requires **both** scopes (AND semantics). The caller must hold `access_medical` AND `access_billing` to see the field unredacted.

**Rationale:** "Narrow but never widen" means a field can only add restrictions, not remove the Content-level restriction. This is the only interpretation consistent with defense-in-depth.

**Implementation:** `effective_scope(field) = {content_scope, field_scope} - {None}`. Redaction fires if caller is missing *any* scope in the set.

### D2: Reclassification = Different, Not "Narrower"

**Proposed:** Any `Output confidentiality` that differs from the input taint scope is a reclassification. The compiler does not rank scopes. The word "narrower" in the spec should be read as "different from."

**Implementation:** `if output_scope != max_input_scope: emit ReclassificationPoint`.

### D3: One Scope Per Field

**Proposed:** A field declares at most one `confidentiality is "scope"`. Multiple scopes on a single field are not supported. If a field needs multiple scopes, compose them into a single scope name (e.g., `"access_salary_and_hr"`).

**Rationale:** Keeps the DSL simple. Content-level scope already provides the second layer when needed (D1).

### D4: System Fields Pass Through

**Proposed:** `id`, `status`, `created_at` and other system-managed fields are never redacted. They are structural and necessary for the client to render table rows, manage state, and construct URLs. If the *existence* of a record needs protection, that's row-level security (out of scope per BRD § 5).

### D5: List Endpoints Redact Field-by-Field

**Proposed:** API list endpoints return all records the user can see (based on access grants). Within each record, confidential fields are replaced with `{"__redacted": true, "scope": "..."}`. The record itself is never omitted — the client always sees the record shape.

### D6: Update Forms Preserve Redacted Values

**Proposed:** When a user submits an update form that omits confidential fields (because the form didn't render them), the runtime preserves the existing values for those fields. The update handler merges submitted fields with current values, never overwriting a field that wasn't in the submitted payload.

**Implementation:** On form POST or API PATCH/PUT, load the current record, merge only the fields present in the request body, preserve all others.

---

## 3. Implementation Plan

### Phase B1: IR + Grammar + Parser (Compiler)

#### 3.1.1 PEG Grammar Additions

Add to `termin.peg`:

```peg
# Field constraint
confidentiality_constraint = 'confidentiality' 'is' scope:quoted_string ;

# Content-level line (inside Content block)
content_scope_line = 'Scoped' 'to' scope:quoted_string ;

# Compute body lines
compute_identity_line = 'Identity:' mode:ident ;
compute_requires_line = 'Requires' scope:quoted_string ;
compute_output_conf_line = 'Output' 'confidentiality:' scope:quoted_string ;
```

#### 3.1.2 Parser Line Classification

Add to `_classify_line()` in `peg_parser.py`:

| Prefix | Classification |
|--------|---------------|
| `Scoped to ` | `content_scope_line` |
| `Identity: ` | `compute_identity_line` |
| `Output confidentiality:` | `compute_output_conf_line` |
| `Requires "` | `compute_requires_line` |

The `confidentiality is` constraint is handled inline during field constraint parsing (same as `required`, `unique`, etc.).

#### 3.1.3 AST Node Changes

```python
# ast_nodes.py
@dataclass
class TypeExpr:
    # ... existing ...
    confidentiality_scope: Optional[str] = None  # NEW

@dataclass
class Content:
    # ... existing ...
    confidentiality_scope: Optional[str] = None  # NEW

@dataclass
class ComputeNode:
    # ... existing ...
    identity_mode: str = "delegate"              # NEW: "delegate" or "service"
    required_confidentiality_scopes: list = None  # NEW
    output_confidentiality: Optional[str] = None # NEW
```

#### 3.1.4 IR Dataclass Changes

```python
# ir.py
@dataclass(frozen=True)
class FieldSpec:
    # ... existing ...
    confidentiality_scope: Optional[str] = None  # scope required to see this field

@dataclass(frozen=True)
class ContentSchema:
    # ... existing ...
    confidentiality_scope: Optional[str] = None  # inherited by fields without their own

@dataclass(frozen=True)
class FieldDependency:
    """A resolved field access in a Compute body."""
    content_name: str
    field_name: str
    confidentiality_scope: Optional[str] = None

@dataclass(frozen=True)
class ReclassificationPoint:
    """An explicit confidentiality scope change for audit."""
    compute_name: str
    input_scopes: tuple[str, ...]
    output_scope: str

@dataclass(frozen=True)
class ComputeSpec:
    # ... existing ...
    identity_mode: str = "delegate"
    required_confidentiality_scopes: tuple[str, ...] = ()
    output_confidentiality_scope: Optional[str] = None
    field_dependencies: tuple[FieldDependency, ...] = ()

@dataclass(frozen=True)
class AppSpec:
    # ... existing ...
    reclassification_points: tuple[ReclassificationPoint, ...] = ()
```

#### 3.1.5 JSON Schema Update

Add `confidentiality_scope` to FieldSpec and ContentSchema definitions. Add `identity_mode`, `required_confidentiality_scopes`, `output_confidentiality_scope`, `field_dependencies` to ComputeSpec. Add `reclassification_points` to top-level AppSpec. Add `FieldDependency` and `ReclassificationPoint` definitions.

### Phase B2: Analyzer Static Analysis (Compiler)

#### 3.2.1 Confidentiality Scope Validation

- Verify that `confidentiality is "X"` references a declared scope
- Verify that `Scoped to "X"` references a declared scope
- Verify field scope does not widen beyond Content scope (if Content scope is set)

#### 3.2.2 Compute Field Dependency Analysis

For each Compute with a CEL body:

1. **Extract field references** — Parse CEL body to find `content.field` patterns
2. **Resolve confidentiality** — Look up each referenced field's effective scope
3. **Build dependency set** — Collect all unique scopes
4. **Validate `Requires`** — Every scope in dependency set must appear in Compute's `Requires` declarations
5. **Validate service identity** — If `Identity: service`, service must hold union of `Requires` + `Output confidentiality`
6. **Emit reclassification** — If `Output confidentiality` differs from max input scope

**Error examples:**
```
Error: Compute "Calculate Bonus Pool" accesses field "employees.salary"
which requires scope "access_salary", but does not declare Requires "access_salary".

Error: Compute "Calculate Bonus Pool" with Identity: service requires
scopes ["access_salary", "view_team_metrics"] but not all are declared.
```

### Phase B3: Lowering Pass (Compiler)

Thread new fields through `lower()`:

- `FieldSpec.confidentiality_scope` from `TypeExpr.confidentiality_scope`
- `ContentSchema.confidentiality_scope` from `Content.confidentiality_scope`
- `ComputeSpec.identity_mode` from `ComputeNode.identity_mode`
- `ComputeSpec.required_confidentiality_scopes` from `ComputeNode.required_confidentiality_scopes`
- `ComputeSpec.output_confidentiality_scope` from `ComputeNode.output_confidentiality`
- `ComputeSpec.field_dependencies` from analyzer resolution
- `AppSpec.reclassification_points` from analyzer resolution

### Phase B4: Runtime Field Redaction

#### 3.4.1 New Module: `termin_runtime/confidentiality.py`

Central module for all confidentiality enforcement:

```python
def effective_scopes(field_spec, content_schema) -> set[str]:
    """Return the set of scopes required to see this field."""
    scopes = set()
    if content_schema.confidentiality_scope:
        scopes.add(content_schema.confidentiality_scope)
    if field_spec.confidentiality_scope:
        scopes.add(field_spec.confidentiality_scope)
    return scopes

def redact_record(record: dict, schema: ContentSchema, caller_scopes: set[str]) -> dict:
    """Replace restricted field values with redaction markers."""
    result = {}
    fields_by_name = {f.name: f for f in schema.fields}
    for key, value in record.items():
        field_spec = fields_by_name.get(key)
        if field_spec is None:
            result[key] = value  # system fields (id, status) pass through
            continue
        required = effective_scopes(field_spec, schema)
        if required and not required.issubset(caller_scopes):
            result[key] = {"__redacted": True, "scope": sorted(required)[0]}
        else:
            result[key] = value
    return result

def redact_records(records: list[dict], schema: ContentSchema,
                   caller_scopes: set[str]) -> list[dict]:
    """Redact a list of records."""
    return [redact_record(r, schema, caller_scopes) for r in records]

def is_redacted(value) -> bool:
    """Check if a value is a redaction marker."""
    return isinstance(value, dict) and value.get("__redacted") is True
```

#### 3.4.2 Hook Into API Handlers

In `app.py`, after every query and before returning JSON:

- **List route:** `records = redact_records(records, schema, user["scopes"])`
- **Get-one route:** `record = redact_record(record, schema, user["scopes"])`
- **Create route:** No redaction needed (user is providing data)
- **Update route:** Merge strategy — load current, apply only submitted fields, preserve redacted fields

Schema lookup: Build `schemas_by_name: dict[str, ContentSchema]` at startup from IR. The redaction function needs the `ContentSchema` to know which fields have confidentiality.

#### 3.4.3 Hook Into WebSocket

In the WebSocket broadcast, redact records before sending frames. Each subscriber has an identity — use their scopes for redaction.

#### 3.4.4 Hook Into Presentation

In page rendering, pass redacted records to templates. The Jinja2 templates check for `__redacted` markers:

- **Table cells:** `{% if value is mapping and value.__redacted %}[REDACTED]{% else %}{{ value }}{% endif %}`
- **Form fields:** `{% if not field_redacted %}{{ render_input(field) }}{% endif %}`
- **Text components:** Same pattern as table cells
- **Aggregations:** If any source record has redacted fields that the aggregation expression accesses, show `[RESTRICTED]`

### Phase B5: Compute Invocation Gate (Runtime)

#### 3.5.1 Pre-Execution Check

Before any Compute executes:

```python
def check_compute_access(compute_ir: dict, caller_identity: dict):
    """Reject if caller lacks required confidentiality scopes."""
    if compute_ir.get("identity_mode") == "service":
        return  # Service mode — caller just needs invoke permission
    for scope in compute_ir.get("required_confidentiality_scopes", []):
        if scope not in caller_identity.get("scopes", []):
            raise TerminError(
                source=compute_ir["name"]["display"],
                kind="confidentiality_gate_rejected",
                message=f"Requires scope '{scope}'"
            )
```

#### 3.5.2 Taint Integrity Check (Check 2)

When data arrives at Compute input with a delegate identity:

```python
def check_taint_integrity(input_data, schema, delegate_scopes):
    """Detect unredacted confidential fields for unauthorized delegate."""
    for record in input_data:
        for field_spec in schema.fields:
            required = effective_scopes(field_spec, schema)
            if required and not required.issubset(delegate_scopes):
                value = record.get(field_spec.name)
                if value is not None and not is_redacted(value):
                    raise TerminError(
                        source="confidentiality",
                        kind="taint_violation",
                        message=f"Unredacted field '{field_spec.name}' for unauthorized delegate"
                    )
```

### Phase B6: CEL Redaction Guard (Runtime)

#### 3.6.1 Expression Evaluator Extension

Modify `termin_runtime/expression.py` to detect redaction markers during evaluation:

```python
def _check_for_redacted(value):
    """Recursively check if a value contains redaction markers."""
    if isinstance(value, dict):
        if value.get("__redacted"):
            raise TerminError(
                source="expression",
                kind="redacted_field_access",
                message=f"Expression accessed redacted field (scope: {value.get('scope')})"
            )
        for v in value.values():
            _check_for_redacted(v)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_for_redacted(item)
```

Wrap the result of every `ExpressionEvaluator.evaluate()` call with this check. Also wrap context values before CEL evaluation to detect redacted inputs early.

### Phase B7: Output Taint Enforcement (Runtime)

After a service-identity Compute completes:

```python
def enforce_output_taint(output, compute_ir, delegate_scopes):
    """Block or pass Compute output based on taint and reclassification."""
    output_scope = compute_ir.get("output_confidentiality_scope")
    if output_scope:
        # Explicit reclassification
        if output_scope not in delegate_scopes:
            raise TerminError(
                source=compute_ir["name"]["display"],
                kind="output_scope_rejected",
                message=f"Reclassified output requires scope '{output_scope}'"
            )
        return output  # Delegate has the reclassified scope

    # No reclassification — entire output tainted by input scopes
    for scope in compute_ir.get("required_confidentiality_scopes", []):
        if scope not in delegate_scopes:
            raise TerminError(
                source=compute_ir["name"]["display"],
                kind="output_taint_blocked",
                message=f"Output tainted by scope '{scope}' — "
                        "declare Output confidentiality to reclassify"
            )
    return output
```

### Phase B8: TerminAtor Integration

All confidentiality errors route through TerminAtor with structured context:

| Error Kind | Source | HTTP Status |
|-----------|--------|-------------|
| `confidentiality_gate_rejected` | Compute name | 403 |
| `taint_violation` | "confidentiality" | 500 (indicates bug) |
| `redacted_field_access` | "expression" | 500 (indicates bug) |
| `output_taint_blocked` | Compute name | 403 |
| `output_scope_rejected` | Compute name | 403 |

Checks 2 and 3 return 500 because they indicate a pipeline failure (something upstream is broken). Checks 1 and 4 return 403 because they are normal access control.

---

## 4. File Change Summary

### Compiler (`termin/`)

| File | Changes |
|------|---------|
| `termin.peg` | Add `confidentiality_constraint`, `content_scope_line`, `compute_identity_line`, `compute_requires_line`, `compute_output_conf_line` rules |
| `peg_parser.py` | Add classification + handlers for new line types, add confidentiality constraint extraction |
| `ast_nodes.py` | Add `confidentiality_scope` to TypeExpr + Content, add `identity_mode`, `required_confidentiality_scopes`, `output_confidentiality` to ComputeNode |
| `analyzer.py` | Add scope validation, field dependency analysis, reclassification detection |
| `ir.py` | Add `confidentiality_scope` to FieldSpec + ContentSchema, add `FieldDependency`, `ReclassificationPoint`, extend ComputeSpec + AppSpec |
| `lower.py` | Thread all new fields through lowering |

### Runtime (`termin_runtime/`)

| File | Changes |
|------|---------|
| `confidentiality.py` | **NEW** — `redact_record`, `redact_records`, `effective_scopes`, `is_redacted`, `check_compute_access`, `check_taint_integrity`, `enforce_output_taint` |
| `app.py` | Hook redaction into list/get/create/update routes, WebSocket broadcast, page rendering context. Build `schemas_by_name` lookup at startup. |
| `expression.py` | Add `_check_for_redacted()` wrapper around evaluate results |
| `presentation.py` | Handle `__redacted` markers in table cells, form fields, text components, aggregations |
| `errors.py` | Add confidentiality error kinds to TerminAtor |

### Specs/Docs

| File | Changes |
|------|---------|
| `docs/termin-ir-schema.json` | Add all new fields to JSON Schema |
| `docs/termin-runtime-implementers-guide.md` | Add § Confidentiality System |

### Tests

| File | Changes |
|------|---------|
| `tests/test_parser.py` | Parse `confidentiality is`, `Scoped to`, `Identity:`, `Requires`, `Output confidentiality` |
| `tests/test_analyzer.py` | Scope validation, dependency analysis, reclassification detection, error cases |
| `tests/test_ir.py` | IR output includes all new fields |
| `tests/test_runtime.py` | Redaction, compute gating, taint checks, output enforcement |

---

## 5. Example App Requirements

The conformance test fixture for confidentiality should exercise:

1. **Field-level redaction** — salary, SSN fields with different scopes
2. **Content-level scope** — entire Content type scoped
3. **Multiple roles** with different scope combinations
4. **Service-identity Compute** with reclassification
5. **Delegate-mode Compute** with scope requirements
6. **Update form** with redacted field preservation
7. **API list/get** with mixed redacted/unredacted fields
8. **State machine** on confidential content (status visible, salary redacted)
9. **Presentation rendering** with [REDACTED] markers in tables

See § 6 for the proposed example app.

---

## 6. Proposed Example App: HR Portal

An HR management system that naturally exercises all confidentiality features:

### Roles and Scopes

| Scope | Purpose |
|-------|---------|
| `view_employees` | See employee records (names, departments) |
| `manage_employees` | Create/update employee records |
| `access_salary` | See salary and compensation fields |
| `access_pii` | See SSN, date of birth, personal phone |
| `view_team_metrics` | See aggregated team-level metrics |
| `manage_hr` | Full HR administration |

| Role | Scopes |
|------|--------|
| `employee` | `view_employees` |
| `manager` | `view_employees`, `view_team_metrics` |
| `hr business partner` | `view_employees`, `manage_employees`, `access_salary`, `access_pii`, `view_team_metrics` |
| `executive` | `view_employees`, `view_team_metrics` |

### Content Types

**employees** — `Scoped to "view_employees"`:
- name (text, required)
- department (text, required)
- role (text)
- start_date (date)
- salary (currency, `confidentiality is "access_salary"`)
- bonus_rate (number, `confidentiality is "access_salary"`)
- ssn (text, `confidentiality is "access_pii"`)
- phone (text, `confidentiality is "access_pii"`)

**departments** — no special confidentiality:
- name (text, required, unique)
- budget (currency, `confidentiality is "access_salary"`)
- head_count (whole number)

**salary_reviews** — `Scoped to "access_salary"`:
- employee (references employees, required)
- review_date (date)
- old_salary (currency)
- new_salary (currency)
- approved_by (text, `defaults to [User.Name]`)

### Computes

**Calculate Team Bonus Pool** (service identity, reclassification):
- Transform: takes employees, produces bonus_summary
- Identity: service
- Requires "access_salary"
- Output confidentiality: "view_team_metrics"
- Body: `[team_bonus = sum(employees.salary * employees.bonus_rate)]`

**Employee Summary** (delegate identity):
- Transform: takes employees, produces summary
- Requires "view_employees"
- Body: `[count = size(employees)]`

### State Machine

**salary_reviews** lifecycle: `pending` → `approved` → `applied`
- pending → approved: requires `manage_hr`
- approved → applied: requires `manage_hr`

### Pages

- **Employee Directory** (employee, manager, hr business partner, executive) — table of employees
- **HR Dashboard** (hr business partner) — full employee details + salary review queue
- **Team Overview** (manager) — team metrics, bonus pool (via Compute)
- **Add Employee** (hr business partner) — form with all fields
- **Salary Review** (hr business partner) — form for salary changes

### Test Matrix

This app enables these conformance tests:

| Test | What It Validates |
|------|-------------------|
| Manager sees employee names, `[REDACTED]` for salary | Field-level redaction |
| HR BP sees all fields including salary | Scope grants visibility |
| Employee sees names but not salary or SSN | Multiple confidentiality scopes |
| Salary reviews entirely redacted for non-HR | Content-level scope |
| Manager can see team bonus pool via Compute | Service identity + reclassification |
| Manager cannot invoke bonus pool Compute directly in delegate mode | Compute gate (Check 1) |
| HR BP can submit salary review, fields preserved | Update with confidential fields |
| API list returns `__redacted` markers with correct scope | API redaction format |
| Employee directory table shows `[REDACTED]` in salary column | Presentation redaction |
| Reclassification point visible in reflection | IR audit trail |

---

## 7. Implementation Order

1. **B1:** IR + Grammar + Parser (compiler side) — foundation for everything
2. **B2:** Analyzer static analysis — compile-time enforcement
3. **B3:** Lowering pass — thread through to IR
4. **B4:** Runtime field redaction — the core enforcement mechanism
5. **B5:** Compute invocation gate — Checks 1 and 2
6. **B6:** CEL redaction guard — Check 3
7. **B7:** Output taint enforcement — Check 4
8. **B8:** TerminAtor integration — error routing
9. **B9:** Presentation rendering — visual redaction
10. **B10:** Example app + conformance tests
11. **B11:** JSON Schema + Implementer's Guide update

Each phase has a natural commit boundary and can be tested independently.

---

## 8. Block C Inputs Needed

Block C (Boundary Enforcement) depends on:

1. **Confidentiality system (Block B)** — Boundary crossing triggers field redaction. Block B implements the redaction mechanism; Block C determines *when* it fires (at Boundary edges vs. everywhere).

2. **Channel infrastructure** — Currently, Channels exist in the IR but the runtime doesn't enforce cross-boundary data flow rules. Block C needs:
   - Decision: Does the reference runtime support multiple boundaries in a single process? Or is each boundary a separate process/service?
   - Decision: How are Channels materialized in the reference runtime? HTTP? In-process function calls? Message queues?
   - Decision: Does the reference runtime enforce "only through Channels" or is that a distributed-only concern?

3. **Identity propagation** — When a request crosses a Boundary, how is the caller's identity passed? The current stub auth uses cookies. Cross-boundary calls need identity tokens or propagation headers.

**Recommendation:** Block C should wait until Block B is complete. The redaction mechanism from Block B is a prerequisite for Boundary crossing enforcement. The Channel materialization question is the key architectural decision for Block C — JL should decide whether the reference runtime models boundaries as logical (same process, enforced via code isolation) or physical (separate processes, enforced via network).
