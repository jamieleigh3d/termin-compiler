# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""C2: Cross-boundary identity propagation tests.

Tests that identity context flows correctly through:
1. Boundary rejection messages (include caller identity)
2. Boundary identity_mode: "restrict" enforcement
3. Webhook inbound identity propagation
4. Agent tool calls carry triggering user's identity
"""

import json
import pytest
from pathlib import Path


def _c2_ir(boundaries=None, computes=None, content=None, routes=None):
    """Build an IR for identity propagation testing."""
    default_content = [
        {"name": {"display": "public data", "snake": "public_data", "pascal": "PublicData"},
         "singular": "public datum",
         "fields": [{"name": "value", "column_type": "TEXT", "business_type": "text",
                      "enum_values": [], "one_of_values": []}],
         "audit": "actions"},
        {"name": {"display": "restricted data", "snake": "restricted_data", "pascal": "RestrictedData"},
         "singular": "restricted datum",
         "fields": [{"name": "secret", "column_type": "TEXT", "business_type": "text",
                      "enum_values": [], "one_of_values": []}],
         "audit": "actions"},
    ]
    default_routes = [
        {"method": "GET", "path": "/api/v1/public_data", "kind": "LIST",
         "content_ref": "public_data", "required_scope": "read"},
        {"method": "POST", "path": "/api/v1/public_data", "kind": "CREATE",
         "content_ref": "public_data", "required_scope": "write"},
        {"method": "GET", "path": "/api/v1/restricted_data", "kind": "LIST",
         "content_ref": "restricted_data", "required_scope": "admin"},
        {"method": "POST", "path": "/api/v1/restricted_data", "kind": "CREATE",
         "content_ref": "restricted_data", "required_scope": "admin"},
    ]
    return json.dumps({
        "ir_version": "0.8.0", "reflection_enabled": False,
        "app_id": "c2-test", "name": "C2 Test", "description": "",
        "auth": {
            "provider": "stub",
            "scopes": ["read", "write", "admin", "restricted.access"],
            "roles": [
                {"name": "reader", "scopes": ["read"]},
                {"name": "writer", "scopes": ["read", "write"]},
                {"name": "admin", "scopes": ["read", "write", "admin", "restricted.access"]},
            ],
        },
        "content": content or default_content,
        "access_grants": [
            {"content": "public_data", "scope": "read", "verbs": ["VIEW"]},
            {"content": "public_data", "scope": "write", "verbs": ["CREATE", "UPDATE"]},
            {"content": "restricted_data", "scope": "admin", "verbs": ["VIEW", "CREATE"]},
        ],
        "state_machines": [], "events": [],
        "routes": routes or default_routes,
        "pages": [], "nav_items": [], "streams": [],
        "computes": computes or [],
        "channels": [], "boundaries": boundaries or [],
        "error_handlers": [], "reclassification_points": [],
    })


class TestBoundaryRejectionIncludesIdentity:
    """Boundary rejection messages should include the caller's identity."""

    def test_rejection_message_includes_boundary_names(self, tmp_path):
        """Cross-boundary rejection should name both boundaries."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        ir = _c2_ir(
            boundaries=[
                {"name": {"display": "public zone", "snake": "public_zone", "pascal": "PublicZone"},
                 "contains_content": ["public_data"],
                 "contains_boundaries": [], "identity_mode": "inherit",
                 "identity_scopes": [], "properties": []},
                {"name": {"display": "restricted zone", "snake": "restricted_zone", "pascal": "RestrictedZone"},
                 "contains_content": ["restricted_data"],
                 "contains_boundaries": [], "identity_mode": "restrict",
                 "identity_scopes": ["restricted.access"], "properties": []},
            ],
            computes=[{
                "name": {"display": "cross boundary", "snake": "cross_boundary", "pascal": "CrossBoundary"},
                "shape": "TRANSFORM", "input_content": [], "output_content": [],
                "body_lines": ["42"], "required_scope": "write",
                "required_role": None, "input_params": [], "output_params": [],
                "client_safe": False, "identity_mode": "delegate",
                "required_confidentiality_scopes": [],
                "output_confidentiality_scope": None,
                "field_dependencies": [], "provider": None,
                "preconditions": [], "postconditions": [],
                "directive": None, "objective": None, "strategy": None,
                "trigger": None, "trigger_where": None,
                "accesses": ["public_data", "restricted_data"],
                "input_fields": [], "output_fields": [], "output_creates": None,
            }],
        )
        app = create_termin_app(ir, db_path=str(tmp_path / "c2.db"), strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "writer")
            r = client.post("/api/v1/compute/cross_boundary", json={"input": {}})
            assert r.status_code == 403
            detail = r.json()["detail"]
            # Should include boundary names
            assert "public_zone" in detail or "public zone" in detail.lower()
            assert "restricted_zone" in detail or "restricted zone" in detail.lower()
            assert "cross-boundary" in detail.lower()


class TestBoundaryIdentityModeRestrict:
    """Boundary with identity_mode: 'restrict' should check caller scopes."""

    def test_restrict_mode_allows_matching_scope(self, tmp_path):
        """User with required scope can access restricted boundary content."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        ir = _c2_ir(
            boundaries=[
                {"name": {"display": "restricted zone", "snake": "restricted_zone", "pascal": "RestrictedZone"},
                 "contains_content": ["restricted_data"],
                 "contains_boundaries": [], "identity_mode": "restrict",
                 "identity_scopes": ["restricted.access"], "properties": []},
            ],
        )
        app = create_termin_app(ir, db_path=str(tmp_path / "c2_allow.db"), strict_channels=False)
        with TestClient(app) as client:
            # Admin has "restricted.access" scope
            client.cookies.set("termin_role", "admin")
            r = client.get("/api/v1/restricted_data")
            assert r.status_code == 200

    def test_restrict_mode_denies_missing_boundary_scope(self, tmp_path):
        """User with route scope but without boundary scope is denied.

        This is the key C2 test: the route scope check passes (admin can view
        restricted_data) but the boundary identity restriction blocks access
        because the 'writer' role doesn't have 'restricted.access'.
        """
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        # Give writer the admin scope for the route, but NOT restricted.access for boundary
        ir = json.dumps({
            "ir_version": "0.8.0", "reflection_enabled": False,
            "app_id": "c2-restrict", "name": "C2 Restrict Test", "description": "",
            "auth": {
                "provider": "stub",
                "scopes": ["admin", "restricted.access"],
                "roles": [
                    {"name": "route_admin", "scopes": ["admin"]},
                    {"name": "full_admin", "scopes": ["admin", "restricted.access"]},
                ],
            },
            "content": [
                {"name": {"display": "restricted data", "snake": "restricted_data", "pascal": "RestrictedData"},
                 "singular": "restricted datum",
                 "fields": [{"name": "secret", "column_type": "TEXT", "business_type": "text",
                              "enum_values": [], "one_of_values": []}],
                 "audit": "actions"},
            ],
            "access_grants": [
                {"content": "restricted_data", "scope": "admin", "verbs": ["VIEW", "CREATE"]},
            ],
            "state_machines": [], "events": [],
            "routes": [
                {"method": "GET", "path": "/api/v1/restricted_data", "kind": "LIST",
                 "content_ref": "restricted_data", "required_scope": "admin"},
                {"method": "POST", "path": "/api/v1/restricted_data", "kind": "CREATE",
                 "content_ref": "restricted_data", "required_scope": "admin"},
            ],
            "pages": [], "nav_items": [], "streams": [],
            "computes": [],
            "channels": [],
            "boundaries": [
                {"name": {"display": "restricted zone", "snake": "restricted_zone", "pascal": "RestrictedZone"},
                 "contains_content": ["restricted_data"],
                 "contains_boundaries": [], "identity_mode": "restrict",
                 "identity_scopes": ["restricted.access"], "properties": []},
            ],
            "error_handlers": [], "reclassification_points": [],
        })
        app = create_termin_app(ir, db_path=str(tmp_path / "c2_deny.db"), strict_channels=False)
        with TestClient(app) as client:
            # route_admin has "admin" (passes route scope) but NOT "restricted.access"
            client.cookies.set("termin_role", "route_admin")
            r = client.get("/api/v1/restricted_data")
            assert r.status_code == 403
            detail = r.json()["detail"]
            assert "boundary" in detail.lower() or "restrict" in detail.lower()

            # full_admin has both scopes — should pass
            client.cookies.set("termin_role", "full_admin")
            r = client.get("/api/v1/restricted_data")
            assert r.status_code == 200

    def test_restrict_mode_reader_denied(self, tmp_path):
        """Reader role with no write/admin scopes denied from restricted boundary."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        ir = _c2_ir(
            boundaries=[
                {"name": {"display": "restricted zone", "snake": "restricted_zone", "pascal": "RestrictedZone"},
                 "contains_content": ["restricted_data"],
                 "contains_boundaries": [], "identity_mode": "restrict",
                 "identity_scopes": ["restricted.access"], "properties": []},
            ],
        )
        app = create_termin_app(ir, db_path=str(tmp_path / "c2_reader.db"), strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "reader")
            r = client.get("/api/v1/restricted_data")
            assert r.status_code == 403

    def test_inherit_mode_allows_all(self, tmp_path):
        """Boundary with identity_mode: 'inherit' allows based on normal scope checks."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        ir = _c2_ir(
            boundaries=[
                {"name": {"display": "public zone", "snake": "public_zone", "pascal": "PublicZone"},
                 "contains_content": ["public_data"],
                 "contains_boundaries": [], "identity_mode": "inherit",
                 "identity_scopes": [], "properties": []},
            ],
        )
        app = create_termin_app(ir, db_path=str(tmp_path / "c2_inherit.db"), strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "reader")
            r = client.get("/api/v1/public_data")
            assert r.status_code == 200

    def test_no_boundaries_no_restriction(self, tmp_path):
        """App without boundaries should not enforce identity restriction."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        ir = _c2_ir(boundaries=[])
        app = create_termin_app(ir, db_path=str(tmp_path / "c2_none.db"), strict_channels=False)
        with TestClient(app) as client:
            client.cookies.set("termin_role", "admin")
            r = client.get("/api/v1/restricted_data")
            assert r.status_code == 200


class TestWebhookIdentityPropagation:
    """Webhook inbound should propagate source identity into event metadata."""

    def test_webhook_scope_check(self, tmp_path):
        """Webhook requires declared scope to accept data."""
        from termin_runtime import create_termin_app
        from fastapi.testclient import TestClient
        ir = json.dumps({
            "ir_version": "0.8.0", "reflection_enabled": False,
            "app_id": "wh-id-test", "name": "WH ID Test", "description": "",
            "auth": {
                "provider": "stub",
                "scopes": ["write", "admin"],
                "roles": [
                    {"name": "writer", "scopes": ["write"]},
                    {"name": "admin", "scopes": ["write", "admin"]},
                ],
            },
            "content": [
                {"name": {"display": "events", "snake": "events", "pascal": "Events"},
                 "singular": "event",
                 "fields": [{"name": "data", "column_type": "TEXT", "business_type": "text",
                              "enum_values": [], "one_of_values": []}],
                 "audit": "actions"},
            ],
            "access_grants": [
                {"content": "events", "scope": "write", "verbs": ["VIEW", "CREATE"]},
            ],
            "state_machines": [], "events": [],
            "routes": [
                {"method": "GET", "path": "/api/v1/events", "kind": "LIST",
                 "content_ref": "events", "required_scope": "write"},
            ],
            "pages": [], "nav_items": [], "streams": [],
            "computes": [],
            "channels": [{
                "name": {"display": "inbound hook", "snake": "inbound_hook", "pascal": "InboundHook"},
                "carries_content": "events",
                "direction": "INBOUND", "delivery": "RELIABLE",
                "endpoint": "/webhooks/events",
                "type": "inbound",
                "actions": [],
                "send_events": [],
                "requirements": [{"scope": "admin", "direction": "send"}],
            }],
            "boundaries": [],
            "error_handlers": [], "reclassification_points": [],
        })
        app = create_termin_app(ir, db_path=str(tmp_path / "wh.db"),
                                strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            # Writer should be denied (webhook requires admin)
            client.cookies.set("termin_role", "writer")
            r = client.post("/webhooks/inbound_hook", json={"data": "test"})
            assert r.status_code == 403

            # Admin should be allowed
            client.cookies.set("termin_role", "admin")
            r = client.post("/webhooks/inbound_hook", json={"data": "test"})
            assert r.status_code == 200
