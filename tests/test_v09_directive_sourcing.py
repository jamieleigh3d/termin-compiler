# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 6c: agent directive sourcing.

Per BRD #3 §6: `Directive` and `Objective` each take three forms:

  1. Inline literal — `Directive is ` + triple-backtick body (existing).
  2. Deploy-config reference — `Directive from deploy config "<key>"`.
     Resolved at application startup; reused for all invocations.
  3. Field reference — `Directive from <content>.<field>`. Read from
     the triggering record at every invocation.

The same three forms apply to `Objective`. A compute may mix forms
(e.g., deploy-config directive with inline objective).
"""

from __future__ import annotations

import pytest


_BASE_APP_HEADER = '''Application: Directive Source Test
  Description: agent directive sourcing
Id: 11111111-2222-3333-4444-555555555555

Identity:
  Scopes are "x.read", "x.write"
  Anonymous has "x.read"

Content called "sessions":
  Each session has a body which is text, required
  Each session has a system_prompt which is text
  Each session has a task_prompt which is text
  Anyone with "x.read" can view sessions
  Anyone with "x.write" can create or update sessions
'''


def _src(compute_block: str) -> str:
    return _BASE_APP_HEADER + compute_block


# ── Parse: Directive forms ──

def test_directive_inline_form_still_parses():
    from termin.peg_parser import parse_peg as parse
    src = _src('''
Compute called "agent":
  Provider is "ai-agent"
  Accesses sessions
  Trigger on event "sessions.lifecycle.draft.entered"
  Directive is ```You are a helper.```
  Anyone with "x.write" can execute this
''')
    prog, res = parse(src)
    assert not res.errors, f"parse should succeed; errors: {res.errors}"
    comp = prog.computes[0]
    assert comp.directive == "You are a helper."
    assert getattr(comp, "directive_source", None) is None


def test_directive_from_deploy_config_parses():
    from termin.peg_parser import parse_peg as parse
    src = _src('''
Compute called "ARIA":
  Provider is "ai-agent"
  Accesses sessions
  Trigger on event "sessions.lifecycle.draft.entered"
  Directive from deploy config "aria_system_prompt"
  Anyone with "x.write" can execute this
''')
    prog, res = parse(src)
    assert not res.errors, f"parse should succeed; errors: {res.errors}"
    comp = prog.computes[0]
    # Inline directive empty; source carries the key.
    assert not comp.directive
    src_ref = comp.directive_source
    assert src_ref is not None
    assert src_ref.get("kind") == "deploy_config"
    assert src_ref.get("key") == "aria_system_prompt"


def test_directive_from_field_reference_parses():
    from termin.peg_parser import parse_peg as parse
    src = _src('''
Compute called "ARIA":
  Provider is "ai-agent"
  Accesses sessions
  Trigger on event "sessions.lifecycle.draft.entered"
  Directive from sessions.system_prompt
  Anyone with "x.write" can execute this
''')
    prog, res = parse(src)
    assert not res.errors, f"parse should succeed; errors: {res.errors}"
    comp = prog.computes[0]
    assert not comp.directive
    src_ref = comp.directive_source
    assert src_ref is not None
    assert src_ref.get("kind") == "field"
    assert src_ref.get("content") == "sessions"
    assert src_ref.get("field") == "system_prompt"


# ── Parse: Objective forms (mirror of Directive) ──

def test_objective_from_deploy_config_parses():
    from termin.peg_parser import parse_peg as parse
    src = _src('''
Compute called "ARIA":
  Provider is "ai-agent"
  Accesses sessions
  Trigger on event "sessions.lifecycle.draft.entered"
  Directive is ```You are a helper.```
  Objective from deploy config "aria_objective"
  Anyone with "x.write" can execute this
''')
    prog, res = parse(src)
    assert not res.errors, f"parse should succeed; errors: {res.errors}"
    comp = prog.computes[0]
    assert not comp.objective
    src_ref = comp.objective_source
    assert src_ref is not None
    assert src_ref.get("kind") == "deploy_config"
    assert src_ref.get("key") == "aria_objective"


def test_objective_from_field_reference_parses():
    from termin.peg_parser import parse_peg as parse
    src = _src('''
Compute called "ARIA":
  Provider is "ai-agent"
  Accesses sessions
  Trigger on event "sessions.lifecycle.draft.entered"
  Directive is ```You are a helper.```
  Objective from sessions.task_prompt
  Anyone with "x.write" can execute this
''')
    prog, res = parse(src)
    assert not res.errors, f"parse should succeed; errors: {res.errors}"
    comp = prog.computes[0]
    assert not comp.objective
    src_ref = comp.objective_source
    assert src_ref is not None
    assert src_ref.get("kind") == "field"
    assert src_ref.get("content") == "sessions"
    assert src_ref.get("field") == "task_prompt"


# ── Mixed forms within a single compute ──

def test_mixed_forms_directive_deploy_objective_inline():
    from termin.peg_parser import parse_peg as parse
    src = _src('''
Compute called "ARIA":
  Provider is "ai-agent"
  Accesses sessions
  Trigger on event "sessions.lifecycle.draft.entered"
  Directive from deploy config "aria_system_prompt"
  Objective is ```Reply to the player.```
  Anyone with "x.write" can execute this
''')
    prog, res = parse(src)
    assert not res.errors, f"mixed forms should parse; errors: {res.errors}"
    comp = prog.computes[0]
    assert comp.directive_source is not None
    assert comp.directive_source.get("kind") == "deploy_config"
    assert comp.objective == "Reply to the player."
    assert getattr(comp, "objective_source", None) is None


def test_mixed_forms_directive_inline_objective_field():
    from termin.peg_parser import parse_peg as parse
    src = _src('''
Compute called "ARIA":
  Provider is "ai-agent"
  Accesses sessions
  Trigger on event "sessions.lifecycle.draft.entered"
  Directive is ```You are a helper.```
  Objective from sessions.task_prompt
  Anyone with "x.write" can execute this
''')
    prog, res = parse(src)
    assert not res.errors, f"mixed forms should parse; errors: {res.errors}"
    comp = prog.computes[0]
    assert comp.directive == "You are a helper."
    assert comp.objective_source is not None
    assert comp.objective_source.get("kind") == "field"


# ── IR / lowering ──

def test_ir_carries_directive_source():
    from termin.peg_parser import parse_peg as parse
    from termin.lower import lower
    src = _src('''
Compute called "ARIA":
  Provider is "ai-agent"
  Accesses sessions
  Trigger on event "sessions.lifecycle.draft.entered"
  Directive from deploy config "aria_system_prompt"
  Objective from sessions.task_prompt
  Anyone with "x.write" can execute this
''')
    prog, res = parse(src)
    assert not res.errors
    spec = lower(prog)
    comp_spec = spec.computes[0]
    assert comp_spec.directive_source is not None
    assert comp_spec.directive_source["kind"] == "deploy_config"
    assert comp_spec.directive_source["key"] == "aria_system_prompt"
    assert comp_spec.objective_source is not None
    assert comp_spec.objective_source["kind"] == "field"
    assert comp_spec.objective_source["content"] == "sessions"
    assert comp_spec.objective_source["field"] == "task_prompt"


# ── Runtime resolution ──

def test_runtime_resolves_directive_from_deploy_config_at_startup():
    """When a compute declares Directive from deploy config "<key>",
    the runtime reads the value from deploy_config at app startup
    and surfaces it as `comp["directive"]` to compute_runner."""
    from termin_server.app import _resolve_directive_sources

    deploy_config = {"aria_system_prompt": "You are ARIA, a diagnostic AI."}
    comp = {
        "name": "ARIA",
        "directive": "",
        "directive_source": {"kind": "deploy_config", "key": "aria_system_prompt"},
        "objective": "",
        "objective_source": None,
    }
    _resolve_directive_sources([comp], deploy_config)
    assert comp["directive"] == "You are ARIA, a diagnostic AI."


def test_runtime_deploy_config_missing_key_leaves_directive_empty():
    """Missing key resolves to empty string and the runtime continues
    — the prompt-build path skips empty directives gracefully today."""
    from termin_server.app import _resolve_directive_sources

    deploy_config = {}
    comp = {
        "name": "ARIA",
        "directive": "",
        "directive_source": {"kind": "deploy_config", "key": "missing_key"},
        "objective": "",
        "objective_source": None,
    }
    _resolve_directive_sources([comp], deploy_config)
    assert comp["directive"] == ""


def test_runtime_resolves_objective_from_field_at_invocation():
    """Field-ref form reads from the triggering record at each
    invocation. The compute_runner picks up the value from
    `record[<field>]` when objective_source.kind == "field"."""
    from termin_server.compute_runner import _resolve_directive_at_invocation

    comp = {
        "name": "ARIA",
        "directive": "",
        "directive_source": None,
        "objective": "",
        "objective_source": {
            "kind": "field",
            "content": "sessions",
            "field": "task_prompt",
        },
    }
    record = {"id": 1, "task_prompt": "Reply to the player's message."}
    resolved_directive, resolved_objective = _resolve_directive_at_invocation(
        comp, record,
    )
    assert resolved_directive == ""
    assert resolved_objective == "Reply to the player's message."


def test_runtime_field_ref_missing_field_resolves_to_empty_string():
    """A record missing the named field resolves to empty rather
    than blowing up — same forgiving stance as deploy-config."""
    from termin_server.compute_runner import _resolve_directive_at_invocation

    comp = {
        "name": "ARIA",
        "directive": "",
        "directive_source": {
            "kind": "field",
            "content": "sessions",
            "field": "system_prompt",
        },
        "objective": "",
        "objective_source": None,
    }
    record = {"id": 1}
    resolved_directive, resolved_objective = _resolve_directive_at_invocation(
        comp, record,
    )
    assert resolved_directive == ""


def test_inline_form_passes_through_without_resolution():
    """Inline-literal directives are already resolved at parse time;
    the runtime helpers leave them alone."""
    from termin_server.app import _resolve_directive_sources
    from termin_server.compute_runner import _resolve_directive_at_invocation

    comp = {
        "name": "agent",
        "directive": "Inline directive.",
        "directive_source": None,
        "objective": "Inline objective.",
        "objective_source": None,
    }
    _resolve_directive_sources([comp], {})
    assert comp["directive"] == "Inline directive."
    d, o = _resolve_directive_at_invocation(comp, {"id": 1})
    assert d == "Inline directive."
    assert o == "Inline objective."
