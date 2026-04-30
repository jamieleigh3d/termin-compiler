# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 6a.5: ownership row_filter runtime enforcement.

Per BRD #3 §3.4 / §3.5:
  - LIST routes filter by `<owner_field> = the user.id` at query time.
  - GET_ONE returns 404 for rows the principal doesn't own.
  - UPDATE returns 404 for rows the principal doesn't own; strips the
    ownership field from the body to prevent transfer.
  - DELETE returns 404 for rows the principal doesn't own.
  - CREATE stamps `<owner_field> = the user.id` regardless of body.

Uses TestClient against a compiled-and-served fixture app. The
`compiled_packages` session fixture in conftest.py compiles each
example fresh; we add a small inline fixture for a content type
that uses `is owned by` + `their own`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from termin.peg_parser import parse_peg as parse
from termin.lower import lower
from termin_core.ir.serialize import serialize_ir
from termin_runtime import create_termin_app


_OWNED_APP_SOURCE = '''Application: Owned Test
  Description: ownership runtime smoke

Identity:
  Scopes are "x.read", "x.write"
  An "alice" has "x.read" and "x.write"
  A "bob" has "x.read" and "x.write"

Content called "profiles":
  Each profile has a owner which is principal, required, unique
  Each profile has a display_name which is text, required
  Each profile is owned by owner
  Anyone with "x.read" can view their own profiles
  Anyone with "x.write" can create profiles
  Anyone with "x.write" can update their own profiles
  Anyone with "x.write" can delete their own profiles
'''
# Note: per BRD #3 §3.3, the ownership field MUST be unique. This means
# at most one row per principal — the BRD's canonical use case is "the
# user's profile" (one per user). v0.9 does not ship a multi-row
# ownership shape (e.g., "all my orders"); that's a future BRD item.
# All tests below respect the one-per-principal cardinality.


@pytest.fixture
def owned_clients(tmp_path):
    """Compile + serve a tiny app with one owned content type. Yields
    a (alice_client, bob_client) tuple. Both clients hit the same app
    so ownership filtering is observable across principals.

    The TestClient context manager fires the FastAPI lifespan startup
    event, which is what initializes the SQLite schema. Without `with`,
    the schema is never created and every CRUD call gets "no such
    table" 500s.
    """
    prog, _ = parse(_OWNED_APP_SOURCE)
    spec = lower(prog)
    ir_json = serialize_ir(spec)
    db_path = tmp_path / "owned.db"
    app = create_termin_app(ir_json, db_path=str(db_path))
    with TestClient(app) as alice, TestClient(app) as bob:
        alice.cookies.set("termin_role", "alice")
        alice.cookies.set("termin_user_name", "alice")
        bob.cookies.set("termin_role", "bob")
        bob.cookies.set("termin_user_name", "bob")
        yield alice, bob


# ── CREATE: stamps owner ──

def test_create_stamps_owner_field_with_principal_id(owned_clients):
    alice, _ = owned_clients
    resp = alice.post("/api/v1/profiles", json={"display_name": "hello"})
    assert resp.status_code == 201, resp.text
    rec = resp.json()
    # Author was not in the request body but is set automatically.
    assert "owner" in rec
    assert rec["owner"]  # non-empty principal id


def test_create_overrides_client_supplied_owner(owned_clients):
    """A malicious client can't create a row owned by someone else by
    setting the ownership field in the request body."""
    alice, _ = owned_clients
    resp = alice.post("/api/v1/profiles", json={
        "display_name": "trying to impersonate",
        "owner": "bob",  # should be overridden
    })
    assert resp.status_code == 201, resp.text
    rec = resp.json()
    # The runtime stamped author = alice's id, NOT "bob"
    assert rec["owner"] != "bob"


# ── LIST: row filter ──

def test_list_returns_only_own_rows(owned_clients):
    """Each principal has at most one profile (BRD §3.3 unique
    constraint). Alice's LIST returns her one profile; Bob's LIST
    returns his."""
    alice, bob = owned_clients

    alice.post("/api/v1/profiles", json={"display_name": "Alice"})
    bob.post("/api/v1/profiles", json={"display_name": "Bob"})

    alice_list = alice.get("/api/v1/profiles").json()
    bob_list = bob.get("/api/v1/profiles").json()

    alice_names = sorted(r["display_name"] for r in alice_list)
    bob_names = sorted(r["display_name"] for r in bob_list)
    assert alice_names == ["Alice"]
    assert bob_names == ["Bob"]


# ── GET_ONE: 404 for unowned rows ──

def test_get_one_returns_404_for_unowned_record(owned_clients):
    alice, bob = owned_clients

    bob_resp = bob.post("/api/v1/profiles", json={"display_name": "bob's secret"})
    bob_id = bob_resp.json()["id"]

    # Alice tries to read Bob's note → 404
    resp = alice.get(f"/api/v1/profiles/{bob_id}")
    assert resp.status_code == 404


def test_get_one_allows_own_record(owned_clients):
    alice, _ = owned_clients
    create_resp = alice.post("/api/v1/profiles", json={"display_name": "mine"})
    alice_id = create_resp.json()["id"]
    resp = alice.get(f"/api/v1/profiles/{alice_id}")
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "mine"


# ── UPDATE: 404 for unowned, strip-owner on own ──

def test_update_returns_404_for_unowned_record(owned_clients):
    alice, bob = owned_clients
    bob_resp = bob.post("/api/v1/profiles", json={"display_name": "bob's"})
    bob_id = bob_resp.json()["id"]

    resp = alice.put(f"/api/v1/profiles/{bob_id}", json={"display_name": "hijacked"})
    assert resp.status_code == 404

    # Verify the row still belongs to Bob
    bob_row = bob.get(f"/api/v1/profiles/{bob_id}").json()
    assert bob_row["display_name"] == "bob's"


def test_update_strips_ownership_field_from_body(owned_clients):
    """Alice's update body claims author=bob; the runtime drops it so
    Alice's own row stays Alice's."""
    alice, _ = owned_clients
    create_resp = alice.post("/api/v1/profiles", json={"display_name": "v1"})
    note_id = create_resp.json()["id"]
    original_author = create_resp.json()["owner"]

    update_resp = alice.put(
        f"/api/v1/profiles/{note_id}",
        json={"display_name": "v2", "owner": "bob"},
    )
    assert update_resp.status_code == 200
    rec = update_resp.json()
    assert rec["display_name"] == "v2"
    # Author is unchanged — the runtime stripped the override attempt
    assert rec["owner"] == original_author


# ── DELETE: 404 for unowned ──

def test_delete_returns_404_for_unowned_record(owned_clients):
    alice, bob = owned_clients
    bob_resp = bob.post("/api/v1/profiles", json={"display_name": "bob's"})
    bob_id = bob_resp.json()["id"]

    resp = alice.delete(f"/api/v1/profiles/{bob_id}")
    assert resp.status_code == 404

    # Bob's row still there
    assert bob.get(f"/api/v1/profiles/{bob_id}").status_code == 200


def test_delete_allows_own_record(owned_clients):
    alice, _ = owned_clients
    create_resp = alice.post("/api/v1/profiles", json={"display_name": "throwaway"})
    note_id = create_resp.json()["id"]

    resp = alice.delete(f"/api/v1/profiles/{note_id}")
    assert resp.status_code == 200 or resp.status_code == 204

    # Reads after delete should 404
    assert alice.get(f"/api/v1/profiles/{note_id}").status_code == 404


# ── Cross-principal isolation ──

def test_two_principals_cannot_see_each_others_data(owned_clients):
    alice, bob = owned_clients

    alice.post("/api/v1/profiles", json={"display_name": "alice's"})
    bob.post("/api/v1/profiles", json={"display_name": "bob's"})

    alice_view = {r["display_name"] for r in alice.get("/api/v1/profiles").json()}
    bob_view = {r["display_name"] for r in bob.get("/api/v1/profiles").json()}
    assert alice_view == {"alice's"}
    assert bob_view == {"bob's"}
