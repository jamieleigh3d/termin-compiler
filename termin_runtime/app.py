"""Termin App Factory — creates a configured FastAPI app from IR JSON.

This is the main entry point for the Termin runtime. It reads the IR,
creates all subsystems, registers routes, and returns a FastAPI app.
"""

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, HTTPException, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from .expression import ExpressionEvaluator
from .errors import TerminError, TerminAtor
from .events import EventBus
from .identity import make_get_current_user, make_require_scope
from .storage import get_db, init_db, create_record, list_records, get_record, update_record, delete_record
from .state import do_state_transition
from .reflection import ReflectionEngine, register_reflection_with_expr_eval
from .presentation import build_base_template, build_nav_html, build_page_template, jinja_env


def create_termin_app(ir_json: str, db_path: str = None) -> FastAPI:
    """Create a fully configured FastAPI app from an IR JSON string."""
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
    require_scope = make_require_scope(get_current_user)

    # Content schemas for storage
    schemas = []
    content_lookup = {}  # snake_name -> schema dict
    sm_lookup = {}  # content_ref -> state machine
    for cs in ir.get("content", []):
        schemas.append(cs)
        content_lookup[cs["name"]["snake"]] = cs
    for sm in ir.get("state_machines", []):
        sm_lookup[sm["content_ref"]] = sm

    # Access grants for scope lookup
    def scope_for_content_verb(content_snake, verb):
        for g in ir.get("access_grants", []):
            if g["content"] == content_snake and verb in g["verbs"]:
                return g["scope"]
        return None

    # Register reflection with expression evaluator
    register_reflection_with_expr_eval(reflection, expr_eval)

    # ── Lifespan ──
    @asynccontextmanager
    async def lifespan(app):
        print(f"[Termin] Phase 0: Bootstrap")
        print(f"[Termin] Phase 1: TerminAtor initialized")
        print(f"[Termin] Phase 2: Expression evaluator ready")
        print(f"[Termin] Phase 3: Initializing storage")
        await init_db(schemas, db_path)
        print(f"[Termin] Phase 4: Registering primitives")
        print(f"[Termin] Phase 5: Ready to serve")
        yield
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

    # ── Event handlers ──
    async def run_event_handlers(db, content_name: str, trigger: str, record: dict):
        for ev in ir.get("events", []):
            if ev.get("trigger") == "jexl" and ev.get("jexl_condition"):
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
                        if expr_eval.evaluate(ev["jexl_condition"], ctx):
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
                        return [dict(r) for r in rows]
                    finally:
                        await db.close()
            make_list_route(path, content_ref, scope)

        elif kind == "CREATE":
            def make_create_route(p, cr, sc, sm_info):
                deps = [Depends(require_scope(sc))] if sc else []
                @app.post(p, status_code=201, dependencies=deps)
                async def create_route(request: Request, _cr=cr, _sm=sm_info):
                    body = await request.json()
                    db = await get_db(db_path)
                    try:
                        schema = content_lookup.get(_cr, {})
                        record = await create_record(db, _cr, body, schema, _sm, terminator)
                        await run_event_handlers(db, _cr, "created", record)
                        await event_bus.publish({"type": f"{_cr}_created", "id": record.get("id")})
                        return record
                    finally:
                        await db.close()
            make_create_route(path, content_ref, scope, sm_lookup.get(content_ref))

        elif kind == "GET_ONE":
            def make_get_route(p, cr, sc, lc):
                deps = [Depends(require_scope(sc))] if sc else []
                @app.get(p, dependencies=deps)
                async def get_route(request: Request, _cr=cr, _lc=lc, **kwargs):
                    param_val = list(request.path_params.values())[0] if request.path_params else None
                    db = await get_db(db_path)
                    try:
                        return await get_record(db, _cr, param_val, _lc)
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
                    db = await get_db(db_path)
                    try:
                        record = await update_record(db, _cr, param_val, body, _lc, terminator)
                        await run_event_handlers(db, _cr, "updated", record)
                        return record
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
                        await delete_record(db, _cr, param_val, _lc, terminator)
                        return {"deleted": True}
                    finally:
                        await db.close()
            make_delete_route(path, content_ref, scope, lookup_col)

        elif kind == "TRANSITION":
            def make_transition_route(p, cr, lc, ts):
                @app.post(p)
                async def transition_route(request: Request, _cr=cr, _lc=lc, _ts=ts):
                    param_val = list(request.path_params.values())[0] if request.path_params else None
                    user = get_current_user(request)
                    db = await get_db(db_path)
                    try:
                        cursor = await db.execute(f"SELECT id, status FROM {_cr} WHERE {_lc} = ?", (param_val,))
                        row = await cursor.fetchone()
                        if not row:
                            raise HTTPException(status_code=404)
                        sm = sm_lookup.get(_cr, {})
                        return await do_state_transition(db, _cr, row["id"], _ts, user, sm, terminator)
                    finally:
                        await db.close()
            make_transition_route(path, content_ref, lookup_col, target_state)

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

    @app.get("/api/events")
    async def api_events(level: str = Query(default=None)):
        log = event_bus.get_event_log()
        if level:
            order = {"TRACE": 0, "DEBUG": 1, "INFO": 2, "WARN": 3, "ERROR": 4}
            min_l = order.get(level.upper(), 0)
            log = [e for e in log if order.get(e.get("log_level", "INFO"), 2) >= min_l]
        return log

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
    compute_js_parts = []
    for comp in ir.get("computes", []):
        body_lines = comp.get("body_lines", [])
        input_params = comp.get("input_params", [])
        if body_lines and input_params:
            param_name = input_params[0].get("name", "x") if input_params else "x"
            # Compile body to JS: "greeting = expr" -> "return expr;"
            for line in body_lines:
                clean = line.strip().lstrip("[").rstrip("]").strip()
                import re as _re
                m = _re.match(r'(\w+)\s*=\s*(.*)', clean)
                if m:
                    expr = m.group(2).strip()
                    compute_js_parts.append(
                        f'jexl.addFunction("{comp["name"]["display"]}", function({param_name}) {{ return {expr}; }});'
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
        """Walk component tree to find data sources, form targets, reference lists."""
        reqs = {"sources": set(), "form_target": None, "ref_lists": set(), "create_as": None}
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
                    _walk(child.get("children", []))
                elif t == "field_input":
                    ref = p.get("reference_content")
                    if ref:
                        reqs["ref_lists"].add(ref)
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
                    ctx = {
                        "page_title": _pg["name"],
                        "current_role": user["role"],
                        "current_user_name": user["profile"]["DisplayName"],
                        "user_profile_json": json.dumps(user["profile"]),
                        "roles": list(roles.keys()),
                        "q": q,
                        "termin_compute_js": _compute_js,
                    }

                    # Load data sources (data_table, aggregations)
                    for src in _reqs["sources"]:
                        cursor = await db.execute(f"SELECT * FROM {src}")
                        rows = await cursor.fetchall()
                        ctx["items"] = [dict(r) for r in rows]

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
            def make_form_post(pg, sl, ft, sm_info, create_as):
                @app.post(f"/{sl}", response_class=HTMLResponse)
                async def form_post(request: Request, _pg=pg, _sl=sl, _ft=ft, _sm=sm_info, _ca=create_as):
                    form = await request.form()
                    data = dict(form)
                    edit_id = data.pop("edit_id", "")
                    db = await get_db(db_path)
                    try:
                        schema = content_lookup.get(_ft, {})
                        if edit_id:
                            await update_record(db, _ft, edit_id, data, "id", terminator)
                        else:
                            if _sm:
                                data["status"] = _sm.get("initial_state", "")
                            if _ca:
                                data["status"] = _ca
                            await create_record(db, _ft, data, schema, _sm, terminator)
                        return RedirectResponse(url=f"/{_sl}", status_code=303)
                    finally:
                        await db.close()
            make_form_post(page, slug, reqs["form_target"],
                          sm_lookup.get(reqs["form_target"]), reqs["create_as"])

    return app
