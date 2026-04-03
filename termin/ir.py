"""Intermediate Representation for the Termin compiler.

The IR (AppSpec) sits between the analyzer and backends. It is fully resolved:
all name resolution, cross-referencing, and inference happens in the lowering
pass. Backends read pre-resolved, immutable data.

All types are frozen dataclasses with tuples (not lists) for immutability.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


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
    column_type: FieldType
    required: bool = False
    unique: bool = False
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    enum_values: tuple[str, ...] = ()  # non-empty for enum columns
    foreign_key: Optional[str] = None  # target table snake name
    is_auto: bool = False              # automatic timestamp
    list_type: Optional[str] = None    # inner type for JSON list columns


# Backward-compatible alias
Column = FieldSpec


@dataclass(frozen=True)
class ContentSchema:
    name: QualifiedName
    fields: tuple[FieldSpec, ...]
    storage_intent: str = "auto"
    has_state_machine: bool = False
    initial_state: Optional[str] = None


# Backward-compatible alias
Table = ContentSchema


# ── Access Control ──

class Verb(Enum):
    VIEW = "view"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


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
class TransitionSpec:
    from_state: str
    to_state: str
    required_scope: str


@dataclass(frozen=True)
class StateMachineSpec:
    content_ref: str                           # snake_case table name
    machine_name: str
    initial_state: str
    states: tuple[str, ...]
    transitions: tuple[TransitionSpec, ...]


# ── Events ──

@dataclass(frozen=True)
class EventConditionSpec:
    left_column: str       # snake_case
    operator: str          # "lte", "gte", "eq"
    right_column: str      # snake_case


@dataclass(frozen=True)
class EventActionSpec:
    target_content: str                                  # resolved snake_case
    column_mapping: tuple[tuple[str, str], ...]        # (target_col, source_col) pairs


@dataclass(frozen=True)
class EventSpec:
    source_content: str                     # resolved snake_case
    trigger: str                          # "created", "updated", "deleted", "jexl"
    condition: Optional[EventConditionSpec] = None
    action: Optional[EventActionSpec] = None
    jexl_condition: Optional[str] = None  # v2: JEXL expression for trigger
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


# ── Pages / UI ──

@dataclass(frozen=True)
class TableColumn:
    display: str    # "SKU"
    key: str        # "sku"


@dataclass(frozen=True)
class FilterField:
    key: str                        # snake_case column name
    display: str                    # display label
    filter_type: str                # "text", "enum", "status", "distinct"
    options: tuple[str, ...] = ()   # for enum/status filters


@dataclass(frozen=True)
class FormField:
    key: str                        # snake_case column name
    display: str                    # display label
    input_type: str                 # "text", "number", "currency", "enum", "reference"
    required: bool = False
    minimum: Optional[int] = None
    step: Optional[str] = None      # e.g. "0.01" for currency
    enum_values: tuple[str, ...] = ()
    reference_content: Optional[str] = None       # resolved snake_case
    reference_display_col: Optional[str] = None
    reference_unique_col: Optional[str] = None


@dataclass(frozen=True)
class HighlightRule:
    field: str              # snake_case
    operator: str           # "lte"
    threshold_field: str    # snake_case


@dataclass(frozen=True)
class RelatedDataSpec:
    related_content: str       # snake_case
    join_column: str         # column in related table referencing this table
    display_columns: tuple[str, ...]


@dataclass(frozen=True)
class AggregationSpec:
    key: str                # slug for template variable
    description: str        # display text
    agg_type: str           # "count", "count_by_status", "sum_join"
    content_ref: str              # target table snake_case
    join_content: Optional[str] = None
    join_column: Optional[str] = None
    sum_expression: Optional[str] = None


@dataclass(frozen=True)
class ChartSpec:
    content_ref: str              # snake_case
    days: int = 30
    chart_type: str = "line"


@dataclass(frozen=True)
class PageSpec:
    name: str                                       # "Inventory Dashboard"
    slug: str                                       # "inventory_dashboard"
    role: str                                       # which role this story belongs to
    display_content: Optional[str] = None             # table snake_case
    table_columns: tuple[TableColumn, ...] = ()
    filters: tuple[FilterField, ...] = ()
    search_fields: tuple[str, ...] = ()             # snake_case column names
    highlight: Optional[HighlightRule] = None
    subscribe_stream: Optional[str] = None          # content name for SSE
    related: Optional[RelatedDataSpec] = None
    form_fields: tuple[FormField, ...] = ()         # empty = no form
    form_target_content: Optional[str] = None
    create_as_status: Optional[str] = None
    validate_unique_field: Optional[str] = None
    after_save_instruction: Optional[str] = None
    aggregations: tuple[AggregationSpec, ...] = ()
    chart: Optional[ChartSpec] = None
    required_scope: Optional[str] = None            # scope for form POST
    static_texts: tuple[str, ...] = ()              # plain text content blocks
    static_expressions: tuple[str, ...] = ()        # compute expression calls


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
    direction: str  # "send" or "receive"


@dataclass(frozen=True)
class ChannelSpec:
    name: QualifiedName
    carries_content: str                               # resolved snake_case table name
    direction: ChannelDirection = ChannelDirection.INBOUND
    delivery: ChannelDelivery = ChannelDelivery.AUTO
    endpoint: Optional[str] = None
    requirements: tuple[ChannelRequirementSpec, ...] = ()


# ── Boundaries ──

@dataclass(frozen=True)
class BoundaryPropertySpec:
    name: str
    type_name: str
    jexl_expr: str


@dataclass(frozen=True)
class BoundarySpec:
    name: QualifiedName
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
    jexl_expr: Optional[str] = None
    log_level: Optional[str] = None


@dataclass(frozen=True)
class ErrorHandlerSpec:
    source: str  # primitive name, or "" for catch-all
    condition_jexl: Optional[str] = None
    actions: tuple['ErrorActionSpec', ...] = ()
    is_catch_all: bool = False


# ── Top-Level IR ──

@dataclass(frozen=True)
class AppSpec:
    """The complete intermediate representation of a Termin application."""
    ir_version: str = "0.2.0"
    name: str = ""
    description: str = ""
    auth: AuthSpec = None
    content: tuple[ContentSchema, ...] = ()
    access_grants: tuple[AccessGrant, ...] = ()
    state_machines: tuple[StateMachineSpec, ...] = ()
    events: tuple[EventSpec, ...] = ()
    routes: tuple[RouteSpec, ...] = ()
    pages: tuple[PageSpec, ...] = ()
    nav_items: tuple[NavItemSpec, ...] = ()
    streams: tuple[StreamSpec, ...] = ()
    computes: tuple[ComputeSpec, ...] = ()
    channels: tuple[ChannelSpec, ...] = ()
    boundaries: tuple[BoundarySpec, ...] = ()
    error_handlers: tuple[ErrorHandlerSpec, ...] = ()
