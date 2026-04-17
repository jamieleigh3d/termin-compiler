"""WebSocket Connection Manager — subscription-based multiplexer.

Manages WebSocket connections, channel subscriptions, and broadcasting
events to subscribers.
"""

import uuid

from fastapi import WebSocket, WebSocketDisconnect

from .context import RuntimeContext
from .storage import get_db, _q


class ConnectionManager:
    """Manages WebSocket connections and channel subscriptions."""

    def __init__(self):
        self.active: dict[str, dict] = {}  # conn_id -> {ws, user, subscriptions}

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

    async def broadcast_to_subscribers(self, channel_id: str, event: dict):
        dead = []
        for conn_id, conn in self.active.items():
            for pattern in conn["subscriptions"]:
                if channel_id.startswith(pattern):
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
                                cursor = await db.execute(f"SELECT * FROM {_q(content_name)}")
                                rows = [dict(r) for r in await cursor.fetchall()]
                            finally:
                                await db.close()
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
                                cursor = await db.execute(f"SELECT * FROM {_q(content_name)}")
                                rows = [dict(r) for r in await cursor.fetchall()]
                            finally:
                                await db.close()
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
