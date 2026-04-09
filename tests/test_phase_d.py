"""Tests for Phase D features: Role Aliases, Boundary Properties,
State on Non-Content Primitives, and Reflection endpoint.
"""

import ast
import json
import pytest
from pathlib import Path

from termin.peg_parser import parse_peg as parse, _classify_line
from termin.analyzer import analyze
from termin.lower import lower
from termin.ir import BoundaryPropertySpec


# ============================================================
# Shared DSL base for building test programs
# ============================================================

VALID_BASE = '''\
Application: Phase D Test
  Description: Tests for Phase D features

Users authenticate with stub
Scopes are "orders.read", "orders.write", and "orders.admin"

An "order clerk" has "orders.read" and "orders.write"
An "order manager" has "orders.read", "orders.write", and "orders.admin"

Content called "orders":
  Each order has a customer which is text, required
  Each order has a total which is currency
  Anyone with "orders.read" can view orders
  Anyone with "orders.write" can create or update orders

Content called "order lines":
  Each order line has a parent order which references orders, required
  Each order line has a quantity which is a whole number, minimum 1
  Anyone with "orders.read" can view order lines
  Anyone with "orders.write" can create or update order lines
'''


# ============================================================
# Feature 1: Role Aliases
# ============================================================

class TestRoleAliasClassification:
    def test_classify_role_alias(self):
        assert _classify_line('"clerk" is alias for "order clerk"') == "role_alias_line"

    def test_role_alias_before_role_decl(self):
        assert _classify_line('"clerk" is alias for "order clerk"') == "role_alias_line"
        assert _classify_line('A "order clerk" has "read"') == "role_standard_line"

    def test_role_alias_multiword(self):
        assert _classify_line('"mgr" is alias for "order manager"') == "role_alias_line"


class TestRoleAliasParser:
    def test_parse_role_alias(self):
        source = VALID_BASE + '"clerk" is alias for "order clerk"\n'
        program, errors = parse(source)
        assert errors.ok, errors.format()
        assert len(program.role_aliases) == 1
        assert program.role_aliases[0].short_name == "clerk"
        assert program.role_aliases[0].full_name == "order clerk"

    def test_parse_multiple_aliases(self):
        source = VALID_BASE + (
            '"clerk" is alias for "order clerk"\n'
            '"mgr" is alias for "order manager"\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        assert len(program.role_aliases) == 2


class TestRoleAliasAnalyzer:
    def test_alias_target_must_exist(self):
        source = VALID_BASE + '"clerk" is alias for "nonexistent role"\n'
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert not result.ok
        assert any("undefined role" in str(e).lower() for e in result.errors)

    def test_alias_valid_target(self):
        source = VALID_BASE + '"clerk" is alias for "order clerk"\n'
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()

    def test_alias_resolves_in_story(self):
        source = VALID_BASE + (
            '"clerk" is alias for "order clerk"\n\n'
            'As a clerk, I want to see orders\n'
            '  so that I can process them:\n'
            '    Show a page called "Orders"\n'
            '    Display a table of orders with columns: customer, total\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()


# ============================================================
# Feature 2: Boundary Properties
# ============================================================

class TestBoundaryPropertyClassification:
    def test_classify_boundary_exposes(self):
        assert _classify_line('Exposes property "order count" : whole number = [orders.length]') == "boundary_exposes_line"


class TestBoundaryPropertyParser:
    def test_parse_boundary_with_property(self):
        source = VALID_BASE + (
            'Boundary called "order processing":\n'
            '  Contains orders, order lines\n'
            '  Identity inherits from application\n'
            '  Exposes property "order count" : whole number = [orders.length]\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        assert len(program.boundaries) == 1
        bnd = program.boundaries[0]
        assert bnd.name == "order processing"
        assert len(bnd.properties) == 1
        prop = bnd.properties[0]
        assert prop.name == "order count"
        assert prop.type_name == "whole number"
        assert prop.expr == "orders.length"

    def test_parse_boundary_multiple_properties(self):
        source = VALID_BASE + (
            'Boundary called "order processing":\n'
            '  Contains orders, order lines\n'
            '  Identity inherits from application\n'
            '  Exposes property "order count" : whole number = [orders.length]\n'
            '  Exposes property "total revenue" : currency = [orders.reduce((a,o) => a + o.total, 0)]\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        bnd = program.boundaries[0]
        assert len(bnd.properties) == 2
        assert bnd.properties[1].name == "total revenue"
        assert bnd.properties[1].type_name == "currency"


class TestBoundaryPropertyIR:
    def test_lower_boundary_properties(self):
        source = VALID_BASE + (
            'Boundary called "order processing":\n'
            '  Contains orders, order lines\n'
            '  Identity inherits from application\n'
            '  Exposes property "order count" : whole number = [orders.length]\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()
        spec = lower(program)
        assert len(spec.boundaries) == 1
        bnd = spec.boundaries[0]
        assert len(bnd.properties) == 1
        prop = bnd.properties[0]
        assert isinstance(prop, BoundaryPropertySpec)
        assert prop.name == "order count"
        assert prop.type_name == "whole number"
        assert prop.expr == "orders.length"


# ============================================================
# Feature 3: State on Non-Content Primitives
# ============================================================

class TestStateNonContentClassification:
    def test_classify_state_for_channel(self):
        assert _classify_line('State for channel "order webhook" called "lifecycle":') == "state_header"

    def test_classify_state_for_compute(self):
        assert _classify_line('State for compute "calculate total" called "execution":') == "state_header"


class TestStateNonContentParser:
    def test_parse_state_for_channel(self):
        source = VALID_BASE + (
            'Channel called "order webhook":\n'
            '  Carries orders\n'
            '  Direction: inbound\n'
            '  Delivery: reliable\n'
            '  Endpoint: /webhooks/orders\n'
            '  Requires "orders.write" to send\n\n'
            'State for channel "order webhook" called "lifecycle":\n'
            '  A webhook starts as "active"\n'
            '  A webhook can also be "paused" or "disabled"\n'
            '  An active webhook can become paused if the user has "orders.admin"\n'
            '  A paused webhook can become active if the user has "orders.admin"\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        assert len(program.state_machines) == 1
        sm = program.state_machines[0]
        assert sm.content_name == "order webhook"
        assert sm.machine_name == "lifecycle"
        assert sm.initial_state == "active"
        assert "paused" in sm.states
        assert "disabled" in sm.states


class TestStateNonContentAnalyzer:
    def test_state_on_channel_passes_analysis(self):
        source = VALID_BASE + (
            'Channel called "order webhook":\n'
            '  Carries orders\n'
            '  Direction: inbound\n'
            '  Delivery: reliable\n'
            '  Endpoint: /webhooks/orders\n'
            '  Requires "orders.write" to send\n\n'
            'State for channel "order webhook" called "lifecycle":\n'
            '  A webhook starts as "active"\n'
            '  A webhook can also be "paused"\n'
            '  An active webhook can become paused if the user has "orders.admin"\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()

    def test_state_on_undefined_channel_fails(self):
        source = VALID_BASE + (
            'State for channel "nonexistent" called "lifecycle":\n'
            '  A thing starts as "active"\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert not result.ok
        assert any("undefined" in str(e).lower() for e in result.errors)


# ============================================================
# Feature 4: Reflection Endpoint
# ============================================================

@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("pyjexl"),
    reason="Legacy fastapi backend requires pyjexl (deprecated)"
)
class TestReflectionEndpoint:
    def _compile_to_code(self, source: str) -> str:
        from termin.backends.fastapi import FastApiBackend
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()
        spec = lower(program)
        backend = FastApiBackend()
        return backend.generate(spec)

    def test_reflection_endpoint_generated(self):
        code = self._compile_to_code(VALID_BASE)
        assert "/api/reflect" in code
        assert "api_reflect" in code
        assert "APP_SPEC_JSON" in code

    def test_reflection_endpoint_valid_python(self):
        code = self._compile_to_code(VALID_BASE)
        # Ensure the generated code is valid Python
        ast.parse(code)

    def test_reflection_endpoint_scope_guarded(self):
        code = self._compile_to_code(VALID_BASE)
        # Should be guarded by the first view scope: "orders.read"
        assert 'require_scope("orders.read")' in code

    def test_reflection_endpoint_returns_json(self):
        """Compile, import, and verify the /api/reflect endpoint returns valid JSON."""
        import importlib
        import sys
        import tempfile
        import os

        code = self._compile_to_code(VALID_BASE)

        # Write to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False,
                                          dir=str(Path(__file__).parent),
                                          encoding='utf-8') as f:
            f.write(code)
            tmp_path = f.name

        try:
            # Import the generated module
            spec_mod = importlib.util.spec_from_file_location("reflect_test_app", tmp_path)
            mod = importlib.util.module_from_spec(spec_mod)
            spec_mod.loader.exec_module(mod)

            from starlette.testclient import TestClient
            client = TestClient(mod.app)

            # Call the reflect endpoint (using stub auth)
            response = client.get(
                "/api/reflect",
                cookies={"session": "stub_user:read orders,write orders,admin orders:order clerk"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "Phase D Test"
            assert "content" in data
            assert "auth" in data
        finally:
            os.unlink(tmp_path)


# ============================================================
# Integration: all features together
# ============================================================

class TestPhaseD_Integration:
    """End-to-end test combining all Phase D features."""

    FULL_SOURCE = VALID_BASE + (
        '"clerk" is alias for "order clerk"\n'
        '"mgr" is alias for "order manager"\n\n'
        'Channel called "order webhook":\n'
        '  Carries orders\n'
        '  Direction: inbound\n'
        '  Delivery: reliable\n'
        '  Endpoint: /webhooks/orders\n'
        '  Requires "orders.write" to send\n\n'
        'State for channel "order webhook" called "lifecycle":\n'
        '  A webhook starts as "active"\n'
        '  A webhook can also be "paused"\n'
        '  An active webhook can become paused if the user has "orders.admin"\n\n'
        'Boundary called "order processing":\n'
        '  Contains orders, order lines\n'
        '  Identity inherits from application\n'
        '  Exposes property "order count" : whole number = [orders.length]\n\n'
        'As a clerk, I want to see orders\n'
        '  so that I can process them:\n'
        '    Show a page called "Orders"\n'
        '    Display a table of orders with columns: customer, total\n'
    )

    def test_full_pipeline(self):
        program, errors = parse(self.FULL_SOURCE)
        assert errors.ok, errors.format()

        result = analyze(program)
        assert result.ok, result.format()

        spec = lower(program)
        assert spec.name == "Phase D Test"
        assert len(spec.boundaries) == 1
        assert len(spec.boundaries[0].properties) == 1
        assert len(spec.channels) == 1

    def test_full_codegen(self):
        from termin.backends.fastapi import FastApiBackend
        program, errors = parse(self.FULL_SOURCE)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()
        spec = lower(program)
        backend = FastApiBackend()
        code = backend.generate(spec)
        # Verify valid Python
        ast.parse(code)
        # Verify reflection endpoint exists
        assert "/api/reflect" in code
