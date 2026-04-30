# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""CRUD route registration, reflection endpoints, channel endpoints, webhooks.

Auto-CRUD from IR RouteSpec (D-11). Reflection API. Channel action/send
endpoints. Inbound webhook handlers. SSE streams.
"""

import json
import sqlite3

from fastapi import Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pathlib import Path

from .context import RuntimeContext
from .storage import (
    get_db, create_record, get_record, update_record, delete_record,
    list_records, find_by_field,
)
from .providers import (
    Eq, And, OrderBy, QueryOptions, CascadeMode,
)
from .state import do_state_transition
from .confidentiality import redact_record, redact_records, check_write_access
from .boundaries import check_boundary_identity
from .validation import (
    validate_dependent_values, validate_enum_constraints,
    validate_min_max_constraints, evaluate_field_defaults, strip_unknown_fields,
)
from .compute_runner import redact_audit_traces
from .preferences import (
    InvalidThemeValueError,
    VALID_THEMES,
    ensure_preferences_table,
    get_theme_preference,
    set_theme_preference,
)
from .presentation_bundles import (
    register_presentation_bundle_endpoint,
    register_provider_bundle_route,
)
from .bootstrap import (
    register_page_data_endpoint,
    register_shell_endpoint,
)


# v0.9 Phase 2: cross-cutting helpers that wrap ctx.storage with the
# event-publishing + error-routing concerns the legacy storage.py
# entrypoints used to bundle in. The provider stays pure (BRD §6.2
# "Provider's job is small"); the runtime owns the workflow.

async def _publish_content_event(ctx, kind: str, content_name: str, record: dict):
    """Publish a {created|updated|deleted} event for a content row."""
    if ctx.event_bus is None:
        return
    payload = {
        "type": f"{content_name}_{kind}",
        "channel_id": f"content.{content_name}.{kind}",
        "content_name": content_name,
    }
    if kind == "deleted":
        payload["record_id"] = record.get("id")
    else:
        payload["data"] = record
    await ctx.event_bus.publish(payload)


def _route_terminator(ctx, content_name: str, exc: Exception) -> None:
    """Route a storage exception through TerminAtor as a validation
    error. No-op if no terminator is configured. Exception is
    re-raised by the caller — TerminAtor records, doesn't intercept."""
    if ctx.terminator is None:
        return
    from .errors import TerminError
    ctx.terminator.route(TerminError(
        source=content_name, kind="validation", message=str(exc),
    ))


def _seed_state_columns(body: dict, sm_info, *, strip_existing: bool = False) -> dict:
    """Apply state-machine initial values to a creation body.

    If strip_existing is True (CREATE-route gate), state columns are
    removed entirely so the SQL DEFAULT applies — preserves the
    transition-rules-only-via-transitions invariant.
    Otherwise (e.g. internal seeding), a missing/empty state column
    is filled with the machine's initial state.
    """
    if not sm_info:
        return body
    out = dict(body)
    if isinstance(sm_info, list):
        state_cols = {sm["machine_name"] for sm in sm_info if sm.get("machine_name")}
        if strip_existing:
            return {k: v for k, v in out.items() if k not in state_cols}
        for sm in sm_info:
            col = sm.get("machine_name", "")
            if col and not out.get(col):
                out[col] = sm.get("initial", "")
    return out


def register_crud_routes(app, ctx: RuntimeContext):
    """Register all CRUD routes from the IR route specs."""

    # Build a per-content ownership-field lookup so CREATE routes can
    # stamp the ownership field with `the user.id` before insert
    # (Phase 6a.5 / BRD #3 §3.5). The IR's ContentSchema.ownership.field
    # is the snake-case column name; None when no ownership declared.
    ownership_field_for_content: dict[str, str | None] = {}
    for cs in ctx.ir.get("content", []):
        own = cs.get("ownership")
        if own and own.get("field"):
            ownership_field_for_content[cs.get("name", {}).get("snake", "")] = own["field"]

    for route in ctx.ir.get("routes", []):
        content_ref = route.get("content_ref", "")
        method = route.get("method", "GET")
        path = route.get("path", "")
        kind = route.get("kind", "LIST")
        scope = route.get("scope") or route.get("required_scope")
        lookup_col = route.get("lookup_column", "id")
        target_state = route.get("target_state")
        machine_name = route.get("machine_name")
        # v0.9 Phase 6a.5: row_filter from RouteSpec drives ownership-
        # restricted routes. Shape: {"kind": "ownership", "field":
        # "<snake>"} or None.
        row_filter = route.get("row_filter")
        owner_field_for_create = ownership_field_for_content.get(content_ref)

        if kind == "LIST":
            _make_list_route(app, ctx, path, content_ref, scope, row_filter)
        elif kind == "CREATE":
            _make_create_route(app, ctx, path, content_ref, scope,
                               ctx.sm_lookup.get(content_ref, []),
                               owner_field_for_create)
        elif kind == "GET_ONE":
            _make_get_route(app, ctx, path, content_ref, scope, lookup_col, row_filter)
        elif kind == "UPDATE":
            _make_update_route(app, ctx, path, content_ref, scope, lookup_col, row_filter)
        elif kind == "DELETE":
            _make_delete_route(app, ctx, path, content_ref, scope, lookup_col, row_filter)
        elif kind == "TRANSITION":
            _make_transition_route(app, ctx, path, content_ref, scope,
                                   lookup_col, target_state, machine_name)


def _make_list_route(app, ctx, path, cr, sc, row_filter=None):
    """Slice 7.2.e of Phase 7 (2026-04-30): list-content handler
    extracted to ``termin_core.routing.crud.list_content_handler``.
    The FastAPI route here is now a thin bridge that wraps the
    request, delegates to the pure handler, and unwraps the
    response.

    Boundary identity check, redaction, and audit-trace redaction
    are runtime-internal concerns; the handler reads them off ctx
    via thin shims. Slice 7.5 may move boundary checks into core
    too, at which point the ctx hooks here become unnecessary.
    """
    from termin_core.routing import list_content_handler
    from .fastapi_adapter import (
        make_auth_context,
        to_fastapi_response,
        to_termin_request,
    )
    from .compute_runner import redact_audit_traces as _redact_audit_traces

    deps = [Depends(ctx.require_scope(sc))] if sc else []

    # Stash row_filter and ctx-side runtime concerns on ctx itself
    # so the pure handler can read them. Closure-style binding —
    # the handler doesn't see the route registration's scope.
    if not hasattr(ctx, "_row_filter_for_content"):
        ctx._row_filter_for_content = {}
    if row_filter:
        ctx._row_filter_for_content[cr] = row_filter

    if not hasattr(ctx, "row_filter_for"):
        ctx.row_filter_for = lambda cn: ctx._row_filter_for_content.get(cn)
    if not hasattr(ctx, "_check_boundary_identity"):
        ctx._check_boundary_identity = lambda cn, scopes: check_boundary_identity(
            ctx.boundary_identity_scopes, ctx.boundary_for_content,
            cn, scopes,
        )
    if not hasattr(ctx, "redact_audit_traces"):
        async def _redact(records, content_ref, scopes):
            return await _redact_audit_traces(ctx, records, content_ref, scopes)
        ctx.redact_audit_traces = _redact

    @app.get(path, dependencies=deps)
    async def list_route(request: Request, _cr=cr):
        user = ctx.get_current_user(request)
        auth = make_auth_context(user)
        termin_req = await to_termin_request(
            request,
            path_params={"content": _cr},
            auth=auth,
            legacy_user_dict=user,
        )
        response = await list_content_handler(termin_req, ctx)
        return to_fastapi_response(response)


def _make_create_route(app, ctx, path, cr, sc, sm_info, owner_field=None):
    """Slice 7.2.e of Phase 7 (2026-04-30): create handler extracted
    to ``termin_core.routing.crud.create_content_handler``. The
    FastAPI route is now a thin bridge that delegates to the pure
    handler. State-machine seeding, event publishing, and IR event
    handlers are stashed on ctx as runtime-internal hooks the pure
    handler reads.
    """
    from termin_core.routing import create_content_handler
    from .fastapi_adapter import (
        make_auth_context,
        to_fastapi_response,
        to_termin_request,
    )

    deps = [Depends(ctx.require_scope(sc))] if sc else []

    # Per-content-type registration: state-machine info, owner field.
    if not hasattr(ctx, "_state_machine_info_for_content"):
        ctx._state_machine_info_for_content = {}
    ctx._state_machine_info_for_content[cr] = sm_info
    if not hasattr(ctx, "state_machine_info_for"):
        ctx.state_machine_info_for = lambda cn: ctx._state_machine_info_for_content.get(cn)

    if not hasattr(ctx, "_owner_field_for_content"):
        ctx._owner_field_for_content = {}
    if owner_field:
        ctx._owner_field_for_content[cr] = owner_field
    if not hasattr(ctx, "owner_field_for"):
        ctx.owner_field_for = lambda cn: ctx._owner_field_for_content.get(cn)

    # Pure-rule helpers that haven't moved to termin-core yet —
    # exposed via ctx so the handler can call them. Slice 7.5 may
    # move state-column seeding into core proper.
    if not hasattr(ctx, "seed_state_columns"):
        ctx.seed_state_columns = _seed_state_columns

    if not hasattr(ctx, "publish_content_event"):
        async def _publish(kind, content_name, record):
            await _publish_content_event(ctx, kind, content_name, record)
        ctx.publish_content_event = _publish

    if not hasattr(ctx, "route_terminator_validation"):
        ctx.route_terminator_validation = lambda cn, exc: _route_terminator(ctx, cn, exc)

    if not hasattr(ctx, "run_event_handlers_for_content"):
        async def _run_evt(content_name, kind, record):
            db = await get_db(ctx.db_path)
            try:
                await ctx.run_event_handlers(db, content_name, kind, record)
            finally:
                await db.close()
        ctx.run_event_handlers_for_content = _run_evt

    @app.post(path, status_code=201, dependencies=deps)
    async def create_route(request: Request, _cr=cr):
        user = ctx.get_current_user(request)
        auth = make_auth_context(user)
        termin_req = await to_termin_request(
            request,
            path_params={"content": _cr},
            auth=auth,
            legacy_user_dict=user,
        )
        response = await create_content_handler(termin_req, ctx)
        return to_fastapi_response(response)


def _make_get_route(app, ctx, path, cr, sc, lc, row_filter=None):
    """Slice 7.2.e of Phase 7 (2026-04-30): get-by-id handler
    extracted to ``termin_core.routing.crud.get_content_handler``.
    """
    from termin_core.routing import get_content_handler
    from .fastapi_adapter import (
        make_auth_context,
        to_fastapi_response,
        to_termin_request,
    )

    deps = [Depends(ctx.require_scope(sc))] if sc else []

    # Stash lookup-column + row_filter on ctx so the pure handler
    # can read them without per-route closure capture.
    if not hasattr(ctx, "_lookup_column_for_content"):
        ctx._lookup_column_for_content = {}
    ctx._lookup_column_for_content[cr] = lc
    if not hasattr(ctx, "lookup_column_for"):
        ctx.lookup_column_for = lambda cn: ctx._lookup_column_for_content.get(cn, "id")
    if not hasattr(ctx, "_row_filter_for_content"):
        ctx._row_filter_for_content = {}
    if row_filter:
        ctx._row_filter_for_content[cr] = row_filter
    if not hasattr(ctx, "row_filter_for"):
        ctx.row_filter_for = lambda cn: ctx._row_filter_for_content.get(cn)

    @app.get(path, dependencies=deps)
    async def get_route(request: Request, _cr=cr, _lc=lc):
        # FastAPI extracts the lookup-key path param under whatever
        # name the route declared — typically {id} or {sku}. The
        # core handler reads it under "key" so the bridge name-maps.
        key_val = list(request.path_params.values())[0] if request.path_params else None
        user = ctx.get_current_user(request)
        auth = make_auth_context(user)
        termin_req = await to_termin_request(
            request,
            path_params={"content": _cr, "key": key_val},
            auth=auth,
            legacy_user_dict=user,
        )
        response = await get_content_handler(termin_req, ctx)
        return to_fastapi_response(response)


def _make_update_route(app, ctx, path, cr, sc, lc, row_filter=None):
    """Slice 7.2.e of Phase 7 (2026-04-30): update handler extracted
    to ``termin_core.routing.crud.update_content_handler``. The
    FastAPI route is a thin bridge.
    """
    from termin_core.routing import update_content_handler
    from .fastapi_adapter import (
        make_auth_context,
        to_fastapi_response,
        to_termin_request,
    )

    deps = [Depends(ctx.require_scope(sc))] if sc else []

    # Per-content-type: lookup column + row_filter already stashed
    # by _make_get_route on the same content_ref. Be defensive in
    # case create runs in isolation.
    if not hasattr(ctx, "_lookup_column_for_content"):
        ctx._lookup_column_for_content = {}
    ctx._lookup_column_for_content[cr] = lc
    if not hasattr(ctx, "lookup_column_for"):
        ctx.lookup_column_for = lambda cn: ctx._lookup_column_for_content.get(cn, "id")
    if not hasattr(ctx, "_row_filter_for_content"):
        ctx._row_filter_for_content = {}
    if row_filter:
        ctx._row_filter_for_content[cr] = row_filter
    if not hasattr(ctx, "row_filter_for"):
        ctx.row_filter_for = lambda cn: ctx._row_filter_for_content.get(cn)

    @app.put(path, dependencies=deps)
    async def update_route(request: Request, _cr=cr):
        key_val = list(request.path_params.values())[0] if request.path_params else None
        user = ctx.get_current_user(request)
        auth = make_auth_context(user)
        termin_req = await to_termin_request(
            request,
            path_params={"content": _cr, "key": key_val},
            auth=auth,
            legacy_user_dict=user,
        )
        response = await update_content_handler(termin_req, ctx)
        return to_fastapi_response(response)


def _make_delete_route(app, ctx, path, cr, sc, lc, row_filter=None):
    """Slice 7.2.e of Phase 7 (2026-04-30): delete handler extracted
    to ``termin_core.routing.crud.delete_content_handler``. The
    FastAPI route is a thin bridge.
    """
    from termin_core.routing import delete_content_handler
    from .fastapi_adapter import (
        make_auth_context,
        to_fastapi_response,
        to_termin_request,
    )

    deps = [Depends(ctx.require_scope(sc))] if sc else []

    if not hasattr(ctx, "_lookup_column_for_content"):
        ctx._lookup_column_for_content = {}
    ctx._lookup_column_for_content[cr] = lc
    if not hasattr(ctx, "lookup_column_for"):
        ctx.lookup_column_for = lambda cn: ctx._lookup_column_for_content.get(cn, "id")
    if not hasattr(ctx, "_row_filter_for_content"):
        ctx._row_filter_for_content = {}
    if row_filter:
        ctx._row_filter_for_content[cr] = row_filter
    if not hasattr(ctx, "row_filter_for"):
        ctx.row_filter_for = lambda cn: ctx._row_filter_for_content.get(cn)

    @app.delete(path, dependencies=deps)
    async def delete_route(request: Request, _cr=cr):
        key_val = list(request.path_params.values())[0] if request.path_params else None
        user = ctx.get_current_user(request)
        auth = make_auth_context(user)
        termin_req = await to_termin_request(
            request,
            path_params={"content": _cr, "key": key_val},
            auth=auth,
            legacy_user_dict=user,
        )
        response = await delete_content_handler(termin_req, ctx)
        return to_fastapi_response(response)


def _make_transition_route(app, ctx, path, cr, sc, lc, ts, mn=None):
    """Slice 7.2.e of Phase 7 (2026-04-30): per-machine, per-target
    state-transition route. Body extracted to
    ``termin_core.routing.crud.transition_content_handler``.

    ``mn`` is the machine_name (snake_case) the route drives.
    Required in v0.9 — every transition route addresses one machine
    on one content. Callers from ``register_crud_routes`` always
    pass it; older internal callers (none currently) would not, in
    which case the core handler falls back to the first state
    machine on the content for backward compatibility.
    """
    from termin_core.routing import transition_content_handler
    from .fastapi_adapter import (
        make_auth_context,
        to_fastapi_response,
        to_termin_request,
    )

    deps = [Depends(ctx.require_scope(sc))] if sc else []

    if not hasattr(ctx, "_lookup_column_for_content"):
        ctx._lookup_column_for_content = {}
    ctx._lookup_column_for_content[cr] = lc
    if not hasattr(ctx, "lookup_column_for"):
        ctx.lookup_column_for = lambda cn: ctx._lookup_column_for_content.get(cn, "id")

    @app.post(path, dependencies=deps)
    async def transition_route(request: Request, _cr=cr, _ts=ts, _mn=mn):
        key_val = list(request.path_params.values())[0] if request.path_params else None
        user = ctx.get_current_user(request)
        auth = make_auth_context(user)
        termin_req = await to_termin_request(
            request,
            path_params={
                "content": _cr,
                "key": key_val,
                "machine": _mn,
                "target": _ts,
            },
            auth=auth,
            legacy_user_dict=user,
        )
        response = await transition_content_handler(termin_req, ctx)
        return to_fastapi_response(response)


def register_reflection_routes(app, ctx: RuntimeContext):
    """Register reflection, error, and event API endpoints."""

    @app.get("/api/reflect")
    async def api_reflect():
        return json.loads(ctx.ir_json)

    @app.get("/api/reflect/content")
    async def api_reflect_content():
        return ctx.reflection.content_schemas()

    @app.get("/api/reflect/compute")
    async def api_reflect_compute():
        return ctx.reflection.compute_functions()

    @app.get("/api/reflect/roles")
    async def api_reflect_roles():
        return ctx.reflection.roles()

    @app.get("/api/reflect/roles/{role_name}")
    async def api_reflect_role(role_name: str):
        role = ctx.reflection.role(role_name)
        if not role:
            raise HTTPException(status_code=404, detail=f"Role '{role_name}' not found")
        return role

    @app.get("/api/reflect/channels")
    async def api_reflect_channels():
        return ctx.channel_dispatcher.get_full_status()

    @app.get("/api/reflect/channels/{channel_name}")
    async def api_reflect_channel(channel_name: str):
        spec = ctx.channel_dispatcher.get_spec(channel_name)
        if not spec:
            raise HTTPException(status_code=404, detail=f"Channel '{channel_name}' not found")
        display = spec["name"]["display"]
        config = ctx.channel_dispatcher.get_config(channel_name)
        return {
            "name": display,
            "direction": spec.get("direction", ""),
            "delivery": spec.get("delivery", ""),
            "carries": spec.get("carries_content", ""),
            "actions": [a["name"]["display"] for a in spec.get("actions", [])],
            "configured": ctx.channel_dispatcher.is_configured(channel_name),
            "state": ctx.channel_dispatcher.get_connection_state(channel_name),
            "protocol": config.protocol if config else "none",
            "metrics": ctx.channel_dispatcher.get_metrics(channel_name),
        }

    @app.get("/api/errors")
    async def api_errors():
        return ctx.terminator.get_error_log()

    @app.get("/api/events")
    async def api_events(level: str = Query(default=None)):
        log = ctx.event_bus.get_event_log()
        if level:
            order = {"TRACE": 0, "DEBUG": 1, "INFO": 2, "WARN": 3, "ERROR": 4}
            min_l = order.get(level.upper(), 0)
            log = [e for e in log if order.get(e.get("log_level", "INFO"), 2) >= min_l]
        return log


def register_channel_routes(app, ctx: RuntimeContext):
    """Register channel action/send endpoints and inbound webhook handlers."""

    @app.post("/api/v1/channels/{channel_name}/actions/{action_name}")
    async def invoke_channel_action(channel_name: str, action_name: str, request: Request):
        user = ctx.get_current_user(request)
        user_scopes = set(user.get("scopes", []))

        spec = ctx.channel_dispatcher.get_spec(channel_name)
        if not spec:
            raise HTTPException(status_code=404,
                                detail=f"Channel '{channel_name}' not found")

        action_spec = ctx.channel_dispatcher.get_action_spec(channel_name, action_name)
        if not action_spec:
            raise HTTPException(status_code=404,
                                detail=f"Action '{action_name}' not found on channel '{channel_name}'")

        try:
            body = await request.json()
        except Exception:
            body = {}

        from .channels import ChannelScopeError, ChannelValidationError, ChannelError
        try:
            result = await ctx.channel_dispatcher.channel_invoke(
                channel_name, action_name, body, user_scopes=user_scopes)
            return result
        except ChannelScopeError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ChannelValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except ChannelError as e:
            raise HTTPException(status_code=502, detail=str(e))

    @app.post("/api/v1/channels/{channel_name}/send")
    async def channel_send_endpoint(channel_name: str, request: Request):
        user = ctx.get_current_user(request)
        user_scopes = set(user.get("scopes", []))

        spec = ctx.channel_dispatcher.get_spec(channel_name)
        if not spec:
            raise HTTPException(status_code=404,
                                detail=f"Channel '{channel_name}' not found")

        try:
            body = await request.json()
        except Exception:
            body = {}

        from .channels import ChannelScopeError, ChannelError
        try:
            result = await ctx.channel_dispatcher.channel_send(
                channel_name, body, user_scopes=user_scopes)
            return result
        except ChannelScopeError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ChannelError as e:
            raise HTTPException(status_code=502, detail=str(e))

    # Inbound webhook handlers
    for ch in ctx.ir.get("channels", []):
        ch_direction = ch.get("direction", "")
        if ch_direction not in ("INBOUND", "BIDIRECTIONAL"):
            continue
        ch_display = ch["name"]["display"]
        ch_snake = ch["name"]["snake"]
        ch_carries = ch.get("carries_content", "")
        if not ch_carries:
            continue

        webhook_path = f"/webhooks/{ch_snake}"

        def _make_webhook(ch_name=ch_display, ch_content=ch_carries, ch_spec=ch):
            @app.post(webhook_path, name=f"webhook_{ch_snake}")
            async def webhook_receive(request: Request):
                user = ctx.get_current_user(request)
                user_scopes = set(user.get("scopes", []))
                for req in ch_spec.get("requirements", []):
                    if req["direction"] == "send" and req["scope"] not in user_scopes:
                        raise HTTPException(
                            status_code=403,
                            detail=f"Scope '{req['scope']}' required to send to channel '{ch_name}'")

                try:
                    body = await request.json()
                except Exception:
                    raise HTTPException(status_code=400, detail="Invalid JSON payload")

                schema = ctx.content_lookup.get(ch_content)
                if not schema:
                    raise HTTPException(status_code=500,
                                        detail=f"Content '{ch_content}' not found")

                known_cols = set()
                for f in schema.get("fields", []):
                    fname = f.get("name", "")
                    if isinstance(fname, dict):
                        known_cols.add(fname.get("snake", ""))
                    else:
                        known_cols.add(str(fname))
                record_data = {k: v for k, v in body.items() if k in known_cols}

                if not record_data:
                    raise HTTPException(status_code=422, detail="No valid fields in payload")

                db = await get_db(ctx.db_path)
                try:
                    record = await create_record(db, ch_content, record_data,
                                                 ctx.sm_lookup.get(ch_content, []))
                    await ctx.run_event_handlers(db, ch_content, "created", record)

                    ctx.channel_dispatcher._metrics.get(ch_name, {})["received"] = \
                        ctx.channel_dispatcher._metrics.get(ch_name, {}).get("received", 0) + 1

                    await ctx.event_bus.publish({
                        "channel_id": f"content.{ch_content}.created",
                        "data": record,
                    })

                    print(f"[Termin] Webhook '{ch_name}': created {ch_content} record (id={record.get('id', '?')})")
                    return {"ok": True, "id": record.get("id"), "channel": ch_name}
                finally:
                    await db.close()

        _make_webhook()
        print(f"[Termin] Registered webhook: POST {webhook_path} -> {ch_carries}")


def register_sse_routes(app, ctx: RuntimeContext):
    """Register SSE stream endpoints."""
    for stream in ctx.ir.get("streams", []):
        def make_sse(p):
            @app.get(p)
            async def sse_stream(request: Request, _p=p):
                async def generate():
                    q = ctx.event_bus.subscribe()
                    try:
                        while True:
                            event = await q.get()
                            yield f"data: {json.dumps(event)}\n\n"
                    except Exception:
                        ctx.event_bus.unsubscribe(q)
                return StreamingResponse(generate(), media_type="text/event-stream")
        make_sse(stream["path"])


def register_runtime_endpoints(app, ctx: RuntimeContext):
    """Register runtime infrastructure endpoints (registry, bootstrap, termin.js)."""

    @app.get("/runtime/registry")
    async def runtime_registry(request: Request):
        host = request.headers.get("host", "localhost:8000")
        scheme = "wss" if request.url.scheme == "https" else "ws"
        http_scheme = request.url.scheme or "http"
        boundaries = {}
        for bnd in ctx.ir.get("boundaries", []):
            name = bnd.get("name", {}).get("snake", "unknown")
            boundaries[name] = {
                "location": "local",
                "channels": {
                    "realtime": f"{scheme}://{host}/runtime/ws",
                    "reliable": f"{http_scheme}://{host}/runtime/api",
                },
            }
        boundaries["presentation"] = {
            "location": "client",
            "channels": {
                "realtime": f"{scheme}://{host}/runtime/ws",
                "reliable": f"{http_scheme}://{host}/runtime/api",
            },
        }
        return {
            "runtime_version": "0.9.0",
            "application": ctx.ir.get("name", "Termin App"),
            "boundaries": boundaries,
            "protocols": {"realtime": "websocket", "reliable": "rest"},
        }

    @app.get("/runtime/bootstrap")
    async def runtime_bootstrap(request: Request):
        user = ctx.get_current_user(request)
        role = user["role"]
        user_pages = [p for p in ctx.ir.get("pages", [])
                      if p["role"] == role or p["role"].lower() == role.lower()]
        client_computes = []
        for comp in ctx.ir.get("computes", []):
            if comp.get("body_lines"):
                client_computes.append({
                    "name": comp["name"],
                    "input_params": comp.get("input_params", []),
                    "body_lines": comp.get("body_lines", []),
                })
        content_names = [cs["name"]["snake"] for cs in ctx.ir.get("content", [])]
        # v0.9 multi-SM: emit one transition map per machine, keyed by
        # content_ref → machine_name → "from|to" → scope. External clients
        # see every machine on every content; legacy single-SM clients
        # that read transitions[content] directly need to update.
        transitions = {}
        for content_ref, sm_list in ctx.sm_lookup.items():
            transitions[content_ref] = {}
            for sm in sm_list:
                transitions[content_ref][sm["machine_name"]] = {
                    f"{from_s}|{to_s}": scope
                    for (from_s, to_s), scope in sm["transitions"].items()
                }
        return {
            "identity": {"role": role, "scopes": user["scopes"], "profile": user["profile"]},
            "pages": user_pages,
            "computes": client_computes,
            "schemas": ctx.ir.get("content", []),
            "content_names": content_names,
            "transitions": transitions,
        }

    @app.get("/runtime/termin.js")
    async def serve_termin_js():
        js_path = Path(__file__).parent / "static" / "termin.js"
        if js_path.exists():
            return Response(content=js_path.read_text(encoding="utf-8"),
                            media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})
        return Response(content="// termin.js not found",
                        media_type="application/javascript", status_code=404)

    # v0.9 Phase 5a.3: theme preference endpoints. BRD #2 §6.2 +
    # presentation-provider-design.md §3.4. Authenticated principals
    # get a row in `_termin_principal_preferences`; anonymous
    # principals get a session-scoped cookie. Both paths apply
    # `theme_locked` resolution at read time.
    _ANON_THEME_COOKIE = "termin_theme_pref"

    def _resolve_theme_for_request(request: Request) -> str:
        user = ctx.get_current_user(request)
        principal = user.get("Principal")
        theme_default = ctx.theme_default
        theme_locked = ctx.theme_locked
        if principal is not None and not principal.is_anonymous:
            conn = sqlite3.connect(ctx.db_path)
            try:
                return get_theme_preference(
                    conn,
                    principal.id,
                    theme_default=theme_default,
                    theme_locked=theme_locked,
                )
            finally:
                conn.close()
        # Anonymous: cookie-scoped storage, with theme_locked still
        # winning. Cookie cleared on session end (no Max-Age).
        if theme_locked is not None:
            return theme_locked
        cookie_val = request.cookies.get(_ANON_THEME_COOKIE)
        if cookie_val and cookie_val in VALID_THEMES:
            return cookie_val
        return theme_default or "auto"

    @app.get("/_termin/preferences/theme")
    async def get_theme_preference_endpoint(request: Request):
        return {"value": _resolve_theme_for_request(request)}

    @app.post("/_termin/preferences/theme")
    async def set_theme_preference_endpoint(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or "value" not in body:
            raise HTTPException(
                status_code=422,
                detail="Body must be an object with a 'value' key.",
            )
        value = body["value"]
        if value not in VALID_THEMES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"value must be one of {list(VALID_THEMES)!r}; "
                    f"got {value!r}"
                ),
            )
        user = ctx.get_current_user(request)
        principal = user.get("Principal")
        # Per BRD §6.2: write succeeds even under theme_locked. The
        # lock check applies only at read time so the user's stored
        # preference survives lock removal.
        if principal is not None and not principal.is_anonymous:
            conn = sqlite3.connect(ctx.db_path)
            try:
                set_theme_preference(conn, principal.id, value)
                conn.commit()
            except InvalidThemeValueError as e:
                # Should not happen — value already validated above —
                # but kept defensively in case the validator and the
                # endpoint diverge.
                raise HTTPException(status_code=422, detail=str(e))
            finally:
                conn.close()
            effective = (
                ctx.theme_locked
                if ctx.theme_locked is not None
                else value
            )
            return {"value": effective}
        # Anonymous: cookie-scoped store. Set-Cookie with no Max-Age
        # → session cookie, cleared when the browser closes.
        effective = (
            ctx.theme_locked
            if ctx.theme_locked is not None
            else value
        )
        response = Response(
            content=json.dumps({"value": effective}),
            media_type="application/json",
        )
        response.set_cookie(
            key=_ANON_THEME_COOKIE,
            value=value,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.get("/runtime/termin.css")
    async def serve_termin_css():
        css_path = Path(__file__).parent / "static" / "termin.css"
        if css_path.exists():
            return Response(content=css_path.read_text(encoding="utf-8"),
                            media_type="text/css",
                            headers={"Cache-Control": "no-cache"})
        return Response(content="/* termin.css not found */",
                        media_type="text/css", status_code=404)

    # v0.9 Phase 5b.4 platform: CSR bundle discovery for presentation
    # providers. termin.js fetches this at boot to load registered
    # provider bundles and bind their per-contract render functions.
    register_presentation_bundle_endpoint(app, ctx)

    # v0.9 Phase 5b.4 B' loop: serve provider bundle files from the
    # provider package's `static/bundle.js`. Pairs with the discovery
    # endpoint above — the discovery list points at this URL by
    # default; CDN-overrides bypass it.
    register_provider_bundle_route(app, ctx)

    # v0.9 Phase 5b.4 B' plumbing: page-data endpoint for SPA
    # navigation. Per the Spectrum-provider design Q2 (B' = server-
    # authoritative + JS-as-renderer), the client fetches each page's
    # bootstrap JSON via this endpoint instead of doing a full HTML
    # round-trip. Auth is identical to a regular page request.
    register_page_data_endpoint(app, ctx)

    # No action endpoint — `Termin.action(payload)` in termin.js
    # dispatches client-side to the existing CRUD / transition /
    # compute REST surface that BRD #2 §11 already standardizes.
    # See docs/spectrum-provider-design.md "Q-extra (action API
    # surface)" for the rationale.

    # v0.9 Phase 5b.4 B' plumbing: HTML shell endpoint. Returns
    # the minimal SPA shell with embedded bootstrap JSON for a
    # given path. Used for dev / provider-validation today;
    # flipping the production page routes to this in place of
    # SSR-composited HTML is the follow-on slice.
    register_shell_endpoint(app, ctx)
