# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""AST node definitions for the Termin DSL.

All nodes are Python dataclasses with a `line` field for error reporting.
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple


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
    cascade_mode: Optional[str] = None  # v0.9: "cascade" | "restrict" | None.
                                        # Only meaningful when base_type=="reference".
                                        # Required by analyzer for every reference field.
    _cascade_modes_seen: tuple = ()   # v0.9: every cascade-mode token
                                       # detected on this field (in source order).
                                       # The analyzer reads this to emit S041
                                       # when both "cascade on delete" and
                                       # "restrict on delete" appear.
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
    verbs: list[str]  # "view", "create", "update", "delete", "create or update", "append"
    # v0.9 Phase 6a.3: `their own <content>` row-filter qualifier per
    # BRD #3 §3.4. When true, the access rule applies only to rows the
    # invoking principal owns (per the content's `is owned by` declaration).
    their_own: bool = False
    # v0.9.2 Slice L10: the noun the author wrote after `their own`.
    # Preserved verbatim (lowercase, whitespace-trimmed) so the analyzer
    # can distinguish singular from plural forms — required for the
    # multi-row ownership extension (§15.3): on non-unique ownership,
    # `their own <singular>` is TERMIN-S057. None when `their_own=False`.
    their_own_noun: Optional[str] = None
    line: int = 0
    # v0.9.2 L3: when verbs == ["append"], append_field names the
    # specific conversation field this grant targets. None for the
    # content-level CRUD verbs (view/create/update/delete/audit).
    append_field: Optional[str] = None


@dataclass
class AppendAction:
    """v0.9.2 L3: source-level `Append to <record>.<field> as "<kind>" with body <expr>` action.

    Usable in compute bodies, page form-submit handlers, and When-rule
    action lists (L8). Carries the parsed pieces verbatim; the runtime
    materializes the entry shape (id + created_at + appended_by_principal_id
    are auto-generated).
    """
    record: str             # content singular or plural reference (e.g., "chat_threads")
    field: str              # snake_case field name on that content
    kind: str               # canonical kind: "user", "assistant", "tool_call", etc.
    body_expr: str          # CEL expression string (backtick-stripped)
    # Free-form metadata tail captured verbatim — handler decomposes
    # `, source: \`...\`, tool_call_id: \`...\`` etc. when wired up by
    # downstream slices (L7 auto-write-back, L8 When-rule actions).
    metadata_tail: str = ""
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
    # v0.9 Phase 6a.2: ownership declarations. Each entry is the field name
    # (display form, as it appears in source) named on an `Each X is owned
    # by <field>` line. Multi-entry case is a TERMIN-S051 analyzer error
    # per BRD #3 §3.3 ("at most one ownership field per content type");
    # tracked as a list so the analyzer sees the duplication.
    owned_by_declarations: list[str] = field(default_factory=list)
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
    # v0.9: analyzer bookkeeping. Set during assembly so the analyzer can
    # detect multiple `<field> starts as <state>` lines in a single sub-block
    # (a semantic error — exactly one initial state per machine).
    starts_as_count: int = 0
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
    # v0.9.2 L8 (tech-design §13.2): When-rule bodies may carry a
    # heterogeneous sequence of actions — `Create a X with ...`,
    # `Send X to "..."`, `Append to X.Y as "kind" with body \`...\``,
    # etc. — that execute sequentially in source order.
    # Each entry is either an EventAction (for create/send) or an
    # AppendAction (for append). The legacy `action` field stays
    # populated with the first non-append EventAction for callers
    # that still walk the single-action path; the lowering pass mirrors
    # the same convention into EventSpec.
    actions: list = field(default_factory=list)
    line: int = 0


# --- User Story Directives ---

@dataclass
class Directive:
    line: int = 0


@dataclass
class ShowPage(Directive):
    page_name: str = ""


@dataclass
class ChatDirective(Directive):
    source: str = ""            # Content name to display as chat
    role_field: str = "role"    # field name for message role
    content_field: str = "content"  # field name for message body
    # v0.9.2 L9 (tech design §14.1): when set, the chat binds to a
    # `conversation` field on a content type via the dot-notation form
    # `Show a chat for <content>.<field>`. The tuple is
    # ``(content_singular_or_plural, field_name)`` carried through from
    # the source spelling; lower() resolves the content half to the
    # canonical snake_case content name. None for the legacy messages-
    # collection binding (`Show a chat for <messages>` and
    # `Show a chat for <messages> with role "X", content "Y"`) which
    # continues to work per §14.5.
    conversation_field: Optional[Tuple[str, str]] = None


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
class UsingOverride(Directive):
    """v0.9 Phase 5b.1: presentation contract override sub-clause.

    `Using "<namespace>.<contract>"` overrides the implicit
    `presentation-base.<X>` contract for the immediately preceding
    rendering directive. Per BRD #2 §4.3 this is the sole source-
    level construct that names contracts outside `presentation-base`.

    `target` carries the literal `<namespace>.<contract>` string;
    the analyzer parses it apart for validation and the lowerer
    attaches it to the parent ComponentNode's `contract` field.
    """
    target: str = ""


@dataclass
class AllowFilter(Directive):
    fields: list[str] = field(default_factory=list)


@dataclass
class AllowSearch(Directive):
    fields: list[str] = field(default_factory=list)


@dataclass
class AllowInlineEdit(Directive):
    """'Allow inline editing of <field>, <field>, ...' — opt-in to
    click-to-edit cells for the listed fields on the data_table of the
    current page. Requires the content's `can update` rule; state-machine
    columns are not permitted (use transition buttons or the Edit modal).
    """
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
class PackageContractCall(Directive):
    """v0.9 Phase 5c.2: an instance of a contract-package source-verb.

    Created when the parser matches a line like
    `Show a cosmic orb of scenarios` against a registered
    contract-package source-verb template. The matched bindings
    flow through to lowering as ComponentNode.props.

    Fields:
      qualified_name: the fully-qualified contract name, e.g.
        "airlock-components.cosmic-orb".
      source_verb: the verb template that matched, retained for
        diagnostics and round-tripping.
      bindings: placeholder name → matched bareword token. e.g.
        {"state-ref": "scenarios"}.
    """
    qualified_name: str = ""
    source_verb: str = ""
    bindings: dict = field(default_factory=dict)


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
    """Row-level action button inside a 'For each X, show actions:' block.

    Two kinds:
      - kind="transition": 'Label' transitions to 'state' if available [...]
        target_state is the destination state.
      - kind="delete":     'Label' deletes if available [...]
        target_state is unused (empty string). Required scope comes from
        the content's `can delete` access rule; enforced by the lowering
        pass and at the route layer.

    unavailable_behavior controls what happens when the action is not
    permitted for the current row/user: "hide" removes the button, "disable"
    renders it in a disabled state.
    """
    label: str = ""
    target_state: str = ""
    machine_name: str = ""  # v0.9: field name driving the transition
    unavailable_behavior: str = "disable"  # "disable" or "hide"
    kind: str = "transition"  # "transition" or "delete"


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
    # v0.9 Phase 6c (BRD #3 §6): non-inline Directive/Objective sourcing.
    # When set, the inline `directive`/`objective` field is empty and
    # the runtime resolves text from the source — deploy_config at app
    # startup, or the triggering record at each invocation.
    # Shape: {"kind": "deploy_config", "key": <str>}
    #     or {"kind": "field", "content": <str>, "field": <str>}.
    directive_source: Optional[dict] = None
    objective_source: Optional[dict] = None
    trigger: Optional[str] = None                 # "schedule <interval>" or "event <name>"
    trigger_where: Optional[str] = None           # CEL expression for trigger filtering
    # v0.9.2 L6 (tech design §10): `Conversation is <content>.<field>` —
    # the parent content's conversation-typed field that the runtime
    # materializes as native LLM context and auto-appends to on response.
    # Carried as (content_singular_or_plural, field_name) preserving the
    # source spelling; `lower()` normalizes the content reference to the
    # canonical snake_case content name. Mutually exclusive with `Accesses`
    # of the same content (analyzer TERMIN-S057); the trigger event must
    # name `<content>.<field>.appended` (analyzer TERMIN-S058).
    conversation_source: Optional[tuple[str, str]] = None
    accesses: list[str] = field(default_factory=list)        # content types this Compute can touch
    # v0.9 Phase 3 slice (c): full access-grant grammar.
    # Reads grants content.{query,read} only — no writes, no state.
    # Sends to grants channel.{send,invoke_action} for the named channels.
    # Emits grants event.emit for the named events only.
    # Invokes grants compute.invoke for the named computes only.
    reads: list[str] = field(default_factory=list)           # content types this Compute can read but not write
    sends_to: list[str] = field(default_factory=list)        # channel names this Compute can send to
    emits: list[str] = field(default_factory=list)           # event names this Compute can emit
    invokes: list[str] = field(default_factory=list)         # compute names this Compute can invoke
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
    provider_contract: Optional[str] = None  # v0.9 Phase 4: "webhook"|"email"|"messaging"|"event-stream"
    failure_mode: str = "log-and-drop"       # v0.9 Phase 4: runtime failure handling
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
