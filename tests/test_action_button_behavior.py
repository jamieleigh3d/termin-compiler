# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Regression tests: action-button hide-vs-disable behavior.

Context
-------
The DSL offers two terminators for a row action's unavailable state:

    "Close" transitions to "closed" if available, hide otherwise
    "Close" transitions to "closed" if available, disable otherwise

These must lower to different IR values (`unavailable_behavior: "hide"`
vs `"disable"`) and the renderer must render them differently.

A latent bug in parse_handlers was silently collapsing every "hide
otherwise" back to "disable" because the TatSu #Name alternative-tag
annotation was not being surfaced through parseinfo under this codebase's
TatSu 5.15.1 invocation pattern. The existing test suite happened not to
cover this distinction, so the bug was invisible until the Delete action
button work exposed it.

These tests assert the distinction at the IR level. They fail on the
bug, pass on the fix.
"""

from termin.peg_parser import parse_peg as parse
from termin.lower import lower


def _compile_story_with_behavior(behavior_clause: str):
    """Compile a minimal program with a single action button whose
    unavailable behavior clause is `behavior_clause` (e.g. ", hide
    otherwise", ", disable otherwise", or "")."""
    src = f'''Application: Test
Identity:
  Scopes are "read" and "write"
  A "user" has "read" and "write"

Content called "tickets":
  Each ticket has a title which is text, required
  Each ticket has a lifecycle which is state:
    lifecycle starts as open
    lifecycle can also be closed
    open can become closed if the user has write
  Anyone with "read" can view tickets
  Anyone with "write" can create or update tickets

As a user, I want to manage tickets:
  Show a page called "Tickets"
  Display a table of tickets with columns: title
  For each ticket, show actions:
    "Close" transitions lifecycle to closed if available{behavior_clause}
'''
    program, errors = parse(src)
    assert errors.ok, errors.format()
    app_spec = lower(program)
    page = app_spec.pages[0]
    data_tables = [c for c in page.children if c.type == "data_table"]
    assert len(data_tables) == 1
    row_actions = data_tables[0].props.get("row_actions", [])
    assert len(row_actions) == 1
    return row_actions[0]


class TestActionButtonBehaviorLowering:
    """Every "if available, hide otherwise" must lower to hide, every
    "disable otherwise" (or bare "if available") to disable. No matter
    what TatSu does with the #Name annotations, the author's intent must
    survive into the IR."""

    def test_hide_otherwise_lowers_to_hide(self):
        btn = _compile_story_with_behavior(", hide otherwise")
        assert btn.props.get("unavailable_behavior") == "hide", btn.props

    def test_disable_otherwise_lowers_to_disable(self):
        btn = _compile_story_with_behavior(", disable otherwise")
        assert btn.props.get("unavailable_behavior") == "disable", btn.props

    def test_bare_if_available_defaults_to_disable(self):
        """No otherwise-clause at all defaults to disable — the unspecified
        terminator should not silently flip to hide."""
        btn = _compile_story_with_behavior("")
        assert btn.props.get("unavailable_behavior") == "disable", btn.props


class TestRenderedButtonDiffersByBehavior:
    """The IR semantic flag must reach the renderer. A hide-otherwise
    button renders inside a `{% if … %}{% endif %}` guard with no
    `{% else %}` branch; a disable-otherwise button renders with a
    disabled fallback element in the else branch."""

    def test_hide_otherwise_renders_without_disabled_fallback(self):
        import json
        from termin_runtime.presentation import render_component

        btn = _compile_story_with_behavior(", hide otherwise")
        # Build a minimal data_table component containing this button.
        data_table = {
            "type": "data_table",
            "props": {
                "source": "tickets",
                "columns": [{"field": "title", "label": "title"}],
                "row_actions": [{
                    "type": "action_button",
                    "props": btn.props,
                }],
            },
            "children": [],
        }
        html = render_component(data_table)
        # Hide-otherwise: no "disabled" fallback button for this action.
        # Must have {% if %} and {% endif %} but NOT {% else %} paired with
        # a disabled button for the transition.
        assert "{% if" in html
        # Presence of "disabled" inside the rendered block would indicate
        # the else-branch emitted a disabled fallback — wrong for hide.
        # (There may be `disabled` attributes elsewhere on the page from
        # other controls, so scope the check to the action cell.)
        # Simpler structural check: hide-otherwise emits exactly one
        # button for this action, wrapped in an if/endif.
        assert html.count("{% else %}") == 0 or \
               "cursor-not-allowed" not in html, \
            "hide-otherwise must not emit a disabled fallback button"

    def test_disable_otherwise_renders_with_disabled_fallback(self):
        from termin_runtime.presentation import render_component

        btn = _compile_story_with_behavior(", disable otherwise")
        data_table = {
            "type": "data_table",
            "props": {
                "source": "tickets",
                "columns": [{"field": "title", "label": "title"}],
                "row_actions": [{
                    "type": "action_button",
                    "props": btn.props,
                }],
            },
            "children": [],
        }
        html = render_component(data_table)
        assert "{% if" in html and "{% else %}" in html
        # disable-otherwise emits a disabled fallback in the else branch.
        assert "cursor-not-allowed" in html, \
            "disable-otherwise must emit a disabled fallback button"
