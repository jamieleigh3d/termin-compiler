# Termin Product Roadmap & Backlog

**Last updated:** April 2026
**Maintained by:** Jamie-Leigh Blake & Claude Anthropic

---

## Vision

Termin is a governed application substrate where business software is structurally safe by construction. The roadmap builds from a working compiler and runtime toward an ecosystem where applications are authored by humans or AI, deployed to any environment, and guaranteed to enforce security properties without additional engineering effort.

---

## Phases

### Phase 0: Proof of Architecture (Current — Q2 2026)

**Status:** Complete. All exit criteria met. v0.5.0 shipped April 10, 2026. v0.6.0 in progress on `feature/v0.6`.

| Item | Status | Notes |
|------|--------|-------|
| TatSu PEG parser (authoritative) | DONE | Replaced lexer + recursive descent + Lark |
| Component tree IR (Presentation v2) | DONE | PageEntry with ComponentNode children |
| Runtime backend (termin_runtime) | DONE | 12 modules, ~2400 lines |
| WebSocket multiplexer | DONE | Subscribe/push/request, ConnectionManager |
| SSR + vanilla JS hydration (termin.js) | DONE | Client runtime, ~340 lines |
| Seed data support | DONE | Auto-seed from companion _seed.json |
| Dependency completeness tests | DONE | AST scan of imports vs setup.py |
| String iteration guard tests | DONE | Parametrized across all examples |
| Remove legacy code (Lark, codegen, lexer/parser, pyjexl backend) | DONE | ~6,200 lines removed total |
| Field-level confidentiality system | DONE | Block B: redaction, taint, CEL guard, output taint |
| Boundary isolation enforcement | DONE | Block C: implicit app boundary, containment map, channel-only crossing |
| State transition scope-gating | DONE | Runtime rejects transitions caller's identity doesn't permit |
| Conformance test suite | DONE | 531 tests across 18 test files |
| System-defined CEL functions | DONE | sum(), count(), now(), User.* identity object |
| JSON Schema (draft 2020-12) for IR | DONE | Machine-readable contract, validated against all examples |
| Runtime Implementer's Guide | DONE | Companion to schema with WebSocket behavioral contract (§13.2) |
| Code coverage baseline | DONE | pytest-cov, 69% floor, 77% current |

**Exit criteria met:** Conformance test suite passes. `.termin` → IR → runtime → working app with enforced identity, state transitions, boundary isolation, and field redaction. Two runtimes (reference + an AWS-native Termin runtime) consuming the same conformance suite.

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
| Operator-supplied custom migration validators | migration-classifier-design.md §3.12.3 | v0.9 ships only auto validation (FK check, row counts, schema metadata, smoke read). Custom validators would let operators express domain invariants (e.g., "every order has a non-null customer post-migration"); reserve a Protocol method later if/when needed. |
| Sub-language escape mechanism for host-language interpolation | termin-source-refinements-brd-v0.9.md (BRD #3) Appendix B | Unified syntax for interpolating host-language values (Principal fields, content references) into sub-language strings — Directive bodies, Objective bodies, opaque CEL contexts, future provider-defined sub-languages. Each sub-language provider declares its escape form. v0.9 sidesteps this via the field-reference form (BRD #3 §6) plus a session-prep compute. Forcing function: more agent-shaped applications with parameterized prompts in production use. |
| Alternate first-party LLM and ai-agent products (Bedrock, OpenAI, Gemini, local-LLM) | compute-provider-design.md §3.2, §8 (resolved Q9) | v0.9 ships Anthropic + stubs only as first-party. The Phase 3 contract abstraction is one-product-deep on the LLM and ai-agent contracts; per BRD §10's Phase 4 reasoning ("three providers because one isn't enough to prove the abstraction holds"), v1.0 should add at least one alternate product per contract to prove the same for compute. Bedrock is the natural first add (same model family, different transport). Tier-2 third-party adoption can wait until first-party plurality exists. |
| Tiered AI-agent contracts (sandboxed-agent, orchestrator-agent, etc.) | compute-provider-design.md §3.3 | v0.9 ships two named compute contracts (`llm`, `ai-agent`) plus the implicit `default-CEL`. Future capability tiers — sandboxed agents with read-only tool surfaces, orchestrator agents with multi-compute coordination, etc. — would land as new contracts within the existing compute category. The `ContractRegistry.register_contract` mechanism already supports adding contracts at runtime, so this is provider-author-friendly without spec evolution. Each new contract needs its own conformance pack. |

---

## Documentation Index

| Document | Purpose | Status |
|----------|---------|--------|
| `CLAUDE.md` | Developer onboarding, architecture overview, key commands | Current |
| `termin-product-strategy.md` | Business strategy, positioning, roadmap phases, success metrics | Current |
| `termin-presentation-ir-spec-v2.md` | Component tree IR specification | Implemented |
| `termin-distributed-runtime-model.md` | WebSocket multiplexing, registry, client runtime | Phase 1 implemented |
| `termin-appserver-and-ecosystem-v2.md` | Application Server, packages, providers, boundaries, agents | Vision (Phase 2-4) |
| `termin-confidentiality-brd.md` | Business requirements for field redaction + taint propagation | Implemented (Block B) |
| `termin-confidentiality-spec.md` | Technical spec: DSL syntax, IR changes, compiler analysis, runtime enforcement | Implemented (Block B) |
| `termin-roadmap.md` | This document — backlog, priorities, implementation order | Living document |
| `termin-ir-schema.json` | JSON Schema (draft 2020-12) for the IR — machine-readable contract | Current |
| `termin-runtime-implementers-guide.md` | How to build a conforming Termin runtime from the IR schema | Current |
| `termin-package-format.md` | `.termin.pkg` package format spec (manifest, versioning, checksums) | Draft |
| `UI-testing.md` | Manual and automated UI testing guide | Current |

---

## Immediate Priority Queue

Restructured April 11, 2026. v0.5.0 shipped. v0.6.0 "Boundaries" in progress on `feature/v0.6`.

### v0.6.0 "Boundaries" — In Progress

| # | Item | Status | Notes |
|---|------|--------|-------|
| G1 | Compute system type in CEL context | DONE | v0.6 |
| G2 | Before/After snapshots for postconditions | DONE | v0.6 — ContentSnapshot class |
| G5 | Runtime scheduler for Trigger on schedule | DONE | v0.6 |
| D-18 | Audit levels: actions/debug/none | DONE | v0.6 — default "actions" (pit of success) |
| D-19 | Dependent field values (When clauses, is-one-of) | DONE | v0.6 |
| C1 | Boundary isolation enforcement | DONE | v0.6 — implicit app boundary, containment map, channel-only crossing |
| — | Structured English compiler errors | DONE | v0.6 — error codes, fuzzy match suggestions, --format json |
| — | Legacy backend removal (fastapi.py + PageSpec) | DONE | v0.6 — ~2,100 lines removed |
| — | Code coverage baseline (pytest-cov, 69% floor) | DONE | v0.6 — 77% current |
| C2 | Cross-boundary identity propagation | PLANNED | Identity context flows through Channel crossings |
| — | Conformance suite update for v0.6 | PLANNED | Audit, dependent values, boundaries |

### Remaining Quick Wins & Research

| # | Item | Effort | Subsystems | Notes | Status |
|---|------|--------|------------|-------|--------|
| E7 | Role reflection in CEL: `reflect.role("engineer").Scopes` | Small | runtime (reflection) | termin-cel-types.md § 7 | |
| E11 | Compiler CEL body analysis (field dependencies, reclassification) | Medium | compiler (analyzer) | termin-confidentiality-runtime-design.md § B2 | |
| E2 | Package signatures (cryptographic signing of .termin.pkg) | Research | compiler, runtime | — | |
| E6 | Automation API contract for programmatic UI interaction | Research | runtime, conformance | testing-methodology.md § Tier 3 | |

### Completed Blocks

**Block A: End-to-End Demo Completeness** — DONE (A1-A9)

**Block B: Confidentiality System** — DONE (B1-B11). Grammar, parser, analyzer, lowering, runtime redaction, Compute endpoint, CEL guard, output taint, presentation, HR Portal example.

**Block C: Boundary Enforcement** — DONE (v0.6). Implicit app boundary, containment map, channel-only crossing enforcement, duplicate-content analyzer check (TERMIN-S030). No backward-compat exceptions — the app itself is always a boundary.

**Block D: Package Format & Conformance** — DONE (D1-D9). `.termin.pkg`, `termin compile/serve`, conformance suite (475 tests), App Id, IR 0.5.0.

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

**Block G: AI Agent Runtime** — DONE (G1-G7). AI provider (Anthropic + OpenAI), event-triggered Computes, ComputeContext tools, Before/After snapshots, runtime scheduler. G8 (observability) deferred to v0.7.

**Block H: Semantic Mark Primitive** — DONE (H1-H4). Grammar, parser, IR component, runtime renderer with label→CSS + ARIA.

---

## Design Backlog

Open design questions. Resolved decisions moved to `termin-roadmap-archive.md`.

### D-06: Channel-Triggered Computes

**Question:** Can content be pushed into a channel to trigger a compute transformation pipeline?

**Context:** JL described: send a message on a specific channel → goes to a Compute → transformation → goes to something else → stored or queued. This is a dataflow pipeline declared in DSL. Currently Computes are triggered by events or API calls. Channel-triggered Computes would mean the channel IS the input, and the output goes to another channel or content. This is the streaming/pipeline pattern.

### D-07: Standardized Trace/Log Output Schema

**Question:** What's the standard schema for LLM/agent execution traces, and how is access controlled?

**Context:** Traces should capture: input, system prompt, LLM response (including reasoning/thinking), tool calls, token usage, timing. The schema should be standardized so any Compute provider produces the same shape. Access should be scoped — JL suggested a `logs` verb alongside `read`/`write`/`update`/`delete`. Traces available through reflection API. Traces UI is v0.7 backlog.

### D-09: Chat Presentation Component

**Question:** What does a chat UI component look like in the Termin presentation IR?

**Context:** The current table-of-messages approach works but isn't a real chat interface. A `chat` component type would render messages as a conversation with proper turn-taking UI, input bar, scroll behavior. Needed for agent_chatbot to look right. Backlogged to v0.5.

### D-11: Auto-Generated REST API

**Question:** Should Content types auto-generate CRUD API routes by convention?

**Context:** Currently every example explicitly declares `Expose a REST API at /api/v1:` with every route listed. This is boilerplate. Every Content type should get standard CRUD routes automatically. The explicit section should only be needed for customization (extra transition endpoints, restricted access, custom paths). JL asked: "Why do we expose the REST API like we do? Is it because we like doing REST APIs?"

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

Resolved design decisions (D-01 through D-05, D-08, D-10, D-12, D-17, D-18, D-19) and the v0.5.0 dependency analysis are archived in `termin-roadmap-archive.md`.

---

## v0.6.0 Backlog — "Boundaries"

Theme: enforcement, quality, cleanup. Branch: `feature/v0.6`.

| Item | Effort | Status | Notes |
|------|--------|--------|-------|
| Structured English compiler errors | Medium | DONE | Error codes (TERMIN-S/X/W), fuzzy-match, `--format json` |
| D-18: Audit levels (actions/debug/none) | Small | DONE | Default "actions" (pit of success) |
| D-19: Dependent field values | Medium | DONE | When clauses, is-one-of constraint modifier |
| G1: Compute system type in CEL | Small | DONE | |
| G2: Before/After snapshots | Medium | DONE | ContentSnapshot, postcondition 409s |
| G5: Runtime scheduler | Small | DONE | Trigger on schedule |
| Block C: Boundary enforcement | Large | DONE | Implicit app boundary, containment map |
| Legacy backend removal | Small | DONE | ~2,100 lines of dead code |
| Code coverage baseline | Small | DONE | pytest-cov, 69% floor, currently 77% |
| Cross-boundary identity propagation | Medium | PLANNED | Identity context flows through Channel crossings |
| Conformance suite v0.6 update | Medium | PLANNED | Tests for audit, dependent values, boundaries |

---

## v0.7.0 Backlog

Theme: polish, observability, developer experience.

### Completed

| Item | Source | Status |
|------|--------|--------|
| G8: Agent observability (trace logging) | D-07, D-20 | DONE — AUDIT verb, auto-generated audit log Content, trace recording, redaction |
| Chat presentation component | D-09 | DONE — `chat` IR type, integrated input, WebSocket subscription |
| Auto-generated REST API | D-11 | DONE — Every Content gets CRUD at `/api/v1/{content}` automatically |
| Flash/toast notification primitive | Thread 006 | DONE — Toast/banner feedback with CEL interpolation |
| Compound verb fix | Thread 007 | DONE — PEG grammar for all verb combinations + TERMIN-S031 safety net |
| Compiler fidelity tests | 007 post-mortem | DONE — 356 tests asserting specific IR properties per example |
| Close PEG grammar gaps | 007 post-mortem | DONE — All 39 fallback paths closed to 0 |
| Replace hard-coded string offsets | 007 post-mortem | DONE — 48 offsets replaced with `len("prefix")` |
| Refactor: app.py (2105→385 lines) | Code audit | DONE — Split into 8 modules |
| Refactor: peg_parser.py (1345→273 lines) | Code audit | DONE — Split into 4 modules |
| Refactor: lower.py (1096→745 lines) | Code audit | DONE — Pages extracted (remaining tightly coupled) |
| Refactor: channels.py (826→415 lines) | Code audit | DONE — Config + WS extracted into 2 modules |
| Version bump to 0.7.0 | — | DONE — compiler, IR, runtime all at 0.7.0 |

### Remaining — ship with v0.7.0

| Item | Source | Effort | Notes |
|------|--------|--------|-------|
| **Fix Level 1 LLM prompt mapping** | Thread 009 | Small | `system = directive + objective`, `user = input field values only`. Currently objective in user turn. |
| **Make directive optional** | Thread 009 | Small | Don't inject default directive when only Objective is declared. Analyzer warn if no prompts. |
| **Compiler-controlled thinking field** | Thread 009 | Small | `set_output` tool only includes `thinking` when compute's output schema declares it. |
| **UAT tests 4–12** | v0.7 UAT plan | Medium | Headless service, helpdesk, security agent, compute demo, hrportal, projectboard, smoke test |

### Remaining — defer to v0.8 if needed

| Item | Source | Effort | Notes |
|------|--------|--------|-------|
| Code coverage push (6 modules) | — | Medium | ai_provider 69%, channel_ws 32%, channels 64%, confidentiality 64%, transitions 67%, websocket_mgr 73%. Overall runtime: 82%. |
| Conformance: one_of_values runtime enforcement | Thread 010 | Small | POST invalid enum → 422. Runtime has it; conformance suite doesn't test it. |
| Conformance: dependent_values runtime enforcement | Thread 010 | Small | `when` clause CEL eval on create/update → 422. Runtime has it; conformance doesn't test. |
| Structured 422 error format | Thread 010 | Small | Standardize `{"detail", "field", "constraint", "allowed"}`. |
| Fix conformance button assertion coupling | Thread 008 | Small | Tests rendering strategy not behavioral contract. Decouple. |
| Fail-loud parser fallbacks | 007 post-mortem | Medium | 14 fallback paths silently return defaults. Grammar gaps are closed but fallback code still exists. |
| Round-trip DSL→IR fidelity | 007 post-mortem | Medium | Parse → compile → verify every declared feature appears in IR. |
| Refactor: analyzer.py (780 lines) | Code audit | Low | Deferred — single class, flat methods, acceptable at current size. |
| Refactor: presentation.py (599 lines) | Code audit | Low | Deferred — renderers + templates, acceptable at current size. |

---

## v0.8.0 Backlog

Theme: presentation completeness, action primitives, launch-ready polish.

| Item | Source | Notes |
|------|--------|-------|
| **Edit action button** | v0.7 UAT | DSL primitive for "Edit" action that links a row to a pre-filled edit form. Currently only transition actions exist. Need: `"Edit" links to "Edit Product"` syntax, row-to-form data binding, pre-populated fields. |
| **Delete action button** | v0.7 UAT | DSL primitive for "Delete" action on table rows. Currently only available via API. Need: `"Delete" deletes if available, hide otherwise` syntax, confirmation dialog, scope-gated visibility. |
| **Inline edit** | v0.7 UAT | Alternative to edit-page: click a cell to edit in place. Scope-gated per field. May be a presentation hint (`editable: true`) rather than a new component. |
| **Pagination** | D-11 | Auto-CRUD list endpoint returns all records. Need `?limit=20&offset=0` for production use. |
| **Filtering/sorting via API** | D-11 | Query params: `?status=active&sort=created_at:desc`. |
| **Token-by-token LLM streaming** | D-09 | Chat component currently gets full message on completion. Need delta streaming via WebSocket for real-time typing effect. |
| **Manual compute trigger endpoint** | v0.7 UAT | `POST /api/v1/compute/{name}/trigger` to manually fire any compute regardless of declared trigger type (schedule/event). Currently agent computes can only be invoked by their trigger — no way to test audit logging without AI credentials + waiting for schedule. |
| **Presentation polish pass** | Launch readiness | Make the reference runtime's default HTML output not-crappy so first-time-user first impressions match the rest of the project's care. Scope: restrained color/typography system matching termin.dev, readable table density, proper form-input styling, clear focus/hover/disabled states on buttons, page chrome (header/nav spacing), responsive at least to tablet. Bounded: CSS + template edits only, no new components or IR fields. Explicitly NOT the presentation-provider architecture — that is v0.9. |
| **Close PUT-route state-machine backdoor** | v0.8 sprint finding | The auto-CRUD `PUT /api/v1/{content}/{id}` route does not detect state-machine field changes — it blindly writes the new value. A caller can bypass the transition rules *and* the `required_scope` on the transition by PUT-ing `{"status": "approved"}` directly, even if they lack `orders.admin`. Fix: PUT handler detects writes to state-machine-backed columns, routes them through `do_state_transition` (reusing the same code path as `/_transition/...`). Adds defense-in-depth tests covering scope-bypass attempts. Must land before v0.8.0 — this is a latent security issue. Separate from #5 Edit action button (which orchestrates via the transition endpoint and does not require the PUT fix to land first). |

---

## v0.8.1 Backlog — Maintenance release

**Theme:** maintenance — release-process fixes and issues raised against
v0.8.0. Non-breaking. No new DSL, IR, or runtime features. Release-
script fidelity, test-robustness, and release-artifact drift corrections.

Tracking: target tag `v0.8.1` on both compiler and conformance repos
after the work below is committed and verified in one pass through the
full release pipeline (run `util/release.py`, run both test suites,
tag, push).

| Item | Source | Notes |
|------|--------|-------|
| **Release-artifact drift in v0.8.0 tag** | Post-release review (JL) | The `fixtures/ir/*.json` files in the conformance repo's v0.8.0 tag were stale — `warehouse_ir.json` was missing 132 lines (Edit + Delete action buttons + `edit_modal` component). Also, the compiler's authoritative `docs/termin-ir-schema.json` was missing `edit_modal` in the ComponentNode type enum, causing strict-validator adapters to reject every v0.8 warehouse app's IR. Both already fixed in working commits; will ship under v0.8.1. Root cause: `v0.8.0` was tagged and pushed before running `util/release.py` + verifying fixtures. Process lesson recorded — never tag before releasing through the script. |
| **conformance #2: `test_action_buttons_labeled` assumes literal text** | GitHub conformance#2 | The test asserted the literal button label (e.g., `"Delete"`) appears in rendered HTML, which rejects runtimes that render buttons with icons + `aria-label` or rely solely on `data-termin-delete` markers. Fix: accept any of the canonical marker attribute (`data-termin-<action>`), literal label text, or `aria-label="<label>"`. The data-termin-* markers are the spec's canonical affordance — literal text is reference-runtime convention, not contract. Already fixed in working tree. |
| **conformance #1: session-fixture FK robustness in DELETE access matrix** | GitHub conformance#1 | `test_warehouse_access_matrix[warehouse manager-DELETE-products-200]` may false-fail when the session-scoped `warehouse` fixture has accumulated inbound FK references from earlier tests. FK enforcement correctly returns 409; the test wants to test scope gating, not referential integrity. Fix: before the DELETE, clear any `stock_levels` rows referencing the product under test. Cannot reproduce on current `main` but the defensive fix lands regardless. Already fixed in working tree. |
| **compiler #1: PEG grammar coverage gap (environment-dependent)** | GitHub compiler#1 | Reporter observed 857 TatSu fallbacks on `feature/v0.8` / Python 3.10. Cannot reproduce on Python 3.11 (0 fallbacks on both `main` and the cited commit). Commented on the issue asking for exact TatSu version + OS. If the environment-dependent bug is confirmed, fix may land in v0.8.1; otherwise close as un-reproducible. |

### v0.8.1 release checklist (when ready to tag)

1. `python util/release.py --compiler-version 0.8.1 --ir-version 0.8.0` (IR schema unchanged — only patch bump)
2. Both compiler + conformance test suites green (1525+ / 778+ / 10 browser)
3. `git tag -a v0.8.1 -m "…"` on both repos
4. `git push origin main && git push origin v0.8.1` on both

---

## v0.8.2 Backlog — Feature + bug fixes

**Theme:** the features and DSL/runtime fixes originally slated for
v0.8.1 before it became a maintenance-only release.

| Item | Source | Notes |
|------|--------|-------|
| ~~PEG gap: `Accesses` line with multiple content names~~ | v0.8 UAT (JL) | **RESOLVED.** `tests/test_compiler_fidelity.py::TestZeroPEGFallbacks::test_no_tatsu_fallbacks` passes — TatSu parses `Accesses messages, products` cleanly through the existing `accesses_list` rule. `examples-dev/agent_chatbot2.termin` is no longer blocked on this; promotion needs a different fix (its `"Label" transitions to <state>` action lines are pre-v0.9 syntax — the renaming pass JL flagged for "agent data streams" should also bring those up to the `transitions <field> to <state>` v0.9 form). |
| ~~Stale `app_seed.json` between test runs~~ | v0.8 sprint finding | **OBSOLETE.** v0.9 retired the legacy `.py + .json + _seed.json` sidecar codegen path; `.termin.pkg` carries seed bytes inside the archive, so there's no sidecar to go stale. `tests/test_cli.py` documents the obsolescence. |
| **uvicorn `ws="websockets-legacy"` deprecation warnings** | v0.8 sprint | Our own test code is migrated to `websockets.asyncio.client` (slice 7.2.f cleanup, 2026-04-30). Remaining warnings come from uvicorn 0.38's internal websockets-legacy path. `ws="websockets-sansio"` was evaluated during v0.8 and caused full-suite WS hangs (cumulative event-loop state intolerance). Revisit once uvicorn upgrades sansio reliability. Not blocking — uvicorn-internal noise only. |
| **`input_type="state"` dropdown on create forms** | v0.8 review | The edit-modal state-field dropdown correctly filters to valid transitions + user scopes. The same renderer path for create forms (if a content with a state machine exposes one) could receive the same treatment. Currently out of scope because `Accept input for …` forms don't usually include the state field (initial state is implied). Log for when a customer asks. |
| ~~Release script test-run hang under `capture_output=True`~~ | v0.8.1 release prep | **RESOLVED.** `util/release.py::run_tests` (lines 278–310) streams pytest output line-by-line via `Popen` with `-u` instead of `subprocess.run(..., capture_output=True)`. The Windows + Miniconda buffer hang is gone; `--skip-tests` is no longer required for releases. The general `subprocess.run(..., capture_output=True)` rule for long-running pytest commands still applies in test code (see user-level CLAUDE.md), but the release script itself is fixed. |

---

## v0.9.0 Backlog

Theme: provider architecture — pluggable presentation, storage, identity, compute.

| Item | Source | Notes |
|------|--------|-------|
| **Presentation provider architecture** | Vision Layer 2 | Plugin interface letting the same `.termin` application target different design systems without code changes. Scope: provider registry, presentation-provider interface in the IR contract, at least one real alternative provider implementation (e.g., a Carbon-style provider) alongside the default, documentation for authoring a new provider. Separated from v0.8 polish because this is architectural new surface area that should not be rushed as part of the pre-launch crunch. |
| **Storage provider architecture** | Vision Layer 2 | Pluggable storage backend. SQLite today, PostgreSQL as the first real alternative provider. Runtime storage interface, migration path for existing `.termin.pkg` archives across providers. |
| **Identity provider architecture** | Vision Layer 2, roadmap §Deployment | Pluggable identity subsystem. Replaces the stub cookie-based auth with a proper provider interface supporting enterprise single-sign-on standards (SSO, SAML, OIDC). |
| **Provider Tier 2 review framework** | guarantees Tier 2 | The review standard that gates which providers inherit structural guarantees. Currently undesigned; likely a volunteer-reviewer pool with no commercial funding path. |
| **Multi-state-machine per content** | v0.8 sprint finding | The current runtime assumes one state machine per content with a hard-coded `status` column (`storage.py:146`, `lower.py:103` `sm_by_content` dict keyed by content name, `context.py:46` `sm_lookup`, `state.py:33`). A second state machine on the same content silently overwrites the first. The DSL grammar already permits multiple state machines per content (e.g., a Product could have a `lifecycle` state machine AND an `approval_status` state machine). Work: (1) rename the state column per SM — e.g. `products.lifecycle`, `products.approval_status` — instead of a single `status`; (2) upgrade `sm_by_content` to `dict[str, list[StateMachine]]`; (3) update PUT + transition routes to address the right column; (4) update the edit form renderer (v0.8 item #5) to show one dropdown per state field; (5) conformance tests for the multi-SM case. **Landed on `feature/v0.9` 2026-04-24 — see `docs/design-multi-state-machine.md`.** |
| **Compute access grant: scope-based canonical, role-based removed** | v0.9 BRD review (JL 2026-04-25) | Two forms exist in v0.8 grammar: `Anyone with "<scope>" can execute this` (scope-based, used in 9/10 examples) and `"<role>" can execute this` (role-based, used only in `hello_user.termin`). They translate to different IR — the role form ties source to a specific role name, the scope form survives role renames. Make scope-based canonical: (1) remove the `ComputeAccessRole` alternative from the `compute_access_line` grammar rule; (2) update the analyzer to reject the role-based form with a helpful error pointing at the scope-based equivalent; (3) migrate `hello_user.termin` to `Anyone with "app.view" can execute this`; (4) update Appendix A in the provider BRD to use the scope form. Aligns compute access with Content access (which is already scope-only) and the audit-over-authorship tenet (scopes are the structural contract; role names are organizational). **Landed on `feature/v0.9` 2026-04-25 — see TestRoleBasedComputeAccessRemoved in tests/test_parser.py.** |
| **Principal-as-typed-reference (design decision needing attention)** | v0.9 BRD review (JL 2026-04-25) | The provider BRD §6.1 defines a `Principal` record on the Identity contract (id, type, claims). Apps that store per-user data (reputation, preferences, profile) need to key rows on the Principal — the moderation agent example in `examples-dev/v09_moderation_agent.termin` shows the workaround: a plain text `principal_id` field, with no compile-time check that it's actually a Principal id. **Design issue**: introduce a built-in `Principal` reference type usable in source like `Each user reputation has an identity which references Principal, restrict on delete`. Storage providers store as text (no provider-interface change), but the source-level type carries intent and the analyzer can check that lookups go through the right paths. Alternatives considered: (a) deep coupling between Storage and Identity contracts (rejected — too much surface), (b) leave it as text forever (rejected — loses compile-time safety). Decision needed before Phase 1 (Identity extraction) lands or as a Phase 1 follow-up. |
| **Migration conformance pack (Phase 2.x follow-on)** | journal 2026-04-26, migration-contract.md | Cross-runtime conformance for the v0.9 migration story: classifier behaves the same on every runtime, ack-gating gates the same way, provider `migrate()` is atomic across fault stages, v0.8 → v0.9 round-trip preserves data. **Landed on conformance feature branch — 73 tests across 5 files (`test_v09_migration_classifier.py`, `_ack_gating.py`, `_apply.py`, `_fault_injection.py`, `_e2e.py`); spec at `specs/migration-contract.md`; v0.8 fixture captured via temp-clone of v0.8.1 tag.** Closes the Phase 2.x deferred item. |
| **Phase 3–6 conformance packs (per-phase follow-on)** | journal 2026-04-27, channel-provider-design.md, compute-provider-design.md | Cross-runtime conformance for v0.9 Phases 3, 4, 5, and 6 — to be authored by a separate agent after each phase lands on `feature/v0.9`. The reference runtime ships with phase-specific tests in `termin-compiler/tests/` (e.g., `test_v09_compute_grants.py`, `test_v09_channel_compiler.py`, `test_v09_channel_providers.py`); these validate the **reference implementation specifically**. The conformance pack is the thinner adapter-agnostic layer in `termin-conformance/` that any conforming runtime must satisfy — same shape as the Phase 1 (`test_v09_multi_state_machine.py`) and Phase 2.x (`test_v09_migration_*.py`) packs already shipped. Sequencing constraint: each pack can only be written after that phase merges to `feature/v0.9` and `util/release.py` regenerates the `.termin.pkg` fixtures with the new IR fields — otherwise fixtures will lack `provider_contract`, `failure_mode`, tool-surface grants, audit-record fields, etc. **Per-phase scope:** **Phase 3 (compute provider registry, slices a–e):** spec at `specs/compute-contract.md` covering the `llm` / `ai-agent` / `default-CEL` contracts, the registry resolution surface (`Category.COMPUTE`, contract, product), tool-surface gating (Accesses ∪ Reads for read tools, Accesses-only for write/state tools per slice (c)), audit record reshape per BRD §6.3.4 (slice (d) — Principal fields, scope/role provenance), and refusal + Acts-as + sidecar semantics (slice (e)). Tests should cover stub-product fallback, agent loop with tool surface, audit record shape, refusal propagation, Acts-as principal substitution. **Phase 4 (channel provider registry, slices a–c):** spec at `specs/channel-contract.md` covering the four contracts (webhook / email / messaging / event-stream) with their per-contract send signatures, registry surface (`Category.CHANNELS`, contract, product), `bindings.channels` deploy shape (v0.8 top-level `channels` fallback rejected), failure-mode semantics (`log-and-drop` default never raises; `surface-as-error`; `queue-and-retry`), strict-mode contract (outbound channel with `provider_contract` and no binding → startup raises `ChannelConfigError`), inbound channel auto-route registration (`POST /webhooks/<snake-name>`). ~25–30 tests covering dispatch, provider isolation (two channels same contract → distinct instances), unknown-product → stub fallback, log-and-drop never raises, strict-mode raises, inbound webhook lands payload in correct content. Reference dispatcher tests in `termin-compiler/tests/test_v09_channel_providers.py` are the model; conformance tests should be adapter-agnostic. Compiler-side codes (TERMIN-S026/S027/S028/S029) stay in the compiler suite — conformance is runtime behavior only. **Phase 5 + Phase 6:** scope to be defined when those phases are spec'd; same pattern applies — when each phase lands on `feature/v0.9`, the assigned conformance agent regens fixtures, writes a spec markdown in `termin-conformance/specs/`, writes adapter-agnostic tests in `termin-conformance/tests/test_v09_<phase>_<slice>.py`, and runs the suite under both `TERMIN_ADAPTER=reference` and `TERMIN_ADAPTER=served-reference`. **Estimate:** Phase 4 ~half a day (smaller surface than migrations); Phase 3 ~full day (more contracts, audit reshape, sidecar). Phases 5–6 unknown until spec'd. **References for the implementing agent:** the Phase 2.x migration pack (`termin-conformance/tests/test_v09_migration_*.py`, `specs/migration-contract.md`) is the gold-standard precedent for structure and depth; the journal entry on 2026-04-26 (night) describes the workflow including the v0.8 round-trip fixture capture pattern. |

---

### v0.9 Phase Status (current as of 2026-04-29)

The v0.9.0 backlog table above lists the original architectural targets;
this section breaks down where each implementation phase actually stands
and what's queued. Order reflects the agreed sequencing: serial work
first to lock in patterns, then parallel/sub-agent slices, then
cross-repo conformance, then Phase 7 capstone.

#### Phase 5 — Presentation Provider System (BRD #2)

| Slice | Status | Notes |
|---|---|---|
| 5a — `tailwind-default` + theme + redaction + colorblind safety | ✅ done | landed earlier, all five sub-slices on `feature/v0.9-presentation` |
| 5b.1 — `Using "<ns>.<contract>"` grammar | ✅ done | parse rule + override-mode validation |
| 5b.2 — two-pass compilation machinery | ✅ done | shipped with 5b.1 |
| 5b.3 — multi-provider dispatch | 🟡 partial | slim B'-only dispatch landed 2026-04-29 (`_populate_presentation_providers`); full per-render dispatch (SSR + CSR providers coexisting per contract) deferred until needed |
| 5b.4 platform — bundle discovery + `Termin.registerRenderer` | ✅ done | `/_termin/presentation/bundles` + JS API |
| 5b.4 B' plumbing — bootstrap-payload + page-data + shell endpoints | ✅ done | client-side `Termin.action()` dispatch (no `/_termin/action` server endpoint, per Q-extra) |
| 5b.4 B' loop wiring — entry-point discovery + bundle-serving + page-route cut-over | ✅ done | landed 2026-04-29; `pip install -e ../termin-spectrum-provider` is enough for `provider: "spectrum"` to resolve in deploy config; `/<slug>` serves shell when CSR-only provider bound |
| 5b.4 Spectrum v0.1.0 — scaffold + `page` + `text` contracts | ✅ done | landed 2026-04-29; first time anything in Termin rendered through a CSR provider |
| 5b.4 Spectrum data-table | ✅ done | landed 2026-04-29; TableView + ActionButtonGroup + per-row Termin.action dispatch (transition + delete kinds; edit deferred to form-edit-modal slice). visible_when CEL evaluation is the largest deferred piece — every action renders unconditionally and the runtime returns 403/409 on invalid ones |
| 5b.4 Spectrum form | ✅ done | landed 2026-04-29; Form + TextField + NumberField + Picker for text/number/currency/enum/state/reference input types. Submits via Termin.action({kind: "create"}) and reloads on success. Update mode + after_save navigation parsing deferred |
| 5b.4 Spectrum styling polish + esbuild CSS-inject plugin | ✅ done | landed 2026-04-29; per-component CSS injection so Spectrum's macro-bundled stylesheets actually reach the document; `light-dark()` color-scheme propagation; shell template no longer loads `/runtime/termin.css` (was leaking SSR-Tailwind typography into the bundle) |
| 5b.4 Spectrum markdown / metric / nav-bar / toast / banner | ✅ done | landed 2026-04-29; lightweight HTML renderers, ~0 KB bundle growth, 9 of 10 presentation-base contracts now live |
| 5b.3 — full multi-provider dispatch | ✅ done | landed 2026-04-29; Tailwind reaches `_populate_presentation_providers` via setup.py entry point alongside Spectrum; default-Tailwind synthesis when no binding declared; explicit binding overrides synthesis. Per-component override-mode dispatch (Tailwind page + Spectrum data-table) deferred to v0.10 — needs a rewrite of the SSR Jinja path |
| 5b.4 Spectrum chat | ✅ done | landed 2026-04-29; div + Spectrum TextField/Button composer (no `<form>` element — native form submission would 405 against the GET-only page route); subscribes to `content.<source>` for persisted-message arrival and `compute.stream.*` for streaming token deltas; pending-bubble-per-invocation pattern. Surfaced two latent termin.js bugs in the WS subscription path (legacy-state poisoning, onopen-replay miss) — both fixed with regression tests |
| 5b.5 — GOV.UK SSR provider | ⬜ deferred to v0.10 | Tailwind-as-plug-in fills the "second provider proves the surface" closure target for v0.9; GOV.UK gets its own slice in v0.10 with proper accessibility-review attention |
| 5c.1 — contract package YAML format + loader | ✅ done | format + `ContractPackageRegistry` shipped earlier; deploy-config wiring (`_load_contract_packages` + `ctx.contract_package_registry`, path resolution relative to deploy file, fail-closed on missing/malformed/collision) landed 2026-04-29 |
| 5c.2 — grammar table extension + verb collision detection | ✅ done | landed 2026-04-29; pure-Python `package_verb_matcher` (no TatSu — sidesteps the WSL context-state-leak entirely); classifier hook + `package_contract_line` parse handler + `PackageContractCall` AST + lowering to `ComponentNode(type="package_contract", contract=<qualified>)`. Cross-package verb collisions caught at registry-load time per BRD §4.5 |
| 5c.3 — runtime contract-package provider dispatch | ✅ done | landed 2026-04-29; `_populate_presentation_providers` consults `ctx.contract_package_registry` for namespace expansion. Bindings keyed on package namespaces fan out to every contract the package declares, same shape `presentation-base` already used |
| 5c.4 — Airlock contract package end-to-end | ✅ done | landed 2026-04-29; 5-test proving-ground at `tests/test_v09_airlock_proving_ground.py`. Source line `Show a cosmic orb of scenarios` → PackageContractCall AST node → ComponentNode with `contract = "airlock-components.cosmic-orb"` → bound `_StubAirlockProvider` returns the placeholder div per design doc Q6 |
| 5c.5 — presentation conformance pack | ✅ done | landed 2026-04-29 (cross-repo: `termin-conformance` `feature/v0.9`); 33 tests + spec at `specs/presentation-contract.md`. Covers Provider Protocol shape, binding resolution (every clause), contract package loading (every clause). Per-component override-mode dispatch + SSR-via-render_ssr deferred to v0.10 conformance pack additions |
| Spectrum-vs-Tailwind feature parity (item 0 before Phase 7) | ✅ done | landed 2026-04-29; three gaps closed: (0.1) page chrome — bootstrap payload now carries `app_chrome` with app name + filtered nav items + role switcher state + username field; Spectrum bundle renders header via new `<PageChrome>` component with Spectrum primitives. (0.2) security-trimmed row actions — bootstrap pre-evaluates each row_action's `visible_when` (scope check for delete/edit, state-machine + scope check for transition) and attaches `__visible_actions: [<label>...]` to each row; data-table filters by label. (0.3 sweep) audit confirmed nothing else missing within the stated scope; deferred polish: page-title sync on Termin.navigate, `unavailable_behavior="disable"` distinct rendering — both v0.10. Suite: 2541 on Windows |

#### Phase 6 — Source Refinements (BRD #3)

| Slice | Status | Notes |
|---|---|---|
| 6a / 6a.6 — Principal type + ownership + `the user` + preferences + cascade | ✅ done | full Principal type, ownership cascade through subscriptions |
| 6b — state-machine transition events | ✅ done | typed event payloads |
| 6c — agent directive sourcing (`Directive from deploy config "..."`, `Directive from <content>.<field>`) | ✅ done | both inline-literal and reference forms |
| 6d — hardening + migration | 🟡 partial | examples migrated; remaining cleanup likely small; finalize during Phase C |

#### Cross-cutting / journal-flagged

| Item | Status | Notes |
|---|---|---|
| WebSocket → provider-subscription dispatch wiring | ✅ done | landed 2026-04-29; `handleFrame`'s push branch dispatches to both the legacy `notifySubscribers` (SSR hydrators) AND `_dispatchToProviderSubscriptions` (B' provider subscriptions). CSR providers' `Termin.subscribe(channel, handler)` now fires on real push events |
| Page-route cut-over to shell when CSR-only provider bound | ✅ done | landed 2026-04-29 |
| `_resolve_page_for` permissive role match | ✅ done | landed 2026-04-29; single-variant slugs return their page regardless of user role (auth enforced downstream); multi-variant slugs fall back to first when no role matches. Matches SSR pipeline's behavior — fixed a UX regression where stale `termin_role=Anonymous` cookies 404'd role-restricted pages |
| Access-rule fallback fidelity (TatSu WSL bug) | ✅ done | landed 2026-04-29; `_parse_can_clause_fallback` extracts verbs from the `can` clause when TatSu falls back, replacing the hardcoded `verbs=["view"]` that silently rewrote `update`/`delete` rules as view-only on WSL |

#### Recommended sequencing

- **Phase A — serial, supervised (next):** Spectrum data-table → Spectrum form. JL eyes-on for browser verification; locks the integration pattern that subsequent contracts inherit.
- **Phase B — parallel sub-agents (after A):** dispatch sub-agents on (1) the five simple Spectrum contracts, (2) GOV.UK SSR provider scaffold, (3) 5c.2 grammar extension, (4) WebSocket-to-provider-subscription wiring. JL reviews each returned commit. Phase 6d cleanup runs on the same track.
- **Phase C — serial, cross-repo:** 5b.4 Spectrum chat (eyes-on), 5c.3+5c.4 contract-package runtime dispatch + Airlock proving ground (eyes-on), 5c.5 presentation conformance pack. **All landed 2026-04-29 in the autonomous run that closed Phase 5+6.**
- **Phase 7 — termin-core extraction (v0.9 capstone):** see below.

---

## Phase 7 — termin-core extraction (v0.9 capstone)

Theme: extract the shared library surface that any conforming Termin
runtime imports from, so a second runtime (e.g., an AWS-native runtime
implementation, a Rust port, or any third-party implementation) doesn't
have to vendor or re-implement the contract Protocols, the Principal
type, the IR types,
the Redacted sentinel, the categorical channel/storage/identity/compute
contract definitions, and the deploy-config binding resolver.

This is GitHub issue #2 ("extract termin-core library surface") and it
intentionally lands at the **end of v0.9**, not in the middle. The
reasoning: every Phase 5 + Phase 6 slice teaches the project something
about which surfaces are stable enough to extract and which are still
shifting. Extracting too early would freeze immature contracts; doing
it after Phase 5/6 lock means the extraction is mechanical rather than
exploratory.

| Item | Source | Notes |
|------|--------|-------|
| **`termin-core` package surface** | issue #2; v0.9 capstone | A new `termin-core` Python package (sibling repo `termin-core/`) containing: contract Protocols (`PresentationProvider`, `StorageProvider`, `IdentityProvider`, `ComputeProvider`, `ChannelProvider`); the categorical `Category` enum and `ContractRegistry`; the IR dataclasses (`AppSpec`, `ComponentNode`, `PageEntry`, etc.) — read-only types only, not the lowering pass; the `Principal` / `Redacted` / `PrincipalContext` value types; the deploy-config binding resolver. The reference runtime (`termin_runtime`) and the compiler (`termin`) both depend on `termin-core`. Alternate runtimes (AWS-native, third-party Rust ports, etc.) depend on `termin-core` only — no transitive dependency on the reference runtime's storage / FastAPI / SQLite layers. |
| **Cheap discipline before extraction** | feedback_commit_norm + journal | While Phase 5/6 land, when new pure rules ship (e.g., the `_populate_presentation_providers` resolver, the `page_should_use_shell` predicate, contract-package merge logic), drop them in a thin enforcement module rather than weaving into runtime-specific code. This makes the eventual extraction mechanical: "move this module into `termin-core`, fix imports, done." JL has confirmed this discipline is worth the friction. |
| **Language-neutral contract surface (forward-looking)** | issue #2 / 2026-04-28 briefing | Python Protocols can't be imported from Rust. The Phase 7 extraction may also surface a parallel JSON-Schema-shaped contract surface so non-Python runtimes can validate against the same shapes. Decision deferred to the start of Phase 7; doesn't block the Python-side extraction. |
| **`termin-core` conformance pack** | extension of 5c.5 | Adapter-agnostic tests verifying any runtime that imports `termin-core` produces correct binding resolution, contract-Protocol satisfaction, IR-shape acceptance. Lands with the extraction or shortly after. |

**Sequencing constraint:** Phase 7 starts when **Phase 5 + Phase 6 land
on `feature/v0.9` and the conformance pack passes**. Doing it earlier
risks freezing surfaces that later phases would need to revise.

### Slice progress

| Slice | Status | Scope |
|---|---|---|
| 7.1 | ✅ **landed 2026-04-30 evening** | Pure types and Protocols extracted: `termin_core.providers`, `termin_core.ir`, `termin_core.expression`, `termin_core.confidentiality`, `termin_core.errors`. Six target subtrees. Reference runtime imports from `termin-core` via back-compat shims. Compiler suite still 2545 passing on Windows; conformance suite still 915 passing. |
| 7.2 | pending | Framework-agnostic dispatch — define `TerminRequest` / `TerminResponse` / `TerminWebSocket` abstractions on top of ASGI substrate (Q1=b). Move `routing/{crud,channel_dispatch,route_specs}.py` into `termin-core`. Reference runtime gains a `fastapi_adapter.py` shim; no behavior change end-to-end. Slice 7.2 also catches the slice-7.1 deferrals: `termin_runtime/{validation,state,transitions}.py` get extracted once `termin-core` ships `TerminValidationError` / `TerminTransitionError` exception types that adapters translate to their framework's error envelope. |
| 7.3 | pending | Extract `termin-server`. Move FastAPI app, builtins (sqlite/anthropic/tailwind-ssr), static assets, CLI from `termin-compiler` repo into a new sibling. Compiler/conformance import paths flip to `termin-server`. |
| 7.4 | pending | Compiler IR-types unification. `termin/ir.py` re-export shim deletes; compiler imports IR types directly from `termin-core`. |
| 7.5 | pending | Conformance pack + cleanup. New `termin-core-conformance` test pack inside `termin-conformance/`. All slice-7.1 / 7.2 / 7.3 / 7.4 back-compat shims drop. `termin_runtime` package alias deletes. |



**Out of scope for Phase 7 (deferred to v1.0 prep):**

- Splitting the compiler (`termin/`) into its own repo — Phase 7 only
  extracts the runtime / contract surface, not the compiler. Compiler
  decoupling is a v1.0 conversation.
- Versioning policy for `termin-core` releases — likely SemVer-aligned
  with the runtime, but the policy text itself is a Phase 7 deliverable.
- Multi-language ports of `termin-core` itself — issue #2 anticipated
  this; the Python-side extraction is the v0.9 deliverable.

---

## v0.10.0 Backlog — "App Server / Distributed Runtime"

Theme: a hosted Termin app server on `termin.dev` plus the conformance plumbing
runtime implementers need to validate distributed runtimes that cannot be
exercised in-process.

The motivation for the theme: not every conforming runtime can be tested
locally with the in-process `reference` adapter or the served-uvicorn
`served-reference` adapter. A runtime built on a managed cloud platform
(AWS, container orchestrators, serverless) needs to deploy a real instance
and validate behavior over real HTTP. Today the conformance suite has no
adapter that targets such a deployment, which means runtime implementers
have to build a private fork of the conformance plumbing to run the suite
against their stack. Closing this gap removes a friction point for any
runtime built on a non-process-local execution model.

| Item | Source | Notes |
|------|--------|-------|
| **Hosted Termin app server on `termin.dev`** | v0.10 theme | Stand up a real-HTTP deployment of the reference fixtures (warehouse, helpdesk, projectboard, approval_workflow) on `termin.dev`. Used as the reference live target for conformance runs and as a public demo surface. Topology: **per-fixture subdomain** (`warehouse.termin.dev`, `helpdesk.termin.dev`, ...) — gives each app its own cookie scope so the `termin_role` and `termin_user_name` cookies on one fixture don't leak across to another, simplifies CORS posture per-app, and lets each fixture have its own DNS+TLS lifecycle. Path-prefix hosting was considered and rejected on the cookie-scoping argument. |
| **Live-HTTP conformance adapter** | conformance#3 (2026-04-25) | Add a fourth adapter `LiveHttpAdapter` (file: `adapter_live_http.py`). `deploy()` reads `TERMIN_BROWSER_BASE_URL`, returns an `AppInfo` with `base_url=<that url>` and `cleanup=None`. `create_session()` overrides default header injection: when `TERMIN_SERVICE_TOKEN` is set, every request carries `Authorization: Bearer $TERMIN_SERVICE_TOKEN` for the deployment perimeter (Cloudflare Access / IAP / equivalent — NOT Termin identity, which still uses `termin_role` / `termin_user_name` cookies set per-test). The Playwright `browser_context` fixture reads `extra_http_headers` from an adapter hook so the same Bearer token reaches the browser. Scope: browser-marked tests only (`pytest -m browser`); the conftest enforces this — non-browser tests skip when the adapter is `live-http`, because the conformance suite is not idempotent and would pollute a shared deployment. The `seeded_warehouse` fixture is unchanged: `warehouse.post()` already routes through the adapter's session and hits the right base URL. Runtime implementers can point this adapter at their own deployment to validate their stack — does not require `termin.dev` specifically. |
| **Schema / DB lifecycle for the hosted deployment** | v0.10 theme | Hosted deployment needs a redeploy story: which version is live, how IR-schema changes (e.g. v0.8 → v0.9) get applied to the existing DB, when test data is reset between conformance runs. Likely a per-fixture isolated DB plus a "reset to seed" endpoint that's gated by the deployment-perimeter token. |
| **Boundary config `final` markers** | Provider BRD §8 review (JL 2026-04-25) | v0.9 boundary merge is pure key-level shallow with leaf-wins on conflicting values. v0.10 adds a `final` flag on a config entry so that a higher-boundary value cannot be overridden by lower levels. Motivating case: enterprise-wide identity provider — root admin binds the company SSO product and marks it `final`, preventing any org / app / team from accidentally downgrading to a stub or different SSO. Other plausible candidates: storage encryption settings, audit-log retention windows, required confidentiality scopes. Shape: `{"identity": {"provider": "okta", "config": {...}, "final": true}}`. The Phase 0 binding resolver in v0.9 was deliberately kept in pure leaf-wins so partial enforcement doesn't create false expectations; v0.10 lifts the resolver to scan for `final` and refuse leaf overrides at finalized keys. |
| **`termin migrate --chain` CLI utility** | migration-contract.md §8.4 | Operator convenience: chain v0.X → v0.X+1 → ... → v0.Z deploys in one command. Pulls each intermediate package version, presents the cumulative ack list once, runs each per-deploy migration in turn, stops at first failure with a resumable state. Thin orchestration layer over the per-deploy migration contract — does NOT change the runtime's per-IR-transition semantics. The runtime stays simple; the CLI is the operator-friendly wrapper. Forcing function: a v0.6 production database upgrading to v0.9 (or whatever the current version is). Without this tool, the operator manually runs four sequential deploys with separate ack reviews. With it, one command and one consolidated review. |
| **`queue-and-retry` channel failure-mode implementation** | v0.9.1 deferral; channel-provider-design.md §6.3; channel-contract.md §5.4 | The dispatcher already accepts the grammar (renamed from `queue-and-retry-forever` in v0.9.1 because "forever" was operationally wrong — a poison payload would requeue without bound). v0.9.x falls back to log-and-drop with a logged warning. v0.10 lands the actual retry worker. Design: **(1) Persistent queue table** `_termin_channel_queue` (id, channel_name, payload JSON, enqueued_at, retry_count, last_error, last_attempt_at, next_attempt_at) created at app init, scoped per-app. **(2) Async worker task** registered at startup, cancelled at shutdown; wakes on a tick interval (default 5s), picks rows where `next_attempt_at <= now`, attempts `provider.send()`, on success deletes the row, on failure increments retry_count and computes `next_attempt_at = now + min(initial_backoff * 2^retry_count, backoff_cap)`. Defaults: `initial_backoff=30s`, `backoff_cap=1h`, so the schedule walks 30s → 60s → 2m → 4m → 8m → 16m → 32m → 1h → 1h → 1h … . **(3) Send path** — when `failure_mode == "queue-and-retry"` AND the synchronous attempt fails, enqueue the payload and return `{"ok": False, "outcome": "queued", "channel": <display>, "queue_id": <id>}`. The outcome is `"queued"`, not `"failed"`. **(4) Dead-letter table** `_termin_channel_dead_letter` — when a row's `enqueued_at + max_retry_hours < now` and the next attempt is still failing, the worker moves it from `_termin_channel_queue` to dead-letter with the final error. Operators inspect via reflection `/api/v1/_runtime/dead_letter` (gated by deploy-config service token). **(5) Configuration** — per-channel deploy override `bindings.channels.<name>.failure_config.max_retry_hours: <int>` (default reasonable, e.g. **6 hours**; MUST NOT exceed **24h** — runtime rejects deploys with values outside `[1, 24]` at startup). Initial backoff and cap also overrideable, but most apps will use defaults. **(6) Observability** — queue-depth gauge per channel + dead-letter row count visible via reflection; `/api/v1/_runtime/queue_status` lists current queue rows for an operator. **(7) Conformance test for v0.10** — replaces the v0.9.1 skip in `test_v09_channel_failure_modes.py::TestQueueAndRetry::test_queue_shape_with_retry_worker_v010` with deterministic assertions against the queued-shape, the backoff schedule (mock the clock), the dead-letter migration, and the operator endpoints. **Estimated effort**: ~6–8 hrs for the basic implementation; another ~2 hrs for observability + conformance. Scope-creep risk: do NOT add multi-process queue coordination (one worker per app instance is fine for v0.10 — multi-instance coordination is a v1.0+ distributed-runtime concern). |
| **Explicit picker binding for chat-driving tables** | v0.9.2 chat-surface review (JL 2026-05-05) | The v0.9.2 chat hydrator wires "table → chat" via implicit discovery: any `data_table` whose `data-termin-source` matches a sibling `[data-termin-chat]`'s source becomes a row picker, click → `_terminSwitchThread`. Works for the demonstrated case (one table + one chat for the same content), but it's spooky-action-at-a-distance for compositions with two tables of the same content (both become pickers, no opt-out), or two chats on the same parent (one click switches both), or a "recent activity" table the author wanted read-only (silently becomes a picker). The DSL has zero syntax for this coupling — it lives entirely in JS, invisible at the source level. **JL-locked direction (2026-05-05):** v0.10 reframes the table as a generic *selector* primitive (or selector-capable property of `data_table`), then adds explicit chat→selector binding via a new grammar clause. Specifics TBD next revision. Until v0.10 lands, document the implicit-discovery rule in the chat presentation contract spec so authors aren't surprised. **Estimated effort**: ~4–6 hrs design + grammar; ~3–4 hrs implementation across compiler+server+conformance. |

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
| Audit log redaction for agent traces | D-18 | Auto-redact field values from agent execution traces when content has confidentiality scopes. Agent reads sensitive field → trace shows [REDACTED]. |
| Subject access / GDPR data export | — | Per-identity query: "what records exist about me and who accessed them." Row-level identity filtering. |

---

## Technical Debt

Items left intentionally on the floor by recent versions. Compiled from
the changelog and the session journal — distinct from feature backlog
items because they're known-deferred *transitional* code, fallback paths,
or test/conformance gaps that exist because earlier work shipped on a
deliberate cut. Keep this section honest: when an item is paid down,
move the entry to the Completed Work Log with the commit that retired it.

### Provider model — v0.9 Phase 3 transitional code

| Item | Source | Notes |
|------|--------|-------|
| **`.legacy` accessor on Anthropic compute providers** | CHANGELOG v0.9 slice (b); journal 2026-04-26 evening §What I want the next Claude to know #2 | `AnthropicLlmProvider` and `AnthropicAgentProvider` expose a `.legacy` property that returns an embedded `AIProvider` for SDK calls; `compute_runner` calls through `provider.legacy.complete()` / `.agent_loop()`. The proper port — moving prompt building, tool-schema construction, and SDK calls *into* the contract methods (`complete`, `invoke`, `invoke_streaming`) — was deferred to keep slice (b)'s diff small. When this lands, `ai_provider.py` gets deleted and the `is_configured`/`service`/`model` passthrough properties on the new providers go away. |
| **Halt-on-refuse loop semantics** | CHANGELOG v0.9 slice (e) §What slice (e) deliberately defers; journal 2026-04-26 evening §3 | The legacy `AIProvider.agent_loop` keeps calling tools after `system_refuse` is invoked (until `set_output` or `max_turns`). Post-loop refusal-state check overrides the outcome correctly, so audit + sidecar + event are correct end-to-end — but the agent does more work than a clean refusal would imply. True halt-on-refuse needs the SDK port above. |
| **`Acts as service` runtime auth** | CHANGELOG v0.9 slice (e); journal 2026-04-26 evening §4 | Source-side declaration lands in v0.9; the runtime path that constructs the agent's service principal from `role_mappings` is a v1.0 item. Apps declaring `Acts as service` compile and lower with `identity_mode=service` on `ComputeSpec` but authorize through the delegate-mode codepath at runtime until v1.0. |
| **`AgentEvent` runtime translation absent** | CHANGELOG v0.9 slices (a)/(e); journal 2026-04-26 evening §5 | The dataclass union (`TokenEmitted | ToolCalled | ToolResult | Completed | Failed`) is declared on the contract module since slice (a) but unused. Existing `compute.stream.<inv_id>.*` event-bus translation keeps publishing the v0.8 dict shape. Typed events get used when the SDK port retires `.legacy`. |
| **`ChannelDispatcher` back-compat read** | CHANGELOG v0.9 slice (b) §Deploy config | Reads from `bindings.channels` first, falls back to top-level `channels` for one phase as a quiet back-compat for unmigrated test fixtures. Drop the fallback once all fixtures are on the v0.9 shape. |
| ~~**`duration_ms=` kwarg back-compat in `write_audit_trace`**~~ | CHANGELOG v0.9 slice (d) | RESOLVED 2026-04-29 evening. All internal call sites (`compute_runner.py:1090, :1159` + 2 test files) migrated to `latency_ms=`; back-compat kwarg + the "legacy kwarg still works" test removed. Column name in audit Content schema unchanged. |

### Forward-looking but unwired

| Item | Source | Notes |
|------|--------|-------|
| **CEL → Predicate AST compiler has no runtime call-site** | CHANGELOG v0.9 Phase 2.x (f); journal 2026-04-26 late §What I want the next Claude to know #7 | `termin_runtime/cel_predicate.py` shipped in 2.x (f) but no .termin construct produces CEL filter expressions on stored rows yet — URL filter params still build `Eq` predicates directly. Future work (filter-by-CEL UIs, agent-tool query strings, view definitions) plumbs this in. Forward-looking on purpose; flagged here so it isn't forgotten. |
| **Tailwind-default CSR mode** | `docs/presentation-provider-design.md` §3.2, Q7 resolution (2026-04-27); BRD #2 §9.1 | BRD #2 §9.1 specifies `tailwind-default` ships both SSR and CSR render modes. Phase 5a deliberately defers CSR — the existing pipeline is SSR-only via Jinja2 + client-side hydration, and Carbon (Phase 5b) brings the CSR machinery (`Termin.registerRenderer(...)` API in termin.js). Once that machinery lands, Tailwind-default's CSR mode is a straightforward port of the existing renderers to client-side render functions. No forcing function; pure feature parity with the BRD. |

### Parser / grammar fidelity

| Item | Source | Notes |
|------|--------|-------|
| **TatSu PEG context state leak (platform-dependent)** | MEMORY.md standing context; CLAUDE.md sub-agent rule #9 | First parse succeeds, subsequent calls return `None` on WSL/Linux. Python fallback paths in `_parse_line` are NOT removable until the upstream TatSu issue is resolved or replaced. Fidelity tests (`test_compiler_fidelity.py`) are the safety net for fallback correctness. Reporter (compiler#1) observed 857 fallbacks on Python 3.10; can't reproduce on 3.11. |
| **Fail-loud parser fallbacks (14 paths)** | v0.7.0 backlog (deferred to v0.8) | 14 fallback paths in the parser silently return defaults. The grammar gaps that originally drove them are closed, but the fallback code still exists. Replace silent-default with raise-on-unhandled. |
| **Round-trip DSL → IR fidelity sweep** | v0.7.0 backlog (deferred to v0.8) | Parse → compile → verify every declared feature appears in IR. Originally cut from v0.7. The `test_compiler_fidelity.py` suite covers per-example IR property assertions; round-trip "every feature present" is the missing complement. |

### Test infrastructure / build

| Item | Source | Notes |
|------|--------|-------|
| **uvicorn `ws="websockets-legacy"` deprecation warnings** | v0.8.2 backlog | ~3 deprecation warnings per WS test from uvicorn's websockets-legacy path. `ws="websockets-sansio"` was evaluated during v0.8 but caused full-suite WS hangs (cumulative event-loop state intolerance). Revisit once uvicorn upgrades sansio reliability. Not blocking — just noise. |
| **Runtime module coverage gaps** | v0.7.0 backlog (deferred) | ai_provider 69%, channel_ws 32%, channels 64%, confidentiality 64%, transitions 67%, websocket_mgr 73%. Overall runtime: 82%. Push the laggards toward the 95% target for new code. Some of these modules are slated for v0.9 Phase 3/4 rewrite, so wait-and-see may be the right call for ai_provider in particular. |
| **GitHub Actions Node 20 → Node 24 migration** | spectrum-provider CI run 25227040865 (2026-05-01); same applies to every public Termin repo workflow | `actions/checkout@v4` and `actions/setup-python@v5` run on Node 20, which GitHub deprecated 2025-09-19. Soft-deprecation annotation appears on every CI run. Hard cutoffs: **June 2 2026 GitHub forces Node 24 by default**; **September 16 2026 Node 20 is removed from runners entirely.** Suggested fix window: **v0.10**. Pin all Termin-family workflows to action versions that support Node 24 (`actions/checkout@v5+`, `actions/setup-python@v6+` or whatever's current at v0.10 prep) in one coordinated pass — termin-core, termin-server, termin-compiler, termin-conformance, termin-spectrum-provider all have the same pattern. Cheap if done all at once, embarrassing if any single repo's CI starts failing the day Node 20 is removed. Pure CI change; no runtime impact, no version bump required if pushed standalone. |

### Conformance suite gaps

| Item | Source | Notes |
|------|--------|-------|
| **Phase 3 conformance pack** | journal 2026-04-26 evening §What's left for v0.9 #4; design doc §4.10 | ~60 tests in a new `test_v09_compute_provider.py` planned per the compute-provider design doc. Per resolved Q8, lands as one commit after all slices merge. Hasn't shipped — compiler-side coverage is in `test_v09_compute_*.py` files in the compiler repo. |
| **Phase 2.x conformance pack** | journal 2026-04-26 late §What's left for v0.9 #4 | The 2.x compiler-side test packs (cascade, classifier, idempotency, update_if, keyset cursors, CEL → Predicate, db-path) all live in the compiler repo. The conformance suite has the cascade-grammar pack and the cross-version migration pack (landed by parallel agent on 2026-04-26 night, ~73 tests) but not the full 2.x set across providers. |
| **`one_of_values` runtime enforcement** | v0.7 backlog (deferred to v0.8 if needed) | POST invalid enum → 422. Runtime has it; conformance suite doesn't test it. |
| **`dependent_values` runtime enforcement** | v0.7 backlog (deferred to v0.8 if needed) | `when` clause CEL eval on create/update → 422. Runtime has it; conformance suite doesn't. |
| **Structured 422 error format** | v0.7 backlog (deferred to v0.8 if needed) | Standardize `{"detail", "field", "constraint", "allowed"}`. |
| **Conformance button assertion coupling** | v0.7 backlog (deferred to v0.8 if needed) | Tests assert rendering strategy not behavioral contract. Decouple — test for `data-termin-*` markers (canonical affordance) rather than literal label text. The conformance#2 fix already softened one assertion (accept marker, label, OR aria-label); same pattern needs to apply to the other action-button tests. |

### Operational / runtime gaps

| Item | Source | Notes |
|------|--------|-------|
| **ALTER TABLE ADD COLUMN with NOT NULL + default** | journal 2026-04-26 night §Spec-first paid off #3 | Runtime gap surfaced by the migration conformance pack: the SQL builder doesn't thread `default_expr` for ADD COLUMN when the column is NOT NULL with a default. Documented in the migration conformance test, deferred. Spec stays correct; runtime catches up. |
| **Backup retention auto-clean** | CHANGELOG v0.9 Phase 2.x (b) §Backup retention | When a high-risk migration commits, the runtime emits a startup-log line naming the backup identifier; cleanup is the operator's responsibility. Different providers have different primitives (filesystem path for SQLite, snapshot ARN for cloud DBs) so v0.9 doesn't auto-clean. Future versions may add a TTL/auto-clean policy per provider. |
| **`examples-dev/agent_chatbot2.termin` not yet promoted** | CHANGELOG v0.8.1; MEMORY.md standing context | The original blocker (multi-content `Accesses` PEG gap) was paid down in `9e3aaeb` (v0.8.2 fix). Example still lives in `examples-dev/`. JL has flagged renaming it before promotion (intent: "agent data streams"); promote + rename in a future pass. |
| ~~**Deploy-template generator emits v0.8-shape channel bindings**~~ | journal 2026-04-29 cleanup-pass | RESOLVED 2026-04-29 evening. The bug was narrower than first described: channels with declared `Provider is "X"` already emitted v0.9 shape; only the "no `provider_contract` declared" fallback path emitted a flat URL/protocol/auth blob without the `provider`/`config` envelope. Fix in `termin/cli.py::_generate_deploy_template` wraps the fallback in `{provider: "stub", config: {...}}`. Two regression tests added in `tests/test_cli.py` (no-contract fallback shape + end-to-end strict-validator round-trip). `util/release.py` is now safe to regenerate fixtures. |

### Refactor-deferred

| Item | Source | Notes |
|------|--------|-------|
| **`termin/analyzer.py` (780 lines)** | v0.7 backlog (deferred to v0.8 if needed) | Single class, flat methods, acceptable at current size — but trending toward "split this." Re-evaluate when it crosses 1000 lines or grows a second responsibility. |
| **`termin_runtime/presentation.py` (599 lines)** | v0.7 backlog (deferred to v0.8 if needed) | Renderers + templates, acceptable at current size. Same trigger as analyzer.py. |

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
| 2026-04-10 | v0.5.0 release: Channels, AI Agents, Mark...as, WebSocket behavioral tests | 752a5a5 |
| 2026-04-10 | WebSocket sync bug fix (5 stacked bugs), retrospective | 365e025..d827c01 |
| 2026-04-10 | Three test levels: async WS integration, Playwright browser, behavioral conformance | various |
| 2026-04-10 | Design decisions D-03, D-04, D-17, D-18, D-19 decided | various |
| 2026-04-11 | v0.6 Phase 1: G1, G5, D-18, structured errors (534 tests) | da555c8..2a259c0 |
| 2026-04-11 | v0.6 Phase 2: G2 (snapshots), D-19 (dependent values), Block C (boundaries) | 267bf4e..cb72088 |
| 2026-04-11 | Fix boundary enforcement: app is always a boundary, no backward-compat exceptions | — |
| 2026-04-11 | Analyzer: TERMIN-S030 duplicate content across boundaries | — |
| 2026-04-11 | Remove legacy pyjexl backend (~2,100 lines) + PageSpec shim | — |
| 2026-04-11 | Fix stale xfail tests: compute endpoints + channel endpoints already implemented | — |
| 2026-04-11 | Code coverage: pytest-cov config, 69% floor, 77% baseline | — |
| 2026-04-11 | 542 tests, 0 failures, 0 skips, 0 xfails | — |
| 2026-04-15 | v0.7: D-11 auto-CRUD, D-20 agent observability, D-09 chat component | various |
| 2026-04-15 | v0.7: Compound verb fix (007), toast/banner (006), version bump to 0.7.0 | various |
| 2026-04-15 | v0.7: 356 compiler fidelity tests, PEG grammar gaps closed, offset cleanup | various |
| 2026-04-15 | v0.7: Runtime coverage push (77% → 82%), 1399 total tests | various |
| 2026-04-16 | Refactor app.py: 2105 → 385 lines (8 modules) | f2e7faa |
| 2026-04-16 | Refactor peg_parser.py: 1345 → 273 lines (4 modules) | 9420f0d |
| 2026-04-16 | Refactor lower.py: 1096 → 745 lines (page extraction) | 2cb5a32 |
| 2026-04-16 | Refactor channels.py: 826 → 415 lines (config + WS extraction) | 7ba6857 |
