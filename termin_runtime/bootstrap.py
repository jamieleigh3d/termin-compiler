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

from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .confidentiality import redact_records
from .providers import QueryOptions


# ── Page-IR resolution ──

def _resolve_page_for(ir: dict, path: str, user: dict) -> Optional[dict]:
    """Resolve a URL path to a single PageEntry IR for the
    requesting user.

    Path matching is by slug (PageEntry.slug). When multiple pages
    share a slug (role-scoped variants — e.g., one page for `alice`
    and another with the same slug for `Anonymous`), the
    user's-role variant wins. If no page matches the user's role
    for that slug, returns None.
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
    # Prefer exact role match (case-insensitive). If none, fall
    # back to the first variant — defensive for legacy IRs that
    # don't role-scope pages.
    for p in matches:
        page_role = str(p.get("role", ""))
        if page_role.lower() == user_role_lc:
            return p
    # No role-matching variant — the user does not have access to
    # this slug. None signals 404 / forbidden at the endpoint
    # layer (the runtime's CRUD routes apply the same gate).
    return None


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
