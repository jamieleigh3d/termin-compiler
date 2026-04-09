"""Tests for the Termin semantic analyzer and security invariant checker."""

from termin.peg_parser import parse_peg as parse
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


def test_channel_direction_delivery_valid():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Channel called "hook":
  Carries items
  Direction: inbound
  Delivery: reliable
  Requires "read" to send
''')
    assert result.ok, result.format()


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
  Direction: inbound
  Requires "read" to send
''')
    assert not result.ok
    assert any("undefined" in str(e).lower() and "content" in str(e).lower() for e in result.errors)


def test_channel_without_auth():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Channel called "hook":
  Carries items
  Direction: inbound
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
  Direction: internal
  Delivery: auto
''')
    assert result.ok, result.format()


def test_channel_invalid_direction():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Channel called "hook":
  Carries items
  Direction: sideways
  Requires "read" to send
''')
    assert not result.ok
    assert any("invalid direction" in str(e).lower() for e in result.errors)


def test_channel_invalid_delivery():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Channel called "hook":
  Carries items
  Direction: inbound
  Delivery: express
  Requires "read" to send
''')
    assert not result.ok
    assert any("invalid delivery" in str(e).lower() for e in result.errors)


# ── Channel Action checks ──

def test_channel_action_valid():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Channel called "tools":
  Direction: outbound
  Delivery: reliable
  Action called "do-thing":
    Takes name which is text
    Returns result which is text
    Requires "read" to invoke
''')
    assert result.ok


def test_channel_action_undefined_scope():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Channel called "tools":
  Direction: outbound
  Delivery: reliable
  Action called "do-thing":
    Takes name which is text
    Returns result which is text
    Requires "nonexistent" to invoke
''')
    assert not result.ok
    assert any("undefined scope" in str(e).lower() for e in result.errors)


def test_channel_empty_no_carries_no_actions():
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Channel called "nothing":
  Direction: outbound
  Delivery: reliable
  Requires "read" to send
''')
    assert not result.ok
    assert any("no data" in str(e).lower() or "no actions" in str(e).lower() for e in result.errors)


def test_channel_action_only_satisfies_auth():
    """A Channel with only action scopes (no channel-level requirements) still passes auth check."""
    result = _analyze(VALID_BASE + '''
Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Channel called "tools":
  Direction: outbound
  Delivery: reliable
  Action called "do-thing":
    Takes name which is text
    Returns result which is text
    Requires "read" to invoke
''')
    assert result.ok


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


def test_all_examples_pass():
    from pathlib import Path
    for name in ["hello", "hello_user", "warehouse", "helpdesk", "projectboard", "compute_demo"]:
        source = Path(f"examples/{name}.termin").read_text()
        program, parse_errors = parse(source)
        assert parse_errors.ok, f"{name} parse: {parse_errors.format()}"
        result = analyze(program)
        assert result.ok, f"{name} analyze: {result.format()}"


def test_compute_demo_passes():
    from pathlib import Path
    source = Path("examples/compute_demo.termin").read_text()
    program, parse_errors = parse(source)
    assert parse_errors.ok, parse_errors.format()
    result = analyze(program)
    assert result.ok, result.format()
