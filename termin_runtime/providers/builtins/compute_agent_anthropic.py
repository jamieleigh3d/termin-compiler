# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Anthropic ai-agent compute provider — first-party plugin against
the v0.9 ai-agent contract surface (BRD §6.3.3).

Multi-action agent loop with closed tool surface. The provider builds
tool schemas from the runtime-supplied `ToolSurface`, runs the
Anthropic agent loop, and routes each tool call through the gated
`tool_callback` from `AgentContext`. Refusal is a first-class tool
call (`system.refuse`) — the agent invokes it; the runtime's gate
captures it; the provider returns AgentResult with outcome="refused".

Phase 3 slice (a) lands this module without wiring it into the
compute_runner; the existing `termin_runtime/ai_provider.py`
agent_loop path keeps serving until slice (b) cuts over. The
detailed schema-building and streaming-translation logic move from
ai_provider.py into this module in slice (b).

For slice (a), this module ships a working but minimal agent loop
sufficient for conformance smoke tests; the production wiring in
slice (b) will harden the streaming and tool-result handling.
"""

from __future__ import annotations

import time
from typing import Any, AsyncIterator, Mapping, Optional

from ..contracts import Category, ContractRegistry
from ..compute_contract import (
    AgentContext, AgentEvent, AgentResult, AuditableAction, AuditRecord,
    Completed, Cost, Failed, ToolCall, ToolCalled, ToolNotDeclared,
    ToolResult, ToolSurface,
)
from ._provider_hash import hash_provider_config


# Always-available tools that the runtime gate honors regardless of
# source-level grants — see compute_contract.ToolSurface.always_available.
_ALWAYS_AVAILABLE = ("identity.self", "system.refuse")

# Maximum agent loop turns before the runtime forces termination.
_DEFAULT_MAX_TURNS = 20


class AnthropicAgentProvider:
    """Anthropic-backed ai-agent. Lazy SDK client construction.

    The agent loop:
      1. Send (system=directive, messages=[user=objective+context]).
      2. Read response.content for tool_use blocks.
      3. For each tool_use, call context.tool_callback (gated).
      4. Append tool_result blocks to messages.
      5. Repeat until response has no tool_use blocks OR system.refuse
         was called OR max_turns reached.
      6. Build AgentResult.

    Refusal: `system.refuse(reason)` is one of the always-available
    tools. The provider treats a tool_use of `system.refuse` as a
    terminal event — captures the reason, ends the loop, returns
    outcome="refused".
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self._model: str = str(
            self._config.get("model") or "claude-haiku-4-5-20251001"
        )
        self._api_key: Optional[str] = self._config.get("api_key")
        self._max_tokens: int = int(self._config.get("max_tokens", 4096))
        self._max_turns: int = int(
            self._config.get("max_turns", _DEFAULT_MAX_TURNS)
        )
        self._default_sampling: dict = dict(
            self._config.get("default_sampling", {})
        )
        self._config_hash = hash_provider_config(self._config)
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._api_key or self._api_key.startswith("${"):
            raise RuntimeError(
                "AnthropicAgentProvider config is missing a resolved "
                "'api_key'."
            )
        try:
            import anthropic  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "AnthropicAgentProvider requires the `anthropic` package."
            ) from e
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    async def invoke(
        self,
        directive: str,
        objective: str,
        context: AgentContext,
        tools: ToolSurface,
    ) -> AgentResult:
        prompt_as_sent = f"<system>\n{directive}\n</system>\n{objective}"
        started = time.monotonic()
        executed: list[ToolCall] = []
        actions: list[AuditableAction] = []

        try:
            client = self._get_client()
            tool_schemas = self._build_tool_schemas(tools)
            messages = [{"role": "user", "content": objective}]
            refused_reason: Optional[str] = None
            output_value: Any = None

            for turn in range(self._max_turns):
                response = client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=directive,
                    messages=messages,
                    tools=tool_schemas,
                    **self._default_sampling,
                )
                tool_uses = [
                    b for b in (response.content or [])
                    if getattr(b, "type", None) == "tool_use"
                ]
                if not tool_uses:
                    # No tool calls — agent finished. Extract any text
                    # as reasoning_summary.
                    reasoning = self._extract_text(response)
                    latency_ms = int((time.monotonic() - started) * 1000)
                    cost = self._extract_cost(response)
                    audit = AuditRecord(
                        provider_product="anthropic",
                        model_identifier=str(getattr(response, "model", self._model)),
                        provider_config_hash=self._config_hash,
                        prompt_as_sent=prompt_as_sent,
                        sampling_params=self._default_sampling,
                        tool_calls=tuple(executed),
                        outcome="success",
                        cost=cost,
                        latency_ms=latency_ms,
                    )
                    return AgentResult(
                        outcome="success",
                        actions_taken=tuple(actions),
                        reasoning_summary=reasoning,
                        audit_record=audit,
                        output_value=output_value,
                    )

                # Append assistant turn (with tool_use blocks) and run
                # tool calls.
                messages.append(
                    {"role": "assistant", "content": response.content}
                )
                tool_results = []
                for tu in tool_uses:
                    tool_name = tu.name
                    tool_args = tu.input or {}
                    if tool_name == "system.refuse":
                        refused_reason = str(tool_args.get("reason", ""))
                        executed.append(ToolCall(
                            tool=tool_name, args=dict(tool_args),
                            result={"acknowledged": True},
                            is_error=False, latency_ms=0,
                        ))
                        actions.append(AuditableAction(
                            tool=tool_name, target=None, succeeded=True,
                        ))
                        break
                    try:
                        call_started = time.monotonic()
                        result = await _maybe_await(
                            context.tool_callback, tool_name, tool_args,
                        ) if context.tool_callback else None
                        call_latency = int(
                            (time.monotonic() - call_started) * 1000
                        )
                        executed.append(ToolCall(
                            tool=tool_name, args=dict(tool_args),
                            result=result, is_error=False,
                            latency_ms=call_latency,
                        ))
                        actions.append(AuditableAction(
                            tool=tool_name,
                            target=str(tool_args.get("content_type")
                                       or tool_args.get("channel_name")
                                       or ""),
                            succeeded=True,
                        ))
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": str(result),
                        })
                    except ToolNotDeclared as e:
                        executed.append(ToolCall(
                            tool=tool_name, args=dict(tool_args),
                            result=str(e), is_error=True, latency_ms=0,
                        ))
                        actions.append(AuditableAction(
                            tool=tool_name, target=None, succeeded=False,
                        ))
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": str(e),
                            "is_error": True,
                        })

                if refused_reason is not None:
                    break

                messages.append({"role": "user", "content": tool_results})

            # End of loop — refused or max_turns reached.
            latency_ms = int((time.monotonic() - started) * 1000)
            if refused_reason is not None:
                audit = AuditRecord(
                    provider_product="anthropic",
                    model_identifier=self._model,
                    provider_config_hash=self._config_hash,
                    prompt_as_sent=prompt_as_sent,
                    sampling_params=self._default_sampling,
                    tool_calls=tuple(executed),
                    outcome="refused",
                    refusal_reason=refused_reason,
                    latency_ms=latency_ms,
                )
                return AgentResult(
                    outcome="refused",
                    actions_taken=tuple(actions),
                    refusal_reason=refused_reason,
                    audit_record=audit,
                )
            # max_turns reached without completion → error
            err = (
                f"agent loop reached max_turns={self._max_turns} without "
                f"completion"
            )
            audit = AuditRecord(
                provider_product="anthropic",
                model_identifier=self._model,
                provider_config_hash=self._config_hash,
                prompt_as_sent=prompt_as_sent,
                sampling_params=self._default_sampling,
                tool_calls=tuple(executed),
                outcome="error",
                error_detail=err,
                latency_ms=latency_ms,
            )
            return AgentResult(
                outcome="error",
                actions_taken=tuple(actions),
                error_detail=err,
                audit_record=audit,
            )

        except Exception as e:
            latency_ms = int((time.monotonic() - started) * 1000)
            audit = AuditRecord(
                provider_product="anthropic",
                model_identifier=self._model,
                provider_config_hash=self._config_hash,
                prompt_as_sent=prompt_as_sent,
                sampling_params=self._default_sampling,
                tool_calls=tuple(executed),
                outcome="error",
                error_detail=f"{type(e).__name__}: {e}",
                latency_ms=latency_ms,
            )
            return AgentResult(
                outcome="error",
                actions_taken=tuple(actions),
                error_detail=f"{type(e).__name__}: {e}",
                audit_record=audit,
            )

    async def invoke_streaming(
        self,
        directive: str,
        objective: str,
        context: AgentContext,
        tools: ToolSurface,
    ) -> AsyncIterator[AgentEvent]:
        # Slice (a): minimal streaming impl — runs invoke() and emits
        # one Completed event. Slice (b) replaces this with the
        # token-by-token streaming translation from ai_provider.py.
        try:
            result = await self.invoke(directive, objective, context, tools)
            yield Completed(result=result)
        except Exception as e:
            yield Failed(error=f"{type(e).__name__}: {e}")

    def _build_tool_schemas(self, surface: ToolSurface) -> list[dict]:
        """Build Anthropic tool schemas from the source-declared
        ToolSurface. Slice (a) ships always-available tools only; the
        full content/state/channel tool schemas land in slice (b)
        when grant grammar is in place."""
        schemas = [
            {
                "name": "system.refuse",
                "description": (
                    "Refuse the requested work because it conflicts "
                    "with system policy or training constraints. "
                    "Provide a clear reason. Calling this terminates "
                    "the agent loop."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Why the work is being refused.",
                        },
                    },
                    "required": ["reason"],
                },
            },
            {
                "name": "identity.self",
                "description": (
                    "Return the agent's own Principal record including "
                    "delegation chain. Use this when reasoning about "
                    "what scopes you're operating under."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]
        # Slice (b) adds: content.* tools per surface.content_rw /
        # content_ro, state.* per content_rw, channel.* per
        # surface.channels, event.emit per surface.events,
        # compute.invoke per surface.computes.
        return schemas

    def _extract_text(self, response) -> str:
        parts = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                parts.append(getattr(block, "text", ""))
        return "".join(parts)

    def _extract_cost(self, response) -> Optional[Cost]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        total = in_tok + out_tok
        if total == 0:
            return None
        return Cost(units=total, unit_type="tokens")


async def _maybe_await(callback, tool: str, args: Mapping[str, Any]):
    import inspect
    result = callback(tool, args)
    if inspect.isawaitable(result):
        return await result
    return result


# ── Registration ──


def _anthropic_agent_factory(config: Mapping[str, Any]) -> AnthropicAgentProvider:
    return AnthropicAgentProvider(config)


def register_anthropic_agent(
    provider_registry, contract_registry: ContractRegistry | None = None
) -> None:
    """Register the Anthropic agent provider against (compute, "ai-agent")."""
    provider_registry.register(
        category=Category.COMPUTE,
        contract_name="ai-agent",
        product_name="anthropic",
        factory=_anthropic_agent_factory,
        conformance="passing",
        version="0.9.0",
        contract_registry=contract_registry,
    )
