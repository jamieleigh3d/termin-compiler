# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Stub LLM compute provider — first-party plugin against the v0.9
llm contract surface (BRD §6.3.2).

Scripted-response stub for deterministic tests and local development.
Does not call any LLM SDK; returns pre-configured outputs (or refuses
when configured to refuse). Same loading path as the real Anthropic
provider.

Per BRD §10 ("Stub providers required for every contract"), every
named contract ships with a stub product so dev/test deploy configs
can bind to a deterministic implementation.

Configuration shape (deploy_config["bindings"]["compute"]["<name>"]
["config"]):
    {
        "responses": {
            "<directive_or_objective_substring>": {
                "outcome": "success",
                "output_value": <any>,
            },
            ...
        },
        "default_response": { ... },        # optional fallback
        "model_identifier": "stub-llm-1",   # for audit record
    }

Tests that need scripted refusals or errors set the corresponding
outcome value plus refusal_reason / error_detail.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ..contracts import Category, ContractRegistry
from ..compute_contract import (
    AuditRecord, CompletionResult, LlmComputeProvider,
)
from ._provider_hash import hash_provider_config


class StubLlmProvider:
    """Scripted LLM completions for tests.

    The provider matches the directive + objective against a config-
    supplied response map. Match shape is "first key whose substring
    appears in directive+objective"; this lets tests configure
    distinct responses for distinct prompts without exact-match
    brittleness.

    If no key matches and `default_response` is set, returns that.
    Otherwise returns a generic success with output_value=None.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self._responses: dict[str, dict] = dict(self._config.get("responses", {}))
        self._default: Optional[dict] = self._config.get("default_response")
        self._model_id: str = str(self._config.get("model_identifier", "stub-llm-1"))
        self._config_hash = hash_provider_config(self._config)

    async def complete(
        self,
        directive: str,
        objective: str,
        input_value: Any,
        sampling_params: Optional[Mapping[str, Any]] = None,
    ) -> CompletionResult:
        prompt = f"{directive}\n{objective}"
        scripted = self._match(prompt)
        outcome = scripted.get("outcome", "success")

        audit = AuditRecord(
            provider_product="stub",
            model_identifier=self._model_id,
            provider_config_hash=self._config_hash,
            prompt_as_sent=prompt,
            sampling_params=dict(sampling_params or {}),
            outcome=outcome,
            refusal_reason=scripted.get("refusal_reason"),
            error_detail=scripted.get("error_detail"),
            cost=None,
            latency_ms=0,
        )
        return CompletionResult(
            outcome=outcome,
            output_value=scripted.get("output_value"),
            refusal_reason=scripted.get("refusal_reason"),
            error_detail=scripted.get("error_detail"),
            audit_record=audit,
        )

    def _match(self, prompt: str) -> dict:
        for key, response in self._responses.items():
            if key in prompt:
                return response
        if self._default is not None:
            return dict(self._default)
        return {"outcome": "success", "output_value": None}


# ── Registration ──


def _stub_llm_factory(config: Mapping[str, Any]) -> StubLlmProvider:
    return StubLlmProvider(config)


def register_stub_llm(
    provider_registry, contract_registry: ContractRegistry | None = None
) -> None:
    """Register the stub LLM provider against (compute, "llm")."""
    provider_registry.register(
        category=Category.COMPUTE,
        contract_name="llm",
        product_name="stub",
        factory=_stub_llm_factory,
        conformance="passing",
        version="0.9.0",
        contract_registry=contract_registry,
    )
