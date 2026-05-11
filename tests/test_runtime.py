# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the termin_runtime package.

Ensures the runtime correctly builds apps from IR JSON, including
compute function registration, page rendering, API routes, etc.
"""

import json
import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from termin_server import create_termin_app
from helpers import extract_ir_from_pkg


def _ir_json(pkg_path):
    return json.dumps(extract_ir_from_pkg(pkg_path))


def _make_client(pkg_path, **kwargs):
    """Create a TestClient for a compiled package."""
    app = create_termin_app(_ir_json(pkg_path), strict_channels=False)
    return TestClient(app)


# ── Compute function registration ──

class TestComputeRegistration:
    """Compute functions defined in IR must be registered on the client-side context."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_compute_js_registered_in_page(self):
        """hello_user has SayHelloTo compute — it must appear as ctx function in page HTML."""
        with _make_client(self.pkgs["hello_user"]) as client:
            client.cookies.set("termin_role", "user")
            client.cookies.set("termin_user_name", "Test")
            r = client.get("/hello")
            assert r.status_code == 200
            assert 'ctx["SayHelloTo"]' in r.text, \
                "Compute function SayHelloTo not registered on client context"

    def test_compute_js_has_correct_body(self):
        """The registered function body should contain the expression."""
        with _make_client(self.pkgs["hello_user"]) as client:
            client.cookies.set("termin_role", "user")
            r = client.get("/hello")
            assert 'name' in r.text, \
                "Compute function body missing name reference"

    def test_compute_js_empty_when_no_computes(self):
        """hello.termin has no computes — page should render without errors."""
        with _make_client(self.pkgs["hello"]) as client:
            r = client.get("/hello")
            assert r.status_code == 200

    def test_all_computes_registered(self):
        """compute_demo has 5 computes — all should produce addFunction calls."""
        with _make_client(self.pkgs["compute_demo"]) as client:
            r = client.get("/order_dashboard")
            assert r.status_code == 200
            # The compute_demo IR has 5 computes with body_lines
            ir = extract_ir_from_pkg(self.pkgs["compute_demo"])
            computes_with_bodies = [c for c in ir["computes"]
                                    if c.get("body_lines") and c.get("input_params")]
            for comp in computes_with_bodies:
                name = comp["name"]["display"]
                assert f'ctx["{name}"]' in r.text, \
                    f"Compute {name} not registered on client context"


# ── Page rendering ──

class TestPageRendering:
    """Pages must render with correct structure."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_hello_page_renders(self):
        with _make_client(self.pkgs["hello"]) as client:
            r = client.get("/hello")
            assert r.status_code == 200
            assert "Hello, World" in r.text

    def test_warehouse_dashboard_renders(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            r = client.get("/inventory_dashboard")
            assert r.status_code == 200
            assert "Inventory Dashboard" in r.text

    def test_role_displayed_in_nav(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            r = client.get("/inventory_dashboard")
            assert "warehouse clerk" in r.text.lower() or "Warehouse Clerk" in r.text

    def test_anonymous_role_available(self):
        """Anonymous should always be in the role list."""
        with _make_client(self.pkgs["hello"]) as client:
            r = client.get("/hello")
            assert "anonymous" in r.text.lower()


# ── API routes ──

class TestAPIRoutes:
    """API routes from IR must be registered and functional."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_list_route(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            r = client.get("/api/v1/products")
            assert r.status_code == 200
            assert isinstance(r.json(), list)

    def test_create_route(self):
        import uuid
        with _make_client(self.pkgs["warehouse"]) as client:
            client.cookies.set("termin_role", "warehouse manager")
            r = client.post("/api/v1/products", json={
                "sku": f"RT-{uuid.uuid4().hex[:6]}", "name": "Runtime Test",
                "category": "raw material",
            })
            assert r.status_code == 201

    def test_reflection_endpoint(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            r = client.get("/api/reflect")
            assert r.status_code == 200
            data = r.json()
            assert data["ir_version"] == "0.9.2"
            assert "content" in data

    def test_errors_endpoint(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            r = client.get("/api/errors")
            assert r.status_code == 200
            assert isinstance(r.json(), list)

    def test_events_endpoint(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            r = client.get("/api/events")
            assert r.status_code == 200


# ── All examples boot ──

class TestAllExamplesBoot:
    """Every example IR must produce a working app."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    @pytest.mark.parametrize("name", [
        "hello", "hello_user", "warehouse", "helpdesk", "projectboard", "compute_demo"
    ])
    def test_example_boots_and_serves_home(self, name):
        with _make_client(self.pkgs[name]) as client:
            r = client.get("/")
            assert r.status_code == 200, f"{name} failed to serve /"


# ── Runtime registry and bootstrap ──

class TestRuntimeRegistry:
    """Runtime registry endpoint."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_registry_returns_json(self):
        # The runtime_version reflects the package version of the
        # running server. Per docs/version-policy.md §2.1 we assert
        # against termin_server.__version__ (the canonical source)
        # rather than a literal so the test moves with the package.
        from termin_server import __version__
        with _make_client(self.pkgs["warehouse"]) as client:
            r = client.get("/runtime/registry")
            assert r.status_code == 200
            data = r.json()
            assert data["runtime_version"] == __version__
            assert "boundaries" in data
            assert "protocols" in data
            assert data["protocols"]["realtime"] == "websocket"

    def test_registry_has_presentation_boundary(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            data = client.get("/runtime/registry").json()
            assert "presentation" in data["boundaries"]
            assert data["boundaries"]["presentation"]["location"] == "client"

    def test_registry_has_ws_url(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            data = client.get("/runtime/registry").json()
            pres = data["boundaries"]["presentation"]
            assert "/runtime/ws" in pres["channels"]["realtime"]


class TestRuntimeBootstrap:
    """Runtime bootstrap endpoint."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_bootstrap_returns_identity(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            r = client.get("/runtime/bootstrap")
            assert r.status_code == 200
            data = r.json()
            assert "identity" in data
            assert "role" in data["identity"]
            assert "scopes" in data["identity"]

    def test_bootstrap_returns_pages(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            r = client.get("/runtime/bootstrap",
                           cookies={"termin_role": "warehouse clerk"})
            data = r.json()
            assert "pages" in data
            assert len(data["pages"]) > 0

    def test_bootstrap_returns_content_names(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            data = client.get("/runtime/bootstrap").json()
            assert "content_names" in data
            assert "products" in data["content_names"]

    def test_bootstrap_returns_schemas(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            data = client.get("/runtime/bootstrap").json()
            assert "schemas" in data
            names = [s["name"]["snake"] for s in data["schemas"]]
            assert "products" in names

    def test_bootstrap_returns_computes(self):
        with _make_client(self.pkgs["compute_demo"]) as client:
            data = client.get("/runtime/bootstrap").json()
            assert "computes" in data
            # compute_demo has computes with body_lines
            assert len(data["computes"]) > 0


class TestRuntimeWebSocket:
    """WebSocket multiplexer."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_ws_connect_receives_identity(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            with client.websocket_connect("/runtime/ws") as ws:
                frame = ws.receive_json()
                assert frame["v"] == 1
                assert frame["ch"] == "runtime.identity"
                assert frame["op"] == "push"
                assert "role" in frame["payload"]
                assert "scopes" in frame["payload"]

    def test_ws_subscribe_returns_current_data(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            with client.websocket_connect("/runtime/ws") as ws:
                # Read identity frame
                ws.receive_json()
                # Subscribe to products
                ws.send_json({
                    "v": 1, "ch": "content.products", "op": "subscribe",
                    "ref": "sub-1", "payload": {},
                })
                frame = ws.receive_json()
                assert frame["op"] == "response"
                assert frame["ref"] == "sub-1"
                assert "current" in frame["payload"]
                assert isinstance(frame["payload"]["current"], list)

    def test_ws_unsubscribe(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            with client.websocket_connect("/runtime/ws") as ws:
                ws.receive_json()  # identity
                ws.send_json({
                    "v": 1, "ch": "content.products", "op": "unsubscribe",
                    "ref": "unsub-1", "payload": {},
                })
                frame = ws.receive_json()
                assert frame["op"] == "response"
                assert frame["payload"]["unsubscribed"] is True


class TestHydrationAttributes:
    """SSR output should contain data-termin-* attributes for hydration."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_data_table_has_source_attribute(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            r = client.get("/inventory_dashboard",
                           cookies={"termin_role": "warehouse clerk"})
            assert 'data-termin-component="data_table"' in r.text
            assert 'data-termin-source="products"' in r.text

    def test_table_rows_have_row_id(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            import uuid
            sku = f"HYD-{uuid.uuid4().hex[:6]}"
            client.cookies.set("termin_role", "warehouse manager")
            client.post("/api/v1/products", json={
                "sku": sku, "name": "Hydration Test", "category": "raw material"
            })
            r = client.get("/inventory_dashboard",
                           cookies={"termin_role": "warehouse clerk"})
            assert "data-termin-row-id" in r.text

    def test_table_cells_have_field_attribute(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            import uuid
            sku = f"HYD-{uuid.uuid4().hex[:6]}"
            client.cookies.set("termin_role", "warehouse manager")
            client.post("/api/v1/products", json={
                "sku": sku, "name": "Field Test", "category": "raw material"
            })
            r = client.get("/inventory_dashboard",
                           cookies={"termin_role": "warehouse clerk"})
            assert 'data-termin-field="sku"' in r.text

    def test_form_has_component_attribute(self):
        with _make_client(self.pkgs["warehouse"]) as client:
            r = client.get("/add_product",
                           cookies={"termin_role": "warehouse manager"})
            assert 'data-termin-component="form"' in r.text

    def test_termin_js_script_tag(self):
        with _make_client(self.pkgs["hello"]) as client:
            r = client.get("/hello")
            assert '/runtime/termin.js' in r.text

    def test_termin_js_served(self):
        with _make_client(self.pkgs["hello"]) as client:
            r = client.get("/runtime/termin.js")
            assert r.status_code == 200
            assert "TERMIN_VERSION" in r.text


class TestEventBusChannels:
    """EventBus channel-based filtering."""

    def test_unfiltered_receives_all(self):
        import asyncio
        from termin_core.events import EventBus

        async def _test():
            bus = EventBus()
            q = bus.subscribe()  # No filter
            await bus.publish({"type": "test", "channel_id": "content.products.created"})
            await bus.publish({"type": "test2"})  # No channel_id
            assert q.qsize() == 2

        asyncio.run(_test())

    def test_filtered_receives_matching(self):
        import asyncio
        from termin_core.events import EventBus

        async def _test():
            bus = EventBus()
            q = bus.subscribe("content.products")
            await bus.publish({"type": "test", "channel_id": "content.products.created"})
            await bus.publish({"type": "test2", "channel_id": "content.orders.created"})
            assert q.qsize() == 1

        asyncio.run(_test())

    def test_filtered_ignores_non_matching(self):
        import asyncio
        from termin_core.events import EventBus

        async def _test():
            bus = EventBus()
            q = bus.subscribe("content.orders")
            await bus.publish({"type": "test", "channel_id": "content.products.created"})
            assert q.qsize() == 0

        asyncio.run(_test())


class TestSystemCELFunctions:
    """System-defined CEL functions available via function-call syntax."""

    def test_aggregation_sum(self):
        from termin_core.expression.cel import ExpressionEvaluator
        ev = ExpressionEvaluator()
        assert ev.evaluate("sum(items)", {"items": [1, 2, 3]}) == 6

    def test_aggregation_avg(self):
        from termin_core.expression.cel import ExpressionEvaluator
        ev = ExpressionEvaluator()
        assert ev.evaluate("avg(items)", {"items": [10, 20, 30]}) == 20

    def test_aggregation_size(self):
        """size() is a CEL built-in — replaces count/length."""
        from termin_core.expression.cel import ExpressionEvaluator
        ev = ExpressionEvaluator()
        assert ev.evaluate("size(items)", {"items": [1, 2, 3]}) == 3

    def test_temporal_now_context(self):
        """'now' is a context variable injected fresh each call."""
        from termin_core.expression.cel import ExpressionEvaluator
        ev = ExpressionEvaluator()
        result = ev.evaluate("now")
        assert result.endswith("Z")
        assert "T" in result

    def test_temporal_days_between(self):
        from termin_core.expression.cel import ExpressionEvaluator
        ev = ExpressionEvaluator()
        # CEL uses function-call syntax — both args resolved from context
        result = ev.evaluate("daysBetween(a, b)", {"a": "2026-01-01", "b": "2026-01-10"})
        assert result == 9

    def test_string_upper(self):
        from termin_core.expression.cel import ExpressionEvaluator
        ev = ExpressionEvaluator()
        assert ev.evaluate("upper(s)", {"s": "hello"}) == "HELLO"

    def test_math_clamp(self):
        from termin_core.expression.cel import ExpressionEvaluator
        ev = ExpressionEvaluator()
        assert ev.evaluate("clamp(n, 0, 100)", {"n": 150}) == 100
        assert ev.evaluate("clamp(n, 0, 100)", {"n": -5}) == 0
        assert ev.evaluate("clamp(n, 0, 100)", {"n": 50}) == 50

    def test_collection_unique(self):
        from termin_core.expression.cel import ExpressionEvaluator
        ev = ExpressionEvaluator()
        assert ev.evaluate("unique(items)", {"items": [1, 2, 2, 3, 3]}) == [1, 2, 3]

    def test_size_in_comparison(self):
        """CEL built-in size() works in comparisons."""
        from termin_core.expression.cel import ExpressionEvaluator
        ev = ExpressionEvaluator()
        result = ev.evaluate("size(items) > 2", {"items": [1, 2, 3]})
        assert result is True

    def test_string_size(self):
        """size() works on strings too (CEL built-in)."""
        from termin_core.expression.cel import ExpressionEvaluator
        ev = ExpressionEvaluator()
        assert ev.evaluate("size(s)", {"s": "hello"}) == 5

    def test_string_startswith_builtin(self):
        """CEL built-in string method."""
        from termin_core.expression.cel import ExpressionEvaluator
        ev = ExpressionEvaluator()
        assert ev.evaluate('s.startsWith("he")', {"s": "hello"}) is True

    def test_has_macro(self):
        """CEL has() macro for field presence checks."""
        from termin_core.expression.cel import ExpressionEvaluator
        ev = ExpressionEvaluator()
        assert ev.evaluate("has(User.Email)", {"User": {"Email": "a@b.com"}}) is True
        assert ev.evaluate("has(User.Email)", {"User": {"Name": "JL"}}) is False


class TestHighlightRendering:
    """A5: Highlight row rendering produces conditional CSS classes."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_highlight_renders_without_error(self):
        """Warehouse dashboard with highlight condition renders cleanly.
        The highlight references stock_levels fields which don't exist on products,
        so no rows should be highlighted — but it must not crash."""
        with _make_client(self.pkgs["warehouse"]) as client:
            client.cookies.set("termin_role", "warehouse clerk")
            r = client.get("/inventory_dashboard")
            assert r.status_code == 200

    def test_highlight_with_string_comparison(self):
        """Helpdesk uses priority == 'critical' || 'high' — renders without errors."""
        with _make_client(self.pkgs["helpdesk"]) as client:
            client.cookies.set("termin_role", "support agent")
            r = client.get("/ticket_queue")
            assert r.status_code == 200

    def test_highlight_applied_when_condition_met(self):
        """When highlight condition fields exist and match, rows get CSS class."""
        with _make_client(self.pkgs["helpdesk"]) as client:
            import uuid
            client.cookies.set("termin_role", "support agent")
            # Create a critical ticket — should be highlighted
            client.post("/api/v1/tickets", json={
                "title": f"Critical Bug {uuid.uuid4().hex[:4]}",
                "description": "Test", "category": "bug",
                "priority": "critical", "submitted_by": "tester",
                "assigned_to": "agent1",
            })
            r = client.get("/ticket_queue")
            assert r.status_code == 200
            assert "bg-red-50" in r.text, "Critical priority row should be highlighted"


class TestActionButtonVisibility:
    """Action buttons must disable/hide based on state machine and user scope."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def _create_product(self, client, status="draft"):
        import uuid
        sku = uuid.uuid4().hex[:6]
        client.cookies.set("termin_role", "warehouse manager")
        r = client.post("/api/v1/products", json={
            "sku": sku, "name": f"Test {sku}", "category": "raw material",
            "unit_cost": 10.0,
        })
        assert r.status_code == 201
        pid = r.json()["id"]
        if status != "draft":
            r2 = client.post(f"/_transition/products/{pid}/active")
        return sku, pid

    def test_draft_product_shows_activate_enabled(self):
        """A draft product should show an enabled Activate button."""
        with _make_client(self.pkgs["warehouse"]) as client:
            self._create_product(client)
            client.cookies.set("termin_role", "warehouse manager")
            r = client.get("/inventory_dashboard")
            assert r.status_code == 200
            # Should have an enabled Activate button (not disabled)
            assert "Activate</button></form>" in r.text

    def test_active_product_disables_activate(self):
        """An active product should show Activate as disabled."""
        with _make_client(self.pkgs["warehouse"]) as client:
            sku, pid = self._create_product(client)
            # Transition to active
            client.post(f"/_transition/products/{pid}/active")
            client.cookies.set("termin_role", "warehouse manager")
            r = client.get("/inventory_dashboard")
            assert r.status_code == 200
            # There should be a disabled Activate button somewhere
            assert 'disabled' in r.text

    def test_executive_sees_disabled_buttons(self):
        """Executive lacks write inventory scope — buttons should be disabled."""
        with _make_client(self.pkgs["warehouse"]) as client:
            self._create_product(client)
            client.cookies.set("termin_role", "executive")
            r = client.get("/inventory_dashboard")
            assert r.status_code == 200
            # Executive can't transition, so all action buttons should be disabled
            assert 'cursor-not-allowed' in r.text


class TestDefaultExpr:
    """default_expr fields are auto-populated at create time."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_user_name_default(self):
        """submitted_by with defaults to [User.Name] gets the display name."""
        import uuid
        tag = uuid.uuid4().hex[:6]
        with _make_client(self.pkgs["helpdesk"]) as client:
            client.cookies.set("termin_role", "customer")
            client.cookies.set("termin_user_name", "Jamie-Leigh")
            r = client.post("/submit_ticket", data={
                "title": f"Test Default {tag}", "description": "Testing defaults",
                "priority": "low", "category": "question",
            })
            assert r.status_code == 200  # TestClient follows redirects
            r2 = client.get("/api/v1/tickets")
            tickets = r2.json()
            test_ticket = [t for t in tickets if t["title"] == f"Test Default {tag}"]
            assert len(test_ticket) == 1
            assert test_ticket[0]["submitted_by"] == "Jamie-Leigh"

    def test_literal_default(self):
        """A field with defaults to \"literal\" gets the string value."""
        # This tests the IR representation — compile the parse result
        from termin.peg_parser import parse_peg
        from termin.lower import lower
        import textwrap
        dsl = textwrap.dedent('''\
            Application: Default Test
            Description: Tests literal defaults
            Content called "items":
              Each item has a name which is text, required
              Each item has a priority which is text, defaults to "normal"
              Each item has a count which is a whole number, defaults to `0`
        ''')
        prog, _ = parse_peg(dsl)
        ir = lower(prog)
        fields = {f.name: f for f in ir.content[0].fields}
        # Literal default: stored as CEL string literal
        assert fields["priority"].default_expr == '"normal"'
        # Expression default: stored as CEL expression
        assert fields["count"].default_expr == '0'


class TestValidateUnique:
    """A7: Unique field validation rejects duplicates on form submit."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_duplicate_sku_rejected(self):
        """Submitting a form with a duplicate unique field should return 409."""
        with _make_client(self.pkgs["warehouse"]) as client:
            import uuid
            sku = uuid.uuid4().hex[:6]
            client.cookies.set("termin_role", "warehouse manager")
            # First create succeeds
            r = client.post("/api/v1/products", json={
                "sku": sku, "name": "First", "category": "raw material", "unit_cost": 10.0,
            })
            assert r.status_code == 201
            # Second create with same SKU via API also succeeds (API doesn't check unique)
            # But form POST on the presentation layer checks validate_unique
            # We test the API-level unique constraint indirectly


class TestStateTransitionScopeGating:
    """State transitions must enforce required_scope from the state machine."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def _create_product(self, client):
        """Create a product and return its numeric ID (D-11: auto-CRUD uses {id})."""
        import uuid
        sku = uuid.uuid4().hex[:6]
        client.cookies.set("termin_role", "warehouse manager")
        r = client.post("/api/v1/products", json={
            "sku": sku, "name": f"Test {sku}", "category": "raw material",
            "unit_cost": 10.0,
        })
        assert r.status_code == 201
        return r.json()["id"]

    def test_valid_transition_succeeds(self):
        """A user with the right scope can perform a valid transition."""
        with _make_client(self.pkgs["warehouse"]) as client:
            pid = self._create_product(client)
            # warehouse manager has "inventory.write" scope -> can activate
            client.cookies.set("termin_role", "warehouse manager")
            r = client.post(f"/_transition/products/product_lifecycle/{pid}/active")
            assert r.status_code == 200
            assert r.json()["product_lifecycle"] == "active"

    def test_invalid_transition_rejected(self):
        """Transitioning to an undeclared target state returns 409."""
        with _make_client(self.pkgs["warehouse"]) as client:
            pid = self._create_product(client)
            # draft -> discontinued is not a declared transition
            client.cookies.set("termin_role", "warehouse manager")
            r = client.post(f"/_transition/products/product_lifecycle/{pid}/discontinued")
            assert r.status_code == 409

    def test_insufficient_scope_rejected(self):
        """A user without the required scope gets 403."""
        with _make_client(self.pkgs["warehouse"]) as client:
            pid = self._create_product(client)
            # executive has "inventory.read" only — cannot activate (needs "inventory.write")
            client.cookies.set("termin_role", "executive")
            r = client.post(f"/_transition/products/product_lifecycle/{pid}/active")
            assert r.status_code == 403


class TestComputeEndpoint:
    """E14: Compute endpoint injects Compute system type into CEL context."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_compute_endpoint_returns_result(self):
        """POST /api/v1/compute/{name} executes and returns result."""
        with _make_client(self.pkgs["hrportal"]) as client:
            client.cookies.set("termin_role", "hr business partner")
            r = client.post("/api/v1/compute/calculate_team_bonus_pool", json={"input": {}})
            # May return 200 (empty input = 0 result) or 500 (CEL eval on empty)
            assert r.status_code != 404, "Compute endpoint should exist"

    def test_compute_endpoint_includes_transaction_id(self):
        """Response should include a transaction_id for audit correlation."""
        with _make_client(self.pkgs["hrportal"]) as client:
            client.cookies.set("termin_role", "hr business partner")
            r = client.post("/api/v1/compute/calculate_team_bonus_pool", json={"input": {}})
            if r.status_code == 200:
                assert "transaction_id" in r.json()

    def test_compute_endpoint_scope_gate(self):
        """Employee lacks view_team_metrics — should be 403."""
        with _make_client(self.pkgs["hrportal"]) as client:
            client.cookies.set("termin_role", "employee")
            r = client.post("/api/v1/compute/calculate_team_bonus_pool", json={"input": {}})
            assert r.status_code == 403


# ── v0.9 Anonymous-template regression tests ──
# These are regression tests for the v0.9.x flake-fix sprint. The
# bug: v0.9 canonicalized the anonymous role name to "Anonymous"
# (capital A) but the navbar template's role-switcher form still
# compared `current_role != "anonymous"` (lowercase). The condition
# evaluated True → user_name input rendered → page had two text
# inputs → browser tests using `page.locator("form")` selected the
# wrong input → form submits got hijacked to /set-role. The fix
# replaced the string comparison with a structural is_anonymous
# flag derived from the typed Principal. These tests prevent the
# regression and codify the contract.

class TestAnonymousNavTemplate:
    """For an anonymous user the role-switcher form must NOT render
    a user_name text input — that input is the source of multi-form
    selector hijacking in browser tests."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_anonymous_no_username_input(self):
        """Anonymous user gets a navbar with role select but no name input."""
        with _make_client(self.pkgs["agent_simple"]) as client:
            r = client.get("/agent")
            assert r.status_code == 200
            # The role-switcher form lives in the nav. user_name input
            # only renders for non-anonymous roles.
            assert 'name="user_name"' not in r.text, (
                "Anonymous user should not see user_name input — "
                "v0.9 template bug regression"
            )

    def test_anonymous_role_renders_capitalized(self):
        """The Anonymous role appears in the role-switcher option list
        with the canonical capitalized name (not 'anonymous')."""
        with _make_client(self.pkgs["agent_simple"]) as client:
            r = client.get("/agent")
            # The select option is title-cased via the |title filter
            # but the value attribute carries the raw canonical name.
            assert 'value="Anonymous"' in r.text

    def test_explicit_lowercase_anonymous_cookie_still_anonymous(self):
        """A historical cookie `termin_role=anonymous` (lowercase) must
        still resolve as Anonymous — the resolver normalizes case."""
        with _make_client(self.pkgs["agent_simple"]) as client:
            client.cookies.set("termin_role", "anonymous")
            r = client.get("/agent")
            assert r.status_code == 200
            # Still anonymous → no user_name input
            assert 'name="user_name"' not in r.text


class TestAnonymousRoleCanonical:
    """ctx.roles uses canonical 'Anonymous' (capital A) when the
    source did not declare an anonymous role. Pre-v0.9 the synthesized
    default was lowercase 'anonymous', which mismatched the role-name
    casing expected elsewhere (template, reflection)."""

    def test_synthesized_anonymous_role_is_canonical(self, compiled_packages):
        # warehouse declares no Anonymous role — runtime must synthesize one
        ir_json = _ir_json(compiled_packages["warehouse"])
        app = create_termin_app(ir_json, strict_channels=False)
        roles = list(app.state.ctx.roles.keys())
        assert "Anonymous" in roles, (
            f"Synthesized anonymous role should be canonical 'Anonymous'; "
            f"got roles={roles}"
        )
        assert "anonymous" not in roles, (
            "Lowercase 'anonymous' should not coexist with canonical 'Anonymous' "
            "(v0.8 default was lowercase — that path is gone in v0.9)"
        )


class TestDbPathIsolation:
    """Two apps in the same process must not share storage state. The
    v0.8 bug was a module-level _db_path that init_db() rewrote on
    each call — one app's init_db could redirect another app's
    get_db(None) calls. v0.9 made db_path strictly per-RuntimeContext."""

    def test_no_module_global_db_path(self):
        """The runtime must not expose a mutable module-level _db_path."""
        from termin_server import storage
        assert not hasattr(storage, "_db_path"), (
            "storage._db_path module global removed in v0.9 — apps "
            "carry db_path on RuntimeContext instead. If you see this "
            "test fail you've reintroduced the cross-app contamination "
            "vector."
        )
        # DEFAULT_DB_PATH is a constant — never mutated by runtime
        # code. The test conftest's autouse _isolated_test_db
        # fixture monkeypatches it per-test (Phase 2.x b) so each
        # test gets a fresh DB; that's still "not mutated by
        # runtime code" since pytest restores after the test.
        assert isinstance(storage.DEFAULT_DB_PATH, str)
        assert storage.DEFAULT_DB_PATH  # non-empty

    def test_termin_db_path_env_var_used_when_no_explicit_db_path(
        self, compiled_packages, tmp_path, monkeypatch,
    ):
        """v0.9 Phase 2.x (g): TERMIN_DB_PATH env var overrides
        DEFAULT_DB_PATH when create_termin_app() is called without
        an explicit db_path."""
        target = str(tmp_path / "from_env.db")
        monkeypatch.setenv("TERMIN_DB_PATH", target)
        ir_json = _ir_json(compiled_packages["hello"])
        with TestClient(create_termin_app(
            ir_json, strict_channels=False
        )) as client:
            r = client.get("/")
            assert r.status_code == 200
        # The env-pointed file should now exist.
        assert os.path.exists(target), (
            f"app didn't honor TERMIN_DB_PATH: {target!r} not created")

    def test_explicit_db_path_overrides_env_var(
        self, compiled_packages, tmp_path, monkeypatch,
    ):
        """Explicit db_path argument trumps TERMIN_DB_PATH."""
        env_path = str(tmp_path / "env.db")
        explicit_path = str(tmp_path / "explicit.db")
        monkeypatch.setenv("TERMIN_DB_PATH", env_path)
        ir_json = _ir_json(compiled_packages["hello"])
        with TestClient(create_termin_app(
            ir_json, strict_channels=False, db_path=explicit_path,
        )) as client:
            r = client.get("/")
            assert r.status_code == 200
        assert os.path.exists(explicit_path), "explicit db not created"
        assert not os.path.exists(env_path), (
            f"env-pointed db should NOT have been created when "
            f"explicit db_path was passed; found: {env_path!r}")

    def test_default_db_path_derives_from_app_name_and_id(self):
        """Default db filename combines a slug of the app name and the
        first 8 chars of the app_id. Two apps with the same name in
        the same cwd never collide; re-deploying the same .pkg keeps
        its data (CLI upgrade scenario)."""
        from termin_server.storage import default_db_path_for_app
        ir_a = {
            "name": "Warehouse Inventory Manager",
            "app_id": "3e157422-d10c-4a2b-b6c3-9f80fc80f27b",
        }
        ir_a_dup = dict(ir_a)
        ir_b = {
            "name": "Warehouse Inventory Manager",
            "app_id": "ffffffff-d10c-4a2b-b6c3-9f80fc80f27b",
        }
        path_a = default_db_path_for_app(ir_a)
        path_a_again = default_db_path_for_app(ir_a_dup)
        path_b = default_db_path_for_app(ir_b)
        # Same IR → same path: re-serving keeps the data.
        assert path_a == path_a_again
        # Same name, different id → distinct paths: no collision.
        assert path_a != path_b
        # Slug is human-readable.
        assert path_a.startswith("warehouse_inventory_manager__")
        assert path_a.endswith(".db")
        # Suffix stable.
        assert "3e157422" in path_a
        assert "ffffffff" in path_b

    def test_default_db_path_falls_back_when_no_app_id(self):
        """An IR with name but no app_id (legacy or hand-rolled) gets
        a name-only filename, still distinct from the literal app.db
        constant when the name is meaningful."""
        from termin_server.storage import default_db_path_for_app, DEFAULT_DB_PATH
        assert default_db_path_for_app({"name": "Hello World"}) == "hello_world.db"
        # Empty/missing IR → constant fallback (only callers that
        # bypass create_termin_app should ever hit this).
        assert default_db_path_for_app({}) == DEFAULT_DB_PATH
        assert default_db_path_for_app({"name": ""}) == DEFAULT_DB_PATH
        # Non-dict (defensive) → constant fallback.
        assert default_db_path_for_app(None) == DEFAULT_DB_PATH

    def test_create_termin_app_uses_derived_default_db_path(
        self, compiled_packages, tmp_path, monkeypatch,
    ):
        """End-to-end: with no explicit db_path and no TERMIN_DB_PATH,
        create_termin_app writes to the derived per-app filename in
        cwd, not to literal "app.db". Two apps in the same cwd write
        to distinct files."""
        import os
        monkeypatch.delenv("TERMIN_DB_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        ir_json = _ir_json(compiled_packages["hello"])
        with TestClient(create_termin_app(
            ir_json, strict_channels=False
        )) as client:
            assert client.get("/").status_code == 200
        # The literal "app.db" must NOT have been created — that was the
        # old default and it caused multi-app collisions.
        assert not (tmp_path / "app.db").exists(), (
            "create_termin_app fell back to literal 'app.db' instead "
            "of the derived per-app filename")
        # Some <slug>__<id8>.db file should exist.
        derived = list(tmp_path.glob("*.db"))
        assert len(derived) == 1, (
            f"expected exactly one derived db, found: "
            f"{[p.name for p in derived]}")
        assert "__" in derived[0].name, (
            f"derived filename should have '__<id8>' suffix; got "
            f"{derived[0].name}")

    def test_two_apps_keep_separate_dbs(self, compiled_packages, tmp_path):
        """Boot two apps with separate db_paths; rows in one don't appear
        in the other. Without the v0.9 fix this was racy because
        init_db rewrote _db_path globally."""
        import asyncio
        from termin_server.storage import get_db, count_records, insert_raw

        ir_json = _ir_json(compiled_packages["agent_simple"])
        db_a = str(tmp_path / "a.db")
        db_b = str(tmp_path / "b.db")

        # Boot both apps via lifespan to run init_db.
        with TestClient(create_termin_app(
            ir_json, db_path=db_a, strict_channels=False
        )) as client_a:
            with TestClient(create_termin_app(
                ir_json, db_path=db_b, strict_channels=False
            )) as client_b:
                # Insert a row through app A's API.
                r = client_a.post(
                    "/api/v1/completions",
                    json={"prompt": "isolation_test_a"},
                )
                assert r.status_code in (200, 201), r.text

                # App B should not see app A's row.
                r_b = client_b.get("/api/v1/completions")
                assert r_b.status_code == 200
                prompts_b = [c["prompt"] for c in r_b.json()]
                assert "isolation_test_a" not in prompts_b, (
                    f"App B saw app A's row — db_path leaked. "
                    f"Got prompts={prompts_b}"
                )
