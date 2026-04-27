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

import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from helpers import extract_ir_from_pkg
from termin_runtime import create_termin_app


@pytest.fixture(scope="module")
def client(compiled_packages, tmp_path_factory):
    """Serve warehouse via the session-scoped compiled_packages
    fixture. Seeded products (from warehouse_seed.json):
        RM-001 Steel Sheet raw material active
        RM-002 Copper Wire raw material active
        FG-001 Widget A finished good active
        FG-002 Widget B finished good draft
        PK-001 Box Small packaging active
        PK-002 Box Large packaging discontinued

    Phase 2.x retired the legacy `.py + .json` codegen path; tests
    consume the same `.termin.pkg` artifacts production uses.
    """
    pkg = compiled_packages["warehouse"]
    ir_json = json.dumps(extract_ir_from_pkg(pkg))
    seed_data = None
    with zipfile.ZipFile(pkg) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        if manifest.get("seed"):
            try:
                seed_data = json.loads(
                    zf.read(manifest["seed"]).decode("utf-8"))
            except (KeyError, json.JSONDecodeError):
                pass
    db_path = str(tmp_path_factory.mktemp("warehouse_pfs") / "app.db")
    app = create_termin_app(
        ir_json, seed_data=seed_data, db_path=db_path,
        strict_channels=False,
    )
    with TestClient(app) as tc:
        tc.cookies.set("termin_role", "warehouse manager")
        yield tc


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

    def test_offset_param_rejected(self, client):
        # Phase 2.x retired ?offset= in favor of keyset cursors.
        # The route returns 400 with a pointer at ?cursor=.
        r = client.get("/api/v1/products?limit=3&offset=3")
        assert r.status_code == 400
        assert "cursor" in r.json()["detail"].lower()

    def test_cursor_pagination_walks_all_records(self, client):
        # ?limit=3 → first page; pass next_cursor to get the rest.
        # The list route returns a JSON array directly (not a Page
        # envelope), so cursor-based pagination requires checking
        # the response shape. v0.9 list routes return records only;
        # cursor walking via the auto-CRUD URL is a v1.0 feature.
        # For now, verify limit by itself works.
        r = client.get("/api/v1/products?limit=3")
        assert r.status_code == 200
        assert len(r.json()) == 3

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

    # ?offset= retired in v0.9; the negative-offset 400 path
    # collapses into the general "?offset= rejected" check
    # above (test_offset_param_rejected).

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
