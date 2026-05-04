# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""D-11: Auto-generated REST API tests.

Tests that every Content gets CRUD RouteSpecs automatically, that headless
.termin files (no user stories) compile, that 'api' is a reserved page slug,
and that state transition routes are auto-generated.
"""

from pathlib import Path

import pytest

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower
from termin_core.ir.types import (
    RouteKind, HttpMethod, RouteSpec,
)
from termin.errors import SemanticError


# ── Helper ──

def _compile(source: str):
    """Parse, analyze, and lower a .termin source string. Returns AppSpec."""
    program, errors = parse(source)
    assert errors.ok, f"Parse errors:\n{errors.format()}"
    result = analyze(program)
    assert result.ok, f"Analysis errors:\n{result.format()}"
    return lower(program)


def _compile_with_errors(source: str):
    """Parse and analyze a .termin source string. Returns (program, CompileResult)."""
    program, errors = parse(source)
    assert errors.ok, f"Parse errors:\n{errors.format()}"
    result = analyze(program)
    return program, result


def _find_routes(spec, content_ref: str):
    """Find all routes targeting a given content_ref."""
    return [r for r in spec.routes if r.content_ref == content_ref]


def _find_route(spec, content_ref: str, kind: RouteKind):
    """Find the first route matching content_ref and kind."""
    for r in spec.routes:
        if r.content_ref == content_ref and r.kind == kind:
            return r
    return None


# ── Fixtures ──

MINIMAL_CONTENT = """\
Application: Minimal
  Description: Minimal app with one Content

Identity:
  Scopes are "items.read" and "items.write"
  A "user" has "items.read" and "items.write"

Content called "items":
  Each item has a name which is text, required
  Anyone with "items.read" can view items
  Anyone with "items.write" can create or update items
"""

HEADLESS_SERVICE = """\
Application: Order Service
  Description: A headless order processing service

Identity:
  Scopes are "orders.read", "orders.write", and "orders.admin"
  A "writer" has "orders.read" and "orders.write"

Content called "orders":
  Each order has a customer which is text, required
  Each order has a total which is currency
  Each order has a priority which is one of: "low", "medium", "high"
  Each order has an order lifecycle which is state:
    order lifecycle starts as pending
    order lifecycle can also be confirmed or shipped
    pending can become confirmed if the user has orders.write
    confirmed can become shipped if the user has orders.admin
  Anyone with "orders.read" can view orders
  Anyone with "orders.write" can create or update orders
  Anyone with "orders.admin" can delete orders
"""

MULTI_CONTENT = """\
Application: Multi Content
  Description: Multiple content types

Identity:
  Scopes are "read", "write", and "admin"
  A "user" has "read" and "write"

Content called "projects":
  Each project has a name which is text, required
  Anyone with "read" can view projects
  Anyone with "write" can create or update projects
  Anyone with "admin" can delete projects

Content called "tasks":
  Each task has a title which is text, required
  Each task has a project which references projects, restrict on delete
  Anyone with "read" can view tasks
  Anyone with "write" can create or update tasks
"""

API_SLUG_CONFLICT = """\
Application: Bad Slug
  Description: App with api slug conflict

Identity:
  Scopes are "read"
  A "user" has "read"

Content called "widgets":
  Each widget has a name which is text
  Anyone with "read" can view widgets

As a user, I want to see a page "api" so that I see the api:
  Display text "hello"
"""


# ============================================================
# Test: Content with no user stories gets CRUD RouteSpecs
# ============================================================

class TestAutoCrudRoutes:
    """Every Content automatically gets CRUD routes in the IR."""

    def test_content_without_stories_gets_crud_routes(self):
        spec = _compile(MINIMAL_CONTENT)
        routes = _find_routes(spec, "items")
        assert len(routes) >= 5, f"Expected at least 5 CRUD routes, got {len(routes)}"

        kinds = {r.kind for r in routes}
        assert RouteKind.LIST in kinds, "Missing LIST route"
        assert RouteKind.CREATE in kinds, "Missing CREATE route"
        assert RouteKind.GET_ONE in kinds, "Missing GET_ONE route"
        assert RouteKind.UPDATE in kinds, "Missing UPDATE route"
        assert RouteKind.DELETE in kinds, "Missing DELETE route"

    def test_crud_route_paths(self):
        spec = _compile(MINIMAL_CONTENT)
        list_route = _find_route(spec, "items", RouteKind.LIST)
        assert list_route.path == "/api/v1/items"
        assert list_route.method == HttpMethod.GET

        create_route = _find_route(spec, "items", RouteKind.CREATE)
        assert create_route.path == "/api/v1/items"
        assert create_route.method == HttpMethod.POST

        get_route = _find_route(spec, "items", RouteKind.GET_ONE)
        assert get_route.path == "/api/v1/items/{id}"
        assert get_route.method == HttpMethod.GET

        update_route = _find_route(spec, "items", RouteKind.UPDATE)
        assert update_route.path == "/api/v1/items/{id}"
        assert update_route.method == HttpMethod.PUT

        delete_route = _find_route(spec, "items", RouteKind.DELETE)
        assert delete_route.path == "/api/v1/items/{id}"
        assert delete_route.method == HttpMethod.DELETE

    def test_crud_route_scopes(self):
        spec = _compile(MINIMAL_CONTENT)
        list_route = _find_route(spec, "items", RouteKind.LIST)
        assert list_route.required_scope == "items.read"

        create_route = _find_route(spec, "items", RouteKind.CREATE)
        assert create_route.required_scope == "items.write"

        update_route = _find_route(spec, "items", RouteKind.UPDATE)
        assert update_route.required_scope == "items.write"

    def test_multi_content_all_get_routes(self):
        spec = _compile(MULTI_CONTENT)
        project_routes = _find_routes(spec, "projects")
        task_routes = _find_routes(spec, "tasks")
        assert len(project_routes) >= 5
        assert len(task_routes) >= 4  # tasks don't have delete scope

        # Projects have delete scope
        project_kinds = {r.kind for r in project_routes}
        assert RouteKind.DELETE in project_kinds


# ============================================================
# Test: Headless .termin compiles and produces routes
# ============================================================

class TestHeadlessService:
    """A .termin file with no user stories is a headless service."""

    def test_headless_compiles(self):
        spec = _compile(HEADLESS_SERVICE)
        assert spec.name == "Order Service"
        assert len(spec.pages) == 0, "Headless service should have no pages"

    def test_headless_has_crud_routes(self):
        spec = _compile(HEADLESS_SERVICE)
        routes = _find_routes(spec, "orders")
        kinds = {r.kind for r in routes}
        assert RouteKind.LIST in kinds
        assert RouteKind.CREATE in kinds
        assert RouteKind.GET_ONE in kinds
        assert RouteKind.UPDATE in kinds
        assert RouteKind.DELETE in kinds

    def test_headless_route_scopes(self):
        spec = _compile(HEADLESS_SERVICE)
        delete_route = _find_route(spec, "orders", RouteKind.DELETE)
        assert delete_route.required_scope == "orders.admin"

    def test_headless_has_state_machine_transitions(self):
        spec = _compile(HEADLESS_SERVICE)
        transition_routes = [r for r in spec.routes
                             if r.content_ref == "orders" and r.kind == RouteKind.TRANSITION]
        assert len(transition_routes) >= 2, f"Expected at least 2 transition routes, got {len(transition_routes)}"

        target_states = {r.target_state for r in transition_routes}
        assert "confirmed" in target_states
        assert "shipped" in target_states

    def test_headless_transition_paths(self):
        spec = _compile(HEADLESS_SERVICE)
        transition_routes = [r for r in spec.routes
                             if r.content_ref == "orders" and r.kind == RouteKind.TRANSITION]
        paths = {r.path for r in transition_routes}
        # v0.9: transition path includes the snake_case machine_name segment.
        assert "/api/v1/orders/{id}/_transition/order_lifecycle/confirmed" in paths
        assert "/api/v1/orders/{id}/_transition/order_lifecycle/shipped" in paths

    def test_headless_transition_scopes(self):
        """Transition routes don't set route-level scope; do_state_transition checks it."""
        spec = _compile(HEADLESS_SERVICE)
        transition_routes = [r for r in spec.routes
                             if r.content_ref == "orders" and r.kind == RouteKind.TRANSITION]
        # Transition routes should have no route-level scope — the runtime
        # checks scopes in do_state_transition based on the actual from→to pair
        for tr in transition_routes:
            assert tr.required_scope is None


# ============================================================
# Test: 'api' is a reserved page slug
# ============================================================

class TestApiReservedSlug:
    """The page slug 'api' is reserved and produces a compiler error."""

    def test_api_slug_produces_error(self):
        program, result = _compile_with_errors(API_SLUG_CONFLICT)
        assert not result.ok, "Expected a semantic error for 'api' page slug"
        errors = [e for e in result.errors if "api" in e.message.lower() and "reserved" in e.message.lower()]
        assert len(errors) > 0, f"Expected a reserved slug error, got: {result.format()}"


# ============================================================
# Test: State transition routes auto-generated
# ============================================================

class TestStateTransitionRoutes:
    """Content with state machines gets transition RouteSpecs."""

    def test_state_machine_produces_transition_routes(self):
        spec = _compile(HEADLESS_SERVICE)
        transition_routes = [r for r in spec.routes
                             if r.kind == RouteKind.TRANSITION]
        assert len(transition_routes) >= 2

    def test_transition_routes_are_post(self):
        spec = _compile(HEADLESS_SERVICE)
        transition_routes = [r for r in spec.routes
                             if r.kind == RouteKind.TRANSITION]
        for r in transition_routes:
            assert r.method == HttpMethod.POST


# ============================================================
# Test: Existing examples still compile after removing Expose a REST API
# ============================================================

class TestExistingExamplesCompile:
    """All examples should compile successfully after removing Expose a REST API."""

    @pytest.fixture(params=[
        "warehouse.termin",
        "helpdesk.termin",
        "projectboard.termin",
        "hello.termin",
        "hello_user.termin",
        "compute_demo.termin",
        "security_agent.termin",
        "hrportal.termin",
        "channel_simple.termin",
        "channel_demo.termin",
        "agent_simple.termin",
        "agent_chatbot.termin",
    ])
    def example_name(self, request):
        return request.param

    def test_example_compiles(self, example_name):
        path = Path(__file__).parent.parent / "examples" / example_name
        source = path.read_text()
        program, errors = parse(source)
        assert errors.ok, f"{example_name} parse errors:\n{errors.format()}"
        result = analyze(program)
        assert result.ok, f"{example_name} analysis errors:\n{result.format()}"
        spec = lower(program)
        assert spec is not None
        # After D-11, every Content should have auto-generated CRUD routes
        # D-20: Audit log Content (compute_audit_log_*) is read-only — only LIST + GET
        # v0.9.2 L7.5: compute_refusals sidecar retired; no exclusion
        # needed for it anymore.
        for content in spec.content:
            routes = _find_routes(spec, content.name.snake)
            if content.name.snake.startswith("compute_audit_log_"):
                assert len(routes) >= 2, (
                    f"{example_name}: read-only Content '{content.name.display}' has only "
                    f"{len(routes)} routes, expected at least 2 read routes"
                )
            else:
                assert len(routes) >= 4, (
                    f"{example_name}: Content '{content.name.display}' has only "
                    f"{len(routes)} routes, expected at least 4 CRUD routes"
                )

    def test_example_has_state_transition_routes(self, example_name):
        path = Path(__file__).parent.parent / "examples" / example_name
        source = path.read_text()
        program, errors = parse(source)
        if not errors.ok:
            pytest.skip(f"Parse errors in {example_name}")
        result = analyze(program)
        if not result.ok:
            pytest.skip(f"Analysis errors in {example_name}")
        spec = lower(program)
        for sm in spec.state_machines:
            if sm.primitive_type != "content":
                continue
            transition_routes = [r for r in spec.routes
                                 if r.content_ref == sm.content_ref
                                 and r.kind == RouteKind.TRANSITION]
            assert len(transition_routes) >= 1, (
                f"{example_name}: State machine '{sm.machine_name}' for "
                f"'{sm.content_ref}' has no transition routes"
            )


# ============================================================
# Test: Expose a REST API syntax is removed
# ============================================================

class TestExposeApiRemoved:
    """The 'Expose a REST API' syntax should no longer be recognized."""

    def test_expose_api_line_not_classified(self):
        """Lines starting with 'Expose a REST API' should not be classified."""
        from termin.peg_parser import _classify_line
        result = _classify_line("Expose a REST API at /api/v1:")
        assert result == "unknown", f"Expected 'unknown', got '{result}'"

    def test_api_section_not_in_ast(self):
        """The Program AST should not have an 'api' field."""
        source = MINIMAL_CONTENT
        program, errors = parse(source)
        assert errors.ok
        assert not hasattr(program, 'api'), "Program should not have 'api' attribute (removed)"

    def test_http_method_lines_not_classified(self):
        """Lines starting with HTTP methods should not be classified."""
        from termin.peg_parser import _classify_line
        assert _classify_line("GET /products lists products") == "unknown"
        assert _classify_line("POST /products creates a product") == "unknown"


# ============================================================
# Test: headless_service.termin example
# ============================================================

class TestHeadlessServiceExample:
    """The headless_service.termin example should compile."""

    def test_headless_example_compiles(self):
        path = Path(__file__).parent.parent / "examples" / "headless_service.termin"
        if not path.exists():
            pytest.skip("headless_service.termin not yet created")
        source = path.read_text()
        spec = _compile(source)
        assert spec.name == "Order Service"
        assert len(spec.pages) == 0
        routes = _find_routes(spec, "orders")
        assert len(routes) >= 5
