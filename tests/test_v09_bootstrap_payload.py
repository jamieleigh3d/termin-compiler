# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5b.4 B' plumbing: bootstrap payload builder.

Per the Spectrum-provider design Q2 decision (B' = server-authoritative
+ JS-as-renderer), the runtime ships a bootstrap JSON payload instead
of (or alongside) SSR-composited HTML. The payload shape:

  {
    "component_tree_ir": <PageEntry IR>,
    "bound_data": <records keyed by content source>,
    "principal_context": <id, scopes, preferences>,
    "subscriptions_to_open": [<channel_id>, ...]
  }

This payload is returned at first page load (embedded in the HTML
shell) and on SPA navigation (via GET /_termin/page-data?path=<path>).

This test module covers the pure builder. The endpoint integration
test lives in test_v09_page_data_endpoint.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Fixtures ──

def _make_user(user_id: str = "alice", scopes=("x.read",), anonymous: bool = False):
    return {
        "role": user_id,
        "scopes": list(scopes),
        "User": {"Authenticated": not anonymous, "Name": user_id.title()},
        "the_user": {
            "id": user_id,
            "display_name": user_id.title(),
            "is_anonymous": anonymous,
            "is_system": False,
            "scopes": list(scopes),
            "preferences": {"theme": "auto"},
        },
        "profile": {"DisplayName": user_id.title()},
    }


def _make_ctx(*, pages=None, contents=None, sources_data=None):
    """Stand up a stub RuntimeContext with just enough surface for
    the bootstrap builder. Real-runtime tests live elsewhere."""
    ctx = MagicMock()
    ctx.ir = {
        "pages": pages or [],
        "contents": contents or [],
    }
    ctx.content_lookup = {c["name"]["snake"]: c for c in (contents or [])}

    class _StoragePage:
        def __init__(self, records):
            self.records = records
            self.next_cursor = None
            self.estimated_total = None

    storage = MagicMock()
    sources = sources_data or {}

    async def _query(content_name, predicate, options):
        return _StoragePage(sources.get(content_name, []))

    storage.query = _query
    ctx.storage = storage
    return ctx


# ── Builder: basic shapes ──

@pytest.mark.asyncio
async def test_payload_includes_component_tree_for_matched_page():
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {
        "name": "Tickets",
        "slug": "tickets",
        "role": "alice",
        "children": [
            {"type": "data_table", "props": {"source": "tickets"}, "children": []}
        ],
    }
    ctx = _make_ctx(pages=[page], sources_data={"tickets": []})
    payload = await build_bootstrap_payload(ctx, "/tickets", _make_user("alice"))

    assert payload["component_tree_ir"]["slug"] == "tickets"
    assert payload["component_tree_ir"]["children"][0]["type"] == "data_table"


@pytest.mark.asyncio
async def test_payload_includes_principal_context():
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {"name": "X", "slug": "x", "role": "alice", "children": []}
    ctx = _make_ctx(pages=[page])
    user = _make_user("alice", scopes=("x.read", "x.write"))
    payload = await build_bootstrap_payload(ctx, "/x", user)

    pc = payload["principal_context"]
    assert pc["id"] == "alice"
    assert pc["display_name"] == "Alice"
    assert pc["is_anonymous"] is False
    assert set(pc["scopes"]) == {"x.read", "x.write"}
    assert pc["preferences"]["theme"] == "auto"


@pytest.mark.asyncio
async def test_payload_loads_data_for_each_data_source():
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {
        "name": "Mixed", "slug": "mixed", "role": "alice",
        "children": [
            {"type": "data_table", "props": {"source": "tickets"}, "children": []},
            {"type": "chat", "props": {"source": "messages"}, "children": []},
        ],
    }
    ctx = _make_ctx(
        pages=[page],
        sources_data={
            "tickets": [{"id": 1, "title": "Bug"}],
            "messages": [{"id": 1, "body": "Hi"}],
        },
    )
    payload = await build_bootstrap_payload(ctx, "/mixed", _make_user("alice"))

    assert payload["bound_data"]["tickets"] == [{"id": 1, "title": "Bug"}]
    assert payload["bound_data"]["messages"] == [{"id": 1, "body": "Hi"}]


@pytest.mark.asyncio
async def test_payload_subscriptions_for_each_data_source():
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {
        "name": "X", "slug": "x", "role": "alice",
        "children": [
            {"type": "data_table", "props": {"source": "tickets"}, "children": []},
        ],
    }
    ctx = _make_ctx(pages=[page], sources_data={"tickets": []})
    payload = await build_bootstrap_payload(ctx, "/x", _make_user("alice"))

    # Subscriptions are per-data-source, prefix-style — clients can
    # subscribe to "content.tickets" to catch every CRUD event for
    # that content type.
    assert "content.tickets" in payload["subscriptions_to_open"]


# ── Path resolution ──

@pytest.mark.asyncio
async def test_path_with_leading_slash_resolves():
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {"name": "X", "slug": "x", "role": "alice", "children": []}
    ctx = _make_ctx(pages=[page])
    payload = await build_bootstrap_payload(ctx, "/x", _make_user("alice"))
    assert payload["component_tree_ir"]["slug"] == "x"


@pytest.mark.asyncio
async def test_path_without_leading_slash_resolves():
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {"name": "X", "slug": "x", "role": "alice", "children": []}
    ctx = _make_ctx(pages=[page])
    payload = await build_bootstrap_payload(ctx, "x", _make_user("alice"))
    assert payload["component_tree_ir"]["slug"] == "x"


@pytest.mark.asyncio
async def test_unknown_path_returns_none():
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {"name": "X", "slug": "x", "role": "alice", "children": []}
    ctx = _make_ctx(pages=[page])
    payload = await build_bootstrap_payload(ctx, "/nonexistent", _make_user("alice"))
    assert payload is None


# ── Role-scoped page resolution ──

@pytest.mark.asyncio
async def test_role_scoped_page_picks_user_role_variant():
    """Two pages sharing a slug differentiated by role — the
    builder picks the one matching the user's role."""
    from termin_runtime.bootstrap import build_bootstrap_payload

    pages = [
        {"name": "Home", "slug": "home", "role": "alice",
         "children": [{"type": "text", "props": {"value": "alice-home"}, "children": []}]},
        {"name": "Home", "slug": "home", "role": "Anonymous",
         "children": [{"type": "text", "props": {"value": "anon-home"}, "children": []}]},
    ]
    ctx = _make_ctx(pages=pages)

    payload_alice = await build_bootstrap_payload(ctx, "/home", _make_user("alice"))
    assert payload_alice["component_tree_ir"]["children"][0]["props"]["value"] == "alice-home"

    payload_anon = await build_bootstrap_payload(
        ctx, "/home",
        _make_user("anonymous", anonymous=True, scopes=()),
    )
    # The Anonymous-role page should match
    assert payload_anon["component_tree_ir"]["children"][0]["props"]["value"] == "anon-home"


@pytest.mark.asyncio
async def test_single_variant_slug_returns_page_regardless_of_role():
    """Slug exists with exactly one page variant — return it
    regardless of role. Auth is enforced downstream by CRUD scope
    checks and confidentiality redaction; the page UI itself is
    never the security boundary. Matches SSR pipeline behavior:
    the page renders for any user, the data layer filters.

    Replaces the prior `test_role_unmatched_page_returns_none`,
    which asserted the over-strict behavior the page-route cut-over
    revealed as a UX regression on 2026-04-29 (Chrome with a stale
    `termin_role=Anonymous` cookie 404'd on `/inventory_dashboard`
    that the SSR pipeline would have rendered).
    """
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {"name": "Admin", "slug": "admin", "role": "admin", "children": []}
    ctx = _make_ctx(pages=[page])
    payload = await build_bootstrap_payload(ctx, "/admin", _make_user("alice"))
    assert payload is not None
    assert payload["component_tree_ir"]["slug"] == "admin"


@pytest.mark.asyncio
async def test_multi_variant_role_unmatched_falls_back_to_first():
    """Multiple variants for a slug, none matching the user's role —
    fall back to the first variant rather than 404. The user still
    sees the page; the data layer filters.

    A multi-variant slug exists for genuine role-conditional rendering
    (e.g., admin sees a different layout than viewer for the same
    URL). When the user's role doesn't match any variant, returning
    SOMETHING beats returning 404 — same SSR-equivalent permissive
    stance as single-variant.
    """
    from termin_runtime.bootstrap import build_bootstrap_payload

    pages = [
        {"name": "Home", "slug": "home", "role": "alice",
         "children": [{"type": "text", "props": {"value": "alice-home"}, "children": []}]},
        {"name": "Home", "slug": "home", "role": "bob",
         "children": [{"type": "text", "props": {"value": "bob-home"}, "children": []}]},
    ]
    ctx = _make_ctx(pages=pages)
    payload = await build_bootstrap_payload(ctx, "/home", _make_user("carol"))
    assert payload is not None
    # Falls back to the first variant (alice's). UI renders; data
    # layer enforces — carol sees the structure but no data they
    # don't have scope for.
    assert payload["component_tree_ir"]["children"][0]["props"]["value"] == "alice-home"


# ── Anonymous principal ──

@pytest.mark.asyncio
async def test_anonymous_user_builds_principal_context_correctly():
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {"name": "Public", "slug": "public", "role": "Anonymous", "children": []}
    ctx = _make_ctx(pages=[page])
    user = _make_user("anonymous", anonymous=True, scopes=("public.view",))
    payload = await build_bootstrap_payload(ctx, "/public", user)

    pc = payload["principal_context"]
    assert pc["is_anonymous"] is True
    assert pc["scopes"] == ["public.view"]


# ── Form reference lists ──

@pytest.mark.asyncio
async def test_form_reference_content_loaded_into_bound_data():
    """A form's field_input with reference_content (e.g., the
    target content has a foreign-key-shaped reference) should pull
    the referenced content's records into bound_data so the form
    UI has the dropdown options."""
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {
        "name": "Create Ticket", "slug": "create-ticket", "role": "alice",
        "children": [
            {
                "type": "form",
                "props": {"target": "tickets"},
                "children": [
                    {"type": "field_input",
                     "props": {"reference_content": "users", "field": "assignee"},
                     "children": []},
                ],
            },
        ],
    }
    ctx = _make_ctx(
        pages=[page],
        sources_data={
            "users": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
        },
    )
    payload = await build_bootstrap_payload(ctx, "/create-ticket", _make_user("alice"))

    assert "users" in payload["bound_data"]
    assert len(payload["bound_data"]["users"]) == 2


# ── v0.9 Phase 5b.4 0.1: app_chrome (page-chrome metadata) ──

@pytest.mark.asyncio
async def test_payload_includes_app_chrome_metadata():
    """The bootstrap payload must carry an `app_chrome` block so CSR
    providers can render the page header (app name + nav + role
    switcher + username entry) without re-fetching runtime state."""
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {"name": "Home", "slug": "home", "role": "alice", "children": []}
    ctx = _make_ctx(pages=[page])
    ctx.ir["name"] = "Demo App"
    ctx.ir["nav_items"] = [
        {"label": "Home", "page_slug": "home", "visible_to": ["all"]},
        {"label": "Admin", "page_slug": "admin", "visible_to": ["alice"]},
        {"label": "Other", "page_slug": "other", "visible_to": ["bob"]},
    ]
    ctx.roles = {"alice": ["x.read"], "bob": ["x.read"], "Anonymous": []}

    user = _make_user("alice")
    payload = await build_bootstrap_payload(ctx, "/home", user)

    chrome = payload["app_chrome"]
    assert chrome["app_name"] == "Demo App"
    assert chrome["current_role"] == "alice"
    assert chrome["current_user_name"] == "Alice"
    assert chrome["is_anonymous"] is False
    assert chrome["available_roles"] == ["alice", "bob", "Anonymous"]
    # alice sees Home (visible_to=all) + Admin (alice in visible_to);
    # not Other (only bob).
    nav_labels = [n["label"] for n in chrome["nav_items"]]
    assert nav_labels == ["Home", "Admin"]


@pytest.mark.asyncio
async def test_app_chrome_substring_matches_role_for_nav_visibility():
    """Per the Tailwind SSR template's `"<token>" in current_role`
    rule: a nav item visible_to a short token like "clerk" should
    match a full role name like "warehouse clerk". Without this,
    apps that declare nav `visible to clerk` lose those items when
    Spectrum renders."""
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {"name": "Dash", "slug": "dash", "role": "warehouse clerk",
            "children": []}
    ctx = _make_ctx(pages=[page])
    ctx.ir["nav_items"] = [
        {"label": "Receive", "page_slug": "receive",
         "visible_to": ["clerk", "manager"]},
        {"label": "Hidden", "page_slug": "hidden",
         "visible_to": ["executive"]},
    ]
    ctx.roles = {"warehouse clerk": [], "warehouse manager": [],
                 "executive": [], "Anonymous": []}

    user = _make_user("warehouse clerk")
    payload = await build_bootstrap_payload(ctx, "/dash", user)
    nav_labels = [n["label"] for n in payload["app_chrome"]["nav_items"]]
    assert nav_labels == ["Receive"]


@pytest.mark.asyncio
async def test_app_chrome_anonymous_user_hides_username():
    """When the principal is anonymous, the username entry is
    redundant (the user has no display name to maintain). The chrome
    block carries `is_anonymous=True` and `current_user_name=""` so
    the UI can hide that field per the SSR template's behavior."""
    from termin_runtime.bootstrap import build_bootstrap_payload

    page = {"name": "Public", "slug": "public", "role": "Anonymous",
            "children": []}
    ctx = _make_ctx(pages=[page])
    ctx.ir["nav_items"] = []
    ctx.roles = {"Anonymous": []}

    user = _make_user("anonymous", anonymous=True)
    payload = await build_bootstrap_payload(ctx, "/public", user)
    assert payload["app_chrome"]["is_anonymous"] is True
    assert payload["app_chrome"]["current_user_name"] == ""
