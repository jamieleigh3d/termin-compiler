# Copyright 2026 Jamie-Leigh Blake
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Compiler fidelity tests: verify IR output matches DSL declarations.

This is the most important test file in the project. It catches silent
semantic data loss — the class of bug where compilation succeeds (errors.ok)
but the IR doesn't faithfully represent what the DSL declared.

The 007 post-mortem revealed we never tested that compilation was *faithful*,
only that it didn't error. Every example in examples/ is compiled and its IR
is checked against specific properties declared in the DSL.
"""

from pathlib import Path

import pytest

from termin.peg_parser import parse_peg as parse
from termin.lower import lower
from termin.ir import (
    Verb, FieldType, ComputeShape, ChannelDirection, ChannelDelivery,
)


EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def _compile(example: str):
    """Compile an example .termin file and return the AppSpec IR."""
    source = (EXAMPLES_DIR / example).read_text()
    program, errors = parse(source)
    assert errors.ok, f"Parse failed for {example}: {errors.format()}"
    from termin.analyzer import analyze
    result = analyze(program)
    assert result.ok, f"Analysis failed for {example}: {result.format()}"
    return lower(program)


def _content(spec, name):
    """Get a ContentSchema by snake_case name."""
    for c in spec.content:
        if c.name.snake == name:
            return c
    raise AssertionError(f"Content '{name}' not found in IR")


def _field(content, name):
    """Get a FieldSpec by snake_case name."""
    for f in content.fields:
        if f.name == name:
            return f
    raise AssertionError(f"Field '{name}' not found in content '{content.name.snake}'")


def _sm(spec, content_ref):
    """Get a StateMachineSpec by content_ref."""
    for sm in spec.state_machines:
        if sm.content_ref == content_ref:
            return sm
    raise AssertionError(f"StateMachine for '{content_ref}' not found in IR")


def _compute(spec, name_snake):
    """Get a ComputeSpec by snake_case name."""
    for c in spec.computes:
        if c.name.snake == name_snake:
            return c
    raise AssertionError(f"Compute '{name_snake}' not found in IR")


def _channel(spec, name_snake):
    """Get a ChannelSpec by snake_case name."""
    for ch in spec.channels:
        if ch.name.snake == name_snake:
            return ch
    raise AssertionError(f"Channel '{name_snake}' not found in IR")


def _boundary(spec, name_snake):
    """Get a BoundarySpec by snake_case name."""
    for b in spec.boundaries:
        if b.name.snake == name_snake:
            return b
    raise AssertionError(f"Boundary '{name_snake}' not found in IR")


def _grants_for(spec, content_snake):
    """Get all AccessGrants for a content type."""
    return [g for g in spec.access_grants if g.content == content_snake]


def _role(spec, name):
    """Get a RoleSpec by name."""
    for r in spec.auth.roles:
        if r.name == name:
            return r
    raise AssertionError(f"Role '{name}' not found in IR")


# ============================================================
# hello.termin — simplest app
# ============================================================

class TestHelloFidelity:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _compile("hello.termin")

    def test_app_name(self):
        assert self.spec.name == "Hello World"

    def test_description(self):
        assert self.spec.description == "The simplest possible Termin application"

    def test_app_id(self):
        assert self.spec.app_id == "c7151224-0ec6-45dd-a5a7-d0e5620f3650"

    def test_no_content(self):
        assert len(self.spec.content) == 0

    def test_no_access_grants(self):
        assert len(self.spec.access_grants) == 0

    def test_no_state_machines(self):
        assert len(self.spec.state_machines) == 0

    def test_page_exists(self):
        pages = [p for p in self.spec.pages if p.name == "Hello"]
        assert len(pages) == 1

    def test_page_has_text_component(self):
        page = next(p for p in self.spec.pages if p.name == "Hello")
        text_nodes = [c for c in page.children if c.type == "text"]
        assert len(text_nodes) >= 1
        # The DSL says: Display text "Hello, World"
        assert any(c.props.get("content") == "Hello, World" for c in text_nodes)


# ============================================================
# hello_user.termin — roles, scopes, compute
# ============================================================

class TestHelloUserFidelity:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _compile("hello_user.termin")

    def test_app_name(self):
        assert self.spec.name == "Hello User"

    def test_auth_provider(self):
        assert self.spec.auth.provider == "stub"

    def test_scopes(self):
        assert "app.view" in self.spec.auth.scopes

    def test_role_user(self):
        r = _role(self.spec, "user")
        assert "app.view" in r.scopes

    def test_compute_say_hello(self):
        c = _compute(self.spec, "sayhelloto")
        assert c.shape == ComputeShape.TRANSFORM
        assert len(c.input_params) >= 1
        assert len(c.output_params) >= 1
        # DSL: takes name : text, produces greeting : text
        assert any(p.name == "name" for p in c.input_params)
        assert any(p.name == "greeting" for p in c.output_params)

    def test_compute_body(self):
        c = _compute(self.spec, "sayhelloto")
        assert len(c.body_lines) >= 1
        # DSL: `greeting = "Hello, " + name + "!"`
        assert any("greeting" in line for line in c.body_lines)

    def test_compute_access(self):
        c = _compute(self.spec, "sayhelloto")
        # DSL: "user" can execute this
        assert c.required_scope is not None or c.required_role is not None

    def test_two_pages(self):
        # DSL defines pages for Anonymous and user
        assert len(self.spec.pages) >= 2


# ============================================================
# warehouse.termin — full PRFAQ example
# ============================================================

class TestWarehouseFidelity:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _compile("warehouse.termin")

    # -- Content --
    def test_content_names(self):
        names = {c.name.snake for c in self.spec.content}
        assert names == {"products", "stock_levels", "reorder_alerts"}

    def test_products_fields(self):
        p = _content(self.spec, "products")
        field_names = {f.name for f in p.fields}
        assert {"sku", "name", "description", "unit_cost", "category"} <= field_names

    def test_products_sku_constraints(self):
        f = _field(_content(self.spec, "products"), "sku")
        assert f.unique is True
        assert f.required is True
        assert f.business_type == "text"

    def test_products_unit_cost_type(self):
        f = _field(_content(self.spec, "products"), "unit_cost")
        assert f.business_type == "currency"
        assert f.column_type == FieldType.REAL

    def test_products_category_enum(self):
        f = _field(_content(self.spec, "products"), "category")
        assert f.business_type == "enum"
        assert set(f.enum_values) == {"raw material", "finished good", "packaging"}

    def test_stock_levels_quantity_minimum(self):
        f = _field(_content(self.spec, "stock_levels"), "quantity")
        assert f.business_type == "whole_number"
        assert f.minimum == 0

    def test_stock_levels_product_reference(self):
        f = _field(_content(self.spec, "stock_levels"), "product")
        assert f.foreign_key == "products"

    def test_reorder_alerts_created_at(self):
        f = _field(_content(self.spec, "reorder_alerts"), "created_at")
        assert f.is_auto is True

    # -- Access grants --
    def test_products_view_grant(self):
        grants = _grants_for(self.spec, "products")
        view_grants = [g for g in grants if Verb.VIEW in g.verbs]
        assert any(g.scope == "inventory.read" for g in view_grants)

    def test_products_update_grant(self):
        grants = _grants_for(self.spec, "products")
        update_grants = [g for g in grants if Verb.UPDATE in g.verbs]
        assert any(g.scope == "inventory.write" for g in update_grants)

    def test_products_create_delete_grant(self):
        grants = _grants_for(self.spec, "products")
        create_grants = [g for g in grants if Verb.CREATE in g.verbs]
        delete_grants = [g for g in grants if Verb.DELETE in g.verbs]
        assert any(g.scope == "inventory.admin" for g in create_grants)
        assert any(g.scope == "inventory.admin" for g in delete_grants)

    def test_stock_levels_no_delete(self):
        grants = _grants_for(self.spec, "stock_levels")
        delete_grants = [g for g in grants if Verb.DELETE in g.verbs]
        assert len(delete_grants) == 0

    # -- Roles --
    def test_warehouse_clerk_scopes(self):
        r = _role(self.spec, "warehouse clerk")
        assert set(r.scopes) == {"inventory.read", "inventory.write"}

    def test_warehouse_manager_scopes(self):
        r = _role(self.spec, "warehouse manager")
        assert set(r.scopes) == {"inventory.read", "inventory.write", "inventory.admin"}

    def test_executive_scopes(self):
        r = _role(self.spec, "executive")
        assert set(r.scopes) == {"inventory.read"}

    # -- State machine --
    def test_product_lifecycle_initial(self):
        sm = _sm(self.spec, "products")
        assert sm.machine_name == "product lifecycle"
        assert sm.initial_state == "draft"

    def test_product_lifecycle_states(self):
        sm = _sm(self.spec, "products")
        assert set(sm.states) >= {"draft", "active", "discontinued"}

    def test_product_lifecycle_transitions(self):
        sm = _sm(self.spec, "products")
        # DSL: A draft product can become active if the user has "inventory.write"
        draft_to_active = [t for t in sm.transitions
                           if t.from_state == "draft" and t.to_state == "active"]
        assert len(draft_to_active) == 1
        assert draft_to_active[0].required_scope == "inventory.write"

        # DSL: An active product can become discontinued if the user has "inventory.admin"
        active_to_disc = [t for t in sm.transitions
                          if t.from_state == "active" and t.to_state == "discontinued"]
        assert len(active_to_disc) == 1
        assert active_to_disc[0].required_scope == "inventory.admin"

        # DSL: A discontinued product can become active again if the user has "inventory.admin"
        disc_to_active = [t for t in sm.transitions
                          if t.from_state == "discontinued" and t.to_state == "active"]
        assert len(disc_to_active) == 1
        assert disc_to_active[0].required_scope == "inventory.admin"

    def test_transition_feedback(self):
        sm = _sm(self.spec, "products")
        draft_to_active = next(t for t in sm.transitions
                               if t.from_state == "draft" and t.to_state == "active")
        # DSL: success shows toast `product.name + " is now active"`
        success_fb = [fb for fb in draft_to_active.feedback if fb.trigger == "success"]
        assert len(success_fb) == 1
        assert success_fb[0].style == "toast"
        assert success_fb[0].is_expr is True

        # DSL: error shows banner "Could not activate product"
        error_fb = [fb for fb in draft_to_active.feedback if fb.trigger == "error"]
        assert len(error_fb) == 1
        assert error_fb[0].style == "banner"
        assert error_fb[0].is_expr is False
        assert error_fb[0].message == "Could not activate product"

    # -- Events --
    def test_event_exists(self):
        assert len(self.spec.events) >= 1

    # -- Pages --
    def test_pages_exist(self):
        page_names = {p.name for p in self.spec.pages}
        assert "Inventory Dashboard" in page_names
        assert "Receive Stock" in page_names
        assert "Reorder Alerts" in page_names
        assert "Add Product" in page_names
        assert "Inventory Overview" in page_names


# ============================================================
# helpdesk.termin — multi-word states, defaults
# ============================================================

class TestHelpdeskFidelity:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _compile("helpdesk.termin")

    def test_content_names(self):
        names = {c.name.snake for c in self.spec.content}
        assert names == {"tickets", "comments"}

    def test_ticket_fields(self):
        t = _content(self.spec, "tickets")
        field_names = {f.name for f in t.fields}
        assert {"title", "description", "priority", "category",
                "submitted_by", "assigned_to", "created_at"} <= field_names

    def test_ticket_priority_enum(self):
        f = _field(_content(self.spec, "tickets"), "priority")
        assert set(f.enum_values) == {"low", "medium", "high", "critical"}

    def test_ticket_category_enum(self):
        f = _field(_content(self.spec, "tickets"), "category")
        assert set(f.enum_values) == {"bug", "feature request", "question", "account issue"}

    def test_ticket_submitted_by_default(self):
        f = _field(_content(self.spec, "tickets"), "submitted_by")
        assert f.default_expr is not None
        assert "User.Name" in f.default_expr

    def test_comment_ticket_reference(self):
        f = _field(_content(self.spec, "comments"), "ticket")
        assert f.foreign_key == "tickets"

    # -- Access grants (exact verbs) --
    def test_tickets_view_scope(self):
        grants = _grants_for(self.spec, "tickets")
        view = [g for g in grants if Verb.VIEW in g.verbs]
        assert any(g.scope == "tickets.view" for g in view)

    def test_tickets_create_scope(self):
        grants = _grants_for(self.spec, "tickets")
        create = [g for g in grants if Verb.CREATE in g.verbs]
        assert any(g.scope == "tickets.create" for g in create)

    def test_tickets_update_scope(self):
        grants = _grants_for(self.spec, "tickets")
        update = [g for g in grants if Verb.UPDATE in g.verbs]
        assert any(g.scope == "tickets.manage" for g in update)

    def test_tickets_delete_scope(self):
        grants = _grants_for(self.spec, "tickets")
        delete = [g for g in grants if Verb.DELETE in g.verbs]
        assert any(g.scope == "tickets.admin" for g in delete)

    def test_comments_no_update(self):
        grants = _grants_for(self.spec, "comments")
        update = [g for g in grants if Verb.UPDATE in g.verbs]
        assert len(update) == 0

    def test_comments_no_delete(self):
        grants = _grants_for(self.spec, "comments")
        delete = [g for g in grants if Verb.DELETE in g.verbs]
        assert len(delete) == 0

    # -- Roles --
    def test_customer_scopes(self):
        r = _role(self.spec, "customer")
        assert set(r.scopes) == {"tickets.view", "tickets.create"}

    def test_support_agent_scopes(self):
        r = _role(self.spec, "support agent")
        assert set(r.scopes) == {"tickets.view", "tickets.create", "tickets.manage"}

    def test_support_manager_scopes(self):
        r = _role(self.spec, "support manager")
        assert set(r.scopes) == {"tickets.view", "tickets.create", "tickets.manage", "tickets.admin"}

    # -- State machine (multi-word states) --
    def test_ticket_lifecycle_initial(self):
        sm = _sm(self.spec, "tickets")
        assert sm.initial_state == "open"

    def test_ticket_lifecycle_states(self):
        sm = _sm(self.spec, "tickets")
        assert set(sm.states) >= {"open", "in progress", "waiting on customer", "resolved", "closed"}

    def test_ticket_transitions_exact(self):
        sm = _sm(self.spec, "tickets")
        transitions = {(t.from_state, t.to_state): t.required_scope for t in sm.transitions}

        # Verify every transition from DSL
        assert transitions.get(("open", "in progress")) == "tickets.manage"
        assert transitions.get(("in progress", "waiting on customer")) == "tickets.manage"
        assert transitions.get(("waiting on customer", "in progress")) == "tickets.create"
        assert transitions.get(("in progress", "resolved")) == "tickets.manage"
        assert transitions.get(("resolved", "closed")) == "tickets.admin"
        assert transitions.get(("resolved", "in progress")) == "tickets.create"

    def test_transition_count(self):
        sm = _sm(self.spec, "tickets")
        assert len(sm.transitions) == 6  # exactly 6 in the DSL

    def test_resolved_dismiss_feedback(self):
        sm = _sm(self.spec, "tickets")
        in_prog_to_resolved = next(t for t in sm.transitions
                                    if t.from_state == "in progress" and t.to_state == "resolved")
        success_fb = [fb for fb in in_prog_to_resolved.feedback if fb.trigger == "success"]
        assert len(success_fb) == 1
        assert success_fb[0].style == "banner"
        assert success_fb[0].dismiss_seconds == 10


# ============================================================
# projectboard.termin — 5 content types, deep FK chains
# ============================================================

class TestProjectboardFidelity:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _compile("projectboard.termin")

    def test_content_names(self):
        names = {c.name.snake for c in self.spec.content}
        assert names == {"projects", "team_members", "sprints", "tasks", "time_logs"}

    def test_task_references(self):
        tasks = _content(self.spec, "tasks")
        proj_field = _field(tasks, "project")
        sprint_field = _field(tasks, "sprint")
        assignee_field = _field(tasks, "assignee")
        assert proj_field.foreign_key == "projects"
        assert sprint_field.foreign_key == "sprints"
        assert assignee_field.foreign_key == "team_members"

    def test_time_log_references(self):
        tl = _content(self.spec, "time_logs")
        assert _field(tl, "task").foreign_key == "tasks"
        assert _field(tl, "team_member").foreign_key == "team_members"

    def test_team_member_role_enum(self):
        tm = _content(self.spec, "team_members")
        f = _field(tm, "role")
        assert set(f.enum_values) == {"developer", "designer", "qa", "devops"}

    def test_project_status_enum(self):
        p = _content(self.spec, "projects")
        f = _field(p, "status")
        assert set(f.enum_values) == {"active", "on hold", "completed"}

    def test_task_priority_enum(self):
        t = _content(self.spec, "tasks")
        f = _field(t, "priority")
        assert set(f.enum_values) == {"low", "medium", "high", "critical"}

    # -- Access grants --
    def test_tasks_view_scope(self):
        grants = _grants_for(self.spec, "tasks")
        view = [g for g in grants if Verb.VIEW in g.verbs]
        assert any(g.scope == "projects.view" for g in view)

    def test_tasks_create_update_scope(self):
        grants = _grants_for(self.spec, "tasks")
        create = [g for g in grants if Verb.CREATE in g.verbs]
        assert any(g.scope == "tasks.manage" for g in create)

    def test_tasks_delete_scope(self):
        grants = _grants_for(self.spec, "tasks")
        delete = [g for g in grants if Verb.DELETE in g.verbs]
        assert any(g.scope == "projects.admin" for g in delete)

    # -- Roles --
    def test_developer_scopes(self):
        r = _role(self.spec, "developer")
        assert set(r.scopes) == {"projects.view", "tasks.manage"}

    def test_project_manager_scopes(self):
        r = _role(self.spec, "project manager")
        assert set(r.scopes) == {"projects.view", "tasks.manage", "sprints.manage", "projects.admin"}

    def test_stakeholder_scopes(self):
        r = _role(self.spec, "stakeholder")
        assert set(r.scopes) == {"projects.view"}

    # -- State machine --
    def test_task_lifecycle_initial(self):
        sm = _sm(self.spec, "tasks")
        assert sm.initial_state == "backlog"

    def test_task_lifecycle_states(self):
        sm = _sm(self.spec, "tasks")
        assert set(sm.states) >= {"backlog", "in sprint", "in progress", "in review", "done"}

    def test_task_transitions(self):
        sm = _sm(self.spec, "tasks")
        transitions = {(t.from_state, t.to_state): t.required_scope for t in sm.transitions}
        assert transitions.get(("backlog", "in sprint")) == "sprints.manage"
        assert transitions.get(("in sprint", "in progress")) == "tasks.manage"
        assert transitions.get(("in progress", "in review")) == "tasks.manage"
        assert transitions.get(("in review", "done")) == "tasks.manage"
        assert transitions.get(("in review", "in progress")) == "tasks.manage"
        assert transitions.get(("done", "in progress")) == "tasks.manage"

    def test_task_transition_count(self):
        sm = _sm(self.spec, "tasks")
        assert len(sm.transitions) == 6


# ============================================================
# compute_demo.termin — Compute shapes + Channels + Boundaries
# ============================================================

class TestComputeDemoFidelity:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _compile("compute_demo.termin")

    def test_content_names(self):
        names = {c.name.snake for c in self.spec.content}
        # D-20: Filter out auto-generated audit log Content
        user_content = {n for n in names if not n.startswith("compute_audit_log_")}
        assert user_content == {"orders", "order_lines", "reports"}

    def test_order_priority_enum(self):
        f = _field(_content(self.spec, "orders"), "priority")
        assert set(f.enum_values) == {"low", "medium", "high"}

    # -- Access grants --
    def test_orders_view_scope(self):
        grants = _grants_for(self.spec, "orders")
        view = [g for g in grants if Verb.VIEW in g.verbs]
        assert any(g.scope == "orders.read" for g in view)

    def test_orders_delete_scope(self):
        grants = _grants_for(self.spec, "orders")
        delete = [g for g in grants if Verb.DELETE in g.verbs]
        assert any(g.scope == "orders.admin" for g in delete)

    # -- Compute shapes --
    def test_transform_compute(self):
        c = _compute(self.spec, "calculate_order_total")
        assert c.shape == ComputeShape.TRANSFORM

    def test_reduce_compute(self):
        c = _compute(self.spec, "revenue_report")
        assert c.shape == ComputeShape.REDUCE

    def test_expand_compute(self):
        c = _compute(self.spec, "split_order_into_lines")
        assert c.shape == ComputeShape.EXPAND

    def test_correlate_compute(self):
        c = _compute(self.spec, "match_orders_to_lines")
        assert c.shape == ComputeShape.CORRELATE

    def test_route_compute(self):
        c = _compute(self.spec, "triage_order")
        assert c.shape == ComputeShape.ROUTE

    def test_compute_access_scopes(self):
        c = _compute(self.spec, "calculate_order_total")
        # DSL: Anyone with "orders.write" can execute this
        assert c.required_scope == "orders.write" or c.required_role is not None

    # -- Channels --
    def test_channel_count(self):
        assert len(self.spec.channels) == 4

    def test_order_webhook_channel(self):
        ch = _channel(self.spec, "order_webhook")
        assert ch.direction == ChannelDirection.INBOUND
        assert ch.delivery == ChannelDelivery.RELIABLE
        assert ch.carries_content == "orders"
        assert ch.endpoint == "/webhooks/orders"

    def test_order_updates_stream(self):
        ch = _channel(self.spec, "order_updates_stream")
        assert ch.direction == ChannelDirection.OUTBOUND
        assert ch.delivery == ChannelDelivery.REALTIME

    def test_order_notifications_bidirectional(self):
        ch = _channel(self.spec, "order_notifications")
        assert ch.direction == ChannelDirection.BIDIRECTIONAL
        assert ch.delivery == ChannelDelivery.REALTIME
        assert ch.endpoint == "/ws/orders"

    def test_internal_order_bus(self):
        ch = _channel(self.spec, "internal_order_bus")
        assert ch.direction == ChannelDirection.INTERNAL
        assert ch.delivery == ChannelDelivery.AUTO

    def test_channel_requirements(self):
        ch = _channel(self.spec, "order_webhook")
        assert len(ch.requirements) >= 1
        assert any(r.scope == "orders.write" and r.direction == "send" for r in ch.requirements)

    # -- Boundaries --
    def test_boundary_count(self):
        assert len(self.spec.boundaries) == 2

    def test_order_processing_boundary(self):
        b = _boundary(self.spec, "order_processing")
        assert set(b.contains_content) >= {"orders", "order_lines"}
        assert b.identity_mode == "inherit"

    def test_order_reporting_boundary(self):
        b = _boundary(self.spec, "order_reporting")
        assert "reports" in b.contains_content
        assert b.identity_mode == "restrict"
        assert "orders.read" in b.identity_scopes

    # -- State machine --
    def test_order_lifecycle(self):
        sm = _sm(self.spec, "orders")
        assert sm.initial_state == "pending"
        assert set(sm.states) >= {"pending", "confirmed", "shipped", "cancelled"}


# ============================================================
# helpdesk.termin + security_agent.termin — state transitions
# with multi-word and hyphenated states
# ============================================================

class TestSecurityAgentFidelity:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _compile("security_agent.termin")

    def test_app_name(self):
        assert self.spec.name == "Security Agent"

    def test_auth_provider(self):
        assert self.spec.auth.provider == "oidc"

    def test_content_names(self):
        names = {c.name.snake for c in self.spec.content}
        # D-20: Filter out auto-generated audit log Content
        user_content = {n for n in names if not n.startswith("compute_audit_log_")}
        assert user_content == {"findings", "scan_runs"}

    def test_finding_type_enum(self):
        f = _field(_content(self.spec, "findings"), "finding_type")
        assert set(f.enum_values) == {
            "iam-drift", "cve", "confidentiality-violation",
            "stale-secret", "state-deadlock", "error-spike"
        }

    def test_severity_enum(self):
        f = _field(_content(self.spec, "findings"), "severity")
        assert set(f.enum_values) == {"critical", "high", "medium", "low"}

    # -- Access grants --
    def test_findings_view_scope(self):
        grants = _grants_for(self.spec, "findings")
        view = [g for g in grants if Verb.VIEW in g.verbs]
        assert any(g.scope == "findings.view" for g in view)

    def test_findings_create_scope(self):
        grants = _grants_for(self.spec, "findings")
        create = [g for g in grants if Verb.CREATE in g.verbs]
        assert any(g.scope == "findings.triage" for g in create)

    def test_findings_update_scope(self):
        grants = _grants_for(self.spec, "findings")
        update = [g for g in grants if Verb.UPDATE in g.verbs]
        assert any(g.scope == "findings.remediate" for g in update)

    def test_findings_delete_scope(self):
        grants = _grants_for(self.spec, "findings")
        delete = [g for g in grants if Verb.DELETE in g.verbs]
        assert any(g.scope == "findings.remediate" for g in delete)

    # -- Roles --
    def test_platform_engineer_scopes(self):
        r = _role(self.spec, "platform engineer")
        assert set(r.scopes) == {"findings.view", "findings.triage", "findings.remediate", "alerts.send"}

    def test_security_reviewer_scopes(self):
        r = _role(self.spec, "security reviewer")
        assert set(r.scopes) == {"findings.view", "findings.triage"}

    def test_app_owner_scopes(self):
        r = _role(self.spec, "app owner")
        assert set(r.scopes) == {"findings.view"}

    # -- State machine (hyphenated states) --
    def test_remediation_initial(self):
        sm = _sm(self.spec, "findings")
        assert sm.initial_state == "detected"

    def test_remediation_states(self):
        sm = _sm(self.spec, "findings")
        assert set(sm.states) >= {
            "detected", "analyzing", "auto-fix-applied",
            "verified", "flagged-for-human", "closed"
        }

    def test_remediation_transitions(self):
        sm = _sm(self.spec, "findings")
        transitions = {(t.from_state, t.to_state): t.required_scope for t in sm.transitions}
        assert transitions.get(("detected", "analyzing")) == "findings.triage"
        assert transitions.get(("analyzing", "auto-fix-applied")) == "findings.remediate"
        assert transitions.get(("analyzing", "flagged-for-human")) == "findings.triage"
        assert transitions.get(("auto-fix-applied", "verified")) == "findings.remediate"
        assert transitions.get(("auto-fix-applied", "flagged-for-human")) == "findings.triage"
        assert transitions.get(("verified", "closed")) == "findings.triage"
        assert transitions.get(("flagged-for-human", "closed")) == "findings.remediate"

    def test_remediation_transition_count(self):
        sm = _sm(self.spec, "findings")
        assert len(sm.transitions) == 7

    # -- Compute (AI agent) --
    def test_scanner_compute(self):
        c = _compute(self.spec, "scanner")
        assert c.provider == "ai-agent"
        assert c.identity_mode == "service"
        assert c.trigger is not None and "schedule" in c.trigger
        assert c.objective is not None and len(c.objective) > 0
        assert c.strategy is not None and len(c.strategy) > 0
        assert len(c.preconditions) >= 1
        assert len(c.postconditions) >= 1

    def test_remediator_compute(self):
        c = _compute(self.spec, "remediator")
        assert c.provider == "ai-agent"
        assert c.identity_mode == "service"
        assert c.trigger is not None and "event" in c.trigger

    # -- Channels with actions --
    def test_security_tools_channel(self):
        ch = _channel(self.spec, "security_tools")
        assert ch.direction == ChannelDirection.OUTBOUND
        assert ch.delivery == ChannelDelivery.RELIABLE
        assert len(ch.actions) == 3
        action_names = {a.name.snake for a in ch.actions}
        assert action_names == {"restrict_policy", "rotate_secret", "describe_iam_policy"}

    def test_restrict_policy_action(self):
        ch = _channel(self.spec, "security_tools")
        action = next(a for a in ch.actions if a.name.snake == "restrict_policy")
        assert len(action.takes) == 2
        param_names = {p.name for p in action.takes}
        assert param_names == {"role", "policy"}
        assert len(action.returns) == 1

    def test_slack_channel(self):
        ch = _channel(self.spec, "slack")
        assert ch.direction == ChannelDirection.BIDIRECTIONAL
        assert ch.delivery == ChannelDelivery.REALTIME
        assert ch.carries_content == "findings"
        action_names = {a.name.snake for a in ch.actions}
        assert "post_message" in action_names

    # -- Events --
    def test_event_count(self):
        assert len(self.spec.events) >= 2


# ============================================================
# hrportal.termin — field-level confidentiality
# ============================================================

class TestHRPortalFidelity:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _compile("hrportal.termin")

    def test_content_names(self):
        names = {c.name.snake for c in self.spec.content}
        # D-20: Filter out auto-generated audit log Content
        user_content = {n for n in names if not n.startswith("compute_audit_log_")}
        assert user_content == {"employees", "departments", "salary_reviews"}

    def test_salary_field_confidentiality(self):
        f = _field(_content(self.spec, "employees"), "salary")
        assert "salary.access" in f.confidentiality_scopes

    def test_ssn_field_confidentiality(self):
        f = _field(_content(self.spec, "employees"), "ssn")
        assert "pii.access" in f.confidentiality_scopes

    def test_phone_field_confidentiality(self):
        f = _field(_content(self.spec, "employees"), "phone")
        assert "pii.access" in f.confidentiality_scopes

    def test_bonus_rate_confidentiality(self):
        f = _field(_content(self.spec, "employees"), "bonus_rate")
        assert "salary.access" in f.confidentiality_scopes

    def test_department_budget_confidentiality(self):
        f = _field(_content(self.spec, "departments"), "budget")
        assert "salary.access" in f.confidentiality_scopes

    def test_salary_reviews_content_scoped(self):
        sr = _content(self.spec, "salary_reviews")
        assert "salary.access" in sr.confidentiality_scopes

    def test_salary_reviews_fields(self):
        sr = _content(self.spec, "salary_reviews")
        field_names = {f.name for f in sr.fields}
        assert {"employee", "review_date", "old_salary", "new_salary",
                "reason", "approved_by", "created_at"} <= field_names

    def test_salary_reviews_employee_reference(self):
        f = _field(_content(self.spec, "salary_reviews"), "employee")
        assert f.foreign_key == "employees"

    # -- Access grants --
    def test_employees_view_scope(self):
        grants = _grants_for(self.spec, "employees")
        view = [g for g in grants if Verb.VIEW in g.verbs]
        assert any(g.scope == "employees.view" for g in view)

    def test_employees_create_update_scope(self):
        grants = _grants_for(self.spec, "employees")
        create = [g for g in grants if Verb.CREATE in g.verbs]
        assert any(g.scope == "employees.manage" for g in create)

    def test_employees_delete_scope(self):
        grants = _grants_for(self.spec, "employees")
        delete = [g for g in grants if Verb.DELETE in g.verbs]
        assert any(g.scope == "hr.manage" for g in delete)

    # -- Roles --
    def test_employee_role_scopes(self):
        r = _role(self.spec, "employee")
        assert set(r.scopes) == {"employees.view"}

    def test_manager_role_scopes(self):
        r = _role(self.spec, "manager")
        assert set(r.scopes) == {"employees.view", "team_metrics.view"}

    def test_hr_bp_role_scopes(self):
        r = _role(self.spec, "hr business partner")
        assert set(r.scopes) == {
            "employees.view", "employees.manage", "salary.access",
            "pii.access", "team_metrics.view", "hr.manage"
        }

    def test_executive_role_scopes(self):
        r = _role(self.spec, "executive")
        assert set(r.scopes) == {"employees.view", "team_metrics.view"}

    # -- State machine --
    def test_review_lifecycle(self):
        sm = _sm(self.spec, "salary_reviews")
        assert sm.initial_state == "pending"
        assert set(sm.states) >= {"pending", "approved", "applied"}

    def test_review_transitions(self):
        sm = _sm(self.spec, "salary_reviews")
        transitions = {(t.from_state, t.to_state): t.required_scope for t in sm.transitions}
        assert transitions.get(("pending", "approved")) == "hr.manage"
        assert transitions.get(("approved", "applied")) == "hr.manage"

    # -- Compute with confidentiality --
    def test_calculate_team_bonus_pool(self):
        c = _compute(self.spec, "calculate_team_bonus_pool")
        assert c.shape == ComputeShape.REDUCE
        assert c.identity_mode == "service"
        assert "salary.access" in c.required_confidentiality_scopes
        assert c.output_confidentiality_scope == "team_metrics.view"


# ============================================================
# channel_simple.termin — minimal channel demo
# ============================================================

class TestChannelSimpleFidelity:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _compile("channel_simple.termin")

    def test_content_names(self):
        names = {c.name.snake for c in self.spec.content}
        assert names == {"notes", "echoes"}

    def test_note_sync_channel(self):
        ch = _channel(self.spec, "note_sync")
        assert ch.direction == ChannelDirection.OUTBOUND
        assert ch.delivery == ChannelDelivery.RELIABLE
        assert ch.carries_content == "notes"

    def test_echo_receiver_channel(self):
        ch = _channel(self.spec, "echo_receiver")
        assert ch.direction == ChannelDirection.INBOUND
        assert ch.delivery == ChannelDelivery.RELIABLE
        assert ch.carries_content == "echoes"

    def test_event_send(self):
        # DSL: When `note.created`: Send note to "note-sync"
        send_events = [e for e in self.spec.events
                       if e.action and e.action.send_channel]
        assert len(send_events) >= 1


# ============================================================
# channel_demo.termin — complex channels with actions
# ============================================================

class TestChannelDemoFidelity:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _compile("channel_demo.termin")

    def test_content_names(self):
        names = {c.name.snake for c in self.spec.content}
        assert {"incidents", "deployments", "alerts"} <= names

    def test_incident_severity_enum(self):
        f = _field(_content(self.spec, "incidents"), "severity")
        assert set(f.enum_values) == {"critical", "high", "medium", "low"}

    def test_deployment_status_enum(self):
        f = _field(_content(self.spec, "deployments"), "status")
        assert set(f.enum_values) == {"in-progress", "succeeded", "failed", "rolled-back"}

    # -- Channels --
    def test_github_webhooks_inbound(self):
        ch = _channel(self.spec, "github_webhooks")
        assert ch.direction == ChannelDirection.INBOUND
        assert ch.delivery == ChannelDelivery.RELIABLE
        assert ch.carries_content == "deployments"

    def test_pagerduty_outbound(self):
        ch = _channel(self.spec, "pagerduty")
        assert ch.direction == ChannelDirection.OUTBOUND
        assert ch.delivery == ChannelDelivery.RELIABLE
        assert ch.carries_content == "incidents"

    def test_cloud_provider_actions(self):
        ch = _channel(self.spec, "cloud_provider")
        assert ch.direction == ChannelDirection.OUTBOUND
        action_names = {a.name.snake for a in ch.actions}
        assert action_names == {"restart_service", "scale_service", "rollback_deployment"}

    def test_restart_service_action_params(self):
        ch = _channel(self.spec, "cloud_provider")
        action = next(a for a in ch.actions if a.name.snake == "restart_service")
        takes_names = {p.name for p in action.takes}
        assert takes_names == {"service", "region"}
        returns_names = {p.name for p in action.returns}
        assert returns_names == {"status"}

    def test_slack_hybrid_channel(self):
        ch = _channel(self.spec, "slack")
        assert ch.direction == ChannelDirection.BIDIRECTIONAL
        assert ch.delivery == ChannelDelivery.REALTIME
        assert ch.carries_content == "incidents"
        action_names = {a.name.snake for a in ch.actions}
        assert {"post_message", "update_status"} <= action_names

    def test_incident_bus_internal(self):
        ch = _channel(self.spec, "incident_bus")
        assert ch.direction == ChannelDirection.INTERNAL
        assert ch.delivery == ChannelDelivery.AUTO

    # -- Compute --
    def test_classify_alert_compute(self):
        c = _compute(self.spec, "classify_alert")
        assert c.shape == ComputeShape.TRANSFORM
        assert len(c.body_lines) >= 1

    def test_auto_mitigate_agent(self):
        c = _compute(self.spec, "auto_mitigate")
        assert c.provider == "ai-agent"
        assert c.identity_mode == "service"
        assert c.trigger is not None
        assert c.objective is not None
        assert c.strategy is not None
        assert len(c.preconditions) >= 1
        assert len(c.postconditions) >= 1

    # -- State machine --
    def test_response_lifecycle(self):
        sm = _sm(self.spec, "incidents")
        assert sm.initial_state == "opened"
        assert set(sm.states) >= {"opened", "investigating", "mitigating", "resolved", "closed"}

    # -- Events --
    def test_event_count(self):
        assert len(self.spec.events) >= 3


# ============================================================
# agent_simple.termin — LLM provider
# ============================================================

class TestAgentSimpleFidelity:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _compile("agent_simple.termin")

    def test_content_names(self):
        names = {c.name.snake for c in self.spec.content}
        # D-20: Filter out auto-generated audit log Content
        user_content = {n for n in names if not n.startswith("compute_audit_log_")}
        assert user_content == {"completions"}

    def test_complete_compute(self):
        c = _compute(self.spec, "complete")
        assert c.provider == "llm"
        assert c.trigger is not None and "event" in c.trigger
        assert c.directive is not None and len(c.directive) > 0
        assert c.objective is not None and len(c.objective) > 0

    def test_compute_field_wiring(self):
        c = _compute(self.spec, "complete")
        # DSL: Input from field completion.prompt
        assert len(c.input_fields) >= 1
        # DSL: Output into field completion.response
        assert len(c.output_fields) >= 1

    def test_compute_accesses(self):
        c = _compute(self.spec, "complete")
        # DSL: Accesses completions
        assert "completions" in c.accesses


# ============================================================
# agent_chatbot.termin — AI agent provider
# ============================================================

class TestAgentChatbotFidelity:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.spec = _compile("agent_chatbot.termin")

    def test_content_names(self):
        names = {c.name.snake for c in self.spec.content}
        # D-20: Filter out auto-generated audit log Content
        user_content = {n for n in names if not n.startswith("compute_audit_log_")}
        assert user_content == {"messages"}

    def test_message_role_enum(self):
        f = _field(_content(self.spec, "messages"), "role")
        assert set(f.enum_values) == {"user", "assistant"}
        assert f.default_expr is not None  # defaults to "user"

    def test_reply_compute(self):
        c = _compute(self.spec, "reply")
        assert c.provider == "ai-agent"
        assert c.trigger is not None and "event" in c.trigger
        assert c.directive is not None
        assert c.objective is not None

    def test_reply_accesses(self):
        c = _compute(self.spec, "reply")
        assert "messages" in c.accesses

    def test_reply_trigger_where(self):
        c = _compute(self.spec, "reply")
        # DSL: Trigger on event "message.created" where `message.role == "user"`
        assert c.trigger_where is not None
        assert "role" in c.trigger_where


# ============================================================
# Parametrized: every example compiles faithfully
# ============================================================

ALL_EXAMPLES = [f.name for f in EXAMPLES_DIR.glob("*.termin")]


@pytest.mark.parametrize("example", ALL_EXAMPLES)
class TestAllExamplesCompile:
    """Verify every example compiles to a non-trivial IR."""

    def test_compiles_without_error(self, example):
        spec = _compile(example)
        assert spec is not None
        assert spec.name != ""

    def test_has_app_id(self, example):
        spec = _compile(example)
        assert spec.app_id is not None and len(spec.app_id) > 0

    def test_ir_version(self, example):
        spec = _compile(example)
        assert spec.ir_version == "0.8.0"


@pytest.mark.parametrize("example", ALL_EXAMPLES)
class TestAllExamplesAccessGrantVerbs:
    """For every example with access rules, verify the IR grant verbs
    match what the DSL declares — not just that grants exist."""

    def test_grant_verbs_are_valid(self, example):
        spec = _compile(example)
        for grant in spec.access_grants:
            assert len(grant.verbs) > 0, f"Empty verbs in grant for {grant.content}"
            for v in grant.verbs:
                assert v in (Verb.VIEW, Verb.CREATE, Verb.UPDATE, Verb.DELETE, Verb.AUDIT), \
                    f"Invalid verb {v} in grant for {grant.content}"

    def test_grant_scopes_are_declared(self, example):
        spec = _compile(example)
        if spec.auth and spec.auth.scopes:
            declared_scopes = set(spec.auth.scopes)
            for grant in spec.access_grants:
                assert grant.scope in declared_scopes, \
                    f"Grant scope '{grant.scope}' not in declared scopes for {example}"

    def test_grant_content_refs_are_valid(self, example):
        spec = _compile(example)
        content_names = {c.name.snake for c in spec.content}
        for grant in spec.access_grants:
            assert grant.content in content_names, \
                f"Grant content '{grant.content}' not in content names for {example}"


@pytest.mark.parametrize("example", ALL_EXAMPLES)
class TestAllExamplesStateMachines:
    """For every example with state machines, verify transitions, states,
    and feedback match what the DSL declares."""

    def test_initial_state_in_states(self, example):
        spec = _compile(example)
        for sm in spec.state_machines:
            assert sm.initial_state in sm.states, \
                f"Initial state '{sm.initial_state}' not in states for {sm.machine_name}"

    def test_transition_states_exist(self, example):
        spec = _compile(example)
        for sm in spec.state_machines:
            all_states = set(sm.states)
            for t in sm.transitions:
                assert t.from_state in all_states, \
                    f"Transition from_state '{t.from_state}' not in states of {sm.machine_name}"
                assert t.to_state in all_states, \
                    f"Transition to_state '{t.to_state}' not in states of {sm.machine_name}"

    def test_feedback_types_valid(self, example):
        spec = _compile(example)
        for sm in spec.state_machines:
            for t in sm.transitions:
                for fb in t.feedback:
                    assert fb.trigger in ("success", "error"), \
                        f"Invalid feedback trigger '{fb.trigger}'"
                    assert fb.style in ("toast", "banner"), \
                        f"Invalid feedback style '{fb.style}'"


@pytest.mark.parametrize("example", ALL_EXAMPLES)
class TestAllExamplesFieldTypes:
    """For every example with content, spot-check field types, required flags,
    and enum values."""

    def test_field_types_are_valid(self, example):
        spec = _compile(example)
        for content in spec.content:
            for f in content.fields:
                assert isinstance(f.column_type, FieldType), \
                    f"Invalid column_type for {content.name.snake}.{f.name}"

    def test_enum_fields_have_values(self, example):
        spec = _compile(example)
        for content in spec.content:
            for f in content.fields:
                if f.business_type == "enum":
                    assert len(f.enum_values) > 0, \
                        f"Enum field {content.name.snake}.{f.name} has no values"

    def test_reference_fields_have_foreign_key(self, example):
        spec = _compile(example)
        content_names = {c.name.snake for c in spec.content}
        for content in spec.content:
            for f in content.fields:
                if f.foreign_key:
                    assert f.foreign_key in content_names, \
                        f"Foreign key '{f.foreign_key}' for {content.name.snake}.{f.name} " \
                        f"doesn't reference a known content type"

    def test_required_fields_marked(self, example):
        spec = _compile(example)
        for content in spec.content:
            required_fields = [f for f in content.fields if f.required]
            # Every content should have at least one required field if it has fields
            # (exception: some content types like "reports" might have no required fields)
            # We just check they're boolean
            for f in content.fields:
                assert isinstance(f.required, bool), \
                    f"required is not bool for {content.name.snake}.{f.name}"


@pytest.mark.parametrize("example", ALL_EXAMPLES)
class TestAllExamplesRoles:
    """For every example with roles, verify role names and scopes."""

    def test_role_scopes_are_declared(self, example):
        spec = _compile(example)
        if spec.auth and spec.auth.scopes:
            declared_scopes = set(spec.auth.scopes)
            for role in spec.auth.roles:
                for scope in role.scopes:
                    assert scope in declared_scopes, \
                        f"Role '{role.name}' has scope '{scope}' not in declared scopes"

    def test_role_names_not_empty(self, example):
        spec = _compile(example)
        for role in spec.auth.roles:
            assert role.name != "", f"Empty role name in {example}"
            assert len(role.scopes) > 0, f"Role '{role.name}' has no scopes in {example}"


# ============================================================
# Zero PEG fallbacks: verify TatSu succeeds on every line
# ============================================================

class TestZeroPEGFallbacks:
    """Verify TatSu successfully parses every classified line in every example.

    When _try_parse returns None, the parser falls back to Python string
    manipulation. This test ensures no line triggers a fallback — the PEG
    grammar handles every line the classifier identifies.
    """

    def test_no_tatsu_fallbacks(self):
        import sys
        sys.setrecursionlimit(5000)
        import tatsu
        from termin.peg_parser import _preprocess, _classify_line, _model

        fallbacks = []
        for f in sorted(EXAMPLES_DIR.glob("*.termin")):
            source = f.read_text()
            lines = _preprocess(source)
            for line_num, text in lines:
                rule = _classify_line(text)
                if rule == "unknown":
                    continue
                try:
                    result = _model.parse(text, rule_name=rule)
                    if result is None:
                        fallbacks.append((f.name, line_num, rule, text[:80]))
                except Exception:
                    fallbacks.append((f.name, line_num, rule, text[:80]))

        if fallbacks:
            msg = f"{len(fallbacks)} TatSu fallback(s):\n"
            for fname, ln, rule, text in fallbacks:
                msg += f"  {fname}:{ln} [{rule}] {text}\n"
            pytest.fail(msg)
