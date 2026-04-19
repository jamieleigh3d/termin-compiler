# Copyright 2026 Jamie-Leigh Blake
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Page route generation — presentation layer routing and form handling.

Registers page GET routes (with data loading, CEL evaluation, and template
rendering) and form POST routes (with validation, default evaluation, and
redirect logic).
"""

import json
import re

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from .context import RuntimeContext
from .storage import get_db, create_record, update_record, list_records, find_by_field
from .confidentiality import redact_records
from .presentation import build_nav_html, build_base_template, build_page_template, build_merged_page_template
from .validation import evaluate_field_defaults


def extract_page_reqs(page: dict) -> dict:
    """Walk component tree to find data sources, form targets, reference lists, etc."""
    reqs = {
        "sources": set(), "form_target": None, "ref_lists": set(),
        "create_as": None, "unique_fields": set(), "after_save": None,
    }

    def _walk(children):
        for child in (children or []):
            t = child.get("type", "")
            p = child.get("props", {})
            if t in ("data_table", "chat"):
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


def build_compute_js(ir: dict) -> str:
    """Build client-side compute JS registrations from IR."""
    parts = []
    for comp in ir.get("computes", []):
        body_lines = comp.get("body_lines", [])
        input_params = comp.get("input_params", [])
        if body_lines and input_params:
            param_name = input_params[0].get("name", "x") if input_params else "x"
            for line in body_lines:
                clean = line.strip().lstrip("[").rstrip("]").strip()
                m = re.match(r'(\w+)\s*=\s*(.*)', clean)
                if m:
                    expr = m.group(2).strip()
                    fname = comp["name"]["display"]
                    parts.append(
                        f'ctx["{fname}"] = function({param_name}) {{ return {expr}; }};')
                    break
    return "\n".join(parts)


def register_page_routes(app, ctx: RuntimeContext):
    """Register all page GET/POST routes."""

    nav_html = build_nav_html(ctx.ir.get("nav_items", []), list(ctx.roles.keys()))
    base_template = build_base_template(ctx.ir.get("name", "Termin App"), nav_html)

    # Group pages by slug
    pages_by_slug: dict[str, list] = {}
    for page in ctx.ir.get("pages", []):
        pages_by_slug.setdefault(page["slug"], []).append(page)

    page_templates = {}
    for slug, pages_list in pages_by_slug.items():
        if len(pages_list) == 1:
            page_templates[slug] = build_page_template(pages_list[0])
        else:
            page_templates[slug] = build_merged_page_template(pages_list)

    compute_js = build_compute_js(ctx.ir)

    # Home redirect
    if ctx.ir.get("pages"):
        first_slug = ctx.ir["pages"][0]["slug"]

        @app.get("/", response_class=HTMLResponse)
        async def home():
            return RedirectResponse(url=f"/{first_slug}")

    # Page routes — one per unique slug
    emitted_slugs: set = set()
    for page in ctx.ir.get("pages", []):
        slug = page["slug"]
        if slug in emitted_slugs:
            continue
        emitted_slugs.add(slug)
        reqs = extract_page_reqs(page)

        _register_page_get(app, ctx, page, slug, reqs, page_templates,
                           base_template, compute_js)

        if reqs["form_target"]:
            _register_form_post(app, ctx, page, slug, reqs)


def _register_page_get(app, ctx, page, slug, page_reqs, page_templates,
                       base_template, compute_js):
    @app.get(f"/{slug}", response_class=HTMLResponse)
    async def page_route(request: Request, _pg=page, _sl=slug, _reqs=page_reqs):
        user = ctx.get_current_user(request)
        q = request.query_params.get("q", "")
        db = await get_db(ctx.db_path)
        try:
            all_transitions = {}
            for sm_content, sm_data in ctx.sm_lookup.items():
                all_transitions.update(sm_data.get("transitions", {}))

            import datetime
            cel_ctx = {
                "User": user.get("User", {}),
                "now": datetime.datetime.utcnow().isoformat() + "Z",
                "today": datetime.date.today().isoformat(),
            }

            def _termin_eval(expression):
                try:
                    return ctx.expr_eval.evaluate(expression, cel_ctx)
                except Exception:
                    return "..."

            # Flash notification params
            flash_msg = request.query_params.get("_flash")
            flash_style = request.query_params.get("_flash_style", "toast")
            flash_level = request.query_params.get("_flash_level", "success")
            flash_dismiss = request.query_params.get("_flash_dismiss")

            template_ctx = {
                "page_title": _pg["name"],
                "current_role": user["role"],
                "current_user_name": user["profile"]["DisplayName"],
                "user_profile_json": json.dumps(user["profile"]),
                "roles": list(ctx.roles.keys()),
                "q": q,
                "termin_compute_js": compute_js,
                "_sm_transitions": all_transitions,
                "user_scopes": set(user["scopes"]),
                "termin_eval": _termin_eval,
                "flash_msg": flash_msg,
                "flash_style": flash_style,
                "flash_level": flash_level,
                "flash_dismiss": int(flash_dismiss) if flash_dismiss else None,
            }

            # Load data sources
            user_scopes = set(user.get("scopes", []))
            for src in _reqs["sources"]:
                records = await list_records(db, src)
                schema = ctx.content_lookup.get(src, {})
                template_ctx["items"] = redact_records(records, schema, user_scopes)

            # Form reference lists
            for ref in _reqs["ref_lists"]:
                template_ctx[f"{ref}_list"] = await list_records(db, ref)

            content_html = page_templates[_sl].render(**template_ctx)
            return base_template.render(content=content_html, **template_ctx)
        finally:
            await db.close()


def _register_form_post(app, ctx, page, slug, reqs):
    ft = reqs["form_target"]
    sm_info = ctx.sm_lookup.get(ft)
    create_as = reqs["create_as"]
    unique_fields = reqs["unique_fields"]
    after_save = reqs["after_save"]

    @app.post(f"/{slug}", response_class=HTMLResponse)
    async def form_post(request: Request, _pg=page, _sl=slug, _ft=ft,
                        _sm=sm_info, _ca=create_as,
                        _uf=unique_fields, _as=after_save):
        form = await request.form()
        data = dict(form)
        edit_id = data.pop("edit_id", "")
        db = await get_db(ctx.db_path)
        try:
            schema = ctx.content_lookup.get(_ft, {})

            # A7: Validate unique fields before insert
            if not edit_id and _uf:
                for uf in _uf:
                    val = data.get(uf, "")
                    if val:
                        existing = await find_by_field(db, _ft, uf, val)
                        if existing:
                            raise HTTPException(
                                status_code=409,
                                detail=f"A record with {uf} '{val}' already exists")

            if edit_id:
                await update_record(db, _ft, edit_id, data, "id",
                                    ctx.terminator, ctx.event_bus)
            else:
                user = ctx.get_current_user(request)
                evaluate_field_defaults(data, schema, ctx.expr_eval, user)

                if _sm:
                    data["status"] = _sm.get("initial", "")
                if _ca:
                    data["status"] = _ca
                record = await create_record(db, _ft, data, schema, _sm,
                                             ctx.terminator, ctx.event_bus)
                await ctx.run_event_handlers(db, _ft, "created", record)

            # AJAX response
            accept = request.headers.get("accept", "")
            is_ajax = ("application/json" in accept
                       or request.headers.get("x-requested-with", "").lower() == "xmlhttprequest")
            if is_ajax:
                if edit_id:
                    return JSONResponse({"ok": True, "id": edit_id, "action": "updated"})
                elif record:
                    return JSONResponse(record)
                else:
                    return JSONResponse({"ok": True})

            redirect_url = f"/{_sl}"
            if _as and _as.startswith("return_to:"):
                target_slug = _as.split(":", 1)[1].strip()
                redirect_url = f"/{target_slug}"
            return RedirectResponse(url=redirect_url, status_code=303)
        finally:
            await db.close()
