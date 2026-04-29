# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the entry-point discovery + presentation_providers
population helpers (v0.9 Phase 5b.4 B' loop).

Both helpers are private to app.py but their behavior is the
contract the integration depends on — exercise them directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from termin_runtime.app import (
    _discover_external_providers,
    _populate_presentation_providers,
)
from termin_runtime.providers import (
    Category, ContractRegistry, ProviderRegistry,
)
from termin_runtime.providers.presentation_contract import (
    PRESENTATION_BASE_CONTRACTS,
)


# ── Fixtures ──

class _Ctx:
    def __init__(self):
        self.presentation_providers = []


def _registry_with_fake_provider(product_name="fake-spectrum"):
    """Set up a registry with a fake provider registered against all
    ten presentation-base contracts (mirrors how Tailwind-default
    registers in built-ins, mirrors how Spectrum will once it ships).
    """
    contracts = ContractRegistry.default()
    registry = ProviderRegistry()
    instance_holder = {"created": []}

    def factory(config):
        provider = MagicMock(name=f"{product_name}-instance")
        provider.declared_contracts = tuple(
            f"presentation-base.{n}" for n in PRESENTATION_BASE_CONTRACTS
        )
        provider.render_modes = ("csr",)
        provider.csr_bundle_url = MagicMock(
            return_value=f"/_termin/providers/{product_name}/bundle.js"
        )
        provider._config_seen = config  # for assertion
        instance_holder["created"].append(provider)
        return provider

    for name in PRESENTATION_BASE_CONTRACTS:
        registry.register(
            Category.PRESENTATION,
            f"presentation-base.{name}",
            product_name,
            factory,
        )
    return registry, contracts, instance_holder


# ── Population ──

def test_populate_with_namespace_binding_expands_to_all_contracts():
    """A `presentation-base` namespace binding fans out to all ten
    contracts in that namespace."""
    registry, contracts, instances = _registry_with_fake_provider()
    ctx = _Ctx()
    deploy_config = {
        "bindings": {
            "presentation": {
                "presentation-base": {
                    "provider": "fake-spectrum",
                    "config": {},
                }
            }
        }
    }
    _populate_presentation_providers(ctx, deploy_config, registry, contracts)
    bound_contracts = {c for c, _, _ in ctx.presentation_providers}
    expected = {
        f"presentation-base.{n}" for n in PRESENTATION_BASE_CONTRACTS
    }
    assert bound_contracts == expected


def test_populate_caches_one_instance_per_product():
    """Calling the factory ten times for the same product is wasteful —
    one instance per product, used across all its contracts."""
    registry, contracts, instances = _registry_with_fake_provider()
    ctx = _Ctx()
    _populate_presentation_providers(
        ctx,
        {"bindings": {"presentation": {"presentation-base": {
            "provider": "fake-spectrum", "config": {}}}}},
        registry, contracts,
    )
    assert len(instances["created"]) == 1
    instance = instances["created"][0]
    # Every triple references the same instance.
    instances_in_ctx = {id(p) for _, _, p in ctx.presentation_providers}
    assert instances_in_ctx == {id(instance)}


def test_populate_passes_config_to_factory():
    """Config dict from the binding flows through to the factory."""
    registry, contracts, instances = _registry_with_fake_provider()
    ctx = _Ctx()
    _populate_presentation_providers(
        ctx,
        {"bindings": {"presentation": {"presentation-base": {
            "provider": "fake-spectrum",
            "config": {"bundle_url_override": "https://cdn.test/x.js"}}}}},
        registry, contracts,
    )
    assert instances["created"][0]._config_seen == {
        "bundle_url_override": "https://cdn.test/x.js"
    }


def test_populate_per_contract_binding_targets_one():
    """A binding keyed on a fully-qualified contract name targets only
    that contract; namespace expansion is skipped."""
    registry, contracts, _ = _registry_with_fake_provider()
    ctx = _Ctx()
    _populate_presentation_providers(
        ctx,
        {"bindings": {"presentation": {"presentation-base.text": {
            "provider": "fake-spectrum", "config": {}}}}},
        registry, contracts,
    )
    bound_contracts = [c for c, _, _ in ctx.presentation_providers]
    assert bound_contracts == ["presentation-base.text"]


def test_populate_skips_unregistered_products():
    """Binding to a product nobody registered → no triple emitted; the
    runtime later fails closed at deploy-time validation."""
    registry, contracts, _ = _registry_with_fake_provider()
    ctx = _Ctx()
    _populate_presentation_providers(
        ctx,
        {"bindings": {"presentation": {"presentation-base": {
            "provider": "ghost", "config": {}}}}},
        registry, contracts,
    )
    assert ctx.presentation_providers == []


def test_populate_no_bindings_synthesizes_tailwind_default():
    """v0.9 Phase 5b.3: an empty deploy config (no presentation
    bindings) now synthesizes a default `tailwind-default` binding for
    the `presentation-base` namespace. This makes
    `ctx.presentation_providers` symmetric with the explicit-binding
    case — `page_should_use_shell` and the bundle-discovery endpoint
    can read a uniform shape regardless of whether deploy config
    names a provider.

    Tailwind isn't registered in the fake registry above, so the
    synthesized binding skips at the factory-lookup step; no triple
    is emitted. With the real built-in registration in place
    (register_builtins → register_tailwind_default), the populated
    list contains all ten presentation-base.* contracts bound to
    tailwind-default.
    """
    registry, contracts, _ = _registry_with_fake_provider()
    ctx = _Ctx()
    _populate_presentation_providers(ctx, {}, registry, contracts)
    # Fake registry has fake-spectrum but no tailwind-default →
    # synthesized binding skips. The fan-out logic still ran.
    assert ctx.presentation_providers == []


def test_populate_no_bindings_uses_real_tailwind_when_registered():
    """When the real Tailwind builtin is registered (the production
    case), the no-bindings synthesis populates ctx.presentation_providers
    with all ten presentation-base contracts bound to tailwind-default.
    """
    from termin_runtime.providers.builtins.presentation_tailwind_default import (
        register_tailwind_default,
    )
    contracts = ContractRegistry.default()
    registry = ProviderRegistry()
    register_tailwind_default(registry, contracts)

    ctx = _Ctx()
    _populate_presentation_providers(ctx, {}, registry, contracts)
    bound_contracts = {c for c, _, _ in ctx.presentation_providers}
    expected = {
        f"presentation-base.{n}" for n in PRESENTATION_BASE_CONTRACTS
    }
    assert bound_contracts == expected
    products = {p for _, p, _ in ctx.presentation_providers}
    assert products == {"tailwind-default"}


def test_populate_explicit_binding_overrides_default_synthesis():
    """v0.9 Phase 5b.3: when deploy config DOES bind the
    presentation-base namespace, the synthesis is skipped — the
    explicit binding wins. Belt-and-braces against the synthesis
    accidentally polluting deploy-config intent.
    """
    registry, contracts, instances = _registry_with_fake_provider()
    ctx = _Ctx()
    _populate_presentation_providers(
        ctx,
        {"bindings": {"presentation": {"presentation-base": {
            "provider": "fake-spectrum", "config": {}}}}},
        registry, contracts,
    )
    products = {p for _, p, _ in ctx.presentation_providers}
    assert products == {"fake-spectrum"}, (
        "Explicit binding to fake-spectrum should win over the "
        "tailwind-default synthesis."
    )


def test_tailwind_default_declared_in_setup_entry_points():
    """v0.9 Phase 5b.3 Tailwind-as-plug-in migration: tailwind-default
    must be declared under the `termin.providers` entry-point group in
    setup.py so it loads through the same discovery path Spectrum and
    other third-party providers use. This is the structural assertion
    that the migration landed; behavior is covered by the
    `_uses_real_tailwind_when_registered` test above.
    """
    from pathlib import Path
    setup_py = (
        Path(__file__).parent.parent / "setup.py"
    ).read_text(encoding="utf-8")
    assert '"termin.providers"' in setup_py, (
        "setup.py should declare a `termin.providers` entry-point group "
        "so the tailwind-default first-party provider goes through the "
        "same discovery path third-party providers (Spectrum) use."
    )
    assert "tailwind-default = " in setup_py, (
        "setup.py should declare an entry-point named `tailwind-default` "
        "pointing at register_tailwind_default."
    )
    assert (
        "termin_runtime.providers.builtins.presentation_tailwind_default"
        in setup_py
    ), (
        "Entry-point target should resolve to the existing built-in "
        "registration function — no parallel implementation."
    )


def test_populate_alternate_top_level_shape():
    """BRD §11.2 also shows `presentation.bindings.<key>` (not nested
    under top-level `bindings`). Both shapes accepted."""
    registry, contracts, _ = _registry_with_fake_provider()
    ctx = _Ctx()
    _populate_presentation_providers(
        ctx,
        {"presentation": {"bindings": {"presentation-base": {
            "provider": "fake-spectrum", "config": {}}}}},
        registry, contracts,
    )
    assert len(ctx.presentation_providers) == len(PRESENTATION_BASE_CONTRACTS)


# ── Discovery ──

def test_discover_external_providers_calls_each_entry_point(monkeypatch):
    """Each entry-point's `register_<product>` callable gets invoked
    with the registry + contracts."""
    contracts = ContractRegistry.default()
    registry = ProviderRegistry()

    calls = []

    def fake_register(provider_registry, contract_registry):
        calls.append((provider_registry, contract_registry))

    fake_ep = MagicMock()
    fake_ep.name = "spectrum"
    fake_ep.load = MagicMock(return_value=fake_register)

    # Patch importlib.metadata.entry_points to return our fake.
    import importlib.metadata
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kwargs: [fake_ep] if kwargs.get("group") == "termin.providers" else [],
    )
    _discover_external_providers(registry, contracts)
    assert len(calls) == 1
    assert calls[0] == (registry, contracts)


def test_discover_external_providers_logs_and_continues_on_error(
    monkeypatch, capsys
):
    """A registration that raises should print a warning but not crash
    startup — other providers shouldn't pay the price for one bad
    install."""
    contracts = ContractRegistry.default()
    registry = ProviderRegistry()

    def boom(provider_registry, contract_registry):
        raise RuntimeError("install was incomplete")

    bad_ep = MagicMock()
    bad_ep.name = "broken-provider"
    bad_ep.load = MagicMock(return_value=boom)

    import importlib.metadata
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kwargs: [bad_ep] if kwargs.get("group") == "termin.providers" else [],
    )
    _discover_external_providers(registry, contracts)
    captured = capsys.readouterr()
    assert "broken-provider" in captured.out
    assert "install was incomplete" in captured.out


def test_discover_external_providers_no_entry_points_is_quiet(monkeypatch):
    """No installed providers → silent no-op; no spurious warnings."""
    contracts = ContractRegistry.default()
    registry = ProviderRegistry()
    import importlib.metadata
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kwargs: [],
    )
    before = registry.all_records()
    _discover_external_providers(registry, contracts)
    after = registry.all_records()
    # Nothing to assert beyond "didn't crash, didn't add anything."
    assert before == after
