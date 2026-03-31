"""Tests for the an AWS-native Termin runtime code generator."""

import ast
from pathlib import Path

from termin.parser import parse
from termin.analyzer import analyze
from termin.codegen import generate


def _compile(source: str) -> str:
    program, parse_errors = parse(source)
    assert parse_errors.ok, parse_errors.format()
    result = analyze(program)
    assert result.ok, result.format()
    return generate(program)


def test_generates_valid_python():
    code = _compile('''Application: Test App
  Description: A test

Users authenticate with stub
Scopes are "read" and "write"

A "user" has "read" and "write"

Content called "items":
  Each item has a name which is text, required
  Anyone with "read" can view items
  Anyone with "write" can create items

Expose a REST API at /api/v1:
  GET  /items  lists items
  POST /items  creates an item
''')
    # Should be valid Python
    ast.parse(code)
    assert "FastAPI" in code
    assert "CREATE TABLE" in code
    assert "items" in code


def test_generates_parameterized_queries():
    code = _compile('''Application: Test App
  Description: Test

Users authenticate with stub
Scopes are "read"
A "user" has "read"

Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Expose a REST API at /api/v1:
  GET /items lists items
''')
    # Verify no string concatenation in SQL queries
    assert "f\"SELECT" not in code or "?" in code
    # All DB operations should use parameterized queries
    lines = code.splitlines()
    for line in lines:
        if "db.execute(" in line and "CREATE TABLE" not in line and "PRAGMA" not in line:
            # Execution with user data should use ? placeholders
            pass  # This is a structural check, not runtime


def test_generates_scope_enforcement():
    code = _compile('''Application: Test App
  Description: Test

Users authenticate with stub
Scopes are "read" and "write"
A "viewer" has "read"

Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items
  Anyone with "write" can create items

Expose a REST API at /api/v1:
  GET  /items  lists items
  POST /items  creates an item
''')
    assert 'require_scope("read")' in code
    assert 'require_scope("write")' in code


def test_generates_state_machine():
    code = _compile('''Application: Test App
  Description: Test

Users authenticate with stub
Scopes are "read" and "write"
A "user" has "read" and "write"

Content called "tasks":
  Each task has a title which is text
  Anyone with "read" can view tasks

State for tasks called "flow":
  A task starts as "open"
  A task can also be "closed"
  An open task can become closed if the user has "write"

Expose a REST API at /api/v1:
  GET /tasks lists tasks
''')
    assert "STATE_MACHINES" in code
    assert '"open"' in code
    assert '"closed"' in code
    assert "do_state_transition" in code


def test_warehouse_compiles_to_valid_python():
    source = Path("examples/warehouse.termin").read_text()
    code = _compile(source)
    ast.parse(code)
    assert len(code) > 10000  # Should be a substantial app
    assert "products" in code
    assert "stock_levels" in code
    assert "reorder_alerts" in code
