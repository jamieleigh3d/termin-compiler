# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 5c.4: Airlock proving ground.

The proving ground stitches together everything 5c.1–5c.3 shipped:

  * 5c.1 — load `examples-dev/contract_packages/airlock-components.yaml`
    via `load_contract_packages_into_registry`
  * 5c.2 — parse a small Airlock-shaped source that uses the package's
    source-verbs (`Show a cosmic orb of <state-ref>`); the parser
    classifies the line as `package_contract_line` and produces a
    `PackageContractCall` AST node
  * 5c.3 — instantiate a stub Airlock provider and let
    `_populate_presentation_providers` fan out a namespace binding to
    every contract the package declares

Per design doc Q6 the v0.9 stub provider renders each component as a
labeled `<div data-airlock-component="...">` placeholder — real visuals
are out of scope; this slice's job is to prove the dispatch mechanism
end-to-end, which means: source → IR → bound provider lookup → render
call. We assert each step lands.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Mapping, Optional
from unittest.mock import MagicMock

import pytest

from termin.contract_packages import load_contract_packages_into_registry
from termin.peg_parser import parse_peg
from termin.ast_nodes import PackageContractCall
from termin.lower import lower
from termin.analyzer import analyze


# ── Fixture: the airlock-components package fixture ──

_AIRLOCK_PKG = (
    Path(__file__).parent.parent
    / "examples-dev" / "contract_packages" / "airlock-components.yaml"
)


@pytest.fixture
def airlock_registry():
    if not _AIRLOCK_PKG.exists():
        pytest.skip("Airlock package fixture not present")
    return load_contract_packages_into_registry([_AIRLOCK_PKG])


# ── Stub provider for Airlock contracts ──

class _StubAirlockProvider:
    """Placeholder renderer for the airlock-components namespace.

    Per design doc Q6: render each component as a labeled
    `<div data-airlock-component="...">` placeholder. Real visuals are
    out of scope; this proves the bound-provider lookup works
    structurally.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self.declared_contracts = (
            "airlock-components.cosmic-orb",
            "airlock-components.airlock-terminal",
            "airlock-components.scenario-narrative",
        )
        self.render_modes = ("ssr",)

    def render_ssr(
        self, contract: str, ir_fragment: Any,
        data: Any, principal_context: Any,
    ) -> str:
        bindings = {}
        if isinstance(ir_fragment, dict):
            bindings = ir_fragment.get("props", {}).get("bindings", {})
        # Render placeholder; tests assert this shape.
        return (
            f'<div data-airlock-component="{contract}" '
            f'data-bindings=\'{bindings!r}\'>airlock placeholder: {contract}</div>'
        )

    def csr_bundle_url(self) -> Optional[str]:
        return None


# ── End-to-end stages ──

def test_airlock_package_loads_clean(airlock_registry):
    """5c.1 stage: the YAML fixture loads, all three contracts visible."""
    assert "airlock-components" in airlock_registry.namespaces()
    contracts = airlock_registry.source_verbs()
    assert "Show a cosmic orb of <state-ref>" in contracts
    assert "Show an airlock terminal for <command-set>" in contracts
    assert "Show scenario narrative from <content-ref>" in contracts


def test_airlock_source_parses_with_registry(airlock_registry):
    """5c.2 stage: a source line matching a registered verb produces a
    PackageContractCall node with the qualified name and bindings."""
    source = textwrap.dedent("""
        Application: Airlock Demo
          Description: Proving ground
        Id: 12345678-1234-1234-1234-123456789abc

        Identity:
          Scopes are "play"
          Anonymous has "play"

        Content called "scenarios":
          Each scenario has a name which is text
          Anyone with "play" can view scenarios

        As an anonymous, I want to see the scenario so that I can play:
            Show a page called "Scenario"
            Show a cosmic orb of scenarios
    """).strip()

    program, result = parse_peg(source, contract_package_registry=airlock_registry)
    assert result.ok, [(e.line, e.message) for e in result.errors]

    body = []
    for s in program.stories:
        body.extend(getattr(s, "directives", None) or getattr(s, "body", []))
    pkg_calls = [d for d in body if isinstance(d, PackageContractCall)]
    assert len(pkg_calls) == 1
    call = pkg_calls[0]
    assert call.qualified_name == "airlock-components.cosmic-orb"
    assert call.bindings == {"state-ref": "scenarios"}


def test_airlock_lowering_emits_package_contract_node(airlock_registry):
    """The full compile pipeline (parse → analyze → lower) produces an
    IR with a ComponentNode whose contract is the qualified Airlock
    name and whose props include the matched bindings."""
    source = textwrap.dedent("""
        Application: Airlock Demo
          Description: Proving ground
        Id: 12345678-1234-1234-1234-123456789abd

        Identity:
          Scopes are "play"
          Anonymous has "play"

        Content called "scenarios":
          Each scenario has a name which is text
          Anyone with "play" can view scenarios

        As an anonymous, I want to see the scenario so that I can play:
            Show a page called "Scenario"
            Show a cosmic orb of scenarios
    """).strip()

    program, parse_result = parse_peg(
        source, contract_package_registry=airlock_registry
    )
    assert parse_result.ok
    analyze_result = analyze(program)
    assert analyze_result.ok, [
        (e.line, e.message) for e in analyze_result.errors
    ]
    spec = lower(program)
    # Walk the lowered pages for a node with the airlock contract.
    found_node = None
    for page in spec.pages:
        for node in page.children:
            if getattr(node, "contract", "") == "airlock-components.cosmic-orb":
                found_node = node
                break
        if found_node:
            break
    assert found_node is not None, (
        "Lowering should emit a ComponentNode with contract = "
        "'airlock-components.cosmic-orb' for `Show a cosmic orb of "
        "scenarios`"
    )
    # Bindings flow through as props (both the bindings dict and the
    # ergonomic top-level keys).
    assert found_node.props.get("bindings") == {"state-ref": "scenarios"}
    assert found_node.props.get("state-ref") == "scenarios"


def test_airlock_required_contracts_includes_package_namespace(airlock_registry):
    """5c.4 stage: lowering aggregates required_contracts from
    `node.contract`, so the airlock contract appears in the IR's
    required_contracts manifest. Deploy-time validation (BRD §8.5)
    consults this to fail-closed when no provider is bound."""
    source = textwrap.dedent("""
        Application: Airlock Demo
          Description: Proving ground
        Id: 12345678-1234-1234-1234-123456789abe

        Identity:
          Scopes are "play"
          Anonymous has "play"

        Content called "scenarios":
          Each scenario has a name which is text
          Anyone with "play" can view scenarios

        As an anonymous, I want to see the scenario so that I can play:
            Show a page called "Scenario"
            Show a cosmic orb of scenarios
    """).strip()

    program, _ = parse_peg(source, contract_package_registry=airlock_registry)
    assert _.ok
    analyze_result = analyze(program)
    assert analyze_result.ok
    spec = lower(program)
    assert "airlock-components.cosmic-orb" in spec.required_contracts


def test_airlock_provider_dispatch_proving_ground(airlock_registry):
    """5c.4 stage: with the package loaded and an Airlock provider
    registered, `_populate_presentation_providers` binds every Airlock
    contract to the provider via a namespace binding. Final stage of
    the proving ground.
    """
    from termin_server.app import _populate_presentation_providers
    from termin_server.providers import (
        Category, ContractRegistry, ProviderRegistry,
    )
    from termin_server.providers.contracts import (
        ContractDefinition, Tier,
    )

    contracts = ContractRegistry.default()
    # Register the three airlock contracts so the provider registry
    # accepts the registration. In production this would happen via
    # a startup hook from the provider's package.
    for short in ("cosmic-orb", "airlock-terminal", "scenario-narrative"):
        contracts.register_contract(ContractDefinition(
            name=f"airlock-components.{short}",
            category=Category.PRESENTATION,
            tier=Tier.TIER_2,
            naming="named",
            description="Airlock proving ground stub",
        ))

    registry = ProviderRegistry()
    instances = []

    def factory(config):
        prov = _StubAirlockProvider(config)
        instances.append(prov)
        return prov
    for short in ("cosmic-orb", "airlock-terminal", "scenario-narrative"):
        registry.register(
            Category.PRESENTATION,
            f"airlock-components.{short}",
            "airlock-stub",
            factory,
        )

    class _Ctx:
        contract_package_registry = airlock_registry
        presentation_providers = []

    ctx = _Ctx()
    deploy_config = {
        "bindings": {"presentation": {
            "airlock-components": {"provider": "airlock-stub", "config": {}},
            # Tailwind handles presentation-base so no synthesis happens.
            "presentation-base": {"provider": "tailwind-default", "config": {}},
        }}
    }
    _populate_presentation_providers(ctx, deploy_config, registry, contracts)

    # All three airlock contracts should be bound to airlock-stub.
    bound_airlock = {
        c for c, p, _ in ctx.presentation_providers
        if p == "airlock-stub"
    }
    expected = {
        "airlock-components.cosmic-orb",
        "airlock-components.airlock-terminal",
        "airlock-components.scenario-narrative",
    }
    assert bound_airlock == expected
    # One factory call per product (cached across contracts).
    assert len(instances) == 1
    # The bound instance is a real _StubAirlockProvider.
    assert isinstance(instances[0], _StubAirlockProvider)
    # Render call returns the placeholder with the contract name in.
    rendered = instances[0].render_ssr(
        "airlock-components.cosmic-orb",
        {"props": {"bindings": {"state-ref": "scenarios"}}},
        None, None,
    )
    assert "airlock-components.cosmic-orb" in rendered
    assert "scenarios" in rendered
