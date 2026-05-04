# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Per-line parse dispatch — the main _parse_line function.

Maps each classified rule to the appropriate TatSu parse + AST builder,
with fallback paths for TatSu context state leaks.
"""
from __future__ import annotations
from typing import Optional

from .ast_nodes import (
    Application, Identity, Role, RoleAlias, Content, Field, TypeExpr,
    AccessRule, AppendAction, StateMachine, EventRule, EventAction, EventCondition,
    ErrorHandler, ErrorAction, UserStory, ShowPage, DisplayTable, ShowRelated,
    HighlightRows, MarkAs, UsingOverride, AllowFilter, AllowSearch, AllowInlineEdit, SubscribeTo, AcceptInput,
    ValidateUnique, CreateAs, AfterSave, ShowChart, DisplayAggregation,
    StructuredAggregation, SectionStart, ActionHeader, ActionButtonDef,
    Stream, ChatDirective, DisplayText, LinkColumn, PackageContractCall,
    ComputeNode, ChannelDecl, ChannelRequirement, ActionDecl,
    BoundaryDecl, BoundaryProperty,
)
from .parse_helpers import (
    _try_parse, _rule, _qs, _ql, _cl, _ol, _si,
    _scal, _eqs, _fq, _eb,
    _parse_field_type,
)
from .parse_builders import (
    _build_access, _build_story, _build_nav, _build_feedback,
    _build_trans, _build_ev1, _build_err_act, _build_comp_shape,
    _parse_action_params, _parse_content_when, _parse_unconditional_constraint,
)


def _quoted_or_word(item) -> str:
    """Normalize one element from a quoted_or_word_list TatSu match.

    The PEG `quoted_or_word_item` alternates between `quoted_string`
    (which TatSu returns as a dict like `{"content": "value"}` per
    the named-capture syntax) and a bare word/phrase (returned as a
    plain string). Normalizes both shapes to a stripped string."""
    if item is None:
        return ""
    if isinstance(item, dict):
        return str(item.get("content", "")).strip()
    return str(item).strip().strip('"')


def _cl_qw(r) -> list[str]:
    """Extract a comma-separated quoted-or-word list from a TatSu
    result, preserving the dict shape of quoted-string items so
    `_quoted_or_word` can extract the inner content key.

    Mirrors `_cl` from parse_helpers but skips the str() coercion
    that would stringify quoted-string dicts."""
    if r is None:
        return []
    if isinstance(r, dict) and "item" in r:
        items = r["item"]
    elif isinstance(r, list):
        items = r
    else:
        items = [r]
    if not isinstance(items, list):
        items = [items]
    return [_quoted_or_word(i) for i in items if i is not None]


_KNOWN_VERBS = {"view", "create", "update", "delete"}
_VERB_HELP = (
    "Termin verbs are: view, create, update, delete. "
    "(Did you mean 'view' instead of 'read'?)"
)


def _validate_access_content_name(cn: str) -> None:
    """Catch verb-list truncation that slipped through grammar.

    When an access grant contains an unknown verb mid-list (e.g. 'create,
    read, update, and delete documents'), TatSu's verb_phrase falls back
    to the SingleVerb alternative — capturing only 'create' — and lets
    rest_of_line greedily consume ', read, update, and delete documents'
    as the content name. A real content name has none of those features.
    """
    if not cn:
        return
    if cn.startswith(","):
        # Verb list got truncated; rest swallowed as content_name.
        rest = cn.lstrip(", ").rstrip()
        _check_can_clause_for_unknown_verbs(rest)
        # If no unknown verb was found, the malformed shape is something
        # else — surface a generic error.
        raise ValueError(
            f"Malformed access grant: content name starts with comma "
            f"({cn!r}). " + _VERB_HELP
        )
    # Look for `, <known-verb>` or ` and <known-verb>` patterns inside
    # what should be a single content name.
    lowered = cn.lower()
    for v in _KNOWN_VERBS:
        if f", {v}" in lowered or f" and {v}" in lowered or f" or {v}" in lowered:
            raise ValueError(
                f"Malformed access grant: content name contains stray "
                f"verb token ({cn!r}). " + _VERB_HELP
            )


def _parse_can_clause_fallback(rest: str) -> tuple[list[str], str, bool]:
    """Extract `<verbs> [their own] <content_name>` from an access-rule's `can` clause.

    Mirrors `_check_can_clause_for_unknown_verbs`'s tokenization but
    returns the parsed verb list, content name, and the `their own`
    flag instead of validating. Used by the fallback path in the
    access-rule line handler when TatSu rejects the line — TatSu has
    a known platform-dependent context-state leak on WSL/Linux (see
    workspace MEMORY.md and `feedback_grammar_peg_authoritative.md`)
    where the second and subsequent calls return None even when the
    source is valid PEG.

    Without this helper, the fallback hardcoded `verbs=["view"]` for
    every line, which silently rewrites `Anyone with X can update Y`
    as a view-only rule. Downstream semantic checks (TERMIN-S020/021/022
    on row-action access matching) then fail because no rule actually
    has the `update` or `delete` verb. Tracked by the bug retrospective
    on 2026-04-29 — the fallback's "always emit something safe" stance
    masked semantic intent on WSL while Windows worked fine.

    The `their own` qualifier (Phase 6a.3 / BRD #3 §3.4) extends this:
    `Anyone with X can view their own Y` declares a row-filtered
    access rule where the principal sees only rows they own. The
    fallback must preserve that flag — without it, ownership-cascade
    auth degrades to scope-only on WSL.

    Returns (verbs, content_name, their_own); verbs is `["view"]` only
    when no known verb appears (extreme defensive case — should not
    happen for well-formed source).
    """
    if not rest:
        return ["view"], "", False
    connectors = {"or", "and", ","}
    tokens = rest.replace(",", " , ").split()
    verbs: list[str] = []
    content_start_idx = 0
    for i, t in enumerate(tokens):
        if t in _KNOWN_VERBS:
            verbs.append(t)
            content_start_idx = i + 1
            continue
        if t in connectors:
            content_start_idx = i + 1
            continue
        # Non-verb, non-connector → content name (or `their own`
        # qualifier) starts here.
        content_start_idx = i
        break
    rest_tokens = [t for t in tokens[content_start_idx:] if t != ","]
    # Detect `their own` at the start of the content section. The
    # qualifier is exactly two tokens; consume them and flag the rule.
    their_own = False
    if (
        len(rest_tokens) >= 2
        and rest_tokens[0].lower() == "their"
        and rest_tokens[1].lower() == "own"
    ):
        their_own = True
        rest_tokens = rest_tokens[2:]
    content_name = " ".join(rest_tokens).strip()
    if not verbs:
        verbs = ["view"]
    return verbs, content_name, their_own


def _check_can_clause_for_unknown_verbs(rest: str) -> None:
    """Scan a `... can <rest>` clause for unknown verb tokens.

    Walks tokens until the first non-verb/non-connector token that's
    NOT preceded by a connector — that's the content name. Any unknown
    word in the verb section raises ValueError with the bad word(s)
    named.
    """
    if not rest:
        return
    connectors = {"or", "and", ","}
    tokens = rest.replace(",", " , ").split()
    unknowns: list[str] = []
    verbs_seen = 0
    for i, t in enumerate(tokens):
        if t in _KNOWN_VERBS:
            verbs_seen += 1
            continue
        if t in connectors:
            continue
        # Non-verb, non-connector. If preceded by a connector OR no
        # verbs seen yet, this is in the verb section.
        prev = tokens[i - 1] if i > 0 else ""
        if prev in connectors or verbs_seen == 0:
            unknowns.append(t)
        else:
            # Content name section starts here; stop scanning.
            break
    if unknowns:
        bad = ", ".join(repr(v) for v in unknowns)
        raise ValueError(
            f"Unknown verb(s) {bad} in access grant. " + _VERB_HELP
        )


def _parse_access_append(text: str, ln: int) -> AccessRule:
    """v0.9.2 L3: parse `Anyone with "X" can append to [their own] <content>.<field>`.

    Tries the TatSu rule first; falls back to a regex-based path for the
    Linux/WSL state-leak case (workspace rule 9). Both must produce the
    same AccessRule shape — verbs=["append"], append_field=<snake>,
    their_own=<bool>. Fallback fidelity is exercised in
    tests/test_access_rule_fallback_fidelity.py.

    Dot notation matches `Conversation is X.Y`, `Append to X.Y as ...`,
    and the trigger event name shape — one canonical content+field
    reference shape across the whole DSL.
    """
    r = _try_parse(text, "access_append_line")
    if r:
        scope = _qs(r.get("scope", ""))
        their = bool(r.get("their_own"))
        field = str(r.get("field", "")).strip()
        return AccessRule(
            scope=scope, verbs=["append"], their_own=their,
            append_field=field, line=ln,
        )
    # Fallback: parse out scope, their_own flag, content, field via
    # straight string ops. Match the TatSu output exactly so the shape
    # is identical.
    scope = _fq(text)
    rest_idx = text.find(" can append to ")
    if rest_idx < 0:
        # Defensive: classifier shouldn't have routed us here.
        return AccessRule(scope=scope, verbs=["append"], line=ln)
    tail = text[rest_idx + len(" can append to "):].strip()
    their_own = False
    if tail.startswith("their own "):
        their_own = True
        tail = tail[len("their own "):].strip()
    # tail is `<content>.<field>` — split on the dot.
    dot_idx = tail.find(".")
    field = ""
    if dot_idx >= 0:
        field = tail[dot_idx + 1:].strip()
    return AccessRule(
        scope=scope, verbs=["append"], their_own=their_own,
        append_field=field, line=ln,
    )


def _parse_append_action(text: str, ln: int) -> AppendAction:
    """v0.9.2 L3: parse a source-level `Append to <ref>.<field> as "<kind>" with body \\`<expr>\\`` line.

    Optional metadata tail (`, source: \\`...\\``) is captured verbatim
    on the AppendAction for later slices to decompose. Fallback path
    uses string ops only — TatSu state leaks have historically caused
    None returns on Linux for valid input.
    """
    r = _try_parse(text, "append_action_line")
    if r:
        record = str(r.get("record", "")).strip()
        field = str(r.get("field", "")).strip()
        kind = _qs(r.get("kind", ""))
        body = _qs(r.get("body", "")) if isinstance(r.get("body"), dict) else ""
        if not body:
            # body is an `expr` — content sits in r["body"]["content"]
            b = r.get("body")
            if isinstance(b, dict):
                body = str(b.get("content", "")).strip()
        meta = str(r.get("metadata", "")).strip()
        return AppendAction(
            record=record, field=field, kind=kind,
            body_expr=body, metadata_tail=meta, line=ln,
        )
    # Fallback: extract pieces via straight string ops.
    # Format: "Append to RECORD.FIELD as "KIND" with body `EXPR`[, ...]"
    after_to = text[len("Append to "):].strip() if text.startswith("Append to ") else text.strip()
    dot_idx = after_to.find(".")
    record = after_to[:dot_idx].strip() if dot_idx >= 0 else ""
    after_dot = after_to[dot_idx + 1:].strip() if dot_idx >= 0 else after_to
    as_idx = after_dot.find(" as ")
    field = after_dot[:as_idx].strip() if as_idx >= 0 else after_dot
    after_as = after_dot[as_idx + len(" as "):].strip() if as_idx >= 0 else ""
    kind = _fq(after_as)
    # Find first backtick block — that's the body expression.
    bt1 = after_as.find("`")
    body = ""
    meta = ""
    if bt1 >= 0:
        bt2 = after_as.find("`", bt1 + 1)
        if bt2 >= 0:
            body = after_as[bt1 + 1:bt2].strip()
            meta = after_as[bt2 + 1:].strip()
    return AppendAction(
        record=record, field=field, kind=kind,
        body_expr=body, metadata_tail=meta, line=ln,
    )


def _parse_line(text: str, rule: str, ln: int):
    """Parse a single classified line into a tagged AST tuple.

    Returns a tuple like ("application", Application(...)) or None.
    The tag is used by _assemble() to build the Program tree.
    """
    P = _try_parse  # alias

    if rule == "application_line":
        r = P(text, rule); return ("application", Application(name=str(r["name"]).strip() if r else text[len("Application:"):].strip(), line=ln))
    if rule == "description_line":
        r = P(text, rule); return ("description", str(r["desc"]).strip() if r else text[len("Description:"):].strip())
    if rule == "id_line":
        r = P(text, rule); return ("app_id", str(r["id"]).strip() if r else text[len("Id:"):].strip())
    if rule == "identity_block_open_line":
        # v0.9 Phase 1: bare top-level `Identity:` opens the sub-block.
        # Run through TatSu first so test_no_tatsu_fallbacks sees the
        # rule was matched; the parse result has no useful payload
        # (just the keyword) so we ignore it and emit a sentinel tag
        # the assembler uses to construct the Identity AST node and
        # to gate subsequent scopes/role lines.
        P(text, rule)
        return ("identity_block_open", Identity(provider="stub", line=ln))
    if rule == "identity_line":
        # v0.9 Phase 1: `Users authenticate with X` is removed.
        # Authentication is implied by the presence of any non-Anonymous
        # role inside the Identity block. Provider product names
        # (stub, okta, etc.) live in deploy config, not source.
        raise ValueError(
            "`Users authenticate with X` is removed in v0.9. "
            "Identity is now declared in an `Identity:` block:\n"
            "  Identity:\n"
            "    Scopes are \"...\"\n"
            "    A \"role\" has \"...\"\n"
            "    Anonymous has \"...\"\n"
            "Authentication provider (stub, okta, etc.) lives in deploy config."
        )
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
            sg = text[len("Each "):hi].strip(); ah = text[hi+len(" has "):].strip()
            for a in ("a ","an ","the "):
                if ah.startswith(a): ah = ah[len(a):]; break
            wi = ah.find(" which ")
            if wi < 0: return ("field", Field(name="unknown", type_expr=TypeExpr(base_type="text"), line=ln), sg)
            fn = ah[:wi].strip()
        wi = text.find(" which ")
        te = _parse_field_type(text[wi+len(" which "):].strip(), ln) if wi >= 0 else TypeExpr(base_type="text", line=ln)
        # v0.9: a `which is state:` field opens an inline state machine sub-block.
        # Emit a distinct tag so the assembler can build the StateMachine.
        if te.base_type == "state":
            return ("state_field", Field(name=fn, type_expr=te, line=ln), sg)
        return ("field", Field(name=fn, type_expr=te, line=ln), sg)
    if rule == "access_line":
        r = P(text, rule)
        if r:
            # Validate that the verb list didn't get truncated. The
            # grammar's verb_phrase falls back to the single_verb
            # alternative when later verbs include unknown words
            # ('read' is a common offender — Termin uses 'view').
            # When that happens, single_verb captures the first verb
            # and rest_of_line swallows the comma-separated remainder
            # as content_name. A content name should never start with
            # a comma or contain stray known-verb tokens.
            cn = str(r.get("content_name", "")).strip()
            _validate_access_content_name(cn)
            return ("access", _build_access(r, ln))
        # TatSu rejected the line entirely. Look for unknown verbs in
        # the rest-of-can clause and raise a clear error rather than
        # silently truncating.
        sc = _fq(text); ci = text.find(" can ")
        if ci >= 0:
            rest = text[ci+5:].strip()
            _check_can_clause_for_unknown_verbs(rest)
            # PARSE the verb list from the `can` clause instead of
            # hardcoding ["view"] — the bug from the 2026-04-29
            # retrospective. On WSL/Linux TatSu falls back here for
            # otherwise-valid lines, and emitting verbs=["view"] for
            # an `... can update <content>` line silently rewrote
            # update grants as view grants, breaking the row-action
            # semantic checks. _parse_can_clause_fallback uses the
            # same tokenization as _check_can_clause_for_unknown_verbs.
            verbs, _content_name, their_own = _parse_can_clause_fallback(rest)
            # v0.9.2 Slice L10: preserve the noun the author wrote after
            # `their own` so the analyzer can detect singular vs plural
            # for the §15.3 TERMIN-S057 check.
            their_own_noun = _content_name.lower().strip() if their_own else None
            return ("access", AccessRule(
                scope=sc,
                verbs=verbs,
                their_own=their_own,
                their_own_noun=their_own_noun,
                line=ln,
            ))
        return ("access", AccessRule(scope=_fq(text), verbs=["view"], line=ln))
    # v0.9.2 L3: field-targeted append permission
    # `Anyone with "X" can append to [their own] <plural>' <field>`
    if rule == "access_append_line":
        return ("access", _parse_access_append(text, ln))
    # v0.9.2 L3: source-level Append action verb
    # `Append to <record>.<field> as "<kind>" with body \`<expr>\` [, <metadata>: <expr>]`
    if rule == "append_action_line":
        return ("append_action", _parse_append_action(text, ln))
    # v0.9: inline state machine sub-block lines.
    if rule == "sm_starts_as_line":
        # `<field name> starts as <state>` — extract field name and initial state.
        si = text.find(" starts as ")
        if si < 0:
            return None
        field_name = text[:si].strip()
        state_text = text[si + len(" starts as "):].strip()
        # State value: quoted or bare; strip quotes if present.
        if state_text.startswith('"') and state_text.endswith('"'):
            state_text = state_text[1:-1]
        return ("sm_starts_as", field_name, state_text)
    if rule == "sm_also_line":
        # `<field name> can also be <state list>`
        ci = text.find(" can also be ")
        if ci < 0:
            return None
        field_name = text[:ci].strip()
        list_text = text[ci + len(" can also be "):].strip()
        # Parse states: split on `or` and commas, strip quotes.
        states = []
        # Replace ", or " and " or " with comma sentinels for splitting.
        normalized = list_text
        for sep in [", or ", ", ", " or "]:
            normalized = normalized.replace(sep, "\x00")
        for part in normalized.split("\x00"):
            p = part.strip()
            if not p:
                continue
            if p.startswith('"') and p.endswith('"'):
                p = p[1:-1]
            states.append(p)
        return ("sm_also", field_name, states)
    if rule == "sm_transition_line":
        # `[A|An] <from> can become <to> if the user has <scope>`
        rest = text.strip()
        for a in ("A ", "An "):
            if rest.startswith(a):
                rest = rest[len(a):]
                break
        ci = rest.find(" can become ")
        if ci < 0:
            return None
        from_state = rest[:ci].strip()
        if from_state.startswith('"') and from_state.endswith('"'):
            from_state = from_state[1:-1]
        after = rest[ci + len(" can become "):].strip()
        # Find the " if " boundary; "if the user has <scope>" or "if <cel>".
        ii = after.find(" if ")
        if ii < 0:
            return None
        to_state = after[:ii].strip()
        if to_state.startswith('"') and to_state.endswith('"'):
            to_state = to_state[1:-1]
        cond_text = after[ii + len(" if "):].strip()
        if cond_text.startswith("the user has "):
            scope_text = cond_text[len("the user has "):].strip()
            if scope_text.startswith('"') and scope_text.endswith('"'):
                scope_text = scope_text[1:-1]
            from .ast_nodes import Transition
            return ("sm_transition", Transition(
                from_state=from_state,
                to_state=to_state,
                required_scope=scope_text,
                line=ln,
            ))
        # CEL expression form (placeholder — store raw scope string).
        from .ast_nodes import Transition
        return ("sm_transition", Transition(
            from_state=from_state,
            to_state=to_state,
            required_scope=cond_text.strip("`"),
            line=ln,
        ))
    if rule == "transition_feedback_line":
        return ("transition_feedback", _build_feedback(text, ln))
    if rule == "event_expr_line":
        r = P(text, rule); j = _qs(r.get("cel","")) if r else (_eb(text) or "")
        return ("event_header", EventRule(content_name="", trigger="expr", condition_expr=j, line=ln))
    if rule == "event_v1_line":
        return ("event_header", _build_ev1(text, ln))
    if rule == "event_action_line":
        r = P(text, rule)
        if r: return ("event_action", EventAction(create_content=_qs(r.get("name","")), fields=_cl(r.get("fields")), line=ln))
        rest = text[len("Create "):].strip()
        for a in ("a ","an ","the "):
            if rest.startswith(a): rest = rest[len(a):]; break
        wi = rest.find(" with ")
        if wi >= 0:
            n = rest[:wi].strip().strip('"'); ft = rest[wi+6:].strip()
            if ft.startswith("the "): ft = ft[4:]
            return ("event_action", EventAction(create_content=n, fields=_scal(ft), line=ln))
        return ("event_action", EventAction(create_content=rest.strip().strip('"'), line=ln))
    if rule == "event_send_line":
        r = P(text, rule)
        if r:
            content = str(r.get("content","")).strip()
            channel = _qs(r.get("channel",""))
        else:
            parts = text[len("Send "):].strip()
            if " to " in parts:
                content, channel = parts.split(" to ", 1)
                content = content.strip()
                channel = channel.strip().strip('"')
            else:
                content = parts; channel = ""
        return ("event_action", EventAction(send_content=content, send_channel=channel, line=ln))
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
        r = P(text, rule); return ("so_that", str(r.get("text","")).strip().rstrip(":") if r else text[len("so that "):].strip().rstrip(":"))
    if rule == "show_page_line":
        r = P(text, rule); return ("directive", ShowPage(page_name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "chat_line":
        r = P(text, rule)
        if r:
            source = str(r.get("source", "")).strip()
            if r.get("role_field") is not None:
                role_field = _qs(r.get("role_field", ""))
                content_field = _qs(r.get("content_field", ""))
                return ("directive", ChatDirective(source=source, role_field=role_field, content_field=content_field, line=ln))
            else:
                return ("directive", ChatDirective(source=source, line=ln))
        rest = text[len("Show a chat for "):].strip()
        wi = rest.find(" with role ")
        if wi >= 0:
            source = rest[:wi].strip()
            mapping = rest[wi:]
            role_field = _fq(mapping.split(",")[0]) if "," in mapping else "role"
            content_field = ""
            ci = mapping.find(', content "')
            if ci >= 0:
                content_field = _fq(mapping[ci+10:])
            return ("directive", ChatDirective(source=source, role_field=role_field or "role", content_field=content_field or "content", line=ln))
        return ("directive", ChatDirective(source=rest, line=ln))
    if rule == "display_table_line":
        rest = text[len("Display a table of "):].strip(); wi = rest.find(" with columns:")
        cn = rest[:wi].strip() if wi>=0 else rest.strip()
        cols = _scal(rest[wi+14:]) if wi>=0 else []
        return ("directive", DisplayTable(content_name=cn, columns=cols, line=ln))
    if rule == "show_related_line":
        rest = text[len("For each "):].strip(); ci = rest.find(",")
        if ci < 0: return ("directive", ShowRelated(singular=rest, line=ln))
        sg = rest[:ci].strip(); af = rest[ci+1:].strip()
        if af.lower().startswith("show "): af = af[5:].strip()
        gi = af.find(" grouped by ")
        if gi < 0: return ("directive", ShowRelated(singular=sg, related_content=af, line=ln))
        return ("directive", ShowRelated(singular=sg, related_content=af[:gi].strip(), group_by=af[gi+12:].strip(), line=ln))
    if rule == "mark_rows_line":
        cel = _eb(text) or ""
        label = ""
        if ' as "' in text:
            label = text.rsplit(' as "', 1)[1].rstrip('"').strip()
        elif " as '" in text:
            label = text.rsplit(" as '", 1)[1].rstrip("'").strip()
        scope = "row"
        if text.startswith("Mark ") and not text.startswith("Mark rows"):
            after_mark = text[len("Mark "):].strip()
            if " where " in after_mark:
                scope = after_mark.split(" where ", 1)[0].strip()
        return ("directive", MarkAs(condition_expr=cel, label=label, scope=scope, line=ln))
    if rule == "highlight_rows_line":
        rest = text[len("Highlight rows where "):].strip(); j = _eb(rest)
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
        if len(fs) == 1 and " or " in fs[0]:
            fs = [f.strip() for f in fs[0].split(" or ") if f.strip()]
        if not fs:
            rest = text[len("Allow searching by "):].strip()
            fs = [f.strip() for f in rest.split(" or ") if f.strip()]
        return ("directive", AllowSearch(fields=fs, line=ln))
    if rule == "allow_inline_editing_line":
        r = P(text, rule)
        fs = _cl(r.get("fields")) if r else _scal(
            text[len("Allow inline editing of "):])
        return ("directive", AllowInlineEdit(fields=fs, line=ln))
    if rule == "using_line":
        # v0.9 Phase 5b.1. Strip the surrounding double quotes from
        # the target. PEG already validated the shape; the analyzer
        # parses `<ns>.<contract>` apart for validation, and the
        # lowerer attaches it to the parent ComponentNode.
        r = P(text, rule)
        target = _qs(r["target"]) if r and r.get("target") else ""
        if not target:
            # Fallback for the rare case where TatSu disagrees with
            # the literal — extract whatever's in quotes.
            m = _eqs(text)
            target = m[0] if m else ""
        return ("directive", UsingOverride(target=target, line=ln))
    if rule == "link_column_line":
        r = P(text, rule)
        if r:
            col = _qs(r.get("col", ""))
            template = _qs(r.get("template", ""))
        else:
            parts = _eqs(text)
            col = parts[0] if parts else ""
            template = parts[1] if len(parts) > 1 else ""
        return ("directive", LinkColumn(column=col, link_template=template, line=ln))
    if rule == "subscribes_to_line":
        rest = text[len("This table subscribes to "):].strip()
        if rest.endswith(" changes"): rest = rest[:-8].strip()
        return ("directive", SubscribeTo(content_name=rest, line=ln))
    if rule == "accept_input_line":
        r = P(text, rule); return ("directive", AcceptInput(fields=_cl(r.get("fields")) if r else _scal(text[len("Accept input for "):]), line=ln))
    if rule == "validate_unique_line":
        rest = text[len("Validate that "):].strip()
        if rest.startswith("["):
            be = rest.find("]"); return ("directive", ValidateUnique(condition_expr=rest[1:be].strip() if be>0 else rest[1:].strip(), line=ln))
        ii = rest.find(" is unique"); return ("directive", ValidateUnique(field=rest[:ii].strip() if ii>=0 else rest.strip(), line=ln))
    if rule == "create_as_line":
        rest = text[len("Create the "):].strip(); ai = rest.rfind(" as ")
        return ("directive", CreateAs(initial_state=rest[ai+4:].strip() if ai>=0 else "", line=ln))
    if rule == "after_saving_line":
        r = P(text, rule); return ("directive", AfterSave(instruction=str(r.get("text","")).strip() if r else text[len("After saving, "):].strip(), line=ln))
    if rule == "show_chart_line":
        rest = text[len("Show a chart of "):].strip(); oi = rest.find(" over the past ")
        if oi < 0: return ("directive", ShowChart(content_name=rest, days=30, line=ln))
        cn = rest[:oi].strip(); af = rest[oi+15:].strip(); sp = af.find(" ")
        return ("directive", ShowChart(content_name=cn, days=_si(af[:sp] if sp>0 else af, 30), line=ln))
    if rule == "display_text_line":
        r = P(text, rule)
        if r:
            if r.get("cel"): return ("directive", DisplayText(text=_qs(r["cel"]), is_expression=True, line=ln))
            if r.get("text"): return ("directive", DisplayText(text=_qs(r["text"]), line=ln))
            if r.get("expr"): return ("directive", DisplayText(text=str(r["expr"]).strip(), is_expression=True, line=ln))
        rest = text[len("Display text"):].strip(); j = _eb(rest)
        if j: return ("directive", DisplayText(text=j, is_expression=True, line=ln))
        q = _fq(rest)
        if q: return ("directive", DisplayText(text=q, line=ln))
        return ("directive", DisplayText(text=rest.strip(), is_expression=True, line=ln))
    if rule == "structured_agg_line":
        r = P(text, rule)
        if r:
            content = str(r.get("content","")).strip()
            if r.get("field"):
                return ("directive", StructuredAggregation(agg_type="count", source_content=content,
                                                            group_by=str(r["field"]).strip(), line=ln))
            if r.get("func"):
                func = str(r["func"]).strip()
                expr_val = _qs(r.get("expr","")) if r.get("expr") else None
                fmt = str(r.get("format","number")).strip() if r.get("format") else "number"
                return ("directive", StructuredAggregation(agg_type=func, source_content=content, expression=expr_val, format=fmt, line=ln))
            return ("directive", StructuredAggregation(agg_type="count", source_content=content, line=ln))
        rest = text[len("Display "):].strip()
        if rest.lower().startswith("count of"):
            content = rest[len("count of"):].strip()
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
            title = _fq(text[len("Section "):].strip().rstrip(":")) or text[len("Section "):].strip().rstrip(":")
        return ("directive", SectionStart(title=title, line=ln))
    if rule == "action_header_line":
        r = P(text, rule)
        singular = str(r.get("singular","")).strip() if r else ""
        if not singular:
            rest = text[len("For each "):].strip()
            ci = rest.find(",")
            singular = rest[:ci].strip() if ci >= 0 else ""
        return ("directive", ActionHeader(singular=singular, line=ln))
    if rule == "action_button_line":
        # TatSu 5.15.1 does not populate parseinfo.rule for #Name-tagged
        # alternatives under rule_name= dispatch, so we cannot use
        # _rule(r) to discriminate the six alternatives. Instead, we
        # inspect the source text (which is reliable) and use TatSu only
        # to extract the label/field/state content.
        r = P(text, rule)
        lower_text = text.lower()
        has_transitions = " transitions " in lower_text
        is_delete = " deletes" in lower_text and not has_transitions
        is_edit = " edits" in lower_text and not has_transitions
        if is_delete:
            kind = "delete"
        elif is_edit:
            kind = "edit"
        else:
            kind = "transition"
        behavior = "hide" if "hide otherwise" in lower_text else "disable"
        machine_name = ""
        state = ""
        if r:
            label = _qs(r.get("label",""))
            if kind == "transition":
                # New v0.9 form: `transitions <field> to <state>`
                fn = r.get("field_name")
                st = r.get("state")
                if fn:
                    machine_name = str(fn).strip()
                if st:
                    state = str(st).strip()
        else:
            label = _fq(text) or ""
        # Fallback / supplemental parsing from raw text.
        if kind == "transition" and (not state or not machine_name):
            ti = lower_text.find(" transitions ")
            if ti >= 0:
                rest = text[ti + len(" transitions "):].strip()
                # Stop at " to " for field name.
                to_idx = rest.find(" to ")
                if to_idx >= 0:
                    fn_raw = rest[:to_idx].strip()
                    after = rest[to_idx + len(" to "):].strip()
                    # State runs until " if ".
                    if_idx = after.find(" if ")
                    state_raw = (after[:if_idx] if if_idx >= 0 else after).strip()
                    if fn_raw.startswith('"') and fn_raw.endswith('"'):
                        fn_raw = fn_raw[1:-1]
                    if state_raw.startswith('"') and state_raw.endswith('"'):
                        state_raw = state_raw[1:-1]
                    if not machine_name:
                        machine_name = fn_raw
                    if not state:
                        state = state_raw
        # Strip quotes from TatSu-extracted values too.
        if state.startswith('"') and state.endswith('"'):
            state = state[1:-1]
        if machine_name.startswith('"') and machine_name.endswith('"'):
            machine_name = machine_name[1:-1]
        # v0.9 syntactic gate: a transition action button MUST name the field
        # between `transitions` and `to`. Old syntax `"Label" transitions to <state>`
        # is removed.
        if kind == "transition":
            mn = machine_name.strip()
            if mn == "to" or not mn:
                raise ValueError(
                    "v0.9 action buttons require a field name: "
                    "'\"Label\" transitions <field> to <state> if available'. "
                    "Got 'transitions to <state>' (legacy syntax)."
                )
        return ("directive", ActionButtonDef(
            label=label, target_state=state,
            machine_name=machine_name,
            unavailable_behavior=behavior, kind=kind, line=ln))
    if rule == "display_agg_line":
        r = P(text, rule); return ("directive", DisplayAggregation(description=str(r.get("text","")).strip() if r else text[len("Display "):].strip(), line=ln))
    if rule == "nav_bar_line": return ("nav_bar",)
    if rule == "nav_item_line": return ("nav_item", _build_nav(text, ln))
    if rule == "stream_line":
        rest = text[len("Stream "):].strip(); ai = rest.rfind(" at ")
        if ai < 0: return ("stream", Stream(description=rest, path="", line=ln))
        return ("stream", Stream(description=rest[:ai].strip(), path=rest[ai+4:].strip(), line=ln))
    if rule == "compute_header":
        r = P(text, rule); return ("compute_header", ComputeNode(name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "compute_shape_line": return ("compute_shape", _build_comp_shape(text))
    if rule == "compute_body_expr_line": return ("compute_body", text[1:-1].strip())
    if rule == "compute_body_multiline": return ("compute_body_multiline", text[3:-3].strip())
    if rule == "compute_access_line":
        # v0.9: only the scope-based form is canonical
        # (Anyone with "<scope>" can execute this). The bare-role form
        # `"<role>" can execute this` was removed because it had
        # different semantics (direct role check vs scope check) and
        # caused confusion in source review.
        if not text.startswith("Anyone with"):
            # Try to extract whatever was on the LHS of "can execute this"
            # so the error message can name the offending role.
            ci = text.find(" can execute this")
            offender = text[:ci].strip().strip('"') if ci >= 0 else text.strip()
            raise ValueError(
                f"`\"{offender}\" can execute this` is removed in v0.9. "
                f"Use the scope-based form: "
                f'`Anyone with "<scope>" can execute this`. '
                f"Compute access grants now match Content access grants — "
                f"both gate on scope, not role name. The role-to-scope "
                f"mapping in the Identity block determines who can "
                f"execute. See termin-roadmap.md (v0.9 backlog)."
            )
        r = P(text, rule)
        if r:
            val = _qs(r.get("role", ""))
            return ("access", AccessRule(scope=val, verbs=["execute"], line=ln))
        # TatSu fell through but text starts with "Anyone with" —
        # extract scope from quotes as a defensive fallback.
        return ("access", AccessRule(scope=_fq(text), verbs=["execute"], line=ln))
    if rule == "compute_audit_access_line":
        r = P(text, "compute_audit_access_line")
        if r:
            scope = _qs(r.get("scope", ""))
        else:
            scope = _fq(text)
        return ("compute_audit_access", scope)
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
        rest = text[len("Trigger on "):].strip()
        where_expr = None
        if " where " in rest:
            trigger_part, where_part = rest.split(" where ", 1)
            rest = trigger_part.strip()
            where_expr = _eb(where_part)
        return ("compute_trigger", rest, where_expr)
    # v0.9.2 L6 (tech design §10): `Conversation is <content>.<field>`
    # wires a conversation field to the compute's LLM context. Carries the
    # raw source spelling forward; `lower()` resolves the content singular
    # to the canonical snake_case content name. The fallback path mirrors
    # the TatSu shape exactly (a `(content, field)` pair from a `<word>.<word>`
    # token) to preserve fidelity on WSL/Linux where the TatSu state-leak
    # hits — see workspace MEMORY.md note 9.
    if rule == "compute_conversation_line":
        r = P(text, rule)
        if r is not None:
            ref = r.get("ref") or {}
            content = str(ref.get("content", "")).strip()
            field_name = str(ref.get("field", "")).strip()
        else:
            rest = text[len("Conversation is "):].strip()
            if "." in rest:
                content, field_name = rest.split(".", 1)
                content = content.strip()
                field_name = field_name.strip()
            else:
                content = rest
                field_name = ""
        return ("compute_conversation", (content, field_name))
    if rule == "compute_preconditions_line":
        return ("compute_preconditions_header",)
    if rule == "compute_postconditions_line":
        return ("compute_postconditions_header",)
    if rule == "compute_objective_line":
        rest = text[len("Objective is "):].strip()
        if rest.startswith("```") and rest.endswith("```"):
            rest = rest[3:-3].strip()
        return ("compute_objective", rest)
    # v0.9 Phase 6c (BRD #3 §6.3): non-inline Objective sourcing forms.
    if rule == "compute_objective_deploy_line":
        r = P(text, rule)
        key = _qs(r["key"]) if r and r.get("key") else ""
        if not key:
            m = _eqs(text)
            key = m[0] if m else ""
        return ("compute_objective_source",
                {"kind": "deploy_config", "key": key})
    if rule == "compute_objective_field_line":
        r = P(text, rule)
        if r and r.get("content") and r.get("field"):
            return ("compute_objective_source", {
                "kind": "field",
                "content": str(r["content"]),
                "field": str(r["field"]),
            })
        rest = text[len("Objective from "):].strip()
        if "." in rest:
            content, field = rest.split(".", 1)
            return ("compute_objective_source", {
                "kind": "field",
                "content": content.strip(),
                "field": field.strip(),
            })
        return ("compute_objective_source",
                {"kind": "field", "content": "", "field": ""})
    if rule == "compute_strategy_line":
        rest = text[len("Strategy is "):].strip()
        if rest.startswith("```") and rest.endswith("```"):
            rest = rest[3:-3].strip()
        return ("compute_strategy", rest)
    if rule == "compute_directive_line":
        rest = text[len("Directive is "):].strip()
        if rest.startswith("```") and rest.endswith("```"):
            rest = rest[3:-3].strip()
        return ("compute_directive", rest)
    # v0.9 Phase 6c (BRD #3 §6.2): non-inline Directive sourcing forms.
    if rule == "compute_directive_deploy_line":
        r = P(text, rule)
        key = _qs(r["key"]) if r and r.get("key") else ""
        if not key:
            m = _eqs(text)
            key = m[0] if m else ""
        return ("compute_directive_source",
                {"kind": "deploy_config", "key": key})
    if rule == "compute_directive_field_line":
        r = P(text, rule)
        if r and r.get("content") and r.get("field"):
            return ("compute_directive_source", {
                "kind": "field",
                "content": str(r["content"]),
                "field": str(r["field"]),
            })
        # Fallback: tokenize "Directive from <content>.<field>".
        rest = text[len("Directive from "):].strip()
        if "." in rest:
            content, field = rest.split(".", 1)
            return ("compute_directive_source", {
                "kind": "field",
                "content": content.strip(),
                "field": field.strip(),
            })
        return ("compute_directive_source",
                {"kind": "field", "content": "", "field": ""})
    if rule == "compute_accesses_line":
        r = P(text, rule)
        if r is not None:
            items = _cl(r.get("content_list"))
            return ("compute_accesses", [w.strip().strip('"') for w in items if w.strip()])
        # Fallback for any line shape TatSu can't yet handle.
        rest = text[len("Accesses "):].strip()
        items = [w.strip().strip('"') for w in rest.replace(" and ", ",").split(",") if w.strip()]
        return ("compute_accesses", items)
    # v0.9 Phase 3 slice (c) — Reads / Sends to / Emits / Invokes.
    # Same comma-or-and split as compute_accesses with a leading-prefix
    # difference and (for Sends to) a trailing 'channel' keyword that
    # the fallback strips before splitting items.
    if rule == "compute_reads_line":
        r = P(text, rule)
        if r is not None:
            items = _cl(r.get("content_list"))
            return ("compute_reads", [w.strip().strip('"') for w in items if w.strip()])
        rest = text[len("Reads "):].strip()
        items = [w.strip().strip('"') for w in rest.replace(" and ", ",").split(",") if w.strip()]
        return ("compute_reads", items)
    if rule == "compute_sends_to_line":
        r = P(text, rule)
        if r is not None:
            return ("compute_sends_to", [w for w in _cl_qw(r.get("channel_list")) if w])
        # Fallback: strip leading 'Sends to ' and trailing ' channel'.
        rest = text[len("Sends to "):].strip()
        if rest.endswith(" channel"):
            rest = rest[: -len(" channel")].strip()
        elif rest.endswith(" channels"):
            rest = rest[: -len(" channels")].strip()
        items = [w.strip().strip('"') for w in rest.replace(" and ", ",").split(",") if w.strip()]
        return ("compute_sends_to", items)
    if rule == "compute_emits_line":
        r = P(text, rule)
        if r is not None:
            return ("compute_emits", [w for w in _cl_qw(r.get("event_list")) if w])
        rest = text[len("Emits "):].strip()
        items = [w.strip().strip('"') for w in rest.replace(" and ", ",").split(",") if w.strip()]
        return ("compute_emits", items)
    if rule == "compute_invokes_line":
        r = P(text, rule)
        if r is not None:
            return ("compute_invokes", [w for w in _cl_qw(r.get("compute_list")) if w])
        rest = text[len("Invokes "):].strip()
        items = [w.strip().strip('"') for w in rest.replace(" and ", ",").split(",") if w.strip()]
        return ("compute_invokes", items)
    if rule == "compute_acts_as_line":
        r = P(text, rule)
        if r is not None:
            mode = str(r.get("mode", "")).strip().lower()
        else:
            rest = text[len("Acts as "):].strip().lower()
            mode = rest
        if mode not in ("service", "delegate"):
            return None
        return ("compute_acts_as", mode)
    if rule == "compute_input_field_line":
        rest = text[len("Input from field "):].strip()
        if "." in rest:
            parts = rest.split(".", 1)
            return ("compute_input_field", (parts[0].strip(), parts[1].strip()))
        return ("compute_input_field", (rest, ""))
    if rule == "compute_output_field_line":
        rest = text[len("Output into field "):].strip()
        if "." in rest:
            parts = rest.split(".", 1)
            return ("compute_output_field", (parts[0].strip(), parts[1].strip()))
        return ("compute_output_field", (rest, ""))
    if rule == "compute_output_creates_line":
        rest = text[len("Output creates "):].strip()
        return ("compute_output_creates", rest.strip().strip('"'))
    if rule == "content_scoped_line":
        r = P(text, rule)
        scopes = _ql(r.get("scopes")) if r else _eqs(text)
        return ("content_scoped", scopes)
    if rule == "content_audit_line":
        r = P(text, rule)
        level = str(r.get("level", "content")).strip().lower() if r else text.split(":", 1)[1].strip().lower()
        return ("content_audit", level)
    if rule == "content_when_line":
        return _parse_content_when(text, ln)
    if rule == "content_owned_by_line":
        # `Each <singular> is owned by <field>` — names which field carries
        # the owning principal's id. Per BRD #3 §3.3.
        r = P(text, rule)
        if r:
            field_name = str(r.get("field", "")).strip()
        else:
            # Fallback: split on " is owned by "
            idx = text.find(" is owned by ")
            field_name = text[idx + len(" is owned by "):].strip().rstrip(".")
        return ("content_owned_by", field_name)
    if rule == "unconditional_constraint_line":
        return _parse_unconditional_constraint(text, ln)
    if rule == "package_contract_line":
        # v0.9 Phase 5c.2: a contract-package source-verb instance.
        # The classifier already confirmed the line matches some
        # registered template — re-run the matcher here to capture
        # the qualified name and bindings. Pure-Python; no TatSu
        # involvement, so no fallback fidelity test needed (the
        # test in `tests/test_v09_package_verb_matcher.py` covers
        # the matcher directly on every platform).
        from .package_verb_matcher import match_active_packages
        match = match_active_packages(text)
        if match is None:
            # Defensive: classifier said yes but matcher says no
            # — possible if the registry was cleared between
            # classify and parse. Treat as parse error.
            raise ParseError(
                f"Line {ln}: classified as a contract-package verb "
                f"but no registered template matches",
                line=ln,
            )
        qualified, bindings = match
        # Find the matching source_verb template — needed for
        # round-tripping diagnostics. The registry's _verb_owners
        # is verb→qualified, so reverse-lookup.
        from .package_verb_matcher import get_active_registry
        reg = get_active_registry()
        template = ""
        if reg is not None:
            for verb, owner in getattr(reg, "_verb_owners", {}).items():
                if owner == qualified:
                    template = verb
                    break
        return ("directive", PackageContractCall(
            qualified_name=qualified,
            source_verb=template,
            bindings=dict(bindings),
            line=ln,
        ))
    if rule == "channel_header":
        r = P(text, rule); return ("channel_header", ChannelDecl(name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "channel_carries_line":
        r = P(text, rule); return ("channel_prop", "carries", str(r.get("content","")).strip() if r else text[len("Carries "):].strip())
    if rule == "channel_direction_line":
        r = P(text, rule); return ("channel_prop", "direction", str(r.get("dir","")).strip().lower() if r else text.split(":",1)[1].strip().lower())
    if rule == "channel_delivery_line":
        r = P(text, rule); return ("channel_prop", "delivery", str(r.get("del","")).strip().lower() if r else text.split(":",1)[1].strip().lower())
    if rule == "channel_requires_line":
        r = P(text, rule)
        if r:
            direction = str(r.get("dir","")).strip()
            return ("channel_prop", "requires", ChannelRequirement(scope=_qs(r.get("scope","")), direction=direction, line=ln))
        direction = "send" if " to send" in text else ("invoke" if " to invoke" in text else "receive")
        return ("channel_prop", "requires", ChannelRequirement(scope=_fq(text), direction=direction, line=ln))
    if rule == "channel_endpoint_line":
        r = P(text, rule); return ("channel_prop", "endpoint", str(r.get("path","")).strip() if r else text.split(":",1)[1].strip())
    if rule == "channel_failure_mode_line":
        # "Failure mode is surface-as-error" / "Failure mode is queue-and-retry" / "Failure mode is log-and-drop"
        # v0.9.1: surface-as-error implemented in reference runtime (re-raises
        # ChannelError on send failure). queue-and-retry remains a placeholder
        # — full implementation deferred to v0.10 with exponential backoff +
        # dead-letter queue + configurable max-retry-hours (24h cap).
        mode = text[len("Failure mode is"):].strip().strip('"').strip("'").lower()
        return ("channel_prop", "failure_mode", mode)
    if rule == "action_header":
        r = P(text, rule); return ("action_header", ActionDecl(name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "action_takes_line":
        params = _parse_action_params(text, "Takes ", ln)
        return ("action_prop", "takes", params)
    if rule == "action_returns_line":
        params = _parse_action_params(text, "Returns ", ln)
        return ("action_prop", "returns", params)
    if rule == "action_requires_line":
        r = P(text, rule)
        scope = _qs(r.get("scope","")) if r else _fq(text)
        return ("action_prop", "requires", scope)
    if rule == "boundary_header":
        r = P(text, rule); return ("boundary_header", BoundaryDecl(name=_qs(r.get("name","")) if r else _fq(text), line=ln))
    if rule == "boundary_contains_line":
        r = P(text, rule); return ("boundary_prop", "contains", _cl(r.get("items_") or r.get("items")) if r else _scal(text[len("Contains "):]))
    if rule == "boundary_inherits_line":
        r = P(text, rule); return ("boundary_prop", "inherits", str(r.get("parent","")).strip() if r else text[len("Identity inherits from "):].strip())
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
