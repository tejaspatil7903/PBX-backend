import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from src.main import app
from src.core.permissions import get_current_user, CurrentUser

class TestQAAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.mock_user = CurrentUser(
            user_id="user-supervisor-uuid",
            tenant_id="00000000-0000-0000-0000-000000000001",
            username="sup_user",
            email="sup@acme.com",
            role="TENANT_ADMIN"
        )
        app.dependency_overrides[get_current_user] = lambda: self.mock_user

    def tearDown(self):
        app.dependency_overrides.clear()

    @patch("src.api.v1.qa.execute_query_one")
    @patch("src.api.v1.qa.execute_query")
    def test_qa_config_create_and_virtual_extension_provision(self, mock_query, mock_query_one):
        # 1. Mock extension lookup for 8999
        mock_query_one.side_effect = [
            # Check if extension 8999 exists
            {
                "id": "ext-8999-uuid",
                "extension_number": "8999",
                "tenant_id": "tenant-uuid-1"
            },
            # Insert return
            {
                "id": "cfg-new-uuid",
                "tenant_id": "tenant-uuid-1",
                "qa_extension_id": "ext-8999-uuid",
                "max_warn_retries": 3,
                "cooldown_seconds": 10,
                "supervisor_extension": "9001",
                "monitored_agents": ["1001"],
                "monitored_callers": ["*"],
                "enabled": True
            }
        ]

        payload = {
            "tenant_id": "tenant-uuid-1",
            "qa_extension_number": "8999",
            "max_warn_retries": 3,
            "cooldown_seconds": 10,
            "supervisor_extension": "9001",
            "monitored_agents": ["1001"],
            "monitored_callers": ["*"],
            "policy_prompt": "Speak professionally.",
            "banned_keywords": ["hell", "damn"]
        }

        resp = self.client.post("/api/v1/qa/configs", json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["id"], "cfg-new-uuid")

        # Verify extension was marked is_virtual = true and assigned VIRTUAL_QA role
        update_calls = [call for call in mock_query.call_args_list if "UPDATE extensions" in call[0][0]]
        self.assertTrue(len(update_calls) > 0, "Must update extension to set is_virtual = true")

    @patch("src.api.v1.qa.get_redis")
    def test_qa_active_calls_hydration(self, mock_get_redis):
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_redis.keys.return_value = ["tenant:tenant-uuid-1:qa_call:call-active-1"]
        mock_redis.hgetall.return_value = {
            "call_uuid": "call-active-1",
            "tenant_id": "tenant-uuid-1",
            "agent_ext": "1001",
            "customer_ext": "1002",
            "agent_leg": "bleg",
            "warn_count": "1",
            "escalated": "0",
            "started_at": "1730000000.0"
        }

        resp = self.client.get("/api/v1/qa/active-calls")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["call_uuid"], "call-active-1")
        self.assertEqual(data[0]["warn_count"], 1)

    @patch("src.api.v1.qa.execute_query_one")
    @patch("src.api.v1.qa.qa_service.api", new_callable=AsyncMock)
    @patch("src.api.v1.qa.get_redis")
    def test_supervisor_intervene_originate(self, mock_get_redis, mock_esl_api, mock_query_one):
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_redis.hgetall.return_value = {
            "call_uuid": "call-active-1",
            "tenant_id": "tenant-uuid-1",
            "agent_ext": "1001",
            "agent_leg": "bleg"
        }
        mock_query_one.return_value = {
            "sip_domain": "acme.pbx.com"
        }

        resp = self.client.post("/api/v1/qa/calls/call-active-1/intervene?mode=whisper")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "intervention_initiated")

        # ESL API must have been called with originate eavesdrop
        mock_esl_api.assert_called_once()
        esl_cmd = mock_esl_api.call_args[0][0]
        self.assertIn("originate", esl_cmd)
        self.assertIn("eavesdrop", esl_cmd)
        self.assertIn("call-active-1", esl_cmd)

    @patch("src.api.v1.qa.settings")
    def test_simulate_violation_demo_mode_gate(self, mock_settings):
        # When QA_DEMO_MODE is False -> 404
        mock_settings.QA_DEMO_MODE = False
        resp = self.client.post("/api/v1/qa/calls/call-active-1/simulate-violation")
        self.assertEqual(resp.status_code, 404)

    @patch("src.api.v1.qa.execute_query_one")
    def test_get_webrtc_credentials(self, mock_query_one):
        mock_query_one.side_effect = [
            # 1. Tenant lookup
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "name": "Acme Corp",
                "sip_domain": "pbx.acme.local"
            },
            # 2. Extension lookup by email
            {
                "extension_number": "1004",
                "sip_password": "supersecretpassword",
                "display_name": "Supervisor"
            }
        ]

        resp = self.client.get("/api/v1/qa/webrtc-credentials")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["extension"], "1004")
        self.assertEqual(data["password"], "supersecretpassword")
        self.assertEqual(data["sip_domain"], "pbx.acme.local")
        self.assertIn("ws://", data["ws_server"])
        self.assertTrue(len(data["ice_servers"]) > 0)

if __name__ == "__main__":
    unittest.main()
