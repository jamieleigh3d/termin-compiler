"""Tests for the IR lowering pass.

Verifies that lower(Program) -> AppSpec produces correct, fully-resolved
intermediate representations for all three example applications.
"""

from pathlib import Path

from termin.parser import parse
from termin.analyzer import analyze
from termin.lower import lower
from termin.ir import (
    ColumnType, Verb, RouteKind, HttpMethod,
    ComputeShape, ChannelDirection, ChannelDelivery, ComputeParamSpec,
)


def _load_and_lower(example: str):
    source = (Path(__file__).parent.parent / "examples" / example).read_text()
    program, errors = parse(source)
    assert errors.ok, errors.format()
    result = analyze(program)
    assert result.ok, result.format()
    return lower(program)


# ============================================================
# Warehouse example
# ============================================================

class TestWarehouseIR:
    def setup_method(self):
        self.spec = _load_and_lower("warehouse.termin")

    def test_app_name(self):
        assert self.spec.name == "Warehouse Inventory Manager"

    def test_tables(self):
        names = [t.name.snake for t in self.spec.tables]
        assert "products" in names
        assert "stock_levels" in names
        assert "reorder_alerts" in names

    def test_products_columns(self):
        products = next(t for t in self.spec.tables if t.name.snake == "products")
        col_names = [c.name for c in products.columns]
        assert "sku" in col_names
        assert "name" in col_names
        assert "unit_cost" in col_names
        assert "category" in col_names

    def test_products_column_types(self):
        products = next(t for t in self.spec.tables if t.name.snake == "products")
        cols = {c.name: c for c in products.columns}
        assert cols["sku"].column_type == ColumnType.TEXT
        assert cols["sku"].unique is True
        assert cols["sku"].required is True
        assert cols["unit_cost"].column_type == ColumnType.REAL
        assert cols["category"].enum_values == ("raw material", "finished good", "packaging")

    def test_products_state_machine(self):
        products = next(t for t in self.spec.tables if t.name.snake == "products")
        assert products.has_status_column is True
        assert products.initial_status == "draft"

    def test_stock_levels_foreign_key(self):
        sl = next(t for t in self.spec.tables if t.name.snake == "stock_levels")
        product_col = next(c for c in sl.columns if c.name == "product")
        assert product_col.foreign_key == "products"
        assert product_col.column_type == ColumnType.INTEGER

    def test_auth(self):
        assert self.spec.auth.provider == "stub"
        assert "read inventory" in self.spec.auth.scopes
        assert len(self.spec.auth.roles) == 3

    def test_access_grants(self):
        product_grants = [g for g in self.spec.access_grants if g.table == "products"]
        view_grant = next(g for g in product_grants if Verb.VIEW in g.verbs)
        assert view_grant.scope == "read inventory"
        delete_grant = next(g for g in product_grants if Verb.DELETE in g.verbs)
        assert delete_grant.scope == "admin inventory"

    def test_state_machine(self):
        sm = next(s for s in self.spec.state_machines if s.table == "products")
        assert sm.initial_state == "draft"
        assert "active" in sm.states
        assert "discontinued" in sm.states
        trans = {(t.from_state, t.to_state): t.required_scope for t in sm.transitions}
        assert trans[("draft", "active")] == "write inventory"
        assert trans[("active", "discontinued")] == "admin inventory"

    def test_events(self):
        assert len(self.spec.events) == 1
        ev = self.spec.events[0]
        assert ev.trigger == "jexl"
        assert ev.jexl_condition is not None
        assert "stockLevel" in ev.jexl_condition
        assert ev.action.target_table == "reorder_alerts"

    def test_routes(self):
        routes_by_path = {r.path: r for r in self.spec.routes}
        assert "/api/v1/products" in routes_by_path
        # Check transition routes
        activate = next(r for r in self.spec.routes if "activate" in r.path)
        assert activate.kind == RouteKind.TRANSITION
        assert activate.target_state == "active"

    def test_route_scopes(self):
        create_route = next(r for r in self.spec.routes
                           if r.path == "/api/v1/products" and r.method == HttpMethod.POST)
        assert create_route.kind == RouteKind.CREATE
        assert create_route.required_scope == "write inventory"

    def test_pages(self):
        slugs = [p.slug for p in self.spec.pages]
        assert "inventory_dashboard" in slugs
        assert "add_product" in slugs
        assert "receive_stock" in slugs
        assert "reorder_alerts" in slugs
        assert "inventory_overview" in slugs

    def test_dashboard_page(self):
        dash = next(p for p in self.spec.pages if p.slug == "inventory_dashboard")
        assert dash.display_table == "products"
        assert len(dash.table_columns) >= 4
        assert len(dash.filters) == 3
        # Category filter should be enum
        cat_filter = next(f for f in dash.filters if f.key == "category")
        assert cat_filter.filter_type == "enum"
        assert "raw material" in cat_filter.options
        # Status filter should be status
        status_filter = next(f for f in dash.filters if f.key == "status")
        assert status_filter.filter_type == "status"
        assert "draft" in status_filter.options

    def test_add_product_form(self):
        add = next(p for p in self.spec.pages if p.slug == "add_product")
        assert add.form_target_table == "products"
        assert len(add.form_fields) >= 5
        sku_field = next(f for f in add.form_fields if f.key == "sku")
        assert sku_field.input_type == "text"
        assert sku_field.required is True
        cat_field = next(f for f in add.form_fields if f.key == "category")
        assert cat_field.input_type == "enum"
        assert "packaging" in cat_field.enum_values

    def test_receive_stock_form_has_reference(self):
        rs = next(p for p in self.spec.pages if p.slug == "receive_stock")
        product_field = next(f for f in rs.form_fields if f.key == "product")
        assert product_field.input_type == "reference"
        assert product_field.reference_table == "products"
        assert product_field.reference_display_col == "name"
        assert product_field.reference_unique_col == "sku"

    def test_nav_items(self):
        labels = [n.label for n in self.spec.nav_items]
        assert "Dashboard" in labels
        assert "Add Product" in labels
        assert "Receive Stock" in labels
        assert "Alerts" in labels


# ============================================================
# Helpdesk example
# ============================================================

class TestHelpdeskIR:
    def setup_method(self):
        self.spec = _load_and_lower("helpdesk.termin")

    def test_multi_word_states(self):
        sm = next(s for s in self.spec.state_machines if s.table == "tickets")
        assert "in progress" in sm.states
        assert "waiting on customer" in sm.states
        assert "resolved" in sm.states

    def test_transition_resolution(self):
        start_route = next(r for r in self.spec.routes if "start" in r.path)
        assert start_route.target_state == "in progress"
        wait_route = next(r for r in self.spec.routes if "wait" in r.path)
        assert wait_route.target_state == "waiting on customer"
        resolve_route = next(r for r in self.spec.routes if "resolve" in r.path)
        assert resolve_route.target_state == "resolved"

    def test_ticket_priority_enum(self):
        tickets = next(t for t in self.spec.tables if t.name.snake == "tickets")
        priority = next(c for c in tickets.columns if c.name == "priority")
        assert priority.enum_values == ("low", "medium", "high", "critical")

    def test_comments_reference(self):
        comments = next(t for t in self.spec.tables if t.name.snake == "comments")
        ticket_col = next(c for c in comments.columns if c.name == "ticket")
        assert ticket_col.foreign_key == "tickets"


# ============================================================
# Project board example
# ============================================================

class TestProjectBoardIR:
    def setup_method(self):
        self.spec = _load_and_lower("projectboard.termin")

    def test_five_tables(self):
        names = [t.name.snake for t in self.spec.tables]
        assert "projects" in names
        assert "team_members" in names
        assert "sprints" in names
        assert "tasks" in names
        assert "time_logs" in names

    def test_deep_fk_chain(self):
        """tasks -> sprints -> projects (3-level FK chain)."""
        tasks = next(t for t in self.spec.tables if t.name.snake == "tasks")
        cols = {c.name: c for c in tasks.columns}
        assert cols["project"].foreign_key == "projects"
        assert cols["sprint"].foreign_key == "sprints"
        assert cols["assignee"].foreign_key == "team_members"

    def test_task_lifecycle(self):
        sm = next(s for s in self.spec.state_machines if s.table == "tasks")
        assert sm.initial_state == "backlog"
        assert "in sprint" in sm.states
        assert "in review" in sm.states
        assert "done" in sm.states

    def test_plan_transition(self):
        plan_route = next(r for r in self.spec.routes if "plan" in r.path)
        assert plan_route.target_state == "in sprint"

    def test_create_task_form_fields(self):
        ct = next(p for p in self.spec.pages if p.slug == "create_task")
        assert ct.form_target_table == "tasks"
        project_field = next(f for f in ct.form_fields if f.key == "project")
        assert project_field.input_type == "reference"
        assert project_field.reference_table == "projects"
        priority_field = next(f for f in ct.form_fields if f.key == "priority")
        assert priority_field.input_type == "enum"

    def test_seven_pages(self):
        assert len(self.spec.pages) == 7

    def test_nav_items(self):
        labels = [n.label for n in self.spec.nav_items]
        assert "Board" in labels
        assert "New Project" in labels
        assert "Dashboard" in labels


# ============================================================
# Backward compatibility
# ============================================================

class TestBackwardCompatibility:
    """Existing examples produce empty new primitive collections."""

    def test_warehouse_no_new_primitives(self):
        spec = _load_and_lower("warehouse.termin")
        assert spec.computes == ()
        assert spec.channels == ()
        assert spec.boundaries == ()

    def test_helpdesk_no_new_primitives(self):
        spec = _load_and_lower("helpdesk.termin")
        assert spec.computes == ()
        assert spec.channels == ()
        assert spec.boundaries == ()

    def test_projectboard_no_new_primitives(self):
        spec = _load_and_lower("projectboard.termin")
        assert spec.computes == ()
        assert spec.channels == ()
        assert spec.boundaries == ()

    def test_hello_no_new_primitives(self):
        spec = _load_and_lower("hello.termin")
        assert spec.computes == ()
        assert spec.channels == ()
        assert spec.boundaries == ()

    def test_hello_has_static_text(self):
        spec = _load_and_lower("hello.termin")
        assert len(spec.pages) == 1
        page = spec.pages[0]
        assert page.name == "Hello"
        assert page.role == "anonymous"
        assert "Hello, World" in page.static_texts


# ============================================================
# Compute demo example
# ============================================================

class TestComputeDemoIR:
    def setup_method(self):
        self.spec = _load_and_lower("compute_demo.termin")

    def test_app_name(self):
        assert self.spec.name == "Order Processing Demo"

    def test_tables(self):
        names = [t.name.snake for t in self.spec.tables]
        assert "orders" in names
        assert "order_lines" in names
        assert "reports" in names

    # Compute

    def test_five_computes(self):
        assert len(self.spec.computes) == 5

    def test_compute_transform(self):
        c = next(c for c in self.spec.computes if c.name.snake == "calculate_order_total")
        assert c.shape == ComputeShape.TRANSFORM
        assert "orders" in c.input_tables
        assert "orders" in c.output_tables
        assert c.required_scope == "write orders"

    def test_compute_reduce(self):
        c = next(c for c in self.spec.computes if c.name.snake == "revenue_report")
        assert c.shape == ComputeShape.REDUCE
        assert "orders" in c.input_tables

    def test_compute_expand(self):
        c = next(c for c in self.spec.computes if c.name.snake == "split_order_into_lines")
        assert c.shape == ComputeShape.EXPAND

    def test_compute_correlate(self):
        c = next(c for c in self.spec.computes if c.name.snake == "match_orders_to_lines")
        assert c.shape == ComputeShape.CORRELATE
        assert "orders" in c.input_tables
        assert "order_lines" in c.input_tables

    def test_compute_route(self):
        c = next(c for c in self.spec.computes if c.name.snake == "triage_order")
        assert c.shape == ComputeShape.ROUTE

    # Channels

    def test_four_channels(self):
        assert len(self.spec.channels) == 4

    def test_webhook_channel(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "order_webhook")
        assert ch.direction == ChannelDirection.INBOUND
        assert ch.delivery == ChannelDelivery.RELIABLE
        assert ch.carries_table == "orders"
        assert ch.endpoint == "/webhooks/orders"

    def test_sse_channel(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "order_updates_stream")
        assert ch.direction == ChannelDirection.OUTBOUND
        assert ch.delivery == ChannelDelivery.REALTIME

    def test_websocket_channel(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "order_notifications")
        assert ch.direction == ChannelDirection.BIDIRECTIONAL
        assert ch.delivery == ChannelDelivery.REALTIME
        assert ch.endpoint == "/ws/orders"

    def test_internal_channel(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "internal_order_bus")
        assert ch.direction == ChannelDirection.INTERNAL

    # Boundaries

    def test_two_boundaries(self):
        assert len(self.spec.boundaries) == 2

    def test_order_processing_boundary(self):
        b = next(b for b in self.spec.boundaries if b.name.snake == "order_processing")
        assert "orders" in b.contains_tables
        assert "order_lines" in b.contains_tables
        assert b.identity_mode == "inherit"

    def test_hello_user_no_tables(self):
        spec = _load_and_lower("hello_user.termin")
        assert spec.tables == ()

    def test_hello_user_compute_typed_params(self):
        spec = _load_and_lower("hello_user.termin")
        assert len(spec.computes) == 1
        c = spec.computes[0]
        assert c.name.display == "SayHelloTo"
        assert c.shape == ComputeShape.TRANSFORM
        assert len(c.input_params) == 1
        assert c.input_params[0].name == "u"
        assert c.input_params[0].type_name == "UserProfile"
        assert c.required_role == "LoggedInUser"

    def test_hello_user_merged_pages(self):
        spec = _load_and_lower("hello_user.termin")
        assert len(spec.pages) == 2
        anon_page = next(p for p in spec.pages if p.role == "Anonymous")
        assert "Anon, Hello!" in anon_page.static_texts
        logged_page = next(p for p in spec.pages if p.role == "LoggedInUser")
        assert "SayHelloTo(LoggedInUser.CurrentUser)" in logged_page.static_expressions

    def test_order_reporting_boundary(self):
        b = next(b for b in self.spec.boundaries if b.name.snake == "order_reporting")
        assert "reports" in b.contains_tables
        assert b.identity_mode == "restrict"
        assert "read orders" in b.identity_scopes
