# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.8 item #6: Inline edit primitive.

DSL form:

    For each product, show actions:
      "Edit" edits if available, hide otherwise
    Allow inline editing of name, description

Semantics:
  - Listed fields become click-to-edit cells in the content's data_table.
  - Click a cell -> the cell content is replaced with an <input> carrying
    data-termin-inline-input + data-termin-field=<snake_name>.
  - Blur or Enter -> PUT /api/v1/{content}/{id} with {<field>: <new_value>}
    (partial update; the PUT route handles that today).
  - Success -> the cell restores display mode with the new value.
  - Failure -> the cell reverts + shows an alert with the server's detail.
  - Scope gate: requires the content's `can update` scope, same as the
    Edit action. Defense in depth: the PUT route itself is scope-gated.
  - State-machine columns (e.g., status) cannot be inline-edited in v0.8.
    The analyzer rejects the declaration; users still change state via
    transition buttons or the Edit modal's filtered state dropdown.
  - Per-field scope gating (each field with its own required scope) is a
    future iteration; v0.8 uses the content-level update scope for all
    listed fields.
"""

import importlib
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from termin.peg_parser import parse_peg as parse
from termin.ast_nodes import AllowInlineEdit
from termin.analyzer import analyze
from termin.lower import lower


# ── Parser-level tests ──────────────────────────────────────────────

class TestParseInlineEdit:
    """Grammar accepts `Allow inline editing of <fields>` directive."""

    def _minimal_program(self, inline_line: str) -> str:
        return f'''Application: Test
Identity:
  Scopes are "read" and "write"
  A "user" has "read" and "write"

Content called "products":
  Each product has a name which is text, required
  Each product has a description which is text
  Anyone with "read" can view products
  Anyone with "write" can create or update products

As a user, I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name, description
  {inline_line}
'''

    def test_single_field_parses(self):
        src = self._minimal_program("Allow inline editing of name")
        program, errors = parse(src)
        assert errors.ok, errors.format()
        story = program.stories[0]
        dirs = [d for d in story.directives
                if isinstance(d, AllowInlineEdit)]
        assert len(dirs) == 1
        assert dirs[0].fields == ["name"]

    def test_multiple_fields_parses(self):
        src = self._minimal_program("Allow inline editing of name, description")
        program, errors = parse(src)
        assert errors.ok, errors.format()
        story = program.stories[0]
        dirs = [d for d in story.directives
                if isinstance(d, AllowInlineEdit)]
        assert len(dirs) == 1
        assert dirs[0].fields == ["name", "description"]


# ── Analyzer-level tests ────────────────────────────────────────────

class TestAnalyzeInlineEdit:
    """Semantic checks: requires can-update rule; rejects state fields
    and unknown fields."""

    def _analyze(self, source):
        program, parse_errors = parse(source)
        assert parse_errors.ok, parse_errors.format()
        return analyze(program)

    VALID = '''Application: Test
Identity:
  Scopes are "read" and "write"
  A "user" has "read" and "write"

Content called "products":
  Each product has a name which is text, required
  Anyone with "read" can view products
  Anyone with "write" can update products

As a user, I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name
  Allow inline editing of name
'''

    NO_UPDATE_RULE = '''Application: Test
Identity:
  Scopes are "read"
  A "viewer" has "read"

Content called "products":
  Each product has a name which is text
  Anyone with "read" can view products

As a viewer, I want to browse products:
  Show a page called "Products"
  Display a table of products with columns: name
  Allow inline editing of name
'''

    UNKNOWN_FIELD = '''Application: Test
Identity:
  Scopes are "read" and "write"
  A "user" has "read" and "write"

Content called "products":
  Each product has a name which is text
  Anyone with "read" can view products
  Anyone with "write" can update products

As a user, I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name
  Allow inline editing of zzz_nonexistent_field
'''

    STATE_FIELD_ATTEMPTED = '''Application: Test
Identity:
  Scopes are "read" and "write"
  A "user" has "read" and "write"

Content called "products":
  Each product has a name which is text
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active
    draft can become active if the user has write
  Anyone with "read" can view products
  Anyone with "write" can update products

As a user, I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name, lifecycle
  Allow inline editing of name, lifecycle
'''

    def test_valid_declaration(self):
        result = self._analyze(self.VALID)
        assert result.ok, result.format()

    def test_missing_update_rule_is_error(self):
        result = self._analyze(self.NO_UPDATE_RULE)
        assert not result.ok
        msgs = [str(e).lower() for e in result.errors]
        assert any("inline" in m and ("update" in m or "products" in m)
                   for m in msgs), msgs

    def test_unknown_field_is_error(self):
        result = self._analyze(self.UNKNOWN_FIELD)
        assert not result.ok
        msgs = [str(e).lower() for e in result.errors]
        assert any("zzz_nonexistent_field" in m or "zzz nonexistent field" in m
                   for m in msgs), msgs

    def test_state_field_is_error(self):
        result = self._analyze(self.STATE_FIELD_ATTEMPTED)
        assert not result.ok
        msgs = [str(e).lower() for e in result.errors]
        assert any("lifecycle" in m and ("state" in m or "inline" in m)
                   for m in msgs), msgs


# ── Lowering / IR tests ─────────────────────────────────────────────

class TestLowerInlineEdit:
    """data_table props gain `inline_editable_fields` and `update_scope`."""

    SRC = '''Application: Test
Identity:
  Scopes are "read", "write", and "admin"
  A "user" has "read", "write", and "admin"

Content called "products":
  Each product has a name which is text, required
  Each product has a description which is text
  Anyone with "read" can view products
  Anyone with "write" can update products

As a user, I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name, description
  Allow inline editing of name, description
'''

    def test_inline_editable_fields_on_data_table(self):
        program, errors = parse(self.SRC)
        assert errors.ok, errors.format()
        app_spec = lower(program)
        page = app_spec.pages[0]
        data_tables = [c for c in page.children if c.type == "data_table"]
        assert len(data_tables) == 1
        dt = data_tables[0]
        # Lowering stamps the editable fields on the data_table props.
        assert dt.props.get("inline_editable_fields") == ["name", "description"]
        # And the required scope for the partial update (for client-side
        # scope-gating of the click handler).
        assert dt.props.get("inline_edit_scope") == "write"


# ── Runtime end-to-end ──────────────────────────────────────────────

APP_DIR = Path(__file__).parent.parent
APP_PY = APP_DIR / "app.py"
DB_PATH = APP_DIR / "app.db"
SEED_PATH = APP_DIR / "app_seed.json"


@pytest.fixture(scope="module")
def client(compiled_packages, tmp_path_factory):
    """Phase 2.x: legacy `compile -o app.py` + importlib pattern
    retired; consume the same .termin.pkg artifacts production
    uses."""
    from helpers import make_app_from_pkg

    db_path = str(tmp_path_factory.mktemp("warehouse_inline") / "app.db")
    app = make_app_from_pkg(compiled_packages["warehouse"], db_path)
    with TestClient(app) as tc:
        yield tc


def _create_product(client):
    client.cookies.set("termin_role", "warehouse manager")
    sku = "IE-" + uuid.uuid4().hex[:6].upper()
    r = client.post("/api/v1/products", json={
        "sku": sku, "name": f"Inline-editable {sku}",
        "category": "raw material", "unit_cost": 1.0,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestInlineEditRuntime:
    """End-to-end: table cells for editable fields carry the inline
    markers; cells for non-editable fields do not."""

    def test_editable_cells_carry_marker(self, client):
        """Cells for `name` and `description` (declared editable in
        warehouse.termin for this sprint) must carry the inline-edit
        marker attribute so the browser-automation tests can find them."""
        _create_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        r = client.get("/inventory_dashboard")
        assert r.status_code == 200
        # The data-termin-inline-editable attribute marks editable cells.
        # The test asserts the marker appears on the name column cell.
        assert 'data-termin-inline-editable' in r.text, \
            "Expected inline-edit markers on editable cells"
        assert 'data-termin-field="name"' in r.text

    def test_non_editable_cells_do_not_carry_marker(self, client):
        """SKU is not in the inline-editing list; its cells must not
        advertise as inline-editable. Verified by checking the marker
        is absent on any cell whose field is 'sku'."""
        _create_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        r = client.get("/inventory_dashboard")
        assert r.status_code == 200
        # Simple structural check: the pattern
        # data-termin-field="sku" ... data-termin-inline-editable
        # must not occur in the same element. We enforce this by looking
        # for the reverse attribute order — since renderers emit a stable
        # attribute ordering, we can verify the marker does not immediately
        # follow the field="sku" attribute.
        import re
        bad = re.search(
            r'data-termin-field="sku"[^>]*data-termin-inline-editable',
            r.text,
        )
        assert bad is None, "sku cells should not be inline-editable"

    def test_inline_edit_uses_put_route(self, client):
        """Inline edit commits via the existing PUT route. Round-trip
        confirms the route handles single-field updates (already does)."""
        pid = _create_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        r = client.put(f"/api/v1/products/{pid}",
                       json={"name": "Renamed inline"})
        assert r.status_code == 200, r.text
        r2 = client.get(f"/api/v1/products/{pid}")
        assert r2.json()["name"] == "Renamed inline"

    def test_inline_edit_blocked_for_out_of_scope(self, client):
        """Executive lacks inventory.write. PUT must 403, so even if
        they forced the inline input to appear client-side, the round
        trip would fail."""
        pid = _create_product(client)
        client.cookies.set("termin_role", "executive")
        r = client.put(f"/api/v1/products/{pid}",
                       json={"name": "Hacked"})
        assert r.status_code == 403
