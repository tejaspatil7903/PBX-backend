import json
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from src.core.websocket_manager import ConnectionManager

class TestQAWebSocketPubSub(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_qa_event_payload_and_role_filtering(self):
        manager = ConnectionManager()
        mock_ws_admin = AsyncMock()
        mock_ws_supervisor = AsyncMock()
        mock_ws_agent = AsyncMock()

        # Connect admin, supervisor, and agent for tenant-101
        await manager.connect(mock_ws_admin, tenant_id="tenant-101", is_admin=True, role="super_admin")
        await manager.connect(mock_ws_supervisor, tenant_id="tenant-101", is_admin=False, role="supervisor")
        await manager.connect(mock_ws_agent, tenant_id="tenant-101", is_admin=False, role="agent")

        qa_payload = {
            "event": "qa.warning",
            "call_uuid": "call-abc-123",
            "agent_extension": "1001",
            "customer_extension": "1002",
            "warn_count": 1,
            "max_retries": 3,
            "reason": "Hostile tone detected"
        }

        # Broadcast QA event for tenant-101
        await manager.broadcast_qa_event(
            tenant_id="tenant-101",
            payload=qa_payload,
            roles=("supervisor", "admin", "super_admin", "tenant_admin")
        )

        # Admin and Supervisor must receive the event
        mock_ws_admin.send_json.assert_called_once()
        sent_admin = mock_ws_admin.send_json.call_args[0][0]
        self.assertEqual(sent_admin["event"], "qa.warning")
        self.assertEqual(sent_admin["version"], 1)
        self.assertEqual(sent_admin["call_uuid"], "call-abc-123")
        self.assertTrue("timestamp" in sent_admin)

        mock_ws_supervisor.send_json.assert_called_once()
        sent_sup = mock_ws_supervisor.send_json.call_args[0][0]
        self.assertEqual(sent_sup["call_uuid"], "call-abc-123")

        # Standard agent must NOT receive the QA event
        mock_ws_agent.send_json.assert_not_called()

    @patch("src.core.websocket_manager.get_redis")
    async def test_broadcast_qa_event_publishes_to_redis(self, mock_get_redis):
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis

        manager = ConnectionManager()
        qa_payload = {
            "event": "qa.escalation",
            "call_uuid": "call-esc-999",
            "agent_extension": "1001",
            "supervisor_extension": "9001"
        }

        await manager.broadcast_qa_event(tenant_id="tenant-202", payload=qa_payload)

        # Verify Redis publish was called with channel qa_events:tenant-202
        mock_redis.publish.assert_called_once()
        channel, published_raw = mock_redis.publish.call_args[0]
        self.assertEqual(channel, "qa_events:tenant-202")
        data = json.loads(published_raw)
        self.assertEqual(data["event"], "qa.escalation")
        self.assertEqual(data["version"], 1)

if __name__ == "__main__":
    unittest.main()
