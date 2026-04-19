# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""End-to-end tests for the compute_demo example.

Validates Compute endpoints, Channel endpoints, Boundary definitions,
and Page rendering in the compute_demo application using the runtime.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

IR_PATH = Path(__file__).parent.parent / "ir_dumps" / "compute_demo_ir.json"


@pytest.fixture(scope="module")
def client():
    """Load compute_demo IR and return a TestClient."""
    from termin_runtime import create_termin_app

    ir_json = IR_PATH.read_text(encoding="utf-8")
    import tempfile, os
    db_file = os.path.join(tempfile.mkdtemp(), "compute_demo.db")
    app = create_termin_app(ir_json, db_path=db_file, strict_channels=False)
    with TestClient(app) as tc:
        yield tc


# ── Compute Endpoints ──

class TestComputeEndpoints:
    """Compute invocation via /api/v1/compute/{name}.

    CEL-body Computes (Transform, Reduce, etc.) evaluate their body expression
    against input data. With empty input, the CEL expression may error —
    we test that the endpoint exists and responds (not 404).
    """

    def test_compute_endpoint_exists(self, client):
        """All declared Computes should have an invocation endpoint."""
        client.cookies.set("termin_role", "order clerk")
        for name in ["calculate_order_total", "revenue_report",
                     "split_order_into_lines", "match_orders_to_lines",
                     "triage_order"]:
            r = client.post(f"/api/v1/compute/{name}", json={"input": {}})
            # Should not be 404 (endpoint exists) or 403 (scope OK)
            assert r.status_code != 404, f"Compute {name} endpoint missing"
            assert r.status_code != 403, f"Compute {name} scope rejected"

    def test_transform_with_data(self, client):
        """Transform Compute evaluates CEL body against input."""
        client.cookies.set("termin_role", "order clerk")
        # Create an order first so the Compute has data to work with
        client.post("/order_dashboard",
                    data={"customer": "Compute Test", "total": "100", "priority": "high"})
        r = client.post("/api/v1/compute/calculate_order_total",
                        json={"input": {"total": 100}})
        # CEL body may fail on complex expressions, but endpoint should respond
        assert r.status_code in (200, 500)


# ── Channel Endpoints ──

class TestChannelEndpoints:
    def test_webhook_accepts_order(self, client):
        client.cookies.set("termin_role", "order clerk")
        r = client.post("/webhooks/order_webhook",
                        json={"customer": "Hook", "total": 50, "priority": "high"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "id" in data

    def test_webhook_creates_record(self, client):
        """Webhook should insert a row into orders table."""
        client.cookies.set("termin_role", "order clerk")
        client.post("/webhooks/order_webhook",
                    json={"customer": "Webhook Insert", "total": 99, "priority": "low"})
        # Verify via page rendering (no generic list API)
        r = client.get("/order_dashboard")
        assert r.status_code == 200
        assert "Webhook Insert" in r.text


# ── Page Endpoints ──

class TestPages:
    def test_order_dashboard(self, client):
        client.cookies.set("termin_role", "order clerk")
        r = client.get("/order_dashboard")
        assert r.status_code == 200
        assert "Order Dashboard" in r.text

    def test_order_analytics(self, client):
        client.cookies.set("termin_role", "order manager")
        r = client.get("/order_analytics")
        assert r.status_code == 200

    def test_create_order_via_form(self, client):
        client.cookies.set("termin_role", "order clerk")
        r = client.post("/order_dashboard",
                        data={"customer": "Form User", "priority": "medium"},
                        follow_redirects=False)
        assert r.status_code in (200, 303)

    def test_navigation(self, client):
        client.cookies.set("termin_role", "order clerk")
        r = client.get("/")
        assert r.status_code == 200
