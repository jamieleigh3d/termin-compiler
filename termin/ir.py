# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Intermediate Representation for the Termin compiler.

The IR (AppSpec) sits between the analyzer and backends. It is fully resolved:
all name resolution, cross-referencing, and inference happens in the lowering
pass. Backends read pre-resolved, immutable data.

All types are frozen dataclasses with tuples (not lists) for immutability.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional


# ── Naming ──

@dataclass(frozen=True)
class QualifiedName:
    """A resolved identifier with display, snake_case, and PascalCase forms."""
    display: str   # "stock levels"
    snake: str     # "stock_levels"
    pascal: str    # "StockLevels"


# ── Field / Schema ──

class FieldType(Enum):
    TEXT = auto()
    INTEGER = auto()
    REAL = auto()
    BOOLEAN = auto()
    DATE = auto()
    TIMESTAMP = auto()
    JSON = auto()       # for list types


# Backward-compatible alias
ColumnType = FieldType


@dataclass(frozen=True)
class FieldSpec:
    name: str                          # snake_case
    display_name: str                  # original: "unit cost"
    business_type: str = "text"        # semantic type from DSL: "text", "currency", "number", etc.
    column_type: FieldType = FieldType.TEXT  # storage type for backend SQL mapping
    required: bool = False
    unique: bool = False
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    enum_values: tuple[str, ...] = ()  # non-empty for enum columns
    one_of_values: tuple = ()          # D-19: is one of constraint values (numbers or strings)
    foreign_key: Optional[str] = None  # target table snake name
    cascade_mode: Optional[str] = None # v0.9: "cascade" | "restrict" | None.
                                       # Required when foreign_key is set; None otherwise.
                                       # Drives ON DELETE clause in storage SQL.
    is_auto: bool = False              # automatic timestamp
    list_type: Optional[str] = None    # inner type for JSON list columns
    default_expr: Optional[str] = None # CEL expression or literal string for default value
    confidentiality_scopes: tuple[str, ...] = ()  # scopes required to see this field (AND)


# Backward-compatible alias
Column = FieldSpec


@dataclass(frozen=True)
class DependentValueSpec:
    """A conditional or unconditional field constraint in the IR."""
    when: Optional[str]           # CEL expression, or None for unconditional
    field: str                    # snake_case field name
    constraint: str               # "one_of", "equals", or "default"
    values: tuple = ()            # tuple of allowed values (for one_of)
    value: Any = None             # single value (for equals/default)


@dataclass(frozen=True)
class ContentSchema:
    name: QualifiedName
    fields: tuple[FieldSpec, ...]
    singular: str = ""                               # e.g. "echo" for Content "echoes"
    storage_intent: str = "auto"
    # v0.9: multi-state-machine support. Each entry is
    # {"machine_name": snake_case, "initial": state_name}. Replaces the
    # v0.8 has_state_machine/initial_state pair, which could only hold a
    # single machine per content.
    state_machines: tuple[dict, ...] = ()
    confidentiality_scopes: tuple[str, ...] = ()  # content-level scopes (inherited by fields)
    audit: str = "actions"                          # "actions" (default), "debug", or "none"
    dependent_values: tuple['DependentValueSpec', ...] = ()  # D-19: conditional field constraints


# Backward-compatible alias
Table = ContentSchema


# ── Access Control ──

class Verb(Enum):
    VIEW = "view"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    AUDIT = "audit"


@dataclass(frozen=True)
class AccessGrant:
    content: str              # snake_case table name
    scope: str              # scope string
    verbs: frozenset[Verb]


# ── Auth ──

@dataclass(frozen=True)
class RoleSpec:
    name: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class AuthSpec:
    provider: str                  # "stub", or a custom auth provider
    scopes: tuple[str, ...]
    roles: tuple[RoleSpec, ...]


# ── State Machines ──

@dataclass(frozen=True)
class TransitionFeedbackSpec:
    trigger: str                # "success" or "error"
    style: str                  # "toast" or "banner"
    message: str                # literal string or CEL expression
    is_expr: bool = False       # True if message is a CEL expression
    dismiss_seconds: Optional[int] = None  # auto-dismiss timer; None = type default


@dataclass(frozen=True)
class TransitionSpec:
    from_state: str
    to_state: str
    required_scope: str
    feedback: tuple = ()        # tuple[TransitionFeedbackSpec, ...]


@dataclass(frozen=True)
class StateMachineSpec:
    content_ref: str                           # snake_case table name
    machine_name: str
    initial_state: str
    states: tuple[str, ...]
    transitions: tuple[TransitionSpec, ...]
    primitive_type: str = "content"             # "content", "channel", "boundary", "compute"


# ── Events ──

@dataclass(frozen=True)
class EventConditionSpec:
    left_column: str       # snake_case
    operator: str          # "lte", "gte", "eq"
    right_column: str      # snake_case


@dataclass(frozen=True)
class EventActionSpec:
    target_content: str = ""                             # resolved snake_case (for create actions)
    column_mapping: tuple[tuple[str, str], ...] = ()   # (target_col, source_col) pairs
    send_content: str = ""                               # content to send (for channel send actions)
    send_channel: str = ""                               # channel name (for channel send actions)


@dataclass(frozen=True)
class EventSpec:
    source_content: str                     # resolved snake_case
    trigger: str                          # "created", "updated", "deleted", "expr"
    condition: Optional[EventConditionSpec] = None
    action: Optional[EventActionSpec] = None
    condition_expr: Optional[str] = None  # v2: CEL expression for trigger
    log_level: str = "INFO"               # v2: TRACE, DEBUG, INFO, WARN, ERROR


# ── API Routes ──

class HttpMethod(Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"


class RouteKind(Enum):
    LIST = auto()
    GET_ONE = auto()
    CREATE = auto()
    UPDATE = auto()
    DELETE = auto()
    TRANSITION = auto()
    STREAM = auto()


@dataclass(frozen=True)
class RouteSpec:
    method: HttpMethod
    path: str                           # e.g. "/api/v1/products"
    kind: RouteKind
    content_ref: str                          # snake_case target table
    required_scope: Optional[str] = None
    lookup_column: str = "id"           # column for {param} routes
    target_state: Optional[str] = None  # for TRANSITION routes
    machine_name: Optional[str] = None  # snake_case state-machine column
                                        # name; for TRANSITION routes only.
                                        # Multi-SM content may have several
                                        # transition routes that share a
                                        # target_state across machines, so
                                        # the runtime needs both fields to
                                        # disambiguate.


# ── Pages / UI ──


# ── Presentation v2: Component Tree ──

@dataclass(frozen=True)
class PropValue:
    """A prop value that may be a literal string or a CEL expression."""
    value: str
    is_expr: bool = False


@dataclass
class ComponentNode:
    """A composable UI component in the Presentation tree.

    Not frozen because props/style/layout are dicts. Constructed once in
    lowering, serialized to JSON, never mutated after construction.
    """
    type: str                                      # "text", "data_table", "form", etc.
    props: dict = field(default_factory=dict)       # key → value or PropValue
    style: dict = field(default_factory=dict)       # CSS-like visual properties
    layout: dict = field(default_factory=dict)      # visual editor canvas state
    children: tuple = ()                            # tuple of ComponentNode


@dataclass(frozen=True)
class PageEntry:
    """A page in the component tree IR — replaces the flat PageSpec."""
    name: str
    slug: str
    role: str
    required_scope: Optional[str] = None
    children: tuple = ()   # tuple of ComponentNode



# ── Navigation ──

@dataclass(frozen=True)
class NavItemSpec:
    label: str
    page_slug: str
    visible_to: tuple[str, ...]        # role names or ("all",)
    badge_content: Optional[str] = None  # table to COUNT(*) for badge


# ── Streams ──

@dataclass(frozen=True)
class StreamSpec:
    description: str
    path: str


# ── Compute ──

class ComputeShape(Enum):
    NONE = auto()       # No shape — LLM/agent Computes with field wiring
    TRANSFORM = auto()
    REDUCE = auto()
    EXPAND = auto()
    CORRELATE = auto()
    ROUTE = auto()


@dataclass(frozen=True)
class ComputeParamSpec:
    name: str
    type_name: str


@dataclass(frozen=True)
class FieldDependency:
    """A resolved field access in a Compute body, with confidentiality metadata."""
    content_name: str
    field_name: str
    confidentiality_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReclassificationPoint:
    """Records an explicit confidentiality scope change for audit."""
    compute_name: str
    input_scopes: tuple[str, ...]
    output_scope: str


@dataclass(frozen=True)
class ComputeSpec:
    name: QualifiedName
    shape: ComputeShape
    input_content: tuple[str, ...]    # resolved snake_case table names
    output_content: tuple[str, ...]   # resolved snake_case table names
    body_lines: tuple[str, ...] = ()
    required_scope: Optional[str] = None
    required_role: Optional[str] = None   # alternative to scope
    input_params: tuple[ComputeParamSpec, ...] = ()
    output_params: tuple[ComputeParamSpec, ...] = ()
    client_safe: bool = False             # compiler-inferred: safe to evaluate on client
    identity_mode: str = "delegate"       # "delegate" or "service"
    required_confidentiality_scopes: tuple[str, ...] = ()  # confidential field scopes accessed
    output_confidentiality_scope: Optional[str] = None     # explicit reclassification
    field_dependencies: tuple[FieldDependency, ...] = ()   # compiler-resolved
    provider: Optional[str] = None                         # "cel" (default), "llm", "ai-agent"
    preconditions: tuple[str, ...] = ()                    # CEL expressions checked before execution
    postconditions: tuple[str, ...] = ()                   # CEL expressions checked after execution
    directive: Optional[str] = None                        # system prompt (strong prior)
    objective: Optional[str] = None                        # task prompt (what to accomplish)
    strategy: Optional[str] = None                         # legacy: execution plan (folded into objective)
    trigger: Optional[str] = None                          # "schedule <interval>" or "event <name>"
    trigger_where: Optional[str] = None                    # CEL expression for trigger filtering
    accesses: tuple[str, ...] = ()                         # content types this Compute can touch
    # v0.9 Phase 3 slice (c): full source-level access grant grammar.
    # Together with accesses, these populate the agent's ToolSurface
    # at runtime. Reads grants read-only access (no state, no writes);
    # sends_to grants channel send/invoke; emits grants event.emit;
    # invokes grants compute.invoke.
    reads: tuple[str, ...] = ()                            # content types this Compute can read but not write
    sends_to: tuple[str, ...] = ()                         # channel names this Compute can send to
    emits: tuple[str, ...] = ()                            # event names this Compute can emit
    invokes: tuple[str, ...] = ()                          # compute names this Compute can invoke
    input_fields: tuple[tuple[str, str], ...] = ()         # (content_ref, field_name) pairs
    output_fields: tuple[tuple[str, str], ...] = ()        # (content_ref, field_name) pairs
    output_creates: Optional[str] = None                   # content type for "Output creates X"
    audit_level: str = "actions"                              # "none", "actions" (default), "debug" (D-20)
    audit_scope: Optional[str] = None                         # scope for "can audit" access (D-20)
    audit_content_ref: Optional[str] = None                   # snake_name of auto-generated audit log Content (D-20)


# ── Channels ──

class ChannelDirection(Enum):
    INBOUND = auto()
    OUTBOUND = auto()
    BIDIRECTIONAL = auto()
    INTERNAL = auto()


class ChannelDelivery(Enum):
    REALTIME = auto()
    RELIABLE = auto()
    BATCH = auto()
    AUTO = auto()


@dataclass(frozen=True)
class ChannelRequirementSpec:
    scope: str
    direction: str  # "send", "receive", or "invoke"


@dataclass(frozen=True)
class ChannelActionParamSpec:
    name: str
    param_type: str  # "text", "number", "yes or no", content name


@dataclass(frozen=True)
class ChannelActionSpec:
    name: QualifiedName
    takes: tuple[ChannelActionParamSpec, ...] = ()
    returns: tuple[ChannelActionParamSpec, ...] = ()
    required_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChannelSpec:
    name: QualifiedName
    carries_content: str = ""                          # resolved snake_case table name (data Channels)
    direction: ChannelDirection = ChannelDirection.INBOUND
    delivery: ChannelDelivery = ChannelDelivery.AUTO
    endpoint: Optional[str] = None
    requirements: tuple[ChannelRequirementSpec, ...] = ()
    actions: tuple[ChannelActionSpec, ...] = ()        # typed RPC verbs (action Channels)


# ── Boundaries ──

@dataclass(frozen=True)
class BoundaryPropertySpec:
    name: str
    type_name: str
    expr: str


@dataclass(frozen=True)
class BoundarySpec:
    name: QualifiedName
    boundary_type: str = "application"               # "application", "library", "module", "configuration"
    contains_content: tuple[str, ...] = ()            # resolved snake_case table names
    contains_boundaries: tuple[str, ...] = ()        # snake_case boundary names
    identity_mode: str = "inherit"                   # "inherit" or "restrict"
    identity_scopes: tuple[str, ...] = ()            # for restrict mode
    properties: tuple[BoundaryPropertySpec, ...] = ()  # exposed computed properties


# ── Error Handling ──

@dataclass(frozen=True)
class ErrorActionSpec:
    kind: str  # "retry", "disable", "escalate", "create", "notify", "set"
    retry_count: int = 0
    retry_backoff: bool = False
    retry_max_delay: Optional[str] = None
    target: Optional[str] = None
    expr: Optional[str] = None
    log_level: Optional[str] = None


@dataclass(frozen=True)
class ErrorHandlerSpec:
    source: str  # primitive name, or "" for catch-all
    source_type: str = ""              # "content", "channel", "compute", "boundary", or ""
    boundary: Optional[str] = None     # which boundary this handler belongs to
    condition_expr: Optional[str] = None
    actions: tuple['ErrorActionSpec', ...] = ()
    is_catch_all: bool = False


# ── Top-Level IR ──

@dataclass(frozen=True)
class AppSpec:
    """The complete intermediate representation of a Termin application."""
    ir_version: str = "0.9.0"
    reflection_enabled: bool = True
    app_id: Optional[str] = None    # UUID, compiler-managed, source of truth for deployment identity
    name: str = ""
    description: str = ""
    auth: AuthSpec = None
    content: tuple[ContentSchema, ...] = ()
    access_grants: tuple[AccessGrant, ...] = ()
    state_machines: tuple[StateMachineSpec, ...] = ()
    events: tuple[EventSpec, ...] = ()
    routes: tuple[RouteSpec, ...] = ()
    pages: tuple[PageEntry, ...] = ()
    nav_items: tuple[NavItemSpec, ...] = ()
    streams: tuple[StreamSpec, ...] = ()
    computes: tuple[ComputeSpec, ...] = ()
    channels: tuple[ChannelSpec, ...] = ()
    boundaries: tuple[BoundarySpec, ...] = ()
    error_handlers: tuple[ErrorHandlerSpec, ...] = ()
    reclassification_points: tuple[ReclassificationPoint, ...] = ()
