# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 3 slice (e): refusal + Acts as + sidecar.

Covers:
  - `Acts as service|delegate` grammar parses to `identity_mode`.
  - Default identity_mode is "delegate" when the line is absent.
  - `compute_refusals` Content type auto-generated for apps with at
    least one ai-agent compute, NOT generated otherwise.
  - The sidecar carries the BRD §6.3.7 fields (compute_name,
    invocation_id, reason, refused_at, principal info).
  - `system_refuse` tool schema appears in the agent's tool surface.
  - The audit Content's `outcome` enum includes "refused".
"""

from __future__ import annotations

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower


def _compile(src: str):
    prog, _ = parse(src)
    result = analyze(prog)
    assert result.ok, [str(e) for e in result.errors]
    return prog, lower(prog)


def _find(spec, snake: str):
    for cs in spec.content:
        if cs.name.snake == snake:
            return cs
    return None


_AGENT_SOURCE_DEFAULT = '''Application: A
  Description: x
Identity:
  Scopes are "x.write", "audit.x"
  An "u" has "x.write"

Content called "messages":
  Each message has a body which is text, required
  Anyone with "x.write" can view, create, update, or delete messages

Compute called "moderator":
  Provider is "ai-agent"
  Accesses messages
  Trigger on event "messages.created"
  Directive is ```you are a moderator```
  Objective is ```review```
  Anyone with "x.write" can execute this
  Anyone with "audit.x" can audit
'''


_AGENT_SOURCE_SERVICE = _AGENT_SOURCE_DEFAULT.replace(
    "Compute called \"moderator\":\n  Provider is \"ai-agent\"\n  Accesses messages\n",
    "Compute called \"moderator\":\n  Provider is \"ai-agent\"\n  Acts as service\n  Accesses messages\n",
)


_LLM_SOURCE = '''Application: A
  Description: x
Identity:
  Scopes are "x.write", "audit.x"
  An "u" has "x.write"

Content called "things":
  Each thing has a name which is text, required
  Each thing has a result which is text
  Anyone with "x.write" can view, create, update, or delete things

Compute called "summarize":
  Provider is "llm"
  Accesses things
  Input from field thing.name
  Output into field thing.result
  Directive is ```be brief```
  Objective is ```summarize```
  Anyone with "x.write" can execute this
'''


# ── Acts as grammar ──


class TestActsAsGrammar:
    def test_default_identity_mode_is_delegate(self):
        prog, _ = _compile(_AGENT_SOURCE_DEFAULT)
        assert prog.computes[0].identity_mode == "delegate"

    def test_acts_as_service(self):
        prog, _ = _compile(_AGENT_SOURCE_SERVICE)
        assert prog.computes[0].identity_mode == "service"

    def test_acts_as_delegate_explicit(self):
        src = _AGENT_SOURCE_DEFAULT.replace(
            "Accesses messages",
            "Acts as delegate\n  Accesses messages",
        )
        prog, _ = _compile(src)
        assert prog.computes[0].identity_mode == "delegate"

    def test_invalid_acts_as_value_does_not_match(self):
        """`Acts as bogus` doesn't match the rule — line falls
        through and parses with default identity_mode."""
        src = _AGENT_SOURCE_DEFAULT.replace(
            "Accesses messages",
            "Acts as bogus\n  Accesses messages",
        )
        # Should still parse the rest of the compute (the bogus line
        # is rejected at classifier/handler level), or produce an
        # error. Either way, parse should not crash.
        prog, errs = parse(src)
        # Parser tolerates the line as unparsed; identity_mode stays
        # at its default value.
        assert prog.computes[0].identity_mode == "delegate"


# ── compute_refusals sidecar ──


class TestComputeRefusalsSidecar:
    def test_generated_for_ai_agent_app(self):
        _, spec = _compile(_AGENT_SOURCE_DEFAULT)
        sidecar = _find(spec, "compute_refusals")
        assert sidecar is not None, (
            "Apps with at least one ai-agent compute must "
            "auto-generate the compute_refusals sidecar"
        )

    def test_not_generated_for_llm_only_app(self):
        _, spec = _compile(_LLM_SOURCE)
        sidecar = _find(spec, "compute_refusals")
        assert sidecar is None, (
            "Apps with no ai-agent computes do not need the sidecar"
        )

    def test_sidecar_fields(self):
        _, spec = _compile(_AGENT_SOURCE_DEFAULT)
        sidecar = _find(spec, "compute_refusals")
        names = {f.name for f in sidecar.fields}
        expected = {
            "compute_name",
            "invocation_id",
            "reason",
            "refused_at",
            "invoked_by_principal_id",
            "on_behalf_of_principal_id",
        }
        assert expected.issubset(names), (
            f"Missing sidecar fields: {expected - names}"
        )

    def test_sidecar_grants_audit_scope(self):
        """Anyone with the agent's audit scope can VIEW + AUDIT
        the sidecar."""
        _, spec = _compile(_AGENT_SOURCE_DEFAULT)
        from termin.ir import Verb
        sidecar_grants = [
            g for g in spec.access_grants
            if g.content == "compute_refusals"
        ]
        assert sidecar_grants, "no grants for compute_refusals sidecar"
        scopes = {g.scope for g in sidecar_grants}
        assert "audit.x" in scopes


# ── Outcome enum / system_refuse tool surface ──


class TestRefusalAuditWiring:
    def test_audit_outcome_enum_includes_refused(self):
        _, spec = _compile(_AGENT_SOURCE_DEFAULT)
        audit = _find(spec, "compute_audit_log_moderator")
        outcome = next(f for f in audit.fields if f.name == "outcome")
        assert "refused" in set(outcome.enum_values)

    def test_audit_has_refusal_reason_column(self):
        _, spec = _compile(_AGENT_SOURCE_DEFAULT)
        audit = _find(spec, "compute_audit_log_moderator")
        names = {f.name for f in audit.fields}
        assert "refusal_reason" in names

    def test_build_agent_tools_includes_system_refuse(self):
        from termin_runtime.ai_provider import build_agent_tools
        tools = build_agent_tools(["messages"], {})
        names = [t["name"] for t in tools]
        assert "system_refuse" in names
        refuse = next(t for t in tools if t["name"] == "system_refuse")
        assert "reason" in refuse["input_schema"]["properties"]
        assert "reason" in refuse["input_schema"]["required"]


# ── Refusal end-to-end via tool invocation ──


class TestRefusalEndToEnd:
    """Drive the agent loop with a stub-shaped legacy provider that
    invokes system_refuse, and verify (a) the audit record has
    outcome=refused, (b) compute_refusals row exists, (c) the
    refusal event fires on the bus."""

    def test_refusal_writes_sidecar_and_audit(self, tmp_path):
        import asyncio
        from termin_runtime.context import RuntimeContext
        from termin_runtime.compute_runner import _execute_agent_compute
        from termin_runtime.storage import get_db, init_db, list_records
        from termin_runtime.events import EventBus

        # Build a synthetic comp dict + audit + sidecar schemas.
        audit_ref = "compute_audit_log_moderator"

        def _f(name, business_type, column_type, enum_values=None):
            d = {
                "name": name, "display_name": name.replace("_", " "),
                "business_type": business_type,
                "column_type": column_type,
            }
            if enum_values is not None:
                d["enum_values"] = list(enum_values)
            return d

        audit_schema = {
            "name": {
                "display": audit_ref.replace("_", " "),
                "snake": audit_ref, "pascal": "ComputeAuditLogModerator",
            },
            "singular": audit_ref,
            "audit": "none",
            "verbs": [],
            "fields": [
                _f("compute_name", "text", "TEXT"),
                _f("invocation_id", "text", "TEXT"),
                _f("trigger", "text", "TEXT"),
                _f("started_at", "datetime", "TIMESTAMP"),
                _f("completed_at", "datetime", "TIMESTAMP"),
                _f("latency_ms", "number", "REAL"),
                _f("outcome", "enum", "TEXT",
                   ("success", "refused", "error", "timeout", "cancelled")),
                _f("total_input_tokens", "number", "INTEGER"),
                _f("total_output_tokens", "number", "INTEGER"),
                _f("trace", "text", "TEXT"),
                _f("error_message", "text", "TEXT"),
                _f("invoked_by_principal_id", "text", "TEXT"),
                _f("invoked_by_display_name", "text", "TEXT"),
                _f("on_behalf_of_principal_id", "text", "TEXT"),
                _f("provider_product", "text", "TEXT"),
                _f("model_identifier", "text", "TEXT"),
                _f("provider_config_hash", "text", "TEXT"),
                _f("prompt_as_sent", "text", "TEXT"),
                _f("sampling_params", "text", "TEXT"),
                _f("tool_calls", "text", "TEXT"),
                _f("refusal_reason", "text", "TEXT"),
                _f("cost_units", "number", "INTEGER"),
                _f("cost_unit_type", "text", "TEXT"),
                _f("cost_currency_amount", "text", "TEXT"),
            ],
        }
        sidecar_schema = {
            "name": {
                "display": "compute refusals",
                "snake": "compute_refusals",
                "pascal": "ComputeRefusals",
            },
            "singular": "compute_refusals",
            "audit": "none",
            "verbs": [],
            "fields": [
                _f("compute_name", "text", "TEXT"),
                _f("invocation_id", "text", "TEXT"),
                _f("reason", "text", "TEXT"),
                _f("refused_at", "datetime", "TIMESTAMP"),
                _f("invoked_by_principal_id", "text", "TEXT"),
                _f("on_behalf_of_principal_id", "text", "TEXT"),
            ],
        }

        comp = {
            "name": {"display": "moderator", "snake": "moderator"},
            "provider": "ai-agent",
            "audit_level": "actions",
            "audit_content_ref": audit_ref,
            "directive": "be helpful",
            "objective": "do work",
            "accesses": [],
            "reads": [],
            "input_fields": [],
            "output_fields": [],
        }

        db_path = str(tmp_path / "refusal.db")
        ctx = RuntimeContext()
        ctx.db_path = db_path
        ctx.event_bus = EventBus()
        ctx.ir = {"content": [audit_schema, sidecar_schema], "computes": [comp]}
        ctx.content_lookup = {"compute_audit_log_moderator": audit_schema,
                              "compute_refusals": sidecar_schema}
        ctx.singular_lookup = {}
        ctx.sm_lookup = {}

        # Stub provider that invokes system_refuse via execute_tool
        # then returns a "completed" set_output result. is_configured
        # is True so the runtime doesn't skip.
        class _StubLegacy:
            async def agent_loop(self, system, user, tools, execute_tool):
                # Agent calls system_refuse, then set_output to terminate.
                await execute_tool("system_refuse", {"reason": "not allowed"})
                return {"thinking": "refused", "response": ""}

        class _StubProvider:
            is_configured = True
            service = "stub"
            model = "stub-1"
            legacy = _StubLegacy()
            _config_hash = "sha256:stub"

        ctx.compute_providers = {"moderator": _StubProvider()}

        # Capture refusal events.
        events_seen = []

        async def _capture():
            queue = ctx.event_bus.subscribe(channel_id="compute.moderator.refused")
            try:
                while True:
                    ev = await queue.get()
                    events_seen.append(ev)
            except asyncio.CancelledError:
                pass

        async def _run():
            await init_db([audit_schema, sidecar_schema], db_path)
            tap = asyncio.create_task(_capture())
            await _execute_agent_compute(
                ctx, comp, record={"id": 1}, content_name="messages",
                main_loop=None,
            )
            await asyncio.sleep(0.05)  # let event drain
            tap.cancel()
            try:
                await tap
            except asyncio.CancelledError:
                pass
            db = await get_db(db_path)
            try:
                audit_rows = await list_records(db, audit_ref)
                refusal_rows = await list_records(db, "compute_refusals")
            finally:
                await db.close()
            return audit_rows, refusal_rows

        audit_rows, refusal_rows = asyncio.run(_run())

        # Audit row has outcome=refused, refusal_reason populated.
        assert len(audit_rows) == 1
        ar = audit_rows[0]
        assert ar["outcome"] == "refused"
        assert ar["refusal_reason"] == "not allowed"

        # Sidecar row exists with the reason.
        assert len(refusal_rows) == 1
        rr = refusal_rows[0]
        assert rr["reason"] == "not allowed"
        assert rr["invocation_id"] == ar["invocation_id"]

        # Refusal event fired on the bus.
        assert len(events_seen) >= 1
        assert events_seen[0]["data"]["reason"] == "not allowed"
