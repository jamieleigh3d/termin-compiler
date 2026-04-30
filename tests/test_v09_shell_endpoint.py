# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5b.4 B' plumbing: HTML shell mode.

The shell-HTML builder produces the minimal HTML response that
bootstraps a B'-mode page: a `<div id="termin-root">` container,
a `<script>` tag with the embedded bootstrap JSON, and `<script>`
tags referencing termin.js plus each provider bundle. A
GET /_termin/shell?path=<path> endpoint serves this for dev /
provider-validation purposes; flipping the production page routes
to use it is the next slice's job, and lands once the new
Spectrum bundle is ready to render.

Two layers tested here: pure shell-HTML building (no FastAPI,
no ctx — just the function) and the HTTP endpoint round-trip.
"""

from __future__ import annotations

import html as _html_module
import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from termin_server.bootstrap import (
    build_shell_html,
    register_shell_endpoint,
)


# ── Pure builder ──

def test_shell_html_embeds_bootstrap_payload_as_json():
    payload = {
        "component_tree_ir": {"slug": "tickets", "children": []},
        "bound_data": {"tickets": []},
        "principal_context": {"id": "alice"},
        "subscriptions_to_open": ["content.tickets"],
    }
    html = build_shell_html(payload, bundle_urls=[], page_title="Tickets")

    # The bootstrap data is in a <script> tag, embedded as JSON
    # so the browser's JSON.parse handles it. Look for the
    # __termin_bootstrap assignment.
    assert "__termin_bootstrap" in html
    assert "<script" in html
    # Pull out the JSON-shaped chunk and round-trip it.
    m = re.search(
        r"window\.__termin_bootstrap\s*=\s*(\{.*?\});",
        html, re.DOTALL,
    )
    assert m, "shell HTML must embed the bootstrap JSON"
    parsed = json.loads(m.group(1))
    assert parsed["component_tree_ir"]["slug"] == "tickets"
    assert parsed["principal_context"]["id"] == "alice"


def test_shell_html_includes_termin_js_script_tag():
    payload = {"component_tree_ir": {}, "bound_data": {},
               "principal_context": {}, "subscriptions_to_open": []}
    html = build_shell_html(payload, bundle_urls=[])
    assert "termin.js" in html or "termin/runtime" in html


def test_shell_html_includes_each_provider_bundle_url():
    payload = {"component_tree_ir": {}, "bound_data": {},
               "principal_context": {}, "subscriptions_to_open": []}
    html = build_shell_html(
        payload,
        bundle_urls=[
            "https://cdn.example.com/spectrum.js",
            "/runtime/providers/govuk/bundle.js",
        ],
    )
    assert "https://cdn.example.com/spectrum.js" in html
    assert "/runtime/providers/govuk/bundle.js" in html


def test_shell_html_dedupes_repeated_bundle_urls():
    """Regression: collect_csr_bundles returns one entry per
    (contract, provider) — a single-bundle provider serving ten
    contracts shows up ten times. The shell template must dedupe by
    URL before injecting <script> tags; otherwise the bundle executes
    multiple times in the browser, registering renderers redundantly
    and noisy in DevTools.
    """
    payload = {"component_tree_ir": {}, "bound_data": {},
               "principal_context": {}, "subscriptions_to_open": []}
    spectrum_url = "/_termin/providers/spectrum/bundle.js"
    html = build_shell_html(
        payload,
        bundle_urls=[spectrum_url] * 10,  # ten contracts, same URL
    )
    # Exactly one <script> tag for that URL — not ten.
    assert html.count(f'src="{spectrum_url}"') == 1


def test_shell_html_dedupes_but_preserves_distinct_urls():
    """Dedup must preserve distinct URLs — a deploy with two providers
    (e.g. spectrum + govuk) needs both bundles loaded."""
    payload = {"component_tree_ir": {}, "bound_data": {},
               "principal_context": {}, "subscriptions_to_open": []}
    a = "/_termin/providers/spectrum/bundle.js"
    b = "/_termin/providers/govuk/bundle.js"
    html = build_shell_html(payload, bundle_urls=[a, a, b, b, a])
    assert html.count(f'src="{a}"') == 1
    assert html.count(f'src="{b}"') == 1


def test_shell_html_includes_termin_root_div():
    payload = {"component_tree_ir": {}, "bound_data": {},
               "principal_context": {}, "subscriptions_to_open": []}
    html = build_shell_html(payload, bundle_urls=[])
    assert 'id="termin-root"' in html


def test_shell_html_escapes_unsafe_payload_content():
    """The bootstrap JSON could contain arbitrary user data
    (record values, principal display names) — the embedding
    must escape `</script>` to prevent script-tag breakout.
    Standard XSS hygiene for inline JSON."""
    payload = {
        "component_tree_ir": {},
        "bound_data": {"tickets": [{"title": "</script><script>alert(1)//"}]},
        "principal_context": {},
        "subscriptions_to_open": [],
    }
    html = build_shell_html(payload, bundle_urls=[])
    # The literal `</script>` substring should not appear inside
    # the embedded JSON (it would break out of the surrounding
    # script tag). Common defensive pattern is to encode it as
    # `<\/script>` or use Unicode escapes.
    # First find the script-block boundaries:
    bootstrap_match = re.search(
        r"window\.__termin_bootstrap\s*=\s*(\{.*?\});",
        html, re.DOTALL,
    )
    assert bootstrap_match
    embedded_json = bootstrap_match.group(1)
    # The closing-script-tag sequence must not appear unescaped
    # inside the JSON literal in the rendered HTML.
    assert "</script>" not in embedded_json


def test_shell_html_sets_page_title_when_provided():
    payload = {"component_tree_ir": {}, "bound_data": {},
               "principal_context": {}, "subscriptions_to_open": []}
    html = build_shell_html(payload, bundle_urls=[], page_title="My App")
    assert "<title>My App</title>" in html or "<title>" in html and "My App" in html


def test_shell_html_default_title_when_none_provided():
    payload = {"component_tree_ir": {}, "bound_data": {},
               "principal_context": {}, "subscriptions_to_open": []}
    html = build_shell_html(payload, bundle_urls=[])
    # Default falls back to a generic Termin title; the exact
    # string is provider-irrelevant but the tag must exist.
    assert "<title>" in html


# ── Endpoint integration ──

def _make_user(role: str = "alice", scopes=("x.read",)):
    return {
        "role": role,
        "scopes": list(scopes),
        "User": {"Authenticated": True, "Name": role.title()},
        "the_user": {
            "id": role,
            "display_name": role.title(),
            "is_anonymous": False,
            "is_system": False,
            "scopes": list(scopes),
            "preferences": {},
        },
        "profile": {"DisplayName": role.title()},
    }


class _StoragePage:
    def __init__(self, records):
        self.records = records
        self.next_cursor = None
        self.estimated_total = None


class StubStorage:
    async def query(self, content_name, predicate, options):
        return _StoragePage([])


class StubCtx:
    def __init__(self, pages, name="Termin Test"):
        self.ir = {"pages": pages, "contents": [], "name": name}
        self.content_lookup = {}
        self.storage = StubStorage()
        self.presentation_providers = []
        self.deploy_config = {}

    def get_current_user(self, request):
        return _make_user()


@pytest.fixture
def app_with_shell_endpoint():
    page = {
        "name": "Tickets", "slug": "tickets", "role": "alice",
        "children": [],
    }
    ctx = StubCtx(pages=[page], name="Test App")
    app = FastAPI()
    register_shell_endpoint(app, ctx)
    return app


def test_shell_endpoint_returns_html(app_with_shell_endpoint):
    with TestClient(app_with_shell_endpoint) as client:
        resp = client.get("/_termin/shell", params={"path": "/tickets"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "__termin_bootstrap" in resp.text


def test_shell_endpoint_404_for_unknown_path(app_with_shell_endpoint):
    with TestClient(app_with_shell_endpoint) as client:
        resp = client.get("/_termin/shell", params={"path": "/no-such-page"})
        assert resp.status_code == 404


def test_shell_endpoint_422_when_path_missing(app_with_shell_endpoint):
    with TestClient(app_with_shell_endpoint) as client:
        resp = client.get("/_termin/shell")
        assert resp.status_code == 422
