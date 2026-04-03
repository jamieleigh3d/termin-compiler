"""Lowering pass: AST (Program) -> IR (AppSpec).

Resolves all names, cross-references, and inference. After lowering,
backends receive fully resolved, immutable data — no inference needed.
"""

import re
from typing import Optional

from .ast_nodes import (
    Program, Content, Field, TypeExpr, AccessRule, StateMachine,
    Transition, EventRule, UserStory, Directive, ShowPage, DisplayTable,
    ShowRelated, HighlightRows, AllowFilter, AllowSearch, SubscribeTo,
    AcceptInput, ValidateUnique, CreateAs, AfterSave, ShowChart,
    DisplayAggregation, DisplayText, ComputeNode, ChannelDecl, BoundaryDecl,
    BoundaryProperty, ErrorHandler, ErrorAction,
)
from .ir import (
    QualifiedName, FieldType, FieldSpec, ContentSchema, Verb, AccessGrant,
    RoleSpec, AuthSpec, TransitionSpec, StateMachineSpec,
    EventConditionSpec, EventActionSpec, EventSpec,
    HttpMethod, RouteKind, RouteSpec,
    TableColumn, FilterField, FormField, HighlightRule, RelatedDataSpec,
    AggregationSpec, ChartSpec, PageSpec, NavItemSpec, StreamSpec, AppSpec,
    ComputeShape, ComputeSpec, ComputeParamSpec, ChannelDirection,
    ChannelDelivery, ChannelRequirementSpec, ChannelSpec, BoundarySpec,
    BoundaryPropertySpec, ErrorHandlerSpec, ErrorActionSpec,
)


# ── Naming helpers ──

def _snake(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def _pascal(name: str) -> str:
    return ''.join(w.capitalize() for w in re.split(r'[^a-zA-Z0-9]+', name) if w)


def _qname(display: str) -> QualifiedName:
    return QualifiedName(display=display, snake=_snake(display), pascal=_pascal(display))


# ── Type mapping ──

def _field_type(te: TypeExpr) -> FieldType:
    if te.base_type in ("text", "enum"):
        return FieldType.TEXT
    if te.base_type in ("whole_number", "reference"):
        return FieldType.INTEGER
    if te.base_type in ("currency", "number", "percentage"):
        return FieldType.REAL
    if te.base_type == "boolean":
        return FieldType.INTEGER  # SQLite: 0/1
    if te.base_type == "date":
        return FieldType.TEXT     # SQLite: ISO date string
    if te.base_type == "datetime":
        return FieldType.TIMESTAMP
    if te.base_type == "automatic":
        return FieldType.TIMESTAMP
    if te.base_type == "list":
        return FieldType.JSON
    return FieldType.TEXT


# ── Scope resolution ──

def _scope_for_verb(access_rules: list[AccessRule], verb: str) -> Optional[str]:
    for rule in access_rules:
        if verb in rule.verbs or "create or update" in rule.verbs and verb in ("create", "update"):
            return rule.scope
    return None


# ── Content inference from path ──

def _infer_content_for_path(path: str, contents: list[Content]) -> Optional[Content]:
    parts = path.strip("/").split("/")
    segment = parts[0].replace("-", " ") if parts else ""
    for c in contents:
        if _snake(c.name) == _snake(segment):
            return c
        if segment in c.name or c.name.endswith(segment):
            return c
    return None


# ── Verb-to-state mapping ──

VERB_STATE_MAP = {
    "activate": "active",
    "discontinue": "discontinued",
    "acknowledge": "acknowledged",
    "close": "closed",
    "resolve": "resolved",
    "reopen": "in progress",
    "start": "in progress",
    "wait": "waiting on customer",
    "complete": "done",
    "cancel": "cancelled",
    "approve": "approved",
    "reject": "rejected",
    "archive": "archived",
    "suspend": "suspended",
    "resume": "active",
    "plan": "in sprint",
    "review": "in review",
    "rework": "in progress",
}


def _resolve_target_state(action: str, sm: StateMachine, ep_description: str = "") -> str:
    """Map an API action word to an actual state name."""
    if action in [s for s in sm.states]:
        return action
    if action in VERB_STATE_MAP and VERB_STATE_MAP[action] in sm.states:
        return VERB_STATE_MAP[action]
    # Fuzzy: state starts with action
    for state in sm.states:
        if state.startswith(action) or action.startswith(state.split()[0]):
            return state
    # Match from endpoint description
    if ep_description:
        desc_lower = ep_description.lower()
        for state in sm.states:
            if state in desc_lower:
                return state
    return action


# ── Best-match content for form fields ──

def _best_content_for_fields(field_names: list[str], contents: list[Content]) -> Optional[Content]:
    best, best_count = None, 0
    for c in contents:
        content_cols = {_snake(f.name) for f in c.fields}
        count = sum(1 for f in field_names if _snake(f) in content_cols)
        if count > best_count:
            best_count = count
            best = c
    return best


# ── Guess content from aggregation description text ──

def _guess_content(text: str, contents: list[Content]) -> Optional[Content]:
    text_lower = text.lower()
    for c in contents:
        if c.name.lower() in text_lower or c.singular.lower() in text_lower:
            return c
    return None


# ── Reference field display resolution ──

def _resolve_ref_display(ref_content: Content) -> tuple[str, Optional[str]]:
    """Find the best display column and unique column for a referenced content."""
    display_col = "id"
    unique_col = None
    for f in ref_content.fields:
        if f.type_expr.unique and f.type_expr.base_type == "text":
            unique_col = _snake(f.name)
        if _snake(f.name) == "name":
            display_col = "name"
    if not unique_col and display_col == "id":
        for f in ref_content.fields:
            if f.type_expr.base_type == "text":
                display_col = _snake(f.name)
                break
    return display_col, unique_col


# ═══════════════════════════════��════════════════════════════
# Main lowering function
# ════════════════════════════════════════════════��═══════════

def lower(program: Program) -> AppSpec:
    """Lower a validated Program AST into an AppSpec IR."""

    # ── Build lookup tables ──
    content_by_name: dict[str, Content] = {c.name: c for c in program.contents}
    # Also map singular -> content for event resolution ("stock level" -> "stock levels")
    content_by_singular: dict[str, Content] = {c.singular: c for c in program.contents}
    access_map: dict[str, list[AccessRule]] = {}
    for c in program.contents:
        access_map[c.name] = list(c.access_rules)

    sm_by_content: dict[str, StateMachine] = {}
    for sm in program.state_machines:
        sm_by_content[sm.content_name] = sm

    # ── Lower content schemas ──
    content_schemas = []
    for c in program.contents:
        fields = []
        for f in c.fields:
            fields.append(FieldSpec(
                name=_snake(f.name),
                display_name=f.name,
                column_type=_field_type(f.type_expr),
                required=f.type_expr.required,
                unique=f.type_expr.unique,
                minimum=f.type_expr.minimum,
                maximum=f.type_expr.maximum,
                enum_values=tuple(f.type_expr.enum_values),
                foreign_key=_snake(f.type_expr.references) if f.type_expr.references else None,
                is_auto=f.type_expr.base_type == "automatic",
                list_type=f.type_expr.list_type,
            ))
        has_sm = c.name in sm_by_content
        content_schemas.append(ContentSchema(
            name=_qname(c.name),
            fields=tuple(fields),
            has_state_machine=has_sm,
            initial_state=sm_by_content[c.name].initial_state if has_sm else None,
        ))

    # ── Lower auth ──
    auth = AuthSpec(
        provider=program.identity.provider if program.identity else "stub",
        scopes=tuple(program.identity.scopes if program.identity else []),
        roles=tuple(RoleSpec(name=r.name, scopes=tuple(r.scopes)) for r in program.roles),
    )

    # ── Lower access grants ──
    grants = []
    for c in program.contents:
        for rule in c.access_rules:
            verbs = set()
            for v in rule.verbs:
                if v == "create or update":
                    verbs.add(Verb.CREATE)
                    verbs.add(Verb.UPDATE)
                elif v == "view":
                    verbs.add(Verb.VIEW)
                elif v == "create":
                    verbs.add(Verb.CREATE)
                elif v == "update":
                    verbs.add(Verb.UPDATE)
                elif v == "delete":
                    verbs.add(Verb.DELETE)
            grants.append(AccessGrant(
                content=_snake(c.name),
                scope=rule.scope,
                verbs=frozenset(verbs),
            ))

    # ── Lower state machines ──
    state_machines = []
    for sm in program.state_machines:
        state_machines.append(StateMachineSpec(
            content_ref=_snake(sm.content_name),
            machine_name=sm.machine_name,
            initial_state=sm.initial_state,
            states=tuple(sm.states),
            transitions=tuple(
                TransitionSpec(
                    from_state=t.from_state,
                    to_state=t.to_state,
                    required_scope=t.required_scope,
                ) for t in sm.transitions
            ),
        ))

    # ── Lower events ──
    events = []
    for ev in program.events:
        # Resolve content name (event may use singular: "stock level" -> "stock levels")
        resolved_content = content_by_name.get(ev.content_name)
        if not resolved_content:
            resolved_content = content_by_singular.get(ev.content_name)

        # For JEXL events, try to infer source content from expression prefix
        # e.g., "stockLevel.updated" -> content "stock levels"
        if not resolved_content and ev.jexl_condition:
            prefix = ev.jexl_condition.split(".")[0].strip()
            # Convert camelCase to snake_case: "stockLevel" -> "stock_level"
            import re as _re
            camel_to_snake = _re.sub(r'([a-z])([A-Z])', r'\1_\2', prefix).lower()
            for c in program.contents:
                c_snake = _snake(c.name)
                c_singular_snake = _snake(c.singular)
                if (c_snake == camel_to_snake or c_singular_snake == camel_to_snake
                        or c_snake == camel_to_snake + "s"
                        or c_snake.startswith(camel_to_snake)):
                    resolved_content = c
                    break

        source_content = _snake(resolved_content.name) if resolved_content else _snake(ev.content_name)

        cond = None
        if ev.condition:
            cond = EventConditionSpec(
                left_column=_snake(ev.condition.field1),
                operator="lte" if "below" in ev.condition.operator else ev.condition.operator,
                right_column=_snake(ev.condition.field2),
            )
        action = None
        if ev.action:
            target_content_obj = content_by_name.get(ev.action.create_content)
            if not target_content_obj:
                target_content_obj = content_by_singular.get(ev.action.create_content)
            source_content_obj = resolved_content
            mapping = []
            if target_content_obj and source_content_obj:
                source_cols = {_snake(f.name) for f in source_content_obj.fields}
                for target_field_name in ev.action.fields:
                    tcol = _snake(target_field_name)
                    # Direct match in source
                    if tcol in source_cols:
                        mapping.append((tcol, tcol))
                    elif tcol == "current_quantity":
                        mapping.append((tcol, "quantity"))
                    elif tcol == "threshold":
                        mapping.append((tcol, "reorder_threshold"))
                    else:
                        mapping.append((tcol, tcol))

            action = EventActionSpec(
                target_content=_snake(target_content_obj.name) if target_content_obj else _snake(ev.action.create_content),
                column_mapping=tuple(mapping),
            )
        events.append(EventSpec(
            source_content=source_content,
            trigger=ev.trigger,
            condition=cond,
            action=action,
            jexl_condition=ev.jexl_condition,
            log_level=ev.log_level or "INFO",
        ))

    # ── Lower API routes ──
    routes = []
    if program.api:
        for ep in program.api.endpoints:
            path = program.api.base_path.rstrip("/") + ep.path
            content = _infer_content_for_path(ep.path, program.contents)
            content_ref = _snake(content.name) if content else ""
            method = HttpMethod(ep.method)

            # Determine route kind
            has_param = "{" in ep.path
            is_transition = False
            kind = RouteKind.LIST

            if content and content.name in sm_by_content:
                # Check if this is a transition endpoint
                action = ep.path.rstrip("/").split("/")[-1]
                if not action.startswith("{") and has_param and method == HttpMethod.POST:
                    is_transition = True

            if is_transition:
                kind = RouteKind.TRANSITION
            elif method == HttpMethod.GET and not has_param:
                kind = RouteKind.LIST
            elif method == HttpMethod.GET and has_param:
                kind = RouteKind.GET_ONE
            elif method == HttpMethod.POST and not has_param:
                kind = RouteKind.CREATE
            elif method == HttpMethod.PUT:
                kind = RouteKind.UPDATE
            elif method == HttpMethod.DELETE:
                kind = RouteKind.DELETE

            # Resolve scope
            scope = None
            if content:
                if kind == RouteKind.LIST or kind == RouteKind.GET_ONE:
                    scope = _scope_for_verb(content.access_rules, "view")
                elif kind == RouteKind.CREATE:
                    scope = _scope_for_verb(content.access_rules, "create")
                elif kind == RouteKind.UPDATE:
                    scope = _scope_for_verb(content.access_rules, "update")
                elif kind == RouteKind.DELETE:
                    scope = _scope_for_verb(content.access_rules, "delete")

            # Resolve lookup column
            lookup_col = "id"
            if content and has_param:
                for f in content.fields:
                    if f.type_expr.unique:
                        lookup_col = _snake(f.name)
                        break

            # Resolve target state for transitions
            target_state = None
            if is_transition and content and content.name in sm_by_content:
                action = ep.path.rstrip("/").split("/")[-1]
                sm = sm_by_content[content.name]
                target_state = _resolve_target_state(action, sm, ep.description)

            routes.append(RouteSpec(
                method=method,
                path=path,
                kind=kind,
                content_ref=content_ref,
                required_scope=scope,
                lookup_column=lookup_col,
                target_state=target_state,
            ))

    # ── Lower pages ──
    pages = []
    for story in program.stories:
        page_name = ""
        display_content_name = None
        table_columns = []
        filters = []
        search_fields = []
        highlight = None
        subscribe = None
        related = None
        form_fields = []
        form_target_content = None
        create_as = None
        validate_unique = None
        after_save = None
        aggregations = []
        chart = None
        static_texts = []
        static_expressions = []

        for d in story.directives:
            if isinstance(d, ShowPage):
                page_name = d.page_name
            elif isinstance(d, DisplayTable):
                dt_content = content_by_name.get(d.content_name)
                if dt_content:
                    display_content_name = _snake(d.content_name)
                    table_columns = [TableColumn(display=col, key=_snake(col)) for col in d.columns]
            elif isinstance(d, AllowFilter):
                if display_content_name and d.fields:
                    dt_content = None
                    for c in program.contents:
                        if _snake(c.name) == display_content_name:
                            dt_content = c
                            break
                    for fname in d.fields:
                        fkey = _snake(fname)
                        ft = "text"
                        opts = ()
                        if fkey == "status" and dt_content and dt_content.name in sm_by_content:
                            ft = "status"
                            opts = tuple(sm_by_content[dt_content.name].states)
                        elif dt_content:
                            for cf in dt_content.fields:
                                if _snake(cf.name) == fkey and cf.type_expr.base_type == "enum":
                                    ft = "enum"
                                    opts = tuple(cf.type_expr.enum_values)
                                    break
                        if ft == "text" and fkey == "warehouse":
                            ft = "distinct"
                        filters.append(FilterField(key=fkey, display=fname, filter_type=ft, options=opts))
            elif isinstance(d, AllowSearch):
                search_fields = tuple(_snake(f) for f in d.fields)
            elif isinstance(d, HighlightRows):
                highlight = HighlightRule(
                    field=_snake(d.field),
                    operator="lte",
                    threshold_field=_snake(d.threshold_field),
                )
            elif isinstance(d, SubscribeTo):
                subscribe = d.content_name
            elif isinstance(d, ShowRelated):
                related = RelatedDataSpec(
                    related_content=_snake(d.related_content),
                    join_column=_snake(d.singular) if d.singular else "product",
                    display_columns=(_snake(d.group_by),) if d.group_by else (),
                )
            elif isinstance(d, AcceptInput):
                target_content = _best_content_for_fields(d.fields, program.contents)
                if target_content:
                    form_target_content = _snake(target_content.name)
                    for fname in d.fields:
                        col = _snake(fname)
                        field_obj = next((f for f in target_content.fields if _snake(f.name) == col), None)
                        ff = FormField(key=col, display=fname, input_type="text")
                        if field_obj:
                            if field_obj.type_expr.base_type == "currency":
                                ff = FormField(key=col, display=fname, input_type="currency",
                                               required=field_obj.type_expr.required, step="0.01")
                            elif field_obj.type_expr.base_type == "whole_number":
                                ff = FormField(key=col, display=fname, input_type="number",
                                               required=field_obj.type_expr.required,
                                               minimum=field_obj.type_expr.minimum)
                            elif field_obj.type_expr.base_type == "enum":
                                ff = FormField(key=col, display=fname, input_type="enum",
                                               required=field_obj.type_expr.required,
                                               enum_values=tuple(field_obj.type_expr.enum_values))
                            elif field_obj.type_expr.references:
                                ref_content = content_by_name.get(field_obj.type_expr.references)
                                ref_display, ref_unique = ("id", None)
                                if ref_content:
                                    ref_display, ref_unique = _resolve_ref_display(ref_content)
                                ff = FormField(key=col, display=fname, input_type="reference",
                                               required=field_obj.type_expr.required,
                                               reference_content=_snake(field_obj.type_expr.references),
                                               reference_display_col=ref_display,
                                               reference_unique_col=ref_unique)
                            else:
                                ff = FormField(key=col, display=fname, input_type="text",
                                               required=field_obj.type_expr.required)
                        form_fields.append(ff)
            elif isinstance(d, ValidateUnique):
                validate_unique = _snake(d.field)
            elif isinstance(d, CreateAs):
                # Only set create_as if the target content has a state machine
                if form_target_content:
                    for c in program.contents:
                        if _snake(c.name) == form_target_content and c.name in sm_by_content:
                            create_as = d.initial_state
                            break
            elif isinstance(d, AfterSave):
                after_save = d.instruction
            elif isinstance(d, DisplayAggregation):
                slug = _snake(d.description)[:30]
                desc_lower = d.description.lower()
                gc = _guess_content(d.description, program.contents)
                agg_type = "count"
                if gc:
                    tbl = _snake(gc.name)
                    if "count" in desc_lower:
                        if "breakdown" in desc_lower or "active" in desc_lower:
                            agg_type = "count_by_status"
                        else:
                            agg_type = "count"
                    elif "sum" in desc_lower or "value" in desc_lower:
                        agg_type = "sum_join"
                    aggregations.append(AggregationSpec(
                        key=slug, description=d.description, agg_type=agg_type, content_ref=tbl,
                    ))
            elif isinstance(d, DisplayText):
                if d.is_expression:
                    static_expressions.append(d.text)
                else:
                    static_texts.append(d.text)
            elif isinstance(d, ShowChart):
                chart = ChartSpec(content_ref=_snake(d.content_name), days=d.days)

        # Resolve required scope for form POST
        req_scope = None
        if form_target_content:
            for c in program.contents:
                if _snake(c.name) == form_target_content:
                    req_scope = _scope_for_verb(c.access_rules, "create")
                    break

        if page_name:
            pages.append(PageSpec(
                name=page_name,
                slug=_snake(page_name),
                role=story.role,
                display_content=display_content_name,
                table_columns=tuple(table_columns),
                filters=tuple(filters),
                search_fields=tuple(search_fields) if search_fields else (),
                highlight=highlight,
                subscribe_stream=subscribe,
                related=related,
                form_fields=tuple(form_fields),
                form_target_content=form_target_content,
                create_as_status=create_as,
                validate_unique_field=validate_unique,
                after_save_instruction=after_save,
                aggregations=tuple(aggregations),
                chart=chart,
                required_scope=req_scope,
                static_texts=tuple(static_texts),
                static_expressions=tuple(static_expressions),
            ))

    # ── Lower navigation ──
    nav_items = []
    if program.navigation:
        for item in program.navigation.items:
            badge_content = None
            if item.badge and "alert" in item.badge.lower() and "count" in item.badge.lower():
                badge_content = "reorder_alerts"
            nav_items.append(NavItemSpec(
                label=item.label,
                page_slug=_snake(item.page_name),
                visible_to=tuple(item.visible_to),
                badge_content=badge_content,
            ))

    # ── Lower streams ──
    streams = []
    for s in program.streams:
        streams.append(StreamSpec(description=s.description, path=s.path))

    # ── Helper: resolve content name to snake table name ──
    def _resolve_to_content(name: str) -> str:
        c = content_by_name.get(name) or content_by_singular.get(name)
        if c:
            return _snake(c.name)
        # Try plural
        c = content_by_name.get(name + "s")
        if c:
            return _snake(c.name)
        return _snake(name)

    # ── Lower computes ──
    SHAPE_MAP = {
        "transform": ComputeShape.TRANSFORM,
        "reduce": ComputeShape.REDUCE,
        "expand": ComputeShape.EXPAND,
        "correlate": ComputeShape.CORRELATE,
        "route": ComputeShape.ROUTE,
    }
    computes = []
    compute_by_name: dict[str, ComputeNode] = {c.name: c for c in program.computes}
    for comp in program.computes:
        computes.append(ComputeSpec(
            name=_qname(comp.name),
            shape=SHAPE_MAP.get(comp.shape, ComputeShape.TRANSFORM),
            input_content=tuple(_resolve_to_content(i) for i in comp.inputs),
            output_content=tuple(_resolve_to_content(o) for o in comp.outputs),
            body_lines=tuple(comp.body_lines),
            required_scope=comp.access_scope,
            required_role=comp.access_role,
            input_params=tuple(
                ComputeParamSpec(name=p.name, type_name=p.type_name)
                for p in comp.input_params
            ),
            output_params=tuple(
                ComputeParamSpec(name=p.name, type_name=p.type_name)
                for p in comp.output_params
            ),
        ))

    # ── Lower channels ──
    DIRECTION_MAP = {
        "inbound": ChannelDirection.INBOUND,
        "outbound": ChannelDirection.OUTBOUND,
        "bidirectional": ChannelDirection.BIDIRECTIONAL,
        "internal": ChannelDirection.INTERNAL,
    }
    DELIVERY_MAP = {
        "realtime": ChannelDelivery.REALTIME,
        "reliable": ChannelDelivery.RELIABLE,
        "batch": ChannelDelivery.BATCH,
        "auto": ChannelDelivery.AUTO,
    }
    channels = []
    for ch in program.channels:
        direction = DIRECTION_MAP.get(ch.direction, ChannelDirection.INBOUND)
        delivery = DELIVERY_MAP.get(ch.delivery, ChannelDelivery.AUTO)
        channels.append(ChannelSpec(
            name=_qname(ch.name),
            carries_content=_resolve_to_content(ch.carries) if ch.carries else "",
            direction=direction,
            delivery=delivery,
            endpoint=ch.endpoint,
            requirements=tuple(
                ChannelRequirementSpec(scope=r.scope, direction=r.direction)
                for r in ch.requirements
            ),
        ))

    # ── Lower boundaries ──
    boundaries = []
    boundary_names_set = {b.name for b in program.boundaries}
    for bnd in program.boundaries:
        bnd_content = []
        sub_boundaries = []
        for item in bnd.contains:
            if item in boundary_names_set:
                sub_boundaries.append(_snake(item))
            else:
                bnd_content.append(_resolve_to_content(item))
        props = tuple(
            BoundaryPropertySpec(
                name=p.name,
                type_name=p.type_name,
                jexl_expr=p.jexl_expr,
            )
            for p in bnd.properties
        )
        boundaries.append(BoundarySpec(
            name=_qname(bnd.name),
            contains_content=tuple(bnd_content),
            contains_boundaries=tuple(sub_boundaries),
            identity_mode=bnd.identity_mode,
            identity_scopes=tuple(bnd.identity_scopes),
            properties=props,
        ))

    # ── Lower error handlers ──
    error_handlers = []
    for eh in program.error_handlers:
        actions = []
        for a in eh.actions:
            actions.append(ErrorActionSpec(
                kind=a.kind,
                retry_count=a.retry_count,
                retry_backoff=a.retry_backoff,
                retry_max_delay=a.retry_max_delay,
                target=a.target,
                jexl_expr=a.jexl_expr,
                log_level=a.log_level,
            ))
        error_handlers.append(ErrorHandlerSpec(
            source=eh.source,
            condition_jexl=eh.condition_jexl,
            actions=tuple(actions),
            is_catch_all=eh.is_catch_all,
        ))

    return AppSpec(
        name=program.application.name if program.application else "App",
        description=program.application.description if program.application else "",
        auth=auth,
        content=tuple(content_schemas),
        access_grants=tuple(grants),
        state_machines=tuple(state_machines),
        events=tuple(events),
        routes=tuple(routes),
        pages=tuple(pages),
        nav_items=tuple(nav_items),
        streams=tuple(streams),
        computes=tuple(computes),
        channels=tuple(channels),
        boundaries=tuple(boundaries),
        error_handlers=tuple(error_handlers),
    )
