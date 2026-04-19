# Copyright 2026 Jamie-Leigh Blake
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for transition feedback (toast/banner) — issue #006.

State machine transitions can declare feedback messages shown to users
after success or failure. Two notification types:
  - toast: auto-dismiss (default 5s), bottom-right
  - banner: persistent (no auto-dismiss by default), stackable

Feedback lines are indented under transitions:
  A draft product can become active if the user has "inventory.write"
    success shows toast `product.name + " is now active"`
    error shows banner "Could not activate product"

TDD: These tests are written RED before the implementation.
"""

import pytest
from termin.peg_parser import parse_peg as parse


# ── Parser: feedback line recognition ──

class TestTransitionFeedbackParsing:
    """Test that feedback lines are parsed and attached to transitions."""

    def _parse_transitions(self, state_block: str):
        """Parse a minimal app with a state machine and return transitions."""
        src = f'''Application: Test
  Description: Test

Users authenticate with stub
Scopes are "manage"
A "user" has "manage"

Content called "items":
  Each item has a name which is text
  Anyone with "manage" can view, create, or update items

{state_block}'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        assert len(program.state_machines) == 1
        return program.state_machines[0].transitions

    def test_transition_without_feedback(self):
        """Transitions without feedback lines should still work."""
        transitions = self._parse_transitions('''State for items called "lifecycle":
  An item starts as "draft"
  An item can also be "active"
  A draft item can become active if the user has "manage"''')
        assert len(transitions) == 1
        assert transitions[0].from_state == "draft"
        assert transitions[0].to_state == "active"
        # No feedback
        assert not transitions[0].feedback

    def test_success_toast_with_cel(self):
        """Success toast with a CEL expression."""
        transitions = self._parse_transitions('''State for items called "lifecycle":
  An item starts as "draft"
  An item can also be "active"
  A draft item can become active if the user has "manage"
    success shows toast `item.name + " is now active"`''')
        assert len(transitions) == 1
        fb = transitions[0].feedback
        assert len(fb) == 1
        assert fb[0].trigger == "success"
        assert fb[0].style == "toast"
        assert fb[0].message == 'item.name + " is now active"'
        assert fb[0].is_expr is True
        assert fb[0].dismiss_seconds is None  # default auto-dismiss

    def test_error_banner_with_literal(self):
        """Error banner with a plain string literal."""
        transitions = self._parse_transitions('''State for items called "lifecycle":
  An item starts as "draft"
  An item can also be "active"
  A draft item can become active if the user has "manage"
    error shows banner "Could not activate item"''')
        fb = transitions[0].feedback
        assert len(fb) == 1
        assert fb[0].trigger == "error"
        assert fb[0].style == "banner"
        assert fb[0].message == "Could not activate item"
        assert fb[0].is_expr is False
        assert fb[0].dismiss_seconds is None  # no auto-dismiss for banner

    def test_both_success_and_error(self):
        """A transition can have both success and error feedback."""
        transitions = self._parse_transitions('''State for items called "lifecycle":
  An item starts as "draft"
  An item can also be "active"
  A draft item can become active if the user has "manage"
    success shows toast `item.name + " activated"`
    error shows banner "Activation failed"''')
        fb = transitions[0].feedback
        assert len(fb) == 2
        assert fb[0].trigger == "success"
        assert fb[0].style == "toast"
        assert fb[1].trigger == "error"
        assert fb[1].style == "banner"

    def test_dismiss_after_seconds(self):
        """Custom dismiss timer."""
        transitions = self._parse_transitions('''State for items called "lifecycle":
  An item starts as "draft"
  An item can also be "active"
  A draft item can become active if the user has "manage"
    success shows banner `item.name + " resolved"` dismiss after 10 seconds''')
        fb = transitions[0].feedback
        assert fb[0].style == "banner"
        assert fb[0].dismiss_seconds == 10

    def test_multiple_transitions_with_feedback(self):
        """Each transition gets its own feedback independently."""
        transitions = self._parse_transitions('''State for items called "lifecycle":
  An item starts as "draft"
  An item can also be "active" or "archived"
  A draft item can become active if the user has "manage"
    success shows toast "Item activated"
  An active item can become archived if the user has "manage"
    success shows toast "Item archived"
    error shows banner "Cannot archive"''')
        assert len(transitions) == 2
        assert len(transitions[0].feedback) == 1
        assert len(transitions[1].feedback) == 2


# ── IR: TransitionSpec feedback field ──

class TestTransitionFeedbackIR:
    """Test that feedback is correctly lowered to the IR."""

    def _compile_transitions(self, state_block: str):
        """Parse and lower, return StateMachineSpec."""
        from termin.lower import lower
        src = f'''Application: Test
  Description: Test

Users authenticate with stub
Scopes are "manage"
A "user" has "manage"

Content called "items":
  Each item has a name which is text
  Anyone with "manage" can view, create, or update items

{state_block}'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        spec = lower(program)
        assert len(spec.state_machines) == 1
        return spec.state_machines[0]

    def test_ir_transition_without_feedback(self):
        sm = self._compile_transitions('''State for items called "lifecycle":
  An item starts as "draft"
  An item can also be "active"
  A draft item can become active if the user has "manage"''')
        t = sm.transitions[0]
        assert t.feedback == ()

    def test_ir_toast_with_cel(self):
        sm = self._compile_transitions('''State for items called "lifecycle":
  An item starts as "draft"
  An item can also be "active"
  A draft item can become active if the user has "manage"
    success shows toast `item.name + " activated"`''')
        t = sm.transitions[0]
        assert len(t.feedback) == 1
        fb = t.feedback[0]
        assert fb.trigger == "success"
        assert fb.style == "toast"
        assert fb.message == 'item.name + " activated"'
        assert fb.is_expr is True
        assert fb.dismiss_seconds is None

    def test_ir_banner_with_dismiss(self):
        sm = self._compile_transitions('''State for items called "lifecycle":
  An item starts as "draft"
  An item can also be "active"
  A draft item can become active if the user has "manage"
    success shows banner "Done" dismiss after 10 seconds''')
        t = sm.transitions[0]
        fb = t.feedback[0]
        assert fb.style == "banner"
        assert fb.dismiss_seconds == 10


# ── Full examples compile correctly ──

class TestFeedbackExamples:
    """Verify the updated examples compile with feedback."""

    def test_warehouse_compiles(self):
        from pathlib import Path
        from termin.lower import lower
        src = Path("examples/warehouse.termin").read_text()
        program, errors = parse(src)
        assert errors.ok, errors.format()
        spec = lower(program)
        # Find the product lifecycle state machine
        sm = [s for s in spec.state_machines if s.machine_name == "product lifecycle"][0]
        # draft → active should have success toast + error banner
        t_activate = [t for t in sm.transitions
                      if t.from_state == "draft" and t.to_state == "active"][0]
        assert len(t_activate.feedback) == 2
        assert t_activate.feedback[0].trigger == "success"
        assert t_activate.feedback[0].style == "toast"
        assert t_activate.feedback[0].is_expr is True
        assert t_activate.feedback[1].trigger == "error"
        assert t_activate.feedback[1].style == "banner"

    def test_helpdesk_compiles(self):
        from pathlib import Path
        from termin.lower import lower
        src = Path("examples/helpdesk.termin").read_text()
        program, errors = parse(src)
        assert errors.ok, errors.format()
        spec = lower(program)
        sm = [s for s in spec.state_machines if s.machine_name == "ticket lifecycle"][0]
        # in progress → resolved should have banner with 10s dismiss
        t_resolve = [t for t in sm.transitions
                     if t.from_state == "in progress" and t.to_state == "resolved"][0]
        assert len(t_resolve.feedback) == 1
        assert t_resolve.feedback[0].style == "banner"
        assert t_resolve.feedback[0].dismiss_seconds == 10

    def test_other_examples_still_compile(self):
        """Examples without feedback should still compile cleanly."""
        from pathlib import Path
        for name in ["hello", "hello_user", "compute_demo", "projectboard",
                     "channel_demo", "channel_simple", "agent_chatbot",
                     "agent_simple", "security_agent", "hrportal"]:
            src = Path(f"examples/{name}.termin").read_text()
            program, errors = parse(src)
            assert errors.ok, f"{name}: {errors.format()}"


# ── IR JSON serialization ──

class TestFeedbackIRSerialization:
    """Test that feedback appears in the serialized IR JSON."""

    def test_feedback_in_json(self):
        from termin.lower import lower
        from dataclasses import asdict
        import json
        src = '''Application: Test
  Description: Test

Users authenticate with stub
Scopes are "manage"
A "user" has "manage"

Content called "items":
  Each item has a name which is text
  Anyone with "manage" can view items

State for items called "lifecycle":
  An item starts as "draft"
  An item can also be "active"
  A draft item can become active if the user has "manage"
    success shows toast `item.name + " activated"`
    error shows banner "Activation failed"'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        spec = lower(program)
        ir_dict = asdict(spec)
        sm = ir_dict["state_machines"][0]
        t = sm["transitions"][0]
        assert "feedback" in t
        assert len(t["feedback"]) == 2
        fb0 = t["feedback"][0]
        assert fb0["trigger"] == "success"
        assert fb0["style"] == "toast"
        assert fb0["message"] == 'item.name + " activated"'
        assert fb0["is_expr"] is True
        fb1 = t["feedback"][1]
        assert fb1["trigger"] == "error"
        assert fb1["style"] == "banner"
        assert fb1["is_expr"] is False
