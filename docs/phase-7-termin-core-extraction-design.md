# Phase 7 — `termin-core` Extraction: Design Document

**Status:** Q1–Q8 decided 2026-04-30. Slice 7.1 ready to begin.
**Tracker:** GitHub issue [#2](https://github.com/jamieleigh3d/termin-compiler/issues/2).
**Roadmap entry:** `docs/termin-roadmap.md` § Phase 7.
**Authors:** Jamie-Leigh Blake & Claude Anthropic.
**Last touched:** 2026-04-30.

## TL;DR — locked decisions

| ID | Question | Decision |
|---|---|---|
| Q1 | Abstract request shape | **ASGI** — adopt the de-facto Python async substrate |
| Q2 | Builtin providers split | **Pure-Python builtins stay in core** (CEL compute, stub identity, Tailwind synthesis rule); IO-bound builtins (SQLite, Anthropic, Jinja2 SSR, channel impls) move to `termin-server` |
| Q3 | WebSocket dispatcher | **Full extraction** — define `TerminWebSocket` Protocol, move both topic dispatch and connection management; FastAPI adapter wraps `fastapi.WebSocket` |
| Q4 | Compiler imports IR from core | **Yes** — IR dataclasses move to `termin-core/ir/`; compiler `lower()` builds core types |
| Q5 | Repo layout | **Four sibling repos** — `termin-core`, `termin-server`, `termin-compiler`, `termin-conformance`. Existing repo URLs preserved |
| Q6 | Slicing | **Incremental 7.1 → 7.5** — pure-types extract first, dispatch second, server split third, IR-types unify fourth, conformance pack + cleanup fifth |
| Q7 | Core conformance pack | **New pack lands in slice 7.5** inside the existing `termin-conformance` repo |
| Q8 | Reference-runtime package name | **`termin-server`** (Python package: `termin_server`); `termin_runtime` aliased then dropped in 7.5 |

### Decision rationale notes for the audit trail

- **Q5 (repo layout):** debated explicitly. The hybrid monorepo (core+server+compiler in one tree, conformance separate) was on the table because most Phase 7 slices touch core+server together. Rejected because (a) JL doesn't want to move repositories around again — `termin-compiler` URL is referenced in BRDs, on termin.dev, in CHANGELOG history; (b) the existing multi-repo plumbing (`util/release.py`) already coordinates compiler↔conformance and extending it to four repos is a smaller jump than restructuring; (c) sibling-repo boundaries match the existing project organization (`termin-spectrum-provider` is also a sibling repo). The cost we accept: cross-cutting Phase 7 refactors need PR coordination across repos. Mitigated by slicing strategy (Q6) — each slice is sized so the bulk of its diff lives in one repo.

## 1. Scope

Phase 7 extracts the load-bearing library surface from
`termin_runtime/` into a separate, framework-free Python package
`termin-core`. Any conforming runtime — an AWS-native runtime, a Rust
port, a managed-cloud runtime — depends only on `termin-core` and
supplies its own framework adapter.

The original Issue #2 framing was "extract pure types and Protocols."
JL's 2026-04-30 framing extends that: **the routing dispatch
architecture (REST + WebSocket) also belongs in `termin-core`, with
adapters for the actual framework**. That extension makes
`termin-core` an SDK rather than a header-file pile, and is what
delivers Tenet 4 ("Providers over primitives") in practice — the
provider system stops being something every runtime re-implements
and starts being something they import.

This doc:

- Captures the constraints (§2)
- Proposes the package boundary (§3)
- Identifies the architectural decisions JL needs to make (§4, Q1–Q8)
- Outlines the slicing strategy contingent on those decisions (§5)
- Lists what's explicitly out of scope (§6)

## 2. Constraints

1. **An alternate Termin runtime (e.g. an AWS-native Python runtime, or
   any third-party Python implementation) imports `termin-core` and gets
   the provider plugin architecture, the WebSocket routing/dispatch
   architecture, and the REST API routing/dispatch architecture for
   free.** It supplies adapters for its actual WebSocket implementation
   and HTTP framework. (JL, 2026-04-30.)
2. **The reference runtime's FastAPI / uvicorn / aiosqlite / Anthropic
   layer moves to a separate package.** `termin-core` carries
   zero dependency on FastAPI. (Implied by #1, made explicit in JL's
   2026-04-30 framing.)
3. **The compiler (`termin/`) and the conformance suite both depend
   on `termin-core` for IR types and contract Protocols.** Single
   source of truth. (Issue #2.)
4. **Phase 5 + Phase 6 must be landed and the conformance pack
   passing before extraction starts.** Otherwise immature contract
   surfaces get frozen too early. (Roadmap.) **This precondition is
   met as of 2026-04-29 evening.**
5. **No backwards-compatibility shims.** Termin is pre-v1.0; the
   v0.9 → v1.0 path is a hard cut. (Workspace CLAUDE.md.)
6. **JL's existing apps run on the reference runtime today.** The
   extraction must not regress their behavior; the conformance suite
   running against the post-extraction reference runtime must stay
   green.

## 3. Proposed package layout

```
termin-core/                                 # NEW repo
  termin_core/
    __init__.py                              # Re-exports curated public API
    ir/
      types.py                               # AppSpec, ContentSchema, FieldSpec,
                                             #   PageEntry, ComponentNode, …
                                             #   (read-only — lowering stays in
                                             #   termin-compiler)
      schema.py                              # JSON Schema loader / validator
      serialize.py                           # Canonical IR JSON encode / decode
    providers/
      identity_contract.py                   # Protocol surfaces moved as-is
      storage_contract.py                    #   from termin_runtime/providers/
      compute_contract.py
      channel_contract.py
      presentation_contract.py
      contracts.py                           # Category, Tier, ContractDefinition
      registry.py                            # ContractRegistry, register_*
      binding.py                             # Binding resolution
      deploy_config.py                       # Deploy-config parser + validator
    routing/                                 # NEW: framework-agnostic dispatch
      request.py                             # TerminRequest / TerminResponse
                                             #   value types (or ASGI scope wrap;
                                             #   see Q1)
      websocket.py                           # TerminWebSocket Protocol
      crud.py                                # ListRoute / GetRoute / CreateRoute /
                                             #   UpdateRoute / DeleteRoute /
                                             #   InlineEdit / Transition pure
                                             #   coroutines: take TerminRequest +
                                             #   ctx, return TerminResponse
      channel_dispatch.py                    # The compute.stream.<id>.<field>
                                             #   topic router (was channel_ws.py)
      route_specs.py                         # IR → declarative route table that
                                             #   adapters bind onto their framework
    expression/                              # CEL evaluator + Predicate AST
    confidentiality/                         # Redacted, redact_records
    identity/                                # Principal, PrincipalContext value types
    errors/                                  # TerminAtor + error envelope shapes
    events/                                  # EventBus interface (Protocol only —
                                             #   concrete in-process impl moves to
                                             #   termin-server)
    validation/                              # D-19 dependent_values, one_of_values
    state/                                   # State machine evaluator (pure)
    builtins/                                # Pure-Python builtins that need no IO
      compute_cel.py                         # CEL compute provider
      identity_stub.py                       # Stub identity (dict lookup)
      presentation_default_synthesis.py      # Default-Tailwind-binding synthesis
                                             #   logic (stays here per design doc
                                             #   §3.2 — the rule is pure)

termin-server/                               # NEW repo (was termin_runtime/)
  termin_server/
    app.py                                   # FastAPI app factory
    fastapi_adapter.py                       # Wraps Request/Response/WebSocket
                                             #   <-> termin_core.routing types
    websocket_manager.py                     # FastAPI WebSocket connection mgr
    bootstrap.py                             # IR → bound providers + route table
    cli.py                                   # `termin serve <pkg>`
    builtins/
      storage_sqlite.py                      # aiosqlite implementation
      compute_anthropic.py                   # LLM + agent providers
      channel_*.py                           # Webhook/email/messaging/event-stream
      presentation_tailwind_ssr.py           # SSR Jinja2 renderer
    static/                                  # bootstrap.js, termin.js, etc.
    templates/                               # Jinja2 templates
    migrations/                              # Migration apply machinery
                                             #   (provider-specific SQL builders)

termin-compiler/                             # Existing repo, slimmed
  termin/
    parser.py, analyzer.py, lower.py         # Stays
    cli.py                                   # `termin compile`
    backends/                                # Stays
    # ir.py, ir_serialize.py — types move into termin-core; lower.py
    # depends on termin-core for AppSpec/ComponentNode
    
termin-conformance/                          # Existing repo, slimmed
  tests/                                     # Stays
  adapter*.py                                # Stays
  # depends on termin-core for contract shapes; depends on termin-server
  # for the reference adapter
```

### What moves vs. what stays — quick inventory

| Module today | Destination |
|---|---|
| `termin_runtime/providers/{identity,storage,compute,channel,presentation}_contract.py` | `termin-core/termin_core/providers/` |
| `termin_runtime/providers/{contracts,registry,binding,deploy_config}.py` | `termin-core/termin_core/providers/` |
| `termin_runtime/cel_predicate.py`, `expression.py` | `termin-core/termin_core/expression/` |
| `termin_runtime/confidentiality.py` | `termin-core/termin_core/confidentiality/` |
| `termin_runtime/identity.py` (the value types only — *not* the FastAPI cookie reader) | `termin-core/termin_core/identity/` |
| `termin_runtime/errors.py` (TerminAtor) | `termin-core/termin_core/errors/` |
| `termin_runtime/state.py`, `transitions.py` (pure rules) | `termin-core/termin_core/state/` |
| `termin_runtime/validation.py` | `termin-core/termin_core/validation/` |
| `termin/ir.py`, `termin/ir_serialize.py` | `termin-core/termin_core/ir/` |
| `termin_runtime/routes.py` (1063 lines, **87** FastAPI references) | **split:** dispatch logic → `termin-core/termin_core/routing/`; FastAPI binding → `termin-server/termin_server/fastapi_adapter.py` |
| `termin_runtime/channel_ws.py`, `websocket_manager.py` | **split:** topic dispatch → `termin-core`; FastAPI WebSocket connection plumbing → `termin-server` |
| `termin_runtime/storage.py`, `providers/builtins/storage_sqlite.py` | `termin-server/termin_server/builtins/` |
| `termin_runtime/ai_provider.py`, `providers/builtins/compute_*anthropic.py` | `termin-server/termin_server/builtins/` |
| `termin_runtime/presentation.py` (1080 lines — Jinja2 renderer) | `termin-server/termin_server/builtins/presentation_tailwind_ssr.py` |
| `termin_runtime/app.py` (FastAPI app factory) | `termin-server/termin_server/app.py` |
| `termin_runtime/bootstrap.py` (11 FastAPI references) | **split:** IR-walking + provider-binding → `termin-core`; FastAPI route registration → `termin-server` |
| `termin_runtime/scheduler.py`, `events.py`, `boundaries.py`, `preferences.py` | `termin-core/` (pure rules) — verify zero FastAPI/IO coupling first |
| `termin/parser.py`, `analyzer.py`, `lower.py`, `backends/` | Stays in `termin-compiler` |

### Where each consumer ends up

| Consumer | Imports `termin-core` | Imports `termin-server` |
|---|---|---|
| Reference runtime (today's `termin_runtime/`) | yes | this *is* it |
| Compiler (`termin/`) | yes | no |
| Conformance suite | yes | yes (for reference adapter only) |
| Alternate Python runtime (planned, e.g. AWS-native) | **yes** | **no** |
| Spectrum provider package | yes | no |
| Third-party Rust runtime (future) | via JSON Schema parallel surface (Q1, Q3) | no |

## 4. Open architectural decisions (briefing format)

These are the calls that need JL's eyes before slicing starts. Each
question is independent of the others — the answers can come in any
order.

### Q1 — Abstract request/response shape: invent or adopt ASGI?

The dispatch logic in `routes.py` today reads from FastAPI's `Request`
type (path params, query params, body, headers, cookies). For the
core to be framework-free, we need an abstract request type.

- **Option a:** Invent `termin_core.routing.TerminRequest` /
  `TerminResponse` as plain dataclasses. Adapters wrap their
  framework's request into our type and unwrap our response on the
  way out. Pure, no transitive dependency.
- **Option b:** Adopt the ASGI scope/receive/send protocol directly
  as the substrate. ASGI is the de-facto Python async web standard
  (Starlette, FastAPI, Quart, Hypercorn, Uvicorn all build on it).
  No package dependency — it's a calling convention, not a library.
  Adapters that already speak ASGI (most of them) need almost no
  glue.

**Recommendation:** Option b. The ASGI surface is broad enough to
cover everything we need, it's the substrate the Python async-web
ecosystem already standardizes on, and "Termin runs on top of any
ASGI host" is a stronger contract than "Termin defines its own
request type." Option a is purer in theory but creates wrap/unwrap
overhead for every adapter that already speaks ASGI (which is most
of them).

**Risk under option b:** if Termin ever wants a transport that isn't
ASGI-shaped (e.g., a Lambda direct integration), we still have to
write an ASGI shim. That's a known cost; cloud Lambda hosts already
ship ASGI adapters (Mangum etc.).

---

### Q2 — Where do builtin providers live?

The current `termin_runtime/providers/builtins/` directory ships
~10 concrete providers (stub identity, SQLite storage, CEL compute,
LLM+agent Anthropic, four channel stubs, Tailwind-default
presentation).

- **Option a:** All builtins move to `termin-server`. `termin-core`
  ships zero concrete providers. An alternate runtime registers its
  own from scratch (or imports `termin-server`'s SQLite provider
  opportunistically if it wants to).
- **Option b:** Pure-Python builtins (CEL compute, stub identity,
  default-Tailwind-binding *synthesis*) stay in `termin-core`.
  IO-bound builtins (SQLite, Anthropic, Tailwind SSR rendering,
  channel webhooks) move to `termin-server`. The split is "needs
  network/FS/process" vs "doesn't."

**Recommendation:** Option b. CEL compute and stub identity are
load-bearing for *any* runtime — every conformance test that uses an
agent compute, every test that uses the stub identity, every test
that uses default presentation synthesis depends on these. Forcing
every alternate runtime to vendor its own CEL evaluator and stub
identity reintroduces exactly the duplication Phase 7 is trying to
remove. The IO-bound
builtins genuinely need the framework adapter and belong outside.

**Edge case:** Tailwind-default presentation synthesis is split.
The *rule* (when a presentation binding is missing, synthesize a
default-Tailwind binding) is pure — it goes in core. The *renderer*
(Jinja2 templates that turn `ComponentNode` trees into HTML) is
IO-shaped (template files on disk, Jinja2 dependency) and goes in
`termin-server`.

---

### Q3 — WebSocket dispatcher: full extraction or partial?

The WebSocket layer is 246 lines spread across `websocket_manager.py`,
`channel_ws.py`, and parts of `channels.py`. It does two distinct
jobs:

1. **Connection management:** open/close, heartbeat, per-connection
   subscription state. Tightly coupled to FastAPI's `WebSocket` type.
2. **Topic dispatch:** the `compute.stream.<inv_id>.<field>` topic
   model, the `content.<source>` channel model, fan-out to subscribed
   connections. Pure routing logic.

- **Option a:** Full extraction. Define `TerminWebSocket` Protocol
  (`accept`, `send_json`, `receive_json`, `close`) in core. Move both
  jobs. FastAPI adapter implements `TerminWebSocket` by wrapping
  `fastapi.WebSocket`.
- **Option b:** Move only the topic-dispatch job to core. Connection
  management stays in `termin-server` for now. Alternate runtimes
  reinvent their own connection manager but reuse the dispatcher.

**Recommendation:** Option a. JL specifically called out
"WebSocket routing dispatch architecture, that has adapters for the
actual implementation of WebSockets" as a Phase 7 goal in the
2026-04-30 framing. Option b ships sooner but every alternate runtime
has to redo the hardest part of WebSocket lifecycle correctness — the
part that
already had two latent bugs the Spectrum chat slice surfaced. The
proper extraction encodes that hard-won correctness once in core.

---

### Q4 — Compiler dependency on core?

The compiler today defines IR types in `termin/ir.py` (AppSpec,
ContentSchema, FieldSpec, PageEntry, ComponentNode, etc.). The
runtime re-loads the IR from JSON and re-builds equivalent dicts.

- **Option a:** Move IR types to `termin-core`. Compiler imports
  from core. `lower()` stays in compiler but builds `termin-core`
  IR objects. Single source of truth.
- **Option b:** Keep IR in compiler. Add a thin "read-only IR view"
  in core. Two parallel definitions, kept in sync by tests.

**Recommendation:** Option a. Issue #2 explicitly anticipates this.
The cost is mechanical (move dataclasses, fix imports). The benefit
is structural — the schema becomes the contract, and the conformance
test pack can validate against it without indirection.

**Edge case:** IR types should be **read-only / frozen dataclasses**
in core. The `lower()` pass that builds them stays in
`termin-compiler` and is the only writer.

---

### Q5 — Repo layout: sibling repos or monorepo?

- **Option a:** Three sibling repos under `jamieleigh3d/`:
  `termin-core`, `termin-server`, `termin-compiler` (plus existing
  `termin-conformance`, `termin-spectrum-provider`, `termin-dev`).
  Matches the existing `termin-compiler` + `termin-conformance` +
  `termin-spectrum-provider` pattern.
- **Option b:** Monorepo `termin/` with workspaces
  `termin-core/`, `termin-server/`, `termin-compiler/`,
  `termin-conformance/`. Coordinated PRs, single CI graph.

**Recommendation:** Option a. Consistent with the existing layout,
and crucially the existing `termin-compiler` repo gets *slimmed* in
place (just remove `termin_runtime/`); we don't need to rename or
restructure it. Atomic cross-repo changes can still happen via the
existing `util/release.py` script extended to handle the new repos.

**Risk under option a:** cross-repo dependency cycles are easier to
miss. Mitigate with a CI gate that builds each downstream repo
against a fresh install of upstream main.

---

### Q6 — Slicing: big-bang or incremental?

- **Option a:** One drop. Create both new repos, move all the
  modules in one go, fix all imports, ship. Clean cut, large blast
  radius — if anything breaks, the entire workspace breaks together.
- **Option b:** Incremental:
  - **7.1** — extract pure types (Issue #2 minimal): IR types,
    Provider Protocols, Principal/Redacted, deploy_config, CEL
    evaluator, errors. New `termin-core` repo. Reference runtime
    imports from it. Compiler still has its own IR (Q4 deferred to
    7.4).
  - **7.2** — extract framework-agnostic dispatch: `routing/crud.py`,
    `routing/route_specs.py`, the channel-dispatch topic router.
    Define `TerminRequest`/`TerminResponse`/`TerminWebSocket`
    abstractions (Q1 lands here). Reference runtime keeps its FastAPI
    adapter shim, no behavior change.
  - **7.3** — extract `termin-server`. Move FastAPI app factory,
    builtins, static assets out of the existing `termin-compiler`
    repo into a new sibling. Reference runtime is now pure
    composition. Compiler/conformance import paths flip to
    `termin-server`.
  - **7.4** — IR-types unification (Q4). Compiler imports IR types
    from core; `termin/ir.py` becomes a re-export shim that we drop
    in 7.5.
  - **7.5** — drop the back-compat shims, conformance pack lands.

**Recommendation:** Option b incremental. Matches the slicing
discipline that worked for Phase 5 (5a → 5b → 5c). Each slice is
independently revertable. Slice 7.1 alone unblocks alternate runtimes
on the contract surface; the dispatch extraction in 7.2 unblocks them
on
the routing surface.

---

### Q7 — Conformance pack scope?

- **Option a:** Land a new `termin-core-conformance` test pack
  inside `termin-conformance/`. Tests the contract surface in
  isolation: Provider Protocols satisfied by reference impls, deploy
  config resolution, IR-shape acceptance, CEL evaluation, predicate
  AST round-trips. Adapter-agnostic.
- **Option b:** Defer. Keep using the existing reference-adapter
  conformance suite, which exercises contracts indirectly.

**Recommendation:** Option a, lands as part of slice 7.5. The whole
point of the extraction is "this is the testable surface anyone can
claim conformance against." Without a pack that exercises the surface
directly, there's no objective standard for "this alternate runtime
conforms to termin-core."

---

### Q8 — Naming for the "reference runtime" package?

The current Python package is `termin_runtime`. Post-extraction,
that's a misnomer — `termin-core` *is* the runtime contract; the
extracted package is the **reference HOSTING / SERVER layer**.

- **Option a:** `termin-server` (Python package: `termin_server`).
  Describes what it is.
- **Option b:** `termin-runtime` kept as the package name.
  Compatible with existing imports during the migration.
- **Option c:** `termin-reference-runtime` (verbose but explicit).

**Recommendation:** Option a — `termin-server`. Clear, short,
matches FastAPI/Starlette ecosystem norms ("a server"), and signals
the reference-implementation status without being verbose. Option b
muddles the meaning; option c is what JL gestured at verbally but
is mouthful in import paths.

**Note:** the package rename is the **last** slice (7.5) so existing
`from termin_runtime import …` imports keep working through the
intermediate slices via a re-export shim, and we drop the shim only
when nothing uses it.

## 5. Slicing strategy (contingent on Q1–Q8)

Assuming the recommended answers (b, b, a, a, a, b, a, a):

| Slice | Goal | Scope | Approx size |
|---|---|---|---|
| 7.1 | Extract pure types | New `termin-core` repo with: ir/types.py, providers/{contract,registry,binding,deploy_config}, expression, confidentiality, identity (value types), errors, validation, state (rules only). Reference runtime imports from it. | ~30 files moved, ~50 imports flipped |
| 7.2 | Extract dispatch | `routing/{request,websocket,crud,channel_dispatch,route_specs}.py` lands in core. ASGI substrate adopted (Q1=b). Reference runtime gains `fastapi_adapter.py` shim. No behavior change end-to-end. | routes.py + channel_ws.py + websocket_manager.py refactored |
| 7.3 | Extract `termin-server` | New `termin-server` repo. Move FastAPI app, builtins (sqlite/anthropic/tailwind-ssr), static assets, CLI from `termin-compiler` repo. Compiler repo retains only `termin/` + tests for the compiler. | ~25 files moved across repos |
| 7.4 | Unify IR types | Compiler's `termin/ir.py` deletes its types and re-exports from `termin-core`. `lower()` builds core IR. | mechanical |
| 7.5 | Conformance pack + cleanup | New `termin-core-conformance` test pack. `termin_runtime` package alias dropped. Final docs/CHANGELOG. | ~50 tests + docs |

**Each slice is independently green-suite-able.** Each slice
preserves the contract that JL can compile and serve a `.termin.pkg`
without behavior change.

## 6. Out of scope for Phase 7

- **Splitting the compiler into its own repo.** `termin-compiler`
  stays as the compiler home; only `termin_runtime/` leaves it.
- **Multi-language ports of `termin-core`.** A parallel JSON Schema
  surface for Rust/TS clients is a v1.0 conversation. Phase 7 ships
  the Python-side extraction.
- **Versioning policy for `termin-core` releases.** SemVer-aligned
  with the runtime is the obvious default; a written policy is a
  v1.0 deliverable.
- **Replacing FastAPI in the reference runtime.** Termin-server
  stays on FastAPI. The whole point of the extraction is that other
  runtimes *don't have to*; the reference implementation can keep
  whatever framework it likes.
- **Removing the v0.9 deferred-tech-debt items not blocking Phase 7.**
  (uvicorn deprecation, state-dropdown on create forms,
  release-script `capture_output` hang.) Those are v0.10 / v1.0 work.

## 7. Pre-Phase-7 readiness check (status as of 2026-04-30)

- [x] Phase 5 + Phase 6 closed on `feature/v0.9` (compiler).
- [x] Phase 5 conformance pack passing (`feature/v0.9` of conformance).
- [x] `feature/v0.9` pushed on both repos.
- [x] Compiler suite green (2545 / Windows).
- [x] Conformance suite green (915 / 0 failed / Windows reference).
- [x] Spectrum provider feature parity item 0 closed.
- [x] All deferred technical-debt items either resolved or
      explicitly deferred-to-v1.0 (deploy template generator,
      `duration_ms=` kwarg, default db path).

**Phase 7 prerequisites are met.** Slice 7.1 can begin once JL
signs off on Q1–Q8.

## 8. Decisions (closed 2026-04-30)

| ID | Question | Recommendation | JL decision |
|---|---|---|---|
| Q1 | Abstract request shape — invent or ASGI | b (ASGI) | **b approved** |
| Q2 | Builtin providers — all out, or pure stays | b (pure stays) | **b approved** |
| Q3 | WebSocket dispatcher — full or partial | a (full) | **a approved** |
| Q4 | Compiler imports IR from core? | a (yes) | **a approved** |
| Q5 | Repo layout — sibling or monorepo | a (sibling) | **a approved** (debated; URL preservation + existing pattern over hybrid monorepo) |
| Q6 | Slicing — big-bang or incremental | b (incremental, 7.1–7.5) | **b approved** |
| Q7 | Core conformance pack — now or defer | a (now, in slice 7.5) | **a approved** |
| Q8 | Reference-runtime package name | a (`termin-server`) | **a approved** |

## 9. Slice 7.1 — entry conditions

When JL gives the go-ahead, slice 7.1 begins. Pre-flight:

- New repo `github.com/jamieleigh3d/termin-core` exists, empty,
  `feature/v0.9` branch ready to receive the first commit.
- A second working tree at `E:/ClaudeWorkspace/termin-core/`.
- The slice 7.1 scope (per §5):
  - `ir/types.py` — IR dataclasses
  - `providers/` — Protocol contracts, Category, Tier,
    ContractDefinition, ContractRegistry, binding resolution,
    deploy_config parser
  - `expression/` — CEL evaluator + Predicate AST
  - `confidentiality/` — Redacted sentinel + redact_records
  - `identity/` — Principal, PrincipalContext value types
  - `errors/` — TerminAtor + error envelope shapes
  - `validation/` — D-19 dependent_values, one_of_values
  - `state/` — pure state-machine evaluator
- The reference runtime in `termin-compiler/termin_runtime/` keeps
  working through slice 7.1 by importing from `termin-core` rather
  than its own modules. No FastAPI changes yet.
- Pin the `termin-core` version in `termin_runtime`'s setup.py at
  `>=0.9.0,<0.10` for the duration of v0.9 development.

7.1 commit boundary: every test in `termin-compiler` and
`termin-conformance` still green; conformance fixtures unchanged;
no behavior visible to a `.termin.pkg` consumer.
