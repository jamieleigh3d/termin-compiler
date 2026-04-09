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

Restructured April 9, 2026. Blocks A, B, D are complete. Channel runtime and AI agent support are the critical path to v0.5.0 for an AWS-native Termin runtime. Everything else is lower priority.

### Priority 1: Block F — Channel Runtime (v0.5.0 blocker)

**Why first:** Channels are declared in the compiler and IR, but the reference runtime doesn't implement them. an AWS-native Termin runtime needs working channels for external integrations. We need to validate the Channel model in the reference runtime before shipping v0.5.0 to an AWS-native Termin runtime.

| # | Item | Effort | Subsystems | Notes |
|---|------|--------|------------|-------|
| F1 | Deploy config loader: read `termin.deploy.json`, resolve `${ENV_VAR}` | Small | runtime (new module) | New file: `termin_runtime/channels.py`. Loads config at startup, maps channel names → URL + auth + protocol. |
| F2 | Outbound channel dispatcher (HTTP): `channel_send()` for reliable delivery | Medium | runtime (channels) | HTTP client (httpx). POST to configured URL. Retry with exponential backoff. Scope check against ChannelRequirementSpec. |
| F3 | Outbound channel dispatcher (WebSocket): persistent connections for realtime | Medium | runtime (channels) | WebSocket client manager. Auto-reconnect, heartbeat. Scope check on send. |
| F4 | Event action handler: wire `send_channel` in `run_event_handlers()` | Small | runtime (app, channels) | Currently dead code. When EventActionSpec has `send_channel`, call the channel dispatcher. |
| F5 | Action invocation endpoint: `POST /api/v1/channels/{name}/actions/{action}` | Medium | runtime (app, channels) | Validate params against ChannelActionSpec.takes, check scopes, forward to external service, validate response against returns. |
| F6 | Inbound webhook handler: register endpoints from IR, validate payloads | Medium | runtime (app, channels) | For inbound reliable channels: register route at channel endpoint (or `/webhooks/{name}`), validate against carries_content schema, create content record, fire events. |
| F7 | Inbound WebSocket handler: accept persistent connections for realtime channels | Medium | runtime (app, channels) | For inbound realtime channels: dedicated WebSocket endpoint, message validation, content creation, event firing. |
| F8 | Channel reflection: expose connection status, send/receive metrics | Small | runtime (reflection, channels) | Extend existing reflection with live connection state (connected/disconnected/error), message counts, last activity. |
| F9 | Channel demo end-to-end test: channel_demo.termin with mock external services | Medium | tests | TestClient + mock HTTP/WS servers. Verify: outbound send, inbound webhook → content created, action invocation → response, event → channel send. |
| F10 | Deploy config JSON Schema: `termin.deploy.schema.json` | Small | docs | DONE — `docs/termin-deploy-schema.json` |

**Exit criteria:** `channel_demo.termin` works end-to-end. Outbound sends hit configured URLs. Inbound webhooks create content records. Action invocations dispatch and return typed responses. Events trigger channel sends. All behind scope enforcement.

### Priority 2: Block G — AI Agent Runtime

**Why second:** Depends on channels (agents call `channel.invoke()`). an AWS-native Termin runtime is building agent support. We need a working AI agent demo to prove the model before v0.5.0.

| # | Item | Effort | Subsystems | Notes |
|---|------|--------|------------|-------|
| G1 | Wire Compute system type into runtime CEL context | Small | runtime (app, expression) | `Compute.Name`, `Compute.Scopes`, `Compute.Provider`, `Compute.Trigger`, `Compute.StartedAt`. Needed for precondition evaluation. |
| G2 | Full Before/After snapshots backed by transaction staging | Medium | runtime (transaction) | `Before.content_query()` → snapshot at transaction start. `After.content_query()` → staged writes merged with snapshot. Needed for postcondition evaluation. |
| G3 | Agent tool API: ComputeContext with `content.*`, `state.*`, `channel.*`, `reflect.*` | Large | runtime (new module: `agent.py`) | Python class wrapping transaction + channel dispatcher. Methods: `content_query()`, `content_create()`, `content_update()`, `content_delete()`, `state_transition()`, `channel_invoke()`, `channel_send()`, `reflect_app()`. |
| G4 | AI agent provider: Claude API integration | Large | runtime (agent) | Call Claude API with objective + strategy + available tools. Map tool_use responses to ComputeContext method calls. Loop until agent signals done or token/turn limit reached. |
| G5 | Runtime scheduler for `Trigger on schedule` Computes | Medium | runtime (new module: `scheduler.py`) | APScheduler or asyncio-based. Read trigger specs from IR. Execute Compute on schedule via the existing Compute endpoint. |
| G6 | Runtime event trigger for `Trigger on event` Computes | Medium | runtime (app, events) | When event fires matching a Compute's trigger spec, auto-invoke that Compute with the triggering record. |
| G7 | AI agent demo app: `.termin` example with working agent | Medium | examples | Simple enough to demo: e.g., a support bot that triages tickets, or a content moderator. Must exercise: agent Compute, channel.invoke, pre/postconditions, state transitions. |
| G8 | Agent observability: execution log, tool call trace, token usage | Small | runtime (agent, reflection) | Log each tool call with args/result. Expose via reflection. Track token usage per invocation. |

**Exit criteria:** A `.termin` app with `Provider is "ai-agent"` actually calls Claude, executes tool calls through ComputeContext, respects pre/postconditions with rollback, and the whole thing is observable.

### Priority 3: Block E — Remaining Quick Wins & Research

Previously "Block E: Research & Future." Items not yet done, reprioritized.

| # | Item | Effort | Subsystems | Notes | Status |
|---|------|--------|------------|-------|--------|
| E7 | Role reflection in CEL: `reflect.role("engineer").Scopes` | Small | runtime (reflection) | termin-cel-types.md § 7 | |
| E11 | Compiler CEL body analysis (field dependencies, reclassification) | Medium | compiler (analyzer) | termin-confidentiality-runtime-design.md § B2 | |
| E1 | Multi-file apps: research composition of .termin sources | Research | compiler | appserver-v2.md § Library dependencies | |
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
