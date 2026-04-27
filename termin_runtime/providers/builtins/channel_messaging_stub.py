# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Stub messaging channel provider — first-party plugin against the v0.9
messaging contract surface (BRD §6.4.3).

Supports outbound call logging AND inbound message injection for test
setup. No platform SDK is called; safe for CI environments with no
Slack/Teams/Discord access.

Per BRD §6.4.5 ("Stub providers required for every contract"), this is
the default product for dev/test deploy configs that bind the messaging
contract.

Registration key: (Category.CHANNELS, "messaging", "stub").
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from ..contracts import Category, ContractRegistry
from ..channel_contract import (
    ChannelAuditRecord, ChannelSendResult, MessageRef,
    MessagingChannelProvider, _StubSubscription, _now_iso,
)


class MessagingChannelStub:
    """Stub messaging provider — records sends and supports inbound injection.

    Configuration shape (bindings.channels.<name>.config):
        {
            "target": "<channel-name>",         # used as default send target
            "subscription": "<channel-name>"    # used for subscribe target
        }

    Outbound tracking:
        stub.sent_messages  — list of dicts, one per send() call
        stub.reactions      — list of dicts, one per react() call
        stub.updates        — list of dicts, one per update() call

    Each sent_message dict:
        {
            "target": str, "text": str, "thread_ref": Optional[str],
            "ref": str,     # stub-generated message id
            "sent_at": str
        }

    Inbound simulation (for tests that trigger on inbound messages):
        await stub.inject_message("supplier-team", "reorder needed", sender_id="user1")
        # → calls all registered message_handlers for that target

    Subscription:
        sub = await stub.subscribe("channel-name", async_handler)
        await stub.inject_message("channel-name", "hello")
        sub.cancel()  # removes the handler

    Full example:
        stub = MessagingChannelStub({"target": "alerts"})
        ref = await stub.send("alerts", "Alert: low stock")
        assert len(stub.sent_messages) == 1
        assert stub.sent_messages[0]["text"] == "Alert: low stock"
        assert ref.id.startswith("stub-msg-")
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self._default_target: str = str(self._config.get("target", "stub-channel"))
        self.sent_messages: list[dict] = []
        self.reactions: list[dict] = []
        self.updates: list[dict] = []
        self._subscriptions: dict[str, list[Callable]] = {}
        self._reaction_subscriptions: dict[str, list[Callable]] = {}
        self._ref_counter: int = 0

    def _next_ref(self) -> str:
        self._ref_counter += 1
        return f"stub-msg-{self._ref_counter}"

    async def send(
        self,
        target: str,
        message_text: str,
        thread_ref: Optional[str] = None,
    ) -> MessageRef:
        """Record the send and return a stub MessageRef."""
        ref = self._next_ref()
        self.sent_messages.append({
            "target": target,
            "text": message_text,
            "thread_ref": thread_ref,
            "ref": ref,
            "sent_at": _now_iso(),
        })
        return MessageRef(id=ref, channel=target, thread_id=thread_ref)

    async def update(
        self,
        message_ref: str,
        new_text: str,
    ) -> None:
        """Record the update without calling any platform API."""
        self.updates.append({
            "ref": message_ref,
            "new_text": new_text,
            "updated_at": _now_iso(),
        })

    async def react(
        self,
        message_ref: str,
        emoji: str,
    ) -> None:
        """Record the reaction without calling any platform API."""
        self.reactions.append({
            "ref": message_ref,
            "emoji": emoji,
            "reacted_at": _now_iso(),
        })

    async def subscribe(
        self,
        target: str,
        message_handler: Callable,
        reaction_handler: Optional[Callable] = None,
    ) -> _StubSubscription:
        """Register handlers for the given target.

        Returns a subscription with a cancel() method that removes
        the handlers.
        """
        msg_list = self._subscriptions.setdefault(target, [])
        msg_list.append(message_handler)
        if reaction_handler is not None:
            rx_list = self._reaction_subscriptions.setdefault(target, [])
            rx_list.append(reaction_handler)
        return _StubSubscription(target, msg_list)

    # ── Test helper: inbound message injection ──

    async def inject_message(
        self,
        target: str,
        text: str,
        sender_id: str = "stub-sender",
        thread_id: Optional[str] = None,
    ) -> None:
        """Simulate an inbound message arriving on `target`.

        Calls all registered message_handlers for that target. Tests use
        this to exercise `When a message is received` channel handlers.

        Example:
            received = []
            async def handler(msg): received.append(msg)
            await stub.subscribe("alerts", handler)
            await stub.inject_message("alerts", "stock low", sender_id="u1")
            assert received[0]["text"] == "stock low"
        """
        payload = {
            "text": text,
            "sender_id": sender_id,
            "channel": target,
            "thread_id": thread_id,
            "received_at": _now_iso(),
        }
        for handler in list(self._subscriptions.get(target, [])):
            await handler(payload)

    async def inject_reaction(
        self,
        target: str,
        message_ref: str,
        emoji: str,
        sender_id: str = "stub-sender",
    ) -> None:
        """Simulate an inbound reaction event. Calls all reaction handlers."""
        payload = {
            "message_ref": message_ref,
            "emoji": emoji,
            "sender_id": sender_id,
            "channel": target,
            "received_at": _now_iso(),
        }
        for handler in list(self._reaction_subscriptions.get(target, [])):
            await handler(payload)


# ── Registration ──


def _messaging_stub_factory(config: Mapping[str, Any]) -> MessagingChannelStub:
    return MessagingChannelStub(config)


def register_messaging_stub(
    provider_registry,
    contract_registry: ContractRegistry | None = None,
) -> None:
    """Register the stub messaging provider against (channels, "messaging")."""
    provider_registry.register(
        category=Category.CHANNELS,
        contract_name="messaging",
        product_name="stub",
        factory=_messaging_stub_factory,
        conformance="passing",
        version="0.9.0",
        features=["send", "update", "react", "subscribe"],
        contract_registry=contract_registry,
    )
