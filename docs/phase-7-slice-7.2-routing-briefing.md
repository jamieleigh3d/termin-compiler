# Phase 7 — Slice 7.2 Routing Briefing

**Status:** Q1–Q4 closed (JL approved 2026-04-30 night). Q5 (slicing)
defaulted to sub-slices. 7.2.a/b/c/d landed; 7.2.e blocked on the
Principal-flow question (see §11 below).
**Author:** Claude Anthropic.
**Last touched:** 2026-04-30 night (autonomous mode).

## Where we are

Slice 7.2.a (framework-agnostic exception types), 7.2.b
(validation migration), and 7.2.c (state.py migration) are landed
and committed locally. Both suites still green:

- compiler 2545 / Windows
- conformance 915 / Windows reference

Now the architecturally substantial part of slice 7.2: extracting
the routing dispatch surface (REST + WebSocket) into termin-core
so alternate runtimes get framework-agnostic dispatch for free
and only have to write a thin adapter for their HTTP / WS
framework.

`routes.py` alone is 1063 lines with 87 FastAPI references. Five
real architectural decisions need JL's eyes before I can land
this cleanly.

## Key constraints (recap from the Phase 7 design)

- **Q1 ASGI substrate** — termin-core builds on top of ASGI
  semantics, not invented Request/Response types.
- **Q3 full WebSocket extraction** — `TerminWebSocket` Protocol +
  topic dispatch + connection management all in core; adapters
  wrap their framework's WS type.
- **No FastAPI in termin-core** — the framework-free guard in
  `tests/test_smoke.py` enforces this.
- **Behavior preservation** — every existing conformance test
  (915) must still pass after the extraction; the move is
  rearrangement, not redesign.

## Q1 — `TerminRequest` shape: rich vs. thin?

The route handlers need a request abstraction. Two flavors of
"ASGI substrate":

- **Option a — thin (raw ASGI scope/receive).** Handler signature:
  `async def handler(scope: dict, receive: Callable, send: Callable, ctx)`.
  Maximum portability — *any* ASGI host (FastAPI, Starlette,
  Quart, Hypercorn, raw uvicorn) just calls the handler. But every
  handler needs to parse the body, decode query strings, etc., or
  we ship parsing helpers next to it. Lots of boilerplate per
  handler.

- **Option b — rich (parsed convenience type).** Handler signature:
  `async def handler(request: TerminRequest, ctx) -> TerminResponse`.
  Where:

  ```python
  @dataclass
  class TerminRequest:
      method: str
      path: str
      path_params: dict[str, str]
      query_params: dict[str, str]
      headers: dict[str, str]   # case-insensitive lookup
      cookies: dict[str, str]
      body: bytes
      principal: Optional[Principal]  # filled by adapter middleware
      async def json(self) -> Any: ...
      async def form(self) -> dict[str, str]: ...

  @dataclass
  class TerminResponse:
      status_code: int = 200
      headers: dict[str, str] = field(default_factory=dict)
      body: bytes | None = None
      json_body: Any = None        # if set, body serializes from this
      redirect_url: str | None = None  # 303 + Location header
      streaming: AsyncIterator[bytes] | None = None  # for SSE
  ```

  The adapter does the ASGI scope/receive parsing once and hands a
  `TerminRequest` to the handler. The handler returns a
  `TerminResponse`; adapter translates back to ASGI send events.

**My recommendation: option b.** The thin version pushes the same
parsing boilerplate into every one of ~12 handlers, with no real
benefit — every adapter we'd realistically write already does this
parsing. The rich version is one-time overhead in the adapter.

**Trade-off if option b is chosen:** termin-core's
`TerminRequest.json()` becomes the canonical parser; adapters that
want to keep their framework's parsing (e.g., FastAPI's pydantic
body validation) need to either bypass it (call our parser) or
build a `TerminRequest` from a pre-parsed body. The current
`route_specs` design lets each handler declare what it consumes.

## Q2 — Route dispatch shape: decorator vs. declarative list?

Most Termin routes are **dynamic** — generated from the IR per
content type at app startup. Today the runtime walks
`ctx.ir["content"]` and registers handlers via FastAPI decorators
inside Python loops.

- **Option a — keep the decorator pattern, port to TerminRouter.**
  termin-core defines a `TerminRouter` class with `.get()`,
  `.post()`, `.put()`, `.delete()`, `.websocket()` decorators. The
  runtime builds a router instance, decorates handlers, and passes
  it to the adapter. Adapter binds the router's route table to its
  framework.

- **Option b — declarative `RouteSpec` list.**

  ```python
  @dataclass
  class RouteSpec:
      method: str
      path: str             # "/api/v1/{content}"
      handler: Callable[[TerminRequest, Any], Awaitable[TerminResponse]]
      required_scope: Optional[str] = None
      description: str = ""

  def build_crud_routes(ctx) -> list[RouteSpec]:
      """Walk the IR, produce all RouteSpecs."""
      routes = []
      for cs in ctx.ir["content"]:
          path = f"/api/v1/{cs['name']['snake']}"
          routes.append(RouteSpec(
              method="GET", path=path,
              handler=make_list_handler(cs, ctx),
              required_scope=cs.get("read_scope"),
              description=f"List {cs['name']['display']}",
          ))
          ...
      return routes
  ```

  Adapter consumes the list:

  ```python
  for spec in route_specs:
      app.add_api_route(spec.path, _wrap(spec), methods=[spec.method])
  ```

**My recommendation: option b (declarative list).** Three reasons:

1. **Inspectable.** The conformance suite can verify a runtime
   exposes the right routes by inspecting the list — no need to
   crawl the framework's internal router. Builds toward the
   slice-7.5 core conformance pack.
2. **Adapter-friendly.** A single `for spec in routes:` loop binds
   any list to any framework. No framework-specific decorator
   semantics to translate.
3. **Better for non-decorator hosts.** Hosts that aren't
   decorator-shaped (a serverless dispatcher that maps URL patterns
   to handlers via config) get the route table as data, not as a
   chain of decorator side effects.

## Q3 — `Principal` extraction: middleware vs. dependency vs. handler-side?

Today the runtime extracts the principal from cookies via
`ctx.get_current_user(request)` inside each route handler. This
becomes a dependency-injection-shaped problem in the new design.

- **Option a — adapter middleware.** The adapter (FastAPI side)
  reads cookies, calls `ctx.identity_provider.principal_for(...)`,
  attaches the result to `TerminRequest.principal` before invoking
  the handler. Handler reads `request.principal` directly.

- **Option b — termin-core helper invoked by handler.**
  `request.principal` is unset on arrival; handler calls
  `await get_principal(request, ctx)` which reads the request's
  headers/cookies and consults the identity provider. Adapter does
  no cookie parsing.

- **Option c — separate Authentication call.** termin-core defines
  an `Authenticator` Protocol; the adapter constructs one and
  passes it alongside ctx. Handler calls
  `principal = await authenticator.authenticate(request)`. Most
  flexible, most boilerplate.

**My recommendation: option a (adapter middleware).** The adapter
already speaks the framework's auth/cookie machinery; running
authentication once at the boundary, before the handler runs, is
the standard pattern (FastAPI middleware, Starlette middleware,
ASGI middleware). `TerminRequest.principal` becomes a documented
field that handlers can rely on. Anonymous principal is the default
when no auth context is found.

**Caveat:** WebSocket connections also need principal extraction,
on connect. Same pattern applies — adapter populates
`TerminWebSocket.principal` before yielding to the handler.

## Q4 — `TerminWebSocket` Protocol shape

Slice 7.2.f extracts the WS dispatch fully (per Q3 of the Phase 7
design). The Protocol shape:

- **Option a — minimal Protocol.**

  ```python
  class TerminWebSocket(Protocol):
      principal: Optional[Principal]
      async def accept(self) -> None: ...
      async def send_json(self, data: Any) -> None: ...
      async def send_bytes(self, data: bytes) -> None: ...
      async def receive_json(self) -> Any: ...
      async def receive_text(self) -> str: ...
      async def close(self, code: int = 1000) -> None: ...
  ```

  Topic dispatch (the `compute.stream.<id>.<field>` channel router,
  the `content.<source>` content stream) lives in termin-core.
  Connection management (the per-connection subscription state
  table) lives in termin-core. Adapter only supplies the bytes-on-the-wire.

- **Option b — Protocol + connection registry in core, dispatch in
  core, but the adapter owns the actual fanout.** Adapter
  implements its own connection registry and uses our dispatcher
  to look up which subscriptions match a published event. Less
  state in core but split responsibility.

**My recommendation: option a.** The whole point of Q3 (full
extraction) is encoding the hard-won WebSocket lifecycle
correctness once in core. Splitting the registry across core and
adapter reintroduces the seam Phase 7 is collapsing.

## Q5 — Slicing within 7.2

What lands in which sub-slice?

- **Option a — single big 7.2 slice.** All five Qs land in one
  drop. High blast radius.
- **Option b — three sub-slices:**
  - **7.2.d** — types only: `TerminRequest`, `TerminResponse`,
    `TerminWebSocket`, `RouteSpec`. Smoke tests for each. No
    handler extraction yet.
  - **7.2.e** — extract the CRUD handlers (list, get, create,
    update, delete, inline edit, transition). FastAPI adapter
    bridges to the new types. Routes still mounted via FastAPI.
  - **7.2.f** — extract WebSocket dispatch + connection registry.
    `channel_ws.py` and `websocket_manager.py` move; FastAPI
    WebSocket adapter is a thin wrapper.

**My recommendation: option b.** Each sub-slice is independently
revertable and independently green-suite-able. 7.2.d alone gives
me a working integration point I can validate before touching the
1063 lines of routes.py.

## What I'd do next if Q1–Q5 are answered

Working autonomously through the night:

1. Land 7.2.d (types + tests). 1–2 hours.
2. Start 7.2.e: extract one easy route first (e.g., `GET /api/v1/{content}` list endpoint), prove the FastAPI-adapter bridge works end-to-end, then walk through the remaining ~10 CRUD handlers. 4–6 hours.
3. Stop before 7.2.f if the night runs out — WebSocket extraction is the riskiest piece and benefits from morning eyes.

If JL has different recommendations on any of Q1–Q5, the plan
adjusts. Each Q has a self-contained answer; partial sign-off
(say, just Q1+Q2) lets me start 7.2.d while the remaining Qs
mature.

## Decisions table

| ID | Question | Decision |
|---|---|---|
| Q1 | TerminRequest shape — thin ASGI vs. rich convenience | **rich** (TerminRequest dataclass) |
| Q2 | Route dispatch — decorator vs. declarative list | **declarative `RouteSpec` list** |
| Q3 | Principal extraction — middleware vs. dep vs. handler | **adapter middleware fills `request.principal`** |
| Q4 | TerminWebSocket Protocol — minimal vs. split-registry | **minimal Protocol; full state in core** |
| Q5 | Slicing — single 7.2 vs. 7.2.d / .e / .f | sub-slices (defaulted, no debate) |

## 10. Slice 7.2.a / .b / .c / .d — closure

| Sub-slice | Status | What landed |
|---|---|---|
| 7.2.a | ✅ landed | `termin_core.errors.exceptions` — TerminRuntimeError base + 5 subclasses (BadRequest, Scope, NotFound, Conflict, Validation) with class-level status_code mapping. 25 tests. |
| 7.2.b | ✅ landed | `termin_runtime/validation.py` migrated to `termin_core.validation`. Raises TerminValidationError. FastAPI exception handler in app.py translates to 422 with the same response body conformance saw before. |
| 7.2.c | ✅ landed | `termin_runtime/state.py` migrated to `termin_core.state.machine`. Raises Termin{BadRequest,Scope,NotFound,Conflict}Error. transitions.py stays as FastAPI adapter wiring (extracts in slice 7.3). |
| 7.2.d | ✅ landed | `termin_core.routing` package — TerminRequest / TerminResponse / TerminWebSocket Protocol / RouteSpec / WebSocketRouteSpec. 29 tests. |

**Suites:** termin-compiler 2545 passing on Windows; termin-conformance
915 passing on Windows reference adapter; termin-core 57/57 passing
(includes the framework-free guard).

## 12. Slice 7.2.e — closure (2026-04-30 night, autonomous mode)

**Status: landed.** All six CRUD-class handlers extracted to
`termin_core.routing.crud` and the FastAPI routes in
`termin_runtime/routes.py` are now thin bridges that delegate
through `termin_runtime/fastapi_adapter.py`.

| Handler | termin-core function | FastAPI route delegates |
|---|---|---|
| GET `/api/v1/{content}` | `list_content_handler` | `_make_list_route` |
| GET `/api/v1/{content}/{key}` | `get_content_handler` | `_make_get_route` |
| POST `/api/v1/{content}` | `create_content_handler` | `_make_create_route` |
| PUT `/api/v1/{content}/{key}` | `update_content_handler` | `_make_update_route` |
| DELETE `/api/v1/{content}/{key}` | `delete_content_handler` | `_make_delete_route` |
| POST `/api/v1/{content}/{key}/_transition/{m}/{t}` | `transition_content_handler` | `_make_transition_route` |

**Bridge pattern.** Each FastAPI route became a ~10-line shell:

```python
@app.<verb>(path, dependencies=deps)
async def fastapi_route(request: Request, _cr=cr):
    user = ctx.get_current_user(request)
    auth = make_auth_context(user)
    termin_req = await to_termin_request(
        request,
        path_params={"content": _cr, ...},
        auth=auth,
        legacy_user_dict=user,
    )
    response = await <core_handler>(termin_req, ctx)
    return to_fastapi_response(response)
```

Per-content-type registration data (lookup_column, row_filter,
owner_field, state_machine_info) is stashed on ctx via
`ctx._<name>_for_content` dicts and surfaced as
`ctx.<name>_for(content_ref)` callables. Pure-rule helpers the
handler still needs (state-column seeding, content-event
publishing, IR event handlers) are exposed via ctx hooks. Slice
7.5 may move some into core proper.

**Transitional carrier — `TerminRequest.legacy_user_dict`.** A
real bug surfaced: `evaluate_field_defaults` reads the v0.9 user
dict's `["User"]` key for the PascalCase-keyed CEL shape
(`User.Username`, `User.Role`, etc.) that IR-declared default_expr
expressions reference. The first version of `create_content_handler`
synthesized a wrong-shape dict (`id`/`display_name`/`scopes`)
which caused 67 conformance failures. Fix: adapter middleware
stamps `request.legacy_user_dict` with the original v0.9 user
dict so the CEL surface keeps working unchanged. Deletes in slice
7.5 once the CEL-resolved User shape moves into AuthContext or
the CEL surface is rewritten.

**Suites at slice close:**

- compiler 2545 / Windows
- conformance 915 / 0 failed / Windows reference adapter
- termin-core 80 / 80 / Windows

**Cumulative termin-core surface after 7.2.e:**

```
termin_core/
  ir/             types, serialize
  providers/      contracts, registry, binding, deploy_config,
                  identity_contract, storage_contract,
                  compute_contract, channel_contract,
                  presentation_contract
  expression/     cel, predicate
  confidentiality/ redaction
  identity/       (Principal lives in providers; AuthContext lives
                   in routing/)
  errors/         router (TerminError envelope, TerminAtor),
                  exceptions (TerminRuntimeError + 5 subclasses)
  validation/     dependents
  state/          machine
  routing/        request (TerminRequest, TerminResponse),
                  websocket (TerminWebSocket Protocol),
                  auth (AuthContext),
                  route_specs (RouteSpec, WebSocketRouteSpec),
                  crud (6 CRUD handlers)
```

Remaining handlers in `termin_runtime/routes.py` that haven't
moved (each its own slice when scheduled):

- `register_reflection_routes` — pure runtime introspection,
  stays as adapter code; moves to termin-server in slice 7.3.
- `register_compute_endpoint` — POST /api/v1/compute/<name>/trigger
  invokes the compute pipeline. Business-logic-heavy, candidate
  for extraction in a later 7.2.x slice.
- `register_sse_routes` — server-sent events. Adapter-coupled
  (StreamingResponse). Moves with termin-server.
- `register_page_routes` — SSR page rendering. Presentation-layer,
  Jinja2/Tailwind-coupled. Moves with termin-server.
- `register_channel_routes` — webhook receivers, channel send.
  Channel-layer, intertwined with channel_ws.py — slice 7.2.f.

## 11. Slice 7.2.e — blocking decision (Principal flow)

The CRUD handler extraction needs a decision on how the principal
information flows through `TerminRequest`. Today every CRUD route
in `termin_runtime/routes.py` calls
``ctx.get_current_user(request)`` which returns a *user dict* of
shape::

    {"role": "warehouse manager",
     "scopes": ["orders.read", "orders.write", ...],
     "the_user": <Principal-shaped dict: id, display_name, claims, ...>}

`Principal` (from `termin_core.providers.identity_contract`) does
*not* carry scopes — scopes are a runtime-layer concept derived
from role mapping at request time. So `TerminRequest.principal:
Optional[Principal]` (per Q3=a) carries identity, not scopes.

Three ways to thread scopes onto the request, each with a
different migration path for the ~10 CRUD handlers:

- **Option a (full normalization, biggest cut):** Extend
  `TerminRequest` with a `scopes: tuple[str, ...]` field, populated
  by the adapter middleware from `ctx.identity_provider.roles_for(
  principal)` + role-to-scopes mapping. Every handler uses
  `request.principal` (Principal) and `request.scopes` (tuple).
  The legacy `ctx.get_current_user(request)` deletes. Cleanest end
  state but every handler refactors at once.

- **Option b (interim, smallest cut):** Adapter populates
  `request.principal` AND keeps `ctx.get_current_user(request)`
  working. Handlers being migrated to take `TerminRequest` get an
  additional kwarg `user: dict` containing the legacy shape.
  Migration is incremental — one handler at a time. The legacy
  user dict is a transitional concept that drops in slice 7.5.

- **Option c (split into AuthContext value type):** termin-core
  defines a new `AuthContext` value type carrying
  `(principal, scopes, role_name)` — not just the principal. The
  adapter middleware computes it once and sets
  `request.auth: AuthContext`. Cleaner data model than option b's
  loose dict, less aggressive than option a.

**Recommendation: option c.** Splits the concerns cleanly
(Principal is *who*, AuthContext is *who-plus-what-they-can-do-here*),
keeps the migration incremental, and the new value type is the
right end-state shape — it doesn't decay to "transitional" the
way option b's loose dict does. Estimated cost: define
`AuthContext` (~20 lines), migrate handlers one at a time, drop
`ctx.get_current_user` in slice 7.5.

**Why this needs JL:** the choice ripples through every handler.
Option a is one big PR with high merge-conflict risk; option b
ships fast but leaves a transitional shape; option c sits between
and adds a new value type to the contract surface. JL's call.
