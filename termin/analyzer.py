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


# ── Fuzzy matching ──

def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


def _fuzzy_match(name: str, candidates: set[str], max_distance: int = 2) -> str | None:
    """Find the closest match to `name` in `candidates` within edit distance threshold."""
    best = None
    best_dist = max_distance + 1
    for cand in sorted(candidates):
        d = _levenshtein(name.lower(), cand.lower())
        if d < best_dist and d > 0:
            best = cand
            best_dist = d
    return best if best_dist <= max_distance else None


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

    def _resolve_content_name_in_accesses(self, ref: str, compute) -> bool:
        """Check if a content reference (possibly singular) is in the Compute's Accesses list."""
        for acc in compute.accesses:
            # Direct match
            if ref == acc:
                return True
            # Singular match: "completion" matches "completions"
            if ref + "s" == acc or ref + "es" == acc:
                return True
            # The access item's singular matches the ref
            resolved = self._find_content_by_name(acc)
            if resolved and resolved.singular == ref:
                return True
        return False

    def _find_content_by_name(self, name: str):
        """Find a Content node by name or singular."""
        for c in self.program.contents:
            if c.name == name or c.singular == name:
                return c
        return None

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
        self._check_dependent_values()

    def _check_role_aliases(self) -> None:
        role_names_lower = {r.lower() for r in self.role_names}
        for alias in self.program.role_aliases:
            # Check that the alias target role exists
            if alias.full_name.lower() not in role_names_lower:
                suggestion = _fuzzy_match(alias.full_name, self.role_names)
                self.errors.add(SemanticError(
                    message=f'Role alias "{alias.short_name}" targets undefined '
                            f'role "{alias.full_name}"',
                    line=alias.line,
                    code="TERMIN-S001",
                    suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                ))

    def _check_role_scopes(self) -> None:
        for role in self.program.roles:
            for scope in role.scopes:
                if scope not in self.scope_names:
                    suggestion = _fuzzy_match(scope, self.scope_names)
                    self.errors.add(SemanticError(
                        message=f'Role "{role.name}" references undefined scope "{scope}"',
                        line=role.line,
                        code="TERMIN-S002",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))

    def _check_content_references(self) -> None:
        for content in self.program.contents:
            for field in content.fields:
                if field.type_expr.references:
                    if field.type_expr.references not in self.content_names:
                        suggestion = _fuzzy_match(field.type_expr.references, self.content_names)
                        self.errors.add(SemanticError(
                            message=f'Field "{field.name}" in "{content.name}" references '
                                    f'undefined content "{field.type_expr.references}"',
                            line=field.line,
                            code="TERMIN-S003",
                            suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                        ))

            for rule in content.access_rules:
                if rule.scope not in self.scope_names:
                    suggestion = _fuzzy_match(rule.scope, self.scope_names)
                    self.errors.add(SemanticError(
                        message=f'Access rule in "{content.name}" references '
                                f'undefined scope "{rule.scope}"',
                        line=rule.line,
                        code="TERMIN-S004",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))

            # Validate audit level
            if content.audit not in ("actions", "debug", "none"):
                self.errors.add(SemanticError(
                    message=f'Content "{content.name}" has invalid audit level '
                            f'"{content.audit}". Must be one of: actions, debug, none',
                    line=content.line,
                    code="TERMIN-S005",
                ))

    def _check_state_machines(self) -> None:
        for sm in self.program.state_machines:
            # Allow state machines on Content, Channel, or Compute names
            all_targets = self.content_names | self.channel_names | self.compute_names
            if (sm.content_name not in all_targets):
                suggestion = _fuzzy_match(sm.content_name, all_targets)
                self.errors.add(SemanticError(
                    message=f'State machine "{sm.machine_name}" references '
                            f'undefined content "{sm.content_name}"',
                    line=sm.line,
                    code="TERMIN-S006",
                    suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                ))

            all_states = set(sm.states)
            for tr in sm.transitions:
                if tr.from_state not in all_states:
                    suggestion = _fuzzy_match(tr.from_state, all_states)
                    self.errors.add(SemanticError(
                        message=f'Transition from undefined state "{tr.from_state}" '
                                f'in "{sm.machine_name}"',
                        line=tr.line,
                        code="TERMIN-S007",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))
                if tr.to_state not in all_states:
                    suggestion = _fuzzy_match(tr.to_state, all_states)
                    self.errors.add(SemanticError(
                        message=f'Transition to undefined state "{tr.to_state}" '
                                f'in "{sm.machine_name}"',
                        line=tr.line,
                        code="TERMIN-S008",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))
                if tr.required_scope not in self.scope_names:
                    suggestion = _fuzzy_match(tr.required_scope, self.scope_names)
                    self.errors.add(SemanticError(
                        message=f'Transition in "{sm.machine_name}" references '
                                f'undefined scope "{tr.required_scope}"',
                        line=tr.line,
                        code="TERMIN-S009",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))

    def _check_events(self) -> None:
        for event in self.program.events:
            if event.action and event.action.create_content:
                found = False
                for cname in self.content_names:
                    singular = cname.rstrip("s") if cname.endswith("s") else cname
                    if singular == event.action.create_content:
                        found = True
                        break
                if not found and event.action.create_content not in self.content_names:
                    suggestion = _fuzzy_match(event.action.create_content, self.content_names)
                    self.errors.add(SemanticError(
                        message=f'Event action creates undefined content '
                                f'"{event.action.create_content}"',
                        line=event.action.line,
                        code="TERMIN-S010",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))

    def _check_stories(self) -> None:
        role_names_lower = {r.lower() for r in self.role_names}
        for story in self.program.stories:
            if story.role.lower() == "anonymous":
                continue
            role_lower = story.role.lower()
            if role_lower in self.role_alias_map:
                role_lower = self.role_alias_map[role_lower]
            if role_lower not in role_names_lower:
                found = any(role_lower in r.lower() or r.lower() in role_lower
                            for r in self.role_names)
                if not found:
                    suggestion = _fuzzy_match(story.role, self.role_names)
                    self.errors.add(SemanticError(
                        message=f'User story references undefined role "{story.role}"',
                        line=story.line,
                        code="TERMIN-S011",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))

    def _check_navigation(self) -> None:
        nav = self.program.navigation
        if not nav:
            return
        for item in nav.items:
            if item.page_name not in self.page_names:
                suggestion = _fuzzy_match(item.page_name, self.page_names)
                self.errors.add(SemanticError(
                    message=f'Navigation item "{item.label}" links to undefined '
                            f'page "{item.page_name}"',
                    line=item.line,
                    code="TERMIN-S012",
                    suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                ))

    def _check_api(self) -> None:
        # D-11: 'api' is a reserved page slug — auto-CRUD routes live at /api/v1/
        for page in self.page_names:
            slug = page.lower().replace(" ", "_")
            if slug == "api":
                # Find the line number for the page definition
                line = 0
                for story in self.program.stories:
                    for d in story.directives:
                        if isinstance(d, ShowPage) and d.page_name == page:
                            line = d.line or story.line
                            break
                self.errors.add(SemanticError(
                    message=f'Page slug "api" is reserved for auto-generated REST API '
                            f'routes (/api/v1/...). Choose a different page name.',
                    line=line,
                    code="TERMIN-S032",
                ))

    # ── Security Invariant Checks ──

    def _check_security_invariants(self) -> None:
        self._check_content_has_access_rules()
        self._check_transitions_have_scopes()
        self._check_no_orphan_states()
        self._check_compute_has_access()
        self._check_channel_has_auth()
        self._check_boundary_scope_restriction()
        self._check_confidentiality_scopes()

    def _check_content_has_access_rules(self) -> None:
        """Every Content must have at least one access rule."""
        for content in self.program.contents:
            if not content.access_rules:
                self.errors.add(SecurityError(
                    message=f'Content "{content.name}" has no access rules. '
                            f'Every Content must declare who can access it.',
                    line=content.line,
                    code="TERMIN-X001",
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
                        code="TERMIN-X002",
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
                    code="TERMIN-X003",
                ))


    # ── Compute Checks ──

    def _check_computes(self) -> None:
        valid_shapes = {"transform", "reduce", "expand", "correlate", "route"}
        llm_providers = {"llm", "ai-agent"}
        for compute in self.program.computes:
            has_field_wiring = bool(compute.input_fields or compute.output_fields or compute.output_creates)
            has_accesses = bool(compute.accesses)
            is_llm_provider = compute.provider in llm_providers

            # Shape is required for CEL computes, optional for LLM/agent providers with field wiring
            if compute.shape and compute.shape not in valid_shapes:
                self.errors.add(SemanticError(
                    message=f'Compute "{compute.name}" has invalid shape "{compute.shape}". '
                            f'Valid shapes: {", ".join(sorted(valid_shapes))}',
                    line=compute.line,
                    code="TERMIN-S013",
                ))
            elif not compute.shape and not is_llm_provider and not has_field_wiring:
                self.errors.add(SemanticError(
                    message=f'Compute "{compute.name}" has no shape and no field wiring. '
                            f'Add a Transform/Reduce/Expand/Correlate/Route shape, or use '
                            f'Input from field / Output into field with Provider is "llm".',
                    line=compute.line,
                    code="TERMIN-S014",
                ))

            # Validate Accesses references
            for acc in compute.accesses:
                if not self._resolve_content_name(acc):
                    suggestion = _fuzzy_match(acc, self.content_names)
                    self.errors.add(SemanticError(
                        message=f'Compute "{compute.name}" declares access to undefined '
                                f'content "{acc}"',
                        line=compute.line,
                        code="TERMIN-S015",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))

            # Validate input/output field references against Accesses
            for content_ref, field_name in compute.input_fields:
                if has_accesses and not self._resolve_content_name_in_accesses(content_ref, compute):
                    self.errors.add(SemanticError(
                        message=f'Compute "{compute.name}": Input field "{content_ref}.{field_name}" '
                                f'references content not in Accesses declaration',
                        line=compute.line,
                        code="TERMIN-S016",
                    ))
            for content_ref, field_name in compute.output_fields:
                if has_accesses and not self._resolve_content_name_in_accesses(content_ref, compute):
                    self.errors.add(SemanticError(
                        message=f'Compute "{compute.name}": Output field "{content_ref}.{field_name}" '
                                f'references content not in Accesses declaration',
                        line=compute.line,
                        code="TERMIN-S016",
                    ))

            # Legacy shape-based input/output validation
            for inp in compute.inputs:
                if not self._resolve_content_name(inp):
                    suggestion = _fuzzy_match(inp, self.content_names)
                    self.errors.add(SemanticError(
                        message=f'Compute "{compute.name}" references undefined '
                                f'input content "{inp}"',
                        line=compute.line,
                        code="TERMIN-S017",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))
            for out in compute.outputs:
                if not self._resolve_content_name(out):
                    suggestion = _fuzzy_match(out, self.content_names)
                    self.errors.add(SemanticError(
                        message=f'Compute "{compute.name}" references undefined '
                                f'output content "{out}"',
                        line=compute.line,
                        code="TERMIN-S017",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))
            if compute.access_scope and compute.access_scope not in self.scope_names:
                suggestion = _fuzzy_match(compute.access_scope, self.scope_names)
                self.errors.add(SemanticError(
                    message=f'Compute "{compute.name}" references undefined '
                            f'scope "{compute.access_scope}"',
                    line=compute.line,
                    code="TERMIN-S018",
                    suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
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
                    code="TERMIN-X004",
                ))
            if compute.access_role:
                if (compute.access_role.lower() not in role_names_lower
                        and compute.access_role.lower() != "anonymous"):
                    suggestion = _fuzzy_match(compute.access_role, self.role_names)
                    self.errors.add(SemanticError(
                        message=f'Compute "{compute.name}" references undefined '
                                f'role "{compute.access_role}"',
                        line=compute.line,
                        code="TERMIN-S019",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))

    # ── Channel Checks ──

    VALID_DIRECTIONS = {"inbound", "outbound", "bidirectional", "internal"}
    VALID_DELIVERIES = {"realtime", "reliable", "batch", "auto"}

    def _check_channels(self) -> None:
        for channel in self.program.channels:
            if channel.carries and not self._resolve_content_name(channel.carries):
                suggestion = _fuzzy_match(channel.carries, self.content_names)
                self.errors.add(SemanticError(
                    message=f'Channel "{channel.name}" carries undefined '
                            f'content "{channel.carries}"',
                    line=channel.line,
                    code="TERMIN-S020",
                    suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                ))
            if channel.direction and channel.direction not in self.VALID_DIRECTIONS:
                self.errors.add(SemanticError(
                    message=f'Channel "{channel.name}" has invalid direction "{channel.direction}". '
                            f'Valid directions: {", ".join(sorted(self.VALID_DIRECTIONS))}',
                    line=channel.line,
                    code="TERMIN-S021",
                ))
            if channel.delivery and channel.delivery not in self.VALID_DELIVERIES:
                self.errors.add(SemanticError(
                    message=f'Channel "{channel.name}" has invalid delivery "{channel.delivery}". '
                            f'Valid deliveries: {", ".join(sorted(self.VALID_DELIVERIES))}',
                    line=channel.line,
                    code="TERMIN-S022",
                ))
            for req in channel.requirements:
                if req.scope not in self.scope_names:
                    suggestion = _fuzzy_match(req.scope, self.scope_names)
                    self.errors.add(SemanticError(
                        message=f'Channel "{channel.name}" references undefined '
                                f'scope "{req.scope}"',
                        line=req.line,
                        code="TERMIN-S023",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))
            for action in channel.actions:
                for scope in action.required_scopes:
                    if scope not in self.scope_names:
                        suggestion = _fuzzy_match(scope, self.scope_names)
                        self.errors.add(SemanticError(
                            message=f'Action "{action.name}" on Channel "{channel.name}" '
                                    f'references undefined scope "{scope}"',
                            line=action.line,
                            code="TERMIN-S024",
                            suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                        ))
            if not channel.carries and not channel.actions:
                self.errors.add(SemanticError(
                    message=f'Channel "{channel.name}" has no data (Carries) and no Actions. '
                            f'A Channel must carry content, expose actions, or both.',
                    line=channel.line,
                    code="TERMIN-S025",
                ))

    def _is_channel_internal(self, channel) -> bool:
        """Check if a channel is internal."""
        return channel.direction == "internal"

    def _check_channel_has_auth(self) -> None:
        """Every non-internal Channel must have auth requirements."""
        for channel in self.program.channels:
            if self._is_channel_internal(channel):
                continue
            has_channel_reqs = bool(channel.requirements)
            has_action_scopes = any(act.required_scopes for act in channel.actions)
            if not has_channel_reqs and not has_action_scopes:
                self.errors.add(SecurityError(
                    message=f'Channel "{channel.name}" has no authentication requirements. '
                            f'Every external Channel must declare required scopes.',
                    line=channel.line,
                    code="TERMIN-X005",
                ))

    # ── Boundary Checks ──

    def _check_boundaries(self) -> None:
        # Track which boundary each content type belongs to
        content_to_boundary: dict[str, str] = {}

        for boundary in self.program.boundaries:
            for item in boundary.contains:
                if not self._resolve_content_name(item) and item not in self.boundary_names:
                    all_names = self.content_names | self.boundary_names
                    suggestion = _fuzzy_match(item, all_names)
                    self.errors.add(SemanticError(
                        message=f'Boundary "{boundary.name}" contains undefined '
                                f'item "{item}"',
                        line=boundary.line,
                        code="TERMIN-S026",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))
                elif item in content_to_boundary:
                    self.errors.add(SemanticError(
                        message=f'Content "{item}" is in both boundary '
                                f'"{content_to_boundary[item]}" and '
                                f'boundary "{boundary.name}". '
                                f'Content can only belong to one boundary.',
                        line=boundary.line,
                        code="TERMIN-S030",
                    ))
                else:
                    content_to_boundary[item] = boundary.name

    def _check_dependent_values(self) -> None:
        """D-19: Validate dependent value (When clause) declarations."""
        for content in self.program.contents:
            field_names = {f.name for f in content.fields}
            # Build enum field map for exhaustiveness check
            enum_fields = {}
            for f in content.fields:
                if f.type_expr.enum_values:
                    enum_fields[f.name] = set(f.type_expr.enum_values)

            # Track When clauses per (condition_field, target_field) for exhaustiveness
            when_coverage: dict[str, set[str]] = {}  # condition_field -> set of covered values

            for dv in content.dependent_values:
                # Check that the target field exists
                if dv.field not in field_names:
                    suggestion = _fuzzy_match(dv.field, field_names)
                    self.errors.add(SemanticError(
                        message=f'Dependent value in "{content.name}" references '
                                f'undefined field "{dv.field}"',
                        line=dv.line,
                        code="TERMIN-S029",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))

                # Track coverage for exhaustiveness (simple equality on enum field)
                if dv.when_expr:
                    import re
                    # Match simple patterns like: field == "value"
                    m = re.match(r'(\w+)\s*==\s*"([^"]*)"', dv.when_expr)
                    if m:
                        cond_field = m.group(1)
                        cond_value = m.group(2)
                        key = f"{cond_field}:{dv.field}"
                        when_coverage.setdefault(key, set()).add(cond_value)

            # Exhaustiveness warning: if When clauses reference an enum field
            # but don't cover all values
            for key, covered_values in when_coverage.items():
                cond_field, target_field = key.split(":", 1)
                if cond_field in enum_fields:
                    all_values = enum_fields[cond_field]
                    missing = all_values - covered_values
                    if missing and len(covered_values) > 0:
                        # Warning, not error
                        self.errors.add(SemanticError(
                            message=f'Content "{content.name}": When clauses on '
                                    f'"{cond_field}" for field "{target_field}" '
                                    f'do not cover all enum values. Missing: '
                                    f'{", ".join(sorted(missing))}',
                            line=content.line,
                            code="TERMIN-W001",
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
                suggestion = _fuzzy_match(handler.source, all_primitive_names)
                self.errors.add(SemanticError(
                    message=f'Error handler references undefined primitive "{handler.source}"',
                    line=handler.line,
                    code="TERMIN-S027",
                    suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                ))

    def _check_boundary_scope_restriction(self) -> None:
        """Boundary scope restrictions must use valid scopes."""
        for boundary in self.program.boundaries:
            if boundary.identity_mode == "restrict":
                for scope in boundary.identity_scopes:
                    if scope not in self.scope_names:
                        suggestion = _fuzzy_match(scope, self.scope_names)
                        self.errors.add(SecurityError(
                            message=f'Boundary "{boundary.name}" restricts to undefined '
                                    f'scope "{scope}"',
                            line=boundary.line,
                            code="TERMIN-X006",
                            suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                        ))

    # ── Confidentiality Checks ──

    def _check_confidentiality_scopes(self) -> None:
        """Validate confidentiality scope declarations."""
        for content in self.program.contents:
            for scope in content.confidentiality_scopes:
                if scope not in self.scope_names:
                    suggestion = _fuzzy_match(scope, self.scope_names)
                    self.errors.add(SecurityError(
                        message=f'Content "{content.name}" scoped to undefined '
                                f'scope "{scope}"',
                        line=content.line,
                        code="TERMIN-X007",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))
            for field in content.fields:
                for scope in field.type_expr.confidentiality_scopes:
                    if scope not in self.scope_names:
                        suggestion = _fuzzy_match(scope, self.scope_names)
                        self.errors.add(SecurityError(
                            message=f'Field "{field.name}" in "{content.name}" has '
                                    f'confidentiality scope "{scope}" which is not declared',
                            line=field.line,
                            code="TERMIN-X008",
                            suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                        ))

        for compute in self.program.computes:
            for scope in compute.required_confidentiality_scopes:
                if scope not in self.scope_names:
                    suggestion = _fuzzy_match(scope, self.scope_names)
                    self.errors.add(SecurityError(
                        message=f'Compute "{compute.name}" requires undefined '
                                f'confidentiality scope "{scope}"',
                        line=compute.line,
                        code="TERMIN-X009",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))
            if compute.output_confidentiality:
                if compute.output_confidentiality not in self.scope_names:
                    suggestion = _fuzzy_match(compute.output_confidentiality, self.scope_names)
                    self.errors.add(SecurityError(
                        message=f'Compute "{compute.name}" output confidentiality '
                                f'scope "{compute.output_confidentiality}" is not declared',
                        line=compute.line,
                        code="TERMIN-X010",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))
            # Service identity must have output_confidentiality or no reclassification
            if compute.identity_mode == "service" and compute.output_confidentiality:
                # Output scope must be in the union of Requires + Output
                # (this is validated — the service identity auto-provisions these)
                pass
            # Identity mode validation
            if compute.identity_mode not in ("delegate", "service"):
                self.errors.add(SemanticError(
                    message=f'Compute "{compute.name}" has invalid identity mode '
                            f'"{compute.identity_mode}". Must be "delegate" or "service".',
                    line=compute.line,
                    code="TERMIN-S028",
                ))


def analyze(program: Program) -> CompileResult:
    """Run semantic analysis and security invariant checks on a Program AST."""
    analyzer = Analyzer(program)
    return analyzer.analyze()
