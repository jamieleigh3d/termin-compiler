# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.8 item #4: Delete action button primitive.

Covers the new DSL form:

    For each product, show actions:
      "Delete" deletes if available, hide otherwise
      "Delete" deletes if available, disable otherwise
      "Delete" deletes if available                     (defaults to disable)

Semantics:
  - A Delete action is available iff the current user holds the content's
    `can delete` scope. No state-machine involvement.
  - Clicking the button fires DELETE /api/v1/{content}/{id}, which is
    also scope-gated at the route layer (defense in depth).
  - Using a Delete action on a content with no `can delete` access rule
    is a compile-time semantic error.

Sibling test patterns:
  tests/test_parser.py          — parse-a-string unit tests
  tests/test_analyzer.py        — semantic-error tests via _analyze
  tests/test_ir.py              — IR-level component tree assertions
  tests/test_runtime.py         — end-to-end via TestClient + IR dump
  tests/test_pagination_filter_sort.py — compile-and-import pattern
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

class TestParseDeleteAction:
    """The grammar must accept the three Delete-action forms."""

    def _minimal_program_with_delete(self, delete_line: str) -> str:
        """Shared preamble for parser tests: a minimal valid program
        that declares a Delete action on products."""
        return f'''Application: Test
Users authenticate with stub
Scopes are "read", "write", and "admin"
A "clerk" has "read" and "write"
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
    {delete_line}
'''

    def test_delete_hide_otherwise_parses(self):
        src = self._minimal_program_with_delete(
            '"Delete" deletes if available, hide otherwise')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        # The directive is inside the story
        story = program.stories[0]
        delete_dirs = [d for d in story.directives
                       if isinstance(d, ActionButtonDef) and d.label == "Delete"]
        assert len(delete_dirs) == 1
        d = delete_dirs[0]
        assert d.kind == "delete"
        assert d.unavailable_behavior == "hide"

    def test_delete_disable_otherwise_parses(self):
        src = self._minimal_program_with_delete(
            '"Delete" deletes if available, disable otherwise')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        story = program.stories[0]
        d = [x for x in story.directives
             if isinstance(x, ActionButtonDef) and x.label == "Delete"][0]
        assert d.kind == "delete"
        assert d.unavailable_behavior == "disable"

    def test_delete_no_otherwise_defaults_to_disable(self):
        src = self._minimal_program_with_delete(
            '"Delete" deletes if available')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        story = program.stories[0]
        d = [x for x in story.directives
             if isinstance(x, ActionButtonDef) and x.label == "Delete"][0]
        assert d.kind == "delete"
        assert d.unavailable_behavior == "disable"

    def test_existing_transition_action_still_parses(self):
        """Regression guard: adding Delete must not break transitions."""
        src = '''Application: Test
Users authenticate with stub
Scopes are "read" and "write"
A "user" has "read" and "write"

Content called "tickets":
  Each ticket has a title which is text, required
  Anyone with "read" can view tickets
  Anyone with "write" can create or update tickets

State for tickets called "lifecycle":
  A ticket starts as "open"
  A ticket can also be "closed"
  An open ticket can become closed if the user has "write"

As a user, I want to manage tickets:
  Show a page called "Tickets"
  Display a table of tickets with columns: title
  For each ticket, show actions:
    "Close" transitions to "closed" if available, hide otherwise
'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        story = program.stories[0]
        d = [x for x in story.directives if isinstance(x, ActionButtonDef)][0]
        # Existing transition action must still be kind="transition"
        assert d.kind == "transition"
        assert d.target_state == "closed"


# ── Analyzer-level tests ────────────────────────────────────────────

class TestAnalyzeDeleteAction:
    """Semantic checks around the Delete action."""

    VALID = '''Application: Test
Users authenticate with stub
Scopes are "read", "write", and "admin"
A "manager" has "read", "write", and "admin"

Content called "products":
  Each product has a name which is text, required
  Anyone with "read" can view products
  Anyone with "admin" can delete products

As a manager, I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name
  For each product, show actions:
    "Delete" deletes if available, hide otherwise
'''

    NO_DELETE_RULE = '''Application: Test
Users authenticate with stub
Scopes are "read" and "write"
A "user" has "read" and "write"

Content called "products":
  Each product has a name which is text, required
  Anyone with "read" can view products
  Anyone with "write" can create or update products

As a user, I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name
  For each product, show actions:
    "Delete" deletes if available, hide otherwise
'''

    def _analyze(self, source):
        program, parse_errors = parse(source)
        assert parse_errors.ok, parse_errors.format()
        return analyze(program)

    def test_delete_with_rule_is_valid(self):
        result = self._analyze(self.VALID)
        assert result.ok, result.format()

    def test_delete_without_rule_is_semantic_error(self):
        result = self._analyze(self.NO_DELETE_RULE)
        assert not result.ok
        # The message should mention both "delete" and the content name
        msgs = [str(e).lower() for e in result.errors]
        assert any("delete" in m and "products" in m for m in msgs), \
            f"Expected a delete-related semantic error mentioning 'products'; got {msgs}"


# ── Lowering / IR tests ─────────────────────────────────────────────

class TestLowerDeleteAction:
    """The Delete action must lower to an action_button component
    with action='delete' and the correct required_scope."""

    SRC = '''Application: Test
Users authenticate with stub
Scopes are "read", "write", and "admin"
A "manager" has "read", "write", and "admin"

Content called "products":
  Each product has a name which is text, required
  Anyone with "read" can view products
  Anyone with "admin" can delete products

As a manager, I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name
  For each product, show actions:
    "Delete" deletes if available, hide otherwise
'''

    def test_action_button_is_delete_kind(self):
        program, errors = parse(self.SRC)
        assert errors.ok, errors.format()
        app_spec = lower(program)
        page = app_spec.pages[0]
        data_tables = [c for c in page.children if c.type == "data_table"]
        assert len(data_tables) == 1
        row_actions = data_tables[0].props.get("row_actions", [])
        delete_btns = [b for b in row_actions
                       if b.props.get("label") == "Delete"]
        assert len(delete_btns) == 1
        btn = delete_btns[0]
        assert btn.props.get("action") == "delete", btn.props
        # Required scope is resolved from the `can delete` rule.
        assert btn.props.get("required_scope") == "admin", btn.props
        # hide_when_unavailable propagates.
        assert btn.props.get("unavailable_behavior") == "hide"
        # No target_state: this is not a state transition.
        assert "target_state" not in btn.props or \
               btn.props.get("target_state") in (None, "")


# ── Runtime end-to-end ──────────────────────────────────────────────

APP_DIR = Path(__file__).parent.parent
APP_PY = APP_DIR / "app.py"
DB_PATH = APP_DIR / "app.db"
SEED_PATH = APP_DIR / "app_seed.json"


@pytest.fixture(scope="module")
def client():
    """Compile warehouse.termin (which we will teach to use Delete),
    import, yield TestClient. Same pattern as the pagination suite."""
    if SEED_PATH.exists():
        SEED_PATH.unlink()
    subprocess.run(
        [sys.executable, "-m", "termin.cli", "compile",
         "examples/warehouse.termin", "-o", "app.py"],
        cwd=str(APP_DIR), check=True,
    )
    if DB_PATH.exists():
        DB_PATH.unlink()

    spec = importlib.util.spec_from_file_location("generated_app_del",
                                                   str(APP_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with TestClient(mod.app) as tc:
        yield tc

    if DB_PATH.exists():
        DB_PATH.unlink()


class TestDeleteButtonRuntime:
    """End-to-end: rendered button, scope-gated visibility, wire-up to
    the DELETE route."""

    def _seed_product(self, client):
        """Create a product with a unique SKU. The fixture is module-scoped,
        so the DB persists across tests — SKU must be unique per call."""
        import uuid
        sku = "DEL-" + uuid.uuid4().hex[:6].upper()
        client.cookies.set("termin_role", "warehouse manager")
        r = client.post("/api/v1/products", json={
            "sku": sku, "name": f"Deletable {sku}",
            "category": "raw material", "unit_cost": 1.0,
        })
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_manager_sees_delete_button(self, client):
        """warehouse manager has inventory.admin → hide-otherwise action
        renders as an enabled button."""
        self._seed_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        r = client.get("/inventory_dashboard")
        assert r.status_code == 200
        assert "Delete" in r.text, \
            "Manager should see the Delete button label in the rendered page"

    def test_executive_does_not_see_delete_button(self, client):
        """Executive lacks inventory.admin. The Delete action declares
        hide-otherwise, so the button element must not render — scope gate
        is the visibility gate.

        The wrapper <span data-termin-delete …> is always emitted (same
        pattern as transition buttons, for client-side re-evaluation on
        live updates). We check the actual button element, not the marker
        wrapper."""
        self._seed_product(client)
        client.cookies.set("termin_role", "executive")
        r = client.get("/inventory_dashboard")
        assert r.status_code == 200
        assert ">Delete</button>" not in r.text, \
            "Executive should not see a rendered Delete button (hide-otherwise)"

    def test_delete_route_works_for_manager(self, client):
        pid = self._seed_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        r = client.delete(f"/api/v1/products/{pid}")
        assert r.status_code in (200, 204), r.text
        # Gone.
        r2 = client.get(f"/api/v1/products/{pid}")
        assert r2.status_code == 404

    def test_delete_route_blocked_for_executive(self, client):
        pid = self._seed_product(client)
        client.cookies.set("termin_role", "executive")
        r = client.delete(f"/api/v1/products/{pid}")
        assert r.status_code == 403, r.text

    def test_delete_blocked_by_foreign_key_returns_409(self, client):
        """Regression for the production 500: deleting a product that has
        stock_levels referencing it violates SQLite's FK RESTRICT. Must
        surface as a clean 409 Conflict with a human-readable message,
        not a 500 with a raw sqlite3.IntegrityError traceback."""
        pid = self._seed_product(client)
        client.cookies.set("termin_role", "warehouse manager")
        # Create a stock_level referencing this product.
        r_stock = client.post("/api/v1/stock_levels", json={
            "product": pid, "warehouse": "Main",
            "quantity": 10, "reorder_threshold": 5,
        })
        assert r_stock.status_code == 201, r_stock.text

        r = client.delete(f"/api/v1/products/{pid}")
        assert r.status_code == 409, r.text
        body = r.json()
        # Message should name the relationship problem in author-friendly English.
        assert "reference" in body.get("detail", "").lower(), body
        # Sanity: the product still exists.
        r2 = client.get(f"/api/v1/products/{pid}")
        assert r2.status_code == 200
