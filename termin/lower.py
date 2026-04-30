# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Lowering pass: AST (Program) -> IR (AppSpec).

Resolves all names, cross-references, and inference. After lowering,
backends receive fully resolved, immutable data — no inference needed.
"""

import re
from typing import Optional

from .ast_nodes import (
    Program, Content, Field, TypeExpr, AccessRule, StateMachine,
    Transition, EventRule, UserStory, Directive, ShowPage, DisplayTable,
    ShowRelated, HighlightRows, MarkAs, AllowFilter, AllowSearch, SubscribeTo,
    AcceptInput, ValidateUnique, CreateAs, AfterSave, ShowChart,
    DisplayAggregation, DisplayText, StructuredAggregation, SectionStart,
    ActionHeader, ActionButtonDef, LinkColumn, ChatDirective,
    ComputeNode, ChannelDecl, BoundaryDecl,
    BoundaryProperty, ErrorHandler, ErrorAction,
)
from termin_core.ir.types import (
    QualifiedName, FieldType, FieldSpec, ContentSchema, Verb, AccessGrant,
    RoleSpec, AuthSpec, TransitionFeedbackSpec, TransitionSpec, StateMachineSpec,
    EventConditionSpec, EventActionSpec, EventSpec,
    HttpMethod, RouteKind, RouteSpec,
    PropValue, ComponentNode, PageEntry,
    NavItemSpec, StreamSpec, AppSpec,
    ComputeShape, ComputeSpec, ComputeParamSpec, ChannelDirection,
    ChannelDelivery, ChannelRequirementSpec, ChannelActionParamSpec,
    ChannelActionSpec, ChannelSpec, BoundarySpec,
    BoundaryPropertySpec, ErrorHandlerSpec, ErrorActionSpec,
    FieldDependency, ReclassificationPoint, DependentValueSpec,
    OwnershipSpec, RowFilterSpec,
)


# v0.9 Phase 5a.1: ComponentNode.type → presentation-base contract
# (BRD #2 §5.1 mapping per design doc §3.10). Types not listed here
# are either modifiers (filter, search, highlight, subscribe,
# action_button, edit_modal, semantic_mark, field_input) or internal
# layout (section, related) and have no contract — they're rendered
# inside their parent contract's render call.
#
# Special handling:
#   - chart: deferred per BRD §5.1; no contract. Source verb
#     `Show a chart of` still parses and lowers to ComponentNode
#     type="chart" but contract stays "". Runtime renders as a
#     deferred-placeholder until the contract is added (post-v0.9).
#   - page contract: emitted at the PageEntry level, not per node.
COMPONENT_TYPE_TO_CONTRACT: dict[str, str] = {
    "text":          "presentation-base.text",
    "markdown_view": "presentation-base.markdown",
    "data_table":    "presentation-base.data-table",
    "form":          "presentation-base.form",
    "chat":          "presentation-base.chat",
    "aggregation":   "presentation-base.metric",
    "stat_breakdown": "presentation-base.metric",
}
from .lower_pages import lower_pages


# ── Naming helpers ──

def _snake(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def _pascal(name: str) -> str:
    return ''.join(w.capitalize() for w in re.split(r'[^a-zA-Z0-9]+', name) if w)


def _qname(display: str) -> QualifiedName:
    return QualifiedName(display=display, snake=_snake(display), pascal=_pascal(display))


# ── Type mapping ──

def _field_type(te: TypeExpr) -> FieldType:
    if te.base_type in ("text", "enum", "principal"):
        # v0.9 Phase 6a: principal-typed fields store as opaque text
        # (the principal id as issued by the bound Identity provider).
        # Storage layer is unchanged; type discrimination happens at the
        # business layer per BRD #3 §3.2.
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


def _their_own_for_verb(access_rules: list[AccessRule], verb: str) -> bool:
    """v0.9 Phase 6a.3: returns True iff the first matching access rule
    for `verb` carries the `their own` qualifier. Mirrors `_scope_for_verb`
    semantics so the row_filter follows the same rule that determined the
    required_scope on the route.

    A non-their_own rule that matches first wins — meaning that if a
    content declares both `can view their own X` and `can view X` for the
    same scope, the broader rule (whichever appears first) determines the
    route shape. This is the simplest source-order policy for v0.9.
    """
    for rule in access_rules:
        if verb in rule.verbs or "create or update" in rule.verbs and verb in ("create", "update"):
            return rule.their_own
    return False



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

    # v0.9: a content may own multiple state machines (one per state-typed
    # field). v0.8 kept a dict keyed by content_name — the later machine
    # silently overwrote the earlier one. The new structure is a list per
    # content; downstream code that needs a single machine iterates.
    sm_by_content: dict[str, list[StateMachine]] = {}
    for sm in program.state_machines:
        sm_by_content.setdefault(sm.content_name, []).append(sm)

    # ── Lower content schemas ──
    content_schemas = []
    for c in program.contents:
        fields = []
        for f in c.fields:
            # Build default_expr for IR: CEL expressions pass through,
            # literal strings are wrapped in quotes to form a valid CEL literal
            default_ir = None
            if f.type_expr.default_expr is not None:
                if f.type_expr.default_is_expr:
                    default_ir = f.type_expr.default_expr  # CEL: User.Name, 0, now
                else:
                    default_ir = f'"{f.type_expr.default_expr}"'  # Literal: "N/A" → JEXL '"N/A"'
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
                one_of_values=tuple(f.type_expr.one_of_values),
                foreign_key=_snake(f.type_expr.references) if f.type_expr.references else None,
                cascade_mode=f.type_expr.cascade_mode,
                is_auto=f.type_expr.base_type == "automatic",
                list_type=f.type_expr.list_type,
                default_expr=default_ir,
                confidentiality_scopes=tuple(f.type_expr.confidentiality_scopes),
            ))
        # Lower dependent values (D-19)
        dep_vals = []
        for dv in c.dependent_values:
            if dv.constraint == "one_of":
                dep_vals.append(DependentValueSpec(
                    when=dv.when_expr,
                    field=_snake(dv.field),
                    constraint="one_of",
                    values=tuple(dv.values),
                ))
            elif dv.constraint == "equals":
                dep_vals.append(DependentValueSpec(
                    when=dv.when_expr,
                    field=_snake(dv.field),
                    constraint="equals",
                    value=dv.values[0] if dv.values else None,
                ))
            elif dv.constraint == "default":
                dep_vals.append(DependentValueSpec(
                    when=dv.when_expr,
                    field=_snake(dv.field),
                    constraint="default",
                    value=dv.values[0] if dv.values else None,
                ))

        # v0.9: emit the per-content state_machines list. Each entry:
        # {"machine_name": snake_case_field_name, "initial": initial_state}.
        # machine_name is the SQL column name — no derivation step anywhere.
        content_sms = sm_by_content.get(c.name, [])
        sm_specs = tuple(
            {"machine_name": _snake(sm.machine_name), "initial": sm.initial_state}
            for sm in content_sms
        )
        # v0.9 Phase 6a.2: ownership block. Empty list → None. Multiple
        # entries → first one wins at lowering (analyzer raises
        # TERMIN-S051 separately so the user sees the structural error).
        ownership = None
        if c.owned_by_declarations:
            ownership = OwnershipSpec(field=_snake(c.owned_by_declarations[0]))

        content_schemas.append(ContentSchema(
            name=_qname(c.name),
            singular=_snake(c.singular) if c.singular else "",
            fields=tuple(fields),
            state_machines=sm_specs,
            confidentiality_scopes=tuple(c.confidentiality_scopes),
            audit=c.audit,
            dependent_values=tuple(dep_vals),
            ownership=ownership,
        ))

    # ── Lower auth ──
    auth = AuthSpec(
        provider=program.identity.provider if program.identity else "stub",
        scopes=tuple(program.identity.scopes if program.identity else []),
        roles=tuple(RoleSpec(name=r.name, scopes=tuple(r.scopes)) for r in program.roles),
    )

    # ── Lower access grants ──
    verb_map = {"view": Verb.VIEW, "create": Verb.CREATE, "update": Verb.UPDATE, "delete": Verb.DELETE, "audit": Verb.AUDIT}
    grants = []
    for c in program.contents:
        for rule in c.access_rules:
            verbs = set()
            for v in rule.verbs:
                if v in verb_map:
                    verbs.add(verb_map[v])
                elif v == "create or update":  # legacy compound form
                    verbs.add(Verb.CREATE)
                    verbs.add(Verb.UPDATE)
            if not verbs:
                from termin.errors import SemanticError
                raise SemanticError(
                    f"TERMIN-S031: Access grant for '{c.name}' with scope '{rule.scope}' "
                    f"has no recognized verbs (got {rule.verbs!r}). "
                    f"Valid verbs: view, create, update, delete, audit.",
                    line=rule.line,
                )
            grants.append(AccessGrant(
                content=_snake(c.name),
                scope=rule.scope,
                verbs=frozenset(verbs),
                their_own=rule.their_own,
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
            # v0.9: machine_name is the snake_case field name — also the
            # SQL column name used by conforming runtimes.
            machine_name=_snake(sm.machine_name),
            initial_state=sm.initial_state,
            states=tuple(sm.states),
            transitions=tuple(
                TransitionSpec(
                    from_state=t.from_state,
                    to_state=t.to_state,
                    required_scope=t.required_scope,
                    feedback=tuple(
                        TransitionFeedbackSpec(
                            trigger=fb.trigger,
                            style=fb.style,
                            message=fb.message,
                            is_expr=fb.is_expr,
                            dismiss_seconds=fb.dismiss_seconds,
                        ) for fb in t.feedback
                    ),
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

        # For CEL events, try to infer source content from expression prefix
        # e.g., "stockLevel.updated" -> content "stock levels"
        if not resolved_content and ev.condition_expr:
            prefix = ev.condition_expr.split(".")[0].strip()
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
            if ev.action.send_channel:
                # Channel send action: "Send X to "channel""
                action = EventActionSpec(
                    send_content=ev.action.send_content,
                    send_channel=ev.action.send_channel,
                )
            elif ev.action.create_content:
                # Content create action: "Create a X with fields"
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
            condition_expr=ev.condition_expr,
            log_level=ev.log_level or "INFO",
        ))

    # ── Auto-generate CRUD routes for every Content (D-11) ──
    routes = []
    for c in program.contents:
        content_ref = _snake(c.name)
        base_path = f"/api/v1/{content_ref}"

        # Resolve scopes for each CRUD verb
        view_scope = _scope_for_verb(c.access_rules, "view")
        create_scope = _scope_for_verb(c.access_rules, "create")
        update_scope = _scope_for_verb(c.access_rules, "update")
        delete_scope = _scope_for_verb(c.access_rules, "delete")

        # v0.9 Phase 6a.3: row-filter resolution. When an access rule
        # uses `their own <content>` and the content declares ownership,
        # the corresponding route gets a RowFilterSpec(kind="ownership").
        # Without ownership the analyzer raises TERMIN-S053; we still
        # emit the filter here defensively so downstream code sees a
        # consistent shape.
        ownership_field = (
            _snake(c.owned_by_declarations[0])
            if c.owned_by_declarations else None
        )

        def _row_filter_if(verb: str) -> Optional[RowFilterSpec]:
            if not ownership_field:
                return None
            return (
                RowFilterSpec(kind="ownership", field=ownership_field)
                if _their_own_for_verb(c.access_rules, verb)
                else None
            )

        view_filter = _row_filter_if("view")
        update_filter = _row_filter_if("update")
        delete_filter = _row_filter_if("delete")
        # CREATE never gets a row_filter — there's no row to filter
        # against at create time. The runtime stamps `<owning_field> =
        # the user.id` on creates instead (Phase 6a.5 wires this).

        # GET list
        routes.append(RouteSpec(
            method=HttpMethod.GET,
            path=base_path,
            kind=RouteKind.LIST,
            content_ref=content_ref,
            required_scope=view_scope,
            row_filter=view_filter,
        ))
        # POST create
        routes.append(RouteSpec(
            method=HttpMethod.POST,
            path=base_path,
            kind=RouteKind.CREATE,
            content_ref=content_ref,
            required_scope=create_scope,
        ))
        # GET one
        routes.append(RouteSpec(
            method=HttpMethod.GET,
            path=f"{base_path}/{{id}}",
            kind=RouteKind.GET_ONE,
            content_ref=content_ref,
            required_scope=view_scope,
            row_filter=view_filter,
        ))
        # PUT update
        routes.append(RouteSpec(
            method=HttpMethod.PUT,
            path=f"{base_path}/{{id}}",
            kind=RouteKind.UPDATE,
            content_ref=content_ref,
            required_scope=update_scope,
            row_filter=update_filter,
        ))
        # DELETE
        routes.append(RouteSpec(
            method=HttpMethod.DELETE,
            path=f"{base_path}/{{id}}",
            kind=RouteKind.DELETE,
            content_ref=content_ref,
            required_scope=delete_scope,
            row_filter=delete_filter,
        ))

        # State transition routes (D-11.2).
        # v0.9: a content may own multiple state machines. The route path
        # must include the machine_name so the runtime can disambiguate
        # (two different machines may legitimately share a `to_state` name
        # — e.g. "approved" on both approval_status and review_status).
        # Deduplicate per machine by target_state: several transitions on
        # the same machine may land on the same state from different
        # sources with different scopes; the runtime's do_state_transition()
        # handler authorizes based on (from_state, to_state).
        for sm in sm_by_content.get(c.name, []):
            sm_col = _snake(sm.machine_name)
            seen_targets: set[str] = set()
            for tr in sm.transitions:
                if tr.to_state not in seen_targets:
                    seen_targets.add(tr.to_state)
                    routes.append(RouteSpec(
                        method=HttpMethod.POST,
                        path=f"{base_path}/{{id}}/_transition/{sm_col}/{tr.to_state}",
                        kind=RouteKind.TRANSITION,
                        content_ref=content_ref,
                        required_scope=None,  # enforced by do_state_transition
                        target_state=tr.to_state,
                        machine_name=sm_col,
                    ))

    # ── Lower pages (Presentation v2: component trees) ──
    pages = lower_pages(program, content_by_name, sm_by_content)

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

    # ── Field dependency resolution for Compute bodies ──
    # Build a lookup: snake_content_name -> ContentSchema
    _cs_by_snake = {cs.name.snake: cs for cs in content_schemas}

    def _resolve_field_dependencies(comp_node, schemas):
        """Extract content.field references from CEL body lines and resolve confidentiality."""
        deps = []
        seen = set()
        for body_line in comp_node.body_lines:
            # Find content_name.field_name patterns in the CEL expression
            for m in re.finditer(r'(\w+)\.(\w+)', body_line):
                content_ref, field_ref = m.group(1), m.group(2)
                cs = _cs_by_snake.get(content_ref)
                if not cs:
                    continue
                for f in cs.fields:
                    if f.name == field_ref:
                        key = (content_ref, field_ref)
                        if key not in seen:
                            seen.add(key)
                            deps.append(FieldDependency(
                                content_name=content_ref,
                                field_name=field_ref,
                                confidentiality_scopes=f.confidentiality_scopes,
                            ))
                        break
        return tuple(deps)

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
        shape = SHAPE_MAP.get(comp.shape, ComputeShape.NONE if comp.provider in ("llm", "ai-agent") else ComputeShape.TRANSFORM)
        # Infer client_safe: a Compute is client-safe when it has a pure CEL
        # body, is a TRANSFORM shape (1:1, no joins/aggregation), and has no
        # required scope (no server-side authorization needed for evaluation).
        # This is a conservative heuristic — false negatives are safe.
        is_client_safe = (
            bool(comp.body_lines)
            and shape == ComputeShape.TRANSFORM
            and not comp.access_scope
        )
        # D-20: Compute audit level and audit content reference
        audit_level = comp.audit_level  # "none", "actions", "debug"
        audit_scope = comp.audit_scope
        comp_snake = _snake(comp.name)
        audit_content_ref = f"compute_audit_log_{comp_snake}" if audit_level != "none" else None

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
            identity_mode=comp.identity_mode,
            required_confidentiality_scopes=tuple(comp.required_confidentiality_scopes),
            output_confidentiality_scope=comp.output_confidentiality,
            field_dependencies=_resolve_field_dependencies(comp, content_schemas),
            provider=comp.provider,
            preconditions=tuple(comp.preconditions),
            postconditions=tuple(comp.postconditions),
            directive=comp.directive,
            objective=comp.objective,
            directive_source=dict(comp.directive_source) if comp.directive_source else None,
            objective_source=dict(comp.objective_source) if comp.objective_source else None,
            strategy=comp.strategy,
            trigger=comp.trigger,
            trigger_where=comp.trigger_where,
            accesses=tuple(_resolve_to_content(a) for a in comp.accesses),
            reads=tuple(_resolve_to_content(r) for r in comp.reads),
            sends_to=tuple(comp.sends_to),
            emits=tuple(comp.emits),
            invokes=tuple(comp.invokes),
            input_fields=tuple(comp.input_fields),
            output_fields=tuple(comp.output_fields),
            output_creates=_resolve_to_content(comp.output_creates) if comp.output_creates else None,
            audit_level=audit_level,
            audit_scope=audit_scope,
            audit_content_ref=audit_content_ref,
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
        # Lower actions
        actions = []
        for act in ch.actions:
            actions.append(ChannelActionSpec(
                name=_qname(act.name),
                takes=tuple(
                    ChannelActionParamSpec(name=p.name, param_type=p.type_name)
                    for p in act.takes
                ),
                returns=tuple(
                    ChannelActionParamSpec(name=p.name, param_type=p.type_name)
                    for p in act.returns
                ),
                required_scopes=tuple(act.required_scopes),
            ))
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
            actions=tuple(actions),
            provider_contract=ch.provider_contract,
            failure_mode=ch.failure_mode,
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
                expr=p.expr,
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
                expr=a.expr,
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
            condition_expr=eh.condition_expr,
            actions=tuple(actions),
            is_catch_all=eh.is_catch_all,
        ))

    # ── D-20: Auto-generate audit log Content per Compute ──
    audit_log_schemas = []
    audit_log_grants = []
    audit_log_routes = []
    for cs in computes:
        if cs.audit_level == "none" or cs.audit_content_ref is None:
            continue

        audit_table_name = cs.audit_content_ref  # "compute_audit_log_{snake_name}"
        audit_qname = QualifiedName(
            display=audit_table_name.replace("_", " "),
            snake=audit_table_name,
            pascal=_pascal(audit_table_name),
        )

        # Standard fields from D-20.2
        # Note: 'id' is omitted here — the runtime storage module auto-adds
        # "id INTEGER PRIMARY KEY AUTOINCREMENT" to every Content table.
        # v0.9: Per BRD §6.3.4, audit records carry the invoking
        # Principal (with on_behalf_of for delegate-mode agents).
        # Stored as text Principal.id values rather than typed
        # references — Principal-as-typed-reference is a v0.10
        # design item (see roadmap).
        #
        # v0.9 Phase 3 slice (d): audit-Content schema is now
        # contract-aware. CEL computes get the base shape; LLM and
        # ai-agent computes get an additional reproducibility-grade
        # field set per BRD §6.3.4 (provider_product, model_identifier,
        # provider_config_hash, prompt_as_sent, sampling_params,
        # tool_calls, refusal_reason, cost_*).
        #
        # Renames in v0.9 Phase 3 slice (d):
        #   duration_ms → latency_ms (BRD §6.3.4 names it latency_ms).
        #     Operators upgrading v0.8 audit tables apply via the
        #     Phase 2.x rename-mapping path in deploy config.
        # Outcome enum widened with "refused" (BRD §6.3 contract value).
        base_audit_fields = (
            FieldSpec(name="compute_name", display_name="compute name",
                      business_type="text", column_type=FieldType.TEXT),
            FieldSpec(name="invocation_id", display_name="invocation id",
                      business_type="text", column_type=FieldType.TEXT),
            FieldSpec(name="trigger", display_name="trigger",
                      business_type="text", column_type=FieldType.TEXT),
            FieldSpec(name="started_at", display_name="started at",
                      business_type="datetime", column_type=FieldType.TIMESTAMP),
            FieldSpec(name="completed_at", display_name="completed at",
                      business_type="datetime", column_type=FieldType.TIMESTAMP),
            FieldSpec(name="latency_ms", display_name="latency ms",
                      business_type="number", column_type=FieldType.REAL),
            FieldSpec(name="outcome", display_name="outcome",
                      business_type="enum", column_type=FieldType.TEXT,
                      enum_values=("success", "refused", "error", "timeout", "cancelled")),
            FieldSpec(name="total_input_tokens", display_name="total input tokens",
                      business_type="number", column_type=FieldType.INTEGER),
            FieldSpec(name="total_output_tokens", display_name="total output tokens",
                      business_type="number", column_type=FieldType.INTEGER),
            FieldSpec(name="trace", display_name="trace",
                      business_type="text", column_type=FieldType.TEXT),
            FieldSpec(name="error_message", display_name="error message",
                      business_type="text", column_type=FieldType.TEXT),
            # v0.9 Phase 1 step 4: invoking Principal (BRD §6.3.4).
            FieldSpec(name="invoked_by_principal_id",
                      display_name="invoked by principal id",
                      business_type="text", column_type=FieldType.TEXT),
            FieldSpec(name="invoked_by_display_name",
                      display_name="invoked by display name",
                      business_type="text", column_type=FieldType.TEXT),
            FieldSpec(name="on_behalf_of_principal_id",
                      display_name="on behalf of principal id",
                      business_type="text", column_type=FieldType.TEXT),
        )

        # Per BRD §6.3.4: LLM and ai-agent invocations capture
        # reproducibility-grade fields. CEL computes don't have a
        # provider call to reproduce (the whole expression is in
        # source), so these don't apply.
        if cs.provider in ("llm", "ai-agent"):
            llm_audit_fields = (
                FieldSpec(name="provider_product", display_name="provider product",
                          business_type="text", column_type=FieldType.TEXT),
                FieldSpec(name="model_identifier", display_name="model identifier",
                          business_type="text", column_type=FieldType.TEXT),
                # Hash is the canonical-JSON hash of the provider's
                # config dict with secret values redacted to their key
                # paths. Same operational config across API-key
                # rotations hashes equal; surrounding-config changes
                # do not. See providers/builtins/_provider_hash.py.
                FieldSpec(name="provider_config_hash",
                          display_name="provider config hash",
                          business_type="text", column_type=FieldType.TEXT),
                # The fully assembled prompt the provider sent.
                # Large for chat-style apps; readers paginate.
                FieldSpec(name="prompt_as_sent", display_name="prompt as sent",
                          business_type="text", column_type=FieldType.TEXT),
                # JSON: temperature / top_p / seed / etc.
                FieldSpec(name="sampling_params", display_name="sampling params",
                          business_type="text", column_type=FieldType.TEXT),
                # JSON list of {tool, args, result, is_error, latency_ms}
                # for ai-agent invocations. Empty list for llm.
                FieldSpec(name="tool_calls", display_name="tool calls",
                          business_type="text", column_type=FieldType.TEXT),
                # Populated only when outcome="refused".
                FieldSpec(name="refusal_reason", display_name="refusal reason",
                          business_type="text", column_type=FieldType.TEXT),
                # Provider-reported cost. cost_units is the count
                # (tokens, requests); cost_unit_type names the unit;
                # cost_currency_amount carries the numeric currency
                # value as a text string when the provider supplies it.
                FieldSpec(name="cost_units", display_name="cost units",
                          business_type="number", column_type=FieldType.INTEGER),
                FieldSpec(name="cost_unit_type", display_name="cost unit type",
                          business_type="text", column_type=FieldType.TEXT),
                FieldSpec(name="cost_currency_amount",
                          display_name="cost currency amount",
                          business_type="text", column_type=FieldType.TEXT),
            )
            audit_fields = base_audit_fields + llm_audit_fields
        else:
            audit_fields = base_audit_fields

        audit_log_schemas.append(ContentSchema(
            name=audit_qname,
            fields=audit_fields,
            singular=audit_table_name,  # e.g. "compute_audit_log_scanner" — plural form doubles as singular
            audit="none",  # audit logs don't recursively audit themselves
        ))

        # Generate access grants: AUDIT + VIEW for the audit scope
        if cs.audit_scope:
            audit_log_grants.append(AccessGrant(
                content=audit_table_name,
                scope=cs.audit_scope,
                verbs=frozenset({Verb.AUDIT}),
            ))
            audit_log_grants.append(AccessGrant(
                content=audit_table_name,
                scope=cs.audit_scope,
                verbs=frozenset({Verb.VIEW}),
            ))

        # Generate CRUD routes for the audit log Content (LIST + GET_ONE)
        audit_base_path = f"/api/v1/{audit_table_name}"
        audit_view_scope = cs.audit_scope  # may be None
        audit_log_routes.append(RouteSpec(
            method=HttpMethod.GET,
            path=audit_base_path,
            kind=RouteKind.LIST,
            content_ref=audit_table_name,
            required_scope=audit_view_scope,
        ))
        audit_log_routes.append(RouteSpec(
            method=HttpMethod.GET,
            path=f"{audit_base_path}/{{id}}",
            kind=RouteKind.GET_ONE,
            content_ref=audit_table_name,
            required_scope=audit_view_scope,
        ))

    # Merge audit log schemas, grants, and routes into the main lists
    content_schemas.extend(audit_log_schemas)
    grants.extend(audit_log_grants)
    routes.extend(audit_log_routes)

    # ── v0.9 Phase 3 slice (e): compute_refusals sidecar ──
    # Per design Q6, refusal is a first-class agent tool call
    # (system.refuse(reason)). The agent-driven refusal records land in a
    # runtime-managed sidecar Content type, queryable by anyone with any
    # ai-agent compute's audit scope. One sidecar table for all
    # ai-agent computes in the app — they're correlated by
    # invocation_id with the per-compute audit log.
    has_ai_agent = any(c.provider == "ai-agent" for c in computes)
    if has_ai_agent:
        refusals_qname = QualifiedName(
            display="compute refusals",
            snake="compute_refusals",
            pascal="ComputeRefusals",
        )
        refusal_fields = (
            FieldSpec(name="compute_name", display_name="compute name",
                      business_type="text", column_type=FieldType.TEXT),
            FieldSpec(name="invocation_id", display_name="invocation id",
                      business_type="text", column_type=FieldType.TEXT),
            FieldSpec(name="reason", display_name="reason",
                      business_type="text", column_type=FieldType.TEXT),
            FieldSpec(name="refused_at", display_name="refused at",
                      business_type="datetime", column_type=FieldType.TIMESTAMP),
            FieldSpec(name="invoked_by_principal_id",
                      display_name="invoked by principal id",
                      business_type="text", column_type=FieldType.TEXT),
            FieldSpec(name="on_behalf_of_principal_id",
                      display_name="on behalf of principal id",
                      business_type="text", column_type=FieldType.TEXT),
        )
        content_schemas.append(ContentSchema(
            name=refusals_qname,
            fields=refusal_fields,
            singular="compute_refusals",
            audit="none",  # the sidecar IS the audit; don't recursively log
        ))
        # Grant view+audit access to anyone with any ai-agent compute's
        # audit scope. Operators define a single broad audit role for
        # this in practice; the sidecar is a single shared resource.
        seen_audit_scopes: set[str] = set()
        for cs in computes:
            if cs.provider != "ai-agent":
                continue
            if cs.audit_scope and cs.audit_scope not in seen_audit_scopes:
                seen_audit_scopes.add(cs.audit_scope)
                grants.append(AccessGrant(
                    content="compute_refusals",
                    scope=cs.audit_scope,
                    verbs=frozenset({Verb.VIEW}),
                ))
                grants.append(AccessGrant(
                    content="compute_refusals",
                    scope=cs.audit_scope,
                    verbs=frozenset({Verb.AUDIT}),
                ))
        # CRUD routes for the sidecar — list + get-one only.
        # Refusal records are runtime-written; operators don't create
        # or update them through the API.
        routes.append(RouteSpec(
            kind=RouteKind.LIST,
            method="GET",
            path="/api/v1/compute_refusals",
            content_ref="compute_refusals",
            required_scope=next(iter(seen_audit_scopes), None),
        ))
        routes.append(RouteSpec(
            kind=RouteKind.GET_ONE,
            method="GET",
            path="/api/v1/compute_refusals/{id}",
            content_ref="compute_refusals",
            required_scope=next(iter(seen_audit_scopes), None),
        ))

    # ── Build reclassification points from Compute specs ──
    reclass_points = []
    for cs in computes:
        if cs.output_confidentiality_scope and cs.required_confidentiality_scopes:
            # Output scope differs from input scopes — this is a reclassification
            reclass_points.append(ReclassificationPoint(
                compute_name=cs.name.display,
                input_scopes=cs.required_confidentiality_scopes,
                output_scope=cs.output_confidentiality_scope,
            ))

    # v0.9 Phase 5a.1: tag every ComponentNode with its
    # presentation-base contract and aggregate the required_contracts
    # manifest (BRD #2 §8.5). Walks the lowered pages mutating
    # ComponentNode.contract in place; ComponentNode is mutable by
    # design (not frozen), so this is well-defined.
    required_contracts: set[str] = set()
    if pages:
        # Every page implies presentation-base.page.
        required_contracts.add("presentation-base.page")
    if nav_items:
        # Navigation bar implies presentation-base.nav-bar.
        required_contracts.add("presentation-base.nav-bar")
    # Toast / banner from transition feedback specs.
    for sm in state_machines:
        for tr in sm.transitions:
            for fb in tr.feedback:
                if getattr(fb, "style", None) == "toast":
                    required_contracts.add("presentation-base.toast")
                elif getattr(fb, "style", None) == "banner":
                    required_contracts.add("presentation-base.banner")

    def _walk_node(node: ComponentNode) -> None:
        # v0.9 Phase 5b.1: a `Using` sub-clause may have already
        # set node.contract to an override target (e.g., a
        # third-party namespace `acme-ui.premium-table`). When
        # present, honor it and aggregate the override into
        # required_contracts. Otherwise default to the type→
        # contract map.
        if node.contract:
            required_contracts.add(node.contract)
        else:
            contract = COMPONENT_TYPE_TO_CONTRACT.get(node.type, "")
            if contract:
                node.contract = contract
                required_contracts.add(contract)
        for child in node.children:
            if isinstance(child, ComponentNode):
                _walk_node(child)

    for page in pages:
        for node in page.children:
            if isinstance(node, ComponentNode):
                _walk_node(node)

    return AppSpec(
        reflection_enabled=True,
        app_id=program.application.app_id if program.application else None,
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
        reclassification_points=tuple(reclass_points),
        required_contracts=tuple(sorted(required_contracts)),
    )
