# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9.2 Slice L10: multi-row ownership extension.

Per `docs/termin-v0.9.2-conversation-field-type-tech-design.md` §15:
  - `Each <singular> is owned by <field>` no longer requires `unique`
    on the named field. v0.9.1's TERMIN-S050 is dropped.
  - When the field is non-unique, `their own <plural>` is valid and
    resolves to the set `{r ∈ Content : r.<field> == principal.id}`.
  - When the field is non-unique, `their own <singular>` is a compile
    error (TERMIN-S057) — the singular form implies a single record but
    multi-row content has many.
  - When the field is unique, behavior is unchanged from v0.9.1: both
    singular and plural forms work, the cardinality is one row per
    principal, and the lookup returns at most one row.

Note on `the user's <singular>` (§15.3): the spec also calls this a
compile error on non-unique ownership. The `the user's <X>` source form
itself (BRD #3 §4.3) is not yet implemented in the grammar — it's still
deferred. When that form lands, a parallel TERMIN-S058 check will gate
it the same way TERMIN-S057 gates `their own <singular>`.
"""

from __future__ import annotations

import pytest

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower
from termin_core.ir.types import OwnershipSpec, RouteKind, RowFilterSpec


def _compile(src):
    """Parse + analyze. Returns (program, analysis_result)."""
    prog, _ = parse(src)
    res = analyze(prog)
    return prog, res


# ── Headline: non-unique ownership compiles ──

_NON_UNIQUE_BASE = '''Application: Multi-row Ownership Test
  Description: non-unique ownership for multi-row content

Identity:
  Scopes are "play"
  A "player" has "play"

Content called "sessions":
  Each session has a player_principal which is principal, required
  Each session has a self_rating which is whole number
  Each session is owned by player_principal
  Anyone with "play" can view their own sessions
  Anyone with "play" can create sessions
'''


def test_non_unique_ownership_compiles():
    """Headline: `Each <singular> is owned by <field>` no longer requires
    the field to be unique. Per §15.2, the field is interpreted as a
    scoping key, and a principal may own many rows."""
    _, res = _compile(_NON_UNIQUE_BASE)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S050" not in codes, (
        "v0.9.2 §15: non-unique ownership must compile cleanly. "
        f"Got errors: {[(e.code, e.message) for e in res.errors]}"
    )
    assert list(res.errors) == [], (
        f"unexpected errors: {[(e.code, e.message) for e in res.errors]}"
    )


def test_non_unique_ownership_lowers_to_ownership_spec():
    """The IR carries OwnershipSpec on the content schema regardless of
    whether the field is unique. Runtimes inspect FieldSpec.unique to
    decide the cardinality semantics."""
    prog, res = _compile(_NON_UNIQUE_BASE)
    assert list(res.errors) == []
    spec = lower(prog)
    [sessions] = [c for c in spec.content if c.name.snake == "sessions"]
    assert sessions.ownership == OwnershipSpec(field="player_principal")
    # And the field itself is not unique — runtime can read this to
    # decide single-vs-set semantics.
    [pp] = [f for f in sessions.fields if f.name == "player_principal"]
    assert pp.unique is False


def test_their_own_plural_on_non_unique_compiles():
    """`their own <plural>` is the canonical multi-row read form. It
    resolves to the set of records the principal owns."""
    _, res = _compile(_NON_UNIQUE_BASE)
    assert list(res.errors) == []


def test_their_own_plural_lowers_to_row_filter_on_non_unique():
    """LIST and GET_ONE routes carry RowFilterSpec(kind=ownership)
    just as they do for unique ownership. The runtime contract is
    unchanged at the route level — single-vs-set semantics fall out
    of the field's unique flag, not the route shape."""
    prog, res = _compile(_NON_UNIQUE_BASE)
    assert list(res.errors) == []
    spec = lower(prog)
    [list_route] = [
        r for r in spec.routes
        if r.content_ref == "sessions" and r.kind == RouteKind.LIST
    ]
    assert list_route.row_filter == RowFilterSpec(
        kind="ownership", field="player_principal"
    )
    [get_one] = [
        r for r in spec.routes
        if r.content_ref == "sessions" and r.kind == RouteKind.GET_ONE
    ]
    assert get_one.row_filter == RowFilterSpec(
        kind="ownership", field="player_principal"
    )


# ── TERMIN-S057: their own <singular> on non-unique ownership ──

_NON_UNIQUE_SINGULAR = '''Application: Multi-row Ownership Singular
  Description: their own singular on non-unique ownership

Identity:
  Scopes are "play"
  A "player" has "play"

Content called "sessions":
  Each session has a player_principal which is principal, required
  Each session is owned by player_principal
  Anyone with "play" can view their own session
  Anyone with "play" can create sessions
'''


def test_S057_their_own_singular_on_non_unique_ownership():
    """`their own <singular>` implies a single record. On non-unique
    ownership, multiple records may exist — emit TERMIN-S057 so the
    author switches to `their own <plural>`."""
    _, res = _compile(_NON_UNIQUE_SINGULAR)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S057" in codes, (
        f"expected TERMIN-S057, got: {[(e.code, e.message) for e in res.errors]}"
    )


def test_S057_message_names_the_content_and_suggests_plural():
    """The error must point at the offending content and tell the
    author to use the plural form."""
    _, res = _compile(_NON_UNIQUE_SINGULAR)
    s057 = [e for e in res.errors if e.code == "TERMIN-S057"]
    assert len(s057) == 1
    msg = s057[0].message.lower()
    assert "session" in msg  # content singular named
    assert "sessions" in msg  # suggested plural form
    assert "non-unique" in msg or "not unique" in msg


# ── Backward compatibility: unique ownership still works ──

_UNIQUE_BASE = '''Application: Unique Ownership Test
  Description: unique ownership keeps v0.9.1 behavior

Identity:
  Scopes are "x.read", "x.write"
  A "user" has "x.read"

Content called "profiles":
  Each profile has a owner which is principal, required, unique
  Each profile has a display_name which is text, required
  Each profile is owned by owner
  Anyone with "x.read" can view their own profiles
  Anyone with "x.write" can create profiles
  Anyone with "x.write" can update their own profiles
'''


def test_unique_ownership_still_compiles_unchanged():
    """v0.9.1 behavior is preserved when the ownership field is unique."""
    _, res = _compile(_UNIQUE_BASE)
    assert list(res.errors) == []


def test_unique_ownership_their_own_singular_does_not_fire_S057():
    """On unique ownership, `their own <singular>` is a legal idiom — the
    field carries a uniqueness guarantee so singular makes sense.
    Per §15.3: behavior is unchanged from v0.9.1 when the field is unique."""
    src = _UNIQUE_BASE.replace(
        "can view their own profiles",
        "can view their own profile",
    )
    _, res = _compile(src)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S057" not in codes, (
        "TERMIN-S057 must NOT fire on unique ownership singular form. "
        f"Got: {[(e.code, e.message) for e in res.errors]}"
    )


def test_unique_ownership_their_own_plural_still_lowers_to_row_filter():
    """The IR shape for unique ownership is unchanged from v0.9.1."""
    prog, res = _compile(_UNIQUE_BASE)
    assert list(res.errors) == []
    spec = lower(prog)
    [list_route] = [
        r for r in spec.routes
        if r.content_ref == "profiles" and r.kind == RouteKind.LIST
    ]
    assert list_route.row_filter == RowFilterSpec(
        kind="ownership", field="owner"
    )


# ── Other regression: TERMIN-S050 must NOT fire ──

def test_TERMIN_S050_no_longer_fires_for_non_unique_ownership():
    """v0.9.2 explicitly drops TERMIN-S050. A regression that
    re-introduced it would silently break multi-row ownership for
    every author who declares it."""
    src = _NON_UNIQUE_BASE  # the canonical non-unique case
    _, res = _compile(src)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S050" not in codes
