# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for the v0.9 Phase 3 slice (a): compute contract surface,
provider Protocols, and the supporting data shapes.

This slice introduces the contract layer without changing runtime
behavior — the compute_runner still uses ctx.ai_provider; these tests
verify the new layer exists, registers, and conforms to the BRD §6.3
shape.
"""

from __future__ import annotations

import pytest

from termin_server.providers import (
    AgentContext, AgentEvent, AgentResult, AuditableAction, AuditRecord,
    Cost, CompletionResult, Completed, Failed,
    DefaultCelComputeProvider, LlmComputeProvider, AiAgentComputeProvider,
    NotAuthorized, ToolCall, ToolCalled, ToolResult, ToolSurface,
    TokenEmitted, ToolNotDeclared,
    Principal,
)


# ── ToolSurface ──


class TestToolSurface:
    def test_default_always_available(self):
        s = ToolSurface()
        assert "identity.self" in s.always_available
        assert "system.refuse" in s.always_available

    def test_permits_content_read_via_accesses(self):
        s = ToolSurface(content_rw=("orders",))
        assert s.permits_content_read("orders") is True
        assert s.permits_content_write("orders") is True
        assert s.permits_state_transition("orders") is True

    def test_permits_content_read_via_reads_only(self):
        s = ToolSurface(content_ro=("products",))
        assert s.permits_content_read("products") is True
        assert s.permits_content_write("products") is False
        # State tools come from Accesses only — never from Reads.
        assert s.permits_state_transition("products") is False

    def test_permits_undeclared_content_denied(self):
        s = ToolSurface(content_rw=("orders",))
        assert s.permits_content_read("customers") is False
        assert s.permits_content_write("customers") is False

    def test_permits_channel(self):
        s = ToolSurface(channels=("supplier alerts",))
        assert s.permits_channel("supplier alerts") is True
        assert s.permits_channel("other channel") is False

    def test_permits_event(self):
        s = ToolSurface(events=("order.placed",))
        assert s.permits_event("order.placed") is True
        assert s.permits_event("order.cancelled") is False

    def test_permits_compute(self):
        s = ToolSurface(computes=("compute_price",))
        assert s.permits_compute("compute_price") is True
        assert s.permits_compute("other") is False

    def test_immutable(self):
        s = ToolSurface(content_rw=("orders",))
        with pytest.raises(Exception):
            s.content_rw = ("changed",)  # type: ignore


# ── AuditRecord ──


class TestAuditRecord:
    def test_minimal_success(self):
        r = AuditRecord(
            provider_product="stub",
            model_identifier="m",
            provider_config_hash="sha256:0",
            prompt_as_sent="p",
        )
        assert r.outcome == "success"
        assert r.refusal_reason is None
        assert r.tool_calls == ()

    def test_refused_requires_reason(self):
        with pytest.raises(ValueError, match="refusal_reason"):
            AuditRecord(
                provider_product="stub", model_identifier="m",
                provider_config_hash="sha256:0", prompt_as_sent="p",
                outcome="refused",
            )

    def test_error_requires_detail(self):
        with pytest.raises(ValueError, match="error_detail"):
            AuditRecord(
                provider_product="stub", model_identifier="m",
                provider_config_hash="sha256:0", prompt_as_sent="p",
                outcome="error",
            )

    def test_outcome_must_be_valid(self):
        with pytest.raises(ValueError, match="outcome"):
            AuditRecord(
                provider_product="stub", model_identifier="m",
                provider_config_hash="sha256:0", prompt_as_sent="p",
                outcome="bogus",
            )

    def test_with_tool_calls(self):
        tc = ToolCall(tool="content.query", args={"x": 1}, result=[])
        r = AuditRecord(
            provider_product="stub", model_identifier="m",
            provider_config_hash="sha256:0", prompt_as_sent="p",
            tool_calls=(tc,),
        )
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].tool == "content.query"

    def test_with_cost(self):
        c = Cost(units=1234, unit_type="tokens", currency_amount="0.0042")
        r = AuditRecord(
            provider_product="stub", model_identifier="m",
            provider_config_hash="sha256:0", prompt_as_sent="p",
            cost=c,
        )
        assert r.cost is not None
        assert r.cost.units == 1234


# ── CompletionResult / AgentResult outcome validation ──


class TestResultOutcomeValidation:
    def test_completion_result_success(self):
        r = CompletionResult(outcome="success", output_value="hello")
        assert r.outcome == "success"

    def test_completion_result_invalid_outcome(self):
        with pytest.raises(ValueError):
            CompletionResult(outcome="bogus")

    def test_agent_result_invalid_outcome(self):
        with pytest.raises(ValueError):
            AgentResult(outcome="weird")

    def test_agent_result_with_actions(self):
        a1 = AuditableAction(tool="content.query", target="orders")
        r = AgentResult(outcome="success", actions_taken=(a1,))
        assert len(r.actions_taken) == 1


# ── AgentEvent variants ──


class TestAgentEvents:
    def test_token_emitted(self):
        e = TokenEmitted(text="hello")
        assert e.text == "hello"

    def test_tool_called(self):
        e = ToolCalled(tool="content.query", args={"x": 1}, call_id="c1")
        assert e.call_id == "c1"

    def test_tool_result(self):
        e = ToolResult(call_id="c1", result={"data": []}, is_error=False)
        assert e.is_error is False

    def test_completed_carries_agent_result(self):
        r = AgentResult(outcome="success")
        e = Completed(result=r)
        assert e.result.outcome == "success"

    def test_failed_carries_error_string(self):
        e = Failed(error="connection reset")
        assert e.error == "connection reset"


# ── Gate exceptions ──


class TestGateExceptions:
    def test_tool_not_declared_message(self):
        e = ToolNotDeclared("content.query", target="orders")
        assert "TERMIN-A001" in str(e)
        assert "content.query" in str(e)
        assert "orders" in str(e)

    def test_tool_not_declared_no_target(self):
        e = ToolNotDeclared("identity.self")
        assert "TERMIN-A001" in str(e)
        assert "identity.self" in str(e)

    def test_not_authorized_message(self):
        e = NotAuthorized("content.update", required_scope="orders.write")
        assert "TERMIN-A002" in str(e)
        assert "orders.write" in str(e)


# ── AgentContext ──


class TestAgentContext:
    def test_minimal(self):
        p = Principal(id="u1", type="human", display_name="Alice")
        c = AgentContext(principal=p)
        assert c.principal.id == "u1"
        assert c.bound_symbols == {}
        assert c.tool_callback is None

    def test_with_callback(self):
        p = Principal(id="u1", type="human")
        called = []
        def cb(name, args):
            called.append((name, args))
            return "ok"
        c = AgentContext(principal=p, tool_callback=cb)
        result = c.tool_callback("identity.self", {})
        assert result == "ok"
        assert called == [("identity.self", {})]


# ── Protocol structural conformance ──


class TestProtocolConformance:
    """The three Protocols are runtime-checkable. Built-in providers
    must satisfy them. This is the contract-layer guarantee."""

    def test_default_cel_protocol(self):
        from termin_server.providers.builtins import DefaultCelProvider
        p = DefaultCelProvider()
        assert isinstance(p, DefaultCelComputeProvider)

    def test_llm_stub_protocol(self):
        from termin_server.providers.builtins import StubLlmProvider
        p = StubLlmProvider()
        assert isinstance(p, LlmComputeProvider)

    def test_llm_anthropic_protocol(self):
        from termin_server.providers.builtins import AnthropicLlmProvider
        # Construct without an api_key — Protocol check is structural,
        # doesn't require working SDK.
        p = AnthropicLlmProvider({"model": "x"})
        assert isinstance(p, LlmComputeProvider)

    def test_agent_stub_protocol(self):
        from termin_server.providers.builtins import StubAgentProvider
        p = StubAgentProvider()
        assert isinstance(p, AiAgentComputeProvider)

    def test_agent_anthropic_protocol(self):
        from termin_server.providers.builtins import AnthropicAgentProvider
        p = AnthropicAgentProvider({"model": "x"})
        assert isinstance(p, AiAgentComputeProvider)
