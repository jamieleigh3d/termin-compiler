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

from fastapi import Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pathlib import Path

from .context import RuntimeContext
from .storage import (
    get_db, create_record, get_record, update_record, delete_record,
    list_records, find_by_field,
)
from .state import do_state_transition
from .confidentiality import redact_record, redact_records, check_write_access
from .boundaries import check_boundary_identity
from .validation import (
    validate_dependent_values, validate_enum_constraints,
    validate_min_max_constraints, evaluate_field_defaults, strip_unknown_fields,
)
from .compute_runner import redact_audit_traces


def register_crud_routes(app, ctx: RuntimeContext):
    """Register all CRUD routes from the IR route specs."""

    for route in ctx.ir.get("routes", []):
        content_ref = route.get("content_ref", "")
        method = route.get("method", "GET")
        path = route.get("path", "")
        kind = route.get("kind", "LIST")
        scope = route.get("scope") or route.get("required_scope")
        lookup_col = route.get("lookup_column", "id")
        target_state = route.get("target_state")

        if kind == "LIST":
            _make_list_route(app, ctx, path, content_ref, scope)
        elif kind == "CREATE":
            _make_create_route(app, ctx, path, content_ref, scope,
                               ctx.sm_lookup.get(content_ref))
        elif kind == "GET_ONE":
            _make_get_route(app, ctx, path, content_ref, scope, lookup_col)
        elif kind == "UPDATE":
            _make_update_route(app, ctx, path, content_ref, scope, lookup_col)
        elif kind == "DELETE":
            _make_delete_route(app, ctx, path, content_ref, scope, lookup_col)
        elif kind == "TRANSITION":
            _make_transition_route(app, ctx, path, content_ref, scope,
                                   lookup_col, target_state)


def _make_list_route(app, ctx, path, cr, sc):
    deps = [Depends(ctx.require_scope(sc))] if sc else []

    # Reserved query-param names — not treated as field filters.
    _reserved_params = {"limit", "offset", "sort"}

    @app.get(path, dependencies=deps)
    async def list_route(request: Request, _cr=cr):
        user = ctx.get_current_user(request)
        user_scopes = list(user.get("scopes", []))
        bnd_id_err = check_boundary_identity(
            ctx.boundary_identity_scopes, ctx.boundary_for_content,
            _cr, user_scopes)
        if bnd_id_err:
            raise HTTPException(status_code=403, detail=bnd_id_err)

        schema = ctx.content_lookup.get(_cr, {})

        # Parse pagination: ?limit=N&offset=N.
        qp = request.query_params
        limit = None
        offset = None
        if "limit" in qp:
            try:
                limit = int(qp["limit"])
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"limit must be an integer, got {qp['limit']!r}")
            if limit < 0:
                raise HTTPException(
                    status_code=400, detail="limit must be non-negative")
            if limit > 1000:
                # Protect the runtime from pathological queries.
                raise HTTPException(
                    status_code=400, detail="limit must not exceed 1000")
        if "offset" in qp:
            try:
                offset = int(qp["offset"])
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"offset must be an integer, got {qp['offset']!r}")
            if offset < 0:
                raise HTTPException(
                    status_code=400, detail="offset must be non-negative")

        # Parse sort: ?sort=field or ?sort=field:asc or ?sort=field:desc.
        sort_by = None
        sort_dir = None
        if "sort" in qp:
            raw = qp["sort"]
            if ":" in raw:
                sort_by, sort_dir = raw.split(":", 1)
            else:
                sort_by, sort_dir = raw, "asc"

        # Parse filters: every non-reserved query param becomes a
        # {field: value} equality filter. Schema validation in list_records
        # rejects unknown fields.
        filters = {k: v for k, v in qp.items() if k not in _reserved_params}

        db = await get_db(ctx.db_path)
        try:
            try:
                records = await list_records(
                    db, _cr,
                    limit=limit, offset=offset,
                    filters=filters if filters else None,
                    sort_by=sort_by, sort_dir=sort_dir,
                    schema=schema if (filters or sort_by) else None,
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            records = redact_records(records, schema, set(user_scopes))
            if _cr.startswith("compute_audit_log_"):
                records = await redact_audit_traces(
                    ctx, records, _cr, set(user_scopes))
            return records
        finally:
            await db.close()


def _make_create_route(app, ctx, path, cr, sc, sm_info):
    deps = [Depends(ctx.require_scope(sc))] if sc else []

    @app.post(path, status_code=201, dependencies=deps)
    async def create_route(request: Request, _cr=cr, _sm=sm_info):
        user = ctx.get_current_user(request)
        user_scopes = list(user.get("scopes", []))
        bnd_id_err = check_boundary_identity(
            ctx.boundary_identity_scopes, ctx.boundary_for_content,
            _cr, user_scopes)
        if bnd_id_err:
            raise HTTPException(status_code=403, detail=bnd_id_err)

        # Accept both JSON and form-encoded data
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
        else:
            form = await request.form()
            body = {k: v for k, v in form.items() if v}

        if _sm:
            body["status"] = _sm.get("initial", "")

        schema = ctx.content_lookup.get(_cr, {})
        evaluate_field_defaults(body, schema, ctx.expr_eval, user)
        validate_enum_constraints(body, schema)
        validate_min_max_constraints(body, schema)
        validate_dependent_values(_cr, body, ctx.content_lookup, ctx.expr_eval)
        body = strip_unknown_fields(body, schema)

        db = await get_db(ctx.db_path)
        try:
            record = await create_record(db, _cr, body, schema, _sm,
                                         ctx.terminator, ctx.event_bus)
            await ctx.run_event_handlers(db, _cr, "created", record)
            user_scopes_set = set(user.get("scopes", []))
            return redact_record(record, schema, user_scopes_set)
        except HTTPException:
            raise
        except Exception as e:
            err_msg = str(e)
            if "UNIQUE constraint" in err_msg:
                raise HTTPException(status_code=409, detail=err_msg)
            if "NOT NULL constraint" in err_msg:
                raise HTTPException(status_code=400, detail=err_msg)
            raise HTTPException(status_code=500, detail=err_msg)
        finally:
            await db.close()


def _make_get_route(app, ctx, path, cr, sc, lc):
    deps = [Depends(ctx.require_scope(sc))] if sc else []

    @app.get(path, dependencies=deps)
    async def get_route(request: Request, _cr=cr, _lc=lc):
        param_val = list(request.path_params.values())[0] if request.path_params else None
        db = await get_db(ctx.db_path)
        try:
            record = await get_record(db, _cr, param_val, _lc)
            schema = ctx.content_lookup.get(_cr, {})
            user = ctx.get_current_user(request)
            user_scopes = set(user.get("scopes", []))
            record = redact_record(record, schema, user_scopes)
            if _cr.startswith("compute_audit_log_"):
                records = await redact_audit_traces(
                    ctx, [record], _cr, set(user_scopes))
                record = records[0] if records else record
            return record
        finally:
            await db.close()


def _make_update_route(app, ctx, path, cr, sc, lc):
    deps = [Depends(ctx.require_scope(sc))] if sc else []

    @app.put(path, dependencies=deps)
    async def update_route(request: Request, _cr=cr, _lc=lc):
        param_val = list(request.path_params.values())[0] if request.path_params else None
        body = await request.json()
        user = ctx.get_current_user(request)
        user_scopes = set(user.get("scopes", []))
        schema = ctx.content_lookup.get(_cr, {})
        write_err = check_write_access(body, schema, user_scopes)
        if write_err:
            raise HTTPException(status_code=403, detail=write_err)
        db = await get_db(ctx.db_path)
        try:
            existing = await get_record(db, _cr, param_val, _lc)
            if existing:
                merged = dict(existing)
                merged.update(body)
                validate_dependent_values(_cr, merged, ctx.content_lookup, ctx.expr_eval)
            else:
                validate_dependent_values(_cr, body, ctx.content_lookup, ctx.expr_eval)
            record = await update_record(db, _cr, param_val, body, _lc,
                                         ctx.terminator, ctx.event_bus)
            await ctx.run_event_handlers(db, _cr, "updated", record)
            return redact_record(record, schema, user_scopes)
        finally:
            await db.close()


def _make_delete_route(app, ctx, path, cr, sc, lc):
    deps = [Depends(ctx.require_scope(sc))] if sc else []

    @app.delete(path, dependencies=deps)
    async def delete_route(request: Request, _cr=cr, _lc=lc):
        param_val = list(request.path_params.values())[0] if request.path_params else None
        db = await get_db(ctx.db_path)
        try:
            await delete_record(db, _cr, param_val, _lc, ctx.terminator, ctx.event_bus)
            return {"deleted": True}
        finally:
            await db.close()


def _make_transition_route(app, ctx, path, cr, sc, lc, ts):
    deps = [Depends(ctx.require_scope(sc))] if sc else []

    @app.post(path, dependencies=deps)
    async def transition_route(request: Request, _cr=cr, _lc=lc, _ts=ts):
        param_val = list(request.path_params.values())[0] if request.path_params else None
        user = ctx.get_current_user(request)
        db = await get_db(ctx.db_path)
        try:
            row = await find_by_field(db, _cr, _lc, param_val)
            if not row:
                raise HTTPException(status_code=404)
            return await do_state_transition(db, _cr, row["id"], _ts, user,
                                             ctx.sm_lookup, ctx.terminator, ctx.event_bus)
        finally:
            await db.close()


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
                                                 ctx.sm_lookup.get(ch_content))
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
            "runtime_version": "0.8.0",
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
        transitions = {}
        for content_ref, sm_data in ctx.sm_lookup.items():
            transitions[content_ref] = {
                f"{from_s}|{to_s}": scope
                for (from_s, to_s), scope in sm_data["transitions"].items()
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

    @app.get("/runtime/termin.css")
    async def serve_termin_css():
        css_path = Path(__file__).parent / "static" / "termin.css"
        if css_path.exists():
            return Response(content=css_path.read_text(encoding="utf-8"),
                            media_type="text/css",
                            headers={"Cache-Control": "no-cache"})
        return Response(content="/* termin.css not found */",
                        media_type="text/css", status_code=404)
