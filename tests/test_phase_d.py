# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for Phase D features: Role Aliases, Boundary Properties,
State on Non-Content Primitives, and Reflection endpoint.
"""

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


class TestBoundaryAnalyzer:
    def test_duplicate_content_across_boundaries(self):
        """Content in two boundaries should produce an error."""
        source = VALID_BASE + (
            'Boundary called "sales":\n'
            '  Contains orders\n'
            '  Identity inherits from application\n\n'
            'Boundary called "fulfillment":\n'
            '  Contains orders\n'
            '  Identity inherits from application\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert not result.ok, "Content in two boundaries should be rejected"
        assert any("TERMIN-S030" in str(e) for e in result.errors), (
            f"Expected TERMIN-S030, got: {result.format()}"
        )


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

    def test_full_ir_lowering(self):
        """Verify the full pipeline lowers to IR correctly."""
        program, errors = parse(self.FULL_SOURCE)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()
        spec = lower(program)
        assert len(spec.boundaries) == 1
        assert spec.boundaries[0].name.snake == "order_processing"
        assert len(spec.channels) == 1


# ============================================================
# D-19: Dependent Field Values
# ============================================================

D19_BASE = '''\
Application: D-19 Test
  Description: Dependent field values test

Users authenticate with stub
Scopes are "orders.read" and "orders.write"

An "order clerk" has "orders.read" and "orders.write"

Content called "laptop orders":
  Each order has a size which is an enum, is one of: "14-inch", "16-inch"
  Each order has a ram which is a whole number
  Each order has a color which is text
  Anyone with "orders.read" can view laptop orders
  Anyone with "orders.write" can create or update laptop orders

  When `size == "14-inch"`, ram must be one of: 8, 16, 24
  When `size == "16-inch"`, ram must be one of: 16, 32, 48
  When `size == "16-inch"`, color defaults to "space gray"
'''


class TestD19Parser:
    """D-19: Parser tests for dependent field values."""

    def test_classify_content_when_one_of(self):
        line = 'When `size == "14-inch"`, ram must be one of: 8, 16, 24'
        assert _classify_line(line) == "content_when_line"

    def test_classify_content_when_defaults(self):
        line = 'When `size == "16-inch"`, color defaults to "space gray"'
        assert _classify_line(line) == "content_when_line"

    def test_classify_unconditional_constraint(self):
        line = 'ram must be one of: 8, 16, 24, 32, 48'
        assert _classify_line(line) == "unconditional_constraint_line"

    def test_event_when_not_confused_with_content_when(self):
        """Event When (no comma + constraint) should still classify as event."""
        line = 'When `quantity < 10`:'
        assert _classify_line(line) == "event_expr_line"

    def test_parse_d19_full(self):
        program, errors = parse(D19_BASE)
        assert errors.ok, errors.format()
        assert len(program.contents) == 1
        ct = program.contents[0]
        assert ct.name == "laptop orders"
        assert len(ct.dependent_values) == 3

    def test_when_one_of_parsed(self):
        program, _ = parse(D19_BASE)
        ct = program.contents[0]
        dv = ct.dependent_values[0]
        assert dv.when_expr == 'size == "14-inch"'
        assert dv.field == "ram"
        assert dv.constraint == "one_of"
        assert dv.values == [8, 16, 24]

    def test_when_defaults_parsed(self):
        program, _ = parse(D19_BASE)
        ct = program.contents[0]
        dv = ct.dependent_values[2]
        assert dv.when_expr == 'size == "16-inch"'
        assert dv.field == "color"
        assert dv.constraint == "default"
        assert dv.values == ["space gray"]

    def test_is_one_of_field_constraint(self):
        """Field-level 'is one of' constraint on base type."""
        src = '''\
Application: Is One Of Test
  Description: Test

Users authenticate with stub
Scopes are "read"

Content called "orders":
  Each order has a ram which is a whole number, is one of: 8, 16, 24, 32
  Anyone with "read" can view orders
'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        f = program.contents[0].fields[0]
        assert f.type_expr.base_type == "whole_number"
        assert f.type_expr.one_of_values == [8, 16, 24, 32]


class TestD19IR:
    """D-19: IR lowering tests for dependent field values."""

    def test_dependent_values_in_ir(self):
        program, errors = parse(D19_BASE)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()
        spec = lower(program)
        cs = spec.content[0]
        assert len(cs.dependent_values) == 3

    def test_dependent_value_shape(self):
        program, _ = parse(D19_BASE)
        spec = lower(program)
        cs = spec.content[0]
        dv = cs.dependent_values[0]
        assert dv.when == 'size == "14-inch"'
        assert dv.field == "ram"
        assert dv.constraint == "one_of"
        assert dv.values == (8, 16, 24)

    def test_is_one_of_in_field_ir(self):
        src = '''\
Application: One Of IR Test
  Description: Test

Users authenticate with stub
Scopes are "read"

Content called "orders":
  Each order has a ram which is a whole number, is one of: 8, 16, 24
  Anyone with "read" can view orders
'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        spec = lower(program)
        f = spec.content[0].fields[0]
        assert f.one_of_values == (8, 16, 24)


class TestD19Analyzer:
    """D-19: Analyzer tests for dependent field values."""

    def test_undefined_field_in_when_clause(self):
        src = '''\
Application: Analyzer Test
  Description: Test

Users authenticate with stub
Scopes are "read" and "write"

Content called "orders":
  Each order has a size which is an enum, is one of: "14-inch", "16-inch"
  Anyone with "read" can view orders
  Anyone with "write" can create orders

  When `size == "14-inch"`, nonexistent_field must be one of: 1, 2, 3
'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert not result.ok
        assert any("TERMIN-S029" in str(e) for e in result.errors)

    def test_exhaustiveness_warning(self):
        """When clauses that don't cover all enum values should warn."""
        src = '''\
Application: Exhaustiveness Test
  Description: Test

Users authenticate with stub
Scopes are "read" and "write"

Content called "laptops":
  Each laptop has a size which is one of: "small", "medium", "large"
  Each laptop has a ram which is a whole number
  Anyone with "read" can view laptops
  Anyone with "write" can create laptops

  When `size == "small"`, ram must be one of: 8, 16
  When `size == "large"`, ram must be one of: 32, 48
'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        result = analyze(program)
        # Should have a warning (not error) about missing "medium"
        warnings = [e for e in result.errors if "TERMIN-W001" in str(e)]
        assert len(warnings) == 1
        assert "medium" in str(warnings[0])


class TestD19Runtime:
    """D-19: Runtime validation tests for dependent field values."""

    def _build_ir(self):
        return json.dumps({
            "ir_version": "0.8.0",
            "reflection_enabled": False,
            "app_id": "d19-test",
            "name": "D-19 Test",
            "description": "",
            "auth": {
                "provider": "stub",
                "scopes": ["orders.read", "orders.write"],
                "roles": [{"name": "clerk", "scopes": ["orders.read", "orders.write"]}],
            },
            "content": [{
                "name": {"display": "laptop orders", "snake": "laptop_orders", "pascal": "LaptopOrders"},
                "singular": "laptop_order",
                "fields": [
                    {"name": "size", "column_type": "TEXT", "business_type": "enum",
                     "enum_values": ["14-inch", "16-inch"], "one_of_values": []},
                    {"name": "ram", "column_type": "INTEGER", "business_type": "whole_number",
                     "enum_values": [], "one_of_values": []},
                    {"name": "color", "column_type": "TEXT", "business_type": "text",
                     "enum_values": [], "one_of_values": []},
                ],
                "dependent_values": [
                    {"when": 'size == "14-inch"', "field": "ram", "constraint": "one_of", "values": [8, 16, 24]},
                    {"when": 'size == "16-inch"', "field": "ram", "constraint": "one_of", "values": [16, 32, 48]},
                    {"when": 'size == "16-inch"', "field": "color", "constraint": "default", "value": "space gray"},
                ],
                "audit": "actions",
            }],
            "access_grants": [
                {"content": "laptop_orders", "scope": "orders.read", "verbs": ["VIEW"]},
                {"content": "laptop_orders", "scope": "orders.write", "verbs": ["CREATE", "UPDATE"]},
            ],
            "state_machines": [],
            "events": [],
            "routes": [
                {"method": "GET", "path": "/api/v1/laptop_orders", "kind": "LIST",
                 "content_ref": "laptop_orders", "required_scope": "orders.read"},
                {"method": "POST", "path": "/api/v1/laptop_orders", "kind": "CREATE",
                 "content_ref": "laptop_orders", "required_scope": "orders.write"},
                {"method": "PUT", "path": "/api/v1/laptop_orders/{id}", "kind": "UPDATE",
                 "content_ref": "laptop_orders", "required_scope": "orders.write", "lookup_column": "id"},
            ],
            "pages": [],
            "nav_items": [],
            "streams": [],
            "computes": [],
            "channels": [],
            "boundaries": [],
            "error_handlers": [],
            "reclassification_points": [],
        })

    def test_create_valid_14inch_ram(self):
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        app = create_termin_app(self._build_ir(), strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "clerk")
            r = client.post("/api/v1/laptop_orders", json={"size": "14-inch", "ram": 8})
            assert r.status_code == 201, r.text

    def test_create_invalid_14inch_ram(self):
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        app = create_termin_app(self._build_ir(), strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "clerk")
            r = client.post("/api/v1/laptop_orders", json={"size": "14-inch", "ram": 48})
            assert r.status_code == 422, f"Expected 422 for invalid ram, got {r.status_code}: {r.text}"
            assert "must be one of" in r.json()["detail"].lower()

    def test_create_valid_16inch_ram(self):
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        app = create_termin_app(self._build_ir(), strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "clerk")
            r = client.post("/api/v1/laptop_orders", json={"size": "16-inch", "ram": 32})
            assert r.status_code == 201, r.text

    def test_create_default_color_applied(self):
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        app = create_termin_app(self._build_ir(), strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "clerk")
            r = client.post("/api/v1/laptop_orders", json={"size": "16-inch", "ram": 16})
            assert r.status_code == 201, r.text
            assert r.json()["color"] == "space gray"

    def test_create_no_constraint_when_condition_unmet(self):
        """When condition is false, constraint should not apply."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        app = create_termin_app(self._build_ir(), strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "clerk")
            # 14-inch with ram=48 should fail, but 16-inch with ram=48 should pass
            r = client.post("/api/v1/laptop_orders", json={"size": "16-inch", "ram": 48})
            assert r.status_code == 201, r.text


# ============================================================
# Block C: Boundary Enforcement
# ============================================================

def _block_c_ir(boundaries=None, computes=None):
    """Build a minimal IR for boundary enforcement testing."""
    return json.dumps({
        "ir_version": "0.8.0",
        "reflection_enabled": False,
        "app_id": "block-c-test",
        "name": "Block C Test",
        "description": "",
        "auth": {
            "provider": "stub",
            "scopes": ["admin"],
            "roles": [{"name": "admin", "scopes": ["admin"]}],
        },
        "content": [
            {
                "name": {"display": "orders", "snake": "orders", "pascal": "Orders"},
                "singular": "order",
                "fields": [
                    {"name": "title", "column_type": "TEXT", "business_type": "text",
                     "enum_values": [], "one_of_values": []},
                ],
                "audit": "actions",
            },
            {
                "name": {"display": "invoices", "snake": "invoices", "pascal": "Invoices"},
                "singular": "invoice",
                "fields": [
                    {"name": "amount", "column_type": "REAL", "business_type": "currency",
                     "enum_values": [], "one_of_values": []},
                ],
                "audit": "actions",
            },
            {
                "name": {"display": "logs", "snake": "logs", "pascal": "Logs"},
                "singular": "log",
                "fields": [
                    {"name": "message", "column_type": "TEXT", "business_type": "text",
                     "enum_values": [], "one_of_values": []},
                ],
                "audit": "actions",
            },
        ],
        "access_grants": [
            {"content": "orders", "scope": "admin", "verbs": ["VIEW", "CREATE"]},
            {"content": "invoices", "scope": "admin", "verbs": ["VIEW", "CREATE"]},
            {"content": "logs", "scope": "admin", "verbs": ["VIEW", "CREATE"]},
        ],
        "state_machines": [],
        "events": [],
        "routes": [],
        "pages": [],
        "nav_items": [],
        "streams": [],
        "computes": computes or [],
        "channels": [],
        "boundaries": boundaries or [],
        "error_handlers": [],
        "reclassification_points": [],
    })


class TestBoundaryEnforcementMap:
    """Block C: Verify boundary containment map is built correctly."""

    def test_content_in_same_boundary_allowed(self):
        """Compute accessing content in the same boundary should succeed."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        ir = _block_c_ir(
            boundaries=[{
                "name": {"display": "sales", "snake": "sales", "pascal": "Sales"},
                "contains_content": ["orders", "invoices"],
                "contains_boundaries": [],
                "identity_mode": "inherit",
                "identity_scopes": [],
                "properties": [],
            }],
            computes=[{
                "name": {"display": "order total", "snake": "order_total", "pascal": "OrderTotal"},
                "shape": "TRANSFORM",
                "input_content": ["orders"],
                "output_content": [],
                "body_lines": ["42"],
                "required_scope": "admin",
                "required_role": None,
                "input_params": [],
                "output_params": [],
                "client_safe": False,
                "identity_mode": "delegate",
                "required_confidentiality_scopes": [],
                "output_confidentiality_scope": None,
                "field_dependencies": [],
                "provider": None,
                "preconditions": [],
                "postconditions": [],
                "directive": None,
                "objective": None,
                "strategy": None,
                "trigger": None,
                "trigger_where": None,
                "accesses": ["orders"],
                "input_fields": [],
                "output_fields": [],
                "output_creates": None,
            }],
        )
        app = create_termin_app(ir, strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "admin")
            r = client.post("/api/v1/compute/order_total", json={"input": {}})
            assert r.status_code != 403, f"Same-boundary access should be allowed: {r.text}"

    def test_cross_boundary_rejected(self):
        """Compute accessing content in a different boundary should get 403."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        ir = _block_c_ir(
            boundaries=[
                {
                    "name": {"display": "sales", "snake": "sales", "pascal": "Sales"},
                    "contains_content": ["orders"],
                    "contains_boundaries": [],
                    "identity_mode": "inherit",
                    "identity_scopes": [],
                    "properties": [],
                },
                {
                    "name": {"display": "finance", "snake": "finance", "pascal": "Finance"},
                    "contains_content": ["invoices"],
                    "contains_boundaries": [],
                    "identity_mode": "inherit",
                    "identity_scopes": [],
                    "properties": [],
                },
            ],
            computes=[{
                "name": {"display": "cross boundary", "snake": "cross_boundary", "pascal": "CrossBoundary"},
                "shape": "TRANSFORM",
                "input_content": [],
                "output_content": [],
                "body_lines": ["42"],
                "required_scope": "admin",
                "required_role": None,
                "input_params": [],
                "output_params": [],
                "client_safe": False,
                "identity_mode": "delegate",
                "required_confidentiality_scopes": [],
                "output_confidentiality_scope": None,
                "field_dependencies": [],
                "provider": None,
                "preconditions": [],
                "postconditions": [],
                "directive": None,
                "objective": None,
                "strategy": None,
                "trigger": None,
                "trigger_where": None,
                "accesses": ["orders", "invoices"],
                "input_fields": [],
                "output_fields": [],
                "output_creates": None,
            }],
        )
        app = create_termin_app(ir, strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "admin")
            r = client.post("/api/v1/compute/cross_boundary", json={"input": {}})
            assert r.status_code == 403, f"Cross-boundary access should be rejected: {r.text}"
            assert "cross-boundary" in r.json()["detail"].lower()

    def test_content_outside_subboundary_rejected_from_subboundary(self):
        """Content not in any sub-boundary lives in the app boundary.
        A Compute in a sub-boundary cannot reach app-level content without a channel."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        ir = _block_c_ir(
            boundaries=[{
                "name": {"display": "sales", "snake": "sales", "pascal": "Sales"},
                "contains_content": ["orders"],
                "contains_boundaries": [],
                "identity_mode": "inherit",
                "identity_scopes": [],
                "properties": [],
            }],
            computes=[{
                "name": {"display": "log writer", "snake": "log_writer", "pascal": "LogWriter"},
                "shape": "TRANSFORM",
                "input_content": [],
                "output_content": [],
                "body_lines": ["42"],
                "required_scope": "admin",
                "required_role": None,
                "input_params": [],
                "output_params": [],
                "client_safe": False,
                "identity_mode": "delegate",
                "required_confidentiality_scopes": [],
                "output_confidentiality_scope": None,
                "field_dependencies": [],
                "provider": None,
                "preconditions": [],
                "postconditions": [],
                "directive": None,
                "objective": None,
                "strategy": None,
                "trigger": None,
                "trigger_where": None,
                "accesses": ["orders", "logs"],
                "input_fields": [],
                "output_fields": [],
                "output_creates": None,
            }],
        )
        app = create_termin_app(ir, strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "admin")
            # "logs" is NOT in the "sales" boundary — it's in the implicit app boundary.
            # Compute is in "sales" (via its access to "orders"), so accessing "logs"
            # is a cross-boundary access and should be rejected.
            r = client.post("/api/v1/compute/log_writer", json={"input": {}})
            assert r.status_code == 403, f"Cross-boundary access to app-level content should be rejected: {r.text}"
            assert "cross-boundary" in r.json()["detail"].lower()

    def test_no_boundaries_all_content_in_app_boundary(self):
        """App with no explicit boundaries: all content is in the implicit app boundary.
        All Computes are also in the app boundary. Same boundary → allowed."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        ir = _block_c_ir(
            boundaries=[],
            computes=[{
                "name": {"display": "free compute", "snake": "free_compute", "pascal": "FreeCompute"},
                "shape": "TRANSFORM",
                "input_content": ["orders"],
                "output_content": ["invoices"],
                "body_lines": ["42"],
                "required_scope": "admin",
                "required_role": None,
                "input_params": [],
                "output_params": [],
                "client_safe": False,
                "identity_mode": "delegate",
                "required_confidentiality_scopes": [],
                "output_confidentiality_scope": None,
                "field_dependencies": [],
                "provider": None,
                "preconditions": [],
                "postconditions": [],
                "directive": None,
                "objective": None,
                "strategy": None,
                "trigger": None,
                "trigger_where": None,
                "accesses": ["orders", "invoices"],
                "input_fields": [],
                "output_fields": [],
                "output_creates": None,
            }],
        )
        app = create_termin_app(ir, strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "admin")
            # No explicit boundaries → implicit app boundary contains everything → same boundary → allowed
            r = client.post("/api/v1/compute/free_compute", json={"input": {}})
            assert r.status_code != 403, f"Same app boundary should allow access: {r.text}"

    def test_app_level_compute_accesses_app_level_content(self):
        """A Compute not in any sub-boundary (app-level) can access app-level content."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        ir = _block_c_ir(
            boundaries=[{
                "name": {"display": "sales", "snake": "sales", "pascal": "Sales"},
                "contains_content": ["orders"],
                "contains_boundaries": [],
                "identity_mode": "inherit",
                "identity_scopes": [],
                "properties": [],
            }],
            computes=[{
                "name": {"display": "log reader", "snake": "log_reader", "pascal": "LogReader"},
                "shape": "TRANSFORM",
                "input_content": [],
                "output_content": [],
                "body_lines": ["42"],
                "required_scope": "admin",
                "required_role": None,
                "input_params": [],
                "output_params": [],
                "client_safe": False,
                "identity_mode": "delegate",
                "required_confidentiality_scopes": [],
                "output_confidentiality_scope": None,
                "field_dependencies": [],
                "provider": None,
                "preconditions": [],
                "postconditions": [],
                "directive": None,
                "objective": None,
                "strategy": None,
                "trigger": None,
                "trigger_where": None,
                "accesses": ["logs"],
                "input_fields": [],
                "output_fields": [],
                "output_creates": None,
            }],
        )
        app = create_termin_app(ir, strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "admin")
            # "logs" is not in any sub-boundary → app boundary.
            # Compute only accesses "logs" → also app boundary.
            # Same boundary → allowed.
            r = client.post("/api/v1/compute/log_reader", json={"input": {}})
            assert r.status_code != 403, f"App-level Compute accessing app-level content should be allowed: {r.text}"

    def test_app_level_compute_cannot_reach_into_subboundary(self):
        """A Compute at app level cannot access content inside a sub-boundary without a channel."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        ir = _block_c_ir(
            boundaries=[{
                "name": {"display": "sales", "snake": "sales", "pascal": "Sales"},
                "contains_content": ["orders"],
                "contains_boundaries": [],
                "identity_mode": "inherit",
                "identity_scopes": [],
                "properties": [],
            }],
            computes=[{
                "name": {"display": "order peeker", "snake": "order_peeker", "pascal": "OrderPeeker"},
                "shape": "TRANSFORM",
                "input_content": [],
                "output_content": [],
                "body_lines": ["42"],
                "required_scope": "admin",
                "required_role": None,
                "input_params": [],
                "output_params": [],
                "client_safe": False,
                "identity_mode": "delegate",
                "required_confidentiality_scopes": [],
                "output_confidentiality_scope": None,
                "field_dependencies": [],
                "provider": None,
                "preconditions": [],
                "postconditions": [],
                "directive": None,
                "objective": None,
                "strategy": None,
                "trigger": None,
                "trigger_where": None,
                "accesses": ["logs", "orders"],
                "input_fields": [],
                "output_fields": [],
                "output_creates": None,
            }],
        )
        app = create_termin_app(ir, strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "admin")
            # Compute accesses "logs" (app boundary) and "orders" (sales boundary).
            # First match puts Compute in app boundary (logs).
            # Accessing "orders" (sales boundary) is cross-boundary → rejected.
            r = client.post("/api/v1/compute/order_peeker", json={"input": {}})
            assert r.status_code == 403, f"App-level Compute reaching into sub-boundary should be rejected: {r.text}"
            assert "cross-boundary" in r.json()["detail"].lower()
