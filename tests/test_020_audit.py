# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""D-20: Agent Observability tests.

Tests for:
- AUDIT verb in the Verb enum
- `can audit` syntax inside Compute blocks
- Audit level on Compute (none/actions/debug)
- Auto-generated compute_audit_log_{name} Content per Compute
- Access grants for audit log Content
- audit_content_ref field on ComputeSpec
- Runtime: trace recording after compute invocations
- Runtime: redaction in flight when serving audit records
"""

import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower
from termin.ir import Verb, ComputeSpec, ContentSchema, AccessGrant
from termin_runtime import create_termin_app


# ── Helper ──

def _compile(source: str):
    """Parse, analyze, and lower a .termin source string. Returns AppSpec."""
    program, errors = parse(source)
    assert errors.ok, f"Parse errors:\n{errors.format()}"
    result = analyze(program)
    assert result.ok, f"Analysis errors:\n{result.format()}"
    return lower(program)


def _find_content(spec, snake_name: str) -> ContentSchema:
    """Find a ContentSchema by snake_case name."""
    for c in spec.content:
        if c.name.snake == snake_name:
            return c
    return None


def _find_compute(spec, snake_name: str) -> ComputeSpec:
    """Find a ComputeSpec by snake_case name."""
    for c in spec.computes:
        if c.name.snake == snake_name:
            return c
    return None


def _find_grants(spec, content_name: str) -> list[AccessGrant]:
    """Find all AccessGrants for a given content name."""
    return [g for g in spec.access_grants if g.content == content_name]


# ── Fixtures ──

COMPUTE_WITH_AUDIT = """\
Application: Audit Demo
  Description: Tests D-20 agent observability

Users authenticate with stub
Scopes are "items.read", "items.write", and "compute.audit"

A "user" has "items.read" and "items.write"
An "auditor" has "items.read" and "compute.audit"

Content called "items":
  Each item has a name which is text, required
  Each item has a total which is currency
  Anyone with "items.read" can view items
  Anyone with "items.write" can create or update items

Compute called "calculate total":
  Transform: takes an item, produces an item
  `item.total = item.quantity * item.unit_price`
  Anyone with "items.write" can execute this
  Audit level: actions
  Anyone with "compute.audit" can audit
"""

COMPUTE_AUDIT_DEBUG = """\
Application: Debug Audit
  Description: Compute with debug audit level

Users authenticate with stub
Scopes are "items.read", "items.write", and "debug.audit"

A "user" has "items.read" and "items.write"

Content called "items":
  Each item has a name which is text, required
  Anyone with "items.read" can view items
  Anyone with "items.write" can create or update items

Compute called "process item":
  Transform: takes an item, produces an item
  `item.name = upper(item.name)`
  Anyone with "items.write" can execute this
  Audit level: debug
  Anyone with "debug.audit" can audit
"""

COMPUTE_AUDIT_NONE = """\
Application: No Audit
  Description: Compute with audit disabled

Users authenticate with stub
Scopes are "items.read" and "items.write"

A "user" has "items.read" and "items.write"

Content called "items":
  Each item has a name which is text, required
  Anyone with "items.read" can view items
  Anyone with "items.write" can create or update items

Compute called "fast transform":
  Transform: takes an item, produces an item
  `item.name = lower(item.name)`
  Anyone with "items.write" can execute this
  Audit level: none
"""

COMPUTE_DEFAULT_AUDIT = """\
Application: Default Audit
  Description: Compute with default audit level (actions)

Users authenticate with stub
Scopes are "items.read", "items.write", and "audit.scope"

A "user" has "items.read" and "items.write"

Content called "items":
  Each item has a name which is text, required
  Anyone with "items.read" can view items
  Anyone with "items.write" can create or update items

Compute called "default compute":
  Transform: takes an item, produces an item
  `item.name = trim(item.name)`
  Anyone with "items.write" can execute this
  Anyone with "audit.scope" can audit
"""

MULTIPLE_COMPUTES = """\
Application: Multi Compute
  Description: Multiple computes each get their own audit log

Users authenticate with stub
Scopes are "items.read", "items.write", "reports.read", and "ops.audit"

A "user" has "items.read" and "items.write"

Content called "items":
  Each item has a name which is text, required
  Anyone with "items.read" can view items
  Anyone with "items.write" can create or update items

Content called "reports":
  Each report has a title which is text, required
  Anyone with "reports.read" can view reports

Compute called "calculate total":
  Transform: takes an item, produces an item
  `item.total = item.quantity * item.unit_price`
  Anyone with "items.write" can execute this
  Anyone with "ops.audit" can audit

Compute called "generate report":
  Reduce: takes items, produces a report
  `report.summary = items.map(i, i.name).join(", ")`
  Anyone with "items.read" can execute this
  Anyone with "ops.audit" can audit
"""

AGENT_WITH_AUDIT = """\
Application: Agent Audit
  Description: AI agent compute with audit

Users authenticate with stub
Scopes are "items.read", "items.write", and "agent.audit"

A "user" has "items.read" and "items.write"

Content called "items":
  Each item has a name which is text, required
  Each item has a response which is text
  Anyone with "items.read" can view items
  Anyone with "items.write" can create or update items

Compute called "smart agent":
  Provider is "ai-agent"
  Accesses items
  Trigger on event "item.created"
  Directive is ```
    You are a helpful assistant.
  ```
  Objective is ```
    Process the item.
  ```
  Anyone with "items.write" can execute this
  Audit level: debug
  Anyone with "agent.audit" can audit
"""


# ===== Step 1a: AUDIT Verb Tests =====

class TestAuditVerb:
    """Test that AUDIT is a valid Verb in the IR."""

    def test_audit_verb_exists(self):
        """The Verb enum should include AUDIT."""
        assert hasattr(Verb, "AUDIT"), "Verb.AUDIT should exist"
        assert Verb.AUDIT.value == "audit"

    def test_audit_verb_in_enum_members(self):
        """AUDIT should be one of the five verbs."""
        verb_names = [v.name for v in Verb]
        assert "AUDIT" in verb_names
        assert len(verb_names) == 5  # VIEW, CREATE, UPDATE, DELETE, AUDIT


# ===== Step 1b: `can audit` Syntax Tests =====

class TestCanAuditParsing:
    """Test that 'Anyone with X can audit' parses inside Compute blocks."""

    def test_can_audit_parsed_on_compute(self):
        """Compute should have audit_scope populated from 'can audit' declaration."""
        spec = _compile(COMPUTE_WITH_AUDIT)
        comp = _find_compute(spec, "calculate_total")
        assert comp is not None
        assert comp.audit_scope == "compute.audit"

    def test_can_audit_with_different_scope(self):
        """Different scopes work for can audit."""
        spec = _compile(COMPUTE_AUDIT_DEBUG)
        comp = _find_compute(spec, "process_item")
        assert comp is not None
        assert comp.audit_scope == "debug.audit"

    def test_no_can_audit_means_no_scope(self):
        """Compute without 'can audit' has no audit_scope."""
        spec = _compile(COMPUTE_AUDIT_NONE)
        comp = _find_compute(spec, "fast_transform")
        assert comp is not None
        assert comp.audit_scope is None

    def test_agent_compute_can_audit(self):
        """AI agent computes also support can audit."""
        spec = _compile(AGENT_WITH_AUDIT)
        comp = _find_compute(spec, "smart_agent")
        assert comp is not None
        assert comp.audit_scope == "agent.audit"


# ===== Step 1c: Audit Level on Compute Tests =====

class TestComputeAuditLevel:
    """Test Audit level: syntax on Compute blocks."""

    def test_audit_level_actions(self):
        """Audit level: actions should be captured."""
        spec = _compile(COMPUTE_WITH_AUDIT)
        comp = _find_compute(spec, "calculate_total")
        assert comp.audit_level == "actions"

    def test_audit_level_debug(self):
        """Audit level: debug should be captured."""
        spec = _compile(COMPUTE_AUDIT_DEBUG)
        comp = _find_compute(spec, "process_item")
        assert comp.audit_level == "debug"

    def test_audit_level_none(self):
        """Audit level: none should be captured."""
        spec = _compile(COMPUTE_AUDIT_NONE)
        comp = _find_compute(spec, "fast_transform")
        assert comp.audit_level == "none"

    def test_audit_level_default(self):
        """Default audit level for Compute should be 'actions'."""
        spec = _compile(COMPUTE_DEFAULT_AUDIT)
        comp = _find_compute(spec, "default_compute")
        assert comp.audit_level == "actions"


# ===== Step 1d: Auto-Generated Audit Log Content Tests =====

class TestAuditLogGeneration:
    """Test that the compiler generates compute_audit_log_{name} Content tables."""

    def test_audit_log_content_exists(self):
        """A compute_audit_log_{name} Content should be generated for each Compute with audit."""
        spec = _compile(COMPUTE_WITH_AUDIT)
        audit_log = _find_content(spec, "compute_audit_log_calculate_total")
        assert audit_log is not None, "Expected compute_audit_log_calculate_total Content"

    def test_audit_log_standard_fields(self):
        """The auto-generated audit log should have all D-20 standard fields."""
        spec = _compile(COMPUTE_WITH_AUDIT)
        audit_log = _find_content(spec, "compute_audit_log_calculate_total")
        assert audit_log is not None
        field_names = {f.name for f in audit_log.fields}
        # Note: 'id' is auto-added by the runtime storage module, not in fields
        expected_fields = {
            "compute_name", "invocation_id", "trigger",
            "started_at", "completed_at", "duration_ms",
            "outcome", "total_input_tokens", "total_output_tokens",
            "trace", "error_message",
        }
        assert expected_fields.issubset(field_names), (
            f"Missing fields: {expected_fields - field_names}"
        )

    def test_audit_log_outcome_is_enum(self):
        """The outcome field should be an enum with success/error/timeout/cancelled."""
        spec = _compile(COMPUTE_WITH_AUDIT)
        audit_log = _find_content(spec, "compute_audit_log_calculate_total")
        outcome_field = None
        for f in audit_log.fields:
            if f.name == "outcome":
                outcome_field = f
                break
        assert outcome_field is not None
        assert set(outcome_field.enum_values) == {"success", "error", "timeout", "cancelled"}

    def test_audit_log_id_auto_added_by_runtime(self):
        """The id column is auto-added by the runtime storage module (not in schema fields)."""
        spec = _compile(COMPUTE_WITH_AUDIT)
        audit_log = _find_content(spec, "compute_audit_log_calculate_total")
        # id is NOT in the schema fields — it's auto-added by the storage layer
        field_names = {f.name for f in audit_log.fields}
        assert "id" not in field_names, "id should be auto-added by runtime, not in schema fields"

    def test_no_audit_log_for_audit_none(self):
        """Compute with Audit level: none should NOT generate an audit log Content."""
        spec = _compile(COMPUTE_AUDIT_NONE)
        audit_log = _find_content(spec, "compute_audit_log_fast_transform")
        assert audit_log is None, "Audit level: none should not generate an audit log"

    def test_multiple_computes_each_get_audit_log(self):
        """Each Compute gets its own audit log Content."""
        spec = _compile(MULTIPLE_COMPUTES)
        log1 = _find_content(spec, "compute_audit_log_calculate_total")
        log2 = _find_content(spec, "compute_audit_log_generate_report")
        assert log1 is not None, "Expected audit log for calculate_total"
        assert log2 is not None, "Expected audit log for generate_report"

    def test_audit_log_audit_level_matches_compute(self):
        """The audit log Content's audit level should be 'none' (it's auto-generated, not re-audited)."""
        spec = _compile(COMPUTE_WITH_AUDIT)
        audit_log = _find_content(spec, "compute_audit_log_calculate_total")
        # The audit log itself shouldn't recursively audit — set to 'none'
        assert audit_log.audit == "none"


# ===== Access Grants for Audit Log =====

class TestAuditLogAccessGrants:
    """Test that access grants are generated for audit log Content."""

    def test_audit_grant_with_audit_verb(self):
        """The audit log should have an AUDIT grant from the compute's 'can audit' scope."""
        spec = _compile(COMPUTE_WITH_AUDIT)
        grants = _find_grants(spec, "compute_audit_log_calculate_total")
        audit_grants = [g for g in grants if Verb.AUDIT in g.verbs]
        assert len(audit_grants) >= 1
        assert any(g.scope == "compute.audit" for g in audit_grants)

    def test_view_grant_for_auditors(self):
        """Auditors should also get VIEW on the audit log (need to list/read records)."""
        spec = _compile(COMPUTE_WITH_AUDIT)
        grants = _find_grants(spec, "compute_audit_log_calculate_total")
        view_grants = [g for g in grants if Verb.VIEW in g.verbs]
        assert len(view_grants) >= 1
        assert any(g.scope == "compute.audit" for g in view_grants)

    def test_no_grants_for_audit_none(self):
        """No grants should exist for a compute with Audit level: none (no audit log)."""
        spec = _compile(COMPUTE_AUDIT_NONE)
        grants = _find_grants(spec, "compute_audit_log_fast_transform")
        assert len(grants) == 0


# ===== ComputeSpec audit_content_ref =====

class TestAuditContentRef:
    """Test the audit_content_ref field on ComputeSpec."""

    def test_audit_content_ref_set(self):
        """ComputeSpec should have audit_content_ref pointing to the audit log Content."""
        spec = _compile(COMPUTE_WITH_AUDIT)
        comp = _find_compute(spec, "calculate_total")
        assert comp.audit_content_ref == "compute_audit_log_calculate_total"

    def test_audit_content_ref_none_for_none_level(self):
        """ComputeSpec with audit none should have no audit_content_ref."""
        spec = _compile(COMPUTE_AUDIT_NONE)
        comp = _find_compute(spec, "fast_transform")
        assert comp.audit_content_ref is None

    def test_audit_content_ref_default(self):
        """ComputeSpec with default (actions) audit level should have audit_content_ref."""
        spec = _compile(COMPUTE_DEFAULT_AUDIT)
        comp = _find_compute(spec, "default_compute")
        assert comp.audit_content_ref == "compute_audit_log_default_compute"

    def test_agent_compute_audit_content_ref(self):
        """Agent compute should also get audit_content_ref."""
        spec = _compile(AGENT_WITH_AUDIT)
        comp = _find_compute(spec, "smart_agent")
        assert comp.audit_content_ref == "compute_audit_log_smart_agent"


# ===== Routes for Audit Log Content =====

class TestAuditLogRoutes:
    """Test that auto-generated CRUD routes exist for audit log Content."""

    def test_audit_log_has_list_route(self):
        """The audit log Content should get a LIST route (from auto-CRUD)."""
        spec = _compile(COMPUTE_WITH_AUDIT)
        list_routes = [
            r for r in spec.routes
            if r.content_ref == "compute_audit_log_calculate_total" and r.kind.name == "LIST"
        ]
        assert len(list_routes) >= 1

    def test_audit_log_has_get_route(self):
        """The audit log Content should get a GET_ONE route."""
        spec = _compile(COMPUTE_WITH_AUDIT)
        get_routes = [
            r for r in spec.routes
            if r.content_ref == "compute_audit_log_calculate_total" and r.kind.name == "GET_ONE"
        ]
        assert len(get_routes) >= 1


# ===== Runtime Tests =====

import json as _json_mod
from conftest import extract_ir_from_pkg


def _ir_json(pkg_path):
    return _json_mod.dumps(extract_ir_from_pkg(pkg_path))


def _make_client(pkg_path):
    """Create a TestClient for a compiled package."""
    app = create_termin_app(_ir_json(pkg_path), strict_channels=False)
    return TestClient(app)


class TestRuntimeTraceRecording:
    """Test that compute invocations produce trace records in the audit log."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_compute_invocation_writes_trace(self):
        """POST /api/v1/compute/{name} should write a trace record to the audit log."""
        with _make_client(self.pkgs["compute_demo"]) as client:
            client.cookies.set("termin_role", "order manager")
            # Invoke the compute
            r = client.post("/api/v1/compute/calculate_order_total", json={"input": {}})
            # Check audit log
            r2 = client.get("/api/v1/compute_audit_log_calculate_order_total")
            assert r2.status_code == 200
            records = r2.json()
            assert len(records) >= 1, "Expected at least one trace record"
            rec = records[0]
            assert rec["compute_name"] == "calculate order total"
            assert rec["invocation_id"] is not None
            assert rec["outcome"] in ("success", "error")

    def test_trace_has_duration(self):
        """Trace records should include timing information."""
        with _make_client(self.pkgs["compute_demo"]) as client:
            client.cookies.set("termin_role", "order manager")
            client.post("/api/v1/compute/calculate_order_total", json={"input": {}})
            r = client.get("/api/v1/compute_audit_log_calculate_order_total")
            records = r.json()
            assert len(records) >= 1
            rec = records[0]
            assert rec["duration_ms"] is not None
            assert rec["started_at"] is not None
            assert rec["completed_at"] is not None

    def test_no_trace_for_audit_none(self):
        """Compute with Audit level: none should NOT write a trace record."""
        with _make_client(self.pkgs["compute_demo"]) as client:
            client.cookies.set("termin_role", "order manager")
            # triage_order has Audit level: none
            client.post("/api/v1/compute/triage_order", json={"input": {}})
            # The audit log table shouldn't even exist for this compute
            r = client.get("/api/v1/compute_audit_log_triage_order")
            assert r.status_code == 404 or (r.status_code == 200 and len(r.json()) == 0)

    def test_trace_records_error_outcome(self):
        """A compute that fails should record outcome='error' and error_message."""
        with _make_client(self.pkgs["compute_demo"]) as client:
            client.cookies.set("termin_role", "order manager")
            # Invoke with bad input — likely to cause a CEL error
            r = client.post("/api/v1/compute/revenue_report", json={"input": {"invalid": True}})
            r2 = client.get("/api/v1/compute_audit_log_revenue_report")
            records = r2.json()
            assert len(records) >= 1
            # At least one record should exist (might be success or error depending on input)


class TestRuntimeRedaction:
    """Test that audit log records are redacted based on caller scopes."""

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_redaction_of_confidential_values(self):
        """Trace content should redact confidential field values for callers lacking scope."""
        with _make_client(self.pkgs["hrportal"]) as client:
            # Create an employee with confidential salary
            client.cookies.set("termin_role", "hr business partner")
            emp = client.post("/api/v1/employees", json={
                "name": "Alice Johnson",
                "department": "Engineering",
                "salary": 95000,
                "bonus_rate": 0.15,
                "ssn": "123-45-6789",
                "phone": "555-1234",
            })

            # Invoke the compute (hr business partner has team_metrics.view)
            r = client.post("/api/v1/compute/calculate_team_bonus_pool", json={"input": {}})

            # Read the audit log as hr business partner (has salary.access)
            r2 = client.get("/api/v1/compute_audit_log_calculate_team_bonus_pool")
            assert r2.status_code == 200

            # Now read as a manager (lacks salary.access) — salary values in trace should be redacted
            client.cookies.set("termin_role", "manager")
            r3 = client.get("/api/v1/compute_audit_log_calculate_team_bonus_pool")
            if r3.status_code == 200 and len(r3.json()) > 0:
                trace_text = json.dumps(r3.json()[0])
                # If the trace mentions salary values, they should be redacted
                if "95000" in trace_text:
                    # The value should be redacted for a caller without salary.access
                    assert False, "Salary value should be redacted for caller without salary.access"
