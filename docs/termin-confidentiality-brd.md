# Termin Confidentiality System — Business Requirements Document

**Author:** Jamie-Leigh Blake & Claude
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

### 2.4 Explicit Declassification

When a Compute intentionally produces lower-confidentiality output from higher-confidentiality input (e.g., returning a team-level aggregate count from individual salary data), the DSL author must explicitly declare this. Declassification points are flagged for security review.

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

Taint propagation ensures that `bonus_pool` inherits `access_salary` confidentiality by default. The only way to produce output at a lower confidentiality is explicit declassification in the DSL. The declassification is logged and available for audit review. The business decision to allow aggregate access is visible in the `.termin` file, not hidden in application code.

### 3.5 Compiler Catches Missing Scope Declarations

> As a developer writing a Compute module, I want the compiler to tell me if I forgot to declare required scopes, so that I don't create a runtime security hole.

The compiler analyzes the JEXL body, traces field access, resolves confidentiality requirements, and compares against the Compute's declared scope requirements. If a Compute accesses `salary` (which requires `access_salary`) but doesn't declare `Requires "access_salary"`, the compiler emits an error and refuses to compile.

### 3.6 Runtime Blocks Unauthorized Compute Invocation

> As a runtime operator, I want the system to reject unauthorized Compute invocations at the Channel boundary, before any code executes.

When a manager (lacking `access_salary`) attempts to invoke `CalculateBonusPool` in delegate mode, the runtime checks the Compute's required scopes against the caller's identity at the Channel boundary. The invocation is rejected with a TerminAtor error before any JEXL evaluates. The error is logged for audit.

### 3.7 Malicious App Detected and Terminated

> As a platform operator, I want the runtime to detect and terminate applications that attempt to access redacted fields at runtime, even if the compile-time checks were bypassed.

If a compromised compiler produces an application that attempts to read redacted field values at runtime, the JEXL evaluator encounters the redaction marker and raises a `RedactedFieldAccess` error. This error routes through TerminAtor, is logged with full context (identity, Compute, field, timestamp), and can trigger application termination if the violation pattern indicates malicious intent.

---

## 4. Requirements

### 4.1 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1 | Content fields can declare a confidentiality scope in the DSL | Must |
| FR-2 | Records crossing Channel boundaries have restricted fields redacted | Must |
| FR-3 | Redacted fields use a marker (`__redacted: true`) preserving schema shape | Must |
| FR-4 | Compiler traces field access in Compute JEXL bodies | Must |
| FR-5 | Compiler enforces scope declarations match field confidentiality | Must |
| FR-6 | Missing scope declaration on Compute is a compile error | Must |
| FR-7 | Runtime rejects Compute invocation at Channel boundary if caller lacks required scopes | Must |
| FR-8 | Output confidentiality defaults to maximum confidentiality of all inputs (taint propagation) | Must |
| FR-9 | Explicit declassification syntax in DSL for Compute output | Must |
| FR-10 | Declassification points flagged in IR for security review | Must |
| FR-11 | Service identity Compute must have union of required scopes AND output confidentiality scopes | Must |
| FR-12 | Runtime JEXL evaluator detects access to redacted markers and raises error | Must |
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

## 6. Acceptance Criteria

1. A `.termin` file with `confidentiality is "access_salary"` on a field compiles without error.
2. A Compute that accesses that field without declaring `Requires "access_salary"` fails to compile.
3. A runtime serving that application redacts the field for identities lacking `access_salary` scope.
4. The redacted field appears as `{ "__redacted": true }` in API responses and as `[REDACTED]` in Presentation rendering.
5. A Compute with `Identity: service` and declared scopes can process the field and return derived output.
6. Derived output inherits input confidentiality unless explicitly declassified.
7. Declassification is visible in the IR and queryable via Reflection.
8. Runtime JEXL evaluation of a redacted marker raises an error routed through TerminAtor.
