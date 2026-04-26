# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Termin App Factory — creates a configured FastAPI app from IR JSON.

This is the main entry point for the Termin runtime. It reads the IR,
creates all subsystems, registers routes, and returns a FastAPI app.

Subsystem modules:
  - context.py: RuntimeContext shared state
  - websocket_manager.py: ConnectionManager + WS multiplexer
  - boundaries.py: Block C boundary containment
  - validation.py: D-19 dependent values + constraints
  - compute_runner.py: LLM/Agent/CEL compute execution + D-20 audit
  - transitions.py: Toast/banner feedback + generic transition endpoint
  - routes.py: CRUD, reflection, channel, webhook endpoints
  - pages.py: Page rendering + form POST
"""

import asyncio
import json
import threading

from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse

from .context import RuntimeContext
from .expression import ExpressionEvaluator
from .errors import TerminAtor
from .events import EventBus
from .identity import make_get_current_user, make_require_scope, make_get_user_from_websocket
from .providers import Category, ContractRegistry, ProviderRegistry
from .providers.builtins import register_builtins as register_builtin_providers
from .storage import get_db, init_db, create_record, insert_raw, count_records
from .reflection import ReflectionEngine, register_reflection_with_expr_eval
from .channels import ChannelDispatcher, load_deploy_config, check_deploy_config_warnings
from .ai_provider import AIProvider
from .scheduler import Scheduler, parse_schedule_interval

# Subsystem modules
from .websocket_manager import ConnectionManager, register_websocket_routes
from .boundaries import build_boundary_maps
from .transitions import build_transition_feedback, register_transition_routes
from .routes import (
    register_crud_routes, register_reflection_routes, register_channel_routes,
    register_sse_routes, register_runtime_endpoints,
)
from .pages import register_page_routes
from .compute_runner import execute_compute, register_compute_endpoint


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

    # ── Build RuntimeContext ──
    # Resolve db_path immediately so ctx.db_path is never None — every
    # runtime caller passes ctx.db_path through to get_db, and we want
    # the value visible at app construction (not buried in storage's
    # fallback). DEFAULT_DB_PATH is "app.db" relative to cwd, matching
    # the historical behavior for users running `python app.py` directly.
    from .storage import DEFAULT_DB_PATH
    resolved_db_path = db_path if db_path else DEFAULT_DB_PATH
    ctx = RuntimeContext(ir=ir, ir_json=ir_json, db_path=resolved_db_path)

    # Subsystem initialization
    ctx.expr_eval = ExpressionEvaluator()
    ctx.terminator = TerminAtor()
    ctx.event_bus = EventBus()
    ctx.reflection = ReflectionEngine(ir_json)

    # Deploy config
    has_external_channels = any(
        ch.get("direction", "") != "INTERNAL"
        for ch in ir.get("channels", []))
    has_llm_computes = any(
        (c.get("provider") or "") in ("llm", "ai-agent")
        for c in ir.get("computes", []))
    needs_deploy_config = has_external_channels or has_llm_computes
    if deploy_config is None and needs_deploy_config:
        app_snake = ir.get("name", "app").lower().replace(" ", "_").replace("-", "_")
        deploy_config = load_deploy_config(path=deploy_config_path, app_name=app_snake)
    elif deploy_config is None:
        deploy_config = {}
    ctx.channel_dispatcher = ChannelDispatcher(ir, deploy_config)
    ctx.ai_provider = AIProvider(deploy_config)

    # Compute indexes
    for comp in ir.get("computes", []):
        ctx.compute_specs[comp["name"]["snake"]] = comp
        ctx.compute_lookup[comp["name"]["snake"]] = comp
        trigger = comp.get("trigger") or ""
        if trigger.startswith("event "):
            ctx.trigger_computes.append(comp)
        else:
            interval = parse_schedule_interval(trigger)
            if interval is not None:
                ctx.schedule_computes.append((comp, interval))

    # Boundary maps (Block C)
    ctx.boundary_for_content, ctx.boundary_for_compute, ctx.boundary_identity_scopes = \
        build_boundary_maps(ir)

    # Identity
    for role in ir.get("auth", {}).get("roles", []):
        ctx.roles[role["name"]] = role["scopes"]
    # v0.9: canonical Anonymous role name is capitalized "Anonymous".
    # If the source declared no anonymous role, synthesize an empty
    # one under the canonical name so role-key comparisons (template,
    # reflection, identity resolution) stay consistent.
    if not any(k.lower() == "anonymous" for k in ctx.roles):
        ctx.roles["Anonymous"] = []

    # v0.9 Phase 1: instantiate the bound IdentityProvider via the
    # provider registry. The runtime ships first-party providers
    # through the same registration path third-party providers will
    # use (BRD §10). Deploy config selects which product to bind;
    # in v0.9 the catalog is "stub" only (one product per contract,
    # no real auth providers yet) — Phase 2+ adds real bindings.
    ctx.contract_registry = ContractRegistry.default()
    ctx.provider_registry = ProviderRegistry()
    register_builtin_providers(ctx.provider_registry, ctx.contract_registry)
    identity_binding = (deploy_config or {}).get("bindings", {}).get("identity", {})
    identity_product = identity_binding.get("provider") or "stub"
    identity_config = identity_binding.get("config") or {}
    identity_record = ctx.provider_registry.get(
        Category.IDENTITY, "default", identity_product
    )
    if identity_record is None:
        # Per BRD §6.1 fail-closed: an unregistered identity product
        # is a deploy misconfiguration, not a fall-back-to-stub case.
        # Refuse to start so the operator catches the binding error
        # at deploy time rather than at the first auth-required
        # request. In production this is critical: a deploy that
        # silently falls back to a dev stub when the configured
        # SSO product is missing would be a security incident.
        available = ctx.provider_registry.list_products(
            Category.IDENTITY, "default"
        )
        raise RuntimeError(
            f"Identity provider {identity_product!r} is not registered. "
            f"Available: {sorted(available) or '<none>'}. "
            f"Either register the provider before calling "
            f"create_termin_app(), or update the deploy config "
            f"bindings.identity.provider to a registered product."
        )
    ctx.identity_provider = identity_record.factory(identity_config)

    app_id = ir.get("app_id", "") or ir.get("name", "") or ""
    ctx.get_current_user = make_get_current_user(
        ctx.roles, ctx.identity_provider, app_id,
    )
    ctx.get_user_from_ws = make_get_user_from_websocket(
        ctx.roles, ctx.identity_provider, app_id,
    )
    ctx.require_scope = make_require_scope(ctx.get_current_user)

    # Content lookups
    for cs in ir.get("content", []):
        snake = cs["name"]["snake"]
        ctx.content_lookup[snake] = cs
        if cs.get("singular"):
            ctx.singular_lookup[snake] = cs["singular"]

    # State machine lookup — v0.9 multi-SM shape.
    # Maps content_ref -> list[{machine_name, column, initial, transitions}].
    # A content with two state machines (e.g. lifecycle + approval status)
    # appears once with two list entries; the legacy one-SM-per-content
    # overwriting bug from v0.8 (sm_by_content[content] = sm) is gone.
    from collections import defaultdict
    _sm_by_content = defaultdict(list)
    for sm in ir.get("state_machines", []):
        col = sm["machine_name"]   # already snake_case in IR
        trans_dict = {
            (t["from_state"], t["to_state"]): t.get("required_scope", "")
            for t in sm.get("transitions", [])
        }
        _sm_by_content[sm["content_ref"]].append({
            "machine_name": col,
            "column": col,           # same as machine_name
            "initial": sm.get("initial_state", ""),
            "transitions": trans_dict,
        })
    ctx.sm_lookup = dict(_sm_by_content)

    # Transition feedback
    ctx.transition_feedback = build_transition_feedback(ir)

    # Register reflection with expression evaluator
    register_reflection_with_expr_eval(ctx.reflection, ctx.expr_eval)

    # WebSocket connection manager
    ctx.conn_manager = ConnectionManager()

    # ── Event handlers (needs access to ctx for singular_lookup, expr_eval, etc.) ──
    async def run_event_handlers(db, content_name: str, trigger: str, record: dict):
        for ev in ir.get("events", []):
            if ev.get("trigger") == "expr" and ev.get("condition_expr"):
                if content_name == ev.get("source_content", ""):
                    evctx = dict(record)
                    for k, v in list(record.items()):
                        parts = k.split("_")
                        camel = parts[0] + "".join(w.capitalize() for w in parts[1:])
                        evctx[camel] = v
                    snake_singular = ctx.singular_lookup.get(content_name, "")
                    if not snake_singular:
                        snake_singular = content_name.rstrip("s") if content_name.endswith("s") else content_name
                    parts = snake_singular.split("_")
                    camel_prefix = parts[0] + "".join(w.capitalize() for w in parts[1:])
                    prefixed = dict(evctx)
                    prefixed["updated"] = True
                    prefixed["created"] = True
                    evctx[camel_prefix] = prefixed
                    try:
                        if ctx.expr_eval.evaluate(ev["condition_expr"], evctx):
                            action = ev.get("action")
                            if action and action.get("column_mapping"):
                                insert_data = {p[0]: record.get(p[1], "") for p in action["column_mapping"]}
                                await insert_raw(db, action["target_content"], insert_data)
                            elif action and action.get("send_channel"):
                                def _sync_send(_action=action, _record=dict(record), _ev=ev):
                                    import httpx as _httpx
                                    ch_name = _action["send_channel"]
                                    try:
                                        config = ctx.channel_dispatcher.get_config(ch_name)
                                        if not config or not config.url:
                                            print(f"[Termin] Channel '{ch_name}': no deploy config, send skipped")
                                            return
                                        headers = ctx.channel_dispatcher._build_headers(config)
                                        resp = _httpx.post(config.url, json=_record, headers=headers,
                                                           timeout=config.timeout_ms / 1000.0)
                                        log = _ev.get("log_level", "INFO")
                                        print(f"[Termin] [{log}] Event sent {_action.get('send_content', 'record')} to channel '{ch_name}' (HTTP {resp.status_code})")
                                    except Exception as e:
                                        print(f"[Termin] [ERROR] Channel send to '{ch_name}' failed: {e}")
                                threading.Thread(target=_sync_send, daemon=True).start()
                            await ctx.event_bus.publish({
                                "type": f"{ev.get('source_content', '')}_event",
                                "log_level": ev.get("log_level", "INFO")})
                    except Exception as _ev_err:
                        print(f"[Termin] [WARN] Event handler error: {_ev_err}")

        # Event-triggered Computes (G6)
        event_type = f"{content_name.rstrip('s') if content_name.endswith('s') else content_name}.{trigger}"
        singular = ctx.singular_lookup.get(content_name, "")
        event_type_singular = f"{singular}.{trigger}" if singular else event_type

        for comp in ctx.trigger_computes:
            trigger_spec = comp.get("trigger", "")
            if trigger_spec.startswith("event "):
                trigger_event = trigger_spec[len("event "):].strip().strip('"')
                if trigger_event in (event_type, event_type_singular, f"{content_name}.{trigger}"):
                    where_expr = comp.get("trigger_where")
                    if where_expr:
                        wctx = dict(record)
                        snake_sing = ctx.singular_lookup.get(
                            content_name,
                            content_name.rstrip("s") if content_name.endswith("s") else content_name)
                        prefixed = dict(wctx)
                        prefixed["created"] = True
                        prefixed["updated"] = True
                        wctx[snake_sing] = prefixed
                        try:
                            if not ctx.expr_eval.evaluate(where_expr, wctx):
                                continue
                        except Exception:
                            continue

                    _main_loop = asyncio.get_event_loop()

                    def _run_compute(_comp=comp, _record=dict(record),
                                     _content=content_name, _loop=_main_loop):
                        import asyncio as _aio
                        bg_loop = _aio.new_event_loop()
                        try:
                            bg_loop.run_until_complete(
                                execute_compute(ctx, _comp, _record, _content, _loop))
                        except Exception as e:
                            print(f"[Termin] [ERROR] Compute '{_comp['name']['display']}' failed: {e}")
                        finally:
                            bg_loop.close()
                    threading.Thread(target=_run_compute, daemon=True).start()

    ctx.run_event_handlers = run_event_handlers
    ctx.execute_compute = lambda comp, record=None, content_name="", main_loop=None: \
        execute_compute(ctx, comp, record or {}, content_name, main_loop)

    # Content schemas for storage init
    schemas = list(ir.get("content", []))

    # ── Lifespan ──
    @asynccontextmanager
    async def lifespan(app):
        print(f"[Termin] Phase 0: Bootstrap")
        print(f"[Termin] Phase 1: TerminAtor initialized")
        print(f"[Termin] Phase 2: Expression evaluator ready")
        print(f"[Termin] Phase 3: Initializing storage")
        # Pass the resolved path (never None) so storage.init_db can't
        # fall through to its fallback constant — the app is the
        # authoritative source for db_path, not module state.
        await init_db(schemas, resolved_db_path)

        # Seed data
        if seed_data:
            db = await get_db(resolved_db_path)
            try:
                for content_name, records in seed_data.items():
                    cnt = await count_records(db, content_name)
                    if cnt == 0:
                        for record in records:
                            await insert_raw(db, content_name, record)
                        print(f"[Termin] Seeded {len(records)} records into {content_name}")
            finally:
                await db.close()

        print(f"[Termin] Phase 4: Registering primitives")

        # Inbound WebSocket handler for channel dispatcher
        async def _handle_inbound_ws(channel_name: str, data: dict):
            spec = ctx.channel_dispatcher.get_spec(channel_name)
            if not spec:
                return
            carries = spec.get("carries_content", "")
            if not carries:
                return
            schema = ctx.content_lookup.get(carries)
            if not schema:
                return
            known_cols = set()
            for f in schema.get("fields", []):
                fname = f.get("name", "")
                known_cols.add(fname if isinstance(fname, str) else fname.get("snake", ""))
            record_data = {k: v for k, v in data.items() if k in known_cols}
            if not record_data:
                return
            db = await get_db(resolved_db_path)
            try:
                record = await create_record(db, carries, record_data, ctx.sm_lookup.get(carries, []))
                await run_event_handlers(db, carries, "created", record)
                await ctx.event_bus.publish({
                    "channel_id": f"content.{carries}.created", "data": record})
                print(f"[Termin] Inbound WS '{channel_name}': created {carries} record (id={record.get('id', '?')})")
            finally:
                await db.close()

        ctx.channel_dispatcher.on_ws_message(_handle_inbound_ws)
        await ctx.channel_dispatcher.startup(strict=strict_channels)

        # AI provider
        ctx.ai_provider.startup()
        if ctx.ai_provider.is_configured:
            print(f"[Termin] Phase 4b: AI provider ready ({ctx.ai_provider.service}/{ctx.ai_provider.model})")
        elif ctx.trigger_computes:
            print(f"[Termin] Phase 4b: AI provider not configured — {len(ctx.trigger_computes)} LLM Compute(s) will be skipped")

        config_warnings = check_deploy_config_warnings(deploy_config, ir)
        for w in config_warnings:
            print(f"[Termin] WARNING: {w}")

        # Scheduler
        scheduler = Scheduler()
        for comp, interval in ctx.schedule_computes:
            scheduler.register(comp, interval, ctx.execute_compute)
        if scheduler.task_count:
            await scheduler.start()
            print(f"[Termin] Phase 4c: Scheduler started ({scheduler.task_count} task(s))")

        configured_channels = [
            ch["name"]["display"] for ch in ir.get("channels", [])
            if ctx.channel_dispatcher.is_configured(ch["name"]["display"])]
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
            q = ctx.event_bus.subscribe()
            try:
                while True:
                    event = await q.get()
                    ch_id = event.get("channel_id")
                    if ch_id:
                        await ctx.conn_manager.broadcast_to_subscribers(ch_id, event)
            except asyncio.CancelledError:
                pass
            finally:
                ctx.event_bus.unsubscribe(q)

        forwarder = asyncio.create_task(_ws_forwarder())
        print(f"[Termin] Phase 5: Ready to serve")
        yield
        forwarder.cancel()
        await scheduler.stop()
        await ctx.channel_dispatcher.shutdown()
        print(f"[Termin] Shutting down...")

    # ── Create FastAPI app ──
    app = FastAPI(title=app_name, lifespan=lifespan)

    # Set-role endpoint
    @app.post("/set-role")
    async def set_role(role: str = Form(...), user_name: str = Form("")):
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie("termin_role", role)
        if user_name:
            response.set_cookie("termin_user_name", user_name)
        return response

    # ── Register all subsystem routes ──
    register_runtime_endpoints(app, ctx)
    register_websocket_routes(app, ctx)
    register_crud_routes(app, ctx)
    register_reflection_routes(app, ctx)
    register_compute_endpoint(app, ctx)
    register_transition_routes(app, ctx)
    register_sse_routes(app, ctx)
    register_page_routes(app, ctx)
    register_channel_routes(app, ctx)

    # Stash the RuntimeContext on app.state for introspection by
    # tests, debugging tools, and runtime extension code that wants
    # to access ctx.identity_provider / ctx.contract_registry / etc.
    # Not part of the public ASGI contract — consumers using this
    # accept that the field is runtime-internal.
    app.state.ctx = ctx

    return app
