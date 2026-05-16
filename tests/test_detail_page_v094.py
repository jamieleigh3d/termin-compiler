# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""v0.9.4 Phase 2 — detail-page primitive.

Source form:

    As a viewer, I want to see one note in detail so that I can read it:
      Show a detail page for notes called "Note Detail"
      Display a table of notes

Lowers to a PageEntry whose `record_binding` is the snake_case plural
content name. The runtime registers `/<page-slug>/{id}` for these
pages and fetches the record by id before rendering the child
contracts. Without `record_binding`, the page routes at `/<slug>` as
before.

This test covers grammar + analyzer + lower in one file because the
slice is small. If it grows, split it.
"""

from __future__ import annotations

import pytest

from termin.ast_nodes import ShowPage
from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower


# ── Grammar tests ─────────────────────────────────────────────────


def _minimal_program(detail_line: str = '  Show a detail page for notes called "Note Detail"') -> str:
    """A complete program declaring a `notes` content type and one
    user story whose body is the detail-page directive under test."""
    return f'''Application: DetailDemo
Identity:
  Scopes are "view"
  A "viewer" has "view"

Content called "notes":
  Each note has a title which is text, required
  Each note has a body which is text
  Anyone with "view" can view notes
  Anyone with "view" can create notes

As a viewer, I want to see one note in detail so that I can read it:
{detail_line}
'''


class TestParseDetailPageLine:
    def test_minimal_detail_page_parses(self):
        program, errors = parse(_minimal_program())
        assert errors.ok, errors.format()
        story = program.stories[0]
        # The first directive should be a ShowPage with record_binding set.
        directives = [d for d in story.directives if isinstance(d, ShowPage)]
        assert len(directives) == 1
        page = directives[0]
        assert page.page_name == "Note Detail"
        assert page.record_binding == "notes"

    def test_detail_page_does_not_match_bare_show_a_page(self):
        """`Show a page called "X"` must NOT pick up record_binding —
        regression guard against accidental classification."""
        src = _minimal_program('  Show a page called "Notes List"')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        story = program.stories[0]
        directives = [d for d in story.directives if isinstance(d, ShowPage)]
        assert len(directives) == 1
        assert directives[0].page_name == "Notes List"
        assert directives[0].record_binding == ""

    def test_detail_page_with_multi_word_name(self):
        src = _minimal_program('  Show a detail page for notes called "Note Detail View"')
        program, errors = parse(src)
        assert errors.ok, errors.format()
        page = next(
            d for d in program.stories[0].directives
            if isinstance(d, ShowPage)
        )
        assert page.page_name == "Note Detail View"
        assert page.record_binding == "notes"


# ── Analyzer tests ───────────────────────────────────────────────


class TestAnalyzeDetailPage:
    def test_valid_detail_page_accepted(self):
        src = _minimal_program()
        program, perrs = parse(src)
        assert perrs.ok, perrs.format()
        errors = analyze(program)
        assert errors.ok, errors.format()

    def test_unknown_plural_rejected_a110(self):
        """Detail page bound to a plural that doesn't name any
        declared content type must surface as TERMIN-A110 — the
        runtime would otherwise serve a 404-for-everything route."""
        src = _minimal_program('  Show a detail page for widgets called "Widget Detail"')
        program, perrs = parse(src)
        assert perrs.ok, perrs.format()
        errors = analyze(program)
        assert not errors.ok, errors.format()
        codes = [e.code for e in errors.errors]
        assert "TERMIN-A110" in codes, (
            f"Expected TERMIN-A110 (unknown content); got {codes!r}"
        )

    def test_role_without_view_scope_rejected_a111(self):
        """A detail page where the story's role lacks a `can view`
        scope on the bound content would render but instantly 403 —
        catch at compile time. TERMIN-A111."""
        src = '''Application: DetailDemo
Identity:
  Scopes are "edit"
  An "editor" has "edit"

Content called "notes":
  Each note has a title which is text, required
  Anyone with "edit" can create notes

As an editor, I want to see one note in detail so that I can read it:
  Show a detail page for notes called "Note Detail"
'''
        program, perrs = parse(src)
        assert perrs.ok, perrs.format()
        errors = analyze(program)
        assert not errors.ok, errors.format()
        codes = [e.code for e in errors.errors]
        assert "TERMIN-A111" in codes, (
            f"Expected TERMIN-A111 (role lacks view scope); got {codes!r}"
        )


# ── Lower tests ──────────────────────────────────────────────────


class TestLowerDetailPage:
    def test_lower_emits_page_with_record_binding(self):
        src = _minimal_program()
        program, perrs = parse(src)
        assert perrs.ok, perrs.format()
        app_spec = lower(program)
        pages = [p for p in app_spec.pages if p.name == "Note Detail"]
        assert len(pages) == 1
        page = pages[0]
        assert page.record_binding == "notes", (
            f"Page IR must carry record_binding; got {page.record_binding!r}"
        )

    def test_lower_regular_page_has_no_record_binding(self):
        """Regression: a `Show a page called` page must NOT have
        record_binding set — the runtime distinguishes detail vs
        regular routes by this field."""
        src = _minimal_program('  Show a page called "Plain Page"')
        program, perrs = parse(src)
        assert perrs.ok, perrs.format()
        app_spec = lower(program)
        page = next(p for p in app_spec.pages if p.name == "Plain Page")
        assert page.record_binding == "" or page.record_binding is None
