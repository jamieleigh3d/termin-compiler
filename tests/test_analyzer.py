"""Tests for the an AWS-native Termin runtime semantic analyzer and security invariant checker."""

from termin.parser import parse
from termin.analyzer import analyze
from termin.errors import SemanticError, SecurityError


def _analyze(source: str):
    program, parse_errors = parse(source)
    assert parse_errors.ok, parse_errors.format()
    return analyze(program)


VALID_BASE = '''Users authenticate with stub
Scopes are "read" and "write"
A "user" has "read" and "write"
'''


def test_valid_program():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items
''')
    assert result.ok, result.format()


def test_content_without_access_rules():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
''')
    assert not result.ok
    assert result.has_security_errors
    assert any("no access rules" in str(e) for e in result.errors)


def test_undefined_scope_in_role():
    result = _analyze('''Users authenticate with stub
Scopes are "read"
A "user" has "read" and "nonexistent"

Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items
''')
    assert not result.ok
    assert any("undefined scope" in str(e).lower() for e in result.errors)


def test_undefined_content_reference():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a parent which references nonexistent, required
  Anyone with "read" can view items
''')
    assert not result.ok
    assert any(isinstance(e, SemanticError) for e in result.errors)


def test_undefined_scope_in_access_rule():
    result = _analyze('''Users authenticate with stub
Scopes are "read"
A "user" has "read"

Content called "items":
  Each item has a name which is text
  Anyone with "fake_scope" can view items
''')
    assert not result.ok
    assert any("undefined scope" in str(e).lower() for e in result.errors)


def test_state_transition_to_undefined_state():
    result = _analyze(VALID_BASE + '''
Content called "tasks":
  Each task has a title which is text
  Anyone with "read" can view tasks

State for tasks called "flow":
  A task starts as "open"
  A task can also be "closed"
  An open task can become nonexistent if the user has "write"
''')
    assert not result.ok
    assert any("undefined state" in str(e).lower() for e in result.errors)


def test_state_machine_undefined_content():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

State for nonexistent called "flow":
  A thing starts as "open"
''')
    assert not result.ok
    assert any("undefined content" in str(e).lower() for e in result.errors)


def test_warehouse_example_passes():
    from pathlib import Path
    source = Path("examples/warehouse.termin").read_text()
    program, parse_errors = parse(source)
    assert parse_errors.ok
    result = analyze(program)
    assert result.ok, result.format()
