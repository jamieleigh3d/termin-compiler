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

| Item | Source | Notes |
|------|--------|-------|
| G8: Agent observability (trace logging) | D-07, D-20 | AUDIT verb, auto-generated `compute_audit_log_{name}` Content per Compute, one trace record per invocation, redaction is runtime concern (conformance: exact-match 4+ chars, structural elements exempt). Design complete in D-20. |
| Chat presentation component | D-09 | New `chat` IR component type. Not AI-specific — any Content with role+content fields. Integrated input with file attach, streaming via WebSocket. Design complete in D-09. |
| Auto-generated REST API (convention over configuration) | D-11 | Content types get CRUD routes automatically |
| Examples audit: remove boilerplate, use latest syntax | — | All examples updated to use v0.6 features |
| **Code coverage: ai_provider.py** | — | Currently 37%. Needs mock LLM tests for agent loop, tool building |
| **Code coverage: channels.py** | — | Currently 54%. WebSocket reconnect, metrics, error paths |
| **Code coverage: transaction.py** | — | Currently 49%. Staging, commit/rollback semantics |
| **Code coverage: expression.py** | — | Currently 68%. More CEL edge cases |
| **Code coverage: reflection.py** | — | Currently 40%. Reflection engine query paths |
| **Code coverage: errors.py (runtime)** | — | Currently 37%. Error router paths |
| **Compiler fidelity tests** | Issue 007 post-mortem | For every example, compile and assert specific IR properties (verbs, transitions, field types) — not just `errors.ok`. The examples are the spec; tests must verify the spec compiled faithfully. |
| **Fail-loud parser fallbacks** | Issue 007 post-mortem | 14 fallback paths in `peg_parser.py` silently return defaults (`name="unknown"`, `verbs=["view"]`, `channel=""`) instead of erroring. Replace silent defaults with explicit errors. |
| **Close PEG grammar gaps (eliminate fallbacks)** | Issue 007 post-mortem | 39/466 lines (8.4%) fall through to regex fallback: multi-word states (14), "can execute this" misclassified as access_line (12), field modifiers (7), state_also commas (4). Fix the grammar for each case, then replace fallback with `raise ParseError`. Phased: fix grammar → add tests → remove fallback → verify. |
| **Round-trip DSL→IR fidelity** | Issue 007 post-mortem | Parse DSL → compile to IR → verify every declared feature appears correctly in IR. Catches silent semantic data loss during compilation. |
| **Replace hard-coded string offsets** | Issue 007 post-mortem | 25+ locations use brittle `text[19:]` magic numbers. Replace with `len("prefix")` constants. One was already a historical bug site. |
| Flash/toast notification primitive | Thread 006 | Toast (auto-dismiss) and banner (persistent) feedback for state transitions and actions. CEL interpolation with `{singular}.*`, `from_state`, `to_state`, `User.*` context. |
| Fix conformance button assertion coupling | Thread 008 | `test_active_product_disables_activate` tests rendering strategy (disabled attr) not behavioral contract (no transition URL). Decouple. |

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
