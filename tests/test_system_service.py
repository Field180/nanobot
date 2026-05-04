"""Unit tests for services/system_service.py"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.system_service import (
    get_basic_status,
    get_api_version_info,
    get_detailed_system_status,
    API_VERSION,
    API_VERSIONS,
)


class TestGetBasicStatus(unittest.TestCase):

    @patch("services.system_service.subprocess.run")
    def test_nanobot_available(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = get_basic_status("/usr/bin/python3", Path("/ws"), 5)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["nanobot_available"])
        self.assertEqual(result["sessions"], 5)

    @patch("services.system_service.subprocess.run")
    def test_nanobot_unavailable(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        result = get_basic_status("/usr/bin/python3", Path("/ws"), 0)
        self.assertFalse(result["nanobot_available"])

    @patch("services.system_service.subprocess.run", side_effect=Exception("boom"))
    def test_nanobot_exception(self, mock_run):
        result = get_basic_status("/usr/bin/python3", Path("/ws"), 0)
        self.assertFalse(result["nanobot_available"])
        self.assertEqual(result["status"], "ok")

    def test_workspace_str(self):
        with patch("services.system_service.subprocess.run", return_value=MagicMock(returncode=0)):
            result = get_basic_status("/py", Path("/my/workspace"), 1)
            self.assertEqual(result["workspace"], "/my/workspace")


class TestGetApiVersionInfo(unittest.TestCase):

    def test_returns_success(self):
        result = get_api_version_info()
        self.assertTrue(result["success"])
        self.assertEqual(result["version"], API_VERSION)

    def test_has_features(self):
        result = get_api_version_info()
        self.assertIsInstance(result["features"], list)
        self.assertTrue(len(result["features"]) > 0)

    def test_has_release_date(self):
        result = get_api_version_info()
        self.assertIn("release_date", result)

    def test_version_in_versions_dict(self):
        self.assertIn(API_VERSION, API_VERSIONS)


class TestGetDetailedSystemStatus(unittest.TestCase):

    def test_with_empty_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_detailed_system_status(Path(tmpdir))
            self.assertTrue(result["success"])
            self.assertEqual(result["tasks"]["total"], 0)
            self.assertIsNone(result["health"]["score"])

    def test_with_tasks_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_path = Path(tmpdir) / "scheduled_tasks.json"
            tasks_path.write_text(json.dumps({
                "tasks": [
                    {"id": "t1", "name": "Test", "status": "pending", "enabled": True},
                    {"id": "t2", "name": "Test2", "status": "running"},
                ]
            }))
            result = get_detailed_system_status(Path(tmpdir))
            self.assertEqual(result["tasks"]["total"], 2)
            self.assertEqual(result["tasks"]["pending"], 1)
            self.assertEqual(result["tasks"]["running"], 1)

    def test_resources_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_detailed_system_status(Path(tmpdir))
            self.assertIn("resources", result)
            self.assertIn("memory_percent", result["resources"])
            self.assertIn("disk_percent", result["resources"])


if __name__ == "__main__":
    unittest.main()
