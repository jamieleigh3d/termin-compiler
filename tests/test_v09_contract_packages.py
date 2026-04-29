# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5c.1: YAML contract package format and loader.

Per BRD #2 §10 and Appendix C: contract packages declare new
component types in a new namespace. Each package is a YAML
document with a top-level `namespace`, `version`, and `contracts`
list; each contract specifies its source-verb, modifiers,
data-shape, actions, and principal-context. Optional `extends`
relates a contract to a base in another namespace (override mode).

This slice ships the load + validation layer only. The two-pass
compiler integration (5b.2) and runtime provider dispatch (5c.3)
that consume packages land in subsequent slices.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


# ── Well-formed packages parse cleanly ──

def test_load_minimal_package(tmp_path: Path):
    from termin.contract_packages import load_contract_package

    src = textwrap.dedent("""
        namespace: airlock-components
        version: 0.1.0
        description: Airlock escape-room presentation components
        contracts:
          - name: cosmic-orb
            source-verb: "Show a cosmic orb of <state-ref>"
            modifiers:
              - "Pulse on event <event-name>"
              - "Color by <state-field>"
            data-shape:
              state-record:
                type: content-record
                confidentiality-filtered: true
            actions:
              - name: orb-clicked
                payload:
                  state-id: id
            principal-context:
              - role-set
              - theme-preference
    """).strip()
    p = tmp_path / "airlock-components.yaml"
    p.write_text(src, encoding="utf-8")

    pkg = load_contract_package(p)
    assert pkg.namespace == "airlock-components"
    assert pkg.version == "0.1.0"
    assert pkg.description == "Airlock escape-room presentation components"
    assert len(pkg.contracts) == 1
    contract = pkg.contracts[0]
    assert contract.name == "cosmic-orb"
    assert contract.source_verb.startswith("Show a cosmic orb of")
    assert "Pulse on event <event-name>" in contract.modifiers
    assert contract.extends is None
    assert "role-set" in contract.principal_context


def test_load_multi_contract_package(tmp_path: Path):
    from termin.contract_packages import load_contract_package

    src = textwrap.dedent("""
        namespace: airlock-components
        version: 0.2.0
        contracts:
          - name: cosmic-orb
            source-verb: "Show a cosmic orb of <state-ref>"
            modifiers: []
            data-shape: {}
            actions: []
            principal-context: [role-set]
          - name: airlock-terminal
            source-verb: "Show an airlock terminal for <command-set>"
            modifiers:
              - "History limit <number>"
            data-shape: {}
            actions:
              - name: command-submitted
                payload:
                  command: string
            principal-context: [role-set, scope-membership]
    """).strip()
    p = tmp_path / "pkg.yaml"
    p.write_text(src, encoding="utf-8")

    pkg = load_contract_package(p)
    assert len(pkg.contracts) == 2
    names = [c.name for c in pkg.contracts]
    assert names == ["cosmic-orb", "airlock-terminal"]


def test_extends_field_carried_through(tmp_path: Path):
    from termin.contract_packages import load_contract_package

    src = textwrap.dedent("""
        namespace: acme-ui
        version: 1.0.0
        contracts:
          - name: premium-table
            source-verb: ""
            extends: presentation-base.data-table
            modifiers:
              - "Show density toggle"
            data-shape: {}
            actions: []
            principal-context: [role-set]
    """).strip()
    p = tmp_path / "acme-ui.yaml"
    p.write_text(src, encoding="utf-8")

    pkg = load_contract_package(p)
    contract = pkg.contracts[0]
    assert contract.extends == "presentation-base.data-table"
    assert "Show density toggle" in contract.modifiers


# ── Validation: required fields ──

def test_missing_namespace_raises(tmp_path: Path):
    from termin.contract_packages import (
        ContractPackageError, load_contract_package,
    )

    src = "version: 1.0.0\ncontracts: []\n"
    p = tmp_path / "bad.yaml"
    p.write_text(src, encoding="utf-8")

    with pytest.raises(ContractPackageError, match=r"namespace"):
        load_contract_package(p)


def test_missing_version_raises(tmp_path: Path):
    from termin.contract_packages import (
        ContractPackageError, load_contract_package,
    )

    src = "namespace: x\ncontracts: []\n"
    p = tmp_path / "bad.yaml"
    p.write_text(src, encoding="utf-8")

    with pytest.raises(ContractPackageError, match=r"version"):
        load_contract_package(p)


def test_missing_contracts_raises(tmp_path: Path):
    from termin.contract_packages import (
        ContractPackageError, load_contract_package,
    )

    src = "namespace: x\nversion: 1.0.0\n"
    p = tmp_path / "bad.yaml"
    p.write_text(src, encoding="utf-8")

    with pytest.raises(ContractPackageError, match=r"contracts"):
        load_contract_package(p)


def test_contract_missing_name_raises(tmp_path: Path):
    from termin.contract_packages import (
        ContractPackageError, load_contract_package,
    )

    src = textwrap.dedent("""
        namespace: x
        version: 1.0.0
        contracts:
          - source-verb: "Show a foo of <ref>"
            modifiers: []
            data-shape: {}
            actions: []
            principal-context: []
    """).strip()
    p = tmp_path / "bad.yaml"
    p.write_text(src, encoding="utf-8")

    with pytest.raises(ContractPackageError, match=r"name"):
        load_contract_package(p)


def test_contract_missing_source_verb_for_non_extends_raises(tmp_path: Path):
    """A contract without `extends` MUST have a non-empty source-verb
    (it's a wholly-new component type per BRD #2 §10.2). With
    `extends`, source-verb may be empty (drop-in mode)."""
    from termin.contract_packages import (
        ContractPackageError, load_contract_package,
    )

    src = textwrap.dedent("""
        namespace: x
        version: 1.0.0
        contracts:
          - name: floating-orb
            source-verb: ""
            modifiers: []
            data-shape: {}
            actions: []
            principal-context: []
    """).strip()
    p = tmp_path / "bad.yaml"
    p.write_text(src, encoding="utf-8")

    with pytest.raises(ContractPackageError, match=r"source-verb"):
        load_contract_package(p)


# ── Validation: verb collision within a package ──

def test_duplicate_source_verb_in_same_package_raises(tmp_path: Path):
    """BRD §4.5 verb collision rule applies across packages, but the
    same collision within one package is also a hard error."""
    from termin.contract_packages import (
        ContractPackageError, load_contract_package,
    )

    src = textwrap.dedent("""
        namespace: x
        version: 1.0.0
        contracts:
          - name: alpha
            source-verb: "Show a thing of <ref>"
            modifiers: []
            data-shape: {}
            actions: []
            principal-context: []
          - name: beta
            source-verb: "Show a thing of <ref>"
            modifiers: []
            data-shape: {}
            actions: []
            principal-context: []
    """).strip()
    p = tmp_path / "dup.yaml"
    p.write_text(src, encoding="utf-8")

    with pytest.raises(ContractPackageError, match=r"duplicate"):
        load_contract_package(p)


# ── Validation: cross-package verb collision detection ──

def test_cross_package_verb_collision_detected(tmp_path: Path):
    """Loading a second package whose verb conflicts with the first
    raises per BRD §4.5. Both colliding packages must be named in
    the error message."""
    from termin.contract_packages import (
        ContractPackageError, load_contract_packages_into_registry,
    )

    pkg_a_src = textwrap.dedent("""
        namespace: pkg-a
        version: 1.0.0
        contracts:
          - name: orb
            source-verb: "Show a thing of <ref>"
            modifiers: []
            data-shape: {}
            actions: []
            principal-context: []
    """).strip()
    pkg_b_src = textwrap.dedent("""
        namespace: pkg-b
        version: 1.0.0
        contracts:
          - name: blob
            source-verb: "Show a thing of <ref>"
            modifiers: []
            data-shape: {}
            actions: []
            principal-context: []
    """).strip()
    a = tmp_path / "a.yaml"; a.write_text(pkg_a_src, encoding="utf-8")
    b = tmp_path / "b.yaml"; b.write_text(pkg_b_src, encoding="utf-8")

    with pytest.raises(ContractPackageError, match=r"pkg-a.*pkg-b|pkg-b.*pkg-a"):
        load_contract_packages_into_registry([a, b])


# ── Registry: cross-package state ──

def test_registry_lookup_by_qualified_name(tmp_path: Path):
    from termin.contract_packages import load_contract_packages_into_registry

    src = textwrap.dedent("""
        namespace: airlock-components
        version: 0.1.0
        contracts:
          - name: cosmic-orb
            source-verb: "Show a cosmic orb of <ref>"
            modifiers: []
            data-shape: {}
            actions: []
            principal-context: []
    """).strip()
    p = tmp_path / "p.yaml"; p.write_text(src, encoding="utf-8")

    registry = load_contract_packages_into_registry([p])
    contract = registry.get_contract("airlock-components.cosmic-orb")
    assert contract is not None
    assert contract.name == "cosmic-orb"
    assert registry.get_contract("airlock-components.unknown") is None
    assert registry.get_contract("nonexistent.foo") is None


def test_registry_lists_namespaces(tmp_path: Path):
    from termin.contract_packages import load_contract_packages_into_registry

    pkg_a = textwrap.dedent("""
        namespace: ns-a
        version: 1.0.0
        contracts:
          - name: alpha
            source-verb: "A <ref>"
            modifiers: []
            data-shape: {}
            actions: []
            principal-context: []
    """).strip()
    pkg_b = textwrap.dedent("""
        namespace: ns-b
        version: 1.0.0
        contracts:
          - name: beta
            source-verb: "B <ref>"
            modifiers: []
            data-shape: {}
            actions: []
            principal-context: []
    """).strip()
    a = tmp_path / "a.yaml"; a.write_text(pkg_a, encoding="utf-8")
    b = tmp_path / "b.yaml"; b.write_text(pkg_b, encoding="utf-8")

    registry = load_contract_packages_into_registry([a, b])
    assert set(registry.namespaces()) == {"ns-a", "ns-b"}


def test_registry_collects_verbs_for_grammar_extension(tmp_path: Path):
    """The two-pass compiler (slice 5c.2) needs to ask the registry
    for every legal source-verb after packages are loaded so it can
    extend the grammar dispatch table."""
    from termin.contract_packages import load_contract_packages_into_registry

    src = textwrap.dedent("""
        namespace: airlock-components
        version: 0.1.0
        contracts:
          - name: cosmic-orb
            source-verb: "Show a cosmic orb of <ref>"
            modifiers: []
            data-shape: {}
            actions: []
            principal-context: []
          - name: airlock-terminal
            source-verb: "Show an airlock terminal for <command-set>"
            modifiers: []
            data-shape: {}
            actions: []
            principal-context: []
    """).strip()
    p = tmp_path / "p.yaml"; p.write_text(src, encoding="utf-8")

    registry = load_contract_packages_into_registry([p])
    verbs = registry.source_verbs()
    assert "Show a cosmic orb of <ref>" in verbs
    assert "Show an airlock terminal for <command-set>" in verbs


# ── Malformed YAML ──

def test_invalid_yaml_raises(tmp_path: Path):
    from termin.contract_packages import (
        ContractPackageError, load_contract_package,
    )

    p = tmp_path / "bad.yaml"
    p.write_text("namespace: [unterminated\n", encoding="utf-8")

    with pytest.raises(ContractPackageError, match=r"YAML"):
        load_contract_package(p)


def test_yaml_root_must_be_mapping(tmp_path: Path):
    from termin.contract_packages import (
        ContractPackageError, load_contract_package,
    )

    p = tmp_path / "bad.yaml"
    p.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(ContractPackageError, match=r"mapping|object"):
        load_contract_package(p)


# ── Airlock fixture ──

def test_airlock_components_fixture_loads_clean():
    """The Airlock contract package fixture is the canonical example
    for new-vocabulary contracts (BRD #2 §10.5). It must load clean
    against the validator — that's the proving ground for the
    format."""
    from termin.contract_packages import load_contract_package

    fixture = (
        Path(__file__).parent.parent
        / "examples-dev" / "contract_packages"
        / "airlock-components.yaml"
    )
    if not fixture.exists():
        pytest.skip("Airlock fixture not yet authored")

    pkg = load_contract_package(fixture)
    assert pkg.namespace == "airlock-components"
    contract_names = {c.name for c in pkg.contracts}
    # The three contracts called out in BRD #2 §10.5.
    assert "cosmic-orb" in contract_names
    assert "airlock-terminal" in contract_names
    assert "scenario-narrative" in contract_names


# ── 5c.1-finish: deploy-config wired loader ──

def test_load_contract_packages_attaches_registry_to_ctx(tmp_path):
    """v0.9 Phase 5c.1-finish: when deploy config declares
    contract_packages, the runtime helper loads them into a registry
    attached at ctx.contract_package_registry. The two-pass compiler
    (5c.2) and runtime contract-package dispatch (5c.3) read from
    this registry — without it they have no way to resolve `Using
    "<ns>.<contract>"` references at compile/render time.
    """
    from termin_runtime.app import _load_contract_packages

    pkg_path = tmp_path / "demo.yaml"
    pkg_path.write_text(textwrap.dedent("""
        namespace: demo-components
        version: 0.1.0
        contracts:
          - name: widget
            source-verb: "Show a widget for <state-ref>"
    """).strip(), encoding="utf-8")

    class _Ctx:
        contract_package_registry = None

    ctx = _Ctx()
    deploy_config = {"contract_packages": [str(pkg_path)]}
    _load_contract_packages(ctx, deploy_config)
    assert ctx.contract_package_registry is not None
    assert "demo-components" in ctx.contract_package_registry.namespaces()


def test_load_contract_packages_no_packages_is_noop(tmp_path):
    """No contract_packages field → ctx.contract_package_registry
    stays None. Apps using only presentation-base never invoke this
    machinery; the code path must stay quiet for them.
    """
    from termin_runtime.app import _load_contract_packages

    class _Ctx:
        contract_package_registry = None

    ctx = _Ctx()
    _load_contract_packages(ctx, {})
    assert ctx.contract_package_registry is None


def test_load_contract_packages_resolves_relative_to_deploy_path(tmp_path):
    """5c.1-finish: paths in deploy config resolve relative to the
    deploy file's parent directory — the natural authoring layout
    where contract_packages/ sits next to the deploy.json. Without
    this, every operator would have to cd to the repo root before
    starting the runtime.
    """
    from termin_runtime.app import _load_contract_packages

    sub = tmp_path / "deploys"
    sub.mkdir()
    pkg_dir = sub / "contract_packages"
    pkg_dir.mkdir()
    (pkg_dir / "demo.yaml").write_text(textwrap.dedent("""
        namespace: rel-demo
        version: 0.1.0
        contracts:
          - name: thing
            source-verb: "Show a thing for <state-ref>"
    """).strip(), encoding="utf-8")
    deploy_path = sub / "app.deploy.json"
    deploy_path.write_text("{}", encoding="utf-8")

    class _Ctx:
        contract_package_registry = None

    ctx = _Ctx()
    deploy_config = {
        "_deploy_config_path": str(deploy_path),
        "contract_packages": ["contract_packages/demo.yaml"],
    }
    _load_contract_packages(ctx, deploy_config)
    assert ctx.contract_package_registry is not None
    assert "rel-demo" in ctx.contract_package_registry.namespaces()


def test_load_contract_packages_fail_closed_on_missing(tmp_path):
    """5c.1-finish: a deploy declaring a missing package can't
    proceed — the `Using "<ns>.<contract>"` references in source
    would be unresolvable at compile time. Fail closed at startup
    with a useful error rather than letting the app limp along.
    """
    from termin_runtime.app import _load_contract_packages

    class _Ctx:
        contract_package_registry = None

    ctx = _Ctx()
    deploy_config = {
        "contract_packages": [str(tmp_path / "missing.yaml")],
    }
    with pytest.raises(RuntimeError, match="contract package"):
        _load_contract_packages(ctx, deploy_config)


def test_load_contract_packages_fail_closed_on_verb_collision(tmp_path):
    """5c.1-finish: cross-package verb collision surfaces at startup.
    Per BRD #2 §4.5 this is a hard stop in v0.9 (no aliasing).
    """
    from termin_runtime.app import _load_contract_packages

    a = tmp_path / "a.yaml"
    a.write_text(textwrap.dedent("""
        namespace: pkg-a
        version: 0.1.0
        contracts:
          - name: thing
            source-verb: "Show a colored item for <state-ref>"
    """).strip(), encoding="utf-8")
    b = tmp_path / "b.yaml"
    b.write_text(textwrap.dedent("""
        namespace: pkg-b
        version: 0.1.0
        contracts:
          - name: rival
            source-verb: "Show a colored item for <state-ref>"
    """).strip(), encoding="utf-8")

    class _Ctx:
        contract_package_registry = None

    ctx = _Ctx()
    with pytest.raises(RuntimeError, match="Verb collision"):
        _load_contract_packages(ctx, {
            "contract_packages": [str(a), str(b)],
        })


def test_load_contract_packages_rejects_non_list():
    """Defensive: deploy config typo (string instead of list) gets
    a clear error, not a confusing AttributeError downstream.
    """
    from termin_runtime.app import _load_contract_packages

    class _Ctx:
        contract_package_registry = None

    ctx = _Ctx()
    with pytest.raises(RuntimeError, match="must be a list"):
        _load_contract_packages(ctx, {"contract_packages": "single.yaml"})
