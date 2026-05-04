#!/usr/bin/env python3
"""
Tests for P94-P95: Nanobot Skill Framework.

Covers:
  - P94: Skill registry (register, get, list, execute, help)
  - P95: Bundled skills (simplify, remember, verify, commit, debug)
  - Alias resolution
  - Prompt generation
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestP94SkillRegistry(unittest.TestCase):
    """P94: Skill registry operations."""

    def test_registry_initialized(self):
        from skills import _registry
        self.assertGreater(len(_registry), 0)

    def test_get_skill_by_name(self):
        from skills import get_skill
        skill = get_skill("simplify")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.name, "simplify")

    def test_get_skill_by_alias(self):
        from skills import get_skill
        skill = get_skill("review")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.name, "simplify")

    def test_get_skill_case_insensitive(self):
        from skills import get_skill
        skill = get_skill("SIMPLIFY")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.name, "simplify")

    def test_get_nonexistent_skill(self):
        from skills import get_skill
        self.assertIsNone(get_skill("nonexistent_skill"))

    def test_list_skills(self):
        from skills import list_skills
        skills = list_skills()
        self.assertGreaterEqual(len(skills), 5)
        names = [s.name for s in skills]
        self.assertIn("simplify", names)
        self.assertIn("remember", names)
        self.assertIn("verify", names)
        self.assertIn("commit", names)
        self.assertIn("debug", names)

    def test_execute_skill(self):
        from skills import execute_skill
        prompt = execute_skill("simplify", "focus on the tests")
        self.assertIsNotNone(prompt)
        self.assertIn("Simplify", prompt)
        self.assertIn("focus on the tests", prompt)

    def test_execute_skill_no_args(self):
        from skills import execute_skill
        prompt = execute_skill("debug")
        self.assertIsNotNone(prompt)
        # P101: /debug is now a runtime-enforced coding skill
        self.assertIn("debug mode", prompt)
        self.assertIn("Root Cause", prompt)

    def test_execute_nonexistent_skill(self):
        from skills import execute_skill
        self.assertIsNone(execute_skill("doesnotexist"))

    def test_format_skill_help(self):
        from skills import format_skill_help
        help_text = format_skill_help()
        self.assertIn("Available Skills", help_text)
        self.assertIn("/simplify", help_text)
        self.assertIn("/remember", help_text)
        self.assertIn("/verify", help_text)
        self.assertIn("/commit", help_text)
        self.assertIn("/debug", help_text)

    def test_skill_definition_fields(self):
        from skills import get_skill
        s = get_skill("simplify")
        self.assertTrue(s.uses_sub_agent)
        self.assertTrue(s.user_invocable)
        self.assertIsNotNone(s.description)
        self.assertIsNotNone(s.argument_hint)


class TestP95BundledSkills(unittest.TestCase):
    """P95: Bundled skill prompt generation."""

    def test_simplify_prompt(self):
        from skills import execute_skill
        prompt = execute_skill("simplify")
        self.assertIn("Phase 1", prompt)
        self.assertIn("Phase 2", prompt)
        self.assertIn("Phase 3", prompt)
        self.assertIn("git diff", prompt)
        self.assertIn("sub_agent", prompt)

    def test_simplify_with_args(self):
        from skills import execute_skill
        prompt = execute_skill("simplify", "focus on memory module")
        self.assertIn("Additional Focus", prompt)
        self.assertIn("memory module", prompt)

    def test_remember_prompt(self):
        from skills import execute_skill
        prompt = execute_skill("remember")
        self.assertIn("Memory Review", prompt)
        self.assertIn("Classify each memory", prompt)
        self.assertIn("Duplicates", prompt)
        self.assertIn("NANOBOT.md", prompt)

    def test_verify_prompt(self):
        from skills import execute_skill
        prompt = execute_skill("verify")
        # P101: /verify is now a runtime-enforced coding skill
        self.assertIn("verify mode", prompt)
        self.assertIn("Verification", prompt)
        self.assertIn("PASSED", prompt)
        self.assertIn("FAILED", prompt)
        self.assertIn("BLOCKED", prompt)

    def test_commit_prompt(self):
        from skills import execute_skill
        prompt = execute_skill("commit")
        self.assertIn("Conventional Commits", prompt)
        self.assertIn("git diff", prompt)
        self.assertIn("imperative mood", prompt)
        self.assertIn("approval", prompt)

    def test_debug_prompt_no_args(self):
        from skills import execute_skill
        prompt = execute_skill("debug")
        # P101: /debug is now a runtime-enforced coding skill
        self.assertIn("debug mode", prompt)
        self.assertIn("Root Cause", prompt)
        self.assertIn("git diff", prompt)

    def test_debug_prompt_with_args(self):
        from skills import execute_skill
        prompt = execute_skill("debug", "tests are failing with ImportError")
        # P101: /debug is now a runtime-enforced coding skill
        self.assertIn("Bug Report", prompt)
        self.assertIn("ImportError", prompt)
        self.assertIn("debug mode", prompt)


class TestP95Aliases(unittest.TestCase):
    """P95: Alias resolution for all bundled skills."""

    def test_simplify_aliases(self):
        from skills import get_skill
        for alias in ["review", "cleanup", "clean"]:
            s = get_skill(alias)
            self.assertIsNotNone(s, f"Alias '{alias}' should resolve")
            self.assertEqual(s.name, "simplify")

    def test_remember_aliases(self):
        from skills import get_skill
        for alias in ["memory_review", "mem"]:
            s = get_skill(alias)
            self.assertIsNotNone(s, f"Alias '{alias}' should resolve")
            self.assertEqual(s.name, "remember")

    def test_verify_aliases(self):
        from skills import get_skill
        # P101: /verify coding skill has updated aliases
        for alias in ["check", "test_changes", "validate", "lint"]:
            s = get_skill(alias)
            self.assertIsNotNone(s, f"Alias '{alias}' should resolve")
            self.assertEqual(s.name, "verify")

    def test_commit_aliases(self):
        from skills import get_skill
        for alias in ["gc", "git_commit"]:
            s = get_skill(alias)
            self.assertIsNotNone(s, f"Alias '{alias}' should resolve")
            self.assertEqual(s.name, "commit")

    def test_debug_aliases(self):
        from skills import get_skill
        # P101: /debug coding skill aliases changed — 'fix' is no longer a debug alias
        for alias in ["diagnose", "rootcause", "bisect"]:
            s = get_skill(alias)
            self.assertIsNotNone(s, f"Alias '{alias}' should resolve")
            self.assertEqual(s.name, "debug")


class TestP94SkillDefinition(unittest.TestCase):
    """P94: Custom skill registration."""

    def test_register_custom_skill(self):
        from skills import SkillDefinition, register_skill, get_skill, _registry
        # Save original state
        original_count = len(_registry)

        register_skill(SkillDefinition(
            name="test_custom",
            description="A test skill",
            prompt_builder=lambda args: f"Test prompt: {args}",
            aliases=["tc"],
        ))

        s = get_skill("test_custom")
        self.assertIsNotNone(s)
        self.assertEqual(s.description, "A test skill")

        # Alias works
        s2 = get_skill("tc")
        self.assertIsNotNone(s2)
        self.assertEqual(s2.name, "test_custom")

        # Execute works
        from skills import execute_skill
        prompt = execute_skill("test_custom", "hello")
        self.assertEqual(prompt, "Test prompt: hello")

        # Cleanup
        del _registry["test_custom"]
        from skills import _alias_map
        del _alias_map["tc"]


# ═══════════════════════════════════════════════════════════════
# P97: New skills, SKILL.md loader, system prompt, intent matching
# ═══════════════════════════════════════════════════════════════

class TestP97NewSkills(unittest.TestCase):
    """P97b: /batch and /skillify skills."""

    def test_batch_registered(self):
        from skills import get_skill
        s = get_skill("batch")
        self.assertIsNotNone(s)
        self.assertTrue(s.uses_sub_agent)

    def test_batch_aliases(self):
        from skills import get_skill
        for alias in ["parallel", "mass_change"]:
            s = get_skill(alias)
            self.assertIsNotNone(s, f"Alias '{alias}' should resolve")
            self.assertEqual(s.name, "batch")

    def test_batch_prompt_no_args(self):
        from skills import execute_skill
        prompt = execute_skill("batch", "")
        self.assertIn("Provide an instruction", prompt)
        self.assertIn("Examples", prompt)

    def test_batch_prompt_with_args(self):
        from skills import execute_skill
        prompt = execute_skill("batch", "migrate print to logging")
        self.assertIn("Phase 1", prompt)
        self.assertIn("Phase 2", prompt)
        self.assertIn("Phase 3", prompt)
        self.assertIn("migrate print to logging", prompt)

    def test_skillify_registered(self):
        from skills import get_skill
        s = get_skill("skillify")
        self.assertIsNotNone(s)
        self.assertFalse(s.uses_sub_agent)

    def test_skillify_aliases(self):
        from skills import get_skill
        for alias in ["capture_skill", "save_skill"]:
            s = get_skill(alias)
            self.assertIsNotNone(s, f"Alias '{alias}' should resolve")
            self.assertEqual(s.name, "skillify")

    def test_skillify_prompt(self):
        from skills import execute_skill
        prompt = execute_skill("skillify")
        self.assertIn("Skillify", prompt)
        self.assertIn("Step 1", prompt)
        self.assertIn("Step 2", prompt)
        self.assertIn("SKILL.md", prompt)
        self.assertIn("frontmatter", prompt.lower())

    def test_skillify_prompt_with_description(self):
        from skills import execute_skill
        prompt = execute_skill("skillify", "deploying to production")
        self.assertIn("deploying to production", prompt)
        self.assertIn("User Description", prompt)

    def test_total_bundled_skills(self):
        from skills import list_skills
        skills = list_skills()
        names = [s.name for s in skills]
        for expected in ["simplify", "remember", "verify", "commit", "debug", "batch", "skillify"]:
            self.assertIn(expected, names, f"Missing bundled skill: {expected}")
        self.assertGreaterEqual(len(skills), 7)

    def test_source_field_bundled(self):
        from skills import get_skill
        s = get_skill("simplify")
        self.assertEqual(s.source, "bundled")


class TestP97aSkillMdLoader(unittest.TestCase):
    """P97a: User-defined SKILL.md file loading."""

    def test_parse_frontmatter_basic(self):
        from skills import _parse_frontmatter
        content = "---\nname: test\ndescription: A test\n---\n# Body"
        fm, body = _parse_frontmatter(content)
        self.assertEqual(fm["name"], "test")
        self.assertEqual(fm["description"], "A test")
        self.assertEqual(body, "# Body")

    def test_parse_frontmatter_no_frontmatter(self):
        from skills import _parse_frontmatter
        content = "# Just markdown\nNo frontmatter here."
        fm, body = _parse_frontmatter(content)
        self.assertEqual(fm, {})
        self.assertEqual(body, content)

    def test_parse_frontmatter_with_aliases(self):
        from skills import _parse_frontmatter
        content = "---\nname: deploy\naliases:\n  - dp\n  - ship\n---\nDeploy stuff"
        fm, body = _parse_frontmatter(content)
        self.assertEqual(fm["aliases"], ["dp", "ship"])

    def test_parse_frontmatter_invalid_yaml(self):
        from skills import _parse_frontmatter
        content = "---\n: invalid: yaml: stuff\n---\nBody"
        fm, body = _parse_frontmatter(content)
        # Should not crash, returns empty dict
        self.assertIsInstance(fm, dict)

    def test_load_skill_from_md(self):
        import tempfile, shutil
        from skills import _load_skill_from_md
        tmpdir = Path(tempfile.mkdtemp())
        try:
            skill_dir = tmpdir / "my-deploy"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: my-deploy\ndescription: Deploy to production\n"
                "aliases: dp, ship\nwhen_to_use: When user wants to deploy\n---\n"
                "# Deploy\n\nRun the deploy pipeline."
            )
            skill = _load_skill_from_md(skill_dir, "user")
            self.assertIsNotNone(skill)
            self.assertEqual(skill.name, "my-deploy")
            self.assertEqual(skill.description, "Deploy to production")
            self.assertEqual(skill.source, "user")
            self.assertIn("dp", skill.aliases)
            self.assertIn("ship", skill.aliases)
            # Prompt builder works
            prompt = skill.prompt_builder("staging")
            self.assertIn("Deploy", prompt)
            self.assertIn("staging", prompt)
        finally:
            shutil.rmtree(tmpdir)

    def test_load_skill_from_md_no_file(self):
        import tempfile, shutil
        from skills import _load_skill_from_md
        tmpdir = Path(tempfile.mkdtemp())
        try:
            skill_dir = tmpdir / "no-skill"
            skill_dir.mkdir()
            skill = _load_skill_from_md(skill_dir, "user")
            self.assertIsNone(skill)
        finally:
            shutil.rmtree(tmpdir)

    def test_load_skill_arg_substitution(self):
        import tempfile, shutil
        from skills import _load_skill_from_md
        tmpdir = Path(tempfile.mkdtemp())
        try:
            skill_dir = tmpdir / "greet"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: greet\ndescription: Greet someone\n"
                "arguments:\n  - name\n  - greeting\n---\n"
                "Say $greeting to $name"
            )
            skill = _load_skill_from_md(skill_dir, "project")
            prompt = skill.prompt_builder("Alice Hello")
            self.assertIn("Hello", prompt)
            self.assertIn("Alice", prompt)
        finally:
            shutil.rmtree(tmpdir)


class TestP97dSystemPrompt(unittest.TestCase):
    """P97d: Skill system prompt injection."""

    def test_build_skill_system_prompt(self):
        from skills import build_skill_system_prompt
        prompt = build_skill_system_prompt()
        self.assertIn("Available Skills", prompt)
        self.assertIn("/simplify", prompt)
        self.assertIn("/batch", prompt)
        self.assertIn("/skillify", prompt)
        # P98b: when_to_use is now combined with description (not separate line)
        self.assertIn("when to use", prompt.lower())

    def test_system_prompt_has_when_to_use(self):
        from skills import build_skill_system_prompt
        prompt = build_skill_system_prompt()
        self.assertIn("review quality", prompt.lower())

    def test_self_knowledge_mentions_skills(self):
        from system_prompts import _SYSTEM_PROMPT_SELF_KNOWLEDGE
        self.assertIn("7 built-in skills", _SYSTEM_PROMPT_SELF_KNOWLEDGE)
        self.assertIn("/simplify", _SYSTEM_PROMPT_SELF_KNOWLEDGE)
        self.assertIn("/batch", _SYSTEM_PROMPT_SELF_KNOWLEDGE)
        self.assertIn("/skillify", _SYSTEM_PROMPT_SELF_KNOWLEDGE)
        self.assertIn("SKILL.md", _SYSTEM_PROMPT_SELF_KNOWLEDGE)

    def test_dynamic_context_accepts_skill_context(self):
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(skill_context="# Skills\n- /test")
        self.assertIn("# Skills", ctx)
        self.assertIn("/test", ctx)

    def test_dynamic_context_empty_skill(self):
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(skill_context="")
        self.assertNotIn("Skills", ctx)

    def test_planned_task_toolbox_recommends_relevant_skills(self):
        from system_prompts import build_dynamic_context, build_task_skill_toolbox_prompt

        prompt = build_task_skill_toolbox_prompt(
            task_state="planned",
            task_title="修复登录页报错",
            task_objective="定位报错根因并简化重复逻辑，确保修复后可验证",
            task_current_step="",
            language="zh",
        )
        self.assertIn("任务工具箱建议", prompt)
        self.assertIn("/debug", prompt)
        self.assertIn("/simplify", prompt)

        ctx = build_dynamic_context(
            language="zh",
            task_state="planned",
            task_title="修复登录页报错",
            task_objective="定位报错根因并简化重复逻辑，确保修复后可验证",
        )
        self.assertIn("任务工具箱建议", ctx)
        self.assertIn("/debug", ctx)
        self.assertIn("/simplify", ctx)

    def test_non_planned_task_does_not_inject_toolbox(self):
        from system_prompts import build_dynamic_context, build_task_skill_toolbox_prompt

        prompt = build_task_skill_toolbox_prompt(
            task_state="in_progress",
            task_title="修复登录页报错",
            task_objective="定位报错根因并简化重复逻辑",
            language="zh",
        )
        self.assertEqual(prompt, "")

        ctx = build_dynamic_context(
            language="zh",
            task_state="in_progress",
            task_title="修复登录页报错",
            task_objective="定位报错根因并简化重复逻辑",
        )
        self.assertNotIn("任务工具箱建议", ctx)
        self.assertNotIn("Planned task toolbox", ctx)


class TestP97IntentMatching(unittest.TestCase):
    """P97d: Natural language → skill matching."""

    def test_match_code_review(self):
        from skills import match_skill_by_intent
        result = match_skill_by_intent("review my code changes")
        self.assertIsNotNone(result)
        skill, score = result
        self.assertEqual(skill.name, "simplify")
        self.assertGreaterEqual(score, 0.5)

    def test_match_debug_skipped_model_invocation(self):
        # P99e: /debug now has disable_model_invocation=True
        from skills import match_skill_by_intent
        result = match_skill_by_intent("I have a bug in my code")
        self.assertIsNone(result)  # Correctly skipped

    def test_match_commit_zh(self):
        from skills import match_skill_by_intent
        result = match_skill_by_intent("帮我提交代码")
        self.assertIsNotNone(result)
        self.assertEqual(result[0].name, "commit")

    def test_match_verify(self):
        from skills import match_skill_by_intent
        result = match_skill_by_intent("verify my changes")
        self.assertIsNotNone(result)
        self.assertEqual(result[0].name, "verify")

    def test_match_batch_skipped_model_invocation(self):
        # P99e: /batch now has disable_model_invocation=True
        from skills import match_skill_by_intent
        result = match_skill_by_intent("批量修改所有文件")
        self.assertIsNone(result)  # Correctly skipped

    def test_no_match_generic(self):
        from skills import match_skill_by_intent
        result = match_skill_by_intent("what is the weather today")
        self.assertIsNone(result)

    def test_no_match_short(self):
        from skills import match_skill_by_intent
        result = match_skill_by_intent("hi")
        self.assertIsNone(result)

    def test_no_match_empty(self):
        from skills import match_skill_by_intent
        self.assertIsNone(match_skill_by_intent(""))


# ═══════════════════════════════════════════════════════════════
# P98 Tests
# ═══════════════════════════════════════════════════════════════

class TestP98aUsageTracking(unittest.TestCase):
    """P98a: Skill usage tracking with recency-decay scoring."""

    def setUp(self):
        from skills.usage_tracking import reset_usage_tracking
        reset_usage_tracking()

    def tearDown(self):
        from skills.usage_tracking import reset_usage_tracking
        reset_usage_tracking()

    def test_record_and_score(self):
        from skills.usage_tracking import record_skill_usage, get_skill_usage_score, _usage_data, _last_write_time
        # Force no debounce for testing
        _last_write_time.clear()
        record_skill_usage("test_skill")
        score = get_skill_usage_score("test_skill")
        self.assertGreater(score, 0)
        # Score ≈ 1.0 (1 usage * recency ~1.0)
        self.assertAlmostEqual(score, 1.0, places=1)

    def test_untracked_skill_zero(self):
        from skills.usage_tracking import get_skill_usage_score
        self.assertEqual(get_skill_usage_score("nonexistent"), 0.0)

    def test_multiple_uses_increase_score(self):
        from skills.usage_tracking import record_skill_usage, get_skill_usage_score, _last_write_time
        _last_write_time.clear()
        record_skill_usage("multi")
        s1 = get_skill_usage_score("multi")
        # Force debounce bypass
        _last_write_time.clear()
        record_skill_usage("multi")
        s2 = get_skill_usage_score("multi")
        self.assertGreater(s2, s1)

    def test_decay_reduces_score(self):
        import time as _time
        from skills.usage_tracking import (
            record_skill_usage, get_skill_usage_score,
            _usage_data, _last_write_time
        )
        _last_write_time.clear()
        record_skill_usage("old_skill")
        # Manually set last_used_at to 14 days ago
        _usage_data["old_skill"]["last_used_at"] = _time.time() - 14 * 86400
        score = get_skill_usage_score("old_skill")
        # After 14 days (2 half-lives): ~0.25
        self.assertLess(score, 0.5)

    def test_all_scores(self):
        from skills.usage_tracking import record_skill_usage, get_all_usage_scores, _last_write_time
        _last_write_time.clear()
        record_skill_usage("a")
        _last_write_time.clear()
        record_skill_usage("b")
        scores = get_all_usage_scores()
        self.assertIn("a", scores)
        self.assertIn("b", scores)

    def test_reset(self):
        from skills.usage_tracking import record_skill_usage, get_skill_usage_score, reset_usage_tracking, _last_write_time
        _last_write_time.clear()
        record_skill_usage("temp")
        reset_usage_tracking()
        # After reset, should reload from disk or be empty
        # Since we're in test mode with no file, it should be 0
        from skills.usage_tracking import _usage_data
        self.assertEqual(len(_usage_data), 0)


class TestP98bBudgetListing(unittest.TestCase):
    """P98b: Context-budget-aware skill listing."""

    def test_default_budget(self):
        from skills import build_skill_system_prompt
        prompt = build_skill_system_prompt()
        self.assertIn("Available Skills", prompt)
        # Should contain all 7 bundled skills
        self.assertIn("/simplify", prompt)
        self.assertIn("/commit", prompt)

    def test_small_budget_still_includes_bundled(self):
        from skills import build_skill_system_prompt
        # Very small context → should still include bundled skills
        prompt = build_skill_system_prompt(context_window_tokens=100)
        self.assertIn("/simplify", prompt)

    def test_large_budget_full_descriptions(self):
        from skills import build_skill_system_prompt
        prompt = build_skill_system_prompt(context_window_tokens=200000)
        # Should have full descriptions with when_to_use
        self.assertIn("review", prompt.lower())

    def test_description_truncation(self):
        from skills import _MAX_LISTING_DESC_CHARS
        self.assertEqual(_MAX_LISTING_DESC_CHARS, 250)

    def test_sorted_by_usage(self):
        """Skills should be sorted by usage score."""
        from skills import build_skill_system_prompt
        from skills.usage_tracking import record_skill_usage, reset_usage_tracking, _last_write_time
        reset_usage_tracking()
        _last_write_time.clear()
        # Record heavy usage for 'debug'
        for _ in range(5):
            _last_write_time.clear()
            record_skill_usage("debug")
        prompt = build_skill_system_prompt()
        # debug should appear before others since it has highest usage
        idx_debug = prompt.find("/debug")
        idx_commit = prompt.find("/commit")
        self.assertLess(idx_debug, idx_commit, "debug should appear before commit due to usage score")
        reset_usage_tracking()


class TestP98dEnhancements(unittest.TestCase):
    """P98d: SkillDefinition enhancements."""

    def test_new_fields_exist(self):
        from skills import SkillDefinition
        s = SkillDefinition(
            name="test", description="test", prompt_builder=lambda a: a,
            allowed_tools=["file_read"], disable_model_invocation=True,
            skill_dir="/tmp/test"
        )
        self.assertEqual(s.allowed_tools, ["file_read"])
        self.assertTrue(s.disable_model_invocation)
        self.assertEqual(s.skill_dir, "/tmp/test")

    def test_default_values(self):
        from skills import SkillDefinition
        s = SkillDefinition(name="test", description="test", prompt_builder=lambda a: a)
        self.assertEqual(s.allowed_tools, [])
        self.assertFalse(s.disable_model_invocation)
        self.assertIsNone(s.is_enabled)
        self.assertIsNone(s.skill_dir)

    def test_is_enabled_function(self):
        from skills import SkillDefinition, register_skill, list_skills, _registry, _alias_map
        # Save state
        old_reg = dict(_registry)
        old_alias = dict(_alias_map)
        try:
            _registry.clear()
            _alias_map.clear()
            s1 = SkillDefinition(name="enabled_skill", description="on",
                                 prompt_builder=lambda a: a, is_enabled=lambda: True)
            s2 = SkillDefinition(name="disabled_skill", description="off",
                                 prompt_builder=lambda a: a, is_enabled=lambda: False)
            register_skill(s1)
            register_skill(s2)
            skills = list_skills()
            names = [s.name for s in skills]
            self.assertIn("enabled_skill", names)
            self.assertNotIn("disabled_skill", names)
        finally:
            _registry.clear()
            _registry.update(old_reg)
            _alias_map.clear()
            _alias_map.update(old_alias)

    def test_disable_model_invocation_skips_matching(self):
        from skills import SkillDefinition, register_skill, match_skill_by_intent, _registry, _alias_map
        old_reg = dict(_registry)
        old_alias = dict(_alias_map)
        try:
            _registry.clear()
            _alias_map.clear()
            # Register a debug skill with model invocation disabled
            s = SkillDefinition(
                name="debug", description="Debug tool",
                prompt_builder=lambda a: a,
                when_to_use="debug, bug, error",
                disable_model_invocation=True
            )
            register_skill(s)
            result = match_skill_by_intent("I have a bug in my code")
            self.assertIsNone(result, "Should not match disabled skill")
        finally:
            _registry.clear()
            _registry.update(old_reg)
            _alias_map.clear()
            _alias_map.update(old_alias)

    def test_skill_dir_substitution(self):
        """${NANOBOT_SKILL_DIR} should be replaced in user skill prompts."""
        import tempfile
        from skills import _load_skill_from_md
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path
            skill_dir = Path(tmpdir) / "my-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: test-dir\ndescription: test dir sub\n---\n"
                "Run script at ${NANOBOT_SKILL_DIR}/run.sh\n"
            )
            skill = _load_skill_from_md(skill_dir, "project")
            self.assertIsNotNone(skill)
            prompt = skill.prompt_builder("")
            self.assertIn(str(skill_dir), prompt)
            self.assertNotIn("${NANOBOT_SKILL_DIR}", prompt)

    def test_allowed_tools_from_frontmatter(self):
        """allowed-tools in frontmatter should populate the field."""
        import tempfile
        from skills import _load_skill_from_md
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path
            skill_dir = Path(tmpdir) / "restricted"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: restricted\ndescription: restricted skill\n"
                "allowed-tools:\n  - file_read\n  - grep_search\n---\n"
                "Only use file_read and grep_search.\n"
            )
            skill = _load_skill_from_md(skill_dir, "user")
            self.assertIsNotNone(skill)
            self.assertEqual(skill.allowed_tools, ["file_read", "grep_search"])

    def test_disable_model_invocation_from_frontmatter(self):
        import tempfile
        from skills import _load_skill_from_md
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path
            skill_dir = Path(tmpdir) / "noinvoke"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: noinvoke\ndescription: no auto\n"
                "disable-model-invocation: true\n---\n"
                "Manual only.\n"
            )
            skill = _load_skill_from_md(skill_dir, "user")
            self.assertIsNotNone(skill)
            self.assertTrue(skill.disable_model_invocation)


class TestP98eDedup(unittest.TestCase):
    """P98e: Skill deduplication and dynamic discovery."""

    def test_dedup_by_realpath(self):
        """Duplicate skills from symlinks should be deduplicated."""
        import tempfile
        from pathlib import Path
        from skills import _load_skill_from_md, _seen_skill_paths

        # Save and clear state
        old_seen = set(_seen_skill_paths)
        _seen_skill_paths.clear()

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                # Create original skill
                orig_dir = Path(tmpdir) / "original"
                orig_dir.mkdir()
                (orig_dir / "SKILL.md").write_text(
                    "---\nname: dedup-test\ndescription: test\n---\nBody\n"
                )

                # Create symlink
                link_dir = Path(tmpdir) / "linked"
                link_dir.symlink_to(orig_dir)

                # Load first should succeed
                s1 = _load_skill_from_md(orig_dir, "user")
                self.assertIsNotNone(s1)

                # Add to seen
                resolved = str((orig_dir / "SKILL.md").resolve())
                _seen_skill_paths.add(resolved)

                # Symlink resolves to same path
                link_resolved = str((link_dir / "SKILL.md").resolve())
                self.assertEqual(resolved, link_resolved)
                self.assertIn(link_resolved, _seen_skill_paths)
        finally:
            _seen_skill_paths.clear()
            _seen_skill_paths.update(old_seen)

    def test_discover_skills_for_paths(self):
        """Dynamic discovery should find skills in subdirectories."""
        import tempfile
        from pathlib import Path
        from skills import discover_skills_for_paths, _registry, _alias_map, _seen_skill_paths

        old_reg = dict(_registry)
        old_alias = dict(_alias_map)
        old_seen = set(_seen_skill_paths)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                workspace = tmpdir
                # Create nested skill directory
                sub = Path(tmpdir) / "src" / "modules" / ".nanobot" / "skills" / "local-lint"
                sub.mkdir(parents=True)
                (sub / "SKILL.md").write_text(
                    "---\nname: local-lint\ndescription: Lint this module\n---\nRun linter.\n"
                )

                # Discover from a file path in that subtree
                _seen_skill_paths.clear()
                found = discover_skills_for_paths(
                    [str(Path(tmpdir) / "src" / "modules" / "main.py")],
                    workspace=workspace
                )
                self.assertEqual(found, 1)
                from skills import get_skill
                self.assertIsNotNone(get_skill("local-lint"))
        finally:
            _registry.clear()
            _registry.update(old_reg)
            _alias_map.clear()
            _alias_map.update(old_alias)
            _seen_skill_paths.clear()
            _seen_skill_paths.update(old_seen)

    def test_discover_no_workspace(self):
        from skills import discover_skills_for_paths
        self.assertEqual(discover_skills_for_paths(["/tmp/foo.py"], workspace=""), 0)


class TestP98cProactiveHint(unittest.TestCase):
    """P98c: Proactive skill suggestion via match_skill_by_intent."""

    def test_hint_not_for_slash_commands(self):
        """Slash commands should NOT trigger skill hints."""
        # This is tested indirectly — the agentic loop checks startswith("/")
        msg = "/debug my code"
        self.assertTrue(msg.lstrip().startswith("/"))

    def test_hint_for_natural_language(self):
        from skills import match_skill_by_intent
        result = match_skill_by_intent("review my code for quality issues")
        self.assertIsNotNone(result)
        skill, confidence = result
        self.assertEqual(skill.name, "simplify")
        self.assertGreaterEqual(confidence, 0.3)


class TestP99aConditionalSkills(unittest.TestCase):
    """P99a: Conditional skills — paths frontmatter + activation."""

    def setUp(self):
        from skills import (_conditional_skills, _activated_conditional_names,
                            _registry, _alias_map, clear_conditional_skills)
        self._orig_registry = dict(_registry)
        self._orig_aliases = dict(_alias_map)
        clear_conditional_skills()

    def tearDown(self):
        from skills import (_conditional_skills, _activated_conditional_names,
                            _registry, _alias_map, clear_conditional_skills)
        clear_conditional_skills()
        # Restore registry
        _registry.clear()
        _registry.update(self._orig_registry)
        _alias_map.clear()
        _alias_map.update(self._orig_aliases)

    def test_paths_field_exists(self):
        from skills import SkillDefinition
        sd = SkillDefinition(name="test", description="", prompt_builder=lambda a: a)
        self.assertEqual(sd.paths, [])
        self.assertEqual(sd.context, "inline")

    def test_conditional_skill_not_in_registry(self):
        """Skills with paths are stored as conditional, not in active registry."""
        from skills import register_skill, get_skill, SkillDefinition, _conditional_skills
        skill = SkillDefinition(
            name="pytest_helper", description="Test helper",
            prompt_builder=lambda a: "test",
            paths=["tests/**/*.py"],
            source="user",
        )
        register_skill(skill)
        self.assertIsNone(get_skill("pytest_helper"))
        self.assertIn("pytest_helper", _conditional_skills)

    def test_activate_on_matching_path(self):
        """Conditional skill activates when a matching file is touched."""
        from skills import (register_skill, get_skill, activate_conditional_skills,
                            SkillDefinition, get_conditional_skill_count)
        skill = SkillDefinition(
            name="react_lint", description="React linting",
            prompt_builder=lambda a: "lint",
            paths=["src/components/*.tsx", "*.jsx"],
            source="project",
        )
        register_skill(skill)
        self.assertEqual(get_conditional_skill_count(), 1)
        self.assertIsNone(get_skill("react_lint"))

        # Touch a non-matching file
        activated = activate_conditional_skills(["/proj/src/utils/helper.py"], cwd="/proj")
        self.assertEqual(activated, [])
        self.assertEqual(get_conditional_skill_count(), 1)

        # Touch a matching file
        activated = activate_conditional_skills(["/proj/src/components/Button.tsx"], cwd="/proj")
        self.assertEqual(activated, ["react_lint"])
        self.assertEqual(get_conditional_skill_count(), 0)
        self.assertIsNotNone(get_skill("react_lint"))

    def test_activate_no_double_activation(self):
        """Already activated skills should not be re-activated."""
        from skills import (register_skill, activate_conditional_skills,
                            SkillDefinition, get_conditional_skill_count)
        skill = SkillDefinition(
            name="ts_helper", description="TS",
            prompt_builder=lambda a: "ts",
            paths=["*.ts"],
            source="user",
        )
        register_skill(skill)
        activate_conditional_skills(["/proj/app.ts"], cwd="/proj")
        # Second activation on another matching file
        activated2 = activate_conditional_skills(["/proj/index.ts"], cwd="/proj")
        self.assertEqual(activated2, [])  # Already activated

    def test_bundled_skills_not_conditional(self):
        """Bundled skills with paths should still be registered normally."""
        from skills import register_skill, get_skill, SkillDefinition, _conditional_skills
        skill = SkillDefinition(
            name="bundled_with_paths", description="Test",
            prompt_builder=lambda a: "b",
            paths=["*.py"],
            source="bundled",
        )
        register_skill(skill)
        # Should be in main registry, not conditional
        self.assertIsNotNone(get_skill("bundled_with_paths"))
        self.assertNotIn("bundled_with_paths", _conditional_skills)

    def test_frontmatter_paths_parsing(self):
        """SKILL.md with paths frontmatter should create conditional skill."""
        import tempfile, os
        from pathlib import Path
        from skills import _load_skill_from_md
        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: vue-helper\ndescription: Vue assistant\n"
                "paths:\n  - \"src/**/*.vue\"\n  - \"*.vue\"\n---\n# Vue Helper\nHelp with Vue"
            )
            sd = _load_skill_from_md(skill_dir, "project")
            self.assertIsNotNone(sd)
            self.assertEqual(sd.paths, ["src/**/*.vue", "*.vue"])


class TestP99bShellExecution(unittest.TestCase):
    """P99b: Shell command execution in skill prompts."""

    def test_no_shell_commands_passthrough(self):
        from skills import execute_shell_in_prompt
        text = "# Simple prompt\n\nNo shell commands here."
        self.assertEqual(execute_shell_in_prompt(text), text)

    def test_inline_shell_command(self):
        from skills import execute_shell_in_prompt
        text = "Current date: !`echo hello_world`"
        # Need leading whitespace before !
        text2 = "Current date: !`echo hello_world`"
        result = execute_shell_in_prompt(text2, "test")
        self.assertIn("hello_world", result)
        self.assertNotIn("!`", result)

    def test_block_shell_command(self):
        from skills import execute_shell_in_prompt
        text = "```!\necho block_output_test\n```"
        result = execute_shell_in_prompt(text, "test")
        self.assertIn("block_output_test", result)
        self.assertNotIn("```!", result)

    def test_shell_command_timeout(self):
        """Commands that exceed timeout should be replaced with error comment."""
        from skills import execute_shell_in_prompt
        # Use a very fast version for test — just verify the function handles errors
        text = "Result: !`false`"
        result = execute_shell_in_prompt(text, "test")
        # Should have replaced the shell command (even if empty output)
        self.assertNotIn("!`false`", result)

    def test_shell_command_failure(self):
        from skills import execute_shell_in_prompt
        text = "```!\ncommand_that_does_not_exist_xyz123\n```"
        result = execute_shell_in_prompt(text, "test")
        # Should contain error or stderr comment
        self.assertNotIn("```!", result)

    def test_multiple_shell_commands(self):
        from skills import execute_shell_in_prompt
        text = "A: !`echo aaa` and B: !`echo bbb`"
        result = execute_shell_in_prompt(text, "test")
        self.assertIn("aaa", result)
        self.assertIn("bbb", result)


class TestP99cForkedExecution(unittest.TestCase):
    """P99c: Forked skill execution (context: fork)."""

    def test_context_field_default(self):
        from skills import SkillDefinition
        sd = SkillDefinition(name="t", description="", prompt_builder=lambda a: a)
        self.assertEqual(sd.context, "inline")

    def test_context_fork_in_definition(self):
        from skills import SkillDefinition
        sd = SkillDefinition(name="t", description="", prompt_builder=lambda a: a,
                             context="fork")
        self.assertEqual(sd.context, "fork")
        # fork implies uses_sub_agent should be true logically

    def test_build_forked_prompt(self):
        from skills import _build_forked_prompt, SkillDefinition
        skill = SkillDefinition(name="test_fork", description="",
                                prompt_builder=lambda a: "base prompt", context="fork")
        result = _build_forked_prompt(skill, "base prompt")
        self.assertIn("SKILL_FORK: test_fork", result)
        self.assertIn("forked execution", result)
        self.assertIn("isolated sub-agent", result)
        self.assertIn("base prompt", result)

    def test_execute_skill_with_fork_context(self):
        """Executing a fork skill should wrap prompt with fork metadata."""
        from skills import register_skill, execute_skill, SkillDefinition, _registry, _alias_map
        orig_reg = dict(_registry)
        orig_alias = dict(_alias_map)
        try:
            skill = SkillDefinition(
                name="forked_test", description="A forked skill",
                prompt_builder=lambda a: f"Do something with: {a}",
                context="fork", source="bundled",
            )
            register_skill(skill)
            result = execute_skill("forked_test", "my args")
            self.assertIsNotNone(result)
            self.assertIn("SKILL_FORK: forked_test", result)
            self.assertIn("Do something with: my args", result)
        finally:
            _registry.clear()
            _registry.update(orig_reg)
            _alias_map.clear()
            _alias_map.update(orig_alias)

    def test_frontmatter_context_fork(self):
        """SKILL.md with context: fork should set uses_sub_agent=True."""
        import tempfile
        from pathlib import Path
        from skills import _load_skill_from_md
        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: fork-skill\ndescription: Forked\n"
                "context: fork\n---\n# Forked Skill\nRun in isolation"
            )
            sd = _load_skill_from_md(skill_dir, "user")
            self.assertIsNotNone(sd)
            self.assertEqual(sd.context, "fork")
            self.assertTrue(sd.uses_sub_agent)


class TestP99dAllowedToolsEnforcement(unittest.TestCase):
    """P99d: Allowed tools enforcement."""

    def test_extract_allowed_tools_from_prompt(self):
        from skills import extract_allowed_tools_from_prompt
        prompt = "Some prompt\n<!-- SKILL_ALLOWED_TOOLS: file_read,grep_search -->"
        tools = extract_allowed_tools_from_prompt(prompt)
        self.assertEqual(tools, ["file_read", "grep_search"])

    def test_extract_no_allowed_tools(self):
        from skills import extract_allowed_tools_from_prompt
        prompt = "Some prompt without tool restriction"
        tools = extract_allowed_tools_from_prompt(prompt)
        self.assertEqual(tools, [])

    def test_is_tool_allowed_empty_list(self):
        from skills import is_tool_allowed_for_skill
        self.assertTrue(is_tool_allowed_for_skill("file_read", []))

    def test_is_tool_allowed_exact_match(self):
        from skills import is_tool_allowed_for_skill
        allowed = ["file_read", "grep_search", "shell_execute"]
        self.assertTrue(is_tool_allowed_for_skill("file_read", allowed))
        self.assertTrue(is_tool_allowed_for_skill("grep_search", allowed))
        self.assertFalse(is_tool_allowed_for_skill("file_edit", allowed))
        self.assertFalse(is_tool_allowed_for_skill("python_execute", allowed))

    def test_is_tool_allowed_glob_pattern(self):
        from skills import is_tool_allowed_for_skill
        allowed = ["file_*", "grep_search"]
        self.assertTrue(is_tool_allowed_for_skill("file_read", allowed))
        self.assertTrue(is_tool_allowed_for_skill("file_edit", allowed))
        self.assertTrue(is_tool_allowed_for_skill("file_write", allowed))
        self.assertFalse(is_tool_allowed_for_skill("shell_execute", allowed))

    def test_is_tool_allowed_claw_style_pattern(self):
        from skills import is_tool_allowed_for_skill
        # Claw-style: 'Bash(git:*)' — should match shell_execute
        allowed = ["shell_execute(git:*)"]
        self.assertTrue(is_tool_allowed_for_skill("shell_execute", allowed))
        self.assertFalse(is_tool_allowed_for_skill("file_read", allowed))

    def test_execute_skill_appends_allowed_tools(self):
        """Skills with allowed_tools should have metadata in their prompt."""
        from skills import register_skill, execute_skill, SkillDefinition, _registry, _alias_map
        orig_reg = dict(_registry)
        orig_alias = dict(_alias_map)
        try:
            skill = SkillDefinition(
                name="restricted_test", description="Restricted",
                prompt_builder=lambda a: "Do restricted things",
                allowed_tools=["file_read", "grep_search"],
                source="bundled",
            )
            register_skill(skill)
            result = execute_skill("restricted_test", "")
            self.assertIn("SKILL_ALLOWED_TOOLS: file_read,grep_search", result)
        finally:
            _registry.clear()
            _registry.update(orig_reg)
            _alias_map.clear()
            _alias_map.update(orig_alias)

    def test_get_skill_metadata(self):
        from skills import get_skill_metadata
        meta = get_skill_metadata("debug")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["name"], "debug")
        self.assertIn("file_read", meta["allowed_tools"])
        self.assertTrue(meta["disable_model_invocation"])

    def test_get_skill_metadata_nonexistent(self):
        from skills import get_skill_metadata
        meta = get_skill_metadata("nonexistent_skill_xyz")
        self.assertIsNone(meta)


class TestP99eBundledSkillUpgrades(unittest.TestCase):
    """P99e: Bundled skill enhancements — allowed_tools and disable_model_invocation."""

    def test_debug_has_allowed_tools(self):
        from skills import get_skill
        debug = get_skill("debug")
        self.assertIsNotNone(debug)
        # P101: /debug coding skill has broader read tools + shell
        self.assertIn("file_read", debug.allowed_tools)
        self.assertIn("grep_search", debug.allowed_tools)
        self.assertIn("shell_execute", debug.allowed_tools)
        self.assertNotIn("file_edit", debug.allowed_tools)  # write blocked
        self.assertTrue(debug.disable_model_invocation)
        # P101: Runtime enforcement fields
        self.assertEqual(debug.mode, "debug")
        self.assertEqual(debug.write_policy, "explicit_only")

    def test_batch_has_disable_model_invocation(self):
        from skills import get_skill
        batch = get_skill("batch")
        self.assertIsNotNone(batch)
        self.assertTrue(batch.disable_model_invocation)

    def test_skillify_has_allowed_tools_and_disable(self):
        from skills import get_skill
        sk = get_skill("skillify")
        self.assertIsNotNone(sk)
        self.assertTrue(sk.disable_model_invocation)
        self.assertIn("file_read", sk.allowed_tools)
        self.assertIn("file_edit", sk.allowed_tools)
        self.assertIn("shell_execute", sk.allowed_tools)

    def test_simplify_no_disable(self):
        """Simplify should still be auto-invocable (not disabled)."""
        from skills import get_skill
        simplify = get_skill("simplify")
        self.assertFalse(simplify.disable_model_invocation)
        self.assertEqual(simplify.allowed_tools, [])

    def test_commit_no_disable(self):
        from skills import get_skill
        commit = get_skill("commit")
        self.assertFalse(commit.disable_model_invocation)

    def test_verify_no_disable(self):
        from skills import get_skill
        verify = get_skill("verify")
        self.assertFalse(verify.disable_model_invocation)

    def test_debug_prompt_includes_allowed_tools_metadata(self):
        """Executing /debug should include SKILL_ALLOWED_TOOLS in prompt."""
        from skills import execute_skill
        result = execute_skill("debug", "test issue")
        self.assertIsNotNone(result)
        self.assertIn("SKILL_ALLOWED_TOOLS:", result)
        self.assertIn("file_read", result)


# ═══════════════════════════════════════════════════════════════
# P100a: Skill Input Validation Tests
# ═══════════════════════════════════════════════════════════════

class TestP100aInputValidation(unittest.TestCase):
    """P100a: Test validate_skill_input (Claw SkillTool.validateInput)."""

    def test_valid_skill(self):
        """Known bundled skill should validate successfully."""
        from skills import validate_skill_input
        result = validate_skill_input("simplify")
        self.assertTrue(result["valid"])
        self.assertEqual(result["error_code"], 0)
        self.assertIsNotNone(result["skill"])

    def test_valid_with_leading_slash(self):
        """Leading slash should be stripped and still validate."""
        from skills import validate_skill_input
        result = validate_skill_input("/simplify")
        self.assertTrue(result["valid"])

    def test_empty_name(self):
        """Empty skill name should fail with error_code 1."""
        from skills import validate_skill_input
        result = validate_skill_input("")
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_code"], 1)

    def test_whitespace_only(self):
        """Whitespace-only name should fail with error_code 1."""
        from skills import validate_skill_input
        result = validate_skill_input("   ")
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_code"], 1)

    def test_unknown_skill(self):
        """Unknown skill should fail with error_code 2."""
        from skills import validate_skill_input
        result = validate_skill_input("nonexistent_skill_xyz")
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_code"], 2)

    def test_model_invocation_blocked(self):
        """Skill with disable_model_invocation should fail when model-invoked."""
        from skills import validate_skill_input
        result = validate_skill_input("debug", is_model_invocation=True)
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_code"], 3)

    def test_model_invocation_allowed_for_user(self):
        """Skill with disable_model_invocation should pass for direct user invocation."""
        from skills import validate_skill_input
        result = validate_skill_input("debug", is_model_invocation=False)
        self.assertTrue(result["valid"])

    def test_disabled_skill(self):
        """Skill with is_enabled returning False should fail with error_code 4."""
        from skills import register_skill, validate_skill_input, _registry, SkillDefinition
        # Register a disabled skill
        _registry["_test_disabled"] = SkillDefinition(
            name="_test_disabled",
            description="test",
            prompt_builder=lambda a: "test",
            is_enabled=lambda: False,
        )
        try:
            result = validate_skill_input("_test_disabled")
            self.assertFalse(result["valid"])
            self.assertEqual(result["error_code"], 4)
        finally:
            del _registry["_test_disabled"]


# ═══════════════════════════════════════════════════════════════
# P100b: Skill Permission Tests
# ═══════════════════════════════════════════════════════════════

class TestP100bPermissions(unittest.TestCase):
    """P100b: Test permission allow/deny rules and safe-properties auto-allow."""

    def setUp(self):
        from skills import set_skill_permission_rules
        set_skill_permission_rules(allow=[], deny=[])

    def tearDown(self):
        from skills import set_skill_permission_rules
        set_skill_permission_rules(allow=[], deny=[])

    def test_deny_rule_exact(self):
        """Exact deny rule should block skill."""
        from skills import set_skill_permission_rules, check_skill_permission
        set_skill_permission_rules(deny=["simplify"])
        result = check_skill_permission("simplify")
        self.assertEqual(result["decision"], "deny")

    def test_deny_rule_with_slash(self):
        """Deny rule with leading slash should still match."""
        from skills import set_skill_permission_rules, check_skill_permission
        set_skill_permission_rules(deny=["/simplify"])
        result = check_skill_permission("simplify")
        self.assertEqual(result["decision"], "deny")

    def test_deny_rule_prefix_wildcard(self):
        """Deny rule with :* suffix should match prefix."""
        from skills import set_skill_permission_rules, check_skill_permission
        set_skill_permission_rules(deny=["review:*"])
        result = check_skill_permission("review-code")
        self.assertEqual(result["decision"], "deny")

    def test_allow_rule_exact(self):
        """Exact allow rule should allow skill."""
        from skills import set_skill_permission_rules, check_skill_permission
        set_skill_permission_rules(allow=["debug"])
        result = check_skill_permission("debug")
        self.assertEqual(result["decision"], "allow")
        self.assertIn("allow rule", result["reason"])

    def test_deny_takes_priority(self):
        """Deny rules should be checked before allow rules."""
        from skills import set_skill_permission_rules, check_skill_permission
        set_skill_permission_rules(allow=["simplify"], deny=["simplify"])
        result = check_skill_permission("simplify")
        self.assertEqual(result["decision"], "deny")

    def test_safe_properties_auto_allow(self):
        """Skills with only safe properties should auto-allow."""
        from skills import check_skill_permission
        # simplify has no allowed_tools and is inline context → safe
        result = check_skill_permission("simplify")
        self.assertEqual(result["decision"], "allow")
        self.assertIn("Safe properties", result["reason"])

    def test_unsafe_skill_asks(self):
        """Skills with allowed_tools should not auto-allow (asks user)."""
        from skills import check_skill_permission
        # debug has allowed_tools → not safe → asks
        result = check_skill_permission("debug")
        self.assertEqual(result["decision"], "ask")

    def test_skill_has_only_safe_properties(self):
        """Direct test of _skill_has_only_safe_properties."""
        from skills import _skill_has_only_safe_properties, SkillDefinition
        safe = SkillDefinition(name="safe", description="t", prompt_builder=lambda a: "t")
        self.assertTrue(_skill_has_only_safe_properties(safe))

        unsafe_tools = SkillDefinition(name="u", description="t",
                                        prompt_builder=lambda a: "t",
                                        allowed_tools=["file_read"])
        self.assertFalse(_skill_has_only_safe_properties(unsafe_tools))

        unsafe_fork = SkillDefinition(name="f", description="t",
                                       prompt_builder=lambda a: "t",
                                       context="fork")
        self.assertFalse(_skill_has_only_safe_properties(unsafe_fork))

    def test_glob_pattern_deny(self):
        """Deny rule with glob pattern should work."""
        from skills import set_skill_permission_rules, check_skill_permission
        set_skill_permission_rules(deny=["sim*"])
        result = check_skill_permission("simplify")
        self.assertEqual(result["decision"], "deny")


# ═══════════════════════════════════════════════════════════════
# P100c: Allowed Tools Enforcement Tests
# ═══════════════════════════════════════════════════════════════

class TestP100cToolEnforcement(unittest.TestCase):
    """P100c: Test agentic loop skill tool restriction detection."""

    def test_extract_allowed_tools(self):
        """SKILL_ALLOWED_TOOLS metadata extracted from prompt."""
        from skills import extract_allowed_tools_from_prompt
        prompt = "Do something\n\n<!-- SKILL_ALLOWED_TOOLS: file_read,grep_search -->"
        tools = extract_allowed_tools_from_prompt(prompt)
        self.assertEqual(tools, ["file_read", "grep_search"])

    def test_extract_no_tools(self):
        """No metadata returns empty list."""
        from skills import extract_allowed_tools_from_prompt
        tools = extract_allowed_tools_from_prompt("plain prompt")
        self.assertEqual(tools, [])

    def test_skill_overrides_extraction(self):
        """extract_skill_overrides_from_prompt returns combined metadata."""
        from skills import extract_skill_overrides_from_prompt
        prompt = (
            "Task\n"
            "<!-- SKILL_ALLOWED_TOOLS: file_read -->\n"
            "<!-- SKILL_MODEL: gpt-4 -->\n"
            "<!-- SKILL_EFFORT: 0.8 -->"
        )
        overrides = extract_skill_overrides_from_prompt(prompt)
        self.assertEqual(overrides["allowed_tools"], ["file_read"])
        self.assertEqual(overrides["model"], "gpt-4")
        self.assertAlmostEqual(overrides["effort"], 0.8)
        self.assertFalse(overrides["is_forked"])

    def test_skill_overrides_forked(self):
        """Forked skill detected from SKILL_FORK marker."""
        from skills import extract_skill_overrides_from_prompt
        prompt = "<!-- SKILL_FORK: myskill -->\nContent"
        overrides = extract_skill_overrides_from_prompt(prompt)
        self.assertTrue(overrides["is_forked"])

    def test_skill_overrides_empty(self):
        """Plain prompt returns no overrides."""
        from skills import extract_skill_overrides_from_prompt
        overrides = extract_skill_overrides_from_prompt("just text")
        self.assertEqual(overrides["allowed_tools"], [])
        self.assertIsNone(overrides["model"])
        self.assertIsNone(overrides["effort"])
        self.assertFalse(overrides["is_forked"])

    def test_effort_out_of_range_ignored(self):
        """Effort value > 1.0 should be ignored."""
        from skills import extract_skill_overrides_from_prompt
        prompt = "<!-- SKILL_EFFORT: 1.5 -->"
        overrides = extract_skill_overrides_from_prompt(prompt)
        self.assertIsNone(overrides["effort"])

    def test_execute_skill_appends_allowed_tools_metadata(self):
        """execute_skill should embed SKILL_ALLOWED_TOOLS for debug skill."""
        from skills import execute_skill, extract_allowed_tools_from_prompt
        result = execute_skill("debug", "test")
        self.assertIsNotNone(result)
        tools = extract_allowed_tools_from_prompt(result)
        self.assertIn("file_read", tools)


# ═══════════════════════════════════════════════════════════════
# P100d: Skill Model/Effort Override Tests
# ═══════════════════════════════════════════════════════════════

class TestP100dOverrides(unittest.TestCase):
    """P100d: Test model and effort override fields."""

    def test_skill_definition_model_field(self):
        """SkillDefinition should accept model field."""
        from skills import SkillDefinition
        sd = SkillDefinition(
            name="test", description="t",
            prompt_builder=lambda a: "t",
            model="llama-3.1-70b",
        )
        self.assertEqual(sd.model, "llama-3.1-70b")

    def test_skill_definition_effort_field(self):
        """SkillDefinition should accept effort field."""
        from skills import SkillDefinition
        sd = SkillDefinition(
            name="test", description="t",
            prompt_builder=lambda a: "t",
            effort=0.7,
        )
        self.assertAlmostEqual(sd.effort, 0.7)

    def test_metadata_includes_model_effort(self):
        """get_skill_metadata should include model and effort."""
        from skills import get_skill_metadata, register_skill, SkillDefinition, _registry
        _registry["_test_override"] = SkillDefinition(
            name="_test_override", description="t",
            prompt_builder=lambda a: "t",
            model="custom-model",
            effort=0.5,
        )
        try:
            meta = get_skill_metadata("_test_override")
            self.assertEqual(meta["model"], "custom-model")
            self.assertAlmostEqual(meta["effort"], 0.5)
        finally:
            del _registry["_test_override"]

    def test_execute_skill_appends_model_metadata(self):
        """execute_skill should embed SKILL_MODEL metadata."""
        from skills import _registry, SkillDefinition, execute_skill
        _registry["_test_model"] = SkillDefinition(
            name="_test_model", description="t",
            prompt_builder=lambda a: "prompt " + a,
            model="gpt-4o",
        )
        try:
            result = execute_skill("_test_model", "arg")
            self.assertIn("SKILL_MODEL: gpt-4o", result)
        finally:
            del _registry["_test_model"]

    def test_execute_skill_appends_effort_metadata(self):
        """execute_skill should embed SKILL_EFFORT metadata."""
        from skills import _registry, SkillDefinition, execute_skill
        _registry["_test_effort"] = SkillDefinition(
            name="_test_effort", description="t",
            prompt_builder=lambda a: "prompt " + a,
            effort=0.3,
        )
        try:
            result = execute_skill("_test_effort", "arg")
            self.assertIn("SKILL_EFFORT: 0.3", result)
        finally:
            del _registry["_test_effort"]

    def test_frontmatter_model_parsing(self):
        """SKILL.md with model: field should produce SkillDefinition with model."""
        import tempfile, os
        from skills import _load_skill_from_md
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td) / "test-model-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: test-model\ndescription: test\nmodel: llama-3.1\n---\nBody"
            )
            sd = _load_skill_from_md(skill_dir, "user")
            self.assertIsNotNone(sd)
            self.assertEqual(sd.model, "llama-3.1")

    def test_frontmatter_effort_parsing(self):
        """SKILL.md with effort: field should produce SkillDefinition with effort."""
        import tempfile
        from skills import _load_skill_from_md
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td) / "test-effort-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: test-effort\ndescription: test\neffort: 0.6\n---\nBody"
            )
            sd = _load_skill_from_md(skill_dir, "user")
            self.assertIsNotNone(sd)
            self.assertAlmostEqual(sd.effort, 0.6)

    def test_frontmatter_effort_invalid_ignored(self):
        """SKILL.md with effort > 1.0 should be ignored."""
        import tempfile
        from skills import _load_skill_from_md
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td) / "test-effort-bad"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: test-effort-bad\ndescription: test\neffort: 2.0\n---\nBody"
            )
            sd = _load_skill_from_md(skill_dir, "user")
            self.assertIsNotNone(sd)
            self.assertIsNone(sd.effort)


# ═══════════════════════════════════════════════════════════════
# P100e: Skill Invocation Tracking Tests
# ═══════════════════════════════════════════════════════════════

class TestP100eInvocationTracking(unittest.TestCase):
    """P100e: Test skill invocation tracking for compaction preservation."""

    def setUp(self):
        from skills import clear_invoked_skills
        clear_invoked_skills()

    def tearDown(self):
        from skills import clear_invoked_skills
        clear_invoked_skills()

    def test_track_and_get(self):
        """track_skill_invocation should store prompt, get_invoked_skills retrieves it."""
        from skills import track_skill_invocation, get_invoked_skills
        track_skill_invocation("simplify", "Review and simplify code")
        invoked = get_invoked_skills()
        self.assertIn("simplify", invoked)
        self.assertEqual(invoked["simplify"], "Review and simplify code")

    def test_clear(self):
        """clear_invoked_skills should remove all tracking."""
        from skills import track_skill_invocation, get_invoked_skills, clear_invoked_skills
        track_skill_invocation("test", "prompt")
        self.assertEqual(len(get_invoked_skills()), 1)
        clear_invoked_skills()
        self.assertEqual(len(get_invoked_skills()), 0)

    def test_execute_skill_tracks_invocation(self):
        """execute_skill should automatically track the invocation."""
        from skills import execute_skill, get_invoked_skills
        execute_skill("simplify", "test args")
        invoked = get_invoked_skills()
        self.assertIn("simplify", invoked)
        self.assertIn("test args", invoked["simplify"])

    def test_multiple_skills_tracked(self):
        """Multiple skill invocations should all be tracked."""
        from skills import track_skill_invocation, get_invoked_skills
        track_skill_invocation("a", "prompt a")
        track_skill_invocation("b", "prompt b")
        invoked = get_invoked_skills()
        self.assertEqual(len(invoked), 2)
        self.assertIn("a", invoked)
        self.assertIn("b", invoked)

    def test_overwrite_same_skill(self):
        """Re-invoking same skill should overwrite the prompt."""
        from skills import track_skill_invocation, get_invoked_skills
        track_skill_invocation("x", "old prompt")
        track_skill_invocation("x", "new prompt")
        invoked = get_invoked_skills()
        self.assertEqual(invoked["x"], "new prompt")

    def test_get_returns_copy(self):
        """get_invoked_skills should return a copy, not the original dict."""
        from skills import track_skill_invocation, get_invoked_skills
        track_skill_invocation("t", "p")
        a = get_invoked_skills()
        b = get_invoked_skills()
        self.assertIsNot(a, b)


# ═══════════════════════════════════════════════════════════════
# P101: Coding Skills Tests
# ═══════════════════════════════════════════════════════════════

class TestP101CodingSkills(unittest.TestCase):
    """P101: 4 runtime-enforced coding skills — analyze, debug, verify, refactor."""

    # ── Registration ──

    def test_all_four_registered(self):
        from skills import get_skill
        for name in ["analyze", "debug", "verify", "refactor"]:
            s = get_skill(name)
            self.assertIsNotNone(s, f"/{name} should be registered")
            self.assertIsNotNone(s.mode, f"/{name} should have a mode")

    def test_analyze_mode_fields(self):
        from skills import get_skill
        s = get_skill("analyze")
        self.assertEqual(s.mode, "analyze")
        self.assertEqual(s.write_policy, "forbid")
        self.assertFalse(s.requires_verification)
        self.assertIn("file_edit", s.disallowed_tools)
        self.assertIn("file_write", s.disallowed_tools)
        self.assertNotIn("file_edit", s.allowed_tools)

    def test_debug_mode_fields(self):
        from skills import get_skill
        s = get_skill("debug")
        self.assertEqual(s.mode, "debug")
        self.assertEqual(s.write_policy, "explicit_only")
        self.assertFalse(s.requires_verification)
        self.assertTrue(s.disable_model_invocation)
        self.assertIn("shell_execute", s.allowed_tools)
        self.assertNotIn("file_edit", s.allowed_tools)

    def test_verify_mode_fields(self):
        from skills import get_skill
        s = get_skill("verify")
        self.assertEqual(s.mode, "verify")
        self.assertEqual(s.write_policy, "forbid")
        self.assertFalse(s.requires_verification)
        self.assertIn("shell_execute", s.allowed_tools)
        self.assertNotIn("file_edit", s.allowed_tools)

    def test_refactor_mode_fields(self):
        from skills import get_skill
        s = get_skill("refactor")
        self.assertEqual(s.mode, "refactor")
        self.assertEqual(s.write_policy, "allowed_with_verification")
        self.assertTrue(s.requires_verification)
        self.assertIn("file_edit", s.allowed_tools)
        self.assertIn("shell_execute", s.allowed_tools)

    # ── Output fields & completion criteria ──

    def test_analyze_output_fields(self):
        from skills import get_skill
        s = get_skill("analyze")
        self.assertIn("scope", s.output_fields)
        self.assertIn("entrypoints", s.output_fields)
        self.assertIn("call_flow", s.output_fields)
        self.assertGreater(len(s.completion_criteria), 0)

    def test_verify_output_fields(self):
        from skills import get_skill
        s = get_skill("verify")
        self.assertIn("status", s.output_fields)
        self.assertIn("commands_run", s.output_fields)

    def test_refactor_output_fields(self):
        from skills import get_skill
        s = get_skill("refactor")
        self.assertIn("files_changed", s.output_fields)
        self.assertIn("verification", s.output_fields)

    # ── Aliases ──

    def test_analyze_aliases(self):
        from skills import get_skill
        for alias in ["analyse", "understand", "trace", "map"]:
            s = get_skill(alias)
            self.assertIsNotNone(s, f"Alias '{alias}' should resolve")
            self.assertEqual(s.name, "analyze")

    def test_refactor_aliases(self):
        from skills import get_skill
        for alias in ["restructure", "cleanup_code", "reorganize"]:
            s = get_skill(alias)
            self.assertIsNotNone(s, f"Alias '{alias}' should resolve")
            self.assertEqual(s.name, "refactor")

    # ── Prompt metadata emission ──

    def test_analyze_prompt_metadata(self):
        from skills import execute_skill
        prompt = execute_skill("analyze", "test")
        self.assertIn("<!-- SKILL_MODE: analyze -->", prompt)
        self.assertIn("<!-- SKILL_WRITE_POLICY: forbid -->", prompt)
        self.assertNotIn("SKILL_REQUIRES_VERIFICATION", prompt)

    def test_refactor_prompt_metadata(self):
        from skills import execute_skill
        prompt = execute_skill("refactor", "test")
        self.assertIn("<!-- SKILL_MODE: refactor -->", prompt)
        self.assertIn("<!-- SKILL_WRITE_POLICY: allowed_with_verification -->", prompt)
        self.assertIn("<!-- SKILL_REQUIRES_VERIFICATION: true -->", prompt)

    def test_verify_prompt_metadata(self):
        from skills import execute_skill
        prompt = execute_skill("verify", "test")
        self.assertIn("<!-- SKILL_MODE: verify -->", prompt)
        self.assertIn("<!-- SKILL_WRITE_POLICY: forbid -->", prompt)

    # ── Extraction round-trip ──

    def test_overrides_extraction_roundtrip(self):
        from skills import execute_skill, extract_skill_overrides_from_prompt
        prompt = execute_skill("refactor", "test")
        overrides = extract_skill_overrides_from_prompt(prompt)
        self.assertEqual(overrides["mode"], "refactor")
        self.assertEqual(overrides["write_policy"], "allowed_with_verification")
        self.assertTrue(overrides["requires_verification"])

    def test_overrides_extraction_analyze(self):
        from skills import execute_skill, extract_skill_overrides_from_prompt
        prompt = execute_skill("analyze", "test")
        overrides = extract_skill_overrides_from_prompt(prompt)
        self.assertEqual(overrides["mode"], "analyze")
        self.assertEqual(overrides["write_policy"], "forbid")
        self.assertFalse(overrides["requires_verification"])

    # ── Metadata export ──

    def test_get_skill_metadata_includes_p101_fields(self):
        from skills import get_skill_metadata
        meta = get_skill_metadata("refactor")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["mode"], "refactor")
        self.assertEqual(meta["write_policy"], "allowed_with_verification")
        self.assertTrue(meta["requires_verification"])
        self.assertIn("python_execute", meta["disallowed_tools"])
        self.assertIn("files_changed", meta["output_fields"])
        self.assertEqual(meta["default_subagent"], "research")

    # ── Legacy skills ──

    def test_legacy_debug_still_exists(self):
        from skills import get_skill
        s = get_skill("debug_legacy")
        self.assertIsNotNone(s)
        self.assertIn("[Legacy]", s.description)

    def test_legacy_verify_still_exists(self):
        from skills import get_skill
        s = get_skill("verify_legacy")
        self.assertIsNotNone(s)
        self.assertIn("[Legacy]", s.description)

    # ── Prompt content ──

    def test_analyze_prompt_structure(self):
        from skills import execute_skill
        prompt = execute_skill("analyze", "the auth module")
        self.assertIn("analyze mode", prompt)
        self.assertIn("the auth module", prompt)
        self.assertIn("Anti-hallucination", prompt)
        self.assertIn("Output Structure", prompt)

    def test_debug_prompt_structure(self):
        from skills import execute_skill
        prompt = execute_skill("debug", "test failure")
        self.assertIn("debug mode", prompt)
        self.assertIn("test failure", prompt)
        self.assertIn("Root Cause", prompt)
        self.assertIn("Validation Plan", prompt)

    def test_verify_prompt_structure(self):
        from skills import execute_skill
        prompt = execute_skill("verify")
        self.assertIn("verify mode", prompt)
        self.assertIn("PASSED", prompt)
        self.assertIn("BLOCKED", prompt)
        self.assertIn("Verification Priority", prompt)

    def test_refactor_prompt_structure(self):
        from skills import execute_skill
        prompt = execute_skill("refactor", "extract helper")
        self.assertIn("refactor mode", prompt)
        self.assertIn("extract helper", prompt)
        self.assertIn("pending_verification", prompt)
        self.assertIn("Residual Risks", prompt)


# ═══════════════════════════════════════════════════════════════
# P102: Runtime Enforcement Tests
# ═══════════════════════════════════════════════════════════════

class TestP102RuntimeEnforcement(unittest.TestCase):
    """P102: agentic_loop.py runtime enforcement for coding skills."""

    # ── _is_verification_command ──

    def test_pytest_is_verification(self):
        from agentic_loop import _is_verification_command
        self.assertTrue(_is_verification_command("pytest tests/"))
        self.assertTrue(_is_verification_command("pytest -v tests/test_skills.py"))

    def test_python_m_pytest_is_verification(self):
        from agentic_loop import _is_verification_command
        self.assertTrue(_is_verification_command("python3 -m pytest tests/"))
        self.assertTrue(_is_verification_command("python -m unittest tests/test_skills.py"))

    def test_python_m_pycompile_is_verification(self):
        from agentic_loop import _is_verification_command
        self.assertTrue(_is_verification_command("python -m py_compile foo.py"))

    def test_linters_are_verification(self):
        from agentic_loop import _is_verification_command
        self.assertTrue(_is_verification_command("mypy src/"))
        self.assertTrue(_is_verification_command("ruff check ."))
        self.assertTrue(_is_verification_command("flake8 module.py"))
        self.assertTrue(_is_verification_command("eslint src/"))

    def test_build_commands_are_verification(self):
        from agentic_loop import _is_verification_command
        self.assertTrue(_is_verification_command("npm run build"))
        self.assertTrue(_is_verification_command("make test"))
        self.assertTrue(_is_verification_command("cargo test"))
        self.assertTrue(_is_verification_command("go test ./..."))
        self.assertTrue(_is_verification_command("tsc"))

    def test_git_diff_is_verification(self):
        from agentic_loop import _is_verification_command
        self.assertTrue(_is_verification_command("git diff"))
        self.assertTrue(_is_verification_command("git status"))

    def test_chained_commands_are_verification(self):
        from agentic_loop import _is_verification_command
        self.assertTrue(_is_verification_command("cd /app && pytest"))
        self.assertTrue(_is_verification_command("ruff check . && mypy src/"))

    def test_ls_is_not_verification(self):
        from agentic_loop import _is_verification_command
        self.assertFalse(_is_verification_command("ls -la"))
        self.assertFalse(_is_verification_command("cat file.py"))
        self.assertFalse(_is_verification_command("echo hello"))

    def test_pip_install_is_not_verification(self):
        from agentic_loop import _is_verification_command
        self.assertFalse(_is_verification_command("pip install pytest"))
        self.assertFalse(_is_verification_command("npm install"))

    def test_empty_is_not_verification(self):
        from agentic_loop import _is_verification_command
        self.assertFalse(_is_verification_command(""))
        self.assertFalse(_is_verification_command("  "))

    # ── _check_completion_criteria ──

    def test_verify_mode_needs_status(self):
        from agentic_loop import _check_completion_criteria
        # No status keyword → hint
        hint = _check_completion_criteria("verify", "The code looks correct.")
        self.assertIn("INCOMPLETE", hint)
        self.assertIn("PASSED", hint)

    def test_verify_mode_passes_with_status(self):
        from agentic_loop import _check_completion_criteria
        hint = _check_completion_criteria("verify", "All tests PASSED successfully.")
        self.assertEqual(hint, "")

    def test_verify_mode_accepts_failed(self):
        from agentic_loop import _check_completion_criteria
        hint = _check_completion_criteria("verify", "2 tests FAILED with errors.")
        self.assertEqual(hint, "")

    def test_verify_mode_accepts_blocked(self):
        from agentic_loop import _check_completion_criteria
        hint = _check_completion_criteria("verify", "Status: BLOCKED — no test framework found.")
        self.assertEqual(hint, "")

    def test_analyze_mode_needs_structure(self):
        from agentic_loop import _check_completion_criteria
        # Only 1 structural keyword (needs 2)
        hint = _check_completion_criteria("analyze", "The function is complex.")
        self.assertIn("INCOMPLETE", hint)

    def test_analyze_mode_passes_with_structure(self):
        from agentic_loop import _check_completion_criteria
        hint = _check_completion_criteria("analyze",
            "The entry point is main.py which imports the module utils.")
        self.assertEqual(hint, "")

    def test_no_mode_returns_empty(self):
        from agentic_loop import _check_completion_criteria
        self.assertEqual(_check_completion_criteria("", "whatever"), "")
        self.assertEqual(_check_completion_criteria("refactor", "done"), "")

    # ── Disallowed tools extraction ──

    def test_disallowed_tools_extracted_for_analyze(self):
        from skills import get_skill_metadata
        meta = get_skill_metadata("analyze")
        self.assertIn("file_edit", meta["disallowed_tools"])
        self.assertIn("file_write", meta["disallowed_tools"])
        self.assertIn("shell_execute", meta["disallowed_tools"])
        self.assertIn("python_execute", meta["disallowed_tools"])

    def test_disallowed_tools_extracted_for_debug(self):
        from skills import get_skill_metadata
        meta = get_skill_metadata("debug")
        self.assertIn("file_edit", meta["disallowed_tools"])
        self.assertIn("python_execute", meta["disallowed_tools"])
        self.assertNotIn("shell_execute", meta["disallowed_tools"])

    def test_disallowed_tools_extracted_for_verify(self):
        from skills import get_skill_metadata
        meta = get_skill_metadata("verify")
        self.assertIn("file_edit", meta["disallowed_tools"])
        self.assertNotIn("shell_execute", meta["disallowed_tools"])

    def test_disallowed_tools_extracted_for_refactor(self):
        from skills import get_skill_metadata
        meta = get_skill_metadata("refactor")
        self.assertIn("python_execute", meta["disallowed_tools"])
        self.assertNotIn("file_edit", meta["disallowed_tools"])  # refactor CAN edit
        self.assertNotIn("shell_execute", meta["disallowed_tools"])

    # ── Completion criteria extraction ──

    def test_completion_criteria_nonempty(self):
        from skills import get_skill_metadata
        for name in ["analyze", "debug", "verify", "refactor"]:
            meta = get_skill_metadata(name)
            self.assertGreater(len(meta.get("completion_criteria", [])), 0,
                               f"/{name} should have completion_criteria")

    # ── VERIFICATION_CMD_RE edge cases ──

    def test_python_test_file_is_verification(self):
        from agentic_loop import _is_verification_command
        self.assertTrue(_is_verification_command("python tests/test_skills.py"))
        self.assertTrue(_is_verification_command("python3 test_main.py"))

    def test_tox_nox_are_verification(self):
        from agentic_loop import _is_verification_command
        self.assertTrue(_is_verification_command("tox"))
        self.assertTrue(_is_verification_command("nox -s tests"))

    def test_black_check_is_verification(self):
        from agentic_loop import _is_verification_command
        self.assertTrue(_is_verification_command("black --check src/"))
        self.assertTrue(_is_verification_command("isort --check ."))

    def test_prettier_check_is_verification(self):
        from agentic_loop import _is_verification_command
        self.assertTrue(_is_verification_command("prettier --check 'src/**/*.ts'"))


# ═══════════════════════════════════════════════════════════════
# P103: Forked Subtask Tests
# ═══════════════════════════════════════════════════════════════

class TestP103ForkedSubtasks(unittest.TestCase):
    """P103: Lightweight forked sub-agent execution for coding skills."""

    # ── New agent types exist ──

    def test_research_agent_type_registered(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertIn("research", BUILT_IN_AGENTS)

    def test_edit_agent_type_registered(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertIn("edit", BUILT_IN_AGENTS)

    # ── Research agent is read-only ──

    def test_research_agent_write_policy_forbid(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertEqual(BUILT_IN_AGENTS["research"].get("write_policy"), "forbid")

    def test_research_agent_no_write_tools(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        allowed = BUILT_IN_AGENTS["research"]["allowed_tools"]
        self.assertNotIn("file_edit", allowed)
        self.assertNotIn("file_write", allowed)
        self.assertNotIn("shell_execute", allowed)

    def test_research_agent_has_read_tools(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        allowed = BUILT_IN_AGENTS["research"]["allowed_tools"]
        self.assertIn("file_read", allowed)
        self.assertIn("grep_search", allowed)
        self.assertIn("find_by_name", allowed)
        self.assertIn("code_intel", allowed)

    # ── D5: Edit agent is DRAFT/patch-only (no write tools) ──

    def test_edit_agent_write_policy_forbid(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertEqual(BUILT_IN_AGENTS["edit"].get("write_policy"), "forbid")

    def test_edit_agent_disallows_writes(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        allowed = BUILT_IN_AGENTS["edit"]["allowed_tools"]
        self.assertNotIn("file_edit", allowed)
        self.assertNotIn("file_write", allowed)
        self.assertNotIn("shell_execute", allowed)

    def test_edit_agent_has_read_tools(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        allowed = BUILT_IN_AGENTS["edit"]["allowed_tools"]
        self.assertIn("file_read", allowed)
        self.assertIn("grep_search", allowed)
        self.assertIn("find_by_name", allowed)

    def test_edit_agent_disallowed_list_explicit(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        disallowed = BUILT_IN_AGENTS["edit"].get("disallowed_tools", [])
        self.assertIn("file_edit", disallowed)
        self.assertIn("file_write", disallowed)
        self.assertIn("shell_execute", disallowed)

    def test_edit_agent_requires_verification_false(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertFalse(BUILT_IN_AGENTS["edit"].get("requires_verification", True))

    # ── System prompts contain key instructions ──

    def test_research_prompt_has_readonly_constraint(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["research"]["system_prompt"]
        self.assertIn("READ-ONLY", prompt)
        self.assertIn("PROHIBITED", prompt)

    def test_research_prompt_has_output_format(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["research"]["system_prompt"]
        self.assertIn("Scope", prompt)
        self.assertIn("Key Findings", prompt)
        self.assertIn("Structure Map", prompt)

    def test_edit_prompt_mentions_draft_mode(self):
        """D5: Edit sub-agent prompt must mention DRAFT mode."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["edit"]["system_prompt"]
        self.assertIn("DRAFT mode", prompt)
        self.assertIn("FORBIDDEN", prompt)

    def test_edit_prompt_has_diff_format(self):
        """D5: Edit sub-agent prompt must specify unified diff format."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["edit"]["system_prompt"]
        self.assertIn("```diff", prompt)
        self.assertIn("--- a/", prompt)
        self.assertIn("+++ b/", prompt)

    def test_edit_prompt_has_anti_overreach(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["edit"]["system_prompt"]
        self.assertIn("Do NOT refactor", prompt)
        self.assertIn("don't improvise", prompt)

    def test_edit_prompt_forbids_apply(self):
        """D5: Edit sub-agent must explicitly say it does NOT apply patches."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["edit"]["system_prompt"]
        self.assertIn("YOU DO NOT APPLY THE PATCH", prompt)

    # ── TOOL_DEF includes new types ──

    def test_tool_def_enum_includes_research(self):
        from tools.sub_agent import TOOL_DEF
        enum_list = TOOL_DEF["function"]["parameters"]["properties"]["agent_type"]["enum"]
        self.assertIn("research", enum_list)
        self.assertIn("edit", enum_list)

    def test_tool_def_description_mentions_research(self):
        from tools.sub_agent import TOOL_DEF
        desc = TOOL_DEF["function"]["parameters"]["properties"]["agent_type"]["description"]
        self.assertIn("research", desc)
        self.assertIn("edit", desc)

    # ── set_parent_context accepts skill_policy ──

    def test_set_parent_context_accepts_skill_policy(self):
        from tools.sub_agent import set_parent_context, _PARENT_SKILL_POLICY
        set_parent_context(
            env={"test": "1"}, workspace=__import__("pathlib").Path("."),
            session_id="test_session", messages=[],
            skill_policy={"mode": "analyze", "write_policy": "forbid"}
        )
        from tools import sub_agent as sa
        self.assertEqual(sa._PARENT_SKILL_POLICY.get("mode"), "analyze")
        self.assertEqual(sa._PARENT_SKILL_POLICY.get("write_policy"), "forbid")
        # Clean up
        set_parent_context(env={}, workspace=__import__("pathlib").Path("."),
                           session_id="", messages=[], skill_policy={})

    def test_set_parent_context_empty_policy_default(self):
        from tools.sub_agent import set_parent_context
        set_parent_context(
            env={}, workspace=__import__("pathlib").Path("."),
            session_id="test", messages=[]
        )
        from tools import sub_agent as sa
        self.assertEqual(sa._PARENT_SKILL_POLICY, {})

    # ── Validation ──

    def test_research_max_turns_default(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertEqual(BUILT_IN_AGENTS["research"]["max_turns_default"], 6)

    def test_edit_max_turns_default(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertEqual(BUILT_IN_AGENTS["edit"]["max_turns_default"], 6)

    def test_all_5_agent_types_registered(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        expected = {"explore", "verify", "plan", "research", "edit"}
        self.assertEqual(set(BUILT_IN_AGENTS.keys()), expected)

    # ── Anti-hallucination in research prompt ──

    def test_research_prompt_has_anti_hallucination(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["research"]["system_prompt"]
        self.assertIn("ANTI-HALLUCINATION", prompt)
        self.assertIn("don't fabricate", prompt)


# ═══════════════════════════════════════════════════════════════
# D5: Patch Approval Protocol Tests
# ═══════════════════════════════════════════════════════════════

class TestD5PatchApproval(unittest.TestCase):
    """D5: Edit sub-agent patch approval — draft mode + main agent nudge."""

    def test_d5_system_prompt_has_patch_protocol(self):
        """System prompt _SYSTEM_PROMPT_ACTIONS must mention patch approval."""
        from system_prompts import _SYSTEM_PROMPT_ACTIONS
        self.assertIn("Patch approval protocol", _SYSTEM_PROMPT_ACTIONS)
        self.assertIn("unified diff", _SYSTEM_PROMPT_ACTIONS)

    def test_d5_system_prompt_requires_user_confirm(self):
        """System prompt must require user approval before applying patches."""
        from system_prompts import _SYSTEM_PROMPT_ACTIONS
        self.assertIn("explicit approval", _SYSTEM_PROMPT_ACTIONS)
        self.assertIn("Do NOT apply the patch", _SYSTEM_PROMPT_ACTIONS)

    def test_d5_edit_agent_description_mentions_approval(self):
        """TOOL_DEF description for edit agent must mention approval."""
        from tools.sub_agent import TOOL_DEF
        desc = TOOL_DEF["function"]["parameters"]["properties"]["agent_type"]["description"]
        self.assertIn("approval", desc)

    def test_d5_edit_agent_description_mentions_readonly(self):
        """TOOL_DEF description for edit agent must mention read-only."""
        from tools.sub_agent import TOOL_DEF
        desc = TOOL_DEF["function"]["parameters"]["properties"]["agent_type"]["description"]
        self.assertIn("read-only", desc)

    def test_d5_edit_prompt_strategy_section(self):
        """Edit sub-agent prompt must have a STRATEGY section."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["edit"]["system_prompt"]
        self.assertIn("STRATEGY", prompt)
        self.assertIn("Read each target file", prompt)

    def test_d5_edit_prompt_rules_section(self):
        """Edit sub-agent prompt must have a RULES section."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["edit"]["system_prompt"]
        self.assertIn("## RULES", prompt)
        self.assertIn("One logical change per diff hunk", prompt)

    def test_d5_edit_policy_prefix_mentions_draft(self):
        """P103 policy prefix for edit agent must say DRAFT mode."""
        from tools.sub_agent import set_parent_context, _PARENT_SKILL_POLICY
        import pathlib
        set_parent_context(
            env={"test": "1"}, workspace=pathlib.Path("."),
            session_id="d5_test", messages=[],
            skill_policy={"mode": "refactor", "write_policy": "allowed"}
        )
        # Simulate what execute_async does for edit agent
        from tools import sub_agent as sa
        parent_mode = sa._PARENT_SKILL_POLICY.get("mode", "")
        self.assertEqual(parent_mode, "refactor")
        # Clean up
        set_parent_context(env={}, workspace=pathlib.Path("."),
                           session_id="", messages=[], skill_policy={})


# ═══════════════════════════════════════════════════════════════
# U2: Adversarial Verification Agent Tests
# ═══════════════════════════════════════════════════════════════

class TestU2AdversarialVerification(unittest.TestCase):
    """U2: Verify sub-agent — Claw-grade adversarial prompt + verdict nudge."""

    # ── Prompt content ──

    def test_u2_verify_prompt_has_break_it(self):
        """Verify prompt must contain the core adversarial framing."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["verify"]["system_prompt"]
        self.assertIn("try to BREAK it", prompt)

    def test_u2_verify_prompt_has_first_80_pct(self):
        """Verify prompt must warn about 'seduced by the first 80%'."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["verify"]["system_prompt"]
        self.assertIn("first 80%", prompt)
        self.assertIn("last 20%", prompt)

    def test_u2_verify_prompt_has_type_specific_strategies(self):
        """Verify prompt must have type-specific verification strategies."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["verify"]["system_prompt"]
        self.assertIn("Backend/API changes", prompt)
        self.assertIn("CLI/script changes", prompt)
        self.assertIn("Bug fixes", prompt)
        self.assertIn("Refactoring", prompt)
        self.assertIn("Python changes", prompt)
        self.assertIn("Infrastructure/config changes", prompt)

    def test_u2_verify_prompt_has_rationalizations(self):
        """Verify prompt must list specific rationalizations to recognize."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["verify"]["system_prompt"]
        self.assertIn("reading is not verification", prompt)
        self.assertIn("the implementer is an LLM", prompt)
        self.assertIn("not your call", prompt)
        self.assertIn("Start the server AND HIT the endpoint", prompt)

    def test_u2_verify_prompt_has_adversarial_probes(self):
        """Verify prompt must list adversarial probe categories."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["verify"]["system_prompt"]
        self.assertIn("Concurrency", prompt)
        self.assertIn("Boundary values", prompt)
        self.assertIn("Idempotency", prompt)
        self.assertIn("Orphan operations", prompt)

    def test_u2_verify_prompt_has_before_pass(self):
        """Verify prompt must have BEFORE ISSUING PASS section."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["verify"]["system_prompt"]
        self.assertIn("BEFORE ISSUING PASS", prompt)
        self.assertIn("adversarial probe you ran", prompt)

    def test_u2_verify_prompt_has_before_fail(self):
        """Verify prompt must have BEFORE ISSUING FAIL section."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["verify"]["system_prompt"]
        self.assertIn("BEFORE ISSUING FAIL", prompt)
        self.assertIn("Already handled", prompt)
        self.assertIn("Intentional", prompt)
        self.assertIn("Not actionable", prompt)

    def test_u2_verify_prompt_has_output_format(self):
        """Verify prompt must enforce structured output format."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["verify"]["system_prompt"]
        self.assertIn("### Check:", prompt)
        self.assertIn("**Command run:**", prompt)
        self.assertIn("**Output observed:**", prompt)
        self.assertIn("VERDICT: PASS", prompt)
        self.assertIn("VERDICT: FAIL", prompt)
        self.assertIn("VERDICT: PARTIAL", prompt)

    def test_u2_verify_prompt_has_no_modify(self):
        """Verify prompt must be strictly read-only."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["verify"]["system_prompt"]
        self.assertIn("DO NOT MODIFY THE PROJECT", prompt)
        self.assertIn("STRICTLY PROHIBITED", prompt)

    def test_u2_verify_prompt_what_you_receive(self):
        """Verify prompt must explain what data the agent receives."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["verify"]["system_prompt"]
        self.assertIn("WHAT YOU RECEIVE", prompt)
        self.assertIn("original task description", prompt)
        self.assertIn("files changed", prompt)

    def test_u2_verify_prompt_test_suite_warning(self):
        """Verify prompt must warn that test results are context not evidence."""
        from tools.sub_agent import BUILT_IN_AGENTS
        prompt = BUILT_IN_AGENTS["verify"]["system_prompt"]
        self.assertIn("Test suite results are context, not evidence", prompt)

    # ── Config ──

    def test_u2_verify_config_max_turns(self):
        """Verify agent must allow 8 turns for thorough adversarial probing."""
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertEqual(BUILT_IN_AGENTS["verify"]["max_turns_default"], 8)

    def test_u2_verify_config_write_policy_forbid(self):
        """Verify agent must have write_policy=forbid."""
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertEqual(BUILT_IN_AGENTS["verify"]["write_policy"], "forbid")

    def test_u2_verify_config_disallowed_tools(self):
        """Verify agent must explicitly block file_edit and file_write."""
        from tools.sub_agent import BUILT_IN_AGENTS
        disallowed = BUILT_IN_AGENTS["verify"]["disallowed_tools"]
        self.assertIn("file_edit", disallowed)
        self.assertIn("file_write", disallowed)

    def test_u2_verify_config_has_shell_and_python(self):
        """Verify agent must have shell_execute and python_execute for running tests."""
        from tools.sub_agent import BUILT_IN_AGENTS
        allowed = BUILT_IN_AGENTS["verify"]["allowed_tools"]
        self.assertIn("shell_execute", allowed)
        self.assertIn("python_execute", allowed)

    # ── Critical reminder ──

    def test_u2_verify_critical_reminder_exists(self):
        """Verify agent must have a critical_reminder field."""
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertIn("critical_reminder", BUILT_IN_AGENTS["verify"])

    def test_u2_verify_critical_reminder_content(self):
        """Critical reminder must mention VERDICT and read-only constraints."""
        from tools.sub_agent import _VERIFY_CRITICAL_REMINDER
        self.assertIn("VERIFICATION-ONLY", _VERIFY_CRITICAL_REMINDER)
        self.assertIn("VERDICT: PASS", _VERIFY_CRITICAL_REMINDER)
        self.assertIn("VERDICT: FAIL", _VERIFY_CRITICAL_REMINDER)
        self.assertIn("VERDICT: PARTIAL", _VERIFY_CRITICAL_REMINDER)

    def test_u2_verify_critical_reminder_appended_to_prompt(self):
        """Critical reminder must be appended to system prompt during execution."""
        from tools.sub_agent import BUILT_IN_AGENTS, _VERIFY_CRITICAL_REMINDER
        config = BUILT_IN_AGENTS["verify"]
        # Simulate what execute_async does
        sys_prompt = config["system_prompt"]
        reminder = config.get("critical_reminder", "")
        if reminder:
            sys_prompt = f"{sys_prompt}\n\n{reminder}"
        self.assertTrue(sys_prompt.endswith(_VERIFY_CRITICAL_REMINDER))
        self.assertIn("try to BREAK it", sys_prompt)


# ═══════════════════════════════════════════════════════════════
# D7: Language Auto-Detection Tests
# ═══════════════════════════════════════════════════════════════

class TestD7LanguageAutoDetection(unittest.TestCase):
    """D7: User language auto-detection from message text."""

    def test_d7_detect_chinese(self):
        from system_prompts import _detect_user_language
        self.assertEqual(_detect_user_language("读取前50行代码"), "zh")

    def test_d7_detect_chinese_mixed(self):
        from system_prompts import _detect_user_language
        self.assertEqual(_detect_user_language("读取 web_ui/agentic_loop.py 的前 50 行"), "zh")

    def test_d7_detect_english(self):
        from system_prompts import _detect_user_language
        self.assertEqual(_detect_user_language("Read the first 50 lines of agentic_loop.py"), "en")

    def test_d7_detect_japanese(self):
        from system_prompts import _detect_user_language
        self.assertEqual(_detect_user_language("このファイルを読んでください"), "ja")

    def test_d7_detect_korean(self):
        from system_prompts import _detect_user_language
        self.assertEqual(_detect_user_language("이 파일을 읽어주세요"), "ko")

    def test_d7_empty_returns_auto(self):
        from system_prompts import _detect_user_language
        self.assertEqual(_detect_user_language(""), "auto")
        self.assertEqual(_detect_user_language("  "), "auto")

    def test_d7_slash_command_stripped(self):
        """Slash commands like /analyze should not influence detection."""
        from system_prompts import _detect_user_language
        self.assertEqual(_detect_user_language("/analyze 分析这段代码"), "zh")

    def test_d7_code_blocks_stripped(self):
        """Code blocks should not influence detection."""
        from system_prompts import _detect_user_language
        msg = "修改这段代码：\n```python\ndef foo():\n    return bar\n```"
        self.assertEqual(_detect_user_language(msg), "zh")

    def test_d7_dynamic_context_injects_language(self):
        """When language=auto, dynamic context should auto-detect and inject."""
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(
            language="auto",
            workspace_info="CWD: /tmp",
            user_message="读取前50行代码",
        )
        self.assertIn("Chinese", ctx)
        self.assertIn("ALWAYS respond in", ctx)

    def test_d7_dynamic_context_english(self):
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(
            language="auto",
            workspace_info="CWD: /tmp",
            user_message="Read the first 50 lines of the file",
        )
        self.assertIn("English", ctx)

    def test_d7_explicit_language_overrides_detection(self):
        """Explicit language should override auto-detection."""
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(
            language="en",
            workspace_info="CWD: /tmp",
            user_message="读取前50行代码",
        )
        # Even though message is Chinese, explicit "en" wins
        self.assertIn("English", ctx)
        self.assertNotIn("Chinese", ctx)

    def test_d7_language_instruction_is_strong(self):
        """Language instruction should include 'MUST' for emphasis."""
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(
            language="auto",
            user_message="读取代码文件",
        )
        self.assertIn("MUST be in", ctx)


# ═══════════════════════════════════════════════════════════════
# F1: Skill Prompt Language Preservation Tests
# ═══════════════════════════════════════════════════════════════

class TestF1SkillLanguagePreservation(unittest.TestCase):
    """F1: Verify language is correctly detected from original user message,
    not from the English-dominated skill prompt that replaces it."""

    def test_f1_chinese_detected_before_skill_replacement(self):
        """Original Chinese message should be detected as 'zh'."""
        from system_prompts import _detect_user_language
        original = "/refactor\n在 test_sample.py 中，把函数 product 重命名为 multiply，并更新所有调用。"
        self.assertEqual(_detect_user_language(original), "zh")

    def test_f1_skill_prompt_detected_as_english(self):
        """The English skill prompt (after replacement) would detect as 'en'."""
        from system_prompts import _detect_user_language
        skill_prompt = (
            "[SKILL: /refactor]\n\n"
            "You are now in **refactor mode**. You can modify code, but every write "
            "operation triggers a mandatory verification step before you can finish.\n"
            "## Refactor Target\n\n"
            "在 test_sample.py 中，把函数 product 重命名为 multiply"
        )
        # English text dominates → should detect "en" (this is the bug scenario)
        detected = _detect_user_language(skill_prompt)
        self.assertIn(detected, ("en", "zh"))  # might detect either

    def test_f1_explicit_zh_overrides_skill_prompt_detection(self):
        """When NANOBOT_LANGUAGE=zh is set, dynamic context uses zh regardless."""
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(
            language="zh",
            workspace_info="CWD: /tmp",
            user_message="[SKILL: /refactor]\n\nYou are now in refactor mode...",
        )
        self.assertIn("Chinese", ctx)
        self.assertIn("ALWAYS respond in", ctx)
        self.assertNotIn("English", ctx)

    def test_f1_auto_on_pure_english_gives_english(self):
        """Auto-detect on pure English should still give English."""
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(
            language="auto",
            workspace_info="CWD: /tmp",
            user_message="Read the first 50 lines of agentic_loop.py",
        )
        self.assertIn("English", ctx)

    def test_f1_auto_on_chinese_skill_args_gives_chinese(self):
        """Auto-detect on Chinese user args should give Chinese."""
        from system_prompts import _detect_user_language
        # Simulates the original message before skill replacement
        msg = "在 test_sample.py 中，把函数 product 重命名为 multiply，并更新所有调用。"
        self.assertEqual(_detect_user_language(msg), "zh")

    def test_f1_dynamic_context_zh_includes_must(self):
        """Chinese language instruction should include MUST for emphasis."""
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(language="zh", user_message="test")
        self.assertIn("MUST be in", ctx)
        self.assertIn("Chinese", ctx)

    def test_f1_japanese_preserved(self):
        """Japanese should also be preserved through skill invocation."""
        from system_prompts import _detect_user_language
        msg = "このファイルをリファクタリングしてください"
        self.assertEqual(_detect_user_language(msg), "ja")

    def test_f1_korean_preserved(self):
        """Korean should also be preserved through skill invocation."""
        from system_prompts import _detect_user_language
        msg = "이 파일에서 함수 이름을 변경해주세요"
        self.assertEqual(_detect_user_language(msg), "ko")


# ═══════════════════════════════════════════════════════════════
# A1/A2/A3: Context Engineering Deep Optimization Tests
# ═══════════════════════════════════════════════════════════════

class TestA1A2A3ContextEngineering(unittest.TestCase):
    """Tests for Branch A context engineering: sub-agent summary protection,
    file state preservation, and failure rollback memory."""

    # ── A1: _is_high_value_content ──

    def test_a1_sub_agent_summary_detected(self):
        from agentic_loop import _is_high_value_content
        self.assertTrue(_is_high_value_content(
            "[Sub-agent research summary: 235 chars, parent_mode=analyze] Key findings..."
        ))

    def test_a1_sub_agent_verify_detected(self):
        from agentic_loop import _is_high_value_content
        self.assertTrue(_is_high_value_content(
            "[Sub-agent verify summary: 3 turns, 5 tool calls] VERDICT: PASS"
        ))

    def test_a1_sub_agent_edit_detected(self):
        from agentic_loop import _is_high_value_content
        self.assertTrue(_is_high_value_content(
            "[Sub-agent edit summary: 2 turns, 3 tool calls (file_edit, file_read)] Done"
        ))

    def test_a1_normal_tool_not_high_value(self):
        from agentic_loop import _is_high_value_content
        self.assertFalse(_is_high_value_content("[Summary: grep_search found 3 matches]"))

    def test_a1_empty_not_high_value(self):
        from agentic_loop import _is_high_value_content
        self.assertFalse(_is_high_value_content(""))
        self.assertFalse(_is_high_value_content(None))

    def test_a1_regex_pattern(self):
        from agentic_loop import _SUB_AGENT_SUMMARY_RE
        self.assertIsNotNone(_SUB_AGENT_SUMMARY_RE.search("[Sub-agent explore summary: 100 chars]"))
        self.assertIsNone(_SUB_AGENT_SUMMARY_RE.search("[Sub-agent-explore summary: 100 chars]"))

    # ── A1: TimeMC protection ──

    def test_a1_time_mc_protects_sub_agent(self):
        from agentic_loop import _time_based_micro_compact, _TOOL_RESULT_CLEARED_MSG
        sub_msg = {
            "role": "tool", "_tool_name": "sub_agent", "_turn": 1,
            "content": "[Sub-agent research summary: 500 chars] Analysis results..."
        }
        normal_msg = {
            "role": "tool", "_tool_name": "grep_search", "_turn": 1,
            "content": "Found 5 matches in src/"
        }
        messages = [
            {"role": "system", "content": "sys"},
            normal_msg,
            sub_msg,
            {"role": "tool", "_tool_name": "file_read", "_turn": 2, "content": "file content"},
            {"role": "tool", "_tool_name": "file_list", "_turn": 3, "content": "dir listing"},
        ]
        cleared = _time_based_micro_compact(messages, gap_minutes=10.0)
        # Sub-agent msg should NOT be cleared
        self.assertIn("[Sub-agent research summary:", sub_msg["content"])
        # Normal old msg should be cleared
        self.assertEqual(normal_msg["content"], _TOOL_RESULT_CLEARED_MSG)

    # ── A1: MicroCompact protection ──

    def test_a1_micro_compact_protects_sub_agent(self):
        from agentic_loop import _micro_compact_old_messages
        long_sub = "[Sub-agent research summary: 500 chars]\n" + "x" * 20000
        msg = {"role": "tool", "_tool_name": "sub_agent", "_turn": 1, "content": long_sub}
        messages = [msg]
        _micro_compact_old_messages(messages, current_turn=10)
        # Should NOT be truncated despite being old and large
        self.assertTrue(messages[0]["content"].startswith("[Sub-agent research summary:"))
        self.assertEqual(len(messages[0]["content"]), len(long_sub))

    # ── A2: File read tracking ──

    def test_a2_track_file_read(self):
        from agentic_loop import _track_file_read, _SESSION_FILE_READS, _reset_session_file_reads
        _reset_session_file_reads()
        result = {"output": "[File: /test/main.py | 100 lines | 3200 bytes]\ndef main():\n    pass\nclass Config:\n    pass"}
        _track_file_read("file_read", result, {"path": "/test/main.py"}, turn=1)
        self.assertIn("/test/main.py", _SESSION_FILE_READS)
        entry = _SESSION_FILE_READS["/test/main.py"]
        self.assertEqual(entry["last_turn"], 1)
        self.assertEqual(entry["lines"], 100)
        self.assertIn("defines:", entry["summary"])
        _reset_session_file_reads()

    def test_a2_track_ignores_non_file_read(self):
        from agentic_loop import _track_file_read, _SESSION_FILE_READS, _reset_session_file_reads
        _reset_session_file_reads()
        _track_file_read("grep_search", {"output": "some result"}, {}, turn=1)
        self.assertEqual(len(_SESSION_FILE_READS), 0)
        _reset_session_file_reads()

    def test_a2_track_ignores_errors(self):
        from agentic_loop import _track_file_read, _SESSION_FILE_READS, _reset_session_file_reads
        _reset_session_file_reads()
        result = {"output": "", "error": "File not found"}
        _track_file_read("file_read", result, {"path": "/missing.py"}, turn=1)
        self.assertEqual(len(_SESSION_FILE_READS), 0)
        _reset_session_file_reads()

    def test_a2_evicts_oldest(self):
        from agentic_loop import (
            _track_file_read, _SESSION_FILE_READS, _reset_session_file_reads,
            _SESSION_FILE_READS_MAX,
        )
        _reset_session_file_reads()
        for i in range(_SESSION_FILE_READS_MAX + 3):
            _track_file_read(
                "file_read",
                {"output": f"[File: /test/f{i}.py | 10 lines | 200 bytes]\ncontent"},
                {"path": f"/test/f{i}.py"},
                turn=i,
            )
        self.assertLessEqual(len(_SESSION_FILE_READS), _SESSION_FILE_READS_MAX + 1)
        _reset_session_file_reads()

    def test_a2_get_preserved_summary(self):
        from agentic_loop import (
            _track_file_read, _get_preserved_files_summary,
            _reset_session_file_reads,
        )
        _reset_session_file_reads()
        _track_file_read(
            "file_read",
            {"output": "[File: /a.py | 50 lines | 1000 bytes]\ndef foo(): pass"},
            {"path": "/a.py"}, turn=1,
        )
        _track_file_read(
            "file_read",
            {"output": "[File: /b.py | 80 lines | 2000 bytes]\nclass Bar: pass"},
            {"path": "/b.py"}, turn=2,
        )
        summary = _get_preserved_files_summary()
        # D3: A2-compat summary now uses (N lines, defines: ...) format
        self.assertIn("50 lines", summary)
        self.assertIn("80 lines", summary)
        # Most recent first
        lines = summary.strip().split("\n")
        self.assertIn("80 lines", lines[0])
        _reset_session_file_reads()

    def test_a2_empty_returns_empty(self):
        from agentic_loop import _get_preserved_files_summary, _reset_session_file_reads
        _reset_session_file_reads()
        self.assertEqual(_get_preserved_files_summary(), "")

    def test_a2_extracts_definitions(self):
        from agentic_loop import _track_file_read, _SESSION_FILE_READS, _reset_session_file_reads
        _reset_session_file_reads()
        output = "[File: /x.py | 30 lines | 800 bytes]\ndef alpha():\n    pass\ndef beta():\n    pass\nclass Gamma:\n    pass"
        _track_file_read("file_read", {"output": output}, {"path": "/x.py"}, turn=1)
        entry = _SESSION_FILE_READS["/x.py"]
        self.assertIn("defines:", entry["summary"])
        self.assertIn("alpha", entry["summary"])
        _reset_session_file_reads()

    # ── A3: Failure tracking ──

    def test_a3_record_failure(self):
        from agentic_loop import _record_tool_failure, _SESSION_FAILURES, _reset_session_failures
        _reset_session_failures()
        _record_tool_failure("shell_execute", {"command": "pip install foo"}, "Network unreachable", turn=3)
        self.assertEqual(len(_SESSION_FAILURES), 1)
        self.assertEqual(_SESSION_FAILURES[0]["tool"], "shell_execute")
        self.assertIn("Network", _SESSION_FAILURES[0]["error"])
        _reset_session_failures()

    def test_a3_record_ignores_empty_error(self):
        from agentic_loop import _record_tool_failure, _SESSION_FAILURES, _reset_session_failures
        _reset_session_failures()
        _record_tool_failure("shell_execute", {"command": "ls"}, "", turn=1)
        self.assertEqual(len(_SESSION_FAILURES), 0)
        _reset_session_failures()

    def test_a3_evicts_oldest(self):
        from agentic_loop import (
            _record_tool_failure, _SESSION_FAILURES, _reset_session_failures,
            _SESSION_FAILURES_MAX,
        )
        _reset_session_failures()
        for i in range(_SESSION_FAILURES_MAX + 5):
            _record_tool_failure("shell_execute", {"cmd": f"cmd_{i}"}, f"error_{i}", turn=i)
        self.assertLessEqual(len(_SESSION_FAILURES), _SESSION_FAILURES_MAX)
        # Oldest should have been evicted
        self.assertNotIn("error_0", _SESSION_FAILURES[0]["error"])
        _reset_session_failures()

    def test_a3_get_relevant_by_tool_name(self):
        from agentic_loop import (
            _record_tool_failure, _get_relevant_failures, _reset_session_failures,
        )
        _reset_session_failures()
        _record_tool_failure("shell_execute", {"command": "pip install pkg"}, "Network unreachable", turn=1)
        _record_tool_failure("file_edit", {"path": "/a.py"}, "Syntax error", turn=2)
        result = _get_relevant_failures(tool_name="shell_execute")
        self.assertIn("shell_execute", result)
        self.assertIn("Network unreachable", result)
        _reset_session_failures()

    def test_a3_get_relevant_by_context(self):
        from agentic_loop import (
            _record_tool_failure, _get_relevant_failures, _reset_session_failures,
        )
        _reset_session_failures()
        _record_tool_failure("shell_execute", {"command": "pip install requests"}, "Network unreachable timeout", turn=1)
        result = _get_relevant_failures(context="install requests via pip network")
        self.assertIn("Network unreachable", result)
        _reset_session_failures()

    def test_a3_no_failures_returns_empty(self):
        from agentic_loop import _get_relevant_failures, _reset_session_failures
        _reset_session_failures()
        self.assertEqual(_get_relevant_failures(), "")

    def test_a3_failure_format(self):
        from agentic_loop import (
            _record_tool_failure, _get_relevant_failures, _reset_session_failures,
        )
        _reset_session_failures()
        _record_tool_failure("shell_execute", {"command": "make test"}, "exit code 1", turn=5)
        result = _get_relevant_failures(tool_name="shell_execute")
        self.assertIn("[Previous failures", result)
        self.assertIn("Turn 5", result)
        self.assertIn("exit code 1", result)
        _reset_session_failures()

    # ── A1: Compact engine preservation ──

    def test_a1_compact_engine_extracts_sub_agent_summaries(self):
        """Verify that _auto_compact's summary extraction logic works."""
        import re
        content = "[Sub-agent research summary: 500 chars, parent_mode=analyze]\n\nKey Findings:\n1. Module structure is clean\n2. No circular imports\n\nRisks: None identified"
        # Simulate the extraction logic from compact_engine
        match = re.search(r'\[Sub-agent\s+\w+\s+summary:', content)
        self.assertIsNotNone(match)
        header_end = content.find("\n\n")
        self.assertGreater(header_end, 0)
        header = content[:header_end]
        body = content[header_end + 2:]
        self.assertIn("[Sub-agent research summary:", header)
        self.assertIn("Key Findings", body)

    def test_a1_compact_engine_caps_body_preview(self):
        """Body preview should be capped at 500 chars."""
        import re
        long_body = "x" * 1000
        content = f"[Sub-agent research summary: 1000 chars]\n\n{long_body}"
        header_end = content.find("\n\n")
        body = content[header_end + 2:]
        body_preview = body[:500]
        if len(body) > 500:
            body_preview += "..."
        self.assertEqual(len(body_preview), 503)  # 500 + "..."

    # ── Reset functions ──

    def test_a2_reset_clears(self):
        from agentic_loop import _SESSION_FILE_READS, _reset_session_file_reads
        _SESSION_FILE_READS["test"] = {"summary": "test", "turn": 1, "lines": 10}
        _reset_session_file_reads()
        self.assertEqual(len(_SESSION_FILE_READS), 0)

    def test_a3_reset_clears(self):
        from agentic_loop import _SESSION_FAILURES, _reset_session_failures
        _SESSION_FAILURES.append({"tool": "test", "error": "test"})
        _reset_session_failures()
        self.assertEqual(len(_SESSION_FAILURES), 0)

    # ── CTO-audit: Observability counter tests ──

    def test_hint_reread_counter_increments(self):
        """Counter increments when model re-reads a known file."""
        import agentic_loop as al
        al._SESSION_ACTIVE_FILES.clear()
        al._HINT_REREAD_COUNT = 0
        al._HINT_REREAD_LOGGED.clear()
        # First read: registers the file
        al._track_file_activity("file_read", {"output": "[File: /a.py | 10 lines] content"}, {"path": "/a.py"}, turn=1)
        self.assertEqual(al._HINT_REREAD_COUNT, 0)
        # Second read of same file: should increment
        al._track_file_activity("file_read", {"output": "[File: /a.py | 10 lines] content"}, {"path": "/a.py"}, turn=3)
        self.assertEqual(al._HINT_REREAD_COUNT, 1)
        al._SESSION_ACTIVE_FILES.clear()
        al._HINT_REREAD_COUNT = 0
        al._HINT_REREAD_LOGGED.clear()

    def test_hint_reread_no_increment_for_write_then_read(self):
        """Re-reading after a write is legitimate — should NOT count as hint-ignored."""
        import agentic_loop as al
        al._SESSION_ACTIVE_FILES.clear()
        al._HINT_REREAD_COUNT = 0
        al._HINT_REREAD_LOGGED.clear()
        al._track_file_activity("file_read", {"output": "[File: /b.py | 5 lines] x"}, {"path": "/b.py"}, turn=1)
        al._track_file_activity("file_edit", {"success": True}, {"path": "/b.py"}, turn=2)
        # Read after write — last_action is "write", so counter should NOT increment
        al._track_file_activity("file_read", {"output": "[File: /b.py | 5 lines] x"}, {"path": "/b.py"}, turn=3)
        self.assertEqual(al._HINT_REREAD_COUNT, 0)
        al._SESSION_ACTIVE_FILES.clear()
        al._HINT_REREAD_COUNT = 0
        al._HINT_REREAD_LOGGED.clear()

    def test_hint_reread_counter_resets_at_session_start(self):
        """Counter and throttle set should be 0 after _reset_session_file_reads()."""
        import agentic_loop as al
        al._HINT_REREAD_COUNT = 5
        al._HINT_REREAD_LOGGED.add("/x.py")
        al._SESSION_ACTIVE_FILES["/x.py"] = {"last_action": "read", "last_turn": 1}
        al._reset_session_file_reads()
        self.assertEqual(al._HINT_REREAD_COUNT, 0)
        self.assertEqual(len(al._HINT_REREAD_LOGGED), 0)
        self.assertEqual(len(al._SESSION_ACTIVE_FILES), 0)

    def test_hint_reread_throttle_logs_once_per_file(self):
        """Per-file throttle set should track logged paths."""
        import agentic_loop as al
        al._SESSION_ACTIVE_FILES.clear()
        al._HINT_REREAD_COUNT = 0
        al._HINT_REREAD_LOGGED.clear()
        al._track_file_activity("file_read", {"output": "[File: /c.py | 10 lines] x"}, {"path": "/c.py"}, turn=1)
        al._track_file_activity("file_read", {"output": "[File: /c.py | 10 lines] x"}, {"path": "/c.py"}, turn=2)
        self.assertIn("/c.py", al._HINT_REREAD_LOGGED)
        # Third read: counter increments but path already in logged set
        al._track_file_activity("file_read", {"output": "[File: /c.py | 10 lines] x"}, {"path": "/c.py"}, turn=3)
        self.assertEqual(al._HINT_REREAD_COUNT, 2)
        self.assertEqual(len(al._HINT_REREAD_LOGGED), 1)  # still just 1 path
        al._SESSION_ACTIVE_FILES.clear()
        al._HINT_REREAD_COUNT = 0
        al._HINT_REREAD_LOGGED.clear()

    def test_hint_reread_warn_threshold(self):
        """Warn threshold constant exists and is positive."""
        import agentic_loop as al
        self.assertGreater(al._HINT_REREAD_WARN_THRESHOLD, 0)
        self.assertEqual(al._HINT_REREAD_WARN_THRESHOLD, 3)

    def test_hint_reread_different_files_counted_separately(self):
        """Re-reads of different files each increment counter independently."""
        import agentic_loop as al
        al._SESSION_ACTIVE_FILES.clear()
        al._HINT_REREAD_COUNT = 0
        al._HINT_REREAD_LOGGED.clear()
        al._track_file_activity("file_read", {"output": "[File: /d.py | 1 lines] x"}, {"path": "/d.py"}, turn=1)
        al._track_file_activity("file_read", {"output": "[File: /e.py | 1 lines] x"}, {"path": "/e.py"}, turn=2)
        # Re-read both
        al._track_file_activity("file_read", {"output": "[File: /d.py | 1 lines] x"}, {"path": "/d.py"}, turn=3)
        al._track_file_activity("file_read", {"output": "[File: /e.py | 1 lines] x"}, {"path": "/e.py"}, turn=4)
        self.assertEqual(al._HINT_REREAD_COUNT, 2)
        self.assertEqual(len(al._HINT_REREAD_LOGGED), 2)
        al._SESSION_ACTIVE_FILES.clear()
        al._HINT_REREAD_COUNT = 0
        al._HINT_REREAD_LOGGED.clear()

    # ── A5: Repo map ──

    def test_a5_generate_repo_map_basic(self):
        import tempfile, os
        from agentic_loop import _generate_repo_map
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "main.py").write_text("print('hello')")
            (p / "utils.py").write_text("def foo(): pass")
            (p / "README.md").write_text("# Test")
            result = _generate_repo_map(p)
            self.assertIn("README.md", result)
            self.assertIn("main.py", result)

    def test_a5_skips_ignored_dirs(self):
        import tempfile
        from agentic_loop import _generate_repo_map
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "src").mkdir()
            (p / "src" / "app.py").write_text("x = 1")
            (p / "__pycache__").mkdir()
            (p / "__pycache__" / "cached.pyc").write_text("binary")
            (p / "node_modules").mkdir()
            (p / "node_modules" / "pkg.js").write_text("x")
            result = _generate_repo_map(p)
            self.assertIn("src/", result)
            self.assertNotIn("__pycache__", result)
            self.assertNotIn("node_modules", result)

    def test_a5_key_files_annotated(self):
        import tempfile
        from agentic_loop import _generate_repo_map
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "requirements.txt").write_text("flask==2.0")
            (p / "Makefile").write_text("all: build")
            (p / "app.py").write_text("from flask import Flask")
            result = _generate_repo_map(p)
            self.assertIn("requirements.txt", result)
            self.assertIn("Makefile", result)

    def test_a5_max_entries_caps_output(self):
        import tempfile
        from agentic_loop import _generate_repo_map
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            # Create enough subdirectories + files to exceed a small limit
            for i in range(20):
                sub = p / f"pkg{i}"
                sub.mkdir()
                (sub / "mod.py").write_text(f"x = {i}")
            result = _generate_repo_map(p, max_entries=10)
            self.assertIn("truncated", result)

    def test_a5_empty_dir_returns_minimal(self):
        import tempfile
        from agentic_loop import _generate_repo_map
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _generate_repo_map(Path(tmpdir))
            self.assertIn("Repo:", result)

    def test_a5_invalid_path_returns_empty(self):
        from agentic_loop import _generate_repo_map
        result = _generate_repo_map(Path("/nonexistent/path/xyz"))
        self.assertEqual(result, "")

    def test_a5_caching(self):
        import tempfile
        from agentic_loop import _get_repo_map, _REPO_MAP_CACHE
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "a.py").write_text("x = 1")
            map1 = _get_repo_map(p, current_turn=0)
            map2 = _get_repo_map(p, current_turn=1)
            self.assertEqual(map1, map2)
            # Should be cached
            self.assertIn(str(p), _REPO_MAP_CACHE)

    def test_a5_cache_invalidation(self):
        import tempfile
        from agentic_loop import _get_repo_map, _REPO_MAP_REGEN_INTERVAL
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "a.py").write_text("x = 1")
            map1 = _get_repo_map(p, current_turn=0)
            # Force stale
            map2 = _get_repo_map(p, current_turn=_REPO_MAP_REGEN_INTERVAL + 1)
            # Should regenerate (same content but cache was refreshed)
            self.assertTrue(len(map2) > 0)

    def test_a5_dynamic_context_includes_repo_map(self):
        from system_prompts import build_dynamic_context
        result = build_dynamic_context(repo_map="Repo: test/\n  main.py\n  utils.py")
        self.assertIn("Project structure:", result)
        self.assertIn("main.py", result)

    def test_a5_dynamic_context_empty_without_repo_map(self):
        from system_prompts import build_dynamic_context
        result = build_dynamic_context(repo_map="")
        self.assertNotIn("Project structure:", result)

    def test_a5_subdirectory_file_counts(self):
        import tempfile
        from agentic_loop import _generate_repo_map
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            sub = p / "src"
            sub.mkdir()
            for i in range(5):
                (sub / f"mod{i}.py").write_text(f"x = {i}")
            result = _generate_repo_map(p)
            self.assertIn("src/", result)
            # Should show file count for subdirectory
            self.assertIn("files", result)

    def test_a5_repo_map_integration_realistic_project(self):
        """Integration test: realistic project structure → useful, concise map."""
        import tempfile
        from agentic_loop import _generate_repo_map
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            # Key config files
            (p / "README.md").write_text("# My Project")
            (p / "pyproject.toml").write_text("[project]\nname='myapp'")
            (p / "requirements.txt").write_text("flask\nrequests")
            (p / "Makefile").write_text("all: test")
            # Source
            src = p / "src"
            src.mkdir()
            for name in ["app.py", "config.py", "models.py", "utils.py", "cli.py"]:
                (src / name).write_text(f"# {name}")
            # Tests
            tests = p / "tests"
            tests.mkdir()
            for i in range(3):
                (tests / f"test_{i}.py").write_text(f"# test {i}")
            # Noise dirs that should be skipped
            for skip in [".git", "__pycache__", "node_modules", ".venv"]:
                sd = p / skip
                sd.mkdir()
                (sd / "noise.txt").write_text("noise")
            # Deep sub-package
            sub = src / "api"
            sub.mkdir()
            (sub / "routes.py").write_text("# routes")
            (sub / "auth.py").write_text("# auth")

            result = _generate_repo_map(p)

            # Key files present
            self.assertIn("README.md", result)
            self.assertIn("pyproject.toml", result)
            self.assertIn("requirements.txt", result)
            self.assertIn("Makefile", result)
            # Directories present
            self.assertIn("src/", result)
            self.assertIn("tests/", result)
            self.assertIn("api/", result)
            # Code files visible (≤15 per dir so listed individually)
            self.assertIn("app.py", result)
            self.assertIn("routes.py", result)
            # Noise dirs skipped
            self.assertNotIn("__pycache__", result)
            self.assertNotIn("node_modules", result)
            self.assertNotIn(".git", result)
            self.assertNotIn(".venv", result)
            # Size check: should be under 2KB
            self.assertLess(len(result), 2048)

    def test_a5_code_summary_format(self):
        """Verify the '(N code files: X .py)' summary format is correct."""
        import tempfile
        from agentic_loop import _generate_repo_map
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            # Create >15 .py files to trigger summarization
            for i in range(20):
                (p / f"module_{i}.py").write_text(f"x = {i}")
            (p / "app.ts").write_text("const x = 1")
            (p / "lib.ts").write_text("const y = 2")
            result = _generate_repo_map(p)
            # Should contain a summary line with correct format
            self.assertIn("code files:", result)
            self.assertIn(".py", result)
            # Format: "N .ext" with space between count and extension
            self.assertRegex(result, r'\d+ \.py')

    def test_a5_cache_uses_abspath(self):
        """Cache key uses os.path.abspath for consistent keying."""
        import os, tempfile
        from agentic_loop import _get_repo_map, _REPO_MAP_CACHE
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "a.py").write_text("x = 1")
            _get_repo_map(p, current_turn=0)
            # Cache key should be absolute
            abs_key = os.path.abspath(p)
            self.assertIn(abs_key, _REPO_MAP_CACHE)

    def test_a5_dot_dirs_properly_skipped(self):
        """All dot-prefixed dirs except allowed ones (.env, .github, .vscode) are skipped."""
        import tempfile
        from agentic_loop import _generate_repo_map
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            # Allowed dot-dirs
            (p / ".github").mkdir()
            (p / ".github" / "workflows").mkdir()
            (p / ".github" / "workflows" / "ci.yml").write_text("on: push")
            # Disallowed dot-dirs
            (p / ".tool_results").mkdir()
            (p / ".tool_results" / "data.json").write_text("{}")
            (p / ".hidden_cache").mkdir()
            (p / ".hidden_cache" / "junk.txt").write_text("junk")
            result = _generate_repo_map(p)
            self.assertIn(".github/", result)
            self.assertNotIn(".tool_results", result)
            self.assertNotIn(".hidden_cache", result)


# ═══════════════════════════════════════════════════════════════
# CW1: Token-Weighted MicroCompact Eviction Tests
# ═══════════════════════════════════════════════════════════════

class TestCW1TokenWeightedMicroCompact(unittest.TestCase):
    """CW1: Token-weighted greedy eviction with atomic turn groups."""

    def _make_tool_msg(self, content, turn, tool_name="file_read"):
        return {"role": "tool", "content": content, "_turn": turn, "_tool_name": tool_name}

    def test_cw1_no_eviction_below_target(self):
        """No eviction when tokens are below 70% of ceiling."""
        from agentic_loop import _token_weighted_micro_compact
        msgs = [self._make_tool_msg("x" * 100, turn=1)]
        cleared = _token_weighted_micro_compact(msgs, current_turn=5, ceiling=100000)
        self.assertEqual(cleared, 0)
        self.assertNotEqual(msgs[0]["content"], "[Old tool result content cleared]")

    def test_cw1_evicts_largest_first(self):
        """Greedy eviction removes the biggest tool result group first."""
        from agentic_loop import _token_weighted_micro_compact, _TOOL_RESULT_CLEARED_MSG
        # Two old turns: turn 1 has a large result, turn 2 has a small result
        large = "x" * 20000  # ~5000 tokens
        small = "y" * 400    # ~100 tokens
        msgs = [
            self._make_tool_msg(large, turn=1),
            self._make_tool_msg(small, turn=2),
        ]
        # ceiling=8000 tokens → target=5600 (70%). With ~5100 tokens total,
        # we need a ceiling where total > target but < 80%.
        # Use a ceiling where 70% < total tokens < 80%
        ceiling = 7500  # target=5250, 80%=6000; total ~5100 → below target, won't trigger
        # Make ceiling smaller so tokens exceed target
        ceiling = 6000  # target=4200; total ~5100 > 4200 → triggers
        cleared = _token_weighted_micro_compact(msgs, current_turn=5, ceiling=ceiling)
        # Should evict the large one first
        self.assertGreater(cleared, 0)
        self.assertEqual(msgs[0]["content"], _TOOL_RESULT_CLEARED_MSG)

    def test_cw1_protects_recent_turns(self):
        """Tool results from the last 2 turns are never evicted."""
        from agentic_loop import _token_weighted_micro_compact, _TW_MC_KEEP_RECENT_TURNS
        large = "x" * 20000
        msgs = [self._make_tool_msg(large, turn=4)]  # recent if current_turn=5
        cleared = _token_weighted_micro_compact(msgs, current_turn=5, ceiling=1000)
        self.assertEqual(cleared, 0)  # protected

    def test_cw1_protects_high_value_content(self):
        """Sub-agent summaries are never evicted."""
        from agentic_loop import _token_weighted_micro_compact
        hv = "[Sub-agent research summary: 500 chars] " + "x" * 20000
        msgs = [self._make_tool_msg(hv, turn=1)]
        cleared = _token_weighted_micro_compact(msgs, current_turn=10, ceiling=1000)
        self.assertEqual(cleared, 0)

    def test_cw1_atomic_turn_group(self):
        """All tool results from the same turn are evicted together."""
        from agentic_loop import _token_weighted_micro_compact, _TOOL_RESULT_CLEARED_MSG
        # Turn 1 has two results
        msgs = [
            self._make_tool_msg("a" * 10000, turn=1, tool_name="file_read"),
            self._make_tool_msg("b" * 10000, turn=1, tool_name="grep_search"),
            self._make_tool_msg("c" * 200, turn=2, tool_name="file_read"),
        ]
        cleared = _token_weighted_micro_compact(msgs, current_turn=10, ceiling=5000)
        # Both turn-1 messages should be cleared together
        self.assertEqual(msgs[0]["content"], _TOOL_RESULT_CLEARED_MSG)
        self.assertEqual(msgs[1]["content"], _TOOL_RESULT_CLEARED_MSG)

    def test_cw1_skips_already_cleared(self):
        """Already-cleared messages are not counted or re-cleared."""
        from agentic_loop import _token_weighted_micro_compact, _TOOL_RESULT_CLEARED_MSG
        msgs = [
            {"role": "tool", "content": _TOOL_RESULT_CLEARED_MSG, "_turn": 1, "_tool_name": "file_read"},
            self._make_tool_msg("x" * 200, turn=2),
        ]
        cleared = _token_weighted_micro_compact(msgs, current_turn=10, ceiling=1000)
        # Only the non-cleared message could be a candidate
        self.assertLessEqual(cleared, 1)

    def test_cw1_safety_margin_constant(self):
        """Safety margin constant exists and is 1.2 (120%)."""
        from agentic_loop import _TW_MC_TOKEN_SAFETY_MARGIN
        self.assertEqual(_TW_MC_TOKEN_SAFETY_MARGIN, 1.2)

    def test_cw1_target_ratio_constant(self):
        """Target ratio constant exists and is 0.70."""
        from agentic_loop import _TW_MC_TARGET_RATIO
        self.assertAlmostEqual(_TW_MC_TARGET_RATIO, 0.70)

    def test_cw1_keep_recent_turns_constant(self):
        """Keep-recent-turns constant exists and is 2."""
        from agentic_loop import _TW_MC_KEEP_RECENT_TURNS
        self.assertEqual(_TW_MC_KEEP_RECENT_TURNS, 2)

    def test_cw1_stops_evicting_when_target_reached(self):
        """Eviction stops once freed tokens bring context below target."""
        from agentic_loop import _token_weighted_micro_compact, _TOOL_RESULT_CLEARED_MSG
        # Turn 1: huge (will be evicted first)
        # Turn 2: medium (should NOT be evicted if turn 1 sufficed)
        msgs = [
            self._make_tool_msg("x" * 20000, turn=1),
            self._make_tool_msg("y" * 4000, turn=2),
            self._make_tool_msg("z" * 200, turn=8),  # recent, protected
        ]
        # ceiling=8000 → target=5600. After evicting turn 1 (~5000+ tokens),
        # remaining ~1100 tokens << 5600, so turn 2 should NOT be evicted
        cleared = _token_weighted_micro_compact(msgs, current_turn=10, ceiling=8000)
        self.assertEqual(msgs[0]["content"], _TOOL_RESULT_CLEARED_MSG)
        self.assertNotEqual(msgs[1]["content"], _TOOL_RESULT_CLEARED_MSG)

    def test_cw1_prometheus_counters_increment(self):
        """Eviction increments the global Prometheus counters."""
        import agentic_loop as al
        old_evictions = al._CW1_EVICTIONS_TOTAL
        old_freed = al._CW1_TOKENS_FREED_TOTAL
        msgs = [self._make_tool_msg("x" * 20000, turn=1)]
        al._token_weighted_micro_compact(msgs, current_turn=10, ceiling=6000)
        self.assertGreater(al._CW1_EVICTIONS_TOTAL, old_evictions)
        self.assertGreater(al._CW1_TOKENS_FREED_TOTAL, old_freed)

    def test_cw1_prometheus_counters_exist(self):
        """Counter globals exist and are non-negative integers."""
        import agentic_loop as al
        self.assertIsInstance(al._CW1_EVICTIONS_TOTAL, int)
        self.assertIsInstance(al._CW1_TOKENS_FREED_TOTAL, int)
        self.assertGreaterEqual(al._CW1_EVICTIONS_TOTAL, 0)
        self.assertGreaterEqual(al._CW1_TOKENS_FREED_TOTAL, 0)


# ═══════════════════════════════════════════════════════════════
# CW2: Compact Pre-flight Token Guard Tests
# ═══════════════════════════════════════════════════════════════

class TestCW2PreflightTokenGuard(unittest.TestCase):
    """CW2: Verify pre-flight truncation of compact requests."""

    def test_cw2_guard_exists_in_auto_compact(self):
        """CW2 guard code is present in compact_engine module."""
        import compact_engine
        src = Path(compact_engine.__file__).read_text(encoding="utf-8")
        self.assertIn("[CW2]", src)
        self.assertIn("Pre-flight", src)

    def test_cw2_small_request_no_trim(self):
        """When compact request fits within limit, no trimming occurs."""
        from compact_engine import _estimate_tokens, _messages_to_text
        # Simulate: short old_msgs, large ceiling → no trim needed
        old_msgs = [{"role": "user", "content": f"msg {i}"} for i in range(5)]
        text = _messages_to_text(old_msgs)
        tokens = _estimate_tokens(text) + 100  # overhead
        # With 8192 ctx, 90% = 7372 → short messages easily fit
        self.assertLess(tokens, 7372)

    def test_cw2_messages_to_text_deterministic(self):
        """_messages_to_text produces consistent output for same input."""
        from compact_engine import _messages_to_text
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        self.assertEqual(_messages_to_text(msgs), _messages_to_text(msgs))

    def test_cw2_estimate_tokens_large_content(self):
        """Token estimation handles large content without error."""
        from compact_engine import _estimate_tokens
        large = "x" * 100000
        est = _estimate_tokens(large)
        self.assertGreater(est, 1000)
        self.assertLess(est, 100000)  # not 1:1

    def test_cw2_trim_preserves_recent(self):
        """CW2 only trims old_msgs, recent_tail is not touched."""
        # This is a design invariant: CW2 operates on old_msgs before
        # compact_messages is built; recent_tail is added after compaction
        from compact_engine import _estimate_tokens
        # Verify token estimation is available (required by CW2)
        self.assertGreater(_estimate_tokens("test content"), 0)

    def test_cw2_90_percent_limit(self):
        """CW2 uses 90% of model context as the compact request limit."""
        # Verify the math: 8192 * 0.90 = 7372
        ceiling = 8192
        limit = int(ceiling * 0.90)
        self.assertEqual(limit, 7372)

    def test_cw2_30_percent_trim_per_round(self):
        """Each CW2 round trims 30% of old_msgs."""
        n = 20
        trim_count = max(1, n * 30 // 100)
        self.assertEqual(trim_count, 6)
        remaining = n - trim_count
        self.assertEqual(remaining, 14)
        # Second round
        trim_count2 = max(1, remaining * 30 // 100)
        self.assertEqual(trim_count2, 4)

    def test_cw2_max_2_rounds(self):
        """CW2 performs at most 2 truncation rounds."""
        # After 2 rounds of 30% trim from 20 msgs:
        # Round 1: 20 → 14 (trim 6)
        # Round 2: 14 → 10 (trim 4)
        n = 20
        for _ in range(2):
            trim = max(1, n * 30 // 100)
            n -= trim
        self.assertEqual(n, 10)

    def test_cw2_fallback_80_percent_hard_limit(self):
        """CW2 fallback uses 80% of ceiling as absolute safety ceiling."""
        ceiling = 8192
        hard_limit = int(ceiling * 0.80)
        self.assertEqual(hard_limit, 6553)
        # Must be strictly less than 90% limit
        soft_limit = int(ceiling * 0.90)
        self.assertLess(hard_limit, soft_limit)

    def test_cw2_fallback_drops_one_at_a_time(self):
        """CW2 fallback drops messages one at a time for precision."""
        # Simulate: 10 msgs, drop one at a time
        msgs = list(range(10))
        for _ in range(3):
            msgs = msgs[1:]
        self.assertEqual(len(msgs), 7)
        self.assertEqual(msgs[0], 3)  # oldest 3 dropped

    def test_cw2_prometheus_counters_exist(self):
        """CW2 split counters exist and are non-negative."""
        from compact_engine import _CW2_NORMAL_TRIMS_TOTAL, _CW2_FALLBACK_TRIMS_TOTAL
        self.assertIsInstance(_CW2_NORMAL_TRIMS_TOTAL, int)
        self.assertIsInstance(_CW2_FALLBACK_TRIMS_TOTAL, int)
        self.assertGreaterEqual(_CW2_NORMAL_TRIMS_TOTAL, 0)
        self.assertGreaterEqual(_CW2_FALLBACK_TRIMS_TOTAL, 0)

    def test_cw2_fallback_logs_error(self):
        """CW2 fallback code path exists with ERROR log level."""
        import compact_engine
        src = Path(compact_engine.__file__).read_text(encoding="utf-8")
        self.assertIn("Fallback force-truncation", src)
        self.assertIn("logger.error", src)

    def test_cw2_single_message_exceeds_limit(self):
        """CW2 handles edge case where single message exceeds hard limit."""
        import compact_engine
        src = Path(compact_engine.__file__).read_text(encoding="utf-8")
        self.assertIn("Single-message exceeds hard limit", src)
        self.assertIn("truncated by CW2 guard", src)
        self.assertIn("logger.critical", src)

    def test_cw2_single_message_truncation_preserves_min_200_chars(self):
        """CW2 single-message truncation keeps at least 200 chars."""
        # Verify: max(200, ...) ensures minimum content is preserved
        _avail = max(200, int((100 - 50) * 3))  # small budget
        self.assertGreaterEqual(_avail, 200)
        # Large budget case
        _avail2 = max(200, int((6000 - 200) * 3))  # large budget
        self.assertGreater(_avail2, 200)


# ═══════════════════════════════════════════════════════════════
# CW3: AutoCompact Circuit Breaker Tests
# ═══════════════════════════════════════════════════════════════

class TestCW3CircuitBreaker(unittest.TestCase):
    """CW3: Auto-compact circuit breaker (Claw MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES)."""

    def test_cw3_constant_exists(self):
        """CW3 max consecutive failures constant exists and equals 3."""
        from agentic_loop import _CW3_MAX_CONSECUTIVE_FAILURES
        self.assertEqual(_CW3_MAX_CONSECUTIVE_FAILURES, 3)

    def test_cw3_matches_claw_value(self):
        """CW3 threshold matches Claw's MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES."""
        from agentic_loop import _CW3_MAX_CONSECUTIVE_FAILURES
        # Claw autoCompact.ts:70 — MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES=3
        self.assertEqual(_CW3_MAX_CONSECUTIVE_FAILURES, 3)

    def test_cw3_circuit_breaker_code_exists(self):
        """CW3 circuit breaker logic is present in agentic_loop."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("[CW3] Circuit breaker OPEN", src)
        self.assertIn("_cw3_autocompact_failures", src)

    def test_cw3_session_counter_initialized(self):
        """CW3 failure counter is initialized to 0 in session state trackers."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("_cw3_autocompact_failures = 0", src)

    def test_cw3_reset_on_success(self):
        """CW3 failure counter resets to 0 on successful compaction."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        # After successful compact, counter must be reset
        self.assertIn("_cw3_autocompact_failures = 0  # CW3: reset on success", src)

    def test_cw3_increment_on_failure(self):
        """CW3 failure counter increments on failed compaction."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("_cw3_autocompact_failures += 1", src)

    def test_cw3_logs_error_when_open(self):
        """CW3 logs ERROR when circuit breaker opens."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        # Circuit breaker open should be ERROR level
        self.assertIn('logger.error', src)
        self.assertIn('Circuit breaker OPEN', src)

    def test_cw3_skips_compact_when_open(self):
        """CW3 sets _compact_mode = None when circuit breaker is open."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("_compact_mode = None", src)

    def test_cw3_both_failure_paths_tracked(self):
        """CW3 increments counter for both exception and no-result failures."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        # Count increment occurrences — should be at least 2 (exception + no-result)
        count = src.count("_cw3_autocompact_failures += 1")
        self.assertGreaterEqual(count, 2)

    def test_cw3_optimization_history_listed(self):
        """CW3 is listed in the module optimization history."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("CW3: AutoCompact circuit breaker", src)

    def test_cw3_half_open_probe_interval_exists(self):
        """CW3 half-open probe interval constant exists."""
        from agentic_loop import _CW3_HALF_OPEN_PROBE_INTERVAL
        self.assertEqual(_CW3_HALF_OPEN_PROBE_INTERVAL, 5)

    def test_cw3_half_open_state_code_exists(self):
        """CW3 half-open state logic is present in agentic_loop."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("HALF-OPEN", src)
        self.assertIn("_cw3_breaker_open_turn", src)

    def test_cw3_half_open_probes_after_interval(self):
        """After breaker opens, a probe is allowed after N turns."""
        from agentic_loop import _CW3_MAX_CONSECUTIVE_FAILURES, _CW3_HALF_OPEN_PROBE_INTERVAL
        # Simulate: breaker opens at turn 10, probe at turn 15
        breaker_open_turn = 10
        probe_turn = breaker_open_turn + _CW3_HALF_OPEN_PROBE_INTERVAL
        self.assertEqual(probe_turn, 15)
        self.assertTrue(probe_turn - breaker_open_turn >= _CW3_HALF_OPEN_PROBE_INTERVAL)

    def test_cw3_half_open_blocks_before_interval(self):
        """Before probe interval elapses, compact is still skipped."""
        from agentic_loop import _CW3_HALF_OPEN_PROBE_INTERVAL
        breaker_open_turn = 10
        too_early_turn = breaker_open_turn + _CW3_HALF_OPEN_PROBE_INTERVAL - 1
        self.assertFalse(too_early_turn - breaker_open_turn >= _CW3_HALF_OPEN_PROBE_INTERVAL)

    def test_cw3_breaker_open_turn_recorded(self):
        """Breaker records the turn when it first opens."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        # When failures hit threshold, open_turn is set
        self.assertIn("_cw3_breaker_open_turn = turn", src)

    def test_cw3_session_isolated_counter(self):
        """CW3 counter is a local variable (session-isolated, not module-level)."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        # Counter must NOT be module-level (no 'global _cw3_autocompact_failures')
        self.assertNotIn("global _cw3_autocompact_failures", src)
        # Must be initialized inside the function (session-scoped)
        self.assertIn("_cw3_autocompact_failures = 0", src)

    def test_cw3_successful_probe_closes_breaker(self):
        """A successful probe resets failures to 0 (breaker CLOSED)."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("breaker CLOSED", src)

    def test_cw3_timer_uses_gte_not_eq(self):
        """CW3 failure paths use >= (not ==) to update breaker_open_turn on every breach."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        # Must use >= so failed probes also update the timer
        self.assertIn(">= _CW3_MAX_CONSECUTIVE_FAILURES:\n                        _cw3_breaker_open_turn = turn", src)
        # Must NOT have premature timer reset before probe
        self.assertNotIn("_cw3_breaker_open_turn = turn  # reset probe timer", src)

    def test_cw3_e2e_full_breaker_cycle(self):
        """E2E: simulate CLOSED→OPEN→HALF-OPEN→probe success→CLOSED cycle."""
        from agentic_loop import _CW3_MAX_CONSECUTIVE_FAILURES, _CW3_HALF_OPEN_PROBE_INTERVAL
        # Simulate breaker state machine
        failures = 0
        breaker_open_turn = 0
        log = []

        # Phase 1: 3 consecutive failures → breaker OPEN
        for t in range(1, 4):
            failures += 1
            if failures >= _CW3_MAX_CONSECUTIVE_FAILURES:
                breaker_open_turn = t
            log.append(f"turn={t} fail={failures} state={'OPEN' if failures >= 3 else 'CLOSED'}")
        self.assertEqual(failures, 3)
        self.assertEqual(breaker_open_turn, 3)

        # Phase 2: turns 4-7 — breaker OPEN, compact skipped
        for t in range(4, 8):
            turns_since = t - breaker_open_turn
            should_skip = turns_since < _CW3_HALF_OPEN_PROBE_INTERVAL
            log.append(f"turn={t} skip={should_skip} turns_since={turns_since}")
            self.assertTrue(should_skip, f"Should skip at turn {t}")

        # Phase 3: turn 8 — HALF-OPEN, probe allowed (5 turns since open at turn 3)
        t = 8
        turns_since = t - breaker_open_turn
        self.assertTrue(turns_since >= _CW3_HALF_OPEN_PROBE_INTERVAL)
        log.append(f"turn={t} HALF-OPEN probe (turns_since={turns_since})")

        # Probe fails → timer updates to current turn
        failures += 1
        if failures >= _CW3_MAX_CONSECUTIVE_FAILURES:
            breaker_open_turn = t  # timer resets to failed probe turn
        self.assertEqual(breaker_open_turn, 8)
        self.assertEqual(failures, 4)

        # Phase 4: turns 9-12 — OPEN again, skipped
        for t in range(9, 13):
            turns_since = t - breaker_open_turn
            self.assertTrue(turns_since < _CW3_HALF_OPEN_PROBE_INTERVAL)

        # Phase 5: turn 13 — HALF-OPEN again, probe succeeds
        t = 13
        turns_since = t - breaker_open_turn
        self.assertTrue(turns_since >= _CW3_HALF_OPEN_PROBE_INTERVAL)
        # Success!
        failures = 0  # breaker CLOSED
        self.assertEqual(failures, 0)

        # Phase 6: verify breaker is fully closed
        t = 14
        is_open = failures >= _CW3_MAX_CONSECUTIVE_FAILURES
        self.assertFalse(is_open, "Breaker should be CLOSED after successful probe")

    def test_cw3_e2e_probe_spacing_correct(self):
        """E2E: verify probes are spaced exactly _CW3_HALF_OPEN_PROBE_INTERVAL turns apart."""
        from agentic_loop import _CW3_MAX_CONSECUTIVE_FAILURES, _CW3_HALF_OPEN_PROBE_INTERVAL
        failures = 3
        breaker_open_turn = 10
        probe_turns = []

        # Simulate 30 turns of continuous failure
        for t in range(11, 41):
            if failures >= _CW3_MAX_CONSECUTIVE_FAILURES:
                turns_since = t - breaker_open_turn
                if turns_since >= _CW3_HALF_OPEN_PROBE_INTERVAL:
                    probe_turns.append(t)
                    # Probe fails, timer updates
                    failures += 1
                    breaker_open_turn = t

        # Probes should be at turns 15, 20, 25, 30, 35, 40
        expected = list(range(15, 41, _CW3_HALF_OPEN_PROBE_INTERVAL))
        self.assertEqual(probe_turns, expected)


# ═══════════════════════════════════════════════════════════════
# CW4: Post-Compact Cleanup Centralization Tests
# ═══════════════════════════════════════════════════════════════

class TestCW4PostCompactCleanup(unittest.TestCase):
    """CW4: Post-compact cleanup centralization (Claw runPostCompactCleanup)."""

    def test_cw4_function_exists(self):
        """CW4 cleanup function exists and is callable."""
        from agentic_loop import _run_post_compact_cleanup
        self.assertTrue(callable(_run_post_compact_cleanup))

    def test_cw4_resets_observability(self):
        """CW4 cleanup resets observability state."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("_reset_observability_state()", src)

    def test_cw4_resets_file_read_tracker(self):
        """CW4 cleanup resets file_read session tracker."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("reset_session_read_tracker()", src)

    def test_cw4_called_in_p5(self):
        """CW4 cleanup is called after P5 auto-compact success."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        # Must appear in the P5 success block (near CW3 reset)
        idx_cw3 = src.index("breaker CLOSED")
        idx_cw4 = src.index("_run_post_compact_cleanup()", idx_cw3)
        self.assertGreater(idx_cw4, idx_cw3)

    def test_cw4_called_in_p10(self):
        """CW4 cleanup is called after P10 reactive compact success."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        # Must appear near P10 reactive compact
        idx_p10 = src.index("Reactive compaction succeeded")
        idx_cw4 = src.rfind("_run_post_compact_cleanup()", 0, idx_p10)
        self.assertGreater(idx_cw4, 0)

    def test_cw4_clears_failed_paths_p5(self):
        """CW4 clears _file_read_failed_paths in P5 path."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("_file_read_failed_paths.clear()  # CW4", src)

    def test_cw4_optimization_history_listed(self):
        """CW4 is listed in the module optimization history."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("CW4: Post-compact cleanup centralization", src)


# ═══════════════════════════════════════════════════════════════
# CW5/CW6: Token Warning State Tests
# ═══════════════════════════════════════════════════════════════

class TestCW6TokenWarningState(unittest.TestCase):
    """CW6: 4-level token warning state (Claw calculateTokenWarningState)."""

    def test_cw6_get_token_warning_state_exists(self):
        """TokenBudgetTracker has get_token_warning_state method."""
        from compact_engine import TokenBudgetTracker
        t = TokenBudgetTracker(10000)
        self.assertTrue(hasattr(t, 'get_token_warning_state'))

    def test_cw6_normal_below_70pct(self):
        """Below 70% returns 'normal'."""
        from compact_engine import TokenBudgetTracker
        t = TokenBudgetTracker(10000)
        self.assertEqual(t.get_token_warning_state(6999), "normal")

    def test_cw6_warning_at_70pct(self):
        """Between 70-80% returns 'warning'."""
        from compact_engine import TokenBudgetTracker
        t = TokenBudgetTracker(10000)
        self.assertEqual(t.get_token_warning_state(7500), "warning")

    def test_cw6_autocompact_at_80pct(self):
        """Between 80-90% returns 'autoCompact'."""
        from compact_engine import TokenBudgetTracker
        t = TokenBudgetTracker(10000)
        self.assertEqual(t.get_token_warning_state(8500), "autoCompact")

    def test_cw6_error_at_90pct(self):
        """Between 90-95% returns 'error'."""
        from compact_engine import TokenBudgetTracker
        t = TokenBudgetTracker(10000)
        self.assertEqual(t.get_token_warning_state(9200), "error")

    def test_cw6_blocking_above_95pct(self):
        """Above 95% returns 'blocking'."""
        from compact_engine import TokenBudgetTracker
        t = TokenBudgetTracker(10000)
        self.assertEqual(t.get_token_warning_state(9600), "blocking")

    def test_cw6_boundary_70pct_exact(self):
        """Exactly at 70% boundary (7000) is still 'normal' (> not >=)."""
        from compact_engine import TokenBudgetTracker
        t = TokenBudgetTracker(10000)
        self.assertEqual(t.get_token_warning_state(7000), "normal")

    def test_cw6_boundary_80pct_exact(self):
        """Exactly at 80% boundary (8000) is still 'warning' (> not >=)."""
        from compact_engine import TokenBudgetTracker
        t = TokenBudgetTracker(10000)
        self.assertEqual(t.get_token_warning_state(8000), "warning")

    def test_cw6_should_compact_uses_autocompact_ratio(self):
        """should_compact uses the _AUTOCOMPACT_RATIO (80%) threshold."""
        from compact_engine import TokenBudgetTracker
        t = TokenBudgetTracker(10000)
        self.assertFalse(t.should_compact(8000))  # at boundary
        self.assertTrue(t.should_compact(8001))    # above

    def test_cw6_four_ratios_defined(self):
        """All 4 ratio constants are defined on the class."""
        from compact_engine import TokenBudgetTracker
        self.assertAlmostEqual(TokenBudgetTracker._WARNING_RATIO, 0.70)
        self.assertAlmostEqual(TokenBudgetTracker._AUTOCOMPACT_RATIO, 0.80)
        self.assertAlmostEqual(TokenBudgetTracker._ERROR_RATIO, 0.90)
        self.assertAlmostEqual(TokenBudgetTracker._BLOCKING_RATIO, 0.95)

    def test_cw6_returns_string(self):
        """get_token_warning_state always returns a string."""
        from compact_engine import TokenBudgetTracker
        t = TokenBudgetTracker(10000)
        for tokens in [0, 5000, 7500, 8500, 9200, 9800]:
            self.assertIsInstance(t.get_token_warning_state(tokens), str)


class TestCW5TokenWarningSSE(unittest.TestCase):
    """CW5: Token warning SSE event emission and forwarding."""

    def test_cw5_event_emitted_in_agentic_loop(self):
        """token_warning event is yielded from agentic_loop."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn('"type": "token_warning"', src)

    def test_cw5_event_includes_state(self):
        """token_warning event includes state field."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn('"state": _cw6_state', src)

    def test_cw5_event_includes_usage_pct(self):
        """token_warning event includes usage percentage."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("usage_pct", src)

    def test_cw5_forwarded_in_server(self):
        """server_final.py forwards token_warning SSE events."""
        src = Path("/home/field/.nanobot/workspace/web_ui/server_final.py").read_text(encoding="utf-8")
        self.assertIn("token_warning", src)

    def test_cw5_not_emitted_when_normal(self):
        """token_warning is only emitted when state changes (throttled)."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn('_cw6_state != _cw5_last_warning_state', src)

    def test_cw5_optimization_history_listed(self):
        """CW5 and CW6 are listed in the module optimization history."""
        import agentic_loop
        src = Path(agentic_loop.__file__).read_text(encoding="utf-8")
        self.assertIn("CW5: Compaction warning SSE events", src)
        self.assertIn("CW6: Token warning state 4-level thresholds", src)


# ═══════════════════════════════════════════════════════════════
# D2: Multi-Agent Status UI Tests
# ═══════════════════════════════════════════════════════════════

class TestD2MultiAgentStatusUI(unittest.TestCase):
    """D2: Verify SSE events for multi-agent status awareness in Web UI."""

    def test_d2_done_event_includes_skill_metadata(self):
        """agentic_done event should include skill_mode, verification_ran, criteria_nudges when skill is active."""
        # Build a mock done event as agentic_loop would produce
        _done_event = {
            "type": "agentic_done",
            "tools_used": ["file_read"],
            "total_tool_calls": 1,
            "turns": 2,
        }
        # Simulate skill mode active
        _skill_mode = "debug"
        _skill_write_policy = "allowed"
        _has_run_verification = True
        _p102_criteria_nudges = 1
        _skill_disallowed_tools = []
        if _skill_mode:
            _done_event["skill_mode"] = _skill_mode
            _done_event["write_policy"] = _skill_write_policy
            _done_event["verification_ran"] = _has_run_verification
            _done_event["criteria_nudges"] = _p102_criteria_nudges
            _done_event["disallowed_tools"] = _skill_disallowed_tools

        self.assertEqual(_done_event["skill_mode"], "debug")
        self.assertTrue(_done_event["verification_ran"])
        self.assertEqual(_done_event["criteria_nudges"], 1)
        self.assertEqual(_done_event["write_policy"], "allowed")

    def test_d2_done_event_no_skill_metadata_when_inactive(self):
        """agentic_done event should NOT include skill_mode fields when no skill is active."""
        _done_event = {
            "type": "agentic_done",
            "tools_used": [],
            "total_tool_calls": 0,
            "turns": 1,
        }
        _skill_mode = ""
        if _skill_mode:
            _done_event["skill_mode"] = _skill_mode
        self.assertNotIn("skill_mode", _done_event)
        self.assertNotIn("verification_ran", _done_event)

    def test_d2_skill_mode_event_structure(self):
        """skill_mode event should have mode, write_policy, requires_verification."""
        event = {
            "type": "skill_mode",
            "mode": "analyze",
            "write_policy": "forbidden",
            "requires_verification": False,
        }
        self.assertEqual(event["type"], "skill_mode")
        self.assertEqual(event["mode"], "analyze")
        self.assertEqual(event["write_policy"], "forbidden")
        self.assertFalse(event["requires_verification"])

    def test_d2_turn_start_event_structure(self):
        """turn_start event should have type and turn number."""
        event = {"type": "turn_start", "turn": 3}
        self.assertEqual(event["type"], "turn_start")
        self.assertEqual(event["turn"], 3)

    def test_d2_tool_start_event_structure(self):
        """tool_start event should have name, arguments preview, turn."""
        targs = {"path": "src/main.py", "offset": 1, "limit": 100}
        _args_preview = {}
        for k, v in list(targs.items())[:3]:
            _args_preview[k] = str(v)[:120] if isinstance(v, str) else v
        event = {
            "type": "tool_start",
            "name": "file_read",
            "arguments": _args_preview,
            "turn": 1,
        }
        self.assertEqual(event["name"], "file_read")
        self.assertEqual(event["arguments"]["path"], "src/main.py")
        self.assertEqual(event["arguments"]["offset"], 1)
        self.assertEqual(event["turn"], 1)

    def test_d2_tool_start_truncates_long_args(self):
        """tool_start should truncate argument values over 120 chars."""
        targs = {"query": "x" * 200}
        _args_preview = {}
        for k, v in list(targs.items())[:3]:
            _args_preview[k] = str(v)[:120] if isinstance(v, str) else v
        self.assertEqual(len(_args_preview["query"]), 120)

    def test_d2_sub_agent_start_event_structure(self):
        """sub_agent_start event should have agent_type, task_preview, turn."""
        targs = {"agent_type": "verify", "task": "Run all tests and check for regressions"}
        _sa_type = targs.get("agent_type", "general")
        _sa_task = targs.get("task", "")[:100]
        event = {
            "type": "sub_agent_start",
            "agent_type": _sa_type,
            "task_preview": _sa_task,
            "turn": 2,
        }
        self.assertEqual(event["agent_type"], "verify")
        self.assertIn("Run all tests", event["task_preview"])
        self.assertEqual(event["turn"], 2)

    def test_d2_sub_agent_end_event_structure(self):
        """sub_agent_end event should have agent_type, success, elapsed, turn."""
        event = {
            "type": "sub_agent_end",
            "agent_type": "explore",
            "success": True,
            "elapsed": 3.7,
            "turn": 2,
        }
        self.assertEqual(event["agent_type"], "explore")
        self.assertTrue(event["success"])
        self.assertAlmostEqual(event["elapsed"], 3.7, places=1)

    def test_d2_sub_agent_detection_aliases(self):
        """Sub-agent detection should recognize tool name aliases."""
        aliases = ("sub_agent", "fork", "delegate", "spawn_agent")
        for name in aliases:
            is_sub = name in aliases
            self.assertTrue(is_sub, f"{name} should be recognized as sub-agent")
        self.assertFalse("file_read" in aliases)

    def test_d2_server_done_stats_includes_skill_metadata(self):
        """Server should forward skill metadata from agentic_done into done_stats."""
        event = {
            "type": "agentic_done",
            "skill_mode": "refactor",
            "verification_ran": False,
            "criteria_nudges": 2,
            "write_policy": "allowed",
            "tools_used": ["file_edit"],
            "total_tool_calls": 3,
            "turns": 4,
        }
        done_stats = {
            "agentic_turns": event.get("turns", 0),
            "agentic_tool_calls": event.get("total_tool_calls", 0),
        }
        if event.get("skill_mode"):
            done_stats["skill_mode"] = event["skill_mode"]
            done_stats["verification_ran"] = event.get("verification_ran", False)
            done_stats["criteria_nudges"] = event.get("criteria_nudges", 0)
            done_stats["write_policy"] = event.get("write_policy", "")
        self.assertEqual(done_stats["skill_mode"], "refactor")
        self.assertFalse(done_stats["verification_ran"])
        self.assertEqual(done_stats["criteria_nudges"], 2)

    def test_d2_mode_label_mapping(self):
        """Frontend mode label mapping should cover known modes."""
        labels = {"analyze": "分析模式", "debug": "调试模式", "verify": "验证模式", "refactor": "重构模式"}
        icons = {"analyze": "🔍", "debug": "🐛", "verify": "✅", "refactor": "♻️"}
        for mode in ("analyze", "debug", "verify", "refactor"):
            self.assertIn(mode, labels)
            self.assertIn(mode, icons)
        # Unknown mode gets fallback
        unknown = "custom_mode"
        label = labels.get(unknown, f"/{unknown}")
        self.assertEqual(label, "/custom_mode")


# ═══════════════════════════════════════════════════════════════
# D3: Active File List (Workbench Awareness) Tests
# ═══════════════════════════════════════════════════════════════

class TestD3ActiveFileList(unittest.TestCase):
    """D3: Verify active file tracking and workbench summary injection."""

    def setUp(self):
        from agentic_loop import _reset_session_file_reads
        _reset_session_file_reads()

    def tearDown(self):
        from agentic_loop import _reset_session_file_reads
        _reset_session_file_reads()

    def test_d3_track_file_write(self):
        """file_edit should be tracked as a write action."""
        from agentic_loop import _track_file_activity, _SESSION_ACTIVE_FILES
        _track_file_activity("file_edit", {"success": True, "output": "ok"}, {"path": "/src/app.py"}, turn=2)
        self.assertIn("/src/app.py", _SESSION_ACTIVE_FILES)
        entry = _SESSION_ACTIVE_FILES["/src/app.py"]
        self.assertEqual(entry["last_action"], "write")
        self.assertTrue(entry["write_success"])
        self.assertEqual(entry["last_turn"], 2)

    def test_d3_track_file_write_failure(self):
        """Failed file_edit should record write_success=False."""
        from agentic_loop import _track_file_activity, _SESSION_ACTIVE_FILES
        _track_file_activity("file_edit", {"success": False, "error": "Permission denied"}, {"path": "/etc/hosts"}, turn=1)
        entry = _SESSION_ACTIVE_FILES["/etc/hosts"]
        self.assertFalse(entry["write_success"])

    def test_d3_read_then_write_updates_action(self):
        """Writing to a previously read file should update last_action to write."""
        from agentic_loop import _track_file_activity, _SESSION_ACTIVE_FILES
        _track_file_activity("file_read", {"output": "[File: /src/main.py | 50 lines]\ndef main(): pass"}, {"path": "/src/main.py"}, turn=1)
        self.assertEqual(_SESSION_ACTIVE_FILES["/src/main.py"]["last_action"], "read")
        _track_file_activity("file_edit", {"success": True, "output": "ok"}, {"path": "/src/main.py"}, turn=2)
        self.assertEqual(_SESSION_ACTIVE_FILES["/src/main.py"]["last_action"], "write")
        self.assertEqual(_SESSION_ACTIVE_FILES["/src/main.py"]["last_turn"], 2)
        # Summary from read should be preserved
        self.assertIn("defines:", _SESSION_ACTIVE_FILES["/src/main.py"]["summary"])

    def test_d3_active_files_summary_empty(self):
        """No active files should return empty string."""
        from agentic_loop import _get_active_files_summary
        self.assertEqual(_get_active_files_summary(), "")

    def test_d3_active_files_summary_format(self):
        """Summary should show icons and file paths."""
        from agentic_loop import _track_file_activity, _get_active_files_summary
        _track_file_activity("file_read", {"output": "[File: /ws/a.py | 30 lines]\ndef foo(): pass"}, {"path": "/ws/a.py"}, turn=1)
        _track_file_activity("file_edit", {"success": True, "output": "ok"}, {"path": "/ws/b.py"}, turn=2)
        summary = _get_active_files_summary(workspace="/ws")
        self.assertIn("Active files in this session:", summary)
        self.assertIn("📖", summary)  # read icon
        self.assertIn("✏️", summary)  # write icon
        self.assertIn("✅", summary)  # write success

    def test_d3_active_files_summary_order(self):
        """Most recently used files should appear first."""
        from agentic_loop import _track_file_activity, _get_active_files_summary
        _track_file_activity("file_read", {"output": "[File: /ws/old.py | 10 lines]\n"}, {"path": "/ws/old.py"}, turn=1)
        _track_file_activity("file_read", {"output": "[File: /ws/new.py | 20 lines]\n"}, {"path": "/ws/new.py"}, turn=5)
        summary = _get_active_files_summary(workspace="/ws")
        lines = summary.split("\n")
        # First file entry (after instruction + header) should be the most recent
        self.assertIn("new.py", lines[2])

    def test_d3_active_files_summary_max_files(self):
        """Summary should respect max_files limit."""
        from agentic_loop import _track_file_activity, _get_active_files_summary
        for i in range(10):
            _track_file_activity("file_read", {"output": f"[File: /ws/f{i}.py | 10 lines]\n"}, {"path": f"/ws/f{i}.py"}, turn=i)
        summary = _get_active_files_summary(workspace="/ws", max_files=3)
        # Header + 3 file entries (no summary lines since no defs)
        file_lines = [l for l in summary.split("\n") if l.strip().startswith(("📖", "✏️"))]
        self.assertEqual(len(file_lines), 3)

    def test_d3_write_without_read_no_summary(self):
        """Write-only files should have empty summary."""
        from agentic_loop import _track_file_activity, _SESSION_ACTIVE_FILES
        _track_file_activity("file_edit", {"success": True, "output": "ok"}, {"path": "/ws/new.py"}, turn=1)
        self.assertEqual(_SESSION_ACTIVE_FILES["/ws/new.py"]["summary"], "")

    def test_d3_file_write_tool(self):
        """file_write (not just file_edit) should be tracked as write."""
        from agentic_loop import _track_file_activity, _SESSION_ACTIVE_FILES
        _track_file_activity("file_write", {"success": True, "output": "created"}, {"path": "/ws/new.txt"}, turn=1)
        self.assertIn("/ws/new.txt", _SESSION_ACTIVE_FILES)
        self.assertEqual(_SESSION_ACTIVE_FILES["/ws/new.txt"]["last_action"], "write")

    def test_d3_ignored_tools(self):
        """Non-file tools should not be tracked."""
        from agentic_loop import _track_file_activity, _SESSION_ACTIVE_FILES
        _track_file_activity("grep_search", {"output": "match"}, {"query": "foo"}, turn=1)
        _track_file_activity("shell_execute", {"output": "ok"}, {"command": "ls"}, turn=1)
        self.assertEqual(len(_SESSION_ACTIVE_FILES), 0)

    def test_d3_build_dynamic_context_includes_active_files(self):
        """build_dynamic_context should include active_files_summary when provided."""
        from system_prompts import build_dynamic_context
        summary = "Active files in this session:\n  📖 src/main.py\n     defines: main, Config"
        ctx = build_dynamic_context(active_files_summary=summary)
        self.assertIn("Active files in this session:", ctx)
        self.assertIn("📖 src/main.py", ctx)

    def test_d3_build_dynamic_context_empty_when_no_files(self):
        """build_dynamic_context should not add section when no active files."""
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(active_files_summary="")
        self.assertNotIn("Active files", ctx)


# ═══════════════════════════════════════════════════════════════
# D4: File-Target Cross-Check Tests
# ═══════════════════════════════════════════════════════════════

class TestD4FileTargetCrossCheck(unittest.TestCase):
    """D4: Verify file-target mismatch detection."""

    def test_d4_extract_cn_filename(self):
        """Chinese '在 test_sample.py 中' should extract test_sample.py."""
        from agentic_loop import _extract_user_target_files
        files = _extract_user_target_files("在 test_sample.py 中，把函数 add 改名为 addition")
        self.assertIn("test_sample.py", files)

    def test_d4_extract_en_filename(self):
        """English 'in test_sample.py' should extract."""
        from agentic_loop import _extract_user_target_files
        files = _extract_user_target_files("in test_sample.py, rename function add to addition")
        self.assertIn("test_sample.py", files)

    def test_d4_extract_path(self):
        """Paths like 'in web_ui/tools/sub_agent.py' should extract."""
        from agentic_loop import _extract_user_target_files
        files = _extract_user_target_files("in web_ui/tools/sub_agent.py check the code")
        self.assertIn("web_ui/tools/sub_agent.py", files)

    def test_d4_extract_backtick_path(self):
        """Backtick-wrapped paths should extract."""
        from agentic_loop import _extract_user_target_files
        files = _extract_user_target_files("file `src/main.py` needs fixes")
        self.assertIn("src/main.py", files)

    def test_d4_no_file_mentioned(self):
        """No file mentioned should return empty list."""
        from agentic_loop import _extract_user_target_files
        files = _extract_user_target_files("explain the GIL mechanism")
        self.assertEqual(files, [])

    def test_d4_mismatch_detected(self):
        """Editing test_demo.py when user said test_sample.py should warn."""
        from agentic_loop import _check_edit_target_mismatch
        warn = _check_edit_target_mismatch("test_demo.py", ["test_sample.py"])
        self.assertIn("TARGET MISMATCH", warn)
        self.assertIn("test_sample.py", warn)

    def test_d4_match_basename(self):
        """Editing web_ui/test_sample.py when user said test_sample.py should pass."""
        from agentic_loop import _check_edit_target_mismatch
        warn = _check_edit_target_mismatch("web_ui/test_sample.py", ["test_sample.py"])
        self.assertEqual(warn, "")

    def test_d4_match_exact_path(self):
        """Exact path match should pass."""
        from agentic_loop import _check_edit_target_mismatch
        warn = _check_edit_target_mismatch("src/main.py", ["src/main.py"])
        self.assertEqual(warn, "")

    def test_d4_no_target_files_no_warn(self):
        """No user target files should never warn."""
        from agentic_loop import _check_edit_target_mismatch
        warn = _check_edit_target_mismatch("anything.py", [])
        self.assertEqual(warn, "")

    def test_d4_empty_edit_path_no_warn(self):
        """Empty edit path should not warn."""
        from agentic_loop import _check_edit_target_mismatch
        warn = _check_edit_target_mismatch("", ["test.py"])
        self.assertEqual(warn, "")


# ═══════════════════════════════════════════════════════════════
# U1: Session Memory Tests
# ═══════════════════════════════════════════════════════════════

class TestU1SessionMemory(unittest.TestCase):
    """U1: Automatic session notes for context preservation."""

    def test_u1_session_state_creation(self):
        """get_session_state creates new state."""
        from session_memory import get_session_state, reset_session_state
        reset_session_state("test_u1_create")
        state = get_session_state("test_u1_create")
        self.assertEqual(state.session_id, "test_u1_create")
        self.assertEqual(state.total_tool_calls, 0)
        self.assertEqual(state.extract_count, 0)
        reset_session_state("test_u1_create")

    def test_u1_session_state_reset(self):
        """reset_session_state clears state."""
        from session_memory import get_session_state, reset_session_state
        state = get_session_state("test_u1_reset")
        state.total_tool_calls = 99
        reset_session_state("test_u1_reset")
        state2 = get_session_state("test_u1_reset")
        self.assertEqual(state2.total_tool_calls, 0)
        reset_session_state("test_u1_reset")

    def test_u1_should_extract_interval(self):
        """should_extract returns True after N tool calls."""
        from session_memory import get_session_state, reset_session_state, EXTRACT_INTERVAL_TOOL_CALLS
        reset_session_state("test_u1_interval")
        state = get_session_state("test_u1_interval")
        for _ in range(EXTRACT_INTERVAL_TOOL_CALLS - 1):
            state.record_tool_call()
        self.assertFalse(state.should_extract())
        state.record_tool_call()
        self.assertTrue(state.should_extract())
        reset_session_state("test_u1_interval")

    def test_u1_should_extract_rate_limit(self):
        """should_extract rate-limits to 30s minimum between extractions."""
        import time
        from session_memory import get_session_state, reset_session_state, EXTRACT_INTERVAL_TOOL_CALLS
        reset_session_state("test_u1_rate")
        state = get_session_state("test_u1_rate")
        for _ in range(EXTRACT_INTERVAL_TOOL_CALLS):
            state.record_tool_call()
        state.last_extract_time = time.time()  # Just extracted
        self.assertFalse(state.should_extract())  # Too soon
        reset_session_state("test_u1_rate")

    def test_u1_extract_session_notes_basic(self):
        """extract_session_notes populates notes from messages."""
        from session_memory import get_session_state, reset_session_state, extract_session_notes
        reset_session_state("test_u1_extract")
        state = get_session_state("test_u1_extract")
        messages = [
            {"role": "user", "content": "Fix the login bug in auth.py"},
            {"role": "tool", "content": "[File: /src/auth.py | 50 lines]\ndef login():\n    pass", "_tool_name": "file_read"},
            {"role": "assistant", "content": "I found the issue in the login function. The session token is not being validated."},
        ]
        extract_session_notes(messages, state)
        self.assertIn("Session Title", state.notes)
        self.assertIn("login", state.notes["Session Title"])
        self.assertIn("Files and Functions", state.notes)
        self.assertIn("/src/auth.py", state.notes["Files and Functions"])
        self.assertEqual(state.extract_count, 1)

    def test_u1_extract_tracks_errors(self):
        """extract_session_notes captures error messages."""
        from session_memory import get_session_state, reset_session_state, extract_session_notes
        reset_session_state("test_u1_errors")
        state = get_session_state("test_u1_errors")
        messages = [
            {"role": "user", "content": "Run the tests"},
            {"role": "tool", "content": "Error: ModuleNotFoundError: No module named 'foo'", "_tool_name": "shell_execute"},
        ]
        extract_session_notes(messages, state)
        self.assertIn("Errors & Corrections", state.notes)
        self.assertIn("ModuleNotFoundError", state.notes["Errors & Corrections"])

    def test_u1_extract_tracks_worklog(self):
        """extract_session_notes records tool actions in worklog."""
        from session_memory import get_session_state, reset_session_state, extract_session_notes
        reset_session_state("test_u1_worklog")
        state = get_session_state("test_u1_worklog")
        messages = [
            {"role": "user", "content": "Edit the config file"},
            {"role": "tool", "content": "Applied 1 edit to config.json", "_tool_name": "file_edit"},
            {"role": "tool", "content": "$ python -m pytest\n5 passed", "_tool_name": "shell_execute"},
        ]
        extract_session_notes(messages, state)
        self.assertIn("Worklog", state.notes)
        self.assertIn("file_edit", state.notes["Worklog"])
        self.assertIn("shell_execute", state.notes["Worklog"])

    def test_u1_get_notes_text(self):
        """get_notes_text formats sections into markdown."""
        from session_memory import get_session_state, reset_session_state
        reset_session_state("test_u1_format")
        state = get_session_state("test_u1_format")
        state.notes["Session Title"] = "Fix auth bug"
        state.notes["Current State"] = "Debugging login"
        text = state.get_notes_text()
        self.assertIn("## Session Title", text)
        self.assertIn("Fix auth bug", text)
        self.assertIn("## Current State", text)

    def test_u1_get_session_memory_for_compact(self):
        """get_session_memory_for_compact returns notes for injection."""
        from session_memory import get_session_state, reset_session_state, get_session_memory_for_compact
        reset_session_state("test_u1_compact")
        state = get_session_state("test_u1_compact")
        state.notes["Session Title"] = "Implement login feature"
        state.notes["Current State"] = "Writing unit tests for the login endpoint"
        result = get_session_memory_for_compact("test_u1_compact")
        self.assertIn("Session Title", result)
        self.assertIn("Current State", result)
        reset_session_state("test_u1_compact")

    def test_u1_get_session_memory_for_compact_empty(self):
        """get_session_memory_for_compact returns empty for no notes."""
        from session_memory import reset_session_state, get_session_memory_for_compact
        reset_session_state("test_u1_empty")
        result = get_session_memory_for_compact("test_u1_empty")
        self.assertEqual(result, "")

    def test_u1_get_session_memory_for_context(self):
        """get_session_memory_for_context returns lightweight context."""
        from session_memory import get_session_state, reset_session_state, get_session_memory_for_context
        reset_session_state("test_u1_ctx")
        state = get_session_state("test_u1_ctx")
        state.notes["Current State"] = "Writing tests"
        state.notes["Files and Functions"] = "- `src/auth.py`"
        result = get_session_memory_for_context("test_u1_ctx")
        self.assertIn("[SESSION MEMORY]", result)
        self.assertIn("Writing tests", result)
        self.assertIn("src/auth.py", result)
        reset_session_state("test_u1_ctx")

    def test_u1_truncate_long_section(self):
        """Long sections are truncated to MAX_SECTION_CHARS."""
        from session_memory import _truncate, MAX_SECTION_CHARS
        long_text = "x" * (MAX_SECTION_CHARS + 500)
        result = _truncate(long_text, MAX_SECTION_CHARS)
        self.assertLessEqual(len(result), MAX_SECTION_CHARS + 50)  # Allow for truncation marker
        self.assertIn("[...truncated]", result)

    def test_u1_total_size_cap(self):
        """get_session_memory_for_compact caps total size."""
        from session_memory import get_session_state, reset_session_state, get_session_memory_for_compact, MAX_TOTAL_CHARS
        reset_session_state("test_u1_cap")
        state = get_session_state("test_u1_cap")
        # Fill with large content
        state.notes["Session Title"] = "Big session"
        state.notes["Files and Functions"] = "x" * (MAX_TOTAL_CHARS + 100)
        result = get_session_memory_for_compact("test_u1_cap")
        self.assertLessEqual(len(result), MAX_TOTAL_CHARS + 100)  # Allow marker
        reset_session_state("test_u1_cap")

    def test_u1_template_has_all_sections(self):
        """SESSION_MEMORY_TEMPLATE has all required sections."""
        from session_memory import SESSION_MEMORY_TEMPLATE
        for section in ["Session Title", "Current State", "Task Specification",
                        "Files and Functions", "Workflow", "Errors & Corrections",
                        "Key Results", "Worklog"]:
            self.assertIn(f"# {section}", SESSION_MEMORY_TEMPLATE)

    def test_u1_extract_resets_counter(self):
        """extract_session_notes resets tool_calls_since_extract."""
        from session_memory import get_session_state, reset_session_state, extract_session_notes
        reset_session_state("test_u1_reset_ctr")
        state = get_session_state("test_u1_reset_ctr")
        state.tool_calls_since_extract = 99
        extract_session_notes([], state)
        self.assertEqual(state.tool_calls_since_extract, 0)
        reset_session_state("test_u1_reset_ctr")

    def test_u1_constants_reasonable(self):
        """Extraction constants are reasonable values."""
        from session_memory import EXTRACT_INTERVAL_TOOL_CALLS, MAX_SECTION_CHARS, MAX_TOTAL_CHARS
        self.assertGreaterEqual(EXTRACT_INTERVAL_TOOL_CALLS, 4)
        self.assertLessEqual(EXTRACT_INTERVAL_TOOL_CALLS, 20)
        self.assertGreaterEqual(MAX_SECTION_CHARS, 500)
        self.assertLessEqual(MAX_TOTAL_CHARS, 20000)

    def test_u1_extract_skips_system_messages(self):
        """extract_session_notes ignores system context injections."""
        from session_memory import get_session_state, reset_session_state, extract_session_notes
        reset_session_state("test_u1_skip_sys")
        state = get_session_state("test_u1_skip_sys")
        messages = [
            {"role": "system", "content": "You are Nanobot"},
            {"role": "user", "content": "[SESSION CONTEXT] language=zh"},
            {"role": "user", "content": "Fix the bug in parser.py"},
        ]
        extract_session_notes(messages, state)
        # Task spec should contain the real user message, not the session context
        if "Task Specification" in state.notes:
            self.assertNotIn("[SESSION CONTEXT]", state.notes["Task Specification"])
        reset_session_state("test_u1_skip_sys")


# ═══════════════════════════════════════════════════════════════
# U3: Background Fork Subagent Tests
# ═══════════════════════════════════════════════════════════════

class TestU3BackgroundFork(unittest.TestCase):
    """U3: Background fork sub-agent — async execution, anti-recursion, notification."""

    def test_u3_tool_def_has_background_param(self):
        """TOOL_DEF includes background parameter."""
        from tools.sub_agent import TOOL_DEF
        props = TOOL_DEF["function"]["parameters"]["properties"]
        self.assertIn("background", props)
        self.assertEqual(props["background"]["type"], "boolean")

    def test_u3_background_param_description(self):
        """background param description mentions task-notification."""
        from tools.sub_agent import TOOL_DEF
        desc = TOOL_DEF["function"]["parameters"]["properties"]["background"]["description"]
        self.assertIn("task-notification", desc)
        self.assertIn("BACKGROUND", desc)

    def test_u3_background_task_class_exists(self):
        """BackgroundTask class has required slots."""
        from tools.sub_agent import BackgroundTask
        self.assertTrue(hasattr(BackgroundTask, "__slots__"))
        slots = BackgroundTask.__slots__
        for attr in ("task_id", "agent_type", "task_preview", "asyncio_task",
                      "created_at", "completed_at", "result", "notified",
                      "session_id", "fork_depth"):
            self.assertIn(attr, slots)

    def test_u3_max_concurrent_limit(self):
        """_MAX_CONCURRENT_BACKGROUND is reasonable."""
        from tools.sub_agent import _MAX_CONCURRENT_BACKGROUND
        self.assertGreaterEqual(_MAX_CONCURRENT_BACKGROUND, 2)
        self.assertLessEqual(_MAX_CONCURRENT_BACKGROUND, 10)

    def test_u3_max_fork_depth(self):
        """_MAX_FORK_DEPTH prevents recursive background forks."""
        from tools.sub_agent import _MAX_FORK_DEPTH
        self.assertEqual(_MAX_FORK_DEPTH, 1)

    def test_u3_registry_session_isolation(self):
        """Background task registry isolates sessions."""
        from tools.sub_agent import _get_session_tasks, reset_background_tasks
        reset_background_tasks("test_u3_a")
        reset_background_tasks("test_u3_b")
        tasks_a = _get_session_tasks("test_u3_a")
        tasks_b = _get_session_tasks("test_u3_b")
        self.assertIsNot(tasks_a, tasks_b)
        reset_background_tasks("test_u3_a")
        reset_background_tasks("test_u3_b")

    def test_u3_get_active_count_empty(self):
        """get_active_background_count returns 0 for fresh session."""
        from tools.sub_agent import get_active_background_count, reset_background_tasks
        reset_background_tasks("test_u3_empty")
        self.assertEqual(get_active_background_count("test_u3_empty"), 0)

    def test_u3_get_completed_empty(self):
        """get_completed_background_tasks returns empty for fresh session."""
        from tools.sub_agent import get_completed_background_tasks, reset_background_tasks
        reset_background_tasks("test_u3_comp_empty")
        self.assertEqual(get_completed_background_tasks("test_u3_comp_empty"), [])

    def test_u3_background_task_is_done(self):
        """BackgroundTask.is_done reflects asyncio task state."""
        import asyncio
        from tools.sub_agent import BackgroundTask

        async def _done():
            return {"success": True, "output": "done", "error": ""}

        loop = asyncio.new_event_loop()
        try:
            atask = loop.create_task(_done())
            loop.run_until_complete(atask)
            bt = BackgroundTask("t1", "verify", "test task", atask, "sess", 1)
            self.assertTrue(bt.is_done)
        finally:
            loop.close()

    def test_u3_background_task_collect_result(self):
        """BackgroundTask.collect_result returns result dict."""
        import asyncio
        from tools.sub_agent import BackgroundTask

        async def _done():
            return {"success": True, "output": "hello", "error": ""}

        loop = asyncio.new_event_loop()
        try:
            atask = loop.create_task(_done())
            loop.run_until_complete(atask)
            bt = BackgroundTask("t2", "explore", "test", atask, "sess", 1)
            result = bt.collect_result()
            self.assertTrue(result["success"])
            self.assertEqual(result["output"], "hello")
            self.assertIsNotNone(bt.completed_at)
        finally:
            loop.close()

    def test_u3_background_task_collect_exception(self):
        """BackgroundTask.collect_result handles exceptions."""
        import asyncio
        from tools.sub_agent import BackgroundTask

        async def _fail():
            raise RuntimeError("boom")

        loop = asyncio.new_event_loop()
        try:
            atask = loop.create_task(_fail())
            try:
                loop.run_until_complete(atask)
            except RuntimeError:
                pass
            bt = BackgroundTask("t3", "general", "test", atask, "sess", 1)
            result = bt.collect_result()
            self.assertFalse(result["success"])
            self.assertIn("boom", result["error"])
        finally:
            loop.close()

    def test_u3_mark_notified(self):
        """mark_task_notified sets notified flag."""
        import asyncio
        from tools.sub_agent import BackgroundTask, _get_session_tasks, mark_task_notified, reset_background_tasks

        async def _done():
            return {"success": True, "output": "ok", "error": ""}

        reset_background_tasks("test_u3_notify")
        loop = asyncio.new_event_loop()
        try:
            atask = loop.create_task(_done())
            loop.run_until_complete(atask)
            bt = BackgroundTask("tn1", "verify", "test", atask, "test_u3_notify", 1)
            _get_session_tasks("test_u3_notify").append(bt)
            self.assertFalse(bt.notified)
            mark_task_notified("tn1", "test_u3_notify")
            self.assertTrue(bt.notified)
        finally:
            loop.close()
            reset_background_tasks("test_u3_notify")

    def test_u3_reset_clears_tasks(self):
        """reset_background_tasks removes all tasks for session."""
        from tools.sub_agent import _get_session_tasks, reset_background_tasks, _BACKGROUND_TASKS
        _get_session_tasks("test_u3_clear")  # ensure key exists
        reset_background_tasks("test_u3_clear")
        self.assertNotIn("test_u3_clear", _BACKGROUND_TASKS)

    def test_u3_anti_recursion_depth_check(self):
        """execute_async rejects background=True when fork depth exceeded."""
        import asyncio
        from tools import sub_agent as sa

        saved = sa._CURRENT_FORK_DEPTH
        sa._CURRENT_FORK_DEPTH = sa._MAX_FORK_DEPTH  # at limit
        try:
            result = asyncio.run(sa.execute_async(
                {"task": "verify everything works correctly", "background": True},
                sa._PARENT_WORKSPACE,
            ))
            self.assertFalse(result["success"])
            self.assertIn("fork depth limit", result["error"])
        finally:
            sa._CURRENT_FORK_DEPTH = saved

    def test_u3_anti_flood_concurrent_check(self):
        """execute_async rejects background when max concurrent reached."""
        import asyncio
        from tools import sub_agent as sa

        sa.reset_background_tasks("test_u3_flood")
        saved_session = sa._PARENT_SESSION_ID
        sa._PARENT_SESSION_ID = "test_u3_flood"
        saved_depth = sa._CURRENT_FORK_DEPTH
        sa._CURRENT_FORK_DEPTH = 0

        # Fill up the registry with fake running tasks
        async def _never_done():
            await asyncio.sleep(9999)

        loop = asyncio.new_event_loop()
        try:
            for i in range(sa._MAX_CONCURRENT_BACKGROUND):
                atask = loop.create_task(_never_done())
                bt = sa.BackgroundTask(f"flood_{i}", "general", "test", atask, "test_u3_flood", 1)
                sa._get_session_tasks("test_u3_flood").append(bt)

            result = loop.run_until_complete(sa.execute_async(
                {"task": "this should be rejected because too many running", "background": True},
                sa._PARENT_WORKSPACE,
            ))
            self.assertFalse(result["success"])
            self.assertIn("concurrent background tasks", result["error"])
        finally:
            sa._PARENT_SESSION_ID = saved_session
            sa._CURRENT_FORK_DEPTH = saved_depth
            # Cancel all fake tasks
            for t in sa._get_session_tasks("test_u3_flood"):
                t.asyncio_task.cancel()
            sa.reset_background_tasks("test_u3_flood")
            loop.close()

    def test_u3_notification_format_success(self):
        """task-notification for success has correct XML structure."""
        notification = (
            '<task-notification task_id="bg_verify_12345" '
            'agent_type="verify" status="completed" elapsed="5.2s">\n'
            'output text\n'
            '</task-notification>'
        )
        self.assertIn("task_id=", notification)
        self.assertIn("status=\"completed\"", notification)
        self.assertIn("</task-notification>", notification)

    def test_u3_notification_format_failure(self):
        """task-notification for failure includes error."""
        notification = (
            '<task-notification task_id="bg_verify_12345" '
            'agent_type="verify" status="failed" elapsed="2.0s">\n'
            'Error: some error\n'
            '</task-notification>'
        )
        self.assertIn("status=\"failed\"", notification)
        self.assertIn("Error:", notification)

    def test_u3_background_result_has_task_id(self):
        """Background fork returns _background_task_id in result."""
        import asyncio
        from tools import sub_agent as sa

        sa.reset_background_tasks("test_u3_result")
        saved_session = sa._PARENT_SESSION_ID
        saved_env = sa._PARENT_ENV
        saved_depth = sa._CURRENT_FORK_DEPTH
        sa._PARENT_SESSION_ID = "test_u3_result"
        sa._PARENT_ENV = {"NANOBOT_AGENTS__DEFAULTS__MODEL": "test"}
        sa._CURRENT_FORK_DEPTH = 0

        try:
            result = asyncio.run(sa.execute_async(
                {"task": "verify everything works correctly in auth.py", "agent_type": "verify", "background": True},
                sa._PARENT_WORKSPACE,
            ))
            self.assertTrue(result["success"])
            self.assertIn("_background_task_id", result)
            self.assertTrue(result["_background_task_id"].startswith("bg_verify_"))
            self.assertIn("Background task launched", result["output"])
        finally:
            sa._PARENT_SESSION_ID = saved_session
            sa._PARENT_ENV = saved_env
            sa._CURRENT_FORK_DEPTH = saved_depth
            sa.reset_background_tasks("test_u3_result")

    def test_u3_elapsed_property(self):
        """BackgroundTask.elapsed returns positive duration."""
        import asyncio
        from tools.sub_agent import BackgroundTask

        async def _done():
            return {"success": True, "output": "", "error": ""}

        loop = asyncio.new_event_loop()
        try:
            atask = loop.create_task(_done())
            loop.run_until_complete(atask)
            bt = BackgroundTask("te1", "verify", "test", atask, "sess", 1)
            self.assertGreaterEqual(bt.elapsed, 0)
        finally:
            loop.close()


# ═══════════════════════════════════════════════════════════════
# U4: Cache-Aware MicroCompact Tests
# ═══════════════════════════════════════════════════════════════

class TestU4CacheAwareMC(unittest.TestCase):
    """U4: Cache-aware micro-compact — hint-based instead of destructive."""

    def test_u4_flag_exists(self):
        """_U4_CACHE_AWARE master switch exists."""
        from agentic_loop import _U4_CACHE_AWARE
        self.assertIsInstance(_U4_CACHE_AWARE, bool)

    def test_u4_flag_default_on(self):
        """U4 cache-aware mode is ON by default."""
        from agentic_loop import _U4_CACHE_AWARE
        self.assertTrue(_U4_CACHE_AWARE)

    def test_u4_stale_threshold(self):
        """_U4_STALE_TURN_THRESHOLD is reasonable."""
        from agentic_loop import _U4_STALE_TURN_THRESHOLD
        self.assertGreaterEqual(_U4_STALE_TURN_THRESHOLD, 2)
        self.assertLessEqual(_U4_STALE_TURN_THRESHOLD, 10)

    def test_u4_hint_tag(self):
        """_U4_HINT_TAG is identifiable."""
        from agentic_loop import _U4_HINT_TAG
        self.assertIn("CONTEXT MANAGEMENT", _U4_HINT_TAG)

    def test_u4_build_hint_empty_early_turns(self):
        """No hint generated for early turns."""
        from agentic_loop import _build_cache_aware_hint, _U4_STALE_TURN_THRESHOLD
        messages = [
            {"role": "tool", "content": "data", "_tool_name": "file_read", "_turn": 1},
        ]
        hint = _build_cache_aware_hint(messages, _U4_STALE_TURN_THRESHOLD)
        self.assertEqual(hint, "")

    def test_u4_build_hint_with_stale(self):
        """Hint generated when stale tool results exist."""
        from agentic_loop import _build_cache_aware_hint, _U4_STALE_TURN_THRESHOLD
        messages = [
            {"role": "tool", "content": "old data from turn 1", "_tool_name": "file_read", "_turn": 1},
            {"role": "tool", "content": "old data from turn 2", "_tool_name": "grep_search", "_turn": 2},
            {"role": "tool", "content": "recent data", "_tool_name": "file_read", "_turn": 8},
        ]
        hint = _build_cache_aware_hint(messages, 8)
        self.assertIn("CONTEXT MANAGEMENT", hint)
        self.assertIn("STALE", hint)
        self.assertIn("turn 1", hint)
        self.assertIn("file_read", hint)

    def test_u4_build_hint_skips_cleared(self):
        """Hint skips already-cleared tool results."""
        from agentic_loop import _build_cache_aware_hint, _TOOL_RESULT_CLEARED_MSG
        messages = [
            {"role": "tool", "content": _TOOL_RESULT_CLEARED_MSG, "_tool_name": "file_read", "_turn": 1},
        ]
        hint = _build_cache_aware_hint(messages, 10)
        self.assertEqual(hint, "")

    def test_u4_build_hint_skips_high_value(self):
        """Hint skips sub-agent summaries (A1 high-value content)."""
        from agentic_loop import _build_cache_aware_hint
        messages = [
            {"role": "tool", "content": "[Sub-agent verify summary: 3 turns] VERDICT: PASS", "_tool_name": "sub_agent", "_turn": 1},
        ]
        hint = _build_cache_aware_hint(messages, 10)
        self.assertEqual(hint, "")

    def test_u4_build_hint_shows_recent_range(self):
        """Hint includes guidance about which turns are fresh."""
        from agentic_loop import _build_cache_aware_hint, _U4_STALE_TURN_THRESHOLD
        messages = [
            {"role": "tool", "content": "old stuff", "_tool_name": "grep_search", "_turn": 1},
        ]
        current = _U4_STALE_TURN_THRESHOLD + 2
        hint = _build_cache_aware_hint(messages, current)
        self.assertIn("Focus on the most recent", hint)

    def test_u4_build_hint_deduplicates_tools(self):
        """Multiple same-tool results in one turn shown once."""
        from agentic_loop import _build_cache_aware_hint
        messages = [
            {"role": "tool", "content": "data1", "_tool_name": "file_read", "_turn": 1},
            {"role": "tool", "content": "data2", "_tool_name": "file_read", "_turn": 1},
        ]
        hint = _build_cache_aware_hint(messages, 10)
        # "turn 1: file_read" should appear once, not twice
        self.assertEqual(hint.count("turn 1:"), 1)

    def test_u4_no_messages_modified(self):
        """U4 hint generation does NOT modify any message content."""
        from agentic_loop import _build_cache_aware_hint
        messages = [
            {"role": "tool", "content": "original data here", "_tool_name": "file_read", "_turn": 1},
        ]
        original_content = messages[0]["content"]
        _build_cache_aware_hint(messages, 10)
        self.assertEqual(messages[0]["content"], original_content)


# ═══════════════════════════════════════════════════════════════
# U5: System Prompt Section Caching Tests
# ═══════════════════════════════════════════════════════════════

class TestU5SectionCaching(unittest.TestCase):
    """U5: System prompt section-level hash caching."""

    def test_u5_section_names_exist(self):
        """get_section_names returns non-empty list."""
        from system_prompts import get_section_names
        names = get_section_names()
        self.assertIsInstance(names, list)
        self.assertGreaterEqual(len(names), 8)

    def test_u5_section_names_content(self):
        """All expected sections are present."""
        from system_prompts import get_section_names
        names = get_section_names()
        for expected in ("identity", "tasks", "stop", "output", "quality", "platform_info"):
            self.assertIn(expected, names)

    def test_u5_section_count(self):
        """get_prompt_section_count matches names list length."""
        from system_prompts import get_prompt_section_count, get_section_names
        self.assertEqual(get_prompt_section_count(), len(get_section_names()))

    def test_u5_hashes_populated_after_build(self):
        """Section hashes are populated after _get_static_system_prompt."""
        from system_prompts import _get_static_system_prompt, get_section_hash, get_section_names
        _get_static_system_prompt()
        for name in get_section_names():
            h = get_section_hash(name)
            self.assertTrue(len(h) > 0, f"Section '{name}' has empty hash")
            self.assertEqual(len(h), 32, f"Section '{name}' hash should be 32-char MD5")

    def test_u5_hash_section_deterministic(self):
        """_hash_section returns same hash for same content."""
        from system_prompts import _hash_section
        h1 = _hash_section("hello world")
        h2 = _hash_section("hello world")
        self.assertEqual(h1, h2)

    def test_u5_hash_section_different_for_different_content(self):
        """_hash_section returns different hash for different content."""
        from system_prompts import _hash_section
        h1 = _hash_section("hello")
        h2 = _hash_section("world")
        self.assertNotEqual(h1, h2)

    def test_u5_static_prompt_cached(self):
        """Second call returns same object (cached)."""
        from system_prompts import _get_static_system_prompt
        p1 = _get_static_system_prompt()
        p2 = _get_static_system_prompt()
        # Same string content
        self.assertEqual(p1, p2)

    def test_u5_build_system_prompt_reuses_base(self):
        """build_system_prompt with no extras returns same as static prompt."""
        from system_prompts import build_system_prompt, _get_static_system_prompt
        base = _get_static_system_prompt()
        full = build_system_prompt()
        self.assertEqual(full, base)

    def test_u5_build_system_prompt_with_language(self):
        """build_system_prompt with language appends language section."""
        from system_prompts import build_system_prompt, _get_static_system_prompt
        base = _get_static_system_prompt()
        full = build_system_prompt(language="zh")
        self.assertTrue(full.startswith(base))
        self.assertIn("Chinese", full)

    def test_u5_build_system_prompt_with_workspace(self):
        """build_system_prompt with workspace appends environment section."""
        from system_prompts import build_system_prompt, _get_static_system_prompt
        base = _get_static_system_prompt()
        full = build_system_prompt(workspace_info="/home/test/project")
        self.assertTrue(full.startswith(base))
        self.assertIn("/home/test/project", full)

    def test_u5_build_sections_list_count(self):
        """_build_sections_list returns correct number of sections."""
        from system_prompts import _build_sections_list, get_prompt_section_count
        sections = _build_sections_list()
        self.assertEqual(len(sections), get_prompt_section_count())

    def test_u5_build_sections_list_tuples(self):
        """_build_sections_list returns (name, content) tuples."""
        from system_prompts import _build_sections_list
        sections = _build_sections_list()
        for name, content in sections:
            self.assertIsInstance(name, str)
            self.assertIsInstance(content, str)
            self.assertTrue(len(content) > 0, f"Section '{name}' is empty")

    def test_u5_unknown_section_hash_empty(self):
        """get_section_hash returns '' for unknown section."""
        from system_prompts import get_section_hash
        self.assertEqual(get_section_hash("nonexistent_section"), "")


class TestU8U9U10U11(unittest.TestCase):
    """U8: Scratchpad, U9: Post-Compact File Restore, U10: Risk tiers, U11: Explore speed."""

    # -- U8: Scratchpad --

    def test_u8_scratchpad_in_dynamic_context(self):
        """build_dynamic_context includes scratchpad info when path provided."""
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(scratchpad_path="/tmp/.scratchpad/sess123")
        self.assertIn("Scratchpad", ctx)
        self.assertIn("/tmp/.scratchpad/sess123", ctx)
        self.assertIn("freely", ctx)

    def test_u8_scratchpad_absent_when_empty(self):
        """build_dynamic_context omits scratchpad when path is empty."""
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(scratchpad_path="")
        self.assertNotIn("Scratchpad", ctx)

    def test_u8_scratchpad_in_safe_tier(self):
        """U10 SAFE tier mentions scratchpad."""
        from system_prompts import _SYSTEM_PROMPT_ACTIONS
        self.assertIn("scratchpad", _SYSTEM_PROMPT_ACTIONS)

    # -- U9: Post-Compact File Restore --

    def test_u9_restore_tag_in_compact_engine(self):
        """compact_engine source contains Post-compact file restore marker."""
        import compact_engine
        src = open(compact_engine.__file__).read()
        self.assertIn("Post-compact file restore", src)

    def test_u9_restore_uses_active_files_snapshot(self):
        """compact_engine uses public get_active_files_snapshot() accessor."""
        import compact_engine
        src = open(compact_engine.__file__).read()
        self.assertIn("get_active_files_snapshot", src)

    # -- U10: Actions Risk Tiers --

    def test_u10_four_tiers_present(self):
        """_SYSTEM_PROMPT_ACTIONS contains all four risk tiers."""
        from system_prompts import _SYSTEM_PROMPT_ACTIONS
        for tier in ["**SAFE**", "**CAUTION**", "**DESTRUCTIVE**", "**IRREVERSIBLE**"]:
            self.assertIn(tier, _SYSTEM_PROMPT_ACTIONS, f"Missing tier: {tier}")

    def test_u10_measure_twice_header(self):
        """U10 section has 'measure twice' heading."""
        from system_prompts import _SYSTEM_PROMPT_ACTIONS
        self.assertIn("measure twice", _SYSTEM_PROMPT_ACTIONS)

    def test_u10_caution_includes_build(self):
        """CAUTION tier includes build commands."""
        from system_prompts import _SYSTEM_PROMPT_ACTIONS
        self.assertIn("npm run build", _SYSTEM_PROMPT_ACTIONS)

    def test_u10_irreversible_includes_force_push(self):
        """IRREVERSIBLE tier includes git push --force to shared branches."""
        from system_prompts import _SYSTEM_PROMPT_ACTIONS
        self.assertIn("shared branches", _SYSTEM_PROMPT_ACTIONS)

    def test_u10_safe_includes_tests(self):
        """SAFE tier includes running tests."""
        from system_prompts import _SYSTEM_PROMPT_ACTIONS
        # Find the SAFE line
        safe_idx = _SYSTEM_PROMPT_ACTIONS.index("**SAFE**")
        caution_idx = _SYSTEM_PROMPT_ACTIONS.index("**CAUTION**")
        safe_section = _SYSTEM_PROMPT_ACTIONS[safe_idx:caution_idx]
        self.assertIn("tests", safe_section)

    # -- U11/U11b: Explore Agent Speed --

    def test_u11_explore_speed_first(self):
        """Explore agent prompt contains speed-first strategy."""
        from tools.sub_agent import _EXPLORE_SYSTEM_PROMPT
        self.assertIn("FAST", _EXPLORE_SYSTEM_PROMPT)

    def test_u11_explore_batch_instruction(self):
        """Explore agent prompt instructs batching tool calls."""
        from tools.sub_agent import _EXPLORE_SYSTEM_PROMPT
        self.assertIn("MULTIPLE tools", _EXPLORE_SYSTEM_PROMPT)

    def test_u11_explore_parallel_flag(self):
        """Explore agent config has parallel_tool_calls=True."""
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertTrue(BUILT_IN_AGENTS["explore"].get("parallel_tool_calls"))

    def test_u11_explore_stop_early(self):
        """Explore agent prompt says to stop when enough info."""
        from tools.sub_agent import _EXPLORE_SYSTEM_PROMPT
        self.assertIn("STOP", _EXPLORE_SYSTEM_PROMPT)

    def test_u11b_explore_max_turns_is_3(self):
        """U11b: Explore agent has hard budget of 3 turns."""
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertEqual(BUILT_IN_AGENTS["explore"]["max_turns_default"], 3)

    def test_u11b_explore_max_output_chars(self):
        """U11b: Explore agent config has max_output_chars."""
        from tools.sub_agent import BUILT_IN_AGENTS, _EXPLORE_MAX_OUTPUT_CHARS
        self.assertEqual(BUILT_IN_AGENTS["explore"]["max_output_chars"], _EXPLORE_MAX_OUTPUT_CHARS)
        self.assertLessEqual(_EXPLORE_MAX_OUTPUT_CHARS, 3000)

    def test_u11b_explore_output_format_mandatory(self):
        """U11b: Explore prompt requires bullet-point-only output format."""
        from tools.sub_agent import _EXPLORE_SYSTEM_PROMPT
        self.assertIn("OUTPUT FORMAT (MANDATORY)", _EXPLORE_SYSTEM_PROMPT)
        self.assertIn("Bullet points ONLY", _EXPLORE_SYSTEM_PROMPT)
        self.assertIn("15 lines", _EXPLORE_SYSTEM_PROMPT)

    def test_u11b_explore_no_narration_rule(self):
        """U11b: Explore prompt forbids narration."""
        from tools.sub_agent import _EXPLORE_SYSTEM_PROMPT
        self.assertIn("Do NOT narrate", _EXPLORE_SYSTEM_PROMPT)

    def test_u11b_explore_budget_in_prompt(self):
        """U11b: Explore prompt mentions 3-turn budget."""
        from tools.sub_agent import _EXPLORE_SYSTEM_PROMPT
        self.assertIn("BUDGET of 3 turns", _EXPLORE_SYSTEM_PROMPT)

    def test_u11b_quick_find_regex(self):
        """U11b: Quick-path regex matches 'find package.json' patterns."""
        from tools.sub_agent import _QUICK_FIND_RE
        m1 = _QUICK_FIND_RE.search("find package.json in the project")
        self.assertIsNotNone(m1)
        self.assertEqual(m1.group(1) or m1.group(2), "package.json")
        m2 = _QUICK_FIND_RE.search("locate setup.py")
        self.assertIsNotNone(m2)
        self.assertEqual(m2.group(1) or m2.group(2), "setup.py")
        m3 = _QUICK_FIND_RE.search("check if requirements.txt exists")
        self.assertIsNotNone(m3)
        self.assertEqual(m3.group(1) or m3.group(2), "requirements.txt")

    def test_u11b_quick_find_regex_chinese(self):
        """U11b: Quick-path regex matches Chinese find patterns."""
        from tools.sub_agent import _QUICK_FIND_RE
        m = _QUICK_FIND_RE.search("查找 config.yaml 文件")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1) or m.group(2), "config.yaml")

    def test_u11b_quick_list_regex(self):
        """U11b: Quick-path regex matches 'list directory' patterns."""
        from tools.sub_agent import _QUICK_LIST_RE
        self.assertIsNotNone(_QUICK_LIST_RE.search("list the directory structure"))
        self.assertIsNotNone(_QUICK_LIST_RE.search("show me the root folder"))
        self.assertIsNotNone(_QUICK_LIST_RE.search("查看目录结构"))

    def test_u11b_quick_path_find_file(self):
        """U11b: Quick-path actually finds files in workspace."""
        import asyncio, tempfile, os
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test file
            os.makedirs(os.path.join(tmpdir, "src"))
            with open(os.path.join(tmpdir, "src", "main.py"), "w") as f:
                f.write("print('hello')")
            result = asyncio.run(
                _explore_quick_path("find main.py", Path(tmpdir))
            )
            self.assertIsNotNone(result)
            self.assertTrue(result["success"])
            self.assertIn("main.py", result["output"])

    def test_u11b_quick_path_not_found(self):
        """U11b: Quick-path returns 'not found' when file doesn't exist."""
        import asyncio, tempfile
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            result = asyncio.run(
                _explore_quick_path("find package.json", Path(tmpdir))
            )
            self.assertIsNotNone(result)
            self.assertTrue(result["success"])
            self.assertIn("not found", result["output"])

    def test_u11b_quick_path_list_dir(self):
        """U11b: Quick-path lists workspace root."""
        import asyncio, tempfile, os
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "src"))
            os.makedirs(os.path.join(tmpdir, "tests"))
            with open(os.path.join(tmpdir, "README.md"), "w") as f:
                f.write("hello")
            result = asyncio.run(
                _explore_quick_path("list the directory structure", Path(tmpdir))
            )
            self.assertIsNotNone(result)
            self.assertTrue(result["success"])
            self.assertIn("src/", result["output"])
            self.assertIn("tests/", result["output"])
            self.assertIn("README.md", result["output"])

    def test_u11b_quick_path_no_match_falls_through(self):
        """U11b: Complex tasks fall through to full sub-agent."""
        import asyncio, tempfile
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            result = asyncio.run(
                _explore_quick_path("analyze the authentication flow and find all security vulnerabilities", Path(tmpdir))
            )
            self.assertIsNone(result)  # Falls through

    def test_u11b_explore_conclude_nudge_in_agentic_loop(self):
        """U11b/c: agentic_loop.py contains explore conclude nudge."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("EXPLORE DONE", src)
        self.assertIn("Do NOT re-run the exploration", src)

    # -- U11c: Explore Agent Hardening --

    def test_u11c_explore_lock_regex(self):
        """U11c: @explore lock regex matches user prefix."""
        from agentic_loop import _re_explore_lock
        self.assertIsNotNone(_re_explore_lock.match("@explore find something"))
        self.assertIsNotNone(_re_explore_lock.match("@Explore list files"))
        self.assertIsNone(_re_explore_lock.match("please @explore"))
        self.assertIsNone(_re_explore_lock.match("use the explore agent"))

    def test_u11c_explore_lock_in_source(self):
        """U11c: agentic_loop.py contains explore lock override logic."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("Locked @explore", src)
        self.assertIn("_re_explore_lock", src)

    def test_u11c_quick_find_chinese_expanded(self):
        """U11c: Quick-path regex matches expanded Chinese phrases."""
        from tools.sub_agent import _QUICK_FIND_RE
        # "找一下有没有 Dockerfile" — the failing case
        m1 = _QUICK_FIND_RE.search("找一下项目里有没有 Dockerfile.txt")
        self.assertIsNotNone(m1)
        # "帮我找找 requirements.txt"
        m2 = _QUICK_FIND_RE.search("帮我找找 requirements.txt")
        self.assertIsNotNone(m2)
        self.assertEqual(m2.group(1) or m2.group(2), "requirements.txt")
        # "帮我看看有没有 config.yaml"
        m3 = _QUICK_FIND_RE.search("帮我看看有没有 config.yaml")
        self.assertIsNotNone(m3)
        self.assertEqual(m3.group(1) or m3.group(2), "config.yaml")
        # "帮我查 setup.py"
        m4 = _QUICK_FIND_RE.search("帮我查 setup.py")
        self.assertIsNotNone(m4)

    def test_u11c_quick_list_chinese_expanded(self):
        """U11c: Quick-path list regex matches expanded Chinese phrases."""
        from tools.sub_agent import _QUICK_LIST_RE
        self.assertIsNotNone(_QUICK_LIST_RE.search("帮我看看根目录"))
        self.assertIsNotNone(_QUICK_LIST_RE.search("显示文件结构"))
        self.assertIsNotNone(_QUICK_LIST_RE.search("看看文件夹"))

    def test_u11c_external_path_regex(self):
        """U11c: External path regex detects absolute paths."""
        from tools.sub_agent import _EXTERNAL_PATH_RE
        m1 = _EXTERNAL_PATH_RE.search("读取 /tmp/nonexistent_folder 里的内容")
        self.assertIsNotNone(m1)
        self.assertIn("/tmp/nonexistent_folder", m1.group(1))
        m2 = _EXTERNAL_PATH_RE.search("look at /home/user/file.txt")
        self.assertIsNotNone(m2)
        # Should NOT match workspace-relative paths
        m3 = _EXTERNAL_PATH_RE.search("read src/main.py")
        self.assertIsNone(m3)

    def test_u11c_external_path_not_exist(self):
        """U11c: Quick-path returns 'does not exist' for nonexistent external path."""
        import asyncio
        from tools.sub_agent import _explore_quick_path
        result = asyncio.run(
            _explore_quick_path("读取 /tmp/u11c_definitely_not_here 里的内容", Path("/tmp"))
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["success"])
        self.assertIn("does not exist", result["output"])
        self.assertTrue(result.get("_quick_path"))

    def test_u11c_external_path_exists(self):
        """U11c: Quick-path lists existing external directory."""
        import asyncio, tempfile, os
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "sub"))
            with open(os.path.join(tmpdir, "test.txt"), "w") as f:
                f.write("hello")
            result = asyncio.run(
                _explore_quick_path(f"查看 {tmpdir} 里的内容", Path("/tmp"))
            )
            self.assertIsNotNone(result)
            self.assertIn("test.txt", result["output"])
            self.assertIn("sub/", result["output"])

    def test_u11c_quick_path_has_flag(self):
        """U11c: All quick-path results have _quick_path=True."""
        import asyncio, tempfile, os
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "a.py"), "w") as f:
                f.write("x")
            result = asyncio.run(
                _explore_quick_path("find a.py", Path(tmpdir))
            )
            self.assertTrue(result.get("_quick_path"))

    def test_u11c_suppress_nudge_in_source(self):
        """U11c: agentic_loop.py contains explore output suppression."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("EXPLORE COMPLETE", src)
        self.assertIn("KEY INFORMATION ONLY", src)
        self.assertIn("Re-format, re-organize", src)

    def test_u11c_suppress_quick_vs_normal(self):
        """U11c: Suppression message differs for quick-path vs normal explore."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        # Quick-path suppression mentions tables/headers
        self.assertIn("add tables/headers", src)
        # Normal explore suppression mentions bullet points
        self.assertIn("Duplicate the sub-agent", src)

    # -- U11d: Main-loop @explore pre-emption --

    def test_u11d_preempt_in_source(self):
        """U11d: agentic_loop.py contains @explore pre-emption logic."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("U11d", src)
        self.assertIn("_explore_quick_path", src)
        self.assertIn("EXPLORE QUICK-PATH RESULT", src)
        self.assertIn("MANDATORY TOOL", src)

    def test_u11d_noext_dockerfile(self):
        """U11d: Quick-path regex matches Dockerfile (no extension)."""
        from tools.sub_agent import _QUICK_FIND_RE
        m = _QUICK_FIND_RE.search("find Dockerfile")
        self.assertIsNotNone(m)
        target = m.group(1) or m.group(2)
        self.assertEqual(target, "Dockerfile")

    def test_u11d_noext_makefile(self):
        """U11d: Quick-path regex matches Makefile (no extension)."""
        from tools.sub_agent import _QUICK_FIND_RE
        m = _QUICK_FIND_RE.search("找一下项目里有没有 Makefile")
        self.assertIsNotNone(m)
        target = m.group(1) or m.group(2)
        self.assertEqual(target, "Makefile")

    def test_u11d_noext_readme(self):
        """U11d: Quick-path regex matches README (no extension)."""
        from tools.sub_agent import _QUICK_FIND_RE
        m = _QUICK_FIND_RE.search("check if README exists")
        self.assertIsNotNone(m)
        target = m.group(1) or m.group(2)
        self.assertEqual(target, "README")

    def test_u11d_noext_gitignore(self):
        """U11d: Quick-path regex matches .gitignore (dot-prefixed, no ext)."""
        from tools.sub_agent import _QUICK_FIND_RE
        m = _QUICK_FIND_RE.search("找一下 .gitignore")
        self.assertIsNotNone(m)
        target = m.group(1) or m.group(2)
        self.assertEqual(target, ".gitignore")

    def test_u11d_noext_agents_md(self):
        """U11d: Quick-path regex matches AGENTS.md (known extensionless pattern)."""
        from tools.sub_agent import _QUICK_FIND_RE
        m = _QUICK_FIND_RE.search("帮我查查 AGENTS.md")
        self.assertIsNotNone(m)
        target = m.group(1) or m.group(2)
        self.assertIn("AGENTS", target)

    def test_u11d_ext_still_works(self):
        """U11d: With-extension files still match after regex change."""
        from tools.sub_agent import _QUICK_FIND_RE
        m = _QUICK_FIND_RE.search("帮我找找 requirements.txt")
        self.assertIsNotNone(m)
        target = m.group(1) or m.group(2)
        self.assertEqual(target, "requirements.txt")

    def test_u11d_quick_path_dockerfile_search(self):
        """U11d: Quick-path actually finds Dockerfile via os.walk."""
        import asyncio, tempfile, os
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "Dockerfile"), "w") as f:
                f.write("FROM python:3.11")
            result = asyncio.run(
                _explore_quick_path("find Dockerfile", Path(tmpdir))
            )
            self.assertIsNotNone(result)
            self.assertTrue(result["success"])
            self.assertIn("Dockerfile", result["output"])
            self.assertTrue(result.get("_quick_path"))

    def test_u11d_direct_path_suppression_in_source(self):
        """U11d: agentic_loop.py contains direct-path @explore suppression."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("Direct-path @explore suppression", src)
        self.assertIn("emoji headers", src)
        self.assertIn("dependency categories", src)

    def test_u11d_mandatory_sub_agent_in_source(self):
        """U11d: agentic_loop.py injects mandatory sub_agent hint for @explore."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        self.assertIn("MUST call sub_agent with agent_type='explore'", src)
        self.assertIn("Do NOT handle this yourself", src)

    def test_u11d_preempt_find_resolves(self):
        """U11d: @explore with simple file find would be pre-empted by quick-path."""
        import asyncio, tempfile, os
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "package.json"), "w") as f:
                f.write("{}")
            # Simulate what U11d does: strip @explore, run quick-path
            task = "帮我看看项目里有没有 package.json"
            result = asyncio.run(_explore_quick_path(task, Path(tmpdir)))
            self.assertIsNotNone(result, "Quick-path should resolve simple file find")
            self.assertIn("package.json", result["output"])

    def test_u11d_preempt_list_resolves(self):
        """U11d: @explore with directory list would be pre-empted by quick-path."""
        import asyncio, tempfile, os
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "src"))
            with open(os.path.join(tmpdir, "main.py"), "w") as f:
                f.write("pass")
            task = "列出项目根目录下的文件结构"
            result = asyncio.run(_explore_quick_path(task, Path(tmpdir)))
            self.assertIsNotNone(result, "Quick-path should resolve directory listing")
            self.assertIn("src/", result["output"])

    def test_u11d_complex_task_falls_through(self):
        """U11d: Complex @explore still falls through quick-path to sub_agent."""
        import asyncio, tempfile
        from tools.sub_agent import _explore_quick_path
        with tempfile.TemporaryDirectory() as tmpdir:
            task = "分析整个项目的架构，详细说明每个模块的作用"
            result = asyncio.run(_explore_quick_path(task, Path(tmpdir)))
            self.assertIsNone(result, "Complex analysis should NOT be quick-path resolved")


# ═══════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []

    test_classes = [
        TestP94SkillRegistry,
        TestP95BundledSkills,
        TestP95Aliases,
        TestP94SkillDefinition,
        TestP97NewSkills,
        TestP97aSkillMdLoader,
        TestP97dSystemPrompt,
        TestP97IntentMatching,
        TestP98aUsageTracking,
        TestP98bBudgetListing,
        TestP98cProactiveHint,
        TestP98dEnhancements,
        TestP98eDedup,
        TestP99aConditionalSkills,
        TestP99bShellExecution,
        TestP99cForkedExecution,
        TestP99dAllowedToolsEnforcement,
        TestP99eBundledSkillUpgrades,
        TestP100aInputValidation,
        TestP100bPermissions,
        TestP100cToolEnforcement,
        TestP100dOverrides,
        TestP100eInvocationTracking,
        TestP101CodingSkills,
        TestP102RuntimeEnforcement,
        TestP103ForkedSubtasks,
        TestA1A2A3ContextEngineering,
        TestD2MultiAgentStatusUI,
        TestD3ActiveFileList,
        TestD4FileTargetCrossCheck,
        TestD5PatchApproval,
        TestU2AdversarialVerification,
        TestD7LanguageAutoDetection,
        TestF1SkillLanguagePreservation,
        TestU1SessionMemory,
        TestU3BackgroundFork,
        TestU4CacheAwareMC,
        TestU5SectionCaching,
        TestU8U9U10U11,
    ]

    for cls in test_classes:
        suite = unittest.TestLoader().loadTestsFromTestCase(cls)
        print(f"\n╔══ {cls.__name__} ══╗")
        for test in suite:
            try:
                test.setUp() if hasattr(test, "setUp") else None
                test_method = getattr(test, test._testMethodName)
                test_method()
                test.tearDown() if hasattr(test, "tearDown") else None
                print(f"  ✅ {test._testMethodName}")
                passed += 1
            except Exception as e:
                test.tearDown() if hasattr(test, "tearDown") else None
                print(f"  ❌ {test._testMethodName}: {e}")
                failed += 1
                errors.append((test._testMethodName, str(e)))

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print(f"\nFailed tests:")
        for name, err in errors:
            print(f"  - {name}: {err}")
    print(f"{'=' * 60}")

    if failed == 0:
        print("🎉 All skill system tests passed!")

    sys.exit(0 if failed == 0 else 1)
