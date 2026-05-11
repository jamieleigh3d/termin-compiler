# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Bare default literals (numbers, yes, no) on field declarations.

Closes compiler issues #4 and #6 (both surfaced by Airlock-on-Termin
slice A3 authoring). Today the grammar's `constraint` rule has two
default alternatives:

    | 'defaults' 'to' expr:expr        #DefaultExpr
    | 'defaults' 'to' lit:quoted_string    #DefaultLiteral

`expr` requires backticks (\`<cel-expression>\`); `quoted_string`
requires double quotes. Bare literals like `defaults to 300`,
`defaults to 0`, `defaults to no`, `defaults to yes` match
NEITHER and the constraint silently fails to attach. The IR
ends up with `default_expr = None` for those fields. Downstream
the runtime returns `null` for the column on create, breaking
any consumer that expects the declared default value.

Fix: a third constraint alternative `#DefaultBare` accepts bare
numbers + bare yes/no tokens. Both forms lower to the same
`default_expr` shape the existing alternatives produce.
"""

from __future__ import annotations

import pytest

from termin.peg_parser import parse_peg


def _parse_and_get_field_defaults(src):
    """Helper: parse, return {field_name: default_expr} for all
    fields on the first content type."""
    program, errors = parse_peg(src)
    assert errors.ok, f"parse errors: {errors.messages}"
    out = {}
    for c in program.contents:
        for f in c.fields:
            out[f.name] = (f.type_expr.default_expr,
                           f.type_expr.default_is_expr)
    return out


_SRC_TEMPLATE = '''Application: Bare Default Test
  Description: defaults
Id: 9e3f4a5b-6c7d-4e8f-8a9b-1c2d3e4f5a6b

Identity:
  Scopes are "play"
  An "anonymous" has "play"

Content called "sessions":
{fields}
  Anyone with "play" can view sessions
  Anyone with "play" can create sessions
'''


class TestBareNumericDefaults:
    def test_bare_integer_default(self):
        """`defaults to 300` (bare integer) parses and lands on the
        field as a default literal — same shape as the quoted
        alternative would produce."""
        src = _SRC_TEMPLATE.format(
            fields='  Each session has timer_seconds which is a whole number, defaults to 300\n'
        )
        defaults = _parse_and_get_field_defaults(src)
        assert defaults["timer_seconds"][0] == "300", (
            f"expected default_expr='300'; got {defaults['timer_seconds']}"
        )

    def test_bare_zero_default(self):
        """`defaults to 0` is the most common — message_count
        counters, retry attempts, etc."""
        src = _SRC_TEMPLATE.format(
            fields='  Each session has counter which is a whole number, defaults to 0\n'
        )
        defaults = _parse_and_get_field_defaults(src)
        assert defaults["counter"][0] == "0"

    def test_bare_negative_integer(self):
        src = _SRC_TEMPLATE.format(
            fields='  Each session has offset which is a whole number, defaults to -5\n'
        )
        defaults = _parse_and_get_field_defaults(src)
        assert defaults["offset"][0] == "-5"

    def test_bare_decimal_default(self):
        src = _SRC_TEMPLATE.format(
            fields='  Each session has rate which is text, defaults to 1.5\n'
        )
        defaults = _parse_and_get_field_defaults(src)
        assert defaults["rate"][0] == "1.5"


class TestBareYesNoDefaults:
    def test_bare_no_default(self):
        """`defaults to no` is the natural form for a yes-or-no
        field. Today it silently drops; this test verifies it
        lands on the field correctly."""
        src = _SRC_TEMPLATE.format(
            fields='  Each session has fired which is yes or no, defaults to no\n'
        )
        defaults = _parse_and_get_field_defaults(src)
        # The bare 'no' token gets stored as the literal 'no' so
        # downstream consumers can treat it the same as
        # `defaults to "no"`.
        assert defaults["fired"][0] == "no", (
            f"expected default_expr='no'; got {defaults['fired']}"
        )

    def test_bare_yes_default(self):
        src = _SRC_TEMPLATE.format(
            fields='  Each session has enabled which is yes or no, defaults to yes\n'
        )
        defaults = _parse_and_get_field_defaults(src)
        assert defaults["enabled"][0] == "yes"


class TestExistingFormsStillWork:
    """Regression guards: the two pre-existing alternatives
    (quoted-string + backtick-CEL) must continue to parse."""

    def test_quoted_string_default_still_works(self):
        src = _SRC_TEMPLATE.format(
            fields='  Each session has status which is text, defaults to "draft"\n'
        )
        defaults = _parse_and_get_field_defaults(src)
        assert defaults["status"][0] == "draft"

    def test_backtick_cel_default_still_works(self):
        src = _SRC_TEMPLATE.format(
            fields='  Each session has now_field which is text, defaults to `now`\n'
        )
        defaults = _parse_and_get_field_defaults(src)
        assert defaults["now_field"][0] == "now"
        assert defaults["now_field"][1] is True  # marked as expression

    def test_backtick_numeric_cel_default_still_works(self):
        """Pre-existing escape hatch: `defaults to \`300\`` works."""
        src = _SRC_TEMPLATE.format(
            fields='  Each session has timer which is a whole number, defaults to `300`\n'
        )
        defaults = _parse_and_get_field_defaults(src)
        assert defaults["timer"][0] == "300"
        assert defaults["timer"][1] is True


class TestDefaultsApplyAtCreateTime:
    """End-to-end: bare defaults reach the runtime and get applied
    at create time (the original A3b smoke failure)."""

    def test_runtime_applies_bare_numeric_default(self, tmp_path):
        from fastapi.testclient import TestClient
        from termin.lower import lower
        from termin_core.ir.serialize import serialize_ir
        from termin_server import create_termin_app

        src = _SRC_TEMPLATE.format(
            fields=(
                '  Each session has timer_seconds which is a whole number, defaults to 300\n'
                '  Each session has counter which is a whole number, defaults to 0\n'
                '  Anyone with "play" can update sessions\n'
            )
        )
        program, errors = parse_peg(src)
        assert errors.ok
        spec = lower(program)
        app = create_termin_app(
            serialize_ir(spec), db_path=str(tmp_path / "defaults.db"))
        with TestClient(app) as c:
            r = c.post("/api/v1/sessions", json={})
            assert r.status_code in (200, 201), r.text
            body = r.json()
            assert body.get("timer_seconds") == 300, (
                f"bare numeric default not applied; got {body}"
            )
            assert body.get("counter") == 0, (
                f"bare zero default not applied; got {body}"
            )

    def test_runtime_applies_bare_yes_no_default(self, tmp_path):
        from fastapi.testclient import TestClient
        from termin.lower import lower
        from termin_core.ir.serialize import serialize_ir
        from termin_server import create_termin_app

        src = _SRC_TEMPLATE.format(
            fields=(
                '  Each session has fired which is yes or no, defaults to no\n'
                '  Each session has enabled which is yes or no, defaults to yes\n'
                '  Anyone with "play" can update sessions\n'
            )
        )
        program, errors = parse_peg(src)
        assert errors.ok
        spec = lower(program)
        app = create_termin_app(
            serialize_ir(spec), db_path=str(tmp_path / "yesno.db"))
        with TestClient(app) as c:
            r = c.post("/api/v1/sessions", json={})
            assert r.status_code in (200, 201), r.text
            body = r.json()
            assert body.get("fired") == "no", (
                f"bare 'no' default not applied; got {body}"
            )
            assert body.get("enabled") == "yes", (
                f"bare 'yes' default not applied; got {body}"
            )
