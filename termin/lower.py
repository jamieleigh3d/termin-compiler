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
    DisplayAggregation, DisplayText, StructuredAggregation, SectionStart,
    ActionHeader, ActionButtonDef,
    ComputeNode, ChannelDecl, BoundaryDecl,
    BoundaryProperty, ErrorHandler, ErrorAction,
)
from .ir import (
    QualifiedName, FieldType, FieldSpec, ContentSchema, Verb, AccessGrant,
    RoleSpec, AuthSpec, TransitionSpec, StateMachineSpec,
    EventConditionSpec, EventActionSpec, EventSpec,
    HttpMethod, RouteKind, RouteSpec,
    PropValue, ComponentNode, PageEntry,
    NavItemSpec, StreamSpec, AppSpec,
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
                business_type=f.type_expr.base_type,
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
    channel_names = {ch.name for ch in program.channels}
    compute_names = {c.name for c in program.computes}
    boundary_names = {b.name for b in program.boundaries}
    state_machines = []
    for sm in program.state_machines:
        # Infer primitive_type from what the state machine attaches to
        sm_ref = sm.content_name
        if sm_ref in channel_names:
            prim_type = "channel"
        elif sm_ref in compute_names:
            prim_type = "compute"
        elif sm_ref in boundary_names:
            prim_type = "boundary"
        else:
            prim_type = "content"
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
            primitive_type=prim_type,
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

    # ── Lower pages (Presentation v2: component trees) ──
    pages = []
    for story in program.stories:
        page_name = ""
        children = []          # top-level ComponentNode list
        cur_data_table = None   # current data_table being assembled
        cur_form = None         # current form being assembled
        form_target_name = None # snake_case target content for form

        for d in story.directives:
            if isinstance(d, ShowPage):
                page_name = d.page_name

            elif isinstance(d, DisplayTable):
                dt_content = content_by_name.get(d.content_name)
                if dt_content:
                    cols = [{"field": _snake(col), "label": col} for col in d.columns]
                    cur_data_table = ComponentNode(
                        type="data_table",
                        props={"source": _snake(d.content_name), "columns": cols},
                        children=(),
                    )

            elif isinstance(d, AllowFilter):
                if cur_data_table and d.fields:
                    source_name = cur_data_table.props.get("source", "")
                    dt_content = next((c for c in program.contents if _snake(c.name) == source_name), None)
                    filter_nodes = []
                    for fname in d.fields:
                        fkey = _snake(fname)
                        mode = "text"
                        options = []
                        if fkey == "status" and dt_content and dt_content.name in sm_by_content:
                            mode = "state"
                            options = list(sm_by_content[dt_content.name].states)
                        elif dt_content:
                            for cf in dt_content.fields:
                                if _snake(cf.name) == fkey and cf.type_expr.base_type == "enum":
                                    mode = "enum"
                                    options = list(cf.type_expr.enum_values)
                                    break
                        if mode == "text" and fkey == "warehouse":
                            mode = "distinct"
                        props = {"field": fkey, "mode": mode}
                        if options:
                            props["options"] = options
                        filter_nodes.append(ComponentNode(type="filter", props=props))
                    cur_data_table.children = cur_data_table.children + tuple(filter_nodes)

            elif isinstance(d, AllowSearch):
                if cur_data_table:
                    cur_data_table.children = cur_data_table.children + (
                        ComponentNode(type="search", props={"fields": [_snake(f) for f in d.fields]}),
                    )

            elif isinstance(d, HighlightRows):
                if cur_data_table:
                    if d.jexl_condition:
                        cond = PropValue(value=d.jexl_condition, is_expr=True)
                    else:
                        cond = PropValue(value=f".{_snake(d.field)} <= .{_snake(d.threshold_field)}", is_expr=True)
                    cur_data_table.children = cur_data_table.children + (
                        ComponentNode(type="highlight", props={"condition": cond}),
                    )

            elif isinstance(d, SubscribeTo):
                if cur_data_table:
                    cur_data_table.children = cur_data_table.children + (
                        ComponentNode(type="subscribe", props={"content": _snake(d.content_name)}),
                    )

            elif isinstance(d, ShowRelated):
                if cur_data_table:
                    props = {
                        "content": _snake(d.related_content),
                        "join": _snake(d.singular) if d.singular else "product",
                    }
                    if d.group_by:
                        props["group_by"] = _snake(d.group_by)
                    cur_data_table.children = cur_data_table.children + (
                        ComponentNode(type="related", props=props),
                    )

            elif isinstance(d, AcceptInput):
                target_content = _best_content_for_fields(d.fields, program.contents)
                if target_content:
                    form_target_name = _snake(target_content.name)
                    field_inputs = []
                    for fname in d.fields:
                        col = _snake(fname)
                        field_obj = next((f for f in target_content.fields if _snake(f.name) == col), None)
                        fi_props = {"field": col, "label": fname}
                        if field_obj:
                            fi_props["input_type"] = field_obj.type_expr.base_type
                            if field_obj.type_expr.required:
                                fi_props["required"] = True
                            if field_obj.type_expr.base_type == "currency":
                                fi_props["input_type"] = "currency"
                                fi_props["step"] = "0.01"
                            elif field_obj.type_expr.base_type == "whole_number":
                                fi_props["input_type"] = "number"
                                if field_obj.type_expr.minimum is not None:
                                    fi_props["minimum"] = field_obj.type_expr.minimum
                            elif field_obj.type_expr.base_type == "enum":
                                fi_props["input_type"] = "enum"
                                fi_props["enum_values"] = list(field_obj.type_expr.enum_values)
                            elif field_obj.type_expr.references:
                                fi_props["input_type"] = "reference"
                                fi_props["reference_content"] = _snake(field_obj.type_expr.references)
                                ref_content = content_by_name.get(field_obj.type_expr.references)
                                if ref_content:
                                    ref_display, ref_unique = _resolve_ref_display(ref_content)
                                    fi_props["reference_display_col"] = ref_display
                                    if ref_unique:
                                        fi_props["reference_unique_col"] = ref_unique
                            else:
                                fi_props["input_type"] = "text"
                        field_inputs.append(ComponentNode(type="field_input", props=fi_props))
                    cur_form = ComponentNode(
                        type="form",
                        props={"target": form_target_name},
                        children=tuple(field_inputs),
                    )

            elif isinstance(d, ValidateUnique):
                if cur_form:
                    field_key = _snake(d.field)
                    new_children = []
                    for ch in cur_form.children:
                        if ch.type == "field_input" and ch.props.get("field") == field_key:
                            ch.props["validate_unique"] = True
                        new_children.append(ch)
                    cur_form.children = tuple(new_children)

            elif isinstance(d, CreateAs):
                if cur_form and form_target_name:
                    for c in program.contents:
                        if _snake(c.name) == form_target_name and c.name in sm_by_content:
                            cur_form.props["create_as"] = d.initial_state
                            break

            elif isinstance(d, AfterSave):
                if cur_form:
                    cur_form.props["after_save"] = d.instruction

            elif isinstance(d, DisplayAggregation):
                desc_lower = d.description.lower()
                gc = _guess_content(d.description, program.contents)
                # Extract bracket expression if present: "total time [sum(hours)]" -> "sum(hours)"
                import re as _re_agg
                jexl_match = _re_agg.search(r'\[(.+?)\]', d.description)
                jexl_expr = jexl_match.group(1) if jexl_match else None
                # Clean label: strip bracket expression
                clean_label = _re_agg.sub(r'\[.+?\]', '', d.description).strip()
                if gc:
                    source = _snake(gc.name)
                    if "count" in desc_lower:
                        if "breakdown" in desc_lower or "active" in desc_lower:
                            children.append(ComponentNode(
                                type="stat_breakdown",
                                props={"source": source, "label": clean_label or d.description, "group_by": "status"},
                            ))
                        else:
                            children.append(ComponentNode(
                                type="aggregation",
                                props={"source": source, "label": clean_label or d.description, "agg_type": "count"},
                            ))
                    elif "sum" in desc_lower or "value" in desc_lower:
                        props = {"source": source, "label": clean_label or d.description, "agg_type": "sum"}
                        if jexl_expr:
                            # Extract the column from expressions like "sum(hours)" -> "hours"
                            col_match = _re_agg.match(r'sum\((\w+)\)', jexl_expr)
                            if col_match:
                                props["expression"] = PropValue(value=col_match.group(1), is_expr=True)
                            else:
                                props["expression"] = PropValue(value=jexl_expr, is_expr=True)
                        children.append(ComponentNode(type="aggregation", props=props))
                    else:
                        children.append(ComponentNode(
                            type="aggregation",
                            props={"source": source, "label": d.description, "agg_type": "count"},
                        ))

            elif isinstance(d, DisplayText):
                if d.is_expression:
                    children.append(ComponentNode(
                        type="text",
                        props={"content": PropValue(value=d.text, is_expr=True)},
                    ))
                else:
                    children.append(ComponentNode(type="text", props={"content": d.text}))

            elif isinstance(d, ShowChart):
                children.append(ComponentNode(
                    type="chart",
                    props={
                        "source": _snake(d.content_name),
                        "chart_type": "line",
                        "period_days": d.days,
                        "label": f"{d.content_name} ({d.days} days)",
                    },
                ))

            elif isinstance(d, StructuredAggregation):
                source = _snake(d.source_content)
                if d.group_by:
                    children.append(ComponentNode(
                        type="stat_breakdown",
                        props={"source": source, "label": f"{d.source_content} by {d.group_by}",
                               "group_by": _snake(d.group_by)},
                    ))
                else:
                    props = {"source": source, "agg_type": d.agg_type,
                             "label": f"{d.agg_type.title()} of {d.source_content}"}
                    if d.expression:
                        props["expression"] = PropValue(value=d.expression, is_expr=True)
                    if d.format != "number":
                        props["format"] = d.format
                    children.append(ComponentNode(type="aggregation", props=props))

            elif isinstance(d, SectionStart):
                # Push a section marker — subsequent directives become children
                # We handle this by creating the section node and tracking it
                section_node = ComponentNode(type="section", props={"title": d.title})
                children.append(section_node)
                # Note: true nesting requires indentation tracking which we don't
                # do yet. For now sections are flat markers. Future: use a stack.

            elif isinstance(d, ActionHeader):
                pass  # Action header is a context marker; buttons follow

            elif isinstance(d, ActionButtonDef):
                if cur_data_table:
                    row_actions = cur_data_table.props.get("row_actions", [])
                    row_actions.append(ComponentNode(
                        type="action_button",
                        props={
                            "label": d.label,
                            "action": "transition",
                            "target_state": d.target_state,
                            "visible_when": PropValue(
                                value=f".state.canTransition('{d.target_state}')",
                                is_expr=True,
                            ),
                            "unavailable_behavior": d.unavailable_behavior,
                        },
                    ))
                    cur_data_table.props["row_actions"] = row_actions

        # Finalize accumulated data_table and form
        if cur_data_table:
            children.insert(0, cur_data_table)
        if cur_form:
            # Resolve scope for form POST
            if form_target_name:
                for c in program.contents:
                    if _snake(c.name) == form_target_name:
                        scope = _scope_for_verb(c.access_rules, "create")
                        if scope:
                            cur_form.props["submit_scope"] = scope
                        break
            children.append(cur_form)

        # Resolve required scope for the page
        req_scope = None
        if form_target_name:
            for c in program.contents:
                if _snake(c.name) == form_target_name:
                    req_scope = _scope_for_verb(c.access_rules, "create")
                    break

        if page_name:
            pages.append(PageEntry(
                name=page_name,
                slug=_snake(page_name),
                role=story.role,
                required_scope=req_scope,
                children=tuple(children),
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
        shape = SHAPE_MAP.get(comp.shape, ComputeShape.TRANSFORM)
        # Infer client_safe: a Compute is client-safe when it has a pure JEXL
        # body, is a TRANSFORM shape (1:1, no joins/aggregation), and has no
        # required scope (no server-side authorization needed for evaluation).
        # This is a conservative heuristic — false negatives are safe.
        is_client_safe = (
            bool(comp.body_lines)
            and shape == ComputeShape.TRANSFORM
            and not comp.access_scope
        )
        computes.append(ComputeSpec(
            name=_qname(comp.name),
            shape=shape,
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
            client_safe=is_client_safe,
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
    # Build lookup sets for source_type inference
    content_snake_names = {_snake(c.name) for c in program.contents}
    channel_snake_names = {_snake(ch.name) for ch in program.channels}
    compute_snake_names = {_snake(c.name) for c in program.computes}
    boundary_snake_names = {_snake(b.name) for b in program.boundaries}
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
        # Infer source_type from source name
        src_snake = _snake(eh.source) if eh.source else ""
        if src_snake in channel_snake_names:
            src_type = "channel"
        elif src_snake in compute_snake_names:
            src_type = "compute"
        elif src_snake in content_snake_names:
            src_type = "content"
        elif src_snake in boundary_snake_names:
            src_type = "boundary"
        else:
            src_type = ""
        error_handlers.append(ErrorHandlerSpec(
            source=eh.source,
            source_type=src_type,
            condition_jexl=eh.condition_jexl,
            actions=tuple(actions),
            is_catch_all=eh.is_catch_all,
        ))

    return AppSpec(
        reflection_enabled=True,
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
