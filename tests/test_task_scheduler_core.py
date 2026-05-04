"""
Unit tests for task_scheduler_core module (P9b extraction).
"""
import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import task_scheduler_core as tsc


class TestScheduledTasksRegistry(unittest.TestCase):
    def setUp(self):
        tsc.SCHEDULED_TASKS.clear()

    def test_empty_initially(self):
        self.assertEqual(tsc.SCHEDULED_TASKS, {})

    def test_add_task(self):
        tsc.SCHEDULED_TASKS["t1"] = {
            "name": "test_task",
            "interval": 60,
            "enabled": True,
            "last_run": None,
            "next_run": datetime.now(),
            "callback": None,
        }
        self.assertIn("t1", tsc.SCHEDULED_TASKS)

    def test_remove_task(self):
        tsc.SCHEDULED_TASKS["t1"] = {"name": "x", "enabled": False}
        del tsc.SCHEDULED_TASKS["t1"]
        self.assertNotIn("t1", tsc.SCHEDULED_TASKS)


class TestRunScheduledTasks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tsc.SCHEDULED_TASKS.clear()

    async def test_executes_due_task(self):
        callback = AsyncMock()
        tsc.SCHEDULED_TASKS["t1"] = {
            "name": "due_task",
            "interval": 60,
            "enabled": True,
            "last_run": None,
            "next_run": datetime.now() - timedelta(seconds=1),
            "callback": callback,
        }

        # Run one iteration then cancel
        task = asyncio.create_task(tsc.run_scheduled_tasks())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        callback.assert_awaited_once()
        self.assertIsNotNone(tsc.SCHEDULED_TASKS["t1"]["last_run"])

    async def test_skips_disabled_task(self):
        callback = AsyncMock()
        tsc.SCHEDULED_TASKS["t2"] = {
            "name": "disabled_task",
            "interval": 60,
            "enabled": False,
            "last_run": None,
            "next_run": datetime.now() - timedelta(seconds=1),
            "callback": callback,
        }

        task = asyncio.create_task(tsc.run_scheduled_tasks())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        callback.assert_not_awaited()

    async def test_skips_future_task(self):
        callback = AsyncMock()
        tsc.SCHEDULED_TASKS["t3"] = {
            "name": "future_task",
            "interval": 60,
            "enabled": True,
            "last_run": None,
            "next_run": datetime.now() + timedelta(hours=1),
            "callback": callback,
        }

        task = asyncio.create_task(tsc.run_scheduled_tasks())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        callback.assert_not_awaited()


class TestAutoStartTaskScheduler(unittest.IsolatedAsyncioTestCase):
    async def test_skips_when_no_config_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # No scheduled_tasks.json → should return early
            await tsc.auto_start_task_scheduler(workspace=Path(tmpdir))
            # No error = success

    @patch("task_scheduler_core.psutil.process_iter")
    async def test_skips_when_already_running(self, mock_iter):
        proc = MagicMock()
        proc.info = {"pid": 123, "cmdline": ["python3", "scheduler.py", "daemon"]}
        mock_iter.return_value = [proc]

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / "scheduled_tasks.json").write_text("{}")
            await tsc.auto_start_task_scheduler(workspace=ws)

    async def test_skips_when_no_scheduler_script(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / "scheduled_tasks.json").write_text("{}")
            # No scheduler.py anywhere
            await tsc.auto_start_task_scheduler(workspace=ws)


if __name__ == "__main__":
    unittest.main()
