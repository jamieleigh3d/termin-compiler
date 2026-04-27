# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""WebSocket Connection Manager — subscription-based multiplexer.

Manages WebSocket connections, channel subscriptions, and broadcasting
events to subscribers.

v0.9 Phase 6a.6 (BRD #3 §3.6): the manager carries an
ownership-field lookup so that `content.<X>.{created,updated,deleted}`
broadcasts on owned content types only fan out to subscribers whose
principal id equals the record's owning-field value. Channels (per
BRD #1 §6.4) that carry owned content cascade their subscriber
filtering through the same mechanism — a subscription is intrinsic
to the carried content's ownership, no extra source-level construct.
"""

import uuid
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from .context import RuntimeContext
from .storage import get_db, list_records


# ── Ownership-cascade helpers ──

def _principal_id_of(user: dict) -> str:
    """Return the principal id for a runtime user dict, or '' for
    anonymous / system-with-empty-id callers. Mirrors the shape
    `identity._build_user_dict` produces."""
    the_user = user.get("the_user") if isinstance(user, dict) else None
    if isinstance(the_user, dict):
        if the_user.get("is_anonymous"):
            return ""
        return str(the_user.get("id") or "")
    return ""


def _filter_owned_rows(rows: list[dict], ownership_field: Optional[str],
                       user: dict) -> list[dict]:
    """v0.9 Phase 6a.6: pre-filter list_records output by ownership.

    When the content is owned (ownership_field is the snake-case
    column carrying the owning principal id), drop rows the user
    doesn't own. Anonymous principals see nothing on owned content.
    Pass-through for non-owned content (ownership_field is None).
    """
    if not ownership_field:
        return rows
    pid = _principal_id_of(user)
    if not pid:
        return []
    return [r for r in rows if r.get(ownership_field) == pid]


def _content_name_from_channel_id(channel_id: str) -> Optional[str]:
    """Extract the content type name from a channel id of shape
    `content.<X>.<verb>` (or `content.<X>` as a prefix). Returns
    None for non-content channels (state-machine transitions,
    ad-hoc events) so the cascade leaves them alone."""
    if not channel_id.startswith("content."):
        return None
    parts = channel_id.split(".")
    if len(parts) < 2:
        return None
    return parts[1]


class ConnectionManager:
    """Manages WebSocket connections and channel subscriptions."""

    def __init__(self):
        self.active: dict[str, dict] = {}  # conn_id -> {ws, user, subscriptions}
        # v0.9 Phase 6a.6: snake-case content name -> ownership column.
        # Empty by default; populated by app startup from IR.
        self._content_ownership: dict[str, str] = {}

    def set_content_ownership(self, mapping: dict[str, str]) -> None:
        """Register the per-content ownership-field lookup. Keyed by
        snake-case content name, value is the snake-case column that
        carries the owning principal id. Content not in the mapping
        has no ownership cascade applied."""
        self._content_ownership = dict(mapping or {})

    async def connect(self, ws: WebSocket, user: dict) -> str:
        conn_id = str(uuid.uuid4())[:8]
        self.active[conn_id] = {"ws": ws, "user": user, "subscriptions": set()}
        return conn_id

    def disconnect(self, conn_id: str):
        self.active.pop(conn_id, None)

    def add_subscription(self, conn_id: str, channel_id: str):
        if conn_id in self.active:
            self.active[conn_id]["subscriptions"].add(channel_id)

    def remove_subscription(self, conn_id: str, channel_id: str):
        if conn_id in self.active:
            self.active[conn_id]["subscriptions"].discard(channel_id)

    def _should_deliver_to(self, conn: dict, channel_id: str,
                           event: dict) -> bool:
        """v0.9 Phase 6a.6: ownership cascade gate.

        Returns True when the connection should receive this
        broadcast. False for connections whose principal does not
        own the record carried by an owned-content event.
        Non-owned content and non-content channels always pass.
        """
        content_name = _content_name_from_channel_id(channel_id)
        if not content_name:
            return True  # state-machine events, custom events: not gated
        owner_field = self._content_ownership.get(content_name)
        if not owner_field:
            return True  # content type has no ownership declared
        # Owned content: payload must carry the field, and the
        # subscriber's principal id must match. Missing field is a
        # conservative drop (no over-share on malformed payloads).
        payload = event.get("data") or event.get("record") or {}
        if not isinstance(payload, dict):
            return False
        owner_value = payload.get(owner_field)
        if owner_value is None:
            return False
        return owner_value == _principal_id_of(conn["user"])

    async def broadcast_to_subscribers(self, channel_id: str, event: dict):
        dead = []
        for conn_id, conn in self.active.items():
            for pattern in conn["subscriptions"]:
                if channel_id.startswith(pattern):
                    if not self._should_deliver_to(conn, channel_id, event):
                        break
                    try:
                        await conn["ws"].send_json({
                            "v": 1,
                            "ch": channel_id,
                            "op": "push",
                            "ref": None,
                            "payload": event.get("data") or event.get("record") or event,
                        })
                    except Exception:
                        dead.append(conn_id)
                    break
        for conn_id in dead:
            self.disconnect(conn_id)


def register_websocket_routes(app, ctx: RuntimeContext):
    """Register the WebSocket multiplexer endpoint on the app."""

    @app.websocket("/runtime/ws")
    async def runtime_ws(websocket: WebSocket):
        user = ctx.get_user_from_ws(websocket)
        await websocket.accept()
        conn_id = await ctx.conn_manager.connect(websocket, user)

        # Send identity context as first frame
        await websocket.send_json({
            "v": 1, "ch": "runtime.identity", "op": "push", "ref": None,
            "payload": {"role": user["role"], "scopes": user["scopes"], "profile": user["profile"]},
        })

        try:
            while True:
                frame = await websocket.receive_json()
                op = frame.get("op", "")
                ch = frame.get("ch", "")
                ref = frame.get("ref")

                if op == "subscribe":
                    ctx.conn_manager.add_subscription(conn_id, ch)
                    # Send current data
                    parts = ch.split(".")
                    if len(parts) >= 2 and parts[0] == "content":
                        content_name = parts[1]
                        try:
                            db = await get_db(ctx.db_path)
                            try:
                                rows = await list_records(db, content_name)
                            finally:
                                await db.close()
                            # v0.9 Phase 6a.6: cascade ownership
                            # filter onto initial-data load too.
                            owner_field = ctx.conn_manager._content_ownership.get(
                                content_name)
                            rows = _filter_owned_rows(rows, owner_field, user)
                            await websocket.send_json({
                                "v": 1, "ch": ch, "op": "response", "ref": ref,
                                "payload": {"current": rows},
                            })
                        except Exception as e:
                            await websocket.send_json({
                                "v": 1, "ch": ch, "op": "error", "ref": ref,
                                "payload": {"message": str(e)},
                            })
                    else:
                        await websocket.send_json({
                            "v": 1, "ch": ch, "op": "response", "ref": ref,
                            "payload": {"current": []},
                        })

                elif op == "unsubscribe":
                    ctx.conn_manager.remove_subscription(conn_id, ch)
                    await websocket.send_json({
                        "v": 1, "ch": ch, "op": "response", "ref": ref,
                        "payload": {"unsubscribed": True},
                    })

                elif op == "request":
                    parts = ch.split(".")
                    if len(parts) >= 2 and parts[0] == "content":
                        content_name = parts[1]
                        try:
                            db = await get_db(ctx.db_path)
                            try:
                                rows = await list_records(db, content_name)
                            finally:
                                await db.close()
                            # v0.9 Phase 6a.6: same cascade applies.
                            owner_field = ctx.conn_manager._content_ownership.get(
                                content_name)
                            rows = _filter_owned_rows(rows, owner_field, user)
                            await websocket.send_json({
                                "v": 1, "ch": ch, "op": "response", "ref": ref,
                                "payload": {"data": rows},
                            })
                        except Exception as e:
                            await websocket.send_json({
                                "v": 1, "ch": ch, "op": "error", "ref": ref,
                                "payload": {"message": str(e)},
                            })

        except WebSocketDisconnect:
            ctx.conn_manager.disconnect(conn_id)
        except Exception:
            ctx.conn_manager.disconnect(conn_id)
