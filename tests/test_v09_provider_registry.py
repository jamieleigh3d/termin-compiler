# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 0 — contract and provider registry.

Phase 0 of the v0.9 provider system rollout adds the registry
infrastructure WITHOUT changing any primitive behavior. The runtime
ships with hardcoded contract definitions and an empty provider
registry; subsequent phases populate the registry with real providers.

These tests cover:
  - Contract metadata: every primitive category has a defined contract
    surface with the right tier classification.
  - Provider registration: a provider can register itself against a
    (category, contract) key with a product name.
  - Provider lookup: the registry returns the right provider for a
    (category, contract, product) tuple.
  - Conformance advertisement: providers declare their conformance
    level and supported sub-features at registration.
  - Empty-by-default: nothing is pre-registered. Phase 1+ adds
    real first-party providers through the same registration path.

These tests must pass on top of all existing 1605 compiler tests
without regression.
"""

import pytest

from termin_runtime.providers import (
    ContractRegistry, ProviderRegistry, ContractDefinition,
    ProviderRecord, Category, Tier,
)


# ── Contract registry: every primitive has a defined contract ──

class TestContractRegistry:
    """The contract registry is the catalog of what providers may
    implement. Phase 0 hardcodes the catalog; Phase 1+ providers look
    up contracts by name."""

    def test_default_registry_has_five_categories(self):
        reg = ContractRegistry.default()
        cats = {c.value for c in reg.categories()}
        assert cats == {"identity", "storage", "compute", "channels", "presentation"}

    def test_identity_contract_is_tier_0(self):
        reg = ContractRegistry.default()
        c = reg.get_contract(Category.IDENTITY, "default")
        assert c is not None
        assert c.tier == Tier.TIER_0

    def test_storage_contract_is_tier_1(self):
        reg = ContractRegistry.default()
        c = reg.get_contract(Category.STORAGE, "default")
        assert c.tier == Tier.TIER_1

    def test_compute_has_three_built_in_contracts(self):
        """default-CEL, llm, and ai-agent per BRD §6.3."""
        reg = ContractRegistry.default()
        names = {c.name for c in reg.contracts_in(Category.COMPUTE)}
        assert names == {"default-CEL", "llm", "ai-agent"}

    def test_compute_contracts_are_tier_1(self):
        reg = ContractRegistry.default()
        for c in reg.contracts_in(Category.COMPUTE):
            assert c.tier == Tier.TIER_1, f"{c.name} should be tier 1"

    def test_channels_has_four_built_in_contracts(self):
        """webhook, email, messaging, event-stream per BRD §6.4."""
        reg = ContractRegistry.default()
        names = {c.name for c in reg.contracts_in(Category.CHANNELS)}
        assert names == {"webhook", "email", "messaging", "event-stream"}

    def test_channels_contracts_are_tier_2(self):
        reg = ContractRegistry.default()
        for c in reg.contracts_in(Category.CHANNELS):
            assert c.tier == Tier.TIER_2, f"{c.name} should be tier 2"

    def test_presentation_contract_is_tier_1(self):
        reg = ContractRegistry.default()
        c = reg.get_contract(Category.PRESENTATION, "default")
        assert c.tier == Tier.TIER_1

    def test_get_unknown_contract_returns_none(self):
        reg = ContractRegistry.default()
        assert reg.get_contract(Category.COMPUTE, "made-up") is None

    def test_contract_definition_carries_naming_kind(self):
        """Per BRD §4: identity/storage/presentation are 'implicit'
        (source doesn't name the contract); compute/channels are
        'named' (source uses Provider is "X")."""
        reg = ContractRegistry.default()
        assert reg.get_contract(Category.IDENTITY, "default").naming == "implicit"
        assert reg.get_contract(Category.STORAGE, "default").naming == "implicit"
        assert reg.get_contract(Category.PRESENTATION, "default").naming == "implicit"
        assert reg.get_contract(Category.COMPUTE, "ai-agent").naming == "named"
        assert reg.get_contract(Category.CHANNELS, "messaging").naming == "named"


# ── Provider registry: empty by default, supports registration ──

class TestProviderRegistry:
    """Providers register themselves against (category, contract,
    product_name). The registry is empty in Phase 0 — first-party
    providers register in Phase 1+."""

    def test_default_registry_is_empty(self):
        reg = ProviderRegistry()
        assert reg.list_products(Category.IDENTITY, "default") == []
        assert reg.list_products(Category.STORAGE, "default") == []
        assert reg.list_products(Category.COMPUTE, "ai-agent") == []

    def test_register_and_retrieve(self):
        reg = ProviderRegistry()

        def factory(config):
            return {"kind": "test-stub", "config": config}

        reg.register(
            category=Category.IDENTITY,
            contract_name="default",
            product_name="stub",
            factory=factory,
            conformance="passing",
            version="0.9.0",
        )
        rec = reg.get(Category.IDENTITY, "default", "stub")
        assert rec is not None
        assert rec.product_name == "stub"
        assert rec.conformance == "passing"
        assert rec.version == "0.9.0"
        assert rec.factory is factory

    def test_register_two_products_for_same_contract(self):
        """Multiple products may implement the same contract — e.g.,
        both 'stub' and 'okta' implement Identity."""
        reg = ProviderRegistry()
        reg.register(Category.IDENTITY, "default", "stub", lambda c: c, "passing")
        reg.register(Category.IDENTITY, "default", "okta", lambda c: c, "passing")
        names = sorted(reg.list_products(Category.IDENTITY, "default"))
        assert names == ["okta", "stub"]

    def test_get_unknown_product_returns_none(self):
        reg = ProviderRegistry()
        assert reg.get(Category.IDENTITY, "default", "nonexistent") is None

    def test_register_unknown_contract_raises(self):
        """A provider can only register against a known contract."""
        reg = ProviderRegistry()
        contracts = ContractRegistry.default()
        with pytest.raises(ValueError) as exc:
            reg.register(
                Category.COMPUTE, "nonexistent-contract", "myprod",
                lambda c: c, "passing",
                contract_registry=contracts,
            )
        assert "nonexistent-contract" in str(exc.value)

    def test_conformance_advertisement_supports_features(self):
        """Per BRD §9.2 conformance manifest — providers may advertise
        partial implementation (e.g., messaging provider supports
        'send' and 'react' but not 'thread_reply')."""
        reg = ProviderRegistry()
        reg.register(
            Category.CHANNELS, "messaging", "discord",
            lambda c: c, "partial",
            features=["send_message", "react"],
        )
        rec = reg.get(Category.CHANNELS, "messaging", "discord")
        assert rec.conformance == "partial"
        assert "send_message" in rec.features
        assert "thread_reply" not in rec.features

    def test_no_existing_runtime_module_imports_providers(self):
        """Phase 0 invariant: the providers package exists but no
        existing runtime module pulls from it. Phase 1+ wires it up
        per primitive."""
        import termin_runtime
        # Sentinel: confirm the package is importable on its own.
        from termin_runtime import providers  # noqa: F401
        # And that no primitive module currently references it. This
        # guards against accidental wiring during Phase 0.
        import importlib
        for module_name in [
            "termin_runtime.identity",
            "termin_runtime.storage",
            "termin_runtime.app",
            "termin_runtime.routes",
            "termin_runtime.compute_runner",
            "termin_runtime.channels",
        ]:
            m = importlib.import_module(module_name)
            src = m.__file__
            # Read the source and check it doesn't import providers.
            with open(src, encoding="utf-8") as f:
                content = f.read()
            assert "from termin_runtime.providers" not in content, (
                f"{module_name} imports providers — Phase 0 is "
                f"scaffolding-only, no primitive should be wired up yet."
            )
            assert "import termin_runtime.providers" not in content, (
                f"{module_name} imports providers — Phase 0 is "
                f"scaffolding-only, no primitive should be wired up yet."
            )
