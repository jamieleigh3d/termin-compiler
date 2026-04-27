# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 3 slice (a) compute providers and registry
behavior. Covers default-CEL, llm stub, ai-agent stub end-to-end
behavior; the Anthropic providers are tested only at the
construction / Protocol level since hitting a real LLM in unit tests
is not appropriate.
"""

from __future__ import annotations

import pytest

from termin_runtime.providers import (
    AgentContext, AgentResult, AuditRecord, Category, CompletionResult,
    Completed, ContractRegistry, ProviderRegistry, ToolSurface,
    Principal,
)
from termin_runtime.providers.builtins import (
    DefaultCelProvider, StubLlmProvider, StubAgentProvider,
    AnthropicLlmProvider, AnthropicAgentProvider, register_builtins,
    register_default_cel, register_stub_llm, register_stub_agent,
    register_anthropic_llm, register_anthropic_agent,
)
from termin_runtime.providers.builtins._provider_hash import (
    hash_provider_config,
)


# ── Registry: registration of all five compute products ──


class TestComputeRegistration:
    def test_register_builtins_includes_compute(self):
        contracts = ContractRegistry.default()
        providers = ProviderRegistry()
        register_builtins(providers, contracts)
        keys = [
            (Category.COMPUTE, "default-CEL", "default-cel"),
            (Category.COMPUTE, "llm", "stub"),
            (Category.COMPUTE, "llm", "anthropic"),
            (Category.COMPUTE, "ai-agent", "stub"),
            (Category.COMPUTE, "ai-agent", "anthropic"),
        ]
        for cat, contract, product in keys:
            rec = providers.get(cat, contract, product)
            assert rec is not None, f"missing {cat=} {contract=} {product=}"
            assert rec.factory is not None
            assert rec.version == "0.9.0"

    def test_factory_constructs_correct_class(self):
        contracts = ContractRegistry.default()
        providers = ProviderRegistry()
        register_builtins(providers, contracts)

        rec = providers.get(Category.COMPUTE, "default-CEL", "default-cel")
        assert isinstance(rec.factory({}), DefaultCelProvider)
        rec = providers.get(Category.COMPUTE, "llm", "stub")
        assert isinstance(rec.factory({}), StubLlmProvider)
        rec = providers.get(Category.COMPUTE, "llm", "anthropic")
        assert isinstance(rec.factory({"model": "x"}), AnthropicLlmProvider)
        rec = providers.get(Category.COMPUTE, "ai-agent", "stub")
        assert isinstance(rec.factory({}), StubAgentProvider)
        rec = providers.get(Category.COMPUTE, "ai-agent", "anthropic")
        assert isinstance(rec.factory({"model": "x"}), AnthropicAgentProvider)

    def test_register_each_individually(self):
        # Each provider can be registered without the others.
        contracts = ContractRegistry.default()
        providers = ProviderRegistry()
        register_default_cel(providers, contracts)
        assert providers.get(Category.COMPUTE, "default-CEL", "default-cel")
        assert providers.list_products(Category.COMPUTE, "llm") == []

    def test_typo_in_contract_name_rejected(self):
        contracts = ContractRegistry.default()
        providers = ProviderRegistry()
        with pytest.raises(ValueError, match="not known"):
            providers.register(
                category=Category.COMPUTE,
                contract_name="ai_agent",  # typo: underscore
                product_name="anthropic",
                factory=lambda c: None,
                contract_registry=contracts,
            )


# ── Default-CEL: real evaluation ──


class TestDefaultCelProvider:
    def test_evaluate_simple_arithmetic(self):
        p = DefaultCelProvider()
        result = p.evaluate("1 + 2", {})
        assert result == 3

    def test_evaluate_with_bound_symbols(self):
        p = DefaultCelProvider()
        result = p.evaluate("x * y", {"x": 3, "y": 4})
        assert result == 12

    def test_evaluate_string_function(self):
        p = DefaultCelProvider()
        result = p.evaluate("upper(name)", {"name": "alice"})
        assert result == "ALICE"

    def test_evaluate_invalid_expression_raises(self):
        p = DefaultCelProvider()
        with pytest.raises(Exception):
            p.evaluate("&&& not valid", {})


# ── Stub LLM: scripted completions ──


class TestStubLlmProvider:
    @pytest.mark.asyncio
    async def test_default_response(self):
        p = StubLlmProvider()
        result = await p.complete("be helpful", "say something", None)
        assert result.outcome == "success"
        assert result.audit_record is not None
        assert result.audit_record.provider_product == "stub"

    @pytest.mark.asyncio
    async def test_scripted_match(self):
        p = StubLlmProvider({
            "responses": {
                "weather": {
                    "outcome": "success",
                    "output_value": "sunny",
                },
            }
        })
        result = await p.complete("be helpful", "what's the weather?", None)
        assert result.output_value == "sunny"

    @pytest.mark.asyncio
    async def test_scripted_refusal(self):
        p = StubLlmProvider({
            "responses": {
                "harmful": {
                    "outcome": "refused",
                    "refusal_reason": "I can't help with that",
                }
            }
        })
        result = await p.complete("system", "do something harmful", None)
        assert result.outcome == "refused"
        assert result.refusal_reason == "I can't help with that"
        assert result.audit_record.outcome == "refused"

    @pytest.mark.asyncio
    async def test_scripted_error(self):
        p = StubLlmProvider({
            "default_response": {
                "outcome": "error",
                "error_detail": "service down",
            }
        })
        result = await p.complete("x", "y", None)
        assert result.outcome == "error"
        assert result.error_detail == "service down"

    @pytest.mark.asyncio
    async def test_audit_record_has_prompt_as_sent(self):
        p = StubLlmProvider()
        result = await p.complete("DIRECTIVE_TEXT", "OBJECTIVE_TEXT", None)
        assert "DIRECTIVE_TEXT" in result.audit_record.prompt_as_sent
        assert "OBJECTIVE_TEXT" in result.audit_record.prompt_as_sent

    @pytest.mark.asyncio
    async def test_audit_record_has_config_hash(self):
        p = StubLlmProvider({"model_identifier": "test-model"})
        result = await p.complete("x", "y", None)
        assert result.audit_record.provider_config_hash.startswith("sha256:")
        assert result.audit_record.model_identifier == "test-model"


# ── Stub agent: scripted tool sequences ──


class TestStubAgentProvider:
    @pytest.mark.asyncio
    async def test_zero_tool_calls_success(self):
        p = StubAgentProvider({"default_script": {
            "final_outcome": "success",
            "final_result": "done",
            "tool_calls": [],
        }})
        principal = Principal(id="u1", type="human")
        ctx = AgentContext(principal=principal)
        result = await p.invoke("dir", "obj", ctx, ToolSurface())
        assert result.outcome == "success"
        assert result.output_value == "done"
        assert result.audit_record.outcome == "success"
        assert result.audit_record.tool_calls == ()

    @pytest.mark.asyncio
    async def test_scripted_tool_calls_invoke_callback(self):
        called: list[tuple[str, dict]] = []

        async def cb(name, args):
            called.append((name, dict(args)))
            return {"rows": []}

        p = StubAgentProvider({"default_script": {
            "final_outcome": "success",
            "tool_calls": [
                {"tool": "content.query", "args": {"content_type": "orders"}},
                {"tool": "content.query", "args": {"content_type": "customers"}},
            ],
        }})
        principal = Principal(id="u1", type="human")
        ctx = AgentContext(principal=principal, tool_callback=cb)
        result = await p.invoke("dir", "obj", ctx, ToolSurface())
        assert result.outcome == "success"
        assert len(called) == 2
        assert called[0] == ("content.query", {"content_type": "orders"})
        assert len(result.audit_record.tool_calls) == 2

    @pytest.mark.asyncio
    async def test_scripted_refusal(self):
        p = StubAgentProvider({"default_script": {
            "final_outcome": "refused",
            "refusal_reason": "policy",
            "tool_calls": [],
        }})
        principal = Principal(id="u1", type="human")
        ctx = AgentContext(principal=principal)
        result = await p.invoke("dir", "obj", ctx, ToolSurface())
        assert result.outcome == "refused"
        assert result.refusal_reason == "policy"
        assert result.audit_record.outcome == "refused"

    @pytest.mark.asyncio
    async def test_streaming_yields_completed_at_end(self):
        p = StubAgentProvider({"default_script": {
            "final_outcome": "success",
            "tool_calls": [],
        }})
        principal = Principal(id="u1", type="human")
        ctx = AgentContext(principal=principal)
        events = []
        async for ev in p.invoke_streaming("dir", "obj", ctx, ToolSurface()):
            events.append(ev)
        assert len(events) >= 1
        assert isinstance(events[-1], Completed)
        assert events[-1].result.outcome == "success"

    @pytest.mark.asyncio
    async def test_streaming_emits_tool_called_and_result(self):
        async def cb(name, args):
            return {"ok": True}

        p = StubAgentProvider({"default_script": {
            "final_outcome": "success",
            "tool_calls": [
                {"tool": "content.read", "args": {"content_type": "orders", "id": 1}},
            ],
        }})
        principal = Principal(id="u1", type="human")
        ctx = AgentContext(principal=principal, tool_callback=cb)
        events = []
        async for ev in p.invoke_streaming("dir", "obj", ctx, ToolSurface()):
            events.append(ev)
        # Expect: ToolCalled, ToolResult, Completed
        from termin_runtime.providers import ToolCalled, ToolResult
        types = [type(e).__name__ for e in events]
        assert "ToolCalled" in types
        assert "ToolResult" in types
        assert "Completed" in types


# ── Anthropic providers: construction-time only (no real API calls) ──


class TestAnthropicProviderConstruction:
    def test_llm_construct_with_minimal_config(self):
        p = AnthropicLlmProvider({"model": "claude-haiku-4-5-20251001"})
        assert p._model == "claude-haiku-4-5-20251001"

    def test_agent_construct_with_minimal_config(self):
        p = AnthropicAgentProvider({"model": "claude-haiku-4-5-20251001"})
        assert p._model == "claude-haiku-4-5-20251001"

    def test_llm_default_model(self):
        p = AnthropicLlmProvider()
        assert "claude" in p._model

    def test_llm_unresolved_api_key_fails_at_call(self):
        # The factory itself constructs fine — the provider only fails
        # when actually called. This matches BRD §6.1 fail-closed
        # posture: deployment continues with stale config until first
        # use, then surfaces a clear error.
        p = AnthropicLlmProvider({"model": "x", "api_key": "${ANTHROPIC_API_KEY}"})
        assert p._api_key == "${ANTHROPIC_API_KEY}"
        with pytest.raises(RuntimeError, match="resolved"):
            p._get_client()

    def test_config_hash_is_stable(self):
        p1 = AnthropicLlmProvider({"model": "x", "api_key": "secret-a"})
        p2 = AnthropicLlmProvider({"model": "x", "api_key": "secret-b"})
        # Different api_keys → same hash (redacted then hashed)
        assert p1._config_hash == p2._config_hash

    def test_config_hash_differs_on_model_change(self):
        p1 = AnthropicLlmProvider({"model": "claude-a", "api_key": "k"})
        p2 = AnthropicLlmProvider({"model": "claude-b", "api_key": "k"})
        assert p1._config_hash != p2._config_hash


# ── Provider config hash ──


class TestProviderConfigHash:
    def test_returns_sha256_prefixed(self):
        h = hash_provider_config({"model": "x"})
        assert h.startswith("sha256:")
        assert len(h) == 71  # sha256: + 64 hex

    def test_same_config_same_hash(self):
        a = hash_provider_config({"model": "x", "max_tokens": 100})
        b = hash_provider_config({"model": "x", "max_tokens": 100})
        assert a == b

    def test_key_order_does_not_matter(self):
        a = hash_provider_config({"model": "x", "max_tokens": 100})
        b = hash_provider_config({"max_tokens": 100, "model": "x"})
        assert a == b

    def test_secret_redaction_api_key(self):
        a = hash_provider_config({"model": "x", "api_key": "AAA"})
        b = hash_provider_config({"model": "x", "api_key": "BBB"})
        assert a == b, "API key rotation must not change config hash"

    def test_secret_redaction_token(self):
        a = hash_provider_config({"model": "x", "bearer_token": "AAA"})
        b = hash_provider_config({"model": "x", "bearer_token": "BBB"})
        assert a == b

    def test_secret_redaction_password(self):
        a = hash_provider_config({"db_password": "X"})
        b = hash_provider_config({"db_password": "Y"})
        assert a == b

    def test_non_secret_value_change_changes_hash(self):
        a = hash_provider_config({"model": "claude-haiku"})
        b = hash_provider_config({"model": "claude-sonnet"})
        assert a != b

    def test_nested_secret_redaction(self):
        a = hash_provider_config({
            "model": "x",
            "auth": {"api_key": "AAA", "endpoint": "https://example.com"}
        })
        b = hash_provider_config({
            "model": "x",
            "auth": {"api_key": "BBB", "endpoint": "https://example.com"}
        })
        assert a == b

    def test_nested_non_secret_change_changes_hash(self):
        a = hash_provider_config({
            "model": "x",
            "auth": {"api_key": "k", "endpoint": "https://a.com"}
        })
        b = hash_provider_config({
            "model": "x",
            "auth": {"api_key": "k", "endpoint": "https://b.com"}
        })
        assert a != b
