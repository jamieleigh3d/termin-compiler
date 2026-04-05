"""Semantic analysis and security invariant checking for Termin AST.

Two-pass analysis:
1. Semantic analysis: resolve references, validate cross-links
2. Security invariant checks: enforce the security properties that make Termin's argument
"""

from .ast_nodes import (
    Program, Content, StateMachine, EventRule, UserStory, ShowPage,
    DisplayTable, AcceptInput, SubscribeTo, ShowRelated, AllowFilter,
    AllowSearch, ShowChart, DisplayAggregation,
    ComputeNode, ChannelDecl, BoundaryDecl, RoleAlias,
    ErrorHandler,
)
from .errors import SemanticError, SecurityError, CompileResult


class Analyzer:
    def __init__(self, program: Program):
        self.program = program
        self.errors = CompileResult()

        # Symbol tables built during analysis
        self.content_names: set[str] = set()
        self.content_singulars: set[str] = set()
        self.scope_names: set[str] = set()
        self.role_names: set[str] = set()
        self.page_names: set[str] = set()
        self.content_field_names: dict[str, set[str]] = {}  # content_name -> {field_names}
        self.compute_names: set[str] = set()
        self.channel_names: set[str] = set()
        self.boundary_names: set[str] = set()
        self.state_machine_names: set[str] = set()
        self.role_alias_map: dict[str, str] = {}  # short_name -> full_name

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
            self.content_singulars.add(content.singular)
            self.content_field_names[content.name] = {f.name for f in content.fields}

        for compute in p.computes:
            self.compute_names.add(compute.name)

        for channel in p.channels:
            self.channel_names.add(channel.name)

        for boundary in p.boundaries:
            self.boundary_names.add(boundary.name)

        for sm in p.state_machines:
            self.state_machine_names.add(sm.machine_name)

        for alias in p.role_aliases:
            self.role_alias_map[alias.short_name.lower()] = alias.full_name.lower()

        for story in p.stories:
            for d in story.directives:
                if isinstance(d, ShowPage):
                    self.page_names.add(d.page_name)

    # ── Semantic Checks ──

    # Built-in type names that don't require Content declarations
    BUILTIN_TYPES = {"text", "userprofile", "role", "integer", "real", "timestamp"}

    def _resolve_content_name(self, name: str) -> bool:
        """Check if a name matches a content name, singular form, or built-in type."""
        if name.lower() in self.BUILTIN_TYPES:
            return True
        if name in self.content_names:
            return True
        if name in self.content_singulars:
            return True
        # Try plural form
        if name + "s" in self.content_names:
            return True
        return False

    def _check_semantics(self) -> None:
        self._check_role_aliases()
        self._check_role_scopes()
        self._check_content_references()
        self._check_state_machines()
        self._check_events()
        self._check_stories()
        self._check_navigation()
        self._check_api()
        self._check_computes()
        self._check_channels()
        self._check_boundaries()
        self._check_error_handlers()

    def _check_role_aliases(self) -> None:
        role_names_lower = {r.lower() for r in self.role_names}
        for alias in self.program.role_aliases:
            # Check that the alias target role exists
            if alias.full_name.lower() not in role_names_lower:
                self.errors.add(SemanticError(
                    message=f'Role alias "{alias.short_name}" targets undefined '
                            f'role "{alias.full_name}"',
                    line=alias.line,
                ))

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
            # Allow state machines on Content, Channel, or Compute names
            if (sm.content_name not in self.content_names
                    and sm.content_name not in self.channel_names
                    and sm.content_name not in self.compute_names):
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
        role_names_lower = {r.lower() for r in self.role_names}
        for story in self.program.stories:
            # "anonymous" is a built-in role — no definition required
            if story.role.lower() == "anonymous":
                continue
            # Check role exists (case-insensitive)
            role_lower = story.role.lower()
            # Resolve alias if present
            if role_lower in self.role_alias_map:
                role_lower = self.role_alias_map[role_lower]
            if role_lower not in role_names_lower:
                # Check partial match (e.g., "warehouse clerk" in roles)
                found = any(role_lower in r.lower() or r.lower() in role_lower
                            for r in self.role_names)
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
        self._check_compute_has_access()
        self._check_channel_has_auth()
        self._check_boundary_scope_restriction()

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


    # ── Compute Checks ──

    def _check_computes(self) -> None:
        valid_shapes = {"transform", "reduce", "expand", "correlate", "route"}
        for compute in self.program.computes:
            if not compute.shape or compute.shape not in valid_shapes:
                self.errors.add(SemanticError(
                    message=f'Compute "{compute.name}" has invalid shape "{compute.shape}". '
                            f'Valid shapes: {", ".join(sorted(valid_shapes))}',
                    line=compute.line,
                ))
            for inp in compute.inputs:
                if not self._resolve_content_name(inp):
                    self.errors.add(SemanticError(
                        message=f'Compute "{compute.name}" references undefined '
                                f'input content "{inp}"',
                        line=compute.line,
                    ))
            for out in compute.outputs:
                if not self._resolve_content_name(out):
                    self.errors.add(SemanticError(
                        message=f'Compute "{compute.name}" references undefined '
                                f'output content "{out}"',
                        line=compute.line,
                    ))
            if compute.access_scope and compute.access_scope not in self.scope_names:
                self.errors.add(SemanticError(
                    message=f'Compute "{compute.name}" references undefined '
                            f'scope "{compute.access_scope}"',
                    line=compute.line,
                ))

    def _check_compute_has_access(self) -> None:
        """Every Compute must have an access rule."""
        role_names_lower = {r.lower() for r in self.role_names}
        for compute in self.program.computes:
            if not compute.access_scope and not compute.access_role:
                self.errors.add(SecurityError(
                    message=f'Compute "{compute.name}" has no access rule. '
                            f'Every Compute must declare who can execute it.',
                    line=compute.line,
                ))
            if compute.access_role:
                if (compute.access_role.lower() not in role_names_lower
                        and compute.access_role.lower() != "anonymous"):
                    self.errors.add(SemanticError(
                        message=f'Compute "{compute.name}" references undefined '
                                f'role "{compute.access_role}"',
                        line=compute.line,
                    ))

    # ── Channel Checks ──

    VALID_DIRECTIONS = {"inbound", "outbound", "bidirectional", "internal"}
    VALID_DELIVERIES = {"realtime", "reliable", "batch", "auto"}

    def _check_channels(self) -> None:
        for channel in self.program.channels:
            if channel.carries and not self._resolve_content_name(channel.carries):
                self.errors.add(SemanticError(
                    message=f'Channel "{channel.name}" carries undefined '
                            f'content "{channel.carries}"',
                    line=channel.line,
                ))
            # v2: validate direction and delivery
            if channel.direction and channel.direction not in self.VALID_DIRECTIONS:
                self.errors.add(SemanticError(
                    message=f'Channel "{channel.name}" has invalid direction "{channel.direction}". '
                            f'Valid directions: {", ".join(sorted(self.VALID_DIRECTIONS))}',
                    line=channel.line,
                ))
            if channel.delivery and channel.delivery not in self.VALID_DELIVERIES:
                self.errors.add(SemanticError(
                    message=f'Channel "{channel.name}" has invalid delivery "{channel.delivery}". '
                            f'Valid deliveries: {", ".join(sorted(self.VALID_DELIVERIES))}',
                    line=channel.line,
                ))
            for req in channel.requirements:
                if req.scope not in self.scope_names:
                    self.errors.add(SemanticError(
                        message=f'Channel "{channel.name}" references undefined '
                                f'scope "{req.scope}"',
                        line=req.line,
                    ))

    def _is_channel_internal(self, channel) -> bool:
        """Check if a channel is internal."""
        return channel.direction == "internal"

    def _check_channel_has_auth(self) -> None:
        """Every non-internal Channel must have auth requirements."""
        for channel in self.program.channels:
            if not self._is_channel_internal(channel) and not channel.requirements:
                self.errors.add(SecurityError(
                    message=f'Channel "{channel.name}" has no authentication requirements. '
                            f'Every external Channel must declare required scopes.',
                    line=channel.line,
                ))

    # ── Boundary Checks ──

    def _check_boundaries(self) -> None:
        for boundary in self.program.boundaries:
            for item in boundary.contains:
                if not self._resolve_content_name(item) and item not in self.boundary_names:
                    self.errors.add(SemanticError(
                        message=f'Boundary "{boundary.name}" contains undefined '
                                f'item "{item}"',
                        line=boundary.line,
                    ))

    def _check_error_handlers(self) -> None:
        """Validate that error handler sources reference defined primitives."""
        all_primitive_names = (
            self.content_names | self.compute_names | self.channel_names
            | self.state_machine_names | self.boundary_names
        )
        for handler in self.program.error_handlers:
            if handler.is_catch_all:
                continue
            if handler.source and handler.source not in all_primitive_names:
                self.errors.add(SemanticError(
                    message=f'Error handler references undefined primitive "{handler.source}"',
                    line=handler.line,
                ))

    def _check_boundary_scope_restriction(self) -> None:
        """Boundary scope restrictions must use valid scopes."""
        for boundary in self.program.boundaries:
            if boundary.identity_mode == "restrict":
                for scope in boundary.identity_scopes:
                    if scope not in self.scope_names:
                        self.errors.add(SecurityError(
                            message=f'Boundary "{boundary.name}" restricts to undefined '
                                    f'scope "{scope}"',
                            line=boundary.line,
                        ))


def analyze(program: Program) -> CompileResult:
    """Run semantic analysis and security invariant checks on a Program AST."""
    analyzer = Analyzer(program)
    return analyzer.analyze()
