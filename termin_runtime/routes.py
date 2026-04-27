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
from .presentation_bundles import register_presentation_bundle_endpoint


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
    deps = [Depends(ctx.require_scope(sc))] if sc else []

    # Reserved query-param names — not treated as field filters.
    # `offset` was retired in v0.9 along with the keyset-cursor
    # pagination contract; the runtime rejects it explicitly so
    # callers can't silently get incorrect behavior.
    _reserved_params = {"limit", "sort", "cursor"}

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
        schema_fields = {f["name"] for f in schema.get("fields", [])}
        # Implicit columns the contract layer also accepts.
        schema_fields.update({"id", "status"})
        # State machine columns — multi-SM in v0.9.
        for sm in ctx.sm_lookup.get(_cr, []):
            schema_fields.add(sm["machine_name"])

        # Parse pagination: ?limit=N&cursor=<token>. The v0.9
        # storage contract is keyset-cursor only (BRD §6.2); the
        # legacy ?offset= URL parameter was retired in Phase 2.x
        # and is now rejected with a 400 so callers don't silently
        # get incorrect behavior.
        qp = request.query_params
        if "offset" in qp:
            raise HTTPException(
                status_code=400,
                detail=(
                    "?offset= was removed in v0.9. Use ?cursor= "
                    "with the next_cursor token from a prior "
                    "response. Cursors are opaque; do not parse."
                )
            )
        # Default: no limit (return everything) — preserves the
        # v0.8 callers' expectation. Provider's QueryOptions.limit
        # defaults to 50, which is the contract default; the route
        # opts out by passing a large value when the caller didn't
        # ask for one.
        limit_from_url = None
        if "limit" in qp:
            try:
                limit_from_url = int(qp["limit"])
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"limit must be an integer, got {qp['limit']!r}")
            if limit_from_url < 0:
                raise HTTPException(
                    status_code=400, detail="limit must be non-negative")
            if limit_from_url > 1000:
                raise HTTPException(
                    status_code=400, detail="limit must not exceed 1000")

        # Parse sort: ?sort=field or ?sort=field:asc or ?sort=field:desc.
        order_by_list: list[OrderBy] = []
        if "sort" in qp:
            raw = qp["sort"]
            if ":" in raw:
                sf, sd = raw.split(":", 1)
            else:
                sf, sd = raw, "asc"
            sd_lower = sd.lower()
            if sd_lower not in ("asc", "desc"):
                raise HTTPException(
                    status_code=400,
                    detail=f"sort direction must be 'asc' or 'desc', got {sd!r}")
            if sf not in schema_fields:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown sort field '{sf}' for {_cr}")
            order_by_list.append(OrderBy(field=sf, direction=sd_lower))

        # Parse filters: non-reserved query params become Eq predicates.
        filter_eqs: list = []
        for k, v in qp.items():
            if k in _reserved_params:
                continue
            if k not in schema_fields:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown filter field '{k}' for {_cr}")
            filter_eqs.append(Eq(field=k, value=v))

        # v0.9 Phase 6a.5: ownership row_filter (BRD #3 §3.4 / §3.5).
        # When the route carries row_filter={"kind":"ownership","field":F},
        # restrict the result set to rows where F equals the principal's
        # id (on_behalf_of per BRD §3.5; for direct user actions this is
        # the same as the request principal — agent invocations split in
        # Phase 6a.6+).
        if row_filter and row_filter.get("kind") == "ownership":
            owner_field = row_filter.get("field")
            owner_id = (user.get("the_user") or {}).get("id", "")
            if owner_field and owner_id:
                filter_eqs.append(Eq(field=owner_field, value=owner_id))

        predicate = None
        if len(filter_eqs) == 1:
            predicate = filter_eqs[0]
        elif len(filter_eqs) > 1:
            predicate = And(predicates=tuple(filter_eqs))

        # If no limit was supplied, fall back to the contract max so
        # legacy "return all" behavior is preserved within reason.
        # 1000 matches QueryOptions' upper bound and the v0.8 list
        # endpoint's protective cap.
        effective_limit = limit_from_url if limit_from_url is not None else 1000

        # Phase 2.x (e): keyset cursors only.
        url_cursor = qp.get("cursor")
        options = QueryOptions(
            limit=effective_limit,
            cursor=url_cursor,
            order_by=tuple(order_by_list),
        )
        try:
            page = await ctx.storage.query(_cr, predicate, options)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        records = [dict(r) for r in page.records]
        records = redact_records(records, schema, set(user_scopes))
        if _cr.startswith("compute_audit_log_"):
            records = await redact_audit_traces(
                ctx, records, _cr, set(user_scopes))
        return records


def _make_create_route(app, ctx, path, cr, sc, sm_info, owner_field=None):
    deps = [Depends(ctx.require_scope(sc))] if sc else []

    @app.post(path, status_code=201, dependencies=deps)
    async def create_route(request: Request, _cr=cr, _sm=sm_info, _owner=owner_field):
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

        # v0.9 multi-SM create gate: strip every state-machine column
        # from the body before validation+insert. The SQL DEFAULT on
        # each state column applies the machine's initial state; a
        # client-supplied value for a state column would otherwise win
        # over the default and let a caller bootstrap a record already
        # past its initial state — bypassing the transition rules that
        # would otherwise gate that move.
        body = _seed_state_columns(body, _sm, strip_existing=True)

        # v0.9 Phase 6a.5: stamp the ownership field with the_user.id
        # at create time (BRD #3 §3.5 — "the new row's owner is set to
        # on_behalf_of.id, not invoked_by.id"). Overwrites any client-
        # supplied value so apps cannot create rows owned by other
        # principals. Anonymous principals can still own records (id =
        # "anonymous"); apps that don't want anonymous ownership gate
        # CREATE with a non-anonymous scope.
        if _owner:
            owner_id = (user.get("the_user") or {}).get("id", "")
            body[_owner] = owner_id

        schema = ctx.content_lookup.get(_cr, {})
        evaluate_field_defaults(body, schema, ctx.expr_eval, user)
        validate_enum_constraints(body, schema)
        validate_min_max_constraints(body, schema)
        validate_dependent_values(_cr, body, ctx.content_lookup, ctx.expr_eval)
        body = strip_unknown_fields(body, schema)

        # v0.9 Phase 2: persist via ctx.storage. The provider returns
        # the persisted row (id assigned). The route owns the
        # cross-cutting concerns the legacy create_record had bundled:
        # state-column response seeding, event publishing, IR event
        # handlers, and TerminAtor error routing.
        try:
            record = await ctx.storage.create(_cr, body)
            # Seed state columns into the response dict. The SQL DEFAULT
            # has already applied to the persisted row; the in-memory
            # dict needs the columns too so downstream callers see a
            # consistent shape.
            record = _seed_state_columns(dict(record), _sm)
            # event_bus publish — listeners (WebSocket forwarder,
            # subscribers) get notified. This is the channel-id
            # broadcast separate from IR-declared event handlers.
            await _publish_content_event(ctx, "created", _cr, record)
        except HTTPException:
            raise
        except Exception as e:
            _route_terminator(ctx, _cr, e)
            err_msg = str(e)
            if "UNIQUE constraint" in err_msg:
                raise HTTPException(status_code=409, detail=err_msg)
            if "NOT NULL constraint" in err_msg:
                raise HTTPException(status_code=400, detail=err_msg)
            raise HTTPException(status_code=500, detail=err_msg)

        # IR-declared event handlers (When ...: Create a ..., Send to
        # channel ...) still need a raw db handle for their nested
        # insert paths (they call insert_raw directly). v0.9.x cleanup
        # item: route those through ctx.storage too.
        db = await get_db(ctx.db_path)
        try:
            await ctx.run_event_handlers(db, _cr, "created", record)
        finally:
            await db.close()

        return redact_record(record, schema, set(user.get("scopes", [])))


def _make_get_route(app, ctx, path, cr, sc, lc, row_filter=None):
    deps = [Depends(ctx.require_scope(sc))] if sc else []

    @app.get(path, dependencies=deps)
    async def get_route(request: Request, _cr=cr, _lc=lc):
        param_val = list(request.path_params.values())[0] if request.path_params else None
        # v0.9 Phase 2: Read by primary key uses ctx.storage.read; for
        # alternate-key lookups (lookup_column != "id") we fall back
        # to a query() with an Eq predicate. The provider boundary
        # only knows primary keys; alternate-key lookups are runtime
        # convenience layered on top.
        if _lc == "id":
            record = await ctx.storage.read(_cr, param_val)
        else:
            page = await ctx.storage.query(
                _cr, Eq(field=_lc, value=param_val),
                QueryOptions(limit=1),
            )
            record = dict(page.records[0]) if page.records else None
        if record is None:
            raise HTTPException(status_code=404, detail="Not found")
        schema = ctx.content_lookup.get(_cr, {})
        user = ctx.get_current_user(request)
        user_scopes = set(user.get("scopes", []))
        # v0.9 Phase 6a.5: ownership row_filter on GET_ONE. Reject the
        # read with a 404 when the principal doesn't own the record.
        # 404 (not 403) by design — per BRD §3.4, the row "doesn't
        # exist" from the principal's perspective; existence shouldn't
        # leak through the auth shape.
        if row_filter and row_filter.get("kind") == "ownership":
            owner_field = row_filter.get("field")
            owner_id = (user.get("the_user") or {}).get("id", "")
            if owner_field and record.get(owner_field) != owner_id:
                raise HTTPException(status_code=404, detail="Not found")
        record = redact_record(record, schema, user_scopes)
        if _cr.startswith("compute_audit_log_"):
            records = await redact_audit_traces(
                ctx, [record], _cr, set(user_scopes))
            record = records[0] if records else record
        return record


def _make_update_route(app, ctx, path, cr, sc, lc, row_filter=None):
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

        # v0.9 Phase 2: lookup goes through ctx.storage.read (id) or
        # query() (alternate key). The state-machine PUT-gate still
        # uses a raw db handle because do_state_transition() reads
        # and writes via low-level helpers; that path is a v0.9.x
        # cleanup item — wiring transitions through ctx.storage is
        # straightforward but cascades into state.py + transitions.py.
        if _lc == "id":
            existing = await ctx.storage.read(_cr, param_val)
            target_id = param_val
        else:
            page = await ctx.storage.query(
                _cr, Eq(field=_lc, value=param_val),
                QueryOptions(limit=1),
            )
            existing = dict(page.records[0]) if page.records else None
            target_id = existing["id"] if existing else None

        # v0.9 Phase 6a.5: ownership row_filter on UPDATE. Reject with
        # 404 when the principal doesn't own the row, matching GET_ONE
        # semantics — the row "doesn't exist" from this principal's
        # perspective. Also rejects attempts to overwrite the
        # ownership field itself: principal X cannot transfer their
        # row to principal Y by setting <owner_field>=Y, even with
        # the right scope.
        if row_filter and row_filter.get("kind") == "ownership":
            owner_field = row_filter.get("field")
            owner_id = (user.get("the_user") or {}).get("id", "")
            if existing and owner_field and existing.get(owner_field) != owner_id:
                raise HTTPException(status_code=404, detail="Not found")
            if owner_field in body:
                # Strip the ownership field from the body; preserves
                # the original owner. (Alternative: 403. The strip is
                # less informative but lets benign updates with the
                # same owner_id pass through cleanly.)
                body = {k: v for k, v in body.items() if k != owner_field}

        # ── State-machine PUT-route gate ──
        #
        # If the body carries any state-machine column whose value
        # differs from the current record, route that change through
        # do_state_transition — same security posture as POST
        # /_transition: declared transitions only, required_scope
        # enforced, atomic on rejection. Multi-SM in v0.9: each
        # touched machine transitions in IR order; any failure stops
        # the chain and rolls subsequent updates.
        if existing and _cr in ctx.sm_lookup:
            sm_list = ctx.sm_lookup.get(_cr, [])
            state_cols = {sm["machine_name"] for sm in sm_list}
            touched = [sm for sm in sm_list if sm["machine_name"] in body]
            if touched:
                # Phase 2.x (d): transitions go through ctx.storage's
                # atomic CAS. No raw db connection in this path
                # anymore.
                for sm in touched:
                    col = sm["machine_name"]
                    new_val = body.get(col, "")
                    cur_val = existing.get(col, "")
                    if new_val != cur_val:
                        await do_state_transition(
                            ctx.storage, _cr, existing["id"], col, new_val, user,
                            ctx.sm_lookup, ctx.terminator, ctx.event_bus,
                        )
            if state_cols:
                body = {k: v for k, v in body.items() if k not in state_cols}

        if existing:
            merged = dict(existing)
            merged.update(body)
            validate_dependent_values(_cr, merged, ctx.content_lookup, ctx.expr_eval)
        else:
            validate_dependent_values(_cr, body, ctx.content_lookup, ctx.expr_eval)

        if body and target_id is not None:
            try:
                record = await ctx.storage.update(_cr, target_id, body)
            except Exception as e:
                _route_terminator(ctx, _cr, e)
                raise
            if record is None:
                raise HTTPException(status_code=404, detail="Not found")
            record = dict(record)
            await _publish_content_event(ctx, "updated", _cr, record)
            db = await get_db(ctx.db_path)
            try:
                await ctx.run_event_handlers(db, _cr, "updated", record)
            finally:
                await db.close()
        else:
            # Body was state-only and already applied via transition,
            # OR there was no record to update. Return the current
            # record post-transition (or 404).
            if target_id is None:
                raise HTTPException(status_code=404, detail="Not found")
            record = await ctx.storage.read(_cr, target_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Not found")

        return redact_record(record, schema, user_scopes)


def _make_delete_route(app, ctx, path, cr, sc, lc, row_filter=None):
    deps = [Depends(ctx.require_scope(sc))] if sc else []

    @app.delete(path, dependencies=deps)
    async def delete_route(request: Request, _cr=cr, _lc=lc):
        param_val = list(request.path_params.values())[0] if request.path_params else None
        # v0.9: delete via ctx.storage. cascade_mode at the contract
        # boundary is the caller's intent ("if any children, what
        # should happen?"). The actual cascade semantics for this
        # delete come from each child's FK declaration in the schema
        # (ON DELETE CASCADE vs ON DELETE RESTRICT, emitted by
        # init_db from the IR's FieldSpec.cascade_mode). RESTRICT
        # at this level just means "if the schema says any child
        # should restrict, honor that." The SqliteStorageProvider
        # ignores the arg in v0.9 since SQLite's FK enforcement is
        # the source of truth. Future providers (Postgres, DynamoDB)
        # may consult it.
        try:
            target_id = param_val
            if _lc != "id":
                page = await ctx.storage.query(
                    _cr, Eq(field=_lc, value=param_val),
                    QueryOptions(limit=1),
                )
                if not page.records:
                    raise HTTPException(status_code=404, detail="Record not found")
                target_id = page.records[0].get("id")

            # v0.9 Phase 6a.5: ownership row_filter on DELETE. Reject
            # with 404 when the principal doesn't own the row, matching
            # GET_ONE / UPDATE semantics.
            if row_filter and row_filter.get("kind") == "ownership":
                user = ctx.get_current_user(request)
                owner_field = row_filter.get("field")
                owner_id = (user.get("the_user") or {}).get("id", "")
                if owner_field:
                    rec = await ctx.storage.read(_cr, target_id)
                    if rec is None or rec.get(owner_field) != owner_id:
                        raise HTTPException(status_code=404, detail="Record not found")
            deleted = await ctx.storage.delete(
                _cr, target_id, cascade_mode=CascadeMode.RESTRICT,
            )
        except HTTPException:
            raise
        except sqlite3.IntegrityError as e:
            msg = str(e)
            if "FOREIGN KEY" in msg.upper():
                singular = _cr[:-1] if _cr.endswith("s") else _cr
                detail = (
                    f"Cannot delete this {singular}: "
                    f"other records reference it. Remove or reassign those first."
                )
                _route_terminator(ctx, _cr, e)
                raise HTTPException(status_code=409, detail=detail)
            raise
        if not deleted:
            raise HTTPException(status_code=404, detail="Record not found")
        await _publish_content_event(ctx, "deleted", _cr, {"id": target_id})
        return {"deleted": True}


def _make_transition_route(app, ctx, path, cr, sc, lc, ts, mn=None):
    """Per-machine, per-target-state transition route from RouteSpec.

    `mn` is the machine_name (snake_case) the route drives. Required in
    v0.9 — every transition route addresses one machine on one content.
    `_make_transition_route` callers from `register_crud_routes` always
    pass it; older internal callers (none currently) would not, in
    which case the route falls back to driving the first state machine
    on the content for backward compatibility.
    """
    deps = [Depends(ctx.require_scope(sc))] if sc else []

    @app.post(path, dependencies=deps)
    async def transition_route(request: Request, _cr=cr, _lc=lc, _ts=ts, _mn=mn):
        param_val = list(request.path_params.values())[0] if request.path_params else None
        user = ctx.get_current_user(request)
        # Phase 2.x (d): transition_route resolves the record id via
        # find_by_field (which currently still uses raw db; that's
        # storage-helper land, not a do_state_transition arg) and
        # routes the actual transition through ctx.storage's atomic
        # CAS.
        db = await get_db(ctx.db_path)
        try:
            row = await find_by_field(db, _cr, _lc, param_val)
        finally:
            await db.close()
        if not row:
            raise HTTPException(status_code=404)
        # Resolve machine_name: if RouteSpec didn't carry it (legacy IR
        # generated by an older compiler), fall back to the first
        # state machine on the content. Single-SM content behaves
        # identically; multi-SM content with a legacy IR would route
        # ambiguously and is a compiler/runtime version mismatch.
        machine = _mn
        if machine is None:
            sms = ctx.sm_lookup.get(_cr, [])
            if sms:
                machine = sms[0]["machine_name"]
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"No state machine for {_cr}")
        return await do_state_transition(
            ctx.storage, _cr, row["id"], machine, _ts, user,
            ctx.sm_lookup, ctx.terminator, ctx.event_bus)


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
