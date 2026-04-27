# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 4 — Channel contract surface and stub provider tests.

Tests Protocol conformance, data shape validation, and stub behavior.
These are unit tests — no FastAPI app, no compiled examples required.

TDD: written before the stubs were implemented, then stubs written to
make them green.
"""

import pytest

from termin_runtime.providers.channel_contract import (
    ChannelAuditRecord,
    ChannelSendResult,
    MessageRef,
    WebhookChannelProvider,
    EmailChannelProvider,
    MessagingChannelProvider,
    EventStreamChannelProvider,
    CHANNEL_CONTRACT_ACTION_VOCAB,
    CHANNEL_CONTRACT_FULL_FEATURES,
)
from termin_runtime.providers.builtins.channel_webhook_stub import WebhookChannelStub
from termin_runtime.providers.builtins.channel_email_stub import (
    EmailChannelStub, CapturedEmail,
)
from termin_runtime.providers.builtins.channel_messaging_stub import MessagingChannelStub
from termin_runtime.providers import (
    Category, ContractRegistry, ProviderRegistry,
)
from termin_runtime.providers.builtins import register_builtins


# ── ChannelAuditRecord validation ──

class TestChannelAuditRecord:
    def test_valid_outbound_delivered(self):
        rec = ChannelAuditRecord(
            channel_name="alerts", provider_product="stub",
            direction="outbound", action="send", target="stub-channel",
            payload_summary="hello", outcome="delivered",
            attempt_count=1, latency_ms=0,
        )
        assert rec.outcome == "delivered"

    def test_valid_inbound(self):
        rec = ChannelAuditRecord(
            channel_name="inbound", provider_product="stub",
            direction="inbound", action="receive", target="stub",
            payload_summary="msg", outcome="delivered",
            attempt_count=1, latency_ms=5,
        )
        assert rec.direction == "inbound"
        assert rec.invoked_by is None

    def test_invalid_outcome_raises(self):
        with pytest.raises(ValueError, match="outcome"):
            ChannelAuditRecord(
                channel_name="a", provider_product="stub",
                direction="outbound", action="send", target="t",
                payload_summary="p", outcome="unknown",
                attempt_count=1, latency_ms=0,
            )

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError, match="direction"):
            ChannelAuditRecord(
                channel_name="a", provider_product="stub",
                direction="sideways", action="send", target="t",
                payload_summary="p", outcome="delivered",
                attempt_count=1, latency_ms=0,
            )


# ── ChannelSendResult validation ──

class TestChannelSendResult:
    def test_valid_delivered(self):
        r = ChannelSendResult(outcome="delivered")
        assert r.outcome == "delivered"
        assert r.attempt_count == 1
        assert r.audit_record is None

    def test_valid_queued(self):
        r = ChannelSendResult(outcome="queued", attempt_count=0)
        assert r.outcome == "queued"

    def test_invalid_outcome_raises(self):
        with pytest.raises(ValueError, match="outcome"):
            ChannelSendResult(outcome="ok")


# ── Protocol conformance ──

class TestProtocolConformance:
    """Verify each stub implements the corresponding Protocol."""

    def test_webhook_stub_implements_protocol(self):
        stub = WebhookChannelStub()
        assert isinstance(stub, WebhookChannelProvider)

    def test_email_stub_implements_protocol(self):
        stub = EmailChannelStub()
        assert isinstance(stub, EmailChannelProvider)

    def test_messaging_stub_implements_protocol(self):
        stub = MessagingChannelStub()
        assert isinstance(stub, MessagingChannelProvider)


# ── WebhookChannelStub behavior ──

class TestWebhookChannelStub:
    @pytest.mark.asyncio
    async def test_send_records_call(self):
        stub = WebhookChannelStub({"target": "https://example.com/hook"})
        result = await stub.send({"event": "created", "id": 1})
        assert len(stub.sent_calls) == 1
        assert stub.sent_calls[0]["body"] == {"event": "created", "id": 1}
        assert stub.sent_calls[0]["target"] == "https://example.com/hook"

    @pytest.mark.asyncio
    async def test_send_with_headers(self):
        stub = WebhookChannelStub()
        await stub.send("payload", headers={"X-Event": "test"})
        assert stub.sent_calls[0]["headers"]["X-Event"] == "test"

    @pytest.mark.asyncio
    async def test_send_returns_delivered(self):
        stub = WebhookChannelStub()
        result = await stub.send({"data": "x"})
        assert result.outcome == "delivered"
        assert result.attempt_count == 1
        assert result.audit_record is not None
        assert result.audit_record.outcome == "delivered"
        assert result.audit_record.action == "post"

    @pytest.mark.asyncio
    async def test_multiple_sends_accumulate(self):
        stub = WebhookChannelStub()
        await stub.send({"n": 1})
        await stub.send({"n": 2})
        await stub.send({"n": 3})
        assert len(stub.sent_calls) == 3
        assert [c["body"]["n"] for c in stub.sent_calls] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_default_target_when_no_config(self):
        stub = WebhookChannelStub()
        await stub.send("data")
        assert stub.sent_calls[0]["target"] == "stub"


# ── EmailChannelStub behavior ──

class TestEmailChannelStub:
    @pytest.mark.asyncio
    async def test_send_captures_to_inbox(self):
        stub = EmailChannelStub({"from": "alerts@acme.com"})
        result = await stub.send(
            recipients=["alice@acme.com", "bob@acme.com"],
            subject="Weekly digest",
            body="Here is your digest.",
        )
        assert len(stub.inbox) == 1
        assert result.outcome == "delivered"

    @pytest.mark.asyncio
    async def test_captured_email_fields(self):
        stub = EmailChannelStub()
        await stub.send(
            recipients=["alice@example.com"],
            subject="Alert",
            body="Body text",
            html_body="<b>Body text</b>",
            attachments=["report.pdf"],
        )
        captured = stub.inbox[0]
        assert isinstance(captured, CapturedEmail)
        assert captured.recipients == ["alice@example.com"]
        assert captured.subject == "Alert"
        assert captured.body == "Body text"
        assert captured.html_body == "<b>Body text</b>"
        assert captured.attachments == ["report.pdf"]

    @pytest.mark.asyncio
    async def test_send_without_optional_fields(self):
        stub = EmailChannelStub()
        await stub.send(
            recipients=["x@example.com"],
            subject="Test",
            body="Plain text only",
        )
        captured = stub.inbox[0]
        assert captured.html_body is None
        assert captured.attachments == []

    @pytest.mark.asyncio
    async def test_multiple_sends_accumulate(self):
        stub = EmailChannelStub()
        for i in range(5):
            await stub.send(["u@example.com"], f"Subject {i}", "body")
        assert len(stub.inbox) == 5
        assert stub.inbox[2].subject == "Subject 2"

    @pytest.mark.asyncio
    async def test_audit_record_present(self):
        stub = EmailChannelStub()
        result = await stub.send(["a@b.com"], "Hi", "body")
        assert result.audit_record is not None
        assert result.audit_record.action == "send"
        assert result.audit_record.outcome == "delivered"


# ── MessagingChannelStub behavior ──

class TestMessagingChannelStub:
    @pytest.mark.asyncio
    async def test_send_records_message(self):
        stub = MessagingChannelStub({"target": "supplier-team"})
        ref = await stub.send("supplier-team", "Alert: low stock on Widget A")
        assert len(stub.sent_messages) == 1
        assert stub.sent_messages[0]["text"] == "Alert: low stock on Widget A"
        assert stub.sent_messages[0]["target"] == "supplier-team"

    @pytest.mark.asyncio
    async def test_send_returns_message_ref(self):
        stub = MessagingChannelStub()
        ref = await stub.send("alerts", "hello")
        assert isinstance(ref, MessageRef)
        assert ref.id.startswith("stub-msg-")
        assert ref.channel == "alerts"

    @pytest.mark.asyncio
    async def test_send_message_refs_are_unique(self):
        stub = MessagingChannelStub()
        ref1 = await stub.send("ch", "msg1")
        ref2 = await stub.send("ch", "msg2")
        assert ref1.id != ref2.id

    @pytest.mark.asyncio
    async def test_send_with_thread_ref(self):
        stub = MessagingChannelStub()
        ref = await stub.send("ch", "reply text", thread_ref="thread-123")
        assert stub.sent_messages[0]["thread_ref"] == "thread-123"
        assert ref.thread_id == "thread-123"

    @pytest.mark.asyncio
    async def test_update_records(self):
        stub = MessagingChannelStub()
        await stub.update("msg-42", "Updated text")
        assert len(stub.updates) == 1
        assert stub.updates[0]["ref"] == "msg-42"
        assert stub.updates[0]["new_text"] == "Updated text"

    @pytest.mark.asyncio
    async def test_react_records(self):
        stub = MessagingChannelStub()
        await stub.react("msg-7", "👍")
        assert len(stub.reactions) == 1
        assert stub.reactions[0]["ref"] == "msg-7"
        assert stub.reactions[0]["emoji"] == "👍"

    @pytest.mark.asyncio
    async def test_subscribe_and_inject_message(self):
        stub = MessagingChannelStub()
        received = []

        async def handler(msg):
            received.append(msg)

        await stub.subscribe("supplier-team", handler)
        await stub.inject_message("supplier-team", "reorder needed", sender_id="user1")
        assert len(received) == 1
        assert received[0]["text"] == "reorder needed"
        assert received[0]["sender_id"] == "user1"
        assert received[0]["channel"] == "supplier-team"

    @pytest.mark.asyncio
    async def test_inject_message_no_handlers_noop(self):
        stub = MessagingChannelStub()
        # No exception when there are no handlers for the target
        await stub.inject_message("unsubscribed-channel", "msg")

    @pytest.mark.asyncio
    async def test_subscribe_cancel_removes_handler(self):
        stub = MessagingChannelStub()
        received = []

        async def handler(msg):
            received.append(msg)

        sub = await stub.subscribe("ch", handler)
        sub.cancel()
        await stub.inject_message("ch", "after cancel")
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_inject_reaction_calls_reaction_handler(self):
        stub = MessagingChannelStub()
        reactions_seen = []

        async def msg_handler(msg): pass
        async def rxn_handler(rxn): reactions_seen.append(rxn)

        await stub.subscribe("ch", msg_handler, reaction_handler=rxn_handler)
        await stub.inject_reaction("ch", "msg-1", "❤️", sender_id="u2")
        assert len(reactions_seen) == 1
        assert reactions_seen[0]["emoji"] == "❤️"

    @pytest.mark.asyncio
    async def test_multiple_subscribers_all_called(self):
        stub = MessagingChannelStub()
        calls = []

        async def h1(msg): calls.append(("h1", msg["text"]))
        async def h2(msg): calls.append(("h2", msg["text"]))

        await stub.subscribe("ch", h1)
        await stub.subscribe("ch", h2)
        await stub.inject_message("ch", "broadcast")
        assert ("h1", "broadcast") in calls
        assert ("h2", "broadcast") in calls


# ── Action vocabulary table ──

class TestActionVocabulary:
    def test_all_four_contracts_present(self):
        assert "webhook" in CHANNEL_CONTRACT_ACTION_VOCAB
        assert "email" in CHANNEL_CONTRACT_ACTION_VOCAB
        assert "messaging" in CHANNEL_CONTRACT_ACTION_VOCAB
        assert "event-stream" in CHANNEL_CONTRACT_ACTION_VOCAB

    def test_webhook_vocab(self):
        vocab = CHANNEL_CONTRACT_ACTION_VOCAB["webhook"]
        assert "Post" in vocab

    def test_email_vocab(self):
        vocab = CHANNEL_CONTRACT_ACTION_VOCAB["email"]
        assert "Subject is" in vocab
        assert "Body is" in vocab
        assert "Recipients are" in vocab

    def test_messaging_vocab(self):
        vocab = CHANNEL_CONTRACT_ACTION_VOCAB["messaging"]
        assert "Send a message" in vocab
        assert "Reply in thread to" in vocab
        assert "When a message is received" in vocab

    def test_full_features_messaging(self):
        feats = CHANNEL_CONTRACT_FULL_FEATURES["messaging"]
        assert "send" in feats
        assert "update" in feats
        assert "react" in feats
        assert "subscribe" in feats


# ── Provider registration ──

class TestChannelProviderRegistration:
    def test_register_builtins_registers_channel_stubs(self):
        contracts = ContractRegistry.default()
        providers = ProviderRegistry()
        register_builtins(providers, contracts)
        webhook = providers.get(Category.CHANNELS, "webhook", "stub")
        email = providers.get(Category.CHANNELS, "email", "stub")
        messaging = providers.get(Category.CHANNELS, "messaging", "stub")
        assert webhook is not None
        assert email is not None
        assert messaging is not None

    def test_webhook_stub_factory_produces_instance(self):
        contracts = ContractRegistry.default()
        providers = ProviderRegistry()
        register_builtins(providers, contracts)
        record = providers.get(Category.CHANNELS, "webhook", "stub")
        instance = record.factory({"target": "https://example.com"})
        assert isinstance(instance, WebhookChannelStub)

    def test_messaging_stub_features_declared(self):
        contracts = ContractRegistry.default()
        providers = ProviderRegistry()
        register_builtins(providers, contracts)
        record = providers.get(Category.CHANNELS, "messaging", "stub")
        assert "send" in record.features
        assert "update" in record.features
        assert "react" in record.features
        assert "subscribe" in record.features

    def test_webhook_stub_features_declared(self):
        contracts = ContractRegistry.default()
        providers = ProviderRegistry()
        register_builtins(providers, contracts)
        record = providers.get(Category.CHANNELS, "webhook", "stub")
        assert "send" in record.features

    def test_channel_contracts_registered_in_contract_registry(self):
        contracts = ContractRegistry.default()
        for name in ("webhook", "email", "messaging", "event-stream"):
            assert contracts.has_contract(Category.CHANNELS, name), \
                f"contract 'channels/{name}' not in registry"
