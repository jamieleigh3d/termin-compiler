# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 6a.2: `Each X is owned by <field>` ownership.

Per BRD #3 §3.3:
  - Content body sub-line: `Each <singular> is owned by <field>`.
  - The named field must:
      * exist on the content
      * be `principal`-typed (TERMIN-S049)
      * be `unique` (TERMIN-S050)
      * be `required` (TERMIN-S051)
  - At most one ownership declaration per content (TERMIN-S052).
  - IR emits ContentSchema.ownership = OwnershipSpec(field=<snake>) when
    declared; None otherwise.
"""

from __future__ import annotations

import pytest

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower
from termin.ir import OwnershipSpec


def _compile(src):
    """Parse + analyze. Returns (program, analysis_result)."""
    prog, _ = parse(src)
    res = analyze(prog)
    return prog, res


_BASE = '''Application: Ownership Test
  Description: ownership smoke

Identity:
  Scopes are "x.read", "x.write"
  A "user" has "x.read"

Content called "sessions":
  Each session has a player_principal which is principal, required, unique
  Each session has a self_rating which is whole number
  Each session is owned by player_principal
  Anyone with "x.read" can view sessions
  Anyone with "x.write" can create sessions
'''


# ── Happy path ──

def test_ownership_parses_and_lands_on_ast():
    prog, res = _compile(_BASE)
    assert list(res.errors) == []
    [sessions] = [c for c in prog.contents if c.name == "sessions"]
    assert sessions.owned_by_declarations == ["player_principal"]


def test_ownership_lowers_to_ir_ownership_spec():
    prog, res = _compile(_BASE)
    assert list(res.errors) == []
    spec = lower(prog)
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    assert sessions.ownership == OwnershipSpec(field="player_principal")


def test_ownership_field_snake_cases_in_ir():
    src = _BASE.replace(
        "Each session has a player_principal which is principal, required, unique",
        "Each session has a Player Principal which is principal, required, unique",
    ).replace(
        "Each session is owned by player_principal",
        "Each session is owned by Player Principal",
    )
    prog, res = _compile(src)
    assert list(res.errors) == []
    spec = lower(prog)
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    assert sessions.ownership.field == "player_principal"


def test_no_ownership_declaration_yields_none():
    src = _BASE.replace(
        "Each session is owned by player_principal\n",
        "",
    )
    prog, res = _compile(src)
    assert list(res.errors) == []
    spec = lower(prog)
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    assert sessions.ownership is None


# ── TERMIN-S048: field doesn't exist ──

def test_S048_missing_field():
    src = _BASE.replace(
        "Each session is owned by player_principal",
        "Each session is owned by ghost_field",
    )
    _, res = _compile(src)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S048" in codes


def test_S048_fuzzy_suggestion_for_typo():
    src = _BASE.replace(
        "Each session is owned by player_principal",
        "Each session is owned by player_principa",
    )
    _, res = _compile(src)
    s048 = [e for e in res.errors if e.code == "TERMIN-S048"]
    assert len(s048) == 1
    assert s048[0].suggestion is not None
    assert "player_principal" in s048[0].suggestion


# ── TERMIN-S049: field not principal-typed ──

def test_S049_field_is_text_not_principal():
    src = _BASE.replace(
        "Each session has a player_principal which is principal, required, unique",
        "Each session has a player_principal which is text, required, unique",
    )
    _, res = _compile(src)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S049" in codes


def test_S049_field_is_whole_number_not_principal():
    src = _BASE.replace(
        "Each session has a player_principal which is principal, required, unique",
        "Each session has a player_principal which is whole number, required, unique",
    )
    _, res = _compile(src)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S049" in codes


# ── TERMIN-S050: field not unique ──

def test_S050_field_missing_unique():
    src = _BASE.replace(
        "Each session has a player_principal which is principal, required, unique",
        "Each session has a player_principal which is principal, required",
    )
    _, res = _compile(src)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S050" in codes


# ── TERMIN-S051: field not required ──

def test_S051_field_missing_required():
    src = _BASE.replace(
        "Each session has a player_principal which is principal, required, unique",
        "Each session has a player_principal which is principal, unique",
    )
    _, res = _compile(src)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S051" in codes


# ── TERMIN-S052: multiple ownership declarations ──

def test_S052_duplicate_ownership_declarations():
    src = _BASE.replace(
        "Each session is owned by player_principal",
        "Each session is owned by player_principal\n  Each session is owned by player_principal",
    )
    _, res = _compile(src)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S052" in codes


def test_S052_two_different_owner_fields():
    """Two different fields named in two `is owned by` lines = also S052."""
    src = '''Application: T
  Description: t

Identity:
  Scopes are "x"
  A "user" has "x"

Content called "things":
  Each thing has a owner_a which is principal, required, unique
  Each thing has a owner_b which is principal, required, unique
  Each thing is owned by owner_a
  Each thing is owned by owner_b
  Anyone with "x" can view things
'''
    _, res = _compile(src)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S052" in codes


# ── Combined / aggregated ──

def test_S049_S050_S051_all_fire_for_text_unrequired_ununique_field():
    src = _BASE.replace(
        "Each session has a player_principal which is principal, required, unique",
        "Each session has a player_principal which is text",
    )
    _, res = _compile(src)
    codes = {e.code for e in res.errors}
    # S049 (not principal), S050 (not unique), S051 (not required) all fire
    assert "TERMIN-S049" in codes
    assert "TERMIN-S050" in codes
    assert "TERMIN-S051" in codes


def test_existing_examples_have_no_ownership():
    """Sanity: introducing ownership doesn't break existing examples that
    don't use it."""
    src = '''Application: Plain
  Description: no ownership at all

Identity:
  Scopes are "x"
  A "user" has "x"

Content called "items":
  Each item has a name which is text, required
  Anyone with "x" can view items
'''
    prog, res = _compile(src)
    assert list(res.errors) == []
    spec = lower(prog)
    [items] = [c for c in spec.content if c.name.snake == "items"]
    assert items.ownership is None
