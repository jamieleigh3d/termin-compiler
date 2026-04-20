# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Per-line parse dispatch — the main _parse_line function.

Maps each classified rule to the appropriate TatSu parse + AST builder,
with fallback paths for TatSu context state leaks.
"""
from __future__ import annotations
from typing import Optional

from .ast_nodes import (
    Application, Identity, Role, RoleAlias, Content, Field, TypeExpr,
    AccessRule, StateMachine, EventRule, EventAction, EventCondition,
    ErrorHandler, ErrorAction, UserStory, ShowPage, DisplayTable, ShowRelated,
    HighlightRows, MarkAs, AllowFilter, AllowSearch, AllowInlineEdit, SubscribeTo, AcceptInput,
    ValidateUnique, CreateAs, AfterSave, ShowChart, DisplayAggregation,
    StructuredAggregation, SectionStart, ActionHeader, ActionButtonDef,
    Stream, ChatDirective, DisplayText, LinkColumn,
    ComputeNode, ChannelDecl, ChannelRequirement, ActionDecl,
    BoundaryDecl, BoundaryProperty,
)
from .parse_helpers import (
    _try_parse, _rule, _qs, _ql, _cl, _ol, _si,
    _scal, _eqs, _fq, _eb,
    _parse_field_type,
)
from .parse_builders import (
    _build_access, _build_story, _build_nav, _build_feedback,
    _build_trans, _build_ev1, _build_err_act, _build_comp_shape,
    _parse_action_params, _parse_content_when, _parse_unconditional_constraint,
)


def _parse_line(text: str, rule: str, ln: int):
    """Parse a single classified line into a tagged AST tuple.

    Returns a tuple like ("application", Application(...)) or None.
    The tag is used by _assemble() to build the Program tree.
    """
    P = _try_parse  # alias

    if rule == "application_line":
        r = P(text, rule); return ("application", Application(name=str(r["name"]).strip() if r else text[len("Application:"):].strip(), line=ln))
    if rule == "description_line":
        r = P(text, rule); return ("description", str(r["desc"]).strip() if r else text[len("Description:"):].strip())
    if rule == "id_line":
        r = P(text, rule); return ("app_id", str(r["id"]).strip() if r else text[len("Id:"):].strip())
    if rule == "identity_line":
        r = P(text, rule); return ("identity", Identity(provider=str(r["provider"]).strip() if r else text.split("with",1)[1].strip(), line=ln))
    if rule == "scopes_line":
        r = P(text, rule); return ("scopes", _ql(r.get("scopes")) if r else _eqs(text))
    if rule == "role_standard_line":
        r = P(text, rule)
        if r: return ("role", Role(name=_qs(r.get("name","")), scopes=_ql(r.get("scopes")), line=ln))
        n = _fq(text); sc = _eqs(text)
        if sc and sc[0] == n: sc = sc[1:]
        return ("role", Role(name=n, scopes=sc, line=ln))
    if rule == "role_bare_line":
        r = P(text, rule)
        if r: return ("role", Role(name=str(r.get("name","")).strip(), scopes=_ql(r.get("scopes")), line=ln))
        hi = text.find(" has "); return ("role", Role(name=text[:hi].strip() if hi>=0 else text.strip(), scopes=_eqs(text), line=ln))
    if rule == "role_alias_line":
        r = P(text, rule)
        if r: return ("role_alias", RoleAlias(short_name=_qs(r.get("short","")), full_name=_qs(r.get("full","")), line=ln))
        qs = _eqs(text); return ("role_alias", RoleAlias(short_name=qs[0] if qs else "", full_name=qs[1] if len(qs)>1 else "", line=ln))
    if rule == "content_header":
        r = P(text, rule); n = _qs(r.get("name","")) if r else _fq(text)
        sg = n.rstrip("s") if n.endswith("s") else n
        return ("content_header", Content(name=n, singular=sg, line=ln))
    if rule == "field_line":
        r = P(text, rule)
        if r: sg = str(r.get("singular","")).strip(); fn = str(r.get("field_name","")).strip()
        else:
            hi = text.find(" has ")
            if hi < 0: return ("field", Field(name="unknown", type_expr=TypeExpr(base_type="text"), line=ln), "")
            sg = text[len("Each "):hi].strip(); ah = text[hi+len(" has "):].strip()
            for a in ("a ","an ","the "):
                if ah.startswith(a): ah = ah[len(a):]; break
            wi = ah.find(" which ")
            if wi < 0: return ("field", Field(name="unknown", type_expr=TypeExpr(base_type="text"), line=ln), sg)
            fn = ah[:wi].strip()
        wi = text.find(" which ")
        te = _parse_field_type(text[wi+len(" which "):].strip(), ln) if wi >= 0 else TypeExpr(base_type="text", line=ln)
        return ("field", Field(name=fn, type_expr=te, line=ln), sg)
    if rule == "access_line":
        r = P(text, rule)
        if r: return ("access", _build_access(r, ln))
        sc = _fq(text); ci = text.find(" can ")
        if ci >= 0:
            rest = text[ci+5:].strip()
            known = {"view", "create", "update", "delete"}
            words = rest.replace(",", " ").split()
            verbs = []
            for w in words:
                w = w.strip()
                if w in known:
                    verbs.append(w)
                elif w in ("or", "and"):
                    continue
                else:
                    break
            if not verbs:
                verbs = ["view"]
            return ("access", AccessRule(scope=sc, verbs=verbs, line=ln))
        return ("access", AccessRule(scope=sc, verbs=["view"], line=ln))
    if rule == "state_header":
        r = P(text, rule)
        if r:
            tgt = _qs(r.get("target",""))
            mn = _qs(r.get("name",""))
        else:
            mn = _fq(text)
            ci = text.find(" called ")
            tgt = text[len("State for "):ci].strip() if ci >= 0 else ""
            for prefix in ("channel ", "compute ", "boundary "):
                if tgt.startswith(prefix):
                    tgt = tgt[len(prefix):].strip().strip('"')
                    break
        return ("state_header", StateMachine(content_name=tgt, machine_name=mn, singular="", initial_state="", line=ln))
    if rule == "state_starts_line":
        r = P(text, rule)
        if r: return ("state_starts", str(r.get("singular","")).strip(), _qs(r.get("state","")))
        st = _fq(text); si = text.find(" starts as "); b = text[:si].strip() if si>=0 else ""
        for a in ("A ","An "):
            if b.startswith(a): b = b[len(a):]
        return ("state_starts", b.strip(), st)
    if rule == "state_also_line":
        r = P(text, rule); return ("state_also", _ql(r.get("states")) if r else _eqs(text))
    if rule == "state_transition_line":
        t = _build_trans(text, ln); return ("state_transition", t) if t else None
    if rule == "transition_feedback_line":
        return ("transition_feedback", _build_feedback(text, ln))
    if rule == "event_expr_line":
        r = P(text, rule); j = _qs(r.get("cel","")) if r else (_eb(text) or "")
        return ("event_header", EventRule(content_name="", trigger="expr", condition_expr=j, line=ln))
    if rule == "event_v1_line":
        return ("event_header", _build_ev1(text, ln))
    if rule == "event_action_line":
        r = P(text, rule)
        if r: return ("event_action", EventAction(create_content=_qs(r.get("name","")), fields=_cl(r.get("fields")), line=ln))
        rest = text[len("Create "):].strip()
        for a in ("a ","an ","the "):
            if rest.startswith(a): rest = rest[len(a):]; break
        wi = rest.find(" with ")
        if wi >= 0:
            n = rest[:wi].strip().strip('"'); ft = rest[wi+6:].strip()
            if ft.startswith("the "): ft = ft[4:]
            return ("event_action", EventAction(create_content=n, fields=_scal(ft), line=ln))
        return ("event_action", EventAction(create_content=rest.strip().strip('"'), line=ln))
    if rule == "event_send_line":
        r = P(text, rule)
        if r:
            content = str(r.get("content","")).strip()
            channel = _qs(r.get("channel",""))
        else:
            parts = text[len("Send "):].strip()
            if " to " in parts:
                content, channel = parts.split(" to ", 1)
                content = content.strip()
                channel = channel.strip().strip('"')
            else:
                content = parts; channel = ""
        return ("event_action", EventAction(send_content=content, send_channel=channel, line=ln))
    if rule == "log_level_line":
        r = P(text, rule); return ("log_level", str(r.get("level","")).strip() if r else text.split(":",1)[1].strip())
    if rule == "error_from_line":
        r = P(text, rule)
        if r: src = _qs(r.get("source","")); j = _qs(r.get("cel","")) if r.get("cel") else None
        else: src = _fq(text); j = _eb(text)
        return ("error_header", ErrorHandler(source=src, condition_expr=j, line=ln))
    if rule == "error_catch_all_line":
        return ("error_header", ErrorHandler(source="", is_catch_all=True, line=ln))
    if rule == "error_retry_line":
        r = P(text, rule)
        if r: cnt = _si(r.get("count"),1); md = str(r.get("max_delay","")).strip() if r.get("max_delay") else None
        else:
            cnt = 1; md = None
            for p in text.split():
                if p.isdigit(): cnt = int(p); break
        return ("error_retry", ErrorAction(kind="retry", retry_count=cnt, retry_backoff="backoff" in text.lower(), retry_max_delay=md, line=ln))
    if rule == "error_then_line":
        return ("error_then", _build_err_act(text, ln))
    if rule == "story_header":
        return ("story_header", _build_story(text, ln))
    if rule == "so_that_line":
        r = P(text, rule); return ("so_that", str(r.get("text","")).strip().rstrip(":") if r else text[len("so that "):].strip().rstrip(":"))
    if rule == "show_page_line":
        r = P(text, rule); return ("directive", ShowPage(page_name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "chat_line":
        r = P(text, rule)
        if r:
            source = str(r.get("source", "")).strip()
            if r.get("role_field") is not None:
                role_field = _qs(r.get("role_field", ""))
                content_field = _qs(r.get("content_field", ""))
                return ("directive", ChatDirective(source=source, role_field=role_field, content_field=content_field, line=ln))
            else:
                return ("directive", ChatDirective(source=source, line=ln))
        rest = text[len("Show a chat for "):].strip()
        wi = rest.find(" with role ")
        if wi >= 0:
            source = rest[:wi].strip()
            mapping = rest[wi:]
            role_field = _fq(mapping.split(",")[0]) if "," in mapping else "role"
            content_field = ""
            ci = mapping.find(', content "')
            if ci >= 0:
                content_field = _fq(mapping[ci+10:])
            return ("directive", ChatDirective(source=source, role_field=role_field or "role", content_field=content_field or "content", line=ln))
        return ("directive", ChatDirective(source=rest, line=ln))
    if rule == "display_table_line":
        rest = text[len("Display a table of "):].strip(); wi = rest.find(" with columns:")
        cn = rest[:wi].strip() if wi>=0 else rest.strip()
        cols = _scal(rest[wi+14:]) if wi>=0 else []
        return ("directive", DisplayTable(content_name=cn, columns=cols, line=ln))
    if rule == "show_related_line":
        rest = text[len("For each "):].strip(); ci = rest.find(",")
        if ci < 0: return ("directive", ShowRelated(singular=rest, line=ln))
        sg = rest[:ci].strip(); af = rest[ci+1:].strip()
        if af.lower().startswith("show "): af = af[5:].strip()
        gi = af.find(" grouped by ")
        if gi < 0: return ("directive", ShowRelated(singular=sg, related_content=af, line=ln))
        return ("directive", ShowRelated(singular=sg, related_content=af[:gi].strip(), group_by=af[gi+12:].strip(), line=ln))
    if rule == "mark_rows_line":
        cel = _eb(text) or ""
        label = ""
        if ' as "' in text:
            label = text.rsplit(' as "', 1)[1].rstrip('"').strip()
        elif " as '" in text:
            label = text.rsplit(" as '", 1)[1].rstrip("'").strip()
        scope = "row"
        if text.startswith("Mark ") and not text.startswith("Mark rows"):
            after_mark = text[len("Mark "):].strip()
            if " where " in after_mark:
                scope = after_mark.split(" where ", 1)[0].strip()
        return ("directive", MarkAs(condition_expr=cel, label=label, scope=scope, line=ln))
    if rule == "highlight_rows_line":
        rest = text[len("Highlight rows where "):].strip(); j = _eb(rest)
        if j: return ("directive", HighlightRows(condition_expr=j, line=ln))
        for op in (" is at or below "," is above "," is below "," is equal to "):
            idx = rest.find(op)
            if idx >= 0: return ("directive", HighlightRows(field=rest[:idx].strip(), operator=op.strip()[3:],
                                                             threshold_field=rest[idx+len(op):].strip(), line=ln))
        return ("directive", HighlightRows(line=ln))
    if rule == "allow_filtering_line":
        r = P(text, rule); return ("directive", AllowFilter(fields=_cl(r.get("fields")) if r else _scal(text[len("Allow filtering by "):]), line=ln))
    if rule == "allow_searching_line":
        r = P(text, rule)
        fs = _ol(r.get("fields")) if r else []
        if len(fs) == 1 and " or " in fs[0]:
            fs = [f.strip() for f in fs[0].split(" or ") if f.strip()]
        if not fs:
            rest = text[len("Allow searching by "):].strip()
            fs = [f.strip() for f in rest.split(" or ") if f.strip()]
        return ("directive", AllowSearch(fields=fs, line=ln))
    if rule == "allow_inline_editing_line":
        r = P(text, rule)
        fs = _cl(r.get("fields")) if r else _scal(
            text[len("Allow inline editing of "):])
        return ("directive", AllowInlineEdit(fields=fs, line=ln))
    if rule == "link_column_line":
        r = P(text, rule)
        if r:
            col = _qs(r.get("col", ""))
            template = _qs(r.get("template", ""))
        else:
            parts = _eqs(text)
            col = parts[0] if parts else ""
            template = parts[1] if len(parts) > 1 else ""
        return ("directive", LinkColumn(column=col, link_template=template, line=ln))
    if rule == "subscribes_to_line":
        rest = text[len("This table subscribes to "):].strip()
        if rest.endswith(" changes"): rest = rest[:-8].strip()
        return ("directive", SubscribeTo(content_name=rest, line=ln))
    if rule == "accept_input_line":
        r = P(text, rule); return ("directive", AcceptInput(fields=_cl(r.get("fields")) if r else _scal(text[len("Accept input for "):]), line=ln))
    if rule == "validate_unique_line":
        rest = text[len("Validate that "):].strip()
        if rest.startswith("["):
            be = rest.find("]"); return ("directive", ValidateUnique(condition_expr=rest[1:be].strip() if be>0 else rest[1:].strip(), line=ln))
        ii = rest.find(" is unique"); return ("directive", ValidateUnique(field=rest[:ii].strip() if ii>=0 else rest.strip(), line=ln))
    if rule == "create_as_line":
        rest = text[len("Create the "):].strip(); ai = rest.rfind(" as ")
        return ("directive", CreateAs(initial_state=rest[ai+4:].strip() if ai>=0 else "", line=ln))
    if rule == "after_saving_line":
        r = P(text, rule); return ("directive", AfterSave(instruction=str(r.get("text","")).strip() if r else text[len("After saving, "):].strip(), line=ln))
    if rule == "show_chart_line":
        rest = text[len("Show a chart of "):].strip(); oi = rest.find(" over the past ")
        if oi < 0: return ("directive", ShowChart(content_name=rest, days=30, line=ln))
        cn = rest[:oi].strip(); af = rest[oi+15:].strip(); sp = af.find(" ")
        return ("directive", ShowChart(content_name=cn, days=_si(af[:sp] if sp>0 else af, 30), line=ln))
    if rule == "display_text_line":
        r = P(text, rule)
        if r:
            if r.get("cel"): return ("directive", DisplayText(text=_qs(r["cel"]), is_expression=True, line=ln))
            if r.get("text"): return ("directive", DisplayText(text=_qs(r["text"]), line=ln))
            if r.get("expr"): return ("directive", DisplayText(text=str(r["expr"]).strip(), is_expression=True, line=ln))
        rest = text[len("Display text"):].strip(); j = _eb(rest)
        if j: return ("directive", DisplayText(text=j, is_expression=True, line=ln))
        q = _fq(rest)
        if q: return ("directive", DisplayText(text=q, line=ln))
        return ("directive", DisplayText(text=rest.strip(), is_expression=True, line=ln))
    if rule == "structured_agg_line":
        r = P(text, rule)
        if r:
            content = str(r.get("content","")).strip()
            if r.get("field"):
                return ("directive", StructuredAggregation(agg_type="count", source_content=content,
                                                            group_by=str(r["field"]).strip(), line=ln))
            if r.get("func"):
                func = str(r["func"]).strip()
                expr_val = _qs(r.get("expr","")) if r.get("expr") else None
                fmt = str(r.get("format","number")).strip() if r.get("format") else "number"
                return ("directive", StructuredAggregation(agg_type=func, source_content=content, expression=expr_val, format=fmt, line=ln))
            return ("directive", StructuredAggregation(agg_type="count", source_content=content, line=ln))
        rest = text[len("Display "):].strip()
        if rest.lower().startswith("count of"):
            content = rest[len("count of"):].strip()
            gi = content.lower().find(" grouped by ")
            if gi >= 0:
                return ("directive", StructuredAggregation(agg_type="count", source_content=content[:gi].strip(),
                                                            group_by=content[gi+12:].strip(), line=ln))
            return ("directive", StructuredAggregation(agg_type="count", source_content=content, line=ln))
        return ("directive", DisplayAggregation(description=rest, line=ln))
    if rule == "section_header_line":
        r = P(text, rule)
        title = _qs(r.get("title","")) if r else ""
        if not title:
            title = _fq(text[len("Section "):].strip().rstrip(":")) or text[len("Section "):].strip().rstrip(":")
        return ("directive", SectionStart(title=title, line=ln))
    if rule == "action_header_line":
        r = P(text, rule)
        singular = str(r.get("singular","")).strip() if r else ""
        if not singular:
            rest = text[len("For each "):].strip()
            ci = rest.find(",")
            singular = rest[:ci].strip() if ci >= 0 else ""
        return ("directive", ActionHeader(singular=singular, line=ln))
    if rule == "action_button_line":
        # TatSu 5.15.1 does not populate parseinfo.rule for #Name-tagged
        # alternatives under rule_name= dispatch, so we cannot use
        # _rule(r) to discriminate the six alternatives. Instead, we
        # inspect the source text (which is reliable) and use TatSu only
        # to extract the label/state content.
        r = P(text, rule)
        lower_text = text.lower()
        has_transitions = " transitions to " in lower_text
        is_delete = " deletes" in lower_text and not has_transitions
        is_edit = " edits" in lower_text and not has_transitions
        if is_delete:
            kind = "delete"
        elif is_edit:
            kind = "edit"
        else:
            kind = "transition"
        behavior = "hide" if "hide otherwise" in lower_text else "disable"
        if r:
            label = _qs(r.get("label",""))
            state = "" if (is_delete or is_edit) else _qs(r.get("state",""))
        else:
            # Full text fallback.
            parts = text.strip()
            label = _fq(parts) or ""
            state = ""
            if kind == "transition":
                si = lower_text.find("transitions to ")
                if si >= 0:
                    rest = parts[si+len("transitions to "):]
                    state = _fq(rest) or (rest.split()[0] if rest else "")
        return ("directive", ActionButtonDef(
            label=label, target_state=state,
            unavailable_behavior=behavior, kind=kind, line=ln))
    if rule == "display_agg_line":
        r = P(text, rule); return ("directive", DisplayAggregation(description=str(r.get("text","")).strip() if r else text[len("Display "):].strip(), line=ln))
    if rule == "nav_bar_line": return ("nav_bar",)
    if rule == "nav_item_line": return ("nav_item", _build_nav(text, ln))
    if rule == "stream_line":
        rest = text[len("Stream "):].strip(); ai = rest.rfind(" at ")
        if ai < 0: return ("stream", Stream(description=rest, path="", line=ln))
        return ("stream", Stream(description=rest[:ai].strip(), path=rest[ai+4:].strip(), line=ln))
    if rule == "compute_header":
        r = P(text, rule); return ("compute_header", ComputeNode(name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "compute_shape_line": return ("compute_shape", _build_comp_shape(text))
    if rule == "compute_body_expr_line": return ("compute_body", text[1:-1].strip())
    if rule == "compute_body_multiline": return ("compute_body_multiline", text[3:-3].strip())
    if rule == "compute_access_line":
        r = P(text, rule)
        if r:
            val = _qs(r.get("role",""))
            if _rule(r) == "ComputeAccessAnyone" or text.startswith("Anyone with"):
                return ("access", AccessRule(scope=val, verbs=["execute"], line=ln))
            return ("compute_access", val)
        ci = text.find(" can execute this")
        raw = text[:ci].strip().strip('"') if ci>=0 else ""
        if text.startswith("Anyone with"):
            return ("access", AccessRule(scope=_fq(text), verbs=["execute"], line=ln))
        return ("compute_access", raw)
    if rule == "compute_audit_access_line":
        r = P(text, "compute_audit_access_line")
        if r:
            scope = _qs(r.get("scope", ""))
        else:
            scope = _fq(text)
        return ("compute_audit_access", scope)
    if rule == "compute_identity_line":
        r = P(text, rule)
        mode = str(r.get("mode", "")).strip().lower() if r else text.split(":", 1)[1].strip().lower()
        return ("compute_identity", mode)
    if rule == "compute_requires_conf_line":
        r = P(text, rule)
        scopes = _ql(r.get("scopes")) if r else _eqs(text)
        return ("compute_requires_conf", scopes)
    if rule == "compute_output_conf_line":
        r = P(text, rule)
        scope = _qs(r.get("scope", "")) if r else _fq(text)
        return ("compute_output_conf", scope)
    if rule == "compute_provider_line":
        r = P(text, rule)
        provider = _qs(r.get("provider", "")) if r else _fq(text)
        return ("compute_provider", provider)
    if rule == "compute_trigger_line":
        rest = text[len("Trigger on "):].strip()
        where_expr = None
        if " where " in rest:
            trigger_part, where_part = rest.split(" where ", 1)
            rest = trigger_part.strip()
            where_expr = _eb(where_part)
        return ("compute_trigger", rest, where_expr)
    if rule == "compute_preconditions_line":
        return ("compute_preconditions_header",)
    if rule == "compute_postconditions_line":
        return ("compute_postconditions_header",)
    if rule == "compute_objective_line":
        rest = text[len("Objective is "):].strip()
        if rest.startswith("```") and rest.endswith("```"):
            rest = rest[3:-3].strip()
        return ("compute_objective", rest)
    if rule == "compute_strategy_line":
        rest = text[len("Strategy is "):].strip()
        if rest.startswith("```") and rest.endswith("```"):
            rest = rest[3:-3].strip()
        return ("compute_strategy", rest)
    if rule == "compute_directive_line":
        rest = text[len("Directive is "):].strip()
        if rest.startswith("```") and rest.endswith("```"):
            rest = rest[3:-3].strip()
        return ("compute_directive", rest)
    if rule == "compute_accesses_line":
        rest = text[len("Accesses "):].strip()
        items = [w.strip().strip('"') for w in rest.replace(" and ", ",").split(",") if w.strip()]
        return ("compute_accesses", items)
    if rule == "compute_input_field_line":
        rest = text[len("Input from field "):].strip()
        if "." in rest:
            parts = rest.split(".", 1)
            return ("compute_input_field", (parts[0].strip(), parts[1].strip()))
        return ("compute_input_field", (rest, ""))
    if rule == "compute_output_field_line":
        rest = text[len("Output into field "):].strip()
        if "." in rest:
            parts = rest.split(".", 1)
            return ("compute_output_field", (parts[0].strip(), parts[1].strip()))
        return ("compute_output_field", (rest, ""))
    if rule == "compute_output_creates_line":
        rest = text[len("Output creates "):].strip()
        return ("compute_output_creates", rest.strip().strip('"'))
    if rule == "content_scoped_line":
        r = P(text, rule)
        scopes = _ql(r.get("scopes")) if r else _eqs(text)
        return ("content_scoped", scopes)
    if rule == "content_audit_line":
        r = P(text, rule)
        level = str(r.get("level", "content")).strip().lower() if r else text.split(":", 1)[1].strip().lower()
        return ("content_audit", level)
    if rule == "content_when_line":
        return _parse_content_when(text, ln)
    if rule == "unconditional_constraint_line":
        return _parse_unconditional_constraint(text, ln)
    if rule == "channel_header":
        r = P(text, rule); return ("channel_header", ChannelDecl(name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "channel_carries_line":
        r = P(text, rule); return ("channel_prop", "carries", str(r.get("content","")).strip() if r else text[len("Carries "):].strip())
    if rule == "channel_direction_line":
        r = P(text, rule); return ("channel_prop", "direction", str(r.get("dir","")).strip().lower() if r else text.split(":",1)[1].strip().lower())
    if rule == "channel_delivery_line":
        r = P(text, rule); return ("channel_prop", "delivery", str(r.get("del","")).strip().lower() if r else text.split(":",1)[1].strip().lower())
    if rule == "channel_requires_line":
        r = P(text, rule)
        if r:
            direction = str(r.get("dir","")).strip()
            return ("channel_prop", "requires", ChannelRequirement(scope=_qs(r.get("scope","")), direction=direction, line=ln))
        direction = "send" if " to send" in text else ("invoke" if " to invoke" in text else "receive")
        return ("channel_prop", "requires", ChannelRequirement(scope=_fq(text), direction=direction, line=ln))
    if rule == "channel_endpoint_line":
        r = P(text, rule); return ("channel_prop", "endpoint", str(r.get("path","")).strip() if r else text.split(":",1)[1].strip())
    if rule == "action_header":
        r = P(text, rule); return ("action_header", ActionDecl(name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "action_takes_line":
        params = _parse_action_params(text, "Takes ", ln)
        return ("action_prop", "takes", params)
    if rule == "action_returns_line":
        params = _parse_action_params(text, "Returns ", ln)
        return ("action_prop", "returns", params)
    if rule == "action_requires_line":
        r = P(text, rule)
        scope = _qs(r.get("scope","")) if r else _fq(text)
        return ("action_prop", "requires", scope)
    if rule == "boundary_header":
        r = P(text, rule); return ("boundary_header", BoundaryDecl(name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "boundary_contains_line":
        r = P(text, rule); return ("boundary_prop", "contains", _cl(r.get("items_") or r.get("items")) if r else _scal(text[len("Contains "):]))
    if rule == "boundary_inherits_line":
        r = P(text, rule); return ("boundary_prop", "inherits", str(r.get("parent","")).strip() if r else text[len("Identity inherits from "):].strip())
    if rule == "boundary_restricts_line":
        r = P(text, rule); return ("boundary_prop", "restricts", _ql(r.get("scopes")) if r else _eqs(text))
    if rule == "boundary_exposes_line":
        r = P(text, rule)
        if r: return ("boundary_prop", "exposes", BoundaryProperty(name=_qs(r.get("name","")),
                          type_name=str(r.get("type_name","")).strip(), expr=_qs(r.get("cel","")), line=ln))
        n = _fq(text); j = _eb(text)
        ci = text.find(":", text.find('"', text.find('"')+1)+1); ei = text.find("=")
        tn = text[ci+1:ei].strip() if ci>=0 and ei>ci else ""
        return ("boundary_prop", "exposes", BoundaryProperty(name=n, type_name=tn, expr=j or "", line=ln))
    return None
