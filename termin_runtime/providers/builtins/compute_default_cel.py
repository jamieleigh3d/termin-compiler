# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Default CEL compute provider — first-party plugin against the v0.9
default-CEL contract surface (BRD §6.3.1).

Wraps the runtime's existing ExpressionEvaluator (cel-python with the
Termin-defined system functions). Implicit contract — applies whenever
source has no `Provider is` line on a Compute block.

The `default-CEL` contract is symbol-environment-agnostic. Different
runtime sites supply different bound-symbol environments (compute
body, trigger filter, event handler condition, pre/postcondition);
this provider doesn't know which it's serving and shouldn't.

Loaded through the same ProviderRegistry mechanism third-party
providers use — see `register_default_cel` below.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts import Category, ContractRegistry


class DefaultCelProvider:
    """Pure CEL evaluation. Synchronous, deterministic.

    Configuration: none required. Custom CEL functions can be
    registered via `register_function`, but in v0.9 the runtime ships
    a fixed catalog (see expression.SYSTEM_FUNCTIONS) — third-party
    extension is post-v0.9.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        # Lazy-import the evaluator to avoid pulling celpy into
        # contract-only test paths (some tests just check the registry
        # without needing an actual evaluator).
        from ...expression import ExpressionEvaluator
        self._config = dict(config or {})
        self._evaluator = ExpressionEvaluator()

    def evaluate(
        self,
        expression: str,
        bound_symbols: Mapping[str, Any],
    ) -> Any:
        """Evaluate the CEL expression against the bound symbols.

        The runtime's ExpressionEvaluator already injects dynamic
        system context (now, today) on each call, so callers don't
        need to supply those.

        Errors propagate as the underlying celpy errors — runtime
        wraps them into TerminAtor-style structured errors before
        surfacing to the caller's source code.
        """
        return self._evaluator.evaluate(expression, dict(bound_symbols))


# ── Registration ──


def _default_cel_factory(config: Mapping[str, Any]) -> DefaultCelProvider:
    """Factory used by the ProviderRegistry to construct an instance
    when an app's deploy config binds default-CEL to 'default-cel'.

    For the implicit default-CEL contract, the binding is automatic —
    runtime constructs this product whenever a compute has no
    `Provider is` line. No explicit deploy-config entry needed.
    """
    return DefaultCelProvider(config)


def register_default_cel(
    provider_registry, contract_registry: ContractRegistry | None = None
) -> None:
    """Register the default-CEL provider with a ProviderRegistry.

    Same registration path third-party providers will use — no
    runtime-internal special casing. Pass the contract_registry to
    enable shape validation; first-party registration always passes it.
    """
    provider_registry.register(
        category=Category.COMPUTE,
        contract_name="default-CEL",
        product_name="default-cel",
        factory=_default_cel_factory,
        conformance="passing",
        version="0.9.0",
        contract_registry=contract_registry,
    )
