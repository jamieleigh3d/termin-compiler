# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""CEL-condition state transitions (Gap #3 from Airlock-on-Termin slice A3a).

The PEG grammar already supports two transition forms in
``sm_transition_line`` (termin.peg lines 266-273):

  scope-gated:  ``X can become Y if the user has "scope"``
  CEL-gated:    ``X can become Y if `<cel-expression>` ``

The parser handler at ``parse_handlers.py:498-505`` recognized the
CEL form but stored the CEL text into ``required_scope`` as a
placeholder. The analyzer then rejected the CEL string as an
undefined scope ("Transition references undefined scope ...").

This slice promotes the CEL form to a first-class transition
condition. ``Transition.condition_expr`` carries the CEL; the
analyzer skips the scope check when it's set; the runtime
state engine evaluates the condition at ``state.transition(...)``
time and refuses the transition when it returns false.

This is the "guarded CEL transition" interpretation — the runtime
checks the condition AT the explicit transition call. The
"auto-firing on field change" interpretation (the v0.9.4 design
doc §4.3 vision) requires a runtime event-watcher and stays a
v0.10 follow-up. The guarded form is enough for the Airlock case
where ARIA's ``repair_execute`` correct-fix tool sets
``hatch_unlocked = true`` and explicitly calls
``state.transition(session, "scoring")`` — the runtime then
evaluates ``session.hatch_unlocked`` and allows the transition.
"""

from __future__ import annotations

import pytest

from termin.peg_parser import parse_peg as parse


_SRC_BASIC_CEL_TRANSITION = '''Application: Lifecycle CEL Smoke
  Description: Single CEL-conditioned transition
Id: 1c2e3f4a-5b6c-4d7e-8f9a-0b1c2d3e4f5a

Identity:
  Scopes are "play"
  Anonymous has "play"

Content called "items":
  Each item has a name which is text
  Each item has hatch_open which is yes or no, defaults to no
  Each item has a lifecycle which is state:
    lifecycle starts as closed
    lifecycle can also be open
    closed can become open if `item.hatch_open`
  Anyone with "play" can view items
  Anyone with "play" can update items
  Anyone with "play" can create items
'''


_SRC_MIXED_TRANSITIONS = '''Application: Lifecycle Mixed Smoke
  Description: One scope-gated and one CEL-gated transition on the same machine
Id: 2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f

Identity:
  Scopes are "play"
  Anonymous has "play"

Content called "tasks":
  Each task has a name which is text
  Each task has done which is yes or no, defaults to no
  Each task has a lifecycle which is state:
    lifecycle starts as todo
    lifecycle can also be doing or done_state
    todo can become doing if the user has "play"
    doing can become done_state if `task.done`
  Anyone with "play" can view tasks
  Anyone with "play" can update tasks
  Anyone with "play" can create tasks
'''


_SRC_REAL_AIRLOCK_SHAPE = '''Application: Airlock Lifecycle Smoke
  Description: Mirrors the real airlock.termin lifecycle shape
Id: 3a4b5c6d-7e8f-4a9b-8c0d-2e3f4a5b6c7d

Identity:
  Scopes are "play"
  Anonymous has "play"

Content called "sessions":
  Each session has hatch_unlocked which is yes or no, defaults to no
  Each session has scores which is text
  Each session has a lifecycle which is state:
    lifecycle starts as scenario
    lifecycle can also be scoring or complete
    scenario can become scoring if `session.hatch_unlocked`
    scoring can become complete if `session.scores != null`
  Anyone with "play" can view sessions
  Anyone with "play" can update sessions
  Anyone with "play" can create sessions
'''


class TestCelTransitionParses:
    """Source containing a CEL-conditioned transition must parse
    cleanly. The Transition AST node must carry the CEL in
    ``condition_expr`` and leave ``required_scope`` empty/None."""

    def test_cel_transition_parses_clean(self):
        program, errors = parse(_SRC_BASIC_CEL_TRANSITION)
        assert errors.ok, f"unexpected parse errors: {errors.messages}"
        assert program is not None

    def test_cel_transition_stored_in_condition_expr(self):
        program, errors = parse(_SRC_BASIC_CEL_TRANSITION)
        assert errors.ok
        sm_list = program.state_machines
        assert len(sm_list) == 1
        sm = sm_list[0]
        assert len(sm.transitions) == 1
        t = sm.transitions[0]
        assert t.from_state == "closed"
        assert t.to_state == "open"
        # The CEL goes into condition_expr; required_scope is empty.
        assert t.condition_expr == "item.hatch_open", (
            f"expected condition_expr='item.hatch_open', got "
            f"condition_expr={t.condition_expr!r}, "
            f"required_scope={t.required_scope!r}"
        )
        assert not t.required_scope, (
            f"required_scope must be empty when condition_expr is set; "
            f"got {t.required_scope!r}"
        )

    def test_mixed_transitions_each_get_their_own_form(self):
        """A machine with both scope and CEL transitions: each
        Transition node carries the right discriminator."""
        program, errors = parse(_SRC_MIXED_TRANSITIONS)
        assert errors.ok
        sm = program.state_machines[0]
        assert len(sm.transitions) == 2
        # First transition: scope-gated
        scope_t = next(t for t in sm.transitions if t.from_state == "todo")
        assert scope_t.required_scope == "play"
        assert not scope_t.condition_expr
        # Second transition: CEL-gated
        cel_t = next(t for t in sm.transitions if t.from_state == "doing")
        assert cel_t.condition_expr == "task.done"
        assert not cel_t.required_scope

    def test_airlock_shape_compiles(self):
        """The real Airlock-on-Termin lifecycle shape from the
        slice A3a authoring."""
        program, errors = parse(_SRC_REAL_AIRLOCK_SHAPE)
        assert errors.ok, f"unexpected errors: {errors.messages}"
        sm = program.state_machines[0]
        cel_transitions = [t for t in sm.transitions if t.condition_expr]
        assert len(cel_transitions) == 2
        conditions = {t.condition_expr for t in cel_transitions}
        assert "session.hatch_unlocked" in conditions
        assert "session.scores != null" in conditions


class TestCelTransitionLowers:
    """The CEL condition must round-trip through the lowering pass
    into the IR's TransitionSpec.condition_expr."""

    def test_cel_transition_appears_in_ir(self):
        from termin.lower import lower
        program, errors = parse(_SRC_BASIC_CEL_TRANSITION)
        assert errors.ok
        spec = lower(program)
        sm_list = spec.state_machines
        assert len(sm_list) == 1
        ir_sm = sm_list[0]
        assert len(ir_sm.transitions) == 1
        ir_t = ir_sm.transitions[0]
        assert ir_t.condition_expr == "item.hatch_open"
        # required_scope stays empty in the IR too.
        assert not ir_t.required_scope


class TestNoRegressionOnScopeTransitions:
    """The existing scope-gated transitions must not regress —
    every example in `examples/` uses them, and the TransitionSpec
    field shape must remain compatible."""

    def test_scope_transition_still_lowers_with_required_scope(self):
        from termin.lower import lower
        src = '''Application: Scope Smoke
  Description: Scope transition baseline
Id: 4d5e6f7a-8b9c-4d0e-9f1a-2b3c4d5e6f7a

Identity:
  Scopes are "play"
  Anonymous has "play"

Content called "items":
  Each item has a name which is text
  Each item has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be live
    draft can become live if the user has "play"
  Anyone with "play" can view items
  Anyone with "play" can update items
  Anyone with "play" can create items
'''
        program, errors = parse(src)
        assert errors.ok
        spec = lower(program)
        ir_sm = spec.state_machines[0]
        ir_t = ir_sm.transitions[0]
        assert ir_t.required_scope == "play"
        assert not ir_t.condition_expr
