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
from .transaction import Transaction, ContentSnapshot
from .channels import ChannelDispatcher, ChannelError, ChannelScopeError, ChannelValidationError, ChannelConfigError, load_deploy_config, check_deploy_config_warnings
from .ai_provider import AIProvider, AIProviderError, build_output_tool, build_agent_tools
from .scheduler import Scheduler, parse_schedule_interval


def create_termin_app(ir_json: str, db_path: str = None, seed_data: dict = None,
                      deploy_config: dict = None, deploy_config_path: str = None,
                      strict_channels: bool = True) -> FastAPI:
    """Create a fully configured FastAPI app from an IR JSON string.

    Args:
        ir_json: The IR JSON string.
        db_path: Path to the SQLite database file.
        seed_data: Optional dict of {content_name: [record_dicts]} to seed on first run.
        deploy_config: Optional deploy config dict. Overrides file loading.
        deploy_config_path: Explicit path to deploy config file.
        strict_channels: If True, refuse to start if non-internal channels lack
                         deploy config. Set False for testing or dev mode.
    """
    ir = json.loads(ir_json)
    app_name = ir.get("name", "Termin App")

    # ── Subsystem initialization ──
    expr_eval = ExpressionEvaluator()
    terminator = TerminAtor()
    event_bus = EventBus()
    reflection = ReflectionEngine(ir_json)

    # Load app-specific deploy config
    has_external_channels = any(
        ch.get("direction", "") != "INTERNAL"
        for ch in ir.get("channels", [])
    )
    has_llm_computes = any(
        (c.get("provider") or "") in ("llm", "ai-agent")
        for c in ir.get("computes", [])
    )
    needs_deploy_config = has_external_channels or has_llm_computes
    if deploy_config is None and needs_deploy_config:
        app_snake = ir.get("name", "app").lower().replace(" ", "_").replace("-", "_")
        deploy_config = load_deploy_config(path=deploy_config_path, app_name=app_snake)
    elif deploy_config is None:
        deploy_config = {}
    channel_dispatcher = ChannelDispatcher(ir, deploy_config)

    # AI provider
    ai_provider = AIProvider(deploy_config)

    # Build Compute lookup with trigger index
    compute_specs = {}  # snake_name -> compute IR dict
    trigger_computes = []  # computes with event triggers
    schedule_computes = []  # computes with schedule triggers (interval_seconds, comp)
    for comp in ir.get("computes", []):
        compute_specs[comp["name"]["snake"]] = comp
        trigger = comp.get("trigger") or ""
        if trigger.startswith("event "):
            trigger_computes.append(comp)
        else:
            interval = parse_schedule_interval(trigger)
            if interval is not None:
                schedule_computes.append((comp, interval))

    # ── Block C: Boundary containment map ──
    # The app itself is always a boundary. Content not in any explicit sub-boundary
    # lives in the implicit app boundary "__app__". There is no "unrestricted" —
    # every content type and every Compute is in exactly one boundary.
    APP_BOUNDARY = "__app__"

    # Maps content_name (snake) -> boundary_name (snake)
    boundary_for_content: dict[str, str] = {}
    # Maps compute_name (snake) -> boundary_name (snake)
    boundary_for_compute: dict[str, str] = {}

    # Assign content to explicit sub-boundaries
    for bnd in ir.get("boundaries", []):
        bnd_snake = bnd["name"]["snake"]
        for content_snake in bnd.get("contains_content", []):
            boundary_for_content[content_snake] = bnd_snake

    # Content not in any explicit boundary → app boundary
    for ct in ir.get("content", []):
        ct_snake = ct["name"]["snake"]
        if ct_snake not in boundary_for_content:
            boundary_for_content[ct_snake] = APP_BOUNDARY

    # Infer boundary for each Compute from its Accesses: a Compute is "in" a boundary
    # if any of its Accesses content is in that boundary (first match wins)
    for comp in ir.get("computes", []):
        comp_snake = comp["name"]["snake"]
        for acc in comp.get("accesses", []):
            if acc in boundary_for_content:
                boundary_for_compute[comp_snake] = boundary_for_content[acc]
                break
        # Compute with no Accesses or no matched content → app boundary
        if comp_snake not in boundary_for_compute:
            boundary_for_compute[comp_snake] = APP_BOUNDARY

    def check_boundary_access(compute_snake: str, target_content: str) -> str | None:
        """Check if a Compute can access a content type across boundaries.

        Returns None if access is allowed, or an error message if denied.
        """
        compute_bnd = boundary_for_compute.get(compute_snake, APP_BOUNDARY)
        content_bnd = boundary_for_content.get(target_content, APP_BOUNDARY)
        # Same boundary → allow
        if compute_bnd == content_bnd:
            return None
        # Different boundary → reject
        return (f"Cross-boundary access denied: Compute '{compute_snake}' "
                f"(boundary '{compute_bnd}') cannot directly access "
                f"content '{target_content}' (boundary '{content_bnd}'). "
                f"Cross-boundary access requires a channel.")

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
    singular_lookup = {}  # snake_name -> singular (e.g. "echoes" -> "echo")
    sm_lookup = {}  # content_ref -> {"initial": str, "transitions": {(from,to): scope}}
    for cs in ir.get("content", []):
        schemas.append(cs)
        snake = cs["name"]["snake"]
        content_lookup[snake] = cs
        if cs.get("singular"):
            singular_lookup[snake] = cs["singular"]
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

    # ── D-19: Dependent value validation ──
    def validate_dependent_values(content_name: str, data: dict):
        """Validate dependent value constraints (When clauses) on create/update.

        Evaluates all matching When conditions and validates constraints.
        Raises HTTPException(422) if a must be one of or must be constraint is violated.
        """
        schema = content_lookup.get(content_name, {})
        dep_vals = schema.get("dependent_values", [])
        if not dep_vals:
            return

        # Also validate field-level one_of_values constraints
        for field_def in schema.get("fields", []):
            fname = field_def["name"]
            one_of = field_def.get("one_of_values", [])
            if one_of and fname in data and data[fname] is not None and data[fname] != "":
                val = data[fname]
                # Type-coerce for comparison
                if isinstance(one_of[0], (int, float)):
                    try:
                        val = type(one_of[0])(val)
                    except (ValueError, TypeError):
                        pass
                if val not in one_of:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Invalid value '{data[fname]}' for {fname}. "
                               f"Must be one of: {', '.join(str(v) for v in one_of)}")

        for dv in dep_vals:
            # Evaluate When condition (or unconditional if when is None)
            if dv.get("when"):
                try:
                    matched = expr_eval.evaluate(dv["when"], data)
                    if not matched:
                        continue  # Condition not met, skip this rule
                except Exception:
                    continue  # Eval error — skip silently

            field_name = dv["field"]
            constraint = dv["constraint"]

            if constraint == "one_of":
                allowed = list(dv.get("values", []))
                if field_name in data and data[field_name] is not None and data[field_name] != "":
                    val = data[field_name]
                    # Type-coerce for comparison
                    if allowed and isinstance(allowed[0], (int, float)):
                        try:
                            val = type(allowed[0])(val)
                        except (ValueError, TypeError):
                            pass
                    if val not in allowed:
                        when_desc = f" (when {dv['when']})" if dv.get("when") else ""
                        raise HTTPException(
                            status_code=422,
                            detail=f"Invalid value '{data[field_name]}' for {field_name}{when_desc}. "
                                   f"Must be one of: {', '.join(str(v) for v in allowed)}")

            elif constraint == "equals":
                required_val = dv.get("value")
                if field_name in data and data[field_name] is not None:
                    val = data[field_name]
                    if isinstance(required_val, (int, float)):
                        try:
                            val = type(required_val)(val)
                        except (ValueError, TypeError):
                            pass
                    if val != required_val:
                        when_desc = f" (when {dv['when']})" if dv.get("when") else ""
                        raise HTTPException(
                            status_code=422,
                            detail=f"Value for {field_name}{when_desc} must be {required_val}")

            elif constraint == "default":
                # Set default if field not provided
                default_val = dv.get("value")
                if field_name not in data or data[field_name] is None or data[field_name] == "":
                    data[field_name] = default_val

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
                                "payload": event.get("data") or event.get("record") or event,
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
        # Channel dispatcher startup
        # Register inbound WebSocket message handler — creates content records
        async def _handle_inbound_ws(channel_name: str, data: dict):
            spec = channel_dispatcher.get_spec(channel_name)
            if not spec:
                return
            carries = spec.get("carries_content", "")
            if not carries:
                return  # action-only channel, no content creation
            schema = content_lookup.get(carries)
            if not schema:
                return
            known_cols = set()
            for f in schema.get("fields", []):
                fname = f.get("name", "")
                known_cols.add(fname if isinstance(fname, str) else fname.get("snake", ""))
            record_data = {k: v for k, v in data.items() if k in known_cols}
            if not record_data:
                return
            db = await get_db(db_path)
            try:
                record = await create_record(db, carries, record_data, sm_lookup.get(carries))
                await run_event_handlers(db, carries, "created", record)
                await event_bus.publish({"channel_id": f"content.{carries}.created", "data": record})
                print(f"[Termin] Inbound WS '{channel_name}': created {carries} record (id={record.get('id', '?')})")
            finally:
                await db.close()

        channel_dispatcher.on_ws_message(_handle_inbound_ws)
        await channel_dispatcher.startup(strict=strict_channels)
        # AI provider startup
        ai_provider.startup()
        if ai_provider.is_configured:
            print(f"[Termin] Phase 4b: AI provider ready ({ai_provider.service}/{ai_provider.model})")
        elif trigger_computes:
            print(f"[Termin] Phase 4b: AI provider not configured — {len(trigger_computes)} LLM Compute(s) will be skipped")
        # Warn about unset env vars or uncustomized placeholders
        config_warnings = check_deploy_config_warnings(deploy_config, ir)
        for w in config_warnings:
            print(f"[Termin] WARNING: {w}")
        # Schedule-triggered Computes
        scheduler = Scheduler()
        for comp, interval in schedule_computes:
            scheduler.register(comp, interval, _execute_compute)
        if scheduler.task_count:
            await scheduler.start()
            print(f"[Termin] Phase 4c: Scheduler started ({scheduler.task_count} task(s))")

        configured_channels = [ch["name"]["display"] for ch in ir.get("channels", []) if channel_dispatcher.is_configured(ch["name"]["display"])]
        if configured_channels:
            print(f"[Termin] Phase 4a: Channels connected: {', '.join(configured_channels)}")
        elif ir.get("channels"):
            internal_only = all(ch.get("direction") == "INTERNAL" for ch in ir.get("channels", []))
            if internal_only:
                print(f"[Termin] Phase 4a: {len(ir['channels'])} internal channel(s)")
            else:
                print(f"[Termin] Phase 4a: {len(ir['channels'])} channel(s) declared (no deploy config)")
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
        await scheduler.stop()
        await channel_dispatcher.shutdown()
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
                            headers={"Cache-Control": "no-cache"})
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
                    # Use authoritative singular from IR, fall back to naive strip
                    snake_singular = singular_lookup.get(content_name, "")
                    if not snake_singular:
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
                                # Create action: insert into target content
                                cols = [p[0] for p in action["column_mapping"]]
                                vals = [record.get(p[1], "") for p in action["column_mapping"]]
                                placeholders = ", ".join("?" for _ in cols)
                                col_str = ", ".join(cols)
                                await db.execute(
                                    f'INSERT INTO {action["target_content"]} ({col_str}) VALUES ({placeholders})',
                                    tuple(vals)
                                )
                                await db.commit()
                            elif action and action.get("send_channel"):
                                # Channel send action: dispatch record to channel
                                # Fire-and-forget via background task. Uses a fresh
                                # sync httpx client in a thread to avoid event loop
                                # deadlocks when the channel target is the same server.
                                import threading
                                def _sync_send(_action=action, _record=dict(record), _ev=ev):
                                    import httpx as _httpx
                                    ch_name = _action["send_channel"]
                                    try:
                                        config = channel_dispatcher.get_config(ch_name)
                                        if not config or not config.url:
                                            print(f"[Termin] Channel '{ch_name}': no deploy config, send skipped")
                                            return
                                        headers = channel_dispatcher._build_headers(config)
                                        resp = _httpx.post(config.url, json=_record, headers=headers, timeout=config.timeout_ms / 1000.0)
                                        log = _ev.get("log_level", "INFO")
                                        print(f"[Termin] [{log}] Event sent {_action.get('send_content', 'record')} to channel '{ch_name}' (HTTP {resp.status_code})")
                                    except Exception as e:
                                        print(f"[Termin] [ERROR] Channel send to '{ch_name}' failed: {e}")
                                threading.Thread(target=_sync_send, daemon=True).start()
                            await event_bus.publish({"type": f"{ev.get('source_content', '')}_event", "log_level": ev.get("log_level", "INFO")})
                    except Exception as _ev_err:
                        print(f"[Termin] [WARN] Event handler error: {_ev_err}")

        # Check event-triggered Computes (G6)
        event_type = f"{content_name.rstrip('s') if content_name.endswith('s') else content_name}.{trigger}"
        # Also use authoritative singular
        singular = singular_lookup.get(content_name, "")
        if singular:
            event_type_singular = f"{singular}.{trigger}"
        else:
            event_type_singular = event_type

        for comp in trigger_computes:
            trigger_spec = comp.get("trigger", "")
            # Parse "event "X.Y"" to match
            if trigger_spec.startswith("event "):
                trigger_event = trigger_spec[6:].strip().strip('"')
                if trigger_event == event_type or trigger_event == event_type_singular or trigger_event == f"{content_name}.{trigger}":
                    # Check where clause
                    where_expr = comp.get("trigger_where")
                    if where_expr:
                        ctx = dict(record)
                        snake_sing = singular_lookup.get(content_name, content_name.rstrip("s") if content_name.endswith("s") else content_name)
                        prefixed = dict(ctx)
                        prefixed["created"] = True
                        prefixed["updated"] = True
                        ctx[snake_sing] = prefixed
                        try:
                            if not expr_eval.evaluate(where_expr, ctx):
                                continue  # Where clause filtered this event
                        except Exception:
                            continue  # Expression error — skip

                    # Fire the Compute in a background thread.
                    # Capture the main event loop so the background thread can
                    # publish events back to WebSocket subscribers.
                    import threading
                    _main_loop = asyncio.get_event_loop()
                    def _run_compute(_comp=comp, _record=dict(record), _content=content_name, _loop=_main_loop):
                        import asyncio as _aio
                        bg_loop = _aio.new_event_loop()
                        try:
                            bg_loop.run_until_complete(_execute_compute(_comp, _record, _content, _loop))
                        except Exception as e:
                            print(f"[Termin] [ERROR] Compute '{_comp['name']['display']}' failed: {e}")
                        finally:
                            bg_loop.close()
                    threading.Thread(target=_run_compute, daemon=True).start()

    async def _execute_compute(comp: dict, record: dict, content_name: str, main_loop=None):
        """Execute a Compute triggered by an event."""
        comp_name = comp["name"]["display"]
        provider = comp.get("provider", "cel")

        if provider == "llm":
            await _execute_llm_compute(comp, record, content_name, main_loop)
        elif provider == "ai-agent":
            await _execute_agent_compute(comp, record, content_name, main_loop)
        else:
            print(f"[Termin] Compute '{comp_name}': provider '{provider}' not supported for event triggers")

    async def _execute_llm_compute(comp: dict, record: dict, content_name: str, main_loop=None):
        """Execute a Level 1 LLM Compute — field-to-field completion."""
        comp_name = comp["name"]["display"]

        if not ai_provider.is_configured:
            print(f"[Termin] Compute '{comp_name}': AI provider not configured, skipped")
            return

        # Read input fields from record
        input_values = {}
        for content_ref, field_name in comp.get("input_fields", []):
            if field_name in record:
                input_values[field_name] = record[field_name]

        # Build prompts
        directive = comp.get("directive", "You are a helpful assistant.")
        objective = comp.get("objective", "")

        # Interpolate inline expressions in objective (field references)
        for fname, fval in input_values.items():
            # Simple interpolation: replace field references in objective
            singular = singular_lookup.get(content_name, content_name.rstrip("s") if content_name.endswith("s") else content_name)
            objective = objective.replace(f"{singular}.{fname}", str(fval))

        # Build user message from input fields
        if input_values:
            user_msg = objective + "\n\n" + "\n".join(f"{k}: {v}" for k, v in input_values.items())
        else:
            user_msg = objective

        # Build output tool
        output_fields = comp.get("output_fields", [])
        output_tool = build_output_tool(output_fields, content_lookup)

        print(f"[Termin] Compute '{comp_name}': calling {ai_provider.service} (record {record.get('id', '?')})")

        try:
            result = await ai_provider.complete(directive, user_msg, output_tool)
            thinking = result.pop("thinking", "")
            if thinking:
                print(f"[Termin] Compute '{comp_name}' thinking: {thinking[:100]}")

            # Write output fields back to the record
            if output_fields and record.get("id"):
                update_data = {}
                for content_ref, field_name in output_fields:
                    if field_name in result:
                        update_data[field_name] = result[field_name]
                if update_data:
                    db = await get_db(db_path)
                    try:
                        sets = ", ".join(f"{k} = ?" for k in update_data)
                        vals = list(update_data.values()) + [record["id"]]
                        await db.execute(f"UPDATE {content_name} SET {sets} WHERE id = ?", tuple(vals))
                        await db.commit()
                        print(f"[Termin] Compute '{comp_name}': updated record {record['id']}")
                        # Publish update event for WebSocket subscribers.
                        # Use the main event loop since we're in a background thread.
                        updated_record = dict(record)
                        updated_record.update(update_data)
                        event_data = {
                            "channel_id": f"content.{content_name}.updated",
                            "data": updated_record,
                        }
                        if main_loop and main_loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                event_bus.publish(event_data), main_loop
                            )
                        else:
                            await event_bus.publish(event_data)
                    finally:
                        await db.close()
        except AIProviderError as e:
            print(f"[Termin] [ERROR] Compute '{comp_name}': {e}")

    async def _execute_agent_compute(comp: dict, record: dict, content_name: str, main_loop=None):
        """Execute a Level 3 Agent Compute — autonomous with tool calls."""
        comp_name = comp["name"]["display"]

        if not ai_provider.is_configured:
            print(f"[Termin] Compute '{comp_name}': AI provider not configured, skipped")
            return

        # Build prompts
        directive = comp.get("directive", "You are a helpful AI agent.")
        objective = comp.get("objective", "")
        accesses = comp.get("accesses", [])

        # Build tools
        agent_tools = build_agent_tools(accesses, content_lookup)
        # Add set_output tool for completion signal
        set_output = {
            "name": "set_output",
            "description": "Signal that you have completed the task. Call this when done.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "thinking": {"type": "string", "description": "Brief summary of what you did."},
                    "summary": {"type": "string", "description": "Result summary."},
                },
                "required": ["thinking"],
            }
        }
        all_tools = agent_tools + [set_output]

        # Build user message with context
        user_msg = f"{objective}\n\nTriggering record:\n{json.dumps(record, indent=2, default=str)}"

        # Tool execution function
        comp_snake = comp["name"]["snake"]
        async def execute_tool(tool_name: str, tool_input: dict) -> dict:
            db = await get_db(db_path)
            try:
                if tool_name == "content_query":
                    cname = tool_input.get("content_name", "")
                    if cname not in accesses:
                        return {"error": f"Access denied: {cname} not in Accesses"}
                    bnd_err = check_boundary_access(comp_snake, cname)
                    if bnd_err:
                        return {"error": bnd_err}
                    filters = tool_input.get("filters", {})
                    if filters:
                        where = " AND ".join(f"{k} = ?" for k in filters)
                        cursor = await db.execute(f"SELECT * FROM {cname} WHERE {where}", tuple(filters.values()))
                    else:
                        cursor = await db.execute(f"SELECT * FROM {cname}")
                    return [dict(r) for r in await cursor.fetchall()]

                elif tool_name == "content_create":
                    cname = tool_input.get("content_name", "")
                    if cname not in accesses:
                        return {"error": f"Access denied: {cname} not in Accesses"}
                    bnd_err = check_boundary_access(comp_snake, cname)
                    if bnd_err:
                        return {"error": bnd_err}
                    data = tool_input.get("data", {})
                    sm_info = sm_lookup.get(cname)
                    if sm_info:
                        data["status"] = sm_info.get("initial", "")
                    schema = content_lookup.get(cname, {})
                    rec = await create_record(db, cname, data, schema, sm_info, terminator, event_bus)
                    # DON'T call run_event_handlers here to avoid recursive agent invocation
                    # Publish to main loop for WebSocket subscribers
                    event_data = {"channel_id": f"content.{cname}.created", "data": rec}
                    if main_loop and main_loop.is_running():
                        asyncio.run_coroutine_threadsafe(event_bus.publish(event_data), main_loop)
                    else:
                        await event_bus.publish(event_data)
                    return rec

                elif tool_name == "content_update":
                    cname = tool_input.get("content_name", "")
                    if cname not in accesses:
                        return {"error": f"Access denied: {cname} not in Accesses"}
                    bnd_err = check_boundary_access(comp_snake, cname)
                    if bnd_err:
                        return {"error": bnd_err}
                    rid = tool_input.get("record_id")
                    data = tool_input.get("data", {})
                    await update_record(db, cname, rid, data, "id", terminator, event_bus)
                    return {"ok": True, "id": rid}

                elif tool_name == "state_transition":
                    cname = tool_input.get("content_name", "")
                    if cname not in accesses:
                        return {"error": f"Access denied: {cname} not in Accesses"}
                    bnd_err = check_boundary_access(comp_snake, cname)
                    if bnd_err:
                        return {"error": bnd_err}
                    rid = tool_input.get("record_id")
                    target = tool_input.get("target_state")
                    result = await do_state_transition(db, cname, rid, target,
                                                       {"role": "service", "scopes": list(scope_for_content_verb(cname, "update") or [])},
                                                       sm_lookup, terminator, event_bus)
                    return result

                else:
                    return {"error": f"Unknown tool: {tool_name}"}
            finally:
                await db.close()

        print(f"[Termin] Compute '{comp_name}': starting agent loop ({ai_provider.service})")

        try:
            result = await ai_provider.agent_loop(directive, user_msg, all_tools, execute_tool)
            thinking = result.get("thinking", "")
            if thinking:
                print(f"[Termin] Compute '{comp_name}' completed: {thinking[:100]}")
        except AIProviderError as e:
            print(f"[Termin] [ERROR] Compute '{comp_name}': {e}")

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
                    # D-19: Validate dependent value constraints
                    validate_dependent_values(_cr, body)

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
                    # D-19: Validate dependent value constraints
                    # For updates, merge with existing data for condition evaluation
                    db = await get_db(db_path)
                    try:
                        existing = await get_record(db, _cr, param_val, _lc)
                        if existing:
                            merged = dict(existing)
                            merged.update(body)
                            validate_dependent_values(_cr, merged)
                        else:
                            validate_dependent_values(_cr, body)
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

    @app.get("/api/reflect/roles")
    async def api_reflect_roles():
        return reflection.roles()

    @app.get("/api/reflect/roles/{role_name}")
    async def api_reflect_role(role_name: str):
        role = reflection.role(role_name)
        if not role:
            raise HTTPException(status_code=404, detail=f"Role '{role_name}' not found")
        return role

    @app.get("/api/reflect/channels")
    async def api_reflect_channels():
        """Live channel status: connection state, protocol, metrics."""
        return channel_dispatcher.get_full_status()

    @app.get("/api/reflect/channels/{channel_name}")
    async def api_reflect_channel(channel_name: str):
        spec = channel_dispatcher.get_spec(channel_name)
        if not spec:
            raise HTTPException(status_code=404, detail=f"Channel '{channel_name}' not found")
        display = spec["name"]["display"]
        config = channel_dispatcher.get_config(channel_name)
        return {
            "name": display,
            "direction": spec.get("direction", ""),
            "delivery": spec.get("delivery", ""),
            "carries": spec.get("carries_content", ""),
            "actions": [a["name"]["display"] for a in spec.get("actions", [])],
            "configured": channel_dispatcher.is_configured(channel_name),
            "state": channel_dispatcher.get_connection_state(channel_name),
            "protocol": config.protocol if config else "none",
            "metrics": channel_dispatcher.get_metrics(channel_name),
        }

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

        # Block C: Boundary enforcement for CEL Compute content access
        comp_snake_name = comp["name"]["snake"]
        for acc_content in comp.get("accesses", []):
            bnd_err = check_boundary_access(comp_snake_name, acc_content)
            if bnd_err:
                tx.rollback()
                raise HTTPException(status_code=403, detail=bnd_err)

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

        # Build Before/After snapshots for postcondition evaluation
        # Before: frozen read-only snapshot of content state at transaction start
        # After: current state (staging merged with DB) at postcondition evaluation time
        before_data = {"result": None}
        after_data = {"result": result}

        # Load content data for input/output content types referenced by this Compute
        try:
            db = await get_db(db_path)
            all_content_refs = set(
                comp.get("input_content", [])
                + comp.get("output_content", [])
                + comp.get("accesses", [])
            )
            for content_name in all_content_refs:
                cursor = await db.execute(f"SELECT * FROM {content_name}")
                rows = await cursor.fetchall()
                records = [dict(r) for r in rows]
                before_data[content_name] = records
                # After = production records with staged changes applied
                after_data[content_name] = await tx.read_all(content_name, records)
            await db.close()
        except Exception:
            pass  # If content loading fails, postconditions use simplified snapshots

        # Also store as ContentSnapshot objects for Python-side use
        before_snapshot_obj = ContentSnapshot(
            {k: v for k, v in before_data.items() if k != "result"}, result=None)
        after_snapshot_obj = ContentSnapshot(
            {k: v for k, v in after_data.items() if k != "result"}, result=result)

        # Evaluate postconditions — Before/After are CEL-compatible dicts
        # with content_query available as a registered function
        post_ctx = dict(compute_ctx)
        post_ctx["After"] = after_data
        post_ctx["Before"] = before_data
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
                            record = await create_record(db, _ft, data, schema, _sm, terminator, event_bus)
                            await run_event_handlers(db, _ft, "created", record)

                        # Check if this is an AJAX request — return JSON instead of redirect
                        accept = request.headers.get("accept", "")
                        is_ajax = ("application/json" in accept
                                   or request.headers.get("x-requested-with", "").lower() == "xmlhttprequest")
                        if is_ajax:
                            from fastapi.responses import JSONResponse
                            if edit_id:
                                return JSONResponse({"ok": True, "id": edit_id, "action": "updated"})
                            elif record:
                                return JSONResponse(record)
                            else:
                                return JSONResponse({"ok": True})
                        # A8: After-save navigation (traditional form submit)
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

    # ── Channel action invocation endpoint ──
    @app.post("/api/v1/channels/{channel_name}/actions/{action_name}")
    async def invoke_channel_action(channel_name: str, action_name: str, request: Request):
        """Invoke a typed action on an external channel."""
        user = get_current_user(request)
        user_scopes = set(user.get("scopes", []))

        spec = channel_dispatcher.get_spec(channel_name)
        if not spec:
            raise HTTPException(status_code=404, detail=f"Channel '{channel_name}' not found")

        action_spec = channel_dispatcher.get_action_spec(channel_name, action_name)
        if not action_spec:
            raise HTTPException(status_code=404, detail=f"Action '{action_name}' not found on channel '{channel_name}'")

        try:
            body = await request.json()
        except Exception:
            body = {}

        try:
            result = await channel_dispatcher.channel_invoke(
                channel_name, action_name, body, user_scopes=user_scopes
            )
            return result
        except ChannelScopeError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ChannelValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except ChannelError as e:
            raise HTTPException(status_code=502, detail=str(e))

    # ── Channel send endpoint ──
    @app.post("/api/v1/channels/{channel_name}/send")
    async def channel_send_endpoint(channel_name: str, request: Request):
        """Send data through an outbound channel."""
        user = get_current_user(request)
        user_scopes = set(user.get("scopes", []))

        spec = channel_dispatcher.get_spec(channel_name)
        if not spec:
            raise HTTPException(status_code=404, detail=f"Channel '{channel_name}' not found")

        try:
            body = await request.json()
        except Exception:
            body = {}

        try:
            result = await channel_dispatcher.channel_send(
                channel_name, body, user_scopes=user_scopes
            )
            return result
        except ChannelScopeError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ChannelError as e:
            raise HTTPException(status_code=502, detail=str(e))

    # ── Inbound webhook handler ──
    for ch in ir.get("channels", []):
        ch_direction = ch.get("direction", "")
        if ch_direction not in ("INBOUND", "BIDIRECTIONAL"):
            continue
        ch_display = ch["name"]["display"]
        ch_snake = ch["name"]["snake"]
        ch_carries = ch.get("carries_content", "")
        if not ch_carries:
            continue  # action-only channels don't receive data

        webhook_path = f"/webhooks/{ch_snake}"

        def _make_webhook(ch_name=ch_display, ch_content=ch_carries, ch_spec=ch):
            @app.post(webhook_path, name=f"webhook_{ch_snake}")
            async def webhook_receive(request: Request):
                # Scope check: use send requirements (sender must have scope to push data)
                user = get_current_user(request)
                user_scopes = set(user.get("scopes", []))
                for req in ch_spec.get("requirements", []):
                    if req["direction"] == "send" and req["scope"] not in user_scopes:
                        raise HTTPException(
                            status_code=403,
                            detail=f"Scope '{req['scope']}' required to send to channel '{ch_name}'"
                        )

                try:
                    body = await request.json()
                except Exception:
                    raise HTTPException(status_code=400, detail="Invalid JSON payload")

                # Create record in the carried content table
                schema = content_lookup.get(ch_content)
                if not schema:
                    raise HTTPException(status_code=500, detail=f"Content '{ch_content}' not found")

                # Filter body to known columns
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

                db = await get_db(db_path)
                try:
                    record = await create_record(db, ch_content, record_data, sm_lookup.get(ch_content))
                    await run_event_handlers(db, ch_content, "created", record)

                    # Update channel metrics
                    channel_dispatcher._metrics.get(ch_name, {})["received"] = \
                        channel_dispatcher._metrics.get(ch_name, {}).get("received", 0) + 1

                    # Publish to event bus for WebSocket subscribers
                    await event_bus.publish({
                        "channel_id": f"content.{ch_content}.created",
                        "data": record,
                    })

                    print(f"[Termin] Webhook '{ch_name}': created {ch_content} record (id={record.get('id', '?')})")
                    return {"ok": True, "id": record.get("id"), "channel": ch_name}
                finally:
                    await db.close()

        _make_webhook()
        print(f"[Termin] Registered webhook: POST {webhook_path} -> {ch_carries}")

    return app
