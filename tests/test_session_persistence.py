"""
Unit tests for session_persistence module (P9 extraction).
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import session_persistence as sp


class _SPTestCase(unittest.TestCase):
    """Base that inits the module with a temp dir before each test."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._store_dir = Path(self._tmpdir)
        self._activity = {}
        self._timeout_cfg = {
            "enabled": True,
            "default_timeout_minutes": 30,
        }
        sp.init(self._store_dir, self._activity, self._timeout_cfg)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestSessionStorePath(_SPTestCase):
    def test_returns_path_in_store_dir(self):
        p = sp.session_store_path("abc123")
        self.assertEqual(p, self._store_dir / "web_ui_abc123.json")


class TestPersistAndLoad(_SPTestCase):
    def test_round_trip(self):
        sp._persist_session_payload("s1", {"hello": "world"})
        payload = sp.load_session_payload("s1")
        self.assertEqual(payload["hello"], "world")

    def test_load_missing_returns_empty(self):
        self.assertEqual(sp.load_session_payload("nonexistent"), {})


class TestParseSessionTimestamp(_SPTestCase):
    def test_none(self):
        self.assertIsNone(sp._parse_session_timestamp(None))

    def test_empty_string(self):
        self.assertIsNone(sp._parse_session_timestamp(""))

    def test_datetime_passthrough(self):
        now = datetime.now()
        self.assertEqual(sp._parse_session_timestamp(now), now)

    def test_unix_timestamp(self):
        ts = 1700000000.0
        result = sp._parse_session_timestamp(ts)
        self.assertIsInstance(result, datetime)

    def test_iso_string(self):
        iso = "2025-01-15T12:00:00"
        result = sp._parse_session_timestamp(iso)
        self.assertIsInstance(result, datetime)

    def test_invalid(self):
        self.assertIsNone(sp._parse_session_timestamp("not-a-date"))


class TestTouchSessionActivity(_SPTestCase):
    def test_no_persist(self):
        result = sp.touch_session_activity("s1", persist=False)
        self.assertIsInstance(result, datetime)
        self.assertIn("s1", self._activity)

    def test_persist_creates_file(self):
        # Pre-create a payload so touch has something to update
        sp._persist_session_payload("s2", {"session_id": "s2"})
        sp.touch_session_activity("s2", persist=True)
        payload = json.loads(sp.session_store_path("s2").read_text())
        self.assertIn("last_activity", payload)
        self.assertIn("expires_at", payload)


class TestSaveAndLoadSessionPayload(_SPTestCase):
    def test_save_creates_file(self):
        sp.save_session_payload("s3", {"key": "val"})
        self.assertTrue(sp.session_store_path("s3").exists())

    def test_save_adds_metadata(self):
        sp.save_session_payload("s4", {"key": "val"})
        payload = json.loads(sp.session_store_path("s4").read_text())
        self.assertIn("session_id", payload)
        self.assertIn("last_activity", payload)
        self.assertIn("expires_at", payload)

    def test_activity_updated(self):
        sp.save_session_payload("s5", {})
        self.assertIn("s5", self._activity)


class TestSaveAndLoadHistory(_SPTestCase):
    def test_round_trip(self):
        history = [{"role": "user", "content": "hi"}]
        sp.save_session_history("s6", history)
        loaded = sp.load_session_history("s6")
        self.assertEqual(loaded, history)

    def test_load_missing(self):
        result = sp.load_session_history("missing")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
