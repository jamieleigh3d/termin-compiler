# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5b.4 B' plumbing: GET /_termin/page-data
endpoint integration.

The endpoint returns the bootstrap JSON for SPA navigation in B'
mode (server-authoritative + JS-as-renderer per the Spectrum-
provider design Q2). The unit tests for the payload builder live
in test_v09_bootstrap_payload.py; this module exercises the HTTP
shape — auth resolution, missing-path 422, unknown-path 404,
JSON content-type, payload round-trip.
"""

from __future__ import annotations

import pytest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from termin_server.bootstrap import register_page_data_endpoint


# ── Fixture: app + ctx with one page ──

def _make_user(role: str = "alice", scopes=("x.read",), anonymous: bool = False):
    return {
        "role": role,
        "scopes": list(scopes),
        "User": {"Authenticated": not anonymous, "Name": role.title()},
        "the_user": {
            "id": role,
            "display_name": role.title(),
            "is_anonymous": anonymous,
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
    def __init__(self, sources_data):
        self._data = sources_data

    async def query(self, content_name, predicate, options):
        return _StoragePage(self._data.get(content_name, []))


class StubCtx:
    def __init__(self, pages, sources_data=None, user=None):
        self.ir = {"pages": pages, "contents": []}
        self.content_lookup = {}
        self.storage = StubStorage(sources_data or {})
        self._user = user or _make_user()

    def get_current_user(self, request):
        return self._user


@pytest.fixture
def app_with_one_page():
    page = {
        "name": "Tickets", "slug": "tickets", "role": "alice",
        "children": [
            {"type": "data_table", "props": {"source": "tickets"}, "children": []}
        ],
    }
    ctx = StubCtx(
        pages=[page],
        sources_data={"tickets": [{"id": 1, "title": "Bug"}]},
    )
    app = FastAPI()
    register_page_data_endpoint(app, ctx)
    return app


# ── Endpoint behavior ──

def test_endpoint_returns_payload_for_known_path(app_with_one_page):
    with TestClient(app_with_one_page) as client:
        resp = client.get("/_termin/page-data", params={"path": "/tickets"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["component_tree_ir"]["slug"] == "tickets"
        assert body["bound_data"]["tickets"] == [{"id": 1, "title": "Bug"}]
        assert body["principal_context"]["id"] == "alice"
        assert "content.tickets" in body["subscriptions_to_open"]


def test_endpoint_returns_404_for_unknown_path(app_with_one_page):
    with TestClient(app_with_one_page) as client:
        resp = client.get("/_termin/page-data", params={"path": "/no-such-page"})
        assert resp.status_code == 404


def test_endpoint_returns_422_when_path_missing(app_with_one_page):
    with TestClient(app_with_one_page) as client:
        resp = client.get("/_termin/page-data")
        assert resp.status_code == 422


def test_endpoint_accepts_path_without_leading_slash(app_with_one_page):
    with TestClient(app_with_one_page) as client:
        resp = client.get("/_termin/page-data", params={"path": "tickets"})
        assert resp.status_code == 200


def test_endpoint_returns_json_content_type(app_with_one_page):
    with TestClient(app_with_one_page) as client:
        resp = client.get("/_termin/page-data", params={"path": "/tickets"})
        assert resp.headers["content-type"].startswith("application/json")


def test_endpoint_single_variant_renders_for_any_role():
    """A path whose only registered page variant has a different role
    still renders — auth enforcement lives in the data layer (CRUD
    scope checks, confidentiality redaction), not in URL routing.
    Same SSR-equivalent permissive stance the page route serves.

    Replaces the prior `test_endpoint_role_unmatched_returns_404`
    which captured the over-strict behavior fixed 2026-04-29.
    """
    page = {"name": "Admin", "slug": "admin", "role": "admin", "children": []}
    ctx = StubCtx(pages=[page], user=_make_user("alice"))
    app = FastAPI()
    register_page_data_endpoint(app, ctx)

    with TestClient(app) as client:
        resp = client.get("/_termin/page-data", params={"path": "/admin"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["component_tree_ir"]["slug"] == "admin"


def test_endpoint_unknown_slug_still_returns_404():
    """Genuinely unknown slug (no page variant at all) → 404. The
    relaxation in the role-mismatch case doesn't extend to unknown
    paths."""
    page = {"name": "Admin", "slug": "admin", "role": "admin", "children": []}
    ctx = StubCtx(pages=[page], user=_make_user("alice"))
    app = FastAPI()
    register_page_data_endpoint(app, ctx)

    with TestClient(app) as client:
        resp = client.get("/_termin/page-data", params={"path": "/no-such-slug"})
        assert resp.status_code == 404
