# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
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
from .providers import Eq, QueryOptions
from .confidentiality import redact_records
from .presentation import build_nav_html, build_base_template, build_page_template, build_merged_page_template
from .validation import evaluate_field_defaults
from .bootstrap import page_should_use_shell, render_shell_response


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
        # v0.9 Phase 5b.4 B' loop: page-route cut-over. When the bound
        # presentation provider for `presentation-base.page` is CSR-only
        # (e.g., spectrum), the SSR-Tailwind pipeline below would render
        # markup the bundle then has to reconcile React against — wasted
        # work and a flash of wrong content. Short-circuit to the shell
        # HTML in that case; the bundle's renderer takes over from the
        # bootstrap payload. Per the page_should_use_shell contract this
        # is a no-op when no CSR-only provider is bound (legacy default).
        if page_should_use_shell(ctx):
            return await render_shell_response(ctx, request, f"/{_sl}")

        user = ctx.get_current_user(request)
        q = request.query_params.get("q", "")
        db = await get_db(ctx.db_path)
        try:
            all_transitions = {}
            # Per-content transition lists, used by the edit modal JS to
            # filter state dropdowns to valid targets reachable from the
            # current row state and allowed by the user's scopes.
            # Shape: {content_ref: [{from, to, scope, machine_name}, ...]}.
            # With multi-SM the list may contain entries from multiple
            # machines; the edit modal JS filters by `machine_name` per
            # dropdown.
            sm_transitions_by_content = {}
            # Per-(content, machine_name) transitions for templates that
            # want machine-scoped lookup. Backward-compatibility flat
            # `_sm_transitions` (a union over all machines on all contents)
            # is preserved for templates that key directly on (from, to).
            sm_transitions_by_machine = {}
            for sm_content, sm_list in ctx.sm_lookup.items():
                sm_transitions_by_content[sm_content] = []
                for sm in sm_list:
                    trans = sm.get("transitions", {})
                    machine = sm.get("machine_name", "")
                    all_transitions.update(trans)
                    sm_transitions_by_machine[(sm_content, machine)] = trans
                    sm_transitions_by_content[sm_content].extend([
                        {"from": f, "to": t, "scope": s,
                         "machine_name": machine}
                        for (f, t), s in trans.items()
                    ])

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

            # Structural is_anonymous flag derived from the typed
            # Principal — templates should use this rather than string-
            # comparing current_role, which is fragile across casing
            # (v0.9 canonicalized the role name to "Anonymous" but
            # historical templates compared to "anonymous"). Falls back
            # to a case-insensitive role-name check if Principal isn't
            # in the user dict (defensive — every code path through
            # identity.py now puts it there).
            principal = user.get("Principal")
            is_anonymous = (
                principal.is_anonymous if principal is not None
                else str(user.get("role", "")).lower() == "anonymous"
            )
            template_ctx = {
                "page_title": _pg["name"],
                "current_role": user["role"],
                "current_user_name": user["profile"]["DisplayName"],
                "is_anonymous": is_anonymous,
                "user_profile_json": json.dumps(user["profile"]),
                "roles": list(ctx.roles.keys()),
                "q": q,
                "termin_compute_js": compute_js,
                "_sm_transitions": all_transitions,
                "_sm_transitions_by_content": sm_transitions_by_content,
                "_sm_transitions_by_machine": sm_transitions_by_machine,
                "user_scopes": set(user["scopes"]),
                "termin_eval": _termin_eval,
                "flash_msg": flash_msg,
                "flash_style": flash_style,
                "flash_level": flash_level,
                "flash_dismiss": int(flash_dismiss) if flash_dismiss else None,
            }

            # Load data sources via the storage contract. v0.9 Phase 2:
            # page rendering reads through ctx.storage.query — same path
            # the auto-CRUD list route uses. limit=1000 matches the
            # legacy "return all" behavior; large content sets should
            # paginate via the auto-CRUD endpoint.
            user_scopes = set(user.get("scopes", []))
            for src in _reqs["sources"]:
                page = await ctx.storage.query(
                    src, None, QueryOptions(limit=1000),
                )
                records = [dict(r) for r in page.records]
                schema = ctx.content_lookup.get(src, {})
                template_ctx["items"] = redact_records(records, schema, user_scopes)

            # Form reference lists — same path.
            for ref in _reqs["ref_lists"]:
                page = await ctx.storage.query(
                    ref, None, QueryOptions(limit=1000),
                )
                template_ctx[f"{ref}_list"] = [dict(r) for r in page.records]

            content_html = page_templates[_sl].render(**template_ctx)
            return base_template.render(content=content_html, **template_ctx)
        finally:
            await db.close()


def _register_form_post(app, ctx, page, slug, reqs):
    ft = reqs["form_target"]
    # v0.9: sm_info is a list of state-machine dicts (one per SM on this
    # content). Empty list = no state machines on this content.
    sm_info = ctx.sm_lookup.get(ft, [])
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
        record = None
        schema = ctx.content_lookup.get(_ft, {})

        # v0.9 Phase 2: unique-field check uses ctx.storage.query with
        # an Eq predicate — replaces the legacy find_by_field helper.
        if not edit_id and _uf:
            for uf in _uf:
                val = data.get(uf, "")
                if val:
                    page_result = await ctx.storage.query(
                        _ft, Eq(field=uf, value=val), QueryOptions(limit=1),
                    )
                    if page_result.records:
                        raise HTTPException(
                            status_code=409,
                            detail=f"A record with {uf} '{val}' already exists")

        if edit_id:
            try:
                updated = await ctx.storage.update(_ft, edit_id, data)
            except Exception as e:
                from .errors import TerminError
                if ctx.terminator:
                    ctx.terminator.route(TerminError(
                        source=_ft, kind="validation", message=str(e)))
                raise
            if updated is not None:
                if ctx.event_bus:
                    await ctx.event_bus.publish({
                        "type": f"{_ft}_updated",
                        "channel_id": f"content.{_ft}.updated",
                        "content_name": _ft,
                        "data": dict(updated),
                    })
        else:
            user = ctx.get_current_user(request)
            evaluate_field_defaults(data, schema, ctx.expr_eval, user)

            # v0.9 multi-SM: state-machine column initial values are
            # the route's responsibility (provider stays SM-agnostic).
            # `create_as` overrides the initial of the first SM on
            # this content; remaining machines get their declared
            # initial state.
            for sm in (_sm or []):
                col = sm.get("machine_name", "")
                if col and not data.get(col):
                    data[col] = sm.get("initial", "")
            if _ca and _sm:
                data[_sm[0]["machine_name"]] = _ca

            try:
                record = await ctx.storage.create(_ft, data)
            except Exception as e:
                from .errors import TerminError
                if ctx.terminator:
                    ctx.terminator.route(TerminError(
                        source=_ft, kind="validation", message=str(e)))
                raise
            record = dict(record)
            if ctx.event_bus:
                await ctx.event_bus.publish({
                    "type": f"{_ft}_created",
                    "channel_id": f"content.{_ft}.created",
                    "content_name": _ft,
                    "data": record,
                })
            db = await get_db(ctx.db_path)
            try:
                await ctx.run_event_handlers(db, _ft, "created", record)
            finally:
                await db.close()

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
