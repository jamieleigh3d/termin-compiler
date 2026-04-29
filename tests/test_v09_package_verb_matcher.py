# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5c.2: contract-package source-verb matcher.

The matcher is pure-Python (no TatSu involvement), so its behavior
is platform-uniform — these tests run identically on Windows and
WSL/Linux. No fidelity-test discipline applies because there's no
TatSu fallback path to keep faithful.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from termin.package_verb_matcher import (
    match_verb,
    set_active_registry,
    clear_active_registry,
    match_active_packages,
)


# ── match_verb: literal segments + placeholders ──

def test_match_verb_simple_one_placeholder():
    bindings = match_verb(
        "Show a cosmic orb of scenarios",
        "Show a cosmic orb of <state-ref>",
    )
    assert bindings == {"state-ref": "scenarios"}


def test_match_verb_collapses_extra_whitespace():
    """Whitespace inside literals is normalized — extra spaces in
    the source line don't break the match. Authors who hand-format
    DSL files shouldn't be punished for double spaces."""
    bindings = match_verb(
        "Show a  cosmic   orb   of  scenarios",
        "Show a cosmic orb of <state-ref>",
    )
    assert bindings == {"state-ref": "scenarios"}


def test_match_verb_rejects_non_bareword_placeholder():
    """Placeholders match snake-case identifiers only; quoted
    strings or multi-word phrases are not accepted in v0.9."""
    assert match_verb(
        'Show a cosmic orb of "long phrase"',
        "Show a cosmic orb of <state-ref>",
    ) is None


def test_match_verb_rejects_uppercase_first_letter_in_placeholder():
    """Bareword pattern starts with [a-z]. Content names in Termin
    are snake_case lowercase by convention."""
    assert match_verb(
        "Show a cosmic orb of Scenarios",
        "Show a cosmic orb of <state-ref>",
    ) is None


def test_match_verb_multiple_placeholders():
    bindings = match_verb(
        "Show an airlock terminal for room_a controlled by switches",
        "Show an airlock terminal for <command-set> controlled by <controller>",
    )
    assert bindings == {
        "command-set": "room_a",
        "controller": "switches",
    }


def test_match_verb_rejects_partial_match():
    """Trailing content not in template → no match. v0.9 does not
    support partial / suffix matching."""
    assert match_verb(
        "Show a cosmic orb of scenarios with extra stuff",
        "Show a cosmic orb of <state-ref>",
    ) is None


def test_match_verb_rejects_missing_literal_prefix():
    """Different literal prefix → no match."""
    assert match_verb(
        "Display a cosmic orb of scenarios",
        "Show a cosmic orb of <state-ref>",
    ) is None


def test_match_verb_rejects_missing_placeholder_value():
    """Empty token where placeholder was → no match."""
    assert match_verb(
        "Show a cosmic orb of",
        "Show a cosmic orb of <state-ref>",
    ) is None


def test_match_verb_handles_placeholder_at_start():
    """Templates can start with a placeholder; literal anchor follows."""
    bindings = match_verb(
        "scenarios are tracked",
        "<topic> are tracked",
    )
    assert bindings == {"topic": "scenarios"}


def test_match_verb_handles_underscore_in_placeholder_value():
    bindings = match_verb(
        "Show a cosmic orb of stock_levels",
        "Show a cosmic orb of <state-ref>",
    )
    assert bindings == {"state-ref": "stock_levels"}


# ── match_active_packages: registry hook ──

def test_match_active_packages_returns_none_when_no_registry():
    clear_active_registry()
    assert match_active_packages("Show a cosmic orb of scenarios") is None


def test_match_active_packages_finds_matching_template(tmp_path):
    """End-to-end: register a package, match a line against it,
    expect (qualified_name, bindings)."""
    from termin.contract_packages import (
        load_contract_packages_into_registry,
    )

    pkg = tmp_path / "demo.yaml"
    pkg.write_text(textwrap.dedent("""
        namespace: demo-ns
        version: 0.1.0
        contracts:
          - name: orb
            source-verb: "Show a cosmic orb of <state-ref>"
    """).strip(), encoding="utf-8")
    registry = load_contract_packages_into_registry([pkg])

    set_active_registry(registry)
    try:
        result = match_active_packages("Show a cosmic orb of scenarios")
        assert result == (
            "demo-ns.orb",
            {"state-ref": "scenarios"},
        )
    finally:
        clear_active_registry()


def test_clear_active_registry_resets_state(tmp_path):
    """clear_active_registry truly empties — subsequent calls see None."""
    from termin.contract_packages import (
        load_contract_packages_into_registry,
    )
    pkg = tmp_path / "demo.yaml"
    pkg.write_text(textwrap.dedent("""
        namespace: demo-ns
        version: 0.1.0
        contracts:
          - name: orb
            source-verb: "Show a cosmic orb of <state-ref>"
    """).strip(), encoding="utf-8")
    registry = load_contract_packages_into_registry([pkg])

    set_active_registry(registry)
    assert match_active_packages("Show a cosmic orb of scenarios") is not None
    clear_active_registry()
    assert match_active_packages("Show a cosmic orb of scenarios") is None


# ── Parser integration: parse_peg with registry ──

def _make_demo_registry(tmp_path):
    from termin.contract_packages import (
        load_contract_packages_into_registry,
    )
    pkg = tmp_path / "demo.yaml"
    pkg.write_text(textwrap.dedent("""
        namespace: demo-ns
        version: 0.1.0
        contracts:
          - name: orb
            source-verb: "Show a cosmic orb of <state-ref>"
    """).strip(), encoding="utf-8")
    return load_contract_packages_into_registry([pkg])


def test_parse_peg_routes_package_verb_to_handler(tmp_path):
    """parse_peg with a registry classifies a matching line as
    package_contract_line and produces a PackageContractCall AST node.
    The story body's directives include the call with the qualified
    name and bindings filled in.
    """
    from termin.peg_parser import parse_peg
    from termin.ast_nodes import PackageContractCall

    source = textwrap.dedent("""
        Application: Demo
          Description: Demo

        Identity:
          Scopes are "app.use"
          Anonymous has "app.use"

        Content called "scenarios":
          Each scenario has a name which is text

        As an anonymous, I want to see scenarios so that I can play:
            Show a page called "Scenarios"
            Show a cosmic orb of scenarios
    """).strip()

    registry = _make_demo_registry(tmp_path)
    program, result = parse_peg(source, contract_package_registry=registry)
    assert result.ok, result.errors

    assert program.stories
    story = program.stories[0]
    # Story body lists are exposed under either `directives` or
    # `body` depending on grammar version — accept both.
    body = getattr(story, "directives", None) or getattr(story, "body", [])
    pkg_calls = [
        d for d in body if isinstance(d, PackageContractCall)
    ]
    assert len(pkg_calls) == 1
    call = pkg_calls[0]
    assert call.qualified_name == "demo-ns.orb"
    assert call.bindings == {"state-ref": "scenarios"}


def test_parse_peg_without_registry_rejects_package_verb(tmp_path):
    """No registry → the package verb is unrecognized; parser surfaces
    a TERMIN-P002 error rather than silently dropping the line."""
    from termin.peg_parser import parse_peg

    source = textwrap.dedent("""
        Application: Demo
          Description: Demo

        Identity:
          Scopes are "app.use"
          Anonymous has "app.use"

        Content called "scenarios":
          Each scenario has a name which is text

        As an anonymous, I want to see scenarios so that I can play:
            Show a page called "Scenarios"
            Show a cosmic orb of scenarios
    """).strip()

    program, result = parse_peg(source)  # no registry
    assert not result.ok
    assert any(
        getattr(e, "code", "") == "TERMIN-P002"
        and "cosmic orb" in (getattr(e, "source_line", "") or e.message)
        for e in result.errors
    )


def test_parse_peg_clears_registry_after_use(tmp_path):
    """The module-level active-registry must be torn down after parse,
    even when parse succeeds — leaving it set would poison subsequent
    parses (e.g., test isolation in the same process)."""
    from termin.peg_parser import parse_peg
    from termin.package_verb_matcher import get_active_registry

    registry = _make_demo_registry(tmp_path)
    parse_peg(
        "Application: Demo\n  Description: Demo",
        contract_package_registry=registry,
    )
    assert get_active_registry() is None
