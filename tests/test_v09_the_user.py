# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 6a.4: `the user` reserved phrase + Principal extension.

Per BRD #3 §4.2:
  - `the user` evaluates to a Principal record with fields:
      id, display_name, is_anonymous, is_system, scopes, preferences
  - Source-level `the user` is rewritten to `the_user` before CEL
    evaluation (CEL doesn't allow spaces in identifiers).
  - Principal dataclass extended with `is_system: bool` and
    `preferences: Mapping[str, Any]`.

Deferred to a future slice: the `Update the user's <content>:` source
construct (BRD §4.3). No example uses it yet; revisit when one does.
"""

from __future__ import annotations

import pytest

from termin_runtime.providers.identity_contract import (
    ANONYMOUS_PRINCIPAL,
    Principal,
)
from termin_runtime.identity import _build_user_dict, _build_the_user_object
from termin_runtime.expression import (
    ExpressionEvaluator,
    _rewrite_the_user,
)


# ── Principal dataclass extensions ──

def test_principal_has_is_system_default_false():
    p = Principal(id="u1", type="human", display_name="Alice")
    assert p.is_system is False


def test_principal_has_preferences_default_empty_dict():
    p = Principal(id="u1", type="human")
    assert p.preferences == {}


def test_principal_can_carry_preferences():
    p = Principal(
        id="u1",
        type="human",
        preferences={"theme": "dark", "locale": "en-US"},
    )
    assert p.preferences["theme"] == "dark"
    assert p.preferences["locale"] == "en-US"


def test_principal_can_be_marked_system():
    p = Principal(id="cron-1", type="service", is_system=True)
    assert p.is_system is True


def test_anonymous_principal_is_not_system():
    """Anonymous is a human, just unauthenticated. Not system."""
    assert ANONYMOUS_PRINCIPAL.is_system is False
    assert ANONYMOUS_PRINCIPAL.is_anonymous is True


# ── _build_the_user_object shape ──

def test_the_user_object_has_brd_aligned_fields():
    p = Principal(
        id="u-42",
        type="human",
        display_name="Alice",
        preferences={"theme": "dark"},
    )
    obj = _build_the_user_object(p, scopes=["x.read"])
    assert obj["id"] == "u-42"
    assert obj["display_name"] == "Alice"
    assert obj["is_anonymous"] is False
    assert obj["is_system"] is False
    assert obj["scopes"] == ["x.read"]
    assert obj["preferences"] == {"theme": "dark"}


def test_the_user_object_for_anonymous_principal():
    obj = _build_the_user_object(ANONYMOUS_PRINCIPAL, scopes=[])
    assert obj["id"] == "anonymous"
    assert obj["is_anonymous"] is True
    assert obj["is_system"] is False
    assert obj["scopes"] == []
    assert obj["preferences"] == {}


def test_the_user_object_empty_display_name_when_unset():
    p = Principal(id="u-1", type="human")
    obj = _build_the_user_object(p, scopes=[])
    assert obj["display_name"] == ""


# ── _build_user_dict integration ──

def test_user_dict_contains_the_user_key():
    p = Principal(id="u-1", type="human", display_name="Alice")
    d = _build_user_dict(p, role_name="user", scopes=["x.read"])
    assert "the_user" in d


def test_user_dict_the_user_matches_brd_shape():
    p = Principal(
        id="u-1",
        type="human",
        display_name="Alice",
        preferences={"theme": "auto"},
    )
    d = _build_user_dict(p, role_name="user", scopes=["x.read"])
    tu = d["the_user"]
    assert tu["id"] == "u-1"
    assert tu["display_name"] == "Alice"
    assert tu["preferences"]["theme"] == "auto"


def test_user_dict_legacy_User_still_present():
    """Back-compat: the legacy User key (PascalCase fields) stays for
    code that already uses it."""
    p = Principal(id="u-1", type="human", display_name="Alice")
    d = _build_user_dict(p, role_name="user", scopes=["x.read"])
    assert "User" in d
    assert "Name" in d["User"]
    assert "Authenticated" in d["User"]


# ── CEL preprocessor: `the user` → `the_user` ──

def test_rewrite_replaces_the_user_with_the_user():
    assert _rewrite_the_user("the user.id") == "the_user.id"


def test_rewrite_preserves_unrelated_strings():
    assert _rewrite_the_user("record.status == 'pending'") == "record.status == 'pending'"
    assert _rewrite_the_user("") == ""


def test_rewrite_handles_multiple_occurrences():
    src = "the user.id == record.owner && the user.is_anonymous == false"
    rewritten = _rewrite_the_user(src)
    assert "the_user.id" in rewritten
    assert "the_user.is_anonymous" in rewritten
    assert "the user" not in rewritten


def test_rewrite_word_boundary_safe():
    """The pattern uses \\bthe user\\b so substrings like
    `\\\"weather user\\\"` (hypothetical) are not stomped."""
    # Use a string that contains "the user" as a substring of a longer
    # token. The regex word boundaries keep us from matching inside.
    src = "feathersther user.id"  # not a valid identifier; word-boundary won't match
    rewritten = _rewrite_the_user(src)
    # The leading `feathersther` ends with non-word boundary before `user`
    # — so this could match. Better test: confirm `_the_user.id` (with
    # leading underscore) does NOT get rewritten, since `\bthe user` won't
    # match after `_`.
    src2 = "_the user.id"
    out = _rewrite_the_user(src2)
    # `_` is a word char, so `\bthe` requires boundary before `t`. `_t`
    # is word→word, no boundary. So no rewrite.
    assert out == "_the user.id"


def test_rewrite_does_not_double_apply():
    """If text already has `the_user`, leave it alone."""
    assert _rewrite_the_user("the_user.id") == "the_user.id"


# ── End-to-end CEL evaluation through ExpressionEvaluator ──

def test_evaluator_resolves_the_user_id():
    ev = ExpressionEvaluator()
    result = ev.evaluate(
        "the user.id",
        context={
            "the_user": {
                "id": "u-42",
                "display_name": "Alice",
                "is_anonymous": False,
                "is_system": False,
                "scopes": ["x.read"],
                "preferences": {},
            }
        },
    )
    assert result == "u-42"


def test_evaluator_resolves_the_user_is_anonymous():
    ev = ExpressionEvaluator()
    result = ev.evaluate(
        "the user.is_anonymous",
        context={
            "the_user": {
                "id": "anonymous",
                "display_name": "",
                "is_anonymous": True,
                "is_system": False,
                "scopes": [],
                "preferences": {},
            }
        },
    )
    assert result is True


def test_evaluator_resolves_the_user_preference():
    ev = ExpressionEvaluator()
    result = ev.evaluate(
        "the user.preferences.theme",
        context={
            "the_user": {
                "id": "u-1",
                "display_name": "",
                "is_anonymous": False,
                "is_system": False,
                "scopes": [],
                "preferences": {"theme": "dark"},
            }
        },
    )
    assert result == "dark"


def test_evaluator_compares_the_user_id_to_record_field():
    """The canonical ownership predicate from BRD §4.2."""
    ev = ExpressionEvaluator()
    result = ev.evaluate(
        "the user.id == record.player_principal",
        context={
            "the_user": {
                "id": "u-7",
                "display_name": "",
                "is_anonymous": False,
                "is_system": False,
                "scopes": [],
                "preferences": {},
            },
            "record": {"player_principal": "u-7"},
        },
    )
    assert result is True


def test_legacy_User_still_evaluates():
    """Back-compat: `User.Authenticated` keeps working. Both `User` and
    `the_user` bindings live in the context."""
    ev = ExpressionEvaluator()
    result = ev.evaluate(
        "User.Authenticated",
        context={
            "User": {
                "Username": "alice",
                "Name": "Alice",
                "FirstName": "Alice",
                "Role": "user",
                "Scopes": ["x.read"],
                "Authenticated": True,
            }
        },
    )
    assert result is True
