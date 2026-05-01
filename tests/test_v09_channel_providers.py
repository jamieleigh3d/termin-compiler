# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 4 Slice 4c — Channel provider runtime dispatch tests.

Tests that the ChannelDispatcher:
- Accepts a ProviderRegistry in its constructor
- Populates _channel_providers at startup() for channels with provider_contract
- Routes channel_send() through the stub provider when a provider is registered
- Returns log-and-drop dict when no binding is present (does not raise)
- Raises ChannelConfigError in strict mode when a binding is missing
- Routes email and messaging channels to the correct stub methods

These are unit tests against ChannelDispatcher directly (no compiled packages needed).
"""

import asyncio

import pytest

from termin_server.channels import ChannelDispatcher
from termin_server.channel_config import ChannelConfigError
from termin_server.providers import Category, ProviderRegistry, ContractRegistry
from termin_server.providers.builtins import register_builtins
from termin_server.providers.builtins.channel_webhook_stub import WebhookChannelStub
from termin_server.providers.builtins.channel_email_stub import EmailChannelStub
from termin_server.providers.builtins.channel_messaging_stub import MessagingChannelStub


# ── IR factory helpers ──

def _ch(display: str, contract: str, direction: str = "OUTBOUND") -> dict:
    """Build a minimal channel IR dict with a provider_contract."""
    snake = display.replace(" ", "_").replace("-", "_")
    pascal = display.replace(" ", "").replace("-", "")
    return {
        "name": {"display": display, "snake": snake, "pascal": pascal},
        "direction": direction,
        "delivery": "AUTO",
        "provider_contract": contract,
        "failure_mode": "log-and-drop",
        "carries_content": "",
        "requirements": [],
        "actions": [],
    }


def _ch_internal(display: str) -> dict:
    """Build a minimal internal channel IR dict (no provider_contract)."""
    snake = display.replace(" ", "_").replace("-", "_")
    pascal = display.replace(" ", "").replace("-", "")
    return {
        "name": {"display": display, "snake": snake, "pascal": pascal},
        "direction": "INTERNAL",
        "delivery": "AUTO",
        "provider_contract": None,
        "failure_mode": "log-and-drop",
        "carries_content": "",
        "requirements": [],
        "actions": [],
    }


def _ir(*channels) -> dict:
    """Build a minimal IR dict with the given channel dicts."""
    return {
        "name": "Test App",
        "channels": list(channels),
        "auth": {"scopes": ["admin"], "roles": [{"name": "admin", "scopes": ["admin"]}]},
        "content": [],
        "events": [],
        "computes": [],
    }


def _registry():
    """Build a ProviderRegistry with all built-in stubs registered."""
    reg = ProviderRegistry()
    creg = ContractRegistry.default()
    register_builtins(reg, creg)
    return reg


def _binding(provider: str = "stub", config: dict = None) -> dict:
    return {"provider": provider, "config": config or {}}


def _deploy(*name_binding_pairs) -> dict:
    """Build a v0.9 deploy config with bindings.channels entries.

    Usage: _deploy("alerts", _binding(), "digests", _binding("stub", {"from": "x"}))
    """
    channels = {}
    it = iter(name_binding_pairs)
    for name in it:
        binding = next(it)
        channels[name] = binding
    return {"bindings": {"channels": channels}}


# ── Startup: provider registration ──

class TestChannelDispatcherStartup:
    def test_startup_populates_webhook_provider(self):
        """startup() creates a webhook stub and stores it in _channel_providers."""
        ir = _ir(_ch("alerts", "webhook"))
        deploy = _deploy("alerts", _binding())
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        provider = dispatcher._channel_providers.get("alerts")
        assert provider is not None
        assert isinstance(provider, WebhookChannelStub)

    def test_startup_populates_email_provider(self):
        """startup() creates an email stub and stores it in _channel_providers."""
        ir = _ir(_ch("digests", "email"))
        deploy = _deploy("digests", _binding())
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        provider = dispatcher._channel_providers.get("digests")
        assert provider is not None
        assert isinstance(provider, EmailChannelStub)

    def test_startup_populates_messaging_provider(self):
        """startup() creates a messaging stub and stores it in _channel_providers."""
        ir = _ir(_ch("team chat", "messaging"))
        deploy = _deploy("team chat", _binding())
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        provider = dispatcher._channel_providers.get("team chat")
        assert provider is not None
        assert isinstance(provider, MessagingChannelStub)

    def test_startup_skips_internal_channel(self):
        """Internal channels have no provider_contract — startup() doesn't register them."""
        ir = _ir(_ch_internal("event-bus"))
        deploy = _deploy()
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        assert "event-bus" not in dispatcher._channel_providers

    def test_startup_skips_channel_with_no_binding(self):
        """No binding for a channel → _channel_providers entry absent (non-strict mode)."""
        ir = _ir(_ch("alerts", "webhook"))
        deploy = _deploy()   # no binding for "alerts"
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        assert "alerts" not in dispatcher._channel_providers

    def test_startup_strict_raises_on_missing_binding(self):
        """strict=True raises ChannelConfigError when an external channel has no binding."""
        ir = _ir(_ch("alerts", "webhook"))
        deploy = _deploy()   # no binding
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        with pytest.raises(ChannelConfigError):
            asyncio.run(dispatcher.startup(strict=True))

    def test_startup_strict_ok_with_binding_present(self):
        """strict=True does not raise when all external channels have bindings."""
        ir = _ir(_ch("alerts", "webhook"))
        deploy = _deploy("alerts", _binding())
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        # Should not raise
        asyncio.run(dispatcher.startup(strict=True))

    def test_startup_unknown_product_falls_back_to_stub(self):
        """Unknown product name → registry falls back to 'stub' for that contract."""
        ir = _ir(_ch("alerts", "webhook"))
        deploy = _deploy("alerts", _binding(provider="nonexistent-product"))
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        # Falls back to stub
        assert isinstance(dispatcher._channel_providers.get("alerts"), WebhookChannelStub)


# ── channel_send: routing through provider ──

class TestChannelSendDispatch:
    def test_webhook_send_routes_to_stub(self):
        """channel_send() calls WebhookChannelStub.send(); sent_calls increases."""
        ir = _ir(_ch("alerts", "webhook"))
        deploy = _deploy("alerts", _binding())
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        asyncio.run(
            dispatcher.channel_send("alerts", {"event": "stock_low"})
        )
        stub = dispatcher._channel_providers["alerts"]
        assert len(stub.sent_calls) == 1
        assert stub.sent_calls[0]["body"] == {"event": "stock_low"}

    def test_webhook_send_returns_ok(self):
        """channel_send() for webhook returns dict with ok=True."""
        ir = _ir(_ch("alerts", "webhook"))
        deploy = _deploy("alerts", _binding())
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        result = asyncio.run(
            dispatcher.channel_send("alerts", {"event": "stock_low"})
        )
        assert result["ok"] is True
        assert result["channel"] == "alerts"

    def test_email_send_routes_to_stub(self):
        """channel_send() for email channels the EmailChannelStub; inbox grows."""
        ir = _ir(_ch("digests", "email"))
        deploy = _deploy("digests", _binding())
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        asyncio.run(
            dispatcher.channel_send("digests", {
                "recipients": ["alice@example.com"],
                "subject": "Weekly digest",
                "body": "Here are your items.",
            })
        )
        stub = dispatcher._channel_providers["digests"]
        assert len(stub.inbox) == 1
        assert stub.inbox[0].subject == "Weekly digest"
        assert stub.inbox[0].recipients == ["alice@example.com"]

    def test_messaging_send_routes_to_stub(self):
        """channel_send() for messaging routes to MessagingChannelStub; sent_messages grows."""
        ir = _ir(_ch("team-chat", "messaging"))
        deploy = _deploy("team-chat", _binding(config={"target": "general"}))
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        asyncio.run(
            dispatcher.channel_send("team-chat", {"text": "Reorder needed for Widget A"})
        )
        stub = dispatcher._channel_providers["team-chat"]
        assert len(stub.sent_messages) == 1
        assert stub.sent_messages[0]["text"] == "Reorder needed for Widget A"
        assert stub.sent_messages[0]["target"] == "general"

    def test_messaging_send_uses_default_target_when_not_configured(self):
        """Messaging send without explicit target in config uses stub default."""
        ir = _ir(_ch("pings", "messaging"))
        deploy = _deploy("pings", _binding(config={}))  # no target in config
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        asyncio.run(
            dispatcher.channel_send("pings", {"text": "Hello"})
        )
        stub = dispatcher._channel_providers["pings"]
        assert len(stub.sent_messages) == 1
        # MessagingChannelStub defaults to "stub-channel" when no target in config
        assert stub.sent_messages[0]["target"] == "stub-channel"


# ── Log-and-drop behavior ──

class TestLogAndDrop:
    def test_send_with_no_provider_returns_not_configured(self):
        """No binding → channel_send returns {"ok": True, "status": "not_configured"}."""
        ir = _ir(_ch("alerts", "webhook"))
        deploy = _deploy()   # no binding
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        result = asyncio.run(
            dispatcher.channel_send("alerts", {"event": "stock_low"})
        )
        assert result["ok"] is True
        assert result["status"] == "not_configured"

    def test_send_with_no_provider_does_not_raise(self):
        """Log-and-drop: missing provider must not propagate any exception."""
        ir = _ir(_ch("alerts", "webhook"))
        deploy = _deploy()   # no binding
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        # Must not raise
        asyncio.run(
            dispatcher.channel_send("alerts", {"event": "stock_low"})
        )

    def test_send_on_unknown_channel_raises_channel_error(self):
        """Unknown channel name → ChannelError (not log-and-drop — spec violation)."""
        from termin_server.channel_config import ChannelError
        ir = _ir(_ch("alerts", "webhook"))
        deploy = _deploy("alerts", _binding())
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        with pytest.raises(ChannelError):
            asyncio.run(
                dispatcher.channel_send("nonexistent-channel", {"x": 1})
            )


# ── Failure modes (v0.9.1) ──

class TestSurfaceAsError:
    """v0.9.1 reference-runtime implementation of failure_mode='surface-as-error'.

    Source authors who set `Failure mode is surface-as-error` on a channel
    expect provider exceptions to propagate to the caller as ChannelError
    instead of being silently swallowed (log-and-drop default). The
    dispatcher reads `failure_mode` from the channel spec and re-raises
    the underlying exception wrapped as ChannelError when set."""

    def _ir_with_mode(self, mode: str) -> dict:
        ch = _ch("alerts", "webhook")
        ch["failure_mode"] = mode
        return _ir(ch)

    def test_surface_as_error_re_raises_provider_exception(self):
        """Provider raising → ChannelError propagates to caller."""
        from termin_server.channel_config import ChannelError
        ir = self._ir_with_mode("surface-as-error")
        deploy = _deploy("alerts", _binding())
        d = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(d.startup(strict=False))

        class BoomProvider:
            async def send(self, body=None, headers=None):
                raise RuntimeError("simulated upstream failure")

        d._channel_providers["alerts"] = BoomProvider()
        with pytest.raises(ChannelError) as exc:
            asyncio.run(d.channel_send("alerts", {"x": 1}))
        # The wrapped exception preserves the original error message so
        # operators can debug from the audit log.
        assert "simulated upstream failure" in str(exc.value)

    def test_surface_as_error_chains_original_exception(self):
        """Re-raised ChannelError carries the original exception via __cause__."""
        from termin_server.channel_config import ChannelError
        ir = self._ir_with_mode("surface-as-error")
        deploy = _deploy("alerts", _binding())
        d = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(d.startup(strict=False))

        original = RuntimeError("original error")

        class BoomProvider:
            async def send(self, body=None, headers=None):
                raise original

        d._channel_providers["alerts"] = BoomProvider()
        with pytest.raises(ChannelError) as exc:
            asyncio.run(d.channel_send("alerts", {"x": 1}))
        assert exc.value.__cause__ is original

    def test_surface_as_error_increments_error_metric(self):
        """surface-as-error still records the error in metrics — the
        difference from log-and-drop is propagation, not bookkeeping."""
        ir = self._ir_with_mode("surface-as-error")
        deploy = _deploy("alerts", _binding())
        d = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(d.startup(strict=False))

        class BoomProvider:
            async def send(self, body=None, headers=None):
                raise RuntimeError("boom")

        d._channel_providers["alerts"] = BoomProvider()
        try:
            asyncio.run(d.channel_send("alerts", {"x": 1}))
        except Exception:
            pass
        assert d._metrics["alerts"]["errors"] == 1

    def test_log_and_drop_still_default_when_mode_unspecified(self):
        """Sanity: a channel with no failure_mode still log-and-drops."""
        ir = _ir(_ch("alerts", "webhook"))   # default mode in _ch is log-and-drop
        deploy = _deploy("alerts", _binding())
        d = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(d.startup(strict=False))

        class BoomProvider:
            async def send(self, body=None, headers=None):
                raise RuntimeError("boom")

        d._channel_providers["alerts"] = BoomProvider()
        # Must not raise.
        result = asyncio.run(d.channel_send("alerts", {"x": 1}))
        assert result["ok"] is False
        assert result["outcome"] == "failed"


class TestQueueAndRetryFallback:
    """v0.9.x reference-runtime treatment of failure_mode='queue-and-retry'.

    The grammar accepts the line, the IR records it, the analyzer
    validates the value. Full implementation (exp backoff +
    dead-letter after configurable max-retry-hours, 24h cap) lands
    v0.10. Until then, the dispatcher falls back to log-and-drop
    with a logged warning so existing apps don't break."""

    def test_queue_and_retry_falls_back_to_log_and_drop_in_v091(self):
        ch = _ch("alerts", "webhook")
        ch["failure_mode"] = "queue-and-retry"
        ir = _ir(ch)
        deploy = _deploy("alerts", _binding())
        d = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(d.startup(strict=False))

        class BoomProvider:
            async def send(self, body=None, headers=None):
                raise RuntimeError("boom")

        d._channel_providers["alerts"] = BoomProvider()
        # v0.9.x: must not raise (fallback). v0.10 will replace this
        # with a deterministic queued-shape assertion when the worker
        # ships.
        result = asyncio.run(d.channel_send("alerts", {"x": 1}))
        assert result["ok"] is False
        assert result["outcome"] == "failed"


# ── Multiple channels ──

class TestMultipleChannels:
    def test_multiple_channels_each_get_own_provider(self):
        """Each channel gets its own stub instance — they don't share state."""
        ir = _ir(
            _ch("hook-a", "webhook"),
            _ch("hook-b", "webhook"),
        )
        deploy = _deploy("hook-a", _binding(), "hook-b", _binding())
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        asyncio.run(
            dispatcher.channel_send("hook-a", {"x": 1})
        )
        stub_a = dispatcher._channel_providers["hook-a"]
        stub_b = dispatcher._channel_providers["hook-b"]
        assert len(stub_a.sent_calls) == 1
        assert len(stub_b.sent_calls) == 0   # independent instances

    def test_mixed_provider_and_no_binding(self):
        """One channel configured, one not — each behaves independently."""
        ir = _ir(
            _ch("configured", "webhook"),
            _ch("missing", "webhook"),
        )
        deploy = _deploy("configured", _binding())
        dispatcher = ChannelDispatcher(ir, deploy, _registry())
        asyncio.run(dispatcher.startup(strict=False))
        # configured: provider present
        result_ok = asyncio.run(
            dispatcher.channel_send("configured", {"x": 1})
        )
        # missing: log-and-drop
        result_drop = asyncio.run(
            dispatcher.channel_send("missing", {"x": 1})
        )
        assert result_ok["ok"] is True
        assert result_ok.get("status") != "not_configured"
        assert result_drop["status"] == "not_configured"
