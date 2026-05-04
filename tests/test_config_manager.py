"""
Unit tests for config_manager module (P9 extraction).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config_manager as cm


class TestRateLimiting(unittest.TestCase):
    def setUp(self):
        cm.rate_limit_store.clear()

    def test_rate_limits_keys(self):
        self.assertIn("default", cm.RATE_LIMITS)
        self.assertIn("chat", cm.RATE_LIMITS)
        self.assertIn("stream", cm.RATE_LIMITS)

    def test_allowed_under_limit(self):
        allowed, remaining = cm.check_rate_limit("1.2.3.4", "default")
        self.assertTrue(allowed)
        self.assertIsInstance(remaining, int)

    def test_blocked_over_limit(self):
        # Fill the window
        for _ in range(cm.RATE_LIMITS["stream"]["requests"]):
            cm.check_rate_limit("5.6.7.8", "stream")
        allowed, remaining = cm.check_rate_limit("5.6.7.8", "stream")
        self.assertFalse(allowed)
        self.assertGreater(remaining, 0)

    def test_different_ips_independent(self):
        for _ in range(cm.RATE_LIMITS["stream"]["requests"]):
            cm.check_rate_limit("10.0.0.1", "stream")
        # Another IP should still be allowed
        allowed, _ = cm.check_rate_limit("10.0.0.2", "stream")
        self.assertTrue(allowed)


class TestResponseCache(unittest.TestCase):
    def setUp(self):
        cm._RESPONSE_CACHE.clear()

    def test_cache_key_deterministic(self):
        k1 = cm._get_cache_key("hello", "s1")
        k2 = cm._get_cache_key("hello", "s1")
        self.assertEqual(k1, k2)

    def test_cache_key_varies_by_session(self):
        k1 = cm._get_cache_key("hello", "s1")
        k2 = cm._get_cache_key("hello", "s2")
        self.assertNotEqual(k1, k2)

    def test_cache_miss(self):
        self.assertIsNone(cm._get_cached_response("miss", "s1"))

    def test_cache_round_trip(self):
        cm._cache_response("msg", "s1", {"answer": 42})
        result = cm._get_cached_response("msg", "s1")
        self.assertEqual(result, {"answer": 42})

    def test_cache_eviction(self):
        # Fill cache to max
        for i in range(cm._CACHE_MAX_SIZE + 5):
            cm._cache_response(f"msg{i}", "s1", {"i": i})
        self.assertLessEqual(len(cm._RESPONSE_CACHE), cm._CACHE_MAX_SIZE)


class TestToolVerbs(unittest.TestCase):
    def test_known_verbs(self):
        self.assertEqual(cm.TOOL_VERBS["file_read"], "读取文件")
        self.assertEqual(cm.TOOL_VERBS["shell_execute"], "执行命令")

    def test_get_tool_summary_known(self):
        # Note: original get_tool_summary has an operator-precedence quirk —
        # the `if tool_input.get("command")` ternary spans the whole or-chain,
        # so "path" only resolves when "command" is also present.
        s = cm.get_tool_summary("file_read", {"command": "cat /foo/bar.py"})
        self.assertIn("读取文件", s)

    def test_get_tool_summary_unknown_tool(self):
        s = cm.get_tool_summary("custom_tool", {"path": "/x"})
        self.assertIn("custom_tool", s)

    def test_get_tool_summary_no_target(self):
        s = cm.get_tool_summary("file_read", {})
        self.assertEqual(s, "读取文件")


class TestPromptStrings(unittest.TestCase):
    def test_security_audit_prompt_nonempty(self):
        self.assertGreater(len(cm.SECURITY_AUDIT_PROMPT), 50)

    def test_thinking_prompt_nonempty(self):
        self.assertGreater(len(cm.THINKING_PROMPT), 50)

    def test_actions_prompt_nonempty(self):
        self.assertGreater(len(cm.ACTIONS_PROMPT), 50)

    def test_tools_guide_prompt_nonempty(self):
        self.assertGreater(len(cm.TOOLS_GUIDE_PROMPT), 50)

    def test_security_audit_contains_key_sections(self):
        self.assertIn("风险等级", cm.SECURITY_AUDIT_PROMPT)

    def test_thinking_prompt_contains_thinking_tag(self):
        self.assertIn("<thinking>", cm.THINKING_PROMPT)

    def test_actions_prompt_contains_reversibility(self):
        self.assertIn("可逆性", cm.ACTIONS_PROMPT)

    def test_tools_guide_mentions_file_read(self):
        self.assertIn("file_read", cm.TOOLS_GUIDE_PROMPT)


if __name__ == "__main__":
    unittest.main()
