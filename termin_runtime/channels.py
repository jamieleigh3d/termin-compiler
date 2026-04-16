"""Channel dispatcher — connects declared Channels to external services.

Reads channel declarations from the IR and connection config from
termin.deploy.json. Provides send/invoke/receive operations with
scope enforcement, type validation, and delivery semantics.

Subsystem modules:
  - channel_config.py: Deploy config loading, validation, config types
  - channel_ws.py: Outbound WebSocket connection with auto-reconnect
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

import httpx

from .channel_config import (
    load_deploy_config, check_deploy_config_warnings, validate_channel_config,
    ChannelConfigError, ChannelAuthConfig, ChannelConfig,
    ChannelError, ChannelScopeError, ChannelValidationError,
    _resolve_env_vars, _resolve_config_env, _check_unresolved_vars,
)
from .channel_ws import WebSocketConnection

logger = logging.getLogger("termin.channels")


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
        """Register a callback for inbound WebSocket messages."""
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

    def validate(self) -> list[str]:
        """Validate that all non-internal channels have deploy config."""
        return validate_channel_config(self._ir, self._deploy)

    async def startup(self, strict: bool = True):
        """Initialize HTTP client and connect outbound WebSocket channels."""
        if strict:
            errors = self.validate()
            if errors:
                raise ChannelConfigError(
                    f"Cannot start application — {len(errors)} channel(s) missing deploy config:\n"
                    + "\n".join(f"  - {e}" for e in errors)
                )

        self._http_client = httpx.AsyncClient(timeout=60.0)

        # Connect WebSocket channels
        for ch in self._ir.get("channels", []):
            display = ch["name"]["display"]
            config = self._channel_configs.get(display)
            if not config or config.protocol != "websocket":
                continue

            direction = ch.get("direction", "")
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
        elif auth.auth_type == "none":
            first_role = ""
            for r in self._ir.get("auth", {}).get("roles", []):
                first_role = r.get("name", "")
                break
            if first_role:
                headers["Cookie"] = f"termin_role={first_role}; termin_user_name=channel-dispatcher"
        return headers

    def _check_scope(self, channel_name: str, direction: str, user_scopes: set) -> bool:
        """Check if user has required scope for a channel operation."""
        spec = self.get_spec(channel_name)
        if not spec:
            return False
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
        """Send data through an outbound data channel."""
        spec = self.get_spec(channel_name)
        if not spec:
            raise ChannelError(f"Unknown channel: {channel_name}")

        display = spec["name"]["display"]

        if user_scopes is not None and not self._check_scope(channel_name, "send", user_scopes):
            raise ChannelScopeError(f"Insufficient scope to send on channel '{display}'")

        config = self.get_config(channel_name)
        if not config or not config.url:
            print(f"[Termin] Channel '{display}': no deploy config, send skipped")
            self._metrics[display]["sent"] += 1
            return {"ok": True, "status": "not_configured", "channel": display}

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
                    url, json=data, headers=headers,
                    timeout=config.timeout_ms / 1000.0,
                )
                self._metrics[display]["sent"] += 1
                self._metrics[display]["last_active"] = _now_iso()

                if response.status_code < 400:
                    return {"ok": True, "status": response.status_code, "channel": display}
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
        raise ChannelError(f"Channel '{display}' send failed after {config.max_retries + 1} attempts: {last_error}")

    # ── Invoke (action channels) ──

    async def channel_invoke(
        self, channel_name: str, action_name: str, params: dict, user_scopes: set = None
    ) -> dict:
        """Invoke a typed action on a channel."""
        spec = self.get_spec(channel_name)
        if not spec:
            raise ChannelError(f"Unknown channel: {channel_name}")

        display = spec["name"]["display"]
        action_spec = self.get_action_spec(channel_name, action_name)
        if not action_spec:
            raise ChannelError(f"Unknown action '{action_name}' on channel '{display}'")

        action_display = action_spec["name"]["display"]
        action_snake = action_spec["name"]["snake"]

        if user_scopes is not None and not self._check_action_scope(channel_name, action_name, user_scopes):
            raise ChannelScopeError(f"Insufficient scope to invoke '{action_display}' on channel '{display}'")

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
        headers = self._build_headers(config)
        url = f"{config.url.rstrip('/')}/actions/{action_snake}"
        last_error = None

        for attempt in range(config.max_retries + 1):
            try:
                response = await self._http_client.post(
                    url, json=params, headers=headers,
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
        """Check if a channel has deploy config."""
        config = self.get_config(channel_name)
        return config is not None and bool(config.url)

    def get_connection_state(self, channel_name: str) -> str:
        """Get live connection state for a channel."""
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
            return "connected"
        return "disconnected"

    def get_full_status(self) -> list[dict]:
        """Get full status of all channels for reflection."""
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



# ── Utilities ──

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
