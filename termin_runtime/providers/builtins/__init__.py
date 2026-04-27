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
from .storage_sqlite import SqliteStorageProvider, register_sqlite_storage
from .compute_default_cel import DefaultCelProvider, register_default_cel
from .compute_llm_stub import StubLlmProvider, register_stub_llm
from .compute_agent_stub import StubAgentProvider, register_stub_agent
from .compute_llm_anthropic import (
    AnthropicLlmProvider, register_anthropic_llm,
)
from .compute_agent_anthropic import (
    AnthropicAgentProvider, register_anthropic_agent,
)
from .channel_webhook_stub import WebhookChannelStub, register_webhook_stub
from .channel_email_stub import EmailChannelStub, register_email_stub
from .channel_messaging_stub import MessagingChannelStub, register_messaging_stub
from .presentation_tailwind_default import (
    TailwindDefaultProvider, register_tailwind_default,
)


def register_builtins(provider_registry, contract_registry):
    """Register all first-party providers with the given registry.

    Phase 1: stub identity provider.
    Phase 2: SQLite storage provider.
    Phase 3 (slice a): compute providers — default-CEL plus llm and
        ai-agent (anthropic + stub products for each named contract).
    Phase 4: channel providers — webhook, email, and messaging stubs.
        event-stream stub deferred (no fixture requires it in Phase 4).
    Phase 5a (slice 2): tailwind-default presentation provider —
        SSR-only, covers all ten presentation-base contracts.
        BRD §9.1: subsumes the stub provider role.
    """
    register_stub_identity(provider_registry, contract_registry)
    register_sqlite_storage(provider_registry, contract_registry)
    register_default_cel(provider_registry, contract_registry)
    register_stub_llm(provider_registry, contract_registry)
    register_stub_agent(provider_registry, contract_registry)
    register_anthropic_llm(provider_registry, contract_registry)
    register_anthropic_agent(provider_registry, contract_registry)
    register_webhook_stub(provider_registry, contract_registry)
    register_email_stub(provider_registry, contract_registry)
    register_messaging_stub(provider_registry, contract_registry)
    register_tailwind_default(provider_registry, contract_registry)


__all__ = [
    "StubIdentityProvider",
    "register_stub_identity",
    "SqliteStorageProvider",
    "register_sqlite_storage",
    "DefaultCelProvider",
    "register_default_cel",
    "StubLlmProvider",
    "register_stub_llm",
    "StubAgentProvider",
    "register_stub_agent",
    "AnthropicLlmProvider",
    "register_anthropic_llm",
    "AnthropicAgentProvider",
    "register_anthropic_agent",
    "WebhookChannelStub",
    "register_webhook_stub",
    "EmailChannelStub",
    "register_email_stub",
    "MessagingChannelStub",
    "register_messaging_stub",
    "register_builtins",
]
