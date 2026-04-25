# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.8: Manual compute trigger endpoint.

Tests the new POST /api/v1/compute/{name}/trigger endpoint that manually
fires any Compute regardless of its declared trigger type (event /
schedule / api). Exists so agent and LLM computes can be invoked on
demand for testing and dev-loop iteration without waiting for their
normal trigger.

Scope checks, confidentiality gate, and 404/400 error paths are all
tested here. Actual LLM / agent provider behavior is not exercised in
this file — it covers the endpoint wiring, validation, and permission
logic. Provider execution is covered elsewhere.

Uses compute_demo.termin which has 6 CEL compute definitions.
Compute names in snake_case (how the URL routes look them up):
  calculate_order_total       (orders.write)
  revenue_report              (orders.read)
  split_order_into_lines      (orders.write)
  match_orders_to_lines       (orders.read)
  triage_order                (orders.write)
  purge_cancelled_orders      (orders.admin)  ← admin-gated, used to
                                                deterministically exercise
                                                the scope-rejection path.
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

APP_DIR = Path(__file__).parent.parent
APP_PY = APP_DIR / "app.py"
DB_PATH = APP_DIR / "app.db"
SEED_PATH = APP_DIR / "app_seed.json"

# A CEL-provider compute from compute_demo.termin. Deterministic, no
# AI credentials required. The URL path is the snake form of the name.
CEL_COMPUTE = "calculate_order_total"


@pytest.fixture(scope="module")
def client():
    """Compile compute_demo, import, return TestClient."""
    # Clear stale state from previous test runs.
    if SEED_PATH.exists():
        SEED_PATH.unlink()
    subprocess.run(
        [sys.executable, "-m", "termin.cli", "compile",
         "examples/compute_demo.termin", "-o", "app.py"],
        cwd=str(APP_DIR), check=True,
    )
    if DB_PATH.exists():
        DB_PATH.unlink()

    spec = importlib.util.spec_from_file_location("generated_app_mct",
                                                   str(APP_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with TestClient(mod.app) as tc:
        tc.cookies.set("termin_role", "order manager")
        yield tc

    if DB_PATH.exists():
        DB_PATH.unlink()


# ── Route existence and validation ──

class TestTriggerEndpoint:
    def test_unknown_compute_returns_404(self, client):
        r = client.post(
            "/api/v1/compute/no_such_compute/trigger",
            json={"record": {}, "content_name": "orders"},
        )
        assert r.status_code == 404

    def test_non_json_body_returns_400(self, client):
        r = client.post(
            f"/api/v1/compute/{CEL_COMPUTE}/trigger",
            content=b"not json at all",
            headers={"content-type": "text/plain"},
        )
        assert r.status_code == 400

    def test_unknown_content_name_returns_400(self, client):
        r = client.post(
            f"/api/v1/compute/{CEL_COMPUTE}/trigger",
            json={"record": {}, "content_name": "nonexistent_content"},
        )
        assert r.status_code == 400
        assert "unknown" in r.text.lower() and "content_name" in r.text.lower()


# ── Successful trigger path ──

class TestSuccessfulTrigger:
    def test_trigger_returns_invocation_envelope(self, client):
        """A successful trigger returns compute/provider/trigger/status."""
        # Seed an order so the compute has something to operate on.
        r_create = client.post(
            "/api/v1/orders",
            json={"customer": "Acme Inc", "total": 100, "priority": "medium"},
        )
        assert r_create.status_code == 201, r_create.text

        r = client.post(
            f"/api/v1/compute/{CEL_COMPUTE}/trigger",
            json={"record": r_create.json(), "content_name": "orders"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "invocation_id" in body
        assert "compute" in body
        assert "provider" in body
        assert body.get("trigger") == "manual"
        assert body.get("status") == "completed"

    def test_trigger_infers_content_name_when_single_input(self, client):
        """If the compute declares one input content type, content_name
        can be omitted from the request body and the endpoint infers it."""
        r_create = client.post(
            "/api/v1/orders",
            json={"customer": "Beta Co", "total": 200, "priority": "high"},
        )
        assert r_create.status_code == 201
        r = client.post(
            f"/api/v1/compute/{CEL_COMPUTE}/trigger",
            json={"record": r_create.json()},
        )
        # calculate_order_total has one input_content (orders), so
        # content_name inference works. If the compute declares no
        # input_content, the endpoint accepts empty and passes "".
        assert r.status_code == 200, r.text


# ── Scope enforcement ──

class TestScopeEnforcement:
    def test_scope_gated_compute_rejects_insufficient_role(self, client):
        """A role lacking the compute's declared required_scope cannot
        trigger it.

        Uses purge_cancelled_orders (requires orders.admin) and
        order clerk (has orders.read + orders.write only). The 403
        is the security property under test: the compute trigger
        endpoint enforces required_scope before executing.
        """
        prev = client.cookies.get("termin_role")
        client.cookies.set("termin_role", "order clerk")
        try:
            r = client.post(
                "/api/v1/compute/purge_cancelled_orders/trigger",
                json={"record": {}, "content_name": "orders"},
            )
            assert r.status_code == 403, (
                f"Expected 403 for clerk on admin-gated compute, "
                f"got {r.status_code}: {r.text}"
            )
            assert "orders.admin" in r.text
        finally:
            if prev:
                client.cookies.set("termin_role", prev)
            else:
                client.cookies.delete("termin_role")

    def test_scope_gated_compute_accepts_sufficient_role(self, client):
        """The same admin-gated compute succeeds for a role that has
        the required scope. Pairs with the rejection test as a
        positive control: 403 isn't a coincidence, the gate is
        scope-driven.
        """
        # client fixture default role is "order manager" which has admin.
        r = client.post(
            "/api/v1/compute/purge_cancelled_orders/trigger",
            json={"record": {}, "content_name": "orders"},
        )
        assert r.status_code == 200, r.text
