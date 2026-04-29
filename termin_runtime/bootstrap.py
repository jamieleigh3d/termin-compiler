# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 5b.4 B' plumbing: bootstrap-payload builder.

Per the Spectrum-provider design Q2 decision (B' = server-authoritative
+ JS-as-renderer / LiveView-shaped), the runtime ships a JSON
bootstrap payload that the client-side provider bundle uses to render
the page. The payload is delivered two ways:

  1. Embedded in the initial HTML response (first page load), inside
     a `<script>window.__termin_bootstrap = {...}</script>` data
     island. (Future commit — HTML shell mode.)
  2. Returned from `GET /_termin/page-data?path=<path>` for SPA
     navigation. (Endpoint registered in
     `register_page_data_endpoint`.)

Either way the shape is identical:

    {
      "component_tree_ir": <PageEntry IR>,
      "bound_data": <records keyed by content source>,
      "principal_context": <id, scopes, preferences, ...>,
      "subscriptions_to_open": [<channel_id>, ...]
    }

Trust-plane invariants per BRD #2 + BRD #3:

  - Confidentiality redaction (rows + fields) applies *before*
    payload assembly. The runtime is authoritative; the client
    sees only what the principal is allowed to see.
  - Ownership cascade (Phase 6a.6) — same. The list_records-shaped
    queries already go through the storage layer, which applies
    row filtering for owned content.
  - Subscription channel IDs in `subscriptions_to_open` are the
    coarse `content.<X>` prefix; the WebSocket fan-out path
    (`broadcast_to_subscribers`) re-applies the ownership cascade
    per-event.
  - Role-scoped page resolution: when the same slug has multiple
    pages keyed by role, we pick the page matching the user's
    role. No matching page → returns None → endpoint 404s.
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .confidentiality import redact_records
from .presentation_bundles import collect_csr_bundles
from .providers import QueryOptions


# ── Page-IR resolution ──

def _resolve_page_for(ir: dict, path: str, user: dict) -> Optional[dict]:
    """Resolve a URL path to a single PageEntry IR for the
    requesting user.

    Path matching is by slug (PageEntry.slug). Two cases:

      * **Single variant for a slug.** Return it regardless of the
        page's `role` metadata — the page UI renders for any user,
        and auth is enforced by the data layer (CRUD scope checks,
        confidentiality redaction, ownership cascade). This matches
        how the SSR pipeline already behaves: the page route serves
        the rendered template to any user, and the data table is
        empty when the user lacks the read scope. Anonymous users
        landing on a role-restricted page see the page chrome and
        an empty surface — they switch roles via the role-switcher.

      * **Multiple variants of the same slug** (role-scoped variants —
        e.g., a page for `Anonymous` AND a different page for `alice`
        at the same slug). Pick the variant matching the user's role.
        If no variant matches, fall back to the first one (so the
        user at least sees something — same SSR-equivalent behavior).
        A bug from 2026-04-29 had this branch return None; it caused
        the page-route cut-over to 404 for any user whose role didn't
        explicitly match the page's role metadata.

    Returns None only when no page exists for this slug at all.
    """
    slug = path.lstrip("/").split("?", 1)[0].split("/", 1)[0]
    user_role = str(user.get("role", "")) if isinstance(user, dict) else ""
    user_role_lc = user_role.lower()

    matches = [
        p for p in ir.get("pages", [])
        if p.get("slug") == slug
    ]
    if not matches:
        return None

    # Single-variant fast path — auth is enforced downstream, not by
    # which page we resolve. Match SSR behavior.
    if len(matches) == 1:
        return matches[0]

    # Multi-variant — disambiguate by role. Prefer exact role match
    # (case-insensitive); if none, fall back to the first variant
    # rather than 404. The user still sees the page; the data layer
    # filters access.
    for p in matches:
        page_role = str(p.get("role", ""))
        if page_role.lower() == user_role_lc:
            return p
    return matches[0]


# ── Data-source extraction ──

_DATA_SOURCE_TYPES = ("data_table", "chat", "aggregation", "stat_breakdown")


def _walk_for_sources_and_refs(node: dict, sources: set, ref_lists: set) -> None:
    """Walk a component-tree IR fragment, collecting data sources
    and form reference-lists. Mirrors `pages.extract_page_reqs`
    but produces just the storage-side requirements (the
    payload's `bound_data` keys) — no form_target, create_as,
    after_save, etc., which are runtime-side rather than
    payload-side concerns."""
    if not isinstance(node, dict):
        return
    t = node.get("type", "")
    p = node.get("props", {}) or {}

    if t in _DATA_SOURCE_TYPES:
        src = p.get("source")
        if src:
            sources.add(src)
    elif t == "field_input":
        ref = p.get("reference_content")
        if ref:
            ref_lists.add(ref)

    for child in node.get("children", []) or []:
        _walk_for_sources_and_refs(child, sources, ref_lists)


# ── Principal-context projection ──

def _principal_context_for(user: dict) -> dict:
    """Project the runtime user dict to the wire-shaped principal
    context delivered to the provider. Mirrors `the_user` from
    identity.py, which is the BRD #3 §4.2 Principal projection.

    Defensive: a user dict missing `the_user` (legacy callers,
    tests) gets a synthesized fallback so the payload shape
    stays stable."""
    the_user = user.get("the_user") if isinstance(user, dict) else None
    if isinstance(the_user, dict):
        return {
            "id": the_user.get("id", ""),
            "display_name": the_user.get("display_name", ""),
            "is_anonymous": bool(the_user.get("is_anonymous", False)),
            "is_system": bool(the_user.get("is_system", False)),
            "scopes": list(the_user.get("scopes", []) or []),
            "preferences": dict(the_user.get("preferences", {}) or {}),
        }
    return {
        "id": "",
        "display_name": str(user.get("role", "")) if isinstance(user, dict) else "",
        "is_anonymous": True,
        "is_system": False,
        "scopes": list(user.get("scopes", []) if isinstance(user, dict) else ()),
        "preferences": {},
    }


# ── Builder ──

async def build_bootstrap_payload(
    ctx, path: str, user: dict,
) -> Optional[dict]:
    """Build the B'-mode bootstrap payload for `path` as seen by
    `user`. Returns None when no page matches the path + role.

    Args:
        ctx: RuntimeContext (or stub with `ir`, `storage`,
            `content_lookup` attributes).
        path: URL path. Leading slash optional. Query string is
            stripped.
        user: identity-built user dict (see `_build_user_dict` in
            identity.py).

    Returns: payload dict shaped per Spectrum-provider design Q2,
    or None for unresolvable paths.
    """
    page = _resolve_page_for(ctx.ir, path, user)
    if page is None:
        return None

    sources: set = set()
    ref_lists: set = set()
    for child in page.get("children", []) or []:
        _walk_for_sources_and_refs(child, sources, ref_lists)

    user_scopes = set(user.get("scopes", []) if isinstance(user, dict) else ())
    bound_data: dict[str, list[dict]] = {}

    # Data sources (data_table / chat / aggregation / stat_breakdown)
    # and form reference lists both flow through the same storage
    # query path; the difference is the field redaction policy
    # downstream in the provider, not at the runtime fetch.
    for src in sources | ref_lists:
        page_result = await ctx.storage.query(src, None, QueryOptions(limit=1000))
        records = [dict(r) for r in (page_result.records or [])]
        schema = (
            ctx.content_lookup.get(src, {})
            if hasattr(ctx, "content_lookup") else {}
        )
        bound_data[src] = redact_records(records, schema, user_scopes)

    subscriptions = sorted(f"content.{src}" for src in sources)

    return {
        "component_tree_ir": page,
        "bound_data": bound_data,
        "principal_context": _principal_context_for(user),
        "subscriptions_to_open": subscriptions,
    }


# ── HTTP endpoint ──
#
# `Termin.action(payload)` is **client-side dispatch** — the JS
# helper translates each action `kind` into the appropriate
# existing REST endpoint (CRUD on /api/v1/<content>, transitions
# on /_transition/..., compute on /api/v1/compute/...). No new
# server endpoint exists for actions: BRD #2 §11 already
# standardizes the REST surface every conforming runtime
# implements, and a server-side facade would be duplicate
# plumbing. See docs/spectrum-provider-design.md §"Q-extra
# (action API surface)" for the rationale.


def register_page_data_endpoint(app, ctx) -> None:
    """Register `GET /_termin/page-data?path=<path>` on `app`.

    Returns the bootstrap JSON for the given path scoped to the
    requesting principal. SPA-style navigation in B' mode flows
    through this endpoint — the client calls it to fetch the
    next page's payload, then swaps its rendered tree.

    Auth: identical to a regular page request — the existing
    cookie-based identity resolution applies. A path the user
    can't access (no role-matching page) returns 404.
    """

    @app.get("/_termin/page-data")
    async def page_data(request: Request, path: str = ""):
        if not path:
            raise HTTPException(
                status_code=422,
                detail="Required query parameter `path` is missing",
            )
        user = ctx.get_current_user(request)
        payload = await build_bootstrap_payload(ctx, path, user)
        if payload is None:
            raise HTTPException(
                status_code=404,
                detail=f"No page resolves for path {path!r}",
            )
        return JSONResponse(content=payload)


# ── HTML shell mode ──

_SHELL_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{provider_styles}
</head>
<body>
<div id="termin-root"></div>
<script>window.__termin_bootstrap = {bootstrap_json};</script>
<script src="/runtime/termin.js" defer></script>
{provider_scripts}
</body>
</html>
"""
# v0.9 Phase 5b.4 B' loop: the shell DOES NOT load /runtime/termin.css.
# That stylesheet ships the SSR-Tailwind pipeline's typography
# (h1/h2/etc. with `color: var(--t-text)`, etc.) which leaks into
# the bundle's Spectrum-rendered tree and fights for the same
# elements. The shell stays minimal — termin.js for the WebSocket
# multiplexer + Termin global, plus whatever provider stylesheets
# the bundle injects on its own. SSR pages still load
# /runtime/termin.css normally; this template is shell-only.


def _safe_inline_json(payload: dict) -> str:
    """Serialize `payload` for inlining inside a `<script>` tag.

    The JSON encoding escapes `</script>` to `<\\/script>` so a
    malicious record value containing `</script><script>...`
    can't break out of the surrounding script block. Forward
    slash escaping is JSON-legal and is the standard XSS-hardening
    pattern for inline-JSON-in-HTML.
    """
    raw = json.dumps(payload, ensure_ascii=True, default=str)
    return raw.replace("</", r"<\/")


def build_shell_html(
    payload: dict,
    bundle_urls: Iterable[str] = (),
    page_title: str = "",
    style_urls: Iterable[str] = (),
) -> str:
    """Build the B'-mode HTML shell.

    Per Spectrum-provider design Q2: at first page load the
    runtime emits a minimal HTML response containing
    `<div id="termin-root">`, an embedded
    `<script>window.__termin_bootstrap = {...}</script>` data
    island, and `<script>` tags for termin.js plus each provider
    bundle. The provider's bundle reads the bootstrap data and
    renders the page client-side from there.

    Args:
        payload: the bootstrap dict from `build_bootstrap_payload`.
        bundle_urls: iterable of provider-bundle script URLs to
            load (typically from `collect_csr_bundles`).
        page_title: <title> value. Defaults to "Termin App".
        style_urls: iterable of provider stylesheet URLs to load
            via `<link rel="stylesheet">`.

    Returns: the complete HTML document as a string.
    """
    title = page_title or "Termin App"
    bootstrap_json = _safe_inline_json(payload)
    # Dedupe by URL — the discovery list returns one entry per bound
    # contract, so a single-bundle provider serving ten contracts
    # appears ten times. The browser's script cache would handle the
    # redundancy but the duplicate <script> tags execute the bundle
    # multiple times, which is wasteful and confusing in DevTools.
    seen_urls: set[str] = set()
    deduped_urls: list[str] = []
    for url in bundle_urls:
        if url and url not in seen_urls:
            seen_urls.add(url)
            deduped_urls.append(url)
    provider_scripts = "\n".join(
        f'<script src="{url}" defer></script>'
        for url in deduped_urls
    )
    provider_styles = "\n".join(
        f'<link rel="stylesheet" href="{url}">'
        for url in style_urls if url
    )
    return _SHELL_TEMPLATE.format(
        title=title,
        bootstrap_json=bootstrap_json,
        provider_scripts=provider_scripts,
        provider_styles=provider_styles,
    )


async def render_shell_response(ctx, request, path: str) -> HTMLResponse:
    """Build the B'-mode HTML shell for `path` as an HTMLResponse.

    Shared between the explicit `/_termin/shell?path=...` endpoint and
    the per-slug page route's CSR-only branch. Returns an HTMLResponse
    on success; raises HTTPException(404) when no page resolves for
    the given path. The caller is responsible for any 422 / auth /
    method gating.

    Pure runtime composition: builds the bootstrap payload, collects
    bundle URLs (deduped by `build_shell_html`), reads the page title
    from the IR, and renders the shell template.
    """
    user = ctx.get_current_user(request)
    payload = await build_bootstrap_payload(ctx, path, user)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"No page resolves for path {path!r}",
        )

    bundles = collect_csr_bundles(
        bound_providers=getattr(ctx, "presentation_providers", []),
        deploy_config=getattr(ctx, "deploy_config", {}) or {},
    )
    bundle_urls = [b["url"] for b in bundles if b.get("url")]
    page_title = (
        payload.get("component_tree_ir", {}).get("name", "")
        or ctx.ir.get("name", "Termin App")
    )
    html = build_shell_html(payload, bundle_urls, page_title=page_title)
    return HTMLResponse(content=html)


def page_should_use_shell(ctx) -> bool:
    """True iff the bound provider for `presentation-base.page` is
    CSR-only.

    The rule: if the page-contract provider declares `csr` but not
    `ssr` in its render_modes, the per-slug page route serves the B'
    shell HTML instead of the SSR pipeline — the provider doesn't
    implement SSR, so legacy Tailwind rendering would just be a
    fallback the bundle then has to reconcile against.

    When no provider is bound (default), or when the bound provider
    supports both modes (Tailwind once it adds CSR), or when the
    bound provider is SSR-only (GOV.UK), this returns False and the
    SSR pipeline runs. Apps can still hit `/_termin/shell?path=...`
    explicitly even in those cases.

    The check is per-runtime, not per-page: today every `presentation-
    base.page` contract resolves through the same provider. When 5b.3
    full per-contract dispatch lands, this can extend to per-page-IR
    inspection. For v0.9 5b.4 the namespace-or-contract binding model
    keeps it simple.
    """
    for contract, _product, provider in getattr(ctx, "presentation_providers", []):
        if contract == "presentation-base.page":
            modes = getattr(provider, "render_modes", ())
            return "csr" in modes and "ssr" not in modes
    return False


def register_shell_endpoint(app, ctx) -> None:
    """Register `GET /_termin/shell?path=<path>` on `app`.

    Returns the B'-mode HTML shell for the given path. The per-slug
    page route ALSO serves this shell when the bound provider is
    CSR-only — so this endpoint is mostly useful for dev /
    provider-validation / explicit-shell-URL access. Both paths share
    `render_shell_response`.
    """

    @app.get("/_termin/shell")
    async def termin_shell(request: Request, path: str = ""):
        if not path:
            raise HTTPException(
                status_code=422,
                detail="Required query parameter `path` is missing",
            )
        return await render_shell_response(ctx, request, path)
