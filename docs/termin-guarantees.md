# What Termin Guarantees

**Version:** 0.9.0
**Date:** April 2026
**Status:** Draft for repository inclusion

---

## Purpose

This document is the definitive statement of what properties Termin structurally enforces, what properties are enforced by policy configuration, and what properties remain the responsibility of the application author or deployment operator. It is written for three audiences:

- **Developers** evaluating Termin for use in a project, who need to know what they get out of the box.
- **Security engineers** reviewing Termin for production deployment, who need to know what's actually enforced versus audited.
- **Product leaders** making commitments to customers and regulators, who need precise language about what the platform promises.

Every claim in this document is backed by a test in the conformance suite. If a claim in this document is not covered by a conformance test, it should not be in this document.

---

## Guarantee Tiers

Termin uses a three-tier model to describe what the platform enforces. The tier determines the strength and scope of the guarantee.

### Tier 1: Structural Guarantees

Tier 1 properties are enforced by the structure of the language and runtime. They cannot be violated by an application author, an operator, or an AI agent authoring a `.termin` file, because the language cannot express the violation. Tier 1 guarantees apply to every conforming Termin runtime and every application written in pure Termin.

### Tier 2: Vetted Provider Guarantees

Tier 2 properties are enforced by a provider that has been reviewed and certified. A Compute provider, Channel provider, or Presentation provider that has passed the conformance suite and any additional organizational review inherits the Tier 1 guarantees at its boundary. Tier 2 guarantees depend on the specific provider. They are documented per-provider.

### Tier 3: Custom Provider Responsibilities

Tier 3 covers applications that use providers not reviewed by the Termin ecosystem or the deploying organization. Custom providers extend Termin but require the same review attention as any external code. Tier 3 is not a Termin guarantee — it is a description of where Termin's guarantees end and custom code review begins.

---

## Tier 1: Structural Guarantees

The following properties are enforced structurally by every conforming Termin runtime. Each is validated by the conformance suite.

### Injection Resistance

Every data access in Termin is parameterized by the compiler. The language has no construct for assembling queries as strings or concatenating user input into executable commands. SQL injection, command injection, expression injection, and template injection are not achievable through Termin application code because the language does not provide the machinery to produce injectable constructs.

**Scope:** Applies to Content access, State queries, Channel payloads, and Presentation rendering.
**Caveat:** Custom providers (Tier 3) may introduce injection vectors if they construct queries from untrusted input. Vetted providers (Tier 2) are reviewed for injection resistance as a condition of certification.

### Access Control

Every operation in Termin is scope-checked against the caller's identity. The language requires declaring who can perform each operation, and the runtime enforces the declaration on every call. There is no code path that bypasses access control. Access control is not a library the developer chooses to use — it is a property of the runtime contract.

**Scope:** Applies to Content CRUD operations, State transitions, Compute invocations, Channel sends, and Presentation rendering.
**Caveat:** Correct scope assignment to roles and users is the responsibility of the application author. Termin enforces that scopes are checked. It does not enforce that the scope assignments are correct for your organization.

### State Transition Enforcement

State transitions are declared explicitly in the `.termin` source. Every transition specifies the source state, the destination state, and the scope required to perform it. The runtime rejects transitions that are not declared, transitions from a state other than the declared source, and transitions attempted by identities without the required scope.

**Scope:** Applies to all Content with declared state machines.
**Caveat:** The set of states and transitions is determined by the application author. Termin enforces that declared transitions are the only transitions possible.

### Data Flow Boundaries

Data does not cross a Boundary except through a declared Channel. Channels are typed, schema-validated, and identity-scoped. Cross-boundary data flow cannot occur through side channels, shared memory, or undeclared exports. The runtime enforces that the only way data moves between Boundaries is through a Channel that has been declared in the source.

**Scope:** Applies to all Content and Compute within a Boundary.
**Caveat:** Presentation rendering within a Boundary is not a cross-boundary flow. Observation of Content by Presentation within the same Boundary is permitted by default.

### Confidentiality Redaction

Fields declared as confidential are redacted in any response that is returned to an identity without the required scope. Redaction happens at the runtime layer before data leaves the Boundary. Taint propagation ensures that computed values derived from confidential fields inherit the confidentiality of their inputs. Explicit reclassification is required in the source and is auditable.

**Scope:** Applies to declared confidential fields across Content, Compute outputs, Channel payloads, and Presentation rendering.
**Caveat:** The set of confidential fields is declared by the application author. Termin enforces that declared confidential fields are redacted. It does not automatically identify which fields should be confidential.

### Audit Log Completeness

Every action that modifies Content or triggers a State transition generates an audit log entry. Audit log entries record the identity of the caller, the operation performed, the affected record, and a timestamp. Audit levels (`none`, `actions`, `debug`) control the verbosity but not the existence of the log. At `actions` level (the default), field values are not recorded in the audit log, ensuring confidentiality-protected data is not leaked through logs.

**Scope:** Applies to all Content and State operations.
**Caveat:** `debug` audit level records field values and is intended for development environments. Deployments in production with `debug` audit on confidential Content will record confidential values in the audit log. Use `actions` (default) in production.

### Deterministic Compilation

The same `.termin` source compiled by the same compiler version produces the same IR. The IR is an open JSON artifact that can be inspected, validated against the IR schema, and compared across versions. The compilation pipeline has no sources of nondeterminism.

**Scope:** Applies to the compiler and the IR format.
**Caveat:** Application identity (the `app_id` UUID) is generated on first compile and written back to the source. Once generated, it is deterministic.

### Conformance Verifiability

Every Tier 1 guarantee is tested by one or more conformance tests. Any runtime that passes the conformance suite enforces these guarantees. A runtime that does not pass the conformance suite is not a conforming Termin runtime and cannot claim Tier 1 guarantees.

**Scope:** Applies to every runtime implementation.
**Caveat:** The conformance suite evolves. A runtime that passed an earlier version of the conformance suite may fail a later version. Runtimes must be re-validated against the current conformance suite to maintain their conformance claim.

---

## Tier 2: Vetted Provider Guarantees

Vetted providers extend Termin's capabilities while maintaining Tier 1 guarantees at their boundary. A provider becomes vetted when it has:

1. Passed the Termin conformance suite (or the provider-specific subset relevant to its capability).
2. Undergone a security review by a qualified reviewer.
3. Documented its own guarantees and caveats in a provider manifest.

### Currently Vetted Providers

As of v0.9.0, no providers have completed the formal vetting process. Providers shipped in the Termin reference distribution are marked as "reference" providers — they are maintained alongside the runtime but have not undergone external review.

Reference providers in v0.9.0:
- `ai-agent` — integrates Anthropic and OpenAI APIs for agent-mode Compute nodes.
- `cel` — evaluates Common Expression Language expressions.
- `http` — outbound HTTP and inbound webhook Channel support.
- `websocket` — WebSocket Channel support.

### Provider Review Framework

A forthcoming document, `termin-provider-review.md`, will specify the process by which a provider becomes vetted. That document will define:

- Security review requirements
- Conformance test requirements per provider type
- Provider manifest schema
- Revocation process if a vetted provider is found to have vulnerabilities

Until that framework is published, treat all providers (including reference providers) as Tier 3 for formal guarantee purposes.

---

## Tier 3: Custom Provider Responsibilities

Applications using custom providers take on the responsibility for reviewing those providers. Custom providers can do anything Termin does not structurally prevent — they can issue arbitrary queries, bypass Termin's identity system if designed badly, exfiltrate data through side channels, and introduce any class of vulnerability present in the code they execute.

### What Termin Still Guarantees With Custom Providers

Even when a custom provider is in use, the following Tier 1 guarantees hold for the non-provider parts of the application:

- Access control on Compute invocations (the provider is called, but the decision to call it is scope-checked).
- Boundary isolation (the custom provider's Boundary is still a Boundary).
- Audit logging of provider invocations.
- Confidentiality scope on inputs passed to the provider.

### What Termin Does Not Guarantee With Custom Providers

- Injection resistance inside the provider.
- Correct output scoping from the provider.
- Resource consumption bounds.
- Any behavioral guarantee about what the provider does with the data it receives.

### Recommendation for Custom Provider Use

Treat custom providers the same way you would treat any external dependency. Review the code. Pin the version. Monitor for vulnerabilities. Consider whether the capability can be achieved by composing Tier 1 primitives or vetted providers before reaching for a custom provider.

---

## What Termin Does Not Guarantee

This section exists to prevent overclaim. The following are **not** Termin guarantees.

### Correctness of Business Logic

Termin does not guarantee that an application does the right thing for its users. An application can be structurally safe and still have incorrect business logic. A help desk that routes every ticket to the wrong team is not a Termin failure — it is a requirements failure.

### Correctness of Scope Assignments

Termin enforces that declared scopes are checked. It does not enforce that the declared scopes are appropriate for your organization's security policy. If an application grants `salary.view` to all employees, Termin will check the scope on every access — but it will not tell you that the grant is inappropriate.

### Availability and Performance

Termin does not guarantee any service level. A Termin runtime can be overloaded, can crash, can run slowly, or can be deployed on infrastructure that fails. Availability is a property of the deployment, not the language.

### Protection Against Insider Threats

An administrator with access to the runtime's data store can read and modify records directly, bypassing the runtime's enforcement. Termin enforces guarantees for applications running on the runtime — it does not prevent abuse by those who control the runtime.

### Protection Against Compromised Dependencies

If a library Termin depends on has a vulnerability, Termin inherits that vulnerability. Termin's dependencies should be monitored and updated per normal security practice.

### Long-Term Storage Format Compatibility

The IR format follows semantic versioning. Minor version changes are forward-compatible. Major version changes may require re-compilation. Termin does not guarantee that a `.termin.pkg` compiled against IR version 0.5 will run on a runtime that only supports IR version 2.0. Runtime version constraints are documented in each package manifest.

---

## How to Use This Document

### For Developers

Read Tier 1 to understand what you get automatically. Read Tier 2 to understand which providers add capabilities with retained guarantees. Read Tier 3 to understand what's on you when you write a custom provider. The "What Termin Does Not Guarantee" section is what to tell your team when they ask if Termin solves problem X — if X is in that section, Termin does not solve it.

### For Security Engineers

This document is the scope of the review. The conformance suite is the evidence. For a Termin application that uses only Tier 1 primitives and vetted (Tier 2) providers, the review surface is the runtime itself plus the provider manifests. For a Termin application that uses Tier 3 custom providers, the review surface includes those providers' code. The "review once, certify many" model applies at the Tier 1 and Tier 2 levels. It does not apply to Tier 3.

### For Product Leaders

The claims you can make about a Termin application are the claims in this document. If you need a guarantee that is not in this document, either it's in the "Does Not Guarantee" section (in which case you need another mechanism) or it's a capability that could be added to a future version of Termin (in which case, file an issue). Do not overclaim. The credibility of Termin's guarantees depends on the precision of this document.

---

## Change Log

- **v0.8.0 (April 2026)** — Initial version. Tier structure defined. Tier 1 guarantees enumerated with caveats. Tier 2 framework described but no providers yet formally vetted. Tier 3 responsibilities documented.

---

## References

- `termin-ir-schema.json` — IR schema that defines what applications can express.
- `termin-runtime-implementers-guide.md` — How runtimes implement these guarantees.
- `termin-package-format.md` — How applications are packaged for deployment.
- `specs/conformance/README.md` (forthcoming) — Index of conformance tests by guarantee.
