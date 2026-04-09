"""Tests for the Channel runtime — dispatcher, events, webhooks, actions.

Uses channel_demo IR with a mock external service (httpx mock transport).
"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from termin_runtime import create_termin_app
from termin_runtime.channels import (
    ChannelDispatcher, ChannelConfig, ChannelError, ChannelScopeError, ChannelValidationError,
    load_deploy_config, _resolve_env_vars,
)


IR_DIR = Path(__file__).parent.parent / "ir_dumps"
SEED_DIR = Path(__file__).parent.parent / "examples"


def _load_ir(name: str) -> str:
    return (IR_DIR / f"{name}_ir.json").read_text(encoding="utf-8")


def _load_seed(name: str) -> dict:
    path = SEED_DIR / f"{name}_seed.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


MOCK_DEPLOY_CONFIG = {
    "version": "0.1.0",
    "channels": {
        "pagerduty": {
            "url": "https://mock-pagerduty.test/api",
            "protocol": "http",
            "auth": {"type": "bearer", "token": "test-token"},
        },
        "cloud-provider": {
            "url": "https://mock-cloud.test/api/v1",
            "protocol": "http",
            "auth": {"type": "api_key", "token": "cloud-key", "header": "X-API-Key"},
        },
        "slack": {
            "url": "https://mock-slack.test/api",
            "protocol": "http",
            "auth": {"type": "bearer", "token": "slack-token"},
        },
    },
}


def _make_channel_client(deploy_config=None):
    """Create a TestClient for channel_demo with deploy config."""
    ir_json = _load_ir("channel_demo")
    seed = _load_seed("channel_demo")
    config = deploy_config or MOCK_DEPLOY_CONFIG
    app = create_termin_app(ir_json, seed_data=seed, deploy_config=config)
    return TestClient(app)


# ── Deploy config loading ──

class TestDeployConfigLoading:
    def test_resolve_env_vars(self):
        import os
        os.environ["TEST_TOKEN"] = "secret123"
        assert _resolve_env_vars("${TEST_TOKEN}") == "secret123"
        assert _resolve_env_vars("Bearer ${TEST_TOKEN}") == "Bearer secret123"
        assert _resolve_env_vars("no-vars-here") == "no-vars-here"
        del os.environ["TEST_TOKEN"]

    def test_resolve_missing_env_var_preserved(self):
        result = _resolve_env_vars("${NONEXISTENT_VAR_12345}")
        assert result == "${NONEXISTENT_VAR_12345}"

    def test_load_nonexistent_returns_empty(self):
        config = load_deploy_config("/nonexistent/path/deploy.json")
        assert config == {}


# ── Channel dispatcher unit tests ──

class TestChannelDispatcher:
    def setup_method(self):
        self.ir = json.loads(_load_ir("channel_demo"))
        self.dispatcher = ChannelDispatcher(self.ir, MOCK_DEPLOY_CONFIG)

    def test_get_spec_by_display_name(self):
        spec = self.dispatcher.get_spec("pagerduty")
        assert spec is not None
        assert spec["name"]["display"] == "pagerduty"

    def test_get_spec_by_snake_name(self):
        spec = self.dispatcher.get_spec("cloud_provider")
        assert spec is not None
        assert spec["name"]["snake"] == "cloud_provider"

    def test_get_config_returns_configured(self):
        config = self.dispatcher.get_config("pagerduty")
        assert config is not None
        assert config.url == "https://mock-pagerduty.test/api"
        assert config.auth.auth_type == "bearer"
        assert config.auth.token == "test-token"

    def test_get_config_returns_none_for_unconfigured(self):
        config = self.dispatcher.get_config("github-webhooks")
        assert config is None

    def test_is_configured(self):
        assert self.dispatcher.is_configured("pagerduty")
        assert self.dispatcher.is_configured("cloud-provider")
        assert not self.dispatcher.is_configured("github-webhooks")
        assert not self.dispatcher.is_configured("incident-bus")

    def test_get_action_spec(self):
        action = self.dispatcher.get_action_spec("cloud-provider", "restart-service")
        assert action is not None
        assert action["name"]["display"] == "restart-service"
        assert len(action["takes"]) == 2

    def test_get_action_spec_unknown(self):
        action = self.dispatcher.get_action_spec("cloud-provider", "nope")
        assert action is None

    def test_check_scope_pass(self):
        assert self.dispatcher._check_scope("pagerduty", "send", {"alerts.send"})

    def test_check_scope_fail(self):
        assert not self.dispatcher._check_scope("pagerduty", "send", {"incidents.view"})

    def test_check_action_scope_pass(self):
        assert self.dispatcher._check_action_scope("cloud-provider", "restart-service", {"infra.operate"})

    def test_check_action_scope_fail(self):
        assert not self.dispatcher._check_action_scope("cloud-provider", "restart-service", {"incidents.view"})


# ── Channel action invocation endpoint ──

class TestChannelActionEndpoint:
    def test_action_invoke_returns_not_configured_without_mock(self):
        """Without a real external service, configured channels return mock response."""
        with _make_channel_client(deploy_config={"channels": {}}) as client:
            client.cookies.set("termin_role", "operator")
            r = client.post(
                "/api/v1/channels/cloud_provider/actions/restart_service",
                json={"service": "checkout", "region": "us-east-1"},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "not_configured"

    def test_action_invoke_scope_denied(self):
        with _make_channel_client() as client:
            client.cookies.set("termin_role", "viewer")  # only has incidents.view
            r = client.post(
                "/api/v1/channels/cloud_provider/actions/restart_service",
                json={"service": "checkout", "region": "us-east-1"},
            )
            assert r.status_code == 403

    def test_action_invoke_unknown_channel(self):
        with _make_channel_client() as client:
            client.cookies.set("termin_role", "operator")
            r = client.post(
                "/api/v1/channels/nonexistent/actions/something",
                json={},
            )
            assert r.status_code == 404

    def test_action_invoke_unknown_action(self):
        with _make_channel_client() as client:
            client.cookies.set("termin_role", "operator")
            r = client.post(
                "/api/v1/channels/cloud_provider/actions/nonexistent",
                json={},
            )
            assert r.status_code == 404


# ── Channel send endpoint ──

class TestChannelSendEndpoint:
    def test_send_not_configured(self):
        with _make_channel_client(deploy_config={"channels": {}}) as client:
            client.cookies.set("termin_role", "responder")
            r = client.post(
                "/api/v1/channels/pagerduty/send",
                json={"title": "test incident", "severity": "high"},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "not_configured"

    def test_send_scope_denied(self):
        with _make_channel_client() as client:
            client.cookies.set("termin_role", "viewer")
            r = client.post(
                "/api/v1/channels/pagerduty/send",
                json={"title": "test"},
            )
            assert r.status_code == 403

    def test_send_unknown_channel(self):
        with _make_channel_client() as client:
            client.cookies.set("termin_role", "operator")
            r = client.post(
                "/api/v1/channels/nonexistent/send",
                json={},
            )
            assert r.status_code == 404


# ── Inbound webhook handler ──

class TestInboundWebhooks:
    def test_webhook_creates_record(self):
        """POST to /webhooks/{channel} creates a content record."""
        with _make_channel_client() as client:
            client.cookies.set("termin_role", "operator")

            # Count existing deployments first
            r_before = client.get("/api/v1/deployments")
            count_before = len(r_before.json())

            r = client.post(
                "/webhooks/github_webhooks",
                json={
                    "service": "webhook-test-svc",
                    "version": "v99.0.0",
                    "commit_sha": "deadbeef",
                    "deployed_by": "ci-pipeline",
                    "status": "succeeded",
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["ok"] is True
            assert "id" in body
            created_id = body["id"]

            # Verify record was created
            r2 = client.get("/api/v1/deployments")
            assert r2.status_code == 200
            deployments = r2.json()
            assert len(deployments) == count_before + 1
            new = next(d for d in deployments if d["id"] == created_id)
            assert new["service"] == "webhook-test-svc"
            assert new["version"] == "v99.0.0"

    def test_webhook_scope_denied(self):
        with _make_channel_client() as client:
            client.cookies.set("termin_role", "viewer")  # only has incidents.view, not deploy.read
            r = client.post(
                "/webhooks/github_webhooks",
                json={"service": "x", "version": "v1"},
            )
            assert r.status_code == 403

    def test_webhook_invalid_json(self):
        with _make_channel_client() as client:
            client.cookies.set("termin_role", "operator")
            r = client.post(
                "/webhooks/github_webhooks",
                content="not json",
                headers={"Content-Type": "application/json"},
            )
            assert r.status_code == 400

    def test_webhook_no_valid_fields(self):
        with _make_channel_client() as client:
            client.cookies.set("termin_role", "operator")
            r = client.post(
                "/webhooks/github_webhooks",
                json={"unknown_field": "value"},
            )
            assert r.status_code == 422

    def test_monitoring_feed_webhook(self):
        """The monitoring-feed inbound realtime channel also gets a webhook."""
        with _make_channel_client() as client:
            client.cookies.set("termin_role", "responder")
            r = client.post(
                "/webhooks/monitoring_feed",
                json={
                    "monitor_name": "cpu-check",
                    "metric": "cpu_percent",
                    "threshold": 80,
                    "current_value": 95,
                },
            )
            assert r.status_code == 200
            assert r.json()["ok"] is True


# ── Event-driven channel sends ──

class TestEventChannelSends:
    def test_critical_incident_triggers_pagerduty_send(self):
        """Creating a critical incident should trigger send to pagerduty channel."""
        with _make_channel_client(deploy_config={"channels": {}}) as client:
            client.cookies.set("termin_role", "responder")
            # Create a critical incident — should trigger event: Send incident to "pagerduty"
            r = client.post(
                "/api/v1/incidents",
                json={
                    "title": "Total outage",
                    "severity": "critical",
                    "affected_service": "payment-gateway",
                    "summary": "Everything is down",
                },
            )
            assert r.status_code in (200, 201)
            # The event handler runs but the channel is not configured,
            # so it logs "no deploy config, send skipped" — no error raised


# ── Channel reflection ──

class TestChannelReflection:
    def test_reflect_shows_channels(self):
        with _make_channel_client() as client:
            r = client.get("/api/reflect")
            assert r.status_code == 200
            ir = r.json()
            channels = ir.get("channels", [])
            assert len(channels) == 6
            names = {ch["name"]["display"] for ch in channels}
            assert "pagerduty" in names
            assert "cloud-provider" in names
            assert "slack" in names

    def test_reflect_channels_endpoint(self):
        """GET /api/reflect/channels returns live status for all channels."""
        with _make_channel_client() as client:
            r = client.get("/api/reflect/channels")
            assert r.status_code == 200
            channels = r.json()
            assert len(channels) == 6

            # Configured channels
            pagerduty = next(ch for ch in channels if ch["name"] == "pagerduty")
            assert pagerduty["configured"] is True
            assert pagerduty["protocol"] == "http"
            assert pagerduty["state"] == "connected"
            assert pagerduty["direction"] == "OUTBOUND"
            assert pagerduty["metrics"]["sent"] == 0

            # Unconfigured channels
            github = next(ch for ch in channels if ch["name"] == "github-webhooks")
            assert github["configured"] is False
            assert github["state"] == "not_configured"

            # Internal channels
            bus = next(ch for ch in channels if ch["name"] == "incident-bus")
            assert bus["configured"] is False
            assert bus["direction"] == "INTERNAL"

            # Action-only channel
            cloud = next(ch for ch in channels if ch["name"] == "cloud-provider")
            assert cloud["actions"] == 3
            assert cloud["carries"] == ""

    def test_reflect_single_channel(self):
        """GET /api/reflect/channels/{name} returns detailed status."""
        with _make_channel_client() as client:
            r = client.get("/api/reflect/channels/cloud-provider")
            assert r.status_code == 200
            ch = r.json()
            assert ch["name"] == "cloud-provider"
            assert ch["configured"] is True
            assert len(ch["actions"]) == 3
            assert "restart-service" in ch["actions"]

    def test_reflect_channel_not_found(self):
        with _make_channel_client() as client:
            r = client.get("/api/reflect/channels/nonexistent")
            assert r.status_code == 404


# ── WebSocket dispatcher unit tests ──

class TestWebSocketDispatcher:
    def test_ws_connection_state_initial(self):
        from termin_runtime.channels import WebSocketConnection
        config = ChannelConfig(url="ws://localhost:9999", protocol="websocket")
        ws = WebSocketConnection("test", config)
        assert ws.state == "disconnected"

    def test_dispatcher_routes_ws_protocol(self):
        """Channels with protocol=websocket should use WS connections, not HTTP."""
        ir = json.loads(_load_ir("channel_demo"))
        ws_config = {
            "channels": {
                "slack": {
                    "url": "wss://mock-slack.test/ws",
                    "protocol": "websocket",
                    "auth": {"type": "bearer", "token": "test"},
                },
            },
        }
        dispatcher = ChannelDispatcher(ir, ws_config)
        config = dispatcher.get_config("slack")
        assert config.protocol == "websocket"

    def test_get_full_status_includes_protocol(self):
        ir = json.loads(_load_ir("channel_demo"))
        dispatcher = ChannelDispatcher(ir, MOCK_DEPLOY_CONFIG)
        status = dispatcher.get_full_status()
        pagerduty = next(s for s in status if s["name"] == "pagerduty")
        assert pagerduty["protocol"] == "http"
        assert pagerduty["configured"] is True

    def test_get_connection_state_http_always_connected(self):
        ir = json.loads(_load_ir("channel_demo"))
        dispatcher = ChannelDispatcher(ir, MOCK_DEPLOY_CONFIG)
        assert dispatcher.get_connection_state("pagerduty") == "connected"

    def test_get_connection_state_unconfigured(self):
        ir = json.loads(_load_ir("channel_demo"))
        dispatcher = ChannelDispatcher(ir, {"channels": {}})
        assert dispatcher.get_connection_state("pagerduty") == "not_configured"
