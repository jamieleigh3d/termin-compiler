# Termin Product Strategy & Roadmap

**Author:** Jamie-Leigh Blake
**Date:** April 2026
**Status:** Working draft

---

## What Termin Is

Termin is a declarative application platform. You describe what a business application should do — its data, its rules, its workflows, its access controls — in a constrained English-like DSL. The Termin compiler produces a validated intermediate representation. The Termin runtime executes it with structural enforcement of security, identity, and data flow.

The core thesis: a large class of business software can be fully specified in a constrained declarative language, and the platform can enforce security properties that are structurally impossible to violate — not through policy layers, but through the shape of the system itself.

## What Termin Is Not

- **Not a general-purpose programming language.** Termin cannot build a video game, a machine learning pipeline, a real-time trading engine, or a graphics renderer. It builds business applications: workflow tools, dashboards, CRUD apps, approval pipelines, inventory trackers, ticketing systems, and similar.
- **Not a low-code drag-and-drop builder.** There is no visual canvas. The DSL is text. The primary author may be an AI agent, but the artifact is a text file that can be version-controlled, diffed, code-reviewed, and understood by a human reader.
- **Not trying to solve all security problems.** Termin eliminates entire categories of vulnerability (injection, unauthorized data flow, undeclared state transitions). It does not prevent business logic errors, bad policy design, overbroad scope grants, poor AI prompts, or bugs in custom provider implementations. See Guarantee Tiers below.
- **Not initially targeting consumer-scale internet applications.** The architecture is sound for business-scale workloads (hundreds to low thousands of concurrent users per application). Consumer-scale horizontal scaling is a future concern, not a launch requirement.
- **Not a replacement for existing infrastructure.** Termin runs on existing infrastructure (containers, Lambda, VMs). It does not replace your database, your identity provider, or your cloud account. It sits above them and makes them structurally safe to use.

---

## Why Now

Three forces converge:

**1. AI can write constrained DSLs better than general-purpose code.** LLMs produce better, more correct output when the target language is small, well-specified, and has formal semantics. A 50-line `.termin` file is easier for an AI to get right than the equivalent 2,000 lines of React + Node.js + SQL + auth middleware + RBAC configuration. The smaller the language, the higher the AI's hit rate.

**2. Enterprise AppSec teams are drowning.** Large organizations build thousands of internal tools per year. Each one needs a security review. Each review takes weeks. Each tool has its own implementation of auth, access control, input validation, and data flow. If the platform enforces those properties structurally, the review surface collapses. Review once (the runtime), certify many (every application on that runtime).

**3. The build-vs-buy gap is widening.** Low-code platforms (Mendix, OutSystems) cost $50K–$700K/year and lock you in. Custom development takes months. Internal tool builders (Retool, Appsmith) only handle dashboards, not workflow applications with state machines and access control. There is no option that is fast, cheap, open, secure, and capable of real business workflow. That gap is the market.

---

## First Customers, First Pain, First Apps

### Enterprise Pilot

**Customer:** Internal teams at large organizations that build workflow tools (onboarding trackers, training dashboards, approval workflows) requiring security review, IAM configuration, and ongoing maintenance.

**Pain:** A single internal tool takes weeks to build and days to review. Organizations have a backlog of tools that will never get built because the cost exceeds the value.

**First app class:** Internal workflow applications with role-based access, state machine–driven processes, and real-time dashboards. The project board and help desk examples already exist as working prototypes.

**Why they switch:** If every tool built on Termin comes pre-certified because the runtime is the thing you review, the AppSec bottleneck disappears. Review once, certify many.

### Seedling (Small Business)

**Customer:** Small businesses and nonprofits who need custom workflow tools but can't afford developers.

**Pain:** They use spreadsheets, sticky notes, and manual processes for things that should be software (inventory tracking, client intake, volunteer coordination). Off-the-shelf SaaS doesn't fit their workflow. Custom development is out of budget.

**First app class:** Same as enterprise — workflow applications with roles, state machines, and dashboards. The mutual aid board example is representative.

**Why they switch:** A PM at Clarity Intelligence describes what the client needs. Seedling AI writes the `.termin`, compiles it, deploys it, and maintains it. The client gets a running application in minutes for ~$25/month. They were never going to hire a developer. The alternative was a spreadsheet.

**Why now:** Seedling infrastructure is being built on ECS/Fargate at the $15/month target. The Sprint engagement model ($5,000/5 days + $250/month retainer) is defined.

---

## Guarantee Tiers

Not all Termin applications carry the same level of structural guarantee. The guarantee depends on what the application uses.

### Tier 1: Pure Termin (Built-ins Only)

Applications using only the Termin DSL, JEXL expressions, built-in Compute (JEXL bodies), and built-in Channels (HTTP, WebSocket, Internal).

**Guaranteed by compiler:**
- All Content access is parameterized — no constructed queries, no string interpolation
- All State transitions are explicitly declared — undeclared transitions are rejected at compile time
- All access rules reference declared scopes — typos and undefined scopes are caught
- All expressions are valid JEXL within sandbox restrictions — no host access, no object construction
- All Channel schemas are validated — type mismatches between boundaries are caught

**Guaranteed by runtime:**
- Identity is checked on every Content operation (create, read, update, delete)
- Boundary isolation is enforced — cross-boundary data flows only through declared Channels and Properties
- State transitions are scope-gated — the runtime rejects transitions the user's identity doesn't permit
- Row-level filters are applied server-side — clients cannot bypass them
- Confidentiality scoping is enforced at Channel boundaries — fields outside scope are stripped
- Errors never halt the pipeline — they route through TerminAtor

**What this eliminates:**
- SQL injection (no SQL in the application layer)
- XSS (Presentation is declarative, not template-based)
- Broken access control (access rules are compiler-checked and runtime-enforced)
- Unauthorized state transitions (state machines are explicit and scope-gated)
- Data leakage through Channels (confidentiality enforcement strips unauthorized fields)

**What this does NOT prevent:**
- Business logic errors (the application does the wrong thing, but does it securely)
- Overbroad scope grants (a role has too many permissions — that's a policy design error)
- Bad data modeling (the Content schema doesn't capture what the business needs)

### Tier 2: Termin with Vetted Providers

Applications using Tier 1 features plus registered Compute or Channel providers that have been reviewed and approved by the runtime administrator.

**Additional guarantees:**
- Provider inputs and outputs are schema-validated by the runtime
- Provider execution is scoped by the Compute node's declared identity (caller, service, or delegate)
- Provider errors route through TerminAtor like any other error
- Provider actions are logged in the Event Bus

**What this does NOT prevent:**
- Bugs inside the provider implementation (the provider might mishandle data internally)
- Provider-specific security issues (a Stripe provider might have its own vulnerabilities)
- Data exfiltration if the provider has network access beyond the Channel it's wired to

**Mitigation:** Provider vetting process, provider sandboxing (future), provider audit logging.

### Tier 3: Termin with Custom / Unvetted Providers

Applications using arbitrary custom providers registered by the application developer.

**What still holds:**
- All Tier 1 guarantees for the Termin portions of the application
- Schema validation on provider inputs/outputs
- Identity scoping on provider execution
- Event logging of provider actions

**What does NOT hold:**
- Any guarantee about what happens inside the provider's `execute()` function
- Provider could make arbitrary network calls, access secrets, or exfiltrate data
- Provider could return data that is technically schema-valid but semantically wrong

**This is the trust boundary.** Custom providers are the escape hatch where external risk re-enters the system. The platform's long-term leverage depends on how strong the provider governance story becomes.

---

## The Boundary Question

A legitimate architectural concern: are Boundaries doing too many jobs?

Currently, boundaries serve as: packaging units, tenancy containers, policy inheritance scope, confidentiality scope, service discovery namespace, and deployment topology.

**Why this is probably right (for now):** In the systems Termin targets (internal business tools, small business workflow apps), organizational structure, policy domains, and deployment topology are the same thing. The team that owns the app is the team whose policies govern it, and the app runs in the team's infrastructure. The tree model is a good fit because the organizations are hierarchical.

**Where it might break:** Matrixed organizations where policy authority doesn't follow org hierarchy. Cross-functional projects where apps need to share data across org boundaries in ways that don't fit a tree. Federated deployments where the same app runs in multiple policy domains simultaneously.

**The hedge:** Boundaries are clean enough to refactor later. If the tree model proves too rigid, the system can evolve toward a tagged/labeled model (similar to Kubernetes namespaces with RBAC bindings) without changing the DSL syntax. The DSL says `Scoped to /corporate/team-alpha/` — whether that resolves through a tree walk or a label match is a runtime implementation detail, not a language design commitment.

**Decision:** Ship with the tree model. Monitor whether real deployments need cross-cutting scopes. Don't prematurely engineer for organizational complexity that may not materialize in the first two years.

---

## Roadmap

### Phase 0: Proof of Architecture (Current — Q2 2026)

**Goal:** Prove the compiler → IR → runtime pipeline works end-to-end with structural enforcement.

**What exists:**
- Working TatSu PEG parser and compiler (Python)
- 6 example `.termin` applications (hello world through project management)
- IR specification (current + target vision)
- Runtime package with WebSocket real-time updates, component tree rendering
- Distributed runtime model (registry, bootstrap, multiplexed subscriptions)

**What needs to happen:**
- Implement Boundary isolation enforcement (currently structural in the DSL but not enforced at runtime)
- Implement State machine scope-gating (transitions work but don't check scopes)
- Write the conformance test suite seed (10–20 tests covering Tier 1 guarantees)

**Exit criteria:** A single `.termin` file compiles to an IR, the IR loads into the runtime, the runtime serves a working application with enforced identity checking, enforced state transitions, and enforced boundary isolation. The conformance test suite passes.

**Duration:** 4–6 weeks of focused work.

### Phase 1: Enterprise Pilot (Q3 2026)

**Goal:** Deploy a real internal tool at an enterprise organization using a Termin deployment with their identity provider.

**What needs to happen:**
- Pluggable Identity subsystem (connect to enterprise SSO/SAML/OIDC)
- PostgreSQL or DynamoDB storage adapter (replace SQLite for production)
- AppSec initial review of the runtime (not the applications — the runtime itself)
- One real application chosen by the pilot team, authored in `.termin`, deployed to an internal environment
- `termin deploy` CLI for deploying packages
- Basic Reflection endpoints for operational visibility

**Pilot application candidates:**
- Team onboarding tracker (workflow states, role-based visibility, simple data model)
- Training course catalog with enrollment tracking
- Equipment request / approval pipeline

**Exit criteria:** One real team uses one real Termin application in production for at least 4 weeks. AppSec has reviewed the runtime and provided feedback. At least one person who didn't write the `.termin` file can read and understand it.

**Key risk:** AppSec review timeline. Mitigation: frame the review as "review the runtime once" rather than "review the application," which is a dramatically smaller surface area.

**Duration:** 6–8 weeks.

### Phase 2: Seedling Integration (Q3–Q4 2026)

**Goal:** Termin Application Server running on Seedling's ECS/Fargate infrastructure, serving real small business clients.

**What needs to happen:**
- Termin Application Server (multi-app hosting, lifecycle management, dashboard)
- `.termin.pkg` package format implementation
- Seedling AI → `.termin` authoring pipeline (Seedling writes the DSL, compiles, deploys)
- Email-link and OAuth identity bindings
- PostgreSQL storage adapter (if not done in Phase 1)
- `termin shell` REPL for debugging
- Seedling AI monitoring integration (subscribe to TerminAtor Events, proactive maintenance)

**Exit criteria:** At least 3 Sprint engagement clients have applications deployed. The Seedling AI can author, deploy, and monitor a Termin application without human intervention on the `.termin` file.

**Duration:** 8–12 weeks (overlaps with Phase 1 — runtime work is shared).

### Phase 3: Ecosystem Foundation (Q4 2026 – Q1 2027)

**Goal:** The Termin ecosystem becomes real — libraries, providers, registry, community.

**What needs to happen:**
- Provider SDK (Compute and Channel) — developer guide for implementing custom providers in Python and JavaScript
- Library support — `.termin` libraries that export reusable primitives, published to a registry
- Registry implementation (simple HTTP endpoint, backed by S3 or filesystem)
- Provider governance model — review standards, sandboxing approach, trust tiers
- Termin specification v1.0 — stable DSL grammar, stable IR format, stable runtime contracts
- Open source release — compiler, reference runtime, conformance test suite, example applications, all under Apache 2.0
- Community documentation — getting started guide, DSL reference, provider development guide

**Exit criteria:** An external developer (not JL) can install the Termin compiler, write a `.termin` file, compile it, run it on the reference runtime, and deploy it — using only published documentation.

**Duration:** 12–16 weeks.

### Phase 4: Agent Runtime (2027)

**Goal:** AI agents as first-class Compute nodes, with the full agent → runtime primitive → observability pipeline working.

**What needs to happen:**
- AI Compute Provider implementation (wraps Claude API or other LLM)
- Agent tools mapped to runtime primitives (content.query, state.transition, reflect.*, etc.)
- Service identity and delegate identity modes for agent Compute nodes
- Agent prompt versioning and rollback
- Agent observability through Reflection (monitoring dashboard for agent behavior)
- Agent deployment capability (agent writes `.termin`, compiles, deploys to permitted boundary)

**Exit criteria:** A deployed Termin application includes an AI agent Compute node that autonomously triages incoming tickets, and its behavior is fully observable through the standard Reflection/Event system. Seedling AI operates as a meta-agent that monitors and maintains other Termin applications.

**Duration:** 12+ weeks.

---

## Positioning: The Governed Application Substrate

The headline for Termin is not "AI writes your apps." The headline is:

**Termin is a governed application substrate where business software is structurally safe by construction.**

AI-first authorship (Seedling) is a powerful demonstration of what the substrate enables. But the substrate is the thing. The substrate is what AppSec reviews. The substrate is what enterprises adopt. The substrate is what makes the security claims credible.

Seedling is the consumer of the substrate, not its definition. If Seedling disappeared tomorrow, Termin would still be valuable as a platform where any workflow application written in the DSL inherits structural security guarantees without additional engineering effort.

The positioning pyramid:

```
                    Seedling
                 (AI authorship)
               ──────────────────
              Agent Runtime (Phase 4)
            (AI as first-class compute)
          ──────────────────────────────
        Application Server + Ecosystem (Phase 3)
       (multi-app, libraries, providers, registry)
     ──────────────────────────────────────────────
    Enterprise + Seedling Deployments (Phase 1–2)
   (real users, real applications, real feedback)
  ──────────────────────────────────────────────────
 Governed Application Substrate (Phase 0)
(compiler, runtime, IR, conformance, guarantee tiers)
```

Every layer above depends on the layer below. Nothing above works if the substrate isn't sound.

---

## Debugging and Observability

The more declarative a platform becomes, the more transparent its debugging must be. Termin's observability story rests on the principle that **everything that happens is an Event, and everything that exists is Reflectable.**

### When data is silently stripped at a Channel boundary

The Channel enforcer logs a TRACE-level Event: `{ "kind": "confidentiality_strip", "channel": "supplier-feed", "fields_stripped": ["payment_token", "ssn"], "reason": "scope_path_exceeded" }`. An operator can query these Events through Reflection or `termin logs`.

### When a row-level filter removes records

The Content subsystem logs the filter application: `{ "kind": "row_filter_applied", "content": "medical_records", "filter": "record.patient == identity.user_id", "rows_before": 1247, "rows_after": 3, "identity": "nurse-jane" }`.

### When a provider resolves differently than expected

The runtime logs provider resolution: `{ "kind": "provider_resolution", "requested": "ai-agent", "resolved_from": "/org/ai-services/", "provider_version": "1.2.0" }`.

### When an AI agent took an action

Every agent action is a standard primitive operation logged in the Event Bus: `{ "source": "compute:triage-bot", "identity": "service:triage-bot", "action": "state.transition", "target": "tickets/42", "from": "open", "to": "in_progress" }`.

### Operational philosophy

Silent behavior (field stripping, row filtering, schema intersection) is the correct runtime behavior — the system should work correctly without operator intervention. But silent behavior must never be *invisible* behavior. Everything that the runtime does silently is logged. The operator chooses how much they want to see by setting log levels. TRACE shows everything. INFO shows only material events.

---

## Deployment Configuration: Keeping It Bounded

A legitimate concern: if deployment manifests can alter behavior, they risk becoming a second programming model.

**What a deployment manifest can do:**
- Bind logical channel names to concrete boundary paths
- Override provider resolution to a specific boundary path
- Set the confidentiality policy (or inherit from the target boundary's ancestor)
- Configure storage adapter mappings (which database backs "persistent" intent)
- Set log retention policies

**What a deployment manifest cannot do:**
- Add or remove Content types, Compute nodes, State machines, or Channels
- Alter access rules, scope definitions, or role assignments
- Change JEXL expressions or Presentation layout
- Override confidentiality on individual fields
- Grant scopes that aren't declared in the application

The manifest is pure wiring. It connects the application's declared interfaces to the deployment environment's concrete resources. It cannot change what the application does — only where it does it.

---

## The Termin / Seedling Relationship

```
Termin (open source, Apache 2.0)
├── Spec: DSL grammar, primitive model, IR format, runtime contracts
├── Compiler: TatSu PEG parser → IR (Python)
├── Reference Runtime: Python, conformance-tested
├── Conformance Test Suite
├── Registry Protocol
└── Example applications and libraries

Seedling (Clarity Intelligence product)
├── Deploys: Termin spec + compiler + runtime
├── Adds: Termin Application Server (multi-app hosting, lifecycle)
├── Adds: Seedling AI authorship pipeline (PM describes → AI writes → deploy)
├── Adds: Email-link and OAuth identity bindings
├── Adds: PostgreSQL storage adapter
├── Adds: Managed hosting on ECS/Fargate (~$25/month)
└── Adds: Sprint engagement model ($5K/5 days + $250/month)
```

Termin is the substrate. Seedling is one deployment. Enterprise deployments are another. A `.termin.pkg` authored for one deployment can run on another with different identity bindings and storage adapters. The artifact is portable. The deployment is specific.

---

## Success Metrics

### Phase 0 (Proof of Architecture)
- Conformance test suite exists and passes (≥20 tests)
- Runtime is a library, not a code generator
- One `.termin` file → IR → runtime → working app with enforced guarantees

### Phase 1 (Enterprise Pilot)
- One real team uses one real Termin application for ≥4 weeks
- AppSec review of the runtime completed (feedback received)
- Time from `.termin` to deployed application: <1 hour (including CI)
- Zero injection vulnerabilities found in pilot application (Tier 1 guarantee validated)

### Phase 2 (Seedling Integration)
- ≥3 client applications running on Seedling/Termin
- Seedling AI authors a `.termin` file that compiles and deploys without human editing ≥80% of the time
- Client NPS ≥8 for the first 3 Sprint engagements
- Infrastructure cost per application ≤$25/month

### Phase 3 (Ecosystem)
- ≥1 external developer builds and deploys a Termin application using only documentation
- ≥5 published libraries in the registry
- ≥2 third-party providers (Compute or Channel)
- Open source repository has ≥50 GitHub stars within 3 months of release

### Phase 4 (Agent Runtime)
- ≥1 production application with an AI agent Compute node
- Agent actions are fully auditable through Reflection
- Seedling AI operates as a meta-agent managing ≥3 Termin applications

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| AppSec review takes longer than expected | High | Delays Phase 1 exit | Frame review as "review the runtime" not "review the application." Provide conformance test suite as evidence. Start the conversation early. |
| DSL proves too constrained for real applications | Medium | Undermines core thesis | The provider system is the escape hatch. Monitor which features users request that can't be expressed in DSL. Evolve the DSL based on real demand, not speculation. |
| Boundary tree model doesn't fit real org structures | Medium | Requires architectural rework | Ship with tree model, monitor. The DSL syntax is compatible with future evolution to tagged/labeled model. |
| Custom providers reintroduce security problems | High | Undermines security narrative | Provider governance model, sandboxing, Tier 2/3 guarantee distinction. Be honest about what's guaranteed and what's not. |
| Seedling AI produces incorrect `.termin` files | Medium | Client quality issues | Compiler catches most errors. Conformance test suite validates applications. Human review of AI output in Sprint engagements. Improve over time as DSL stabilizes. |
| Nobody cares about structural security | Low | Product without market | Every enterprise that has experienced a security breach cares. Every AppSec team drowning in reviews cares. The question is whether they care enough to adopt a new platform. The pilot proves this. |
