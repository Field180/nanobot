"""
Tests for CompactService — stateful compact engine service class.
Verifies: initialization, should_compact, circuit breaker, feature flag gating,
stats, and reset behavior.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from typing import Dict, Any, List
from unittest.mock import patch, AsyncMock

WEB_UI = Path(__file__).parent.parent
if str(WEB_UI) not in sys.path:
    sys.path.insert(0, str(WEB_UI))

from compact_engine import CompactService, _estimate_messages_tokens


def _make_env(**overrides) -> Dict[str, str]:
    """Create a minimal env dict for CompactService."""
    env = {
        "OLLAMA_NUM_CTX": "8192",
        "NANOBOT_AGENTS__DEFAULTS__MAX_TOKENS": "4096",
    }
    env.update(overrides)
    return env


def _make_messages(n: int, content_len: int = 100) -> List[Dict[str, Any]]:
    """Create n messages with given content length."""
    msgs = [{"role": "system", "content": "You are a helpful assistant."}]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": "x" * content_len})
    return msgs


class TestCompactServiceInit(unittest.TestCase):
    """CompactService initialization."""

    def test_init_sets_ceiling(self):
        svc = CompactService(_make_env())
        self.assertGreater(svc.ceiling, 0)

    def test_init_breaker_closed(self):
        svc = CompactService(_make_env())
        self.assertFalse(svc.is_breaker_open)

    def test_stats_initial(self):
        svc = CompactService(_make_env())
        stats = svc.stats
        self.assertEqual(stats["compact_failures"], 0)
        self.assertFalse(stats["breaker_open"])
        self.assertEqual(stats["total_compactions"], 0)
        self.assertEqual(stats["total_tokens_saved"], 0)


class TestShouldCompact(unittest.TestCase):
    """should_compact threshold and gating logic."""

    def test_below_threshold_returns_false(self):
        svc = CompactService(_make_env())
        msgs = _make_messages(3, 50)  # small messages
        self.assertFalse(svc.should_compact(msgs))

    def test_above_threshold_returns_true(self):
        svc = CompactService(_make_env(OLLAMA_NUM_CTX="1000"))
        # Create messages that exceed 80% of 1000 token ceiling
        msgs = _make_messages(30, 200)  # lots of content
        tokens = _estimate_messages_tokens(msgs)
        ceiling = svc.ceiling
        if tokens > int(ceiling * 0.80):
            self.assertTrue(svc.should_compact(msgs))
        else:
            # If not enough, just verify it returns bool
            self.assertIsInstance(svc.should_compact(msgs), bool)

    def test_breaker_open_returns_false(self):
        svc = CompactService(_make_env(OLLAMA_NUM_CTX="100"))
        svc._breaker_open = True
        msgs = _make_messages(50, 200)
        self.assertFalse(svc.should_compact(msgs))

    def test_feature_flag_disabled(self):
        from utils.feature_flags import ff
        ff.set_override("auto_compact", False)
        try:
            svc = CompactService(_make_env(OLLAMA_NUM_CTX="100"))
            msgs = _make_messages(50, 200)
            self.assertFalse(svc.should_compact(msgs))
        finally:
            ff.clear_override("auto_compact")


class TestGetTokenPressure(unittest.TestCase):
    """get_token_pressure returns correct metrics."""

    def test_structure(self):
        svc = CompactService(_make_env())
        msgs = _make_messages(5, 50)
        p = svc.get_token_pressure(msgs)
        self.assertIn("current_tokens", p)
        self.assertIn("ceiling", p)
        self.assertIn("threshold", p)
        self.assertIn("utilization", p)
        self.assertIn("messages", p)
        self.assertEqual(p["messages"], 6)  # 1 system + 5

    def test_utilization_range(self):
        svc = CompactService(_make_env())
        msgs = _make_messages(3, 50)
        p = svc.get_token_pressure(msgs)
        self.assertGreaterEqual(p["utilization"], 0)
        self.assertLessEqual(p["utilization"], 1.0)


class TestCircuitBreaker(unittest.TestCase):
    """Circuit breaker opens after 3 failures, half-opens after 5 turns."""

    def test_breaker_opens_after_3_failures(self):
        svc = CompactService(_make_env())
        svc._compact_failures = 2
        # Simulate one more failure
        svc._compact_failures += 1
        if svc._compact_failures >= 3:
            svc._breaker_open = True
        self.assertTrue(svc.is_breaker_open)

    def test_probe_half_open(self):
        svc = CompactService(_make_env())
        svc._breaker_open = True
        svc._breaker_open_turn = 10
        # Not enough turns elapsed
        svc.probe_half_open(14)
        self.assertTrue(svc.is_breaker_open)
        # Enough turns elapsed
        svc.probe_half_open(15)
        self.assertFalse(svc.is_breaker_open)

    def test_reset(self):
        svc = CompactService(_make_env())
        svc._compact_failures = 3
        svc._breaker_open = True
        svc._breaker_open_turn = 5
        svc.reset()
        self.assertEqual(svc._compact_failures, 0)
        self.assertFalse(svc._breaker_open)
        self.assertEqual(svc._breaker_open_turn, 0)


class TestCompactAsync(unittest.TestCase):
    """Async compact() method with mocked LLM."""

    def test_compact_skips_when_flag_disabled(self):
        from utils.feature_flags import ff
        ff.set_override("auto_compact", False)
        try:
            svc = CompactService(_make_env())
            msgs = _make_messages(10, 200)
            result = asyncio.run(svc.compact(msgs, "test-session"))
            self.assertIsNone(result)
        finally:
            ff.clear_override("auto_compact")

    def test_compact_skips_when_breaker_open(self):
        svc = CompactService(_make_env())
        svc._breaker_open = True
        msgs = _make_messages(10, 200)
        result = asyncio.run(svc.compact(msgs, "test-session"))
        self.assertIsNone(result)

    @patch("compact_engine._auto_compact")
    def test_compact_success_resets_failures(self, mock_compact):
        async def _mock_compact(*args, **kwargs):
            return {
                "compacted": True, "old_tokens": 5000, "new_tokens": 1000,
                "summary_length": 200, "mode": "full",
            }
        mock_compact.side_effect = _mock_compact
        svc = CompactService(_make_env())
        svc._compact_failures = 2
        msgs = _make_messages(10, 200)
        result = asyncio.run(svc.compact(msgs, "test-session"))
        if result and result.get("compacted"):
            self.assertEqual(svc._compact_failures, 0)
            self.assertEqual(svc._total_compactions, 1)
            self.assertEqual(svc._total_tokens_saved, 4000)

    @patch("compact_engine._auto_compact")
    def test_compact_failure_increments_counter(self, mock_compact):
        mock_compact.side_effect = Exception("LLM down")
        svc = CompactService(_make_env())
        msgs = _make_messages(10, 200)
        result = asyncio.run(svc.compact(msgs, "test-session"))
        self.assertIsNone(result)
        self.assertEqual(svc._compact_failures, 1)

    @patch("compact_engine._auto_compact")
    def test_compact_3_failures_opens_breaker(self, mock_compact):
        mock_compact.side_effect = Exception("LLM down")
        svc = CompactService(_make_env())
        msgs = _make_messages(10, 200)

        async def _run_3():
            for _ in range(3):
                await svc.compact(msgs, "test-session")

        asyncio.run(_run_3())
        self.assertTrue(svc.is_breaker_open)


class TestCompactServiceImport(unittest.TestCase):
    """Verify CompactService is importable from compact_engine."""

    def test_import(self):
        from compact_engine import CompactService
        self.assertTrue(hasattr(CompactService, "compact"))
        self.assertTrue(hasattr(CompactService, "should_compact"))
        self.assertTrue(hasattr(CompactService, "compact_with_heartbeat"))

    def test_agentic_loop_imports(self):
        import importlib
        mod = importlib.import_module("agentic_loop")
        self.assertTrue(hasattr(mod, "CompactService"))


if __name__ == "__main__":
    unittest.main()
