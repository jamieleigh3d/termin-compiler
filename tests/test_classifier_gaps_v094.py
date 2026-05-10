# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Classifier-gap tests surfaced by Airlock-on-Termin slice A3a authoring.

Two early-return heuristics in ``classify.py`` race ahead of the
prefix-loop dispatch in ways that bite real authoring:

  Gap #1 — quoted scopes in state transitions misclassify.
    ``survey can become scenario if the user has "play"``
    matches the ``" has " + '"'`` heuristic at line ~110 and gets
    classified ``role_bare_line`` before the line ever reaches the
    ``sm_transition_line`` check at line ~126. The analyzer then
    reports the destination state as unreachable. The bare-scope
    workaround (``if the user has play``) compiles, but the natural
    quoted form is what every other access rule in the codebase uses
    — denying it in transitions is an unprincipled inconsistency.

  Gap #2 — quotes in Directive body misclassify the joined block.
    ``Directive is ```...```'' opens a triple-backtick multi-line
    block that the preprocessor joins into one line. If the joined
    text contains both ``" has "`` and ``"`` (almost any real
    directive does), the same heuristic above misclassifies the
    whole compute block. The prefix-loop entry ``("Directive is",
    "compute_directive_line")`` would route correctly, but it comes
    AFTER the heuristic.

Both fixes are localized to ``classify.py``: either the heuristic
needs structural exclusions (``" can become "`` and a Directive/
Strategy/Objective prefix list), or the prefix loop must run
first. We add the structural exclusions because they're more
narrowly-scoped — moving the prefix loop ahead of the heuristic
might shadow other intentional overrides we haven't audited.
"""

from __future__ import annotations

from termin.classify import classify_line


class TestStateTransitionWithQuotedScope:
    """Gap #1: ``X can become Y if the user has "scope"`` must be
    classified as ``sm_transition_line``, not ``role_bare_line``,
    regardless of whether the scope is quoted or bare."""

    def test_quoted_scope_classifies_as_transition(self):
        line = 'survey can become scenario if the user has "play"'
        assert classify_line(line) == "sm_transition_line"

    def test_bare_scope_still_classifies_as_transition(self):
        line = "survey can become scenario if the user has play"
        assert classify_line(line) == "sm_transition_line"

    def test_dotted_scope_classifies_as_transition(self):
        line = 'open can become in_progress if the user has "tickets.manage"'
        assert classify_line(line) == "sm_transition_line"

    def test_multi_word_state_with_quoted_scope(self):
        """Real-world: helpdesk-style multi-word state names."""
        line = 'in progress can become waiting on customer if the user has "tickets.manage"'
        assert classify_line(line) == "sm_transition_line"

    def test_cel_condition_transition_classifies_as_transition(self):
        """The CEL-condition form must also reach sm_transition_line.
        Whether the parser then handles it correctly is a separate
        gap (#3); classification must succeed regardless."""
        line = "scenario can become scoring if `session.hatch_unlocked`"
        assert classify_line(line) == "sm_transition_line"


class TestDirectiveBlockClassification:
    """Gap #2: ``Directive is ```...```'' (joined to one line by the
    preprocessor) must classify as ``compute_directive_line`` no
    matter what the body contains. Same for Strategy and Objective."""

    def test_directive_with_quotes_in_body(self):
        """The joined-block typically contains both quoted phrases
        (referring to user input or example commands) and the word
        ``has`` (in prose like 'you have access to'). The
        heuristic at line ~110 catches both and misclassifies."""
        line = (
            'Directive is ```You are an assistant. The user has access to '
            'tools called "scan" and "fix". Be helpful.```'
        )
        assert classify_line(line) == "compute_directive_line"

    def test_directive_with_only_quotes(self):
        line = 'Directive is ```Use the "scan" tool when asked.```'
        assert classify_line(line) == "compute_directive_line"

    def test_directive_with_only_has(self):
        """No quotes — should pass the heuristic but exercise the
        prefix-loop dispatch nonetheless."""
        line = "Directive is ```The user has admin rights.```"
        assert classify_line(line) == "compute_directive_line"

    def test_strategy_with_quotes_in_body(self):
        """Same root cause should also apply to Strategy."""
        line = (
            'Strategy is ```Step 1: scan. Step 2: if the user has admin '
            'access then call "elevated_repair" else call "repair".```'
        )
        assert classify_line(line) == "compute_strategy_line"

    def test_objective_with_quotes_in_body(self):
        """Same root cause should also apply to Objective."""
        line = (
            'Objective is ```Find issues the user has flagged. Treat '
            '"critical" severity as urgent.```'
        )
        assert classify_line(line) == "compute_objective_line"

    def test_directive_from_deploy_config_still_works(self):
        """Sourced-Directive forms (which don't have a body, just a
        deploy-config key) must still classify correctly. Regression
        guard for the fix."""
        line = 'Directive from deploy config "ai.aria.system_prompt"'
        assert (
            classify_line(line) == "compute_directive_deploy_line"
        )

    def test_directive_from_field_still_works(self):
        line = "Directive from sessions.aria_system_prompt"
        assert classify_line(line) == "compute_directive_field_line"


class TestRoleLineHeuristicNotRegressed:
    """The fix MUST NOT break the legitimate role_bare_line cases the
    heuristic was originally written to catch. ``role_bare_line``
    matches ``<bare-word> has <quoted-scopes>`` per termin.peg
    (line 71-73). The canonical example is Identity-block lines like
    ``Anonymous has "play"`` in `agent_chatbot.termin` and similar.
    Lines starting with ``"<role-name>"`` route to ``role_standard_line``
    or stay ``unknown`` per the existing exclusion."""

    def test_anonymous_role_classifies(self):
        """The most common form — bare role-name + quoted scopes."""
        line = 'Anonymous has "play"'
        assert classify_line(line) == "role_bare_line"

    def test_bare_role_with_two_scopes(self):
        line = 'Operator has "ops.read" and "ops.write"'
        assert classify_line(line) == "role_bare_line"

    def test_bare_role_with_three_scopes(self):
        line = 'Manager has "read", "write", and "admin"'
        assert classify_line(line) == "role_bare_line"

    def test_quoted_role_name_still_routes_to_standard(self):
        """A quoted role name (the article + quoted form) routes to
        role_standard_line via its own check at line 109. Regression
        guard that the fix doesn't break that path."""
        line = 'A "order clerk" has "orders.read" and "orders.write"'
        assert classify_line(line) == "role_standard_line"
