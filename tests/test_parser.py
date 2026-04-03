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


# ── Compute parsing ──

def test_parse_compute_transform():
    program, errors = parse('''Compute called "enrich order":
  Transform: takes an order, produces an order
  Add tax calculation
  Anyone with "write" can execute this''')
    assert errors.ok
    c = program.computes[0]
    assert c.name == "enrich order"
    assert c.shape == "transform"
    assert c.inputs == ["order"]
    assert c.outputs == ["order"]
    assert c.body_lines == ["Add tax calculation"]
    assert c.access_scope == "write"


def test_parse_compute_reduce():
    program, errors = parse('''Compute called "summarize":
  Reduce: takes orders, produces a report
  Anyone with "read" can execute this''')
    assert errors.ok
    c = program.computes[0]
    assert c.shape == "reduce"
    assert c.inputs == ["orders"]
    assert c.outputs == ["report"]


def test_parse_compute_correlate():
    program, errors = parse('''Compute called "match":
  Correlate: takes invoices and payments, produces reports
  Anyone with "read" can execute this''')
    assert errors.ok
    c = program.computes[0]
    assert c.shape == "correlate"
    assert c.inputs == ["invoices", "payments"]
    assert c.outputs == ["reports"]


def test_parse_compute_route():
    program, errors = parse('''Compute called "classify":
  Route: takes a ticket, produces one of bugs or features
  Anyone with "write" can execute this''')
    assert errors.ok
    c = program.computes[0]
    assert c.shape == "route"
    assert c.inputs == ["ticket"]
    assert c.outputs == ["bugs", "features"]


# ── Channel parsing ──

def test_parse_channel_inbound_reliable():
    program, errors = parse('''Channel called "order hook":
  Carries orders
  Direction: inbound
  Delivery: reliable
  Endpoint: /webhooks/orders
  Requires "write" to send''')
    assert errors.ok
    ch = program.channels[0]
    assert ch.name == "order hook"
    assert ch.carries == "orders"
    assert ch.direction == "inbound"
    assert ch.delivery == "reliable"
    assert ch.endpoint == "/webhooks/orders"
    assert len(ch.requirements) == 1
    assert ch.requirements[0].scope == "write"
    assert ch.requirements[0].direction == "send"


def test_parse_channel_outbound_realtime():
    program, errors = parse('''Channel called "updates":
  Carries items
  Direction: outbound
  Delivery: realtime
  Requires "read" to receive''')
    assert errors.ok
    ch = program.channels[0]
    assert ch.direction == "outbound"
    assert ch.delivery == "realtime"
    assert ch.requirements[0].direction == "receive"


def test_parse_channel_internal():
    program, errors = parse('''Channel called "bus":
  Carries items
  Direction: internal
  Delivery: auto''')
    assert errors.ok
    ch = program.channels[0]
    assert ch.direction == "internal"
    assert ch.delivery == "auto"
    assert len(ch.requirements) == 0


# ── Boundary parsing ──

def test_parse_boundary_inherit():
    program, errors = parse('''Boundary called "inventory":
  Contains products, stock levels, and alerts
  Identity inherits from application''')
    assert errors.ok
    b = program.boundaries[0]
    assert b.name == "inventory"
    assert b.contains == ["products", "stock levels", "alerts"]
    assert b.identity_mode == "inherit"
    assert b.identity_parent == "application"


def test_parse_boundary_restrict():
    program, errors = parse('''Boundary called "reporting":
  Contains reports
  Identity restricts to "read only"''')
    assert errors.ok
    b = program.boundaries[0]
    assert b.name == "reporting"
    assert b.contains == ["reports"]
    assert b.identity_mode == "restrict"
    assert b.identity_scopes == ["read only"]


def test_parse_anonymous_story():
    program, errors = parse('''As anonymous, I want to see a page "Hello" so that I can be greeted:
  Display text "Hello, World"''')
    assert errors.ok, errors.format()
    story = program.stories[0]
    assert story.role == "anonymous"
    assert story.objective == "I can be greeted"
    # ShowPage auto-extracted from action
    assert any(isinstance(d, ShowPage) and d.page_name == "Hello" for d in story.directives)
    # DisplayText parsed
    assert any(isinstance(d, DisplayText) and d.text == "Hello, World" for d in story.directives)


def test_parse_inline_so_that():
    program, errors = parse('''As a user, I want to see things so that I can browse:
  Display text "Welcome"''')
    assert errors.ok, errors.format()
    story = program.stories[0]
    assert story.role == "user"
    assert story.objective == "I can browse"


def test_parse_inline_page():
    program, errors = parse('''As a user, I want to see a page "Dashboard"
  so that I can view data:
    Display text "Welcome to the dashboard"''')
    assert errors.ok, errors.format()
    story = program.stories[0]
    # ShowPage should be auto-created from action text
    show_pages = [d for d in story.directives if isinstance(d, ShowPage)]
    assert len(show_pages) >= 1
    assert show_pages[0].page_name == "Dashboard"


def test_parse_bare_role():
    program, errors = parse('Anonymous has "view" and "write"')
    assert errors.ok, errors.format()
    assert program.roles[0].name == "Anonymous"
    assert program.roles[0].scopes == ["view", "write"]


def test_parse_compute_typed_params():
    program, errors = parse('''Compute called "greet":
  Transform: takes u : UserProfile, produces "msg" : Text
  msg = "Hello " + u.Name
  Admin can execute this''')
    assert errors.ok, errors.format()
    c = program.computes[0]
    assert c.input_params[0].name == "u"
    assert c.input_params[0].type_name == "UserProfile"
    assert c.output_params[0].name == "msg"
    assert c.output_params[0].type_name == "Text"
    assert c.access_role == "Admin"
    assert c.access_scope is None
    assert 'msg = "Hello " + u.Name' in c.body_lines


# ── JEXL bracket syntax (v2) ──

def test_parse_display_text_jexl_brackets():
    program, errors = parse('''As anonymous, I want to see a page "Hello" so that I can test:
  Display text [SayHello(user.name)]''')
    assert errors.ok, errors.format()
    dt = [d for d in program.stories[0].directives if isinstance(d, DisplayText)]
    assert dt[0].is_expression is True
    assert dt[0].text == "SayHello(user.name)"


def test_parse_event_jexl():
    program, errors = parse('''When [stockLevel.updated && stockLevel.quantity <= stockLevel.reorderThreshold]:
  Create a reorder alert with the product, warehouse''')
    assert errors.ok, errors.format()
    ev = program.events[0]
    assert ev.trigger == "jexl"
    assert ev.jexl_condition == "stockLevel.updated && stockLevel.quantity <= stockLevel.reorderThreshold"


def test_parse_compute_jexl_body():
    program, errors = parse('''Compute called "greet":
  Transform: takes u : UserProfile, produces greeting : Text
  [greeting = "Hello, " + u.FirstName + "!"]
  "Admin" can execute this''')
    assert errors.ok, errors.format()
    c = program.computes[0]
    assert 'greeting = "Hello, " + u.FirstName + "!"' in c.body_lines
    assert c.access_role == "Admin"


def test_parse_highlight_jexl():
    program, errors = parse('''As a user, I want to see items
  so that I can browse:
    Show a page called "Items"
    Display a table of items with columns: name, quantity
    Highlight rows where [quantity <= threshold]''')
    assert errors.ok, errors.format()
    hl = [d for d in program.stories[0].directives if isinstance(d, HighlightRows)]
    assert hl[0].jexl_condition == "quantity <= threshold"


def test_parse_new_types():
    program, errors = parse('''Content called "items":
  Each item has a price which is number
  Each item has a margin which is percentage
  Each item has a active which is true/false
  Each item has a created which is date
  Each item has a updated which is date and time
  Each item has a tags which is list of text
  Each item has a count which is whole number, minimum 0, maximum 9999
  Anyone with "read" can view items''')
    assert errors.ok, errors.format()
    c = program.contents[0]
    types = {f.name: f.type_expr for f in c.fields}
    assert types["price"].base_type == "number"
    assert types["margin"].base_type == "percentage"
    assert types["active"].base_type == "boolean"
    assert types["created"].base_type == "date"
    assert types["updated"].base_type == "datetime"
    assert types["tags"].base_type == "list"
    assert types["tags"].list_type == "text"
    assert types["count"].base_type == "whole_number"
    assert types["count"].minimum == 0
    assert types["count"].maximum == 9999


def test_parse_all_examples():
    from pathlib import Path
    for name in ["hello", "hello_user", "warehouse", "helpdesk", "projectboard", "compute_demo"]:
        source = Path(f"examples/{name}.termin").read_text()
        program, errors = parse(source)
        assert errors.ok, f"{name}: {errors.format()}"


def test_parse_hello_user_example():
    from pathlib import Path
    source = Path("examples/hello_user.termin").read_text()
    program, errors = parse(source)
    assert errors.ok, errors.format()
    assert program.application.name == "Hello User"
    assert len(program.roles) == 2
    assert len(program.stories) == 2
    assert len(program.computes) == 1
    assert program.computes[0].access_role == "LoggedInUser"


def test_parse_hello_example():
    from pathlib import Path
    source = Path("examples/hello.termin").read_text()
    program, errors = parse(source)
    assert errors.ok, errors.format()
    assert program.application.name == "Hello World"
    assert len(program.stories) == 1
    assert program.stories[0].role == "anonymous"


def test_parse_compute_demo():
    from pathlib import Path
    source = Path("examples/compute_demo.termin").read_text()
    program, errors = parse(source)
    assert errors.ok, errors.format()
    assert len(program.computes) == 5
    assert len(program.channels) == 4
    assert len(program.boundaries) == 2
