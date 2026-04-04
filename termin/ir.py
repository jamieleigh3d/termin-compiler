"""Intermediate Representation for the Termin compiler.

The IR (AppSpec) sits between the analyzer and backends. It is fully resolved:
all name resolution, cross-referencing, and inference happens in the lowering
pass. Backends read pre-resolved, immutable data.

All types are frozen dataclasses with tuples (not lists) for immutability.
"""

import re
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
    primitive_type: str = "content"             # "content", "channel", "boundary", "compute"


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


# ── Presentation v2: Component Tree ──

@dataclass(frozen=True)
class PropValue:
    """A prop value that may be a literal string or a JEXL expression."""
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


def page_entry_to_pagespec(entry: 'PageEntry') -> PageSpec:
    """Convert a PageEntry component tree back to a flat PageSpec for legacy backends."""
    display_content = None
    table_columns = []
    filters = []
    search_fields = []
    highlight = None
    subscribe_stream = None
    related = None
    form_fields = []
    form_target = None
    create_as = None
    validate_unique = None
    after_save = None
    aggregations = []
    chart = None
    scope = entry.required_scope
    static_texts = []
    static_expressions = []

    def _walk(children, parent_type=None):
        nonlocal display_content, highlight, subscribe_stream, related
        nonlocal form_target, create_as, validate_unique, after_save, chart, scope

        for node in children:
            t = node.type
            p = node.props

            if t == "text":
                content = p.get("content", "")
                if isinstance(content, dict) and content.get("is_expr"):
                    static_expressions.append(content["value"])
                elif isinstance(content, PropValue) and content.is_expr:
                    static_expressions.append(content.value)
                else:
                    val = content.value if isinstance(content, PropValue) else content
                    static_texts.append(val)

            elif t == "data_table":
                display_content = p.get("source")
                for col in p.get("columns", []):
                    table_columns.append(TableColumn(
                        display=col.get("label", col.get("field", "")),
                        key=col.get("field", ""),
                    ))
                _walk(node.children, parent_type="data_table")

            elif t == "filter":
                filters.append(FilterField(
                    key=p.get("field", ""),
                    display=p.get("field", ""),
                    filter_type={"enum": "enum", "state": "status", "distinct": "distinct",
                                 "reference": "text"}.get(p.get("mode", "text"), "text"),
                    options=tuple(p.get("options", [])),
                ))

            elif t == "search":
                search_fields.extend(p.get("fields", []))

            elif t == "highlight":
                cond = p.get("condition")
                if isinstance(cond, dict) and cond.get("is_expr"):
                    highlight = HighlightRule(field="", operator="jexl", threshold_field=cond["value"])

            elif t == "subscribe":
                subscribe_stream = p.get("content")

            elif t == "related":
                related = RelatedDataSpec(
                    related_content=p.get("content", ""),
                    join_column=p.get("join", ""),
                    display_columns=(),
                )

            elif t == "form":
                form_target = p.get("target")
                create_as = p.get("create_as")
                after_save = p.get("after_save")
                if p.get("submit_scope"):
                    scope = p["submit_scope"]
                _walk(node.children, parent_type="form")

            elif t == "field_input":
                form_fields.append(FormField(
                    key=p.get("field", ""),
                    display=p.get("label", p.get("field", "")),
                    input_type=p.get("input_type", "text"),
                    required=p.get("required", False),
                    minimum=p.get("minimum"),
                    step=p.get("step"),
                    enum_values=tuple(p.get("enum_values", [])),
                    reference_content=p.get("reference_content"),
                    reference_display_col=p.get("reference_display_col"),
                    reference_unique_col=p.get("reference_unique_col"),
                ))

            elif t == "aggregation":
                agg_key = re.sub(r'[^a-z0-9]+', '_', p.get("label", "agg").lower()).strip('_')[:30]
                # Map component agg_type to legacy backend agg_type
                at = p.get("agg_type", "count")
                legacy_agg = {"sum": "sum_join", "average": "sum_join", "minimum": "sum_join",
                              "maximum": "sum_join"}.get(at, at)
                expr = None
                if isinstance(p.get("expression"), dict):
                    expr = p["expression"].get("value")
                aggregations.append(AggregationSpec(
                    key=agg_key,
                    description=p.get("label", ""),
                    agg_type=legacy_agg,
                    content_ref=p.get("source", ""),
                    sum_expression=expr,
                ))

            elif t == "stat_breakdown":
                agg_key = re.sub(r'[^a-z0-9]+', '_', p.get("label", "breakdown").lower()).strip('_')[:30]
                aggregations.append(AggregationSpec(
                    key=agg_key,
                    description=p.get("label", ""),
                    agg_type="count_by_status",
                    content_ref=p.get("source", ""),
                ))

            elif t == "chart":
                chart = ChartSpec(
                    content_ref=p.get("source", ""),
                    days=p.get("period_days", 30),
                    chart_type=p.get("chart_type", "line"),
                )

            elif t == "section":
                _walk(node.children, parent_type=parent_type)

    _walk(entry.children)

    return PageSpec(
        name=entry.name,
        slug=entry.slug,
        role=entry.role,
        display_content=display_content,
        table_columns=tuple(table_columns),
        filters=tuple(filters),
        search_fields=tuple(search_fields),
        highlight=highlight,
        subscribe_stream=subscribe_stream,
        related=related,
        form_fields=tuple(form_fields),
        form_target_content=form_target,
        create_as_status=create_as,
        validate_unique_field=validate_unique,
        after_save_instruction=after_save,
        aggregations=tuple(aggregations),
        chart=chart,
        required_scope=scope,
        static_texts=tuple(static_texts),
        static_expressions=tuple(static_expressions),
    )


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
    source_type: str = ""              # "content", "channel", "compute", "boundary", or ""
    boundary: Optional[str] = None     # which boundary this handler belongs to
    condition_jexl: Optional[str] = None
    actions: tuple['ErrorActionSpec', ...] = ()
    is_catch_all: bool = False


# ── Top-Level IR ──

@dataclass(frozen=True)
class AppSpec:
    """The complete intermediate representation of a Termin application."""
    ir_version: str = "0.2.0"
    reflection_enabled: bool = True
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
