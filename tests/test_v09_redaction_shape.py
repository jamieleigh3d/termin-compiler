# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5a.4: field-level redaction shape.

Per BRD #2 §7.6: when a single field on an otherwise-visible record
is restricted from the current principal, the runtime replaces the
value with a typed redaction sentinel before the record reaches the
provider. The shape carries `field`, `expected_type`, and an optional
`reason` so providers can render appropriate placeholders.

Back-compat: the legacy `__redacted: True, scope: <scope>` shape is
preserved as a strict superset — old consumers keep working.
"""

from __future__ import annotations

import pytest

from termin_core.confidentiality.redaction import redact_record, is_redacted


def _content_ir(field_specs: list[dict]) -> dict:
    """Build a minimal content IR dict for testing."""
    return {
        "name": {"snake": "items", "display": "items"},
        "fields": field_specs,
        "confidentiality_scopes": [],
    }


def test_unredacted_field_passes_through():
    content = _content_ir([
        {"name": "name", "business_type": "text",
         "confidentiality_scopes": []},
    ])
    out = redact_record({"id": 1, "name": "alice"}, content, {"x.read"})
    assert out["name"] == "alice"


def test_redacted_field_carries_brd_aligned_shape():
    """Per BRD #2 §7.6, the marker carries field + expected_type."""
    content = _content_ir([
        {"name": "salary", "business_type": "currency",
         "confidentiality_scopes": ["hr.access"]},
    ])
    out = redact_record({"id": 1, "salary": 95000}, content, set())
    marker = out["salary"]
    assert isinstance(marker, dict)
    assert marker["__redacted"] is True
    assert marker["field"] == "salary"
    assert marker["expected_type"] == "currency"


def test_redacted_marker_carries_reason():
    content = _content_ir([
        {"name": "ssn", "business_type": "text",
         "confidentiality_scopes": ["pii.access"]},
    ])
    out = redact_record({"id": 1, "ssn": "123-45-6789"}, content, set())
    marker = out["ssn"]
    assert "reason" in marker
    assert "pii.access" in marker["reason"]


def test_redacted_marker_keeps_legacy_scope_key():
    """Back-compat with pre-v0.9 consumers checking the 'scope' key."""
    content = _content_ir([
        {"name": "salary", "business_type": "currency",
         "confidentiality_scopes": ["hr.access"]},
    ])
    out = redact_record({"id": 1, "salary": 95000}, content, set())
    marker = out["salary"]
    assert marker["scope"] == "hr.access"


def test_redacted_marker_distinguishable_from_natural_zero():
    """Numeric fields can hold 0; the marker must not look like 0."""
    content = _content_ir([
        {"name": "balance", "business_type": "number",
         "confidentiality_scopes": ["finance.access"]},
    ])
    out = redact_record({"id": 1, "balance": 0}, content, set())
    assert out["balance"] != 0
    assert is_redacted(out["balance"])


def test_redacted_marker_distinguishable_from_empty_string():
    content = _content_ir([
        {"name": "note", "business_type": "text",
         "confidentiality_scopes": ["x"]},
    ])
    out = redact_record({"id": 1, "note": ""}, content, set())
    assert out["note"] != ""
    assert is_redacted(out["note"])


def test_redacted_marker_distinguishable_from_false():
    content = _content_ir([
        {"name": "active", "business_type": "boolean",
         "confidentiality_scopes": ["x"]},
    ])
    out = redact_record({"id": 1, "active": False}, content, set())
    assert out["active"] is not False
    assert is_redacted(out["active"])


def test_caller_with_required_scope_sees_unredacted():
    content = _content_ir([
        {"name": "salary", "business_type": "currency",
         "confidentiality_scopes": ["hr.access"]},
    ])
    out = redact_record({"id": 1, "salary": 95000}, content, {"hr.access"})
    assert out["salary"] == 95000


def test_field_without_confidentiality_scopes_never_redacts():
    content = _content_ir([
        {"name": "name", "business_type": "text",
         "confidentiality_scopes": []},
    ])
    out = redact_record({"id": 1, "name": "Alice"}, content, set())
    assert out["name"] == "Alice"


def test_system_id_field_passes_through_when_unscoped():
    """Fields not in the schema (like auto-generated id) always pass."""
    content = _content_ir([
        {"name": "name", "business_type": "text",
         "confidentiality_scopes": []},
    ])
    out = redact_record({"id": 42, "name": "x"}, content, set())
    assert out["id"] == 42


def test_expected_type_matches_field_business_type():
    """Each redacted field carries its declared business_type so the
    presentation provider can render an appropriate placeholder
    (a redacted currency cell looks different from a redacted boolean
    cell per BRD §7.6)."""
    content = _content_ir([
        {"name": "f_text", "business_type": "text",
         "confidentiality_scopes": ["x"]},
        {"name": "f_currency", "business_type": "currency",
         "confidentiality_scopes": ["x"]},
        {"name": "f_principal", "business_type": "principal",
         "confidentiality_scopes": ["x"]},
    ])
    out = redact_record(
        {"id": 1, "f_text": "x", "f_currency": 100, "f_principal": "u-1"},
        content, set(),
    )
    assert out["f_text"]["expected_type"] == "text"
    assert out["f_currency"]["expected_type"] == "currency"
    assert out["f_principal"]["expected_type"] == "principal"
