# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 cascade grammar tests.

Covers BRD §6.2 cascade declaration requirements:
  - bare `references X` is a parse error (TERMIN-S039)
  - cascade-on-delete on non-reference field (TERMIN-S040)
  - duplicate cascade modes on same reference (TERMIN-S041)
  - transitive cascade-restrict deadlock (TERMIN-S042)
  - multi-content cascade cycle (TERMIN-S043)

Plus IR shape, SQL emission, and pos/neg fixture round-trip.

These tests are RED before implementation lands and GREEN after the
grammar/parser/analyzer/lowering/IR/runtime changes are in place.
"""
from pathlib import Path

import pytest

from termin.analyzer import analyze
from termin.errors import SemanticError
from termin.lower import lower
from termin.peg_parser import parse_peg as parse


FIXTURES = Path(__file__).parent / "fixtures" / "cascade"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _compile(source: str):
    """Parse + analyze. Returns (program, parse_result, analysis_result)."""
    program, parse_result = parse(source)
    if not parse_result.ok:
        return program, parse_result, None
    analysis = analyze(program)
    return program, parse_result, analysis


def _errors_with_code(result, code: str):
    return [e for e in result.errors if getattr(e, "code", None) == code]


# ── §5.1 grammar tests ──────────────────────────────────────────────


class TestCascadeGrammarParse:
    """Parser accepts cascade clauses; analyzer enforces semantics."""

    def test_cascade_clause_appears_in_typeexpr(self):
        src = _read_fixture("cascade_demo.termin")
        program, parse_result, _ = _compile(src)
        assert parse_result.ok, parse_result.format()
        # Find the "cascade children" content's "parent" field.
        cc = next(c for c in program.contents if c.name == "cascade children")
        parent_field = next(f for f in cc.fields if f.name == "parent")
        assert parent_field.type_expr.references == "parents"
        assert parent_field.type_expr.cascade_mode == "cascade"
        # And the restrict variant.
        rc = next(c for c in program.contents if c.name == "restrict children")
        rparent = next(f for f in rc.fields if f.name == "parent")
        assert rparent.type_expr.cascade_mode == "restrict"

    def test_cascade_position_flexible(self):
        # cascade clause can appear before or after `required`.
        before = '''Identity:
  Scopes are "x.view", "x.manage"
  A "user" has "x.view" and "x.manage"

Content called "ps":
  Each p has a name which is text, required
  Anyone with "x.view" can view ps
  Anyone with "x.manage" can create ps

Content called "cs":
  Each c has a p which references ps, cascade on delete, required
  Anyone with "x.view" can view cs
  Anyone with "x.manage" can create cs
'''
        after = before.replace(
            "references ps, cascade on delete, required",
            "references ps, required, cascade on delete",
        )
        for src, label in [(before, "before"), (after, "after")]:
            _, parse_result, analysis = _compile(src)
            assert parse_result.ok, f"[{label}] {parse_result.format()}"
            assert analysis.ok, f"[{label}] {analysis.format()}"


# ── §5.1 negative grammar tests ─────────────────────────────────────


class TestCascadeGrammarErrors:
    def test_bare_references_rejected(self):
        src = _read_fixture("cascade_bare_rejected.termin")
        _, parse_result, analysis = _compile(src)
        assert parse_result.ok, parse_result.format()
        assert not analysis.ok
        errs = _errors_with_code(analysis, "TERMIN-S039")
        assert len(errs) == 1, f"expected 1 S039, got {analysis.errors}"
        # Error message should be actionable.
        msg = errs[0].message
        assert "cascade on delete" in msg or "restrict on delete" in msg

    def test_cascade_on_non_reference_rejected(self):
        src = _read_fixture("cascade_on_text_rejected.termin")
        _, parse_result, analysis = _compile(src)
        # Parser may accept; analyzer rejects.
        assert parse_result.ok, parse_result.format()
        assert not analysis.ok
        errs = _errors_with_code(analysis, "TERMIN-S040")
        assert len(errs) >= 1

    def test_duplicate_cascade_modes_rejected(self):
        src = _read_fixture("cascade_double_rejected.termin")
        _, parse_result, analysis = _compile(src)
        assert parse_result.ok, parse_result.format()
        assert not analysis.ok
        errs = _errors_with_code(analysis, "TERMIN-S041")
        assert len(errs) >= 1


# ── §5.1.5 static cascade-restrict mix detection ────────────────────


class TestCascadeStaticCheck:
    def test_simple_deadlock_rejected(self):
        src = _read_fixture("cascade_deadlock_simple_rejected.termin")
        _, parse_result, analysis = _compile(src)
        assert parse_result.ok, parse_result.format()
        assert not analysis.ok
        errs = _errors_with_code(analysis, "TERMIN-S042")
        assert len(errs) >= 1
        # Error must cite the contributing edges (file:line in message
        # or via .line on the error).
        msg = errs[0].message
        # Message should name the deadlock-target content.
        assert "bs" in msg

    def test_diamond_deadlock_rejected(self):
        src = _read_fixture("cascade_deadlock_diamond_rejected.termin")
        _, parse_result, analysis = _compile(src)
        assert parse_result.ok, parse_result.format()
        assert not analysis.ok
        errs = _errors_with_code(analysis, "TERMIN-S042")
        assert len(errs) >= 1
        # Diamond: target is "branches"
        assert any("branches" in e.message for e in errs)

    def test_pure_cascade_chain_accepted(self):
        src = _read_fixture("cascade_multihop_ok.termin")
        _, parse_result, analysis = _compile(src)
        assert parse_result.ok, parse_result.format()
        assert analysis.ok, f"multihop should pass: {analysis.format()}"

    def test_self_cascade_accepted(self):
        src = _read_fixture("cascade_self_ref.termin")
        _, parse_result, analysis = _compile(src)
        assert parse_result.ok, parse_result.format()
        assert analysis.ok, f"self-cascade should pass: {analysis.format()}"

    def test_optional_cascade_reference_accepted(self):
        src = _read_fixture("cascade_optional.termin")
        _, parse_result, analysis = _compile(src)
        assert parse_result.ok, parse_result.format()
        assert analysis.ok, f"optional cascade FK should pass: {analysis.format()}"

    def test_unrelated_cascade_and_restrict_accepted(self):
        # A cascade B AND C restrict D, with B and D distinct.
        # No shared target node ⇒ no deadlock.
        src = '''Identity:
  Scopes are "u.view", "u.manage"
  A "user" has "u.view" and "u.manage"

Content called "as":
  Each a has a name which is text, required
  Anyone with "u.view" can view as
  Anyone with "u.manage" can create as

Content called "bs":
  Each b has a name which is text, required
  Each b has an a which references as, required, cascade on delete
  Anyone with "u.view" can view bs
  Anyone with "u.manage" can create bs

Content called "cs":
  Each c has a name which is text, required
  Anyone with "u.view" can view cs
  Anyone with "u.manage" can create cs

Content called "ds":
  Each d has a name which is text, required
  Each d has a c which references cs, required, restrict on delete
  Anyone with "u.view" can view ds
  Anyone with "u.manage" can create ds
'''
        _, parse_result, analysis = _compile(src)
        assert parse_result.ok, parse_result.format()
        assert analysis.ok, analysis.format()


class TestCascadeCycleDetection:
    def test_multi_content_cascade_cycle_rejected(self):
        src = _read_fixture("cascade_cycle_rejected.termin")
        _, parse_result, analysis = _compile(src)
        assert parse_result.ok, parse_result.format()
        assert not analysis.ok
        errs = _errors_with_code(analysis, "TERMIN-S043")
        assert len(errs) >= 1


# ── §5.3 IR shape tests ─────────────────────────────────────────────


class TestCascadeIRShape:
    def test_cascade_mode_in_field_spec(self):
        src = _read_fixture("cascade_demo.termin")
        program, parse_result, analysis = _compile(src)
        assert parse_result.ok and analysis.ok, (
            parse_result.format() + analysis.format()
        )
        spec = lower(program)
        # Find the cascade children content schema.
        cc = next(s for s in spec.content if s.name.snake == "cascade_children")
        parent_field = next(f for f in cc.fields if f.name == "parent")
        assert parent_field.cascade_mode == "cascade"
        rc = next(s for s in spec.content if s.name.snake == "restrict_children")
        rparent = next(f for f in rc.fields if f.name == "parent")
        assert rparent.cascade_mode == "restrict"

    def test_non_reference_fields_have_none_cascade_mode(self):
        src = _read_fixture("cascade_demo.termin")
        program, _, analysis = _compile(src)
        assert analysis.ok, analysis.format()
        spec = lower(program)
        for content in spec.content:
            for field in content.fields:
                if field.foreign_key is None:
                    assert field.cascade_mode is None, (
                        f"{content.name.snake}.{field.name} is not a reference "
                        f"but has cascade_mode={field.cascade_mode!r}"
                    )

    def test_all_reference_fields_have_cascade_mode(self):
        # After analysis succeeds, every reference field MUST have a
        # cascade_mode in {"cascade", "restrict"}.
        src = _read_fixture("cascade_demo.termin")
        program, _, analysis = _compile(src)
        assert analysis.ok
        spec = lower(program)
        ref_fields = [
            (c, f) for c in spec.content
            for f in c.fields if f.foreign_key is not None
        ]
        assert ref_fields, "test fixture must contain reference fields"
        for content, field in ref_fields:
            assert field.cascade_mode in ("cascade", "restrict"), (
                f"{content.name.snake}.{field.name} has cascade_mode="
                f"{field.cascade_mode!r}"
            )


# ── Examples migration sanity check ─────────────────────────────────


class TestExamplesMigrated:
    """Every example using `references` should have a cascade clause
    after migration. This test fails RED until §4.10 migration lands.
    """

    def test_all_example_references_have_cascade_clause(self):
        examples = (Path(__file__).parent.parent / "examples").glob("*.termin")
        for path in examples:
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if "references" not in stripped:
                    continue
                # Skip non-field references (docs, comments).
                if not stripped.startswith("Each "):
                    continue
                if "cascade on delete" in stripped or "restrict on delete" in stripped:
                    continue
                pytest.fail(
                    f"{path.name}:{lineno} uses `references` without "
                    f"cascade clause: {stripped}"
                )
