# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 2.x (f) — CEL → Predicate AST compiler tests.

Verifies the compilable subset of CEL maps to the right Predicate
AST nodes, and that out-of-subset expressions raise NotCompilable
(so the runtime knows to fall back to in-process cel-python).
"""

from __future__ import annotations

import pytest

from termin_runtime.cel_predicate import (
    compile_cel_to_predicate, NotCompilable,
)
from termin_runtime.providers.storage_contract import (
    Eq, Ne, Gt, Gte, Lt, Lte, In, Contains, And, Or, Not,
)


# ── Leaf comparisons ────────────────────────────────────────────────


class TestLeafComparisons:
    def test_eq_with_string(self):
        p = compile_cel_to_predicate('status == "draft"')
        assert p == Eq(field="status", value="draft")

    def test_eq_with_int(self):
        p = compile_cel_to_predicate("version == 5")
        assert p == Eq(field="version", value=5)

    def test_eq_with_null(self):
        p = compile_cel_to_predicate("assignee == null")
        assert p == Eq(field="assignee", value=None)

    def test_eq_with_bool(self):
        p = compile_cel_to_predicate("active == true")
        assert p == Eq(field="active", value=True)

    def test_ne(self):
        p = compile_cel_to_predicate('status != "approved"')
        assert p == Ne(field="status", value="approved")

    def test_gt(self):
        p = compile_cel_to_predicate("version > 5")
        assert p == Gt(field="version", value=5)

    def test_gte(self):
        p = compile_cel_to_predicate("rank >= 10")
        assert p == Gte(field="rank", value=10)

    def test_lt(self):
        p = compile_cel_to_predicate("score < 100")
        assert p == Lt(field="score", value=100)

    def test_lte(self):
        p = compile_cel_to_predicate("score <= 50")
        assert p == Lte(field="score", value=50)


# ── In ──────────────────────────────────────────────────────────────


class TestIn:
    def test_in_string_list(self):
        p = compile_cel_to_predicate('status in ["draft", "in_review"]')
        assert p == In(field="status", values=("draft", "in_review"))

    def test_in_int_list(self):
        p = compile_cel_to_predicate("rank in [1, 2, 3]")
        assert p == In(field="rank", values=(1, 2, 3))


# ── Contains ────────────────────────────────────────────────────────


class TestContains:
    def test_contains_substring(self):
        p = compile_cel_to_predicate('label.contains("widget")')
        assert p == Contains(field="label", substring="widget")


# ── Boolean combinators ─────────────────────────────────────────────


class TestBooleanCombinators:
    def test_and(self):
        p = compile_cel_to_predicate(
            'status == "draft" && version > 5'
        )
        assert isinstance(p, And)
        assert len(p.predicates) == 2
        assert Eq(field="status", value="draft") in p.predicates
        assert Gt(field="version", value=5) in p.predicates

    def test_or(self):
        p = compile_cel_to_predicate(
            'status == "draft" || status == "in_review"'
        )
        assert isinstance(p, Or)
        assert len(p.predicates) == 2

    def test_not(self):
        p = compile_cel_to_predicate('!(status == "approved")')
        assert isinstance(p, Not)
        assert p.predicate == Eq(field="status", value="approved")

    def test_nested_and_or(self):
        p = compile_cel_to_predicate(
            '(status == "draft" || status == "in_review") '
            '&& version > 0'
        )
        assert isinstance(p, And)
        # First predicate is the OR (or vice-versa, depending on
        # parse order); just verify structure.
        assert any(isinstance(sub, Or) for sub in p.predicates)


# ── Field-name validation ───────────────────────────────────────────


class TestFieldNameValidation:
    def test_valid_field_accepted(self):
        p = compile_cel_to_predicate(
            'status == "draft"',
            field_names={"status", "version"},
        )
        assert p.field == "status"

    def test_unknown_field_rejected(self):
        with pytest.raises(NotCompilable, match="not a known field"):
            compile_cel_to_predicate(
                'unknown_col == "x"',
                field_names={"status", "version"},
            )


# ── Not compilable ──────────────────────────────────────────────────


class TestNotCompilable:
    def test_arithmetic_rejected(self):
        with pytest.raises(NotCompilable):
            compile_cel_to_predicate("version + 1 > 5")

    def test_function_call_other_than_contains_rejected(self):
        with pytest.raises(NotCompilable, match="contains"):
            compile_cel_to_predicate('label.startsWith("widget")')

    def test_macro_rejected(self):
        # has() / all() / exists() are CEL macros; not pushable.
        with pytest.raises(NotCompilable):
            compile_cel_to_predicate('has(status)')

    def test_lhs_must_be_field_not_literal(self):
        with pytest.raises(NotCompilable):
            compile_cel_to_predicate('"draft" == status')

    def test_rhs_must_be_literal_not_field(self):
        with pytest.raises(NotCompilable):
            compile_cel_to_predicate("status == other_field")


# ── End-to-end with the SQLite provider ─────────────────────────────


class TestEndToEndPushdown:
    """Verify a CEL-derived predicate actually filters records when
    handed to the SqliteStorageProvider's query()."""

    @pytest.mark.asyncio
    async def test_compiled_predicate_filters_via_provider(self, tmp_path):
        from termin_runtime.providers.builtins.storage_sqlite import (
            SqliteStorageProvider,
        )
        from termin_runtime.providers.storage_contract import (
            initial_deploy_diff, QueryOptions,
        )
        db_path = str(tmp_path / "app.db")
        p = SqliteStorageProvider({"db_path": db_path})
        schema = {
            "name": {"snake": "tickets", "display": "tickets",
                     "pascal": "Tickets"},
            "fields": (
                {"name": "title", "business_type": "text",
                 "column_type": "TEXT", "required": True,
                 "unique": False, "minimum": None, "maximum": None,
                 "enum_values": (), "foreign_key": None,
                 "cascade_mode": None, "default_expr": None},
                {"name": "status", "business_type": "text",
                 "column_type": "TEXT", "required": True,
                 "unique": False, "minimum": None, "maximum": None,
                 "enum_values": (), "foreign_key": None,
                 "cascade_mode": None, "default_expr": None},
                {"name": "priority", "business_type": "whole_number",
                 "column_type": "INTEGER", "required": True,
                 "unique": False, "minimum": None, "maximum": None,
                 "enum_values": (), "foreign_key": None,
                 "cascade_mode": None, "default_expr": None},
            ),
            "state_machines": (),
        }
        await p.migrate(initial_deploy_diff([schema]))
        # Insert 4 tickets.
        for label, status, prio in [
            ("a", "draft", 1),
            ("b", "draft", 5),
            ("c", "approved", 3),
            ("d", "draft", 8),
        ]:
            await p.create("tickets", {
                "title": label, "status": status, "priority": prio,
            })

        # Compile a CEL filter.
        pred = compile_cel_to_predicate(
            'status == "draft" && priority > 2',
            field_names={"title", "status", "priority"},
        )
        page = await p.query("tickets", pred, QueryOptions(limit=10))
        titles = sorted(r["title"] for r in page.records)
        assert titles == ["b", "d"]
