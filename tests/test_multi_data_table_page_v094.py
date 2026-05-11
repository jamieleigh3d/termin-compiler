# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""A page with multiple `Display a table of <content>` directives must
emit one ComponentNode per directive in source order.

Regression: lower_pages used to accumulate one `cur_data_table` and
only flush it at end-of-loop, so a second `Display a table` directive
silently overwrote the first — the earlier table never made it into
the page's children. The compiler also inserted the lone surviving
table at children[0] instead of preserving its source-order position.

Surfaced by airlock Results page (`Display a table of sessions /
Using "airlock.score-axis-card"` followed by `Display a table of
profiles / Using "airlock.badge-strip"`); only the profiles table
made it into the IR. Same bug would silently affect any future page
that needs more than one data_table.

The fix preserves source order: each new DisplayTable flushes the
prior one to children before starting a new one, and the final
flush at end-of-loop appends rather than insert(0)-ing. Single-
data_table pages are unaffected (the only directive sequence in
existing examples).
"""

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower


_TWO_TABLES = """
Application: Two Tables
Description: Page with two Display-a-table directives.

Identity:
  Scopes are "view"
  A "viewer" has "view"

Content called "sessions":
  Each session has a label which is text, required
  Anyone with "view" can view sessions

Content called "profiles":
  Each profile has a name which is text, required
  Anyone with "view" can view profiles

As a viewer, I want to see results so that I know how I did:
  Show a page called "Results"
  Display a table of sessions
    Using "airlock.score-axis-card"
  Display a table of profiles
    Using "airlock.badge-strip"
"""


_TABLE_THEN_AGGREGATION = """
Application: Table Then Agg
Description: Single data_table followed by a count stat_breakdown.

Identity:
  Scopes are "view"
  A "viewer" has "view"

Content called "items":
  Each item has a name which is text, required
  Each item has a category which is text
  Anyone with "view" can view items

As a viewer, I want to see items so that I can browse:
  Show a page called "Items"
  Display a table of items with columns: name, category
  Display count of items grouped by category
"""


_THREE_TABLES = """
Application: Three Tables
Description: Triple-table page (defensive).

Identity:
  Scopes are "view"
  A "viewer" has "view"

Content called "alpha":
  Each alpha has a label which is text, required
  Anyone with "view" can view alpha

Content called "beta":
  Each beta has a label which is text, required
  Anyone with "view" can view beta

Content called "gamma":
  Each gamma has a label which is text, required
  Anyone with "view" can view gamma

As a viewer, I want to see triples so that I have my data:
  Show a page called "Triples"
  Display a table of alpha
  Display a table of beta
  Display a table of gamma
"""


class TestMultipleDataTablesPerPage:
    """Multiple Display-a-table directives produce one ComponentNode
    each in source order."""

    def test_two_tables_both_appear_in_children(self):
        ast, errs = parse(_TWO_TABLES)
        assert errs.ok, f"Parse errors: {errs}"
        analyze(ast)
        spec = lower(ast)
        page = next(p for p in spec.pages if p.slug == "results")
        data_tables = [c for c in page.children if c.type == "data_table"]
        assert len(data_tables) == 2, (
            f"Expected 2 data_tables, got {len(data_tables)}: "
            f"{[(t.props.get('source'), getattr(t, 'contract', None)) for t in data_tables]}"
        )

    def test_two_tables_preserve_source_order(self):
        ast, errs = parse(_TWO_TABLES)
        assert errs.ok
        analyze(ast)
        spec = lower(ast)
        page = next(p for p in spec.pages if p.slug == "results")
        data_tables = [c for c in page.children if c.type == "data_table"]
        # Source declares sessions/score-axis-card FIRST, profiles/
        # badge-strip second. The IR must preserve that order so the
        # author's page-layout intent is respected.
        assert data_tables[0].props["source"] == "sessions"
        assert data_tables[0].contract == "airlock.score-axis-card"
        assert data_tables[1].props["source"] == "profiles"
        assert data_tables[1].contract == "airlock.badge-strip"

    def test_three_tables_all_appear(self):
        """Defensive: three or more tables also work."""
        ast, errs = parse(_THREE_TABLES)
        assert errs.ok
        analyze(ast)
        spec = lower(ast)
        page = next(p for p in spec.pages if p.slug == "triples")
        data_tables = [c for c in page.children if c.type == "data_table"]
        assert len(data_tables) == 3
        assert [t.props["source"] for t in data_tables] == ["alpha", "beta", "gamma"]


class TestSingleTableUnchanged:
    """Single-data_table pages — the existing common case — must still
    have the table appear in children. The fix changes positioning
    semantics (append vs insert(0)) but a one-table page has only one
    landing spot so the visible IR is identical."""

    def test_table_then_stat_breakdown_table_appears(self):
        ast, errs = parse(_TABLE_THEN_AGGREGATION)
        assert errs.ok
        analyze(ast)
        spec = lower(ast)
        page = next(p for p in spec.pages if p.slug == "items")
        data_tables = [c for c in page.children if c.type == "data_table"]
        assert len(data_tables) == 1
        assert data_tables[0].props["source"] == "items"

    def test_table_then_stat_breakdown_table_appears_in_source_order(self):
        """Source order: data_table THEN stat_breakdown. After the fix the
        table appears before the stat_breakdown in children — matches
        source intent. (Pre-fix used insert(0) which happened to put
        the table first too, so this is a behavior-preserving claim
        for this specific layout — see CHANGELOG for the broader
        ordering policy change.)"""
        ast, errs = parse(_TABLE_THEN_AGGREGATION)
        assert errs.ok
        analyze(ast)
        spec = lower(ast)
        page = next(p for p in spec.pages if p.slug == "items")
        types_in_order = [c.type for c in page.children]
        # data_table first, stat_breakdown second.
        assert types_in_order.index("data_table") < types_in_order.index("stat_breakdown")
