#!/usr/bin/env python3
"""
Tests for P90-P93: Nanobot Memory Persistence System.

Covers:
  - P90: Memory Manager (save/recall/update/forget/list/search + index + frontmatter)
  - P91: Memory Prompts (system prompt injection + truncation)
  - P92: Memory tool (execute function + all actions)
  - P93: Memory search (keyword matching + relevance)
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

# Ensure parent is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestP90MemoryManager(unittest.TestCase):
    """P90: Core memory CRUD operations."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.mem_dir = self.tmpdir / "memory"
        self.mem_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_memory_creates_file_and_index(self):
        from memory.memory_manager import save_memory, read_index
        result = save_memory(self.mem_dir, "User Role", "I am a data scientist", "user", "user is a data scientist")
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "created")
        self.assertTrue((self.mem_dir / "user_role.md").exists())
        index = read_index(self.mem_dir)
        self.assertIn("user_role.md", index)
        self.assertIn("User Role", index)

    def test_save_memory_with_frontmatter(self):
        from memory.memory_manager import save_memory, parse_frontmatter
        save_memory(self.mem_dir, "Test Topic", "Some content", "feedback", "test description")
        content = (self.mem_dir / "test_topic.md").read_text()
        fm, body = parse_frontmatter(content)
        self.assertEqual(fm["name"], "Test Topic")
        self.assertEqual(fm["type"], "feedback")
        self.assertEqual(fm["description"], "test description")
        self.assertIn("Some content", body)

    def test_save_memory_invalid_type(self):
        from memory.memory_manager import save_memory
        result = save_memory(self.mem_dir, "Test", "content", "invalid_type")
        self.assertFalse(result["success"])
        self.assertIn("Invalid memory type", result["error"])

    def test_save_memory_update_existing(self):
        from memory.memory_manager import save_memory
        save_memory(self.mem_dir, "Topic", "v1", "user")
        result = save_memory(self.mem_dir, "Topic", "v2", "user")
        self.assertEqual(result["action"], "updated")
        content = (self.mem_dir / "topic.md").read_text()
        self.assertIn("v2", content)

    def test_recall_memory_by_topic(self):
        from memory.memory_manager import save_memory, recall_memory
        save_memory(self.mem_dir, "My Prefs", "I prefer vim", "user", "editor preference")
        result = recall_memory(self.mem_dir, "My Prefs")
        self.assertTrue(result["success"])
        self.assertEqual(result["type"], "user")
        self.assertIn("vim", result["content"])

    def test_recall_memory_by_filename(self):
        from memory.memory_manager import save_memory, recall_memory
        save_memory(self.mem_dir, "Config", "use bun", "feedback")
        result = recall_memory(self.mem_dir, "config.md")
        self.assertTrue(result["success"])
        self.assertIn("bun", result["content"])

    def test_recall_memory_not_found(self):
        from memory.memory_manager import recall_memory
        result = recall_memory(self.mem_dir, "nonexistent")
        self.assertFalse(result["success"])
        self.assertIn("No memory found", result["error"])

    def test_forget_memory(self):
        from memory.memory_manager import save_memory, forget_memory, read_index
        save_memory(self.mem_dir, "Temp", "delete me", "project")
        result = forget_memory(self.mem_dir, "Temp")
        self.assertTrue(result["success"])
        self.assertFalse((self.mem_dir / "temp.md").exists())
        index = read_index(self.mem_dir)
        self.assertNotIn("temp.md", index)

    def test_forget_memory_not_found(self):
        from memory.memory_manager import forget_memory
        result = forget_memory(self.mem_dir, "nonexistent")
        self.assertFalse(result["success"])

    def test_list_memories_empty(self):
        from memory.memory_manager import list_memories
        result = list_memories(self.mem_dir)
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)

    def test_list_memories_with_entries(self):
        from memory.memory_manager import save_memory, list_memories
        save_memory(self.mem_dir, "A", "content a", "user")
        save_memory(self.mem_dir, "B", "content b", "feedback")
        result = list_memories(self.mem_dir)
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        names = [m["name"] for m in result["memories"]]
        self.assertIn("A", names)
        self.assertIn("B", names)

    def test_update_memory_preserves_type(self):
        from memory.memory_manager import save_memory, update_memory, parse_frontmatter
        save_memory(self.mem_dir, "Style", "concise", "feedback", "response style")
        update_memory(self.mem_dir, "Style", "very concise and terse")
        content = (self.mem_dir / "style.md").read_text()
        fm, body = parse_frontmatter(content)
        self.assertEqual(fm["type"], "feedback")
        self.assertIn("very concise", body)

    def test_update_memory_not_found(self):
        from memory.memory_manager import update_memory
        result = update_memory(self.mem_dir, "nonexistent", "new content")
        self.assertFalse(result["success"])


class TestP90Frontmatter(unittest.TestCase):
    """P90: Frontmatter parsing."""

    def test_parse_valid_frontmatter(self):
        from memory.memory_manager import parse_frontmatter
        content = "---\nname: Test\ntype: user\ndescription: A test\n---\n\nBody text here."
        fm, body = parse_frontmatter(content)
        self.assertEqual(fm["name"], "Test")
        self.assertEqual(fm["type"], "user")
        self.assertEqual(fm["description"], "A test")
        self.assertIn("Body text", body)

    def test_parse_no_frontmatter(self):
        from memory.memory_manager import parse_frontmatter
        content = "Just plain text"
        fm, body = parse_frontmatter(content)
        self.assertEqual(fm, {})
        self.assertEqual(body, content)

    def test_build_frontmatter(self):
        from memory.memory_manager import build_frontmatter
        fm = build_frontmatter("My Topic", "A description", "feedback")
        self.assertIn("name: My Topic", fm)
        self.assertIn("type: feedback", fm)
        self.assertIn("description: A description", fm)
        self.assertTrue(fm.startswith("---\n"))
        self.assertIn("---\n\n", fm)


class TestP90Index(unittest.TestCase):
    """P90: MEMORY.md index operations."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.mem_dir = self.tmpdir / "memory"
        self.mem_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_read_empty_index(self):
        from memory.memory_manager import read_index
        self.assertEqual(read_index(self.mem_dir), "")

    def test_write_and_read_index(self):
        from memory.memory_manager import write_index, read_index
        write_index(self.mem_dir, "- [Test](test.md) — description")
        content = read_index(self.mem_dir)
        self.assertIn("test.md", content)

    def test_add_index_entry(self):
        from memory.memory_manager import _add_index_entry, read_index
        _add_index_entry(self.mem_dir, "a.md", "Title A", "desc A")
        _add_index_entry(self.mem_dir, "b.md", "Title B", "desc B")
        index = read_index(self.mem_dir)
        self.assertIn("a.md", index)
        self.assertIn("b.md", index)

    def test_add_index_entry_updates_existing(self):
        from memory.memory_manager import _add_index_entry, read_index
        _add_index_entry(self.mem_dir, "a.md", "Old Title", "old desc")
        _add_index_entry(self.mem_dir, "a.md", "New Title", "new desc")
        index = read_index(self.mem_dir)
        self.assertNotIn("Old Title", index)
        self.assertIn("New Title", index)
        self.assertEqual(index.count("a.md"), 1)

    def test_remove_index_entry(self):
        from memory.memory_manager import _add_index_entry, _remove_index_entry, read_index
        _add_index_entry(self.mem_dir, "a.md", "A", "desc a")
        _add_index_entry(self.mem_dir, "b.md", "B", "desc b")
        _remove_index_entry(self.mem_dir, "a.md")
        index = read_index(self.mem_dir)
        self.assertNotIn("a.md", index)
        self.assertIn("b.md", index)


class TestP90Truncation(unittest.TestCase):
    """P90: MEMORY.md truncation (from Claw's truncateEntrypointContent)."""

    def test_no_truncation_needed(self):
        from memory.memory_manager import truncate_entrypoint_content
        content = "- [A](a.md) — desc\n" * 10
        t = truncate_entrypoint_content(content)
        self.assertFalse(t.was_line_truncated)
        self.assertFalse(t.was_byte_truncated)
        self.assertEqual(t.line_count, 10)

    def test_line_truncation(self):
        from memory.memory_manager import truncate_entrypoint_content, MAX_ENTRYPOINT_LINES
        content = "\n".join([f"- [Item{i}](item{i}.md) — desc" for i in range(250)])
        t = truncate_entrypoint_content(content)
        self.assertTrue(t.was_line_truncated)
        self.assertEqual(t.line_count, 250)
        # Content should be truncated to MAX_ENTRYPOINT_LINES lines + warning
        self.assertIn("WARNING", t.content)
        self.assertIn(str(MAX_ENTRYPOINT_LINES), t.content)

    def test_byte_truncation(self):
        from memory.memory_manager import truncate_entrypoint_content, MAX_ENTRYPOINT_BYTES
        # Create content that's under 200 lines but over 25KB
        content = "\n".join([f"- [Item{i}](item{i}.md) — {'x' * 200}" for i in range(150)])
        self.assertGreater(len(content), MAX_ENTRYPOINT_BYTES)
        t = truncate_entrypoint_content(content)
        self.assertTrue(t.was_byte_truncated)
        self.assertIn("WARNING", t.content)

    def test_both_truncations(self):
        from memory.memory_manager import truncate_entrypoint_content
        content = "\n".join([f"- [Item{i}](item{i}.md) — {'x' * 200}" for i in range(300)])
        t = truncate_entrypoint_content(content)
        self.assertTrue(t.was_line_truncated)
        self.assertTrue(t.was_byte_truncated)
        self.assertIn("WARNING", t.content)


class TestP90Slugify(unittest.TestCase):
    """P90: Topic name to filename slugification."""

    def test_basic_slugify(self):
        from memory.memory_manager import _slugify
        self.assertEqual(_slugify("User Preferences"), "user_preferences")

    def test_special_chars(self):
        from memory.memory_manager import _slugify
        self.assertEqual(_slugify("Don't use npm!"), "don_t_use_npm")

    def test_max_length(self):
        from memory.memory_manager import _slugify
        long_name = "a" * 100
        slug = _slugify(long_name)
        self.assertLessEqual(len(slug), 60)

    def test_unicode(self):
        from memory.memory_manager import _slugify
        slug = _slugify("用户偏好")
        self.assertTrue(len(slug) > 0)


class TestP91MemoryPrompts(unittest.TestCase):
    """P91: Memory prompt building and injection."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.mem_dir = self.tmpdir / "memory"
        self.mem_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_build_memory_prompt_empty(self):
        from memory.memory_prompts import build_memory_prompt
        prompt = build_memory_prompt(self.mem_dir)
        self.assertIn("Persistent Memory", prompt)
        self.assertIn("currently empty", prompt)
        self.assertIn("Types of memory", prompt)
        self.assertIn("user", prompt)
        self.assertIn("feedback", prompt)
        self.assertIn("project", prompt)
        self.assertIn("reference", prompt)

    def test_build_memory_prompt_with_index(self):
        from memory.memory_manager import save_memory
        from memory.memory_prompts import build_memory_prompt
        save_memory(self.mem_dir, "Prefs", "vim user", "user", "editor preference")
        prompt = build_memory_prompt(self.mem_dir)
        self.assertIn("prefs.md", prompt)
        self.assertIn("Prefs", prompt)
        self.assertNotIn("currently empty", prompt)

    def test_build_memory_prompt_includes_guidance(self):
        from memory.memory_prompts import build_memory_prompt
        prompt = build_memory_prompt(self.mem_dir)
        self.assertIn("What NOT to save", prompt)
        self.assertIn("When to access memories", prompt)
        self.assertIn("Before recommending from memory", prompt)
        self.assertIn("How to save memories", prompt)

    def test_build_memory_prompt_truncates_large_index(self):
        from memory.memory_manager import write_index
        from memory.memory_prompts import build_memory_prompt
        large_index = "\n".join([f"- [Item{i}](item{i}.md) — description" for i in range(250)])
        write_index(self.mem_dir, large_index)
        prompt = build_memory_prompt(self.mem_dir)
        self.assertIn("WARNING", prompt)

    def test_memory_context_for_query_empty(self):
        from memory.memory_prompts import build_memory_context_for_query
        ctx = build_memory_context_for_query(self.mem_dir, "anything")
        self.assertEqual(ctx, "")

    def test_memory_context_for_query_with_match(self):
        from memory.memory_manager import save_memory
        from memory.memory_prompts import build_memory_context_for_query
        save_memory(self.mem_dir, "Python Testing", "always use pytest", "feedback", "testing framework preference")
        ctx = build_memory_context_for_query(self.mem_dir, "how to test python code")
        self.assertIn("RECALLED MEMORIES", ctx)
        self.assertIn("pytest", ctx)


class TestP90Search(unittest.TestCase):
    """P93: Memory search and relevance matching."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.mem_dir = self.tmpdir / "memory"
        self.mem_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_search_by_keyword(self):
        from memory.memory_manager import save_memory, find_relevant_memories
        save_memory(self.mem_dir, "Python Testing", "use pytest", "feedback", "testing framework")
        save_memory(self.mem_dir, "Code Style", "use black", "feedback", "code formatter")
        results = find_relevant_memories(self.mem_dir, "testing", max_results=5)
        self.assertEqual(len(results), 1)
        self.assertIn("pytest", results[0]["content"])

    def test_search_multiple_results(self):
        from memory.memory_manager import save_memory, find_relevant_memories
        save_memory(self.mem_dir, "Python Libs", "numpy pandas", "reference", "python libraries")
        save_memory(self.mem_dir, "Python Style", "pep8 black", "feedback", "python code style")
        results = find_relevant_memories(self.mem_dir, "python", max_results=5)
        self.assertEqual(len(results), 2)

    def test_search_no_results(self):
        from memory.memory_manager import save_memory, find_relevant_memories
        save_memory(self.mem_dir, "Unrelated", "nothing here", "user")
        results = find_relevant_memories(self.mem_dir, "quantum physics", max_results=5)
        self.assertEqual(len(results), 0)

    def test_search_with_already_surfaced(self):
        from memory.memory_manager import save_memory, find_relevant_memories
        save_memory(self.mem_dir, "Python A", "content a", "user", "python thing a")
        save_memory(self.mem_dir, "Python B", "content b", "user", "python thing b")
        path_a = str(self.mem_dir / "python_a.md")
        results = find_relevant_memories(self.mem_dir, "python", already_surfaced={path_a})
        names = [r["name"] for r in results]
        self.assertNotIn("Python A", names)
        self.assertIn("Python B", names)


class TestP92MemoryTool(unittest.TestCase):
    """P92: Memory tool execute() function."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.workspace = self.tmpdir / "workspace"
        self.workspace.mkdir()
        # Pre-create the memory dir so we can control its location
        self.mem_dir = self.workspace / ".nanobot_memory"
        self.mem_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_action(self):
        from tools.memory import execute
        result = execute({
            "action": "save",
            "topic": "Test Memory",
            "content": "This is a test",
            "type": "user",
            "description": "test description",
        }, self.workspace)
        self.assertTrue(result["success"])
        self.assertIn("Created", result["output"])

    def test_recall_action(self):
        from tools.memory import execute
        execute({
            "action": "save",
            "topic": "Recall Test",
            "content": "Important info",
            "type": "feedback",
        }, self.workspace)
        result = execute({"action": "recall", "topic": "Recall Test"}, self.workspace)
        self.assertTrue(result["success"])
        self.assertIn("Important info", result["output"])

    def test_list_action_empty(self):
        from tools.memory import execute
        result = execute({"action": "list"}, self.workspace)
        self.assertTrue(result["success"])
        self.assertIn("No memories", result["output"])

    def test_list_action_with_entries(self):
        from tools.memory import execute
        execute({"action": "save", "topic": "A", "content": "c", "type": "user"}, self.workspace)
        result = execute({"action": "list"}, self.workspace)
        self.assertTrue(result["success"])
        self.assertIn("1 memories", result["output"])

    def test_forget_action(self):
        from tools.memory import execute
        execute({"action": "save", "topic": "Delete Me", "content": "bye", "type": "project"}, self.workspace)
        result = execute({"action": "forget", "topic": "Delete Me"}, self.workspace)
        self.assertTrue(result["success"])
        self.assertIn("Deleted", result["output"])

    def test_update_action(self):
        from tools.memory import execute
        execute({"action": "save", "topic": "Updatable", "content": "v1", "type": "user"}, self.workspace)
        result = execute({"action": "update", "topic": "Updatable", "content": "v2"}, self.workspace)
        self.assertTrue(result["success"])
        # Verify content updated
        recall = execute({"action": "recall", "topic": "Updatable"}, self.workspace)
        self.assertIn("v2", recall["output"])

    def test_search_action(self):
        from tools.memory import execute
        execute({"action": "save", "topic": "Python Prefs", "content": "use pytest", "type": "feedback", "description": "python testing"}, self.workspace)
        result = execute({"action": "search", "topic": "python"}, self.workspace)
        self.assertTrue(result["success"])
        self.assertIn("pytest", result["output"])

    def test_invalid_action(self):
        from tools.memory import execute
        result = execute({"action": "invalid"}, self.workspace)
        self.assertFalse(result["success"])
        self.assertIn("Unknown action", result["error"])

    def test_save_missing_topic(self):
        from tools.memory import execute
        result = execute({"action": "save", "content": "no topic"}, self.workspace)
        self.assertFalse(result["success"])
        self.assertIn("Missing", result["error"])

    def test_save_missing_content(self):
        from tools.memory import execute
        result = execute({"action": "save", "topic": "has topic"}, self.workspace)
        self.assertFalse(result["success"])
        self.assertIn("Missing", result["error"])


class TestP91DynamicContextIntegration(unittest.TestCase):
    """P91: Memory integration with build_dynamic_context."""

    def test_memory_context_parameter(self):
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(memory_context="# Persistent Memory\nTest memory content")
        self.assertIn("Persistent Memory", ctx)
        self.assertIn("SESSION CONTEXT", ctx)

    def test_memory_context_empty(self):
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(memory_context="")
        # Empty memory context should not add anything
        self.assertEqual(ctx, "")

    def test_memory_context_with_other_context(self):
        from system_prompts import build_dynamic_context
        ctx = build_dynamic_context(
            workspace_info="Workspace: /test",
            memory_context="# Memory\nSome memories",
        )
        self.assertIn("Memory", ctx)
        self.assertIn("Workspace", ctx)


class TestP92MemoryTriggers(unittest.TestCase):
    """P92: Memory auto-extraction trigger detection."""

    def test_explicit_chinese_remember(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("记住我喜欢用vim")
        self.assertIsNotNone(hint)
        self.assertIn("MEMORY HINT", hint)
        self.assertIn("save", hint)

    def test_explicit_english_remember(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("remember this: I prefer tabs over spaces")
        self.assertIsNotNone(hint)
        self.assertIn("MEMORY HINT", hint)

    def test_explicit_save_preference(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("save my preference for dark mode")
        self.assertIsNotNone(hint)

    def test_feedback_chinese_stop(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("不要再在回答末尾加总结了")
        self.assertIsNotNone(hint)
        self.assertIn("feedback", hint)

    def test_feedback_english_stop(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("stop doing that summary at the end")
        self.assertIsNotNone(hint)
        self.assertIn("feedback", hint)

    def test_feedback_dont(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("don't use mocks in the database tests")
        self.assertIsNotNone(hint)
        self.assertIn("feedback", hint)

    def test_feedback_from_now_on(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("from now on, always use pytest instead of unittest")
        self.assertIsNotNone(hint)

    def test_confirm_exactly(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("yes exactly, that's what I wanted")
        self.assertIsNotNone(hint)

    def test_no_trigger_normal_message(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("请分析这个文件的代码质量")
        self.assertIsNone(hint)

    def test_no_trigger_short_message(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("ok")
        self.assertIsNone(hint)

    def test_no_trigger_empty(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("")
        self.assertIsNone(hint)

    def test_strips_history_delimiter(self):
        from memory.memory_triggers import detect_memory_trigger
        msg = "[对话历史]\nsome old stuff\n[当前问题]\n记住我是数据科学家"
        hint = detect_memory_trigger(msg)
        self.assertIsNotNone(hint)

    def test_keep_doing_that(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("perfect, keep doing that approach")
        self.assertIsNotNone(hint)

    def test_never_use(self):
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger("never use var in JavaScript code")
        self.assertIsNotNone(hint)


class TestP90FormatManifest(unittest.TestCase):
    """P90: Memory manifest formatting."""

    def test_format_memory_manifest(self):
        from memory.memory_manager import format_memory_manifest
        memories = [
            {"filename": "prefs.md", "type": "user", "description": "editor prefs", "modified": "2026-04-22"},
            {"filename": "testing.md", "type": "feedback", "description": "testing rules", "modified": "2026-04-21"},
        ]
        manifest = format_memory_manifest(memories)
        self.assertIn("[user] prefs.md", manifest)
        self.assertIn("[feedback] testing.md", manifest)
        self.assertIn("editor prefs", manifest)

    def test_format_empty_manifest(self):
        from memory.memory_manager import format_memory_manifest
        self.assertEqual(format_memory_manifest([]), "")


# ═══════════════════════════════════════════════════════════════
# U6: Memory Type System Tests
# ═══════════════════════════════════════════════════════════════

class TestU6MemoryTypeSystem(unittest.TestCase):
    """U6: Memory type classification, weights, and protection."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.mem_dir = self.tmpdir / "memory"
        self.mem_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- Type constants --

    def test_u6_type_weights_exist(self):
        """MEMORY_TYPE_WEIGHTS has all 4 types."""
        from memory.memory_manager import MEMORY_TYPE_WEIGHTS, MEMORY_TYPES
        for t in MEMORY_TYPES:
            self.assertIn(t, MEMORY_TYPE_WEIGHTS)

    def test_u6_user_feedback_higher_weight(self):
        """user and feedback types have higher weight than project/reference."""
        from memory.memory_manager import MEMORY_TYPE_WEIGHTS
        self.assertGreater(MEMORY_TYPE_WEIGHTS["user"], MEMORY_TYPE_WEIGHTS["project"])
        self.assertGreater(MEMORY_TYPE_WEIGHTS["feedback"], MEMORY_TYPE_WEIGHTS["project"])
        self.assertGreater(MEMORY_TYPE_WEIGHTS["project"], MEMORY_TYPE_WEIGHTS["reference"])

    def test_u6_protected_types(self):
        """PROTECTED_TYPES contains user and feedback."""
        from memory.memory_manager import PROTECTED_TYPES
        self.assertIn("user", PROTECTED_TYPES)
        self.assertIn("feedback", PROTECTED_TYPES)
        self.assertNotIn("project", PROTECTED_TYPES)

    # -- Auto-classification --

    def test_u6_classify_user_preference(self):
        """Detects user preference content."""
        from memory.memory_manager import classify_memory_type
        self.assertEqual(classify_memory_type("I prefer dark mode"), "user")

    def test_u6_classify_user_chinese(self):
        """Detects user content in Chinese."""
        from memory.memory_manager import classify_memory_type
        self.assertEqual(classify_memory_type("我是前端开发，偏好React"), "user")

    def test_u6_classify_feedback(self):
        """Detects feedback/correction content."""
        from memory.memory_manager import classify_memory_type
        self.assertEqual(classify_memory_type("Don't use tabs, always use spaces"), "feedback")

    def test_u6_classify_feedback_chinese(self):
        """Detects feedback in Chinese."""
        from memory.memory_manager import classify_memory_type
        self.assertEqual(classify_memory_type("不要用emoji"), "feedback")

    def test_u6_classify_project(self):
        """Detects project context."""
        from memory.memory_manager import classify_memory_type
        self.assertEqual(classify_memory_type("Sprint 3 deadline is March 15"), "project")

    def test_u6_classify_reference(self):
        """Detects reference/external pointer."""
        from memory.memory_manager import classify_memory_type
        self.assertEqual(classify_memory_type("API documentation: https://docs.example.com"), "reference")

    def test_u6_classify_ambiguous_defaults_project(self):
        """Ambiguous content defaults to project."""
        from memory.memory_manager import classify_memory_type
        self.assertEqual(classify_memory_type("some random note"), "project")

    def test_u6_classify_uses_topic(self):
        """Topic text also influences classification."""
        from memory.memory_manager import classify_memory_type
        # Topic says "preference", content is ambiguous
        self.assertEqual(classify_memory_type("use 2 spaces", topic="coding preference"), "user")

    # -- Protection policy --

    def test_u6_forget_protected_user_blocked(self):
        """Deleting a 'user' memory is blocked without force."""
        from memory.memory_manager import save_memory, forget_memory
        save_memory(self.mem_dir, "my prefs", "dark mode", mem_type="user")
        result = forget_memory(self.mem_dir, "my prefs")
        self.assertFalse(result["success"])
        self.assertTrue(result.get("protected"))

    def test_u6_forget_protected_feedback_blocked(self):
        """Deleting a 'feedback' memory is blocked without force."""
        from memory.memory_manager import save_memory, forget_memory
        save_memory(self.mem_dir, "no tabs", "always spaces", mem_type="feedback")
        result = forget_memory(self.mem_dir, "no tabs")
        self.assertFalse(result["success"])
        self.assertIn("protected", result.get("error", ""))

    def test_u6_forget_protected_force_allowed(self):
        """Deleting a protected memory succeeds with force=True."""
        from memory.memory_manager import save_memory, forget_memory
        save_memory(self.mem_dir, "my prefs", "dark mode", mem_type="user")
        result = forget_memory(self.mem_dir, "my prefs", force=True)
        self.assertTrue(result["success"])

    def test_u6_forget_project_not_blocked(self):
        """Deleting a 'project' memory is allowed without force."""
        from memory.memory_manager import save_memory, forget_memory
        save_memory(self.mem_dir, "sprint plan", "v2 release", mem_type="project")
        result = forget_memory(self.mem_dir, "sprint plan")
        self.assertTrue(result["success"])

    # -- Type-weighted retrieval --

    def test_u6_weighted_search_user_boosted(self):
        """User-type memories score higher than project-type for same keywords."""
        from memory.memory_manager import save_memory, _search_topic_files
        save_memory(self.mem_dir, "python user", "user uses python daily", mem_type="user",
                    description="python user preference")
        save_memory(self.mem_dir, "python project", "python backend service", mem_type="project",
                    description="python project info")
        results = _search_topic_files(self.mem_dir, "python", max_results=5)
        self.assertGreaterEqual(len(results), 2)
        # User type should rank first due to weight boost
        self.assertEqual(results[0]["type"], "user")


# ═══════════════════════════════════════════════════════════════
# U7: Compact Prompt Structure Tests
# ═══════════════════════════════════════════════════════════════

class TestU7CompactPromptStructure(unittest.TestCase):
    """U7: Structured 9-section compact template."""

    def test_u7_no_tools_preamble_strong(self):
        """NO_TOOLS_PREAMBLE explicitly bans function_call and tool_use."""
        from compact_engine import _NO_TOOLS_PREAMBLE
        self.assertIn("function_call", _NO_TOOLS_PREAMBLE)
        self.assertIn("tool_use", _NO_TOOLS_PREAMBLE)
        self.assertIn("REJECTED", _NO_TOOLS_PREAMBLE)

    def test_u7_sections_has_9(self):
        """_COMPACT_SECTIONS mentions exactly 9 sections."""
        from compact_engine import _COMPACT_SECTIONS
        self.assertIn("9 sections", _COMPACT_SECTIONS)

    def test_u7_sections_all_present(self):
        """All 9 section titles are in the template."""
        from compact_engine import _COMPACT_SECTIONS
        expected = [
            "Primary Request and Intent",
            "Key Technical Concepts",
            "Files and Code Sections",
            "Errors and Fixes",
            "Problem Solving",
            "All User Messages",
            "Pending Tasks",
            "Current Work",
            "Optional Next Step",
        ]
        for section in expected:
            self.assertIn(section, _COMPACT_SECTIONS, f"Missing section: {section}")

    def test_u7_sections_bold_format(self):
        """Section titles use bold markdown format."""
        from compact_engine import _COMPACT_SECTIONS
        self.assertIn("**Primary Request and Intent**", _COMPACT_SECTIONS)
        self.assertIn("**Current Work**", _COMPACT_SECTIONS)

    def test_u7_example_has_9_sections(self):
        """Example output contains all 9 numbered sections."""
        from compact_engine import _COMPACT_EXAMPLE
        for i in range(1, 10):
            self.assertIn(f"{i}.", _COMPACT_EXAMPLE)

    def test_u7_example_has_analysis_tags(self):
        """Example includes <analysis> and <summary> tags."""
        from compact_engine import _COMPACT_EXAMPLE
        self.assertIn("<analysis>", _COMPACT_EXAMPLE)
        self.assertIn("</analysis>", _COMPACT_EXAMPLE)
        self.assertIn("<summary>", _COMPACT_EXAMPLE)
        self.assertIn("</summary>", _COMPACT_EXAMPLE)

    def test_u7_analysis_anti_derivation_rule(self):
        """Analysis instruction bans recording code-derivable info."""
        from compact_engine import _DETAILED_ANALYSIS_INSTRUCTION
        self.assertIn("derivable from the code", _DETAILED_ANALYSIS_INSTRUCTION)

    def test_u7_compact_prompt_has_preamble(self):
        """Full compact prompt starts with NO_TOOLS_PREAMBLE."""
        from compact_engine import _COMPACT_PROMPT, _NO_TOOLS_PREAMBLE
        self.assertTrue(_COMPACT_PROMPT.startswith(_NO_TOOLS_PREAMBLE))

    def test_u7_compact_prompt_has_trailer(self):
        """Full compact prompt ends with NO_TOOLS_TRAILER."""
        from compact_engine import _COMPACT_PROMPT, _NO_TOOLS_TRAILER
        self.assertTrue(_COMPACT_PROMPT.endswith(_NO_TOOLS_TRAILER))

    def test_u7_partial_prompt_mentions_older(self):
        """Partial compact prompt mentions 'OLDER portion'."""
        from compact_engine import _PARTIAL_COMPACT_PROMPT
        self.assertIn("OLDER", _PARTIAL_COMPACT_PROMPT)

    def test_u7_format_compact_summary_strips_analysis(self):
        """_format_compact_summary strips <analysis> block."""
        from compact_engine import _format_compact_summary
        raw = "<analysis>thinking...</analysis>\n<summary>Result here</summary>"
        result = _format_compact_summary(raw)
        self.assertNotIn("thinking", result)
        self.assertIn("Result here", result)

    def test_u7_sections_no_task_completion_as_separate(self):
        """Old section 9 (Task Completion Status) merged into Pending Tasks."""
        from compact_engine import _COMPACT_SECTIONS
        # Should NOT have "Task Completion Status" as a separate section
        self.assertNotIn("Task Completion Status", _COMPACT_SECTIONS)
        # Status info merged into Pending Tasks
        self.assertIn("COMPLETED", _COMPACT_SECTIONS)
        self.assertIn("IN PROGRESS", _COMPACT_SECTIONS)
        self.assertIn("PENDING", _COMPACT_SECTIONS)

    def test_u7_example_absolute_paths(self):
        """Example shows absolute paths for file references."""
        from compact_engine import _COMPACT_EXAMPLE
        self.assertIn("/absolute/path", _COMPACT_EXAMPLE)


# ═══════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []

    test_classes = [
        TestP90MemoryManager,
        TestP90Frontmatter,
        TestP90Index,
        TestP90Truncation,
        TestP90Slugify,
        TestP91MemoryPrompts,
        TestP90Search,
        TestP92MemoryTool,
        TestP91DynamicContextIntegration,
        TestP92MemoryTriggers,
        TestP90FormatManifest,
        TestU6MemoryTypeSystem,
        TestU7CompactPromptStructure,
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
        print("🎉 All memory system tests passed!")

    sys.exit(0 if failed == 0 else 1)
