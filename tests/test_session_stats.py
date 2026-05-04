"""
P5: Tests for session_stats.py — session-level statistics aggregation.
"""
import sys
import time
import unittest
from pathlib import Path

_WEB_UI = Path(__file__).resolve().parent.parent
if str(_WEB_UI) not in sys.path:
    sys.path.insert(0, str(_WEB_UI))

from session_stats import SessionStatsStore, TurnRecord, get_stats_store, reset_global_store


class TestTurnRecord(unittest.TestCase):
    """Test TurnRecord data class."""

    def test_basic_creation(self):
        r = TurnRecord(prompt_tokens=100, completion_tokens=50)
        self.assertEqual(r.prompt_tokens, 100)
        self.assertEqual(r.completion_tokens, 50)
        self.assertEqual(r.total_tokens, 150)
        self.assertIsInstance(r.timestamp, float)

    def test_explicit_total(self):
        r = TurnRecord(prompt_tokens=100, completion_tokens=50, total_tokens=200)
        self.assertEqual(r.total_tokens, 200)

    def test_defaults(self):
        r = TurnRecord()
        self.assertEqual(r.prompt_tokens, 0)
        self.assertEqual(r.tool_calls, 0)
        self.assertEqual(r.tools_used, [])
        self.assertEqual(r.model, "")
        self.assertEqual(r.turns, 1)


class TestSessionStatsStore(unittest.TestCase):
    """Test session stats aggregation."""

    def setUp(self):
        self.store = SessionStatsStore()

    def test_empty_session(self):
        stats = self.store.get_stats("nonexistent")
        self.assertEqual(stats["total_exchanges"], 0)
        self.assertEqual(stats["total_tokens"], 0)
        self.assertIsNone(stats["first_activity"])

    def test_single_turn(self):
        self.store.add_turn(
            "s1",
            prompt_tokens=1000,
            completion_tokens=200,
            tool_calls=3,
            tools_used=["file_read", "grep_search", "file_read"],
            elapsed_seconds=2.5,
            model="qwen3.5:35b",
            turns=2,
        )
        stats = self.store.get_stats("s1")
        self.assertEqual(stats["total_exchanges"], 1)
        self.assertEqual(stats["total_turns"], 2)
        self.assertEqual(stats["total_prompt_tokens"], 1000)
        self.assertEqual(stats["total_completion_tokens"], 200)
        self.assertEqual(stats["total_tokens"], 1200)
        self.assertEqual(stats["total_tool_calls"], 3)
        self.assertEqual(stats["tools_distribution"], {"file_read": 2, "grep_search": 1})
        self.assertAlmostEqual(stats["total_elapsed_seconds"], 2.5, places=1)
        self.assertEqual(stats["models_used"], ["qwen3.5:35b"])

    def test_multiple_turns(self):
        self.store.add_turn("s1", prompt_tokens=500, completion_tokens=100,
                            tool_calls=2, tools_used=["file_read", "file_read"],
                            elapsed_seconds=1.0, model="m1")
        self.store.add_turn("s1", prompt_tokens=800, completion_tokens=300,
                            tool_calls=5, tools_used=["grep_search", "shell_execute", "file_read"],
                            elapsed_seconds=3.0, model="m1")
        stats = self.store.get_stats("s1")
        self.assertEqual(stats["total_exchanges"], 2)
        self.assertEqual(stats["total_prompt_tokens"], 1300)
        self.assertEqual(stats["total_completion_tokens"], 400)
        self.assertEqual(stats["total_tokens"], 1700)
        self.assertEqual(stats["total_tool_calls"], 7)
        self.assertEqual(stats["tools_distribution"]["file_read"], 3)
        self.assertEqual(stats["tools_distribution"]["grep_search"], 1)
        self.assertAlmostEqual(stats["total_elapsed_seconds"], 4.0, places=1)
        self.assertAlmostEqual(stats["avg_elapsed_per_exchange"], 2.0, places=1)

    def test_avg_tokens_per_exchange(self):
        self.store.add_turn("s1", total_tokens=1000, elapsed_seconds=1.0)
        self.store.add_turn("s1", total_tokens=2000, elapsed_seconds=2.0)
        stats = self.store.get_stats("s1")
        self.assertAlmostEqual(stats["avg_tokens_per_exchange"], 1500.0)

    def test_multiple_models(self):
        self.store.add_turn("s1", model="model-a")
        self.store.add_turn("s1", model="model-b")
        self.store.add_turn("s1", model="model-a")
        stats = self.store.get_stats("s1")
        self.assertEqual(stats["models_used"], ["model-a", "model-b"])

    def test_reset(self):
        self.store.add_turn("s1", prompt_tokens=100)
        self.store.reset("s1")
        stats = self.store.get_stats("s1")
        self.assertEqual(stats["total_exchanges"], 0)

    def test_max_sessions_eviction(self):
        store = SessionStatsStore(max_sessions=3)
        for i in range(5):
            store.add_turn(f"session-{i}", prompt_tokens=100)
            time.sleep(0.01)  # Ensure different timestamps
        # Should have kept max 3 sessions
        self.assertLessEqual(store.get_session_count(), 3)
        # Most recent should be kept
        stats = store.get_stats("session-4")
        self.assertEqual(stats["total_exchanges"], 1)

    def test_max_turns_per_session(self):
        store = SessionStatsStore(max_turns_per_session=5)
        for i in range(10):
            store.add_turn("s1", prompt_tokens=i * 100)
        stats = store.get_stats("s1")
        self.assertEqual(stats["total_exchanges"], 5)  # capped
        # Should keep the last 5 (500 + 600 + 700 + 800 + 900)
        self.assertEqual(stats["total_prompt_tokens"], 500 + 600 + 700 + 800 + 900)

    def test_get_all_sessions(self):
        self.store.add_turn("s1", total_tokens=100, tool_calls=2)
        self.store.add_turn("s2", total_tokens=200, tool_calls=3)
        sessions = self.store.get_all_sessions()
        self.assertEqual(len(sessions), 2)
        # Sorted by last_activity descending
        self.assertEqual(sessions[0]["session_id"], "s2")
        self.assertEqual(sessions[0]["total_tokens"], 200)
        self.assertEqual(sessions[1]["session_id"], "s1")

    def test_timestamp_tracking(self):
        before = time.time()
        self.store.add_turn("s1", prompt_tokens=100)
        after = time.time()
        stats = self.store.get_stats("s1")
        self.assertGreaterEqual(stats["first_activity"], before)
        self.assertLessEqual(stats["last_activity"], after)

    def test_avg_tokens_per_second(self):
        self.store.add_turn("s1", completion_tokens=100, elapsed_seconds=2.0)
        self.store.add_turn("s1", completion_tokens=200, elapsed_seconds=4.0)
        stats = self.store.get_stats("s1")
        # 300 completion / 6.0 seconds = 50.0 tok/s
        self.assertAlmostEqual(stats["avg_tokens_per_second"], 50.0, places=1)


class TestGlobalSingleton(unittest.TestCase):
    """Test singleton pattern."""

    def setUp(self):
        reset_global_store()

    def tearDown(self):
        reset_global_store()

    def test_get_returns_same_instance(self):
        s1 = get_stats_store()
        s2 = get_stats_store()
        self.assertIs(s1, s2)

    def test_reset_creates_new(self):
        s1 = get_stats_store()
        reset_global_store()
        s2 = get_stats_store()
        self.assertIsNot(s1, s2)

    def test_data_persists_in_singleton(self):
        store = get_stats_store()
        store.add_turn("test-session", prompt_tokens=500)
        # Get again
        store2 = get_stats_store()
        stats = store2.get_stats("test-session")
        self.assertEqual(stats["total_prompt_tokens"], 500)


class TestTuiStatsIntegration(unittest.TestCase):
    """Test TUI /stats command with session_stats."""

    def setUp(self):
        reset_global_store()

    def tearDown(self):
        reset_global_store()

    def test_stats_command_with_data(self):
        from tui import handle_builtin, SessionHistory
        from session_stats import get_stats_store

        store = get_stats_store()
        store.add_turn(
            "tui-test123",
            prompt_tokens=2000,
            completion_tokens=500,
            tool_calls=4,
            tools_used=["file_read", "grep_search", "file_read", "shell_execute"],
            elapsed_seconds=5.0,
            model="qwen3.5:35b",
            turns=3,
        )

        state = {
            "history": SessionHistory(),
            "mode": "code",
            "session_id": "tui-test123",
            "workspace": Path("/tmp"),
            "model": "qwen3.5:35b",
            "exchange_count": 1,
        }
        # Should not crash and return True
        result = handle_builtin("stats", "", state)
        self.assertTrue(result)

    def test_stats_command_no_data(self):
        from tui import handle_builtin, SessionHistory

        state = {
            "history": SessionHistory(),
            "mode": "code",
            "session_id": "empty-session",
            "workspace": Path("/tmp"),
            "model": "",
            "exchange_count": 0,
        }
        result = handle_builtin("stats", "", state)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
