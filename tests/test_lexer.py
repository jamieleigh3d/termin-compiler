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
