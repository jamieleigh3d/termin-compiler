# Termin Confidentiality System — Business Requirements Document

**Author:** Jamie-Leigh Blake & Claude Anthropic
**Version:** 1.0.0
**Date:** April 2026
**Status:** Approved for implementation

---

## 1. Problem Statement

Business applications handle data with varying sensitivity levels. Employee salaries, customer SSNs, medical records, financial projections — these fields exist alongside non-sensitive data in the same Content types. Today's approach to protecting sensitive fields relies on application developers implementing access checks correctly in every code path. This fails at scale: developers forget, code reviews miss it, and one leaked endpoint exposes everything.

Termin's security thesis is that structural enforcement eliminates these failures. The confidentiality system extends this thesis to **field-level data sensitivity** — ensuring that restricted fields are never visible to unauthorized identities, even through derived computations.

---

## 2. Business Objectives

### 2.1 Structural Field Redaction

When a record crosses a Boundary or Channel, fields that the recipient's identity is not authorized to see are **redacted** — replaced with a redaction marker that preserves schema shape while withholding the value. This happens automatically, without application code, at every Channel crossing.

### 2.2 Compile-Time Scope Enforcement

The compiler analyzes Compute modules to determine which Content fields they access. If a Compute accesses fields with confidentiality requirements, the compiler enforces that the Compute declares matching scope requirements. Missing declarations are compile errors, not runtime surprises.

### 2.3 Taint Propagation Through Computation

Any value derived from a confidential field inherits that field's confidentiality. If `salary` requires `access_salary` scope, then `bonus_pool = sum(salary * rate)` also requires `access_salary` scope. This prevents information laundering through aggregation, renaming, or mathematical transformation.

### 2.4 Explicit Reclassification

When a Compute intentionally produces output at a different confidentiality scope than its inputs (e.g., returning a team-level aggregate from individual salary data), the DSL author must explicitly declare this. The system does not assign hierarchy to scopes — `access_salary` is not "higher" or "lower" than `view_team_metrics`, they are simply different scopes. Reclassification is the act of declaring that the output carries a different scope than the inputs. Without this declaration, the entire output inherits the input taint and is only accessible to identities holding the input scope. Reclassification points are flagged for security review.

---

## 3. User Stories

### 3.1 HR Business Partner Views Employee Records

> As an HR Business Partner, I want to see employee records including salary and compensation details, so that I can make compensation decisions.

The HRBP has `access_salary` scope. When they view an employee record, all fields are visible including salary. No redaction occurs.

### 3.2 Manager Views Employee Records

> As a Manager, I want to see my team's employee records (name, department, role, start date) without seeing salary or SSN, so that I can manage my team.

The manager lacks `access_salary` and `access_pii` scopes. When they view an employee record, the salary field shows `[REDACTED]` and SSN is absent or redacted. The schema shape is preserved — the manager knows the field exists but cannot see the value.

### 3.3 Manager Requests Team Bonus Pool

> As a Manager, I want to know my team's total bonus pool amount, so that I can plan recognition events.

The `CalculateBonusPool` Compute requires `access_salary` (because it reads salary) but is declared with `Identity: service` and `Output confidentiality: "view_team_metrics"`. The Compute runs with service identity (which has `access_salary`), computes the aggregate, and returns the result. The output is at `view_team_metrics` confidentiality, which the manager has. The manager sees $450,000 without seeing any individual salary.

### 3.4 Manager Cannot Reverse-Engineer Salary

> As a security auditor, I want assurance that a manager cannot extract individual salaries through constructed queries (team of one, bonus rate of 1.0, etc.).

Taint propagation ensures that `bonus_pool` inherits `access_salary` confidentiality by default. The only way to produce output at a lower confidentiality is explicit reclassification in the DSL. The reclassification is logged and available for audit review. The business decision to allow aggregate access is visible in the `.termin` file, not hidden in application code.

### 3.5 Compiler Catches Missing Scope Declarations

> As a developer writing a Compute module, I want the compiler to tell me if I forgot to declare required scopes, so that I don't create a runtime security hole.

The compiler analyzes the CEL body, traces field access, resolves confidentiality requirements, and compares against the Compute's declared scope requirements. If a Compute accesses `salary` (which requires `access_salary`) but doesn't declare `Requires "access_salary"`, the compiler emits an error and refuses to compile.

### 3.6 Runtime Blocks Unauthorized Compute Invocation

> As a runtime operator, I want the system to reject unauthorized Compute invocations at the Channel boundary, before any code executes.

When a manager (lacking `access_salary`) attempts to invoke `CalculateBonusPool` in delegate mode, the runtime checks the Compute's required scopes against the caller's identity at the Channel boundary. The invocation is rejected with a TerminAtor error before any CEL evaluates. The error is logged for audit.

### 3.7 Malicious App Detected and Terminated

> As a platform operator, I want the runtime to detect and terminate applications that attempt to access redacted fields at runtime, even if the compile-time checks were bypassed.

If a compromised compiler produces an application that attempts to read redacted field values at runtime, the CEL evaluator encounters the redaction marker and raises a `RedactedFieldAccess` error. This error routes through TerminAtor, is logged with full context (identity, Compute, field, timestamp), and can trigger application termination if the violation pattern indicates malicious intent.

---

## 4. Requirements

### 4.1 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1 | Content fields can declare a confidentiality scope in the DSL | Must |
| FR-2 | Records crossing Channel boundaries have restricted fields redacted | Must |
| FR-3 | Redacted fields use a marker (`__redacted: true`) preserving schema shape | Must |
| FR-4 | Compiler traces field access in Compute CEL bodies | Must |
| FR-5 | Compiler enforces scope declarations match field confidentiality | Must |
| FR-6 | Missing scope declaration on Compute is a compile error | Must |
| FR-7 | Runtime rejects Compute invocation at Channel boundary if caller lacks required scopes | Must |
| FR-8 | Output confidentiality defaults to maximum confidentiality of all inputs (taint propagation) | Must |
| FR-9 | Explicit reclassification syntax in DSL for Compute output | Must |
| FR-10 | Reclassification points flagged in IR for security review | Must |
| FR-11 | Service identity Compute must have union of required scopes AND output confidentiality scopes | Must |
| FR-12 | Runtime CEL evaluator detects access to redacted markers and raises error | Must |
| FR-13 | Redacted field access errors route through TerminAtor | Must |
| FR-14 | Default identity mode for all Compute is delegate | Must |
| FR-15 | Service identity is opt-in via DSL declaration | Must |
| FR-16 | Dependency graph (field → scope → Compute) stored in IR | Should |
| FR-17 | Reflection endpoint exposes confidentiality metadata | Should |
| FR-18 | Content-level scope inheritance to fields (fields inherit Content scope by default) | Should |
| FR-19 | Field-level scope can narrow but never widen beyond Content scope | Should |

### 4.2 Non-Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| NFR-1 | Field redaction adds < 1ms latency per Channel crossing | Must |
| NFR-2 | Compile-time field analysis completes in < 1s for applications with ≤ 50 Compute modules | Must |
| NFR-3 | Redaction is transparent to Presentation rendering (redacted fields show placeholder, not error) | Should |
| NFR-4 | Audit log of all redaction events is queryable via Reflection | Should |

---

## 5. Out of Scope

- **Row-level security filters** — Future feature. This BRD covers field-level confidentiality only.
- **Encryption at rest** — Storage-level concern, not application-level. Termin delegates to the storage adapter.
- **Network encryption** — Transport-level concern. Termin assumes TLS on all Channel transports.
- **Time-based access** — "Field X is visible only during business hours." Future feature.
- **Consent-based access** — "Field X is visible only if the data subject consented." Future feature.

---

## 6. Runtime Check Enumeration

The confidentiality system performs checks at multiple points. Each catches a distinct failure mode. The checks are ordered from earliest (Channel input boundary) to latest (CEL evaluation), forming defense in depth.

**Terminology note:** "On-behalf-of identity" and "delegate identity" are the same thing. Delegate is the default identity mode — the Compute runs carrying the original caller's identity. In the check descriptions below, "on-behalf-of" describes the delegate identity attached to a service-mode invocation (the service acts, but the delegate identity determines what the output can contain).

### Check 1: Channel Input — Identity vs. Required Scopes (Pre-Execution Gate)

**When:** A Compute invocation arrives at the input Channel boundary, before any code runs.

**What:** The runtime reads the Compute's `required_confidentiality_scopes` from the IR dependency graph and checks them against the effective identity (the delegate identity for delegate mode, the service identity for service mode).

**Failure mode — delegate identity lacks required scope:**
A manager (lacking `access_salary`) invokes `CalculateBonusPool` in delegate mode. The Compute requires `access_salary`. Invocation rejected. No CEL executes.

```
TerminAtor: confidentiality_gate_rejected
  compute: CalculateBonusPool
  identity: manager (delegate)
  missing_scope: access_salary
  action: invocation_blocked
```

**Failure mode — service identity lacks required scope:**
A misconfigured service account invokes `CalculateBonusPool` in service mode but the service account doesn't have `access_salary`. The union check fails (service needs `access_salary` + `view_team_metrics`). Invocation rejected.

```
TerminAtor: confidentiality_gate_rejected
  compute: CalculateBonusPool
  identity: service:bonus-calculator
  missing_scope: access_salary
  action: invocation_blocked
```

### Check 2: Channel Input — Unredacted Field Integrity (Taint Violation)

**When:** Data arrives at the Compute's input Channel with the on-behalf-of identity attached.

**What:** The runtime inspects the incoming record. For each field with a confidentiality scope, it checks: does the on-behalf-of identity have that scope? If the on-behalf-of identity does NOT have `access_salary` but the salary field arrives **unredacted**, something upstream failed to redact — the data is tainted. The Compute must not process it, because any result would inherit the taint and be returned to someone who shouldn't have it.

**Failure mode — unredacted field sent on behalf of unauthorized identity:**
A service account sends `{ "name": "Alice", "salary": 150000 }` on behalf of a manager who lacks `access_salary`. The salary field should have been redacted before reaching this Channel, but wasn't (either a bug or a compromised upstream).

```
TerminAtor: confidentiality_taint_violation
  compute: CalculateBonusPool
  identity: service:bonus-calculator (on-behalf-of: manager)
  field: salary
  required_scope: access_salary
  on_behalf_of_has_scope: false
  field_redacted: false
  action: invocation_blocked — unredacted confidential field for unauthorized delegate
```

This is the "belt and suspenders" check. If redaction happened correctly upstream, this never fires. It catches the case where a compromised or buggy component in the pipeline sent unredacted data.

### Check 3: CEL Evaluation — Redacted Field Dereference

**When:** A CEL expression accesses a field that IS correctly redacted (the `{ "__redacted": true }` marker).

**What:** The CEL evaluator encounters the redaction marker during expression evaluation. This happens when:
- The dependency graph said the Compute uses `salary`, but the field arrived redacted (e.g., the identity check passed because the Compute is in service mode, but the data source sent already-redacted data)
- Dynamic property access that the static analyzer couldn't trace

**Failure mode — CEL expression operates on redacted marker:**
`[bonus_pool = sum(employees.salary * bonus_rate)]` evaluates, and `employees[0].salary` resolves to `{ "__redacted": true }`. Arithmetic on a redaction marker is meaningless.

```
TerminAtor: redacted_field_access
  compute: CalculateBonusPool
  expression: sum(employees.salary * bonus_rate)
  field: employees.salary
  redaction_scope: access_salary
  action: expression_aborted
```

### Check 4: Output Channel — Taint Propagation Enforcement

**When:** A Compute completes and its output is about to cross the output Channel boundary back to the caller.

**What:** The **entire output** of a Compute inherits the maximum confidentiality of all its inputs. This is not field-by-field — the whole output blob is tainted. The runtime does not attempt to trace which output values derived from which input fields, because such tracing is both unreliable and trivially defeated by renaming fields or restructuring data.

The rule is simple: if a Compute consumed `access_salary`-scoped data, **everything it produces** is at `access_salary` scope. The output key names are irrelevant — `bonus_pool`, `total`, `result`, or `age` all carry the same taint regardless of what they're called.

**Without reclassification:** The entire output is at the input taint scope. If the on-behalf-of identity lacks that scope, the entire output is blocked. Not redacted field-by-field — blocked entirely, because every value in the output is potentially derived from the confidential input.

**With reclassification:** The Compute declares `Output reclassification: "view_team_metrics"`. This is the author's assertion that the output, while derived from confidential data, is intentionally being produced at a different scope. The on-behalf-of identity's scopes are checked against the reclassified output scope.

**Failure mode — no reclassification, delegate lacks input scope:**
`CalculateBonusPool` runs in service mode, consumes `access_salary` data, produces `{ "bonus_pool": 450000 }`. No reclassification declared. The entire output is at `access_salary` scope. The on-behalf-of identity (manager) lacks `access_salary`. The entire output is blocked.

```
TerminAtor: output_taint_blocked
  compute: CalculateBonusPool
  output_taint: access_salary
  on_behalf_of: manager
  has_scope: false
  reclassification: none
  action: entire_output_blocked
```

This is correct — without explicit reclassification, the system treats the output as confidential as its inputs. The Compute author must add `Output reclassification: "view_team_metrics"` to deliberately release the aggregate.

**Failure mode — reclassified output but caller lacks the reclassified scope:**
The Compute declares `Output reclassification: "view_team_metrics"` but the on-behalf-of identity (intern) doesn't have `view_team_metrics` either.

```
TerminAtor: output_scope_rejected
  compute: CalculateBonusPool
  reclassified_scope: view_team_metrics
  on_behalf_of: intern
  action: output_blocked
```

**Success case — reclassification with authorized delegate:**
The Compute declares `Output reclassification: "view_team_metrics"`. The on-behalf-of identity (manager) has `view_team_metrics`. The output passes through at the reclassified scope.

```
TerminAtor: output_reclassified (TRACE)
  compute: CalculateBonusPool
  input_taint: access_salary
  reclassified_to: view_team_metrics
  on_behalf_of: manager
  action: output_delivered
```

### Check Summary

| # | Check Point | What It Catches | When It Fires |
|---|------------|-----------------|---------------|
| 1 | Channel input gate | Identity lacks required scopes | Every invocation |
| 2 | Channel input integrity | Unredacted field for unauthorized delegate | Upstream bug or compromise |
| 3 | CEL evaluation | Expression accesses redacted marker | Static analysis gap or dynamic access |
| 4 | Channel output taint | Entire output tainted by input confidentiality | Every service-mode output |

Checks 1 and 4 fire on every invocation (they're the normal enforcement path). Checks 2 and 3 are defense-in-depth — they catch failures in the redaction pipeline itself, indicating either bugs or malicious behavior. A Check 2 or Check 3 firing in production should trigger investigation because it means something upstream in the pipeline is broken.

---

## 7. Acceptance Criteria

1. A `.termin` file with `confidentiality is "access_salary"` on a field compiles without error.
2. A Compute that accesses that field without declaring `Requires "access_salary"` fails to compile.
3. A runtime serving that application redacts the field for identities lacking `access_salary` scope.
4. The redacted field appears as `{ "__redacted": true }` in API responses and as `[REDACTED]` in Presentation rendering.
5. A Compute with `Identity: service` and declared scopes can process the field and return derived output.
6. Derived output inherits input confidentiality unless explicitly reclassified.
7. Reclassification is visible in the IR and queryable via Reflection.
8. Runtime CEL evaluation of a redacted marker raises an error routed through TerminAtor.
9. Check 1 (identity gate) blocks invocation before any CEL executes when caller lacks required scopes.
10. Check 2 (taint violation) blocks invocation when unredacted confidential fields arrive for an unauthorized delegate.
11. Check 3 (redacted dereference) aborts CEL evaluation when an expression accesses a redaction marker.
12. Check 4 (output taint) redacts derived fields in service-mode Compute output that the delegate identity cannot see.
