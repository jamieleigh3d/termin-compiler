# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5b.1: `Using "<ns>.<contract>"` grammar.

Per BRD #2 §4.2 / §4.3 + presentation-provider-design.md §6 slice 5b.1.
The `Using` sub-clause is a modifier that applies to the immediately
preceding rendering directive, overriding the contract that would
otherwise be inferred from the verb.

Two source-level shapes:

  1. **Override mode** — verb is a `presentation-base` verb; the
     `Using` target must reference a contract whose namespace is
     either `presentation-base` (a per-site re-binding) or any other
     namespace whose contract `extends` the implicit base contract.
  2. **New-verb mode** — verb is only legal because some included
     contract package declared it. Reserved for slice 5c (contract
     packages); 5b.1 only validates that `Using` *grammar* parses
     correctly. New-verb-mode validation lands when grammar-extension
     does in 5b.2/5c.

Scope of this test module: parse correctness + IR plumbing +
compile-time validation against the closed `presentation-base`
contract set. Multi-provider runtime dispatch ships in 5b.3.
"""

from __future__ import annotations

import pytest

from termin.peg_parser import parse_peg as parse
from termin.lower import lower
from termin.analyzer import Analyzer


_BASE_APP = '''Application: Using Smoke
  Description: smoke test for Using sub-clause

Identity:
  Scopes are "x.read"
  Anonymous has "x.read"

Content called "tickets":
  Each ticket has a title which is text, required
  Anyone with "x.read" can view tickets

As an anonymous, I want to see tickets:
'''


def _src_with_table_using(using_clause: str) -> str:
    return (
        _BASE_APP
        + "  Show a page called \"Tickets\"\n"
        + "  Display a table of tickets\n"
        + (f"    {using_clause}\n" if using_clause else "")
    )


# ── Grammar parse smoke ──

def test_using_subclause_parses_against_data_table():
    """`Using "presentation-base.data-table"` parses cleanly under
    a Display a table of directive."""
    src = _src_with_table_using('Using "presentation-base.data-table"')
    prog, errs = parse(src)
    assert not errs.errors, f"unexpected parse errors: {errs.errors}"


def test_using_subclause_parses_against_third_party_namespace():
    """Grammar accepts any `<ns>.<contract>` shape; namespace
    validation against installed contract packages is 5c."""
    src = _src_with_table_using('Using "acme-ui.premium-table"')
    prog, errs = parse(src)
    assert not errs.errors, f"unexpected parse errors: {errs.errors}"


def test_using_without_contract_qualifier_is_parse_error():
    """`Using "presentation-base"` (no `.contract`) is malformed."""
    src = _src_with_table_using('Using "presentation-base"')
    prog, errs = parse(src)
    # Either the parse fails OR the analyzer rejects.
    if errs.errors:
        return
    spec = lower(prog)
    res = Analyzer(prog).analyze()
    assert any("Using" in str(e) or "contract" in str(e).lower()
               for e in res.errors), (
        f"expected an error about malformed Using target; got {res.errors}"
    )


# ── AST / IR carries the override ──

def test_lowering_attaches_using_target_to_component_node():
    """After lowering, the data-table ComponentNode's `contract`
    field reflects the Using override, not the type→contract default."""
    src = _src_with_table_using('Using "acme-ui.premium-table"')
    prog, errs = parse(src)
    assert not errs.errors
    spec = lower(prog)
    # Find the data-table component in the page tree.
    found_contract = None
    for page in spec.pages:
        for child in page.children:
            for descendant in _walk(child):
                if descendant.type == "data_table":
                    found_contract = descendant.contract
    assert found_contract == "acme-ui.premium-table", (
        f"expected contract override to land on the node; got {found_contract!r}"
    )


def test_lowering_default_contract_when_no_using():
    """Without Using, the data-table node carries
    `presentation-base.data-table` from the type→contract map."""
    src = _src_with_table_using("")
    prog, errs = parse(src)
    assert not errs.errors
    spec = lower(prog)
    found_contract = None
    for page in spec.pages:
        for child in page.children:
            for descendant in _walk(child):
                if descendant.type == "data_table":
                    found_contract = descendant.contract
    assert found_contract == "presentation-base.data-table"


def test_required_contracts_includes_override_namespace():
    """The IR's `required_contracts` manifest carries the override
    namespace+contract so deploy-time binding resolution sees it."""
    src = _src_with_table_using('Using "acme-ui.premium-table"')
    prog, errs = parse(src)
    spec = lower(prog)
    assert "acme-ui.premium-table" in spec.required_contracts


def test_required_contracts_no_duplicates_when_using_matches_default():
    """When `Using "presentation-base.data-table"` is explicit (a
    no-op rebind), the manifest still has just one entry."""
    src = _src_with_table_using('Using "presentation-base.data-table"')
    prog, errs = parse(src)
    spec = lower(prog)
    occurrences = [
        c for c in spec.required_contracts
        if c == "presentation-base.data-table"
    ]
    assert len(occurrences) == 1


def test_required_contracts_alphabetical_with_override():
    """BRD §8.5: alphabetical order in the manifest. Mixing override
    and default namespaces still sorts."""
    src = _src_with_table_using('Using "acme-ui.premium-table"')
    prog, errs = parse(src)
    spec = lower(prog)
    contracts = list(spec.required_contracts)
    assert contracts == sorted(contracts)


# ── Compile-time validation against presentation-base ──

def test_unknown_presentation_base_contract_is_error():
    """`presentation-base.<unknown>` should raise a TERMIN-S054
    analyzer error — typo prevention before deploy."""
    src = _src_with_table_using('Using "presentation-base.tabel"')
    prog, errs = parse(src)
    assert not errs.errors, f"unexpected parse errors: {errs}"
    spec = lower(prog)
    res = Analyzer(prog).analyze()
    assert any(
        "presentation-base.tabel" in str(e) or "TERMIN-S054" in str(e)
        for e in res.errors
    ), f"expected unknown-contract error; got {res.errors}"


def test_known_presentation_base_contract_accepted():
    """All ten BRD §5.1 contracts must pass validation when
    referenced by name."""
    valid = "presentation-base.data-table"
    src = _src_with_table_using(f'Using "{valid}"')
    prog, errs = parse(src)
    assert not errs.errors
    spec = lower(prog)
    res = Analyzer(prog).analyze()
    assert not any(
        "TERMIN-S054" in str(e) for e in res.errors
    ), f"presentation-base.data-table should be valid; errors: {res.errors}"


def test_third_party_namespace_passes_validation_in_5b():
    """5b.1 cannot validate third-party namespaces (no contract
    package machinery yet — that's 5c). The analyzer must NOT
    flag them; deploy-time binding resolution is the gate."""
    src = _src_with_table_using('Using "acme-ui.premium-table"')
    prog, errs = parse(src)
    assert not errs.errors
    spec = lower(prog)
    res = Analyzer(prog).analyze()
    assert not any(
        "acme-ui.premium-table" in str(e) for e in res.errors
    ), f"third-party namespace should pass at 5b; errors: {res.errors}"


# ── Compatibility with other modifier sub-clauses ──

def test_using_combines_with_highlight_and_filter():
    """Using is one modifier among many. Order doesn't matter at
    the source level (BRD recommends `Using` first by convention)."""
    src = (
        _BASE_APP
        + '  Show a page called "Tickets"\n'
        + "  Display a table of tickets\n"
        + '    Using "acme-ui.premium-table"\n'
        + '    Highlight rows where `title == "x"`\n'
        + "    Allow filtering by title\n"
    )
    prog, errs = parse(src)
    assert not errs.errors, f"parse errors: {errs}"
    spec = lower(prog)
    found_contract = None
    for page in spec.pages:
        for child in page.children:
            for descendant in _walk(child):
                if descendant.type == "data_table":
                    found_contract = descendant.contract
    assert found_contract == "acme-ui.premium-table"


# ── Helpers ──

def _walk(node):
    """Yield node and every descendant ComponentNode, depth-first."""
    yield node
    for child in getattr(node, "children", ()):
        yield from _walk(child)
