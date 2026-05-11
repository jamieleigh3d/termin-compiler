# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Lifecycle state-entered side-effects on transitions (Gap #7).

Some state transitions need to write companion fields atomically
with the state-column update — most commonly a "scenario started
at" timestamp on the transition INTO the active phase. Without
runtime support, the .termin author has to either:

  - Issue a separate PUT after the transition (client-controllable
    timestamp; defeats the security invariant); or
  - Write a When-rule that listens for the lifecycle event and
    PATCHes the field (race window between transition and
    PATCH; non-atomic).

v0.9.4 introduces an `entered:` clause on transitions:

    survey can become scenario if the user has "play"
      entered: scenario_started_at = `now`

Semantics:
  - The runtime evaluates each `<field> = \`<cel-expression>\``
    assignment against the record context at transition time.
  - The assignments are applied atomically with the state-column
    update (single storage.update_if call).
  - The CEL evaluation context is the same as the state-machine
    transition's eval context (record under singular alias,
    `record` alias, `the_user`, `now`).

One assignment per `entered:` line; multiple assignments use
multiple `entered:` lines. Mirrors the Update action verb shape
(Gap #5).

Surfaced by Airlock-on-Termin slice A3b smoke. The design doc
§4.3 specifies `scenario_started_at` is set when lifecycle
enters scenario, but no mechanism existed.
"""

from __future__ import annotations

import pytest

from termin.peg_parser import parse_peg


_SRC_BASIC = '''Application: Entered Assignment Test
  Description: v0.9.4 Gap #7 fixture
Id: ad4e5f6a-7b8c-4d9e-8f0a-2b3c4d5e6f7a

Identity:
  Scopes are "play"
  An "anonymous" has "play"

Content called "sessions":
  Each session has a name which is text
  Each session has scenario_started_at which is timestamp
  Each session has a lifecycle which is state:
    lifecycle starts as survey
    lifecycle can also be scenario
    survey can become scenario if the user has "play"
      entered: scenario_started_at = `now`
  Anyone with "play" can view sessions
  Anyone with "play" can update sessions
  Anyone with "play" can create sessions
'''


_SRC_MULTIPLE_ENTERED = '''Application: Multi Entered Test
  Description: Multiple entered: lines per transition
Id: ae5f6a7b-8c9d-4e0f-8a1b-3c4d5e6f7a8b

Identity:
  Scopes are "play"
  An "anonymous" has "play"

Content called "tasks":
  Each task has a name which is text
  Each task has started_at which is timestamp
  Each task has retry_count which is a whole number
  Each task has a status which is state:
    status starts as todo
    status can also be doing
    todo can become doing if the user has "play"
      entered: started_at = `now`
      entered: retry_count = `0`
  Anyone with "play" can view tasks
  Anyone with "play" can update tasks
  Anyone with "play" can create tasks
'''


class TestEnteredClauseParses:
    def test_basic_entered_clause_parses(self):
        program, errors = parse_peg(_SRC_BASIC)
        assert errors.ok, f"unexpected parse errors: {errors.messages}"

    def test_basic_entered_assignment_in_ast(self):
        program, errors = parse_peg(_SRC_BASIC)
        assert errors.ok
        sm = program.state_machines[0]
        assert len(sm.transitions) == 1
        t = sm.transitions[0]
        # The transition has a single entered_assignment.
        assert hasattr(t, "entered_assignments"), (
            "Transition AST node must carry entered_assignments"
        )
        assert len(t.entered_assignments) == 1
        field, expr = t.entered_assignments[0]
        assert field == "scenario_started_at"
        assert expr == "now"

    def test_multiple_entered_clauses_each_parse(self):
        program, errors = parse_peg(_SRC_MULTIPLE_ENTERED)
        assert errors.ok
        sm = program.state_machines[0]
        t = sm.transitions[0]
        assert len(t.entered_assignments) == 2
        fields = [a[0] for a in t.entered_assignments]
        assert "started_at" in fields
        assert "retry_count" in fields


class TestEnteredClauseLowers:
    def test_entered_assignments_appear_in_ir(self):
        from termin.lower import lower
        program, errors = parse_peg(_SRC_BASIC)
        assert errors.ok
        spec = lower(program)
        ir_sm = spec.state_machines[0]
        ir_t = ir_sm.transitions[0]
        assert ir_t.entered_assignments == (
            ("scenario_started_at", "now"),
        ), f"got {ir_t.entered_assignments}"


class TestEnteredAssignmentsAtRuntime:
    """End-to-end: a lifecycle transition with `entered:` clauses
    writes the companion fields atomically."""

    def test_runtime_applies_entered_assignment_on_transition(self, tmp_path):
        from fastapi.testclient import TestClient
        from termin.lower import lower
        from termin_core.ir.serialize import serialize_ir
        from termin_server import create_termin_app

        program, errors = parse_peg(_SRC_BASIC)
        assert errors.ok
        spec = lower(program)
        app = create_termin_app(
            serialize_ir(spec), db_path=str(tmp_path / "entered.db"))
        with TestClient(app) as c:
            r = c.post("/api/v1/sessions", json={"name": "x"})
            assert r.status_code in (200, 201), r.text
            session_id = r.json()["id"]
            # Pre-state: scenario_started_at is null (not set yet).
            pre = c.get(f"/api/v1/sessions/{session_id}").json()
            assert pre.get("scenario_started_at") in (None, ""), (
                f"pre-state expected null scenario_started_at; got {pre}"
            )
            # Transition lifecycle survey → scenario.
            tr = c.post(
                f"/_transition/sessions/lifecycle/{session_id}/scenario",
                json={},
            )
            assert tr.status_code in (200, 201), tr.text
            # Post-state: scenario_started_at is now set (an ISO
            # timestamp from `now()` at transition time).
            post = c.get(f"/api/v1/sessions/{session_id}").json()
            assert post.get("lifecycle") == "scenario"
            ts = post.get("scenario_started_at")
            assert ts not in (None, ""), (
                f"entered: assignment did not fire; "
                f"scenario_started_at is {ts!r} on session {post}"
            )
            # Should look like an ISO-8601 timestamp.
            assert "T" in str(ts) or "-" in str(ts), (
                f"scenario_started_at should be ISO-8601-shaped; "
                f"got {ts!r}"
            )


class TestNoRegressionOnTransitionsWithoutEntered:
    """Existing transitions without entered: clauses must continue
    to work — the field defaults to an empty tuple."""

    def test_legacy_transition_has_empty_entered_assignments(self):
        from termin.lower import lower
        src = '''Application: Legacy Transition
  Description: No entered: clause
Id: af6a7b8c-9d0e-4f1a-8b2c-4d5e6f7a8b9c

Identity:
  Scopes are "play"
  An "anonymous" has "play"

Content called "sessions":
  Each session has a name which is text
  Each session has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be live
    draft can become live if the user has "play"
  Anyone with "play" can view sessions
  Anyone with "play" can update sessions
  Anyone with "play" can create sessions
'''
        program, errors = parse_peg(src)
        assert errors.ok
        spec = lower(program)
        ir_t = spec.state_machines[0].transitions[0]
        assert ir_t.entered_assignments == ()
