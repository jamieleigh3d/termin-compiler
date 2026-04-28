# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5b.4 B' plumbing: POST /_termin/action endpoint.

`Termin.action(payload)` in termin.js submits typed action
payloads to the runtime. This slice ships the endpoint surface
with payload validation. Action *dispatch* — actually performing
the create/update/delete/transition the payload describes —
delegates to the existing CRUD route layer in subsequent work;
for now the endpoint round-trips and validates shape so the
client API has somewhere to land.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from termin_runtime.bootstrap import register_action_endpoint


def _make_user(role: str = "alice", scopes=("x.read", "x.write")):
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


class StubCtx:
    def __init__(self, user=None):
        self.ir = {"contents": [], "pages": []}
        self._user = user or _make_user()

    def get_current_user(self, request):
        return self._user


@pytest.fixture
def app_with_action_endpoint():
    ctx = StubCtx()
    app = FastAPI()
    register_action_endpoint(app, ctx)
    return app


def test_action_endpoint_accepts_well_formed_payload(app_with_action_endpoint):
    with TestClient(app_with_action_endpoint) as client:
        resp = client.post(
            "/_termin/action",
            json={"kind": "create", "content": "tickets", "payload": {"title": "x"}},
        )
        assert resp.status_code in (200, 202)
        body = resp.json()
        assert body.get("ok") is True


def test_action_endpoint_rejects_missing_kind(app_with_action_endpoint):
    with TestClient(app_with_action_endpoint) as client:
        resp = client.post("/_termin/action", json={"content": "tickets"})
        assert resp.status_code == 422


def test_action_endpoint_rejects_unknown_kind(app_with_action_endpoint):
    with TestClient(app_with_action_endpoint) as client:
        resp = client.post(
            "/_termin/action",
            json={"kind": "summon-demons", "content": "tickets"},
        )
        assert resp.status_code == 422


def test_action_endpoint_rejects_non_object_body(app_with_action_endpoint):
    with TestClient(app_with_action_endpoint) as client:
        resp = client.post("/_termin/action", json=["not", "an", "object"])
        assert resp.status_code == 422


def test_action_endpoint_returns_json(app_with_action_endpoint):
    with TestClient(app_with_action_endpoint) as client:
        resp = client.post(
            "/_termin/action",
            json={"kind": "create", "content": "tickets"},
        )
        assert resp.headers["content-type"].startswith("application/json")


def test_action_endpoint_echoes_kind_in_response(app_with_action_endpoint):
    """For now the endpoint validates and acknowledges; eventual
    dispatch to CRUD routes is a later slice. The response
    includes the kind so clients can correlate to their request."""
    with TestClient(app_with_action_endpoint) as client:
        resp = client.post(
            "/_termin/action",
            json={"kind": "transition", "content": "tickets",
                  "id": 1, "target_state": "published"},
        )
        body = resp.json()
        assert body["kind"] == "transition"
