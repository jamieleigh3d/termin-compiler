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

    def test_register_contract_extends_catalog(self):
        """Per BRD §4 (revised): contracts are semi-open. Providers
        can register new contracts within existing primitive
        categories. The geospatial-compute case from JL's review."""
        reg = ContractRegistry.default()
        assert reg.get_contract(Category.COMPUTE, "geospatial") is None
        new_contract = ContractDefinition(
            name="geospatial",
            category=Category.COMPUTE,
            tier=Tier.TIER_1,
            naming="named",
            description="Domain-specific geospatial transforms.",
        )
        reg.register_contract(new_contract)
        c = reg.get_contract(Category.COMPUTE, "geospatial")
        assert c is not None
        assert c.naming == "named"
        # Built-in contracts in the same category still present.
        assert reg.get_contract(Category.COMPUTE, "ai-agent") is not None

    def test_register_contract_rejects_duplicate(self):
        """A provider cannot silently shadow a built-in contract.
        Replacing a built-in requires a spec evolution + new release."""
        reg = ContractRegistry.default()
        duplicate = ContractDefinition(
            name="ai-agent",  # already exists
            category=Category.COMPUTE,
            tier=Tier.TIER_1,
            naming="named",
            description="Trying to override the built-in.",
        )
        with pytest.raises(ValueError) as exc:
            reg.register_contract(duplicate)
        assert "already registered" in str(exc.value)

    def test_register_contract_allows_same_name_in_different_category(self):
        """Same contract name is fine if it lives under a different
        primitive category (unlikely in practice but the constraint
        is per (category, name))."""
        reg = ContractRegistry.default()
        # Hypothetical: a presentation contract also named "default-CEL"
        # (the compute one). Different category, different contract.
        new_contract = ContractDefinition(
            name="default-CEL",
            category=Category.PRESENTATION,
            tier=Tier.TIER_1,
            naming="implicit",
            description="Hypothetical presentation contract sharing a name.",
        )
        # Should NOT raise.
        reg.register_contract(new_contract)
        # Both still resolvable.
        assert reg.get_contract(Category.COMPUTE, "default-CEL") is not None
        assert reg.get_contract(Category.PRESENTATION, "default-CEL") is not None


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

    def test_unwired_runtime_modules_do_not_import_providers(self):
        """Phase 1 invariant: identity + app are wired through the
        provider registry; the other primitive modules are not yet.

        Phase 2+ wires storage, routes, compute_runner, channels.
        When each phase lands, that module moves out of this list
        and becomes a wired-and-tested primitive instead.

        The check looks for both absolute (`from termin_runtime.providers`)
        and relative (`from .providers`) imports so accidental wiring
        in either form is caught.
        """
        import importlib
        from termin_runtime import providers  # noqa: F401  (sentinel)
        unwired = [
            "termin_runtime.storage",
            "termin_runtime.routes",
            "termin_runtime.compute_runner",
            "termin_runtime.channels",
        ]
        for module_name in unwired:
            m = importlib.import_module(module_name)
            with open(m.__file__, encoding="utf-8") as f:
                content = f.read()
            assert "from termin_runtime.providers" not in content, (
                f"{module_name} imports providers — that primitive is "
                f"not yet wired through the provider registry. If "
                f"you're starting that phase, remove this entry from "
                f"the unwired list."
            )
            assert "import termin_runtime.providers" not in content, (
                f"{module_name} imports providers — see above."
            )
            assert "from .providers" not in content, (
                f"{module_name} imports providers via relative form — "
                f"see above."
            )

    def test_phase_1_wired_modules_do_import_providers(self):
        """Positive control: Phase 1 specifically wires identity and
        app through the provider registry. If these stop importing
        providers, the wire-up was reverted."""
        import importlib
        for module_name in ("termin_runtime.identity", "termin_runtime.app"):
            m = importlib.import_module(module_name)
            with open(m.__file__, encoding="utf-8") as f:
                content = f.read()
            imports_providers = (
                "from termin_runtime.providers" in content
                or "import termin_runtime.providers" in content
                or "from .providers" in content
            )
            assert imports_providers, (
                f"{module_name} no longer imports providers — Phase 1 "
                f"wire-up appears reverted."
            )


# ── Behavioral guard: the runtime actually constructs and uses an
#    IdentityProvider. Catches the case where Phase 1 imports stay
#    in place but the runtime path bypasses the provider. ──


class TestRuntimeUsesIdentityProvider:
    """Phase 1 step 4 behavioral guard. Static-import checks are
    necessary but not sufficient — a future refactor could keep the
    imports while routing around the provider. These tests exercise
    the runtime end-to-end and assert the provider is actually
    consulted."""

    def _make_test_app(self):
        """Build a minimal app + return its RuntimeContext."""
        import json
        from termin_runtime.app import create_termin_app
        # Construct a tiny IR-shaped dict directly to avoid the full
        # compile path. Only what's needed for the identity bootstrap.
        ir = {
            "name": "Test", "app_id": "test-app",
            "auth": {
                "provider": "stub",
                "scopes": ["app.view"],
                "roles": [
                    {"name": "Anonymous", "scopes": ["app.view"]},
                    {"name": "user", "scopes": ["app.view"]},
                ],
            },
            "content": [], "computes": [], "channels": [],
            "boundaries": [], "events": [], "pages": [],
            "routes": [], "state_machines": [],
            "reflection_enabled": False,
        }
        app = create_termin_app(
            json.dumps(ir),
            db_path=":memory:",
            strict_channels=False,
            deploy_config={},
        )
        # The RuntimeContext is stashed on the app; identity_provider
        # lives there per the Phase 1 step 4 wire-up.
        return app, app.state.ctx if hasattr(app.state, "ctx") else None

    def test_identity_provider_is_constructed_at_startup(self):
        """ctx.identity_provider must be a real IdentityProvider
        instance after create_termin_app returns."""
        from termin_runtime.providers import IdentityProvider
        app, ctx = self._make_test_app()
        assert ctx is not None, "RuntimeContext should be on app.state.ctx"
        assert ctx.identity_provider is not None
        assert isinstance(ctx.identity_provider, IdentityProvider)

    def test_get_current_user_routes_through_provider(self):
        """A request with a non-Anonymous role cookie must produce
        a user dict whose Principal was constructed by the provider's
        authenticate path (not a synthesized inline shape)."""
        from termin_runtime.providers.builtins.identity_stub import (
            StubIdentityProvider,
        )
        app, ctx = self._make_test_app()
        # Mock request with a role cookie.
        class _Req:
            cookies = {"termin_role": "user", "termin_user_name": "Alice"}
        user = ctx.get_current_user(_Req())
        principal = user["Principal"]
        # The Principal id pattern is provider-stamped — the stub
        # uses 'stub:<hash>'. If the runtime were synthesizing
        # principals inline (bypassing the provider), the id would
        # not have this prefix.
        assert principal.id.startswith("stub:"), (
            f"Principal id should be provider-stamped; got {principal.id!r}. "
            f"The runtime appears to bypass the IdentityProvider."
        )
        assert principal.display_name == "Alice"
        assert principal.is_anonymous is False

    def test_anonymous_request_bypasses_provider(self):
        """Per BRD §6.1: Anonymous bypasses the provider entirely.
        The runtime must construct ANONYMOUS_PRINCIPAL directly,
        never call authenticate."""
        from termin_runtime.providers import ANONYMOUS_PRINCIPAL
        app, ctx = self._make_test_app()
        class _Req:
            cookies = {"termin_role": "Anonymous"}
        user = ctx.get_current_user(_Req())
        principal = user["Principal"]
        # Anonymous principal is the canonical sentinel — same id.
        assert principal.id == ANONYMOUS_PRINCIPAL.id == "anonymous"
        assert principal.is_anonymous is True
        # Stub-id prefix would be a bug — means the runtime called
        # provider.authenticate for the Anonymous case.
        assert not principal.id.startswith("stub:")

    def test_unregistered_identity_product_fails_closed(self):
        """Per BRD §6.1 fail-closed: a deploy_config naming an
        identity product that isn't registered is a deploy
        misconfiguration. Runtime must refuse to start rather
        than silently fall back to stub."""
        import json
        import pytest
        from termin_runtime.app import create_termin_app
        ir = {
            "name": "Test", "app_id": "test-app",
            "auth": {
                "provider": "stub", "scopes": [],
                "roles": [{"name": "Anonymous", "scopes": []}],
            },
            "content": [], "computes": [], "channels": [],
            "boundaries": [], "events": [], "pages": [],
            "routes": [], "state_machines": [],
            "reflection_enabled": False,
        }
        bad_config = {
            "bindings": {
                "identity": {"provider": "made-up-sso", "config": {}},
            },
        }
        with pytest.raises(RuntimeError) as exc:
            create_termin_app(
                json.dumps(ir),
                db_path=":memory:",
                strict_channels=False,
                deploy_config=bad_config,
            )
        msg = str(exc.value)
        assert "made-up-sso" in msg
        assert "not registered" in msg.lower()
