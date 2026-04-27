# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 5c.1: contract package format and loader.

Contract packages declare new component types in a new namespace
(BRD #2 §10). Each package is a YAML document with `namespace`,
`version`, optional `description`, and a `contracts` list. Each
contract specifies its `source-verb`, `modifiers`, `data-shape`,
`actions`, and `principal-context`. An optional `extends` relates
the contract to a base contract in another namespace (drop-in or
extension mode per BRD §4.3 / §10.2).

This module ships the load + validation layer. The two-pass
compiler integration that consumes packages at compile time
(slice 5b.2) and the runtime provider dispatch (slice 5c.3) live
in subsequent slices and read from this module's `ContractPackage`
and `ContractPackageRegistry` types.

Format example (Appendix C of BRD #2):

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

Verb collisions — same source-verb declared by two contracts in
the same package, or across two loaded packages — surface here as
`ContractPackageError` with both colliding namespaces named, per
BRD §4.5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Union

import yaml


class ContractPackageError(Exception):
    """Raised when a contract package fails to load or validate."""


# ── Package and contract types ──

@dataclass(frozen=True)
class ContractDefinition:
    """One entry in a contract package's `contracts` list."""

    name: str
    source_verb: str
    modifiers: tuple[str, ...]
    data_shape: dict
    actions: tuple[dict, ...]
    principal_context: tuple[str, ...]
    extends: Optional[str] = None


@dataclass(frozen=True)
class ContractPackage:
    """A loaded contract package — namespace + version + contracts."""

    namespace: str
    version: str
    description: str
    contracts: tuple[ContractDefinition, ...]

    @property
    def qualified_names(self) -> tuple[str, ...]:
        return tuple(f"{self.namespace}.{c.name}" for c in self.contracts)


# ── Registry: cross-package state ──

class ContractPackageRegistry:
    """In-memory registry of loaded contract packages.

    The two-pass compiler (slice 5b.2 / 5c.2) builds one of these
    from every namespace referenced by `Using` in source, then asks
    it for `source_verbs()` to extend the grammar dispatch table
    and `get_contract()` to resolve `<ns>.<contract>` references
    at validation time.
    """

    def __init__(self) -> None:
        self._packages: dict[str, ContractPackage] = {}
        # source_verb -> qualified contract name, used for collision detection
        self._verb_owners: dict[str, str] = {}

    def add(self, pkg: ContractPackage) -> None:
        """Register a loaded package. Raises ContractPackageError on
        cross-package verb collision; both colliding packages are
        named in the error message per BRD §4.5."""
        if pkg.namespace in self._packages:
            raise ContractPackageError(
                f"Namespace {pkg.namespace!r} already loaded; cannot register twice"
            )
        for contract in pkg.contracts:
            verb = contract.source_verb
            if not verb:
                continue  # extends-only contracts have no verb of their own
            owner = self._verb_owners.get(verb)
            if owner is not None:
                # Collision: same verb in another package.
                other_ns = owner.split(".", 1)[0]
                raise ContractPackageError(
                    f"Verb collision: source-verb {verb!r} is declared by "
                    f"both {other_ns!r} and {pkg.namespace!r}. Per BRD #2 "
                    f"§4.5, two packages cannot claim the same source-verb. "
                    f"v0.10 may add aliasing as a resolution path; for v0.9 "
                    f"this is a hard stop. Remove one of the colliding "
                    f"packages from the deploy."
                )
            self._verb_owners[verb] = f"{pkg.namespace}.{contract.name}"
        self._packages[pkg.namespace] = pkg

    def get_contract(self, qualified_name: str) -> Optional[ContractDefinition]:
        """Look up `<namespace>.<contract>`. None if either segment
        doesn't resolve."""
        if "." not in qualified_name:
            return None
        ns, name = qualified_name.split(".", 1)
        pkg = self._packages.get(ns)
        if pkg is None:
            return None
        for contract in pkg.contracts:
            if contract.name == name:
                return contract
        return None

    def namespaces(self) -> tuple[str, ...]:
        return tuple(self._packages.keys())

    def source_verbs(self) -> tuple[str, ...]:
        """All declared source-verbs across loaded packages.
        Used by the two-pass compiler to extend the grammar."""
        return tuple(self._verb_owners.keys())


# ── Loader ──

def load_contract_package(path: Union[str, Path]) -> ContractPackage:
    """Load and validate a single YAML contract package file.

    Raises ContractPackageError on malformed YAML, missing required
    fields, missing source-verb on a non-extends contract, or
    duplicate source-verb within the package itself. Cross-package
    verb collisions surface from `load_contract_packages_into_registry`
    when a second package is added.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ContractPackageError(
            f"Cannot read contract package {path}: {e}"
        ) from e

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ContractPackageError(
            f"Malformed YAML in {path.name}: {e}"
        ) from e

    if not isinstance(data, dict):
        raise ContractPackageError(
            f"Contract package {path.name} root must be a mapping/object, "
            f"got {type(data).__name__}"
        )

    namespace = data.get("namespace")
    if not namespace or not isinstance(namespace, str):
        raise ContractPackageError(
            f"{path.name}: missing required `namespace` (top-level string)"
        )
    version = data.get("version")
    if not version or not isinstance(version, str):
        raise ContractPackageError(
            f"{path.name}: missing required `version` (top-level string)"
        )
    contracts_raw = data.get("contracts")
    if contracts_raw is None or not isinstance(contracts_raw, list):
        raise ContractPackageError(
            f"{path.name}: missing required `contracts` (top-level list)"
        )

    contracts: list[ContractDefinition] = []
    seen_verbs: set[str] = set()
    for idx, item in enumerate(contracts_raw):
        if not isinstance(item, dict):
            raise ContractPackageError(
                f"{path.name}: contracts[{idx}] must be a mapping, "
                f"got {type(item).__name__}"
            )
        contract = _parse_contract(item, idx, path.name)
        if contract.source_verb:
            if contract.source_verb in seen_verbs:
                raise ContractPackageError(
                    f"{path.name}: duplicate source-verb "
                    f"{contract.source_verb!r} within package {namespace!r}"
                )
            seen_verbs.add(contract.source_verb)
        contracts.append(contract)

    return ContractPackage(
        namespace=namespace,
        version=version,
        description=str(data.get("description", "")),
        contracts=tuple(contracts),
    )


def load_contract_packages_into_registry(
    paths: Iterable[Union[str, Path]],
) -> ContractPackageRegistry:
    """Load multiple contract packages into a single registry.

    Cross-package verb collisions raise ContractPackageError with
    both namespaces named. Use this entry point when a deploy or
    compile references multiple namespaces — the registry tracks
    them collectively for grammar extension and reference lookup.
    """
    registry = ContractPackageRegistry()
    for path in paths:
        pkg = load_contract_package(path)
        registry.add(pkg)
    return registry


# ── Internal: contract-record parsing ──

def _parse_contract(item: dict, idx: int, source_name: str) -> ContractDefinition:
    name = item.get("name")
    if not name or not isinstance(name, str):
        raise ContractPackageError(
            f"{source_name}: contracts[{idx}] missing required `name`"
        )

    extends = item.get("extends")
    if extends is not None and not isinstance(extends, str):
        raise ContractPackageError(
            f"{source_name}: contracts[{idx}] (`{name}`) `extends` must be "
            f"a string of shape <namespace>.<contract>"
        )

    source_verb = item.get("source-verb", "")
    if not isinstance(source_verb, str):
        raise ContractPackageError(
            f"{source_name}: contracts[{idx}] (`{name}`) `source-verb` "
            f"must be a string"
        )
    # New-verb mode (no extends) requires a non-empty source-verb.
    # Override mode (extends) may have an empty source-verb (drop-in).
    if not source_verb and not extends:
        raise ContractPackageError(
            f"{source_name}: contracts[{idx}] (`{name}`) requires either "
            f"a non-empty `source-verb` (new-verb mode) or an `extends` "
            f"reference (override mode). Per BRD #2 §10.2, exactly one "
            f"of these must be declared."
        )

    modifiers_raw = item.get("modifiers", [])
    if not isinstance(modifiers_raw, list):
        raise ContractPackageError(
            f"{source_name}: contracts[{idx}] (`{name}`) `modifiers` "
            f"must be a list"
        )
    modifiers = tuple(str(m) for m in modifiers_raw)

    data_shape = item.get("data-shape", {})
    if not isinstance(data_shape, dict):
        raise ContractPackageError(
            f"{source_name}: contracts[{idx}] (`{name}`) `data-shape` "
            f"must be a mapping"
        )

    actions_raw = item.get("actions", [])
    if not isinstance(actions_raw, list):
        raise ContractPackageError(
            f"{source_name}: contracts[{idx}] (`{name}`) `actions` "
            f"must be a list"
        )
    actions = tuple(a for a in actions_raw if isinstance(a, dict))

    principal_ctx_raw = item.get("principal-context", [])
    if not isinstance(principal_ctx_raw, list):
        raise ContractPackageError(
            f"{source_name}: contracts[{idx}] (`{name}`) `principal-context` "
            f"must be a list"
        )
    principal_context = tuple(str(p) for p in principal_ctx_raw)

    return ContractDefinition(
        name=name,
        source_verb=source_verb,
        modifiers=modifiers,
        data_shape=dict(data_shape),
        actions=actions,
        principal_context=principal_context,
        extends=extends,
    )
