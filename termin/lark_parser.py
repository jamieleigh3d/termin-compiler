"""Lark-based parser for the Termin DSL.

Loads grammar.lark as the AUTHORITATIVE grammar and uses Lark's Earley
parser to produce the same AST nodes as the hand-rolled parser.

Public API
----------
    parse_lark(source: str) -> tuple[Program, CompileResult]

This is a drop-in replacement for ``termin.parser.parse``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from lark import Lark, Transformer, Tree, Token as LarkToken

from .ast_nodes import (
    Program, Application, Identity, Role, Content, Field, TypeExpr,
    AccessRule, StateMachine, Transition, EventRule, EventCondition,
    EventAction, UserStory, ShowPage, DisplayTable, ShowRelated,
    HighlightRows, AllowFilter, AllowSearch, SubscribeTo, AcceptInput,
    ValidateUnique, CreateAs, AfterSave, ShowChart, DisplayAggregation,
    NavBar, NavItem, ApiSection, ApiEndpoint, Stream, Directive,
    ComputeNode, ComputeParam, ChannelDecl, ChannelRequirement,
    BoundaryDecl, BoundaryProperty, DisplayText, RoleAlias,
    ErrorHandler, ErrorAction,
)
from .errors import ParseError, CompileResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_quoted(text: str) -> list[str]:
    return re.findall(r'"([^"]*)"', text)

def _extract_jexl(text: str) -> Optional[str]:
    m = re.search(r'\[([^\]]+)\]', text)
    return m.group(1).strip() if m else None

def _parse_comma_list(text: str) -> list[str]:
    text = text.strip().rstrip(":")
    parts = re.split(r',\s*(?:and\s+)?|\s+and\s+', text)
    return [p.strip() for p in parts if p.strip()]

def _strip_quotes(s: str) -> str:
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s

def _tok_str(tok) -> str:
    if isinstance(tok, LarkToken):
        return str(tok).strip()
    if isinstance(tok, Tree):
        return " ".join(_tok_str(c) for c in tok.children).strip()
    return str(tok).strip()

# ---------------------------------------------------------------------------
# Load grammar from file (SINGLE SOURCE OF TRUTH)
# ---------------------------------------------------------------------------

_GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"
_grammar_text = _GRAMMAR_PATH.read_text(encoding="utf-8")
_parser = Lark(_grammar_text, parser="earley", start="start", ambiguity="resolve")

# ---------------------------------------------------------------------------
# Preprocessor
# ---------------------------------------------------------------------------

def _preprocess(source: str) -> str:
    lines = []
    for raw in source.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("---"):
            continue
        if stripped.startswith("(") and stripped.endswith(")"):
            continue
        cleaned = re.sub(r'\s+\([^)]*\)\s*$', '', stripped).strip()
        lines.append(cleaned)
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Type expression parser
# ---------------------------------------------------------------------------

def _parse_type_expr(text: str) -> TypeExpr:
    expr = TypeExpr(base_type="text")
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

    if text == "text": expr.base_type = "text"
    elif text == "currency": expr.base_type = "currency"
    elif text == "number": expr.base_type = "number"
    elif text == "percentage": expr.base_type = "percentage"
    elif text in ("true/false", "boolean"): expr.base_type = "boolean"
    elif text == "date": expr.base_type = "date"
    elif text in ("date and time", "datetime"): expr.base_type = "datetime"
    elif text == "automatic": expr.base_type = "automatic"
    elif text.startswith("a whole number") or text == "whole number": expr.base_type = "whole_number"
    elif text.startswith("one of:"):
        expr.base_type = "enum"
        vals = text.split(":", 1)[1].strip()
        expr.enum_values = [v.strip('"') for v in _parse_comma_list(vals)]
    elif text.startswith("list of "):
        expr.base_type = "list"
        expr.list_type = text[8:].strip().strip('"')
    elif text.startswith("references "):
        expr.base_type = "reference"
        expr.references = text.split("references", 1)[1].strip().strip('"')
    return expr

# ---------------------------------------------------------------------------
# Transformer: grammar.lark parse tree -> AST nodes
# ---------------------------------------------------------------------------

class _TerminTransformer(Transformer):

    def start(self, items):
        prog = Program()
        for item in items:
            if item is None: continue
            if isinstance(item, Application): prog.application = item
            elif isinstance(item, Identity): prog.identity = item
            elif isinstance(item, Role): prog.roles.append(item)
            elif isinstance(item, RoleAlias): prog.role_aliases.append(item)
            elif isinstance(item, Content): prog.contents.append(item)
            elif isinstance(item, StateMachine): prog.state_machines.append(item)
            elif isinstance(item, EventRule): prog.events.append(item)
            elif isinstance(item, UserStory): prog.stories.append(item)
            elif isinstance(item, NavBar): prog.navigation = item
            elif isinstance(item, ApiSection): prog.api = item
            elif isinstance(item, Stream): prog.streams.append(item)
            elif isinstance(item, ComputeNode): prog.computes.append(item)
            elif isinstance(item, ChannelDecl): prog.channels.append(item)
            elif isinstance(item, BoundaryDecl): prog.boundaries.append(item)
            elif isinstance(item, ErrorHandler): prog.error_handlers.append(item)
            elif isinstance(item, tuple) and item[0] == "scopes":
                if prog.identity: prog.identity.scopes = item[1]
                else: prog.identity = Identity(provider="stub", scopes=item[1])
        return prog

    def _filter_newlines(self, items):
        """Filter out NEWLINE tokens from items."""
        return [i for i in items if not (isinstance(i, LarkToken) and i.type == "NEWLINE")]

    # -- Application --
    def application_block(self, items):
        items = self._filter_newlines(items)
        app = items[0]
        if len(items) > 1 and isinstance(items[1], str):
            app.description = items[1]
        return app
    def application(self, items):
        return Application(name=_tok_str(items[0]).strip())
    def description(self, items):
        return _tok_str(items[0]).strip()

    # -- Identity --
    def identity_line(self, items):
        return Identity(provider=_tok_str(items[0]).strip())
    def scopes_line(self, items):
        return ("scopes", _extract_quoted(_tok_str(items[0])))

    # -- Roles --
    def role_standard(self, items):
        name = _strip_quotes(_tok_str(items[1]))
        rest = _tok_str(items[2]) if len(items) > 2 else ""
        return Role(name=name, scopes=_extract_quoted(rest))
    def role_bare(self, items):
        name = _tok_str(items[0])
        rest = _tok_str(items[1]) if len(items) > 1 else ""
        return Role(name=name, scopes=_extract_quoted(rest))
    def role_alias(self, items):
        return RoleAlias(short_name=_strip_quotes(_tok_str(items[0])),
                         full_name=_strip_quotes(_tok_str(items[1])))

    # -- Content --
    def content_block(self, items):
        items = self._filter_newlines(items)
        content = items[0]
        for item in items[1:]:
            if isinstance(item, Field): content.fields.append(item)
            elif isinstance(item, AccessRule): content.access_rules.append(item)
        return content
    def content_decl(self, items):
        name = _strip_quotes(_tok_str(items[0]))
        singular = name.rstrip("s") if name.endswith("s") else name
        return Content(name=name, singular=singular)
    def field_decl(self, items):
        text = _tok_str(items[0])
        m = re.match(r'(.+?)\s+has\s+(?:a|an)\s+(.+?)\s+which\s+(?:is\s+|references\s+)(.*)', text)
        if not m:
            return Field(name="unknown", type_expr=TypeExpr(base_type="text"))
        field_name = m.group(2).strip()
        type_text = m.group(3).strip()
        if "which references" in text.split("has", 1)[1]:
            type_text = "references " + type_text
        return Field(name=field_name, type_expr=_parse_type_expr(type_text))
    def access_rule(self, items):
        scope = _strip_quotes(_tok_str(items[0]))
        rest = _tok_str(items[1]) if len(items) > 1 else ""
        parts = rest.rsplit(" ", 1)
        verb_part = parts[0] if len(parts) > 1 else rest
        verbs = [verb_part] if "or" in verb_part else [v.strip() for v in verb_part.split(",")]
        return AccessRule(scope=scope, verbs=verbs)

    # -- State --
    def state_block(self, items):
        items = self._filter_newlines(items)
        sm = items[0]
        for item in items[1:]:
            if isinstance(item, tuple):
                kind, data = item
                if kind == "starts":
                    sm.singular, sm.initial_state = data
                    sm.states.append(sm.initial_state)
                elif kind == "also": sm.states.extend(data)
                elif kind == "transition": sm.transitions.append(data)
        return sm
    def state_decl(self, items):
        content_part = _tok_str(items[0]).strip()
        machine_name = _strip_quotes(_tok_str(items[1]))
        return StateMachine(content_name=content_part, machine_name=machine_name, singular="", initial_state="")
    def state_starts(self, items):
        # ARTICLE WORDS_BEFORE_STARTS "starts as" QUOTED
        singular = _tok_str(items[1]).strip()
        state = _strip_quotes(_tok_str(items[-1]))
        return ("starts", (singular, state))
    def state_also(self, items):
        return ("also", _extract_quoted(_tok_str(items[-1])))
    def state_transition(self, items):
        # ARTICLE WORDS_BEFORE_CAN "can become" REST_OF_LINE
        # WORDS_BEFORE_CAN captures e.g., "draft product" — we need just "draft"
        # The singular is the last word; the from_state is everything before it
        before_can = _tok_str(items[1]).strip()
        # Split off the last word (singular noun) to get the from_state
        parts = before_can.rsplit(" ", 1)
        from_state = parts[0] if len(parts) > 1 else before_can
        rest = _tok_str(items[-1]).strip()
        m = re.match(r'(.+?)(?:\s+again)?\s+if\s+the\s+user\s+has\s+"([^"]+)"', rest)
        if m:
            return ("transition", Transition(from_state=from_state, to_state=m.group(1).strip(), required_scope=m.group(2)))
        return ("transition", Transition(from_state=from_state, to_state=rest, required_scope=""))

    # -- Events --
    def event_block(self, items):
        items = self._filter_newlines(items)
        event = items[0]
        for item in items[1:]:
            if isinstance(item, EventAction): event.action = item
            elif isinstance(item, str) and item.startswith("log:"): event.log_level = item[4:]
        return event
    def event_when_jexl(self, items):
        jexl = _tok_str(items[0]).strip("[] ")
        return EventRule(content_name="", trigger="jexl", jexl_condition=jexl)
    def event_when_v1(self, items):
        # items: ARTICLE REST_OF_LINE — strip the article from the text
        text = " ".join(_tok_str(i) for i in items).strip()
        # Remove leading article
        text = re.sub(r'^(?:a|an|the)\s+', '', text, flags=re.IGNORECASE)
        m = re.match(r'(.+?)\s+is\s+(created|updated|deleted)(?:\s+and\s+its\s+(\w[\w\s]*?)\s+is\s+(at or below)\s+its\s+(\w[\w\s]*?))?:?$', text)
        if m:
            ev = EventRule(content_name=m.group(1), trigger=m.group(2))
            if m.group(3):
                ev.condition = EventCondition(field1=m.group(3).strip(), operator=m.group(4), field2=m.group(5).strip())
            return ev
        return EventRule(content_name=text.rstrip(":"), trigger="unknown")
    def event_action(self, items):
        # items: ARTICLE REST_OF_LINE — strip article
        text = " ".join(_tok_str(i) for i in items).strip()
        text = re.sub(r'^(?:a|an|the)\s+', '', text, flags=re.IGNORECASE)
        m = re.match(r'(.+?)\s+with\s+(?:the\s+)?(.+)', text)
        if m:
            return EventAction(create_content=m.group(1).strip().strip('"'), fields=_parse_comma_list(m.group(2)))
        return EventAction(create_content=text.strip('"'))
    def event_log_level(self, items):
        return "log:" + _tok_str(items[0]).strip()

    # -- Error Handling --
    def error_block(self, items):
        items = self._filter_newlines(items)
        handler = items[0]
        for item in items[1:]:
            if isinstance(item, ErrorAction): handler.actions.append(item)
            elif isinstance(item, str) and item.startswith("log:"):
                handler.actions.append(ErrorAction(kind="log_level", target=item[4:]))
        return handler
    def error_handler(self, items):
        source = _strip_quotes(_tok_str(items[0]))
        cond = None
        for item in items[1:]:
            j = _tok_str(item).strip("[] ")
            if j: cond = j
        return ErrorHandler(source=source, condition_jexl=cond)
    def error_catch_all(self, items):
        return ErrorHandler(source="", is_catch_all=True)
    def error_retry(self, items):
        text = _tok_str(items[0])
        count = 1
        m = re.search(r'(\d+)\s+times?', text)
        if m: count = int(m.group(1))
        return ErrorAction(kind="retry", retry_count=count, retry_backoff="backoff" in text)
    def error_then(self, items):
        text = _tok_str(items[0]).strip()
        if text.startswith("disable"):
            return ErrorAction(kind="disable", target=text.split("disable", 1)[1].strip())
        elif text == "escalate":
            return ErrorAction(kind="escalate")
        elif text.startswith("notify"):
            m = re.match(r'notify\s+"([^"]+)"\s+with\s+\[(.+)\]', text)
            if m: return ErrorAction(kind="notify", target=m.group(1), jexl_expr=m.group(2))
        elif text.startswith("create"):
            m = re.match(r'create\s+"([^"]+)"\s+.*with\s+\[(.+)\]', text)
            if m: return ErrorAction(kind="create", target=m.group(1), jexl_expr=m.group(2))
        elif text.startswith("set"):
            return ErrorAction(kind="set", jexl_expr=_extract_jexl(text))
        return ErrorAction(kind="unknown", target=text)

    # -- Stories --
    def story_block(self, items):
        items = self._filter_newlines(items)
        story = items[0]
        for item in items[1:]:
            if isinstance(item, str) and not isinstance(item, Directive): story.objective = item
            elif isinstance(item, Directive): story.directives.append(item)
        return story
    def story_header(self, items):
        text = _tok_str(items[0])
        m = re.match(r'(?:(?:a|an)\s+)?(.+?),\s+I\s+want\s+to\s+(.*)', text)
        if not m: return UserStory(role="unknown", action=text, objective="")
        role, action = m.group(1).strip(), m.group(2).strip()
        story = UserStory(role=role, action=action, objective="")
        so_m = re.match(r'(.+?)\s+so\s+that\s+(.*?):?$', action)
        if so_m:
            story.action = so_m.group(1).strip()
            story.objective = so_m.group(2).strip()
        page_m = re.search(r'(?:see\s+)?a\s+page\s+"([^"]+)"', story.action)
        if page_m: story.directives.append(ShowPage(page_name=page_m.group(1)))
        return story
    def story_so_that(self, items):
        return _tok_str(items[0]).rstrip(":").strip()

    # -- Directives --
    def show_page(self, items): return ShowPage(page_name=_strip_quotes(_tok_str(items[0])))
    def display_table(self, items):
        text = _tok_str(items[0])
        m = re.match(r'(\w[\w\s]*?)(?:\s+with\s+columns:\s*(.*))?$', text.strip())
        return DisplayTable(content_name=m.group(1).strip() if m else "", columns=_parse_comma_list(m.group(2)) if m and m.group(2) else [])
    def show_related(self, items):
        text = _tok_str(items[0])
        m = re.match(r'(\w[\w\s]*?),\s+show\s+(\w[\w\s]*?)\s+grouped\s+by\s+(\w[\w\s]*?)$', text.strip())
        if m: return ShowRelated(singular=m.group(1).strip(), related_content=m.group(2).strip(), group_by=m.group(3).strip())
        return ShowRelated()
    def highlight_rows(self, items):
        text = _tok_str(items[0])
        jexl = _extract_jexl(text)
        if jexl: return HighlightRows(jexl_condition=jexl)
        m = re.match(r'(\w[\w\s]*?)\s+is\s+(at or below|above|below|equal to)\s+(\w[\w\s]*?)$', text.strip())
        if m: return HighlightRows(field=m.group(1).strip(), operator=m.group(2).strip(), threshold_field=m.group(3).strip())
        return HighlightRows()
    def allow_filtering(self, items): return AllowFilter(fields=_parse_comma_list(_tok_str(items[0])))
    def allow_searching(self, items):
        fields = re.split(r'\s+or\s+|,\s*', _tok_str(items[0]).strip())
        return AllowSearch(fields=[f.strip() for f in fields if f.strip()])
    def subscribes_to(self, items):
        m = re.match(r'(.+?)\s+changes', _tok_str(items[0]).strip())
        return SubscribeTo(content_name=m.group(1).strip() if m else "")
    def accept_input(self, items): return AcceptInput(fields=_parse_comma_list(_tok_str(items[0])))
    def validate_unique(self, items):
        text = _tok_str(items[0])
        jexl = _extract_jexl(text)
        if jexl: return ValidateUnique(jexl_condition=jexl)
        m = re.match(r'(\w[\w\s]*?)\s+is\s+unique', text.strip())
        return ValidateUnique(field=m.group(1).strip() if m else "")
    def create_as(self, items):
        m = re.match(r'\w[\w\s]*?\s+as\s+(\w+)', _tok_str(items[0]).strip())
        return CreateAs(initial_state=m.group(1).strip() if m else "")
    def after_saving(self, items): return AfterSave(instruction=_tok_str(items[0]).strip())
    def show_chart(self, items):
        text = _tok_str(items[0])
        m = re.match(r'(.+?)\s+over\s+the\s+past\s+(\d+)\s+days', text.strip())
        return ShowChart(content_name=m.group(1).strip() if m else "", days=int(m.group(2)) if m else 30)
    def display_text(self, items):
        text = _tok_str(items[0])
        jexl = _extract_jexl(text)
        if jexl: return DisplayText(text=jexl, is_expression=True)
        quoted = _extract_quoted(text)
        if quoted: return DisplayText(text=quoted[0])
        return DisplayText(text=text.strip(), is_expression=True)
    def display_aggregation(self, items):
        return DisplayAggregation(description=_tok_str(items[0]).strip())

    # -- Navigation --
    def nav_block(self, items):
        items = self._filter_newlines(items)
        nav = NavBar()
        for item in items:
            if isinstance(item, NavItem): nav.items.append(item)
        return nav
    def nav_bar(self, items): return None
    def nav_item(self, items):
        label = _strip_quotes(_tok_str(items[0]))
        page = _strip_quotes(_tok_str(items[1]))
        rest = _tok_str(items[2]) if len(items) > 2 else ""
        vis_m = re.search(r'visible\s+to\s+(.+?)(?:,\s*badge:|$)', rest)
        visible_to = _parse_comma_list(vis_m.group(1)) if vis_m else []
        badge_m = re.search(r'badge:\s*(.+)$', rest)
        return NavItem(label=label, page_name=page, visible_to=visible_to, badge=badge_m.group(1).strip() if badge_m else None)

    # -- API --
    def api_block(self, items):
        items = self._filter_newlines(items)
        api = items[0]
        for item in items[1:]:
            if isinstance(item, ApiEndpoint): api.endpoints.append(item)
        return api
    def api_section(self, items): return ApiSection(base_path=_tok_str(items[0]).rstrip(":").strip())
    def api_endpoint(self, items):
        return ApiEndpoint(method=_tok_str(items[0]), path=_tok_str(items[1]), description=_tok_str(items[2]).strip() if len(items) > 2 else "")

    # -- Streams --
    def stream_decl(self, items):
        m = re.match(r'(.+?)\s+at\s+(\S+)', _tok_str(items[0]))
        return Stream(description=m.group(1), path=m.group(2)) if m else Stream(description=_tok_str(items[0]), path="")

    # -- Compute --
    def compute_block(self, items):
        items = self._filter_newlines(items)
        node = items[0]
        for item in items[1:]:
            if isinstance(item, tuple) and item[0] == "shape":
                _, node.shape, node.inputs, node.outputs, node.input_params, node.output_params = item
            elif isinstance(item, str) and item.startswith("body:"):
                node.body_lines.append(item[5:])
            elif isinstance(item, AccessRule):
                node.access_scope = item.scope
        remaining = []
        for line in node.body_lines:
            m = re.match(r'(?:"([^"]+)"|(\w+))\s+can\s+execute\s+this', line)
            if m and not node.access_scope: node.access_role = m.group(1) or m.group(2)
            else: remaining.append(line)
        node.body_lines = remaining
        return node
    def compute_decl(self, items): return ComputeNode(name=_strip_quotes(_tok_str(items[0])))
    def compute_shape(self, items):
        shape = _tok_str(items[0]).lower()
        rest = _tok_str(items[1])
        inputs, outputs, in_p, out_p = [], [], [], []
        io_m = re.match(r'takes\s+(?:a\s+|an\s+)?(.+?),\s*produces\s+(?:a\s+|an\s+)?(.+)', rest)
        if io_m:
            it, ot = io_m.group(1).strip(), io_m.group(2).strip()
            for m in re.finditer(r'(?:"([^"]+)"|(\w+))\s*:\s*(\w+)', it):
                in_p.append(ComputeParam(name=m.group(1) or m.group(2), type_name=m.group(3)))
            for m in re.finditer(r'(?:"([^"]+)"|(\w+))\s*:\s*(\w+)', ot):
                out_p.append(ComputeParam(name=m.group(1) or m.group(2), type_name=m.group(3)))
            inputs = [p.type_name for p in in_p] if in_p else [i.strip() for i in re.split(r'\s+and\s+', it)]
            if ot.startswith("one of "): outputs = [o.strip() for o in re.split(r',\s*(?:or\s+)?|\s+or\s+', ot[7:])]
            elif out_p: outputs = [p.type_name for p in out_p]
            else: outputs = [o.strip() for o in re.split(r'\s+and\s+', ot)]
        return ("shape", shape, inputs, outputs, in_p, out_p)
    def compute_body_jexl(self, items): return "body:" + _tok_str(items[0]).strip("[] ")
    def compute_body_text(self, items): return "body:" + _tok_str(items[0])

    # -- Channels --
    def channel_block(self, items):
        items = self._filter_newlines(items)
        ch = items[0]
        for item in items[1:]:
            if isinstance(item, tuple):
                k, v = item
                if k == "carries": ch.carries = v
                elif k == "direction": ch.direction = v
                elif k == "delivery": ch.delivery = v
                elif k == "endpoint": ch.endpoint = v
                elif k == "requires": ch.requirements.append(v)
        return ch
    def channel_decl(self, items): return ChannelDecl(name=_strip_quotes(_tok_str(items[0])))
    def channel_carries(self, items): return ("carries", _tok_str(items[0]).strip())
    def channel_direction(self, items): return ("direction", _tok_str(items[0]).strip().lower())
    def channel_delivery(self, items): return ("delivery", _tok_str(items[0]).strip().lower())
    def channel_requires(self, items):
        scope = _strip_quotes(_tok_str(items[0]))
        rest = _tok_str(items[1]) if len(items) > 1 else "receive"
        m = re.search(r'(send|receive)', rest)
        return ("requires", ChannelRequirement(scope=scope, direction=m.group(1) if m else "receive"))
    def channel_endpoint(self, items): return ("endpoint", _tok_str(items[0]).strip())

    # -- Boundaries --
    def boundary_block(self, items):
        items = self._filter_newlines(items)
        bnd = items[0]
        for item in items[1:]:
            if isinstance(item, tuple):
                k, v = item
                if k == "contains": bnd.contains = v
                elif k == "identity_mode":
                    bnd.identity_mode = v[0]
                    if v[0] == "inherit": bnd.identity_parent = v[1]
                    elif v[0] == "restrict": bnd.identity_scopes = v[1]
                elif k == "property": bnd.properties.append(v)
        return bnd
    def boundary_decl(self, items): return BoundaryDecl(name=_strip_quotes(_tok_str(items[0])))
    def boundary_contains(self, items): return ("contains", _parse_comma_list(_tok_str(items[0])))
    def boundary_identity(self, items):
        text = _tok_str(items[0])
        if "inherits" in text:
            m = re.search(r'from\s+(.+)', text)
            return ("identity_mode", ("inherit", m.group(1).strip() if m else ""))
        elif "restricts" in text:
            return ("identity_mode", ("restrict", _extract_quoted(text)))
        return ("identity_mode", ("inherit", ""))
    def boundary_exposes(self, items):
        name = _strip_quotes(_tok_str(items[0]))
        rest = _tok_str(items[1])
        m = re.match(r'(\w[\w\s]*?)\s*=\s*\[(.+)\]', rest.strip())
        if m: return ("property", BoundaryProperty(name=name, type_name=m.group(1).strip(), jexl_expr=m.group(2).strip()))
        return ("property", BoundaryProperty(name=name, type_name=rest.strip(), jexl_expr=""))


_transformer = _TerminTransformer()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_lark(source: str) -> tuple[Program, CompileResult]:
    """Parse a .termin source string using grammar.lark (authoritative grammar).

    Drop-in replacement for termin.parser.parse().
    """
    errors = CompileResult()
    try:
        cleaned = _preprocess(source)
        tree = _parser.parse(cleaned)
        program = _transformer.transform(tree)
        return program, errors
    except Exception as e:
        errors.add(ParseError(message=str(e), line=0))
        return Program(), errors
