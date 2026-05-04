"""Tests for P60 (Agent Type System), P61 (Verification Agent),
P62 (TodoWriteTool), and P71 (Git Safety Protocol).
"""
import sys
import os
import re
import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure web_ui is on the path
WEB_UI = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_UI))


# ═══════════════════════════════════════════════════════════════
# P62: TodoManage Tool Tests
# ═══════════════════════════════════════════════════════════════

class TestTodoManageTool(unittest.TestCase):
    """Test todo_manage tool execute()."""

    def setUp(self):
        from tools.todo_manage import set_session_id, _SESSION_TODOS
        _SESSION_TODOS.clear()
        set_session_id("test_session")

    def _exec(self, args):
        from tools.todo_manage import execute
        return execute(args, WEB_UI)

    def test_basic_create(self):
        result = self._exec({
            "todos": [
                {"id": "1", "content": "Read file", "status": "completed"},
                {"id": "2", "content": "Edit code", "status": "in_progress"},
                {"id": "3", "content": "Run tests", "status": "pending"},
            ]
        })
        self.assertTrue(result["success"])
        self.assertIn("1/3 completed", result["output"])
        self.assertIn("✅", result["output"])
        self.assertIn("🔄", result["output"])
        self.assertIn("⬚", result["output"])
        self.assertIsInstance(result["_todo_update"], list)
        self.assertEqual(len(result["_todo_update"]), 3)

    def test_empty_todos_rejected(self):
        result = self._exec({"todos": []})
        self.assertFalse(result["success"])
        self.assertIn("empty", result["error"])

    def test_too_many_todos(self):
        todos = [{"id": str(i), "content": f"Task {i}", "status": "pending"} for i in range(25)]
        result = self._exec({"todos": todos})
        self.assertFalse(result["success"])
        self.assertIn("max 20", result["error"])

    def test_duplicate_id_rejected(self):
        result = self._exec({
            "todos": [
                {"id": "1", "content": "Task A", "status": "pending"},
                {"id": "1", "content": "Task B", "status": "pending"},
            ]
        })
        self.assertFalse(result["success"])
        self.assertIn("Duplicate", result["error"])

    def test_empty_content_rejected(self):
        result = self._exec({
            "todos": [
                {"id": "1", "content": "", "status": "pending"},
            ]
        })
        self.assertFalse(result["success"])
        self.assertIn("empty content", result["error"])

    def test_invalid_status_normalized(self):
        result = self._exec({
            "todos": [
                {"id": "1", "content": "Task", "status": "unknown_status"},
            ]
        })
        self.assertTrue(result["success"])
        # Invalid status is normalized to "pending"
        self.assertEqual(result["_todo_update"][0]["status"], "pending")

    def test_multiple_in_progress_warning(self):
        result = self._exec({
            "todos": [
                {"id": "1", "content": "Task A", "status": "in_progress"},
                {"id": "2", "content": "Task B", "status": "in_progress"},
            ]
        })
        self.assertTrue(result["success"])
        self.assertIn("Warning", result["output"])

    def test_get_todos(self):
        from tools.todo_manage import get_todos
        self._exec({
            "todos": [
                {"id": "1", "content": "Read file", "status": "completed"},
                {"id": "2", "content": "Edit code", "status": "in_progress"},
            ]
        })
        todos = get_todos("test_session")
        self.assertEqual(len(todos), 2)
        self.assertEqual(todos[0]["status"], "completed")
        self.assertEqual(todos[1]["status"], "in_progress")

    def test_replaces_previous_state(self):
        """Sending a new todo list replaces the old one entirely."""
        self._exec({
            "todos": [
                {"id": "1", "content": "Old task", "status": "pending"},
            ]
        })
        self._exec({
            "todos": [
                {"id": "a", "content": "New task A", "status": "completed"},
                {"id": "b", "content": "New task B", "status": "pending"},
            ]
        })
        from tools.todo_manage import get_todos
        todos = get_todos("test_session")
        self.assertEqual(len(todos), 2)
        self.assertEqual(todos[0]["id"], "a")

    def test_all_completed(self):
        result = self._exec({
            "todos": [
                {"id": "1", "content": "Done A", "status": "completed"},
                {"id": "2", "content": "Done B", "status": "completed"},
            ]
        })
        self.assertTrue(result["success"])
        self.assertIn("2/2 completed", result["output"])


# ═══════════════════════════════════════════════════════════════
# P62: Tool Registration Tests
# ═══════════════════════════════════════════════════════════════

class TestTodoRegistration(unittest.TestCase):
    """Test that todo_manage is properly registered in the tool system."""

    def test_in_agentic_tools(self):
        from tools import AGENTIC_TOOLS
        names = [t["function"]["name"] for t in AGENTIC_TOOLS]
        self.assertIn("todo_manage", names)

    def test_in_tool_aliases(self):
        from tools import TOOL_NAME_ALIASES
        self.assertIn("todo", TOOL_NAME_ALIASES)
        self.assertEqual(TOOL_NAME_ALIASES["todo"], "todo_manage")
        self.assertIn("task_list", TOOL_NAME_ALIASES)
        self.assertIn("plan", TOOL_NAME_ALIASES)

    def test_is_readonly(self):
        from tools import READONLY_TOOLS
        self.assertIn("todo_manage", READONLY_TOOLS)

    def test_execute_tool_dispatch(self):
        from tools import execute_tool
        result = execute_tool("todo_manage", {
            "todos": [{"id": "1", "content": "Test task", "status": "pending"}]
        }, WEB_UI)
        self.assertTrue(result["success"])

    def test_alias_dispatch(self):
        from tools import execute_tool
        result = execute_tool("todo", {
            "todos": [{"id": "1", "content": "Test task", "status": "pending"}]
        }, WEB_UI)
        self.assertTrue(result["success"])


# ═══════════════════════════════════════════════════════════════
# P1: Task Store / Control Plane Phase 1 Tests
# ═══════════════════════════════════════════════════════════════

class TestTaskStorePhase1(unittest.TestCase):
    """Test the new session-scoped Task model/store."""

    def test_root_task_created_and_summarized(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            from task_store import ensure_root_task_for_message, get_current_task_summary, get_task_store

            task = ensure_root_task_for_message(
                workspace,
                "phase1-session",
                "Implement the task-centric control plane for Nanobot",
            )

            self.assertTrue(task.id.startswith("root-"))
            self.assertEqual(task.state, "created")
            self.assertIn("task-centric control plane", task.title.lower())

            store = get_task_store("phase1-session", workspace)
            loaded = store.get_latest_root_task()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.id, task.id)

            summary = get_current_task_summary("phase1-session", workspace)
            self.assertIn("[TASK CONTEXT]", summary)
            self.assertIn(task.id, summary)
            self.assertIn("State: created", summary)

    def test_task_store_transition_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            from task_store import get_task_store, ensure_root_task_for_message

            store = get_task_store("phase1-transition", workspace)
            root = ensure_root_task_for_message(workspace, "phase1-transition", "Build the phase 1 task store")
            self.assertEqual(root.state, "created")

            child = store.create_task(
                id="child-1",
                title="Plan schema",
                objective="Plan the task schema and store",
                state="created",
                parent_id=root.id,
            )

            with self.assertRaises(ValueError):
                store.transition(child.id, "completed")

            transitioned = store.transition(child.id, "planned")
            self.assertEqual(transitioned.state, "planned")
            transitioned = store.transition(child.id, "in_progress")
            self.assertEqual(transitioned.state, "in_progress")

    def test_task_store_done_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            from task_store import ensure_root_task_for_message, get_task_store

            root = ensure_root_task_for_message(workspace, "phase1-summary", "Track a multi-step plan")
            store = get_task_store("phase1-summary", workspace)
            store.create_task(id="step-1", title="Read code", state="completed", parent_id=root.id)
            store.create_task(id="step-2", title="Write code", state="in_progress", parent_id=root.id)

            done_summary = store.build_done_summary()
            self.assertTrue(done_summary["found"])
            self.assertEqual(done_summary["root_task"]["id"], root.id)
            self.assertEqual(len(done_summary["tasks"]), 2)
            self.assertEqual(len(done_summary["todos"]), 2)
            self.assertEqual(done_summary["todos"][1]["state"], "in_progress")


class TestTaskManagePhase1Integration(unittest.TestCase):
    """Test todo_manage integration with the TaskStore control plane."""

    def setUp(self):
        from tools.todo_manage import set_session_id, _SESSION_TODOS
        _SESSION_TODOS.clear()
        set_session_id("phase1-todo-session")

    def test_todo_manage_writes_task_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            from tools.todo_manage import execute, get_todos

            result = execute(
                {
                    "todos": [
                        {"id": "1", "content": "Create task model", "status": "completed"},
                        {"id": "2", "content": "Persist task store", "status": "in_progress"},
                        {"id": "3", "content": "Expose task context", "status": "pending"},
                    ]
                },
                workspace,
            )

            self.assertTrue(result["success"])
            self.assertIn("Todo list updated", result["output"])
            self.assertIn("_task_update", result)
            self.assertIn("root_task", result["_task_update"])
            self.assertEqual(result["_task_update"]["root_task"]["state"], "in_progress")
            self.assertEqual(len(result["_todo_update"]), 3)

            todos = get_todos("phase1-todo-session")
            self.assertEqual(len(todos), 3)
            self.assertEqual(todos[1]["status"], "in_progress")
            self.assertEqual(todos[1]["id"], "2")



# ═══════════════════════════════════════════════════════════════
# P60: Agent Type System Tests
# ═══════════════════════════════════════════════════════════════

class TestAgentTypeSystem(unittest.TestCase):
    """Test P60 built-in agent types in sub_agent."""

    def test_built_in_agents_defined(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        self.assertIn("explore", BUILT_IN_AGENTS)
        self.assertIn("verify", BUILT_IN_AGENTS)
        self.assertIn("plan", BUILT_IN_AGENTS)

    def test_explore_agent_config(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        explore = BUILT_IN_AGENTS["explore"]
        self.assertIn("system_prompt", explore)
        self.assertIn("allowed_tools", explore)
        # explore is read-only: should not have file_write, file_edit
        self.assertNotIn("file_write", explore["allowed_tools"])
        self.assertNotIn("file_edit", explore["allowed_tools"])
        self.assertNotIn("sub_agent", explore["allowed_tools"])
        # Should have read tools
        self.assertIn("file_read", explore["allowed_tools"])
        self.assertIn("grep_search", explore["allowed_tools"])
        self.assertIn("find_by_name", explore["allowed_tools"])

    def test_verify_agent_config(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        verify = BUILT_IN_AGENTS["verify"]
        # verify is read-only + shell: should not have file_write, file_edit
        self.assertNotIn("file_write", verify["allowed_tools"])
        self.assertNotIn("file_edit", verify["allowed_tools"])
        self.assertNotIn("sub_agent", verify["allowed_tools"])
        # Should have shell + python for running tests
        self.assertIn("shell_execute", verify["allowed_tools"])
        self.assertIn("python_execute", verify["allowed_tools"])

    def test_plan_agent_config(self):
        from tools.sub_agent import BUILT_IN_AGENTS
        plan = BUILT_IN_AGENTS["plan"]
        # plan is strictly read-only: no shell, no write, no edit
        self.assertNotIn("file_write", plan["allowed_tools"])
        self.assertNotIn("file_edit", plan["allowed_tools"])
        self.assertNotIn("shell_execute", plan["allowed_tools"])
        self.assertNotIn("python_execute", plan["allowed_tools"])
        self.assertNotIn("sub_agent", plan["allowed_tools"])
        # Should have read tools
        self.assertIn("file_read", plan["allowed_tools"])
        self.assertIn("grep_search", plan["allowed_tools"])

    def test_tool_def_has_agent_type_param(self):
        from tools.sub_agent import TOOL_DEF
        props = TOOL_DEF["function"]["parameters"]["properties"]
        self.assertIn("agent_type", props)
        self.assertEqual(props["agent_type"]["type"], "string")
        self.assertIn("enum", props["agent_type"])
        self.assertEqual(sorted(props["agent_type"]["enum"]),
                         ["edit", "explore", "plan", "research", "verify"])

    def test_invalid_agent_type_rejected(self):
        """Unknown agent_type should return an error synchronously."""
        loop = asyncio.new_event_loop()
        try:
            from tools.sub_agent import execute_async, set_parent_context
            set_parent_context({"OLLAMA_BASE_URL": "http://localhost:11434"}, WEB_UI, "test")
            result = loop.run_until_complete(execute_async(
                {"task": "This is a test task for invalid agent type", "agent_type": "nonexistent"},
                WEB_UI,
            ))
            self.assertFalse(result["success"])
            self.assertIn("Unknown agent_type", result["error"])
            self.assertIn("explore", result["error"])  # should list valid types
        finally:
            loop.close()


# ═══════════════════════════════════════════════════════════════
# P61: Verification Agent Tests
# ═══════════════════════════════════════════════════════════════

class TestVerificationAgent(unittest.TestCase):
    """Test P61 verify agent system prompt content."""

    def test_verify_prompt_has_key_sections(self):
        from tools.sub_agent import _VERIFY_SYSTEM_PROMPT
        # Must contain adversarial mindset
        self.assertIn("try to BREAK it", _VERIFY_SYSTEM_PROMPT)
        # Must contain rationalizations section
        self.assertIn("RECOGNIZE YOUR OWN RATIONALIZATIONS", _VERIFY_SYSTEM_PROMPT)
        self.assertIn("reading is not verification", _VERIFY_SYSTEM_PROMPT)
        # Must contain output format
        self.assertIn("VERDICT: PASS", _VERIFY_SYSTEM_PROMPT)
        self.assertIn("VERDICT: FAIL", _VERIFY_SYSTEM_PROMPT)
        self.assertIn("VERDICT: PARTIAL", _VERIFY_SYSTEM_PROMPT)
        # Must prohibit project modification
        self.assertIn("DO NOT MODIFY THE PROJECT", _VERIFY_SYSTEM_PROMPT)

    def test_explore_prompt_is_readonly(self):
        from tools.sub_agent import _EXPLORE_SYSTEM_PROMPT
        self.assertIn("READ-ONLY", _EXPLORE_SYSTEM_PROMPT)
        self.assertIn("FORBIDDEN", _EXPLORE_SYSTEM_PROMPT)  # U11b: section renamed

    def test_plan_prompt_is_readonly(self):
        from tools.sub_agent import _PLAN_SYSTEM_PROMPT
        self.assertIn("READ-ONLY", _PLAN_SYSTEM_PROMPT)
        self.assertIn("Execution Plan", _PLAN_SYSTEM_PROMPT)


# ═══════════════════════════════════════════════════════════════
# P71: Git Safety Protocol Tests
# ═══════════════════════════════════════════════════════════════

class TestGitSafetyProtocol(unittest.TestCase):
    """Test P71 git safety rules in system prompt."""

    def test_git_safety_in_prompt(self):
        from system_prompts import _get_static_system_prompt
        prompt = _get_static_system_prompt()
        # Must have Git safety section
        self.assertIn("Git safety protocol", prompt)
        # Must prohibit destructive git commands
        self.assertIn("push --force", prompt)
        self.assertIn("reset --hard", prompt)
        # Must require explicit user approval
        self.assertIn("EXPLICITLY", prompt)
        # Must prohibit --no-verify
        self.assertIn("--no-verify", prompt)
        # Must advise creating NEW commits
        self.assertIn("NEW commits", prompt)
        # Must prohibit -i flag
        self.assertIn("-i flag", prompt)
        # Must advise against git add -A
        self.assertIn("git add -A", prompt)

    def test_git_safety_in_actions_section(self):
        from system_prompts import _SYSTEM_PROMPT_ACTIONS
        self.assertIn("Git safety protocol", _SYSTEM_PROMPT_ACTIONS)
        self.assertIn("NEVER update git config", _SYSTEM_PROMPT_ACTIONS)
        self.assertIn("NEVER commit unless the user explicitly asks", _SYSTEM_PROMPT_ACTIONS)


# ═══════════════════════════════════════════════════════════════
# P60: agentic_chat_stream Signature Tests
# ═══════════════════════════════════════════════════════════════

class TestAgenticLoopSignature(unittest.TestCase):
    """Test that agentic_chat_stream accepts new P60 parameters."""

    def test_signature_has_agent_params(self):
        import inspect
        from agentic_loop import agentic_chat_stream
        sig = inspect.signature(agentic_chat_stream)
        params = list(sig.parameters.keys())
        self.assertIn("agent_system_prompt", params)
        self.assertIn("allowed_tools", params)

    def test_default_values_are_none(self):
        import inspect
        from agentic_loop import agentic_chat_stream
        sig = inspect.signature(agentic_chat_stream)
        self.assertIs(sig.parameters["agent_system_prompt"].default, None)
        self.assertIs(sig.parameters["allowed_tools"].default, None)


# ═══════════════════════════════════════════════════════════════
# P62: System Prompt Self-Knowledge Tests
# ═══════════════════════════════════════════════════════════════

class TestSelfKnowledgeUpdate(unittest.TestCase):
    """Test that system prompt reflects new tools and features."""

    def test_12_tools_mentioned(self):
        from system_prompts import _SYSTEM_PROMPT_SELF_KNOWLEDGE
        self.assertIn("14 tools", _SYSTEM_PROMPT_SELF_KNOWLEDGE)

    def test_todo_manage_in_self_knowledge(self):
        from system_prompts import _SYSTEM_PROMPT_SELF_KNOWLEDGE
        self.assertIn("todo_manage", _SYSTEM_PROMPT_SELF_KNOWLEDGE)

    def test_agent_types_in_self_knowledge(self):
        from system_prompts import _SYSTEM_PROMPT_SELF_KNOWLEDGE
        self.assertIn("agent_type='explore'", _SYSTEM_PROMPT_SELF_KNOWLEDGE)
        self.assertIn("agent_type='verify'", _SYSTEM_PROMPT_SELF_KNOWLEDGE)
        self.assertIn("agent_type='plan'", _SYSTEM_PROMPT_SELF_KNOWLEDGE)

    def test_todo_manage_in_mc_config(self):
        from agentic_loop import MICRO_COMPACT_CONFIG
        self.assertIn("todo_manage", MICRO_COMPACT_CONFIG)
        self.assertEqual(MICRO_COMPACT_CONFIG["todo_manage"]["max"], 2000)


# ═══════════════════════════════════════════════════════════════
# P62: Tool Guidance Tests
# ═══════════════════════════════════════════════════════════════

class TestToolGuidance(unittest.TestCase):
    """Test that build_tool_guidance includes todo_manage."""

    def test_todo_manage_in_guidance(self):
        from tools import build_tool_guidance
        guidance = build_tool_guidance()
        self.assertIn("todo_manage", guidance)


if __name__ == "__main__":
    unittest.main()
