"""TatSu PEG-based parser for the Termin DSL.

Two-level design:
  Level 1 (Python): Line classification by keyword + block assembly
  Level 2 (TatSu PEG): Per-line content parsing using termin.peg

Public API: parse_peg(source: str) -> tuple[Program, CompileResult]
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import tatsu

from .ast_nodes import (
    Program, Application, Identity, Role, RoleAlias, Content, Field, TypeExpr,
    AccessRule, StateMachine, Transition, EventRule, EventCondition,
    EventAction, UserStory, ShowPage, DisplayTable, ShowRelated,
    HighlightRows, AllowFilter, AllowSearch, SubscribeTo, AcceptInput,
    ValidateUnique, CreateAs, AfterSave, ShowChart, DisplayAggregation,
    StructuredAggregation, SectionStart, ActionHeader, ActionButtonDef,
    NavBar, NavItem, ApiSection, ApiEndpoint, Stream, Directive,
    ComputeNode, ComputeParam, ChannelDecl, ChannelRequirement, BoundaryDecl,
    BoundaryProperty, DisplayText, ErrorHandler, ErrorAction,
)
from .errors import ParseError, CompileResult

# --- Load TatSu grammar ---
_GRAMMAR_PATH = Path(__file__).parent / "termin.peg"
_model = tatsu.compile(_GRAMMAR_PATH.read_text(encoding="utf-8"))

# --- Preprocessor ---
def _preprocess(source: str) -> list[tuple[int, str]]:
    result = []
    in_multiline = False
    multiline_start = 0
    multiline_prefix = ""
    multiline_content = []
    for line_num, raw in enumerate(source.splitlines(), start=1):
        s = raw.strip()

        # Handle triple-backtick multi-line blocks
        if in_multiline:
            if s == "```" or s.endswith("```"):
                # Closing triple-backtick
                if s != "```":
                    multiline_content.append(s[:-3].rstrip())
                joined = "\n".join(multiline_content)
                # Emit as: prefix ```content```
                result.append((multiline_start, f'{multiline_prefix}```{joined}```'))
                in_multiline = False
                multiline_content = []
            else:
                multiline_content.append(raw.rstrip())
            continue

        if not s or s.startswith("---"):
            continue
        if s.startswith("(") and s.endswith(")"):
            continue

        # Check for triple-backtick opening (not closed on same line)
        triple_idx = s.find("```")
        if triple_idx >= 0:
            after = s[triple_idx + 3:]
            close_idx = after.find("```")
            if close_idx < 0:
                # Opens but doesn't close — start multi-line block
                in_multiline = True
                multiline_start = line_num
                multiline_prefix = s[:triple_idx]
                if after.strip():
                    multiline_content.append(after)
                continue
            # else: opens and closes on same line — pass through normally

        idx = s.find(" (")
        if idx > 0:
            tail = s[idx:]
            pc = tail.find(")")
            if pc > 0 and pc == len(tail) - 1:
                s = s[:idx].strip()
        result.append((line_num, s))
    return result

# --- Line classifier ---
_PREFIXES: list[tuple[str, str]] = [
    ("Application:", "application_line"), ("Description:", "description_line"), ("Id:", "id_line"),
    ("Users authenticate with", "identity_line"), ("Scopes are", "scopes_line"),
    ("Content called", "content_header"), ("Scoped to", "content_scoped_line"), ("Each ", "field_line"),
    ("Anyone with", "access_line"), ("State for", "state_header"),
    ("When `", "event_expr_line"), ("When [", "event_expr_line"),  # backtick first, bracket legacy
    ("When a ", "event_v1_line"), ("When an ", "event_v1_line"),
    ("Create a ", "event_action_line"), ("Create an ", "event_action_line"),
    ("Log level:", "log_level_line"), ("On error from", "error_from_line"),
    ("On any error:", "error_catch_all_line"), ("Retry ", "error_retry_line"),
    ("Then ", "error_then_line"), ("As ", "story_header"), ("so that ", "so_that_line"),
    ("Show a page called", "show_page_line"), ("Display a table of", "display_table_line"),
    ("For each ", "show_related_line"),  # also handles action_header_line — disambiguated in _classify_line
    ("Highlight rows where", "highlight_rows_line"),
    ("Allow filtering by", "allow_filtering_line"), ("Allow searching by", "allow_searching_line"),
    ("This table subscribes to", "subscribes_to_line"), ("Accept input for", "accept_input_line"),
    ("Validate that", "validate_unique_line"), ("Create the ", "create_as_line"),
    ("After saving,", "after_saving_line"), ("Show a chart of", "show_chart_line"),
    ("Section ", "section_header_line"),
    ("Display text", "display_text_line"),
    ("Display count of", "structured_agg_line"),
    ("Display sum of", "structured_agg_line"),
    ("Display average of", "structured_agg_line"),
    ("Display minimum of", "structured_agg_line"),
    ("Display maximum of", "structured_agg_line"),
    ("Display ", "display_agg_line"),
    ("Navigation bar:", "nav_bar_line"), ("Expose a REST API at", "api_header_line"),
    ("Stream ", "stream_line"), ("Compute called", "compute_header"),
    ("Channel called", "channel_header"), ("Carries ", "channel_carries_line"),
    ("Direction:", "channel_direction_line"), ("Delivery:", "channel_delivery_line"),
    ("Requires ", "channel_requires_line"),  # disambiguated in _classify_line for Compute context
    ("Endpoint:", "channel_endpoint_line"),
    ("Boundary called", "boundary_header"), ("Contains ", "boundary_contains_line"),
    ("Identity inherits", "boundary_inherits_line"), ("Identity restricts", "boundary_restricts_line"),
    ("Identity:", "compute_identity_line"),
    ("Provider is", "compute_provider_line"),
    ("Output confidentiality:", "compute_output_conf_line"),
    ("Trigger on", "compute_trigger_line"),
    ("Preconditions are:", "compute_preconditions_line"),
    ("Postconditions are:", "compute_postconditions_line"),
    ("Objective is", "compute_objective_line"),
    ("Strategy is", "compute_strategy_line"),
    ("Exposes property", "boundary_exposes_line"),
]
_SHAPE_KW = ("Transform:", "Reduce:", "Expand:", "Correlate:", "Route:")
_HTTP_METHODS = ("GET ", "POST ", "PUT ", "DELETE ", "PATCH ")

def _classify_line(text: str) -> str:
    if text.startswith('"') and " is alias for " in text: return "role_alias_line"
    if text.startswith(('A "', 'An "')) and " has " in text: return "role_standard_line"
    if " has " in text and '"' in text and not text.startswith(("A ", "An ", '"', "Content", "Each")):
        return "role_bare_line"
    if text.startswith(("A ", "An ")):
        if " starts as " in text: return "state_starts_line"
        if " can also be " in text: return "state_also_line"
        if " can become " in text: return "state_transition_line"
    for prefix, rule in _PREFIXES:
        if text.startswith(prefix):
            # Disambiguate "For each X, show actions:" from "For each X, show Y grouped by Z"
            if rule == "show_related_line" and "show actions" in text.lower():
                return "action_header_line"
            # Disambiguate "Requires" — channel (has "to send/receive") vs compute confidentiality
            if rule == "channel_requires_line" and " to send" not in text and " to receive" not in text:
                return "compute_requires_conf_line"
            return rule
    if text.startswith('"') and " transitions to " in text: return "action_button_line"
    if text.startswith('"') and " links to " in text: return "nav_item_line"
    if any(text.startswith(m) or text.lstrip().startswith(m) for m in _HTTP_METHODS): return "api_endpoint_line"
    if any(text.startswith(kw) for kw in _SHAPE_KW): return "compute_shape_line"
    if text.startswith("```") and text.endswith("```") and len(text) > 6: return "compute_body_multiline"
    if text.startswith("`") and text.endswith("`") and not text.startswith("```"): return "compute_body_expr_line"
    if text.startswith("[") and text.endswith("]"): return "compute_body_expr_line"  # legacy bracket support
    if " can execute this" in text: return "compute_access_line"
    return "unknown"

# --- TatSu helpers ---
def _try_parse(line, rule):
    try: return _model.parse(line, rule_name=rule)
    except Exception: return None

def _rule(result) -> str:
    if hasattr(result, "parseinfo") and result.parseinfo: return result.parseinfo.rule or ""
    if isinstance(result, dict):
        pi = result.get("parseinfo")
        if pi and hasattr(pi, "rule"): return pi.rule or ""
    return ""

def _qs(r) -> str:
    if isinstance(r, dict) and "content" in r: return r["content"]
    return str(r) if r is not None else "" if not isinstance(r, str) else r

def _ql(r) -> list[str]:
    if r is None: return []
    if isinstance(r, dict) and "val" in r:
        v = r["val"]
        return [_qs(x) for x in v] if isinstance(v, list) else [_qs(v)]
    return [_qs(x) for x in r] if isinstance(r, list) else [_qs(r)]

def _cl(r) -> list[str]:
    if r is None: return []
    items = r.get("item") if isinstance(r, dict) and "item" in r else r if isinstance(r, list) else [r]
    if not isinstance(items, list): items = [items]
    return [str(i).strip() for i in items if i is not None and str(i).strip()]

def _ol(r) -> list[str]:
    if r is None: return []
    if isinstance(r, dict) and "item" in r:
        items = r["item"]
        if not isinstance(items, list): items = [items]
        return [str(i).strip() for i in items if i is not None]
    return [str(i).strip() for i in r if i is not None] if isinstance(r, list) else [str(r).strip()]

def _si(val, d=0) -> int:
    if val is None: return d
    try: return int(val)
    except (ValueError, TypeError): return d

def _scal(text: str) -> list[str]:
    """Split comma-and list: 'a, b, and c' -> ['a','b','c']."""
    text = text.strip().rstrip(":")
    result = []
    for part in text.split(","):
        p = part.strip()
        if not p: continue
        if p.startswith("and "): p = p[4:].strip()
        for ap in p.split(" and "):
            if ap.strip(): result.append(ap.strip())
    return result

def _eqs(text: str) -> list[str]:
    result, start = [], 0
    while True:
        q1 = text.find('"', start)
        if q1 < 0: break
        q2 = text.find('"', q1 + 1)
        if q2 < 0: break
        result.append(text[q1 + 1:q2]); start = q2 + 1
    return result

def _fq(text: str) -> str:
    q1 = text.find('"')
    if q1 < 0: return ""
    q2 = text.find('"', q1 + 1)
    return text[q1 + 1:q2] if q2 >= 0 else ""

def _eb(text: str) -> Optional[str]:
    """Extract backtick expression content from text."""
    b1 = text.find("`")
    if b1 < 0:
        # Legacy bracket fallback
        b1 = text.find("[")
        if b1 < 0: return None
        b2 = text.find("]", b1 + 1)
        return text[b1 + 1:b2].strip() if b2 >= 0 else None
    b2 = text.find("`", b1 + 1)
    return text[b1 + 1:b2].strip() if b2 >= 0 else None

# --- Type expression ---
def _parse_type_text(text: str, ln: int = 0) -> TypeExpr:
    expr = TypeExpr(base_type="text", line=ln)
    # Extract "confidentiality is ..." before other constraint stripping
    import re
    cm = re.search(r',?\s*confidentiality\s+is\s+("(?:[^"]*)"(?:\s+and\s+"[^"]*")*)', text, re.IGNORECASE)
    if cm:
        scope_str = cm.group(1)
        expr.confidentiality_scopes = [s.strip().strip('"') for s in re.findall(r'"([^"]*)"', scope_str)]
        text = text[:cm.start()] + text[cm.end():]

    # Extract "defaults to `expr`" or "defaults to [expr]" (legacy) or 'defaults to "literal"'
    dm = re.search(r',?\s*defaults\s+to\s+`([^`]+)`', text, re.IGNORECASE)
    if not dm:
        dm = re.search(r',?\s*defaults\s+to\s+\[([^\]]+)\]', text, re.IGNORECASE)  # legacy bracket
    if dm:
        expr.default_expr = dm.group(1).strip()
        expr.default_is_expr = True
        text = text[:dm.start()] + text[dm.end():]
    else:
        dm = re.search(r',?\s*defaults\s+to\s+"([^"]*)"', text, re.IGNORECASE)
        if dm:
            expr.default_expr = dm.group(1)
            expr.default_is_expr = False
            text = text[:dm.start()] + text[dm.end():]
    while True:
        tl = text.lower().rstrip()
        if tl.endswith(", required") or tl.endswith(",required"):
            expr.required = True; text = text[:text.lower().rfind("required")].rstrip().rstrip(",").strip(); continue
        if tl.endswith(", unique") or tl.endswith(",unique"):
            expr.unique = True; text = text[:text.lower().rfind("unique")].rstrip().rstrip(",").strip(); continue
        changed = False
        for mk, attr in [(",maximum ", "maximum"), (", maximum ", "maximum"),
                         (",minimum ", "minimum"), (", minimum ", "minimum")]:
            idx = tl.rfind(mk)
            if idx >= 0:
                vt = text[idx + len(mk):].strip().rstrip(",").strip()
                c = vt.find(",")
                setattr(expr, attr, _si(vt[:c].strip() if c >= 0 else vt))
                text = text[:idx].strip(); changed = True; break
        if not changed: break
    text = text.strip()
    if text.startswith("unique "):
        expr.unique = True; text = text[7:].strip()
    TM = {"text": "text", "currency": "currency", "number": "number",
          "percentage": "percentage", "true/false": "boolean", "boolean": "boolean",
          "date and time": "datetime", "date": "date", "automatic": "automatic",
          "a whole number": "whole_number", "whole number": "whole_number"}
    if text in TM: expr.base_type = TM[text]
    elif text.startswith("one of:"):
        expr.base_type = "enum"
        expr.enum_values = [v.strip().strip('"') for v in _scal(text[7:])]
    elif text.startswith("list of "):
        expr.base_type = "list"; expr.list_type = text[8:].strip().strip('"')
    return expr

def _parse_field_type(text: str, ln: int) -> TypeExpr:
    if text.startswith("is "): return _parse_type_text(text[3:], ln)
    if text.startswith("references "):
        rt = text[11:].strip(); ci = rt.find(","); ct = ""
        if ci >= 0: ct = rt[ci:]; rt = rt[:ci].strip()
        te = TypeExpr(base_type="reference", references=rt.strip('"'), line=ln)
        if "required" in ct: te.required = True
        if "unique" in ct: te.unique = True
        return te
    return TypeExpr(base_type="text", line=ln)

# --- AST builders ---
def _build_access(r, ln) -> AccessRule:
    scope = _qs(r.get("scope", ""))
    vr = r.get("verbs")
    vm = {"VerbView": ["view"], "VerbCreate": ["create"], "VerbUpdate": ["update"],
          "VerbDelete": ["delete"], "VerbCreateOrUpdate": ["create or update"]}
    rn = _rule(vr)
    if rn in vm: return AccessRule(scope=scope, verbs=vm[rn], line=ln)
    if isinstance(vr, (list, tuple)):
        j = " ".join(str(v) for v in vr).strip()
        vs = ["create or update"] if j == "create or update" else [str(v).strip() for v in vr if str(v).strip() != "or"]
    elif isinstance(vr, str): vs = [vr.strip()]
    else: vs = ["view"]
    return AccessRule(scope=scope, verbs=vs, line=ln)

def _build_story(text, ln) -> UserStory:
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
    rest = text[3:].strip()
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

def _build_trans(text, ln) -> Optional[Transition]:
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
    rest = text[5:].strip().rstrip(":")
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
    for op in ("at or below","above","below","equal to"):
        mk = " is "+op+" its "; idx = text.find(mk)
        if idx >= 0:
            ev.condition = EventCondition(field1=text[:idx].strip(), operator=op,
                                          field2=text[idx+len(mk):].strip(), line=ev.line)
            return

def _build_err_act(text, ln) -> ErrorAction:
    r = _try_parse(text, "error_then_line")
    if r is not None:
        ar = r.get("action"); rn = _rule(ar)
        if rn == "ActionDisable": return ErrorAction(kind="disable", target=str(ar.get("target","")).strip(), line=ln)
        if rn == "ActionEscalate" or ar == "escalate": return ErrorAction(kind="escalate", line=ln)
        if rn == "ActionNotify": return ErrorAction(kind="notify", target=_qs(ar.get("role","")),
                                                     expr=_qs(ar.get("expr","")), line=ln)
        if rn == "ActionCreate": return ErrorAction(kind="create", target=_qs(ar.get("name","")), line=ln)
        if rn == "ActionSet": return ErrorAction(kind="set", expr=_qs(ar.get("expr","")), line=ln)
    rest = text[5:].strip()
    if rest.startswith("disable "): return ErrorAction(kind="disable", target=rest[8:].strip(), line=ln)
    if rest == "escalate": return ErrorAction(kind="escalate", line=ln)
    if rest.startswith("notify "): return ErrorAction(kind="notify", target=_fq(rest), expr=_eb(rest) or "", line=ln)
    if rest.startswith("create "): return ErrorAction(kind="create", target=_fq(rest), line=ln)
    if rest.startswith("set "): return ErrorAction(kind="set", expr=_eb(rest) or "", line=ln)
    return ErrorAction(kind="unknown", target=rest, line=ln)

def _build_comp_shape(text) -> tuple:
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

# --- Per-line parse dispatch ---
def _parse_line(text: str, rule: str, ln: int):
    P = _try_parse  # alias

    if rule == "application_line":
        r = P(text, rule); return ("application", Application(name=str(r["name"]).strip() if r else text[12:].strip(), line=ln))
    if rule == "description_line":
        r = P(text, rule); return ("description", str(r["desc"]).strip() if r else text[12:].strip())
    if rule == "id_line":
        r = P(text, rule); return ("app_id", str(r["id"]).strip() if r else text[3:].strip())
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
            sg = text[5:hi].strip(); ah = text[hi+5:].strip()
            for a in ("a ","an ","the "):
                if ah.startswith(a): ah = ah[len(a):]; break
            wi = ah.find(" which ")
            if wi < 0: return ("field", Field(name="unknown", type_expr=TypeExpr(base_type="text"), line=ln), sg)
            fn = ah[:wi].strip()
        wi = text.find(" which ")
        te = _parse_field_type(text[wi+7:].strip(), ln) if wi >= 0 else TypeExpr(base_type="text", line=ln)
        return ("field", Field(name=fn, type_expr=te, line=ln), sg)
    if rule == "access_line":
        r = P(text, rule)
        if r: return ("access", _build_access(r, ln))
        sc = _fq(text); ci = text.find(" can ")
        if ci >= 0:
            rest = text[ci+5:].strip(); si = rest.rfind(" ")
            v = rest[:si].strip() if si >= 0 else rest
            return ("access", AccessRule(scope=sc, verbs=[v], line=ln))
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
            # Strip channel/compute/boundary prefix
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
    if rule == "event_expr_line":
        r = P(text, rule); j = _qs(r.get("cel","")) if r else (_eb(text) or "")
        return ("event_header", EventRule(content_name="", trigger="expr", condition_expr=j, line=ln))
    if rule == "event_v1_line":
        return ("event_header", _build_ev1(text, ln))
    if rule == "event_action_line":
        r = P(text, rule)
        if r: return ("event_action", EventAction(create_content=_qs(r.get("name","")), fields=_cl(r.get("fields")), line=ln))
        rest = text[7:].strip()
        for a in ("a ","an ","the "):
            if rest.startswith(a): rest = rest[len(a):]; break
        wi = rest.find(" with ")
        if wi >= 0:
            n = rest[:wi].strip().strip('"'); ft = rest[wi+6:].strip()
            if ft.startswith("the "): ft = ft[4:]
            return ("event_action", EventAction(create_content=n, fields=_scal(ft), line=ln))
        return ("event_action", EventAction(create_content=rest.strip().strip('"'), line=ln))
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
        r = P(text, rule); return ("so_that", str(r.get("text","")).strip().rstrip(":") if r else text[8:].strip().rstrip(":"))
    if rule == "show_page_line":
        r = P(text, rule); return ("directive", ShowPage(page_name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "display_table_line":
        rest = text[19:].strip(); wi = rest.find(" with columns:")
        cn = rest[:wi].strip() if wi>=0 else rest.strip()
        cols = _scal(rest[wi+14:]) if wi>=0 else []
        return ("directive", DisplayTable(content_name=cn, columns=cols, line=ln))
    if rule == "show_related_line":
        rest = text[9:].strip(); ci = rest.find(",")
        if ci < 0: return ("directive", ShowRelated(singular=rest, line=ln))
        sg = rest[:ci].strip(); af = rest[ci+1:].strip()
        if af.lower().startswith("show "): af = af[5:].strip()
        gi = af.find(" grouped by ")
        if gi < 0: return ("directive", ShowRelated(singular=sg, related_content=af, line=ln))
        return ("directive", ShowRelated(singular=sg, related_content=af[:gi].strip(), group_by=af[gi+12:].strip(), line=ln))
    if rule == "highlight_rows_line":
        rest = text[21:].strip(); j = _eb(rest)
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
        # Fallback: if _ol returned a single item containing " or ", split it
        if len(fs) == 1 and " or " in fs[0]:
            fs = [f.strip() for f in fs[0].split(" or ") if f.strip()]
        if not fs:
            # "Allow searching by " = 19 chars (NOT 20 — off-by-one caused "itle" from "title")
            rest = text[len("Allow searching by "):].strip()
            fs = [f.strip() for f in rest.split(" or ") if f.strip()]
        return ("directive", AllowSearch(fields=fs, line=ln))
    if rule == "subscribes_to_line":
        rest = text[25:].strip()
        if rest.endswith(" changes"): rest = rest[:-8].strip()
        return ("directive", SubscribeTo(content_name=rest, line=ln))
    if rule == "accept_input_line":
        r = P(text, rule); return ("directive", AcceptInput(fields=_cl(r.get("fields")) if r else _scal(text[len("Accept input for "):]), line=ln))
    if rule == "validate_unique_line":
        rest = text[14:].strip()
        if rest.startswith("["):
            be = rest.find("]"); return ("directive", ValidateUnique(condition_expr=rest[1:be].strip() if be>0 else rest[1:].strip(), line=ln))
        ii = rest.find(" is unique"); return ("directive", ValidateUnique(field=rest[:ii].strip() if ii>=0 else rest.strip(), line=ln))
    if rule == "create_as_line":
        rest = text[11:].strip(); ai = rest.rfind(" as ")
        return ("directive", CreateAs(initial_state=rest[ai+4:].strip() if ai>=0 else "", line=ln))
    if rule == "after_saving_line":
        r = P(text, rule); return ("directive", AfterSave(instruction=str(r.get("text","")).strip() if r else text[14:].strip(), line=ln))
    if rule == "show_chart_line":
        rest = text[16:].strip(); oi = rest.find(" over the past ")
        if oi < 0: return ("directive", ShowChart(content_name=rest, days=30, line=ln))
        cn = rest[:oi].strip(); af = rest[oi+15:].strip(); sp = af.find(" ")
        return ("directive", ShowChart(content_name=cn, days=_si(af[:sp] if sp>0 else af, 30), line=ln))
    if rule == "display_text_line":
        r = P(text, rule)
        if r:
            if r.get("cel"): return ("directive", DisplayText(text=_qs(r["cel"]), is_expression=True, line=ln))
            if r.get("text"): return ("directive", DisplayText(text=_qs(r["text"]), line=ln))
            if r.get("expr"): return ("directive", DisplayText(text=str(r["expr"]).strip(), is_expression=True, line=ln))
        rest = text[12:].strip(); j = _eb(rest)
        if j: return ("directive", DisplayText(text=j, is_expression=True, line=ln))
        q = _fq(rest)
        if q: return ("directive", DisplayText(text=q, line=ln))
        return ("directive", DisplayText(text=rest.strip(), is_expression=True, line=ln))
    if rule == "structured_agg_line":
        r = P(text, rule)
        if r:
            content = str(r.get("content","")).strip()
            # Disambiguate by presence of fields rather than rule name
            if r.get("field"):
                # count of X grouped by Y
                return ("directive", StructuredAggregation(agg_type="count", source_content=content,
                                                            group_by=str(r["field"]).strip(), line=ln))
            if r.get("func"):
                # sum/average/min/max of [expr] from X [as format]
                func = str(r["func"]).strip()
                expr_val = _qs(r.get("expr","")) if r.get("expr") else None
                fmt = str(r.get("format","number")).strip() if r.get("format") else "number"
                return ("directive", StructuredAggregation(agg_type=func, source_content=content, expression=expr_val, format=fmt, line=ln))
            # count of X (no grouping, no func)
            return ("directive", StructuredAggregation(agg_type="count", source_content=content, line=ln))
        # Fallback: parse manually
        rest = text[8:].strip()
        if rest.lower().startswith("count of"):
            content = rest[8:].strip()
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
            # Fallback: extract quoted string
            title = _fq(text[8:].strip().rstrip(":")) or text[8:].strip().rstrip(":")
        return ("directive", SectionStart(title=title, line=ln))
    if rule == "action_header_line":
        r = P(text, rule)
        singular = str(r.get("singular","")).strip() if r else ""
        if not singular:
            # Extract from "For each X, show actions:"
            rest = text[9:].strip()
            ci = rest.find(",")
            singular = rest[:ci].strip() if ci >= 0 else ""
        return ("directive", ActionHeader(singular=singular, line=ln))
    if rule == "action_button_line":
        r = P(text, rule)
        if r:
            label = _qs(r.get("label",""))
            state = _qs(r.get("state",""))
            rn = _rule(r)
            behavior = "hide" if rn == "ActionHide" else "disable"
            return ("directive", ActionButtonDef(label=label, target_state=state, unavailable_behavior=behavior, line=ln))
        # Fallback: parse quoted strings
        parts = text.strip()
        label = _fq(parts) or ""
        state = ""
        si = parts.lower().find("transitions to ")
        if si >= 0:
            rest = parts[si+15:]
            state = _fq(rest) or rest.split()[0] if rest else ""
        behavior = "hide" if "hide otherwise" in parts.lower() else "disable"
        return ("directive", ActionButtonDef(label=label, target_state=state, unavailable_behavior=behavior, line=ln))
    if rule == "display_agg_line":
        r = P(text, rule); return ("directive", DisplayAggregation(description=str(r.get("text","")).strip() if r else text[8:].strip(), line=ln))
    if rule == "nav_bar_line": return ("nav_bar",)
    if rule == "nav_item_line": return ("nav_item", _build_nav(text, ln))
    if rule == "api_header_line":
        r = P(text, rule); return ("api_header", ApiSection(base_path=str(r.get("path","")).strip().rstrip(":") if r else text[21:].strip().rstrip(":"), line=ln))
    if rule == "api_endpoint_line":
        r = P(text, rule)
        if r: return ("api_endpoint", ApiEndpoint(method=str(r.get("method","")).strip(), path=str(r.get("path","")).strip(),
                                                   description=str(r.get("desc","")).strip() if r.get("desc") else "", line=ln))
        ps = text.strip().split(None, 2)
        return ("api_endpoint", ApiEndpoint(method=ps[0] if ps else "", path=ps[1] if len(ps)>1 else "",
                                             description=ps[2].strip() if len(ps)>2 else "", line=ln))
    if rule == "stream_line":
        rest = text[7:].strip(); ai = rest.rfind(" at ")
        if ai < 0: return ("stream", Stream(description=rest, path="", line=ln))
        return ("stream", Stream(description=rest[:ai].strip(), path=rest[ai+4:].strip(), line=ln))
    if rule == "compute_header":
        r = P(text, rule); return ("compute_header", ComputeNode(name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "compute_shape_line": return ("compute_shape", _build_comp_shape(text))
    if rule == "compute_body_expr_line": return ("compute_body", text[1:-1].strip())
    if rule == "compute_body_multiline": return ("compute_body_multiline", text[3:-3].strip())
    if rule == "compute_access_line":
        r = P(text, rule)
        if r: return ("compute_access", _qs(r.get("role","")))
        ci = text.find(" can execute this"); return ("compute_access", text[:ci].strip().strip('"') if ci>=0 else "")
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
        rest = text[11:].strip()  # after "Trigger on "
        return ("compute_trigger", rest)
    if rule == "compute_preconditions_line":
        return ("compute_preconditions_header",)
    if rule == "compute_postconditions_line":
        return ("compute_postconditions_header",)
    if rule == "compute_objective_line":
        # Content may be inline ```...``` or just text after "Objective is "
        rest = text[13:].strip()  # after "Objective is "
        # Strip triple-backtick wrapper if present
        if rest.startswith("```") and rest.endswith("```"):
            rest = rest[3:-3].strip()
        return ("compute_objective", rest)
    if rule == "compute_strategy_line":
        rest = text[12:].strip()  # after "Strategy is "
        if rest.startswith("```") and rest.endswith("```"):
            rest = rest[3:-3].strip()
        return ("compute_strategy", rest)
    if rule == "content_scoped_line":
        r = P(text, rule)
        scopes = _ql(r.get("scopes")) if r else _eqs(text)
        return ("content_scoped", scopes)
    if rule == "channel_header":
        r = P(text, rule); return ("channel_header", ChannelDecl(name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "channel_carries_line":
        r = P(text, rule); return ("channel_prop", "carries", str(r.get("content","")).strip() if r else text[8:].strip())
    if rule == "channel_direction_line":
        r = P(text, rule); return ("channel_prop", "direction", str(r.get("dir","")).strip().lower() if r else text.split(":",1)[1].strip().lower())
    if rule == "channel_delivery_line":
        r = P(text, rule); return ("channel_prop", "delivery", str(r.get("del","")).strip().lower() if r else text.split(":",1)[1].strip().lower())
    if rule == "channel_requires_line":
        r = P(text, rule)
        if r: return ("channel_prop", "requires", ChannelRequirement(scope=_qs(r.get("scope","")), direction=str(r.get("dir","")).strip(), line=ln))
        return ("channel_prop", "requires", ChannelRequirement(scope=_fq(text), direction="send" if " to send" in text else "receive", line=ln))
    if rule == "channel_endpoint_line":
        r = P(text, rule); return ("channel_prop", "endpoint", str(r.get("path","")).strip() if r else text.split(":",1)[1].strip())
    if rule == "boundary_header":
        r = P(text, rule); return ("boundary_header", BoundaryDecl(name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "boundary_contains_line":
        r = P(text, rule); return ("boundary_prop", "contains", _cl(r.get("items_") or r.get("items")) if r else _scal(text[9:]))
    if rule == "boundary_inherits_line":
        r = P(text, rule); return ("boundary_prop", "inherits", str(r.get("parent","")).strip() if r else text[24:].strip())
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

# --- Block assembly ---
def _assemble(parsed: list) -> Program:
    prog = Program(); i = 0; n = len(parsed)
    def _collect(pred):
        nonlocal i
        while i < n and parsed[i] is not None and pred(parsed[i][0]):
            yield parsed[i]; i += 1
    while i < n:
        item = parsed[i]
        if item is None: i += 1; continue
        k = item[0]
        if k == "application":
            app = item[1]; i += 1
            if i < n and parsed[i] is not None and parsed[i][0] == "description":
                app.description = parsed[i][1]; i += 1
            if i < n and parsed[i] is not None and parsed[i][0] == "app_id":
                app.app_id = parsed[i][1]; i += 1
            prog.application = app
        elif k == "description":
            if prog.application: prog.application.description = item[1]
            i += 1
        elif k == "app_id":
            if prog.application: prog.application.app_id = item[1]
            i += 1
        elif k == "identity": prog.identity = item[1]; i += 1
        elif k == "scopes":
            if prog.identity: prog.identity.scopes = item[1]
            else: prog.identity = Identity(provider="stub", scopes=item[1])
            i += 1
        elif k == "role": prog.roles.append(item[1]); i += 1
        elif k == "role_alias": prog.role_aliases.append(item[1]); i += 1
        elif k == "content_header":
            ct = item[1]; i += 1
            for ch in _collect(lambda x: x in ("field","access","content_scoped")):
                if ch[0] == "field":
                    if ch[2]: ct.singular = ch[2]
                    ct.fields.append(ch[1])
                elif ch[0] == "access": ct.access_rules.append(ch[1])
                elif ch[0] == "content_scoped": ct.confidentiality_scopes.extend(ch[1])
            prog.contents.append(ct)
        elif k == "state_header":
            sm = item[1]; i += 1
            for ch in _collect(lambda x: x in ("state_starts","state_also","state_transition")):
                if ch[0] == "state_starts": sm.singular = ch[1]; sm.initial_state = ch[2]; sm.states.append(sm.initial_state)
                elif ch[0] == "state_also": sm.states.extend(ch[1])
                elif ch[0] == "state_transition": sm.transitions.append(ch[1])
            prog.state_machines.append(sm)
        elif k == "event_header":
            ev = item[1]; i += 1
            for ch in _collect(lambda x: x in ("event_action","log_level")):
                if ch[0] == "event_action": ev.action = ch[1]
                elif ch[0] == "log_level": ev.log_level = ch[1]
            prog.events.append(ev)
        elif k == "error_header":
            h = item[1]; i += 1
            for ch in _collect(lambda x: x in ("error_retry","error_then","log_level")):
                if ch[0] == "log_level": h.actions.append(ErrorAction(kind="log_level", target=ch[1]))
                else: h.actions.append(ch[1])
            prog.error_handlers.append(h)
        elif k == "story_header":
            st = item[1]; i += 1
            for ch in _collect(lambda x: x in ("so_that","directive")):
                if ch[0] == "so_that": st.objective = ch[1]
                else: st.directives.append(ch[1])
            prog.stories.append(st)
        elif k == "nav_bar":
            nav = NavBar(); i += 1
            for ch in _collect(lambda x: x == "nav_item"): nav.items.append(ch[1])
            prog.navigation = nav
        elif k == "api_header":
            api = item[1]; i += 1
            for ch in _collect(lambda x: x == "api_endpoint"): api.endpoints.append(ch[1])
            prog.api = api
        elif k == "stream": prog.streams.append(item[1]); i += 1
        elif k == "compute_header":
            nd = item[1]; i += 1
            _compute_child_kinds = ("compute_shape","compute_body","compute_body_multiline",
                "compute_access","access","compute_identity","compute_requires_conf",
                "compute_output_conf","compute_provider","compute_trigger",
                "compute_preconditions_header","compute_postconditions_header",
                "compute_objective","compute_strategy")
            collecting_pre = False
            collecting_post = False
            for ch in _collect(lambda x: x in _compute_child_kinds):
                if ch[0] == "compute_preconditions_header":
                    collecting_pre = True; collecting_post = False; continue
                elif ch[0] == "compute_postconditions_header":
                    collecting_post = True; collecting_pre = False; continue
                elif ch[0] in ("compute_body", "compute_body_multiline") and collecting_pre:
                    nd.preconditions.append(ch[1]); continue
                elif ch[0] in ("compute_body", "compute_body_multiline") and collecting_post:
                    nd.postconditions.append(ch[1]); continue
                else:
                    collecting_pre = False; collecting_post = False
                if ch[0] == "compute_shape":
                    sd = ch[1]; nd.shape, nd.inputs, nd.outputs = sd[0], sd[1], sd[2]; nd.input_params, nd.output_params = sd[3], sd[4]
                elif ch[0] in ("compute_body", "compute_body_multiline"): nd.body_lines.append(ch[1])
                elif ch[0] == "compute_access":
                    if ch[1]: nd.access_role = ch[1]
                elif ch[0] == "access": nd.access_scope = ch[1].scope
                elif ch[0] == "compute_identity": nd.identity_mode = ch[1]
                elif ch[0] == "compute_requires_conf": nd.required_confidentiality_scopes.extend(ch[1])
                elif ch[0] == "compute_output_conf": nd.output_confidentiality = ch[1]
                elif ch[0] == "compute_provider": nd.provider = ch[1]
                elif ch[0] == "compute_trigger": nd.trigger = ch[1]
                elif ch[0] == "compute_objective": nd.objective = ch[1]
                elif ch[0] == "compute_strategy": nd.strategy = ch[1]
            prog.computes.append(nd)
        elif k == "channel_header":
            ch_ = item[1]; i += 1
            for child in _collect(lambda x: x == "channel_prop"):
                p, v = child[1], child[2]
                if p == "carries": ch_.carries = v
                elif p == "direction": ch_.direction = v
                elif p == "delivery": ch_.delivery = v
                elif p == "endpoint": ch_.endpoint = v
                elif p == "requires": ch_.requirements.append(v)
            prog.channels.append(ch_)
        elif k == "boundary_header":
            bnd = item[1]; i += 1
            for child in _collect(lambda x: x == "boundary_prop"):
                p, v = child[1], child[2]
                if p == "contains": bnd.contains = v
                elif p == "inherits": bnd.identity_mode = "inherit"; bnd.identity_parent = v
                elif p == "restricts": bnd.identity_mode = "restrict"; bnd.identity_scopes = v
                elif p == "exposes": bnd.properties.append(v)
            prog.boundaries.append(bnd)
        else: i += 1
    return prog

# --- Public API ---
def parse_peg(source: str) -> tuple[Program, CompileResult]:
    errors = CompileResult()
    try: lines = _preprocess(source)
    except Exception as e:
        errors.add(ParseError(message=f"Preprocessing failed: {e}", line=0)); return Program(), errors
    parsed = []
    for line_num, text in lines:
        rule = _classify_line(text)
        if rule == "unknown":
            errors.add(ParseError(message=f"Unrecognized line: {text}", line=line_num, source_line=text)); continue
        try:
            result = _parse_line(text, rule, line_num)
            if result is not None: parsed.append(result)
        except Exception as e:
            errors.add(ParseError(message=f"Failed to parse line: {e}", line=line_num, source_line=text))
    if not errors.ok: return Program(), errors
    try: program = _assemble(parsed)
    except Exception as e:
        errors.add(ParseError(message=f"Block assembly failed: {e}", line=0)); return Program(), errors
    return program, errors
