# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 3 slice (d): audit-Content schema reshape per
BRD §6.3.4.

Covers:
  - `latency_ms` rename (was `duration_ms` in v0.8).
  - `outcome` enum widened with "refused".
  - LLM/agent computes get extra reproducibility-grade columns:
    provider_product, model_identifier, provider_config_hash,
    prompt_as_sent, sampling_params, tool_calls, refusal_reason,
    cost_units, cost_unit_type, cost_currency_amount.
  - CEL computes do NOT get the LLM-only columns.
  - write_audit_trace populates the new columns when audit_metadata
    is supplied.
"""

from __future__ import annotations

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower


def _compile(src: str):
    prog, _ = parse(src)
    result = analyze(prog)
    assert result.ok, [str(e) for e in result.errors]
    return lower(prog)


def _find_audit(spec, snake: str):
    for cs in spec.content:
        if cs.name.snake == snake:
            return cs
    return None


def _field_names(cs) -> set[str]:
    return {f.name for f in cs.fields}


_BASE_LLM_SOURCE = '''Application: A
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
  Objective is ```summarize the input```
  Anyone with "x.write" can execute this
  Anyone with "audit.x" can audit
'''


_BASE_CEL_SOURCE = '''Application: A
  Description: x
Identity:
  Scopes are "x.write", "audit.x"
  An "u" has "x.write"

Content called "orders":
  Each order has a name which is text, required
  Each order has a total which is currency
  Anyone with "x.write" can view, create, update, or delete orders

Compute called "calculate":
  Transform: takes an order, produces an order
  `order.total = 100`
  Anyone with "x.write" can execute this
  Anyone with "audit.x" can audit
'''


_BASE_AGENT_SOURCE = '''Application: A
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
  Objective is ```review the message```
  Anyone with "x.write" can execute this
  Anyone with "audit.x" can audit
'''


# ── Schema reshape ──


class TestAuditSchemaCommonShape:
    def test_latency_ms_replaces_duration_ms_for_cel(self):
        spec = _compile(_BASE_CEL_SOURCE)
        audit = _find_audit(spec, "compute_audit_log_calculate")
        names = _field_names(audit)
        assert "latency_ms" in names
        assert "duration_ms" not in names

    def test_latency_ms_replaces_duration_ms_for_llm(self):
        spec = _compile(_BASE_LLM_SOURCE)
        audit = _find_audit(spec, "compute_audit_log_summarize")
        names = _field_names(audit)
        assert "latency_ms" in names
        assert "duration_ms" not in names

    def test_outcome_includes_refused(self):
        spec = _compile(_BASE_LLM_SOURCE)
        audit = _find_audit(spec, "compute_audit_log_summarize")
        outcome = next(f for f in audit.fields if f.name == "outcome")
        assert "refused" in set(outcome.enum_values)
        assert "success" in set(outcome.enum_values)
        assert "error" in set(outcome.enum_values)


class TestAuditSchemaLlmAgentExtras:
    """LLM and ai-agent computes get reproducibility-grade columns
    per BRD §6.3.4. CEL computes do not."""

    LLM_EXTRA_FIELDS = {
        "provider_product",
        "model_identifier",
        "provider_config_hash",
        "prompt_as_sent",
        "sampling_params",
        "tool_calls",
        "refusal_reason",
        "cost_units",
        "cost_unit_type",
        "cost_currency_amount",
    }

    def test_llm_audit_has_extra_columns(self):
        spec = _compile(_BASE_LLM_SOURCE)
        audit = _find_audit(spec, "compute_audit_log_summarize")
        assert self.LLM_EXTRA_FIELDS.issubset(_field_names(audit))

    def test_agent_audit_has_extra_columns(self):
        spec = _compile(_BASE_AGENT_SOURCE)
        audit = _find_audit(spec, "compute_audit_log_moderator")
        assert self.LLM_EXTRA_FIELDS.issubset(_field_names(audit))

    def test_cel_audit_lacks_llm_extras(self):
        """CEL computes get the base shape only — no
        provider_product, no model_identifier, no prompt_as_sent."""
        spec = _compile(_BASE_CEL_SOURCE)
        audit = _find_audit(spec, "compute_audit_log_calculate")
        names = _field_names(audit)
        for f in self.LLM_EXTRA_FIELDS:
            assert f not in names, f"CEL audit should not have '{f}'"


# ── write_audit_trace populates the new columns ──


class TestWriteAuditTraceMetadata:
    """write_audit_trace's audit_metadata kwarg writes the BRD §6.3.4
    columns when the compute is llm/ai-agent."""

    def _audit_schema_dict(self, audit_ref: str, *, llm: bool):
        """Build an IR-shaped schema dict for the test audit table."""
        def _f(name, business_type, column_type, enum_values=None):
            d = {
                "name": name,
                "display_name": name.replace("_", " "),
                "business_type": business_type,
                "column_type": column_type,
            }
            if enum_values is not None:
                d["enum_values"] = list(enum_values)
            return d

        fields = [
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
        ]
        if llm:
            fields.extend([
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
            ])
        return {
            "name": {"display": audit_ref.replace("_", " "),
                     "snake": audit_ref, "pascal": "ComputeAuditLog"},
            "singular": audit_ref,
            "fields": fields,
            "audit": "none",
            "verbs": ["VIEW", "AUDIT"],
        }

    def test_audit_metadata_populates_new_columns(self, tmp_path):
        import asyncio
        from termin_runtime.compute_runner import write_audit_trace
        from termin_runtime.storage import get_db, init_db, list_records

        audit_ref = "compute_audit_log_summarize"
        schema = self._audit_schema_dict(audit_ref, llm=True)
        db_path = str(tmp_path / "audit_meta.db")

        class _Ctx:
            pass
        ctx = _Ctx()
        ctx.db_path = db_path
        ctx.ir = {"content": [schema], "computes": []}

        comp_dict = {
            "name": {"display": "summarize", "snake": "summarize"},
            "provider": "llm",
            "audit_level": "actions",
            "audit_content_ref": audit_ref,
        }
        meta = {
            "provider_product": "anthropic",
            "model_identifier": "claude-haiku-4-5",
            "provider_config_hash": "sha256:deadbeef",
            "prompt_as_sent": "<system>be brief</system>\nsummarize",
            "sampling_params_json": '{"temperature":0.3}',
            "tool_calls_json": "[]",
            "cost_units": 1234,
            "cost_unit_type": "tokens",
        }

        async def _go():
            await init_db([schema], db_path)
            await write_audit_trace(
                ctx, comp_dict, invocation_id="inv-1", trigger="event",
                started_at="2026-04-25T00:00:00Z",
                completed_at="2026-04-25T00:00:01Z",
                latency_ms=1500.0, outcome="success",
                audit_metadata=meta,
            )
            db = await get_db(db_path)
            try:
                rows = await list_records(db, audit_ref)
            finally:
                await db.close()
            return rows

        rows = asyncio.run(_go())
        assert len(rows) == 1
        row = rows[0]
        assert row["latency_ms"] == 1500.0
        assert row["provider_product"] == "anthropic"
        assert row["model_identifier"] == "claude-haiku-4-5"
        assert row["provider_config_hash"] == "sha256:deadbeef"
        assert "summarize" in row["prompt_as_sent"]
        assert row["sampling_params"] == '{"temperature":0.3}'
        assert row["tool_calls"] == "[]"
        assert row["cost_units"] == 1234
        assert row["cost_unit_type"] == "tokens"
        # refusal_reason empty when outcome=success
        assert row["refusal_reason"] == ""

    def test_audit_metadata_legacy_duration_ms_kwarg_still_works(self, tmp_path):
        """Internal callers may still pass `duration_ms=` during the
        transition; write_audit_trace accepts both names."""
        import asyncio
        from termin_runtime.compute_runner import write_audit_trace
        from termin_runtime.storage import get_db, init_db, list_records

        audit_ref = "compute_audit_log_calculate"
        schema = self._audit_schema_dict(audit_ref, llm=False)
        db_path = str(tmp_path / "audit_legacy.db")

        class _Ctx:
            pass
        ctx = _Ctx()
        ctx.db_path = db_path
        ctx.ir = {"content": [schema], "computes": []}

        comp_dict = {
            "name": {"display": "calculate", "snake": "calculate"},
            "provider": None,  # CEL
            "audit_level": "actions",
            "audit_content_ref": audit_ref,
        }

        async def _go():
            await init_db([schema], db_path)
            await write_audit_trace(
                ctx, comp_dict, invocation_id="inv-1", trigger="api",
                started_at="2026-04-25T00:00:00Z",
                completed_at="2026-04-25T00:00:01Z",
                duration_ms=2500.0, outcome="success",  # legacy kwarg
            )
            db = await get_db(db_path)
            try:
                rows = await list_records(db, audit_ref)
            finally:
                await db.close()
            return rows

        rows = asyncio.run(_go())
        assert len(rows) == 1
        assert rows[0]["latency_ms"] == 2500.0
