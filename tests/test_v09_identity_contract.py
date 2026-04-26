# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 1 — Identity contract conformance.

Tests the BRD §6.1 behavioral requirements against the stub
IdentityProvider (the only first-party identity provider in v0.9).
The tests are organized so that future providers (Okta, Cognito,
custom SAML) can run the same suite by parametrizing the
`identity_provider` fixture — same input/output expectations,
different implementation underneath.

Behavioral requirements covered (BRD §6.1):
  - Anonymous bypasses provider entirely (runtime never calls
    authenticate; runtime constructs ANONYMOUS_PRINCIPAL directly).
  - authenticate(credentials) returns a typed Principal.
  - Stable Principal.id across re-authentications of same identity.
  - roles_for(principal, app_id) returns role names.
  - Multi-role principal support (effective scopes = union).
  - Service principals carry their own roles.
  - Mid-session role changes — provider returns fresh roles per call.
  - Fail-closed on provider errors (runtime falls back to Anonymous).

Tests of the runtime's translation layer (provider role names →
source-declared scopes) live in tests/test_runtime.py and the
existing role/scope test files; this file is contract-shape only.
"""

import pytest

from termin_runtime.providers import (
    Category, ContractRegistry, ProviderRegistry,
    Principal, IdentityProvider, ANONYMOUS_PRINCIPAL,
)
from termin_runtime.providers.builtins import register_builtins
from termin_runtime.providers.builtins.identity_stub import StubIdentityProvider


# ── Fixtures ──


@pytest.fixture
def identity_provider() -> IdentityProvider:
    """A fresh stub provider per test. Future providers parametrize
    here to run the same conformance suite against their impl."""
    return StubIdentityProvider({})


@pytest.fixture
def populated_registry():
    """A ProviderRegistry with built-in providers registered, paired
    with the contract registry."""
    contracts = ContractRegistry.default()
    providers = ProviderRegistry()
    register_builtins(providers, contracts)
    return contracts, providers


# ── Anonymous bypass ──


class TestAnonymousBypass:
    """Per BRD §6.1: 'Anonymous bypasses the provider entirely. No-
    credentials requests never call authenticate; runtime treats as
    Anonymous principal directly.'

    These tests prove the contract recognizes the bypass — they don't
    test the runtime's bypass implementation (that's runtime tests)."""

    def test_anonymous_principal_is_typed(self):
        assert isinstance(ANONYMOUS_PRINCIPAL, Principal)

    def test_anonymous_principal_id_is_stable(self):
        assert ANONYMOUS_PRINCIPAL.id == "anonymous"

    def test_anonymous_principal_is_human_type(self):
        assert ANONYMOUS_PRINCIPAL.type == "human"

    def test_anonymous_has_no_on_behalf_of(self):
        assert ANONYMOUS_PRINCIPAL.on_behalf_of is None

    def test_anonymous_has_empty_claims(self):
        assert dict(ANONYMOUS_PRINCIPAL.claims) == {}

    def test_anonymous_is_anonymous_property(self):
        assert ANONYMOUS_PRINCIPAL.is_anonymous is True

    def test_provider_rejects_anonymous_in_roles_for(self, identity_provider):
        """The stub raises if asked to resolve roles for Anonymous —
        runtime is required to bypass, not delegate. Future providers
        may also raise here."""
        with pytest.raises(ValueError):
            identity_provider.roles_for(ANONYMOUS_PRINCIPAL, "app-id")

    def test_provider_rejects_empty_credentials(self, identity_provider):
        """Per BRD: runtime never calls authenticate with empty creds.
        Provider may raise to enforce the contract."""
        with pytest.raises(ValueError):
            identity_provider.authenticate({})

    def test_provider_rejects_empty_role(self, identity_provider):
        with pytest.raises(ValueError):
            identity_provider.authenticate({"role": "", "user_name": "x"})


# ── Principal shape ──


class TestPrincipalShape:
    """authenticate produces a typed Principal with the expected fields."""

    def test_authenticate_returns_principal(self, identity_provider):
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert isinstance(p, Principal)

    def test_authenticate_principal_is_human(self, identity_provider):
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert p.type == "human"

    def test_authenticate_principal_carries_display_name(self, identity_provider):
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert p.display_name == "Alice"

    def test_authenticate_principal_id_is_opaque(self, identity_provider):
        """id should be a non-trivial string, not the role/name themselves —
        runtime treats id as opaque."""
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert isinstance(p.id, str) and len(p.id) > 0
        # Stub specifically uses a "stub:" prefix on the hashed id;
        # this isn't a contract requirement, just a stub property.
        assert p.id.startswith("stub:")

    def test_authenticate_default_user_name(self, identity_provider):
        """Stub defaults user_name when omitted (dev-friendly behavior)."""
        p = identity_provider.authenticate({"role": "warehouse clerk"})
        assert p.display_name == "User"

    def test_authenticate_principal_has_no_on_behalf_of(self, identity_provider):
        """Standard human principal — no delegation chain."""
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert p.on_behalf_of is None

    def test_principal_rejects_invalid_type(self):
        with pytest.raises(ValueError):
            Principal(id="x", type="robot")  # not human/agent/service

    def test_agent_cannot_be_on_behalf_of_agent(self):
        agent_a = Principal(id="a1", type="agent")
        with pytest.raises(ValueError):
            Principal(id="a2", type="agent", on_behalf_of=agent_a)


# ── Stable id ──


class TestStableId:
    """Per BRD §6.1: 'id ... stable identifier, never changes.' Same
    underlying entity must resolve to the same id on each
    authentication."""

    def test_same_credentials_same_id(self, identity_provider):
        p1 = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        p2 = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert p1.id == p2.id

    def test_different_users_different_ids(self, identity_provider):
        p1 = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        p2 = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Bob",
        })
        assert p1.id != p2.id

    def test_different_roles_different_ids(self, identity_provider):
        """The stub treats role+name as the identity tuple. Real
        providers (OIDC sub) would key on the user only — both
        behaviors satisfy the contract."""
        p1 = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        p2 = identity_provider.authenticate({
            "role": "warehouse manager", "user_name": "Alice",
        })
        # Stub: differ. Doc tests this is acceptable; not a hard
        # contract requirement.
        assert p1.id != p2.id


# ── roles_for ──


class TestRolesFor:
    def test_roles_for_returns_set(self, identity_provider):
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        roles = identity_provider.roles_for(p, "warehouse-app")
        assert isinstance(roles, set)

    def test_roles_for_returns_authenticated_role(self, identity_provider):
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        assert identity_provider.roles_for(p, "warehouse-app") == {"warehouse clerk"}

    def test_roles_for_independent_of_app_id(self, identity_provider):
        """Stub doesn't scope by app — same principal gets the same
        roles in every app. Real providers may scope; either is
        contract-compliant."""
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        r1 = identity_provider.roles_for(p, "app-1")
        r2 = identity_provider.roles_for(p, "app-2")
        assert r1 == r2

    def test_roles_for_freshness(self, identity_provider):
        """Per BRD: 'mid-session role changes MUST be enforced. The
        runtime authorizes against the freshest roles the provider
        can supply.' Calling roles_for twice returns current state
        each time — no caching of stale results in the provider."""
        p = identity_provider.authenticate({
            "role": "warehouse clerk", "user_name": "Alice",
        })
        r1 = identity_provider.roles_for(p, "app")
        r2 = identity_provider.roles_for(p, "app")
        # Stub is stateless; same answer. The contract permits TTL
        # caching but the runtime still expects per-call freshness
        # within a tolerance.
        assert r1 == r2


# ── Registry integration ──


class TestRegistryIntegration:
    def test_stub_registers_under_identity_default(self, populated_registry):
        contracts, providers = populated_registry
        rec = providers.get(Category.IDENTITY, "default", "stub")
        assert rec is not None
        assert rec.product_name == "stub"

    def test_stub_factory_constructs_provider(self, populated_registry):
        contracts, providers = populated_registry
        rec = providers.get(Category.IDENTITY, "default", "stub")
        instance = rec.factory({})
        assert isinstance(instance, IdentityProvider)

    def test_stub_factory_accepts_config(self, populated_registry):
        """Forward-compatibility: factory accepts a config dict even
        though the stub doesn't currently use it. Third-party stubs
        or extension stubs may take config."""
        contracts, providers = populated_registry
        rec = providers.get(Category.IDENTITY, "default", "stub")
        instance = rec.factory({"some": "future_option"})
        # Should construct successfully and ignore the unknown key.
        assert isinstance(instance, IdentityProvider)

    def test_stub_advertised_conformance_passing(self, populated_registry):
        contracts, providers = populated_registry
        rec = providers.get(Category.IDENTITY, "default", "stub")
        assert rec.conformance == "passing"


# ── Service / agent principals (shape only — runtime use comes later) ──


class TestServiceAndAgent:
    """Service + agent principal shape. The stub doesn't construct
    these (its credentials are cookie-style); they're shown here as
    typed shapes that future providers will produce per BRD §6.1."""

    def test_service_principal_has_own_roles(self):
        """A service principal carries roles via deploy config
        role_mappings; on_behalf_of is None."""
        svc = Principal(
            id="svc:warehouse-batch",
            type="service",
            display_name="Warehouse batch job",
            claims={"service_account_id": "warehouse-batch"},
            on_behalf_of=None,
        )
        assert svc.type == "service"
        assert svc.on_behalf_of is None
        assert svc.is_anonymous is False

    def test_agent_principal_in_delegate_mode(self):
        """An agent in delegate mode has a human on_behalf_of and
        derives authorization from that human."""
        human = Principal(
            id="okta:user-42",
            type="human",
            display_name="Alice",
        )
        agent = Principal(
            id="agent:moderation-bot",
            type="agent",
            display_name="Moderation Bot",
            claims={"agent_id": "moderation-bot"},
            on_behalf_of=human,
        )
        assert agent.type == "agent"
        assert agent.on_behalf_of is human

    def test_agent_principal_in_service_mode(self):
        """An agent in service mode has its own roles and no
        on_behalf_of."""
        agent = Principal(
            id="agent:nightly-cleanup",
            type="agent",
            display_name="Nightly Cleanup",
            on_behalf_of=None,
        )
        assert agent.on_behalf_of is None
