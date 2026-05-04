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


# ── compute_refusals sidecar — RETIRED in v0.9.2 L7.5 ──
#
# The sidecar Content type that Phase 3 slice (e) auto-generated for
# every ai-agent app is gone. The WARN-level audit log entry is the
# audit-trail surface; L7.4 appends a kind="assistant", type="refusal"
# conversation entry as the chat surface (where the compute has a
# Conversation source). These tests confirm the absence + that the
# audit path still fires.


class TestComputeRefusalsSidecarRetired:
    def test_sidecar_not_generated_for_ai_agent_app(self):
        """v0.9.2 L7.5: the compute_refusals sidecar is no longer
        auto-generated for ai-agent apps. Replaced by audit log +
        conversation refusal entry."""
        _, spec = _compile(_AGENT_SOURCE_DEFAULT)
        sidecar = _find(spec, "compute_refusals")
        assert sidecar is None, (
            "compute_refusals sidecar should be retired in v0.9.2"
        )

    def test_sidecar_not_generated_for_llm_only_app(self):
        """LLM-only apps already had no sidecar; this stays the same."""
        _, spec = _compile(_LLM_SOURCE)
        sidecar = _find(spec, "compute_refusals")
        assert sidecar is None

    def test_no_compute_refusals_routes_emitted(self):
        """The /api/v1/compute_refusals routes are gone too."""
        _, spec = _compile(_AGENT_SOURCE_DEFAULT)
        bad_routes = [
            r for r in spec.routes
            if "compute_refusals" in r.path or r.content_ref == "compute_refusals"
        ]
        assert bad_routes == [], (
            f"compute_refusals routes should be retired; got {bad_routes!r}"
        )

    def test_no_compute_refusals_grants_emitted(self):
        """Access grants for the retired sidecar should be gone."""
        _, spec = _compile(_AGENT_SOURCE_DEFAULT)
        bad_grants = [
            g for g in spec.access_grants
            if g.content == "compute_refusals"
        ]
        assert bad_grants == [], (
            f"compute_refusals grants should be retired; got {bad_grants!r}"
        )


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
        from termin_server.ai_provider import build_agent_tools
        tools = build_agent_tools(["messages"], {})
        names = [t["name"] for t in tools]
        assert "system_refuse" in names
        refuse = next(t for t in tools if t["name"] == "system_refuse")
        assert "reason" in refuse["input_schema"]["properties"]
        assert "reason" in refuse["input_schema"]["required"]


# ── Refusal end-to-end via tool invocation ──


class TestRefusalEndToEnd:
    """v0.9.2 L7.5: drive the agent loop with a stub-shaped legacy
    provider that invokes system_refuse, and verify the audit record
    has outcome=refused and refusal_reason populated.

    The Phase 3 slice (e) sidecar write + `compute.<name>.refused`
    event publish are retired (per JL Wave 3 callout); the audit log
    is the single audit-trail surface. L7.4 (next slice) adds the
    parallel conversation-entry append for the chat surface."""

    def test_refusal_writes_audit(self, tmp_path):
        import asyncio
        from termin_server.context import RuntimeContext
        from termin_server.compute_runner import _execute_agent_compute
        from termin_server.storage import get_db, init_db, list_records
        from termin_server.events import EventBus

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
        # v0.9.2 L7.5: sidecar_schema removed; audit log is the only
        # audit-trail surface for refusals now.

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
        ctx.ir = {"content": [audit_schema], "computes": [comp]}
        ctx.content_lookup = {"compute_audit_log_moderator": audit_schema}
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

        async def _run():
            await init_db([audit_schema], db_path)
            await _execute_agent_compute(
                ctx, comp, record={"id": 1}, content_name="messages",
                main_loop=None,
            )
            db = await get_db(db_path)
            try:
                audit_rows = await list_records(db, audit_ref)
            finally:
                await db.close()
            return audit_rows

        audit_rows = asyncio.run(_run())

        # Audit row has outcome=refused, refusal_reason populated.
        # v0.9.2 L7.5: this is the only assertion now — no sidecar
        # row, no separate refusal event. The audit log entry is the
        # single audit-trail surface for refusals.
        assert len(audit_rows) == 1
        ar = audit_rows[0]
        assert ar["outcome"] == "refused"
        assert ar["refusal_reason"] == "not allowed"
