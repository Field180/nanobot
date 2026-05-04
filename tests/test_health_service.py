"""Unit tests for services/health_service.py"""

import os
import sys
import time
import unittest
from collections import defaultdict
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.health_service import (
    get_error_stats,
    reset_error_stats,
    get_performance_stats,
    get_performance_timeline,
    reset_performance_stats,
)


def _make_metrics(**overrides):
    m = {
        "request_times": [],
        "snn_times": [],
        "tool_calls": defaultdict(int),
        "total_requests": 0,
        "start_time": time.time(),
    }
    m.update(overrides)
    return m


class TestErrorStats(unittest.TestCase):

    def test_get_error_stats(self):
        handler = MagicMock()
        handler.get_error_stats.return_value = {"total": 5}
        result = get_error_stats(handler)
        self.assertTrue(result["success"])
        self.assertEqual(result["stats"]["total"], 5)

    def test_reset_error_stats(self):
        handler = MagicMock()
        result = reset_error_stats(handler)
        handler.reset_stats.assert_called_once()
        self.assertTrue(result["success"])
        self.assertIn("重置", result["message"])


class TestPerformanceStats(unittest.TestCase):

    def test_empty_metrics(self):
        metrics = _make_metrics()
        result = get_performance_stats(metrics)
        self.assertTrue(result["success"])
        self.assertEqual(result["stats"]["avg_response_time_ms"], 0)
        self.assertEqual(result["stats"]["avg_snn_time_ms"], 0)

    def test_with_request_times(self):
        metrics = _make_metrics(
            request_times=[0.1, 0.2, 0.3],
            total_requests=3,
        )
        result = get_performance_stats(metrics)
        self.assertGreater(result["stats"]["avg_response_time_ms"], 0)
        self.assertEqual(result["stats"]["total_requests"], 3)

    def test_uptime_format(self):
        metrics = _make_metrics(start_time=time.time() - 3661)
        result = get_performance_stats(metrics)
        self.assertIn("1h", result["stats"]["uptime_human"])

    def test_system_resources(self):
        metrics = _make_metrics()
        result = get_performance_stats(metrics)
        self.assertIn("cpu_percent", result["stats"]["system"])
        self.assertIn("memory_percent", result["stats"]["system"])


class TestPerformanceTimeline(unittest.TestCase):

    def test_empty(self):
        metrics = _make_metrics()
        result = get_performance_timeline(metrics, minutes=5)
        self.assertTrue(result["success"])
        self.assertEqual(result["timeline"]["request_count"], 0)

    def test_recent_requests(self):
        now = time.time()
        metrics = _make_metrics(request_times=[now - 60, now - 120, now - 600])
        result = get_performance_timeline(metrics, minutes=5)
        self.assertEqual(result["timeline"]["request_count"], 2)

    def test_requests_per_minute(self):
        now = time.time()
        metrics = _make_metrics(request_times=[now - 30, now - 60])
        result = get_performance_timeline(metrics, minutes=2)
        self.assertEqual(result["timeline"]["requests_per_minute"], 1.0)


class TestResetPerformanceStats(unittest.TestCase):

    def test_reset(self):
        metrics = _make_metrics(
            request_times=[0.1, 0.2],
            snn_times=[0.3],
            total_requests=10,
        )
        metrics["tool_calls"]["file_read"] = 5
        before = metrics["start_time"]
        result = reset_performance_stats(metrics)
        self.assertTrue(result["success"])
        self.assertEqual(len(metrics["request_times"]), 0)
        self.assertEqual(len(metrics["snn_times"]), 0)
        self.assertEqual(len(metrics["tool_calls"]), 0)
        self.assertEqual(metrics["total_requests"], 0)
        self.assertGreaterEqual(metrics["start_time"], before)


if __name__ == "__main__":
    unittest.main()
