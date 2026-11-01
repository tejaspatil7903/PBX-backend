import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from src.main import app
from src.core.permissions import get_current_user, CurrentUser
from src.xml_curl.directory import handle_directory_request
from src.xml_curl.dialplan import handle_dialplan_request
from src.services.virtual_qa_service import trigger_qa_violation, qa_service

class TestVirtualQAE2E(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.mock_user = CurrentUser(
            user_id="user-supervisor-uuid",
            tenant_id="tenant-uuid-1",
            username="sup_user",
            email="sup@acme.com",
            role="SUPERVISOR"
        )
        app.dependency_overrides[get_current_user] = lambda: self.mock_user

        self.call_uuid = "e2e-call-uuid-999"
        self.tenant_id = "tenant-uuid-1"
        self.agent_ext = "1001"
        self.customer_ext = "1002"
        self.qa_ext = "8999"

    def tearDown(self):
        app.dependency_overrides.clear()

    @patch("src.xml_curl.directory.execute_query_one")
    @patch("src.xml_curl.directory.execute_query")
    async def test_step1_directory_virtual_extension_provisioning(self, mock_query, mock_query_one):
        """Verifies virtual QA extension returns loopback/park and hides SIP auth password."""
        mock_query_one.return_value = {"id": self.tenant_id, "sip_domain": "acme.pbx.com", "domain": "acme.pbx.com"}
        mock_query.return_value = [{
            "extension_number": self.qa_ext,
            "display_name": "Virtual QA Bot",
            "email": "qa@acme.com",
            "sip_password": "secretpassword",
            "enabled": True,
            "is_virtual": True
        }]

        res_xml = await handle_directory_request({"domain": "acme.pbx.com", "user": self.qa_ext})
        self.assertIn("loopback/park", res_xml)
        self.assertNotIn("secretpassword", res_xml)

    @patch("src.xml_curl.dialplan.execute_query_one")
    async def test_step2_dialplan_variable_injection(self, mock_query_one):
        """Verifies dialplan injects unbuffered stereo recording and QA channel variables."""
        def q_side_effect(sql, params=None):
            if "FROM extensions" in sql:
                return {
                    "id": "ext-1001-uuid",
                    "extension_number": "1001",
                    "display_name": "Agent Alice",
                    "tenant_id": self.tenant_id,
                    "sip_domain": "acme.pbx.com"
                }
            elif "FROM tenants" in sql:
                return {"allow_internal_calls": True}
            elif "FROM virtual_qa_configs" in sql:
                return {
                    "id": "cfg-qa-1",
                    "qa_extension_id": "ext-8999-uuid",
                    "max_warn_retries": 2,
                    "cooldown_seconds": 5,
                    "supervisor_extension": "9001",
                    "monitored_agents": ["1001"],
                    "monitored_callers": ["*"]
                }
            return None

        mock_query_one.side_effect = q_side_effect

        form = {
            "Caller-Context": "default",
            "Caller-Destination-Number": "1001",
            "Caller-Caller-ID-Number": "1002",
            "variable_tenant_id": self.tenant_id,
            "domain": "acme.pbx.com"
        }
        res_xml = await handle_dialplan_request(form)
        self.assertIn("RECORD_STEREO=true", res_xml)
        self.assertIn("enable_file_write_buffering=false", res_xml)
        self.assertIn("qa_enabled=true", res_xml)
        self.assertIn("qa_agent_leg=bleg", res_xml)

    @patch("src.services.virtual_qa_service.execute_query")
    @patch("src.services.virtual_qa_service.broadcast_qa_event")
    @patch("src.services.virtual_qa_service.synthesize_elevenlabs_tts")
    async def test_step3_whisper_and_escalation_lifecycle(self, mock_tts, mock_broadcast, mock_db):
        """
        Tests the complete violation lifecycle:
        1. First violation -> Warning 1 (whisper dispatched via ESL uuid_broadcast to bleg)
        2. Cooldown lock -> duplicate violation within cooldown is suppressed
        3. Second violation -> Warning 2 (whisper dispatched)
        4. Third violation (> max_warn_retries 2) -> Escalation emitted, whisper suppressed!
        """
        mock_redis = AsyncMock()
        mock_esl = AsyncMock()
        mock_tts.return_value = "/tmp/warn_audio.wav"

        call_info = {
            "call_uuid": self.call_uuid,
            "tenant_id": self.tenant_id,
            "qa_config_id": "cfg-qa-1",
            "qa_agent_ext": "1001",
            "qa_customer_ext": "1002",
            "qa_agent_leg": "bleg",
            "qa_supervisor_ext": "9001",
            "max_warn_retries": 2,
            "cooldown_seconds": 5
        }
        violation = {
            "violation": True,
            "type": "keyword",
            "keyword": "shut up",
            "reason": "Restricted phrase detected",
            "coaching": "Remain calm and polite with the caller."
        }

        # --- Violation 1: Warning 1 ---
        mock_redis.set.return_value = True # Cooldown passes
        mock_redis.hincrby.return_value = 1 # warn_count = 1
        res1 = await trigger_qa_violation(call_info, violation, "Shut up now!", redis_client=mock_redis, esl_client=mock_esl)
        self.assertEqual(res1["status"], "warned")
        self.assertEqual(res1["warn_count"], 1)
        mock_esl.api.assert_called_with(f"uuid_broadcast {self.call_uuid} /tmp/warn_audio.wav bleg")
        mock_broadcast.assert_called()
        self.assertEqual(mock_broadcast.call_args[0][1]["event_type"], "qa.warning")

        # --- Cooldown Suppression ---
        mock_redis.set.return_value = None # Active cooldown
        res_suppressed = await trigger_qa_violation(call_info, violation, "Shut up again", redis_client=mock_redis, esl_client=mock_esl)
        self.assertIsNone(res_suppressed)

        # --- Violation 2: Warning 2 ---
        mock_redis.set.return_value = True # Cooldown expired
        mock_redis.hincrby.return_value = 2 # warn_count = 2
        res2 = await trigger_qa_violation(call_info, violation, "You shut up", redis_client=mock_redis, esl_client=mock_esl)
        self.assertEqual(res2["status"], "warned")
        self.assertEqual(res2["warn_count"], 2)

        # --- Violation 3: Escalation (> max_retries 2) ---
        mock_esl.reset_mock()
        mock_broadcast.reset_mock()
        mock_redis.set.return_value = True
        mock_redis.hincrby.return_value = 3 # warn_count = 3 (> 2)
        mock_redis.hsetnx.return_value = 1 # Atomic first escalation

        res3 = await trigger_qa_violation(call_info, violation, "Another violation", redis_client=mock_redis, esl_client=mock_esl)
        self.assertEqual(res3["status"], "escalated")
        self.assertEqual(res3["warn_count"], 3)

        # Whisper to agent must be suppressed on escalation
        mock_esl.api.assert_not_called()
        # WebSocket must emit qa.escalation
        mock_broadcast.assert_called_once()
        self.assertEqual(mock_broadcast.call_args[0][1]["event_type"], "qa.escalation")

    @patch("src.api.v1.qa.execute_query_one")
    @patch("src.api.v1.qa.qa_service.api", new_callable=AsyncMock)
    @patch("src.api.v1.qa.get_redis")
    async def test_step4_supervisor_live_intervention(self, mock_get_redis, mock_esl_api, mock_query_one):
        """Verifies supervisor can initiate live whisper intervention on active escalated call."""
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_redis.hgetall.return_value = {
            "call_uuid": self.call_uuid,
            "tenant_id": self.tenant_id,
            "agent_ext": "1001",
            "agent_leg": "bleg",
            "escalated": "1"
        }
        mock_query_one.return_value = {"sip_domain": "acme.pbx.com"}

        resp = self.client.post(f"/api/v1/qa/calls/{self.call_uuid}/intervene?mode=whisper")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "intervention_initiated")
        mock_esl_api.assert_called_once()
        cmd = mock_esl_api.call_args[0][0]
        self.assertIn("originate", cmd)
        self.assertIn("eavesdrop", cmd)
        self.assertIn(self.call_uuid, cmd)

    @patch("src.services.virtual_qa_service.get_redis")
    async def test_step5_call_hangup_cleanup(self, mock_get_redis):
        """Verifies CHANNEL_HANGUP cleans up all Redis keys and audio tailing tasks."""
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis

        hangup_headers = {
            "Event-Name": "CHANNEL_HANGUP",
            "Unique-ID": self.call_uuid,
            "variable_tenant_id": self.tenant_id,
            "variable_qa_agent_ext": "1001"
        }
        await qa_service._handle_channel_hangup(hangup_headers)

        # Assert Redis keys deleted
        delete_calls = [c[0][0] for c in mock_redis.delete.call_args_list]
        self.assertIn(f"tenant:{self.tenant_id}:qa_call:{self.call_uuid}", delete_calls)
        self.assertIn(f"tenant:{self.tenant_id}:qa_agent_call:1001", delete_calls)
        self.assertIn(f"tenant:{self.tenant_id}:qa_cd:{self.call_uuid}", delete_calls)

if __name__ == "__main__":
    unittest.main()
