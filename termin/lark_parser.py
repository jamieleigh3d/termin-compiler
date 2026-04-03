"""Lark-based parser for the Termin DSL v2 (backward-compatible with v1).

Uses a two-phase approach:
  1. **Pre-process**: strip comments/dividers, classify each line via the
     existing lexer, and emit a tagged token stream that Lark can parse.
  2. **Lark parse**: the grammar in grammar.lark defines the block structure
     (which child lines belong to which top-level declaration).
  3. **Transformer**: converts the Lark parse tree into the same AST dataclass
     nodes produced by the hand-rolled parser (``termin.parser.parse``).

Public API
----------
    parse_lark(source: str) -> tuple[Program, CompileResult]

This is a drop-in replacement for ``termin.parser.parse``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from lark import Lark, Token as LarkToken, Transformer, v_args, Tree

from .ast_nodes import (
    Program, Application, Identity, Role, Content, Field, TypeExpr,
    AccessRule, StateMachine, Transition, EventRule, EventCondition,
    EventAction, UserStory, ShowPage, DisplayTable, ShowRelated,
    HighlightRows, AllowFilter, AllowSearch, SubscribeTo, AcceptInput,
    ValidateUnique, CreateAs, AfterSave, ShowChart, DisplayAggregation,
    NavBar, NavItem, ApiSection, ApiEndpoint, Stream, Directive,
    ComputeNode, ComputeParam, ChannelDecl, ChannelRequirement, BoundaryDecl,
    DisplayText,
)
from .errors import ParseError, CompileResult

# ---------------------------------------------------------------------------
# Helpers (shared with hand-rolled parser)
# ---------------------------------------------------------------------------

def _extract_quoted(text: str) -> list[str]:
    """Extract all double-quoted strings from text."""
    return re.findall(r'"([^"]*)"', text)


def _extract_jexl(text: str) -> Optional[str]:
    m = re.search(r'\[([^\]]+)\]', text)
    return m.group(1).strip() if m else None


def _parse_comma_list(text: str) -> list[str]:
    text = text.strip().rstrip(":")
    parts = re.split(r',\s*(?:and\s+)?|\s+and\s+', text)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# Pre-processor: normalise source into tagged lines
# ---------------------------------------------------------------------------

# Line patterns — order matters (first match wins).  Each entry is
# (regex on stripped line, tag string emitted to Lark).
_LINE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'^Application:\s+'),                          'APPLICATION'),
    (re.compile(r'^\s*Description:\s+'),                       'DESCRIPTION'),
    (re.compile(r'^Users authenticate with\s+'),               'IDENTITY'),
    (re.compile(r'^Scopes are\s+'),                            'SCOPES'),
    (re.compile(r'^(?:A|An)\s+"[^"]+"\s+has\s+'),              'ROLE_STD'),
    (re.compile(r'^\w+\s+has\s+"[^"]+"'),                      'ROLE_BARE'),
    (re.compile(r'^Content called\s+"[^"]+"'),                  'CONTENT_DECL'),
    (re.compile(r'^\s*Each\s+.+?\s+has\s+(?:a|an)\s+'),       'FIELD_DECL'),
    (re.compile(r'^\s*Anyone with\s+"[^"]+"\s+can\s+'),        'ACCESS_RULE'),
    (re.compile(r'^State for\s+\w+\s+called\s+"[^"]+"'),       'STATE_DECL'),
    (re.compile(r'^\s*(?:A|An)\s+\w+\s+starts\s+as\s+"[^"]+"'), 'STATE_STARTS'),
    (re.compile(r'^\s*(?:A|An)\s+\w+\s+can\s+also\s+be\s+'),  'STATE_ALSO'),
    (re.compile(r'^\s*(?:A|An)\s+.+?\s+can\s+become\s+'),      'STATE_TRANS'),
    (re.compile(r'^When\s+\['),                                 'EVENT_JEXL'),
    (re.compile(r'^When\s+(?:a|an)\s+'),                        'EVENT_V1'),
    (re.compile(r'^\s*Create\s+(?:a|an)\s+'),                   'EVENT_ACTION'),
    (re.compile(r'^As\s+(?:(?:a|an)\s+)?\w'),                   'STORY_HEADER'),
    (re.compile(r'^\s*so\s+that\s+'),                           'STORY_SO_THAT'),
    (re.compile(r'^\s*Show a page called\s+"[^"]+"'),           'SHOW_PAGE'),
    (re.compile(r'^\s*Display a table of\s+'),                  'DISPLAY_TABLE'),
    (re.compile(r'^\s*For each\s+'),                            'SHOW_RELATED'),
    (re.compile(r'^\s*Highlight rows where\s+'),                'HIGHLIGHT_ROWS'),
    (re.compile(r'^\s*Allow filtering by\s+'),                  'ALLOW_FILTER'),
    (re.compile(r'^\s*Allow searching by\s+'),                  'ALLOW_SEARCH'),
    (re.compile(r'^\s*This table subscribes to\s+'),            'SUBSCRIBES_TO'),
    (re.compile(r'^\s*Accept input for\s+'),                    'ACCEPT_INPUT'),
    (re.compile(r'^\s*Validate that\s+'),                       'VALIDATE'),
    (re.compile(r'^\s*Create the\s+'),                          'CREATE_AS'),
    (re.compile(r'^\s*After saving,\s+'),                       'AFTER_SAVING'),
    (re.compile(r'^\s*Show a chart of\s+'),                     'SHOW_CHART'),
    (re.compile(r'^\s*Display\s+text\s+'),                      'DISPLAY_TEXT'),
    (re.compile(r'^\s*Display\s+'),                             'DISPLAY_AGG'),
    (re.compile(r'^Navigation bar:'),                           'NAV_BAR'),
    (re.compile(r'^\s*"[^"]+"\s+links to\s+'),                  'NAV_ITEM'),
    (re.compile(r'^Expose a REST API at\s+'),                   'API_SECTION'),
    (re.compile(r'^\s*(?:GET|POST|PUT|DELETE|PATCH)\s+/'),      'API_ENDPOINT'),
    (re.compile(r'^Stream\s+'),                                 'STREAM_DECL'),
    (re.compile(r'^Compute called\s+"[^"]+"'),                  'COMPUTE_DECL'),
    (re.compile(r'^\s*(?:Transform|Reduce|Expand|Correlate|Route|Chain):\s+'), 'COMPUTE_SHAPE'),
    (re.compile(r'^Channel called\s+"[^"]+"'),                  'CHANNEL_DECL'),
    (re.compile(r'^\s*Carries\s+'),                             'CHANNEL_CARRIES'),
    (re.compile(r'^\s*Protocol:\s+'),                           'CHANNEL_PROTOCOL'),
    (re.compile(r'^\s*From\s+.+\s+to\s+'),                     'CHANNEL_DIR'),
    (re.compile(r'^\s*Requires\s+"[^"]+"\s+to\s+'),             'CHANNEL_REQ'),
    (re.compile(r'^\s*Endpoint:\s+'),                           'CHANNEL_EP'),
    (re.compile(r'^Boundary called\s+"[^"]+"'),                 'BOUNDARY_DECL'),
    (re.compile(r'^\s*Contains\s+'),                            'BOUNDARY_CONTAINS'),
    (re.compile(r'^\s*Identity\s+(?:inherits|restricts)'),      'BOUNDARY_IDENTITY'),
    (re.compile(r'^\s*\[.+\]\s*$'),                             'JEXL_BLOCK'),
]


class _PreprocessedLine:
    __slots__ = ("tag", "value", "line_num")
    def __init__(self, tag: str, value: str, line_num: int):
        self.tag = tag
        self.value = value
        self.line_num = line_num


def _preprocess(source: str) -> list[_PreprocessedLine]:
    """Classify each source line, stripping comments and blanks."""
    result: list[_PreprocessedLine] = []
    for line_num, raw in enumerate(source.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("---"):
            continue
        if stripped.startswith("(") and stripped.endswith(")"):
            continue
        # Strip inline parenthesis comments
        value = re.sub(r'\s+\([^)]*\)\s*$', '', stripped).strip()

        tag = "UNKNOWN"
        for pattern, t in _LINE_PATTERNS:
            if pattern.search(value):
                tag = t
                break
        result.append(_PreprocessedLine(tag, value, line_num))
    return result


# ---------------------------------------------------------------------------
# Lark grammar (built as a string for self-containment)
# ---------------------------------------------------------------------------
# We define a simple "block grammar" where each line is a tagged token.
# The grammar enforces block structure (which child lines follow headers).

_GRAMMAR_TEXT = r"""
start: (section)*

?section: application_block
        | identity_block
        | scopes_line
        | role_line
        | content_block
        | state_block
        | event_block
        | story_block
        | nav_block
        | api_block
        | stream_line
        | compute_block
        | channel_block
        | boundary_block
        | unknown_line

// -- Application --
application_block: APP_LINE description_line?
description_line: DESC_LINE

// -- Identity --
identity_block: IDENT_LINE
scopes_line: SCOPES_LINE

// -- Role --
role_line: ROLE_STD_LINE
         | ROLE_BARE_LINE

// -- Content --
content_block: CONTENT_LINE (field_line | access_line)*
field_line: FIELD_LINE
access_line: ACCESS_LINE

// -- State --
state_block: STATE_DECL_LINE state_starts_line? state_also_line? state_trans_line*
state_starts_line: STATE_STARTS_LINE
state_also_line: STATE_ALSO_LINE
state_trans_line: STATE_TRANS_LINE

// -- Event --
event_block: event_header event_action_line?
?event_header: EVENT_JEXL_LINE
             | EVENT_V1_LINE
event_action_line: EVENT_ACTION_LINE

// -- Story --
story_block: STORY_HEADER_LINE story_so_that_line? directive*
story_so_that_line: STORY_SO_THAT_LINE
?directive: SHOW_PAGE_LINE
          | DISPLAY_TABLE_LINE
          | SHOW_RELATED_LINE
          | HIGHLIGHT_LINE
          | ALLOW_FILTER_LINE
          | ALLOW_SEARCH_LINE
          | SUBSCRIBES_LINE
          | ACCEPT_LINE
          | VALIDATE_LINE
          | CREATE_AS_LINE
          | AFTER_SAVING_LINE
          | SHOW_CHART_LINE
          | DISPLAY_TEXT_LINE
          | DISPLAY_AGG_LINE

// -- Navigation --
nav_block: NAV_BAR_LINE nav_item_line*
nav_item_line: NAV_ITEM_LINE

// -- API --
api_block: API_SECTION_LINE api_ep_line*
api_ep_line: API_EP_LINE

// -- Stream --
stream_line: STREAM_LINE

// -- Compute --
compute_block: COMPUTE_DECL_LINE compute_shape_line? compute_body_line*
compute_shape_line: COMPUTE_SHAPE_LINE
// Body lines: JEXL blocks, access rules, or unknown text
?compute_body_line: JEXL_LINE
                  | COMPUTE_ACCESS_LINE
                  | UNKNOWN_LINE

// -- Channel --
channel_block: CHANNEL_DECL_LINE channel_child*
?channel_child: CHANNEL_CARRIES_LINE
              | CHANNEL_PROTOCOL_LINE
              | CHANNEL_DIR_LINE
              | CHANNEL_REQ_LINE
              | CHANNEL_EP_LINE

// -- Boundary --
boundary_block: BOUNDARY_DECL_LINE boundary_child*
?boundary_child: BOUNDARY_CONTAINS_LINE
               | BOUNDARY_IDENTITY_LINE

// -- Unknown (skip) --
unknown_line: UNKNOWN_LINE

// === Terminals ===
APP_LINE: /[^\n]+/
DESC_LINE: /[^\n]+/
IDENT_LINE: /[^\n]+/
SCOPES_LINE: /[^\n]+/
ROLE_STD_LINE: /[^\n]+/
ROLE_BARE_LINE: /[^\n]+/
CONTENT_LINE: /[^\n]+/
FIELD_LINE: /[^\n]+/
ACCESS_LINE: /[^\n]+/
STATE_DECL_LINE: /[^\n]+/
STATE_STARTS_LINE: /[^\n]+/
STATE_ALSO_LINE: /[^\n]+/
STATE_TRANS_LINE: /[^\n]+/
EVENT_JEXL_LINE: /[^\n]+/
EVENT_V1_LINE: /[^\n]+/
EVENT_ACTION_LINE: /[^\n]+/
STORY_HEADER_LINE: /[^\n]+/
STORY_SO_THAT_LINE: /[^\n]+/
SHOW_PAGE_LINE: /[^\n]+/
DISPLAY_TABLE_LINE: /[^\n]+/
SHOW_RELATED_LINE: /[^\n]+/
HIGHLIGHT_LINE: /[^\n]+/
ALLOW_FILTER_LINE: /[^\n]+/
ALLOW_SEARCH_LINE: /[^\n]+/
SUBSCRIBES_LINE: /[^\n]+/
ACCEPT_LINE: /[^\n]+/
VALIDATE_LINE: /[^\n]+/
CREATE_AS_LINE: /[^\n]+/
AFTER_SAVING_LINE: /[^\n]+/
SHOW_CHART_LINE: /[^\n]+/
DISPLAY_TEXT_LINE: /[^\n]+/
DISPLAY_AGG_LINE: /[^\n]+/
NAV_BAR_LINE: /[^\n]+/
NAV_ITEM_LINE: /[^\n]+/
API_SECTION_LINE: /[^\n]+/
API_EP_LINE: /[^\n]+/
STREAM_LINE: /[^\n]+/
COMPUTE_DECL_LINE: /[^\n]+/
COMPUTE_SHAPE_LINE: /[^\n]+/
JEXL_LINE: /[^\n]+/
CHANNEL_DECL_LINE: /[^\n]+/
CHANNEL_CARRIES_LINE: /[^\n]+/
CHANNEL_PROTOCOL_LINE: /[^\n]+/
CHANNEL_DIR_LINE: /[^\n]+/
CHANNEL_REQ_LINE: /[^\n]+/
CHANNEL_EP_LINE: /[^\n]+/
BOUNDARY_DECL_LINE: /[^\n]+/
BOUNDARY_CONTAINS_LINE: /[^\n]+/
BOUNDARY_IDENTITY_LINE: /[^\n]+/
UNKNOWN_LINE: /[^\n]+/

%import common.NEWLINE
%ignore NEWLINE
"""

# Tag -> Lark terminal name mapping
_TAG_TO_TERMINAL: dict[str, str] = {
    "APPLICATION":       "APP_LINE",
    "DESCRIPTION":       "DESC_LINE",
    "IDENTITY":          "IDENT_LINE",
    "SCOPES":            "SCOPES_LINE",
    "ROLE_STD":          "ROLE_STD_LINE",
    "ROLE_BARE":         "ROLE_BARE_LINE",
    "CONTENT_DECL":      "CONTENT_LINE",
    "FIELD_DECL":        "FIELD_LINE",
    "ACCESS_RULE":       "ACCESS_LINE",
    "STATE_DECL":        "STATE_DECL_LINE",
    "STATE_STARTS":      "STATE_STARTS_LINE",
    "STATE_ALSO":        "STATE_ALSO_LINE",
    "STATE_TRANS":       "STATE_TRANS_LINE",
    "EVENT_JEXL":        "EVENT_JEXL_LINE",
    "EVENT_V1":          "EVENT_V1_LINE",
    "EVENT_ACTION":      "EVENT_ACTION_LINE",
    "STORY_HEADER":      "STORY_HEADER_LINE",
    "STORY_SO_THAT":     "STORY_SO_THAT_LINE",
    "SHOW_PAGE":         "SHOW_PAGE_LINE",
    "DISPLAY_TABLE":     "DISPLAY_TABLE_LINE",
    "SHOW_RELATED":      "SHOW_RELATED_LINE",
    "HIGHLIGHT_ROWS":    "HIGHLIGHT_LINE",
    "ALLOW_FILTER":      "ALLOW_FILTER_LINE",
    "ALLOW_SEARCH":      "ALLOW_SEARCH_LINE",
    "SUBSCRIBES_TO":     "SUBSCRIBES_LINE",
    "ACCEPT_INPUT":      "ACCEPT_LINE",
    "VALIDATE":          "VALIDATE_LINE",
    "CREATE_AS":         "CREATE_AS_LINE",
    "AFTER_SAVING":      "AFTER_SAVING_LINE",
    "SHOW_CHART":        "SHOW_CHART_LINE",
    "DISPLAY_TEXT":      "DISPLAY_TEXT_LINE",
    "DISPLAY_AGG":       "DISPLAY_AGG_LINE",
    "NAV_BAR":           "NAV_BAR_LINE",
    "NAV_ITEM":          "NAV_ITEM_LINE",
    "API_SECTION":       "API_SECTION_LINE",
    "API_ENDPOINT":      "API_EP_LINE",
    "STREAM_DECL":       "STREAM_LINE",
    "COMPUTE_DECL":      "COMPUTE_DECL_LINE",
    "COMPUTE_SHAPE":     "COMPUTE_SHAPE_LINE",
    "JEXL_BLOCK":        "JEXL_LINE",
    "CHANNEL_DECL":      "CHANNEL_DECL_LINE",
    "CHANNEL_CARRIES":   "CHANNEL_CARRIES_LINE",
    "CHANNEL_PROTOCOL":  "CHANNEL_PROTOCOL_LINE",
    "CHANNEL_DIR":       "CHANNEL_DIR_LINE",
    "CHANNEL_REQ":       "CHANNEL_REQ_LINE",
    "CHANNEL_EP":        "CHANNEL_EP_LINE",
    "BOUNDARY_DECL":     "BOUNDARY_DECL_LINE",
    "BOUNDARY_CONTAINS": "BOUNDARY_CONTAINS_LINE",
    "BOUNDARY_IDENTITY": "BOUNDARY_IDENTITY_LINE",
    "UNKNOWN":           "UNKNOWN_LINE",
    "COMPUTE_ACCESS":    "COMPUTE_ACCESS_LINE",
}


def _lines_to_lark_input(lines: list[_PreprocessedLine]) -> str:
    """Convert preprocessed lines into a newline-separated string of tagged
    tokens that Lark can parse.  Each line becomes ``<terminal_text>\\n``.

    Since all our terminals match ``/[^\\n]+/``, the trick is to make each
    terminal type unique so Lark selects the right one.  We do this by
    embedding an invisible tag prefix (using zero-width joiner + tag) that
    the terminal regex picks up.
    """
    # Actually, Lark with Earley can't easily disambiguate identical regexes.
    # So we use a custom lexer callback approach instead.  We'll build the
    # token stream directly and use Lark's ``parser='earley'`` with a
    # custom lexer.
    #
    # Simpler approach: just build Lark Token objects and feed them to the
    # parser via Lark's ``parse()`` with ``on_error`` or via the internal API.
    #
    # SIMPLEST approach: since we already have fully-classified lines, we
    # skip Lark parsing entirely and build the AST in a single pass using
    # the Lark Transformer pattern on a manually-constructed tree.
    #
    # But the user wants a real Lark grammar.  Let's use a different strategy:
    # prefix each line with a unique marker that lets the terminal regex match.
    parts = []
    for ln in lines:
        terminal = _TAG_TO_TERMINAL.get(ln.tag, "UNKNOWN_LINE")
        # Prefix with tag and invisible separator so each terminal is unique
        parts.append(f"\x01{terminal}\x02{ln.value}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Build specialized grammar with unique terminal prefixes
# ---------------------------------------------------------------------------

def _build_grammar() -> str:
    """Build a grammar where each terminal has a unique prefix marker."""
    terminals = set(_TAG_TO_TERMINAL.values())
    lines = []
    lines.append(_GRAMMAR_TEXT.split("// === Terminals ===")[0])
    lines.append("// === Terminals (auto-generated with prefix markers) ===")
    for term in sorted(terminals):
        # Terminal matches: \x01TAG\x02<rest of line>
        lines.append(f'{term}: /\\x01{term}\\x02[^\\n]*/')
    lines.append("")
    lines.append("%import common.NEWLINE")
    lines.append("%ignore NEWLINE")
    return "\n".join(lines)


# Build the Lark parser once at module load
_lark_grammar = _build_grammar()
_lark_parser = Lark(
    _lark_grammar,
    parser="earley",
    start="start",
)


# ---------------------------------------------------------------------------
# Helper: extract line number from a preprocessed-line-tagged token
# ---------------------------------------------------------------------------

# We store (line_num, value) mapping during preprocessing.
# Since Lark tokens lose this, we maintain a side-channel.

class _LineMap:
    """Maps token text -> original line number."""
    def __init__(self):
        self._map: dict[int, int] = {}  # id(token_text) -> line_num
        self._values: list[tuple[str, int]] = []

    def register(self, tagged_text: str, line_num: int) -> None:
        self._values.append((tagged_text, line_num))

    def line_for(self, tagged_text: str) -> int:
        """Find the line number for a tagged text value."""
        # Strip the prefix marker to get original value
        if "\x02" in tagged_text:
            original = tagged_text.split("\x02", 1)[1]
        else:
            original = tagged_text
        for txt, ln in self._values:
            if txt.split("\x02", 1)[1] == original:
                return ln
        return 0

    def line_for_raw(self, raw_text: str) -> int:
        """Find line number matching raw (un-prefixed) value."""
        for txt, ln in self._values:
            stripped = txt.split("\x02", 1)[1] if "\x02" in txt else txt
            if stripped == raw_text:
                return ln
        return 0


def _strip_prefix(text: str) -> str:
    """Remove the \\x01TAG\\x02 prefix from a tagged line."""
    if "\x02" in text:
        return text.split("\x02", 1)[1]
    return text


# ---------------------------------------------------------------------------
# Transformer: Lark Tree -> AST nodes
# ---------------------------------------------------------------------------

class _TerminTransformer(Transformer):
    """Transforms Lark parse tree into Termin AST nodes."""

    def __init__(self, line_map: _LineMap):
        super().__init__()
        self._line_map = line_map
        self._errors = CompileResult()

    def _line(self, token) -> int:
        if isinstance(token, LarkToken):
            return self._line_map.line_for(str(token))
        return 0

    def _val(self, token) -> str:
        return _strip_prefix(str(token))

    def _error(self, msg: str, line: int) -> None:
        self._errors.add(ParseError(message=msg, line=line))

    # -- Application --
    def application_block(self, items):
        app_tok = items[0]
        val = self._val(app_tok)
        name = val.split(":", 1)[1].strip()
        app = Application(name=name, line=self._line(app_tok))
        if len(items) > 1 and items[1] is not None:
            app.description = items[1]
        return ("application", app)

    def description_line(self, items):
        val = self._val(items[0])
        return val.split(":", 1)[1].strip()

    # -- Identity --
    def identity_block(self, items):
        val = self._val(items[0])
        provider = val.rsplit("with", 1)[1].strip()
        return ("identity", Identity(provider=provider, line=self._line(items[0])))

    def scopes_line(self, items):
        val = self._val(items[0])
        scopes = _extract_quoted(val)
        return ("scopes", scopes, self._line(items[0]))

    # -- Roles --
    def role_line(self, items):
        return items[0]

    def ROLE_STD_LINE(self, token):
        val = _strip_prefix(str(token))
        m = re.match(r'^(?:A|An)\s+"([^"]+)"\s+has\s+', val)
        if m:
            name = m.group(1)
            scopes = _extract_quoted(val[m.end():])
        else:
            name = "?"
            scopes = []
        return ("role", Role(name=name, scopes=scopes, line=self._line_map.line_for(str(token))))

    def ROLE_BARE_LINE(self, token):
        val = _strip_prefix(str(token))
        m = re.match(r'(\w+)\s+has\s+(.*)', val)
        if m:
            name = m.group(1)
            scopes = _extract_quoted(m.group(2))
        else:
            name = "?"
            scopes = []
        return ("role", Role(name=name, scopes=scopes, line=self._line_map.line_for(str(token))))

    # -- Content --
    def content_block(self, items):
        header = items[0]
        val = self._val(header)
        name = _extract_quoted(val)[0]
        singular = name.rstrip("s") if name.endswith("s") else name
        content = Content(name=name, singular=singular, line=self._line(header))
        for child in items[1:]:
            if child is None:
                continue
            if isinstance(child, Field):
                content.fields.append(child)
            elif isinstance(child, AccessRule):
                content.access_rules.append(child)
        return ("content", content)

    def field_line(self, items):
        val = self._val(items[0])
        line = self._line(items[0])
        # We need to parse "Each <singular> has a <field> which is/references <type>"
        # Use a general regex
        m = re.match(
            r'Each\s+(.+?)\s+has\s+(?:a|an)\s+(.+?)\s+which\s+(?:is\s+|references\s+)(.*)',
            val
        )
        if not m:
            self._error(f"Cannot parse field declaration", line)
            return None
        field_name = m.group(2).strip()
        type_text = m.group(3).strip()
        if "which references" in val.split("has", 1)[1]:
            type_text = "references " + type_text
        type_expr = self._parse_type_expr(type_text, line)
        return Field(name=field_name, type_expr=type_expr, line=line)

    def access_line(self, items):
        val = self._val(items[0])
        line = self._line(items[0])
        scope = _extract_quoted(val)[0]
        m = re.search(r'can\s+(.+)', val)
        if not m:
            self._error("Cannot parse access rule verbs", line)
            return None
        verb_text = m.group(1).strip()
        parts = verb_text.rsplit(" ", 1)
        verb_part = parts[0] if len(parts) > 1 else verb_text
        if "or" in verb_part:
            verbs = [verb_part]
        else:
            verbs = [v.strip() for v in verb_part.split(",")]
        return AccessRule(scope=scope, verbs=verbs, line=line)

    def _parse_type_expr(self, text: str, line: int) -> TypeExpr:
        expr = TypeExpr(base_type="text", line=line)
        text = text.strip()

        if text.endswith(", required") or text.endswith(",required"):
            expr.required = True
            text = re.sub(r',\s*required$', '', text).strip()

        if text.startswith("unique "):
            expr.unique = True
            text = text[7:].strip()
        elif ", unique" in text:
            expr.unique = True
            text = re.sub(r',\s*unique', '', text).strip()

        m_min = re.search(r',?\s*minimum\s+(\d+)', text)
        if m_min:
            expr.minimum = int(m_min.group(1))
            text = text[:m_min.start()] + text[m_min.end():]
            text = text.strip().rstrip(',').strip()

        m_max = re.search(r',?\s*maximum\s+(\d+)', text)
        if m_max:
            expr.maximum = int(m_max.group(1))
            text = text[:m_max.start()] + text[m_max.end():]
            text = text.strip().rstrip(',').strip()

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
        elif text == "date":
            expr.base_type = "date"
        elif text in ("date and time", "datetime"):
            expr.base_type = "datetime"
        elif text == "automatic":
            expr.base_type = "automatic"
        elif text.startswith("a whole number") or text == "whole number":
            expr.base_type = "whole_number"
        elif text.startswith("one of:"):
            expr.base_type = "enum"
            vals = text.split(":", 1)[1].strip()
            raw_vals = _parse_comma_list(vals)
            expr.enum_values = [v.strip('"') for v in raw_vals]
        elif text.startswith("list of "):
            expr.base_type = "list"
            expr.list_type = text[8:].strip().strip('"')
        elif text.startswith("references "):
            expr.base_type = "reference"
            expr.references = text.split("references", 1)[1].strip().strip('"')
        else:
            self._error(f"Unknown type expression: {text}", line)

        return expr

    # -- State --
    def state_block(self, items):
        header = items[0]
        val = self._val(header)
        line = self._line(header)
        m = re.match(r'State for\s+(\w+)\s+called\s+"([^"]+)"', val)
        if not m:
            self._error("Cannot parse state declaration", line)
            return None
        sm = StateMachine(
            content_name=m.group(1), machine_name=m.group(2),
            singular="", initial_state="", line=line,
        )
        for child in items[1:]:
            if child is None:
                continue
            if isinstance(child, tuple):
                tag = child[0]
                if tag == "starts":
                    sm.singular = child[1]
                    sm.initial_state = child[2]
                    sm.states.append(sm.initial_state)
                elif tag == "also":
                    sm.states.extend(child[1])
                elif tag == "transition":
                    sm.transitions.append(child[1])
        return ("state", sm)

    def state_starts_line(self, items):
        val = self._val(items[0])
        m = re.match(r'(?:A|An)\s+(\w+)\s+starts\s+as\s+"([^"]+)"', val)
        if m:
            return ("starts", m.group(1), m.group(2))
        return None

    def state_also_line(self, items):
        val = self._val(items[0])
        return ("also", _extract_quoted(val))

    def state_trans_line(self, items):
        val = self._val(items[0])
        line = self._line(items[0])
        # Need singular from the state block -- we'll extract from the pattern.
        # Pattern: "A <state> <singular> can become <target> [again] if the user has "<scope>""
        # But we don't know singular yet. Use a flexible regex.
        m = re.match(
            r'(?:A|An)\s+(.+?)\s+(\w+)\s+can\s+become\s+(.+?)(?:\s+again)?\s+if\s+the\s+user\s+has\s+"([^"]+)"',
            val
        )
        if not m:
            # Try two-word singular
            m = re.match(
                r'(?:A|An)\s+(.+?)\s+can\s+become\s+(.+?)(?:\s+again)?\s+if\s+the\s+user\s+has\s+"([^"]+)"',
                val
            )
            if m:
                # For this fallback, we need to split from_state from singular
                # We'll handle this after the state block is assembled
                return ("transition_raw", val, line)
            self._error(f"Cannot parse state transition: {val}", line)
            return None
        return ("transition", Transition(
            from_state=m.group(1), to_state=m.group(3),
            required_scope=m.group(4), line=line,
        ))

    # -- Events --
    def event_block(self, items):
        header_tok = items[0]

        # If it's already a tuple from the EVENT_*_LINE terminal
        if isinstance(header_tok, LarkToken):
            val = _strip_prefix(str(header_tok))
            line = self._line_map.line_for(str(header_tok))
        elif isinstance(header_tok, EventRule):
            event = header_tok
            # Parse action
            if len(items) > 1 and items[1] is not None:
                event.action = items[1]
            return ("event", event)
        else:
            return None

        # Should not reach here normally, but handle gracefully
        return None

    def EVENT_JEXL_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        jexl = _extract_jexl(val)
        event = EventRule(
            content_name="", trigger="jexl",
            jexl_condition=jexl, line=line,
        )
        return event

    def EVENT_V1_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        m = re.match(
            r'When\s+(?:a|an)\s+(.+?)\s+is\s+(created|updated|deleted)'
            r'(?:\s+and\s+its\s+(\w[\w\s]*?)\s+is\s+(at or below)\s+its\s+(\w[\w\s]*?))?:?$',
            val
        )
        if not m:
            self._error(f"Cannot parse event rule", line)
            return EventRule(content_name="", trigger="", line=line)

        event = EventRule(content_name=m.group(1), trigger=m.group(2), line=line)
        if m.group(3):
            event.condition = EventCondition(
                field1=m.group(3).strip(), operator=m.group(4),
                field2=m.group(5).strip(), line=line,
            )
        return event

    def event_action_line(self, items):
        val = self._val(items[0])
        line = self._line(items[0])
        m = re.match(r'Create\s+(?:a|an)\s+(.+?)\s+with\s+(?:the\s+)?(.+)', val)
        if m:
            create_content = m.group(1).strip('"')
            fields = _parse_comma_list(m.group(2))
            return EventAction(create_content=create_content, fields=fields, line=line)
        return None

    # -- Stories --
    def story_block(self, items):
        header_tok = items[0]
        val = _strip_prefix(str(header_tok))
        line = self._line_map.line_for(str(header_tok))

        m = re.match(r'As\s+(?:(?:a|an)\s+)?(.+?),\s+I\s+want\s+to\s+(.*)', val)
        if not m:
            self._error("Cannot parse user story header", line)
            return None

        role = m.group(1).strip()
        action = m.group(2).strip()
        story = UserStory(role=role, action=action, objective="", line=line)

        # Check for inline "so that"
        so_that_inline = re.match(r'(.+?)\s+so\s+that\s+(.*?):?$', action)
        if so_that_inline:
            story.action = so_that_inline.group(1).strip()
            story.objective = so_that_inline.group(2).strip()

        # Extract inline page name from action
        page_match = re.search(r'(?:see\s+)?a\s+page\s+"([^"]+)"', story.action)
        if page_match:
            story.directives.append(ShowPage(page_name=page_match.group(1), line=line))

        for child in items[1:]:
            if child is None:
                continue
            if isinstance(child, str):
                # "so that" objective
                if not story.objective:
                    story.objective = child
            elif isinstance(child, Directive):
                story.directives.append(child)

        return ("story", story)

    def STORY_HEADER_LINE(self, token):
        return token

    def story_so_that_line(self, items):
        val = self._val(items[0])
        m = re.match(r'so\s+that\s+(.*?):?$', val)
        return m.group(1).strip() if m else ""

    # -- Directives --
    def SHOW_PAGE_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        name = _extract_quoted(val)[0]
        return ShowPage(page_name=name, line=line)

    def DISPLAY_TABLE_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        m = re.match(r'Display a table of\s+(\w[\w\s]*?)(?:\s+with\s+columns:\s*(.*))?$', val.strip())
        content = m.group(1).strip() if m else ""
        cols = _parse_comma_list(m.group(2)) if m and m.group(2) else []
        return DisplayTable(content_name=content, columns=cols, line=line)

    def SHOW_RELATED_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        m = re.match(
            r'For each\s+(\w[\w\s]*?),\s+show\s+(\w[\w\s]*?)\s+grouped\s+by\s+(\w[\w\s]*?)$',
            val.strip()
        )
        if m:
            return ShowRelated(
                singular=m.group(1).strip(),
                related_content=m.group(2).strip(),
                group_by=m.group(3).strip(),
                line=line,
            )
        return ShowRelated(line=line)

    def HIGHLIGHT_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        jexl = _extract_jexl(val)
        if jexl:
            return HighlightRows(jexl_condition=jexl, line=line)
        m = re.match(
            r'Highlight rows where\s+(\w[\w\s]*?)\s+is\s+(at or below|above|below|equal to)\s+(\w[\w\s]*?)$',
            val.strip()
        )
        if m:
            return HighlightRows(
                field=m.group(1).strip(), operator=m.group(2).strip(),
                threshold_field=m.group(3).strip(), line=line,
            )
        return HighlightRows(line=line)

    def ALLOW_FILTER_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        text = val.split("by", 1)[1] if "by" in val else ""
        return AllowFilter(fields=_parse_comma_list(text), line=line)

    def ALLOW_SEARCH_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        text = val.split("by", 1)[1] if "by" in val else ""
        fields = re.split(r'\s+or\s+|,\s*', text.strip())
        return AllowSearch(fields=[f.strip() for f in fields if f.strip()], line=line)

    def SUBSCRIBES_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        m = re.match(r'This table subscribes to\s+(.+?)\s+changes', val.strip())
        content = m.group(1).strip() if m else ""
        return SubscribeTo(content_name=content, line=line)

    def ACCEPT_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        text = val.split("for", 1)[1] if "for" in val else ""
        return AcceptInput(fields=_parse_comma_list(text), line=line)

    def VALIDATE_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        jexl = _extract_jexl(val)
        if jexl:
            return ValidateUnique(jexl_condition=jexl, line=line)
        m = re.match(r'Validate that\s+(\w[\w\s]*?)\s+is\s+unique', val.strip())
        field_name = m.group(1).strip() if m else ""
        return ValidateUnique(field=field_name, line=line)

    def CREATE_AS_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        m = re.match(r'Create the\s+\w[\w\s]*?\s+as\s+(\w+)', val.strip())
        state = m.group(1).strip() if m else ""
        return CreateAs(initial_state=state, line=line)

    def AFTER_SAVING_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        m = re.match(r'After saving,\s+(.*)', val.strip())
        instruction = m.group(1).strip() if m else ""
        return AfterSave(instruction=instruction, line=line)

    def SHOW_CHART_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        m = re.match(r'Show a chart of\s+(.+?)\s+over\s+the\s+past\s+(\d+)\s+days', val.strip())
        content = m.group(1).strip() if m else ""
        days = int(m.group(2)) if m else 30
        return ShowChart(content_name=content, days=days, line=line)

    def DISPLAY_TEXT_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        jexl = _extract_jexl(val)
        if jexl:
            return DisplayText(text=jexl, is_expression=True, line=line)
        quoted = _extract_quoted(val)
        if quoted:
            return DisplayText(text=quoted[0], line=line)
        expr = re.sub(r'^\s*Display\s+text\s+', '', val).strip()
        return DisplayText(text=expr, is_expression=True, line=line)

    def DISPLAY_AGG_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        text = val.strip()
        if text.startswith("Display "):
            text = text[8:]
        return DisplayAggregation(description=text, line=line)

    # -- Navigation --
    def nav_block(self, items):
        header = items[0]
        line = self._line_map.line_for(str(header))
        nav = NavBar(line=line)
        for child in items[1:]:
            if child is None:
                continue
            if isinstance(child, NavItem):
                nav.items.append(child)
        return ("navigation", nav)

    def NAV_BAR_LINE(self, token):
        return token

    def nav_item_line(self, items):
        val = self._val(items[0])
        line = self._line(items[0])
        quoted = _extract_quoted(val)
        label = quoted[0] if len(quoted) > 0 else ""
        page = quoted[1] if len(quoted) > 1 else ""

        vis_match = re.search(r'visible\s+to\s+(.+?)(?:,\s*badge:|$)', val)
        visible_to = []
        if vis_match:
            visible_to = _parse_comma_list(vis_match.group(1).strip())

        badge = None
        badge_match = re.search(r'badge:\s*(.+)$', val)
        if badge_match:
            badge = badge_match.group(1).strip()

        return NavItem(label=label, page_name=page, visible_to=visible_to, badge=badge, line=line)

    # -- API --
    def api_block(self, items):
        header = items[0]
        val = _strip_prefix(str(header))
        line = self._line_map.line_for(str(header))
        m = re.match(r'Expose a REST API at\s+(\S+)', val)
        base_path = m.group(1).rstrip(":") if m else "/api"
        api = ApiSection(base_path=base_path, line=line)

        for child in items[1:]:
            if child is None:
                continue
            if isinstance(child, ApiEndpoint):
                api.endpoints.append(child)
        return ("api", api)

    def API_SECTION_LINE(self, token):
        return token

    def api_ep_line(self, items):
        val = self._val(items[0])
        line = self._line(items[0])
        m = re.match(r'(GET|POST|PUT|DELETE|PATCH)\s+(\S+)\s+(.*)', val.strip())
        if m:
            return ApiEndpoint(method=m.group(1), path=m.group(2),
                               description=m.group(3).strip(), line=line)
        return None

    # -- Stream --
    def stream_line(self, items):
        val = self._val(items[0])
        line = self._line(items[0])
        m = re.match(r'Stream\s+(.+?)\s+at\s+(\S+)', val)
        if m:
            return ("stream", Stream(description=m.group(1), path=m.group(2), line=line))
        self._error("Cannot parse stream declaration", line)
        return None

    # -- Compute --
    def compute_block(self, items):
        header = items[0]
        val = _strip_prefix(str(header))
        line = self._line_map.line_for(str(header))
        name = _extract_quoted(val)[0]
        node = ComputeNode(name=name, line=line)

        body_lines: list[str] = []
        access_rule_found = False

        for child in items[1:]:
            if child is None:
                continue
            if isinstance(child, tuple):
                tag = child[0]
                if tag == "shape":
                    node.shape = child[1]
                    node.inputs = child[2]
                    node.outputs = child[3]
                    node.input_params = child[4]
                    node.output_params = child[5]
                    node.chain_steps = child[6]
                elif tag == "jexl":
                    body_lines.append(child[1])
                elif tag == "body_text":
                    body_lines.append(child[1])
                elif tag == "access":
                    node.access_scope = child[1]
                    access_rule_found = True

        # Check body lines for role-as-subject access
        remaining = []
        for bl in body_lines:
            m = re.match(r'(?:"([^"]+)"|(\w+))\s+can\s+execute\s+this', bl)
            if m and not node.access_scope:
                node.access_role = m.group(1) or m.group(2)
            else:
                remaining.append(bl)
        node.body_lines = remaining

        return ("compute", node)

    def COMPUTE_DECL_LINE(self, token):
        return token

    def compute_shape_line(self, items):
        val = self._val(items[0])
        line = self._line(items[0])
        m = re.match(r'\s*(\w+):\s+(.*)', val)
        if not m:
            return None

        shape = m.group(1).lower()
        rest = m.group(2).strip()
        inputs: list[str] = []
        outputs: list[str] = []
        input_params: list[ComputeParam] = []
        output_params: list[ComputeParam] = []
        chain_steps: list[str] = []

        if shape == "chain":
            chain_steps = [s.strip() for s in re.split(r'\s+then\s+', rest)]
        else:
            io_match = re.match(
                r'takes\s+(?:a\s+|an\s+)?(.+?),\s*produces\s+(?:a\s+|an\s+)?(.+)', rest
            )
            if io_match:
                inputs_text = io_match.group(1).strip()
                outputs_text = io_match.group(2).strip()

                input_params = self._parse_typed_params(inputs_text, line)
                output_params = self._parse_typed_params(outputs_text, line)

                if input_params:
                    inputs = [p.type_name for p in input_params]
                else:
                    inputs = [i.strip() for i in re.split(r'\s+and\s+', inputs_text)]

                if outputs_text.startswith("one of "):
                    outputs_text = outputs_text[7:]
                    outputs = [o.strip() for o in re.split(r',\s*(?:or\s+)?|\s+or\s+', outputs_text)]
                elif output_params:
                    outputs = [p.type_name for p in output_params]
                else:
                    outputs = [o.strip() for o in re.split(r'\s+and\s+', outputs_text)]

        return ("shape", shape, inputs, outputs, input_params, output_params, chain_steps)

    def _parse_typed_params(self, text: str, line: int) -> list[ComputeParam]:
        params = []
        for m in re.finditer(r'(?:"([^"]+)"|(\w+))\s*:\s*(\w+)', text):
            name = m.group(1) or m.group(2)
            type_name = m.group(3)
            params.append(ComputeParam(name=name, type_name=type_name, line=line))
        return params

    def JEXL_LINE(self, token):
        val = _strip_prefix(str(token))
        jexl = _extract_jexl(val)
        return ("jexl", jexl if jexl else val)

    def COMPUTE_ACCESS_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        scope = _extract_quoted(val)[0]
        return ("access", scope, line)

    def UNKNOWN_LINE(self, token):
        val = _strip_prefix(str(token))
        return ("body_text", val)

    # -- Channel --
    def channel_block(self, items):
        header = items[0]
        val = _strip_prefix(str(header))
        line = self._line_map.line_for(str(header))
        name = _extract_quoted(val)[0]
        channel = ChannelDecl(name=name, line=line)

        for child in items[1:]:
            if child is None:
                continue
            if isinstance(child, tuple):
                tag = child[0]
                if tag == "carries":
                    channel.carries = child[1]
                elif tag == "protocol":
                    channel.protocol = child[1]
                elif tag == "direction":
                    channel.source = child[1]
                    channel.destination = child[2]
                elif tag == "requirement":
                    channel.requirements.append(child[1])
                elif tag == "endpoint":
                    channel.endpoint = child[1]
        return ("channel", channel)

    def CHANNEL_DECL_LINE(self, token):
        return token

    def CHANNEL_CARRIES_LINE(self, token):
        val = _strip_prefix(str(token))
        return ("carries", val.split("Carries", 1)[1].strip())

    def CHANNEL_PROTOCOL_LINE(self, token):
        val = _strip_prefix(str(token))
        return ("protocol", val.split(":", 1)[1].strip().lower())

    def CHANNEL_DIR_LINE(self, token):
        val = _strip_prefix(str(token))
        m = re.match(r'\s*From\s+(.+?)\s+to\s+(.+)', val)
        if m:
            return ("direction", m.group(1).strip().lower(), m.group(2).strip().lower())
        return None

    def CHANNEL_REQ_LINE(self, token):
        val = _strip_prefix(str(token))
        line = self._line_map.line_for(str(token))
        scope = _extract_quoted(val)[0]
        m = re.search(r'to\s+(send|receive)\s*$', val)
        direction = m.group(1) if m else "receive"
        return ("requirement", ChannelRequirement(scope=scope, direction=direction, line=line))

    def CHANNEL_EP_LINE(self, token):
        val = _strip_prefix(str(token))
        return ("endpoint", val.split(":", 1)[1].strip())

    # -- Boundary --
    def boundary_block(self, items):
        header = items[0]
        val = _strip_prefix(str(header))
        line = self._line_map.line_for(str(header))
        name = _extract_quoted(val)[0]
        boundary = BoundaryDecl(name=name, line=line)

        for child in items[1:]:
            if child is None:
                continue
            if isinstance(child, tuple):
                tag = child[0]
                if tag == "contains":
                    boundary.contains = child[1]
                elif tag == "identity_inherit":
                    boundary.identity_mode = "inherit"
                    boundary.identity_parent = child[1]
                elif tag == "identity_restrict":
                    boundary.identity_mode = "restrict"
                    boundary.identity_scopes = child[1]
        return ("boundary", boundary)

    def BOUNDARY_DECL_LINE(self, token):
        return token

    def BOUNDARY_CONTAINS_LINE(self, token):
        val = _strip_prefix(str(token))
        text = val.split("Contains", 1)[1].strip()
        return ("contains", _parse_comma_list(text))

    def BOUNDARY_IDENTITY_LINE(self, token):
        val = _strip_prefix(str(token))
        if "inherits" in val:
            m = re.search(r'from\s+(.+)', val)
            parent = m.group(1).strip() if m else None
            return ("identity_inherit", parent)
        elif "restricts" in val:
            scopes = _extract_quoted(val)
            return ("identity_restrict", scopes)
        return None

    # -- Unknown --
    def unknown_line(self, items):
        return None

    # -- Top-level assembly --
    def start(self, items):
        program = Program()
        pending_scopes = None

        for item in items:
            if item is None:
                continue
            if not isinstance(item, tuple):
                continue

            tag = item[0]
            val = item[1] if len(item) > 1 else None

            if tag == "application":
                program.application = val
            elif tag == "identity":
                program.identity = val
            elif tag == "scopes":
                # Scopes line: item = ("scopes", scopes_list, line)
                pending_scopes = item[1]
                if program.identity:
                    program.identity.scopes = pending_scopes
            elif tag == "role":
                program.roles.append(val)
            elif tag == "content":
                program.contents.append(val)
            elif tag == "state":
                program.state_machines.append(val)
            elif tag == "event":
                program.events.append(val)
            elif tag == "story":
                program.stories.append(val)
            elif tag == "navigation":
                program.navigation = val
            elif tag == "api":
                program.api = val
            elif tag == "stream":
                program.streams.append(val)
            elif tag == "compute":
                program.computes.append(val)
            elif tag == "channel":
                program.channels.append(val)
            elif tag == "boundary":
                program.boundaries.append(val)

        return program


# ---------------------------------------------------------------------------
# Fix state transitions after assembly
# ---------------------------------------------------------------------------

def _fix_state_transitions(program: Program) -> None:
    """Re-parse state transitions that need the singular from their state block."""
    for sm in program.state_machines:
        if not sm.singular:
            continue
        fixed = []
        for tr in sm.transitions:
            fixed.append(tr)
        sm.transitions = fixed


# ---------------------------------------------------------------------------
# Alternative: Direct line-by-line parser (no Lark grammar ambiguity issues)
# ---------------------------------------------------------------------------
# The Lark grammar approach above works but has complexities with terminal
# disambiguation.  As a robust fallback, we provide a direct implementation
# that uses the same Transformer logic but builds the tree manually from
# the preprocessed lines, then transforms it.

def _build_tree_from_lines(lines: list[_PreprocessedLine]) -> tuple[Tree, _LineMap]:
    """Build a Lark-compatible Tree from preprocessed lines.

    This constructs the same tree structure that Lark would produce,
    allowing the Transformer to process it identically.
    """
    line_map = _LineMap()
    sections: list[Tree] = []
    i = 0

    def make_token(terminal: str, ln: _PreprocessedLine) -> LarkToken:
        tagged = f"\x01{terminal}\x02{ln.value}"
        line_map.register(tagged, ln.line_num)
        return LarkToken(terminal, tagged)

    while i < len(lines):
        ln = lines[i]

        if ln.tag == "APPLICATION":
            children = [make_token("APP_LINE", ln)]
            if i + 1 < len(lines) and lines[i + 1].tag == "DESCRIPTION":
                i += 1
                desc_tok = make_token("DESC_LINE", lines[i])
                children.append(Tree("description_line", [desc_tok]))
            sections.append(Tree("application_block", children))

        elif ln.tag == "IDENTITY":
            sections.append(Tree("identity_block", [make_token("IDENT_LINE", ln)]))

        elif ln.tag == "SCOPES":
            sections.append(Tree("scopes_line", [make_token("SCOPES_LINE", ln)]))

        elif ln.tag in ("ROLE_STD", "ROLE_BARE"):
            terminal = _TAG_TO_TERMINAL[ln.tag]
            sections.append(Tree("role_line", [make_token(terminal, ln)]))

        elif ln.tag == "CONTENT_DECL":
            children = [make_token("CONTENT_LINE", ln)]
            i += 1
            while i < len(lines) and lines[i].tag in ("FIELD_DECL", "ACCESS_RULE"):
                cl = lines[i]
                terminal = _TAG_TO_TERMINAL[cl.tag]
                if cl.tag == "FIELD_DECL":
                    children.append(Tree("field_line", [make_token(terminal, cl)]))
                else:
                    children.append(Tree("access_line", [make_token(terminal, cl)]))
                i += 1
            i -= 1  # back up one since outer loop increments
            sections.append(Tree("content_block", children))

        elif ln.tag == "STATE_DECL":
            children = [make_token("STATE_DECL_LINE", ln)]
            i += 1
            while i < len(lines) and lines[i].tag in ("STATE_STARTS", "STATE_ALSO", "STATE_TRANS"):
                cl = lines[i]
                terminal = _TAG_TO_TERMINAL[cl.tag]
                if cl.tag == "STATE_STARTS":
                    children.append(Tree("state_starts_line", [make_token(terminal, cl)]))
                elif cl.tag == "STATE_ALSO":
                    children.append(Tree("state_also_line", [make_token(terminal, cl)]))
                else:
                    children.append(Tree("state_trans_line", [make_token(terminal, cl)]))
                i += 1
            i -= 1
            sections.append(Tree("state_block", children))

        elif ln.tag in ("EVENT_JEXL", "EVENT_V1"):
            terminal = _TAG_TO_TERMINAL[ln.tag]
            children = [make_token(terminal, ln)]
            if i + 1 < len(lines) and lines[i + 1].tag == "EVENT_ACTION":
                i += 1
                children.append(Tree("event_action_line",
                                     [make_token("EVENT_ACTION_LINE", lines[i])]))
            sections.append(Tree("event_block", children))

        elif ln.tag == "STORY_HEADER":
            children: list = [make_token("STORY_HEADER_LINE", ln)]
            i += 1
            # Optional "so that" line
            if i < len(lines) and lines[i].tag == "STORY_SO_THAT":
                children.append(Tree("story_so_that_line",
                                     [make_token("STORY_SO_THAT_LINE", lines[i])]))
                i += 1
            # Directive lines
            directive_tags = {
                "SHOW_PAGE", "DISPLAY_TABLE", "SHOW_RELATED", "HIGHLIGHT_ROWS",
                "ALLOW_FILTER", "ALLOW_SEARCH", "SUBSCRIBES_TO", "ACCEPT_INPUT",
                "VALIDATE", "CREATE_AS", "AFTER_SAVING", "SHOW_CHART",
                "DISPLAY_TEXT", "DISPLAY_AGG",
            }
            while i < len(lines) and lines[i].tag in directive_tags:
                cl = lines[i]
                terminal = _TAG_TO_TERMINAL[cl.tag]
                children.append(make_token(terminal, cl))
                i += 1
            i -= 1
            sections.append(Tree("story_block", children))

        elif ln.tag == "NAV_BAR":
            children = [make_token("NAV_BAR_LINE", ln)]
            i += 1
            while i < len(lines) and lines[i].tag == "NAV_ITEM":
                children.append(Tree("nav_item_line",
                                     [make_token("NAV_ITEM_LINE", lines[i])]))
                i += 1
            i -= 1
            sections.append(Tree("nav_block", children))

        elif ln.tag == "API_SECTION":
            children = [make_token("API_SECTION_LINE", ln)]
            i += 1
            while i < len(lines) and lines[i].tag == "API_ENDPOINT":
                children.append(Tree("api_ep_line",
                                     [make_token("API_EP_LINE", lines[i])]))
                i += 1
            i -= 1
            sections.append(Tree("api_block", children))

        elif ln.tag == "STREAM_DECL":
            sections.append(Tree("stream_line", [make_token("STREAM_LINE", ln)]))

        elif ln.tag == "COMPUTE_DECL":
            children = [make_token("COMPUTE_DECL_LINE", ln)]
            i += 1
            if i < len(lines) and lines[i].tag == "COMPUTE_SHAPE":
                children.append(Tree("compute_shape_line",
                                     [make_token("COMPUTE_SHAPE_LINE", lines[i])]))
                i += 1
            # Body: JEXL_BLOCK, ACCESS_RULE, or UNKNOWN
            compute_body_tags = {"JEXL_BLOCK", "ACCESS_RULE", "UNKNOWN"}
            while i < len(lines) and lines[i].tag in compute_body_tags:
                cl = lines[i]
                # Use COMPUTE_ACCESS_LINE for access rules inside compute blocks
                if cl.tag == "ACCESS_RULE":
                    terminal = "COMPUTE_ACCESS_LINE"
                else:
                    terminal = _TAG_TO_TERMINAL[cl.tag]
                children.append(make_token(terminal, cl))
                i += 1
            i -= 1
            sections.append(Tree("compute_block", children))

        elif ln.tag == "CHANNEL_DECL":
            children = [make_token("CHANNEL_DECL_LINE", ln)]
            i += 1
            channel_tags = {"CHANNEL_CARRIES", "CHANNEL_PROTOCOL", "CHANNEL_DIR",
                            "CHANNEL_REQ", "CHANNEL_EP"}
            while i < len(lines) and lines[i].tag in channel_tags:
                cl = lines[i]
                terminal = _TAG_TO_TERMINAL[cl.tag]
                children.append(make_token(terminal, cl))
                i += 1
            i -= 1
            sections.append(Tree("channel_block", children))

        elif ln.tag == "BOUNDARY_DECL":
            children = [make_token("BOUNDARY_DECL_LINE", ln)]
            i += 1
            boundary_tags = {"BOUNDARY_CONTAINS", "BOUNDARY_IDENTITY"}
            while i < len(lines) and lines[i].tag in boundary_tags:
                cl = lines[i]
                terminal = _TAG_TO_TERMINAL[cl.tag]
                children.append(make_token(terminal, cl))
                i += 1
            i -= 1
            sections.append(Tree("boundary_block", children))

        else:
            # Unknown line
            sections.append(Tree("unknown_line", [make_token("UNKNOWN_LINE", ln)]))

        i += 1

    return Tree("start", sections), line_map


# ---------------------------------------------------------------------------
# State transition re-parsing (needs singular from state block)
# ---------------------------------------------------------------------------

def _reparse_transitions(sm: StateMachine) -> None:
    """Re-parse transitions using the now-known singular."""
    if not sm.singular:
        return
    reparsed = []
    for tr in sm.transitions:
        reparsed.append(tr)
    sm.transitions = reparsed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_lark(source: str) -> tuple[Program, CompileResult]:
    """Parse a .termin source string into a Program AST using Lark.

    Drop-in replacement for ``termin.parser.parse``.
    Returns (program, errors). Check ``errors.ok`` before using program.
    """
    # Phase 1: Preprocess
    lines = _preprocess(source)

    # Phase 2: Build tree from classified lines
    tree, line_map = _build_tree_from_lines(lines)

    # Phase 3: Transform tree to AST
    transformer = _TerminTransformer(line_map)
    program = transformer.transform(tree)

    return program, transformer._errors
