# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""FastAPI bridge for the framework-agnostic WebSocket dispatcher.

Slice 7.2.f of Phase 7 (2026-04-30) extracted ``ConnectionManager``,
the ownership-cascade gate, and the per-frame multiplexer loop into
``termin_core.routing.{connection_manager,channel_dispatch}``.

What stays here:

* :class:`ConnectionManager` — re-exported from core for back-compat
  (drops in slice 7.5 once nothing imports from
  ``termin_runtime.websocket_manager`` directly).
* :func:`register_websocket_routes` — the FastAPI route shell. It
  authenticates the incoming connection, accepts the socket, wraps
  the ``fastapi.WebSocket`` as a :class:`TerminWebSocket` via
  :class:`FastAPIWebSocketAdapter`, then hands control to
  :func:`dispatch_websocket_session`. On disconnect the route
  shell cleans up the registry entry the dispatcher's ``connect``
  call created.
"""

from __future__ import annotations

from fastapi import WebSocket, WebSocketDisconnect

from termin_core.routing import (  # noqa: F401  (back-compat re-exports)
    ConnectionManager,
    dispatch_websocket_session,
    filter_owned_rows as _filter_owned_rows,
)

from .context import RuntimeContext
from .fastapi_adapter import FastAPIWebSocketAdapter


def register_websocket_routes(app, ctx: RuntimeContext):
    """Register the WebSocket multiplexer endpoint on ``app``.

    The shell is intentionally tiny — accept, wrap, dispatch, clean
    up. All decisions about frame shape, ownership cascade, and
    initial-data load live in ``termin-core``.
    """

    @app.websocket("/runtime/ws")
    async def runtime_ws(websocket: WebSocket):
        user = ctx.get_user_from_ws(websocket)
        await websocket.accept()
        adapter = FastAPIWebSocketAdapter(websocket)
        # The user dict carries scopes/role today; principal will move
        # off the dict in slice 7.5 when AuthContext flows through the
        # WS path the same way it does for HTTP.
        adapter.principal = None
        # ``connect`` registers the conn before the dispatcher's loop
        # starts, so any in-flight broadcast can find it. We replicate
        # that order here and let the dispatcher reuse the same conn
        # registration via its ``connect`` call — the registry is
        # idempotent per-conn-id.
        try:
            await dispatch_websocket_session(adapter, ctx, user)
        except WebSocketDisconnect:
            pass
        except Exception:
            # Same conservative cleanup the v0.9 implementation had:
            # any error tears down the conn so a client retry gets
            # fresh state.
            pass
        finally:
            # The dispatcher's connect minted a conn_id we don't have
            # a handle to from out here. Walk the registry and drop
            # any entry whose ws is this adapter — single connection
            # per request, so this is exact.
            for cid, entry in list(ctx.conn_manager.active.items()):
                if entry.get("ws") is adapter:
                    ctx.conn_manager.disconnect(cid)
                    break


__all__ = [
    "ConnectionManager",
    "register_websocket_routes",
]
