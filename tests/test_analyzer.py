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


# ── Compute checks ──

def test_compute_without_access():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Compute called "summarize":
  Reduce: takes items, produces items
''')
    assert not result.ok
    assert result.has_security_errors
    assert any("no access rule" in str(e).lower() for e in result.errors)


def test_compute_undefined_input_content():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Compute called "transform":
  Transform: takes nonexistent, produces items
  Anyone with "read" can execute this
''')
    assert not result.ok
    assert any("undefined" in str(e).lower() and "input" in str(e).lower() for e in result.errors)


def test_compute_undefined_scope():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Compute called "transform":
  Transform: takes items, produces items
  Anyone with "fake_scope" can execute this
''')
    assert not result.ok
    assert any("undefined" in str(e).lower() and "scope" in str(e).lower() for e in result.errors)


def test_compute_invalid_shape():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Compute called "bad":
  Anyone with "read" can execute this
''')
    # Shape is empty string - should flag as invalid
    assert not result.ok


def test_compute_chain_undefined_step():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Compute called "pipeline":
  Chain: nonexistent_step then another_missing
  Anyone with "read" can execute this
''')
    assert not result.ok
    assert any("undefined" in str(e).lower() and "step" in str(e).lower() for e in result.errors)


def test_compute_valid():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Compute called "summarize":
  Reduce: takes items, produces items
  Anyone with "read" can execute this
''')
    assert result.ok, result.format()


# ── Channel checks ──

def test_channel_undefined_content():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Channel called "hook":
  Carries nonexistent
  Protocol: webhook
  Requires "read" to send
''')
    assert not result.ok
    assert any("undefined" in str(e).lower() and "content" in str(e).lower() for e in result.errors)


def test_channel_invalid_protocol():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Channel called "hook":
  Carries items
  Protocol: ftp
  Requires "read" to send
''')
    assert not result.ok
    assert any("invalid protocol" in str(e).lower() for e in result.errors)


def test_channel_without_auth():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Channel called "hook":
  Carries items
  Protocol: webhook
''')
    assert not result.ok
    assert result.has_security_errors
    assert any("no authentication" in str(e).lower() for e in result.errors)


def test_channel_internal_no_auth_ok():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Channel called "bus":
  Carries items
  Protocol: internal
''')
    assert result.ok, result.format()


# ── Boundary checks ──

def test_boundary_undefined_content():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Boundary called "mod":
  Contains nonexistent
  Identity inherits from application
''')
    assert not result.ok
    assert any("undefined" in str(e).lower() and "item" in str(e).lower() for e in result.errors)


def test_boundary_undefined_restrict_scope():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Boundary called "mod":
  Contains items
  Identity restricts to "fake_scope"
''')
    assert not result.ok
    assert result.has_security_errors


def test_boundary_valid():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Boundary called "mod":
  Contains items
  Identity restricts to "read"
''')
    assert result.ok, result.format()


def test_anonymous_story_passes():
    result = _analyze('''Application: Hello World
  Description: A test

As anonymous, I want to see a page "Hello" so that I can be greeted:
  Display text "Hello, World"
''')
    assert result.ok, result.format()


def test_hello_example_passes():
    from pathlib import Path
    source = Path("examples/hello.termin").read_text()
    program, parse_errors = parse(source)
    assert parse_errors.ok, parse_errors.format()
    result = analyze(program)
    assert result.ok, result.format()


def test_compute_role_access_valid():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Compute called "summarize":
  Reduce: takes items, produces items
  user can execute this
''')
    assert result.ok, result.format()


def test_compute_role_access_undefined():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Compute called "summarize":
  Reduce: takes items, produces items
  nonexistent_role can execute this
''')
    assert not result.ok


def test_hello_user_example_passes():
    from pathlib import Path
    source = Path("examples/hello_user.termin").read_text()
    program, parse_errors = parse(source)
    assert parse_errors.ok, parse_errors.format()
    result = analyze(program)
    assert result.ok, result.format()


def test_compute_demo_passes():
    from pathlib import Path
    source = Path("examples/compute_demo.termin").read_text()
    program, parse_errors = parse(source)
    assert parse_errors.ok, parse_errors.format()
    result = analyze(program)
    assert result.ok, result.format()
