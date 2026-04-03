"""TatSu PEG-based parser for the Termin DSL.

Two-level design:
  Level 1 (Python): Line classification by keyword + block assembly
  Level 2 (TatSu PEG): Per-line content parsing using termin.peg

Each PEG rule parses ONE line and returns structured data (AST dict from
TatSu).  The Python wrapper preprocesses, classifies, parses, assembles
blocks, and builds the same AST dataclass nodes as the hand-rolled parser.

Public API
----------
    parse_peg(source: str) -> tuple[Program, CompileResult]

This is a drop-in replacement for ``termin.parser.parse``.
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
    NavBar, NavItem, ApiSection, ApiEndpoint, Stream, Directive,
    ComputeNode, ComputeParam, ChannelDecl, ChannelRequirement, BoundaryDecl,
    BoundaryProperty, DisplayText,
    ErrorHandler, ErrorAction,
)
from .errors import ParseError, CompileResult


# ---------------------------------------------------------------------------
# Load TatSu grammar
# ---------------------------------------------------------------------------

_GRAMMAR_PATH = Path(__file__).parent / "termin.peg"
_grammar_text = _GRAMMAR_PATH.read_text(encoding="utf-8")
_model = tatsu.compile(_grammar_text)


# ---------------------------------------------------------------------------
# Preprocessor: strips comments, dividers, blank lines
# ---------------------------------------------------------------------------

def _preprocess(source: str) -> list[tuple[int, str]]:
    """Return (line_number, cleaned_text) pairs for non-blank, non-comment lines."""
    result = []
    for line_num, raw_line in enumerate(source.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("---"):
            continue
        if stripped.startswith("(") and stripped.endswith(")"):
            continue
        # Strip inline parenthesis comments (but not function calls)
        idx = stripped.find(" (")
        if idx > 0:
            tail = stripped[idx:]
            # Only strip if the tail matches " (text)" at end of line
            paren_close = tail.find(")")
            if paren_close > 0 and paren_close == len(tail) - 1:
                stripped = stripped[:idx].strip()
        result.append((line_num, stripped))
    return result


# ---------------------------------------------------------------------------
# Line classification
# ---------------------------------------------------------------------------

# Order matters: first match wins.
_LINE_CLASSIFIERS: list[tuple[str, str]] = [
    ("Application:", "application_line"),
    ("Description:", "description_line"),
    ("Users authenticate with", "identity_line"),
    ("Scopes are", "scopes_line"),
    # Role alias must come before role standard (both start with ")
    ('"ALIAS_CHECK"', "role_alias_line"),  # special: checked by content
    ('A "', "role_standard_line"),
    ('An "', "role_standard_line"),
    # Bare role: word has "scope"  (no leading article+quote)
    ("BARE_ROLE_CHECK", "role_bare_line"),
    ("Content called", "content_header"),
    ("Each ", "field_line"),
    ("Anyone with", "access_line"),
    ("State for", "state_header"),
    # state_starts must come before state_also and state_transition
    ("STATE_STARTS_CHECK", "state_starts_line"),
    ("STATE_ALSO_CHECK", "state_also_line"),
    ("STATE_TRANSITION_CHECK", "state_transition_line"),
    ("When [", "event_jexl_line"),
    ("When a ", "event_v1_line"),
    ("When an ", "event_v1_line"),
    ("Create a ", "event_action_line"),
    ("Create an ", "event_action_line"),
    ("Log level:", "log_level_line"),
    ("On error from", "error_from_line"),
    ("On any error:", "error_catch_all_line"),
    ("Retry ", "error_retry_line"),
    ("Then ", "error_then_line"),
    ("As ", "story_header"),
    ("so that ", "so_that_line"),
    ("Show a page called", "show_page_line"),
    ("Display a table of", "display_table_line"),
    ("For each ", "show_related_line"),
    ("Highlight rows where", "highlight_rows_line"),
    ("Allow filtering by", "allow_filtering_line"),
    ("Allow searching by", "allow_searching_line"),
    ("This table subscribes to", "subscribes_to_line"),
    ("Accept input for", "accept_input_line"),
    ("Validate that", "validate_unique_line"),
    ("Create the ", "create_as_line"),
    ("After saving,", "after_saving_line"),
    ("Show a chart of", "show_chart_line"),
    ("Display text", "display_text_line"),
    ("Display ", "display_agg_line"),
    ("Navigation bar:", "nav_bar_line"),
    # nav item starts with "
    ("NAV_ITEM_CHECK", "nav_item_line"),
    ("Expose a REST API at", "api_header_line"),
    ("API_ENDPOINT_CHECK", "api_endpoint_line"),
    ("Stream ", "stream_line"),
    ("Compute called", "compute_header"),
    ("COMPUTE_SHAPE_CHECK", "compute_shape_line"),
    ("Channel called", "channel_header"),
    ("Carries ", "channel_carries_line"),
    ("Direction:", "channel_direction_line"),
    ("Delivery:", "channel_delivery_line"),
    ("Requires ", "channel_requires_line"),
    ("Endpoint:", "channel_endpoint_line"),
    ("Boundary called", "boundary_header"),
    ("Contains ", "boundary_contains_line"),
    ("Identity inherits", "boundary_inherits_line"),
    ("Identity restricts", "boundary_restricts_line"),
    ("Exposes property", "boundary_exposes_line"),
    # JEXL standalone (compute body)
    ("JEXL_BLOCK_CHECK", "compute_body_jexl_line"),
    # compute body text / role access fallback
    ("COMPUTE_ACCESS_CHECK", "compute_access_line"),
]

_SHAPE_KEYWORDS = ("Transform:", "Reduce:", "Expand:", "Correlate:", "Route:")
_HTTP_METHODS = ("GET ", "POST ", "PUT ", "DELETE ", "PATCH ")


def _classify_line(text: str) -> str:
    """Classify a preprocessed line into a rule name."""
    for prefix, rule in _LINE_CLASSIFIERS:
        # Special checks that can't be done by simple prefix
        if prefix == '"ALIAS_CHECK"':
            if text.startswith('"') and " is alias for " in text:
                return rule
            continue
        if prefix == "BARE_ROLE_CHECK":
            # word has "scope" — not starting with A/An + quote
            if " has " in text and '"' in text and not text.startswith(("A ", "An ", '"', "Content", "Each")):
                return rule
            continue
        if prefix == "STATE_STARTS_CHECK":
            if " starts as " in text and text.startswith(("A ", "An ")):
                return rule
            continue
        if prefix == "STATE_ALSO_CHECK":
            if " can also be " in text and text.startswith(("A ", "An ")):
                return rule
            continue
        if prefix == "STATE_TRANSITION_CHECK":
            if " can become " in text and text.startswith(("A ", "An ")):
                return rule
            continue
        if prefix == "NAV_ITEM_CHECK":
            if text.startswith('"') and " links to " in text:
                return rule
            continue
        if prefix == "API_ENDPOINT_CHECK":
            for method in _HTTP_METHODS:
                if text.startswith(method) or text.lstrip().startswith(method):
                    return rule
            continue
        if prefix == "COMPUTE_SHAPE_CHECK":
            for kw in _SHAPE_KEYWORDS:
                if text.startswith(kw):
                    return rule
            continue
        if prefix == "JEXL_BLOCK_CHECK":
            if text.startswith("[") and text.endswith("]"):
                return rule
            continue
        if prefix == "COMPUTE_ACCESS_CHECK":
            if " can execute this" in text:
                return rule
            continue
        # Normal prefix check
        if text.startswith(prefix):
            return rule
    return "unknown"


# ---------------------------------------------------------------------------
# TatSu result helpers (NO REGEX)
# ---------------------------------------------------------------------------

def _qs(result) -> str:
    """Extract content from a TatSu quoted_string result: {'content': '...'} -> str."""
    if isinstance(result, dict) and "content" in result:
        return result["content"]
    if isinstance(result, str):
        return result
    return str(result) if result is not None else ""


def _quoted_list(result) -> list[str]:
    """Extract list from a TatSu quoted_list result: {'val': [{'content': '...'}, ...]}."""
    if result is None:
        return []
    if isinstance(result, dict) and "val" in result:
        vals = result["val"]
        if isinstance(vals, list):
            return [_qs(v) for v in vals]
        return [_qs(vals)]
    if isinstance(result, list):
        return [_qs(v) for v in result]
    return [_qs(result)]


def _comma_list(result) -> list[str]:
    """Extract list from a TatSu comma_list result: {'item': [...]}."""
    if result is None:
        return []
    if isinstance(result, dict) and "item" in result:
        items = result["item"]
    elif isinstance(result, dict) and "item_" in result:
        items = result["item_"]
    elif isinstance(result, dict) and "items_" in result:
        items = result["items_"]["item"] if isinstance(result["items_"], dict) else result["items_"]
    elif isinstance(result, list):
        items = result
    else:
        items = [result]
    if not isinstance(items, list):
        items = [items]
    return [str(i).strip() for i in items if i is not None and str(i).strip()]


def _or_list(result) -> list[str]:
    """Extract list from a TatSu or_list result: {'item': [...]}."""
    if result is None:
        return []
    if isinstance(result, dict) and "item" in result:
        items = result["item"]
        if not isinstance(items, list):
            items = [items]
        return [str(i).strip() for i in items if i is not None]
    if isinstance(result, list):
        return [str(i).strip() for i in result if i is not None]
    return [str(result).strip()]


def _safe_int(val, default=0) -> int:
    """Convert a value to int safely."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _try_parse(line: str, rule_name: str):
    """Try to parse a line with TatSu. Returns the result or None on failure."""
    try:
        return _model.parse(line, rule_name=rule_name)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# String-splitting helpers for rules where TatSu's greedy `words` fails.
# These use only str methods (split, find, partition, etc.) -- NO REGEX.
# ---------------------------------------------------------------------------

def _split_display_table(text: str) -> tuple[str, list[str]]:
    """Parse 'Display a table of CONTENT [with columns: COL1, COL2]'."""
    # Remove 'Display a table of ' prefix
    rest = text[len("Display a table of "):].strip()
    with_idx = rest.find(" with columns:")
    if with_idx >= 0:
        content_name = rest[:with_idx].strip()
        cols_text = rest[with_idx + len(" with columns:"):].strip()
        cols = _split_comma_and_list(cols_text)
        return content_name, cols
    return rest.strip(), []


def _split_show_related(text: str) -> tuple[str, str, str]:
    """Parse 'For each SINGULAR, show RELATED grouped by GROUP'."""
    rest = text[len("For each "):].strip()
    comma_idx = rest.find(",")
    if comma_idx < 0:
        return rest, "", ""
    singular = rest[:comma_idx].strip()
    after_comma = rest[comma_idx + 1:].strip()
    # Remove 'show '
    if after_comma.lower().startswith("show "):
        after_comma = after_comma[5:].strip()
    grouped_idx = after_comma.find(" grouped by ")
    if grouped_idx < 0:
        return singular, after_comma, ""
    related = after_comma[:grouped_idx].strip()
    group_by = after_comma[grouped_idx + len(" grouped by "):].strip()
    return singular, related, group_by


def _split_show_chart(text: str) -> tuple[str, int]:
    """Parse 'Show a chart of CONTENT over the past N days'."""
    rest = text[len("Show a chart of "):].strip()
    over_idx = rest.find(" over the past ")
    if over_idx < 0:
        return rest, 30
    content_name = rest[:over_idx].strip()
    after_over = rest[over_idx + len(" over the past "):].strip()
    # after_over is like '30 days'
    space_idx = after_over.find(" ")
    if space_idx > 0:
        days = _safe_int(after_over[:space_idx], 30)
    else:
        days = _safe_int(after_over, 30)
    return content_name, days


def _split_stream(text: str) -> tuple[str, str]:
    """Parse 'Stream DESCRIPTION at PATH'."""
    rest = text[len("Stream "):].strip()
    at_idx = rest.rfind(" at ")
    if at_idx < 0:
        return rest, ""
    desc = rest[:at_idx].strip()
    path = rest[at_idx + len(" at "):].strip()
    return desc, path


def _split_validate_unique(text: str) -> tuple[str, Optional[str]]:
    """Parse 'Validate that FIELD is unique [before saving]' or 'Validate that [jexl] ...'."""
    rest = text[len("Validate that "):].strip()
    if rest.startswith("["):
        # JEXL
        bracket_end = rest.find("]")
        if bracket_end > 0:
            return "", rest[1:bracket_end].strip()
        return "", rest[1:].strip()
    # plain: FIELD is unique [before saving]
    is_idx = rest.find(" is unique")
    if is_idx >= 0:
        return rest[:is_idx].strip(), None
    return rest.strip(), None


def _split_create_as(text: str) -> str:
    """Parse 'Create the CONTENT as STATE'."""
    rest = text[len("Create the "):].strip()
    as_idx = rest.rfind(" as ")
    if as_idx >= 0:
        return rest[as_idx + len(" as "):].strip()
    return ""


def _split_highlight_free(text: str) -> tuple[str, str, str]:
    """Parse 'Highlight rows where FIELD is OP FIELD2' (non-jexl case)."""
    rest = text[len("Highlight rows where "):].strip()
    # Check for JEXL
    if rest.startswith("["):
        return "", "", ""
    # Try to find 'is at or below', 'is above', 'is below', 'is equal to'
    ops = [" is at or below ", " is above ", " is below ", " is equal to "]
    for op_str in ops:
        idx = rest.find(op_str)
        if idx >= 0:
            field = rest[:idx].strip()
            op = op_str.strip()[3:]  # remove 'is '
            threshold = rest[idx + len(op_str):].strip()
            return field, op, threshold
    return "", "", ""


def _split_subscribes_to(text: str) -> str:
    """Parse 'This table subscribes to CONTENT changes'."""
    rest = text[len("This table subscribes to "):].strip()
    if rest.endswith(" changes"):
        return rest[:-len(" changes")].strip()
    return rest.strip()


def _split_comma_and_list(text: str) -> list[str]:
    """Split a comma-and-'and'-separated list. No regex.

    Handles: 'SKU, name, description, unit cost, and category'
    Returns: ['SKU', 'name', 'description', 'unit cost', 'category']
    """
    text = text.strip().rstrip(":")
    # First split by comma
    parts = text.split(",")
    result = []
    for part in parts:
        p = part.strip()
        if not p:
            continue
        # Handle leading 'and '
        if p.startswith("and "):
            p = p[4:].strip()
        # Handle standalone 'and' joining in the middle (e.g., 'a and b' with no comma)
        and_parts = p.split(" and ")
        for ap in and_parts:
            ap = ap.strip()
            if ap:
                result.append(ap)
    return result


def _split_or_list(text: str) -> list[str]:
    """Split an 'or'-separated list. No regex."""
    parts = text.split(" or ")
    return [p.strip() for p in parts if p.strip()]


def _extract_quoted_strings(text: str) -> list[str]:
    """Extract all double-quoted string contents from text. No regex."""
    result = []
    start = 0
    while True:
        q1 = text.find('"', start)
        if q1 < 0:
            break
        q2 = text.find('"', q1 + 1)
        if q2 < 0:
            break
        result.append(text[q1 + 1:q2])
        start = q2 + 1
    return result


def _extract_first_quoted(text: str) -> str:
    """Extract the first double-quoted string. No regex."""
    q1 = text.find('"')
    if q1 < 0:
        return ""
    q2 = text.find('"', q1 + 1)
    if q2 < 0:
        return ""
    return text[q1 + 1:q2]


def _extract_jexl_bracket(text: str) -> Optional[str]:
    """Extract [expr] content. No regex."""
    b1 = text.find("[")
    if b1 < 0:
        return None
    b2 = text.find("]", b1 + 1)
    if b2 < 0:
        return None
    return text[b1 + 1:b2].strip()


# ---------------------------------------------------------------------------
# AST builders -- map TatSu parse results or string-split results to AST nodes
# ---------------------------------------------------------------------------

def _build_type_expr(clause) -> TypeExpr:
    """Build a TypeExpr from a TatSu field_clause result."""
    if clause is None:
        return TypeExpr(base_type="text")

    # Reference case: {'ref': 'categories', 'constraints': [...]}
    if "ref" in clause:
        ref = _qs(clause["ref"])
        te = TypeExpr(base_type="reference", references=ref)
        _apply_constraints(te, clause.get("constraints"))
        return te

    # Type expression case: {'te': {...}}
    te_result = clause.get("te")
    if te_result is None:
        return TypeExpr(base_type="text")

    return _build_type_from_te(te_result)


def _build_type_from_te(te) -> TypeExpr:
    """Build TypeExpr from a type_expr result."""
    if te is None:
        return TypeExpr(base_type="text")

    bt_raw = te.get("bt", "text")
    constraints = te.get("cs")

    # Parse unique flag from named alternative
    unique = False
    # TatSu named alternatives: if the result has a parseinfo with rule ending in Unique
    # For simplicity, check if the TatSu AST signals uniqueness
    # The rule is: 'unique' bt:base_type cs:constraints => #TypeUnique
    # or:          bt:base_type cs:constraints           => #TypePlain
    # When unique, bt is the text after 'unique'
    # In practice, TatSu puts the rule name in the result's parseinfo
    # Let's check via the __class__ or type
    rule_name = _get_rule_name(te)
    if rule_name == "TypeUnique":
        unique = True

    base_type = _map_base_type(bt_raw)
    expr = TypeExpr(base_type=base_type, unique=unique)

    # Enum values
    if base_type == "enum" and isinstance(bt_raw, dict) and "vals" in bt_raw:
        expr.enum_values = _extract_enum_values(bt_raw["vals"])
    elif base_type == "enum" and isinstance(bt_raw, str):
        # Check if bt_raw itself has the values
        pass

    # List inner type
    if base_type == "list" and isinstance(bt_raw, dict) and "inner" in bt_raw:
        expr.list_type = str(bt_raw["inner"]).strip()

    _apply_constraints(expr, constraints)
    return expr


def _get_rule_name(result) -> str:
    """Get the named rule (parseinfo.rule) from a TatSu AST result."""
    if hasattr(result, "parseinfo") and result.parseinfo is not None:
        return result.parseinfo.rule or ""
    if isinstance(result, dict):
        pi = result.get("parseinfo")
        if pi is not None and hasattr(pi, "rule"):
            return pi.rule or ""
    return ""


def _map_base_type(bt) -> str:
    """Map a TatSu base_type result to our AST base_type string."""
    rule = _get_rule_name(bt)
    type_map = {
        "TypeText": "text",
        "TypeCurrency": "currency",
        "TypeNumber": "number",
        "TypePercentage": "percentage",
        "TypeBoolean": "boolean",
        "TypeDate": "date",
        "TypeDatetime": "datetime",
        "TypeAutomatic": "automatic",
        "TypeWholeNumber": "whole_number",
        "TypeEnum": "enum",
        "TypeList": "list",
    }
    if rule in type_map:
        return type_map[rule]
    # Fallback: check string value
    if isinstance(bt, str):
        bt_lower = bt.strip().lower()
        str_map = {
            "text": "text",
            "currency": "currency",
            "number": "number",
            "percentage": "percentage",
            "boolean": "boolean",
            "true/false": "boolean",
            "date": "date",
            "automatic": "automatic",
        }
        return str_map.get(bt_lower, "text")
    return "text"


def _extract_enum_values(vals_result) -> list[str]:
    """Extract enum values from a TatSu enum_vals result."""
    if vals_result is None:
        return []
    val_list = vals_result.get("val") if isinstance(vals_result, dict) else vals_result
    if val_list is None:
        return []
    if not isinstance(val_list, list):
        val_list = [val_list]
    result = []
    for v in val_list:
        s = _qs(v) if isinstance(v, dict) else str(v).strip()
        if s:
            result.append(s)
    return result


def _apply_constraints(expr: TypeExpr, constraints) -> None:
    """Apply constraint list to a TypeExpr."""
    if constraints is None:
        return
    if isinstance(constraints, list):
        items = constraints
    elif isinstance(constraints, tuple):
        items = list(constraints)
    else:
        items = [constraints]
    for item in items:
        if item is None or item == ",":
            continue
        if isinstance(item, list):
            for sub in item:
                _apply_single_constraint(expr, sub)
        elif isinstance(item, tuple):
            for sub in item:
                _apply_single_constraint(expr, sub)
        else:
            _apply_single_constraint(expr, item)


def _apply_single_constraint(expr: TypeExpr, item) -> None:
    """Apply a single constraint to a TypeExpr."""
    if item is None or item == ",":
        return
    rule = _get_rule_name(item)
    if rule == "Required" or item == "required":
        expr.required = True
    elif rule == "Unique" or item == "unique":
        expr.unique = True
    elif rule == "Minimum":
        if isinstance(item, dict) and "val" in item:
            expr.minimum = _safe_int(item["val"])
    elif rule == "Maximum":
        if isinstance(item, dict) and "val" in item:
            expr.maximum = _safe_int(item["val"])
    elif isinstance(item, str):
        s = item.strip().lower()
        if s == "required":
            expr.required = True
        elif s == "unique":
            expr.unique = True


def _build_access_rule(result, line_num: int) -> AccessRule:
    """Build an AccessRule from a TatSu access_line result."""
    scope = _qs(result.get("scope", ""))
    verbs_raw = result.get("verbs")
    if isinstance(verbs_raw, (list, tuple)):
        # TatSu returns ['create', 'or', 'update'] for "create or update"
        joined = " ".join(str(v) for v in verbs_raw).strip()
        if joined == "create or update":
            verbs = ["create or update"]
        else:
            # Filter out 'or' connectors
            verbs = [str(v).strip() for v in verbs_raw if str(v).strip() != "or"]
    elif isinstance(verbs_raw, str):
        verbs = [verbs_raw.strip()]
    else:
        rule = _get_rule_name(verbs_raw)
        verb_map = {
            "VerbView": ["view"],
            "VerbCreate": ["create"],
            "VerbUpdate": ["update"],
            "VerbDelete": ["delete"],
            "VerbCreateOrUpdate": ["create or update"],
        }
        verbs = verb_map.get(rule, ["view"])
    return AccessRule(scope=scope, verbs=verbs, line=line_num)


def _build_story(text: str, line_num: int) -> UserStory:
    """Build a UserStory from the story_header result using TatSu."""
    result = _try_parse(text, "story_header")
    if result is None:
        # Fallback: parse manually
        return _build_story_manual(text, line_num)

    role = str(result.get("role", "")).strip()
    action_result = result.get("action")

    action_text = ""
    objective = ""
    page_name = None

    if isinstance(action_result, dict):
        # Could be StoryActionPage or StoryActionFree
        so_that = action_result.get("so_that")
        if so_that:
            objective = str(so_that).strip().rstrip(":")
        page = action_result.get("page")
        if page:
            page_name = _qs(page)
            action_text = "see a page"
        else:
            action_text = str(action_result.get("text", "")).strip()
            # Check if the action text has 'so that' embedded
            so_idx = action_text.find(" so that ")
            if so_idx >= 0 and not objective:
                objective = action_text[so_idx + len(" so that "):].strip().rstrip(":")
                action_text = action_text[:so_idx].strip()
    elif isinstance(action_result, str):
        action_text = action_result.strip()

    story = UserStory(role=role, action=action_text, objective=objective, line=line_num)
    if page_name:
        story.directives.append(ShowPage(page_name=page_name, line=line_num))
    return story


def _build_story_manual(text: str, line_num: int) -> UserStory:
    """Fallback: parse story header using string operations."""
    # Remove 'As ' prefix
    rest = text[3:].strip()
    # Remove optional article
    for article in ("a ", "an ", "the "):
        if rest.startswith(article):
            rest = rest[len(article):].strip()
            break

    # Find ', I want to '
    marker = ", I want to "
    idx = rest.find(marker)
    if idx < 0:
        return UserStory(role=rest, action="", objective="", line=line_num)

    role = rest[:idx].strip()
    action_text = rest[idx + len(marker):].strip()

    objective = ""
    page_name = None

    # Check for inline 'so that' in action
    so_idx = action_text.find(" so that ")
    if so_idx >= 0:
        objective = action_text[so_idx + len(" so that "):].strip().rstrip(":")
        action_text = action_text[:so_idx].strip()

    # Check for 'see a page "Name"'
    see_page = "see a page "
    if action_text.startswith(see_page):
        pn = _extract_first_quoted(action_text)
        if pn:
            page_name = pn

    story = UserStory(role=role, action=action_text, objective=objective, line=line_num)
    if page_name:
        story.directives.append(ShowPage(page_name=page_name, line=line_num))
    return story


def _build_nav_item(text: str, line_num: int) -> NavItem:
    """Build a NavItem from a TatSu nav_item_line result."""
    result = _try_parse(text, "nav_item_line")
    if result is None:
        return _build_nav_item_manual(text, line_num)
    label = _qs(result.get("label", ""))
    page = _qs(result.get("page", ""))
    rest = str(result.get("rest", "")).strip()

    visible_to = []
    badge = None

    # Parse 'visible to X, Y, ..., badge: EXPR'
    vis_marker = "visible to "
    vis_idx = rest.find(vis_marker)
    if vis_idx >= 0:
        vis_text = rest[vis_idx + len(vis_marker):]
        badge_idx = vis_text.find(", badge:")
        if badge_idx >= 0:
            badge = vis_text[badge_idx + len(", badge:"):].strip()
            vis_text = vis_text[:badge_idx]
        visible_to = _split_comma_and_list(vis_text)

    return NavItem(label=label, page_name=page, visible_to=visible_to, badge=badge, line=line_num)


def _build_nav_item_manual(text: str, line_num: int) -> NavItem:
    """Fallback: parse nav item using string operations."""
    quotes = _extract_quoted_strings(text)
    label = quotes[0] if len(quotes) > 0 else ""
    page = quotes[1] if len(quotes) > 1 else ""

    visible_to = []
    badge = None
    vis_marker = "visible to "
    vis_idx = text.find(vis_marker)
    if vis_idx >= 0:
        vis_text = text[vis_idx + len(vis_marker):]
        badge_idx = vis_text.find(", badge:")
        if badge_idx >= 0:
            badge = vis_text[badge_idx + len(", badge:"):].strip()
            vis_text = vis_text[:badge_idx]
        visible_to = _split_comma_and_list(vis_text)

    return NavItem(label=label, page_name=page, visible_to=visible_to, badge=badge, line=line_num)


def _build_transition(text: str, line_num: int) -> Optional[Transition]:
    """Build a Transition from a TatSu state_transition_line result."""
    result = _try_parse(text, "state_transition_line")
    if result is not None:
        from_state = str(result.get("from_state", "")).strip()
        to_state = str(result.get("to_state", "")).strip()
        scope = _qs(result.get("scope", ""))
        return Transition(from_state=from_state, to_state=to_state, required_scope=scope, line=line_num)

    # Manual fallback
    return _build_transition_manual(text, line_num)


def _build_transition_manual(text: str, line_num: int) -> Optional[Transition]:
    """Parse transition manually: 'A STATE SINGULAR can become TARGET [again] if the user has "SCOPE"'."""
    # Remove leading article
    rest = text.strip()
    for article in ("A ", "An "):
        if rest.startswith(article):
            rest = rest[len(article):]
            break

    can_idx = rest.find(" can become ")
    if can_idx < 0:
        return None

    before_can = rest[:can_idx].strip()
    after_can = rest[can_idx + len(" can become "):].strip()

    # before_can is "STATE SINGULAR" — from_state is everything except the last word
    parts = before_can.rsplit(" ", 1)
    from_state = parts[0] if len(parts) > 1 else before_can

    # after_can is "TARGET [again] if the user has "SCOPE""
    if_idx = after_can.find(" if the user has ")
    if if_idx >= 0:
        to_state = after_can[:if_idx].strip()
        # Remove trailing 'again'
        if to_state.endswith(" again"):
            to_state = to_state[:-len(" again")].strip()
        scope = _extract_first_quoted(after_can[if_idx:])
        return Transition(from_state=from_state, to_state=to_state, required_scope=scope, line=line_num)

    # Check for JEXL condition
    if_idx = after_can.find(" if ")
    if if_idx >= 0:
        to_state = after_can[:if_idx].strip()
        if to_state.endswith(" again"):
            to_state = to_state[:-len(" again")].strip()
        return Transition(from_state=from_state, to_state=to_state, required_scope="", line=line_num)

    return Transition(from_state=from_state, to_state=after_can.strip(), required_scope="", line=line_num)


def _build_event_v1(text: str, line_num: int) -> EventRule:
    """Build an EventRule from a v1 event line."""
    result = _try_parse(text, "event_v1_line")
    if result is not None:
        content = str(result.get("content", "")).strip()
        trigger = str(result.get("trigger", "")).strip()
        ev = EventRule(content_name=content, trigger=trigger, line=line_num)
        cond = result.get("condition")
        if cond and isinstance(cond, dict):
            field1 = str(cond.get("field1", "")).strip()
            op_raw = cond.get("op")
            op = _get_rule_name(op_raw)
            op_map = {
                "OpAtOrBelow": "at or below",
                "OpAbove": "above",
                "OpBelow": "below",
                "OpEqualTo": "equal to",
            }
            op_str = op_map.get(op, str(op_raw).strip() if op_raw else "")
            # field2 is the rest_of_line after 'its'
            field2 = str(cond.get("rest_of_line", "")).strip()
            # Actually field2 comes from rest_of_line in the grammar
            # Let's get it from the raw text
            ev.condition = EventCondition(field1=field1, operator=op_str, field2="", line=line_num)
            # Need to extract field2 from text
            _fill_event_condition_field2(ev.condition, text)
        return ev

    # Fallback
    return _build_event_v1_manual(text, line_num)


def _fill_event_condition_field2(cond: EventCondition, text: str) -> None:
    """Extract field2 from the event text after 'its <op> its FIELD2'."""
    # Find the operator in the text
    op = cond.operator
    if not op:
        return
    marker = " is " + op + " its "
    idx = text.find(marker)
    if idx >= 0:
        rest = text[idx + len(marker):].strip().rstrip(":")
        cond.field2 = rest


def _build_event_v1_manual(text: str, line_num: int) -> EventRule:
    """Fallback: parse v1 event line manually."""
    # 'When a CONTENT is TRIGGER [and its FIELD1 is OP its FIELD2]:'
    rest = text[len("When "):].strip().rstrip(":")
    # Remove article
    for article in ("a ", "an ", "the "):
        if rest.startswith(article):
            rest = rest[len(article):]
            break

    # Find 'is created/updated/deleted'
    for trigger in ("created", "updated", "deleted"):
        is_trigger = " is " + trigger
        idx = rest.find(is_trigger)
        if idx >= 0:
            content = rest[:idx].strip()
            ev = EventRule(content_name=content, trigger=trigger, line=line_num)
            after = rest[idx + len(is_trigger):].strip()
            if after.startswith("and its "):
                _parse_event_condition(ev, after[len("and its "):].strip())
            return ev
    return EventRule(content_name=rest, trigger="unknown", line=line_num)


def _parse_event_condition(ev: EventRule, text: str) -> None:
    """Parse 'FIELD1 is OP its FIELD2' into an EventCondition."""
    ops = [
        ("at or below", "at or below"),
        ("above", "above"),
        ("below", "below"),
        ("equal to", "equal to"),
    ]
    for op_text, op_name in ops:
        marker = " is " + op_text + " its "
        idx = text.find(marker)
        if idx >= 0:
            field1 = text[:idx].strip()
            field2 = text[idx + len(marker):].strip()
            ev.condition = EventCondition(field1=field1, operator=op_name, field2=field2, line=ev.line)
            return


def _build_compute_shape(text: str, line_num: int) -> tuple:
    """Build compute shape data from a compute_shape_line.

    Always uses manual IO parsing because TatSu's greedy ``words`` rule
    merges untyped params like 'orders and order lines' into a single name.
    TatSu is only used to extract the shape keyword reliably.
    """
    # Extract shape keyword (before the colon)
    colon_idx = text.find(":")
    if colon_idx < 0:
        return ("transform", [], [], [], [])
    shape = text[:colon_idx].strip().lower()
    rest = text[colon_idx + 1:].strip()
    return _parse_compute_io_text(shape, rest)


def _parse_param_list(raw) -> list[ComputeParam]:
    """Parse a TatSu param_list result into ComputeParam list."""
    if raw is None:
        return []
    params = raw.get("param") if isinstance(raw, dict) else raw
    if params is None:
        return []
    if not isinstance(params, list):
        params = [params]
    result = []
    for p in params:
        if isinstance(p, dict):
            name = _qs(p.get("name", ""))
            type_name = str(p.get("type_name", "")).strip()
            result.append(ComputeParam(name=name, type_name=type_name))
        elif isinstance(p, str):
            result.append(ComputeParam(name=p.strip(), type_name=""))
    return result


def _parse_compute_io_text(shape: str, text: str) -> tuple:
    """Parse compute IO from free text like 'takes orders, produces one of bugs or features'."""
    inputs, outputs, in_p, out_p = [], [], [], []

    takes_idx = text.find("takes ")
    produces_idx = text.find("produces ")
    if takes_idx >= 0 and produces_idx >= 0:
        takes_text = text[takes_idx + 6:produces_idx].strip().rstrip(",").strip()
        produces_text = text[produces_idx + 9:].strip()

        # Remove articles
        for article in ("a ", "an ", "the "):
            if takes_text.startswith(article):
                takes_text = takes_text[len(article):]
            if produces_text.startswith(article):
                produces_text = produces_text[len(article):]

        # Check for typed params (name : Type)
        if " : " in takes_text or ": " in takes_text:
            in_p = _parse_typed_params_text(takes_text)
            inputs = [p.type_name for p in in_p] if in_p and in_p[0].type_name else [p.name for p in in_p]
        else:
            inputs = _split_and_list(takes_text)

        if produces_text.startswith("one of "):
            outputs = _split_or_and_comma(produces_text[7:])
        elif " : " in produces_text or ": " in produces_text:
            out_p = _parse_typed_params_text(produces_text)
            outputs = [p.type_name for p in out_p] if out_p and out_p[0].type_name else [p.name for p in out_p]
        else:
            outputs = _split_and_list(produces_text)

    return (shape, inputs, outputs, in_p, out_p)


def _parse_typed_params_text(text: str) -> list[ComputeParam]:
    """Parse typed params from text like 'u : UserProfile and msg : Text'."""
    # Split by 'and' or ','
    parts = text.replace(" and ", ",").split(",")
    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Remove article
        for article in ("a ", "an ", "the "):
            if part.startswith(article):
                part = part[len(article):]
        colon_idx = part.find(":")
        if colon_idx > 0:
            name = part[:colon_idx].strip().strip('"')
            type_name = part[colon_idx + 1:].strip()
            result.append(ComputeParam(name=name, type_name=type_name))
        else:
            result.append(ComputeParam(name=part.strip(), type_name=""))
    return result


def _split_and_list(text: str) -> list[str]:
    """Split by 'and' and commas."""
    parts = text.replace(" and ", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def _split_or_and_comma(text: str) -> list[str]:
    """Split by 'or' and commas."""
    parts = text.replace(" or ", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def _build_compute_shape_manual(text: str, line_num: int) -> tuple:
    """Fallback: parse compute shape manually."""
    colon_idx = text.find(":")
    if colon_idx < 0:
        return ("transform", [], [], [], [])
    shape = text[:colon_idx].strip().lower()
    rest = text[colon_idx + 1:].strip()
    return _parse_compute_io_text(shape, rest)


def _build_error_action(text: str, line_num: int) -> ErrorAction:
    """Build ErrorAction from a 'Then ...' line."""
    result = _try_parse(text, "error_then_line")
    if result is not None:
        action_raw = result.get("action")
        rule = _get_rule_name(action_raw)
        if rule == "ActionDisable":
            return ErrorAction(kind="disable", target=str(action_raw.get("target", "")).strip(), line=line_num)
        elif rule == "ActionEscalate" or action_raw == "escalate":
            return ErrorAction(kind="escalate", line=line_num)
        elif rule == "ActionNotify":
            return ErrorAction(kind="notify", target=_qs(action_raw.get("role", "")),
                               jexl_expr=_qs(action_raw.get("expr", "")), line=line_num)
        elif rule == "ActionCreate":
            return ErrorAction(kind="create", target=_qs(action_raw.get("name", "")), line=line_num)
        elif rule == "ActionSet":
            return ErrorAction(kind="set", jexl_expr=_qs(action_raw.get("expr", "")), line=line_num)

    # Manual fallback
    rest = text[len("Then "):].strip()
    if rest.startswith("disable "):
        return ErrorAction(kind="disable", target=rest[8:].strip(), line=line_num)
    elif rest == "escalate":
        return ErrorAction(kind="escalate", line=line_num)
    elif rest.startswith("notify "):
        target = _extract_first_quoted(rest)
        jexl = _extract_jexl_bracket(rest)
        return ErrorAction(kind="notify", target=target, jexl_expr=jexl or "", line=line_num)
    elif rest.startswith("create "):
        target = _extract_first_quoted(rest)
        return ErrorAction(kind="create", target=target, line=line_num)
    elif rest.startswith("set "):
        jexl = _extract_jexl_bracket(rest)
        return ErrorAction(kind="set", jexl_expr=jexl or "", line=line_num)
    return ErrorAction(kind="unknown", target=rest, line=line_num)


# ---------------------------------------------------------------------------
# Parse + classify each line
# ---------------------------------------------------------------------------

def _parse_line(text: str, rule: str, line_num: int):
    """Parse a single classified line and return a partial AST fragment."""

    if rule == "application_line":
        r = _try_parse(text, rule)
        if r:
            return ("application", Application(name=str(r["name"]).strip(), line=line_num))
        # Fallback
        return ("application", Application(name=text[len("Application:"):].strip(), line=line_num))

    if rule == "description_line":
        r = _try_parse(text, rule)
        if r:
            return ("description", str(r["desc"]).strip())
        return ("description", text[len("Description:"):].strip())

    if rule == "identity_line":
        r = _try_parse(text, rule)
        if r:
            return ("identity", Identity(provider=str(r["provider"]).strip(), line=line_num))
        return ("identity", Identity(provider=text.split("with", 1)[1].strip(), line=line_num))

    if rule == "scopes_line":
        r = _try_parse(text, rule)
        if r:
            return ("scopes", _quoted_list(r.get("scopes")))
        return ("scopes", _extract_quoted_strings(text))

    if rule == "role_standard_line":
        r = _try_parse(text, rule)
        if r:
            name = _qs(r.get("name", ""))
            scopes = _quoted_list(r.get("scopes"))
            return ("role", Role(name=name, scopes=scopes, line=line_num))
        # Fallback
        name = _extract_first_quoted(text)
        scopes = _extract_quoted_strings(text)
        # Remove the role name from scopes (it's the first quoted string in A "name" has ...)
        if scopes and scopes[0] == name:
            scopes = scopes[1:]
        return ("role", Role(name=name, scopes=scopes, line=line_num))

    if rule == "role_bare_line":
        r = _try_parse(text, rule)
        if r:
            name = str(r.get("name", "")).strip()
            scopes = _quoted_list(r.get("scopes"))
            return ("role", Role(name=name, scopes=scopes, line=line_num))
        # Fallback
        has_idx = text.find(" has ")
        name = text[:has_idx].strip() if has_idx >= 0 else text.strip()
        scopes = _extract_quoted_strings(text)
        return ("role", Role(name=name, scopes=scopes, line=line_num))

    if rule == "role_alias_line":
        r = _try_parse(text, rule)
        if r:
            return ("role_alias", RoleAlias(short_name=_qs(r.get("short", "")),
                                            full_name=_qs(r.get("full", "")), line=line_num))
        quotes = _extract_quoted_strings(text)
        return ("role_alias", RoleAlias(
            short_name=quotes[0] if quotes else "",
            full_name=quotes[1] if len(quotes) > 1 else "",
            line=line_num))

    if rule == "content_header":
        r = _try_parse(text, rule)
        if r:
            name = _qs(r.get("name", ""))
        else:
            name = _extract_first_quoted(text)
        singular = name.rstrip("s") if name.endswith("s") else name
        return ("content_header", Content(name=name, singular=singular, line=line_num))

    if rule == "field_line":
        r = _try_parse(text, rule)
        if r:
            singular = str(r.get("singular", "")).strip()
            field_name = str(r.get("field_name", "")).strip()
            # Use manual type parsing for reliability -- TatSu's named
            # alternatives don't expose which variant matched
            which_idx = text.find(" which ")
            if which_idx >= 0:
                after_which = text[which_idx + 7:].strip()
                if after_which.startswith("is "):
                    te = _parse_type_text(after_which[3:], line_num)
                elif after_which.startswith("references "):
                    ref_text = after_which[11:].strip()
                    comma_idx = ref_text.find(",")
                    constraints_text = ""
                    if comma_idx >= 0:
                        constraints_text = ref_text[comma_idx:]
                        ref_text = ref_text[:comma_idx].strip()
                    te = TypeExpr(base_type="reference", references=ref_text.strip('"'), line=line_num)
                    if "required" in constraints_text:
                        te.required = True
                    if "unique" in constraints_text:
                        te.unique = True
                else:
                    te = TypeExpr(base_type="text", line=line_num)
            else:
                te = TypeExpr(base_type="text", line=line_num)
            return ("field", Field(name=field_name, type_expr=te, line=line_num), singular)
        # Manual fallback for field lines
        return _parse_field_manual(text, line_num)

    if rule == "access_line":
        r = _try_parse(text, rule)
        if r:
            return ("access", _build_access_rule(r, line_num))
        # Fallback
        scope = _extract_first_quoted(text)
        can_idx = text.find(" can ")
        if can_idx >= 0:
            rest = text[can_idx + 5:].strip()
            space_idx = rest.rfind(" ")
            verb = rest[:space_idx].strip() if space_idx >= 0 else rest
            return ("access", AccessRule(scope=scope, verbs=[verb], line=line_num))
        return ("access", AccessRule(scope=scope, verbs=["view"], line=line_num))

    if rule == "state_header":
        r = _try_parse(text, rule)
        if r:
            target = str(r.get("target", "")).strip()
            machine_name = _qs(r.get("name", ""))
        else:
            # Manual
            machine_name = _extract_first_quoted(text)
            called_idx = text.find(" called ")
            target = text[len("State for "):called_idx].strip() if called_idx >= 0 else ""
        return ("state_header", StateMachine(content_name=target, machine_name=machine_name,
                                             singular="", initial_state="", line=line_num))

    if rule == "state_starts_line":
        r = _try_parse(text, rule)
        if r:
            singular = str(r.get("singular", "")).strip()
            state = _qs(r.get("state", ""))
            return ("state_starts", singular, state)
        # Fallback
        state = _extract_first_quoted(text)
        # 'A SINGULAR starts as "STATE"'
        starts_idx = text.find(" starts as ")
        before = text[:starts_idx].strip() if starts_idx >= 0 else ""
        # Remove article
        for article in ("A ", "An "):
            if before.startswith(article):
                before = before[len(article):]
        return ("state_starts", before.strip(), state)

    if rule == "state_also_line":
        r = _try_parse(text, rule)
        if r:
            states = _quoted_list(r.get("states"))
            return ("state_also", states)
        return ("state_also", _extract_quoted_strings(text))

    if rule == "state_transition_line":
        t = _build_transition(text, line_num)
        if t:
            return ("state_transition", t)
        return None

    if rule == "event_jexl_line":
        r = _try_parse(text, rule)
        if r:
            jexl = _qs(r.get("jexl", ""))
            return ("event_header", EventRule(content_name="", trigger="jexl",
                                             jexl_condition=jexl, line=line_num))
        jexl = _extract_jexl_bracket(text)
        return ("event_header", EventRule(content_name="", trigger="jexl",
                                         jexl_condition=jexl or "", line=line_num))

    if rule == "event_v1_line":
        ev = _build_event_v1(text, line_num)
        return ("event_header", ev)

    if rule == "event_action_line":
        r = _try_parse(text, rule)
        if r:
            name = _qs(r.get("name", ""))
            fields = _comma_list(r.get("fields"))
            return ("event_action", EventAction(create_content=name, fields=fields, line=line_num))
        # Fallback
        return _parse_event_action_manual(text, line_num)

    if rule == "log_level_line":
        r = _try_parse(text, rule)
        if r:
            return ("log_level", str(r.get("level", "")).strip())
        # Fallback
        return ("log_level", text.split(":", 1)[1].strip() if ":" in text else "")

    if rule == "error_from_line":
        r = _try_parse(text, rule)
        if r:
            source = _qs(r.get("source", ""))
            jexl = _qs(r.get("jexl", "")) if r.get("jexl") else None
            return ("error_header", ErrorHandler(source=source, condition_jexl=jexl, line=line_num))
        source = _extract_first_quoted(text)
        jexl = _extract_jexl_bracket(text)
        return ("error_header", ErrorHandler(source=source, condition_jexl=jexl, line=line_num))

    if rule == "error_catch_all_line":
        return ("error_header", ErrorHandler(source="", is_catch_all=True, line=line_num))

    if rule == "error_retry_line":
        r = _try_parse(text, rule)
        if r:
            count = _safe_int(r.get("count"), 1)
            backoff = "backoff" in text.lower()
            max_delay = str(r.get("max_delay", "")).strip() if r.get("max_delay") else None
            return ("error_retry", ErrorAction(kind="retry", retry_count=count,
                                               retry_backoff=backoff, retry_max_delay=max_delay, line=line_num))
        # Fallback
        parts = text.split()
        count = 1
        for i, p in enumerate(parts):
            if p.isdigit():
                count = int(p)
                break
        return ("error_retry", ErrorAction(kind="retry", retry_count=count,
                                           retry_backoff="backoff" in text.lower(), line=line_num))

    if rule == "error_then_line":
        return ("error_then", _build_error_action(text, line_num))

    if rule == "story_header":
        story = _build_story(text, line_num)
        return ("story_header", story)

    if rule == "so_that_line":
        r = _try_parse(text, rule)
        if r:
            return ("so_that", str(r.get("text", "")).strip().rstrip(":"))
        rest = text[len("so that "):].strip().rstrip(":")
        return ("so_that", rest)

    if rule == "show_page_line":
        r = _try_parse(text, rule)
        if r:
            return ("directive", ShowPage(page_name=_qs(r.get("name", "")), line=line_num))
        return ("directive", ShowPage(page_name=_extract_first_quoted(text), line=line_num))

    if rule == "display_table_line":
        content_name, cols = _split_display_table(text)
        return ("directive", DisplayTable(content_name=content_name, columns=cols, line=line_num))

    if rule == "show_related_line":
        singular, related, group_by = _split_show_related(text)
        return ("directive", ShowRelated(singular=singular, related_content=related,
                                        group_by=group_by, line=line_num))

    if rule == "highlight_rows_line":
        # Try JEXL first
        rest = text[len("Highlight rows where "):].strip()
        jexl = _extract_jexl_bracket(rest)
        if jexl:
            return ("directive", HighlightRows(jexl_condition=jexl, line=line_num))
        field, op, threshold = _split_highlight_free(text)
        return ("directive", HighlightRows(field=field, operator=op,
                                           threshold_field=threshold, line=line_num))

    if rule == "allow_filtering_line":
        r = _try_parse(text, rule)
        if r:
            fields = _comma_list(r.get("fields"))
            return ("directive", AllowFilter(fields=fields, line=line_num))
        rest = text[len("Allow filtering by "):].strip()
        return ("directive", AllowFilter(fields=_split_comma_and_list(rest), line=line_num))

    if rule == "allow_searching_line":
        r = _try_parse(text, rule)
        if r:
            fields = _or_list(r.get("fields"))
            return ("directive", AllowSearch(fields=fields, line=line_num))
        rest = text[len("Allow searching by "):].strip()
        return ("directive", AllowSearch(fields=_split_or_list(rest), line=line_num))

    if rule == "subscribes_to_line":
        content = _split_subscribes_to(text)
        return ("directive", SubscribeTo(content_name=content, line=line_num))

    if rule == "accept_input_line":
        r = _try_parse(text, rule)
        if r:
            fields = _comma_list(r.get("fields"))
            return ("directive", AcceptInput(fields=fields, line=line_num))
        rest = text[len("Accept input for "):].strip()
        return ("directive", AcceptInput(fields=_split_comma_and_list(rest), line=line_num))

    if rule == "validate_unique_line":
        field, jexl = _split_validate_unique(text)
        if jexl:
            return ("directive", ValidateUnique(jexl_condition=jexl, line=line_num))
        return ("directive", ValidateUnique(field=field, line=line_num))

    if rule == "create_as_line":
        state = _split_create_as(text)
        return ("directive", CreateAs(initial_state=state, line=line_num))

    if rule == "after_saving_line":
        r = _try_parse(text, rule)
        if r:
            return ("directive", AfterSave(instruction=str(r.get("text", "")).strip(), line=line_num))
        rest = text[len("After saving,"):].strip()
        return ("directive", AfterSave(instruction=rest, line=line_num))

    if rule == "show_chart_line":
        content_name, days = _split_show_chart(text)
        return ("directive", ShowChart(content_name=content_name, days=days, line=line_num))

    if rule == "display_text_line":
        r = _try_parse(text, rule)
        if r:
            # Check which variant
            jexl = r.get("jexl")
            if jexl:
                return ("directive", DisplayText(text=_qs(jexl), is_expression=True, line=line_num))
            quoted = r.get("text")
            if quoted:
                return ("directive", DisplayText(text=_qs(quoted), line=line_num))
            expr = r.get("expr")
            if expr:
                return ("directive", DisplayText(text=str(expr).strip(), is_expression=True, line=line_num))
        # Fallback
        rest = text[len("Display text"):].strip()
        jexl = _extract_jexl_bracket(rest)
        if jexl:
            return ("directive", DisplayText(text=jexl, is_expression=True, line=line_num))
        quoted = _extract_first_quoted(rest)
        if quoted:
            return ("directive", DisplayText(text=quoted, line=line_num))
        return ("directive", DisplayText(text=rest.strip(), is_expression=True, line=line_num))

    if rule == "display_agg_line":
        r = _try_parse(text, rule)
        if r:
            return ("directive", DisplayAggregation(description=str(r.get("text", "")).strip(), line=line_num))
        rest = text[len("Display "):].strip()
        return ("directive", DisplayAggregation(description=rest, line=line_num))

    if rule == "nav_bar_line":
        return ("nav_bar",)

    if rule == "nav_item_line":
        return ("nav_item", _build_nav_item(text, line_num))

    if rule == "api_header_line":
        r = _try_parse(text, rule)
        if r:
            path = str(r.get("path", "")).strip().rstrip(":")
            return ("api_header", ApiSection(base_path=path, line=line_num))
        # Fallback
        rest = text[len("Expose a REST API at "):].strip().rstrip(":")
        return ("api_header", ApiSection(base_path=rest, line=line_num))

    if rule == "api_endpoint_line":
        r = _try_parse(text, rule)
        if r:
            method = str(r.get("method", "")).strip()
            path = str(r.get("path", "")).strip()
            desc = str(r.get("desc", "")).strip() if r.get("desc") else ""
            return ("api_endpoint", ApiEndpoint(method=method, path=path, description=desc, line=line_num))
        # Fallback
        parts = text.strip().split(None, 2)
        method = parts[0] if parts else ""
        path = parts[1] if len(parts) > 1 else ""
        desc = parts[2] if len(parts) > 2 else ""
        return ("api_endpoint", ApiEndpoint(method=method, path=path, description=desc.strip(), line=line_num))

    if rule == "stream_line":
        desc, path = _split_stream(text)
        return ("stream", Stream(description=desc, path=path, line=line_num))

    if rule == "compute_header":
        r = _try_parse(text, rule)
        if r:
            name = _qs(r.get("name", ""))
        else:
            name = _extract_first_quoted(text)
        return ("compute_header", ComputeNode(name=name, line=line_num))

    if rule == "compute_shape_line":
        shape_data = _build_compute_shape(text, line_num)
        return ("compute_shape", shape_data)

    if rule == "compute_body_jexl_line":
        content = text[1:-1].strip()  # Strip [ and ]
        return ("compute_body", content)

    if rule == "compute_access_line":
        r = _try_parse(text, rule)
        if r:
            role = _qs(r.get("role", ""))
            return ("compute_access", role)
        # Fallback
        can_idx = text.find(" can execute this")
        if can_idx >= 0:
            role = text[:can_idx].strip().strip('"')
            return ("compute_access", role)
        return ("compute_access", "")

    if rule == "channel_header":
        r = _try_parse(text, rule)
        if r:
            name = _qs(r.get("name", ""))
        else:
            name = _extract_first_quoted(text)
        return ("channel_header", ChannelDecl(name=name, line=line_num))

    if rule == "channel_carries_line":
        r = _try_parse(text, rule)
        if r:
            return ("channel_prop", "carries", str(r.get("content", "")).strip())
        return ("channel_prop", "carries", text[len("Carries "):].strip())

    if rule == "channel_direction_line":
        r = _try_parse(text, rule)
        if r:
            return ("channel_prop", "direction", str(r.get("dir", "")).strip().lower())
        return ("channel_prop", "direction", text.split(":", 1)[1].strip().lower())

    if rule == "channel_delivery_line":
        r = _try_parse(text, rule)
        if r:
            return ("channel_prop", "delivery", str(r.get("del", "")).strip().lower())
        return ("channel_prop", "delivery", text.split(":", 1)[1].strip().lower())

    if rule == "channel_requires_line":
        r = _try_parse(text, rule)
        if r:
            scope = _qs(r.get("scope", ""))
            direction = str(r.get("dir", "")).strip()
            return ("channel_prop", "requires", ChannelRequirement(scope=scope, direction=direction, line=line_num))
        scope = _extract_first_quoted(text)
        direction = "send" if " to send" in text else "receive"
        return ("channel_prop", "requires", ChannelRequirement(scope=scope, direction=direction, line=line_num))

    if rule == "channel_endpoint_line":
        r = _try_parse(text, rule)
        if r:
            return ("channel_prop", "endpoint", str(r.get("path", "")).strip())
        return ("channel_prop", "endpoint", text.split(":", 1)[1].strip())

    if rule == "boundary_header":
        r = _try_parse(text, rule)
        if r:
            name = _qs(r.get("name", ""))
        else:
            name = _extract_first_quoted(text)
        return ("boundary_header", BoundaryDecl(name=name, line=line_num))

    if rule == "boundary_contains_line":
        r = _try_parse(text, rule)
        if r:
            items_raw = r.get("items_") or r.get("items")
            items = _comma_list(items_raw)
            return ("boundary_prop", "contains", items)
        rest = text[len("Contains "):].strip()
        return ("boundary_prop", "contains", _split_comma_and_list(rest))

    if rule == "boundary_inherits_line":
        r = _try_parse(text, rule)
        if r:
            parent = str(r.get("parent", "")).strip()
            return ("boundary_prop", "inherits", parent)
        rest = text[len("Identity inherits from "):].strip()
        return ("boundary_prop", "inherits", rest)

    if rule == "boundary_restricts_line":
        r = _try_parse(text, rule)
        if r:
            scopes = _quoted_list(r.get("scopes"))
            return ("boundary_prop", "restricts", scopes)
        return ("boundary_prop", "restricts", _extract_quoted_strings(text))

    if rule == "boundary_exposes_line":
        r = _try_parse(text, rule)
        if r:
            name = _qs(r.get("name", ""))
            type_name = str(r.get("type_name", "")).strip()
            jexl = _qs(r.get("jexl", ""))
            return ("boundary_prop", "exposes",
                    BoundaryProperty(name=name, type_name=type_name, jexl_expr=jexl, line=line_num))
        # Fallback
        name = _extract_first_quoted(text)
        jexl = _extract_jexl_bracket(text)
        # type_name is between ': ' and '='
        colon_idx = text.find(":", text.find('"', text.find('"') + 1) + 1)
        eq_idx = text.find("=")
        type_name = text[colon_idx + 1:eq_idx].strip() if colon_idx >= 0 and eq_idx > colon_idx else ""
        return ("boundary_prop", "exposes",
                BoundaryProperty(name=name, type_name=type_name, jexl_expr=jexl or "", line=line_num))

    return None


def _parse_field_manual(text: str, line_num: int):
    """Manual fallback for field_line parsing."""
    # 'Each SINGULAR has a FIELD which is/references TYPE'
    has_idx = text.find(" has ")
    if has_idx < 0:
        return ("field", Field(name="unknown", type_expr=TypeExpr(base_type="text"), line=line_num), "")

    singular_part = text[len("Each "):has_idx].strip()
    after_has = text[has_idx + 5:].strip()

    # Remove article
    for article in ("a ", "an ", "the "):
        if after_has.startswith(article):
            after_has = after_has[len(article):]
            break

    which_idx = after_has.find(" which ")
    if which_idx < 0:
        return ("field", Field(name="unknown", type_expr=TypeExpr(base_type="text"), line=line_num), singular_part)

    field_name = after_has[:which_idx].strip()
    type_part = after_has[which_idx + 7:].strip()  # after ' which '

    if type_part.startswith("is "):
        type_text = type_part[3:].strip()
    elif type_part.startswith("references "):
        ref_name = type_part[11:].strip()
        # Check for constraints after the reference name
        comma_idx = ref_name.find(",")
        constraints_text = ""
        if comma_idx >= 0:
            constraints_text = ref_name[comma_idx:]
            ref_name = ref_name[:comma_idx].strip()
        te = TypeExpr(base_type="reference", references=ref_name.strip('"'), line=line_num)
        if "required" in constraints_text:
            te.required = True
        if "unique" in constraints_text:
            te.unique = True
        return ("field", Field(name=field_name, type_expr=te, line=line_num), singular_part)
    else:
        type_text = type_part

    te = _parse_type_text(type_text, line_num)
    return ("field", Field(name=field_name, type_expr=te, line=line_num), singular_part)


def _parse_type_text(text: str, line_num: int = 0) -> TypeExpr:
    """Parse a type expression from text using string operations only."""
    expr = TypeExpr(base_type="text", line=line_num)

    # Extract constraints at end: ', required', ', unique', ', minimum N', ', maximum N'
    while True:
        text_lower = text.lower().rstrip()
        if text_lower.endswith(", required") or text_lower.endswith(",required"):
            expr.required = True
            text = text[:text.lower().rfind("required")].rstrip().rstrip(",").strip()
            continue
        if text_lower.endswith(", unique") or text_lower.endswith(",unique"):
            expr.unique = True
            text = text[:text.lower().rfind("unique")].rstrip().rstrip(",").strip()
            continue
        # Check for minimum N
        min_marker = ", minimum "
        min_idx = text_lower.rfind(min_marker)
        if min_idx >= 0:
            val_text = text[min_idx + len(min_marker):].strip().rstrip(",").strip()
            # val_text might have more constraints after it
            space = val_text.find(",")
            if space >= 0:
                expr.minimum = _safe_int(val_text[:space].strip())
            else:
                expr.minimum = _safe_int(val_text)
            text = text[:min_idx].strip()
            continue
        max_marker = ", maximum "
        max_idx = text_lower.rfind(max_marker)
        if max_idx >= 0:
            val_text = text[max_idx + len(max_marker):].strip().rstrip(",").strip()
            space = val_text.find(",")
            if space >= 0:
                expr.maximum = _safe_int(val_text[:space].strip())
            else:
                expr.maximum = _safe_int(val_text)
            text = text[:max_idx].strip()
            continue
        break

    text = text.strip()

    if text.startswith("unique "):
        expr.unique = True
        text = text[7:].strip()

    if text == "text":
        expr.base_type = "text"
    elif text == "currency":
        expr.base_type = "currency"
    elif text == "number":
        expr.base_type = "number"
    elif text == "percentage":
        expr.base_type = "percentage"
    elif text in ("true/false", "boolean"):
        expr.base_type = "boolean"
    elif text == "date and time":
        expr.base_type = "datetime"
    elif text == "date":
        expr.base_type = "date"
    elif text == "automatic":
        expr.base_type = "automatic"
    elif text in ("a whole number", "whole number"):
        expr.base_type = "whole_number"
    elif text.startswith("one of:"):
        expr.base_type = "enum"
        vals = text[7:].strip()
        expr.enum_values = [v.strip().strip('"') for v in _split_comma_and_list(vals)]
    elif text.startswith("list of "):
        expr.base_type = "list"
        expr.list_type = text[8:].strip().strip('"')
    return expr


def _parse_event_action_manual(text: str, line_num: int):
    """Parse event action line manually."""
    # 'Create a NAME with [the] FIELDS'
    rest = text[len("Create "):].strip()
    # Remove article
    for article in ("a ", "an ", "the "):
        if rest.startswith(article):
            rest = rest[len(article):]
            break

    with_idx = rest.find(" with ")
    if with_idx >= 0:
        name = rest[:with_idx].strip().strip('"')
        fields_text = rest[with_idx + 6:].strip()
        # Remove leading 'the '
        if fields_text.startswith("the "):
            fields_text = fields_text[4:]
        fields = _split_comma_and_list(fields_text)
        return ("event_action", EventAction(create_content=name, fields=fields, line=line_num))
    return ("event_action", EventAction(create_content=rest.strip().strip('"'), line=line_num))


# ---------------------------------------------------------------------------
# Block assembly
# ---------------------------------------------------------------------------

def _assemble(parsed_lines: list) -> Program:
    """Assemble parsed line fragments into a complete Program AST."""
    prog = Program()
    i = 0
    n = len(parsed_lines)

    while i < n:
        item = parsed_lines[i]
        if item is None:
            i += 1
            continue

        kind = item[0]

        if kind == "application":
            app = item[1]
            # Check next line for description
            if i + 1 < n and parsed_lines[i + 1] is not None and parsed_lines[i + 1][0] == "description":
                app.description = parsed_lines[i + 1][1]
                i += 2
            else:
                i += 1
            prog.application = app

        elif kind == "description":
            # Standalone description (should have been consumed by application)
            if prog.application:
                prog.application.description = item[1]
            i += 1

        elif kind == "identity":
            prog.identity = item[1]
            i += 1

        elif kind == "scopes":
            if prog.identity:
                prog.identity.scopes = item[1]
            else:
                prog.identity = Identity(provider="stub", scopes=item[1])
            i += 1

        elif kind == "role":
            prog.roles.append(item[1])
            i += 1

        elif kind == "role_alias":
            prog.role_aliases.append(item[1])
            i += 1

        elif kind == "content_header":
            content = item[1]
            i += 1
            # Collect fields and access rules
            while i < n and parsed_lines[i] is not None:
                ck = parsed_lines[i][0]
                if ck == "field":
                    field = parsed_lines[i][1]
                    singular = parsed_lines[i][2]
                    if singular and not content.singular:
                        content.singular = singular
                    elif singular:
                        content.singular = singular
                    content.fields.append(field)
                    i += 1
                elif ck == "access":
                    content.access_rules.append(parsed_lines[i][1])
                    i += 1
                else:
                    break
            prog.contents.append(content)

        elif kind == "state_header":
            sm = item[1]
            i += 1
            while i < n and parsed_lines[i] is not None:
                sk = parsed_lines[i][0]
                if sk == "state_starts":
                    sm.singular = parsed_lines[i][1]
                    sm.initial_state = parsed_lines[i][2]
                    sm.states.append(sm.initial_state)
                    i += 1
                elif sk == "state_also":
                    sm.states.extend(parsed_lines[i][1])
                    i += 1
                elif sk == "state_transition":
                    sm.transitions.append(parsed_lines[i][1])
                    i += 1
                else:
                    break
            prog.state_machines.append(sm)

        elif kind == "event_header":
            ev = item[1]
            i += 1
            while i < n and parsed_lines[i] is not None:
                ek = parsed_lines[i][0]
                if ek == "event_action":
                    ev.action = parsed_lines[i][1]
                    i += 1
                elif ek == "log_level":
                    ev.log_level = parsed_lines[i][1]
                    i += 1
                else:
                    break
            prog.events.append(ev)

        elif kind == "error_header":
            handler = item[1]
            i += 1
            while i < n and parsed_lines[i] is not None:
                ek = parsed_lines[i][0]
                if ek == "error_retry":
                    handler.actions.append(parsed_lines[i][1])
                    i += 1
                elif ek == "error_then":
                    handler.actions.append(parsed_lines[i][1])
                    i += 1
                elif ek == "log_level":
                    handler.actions.append(ErrorAction(kind="log_level", target=parsed_lines[i][1]))
                    i += 1
                else:
                    break
            prog.error_handlers.append(handler)

        elif kind == "story_header":
            story = item[1]
            i += 1
            # Collect so_that and directives
            while i < n and parsed_lines[i] is not None:
                dk = parsed_lines[i][0]
                if dk == "so_that":
                    story.objective = parsed_lines[i][1]
                    i += 1
                elif dk == "directive":
                    story.directives.append(parsed_lines[i][1])
                    i += 1
                else:
                    break
            prog.stories.append(story)

        elif kind == "nav_bar":
            nav = NavBar()
            i += 1
            while i < n and parsed_lines[i] is not None:
                nk = parsed_lines[i][0]
                if nk == "nav_item":
                    nav.items.append(parsed_lines[i][1])
                    i += 1
                else:
                    break
            prog.navigation = nav

        elif kind == "api_header":
            api = item[1]
            i += 1
            while i < n and parsed_lines[i] is not None:
                ak = parsed_lines[i][0]
                if ak == "api_endpoint":
                    api.endpoints.append(parsed_lines[i][1])
                    i += 1
                else:
                    break
            prog.api = api

        elif kind == "stream":
            prog.streams.append(item[1])
            i += 1

        elif kind == "compute_header":
            node = item[1]
            i += 1
            while i < n and parsed_lines[i] is not None:
                ck = parsed_lines[i][0]
                if ck == "compute_shape":
                    shape_data = parsed_lines[i][1]
                    node.shape = shape_data[0]
                    node.inputs = shape_data[1]
                    node.outputs = shape_data[2]
                    node.input_params = shape_data[3]
                    node.output_params = shape_data[4]
                    i += 1
                elif ck == "compute_body":
                    node.body_lines.append(parsed_lines[i][1])
                    i += 1
                elif ck == "compute_access":
                    role = parsed_lines[i][1]
                    # Check if it looks like a scope (has quotes)
                    if role:
                        node.access_role = role
                    i += 1
                elif ck == "access":
                    node.access_scope = parsed_lines[i][1].scope
                    i += 1
                else:
                    break
            # Check if any access line was actually 'Anyone with "scope" can...'
            # vs role-based 'RoleName can execute this'
            # If access_role is a quoted scope, move it to access_scope
            prog.computes.append(node)

        elif kind == "channel_header":
            ch = item[1]
            i += 1
            while i < n and parsed_lines[i] is not None:
                ck = parsed_lines[i][0]
                if ck == "channel_prop":
                    prop_kind = parsed_lines[i][1]
                    value = parsed_lines[i][2]
                    if prop_kind == "carries":
                        ch.carries = value
                    elif prop_kind == "direction":
                        ch.direction = value
                    elif prop_kind == "delivery":
                        ch.delivery = value
                    elif prop_kind == "endpoint":
                        ch.endpoint = value
                    elif prop_kind == "requires":
                        ch.requirements.append(value)
                    i += 1
                else:
                    break
            prog.channels.append(ch)

        elif kind == "boundary_header":
            bnd = item[1]
            i += 1
            while i < n and parsed_lines[i] is not None:
                bk = parsed_lines[i][0]
                if bk == "boundary_prop":
                    prop_kind = parsed_lines[i][1]
                    value = parsed_lines[i][2]
                    if prop_kind == "contains":
                        bnd.contains = value
                    elif prop_kind == "inherits":
                        bnd.identity_mode = "inherit"
                        bnd.identity_parent = value
                    elif prop_kind == "restricts":
                        bnd.identity_mode = "restrict"
                        bnd.identity_scopes = value
                    elif prop_kind == "exposes":
                        bnd.properties.append(value)
                    i += 1
                else:
                    break
            prog.boundaries.append(bnd)

        else:
            # Unknown or unhandled — skip
            i += 1

    return prog


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_peg(source: str) -> tuple[Program, CompileResult]:
    """Parse a .termin source string using the TatSu PEG grammar.

    Drop-in replacement for ``termin.parser.parse()``.
    """
    errors = CompileResult()

    try:
        lines = _preprocess(source)
    except Exception as e:
        errors.add(ParseError(message=f"Preprocessing failed: {e}", line=0))
        return Program(), errors

    parsed = []
    for line_num, text in lines:
        rule = _classify_line(text)
        if rule == "unknown":
            errors.add(ParseError(message=f"Unrecognized line: {text}", line=line_num, source_line=text))
            continue
        try:
            result = _parse_line(text, rule, line_num)
            if result is not None:
                parsed.append(result)
        except Exception as e:
            errors.add(ParseError(message=f"Failed to parse line: {e}", line=line_num, source_line=text))

    if not errors.ok:
        return Program(), errors

    try:
        program = _assemble(parsed)
    except Exception as e:
        errors.add(ParseError(message=f"Block assembly failed: {e}", line=0))
        return Program(), errors

    return program, errors
