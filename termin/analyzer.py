"""Semantic analysis and security invariant checking for an AWS-native Termin runtime AST.

Two-pass analysis:
1. Semantic analysis: resolve references, validate cross-links
2. Security invariant checks: enforce the security properties that make an AWS-native Termin runtime's argument
"""

from .ast_nodes import (
    Program, Content, StateMachine, EventRule, UserStory, ShowPage,
    DisplayTable, AcceptInput, SubscribeTo, ShowRelated, AllowFilter,
    AllowSearch, ShowChart, DisplayAggregation,
)
from .errors import SemanticError, SecurityError, CompileResult


class Analyzer:
    def __init__(self, program: Program):
        self.program = program
        self.errors = CompileResult()

        # Symbol tables built during analysis
        self.content_names: set[str] = set()
        self.scope_names: set[str] = set()
        self.role_names: set[str] = set()
        self.page_names: set[str] = set()
        self.content_field_names: dict[str, set[str]] = {}  # content_name -> {field_names}

    def analyze(self) -> CompileResult:
        self._build_symbol_tables()
        self._check_semantics()
        self._check_security_invariants()
        return self.errors

    # ── Symbol Table Construction ──

    def _build_symbol_tables(self) -> None:
        p = self.program

        if p.identity:
            self.scope_names = set(p.identity.scopes)

        for role in p.roles:
            self.role_names.add(role.name)

        for content in p.contents:
            self.content_names.add(content.name)
            self.content_field_names[content.name] = {f.name for f in content.fields}

        for story in p.stories:
            for d in story.directives:
                if isinstance(d, ShowPage):
                    self.page_names.add(d.page_name)

    # ── Semantic Checks ──

    def _check_semantics(self) -> None:
        self._check_role_scopes()
        self._check_content_references()
        self._check_state_machines()
        self._check_events()
        self._check_stories()
        self._check_navigation()
        self._check_api()

    def _check_role_scopes(self) -> None:
        for role in self.program.roles:
            for scope in role.scopes:
                if scope not in self.scope_names:
                    self.errors.add(SemanticError(
                        message=f'Role "{role.name}" references undefined scope "{scope}"',
                        line=role.line,
                    ))

    def _check_content_references(self) -> None:
        for content in self.program.contents:
            for field in content.fields:
                if field.type_expr.references:
                    if field.type_expr.references not in self.content_names:
                        self.errors.add(SemanticError(
                            message=f'Field "{field.name}" in "{content.name}" references '
                                    f'undefined content "{field.type_expr.references}"',
                            line=field.line,
                        ))

            for rule in content.access_rules:
                if rule.scope not in self.scope_names:
                    self.errors.add(SemanticError(
                        message=f'Access rule in "{content.name}" references '
                                f'undefined scope "{rule.scope}"',
                        line=rule.line,
                    ))

    def _check_state_machines(self) -> None:
        for sm in self.program.state_machines:
            if sm.content_name not in self.content_names:
                self.errors.add(SemanticError(
                    message=f'State machine "{sm.machine_name}" references '
                            f'undefined content "{sm.content_name}"',
                    line=sm.line,
                ))

            all_states = set(sm.states)
            for tr in sm.transitions:
                if tr.from_state not in all_states:
                    self.errors.add(SemanticError(
                        message=f'Transition from undefined state "{tr.from_state}" '
                                f'in "{sm.machine_name}"',
                        line=tr.line,
                    ))
                if tr.to_state not in all_states:
                    self.errors.add(SemanticError(
                        message=f'Transition to undefined state "{tr.to_state}" '
                                f'in "{sm.machine_name}"',
                        line=tr.line,
                    ))
                if tr.required_scope not in self.scope_names:
                    self.errors.add(SemanticError(
                        message=f'Transition in "{sm.machine_name}" references '
                                f'undefined scope "{tr.required_scope}"',
                        line=tr.line,
                    ))

    def _check_events(self) -> None:
        for event in self.program.events:
            if event.action and event.action.create_content:
                # The created content name should match a defined Content (by singular)
                # "reorder alert" -> check if "reorder alerts" exists
                found = False
                for cname in self.content_names:
                    singular = cname.rstrip("s") if cname.endswith("s") else cname
                    if singular == event.action.create_content:
                        found = True
                        break
                if not found and event.action.create_content not in self.content_names:
                    self.errors.add(SemanticError(
                        message=f'Event action creates undefined content '
                                f'"{event.action.create_content}"',
                        line=event.action.line,
                    ))

    def _check_stories(self) -> None:
        for story in self.program.stories:
            # Check role exists
            if story.role not in self.role_names:
                # Check partial match (e.g., "warehouse clerk" in roles)
                found = any(story.role in r or r in story.role for r in self.role_names)
                if not found:
                    self.errors.add(SemanticError(
                        message=f'User story references undefined role "{story.role}"',
                        line=story.line,
                    ))

    def _check_navigation(self) -> None:
        nav = self.program.navigation
        if not nav:
            return
        for item in nav.items:
            if item.page_name not in self.page_names:
                self.errors.add(SemanticError(
                    message=f'Navigation item "{item.label}" links to undefined '
                            f'page "{item.page_name}"',
                    line=item.line,
                ))

    def _check_api(self) -> None:
        # API endpoints are validated loosely at this stage
        pass

    # ── Security Invariant Checks ──

    def _check_security_invariants(self) -> None:
        self._check_content_has_access_rules()
        self._check_transitions_have_scopes()
        self._check_no_orphan_states()

    def _check_content_has_access_rules(self) -> None:
        """Every Content must have at least one access rule."""
        for content in self.program.contents:
            if not content.access_rules:
                self.errors.add(SecurityError(
                    message=f'Content "{content.name}" has no access rules. '
                            f'Every Content must declare who can access it.',
                    line=content.line,
                ))

    def _check_transitions_have_scopes(self) -> None:
        """Every State transition must require a Scope."""
        for sm in self.program.state_machines:
            for tr in sm.transitions:
                if not tr.required_scope:
                    self.errors.add(SecurityError(
                        message=f'State transition from "{tr.from_state}" to '
                                f'"{tr.to_state}" in "{sm.machine_name}" has no '
                                f'scope requirement. Every transition must require a scope.',
                        line=tr.line,
                    ))

    def _check_no_orphan_states(self) -> None:
        """Every state should be reachable or be a source of a transition."""
        for sm in self.program.state_machines:
            reachable = {sm.initial_state}
            transition_sources = set()
            transition_targets = set()

            for tr in sm.transitions:
                transition_sources.add(tr.from_state)
                transition_targets.add(tr.to_state)
                if tr.from_state in reachable:
                    reachable.add(tr.to_state)

            # Re-iterate until fixed point
            changed = True
            while changed:
                changed = False
                for tr in sm.transitions:
                    if tr.from_state in reachable and tr.to_state not in reachable:
                        reachable.add(tr.to_state)
                        changed = True

            all_states = set(sm.states)
            orphans = all_states - reachable
            for state in orphans:
                self.errors.add(SecurityError(
                    message=f'State "{state}" in "{sm.machine_name}" is unreachable. '
                            f'All states must be reachable from the initial state.',
                    line=sm.line,
                ))


def analyze(program: Program) -> CompileResult:
    """Run semantic analysis and security invariant checks on a Program AST."""
    analyzer = Analyzer(program)
    return analyzer.analyze()
