"""Tests for the Lark-based parser.

Verifies that ``lark_parser.parse_lark()`` produces equivalent AST nodes
to ``parser.parse()`` for every example file (v1 and v2).
"""

from pathlib import Path

import pytest

from termin.parser import parse
from termin.lark_parser import parse_lark
from termin.ast_nodes import (
    Program, Application, Identity, Role, Content, Field, TypeExpr,
    AccessRule, StateMachine, Transition, EventRule, EventCondition,
    EventAction, UserStory, ShowPage, DisplayTable, ShowRelated,
    HighlightRows, AllowFilter, AllowSearch, SubscribeTo, AcceptInput,
    ValidateUnique, CreateAs, AfterSave, ShowChart, DisplayAggregation,
    NavBar, NavItem, ApiSection, ApiEndpoint, Stream,
    ComputeNode, ComputeParam, ChannelDecl, ChannelRequirement, BoundaryDecl,
    DisplayText,
)

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

# All example files to test
EXAMPLE_FILES = sorted(EXAMPLES_DIR.glob("*.termin"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_application_eq(a: Application, b: Application, label: str):
    assert a.name == b.name, f"{label}: application name"
    assert a.description == b.description, f"{label}: application description"


def _assert_identity_eq(a: Identity, b: Identity, label: str):
    assert a.provider == b.provider, f"{label}: identity provider"
    assert a.scopes == b.scopes, f"{label}: identity scopes"


def _assert_type_expr_eq(a: TypeExpr, b: TypeExpr, label: str):
    assert a.base_type == b.base_type, f"{label}: base_type"
    assert a.required == b.required, f"{label}: required"
    assert a.unique == b.unique, f"{label}: unique"
    assert a.minimum == b.minimum, f"{label}: minimum"
    assert a.maximum == b.maximum, f"{label}: maximum"
    assert a.enum_values == b.enum_values, f"{label}: enum_values"
    assert a.references == b.references, f"{label}: references"
    assert a.list_type == b.list_type, f"{label}: list_type"


def _assert_field_eq(a: Field, b: Field, label: str):
    assert a.name == b.name, f"{label}: field name"
    _assert_type_expr_eq(a.type_expr, b.type_expr, f"{label} type")


def _assert_access_rule_eq(a: AccessRule, b: AccessRule, label: str):
    assert a.scope == b.scope, f"{label}: access scope"
    assert a.verbs == b.verbs, f"{label}: access verbs"


def _assert_content_eq(a: Content, b: Content, label: str):
    assert a.name == b.name, f"{label}: content name"
    assert a.singular == b.singular, f"{label}: content singular"
    assert len(a.fields) == len(b.fields), f"{label}: field count"
    for i, (fa, fb) in enumerate(zip(a.fields, b.fields)):
        _assert_field_eq(fa, fb, f"{label} field[{i}]")
    assert len(a.access_rules) == len(b.access_rules), f"{label}: access rule count"
    for i, (ra, rb) in enumerate(zip(a.access_rules, b.access_rules)):
        _assert_access_rule_eq(ra, rb, f"{label} access[{i}]")


def _assert_transition_eq(a: Transition, b: Transition, label: str):
    assert a.from_state == b.from_state, f"{label}: from_state"
    assert a.to_state == b.to_state, f"{label}: to_state"
    assert a.required_scope == b.required_scope, f"{label}: required_scope"


def _assert_state_machine_eq(a: StateMachine, b: StateMachine, label: str):
    assert a.content_name == b.content_name, f"{label}: content_name"
    assert a.machine_name == b.machine_name, f"{label}: machine_name"
    assert a.singular == b.singular, f"{label}: singular"
    assert a.initial_state == b.initial_state, f"{label}: initial_state"
    assert a.states == b.states, f"{label}: states"
    assert len(a.transitions) == len(b.transitions), f"{label}: transition count"
    for i, (ta, tb) in enumerate(zip(a.transitions, b.transitions)):
        _assert_transition_eq(ta, tb, f"{label} trans[{i}]")


def _assert_event_eq(a: EventRule, b: EventRule, label: str):
    assert a.content_name == b.content_name, f"{label}: content_name"
    assert a.trigger == b.trigger, f"{label}: trigger"
    assert a.jexl_condition == b.jexl_condition, f"{label}: jexl_condition"
    if a.condition and b.condition:
        assert a.condition.field1 == b.condition.field1, f"{label}: condition.field1"
        assert a.condition.operator == b.condition.operator, f"{label}: condition.operator"
        assert a.condition.field2 == b.condition.field2, f"{label}: condition.field2"
    else:
        assert a.condition is None and b.condition is None, f"{label}: condition mismatch"
    if a.action and b.action:
        assert a.action.create_content == b.action.create_content, f"{label}: action.create_content"
        assert a.action.fields == b.action.fields, f"{label}: action.fields"
    else:
        assert (a.action is None) == (b.action is None), f"{label}: action mismatch"


def _assert_directive_eq(a, b, label: str):
    assert type(a) == type(b), f"{label}: directive type {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, ShowPage):
        assert a.page_name == b.page_name, f"{label}: page_name"
    elif isinstance(a, DisplayTable):
        assert a.content_name == b.content_name, f"{label}: content_name"
        assert a.columns == b.columns, f"{label}: columns"
    elif isinstance(a, ShowRelated):
        assert a.singular == b.singular, f"{label}: singular"
        assert a.related_content == b.related_content, f"{label}: related_content"
        assert a.group_by == b.group_by, f"{label}: group_by"
    elif isinstance(a, HighlightRows):
        assert a.field == b.field, f"{label}: field"
        assert a.operator == b.operator, f"{label}: operator"
        assert a.threshold_field == b.threshold_field, f"{label}: threshold_field"
        assert a.jexl_condition == b.jexl_condition, f"{label}: jexl_condition"
    elif isinstance(a, AllowFilter):
        assert a.fields == b.fields, f"{label}: fields"
    elif isinstance(a, AllowSearch):
        assert a.fields == b.fields, f"{label}: fields"
    elif isinstance(a, SubscribeTo):
        assert a.content_name == b.content_name, f"{label}: content_name"
    elif isinstance(a, AcceptInput):
        assert a.fields == b.fields, f"{label}: fields"
    elif isinstance(a, ValidateUnique):
        assert a.field == b.field, f"{label}: field"
        assert a.jexl_condition == b.jexl_condition, f"{label}: jexl_condition"
    elif isinstance(a, CreateAs):
        assert a.initial_state == b.initial_state, f"{label}: initial_state"
    elif isinstance(a, AfterSave):
        assert a.instruction == b.instruction, f"{label}: instruction"
    elif isinstance(a, ShowChart):
        assert a.content_name == b.content_name, f"{label}: content_name"
        assert a.days == b.days, f"{label}: days"
    elif isinstance(a, DisplayText):
        assert a.text == b.text, f"{label}: text"
        assert a.is_expression == b.is_expression, f"{label}: is_expression"
    elif isinstance(a, DisplayAggregation):
        assert a.description == b.description, f"{label}: description"


def _assert_story_eq(a: UserStory, b: UserStory, label: str):
    assert a.role == b.role, f"{label}: role"
    assert a.action == b.action, f"{label}: action"
    assert a.objective == b.objective, f"{label}: objective"
    assert len(a.directives) == len(b.directives), \
        f"{label}: directive count {len(a.directives)} != {len(b.directives)}"
    for i, (da, db) in enumerate(zip(a.directives, b.directives)):
        _assert_directive_eq(da, db, f"{label} directive[{i}]")


def _assert_nav_eq(a: NavBar, b: NavBar, label: str):
    assert len(a.items) == len(b.items), f"{label}: nav item count"
    for i, (ia, ib) in enumerate(zip(a.items, b.items)):
        assert ia.label == ib.label, f"{label} nav[{i}]: label"
        assert ia.page_name == ib.page_name, f"{label} nav[{i}]: page_name"
        assert ia.visible_to == ib.visible_to, f"{label} nav[{i}]: visible_to"
        assert ia.badge == ib.badge, f"{label} nav[{i}]: badge"


def _assert_api_eq(a: ApiSection, b: ApiSection, label: str):
    assert a.base_path == b.base_path, f"{label}: base_path"
    assert len(a.endpoints) == len(b.endpoints), f"{label}: endpoint count"
    for i, (ea, eb) in enumerate(zip(a.endpoints, b.endpoints)):
        assert ea.method == eb.method, f"{label} ep[{i}]: method"
        assert ea.path == eb.path, f"{label} ep[{i}]: path"
        assert ea.description == eb.description, f"{label} ep[{i}]: description"


def _assert_compute_eq(a: ComputeNode, b: ComputeNode, label: str):
    assert a.name == b.name, f"{label}: name"
    assert a.shape == b.shape, f"{label}: shape"
    assert a.inputs == b.inputs, f"{label}: inputs"
    assert a.outputs == b.outputs, f"{label}: outputs"
    assert len(a.input_params) == len(b.input_params), f"{label}: input_params count"
    for i, (pa, pb) in enumerate(zip(a.input_params, b.input_params)):
        assert pa.name == pb.name, f"{label} iparam[{i}]: name"
        assert pa.type_name == pb.type_name, f"{label} iparam[{i}]: type_name"
    assert len(a.output_params) == len(b.output_params), f"{label}: output_params count"
    for i, (pa, pb) in enumerate(zip(a.output_params, b.output_params)):
        assert pa.name == pb.name, f"{label} oparam[{i}]: name"
        assert pa.type_name == pb.type_name, f"{label} oparam[{i}]: type_name"
    assert a.body_lines == b.body_lines, f"{label}: body_lines"
    # chain_steps removed in Phase R1
    assert a.access_scope == b.access_scope, f"{label}: access_scope"
    assert a.access_role == b.access_role, f"{label}: access_role"


def _assert_channel_eq(a: ChannelDecl, b: ChannelDecl, label: str):
    assert a.name == b.name, f"{label}: name"
    assert a.carries == b.carries, f"{label}: carries"
    assert a.protocol == b.protocol, f"{label}: protocol"
    assert a.direction == b.direction, f"{label}: direction"
    assert a.delivery == b.delivery, f"{label}: delivery"
    assert a.source == b.source, f"{label}: source"
    assert a.destination == b.destination, f"{label}: destination"
    assert a.endpoint == b.endpoint, f"{label}: endpoint"
    assert len(a.requirements) == len(b.requirements), f"{label}: requirement count"
    for i, (ra, rb) in enumerate(zip(a.requirements, b.requirements)):
        assert ra.scope == rb.scope, f"{label} req[{i}]: scope"
        assert ra.direction == rb.direction, f"{label} req[{i}]: direction"


def _assert_boundary_eq(a: BoundaryDecl, b: BoundaryDecl, label: str):
    assert a.name == b.name, f"{label}: name"
    assert a.contains == b.contains, f"{label}: contains"
    assert a.identity_mode == b.identity_mode, f"{label}: identity_mode"
    assert a.identity_parent == b.identity_parent, f"{label}: identity_parent"
    assert a.identity_scopes == b.identity_scopes, f"{label}: identity_scopes"


def assert_programs_equivalent(expected: Program, actual: Program, label: str = ""):
    """Assert two Program ASTs are structurally equivalent (ignoring line numbers)."""

    # Application
    if expected.application:
        assert actual.application is not None, f"{label}: missing application"
        _assert_application_eq(expected.application, actual.application, label)
    else:
        assert actual.application is None, f"{label}: unexpected application"

    # Identity
    if expected.identity:
        assert actual.identity is not None, f"{label}: missing identity"
        _assert_identity_eq(expected.identity, actual.identity, label)
    else:
        assert actual.identity is None, f"{label}: unexpected identity"

    # Roles
    assert len(expected.roles) == len(actual.roles), \
        f"{label}: role count {len(expected.roles)} != {len(actual.roles)}"
    for i, (re_, ra) in enumerate(zip(expected.roles, actual.roles)):
        assert re_.name == ra.name, f"{label} role[{i}]: name"
        assert re_.scopes == ra.scopes, f"{label} role[{i}]: scopes"

    # Contents
    assert len(expected.contents) == len(actual.contents), \
        f"{label}: content count {len(expected.contents)} != {len(actual.contents)}"
    for i, (ce, ca) in enumerate(zip(expected.contents, actual.contents)):
        _assert_content_eq(ce, ca, f"{label} content[{i}]")

    # State machines
    assert len(expected.state_machines) == len(actual.state_machines), \
        f"{label}: state machine count"
    for i, (se, sa) in enumerate(zip(expected.state_machines, actual.state_machines)):
        _assert_state_machine_eq(se, sa, f"{label} sm[{i}]")

    # Events
    assert len(expected.events) == len(actual.events), f"{label}: event count"
    for i, (ee, ea) in enumerate(zip(expected.events, actual.events)):
        _assert_event_eq(ee, ea, f"{label} event[{i}]")

    # Stories
    assert len(expected.stories) == len(actual.stories), \
        f"{label}: story count {len(expected.stories)} != {len(actual.stories)}"
    for i, (se, sa) in enumerate(zip(expected.stories, actual.stories)):
        _assert_story_eq(se, sa, f"{label} story[{i}]")

    # Navigation
    if expected.navigation:
        assert actual.navigation is not None, f"{label}: missing navigation"
        _assert_nav_eq(expected.navigation, actual.navigation, label)
    else:
        assert actual.navigation is None, f"{label}: unexpected navigation"

    # API
    if expected.api:
        assert actual.api is not None, f"{label}: missing api"
        _assert_api_eq(expected.api, actual.api, label)
    else:
        assert actual.api is None, f"{label}: unexpected api"

    # Streams
    assert len(expected.streams) == len(actual.streams), f"{label}: stream count"
    for i, (se, sa) in enumerate(zip(expected.streams, actual.streams)):
        assert se.description == sa.description, f"{label} stream[{i}]: description"
        assert se.path == sa.path, f"{label} stream[{i}]: path"

    # Computes
    assert len(expected.computes) == len(actual.computes), \
        f"{label}: compute count {len(expected.computes)} != {len(actual.computes)}"
    for i, (ce, ca) in enumerate(zip(expected.computes, actual.computes)):
        _assert_compute_eq(ce, ca, f"{label} compute[{i}]")

    # Channels
    assert len(expected.channels) == len(actual.channels), f"{label}: channel count"
    for i, (ce, ca) in enumerate(zip(expected.channels, actual.channels)):
        _assert_channel_eq(ce, ca, f"{label} channel[{i}]")

    # Boundaries
    assert len(expected.boundaries) == len(actual.boundaries), f"{label}: boundary count"
    for i, (be, ba) in enumerate(zip(expected.boundaries, actual.boundaries)):
        _assert_boundary_eq(be, ba, f"{label} boundary[{i}]")


# ---------------------------------------------------------------------------
# Parametrized test: parse every example file with both parsers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "example_file",
    EXAMPLE_FILES,
    ids=[f.stem for f in EXAMPLE_FILES],
)
def test_lark_matches_hand_rolled(example_file: Path):
    """parse_lark() produces an equivalent AST to parse() for each example."""
    source = example_file.read_text(encoding="utf-8")

    expected, expected_errors = parse(source)
    actual, actual_errors = parse_lark(source)

    # Both should parse without errors
    assert expected_errors.ok, f"Hand-rolled parser failed on {example_file.name}"
    assert actual_errors.ok, f"Lark parser failed on {example_file.name}: {actual_errors.format()}"

    assert_programs_equivalent(expected, actual, label=example_file.stem)


# ---------------------------------------------------------------------------
# Unit tests: specific constructs
# ---------------------------------------------------------------------------

class TestLarkApplicationAndIdentity:
    def test_application(self):
        prog, err = parse_lark("Application: My App\n  Description: A test")
        assert err.ok
        assert prog.application.name == "My App"
        assert prog.application.description == "A test"

    def test_identity_and_scopes(self):
        source = 'Users authenticate with stub\nScopes are "read", "write", and "admin"'
        prog, err = parse_lark(source)
        assert err.ok
        assert prog.identity.provider == "stub"
        assert prog.identity.scopes == ["read", "write", "admin"]


class TestLarkRoles:
    def test_standard_role(self):
        source = 'A "clerk" has "read" and "write"'
        prog, err = parse_lark(source)
        assert err.ok
        assert len(prog.roles) == 1
        assert prog.roles[0].name == "clerk"
        assert prog.roles[0].scopes == ["read", "write"]

    def test_bare_role(self):
        source = 'Anonymous has "view app"'
        prog, err = parse_lark(source)
        assert err.ok
        assert len(prog.roles) == 1
        assert prog.roles[0].name == "Anonymous"
        assert prog.roles[0].scopes == ["view app"]


class TestLarkContent:
    def test_content_with_fields_and_access(self):
        source = '''Content called "products":
  Each product has a SKU which is unique text, required
  Each product has a cost which is currency
  Each product has a count which is a whole number, minimum 0
  Each product has a status which is one of: active, inactive
  Anyone with "read" can view products'''
        prog, err = parse_lark(source)
        assert err.ok
        c = prog.contents[0]
        assert c.name == "products"
        assert c.singular == "product"
        assert len(c.fields) == 4
        assert c.fields[0].name == "SKU"
        assert c.fields[0].type_expr.unique is True
        assert c.fields[0].type_expr.required is True
        assert c.fields[1].type_expr.base_type == "currency"
        assert c.fields[2].type_expr.base_type == "whole_number"
        assert c.fields[2].type_expr.minimum == 0
        assert c.fields[3].type_expr.base_type == "enum"
        assert c.fields[3].type_expr.enum_values == ["active", "inactive"]
        assert len(c.access_rules) == 1

    def test_reference_field(self):
        source = '''Content called "stock levels":
  Each stock level has a product which references products, required'''
        prog, err = parse_lark(source)
        assert err.ok
        f = prog.contents[0].fields[0]
        assert f.name == "product"
        assert f.type_expr.base_type == "reference"
        assert f.type_expr.references == "products"
        assert f.type_expr.required is True


class TestLarkState:
    def test_state_machine(self):
        source = '''State for products called "product lifecycle":
  A product starts as "draft"
  A product can also be "active" or "discontinued"
  A draft product can become active if the user has "write"
  A discontinued product can become active again if the user has "admin"'''
        prog, err = parse_lark(source)
        assert err.ok
        sm = prog.state_machines[0]
        assert sm.content_name == "products"
        assert sm.machine_name == "product lifecycle"
        assert sm.initial_state == "draft"
        assert sm.states == ["draft", "active", "discontinued"]
        assert len(sm.transitions) == 2
        assert sm.transitions[0].from_state == "draft"
        assert sm.transitions[0].to_state == "active"


class TestLarkEvents:
    def test_v1_event(self):
        source = '''When a stock level is updated and its quantity is at or below its reorder threshold:
  Create a reorder alert with the product, warehouse, current quantity, and threshold'''
        prog, err = parse_lark(source)
        assert err.ok
        ev = prog.events[0]
        assert ev.content_name == "stock level"
        assert ev.trigger == "updated"
        assert ev.condition.field1 == "quantity"
        assert ev.condition.operator == "at or below"
        assert ev.action.create_content == "reorder alert"

    def test_v2_jexl_event(self):
        source = '''When [stockLevel.updated && stockLevel.quantity <= stockLevel.reorderThreshold]:
  Create a "reorder alert" with the product, warehouse, current quantity, and threshold'''
        prog, err = parse_lark(source)
        assert err.ok
        ev = prog.events[0]
        assert ev.trigger == "jexl"
        assert ev.jexl_condition == "stockLevel.updated && stockLevel.quantity <= stockLevel.reorderThreshold"


class TestLarkStories:
    def test_story_with_inline_so_that(self):
        source = '''As anonymous, I want to see a page "Hello" so that I can be greeted:
  Display text "Hello, World"'''
        prog, err = parse_lark(source)
        assert err.ok
        s = prog.stories[0]
        assert s.role == "anonymous"
        assert "page" in s.action
        assert s.objective == "I can be greeted"
        # Should have ShowPage + DisplayText
        assert any(isinstance(d, ShowPage) for d in s.directives)
        assert any(isinstance(d, DisplayText) for d in s.directives)

    def test_story_with_separate_so_that(self):
        source = '''As a warehouse clerk, I want to see all products
  so that I know what we have:
    Show a page called "Dashboard"
    Display a table of products with columns: SKU, name'''
        prog, err = parse_lark(source)
        assert err.ok
        s = prog.stories[0]
        assert s.role == "warehouse clerk"
        assert s.objective == "I know what we have"


class TestLarkCompute:
    def test_compute_with_typed_params(self):
        source = '''Compute called "SayHelloTo":
  Transform: takes u : UserProfile, produces "greeting" : Text
  [greeting = "Hello, " + u.FirstName + "!"]
  "LoggedInUser" can execute this'''
        prog, err = parse_lark(source)
        assert err.ok
        c = prog.computes[0]
        assert c.name == "SayHelloTo"
        assert c.shape == "transform"
        assert len(c.input_params) == 1
        assert c.input_params[0].name == "u"
        assert c.input_params[0].type_name == "UserProfile"
        assert len(c.output_params) == 1
        assert c.output_params[0].name == "greeting"
        assert c.output_params[0].type_name == "Text"
        assert c.access_role == "LoggedInUser"
        assert len(c.body_lines) == 1

    def test_compute_route(self):
        source = '''Compute called "triage order":
  Route: takes an order, produces one of orders or reports
  [order.priority == "high" ? "orders" : "reports"]
  Anyone with "write orders" can execute this'''
        prog, err = parse_lark(source)
        assert err.ok
        c = prog.computes[0]
        assert c.shape == "route"
        assert c.outputs == ["orders", "reports"]

    def test_compute_bare_role_v1(self):
        source = '''Compute called "SayHelloTo":
  Transform: takes u : UserProfile, produces "greeting" : Text
  greeting = "Hello, " + u.FirstName + "!"
  LoggedInUser can execute this'''
        prog, err = parse_lark(source)
        assert err.ok
        c = prog.computes[0]
        assert c.access_role == "LoggedInUser"


class TestLarkChannels:
    def test_channel(self):
        source = '''Channel called "order webhook":
  Carries orders
  Protocol: webhook
  From external to application
  Endpoint: /webhooks/orders
  Requires "write orders" to send'''
        prog, err = parse_lark(source)
        assert err.ok
        ch = prog.channels[0]
        assert ch.name == "order webhook"
        assert ch.carries == "orders"
        assert ch.protocol == "webhook"
        assert ch.source == "external"
        assert ch.destination == "application"
        assert ch.endpoint == "/webhooks/orders"
        assert len(ch.requirements) == 1
        assert ch.requirements[0].scope == "write orders"
        assert ch.requirements[0].direction == "send"


class TestLarkBoundaries:
    def test_boundary(self):
        source = '''Boundary called "order processing":
  Contains orders, order lines, and reports
  Identity inherits from application'''
        prog, err = parse_lark(source)
        assert err.ok
        b = prog.boundaries[0]
        assert b.name == "order processing"
        assert b.contains == ["orders", "order lines", "reports"]
        assert b.identity_mode == "inherit"
        assert b.identity_parent == "application"

    def test_boundary_restrict(self):
        source = '''Boundary called "order reporting":
  Contains reports
  Identity restricts to "read orders"'''
        prog, err = parse_lark(source)
        assert err.ok
        b = prog.boundaries[0]
        assert b.identity_mode == "restrict"
        assert b.identity_scopes == ["read orders"]


class TestLarkNavigation:
    def test_nav_bar(self):
        source = '''Navigation bar:
  "Dashboard" links to "Home Page" visible to all
  "Admin" links to "Admin Page" visible to manager, badge: pending count'''
        prog, err = parse_lark(source)
        assert err.ok
        nav = prog.navigation
        assert len(nav.items) == 2
        assert nav.items[0].label == "Dashboard"
        assert nav.items[0].page_name == "Home Page"
        assert nav.items[0].visible_to == ["all"]
        assert nav.items[1].badge == "pending count"


class TestLarkAPI:
    def test_api_section(self):
        source = '''Expose a REST API at /api/v1:
  GET    /products           lists products
  POST   /products           creates a product'''
        prog, err = parse_lark(source)
        assert err.ok
        api = prog.api
        assert api.base_path == "/api/v1"
        assert len(api.endpoints) == 2
        assert api.endpoints[0].method == "GET"
        assert api.endpoints[0].path == "/products"


class TestLarkStream:
    def test_stream(self):
        source = "Stream stock updates at /api/v1/stream"
        prog, err = parse_lark(source)
        assert err.ok
        assert len(prog.streams) == 1
        assert prog.streams[0].description == "stock updates"
        assert prog.streams[0].path == "/api/v1/stream"


class TestLarkComments:
    def test_parenthesis_comment_stripped(self):
        source = '''Application: Hello
(This is a comment.)
Users authenticate with stub (comment here)'''
        prog, err = parse_lark(source)
        assert err.ok
        assert prog.application.name == "Hello"
        assert prog.identity.provider == "stub"

    def test_section_dividers_stripped(self):
        source = '''Application: Hello
--- Identity ---
Users authenticate with stub'''
        prog, err = parse_lark(source)
        assert err.ok
        assert prog.identity.provider == "stub"
