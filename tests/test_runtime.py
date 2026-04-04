"""Tests for the termin_runtime package.

Ensures the runtime correctly builds apps from IR JSON, including
compute function registration, page rendering, API routes, etc.
"""

import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from termin_runtime import create_termin_app


IR_DIR = Path(__file__).parent.parent / "ir_dumps"


def _load_ir(name: str) -> str:
    return (IR_DIR / f"{name}_ir.json").read_text()


def _make_client(name: str):
    """Create a TestClient for an IR dump."""
    app = create_termin_app(_load_ir(name))
    return TestClient(app)


# ── Compute function registration ──

class TestComputeRegistration:
    """Compute functions defined in IR must be registered with client-side jexl."""

    def test_compute_js_registered_in_page(self):
        """hello_user has SayHelloTo compute — it must appear as jexl.addFunction in page HTML."""
        with _make_client("hello_user") as client:
            client.cookies.set("termin_role", "LoggedInUser")
            client.cookies.set("termin_user_name", "Test")
            r = client.get("/hello")
            assert r.status_code == 200
            assert 'jexl.addFunction("SayHelloTo"' in r.text, \
                "Compute function SayHelloTo not registered with client-side jexl"

    def test_compute_js_has_correct_body(self):
        """The registered function body should contain the JEXL expression."""
        with _make_client("hello_user") as client:
            client.cookies.set("termin_role", "LoggedInUser")
            r = client.get("/hello")
            assert 'u.FirstName' in r.text, \
                "Compute function body missing u.FirstName reference"

    def test_compute_js_empty_when_no_computes(self):
        """hello.termin has no computes — compute_js should be empty but not break."""
        with _make_client("hello") as client:
            r = client.get("/hello")
            assert r.status_code == 200
            assert 'jexl.addFunction' not in r.text

    def test_all_computes_registered(self):
        """compute_demo has 5 computes — all should produce addFunction calls."""
        with _make_client("compute_demo") as client:
            r = client.get("/order_dashboard")
            assert r.status_code == 200
            # The compute_demo IR has 5 computes with body_lines
            ir = json.loads(_load_ir("compute_demo"))
            computes_with_bodies = [c for c in ir["computes"]
                                    if c.get("body_lines") and c.get("input_params")]
            for comp in computes_with_bodies:
                name = comp["name"]["display"]
                assert f'jexl.addFunction("{name}"' in r.text, \
                    f"Compute {name} not registered with jexl"


# ── Page rendering ──

class TestPageRendering:
    """Pages must render with correct structure."""

    def test_hello_page_renders(self):
        with _make_client("hello") as client:
            r = client.get("/hello")
            assert r.status_code == 200
            assert "Hello, World" in r.text

    def test_warehouse_dashboard_renders(self):
        with _make_client("warehouse") as client:
            r = client.get("/inventory_dashboard")
            assert r.status_code == 200
            assert "Inventory Dashboard" in r.text

    def test_role_displayed_in_nav(self):
        with _make_client("warehouse") as client:
            r = client.get("/inventory_dashboard")
            assert "warehouse clerk" in r.text.lower() or "Warehouse Clerk" in r.text

    def test_anonymous_role_available(self):
        """Anonymous should always be in the role list."""
        with _make_client("hello") as client:
            r = client.get("/hello")
            assert "anonymous" in r.text.lower()


# ── API routes ──

class TestAPIRoutes:
    """API routes from IR must be registered and functional."""

    def test_list_route(self):
        with _make_client("warehouse") as client:
            r = client.get("/api/v1/products")
            assert r.status_code == 200
            assert isinstance(r.json(), list)

    def test_create_route(self):
        with _make_client("warehouse") as client:
            r = client.post("/api/v1/products", json={
                "sku": "RT-001", "name": "Runtime Test", "category": "raw material"
            })
            assert r.status_code == 201

    def test_reflection_endpoint(self):
        with _make_client("warehouse") as client:
            r = client.get("/api/reflect")
            assert r.status_code == 200
            data = r.json()
            assert data["ir_version"] == "0.2.0"
            assert "content" in data

    def test_errors_endpoint(self):
        with _make_client("warehouse") as client:
            r = client.get("/api/errors")
            assert r.status_code == 200
            assert isinstance(r.json(), list)

    def test_events_endpoint(self):
        with _make_client("warehouse") as client:
            r = client.get("/api/events")
            assert r.status_code == 200


# ── All examples boot ──

class TestAllExamplesBoot:
    """Every example IR must produce a working app."""

    @pytest.mark.parametrize("name", [
        "hello", "hello_user", "warehouse", "helpdesk", "projectboard", "compute_demo"
    ])
    def test_example_boots_and_serves_home(self, name):
        with _make_client(name) as client:
            r = client.get("/")
            assert r.status_code == 200, f"{name} failed to serve /"
