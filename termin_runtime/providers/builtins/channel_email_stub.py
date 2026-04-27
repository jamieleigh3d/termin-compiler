# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Stub email channel provider — first-party plugin against the v0.9
email contract surface (BRD §6.4.2).

Captures every send() call to a queryable inbox. Tests inspect
`stub.inbox` to verify email dispatch behavior. No SMTP connection is
opened; safe for CI environments with no outbound mail relay.

Per BRD §6.4.5 ("Stub providers required for every contract"), this is
the default product for dev/test deploy configs that bind the email
contract.

Registration key: (Category.CHANNELS, "email", "stub").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from ..contracts import Category, ContractRegistry
from ..channel_contract import (
    ChannelAuditRecord, ChannelSendResult, EmailChannelProvider,
    _now_iso,
)


@dataclass
class CapturedEmail:
    """One captured email in the stub inbox.

    Tests read these fields to verify email content:
        assert stub.inbox[0].subject == "Reorder alert: Widget A"
        assert "alice@example.com" in stub.inbox[0].recipients
    """
    recipients: list[str]
    subject: str
    body: str
    html_body: Optional[str]
    attachments: list[Any]
    sent_at: str


class EmailChannelStub:
    """Stub email provider — captures sends to a queryable inbox.

    Configuration shape (bindings.channels.<name>.config):
        {
            "from": "noreply@example.com"   # optional; used in audit
        }

    After construction, `inbox` is a list of CapturedEmail objects.
    Use it in tests to verify dispatch:

        stub = EmailChannelStub({"from": "alerts@acme.com"})
        await stub.send(
            recipients=["alice@acme.com"],
            subject="Weekly digest",
            body="Here is your digest.",
        )
        assert len(stub.inbox) == 1
        assert stub.inbox[0].subject == "Weekly digest"
        assert stub.inbox[0].recipients == ["alice@acme.com"]
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self._from: str = str(self._config.get("from", "stub@termin.dev"))
        self.inbox: list[CapturedEmail] = []

    async def send(
        self,
        recipients: Sequence[str],
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        attachments: Optional[Sequence[Any]] = None,
    ) -> ChannelSendResult:
        """Capture the email without opening an SMTP connection."""
        captured = CapturedEmail(
            recipients=list(recipients),
            subject=subject,
            body=body,
            html_body=html_body,
            attachments=list(attachments or []),
            sent_at=_now_iso(),
        )
        self.inbox.append(captured)
        audit = ChannelAuditRecord(
            channel_name="(stub)",
            provider_product="stub",
            direction="outbound",
            action="send",
            target=", ".join(recipients[:3]) + ("..." if len(recipients) > 3 else ""),
            payload_summary=f"Subject: {subject[:100]}",
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


def _email_stub_factory(config: Mapping[str, Any]) -> EmailChannelStub:
    return EmailChannelStub(config)


def register_email_stub(
    provider_registry,
    contract_registry: ContractRegistry | None = None,
) -> None:
    """Register the stub email provider against (channels, "email")."""
    provider_registry.register(
        category=Category.CHANNELS,
        contract_name="email",
        product_name="stub",
        factory=_email_stub_factory,
        conformance="passing",
        version="0.9.0",
        features=["send"],
        contract_registry=contract_registry,
    )
