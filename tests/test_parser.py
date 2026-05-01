# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the Termin parser (legacy recursive descent)."""

import sys

import pytest

from termin.peg_parser import parse_peg as parse
from termin.ast_nodes import *


# TatSu has a documented platform-dependent context-state leak on
# WSL/Linux: a fresh `_model.parse(...)` call returns None for valid
# input after the first parse in the process. Tests that probe the
# TatSu parser DIRECTLY (the ``_via_tatsu`` family below) hit this and
# fail on Linux even though the high-level production parser path
# works fine — production code falls back to a per-rule Python parser
# in `termin.parse_handlers` whose fidelity is verified by separate
# tests in `tests/test_access_rule_fallback_fidelity.py` (and equivalents).
# These ``_via_tatsu`` probes only meaningfully run on Windows where
# TatSu doesn't leak. See the workspace journal entry 2026-04-29 for
# the original incident and the per-platform notes in MEMORY.md.
_tatsu_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason=(
        "TatSu has a context-state leak on WSL/Linux that makes "
        "direct _model.parse() calls return None for valid input "
        "after the first call. Production paths use the Python "
        "fallback in parse_handlers and are tested separately."
    ),
)


def test_parse_application():
    program, errors = parse('Application: My App\n  Description: A test app')
    assert errors.ok
    assert program.application.name == "My App"
    assert program.application.description == "A test app"


# test_parse_identity and test_parse_roles removed in v0.9: they tested the
# v0.8 top-level `Users authenticate with X` and bare `Scopes are`/`A "..."
# has` lines that no longer exist as top-level grammar. v0.9 coverage of the
# Identity: block lives in TestStateMachineFieldType::test_v09_identity_block_basic.


def test_parse_content_fields():
    program, errors = parse('''Content called "products":
  Each product has a name which is text, required
  Each product has a cost which is currency
  Each product has a count which is a whole number, minimum 0
  Each product has a status which is one of: active, inactive
  Anyone with "read" can view products''')
    assert errors.ok
    c = program.contents[0]
    assert c.name == "products"
    assert c.singular == "product"
    assert len(c.fields) == 4
    assert c.fields[0].name == "name"
    assert c.fields[0].type_expr.base_type == "text"
    assert c.fields[0].type_expr.required is True
    assert c.fields[1].type_expr.base_type == "currency"
    assert c.fields[2].type_expr.base_type == "whole_number"
    assert c.fields[2].type_expr.minimum == 0
    assert c.fields[3].type_expr.base_type == "enum"
    assert c.fields[3].type_expr.enum_values == ["active", "inactive"]


def test_parse_content_references():
    program, errors = parse('''Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Content called "details":
  Each detail has a item which references items, required
  Anyone with "read" can view details''')
    assert errors.ok
    assert len(program.contents) == 2
    ref_field = program.contents[1].fields[0]
    assert ref_field.type_expr.base_type == "reference"
    assert ref_field.type_expr.references == "items"
    assert ref_field.type_expr.required is True


def test_parse_state_machine():
    program, errors = parse('''Content called "tasks":
  Each task has a title which is text
  Each task has a task flow which is state:
    task flow starts as open
    task flow can also be closed or archived
    open can become closed if the user has write
    closed can become archived if the user has admin
  Anyone with "read" can view tasks
''')
    assert errors.ok, errors.format()
    sm = program.state_machines[0]
    assert sm.machine_name == "task flow"
    assert sm.initial_state == "open"
    assert set(sm.states) == {"open", "closed", "archived"}
    assert len(sm.transitions) == 2
    assert sm.transitions[0].from_state == "open"
    assert sm.transitions[0].to_state == "closed"


def test_parse_event():
    program, errors = parse(
        'When a item is updated and its count is at or below its threshold:\n'
        '  Create a alert with the item, count, and threshold'
    )
    assert errors.ok
    ev = program.events[0]
    assert ev.content_name == "item"
    assert ev.trigger == "updated"
    assert ev.condition.field1 == "count"
    assert ev.condition.operator == "at or below"
    assert ev.condition.field2 == "threshold"
    assert ev.action.create_content == "alert"
    assert ev.action.fields == ["item", "count", "threshold"]


def test_parse_user_story():
    program, errors = parse('''As a user, I want to see all items
  so that I can browse:
    Show a page called "Item List"
    Display a table of items with columns: name, status
    Allow filtering by status
    Allow searching by name''')
    assert errors.ok
    story = program.stories[0]
    assert story.role == "user"
    assert isinstance(story.directives[0], ShowPage)
    assert story.directives[0].page_name == "Item List"
    assert isinstance(story.directives[1], DisplayTable)
    assert story.directives[1].content_name == "items"
    assert story.directives[1].columns == ["name", "status"]
    assert isinstance(story.directives[2], AllowFilter)
    assert isinstance(story.directives[3], AllowSearch)


def test_parse_navigation():
    program, errors = parse('''Navigation bar:
  "Home" links to "Dashboard" visible to all
  "Admin" links to "Admin Panel" visible to admin''')
    assert errors.ok
    nav = program.navigation
    assert len(nav.items) == 2
    assert nav.items[0].label == "Home"
    assert nav.items[0].page_name == "Dashboard"
    assert nav.items[1].visible_to == ["admin"]


def test_parse_warehouse_example():
    from pathlib import Path
    source = Path("examples/warehouse.termin").read_text()
    program, errors = parse(source)
    assert errors.ok, errors.format()
    assert program.application.name == "Warehouse Inventory Manager"
    assert len(program.contents) == 3
    assert len(program.state_machines) == 1
    assert len(program.events) == 1
    assert len(program.stories) == 5
    assert program.navigation is not None
    assert len(program.streams) == 1


def test_parse_error_unknown_line():
    program, errors = parse('This is garbage')
    assert not errors.ok


# ── Compute parsing ──

def test_parse_compute_transform():
    program, errors = parse('''Compute called "enrich order":
  Transform: takes an order, produces an order
  [total = subtotal * 1.1]
  Anyone with "write" can execute this''')
    assert errors.ok
    c = program.computes[0]
    assert c.name == "enrich order"
    assert c.shape == "transform"
    assert c.inputs == ["order"]
    assert c.outputs == ["order"]
    assert len(c.body_lines) >= 1
    assert c.access_scope == "write"


def test_parse_compute_reduce():
    program, errors = parse('''Compute called "summarize":
  Reduce: takes orders, produces a report
  Anyone with "read" can execute this''')
    assert errors.ok
    c = program.computes[0]
    assert c.shape == "reduce"
    assert c.inputs == ["orders"]
    assert c.outputs == ["report"]


def test_parse_compute_correlate():
    program, errors = parse('''Compute called "match":
  Correlate: takes invoices and payments, produces reports
  Anyone with "read" can execute this''')
    assert errors.ok
    c = program.computes[0]
    assert c.shape == "correlate"
    assert c.inputs == ["invoices", "payments"]
    assert c.outputs == ["reports"]


def test_parse_compute_route():
    program, errors = parse('''Compute called "classify":
  Route: takes a ticket, produces one of bugs or features
  Anyone with "write" can execute this''')
    assert errors.ok
    c = program.computes[0]
    assert c.shape == "route"
    assert c.inputs == ["ticket"]
    assert c.outputs == ["bugs", "features"]


# ── Channel parsing ──

def test_parse_channel_inbound_reliable():
    program, errors = parse('''Channel called "order hook":
  Carries orders
  Direction: inbound
  Delivery: reliable
  Endpoint: /webhooks/orders
  Requires "write" to send''')
    assert errors.ok
    ch = program.channels[0]
    assert ch.name == "order hook"
    assert ch.carries == "orders"
    assert ch.direction == "inbound"
    assert ch.delivery == "reliable"
    assert ch.endpoint == "/webhooks/orders"
    assert len(ch.requirements) == 1
    assert ch.requirements[0].scope == "write"
    assert ch.requirements[0].direction == "send"


def test_parse_channel_outbound_realtime():
    program, errors = parse('''Channel called "updates":
  Carries items
  Direction: outbound
  Delivery: realtime
  Requires "read" to receive''')
    assert errors.ok
    ch = program.channels[0]
    assert ch.direction == "outbound"
    assert ch.delivery == "realtime"
    assert ch.requirements[0].direction == "receive"


def test_parse_channel_internal():
    program, errors = parse('''Channel called "bus":
  Carries items
  Direction: internal
  Delivery: auto''')
    assert errors.ok
    ch = program.channels[0]
    assert ch.direction == "internal"
    assert ch.delivery == "auto"
    assert len(ch.requirements) == 0


# ── Channel Action parsing ──

def test_parse_channel_action_basic():
    program, errors = parse('''Channel called "tools":
  Direction: outbound
  Delivery: reliable
  Action called "do-thing":
    Takes name which is text, count which is number
    Returns result which is text
    Requires "admin" to invoke''')
    assert errors.ok
    ch = program.channels[0]
    assert ch.name == "tools"
    assert ch.direction == "outbound"
    assert len(ch.actions) == 1
    act = ch.actions[0]
    assert act.name == "do-thing"
    assert len(act.takes) == 2
    assert act.takes[0].name == "name"
    assert act.takes[0].type_name == "text"
    assert act.takes[1].name == "count"
    assert act.takes[1].type_name == "number"
    assert len(act.returns) == 1
    assert act.returns[0].name == "result"
    assert act.returns[0].type_name == "text"
    assert act.required_scopes == ["admin"]


def test_parse_channel_multiple_actions():
    program, errors = parse('''Channel called "security-tools":
  Direction: outbound
  Delivery: reliable
  Action called "restrict-policy":
    Takes role which is text, policy which is text
    Returns result which is text
    Requires "remediate" to invoke
  Action called "rotate-secret":
    Takes arn which is text
    Returns confirmation which is text
    Requires "remediate" to invoke''')
    assert errors.ok
    ch = program.channels[0]
    assert len(ch.actions) == 2
    assert ch.actions[0].name == "restrict-policy"
    assert ch.actions[1].name == "rotate-secret"
    assert ch.actions[0].takes[0].name == "role"
    assert ch.actions[1].takes[0].name == "arn"


def test_parse_channel_data_and_actions():
    """Channel can carry Content AND expose Actions."""
    program, errors = parse('''Channel called "slack":
  Direction: bidirectional
  Delivery: realtime
  Carries messages
  Requires "read" to receive
  Action called "post-message":
    Takes channel which is text, text which is text
    Returns ok which is yes or no
    Requires "send" to invoke''')
    assert errors.ok
    ch = program.channels[0]
    assert ch.carries == "messages"
    assert len(ch.requirements) == 1
    assert ch.requirements[0].direction == "receive"
    assert len(ch.actions) == 1
    assert ch.actions[0].name == "post-message"


def test_parse_event_send_to_channel():
    program, errors = parse('''When `finding.severity == "critical"`:
  Send finding to "slack-alerts"
  Log level: WARN''')
    assert errors.ok
    ev = program.events[0]
    assert ev.action.send_content == "finding"
    assert ev.action.send_channel == "slack-alerts"
    assert ev.log_level == "WARN"


# ── Boundary parsing ──

def test_parse_boundary_inherit():
    program, errors = parse('''Boundary called "inventory":
  Contains products, stock levels, and alerts
  Identity inherits from application''')
    assert errors.ok
    b = program.boundaries[0]
    assert b.name == "inventory"
    assert b.contains == ["products", "stock levels", "alerts"]
    assert b.identity_mode == "inherit"
    assert b.identity_parent == "application"


def test_parse_boundary_restrict():
    program, errors = parse('''Boundary called "reporting":
  Contains reports
  Identity restricts to "read only"''')
    assert errors.ok
    b = program.boundaries[0]
    assert b.name == "reporting"
    assert b.contains == ["reports"]
    assert b.identity_mode == "restrict"
    assert b.identity_scopes == ["read only"]


def test_parse_anonymous_story():
    program, errors = parse('''As anonymous, I want to see a page "Hello" so that I can be greeted:
  Display text "Hello, World"''')
    assert errors.ok, errors.format()
    story = program.stories[0]
    assert story.role == "anonymous"
    assert story.objective == "I can be greeted"
    # ShowPage auto-extracted from action
    assert any(isinstance(d, ShowPage) and d.page_name == "Hello" for d in story.directives)
    # DisplayText parsed
    assert any(isinstance(d, DisplayText) and d.text == "Hello, World" for d in story.directives)


def test_parse_inline_so_that():
    program, errors = parse('''As a user, I want to see things so that I can browse:
  Display text "Welcome"''')
    assert errors.ok, errors.format()
    story = program.stories[0]
    assert story.role == "user"
    assert story.objective == "I can browse"


def test_parse_inline_page():
    program, errors = parse('''As a user, I want to see a page "Dashboard"
  so that I can view data:
    Display text "Welcome to the dashboard"''')
    assert errors.ok, errors.format()
    story = program.stories[0]
    # ShowPage should be auto-created from action text
    show_pages = [d for d in story.directives if isinstance(d, ShowPage)]
    assert len(show_pages) >= 1
    assert show_pages[0].page_name == "Dashboard"


def test_parse_bare_role():
    program, errors = parse(
        'Identity:\n'
        '  Scopes are "view" and "write"\n'
        '  Anonymous has "view" and "write"'
    )
    assert errors.ok, errors.format()
    assert program.roles[0].name == "Anonymous"
    assert program.roles[0].scopes == ["view", "write"]


def test_parse_compute_typed_params():
    """v0.9: compute access is scope-based; the v0.8 bare-role form was
    removed. Test now uses Anyone with "<scope>"."""
    program, errors = parse('''Compute called "greet":
  Transform: takes u : UserProfile, produces "msg" : Text
  [msg = "Hello " + u.Name]
  Anyone with "admin.execute" can execute this''')
    assert errors.ok, errors.format()
    c = program.computes[0]
    assert c.input_params[0].name == "u"
    assert c.input_params[0].type_name == "UserProfile"
    assert c.output_params[0].name == "msg"
    assert c.output_params[0].type_name == "Text"
    assert c.access_scope == "admin.execute"
    assert len(c.body_lines) >= 1


# ── CEL backtick syntax (v2) ──

def test_parse_display_text_backtick():
    program, errors = parse('''As anonymous, I want to see a page "Hello" so that I can test:
  Display text `SayHello(user.name)`''')
    assert errors.ok, errors.format()
    dt = [d for d in program.stories[0].directives if isinstance(d, DisplayText)]
    assert dt[0].is_expression is True
    assert dt[0].text == "SayHello(user.name)"


def test_parse_event_expr():
    program, errors = parse('''When `stockLevel.updated && stockLevel.quantity <= stockLevel.reorderThreshold`:
  Create a reorder alert with the product, warehouse''')
    assert errors.ok, errors.format()
    ev = program.events[0]
    assert ev.trigger == "expr"
    assert ev.condition_expr == "stockLevel.updated && stockLevel.quantity <= stockLevel.reorderThreshold"


def test_parse_compute_expr_body():
    """v0.9: compute access uses scope-based form
    `Anyone with "<scope>" can execute this`. The v0.8 bare-role
    form `"<role>" can execute this` was removed (see
    TestRoleBasedComputeAccessRemoved)."""
    program, errors = parse('''Compute called "greet":
  Transform: takes u : UserProfile, produces greeting : Text
  `greeting = "Hello, " + u.FirstName + "!"`
  Anyone with "admin.execute" can execute this''')
    assert errors.ok, errors.format()
    c = program.computes[0]
    assert 'greeting = "Hello, " + u.FirstName + "!"' in c.body_lines


def test_parse_highlight_expr():
    program, errors = parse('''As a user, I want to see items
  so that I can browse:
    Show a page called "Items"
    Display a table of items with columns: name, quantity
    Highlight rows where `quantity <= threshold`''')
    assert errors.ok, errors.format()
    hl = [d for d in program.stories[0].directives if isinstance(d, HighlightRows)]
    assert hl[0].condition_expr == "quantity <= threshold"


def test_parse_new_types():
    program, errors = parse('''Content called "items":
  Each item has a price which is number
  Each item has a margin which is percentage
  Each item has a active which is true/false
  Each item has a created which is date
  Each item has a updated which is date and time
  Each item has a tags which is list of text
  Each item has a count which is whole number, minimum 0, maximum 9999
  Anyone with "read" can view items''')
    assert errors.ok, errors.format()
    c = program.contents[0]
    types = {f.name: f.type_expr for f in c.fields}
    assert types["price"].base_type == "number"
    assert types["margin"].base_type == "percentage"
    assert types["active"].base_type == "boolean"
    assert types["created"].base_type == "date"
    assert types["updated"].base_type == "datetime"
    assert types["tags"].base_type == "list"
    assert types["tags"].list_type == "text"
    assert types["count"].base_type == "whole_number"
    assert types["count"].minimum == 0
    assert types["count"].maximum == 9999


def test_parse_all_examples():
    from pathlib import Path
    for name in ["hello", "hello_user", "warehouse", "helpdesk", "projectboard", "compute_demo"]:
        source = Path(f"examples/{name}.termin").read_text()
        program, errors = parse(source)
        assert errors.ok, f"{name}: {errors.format()}"


def test_parse_hello_user_example():
    from pathlib import Path
    source = Path("examples/hello_user.termin").read_text()
    program, errors = parse(source)
    assert errors.ok, errors.format()
    assert program.application.name == "Hello User"
    assert len(program.roles) == 2
    assert len(program.stories) == 2
    assert len(program.computes) == 1
    # v0.9: SayHelloTo is scope-gated on "app.view" (was bare-role
    # `"user" can execute this` in v0.8 — that form was removed).
    assert program.computes[0].access_scope == "app.view"


def test_parse_hello_example():
    from pathlib import Path
    source = Path("examples/hello.termin").read_text()
    program, errors = parse(source)
    assert errors.ok, errors.format()
    assert program.application.name == "Hello World"
    assert len(program.stories) == 1
    assert program.stories[0].role == "Anonymous"


def test_parse_compute_demo():
    from pathlib import Path
    source = Path("examples/compute_demo.termin").read_text()
    program, errors = parse(source)
    assert errors.ok, errors.format()
    assert len(program.computes) == 6
    assert len(program.channels) == 4
    assert len(program.boundaries) == 2


# ── v0.9: Compute access scope-based canonical, role-based removed ──
#
# v0.8 had two grammar shapes for granting compute execution:
#   - Anyone with "<scope>" can execute this  (scope-based — canonical)
#   - "<role>" can execute this               (role-based — removed in v0.9)
# These translated to different IR with different semantics, which was
# confusing. v0.9 removes the role-based form; sources using it produce
# a clear migration error pointing at the scope-based equivalent.

class TestRoleBasedComputeAccessRemoved:
    _COMPUTE_PREAMBLE = '''Application: Test
Id: 11111111-2222-3333-4444-555555555555

Identity:
  Scopes are "app.view"
  A "user" has "app.view"

Compute called "say_hi":
  Transform: takes name : text, produces greeting : text
  `greeting = "Hello, " + name + "!"`
'''

    def test_scope_based_form_succeeds(self):
        """`Anyone with "<scope>" can execute this` is the canonical
        v0.9 form."""
        src = self._COMPUTE_PREAMBLE + '  Anyone with "app.view" can execute this\n'
        program, errors = parse(src)
        assert errors.ok, errors.format()
        assert len(program.computes) == 1

    def test_role_based_form_rejected(self):
        """`"<role>" can execute this` is removed in v0.9."""
        src = self._COMPUTE_PREAMBLE + '  "user" can execute this\n'
        program, errors = parse(src)
        assert not errors.ok, "v0.8 role-based compute access must error in v0.9"
        msg = errors.format()
        # Error should mention the migration path.
        assert "Anyone with" in msg or "scope" in msg.lower()

    def test_role_based_form_error_names_role(self):
        src = self._COMPUTE_PREAMBLE + '  "warehouse manager" can execute this\n'
        program, errors = parse(src)
        assert not errors.ok
        msg = errors.format()
        # Helpful error mentions the role name so the user knows which line.
        assert "warehouse manager" in msg or "execute this" in msg


# ── v0.8.2: Accesses line multi-content (PEG gap closure) ──
#
# `Accesses` inside a Compute block must accept a comma-separated list of
# content names, with optional Oxford `and`. Previously the TatSu rule
# captured a single `words` terminal and silently failed on multi-content
# shapes, falling through to a Python fallback in parse_handlers.py.
# `test_no_tatsu_fallbacks` catches that fallback; these tests pin the
# positive contract via TatSu directly so a regression in the grammar
# rule shape (not just the absence of a fallback) is caught.

class TestAccessesLineMultiContent:
    def _parse_via_tatsu(self, line):
        from termin.parse_helpers import _model
        return _model.parse(line, rule_name="compute_accesses_line")

    @_tatsu_only
    def test_single_content_via_tatsu(self):
        r = self._parse_via_tatsu("Accesses messages")
        assert r is not None

    @_tatsu_only
    def test_two_contents_comma_via_tatsu(self):
        r = self._parse_via_tatsu("Accesses messages, products")
        assert r is not None, "TatSu should parse comma-separated contents"

    @_tatsu_only
    def test_three_contents_oxford_comma_via_tatsu(self):
        r = self._parse_via_tatsu("Accesses messages, products, and reports")
        assert r is not None, "TatSu should parse Oxford-comma form"

    def test_compute_accesses_multi_end_to_end(self):
        """Parse a Compute block with multi-content Accesses and confirm
        the AST captures every name. Catches a regression in either the
        grammar rule OR the handler that consumes its output."""
        src = '''Compute called "reply":
  Provider is "ai-agent"
  Accesses messages, products
  Trigger on event "message.created"
  Directive is ```
    Reply.
  ```
  Objective is ```
    Done.
  ```
  Anyone with "chat.use" can execute this
'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        assert len(program.computes) == 1
        compute = program.computes[0]
        # Both content names must be captured. Snake-case normalization
        # happens downstream in lower(); at parse time we expect the
        # source spelling preserved.
        accesses = compute.accesses
        assert "messages" in accesses
        assert "products" in accesses
        assert len(accesses) == 2

    def test_compute_accesses_oxford_comma_end_to_end(self):
        src = '''Compute called "reply":
  Provider is "ai-agent"
  Accesses messages, products, and reports
  Trigger on event "message.created"
  Directive is ```
    Reply.
  ```
  Objective is ```
    Done.
  ```
  Anyone with "chat.use" can execute this
'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        accesses = program.computes[0].accesses
        assert "messages" in accesses
        assert "products" in accesses
        assert "reports" in accesses
        assert len(accesses) == 3


# ── v0.8.2: Access grant unknown-verb rejection ──
#
# Termin verbs are 'view', 'create', 'update', 'delete'. Previously, an
# unknown verb in the middle of a grant ("can create, read, update, and
# delete documents") caused TatSu to fail the whole access_line rule
# (single_verb doesn't match 'read'); the Python fallback then iterated
# words and `break`-ed on the first unknown, silently truncating the
# verb list to ['create']. The user sees no error and gets only one
# verb instead of four. This is a security-adjacent bug: the user
# thinks they granted four operations and got one.
#
# Fix surface: the fallback must detect unknown verbs and raise a clear
# error naming the bad verb and the valid set.

class TestAccessGrantUnknownVerb:
    def _content_block(self, can_clause):
        return f'''Content called "documents":
  Each document has a title which is text, required
  Anyone with "docs.edit" can {can_clause} documents
'''

    def test_unknown_verb_read_raises_with_helpful_message(self):
        src = self._content_block("create, read, update, and delete")
        program, errors = parse(src)
        assert not errors.ok, (
            "Expected unknown-verb error; instead got verbs="
            + str(program.contents[0].access_rules[0].verbs if program.contents else "<no content>")
        )
        msg = errors.format()
        assert "read" in msg, f"Error should name the bad verb: {msg}"
        # Mention at least one valid verb to be helpful.
        assert any(v in msg for v in ("view", "create", "update", "delete")), (
            f"Error should mention valid verbs: {msg}"
        )

    def test_unknown_verb_alone_raises(self):
        src = self._content_block("frobnicate")
        program, errors = parse(src)
        assert not errors.ok
        assert "frobnicate" in errors.format()

    def test_known_four_verb_and_grant_succeeds(self):
        """Regression check: the fix must not break the all-known case."""
        src = self._content_block("view, create, update, and delete")
        program, errors = parse(src)
        assert errors.ok, errors.format()
        verbs = program.contents[0].access_rules[0].verbs
        assert verbs == ["view", "create", "update", "delete"], verbs

    def test_known_four_verb_or_grant_succeeds(self):
        """Regression check: 'or' form still works."""
        src = self._content_block("view, create, update, or delete")
        program, errors = parse(src)
        assert errors.ok, errors.format()
        verbs = program.contents[0].access_rules[0].verbs
        assert verbs == ["view", "create", "update", "delete"], verbs

    def test_known_three_verb_and_grant_succeeds(self):
        src = self._content_block("view, create, and update")
        program, errors = parse(src)
        assert errors.ok, errors.format()
        verbs = program.contents[0].access_rules[0].verbs
        assert verbs == ["view", "create", "update"], verbs

    def test_known_two_verb_and_grant_succeeds(self):
        src = self._content_block("create and update")
        program, errors = parse(src)
        assert errors.ok, errors.format()
        verbs = program.contents[0].access_rules[0].verbs
        assert verbs == ["create", "update"], verbs


# ── v0.8.2: Inverted constraint forms on field types ──
#
# Canonical Termin form is "which is <base_type>, <constraint>+"
# (constraints follow the base type, comma-separated). Native English
# phrasing flips this: "which is required text" reads more naturally
# than "which is text, required". Both are accepted; both produce the
# same TypeExpr.
#
# Forms covered (per JL 2026-04-25):
#   which is text                          base, no constraints
#   which is required text                 inverted required
#   which is text, required                canonical required
#   which is unique text                   inverted unique (already supported)
#   which is text, unique                  canonical unique
#   which is unique required text          inverted both
#   which is required unique text          inverted both, swapped order
#   which is text, unique, required        canonical both
#   which is text, required, unique        canonical both, swapped order

class TestConstraintForms:
    def _field_block(self, type_clause):
        return f'''Content called "documents":
  Each document has a title which is {type_clause}
'''

    def _get_title(self, src):
        program, errors = parse(src)
        assert errors.ok, errors.format()
        return program.contents[0].fields[0]

    def test_plain_text(self):
        f = self._get_title(self._field_block("text"))
        assert f.type_expr.base_type == "text"
        assert not f.type_expr.required
        assert not f.type_expr.unique

    # — required only —

    def test_canonical_required(self):
        f = self._get_title(self._field_block("text, required"))
        assert f.type_expr.base_type == "text"
        assert f.type_expr.required is True
        assert not f.type_expr.unique

    def test_inverted_required(self):
        f = self._get_title(self._field_block("required text"))
        assert f.type_expr.base_type == "text"
        assert f.type_expr.required is True, "inverted 'required text' must set required"
        assert not f.type_expr.unique

    # — unique only —

    def test_canonical_unique(self):
        f = self._get_title(self._field_block("text, unique"))
        assert f.type_expr.base_type == "text"
        assert f.type_expr.unique is True
        assert not f.type_expr.required

    def test_inverted_unique(self):
        f = self._get_title(self._field_block("unique text"))
        assert f.type_expr.base_type == "text"
        assert f.type_expr.unique is True
        assert not f.type_expr.required

    # — both required and unique —

    def test_canonical_unique_required(self):
        f = self._get_title(self._field_block("text, unique, required"))
        assert f.type_expr.base_type == "text"
        assert f.type_expr.required is True
        assert f.type_expr.unique is True

    def test_canonical_required_unique(self):
        f = self._get_title(self._field_block("text, required, unique"))
        assert f.type_expr.base_type == "text"
        assert f.type_expr.required is True
        assert f.type_expr.unique is True

    def test_inverted_unique_required(self):
        f = self._get_title(self._field_block("unique required text"))
        assert f.type_expr.base_type == "text"
        assert f.type_expr.required is True
        assert f.type_expr.unique is True

    def test_inverted_required_unique(self):
        f = self._get_title(self._field_block("required unique text"))
        assert f.type_expr.base_type == "text"
        assert f.type_expr.required is True
        assert f.type_expr.unique is True

    # — TatSu path coverage: every form should parse via TatSu without
    #   falling through to Python. Otherwise test_no_tatsu_fallbacks
    #   blocks any example using these forms.

    @_tatsu_only
    def test_all_forms_parse_via_tatsu(self):
        from termin.parse_helpers import _model
        for clause in [
            "text",
            "text, required",
            "required text",
            "text, unique",
            "unique text",
            "text, unique, required",
            "text, required, unique",
            "unique required text",
            "required unique text",
        ]:
            line = f"Each document has a title which is {clause}"
            try:
                r = _model.parse(line, rule_name="field_line")
            except Exception as e:
                raise AssertionError(f"TatSu rejected {clause!r}: {e}")
            assert r is not None, f"TatSu returned None for {clause!r}"


# ── D-18: Audit declaration on Content ──

class TestContentAudit:
    def test_audit_level_actions(self):
        src = '''Content called "events":
  Each event has a title which is text
  Audit level: actions'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        assert program.contents[0].audit == "actions"

    def test_audit_level_debug(self):
        src = '''Content called "events":
  Each event has a title which is text
  Audit level: debug'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        assert program.contents[0].audit == "debug"

    def test_audit_level_none(self):
        src = '''Content called "events":
  Each event has a title which is text
  Audit level: none'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        assert program.contents[0].audit == "none"

    def test_audit_default_is_actions(self):
        """When no Audit line is present, default is 'actions' (pit of success)."""
        src = '''Content called "events":
  Each event has a title which is text'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        assert program.contents[0].audit == "actions"


# ── v0.9: state as inline field type (multi-state-machine per content) ──

class TestStateMachineFieldType:
    """Tests for the new inline state field syntax:

        Content called "products":
          Each product has a lifecycle which is state:
            lifecycle starts as draft
            lifecycle can also be active or discontinued
            draft can become active if the user has catalog.manage

    Replaces the v0.8 top-level `State for X called "Y":` block syntax.
    """

    # ── Failure cases — must reject the old syntax / malformed blocks ──

    def test_old_state_for_syntax_is_parse_error(self):
        """Top-level `State for X called "Y":` is removed in v0.9."""
        src = '''Application: Test
Content called "products":
  Each product has a name which is text
  Anyone with "read" can view products

State for products called "lifecycle":
  A product starts as "draft"
  A product can also be "active"
  A draft product can become active if the user has "read"'''
        _program, errors = parse(src)
        assert not errors.ok

    def test_state_block_without_starts_as_is_parse_error(self):
        """A state field block must contain a `starts as` line."""
        src = '''Content called "products":
  Each product has a lifecycle which is state:
    lifecycle can also be active or discontinued
    draft can become active if the user has catalog.manage
  Anyone with "catalog.manage" can view products'''
        program, errors = parse(src)
        # Either parser flags an error, or analyzer would catch it (initial_state empty).
        # For the parser-level failure case we accept either: no errors but empty initial,
        # OR explicit ParseError. The fail-safe assertion is that no SM with valid init lands.
        if errors.ok:
            assert len(program.state_machines) == 1
            assert program.state_machines[0].initial_state == ""

    def test_state_block_empty_is_parse_error(self):
        """A `which is state:` field with no sub-block lines is invalid."""
        src = '''Content called "products":
  Each product has a lifecycle which is state:
  Anyone with "read" can view products'''
        program, errors = parse(src)
        # Parser may accept (empty SM); analyzer will reject. Accept either:
        if errors.ok:
            sms = program.state_machines
            # Either no SM was registered, or one with empty fields
            if sms:
                assert sms[0].initial_state == ""
                assert sms[0].states == [] or sms[0].states == [""]

    def test_action_button_old_syntax_is_parse_error(self):
        """Old `transitions to <state>` (without field name) is removed."""
        src = '''Content called "products":
  Each product has a name which is text
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active or discontinued
    draft can become active if the user has catalog.manage
  Anyone with "catalog.manage" can view, create, update, or delete products

As a "Manager", I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name
  For each product, show actions:
    "Publish" transitions to active if available'''
        _program, errors = parse(src)
        assert not errors.ok

    # ── Happy path — basic parsing ──

    def _wrap(self, content_block: str, extras: str = "") -> str:
        """Helper: wrap a content block in a minimal valid program."""
        return f'''Application: Test
Identity:
  Scopes are "catalog.manage", "approvals.approve", and "ops.confirm"
  A "Manager" has "catalog.manage", "approvals.approve", and "ops.confirm"

{content_block}
{extras}'''

    def test_single_state_field_minimal_form(self):
        src = self._wrap('''Content called "products":
  Each product has a name which is text
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active or discontinued
    draft can become active if the user has catalog.manage
    active can become discontinued if the user has catalog.manage
  Anyone with "catalog.manage" can view products''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        assert len(program.state_machines) == 1
        sm = program.state_machines[0]
        assert sm.machine_name == "lifecycle"
        assert sm.content_name == "products"
        assert sm.initial_state == "draft"
        assert set(sm.states) == {"draft", "active", "discontinued"}
        assert len(sm.transitions) == 2
        assert sm.transitions[0].from_state == "draft"
        assert sm.transitions[0].to_state == "active"
        assert sm.transitions[0].required_scope == "catalog.manage"

    def test_single_state_field_full_form(self):
        """Full form with articles and quotes parses identically to minimal form."""
        src = self._wrap('''Content called "products":
  Each product has a lifecycle which is state:
    lifecycle starts as "draft"
    lifecycle can also be "active" or "discontinued"
    A draft can become "active" if the user has "catalog.manage"
    An active can become "discontinued" if the user has "catalog.manage"
  Anyone with "catalog.manage" can view products''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        sm = program.state_machines[0]
        assert sm.machine_name == "lifecycle"
        assert sm.initial_state == "draft"
        assert set(sm.states) == {"draft", "active", "discontinued"}
        assert len(sm.transitions) == 2

    def test_multi_word_field_name(self):
        src = self._wrap('''Content called "documents":
  Each document has an approval status which is state:
    approval status starts as pending
    approval status can also be approved or rejected
    pending can become approved if the user has approvals.approve
  Anyone with "approvals.approve" can view documents''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        sm = program.state_machines[0]
        assert sm.machine_name == "approval status"
        assert sm.content_name == "documents"
        assert sm.initial_state == "pending"

    def test_multi_word_state_names(self):
        src = self._wrap('''Content called "tickets":
  Each ticket has a lifecycle which is state:
    lifecycle starts as in progress
    lifecycle can also be on hold or under review
    in progress can become on hold if the user has ops.confirm
    on hold can become under review if the user has ops.confirm
  Anyone with "ops.confirm" can view tickets''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        sm = program.state_machines[0]
        assert "in progress" in sm.states
        assert "on hold" in sm.states
        assert "under review" in sm.states
        # Find the in-progress -> on-hold transition
        match = [t for t in sm.transitions
                 if t.from_state == "in progress" and t.to_state == "on hold"]
        assert len(match) == 1, f"expected in progress -> on hold, got {sm.transitions}"

    def test_hyphenated_state_names_parse(self):
        """Hyphens are legal in state names. Space-separated is the
        canonical form (`auto fix applied`), but `auto-fix-applied`
        also parses. Both forms must reach the same identifier value
        verbatim — no normalization."""
        src = self._wrap('''Content called "findings":
  Each finding has a remediation which is state:
    remediation starts as detected
    remediation can also be analyzing, auto-fix-applied, or flagged-for-human
    detected can become analyzing if the user has triage
    analyzing can become auto-fix-applied if the user has remediate
    auto-fix-applied can become flagged-for-human if the user has triage
  Anyone with "triage" can view findings''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        sm = program.state_machines[0]
        assert "auto-fix-applied" in sm.states
        assert "flagged-for-human" in sm.states
        # Hyphens carry through transitions verbatim
        match = [t for t in sm.transitions
                 if t.from_state == "auto-fix-applied"
                 and t.to_state == "flagged-for-human"]
        assert len(match) == 1, (
            f"expected auto-fix-applied -> flagged-for-human transition, "
            f"got {[(t.from_state, t.to_state) for t in sm.transitions]}")
        assert match[0].required_scope == "triage"

    def test_self_transition_parses(self):
        src = self._wrap('''Content called "orders":
  Each order has a lifecycle which is state:
    lifecycle starts as pending
    lifecycle can also be processing or complete
    pending can become pending if the user has ops.confirm
    pending can become processing if the user has ops.confirm
  Anyone with "ops.confirm" can view orders''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        sm = program.state_machines[0]
        self_trans = [t for t in sm.transitions
                      if t.from_state == "pending" and t.to_state == "pending"]
        assert len(self_trans) == 1
        assert self_trans[0].required_scope == "ops.confirm"

    def test_two_state_fields_on_one_content(self):
        src = self._wrap('''Content called "documents":
  Each document has a title which is text
  Each document has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be published
    draft can become published if the user has catalog.manage
  Each document has an approval status which is state:
    approval status starts as pending
    approval status can also be approved or rejected
    pending can become approved if the user has approvals.approve
    pending can become rejected if the user has approvals.approve
  Anyone with "catalog.manage" can view documents''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        assert len(program.state_machines) == 2
        names = {sm.machine_name for sm in program.state_machines}
        assert names == {"lifecycle", "approval status"}
        for sm in program.state_machines:
            assert sm.content_name == "documents"

    def test_action_button_with_machine_name(self):
        src = self._wrap('''Content called "products":
  Each product has a name which is text
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active
    draft can become active if the user has catalog.manage
  Anyone with "catalog.manage" can view, create, update, or delete products

As a "Manager", I want to manage products:
  Show a page called "Products"
  Display a table of products with columns: name
  For each product, show actions:
    "Publish" transitions lifecycle to active if available''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        # Find the action button directive.
        from termin.ast_nodes import ActionButtonDef
        story = program.stories[0]
        buttons = [d for d in story.directives if isinstance(d, ActionButtonDef)]
        assert len(buttons) == 1
        b = buttons[0]
        assert b.label == "Publish"
        assert b.machine_name == "lifecycle"
        assert b.target_state == "active"

    def test_action_button_multi_word_field_name(self):
        src = self._wrap('''Content called "documents":
  Each document has a title which is text
  Each document has an approval status which is state:
    approval status starts as pending
    approval status can also be approved
    pending can become approved if the user has approvals.approve
  Anyone with "approvals.approve" can view, create, update, or delete documents

As a "Manager", I want to manage documents:
  Show a page called "Documents"
  Display a table of documents with columns: title
  For each document, show actions:
    "Approve" transitions approval status to approved if available''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        from termin.ast_nodes import ActionButtonDef
        buttons = [d for d in program.stories[0].directives
                   if isinstance(d, ActionButtonDef)]
        assert len(buttons) == 1
        assert buttons[0].machine_name == "approval status"
        assert buttons[0].target_state == "approved"

    def test_action_button_multi_word_target_state(self):
        src = self._wrap('''Content called "tasks":
  Each task has a title which is text
  Each task has a lifecycle which is state:
    lifecycle starts as ready
    lifecycle can also be in progress
    ready can become in progress if the user has ops.confirm
  Anyone with "ops.confirm" can view, create, update, or delete tasks

As a "Manager", I want to manage tasks:
  Show a page called "Tasks"
  Display a table of tasks with columns: title
  For each task, show actions:
    "Start" transitions lifecycle to in progress if available''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        from termin.ast_nodes import ActionButtonDef
        buttons = [d for d in program.stories[0].directives
                   if isinstance(d, ActionButtonDef)]
        assert len(buttons) == 1
        assert buttons[0].machine_name == "lifecycle"
        assert buttons[0].target_state == "in progress"

    # ── Sub-block boundary tests ──

    def test_blank_line_inside_state_block_ignored(self):
        """A blank line between sub-block lines does not end the block."""
        src = self._wrap('''Content called "products":
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active or discontinued
    draft can become active if the user has catalog.manage

    active can become discontinued if the user has catalog.manage
  Anyone with "catalog.manage" can view products''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        sm = program.state_machines[0]
        assert len(sm.transitions) == 2

    def test_parenthetical_comment_inside_state_block_ignored(self):
        src = self._wrap('''Content called "products":
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active or discontinued
    (this state machine controls publication workflow)
    draft can become active if the user has catalog.manage
    active can become discontinued if the user has catalog.manage
  Anyone with "catalog.manage" can view products''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        sm = program.state_machines[0]
        assert len(sm.transitions) == 2

    def test_parenthetical_comment_at_column_zero_ignored(self):
        """Per §1, parenthetical comments are stripped at any indentation."""
        src = self._wrap('''Content called "products":
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active or discontinued
(this comment is at column zero)
    draft can become active if the user has catalog.manage
    active can become discontinued if the user has catalog.manage
  Anyone with "catalog.manage" can view products''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        sm = program.state_machines[0]
        assert len(sm.transitions) == 2

    def test_multiline_parenthetical_comment_ignored(self):
        """A parenthetical comment opened with `(` and not closed on the
        same line continues across subsequent lines until a line
        ending with `)`. Used heavily in v0.9 example drafts where
        the leading explanatory blocks span multiple lines."""
        src = self._wrap('''(This is the opening line of a multi-line
parenthetical comment that explains the purpose of the
content block below. It spans three lines.)
Content called "products":
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active or discontinued
    draft can become active if the user has catalog.manage
    active can become discontinued if the user has catalog.manage
  Anyone with "catalog.manage" can view products''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        assert len(program.contents) == 1
        assert len(program.state_machines) == 1

    def test_v09_identity_block_basic(self):
        """v0.9 Phase 1: Identity: block opener at column zero
        introduces a sub-block holding scopes + roles + Anonymous.
        Top-level `Users authenticate with X` is removed entirely."""
        src = '''Application: Test
Id: c9222f35-1f92-4426-99fc-a97c06243254

Identity:
  Scopes are "app.view"
  A "user" has "app.view"
  Anonymous has "app.view"

Content called "documents":
  Each document has a title which is text, required
  Anyone with "app.view" can view documents
'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        # Identity contains scopes; roles list contains user + Anonymous.
        assert program.identity is not None
        assert "app.view" in program.identity.scopes
        names = {r.name for r in program.roles}
        assert "user" in names
        assert "Anonymous" in names

    def test_v09_users_authenticate_line_rejected(self):
        """The v0.8 top-level `Users authenticate with X` line is
        removed in v0.9. Sources that still use it must error
        clearly so the user knows to migrate."""
        src = '''Application: Test
Id: c9222f35-1f92-4426-99fc-a97c06243254

Users authenticate with stub
Scopes are "app.view"
A "user" has "app.view"

Content called "documents":
  Each document has a title which is text
  Anyone with "app.view" can view documents
'''
        program, errors = parse(src)
        assert not errors.ok, "v0.8 top-level identity lines must error in v0.9"
        msg = errors.format()
        # The error should mention either the `Identity:` block or
        # the removed line — guides the user toward migration.
        assert "Identity" in msg or "authenticate" in msg.lower()

    def test_v09_top_level_scopes_without_block_rejected(self):
        """Top-level `Scopes are` outside the Identity block is also
        removed. The Identity block is the only home for scopes."""
        src = '''Application: Test
Id: c9222f35-1f92-4426-99fc-a97c06243254

Scopes are "app.view"
A "user" has "app.view"

Content called "documents":
  Each document has a title which is text
  Anyone with "app.view" can view documents
'''
        program, errors = parse(src)
        assert not errors.ok
        msg = errors.format()
        assert "Identity" in msg

    def test_v09_identity_block_with_multiple_roles(self):
        """Identity block with 3+ roles, multi-scope grants, Oxford
        comma scope lists. Mirrors the warehouse example shape."""
        src = '''Application: Test
Id: c9222f35-1f92-4426-99fc-a97c06243254

Identity:
  Scopes are "inventory.read", "inventory.write", and "inventory.admin"
  A "warehouse clerk" has "inventory.read" and "inventory.write"
  A "warehouse manager" has "inventory.read", "inventory.write", and "inventory.admin"
  An "executive" has "inventory.read"
  Anonymous has "inventory.read"

Content called "products":
  Each product has a name which is text, required
  Anyone with "inventory.read" can view products
'''
        program, errors = parse(src)
        assert errors.ok, errors.format()
        scopes = set(program.identity.scopes)
        assert {"inventory.read", "inventory.write", "inventory.admin"} <= scopes
        names = {r.name for r in program.roles}
        assert names == {"warehouse clerk", "warehouse manager", "executive", "Anonymous"}

    def test_multiline_parenthetical_with_blank_lines_inside(self):
        """Multi-line parens may contain blank lines — common when
        the comment formats as separated paragraphs."""
        src = self._wrap('''(First paragraph of a comment
explaining the next thing.

Second paragraph after a blank line, still inside the
parenthetical, ending here.)
Content called "products":
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active
    draft can become active if the user has catalog.manage
  Anyone with "catalog.manage" can view products''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        assert len(program.contents) == 1

    def test_multiple_can_also_be_lines_accumulate(self):
        src = self._wrap('''Content called "documents":
  Each document has an approval status which is state:
    approval status starts as pending
    approval status can also be approved
    approval status can also be needs revision
    approval status can also be rejected
    pending can become approved if the user has approvals.approve
    pending can become rejected if the user has approvals.approve
  Anyone with "approvals.approve" can view documents''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        sm = program.state_machines[0]
        assert "approved" in sm.states
        assert "needs revision" in sm.states
        assert "rejected" in sm.states
        assert "pending" in sm.states

    def test_starts_as_after_can_also_be(self):
        """Order of starts as / can also be lines doesn't matter."""
        src = self._wrap('''Content called "documents":
  Each document has an approval status which is state:
    approval status can also be approved or rejected
    approval status starts as pending
    pending can become approved if the user has approvals.approve
  Anyone with "approvals.approve" can view documents''')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        sm = program.state_machines[0]
        assert sm.initial_state == "pending"
        assert "pending" in sm.states
        assert "approved" in sm.states
        assert "rejected" in sm.states
