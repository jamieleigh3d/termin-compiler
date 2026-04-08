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

## Immediate Priority Queue (Next 4-6 Weeks)

Restructured April 2026 to prioritize end-to-end demo completeness. Confidentiality system deferred to a later block after working demos prove the architecture.

### Block A: End-to-End Demo Completeness

| # | Item | Effort | Subsystems | Design Doc |
|---|------|--------|------------|------------|
| A1 | State transition scope-gating in runtime | Medium | runtime | primitives.md § State; implementers-guide.md § 4 State Machines |
| A2 | System-defined CEL functions (sum, count, now, identity.has_scope) | Medium | runtime, compiler | appserver-v2.md § 7 System-Defined Functions |
| A3 | `client_safe` inference logic in compiler | Small | compiler | distributed-runtime.md § 3 Client-Side Compute |
| A4 | Richer example app (helpdesk or HR) demonstrating all primitives | Medium | examples | presentation-ir-v2.md § DSL to IR Examples |
| A5 | Highlight row rendering in presentation | Small | runtime (presentation) | presentation-ir-v2.md § Data Table Sub-Components (highlight) |
| A6 | Related data display in presentation | Small | runtime (presentation, storage) | presentation-ir-v2.md § Data Table Sub-Components (related) |
| A7 | `validate_unique` on form field_input | Small | runtime (app, storage) | presentation-ir-v2.md § Input Components (field_input) |
| A8 | After-save navigation (`after_save` prop on form) | Small | runtime (presentation) | presentation-ir-v2.md § Input Components (form) |
| A9 | Conformance test suite seed (20+ tests) | Medium | tests | product-strategy.md § Tier 1 Guarantees |

### Block B: Confidentiality System

| # | Item | Effort | Subsystems | Design Doc |
|---|------|--------|------------|------------|
| B1 | Add `confidentiality_scope` to FieldSpec + ContentSchema | Small | IR | confidentiality-spec.md § 2 IR Changes |
| B2 | Add `confidentiality is` to PEG grammar + parser + lowering | Medium | compiler (PEG, parser, lower) | confidentiality-spec.md § 1 DSL Syntax |
| B3 | Add `Identity: service/delegate` to Compute DSL + IR | Small | compiler, IR | confidentiality-spec.md § 1 DSL Syntax (identity_mode) |
| B4 | Add `Output confidentiality:` to Compute DSL + IR | Small | compiler, IR | confidentiality-spec.md § 1 DSL Syntax (output_confidentiality) |
| B5 | Implement `redact_record()` in runtime | Medium | runtime (storage, app) | confidentiality-spec.md § 4 Runtime Changes; BRD § 6 Check 1 |
| B6 | Implement Compute invocation gate at Channel boundary | Medium | runtime (app) | confidentiality-spec.md § 4 Runtime Changes; BRD § 6 Check 2 |
| B7 | Implement CEL field dependency static analysis | Large | compiler (analyzer) | confidentiality-spec.md § 3 Compiler Static Analysis |
| B8 | Implement taint propagation + reclassification in lowering | Medium | compiler (lower) | confidentiality-spec.md § 3 Compiler Static Analysis |
| B9 | Add `ReclassificationPoint` to IR + Reflection | Small | IR, runtime | confidentiality-spec.md § 2 IR Changes |
| B10 | Implement runtime CEL redaction guard | Medium | runtime (expression) | confidentiality-spec.md § 4 Runtime Changes; BRD § 6 Check 3 |
| B11 | Add example with confidentiality (HR app or medical records) | Small | examples | confidentiality-BRD.md § 4 User Stories |

### Block C: Boundary Enforcement

| # | Item | Effort | Subsystems | Design Doc |
|---|------|--------|------------|------------|
| C1 | Boundary isolation enforcement | Large | runtime (app, storage) | appserver-v2.md § 3 Boundaries; primitives.md § Boundary |
| C2 | Cross-boundary identity propagation | Medium | runtime (identity, app) | distributed-runtime.md § Cross-Boundary Identity Propagation |

### Block D: Package Format & Conformance Distribution

| # | Item | Effort | Subsystems | Design Doc |
|---|------|--------|------------|------------|
| D1 | Define `.termin.pkg` format (manifest, ZIP structure, checksums) | Medium | compiler (CLI) | DONE — termin-package-format.md |
| D2 | `termin compile` outputs `.termin.pkg` + `termin serve` | Medium | compiler (CLI, backends) | DONE |
| D3 | Package revision auto-increment (monotonic, compare existing .pkg) | Small | compiler (CLI) | DONE |
| D4 | Conformance suite adapter interface (deploy, authenticate, connect) | Medium | conformance | DONE — github.com/jamieleigh3d/termin-conformance |
| D5 | Decouple conformance suite from reference runtime (standalone) | Medium | conformance, tests | DONE — 189 tests, 0 runtime imports |
| D6 | Ship test `.termin.pkg` files with conformance suite | Small | conformance | DONE — 6 packages |
| D7 | App Id: compiler-managed UUID for deployment identity | Small | compiler, PEG, IR | DONE |
| D8 | Testing methodology document (3 tiers) | Small | conformance | DONE — testing-methodology.md |
| D9 | IR version bump to 0.3.0 | Small | compiler, runtime, schema | DONE |

### Block E: Research & Future

| # | Item | Effort | Subsystems | Design Doc |
|---|------|--------|------------|------------|
| E1 | Multi-file apps: research how multiple .termin source files compose | Research | compiler | appserver-v2.md § Library dependencies |
| E2 | Package signatures (cryptographic signing of .termin.pkg) | Research | compiler, runtime | — |
| E3 | Enum constraint enforcement on API creates | Small | runtime (app) | DONE — 422 with allowed values |
| E4 | Min/max constraint enforcement on API creates | Small | runtime (app) | — (xfail in conformance suite) |
| E5 | Tier 3: Behavioral round-trip tests (form submit → API verify) | Medium | conformance | testing-methodology.md § Tier 3 |
| E6 | Automation API contract for programmatic UI interaction | Research | runtime, conformance | testing-methodology.md § Tier 3 |
| E7 | Role reflection in CEL: `reflect.role("engineer").Scopes` | Small | runtime (reflection) | termin-cel-types.md § 7 |
| E8 | Triple-backtick multi-line expression parser support | Medium | compiler (grammar, parser) | termin-cel-types.md § 1.2 |
| E9 | AI agent support: `[[ ]]` Provider, Preconditions, Postconditions | Large | compiler, runtime | msgs/001-ai-agents.md |
| E10 | Transaction staging (snapshot isolation for Compute execution) | Large | runtime | msgs/001-ai-agents.md |
| E11 | Compiler CEL body analysis (field dependencies, reclassification points) | Medium | compiler (analyzer) | termin-confidentiality-runtime-design.md § B2 |
| E12 | `link_template` on data_table columns | Small | compiler, runtime (presentation) | msgs/002-data-table-links.md |
| E13 | Server-side CEL evaluation for text components | Small | runtime (presentation) | msgs/002-data-table-links.md |

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
