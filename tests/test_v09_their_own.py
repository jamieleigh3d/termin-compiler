# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 6a.3: `their own <content>` permission verb.

Per BRD #3 §3.4:
  - `Anyone with "<scope>" can <verbs> their own <content>` is legal
    only when <content> declares ownership.
  - The compiler lowers each `their own` permission line to a RouteSpec
    carrying a RowFilterSpec(kind="ownership", field=<owning-field>).
  - TERMIN-S053 fires when `their own X` is used without ownership.

Runtime enforcement (filtering by `the user.id`) is Phase 6a.5; here we
verify only the source-side IR shape.
"""

from __future__ import annotations

import pytest

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower
from termin.ir import RouteKind, RowFilterSpec


def _compile(src):
    prog, _ = parse(src)
    res = analyze(prog)
    return prog, res


_BASE = '''Application: Their Own Test
  Description: their-own access verb

Identity:
  Scopes are "x.read", "x.write"
  A "user" has "x.read"

Content called "sessions":
  Each session has a player_principal which is principal, required, unique
  Each session has a self_rating which is whole number
  Each session is owned by player_principal
  Anyone with "x.read" can view their own sessions
  Anyone with "x.write" can update their own sessions
  Anyone with "x.write" can create sessions
'''


# ── Grammar / AST ──

def test_their_own_parses_and_flags_ast_rule():
    prog, res = _compile(_BASE)
    assert list(res.errors) == []
    [sessions] = [c for c in prog.contents if c.name == "sessions"]
    rules = sessions.access_rules

    # view rule has their_own=True
    [view_rule] = [r for r in rules if "view" in r.verbs]
    assert view_rule.their_own is True

    # update rule has their_own=True
    [update_rule] = [r for r in rules if "update" in r.verbs]
    assert update_rule.their_own is True

    # create rule does NOT have their_own=True
    [create_rule] = [r for r in rules if "create" in r.verbs]
    assert create_rule.their_own is False


def test_plain_access_without_their_own_flag_is_false():
    src = _BASE.replace(
        "Anyone with \"x.read\" can view their own sessions",
        "Anyone with \"x.read\" can view sessions",
    )
    prog, res = _compile(src)
    assert list(res.errors) == []
    [sessions] = [c for c in prog.contents if c.name == "sessions"]
    [view_rule] = [r for r in sessions.access_rules if "view" in r.verbs]
    assert view_rule.their_own is False


# ── IR: AccessGrant + RouteSpec.row_filter ──

def test_access_grant_carries_their_own_through_to_ir():
    prog, res = _compile(_BASE)
    assert list(res.errors) == []
    spec = lower(prog)
    grants = [g for g in spec.access_grants if g.content == "sessions"]
    # view + update are their_own; create is not
    by_scope = {(g.scope, frozenset(v.value for v in g.verbs)): g for g in grants}
    view_grant = next(g for g in grants if "view" in {v.value for v in g.verbs})
    update_grant = next(g for g in grants if "update" in {v.value for v in g.verbs})
    create_grant = next(g for g in grants if "create" in {v.value for v in g.verbs})
    assert view_grant.their_own is True
    assert update_grant.their_own is True
    assert create_grant.their_own is False


def test_list_route_gets_row_filter_when_view_is_their_own():
    prog, res = _compile(_BASE)
    assert list(res.errors) == []
    spec = lower(prog)
    [list_route] = [
        r for r in spec.routes
        if r.content_ref == "sessions" and r.kind == RouteKind.LIST
    ]
    assert list_route.row_filter is not None
    assert list_route.row_filter.kind == "ownership"
    assert list_route.row_filter.field == "player_principal"


def test_get_one_route_gets_row_filter_when_view_is_their_own():
    prog, res = _compile(_BASE)
    spec = lower(prog)
    [get_one] = [
        r for r in spec.routes
        if r.content_ref == "sessions" and r.kind == RouteKind.GET_ONE
    ]
    assert get_one.row_filter == RowFilterSpec(kind="ownership", field="player_principal")


def test_update_route_gets_row_filter_when_update_is_their_own():
    prog, res = _compile(_BASE)
    spec = lower(prog)
    [upd] = [
        r for r in spec.routes
        if r.content_ref == "sessions" and r.kind == RouteKind.UPDATE
    ]
    assert upd.row_filter == RowFilterSpec(kind="ownership", field="player_principal")


def test_create_route_never_gets_row_filter():
    """CREATE routes have no existing row to filter against — the runtime
    stamps owner=the-user.id at insert time (Phase 6a.5)."""
    prog, res = _compile(_BASE)
    spec = lower(prog)
    [create_route] = [
        r for r in spec.routes
        if r.content_ref == "sessions" and r.kind == RouteKind.CREATE
    ]
    assert create_route.row_filter is None


def test_no_their_own_yields_no_row_filter():
    """Sanity: a content without `their own` rules has plain routes
    regardless of whether ownership is declared."""
    src = _BASE.replace(
        "Anyone with \"x.read\" can view their own sessions",
        "Anyone with \"x.read\" can view sessions",
    ).replace(
        "Anyone with \"x.write\" can update their own sessions",
        "Anyone with \"x.write\" can update sessions",
    )
    prog, res = _compile(src)
    spec = lower(prog)
    for route in spec.routes:
        if route.content_ref == "sessions":
            assert route.row_filter is None


# ── TERMIN-S053: their_own without ownership ──

def test_S053_their_own_without_ownership_block():
    src = '''Application: T
  Description: t

Identity:
  Scopes are "x"
  A "user" has "x"

Content called "items":
  Each item has a name which is text, required
  Anyone with "x" can view their own items
'''
    _, res = _compile(src)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S053" in codes


def test_S053_does_not_fire_when_ownership_present():
    """The happy path: their_own + ownership declared = no S053."""
    _, res = _compile(_BASE)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S053" not in codes


def test_S053_does_not_fire_for_non_their_own_rules():
    """A plain rule without `their own` doesn't need ownership."""
    src = '''Application: T
  Description: t

Identity:
  Scopes are "x"
  A "user" has "x"

Content called "items":
  Each item has a name which is text, required
  Anyone with "x" can view items
'''
    _, res = _compile(src)
    codes = {e.code for e in res.errors}
    assert "TERMIN-S053" not in codes


# ── Combined verbs ──

def test_their_own_with_combined_verbs():
    """The verb list before `their own` can be multi-verb."""
    src = '''Application: T
  Description: t

Identity:
  Scopes are "x"
  A "user" has "x"

Content called "sessions":
  Each session has a player_principal which is principal, required, unique
  Each session is owned by player_principal
  Anyone with "x" can view, update, or delete their own sessions
'''
    prog, res = _compile(src)
    # Should land cleanly with no errors
    codes = {e.code for e in res.errors}
    assert "TERMIN-S053" not in codes
    [sessions] = [c for c in prog.contents if c.name == "sessions"]
    [rule] = sessions.access_rules
    assert rule.their_own is True
    # All three verbs captured
    assert set(rule.verbs) >= {"view", "update", "delete"}
