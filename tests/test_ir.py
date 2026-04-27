# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the IR lowering pass.

Verifies that lower(Program) -> AppSpec produces correct, fully-resolved
intermediate representations for all three example applications.
"""

from pathlib import Path

import pytest

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower
from termin.ir import (
    FieldType, Verb, RouteKind, HttpMethod,
    ComputeShape, ChannelDirection, ChannelDelivery, ComputeParamSpec,
    PageEntry, ComponentNode, PropValue,
)


def _find_child(page_or_node, comp_type, **prop_filters):
    """Find first child component of given type, optionally matching prop values."""
    children = page_or_node.children if hasattr(page_or_node, 'children') else []
    for ch in children:
        if ch.type == comp_type:
            if all(ch.props.get(k) == v for k, v in prop_filters.items()):
                return ch
    return None


def _find_children(page_or_node, comp_type):
    """Find all children of given type."""
    children = page_or_node.children if hasattr(page_or_node, 'children') else []
    return [ch for ch in children if ch.type == comp_type]


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
        names = [t.name.snake for t in self.spec.content]
        assert "products" in names
        assert "stock_levels" in names
        assert "reorder_alerts" in names

    def test_products_columns(self):
        products = next(t for t in self.spec.content if t.name.snake == "products")
        col_names = [c.name for c in products.fields]
        assert "sku" in col_names
        assert "name" in col_names
        assert "unit_cost" in col_names
        assert "category" in col_names

    def test_products_column_types(self):
        products = next(t for t in self.spec.content if t.name.snake == "products")
        cols = {c.name: c for c in products.fields}
        assert cols["sku"].column_type == FieldType.TEXT
        assert cols["sku"].unique is True
        assert cols["sku"].required is True
        assert cols["unit_cost"].column_type == FieldType.REAL
        assert cols["category"].enum_values == ("raw material", "finished good", "packaging")

    def test_products_business_types(self):
        products = next(t for t in self.spec.content if t.name.snake == "products")
        cols = {c.name: c for c in products.fields}
        assert cols["sku"].business_type == "text"
        assert cols["unit_cost"].business_type == "currency"
        assert cols["category"].business_type == "enum"

    def test_products_state_machine(self):
        products = next(t for t in self.spec.content if t.name.snake == "products")
        # v0.9: multi-SM — per-content list keyed by snake_case field name.
        assert len(products.state_machines) == 1
        entry = products.state_machines[0]
        assert entry["machine_name"] == "product_lifecycle"
        assert entry["initial"] == "draft"

    def test_stock_levels_foreign_key(self):
        sl = next(t for t in self.spec.content if t.name.snake == "stock_levels")
        product_col = next(c for c in sl.fields if c.name == "product")
        assert product_col.foreign_key == "products"
        assert product_col.column_type == FieldType.INTEGER

    def test_auth(self):
        assert self.spec.auth.provider == "stub"
        assert "inventory.read" in self.spec.auth.scopes
        assert len(self.spec.auth.roles) == 3

    def test_access_grants(self):
        product_grants = [g for g in self.spec.access_grants if g.content == "products"]
        view_grant = next(g for g in product_grants if Verb.VIEW in g.verbs)
        assert view_grant.scope == "inventory.read"
        delete_grant = next(g for g in product_grants if Verb.DELETE in g.verbs)
        assert delete_grant.scope == "inventory.admin"

    def test_state_machine(self):
        sm = next(s for s in self.spec.state_machines if s.content_ref == "products")
        assert sm.initial_state == "draft"
        assert "active" in sm.states
        assert "discontinued" in sm.states
        trans = {(t.from_state, t.to_state): t.required_scope for t in sm.transitions}
        assert trans[("draft", "active")] == "inventory.write"
        assert trans[("active", "discontinued")] == "inventory.admin"

    def test_state_machine_primitive_type(self):
        sm = next(s for s in self.spec.state_machines if s.content_ref == "products")
        assert sm.primitive_type == "content"

    def test_reflection_enabled(self):
        assert self.spec.reflection_enabled is True

    def test_events(self):
        assert len(self.spec.events) == 1
        ev = self.spec.events[0]
        assert ev.trigger == "expr"
        assert ev.condition_expr is not None
        assert "stockLevel" in ev.condition_expr
        assert ev.action.target_content == "reorder_alerts"

    def test_routes(self):
        routes_by_path = {r.path: r for r in self.spec.routes}
        assert "/api/v1/products" in routes_by_path
        # v0.9: transition routes include the machine_name segment so
        # multi-state-machine content can disambiguate.
        activate = next(r for r in self.spec.routes if r.target_state == "active")
        assert activate.kind == RouteKind.TRANSITION
        assert activate.path == "/api/v1/products/{id}/_transition/product_lifecycle/active"

    def test_route_scopes(self):
        create_route = next(r for r in self.spec.routes
                           if r.path == "/api/v1/products" and r.method == HttpMethod.POST)
        assert create_route.kind == RouteKind.CREATE
        assert create_route.required_scope == "inventory.admin"

    def test_pages(self):
        slugs = [p.slug for p in self.spec.pages]
        assert "inventory_dashboard" in slugs
        assert "add_product" in slugs
        assert "receive_stock" in slugs
        assert "reorder_alerts" in slugs
        assert "inventory_overview" in slugs

    def test_dashboard_page(self):
        dash = next(p for p in self.spec.pages if p.slug == "inventory_dashboard")
        dt = _find_child(dash, "data_table")
        assert dt is not None
        assert dt.props["source"] == "products"
        assert len(dt.props["columns"]) >= 4
        filters = _find_children(dt, "filter")
        assert len(filters) == 3
        cat_filter = next(f for f in filters if f.props["field"] == "category")
        assert cat_filter.props["mode"] == "enum"
        assert "raw material" in cat_filter.props["options"]
        # v0.9: filter on the state machine column uses the field's
        # snake_case column name, not the generic "status".
        status_filter = next(f for f in filters if f.props["field"] == "product_lifecycle")
        assert status_filter.props["mode"] == "state"
        assert "draft" in status_filter.props["options"]

    def test_search_fields_split_by_or(self):
        """'Allow searching by SKU or name' must produce two separate fields, not one."""
        dash = next(p for p in self.spec.pages if p.slug == "inventory_dashboard")
        dt = _find_child(dash, "data_table")
        search = _find_child(dt, "search")
        assert search is not None, "No search component found"
        fields = search.props["fields"]
        assert isinstance(fields, list)
        assert len(fields) == 2, f"Expected 2 search fields, got {fields}"
        assert "sku" in fields
        assert "name" in fields
        # Ensure no field is a single character (string iteration bug)
        for f in fields:
            assert len(f) > 1, f"Search field '{f}' is a single character — likely string iteration bug"

    def test_add_product_form(self):
        add = next(p for p in self.spec.pages if p.slug == "add_product")
        form = _find_child(add, "form")
        assert form is not None
        assert form.props["target"] == "products"
        field_inputs = _find_children(form, "field_input")
        assert len(field_inputs) >= 5
        sku = next(f for f in field_inputs if f.props["field"] == "sku")
        assert sku.props["input_type"] == "text"
        assert sku.props.get("required") is True
        cat = next(f for f in field_inputs if f.props["field"] == "category")
        assert cat.props["input_type"] == "enum"
        assert "packaging" in cat.props["enum_values"]

    def test_receive_stock_form_has_reference(self):
        rs = next(p for p in self.spec.pages if p.slug == "receive_stock")
        form = _find_child(rs, "form")
        field_inputs = _find_children(form, "field_input")
        product = next(f for f in field_inputs if f.props["field"] == "product")
        assert product.props["input_type"] == "reference"
        assert product.props["reference_content"] == "products"
        assert product.props["reference_display_col"] == "name"
        assert product.props["reference_unique_col"] == "sku"

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
        sm = next(s for s in self.spec.state_machines if s.content_ref == "tickets")
        assert "in progress" in sm.states
        assert "waiting on customer" in sm.states
        assert "resolved" in sm.states

    def test_transition_resolution(self):
        # D-11: Auto-generated transition routes use /_transition/{target_state}
        transition_routes = [r for r in self.spec.routes if r.kind == RouteKind.TRANSITION and r.content_ref == "tickets"]
        target_states = {r.target_state for r in transition_routes}
        assert "in progress" in target_states
        assert "waiting on customer" in target_states
        assert "resolved" in target_states
        assert "closed" in target_states

    def test_ticket_priority_enum(self):
        tickets = next(t for t in self.spec.content if t.name.snake == "tickets")
        priority = next(c for c in tickets.fields if c.name == "priority")
        assert priority.enum_values == ("low", "medium", "high", "critical")

    def test_comments_reference(self):
        comments = next(t for t in self.spec.content if t.name.snake == "comments")
        ticket_col = next(c for c in comments.fields if c.name == "ticket")
        assert ticket_col.foreign_key == "tickets"

    def test_business_types(self):
        tickets = next(t for t in self.spec.content if t.name.snake == "tickets")
        cols = {c.name: c for c in tickets.fields}
        assert cols["priority"].business_type == "enum"
        assert cols["title"].business_type == "text"

    def test_state_machine_primitive_type(self):
        sm = next(s for s in self.spec.state_machines if s.content_ref == "tickets")
        assert sm.primitive_type == "content"

    def test_reflection_enabled(self):
        assert self.spec.reflection_enabled is True


# ============================================================
# Project board example
# ============================================================

class TestProjectBoardIR:
    def setup_method(self):
        self.spec = _load_and_lower("projectboard.termin")

    def test_five_tables(self):
        names = [t.name.snake for t in self.spec.content]
        assert "projects" in names
        assert "team_members" in names
        assert "sprints" in names
        assert "tasks" in names
        assert "time_logs" in names

    def test_deep_fk_chain(self):
        """tasks -> sprints -> projects (3-level FK chain)."""
        tasks = next(t for t in self.spec.content if t.name.snake == "tasks")
        cols = {c.name: c for c in tasks.fields}
        assert cols["project"].foreign_key == "projects"
        assert cols["sprint"].foreign_key == "sprints"
        assert cols["assignee"].foreign_key == "team_members"

    def test_task_lifecycle(self):
        sm = next(s for s in self.spec.state_machines if s.content_ref == "tasks")
        assert sm.initial_state == "backlog"
        assert "in sprint" in sm.states
        assert "in review" in sm.states
        assert "done" in sm.states

    def test_plan_transition(self):
        # v0.9: Transition routes are namespaced by machine_name (snake_case
        # field name) so multiple state machines per content can coexist:
        # /_transition/{machine_name}/{target_state}
        plan_route = next(r for r in self.spec.routes if r.target_state == "in sprint")
        assert plan_route.kind == RouteKind.TRANSITION
        assert plan_route.path == "/api/v1/tasks/{id}/_transition/task_lifecycle/in sprint"

    def test_create_task_form_fields(self):
        ct = next(p for p in self.spec.pages if p.slug == "create_task")
        form = _find_child(ct, "form")
        assert form.props["target"] == "tasks"
        field_inputs = _find_children(form, "field_input")
        project = next(f for f in field_inputs if f.props["field"] == "project")
        assert project.props["input_type"] == "reference"
        assert project.props["reference_content"] == "projects"
        priority = next(f for f in field_inputs if f.props["field"] == "priority")
        assert priority.props["input_type"] == "enum"

    def test_search_field_not_truncated(self):
        """'Allow searching by title' must produce ['title'], not ['itle'] or char list."""
        board = next(p for p in self.spec.pages if p.slug == "sprint_board")
        dt = _find_child(board, "data_table")
        search = _find_child(dt, "search")
        assert search is not None, "No search component on sprint board"
        fields = search.props["fields"]
        assert fields == ["title"], f"Expected ['title'], got {fields}"

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
        assert page.role == "Anonymous"
        text_nodes = _find_children(page, "text")
        assert any("Hello, World" == t.props.get("content") for t in text_nodes)


# ============================================================
# Compute demo example
# ============================================================

class TestComputeDemoIR:
    def setup_method(self):
        self.spec = _load_and_lower("compute_demo.termin")

    def test_app_name(self):
        assert self.spec.name == "Order Processing Demo"

    def test_tables(self):
        names = [t.name.snake for t in self.spec.content]
        assert "orders" in names
        assert "order_lines" in names
        assert "reports" in names

    # Compute

    def test_six_computes(self):
        assert len(self.spec.computes) == 6

    def test_compute_transform(self):
        c = next(c for c in self.spec.computes if c.name.snake == "calculate_order_total")
        assert c.shape == ComputeShape.TRANSFORM
        assert "orders" in c.input_content
        assert "orders" in c.output_content
        assert c.required_scope == "orders.write"

    def test_compute_reduce(self):
        c = next(c for c in self.spec.computes if c.name.snake == "revenue_report")
        assert c.shape == ComputeShape.REDUCE
        assert "orders" in c.input_content

    def test_compute_expand(self):
        c = next(c for c in self.spec.computes if c.name.snake == "split_order_into_lines")
        assert c.shape == ComputeShape.EXPAND

    def test_compute_correlate(self):
        c = next(c for c in self.spec.computes if c.name.snake == "match_orders_to_lines")
        assert c.shape == ComputeShape.CORRELATE
        assert "orders" in c.input_content
        assert "order_lines" in c.input_content

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
        assert ch.carries_content == "orders"
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
        assert "orders" in b.contains_content
        assert "order_lines" in b.contains_content
        assert b.identity_mode == "inherit"

    def test_hello_user_no_user_tables(self):
        spec = _load_and_lower("hello_user.termin")
        # D-20: Computes get auto-generated audit log Content; no user-declared Content
        user_content = [c for c in spec.content if not c.name.snake.startswith("compute_audit_log_")]
        assert user_content == []

    def test_hello_user_compute_typed_params(self):
        spec = _load_and_lower("hello_user.termin")
        assert len(spec.computes) == 1
        c = spec.computes[0]
        assert c.name.display == "SayHelloTo"
        assert c.shape == ComputeShape.TRANSFORM
        assert len(c.input_params) == 1
        assert c.input_params[0].name == "name"
        assert c.input_params[0].type_name == "text"
        # v0.9: scope-based compute access (was required_role == "user"
        # in v0.8; bare-role form removed).
        assert c.required_scope == "app.view"
        assert c.required_role is None

    def test_hello_user_merged_pages(self):
        spec = _load_and_lower("hello_user.termin")
        assert len(spec.pages) == 2
        anon_page = next(p for p in spec.pages if p.role == "Anonymous")
        anon_texts = _find_children(anon_page, "text")
        assert any("Anon, Hello!" == t.props.get("content") for t in anon_texts)
        logged_page = next(p for p in spec.pages if p.role == "user")
        logged_texts = _find_children(logged_page, "text")
        assert any(
            isinstance(t.props.get("content"), PropValue)
            and t.props["content"].is_expr
            and "SayHelloTo" in t.props["content"].value
            for t in logged_texts
        )

    def test_order_reporting_boundary(self):
        b = next(b for b in self.spec.boundaries if b.name.snake == "order_reporting")
        assert "reports" in b.contains_content
        assert b.identity_mode == "restrict"
        assert "orders.read" in b.identity_scopes

    def test_business_types(self):
        orders = next(t for t in self.spec.content if t.name.snake == "orders")
        cols = {c.name: c for c in orders.fields}
        assert cols["total"].business_type == "currency"

    def test_reflection_enabled(self):
        assert self.spec.reflection_enabled is True

    def test_error_handler_source_type(self):
        if self.spec.error_handlers:
            for eh in self.spec.error_handlers:
                if eh.source:
                    assert eh.source_type in ("content", "channel", "compute", "boundary")


# ============================================================
# Channel Demo: all Channel patterns
# ============================================================

class TestChannelDemoIR:
    @classmethod
    def setup_class(cls):
        cls.spec = _load_and_lower("channel_demo.termin")

    def test_six_channels(self):
        assert len(self.spec.channels) == 6

    def test_inbound_reliable_data_channel(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "github_webhooks")
        assert ch.direction == ChannelDirection.INBOUND
        assert ch.delivery == ChannelDelivery.RELIABLE
        assert ch.carries_content == "deployments"
        assert len(ch.actions) == 0

    def test_inbound_realtime_data_channel(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "monitoring_feed")
        assert ch.direction == ChannelDirection.INBOUND
        assert ch.delivery == ChannelDelivery.REALTIME
        assert ch.carries_content == "alerts"

    def test_outbound_reliable_data_channel(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "pagerduty")
        assert ch.direction == ChannelDirection.OUTBOUND
        assert ch.delivery == ChannelDelivery.RELIABLE
        assert ch.carries_content == "incidents"

    def test_action_only_channel(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "cloud_provider")
        assert ch.carries_content == ""
        assert ch.direction == ChannelDirection.OUTBOUND
        assert len(ch.actions) == 3

    def test_action_channel_verb_types(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "cloud_provider")
        restart = next(a for a in ch.actions if a.name.snake == "restart_service")
        assert restart.takes[0].name == "service"
        assert restart.takes[0].param_type == "text"
        assert restart.takes[1].name == "region"
        scale = next(a for a in ch.actions if a.name.snake == "scale_service")
        assert scale.takes[1].name == "replicas"
        assert scale.takes[1].param_type == "number"
        rollback = next(a for a in ch.actions if a.name.snake == "rollback_deployment")
        assert rollback.returns[0].name == "deployment id"

    def test_hybrid_channel(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "slack")
        assert ch.carries_content == "incidents"
        assert ch.direction == ChannelDirection.BIDIRECTIONAL
        assert ch.delivery == ChannelDelivery.REALTIME
        assert len(ch.actions) == 2
        assert len(ch.requirements) == 1

    def test_internal_channel_no_auth(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "incident_bus")
        assert ch.direction == ChannelDirection.INTERNAL
        assert ch.delivery == ChannelDelivery.AUTO
        assert len(ch.requirements) == 0

    def test_three_event_channel_sends(self):
        sends = [e for e in self.spec.events if e.action and e.action.send_channel]
        assert len(sends) == 3
        channels = {e.action.send_channel for e in sends}
        assert channels == {"pagerduty", "slack", "incident-bus"}

    def test_event_send_log_levels(self):
        sends = [e for e in self.spec.events if e.action and e.action.send_channel]
        pager = next(e for e in sends if e.action.send_channel == "pagerduty")
        assert pager.log_level == "ERROR"
        slack = next(e for e in sends if e.action.send_channel == "slack")
        assert slack.log_level == "INFO"
        bus = next(e for e in sends if e.action.send_channel == "incident-bus")
        assert bus.log_level == "WARN"

    def test_agent_compute_with_trigger(self):
        comp = next(c for c in self.spec.computes if c.name.snake == "auto_mitigate")
        assert comp.provider == "ai-agent"
        assert comp.trigger == 'event "incident.created"'
        assert len(comp.preconditions) == 1
        assert len(comp.postconditions) == 1


# ============================================================
# Security Agent: Channel Actions + Event Send
# ============================================================

class TestSecurityAgentIR:
    @classmethod
    def setup_class(cls):
        cls.spec = _load_and_lower("security_agent.termin")

    def test_two_channels(self):
        assert len(self.spec.channels) == 2

    def test_security_tools_action_channel(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "security_tools")
        assert ch.carries_content == ""  # action-only, no data carry
        assert ch.direction == ChannelDirection.OUTBOUND
        assert ch.delivery == ChannelDelivery.RELIABLE
        assert len(ch.actions) == 3

    def test_restrict_policy_action(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "security_tools")
        act = next(a for a in ch.actions if a.name.snake == "restrict_policy")
        assert len(act.takes) == 2
        assert act.takes[0].name == "role"
        assert act.takes[0].param_type == "text"
        assert act.takes[1].name == "policy"
        assert act.takes[1].param_type == "text"
        assert len(act.returns) == 1
        assert act.returns[0].name == "result"
        assert ("findings.remediate",) == act.required_scopes

    def test_rotate_secret_action(self):
        ch = next(c for c in self.spec.channels if c.name.snake == "security_tools")
        act = next(a for a in ch.actions if a.name.snake == "rotate_secret")
        assert len(act.takes) == 1
        assert act.takes[0].name == "arn"
        assert ("findings.remediate",) == act.required_scopes

    def test_slack_hybrid_channel(self):
        """Slack carries data AND exposes actions."""
        ch = next(c for c in self.spec.channels if c.name.snake == "slack")
        assert ch.carries_content == "findings"
        assert ch.direction == ChannelDirection.BIDIRECTIONAL
        assert ch.delivery == ChannelDelivery.REALTIME
        assert len(ch.actions) == 1
        assert ch.actions[0].name.snake == "post_message"

    def test_event_send_to_channel(self):
        """Events with 'Send X to channel' produce EventActionSpec with send_channel."""
        send_events = [e for e in self.spec.events if e.action and e.action.send_channel]
        assert len(send_events) == 2
        for ev in send_events:
            assert ev.action.send_channel == "slack"
            assert ev.action.send_content == "finding"

    def test_agent_computes(self):
        scanner = next(c for c in self.spec.computes if c.name.snake == "scanner")
        assert scanner.provider == "ai-agent"
        assert scanner.trigger == "schedule every 1 hour"
        assert len(scanner.preconditions) == 1
        assert len(scanner.postconditions) == 1

    def test_remediator_compute(self):
        rem = next(c for c in self.spec.computes if c.name.snake == "remediator")
        assert rem.provider == "ai-agent"
        assert rem.trigger == 'event "finding.created"'


# ============================================================
# Presentation v2: Component tree tests
# ============================================================

class TestComponentTree:
    """Tests for the component tree IR structure."""

    def test_page_entry_has_children(self):
        """Pages should have children array instead of flat fields."""
        spec = _load_and_lower("warehouse.termin")
        dash = next(p for p in spec.pages if p.slug == "inventory_dashboard")
        assert isinstance(dash, PageEntry)
        assert len(dash.children) > 0

    def test_data_table_component(self):
        """DisplayTable should produce a data_table component."""
        spec = _load_and_lower("warehouse.termin")
        dash = next(p for p in spec.pages if p.slug == "inventory_dashboard")
        dt = _find_child(dash, "data_table")
        assert dt is not None
        assert dt.props["source"] == "products"
        assert isinstance(dt.props["columns"], list)

    def test_form_component(self):
        """AcceptInput should produce a form with field_input children."""
        spec = _load_and_lower("warehouse.termin")
        add = next(p for p in spec.pages if p.slug == "add_product")
        form = _find_child(add, "form")
        assert form is not None
        assert form.props["target"] == "products"
        inputs = _find_children(form, "field_input")
        assert len(inputs) >= 3

    def test_text_literal_component(self):
        """Display text 'literal' -> text component with string content."""
        spec = _load_and_lower("hello.termin")
        page = spec.pages[0]
        text = _find_child(page, "text")
        assert text is not None
        assert text.props["content"] == "Hello, World"

    def test_text_expression_component(self):
        """Display text [expr] -> text component with PropValue content."""
        spec = _load_and_lower("hello_user.termin")
        logged = next(p for p in spec.pages if p.role == "user")
        texts = _find_children(logged, "text")
        expr_text = next((t for t in texts if isinstance(t.props.get("content"), PropValue)
                          and t.props["content"].is_expr), None)
        assert expr_text is not None
        assert "SayHelloTo" in expr_text.props["content"].value

    def test_filter_children_of_data_table(self):
        """Filters should be children of data_table, not siblings."""
        spec = _load_and_lower("warehouse.termin")
        dash = next(p for p in spec.pages if p.slug == "inventory_dashboard")
        dt = _find_child(dash, "data_table")
        filters = _find_children(dt, "filter")
        assert len(filters) >= 2

    def test_chart_component_migrated_to_metric(self):
        """v0.9 Phase 5 Q5 resolution (2026-04-27): warehouse.termin's
        single `Show a chart of` use migrated to `Display count of X
        grouped by <field>` (field-grouped metric form). Per BRD #2
        §5.1, the `chart` contract is intentionally not in v0.9 — chart
        rendering varies wildly across design systems. Field-grouped
        metric covers the count-over-time case adequately for the
        warehouse executive dashboard.
        """
        spec = _load_and_lower("warehouse.termin")
        overview = next(p for p in spec.pages if p.slug == "inventory_overview")
        # No chart component on the page anymore.
        assert _find_child(overview, "chart") is None
        # The replacement is a stat_breakdown / aggregation grouped by
        # warehouse (data shape: one breakdown entry per distinct
        # warehouse value).
        aggs = _find_children(overview, "aggregation") + _find_children(overview, "stat_breakdown")
        # Three things on the page: product breakdown, stock value,
        # reorder alerts grouped by warehouse. All metric-shaped.
        assert len(aggs) >= 2

    def test_aggregation_component(self):
        """DisplayAggregation should produce aggregation/stat_breakdown components."""
        spec = _load_and_lower("warehouse.termin")
        overview = next(p for p in spec.pages if p.slug == "inventory_overview")
        # Should have at least one aggregation or stat_breakdown
        aggs = _find_children(overview, "aggregation") + _find_children(overview, "stat_breakdown")
        assert len(aggs) >= 1


class TestNoStringIterationBugs:
    """Catch the class of bug where a string is iterated as characters.

    When a parser returns 'title' (string) instead of ['title'] (list),
    list comprehensions like [_snake(f) for f in fields] produce
    ['t', 'i', 't', 'l', 'e'] instead of ['title']. These tests catch
    single-character values in list props across ALL examples.
    """

    @pytest.mark.parametrize("name", [
        "hello", "hello_user", "warehouse", "helpdesk", "projectboard", "compute_demo"
    ])
    def test_no_single_char_search_fields(self, name):
        spec = _load_and_lower(f"{name}.termin")
        for page in spec.pages:
            for child in page.children:
                if child.type == "data_table":
                    for subchild in child.children:
                        if subchild.type == "search":
                            for field in subchild.props.get("fields", []):
                                assert len(field) > 1, (
                                    f"{name}/{page.name}: search field '{field}' is a single char "
                                    f"(string iteration bug)"
                                )

    @pytest.mark.parametrize("name", [
        "hello", "hello_user", "warehouse", "helpdesk", "projectboard", "compute_demo"
    ])
    def test_no_single_char_filter_fields(self, name):
        spec = _load_and_lower(f"{name}.termin")
        for page in spec.pages:
            for child in page.children:
                if child.type == "data_table":
                    for subchild in child.children:
                        if subchild.type == "filter":
                            field = subchild.props.get("field", "")
                            assert len(field) > 1, (
                                f"{name}/{page.name}: filter field '{field}' is a single char"
                            )

    @pytest.mark.parametrize("name", [
        "hello", "hello_user", "warehouse", "helpdesk", "projectboard", "compute_demo"
    ])
    def test_no_single_char_column_fields(self, name):
        spec = _load_and_lower(f"{name}.termin")
        for page in spec.pages:
            for child in page.children:
                if child.type == "data_table":
                    for col in child.props.get("columns", []):
                        assert len(col.get("field", "xx")) > 1, (
                            f"{name}/{page.name}: column field '{col}' is a single char"
                        )


class TestStructuredAggregationParsing:
    """Tests for the new structured aggregation DSL syntax."""

    def _parse_and_lower(self, source):
        from termin.peg_parser import parse_peg
        prog, errs = parse_peg(source)
        assert errs.ok, errs.format()
        from termin.analyzer import analyze
        aerrs = analyze(prog)
        # Analyzer may warn about missing content for inline examples
        spec = lower(prog)
        return spec

    def test_count_grouped_by(self):
        source = '''Application: Test
Content called "tasks":
  Each task has a title which is text
  Each task has a status which is text

As anonymous, I want to see a page "Board" so that I can see tasks:
  Display count of tasks grouped by status
'''
        spec = self._parse_and_lower(source)
        page = spec.pages[0]
        sb = _find_child(page, "stat_breakdown")
        assert sb is not None
        assert sb.props["source"] == "tasks"
        assert sb.props["group_by"] == "status"

    def test_count_simple(self):
        source = '''Application: Test
Content called "tasks":
  Each task has a title which is text

As anonymous, I want to see a page "Board" so that I can see tasks:
  Display count of tasks
'''
        spec = self._parse_and_lower(source)
        page = spec.pages[0]
        agg = _find_child(page, "aggregation")
        assert agg is not None
        assert agg.props["agg_type"] == "count"
        assert agg.props["source"] == "tasks"


# ── D-18: Audit on ContentSchema ──

class TestContentSchemaAudit:
    _AUTH_PREAMBLE = '''Identity:
  Scopes are "read"
  A "user" has "read"
'''

    def _parse_and_lower(self, source):
        program, errors = parse(self._AUTH_PREAMBLE + source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()
        return lower(program)

    def test_audit_actions_in_ir(self):
        source = '''Content called "events":
  Each event has a title which is text
  Anyone with "read" can view events
  Audit level: actions
'''
        spec = self._parse_and_lower(source)
        assert spec.content[0].audit == "actions"

    def test_audit_none_in_ir(self):
        source = '''Content called "events":
  Each event has a title which is text
  Anyone with "read" can view events
  Audit level: none
'''
        spec = self._parse_and_lower(source)
        assert spec.content[0].audit == "none"

    def test_audit_default_actions_in_ir(self):
        source = '''Content called "events":
  Each event has a title which is text
  Anyone with "read" can view events
'''
        spec = self._parse_and_lower(source)
        assert spec.content[0].audit == "actions"


# ============================================================
# v0.9 multi-state-machine IR lowering
# ============================================================


_SM_BASE_IR = '''Identity:
  Scopes are "manage" and "approve"
  A "editor" has "manage" and "approve"
'''

_SINGLE_SM_SRC = _SM_BASE_IR + '''
Content called "products":
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active
    draft can become active if the user has manage
  Anyone with "manage" can view products
  Anyone with "manage" can update products

As an editor, I want to manage products so that inventory flows:
  Show a page called "Products":
    Display a table of products with columns: lifecycle
    For each product, show actions:
      "Activate" transitions lifecycle to active if available
'''

_MULTI_SM_SRC = _SM_BASE_IR + '''
Content called "products":
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active
    draft can become active if the user has manage
  Each product has an approval status which is state:
    approval status starts as pending
    approval status can also be approved
    pending can become approved if the user has approve
  Anyone with "manage" can view products
  Anyone with "manage" can update products

As an editor, I want to manage products so that inventory flows:
  Show a page called "Products":
    Display a table of products with columns: lifecycle, approval status
    For each product, show actions:
      "Activate" transitions lifecycle to active if available
      "Approve" transitions approval status to approved if available
'''


def _lower_src(source: str):
    program, errors = parse(source)
    assert errors.ok, errors.format()
    result = analyze(program)
    assert result.ok, result.format()
    return lower(program)


class TestStateMachineIRLowering:
    """v0.9: ContentSchema.state_machines list + ComponentNode machine_name prop."""

    # --- Single-SM content --------------------------------------------

    def test_content_schema_has_state_machines_list(self):
        spec = _lower_src(_SINGLE_SM_SRC)
        cs = next(c for c in spec.content if c.name.snake == "products")
        assert isinstance(cs.state_machines, tuple)
        assert len(cs.state_machines) == 1

    def test_state_machines_entry_shape(self):
        spec = _lower_src(_SINGLE_SM_SRC)
        cs = next(c for c in spec.content if c.name.snake == "products")
        entry = cs.state_machines[0]
        assert "machine_name" in entry
        assert "initial" in entry
        assert entry["machine_name"] == "lifecycle"
        assert entry["initial"] == "draft"

    def test_no_has_state_machine_field(self):
        spec = _lower_src(_SINGLE_SM_SRC)
        cs = next(c for c in spec.content if c.name.snake == "products")
        # v0.9 removes has_state_machine and initial_state from ContentSchema.
        assert not hasattr(cs, "has_state_machine")
        assert not hasattr(cs, "initial_state")

    def test_no_status_in_fields(self):
        spec = _lower_src(_SINGLE_SM_SRC)
        cs = next(c for c in spec.content if c.name.snake == "products")
        # v0.8 auto-injected a generic `status` FieldSpec; v0.9 uses the
        # field-specific column name instead.
        for f in cs.fields:
            assert f.name != "status"

    def test_machine_name_is_snake_case_of_field(self):
        # "approval status" field → "approval_status" machine_name.
        spec = _lower_src(_MULTI_SM_SRC)
        sm_approval = next(
            sm for sm in spec.state_machines
            if sm.machine_name == "approval_status"
        )
        assert sm_approval.content_ref == "products"
        assert sm_approval.initial_state == "pending"

    def test_state_machine_spec_machine_name(self):
        # StateMachineSpec.machine_name is the snake_case field name,
        # not a user-supplied display name.
        spec = _lower_src(_SINGLE_SM_SRC)
        sm = next(sm for sm in spec.state_machines if sm.content_ref == "products")
        assert sm.machine_name == "lifecycle"

    # --- Multi-SM content ---------------------------------------------

    def test_two_state_machines_in_list(self):
        spec = _lower_src(_MULTI_SM_SRC)
        cs = next(c for c in spec.content if c.name.snake == "products")
        assert len(cs.state_machines) == 2
        names = sorted(e["machine_name"] for e in cs.state_machines)
        assert names == ["approval_status", "lifecycle"]

    def test_both_machines_have_correct_initial(self):
        spec = _lower_src(_MULTI_SM_SRC)
        cs = next(c for c in spec.content if c.name.snake == "products")
        by_name = {e["machine_name"]: e["initial"] for e in cs.state_machines}
        assert by_name["lifecycle"] == "draft"
        assert by_name["approval_status"] == "pending"

    def test_no_overwriting(self):
        spec = _lower_src(_MULTI_SM_SRC)
        # The top-level state_machines list must also contain both entries —
        # the v0.8 bug was a dict keyed by content_name that dropped the second.
        sms_for_products = [
            sm for sm in spec.state_machines if sm.content_ref == "products"
        ]
        assert len(sms_for_products) == 2
        names = sorted(sm.machine_name for sm in sms_for_products)
        assert names == ["approval_status", "lifecycle"]

    # --- Action button component nodes --------------------------------

    @staticmethod
    def _collect_action_buttons(page):
        buttons = []
        def walk(node):
            for ch in getattr(node, "children", ()):
                if ch.type == "action_button":
                    buttons.append(ch)
                walk(ch)
            # Row-action buttons live in data_table.props["row_actions"],
            # not in children — the renderer pulls them out per row.
            props = getattr(node, "props", None) or {}
            row_actions = props.get("row_actions") if isinstance(props, dict) else None
            if row_actions:
                for ra in row_actions:
                    if getattr(ra, "type", None) == "action_button":
                        buttons.append(ra)
        walk(page)
        return buttons

    def test_action_button_has_machine_name_prop(self):
        spec = _lower_src(_SINGLE_SM_SRC)
        page = next(p for p in spec.pages if p.name == "Products")
        buttons = self._collect_action_buttons(page)
        assert len(buttons) >= 1
        for btn in buttons:
            assert "machine_name" in btn.props, btn.props

    def test_action_button_machine_name_is_snake_case(self):
        spec = _lower_src(_MULTI_SM_SRC)
        page = next(p for p in spec.pages if p.name == "Products")
        buttons = self._collect_action_buttons(page)
        # Find the button that targets "approved" — its machine_name must
        # be "approval_status" (snake_case of the "approval status" field).
        approved_btn = None
        for b in buttons:
            ts = b.props.get("target_state")
            ts_val = ts.value if hasattr(ts, "value") else ts
            if ts_val == "approved":
                approved_btn = b
                break
        assert approved_btn is not None
        mn = approved_btn.props["machine_name"]
        mn_val = mn.value if hasattr(mn, "value") else mn
        assert mn_val == "approval_status"

    def test_two_action_buttons_different_machines(self):
        spec = _lower_src(_MULTI_SM_SRC)
        page = next(p for p in spec.pages if p.name == "Products")
        buttons = self._collect_action_buttons(page)
        machine_names = set()
        for b in buttons:
            mn = b.props["machine_name"]
            machine_names.add(mn.value if hasattr(mn, "value") else mn)
        assert "lifecycle" in machine_names
        assert "approval_status" in machine_names
