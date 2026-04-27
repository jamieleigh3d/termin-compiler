# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Semantic analysis and security invariant checking for Termin AST.

Two-pass analysis:
1. Semantic analysis: resolve references, validate cross-links
2. Security invariant checks: enforce the security properties that make Termin's argument
"""

import re

from .ast_nodes import (
    Program, Content, StateMachine, EventRule, UserStory, ShowPage,
    DisplayTable, AcceptInput, SubscribeTo, ShowRelated, AllowFilter,
    AllowSearch, AllowInlineEdit, ShowChart, DisplayAggregation,
    ComputeNode, ChannelDecl, BoundaryDecl, RoleAlias,
    ErrorHandler, ActionButtonDef,
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
        self._check_cascade_graph()
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
        self._check_row_action_access_rules()
        self._check_inline_editing()

    def _check_row_action_access_rules(self) -> None:
        """Row action buttons of kind=delete/edit require the governing
        content to declare the matching access rule (can delete / can
        update). Otherwise the button has no resolvable required_scope
        and any click would be unreachable.

        TERMIN-S020: Delete action without `can delete` rule.
        TERMIN-S021: Edit action without `can update` rule.
        """
        # Maps action kind -> (required access verb, error code).
        rules = {
            "delete": ("delete", "TERMIN-S020"),
            "edit":   ("update", "TERMIN-S021"),
        }
        for story in self.program.stories:
            current_table_content_name: str | None = None
            for d in story.directives:
                if isinstance(d, DisplayTable):
                    current_table_content_name = d.content_name
                elif isinstance(d, ActionButtonDef) and d.kind in rules:
                    if not current_table_content_name:
                        # Row action with no preceding table — ungrounded.
                        # Fall through silently; lowering handles it.
                        continue
                    content = self._find_content_by_name(
                        current_table_content_name)
                    if content is None:
                        continue  # undefined content caught elsewhere
                    verb, code = rules[d.kind]
                    has_rule = any(
                        verb in rule.verbs for rule in content.access_rules)
                    if not has_rule:
                        self.errors.add(SemanticError(
                            message=(
                                f'{d.kind.capitalize()} action "{d.label}" on '
                                f'"{content.name}" has no matching access rule '
                                f'— add \'Anyone with "<scope>" can {verb} '
                                f'{content.name}\' to the Content block.'
                            ),
                            line=d.line,
                            code=code,
                        ))

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
                te = field.type_expr
                seen_modes = getattr(te, "_cascade_modes_seen", ())

                # v0.9 TERMIN-S041: declaring both `cascade on delete`
                # AND `restrict on delete` on the same field is a
                # parse-detectable conflict.
                if len(set(seen_modes)) > 1:
                    self.errors.add(SemanticError(
                        message=(
                            f'Field "{field.name}" in "{content.name}" declares '
                            f'both "cascade on delete" and "restrict on delete". '
                            f'Choose exactly one.'
                        ),
                        line=field.line,
                        code="TERMIN-S041",
                    ))

                # v0.9 TERMIN-S040: cascade declarations only apply
                # to reference fields. Cascade-on-non-reference is
                # rejected even when the field would otherwise be
                # well-formed.
                if te.cascade_mode and te.base_type != "reference":
                    self.errors.add(SemanticError(
                        message=(
                            f'"{te.cascade_mode} on delete" only applies to '
                            f'reference fields. Field "{field.name}" in '
                            f'"{content.name}" has type "{te.base_type}". '
                            f'Cascade is only meaningful for fields declared '
                            f'with "references <content>".'
                        ),
                        line=field.line,
                        code="TERMIN-S040",
                    ))

                if te.references:
                    # v0.9 TERMIN-S039: every reference field MUST
                    # declare cascade behavior. Bare `references X` is
                    # not allowed; the BRD §6.2 audit-over-authorship
                    # tenet requires deletion blast radius to be
                    # visible in source.
                    if te.cascade_mode is None:
                        self.errors.add(SemanticError(
                            message=(
                                f'Reference field "{field.name}" in '
                                f'"{content.name}" must declare cascade '
                                f'behavior. Add ", cascade on delete" or '
                                f'", restrict on delete" to the line. '
                                f'"cascade on delete" → when the parent is '
                                f'deleted, this record is deleted too. '
                                f'"restrict on delete" → refuse parent '
                                f'deletion while any record references it.'
                            ),
                            line=field.line,
                            code="TERMIN-S039",
                        ))

                    if te.references not in self.content_names:
                        suggestion = _fuzzy_match(te.references, self.content_names)
                        self.errors.add(SemanticError(
                            message=f'Field "{field.name}" in "{content.name}" references '
                                    f'undefined content "{te.references}"',
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

    def _check_cascade_graph(self) -> None:
        """v0.9 BRD §6.2: enforce structural soundness of the cascade
        graph.

        TERMIN-S042: A content cannot simultaneously be the target of
        any `cascade on delete` reference AND the target of any
        `restrict on delete` reference. That combination is a runtime
        deadlock — deleting the upstream parent cascade-deletes this
        content, which then fails because a downstream restrict
        prevents it. Whole transaction rolls back; the upstream
        record can never be deleted while restrict-protectors exist.
        Better to surface the structural defect at compile time than
        ship the deadlock.

        TERMIN-S043: Multi-content cascade cycles are rejected. Any
        cascade cycle that doesn't terminate in a self-reference
        produces version-dependent behavior in SQLite and is not
        portable to other backends. Self-references are allowed
        (tree-delete is a useful pattern).
        """
        # cascade_in[child] = [(parent, field, line)] — child is
        # cascade-deleted when parent is deleted (because child has a
        # field with `references parent, cascade on delete`).
        # restrict_in[parent] = [(protector, field, line)] — parent's
        # deletion is restricted by protector (because protector has a
        # field with `references parent, restrict on delete`).
        cascade_in: dict[str, list[tuple[str, str, int]]] = {}
        restrict_in: dict[str, list[tuple[str, str, int]]] = {}
        for c in self.program.contents:
            for f in c.fields:
                te = f.type_expr
                if not te.references or te.references not in self.content_names:
                    continue
                if te.cascade_mode == "cascade":
                    cascade_in.setdefault(c.name, []).append(
                        (te.references, f.name, f.line))
                elif te.cascade_mode == "restrict":
                    restrict_in.setdefault(te.references, []).append(
                        (c.name, f.name, f.line))

        # S042: deadlock if any content is BOTH cascade-deleted from
        # above (parent cascades to it) AND has its own deletion
        # restrict-protected from below (some other content
        # restrict-references it).
        for target in sorted(set(cascade_in.keys()) & set(restrict_in.keys())):
            cascade_edges = cascade_in[target]
            restrict_edges = restrict_in[target]
            cascade_descs = "; ".join(
                f'deleted when "{parent}" is deleted (cascade via '
                f'{target}.{fn}, line {ln})'
                for (parent, fn, ln) in cascade_edges)
            restrict_descs = "; ".join(
                f'protected by "{prot}" (restrict via {prot}.{fn}, '
                f'line {ln})'
                for (prot, fn, ln) in restrict_edges)
            min_line = min(
                ln for _, _, ln in cascade_edges + restrict_edges)
            self.errors.add(SemanticError(
                message=(
                    f'Transitive cascade-restrict deadlock involving '
                    f'"{target}". "{target}" is {cascade_descs}, AND '
                    f'"{target}" is {restrict_descs}. Deleting the '
                    f'upstream parent would cascade-delete "{target}", '
                    f'which would then fail because of the restrict-'
                    f'protector(s), aborting the entire transaction. '
                    f'Resolve by changing the cascade edge to "restrict '
                    f'on delete" (require explicit cleanup of '
                    f'"{target}" first) OR changing the restrict edge '
                    f'to "cascade on delete" (let the cascade propagate '
                    f'all the way through).'
                ),
                line=min_line,
                code="TERMIN-S042",
            ))

        # S043: multi-content cascade cycle. Build the cascade-only
        # subgraph where edge P → C means "deleting P cascade-deletes
        # C." Self-loops are allowed (skip them). DFS from each node;
        # if we revisit a node already on the current stack and the
        # back-edge isn't a self-loop, that's a cycle.
        # cascade_edges_out[parent] = list of (child, field, line)
        cascade_edges_out: dict[str, list[tuple[str, str, str, int]]] = {}
        for c in self.program.contents:
            for f in c.fields:
                te = f.type_expr
                if (te.references and te.cascade_mode == "cascade"
                        and te.references in self.content_names):
                    parent = te.references
                    child = c.name
                    if parent == child:
                        continue  # self-cascade explicitly allowed
                    cascade_edges_out.setdefault(parent, []).append(
                        (child, c.name, f.name, f.line))

        # DFS with cycle detection.
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self.content_names}
        parent_in_dfs: dict[str, tuple] = {}  # node → (predecessor, child_record)
        reported_cycles: set[frozenset] = set()

        def visit(node: str) -> None:
            color[node] = GRAY
            for (child, _ref_content, _ref_field, _line) in cascade_edges_out.get(node, []):
                edge_record = (node, child)
                if color.get(child, BLACK) == GRAY:
                    # back-edge → cycle. Reconstruct the cycle path.
                    cycle = [child]
                    cur = node
                    while cur != child and cur in parent_in_dfs:
                        cycle.append(cur)
                        cur = parent_in_dfs[cur][0]
                    cycle.append(child)
                    cycle.reverse()
                    cycle_set = frozenset(cycle)
                    if cycle_set in reported_cycles:
                        continue
                    reported_cycles.add(cycle_set)
                    edge_descs = []
                    for i in range(len(cycle) - 1):
                        a, b = cycle[i], cycle[i + 1]
                        for (ch, ref_c, ref_f, ln) in cascade_edges_out.get(a, []):
                            if ch == b:
                                edge_descs.append(
                                    f'"{a}" cascade-deletes "{b}" '
                                    f'(via {ref_c}.{ref_f}, line {ln})')
                                break
                    min_line = min(
                        ln
                        for n in cycle[:-1]
                        for (_, _, _, ln) in cascade_edges_out.get(n, []))
                    self.errors.add(SemanticError(
                        message=(
                            f'Cascade cycle detected: {" → ".join(cycle)}. '
                            f'{". ".join(edge_descs)}. Cascade cycles produce '
                            f'undefined or backend-dependent behavior at '
                            f'delete time. Self-references (a content '
                            f'referencing itself) are the only allowed form '
                            f'of cyclic cascade. Resolve by changing one '
                            f'edge in the cycle to "restrict on delete".'
                        ),
                        line=min_line,
                        code="TERMIN-S043",
                    ))
                elif color.get(child, BLACK) == WHITE:
                    parent_in_dfs[child] = (node, edge_record)
                    visit(child)
            color[node] = BLACK

        for n in sorted(cascade_edges_out.keys()):
            if color[n] == WHITE:
                visit(n)

    # v0.9: reserved keywords that may not appear as standalone tokens in
    # state names (grammar keywords in state sub-blocks + action button lines).
    STATE_NAME_RESERVED = {
        "a", "an", "also", "as", "become", "be", "can", "has", "if",
        "starts", "the", "to", "user",
    }

    def _state_name_has_reserved_word(self, state: str) -> str | None:
        """If `state` contains a reserved keyword as a whole word, return it."""
        for tok in state.split():
            if tok.lower() in self.STATE_NAME_RESERVED:
                return tok.lower()
        return None

    def _check_state_machines(self) -> None:
        # v0.9: per-content machine name uniqueness + state-vs-user-field
        # column collision. These rely on the Content objects themselves,
        # so iterate them first.
        for content in self.program.contents:
            seen_machines: dict[str, int] = {}
            # Track all snake_case field names on the content.
            # For state fields, fields[i].name is the field display name
            # ("approval status"); snake_casing matches the column name.
            non_state_snake: dict[str, int] = {}
            state_field_snake: dict[str, int] = {}
            for f in content.fields:
                snake = f.name.lower().replace(" ", "_")
                if f.type_expr.base_type == "state":
                    # Duplicate state-typed field name on the same content.
                    key = snake
                    if key in seen_machines:
                        self.errors.add(SemanticError(
                            message=(
                                f'Duplicate state machine "{f.name}" on content '
                                f'"{content.name}": a content may not declare two '
                                f'state-typed fields with the same name.'
                            ),
                            line=f.line,
                            code="TERMIN-S033",
                        ))
                    seen_machines[key] = f.line
                    state_field_snake[snake] = f.line
                else:
                    non_state_snake.setdefault(snake, f.line)
            # Column collision: a state field and a user field share a
            # snake_case column name on the same content.
            for snake, sline in state_field_snake.items():
                if snake in non_state_snake:
                    self.errors.add(SemanticError(
                        message=(
                            f'State field collision on "{content.name}": '
                            f'"{snake}" is declared as both a state machine '
                            f'and a regular field. Rename one of them.'
                        ),
                        line=sline,
                        code="TERMIN-S034",
                    ))

        # Per-machine checks
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

            # v0.9: exactly one `starts as` per machine.
            if sm.starts_as_count > 1:
                self.errors.add(SemanticError(
                    message=(
                        f'State machine "{sm.machine_name}" on "{sm.content_name}" '
                        f'has multiple "starts as" lines — a machine may declare '
                        f'its initial state only once.'
                    ),
                    line=sm.line,
                    code="TERMIN-S035",
                ))

            # v0.9: reserved-keyword tokens in state names.
            for st in sm.states:
                bad = self._state_name_has_reserved_word(st)
                if bad:
                    self.errors.add(SemanticError(
                        message=(
                            f'State name "{st}" in "{sm.machine_name}" contains '
                            f'reserved keyword "{bad}" — \'{bad}\' is a reserved '
                            f'keyword and cannot appear in a state name.'
                        ),
                        line=sm.line,
                        code="TERMIN-S036",
                    ))
            for tr in sm.transitions:
                for st in (tr.from_state, tr.to_state):
                    bad = self._state_name_has_reserved_word(st)
                    if bad:
                        self.errors.add(SemanticError(
                            message=(
                                f'State name "{st}" in "{sm.machine_name}" contains '
                                f'reserved keyword "{bad}" — \'{bad}\' is a reserved '
                                f'keyword and cannot appear in a state name.'
                            ),
                            line=tr.line,
                            code="TERMIN-S036",
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

        # v0.9: action button validation — `machine_name` must be a state
        # field on the acted-on content, and `target_state` must be a
        # reachable state of that machine.
        self._check_action_button_state_refs()

    def _check_action_button_state_refs(self) -> None:
        """Validate v0.9 action button `transitions <field> to <state>` lines.

        Each transition-kind action button is grounded by a preceding
        DisplayTable (gives us the content_name). The button's
        `machine_name` must match a state-typed field on that content;
        `target_state` must appear as a `to_state` in that machine's
        transition table (self-transitions from `to_state` are valid —
        they'd be picked up the same way).
        """
        # Index state machines by (content_name, snake_machine_name)
        sm_by_key: dict[tuple[str, str], StateMachine] = {}
        for sm in self.program.state_machines:
            key = (sm.content_name, sm.machine_name.lower().replace(" ", "_"))
            sm_by_key[key] = sm

        # Index state fields per content (snake_case -> True)
        state_fields_per_content: dict[str, set[str]] = {}
        for content in self.program.contents:
            sf = {
                f.name.lower().replace(" ", "_")
                for f in content.fields if f.type_expr.base_type == "state"
            }
            state_fields_per_content[content.name] = sf

        for story in self.program.stories:
            current_content: str | None = None
            for d in story.directives:
                if isinstance(d, DisplayTable):
                    current_content = d.content_name
                elif isinstance(d, ActionButtonDef) and d.kind == "transition":
                    if not current_content:
                        continue
                    content = self._find_content_by_name(current_content)
                    if content is None:
                        continue
                    mn_snake = (d.machine_name or "").lower().replace(" ", "_")
                    # Machine must be a declared state field on this content.
                    sfs = state_fields_per_content.get(content.name, set())
                    if mn_snake not in sfs:
                        suggestion = _fuzzy_match(mn_snake, sfs) if sfs else None
                        self.errors.add(SemanticError(
                            message=(
                                f'Action button "{d.label}" transitions '
                                f'"{d.machine_name}" — but "{d.machine_name}" is '
                                f'not a state field on "{content.name}".'
                            ),
                            line=d.line,
                            code="TERMIN-S037",
                            suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                        ))
                        continue
                    # Target state must appear as a to_state in the machine.
                    sm = sm_by_key.get((content.name, mn_snake))
                    if sm is None:
                        continue
                    reachable = {tr.to_state for tr in sm.transitions}
                    # Self-transitions: from_state can also equal to_state,
                    # which is already captured in `reachable` above.
                    if d.target_state not in reachable:
                        suggestion = _fuzzy_match(d.target_state, reachable) if reachable else None
                        self.errors.add(SemanticError(
                            message=(
                                f'Action button "{d.label}" targets state '
                                f'"{d.target_state}" — but "{d.target_state}" '
                                f'is not a valid transition target of '
                                f'"{d.machine_name}" on "{content.name}".'
                            ),
                            line=d.line,
                            code="TERMIN-S038",
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

    def _check_inline_editing(self) -> None:
        """`Allow inline editing of <fields>` requires:
          - a preceding DisplayTable on the page (so we know the content)
          - the content to declare `can update`
          - every listed field to exist on the content's schema
          - no state-machine-backed field (use transition buttons instead)

        Error codes:
          TERMIN-S022: Inline editing on content without `can update` rule.
          TERMIN-S023: Inline editing references an unknown field.
          TERMIN-S024: Inline editing attempted on a state-machine column.
        """
        # v0.9: Pre-compute, per content, the snake_case names of any
        # state-typed field declared inline on that content. These cannot
        # be inline-edited (TERMIN-S024).
        state_fields_by_content: dict[str, set[str]] = {}
        for content in self.program.contents:
            snake_names: set[str] = set()
            for f in content.fields:
                base = getattr(f.type_expr, "base_type", None)
                if base == "state":
                    snake_names.add(f.name.lower().replace(" ", "_"))
            if snake_names:
                state_fields_by_content[content.name] = snake_names

        for story in self.program.stories:
            current_table_content_name: str | None = None
            for d in story.directives:
                if isinstance(d, DisplayTable):
                    current_table_content_name = d.content_name
                elif isinstance(d, AllowInlineEdit):
                    if not current_table_content_name:
                        continue  # ungrounded; lowering handles
                    content = self._find_content_by_name(
                        current_table_content_name)
                    if content is None:
                        continue
                    # Require `can update` on the content.
                    has_update = any(
                        "update" in rule.verbs
                        for rule in content.access_rules)
                    if not has_update:
                        self.errors.add(SemanticError(
                            message=(
                                f'Inline editing on "{content.name}" has no '
                                f'matching access rule — add \'Anyone with '
                                f'"<scope>" can update {content.name}\' to the '
                                f'Content block.'
                            ),
                            line=d.line,
                            code="TERMIN-S022",
                        ))
                    # Known-field + not-a-state-field check per listed field.
                    schema_fields = {f.name for f in content.fields}
                    schema_fields_snake = {
                        f.name.lower().replace(" ", "_")
                        for f in content.fields
                    }
                    state_field_snakes = state_fields_by_content.get(
                        content.name, set())
                    for fname in d.fields:
                        fname_snake = fname.lower().replace(" ", "_")
                        if fname_snake in state_field_snakes:
                            self.errors.add(SemanticError(
                                message=(
                                    f'Cannot inline-edit the state-machine '
                                    f'column "{fname}" on "{content.name}" '
                                    f'— use transition buttons or the '
                                    f'Edit modal\'s state dropdown instead.'
                                ),
                                line=d.line,
                                code="TERMIN-S024",
                            ))
                            continue
                        if (fname not in schema_fields
                                and fname_snake not in schema_fields_snake):
                            self.errors.add(SemanticError(
                                message=(
                                    f'Inline editing references unknown field '
                                    f'"{fname}" on "{content.name}".'
                                ),
                                line=d.line,
                                code="TERMIN-S023",
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

            # v0.9 Phase 3 slice (c): validate Reads / Sends to / Emits
            # / Invokes references and the Accesses ∩ Reads = ∅
            # invariant (TERMIN-S044). The four grant kinds resolve
            # against different name catalogs:
            #   Reads    → content names (same as Accesses)
            #   Sends to → channel names
            #   Emits    → event names (open — anything declared
            #              elsewhere, or fresh)
            #   Invokes  → compute names
            # Per BRD §6.3.3: a content type cannot appear in both
            # Accesses and Reads — the grant is contradictory.
            # Canonicalize each content name (lowercase, trim) so a
            # content appearing on both lines with case-different
            # spellings still triggers S044 — typo-detection is the
            # whole point of the rule.
            accesses_resolved = {a.strip().lower() for a in compute.accesses}
            reads_resolved = {r.strip().lower() for r in compute.reads}
            for r in compute.reads:
                if not self._resolve_content_name(r):
                    suggestion = _fuzzy_match(r, self.content_names)
                    self.errors.add(SemanticError(
                        message=f'Compute "{compute.name}" declares Reads on '
                                f'undefined content "{r}"',
                        line=compute.line,
                        code="TERMIN-S045",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))
            dual = accesses_resolved & reads_resolved
            for c in sorted(dual):
                if c is None:
                    continue
                self.errors.add(SemanticError(
                    message=f'Compute "{compute.name}" declares both Accesses '
                            f'and Reads on "{c}". The grant is contradictory '
                            f'— Accesses already includes read access. '
                            f'Remove the duplicate from one of the lines.',
                    line=compute.line,
                    code="TERMIN-S044",
                ))
            for ch in compute.sends_to:
                if ch not in self.channel_names:
                    suggestion = _fuzzy_match(ch, self.channel_names)
                    self.errors.add(SemanticError(
                        message=f'Compute "{compute.name}" declares Sends to '
                                f'undefined channel "{ch}"',
                        line=compute.line,
                        code="TERMIN-S046",
                        suggestion=f'Did you mean "{suggestion}"?' if suggestion else None,
                    ))
            for inv in compute.invokes:
                if inv not in self.compute_names:
                    suggestion = _fuzzy_match(inv, self.compute_names)
                    self.errors.add(SemanticError(
                        message=f'Compute "{compute.name}" declares Invokes on '
                                f'undefined compute "{inv}"',
                        line=compute.line,
                        code="TERMIN-S047",
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
        """Every Compute must have an access rule.

        v0.9: only the scope-based form `Anyone with "<scope>" can
        execute this` is valid. The v0.8 bare-role form was removed
        — see termin/parse_handlers.py compute_access_line handler
        for the rejection migration error.
        """
        for compute in self.program.computes:
            if not compute.access_scope:
                self.errors.add(SecurityError(
                    message=f'Compute "{compute.name}" has no access rule. '
                            f'Every Compute must declare who can execute it: '
                            f'`Anyone with "<scope>" can execute this`.',
                    line=compute.line,
                    code="TERMIN-X004",
                ))

    # ── Channel Checks ──

    VALID_DIRECTIONS = {"inbound", "outbound", "bidirectional", "internal"}
    VALID_DELIVERIES = {"realtime", "reliable", "batch", "auto"}
    # v0.9 Phase 4: valid named contracts for the Channel category.
    VALID_CHANNEL_CONTRACTS = {"webhook", "email", "messaging", "event-stream"}
    # v0.9 Phase 4: valid failure_mode values (grammar placeholder — runtime
    # always uses "log-and-drop" in Phase 4, but we validate syntax now).
    VALID_FAILURE_MODES = {"log-and-drop", "surface-as-error", "queue-and-retry-forever"}
    # v0.9 Phase 4: action vocab per contract. Used to validate Action sub-blocks.
    # Values are frozensets of source-level display-string prefixes. An action
    # body "starts with" a prefix to be considered valid for the contract
    # v0.9 Phase 4: action vocab per contract (first-verb matching).
    # "webhook" is NOT in this dict: webhook is a generic HTTP channel;
    # any action name is valid (operations vary by integration). Vocab
    # restriction applies to structured protocols (messaging, email,
    # event-stream) where the operation space is well-defined and
    # cross-contract naming is a bug indicator (e.g., "Dispatch carrier
    # pigeon" on a messaging contract).
    #
    # Matching extracts the first word/segment of the action name
    # (split on whitespace then on hyphens/underscores, lowercased) and
    # checks it against the valid verb set. This accepts both natural-
    # language style ("Send a message alert") and API-style
    # ("post-message", "update-status") action names.
    _CHANNEL_ACTION_VOCAB: dict[str, frozenset[str]] = {
        "email":        frozenset({"send", "email", "notify", "compose",
                                   "draft", "reply", "forward", "attach"}),
        "messaging":    frozenset({"send", "post", "reply", "update",
                                   "react", "receive", "notify", "publish",
                                   "when", "get", "fetch", "archive",
                                   "delete", "subscribe", "message"}),
        "event-stream": frozenset({"register", "publish", "subscribe",
                                   "emit", "stream"}),
    }

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

            # v0.9 Phase 4: provider contract validation.

            # Outbound/bidirectional channels must declare a provider contract.
            # Internal channels use the distributed runtime layer — no provider.
            # Inbound channels may lack a provider in Phase 4 (inbound-only
            # channels are typically triggered externally; deploy config wires
            # them up). We error only on outbound/bidirectional.
            if channel.direction in ("outbound", "bidirectional") and not channel.provider_contract:
                self.errors.add(SemanticError(
                    message=f'Channel "{channel.name}" is {channel.direction} but has no '
                            f'"Provider is" declaration. Add `Provider is "<contract>"` to the '
                            f'channel block. Valid contracts: '
                            f'{", ".join(sorted(self.VALID_CHANNEL_CONTRACTS))}.',
                    line=channel.line,
                    code="TERMIN-S026",
                ))

            # Provider contract name must be one of the four built-in contracts.
            if channel.provider_contract and channel.provider_contract not in self.VALID_CHANNEL_CONTRACTS:
                self.errors.add(SemanticError(
                    message=f'Channel "{channel.name}" has unknown provider contract '
                            f'"{channel.provider_contract}". '
                            f'Valid contracts: {", ".join(sorted(self.VALID_CHANNEL_CONTRACTS))}.',
                    line=channel.line,
                    code="TERMIN-S027",
                ))

            # Failure mode must be a valid value if specified (grammar placeholder).
            if channel.failure_mode and channel.failure_mode not in self.VALID_FAILURE_MODES:
                self.errors.add(SemanticError(
                    message=f'Channel "{channel.name}" has unknown failure mode '
                            f'"{channel.failure_mode}". '
                            f'Valid modes: {", ".join(sorted(self.VALID_FAILURE_MODES))}.',
                    line=channel.line,
                    code="TERMIN-S028",
                ))

            # Action vocabulary validation: when provider_contract is set,
            # each Action's name must begin with a recognized verb from
            # the contract's vocabulary set. The first word/segment of
            # the action name is extracted (split on whitespace, then on
            # hyphens/underscores) and lowercased before comparison,
            # so "post-message" → "post" and "Send a message" → "send".
            if channel.provider_contract and channel.provider_contract in self._CHANNEL_ACTION_VOCAB:
                allowed_vocab = self._CHANNEL_ACTION_VOCAB[channel.provider_contract]
                for action in channel.actions:
                    action_name = action.name
                    # Extract first word-segment (space-split, then hyphen/underscore)
                    first_word = re.split(r'[\s\-_]', action_name)[0].lower()
                    if first_word not in allowed_vocab:
                        self.errors.add(SemanticError(
                            message=f'Action "{action_name}" on Channel "{channel.name}" is not '
                                    f'in the vocabulary for provider contract '
                                    f'"{channel.provider_contract}". '
                                    f'Valid action verbs: '
                                    f'{", ".join(sorted(allowed_vocab))}.',
                            line=action.line if hasattr(action, "line") else channel.line,
                            code="TERMIN-S029",
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
