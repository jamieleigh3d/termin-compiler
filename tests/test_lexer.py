"""Tests for the an AWS-native Termin runtime lexer."""

from termin.lexer import tokenize, TokenType


def test_tokenize_application():
    tokens = tokenize('Application: My App\n  Description: A test app')
    assert tokens[0].type == TokenType.APPLICATION
    assert tokens[0].line == 1
    assert tokens[1].type == TokenType.DESCRIPTION


def test_tokenize_identity():
    tokens = tokenize('Users authenticate with stub\nScopes are "read" and "write"')
    assert tokens[0].type == TokenType.USERS_AUTHENTICATE
    assert tokens[1].type == TokenType.SCOPES_ARE


def test_tokenize_roles():
    tokens = tokenize('A "admin" has "read" and "write"\nAn "executive" has "read"')
    assert tokens[0].type == TokenType.ROLE_DECL
    assert tokens[1].type == TokenType.ROLE_DECL


def test_tokenize_content():
    source = '''Content called "items":
  Each item has a name which is text, required
  Anyone with "read" can view items'''
    tokens = tokenize(source)
    assert tokens[0].type == TokenType.CONTENT_DECL
    assert tokens[1].type == TokenType.FIELD_DECL
    assert tokens[2].type == TokenType.ACCESS_RULE


def test_tokenize_multiword_singular():
    source = '  Each stock level has a product which references products, required'
    tokens = tokenize(source)
    assert tokens[0].type == TokenType.FIELD_DECL


def test_tokenize_state():
    source = '''State for products called "lifecycle":
  A product starts as "draft"
  A product can also be "active"
  A draft product can become active if the user has "write"'''
    tokens = tokenize(source)
    assert tokens[0].type == TokenType.STATE_DECL
    assert tokens[1].type == TokenType.STATE_STARTS
    assert tokens[2].type == TokenType.STATE_ALSO
    assert tokens[3].type == TokenType.STATE_TRANSITION


def test_tokenize_skips_comments():
    tokens = tokenize('--- Identity ---\nUsers authenticate with stub')
    assert len(tokens) == 1
    assert tokens[0].type == TokenType.USERS_AUTHENTICATE


def test_tokenize_skips_blank_lines():
    tokens = tokenize('\n\nApplication: Test\n\n')
    assert len(tokens) == 1


def test_tokenize_warehouse_example():
    from pathlib import Path
    source = Path("examples/warehouse.termin").read_text()
    tokens = tokenize(source)
    unknowns = [t for t in tokens if t.type == TokenType.UNKNOWN]
    assert len(unknowns) == 0, f"Unknown tokens: {unknowns}"
    assert len(tokens) > 50


# ── Compute tokens ──

def test_tokenize_compute_decl():
    tokens = tokenize('Compute called "calculate total":')
    assert tokens[0].type == TokenType.COMPUTE_DECL

def test_tokenize_compute_shape():
    tokens = tokenize('  Transform: takes an order, produces an order')
    assert tokens[0].type == TokenType.COMPUTE_SHAPE

def test_tokenize_compute_all_shapes():
    for shape in ["Transform", "Reduce", "Expand", "Correlate", "Route"]:
        tokens = tokenize(f'  {shape}: takes items, produces items')
        assert tokens[0].type == TokenType.COMPUTE_SHAPE, f"{shape} not recognized"

def test_tokenize_compute_access_reuses_access_rule():
    tokens = tokenize('  Anyone with "admin" can execute this')
    assert tokens[0].type == TokenType.ACCESS_RULE


# ── Channel tokens ──

def test_tokenize_channel_decl():
    tokens = tokenize('Channel called "order webhook":')
    assert tokens[0].type == TokenType.CHANNEL_DECL

def test_tokenize_channel_carries():
    tokens = tokenize('  Carries orders')
    assert tokens[0].type == TokenType.CHANNEL_CARRIES

def test_tokenize_channel_direction():
    tokens = tokenize('  Direction: inbound')
    assert tokens[0].type == TokenType.CHANNEL_DIRECTION

def test_tokenize_channel_delivery():
    tokens = tokenize('  Delivery: reliable')
    assert tokens[0].type == TokenType.CHANNEL_DELIVERY

def test_tokenize_channel_requires():
    tokens = tokenize('  Requires "write orders" to send')
    assert tokens[0].type == TokenType.CHANNEL_REQUIRES

def test_tokenize_channel_endpoint():
    tokens = tokenize('  Endpoint: /webhooks/orders')
    assert tokens[0].type == TokenType.CHANNEL_ENDPOINT


# ── Boundary tokens ──

def test_tokenize_boundary_decl():
    tokens = tokenize('Boundary called "inventory module":')
    assert tokens[0].type == TokenType.BOUNDARY_DECL

def test_tokenize_boundary_contains():
    tokens = tokenize('  Contains products, stock levels, and alerts')
    assert tokens[0].type == TokenType.BOUNDARY_CONTAINS

def test_tokenize_boundary_identity_inherits():
    tokens = tokenize('  Identity inherits from application')
    assert tokens[0].type == TokenType.BOUNDARY_IDENTITY

def test_tokenize_boundary_identity_restricts():
    tokens = tokenize('  Identity restricts to "read only"')
    assert tokens[0].type == TokenType.BOUNDARY_IDENTITY


# ── Compute demo example ──

def test_tokenize_parenthesis_comment():
    tokens = tokenize('(This is a comment.)')
    assert len(tokens) == 0

def test_tokenize_inline_parenthesis_comment():
    tokens = tokenize('Users authenticate with stub (defines CurrentUser)')
    assert tokens[0].type == TokenType.USERS_AUTHENTICATE
    assert '(defines' not in tokens[0].value

def test_tokenize_bare_role():
    tokens = tokenize('Anonymous has "view app"')
    assert tokens[0].type == TokenType.ROLE_DECL

def test_tokenize_anonymous_story():
    tokens = tokenize('As anonymous, I want to see a page "Hello" so that I can be greeted:')
    assert tokens[0].type == TokenType.STORY_HEADER

def test_tokenize_display_text():
    tokens = tokenize('  Display text "Hello, World"')
    assert tokens[0].type == TokenType.DISPLAY_TEXT

def test_tokenize_display_aggregation_still_works():
    tokens = tokenize('  Display total product count')
    assert tokens[0].type == TokenType.DISPLAY_AGGREGATION


# ── JEXL bracket syntax (v2) ──

def test_tokenize_jexl_block():
    tokens = tokenize('  [greeting = "Hello, " + u.FirstName + "!"]')
    assert tokens[0].type == TokenType.JEXL_BLOCK

def test_tokenize_when_jexl():
    tokens = tokenize('When [stockLevel.updated && stockLevel.quantity <= stockLevel.reorderThreshold]:')
    assert tokens[0].type == TokenType.EVENT_WHEN

def test_tokenize_display_text_jexl():
    tokens = tokenize('  Display text [SayHelloTo(LoggedInUser.CurrentUser)]')
    assert tokens[0].type == TokenType.DISPLAY_TEXT

def test_tokenize_all_examples():
    from pathlib import Path
    for name in ["hello", "hello_user", "warehouse", "helpdesk", "projectboard", "compute_demo"]:
        source = Path(f"examples/{name}.termin").read_text()
        tokens = tokenize(source)
        # No tokens should be UNKNOWN except Compute body lines (which may be UNKNOWN or JEXL_BLOCK)
        assert len(tokens) > 0, f"{name} produced no tokens"


def test_tokenize_compute_demo_example():
    from pathlib import Path
    source = Path("examples/compute_demo.termin").read_text()
    tokens = tokenize(source)
    # Body lines inside compute blocks are UNKNOWN, which is expected
    compute_decls = [t for t in tokens if t.type == TokenType.COMPUTE_DECL]
    assert len(compute_decls) == 5
    channel_decls = [t for t in tokens if t.type == TokenType.CHANNEL_DECL]
    assert len(channel_decls) == 4
    boundary_decls = [t for t in tokens if t.type == TokenType.BOUNDARY_DECL]
    assert len(boundary_decls) == 2
