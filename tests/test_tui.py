"""
P6: Tests for TUI module — command parsing, event rendering, session history.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure web_ui is importable
_WEB_UI = Path(__file__).resolve().parent.parent
if str(_WEB_UI) not in sys.path:
    sys.path.insert(0, str(_WEB_UI))


class TestBuildEnv(unittest.TestCase):
    """Test environment dict construction."""

    def test_basic_env_keys(self):
        from tui import build_env
        env = build_env(Path("/tmp/ws"))
        self.assertEqual(env["NANOBOT_WORKSPACE"], "/tmp/ws")
        self.assertEqual(env["NANOBOT_ENABLE_ALL_TOOLS"], "true")
        self.assertEqual(env["NANOBOT_AGENTIC_LOOP"], "true")

    def test_model_ollama_prefix(self):
        from tui import build_env
        env = build_env(Path("/tmp"), model="qwen3.5:35b")
        self.assertEqual(env["NANOBOT_AGENTS__DEFAULTS__MODEL"], "ollama_chat/qwen3.5:35b")
        self.assertEqual(env["NANOBOT_TELEMETRY_MODEL_NAME"], "qwen3.5:35b")

    def test_model_with_slash_no_prefix(self):
        from tui import build_env
        env = build_env(Path("/tmp"), model="openai/gpt-4")
        self.assertEqual(env["NANOBOT_AGENTS__DEFAULTS__MODEL"], "openai/gpt-4")

    def test_known_model_config(self):
        from tui import build_env
        env = build_env(Path("/tmp"), model="qwen3.5:35b")
        self.assertIn("NANOBOT_AGENTS__DEFAULTS__MAX_TOKENS", env)
        self.assertEqual(env["NANOBOT_AGENTS__DEFAULTS__MAX_TOKENS"], "16384")
        self.assertEqual(env["OLLAMA_NUM_CTX"], "262144")

    def test_unknown_model_no_extra_config(self):
        from tui import build_env
        env = build_env(Path("/tmp"), model="custom:7b")
        self.assertNotIn("OLLAMA_NUM_CTX", env)

    def test_empty_model(self):
        from tui import build_env
        env = build_env(Path("/tmp"))
        self.assertNotIn("NANOBOT_AGENTS__DEFAULTS__MODEL", env)

    def test_max_turns(self):
        from tui import build_env
        env = build_env(Path("/tmp"), max_turns=20)
        self.assertEqual(env["NANOBOT_MAX_TOOL_ITERATIONS"], "20")


class TestSlashCommandDetection(unittest.TestCase):
    """Test slash command → skill matching."""

    def test_known_skill(self):
        from tui import detect_slash_command
        result = detect_slash_command("/debug something broke")
        self.assertEqual(result, "debug")

    def test_known_skill_alias(self):
        from tui import detect_slash_command
        result = detect_slash_command("/gc")
        self.assertEqual(result, "commit")

    def test_unknown_command(self):
        from tui import detect_slash_command
        result = detect_slash_command("/nonexistent_cmd_xyz")
        self.assertIsNone(result)

    def test_no_slash(self):
        from tui import detect_slash_command
        result = detect_slash_command("hello world")
        self.assertIsNone(result)

    def test_empty_string(self):
        from tui import detect_slash_command
        result = detect_slash_command("")
        self.assertIsNone(result)

    def test_just_slash(self):
        from tui import detect_slash_command
        result = detect_slash_command("/")
        self.assertIsNone(result)

    def test_verify_skill(self):
        from tui import detect_slash_command
        result = detect_slash_command("/verify")
        self.assertEqual(result, "verify")

    def test_simplify_alias(self):
        from tui import detect_slash_command
        result = detect_slash_command("/cleanup")
        self.assertEqual(result, "simplify")


class TestSessionHistory(unittest.TestCase):
    """Test conversation history management."""

    def test_empty_history_returns_raw(self):
        from tui import SessionHistory
        h = SessionHistory()
        msg = h.build_context_message("hello")
        self.assertEqual(msg, "hello")

    def test_with_history_has_delimiters(self):
        from tui import SessionHistory
        h = SessionHistory()
        h.add("user", "first")
        h.add("assistant", "reply")
        msg = h.build_context_message("second")
        self.assertIn("[对话历史]", msg)
        self.assertIn("[当前问题]", msg)
        self.assertIn("second", msg)
        self.assertIn("first", msg)

    def test_clear(self):
        from tui import SessionHistory
        h = SessionHistory()
        h.add("user", "x")
        h.clear()
        msg = h.build_context_message("y")
        self.assertEqual(msg, "y")

    def test_truncation(self):
        from tui import SessionHistory
        h = SessionHistory(max_pairs=3)
        for i in range(20):
            h.add("user", f"msg{i}")
        # Should keep only last 6 entries (3 pairs × 2)
        self.assertLessEqual(len(h._pairs), 6)

    def test_long_content_truncated_in_context(self):
        from tui import SessionHistory
        h = SessionHistory()
        h.add("user", "x" * 1000)
        msg = h.build_context_message("q")
        # The long content should be truncated to ~500 chars + "..."
        self.assertIn("...", msg)


class TestEventRenderer(unittest.TestCase):
    """Test event rendering logic (output is to console, we test state)."""

    def test_chunk_accumulation(self):
        from tui import EventRenderer
        r = EventRenderer()
        r.render({"type": "chunk", "content": "hello "})
        r.render({"type": "chunk", "content": "world"})
        self.assertEqual(r.get_full_response(), "hello world")

    def test_reset(self):
        from tui import EventRenderer
        r = EventRenderer()
        r.render({"type": "chunk", "content": "data"})
        r.reset()
        self.assertEqual(r.get_full_response(), "")
        self.assertEqual(r._tool_count, 0)

    def test_brief_args(self):
        from tui import EventRenderer
        brief = EventRenderer._brief_args({"path": "/a/b/c", "name": "test"})
        self.assertIn("path=", brief)
        self.assertIn("name=", brief)

    def test_brief_args_truncation(self):
        from tui import EventRenderer
        brief = EventRenderer._brief_args({"data": "x" * 100})
        self.assertIn("...", brief)
        self.assertLessEqual(len(brief), 80)

    def test_brief_args_empty(self):
        from tui import EventRenderer
        self.assertEqual(EventRenderer._brief_args({}), "")

    def test_tool_count_incremented(self):
        from tui import EventRenderer
        r = EventRenderer()
        r.render({"type": "tool_start", "name": "file_read", "arguments": {}})
        r.render({"type": "tool_start", "name": "grep_search", "arguments": {}})
        self.assertEqual(r._tool_count, 2)

    def test_turn_tracked(self):
        from tui import EventRenderer
        r = EventRenderer()
        r.render({"type": "turn_start", "turn": 3})
        self.assertEqual(r._turn, 3)


class TestBuiltinCommands(unittest.TestCase):
    """Test builtin command handler."""

    def test_clear(self):
        from tui import handle_builtin, SessionHistory
        state = {"history": SessionHistory(), "mode": "code"}
        state["history"].add("user", "x")
        result = handle_builtin("clear", "", state)
        self.assertTrue(result)
        self.assertEqual(len(state["history"]._pairs), 0)

    def test_mode_switch(self):
        from tui import handle_builtin, SessionHistory
        state = {"history": SessionHistory(), "mode": "code"}
        result = handle_builtin("mode", "ask", state)
        self.assertTrue(result)
        self.assertEqual(state["mode"], "ask")

    def test_mode_invalid(self):
        from tui import handle_builtin, SessionHistory
        state = {"history": SessionHistory(), "mode": "code"}
        result = handle_builtin("mode", "invalid", state)
        self.assertTrue(result)
        self.assertEqual(state["mode"], "code")  # unchanged

    def test_exit_raises(self):
        from tui import handle_builtin, SessionHistory
        state = {"history": SessionHistory(), "mode": "code"}
        with self.assertRaises(SystemExit):
            handle_builtin("exit", "", state)

    def test_unknown_not_handled(self):
        from tui import handle_builtin, SessionHistory
        state = {"history": SessionHistory(), "mode": "code"}
        result = handle_builtin("nonexistent", "", state)
        self.assertFalse(result)

    def test_help(self):
        from tui import handle_builtin, SessionHistory
        state = {"history": SessionHistory(), "mode": "code"}
        result = handle_builtin("help", "", state)
        self.assertTrue(result)

    def test_skills_command(self):
        from tui import handle_builtin, SessionHistory
        state = {"history": SessionHistory(), "mode": "code"}
        result = handle_builtin("skills", "", state)
        self.assertTrue(result)

    def test_stats(self):
        from tui import handle_builtin, SessionHistory
        state = {
            "history": SessionHistory(),
            "mode": "code",
            "session_id": "tui-test",
            "workspace": Path("/tmp"),
            "model": "test",
            "exchange_count": 5,
        }
        result = handle_builtin("stats", "", state)
        self.assertTrue(result)


class TestRenderStats(unittest.TestCase):
    """Test token stats rendering."""

    def test_render_stats_no_crash(self):
        from tui import render_stats
        stats = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "model_display": "test-model",
        }
        # Should not raise
        render_stats(stats, 2.5)

    def test_render_stats_zero_elapsed(self):
        from tui import render_stats
        stats = {"prompt_tokens": 0, "completion_tokens": 0}
        render_stats(stats, 0.0)


class TestImports(unittest.TestCase):
    """Verify all TUI module imports work."""

    def test_all_exports(self):
        from tui import (
            build_env,
            detect_slash_command,
            EventRenderer,
            SessionHistory,
            handle_builtin,
            render_stats,
            chat_once,
            main_loop,
            parse_args,
            BUILTIN_COMMANDS,
        )
        self.assertTrue(callable(build_env))
        self.assertTrue(callable(detect_slash_command))
        self.assertIsInstance(BUILTIN_COMMANDS, dict)

    def test_argparse(self):
        from tui import parse_args
        with patch("sys.argv", ["tui.py", "--mode", "ask", "--model", "test:7b"]):
            args = parse_args()
            self.assertEqual(args.mode, "ask")
            self.assertEqual(args.model, "test:7b")


if __name__ == "__main__":
    unittest.main()
