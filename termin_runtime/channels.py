"""Channel dispatcher — connects declared Channels to external services.

Reads channel declarations from the IR and connection config from
termin.deploy.json. Provides send/invoke/receive operations with
scope enforcement, type validation, and delivery semantics.
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

import httpx

try:
    import websockets
    import websockets.client
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

logger = logging.getLogger("termin.channels")


# ── Deploy config loading ──

def _resolve_env_vars(value: str) -> str:
    """Replace ${ENV_VAR} patterns with environment variable values."""
    def _sub(match):
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))
    if isinstance(value, str):
        return re.sub(r'\$\{(\w+)\}', _sub, value)
    return value


def _resolve_config_env(obj):
    """Recursively resolve ${ENV_VAR} in all string values of a config dict."""
    if isinstance(obj, dict):
        return {k: _resolve_config_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_config_env(v) for v in obj]
    if isinstance(obj, str):
        return _resolve_env_vars(obj)
    return obj


def load_deploy_config(path: str = None) -> dict:
    """Load and resolve a termin.deploy.json file.

    Args:
        path: Path to deploy config. If None, looks for termin.deploy.json
              in the current directory.

    Returns:
        Resolved config dict with ${ENV_VAR} substituted, or empty dict if not found.
    """
    if path is None:
        path = "termin.deploy.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return _resolve_config_env(raw)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[Termin] Warning: Failed to load deploy config from {path}: {e}")
        return {}


# ── Channel config types ──

@dataclass
class ChannelAuthConfig:
    auth_type: str = "none"         # bearer, api_key, mtls, oauth2, hmac, none
    token: str = ""
    header: str = "Authorization"
    secret: str = ""                # HMAC
    # Additional fields stored as extras
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "ChannelAuthConfig":
        return cls(
            auth_type=d.get("type", "none"),
            token=d.get("token", ""),
            header=d.get("header", "Authorization"),
            secret=d.get("secret", ""),
            extras={k: v for k, v in d.items() if k not in ("type", "token", "header", "secret")},
        )


@dataclass
class ChannelConfig:
    url: str = ""
    protocol: str = "http"          # http, websocket, grpc
    auth: ChannelAuthConfig = field(default_factory=ChannelAuthConfig)
    timeout_ms: int = 30000
    max_retries: int = 3
    backoff_ms: int = 1000
    reconnect: bool = True
    heartbeat_ms: int = 30000

    @classmethod
    def from_dict(cls, d: dict) -> "ChannelConfig":
        auth = ChannelAuthConfig.from_dict(d.get("auth", {}))
        retry = d.get("retry", {})
        return cls(
            url=d.get("url", ""),
            protocol=d.get("protocol", "http"),
            auth=auth,
            timeout_ms=d.get("timeout_ms", 30000),
            max_retries=retry.get("max_attempts", 3),
            backoff_ms=retry.get("backoff_ms", 1000),
            reconnect=d.get("reconnect", True),
            heartbeat_ms=d.get("heartbeat_ms", 30000),
        )


# ── WebSocket connection manager ──

class WebSocketConnection:
    """Manages a single persistent outbound WebSocket connection with auto-reconnect."""

    def __init__(self, channel_name: str, config: ChannelConfig,
                 on_message: Callable[[str, dict], Coroutine] = None):
        self.channel_name = channel_name
        self.config = config
        self.on_message = on_message
        self._ws = None
        self._reader_task: Optional[asyncio.Task] = None
        self._state = "disconnected"  # disconnected, connecting, connected, error
        self._reconnect_count = 0

    @property
    def state(self) -> str:
        return self._state

    async def connect(self):
        """Establish the WebSocket connection and start the reader loop."""
        if not HAS_WEBSOCKETS:
            self._state = "error"
            logger.error(f"Channel '{self.channel_name}': websockets package not installed")
            return

        self._state = "connecting"
        url = self.config.url

        # Build extra headers for auth
        extra_headers = {}
        auth = self.config.auth
        if auth.auth_type == "bearer" and auth.token:
            extra_headers["Authorization"] = f"Bearer {auth.token}"
        elif auth.auth_type == "api_key" and auth.token:
            extra_headers[auth.header] = auth.token

        try:
            self._ws = await websockets.client.connect(
                url,
                additional_headers=extra_headers,
                ping_interval=self.config.heartbeat_ms / 1000.0 if self.config.heartbeat_ms else None,
            )
            self._state = "connected"
            self._reconnect_count = 0
            logger.info(f"Channel '{self.channel_name}': WebSocket connected to {url}")

            # Start reader loop
            self._reader_task = asyncio.create_task(self._reader_loop())
        except Exception as e:
            self._state = "error"
            logger.error(f"Channel '{self.channel_name}': WebSocket connect failed: {e}")
            if self.config.reconnect:
                asyncio.create_task(self._reconnect())

    async def _reader_loop(self):
        """Read messages from the WebSocket and dispatch to on_message callback."""
        try:
            async for raw_message in self._ws:
                try:
                    if isinstance(raw_message, bytes):
                        raw_message = raw_message.decode("utf-8")
                    data = json.loads(raw_message)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    data = {"raw": raw_message}

                if self.on_message:
                    try:
                        await self.on_message(self.channel_name, data)
                    except Exception as e:
                        logger.error(f"Channel '{self.channel_name}': message handler error: {e}")
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Channel '{self.channel_name}': WebSocket connection closed")
        except Exception as e:
            logger.error(f"Channel '{self.channel_name}': WebSocket reader error: {e}")
        finally:
            self._state = "disconnected"
            if self.config.reconnect:
                asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        """Reconnect with exponential backoff."""
        self._reconnect_count += 1
        backoff = min(self.config.backoff_ms * (2 ** (self._reconnect_count - 1)) / 1000.0, 60.0)
        logger.info(f"Channel '{self.channel_name}': reconnecting in {backoff:.1f}s (attempt {self._reconnect_count})")
        await asyncio.sleep(backoff)
        await self.connect()

    async def send(self, data: dict) -> bool:
        """Send a JSON message over the WebSocket."""
        if self._state != "connected" or not self._ws:
            raise ChannelError(f"Channel '{self.channel_name}': WebSocket not connected (state={self._state})")
        try:
            await self._ws.send(json.dumps(data))
            return True
        except Exception as e:
            raise ChannelError(f"Channel '{self.channel_name}': WebSocket send failed: {e}")

    async def invoke(self, action_name: str, params: dict) -> dict:
        """Invoke an action over WebSocket using request/response convention.

        Sends: {"action": action_name, "params": params, "id": <uuid>}
        The response convention is for the remote to echo back the id.
        For now, fire-and-forget — the external service processes asynchronously.
        """
        if self._state != "connected" or not self._ws:
            raise ChannelError(f"Channel '{self.channel_name}': WebSocket not connected (state={self._state})")
        import uuid
        msg_id = str(uuid.uuid4())[:8]
        try:
            await self._ws.send(json.dumps({
                "action": action_name,
                "params": params,
                "id": msg_id,
            }))
            return {"ok": True, "status": "sent", "id": msg_id}
        except Exception as e:
            raise ChannelError(f"Channel '{self.channel_name}': WebSocket invoke failed: {e}")

    async def close(self):
        """Close the WebSocket connection."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._state = "disconnected"


# ── Channel dispatcher ──

class ChannelDispatcher:
    """Manages connections to external services via declared Channels.

    Reads channel specs from the IR and connection details from deploy config.
    Provides send (data), invoke (action), and receive (inbound) operations.
    Supports HTTP (reliable) and WebSocket (realtime) protocols.
    """

    def __init__(self, ir: dict, deploy_config: dict = None):
        self._ir = ir
        self._deploy = deploy_config or {}
        self._channel_specs: dict[str, dict] = {}   # snake_name -> IR channel spec
        self._channel_configs: dict[str, ChannelConfig] = {}  # display_name -> config
        self._http_client: Optional[httpx.AsyncClient] = None
        self._ws_connections: dict[str, WebSocketConnection] = {}  # display_name -> connection
        self._message_handlers: list[Callable] = []  # callbacks for inbound WS messages
        self._metrics: dict[str, dict] = {}  # channel_name -> {sent, received, errors, last_active, state}

        # Index channel specs by both display and snake names
        for ch in ir.get("channels", []):
            display = ch["name"]["display"]
            snake = ch["name"]["snake"]
            self._channel_specs[snake] = ch
            self._channel_specs[display] = ch
            self._metrics[display] = {
                "sent": 0, "received": 0, "errors": 0,
                "last_active": None, "state": "disconnected",
            }

            # Load config from deploy
            channel_configs = self._deploy.get("channels", {})
            if display in channel_configs:
                self._channel_configs[display] = ChannelConfig.from_dict(channel_configs[display])
            elif snake in channel_configs:
                self._channel_configs[display] = ChannelConfig.from_dict(channel_configs[snake])

    def on_ws_message(self, handler: Callable[[str, dict], Coroutine]):
        """Register a callback for inbound WebSocket messages.

        Handler signature: async def handler(channel_name: str, data: dict)
        """
        self._message_handlers.append(handler)

    async def _dispatch_ws_message(self, channel_name: str, data: dict):
        """Route an inbound WebSocket message to all registered handlers."""
        display = channel_name
        self._metrics.get(display, {})["received"] = \
            self._metrics.get(display, {}).get("received", 0) + 1
        self._metrics.get(display, {})["last_active"] = _now_iso()

        for handler in self._message_handlers:
            try:
                await handler(channel_name, data)
            except Exception as e:
                logger.error(f"Channel '{channel_name}': message handler error: {e}")

    async def startup(self):
        """Initialize HTTP client and connect outbound WebSocket channels."""
        self._http_client = httpx.AsyncClient(timeout=60.0)

        # Connect WebSocket channels
        for ch in self._ir.get("channels", []):
            display = ch["name"]["display"]
            config = self._channel_configs.get(display)
            if not config or config.protocol != "websocket":
                continue

            direction = ch.get("direction", "")
            # Outbound or bidirectional WebSocket channels get a persistent connection
            if direction in ("OUTBOUND", "BIDIRECTIONAL"):
                ws_conn = WebSocketConnection(
                    display, config,
                    on_message=self._dispatch_ws_message,
                )
                self._ws_connections[display] = ws_conn
                try:
                    await ws_conn.connect()
                    self._metrics[display]["state"] = ws_conn.state
                except Exception as e:
                    logger.error(f"Channel '{display}': initial WebSocket connect failed: {e}")
                    self._metrics[display]["state"] = "error"

    async def shutdown(self):
        """Close HTTP client and all WebSocket connections."""
        # Close WebSocket connections
        for name, ws_conn in self._ws_connections.items():
            await ws_conn.close()
            self._metrics.get(name, {})["state"] = "disconnected"
        self._ws_connections.clear()

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def get_spec(self, channel_name: str) -> Optional[dict]:
        """Get IR spec for a channel by display or snake name."""
        return self._channel_specs.get(channel_name)

    def get_config(self, channel_name: str) -> Optional[ChannelConfig]:
        """Get deploy config for a channel."""
        # Try display name first, then resolve from spec
        if channel_name in self._channel_configs:
            return self._channel_configs[channel_name]
        spec = self._channel_specs.get(channel_name)
        if spec:
            display = spec["name"]["display"]
            return self._channel_configs.get(display)
        return None

    def _build_headers(self, config: ChannelConfig) -> dict:
        """Build HTTP headers from auth config."""
        headers = {"Content-Type": "application/json"}
        auth = config.auth
        if auth.auth_type == "bearer" and auth.token:
            headers["Authorization"] = f"Bearer {auth.token}"
        elif auth.auth_type == "api_key" and auth.token:
            headers[auth.header] = auth.token
        return headers

    def _check_scope(self, channel_name: str, direction: str, user_scopes: set) -> bool:
        """Check if user has required scope for a channel operation."""
        spec = self.get_spec(channel_name)
        if not spec:
            return False
        # Check channel-level requirements
        for req in spec.get("requirements", []):
            if req["direction"] == direction:
                if req["scope"] not in user_scopes:
                    return False
        return True

    def _check_action_scope(self, channel_name: str, action_name: str, user_scopes: set) -> bool:
        """Check if user has required scope for an action invocation."""
        spec = self.get_spec(channel_name)
        if not spec:
            return False
        for action in spec.get("actions", []):
            if action["name"]["display"] == action_name or action["name"]["snake"] == action_name:
                for scope in action.get("required_scopes", []):
                    if scope not in user_scopes:
                        return False
                return True
        return False

    def get_action_spec(self, channel_name: str, action_name: str) -> Optional[dict]:
        """Get the IR spec for a specific action on a channel."""
        spec = self.get_spec(channel_name)
        if not spec:
            return None
        for action in spec.get("actions", []):
            if action["name"]["display"] == action_name or action["name"]["snake"] == action_name:
                return action
        return None

    # ── Send (data channels) ──

    async def channel_send(self, channel_name: str, data: dict, user_scopes: set = None) -> dict:
        """Send data through an outbound data channel.

        Args:
            channel_name: Channel display or snake name.
            data: Record dict to send.
            user_scopes: Caller's scopes for access control. None = skip check.

        Returns:
            {"ok": True, "status": <http_status>} on success.

        Raises:
            ChannelError on failure.
        """
        spec = self.get_spec(channel_name)
        if not spec:
            raise ChannelError(f"Unknown channel: {channel_name}")

        display = spec["name"]["display"]

        # Scope check
        if user_scopes is not None and not self._check_scope(channel_name, "send", user_scopes):
            raise ChannelScopeError(f"Insufficient scope to send on channel '{display}'")

        config = self.get_config(channel_name)
        if not config or not config.url:
            # No deploy config — log and return (channel is declared but not connected)
            print(f"[Termin] Channel '{display}': no deploy config, send skipped")
            self._metrics[display]["sent"] += 1
            return {"ok": True, "status": "not_configured", "channel": display}

        # Route by protocol
        if config.protocol == "websocket" and display in self._ws_connections:
            return await self._ws_send(display, data)
        else:
            return await self._http_send(display, config, data)

    async def _ws_send(self, display: str, data: dict) -> dict:
        """Send data over an outbound WebSocket connection."""
        ws_conn = self._ws_connections.get(display)
        if not ws_conn or ws_conn.state != "connected":
            self._metrics[display]["errors"] += 1
            raise ChannelError(f"Channel '{display}': WebSocket not connected")
        try:
            await ws_conn.send(data)
            self._metrics[display]["sent"] += 1
            self._metrics[display]["last_active"] = _now_iso()
            return {"ok": True, "status": "sent", "channel": display, "protocol": "websocket"}
        except ChannelError:
            self._metrics[display]["errors"] += 1
            raise

    async def _http_send(self, display: str, config: ChannelConfig, data: dict) -> dict:
        """Send data over HTTP with retry."""
        headers = self._build_headers(config)
        url = config.url
        last_error = None

        for attempt in range(config.max_retries + 1):
            try:
                response = await self._http_client.post(
                    url,
                    json=data,
                    headers=headers,
                    timeout=config.timeout_ms / 1000.0,
                )
                self._metrics[display]["sent"] += 1
                self._metrics[display]["last_active"] = _now_iso()

                if response.status_code < 400:
                    return {"ok": True, "status": response.status_code, "channel": display}
                else:
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    if response.status_code < 500:
                        # Client error — don't retry
                        break
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as e:
                last_error = str(e)

            # Backoff before retry
            if attempt < config.max_retries:
                backoff = config.backoff_ms * (2 ** attempt) / 1000.0
                await asyncio.sleep(backoff)

        self._metrics[display]["errors"] += 1
        raise ChannelError(f"Channel '{display}' send failed after {config.max_retries + 1} attempts: {last_error}")

    # ── Invoke (action channels) ──

    async def channel_invoke(
        self, channel_name: str, action_name: str, params: dict, user_scopes: set = None
    ) -> dict:
        """Invoke a typed action on a channel.

        Args:
            channel_name: Channel display or snake name.
            action_name: Action display or snake name.
            params: Input parameters matching the action's Takes spec.
            user_scopes: Caller's scopes for access control. None = skip check.

        Returns:
            Response dict from the external service.

        Raises:
            ChannelError on failure.
        """
        spec = self.get_spec(channel_name)
        if not spec:
            raise ChannelError(f"Unknown channel: {channel_name}")

        display = spec["name"]["display"]
        action_spec = self.get_action_spec(channel_name, action_name)
        if not action_spec:
            raise ChannelError(f"Unknown action '{action_name}' on channel '{display}'")

        action_display = action_spec["name"]["display"]
        action_snake = action_spec["name"]["snake"]

        # Scope check
        if user_scopes is not None and not self._check_action_scope(channel_name, action_name, user_scopes):
            raise ChannelScopeError(f"Insufficient scope to invoke '{action_display}' on channel '{display}'")

        # Validate input params against Takes spec
        for param_spec in action_spec.get("takes", []):
            if param_spec["name"] not in params:
                raise ChannelValidationError(
                    f"Missing required parameter '{param_spec['name']}' for action '{action_display}'"
                )

        config = self.get_config(channel_name)
        if not config or not config.url:
            print(f"[Termin] Channel '{display}' action '{action_display}': no deploy config, invoke skipped")
            self._metrics[display]["sent"] += 1
            return {"ok": True, "status": "not_configured", "channel": display, "action": action_display}

        # Route by protocol
        if config.protocol == "websocket" and display in self._ws_connections:
            ws_conn = self._ws_connections[display]
            if ws_conn.state != "connected":
                self._metrics[display]["errors"] += 1
                raise ChannelError(f"Channel '{display}': WebSocket not connected for action '{action_display}'")
            try:
                result = await ws_conn.invoke(action_snake, params)
                self._metrics[display]["sent"] += 1
                self._metrics[display]["last_active"] = _now_iso()
                return result
            except ChannelError:
                self._metrics[display]["errors"] += 1
                raise
        else:
            return await self._http_invoke(display, config, action_display, action_snake, params)

    async def _http_invoke(self, display: str, config: ChannelConfig,
                           action_display: str, action_snake: str, params: dict) -> dict:
        """Invoke an action over HTTP with retry."""
        # Convention: POST {base_url}/actions/{action-snake-name}
        headers = self._build_headers(config)
        url = f"{config.url.rstrip('/')}/actions/{action_snake}"
        last_error = None

        for attempt in range(config.max_retries + 1):
            try:
                response = await self._http_client.post(
                    url,
                    json=params,
                    headers=headers,
                    timeout=config.timeout_ms / 1000.0,
                )
                self._metrics[display]["sent"] += 1
                self._metrics[display]["last_active"] = _now_iso()

                if response.status_code < 400:
                    try:
                        result = response.json()
                    except (json.JSONDecodeError, ValueError):
                        result = {"raw": response.text}
                    return {"ok": True, "status": response.status_code, "result": result}
                else:
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    if response.status_code < 500:
                        break
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as e:
                last_error = str(e)

            if attempt < config.max_retries:
                backoff = config.backoff_ms * (2 ** attempt) / 1000.0
                await asyncio.sleep(backoff)

        self._metrics[display]["errors"] += 1
        raise ChannelError(f"Action '{action_display}' on channel '{display}' failed: {last_error}")

    # ── Metrics ──

    def get_metrics(self, channel_name: str) -> dict:
        """Get send/receive/error metrics for a channel."""
        spec = self.get_spec(channel_name)
        if spec:
            display = spec["name"]["display"]
            return self._metrics.get(display, {})
        return {}

    def is_configured(self, channel_name: str) -> bool:
        """Check if a channel has deploy config (i.e., is connected to something)."""
        config = self.get_config(channel_name)
        return config is not None and bool(config.url)

    def get_connection_state(self, channel_name: str) -> str:
        """Get live connection state for a channel.

        Returns: 'connected', 'connecting', 'disconnected', 'error', or 'not_configured'.
        """
        spec = self.get_spec(channel_name)
        if not spec:
            return "not_configured"
        display = spec["name"]["display"]
        config = self._channel_configs.get(display)
        if not config or not config.url:
            return "not_configured"
        if config.protocol == "websocket" and display in self._ws_connections:
            return self._ws_connections[display].state
        if config.protocol == "http":
            return "connected"  # HTTP is stateless, always "connected" if configured
        return "disconnected"

    def get_full_status(self) -> list[dict]:
        """Get full status of all channels for reflection.

        Returns a list of dicts with name, direction, delivery, protocol,
        connection state, and metrics.
        """
        result = []
        for ch in self._ir.get("channels", []):
            display = ch["name"]["display"]
            config = self._channel_configs.get(display)
            metrics = self._metrics.get(display, {})
            entry = {
                "name": display,
                "direction": ch.get("direction", ""),
                "delivery": ch.get("delivery", ""),
                "carries": ch.get("carries_content", ""),
                "actions": len(ch.get("actions", [])),
                "protocol": config.protocol if config else "none",
                "configured": bool(config and config.url),
                "state": self.get_connection_state(display),
                "metrics": {
                    "sent": metrics.get("sent", 0),
                    "received": metrics.get("received", 0),
                    "errors": metrics.get("errors", 0),
                    "last_active": metrics.get("last_active"),
                },
            }
            result.append(entry)
        return result


# ── Error types ──

class ChannelError(Exception):
    """Base error for channel operations."""
    pass

class ChannelScopeError(ChannelError):
    """Caller lacks required scope for channel operation."""
    pass

class ChannelValidationError(ChannelError):
    """Invalid parameters for channel action."""
    pass


# ── Utilities ──

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
