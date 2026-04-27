# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.8 item #5: Edit action button primitive.

DSL form (parallel to Delete):

    For each product, show actions:
      "Edit" edits if available, hide otherwise
      "Edit" edits if available, disable otherwise
      "Edit" edits if available                     (defaults to disable)

Semantics:
  - An Edit action is available iff the current user holds the content's
    `can update` scope. The UI verb `edits` fires the data verb `update`.
  - Clicking the button opens a modal dialog with a form pre-populated
    from the row's current values.
  - The form contains all non-system fields of the Content, including
    any state-machine-driven columns. State fields render as a <select>
    restricted to valid target transitions from the current state that
    the user's scopes permit.
  - Saving non-state fields: PUT /api/v1/{content}/{id}.
  - Saving state changes: POST /_transition/{content}/{id}/{state}
    (existing route, respects transition rules + required_scope).
  - When both are changed, the client orchestrates: transition first,
    then PUT for other fields. This keeps state changes on the
    already-secured transition path and does not depend on the v0.8
    PUT-backdoor fix landing first.
  - Analyzer rejects Edit action on a content with no `can update` rule.
  - All modal elements carry data-termin-* attributes for behavioral
    testing via DOM selectors (never English text).
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from termin.peg_parser import parse_peg as parse
from termin.ast_nodes import ActionButtonDef
from termin.analyzer import analyze
from termin.lower import lower


# ── Parser-level tests ──────────────────────────────────────────────

class TestParseEditAction:
    """Grammar accepts the three Edit-action forms."""

    def _minimal_program_with_edit(self, edit_line: str) -> str:
        return f'''Application: Test
Identity:
  Scopes are "read", "write", and "admin"
  A "manager" has "read", "write", and "admin"

Content called "products":
  Each product has a name which is text, required
  Anyone with "read" can view products
  Anyone with "write" can create or update products
  Anyone with "admin" can delete products

As a manager, I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name
  For each product, show actions:
    {edit_line}
'''

    def test_edit_hide_otherwise_parses(self):
        src = self._minimal_program_with_edit(
            '"Edit" edits if available, hide otherwise')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        story = program.stories[0]
        edit_dirs = [d for d in story.directives
                     if isinstance(d, ActionButtonDef) and d.label == "Edit"]
        assert len(edit_dirs) == 1
        d = edit_dirs[0]
        assert d.kind == "edit"
        assert d.unavailable_behavior == "hide"

    def test_edit_disable_otherwise_parses(self):
        src = self._minimal_program_with_edit(
            '"Edit" edits if available, disable otherwise')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        story = program.stories[0]
        d = [x for x in story.directives
             if isinstance(x, ActionButtonDef) and x.label == "Edit"][0]
        assert d.kind == "edit"
        assert d.unavailable_behavior == "disable"

    def test_edit_no_otherwise_defaults_to_disable(self):
        src = self._minimal_program_with_edit(
            '"Edit" edits if available')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        story = program.stories[0]
        d = [x for x in story.directives
             if isinstance(x, ActionButtonDef) and x.label == "Edit"][0]
        assert d.kind == "edit"
        assert d.unavailable_behavior == "disable"


# ── Analyzer-level tests ────────────────────────────────────────────

class TestAnalyzeEditAction:
    """Semantic checks around the Edit action."""

    VALID = '''Application: Test
Identity:
  Scopes are "read", "write", and "admin"
  A "manager" has "read", "write", and "admin"

Content called "products":
  Each product has a name which is text, required
  Anyone with "read" can view products
  Anyone with "write" can create or update products

As a manager, I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name
  For each product, show actions:
    "Edit" edits if available, hide otherwise
'''

    NO_UPDATE_RULE = '''Application: Test
Identity:
  Scopes are "read"
  A "viewer" has "read"

Content called "products":
  Each product has a name which is text, required
  Anyone with "read" can view products

As a viewer, I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name
  For each product, show actions:
    "Edit" edits if available, hide otherwise
'''

    def _analyze(self, source):
        program, parse_errors = parse(source)
        assert parse_errors.ok, parse_errors.format()
        return analyze(program)

    def test_edit_with_update_rule_is_valid(self):
        result = self._analyze(self.VALID)
        assert result.ok, result.format()

    def test_edit_without_update_rule_is_semantic_error(self):
        result = self._analyze(self.NO_UPDATE_RULE)
        assert not result.ok
        msgs = [str(e).lower() for e in result.errors]
        assert any("edit" in m and ("update" in m or "products" in m)
                   for m in msgs), \
            f"Expected edit-related semantic error; got {msgs}"


# ── Lowering / IR tests ─────────────────────────────────────────────

class TestLowerEditAction:
    """The Edit action must lower to (a) an action_button with
    action='edit' and (b) an edit_modal component attached to the page
    with a form containing all non-system fields."""

    SRC = '''Application: Test
Identity:
  Scopes are "read", "write", and "admin"
  A "manager" has "read", "write", and "admin"

Content called "products":
  Each product has a SKU which is unique text, required
  Each product has a name which is text, required
  Each product has a unit cost which is currency
  Anyone with "read" can view products
  Anyone with "write" can create or update products

As a manager, I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name
  For each product, show actions:
    "Edit" edits if available, hide otherwise
'''

    def test_action_button_is_edit_kind(self):
        program, errors = parse(self.SRC)
        assert errors.ok, errors.format()
        app_spec = lower(program)
        page = app_spec.pages[0]
        data_tables = [c for c in page.children if c.type == "data_table"]
        assert len(data_tables) == 1
        row_actions = data_tables[0].props.get("row_actions", [])
        edit_btns = [b for b in row_actions
                     if b.props.get("label") == "Edit"]
        assert len(edit_btns) == 1
        btn = edit_btns[0]
        assert btn.props.get("action") == "edit", btn.props
        # The required scope is the content's update scope.
        assert btn.props.get("required_scope") == "write", btn.props
        assert btn.props.get("unavailable_behavior") == "hide"

    def test_page_has_edit_modal_component(self):
        """A page with an Edit action button must also have an edit_modal
        component among its children, with a form containing all
        non-system fields."""
        program, errors = parse(self.SRC)
        assert errors.ok, errors.format()
        app_spec = lower(program)
        page = app_spec.pages[0]
        modals = [c for c in page.children if c.type == "edit_modal"]
        assert len(modals) == 1, \
            f"Expected exactly one edit_modal; got {[c.type for c in page.children]}"
        modal = modals[0]
        assert modal.props.get("content") == "products"
        # Form fields: sku, name, unit_cost. System fields excluded.
        field_inputs = [c for c in modal.children
                        if c.type == "field_input"]
        field_names = {c.props.get("field") for c in field_inputs}
        assert "sku" in field_names
        assert "name" in field_names
        assert "unit_cost" in field_names
        assert "id" not in field_names
        assert "created_at" not in field_names


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

    db_path = str(tmp_path_factory.mktemp("warehouse_edit") / "app.db")
    app = make_app_from_pkg(compiled_packages["warehouse"], db_path)
    with TestClient(app) as tc:
        yield tc


class TestEditButtonRuntime:
    """End-to-end: rendered button, scope-gated visibility, modal
    dialog markup, data-termin-* attributes."""

    def _seed_product(self, client):
        import uuid
        sku = "EDIT-" + uuid.uuid4().hex[:6].upper()
        client.cookies.set("termin_role", "warehouse manager")
        r = client.post("/api/v1/products", json={
            "sku": sku, "name": f"Editable {sku}",
            "category": "raw material", "unit_cost": 1.0,
        })
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_manager_sees_edit_button(self, client):
        """warehouse manager has inventory.write → Edit button visible."""
        self._seed_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        r = client.get("/inventory_dashboard")
        assert r.status_code == 200
        # Use the data-termin marker, not English text — CLAUDE.md rule.
        assert "data-termin-edit" in r.text, \
            "Manager should see the Edit action (data-termin-edit marker missing)"
        assert ">Edit</button>" in r.text

    def test_executive_does_not_see_edit_button(self, client):
        """Executive lacks inventory.write. Edit declares hide-otherwise."""
        self._seed_product(client)
        client.cookies.set("termin_role", "executive")
        r = client.get("/inventory_dashboard")
        assert r.status_code == 200
        # Wrapper marker may remain; the rendered <button>Edit</button>
        # must not render for exec.
        assert ">Edit</button>" not in r.text, \
            "Executive must not see a rendered Edit button"

    def test_edit_modal_renders_on_page(self, client):
        """The page renders exactly one <dialog data-termin-edit-modal>
        per content-with-Edit, with data-termin-field on each input."""
        self._seed_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        r = client.get("/inventory_dashboard")
        assert r.status_code == 200
        assert "data-termin-edit-modal" in r.text
        # Fields from products schema (snake_cased).
        for field in ("sku", "name", "category", "unit_cost"):
            assert f'data-termin-field="{field}"' in r.text, \
                f"Modal missing field input for {field}"

    def test_edit_modal_has_save_and_cancel_buttons(self, client):
        self._seed_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        r = client.get("/inventory_dashboard")
        assert r.status_code == 200
        assert 'data-termin-action="save"' in r.text
        assert 'data-termin-action="cancel"' in r.text

    def test_edit_submit_updates_row_via_put(self, client):
        """The modal submit flow ultimately fires PUT /api/v1/products/{id}.
        That endpoint already works; this test confirms the round trip."""
        pid = self._seed_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        r = client.put(f"/api/v1/products/{pid}", json={
            "name": "Renamed via edit modal",
        })
        assert r.status_code == 200, r.text
        r2 = client.get(f"/api/v1/products/{pid}")
        assert r2.status_code == 200
        assert r2.json()["name"] == "Renamed via edit modal"

    def test_edit_put_blocked_for_executive(self, client):
        """Defense in depth — executive cannot PUT even if they somehow
        bypassed the UI and hit the route directly."""
        pid = self._seed_product(client)
        client.cookies.set("termin_role", "executive")
        r = client.put(f"/api/v1/products/{pid}", json={"name": "Hacked"})
        assert r.status_code == 403

    def test_state_field_rendered_as_valid_target_select(self, client):
        """For a content with a state machine, the modal's status input
        must be a <select> populated with valid target states from the
        current row's state, filtered by user scopes. The element must
        carry data-termin-field='product_lifecycle' (the state machine's
        snake-case field/column name in v0.9 multi-SM IR)."""
        self._seed_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        r = client.get("/inventory_dashboard")
        assert r.status_code == 200
        # The state-machine field should render as a select, not a plain
        # input. At minimum: a select element carrying
        # data-termin-field="product_lifecycle".
        import re
        m = re.search(
            r'<select[^>]*data-termin-field="product_lifecycle"[^>]*>',
            r.text,
        )
        assert m, ('Expected <select data-termin-field="product_lifecycle"> '
                   'for state machine field')
