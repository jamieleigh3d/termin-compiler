# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5a.2: Tailwind-default presentation provider.

Per BRD #2 §9.1 + design doc §3.9. The Tailwind-default provider is
the implicit-default SSR renderer covering all ten `presentation-base`
contracts. It wraps the existing termin_runtime/presentation.py
renderer functions verbatim — the cut-over is the seam, not a
rewrite.

Scope of this test module: the provider's contract conformance.
End-to-end render correctness is already covered by the 1525+ tests
that exercise `render_component(node)` directly (test_e2e.py,
test_helpdesk.py, test_projectboard.py, etc.) — those tests
continue to pass, which is the second-order safety net.
"""

from __future__ import annotations

import pytest

from termin_runtime.providers.builtins import (
    TailwindDefaultProvider,
    register_tailwind_default,
    register_builtins,
)
from termin_runtime.providers.contracts import Category, ContractRegistry
from termin_runtime.providers.registry import ProviderRegistry
from termin_runtime.providers.presentation_contract import (
    PRESENTATION_BASE_CONTRACTS,
    PresentationProvider,
    PresentationData,
    PrincipalContext,
)


# ── Provider construction ──

def test_provider_constructs_without_config():
    p = TailwindDefaultProvider()
    assert p.declared_contracts
    assert p.render_modes


def test_provider_accepts_optional_config():
    p = TailwindDefaultProvider({"some_key": "some_value"})
    assert isinstance(p.declared_contracts, tuple)


# ── Provider declares all ten presentation-base contracts ──

def test_declared_contracts_covers_all_ten():
    p = TailwindDefaultProvider()
    expected = {
        f"presentation-base.{n}" for n in PRESENTATION_BASE_CONTRACTS
    }
    assert set(p.declared_contracts) == expected


def test_declared_contracts_is_tuple():
    p = TailwindDefaultProvider()
    assert isinstance(p.declared_contracts, tuple)


# ── Render modes ──

def test_render_modes_is_ssr_only_in_v09():
    """Per design doc Q7 resolution, Tailwind ships SSR only in v0.9.
    CSR mode is on the technical-debt list and lands alongside Carbon
    (which brings the termin.js extension surface)."""
    p = TailwindDefaultProvider()
    assert p.render_modes == ("ssr",)


def test_csr_bundle_url_returns_none():
    p = TailwindDefaultProvider()
    assert p.csr_bundle_url() is None


# ── Protocol structural conformance ──

def test_provider_satisfies_protocol():
    """runtime_checkable Protocol: isinstance() check confirms the
    provider exposes declared_contracts, render_modes, render_ssr,
    csr_bundle_url with the expected shapes."""
    p = TailwindDefaultProvider()
    assert isinstance(p, PresentationProvider)


# ── render_ssr delegates to legacy render_component ──

def _principal_context(theme="light"):
    return PrincipalContext(
        principal_id="u-1",
        principal_type="human",
        role_set=frozenset({"user"}),
        scope_set=frozenset(),
        theme_preference=theme,
        preferences={"theme": theme},
    )


def test_render_ssr_text_node():
    p = TailwindDefaultProvider()
    out = p.render_ssr(
        contract="presentation-base.text",
        ir_fragment={"type": "text", "props": {"content": "Hello"}},
        data=PresentationData(),
        principal_context=_principal_context(),
    )
    assert "Hello" in out
    assert "<div" in out


def test_render_ssr_unknown_type_falls_back_safely():
    """The legacy render_component dispatches to _render_unknown for
    types it doesn't recognize — produces a safe placeholder rather
    than crashing."""
    p = TailwindDefaultProvider()
    out = p.render_ssr(
        contract="presentation-base.text",
        ir_fragment={"type": "definitely-not-a-real-type", "props": {}},
        data=PresentationData(),
        principal_context=_principal_context(),
    )
    assert isinstance(out, str)


def test_render_ssr_accepts_componentnode_dataclass():
    """Direct callers may pass a ComponentNode dataclass instance
    rather than a dict — the provider projects it before delegating."""
    from termin_core.ir.types import ComponentNode
    p = TailwindDefaultProvider()
    node = ComponentNode(
        type="text",
        props={"content": "From Dataclass"},
    )
    out = p.render_ssr(
        contract="presentation-base.text",
        ir_fragment=node,
        data=PresentationData(),
        principal_context=_principal_context(),
    )
    assert "From Dataclass" in out


# ── Registration ──

def test_register_against_all_ten_contracts():
    """register_tailwind_default registers the provider for every
    presentation-base.<contract> name."""
    contracts = ContractRegistry.default()
    providers = ProviderRegistry()
    register_tailwind_default(providers, contracts)

    for short in PRESENTATION_BASE_CONTRACTS:
        full = f"presentation-base.{short}"
        record = providers.get(Category.PRESENTATION, full, "tailwind-default")
        assert record is not None, f"missing registration for {full}"
        assert record.factory is not None


def test_register_factory_constructs_provider_instance():
    contracts = ContractRegistry.default()
    providers = ProviderRegistry()
    register_tailwind_default(providers, contracts)

    record = providers.get(
        Category.PRESENTATION, "presentation-base.page", "tailwind-default"
    )
    instance = record.factory({})
    assert isinstance(instance, TailwindDefaultProvider)


def test_register_idempotent_on_contract_re_registration():
    """Calling register_tailwind_default twice should not raise on the
    contract-registration side (the second call sees the contracts
    already present and tolerates)."""
    contracts = ContractRegistry.default()
    providers1 = ProviderRegistry()
    providers2 = ProviderRegistry()

    register_tailwind_default(providers1, contracts)
    # Second call against the same contract registry but fresh
    # provider registry — must not raise from the contract side.
    register_tailwind_default(providers2, contracts)

    # Both provider registries got their registrations.
    assert providers1.get(
        Category.PRESENTATION, "presentation-base.page", "tailwind-default"
    ) is not None
    assert providers2.get(
        Category.PRESENTATION, "presentation-base.page", "tailwind-default"
    ) is not None


# ── Integration with register_builtins ──

def test_register_builtins_includes_tailwind_default():
    contracts = ContractRegistry.default()
    providers = ProviderRegistry()
    register_builtins(providers, contracts)

    # All ten presentation-base contracts have a tailwind-default
    # provider available.
    for short in PRESENTATION_BASE_CONTRACTS:
        full = f"presentation-base.{short}"
        record = providers.get(
            Category.PRESENTATION, full, "tailwind-default"
        )
        assert record is not None


def test_register_builtins_does_not_break_other_categories():
    """Smoke check: adding the presentation provider doesn't disturb
    identity / storage / compute / channel registrations."""
    contracts = ContractRegistry.default()
    providers = ProviderRegistry()
    register_builtins(providers, contracts)

    # Identity stub still there.
    assert providers.get(Category.IDENTITY, "default", "stub") is not None
    # Storage SQLite still there.
    assert providers.get(Category.STORAGE, "default", "sqlite") is not None
    # Compute default-CEL still there.
    assert providers.get(Category.COMPUTE, "default-CEL", "default-cel") is not None
