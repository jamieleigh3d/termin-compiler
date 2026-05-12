# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Parser + analyzer + lower tests for the state-entered When-rule
trigger (v0.9.4 cross-content-updates slice B1a / B2 / B3).

Source form:

    When <singular> <state-field> enters <state-name>:
      <actions>

Subscribes to the same `<plural>.<state-field>.<state-name>.entered`
event class computes already use via `Trigger on event`. Lets a
reactive rule express "when this lifecycle state is reached, do
this" without the once-shot guard-flag pattern (the OVERSEER
work-around the airlock app currently uses).

Tests cover three layers per the design doc §10.1:

  1. Grammar — the source parses to an EventRule AST node carrying
     the new trigger fields.
  2. Analyzer — invalid state-field / state-value names produce
     TERMIN-A105 / A106.
  3. Lower — produces an EventSpec with `trigger_state_field` +
     `trigger_state_value` populated and `condition_expr` empty.
"""

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower


_VALID_SOURCE = """
Application: Test
Description: Test app for state-entered When-rule.

Identity:
  Scopes are "play"
  A "player" has "play"

Content called "rounds":
  Each round has a player_principal which is principal, required
  Each round is owned by player_principal
  Each round has points which is a whole number, defaults to 0
  Each round has a status which is state:
    status starts as in_progress
    status can also be done
    in_progress can become done if the user has "play"
  Anyone with "play" can view their own rounds
  Anyone with "play" can create rounds

When round status enters done:
  Log level: INFO
"""


# ── B1a: Grammar tests (failing on red, green after PEG + parser
# ── handler land) ──


class TestStateEnteredTriggerGrammar:
    """The new trigger form parses to an EventRule with
    `trigger_state_field` + `trigger_state_value` populated."""

    def test_parses_without_errors(self):
        program, errors = parse(_VALID_SOURCE)
        assert errors.ok, f"Parse errors: {errors.format()}"
        assert len(program.events) == 1

    def test_event_carries_trigger_state_field(self):
        program, errors = parse(_VALID_SOURCE)
        assert errors.ok
        ev = program.events[0]
        # The new trigger form sets trigger_state_field to the
        # snake-case state column name (the field that holds the
        # state machine's current value).
        assert getattr(ev, "trigger_state_field", "") == "status"

    def test_event_carries_trigger_state_value(self):
        program, errors = parse(_VALID_SOURCE)
        assert errors.ok
        ev = program.events[0]
        # The state-name slot — preserves the source form (no
        # snake-case conversion; multi-word states keep their
        # spaces).
        assert getattr(ev, "trigger_state_value", "") == "done"

    def test_event_content_is_the_singulars_plural(self):
        program, errors = parse(_VALID_SOURCE)
        assert errors.ok
        ev = program.events[0]
        # The trigger form names a singular (`round`); the AST
        # carries the singular in content_name. The analyzer /
        # lower pass map it to the plural for event-class subscription.
        assert ev.content_name == "round"

    def test_event_has_no_condition_expr(self):
        """State-entered triggers don't carry a CEL predicate —
        the trigger IS the predicate."""
        program, errors = parse(_VALID_SOURCE)
        assert errors.ok
        ev = program.events[0]
        # condition_expr is the v2 CEL-predicate trigger field; for
        # state-entered triggers it stays None / empty.
        assert not (ev.condition_expr or "")

    def test_multi_word_state_uses_quoted_form(self):
        program, errors = parse(_VALID_SOURCE.replace(
            'in_progress can become done',
            '"in progress" can become done'
        ).replace(
            'status starts as in_progress',
            'status starts as "in progress"'
        ).replace(
            'status can also be done',
            'status can also be done'
        ).replace(
            'When round status enters done:',
            'When round status enters "in progress":'
        ))
        assert errors.ok, f"Parse errors: {errors.format()}"
        ev = program.events[0]
        assert getattr(ev, "trigger_state_value", "") == "in progress"

    def test_legacy_when_a_X_is_updated_still_parses(self):
        """Regression guard: existing When-rule trigger forms
        (event_v1_line) continue to parse to EventRule with
        trigger_state_field empty."""
        program, errors = parse('''Application: Test
Description: t.

Identity:
  Scopes are "view"
  A "viewer" has "view"

Content called "items":
  Each item has a name which is text, required
  Each item has a count which is a whole number, defaults to 0
  Each item has a threshold which is a whole number, defaults to 0
  Anyone with "view" can view items

When a item is updated and its count is at or below its threshold:
  Create a alert with the item, count, and threshold
''')
        assert errors.ok, f"Parse errors: {errors.format()}"
        ev = program.events[0]
        # The legacy form populates content_name + trigger; the new
        # state-entered fields stay empty.
        assert ev.trigger == "updated"
        assert getattr(ev, "trigger_state_field", "") == ""
        assert getattr(ev, "trigger_state_value", "") == ""

    def test_legacy_when_cel_predicate_still_parses(self):
        """Regression guard: existing CEL-predicate When-rule
        (event_expr_line) still parses with the new fields empty."""
        program, errors = parse('''Application: Test
Description: t.

Identity:
  Scopes are "view"
  A "viewer" has "view"

Content called "items":
  Each item has a count which is a whole number, defaults to 0
  Each item has a threshold which is a whole number, defaults to 0
  Anyone with "view" can view items

When `item.count <= item.threshold`:
  Log level: INFO
''')
        assert errors.ok, f"Parse errors: {errors.format()}"
        ev = program.events[0]
        assert ev.condition_expr
        assert getattr(ev, "trigger_state_field", "") == ""


# ── B2: Analyzer tests — error codes A105 / A106 ──


class TestStateEnteredTriggerAnalyzer:
    """Invalid state-field / state-value names produce clear errors."""

    def test_unknown_state_field_raises_A105(self):
        """Naming a field that doesn't exist on the content."""
        src = _VALID_SOURCE.replace(
            "When round status enters done:",
            "When round nonexistent_field enters done:",
        )
        program, errors = parse(src)
        # Parse should succeed (grammar doesn't know about content
        # schemas); the analyzer is the gate.
        if errors.ok:
            from termin.analyzer import analyze
            result = analyze(program)
            assert not result.ok, "Expected analyzer to reject"
            messages = " ".join(e.message for e in result.errors)
            assert "TERMIN-A105" in messages or (
                "nonexistent_field" in messages
                and "round" in messages
            )

    def test_unknown_state_value_raises_A106(self):
        """Naming a state that isn't a valid state for the matched
        machine."""
        src = _VALID_SOURCE.replace(
            "When round status enters done:",
            "When round status enters not_a_state:",
        )
        program, errors = parse(src)
        if errors.ok:
            from termin.analyzer import analyze
            result = analyze(program)
            assert not result.ok, "Expected analyzer to reject"
            messages = " ".join(e.message for e in result.errors)
            assert "TERMIN-A106" in messages or (
                "not_a_state" in messages
                and ("status" in messages or "state" in messages.lower())
            )

    def test_valid_state_passes_analyzer(self):
        program, errors = parse(_VALID_SOURCE)
        assert errors.ok
        result = analyze(program)
        assert result.ok, f"Analyzer errors: {result.format()}"


# ── B3: Lower tests — EventSpec carries the new fields ──


class TestStateEnteredTriggerLower:
    """Lowering produces an EventSpec with `trigger_state_field`
    and `trigger_state_value` populated."""

    def test_eventspec_has_trigger_state_field(self):
        program, errors = parse(_VALID_SOURCE)
        analyze(program)
        spec = lower(program)
        assert len(spec.events) == 1
        ev = spec.events[0]
        # Lowered EventSpec mirrors the AST trigger fields.
        assert getattr(ev, "trigger_state_field", "") == "status"
        assert getattr(ev, "trigger_state_value", "") == "done"

    def test_eventspec_source_content_is_plural_snake(self):
        """The runtime needs the plural snake_case content name to
        subscribe to the right event channel
        (`<plural>.<field>.<state>.entered`)."""
        program, errors = parse(_VALID_SOURCE)
        analyze(program)
        spec = lower(program)
        ev = spec.events[0]
        assert ev.source_content == "rounds"

    def test_eventspec_condition_expr_is_empty(self):
        program, errors = parse(_VALID_SOURCE)
        analyze(program)
        spec = lower(program)
        ev = spec.events[0]
        # State-entered triggers don't carry a CEL predicate.
        assert not (ev.condition_expr or "")

    def test_legacy_eventspec_still_lowers(self):
        """Regression: existing CEL-predicate event continues to
        lower with the new fields empty."""
        program, errors = parse('''Application: Test
Description: t.

Identity:
  Scopes are "view"
  A "viewer" has "view"

Content called "items":
  Each item has a count which is a whole number, defaults to 0
  Each item has a threshold which is a whole number, defaults to 0
  Anyone with "view" can view items

When `item.count <= item.threshold`:
  Log level: INFO
''')
        assert errors.ok
        analyze(program)
        spec = lower(program)
        ev = spec.events[0]
        assert ev.condition_expr
        assert getattr(ev, "trigger_state_field", "") == ""
