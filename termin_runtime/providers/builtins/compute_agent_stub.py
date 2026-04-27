# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Stub ai-agent compute provider — first-party plugin against the
v0.9 ai-agent contract surface (BRD §6.3.3).

Scripted-script stub for deterministic tests and local development.
Does not call any LLM SDK; instead replays a configured sequence of
tool calls and a final result. The runtime's gate function still
runs against the configured tool calls — so denied tools surface as
ToolNotDeclared even in the stub, which is exactly the conformance
behavior tests want.

Per BRD §10 ("Stub providers required for every contract"), every
named contract ships with a stub product so dev/test deploy configs
can bind to a deterministic implementation.

Configuration shape (deploy_config["bindings"]["compute"]["<name>"]
["config"]):
    {
        "scripts": {
            "<directive_or_objective_substring>": {
                "tool_calls": [
                    {"tool": "content.query", "args": {...}},
                    ...
                ],
                "final_outcome": "success" | "refused" | "error",
                "final_result": <any>,                # if success
                "refusal_reason": "...",              # if refused
                "error_detail": "...",                # if error
                "reasoning_summary": "..."            # optional
            }
        },
        "default_script": {...},                      # optional
        "model_identifier": "stub-agent-1"
    }
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping, Optional

from ..contracts import Category, ContractRegistry
from ..compute_contract import (
    AgentContext, AgentEvent, AgentResult, AuditableAction, AuditRecord,
    Completed, ToolCall, ToolCalled, ToolNotDeclared, ToolResult,
    ToolSurface,
)
from ._provider_hash import hash_provider_config


class StubAgentProvider:
    """Scripted ai-agent for tests.

    Replays a configured tool-call sequence then emits a final result.
    Each scripted tool call goes through `context.tool_callback` so
    the runtime's gate function is exercised — denied tools surface
    via `ToolNotDeclared`, which the script can then react to (or
    propagate as an error).
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self._scripts: dict[str, dict] = dict(self._config.get("scripts", {}))
        self._default: Optional[dict] = self._config.get("default_script")
        self._model_id: str = str(
            self._config.get("model_identifier", "stub-agent-1")
        )
        self._config_hash = hash_provider_config(self._config)

    async def invoke(
        self,
        directive: str,
        objective: str,
        context: AgentContext,
        tools: ToolSurface,
    ) -> AgentResult:
        prompt = f"{directive}\n{objective}"
        script = self._match(prompt)
        return await self._run_script(prompt, script, context)

    async def invoke_streaming(
        self,
        directive: str,
        objective: str,
        context: AgentContext,
        tools: ToolSurface,
    ) -> AsyncIterator[AgentEvent]:
        prompt = f"{directive}\n{objective}"
        script = self._match(prompt)
        # Replay tool calls as ToolCalled / ToolResult events, then
        # emit Completed at the end.
        executed: list[ToolCall] = []
        actions: list[AuditableAction] = []
        for idx, call in enumerate(script.get("tool_calls", [])):
            tool = call["tool"]
            args = call.get("args", {})
            call_id = f"call-{idx}"
            yield ToolCalled(tool=tool, args=args, call_id=call_id)
            try:
                result = await _maybe_await(
                    context.tool_callback, tool, args
                ) if context.tool_callback else None
                yield ToolResult(call_id=call_id, result=result, is_error=False)
                executed.append(ToolCall(
                    tool=tool, args=dict(args), result=result,
                    is_error=False, latency_ms=0,
                ))
                actions.append(AuditableAction(
                    tool=tool,
                    target=str(args.get("content_type") or args.get("channel_name") or ""),
                    succeeded=True,
                ))
            except ToolNotDeclared as e:
                yield ToolResult(
                    call_id=call_id, result=str(e), is_error=True,
                )
                executed.append(ToolCall(
                    tool=tool, args=dict(args), result=str(e),
                    is_error=True, latency_ms=0,
                ))
                actions.append(AuditableAction(
                    tool=tool, target=None, succeeded=False,
                ))
        # Build final AgentResult and emit Completed.
        result = self._build_result(prompt, script, executed, actions)
        yield Completed(result=result)

    async def _run_script(
        self, prompt: str, script: dict, context: AgentContext,
    ) -> AgentResult:
        executed: list[ToolCall] = []
        actions: list[AuditableAction] = []
        for call in script.get("tool_calls", []):
            tool = call["tool"]
            args = call.get("args", {})
            try:
                result = await _maybe_await(
                    context.tool_callback, tool, args
                ) if context.tool_callback else None
                executed.append(ToolCall(
                    tool=tool, args=dict(args), result=result,
                    is_error=False, latency_ms=0,
                ))
                actions.append(AuditableAction(
                    tool=tool,
                    target=str(args.get("content_type") or args.get("channel_name") or ""),
                    succeeded=True,
                ))
            except ToolNotDeclared as e:
                executed.append(ToolCall(
                    tool=tool, args=dict(args), result=str(e),
                    is_error=True, latency_ms=0,
                ))
                actions.append(AuditableAction(
                    tool=tool, target=None, succeeded=False,
                ))
        return self._build_result(prompt, script, executed, actions)

    def _build_result(
        self, prompt: str, script: dict,
        executed: list[ToolCall], actions: list[AuditableAction],
    ) -> AgentResult:
        outcome = script.get("final_outcome", "success")
        audit = AuditRecord(
            provider_product="stub",
            model_identifier=self._model_id,
            provider_config_hash=self._config_hash,
            prompt_as_sent=prompt,
            sampling_params={},
            tool_calls=tuple(executed),
            outcome=outcome,
            refusal_reason=script.get("refusal_reason"),
            error_detail=script.get("error_detail"),
            cost=None,
            latency_ms=0,
        )
        return AgentResult(
            outcome=outcome,
            actions_taken=tuple(actions),
            reasoning_summary=script.get("reasoning_summary"),
            refusal_reason=script.get("refusal_reason"),
            error_detail=script.get("error_detail"),
            audit_record=audit,
            output_value=script.get("final_result"),
        )

    def _match(self, prompt: str) -> dict:
        for key, script in self._scripts.items():
            if key in prompt:
                return script
        if self._default is not None:
            return dict(self._default)
        # Empty default: zero tool calls, success outcome.
        return {"final_outcome": "success", "tool_calls": []}


async def _maybe_await(callback, tool: str, args: Mapping[str, Any]):
    """Invoke the gated tool callback. Supports both sync and async
    callbacks for stub flexibility."""
    import inspect
    result = callback(tool, args)
    if inspect.isawaitable(result):
        return await result
    return result


# ── Registration ──


def _stub_agent_factory(config: Mapping[str, Any]) -> StubAgentProvider:
    return StubAgentProvider(config)


def register_stub_agent(
    provider_registry, contract_registry: ContractRegistry | None = None
) -> None:
    """Register the stub agent provider against (compute, "ai-agent")."""
    provider_registry.register(
        category=Category.COMPUTE,
        contract_name="ai-agent",
        product_name="stub",
        factory=_stub_agent_factory,
        conformance="passing",
        version="0.9.0",
        contract_registry=contract_registry,
    )
