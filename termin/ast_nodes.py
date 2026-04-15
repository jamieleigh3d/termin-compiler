"""AST node definitions for the Termin DSL.

All nodes are Python dataclasses with a `line` field for error reporting.
"""

from dataclasses import dataclass, field
from typing import Optional


# --- Type Expressions ---

@dataclass
class TypeExpr:
    base_type: str  # "text", "whole_number", "number", "currency", "percentage",
                    # "boolean", "date", "datetime", "enum", "reference", "automatic", "list"
    required: bool = False
    unique: bool = False
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    enum_values: list[str] = field(default_factory=list)
    one_of_values: list = field(default_factory=list)  # D-19: is one of constraint (numbers or strings)
    references: Optional[str] = None  # name of referenced Content
    list_type: Optional[str] = None   # inner type for "list of <type>"
    default_expr: Optional[str] = None  # CEL expression or literal string for default value
    default_is_expr: bool = False       # True = CEL from [brackets]; False = literal from "quotes"
    confidentiality_scopes: list[str] = field(default_factory=list)  # scopes required to see this field
    line: int = 0


# --- Content ---

@dataclass
class Field:
    name: str
    type_expr: TypeExpr
    line: int = 0


@dataclass
class AccessRule:
    scope: str
    verbs: list[str]  # "view", "create", "update", "delete", "create or update"
    line: int = 0


@dataclass
class DependentValue:
    """A When clause or unconditional constraint on a field within a Content block."""
    when_expr: Optional[str]        # CEL condition, or None for unconditional
    field: str                      # field name
    constraint: str                 # "one_of", "equals", or "default"
    values: list = field(default_factory=list)  # list of allowed values (strings or numbers)
    line: int = 0


@dataclass
class Content:
    name: str
    singular: str
    fields: list[Field] = field(default_factory=list)
    access_rules: list[AccessRule] = field(default_factory=list)
    dependent_values: list[DependentValue] = field(default_factory=list)
    confidentiality_scopes: list[str] = field(default_factory=list)  # content-level scopes
    audit: str = "actions"  # "actions" (default, safe), "debug" (full values), or "none"
    line: int = 0


# --- Identity ---

@dataclass
class Identity:
    provider: str  # "stub", or a custom auth provider
    scopes: list[str] = field(default_factory=list)
    line: int = 0


@dataclass
class Role:
    name: str
    scopes: list[str] = field(default_factory=list)
    line: int = 0


@dataclass
class RoleAlias:
    short_name: str
    full_name: str
    line: int = 0


# --- State ---

@dataclass
class TransitionFeedback:
    trigger: str             # "success" or "error"
    style: str               # "toast" or "banner"
    message: str             # literal string or CEL expression
    is_expr: bool = False    # True if message is a CEL expression (backtick-delimited)
    dismiss_seconds: Optional[int] = None  # auto-dismiss timer; None = use type default
    line: int = 0


@dataclass
class Transition:
    from_state: str
    to_state: str
    required_scope: str
    feedback: list = field(default_factory=list)  # list[TransitionFeedback]
    line: int = 0


@dataclass
class StateMachine:
    content_name: str
    machine_name: str
    singular: str
    initial_state: str
    states: list[str] = field(default_factory=list)  # all states including initial
    transitions: list[Transition] = field(default_factory=list)
    line: int = 0


# --- Events ---

@dataclass
class EventCondition:
    field1: str
    operator: str  # "at_or_below"
    field2: str
    line: int = 0


@dataclass
class EventAction:
    create_content: str = ""     # Content name to create
    fields: list[str] = field(default_factory=list)  # field names to copy
    send_content: str = ""       # Content to send via Channel
    send_channel: str = ""       # Channel name to send to
    line: int = 0


@dataclass
class EventRule:
    content_name: str
    trigger: str  # "created", "updated", "deleted"
    condition: Optional[EventCondition] = None
    action: Optional[EventAction] = None
    condition_expr: Optional[str] = None  # v2: When [expr]:
    log_level: Optional[str] = None  # v2: Log level: WARN
    line: int = 0


# --- User Story Directives ---

@dataclass
class Directive:
    line: int = 0


@dataclass
class ShowPage(Directive):
    page_name: str = ""


@dataclass
class DisplayTable(Directive):
    content_name: str = ""
    columns: list[str] = field(default_factory=list)


@dataclass
class ShowRelated(Directive):
    content_name: str = ""
    singular: str = ""
    related_content: str = ""
    group_by: str = ""


@dataclass
class HighlightRows(Directive):
    field: str = ""
    operator: str = ""
    threshold_field: str = ""
    condition_expr: Optional[str] = None  # v2: Highlight rows where [expr]


@dataclass
class MarkAs(Directive):
    """Semantic emphasis: Mark rows/fields where [expr] as "label"."""
    condition_expr: str = ""
    label: str = ""
    scope: str = "row"  # "row" or field name


@dataclass
class AllowFilter(Directive):
    fields: list[str] = field(default_factory=list)


@dataclass
class AllowSearch(Directive):
    fields: list[str] = field(default_factory=list)


@dataclass
class LinkColumn(Directive):
    column: str = ""
    link_template: str = ""

@dataclass
class SubscribeTo(Directive):
    content_name: str = ""


@dataclass
class AcceptInput(Directive):
    fields: list[str] = field(default_factory=list)


@dataclass
class ValidateUnique(Directive):
    field: str = ""
    condition_expr: Optional[str] = None  # v2: Validate that [expr] before saving


@dataclass
class CreateAs(Directive):
    initial_state: str = ""


@dataclass
class AfterSave(Directive):
    instruction: str = ""


@dataclass
class ShowChart(Directive):
    content_name: str = ""
    days: int = 30


@dataclass
class DisplayText(Directive):
    text: str = ""
    is_expression: bool = False  # True when text is a compute call, not a string literal


@dataclass
class DisplayAggregation(Directive):
    description: str = ""


@dataclass
class StructuredAggregation(Directive):
    """Structured aggregation: Display count/sum/average/min/max of ..."""
    agg_type: str = ""            # "count", "sum", "average", "minimum", "maximum"
    source_content: str = ""      # content name
    expression: Optional[str] = None  # CEL expression (for sum/avg/min/max)
    group_by: Optional[str] = None    # field name (for count ... grouped by)
    format: str = "number"        # "number", "currency", etc.


@dataclass
class SectionStart(Directive):
    """Section "Title": — groups subsequent directives into a section."""
    title: str = ""


@dataclass
class ActionHeader(Directive):
    """For each X, show actions: — introduces action button definitions."""
    singular: str = ""


@dataclass
class ActionButtonDef(Directive):
    """'Label' transitions to 'state' if available [, hide/disable otherwise]"""
    label: str = ""
    target_state: str = ""
    unavailable_behavior: str = "disable"  # "disable" or "hide"


@dataclass
class UserStory:
    role: str
    action: str
    objective: str
    directives: list[Directive] = field(default_factory=list)
    line: int = 0


# --- Navigation ---

@dataclass
class NavItem:
    label: str
    page_name: str
    visible_to: list[str] = field(default_factory=list)  # role names or ["all"]
    badge: Optional[str] = None  # expression like "open alert count"
    line: int = 0


@dataclass
class NavBar:
    items: list[NavItem] = field(default_factory=list)
    line: int = 0


# --- Streams ---

@dataclass
class Stream:
    description: str
    path: str
    line: int = 0


# --- Compute ---

@dataclass
class ComputeParam:
    name: str
    type_name: str
    line: int = 0


@dataclass
class ComputeNode:
    name: str
    shape: str = ""  # "transform", "reduce", "expand", "correlate", "route"
    inputs: list[str] = field(default_factory=list)   # content names
    outputs: list[str] = field(default_factory=list)   # content names
    input_params: list[ComputeParam] = field(default_factory=list)   # typed params
    output_params: list[ComputeParam] = field(default_factory=list)  # typed params
    body_lines: list[str] = field(default_factory=list)
    access_scope: Optional[str] = None
    access_role: Optional[str] = None  # alternative: role name instead of scope
    identity_mode: str = "delegate"              # "delegate" or "service"
    required_confidentiality_scopes: list[str] = field(default_factory=list)
    output_confidentiality: Optional[str] = None  # explicit reclassification
    provider: Optional[str] = None                # "cel" (default), "llm", "ai-agent"
    preconditions: list[str] = field(default_factory=list)   # CEL expressions
    postconditions: list[str] = field(default_factory=list)  # CEL expressions
    directive: Optional[str] = None               # system prompt (strong prior)
    objective: Optional[str] = None               # task prompt (what to accomplish)
    strategy: Optional[str] = None                # execution plan (legacy, folded into objective)
    trigger: Optional[str] = None                 # "schedule <interval>" or "event <name>"
    trigger_where: Optional[str] = None           # CEL expression for trigger filtering
    accesses: list[str] = field(default_factory=list)        # content types this Compute can touch
    input_fields: list[tuple[str, str]] = field(default_factory=list)   # (content_ref, field_name)
    output_fields: list[tuple[str, str]] = field(default_factory=list)  # (content_ref, field_name)
    output_creates: Optional[str] = None          # content type for "Output creates X"
    audit_scope: Optional[str] = None             # scope for "can audit" (D-20)
    audit_level: str = "actions"                  # "none", "actions" (default), "debug" (D-20)
    line: int = 0


# --- Channel ---

@dataclass
class ChannelRequirement:
    scope: str
    direction: str  # "send", "receive", or "invoke"
    line: int = 0


@dataclass
class ActionParam:
    name: str
    type_name: str  # "text", "number", "yes or no", content name, etc.
    line: int = 0


@dataclass
class ActionDecl:
    name: str
    takes: list[ActionParam] = field(default_factory=list)
    returns: list[ActionParam] = field(default_factory=list)
    required_scopes: list[str] = field(default_factory=list)
    line: int = 0


@dataclass
class ChannelDecl:
    name: str
    carries: str = ""           # Content name
    direction: str = ""         # "inbound", "outbound", "bidirectional", "internal"
    delivery: str = ""          # "realtime", "reliable", "batch", "auto"
    endpoint: Optional[str] = None
    requirements: list[ChannelRequirement] = field(default_factory=list)
    actions: list[ActionDecl] = field(default_factory=list)
    line: int = 0


# --- Boundary ---

@dataclass
class BoundaryProperty:
    name: str
    type_name: str
    expr: str
    line: int = 0


@dataclass
class BoundaryDecl:
    name: str
    contains: list[str] = field(default_factory=list)  # content or boundary names
    identity_mode: str = "inherit"  # "inherit" or "restrict"
    identity_parent: Optional[str] = None
    identity_scopes: list[str] = field(default_factory=list)  # for restrict mode
    properties: list[BoundaryProperty] = field(default_factory=list)
    line: int = 0


# --- Error Handling ---

@dataclass
class ErrorAction:
    kind: str  # "retry", "disable", "escalate", "create", "notify", "set"
    retry_count: int = 0
    retry_backoff: bool = False
    retry_max_delay: Optional[str] = None
    target: Optional[str] = None  # for disable/notify
    expr: Optional[str] = None  # for create/notify/set
    log_level: Optional[str] = None
    line: int = 0


@dataclass
class ErrorHandler:
    source: str  # primitive name, or "" for catch-all
    condition_expr: Optional[str] = None  # where [expr]
    actions: list[ErrorAction] = field(default_factory=list)
    is_catch_all: bool = False
    line: int = 0


# --- Application ---

@dataclass
class Application:
    name: str
    description: str = ""
    app_id: Optional[str] = None  # UUID, compiler-managed
    line: int = 0


# --- Top-level Program ---

@dataclass
class Program:
    application: Optional[Application] = None
    identity: Optional[Identity] = None
    roles: list[Role] = field(default_factory=list)
    role_aliases: list[RoleAlias] = field(default_factory=list)
    contents: list[Content] = field(default_factory=list)
    state_machines: list[StateMachine] = field(default_factory=list)
    events: list[EventRule] = field(default_factory=list)
    stories: list[UserStory] = field(default_factory=list)
    navigation: Optional[NavBar] = None
    streams: list[Stream] = field(default_factory=list)
    computes: list[ComputeNode] = field(default_factory=list)
    channels: list[ChannelDecl] = field(default_factory=list)
    boundaries: list[BoundaryDecl] = field(default_factory=list)
    error_handlers: list[ErrorHandler] = field(default_factory=list)
