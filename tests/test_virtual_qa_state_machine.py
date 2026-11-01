import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from src.services.virtual_qa_service import trigger_qa_violation

class TestVirtualQAStateMachine(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.call_info = {
            "call_uuid": "call-uuid-1234",
            "tenant_id": "tenant-uuid-1",
            "qa_config_id": "cfg-uuid-1",
            "qa_agent_ext": "1001",
            "qa_customer_ext": "1002",
            "qa_agent_leg": "bleg",
            "qa_supervisor_ext": "9001",
            "max_warn_retries": 2,
            "cooldown_seconds": 10
        }
        self.violation = {
            "violation": True,
            "type": "keyword",
            "keyword": "hell",
            "reason": "Banned keyword detected",
            "coaching": "Please refrain from using profanity."
        }

    @patch("src.services.virtual_qa_service.execute_query")
    @patch("src.services.virtual_qa_service.broadcast_qa_event")
    @patch("src.services.virtual_qa_service.synthesize_elevenlabs_tts")
    async def test_warning_under_max_retries_triggers_whisper(self, mock_tts, mock_broadcast, mock_db):
        mock_redis = AsyncMock()
        mock_esl = AsyncMock()
        mock_tts.return_value = "/tmp/warning_1.wav"

        # Cooldown passes
        mock_redis.set.return_value = True
        # First warning (1 <= 2)
        mock_redis.hincrby.return_value = 1

        res = await trigger_qa_violation(
            call_info=self.call_info,
            violation_data=self.violation,
            transcript="What the hell is going on?",
            redis_client=mock_redis,
            esl_client=mock_esl
        )

        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "warned")
        self.assertEqual(res["warn_count"], 1)

        # 1. Must dispatch whisper via ESL uuid_broadcast to Agent's leg (bleg)
        mock_esl.api.assert_called_once_with("uuid_broadcast call-uuid-1234 /tmp/warning_1.wav bleg")

        # 2. Must broadcast WebSocket qa.warning event
        mock_broadcast.assert_called_once()
        broadcast_payload = mock_broadcast.call_args[0][1]
        self.assertEqual(broadcast_payload["event_type"], "qa.warning")
        self.assertEqual(broadcast_payload["warn_count"], 1)

        # 3. Must insert audit record into qa_events
        mock_db.assert_called_once()
        db_sql = mock_db.call_args[0][0]
        self.assertIn("INSERT INTO qa_events", db_sql)

    @patch("src.services.virtual_qa_service.execute_query")
    @patch("src.services.virtual_qa_service.broadcast_qa_event")
    async def test_cooldown_suppresses_duplicate_violations(self, mock_broadcast, mock_db):
        mock_redis = AsyncMock()
        mock_esl = AsyncMock()

        # Cooldown fails (NX set returns False/None because key exists)
        mock_redis.set.return_value = None

        res = await trigger_qa_violation(
            call_info=self.call_info,
            violation_data=self.violation,
            transcript="What the hell",
            redis_client=mock_redis,
            esl_client=mock_esl
        )

        self.assertIsNone(res, "Violation must be suppressed during active cooldown")
        mock_esl.api.assert_not_called()
        mock_broadcast.assert_not_called()
        mock_db.assert_not_called()

    @patch("src.services.virtual_qa_service.execute_query")
    @patch("src.services.virtual_qa_service.broadcast_qa_event")
    @patch("src.services.virtual_qa_service.synthesize_elevenlabs_tts")
    async def test_escalation_when_exceeding_max_retries(self, mock_tts, mock_broadcast, mock_db):
        mock_redis = AsyncMock()
        mock_esl = AsyncMock()

        # Cooldown passes
        mock_redis.set.return_value = True
        # Warning count is 3 (exceeds max_warn_retries = 2)
        mock_redis.hincrby.return_value = 3
        # First escalation flag succeeds
        mock_redis.hsetnx.return_value = 1

        res = await trigger_qa_violation(
            call_info=self.call_info,
            violation_data=self.violation,
            transcript="Damn and hell again",
            redis_client=mock_redis,
            esl_client=mock_esl
        )

        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "escalated")
        self.assertEqual(res["warn_count"], 3)

        # 1. Must NOT play whisper to agent once escalated
        mock_esl.api.assert_not_called()
        mock_tts.assert_not_called()

        # 2. Must broadcast qa.escalation event
        mock_broadcast.assert_called_once()
        broadcast_payload = mock_broadcast.call_args[0][1]
        self.assertEqual(broadcast_payload["event_type"], "qa.escalation")
        self.assertEqual(broadcast_payload["warn_count"], 3)
        self.assertEqual(broadcast_payload["agent_extension"], "1001")

        # 3. Must record escalation event in qa_events
        mock_db.assert_called_once()
        db_sql = mock_db.call_args[0][0]
        self.assertIn("INSERT INTO qa_events", db_sql)

if __name__ == "__main__":
    unittest.main()
