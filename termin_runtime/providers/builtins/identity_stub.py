# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Stub identity provider — first-party plugin against the v0.9
Identity contract surface.

This is the reference runtime's default identity provider for local
development and tests. It implements the BRD §6.1 contract using
cookie-style credentials: the runtime hands the provider a dict
{role, user_name} (typically populated from the `termin_role` and
`termin_user_name` cookies set per-test or by the dev role-switcher
form), and the provider returns a Principal.

The stub is NOT suitable for production. It performs no signature
verification or session validation; it trusts the credentials
verbatim. Real deployments bind a real identity product (Okta,
Cognito, custom SAML, etc.) through deploy config.

Loaded through the same ProviderRegistry mechanism third-party
providers use — see register_stub_identity below.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from ..contracts import Category, ContractRegistry
from ..identity_contract import Principal


class StubIdentityProvider:
    """Cookie-style identity for dev / test.

    Configuration: none required. The provider is keyed entirely by
    what's in `credentials` per request.

    Credentials shape (the runtime supplies these from cookies):
      role: str             — the role-name token the user has chosen
      user_name: str        — display name (optional, defaults to "User")
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        # No configuration in v0.9. Accept and ignore for forward
        # compatibility (third-party stubs may take config).
        self._config = dict(config or {})

    def authenticate(self, credentials: Mapping[str, Any]) -> Principal:
        """Cookie-based authentication.

        Returns a Principal whose id is a stable hash of (role, user_name)
        — same role+name pair always resolves to the same Principal.
        Per BRD §6.1, the runtime never calls this with empty
        credentials (Anonymous bypasses the provider).
        """
        role = str(credentials.get("role", "")).strip()
        user_name = str(credentials.get("user_name", "")).strip() or "User"
        if not role:
            raise ValueError(
                "StubIdentityProvider.authenticate requires a 'role' "
                "credential. Anonymous principals must bypass the "
                "provider per BRD §6.1."
            )
        # Stable id across re-authentications of the same role+name.
        # Hash so the cookie value isn't visible verbatim in the id.
        principal_id = "stub:" + hashlib.sha256(
            f"{role}|{user_name}".encode("utf-8")
        ).hexdigest()[:16]
        return Principal(
            id=principal_id,
            type="human",
            display_name=user_name,
            claims={
                "stub_role": role,
                "stub_user_name": user_name,
            },
            on_behalf_of=None,
        )

    def roles_for(self, principal: Principal, app_id: str) -> set:
        """Return the role names this principal holds.

        For the stub, the role is encoded in the principal's claims
        (set by authenticate). The runtime translates the role name
        to scopes using the source's role-to-scope mapping.

        Per BRD §6.1, providers must NOT be asked to resolve roles
        for the Anonymous principal. We raise loudly if it happens
        rather than silently returning an empty set.
        """
        if principal.is_anonymous:
            raise ValueError(
                "StubIdentityProvider.roles_for must not be called "
                "with the Anonymous principal — runtime should bypass."
            )
        role = principal.claims.get("stub_role", "")
        if not role:
            return set()
        return {role}


# ── Registration ──


def _stub_factory(config: Mapping[str, Any]) -> StubIdentityProvider:
    """Factory used by the ProviderRegistry to construct an instance
    when an app's deploy config binds identity to 'stub'."""
    return StubIdentityProvider(config)


def register_stub_identity(
    provider_registry, contract_registry: ContractRegistry | None = None
) -> None:
    """Register the stub identity provider with a ProviderRegistry.

    Same registration path third-party providers will use — no
    runtime-internal special casing. Pass the contract_registry to
    enable shape validation (rejects typos in category / contract
    name); first-party registration always passes it.
    """
    provider_registry.register(
        category=Category.IDENTITY,
        contract_name="default",
        product_name="stub",
        factory=_stub_factory,
        conformance="passing",
        version="0.9.0",
        contract_registry=contract_registry,
    )
