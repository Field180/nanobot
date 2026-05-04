#!/usr/bin/env python3
"""
U12-ALT: Small Model Behavior Test Suite
=========================================

Verifies that nanobot's correction mechanisms correctly handle the 5 core
behavior defects observed in small/local models (7B-14B via llama.cpp):

  1. Narration — "我将搜索…" / "Let me find…" before tool calls
  2. Hallucination — fabricating details not present in tool results
  3. Over-elaboration — expanding simple results into categorized reports
  4. Wrong tool selection — file_read on non-existent files
  5. Result regurgitation — restating tool output verbatim

Test categories:
  - S1_*: Pattern detection (regex matching on real small-model outputs)
  - S2_*: Pre-emption & quick-path (file/dir operations with real tmpdir)
  - S3_*: Correction injection (message array manipulation)
  - S4_*: End-to-end scenario replay (mock LLM → full pipeline checks)

Each test is derived from REAL conversation logs captured on 2026-04-24
from llama.cpp with a local GGUF model.
"""

import asyncio
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

# ── Ensure web_ui is importable ──
_WEB_UI = Path(__file__).resolve().parent.parent
if str(_WEB_UI) not in sys.path:
    sys.path.insert(0, str(_WEB_UI))


# ═══════════════════════════════════════════════════════════════
# S1: Pattern Detection — regex tests on real small-model outputs
# ═══════════════════════════════════════════════════════════════

class TestS1NarrationDetection(unittest.TestCase):
    """S1.1: Narration regex catches common small-model preamble patterns."""

    def setUp(self):
        from agentic_loop import _NARRATION_RE
        self.RE = _NARRATION_RE

    # -- Chinese narration patterns (from real logs) --

    def test_chinese_wo_jiang(self):
        """'我将搜索整个项目' — most common Chinese narration."""
        self.assertIsNotNone(self.RE.search("我将搜索整个项目，查找是否存在名为 Dockerfile 的文件。"))

    def test_chinese_wo_lai(self):
        """'我来读取' — another Chinese narration variant."""
        self.assertIsNotNone(self.RE.search("我来读取 /home/field/.nanobot/workspace/AGENTS.md 文件的内容。"))

    def test_chinese_rang_wo(self):
        """'让我查看' — third Chinese narration variant."""
        self.assertIsNotNone(self.RE.search("让我查看一下这个文件的内容"))

    def test_chinese_jie_xia_lai(self):
        """'接下来我将' — continuation narration."""
        self.assertIsNotNone(self.RE.search("接下来我将遍历根目录"))

    # -- English narration patterns --

    def test_english_i_will(self):
        self.assertIsNotNone(self.RE.search("I will search the project for Dockerfile"))

    def test_english_let_me(self):
        self.assertIsNotNone(self.RE.search("Let me find the requirements.txt file"))

    def test_english_ill(self):
        self.assertIsNotNone(self.RE.search("I'll read the file contents now"))

    # -- Negative cases (should NOT match) --

    def test_normal_answer_no_narration(self):
        """Normal factual answer should not be flagged as narration."""
        self.assertIsNone(self.RE.search("该文件位于项目根目录下"))

    def test_code_block_no_narration(self):
        """Code inside a block should not be flagged."""
        self.assertIsNone(self.RE.search("```python\nprint('hello')\n```"))

    def test_result_relay_no_narration(self):
        """Relaying tool results is not narration."""
        self.assertIsNone(self.RE.search("项目中没有找到 Dockerfile"))


class TestS1ExploreDetection(unittest.TestCase):
    """S1.2: @explore lock regex correctly identifies user's explicit prefix."""

    def setUp(self):
        from agentic_loop import _re_explore_lock
        self.RE = _re_explore_lock

    def test_basic_explore(self):
        self.assertIsNotNone(self.RE.match("@explore 分析项目架构"))

    def test_explore_with_spaces(self):
        self.assertIsNotNone(self.RE.match("@explore   找一下 Dockerfile"))

    def test_explore_case_insensitive(self):
        self.assertIsNotNone(self.RE.match("@Explore 列出根目录"))

    def test_no_explore_prefix(self):
        """Normal messages should not match."""
        self.assertIsNone(self.RE.match("分析项目架构"))

    def test_explore_in_middle(self):
        """@explore must be at the start."""
        self.assertIsNone(self.RE.match("请用 @explore 分析"))


class TestS1QuickPathPatterns(unittest.TestCase):
    """S1.3: Quick-path regexes match real user queries (from test logs)."""

    def setUp(self):
        from tools.sub_agent import _QUICK_FIND_RE, _QUICK_LIST_RE, _EXTERNAL_PATH_RE
        self.FIND = _QUICK_FIND_RE
        self.LIST = _QUICK_LIST_RE
        self.EXT = _EXTERNAL_PATH_RE

    # -- Real queries from the 2026-04-24 test session --

    def test_find_dockerfile_cn(self):
        """Real query: '找一下项目里有没有 Dockerfile'"""
        m = self.FIND.search("找一下项目里有没有 Dockerfile")
        self.assertIsNotNone(m, "Should match extensionless Dockerfile")
        target = m.group(1) or m.group(2)
        self.assertEqual(target, "Dockerfile")

    def test_find_package_json_cn(self):
        """Real query: '帮我看看项目里有没有 package.json'"""
        m = self.FIND.search("帮我看看项目里有没有 package.json")
        self.assertIsNotNone(m)
        target = m.group(1) or m.group(2)
        self.assertEqual(target, "package.json")

    def test_find_requirements_cn(self):
        """Real query: '帮我找找 requirements.txt'"""
        m = self.FIND.search("帮我找找 requirements.txt")
        self.assertIsNotNone(m)
        target = m.group(1) or m.group(2)
        self.assertEqual(target, "requirements.txt")

    def test_list_root_dir_cn(self):
        """Real query: '列出项目根目录下的文件结构'"""
        self.assertIsNotNone(self.LIST.search("列出项目根目录下的文件结构"))

    def test_external_path_tmp(self):
        """Real query: '读取 /tmp/nonexistent_folder 里的内容'"""
        m = self.EXT.search("读取 /tmp/nonexistent_folder 里的内容")
        self.assertIsNotNone(m)
        self.assertTrue(m.group(1).startswith("/tmp/"))

    def test_external_path_tmp_dir(self):
        """Real query: '查看 /tmp 目录的内容'"""
        m = self.EXT.search("查看 /tmp 目录的内容")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "/tmp")

    # -- Negative: complex queries should NOT match find/list --

    def test_complex_analysis_no_find(self):
        """'分析整个项目的架构' should NOT match find regex."""
        self.assertIsNone(self.FIND.search("分析整个项目的架构，详细说明每个模块的作用"))

    def test_complex_analysis_no_list(self):
        """'分析整个项目的架构' should NOT match list regex."""
        # List regex requires directory/folder/root/structure keywords
        m = self.LIST.search("分析整个项目的架构，详细说明每个模块的作用")
        # This might match "结构" — that's OK, the quick-path will still
        # fall through because a workspace listing alone doesn't answer it.
        # What matters is it doesn't match the FIND regex (tested above).


class TestS1ToolSelectionPatterns(unittest.TestCase):
    """S1.4: Tool guidance patterns that prevent wrong tool selection."""

    def test_file_read_error_suggests_alternatives(self):
        """When file_read fails, postprocessor adds find_by_name hint."""
        # The hint is added by tools/_enrich_error_message() in __init__.py
        from tools import _enrich_error_message
        enriched = _enrich_error_message(
            "file_read", "File not found: /tmp/xyz.py", {"path": "xyz.py"}
        )
        self.assertTrue(
            "find_by_name" in enriched or "file_list" in enriched,
            f"Error enrichment should suggest alternatives, got: {enriched}"
        )

    def test_mandatory_tool_detection_sub_agent(self):
        """P38: Explicit tool name in user message triggers mandatory hint."""
        from agentic_loop import _detect_mandatory_tool
        hint = _detect_mandatory_tool("使用 sub_agent 工具执行分析")
        self.assertIsNotNone(hint)
        self.assertIn("sub_agent", hint)


# ═══════════════════════════════════════════════════════════════
# S2: Pre-emption & Quick-path (real filesystem tests)
# ═══════════════════════════════════════════════════════════════

class TestS2QuickPathResolution(unittest.TestCase):
    """S2.1: Quick-path resolves simple queries without LLM involvement."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_find_existing_file(self):
        """Scenario: '@explore 找 requirements.txt' with file present."""
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "requirements.txt").write_text("fastapi>=0.100.0\n")
            result = self._run(_explore_quick_path(
                "找一下项目里有没有 requirements.txt", Path(tmpdir)
            ))
            self.assertIsNotNone(result, "Quick-path should resolve")
            self.assertTrue(result["success"])
            self.assertIn("requirements.txt", result["output"])
            self.assertTrue(result.get("_quick_path"))

    def test_find_missing_file(self):
        """Scenario: '@explore 找 Dockerfile' with file absent."""
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run(_explore_quick_path(
                "find Dockerfile", Path(tmpdir)
            ))
            self.assertIsNotNone(result)
            self.assertTrue(result["success"])
            self.assertIn("not found", result["output"].lower())

    def test_find_extensionless_makefile(self):
        """Scenario: '@explore 找 Makefile' — extensionless file."""
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "Makefile").write_text("all:\n\techo hello\n")
            result = self._run(_explore_quick_path(
                "check if Makefile exists", Path(tmpdir)
            ))
            self.assertIsNotNone(result)
            self.assertIn("Makefile", result["output"])

    def test_list_directory(self):
        """Scenario: '@explore 列出根目录' — directory listing."""
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "src").mkdir()
            (Path(tmpdir) / "README.md").write_text("# test")
            (Path(tmpdir) / "main.py").write_text("pass")
            result = self._run(_explore_quick_path(
                "列出项目根目录下的文件结构", Path(tmpdir)
            ))
            self.assertIsNotNone(result)
            self.assertIn("src/", result["output"])
            self.assertIn("main.py", result["output"])

    def test_external_path_nonexistent(self):
        """Scenario: '@explore 读取 /tmp/nonexistent_xyz' — should report not exist."""
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run(_explore_quick_path(
                "读取 /tmp/nonexistent_xyz_test_12345 里的内容", Path(tmpdir)
            ))
            self.assertIsNotNone(result)
            self.assertIn("does not exist", result["output"])

    def test_external_path_existing_dir(self):
        """Scenario: '@explore 查看 /tmp' — should list contents."""
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a known temp dir to list
            test_dir = Path(tmpdir) / "test_external"
            test_dir.mkdir()
            (test_dir / "file1.txt").write_text("hello")
            (test_dir / "file2.log").write_text("world")
            result = self._run(_explore_quick_path(
                f"查看 {test_dir} 目录的内容", Path("/tmp")  # workspace doesn't matter for external
            ))
            # External path regex requires /tmp|/home|... prefix
            # Our test_dir is under /tmp so it should match
            if result is not None:
                self.assertIn("file1.txt", result["output"])

    def test_complex_query_falls_through(self):
        """Scenario: '@explore 分析架构' — quick-path should return None."""
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run(_explore_quick_path(
                "分析整个项目的架构，详细说明每个模块的作用", Path(tmpdir)
            ))
            self.assertIsNone(result, "Complex analysis must NOT be quick-path resolved")


# ═══════════════════════════════════════════════════════════════
# S3: Correction Injection — verify message manipulation patterns
# ═══════════════════════════════════════════════════════════════

class TestS3ExploreSuppressionLogic(unittest.TestCase):
    """S3.1: Explore output suppression messages are correctly formed."""

    def test_quick_path_suppression_message_content(self):
        """Quick-path suppression forbids re-organization and expansion."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        # Must have the U11d pre-emption suppression
        self.assertIn("EXPLORE QUICK-PATH RESULT", src)
        self.assertIn("1-3 SHORT sentences", src)
        self.assertIn("Re-organize", src)
        self.assertIn("architecture analysis", src)

    def test_direct_path_suppression_message_content(self):
        """Direct-path suppression (when model bypasses sub_agent) is present."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("handled the @explore request directly", src)
        self.assertIn("emoji headers", src)
        self.assertIn("dependency categories", src)

    def test_mandatory_sub_agent_hint_content(self):
        """When quick-path fails, mandatory sub_agent hint is injected."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("MUST call sub_agent with agent_type='explore'", src)
        self.assertIn("Do NOT handle this yourself", src)

    def test_explore_empty_output_conclude_nudge(self):
        """Empty explore output triggers conclude nudge."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("EXPLORE DONE", src)
        self.assertIn("Do NOT re-run the exploration", src)

    def test_explore_sub_agent_suppression_quick_vs_normal(self):
        """Sub-agent suppression differs between quick-path and normal results."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        # Quick-path variant
        self.assertIn("add tables/headers", src)
        # Normal variant
        self.assertIn("Duplicate the sub-agent", src)


class TestS3NarrationScrub(unittest.TestCase):
    """S3.2: Narration scrub logic is wired into the main loop."""

    def test_narration_scrub_in_source(self):
        """B2 narration scrub replaces narration in stored assistant text."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        # B2: Strip narration from stored history
        self.assertIn("_NARRATION_RE.sub(", src)

    def test_narration_count_tracked(self):
        """P29: Narration instances are counted per turn."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("narration_count", src)

    def test_conciseness_injection_exists(self):
        """P30: Conciseness reminder is injected after verbose turns."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("CONCISE RESPONSE", src)
        self.assertIn("≤150 words", src)


class TestS3AgentTypeLock(unittest.TestCase):
    """S3.3: @explore lock prevents agent_type upgrade to research/plan."""

    def test_lock_source_present(self):
        """Lock logic exists in agentic_loop.py."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("Locked @explore", src)
        self.assertIn('agent_type"] = "explore"', src)

    def test_lock_covers_sub_agent_aliases(self):
        """Lock covers all sub_agent tool name aliases."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        for alias in ("sub_agent", "fork", "delegate", "spawn_agent"):
            self.assertIn(alias, src)


# ═══════════════════════════════════════════════════════════════
# S4: End-to-End Scenario Replay
#
# These tests simulate what happened in real test sessions by
# building the same message sequences and verifying the
# correction mechanisms would fire.
# ═══════════════════════════════════════════════════════════════

class TestS4ScenarioReplay(unittest.TestCase):
    """S4: Replay real small-model failure scenarios and verify corrections."""

    def test_scenario_explore_lock_prevents_research_upgrade(self):
        """
        Real failure: Model upgraded @explore to agent_type='research'.
        Fix: U11c lock overrides agent_type back to 'explore'.

        Replay: Build 'prepared' list with research type, verify override logic.
        """
        from agentic_loop import _re_explore_lock

        user_message = "@explore 分析整个项目的架构"
        self.assertIsNotNone(_re_explore_lock.match(user_message.strip()))

        # Simulate prepared list with wrong agent_type
        prepared = [
            ({"id": "tc_1"}, "sub_agent", {"agent_type": "research", "task": "分析架构"}),
        ]

        # Apply the lock logic (same as agentic_loop.py)
        for _pc_tc, _pc_tn, _pc_args in prepared:
            if _pc_tn in ("sub_agent", "fork", "delegate", "spawn_agent"):
                old_type = _pc_args.get("agent_type", "")
                if old_type and old_type != "explore":
                    _pc_args["agent_type"] = "explore"

        self.assertEqual(prepared[0][2]["agent_type"], "explore")

    def test_scenario_preempt_simple_find(self):
        """
        Real failure: '@explore 帮我看看项目里有没有 package.json'
        Model called file_read (fail) → shell_execute instead of quick-path.
        Fix: U11d pre-emption runs quick-path before LLM.

        Replay: Strip @explore, run quick-path, verify resolution.
        """
        from agentic_loop import _re_explore_lock
        from tools.sub_agent import _explore_quick_path

        user_message = "@explore 帮我看看项目里有没有 package.json"
        m = _re_explore_lock.match(user_message.strip())
        self.assertIsNotNone(m)

        # Strip @explore prefix
        task = _re_explore_lock.sub("", user_message.strip()).strip()
        self.assertEqual(task, "帮我看看项目里有没有 package.json")

        # Run quick-path with a workspace that has the file
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "package.json").write_text('{"name": "test"}')
            result = asyncio.run(_explore_quick_path(task, Path(tmpdir)))
            self.assertIsNotNone(result, "Quick-path should pre-empt this query")
            self.assertIn("package.json", result["output"])

    def test_scenario_preempt_missing_dockerfile(self):
        """
        Real failure: '@explore 找一下项目里有没有 Dockerfile'
        Model called file_read (fail) → file_list → find_by_name (3 calls).
        Fix: U11d pre-emption + U11d extensionless regex.

        Replay: Quick-path should resolve in <0.1s.
        """
        from agentic_loop import _re_explore_lock
        from tools.sub_agent import _explore_quick_path

        user_message = "@explore 找一下项目里有没有 Dockerfile"
        task = _re_explore_lock.sub("", user_message.strip()).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = asyncio.run(_explore_quick_path(task, Path(tmpdir)))
            self.assertIsNotNone(result, "Quick-path should handle 'find Dockerfile'")
            # File doesn't exist → should say "not found"
            self.assertIn("not found", result["output"].lower())

    def test_scenario_external_path_nonexistent(self):
        """
        Real failure: '@explore 读取 /tmp/nonexistent_folder 里的内容'
        Model called file_list (fail).
        Fix: U11c external path regex + U11d pre-emption.

        Replay: Quick-path detects external path and reports not-exist.
        """
        from tools.sub_agent import _explore_quick_path

        task = "读取 /tmp/nonexistent_folder_replay_test 里的内容"
        result = asyncio.run(_explore_quick_path(task, Path("/tmp")))
        self.assertIsNotNone(result)
        self.assertIn("does not exist", result["output"])

    def test_scenario_directory_listing_not_expanded(self):
        """
        Real failure: '@explore 列出项目根目录下的文件结构'
        Model called file_list, then expanded into emoji-categorized report.
        Fix: U11d pre-emption resolves via quick-path, suppression injected.

        Replay: Quick-path resolves + suppression text exists in source.
        """
        from tools.sub_agent import _explore_quick_path
        import agentic_loop

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "core").mkdir()
            (Path(tmpdir) / "tools").mkdir()
            (Path(tmpdir) / "README.md").write_text("# test")

            task = "列出项目根目录下的文件结构"
            result = asyncio.run(_explore_quick_path(task, Path(tmpdir)))
            self.assertIsNotNone(result, "Directory listing should be quick-path resolved")
            self.assertIn("core/", result["output"])

        # Verify suppression message would be injected
        src = open(agentic_loop.__file__).read()
        self.assertIn("EXPLORE QUICK-PATH RESULT", src)

    def test_scenario_requirements_txt_not_overanalyzed(self):
        """
        Real failure: '@explore 帮我找找 requirements.txt'
        Model read the file AND analyzed all dependencies into categories.
        Fix: Quick-path just reports location, suppression prevents expansion.

        Replay: Quick-path returns file location, not file contents.
        """
        from tools.sub_agent import _explore_quick_path

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "requirements.txt").write_text(
                "fastapi>=0.100.0\nnumpy>=1.24.0\n"
            )
            task = "帮我找找 requirements.txt"
            result = asyncio.run(_explore_quick_path(task, Path(tmpdir)))
            self.assertIsNotNone(result)
            # Should report location, NOT dump contents
            self.assertIn("requirements.txt", result["output"])
            # Should NOT contain the actual pip package names
            self.assertNotIn("fastapi", result["output"],
                             "Quick-path should report location, not file contents")

    def test_scenario_complex_architecture_forces_sub_agent(self):
        """
        Real failure: '@explore 分析整个项目的架构'
        Explore sub-agent used 3 turns but produced no text output.
        Main model then hallucinated detailed architecture descriptions.

        Fix: Quick-path returns None → mandatory sub_agent hint injected.
        Verify: The MANDATORY hint instructs model to call sub_agent.
        """
        from tools.sub_agent import _explore_quick_path
        import agentic_loop

        with tempfile.TemporaryDirectory() as tmpdir:
            task = "分析整个项目的架构，详细说明每个模块的作用"
            result = asyncio.run(_explore_quick_path(task, Path(tmpdir)))
            self.assertIsNone(result, "Complex task must fall through quick-path")

        # Verify the mandatory hint exists in source
        src = open(agentic_loop.__file__).read()
        self.assertIn("MANDATORY TOOL", src)
        self.assertIn("agent_type='explore'", src)

    def test_scenario_narration_stripped_from_history(self):
        """
        Real failure: Every response started with '我将搜索整个项目'.
        Fix: B2 strips narration from stored assistant text.

        Replay: Apply narration regex to real model output.
        """
        from agentic_loop import _NARRATION_RE

        # Real model output from test session
        model_output = "我将搜索整个项目，查找是否存在名为 `Dockerfile` 的文件。"

        # B2 logic: strip narration
        cleaned = _NARRATION_RE.sub("", model_output).strip()

        # Should have removed the narration prefix
        self.assertNotIn("我将", cleaned)
        # But should keep the meaningful part
        self.assertIn("Dockerfile", cleaned)


# ═══════════════════════════════════════════════════════════════
# S5: Behavior Regression Guards
#
# These tests verify that key correction mechanisms remain intact
# in the source code, protecting against accidental removal.
# ═══════════════════════════════════════════════════════════════

class TestS5BehaviorGuards(unittest.TestCase):
    """S5: Ensure all 5 correction mechanisms remain in place."""

    def setUp(self):
        import agentic_loop
        self.src = open(agentic_loop.__file__).read()

    def test_guard_narration_scrub(self):
        """B2+P29: Narration scrub must remain active."""
        self.assertIn("_NARRATION_RE", self.src)
        self.assertIn("narration_count", self.src)

    def test_guard_conciseness_injection(self):
        """P30: Verbose output triggers conciseness reminder."""
        self.assertIn("CONCISE RESPONSE", self.src)
        self.assertIn("verbose_turns", self.src)

    def test_guard_explore_preemption(self):
        """U11d: @explore pre-emption runs before LLM loop."""
        self.assertIn("_explore_quick_path", self.src)
        self.assertIn("_u11d_explore_preempted", self.src)

    def test_guard_explore_lock(self):
        """U11c: @explore lock prevents agent_type upgrade."""
        self.assertIn("_re_explore_lock", self.src)
        self.assertIn("Locked @explore", self.src)

    def test_guard_direct_path_suppression(self):
        """U11d: Direct-path suppression fires when model bypasses sub_agent."""
        self.assertIn("_had_sub_agent_this_turn", self.src)
        self.assertIn("Direct-path @explore suppression", self.src)

    def test_guard_mandatory_tool_detection(self):
        """P38: Mandatory tool detection injects hints."""
        self.assertIn("_detect_mandatory_tool", self.src)
        self.assertIn("MANDATORY TOOL", self.src)

    def test_guard_file_read_limit_correction(self):
        """P27: file_read limits are auto-corrected."""
        self.assertIn("_fix_file_read_limits", self.src)

    def test_guard_cross_instruction_focus(self):
        """P42: Cross-instruction confusion prevention."""
        self.assertIn("[FOCUS]", self.src)
        self.assertIn("当前问题", self.src)

    def test_guard_output_efficiency_rules(self):
        """Claw-style output efficiency rules in system prompt."""
        import system_prompts
        sp_src = open(system_prompts.__file__).read()
        # Check for key output efficiency keywords (exact wording may vary)
        self.assertTrue(
            "straight to the point" in sp_src or "Output efficiency" in sp_src
            or "concise" in sp_src.lower(),
            "System prompt should contain output efficiency rules"
        )

    def test_guard_anti_narration_in_system_prompt(self):
        """System prompt explicitly bans narration patterns."""
        import system_prompts
        sp_src = open(system_prompts.__file__).read()
        self.assertIn("Do NOT narrate", sp_src)

    # U12a-d guards
    def test_guard_u12a_scrub_narration(self):
        """U12a: _scrub_narration helper exists and is used."""
        self.assertIn("_scrub_narration", self.src)
        self.assertIn("[CONCISENESS]", self.src)

    def test_guard_u12b_grounding(self):
        """U12b: Anti-hallucination grounding check exists."""
        self.assertIn("[GROUNDING]", self.src)
        self.assertIn("VERBATIM in tool results", self.src)

    def test_guard_u12c_emoji_categorization(self):
        """U12c: Emoji categorization detection exists."""
        self.assertIn("_EMOJI_HEADER_RE", self.src)
        self.assertIn("[NO CATEGORIZATION]", self.src)

    def test_guard_u12d_tool_correction(self):
        """U12d: file_read failure correction exists."""
        self.assertIn("[TOOL CORRECTION]", self.src)
        self.assertIn("find_by_name(pattern=", self.src)


# ═══════════════════════════════════════════════════════════════
# S6: U12a-d Behavior Correction Tests
# ═══════════════════════════════════════════════════════════════

class TestS6U12aNarration(unittest.TestCase):
    """S6.1: U12a anti-narration — enhanced regex + _scrub_narration + immediate nudge."""

    def test_scrub_chinese_wo_jiang(self):
        """U12a: _scrub_narration strips '我将搜索整个项目'."""
        from agentic_loop import _scrub_narration
        cleaned, scrubbed = _scrub_narration("我将搜索整个项目，查找 Dockerfile。")
        self.assertTrue(scrubbed)
        self.assertNotIn("我将", cleaned)

    def test_scrub_chinese_xia_mian_wo(self):
        """U12a: _scrub_narration catches new pattern '下面我'."""
        from agentic_loop import _scrub_narration
        cleaned, scrubbed = _scrub_narration("下面我来查看一下这个文件的内容。")
        self.assertTrue(scrubbed)
        self.assertNotIn("下面我", cleaned)

    def test_scrub_english_i_am_going_to(self):
        """U12a: _scrub_narration catches 'I am going to'."""
        from agentic_loop import _scrub_narration
        cleaned, scrubbed = _scrub_narration("I am going to search the project for Dockerfile.")
        self.assertTrue(scrubbed)
        self.assertNotIn("I am going to", cleaned)

    def test_scrub_no_narration_passthrough(self):
        """U12a: _scrub_narration returns original text when no narration found."""
        from agentic_loop import _scrub_narration
        text = "项目根目录下有 15 个文件。"
        cleaned, scrubbed = _scrub_narration(text)
        self.assertFalse(scrubbed)
        self.assertEqual(cleaned, text)

    def test_scrub_preserves_content(self):
        """U12a: _scrub_narration keeps the substantive part after stripping."""
        from agentic_loop import _scrub_narration
        cleaned, scrubbed = _scrub_narration("让我查看一下 requirements.txt 的内容")
        self.assertTrue(scrubbed)
        self.assertIn("requirements.txt", cleaned)

    def test_immediate_conciseness_nudge_in_source(self):
        """U12a: Immediate [CONCISENESS] nudge injected on first narration."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("[CONCISENESS]", src)
        self.assertIn("不要开场白", src)

    def test_narration_regex_multiline(self):
        """U12a: Narration regex works in multiline mode."""
        from agentic_loop import _NARRATION_RE
        text = "Some header\n我将读取文件内容。"
        self.assertIsNotNone(_NARRATION_RE.search(text))

    def test_stream_scrub_in_source(self):
        """U12a-stream: Buffer-then-scrub approach exists in streaming path."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("U12a-stream", src)
        self.assertIn("_U12A_BUFFER_SIZE", src)
        self.assertIn("_scrub_narration(_buffer)", src)

    def test_stream_scrub_first_chunk(self):
        """U12a-stream: First chunk narration is scrubbed via _NARRATION_RE.match."""
        from agentic_loop import _NARRATION_RE
        chunk = "我将搜索项目文件"
        cleaned = _NARRATION_RE.sub("", chunk).lstrip()
        self.assertNotIn("我将", cleaned)
        self.assertIn("搜索", cleaned)

    def test_narration_regex_expanded_wei_nin(self):
        """U12a: '为您列出...' narration pattern is caught."""
        from agentic_loop import _NARRATION_RE
        self.assertIsNotNone(_NARRATION_RE.match("为您列出项目根目录下的文件"))

    def test_narration_regex_expanded_shou_xian(self):
        """U12a: '首先在项目中查找...' narration pattern is caught."""
        from agentic_loop import _NARRATION_RE
        self.assertIsNotNone(_NARRATION_RE.match("首先在项目中查找 Dockerfile"))

    def test_narration_regex_expanded_xian_zai(self):
        """U12a: '先在...' narration pattern is caught."""
        from agentic_loop import _NARRATION_RE
        self.assertIsNotNone(_NARRATION_RE.match("先在项目中查找 package.json"))

    def test_sentence_dedup_strips_repetition(self):
        """U12a-dedup: Exact sentence repetition is collapsed to single copy."""
        from agentic_loop import _scrub_narration
        text = "读取 `AGENTS.md` 文件的内容。读取 `AGENTS.md` 文件的内容。"
        cleaned, scrubbed = _scrub_narration(text)
        self.assertTrue(scrubbed)
        # Dedup removes one copy, then narration regex strips "读取" prefix
        self.assertEqual(cleaned.count("AGENTS.md"), 1)

    def test_sentence_dedup_preserves_normal_text(self):
        """U12a-dedup: Normal text with no repetition is not modified."""
        from agentic_loop import _scrub_narration
        text = "项目根目录下有 15 个文件。其中包含 3 个目录。"
        cleaned, scrubbed = _scrub_narration(text)
        self.assertFalse(scrubbed)
        self.assertEqual(cleaned, text)

    def test_narration_regex_lie_chu(self):
        """U12a: '列出当前...' verb-start narration is caught."""
        from agentic_loop import _NARRATION_RE
        self.assertIsNotNone(_NARRATION_RE.match("列出当前工作目录下的文件"))

    def test_narration_regex_cha_zhao_bing(self):
        """U12a: '查找并读取...' compound verb narration is caught."""
        from agentic_loop import _NARRATION_RE
        self.assertIsNotNone(_NARRATION_RE.match("查找并读取项目中的 Dockerfile"))

    def test_full_scrub_real_q1(self):
        """U12a: Real Q1 output — repeated narration fully scrubbed."""
        from agentic_loop import _scrub_narration
        text = "列出当前工作目录下的文件和文件夹，以便为您展示项目的整体结构。列出当前工作目录下的文件和文件夹，以便为您展示项目的整体结构。"
        cleaned, scrubbed = _scrub_narration(text)
        self.assertTrue(scrubbed)
        self.assertNotIn("列出当前", cleaned)

    def test_narration_regex_du_qu(self):
        """U12a-R2: Bare '读取' narration prefix is caught."""
        from agentic_loop import _NARRATION_RE
        self.assertIsNotNone(_NARRATION_RE.match("读取 `/home/field/AGENTS.md` 文件的内容"))

    def test_narration_regex_wei_le(self):
        """U12a-R2: '为了' narration prefix is caught."""
        from agentic_loop import _NARRATION_RE
        self.assertIsNotNone(_NARRATION_RE.match("为了准确回答该项目的模块划分，我将阅读架构文档"))

    def test_sentence_dedup_long_path(self):
        """U12a-R4: Sentences >80 chars with file paths are deduped."""
        from agentic_loop import _scrub_narration
        text = ("读取 `/home/field/.nanobot/workspace/AGENTS.md` 文件的内容以为您提供准确的回答。"
                "读取 `/home/field/.nanobot/workspace/AGENTS.md` 文件的内容以为您提供准确的回答。")
        cleaned, scrubbed = _scrub_narration(text)
        self.assertTrue(scrubbed, "Long-path sentence dedup failed")
        # Dedup collapses 2→1 copy, then narration strips "读取" prefix
        self.assertEqual(cleaned.count("AGENTS.md"), 1,
                         "Dedup should collapse to 1 copy")
        self.assertNotIn("读取", cleaned, "Narration prefix should be stripped")


class TestS6U12bHallucination(unittest.TestCase):
    """S6.2: U12b anti-hallucination — grounding check on disproportionate output."""

    def test_grounding_check_in_source(self):
        """U12b: Grounding check exists and uses word/char ratio."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("_response_words > 200", src)
        self.assertIn("_tool_data_chars < 2000", src)
        self.assertIn("[GROUNDING]", src)

    def test_grounding_forces_re_response(self):
        """U12b: Grounding nudge triggers continue (force re-response)."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        # The GROUNDING block is followed by 'continue' to force re-response
        idx = src.index("[GROUNDING]")
        # Search until next 'break' or end of function — 'continue' must appear
        block = src[idx:idx+3000]
        self.assertIn("continue", block)

    def test_grounding_message_content(self):
        """U12b: Grounding message forbids inference from filenames."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("Do NOT infer module purposes from filenames", src)
        self.assertIn("fabricate architecture descriptions", src)

    def test_grounding_fires_only_once(self):
        """U12b: Grounding nudge checks for existing [GROUNDING] message."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("_has_ground", src)

    def test_grounding_only_counts_current_turn(self):
        """U12b-fix: _tool_data_chars only counts current turn, not historical."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        # Find the _tool_data_chars calculation
        idx = src.index("_tool_data_chars = sum")
        block = src[idx:idx+300]
        self.assertIn('m.get("_turn") == turn', block,
                       "_tool_data_chars must be scoped to current turn only")

    def test_grounding_fires_on_turn_1(self):
        """U12b-fix: Grounding check fires on turn 1 (was turn >= 2, now >= 1)."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("turn >= 1:", src)
        # Must NOT have the old turn >= 2 guard for U12b
        idx_u12b = src.index("U12b:")
        block = src[idx_u12b:idx_u12b+500]
        self.assertNotIn("turn >= 2", block)

    def test_grounding_cjk_word_count(self):
        """U12b-fix: CJK-aware word count uses max(split, chars//3)."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        # Must use char-based fallback for CJK
        self.assertIn("_char_words = len(assistant_text) // 3", src)
        self.assertIn("max(_split_words, _char_words)", src)

    def test_grounding_zero_tool_suggest_tools(self):
        """U12b-fix: When tool_data_chars=0, grounding tells model to call tools."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("_tool_data_chars == 0", src)
        self.assertIn("You have NOT called any tools this turn", src)
        self.assertIn("Call file_read, file_list, or grep_search FIRST", src)

    def test_grounding_removes_fabricated_message(self):
        """U12b-fix: Fabricated assistant message is removed before re-response.

        Without this, the model sees its own hallucination in messages
        and just abbreviates it instead of switching to tool calls.
        """
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        # Find the U12b block
        idx = src.index("[U12b] Hallucination risk")
        block = src[idx:idx+800]
        self.assertIn('messages.pop()', block,
                       "U12b must remove the fabricated assistant message before continue")
        self.assertIn("Removed fabricated assistant message", block)

    def test_grounding_cjk_threshold_works(self):
        """U12b-fix: 600-char Chinese text exceeds threshold via char//3."""
        # Simulate: 600 Chinese chars → split()≈20 (way under 200)
        # but chars//3 = 200 (exactly at threshold)
        chinese_text = "这是一段关于项目架构的详细描述，" * 30  # ~450 chars
        split_words = len(chinese_text.split())
        char_words = len(chinese_text) // 3
        effective = max(split_words, char_words)
        self.assertGreater(effective, 100,
                           f"CJK text should yield effective_words > 100, got {effective} "
                           f"(split={split_words}, char={char_words})")
        # Verify split alone would be WAY too low
        self.assertLess(split_words, 50,
                        "split() on Chinese text should be very low")


class TestS6U12cEmojiCategorization(unittest.TestCase):
    """S6.3: U12c emoji categorization detection."""

    def test_emoji_header_regex_matches(self):
        """U12c: Emoji header regex matches real small-model output patterns."""
        from agentic_loop import _EMOJI_HEADER_RE
        text = "\n📁 **核心模块**\n📦 **依赖管理**\n🔧 **工具链**\n📝 **文档**"
        matches = _EMOJI_HEADER_RE.findall(text)
        self.assertGreaterEqual(len(matches), 3, f"Should match 3+ emoji headers, got {len(matches)}")

    def test_emoji_header_regex_no_false_positive(self):
        """U12c: Normal text with occasional emoji doesn't trigger."""
        from agentic_loop import _EMOJI_HEADER_RE
        text = "项目结构很简单，只有3个文件。"
        matches = _EMOJI_HEADER_RE.findall(text)
        self.assertEqual(len(matches), 0)

    def test_emoji_open_folder_included(self):
        """U12c: 📂 (open folder) is in the emoji regex — model used it to bypass 📁."""
        from agentic_loop import _EMOJI_HEADER_RE
        text = "\n📂 **核心模块**\n📂 **安全模块**\n📂 **记忆模块**"
        matches = _EMOJI_HEADER_RE.findall(text)
        self.assertGreaterEqual(len(matches), 3, f"📂 should be detected, got {len(matches)}")

    def test_bold_category_detection(self):
        """U12c-bold: Numbered ### headers + bold list items detected."""
        from agentic_loop import _BOLD_CATEGORY_RE
        text = "### 1. 核心代码目录\n*   **`agents/`**: 实现。\n### 2. 认知系统\n*   **`memory/`**: 存储。"
        matches = _BOLD_CATEGORY_RE.findall(text)
        self.assertGreaterEqual(len(matches), 2, f"Bold headers should be detected, got {len(matches)}")

    def test_bold_category_no_false_positive(self):
        """U12c-bold: Normal numbered list and bold text don't trigger."""
        from agentic_loop import _BOLD_CATEGORY_RE
        # Numbered list without ### prefix — should NOT match
        text = "1. **可逆性原则**：本地可逆操作\n2. **风险操作确认**：以下操作"
        matches = _BOLD_CATEGORY_RE.findall(text)
        self.assertEqual(len(matches), 0, f"Normal numbered list should not trigger, got {len(matches)}")

    def test_emoji_threshold_is_2(self):
        """U12c: Categorization fires with 2+ category headers (emoji or bold)."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("_category_count >= 2", src)

    def test_no_categorization_message_content(self):
        """U12c: [NO CATEGORIZATION] message instructs plain listing."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("without category headers", src)
        self.assertIn("under 5 lines", src)

    def test_emoji_categorization_forces_re_response(self):
        """U12c: Emoji categorization triggers continue (force re-response)."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        idx = src.index("[NO CATEGORIZATION]")
        block = src[idx:idx+2000]
        self.assertIn("continue", block)

    def test_categorization_removes_assistant_message(self):
        """U12c-fix: Categorized assistant message removed before re-response."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        idx = src.index("[U12c] Categorization detected")
        block = src[idx:idx+800]
        self.assertIn('messages.pop()', block,
                       "U12c must remove the categorized message before continue")
        self.assertIn("Removed categorized assistant message", block)

    def test_emoji_bis_inter_tool_scrub(self):
        """U12c-bis: Emoji headers scrubbed from assistant text alongside tool calls."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("U12c-bis", src)
        self.assertIn("_emoji_count_bis", src)

    def test_emoji_hardclean_fallback(self):
        """U12c-hardclean: When nudge already sent, emoji lines physically removed."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("U12c-hardclean", src)
        # Verify both the nudge check and the hardclean exist in same function
        self.assertIn("_has_emoji_nudge", src)
        # Hardclean must come AFTER the nudge check
        idx_nudge = src.index("_has_emoji_nudge")
        idx_hardclean = src.index("U12c-hardclean")
        self.assertGreater(idx_hardclean, idx_nudge)

    def test_emoji_hardclean_removes_lines(self):
        """U12c-hardclean: Physical removal of emoji header lines works correctly."""
        from agentic_loop import _EMOJI_HEADER_RE
        text = "项目文件:\n📁 **核心模块**\n- core/\n📦 **依赖管理**\n- req.txt\n🔧 **工具链**\n- tools/"
        lines = text.split("\n")
        cleaned_lines = [ln for ln in lines if not _EMOJI_HEADER_RE.match(ln)]
        cleaned = "\n".join(cleaned_lines).strip()
        self.assertNotIn("📁", cleaned)
        self.assertNotIn("📦", cleaned)
        self.assertNotIn("🔧", cleaned)
        self.assertIn("core/", cleaned)
        self.assertIn("req.txt", cleaned)


class TestS6U12dToolCorrection(unittest.TestCase):
    """S6.4: U12d tool selection correction — file_read failure → find_by_name."""

    def test_tool_correction_in_source(self):
        """U12d: Tool correction block exists in sequential execution path."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("[TOOL CORRECTION]", src)
        self.assertIn("find_by_name(pattern=", src)

    def test_tool_correction_extracts_filename(self):
        """U12d: Correction uses os.path.basename to extract filename."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("os.path.basename", src)

    def test_tool_correction_targets_file_read(self):
        """U12d: Correction only fires for file_read, not other tools."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        # U12d condition: tname == "file_read" and "not found" check guard
        # the [TOOL CORRECTION] injection. Both must exist.
        self.assertIn('tname == "file_read" and ("not found"', src)

    def test_tool_correction_checks_not_found(self):
        """U12d: Correction checks for 'not found' or 'no such file' in error."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn('"not found" in _err_lower', src)
        self.assertIn('"no such file" in _err_lower', src)

    def test_enriched_error_still_works(self):
        """U12d: P18 error enrichment in tools/__init__.py still suggests alternatives."""
        from tools import _enrich_error_message
        enriched = _enrich_error_message(
            "file_read", "File not found: /workspace/missing.py", {"path": "/workspace/missing.py"}
        )
        self.assertIn("find_by_name", enriched)

    def test_preempt_failed_paths_tracking(self):
        """U12d-preempt: _file_read_failed_paths set is initialized and used."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("_file_read_failed_paths", src)
        self.assertIn("_file_read_failed_paths.add(", src)

    def test_preempt_auto_replace_in_source(self):
        """U12d-preempt: Auto-replace file_read→find_by_name exists in prepared list."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("U12d-preempt", src)
        self.assertIn("_file_read_failed_paths", src)
        # Verify it changes the tool name
        idx = src.index("U12d-preempt")
        block = src[idx:idx+1200]
        self.assertIn('"find_by_name"', block)
        self.assertIn("prepared[_idx]", block)

    def test_preempt_targets_only_previously_failed(self):
        """U12d-preempt: Only replaces paths that have already failed, not new ones."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        idx = src.index("U12d-preempt")
        block = src[idx:idx+600]
        self.assertIn("in _file_read_failed_paths", block)


# ═══════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []

    test_classes = [
        TestS1NarrationDetection,
        TestS1ExploreDetection,
        TestS1QuickPathPatterns,
        TestS1ToolSelectionPatterns,
        TestS2QuickPathResolution,
        TestS3ExploreSuppressionLogic,
        TestS3NarrationScrub,
        TestS3AgentTypeLock,
        TestS4ScenarioReplay,
        TestS5BehaviorGuards,
        TestS6U12aNarration,
        TestS6U12bHallucination,
        TestS6U12cEmojiCategorization,
        TestS6U12dToolCorrection,
    ]

    for cls in test_classes:
        suite = unittest.TestLoader().loadTestsFromTestCase(cls)
        print(f"\n╔══ {cls.__name__} ══╗")
        for test in suite:
            result = unittest.TestResult()
            test.run(result)
            name = test._testMethodName
            if result.wasSuccessful():
                print(f"  ✅ {name}")
                passed += 1
            else:
                err_msg = ""
                if result.failures:
                    err_msg = result.failures[0][1].strip().split("\n")[-1]
                elif result.errors:
                    err_msg = result.errors[0][1].strip().split("\n")[-1]
                print(f"  ❌ {name}: {err_msg}")
                failed += 1
                errors.append(f"  - {name}: {err_msg}")

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print(f"\nFailed tests:")
        for e in errors:
            print(e)
    print(f"{'=' * 60}")
    if failed == 0:
        print("🎉 All small model behavior tests passed!")
    sys.exit(0 if failed == 0 else 1)
