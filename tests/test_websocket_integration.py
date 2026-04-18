"""Level 1: Async WebSocket integration tests.

Uses a REAL uvicorn server and REAL WebSocket client (not TestClient).
This catches bugs invisible to TestClient:
- Event loop isolation (background threads publishing to main loop)
- Push delivery timing
- Event bus cross-thread communication

These tests exercise the actual production execution model.
"""

import asyncio
import json
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
import websockets
import websockets.client

from termin_runtime import create_termin_app

IR_DIR = Path(__file__).parent.parent / "ir_dumps"


def _load_ir(name: str) -> str:
    return (IR_DIR / f"{name}_ir.json").read_text(encoding="utf-8")


class UvicornTestServer:
    """Runs uvicorn in a background thread on a random port."""

    def __init__(self, app, port=0):
        self.app = app
        self.port = port or self._find_free_port()
        self.server = None
        self.thread = None

    @staticmethod
    def _find_free_port():
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self):
        return f"ws://127.0.0.1:{self.port}/runtime/ws"

    def start(self):
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port,
                                log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        # Wait for server to be ready
        for _ in range(100):
            try:
                with httpx.Client() as client:
                    client.get(f"{self.base_url}/api/reflect", timeout=1.0)
                return
            except (httpx.ConnectError, httpx.ReadTimeout, OSError):
                time.sleep(0.1)
        raise RuntimeError("Server didn't start within 10 seconds")

    def stop(self):
        if self.server:
            self.server.should_exit = True
            self.thread.join(timeout=5)


@pytest.fixture(scope="module")
def agent_simple_server():
    """Start a real server with agent_simple app (shared across module)."""
    ir_json = _load_ir("agent_simple")
    app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
    server = UvicornTestServer(app)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
def channel_simple_server():
    """Start a real server with channel_simple app (shared across module)."""
    ir_json = _load_ir("channel_simple")
    app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
    server = UvicornTestServer(app)
    server.start()
    yield server
    server.stop()


# ── Level 1: Real WebSocket push tests ──

class TestRealWebSocketPush:
    """Tests using a real server + real WebSocket client.

    These catch bugs that TestClient misses because TestClient
    runs synchronously on one thread/loop.
    """

    async def _recv_until(self, ws, op, timeout=5, max_msgs=10):
        """Receive messages until we get one with the specified op."""
        for _ in range(max_msgs):
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            if msg.get("op") == op:
                return msg
        pytest.fail(f"Never received op='{op}' after {max_msgs} messages")

    @pytest.mark.asyncio
    async def test_ws_push_on_api_create(self, agent_simple_server):
        """POST to API → real WebSocket subscriber receives push."""
        server = agent_simple_server

        async with websockets.client.connect(server.ws_url) as ws:
            # Subscribe
            await ws.send(json.dumps({
                "v": 1, "ch": "content.completions", "op": "subscribe",
                "ref": "sub1", "payload": {}
            }))
            # Consume subscribe response (may arrive after identity push)
            resp = await self._recv_until(ws, "response")

            # Create record via API
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{server.base_url}/api/v1/completions",
                    json={"prompt": "real ws test"},
                    cookies={"termin_role": "anonymous"},
                )
                assert r.status_code == 201

            # Should receive push — use _recv_until to tolerate interleaved
            # identity/system pushes and slow broadcast on loaded machines
            push = await self._recv_until(ws, "push", timeout=5)
            assert "completions" in push["ch"]
            assert push["payload"]["prompt"] == "real ws test"
            assert "id" in push["payload"]

    @pytest.mark.asyncio
    async def test_ws_push_on_form_create(self, agent_simple_server):
        """AJAX form POST → real WebSocket subscriber receives push."""
        server = agent_simple_server

        async with websockets.client.connect(server.ws_url) as ws:
            await ws.send(json.dumps({
                "v": 1, "ch": "content.completions", "op": "subscribe",
                "ref": "sub1", "payload": {}
            }))
            resp = await self._recv_until(ws, "response")

            # Create via AJAX form POST
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{server.base_url}/agent",
                    data={"prompt": "form ws test"},
                    headers={"Accept": "application/json",
                             "X-Requested-With": "XMLHttpRequest"},
                    cookies={"termin_role": "anonymous"},
                )
                assert r.status_code == 200

            push = await self._recv_until(ws, "push", timeout=5)
            assert push["payload"]["prompt"] == "form ws test"

    @pytest.mark.asyncio
    async def test_ws_subscribe_returns_current_data(self, agent_simple_server):
        """Subscribe after records exist → current data included in response."""
        server = agent_simple_server

        # Create a record first
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{server.base_url}/api/v1/completions",
                json={"prompt": "existing record"},
                cookies={"termin_role": "anonymous"},
            )

        # Now subscribe — should get current data
        async with websockets.client.connect(server.ws_url) as ws:
            await ws.send(json.dumps({
                "v": 1, "ch": "content.completions", "op": "subscribe",
                "ref": "sub1", "payload": {}
            }))

            resp = await self._recv_until(ws, "response")
            assert "current" in resp["payload"]
            records = resp["payload"]["current"]
            assert any(r["prompt"] == "existing record" for r in records)

    @pytest.mark.asyncio
    async def test_ws_no_duplicate_pushes(self, agent_simple_server):
        """One create produces exactly one push — no duplicates."""
        server = agent_simple_server

        async with websockets.client.connect(server.ws_url) as ws:
            await ws.send(json.dumps({
                "v": 1, "ch": "content.completions", "op": "subscribe",
                "ref": "sub1", "payload": {}
            }))
            await self._recv_until(ws, "response")

            # Create ONE record
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{server.base_url}/api/v1/completions",
                    json={"prompt": "dedup test"},
                    cookies={"termin_role": "anonymous"},
                )

            # Should receive exactly ONE push
            push1 = await self._recv_until(ws, "push", timeout=5)
            assert push1["payload"]["prompt"] == "dedup test"

            # Should NOT receive another push within 1 second
            try:
                push2 = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                if push2["op"] == "push" and "completions" in push2.get("ch", ""):
                    pytest.fail(f"Received duplicate push: {push2}")
            except asyncio.TimeoutError:
                pass  # Expected — no duplicate

    @pytest.mark.asyncio
    async def test_ws_push_payload_is_record_not_wrapper(self, agent_simple_server):
        """Push payload should be the record dict, not an event wrapper."""
        server = agent_simple_server

        async with websockets.client.connect(server.ws_url) as ws:
            await ws.send(json.dumps({
                "v": 1, "ch": "content.completions", "op": "subscribe",
                "ref": "sub1", "payload": {}
            }))
            for _ in range(5):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if msg["op"] == "response":
                    break

            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{server.base_url}/api/v1/completions",
                    json={"prompt": "payload test"},
                    cookies={"termin_role": "anonymous"},
                )

            push = await self._recv_until(ws, "push", timeout=5)
            payload = push["payload"]

            # Payload should be the record directly
            # Use data-termin-* field names, not English labels
            assert "id" in payload, f"Payload missing 'id': {list(payload.keys())}"
            assert "prompt" in payload, f"Payload missing 'prompt': {list(payload.keys())}"
            # Should NOT be wrapped in an event dict
            assert "channel_id" not in payload, "Payload is a raw event wrapper, not a record"
            assert "record" not in payload, "Payload contains nested 'record' key"

    @pytest.mark.asyncio
    async def test_ws_push_from_webhook_source(self, channel_simple_server):
        """Webhook-created records should push with correct payload.

        This catches the payload wrapping bug where webhook events use
        'data' key but broadcast extracted 'record' key.
        """
        server = channel_simple_server

        async with websockets.client.connect(server.ws_url) as ws:
            # Subscribe to echoes (the webhook target)
            await ws.send(json.dumps({
                "v": 1, "ch": "content.echoes", "op": "subscribe",
                "ref": "sub1", "payload": {}
            }))
            await self._recv_until(ws, "response")

            # Create via webhook endpoint (simulates inbound channel)
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{server.base_url}/webhooks/echo_receiver",
                    json={"title": "webhook push test", "body": "via webhook"},
                    cookies={"termin_role": "anonymous"},
                )
                assert r.status_code == 200

            # Should receive push with record fields directly
            push = await self._recv_until(ws, "push", timeout=5)
            payload = push["payload"]
            assert "id" in payload, f"Webhook push missing 'id': {list(payload.keys())}"
            assert "title" in payload, f"Webhook push missing 'title': {list(payload.keys())}"
            assert payload["title"] == "webhook push test"
            # Must NOT be wrapped
            assert "channel_id" not in payload
            assert "data" not in payload or isinstance(payload.get("data"), str)

    @pytest.mark.asyncio
    async def test_ws_push_from_background_compute(self):
        """Records updated by a background Compute thread should push to WS.

        Uses a mock AI provider that returns immediately. This exercises
        the actual background thread + event loop isolation path that caused
        the original sync bug.
        """
        from termin_runtime.ai_provider import AIProvider

        ir_json = _load_ir("agent_simple")

        # Mock deploy config with a fake AI provider
        mock_deploy = {
            "ai_provider": {
                "service": "anthropic",
                "model": "mock",
                "api_key": "mock-key",
            }
        }

        app = create_termin_app(ir_json, strict_channels=False, deploy_config=mock_deploy)

        # Patch the AI provider's complete method to return immediately
        # without calling a real API
        from unittest.mock import AsyncMock
        for route in app.routes:
            pass  # We need to patch at the module level

        # Patch the Anthropic client import to return a mock
        import termin_runtime.ai_provider as ai_mod
        original_startup = AIProvider.startup

        def mock_startup(self):
            """Mock startup that creates a fake client."""
            self._client = True  # truthy but not a real client

        original_complete = AIProvider.complete

        async def mock_complete(self, system_prompt, user_message, output_tool):
            """Mock complete that returns a fake response after a tiny delay."""
            await asyncio.sleep(0.1)  # simulate LLM latency
            return {"thinking": "mock thinking", "response": "mock LLM response"}

        AIProvider.startup = mock_startup
        AIProvider.complete = mock_complete

        try:
            server = UvicornTestServer(app)
            server.start()

            try:
                async with websockets.client.connect(server.ws_url) as ws:
                    await ws.send(json.dumps({
                        "v": 1, "ch": "content.completions", "op": "subscribe",
                        "ref": "sub1", "payload": {}
                    }))
                    await self._recv_until(ws, "response")

                    # Create a record — this triggers the event, which fires
                    # the mock LLM Compute in a BACKGROUND THREAD
                    async with httpx.AsyncClient() as client:
                        r = await client.post(
                            f"{server.base_url}/api/v1/completions",
                            json={"prompt": "bg thread test"},
                            cookies={"termin_role": "anonymous"},
                        )
                        assert r.status_code == 201

                    # Should receive the created push first
                    created = await self._recv_until(ws, "push", timeout=5)
                    assert "created" in created["ch"]
                    record_id = created["payload"]["id"]

                    # Then should receive the UPDATE push from the background
                    # thread (the mock LLM fills in the response field)
                    updated = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    assert updated["op"] == "push"
                    assert "updated" in updated["ch"]
                    assert updated["payload"]["response"] == "mock LLM response"
            finally:
                server.stop()
        finally:
            AIProvider.startup = original_startup
            AIProvider.complete = original_complete

    @pytest.mark.asyncio
    async def test_ws_no_push_for_unsubscribed_content(self, channel_simple_server):
        """Creating content type A doesn't push to subscribers of content type B."""
        server = channel_simple_server

        async with websockets.client.connect(server.ws_url) as ws:
            # Subscribe to echoes only
            await ws.send(json.dumps({
                "v": 1, "ch": "content.echoes", "op": "subscribe",
                "ref": "sub1", "payload": {}
            }))
            await self._recv_until(ws, "response")

            # Create a NOTE (not an echo)
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{server.base_url}/api/v1/notes",
                    json={"title": "should not push", "body": "test"},
                    cookies={"termin_role": "anonymous"},
                )

            # Should NOT receive a push (we're subscribed to echoes, not notes)
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                if msg["op"] == "push" and "echoes" not in msg.get("ch", ""):
                    # Got a push for notes — but we only subscribed to echoes
                    # This is only wrong if the channel is notes
                    if "notes" in msg.get("ch", ""):
                        pytest.fail(f"Received push for unsubscribed content: {msg['ch']}")
            except asyncio.TimeoutError:
                pass  # Expected — no push for unsubscribed content
