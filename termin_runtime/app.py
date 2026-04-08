"""Termin App Factory — creates a configured FastAPI app from IR JSON.

This is the main entry point for the Termin runtime. It reads the IR,
creates all subsystems, registers routes, and returns a FastAPI app.
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, HTTPException, Depends, Form, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from .expression import ExpressionEvaluator
from .errors import TerminError, TerminAtor
from .events import EventBus
from .identity import make_get_current_user, make_require_scope, make_get_user_from_websocket
from .storage import get_db, init_db, create_record, list_records, get_record, update_record, delete_record
from .state import do_state_transition
from .reflection import ReflectionEngine, register_reflection_with_expr_eval
from .presentation import build_base_template, build_nav_html, build_page_template, jinja_env
from .confidentiality import (
    redact_record, redact_records, check_write_access,
    check_compute_access, check_taint_integrity, enforce_output_taint,
    check_for_redacted_values, is_redacted,
)
from .transaction import Transaction


def create_termin_app(ir_json: str, db_path: str = None, seed_data: dict = None) -> FastAPI:
    """Create a fully configured FastAPI app from an IR JSON string.

    Args:
        ir_json: The IR JSON string.
        db_path: Path to the SQLite database file.
        seed_data: Optional dict of {content_name: [record_dicts]} to seed on first run.
    """
    ir = json.loads(ir_json)
    app_name = ir.get("name", "Termin App")

    # ── Subsystem initialization ──
    expr_eval = ExpressionEvaluator()
    terminator = TerminAtor()
    event_bus = EventBus()
    reflection = ReflectionEngine(ir_json)

    # Identity
    roles = {}
    for role in ir.get("auth", {}).get("roles", []):
        roles[role["name"]] = role["scopes"]
    if not any(k.lower() == "anonymous" for k in roles):
        roles["anonymous"] = []

    get_current_user = make_get_current_user(roles)
    get_user_from_ws = make_get_user_from_websocket(roles)
    require_scope = make_require_scope(get_current_user)

    # Content schemas for storage
    schemas = []
    content_lookup = {}  # snake_name -> schema dict
    sm_lookup = {}  # content_ref -> {"initial": str, "transitions": {(from,to): scope}}
    for cs in ir.get("content", []):
        schemas.append(cs)
        content_lookup[cs["name"]["snake"]] = cs
    for sm in ir.get("state_machines", []):
        # Normalize transitions array into {(from, to): scope} dict
        trans_dict = {}
        for t in sm.get("transitions", []):
            trans_dict[(t["from_state"], t["to_state"])] = t.get("required_scope", "")
        sm_lookup[sm["content_ref"]] = {
            "initial": sm.get("initial_state", ""),
            "transitions": trans_dict,
        }

    # Access grants for scope lookup
    def scope_for_content_verb(content_snake, verb):
        for g in ir.get("access_grants", []):
            if g["content"] == content_snake and verb in g["verbs"]:
                return g["scope"]
        return None

    # Register reflection with expression evaluator
    register_reflection_with_expr_eval(reflection, expr_eval)

    # ── WebSocket Connection Manager ──
    class ConnectionManager:
        def __init__(self):
            self.active: dict[str, dict] = {}  # conn_id -> {ws, user, subscriptions}

        async def connect(self, ws: WebSocket, user: dict) -> str:
            conn_id = str(uuid.uuid4())[:8]
            self.active[conn_id] = {"ws": ws, "user": user, "subscriptions": set()}
            return conn_id

        def disconnect(self, conn_id: str):
            self.active.pop(conn_id, None)

        def add_subscription(self, conn_id: str, channel_id: str):
            if conn_id in self.active:
                self.active[conn_id]["subscriptions"].add(channel_id)

        def remove_subscription(self, conn_id: str, channel_id: str):
            if conn_id in self.active:
                self.active[conn_id]["subscriptions"].discard(channel_id)

        async def broadcast_to_subscribers(self, channel_id: str, event: dict):
            dead = []
            for conn_id, conn in self.active.items():
                for pattern in conn["subscriptions"]:
                    if channel_id.startswith(pattern):
                        try:
                            await conn["ws"].send_json({
                                "v": 1,
                                "ch": channel_id,
                                "op": "push",
                                "ref": None,
                                "payload": event.get("record") or event,
                            })
                        except Exception:
                            dead.append(conn_id)
                        break
            for conn_id in dead:
                self.disconnect(conn_id)

    conn_manager = ConnectionManager()

    # ── Lifespan ──
    @asynccontextmanager
    async def lifespan(app):
        print(f"[Termin] Phase 0: Bootstrap")
        print(f"[Termin] Phase 1: TerminAtor initialized")
        print(f"[Termin] Phase 2: Expression evaluator ready")
        print(f"[Termin] Phase 3: Initializing storage")
        await init_db(schemas, db_path)
        # Seed data if provided and tables are empty
        if seed_data:
            db = await get_db(db_path)
            try:
                for content_name, records in seed_data.items():
                    cursor = await db.execute(f"SELECT COUNT(*) as cnt FROM {content_name}")
                    row = await cursor.fetchone()
                    if row["cnt"] == 0:
                        for record in records:
                            cols = list(record.keys())
                            placeholders = ", ".join("?" for _ in cols)
                            col_str = ", ".join(cols)
                            vals = [record[k] for k in cols]
                            await db.execute(
                                f"INSERT INTO {content_name} ({col_str}) VALUES ({placeholders})",
                                tuple(vals),
                            )
                        await db.commit()
                        print(f"[Termin] Seeded {len(records)} records into {content_name}")
            finally:
                await db.close()
        print(f"[Termin] Phase 4: Registering primitives")
        print(f"[Termin] Phase 5a: Starting WebSocket forwarder")

        async def _ws_forwarder():
            q = event_bus.subscribe()
            try:
                while True:
                    event = await q.get()
                    ch_id = event.get("channel_id")
                    if ch_id:
                        await conn_manager.broadcast_to_subscribers(ch_id, event)
            except asyncio.CancelledError:
                pass
            finally:
                event_bus.unsubscribe(q)

        forwarder = asyncio.create_task(_ws_forwarder())
        print(f"[Termin] Phase 5: Ready to serve")
        yield
        forwarder.cancel()
        print(f"[Termin] Shutting down...")

    app = FastAPI(title=app_name, lifespan=lifespan)

    # ── Set-role endpoint ──
    @app.post("/set-role")
    async def set_role(role: str = Form(...), user_name: str = Form("")):
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie("termin_role", role)
        if user_name:
            response.set_cookie("termin_user_name", user_name)
        return response

    # ── Runtime endpoints ──
    @app.get("/runtime/registry")
    async def runtime_registry(request: Request):
        """Boundary registry for distributed runtime."""
        host = request.headers.get("host", "localhost:8000")
        scheme = "wss" if request.url.scheme == "https" else "ws"
        http_scheme = request.url.scheme or "http"
        boundaries = {}
        for bnd in ir.get("boundaries", []):
            name = bnd.get("name", {}).get("snake", "unknown")
            boundaries[name] = {
                "location": "local",
                "channels": {
                    "realtime": f"{scheme}://{host}/runtime/ws",
                    "reliable": f"{http_scheme}://{host}/runtime/api",
                },
            }
        # Always include presentation boundary for client
        boundaries["presentation"] = {
            "location": "client",
            "channels": {
                "realtime": f"{scheme}://{host}/runtime/ws",
                "reliable": f"{http_scheme}://{host}/runtime/api",
            },
        }
        return {
            "runtime_version": "0.3.0",
            "application": app_name,
            "boundaries": boundaries,
            "protocols": {"realtime": "websocket", "reliable": "rest"},
        }

    @app.get("/runtime/bootstrap")
    async def runtime_bootstrap(request: Request):
        """Role-scoped bootstrap payload for client runtime."""
        user = get_current_user(request)
        role = user["role"]
        # Filter pages by role
        user_pages = [p for p in ir.get("pages", []) if p["role"] == role
                      or p["role"].lower() == role.lower()]
        # Client-safe computes: Transform shape with body_lines
        client_computes = []
        for comp in ir.get("computes", []):
            if comp.get("body_lines"):
                client_computes.append({
                    "name": comp["name"],
                    "input_params": comp.get("input_params", []),
                    "body_lines": comp.get("body_lines", []),
                })
        # Content names for subscription
        content_names = [cs["name"]["snake"] for cs in ir.get("content", [])]
        return {
            "identity": {"role": role, "scopes": user["scopes"], "profile": user["profile"]},
            "pages": user_pages,
            "computes": client_computes,
            "schemas": ir.get("content", []),
            "content_names": content_names,
        }

    @app.get("/runtime/termin.js")
    async def serve_termin_js():
        """Serve the client runtime module."""
        js_path = Path(__file__).parent / "static" / "termin.js"
        if js_path.exists():
            return Response(content=js_path.read_text(encoding="utf-8"),
                            media_type="application/javascript",
                            headers={"Cache-Control": "public, max-age=3600"})
        return Response(content="// termin.js not found", media_type="application/javascript",
                        status_code=404)

    # ── WebSocket multiplexer ──
    @app.websocket("/runtime/ws")
    async def runtime_ws(websocket: WebSocket):
        user = get_user_from_ws(websocket)
        await websocket.accept()
        conn_id = await conn_manager.connect(websocket, user)

        # Send identity context as first frame
        await websocket.send_json({
            "v": 1, "ch": "runtime.identity", "op": "push", "ref": None,
            "payload": {"role": user["role"], "scopes": user["scopes"], "profile": user["profile"]},
        })

        try:
            while True:
                frame = await websocket.receive_json()
                op = frame.get("op", "")
                ch = frame.get("ch", "")
                ref = frame.get("ref")

                if op == "subscribe":
                    # Subscribe to content channel
                    conn_manager.add_subscription(conn_id, ch)
                    # Send current data
                    # Parse channel: "content.<name>.changes" -> content_name
                    parts = ch.split(".")
                    if len(parts) >= 2 and parts[0] == "content":
                        content_name = parts[1]
                        try:
                            db = await get_db(db_path)
                            try:
                                cursor = await db.execute(f"SELECT * FROM {content_name}")
                                rows = [dict(r) for r in await cursor.fetchall()]
                            finally:
                                await db.close()
                            await websocket.send_json({
                                "v": 1, "ch": ch, "op": "response", "ref": ref,
                                "payload": {"current": rows},
                            })
                        except Exception as e:
                            await websocket.send_json({
                                "v": 1, "ch": ch, "op": "error", "ref": ref,
                                "payload": {"message": str(e)},
                            })
                    else:
                        await websocket.send_json({
                            "v": 1, "ch": ch, "op": "response", "ref": ref,
                            "payload": {"current": []},
                        })

                elif op == "unsubscribe":
                    conn_manager.remove_subscription(conn_id, ch)
                    await websocket.send_json({
                        "v": 1, "ch": ch, "op": "response", "ref": ref,
                        "payload": {"unsubscribed": True},
                    })

                elif op == "request":
                    # One-shot data request
                    parts = ch.split(".")
                    if len(parts) >= 2 and parts[0] == "content":
                        content_name = parts[1]
                        try:
                            db = await get_db(db_path)
                            try:
                                cursor = await db.execute(f"SELECT * FROM {content_name}")
                                rows = [dict(r) for r in await cursor.fetchall()]
                            finally:
                                await db.close()
                            await websocket.send_json({
                                "v": 1, "ch": ch, "op": "response", "ref": ref,
                                "payload": {"data": rows},
                            })
                        except Exception as e:
                            await websocket.send_json({
                                "v": 1, "ch": ch, "op": "error", "ref": ref,
                                "payload": {"message": str(e)},
                            })

        except WebSocketDisconnect:
            conn_manager.disconnect(conn_id)
        except Exception:
            conn_manager.disconnect(conn_id)

    # ── Event handlers ──
    async def run_event_handlers(db, content_name: str, trigger: str, record: dict):
        for ev in ir.get("events", []):
            if ev.get("trigger") == "expr" and ev.get("condition_expr"):
                if content_name == ev.get("source_content", ""):
                    ctx = dict(record)
                    # Add camelCase aliases
                    for k, v in list(record.items()):
                        parts = k.split("_")
                        camel = parts[0] + "".join(w.capitalize() for w in parts[1:])
                        ctx[camel] = v
                    snake_singular = content_name.rstrip("s") if content_name.endswith("s") else content_name
                    parts = snake_singular.split("_")
                    camel_prefix = parts[0] + "".join(w.capitalize() for w in parts[1:])
                    prefixed = dict(ctx)
                    prefixed["updated"] = True
                    prefixed["created"] = True
                    ctx[camel_prefix] = prefixed
                    try:
                        if expr_eval.evaluate(ev["condition_expr"], ctx):
                            action = ev.get("action")
                            if action and action.get("column_mapping"):
                                cols = [p[0] for p in action["column_mapping"]]
                                vals = [record.get(p[1], "") for p in action["column_mapping"]]
                                placeholders = ", ".join("?" for _ in cols)
                                col_str = ", ".join(cols)
                                await db.execute(
                                    f'INSERT INTO {action["target_content"]} ({col_str}) VALUES ({placeholders})',
                                    tuple(vals)
                                )
                                await db.commit()
                            await event_bus.publish({"type": f"{ev.get('source_content', '')}_event", "log_level": ev.get("log_level", "INFO")})
                    except Exception:
                        pass

    # ── API routes from IR ──
    for route in ir.get("routes", []):
        content_ref = route.get("content_ref", "")
        method = route.get("method", "GET")
        path = route.get("path", "")
        kind = route.get("kind", "LIST")
        scope = route.get("scope") or route.get("required_scope")
        lookup_col = route.get("lookup_column", "id")
        target_state = route.get("target_state")

        if kind == "LIST":
            scope_dep = f'require_scope("{scope}")' if scope else None

            def make_list_route(p, cr, sc):
                deps = [Depends(require_scope(sc))] if sc else []
                @app.get(p, dependencies=deps)
                async def list_route(request: Request, _cr=cr):
                    db = await get_db(db_path)
                    try:
                        cursor = await db.execute(f"SELECT * FROM {_cr}")
                        rows = await cursor.fetchall()
                        records = [dict(r) for r in rows]
                        # Redact confidential fields
                        schema = content_lookup.get(_cr, {})
                        user = get_current_user(request)
                        user_scopes = set(user.get("scopes", []))
                        return redact_records(records, schema, user_scopes)
                    finally:
                        await db.close()
            make_list_route(path, content_ref, scope)

        elif kind == "CREATE":
            def make_create_route(p, cr, sc, sm_info):
                deps = [Depends(require_scope(sc))] if sc else []
                @app.post(p, status_code=201, dependencies=deps)
                async def create_route(request: Request, _cr=cr, _sm=sm_info):
                    body = await request.json()
                    # Set initial state from state machine (API creates)
                    # Always override — clients cannot set initial status directly
                    if _sm:
                        body["status"] = _sm.get("initial", "")
                    # Evaluate default_expr for missing fields
                    user = get_current_user(request)
                    default_ctx = {"User": user.get("User", {}), "now": __import__("datetime").datetime.utcnow().isoformat() + "Z", "today": __import__("datetime").date.today().isoformat()}
                    schema = content_lookup.get(_cr, {})
                    for field_def in schema.get("fields", []):
                        fname = field_def["name"]
                        dexpr = field_def.get("default_expr")
                        if dexpr and fname not in body:
                            try:
                                body[fname] = expr_eval.evaluate(dexpr, default_ctx)
                            except Exception:
                                pass
                    # Validate enum constraints
                    for field_def in schema.get("fields", []):
                        fname = field_def["name"]
                        enum_vals = field_def.get("enum_values", [])
                        if enum_vals and fname in body and body[fname]:
                            if body[fname] not in enum_vals:
                                raise HTTPException(
                                    status_code=422,
                                    detail=f"Invalid value '{body[fname]}' for {fname}. "
                                           f"Must be one of: {', '.join(enum_vals)}")
                    # Validate min/max constraints
                    for field_def in schema.get("fields", []):
                        fname = field_def["name"]
                        if fname not in body or body[fname] is None or body[fname] == "":
                            continue
                        try:
                            val = float(body[fname])
                        except (ValueError, TypeError):
                            continue
                        fmin = field_def.get("minimum")
                        fmax = field_def.get("maximum")
                        if fmin is not None and val < fmin:
                            raise HTTPException(
                                status_code=422,
                                detail=f"Value {val} for {fname} is below minimum {fmin}")
                        if fmax is not None and val > fmax:
                            raise HTTPException(
                                status_code=422,
                                detail=f"Value {val} for {fname} exceeds maximum {fmax}")
                    # Strip unknown fields (mass assignment protection)
                    known_fields = {f["name"] for f in schema.get("fields", [])}
                    known_fields.add("status")
                    body = {k: v for k, v in body.items() if k in known_fields}

                    db = await get_db(db_path)
                    try:
                        record = await create_record(db, _cr, body, schema, _sm, terminator, event_bus)
                        await run_event_handlers(db, _cr, "created", record)
                        # Redact confidential fields in response
                        user_scopes = set(user.get("scopes", []))
                        return redact_record(record, schema, user_scopes)
                    except Exception as e:
                        err_msg = str(e)
                        if "UNIQUE constraint" in err_msg:
                            raise HTTPException(status_code=409, detail=err_msg)
                        if "NOT NULL constraint" in err_msg:
                            raise HTTPException(status_code=400, detail=err_msg)
                        raise HTTPException(status_code=500, detail=err_msg)
                    finally:
                        await db.close()
            make_create_route(path, content_ref, scope, sm_lookup.get(content_ref))

        elif kind == "GET_ONE":
            def make_get_route(p, cr, sc, lc):
                deps = [Depends(require_scope(sc))] if sc else []
                @app.get(p, dependencies=deps)
                async def get_route(request: Request, _cr=cr, _lc=lc):
                    param_val = list(request.path_params.values())[0] if request.path_params else None
                    db = await get_db(db_path)
                    try:
                        record = await get_record(db, _cr, param_val, _lc)
                        # Redact confidential fields
                        schema = content_lookup.get(_cr, {})
                        user = get_current_user(request)
                        user_scopes = set(user.get("scopes", []))
                        return redact_record(record, schema, user_scopes)
                    finally:
                        await db.close()
            make_get_route(path, content_ref, scope, lookup_col)

        elif kind == "UPDATE":
            def make_update_route(p, cr, sc, lc):
                deps = [Depends(require_scope(sc))] if sc else []
                @app.put(p, dependencies=deps)
                async def update_route(request: Request, _cr=cr, _lc=lc):
                    param_val = list(request.path_params.values())[0] if request.path_params else None
                    body = await request.json()
                    # Check write access to confidential fields
                    user = get_current_user(request)
                    user_scopes = set(user.get("scopes", []))
                    schema = content_lookup.get(_cr, {})
                    write_err = check_write_access(body, schema, user_scopes)
                    if write_err:
                        raise HTTPException(status_code=403, detail=write_err)
                    db = await get_db(db_path)
                    try:
                        record = await update_record(db, _cr, param_val, body, _lc, terminator, event_bus)
                        await run_event_handlers(db, _cr, "updated", record)
                        return redact_record(record, schema, user_scopes)
                    finally:
                        await db.close()
            make_update_route(path, content_ref, scope, lookup_col)

        elif kind == "DELETE":
            def make_delete_route(p, cr, sc, lc):
                deps = [Depends(require_scope(sc))] if sc else []
                @app.delete(p, dependencies=deps)
                async def delete_route(request: Request, _cr=cr, _lc=lc):
                    param_val = list(request.path_params.values())[0] if request.path_params else None
                    db = await get_db(db_path)
                    try:
                        await delete_record(db, _cr, param_val, _lc, terminator, event_bus)
                        return {"deleted": True}
                    finally:
                        await db.close()
            make_delete_route(path, content_ref, scope, lookup_col)

        elif kind == "TRANSITION":
            def make_transition_route(p, cr, sc, lc, ts):
                deps = [Depends(require_scope(sc))] if sc else []
                @app.post(p, dependencies=deps)
                async def transition_route(request: Request, _cr=cr, _lc=lc, _ts=ts):
                    param_val = list(request.path_params.values())[0] if request.path_params else None
                    user = get_current_user(request)
                    db = await get_db(db_path)
                    try:
                        cursor = await db.execute(f"SELECT id, status FROM {_cr} WHERE {_lc} = ?", (param_val,))
                        row = await cursor.fetchone()
                        if not row:
                            raise HTTPException(status_code=404)
                        return await do_state_transition(db, _cr, row["id"], _ts, user, sm_lookup, terminator, event_bus)
                    finally:
                        await db.close()
            make_transition_route(path, content_ref, scope, lookup_col, target_state)

    # ── Reflection + Error + Events endpoints ──
    @app.get("/api/reflect")
    async def api_reflect():
        return json.loads(ir_json)

    @app.get("/api/reflect/content")
    async def api_reflect_content():
        return reflection.content_schemas()

    @app.get("/api/reflect/compute")
    async def api_reflect_compute():
        return reflection.compute_functions()

    @app.get("/api/errors")
    async def api_errors():
        return terminator.get_error_log()

    # ── Compute lookup ──
    compute_lookup = {}  # snake_name -> compute IR dict
    for comp in ir.get("computes", []):
        compute_lookup[comp["name"]["snake"]] = comp

    # ── Server-side Compute invocation endpoint ──
    @app.post("/api/v1/compute/{compute_name}")
    async def invoke_compute(compute_name: str, request: Request):
        """Execute a Compute server-side with confidentiality checks (Checks 1-4)."""
        comp = compute_lookup.get(compute_name)
        if not comp:
            raise HTTPException(status_code=404, detail=f"Compute '{compute_name}' not found")

        user = get_current_user(request)
        user_scopes = set(user.get("scopes", []))
        body = await request.json()
        input_data = body.get("input", {})

        # Check execution permission (existing scope check)
        req_scope = comp.get("required_scope")
        if req_scope and req_scope not in user_scopes:
            raise HTTPException(status_code=403, detail=f"Requires scope '{req_scope}' to execute")

        # Check 1: Confidentiality gate — delegate must have required scopes
        gate_err = check_compute_access(comp, user_scopes)
        if gate_err:
            terminator.route(TerminError(
                source=comp["name"]["display"], kind="confidentiality_gate_rejected",
                message=gate_err))
            raise HTTPException(status_code=403, detail=gate_err)

        # Check 2: Taint integrity — detect unredacted fields for unauthorized delegate
        if isinstance(input_data, list) and comp.get("identity_mode") == "service":
            # For service mode, check against delegate's scopes
            for input_content_name in comp.get("input_content", []):
                schema = content_lookup.get(input_content_name, {})
                taint_err = check_taint_integrity(input_data, schema, user_scopes)
                if taint_err:
                    terminator.route(TerminError(
                        source="confidentiality", kind="taint_violation",
                        message=taint_err))
                    raise HTTPException(status_code=500, detail=taint_err)

        # ── Transaction + Pre/Postcondition Framework ──

        # Create transaction for snapshot isolation
        tx = Transaction()

        # Build Compute execution context
        compute_ctx = {
            "Compute": {
                "Name": comp["name"]["display"],
                "Provider": comp.get("provider") or "cel",
                "IdentityMode": comp.get("identity_mode", "delegate"),
                "Scopes": list(user_scopes),
                "ExecutionId": tx.id,
                "Trigger": "api",
                "StartedAt": tx.started_at,
            },
            "User": user.get("User", {}),
        }

        # Evaluate preconditions
        for i, precond in enumerate(comp.get("preconditions", [])):
            try:
                result = expr_eval.evaluate(precond, compute_ctx)
                if not result:
                    tx.rollback()
                    detail = f"Precondition {i+1} failed: {precond}"
                    terminator.route(TerminError(
                        source=comp["name"]["display"], kind="precondition_failed",
                        message=detail))
                    raise HTTPException(status_code=412, detail=detail)
            except HTTPException:
                raise
            except Exception as e:
                tx.rollback()
                raise HTTPException(status_code=500, detail=f"Precondition evaluation error: {e}")

        # Execute the CEL body
        body_lines = comp.get("body_lines", [])
        if not body_lines:
            raise HTTPException(status_code=400, detail="Compute has no body to execute")

        cel_body = body_lines[0]  # First body line is the expression
        try:
            ctx = dict(compute_ctx)
            if isinstance(input_data, dict):
                ctx.update(input_data)
            elif isinstance(input_data, list):
                for input_name in comp.get("input_content", []):
                    ctx[input_name] = input_data

            # Check 3: CEL redaction guard — detect __redacted markers in context
            redacted_err = check_for_redacted_values(ctx)
            if redacted_err:
                tx.rollback()
                terminator.route(TerminError(
                    source="expression", kind="redacted_field_access",
                    message=redacted_err))
                raise HTTPException(status_code=500, detail=redacted_err)

            result = expr_eval.evaluate(cel_body, ctx)
        except HTTPException:
            raise
        except Exception as e:
            tx.rollback()
            raise HTTPException(status_code=500, detail=f"Compute evaluation failed: {e}")

        output = {"result": result, "transaction_id": tx.id}

        # Evaluate postconditions
        post_ctx = dict(compute_ctx)
        post_ctx["After"] = {"result": result}
        post_ctx["Before"] = {"result": None}  # simplified — full snapshot in E10 phase 2
        for i, postcond in enumerate(comp.get("postconditions", [])):
            try:
                check = expr_eval.evaluate(postcond, post_ctx)
                if not check:
                    tx.rollback()
                    detail = f"Postcondition {i+1} failed: {postcond}"
                    terminator.route(TerminError(
                        source=comp["name"]["display"], kind="postcondition_failed",
                        message=detail))
                    raise HTTPException(status_code=409, detail=detail)
            except HTTPException:
                raise
            except Exception:
                pass  # postcondition eval errors are non-fatal for now

        # Check 4: Output taint enforcement
        final_output, taint_err = enforce_output_taint(output, comp, user_scopes)
        if taint_err:
            tx.rollback()
            terminator.route(TerminError(
                source=comp["name"]["display"], kind="output_taint_blocked",
                message=taint_err))
            raise HTTPException(status_code=403, detail=taint_err)

        # Transaction succeeds — in a full implementation, tx.commit() would
        # write staged changes to production. For CEL-only Computes, there's
        # nothing to commit (the result is returned directly).

        return final_output

    @app.get("/api/events")
    async def api_events(level: str = Query(default=None)):
        log = event_bus.get_event_log()
        if level:
            order = {"TRACE": 0, "DEBUG": 1, "INFO": 2, "WARN": 3, "ERROR": 4}
            min_l = order.get(level.upper(), 0)
            log = [e for e in log if order.get(e.get("log_level", "INFO"), 2) >= min_l]
        return log

    # ── Generic transition endpoint (used by presentation action buttons) ──
    @app.post("/_transition/{content}/{record_id}/{target_state}")
    async def generic_transition(content: str, record_id: int, target_state: str,
                                 request: Request):
        """Presentation-layer transition by record ID. Converts underscores in
        target_state back to spaces for multi-word states."""
        target = target_state.replace("_", " ")
        user = get_current_user(request)
        db = await get_db(db_path)
        try:
            result = await do_state_transition(db, content, record_id, target, user,
                                               sm_lookup, terminator, event_bus)
            # Redirect back to referring page
            referer = request.headers.get("referer", "/")
            return RedirectResponse(url=referer, status_code=303)
        finally:
            await db.close()

    # ── SSE stream ──
    for stream in ir.get("streams", []):
        def make_sse(p):
            @app.get(p)
            async def sse_stream(request: Request, _p=p):
                async def generate():
                    q = event_bus.subscribe()
                    try:
                        while True:
                            event = await q.get()
                            yield f"data: {json.dumps(event)}\n\n"
                    except Exception:
                        event_bus.unsubscribe(q)
                return StreamingResponse(generate(), media_type="text/event-stream")
        make_sse(stream["path"])

    # ── Build client-side compute JS registrations ──
    # Registers compute functions in the CEL evaluation context.
    # Client-side computes are registered as JS functions on ctx.
    compute_js_parts = []
    for comp in ir.get("computes", []):
        body_lines = comp.get("body_lines", [])
        input_params = comp.get("input_params", [])
        if body_lines and input_params:
            param_name = input_params[0].get("name", "x") if input_params else "x"
            for line in body_lines:
                clean = line.strip().lstrip("[").rstrip("]").strip()
                import re as _re
                m = _re.match(r'(\w+)\s*=\s*(.*)', clean)
                if m:
                    expr = m.group(2).strip()
                    fname = comp["name"]["display"]
                    compute_js_parts.append(
                        f'ctx["{fname}"] = function({param_name}) {{ return {expr}; }};'
                    )
                    break
    _compute_js = "\n".join(compute_js_parts)

    # ── Presentation (pages) ──
    nav_html = build_nav_html(ir.get("nav_items", []), list(roles.keys()))
    base_template = build_base_template(app_name, nav_html)

    # Group pages by slug — merge same-slug pages for role-conditional rendering
    from .presentation import build_merged_page_template
    pages_by_slug: dict[str, list] = {}
    for page in ir.get("pages", []):
        pages_by_slug.setdefault(page["slug"], []).append(page)

    page_templates = {}
    for slug, pages_list in pages_by_slug.items():
        if len(pages_list) == 1:
            page_templates[slug] = build_page_template(pages_list[0])
        else:
            page_templates[slug] = build_merged_page_template(pages_list)

    # Home redirect
    if ir.get("pages"):
        first_slug = ir["pages"][0]["slug"]
        @app.get("/", response_class=HTMLResponse)
        async def home():
            return RedirectResponse(url=f"/{first_slug}")

    # ── Extract data requirements from component trees ──
    def _extract_page_reqs(page):
        """Walk component tree to find data sources, form targets, reference lists, etc."""
        reqs = {
            "sources": set(), "form_target": None, "ref_lists": set(),
            "create_as": None, "unique_fields": set(), "after_save": None,
        }
        def _walk(children):
            for child in (children or []):
                t = child.get("type", "")
                p = child.get("props", {})
                if t == "data_table":
                    src = p.get("source")
                    if src:
                        reqs["sources"].add(src)
                    _walk(child.get("children", []))
                elif t == "form":
                    reqs["form_target"] = p.get("target")
                    reqs["create_as"] = p.get("create_as")
                    reqs["after_save"] = p.get("after_save")
                    _walk(child.get("children", []))
                elif t == "field_input":
                    ref = p.get("reference_content")
                    if ref:
                        reqs["ref_lists"].add(ref)
                    if p.get("validate_unique"):
                        reqs["unique_fields"].add(p.get("field", ""))
                elif t in ("aggregation", "stat_breakdown"):
                    src = p.get("source")
                    if src:
                        reqs["sources"].add(src)
                elif t == "section":
                    _walk(child.get("children", []))
        _walk(page.get("children", []))
        return reqs

    # Page routes — one route per unique slug
    emitted_slugs: set = set()
    for page in ir.get("pages", []):
        slug = page["slug"]
        if slug in emitted_slugs:
            continue
        emitted_slugs.add(slug)
        reqs = _extract_page_reqs(page)

        def make_page_route(pg, sl, page_reqs):
            @app.get(f"/{sl}", response_class=HTMLResponse)
            async def page_route(request: Request, _pg=pg, _sl=sl, _reqs=page_reqs):
                user = get_current_user(request)
                q = request.query_params.get("q", "")
                db = await get_db(db_path)
                try:
                    # Build merged transition dict for action button rendering
                    all_transitions = {}
                    for sm_content, sm_data in sm_lookup.items():
                        all_transitions.update(sm_data.get("transitions", {}))

                    # Build CEL context for server-side text expression evaluation
                    cel_ctx = {
                        "User": user.get("User", {}),
                        "now": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                        "today": __import__("datetime").date.today().isoformat(),
                    }

                    def _termin_eval(expression):
                        """Evaluate a CEL expression server-side for text components."""
                        try:
                            return expr_eval.evaluate(expression, cel_ctx)
                        except Exception:
                            return "..."

                    ctx = {
                        "page_title": _pg["name"],
                        "current_role": user["role"],
                        "current_user_name": user["profile"]["DisplayName"],
                        "user_profile_json": json.dumps(user["profile"]),
                        "roles": list(roles.keys()),
                        "q": q,
                        "termin_compute_js": _compute_js,
                        "_sm_transitions": all_transitions,
                        "user_scopes": set(user["scopes"]),
                        "termin_eval": _termin_eval,
                    }

                    # Load data sources (data_table, aggregations)
                    user_scopes = set(user.get("scopes", []))
                    for src in _reqs["sources"]:
                        cursor = await db.execute(f"SELECT * FROM {src}")
                        rows = await cursor.fetchall()
                        records = [dict(r) for r in rows]
                        # Redact confidential fields for presentation
                        schema = content_lookup.get(src, {})
                        ctx["items"] = redact_records(records, schema, user_scopes)

                    # Form reference lists
                    for ref in _reqs["ref_lists"]:
                        ref_cursor = await db.execute(f"SELECT * FROM {ref}")
                        ctx[f"{ref}_list"] = [dict(r) for r in await ref_cursor.fetchall()]

                    content_html = page_templates[_sl].render(**ctx)
                    return base_template.render(content=content_html, **ctx)
                finally:
                    await db.close()
        make_page_route(page, slug, reqs)

        # Form POST route
        if reqs["form_target"]:
            def make_form_post(pg, sl, ft, sm_info, create_as, unique_fields, after_save):
                @app.post(f"/{sl}", response_class=HTMLResponse)
                async def form_post(request: Request, _pg=pg, _sl=sl, _ft=ft,
                                    _sm=sm_info, _ca=create_as,
                                    _uf=unique_fields, _as=after_save):
                    form = await request.form()
                    data = dict(form)
                    edit_id = data.pop("edit_id", "")
                    db = await get_db(db_path)
                    try:
                        schema = content_lookup.get(_ft, {})

                        # A7: Validate unique fields before insert
                        if not edit_id and _uf:
                            for uf in _uf:
                                val = data.get(uf, "")
                                if val:
                                    cursor = await db.execute(
                                        f"SELECT id FROM {_ft} WHERE {uf} = ?", (val,))
                                    existing = await cursor.fetchone()
                                    if existing:
                                        raise HTTPException(
                                            status_code=409,
                                            detail=f"A record with {uf} '{val}' already exists")

                        if edit_id:
                            await update_record(db, _ft, edit_id, data, "id", terminator, event_bus)
                        else:
                            # Evaluate default_expr for fields not in the form data
                            user = get_current_user(request)
                            default_ctx = {"User": user.get("User", {}), "now": __import__("datetime").datetime.utcnow().isoformat() + "Z", "today": __import__("datetime").date.today().isoformat()}
                            for field_def in schema.get("fields", []):
                                fname = field_def["name"]
                                dexpr = field_def.get("default_expr")
                                if dexpr and fname not in data:
                                    try:
                                        data[fname] = expr_eval.evaluate(dexpr, default_ctx)
                                    except Exception:
                                        pass  # Skip if expression fails

                            if _sm:
                                data["status"] = _sm.get("initial", "")
                            if _ca:
                                data["status"] = _ca
                            await create_record(db, _ft, data, schema, _sm, terminator, event_bus)

                        # A8: After-save navigation
                        redirect_url = f"/{_sl}"
                        if _as and _as.startswith("return_to:"):
                            target_slug = _as.split(":", 1)[1].strip()
                            redirect_url = f"/{target_slug}"
                        return RedirectResponse(url=redirect_url, status_code=303)
                    finally:
                        await db.close()
            make_form_post(page, slug, reqs["form_target"],
                          sm_lookup.get(reqs["form_target"]), reqs["create_as"],
                          reqs["unique_fields"], reqs["after_save"])

    return app
