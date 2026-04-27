# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tailwind-default presentation provider (v0.9 Phase 5a.2).

The first-party reference renderer for the `presentation-base`
namespace. Per BRD #2 §9.1, `tailwind-default` subsumes the stub
provider role required by BRD #1 §10 — when a deploy config does
not bind a Presentation provider, the runtime falls back to this
one.

Scope: SSR mode only in v0.9. CSR mode is on the technical-debt
list (`docs/termin-roadmap.md` Forward-looking but unwired); it
lands alongside Carbon (Phase 5b), which brings the
Termin.registerRenderer(...) extension surface in termin.js.

Implementation strategy (per design doc §3.9): wrap the existing
`termin_runtime.presentation` renderer functions verbatim. The
1525+ tests that already cover those functions are the safety net.
A future cleanup pass can absorb their HTML generation directly
into the provider when the legacy `presentation.render_component`
entry point retires.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ..contracts import Category, ContractRegistry
from ..presentation_contract import (
    PRESENTATION_BASE_CONTRACTS,
    PresentationData,
    PrincipalContext,
    register_presentation_base_contracts,
)


class TailwindDefaultProvider:
    """The reference SSR-only Tailwind renderer.

    Covers all ten `presentation-base.<contract>` names.
    Every render_ssr call dispatches to the legacy
    `termin_runtime.presentation.render_component(node)` entry
    point — the existing Jinja2-template-fragment generators stay
    in place; this class is the new contract-shaped seam over
    them.

    Configuration:
      None in v0.9 (Tailwind class strings are baked into the
      legacy renderer functions). Phase 5a.5's colorblind-safety
      conformance work will add a palette config dict; the shape
      is locked then.
    """

    declared_contracts: tuple[str, ...]
    render_modes: tuple[str, ...]

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        # All ten presentation-base contracts, fully qualified.
        self.declared_contracts = tuple(
            f"presentation-base.{name}"
            for name in PRESENTATION_BASE_CONTRACTS
        )
        # SSR only for v0.9 — see module docstring.
        self.render_modes = ("ssr",)

    def render_ssr(
        self,
        contract: str,
        ir_fragment: Any,
        data: PresentationData,
        principal_context: PrincipalContext,
    ) -> str:
        """Render one component-IR fragment to HTML.

        Delegates to the legacy `presentation.render_component(node)`
        entry. The `data` and `principal_context` parameters are
        accepted but not yet consumed — the legacy path reads from
        the Jinja template context that the runtime sets up. Phase
        5a.5+ will thread principal_context.theme_preference through
        the rendered output once the CSS-variable theme story lands.

        ir_fragment is expected to be a dict (the JSON-deserialized
        ComponentNode shape). Direct ComponentNode dataclass instances
        are also accepted via dict(...) coercion through to_dict.
        """
        # Lazy import: presentation.py pulls in jinja2 + a lot of
        # template setup. Importing at module top would slow down
        # every test that touches the providers package.
        from termin_runtime.presentation import render_component

        # Accept either a dict-shape or a dataclass instance with a
        # `__dict__`-style projection. The legacy renderer expects
        # dict.
        if hasattr(ir_fragment, "type") and not isinstance(ir_fragment, dict):
            # Best-effort projection from a ComponentNode-shaped
            # object. Direct callers (5a.2 internal tests) usually
            # pass dicts already.
            ir_fragment = {
                "type": getattr(ir_fragment, "type", ""),
                "contract": getattr(ir_fragment, "contract", ""),
                "props": dict(getattr(ir_fragment, "props", {})),
                "style": dict(getattr(ir_fragment, "style", {})),
                "layout": dict(getattr(ir_fragment, "layout", {})),
                "children": list(getattr(ir_fragment, "children", [])),
            }
        return render_component(ir_fragment)

    def csr_bundle_url(self) -> Optional[str]:
        """SSR-only in v0.9 — no CSR bundle to load."""
        return None


# ── Registration ──

def _tailwind_default_factory(config: Mapping[str, Any]) -> TailwindDefaultProvider:
    """Factory invoked by the ProviderRegistry when a deploy config
    binds a `presentation-base.<contract>` to product `tailwind-default`."""
    return TailwindDefaultProvider(config)


def register_tailwind_default(
    provider_registry, contract_registry: ContractRegistry | None = None
) -> None:
    """Register the Tailwind-default provider against all ten
    `presentation-base.<contract>` names.

    Per BRD §9.1 the provider implements the full namespace. One
    factory function, one product name (`tailwind-default`), ten
    registrations — the same pattern third-party multi-contract
    providers will use.

    Side effect: also registers the ten presentation-base contracts
    in the contract_registry if one is provided. Idempotent — calling
    twice is a ValueError per ContractRegistry.register_contract,
    so callers ensure register_tailwind_default fires exactly once
    (typically from register_builtins at app startup).
    """
    if contract_registry is not None:
        # Register the ten contracts so deploy-time validation can
        # check `provider.declared_contracts` against them.
        try:
            register_presentation_base_contracts(contract_registry)
        except ValueError:
            # Already registered (e.g., test isolation re-uses the
            # default registry). Tolerate.
            pass

    for contract_short in PRESENTATION_BASE_CONTRACTS:
        full_name = f"presentation-base.{contract_short}"
        provider_registry.register(
            category=Category.PRESENTATION,
            contract_name=full_name,
            product_name="tailwind-default",
            factory=_tailwind_default_factory,
            conformance="passing",
            version="0.9.0",
            contract_registry=contract_registry,
        )
