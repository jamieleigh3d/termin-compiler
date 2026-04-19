# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""TatSu PEG-based parser for the Termin DSL.

Two-level design:
  Level 1 (Python): Line classification by keyword + block assembly
  Level 2 (TatSu PEG): Per-line content parsing using termin.peg

Public API: parse_peg(source: str) -> tuple[Program, CompileResult]

Subsystem modules:
  - classify.py: Line → rule mapping
  - parse_helpers.py: TatSu grammar, helper functions, type parsing
  - parse_builders.py: AST builder functions
  - parse_handlers.py: Per-line parse dispatch
"""
from __future__ import annotations

from .ast_nodes import (
    Program, Application, Identity, Role, Content, NavBar, ErrorHandler,
    ErrorAction, StateMachine, EventRule, EventAction, UserStory,
    ShowPage, ComputeNode, ChannelDecl, ActionDecl, BoundaryDecl,
    BoundaryProperty, Stream,
)
from .errors import ParseError, CompileResult
from .classify import classify_line
from .parse_helpers import _qs, _ql, _fq, _cl, _parse_literal_list, _model
from .parse_builders import _parse_content_when
from .parse_handlers import _parse_line

# Re-export for backward compatibility (tests import these from peg_parser)
_classify_line = classify_line


# --- Preprocessor ---
def _preprocess(source: str) -> list[tuple[int, str]]:
    """Strip comments, blank lines, parenthetical annotations.
    Join triple-backtick multi-line blocks into single lines."""
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
                if s != "```":
                    multiline_content.append(s[:-3].rstrip())
                joined = "\n".join(multiline_content)
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
                in_multiline = True
                multiline_start = line_num
                multiline_prefix = s[:triple_idx]
                if after.strip():
                    multiline_content.append(after)
                continue

        idx = s.find(" (")
        if idx > 0:
            tail = s[idx:]
            pc = tail.find(")")
            if pc > 0 and pc == len(tail) - 1:
                s = s[:idx].rstrip()
        result.append((line_num, s))
    return result


# --- Block assembly ---
def _assemble(parsed: list) -> Program:
    """Assemble flat parsed tuples into a hierarchical Program AST."""
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
            for ch in _collect(lambda x: x in ("field","access","content_scoped","content_audit","dependent_value")):
                if ch[0] == "field":
                    if ch[2]: ct.singular = ch[2]
                    ct.fields.append(ch[1])
                elif ch[0] == "access": ct.access_rules.append(ch[1])
                elif ch[0] == "content_scoped": ct.confidentiality_scopes.extend(ch[1])
                elif ch[0] == "content_audit": ct.audit = ch[1]
                elif ch[0] == "dependent_value": ct.dependent_values.append(ch[1])
            prog.contents.append(ct)
        elif k == "state_header":
            sm = item[1]; i += 1
            last_transition = None
            for ch in _collect(lambda x: x in ("state_starts","state_also","state_transition","transition_feedback")):
                if ch[0] == "state_starts": sm.singular = ch[1]; sm.initial_state = ch[2]; sm.states.append(sm.initial_state)
                elif ch[0] == "state_also": sm.states.extend(ch[1])
                elif ch[0] == "state_transition":
                    sm.transitions.append(ch[1])
                    last_transition = ch[1]
                elif ch[0] == "transition_feedback" and last_transition is not None:
                    last_transition.feedback.append(ch[1])
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
        elif k == "stream": prog.streams.append(item[1]); i += 1
        elif k == "compute_header":
            nd = item[1]; i += 1
            _compute_child_kinds = ("compute_shape","compute_body","compute_body_multiline",
                "compute_access","access","compute_identity","compute_requires_conf",
                "compute_output_conf","compute_provider","compute_trigger",
                "compute_preconditions_header","compute_postconditions_header",
                "compute_objective","compute_strategy","compute_directive",
                "compute_accesses","compute_input_field","compute_output_field",
                "compute_output_creates","compute_audit_access","content_audit")
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
                elif ch[0] == "compute_trigger":
                    nd.trigger = ch[1]
                    if len(ch) > 2 and ch[2]:
                        nd.trigger_where = ch[2]
                elif ch[0] == "compute_objective": nd.objective = ch[1]
                elif ch[0] == "compute_strategy": nd.strategy = ch[1]
                elif ch[0] == "compute_directive": nd.directive = ch[1]
                elif ch[0] == "compute_accesses": nd.accesses.extend(ch[1])
                elif ch[0] == "compute_input_field": nd.input_fields.append(ch[1])
                elif ch[0] == "compute_output_field": nd.output_fields.append(ch[1])
                elif ch[0] == "compute_output_creates": nd.output_creates = ch[1]
                elif ch[0] == "compute_audit_access": nd.audit_scope = ch[1]
                elif ch[0] == "content_audit": nd.audit_level = ch[1]
            prog.computes.append(nd)
        elif k == "channel_header":
            ch_ = item[1]; i += 1
            current_action = None
            for child in _collect(lambda x: x in ("channel_prop", "action_header", "action_prop")):
                if child[0] == "action_header":
                    current_action = child[1]
                    ch_.actions.append(current_action)
                    continue
                if child[0] == "action_prop" and current_action is not None:
                    p, v = child[1], child[2]
                    if p == "takes": current_action.takes = v
                    elif p == "returns": current_action.returns = v
                    elif p == "requires": current_action.required_scopes.append(v)
                    continue
                if child[0] == "channel_prop":
                    p, v = child[1], child[2]
                    if p == "requires" and current_action is not None and hasattr(v, 'direction') and v.direction == "invoke":
                        current_action.required_scopes.append(v.scope)
                        continue
                    current_action = None
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
    """Parse a .termin source string into a Program AST.

    Returns (program, errors) where errors.ok is True if parsing succeeded.
    """
    errors = CompileResult()
    try: lines = _preprocess(source)
    except Exception as e:
        errors.add(ParseError(message=f"Preprocessing failed: {e}", line=0, code="TERMIN-P001")); return Program(), errors
    parsed = []
    for line_num, text in lines:
        rule = classify_line(text)
        if rule == "unknown":
            errors.add(ParseError(message=f"Unrecognized line: {text}", line=line_num, source_line=text, code="TERMIN-P002")); continue
        try:
            result = _parse_line(text, rule, line_num)
            if result is not None: parsed.append(result)
        except Exception as e:
            errors.add(ParseError(message=f"Failed to parse line: {e}", line=line_num, source_line=text, code="TERMIN-P003"))
    if not errors.ok: return Program(), errors
    try: program = _assemble(parsed)
    except Exception as e:
        errors.add(ParseError(message=f"Block assembly failed: {e}", line=0, code="TERMIN-P004")); return Program(), errors
    return program, errors
