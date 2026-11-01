import os
import unittest
from src.core.config import settings

class TestVirtualQASchemaAndConfig(unittest.TestCase):
    def test_virtual_qa_config_settings_exist(self):
        # ElevenLabs settings
        self.assertTrue(hasattr(settings, "ELEVENLABS_API_KEY"))
        self.assertEqual(settings.ELEVENLABS_STT_MODEL, "scribe_v1")
        self.assertEqual(settings.ELEVENLABS_TTS_MODEL, "eleven_flash_v2_5")
        self.assertEqual(settings.ELEVENLABS_VOICE_ID, "JBFqnCBsd6RMkjVDRZzb")

        # Google Gemini & Vertex AI settings
        self.assertTrue(hasattr(settings, "GEMINI_API_KEY"))
        self.assertTrue(hasattr(settings, "GOOGLE_APPLICATION_CREDENTIALS"))
        self.assertTrue(hasattr(settings, "GOOGLE_CLOUD_PROJECT"))
        self.assertEqual(settings.GOOGLE_CLOUD_LOCATION, "global")
        self.assertEqual(settings.GEMINI_QA_MODEL, "gemini-3.5-flash")

        # QA Pipeline settings
        self.assertFalse(settings.QA_DEMO_MODE)
        self.assertEqual(settings.QA_RECORDINGS_DIR, "/var/lib/freeswitch/recordings")
        self.assertEqual(settings.QA_POLL_INTERVAL_SEC, 2.0)
        self.assertEqual(settings.QA_WINDOW_SEC, 6.0)
        self.assertEqual(settings.QA_WINDOW_OVERLAP_SEC, 1.5)
        self.assertEqual(settings.QA_SILENCE_RMS, 350.0)
        self.assertEqual(settings.QA_MAX_CONCURRENT_AI, 8)
        self.assertEqual(settings.QA_LEADER_LOCK_TTL_SEC, 30)
        self.assertEqual(settings.QA_EVENT_RETENTION_DAYS, 30)

    def test_migration_file_exists_and_contains_definitions(self):
        mig_path = os.path.join(os.path.dirname(__file__), "../migrations/002_virtual_qa.sql")
        self.assertTrue(os.path.exists(mig_path), "migrations/002_virtual_qa.sql must exist")
        with open(mig_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("uq_roles_name", content)
        self.assertIn("VIRTUAL_QA", content)
        self.assertIn("is_virtual", content)
        self.assertIn("virtual_qa_configs", content)
        self.assertIn("qa_events", content)
        self.assertIn("idx_qa_events_tenant_time", content)
        self.assertIn("idx_qa_events_call", content)

if __name__ == "__main__":
    unittest.main()
