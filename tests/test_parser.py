"""Tests for the an AWS-native Termin runtime parser."""

from termin.parser import parse
from termin.ast_nodes import *


def test_parse_application():
    program, errors = parse('Application: My App\n  Description: A test app')
    assert errors.ok
    assert program.application.name == "My App"
    assert program.application.description == "A test app"


def test_parse_identity():
    program, errors = parse(
        'Users authenticate with stub\n'
        'Scopes are "read", "write", and "admin"'
    )
    assert errors.ok
    assert program.identity.provider == "stub"
    assert program.identity.scopes == ["read", "write", "admin"]


def test_parse_roles():
    program, errors = parse(
        'Users authenticate with stub\n'
        'Scopes are "read" and "write"\n'
        'A "user" has "read"\n'
        'A "admin" has "read" and "write"'
    )
    assert errors.ok
    assert len(program.roles) == 2
    assert program.roles[0].name == "user"
    assert program.roles[0].scopes == ["read"]
    assert program.roles[1].name == "admin"
    assert program.roles[1].scopes == ["read", "write"]


def test_parse_content_fields():
    program, errors = parse('''Content called "products":
  Each product has a name which is text, required
  Each product has a cost which is currency
  Each product has a count which is a whole number, minimum 0
  Each product has a status which is one of: active, inactive
  Anyone with "read" can view products''')
    assert errors.ok
    c = program.contents[0]
    assert c.name == "products"
    assert c.singular == "product"
    assert len(c.fields) == 4
    assert c.fields[0].name == "name"
    assert c.fields[0].type_expr.base_type == "text"
    assert c.fields[0].type_expr.required is True
    assert c.fields[1].type_expr.base_type == "currency"
    assert c.fields[2].type_expr.base_type == "whole_number"
    assert c.fields[2].type_expr.minimum == 0
    assert c.fields[3].type_expr.base_type == "enum"
    assert c.fields[3].type_expr.enum_values == ["active", "inactive"]


def test_parse_content_references():
    program, errors = parse('''Content called "items":
  Each item has a name which is text
  Anyone with "read" can view items

Content called "details":
  Each detail has a item which references items, required
  Anyone with "read" can view details''')
    assert errors.ok
    assert len(program.contents) == 2
    ref_field = program.contents[1].fields[0]
    assert ref_field.type_expr.base_type == "reference"
    assert ref_field.type_expr.references == "items"
    assert ref_field.type_expr.required is True


def test_parse_state_machine():
    program, errors = parse('''Content called "tasks":
  Each task has a title which is text
  Anyone with "read" can view tasks

State for tasks called "task flow":
  A task starts as "open"
  A task can also be "closed" or "archived"
  An open task can become closed if the user has "write"
  A closed task can become archived if the user has "admin"''')
    assert errors.ok
    sm = program.state_machines[0]
    assert sm.machine_name == "task flow"
    assert sm.initial_state == "open"
    assert set(sm.states) == {"open", "closed", "archived"}
    assert len(sm.transitions) == 2
    assert sm.transitions[0].from_state == "open"
    assert sm.transitions[0].to_state == "closed"


def test_parse_event():
    program, errors = parse(
        'When a item is updated and its count is at or below its threshold:\n'
        '  Create a alert with the item, count, and threshold'
    )
    assert errors.ok
    ev = program.events[0]
    assert ev.content_name == "item"
    assert ev.trigger == "updated"
    assert ev.condition.field1 == "count"
    assert ev.condition.operator == "at or below"
    assert ev.condition.field2 == "threshold"
    assert ev.action.create_content == "alert"
    assert ev.action.fields == ["item", "count", "threshold"]


def test_parse_user_story():
    program, errors = parse('''As a user, I want to see all items
  so that I can browse:
    Show a page called "Item List"
    Display a table of items with columns: name, status
    Allow filtering by status
    Allow searching by name''')
    assert errors.ok
    story = program.stories[0]
    assert story.role == "user"
    assert isinstance(story.directives[0], ShowPage)
    assert story.directives[0].page_name == "Item List"
    assert isinstance(story.directives[1], DisplayTable)
    assert story.directives[1].content_name == "items"
    assert story.directives[1].columns == ["name", "status"]
    assert isinstance(story.directives[2], AllowFilter)
    assert isinstance(story.directives[3], AllowSearch)


def test_parse_navigation():
    program, errors = parse('''Navigation bar:
  "Home" links to "Dashboard" visible to all
  "Admin" links to "Admin Panel" visible to admin''')
    assert errors.ok
    nav = program.navigation
    assert len(nav.items) == 2
    assert nav.items[0].label == "Home"
    assert nav.items[0].page_name == "Dashboard"
    assert nav.items[1].visible_to == ["admin"]


def test_parse_api():
    program, errors = parse('''Expose a REST API at /api/v1:
  GET    /items       lists items
  POST   /items       creates an item
  DELETE /items/{id}  deletes an item''')
    assert errors.ok
    api = program.api
    assert api.base_path == "/api/v1"
    assert len(api.endpoints) == 3
    assert api.endpoints[0].method == "GET"
    assert api.endpoints[2].method == "DELETE"


def test_parse_warehouse_example():
    from pathlib import Path
    source = Path("examples/warehouse.termin").read_text()
    program, errors = parse(source)
    assert errors.ok, errors.format()
    assert program.application.name == "Warehouse Inventory Manager"
    assert len(program.contents) == 3
    assert len(program.state_machines) == 1
    assert len(program.events) == 1
    assert len(program.stories) == 5
    assert program.navigation is not None
    assert program.api is not None
    assert len(program.streams) == 1


def test_parse_error_unknown_line():
    program, errors = parse('This is garbage')
    assert not errors.ok
