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
| **PEG gap: `Accesses` line with multiple content names** | v0.8 UAT (JL) | `Accesses messages, products` inside a Compute block doesn't parse via TatSu — the `compute_accesses_line` grammar rule doesn't match the comma-separated list shape. The Python fallback in `parse_handlers.py` handles it correctly (access enforcement works), so this is a fidelity issue not a security issue. `tests/test_compiler_fidelity.py::TestZeroPEGFallbacks::test_no_tatsu_fallbacks` catches it. Fix: update the PEG rule so TatSu parses the full shape. Blocking promotion of `examples-dev/agent_chatbot2.termin` back to `examples/`. |
| **Stale `app_seed.json` between test runs** | v0.8 sprint finding | The runtime backend writes `app_seed.json` beside each compiled `app.py` but does not delete a stale `app_seed.json` when recompiling an example with no companion `_seed.json`. Test fixtures work around it by explicitly `SEED_PATH.unlink()`. Fix: have the runtime backend also clean up stale sidecar seed files on compile. Test-fixture workarounds can then drop the manual unlink. |
| **uvicorn `ws="websockets-legacy"` deprecation warnings** | v0.8 sprint | Our tests emit ~3 deprecation warnings per WS test from uvicorn's websockets-legacy path. Evaluated `ws="websockets-sansio"` during v0.8 but it caused full-suite WS hangs (cumulative event-loop state intolerance). Revisit once uvicorn upgrades sansio reliability. Not blocking — just noise. |
| **`input_type="state"` dropdown on create forms** | v0.8 review | The edit-modal state-field dropdown correctly filters to valid transitions + user scopes. The same renderer path for create forms (if a content with a state machine exposes one) could receive the same treatment. Currently out of scope because `Accept input for …` forms don't usually include the state field (initial state is implied). Log for when a customer asks. |
| **Release script test-run hang under `capture_output=True`** | v0.8.1 release prep | `util/release.py` calls `subprocess.run([pytest, ...], capture_output=True, text=True)` which hangs at end-of-test-run on Windows + Miniconda Python 3.11. Pytest completes the test run normally (CPU time matches a clean direct invocation) but the main process doesn't see the subprocess exit promptly and stdout stays buffered. Killing the subprocess mid-hang shows tests passed. Workaround: run the release script with `--skip-tests`, then invoke both test suites directly (which behave normally). Fix candidates: (a) stream subprocess output line-by-line instead of capturing, (b) use `-u` (unbuffered) flag when invoking pytest, (c) use `subprocess.Popen` + explicit stdout.readline() loop. Not blocking releases — the manual rerun pattern works, just noisy. |

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
