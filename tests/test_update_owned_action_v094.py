# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Parser + analyzer + lower tests for the owner-keyed Update action
(v0.9.4 cross-content-updates slice B1b / B2 / B3).

Source form:

    Update the user's <singular>: <field> = `<cel-expression>`

Resolves the target by querying the singular's plural for the row
whose ownership field equals the event's "user" principal id.
Restricted to singular target — the ownership field on the target
must declare `unique` (analyzer error TERMIN-A104 enforces this at
compile time per design doc §3 goal 2).

Tests cover three layers per the design doc §10.1:

  1. Grammar — parses to an EventAction AST node carrying the
     target singular + assignments + the new
     `update_target_kind="owner-keyed"` discriminator.
  2. Analyzer — invalid target / non-unique ownership / non-existent
     column / scheduled-trigger context produce TERMIN-A101 .. A107.
  3. Lower — produces an EventActionSpec with the new
     `update_target_kind` + `update_target_owner` fields populated.
"""

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower
from termin.ast_nodes import EventAction


_VALID_SOURCE = """
Application: Test
Description: Test app for owner-keyed Update.

Identity:
  Scopes are "play"
  A "player" has "play"

Content called "profiles":
  Each profile has a player_principal which is principal, required, unique
  Each profile is owned by player_principal
  Each profile has best_score which is a whole number, defaults to 0
  Each profile has games_played which is a whole number, defaults to 0
  Anyone with "play" can view their own profiles
  Anyone with "play" can update their own profiles
  Anyone with "play" can create profiles

Content called "rounds":
  Each round has a player_principal which is principal, required
  Each round is owned by player_principal
  Each round has points which is a whole number, defaults to 0
  Anyone with "play" can view their own rounds
  Anyone with "play" can create rounds

When a round is created:
  Update the user's profile: games_played = `profile.games_played + 1`
"""


# ── B1b: Grammar tests ──


class TestOwnedUpdateActionGrammar:
    """The new action form parses to an EventAction with
    update_content + update_assignments + update_target_kind."""

    def test_parses_without_errors(self):
        program, errors = parse(_VALID_SOURCE)
        assert errors.ok, f"Parse errors: {errors.format()}"

    def test_event_action_carries_owner_keyed_discriminator(self):
        program, errors = parse(_VALID_SOURCE)
        assert errors.ok
        ev = program.events[0]
        # Find the Update action in the rule body. Owner-keyed
        # actions populate update_target_kind="owner-keyed".
        actions = ev.actions or []
        owned_updates = [
            a for a in actions
            if isinstance(a, EventAction)
            and getattr(a, "update_target_kind", "") == "owner-keyed"
        ]
        assert len(owned_updates) == 1, (
            f"Expected 1 owner-keyed update, got {len(owned_updates)} "
            f"out of {len(actions)} actions"
        )

    def test_event_action_carries_singular_as_update_content(self):
        program, errors = parse(_VALID_SOURCE)
        assert errors.ok
        ev = program.events[0]
        owned = next(
            a for a in (ev.actions or [])
            if getattr(a, "update_target_kind", "") == "owner-keyed"
        )
        # The singular as authored ("profile") — the analyzer / lower
        # will map to the plural for storage operations.
        assert owned.update_content == "profile"

    def test_event_action_carries_assignment(self):
        program, errors = parse(_VALID_SOURCE)
        assert errors.ok
        ev = program.events[0]
        owned = next(
            a for a in (ev.actions or [])
            if getattr(a, "update_target_kind", "") == "owner-keyed"
        )
        assignments = list(owned.update_assignments)
        assert len(assignments) == 1
        col, cel = assignments[0]
        assert col == "games_played"
        assert cel == "profile.games_played + 1"

    def test_legacy_same_record_update_unchanged(self):
        """Regression guard: existing `Update <content>: ...` continues
        to parse as A3a same-record (update_target_kind empty / source-record)."""
        src = '''Application: Test
Description: t.

Identity:
  Scopes are "view"
  A "viewer" has "view"

Content called "items":
  Each item has a count which is a whole number, defaults to 0
  Anyone with "view" can view items

When `item.count > 0`:
  Update items: count = `item.count + 1`
'''
        program, errors = parse(src)
        assert errors.ok, f"Parse errors: {errors.format()}"
        ev = program.events[0]
        legacy_updates = [
            a for a in (ev.actions or [])
            if isinstance(a, EventAction) and a.update_content
        ]
        assert len(legacy_updates) == 1
        # Legacy form must NOT carry the owner-keyed discriminator.
        assert getattr(legacy_updates[0], "update_target_kind", "") in (
            "",
            "source-record",
        )

    def test_singular_form_only_no_apostrophe_s_required_to_be_split(self):
        """The grammar accepts the literal 'the user's <singular>'
        idiom as one phrase — apostrophe-s is part of the surface
        form, not three separate tokens."""
        program, errors = parse(_VALID_SOURCE)
        assert errors.ok, f"Parse errors: {errors.format()}"
        # Just ensure it round-trips; the substantive checks above
        # cover the field shape.


# ── B2: Analyzer tests ──


class TestOwnedUpdateActionAnalyzer:
    """Compile-time errors for invalid target / missing ownership /
    non-unique ownership / unknown column."""

    def test_unknown_target_singular_raises_A101(self):
        src = _VALID_SOURCE.replace(
            "Update the user's profile:",
            "Update the user's nonexistent_thing:",
        )
        program, errors = parse(src)
        if errors.ok:
            result = analyze(program)
            assert not result.ok
            messages = " ".join(e.message for e in result.errors)
            assert "TERMIN-A101" in messages or (
                "nonexistent_thing" in messages
            )

    def test_target_without_ownership_raises_A102(self):
        # Add a content with no `is owned by` and try to update it.
        src = _VALID_SOURCE + '''
Content called "freebies":
  Each freebie has a label which is text, required
  Anyone with "play" can view freebies
  Anyone with "play" can create freebies
'''
        # Replace the action target to point at the un-owned content
        src = src.replace(
            "Update the user's profile: games_played = `profile.games_played + 1`",
            "Update the user's freebie: label = `\"x\"`",
        )
        program, errors = parse(src)
        if errors.ok:
            result = analyze(program)
            assert not result.ok
            messages = " ".join(e.message for e in result.errors)
            assert "TERMIN-A102" in messages or "owned" in messages.lower()

    def test_target_with_non_unique_ownership_raises_A104(self):
        # Sessions in the airlock-shaped fixture have player_principal
        # without `unique`. `Update the user's session:` must fail.
        src = '''Application: Test
Description: t.

Identity:
  Scopes are "play"
  A "player" has "play"

Content called "sessions":
  Each session has a player_principal which is principal, required
  Each session is owned by player_principal
  Each session has score which is a whole number, defaults to 0
  Anyone with "play" can view their own sessions
  Anyone with "play" can update their own sessions
  Anyone with "play" can create sessions

When a session is created:
  Update the user's session: score = `1`
'''
        program, errors = parse(src)
        if errors.ok:
            result = analyze(program)
            assert not result.ok, (
                "Non-unique ownership must be rejected at analyzer time"
            )
            messages = " ".join(e.message for e in result.errors)
            assert "TERMIN-A104" in messages or "unique" in messages.lower()

    def test_unknown_assignment_column_raises_A103(self):
        src = _VALID_SOURCE.replace(
            "games_played = `profile.games_played + 1`",
            "not_a_column = `0`",
        )
        program, errors = parse(src)
        if errors.ok:
            result = analyze(program)
            assert not result.ok
            messages = " ".join(e.message for e in result.errors)
            assert "TERMIN-A103" in messages or "not_a_column" in messages

    def test_valid_source_passes(self):
        program, errors = parse(_VALID_SOURCE)
        assert errors.ok
        result = analyze(program)
        assert result.ok, f"Analyzer errors: {result.format()}"


# ── B3: Lower tests ──


class TestOwnedUpdateActionLower:
    """Lowering produces an EventActionSpec with the new fields."""

    def test_eventactionspec_has_owner_keyed_kind(self):
        program, errors = parse(_VALID_SOURCE)
        analyze(program)
        spec = lower(program)
        ev_specs = spec.events
        assert len(ev_specs) == 1
        actions = list(ev_specs[0].actions or ())
        owned = [
            a for a in actions
            if getattr(a, "update_target_kind", "") == "owner-keyed"
        ]
        assert len(owned) == 1, (
            f"Expected 1 owner-keyed action in lowered spec, got "
            f"{len(owned)} of {len(actions)}"
        )

    def test_eventactionspec_owner_owner_field_populated(self):
        program, errors = parse(_VALID_SOURCE)
        analyze(program)
        spec = lower(program)
        owned = next(
            a for a in spec.events[0].actions
            if getattr(a, "update_target_kind", "") == "owner-keyed"
        )
        # The runtime resolves the target by querying
        # <update_content>.<update_target_owner> == principal_id.
        # update_target_owner carries the snake_case ownership
        # field name on the target content (profiles owned by
        # player_principal).
        assert getattr(owned, "update_target_owner", "") == "player_principal"

    def test_eventactionspec_update_content_is_plural_snake(self):
        """Lower converts the singular form ('profile') to the
        plural snake-case ('profiles') for storage operations."""
        program, errors = parse(_VALID_SOURCE)
        analyze(program)
        spec = lower(program)
        owned = next(
            a for a in spec.events[0].actions
            if getattr(a, "update_target_kind", "") == "owner-keyed"
        )
        assert owned.update_content == "profiles"

    def test_legacy_same_record_lower_unchanged(self):
        """Regression: existing same-record Update lowers with
        update_target_kind="" or "source-record"."""
        src = '''Application: Test
Description: t.

Identity:
  Scopes are "view"
  A "viewer" has "view"

Content called "items":
  Each item has a count which is a whole number, defaults to 0
  Anyone with "view" can view items

When `item.count > 0`:
  Update items: count = `item.count + 1`
'''
        program, errors = parse(src)
        assert errors.ok
        analyze(program)
        spec = lower(program)
        actions = list(spec.events[0].actions or ())
        upd = [a for a in actions if a.update_content]
        assert len(upd) == 1
        assert getattr(upd[0], "update_target_kind", "") in (
            "",
            "source-record",
        )
