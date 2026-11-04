import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch, AsyncMock
from src.xml_curl.directory import handle_directory_request
from src.xml_curl.dialplan import handle_dialplan_request

class TestQADialplanAndDirectory(unittest.IsolatedAsyncioTestCase):
    @patch("src.xml_curl.directory.execute_query_one")
    @patch("src.xml_curl.directory.execute_query")
    async def test_virtual_extension_directory_rejection_and_park(self, mock_exec_query, mock_exec_one):
        # Tenant mock
        mock_exec_one.return_value = {
            "id": "tenant-uuid-1",
            "name": "Acme Corp",
            "domain": "acme.pbx.com",
            "sip_domain": "acme.pbx.com"
        }
        # Extension is virtual
        mock_exec_query.return_value = [{
            "extension_number": "8999",
            "display_name": "Virtual QA Bot",
            "email": "qa@acme.com",
            "sip_password": "supersecretpassword",
            "voicemail_pin": "1234",
            "caller_id_name": "Virtual QA",
            "caller_id_number": "8999",
            "outbound_caller_id": "8999",
            "enabled": True,
            "is_virtual": True
        }]

        form_data = {
            "domain": "acme.pbx.com",
            "user": "8999"
        }
        xml_res = await handle_directory_request(form_data)
        root = ET.fromstring(xml_res)

        # 1. Must NOT include SIP password for registration
        passwords = [param.attrib.get("value") for param in root.findall(".//param[@name='password']")]
        self.assertEqual(len(passwords), 0, "Virtual extension must not expose SIP authentication password")

        # 2. Must return loopback/park dial-string for the user
        dial_string = root.find(".//param[@name='dial-string']").attrib.get("value")
        self.assertIn("loopback/park", dial_string)

    @patch("src.xml_curl.dialplan.execute_query_one")
    @patch("src.xml_curl.dialplan.execute_query")
    async def test_monitored_call_dialplan_variable_injection(self, mock_exec_query, mock_exec_one):
        # Target extension 1001 (Agent) called by 1002 (Customer)
        def query_side_effect(sql, params=None):
            if "FROM extensions" in sql:
                return {
                    "id": "ext-1001-uuid",
                    "extension_number": "1001",
                    "display_name": "Agent Alice",
                    "email": "alice@acme.com",
                    "no_answer_timeout": 20,
                    "tenant_id": "tenant-uuid-1",
                    "sip_domain": "acme.pbx.com"
                }
            elif "FROM tenants" in sql:
                return {"allow_internal_calls": True}
            elif "FROM call_forwarding" in sql:
                return None
            elif "FROM virtual_qa_configs" in sql:
                return {
                    "id": "qa-cfg-uuid-1",
                    "qa_extension_id": "ext-8999-uuid",
                    "max_warn_retries": 3,
                    "cooldown_seconds": 12,
                    "supervisor_extension": "9001",
                    "monitored_agents": ["1001"],
                    "monitored_callers": ["*"]
                }
            return None

        mock_exec_one.side_effect = query_side_effect

        form_data = {
            "Caller-Context": "default",
            "Caller-Destination-Number": "1001",
            "Caller-Caller-ID-Number": "1002",
            "Caller-Caller-ID-Name": "Customer Bob",
            "variable_tenant_id": "tenant-uuid-1",
            "domain": "acme.pbx.com"
        }

        xml_res = await handle_dialplan_request(form_data)
        root = ET.fromstring(xml_res)

        actions = [(a.attrib.get("application"), a.attrib.get("data")) for a in root.findall(".//action")]
        action_dict = {app: data for app, data in actions}

        # Check stereo and unbuffered recording
        set_actions = [data for app, data in actions if app == "set"]
        self.assertTrue(any("RECORD_STEREO=true" in s for s in set_actions), "RECORD_STEREO=true must be set")
        self.assertTrue(any("enable_file_write_buffering=false" in s for s in set_actions), "unbuffered recording must be set")
        self.assertTrue(any("qa_enabled=true" in s for s in set_actions), "qa_enabled=true must be set")
        self.assertTrue(any("qa_agent_leg=bleg" in s for s in set_actions), "qa_agent_leg must be bleg since agent 1001 is destination")
        self.assertTrue(any("qa_agent_ext=1001" in s for s in set_actions))
        self.assertTrue(any("qa_customer_ext=1002" in s for s in set_actions))

    @patch("src.xml_curl.dialplan.get_redis")
    @patch("src.xml_curl.dialplan.execute_query_one")
    async def test_supervisor_short_dial_code(self, mock_exec_one, mock_get_redis):
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_redis.get.return_value = "call-active-uuid-999"

        # Mock caller extension (Supervisor 9001)
        mock_exec_one.return_value = {
            "id": "ext-9001-uuid",
            "extension_number": "9001",
            "tenant_id": "tenant-uuid-1"
        }

        form_data = {
            "Caller-Context": "default",
            "Caller-Destination-Number": "*8811001", # Mode 1: Whisper to Agent 1001
            "Caller-Caller-ID-Number": "9001",
            "variable_tenant_id": "tenant-uuid-1",
            "domain": "acme.pbx.com"
        }

        xml_res = await handle_dialplan_request(form_data)
        root = ET.fromstring(xml_res)

        actions = [(a.attrib.get("application"), a.attrib.get("data")) for a in root.findall(".//action")]
        self.assertTrue(any(app == "eavesdrop" and "call-active-uuid-999" in data for app, data in actions), "Must bridge to eavesdrop")
        set_actions = [data for app, data in actions if app == "set"]
        self.assertTrue(any("eavesdrop_whisper_aleg=true" in s or "eavesdrop_enable_dtmf=true" in s for s in set_actions))

    @patch("src.xml_curl.dialplan.get_redis")
    @patch("src.xml_curl.dialplan.execute_query_one")
    async def test_supervisor_dial_listen_mode_0(self, mock_exec_one, mock_get_redis):
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_redis.get.return_value = "call-active-uuid-880"

        mock_exec_one.return_value = {
            "id": "ext-1004-uuid",
            "extension_number": "1004",
            "tenant_id": "tenant-uuid-1"
        }

        form_data = {
            "Caller-Context": "default",
            "Caller-Destination-Number": "*8801001", # Mode 0: Silent Listen (Spy)
            "Caller-Caller-ID-Number": "1004",
            "variable_tenant_id": "tenant-uuid-1",
            "domain": "acme.pbx.com"
        }

        xml_res = await handle_dialplan_request(form_data)
        root = ET.fromstring(xml_res)

        actions = [(a.attrib.get("application"), a.attrib.get("data")) for a in root.findall(".//action")]
        self.assertTrue(any(app == "eavesdrop" and "call-active-uuid-880" in data for app, data in actions))
        set_actions = [data for app, data in actions if app == "set"]
        self.assertTrue(any("eavesdrop_enable_dtmf=true" in s for s in set_actions))
        self.assertFalse(any("eavesdrop_whisper_aleg=true" in s for s in set_actions), "Mode 0 must not whisper to aleg")
        self.assertFalse(any("eavesdrop_whisper_bleg=true" in s for s in set_actions), "Mode 0 must not whisper to bleg")

    @patch("src.xml_curl.dialplan.get_redis")
    @patch("src.xml_curl.dialplan.execute_query_one")
    async def test_supervisor_dial_barge_mode_3(self, mock_exec_one, mock_get_redis):
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        mock_redis.get.return_value = "call-active-uuid-883"

        mock_exec_one.return_value = {
            "id": "ext-1004-uuid",
            "extension_number": "1004",
            "tenant_id": "tenant-uuid-1"
        }

        form_data = {
            "Caller-Context": "default",
            "Caller-Destination-Number": "*8831001", # Mode 3: Three-Way Barge In
            "Caller-Caller-ID-Number": "1004",
            "variable_tenant_id": "tenant-uuid-1",
            "domain": "acme.pbx.com"
        }

        xml_res = await handle_dialplan_request(form_data)
        root = ET.fromstring(xml_res)

        actions = [(a.attrib.get("application"), a.attrib.get("data")) for a in root.findall(".//action")]
        self.assertTrue(any(app == "eavesdrop" and "call-active-uuid-883" in data for app, data in actions))
        set_actions = [data for app, data in actions if app == "set"]
        self.assertTrue(any("eavesdrop_whisper_aleg=true" in s for s in set_actions), "Mode 3 must bridge mic to aleg")
        self.assertTrue(any("eavesdrop_whisper_bleg=true" in s for s in set_actions), "Mode 3 must bridge mic to bleg")

if __name__ == "__main__":
    unittest.main()
