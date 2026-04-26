# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Compiler coverage tests — exercise parser, analyzer, and lowering paths.

Each test compiles a .termin DSL snippet that exercises specific parser
fallback branches (lines that fire when TatSu PEG fails and the Python
string-based fallback handles the line).

Also covers lowering branches, CLI serve command, and backend discovery.
"""

import json
import pytest
from pathlib import Path

from termin.peg_parser import parse_peg as parse, _classify_line
from termin.analyzer import analyze
from termin.lower import lower


EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


# ── Full example compilation (exercises all parser paths) ──

class TestAllExamplesCompile:
    """Compile every example file through parse → analyze → lower.

    This exercises the parser fallback paths for every DSL feature used
    in the examples, plus analyzer cross-references and lowering branches.
    """

    @pytest.mark.parametrize("termin_file", sorted(EXAMPLES_DIR.glob("*.termin")),
                             ids=lambda p: p.stem)
    def test_compile_example(self, termin_file):
        source = termin_file.read_text(encoding="utf-8")
        program, errors = parse(source)
        assert errors.ok, f"{termin_file.name}: {errors.format()}"
        result = analyze(program)
        assert result.ok, f"{termin_file.name}: {result.format()}"
        spec = lower(program)
        assert spec.name, f"{termin_file.name}: lowered spec has no name"

    @pytest.mark.parametrize("termin_file", sorted(EXAMPLES_DIR.glob("*.termin")),
                             ids=lambda p: p.stem)
    def test_lower_to_json(self, termin_file):
        """Lowered spec should serialize to valid JSON."""
        from dataclasses import asdict
        from termin.cli import _ir_json_default, _simplify_props

        source = termin_file.read_text(encoding="utf-8")
        program, _ = parse(source)
        analyze(program)
        spec = lower(program)
        ir_dict = asdict(spec)
        _simplify_props(ir_dict)
        ir_json = json.dumps(ir_dict, default=_ir_json_default)
        # Should round-trip
        ir = json.loads(ir_json)
        assert ir["name"] == spec.name


# ── DSL feature coverage: specific constructs ──

class TestParserFeatureCoverage:
    """Tests for specific DSL features that exercise uncovered parser branches."""

    def test_state_machine_with_transitions(self):
        """State machine parsing exercises transition fallback paths."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "view", "edit", and "admin"\n'
            '  An "editor" has "view" and "edit"\n'
            '  An "admin" has "view", "edit", and "admin"\n\n'
            'Content called "tasks":\n'
            '  Each task has a title which is text, required\n'
            '  Each task has a priority which is one of: "low", "medium", "high"\n'
            '  Each task has a status which is state:\n'
            '    status starts as open\n'
            '    status can also be in progress, review, or closed\n'
            '    open can become in progress if the user has edit\n'
            '    in progress can become review if the user has edit\n'
            '    review can become closed if the user has admin\n'
            '    closed can become open if the user has admin\n'
            '  Anyone with "view" can view tasks\n'
            '  Anyone with "edit" can create or update tasks\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()
        spec = lower(program)
        assert len(spec.state_machines) == 1
        sm = spec.state_machines[0]
        assert len(sm.transitions) >= 4

    def test_event_with_create_action(self):
        """Event with create action exercises event parsing."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "admin"\n  An "admin" has "admin"\n\n'
            'Content called "orders":\n'
            '  Each order has a customer which is text\n'
            '  Anyone with "admin" can create or view orders\n\n'
            'Content called "logs":\n'
            '  Each log has a message which is text\n'
            '  Anyone with "admin" can create or view logs\n\n'
            'When `orders.created`:\n'
            '  Create a log with message\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()

    def test_display_with_filter_and_search(self):
        """Display directives exercise presentation parsing."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "view"\n  A "viewer" has "view"\n\n'
            'Content called "products":\n'
            '  Each product has a name which is text, required\n'
            '  Each product has a category which is one of: "A", "B", "C"\n'
            '  Each product has a price which is currency\n'
            '  Anyone with "view" can view products\n\n'
            'As a viewer, I want to browse products\n'
            '  so that I can find what I need:\n'
            '    Show a page called "Product Catalog"\n'
            '    Display a table of products with columns: name, category, price\n'
            '    Allow filtering by category\n'
            '    Allow searching by name\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()
        spec = lower(program)
        assert len(spec.pages) >= 1

    def test_display_with_form_and_submit(self):
        """Form with submit exercises form parsing paths."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "edit"\n  An "editor" has "edit"\n\n'
            'Content called "notes":\n'
            '  Each note has a title which is text, required\n'
            '  Each note has a body which is long text\n'
            '  Anyone with "edit" can create or view notes\n\n'
            'As an editor, I want to create notes\n'
            '  so that I can record information:\n'
            '    Show a page called "Notes"\n'
            '    Display a table of notes with columns: title, body\n'
            '    Accept input for title, body\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()
        spec = lower(program)
        # Should have form fields
        page = spec.pages[0]
        # Walk component tree for form
        has_form = any(c.type == "form" for c in page.children)
        assert has_form, "Page should have a form component"

    def test_channel_with_actions(self):
        """Channel with typed RPC actions exercises channel parsing."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "admin"\n  An "admin" has "admin"\n\n'
            'Content called "orders":\n'
            '  Each order has a title which is text\n'
            '  Anyone with "admin" can create or view orders\n\n'
            'Channel called "order service":\n'
            '  Carries orders\n'
            '  Direction: outbound\n'
            '  Delivery: reliable\n'
            '  Endpoint: https://example.com/api\n'
            '  Requires "admin" to send\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()
        spec = lower(program)
        assert len(spec.channels) == 1
        ch = spec.channels[0]
        assert ch is not None

    def test_event_send_to_channel(self):
        """Event with Send to channel exercises event send parsing."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "admin"\n  An "admin" has "admin"\n\n'
            'Content called "orders":\n'
            '  Each order has a title which is text\n'
            '  Anyone with "admin" can create or view orders\n\n'
            'Channel called "notifications":\n'
            '  Carries orders\n'
            '  Direction: outbound\n'
            '  Delivery: reliable\n'
            '  Endpoint: https://example.com/notify\n'
            '  Requires "admin" to send\n\n'
            'When `orders.created`:\n'
            '  Send order to "notifications"\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()

    def test_compute_with_cel_body(self):
        """Compute with CEL body exercises compute parsing."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "admin"\n  An "admin" has "admin"\n\n'
            'Content called "items":\n'
            '  Each item has a value which is currency\n'
            '  Anyone with "admin" can view items\n\n'
            'Compute called "total":\n'
            '  Transform: takes items, produces items\n'
            '  `sum(items.value)`\n'
            '  Anyone with "admin" can execute this\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()

    def test_compute_with_llm_provider(self):
        """Compute with LLM provider exercises provider parsing."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "admin"\n  An "admin" has "admin"\n\n'
            'Content called "summaries":\n'
            '  Each summary has a prompt which is text\n'
            '  Each summary has a response which is long text\n'
            '  Anyone with "admin" can create summaries\n\n'
            'Compute called "summarize":\n'
            '  Provider is "llm"\n'
            '  Input from field summary.prompt\n'
            '  Output into field summary.response\n'
            '  Directive is:\n'
            '    ```\n'
            '    Summarize the input text.\n'
            '    ```\n'
            '  Trigger on event "summaries.created"\n'
            '  Anyone with "admin" can execute this\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()

    def test_error_handler_declaration(self):
        """Error handler exercises error handler parsing."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "admin"\n  An "admin" has "admin"\n\n'
            'Content called "items":\n'
            '  Each item has a name which is text\n'
            '  Anyone with "admin" can create items\n\n'
            'On error from "items":\n'
            '  Log level: WARNING\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()

    def test_mark_rows_as(self):
        """Mark rows as exercises semantic mark parsing."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "view"\n  A "viewer" has "view"\n\n'
            'Content called "tasks":\n'
            '  Each task has a title which is text\n'
            '  Each task has a priority which is one of: "low", "high"\n'
            '  Anyone with "view" can view tasks\n\n'
            'As a viewer, I want to see tasks\n'
            '  so that I can work on them:\n'
            '    Show a page called "Tasks"\n'
            '    Display a table of tasks with columns: title, priority\n'
            '    Mark rows where `priority == "high"` as "urgent"\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()

    def test_related_data(self):
        """Related data display exercises related parsing."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "view"\n  A "viewer" has "view"\n\n'
            'Content called "projects":\n'
            '  Each project has a name which is text\n'
            '  Anyone with "view" can view projects\n\n'
            'Content called "tasks":\n'
            '  Each task has a title which is text\n'
            '  Each task has a project which references projects\n'
            '  Anyone with "view" can view tasks\n\n'
            'As a viewer, I want to see project details\n'
            '  so that I can manage work:\n'
            '    Show a page called "Projects"\n'
            '    Display a table of projects with columns: name\n'
            '    Display related tasks by project\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()

    def test_highlight_with_expression(self):
        """Highlight expression exercises highlight parsing."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "view"\n  A "viewer" has "view"\n\n'
            'Content called "items":\n'
            '  Each item has a quantity which is a whole number\n'
            '  Each item has a minimum which is a whole number\n'
            '  Anyone with "view" can view items\n\n'
            'As a viewer, I want to see low stock\n'
            '  so that I can reorder:\n'
            '    Show a page called "Inventory"\n'
            '    Display a table of items with columns: quantity, minimum\n'
            '    Highlight rows where quantity is at or below minimum\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()

    def test_text_with_cel_expression(self):
        """Text with CEL expression exercises text/expression parsing."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "view"\n  A "viewer" has "view"\n\n'
            'Content called "items":\n'
            '  Each item has a name which is text\n'
            '  Anyone with "view" can view items\n\n'
            'As a viewer, I want to see a welcome\n'
            '  so that I know who I am:\n'
            '    Show a page called "Home"\n'
            '    Display text "Welcome to the app"\n'
            '    Display `"Hello, " + User.Name`\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()



# ── Lowering branch coverage ──

class TestLoweringBranches:
    """Cover specific lowering branches for channels, events, and pages."""

    def test_lower_channel_with_websocket(self):
        """WebSocket channel lowering."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "admin"\n  An "admin" has "admin"\n\n'
            'Content called "events":\n'
            '  Each event has a data which is text\n'
            '  Anyone with "admin" can view events\n\n'
            'Channel called "stream":\n'
            '  Carries events\n'
            '  Direction: outbound\n'
            '  Delivery: realtime\n'
            '  Endpoint: wss://example.com/stream\n'
            '  Requires "admin" to send\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()
        spec = lower(program)
        ch = [c for c in spec.channels if c.name.snake == "stream"][0]
        assert "REALTIME" in str(ch.delivery)

    def test_lower_multiple_pages_same_role(self):
        """Multiple pages for same role exercises page merge logic."""
        source = (
            'Application: Test\n  Description: t\n\n'
            'Identity:\n'
            '  Scopes are "view"\n  A "viewer" has "view"\n\n'
            'Content called "items":\n'
            '  Each item has a name which is text\n'
            '  Anyone with "view" can view items\n\n'
            'As a viewer, I want to see items\n'
            '  so that I can browse:\n'
            '    Show a page called "Items"\n'
            '    Display a table of items with columns: name\n\n'
            'As a viewer, I want to see analytics\n'
            '  so that I can understand trends:\n'
            '    Show a page called "Analytics"\n'
            '    Display text "Coming soon"\n'
        )
        program, errors = parse(source)
        assert errors.ok, errors.format()
        result = analyze(program)
        assert result.ok, result.format()
        spec = lower(program)
        assert len(spec.pages) >= 2
