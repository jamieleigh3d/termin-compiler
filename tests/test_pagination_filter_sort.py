# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.8 list-endpoint query params: pagination, filtering, sorting.

Tests that auto-generated list endpoints at /api/v1/{content} accept:
  ?limit=N and ?offset=N for pagination
  ?<field>=<value> for equality filtering (schema-validated)
  ?sort=<field> or ?sort=<field>:asc or ?sort=<field>:desc for sorting

Security invariants the test suite checks:
  - Filter field names must exist in the content schema (reject 400)
  - Sort field name must exist in the content schema (reject 400)
  - Sort direction must be asc or desc (reject 400)
  - Unsafe identifiers are blocked at the schema-lookup gate before
    reaching the storage layer

Uses the warehouse fixture from test_e2e.py (6 seeded products with
varying SKU prefix, name, category, and status) so we get deterministic
data without needing a separate harness.
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


@pytest.fixture(scope="module")
def client():
    """Compile warehouse, import, return TestClient. Seeded products:
    RM-001 Steel Sheet raw material active
    RM-002 Copper Wire raw material active
    FG-001 Widget A finished good active
    FG-002 Widget B finished good draft
    PK-001 Box Small packaging active
    PK-002 Box Large packaging discontinued"""
    subprocess.run(
        [sys.executable, "-m", "termin.cli", "compile",
         "examples/warehouse.termin", "-o", "app.py"],
        cwd=str(APP_DIR), check=True,
    )
    if DB_PATH.exists():
        DB_PATH.unlink()

    spec = importlib.util.spec_from_file_location("generated_app_pfs",
                                                   str(APP_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with TestClient(mod.app) as tc:
        # warehouse manager has inventory.read scope to list products.
        tc.cookies.set("termin_role", "warehouse manager")
        yield tc

    if DB_PATH.exists():
        DB_PATH.unlink()


# ── Pagination ──

class TestPagination:
    def test_no_params_returns_all(self, client):
        r = client.get("/api/v1/products")
        assert r.status_code == 200
        assert len(r.json()) == 6

    def test_limit_bounds_result_count(self, client):
        r = client.get("/api/v1/products?limit=3")
        assert r.status_code == 200
        assert len(r.json()) == 3

    def test_offset_skips_records(self, client):
        # ?limit=3&offset=3 returns records 4-6 in insertion order.
        r = client.get("/api/v1/products?limit=3&offset=3")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 3
        # Insertion order from the seed: positions 4, 5, 6 are
        # FG-002, PK-001, PK-002.
        skus = [row["sku"] for row in body]
        assert skus == ["FG-002", "PK-001", "PK-002"]

    def test_offset_without_limit_returns_remaining(self, client):
        r = client.get("/api/v1/products?offset=4")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        assert body[0]["sku"] == "PK-001"

    def test_limit_zero(self, client):
        r = client.get("/api/v1/products?limit=0")
        assert r.status_code == 200
        assert r.json() == []

    def test_limit_exceeds_total(self, client):
        r = client.get("/api/v1/products?limit=100")
        assert r.status_code == 200
        assert len(r.json()) == 6

    def test_limit_non_integer_rejected(self, client):
        r = client.get("/api/v1/products?limit=abc")
        assert r.status_code == 400

    def test_limit_negative_rejected(self, client):
        r = client.get("/api/v1/products?limit=-1")
        assert r.status_code == 400

    def test_offset_negative_rejected(self, client):
        r = client.get("/api/v1/products?offset=-1")
        assert r.status_code == 400

    def test_limit_exceeds_cap_rejected(self, client):
        r = client.get("/api/v1/products?limit=1001")
        assert r.status_code == 400


# ── Filtering ──

class TestFiltering:
    def test_filter_by_category_returns_only_matching(self, client):
        r = client.get("/api/v1/products?category=raw material")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        assert {row["sku"] for row in body} == {"RM-001", "RM-002"}

    def test_filter_by_sku_returns_single(self, client):
        r = client.get("/api/v1/products?sku=FG-001")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["name"] == "Widget A"

    def test_filter_by_status(self, client):
        r = client.get("/api/v1/products?product_lifecycle=active")
        assert r.status_code == 200
        body = r.json()
        # RM-001, RM-002, FG-001, PK-001 are active.
        assert len(body) == 4
        assert all(row["product_lifecycle"] == "active" for row in body)

    def test_unknown_filter_field_rejected(self, client):
        # 'unknown_field' is not on the products schema.
        r = client.get("/api/v1/products?unknown_field=x")
        assert r.status_code == 400
        assert "unknown filter field" in r.text.lower()

    def test_filter_no_match_returns_empty(self, client):
        r = client.get("/api/v1/products?category=nonexistent")
        assert r.status_code == 200
        assert r.json() == []

    def test_filter_combined_with_pagination(self, client):
        r = client.get("/api/v1/products?category=packaging&limit=1")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["category"] == "packaging"


# ── Sorting ──

class TestSorting:
    def test_sort_by_name_ascending_default(self, client):
        r = client.get("/api/v1/products?sort=name")
        assert r.status_code == 200
        names = [row["name"] for row in r.json()]
        assert names == sorted(names)

    def test_sort_explicit_asc(self, client):
        r = client.get("/api/v1/products?sort=sku:asc")
        assert r.status_code == 200
        skus = [row["sku"] for row in r.json()]
        assert skus == sorted(skus)

    def test_sort_desc(self, client):
        r = client.get("/api/v1/products?sort=sku:desc")
        assert r.status_code == 200
        skus = [row["sku"] for row in r.json()]
        assert skus == sorted(skus, reverse=True)

    def test_unknown_sort_field_rejected(self, client):
        r = client.get("/api/v1/products?sort=unknown_col")
        assert r.status_code == 400
        assert "unknown sort field" in r.text.lower()

    def test_invalid_sort_direction_rejected(self, client):
        r = client.get("/api/v1/products?sort=name:sideways")
        assert r.status_code == 400
        assert "asc" in r.text.lower() or "desc" in r.text.lower()

    def test_sort_combined_with_filter_and_pagination(self, client):
        r = client.get(
            "/api/v1/products?category=packaging&sort=sku:desc&limit=1")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["sku"] == "PK-002"


# ── Security — unsafe identifiers blocked by the schema-lookup gate ──

class TestUnsafeIdentifiersBlocked:
    """Defense in depth: even if a caller crafts SQL-injection-shaped
    query params, the schema-lookup gate rejects them before they reach
    the storage layer. The storage layer would also reject them via
    _assert_safe, but this test confirms the first gate."""

    def test_injection_attempt_in_sort_rejected(self, client):
        r = client.get(
            "/api/v1/products?sort=name;DROP TABLE products")
        assert r.status_code == 400
        # Sanity: the table still exists and is listable.
        r2 = client.get("/api/v1/products")
        assert r2.status_code == 200
        assert len(r2.json()) == 6

    def test_injection_attempt_in_filter_value_safely_parameterized(
            self, client):
        # Filter values are parameterized — an SQL-injection-shaped value
        # is harmless. It matches no record, returns empty list.
        r = client.get(
            "/api/v1/products?category='; DROP TABLE products; --")
        assert r.status_code == 200
        assert r.json() == []
        # Sanity: the table still exists.
        r2 = client.get("/api/v1/products")
        assert r2.status_code == 200
        assert len(r2.json()) == 6
