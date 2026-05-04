"""Unit tests for services/scheduler_service.py"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.scheduler_service import (
    generate_task_description,
    read_tasks_file,
    enrich_tasks,
    execute_task,
    _save_tasks,
)


class TestGenerateTaskDescription(unittest.TestCase):
    """Tests for the generate_task_description heuristic."""

    def test_existing_description_preserved(self):
        task = {"description": "This is a long existing description that should be kept"}
        self.assertEqual(generate_task_description(task), task["description"])

    def test_short_description_ignored(self):
        task = {"description": "short", "id": "health_check"}
        desc = generate_task_description(task)
        self.assertIn("扫描系统状态", desc)

    def test_python_prefix_description_ignored(self):
        task = {"description": "python3 run_health.py", "id": "health_check"}
        desc = generate_task_description(task)
        self.assertIn("扫描系统状态", desc)

    def test_id_pattern_health_check(self):
        task = {"id": "daily_health_check", "name": "", "command": ""}
        self.assertIn("扫描系统状态", generate_task_description(task))

    def test_id_pattern_backup(self):
        task = {"id": "nightly_backup", "name": "", "command": ""}
        self.assertIn("备份", generate_task_description(task))

    def test_name_pattern_cache(self):
        task = {"id": "task_1", "name": "清理缓存", "command": ""}
        self.assertIn("缓存", generate_task_description(task))

    def test_command_python_script(self):
        task = {"id": "x", "name": "x", "command": "python3 run_health.py"}
        self.assertIn("扫描系统状态", generate_task_description(task))

    def test_command_shell_rm(self):
        task = {"id": "x", "name": "x", "command": "rm -rf /tmp/cache"}
        self.assertIn("清理", generate_task_description(task))

    def test_command_curl(self):
        task = {"id": "x", "name": "x", "command": "curl https://example.com"}
        self.assertIn("网络", generate_task_description(task))

    def test_generic_fallback_with_command(self):
        task = {"id": "x", "name": "x", "command": "some_unique_command --flag"}
        desc = generate_task_description(task)
        self.assertIn("执行系统维护任务", desc)

    def test_generic_fallback_no_command(self):
        task = {"id": "x", "name": "x", "command": ""}
        desc = generate_task_description(task)
        self.assertIn("执行预定的系统维护任务", desc)

    def test_script_name_extraction(self):
        task = {"id": "x", "name": "x", "command": "python3 my_custom_tool.py"}
        desc = generate_task_description(task)
        self.assertIn("my_custom_tool", desc)


class TestReadTasksFile(unittest.TestCase):
    """Tests for read_tasks_file."""

    def test_missing_file_returns_empty(self):
        result = read_tasks_file(Path("/nonexistent/tasks.json"))
        self.assertEqual(result, [])

    def test_valid_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"tasks": [{"id": "t1"}, {"id": "t2"}]}, f)
            f.flush()
            path = Path(f.name)
        try:
            result = read_tasks_file(path)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["id"], "t1")
        finally:
            path.unlink()

    def test_corrupt_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{bad json")
            f.flush()
            path = Path(f.name)
        try:
            result = read_tasks_file(path)
            self.assertEqual(result, [])
        finally:
            path.unlink()

    def test_missing_tasks_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"other": 1}, f)
            f.flush()
            path = Path(f.name)
        try:
            result = read_tasks_file(path)
            self.assertEqual(result, [])
        finally:
            path.unlink()


class TestEnrichTasks(unittest.TestCase):
    """Tests for enrich_tasks."""

    def test_defaults_filled(self):
        tasks = [{"name": "test_task"}]
        enrich_tasks(tasks, scheduler_running=False)
        t = tasks[0]
        self.assertIn("id", t)
        self.assertEqual(t["status"], "pending")
        self.assertEqual(t["schedule_type"], "interval")
        self.assertEqual(t["interval_seconds"], 3600)
        self.assertIsNotNone(t["description"])

    def test_overdue_task_marked_failed_when_scheduler_off(self):
        tasks = [{
            "name": "overdue",
            "status": "pending",
            "next_run": "2020-01-01T00:00:00",
        }]
        enrich_tasks(tasks, scheduler_running=False)
        self.assertEqual(tasks[0]["status"], "failed")
        self.assertIn("过期", tasks[0].get("error", ""))

    def test_overdue_task_rescheduled_when_scheduler_on(self):
        tasks = [{
            "name": "overdue",
            "status": "pending",
            "next_run": "2020-01-01T00:00:00",
            "schedule_type": "interval",
            "interval_seconds": 600,
        }]
        enrich_tasks(tasks, scheduler_running=True)
        self.assertNotEqual(tasks[0]["next_run"], "2020-01-01T00:00:00")
        self.assertIsNotNone(tasks[0]["last_run"])

    def test_future_task_not_modified(self):
        tasks = [{
            "name": "future",
            "status": "pending",
            "next_run": "2099-01-01T00:00:00",
        }]
        enrich_tasks(tasks, scheduler_running=False)
        self.assertEqual(tasks[0]["status"], "pending")

    def test_returns_same_list(self):
        tasks = [{"name": "a"}]
        result = enrich_tasks(tasks, scheduler_running=False)
        self.assertIs(result, tasks)


class TestExecuteTask(unittest.TestCase):
    """Tests for execute_task."""

    def _write_tasks_file(self, tasks):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump({"tasks": tasks}, f, ensure_ascii=False)
        f.flush()
        f.close()
        return Path(f.name)

    def test_missing_task_id(self):
        result = execute_task("", Path("/tmp/x.json"), Path("/tmp"))
        self.assertFalse(result["success"])
        self.assertIn("task_id", result["error"])

    def test_missing_file(self):
        result = execute_task("t1", Path("/nonexistent.json"), Path("/tmp"))
        self.assertFalse(result["success"])

    def test_task_not_found(self):
        path = self._write_tasks_file([{"id": "other", "command": "echo hi", "name": "x"}])
        try:
            result = execute_task("missing", path, Path("/tmp"))
            self.assertFalse(result["success"])
            self.assertIn("未找到", result["error"])
        finally:
            path.unlink()

    def test_no_command(self):
        path = self._write_tasks_file([{"id": "t1", "command": "", "name": "x"}])
        try:
            result = execute_task("t1", path, Path("/tmp"))
            self.assertFalse(result["success"])
            self.assertIn("命令", result["error"])
        finally:
            path.unlink()

    @patch("services.scheduler_service.subprocess.run")
    def test_successful_execution(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok\n", stderr=""
        )
        path = self._write_tasks_file([{"id": "t1", "command": "echo hi", "name": "Test"}])
        try:
            result = execute_task("t1", path, Path("/tmp"))
            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "completed")
            self.assertIn("ok", result["result"])
        finally:
            path.unlink()

    @patch("services.scheduler_service.subprocess.run")
    def test_failed_execution(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error msg"
        )
        path = self._write_tasks_file([{"id": "t1", "command": "bad_cmd", "name": "Fail"}])
        try:
            result = execute_task("t1", path, Path("/tmp"))
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "failed")
        finally:
            path.unlink()

    @patch("services.scheduler_service.subprocess.run", side_effect=FileNotFoundError)
    def test_command_not_found(self, mock_run):
        path = self._write_tasks_file([{"id": "t1", "command": "nonexistent_cmd", "name": "X"}])
        try:
            result = execute_task("t1", path, Path("/tmp"))
            self.assertFalse(result["success"])
            self.assertIn("不存在", result["error"])
        finally:
            path.unlink()


class TestSaveTasks(unittest.TestCase):
    """Tests for _save_tasks helper."""

    def test_save_and_read_back(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            data = {"tasks": [{"id": "t1"}]}
            _save_tasks(path, data)
            with open(path) as f:
                loaded = json.load(f)
            self.assertEqual(loaded["tasks"][0]["id"], "t1")
        finally:
            path.unlink()

    def test_save_to_bad_path_no_crash(self):
        # Should not raise
        _save_tasks(Path("/nonexistent_dir/tasks.json"), {"tasks": []})


if __name__ == "__main__":
    unittest.main()
