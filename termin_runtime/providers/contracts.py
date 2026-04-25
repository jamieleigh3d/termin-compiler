# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Contract definitions and registry — v0.9 Phase 0.

Contracts describe the open surface providers implement. Phase 0
hardcodes the catalog of built-in contracts; the runtime ships with
exactly the contracts the BRD declares (one Identity, one Storage,
three Compute, four Channel, one Presentation).

Contract definitions are descriptive metadata in Phase 0 — they carry
the contract's name, category, tier classification, and naming kind
(implicit vs named). Phase 1+ adds operation signatures and
behavioral requirements; Phase 0 doesn't yet need them because no
primitive consults the registry to dispatch operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional


class Category(str, Enum):
    """The five primitive categories that have providers.

    String-valued so they serialize cleanly to JSON in deploy configs
    and conformance manifests.
    """
    IDENTITY = "identity"
    STORAGE = "storage"
    COMPUTE = "compute"
    CHANNELS = "channels"
    PRESENTATION = "presentation"


class Tier(int, Enum):
    """Operational tier per BRD §4.

    Tier 0: provider outage takes down the AppFabric. Identity only.
    Tier 1: provider outage takes down apps that depend on it.
    Tier 2: provider outage degrades a specific integration.
    """
    TIER_0 = 0
    TIER_1 = 1
    TIER_2 = 2


@dataclass(frozen=True)
class ContractDefinition:
    """Metadata describing one contract surface.

    name: the contract's identifier within its category. For implicit-
        naming categories this is "default"; for named categories it's
        the value referenced in source via Provider is "<name>".
    category: which primitive category.
    tier: operational tier.
    naming: "implicit" if source doesn't name the contract; "named"
        if source uses Provider is "<name>" to bind. Determines whether
        deploy config flat-binds the category or keys through contract
        names.
    description: short human-readable description.
    """
    name: str
    category: Category
    tier: Tier
    naming: str  # "implicit" | "named"
    description: str = ""

    def __post_init__(self) -> None:
        if self.naming not in ("implicit", "named"):
            raise ValueError(
                f"naming must be 'implicit' or 'named', got {self.naming!r}"
            )


# ── Built-in contract catalog ──
#
# Frozen at runtime. Phase 0 ships exactly these; new contracts are
# added by Termin spec evolution, not at runtime. Adoption happens
# through new providers against existing contracts (Tenet 4: providers
# over primitives).

_BUILTIN_CONTRACTS: tuple[ContractDefinition, ...] = (
    # Identity — single contract surface, implicit binding.
    ContractDefinition(
        name="default",
        category=Category.IDENTITY,
        tier=Tier.TIER_0,
        naming="implicit",
        description="Authentication + role resolution. Tier 0: AppFabric "
                    "depends on it. See BRD §6.1.",
    ),

    # Storage — single contract surface, implicit binding.
    ContractDefinition(
        name="default",
        category=Category.STORAGE,
        tier=Tier.TIER_1,
        naming="implicit",
        description="CRUD + predicate query + cascade-aware deletes + "
                    "schema migration. See BRD §6.2.",
    ),

    # Presentation — single contract surface, implicit binding.
    # Full treatment in BRD #2.
    ContractDefinition(
        name="default",
        category=Category.PRESENTATION,
        tier=Tier.TIER_1,
        naming="implicit",
        description="Component tree rendering. Three customization "
                    "levels deferred to Presentation BRD.",
    ),

    # Compute — three named contracts.
    ContractDefinition(
        name="default-CEL",
        category=Category.COMPUTE,
        tier=Tier.TIER_1,
        naming="named",
        description="Pure expression evaluation. Synchronous, "
                    "deterministic. Implicit when source has no "
                    "Provider is line. See BRD §6.3.1.",
    ),
    ContractDefinition(
        name="llm",
        category=Category.COMPUTE,
        tier=Tier.TIER_1,
        naming="named",
        description="Single-shot prompt → completion. No tool surface. "
                    "Streaming supported. See BRD §6.3.2.",
    ),
    ContractDefinition(
        name="ai-agent",
        category=Category.COMPUTE,
        tier=Tier.TIER_1,
        naming="named",
        description="Multi-action autonomous behavior with closed tool "
                    "surface. Streamable. See BRD §6.3.3.",
    ),

    # Channels — four named contracts.
    ContractDefinition(
        name="webhook",
        category=Category.CHANNELS,
        tier=Tier.TIER_2,
        naming="named",
        description="Outbound HTTP. Target URL in deploy config, "
                    "never source. See BRD §6.4.1.",
    ),
    ContractDefinition(
        name="email",
        category=Category.CHANNELS,
        tier=Tier.TIER_2,
        naming="named",
        description="Outbound email. SMTP credentials, default-from, "
                    "and recipients in deploy config. See BRD §6.4.2.",
    ),
    ContractDefinition(
        name="messaging",
        category=Category.CHANNELS,
        tier=Tier.TIER_2,
        naming="named",
        description="Chat platforms (Slack, Teams, Discord, etc.). "
                    "Target channel in deploy config. See BRD §6.4.3.",
    ),
    ContractDefinition(
        name="event-stream",
        category=Category.CHANNELS,
        tier=Tier.TIER_2,
        naming="named",
        description="Server-sent events / WebSocket for external "
                    "consumers. Internal Termin-to-Termin event "
                    "propagation uses the distributed runtime, not "
                    "this contract. See BRD §6.4.4.",
    ),
)


@dataclass
class ContractRegistry:
    """Catalog of contracts. Phase 0 is read-only and pre-populated.

    Use ContractRegistry.default() to get the built-in catalog.
    Phase 1+ may extend this if vetted-third-party contracts are
    added — but the closed-primitive principle (Tenet 4) means new
    contracts are rare and require BRD evolution.
    """
    contracts: tuple[ContractDefinition, ...] = field(default_factory=tuple)

    @classmethod
    def default(cls) -> "ContractRegistry":
        """Built-in catalog per BRD §4 and §6."""
        return cls(contracts=_BUILTIN_CONTRACTS)

    def categories(self) -> Iterable[Category]:
        seen = set()
        for c in self.contracts:
            if c.category not in seen:
                seen.add(c.category)
                yield c.category

    def contracts_in(self, category: Category) -> tuple[ContractDefinition, ...]:
        return tuple(c for c in self.contracts if c.category == category)

    def get_contract(
        self, category: Category, name: str
    ) -> Optional[ContractDefinition]:
        for c in self.contracts:
            if c.category == category and c.name == name:
                return c
        return None

    def has_contract(self, category: Category, name: str) -> bool:
        return self.get_contract(category, name) is not None
