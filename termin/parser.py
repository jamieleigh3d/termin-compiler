"""Recursive descent parser for the an AWS-native Termin runtime DSL.

Consumes a token stream from the lexer and builds a Program AST.
"""

import re
from typing import Optional

from .lexer import Token, TokenType, tokenize
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


def _extract_quoted(text: str) -> list[str]:
    """Extract all double-quoted strings from text."""
    return re.findall(r'"([^"]*)"', text)


def _parse_comma_list(text: str) -> list[str]:
    """Parse a comma-and-'and'-separated list of words/phrases.

    Handles: 'SKU, name, description, unit cost, and category'
    Returns: ['SKU', 'name', 'description', 'unit cost', 'category']
    """
    # Remove trailing content after a known stop-word
    text = text.strip().rstrip(":")
    # Split on comma and 'and'
    parts = re.split(r',\s*(?:and\s+)?|\s+and\s+', text)
    return [p.strip() for p in parts if p.strip()]


class Parser:
    def __init__(self, tokens: list[Token], source: str = ""):
        self.tokens = tokens
        self.pos = 0
        self.source_lines = source.splitlines() if source else []
        self.errors = CompileResult()

    def peek(self) -> Optional[Token]:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def advance(self) -> Token:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def at_end(self) -> bool:
        return self.pos >= len(self.tokens)

    def check(self, *types: TokenType) -> bool:
        t = self.peek()
        return t is not None and t.type in types

    def expect(self, *types: TokenType) -> Token:
        t = self.peek()
        if t is None:
            raise ParseError(
                message=f"Unexpected end of file, expected {' or '.join(tt.name for tt in types)}",
                line=self.tokens[-1].line if self.tokens else 0,
            )
        if t.type not in types:
            raise ParseError(
                message=f"Expected {' or '.join(tt.name for tt in types)}, got {t.type.name}",
                line=t.line,
                source_line=self._source_line(t.line),
            )
        return self.advance()

    def _source_line(self, line: int) -> str:
        if 0 < line <= len(self.source_lines):
            return self.source_lines[line - 1]
        return ""

    def _error(self, message: str, line: int) -> None:
        self.errors.add(ParseError(
            message=message,
            line=line,
            source_line=self._source_line(line),
        ))

    # ── Top-level ──

    def parse(self) -> Program:
        program = Program()

        while not self.at_end():
            t = self.peek()
            try:
                if t.type == TokenType.APPLICATION:
                    program.application = self.parse_application()
                elif t.type == TokenType.USERS_AUTHENTICATE:
                    program.identity = self.parse_identity()
                elif t.type == TokenType.ROLE_DECL:
                    program.roles.append(self.parse_role())
                elif t.type == TokenType.CONTENT_DECL:
                    program.contents.append(self.parse_content())
                elif t.type == TokenType.STATE_DECL:
                    program.state_machines.append(self.parse_state())
                elif t.type == TokenType.EVENT_WHEN:
                    program.events.append(self.parse_event())
                elif t.type == TokenType.STORY_HEADER:
                    program.stories.append(self.parse_story())
                elif t.type == TokenType.NAV_BAR:
                    program.navigation = self.parse_navigation()
                elif t.type == TokenType.API_SECTION:
                    program.api = self.parse_api()
                elif t.type == TokenType.STREAM_DECL:
                    program.streams.append(self.parse_stream())
                elif t.type == TokenType.COMPUTE_DECL:
                    program.computes.append(self.parse_compute())
                elif t.type == TokenType.CHANNEL_DECL:
                    program.channels.append(self.parse_channel())
                elif t.type == TokenType.BOUNDARY_DECL:
                    program.boundaries.append(self.parse_boundary())
                else:
                    self._error(f"Unexpected line: {t.value}", t.line)
                    self.advance()
            except ParseError as e:
                self.errors.add(e)
                self.advance()

        return program

    # ── Application ──

    def parse_application(self) -> Application:
        t = self.expect(TokenType.APPLICATION)
        name = t.value.split(":", 1)[1].strip()
        app = Application(name=name, line=t.line)

        if self.check(TokenType.DESCRIPTION):
            dt = self.advance()
            app.description = dt.value.split(":", 1)[1].strip()

        return app

    # ── Identity ──

    def parse_identity(self) -> Identity:
        t = self.expect(TokenType.USERS_AUTHENTICATE)
        # "Users authenticate with stub"
        provider = t.value.rsplit("with", 1)[1].strip()
        identity = Identity(provider=provider, line=t.line)

        if self.check(TokenType.SCOPES_ARE):
            st = self.advance()
            # 'Scopes are "read inventory", "write inventory", and "admin inventory"'
            identity.scopes = _extract_quoted(st.value)

        return identity

    # ── Roles ──

    def parse_role(self) -> Role:
        t = self.expect(TokenType.ROLE_DECL)
        # Check for standard form: A "role" has "scope1" and "scope2"
        m_standard = re.match(r'^(?:A|An)\s+"([^"]+)"\s+has\s+', t.value)
        if m_standard:
            name = m_standard.group(1)
            scopes = _extract_quoted(t.value[m_standard.end():])
        else:
            # Bare form: Anonymous has "scope1" and "scope2"
            m = re.match(r'(\w+)\s+has\s+(.*)', t.value)
            if not m:
                raise ParseError(message="Role declaration missing name", line=t.line,
                                 source_line=self._source_line(t.line))
            name = m.group(1)
            scopes = _extract_quoted(m.group(2))
        return Role(name=name, scopes=scopes, line=t.line)

    # ── Content ──

    def parse_content(self) -> Content:
        t = self.expect(TokenType.CONTENT_DECL)
        name = _extract_quoted(t.value)[0]
        # Derive singular from name: "stock levels" -> "stock level", "products" -> "product"
        singular = name.rstrip("s") if name.endswith("s") else name
        content = Content(name=name, singular=singular, line=t.line)

        while self.check(TokenType.FIELD_DECL, TokenType.ACCESS_RULE):
            if self.check(TokenType.FIELD_DECL):
                content.fields.append(self.parse_field(content.singular))
            elif self.check(TokenType.ACCESS_RULE):
                content.access_rules.append(self.parse_access_rule())

        return content

    def parse_field(self, singular: str) -> Field:
        t = self.expect(TokenType.FIELD_DECL)
        # "Each product has a SKU which is unique text, required"
        # "Each stock level has a product which references products, required"
        # "Each reorder alert has a created at which is automatic"
        # Use the known singular to anchor the match.
        # Handle both "which is {type}" and "which references {content}".
        escaped_singular = re.escape(singular)
        m = re.match(
            r'Each\s+' + escaped_singular + r'\s+has\s+(?:a|an)\s+(.+?)\s+which\s+(?:is\s+|references\s+)(.*)',
            t.value
        )
        if not m:
            raise ParseError(message=f"Cannot parse field declaration", line=t.line,
                             source_line=self._source_line(t.line))
        field_name = m.group(1).strip()
        type_text = m.group(2).strip()
        # If we matched "which references X", prepend "references " for the type parser
        if "which references" in t.value.split("has", 1)[1]:
            type_text = "references " + type_text
        type_expr = self._parse_type_expr(type_text, t.line)
        return Field(name=field_name, type_expr=type_expr, line=t.line)

    def _parse_type_expr(self, text: str, line: int) -> TypeExpr:
        expr = TypeExpr(base_type="text", line=line)
        text = text.strip()

        # Check for 'required' modifier
        if text.endswith(", required") or text.endswith(",required"):
            expr.required = True
            text = re.sub(r',\s*required$', '', text).strip()

        # Check for 'unique' modifier
        if text.startswith("unique "):
            expr.unique = True
            text = text[7:].strip()

        # Now determine base type
        if text == "text":
            expr.base_type = "text"
        elif text == "currency":
            expr.base_type = "currency"
        elif text == "automatic":
            expr.base_type = "automatic"
        elif text.startswith("a whole number"):
            expr.base_type = "whole_number"
            m = re.search(r'minimum\s+(\d+)', text)
            if m:
                expr.minimum = int(m.group(1))
        elif text.startswith("one of:"):
            expr.base_type = "enum"
            vals = text.split(":", 1)[1].strip()
            expr.enum_values = _parse_comma_list(vals)
        elif text.startswith("references "):
            expr.base_type = "reference"
            expr.references = text.split("references", 1)[1].strip()
        else:
            self._error(f"Unknown type expression: {text}", line)

        return expr

    def parse_access_rule(self) -> AccessRule:
        t = self.expect(TokenType.ACCESS_RULE)
        # 'Anyone with "write inventory" can create or update products'
        scope = _extract_quoted(t.value)[0]
        m = re.search(r'can\s+(.+)', t.value)
        if not m:
            raise ParseError(message="Cannot parse access rule verbs", line=t.line,
                             source_line=self._source_line(t.line))
        verb_text = m.group(1).strip()
        # Remove content name at the end
        # "create or update products" -> verbs=["create or update"]
        # "view products" -> verbs=["view"]
        # "delete products" -> verbs=["delete"]
        parts = verb_text.rsplit(" ", 1)  # split off last word (content name)
        verb_part = parts[0] if len(parts) > 1 else verb_text

        # Split compound verbs: "create or update" stays as one verb
        if "or" in verb_part:
            verbs = [verb_part]
        else:
            verbs = [v.strip() for v in verb_part.split(",")]

        return AccessRule(scope=scope, verbs=verbs, line=t.line)

    # ── State ──

    def parse_state(self) -> StateMachine:
        t = self.expect(TokenType.STATE_DECL)
        # 'State for products called "product lifecycle":'
        m = re.match(r'State for\s+(\w+)\s+called\s+"([^"]+)"', t.value)
        if not m:
            raise ParseError(message="Cannot parse state declaration", line=t.line,
                             source_line=self._source_line(t.line))
        content_name = m.group(1)
        machine_name = m.group(2)

        sm = StateMachine(
            content_name=content_name, machine_name=machine_name,
            singular="", initial_state="", line=t.line
        )

        # Parse "A product starts as "draft""
        if self.check(TokenType.STATE_STARTS):
            st = self.advance()
            m2 = re.match(r'(?:A|An)\s+(\w+)\s+starts\s+as\s+"([^"]+)"', st.value)
            if m2:
                sm.singular = m2.group(1)
                sm.initial_state = m2.group(2)
                sm.states.append(sm.initial_state)

        # Parse "A product can also be "active" or "discontinued""
        if self.check(TokenType.STATE_ALSO):
            at = self.advance()
            sm.states.extend(_extract_quoted(at.value))

        # Parse transitions
        while self.check(TokenType.STATE_TRANSITION):
            tt = self.advance()
            # "A draft product can become active if the user has "write inventory""
            # "A discontinued product can become active again if the user has "admin inventory""
            # "An in progress ticket can become resolved if the user has "manage tickets""
            m3 = re.match(
                r'(?:A|An)\s+(.+?)\s+' + re.escape(sm.singular) +
                r'\s+can\s+become\s+(.+?)(?:\s+again)?\s+if\s+the\s+user\s+has\s+"([^"]+)"',
                tt.value
            )
            if m3:
                sm.transitions.append(Transition(
                    from_state=m3.group(1),
                    to_state=m3.group(2),
                    required_scope=m3.group(3),
                    line=tt.line,
                ))
            else:
                self._error(f"Cannot parse state transition: {tt.value}", tt.line)

        return sm

    # ── Events ──

    def parse_event(self) -> EventRule:
        t = self.expect(TokenType.EVENT_WHEN)
        # "When a stock level is updated and its quantity is at or below its reorder threshold:"
        m = re.match(
            r'When\s+(?:a|an)\s+(.+?)\s+is\s+(created|updated|deleted)'
            r'(?:\s+and\s+its\s+(\w[\w\s]*?)\s+is\s+(at or below)\s+its\s+(\w[\w\s]*?))?:?$',
            t.value
        )
        if not m:
            raise ParseError(message="Cannot parse event rule", line=t.line,
                             source_line=self._source_line(t.line))

        event = EventRule(
            content_name=m.group(1),
            trigger=m.group(2),
            line=t.line,
        )

        if m.group(3):
            event.condition = EventCondition(
                field1=m.group(3).strip(),
                operator=m.group(4),
                field2=m.group(5).strip(),
                line=t.line,
            )

        # Parse action
        if self.check(TokenType.EVENT_ACTION):
            at = self.advance()
            # "Create a reorder alert with the product, warehouse, current quantity, and threshold"
            m2 = re.match(r'Create\s+(?:a|an)\s+(.+?)\s+with\s+(?:the\s+)?(.+)', at.value)
            if m2:
                create_content = m2.group(1)
                fields_text = m2.group(2)
                fields = _parse_comma_list(fields_text)
                event.action = EventAction(
                    create_content=create_content,
                    fields=fields,
                    line=at.line,
                )

        return event

    # ── User Stories ──

    def parse_story(self) -> UserStory:
        t = self.expect(TokenType.STORY_HEADER)
        # "As a warehouse clerk, I want to see all products and their current stock levels"
        # "As anonymous, I want to see a page "Hello" so that I can be greeted:"
        m = re.match(r'As\s+(?:(?:a|an)\s+)?(.+?),\s+I\s+want\s+to\s+(.*)', t.value)
        if not m:
            raise ParseError(message="Cannot parse user story header", line=t.line,
                             source_line=self._source_line(t.line))

        role = m.group(1).strip()
        action = m.group(2).strip()

        story = UserStory(role=role, action=action, objective="", line=t.line)

        # Handle inline "so that" on the same line
        so_that_inline = re.match(r'(.+?)\s+so\s+that\s+(.*?):?$', action)
        if so_that_inline:
            story.action = so_that_inline.group(1).strip()
            story.objective = so_that_inline.group(2).strip()

        # Extract inline page name from action: 'see a page "Hello"'
        page_match = re.search(r'(?:see\s+)?a\s+page\s+"([^"]+)"', story.action)
        if page_match:
            story.directives.append(ShowPage(page_name=page_match.group(1), line=t.line))

        # Parse "so that" line (if not already inline)
        if not story.objective and self.check(TokenType.STORY_SO_THAT):
            st = self.advance()
            obj = re.match(r'so\s+that\s+(.*?):?$', st.value)
            if obj:
                story.objective = obj.group(1).strip()

        # Parse directives
        while self.check(
            TokenType.SHOW_PAGE, TokenType.DISPLAY_TABLE, TokenType.SHOW_RELATED,
            TokenType.HIGHLIGHT_ROWS, TokenType.ALLOW_FILTERING, TokenType.ALLOW_SEARCHING,
            TokenType.SUBSCRIBES_TO, TokenType.ACCEPT_INPUT, TokenType.VALIDATE_UNIQUE,
            TokenType.CREATE_AS, TokenType.AFTER_SAVING, TokenType.SHOW_CHART,
            TokenType.DISPLAY_TEXT, TokenType.DISPLAY_AGGREGATION,
        ):
            story.directives.append(self.parse_directive())

        return story

    def parse_directive(self) -> Directive:
        t = self.peek()

        if t.type == TokenType.SHOW_PAGE:
            self.advance()
            name = _extract_quoted(t.value)[0]
            return ShowPage(page_name=name, line=t.line)

        elif t.type == TokenType.DISPLAY_TABLE:
            self.advance()
            # "Display a table of products with columns: SKU, name, category, status"
            m = re.match(r'Display a table of\s+(\w[\w\s]*?)(?:\s+with\s+columns:\s*(.*))?$', t.value.strip())
            content = m.group(1).strip() if m else ""
            cols = _parse_comma_list(m.group(2)) if m and m.group(2) else []
            return DisplayTable(content_name=content, columns=cols, line=t.line)

        elif t.type == TokenType.SHOW_RELATED:
            self.advance()
            # "For each product, show stock levels grouped by warehouse"
            m = re.match(r'For each\s+(\w[\w\s]*?),\s+show\s+(\w[\w\s]*?)\s+grouped\s+by\s+(\w[\w\s]*?)$', t.value.strip())
            if m:
                return ShowRelated(
                    singular=m.group(1).strip(),
                    related_content=m.group(2).strip(),
                    group_by=m.group(3).strip(),
                    line=t.line,
                )
            return ShowRelated(line=t.line)

        elif t.type == TokenType.HIGHLIGHT_ROWS:
            self.advance()
            # "Highlight rows where quantity is at or below reorder threshold"
            m = re.match(r'Highlight rows where\s+(\w[\w\s]*?)\s+is\s+(at or below|above|below|equal to)\s+(\w[\w\s]*?)$', t.value.strip())
            if m:
                return HighlightRows(
                    field=m.group(1).strip(),
                    operator=m.group(2).strip(),
                    threshold_field=m.group(3).strip(),
                    line=t.line,
                )
            return HighlightRows(line=t.line)

        elif t.type == TokenType.ALLOW_FILTERING:
            self.advance()
            # "Allow filtering by category, warehouse, and status"
            text = t.value.split("by", 1)[1] if "by" in t.value else ""
            return AllowFilter(fields=_parse_comma_list(text), line=t.line)

        elif t.type == TokenType.ALLOW_SEARCHING:
            self.advance()
            # "Allow searching by SKU or name"
            text = t.value.split("by", 1)[1] if "by" in t.value else ""
            fields = re.split(r'\s+or\s+|,\s*', text.strip())
            return AllowSearch(fields=[f.strip() for f in fields if f.strip()], line=t.line)

        elif t.type == TokenType.SUBSCRIBES_TO:
            self.advance()
            # "This table subscribes to stock level changes"
            m = re.match(r'This table subscribes to\s+(.+?)\s+changes', t.value.strip())
            content = m.group(1).strip() if m else ""
            return SubscribeTo(content_name=content, line=t.line)

        elif t.type == TokenType.ACCEPT_INPUT:
            self.advance()
            # "Accept input for SKU, name, description, unit cost, and category"
            text = t.value.split("for", 1)[1] if "for" in t.value else ""
            return AcceptInput(fields=_parse_comma_list(text), line=t.line)

        elif t.type == TokenType.VALIDATE_UNIQUE:
            self.advance()
            # "Validate that SKU is unique before saving"
            m = re.match(r'Validate that\s+(\w[\w\s]*?)\s+is\s+unique', t.value.strip())
            field_name = m.group(1).strip() if m else ""
            return ValidateUnique(field=field_name, line=t.line)

        elif t.type == TokenType.CREATE_AS:
            self.advance()
            # "Create the product as draft"
            m = re.match(r'Create the\s+\w[\w\s]*?\s+as\s+(\w+)', t.value.strip())
            state = m.group(1).strip() if m else ""
            return CreateAs(initial_state=state, line=t.line)

        elif t.type == TokenType.AFTER_SAVING:
            self.advance()
            # "After saving, offer to set initial stock levels per warehouse"
            m = re.match(r'After saving,\s+(.*)', t.value.strip())
            instruction = m.group(1).strip() if m else ""
            return AfterSave(instruction=instruction, line=t.line)

        elif t.type == TokenType.SHOW_CHART:
            self.advance()
            # "Show a chart of reorder alerts over the past 30 days"
            m = re.match(r'Show a chart of\s+(.+?)\s+over\s+the\s+past\s+(\d+)\s+days', t.value.strip())
            content = m.group(1).strip() if m else ""
            days = int(m.group(2)) if m else 30
            return ShowChart(content_name=content, days=days, line=t.line)

        elif t.type == TokenType.DISPLAY_TEXT:
            self.advance()
            # 'Display text "Hello, World"' or 'Display text SayHelloTo(...)'
            quoted = _extract_quoted(t.value)
            if quoted:
                return DisplayText(text=quoted[0], line=t.line)
            else:
                # Unquoted expression
                expr = re.sub(r'^\s*Display\s+text\s+', '', t.value).strip()
                return DisplayText(text=expr, is_expression=True, line=t.line)

        elif t.type == TokenType.DISPLAY_AGGREGATION:
            self.advance()
            # "Display total product count with active vs discontinued breakdown"
            text = t.value.strip()
            if text.startswith("Display "):
                text = text[8:]
            return DisplayAggregation(description=text, line=t.line)

        else:
            self.advance()
            return Directive(line=t.line)

    # ── Navigation ──

    def parse_navigation(self) -> NavBar:
        t = self.expect(TokenType.NAV_BAR)
        nav = NavBar(line=t.line)

        while self.check(TokenType.NAV_ITEM):
            nt = self.advance()
            # '"Dashboard" links to "Inventory Dashboard" visible to all'
            # '"Add Product" links to "Add Product" visible to manager'
            # '"Alerts" links to "Reorder Alerts" visible to all, badge: open alert count'
            quoted = _extract_quoted(nt.value)
            label = quoted[0] if len(quoted) > 0 else ""
            page = quoted[1] if len(quoted) > 1 else ""

            # Parse visibility
            vis_match = re.search(r'visible\s+to\s+(.+?)(?:,\s*badge:|$)', nt.value)
            visible_to = []
            if vis_match:
                vis_text = vis_match.group(1).strip()
                visible_to = _parse_comma_list(vis_text)

            # Parse badge
            badge = None
            badge_match = re.search(r'badge:\s*(.+)$', nt.value)
            if badge_match:
                badge = badge_match.group(1).strip()

            nav.items.append(NavItem(
                label=label, page_name=page,
                visible_to=visible_to, badge=badge,
                line=nt.line,
            ))

        return nav

    # ── API ──

    def parse_api(self) -> ApiSection:
        t = self.expect(TokenType.API_SECTION)
        # "Expose a REST API at /api/v1:"
        m = re.match(r'Expose a REST API at\s+(\S+)', t.value)
        base_path = m.group(1).rstrip(":") if m else "/api"
        api = ApiSection(base_path=base_path, line=t.line)

        while self.check(TokenType.API_ENDPOINT):
            et = self.advance()
            # "GET    /products                lists products"
            m2 = re.match(r'(GET|POST|PUT|DELETE|PATCH)\s+(\S+)\s+(.*)', et.value.strip())
            if m2:
                api.endpoints.append(ApiEndpoint(
                    method=m2.group(1),
                    path=m2.group(2),
                    description=m2.group(3).strip(),
                    line=et.line,
                ))

        return api

    # ── Stream ──

    def parse_stream(self) -> Stream:
        t = self.expect(TokenType.STREAM_DECL)
        # "Stream stock updates and alerts at /api/v1/stream"
        m = re.match(r'Stream\s+(.+?)\s+at\s+(\S+)', t.value)
        if m:
            return Stream(description=m.group(1), path=m.group(2), line=t.line)
        raise ParseError(message="Cannot parse stream declaration", line=t.line,
                         source_line=self._source_line(t.line))


    # ── Compute ──

    def parse_compute(self) -> ComputeNode:
        t = self.expect(TokenType.COMPUTE_DECL)
        name = _extract_quoted(t.value)[0]
        node = ComputeNode(name=name, line=t.line)

        # Parse shape line: "Transform: takes X, produces Y" or "Chain: X then Y"
        if self.check(TokenType.COMPUTE_SHAPE):
            st = self.advance()
            m = re.match(r'\s*(\w+):\s+(.*)', st.value)
            if m:
                node.shape = m.group(1).lower()
                rest = m.group(2).strip()
                if node.shape == "chain":
                    # "calculate reorder quantity then inventory valuation"
                    node.chain_steps = [s.strip() for s in re.split(r'\s+then\s+', rest)]
                else:
                    # "takes a stock level, produces a stock level"
                    # "takes u : UserProfile, produces "greeting" : Text"
                    io_match = re.match(r'takes\s+(?:a\s+|an\s+)?(.+?),\s*produces\s+(?:a\s+|an\s+)?(.+)', rest)
                    if io_match:
                        inputs_text = io_match.group(1).strip()
                        outputs_text = io_match.group(2).strip()

                        # Check for typed parameters: "u : UserProfile"
                        node.input_params = self._parse_typed_params(inputs_text, st.line)
                        node.output_params = self._parse_typed_params(outputs_text, st.line)

                        # Also populate plain inputs/outputs for backward compat
                        if node.input_params:
                            node.inputs = [p.type_name for p in node.input_params]
                        else:
                            node.inputs = [i.strip() for i in re.split(r'\s+and\s+', inputs_text)]

                        # Handle "one of X, Y, or Z" for Route shape
                        if outputs_text.startswith("one of "):
                            outputs_text = outputs_text[7:]
                            node.outputs = [o.strip() for o in re.split(r',\s*(?:or\s+)?|\s+or\s+', outputs_text)]
                        elif node.output_params:
                            node.outputs = [p.type_name for p in node.output_params]
                        else:
                            node.outputs = [o.strip() for o in re.split(r'\s+and\s+', outputs_text)]

        # Consume body lines (UNKNOWN tokens) and access rules
        while self.check(TokenType.UNKNOWN, TokenType.ACCESS_RULE):
            if self.check(TokenType.ACCESS_RULE):
                at = self.advance()
                scope = _extract_quoted(at.value)[0]
                node.access_scope = scope
            else:
                bt = self.advance()
                node.body_lines.append(bt.value)

        # Check body lines for role-as-subject access: "RoleName can execute this"
        remaining = []
        for line in node.body_lines:
            m = re.match(r'(\w+)\s+can\s+execute\s+this', line)
            if m and not node.access_scope:
                node.access_role = m.group(1)
            else:
                remaining.append(line)
        node.body_lines = remaining

        return node

    def _parse_typed_params(self, text: str, line: int) -> list[ComputeParam]:
        """Parse typed parameters like 'u : UserProfile' or '"greeting" : Text'."""
        params = []
        # Match patterns like: identifier : Type, or "name" : Type
        for m in re.finditer(r'(?:"([^"]+)"|(\w+))\s*:\s*(\w+)', text):
            name = m.group(1) or m.group(2)
            type_name = m.group(3)
            params.append(ComputeParam(name=name, type_name=type_name, line=line))
        return params

    # ── Channel ──

    def parse_channel(self) -> ChannelDecl:
        t = self.expect(TokenType.CHANNEL_DECL)
        name = _extract_quoted(t.value)[0]
        channel = ChannelDecl(name=name, line=t.line)

        while self.check(
            TokenType.CHANNEL_CARRIES, TokenType.CHANNEL_PROTOCOL,
            TokenType.CHANNEL_DIRECTION, TokenType.CHANNEL_REQUIRES,
            TokenType.CHANNEL_ENDPOINT,
        ):
            ct = self.advance()
            if ct.type == TokenType.CHANNEL_CARRIES:
                # "Carries products"
                channel.carries = ct.value.split("Carries", 1)[1].strip()
            elif ct.type == TokenType.CHANNEL_PROTOCOL:
                # "Protocol: SSE"
                channel.protocol = ct.value.split(":", 1)[1].strip().lower()
            elif ct.type == TokenType.CHANNEL_DIRECTION:
                # "From application to external"
                m = re.match(r'\s*From\s+(.+?)\s+to\s+(.+)', ct.value)
                if m:
                    channel.source = m.group(1).strip().lower()
                    channel.destination = m.group(2).strip().lower()
            elif ct.type == TokenType.CHANNEL_REQUIRES:
                # 'Requires "read inventory" to receive'
                scope = _extract_quoted(ct.value)[0]
                m = re.search(r'to\s+(send|receive)\s*$', ct.value)
                direction = m.group(1) if m else "receive"
                channel.requirements.append(ChannelRequirement(
                    scope=scope, direction=direction, line=ct.line,
                ))
            elif ct.type == TokenType.CHANNEL_ENDPOINT:
                # "Endpoint: /webhooks/orders"
                channel.endpoint = ct.value.split(":", 1)[1].strip()

        return channel

    # ── Boundary ──

    def parse_boundary(self) -> BoundaryDecl:
        t = self.expect(TokenType.BOUNDARY_DECL)
        name = _extract_quoted(t.value)[0]
        boundary = BoundaryDecl(name=name, line=t.line)

        while self.check(TokenType.BOUNDARY_CONTAINS, TokenType.BOUNDARY_IDENTITY):
            ct = self.advance()
            if ct.type == TokenType.BOUNDARY_CONTAINS:
                # "Contains products, stock levels, and reorder alerts"
                text = ct.value.split("Contains", 1)[1].strip()
                boundary.contains = _parse_comma_list(text)
            elif ct.type == TokenType.BOUNDARY_IDENTITY:
                # "Identity inherits from application"
                # "Identity restricts to "read inventory""
                if "inherits" in ct.value:
                    boundary.identity_mode = "inherit"
                    m = re.search(r'from\s+(.+)', ct.value)
                    if m:
                        boundary.identity_parent = m.group(1).strip()
                elif "restricts" in ct.value:
                    boundary.identity_mode = "restrict"
                    boundary.identity_scopes = _extract_quoted(ct.value)

        return boundary


def parse(source: str) -> tuple[Program, CompileResult]:
    """Parse a .termin source string into a Program AST.

    Returns (program, errors). Check errors.ok before using program.
    """
    tokens = tokenize(source)
    parser = Parser(tokens, source)
    program = parser.parse()
    return program, parser.errors
