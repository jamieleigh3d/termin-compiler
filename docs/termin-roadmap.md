# Termin Product Roadmap & Backlog

**Last updated:** April 2026
**Maintained by:** Jamie-Leigh Blake & Claude

---

## Vision

Termin is a governed application substrate where business software is structurally safe by construction. The roadmap builds from a working compiler and runtime toward an ecosystem where applications are authored by humans or AI, deployed to any environment, and guaranteed to enforce security properties without additional engineering effort.

---

## Phases

### Phase 0: Proof of Architecture (Current — Q2 2026)

**Status:** In progress. Core pipeline working. Real-time subscriptions working. Cleaning up legacy code.

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
| **System-defined JEXL functions** | **PLANNED** | termin-appserver-and-ecosystem-v2.md | sum(), count(), now(), identity.has_scope() |
| `boundary_type` in registry response | PLANNED | termin-appserver-and-ecosystem-v2.md | Forward compat: application/library/module/configuration |
| `client_safe` flag on ComputeSpec | PLANNED | termin-distributed-runtime-model.md | Compiler infers from shape + field access |
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
| Compile-time JEXL field dependency analysis | PLANNED | termin-confidentiality-spec.md | Scope requirement inference and validation |
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
| `UI-testing.md` | Manual and automated UI testing guide | Current |

---

## Immediate Priority Queue (Next 4-6 Weeks)

This is the ordered implementation backlog for completing Phase 0:

| # | Item | Effort | Depends On |
|---|------|--------|------------|
| 1 | Add `confidentiality_scope` to FieldSpec + ContentSchema in IR | Small | — |
| 2 | Add `confidentiality is` to PEG grammar + parser + lowering | Medium | #1 |
| 3 | Add `Identity: service/delegate` to Compute DSL + IR | Small | — |
| 4 | Add `Output confidentiality:` to Compute DSL + IR | Small | #3 |
| 5 | Implement `redact_record()` in runtime storage/app | Medium | #1 |
| 6 | Implement Compute invocation gate at Channel boundary | Medium | #3, #5 |
| 7 | Implement JEXL field dependency static analysis in compiler | Large | #2 |
| 8 | Implement taint propagation + reclassification in lowering | Medium | #7 |
| 9 | Add `DeclassificationPoint` to IR + Reflection | Small | #8 |
| 10 | Implement runtime JEXL redaction guard | Medium | #5 |
| 11 | System-defined JEXL functions (sum, count, now, etc.) | Medium | — |
| 12 | State transition scope-gating | Medium | — |
| 13 | Boundary isolation enforcement | Large | — |
| 14 | Conformance test suite (20+ tests) | Medium | #5, #6, #12, #13 |
| 15 | Add example with confidentiality (HR app or medical records) | Small | #2, #5 |

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
