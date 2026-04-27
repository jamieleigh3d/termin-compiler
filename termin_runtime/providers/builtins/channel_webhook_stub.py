# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Stub webhook channel provider — first-party plugin against the v0.9
webhook contract surface (BRD §6.4.1).

Records every send() call without making any HTTP request. Tests inspect
`stub.sent_calls` to verify dispatch behavior. Safe for CI environments
that have no outbound network access.

Per BRD §6.4.5 ("Stub providers required for every contract"), this is
the default product for dev/test deploy configs that bind the webhook
contract.

Registration key: (Category.CHANNELS, "webhook", "stub").
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ..contracts import Category, ContractRegistry
from ..channel_contract import (
    ChannelAuditRecord, ChannelSendResult, WebhookChannelProvider,
    _now_iso,
)


class WebhookChannelStub:
    """Stub webhook provider — records sends without making HTTP calls.

    Configuration shape (bindings.channels.<name>.config):
        {
            "target": "<logical-target-label>"  # optional; used in audit
        }

    After construction, `sent_calls` is a list of dicts:
        [
            {
                "body": <any>,
                "headers": {"Header-Name": "value", ...},
                "sent_at": "<iso8601>",
                "target": "<target from config or 'stub'>",
            },
            ...
        ]

    Tests that need to assert channel dispatch happened:
        stub = WebhookChannelStub({"target": "test-target"})
        await stub.send({"event": "created"})
        assert len(stub.sent_calls) == 1
        assert stub.sent_calls[0]["body"] == {"event": "created"}
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self._target: str = str(self._config.get("target", "stub"))
        self.sent_calls: list[dict] = []

    async def send(
        self,
        body: Any,
        headers: Optional[Mapping[str, str]] = None,
    ) -> ChannelSendResult:
        """Record the send without making any HTTP request."""
        record = {
            "body": body,
            "headers": dict(headers or {}),
            "sent_at": _now_iso(),
            "target": self._target,
        }
        self.sent_calls.append(record)
        audit = ChannelAuditRecord(
            channel_name="(stub)",
            provider_product="stub",
            direction="outbound",
            action="post",
            target=self._target,
            payload_summary=str(body)[:200],
            outcome="delivered",
            attempt_count=1,
            latency_ms=0,
        )
        return ChannelSendResult(
            outcome="delivered",
            attempt_count=1,
            latency_ms=0,
            audit_record=audit,
        )


# ── Registration ──


def _webhook_stub_factory(config: Mapping[str, Any]) -> WebhookChannelStub:
    return WebhookChannelStub(config)


def register_webhook_stub(
    provider_registry,
    contract_registry: ContractRegistry | None = None,
) -> None:
    """Register the stub webhook provider against (channels, "webhook")."""
    provider_registry.register(
        category=Category.CHANNELS,
        contract_name="webhook",
        product_name="stub",
        factory=_webhook_stub_factory,
        conformance="passing",
        version="0.9.0",
        features=["send"],
        contract_registry=contract_registry,
    )
