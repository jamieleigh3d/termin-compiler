# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.8 security item: Close the PUT-route state-machine backdoor.

The auto-CRUD PUT /api/v1/{content}/{id} route writes the request body
directly to the database. Before this fix, a caller could include a
state-machine-backed field (e.g., `status`) in the PUT body and bypass:

  1. The transition rules (draft -> discontinued might not be declared,
     but a PUT with {"status": "discontinued"} on a draft row would
     succeed anyway).
  2. The transition's required_scope (discontinue might require
     inventory.admin, but a caller with only inventory.write could set
     status: discontinued via PUT).
  3. Invalid state values — the PUT would write an arbitrary string.

These tests use warehouse.termin which has:

    A draft product can become active if the user has "inventory.write"
    An active product can become discontinued if the user has "inventory.admin"
    A discontinued product can become active again if the user has "inventory.admin"

Roles:
    warehouse clerk   -> inventory.read + inventory.write
    warehouse manager -> inventory.read + inventory.write + inventory.admin
    executive         -> inventory.read (no write, no admin)

The fix routes PUT body's state-machine-backed columns through
do_state_transition (which already enforces rules + scopes). State
changes in PUT take the same path as POST /_transition, so the two
endpoints have the same security posture.
"""

import importlib
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


APP_DIR = Path(__file__).parent.parent
APP_PY = APP_DIR / "app.py"
DB_PATH = APP_DIR / "app.db"
SEED_PATH = APP_DIR / "app_seed.json"


@pytest.fixture(scope="module")
def client():
    """Compile warehouse.termin, import, return TestClient."""
    if SEED_PATH.exists():
        SEED_PATH.unlink()
    subprocess.run(
        [sys.executable, "-m", "termin.cli", "compile",
         "examples/warehouse.termin", "-o", "app.py"],
        cwd=str(APP_DIR), check=True,
    )
    if DB_PATH.exists():
        DB_PATH.unlink()

    spec = importlib.util.spec_from_file_location("generated_app_put", str(APP_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with TestClient(mod.app) as tc:
        yield tc

    if DB_PATH.exists():
        DB_PATH.unlink()


def _create_draft_product(client):
    """Create a product as the manager. Returns its id (starts in draft)."""
    client.cookies.set("termin_role", "warehouse manager")
    sku = "PUT-" + uuid.uuid4().hex[:6].upper()
    r = client.post("/api/v1/products", json={
        "sku": sku, "name": f"PUT-test {sku}",
        "category": "raw material", "unit_cost": 1.0,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── The bypass scenarios (would succeed on the bug, must fail on fix) ──

class TestPutRouteBlocksUndeclaredTransitions:
    """A PUT body that includes a status field must route that field
    through the state machine. The PUT cannot be used to reach a state
    that no declared transition connects to the current state."""

    def test_clerk_cannot_skip_draft_to_discontinued(self, client):
        """draft -> discontinued is not a declared transition. PUT must
        reject it with 409 regardless of the caller's scopes."""
        pid = _create_draft_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        r = client.put(f"/api/v1/products/{pid}",
                       json={"status": "discontinued"})
        assert r.status_code == 409, r.text
        # Row unchanged.
        r2 = client.get(f"/api/v1/products/{pid}")
        assert r2.json()["status"] == "draft"

    def test_put_with_nonexistent_state_rejected(self, client):
        """A target state the state machine does not know is a 409."""
        pid = _create_draft_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        r = client.put(f"/api/v1/products/{pid}",
                       json={"status": "gibberish"})
        assert r.status_code == 409, r.text
        r2 = client.get(f"/api/v1/products/{pid}")
        assert r2.json()["status"] == "draft"


class TestPutRouteEnforcesTransitionScope:
    """Even a declared transition must be scope-checked. A caller
    lacking the transition's required_scope cannot accomplish the
    transition via a PUT body, just as they cannot via POST /_transition."""

    def test_clerk_cannot_discontinue_via_put(self, client):
        """active -> discontinued requires inventory.admin. A clerk has
        inventory.write but not inventory.admin. PUT must 403."""
        pid = _create_draft_product(client)
        # Promote to active first (manager has admin).
        client.cookies.set("termin_role", "warehouse manager")
        r = client.post(f"/_transition/products/{pid}/active")
        assert r.status_code == 200, r.text

        client.cookies.set("termin_role", "warehouse clerk")
        r = client.put(f"/api/v1/products/{pid}",
                       json={"status": "discontinued"})
        assert r.status_code == 403, r.text
        # Row unchanged.
        client.cookies.set("termin_role", "warehouse manager")
        r2 = client.get(f"/api/v1/products/{pid}")
        assert r2.json()["status"] == "active"


class TestPutRouteAllowsValidTransitions:
    """The fix must not block legitimate state changes via PUT. A
    PUT that includes a declared transition with sufficient scope
    must still succeed — the edit-modal relies on this being the
    equivalent of a POST /_transition."""

    def test_clerk_can_activate_draft_via_put(self, client):
        """draft -> active requires inventory.write. Clerk has it."""
        pid = _create_draft_product(client)
        client.cookies.set("termin_role", "warehouse clerk")
        r = client.put(f"/api/v1/products/{pid}",
                       json={"status": "active"})
        assert r.status_code == 200, r.text
        r2 = client.get(f"/api/v1/products/{pid}")
        assert r2.json()["status"] == "active"


class TestPutRouteSimultaneousStateAndFieldUpdate:
    """A PUT that carries both a state change and other field updates
    must apply them atomically: if the transition is rejected, the
    field updates must not land either. This matches the edit-modal's
    orchestrator (transition first, PUT after) so the same guarantee
    holds whether the client does the orchestration or the server does."""

    def test_rejected_transition_reverts_field_changes(self, client):
        """Clerk tries to set description AND transition to discontinued.
        The transition is forbidden, so the description change must NOT
        persist."""
        pid = _create_draft_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        client.post(f"/_transition/products/{pid}/active")

        client.cookies.set("termin_role", "warehouse clerk")
        r = client.put(f"/api/v1/products/{pid}", json={
            "description": "clerk tried to sneak this in",
            "status": "discontinued",
        })
        assert r.status_code == 403, r.text

        # Description unchanged.
        client.cookies.set("termin_role", "warehouse manager")
        r2 = client.get(f"/api/v1/products/{pid}")
        assert r2.json().get("description", "") != "clerk tried to sneak this in"

    def test_valid_transition_and_field_update_both_succeed(self, client):
        """Clerk updates description AND performs a valid transition
        (draft -> active). Both changes must land."""
        pid = _create_draft_product(client)
        client.cookies.set("termin_role", "warehouse clerk")
        r = client.put(f"/api/v1/products/{pid}", json={
            "description": "updated at activation",
            "status": "active",
        })
        assert r.status_code == 200, r.text
        r2 = client.get(f"/api/v1/products/{pid}")
        body = r2.json()
        assert body["status"] == "active"
        assert body["description"] == "updated at activation"


class TestPutRouteRegressions:
    """The fix must not break existing PUT behavior when no state
    field is present, or when the state field is equal to the current
    state (no-op state change)."""

    def test_put_without_status_still_works(self, client):
        pid = _create_draft_product(client)
        client.cookies.set("termin_role", "warehouse clerk")
        r = client.put(f"/api/v1/products/{pid}",
                       json={"description": "plain field update"})
        assert r.status_code == 200, r.text
        r2 = client.get(f"/api/v1/products/{pid}")
        assert r2.json()["description"] == "plain field update"
        assert r2.json()["status"] == "draft"  # unchanged

    def test_put_with_same_state_is_noop(self, client):
        """PUT {"status": "draft"} on a draft row is a no-op on state.
        Must not 409 for 'draft -> draft is not declared' — the state
        didn't actually change."""
        pid = _create_draft_product(client)
        client.cookies.set("termin_role", "warehouse clerk")
        r = client.put(f"/api/v1/products/{pid}", json={
            "status": "draft",
            "description": "same-state PUT",
        })
        assert r.status_code == 200, r.text
        r2 = client.get(f"/api/v1/products/{pid}")
        assert r2.json()["status"] == "draft"
        assert r2.json()["description"] == "same-state PUT"
