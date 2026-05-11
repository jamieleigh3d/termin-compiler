# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""When-rule Update action (Gap #5 from Airlock-on-Termin slice A3a).

When-rules currently support three body actions:

  * Create <article> <name> with <fields>     (event_action_line)
  * Send <content> to "<channel>"             (event_send_line)
  * Append to <content>.<field> as "<kind>" with body `<expr>`
                                                (append_action_line)

The Airlock OVERSEER rules need a fourth: ``Update <content>:
<field> = `<cel-expr>``. Each OVERSEER trigger must single-shot
per session — without a way to flip the
``session.overseer_X_fired`` flag in the rule body, the rule
re-fires on every subsequent player append and spams the chat.

Source form:

  When `<predicate>`:
    Append to sessions.conversation_log as "system_event" with body `...`
    Update sessions: overseer_time_warning_1_fired = `true`
    Log level: INFO

Semantics:

  * Update targets a record on `<content>` — for OVERSEER it's the
    same parent session whose conversation_log just got the
    upstream append.
  * The parent record id is `record["id"]` from the predicate
    context, mirroring the Append action's id-resolution path.
  * The CEL expression is evaluated against the same predicate
    context (`appended_entry`, `<singular>.<field>` aliases,
    `the_user`, `now`).
  * The runtime applies the patch via ``storage.update``.

This file covers the compiler-side parsing + AST + IR + lower.
The runtime executor is tested via the integration shim in
termin-server's test_integration.py.
"""

from __future__ import annotations

import pytest

from termin.classify import classify_line
from termin.peg_parser import parse_peg as parse


_SRC_BASIC = '''Application: Update Action Smoke
  Description: When-rule with Update action
Id: 5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b

Identity:
  Scopes are "play"
  Anonymous has "play"

Content called "sessions":
  Each session has a name which is text
  Each session has counter which is a whole number, defaults to 0
  Each session has fired which is yes or no, defaults to no
  Each session has a conversation which is conversation
  Anyone with "play" can view sessions
  Anyone with "play" can update sessions
  Anyone with "play" can create sessions
  Anyone with "play" can append to their own sessions.conversation

When `appended_entry.kind == "user" && !session.fired`:
  Append to sessions.conversation as "system_event" with body `"trigger fired"`
  Update sessions: fired = `true`
  Log level: INFO
'''


class TestUpdateActionClassification:
    def test_update_action_line_classifies(self):
        line = "Update sessions: fired = `true`"
        assert classify_line(line) == "update_action_line"

    def test_update_action_with_complex_cel(self):
        line = "Update sessions: counter = `session.counter + 1`"
        assert classify_line(line) == "update_action_line"

    def test_update_in_directive_body_does_not_misclassify(self):
        """Just a regression check — the update prefix should
        ONLY route to update_action_line at the When-rule body
        level, not when 'Update' appears inside a Directive
        block (the joined directive line doesn't start with
        'Update '; it starts with 'Directive is')."""
        line = (
            "Directive is ```You should Update the user when "
            'something changes. The "Update" tool is available.```'
        )
        # Should classify as compute_directive_line, not
        # update_action_line — the prefix-loop entry for
        # 'Directive is' wins.
        assert classify_line(line) == "compute_directive_line"


class TestUpdateActionParses:
    def test_when_rule_with_update_parses_clean(self):
        program, errors = parse(_SRC_BASIC)
        assert errors.ok, f"parse errors: {errors.messages}"
        assert program is not None

    def test_update_action_appears_in_event_actions(self):
        program, errors = parse(_SRC_BASIC)
        assert errors.ok
        assert len(program.events) == 1
        ev = program.events[0]
        # The Append + Update both land in actions.
        assert len(ev.actions) == 2
        # Find the Update action (carries update_content).
        updates = [a for a in ev.actions if getattr(a, "update_content", "")]
        assert len(updates) == 1, (
            f"expected one Update action; got actions={[type(a).__name__ for a in ev.actions]}"
        )
        u = updates[0]
        assert u.update_content == "sessions"
        assert len(u.update_assignments) == 1
        col, expr = u.update_assignments[0]
        assert col == "fired"
        assert expr == "true"


class TestUpdateActionLowers:
    def test_update_action_in_ir(self):
        from termin.lower import lower
        program, errors = parse(_SRC_BASIC)
        assert errors.ok
        spec = lower(program)
        assert len(spec.events) == 1
        ev = spec.events[0]
        update_actions = [
            a for a in ev.actions if getattr(a, "update_content", "")
        ]
        assert len(update_actions) == 1
        u = update_actions[0]
        assert u.update_content == "sessions"
        assert u.update_assignments == (("fired", "true"),)


class TestUpdateActionMultipleAssignments:
    """Multiple Update lines in the same When-rule body are
    independent actions — each one can target a different content
    or a different field. (Multi-field assignment in one Update
    line is a v0.10 syntactic-sugar nice-to-have; for v0.9.4 the
    one-field-per-line form keeps the source mechanically simple.)
    """

    _SRC = '''Application: Multi Update Smoke
  Description: When-rule with two Update actions
Id: 6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c

Identity:
  Scopes are "play"
  Anonymous has "play"

Content called "sessions":
  Each session has a name which is text
  Each session has counter which is a whole number, defaults to 0
  Each session has fired which is yes or no, defaults to no
  Each session has a conversation which is conversation
  Anyone with "play" can view sessions
  Anyone with "play" can update sessions
  Anyone with "play" can create sessions
  Anyone with "play" can append to their own sessions.conversation

When `appended_entry.kind == "user"`:
  Update sessions: counter = `session.counter + 1`
  Update sessions: fired = `true`
  Log level: INFO
'''

    def test_two_update_actions_both_present(self):
        program, errors = parse(self._SRC)
        assert errors.ok
        ev = program.events[0]
        updates = [a for a in ev.actions if getattr(a, "update_content", "")]
        assert len(updates) == 2
        # The two updates target the same content but different fields.
        fields = {a.update_assignments[0][0] for a in updates}
        assert fields == {"counter", "fired"}


class TestUpdateActionWithExistingActionTypes:
    """An Update action coexists with Append, Create, Send,
    Log-level in the same When-rule body."""

    _SRC = '''Application: Mixed Actions Smoke
  Description: When-rule with Append + Update + Log level
Id: 7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d

Identity:
  Scopes are "play"
  Anonymous has "play"

Content called "sessions":
  Each session has a name which is text
  Each session has fired which is yes or no, defaults to no
  Each session has a conversation which is conversation
  Anyone with "play" can view sessions
  Anyone with "play" can update sessions
  Anyone with "play" can create sessions
  Anyone with "play" can append to their own sessions.conversation

When `appended_entry.kind == "user" && !session.fired`:
  Append to sessions.conversation as "system_event" with body `"hello from rule"`
  Update sessions: fired = `true`
  Log level: WARN
'''

    def test_append_and_update_both_in_actions(self):
        program, errors = parse(self._SRC)
        assert errors.ok
        ev = program.events[0]
        # 2 actions (Append + Update); Log level is metadata,
        # not part of the action sequence.
        assert len(ev.actions) == 2
        kinds = []
        for a in ev.actions:
            if getattr(a, "append_field", "") or getattr(a, "field", ""):
                kinds.append("append")
            elif getattr(a, "update_content", ""):
                kinds.append("update")
            else:
                kinds.append("other")
        assert kinds == ["append", "update"], (
            f"expected [append, update] in source order; got {kinds}"
        )
        assert ev.log_level == "WARN"


class TestSourceContentInferenceFromUpdateAction:
    """v0.9.4 (Airlock #11 follow-up to gap #5): the L8 fallback that
    infers ``source_content`` for ``appended_entry``-only When-rules
    only checked AppendAction. Update-only rules (gap #5 added the
    verb) fall through and get ``source_content=""`` in the IR. The
    runtime dispatch loop matches on ``content_name == source_content``
    so the rule never fires.

    Surfaced by the airlock message_count incrementer:

        When `appended_entry.kind == "user"`:
          Update sessions: message_count = `session.message_count + 1`
          Log level: TRACE

    Pre-fix: ``message_count`` stayed 0 across every user append even
    though the rule's predicate evaluated true. Every OVERSEER threshold
    rule that depends on message_count silently never fires.

    Fix: extend the L8 fallback in ``lower._lower_events`` to also
    consider EventAction with non-empty ``update_content`` as a
    routing key (mirrors the AppendAction case).
    """

    _SRC_UPDATE_ONLY = '''Application: Update-Only Smoke
  Description: appended_entry-only When-rule with sole Update action
Id: 8a9b0c1d-2e3f-4a5b-8c6d-7e8f9a0b1c2d

Identity:
  Scopes are "play"
  Anonymous has "play"

Content called "sessions":
  Each session has counter which is a whole number, defaults to 0
  Each session has a conversation which is conversation
  Anyone with "play" can view sessions
  Anyone with "play" can update sessions
  Anyone with "play" can create sessions
  Anyone with "play" can append to their own sessions.conversation

When `appended_entry.kind == "user"`:
  Update sessions: counter = `session.counter + 1`
  Log level: TRACE
'''

    def test_source_content_inferred_from_update_target(self):
        from termin.lower import lower
        program, errors = parse(self._SRC_UPDATE_ONLY)
        assert errors.ok
        spec = lower(program)
        assert len(spec.events) == 1
        ev = spec.events[0]
        # The runtime dispatcher matches content_name == source_content,
        # so an empty source_content here means the rule never fires.
        assert ev.source_content == "sessions", (
            f"Update-only When-rule should infer source_content from "
            f"update target; got {ev.source_content!r}"
        )

    _SRC_UPDATE_THEN_APPEND = '''Application: Update Before Append
  Description: appended_entry-only When-rule with Update first, Append second
Id: 9b0c1d2e-3f4a-5b6c-8d7e-8f9a0b1c2d3e

Identity:
  Scopes are "play"
  Anonymous has "play"

Content called "sessions":
  Each session has counter which is a whole number, defaults to 0
  Each session has a conversation which is conversation
  Anyone with "play" can view sessions
  Anyone with "play" can update sessions
  Anyone with "play" can create sessions
  Anyone with "play" can append to their own sessions.conversation

When `appended_entry.kind == "user"`:
  Update sessions: counter = `session.counter + 1`
  Append to sessions.conversation as "system_event" with body `"counted"`
  Log level: INFO
'''

    def test_first_action_wins_when_both_present(self):
        """When the rule has both Update and Append actions, the L8
        fallback breaks at the first match. The order of attempts
        (Update first vs Append first) doesn't matter because both
        target the same content (sessions) — the test pins behavior
        to whatever the first-match resolution is so a future
        refactor doesn't silently change semantics."""
        from termin.lower import lower
        program, errors = parse(self._SRC_UPDATE_THEN_APPEND)
        assert errors.ok
        spec = lower(program)
        assert len(spec.events) == 1
        ev = spec.events[0]
        assert ev.source_content == "sessions"
