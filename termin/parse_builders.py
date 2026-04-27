# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""AST builder functions — construct AST nodes from parse results.

Each _build_* function takes a TatSu parse result (or raw text + line number)
and returns an AST node. Includes fallback paths for TatSu context state leaks.
"""
from __future__ import annotations
import re
from typing import Optional

from .ast_nodes import (
    AccessRule, UserStory, ShowPage, NavItem, TransitionFeedback,
    Transition, EventRule, EventCondition, ErrorAction,
    ComputeParam, ActionParam, DependentValue,
)
from .parse_helpers import (
    _try_parse, _rule, _qs, _ql, _fq, _eb, _eqs, _scal,
    _parse_literal_list,
)


def _build_access(r, ln) -> AccessRule:
    """Build an AccessRule from a parsed access_line result."""
    scope = _qs(r.get("scope", ""))
    vr = r.get("verbs")
    rn = _rule(vr)
    # New grammar: VerbListTwo/Three/Four have v1..vN keys, VerbSingle has verb key
    if rn == "VerbSingle":
        vs = [str(vr.get("verb", "view")).strip()]
    elif rn in ("VerbListTwo", "VerbListThree", "VerbListFour"):
        vs = [str(vr[k]).strip() for k in sorted(vr) if k.startswith("v")]
    else:
        # Legacy fallback: handle old-style results or string values
        if isinstance(vr, dict):
            vs = [str(vr[k]).strip() for k in sorted(vr) if k.startswith("v")]
            if not vs:
                v = str(vr.get("verb", "view")).strip()
                vs = [v]
        elif isinstance(vr, str):
            vs = [vr.strip()]
        else:
            vs = ["view"]
    # v0.9 Phase 6a.3: detect `their own <content>` row-filter qualifier.
    # The grammar's rest_of_line captures everything after the verb phrase
    # as content_name. When source uses `their own sessions`, content_name
    # ends up as "their own sessions"; we strip the qualifier and flag the
    # AccessRule so lowering can attach a row_filter to the resulting
    # routes. Per BRD #3 §3.4.
    cn = str(r.get("content_name", "")).strip()
    their_own = False
    cn_lower = cn.lower()
    if cn_lower.startswith("their own "):
        their_own = True
    return AccessRule(scope=scope, verbs=vs, their_own=their_own, line=ln)


def _build_story(text, ln) -> UserStory:
    """Build a UserStory from a story_header line."""
    r = _try_parse(text, "story_header")
    if r is not None:
        role = str(r.get("role", "")).strip()
        ar = r.get("action"); at, obj, pn = "", "", None
        if isinstance(ar, dict):
            st = ar.get("so_that")
            if st: obj = str(st).strip().rstrip(":")
            pg = ar.get("page")
            if pg: pn = _qs(pg); at = "see a page"
            else:
                at = str(ar.get("text", "")).strip()
                si = at.find(" so that ")
                if si >= 0 and not obj: obj = at[si+9:].strip().rstrip(":"); at = at[:si].strip()
        elif isinstance(ar, str): at = ar.strip()
        s = UserStory(role=role, action=at, objective=obj, line=ln)
        if pn: s.directives.append(ShowPage(page_name=pn, line=ln))
        return s
    rest = text[len("As "):].strip()
    for a in ("a ", "an ", "the "):
        if rest.startswith(a): rest = rest[len(a):]; break
    mk = ", I want to "; idx = rest.find(mk)
    if idx < 0: return UserStory(role=rest, action="", objective="", line=ln)
    role = rest[:idx].strip(); at = rest[idx+len(mk):].strip(); obj = ""
    si = at.find(" so that ")
    if si >= 0: obj = at[si+9:].strip().rstrip(":"); at = at[:si].strip()
    pn = _fq(at) if at.startswith("see a page ") else None
    s = UserStory(role=role, action=at, objective=obj, line=ln)
    if pn: s.directives.append(ShowPage(page_name=pn, line=ln))
    return s


def _build_nav(text, ln) -> NavItem:
    """Build a NavItem from a nav_item_line."""
    r = _try_parse(text, "nav_item_line")
    if r: label, page, rt = _qs(r.get("label","")), _qs(r.get("page","")), str(r.get("rest","")).strip()
    else:
        qs = _eqs(text); label = qs[0] if qs else ""; page = qs[1] if len(qs)>1 else ""; rt = text
    vis, badge = [], None
    vi = rt.find("visible to ")
    if vi >= 0:
        vt = rt[vi+11:]; bi = vt.find(", badge:")
        if bi >= 0: badge = vt[bi+8:].strip(); vt = vt[:bi]
        vis = _scal(vt)
    return NavItem(label=label, page_name=page, visible_to=vis, badge=badge, line=ln)


def _build_feedback(text, ln) -> TransitionFeedback:
    """Parse a transition feedback line: success/error shows toast/banner message."""
    r = _try_parse(text, "transition_feedback_line")
    if r is not None:
        trigger = str(r.get("trigger", "")).strip()
        style = str(r.get("style", "")).strip()
        raw_msg = r.get("message")
        msg = str(raw_msg["content"]).strip() if isinstance(raw_msg, dict) and "content" in raw_msg else _qs(raw_msg) if raw_msg else ""
        style_idx = text.find(style)
        after_style = text[style_idx + len(style):].strip() if style_idx >= 0 else ""
        is_expr = after_style.startswith("`")
        dismiss = r.get("dismiss")
        dismiss_seconds = int(dismiss["seconds"]) if dismiss else None
        return TransitionFeedback(trigger=trigger, style=style, message=msg,
                                  is_expr=is_expr, dismiss_seconds=dismiss_seconds, line=ln)
    # Fallback: manual parse
    trigger = "success" if text.startswith("success") else "error"
    si = text.find(" shows ")
    rest = text[si + 7:].strip() if si >= 0 else text
    style = "toast" if rest.startswith("toast") else "banner"
    rest = rest[len(style):].strip()
    if rest.startswith("`"):
        end = rest.find("`", 1)
        msg = rest[1:end] if end > 0 else rest[1:]
        is_expr = True
        rest = rest[end + 1:].strip() if end > 0 else ""
    else:
        msg = _fq(rest)
        is_expr = False
        qi = rest.find('"', rest.find('"') + 1 + len(msg))
        rest = rest[qi + 1:].strip() if qi >= 0 else ""
    dismiss_seconds = None
    if "dismiss after" in rest:
        dm = re.search(r'dismiss\s+after\s+(\d+)\s+seconds?', rest)
        if dm:
            dismiss_seconds = int(dm.group(1))
    return TransitionFeedback(trigger=trigger, style=style, message=msg,
                              is_expr=is_expr, dismiss_seconds=dismiss_seconds, line=ln)


def _build_trans(text, ln) -> Optional[Transition]:
    """Build a Transition from a state_transition_line."""
    r = _try_parse(text, "state_transition_line")
    if r is not None:
        return Transition(from_state=str(r.get("from_state","")).strip(),
                          to_state=str(r.get("to_state","")).strip(),
                          required_scope=_qs(r.get("scope","")), line=ln)
    rest = text.strip()
    for a in ("A ", "An "):
        if rest.startswith(a): rest = rest[len(a):]; break
    ci = rest.find(" can become ")
    if ci < 0: return None
    bc = rest[:ci].strip(); ac = rest[ci+12:].strip()
    parts = bc.rsplit(" ", 1); fs = parts[0] if len(parts)>1 else bc
    ii = ac.find(" if the user has ")
    if ii >= 0:
        ts = ac[:ii].strip()
        if ts.endswith(" again"): ts = ts[:-6].strip()
        return Transition(from_state=fs, to_state=ts, required_scope=_fq(ac[ii:]), line=ln)
    ii = ac.find(" if ")
    if ii >= 0:
        ts = ac[:ii].strip()
        if ts.endswith(" again"): ts = ts[:-6].strip()
        return Transition(from_state=fs, to_state=ts, required_scope="", line=ln)
    return Transition(from_state=fs, to_state=ac.strip(), required_scope="", line=ln)


def _build_ev1(text, ln) -> EventRule:
    """Build an EventRule from an event_v1_line."""
    r = _try_parse(text, "event_v1_line")
    if r is not None:
        c = str(r.get("content","")).strip(); t = str(r.get("trigger","")).strip()
        ev = EventRule(content_name=c, trigger=t, line=ln)
        cond = r.get("condition")
        if cond and isinstance(cond, dict):
            f1 = str(cond.get("field1","")).strip()
            om = {"OpAtOrBelow":"at or below","OpAbove":"above","OpBelow":"below","OpEqualTo":"equal to"}
            op_raw = cond.get("op"); op = om.get(_rule(op_raw), str(op_raw).strip() if op_raw else "")
            ev.condition = EventCondition(field1=f1, operator=op, field2="", line=ln)
            mk = " is "+op+" its "; mi = text.find(mk)
            if mi >= 0: ev.condition.field2 = text[mi+len(mk):].strip().rstrip(":")
        return ev
    rest = text[len("When "):].strip().rstrip(":")
    for a in ("a ","an ","the "):
        if rest.startswith(a): rest = rest[len(a):]; break
    for trig in ("created","updated","deleted"):
        idx = rest.find(" is "+trig)
        if idx >= 0:
            ev = EventRule(content_name=rest[:idx].strip(), trigger=trig, line=ln)
            after = rest[idx+4+len(trig):].strip()
            if after.startswith("and its "):
                _parse_ev_cond(ev, after[8:].strip())
            return ev
    return EventRule(content_name=rest, trigger="unknown", line=ln)


def _parse_ev_cond(ev, text):
    """Parse an event condition clause: 'field is op its other_field'."""
    for op in ("at or below","above","below","equal to"):
        mk = " is "+op+" its "; idx = text.find(mk)
        if idx >= 0:
            ev.condition = EventCondition(field1=text[:idx].strip(), operator=op,
                                          field2=text[idx+len(mk):].strip(), line=ev.line)
            return


def _build_err_act(text, ln) -> ErrorAction:
    """Build an ErrorAction from an error_then_line."""
    r = _try_parse(text, "error_then_line")
    if r is not None:
        ar = r.get("action"); rn = _rule(ar)
        if rn == "ActionDisable": return ErrorAction(kind="disable", target=str(ar.get("target","")).strip(), line=ln)
        if rn == "ActionEscalate" or ar == "escalate": return ErrorAction(kind="escalate", line=ln)
        if rn == "ActionNotify": return ErrorAction(kind="notify", target=_qs(ar.get("role","")),
                                                     expr=_qs(ar.get("expr","")), line=ln)
        if rn == "ActionCreate": return ErrorAction(kind="create", target=_qs(ar.get("name","")), line=ln)
        if rn == "ActionSet": return ErrorAction(kind="set", expr=_qs(ar.get("expr","")), line=ln)
    rest = text[len("Then "):].strip()
    if rest.startswith("disable "): return ErrorAction(kind="disable", target=rest[len("disable "):].strip(), line=ln)
    if rest == "escalate": return ErrorAction(kind="escalate", line=ln)
    if rest.startswith("notify "): return ErrorAction(kind="notify", target=_fq(rest), expr=_eb(rest) or "", line=ln)
    if rest.startswith("create "): return ErrorAction(kind="create", target=_fq(rest), line=ln)
    if rest.startswith("set "): return ErrorAction(kind="set", expr=_eb(rest) or "", line=ln)
    return ErrorAction(kind="unknown", target=rest, line=ln)


def _build_comp_shape(text) -> tuple:
    """Parse Compute shape line: 'Transform: takes X, produces Y'."""
    ci = text.find(":")
    if ci < 0: return ("transform", [], [], [], [])
    shape = text[:ci].strip().lower(); rest = text[ci+1:].strip()
    ins, outs, ip, op = [], [], [], []
    ti = rest.find("takes "); pi = rest.find("produces ")
    if ti >= 0 and pi >= 0:
        tt = rest[ti+6:pi].strip().rstrip(",").strip(); pt = rest[pi+9:].strip()
        for a in ("a ","an ","the "):
            if tt.startswith(a): tt = tt[len(a):]
            if pt.startswith(a): pt = pt[len(a):]
        if " : " in tt or ": " in tt:
            ip = _parse_tp(tt)
            ins = [p.type_name for p in ip] if ip and ip[0].type_name else [p.name for p in ip]
        else: ins = [i.strip() for i in tt.replace(" and ",",").split(",") if i.strip()]
        if pt.startswith("one of "):
            outs = [o.strip() for o in pt[7:].replace(" or ",",").split(",") if o.strip()]
        elif " : " in pt or ": " in pt:
            op = _parse_tp(pt)
            outs = [p.type_name for p in op] if op and op[0].type_name else [p.name for p in op]
        else: outs = [o.strip() for o in pt.replace(" and ",",").split(",") if o.strip()]
    return (shape, ins, outs, ip, op)


def _parse_tp(text) -> list[ComputeParam]:
    """Parse compute parameter text: 'X : type' or 'X'."""
    result = []
    for part in text.replace(" and ",",").split(","):
        p = part.strip()
        if not p: continue
        for a in ("a ","an ","the "):
            if p.startswith(a): p = p[len(a):]
        ci = p.find(":")
        if ci > 0: result.append(ComputeParam(name=p[:ci].strip().strip('"'), type_name=p[ci+1:].strip()))
        else: result.append(ComputeParam(name=p.strip(), type_name=""))
    return result


def _parse_action_params(text: str, prefix: str, ln: int) -> list[ActionParam]:
    """Parse 'Takes role which is text, policy which is text' into ActionParam list."""
    body = text[len(prefix):].strip()
    params = []
    for part in body.split(","):
        part = part.strip()
        if not part:
            continue
        if " which is " in part:
            name, type_name = part.split(" which is ", 1)
            params.append(ActionParam(name=name.strip(), type_name=type_name.strip(), line=ln))
        else:
            params.append(ActionParam(name=part.strip(), type_name="text", line=ln))
    return params


def _parse_content_when(text: str, ln: int):
    """Parse a content When clause: When `expr`, field must be one of: / must be / defaults to."""
    cond = _eb(text[len("When "):])
    if not cond:
        return None

    when_prefix_len = len("When ")
    bt_close = text.find("`", when_prefix_len + 1) if "`" in text[when_prefix_len:when_prefix_len + 2] else text.find("]", when_prefix_len + 1)
    if bt_close < 0:
        return None
    comma_pos = text.find(",", bt_close)
    if comma_pos < 0:
        return None
    rest = text[comma_pos + 1:].strip()

    if " must be one of:" in rest:
        field_part = rest[:rest.index(" must be one of:")].strip()
        values_text = rest[rest.index(" must be one of:") + len(" must be one of:"):].strip()
        values = _parse_literal_list(values_text)
        return ("dependent_value", DependentValue(
            when_expr=cond, field=field_part, constraint="one_of", values=values, line=ln))
    elif " must be " in rest:
        field_part = rest[:rest.index(" must be ")].strip()
        value_text = rest[rest.index(" must be ") + len(" must be "):].strip()
        values = _parse_literal_list(value_text)
        value = values[0] if values else value_text.strip().strip('"')
        return ("dependent_value", DependentValue(
            when_expr=cond, field=field_part, constraint="equals", values=[value], line=ln))
    elif " defaults to " in rest:
        field_part = rest[:rest.index(" defaults to ")].strip()
        value_text = rest[rest.index(" defaults to ") + len(" defaults to "):].strip()
        values = _parse_literal_list(value_text)
        value = values[0] if values else value_text.strip().strip('"')
        return ("dependent_value", DependentValue(
            when_expr=cond, field=field_part, constraint="default", values=[value], line=ln))
    return None


def _parse_unconditional_constraint(text: str, ln: int):
    """Parse an unconditional constraint: field must be one of: val1, val2."""
    idx = text.index(" must be one of:")
    field_part = text[:idx].strip()
    values_text = text[idx + len(" must be one of:"):].strip()
    values = _parse_literal_list(values_text)
    return ("dependent_value", DependentValue(
        when_expr=None, field=field_part, constraint="one_of", values=values, line=ln))
