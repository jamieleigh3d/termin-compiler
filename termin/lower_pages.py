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
    ShowPage, DisplayTable, ShowRelated, HighlightRows, MarkAs,
    AllowFilter, AllowSearch, SubscribeTo, AcceptInput, ValidateUnique,
    CreateAs, AfterSave, ShowChart, DisplayAggregation, DisplayText,
    StructuredAggregation, SectionStart, ActionHeader, ActionButtonDef,
    LinkColumn, ChatDirective,
)
from .ir import PropValue, ComponentNode, PageEntry


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


def lower_pages(program, content_by_name, sm_by_content) -> list:
    """Lower UserStory AST nodes into PageEntry IR nodes."""
    pages = []
    for story in program.stories:
        page_name = ""
        children = []
        cur_data_table = None
        cur_form = None
        form_target_name = None

        for d in story.directives:
            if isinstance(d, ShowPage):
                page_name = d.page_name

            elif isinstance(d, ChatDirective):
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

            elif isinstance(d, DisplayTable):
                dt_content = content_by_name.get(d.content_name)
                if dt_content:
                    cols = [{"field": _snake(col), "label": col} for col in d.columns]
                    cur_data_table = ComponentNode(
                        type="data_table",
                        props={"source": _snake(d.content_name), "columns": cols},
                        children=(),
                    )

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
                        if fkey == "status" and dt_content and dt_content.name in sm_by_content:
                            mode = "state"
                            options = list(sm_by_content[dt_content.name].states)
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
                        if _snake(c.name) == form_target_name and c.name in sm_by_content:
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
                    children.append(ComponentNode(
                        type="text", props={"content": PropValue(value=d.text, is_expr=True)}))
                else:
                    children.append(ComponentNode(type="text", props={"content": d.text}))

            elif isinstance(d, ShowChart):
                children.append(ComponentNode(
                    type="chart",
                    props={
                        "source": _snake(d.content_name), "chart_type": "line",
                        "period_days": d.days, "label": f"{d.content_name} ({d.days} days)",
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
                section_node = ComponentNode(type="section", props={"title": d.title})
                children.append(section_node)

            elif isinstance(d, ActionHeader):
                pass

            elif isinstance(d, ActionButtonDef):
                if cur_data_table:
                    row_actions = cur_data_table.props.get("row_actions", [])
                    if d.kind == "delete":
                        # Resolve the content's delete scope from its access
                        # rules. The analyzer guarantees the rule exists.
                        source_snake = cur_data_table.props.get("source", "")
                        dt_content = next(
                            (c for c in program.contents
                             if _snake(c.name) == source_snake), None)
                        delete_scope = _scope_for_verb(
                            dt_content.access_rules, "delete") if dt_content else None
                        props = {
                            "label": d.label,
                            "action": "delete",
                            "required_scope": delete_scope,
                            "unavailable_behavior": d.unavailable_behavior,
                        }
                        # visible_when: the row is deletable iff the user
                        # holds the delete scope. The renderer evaluates
                        # identity.scopes at render time.
                        if delete_scope:
                            props["visible_when"] = PropValue(
                                value=f"'{delete_scope}' in identity.scopes",
                                is_expr=True,
                            )
                        row_actions.append(ComponentNode(
                            type="action_button", props=props))
                    else:
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
