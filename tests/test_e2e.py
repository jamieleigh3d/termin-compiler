# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""End-to-end validation tests per MVP Spec Section 8.

Uses FastAPI's TestClient for in-process testing — no subprocess needed.
The generated app.py is compiled, imported, and tested directly.

D-11: Routes are now auto-generated. All CRUD uses /api/v1/{content}/{key}.
v0.9.4: Transition routes use /_transition/{content}/{machine}/{key}/{target}
(closes termin-core #6 (4)).
Content paths use snake_case (e.g., /api/v1/stock_levels, not /stock-levels).
"""

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

APP_DIR = Path(__file__).parent.parent
APP_PY = APP_DIR / "app.py"
DB_PATH = APP_DIR / "app.db"


@pytest.fixture(scope="module")
def client(compiled_packages, tmp_path_factory):
    """Build a TestClient from the warehouse .termin.pkg fixture.

    Phase 2.x retired the `compile -o app.py` + importlib pattern;
    tests now consume the same .termin.pkg artifacts production
    uses. db_path is per-fixture-tmpdir so this fixture's storage
    stays isolated from other module-scoped server fixtures.
    """
    from fastapi.testclient import TestClient
    from helpers import make_app_from_pkg

    db_path = str(tmp_path_factory.mktemp("warehouse_e2e") / "app.db")
    app = make_app_from_pkg(compiled_packages["warehouse"], db_path)
    with TestClient(app) as tc:
        yield tc


# ── Helper: create a product and return its ID ──

def _create_product(client, sku, name, **extras):
    """Create a product and return its numeric ID.
    Requires warehouse manager role (inventory.admin grants CREATE)."""
    saved = client.cookies.get("termin_role")
    client.cookies.set("termin_role", "warehouse manager")
    body = {"sku": sku, "name": name, **extras}
    r = client.post("/api/v1/products", json=body)
    assert r.status_code == 201, f"Failed to create product: {r.text}"
    if saved:
        client.cookies.set("termin_role", saved)
    return r.json()["id"]


# ============================================================
# SPEC 8.1: Content tables with correct columns/types/constraints
# ============================================================

class TestSpec81_DatabaseSchema:
    def test_tables_exist(self, client):
        """All Content tables accessible via API (D-11: auto-generated routes)."""
        assert client.get("/api/v1/products").status_code == 200
        assert client.get("/api/v1/stock_levels").status_code == 200
        assert client.get("/api/v1/reorder_alerts").status_code == 200

    def test_product_fields_and_initial_state(self, client):
        client.cookies.set("termin_role", "warehouse manager")
        r = client.post("/api/v1/products", json={
            "sku": "SCHEMA-001", "name": "Schema Test",
            "unit_cost": 10.50, "category": "finished good"
        })
        assert r.status_code == 201
        d = r.json()
        assert d["sku"] == "SCHEMA-001"
        assert d["unit_cost"] == 10.50
        assert d["product_lifecycle"] == "draft"

    def test_unique_sku_constraint(self, client):
        client.cookies.set("termin_role", "warehouse manager")
        client.post("/api/v1/products", json={"sku": "DUP-001", "name": "First"})
        r = client.post("/api/v1/products", json={"sku": "DUP-001", "name": "Second"})
        assert r.status_code == 409


# ============================================================
# SPEC 8.2: API routes respond correctly (CRUD + state transitions)
# ============================================================

class TestSpec82_APIRoutes:
    def test_create_product(self, client):
        client.cookies.set("termin_role", "warehouse manager")
        r = client.post("/api/v1/products", json={
            "sku": "CRUD-001", "name": "CRUD Test", "category": "raw material"
        })
        assert r.status_code == 201
        assert "id" in r.json()

    def test_list_products(self, client):
        r = client.get("/api/v1/products")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_by_id(self, client):
        pid = _create_product(client, "GET-001", "Gettable")
        r = client.get(f"/api/v1/products/{pid}")
        assert r.status_code == 200
        assert r.json()["name"] == "Gettable"

    def test_update_product(self, client):
        pid = _create_product(client, "UPD-001", "Before")
        r = client.put(f"/api/v1/products/{pid}", json={"name": "After"})
        assert r.status_code == 200
        assert r.json()["name"] == "After"

    def test_delete_product(self, client):
        pid = _create_product(client, "DEL-001", "Deletable")
        r = client.delete(f"/api/v1/products/{pid}",
                          cookies={"termin_role": "warehouse manager"})
        assert r.status_code == 200
        assert r.json()["deleted"] is True

    def test_get_nonexistent(self, client):
        assert client.get("/api/v1/products/99999").status_code == 404

    def test_create_stock_level(self, client):
        pid = _create_product(client, "STK-001", "Stocked")
        r2 = client.post("/api/v1/stock_levels", json={
            "product": pid, "warehouse": "Main", "quantity": 50, "reorder_threshold": 10
        })
        assert r2.status_code == 201

    def test_activate_product(self, client):
        pid = _create_product(client, "ACT-001", "Activatable")
        r = client.post(f"/_transition/products/product_lifecycle/{pid}/active")
        assert r.status_code == 200
        assert r.json()["product_lifecycle"] == "active"

    def test_discontinue_product(self, client):
        pid = _create_product(client, "DIS-001", "Discontinuable")
        client.post(f"/_transition/products/product_lifecycle/{pid}/active")
        r = client.post(f"/_transition/products/product_lifecycle/{pid}/discontinued",
                        cookies={"termin_role": "warehouse manager"})
        assert r.status_code == 200
        assert r.json()["product_lifecycle"] == "discontinued"


# ============================================================
# SPEC 8.3: Access control enforced
# ============================================================

class TestSpec83_AccessControl:
    def test_clerk_cannot_delete(self, client):
        pid = _create_product(client, "PERM-001", "Protected")
        r = client.delete(f"/api/v1/products/{pid}",
                          cookies={"termin_role": "warehouse clerk"})
        assert r.status_code == 403

    def test_executive_cannot_create(self, client):
        r = client.post("/api/v1/products",
                        json={"sku": "EXEC-001", "name": "Blocked"},
                        cookies={"termin_role": "executive"})
        assert r.status_code == 403

    def test_executive_cannot_update(self, client):
        pid = _create_product(client, "NOUPD-001", "NoUpdate")
        r = client.put(f"/api/v1/products/{pid}", json={"name": "Changed"},
                       cookies={"termin_role": "executive"})
        assert r.status_code == 403

    def test_executive_can_view(self, client):
        r = client.get("/api/v1/products", cookies={"termin_role": "executive"})
        assert r.status_code == 200


# ============================================================
# SPEC 8.4: State transitions enforced
# ============================================================

class TestSpec84_StateTransitions:
    def test_cannot_activate_already_active(self, client):
        pid = _create_product(client, "ST-001", "State Test")
        client.post(f"/_transition/products/product_lifecycle/{pid}/active")
        r = client.post(f"/_transition/products/product_lifecycle/{pid}/active")
        assert r.status_code == 409

    def test_cannot_discontinue_draft(self, client):
        """No direct draft -> discontinued path exists."""
        pid = _create_product(client, "ST-002", "Draft Only")
        r = client.post(f"/_transition/products/product_lifecycle/{pid}/discontinued",
                        cookies={"termin_role": "warehouse manager"})
        assert r.status_code == 409

    def test_clerk_cannot_discontinue(self, client):
        """Clerk lacks admin scope for active -> discontinued."""
        pid = _create_product(client, "ST-003", "Clerk Block")
        client.post(f"/_transition/products/product_lifecycle/{pid}/active")
        r = client.post(f"/_transition/products/product_lifecycle/{pid}/discontinued",
                        cookies={"termin_role": "warehouse clerk"})
        assert r.status_code == 403

    def test_reactivate_discontinued(self, client):
        """Discontinued -> active with admin scope."""
        pid = _create_product(client, "ST-004", "Reactivate")
        client.post(f"/_transition/products/product_lifecycle/{pid}/active")
        client.post(f"/_transition/products/product_lifecycle/{pid}/discontinued",
                    cookies={"termin_role": "warehouse manager"})
        r = client.post(f"/_transition/products/product_lifecycle/{pid}/active",
                        cookies={"termin_role": "warehouse manager"})
        assert r.status_code == 200
        assert r.json()["product_lifecycle"] == "active"


# ============================================================
# SPEC 8.5: Events fire
# ============================================================

class TestSpec85_Events:
    def test_low_stock_creates_alert(self, client):
        pid = _create_product(client, "EVT-001", "Event Test")
        r2 = client.post("/api/v1/stock_levels", json={
            "product": pid, "warehouse": "WH-A",
            "quantity": 50, "reorder_threshold": 10
        })
        sl_id = r2.json()["id"]

        # Update stock below threshold
        client.put(f"/api/v1/stock_levels/{sl_id}",
                   json={"quantity": 5, "reorder_threshold": 10})

        alerts = client.get("/api/v1/reorder_alerts").json()
        matching = [a for a in alerts if a["product"] == pid]
        assert len(matching) >= 1
        assert matching[0]["current_quantity"] == 5
        assert matching[0]["threshold"] == 10

    def test_stock_above_threshold_no_alert(self, client):
        before_count = len(client.get("/api/v1/reorder_alerts").json())
        pid = _create_product(client, "EVT-002", "No Alert")
        r2 = client.post("/api/v1/stock_levels", json={
            "product": pid, "warehouse": "WH-B",
            "quantity": 100, "reorder_threshold": 10
        })
        sl_id = r2.json()["id"]
        client.put(f"/api/v1/stock_levels/{sl_id}", json={"quantity": 80})
        after_count = len(client.get("/api/v1/reorder_alerts").json())
        assert after_count == before_count


# ============================================================
# SPEC 8.6: UI renders
# ============================================================

class TestSpec86_UIRendering:
    def test_dashboard_renders(self, client):
        r = client.get("/inventory_dashboard")
        assert r.status_code == 200
        assert "Inventory Dashboard" in r.text
        assert "<table" in r.text

    def test_add_product_page(self, client):
        r = client.get("/add_product")
        assert r.status_code == 200
        assert "Add Product" in r.text
        assert "<form" in r.text

    def test_overview_page(self, client):
        r = client.get("/inventory_overview")
        assert r.status_code == 200
        assert "Inventory Overview" in r.text

    def test_receive_stock_page(self, client):
        r = client.get("/receive_stock")
        assert r.status_code == 200
        assert "Receive Stock" in r.text
        assert "<form" in r.text

    def test_reorder_alerts_page(self, client):
        r = client.get("/reorder_alerts")
        assert r.status_code == 200
        assert "Reorder Alerts" in r.text
        assert "<table" in r.text

    def test_dashboard_shows_data(self, client):
        r = client.get("/inventory_dashboard")
        # Should contain at least one SKU from earlier tests
        assert "SCHEMA-001" in r.text or "CRUD-001" in r.text or "EVT-001" in r.text

    def test_form_creates_product(self, client):
        r = client.post("/add_product", data={
            "sku": "FORM-001", "name": "Form Created",
            "description": "Via form", "unit_cost": "5.00", "category": "packaging"
        }, follow_redirects=False)
        assert r.status_code == 303

        # D-11: Verify via list + filter since routes now use {id} not {sku}
        products = client.get("/api/v1/products").json()
        form_product = next((p for p in products if p["sku"] == "FORM-001"), None)
        assert form_product is not None
        assert form_product["name"] == "Form Created"
        assert form_product["product_lifecycle"] == "draft"


# ============================================================
# SPEC 8.7: Navigation respects roles
# ============================================================

class TestSpec87_Navigation:
    def test_clerk_sees_dashboard(self, client):
        r = client.get("/inventory_dashboard", cookies={"termin_role": "warehouse clerk"})
        assert r.status_code == 200
        assert "Dashboard" in r.text

    def test_clerk_no_add_product_link(self, client):
        r = client.get("/inventory_dashboard", cookies={"termin_role": "warehouse clerk"})
        assert 'href="/add_product"' not in r.text

    def test_manager_sees_add_product(self, client):
        r = client.get("/inventory_dashboard", cookies={"termin_role": "warehouse manager"})
        assert "Add Product" in r.text

    def test_executive_sees_overview(self, client):
        r = client.get("/inventory_dashboard", cookies={"termin_role": "executive"})
        assert "Overview" in r.text

    def test_clerk_sees_receive_stock(self, client):
        r = client.get("/inventory_dashboard", cookies={"termin_role": "warehouse clerk"})
        assert "Receive Stock" in r.text

    def test_all_roles_see_alerts(self, client):
        r = client.get("/inventory_dashboard", cookies={"termin_role": "executive"})
        assert "Alerts" in r.text

    def test_executive_no_receive_stock(self, client):
        r = client.get("/inventory_dashboard", cookies={"termin_role": "executive"})
        assert 'href="/receive_stock"' not in r.text

    def test_role_switcher_present(self, client):
        r = client.get("/inventory_dashboard")
        assert "set-role" in r.text


# ============================================================
# SECURITY INVARIANT VALIDATION
# ============================================================

class TestSecurityInvariants:
    def test_sql_injection_neutralized(self, client):
        r = client.post("/api/v1/products", json={
            "sku": "'; DROP TABLE products; --",
            "name": "Injection Test"
        })
        assert r.status_code in (201, 422, 500)
        # Table still works
        r2 = client.get("/api/v1/products")
        assert r2.status_code == 200

    def test_scope_bypass_blocked(self, client):
        r = client.post("/api/v1/products",
                        json={"sku": "INJECT", "name": "Nope"},
                        cookies={"termin_role": "executive"})
        assert r.status_code == 403

    def test_invalid_transition_rejected(self, client):
        pid = _create_product(client, "SEC-001", "Security")
        r = client.post(f"/_transition/products/product_lifecycle/{pid}/discontinued",
                        cookies={"termin_role": "warehouse manager"})
        assert r.status_code == 409

    def test_invalid_enum_rejected(self, client):
        r = client.post("/api/v1/products", json={
            "sku": "BAD-001", "name": "Bad Category", "category": "INVALID"
        })
        assert r.status_code == 422

    def test_compile_rejects_missing_access_rules(self):
        from termin.peg_parser import parse_peg as parse
        from termin.analyzer import analyze
        source = ('Identity:\n  Scopes are "read"\n'
                  '  A "user" has "read"\n'
                  'Content called "broken":\n  Each broken has a name which is text\n')
        program, _ = parse(source)
        result = analyze(program)
        assert not result.ok
        assert result.has_security_errors

    def test_compile_rejects_undefined_reference(self):
        from termin.peg_parser import parse_peg as parse
        from termin.analyzer import analyze
        source = ('Identity:\n  Scopes are "read"\n'
                  '  A "user" has "read"\n'
                  'Content called "items":\n'
                  '  Each item has a ref which references ghosts, required\n'
                  '  Anyone with "read" can view items\n')
        program, _ = parse(source)
        result = analyze(program)
        assert not result.ok
        assert any(e.line > 0 for e in result.errors)


# ============================================================
# DEFINITION OF DONE
# ============================================================

class TestDefinitionOfDone:
    def test_app_is_running(self, client):
        assert client.get("/api/v1/products").status_code == 200

    def test_compile_under_60s(self, tmp_path):
        start = time.time()
        out = tmp_path / "warehouse_timing.termin.pkg"
        subprocess.run(
            [sys.executable, "-m", "termin.cli", "compile",
             "examples/warehouse.termin", "-o", str(out)],
            cwd=str(APP_DIR), check=True,
        )
        elapsed = time.time() - start
        assert elapsed < 60, f"Took {elapsed:.1f}s"

    def test_termin_file_readable(self):
        source = (APP_DIR / "examples" / "warehouse.termin").read_text()
        assert "Application:" in source
        assert "Each product has a" in source
        assert "As a warehouse clerk, I want to" in source
        assert "def " not in source
