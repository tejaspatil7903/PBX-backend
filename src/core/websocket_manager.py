import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from src.core.redis import get_redis

logger = logging.getLogger("pbx.websocket.manager")

class ConnectionManager:
    def __init__(self):
        # Maps WebSocket -> dict(tenant_id=..., is_admin=..., role=...)
        self.active_connections: Dict[Any, Dict[str, Any]] = {}
        self._listener_task: Optional[asyncio.Task] = None

    async def connect(
        self,
        websocket: Any,
        tenant_id: Optional[str] = None,
        is_admin: bool = False,
        role: Optional[str] = None
    ):
        try:
            if hasattr(websocket, "accept"):
                await websocket.accept()
        except Exception as e:
            logger.debug(f"Socket accept: {e}")

        normalized_role = str(role or ("super_admin" if is_admin else "agent")).lower()
        self.active_connections[websocket] = {
            "tenant_id": str(tenant_id) if tenant_id else None,
            "is_admin": is_admin,
            "role": normalized_role
        }
        logger.info(
            f"WebSocket client connected. Active: {len(self.active_connections)} "
            f"(admin={is_admin}, tenant={tenant_id}, role={normalized_role})"
        )

    async def disconnect(self, websocket: Any):
        if websocket in self.active_connections:
            del self.active_connections[websocket]
            logger.info(f"WebSocket client disconnected. Active: {len(self.active_connections)}")

    async def broadcast(self, event: dict, tenant_id: Optional[str] = None):
        """
        Broadcasts event to clients.
        If tenant_id is specified: sends to super-admins and clients belonging to tenant_id.
        """
        stale = []
        for ws, meta in list(self.active_connections.items()):
            try:
                if meta["is_admin"] or not tenant_id or meta["tenant_id"] == str(tenant_id):
                    await asyncio.wait_for(ws.send_json(event), timeout=2.0)
            except Exception as e:
                logger.warning(f"Error sending event to websocket: {e}")
                stale.append(ws)

        for ws in stale:
            await self.disconnect(ws)

    async def deliver_qa_event_locally(
        self,
        tenant_id: str,
        event: dict,
        roles: Tuple[str, ...] = ("supervisor", "admin", "super_admin", "tenant_admin")
    ):
        """
        Delivers QA event strictly to local sockets in this process matching tenant and role.
        """
        stale = []
        target_roles = tuple(r.lower() for r in roles)
        for ws, meta in list(self.active_connections.items()):
            try:
                user_role = meta.get("role", "agent").lower()
                user_tid = meta.get("tenant_id")
                is_admin = meta.get("is_admin", False)

                # Scope check: admin or matching tenant
                tenant_match = is_admin or not tenant_id or (user_tid == str(tenant_id))
                # Role check: must have one of the permitted supervisor/admin roles
                role_match = is_admin or any(allowed in user_role for allowed in target_roles)

                if tenant_match and role_match:
                    await asyncio.wait_for(ws.send_json(event), timeout=2.0)
            except Exception as e:
                logger.warning(f"Error delivering QA event to local socket: {e}")
                stale.append(ws)

        for ws in stale:
            await self.disconnect(ws)

    async def broadcast_qa_event(
        self,
        tenant_id: str,
        payload: dict,
        roles: Tuple[str, ...] = ("supervisor", "admin", "super_admin", "tenant_admin")
    ):
        """
        Multi-worker safe QA event broadcaster:
        1. Formats canonical payload with version & timestamp.
        2. Publishes to Redis pub/sub channel `qa_events:{tenant_id}`.
        3. Delivers to local process sockets immediately.
        """
        event = dict(payload)
        event["version"] = 1
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()

        # 1. Publish to Redis Pub/Sub for other API worker processes
        try:
            r = get_redis()
            if r:
                channel = f"qa_events:{tenant_id}"
                await r.publish(channel, json.dumps(event))
        except Exception as e:
            logger.debug(f"Redis pub/sub publish note: {e}")

        # 2. Deliver directly to local sockets connected to this process
        await self.deliver_qa_event_locally(tenant_id, event, roles)

    async def start_redis_listener(self):
        """
        Background listener task subscribing to Redis channel `qa_events:*`.
        Forwards incoming pub/sub events to local process sockets.
        """
        while True:
            try:
                r = get_redis()
                if not r:
                    await asyncio.sleep(2.0)
                    continue

                pubsub = r.pubsub()
                await pubsub.psubscribe("qa_events:*")
                logger.info("Subscribed to Redis pattern 'qa_events:*' for cross-worker event delivery.")

                async for message in pubsub.listen():
                    if not message or message.get("type") != "pmessage":
                        continue
                    channel = message.get("channel", "")
                    data_str = message.get("data", "")
                    try:
                        event_data = json.loads(data_str) if isinstance(data_str, str) else json.loads(data_str.decode())
                        tenant_id = channel.replace("qa_events:", "")
                        # Deliver to local sockets without re-publishing
                        await self.deliver_qa_event_locally(tenant_id, event_data)
                    except Exception as parse_err:
                        logger.warning(f"Failed to process Redis QA event payload: {parse_err}")

            except asyncio.CancelledError:
                logger.info("Redis QA event listener cancelled.")
                break
            except Exception as loop_err:
                logger.warning(f"Redis QA event listener error: {loop_err}. Reconnecting in 3s...")
                await asyncio.sleep(3.0)

    async def broadcast_cdr_created(self, cdr_data: dict):
        event = {
            "event": "cdr.created",
            "type": "cdr.created",
            "data": cdr_data
        }
        target_tenant = cdr_data.get("tenant_id")
        await self.broadcast(event, tenant_id=target_tenant)

    async def broadcast_voicemail_created(self, vm_data: dict):
        event = {
            "event": "voicemail.created",
            "type": "voicemail.created",
            "data": vm_data
        }
        target_tenant = vm_data.get("tenant_id")
        await self.broadcast(event, tenant_id=target_tenant)

ws_manager = ConnectionManager()


async def broadcast_qa_event(
    tenant_id: str,
    payload: dict,
    roles: Tuple[str, ...] = ("supervisor", "admin", "super_admin", "tenant_admin")
):
    """Module-level convenience wrapper for ws_manager.broadcast_qa_event."""
    await ws_manager.broadcast_qa_event(tenant_id, payload, roles=roles)
