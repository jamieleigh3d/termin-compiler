"""End-to-end tests for the compute_demo example.

Validates Compute endpoints, Channel endpoints, and Boundary definitions
in the generated application.
"""

import importlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).parent.parent
APP_PY = APP_DIR / "compute_demo_app.py"
DB_PATH = APP_DIR / "compute_demo_app.db"


@pytest.fixture(scope="module")
def client():
    """Compile the compute_demo app and return a TestClient."""
    from fastapi.testclient import TestClient

    subprocess.run(
        [sys.executable, "-m", "termin.cli", "compile",
         "examples/compute_demo.termin", "-o", "compute_demo_app.py"],
        cwd=str(APP_DIR), check=True,
    )

    if DB_PATH.exists():
        DB_PATH.unlink()

    spec = importlib.util.spec_from_file_location("compute_demo", str(APP_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with TestClient(mod.app) as tc:
        yield tc

    if DB_PATH.exists():
        DB_PATH.unlink()


CLERK = {"X-User-Role": "order clerk"}
MANAGER = {"X-User-Role": "order manager"}


# ── Compute Endpoints ──

class TestComputeEndpoints:
    def test_transform(self, client):
        r = client.post("/compute/calculate_order_total",
                        json={"customer": "Test", "total": 100}, headers=CLERK)
        assert r.status_code == 200

    def test_reduce(self, client):
        r = client.post("/compute/revenue_report", json={}, headers=CLERK)
        assert r.status_code == 200
        data = r.json()
        assert "count" in data

    def test_expand(self, client):
        r = client.post("/compute/split_order_into_lines",
                        json={"items": [1, 2]}, headers=CLERK)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_correlate(self, client):
        r = client.post("/compute/match_orders_to_lines",
                        json={}, headers=CLERK)
        assert r.status_code == 200
        data = r.json()
        assert "orders" in data
        assert "order_lines" in data

    def test_route(self, client):
        r = client.post("/compute/triage_order",
                        json={"priority": "high"}, headers=CLERK)
        assert r.status_code == 200
        assert "routed_to" in r.json()


# ── Channel Endpoints ──

class TestChannelEndpoints:
    def test_webhook_accepts_order(self, client):
        r = client.post("/webhooks/orders",
                        json={"customer": "Hook", "total": 50, "priority": "high"},
                        headers=CLERK)
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"

    def test_webhook_creates_record(self, client):
        """Webhook should insert a row into orders table."""
        client.post("/webhooks/orders",
                    json={"customer": "Webhook Insert", "total": 99, "priority": "low"},
                    headers=CLERK)
        r = client.get("/order_dashboard", headers=CLERK)
        assert r.status_code == 200
        assert "Webhook Insert" in r.text


# ── Page Endpoints ──

class TestPages:
    def test_order_dashboard(self, client):
        r = client.get("/order_dashboard", headers=CLERK)
        assert r.status_code == 200
        assert "Order Dashboard" in r.text

    def test_order_analytics(self, client):
        r = client.get("/order_analytics", headers=MANAGER)
        assert r.status_code == 200

    def test_create_order_via_form(self, client):
        r = client.post("/order_dashboard", data={"customer": "Form User", "priority": "medium"},
                        headers=CLERK, follow_redirects=False)
        assert r.status_code in (200, 303)

    def test_navigation(self, client):
        r = client.get("/", headers=CLERK)
        assert r.status_code == 200
        assert "Orders" in r.text
