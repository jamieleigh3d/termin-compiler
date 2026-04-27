# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Anthropic LLM compute provider — first-party plugin against the
v0.9 llm contract surface (BRD §6.3.2).

Single-shot prompt → completion via the Anthropic Messages API. No
tool surface (the ai-agent contract owns multi-turn tool calling).

Configuration shape (deploy_config["bindings"]["compute"]["<name>"]
["config"]):
    {
        "model": "claude-haiku-4-5-20251001",
        "api_key": "${ANTHROPIC_API_KEY}",
        "max_tokens": 4096,                     # optional
        "default_sampling": {                   # optional
            "temperature": 0.7
        }
    }

Per BRD §6.3.4 the audit record stamps `provider_product="anthropic"`,
`model_identifier=<resolved model>`, and `provider_config_hash` of
the resolved config (with secrets redacted — see _provider_hash.py).

Phase 3 slice (a) lands this module without wiring it into the
compute_runner; the existing `termin_runtime/ai_provider.py` path
keeps serving until slice (b) cuts over.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional

from ..contracts import Category, ContractRegistry
from ..compute_contract import (
    AuditRecord, CompletionResult, Cost, LlmComputeProvider,
)
from ._provider_hash import hash_provider_config


class AnthropicLlmProvider:
    """Single-shot Anthropic completion.

    Constructs the Anthropic SDK client lazily so test imports of the
    module don't require a configured API key. The client is built on
    first call; subsequent calls reuse it.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self._model: str = str(
            self._config.get("model") or "claude-haiku-4-5-20251001"
        )
        self._api_key: Optional[str] = self._config.get("api_key")
        self._max_tokens: int = int(self._config.get("max_tokens", 4096))
        self._default_sampling: dict = dict(
            self._config.get("default_sampling", {})
        )
        self._config_hash = hash_provider_config(self._config)
        self._client = None  # lazy
        self._legacy = None  # lazy AIProvider (slice b interim)

    @property
    def legacy(self):
        """Slice (b) interim accessor: returns an AIProvider instance
        configured against this provider's config. Used by
        compute_runner.execute_compute during the cut-over so the
        existing prompt-building, tool-use forcing, and streaming
        paths keep working unchanged. Slice (d) deletes this and
        ports the legacy logic into the contract methods."""
        if self._legacy is not None:
            return self._legacy
        from ...ai_provider import AIProvider
        synthetic = {
            "ai_provider": {
                "service": "anthropic",
                "model": self._model,
                "api_key": self._api_key,
            }
        }
        self._legacy = AIProvider(synthetic)
        self._legacy.startup()
        return self._legacy

    @property
    def is_configured(self) -> bool:
        """Slice (b) interim. Mirrors AIProvider.is_configured shape."""
        return bool(
            self._api_key and not str(self._api_key).startswith("${")
        )

    @property
    def service(self) -> str:
        """Slice (b) interim. The product name doubles as the
        service-name token for legacy log strings."""
        return "anthropic"

    @property
    def model(self) -> str:
        """Slice (b) interim accessor."""
        return self._model

    def _get_client(self):
        if self._client is not None:
            return self._client
        # Validate config BEFORE pulling in the SDK — deployment-time
        # misconfiguration is more common than missing SDK and yields
        # a clearer error for the operator.
        if not self._api_key or self._api_key.startswith("${"):
            raise RuntimeError(
                "AnthropicLlmProvider config is missing a resolved "
                "'api_key'. Deploy config env-var interpolation must "
                "happen before the provider factory runs."
            )
        try:
            import anthropic  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "AnthropicLlmProvider requires the `anthropic` package. "
                "Install with: pip install anthropic"
            ) from e
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    async def complete(
        self,
        directive: str,
        objective: str,
        input_value: Any,
        output_schema: Optional[Mapping[str, Any]] = None,
        sampling_params: Optional[Mapping[str, Any]] = None,
    ) -> CompletionResult:
        # Assemble user prompt: objective with input value appended in
        # a stable structured form.
        user_message = self._build_user_message(objective, input_value)
        sampling = dict(self._default_sampling)
        if sampling_params:
            sampling.update(sampling_params)

        prompt_as_sent = f"<system>\n{directive}\n</system>\n{user_message}"
        started = time.monotonic()

        try:
            client = self._get_client()
            create_kwargs = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "system": directive,
                "messages": [{"role": "user", "content": user_message}],
                **sampling,
            }
            # When output_schema is supplied, force tool_use by
            # presenting the schema as a single tool that the model
            # must call. This gives us a structured dict result that
            # matches the schema rather than free-form text. Per
            # Anthropic API: tools=[<schema>] + tool_choice forces
            # the named tool.
            if output_schema:
                create_kwargs["tools"] = [output_schema]
                create_kwargs["tool_choice"] = {
                    "type": "tool",
                    "name": output_schema.get("name", "set_output"),
                }
            response = client.messages.create(**create_kwargs)
        except Exception as e:
            return self._build_error_result(prompt_as_sent, sampling, started, e)

        latency_ms = int((time.monotonic() - started) * 1000)
        cost = self._extract_cost(response)
        model_id = getattr(response, "model", self._model)

        if output_schema:
            # Extract the forced tool call's input dict.
            tool_name = output_schema.get("name", "set_output")
            output_value = self._extract_tool_input(response, tool_name)
            if output_value is None:
                return self._build_error_result(
                    prompt_as_sent, sampling, started,
                    RuntimeError(
                        f"Anthropic response did not include the "
                        f"forced tool_use for {tool_name!r}"
                    ),
                )
        else:
            # Free-form text completion.
            output_value = self._extract_text(response)

        audit = AuditRecord(
            provider_product="anthropic",
            model_identifier=str(model_id),
            provider_config_hash=self._config_hash,
            prompt_as_sent=prompt_as_sent,
            sampling_params=sampling,
            outcome="success",
            cost=cost,
            latency_ms=latency_ms,
        )
        return CompletionResult(
            outcome="success",
            output_value=output_value,
            audit_record=audit,
        )

    def _extract_tool_input(self, response, tool_name: str):
        for block in getattr(response, "content", []) or []:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == tool_name
            ):
                return getattr(block, "input", None)
        return None

    def _build_user_message(self, objective: str, input_value: Any) -> str:
        if input_value is None:
            return objective
        if isinstance(input_value, str):
            return f"{objective}\n\nInput:\n{input_value}"
        # Structured input — render as a small key:value block.
        if isinstance(input_value, Mapping):
            lines = [f"{k}: {v}" for k, v in input_value.items()]
            return f"{objective}\n\nInput:\n" + "\n".join(lines)
        return f"{objective}\n\nInput:\n{input_value!r}"

    def _build_error_result(
        self, prompt_as_sent: str, sampling: dict,
        started: float, error: Exception,
    ) -> CompletionResult:
        latency_ms = int((time.monotonic() - started) * 1000)
        audit = AuditRecord(
            provider_product="anthropic",
            model_identifier=self._model,
            provider_config_hash=self._config_hash,
            prompt_as_sent=prompt_as_sent,
            sampling_params=sampling,
            outcome="error",
            error_detail=f"{type(error).__name__}: {error}",
            latency_ms=latency_ms,
        )
        return CompletionResult(
            outcome="error",
            error_detail=f"{type(error).__name__}: {error}",
            audit_record=audit,
        )

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


# ── Registration ──


def _anthropic_llm_factory(config: Mapping[str, Any]) -> AnthropicLlmProvider:
    return AnthropicLlmProvider(config)


def register_anthropic_llm(
    provider_registry, contract_registry: ContractRegistry | None = None
) -> None:
    """Register the Anthropic LLM provider against (compute, "llm")."""
    provider_registry.register(
        category=Category.COMPUTE,
        contract_name="llm",
        product_name="anthropic",
        factory=_anthropic_llm_factory,
        conformance="passing",
        version="0.9.0",
        contract_registry=contract_registry,
    )
