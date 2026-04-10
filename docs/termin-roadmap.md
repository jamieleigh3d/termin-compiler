# Termin Product Roadmap & Backlog

**Last updated:** April 2026
**Maintained by:** Jamie-Leigh Blake & Claude Anthropic

---

## Vision

Termin is a governed application substrate where business software is structurally safe by construction. The roadmap builds from a working compiler and runtime toward an ecosystem where applications are authored by humans or AI, deployed to any environment, and guaranteed to enforce security properties without additional engineering effort.

---

## Phases

### Phase 0: Proof of Architecture (Current — Q2 2026)

**Status:** In progress. Core pipeline and real-time subscriptions working. Legacy code removed. IR schema and implementer's guide published. Focus shifting to end-to-end demo completeness.

| Item | Status | Source Doc | Notes |
|------|--------|-----------|-------|
| TatSu PEG parser (authoritative) | DONE | CLAUDE.md | Replaced lexer + recursive descent + Lark |
| Component tree IR (Presentation v2) | DONE | termin-presentation-ir-spec-v2.md | PageEntry with ComponentNode children |
| Runtime backend (termin_runtime) | DONE | — | 9 modules, ~1200 lines |
| WebSocket multiplexer (Phase 1 distributed) | DONE | termin-distributed-runtime-model.md | Subscribe/push/request, ConnectionManager |
| SSR + vanilla JS hydration (termin.js) | DONE | termin-distributed-runtime-model.md | Client runtime, ~340 lines |
| Seed data support | DONE | — | Auto-seed from companion _seed.json |
| Dependency completeness tests | DONE | — | AST scan of imports vs setup.py |
| String iteration guard tests | DONE | — | Parametrized across all examples |
| Remove legacy code (Lark, codegen, lexer/parser) | DONE | — | ~4,100 lines removed |
| **Field-level confidentiality system** | **PLANNED** | termin-confidentiality-brd.md, termin-confidentiality-spec.md | Tier 1 guarantee. Field redaction, taint propagation, compile-time scope enforcement |
| **Boundary isolation enforcement** | **PLANNED** | termin-appserver-and-ecosystem-v2.md | Cross-boundary data only through declared Channels |
| **State transition scope-gating** | **PLANNED** | termin-product-strategy.md | Runtime rejects transitions caller's identity doesn't permit |
| **Conformance test suite seed** | **PLANNED** | termin-product-strategy.md | 10-20 tests covering Tier 1 guarantees |
| **System-defined CEL functions** | **PLANNED** | termin-appserver-and-ecosystem-v2.md | sum(), count(), now(), identity.has_scope() |
| `boundary_type` in registry response | DONE | termin-appserver-and-ecosystem-v2.md | Forward compat: application/library/module/configuration |
| `client_safe` flag on ComputeSpec | DONE | termin-distributed-runtime-model.md | Added to IR; inference logic pending |
| JSON Schema (draft 2020-12) for IR | DONE | termin-ir-schema.json | Machine-readable contract, validated against all examples |
| Runtime Implementer's Guide | DONE | termin-runtime-implementers-guide.md | Companion to schema for building conforming runtimes |
| `maximum` constraint in PEG grammar | DONE | — | Fixed iteration order bug |
| `State for channel/compute` in PEG parser | DONE | — | Fixed prefix stripping |

**Exit criteria:** Conformance test suite passes. Single `.termin` → IR → runtime → working app with enforced identity, enforced state transitions, enforced boundary isolation, enforced field redaction.

---

### Phase 1: Enterprise Pilot (Q3 2026)

| Item | Status | Source Doc | Notes |
|------|--------|-----------|-------|
| Pluggable Identity subsystem (SSO/SAML/OIDC) | PLANNED | termin-product-strategy.md | Replace stub cookie auth for production |
| PostgreSQL storage adapter | PLANNED | termin-product-strategy.md | Replace SQLite for production workloads |
| `termin deploy` CLI command | PLANNED | termin-product-strategy.md | Package and deploy to target environment |
| Confidentiality enforcement at Channels | PLANNED | termin-confidentiality-spec.md | Field redaction at every Channel crossing |
| Compile-time CEL field dependency analysis | PLANNED | termin-confidentiality-spec.md | Scope requirement inference and validation |
| Taint propagation for derived values | PLANNED | termin-confidentiality-spec.md | Output inherits max input confidentiality |
| Explicit reclassification syntax + audit | PLANNED | termin-confidentiality-spec.md | `Output confidentiality:` in DSL |
| AppSec review of runtime | PLANNED | termin-product-strategy.md | Review once, certify many |
| Pilot application deployed | PLANNED | termin-product-strategy.md | Real team, real use, 4+ weeks |
| Deployment manifest format | PLANNED | termin-distributed-runtime-model.md | JSON mapping boundaries to targets |

**Exit criteria:** One real team uses one real Termin application in production for at least 4 weeks. AppSec has reviewed the runtime.

---

### Phase 2: Application Server (Q3-Q4 2026)

| Item | Status | Source Doc | Notes |
|------|--------|-----------|-------|
| `.termin.pkg` package format | PLANNED | termin-appserver-and-ecosystem-v2.md | manifest.json + IR + source + seed |
| Multi-app hosting (Application Server) | PLANNED | termin-appserver-and-ecosystem-v2.md | Lifecycle management, dashboard |
| Service identity mode for Compute | PLANNED | termin-confidentiality-spec.md | Background jobs, scheduled tasks, elevated Compute |
| Boundary types (application/library/module/configuration) | PLANNED | termin-appserver-and-ecosystem-v2.md | + internal/module type for nested boundaries |
| Library dependencies (import from other packages) | PLANNED | termin-appserver-and-ecosystem-v2.md | Reusable Content/Compute/Channel definitions |
| Multi-service Boundaries (remote targets) | PLANNED | termin-distributed-runtime-model.md | gRPC/HTTP/Lambda protocol adapters |
| Email-link and OAuth identity bindings | PLANNED | termin-product-strategy.md | For small business / Seedling deployments |
| `termin shell` REPL | PLANNED | termin-product-strategy.md | Interactive debugging |

**Exit criteria:** Application Server hosts multiple apps. Package format proven. Service identity working.

---

### Phase 3: Ecosystem Foundation (Q4 2026 - Q1 2027)

| Item | Status | Source Doc | Notes |
|------|--------|-----------|-------|
| Provider SDK (Compute + Channel) | PLANNED | termin-appserver-and-ecosystem-v2.md | Python and JavaScript provider development |
| Provider registry (HTTP endpoint) | PLANNED | termin-appserver-and-ecosystem-v2.md | Publish and discover providers |
| Provider governance model | PLANNED | termin-appserver-and-ecosystem-v2.md | Review standards, trust tiers |
| Library registry | PLANNED | termin-appserver-and-ecosystem-v2.md | Publish and import .termin libraries |
| Termin specification v1.0 | PLANNED | termin-product-strategy.md | Stable DSL, IR, runtime contracts |
| Open source release (Apache 2.0) | PLANNED | termin-product-strategy.md | Compiler, runtime, conformance suite, examples |
| Community documentation | PLANNED | termin-product-strategy.md | Getting started, DSL reference, provider guide |
| Confidentiality scope hierarchies | PLANNED | termin-appserver-and-ecosystem-v2.md | Tree-walk scope resolution |
| Row-level security filters | PLANNED | termin-appserver-and-ecosystem-v2.md | Per-identity record filtering |

**Exit criteria:** External developer builds and deploys a Termin app using only documentation.

---

### Phase 4: Agent Runtime (2027)

| Item | Status | Source Doc | Notes |
|------|--------|-----------|-------|
| AI Compute Provider (Claude API wrapper) | PLANNED | termin-appserver-and-ecosystem-v2.md | Agent as Transform Compute |
| Agent tools mapped to runtime primitives | PLANNED | termin-appserver-and-ecosystem-v2.md | content.query, state.transition, reflect.* |
| Delegate identity mode for agents | PLANNED | termin-confidentiality-spec.md | Agent acts on behalf of caller |
| Agent prompt versioning and rollback | PLANNED | termin-product-strategy.md | Treat prompts as deployable artifacts |
| Agent observability via Reflection | PLANNED | termin-product-strategy.md | Dashboard for agent behavior monitoring |
| Agent-authored application deployment | PLANNED | termin-product-strategy.md | Agent writes .termin, compiles, deploys |

**Exit criteria:** Production app with AI agent Compute node. Agent actions fully auditable.

---

### v1.0 Backlog (Post-Phase 3)

Items deferred to v1.0 or later. Not prioritized, not scheduled. Captured here so they don't get lost.

| Item | Source | Notes |
|------|--------|-------|
| Multi-file apps: composition of multiple .termin source files | appserver-v2.md § Library dependencies | Research needed: import syntax, namespace resolution, cross-file Content/Channel references, compilation order |
| Confidentiality scope hierarchies | appserver-v2.md | Tree-walk scope resolution (e.g., `hr.salary` implies `hr`) |
| Row-level security filters | appserver-v2.md | Per-identity record filtering (only see your own records) |
| Agent-authored application deployment | product-strategy.md | Agent writes .termin, compiles, deploys — requires stable agent runtime first |
| Agent prompt versioning and rollback | product-strategy.md | Treat prompts as deployable artifacts |
| Delegate identity mode for agents | confidentiality-spec.md | Agent acts on behalf of caller (vs service identity) |

---

## Documentation Index

| Document | Purpose | Status |
|----------|---------|--------|
| `CLAUDE.md` | Developer onboarding, architecture overview, key commands | Current |
| `termin-product-strategy.md` | Business strategy, positioning, roadmap phases, success metrics | Current |
| `termin-presentation-ir-spec-v2.md` | Component tree IR specification | Implemented |
| `termin-distributed-runtime-model.md` | WebSocket multiplexing, registry, client runtime | Phase 1 implemented |
| `termin-appserver-and-ecosystem-v2.md` | Application Server, packages, providers, boundaries, agents | Vision (Phase 2-4) |
| `termin-confidentiality-brd.md` | Business requirements for field redaction + taint propagation | Approved |
| `termin-confidentiality-spec.md` | Technical spec: DSL syntax, IR changes, compiler analysis, runtime enforcement | Draft |
| `termin-roadmap.md` | This document — backlog, priorities, implementation order | Living document |
| `termin-ir-schema.json` | JSON Schema (draft 2020-12) for the IR — machine-readable contract | Current |
| `termin-runtime-implementers-guide.md` | How to build a conforming Termin runtime from the IR schema | Current |
| `termin-package-format.md` | `.termin.pkg` package format spec (manifest, versioning, checksums) | Draft |
| `UI-testing.md` | Manual and automated UI testing guide | Current |

---

## Immediate Priority Queue

Restructured April 10, 2026. Blocks A, B, D, F, G (core), H are complete. v0.5.0 compiler and runtime features shipped.

### Completed: Block G — AI Agent Runtime

| # | Item | Status |
|---|------|--------|
| G3 | Agent tool API: ComputeContext tools (content_query/create/update, state_transition) | DONE |
| G4 | AI provider: Anthropic + OpenAI with forced tool_use, thinking-first output | DONE |
| G6 | Event trigger for Computes: Trigger on event with where clause | DONE |
| G7 | Agent demos: agent_simple.termin (Level 1 LLM) + agent_chatbot.termin (Level 3 agent) | DONE |
| G1 | Compute system type in CEL context | DEFERRED to v0.6 (not blocking demos) |
| G2 | Before/After snapshots for postconditions | DEFERRED to v0.6 (not blocking demos) |
| G5 | Runtime scheduler for Trigger on schedule | DEFERRED to v0.6 |
| G8 | Agent observability (trace logging) | DEFERRED to v0.7 |

### Completed: Block H — Semantic Mark Primitive

| # | Item | Status |
|---|------|--------|
| H1 | `Mark rows where [expr] as "label"` grammar + parser | DONE |
| H2 | `Mark [field] where [expr] as "label"` for field-level marks | DONE |
| H3 | `semantic_mark` IR component type | DONE |
| H4 | Runtime renderer: label→CSS + data-termin-mark + aria-label | DONE |
| H5 | Update examples to use Mark...as | Backlogged (existing Highlight still works) |

### Priority 3: Block E — Remaining Quick Wins & Research

Previously "Block E: Research & Future." Items not yet done, reprioritized.

| # | Item | Effort | Subsystems | Notes | Status |
|---|------|--------|------------|-------|--------|
| E7 | Role reflection in CEL: `reflect.role("engineer").Scopes` | Small | runtime (reflection) | termin-cel-types.md § 7 | |
| E11 | Compiler CEL body analysis (field dependencies, reclassification) | Medium | compiler (analyzer) | termin-confidentiality-runtime-design.md § B2 | |
| E2 | Package signatures (cryptographic signing of .termin.pkg) | Research | compiler, runtime | — | |
| E6 | Automation API contract for programmatic UI interaction | Research | runtime, conformance | testing-methodology.md § Tier 3 | |

### Priority 4: Block C — Boundary Enforcement

Deferred until channels and agents are working. Boundaries are metadata-only in the current runtime.

| # | Item | Effort | Subsystems | Notes |
|---|------|--------|------------|-------|
| C1 | Boundary isolation enforcement | Large | runtime (app, storage) | Cross-boundary data only through declared Channels. Depends on F1-F7. |
| C2 | Cross-boundary identity propagation | Medium | runtime (identity, app) | Identity context flows through Channel crossings. |

### Completed Blocks

**Block A: End-to-End Demo Completeness** — DONE (A1-A9)

**Block B: Confidentiality System** — DONE (B1-B11). Grammar, parser, analyzer, lowering, runtime redaction, Compute endpoint, CEL guard, output taint, presentation, HR Portal example.

**Block D: Package Format & Conformance** — DONE (D1-D9). `.termin.pkg`, `termin compile/serve`, conformance suite (251 tests), App Id, IR 0.4.0.

**Block E (completed items):**

| # | Item | Status |
|---|------|--------|
| E3 | Enum constraint enforcement | DONE |
| E4 | Min/max constraint enforcement | DONE |
| E5 | Tier 3: Behavioral round-trip tests | DONE |
| E8 | Triple-backtick multi-line expression parser | DONE |
| E9 | AI agent support: Provider, Preconditions, Postconditions (grammar/IR) | DONE |
| E10 | Transaction staging (snapshot isolation, basic) | DONE |
| E12 | `link_template` on data_table columns | DONE |
| E13 | Server-side CEL evaluation for text components | DONE |
| E14 | Channel Actions: typed RPC verbs in DSL, parser, IR, analyzer | DONE |
| E15 | Event channel sends: `Send X to "channel"` in DSL, parser, IR | DONE |
| E16 | Deploy config schema: `termin.deploy.schema.json` | DONE |
| E17 | Channel demo example: `channel_demo.termin` exercising all patterns | DONE |
| E18 | ~~CCP/ECP package system~~ | KILLED — replaced by Channel Actions (F5) |

**Block F: Channel Runtime** — DONE (F1-F10). Deploy config loader, outbound HTTP/WS dispatch, inbound webhooks, action invocation, event-driven sends, channel reflection, strict validation, auto-generated deploy templates. 36 channel tests.

---

## Design Backlog

Open design questions that need resolution before or during implementation. Each has a name, the open question, and context.

### D-01: Provider Taxonomy and Access Levels

**Question:** How do we formalize the four provider levels and their trust boundaries?

**Context:** Four levels identified:
- **Level 1 — LLM (field-level):** Pure completion. Wired to specific input/output fields. No tools, no AppFabric access. One API call. New syntax: `Input from field X.Y` / `Output into field X.Y`.
- **Level 2 — LLM with context (boundary-scoped):** LLM has read tools scoped to a boundary or declared context. Can explore related data beyond the input fields. Output still typed and constrained to declared fields.
- **Level 3 — Agent (application-scoped):** Full ComputeContext tools (content, state, channel, reflect) but only within the application boundary. Role-based permissions apply. Like a REPL within the app.
- **Level 4 — Agent (configuration-boundary):** Multi-application scope. Can see exposed channels and properties across apps in a configuration boundary. Level 5 variant: admin/superuser can peek into application internals.

Level 1 and 3 are needed for v0.5.0. Levels 2, 4, 5 are future.

### D-02: LLM Field Wiring Syntax

**Question:** What's the exact DSL syntax for connecting LLM input/output to specific fields?

**Context:** JL's direction:
- `Input from field completion.prompt` — reads the prompt field from the triggering record
- `Output into field completion.response` — writes the LLM response to the response field
- `Output new object messages` — creates a new record in a different content type
- Could have multiple inputs (instructions + data), formatted into the system message
- No magic inference — explicit wiring, like a spreadsheet formula connecting cells

### D-03: Implicit Channels in IR

**Question:** Should implicit channels (Compute I/O) be materialized in the IR, or inferred by the runtime?

**Context:** When a Compute declares `Transform: takes messages, produces messages`, that implies two data flows (in/out) that are enforcement points. These are real channels — the runtime should check access control and confidentiality on them. Currently they're not in the IR. Thread 003, open question #4. Related to D-01 Level 3 agent scoping — the implicit channels define what the agent can touch.

### D-04: Events vs Channels as Distinct Primitives

**Question:** What's the precise relationship between events and channels? Are they the same primitive or distinct?

**Context:** JL says don't collapse them. Current model: events fire on content changes (`When note.created`), channels carry data between boundaries. But: an inbound channel creates content which fires an event. An event can trigger a channel send. A channel-triggered Compute is basically an event from a channel. Where does one end and the other begin? Design needed.

### D-05: Compute Access Declarations

**Question:** Should agents declare what content they can access, replacing "takes/produces" for the agent case?

**Context:** `Transform: takes messages, produces messages` works for CEL transforms and LLM completions (deterministic shape). For agents, it's misleading — the agent might query, create, update, delete across its scope. Better: `Accesses messages, findings` — declares what the agent is allowed to touch without implying a functional transform shape. The existing five Compute shapes (Transform, Reduce, Expand, Correlate, Route) may need a sixth shape or a different mechanism for agents.

### D-06: Channel-Triggered Computes

**Question:** Can content be pushed into a channel to trigger a compute transformation pipeline?

**Context:** JL described: send a message on a specific channel → goes to a Compute → transformation → goes to something else → stored or queued. This is a dataflow pipeline declared in DSL. Currently Computes are triggered by events or API calls. Channel-triggered Computes would mean the channel IS the input, and the output goes to another channel or content. This is the streaming/pipeline pattern.

### D-07: Standardized Trace/Log Output Schema

**Question:** What's the standard schema for LLM/agent execution traces, and how is access controlled?

**Context:** Traces should capture: input, system prompt, LLM response (including reasoning/thinking), tool calls, token usage, timing. The schema should be standardized so any Compute provider produces the same shape. Access should be scoped — JL suggested a `logs` verb alongside `read`/`write`/`update`/`delete`. Traces available through reflection API. Traces UI is v0.7 backlog.

### D-08: Event Envelope vs Raw Record

**Question:** When a Compute triggers on an event, should it receive an event envelope or a raw record?

**Context:** Currently the runtime passes the raw record to event handlers. It should pass an event envelope: `{type: "message.created", payload: {the record}, metadata: {timestamp, source, identity}}`. This is how real event-driven systems work. The Compute's trigger declaration says what content type the event carries.

### D-09: Chat Presentation Component

**Question:** What does a chat UI component look like in the Termin presentation IR?

**Context:** The current table-of-messages approach works but isn't a real chat interface. A `chat` component type would render messages as a conversation with proper turn-taking UI, input bar, scroll behavior. Needed for agent_chatbot to look right. Backlogged to v0.5.

### D-10: Default Field Values for Enums

**Question:** Does `defaults to "user"` work for enum fields with string literal defaults?

**Context:** We have `defaults to` syntax for CEL expressions. The chatbot needs `Each message has a role which is one of: "user", "assistant", defaults to "user"`. Need to verify the compiler handles this. If not, it's a small grammar/parser fix.

### D-11: Auto-Generated REST API

**Question:** Should Content types auto-generate CRUD API routes by convention?

**Context:** Currently every example explicitly declares `Expose a REST API at /api/v1:` with every route listed. This is boilerplate. Every Content type should get standard CRUD routes automatically. The explicit section should only be needed for customization (extra transition endpoints, restricted access, custom paths). JL asked: "Why do we expose the REST API like we do? Is it because we like doing REST APIs?"

### D-12: LLM Response Structured Output

**Question:** How does the LLM return structured data for the Level 1 completion case?

**Context:** For `Output into field completion.response`, the runtime needs the LLM to return text that goes into that field. Simple for single-field output. But what about multi-field output? The LLM could return JSON, or use a structured output schema, or the runtime could use tool_use with a return schema matching the output fields. JL mentioned XML-tag-wrapped output as current industry practice. The runtime needs a convention.

### D-13: Inbound Channel Hosting

**Question:** Does the runtime host the webhook endpoint, or is that an infrastructure concern (API Gateway routes to runtime)?

**Context:** Thread 003, Q1. The reference runtime currently registers `/webhooks/{name}` routes (Block F). But in production, an API Gateway or load balancer might front the runtime. Who owns the endpoint URL? The deploy config has the URL, but for inbound channels, that's the URL the *external service* calls. The runtime needs to know its own public URL to register webhooks. Or: this is purely deployment config and the runtime just listens.

### D-14: Inbound Payload Transformation

**Question:** When an external webhook payload arrives in a different schema than the Content type, how is it transformed?

**Context:** Thread 003, Q2. A GitHub webhook payload doesn't match a Termin Content schema. Options: CEL transform on the Channel declaration, an Adapter Compute between Channel and Content, or a mapping in deployment config. This affects how Channel-triggered Computes (D-06) work.

### D-15: Request Channels (Synchronous Bidirectional)

**Question:** How does a Compute synchronously query an external API and get a response?

**Context:** Thread 003, Q3. An agent needs to call an external API (e.g., AWS IAM) and wait for a response. The current Channel model is fire-and-forget (send) or persistent (WebSocket). A request channel is: send request, get response, continue. Is this a Channel direction ("request"), a special action pattern, or a different primitive? Affects `channel.invoke()` semantics.

### D-16: Inbound Channel Event Integration

**Question:** When an inbound Channel creates a Content record, does it automatically fire `When` event triggers?

**Context:** Thread 003, Q5. The reference runtime already does this (Block F: webhook creates record, calls `run_event_handlers`). But is this automatic behavior part of the spec, or should the Channel declaration explicitly say it? If automatic, any inbound data always fires events. If declared, the Channel controls whether events propagate.

### D-17: Block C Architectural Inputs

**Question:** Three decisions needed for boundary enforcement: (1) Multiple boundaries in one process or separate? (2) How are inter-boundary channels materialized — HTTP, in-process, message queue? (3) Does the reference runtime enforce "only through Channels" or is that distributed-only?

**Context:** From termin-confidentiality-runtime-design.md § Block C Inputs. Deferred until v0.6.0. Depends on D-03, D-04, D-13.

### D-18: Audit Declaration on ContentSchema

**Question:** How should builders declare what's safe to log per content type?

**Context:** Thread 004 (an AWS-native runtime-ai). HRBP case management where presence of a record is safe to log but content is sensitive. Proposed: `audit: actions | content | none` on ContentSchema. `actions` = log event type + record ID but not field values. `content` = log everything. `none` = suppress logging. Connects to D-07 (trace schema) — agent traces should respect audit declarations. Relates to confidentiality system (different axis: confidentiality = who sees data, audit = what gets logged).

### D-19: Dependent Field Values / Cascading Constraints

**Question:** How should the DSL declare when one field's value constrains another field's allowed values?

**Context:** Thread 004 (an AWS-native runtime-ai). Retail product configurator: selecting "MacBook Pro 16-inch" narrows RAM to [16, 32, 48]. an AWS-native Termin runtime hits this with hierarchical configuration (region → compliance framework → data residency). Proposed IR shape: `dependent_values: [{when: {field, equals}, then: {field, allowed}}]`. Keeps constraint graph in IR for UI generation, validation, optimization. Concern: simple `when/then` covers 80% but doesn't compose for multi-field or range constraints. Need to decide if this is a flat list or a composable constraint graph.

---

## v0.5.0 Dependency Analysis

What must be resolved to ship v0.5.0 to an AWS-native Termin runtime. Chain of dependencies from examples → design questions → implementation blocks.

### Critical Path

```
agent_simple.termin (example)
  ├── D-02: LLM field wiring syntax (Input from / Output into)
  │     └── Grammar + parser + IR changes
  ├── D-10: defaults to "user" for enum fields
  │     └── Verify or fix in compiler
  ├── D-12: LLM structured output convention
  │     └── How runtime maps LLM response → output fields
  ├── G1: Wire Compute system type into CEL context
  │     └── Compute.Name, Compute.Scopes for preconditions
  └── G4: AI provider integration (Anthropic + OpenAI)
        └── Deploy config ai_provider section
              └── Block F: Deploy config (DONE)

agent_chatbot.termin (example)
  ├── Everything from agent_simple, plus:
  ├── D-05: Compute access declarations (what agent can touch)
  ├── D-08: Event envelope vs raw record
  ├── G3: ComputeContext tool API (content.query/create/update, state.transition)
  │     ├── G2: Before/After snapshots (for postconditions)
  │     └── Block F: Channel dispatcher (DONE) — for channel.invoke/send tools
  └── G6: Event trigger for Computes (Trigger on event)

channel_demo.termin / channel_simple.termin (validation)
  └── Block F: Channel Runtime (DONE)

security_agent.termin (stretch goal, validates full model)
  ├── Everything from agent_chatbot, plus:
  ├── G5: Runtime scheduler (Trigger on schedule)
  └── Full postcondition enforcement with rollback
```

### Resolution Order (suggested)

Phase 1 — Design decisions (can be done in parallel):
```
D-02  LLM field wiring syntax        ← blocks grammar work
D-10  defaults to "user"             ← quick verify/fix
D-12  Structured output convention   ← blocks runtime LLM integration
D-05  Compute access declarations    ← blocks agent scoping
D-08  Event envelope vs raw record   ← blocks event trigger for Computes
```

Phase 2 — Compiler changes (sequential, TDD):
```
Write agent_simple.termin in desired DSL
  → Fix grammar: Input from field / Output into field / Provider is "llm"
  → Fix parser + AST + IR + lowering
  → Conformance tests for expected IR
Write agent_chatbot.termin in desired DSL
  → Fix grammar: Trigger on event, defaults to "user"
  → Fix parser + AST + IR + lowering
  → Conformance tests for expected IR
```

Phase 3 — Runtime implementation (parallel where possible):
```
G4: AI provider (Anthropic + OpenAI SDK)   ← can start during Phase 2
G1: Compute system type in CEL             ← small, independent
G3: ComputeContext tool API                ← largest item, blocks agent_chatbot
G6: Event trigger for Computes             ← medium, blocks agent_chatbot
G2: Before/After snapshots                 ← needed for postconditions
```

Phase 4 — Integration testing:
```
agent_simple end-to-end: form → event → LLM → response appears
agent_chatbot end-to-end: message → event → agent → reply appears
Conformance suite updated with agent tests
```

### Not blocking v0.5.0

These are important but can ship after:
```
D-03  Implicit channels in IR         → v0.6.0
D-04  Events vs channels design       → v0.6.0
D-06  Channel-triggered Computes      → v0.6.0
D-07  Trace/log schema                → v0.7.0
D-09  Chat presentation component     → v0.7.0
D-11  Auto-generated REST API         → v0.7.0
D-13  Inbound channel hosting         → v0.6.0
D-14  Inbound payload transformation  → v0.6.0
D-15  Request channels                → v0.6.0
D-16  Inbound channel event firing    → already implemented, needs spec
D-17  Block C architectural inputs    → v0.6.0
H1-5  Mark...as semantic emphasis     → v0.5.0 (independent track)
G5    Runtime scheduler               → stretch goal for v0.5.0
```

---

## v0.6.0 Backlog

Items planned for v0.6.0 (after v0.5.0 ships to an AWS-native Termin runtime).

| Item | Effort | Source | Notes |
|------|--------|--------|-------|
| Structured English compiler errors | Medium | Thread 004 § 2 | Error codes (TERMIN-E001+), fuzzy-match suggestions ("did you mean?"), `--format json` for Console consumption |
| Boundary isolation enforcement (Block C) | Large | appserver-v2.md | Cross-boundary data only through declared Channels. Depends on Block F. |
| Cross-boundary identity propagation | Medium | distributed-runtime.md | Identity context flows through Channel crossings |

---

## v0.7.0 Backlog

Examples cleanup, advanced examples, and polish.

| Item | Source | Notes |
|------|--------|-------|
| Traces UI: page showing LLM/agent execution logs | D-07 | Content table of standardized trace records, queryable via reflection |
| Chat presentation component | D-09 | Replace table-of-messages with proper chat UI in agent_chatbot |
| Advanced agent example with postconditions and rollback | Block G | Demonstrate pre/postcondition enforcement, Before/After snapshots |
| Auto-generated REST API (convention over configuration) | D-11 | Content types get CRUD routes automatically, explicit section for overrides only |
| Examples audit: remove boilerplate, use latest syntax | — | All examples updated to use v0.5 features (field wiring, Mark...as, auto-API) |

---

## v1.0 Backlog

Items deferred to v1.0 or later. Not prioritized, not scheduled.

| Item | Source | Notes |
|------|--------|-------|
| Multi-file apps: composition of multiple .termin source files | appserver-v2.md § Library dependencies | Research needed: import syntax, namespace resolution, cross-file Content/Channel references |
| Localization layer for end-user labels | Thread 004 § 3 | Labels become keys, `localization/` dir in .termin.pkg with `{lang}.json` files |
| Diff preview: `termin diff base.termin proposed.termin` | Thread 004 § 4 | IR structural diff with human-readable English output for Console change review |
| Confidentiality scope hierarchies | appserver-v2.md | Tree-walk scope resolution (e.g., `hr.salary` implies `hr`) |
| Row-level security filters | appserver-v2.md | Per-identity record filtering |
| Agent-authored application deployment | product-strategy.md | Agent writes .termin, compiles, deploys |
| Agent prompt versioning and rollback | product-strategy.md | Treat prompts as deployable artifacts |
| Delegate identity mode for agents | confidentiality-spec.md | Agent acts on behalf of caller (vs service identity) |
| Package signatures (cryptographic signing) | — | Signing and verification of .termin.pkg |
| Singular/plural plausibility validation | — | Compiler warns if Content name and singular (`Each X has`) look unrelated (e.g., "echoes" vs "banana"). Basic containment check, not full NLP. |

---

## Completed Work Log

| Date | Item | Commit(s) |
|------|------|-----------|
| 2026-04-05 | Presentation IR v2: component trees replace flat PageSpec | e1aa60f |
| 2026-04-05 | Fix aggregation key slugification (projectboard sum bug) | 083f986 |
| 2026-04-05 | Distributed runtime Phase 1: bootstrap + reactive client | ce7a7de |
| 2026-04-05 | Seed data support + dependency completeness tests | 0c0bb71 |
| 2026-04-05 | Fix or_list parsing (greedy words + off-by-one) | 2fffff5 |
| 2026-04-05 | Fix WebSocket: add websockets dependency | 8ebac7b |
| 2026-04-05 | Remove Lark parser (881 lines) | a08b950 |
| 2026-04-05 | Remove old codegen.py (1,744 lines) | 81063bc |
| 2026-04-05 | Remove legacy lexer + parser (1,466 lines) | 61dbbb8 |
| 2026-04-05 | Fix PEG gaps: maximum constraint, State for channel | 61dbbb8 |
| 2026-04-05 | Confidentiality BRD, tech spec, product roadmap | 8c7f8c4..ed469eb |
| 2026-04-06 | Add boundary_type + client_safe to IR | 8730ef1 |
| 2026-04-06 | JSON Schema (2020-12) + Runtime Implementer's Guide | 20de60e |
| 2026-04-06 | A1-A3, A5-A8: State transitions, CEL functions, highlight, buttons, forms | be7ef84..b358407 |
| 2026-04-07 | Fix action button visibility + highlight + transition endpoint | 9bc6abb..746c003 |
| 2026-04-07 | `defaults to [expr]` with User identity object | b358407 |
| 2026-04-07 | JEXL → CEL migration (Phases 0-3) | 297073f..417da41 |
| 2026-04-07 | A9: Conformance test suite (206 tests, 25 categories) | 5c3c4d9 |
| 2026-04-07 | Fix 17 pre-existing test failures + enum validation | 58808db |
| 2026-04-07 | `.termin.pkg` format spec | a921377 |
| 2026-04-07 | App Id: compiler-managed UUID | 95475ea |
| 2026-04-07 | `termin compile` → .termin.pkg + `termin serve` | b4fadf1 |
| 2026-04-07 | IR version bump to 0.3.0 | 285ad37 |
| 2026-04-07 | termin-conformance repo published (189 tests, 12 files) | github.com/jamieleigh3d/termin-conformance |
| 2026-04-07 | Tier 2: IR-driven presentation contract tests (23 tests) | conformance repo |
| 2026-04-07 | Fix distinct/reference filter rendering | 4838878 |
| 2026-04-08 | Phase 3 CEL migration: rename JEXL→CEL in comments, retire stale test_conformance.py | 0af7d0a |
| 2026-04-08 | Block B (B1-B11): Confidentiality system — grammar, parser, analyzer, lowering, runtime redaction, Compute endpoint, CEL guard, output taint, presentation, JSON Schema, Implementer's Guide | fa3c96d..61bcf87 |
| 2026-04-08 | HR Portal example app + 48 confidentiality conformance tests | 7368c6d, 96037e3 |
| 2026-04-08 | CEL types reference doc (User, Compute, Before, After) | c67c5fb |
| 2026-04-08 | Migrate hello_user from CurrentUser to User.* | c67c5fb |
| 2026-04-08 | Expression delimiter: [bracket] → backtick, IR 0.4.0 | 3420606..c274217 |
| 2026-04-08 | Conformance suite: 249 tests, 7 fixtures, IR 0.4.0 changelog | conformance repo |
| 2026-04-09 | Channel Actions: grammar, parser, AST, IR, lowering, analyzer for typed RPC verbs on Channels | — |
| 2026-04-09 | Event channel sends: `Send X to "channel"` grammar, parser, IR, lowering | — |
| 2026-04-09 | Deploy config schema: `termin.deploy.schema.json` (v0.1.0) | — |
| 2026-04-09 | `security_agent.termin` example (action channels, agent computes, event sends) | — |
| 2026-04-09 | `channel_demo.termin` example (all 6 channel patterns + seed data) | — |
| 2026-04-09 | Roadmap restructured: Blocks F (Channel Runtime) and G (Agent Runtime) as v0.5.0 critical path | — |
| 2026-04-09 | Block F complete: Channel runtime (outbound HTTP/WS, inbound webhooks, actions, events, deploy config) | a588070..7b4d21d |
| 2026-04-09 | `channel_simple.termin` loopback demo working end-to-end | da639e5..eb2cb4e |
| 2026-04-09 | Singular field added to ContentSchema IR (fixes pluralization) | 957b317 |
| 2026-04-10 | Design decisions D-02 (field wiring), D-05 (Accesses), D-08 (event envelope), D-12 (structured output) | f899496..2973af3 |
| 2026-04-10 | Design backlog D-01 through D-17, v0.5 dependency analysis | fbd0745 |
| 2026-04-10 | Compiler: Input/Output field wiring, Accesses, Directive, trigger where clause, ComputeShape.NONE | 66f6a4a |
| 2026-04-10 | Block G: AI provider (Anthropic + OpenAI), event-triggered Computes, ComputeContext tools | 166479c |
| 2026-04-10 | Block H: Mark...as semantic emphasis (grammar, parser, IR, renderer) | 77e71c0 |
| 2026-04-10 | Agent examples: agent_simple.termin (Level 1 LLM) + agent_chatbot.termin (Level 3 agent) | 66f6a4a |
| 2026-04-10 | 480 tests passing (24 new agent/mark tests) | ce2ef73 |
