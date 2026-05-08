# Termin v0.9.3 — Runtime Extraction Technical Design

**Status:** Draft v1 — decisions locked in planning thread 2026-05-07.
**Date:** 2026-05-07.
**Author:** JL + Claude.
**Companion documents:**
- `termin-v0.9.4-airlock-on-termin-tech-design.md` (formerly the v0.9.3
  airlock plan — bumped to v0.9.4 since the runtime-extraction work
  now occupies the v0.9.3 slot).
- `termin-runtime-implementers-guide.md` — the spec any alt runtime
  must conform to. v0.9.3 widens the surface this guide can reference
  in `termin-core`.
- `RELEASE_PROCESS.md` — the patch-vs-minor policy. v0.9.3 is a patch
  per §2 (additive Python API, no IR change, no DSL change).
- `termin-roadmap.md` — v0.10 backlog rows that this release does NOT
  cover (`queue-and-retry` worker, picker-binding grammar, GOV.UK
  provider, etc.).

**Phasing:** v0.9.0 (2026-04-30) split `termin-compiler` into
`termin-core` + `termin-server` + `termin-compiler` (Phase 7). v0.9.1
(2026-05-01) closed audit-trail gaps from the Phase 3 conformance
pack. v0.9.2 (2026-05-05) added the conversation field type, `Append`
verb, and AI-agent computes. **v0.9.3 (this document)** completes the
framework-free extraction from `termin-server` into `termin-core`,
enabling alternate Termin runtimes to consume the orchestration logic
without inheriting FastAPI / aiosqlite / Anthropic transitively.
v0.9.4 ports Airlock onto Termin as an advanced sample app. v0.10
ships the multi-tenant hosted platform on `termin.getclarit.ai`.

---

## 1. Purpose & Scope

**v0.9.3 is the "alt-runtime enabling" release.** Phase 7 of v0.9.0
(2026-04-30) split the runtime into two Python packages — `termin-core`
for the framework-free contract surface, `termin-server` for the FastAPI
hosting layer + IO-bound builtins — on the working assumption that an
alternate Termin runtime would import `termin-core` and ship its own
framework-bound surface. The week of v0.9.x work since (audit-trail
fixes in v0.9.1, the conversation-field type in v0.9.2) plus planning
for v0.9.4-airlock and v0.10-platform has surfaced the remaining gap:
**a substantial body of orchestration code in `termin-server` has zero
framework coupling but lives there historically, behind a
`pip install termin-server` that drags FastAPI, aiosqlite, and
Anthropic into any consumer's dep graph.**

This release moves that code to `termin-core` and tightens the public
API contract so an alt runtime — whether AWS-native, third-party-Rust,
or anything else — can build on `termin-core>=0.9.3` alone, without
ever touching `termin-server`.

**This document answers:**

- Which modules move from `termin-server` to `termin-core`, and where
  do they land in the new namespace layout?
- What is the `ComputeDispatcher` Protocol, and how does the existing
  Anthropic-backed AI provider plug into it?
- How does the page composition surface get extracted without touching
  the existing `PresentationProvider` Protocol from Phase 5a?
- What is the import-stability contract that conformance pins?
- What is the migration path for `termin-spectrum-provider` and
  `termin-conformance` as the server shims drop?

**This document does NOT:**

- Re-specify any Termin DSL primitive. v0.9.3 is API-shape work, not
  language work.
- Add IR fields. `ir_version` stays at 0.9.2; package versions in
  `termin-core`, `termin-server`, `termin-compiler`, and
  `termin-spectrum-provider` advance to 0.9.3 independently. The IR
  shape declaration in `termin-core/termin_core/ir/types.py` is
  unchanged. The next IR-shape change (whenever it ships) will bump
  `ir_version` to whatever package version is current at that point.
- Specify or scope Airlock-on-Termin (v0.9.4) or the hosted platform
  (v0.10).
- Cover storage backend extractions. SQLite stays in `termin-server`
  via `termin_server.providers.builtins.storage_sqlite`; alternate
  storage backends are their own provider package, scoped post-v1.0.

---

## 2. Design Goals

1. **Make alt runtimes easier to build.** Anything an alt runtime
   must reimplement to be conformant should live in `termin-core`,
   not behind a framework dep boundary in `termin-server`.
2. **Preserve the existing `PresentationProvider` Protocol.** The
   per-component-fragment Protocol from Phase 5a is load-bearing for
   Tailwind + Spectrum mixing. Issue #4 was overlooking it; the work
   here moves the page composition utilities to core *next to* the
   existing Protocol, not *on top of* it.
3. **Keep the runtime infrastructure pure-asyncio.** Events,
   scheduler, transactions, reflection are contract-shape primitives,
   not server implementations. Their semantics are identical across
   every conformant runtime; the code should be too.
4. **Keep Anthropic + SQLite + FastAPI in `termin-server`.** The IO
   providers, framework adapter, and concrete storage stay where they
   are. Only the framework-free orchestration helpers move.
5. **Zero IR change.** v0.9.3 is internal API only. Existing apps
   compile and run unchanged; existing `.termin.pkg` artifacts load
   and serve unchanged. The package-version bump exists to signal the
   import-surface change for downstream consumers.
6. **No back-compat shims in `termin-server`.** Pre-v1.0 policy:
   server-side modules that today are `from termin_core.X import *`
   re-exports get deleted, not preserved. Consumers (including
   conformance and Spectrum) update their imports as part of this
   release. This is the same policy that landed v0.9.0 → v0.9.2; the
   shim layer that has accumulated since then ends here.

---

## 3. Decisions Locked

These were resolved in the 2026-05-07 planning thread and are pinned
here for posterity so future readers can see what was traded against
what.

| Decision | Resolution |
|---|---|
| Single release or split? | Single v0.9.3 — all 10 items below land together. |
| `ir_version` bump? | No. Stays 0.9.2 until next IR-shape change. |
| Patch or minor? | Patch per `RELEASE_PROCESS.md` §2 (additive API, no removal of public surface). |
| Issue #1 — `build_compute_js` placement? | `termin_core.expression.compute_js` (CEL-shaped, evaluator-adjacent). |
| Issue #2 — single dispatcher or several? | **Both:** per-class dispatchers (`dispatch_crud_request`, `dispatch_channel_request`, etc.) + a thin convenience `dispatch_http_request` that routes to them. Alt runtimes can mount per-class or call the convenience. |
| Issue #3 — `ComputeDispatcher` covers CEL too? | No. CEL stays as a free function (`execute_cel_compute`) in core; `ComputeDispatcher` Protocol covers LLM + agent dispatch only. CEL is part of the deterministic core, not a swappable provider. |
| Issue #3 — AI provider extraction? | Folded into Issue #3. Portable orchestration helpers (kind→role mapping, tool spec assembly, conversation materialization) move to `termin_core.compute.materialize`; the Anthropic SDK call site stays in `termin_server.ai_provider`. |
| Issue #4 — replace existing `PresentationProvider`? | **No.** Existing per-component Protocol is load-bearing. Issue #4 reframed as "extract page composition utilities to core" — `build_base_template`, `build_page_template`, etc. — as free functions in a new `termin_core.presentation.compose` module. |
| Tier 1 inclusion? | All 8 modules (events, boundaries, reflection, scheduler, transaction, markdown_sanitizer, colorblind, migrations) included in v0.9.3. |
| Tier 2 — channels? | Include `channels.py` and `channel_config.py` (channel dispatch logic + deploy config validation). Alt runtimes that ship outbound channel delivery need this. |
| Tier 2 — `channel_ws.py` (outbound WebSocket)? | **Defer.** Reusability for AWS-native runtimes is unclear (those would use API Gateway / SQS / SNS, not raw WebSocket); revisit when an alt runtime actually needs it. Stays in `termin-server` for v0.9.3. |
| Tier 3 (identity, bootstrap, websocket_manager splits)? | Defer. Higher-risk extractions; not required for an alt runtime that brings its own framework adapter. Revisit during v0.10 prep if hosted-platform pressure surfaces a need. |
| Namespace placement? | Top-level (`termin_core.events`, `termin_core.scheduler`, `termin_core.reflection`, etc.) — peers of existing top-level modules like `termin_core.expression`, `termin_core.state`. |
| Server-side shims after the move? | **None.** Drop existing shims (`termin_server.errors`, `.state`, `.validation`, `.expression`, `.confidentiality`, `.cel_predicate`, `.providers/*`) AND don't introduce new ones for the v0.9.3 moves. Server code switches imports to `termin_core` directly in the same patch. |
| Number of commits? | One commit per item — 10 commits in `termin-compiler` repo, plus per-repo commits in `termin-core`, `termin-server`, `termin-conformance`, `termin-spectrum-provider` for their respective changes. |
| Conformance impact? | New `tests/test_alt_runtime_imports.py` pack asserting the new `termin_core` surface stays stable. ~30-40 import + smoke assertions. |

---

## 4. Architecture Overview

### Before v0.9.3

```
termin-core (framework-free)
├── ir/                          # IR types
├── expression/                  # CEL evaluator + predicate
├── confidentiality/             # redaction
├── errors/                      # TerminError + router
├── state/                       # state machine logic
├── validation/                  # field validators
├── providers/                   # Provider Protocols + registry
└── (small surface; alt runtimes outgrow it quickly)

termin-server (FastAPI + IO + everything else)
├── app.py, routes.py, bootstrap.py    # FastAPI bindings
├── pages.py, presentation.py          # SSR + page composition
├── compute_runner.py                  # Compute orchestration
├── ai_provider.py                     # Anthropic SDK (1997 lines!)
├── events.py, scheduler.py, transaction.py    # framework-free!
├── reflection.py, boundaries.py               # framework-free!
├── markdown_sanitizer.py, colorblind.py       # framework-free!
├── channels.py, channel_config.py             # framework-free!
├── migrations/                                # framework-free!
├── storage.py                         # aiosqlite
├── identity.py, websocket_manager.py  # FastAPI-coupled
├── providers/                         # shim layer to core
└── (alt runtime must either reimplement everything framework-free
    above or import termin-server and inherit FastAPI + Anthropic
    + aiosqlite as transitive deps)
```

### After v0.9.3

```
termin-core (framework-free contract surface + orchestration)
├── ir/, expression/, confidentiality/, errors/, state/, validation/, providers/
├── events.py                    ← from termin_server.events
├── scheduler.py                 ← from termin_server.scheduler
├── transaction.py               ← from termin_server.transaction
├── reflection.py                ← from termin_server.reflection
├── boundaries.py                ← from termin_server.boundaries
├── colorblind.py                ← from termin_server.colorblind
├── migrations/                  ← from termin_server.migrations/
├── channels.py                  ← from termin_server.channels (dispatcher only)
├── channel_config.py            ← from termin_server.channel_config
├── compute/
│   ├── __init__.py              ← ComputeDispatcher Protocol (NEW)
│   ├── cel.py                   ← execute_cel_compute (NEW; from compute_runner)
│   └── materialize.py           ← portable AI helpers (NEW; from ai_provider)
├── routing/
│   ├── __init__.py              ← dispatch_http_request (NEW)
│   ├── crud.py                  ← dispatch_crud_request (NEW; from routes.py)
│   ├── channel.py               ← dispatch_channel_request (NEW; from routes.py)
│   ├── reflection.py            ← dispatch_reflection_request (NEW)
│   └── runtime.py               ← dispatch_runtime_request (NEW)
├── presentation/
│   ├── markdown_sanitizer.py    ← from termin_server.markdown_sanitizer
│   ├── compose.py               ← page composition utils (NEW; from pages.py)
│   └── (existing presentation_contract.py untouched)
└── expression/
    └── compute_js.py            ← from termin_server.pages (NEW)

termin-server (framework + IO only)
├── app.py, routes.py, bootstrap.py    # FastAPI bindings, thinned out
├── pages.py                           # FastAPI page-handler shells
├── presentation.py                    # Jinja2 SSR renderer
├── ai_provider.py                     # Anthropic SDK wrapper, thinned
├── storage.py                         # aiosqlite
├── identity.py                        # FastAPI cookie/header extraction
├── websocket_manager.py               # FastAPI WebSocket binding
├── channel_ws.py                      # outbound WebSocket connection (deferred)
├── fastapi_adapter.py                 # FastAPI ↔ core bridge
├── providers/builtins/                # SqliteStorageProvider, AnthropicAIProvider, etc.
└── (everything that needs a framework or IO library; nothing else)
```

The boundary line is sharp after v0.9.3: **if the module imports
`fastapi`, `starlette`, `aiosqlite`, `anthropic`, or `jinja2` —
including transitively — it stays in `termin-server`. Otherwise it
moves to `termin-core`.**

---

## 5. Items in Scope

Ten items, batched as one release. Each item gets its own commit
across the affected repos.

### Item 1 — `build_compute_js` → `termin_core.expression.compute_js`

**What:** Move the JavaScript fragment that the runtime injects into
SSR pages so client-side highlight CEL expressions can evaluate
against the loaded record set.

**Why:** Issue #1. CEL is the universal expression layer; the
JS-fragment builder belongs evaluator-adjacent so any runtime serving
SSR pages can produce the same client-side payload.

**From:** `termin_server/pages.py` (the `build_compute_js` function).
**To:** `termin_core/expression/compute_js.py`.

**Surface:** Single public function `build_compute_js(ir, page_entry)
-> str`. No new types. Pure string assembly.

**Server change:** `termin_server/pages.py` updates its import to
`from termin_core.expression.compute_js import build_compute_js` and
calls it the same way it calls it today.

**Estimate:** ~1 hour.

---

### Item 2 — HTTP route dispatch → `termin_core.routing`

**What:** Extract the per-route-class dispatch logic that today lives
inline in `termin-server/routes.py` into pure functions in core, then
add a thin `dispatch_http_request` convenience that routes to the
per-class dispatchers.

**Why:** Issue #2. Alt runtimes mounting their own framework need a
way to delegate the actual route logic to core without rewriting
~1100 lines of CRUD, append, transition, channel, reflection, SSE,
and runtime-endpoint dispatch.

**From:** `termin_server/routes.py` (most of it; FastAPI binding stays
in server).
**To:** `termin_core/routing/` (new package).

**Surface:**

```python
# termin_core.routing
async def dispatch_http_request(
    ctx: RuntimeContext,
    request: HttpRequest,
) -> HttpResponse: ...
    # Convenience: dispatches to the appropriate per-class function
    # below based on request.path + method.

# termin_core.routing.crud
async def dispatch_crud_request(...) -> HttpResponse: ...
    # GET/POST/PUT/PATCH/DELETE on /api/v1/<resource>[/{id}].

# termin_core.routing.channel
async def dispatch_channel_request(...) -> HttpResponse: ...
    # POST /api/v1/_channel/<name>; SSE on /api/v1/_channel/<name>/stream.

# termin_core.routing.reflection
async def dispatch_reflection_request(...) -> HttpResponse: ...
    # GET /api/v1/_reflection/...

# termin_core.routing.runtime
async def dispatch_runtime_request(...) -> HttpResponse: ...
    # GET /api/v1/_runtime, /api/v1/_health, /api/v1/_registry.
```

`HttpRequest` and `HttpResponse` are framework-agnostic dataclasses
defined in `termin_core.routing` (path, method, query, headers, body
bytes, principal, etc.). The FastAPI adapter in `termin-server` does
the `Request` ↔ `HttpRequest` conversion per existing pattern.

**Server change:** `termin-server/routes.py` becomes ~150 lines of
FastAPI route registrations, each delegating to a per-class core
dispatcher via the existing adapter.

**Estimate:** 1.5-2 days. Most cost is the request/response value-type
design plus adapter plumbing.

---

### Item 3 — Compute dispatch + AI provider extraction → `termin_core.compute`

**What:** Define the `ComputeDispatcher` Protocol, extract CEL compute
execution as a free function, and move the portable orchestration
helpers from `ai_provider.py` to core.

**Why:** Issue #3. Alt runtimes need to register their own LLM /
agent dispatch implementation; the CEL path must be available
unconditionally; the conversation materialization + tool spec
assembly logic is ~600 lines of pure orchestration that has nothing
to do with the Anthropic SDK.

**From:** `termin_server/compute_runner.py` (CEL execution path),
`termin_server/ai_provider.py` (~600 lines of portable helpers).
**To:** `termin_core/compute/__init__.py` + `cel.py` + `materialize.py`.

**Surface:**

```python
# termin_core.compute
class ComputeDispatcher(Protocol):
    async def dispatch(
        self,
        compute: ComputeSpec,
        trigger_event: TriggerEvent,
        record: Mapping[str, Any] | None,
        ctx: RuntimeContext,
    ) -> ComputeResult: ...

# termin_core.compute.cel
async def execute_cel_compute(
    compute: ComputeSpec,
    trigger_event: TriggerEvent,
    record: Mapping[str, Any] | None,
    ctx: RuntimeContext,
) -> ComputeResult: ...
    # Free function — not registered through the Protocol. Pure CEL.

# termin_core.compute.materialize
def materialize_conversation_to_messages(
    conversation_field: list[dict],
    config: MaterializationConfig,
) -> list[dict]: ...
    # Kind → role mapping, adjacent-role merging, system prompt
    # assembly, tool spec building. SDK-agnostic output shape.

def build_invokable_compute_tools(...) -> list[dict]: ...
    # Tool spec assembly from the IR's compute declarations.
```

The Anthropic-specific call site (`anthropic.AsyncClient.messages.create`)
stays in `termin_server/ai_provider.py`. That module becomes a thin
wrapper that imports from `termin_core.compute.materialize` for
input prep, calls the SDK, and parses the response.

**`ComputeDispatcher` registration:** `RuntimeContext` gains a
`compute_dispatcher: ComputeDispatcher | None` field. If `None`, only
CEL computes execute (LLM/agent computes raise a TerminAtor with
`compute.no_dispatcher_registered`). The reference runtime registers
its `AnthropicComputeDispatcher` at app startup.

**Server change:** `termin-server/compute_runner.py` becomes the
orchestration layer that picks CEL vs. dispatcher based on
`compute.shape`; CEL path now imports from core; LLM/agent path goes
through the registered dispatcher.

**Estimate:** 2 days. Most cost is the dispatcher Protocol shape
review + ai_provider split.

---

### Item 4 — Page composition utilities → `termin_core.presentation.compose`

**What:** Move the page-level composition walk (which combines
per-component renders into a full HTML response) from
`termin_server/pages.py` to a new module in core. **Existing
`PresentationProvider` Protocol is untouched.**

**Why:** Issue #4 reframed. The original issue conflated "extract
page composition" with "add a per-page Protocol." The latter would
break Tailwind + Spectrum mixing; the former is genuinely portable.

**From:** `termin_server/pages.py` (`build_base_template`,
`build_page_template`, `build_merged_page_template`,
`extract_page_reqs`, helper utilities).
**To:** `termin_core/presentation/compose.py`.

**Surface:**

```python
# termin_core.presentation.compose
def build_base_template(app_name: str, nav_html: str) -> str: ...
def extract_page_reqs(page_entry: PageEntry, ir: dict) -> PageReqs: ...
def build_page_template(
    page_entry: PageEntry,
    page_reqs: PageReqs,
    rendered_components: dict[str, str],
    ctx: RuntimeContext,
) -> str: ...
def build_merged_page_template(...) -> str: ...
```

These are pure functions over IR + rendered fragments. No framework
deps. They consume per-component renders that the existing
`PresentationProvider.render_ssr` produces; they don't replace that
Protocol.

**Server change:** `termin-server/pages.py` thins down to FastAPI
route handlers that fetch data, run the per-component
`PresentationProvider.render_ssr` calls, and pass results into the
core compose functions for final HTML assembly.

**Estimate:** ~half day.

---

### Item 5 — Runtime infrastructure → top-level core modules

**What:** Move four pure-asyncio runtime infrastructure modules to
top-level locations in `termin-core`.

**Why:** These are contract-shape primitives. Their semantics are
identical across every conformant runtime; their code should be too.
Conformance asserts on the behavior of all four.

| Source | Destination | Lines |
|---|---|---|
| `termin_server/events.py` (`EventBus`) | `termin_core/events.py` | 54 |
| `termin_server/scheduler.py` (`Scheduler`, `parse_schedule_interval`) | `termin_core/scheduler.py` | 111 |
| `termin_server/transaction.py` (`Transaction`, `ContentSnapshot`, `StagedWrite`) | `termin_core/transaction.py` | 250 |
| `termin_server/reflection.py` (`ReflectionEngine`) | `termin_core/reflection.py` | 164 |

**Surface:** Identical to today's `termin-server` exports. Pure code
move, no API changes.

**Server change:** `termin-server` imports them from core wherever it
does today.

**Estimate:** 2 hours total (pure code moves).

---

### Item 6 — Security + accessibility primitives → core

**What:** Move two pure-logic modules that providers and runtimes
both consume.

| Source | Destination | Lines |
|---|---|---|
| `termin_server/boundaries.py` | `termin_core/boundaries.py` | 89 |
| `termin_server/markdown_sanitizer.py` | `termin_core/presentation/markdown_sanitizer.py` | 197 |
| `termin_server/colorblind.py` | `termin_core/colorblind.py` | 204 |

**Why:** Boundaries are an IR-level Termin primitive. The markdown
sanitizer is the BRD-mandated implementation any runtime serving the
`presentation-base.markdown` contract must use. Colorblind utilities
are needed by any provider validating CVD-distinguishability of
themed palettes (Spectrum already uses these via the server import).

**Server change:** Standard import update.

**Estimate:** 1 hour.

---

### Item 7 — IR migrations → `termin_core.migrations`

**What:** Move the entire `termin_server/migrations/` package to
`termin_core/migrations/`.

**Why:** IR migration is an IR-shape concern, not a server concern.
Conformance imports from this namespace today and asserts on its
behavior. Alt runtimes need identical migration logic.

**From:** `termin_server/migrations/` (5 files: `classifier.py`,
`validate.py`, `introspect.py`, `ack.py`, `errors.py`; ~1390 lines
total).
**To:** `termin_core/migrations/`.

**Surface:** Identical to today.

**Server change:** Re-imports. No drop-in shim — server code that
uses the migrations package switches its imports.

**Conformance change:** Three test files
(`test_v09_migration_classifier.py`, `test_v09_migration_apply.py`,
`test_v09_migration_ack_gating.py`) update their imports from
`termin_server.migrations.*` → `termin_core.migrations.*`.

**Estimate:** ~half day. Most cost is verifying the test surface
behaves identically across the import change (it should — pure
relocation).

---

### Item 8 — Channel dispatch → `termin_core.channels`

**What:** Move the channel dispatcher class and deploy-config loading
to core. **Outbound WebSocket connection (`channel_ws.py`) stays in
`termin-server`** for v0.9.3.

| Source | Destination | Lines |
|---|---|---|
| `termin_server/channels.py` (`ChannelDispatcher`) | `termin_core/channels.py` | 621 |
| `termin_server/channel_config.py` | `termin_core/channel_config.py` | 245 |

**Why:** Outbound channel delivery is universal — every alt runtime
needs to dispatch declared Channels with the same `failure_mode`
semantics. Deploy-config loading + validation rules are likewise
universal. The WebSocket-specific connection logic is deferred
because reusability across an AWS-native runtime (which would route
through API Gateway / SQS / SNS instead) is unclear.

**Server change:** Update imports. The `ChannelDispatcher` factory
in `termin-server/app.py` still picks the right concrete provider per
deploy config; that logic doesn't change.

**Estimate:** ~half day. The dispatcher has a few subtle couplings to
the broader runtime context that need careful handling at the import
boundary.

---

### Item 9 — Drop existing server shims

**What:** Delete the v0.9.0 shim modules in `termin-server` that
today are pure `from termin_core.X import *` re-exports.

**Why:** Pre-v1.0 policy says no back-compat shims. The shim layer
introduced in slice 7.1 of Phase 7 was meant to be dropped in slice
7.5; it wasn't, and accumulating new shims for the v0.9.3 moves
would compound the confusion JL flagged in the planning thread
("makes it more confusing").

**Files deleted from `termin-server`:**
- `termin_server/errors.py`
- `termin_server/state.py`
- `termin_server/validation.py`
- `termin_server/expression.py`
- `termin_server/confidentiality.py`
- `termin_server/cel_predicate.py`
- `termin_server/providers/__init__.py` (the shim package)
- `termin_server/providers/binding.py`
- `termin_server/providers/channel_contract.py`
- `termin_server/providers/compute_contract.py`
- `termin_server/providers/contracts.py`
- `termin_server/providers/deploy_config.py`
- `termin_server/providers/identity_contract.py`
- `termin_server/providers/presentation_contract.py`
- `termin_server/providers/registry.py`
- `termin_server/providers/storage_contract.py`

(`termin_server/providers/builtins/` is NOT deleted — it contains
concrete provider implementations, not shims.)

**Consumers updated:**
- `termin-spectrum-provider` (4 files): imports switch from
  `termin_server.providers.*` → `termin_core.providers.*`.
- `termin-conformance` (3 files): imports switch from
  `termin_server.providers.storage_contract` →
  `termin_core.providers.storage_contract`.
- `termin-server` itself: ~25 internal imports updated.

**Estimate:** ~2 hours (mechanical, but spread across three repos).

---

### Item 10 — Conformance import-stability test pack

**What:** Add `termin-conformance/tests/test_alt_runtime_imports.py`
asserting that every public name an alt runtime depends on is
importable from `termin_core` and behaves correctly.

**Why:** Insurance. The whole point of v0.9.3 is to make alt-runtime
dependence on `termin-core` viable; if we accidentally move a name
in v0.9.4 the conformance suite catches it.

**Coverage:**
- ~30-40 import assertions across the new core modules.
- Smoke tests for each top-level extracted class/function (instantiate
  EventBus, build a small Transaction, sanitize a markdown string,
  classify a fake IR migration, etc.).
- Assertion that no `termin_server.*` re-export shim exists for the
  modules in scope (catches a future slip-up where someone adds a
  shim back "for compatibility").

**Estimate:** ~half day.

---

## 6. Out of Scope

Explicitly deferred to later releases.

| Item | Reason | Target |
|---|---|---|
| `channel_ws.py` (outbound WebSocket connection) | Reusability for AWS-native runtimes unclear; alt runtimes that route via API Gateway / SQS / SNS won't use raw WebSocket. Revisit when an actual alt runtime needs it. | v0.9.5 or later |
| Tier 3 splits (`identity.py`, `bootstrap.py`, `websocket_manager.py`) | Higher-risk extractions; not blocking for an alt runtime that brings its own framework adapter. | v0.10 prep, conditional on hosted-platform need |
| Storage backend extraction (alt-storage provider) | Out of scope for v0.9.3 — provider package work, post-v1.0 milestone. | post-v1.0 |
| `presentation.py` Jinja2 SSR renderer | Bound to Jinja2; alt runtimes will ship their own SSR. The component-dispatch table abstraction inside it could move to core later, but isn't blocking. | v0.10 conditional |
| IR change | None planned in v0.9.3. | next IR-shape change (TBD) |
| DSL change | None planned in v0.9.3. | n/a |

---

## 7. Cross-Repo Impact

### `termin-compiler`

- No direct code change. The compiler doesn't import the moving
  modules.
- `setup.py` bumps `0.9.2` → `0.9.3`.
- `CHANGELOG.md` gets a v0.9.3 entry.
- `README.md` "v0.9 release arc" gets the v0.9.3 row appended.
- `docs/termin-roadmap.md` gets v0.9.3 marked done.

### `termin-core`

- Receives all the moved modules (Items 1-8).
- `setup.py` / `pyproject.toml` bumps `0.9.2` → `0.9.3`.
- `__init__.py` `__version__` bumps.
- New top-level imports added to `__all__` where applicable.
- `tests/` gets unit tests for the new modules (most are pure
  relocations of existing tests).

### `termin-server`

- Loses ~3500 lines of code to the move.
- Loses 16 shim files (Item 9).
- ~25 internal imports updated to `termin_core.*`.
- `setup.py` / `pyproject.toml` bumps `0.9.2` → `0.9.3`.
- `setup.py` `install_requires` bumps `termin-core>=0.9.2` →
  `termin-core>=0.9.3`.
- All 12 builtin provider `version=` strings bump (existing pattern
  from v0.9.2 — `release.py` handles via the version-string sweep).

### `termin-conformance`

- 3 test files update imports (Item 7 + Item 9).
- 1 new test file (Item 10).
- `setup.py` / `pyproject.toml` bumps `0.9.2` → `0.9.3` (alignment
  only; conformance pack doesn't gain new tests, just the
  import-stability pack).
- `CHANGELOG.md` documents the import surface change for downstream
  alt-runtime authors reading conformance for the first time.

### `termin-spectrum-provider`

- 4 files update imports (Item 9).
- `setup.py` bumps `0.9.2` → `0.9.3` (alignment only; no provider
  surface change).
- Spectrum CI re-runs against the new core surface.

### `termin-airlock-provider` (does not exist yet)

- N/A. v0.9.4 work; will benefit from the cleaner core surface.

---

## 8. Release Process

Standard `util/release.py` flow per `RELEASE_PROCESS.md`. The script
already covers all five repos as of v0.9.2; no script-side changes
needed for v0.9.3.

```bash
python util/release.py --compiler-version 0.9.3
# (no --ir-version flag this time — IR stays at 0.9.2)
```

The script:
1. Bumps Python package versions in compiler + core + server +
   spectrum (4 repos via `VERSION_FILES["compiler_version"]`).
2. Runs the four test suites: compiler (2643+), core (273+ + new
   tests for moved modules), server (98+, possibly fewer if some
   tests follow modules to core), conformance (1066 + new
   alt-runtime-imports pack).
3. Regenerates `.termin.pkg` fixtures in `termin-conformance/fixtures/`.

**Then per the established checklist:**
1. Manual review of `git diff` in each repo (5-min code-name sweep
   per the public-repo discipline rule).
2. One commit per item per repo (10 items × varies = ~25-30 commits
   total across the five repos).
3. Tag `v0.9.3` in each repo.
4. Push main + tags after explicit GO from JL.
5. Update `MEMORY.md` + `journal.md` post-ship.

---

## 8.5 Implementation deviations (2026-05-07)

Three places where implementation diverged from the planning shape;
recording here so the spec stays faithful to what shipped.

1. **Item 4 — page composition extraction reduced.** Plan said move
   `build_base_template`, `build_page_template`,
   `build_merged_page_template`, and `extract_page_reqs` to
   `termin_core.presentation.compose`. Actual: only
   `extract_page_reqs` moved. The `build_*template` functions return
   Jinja2 ``Template`` objects and use the Jinja-bound
   ``render_component`` dispatch table inside
   ``termin_server.presentation``. Moving them to core would either
   drag Jinja2 into core's dep graph or require a templating
   abstraction that isn't blocking for any alt runtime today.
   Documented in core's ``presentation/compose.py`` docstring and
   added to the v0.10 backlog if a concrete need surfaces.

2. **Item 8 — `channel_ws` moved too.** Plan deferred `channel_ws.py`
   ("uncertain WebSocket reusability for AWS-native runtimes"), but
   `channels.py` imports `WebSocketConnection` from `channel_ws`
   directly. Keeping `channel_ws` in `termin-server` would have
   created a backwards `termin-core → termin-server` dependency,
   which is worse than just moving it. The module is 159 lines of
   pure asyncio with optional `websockets` library import (graceful
   fallback when the library isn't installed) — no framework
   coupling to justify the deferral. Moved alongside `channels` and
   `channel_config`.

3. **Item 3 — `build_agent_tools` and `build_output_tool` kept
   parallel.** The server's `ai_provider.py` has richer versions of
   both functions (with `state_transition`, `system_refuse`, and
   per-content schema elaboration) than what landed in
   `termin_core.compute.materialize`. The core versions are a basic
   scaffold for alt runtimes to extend; the server keeps its
   richer local versions for the Anthropic call site. Pre-v1.0
   parallel implementations are tolerated; v1.0 cleanup either
   teaches core's version to match or drops the server-local
   versions entirely. The other materialize helpers
   (`materialize_to_anthropic`, `entry_role`, `build_content_blocks`,
   `build_invokable_compute_tools`, `truncate_purpose`,
   `purpose_property`, `add_purpose_to_tool`, plus the kind sets and
   exception class) are imported from core into ai_provider.py
   with no parallel definition.

4. **Item 2 — append handler dual-implementation.** The server's
   `_do_append` keeps a parallel implementation in `routes.py` for
   v0.9.3. Reason: server uses `aiosqlite`-direct `update_record(...,
   event_bus=None)` to suppress the standard `_updated` event so it
   doesn't double-fire alongside the field-specific `appended`
   event. The core `append_to_field` uses `ctx.storage.update(...)`
   via the StorageProvider Protocol, which doesn't expose the
   event-suppression hook today. Alt runtimes get the core
   implementation; the reference runtime keeps its server-local
   version. Cleanup is either teaching `StorageProvider.update` to
   accept an event-suppression flag (cleaner) or refactoring the
   reference runtime to fire one event from the append path
   (simpler) — both v0.10 backlog candidates.

These deviations are tracked in the implementation summary
returned to JL alongside this release; no scope was added beyond
what was approved.

## 9. Risks & Open Questions

### Risks

1. **Spectrum CI breakage during the import sweep.** Mitigation:
   commit Item 9 (shim drop + Spectrum imports) atomically in a
   single push window across `termin-server` + `termin-spectrum-provider`.
   If Spectrum push lands first or core push lands first, Spectrum
   CI red-flags briefly. Coordinate the push order.
2. **Conformance test surface drift.** Pure code moves shouldn't
   change behavior, but `test_v09_migration_*` files are large and
   import-tangled. Mitigation: run conformance against the new core
   surface BEFORE the shim drop, then run again AFTER.
3. **The `compute_runner.py` split is the longest-running item.**
   ~600 lines of `ai_provider.py` extraction plus the Protocol
   surface design. Estimate could grow to 3 days. Mitigation: ship
   Items 1, 4, 5, 6, 7 first as a "quick wins" batch; Items 2, 3
   take longer; Items 8, 9, 10 close the release.

### Open questions

None blocking. The decisions table in §3 closes everything that came
up during planning.

---

## 10. Estimate Summary

One focused evening, ~6-8 hours total. Every item is well-bounded and
most are direct code moves. Items 2 and 3 are the only ones with
design depth. Item 9 is the only multi-repo coordination point.

| Item | Rough cut |
|---|---|
| 1. `build_compute_js` → core | ~15 min |
| 2. HTTP route dispatch → core | ~2-3 h (design) |
| 3. ComputeDispatcher + AI helpers → core | ~2-3 h (design) |
| 4. Page composition → core | ~30 min |
| 5. Runtime infrastructure (4 modules) | ~30 min |
| 6. Security + accessibility (3 modules) | ~15 min |
| 7. Migrations package | ~30 min |
| 8. Channel dispatch | ~30 min |
| 9. Shim drop + import sweep | ~30 min |
| 10. Conformance import-stability pack | ~30 min |

---

## 11. Success Criteria

v0.9.3 ships when:

1. All 10 items committed across the five repos.
2. `python util/release.py --compiler-version 0.9.3` exits clean.
3. Compiler + core + server + conformance test suites green
   (currently 2643 + 273 + 98 + 1066 = 4080; expect similar shape
   post-extraction, possibly with some tests migrating from server
   to core alongside their modules).
4. Spectrum Python tests green (currently 16; expect 16 after import
   updates).
5. `termin compile examples/warehouse.termin && termin serve` smoke
   test passes on Windows AND WSL (per the cross-platform smoke
   norm).
6. Tags pushed to all five remotes.
7. `MEMORY.md` + `journal.md` updated.

The **alt-runtime-readiness** test is implicit: any future alt
runtime should be able to `pip install termin-core` and
`pip install termin-conformance` (no `termin-server` dep) and run
the in-process `reference` adapter against its own runtime. v0.9.3
makes that physically possible. v0.9.4 (Airlock-on-Termin) and v0.10
(hosted platform) will exercise it for real.
