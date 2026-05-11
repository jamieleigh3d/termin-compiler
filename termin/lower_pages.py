# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Lower pages — Presentation v2 component tree lowering.

Transforms UserStory AST nodes into PageEntry IR nodes with
component tree children (data_table, form, chat, aggregation, etc.).
"""
from __future__ import annotations
import re

from .ast_nodes import (
    Content, AccessRule, StateMachine, UserStory,
    ShowPage, DisplayTable, ShowRelated, HighlightRows, MarkAs, UsingOverride,
    AllowFilter, AllowSearch, AllowInlineEdit, SubscribeTo, AcceptInput, ValidateUnique,
    CreateAs, AfterSave, ShowChart, DisplayAggregation, DisplayText,
    StructuredAggregation, SectionStart, ActionHeader, ActionButtonDef,
    PackageContractCall,
    LinkColumn, ChatDirective,
)
from termin_core.ir.types import PropValue, ComponentNode, PageEntry


def _snake(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def _scope_for_verb(access_rules: list[AccessRule], verb: str) -> str | None:
    for rule in access_rules:
        if verb in rule.verbs or "create or update" in rule.verbs and verb in ("create", "update"):
            return rule.scope
    return None


def _best_content_for_fields(field_names, contents):
    best, best_count = None, 0
    for c in contents:
        content_cols = {_snake(f.name) for f in c.fields}
        count = sum(1 for f in field_names if _snake(f) in content_cols)
        if count > best_count:
            best_count = count; best = c
    return best


def _guess_content(text, contents):
    text_lower = text.lower()
    for c in contents:
        if c.name.lower() in text_lower or c.singular.lower() in text_lower:
            return c
    return None


def _resolve_ref_display(ref_content):
    display_col, unique_col = "id", None
    for f in ref_content.fields:
        if f.type_expr.unique and f.type_expr.base_type == "text":
            unique_col = _snake(f.name)
        if _snake(f.name) == "name":
            display_col = "name"
    if not unique_col and display_col == "id":
        for f in ref_content.fields:
            if f.type_expr.base_type == "text":
                display_col = _snake(f.name); break
    return display_col, unique_col


def _build_field_input_props(field_obj, display_label, content_by_name) -> dict:
    """Build props for a field_input ComponentNode from a Content Field.

    Shared by AcceptInput (create form) and edit modal lowering so the
    two input renderings stay consistent.
    """
    col = _snake(field_obj.name)
    props = {"field": col, "label": display_label}
    props["input_type"] = field_obj.type_expr.base_type
    if field_obj.type_expr.required:
        props["required"] = True
    if field_obj.type_expr.base_type == "currency":
        props["input_type"] = "currency"
        props["step"] = "0.01"
    elif field_obj.type_expr.base_type == "whole_number":
        props["input_type"] = "number"
        if field_obj.type_expr.minimum is not None:
            props["minimum"] = field_obj.type_expr.minimum
    elif field_obj.type_expr.base_type == "enum":
        props["input_type"] = "enum"
        props["enum_values"] = list(field_obj.type_expr.enum_values)
    elif field_obj.type_expr.references:
        props["input_type"] = "reference"
        props["reference_content"] = _snake(field_obj.type_expr.references)
        ref_content = content_by_name.get(field_obj.type_expr.references)
        if ref_content:
            ref_display, ref_unique = _resolve_ref_display(ref_content)
            props["reference_display_col"] = ref_display
            if ref_unique:
                props["reference_unique_col"] = ref_unique
    else:
        props["input_type"] = "text"
    return props


# System fields excluded from the edit modal. Automatic-type fields
# are also excluded (created_at, updated_at, etc.).
_SYSTEM_FIELD_NAMES = frozenset({"id", "created_at", "updated_at"})


def _build_edit_modal(content, sm_by_content, content_by_name):
    """Build an edit_modal ComponentNode for a given Content.

    Contains a field_input per editable non-system field. If the content
    has a state machine, also appends a field_input with input_type="state"
    for the status column. The renderer populates state-select options
    from transition rules filtered by user scopes at render time.

    Multi-state-machine per content is not yet supported by the runtime
    (see v0.9 backlog). For now, at most one state field is emitted per
    modal, matching the sm_by_content[content.name] singleton assumption.
    """
    # State-machine columns get rendered as state-selects below; skip
    # them in the regular-field loop so they aren't emitted twice (the
    # browser then sees two `data-termin-field="<machine>"` elements,
    # `form.querySelector` matches the text input first, and openEdit's
    # `Array.from(sel.options)` throws on the input's missing .options).
    state_machine_columns = {
        _snake(sm.machine_name)
        for sm in sm_by_content.get(content.name, [])
    }
    field_inputs = []
    for f in content.fields:
        if _snake(f.name) in _SYSTEM_FIELD_NAMES:
            continue
        if f.type_expr.base_type == "automatic":
            continue
        if _snake(f.name) in state_machine_columns:
            continue
        props = _build_field_input_props(f, f.name, content_by_name)
        field_inputs.append(ComponentNode(type="field_input", props=props))

    # If the content has state machines, include one field_input per
    # machine as a state-select. The full list of all states is embedded
    # in props so the renderer can pre-render options without per-content
    # Jinja context plumbing. The JS opener filters those options at
    # modal-open time based on current row state + user scopes + rules.
    for sm in sm_by_content.get(content.name, []):
        all_states = {sm.initial_state}
        for tr in sm.transitions:
            all_states.add(tr.from_state)
            all_states.add(tr.to_state)
        # Preserve initial first, then alphabetical for the rest.
        ordered_states = [sm.initial_state] + sorted(
            s for s in all_states if s != sm.initial_state)
        col = _snake(sm.machine_name)
        state_field_props = {
            "field": col,
            "label": sm.machine_name.title(),
            "input_type": "state",
            "state_machine": col,
            "all_states": ordered_states,
        }
        field_inputs.append(
            ComponentNode(type="field_input", props=state_field_props))

    return ComponentNode(
        type="edit_modal",
        props={
            "content": _snake(content.name),
            "singular": content.singular,
        },
        children=tuple(field_inputs),
    )


def lower_pages(program, content_by_name, sm_by_content) -> list:
    """Lower UserStory AST nodes into PageEntry IR nodes."""
    pages = []
    for story in program.stories:
        page_name = ""
        children = []
        cur_data_table = None
        cur_form = None
        form_target_name = None
        # v0.9 Phase 5b.1: track most recently appended rendering
        # ComponentNode so a following UsingOverride directive
        # can attach its contract to it. Reset across pages /
        # stories. Includes both top-level renderables and the
        # special-case data_table whose modifiers are appended as
        # children rather than siblings.
        cur_renderable = None
        # v0.9.4: tracks every Display-a-table directive in source
        # order so multi-table pages (airlock Results: score-axis-
        # card + badge-strip on the same page) emit one ComponentNode
        # per directive. Pre-v0.9.4 only the *last* cur_data_table
        # made it into children — earlier tables were silently
        # overwritten. All tables get inserted at the top of children
        # at end-of-loop (preserves the "tables first, other
        # renderables below" convention single-table pages
        # depended on; for single-table pages this is a no-op
        # behavior change).
        data_tables_in_source_order: list = []

        for d in story.directives:
            if isinstance(d, ShowPage):
                page_name = d.page_name

            elif isinstance(d, ChatDirective):
                # v0.9.2 L9 (tech design §14): two binding shapes share the
                # `chat` ComponentNode — the legacy messages-collection
                # binding (role_field + content_field) and the new
                # conversation-field binding (source + conversation_field).
                # Provider renderers discriminate on `conversation_field`
                # being present.
                if d.conversation_field is not None:
                    parent_content, conv_field = d.conversation_field
                    parent_snake = _snake(parent_content)
                    field_snake = _snake(conv_field)
                    chat_node = ComponentNode(
                        type="chat",
                        props={
                            "source": parent_snake,
                            "conversation_field": field_snake,
                        },
                        # The chat surface subscribes to the field-specific
                        # `<content>.<field>.appended` event (§14.5) — not the
                        # CRUD `created` event used by the legacy binding.
                        # The presentation contract is the same; only the
                        # channel name differs. The runtime hydrator resolves
                        # the channel from `data-termin-conversation-field`.
                        children=(),
                    )
                else:
                    chat_source = _snake(d.source)
                    chat_node = ComponentNode(
                        type="chat",
                        props={
                            "source": chat_source,
                            "role_field": d.role_field,
                            "content_field": d.content_field,
                        },
                        children=(
                            ComponentNode(type="subscribe", props={"content": chat_source}),
                        ),
                    )
                children.append(chat_node)
                cur_renderable = chat_node

            elif isinstance(d, DisplayTable):
                dt_content = content_by_name.get(d.content_name)
                if dt_content:
                    # v0.9.4: stash the previous data_table in source
                    # order before starting a new one. Earlier
                    # behavior overwrote the reference and dropped
                    # the prior table. Modifier directives (filter,
                    # search, row_actions, inline_edit) still attach
                    # to the latest cur_data_table — they always
                    # belong to the most recently declared table.
                    if cur_data_table is not None:
                        data_tables_in_source_order.append(cur_data_table)
                    cols = [{"field": _snake(col), "label": col} for col in d.columns]
                    cur_data_table = ComponentNode(
                        type="data_table",
                        props={"source": _snake(d.content_name), "columns": cols},
                        children=(),
                    )
                    cur_renderable = cur_data_table

            elif isinstance(d, PackageContractCall):
                # v0.9 Phase 5c.2: a contract-package source-verb
                # instance lowered as a ComponentNode whose `contract`
                # is the fully-qualified name (`<ns>.<contract>`).
                # The matched bindings flow through as props — the
                # bound provider's render_ssr / CSR renderer reads
                # them by name. Type stays generic ("package_contract")
                # so the type→contract default map doesn't override
                # node.contract during the Phase 5a.1 walk.
                pkg_node = ComponentNode(
                    type="package_contract",
                    contract=d.qualified_name,
                    props={
                        "source_verb": d.source_verb,
                        # Preserve the bindings under a stable key
                        # so providers can read them without
                        # special-casing each placeholder name.
                        "bindings": dict(d.bindings),
                        # Also expose each binding as a top-level
                        # prop for ergonomics (provider authors
                        # write `props["state-ref"]` instead of
                        # `props["bindings"]["state-ref"]`).
                        **{k: v for k, v in d.bindings.items()},
                    },
                    children=(),
                )
                children.append(pkg_node)
                cur_renderable = pkg_node

            elif isinstance(d, UsingOverride):
                # v0.9 Phase 5b.1: attach the contract override to
                # the immediately preceding rendering ComponentNode.
                # If no renderable is in scope (Using on its own
                # before any directive), silently ignore — the
                # analyzer surfaces this as a structural error.
                if cur_renderable is not None and d.target:
                    cur_renderable.contract = d.target

            elif isinstance(d, LinkColumn):
                if cur_data_table:
                    col_key = _snake(d.column)
                    for col in cur_data_table.props.get("columns", []):
                        if col.get("field") == col_key:
                            col["link_template"] = d.link_template; break

            elif isinstance(d, AllowFilter):
                if cur_data_table and d.fields:
                    source_name = cur_data_table.props.get("source", "")
                    dt_content = next((c for c in program.contents if _snake(c.name) == source_name), None)
                    filter_nodes = []
                    for fname in d.fields:
                        fkey = _snake(fname)
                        mode, options = "text", []
                        # v0.9: match the filter key against any state
                        # machine's snake_case column name on this content.
                        sm_match = None
                        if dt_content:
                            for sm_candidate in sm_by_content.get(dt_content.name, []):
                                if _snake(sm_candidate.machine_name) == fkey:
                                    sm_match = sm_candidate; break
                        if sm_match is not None:
                            mode = "state"
                            options = list(sm_match.states)
                        elif dt_content:
                            for cf in dt_content.fields:
                                if _snake(cf.name) == fkey and cf.type_expr.base_type == "enum":
                                    mode = "enum"
                                    options = list(cf.type_expr.enum_values); break
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

            elif isinstance(d, AllowInlineEdit):
                # Opt in to click-to-edit cells for the listed fields on
                # the current data_table. Analyzer guaranteed the content
                # has `can update`, each field exists, and none are
                # state-machine-backed.
                if cur_data_table:
                    source_snake = cur_data_table.props.get("source", "")
                    dt_content = next(
                        (c for c in program.contents
                         if _snake(c.name) == source_snake), None)
                    update_scope = _scope_for_verb(
                        dt_content.access_rules, "update") if dt_content else None
                    editable_snake = [_snake(f) for f in d.fields]
                    # Preserve any previously-declared editable fields
                    # (author could split across multiple directives).
                    existing_fields = cur_data_table.props.get(
                        "inline_editable_fields", [])
                    merged = list(existing_fields)
                    for f in editable_snake:
                        if f not in merged:
                            merged.append(f)
                    cur_data_table.props["inline_editable_fields"] = merged
                    if update_scope:
                        cur_data_table.props["inline_edit_scope"] = update_scope

            elif isinstance(d, MarkAs):
                if cur_data_table:
                    cond = PropValue(value=d.condition_expr, is_expr=True)
                    label = PropValue(value=d.label, is_expr=False)
                    scope = PropValue(value=d.scope, is_expr=False)
                    cur_data_table.children = cur_data_table.children + (
                        ComponentNode(type="semantic_mark", props={
                            "condition": cond, "label": label, "scope": scope,
                        }),
                    )

            elif isinstance(d, HighlightRows):
                if cur_data_table:
                    if d.condition_expr:
                        cond = PropValue(value=d.condition_expr, is_expr=True)
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
                        if _snake(c.name) == form_target_name and sm_by_content.get(c.name):
                            cur_form.props["create_as"] = d.initial_state; break

            elif isinstance(d, AfterSave):
                if cur_form:
                    cur_form.props["after_save"] = d.instruction

            elif isinstance(d, DisplayAggregation):
                desc_lower = d.description.lower()
                gc = _guess_content(d.description, program.contents)
                jexl_match = re.search(r'\[(.+?)\]', d.description)
                expr = jexl_match.group(1) if jexl_match else None
                clean_label = re.sub(r'\[.+?\]', '', d.description).strip()
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
                        if expr:
                            col_match = re.match(r'sum\((\w+)\)', expr)
                            if col_match:
                                props["expression"] = PropValue(value=col_match.group(1), is_expr=True)
                            else:
                                props["expression"] = PropValue(value=expr, is_expr=True)
                        children.append(ComponentNode(type="aggregation", props=props))
                    else:
                        children.append(ComponentNode(
                            type="aggregation",
                            props={"source": source, "label": d.description, "agg_type": "count"},
                        ))

            elif isinstance(d, DisplayText):
                if d.is_expression:
                    text_node = ComponentNode(
                        type="text", props={"content": PropValue(value=d.text, is_expr=True)})
                else:
                    text_node = ComponentNode(type="text", props={"content": d.text})
                children.append(text_node)
                cur_renderable = text_node

            elif isinstance(d, ShowChart):
                chart_node = ComponentNode(
                    type="chart",
                    props={
                        "source": _snake(d.content_name), "chart_type": "line",
                        "period_days": d.days, "label": f"{d.content_name} ({d.days} days)",
                    },
                )
                children.append(chart_node)
                cur_renderable = chart_node

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
                section_node = ComponentNode(type="section", props={"title": d.title})
                children.append(section_node)

            elif isinstance(d, ActionHeader):
                pass

            elif isinstance(d, ActionButtonDef):
                if cur_data_table:
                    row_actions = cur_data_table.props.get("row_actions", [])
                    source_snake = cur_data_table.props.get("source", "")
                    dt_content = next(
                        (c for c in program.contents
                         if _snake(c.name) == source_snake), None)
                    if d.kind == "delete":
                        # Resolve the content's delete scope from its access
                        # rules. The analyzer guarantees the rule exists.
                        delete_scope = _scope_for_verb(
                            dt_content.access_rules, "delete") if dt_content else None
                        props = {
                            "label": d.label,
                            "action": "delete",
                            "required_scope": delete_scope,
                            "unavailable_behavior": d.unavailable_behavior,
                        }
                        if delete_scope:
                            props["visible_when"] = PropValue(
                                value=f"'{delete_scope}' in identity.scopes",
                                is_expr=True,
                            )
                        row_actions.append(ComponentNode(
                            type="action_button", props=props))
                    elif d.kind == "edit":
                        # Resolve the content's update scope. The analyzer
                        # guarantees `can update` exists on the content.
                        update_scope = _scope_for_verb(
                            dt_content.access_rules, "update") if dt_content else None
                        props = {
                            "label": d.label,
                            "action": "edit",
                            "required_scope": update_scope,
                            "unavailable_behavior": d.unavailable_behavior,
                        }
                        if update_scope:
                            props["visible_when"] = PropValue(
                                value=f"'{update_scope}' in identity.scopes",
                                is_expr=True,
                            )
                        row_actions.append(ComponentNode(
                            type="action_button", props=props))
                        # Also append an edit_modal to the page if one
                        # for this content is not already there. This is
                        # the pre-populated form the button opens.
                        already_has_modal = any(
                            c.type == "edit_modal"
                            and c.props.get("content") == source_snake
                            for c in children
                        )
                        if not already_has_modal and dt_content:
                            modal = _build_edit_modal(
                                dt_content, sm_by_content, content_by_name)
                            children.append(modal)
                    else:
                        # v0.9: transition buttons carry machine_name so the
                        # runtime can target the right state field on content
                        # with multiple state machines.
                        machine_snake = _snake(d.machine_name) if d.machine_name else ""
                        row_actions.append(ComponentNode(
                            type="action_button",
                            props={
                                "label": d.label,
                                "action": "transition",
                                "machine_name": machine_snake,
                                "target_state": d.target_state,
                                "visible_when": PropValue(
                                    value=f".state.canTransition('{machine_snake}', '{d.target_state}')",
                                    is_expr=True,
                                ),
                                "unavailable_behavior": d.unavailable_behavior,
                            },
                        ))
                    cur_data_table.props["row_actions"] = row_actions

        # Finalize accumulated data_tables and form.
        # v0.9.4: every data_table (collected per directive in source
        # order) gets inserted at the top of children, in source
        # order. Earlier behavior only kept the LAST cur_data_table
        # and inserted it at position 0; multi-table pages lost the
        # earlier ones. Reversing then insert(0)'ing each one yields
        # the source-order list at the top of children.
        if cur_data_table is not None:
            data_tables_in_source_order.append(cur_data_table)
        for dt in reversed(data_tables_in_source_order):
            children.insert(0, dt)
        if cur_form:
            if form_target_name:
                for c in program.contents:
                    if _snake(c.name) == form_target_name:
                        scope = _scope_for_verb(c.access_rules, "create")
                        if scope:
                            cur_form.props["submit_scope"] = scope
                        break
            children.append(cur_form)

        req_scope = None
        if form_target_name:
            for c in program.contents:
                if _snake(c.name) == form_target_name:
                    req_scope = _scope_for_verb(c.access_rules, "create"); break

        if page_name:
            pages.append(PageEntry(
                name=page_name, slug=_snake(page_name), role=story.role,
                required_scope=req_scope, children=tuple(children),
            ))

    return pages
