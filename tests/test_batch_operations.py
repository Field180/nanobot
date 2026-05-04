"""
Unit tests for batch_operations.py — batch delete / warm / execute module.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch_operations import (
    batch_delete_files, batch_warm_cache, batch_execute_operations,
    export_sessions_data, export_logs_data, export_stats_data,
)


class TestBatchDeleteFiles(unittest.TestCase):
    """Tests for batch_delete_files()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_delete_existing_files(self):
        # Create two files
        for name in ("a", "b"):
            Path(self.tmpdir, f"{name}.json").write_text("{}")
        result = batch_delete_files(["a", "b"], self.tmpdir, suffix=".json")
        self.assertTrue(result["success"])
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(sorted(result["deleted"]), ["a", "b"])

    def test_delete_nonexistent_file(self):
        result = batch_delete_files(["no_such_id"], self.tmpdir, suffix=".json")
        self.assertTrue(result["success"])
        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["failed"][0]["reason"], "not_found")

    def test_mixed_existing_and_missing(self):
        Path(self.tmpdir, "exists.json").write_text("{}")
        result = batch_delete_files(["exists", "gone"], self.tmpdir, suffix=".json")
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["failed_count"], 1)

    def test_empty_ids_list(self):
        result = batch_delete_files([], self.tmpdir)
        self.assertTrue(result["success"])
        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["failed_count"], 0)

    def test_custom_suffix(self):
        Path(self.tmpdir, "backup1.zip").write_text("data")
        result = batch_delete_files(["backup1"], self.tmpdir, suffix=".zip")
        self.assertEqual(result["deleted_count"], 1)
        self.assertFalse(Path(self.tmpdir, "backup1.zip").exists())


class TestBatchWarmCache(unittest.TestCase):
    """Tests for batch_warm_cache()."""

    def test_warm_single_endpoint(self):
        cache = {}
        result = batch_warm_cache(["/api/test"], cache)
        self.assertTrue(result["success"])
        self.assertEqual(result["warmed_count"], 1)
        self.assertIn("warm_/api/test", cache)

    def test_warm_multiple_endpoints(self):
        cache = {}
        result = batch_warm_cache(["/a", "/b", "/c"], cache)
        self.assertEqual(result["warmed_count"], 3)
        self.assertEqual(result["failed_count"], 0)

    def test_cache_entry_structure(self):
        cache = {}
        batch_warm_cache(["/ep"], cache, ttl=600)
        entry = cache["warm_/ep"]
        self.assertTrue(entry["data"]["warmed"])
        self.assertEqual(entry["ttl"], 600)
        self.assertIn("timestamp", entry)

    def test_empty_endpoints(self):
        cache = {}
        result = batch_warm_cache([], cache)
        self.assertEqual(result["warmed_count"], 0)
        self.assertEqual(len(cache), 0)

    def test_custom_ttl(self):
        cache = {}
        batch_warm_cache(["/x"], cache, ttl=999)
        self.assertEqual(cache["warm_/x"]["ttl"], 999)


class TestBatchExecuteOperations(unittest.TestCase):
    """Tests for batch_execute_operations()."""

    def test_cache_clear(self):
        cache = {"key": "val"}
        result = batch_execute_operations(
            [{"type": "cache_clear"}], cache, [], None
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(cache), 0)
        self.assertEqual(result["results"][0]["status"], "success")

    def test_audit_clear(self):
        audit = [{"entry": 1}, {"entry": 2}]
        result = batch_execute_operations(
            [{"type": "audit_clear"}], {}, audit, None
        )
        self.assertEqual(len(audit), 0)
        self.assertEqual(result["results"][0]["status"], "success")

    def test_config_reload(self):
        called = []
        fn = lambda: called.append(True)
        result = batch_execute_operations(
            [{"type": "config_reload"}], {}, [], fn
        )
        self.assertEqual(len(called), 1)
        self.assertEqual(result["results"][0]["status"], "success")

    def test_unknown_operation(self):
        result = batch_execute_operations(
            [{"type": "foo_bar"}], {}, [], None
        )
        self.assertEqual(result["results"][0]["status"], "unknown_operation")

    def test_multiple_operations(self):
        cache = {"a": 1}
        audit = [1, 2]
        ops = [
            {"type": "cache_clear"},
            {"type": "audit_clear"},
            {"type": "unknown_thing"},
        ]
        result = batch_execute_operations(ops, cache, audit, None)
        self.assertEqual(result["total_operations"], 3)
        self.assertEqual(len(cache), 0)
        self.assertEqual(len(audit), 0)
        statuses = [r["status"] for r in result["results"]]
        self.assertEqual(statuses, ["success", "success", "unknown_operation"])

    def test_empty_operations(self):
        result = batch_execute_operations([], {}, [], None)
        self.assertEqual(result["total_operations"], 0)
        self.assertEqual(result["results"], [])

    def test_config_reload_none_fn(self):
        """config_reload with no callable should still succeed."""
        result = batch_execute_operations(
            [{"type": "config_reload"}], {}, [], None
        )
        self.assertEqual(result["results"][0]["status"], "success")


class TestExportSessionsData(unittest.TestCase):
    """Tests for export_sessions_data()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_directory(self):
        result = export_sessions_data(self.tmpdir)
        self.assertTrue(result["success"])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["sessions"], [])

    def test_nonexistent_directory(self):
        result = export_sessions_data("/tmp/nonexistent_dir_xyz")
        self.assertTrue(result["success"])
        self.assertEqual(result["total"], 0)

    def test_reads_session_files(self):
        import json as _json
        for name in ("web_ui_aaa", "web_ui_bbb"):
            Path(self.tmpdir, f"{name}.json").write_text(
                _json.dumps({"history": [{"role": "user", "content": "hi"}]})
            )
        result = export_sessions_data(self.tmpdir)
        self.assertEqual(result["total"], 2)
        ids = sorted(s["session_id"] for s in result["sessions"])
        self.assertEqual(ids, ["web_ui_aaa", "web_ui_bbb"])

    def test_csv_format(self):
        import json as _json
        Path(self.tmpdir, "web_ui_x.json").write_text(
            _json.dumps({"history": []})
        )
        result = export_sessions_data(self.tmpdir, output_format="csv")
        self.assertIn("csv_content", result)
        self.assertIn("session_id", result["csv_content"])

    def test_json_format_has_no_csv_key(self):
        result = export_sessions_data(self.tmpdir, output_format="json")
        self.assertNotIn("csv_content", result)

    def test_malformed_json_skipped(self):
        Path(self.tmpdir, "web_ui_bad.json").write_text("NOT JSON")
        result = export_sessions_data(self.tmpdir)
        self.assertEqual(result["total"], 0)


class TestExportLogsData(unittest.TestCase):
    """Tests for export_logs_data()."""

    SAMPLE_LOGS = [
        {"request_id": "r1", "timestamp": "t1", "method": "GET",
         "path": "/a", "client_ip": "127.0.0.1", "status_code": 200,
         "duration_ms": 5},
        {"request_id": "r2", "timestamp": "t2", "method": "POST",
         "path": "/b", "client_ip": "127.0.0.1", "status_code": 201,
         "duration_ms": 12},
    ]

    def test_json_format(self):
        result = export_logs_data(self.SAMPLE_LOGS, output_format="json", limit=100)
        self.assertTrue(result["success"])
        self.assertEqual(result["total"], 2)
        self.assertNotIn("csv_content", result)

    def test_csv_format(self):
        result = export_logs_data(self.SAMPLE_LOGS, output_format="csv", limit=100)
        self.assertIn("csv_content", result)
        self.assertIn("request_id", result["csv_content"])
        self.assertIn("r1", result["csv_content"])

    def test_limit_slices_logs(self):
        result = export_logs_data(self.SAMPLE_LOGS, limit=1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["logs"][0]["request_id"], "r2")

    def test_empty_log(self):
        result = export_logs_data([])
        self.assertEqual(result["total"], 0)

    def test_limit_zero_returns_all(self):
        result = export_logs_data(self.SAMPLE_LOGS, limit=0)
        self.assertEqual(result["total"], 2)


class TestExportStatsData(unittest.TestCase):
    """Tests for export_stats_data()."""

    def _make_mock_manager(self, return_value):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.list_backups.return_value = return_value
        return m

    def _make_ws_manager(self):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.get_stats.return_value = {"total_connections": 3}
        return m

    def test_returns_success(self):
        result = export_stats_data(
            session_store_dir="/tmp/nonexistent",
            backup_manager=self._make_mock_manager([]),
            ws_manager=self._make_ws_manager(),
            performance_metrics={"total_requests": 42, "tool_calls": {}},
            audit_log=[1, 2, 3],
            api_cache={"k": "v"},
        )
        self.assertTrue(result["success"])
        self.assertIn("export_time", result)
        self.assertIn("stats", result)

    def test_stats_structure(self):
        result = export_stats_data(
            session_store_dir="/tmp/nonexistent",
            backup_manager=self._make_mock_manager(["b1"]),
            ws_manager=self._make_ws_manager(),
            performance_metrics={"total_requests": 10, "tool_calls": {"read": 5}},
            audit_log=[],
            api_cache={},
        )
        stats = result["stats"]
        self.assertIn("system", stats)
        self.assertIn("cpu_percent", stats["system"])
        self.assertEqual(stats["backups"]["total"], 1)
        self.assertEqual(stats["performance"]["total_requests"], 10)
        self.assertEqual(stats["audit"]["total_entries"], 0)
        self.assertEqual(stats["cache"]["total_entries"], 0)


if __name__ == "__main__":
    unittest.main()
