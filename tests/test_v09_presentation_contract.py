# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5a.1: presentation contract surface.

Covers:
  - PresentationProvider Protocol shape (one Protocol with discriminator).
  - PRESENTATION_BASE_CONTRACTS = ten contracts.
  - Redacted sentinel + redacted_json_default encoder hook.
  - PrincipalContext shape.
  - register_presentation_base_contracts() registers the ten in a
    ContractRegistry.
  - AppSpec.required_contracts populates from lowering.
  - ComponentNode.contract is set per the type→contract map.

No behavior change: the existing presentation.py keeps driving
rendering. This slice lands the contract layer so 5a.2 has a place
to plug into.
"""

from __future__ import annotations

import json

import pytest

from termin.peg_parser import parse_peg as parse
from termin.lower import lower
from termin_core.ir.types import ComponentNode, AppSpec
from termin_core.providers.presentation_contract import (
    PRESENTATION_BASE_CONTRACTS,
    PresentationProvider,
    PrincipalContext,
    PresentationData,
    Redacted,
    is_redacted,
    redacted_json_default,
    register_presentation_base_contracts,
)


# ── Closed contract list ──

def test_presentation_base_has_ten_contracts():
    """BRD #2 §5.1 specifies ten contracts in presentation-base."""
    assert len(PRESENTATION_BASE_CONTRACTS) == 10


def test_presentation_base_includes_all_canonical_contracts():
    expected = {
        "page", "text", "markdown", "data-table", "form",
        "chat", "metric", "nav-bar", "toast", "banner",
    }
    assert set(PRESENTATION_BASE_CONTRACTS) == expected


def test_chart_is_not_in_presentation_base_v09():
    """BRD §5.1: chart is intentionally deferred."""
    assert "chart" not in PRESENTATION_BASE_CONTRACTS


# ── Redacted sentinel ──

def test_redacted_carries_field_and_type():
    r = Redacted(field_name="salary", expected_type="currency")
    assert r.field_name == "salary"
    assert r.expected_type == "currency"
    assert r.reason is None


def test_redacted_with_optional_reason():
    r = Redacted(field_name="ssn", expected_type="text", reason="hr.access scope required")
    assert r.reason == "hr.access scope required"


def test_redacted_is_distinguishable_from_natural_falsy_values():
    """A type-safe sentinel: not equal to None, "", 0, False, etc."""
    r = Redacted(field_name="x", expected_type="text")
    assert r != ""
    assert r != 0
    assert r is not None
    assert r != False


def test_redacted_is_frozen():
    r = Redacted(field_name="x", expected_type="text")
    with pytest.raises((AttributeError, Exception)):
        r.field_name = "y"


def test_is_redacted_helper():
    assert is_redacted(Redacted(field_name="x", expected_type="text"))
    assert not is_redacted("plain text")
    assert not is_redacted(None)
    assert not is_redacted(42)


# ── JSON encoding ──

def test_redacted_json_default_produces_wire_shape():
    r = Redacted(field_name="salary", expected_type="currency", reason="restricted")
    out = redacted_json_default(r)
    assert out == {
        "__redacted": True,
        "field": "salary",
        "expected_type": "currency",
        "reason": "restricted",
    }


def test_redacted_serializes_through_json_dumps():
    r = Redacted(field_name="ssn", expected_type="text")
    blob = json.dumps({"value": r}, default=redacted_json_default)
    decoded = json.loads(blob)
    assert decoded["value"]["__redacted"] is True
    assert decoded["value"]["field"] == "ssn"


def test_redacted_json_default_raises_on_non_redacted():
    with pytest.raises(TypeError):
        redacted_json_default(object())


# ── PrincipalContext ──

def test_principal_context_has_brd_aligned_fields():
    pc = PrincipalContext(
        principal_id="u-1",
        principal_type="human",
        role_set=frozenset({"editor"}),
        scope_set=frozenset({"docs.read", "docs.write"}),
        theme_preference="dark",
        preferences={"theme": "dark", "locale": "en-US"},
    )
    assert pc.principal_id == "u-1"
    assert "editor" in pc.role_set
    assert pc.theme_preference == "dark"
    assert pc.preferences["locale"] == "en-US"


def test_principal_context_default_claims_empty():
    pc = PrincipalContext(
        principal_id="u",
        principal_type="human",
        role_set=frozenset(),
        scope_set=frozenset(),
        theme_preference="auto",
        preferences={},
    )
    assert pc.claims == {}


def test_principal_context_is_frozen():
    pc = PrincipalContext(
        principal_id="u", principal_type="human",
        role_set=frozenset(), scope_set=frozenset(),
        theme_preference="auto", preferences={},
    )
    with pytest.raises((AttributeError, Exception)):
        pc.principal_id = "other"


# ── PresentationData ──

def test_presentation_data_default_empty():
    d = PresentationData()
    assert d.records == ()
    assert d.props == {}
    assert d.meta == {}


# ── PresentationProvider Protocol structural conformance ──

class _StubProvider:
    declared_contracts = ("presentation-base.page", "presentation-base.text")
    render_modes = ("ssr",)

    def render_ssr(self, contract, ir_fragment, data, principal_context):
        return f"<div>{contract}</div>"

    def csr_bundle_url(self):
        return None


def test_protocol_recognizes_conforming_class():
    """runtime_checkable Protocol: isinstance() works structurally."""
    provider = _StubProvider()
    assert isinstance(provider, PresentationProvider)


def test_protocol_rejects_non_conforming_class():
    class Bad:
        pass
    assert not isinstance(Bad(), PresentationProvider)


# ── Contract registration ──

def test_register_presentation_base_contracts_adds_ten():
    from termin_core.providers.contracts import ContractRegistry, Category

    reg = ContractRegistry()
    before = len(reg.contracts_in(Category.PRESENTATION))
    register_presentation_base_contracts(reg)
    after = len(reg.contracts_in(Category.PRESENTATION))
    # Ten new contracts added.
    assert after - before == 10


def test_register_uses_namespace_qualified_names():
    from termin_core.providers.contracts import ContractRegistry, Category

    reg = ContractRegistry()
    register_presentation_base_contracts(reg)
    presentation_names = {
        c.name for c in reg.contracts_in(Category.PRESENTATION)
    }
    for short in PRESENTATION_BASE_CONTRACTS:
        assert f"presentation-base.{short}" in presentation_names


# ── IR: required_contracts populates from lowering ──

_BASE_APP = '''Application: T
  Description: t

Identity:
  Scopes are "x"
  A "user" has "x"

Content called "items":
  Each item has a name which is text, required
  Anyone with "x" can view items

As a user, I want to see items:
  Show a page called "Items"
    Display a table of items with columns: name
    Display text "hello"
    Display total item count
'''


def test_required_contracts_emitted_from_verbs():
    prog, _ = parse(_BASE_APP)
    spec = lower(prog)
    rc = set(spec.required_contracts)
    # page (every page implies it), text (Display text), data-table
    # (Display a table), metric (Display total)
    assert "presentation-base.page" in rc
    assert "presentation-base.text" in rc
    assert "presentation-base.data-table" in rc
    assert "presentation-base.metric" in rc


def test_required_contracts_alphabetically_sorted():
    prog, _ = parse(_BASE_APP)
    spec = lower(prog)
    assert list(spec.required_contracts) == sorted(spec.required_contracts)


def test_required_contracts_deduplicated():
    """Same contract referenced twice in source → one entry."""
    src = '''Application: T
  Description: t

Identity:
  Scopes are "x"
  A "user" has "x"

Content called "items":
  Each item has a name which is text, required
  Anyone with "x" can view items

As a user, I want to see items:
  Show a page called "Items"
    Display text "hello"
    Display text "world"
    Display text `name`
'''
    prog, _ = parse(src)
    spec = lower(prog)
    rc = list(spec.required_contracts)
    assert rc.count("presentation-base.text") == 1


def test_service_shaped_app_has_empty_required_contracts():
    """An app with no presentation verbs (BRD §4.4 — service shape)
    emits an empty required_contracts list."""
    src = '''Application: Service
  Description: no UI

Identity:
  Scopes are "x"
  A "user" has "x"

Content called "items":
  Each item has a name which is text, required
  Anyone with "x" can view items
'''
    prog, _ = parse(src)
    spec = lower(prog)
    assert spec.required_contracts == ()


# ── ComponentNode.contract tagging ──

def test_component_node_contract_set_for_text():
    prog, _ = parse(_BASE_APP)
    spec = lower(prog)
    [page] = spec.pages
    # Find the text node
    text_nodes = [c for c in page.children if c.type == "text"]
    assert len(text_nodes) >= 1
    assert text_nodes[0].contract == "presentation-base.text"


def test_component_node_contract_set_for_data_table():
    prog, _ = parse(_BASE_APP)
    spec = lower(prog)
    [page] = spec.pages
    dt_nodes = [c for c in page.children if c.type == "data_table"]
    assert len(dt_nodes) >= 1
    assert dt_nodes[0].contract == "presentation-base.data-table"


def test_component_node_default_contract_empty_string():
    """Direct construction without keyword set leaves contract empty
    — backward compat for non-Phase-5 code paths."""
    node = ComponentNode(type="text")
    assert node.contract == ""


def test_component_node_unrecognized_type_has_no_contract():
    """A type not in COMPONENT_TYPE_TO_CONTRACT (e.g., 'section',
    'related', 'subscribe', or anything internal) keeps contract
    empty after lowering."""
    src = '''Application: T
  Description: t

Identity:
  Scopes are "x"
  A "user" has "x"

Content called "items":
  Each item has a name which is text, required
  Anyone with "x" can view items

As a user, I want to see items:
  Show a page called "Items"
    Display a table of items with columns: name
'''
    prog, _ = parse(src)
    spec = lower(prog)
    [page] = spec.pages
    [dt] = [c for c in page.children if c.type == "data_table"]
    # data_table's children are modifiers (filter, search, etc.) —
    # they should NOT carry a contract.
    for child in dt.children:
        if child.type in ("filter", "search", "highlight", "subscribe",
                          "semantic_mark", "action_button", "edit_modal"):
            assert child.contract == "", (
                f"modifier {child.type} should have empty contract"
            )
