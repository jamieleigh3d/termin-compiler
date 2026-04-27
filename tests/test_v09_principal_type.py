# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 6a.1: `principal` business field type.

Per BRD #3 §3.2:
  - Storage: opaque text (the principal id as issued by the bound Identity provider).
  - Type system: typed Principal-reference at the business layer.
  - business_type="principal" on FieldSpec; column_type=TEXT.
  - Constraints (required, unique) flow through normally.
"""

from __future__ import annotations

import pytest

from termin.peg_parser import parse_peg as parse
from termin.lower import lower
from termin.ir import FieldType


_BASE_SOURCE = '''Application: Principal Type Test
  Description: principal business type smoke test

Identity:
  Scopes are "x.read", "x.write"
  A "user" has "x.read"

Content called "sessions":
  Each session has a player_principal which is principal, required, unique
  Each session has a self_rating which is whole number
  Anyone with "x.read" can view sessions
  Anyone with "x.write" can create sessions
'''


def _ir():
    prog, _ = parse(_BASE_SOURCE)
    return lower(prog)


def test_principal_type_lowers_to_business_type_principal():
    spec = _ir()
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    [pp] = [f for f in sessions.fields if f.name == "player_principal"]
    assert pp.business_type == "principal"


def test_principal_type_stores_as_text():
    spec = _ir()
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    [pp] = [f for f in sessions.fields if f.name == "player_principal"]
    assert pp.column_type == FieldType.TEXT, (
        "Per BRD #3 §3.2, principal is opaque text at the storage layer"
    )


def test_principal_required_constraint_preserved():
    spec = _ir()
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    [pp] = [f for f in sessions.fields if f.name == "player_principal"]
    assert pp.required is True


def test_principal_unique_constraint_preserved():
    spec = _ir()
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    [pp] = [f for f in sessions.fields if f.name == "player_principal"]
    assert pp.unique is True


def test_principal_without_modifiers_parses():
    src = _BASE_SOURCE.replace(
        "Each session has a player_principal which is principal, required, unique",
        "Each session has a player_principal which is principal",
    )
    prog, _ = parse(src)
    spec = lower(prog)
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    [pp] = [f for f in sessions.fields if f.name == "player_principal"]
    assert pp.business_type == "principal"
    assert pp.required is False
    assert pp.unique is False


def test_principal_with_only_required():
    src = _BASE_SOURCE.replace(
        "Each session has a player_principal which is principal, required, unique",
        "Each session has a player_principal which is principal, required",
    )
    prog, _ = parse(src)
    spec = lower(prog)
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    [pp] = [f for f in sessions.fields if f.name == "player_principal"]
    assert pp.business_type == "principal"
    assert pp.required is True
    assert pp.unique is False


def test_principal_with_inverted_constraint_form():
    """v0.8.2 inverted-form support: `required principal` parses identically
    to `principal, required`."""
    src = _BASE_SOURCE.replace(
        "Each session has a player_principal which is principal, required, unique",
        "Each session has a player_principal which is required unique principal",
    )
    prog, _ = parse(src)
    spec = lower(prog)
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    [pp] = [f for f in sessions.fields if f.name == "player_principal"]
    assert pp.business_type == "principal"
    assert pp.required is True
    assert pp.unique is True


def test_other_field_types_still_work_alongside_principal():
    """Sanity: introducing `principal` doesn't break existing types."""
    spec = _ir()
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    [sr] = [f for f in sessions.fields if f.name == "self_rating"]
    assert sr.business_type == "whole_number"
    assert sr.column_type == FieldType.INTEGER


def test_principal_field_emits_text_sql_column():
    """End-to-end: the SQL DDL the runtime would emit uses TEXT for the
    principal column."""
    spec = _ir()
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    [pp] = [f for f in sessions.fields if f.name == "player_principal"]
    # column_type maps to the SQL column shape
    assert pp.column_type == FieldType.TEXT
