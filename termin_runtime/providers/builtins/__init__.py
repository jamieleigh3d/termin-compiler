# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""First-party Termin provider implementations.

These ship with the reference runtime but are NOT special-cased — they
load through the same ProviderRegistry that third-party providers
use. The first-party / third-party distinction is governance, not
architecture.

To register all built-in providers in one call, use
`register_builtins(registry)` from this package:

    from termin_runtime.providers import ProviderRegistry, ContractRegistry
    from termin_runtime.providers.builtins import register_builtins

    contracts = ContractRegistry.default()
    providers = ProviderRegistry()
    register_builtins(providers, contracts)
"""

from .identity_stub import StubIdentityProvider, register_stub_identity


def register_builtins(provider_registry, contract_registry):
    """Register all first-party providers with the given registry.

    Phase 1: only the stub identity provider. Phase 2+ adds storage
    (sqlite), compute (default-CEL, llm via anthropic), channels
    (webhook, email, messaging) etc.
    """
    register_stub_identity(provider_registry, contract_registry)


__all__ = [
    "StubIdentityProvider",
    "register_stub_identity",
    "register_builtins",
]
