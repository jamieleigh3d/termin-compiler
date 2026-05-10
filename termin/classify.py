# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Line classifier — maps raw DSL lines to PEG rule names.

Level 1 of the two-level parser: prefix matching + disambiguation
to determine which PEG grammar rule should parse each line.
"""

# Prefix → rule mapping (order matters: first match wins)
_PREFIXES: list[tuple[str, str]] = [
    ("Application:", "application_line"), ("Description:", "description_line"), ("Id:", "id_line"),
    ("Users authenticate with", "identity_line"), ("Scopes are", "scopes_line"),
    ("Content called", "content_header"), ("Scoped to", "content_scoped_line"), ("Audit level:", "content_audit_line"), ("Each ", "field_line"),
    ("Anyone with", "access_line"),
    ("When `", "event_expr_line"), ("When [", "event_expr_line"),  # backtick first, bracket legacy  # disambiguated in _classify_line for content When
    ("When a ", "event_v1_line"), ("When an ", "event_v1_line"),
    ("Create a ", "event_action_line"), ("Create an ", "event_action_line"),
    ("Send ", "event_send_line"),
    ("Log level:", "log_level_line"), ("On error from", "error_from_line"),
    ("On any error:", "error_catch_all_line"), ("Retry ", "error_retry_line"),
    ("Then ", "error_then_line"), ("As ", "story_header"), ("so that ", "so_that_line"),
    ("Show a chat for", "chat_line"),
    ("Show a page called", "show_page_line"), ("Display a table of", "display_table_line"),
    ("For each ", "show_related_line"),  # also handles action_header_line — disambiguated below
    ("Mark ", "mark_rows_line"),
    ("Highlight rows where", "highlight_rows_line"),
    ("Allow filtering by", "allow_filtering_line"), ("Allow searching by", "allow_searching_line"),
    ("Allow inline editing of", "allow_inline_editing_line"),
    ("Link ", "link_column_line"),
    # v0.9 Phase 5b.1: presentation contract override sub-clause.
    ("Using ", "using_line"),
    ("This table subscribes to", "subscribes_to_line"), ("Accept input for", "accept_input_line"),
    ("Validate that", "validate_unique_line"), ("Create the ", "create_as_line"),
    ("After saving,", "after_saving_line"), ("Show a chart of", "show_chart_line"),
    ("Section ", "section_header_line"),
    ("Display text", "display_text_line"),
    ("Display count of", "structured_agg_line"),
    ("Display sum of", "structured_agg_line"),
    ("Display average of", "structured_agg_line"),
    ("Display minimum of", "structured_agg_line"),
    ("Display maximum of", "structured_agg_line"),
    ("Display ", "display_agg_line"),
    ("Navigation bar:", "nav_bar_line"),
    ("Stream ", "stream_line"), ("Compute called", "compute_header"),
    ("Channel called", "channel_header"), ("Carries ", "channel_carries_line"),
    ("Direction:", "channel_direction_line"), ("Delivery:", "channel_delivery_line"),
    ("Requires ", "channel_requires_line"),  # disambiguated below for Compute context
    ("Endpoint:", "channel_endpoint_line"),
    ("Failure mode is", "channel_failure_mode_line"),  # v0.9 Phase 4
    ("Action called", "action_header"),
    ("Takes ", "action_takes_line"), ("Returns ", "action_returns_line"),
    ("Boundary called", "boundary_header"), ("Contains ", "boundary_contains_line"),
    ("Identity inherits", "boundary_inherits_line"), ("Identity restricts", "boundary_restricts_line"),
    ("Identity:", "compute_identity_line"),
    ("Provider is", "compute_provider_line"),
    ("Accesses ", "compute_accesses_line"),
    # v0.9 Phase 3 slice (c): full access-grant grammar.
    ("Reads ", "compute_reads_line"),
    ("Sends to ", "compute_sends_to_line"),
    ("Emits ", "compute_emits_line"),
    ("Invokes ", "compute_invokes_line"),
    ("Acts as ", "compute_acts_as_line"),
    ("Input from field", "compute_input_field_line"),
    ("Output into field", "compute_output_field_line"),
    ("Output creates", "compute_output_creates_line"),
    ("Output confidentiality:", "compute_output_conf_line"),
    # Phase 6c (BRD #3 §6.2): three Directive sourcing forms. The
    # `from deploy config` prefix must be checked before the bare
    # `from` prefix; the `is` form stays as the legacy fallback.
    ("Directive from deploy config", "compute_directive_deploy_line"),
    ("Directive from", "compute_directive_field_line"),
    ("Directive is", "compute_directive_line"),
    ("Trigger on", "compute_trigger_line"),
    # v0.9.2 L6: `Conversation is <content>.<field>` wires conversation
    # context for an ai-agent compute. See termin.peg compute_conversation_line.
    ("Conversation is", "compute_conversation_line"),
    ("Preconditions are:", "compute_preconditions_line"),
    ("Postconditions are:", "compute_postconditions_line"),
    # Phase 6c (BRD #3 §6.3): same three Objective sourcing forms.
    ("Objective from deploy config", "compute_objective_deploy_line"),
    ("Objective from", "compute_objective_field_line"),
    ("Objective is", "compute_objective_line"),
    ("Strategy is", "compute_strategy_line"),
    ("Exposes property", "boundary_exposes_line"),
]

_SHAPE_KW = ("Transform:", "Reduce:", "Expand:", "Correlate:", "Route:")


def classify_line(text: str) -> str:
    """Classify a DSL line to determine which PEG rule to use.

    Returns the rule name string, or "unknown" if unrecognized.
    """
    # v0.9 Phase 1: top-level `Identity:` (exact, bare) opens the
    # Identity sub-block. Must dispatch before the prefix loop so
    # the existing ('Identity:', 'compute_identity_line') prefix
    # entry doesn't shadow it. Indented `Identity: service` (with a
    # mode word after) inside Compute blocks still classifies as
    # compute_identity_line via the prefix loop.
    if text == "Identity:": return "identity_block_open_line"
    # Transition feedback must be checked early — CEL messages can contain " has " which triggers role_bare_line
    if text.startswith(("success ", "error ")) and " shows " in text: return "transition_feedback_line"
    if text.startswith('"') and " is alias for " in text: return "role_alias_line"
    if text.startswith(('A "', 'An "')) and " has " in text: return "role_standard_line"
    # role_bare_line heuristic: ``<bare-role> has <quoted-scopes>`` per
    # termin.peg line 71-73. The exclusion list keeps the heuristic
    # from racing past other constructs that legitimately contain
    # ``" has "`` and ``"`` in their bodies. v0.9.4 additions:
    #   * `" can become "` — state-machine transition lines like
    #     ``X can become Y if the user has "scope"`` (Gap #1 from
    #     Airlock-on-Termin slice A3a authoring).
    #   * Directive/Strategy/Objective `is `` ``...```` blocks
    #     joined by the preprocessor — the joined body almost always
    #     contains both ``"`` (quoted phrases) and ``has`` (in
    #     prose), and the prefix-loop entry below would route them
    #     correctly if not for this early-return (Gap #2).
    if (
        " has " in text
        and '"' in text
        and not text.startswith(
            ("A ", "An ", '"', "Content", "Each",
             "Directive ", "Strategy ", "Objective ")
        )
        and " can become " not in text
    ):
        return "role_bare_line"
    # v0.9: inline state machine sub-block lines.
    # `<field> starts as <state>`, `<field> can also be <list>`, `<from> can become <to> if ...`
    # These are only valid inside `which is state:` sub-blocks; the assembler enforces
    # that they sit under a state-typed field. Classification by structure is safe
    # because no other DSL construct uses these phrasings.
    # v0.9 Phase 6a.2: content-level ownership declaration. Must be checked
    # before the prefix loop so the leading "Each " doesn't classify as
    # field_line. Form: `Each <singular> is owned by <field>`.
    if text.startswith("Each ") and " is owned by " in text:
        return "content_owned_by_line"
    if " starts as " in text:
        return "sm_starts_as_line"
    if " can also be " in text:
        return "sm_also_line"
    if " can become " in text and " if " in text:
        return "sm_transition_line"
    # "can execute this" must be checked BEFORE the prefix loop — lines like
    # 'Anyone with "scope" can execute this' match the "Anyone with" prefix
    # and would be misclassified as access_line instead of compute_access_line
    if " can execute this" in text: return "compute_access_line"
    # D-20: "can audit" inside Compute blocks — must also be checked before prefix loop
    if " can audit" in text and text.startswith("Anyone with"): return "compute_audit_access_line"
    # v0.9.2 L3: "can append to <plural>' <field>" — field-targeted
    # permission, distinct from content-level access_line. Must be
    # checked before the "Anyone with" prefix routes to access_line
    # (which only knows view/create/update/delete verbs).
    if " can append to " in text and text.startswith("Anyone with"):
        return "access_append_line"
    # v0.9.2 L3: source-level Append action verb. Distinct prefix
    # ("Append to ") so it doesn't collide with anything else. Used
    # in compute bodies, page form handlers, When-rule actions (L8).
    if text.startswith("Append to "): return "append_action_line"
    for prefix, rule in _PREFIXES:
        if text.startswith(prefix):
            # Disambiguate "For each X, show actions:" from "For each X, show Y grouped by Z"
            if rule == "show_related_line" and "show actions" in text.lower():
                return "action_header_line"
            # Disambiguate "Requires" — channel/action (has "to send/receive/invoke") vs compute confidentiality
            if rule == "channel_requires_line" and " to send" not in text and " to receive" not in text and " to invoke" not in text:
                return "compute_requires_conf_line"
            # Disambiguate "When `expr`" — content dependent value vs event trigger
            # Content When: "When `expr`, field must be..." or "When `expr`, field defaults to..."
            if rule == "event_expr_line" and (text.startswith("When `") or text.startswith("When [")):
                if " must be " in text or " defaults to " in text:
                    bt_close = text.find("`", 6) if text.startswith("When `") else text.find("]", 6)
                    if bt_close >= 0 and "," in text[bt_close:bt_close+3]:
                        return "content_when_line"
            return rule
    # v0.9: action button form is `"Label" transitions <field> to <state> if available`.
    # The `to` token sits between field name and target state, so we look for
    # ` transitions ` and a downstream ` if available`.
    if text.startswith('"') and " transitions " in text and " if available" in text:
        return "action_button_line"
    if text.startswith('"') and " deletes" in text and " if available" in text: return "action_button_line"
    if text.startswith('"') and " edits" in text and " if available" in text: return "action_button_line"
    if text.startswith('"') and " links to " in text: return "nav_item_line"
    if any(text.startswith(kw) for kw in _SHAPE_KW): return "compute_shape_line"
    if text.startswith("```") and text.endswith("```") and len(text) > 6: return "compute_body_multiline"
    if text.startswith("`") and text.endswith("`") and not text.startswith("```"): return "compute_body_expr_line"
    if text.startswith("[") and text.endswith("]"): return "compute_body_expr_line"  # legacy bracket support
    # D-19: Unconditional constraint: "field must be one of: ..."
    if " must be one of:" in text: return "unconditional_constraint_line"
    # v0.9 Phase 5c.2: contract-package source-verb dispatch. After
    # the legacy prefix loop and special-case checks fall through,
    # consult the active contract-package registry — when set,
    # it can route lines like `Show a cosmic orb of scenarios` to
    # the package-contract handler. The matcher returns None when
    # no registry is active or no verb template matches; the
    # caller stays on the legacy "unknown" path in that case.
    from .package_verb_matcher import match_active_packages
    if match_active_packages(text) is not None:
        return "package_contract_line"
    return "unknown"
