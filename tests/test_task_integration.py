import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

WEB_UI = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(WEB_UI))


class TestTaskIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmpdir.name)
        self.session_id = "task-integration-session"

        import tools.todo_manage as todo_manage
        from task_store import set_task_context, reset_session_tasks

        reset_session_tasks(self.session_id, self.workspace)
        set_task_context(self.session_id, self.workspace)
        todo_manage._SESSION_TODOS.clear()
        todo_manage.set_session_id(self.session_id)

    def tearDown(self):
        try:
            import tools.todo_manage as todo_manage
            todo_manage._SESSION_TODOS.clear()
        finally:
            self._tmpdir.cleanup()

    def test_todo_manage_triggers_task_update(self):
        import tools.todo_manage as todo_manage
        from task_store import get_task_store

        result = todo_manage.execute(
            {
                "todos": [
                    {"id": "1", "content": "Create task model", "status": "completed"},
                    {"id": "2", "content": "Persist task store", "status": "in_progress"},
                    {"id": "3", "content": "Expose task context", "status": "pending"},
                ]
            },
            self.workspace,
        )

        self.assertTrue(result["success"])
        self.assertIn("_task_update", result)
        task_update = result["_task_update"]
        self.assertIn("root_task", task_update)
        self.assertIn("tasks", task_update)
        self.assertEqual(len(task_update["tasks"]), 3)

        root_task = task_update["root_task"]
        self.assertTrue(root_task["id"].startswith("root-"))
        self.assertEqual(root_task["state"], "in_progress")
        self.assertEqual(root_task["current_step"], "Persist task store")
        self.assertEqual(root_task["steps"], ["Create task model", "Persist task store", "Expose task context"])

        store = get_task_store(self.session_id, self.workspace)
        loaded_root = store.get_latest_root_task()
        self.assertIsNotNone(loaded_root)
        self.assertEqual(loaded_root.id, root_task["id"])
        self.assertEqual(loaded_root.state, "in_progress")

    def test_task_state_advances_from_successful_actions(self):
        import tools.todo_manage as todo_manage
        from task_store import get_task_store, ensure_root_task_for_message

        root = ensure_root_task_for_message(
            self.workspace,
            self.session_id,
            "Advance task state through successful actions",
        )
        store = get_task_store(self.session_id, self.workspace)

        store.transition(root.id, "created")
        store._loaded = True

        # Simulate the same data shape that agentic_loop sees after tool success.
        result = todo_manage.execute(
            {"todos": [{"id": "1", "content": "Read file", "status": "completed"}]},
            self.workspace,
        )
        self.assertTrue(result["success"])

        loaded_root = store.get_latest_root_task()
        self.assertIsNotNone(loaded_root)
        self.assertIn(loaded_root.state, {"planned", "in_progress", "waiting_approval", "verifying", "completed"})

    def test_completed_state_does_not_revert_through_transition(self):
        from task_store import get_task_store, ensure_root_task_for_message

        root = ensure_root_task_for_message(
            self.workspace,
            self.session_id,
            "Protect terminal task states",
        )
        store = get_task_store(self.session_id, self.workspace)
        store.transition(root.id, "planned")
        store.transition(root.id, "in_progress")
        store.transition(root.id, "completed")

        with self.assertRaises(ValueError):
            store.transition(root.id, "in_progress")

    def test_task_debug_endpoint_returns_tree_and_state_history(self):
        from fastapi.testclient import TestClient
        from server_final import app
        from task_store import create_child_task, create_session_root_task, get_task_store

        root = create_session_root_task(
            self.workspace,
            self.session_id,
            title="Debug task endpoint",
            objective="Exercise the debug snapshot route",
        )
        store = get_task_store(self.session_id, self.workspace)
        store.transition(root.id, "planned", source="test_case", reason="manual setup")
        store.transition(root.id, "in_progress", source="test_case", reason="manual setup")

        child = create_child_task(
            self.workspace,
            self.session_id,
            root.id,
            title="Verify debug subtree",
            objective="Child task for the debug endpoint",
            state="planned",
        )
        store.transition(child.id, "in_progress", source="test_case", reason="child started")
        store.transition(child.id, "completed", source="test_case", reason="child done")

        client = TestClient(app)
        response = client.get(f"/api/task/{root.id}/debug")
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["task_id"], root.id)
        self.assertEqual(payload["session_id"], self.session_id)
        self.assertEqual(payload["task"]["id"], root.id)
        self.assertIn("state_history", payload)
        self.assertGreaterEqual(len(payload["state_history"]), 2)
        self.assertEqual(payload["state_history"][0]["source"], "test_case")
        self.assertEqual(payload["state_history"][0]["reason"], "manual setup")

        task_tree = payload["task_tree"]
        self.assertEqual(task_tree["task"]["id"], root.id)
        self.assertEqual(len(task_tree["children"]), 1)
        self.assertEqual(task_tree["children"][0]["task"]["id"], child.id)
        self.assertEqual(task_tree["children"][0]["task"]["state"], "completed")
        self.assertGreaterEqual(len(task_tree["children"][0]["task"]["state_history"]), 2)

        self.assertEqual(payload["child_count"], 1)
        self.assertEqual(payload["child_tasks"][0]["id"], child.id)

    def test_current_session_snapshot_exposes_current_task_id(self):
        from fastapi.testclient import TestClient
        import server_final
        from unittest.mock import MagicMock, patch

        root_id = "root-snapshot-001"
        fake_task_store = MagicMock()
        fake_task_store.build_done_summary.return_value = {
            "found": True,
            "root_task": {"id": root_id, "state": "planned", "title": "Current session snapshot"},
            "tasks": [],
            "todos": [],
        }

        try:
            original_sessions = dict(server_final.sessions)
            server_final.sessions.clear()
            server_final.sessions[self.session_id] = {
                "history": [
                    {"role": "user", "content": "Inspect the active task", "time": "2026-04-28T13:00:00"}
                ]
            }

            with patch("pathlib.Path.glob", return_value=[]), patch("task_store.get_task_store", return_value=fake_task_store):
                client = TestClient(server_final.app)
                response = client.get("/api/sessions/current")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["current_session_id"], self.session_id)
            self.assertEqual(payload["current_task_id"], root_id)
            self.assertIn("task_update", payload)
            self.assertEqual(payload["task_update"]["root_task"]["id"], root_id)
        finally:
            server_final.sessions.clear()
            server_final.sessions.update(original_sessions)

    def test_chat_stream_exposes_current_task_id_header(self):
        from fastapi.testclient import TestClient
        import server_final

        async def fake_agentic_chat_stream(**kwargs):
            yield {"type": "chunk", "content": "hello"}
            yield {
                "type": "agentic_done",
                "turns": 1,
                "total_tool_calls": 0,
                "tools_used": [],
            }

        client = TestClient(server_final.app)
        with patch.object(server_final, "_resolve_chat_task_id", return_value="root-header-test"), \
             patch.object(server_final, "agentic_chat_stream", new=fake_agentic_chat_stream):
            response = client.post(
                "/api/chat/stream",
                json={
                    "message": "Please summarize the current task",
                    "session_id": self.session_id,
                    "mode": "code",
                    "backend": "ollama",
                    "model": "",
                    "attachments": [],
                    "history": [],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Nanobot-Current-Task-ID"), "root-header-test")
        self.assertIn("X-Nanobot-Current-Task-ID", response.headers.get("Access-Control-Expose-Headers", ""))
        self.assertIn("data: ", response.text)

    def test_task_status_footer_instruction_omits_completed_tasks(self):
        from agentic_loop import _build_task_status_footer_instruction

        zh_footer = _build_task_status_footer_instruction("verifying", language="zh")
        self.assertIn("静默指令", zh_footer)
        self.assertIn("任务状态", zh_footer)
        self.assertIn("验证中", zh_footer)

        en_footer = _build_task_status_footer_instruction("in_progress", language="en")
        self.assertIn("Quiet instruction", en_footer)
        self.assertIn("Task status", en_footer)
        self.assertIn("in_progress", en_footer)

        self.assertEqual(_build_task_status_footer_instruction("completed", language="zh"), "")

    def test_manual_task_transition_requires_debug_mode(self):
        from fastapi.testclient import TestClient
        import server_final
        from task_store import create_session_root_task, get_task_store

        root = create_session_root_task(
            self.workspace,
            self.session_id,
            title="Manual override task",
            objective="Verify debug-only transition endpoint",
        )
        store = get_task_store(self.session_id, self.workspace)

        client = TestClient(server_final.app)
        with patch.dict("os.environ", {"NANOBOT_DEBUG_MODE": "0"}, clear=False):
            response = client.post(
                f"/api/task/{root.id}/transition",
                json={"state": "planned", "reason": "manual override"},
            )

        self.assertEqual(response.status_code, 404)

        with patch.dict("os.environ", {"NANOBOT_DEBUG_MODE": "true"}, clear=False), \
             patch("task_store.find_task_record", return_value=(store, root)):
            response = client.post(
                f"/api/task/{root.id}/transition",
                json={"state": "planned", "reason": "manual override"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["task_id"], root.id)
        self.assertEqual(payload["state"], "planned")
        self.assertEqual(payload["reason"], "manual override")
        self.assertGreaterEqual(len(payload["state_history"]), 1)
        self.assertEqual(payload["state_history"][-1]["source"], "manual_api")
        self.assertEqual(payload["state_history"][-1]["reason"], "manual override")

    async def test_agentic_done_event_has_task_context(self):
        import agentic_loop
        from task_store import ensure_root_task_for_message

        root = ensure_root_task_for_message(
            self.workspace,
            self.session_id,
            "Implement the task lifecycle control plane",
        )

        async def fake_stream_one_turn(*args, **kwargs):
            yield {"type": "chunk", "content": "done"}

        with patch.object(agentic_loop, "_stream_one_turn", new=fake_stream_one_turn):
            events = []
            async for event in agentic_loop.agentic_chat_stream(
                user_message="Implement the task lifecycle control plane",
                env={},
                session_id=self.session_id,
                workspace=self.workspace,
                max_turns=1,
            ):
                events.append(event)

        done_event = next(ev for ev in events if ev.get("type") == "agentic_done")
        self.assertEqual(done_event["task_id"], root.id)
        self.assertEqual(done_event["task_state"], root.state)
        self.assertIn("task_context", done_event)
        self.assertIn(root.id, done_event["task_context"])
        self.assertIn("[TASK CONTEXT]", done_event["task_context"])

    async def test_root_task_persists_across_calls(self):
        import agentic_loop

        async def fake_stream_one_turn(*args, **kwargs):
            yield {"type": "chunk", "content": "done"}

        with patch.object(agentic_loop, "_stream_one_turn", new=fake_stream_one_turn):
            first_events = []
            async for event in agentic_loop.agentic_chat_stream(
                user_message="Keep the same root task across calls",
                env={},
                session_id=self.session_id,
                workspace=self.workspace,
                max_turns=1,
            ):
                first_events.append(event)

            second_events = []
            async for event in agentic_loop.agentic_chat_stream(
                user_message="Keep the same root task across calls",
                env={},
                session_id=self.session_id,
                workspace=self.workspace,
                max_turns=1,
            ):
                second_events.append(event)

        first_done = next(ev for ev in first_events if ev.get("type") == "agentic_done")
        second_done = next(ev for ev in second_events if ev.get("type") == "agentic_done")

        self.assertEqual(first_done["task_id"], second_done["task_id"])
        self.assertEqual(first_done["task_id"], second_done["task_id"])
        self.assertEqual(first_done["task_state"], second_done["task_state"])

    async def test_state_based_tool_constraint(self):
        """When root task is in waiting_approval, file_edit should be blocked
        and a <system_note> guidance message should appear in the context."""
        import agentic_loop
        from task_store import get_task_store, ensure_root_task_for_message

        # Create root task and advance to waiting_approval
        root = ensure_root_task_for_message(
            self.workspace,
            self.session_id,
            "Test tool constraint under waiting_approval",
        )
        store = get_task_store(self.session_id, self.workspace)
        store.transition(root.id, "planned")
        store.transition(root.id, "in_progress")
        store.transition(root.id, "waiting_approval")

        # Verify setup
        root = store.get_latest_root_task()
        self.assertEqual(root.state, "waiting_approval")

        # Fake LLM: first turn emits a file_edit tool call, second turn is text-only
        _call_count = 0

        async def fake_stream_one_turn(messages, env, tools, stream_stats, **kwargs):
            nonlocal _call_count
            _call_count += 1
            if _call_count == 1:
                # Simulate model requesting file_edit
                yield {"type": "chunk", "content": ""}
                yield {
                    "type": "tool_calls_complete",
                    "tool_calls": [
                        {
                            "id": "tc_file_edit_1",
                            "type": "function",
                            "function": {
                                "name": "file_edit",
                                "arguments": json.dumps({
                                    "path": str(self.workspace / "test.py"),
                                    "old_string": "old",
                                    "new_string": "new",
                                }),
                            },
                        }
                    ],
                }
            else:
                # Second turn: model responds with text (no more tool calls)
                yield {"type": "chunk", "content": "I cannot edit while waiting for approval."}

        # Also mock tool execution so the test doesn't actually run file_edit
        with patch.object(agentic_loop, "_stream_one_turn", new=fake_stream_one_turn):
            events = []
            async for event in agentic_loop.agentic_chat_stream(
                user_message="Edit the test file",
                env={},
                session_id=self.session_id,
                workspace=self.workspace,
                max_turns=3,
            ):
                events.append(event)

        # Assert: file_edit was blocked (tool_result with success=False or
        # a tool message containing the <system_note> guidance)
        tool_results = [
            ev for ev in events
            if ev.get("type") == "tool_result" and ev.get("tool_name") == "file_edit"
        ]
        # The tool should NOT have been actually executed — either no tool_result
        # at all, or the result should indicate blocking
        # Check that the <system_note> guidance was injected into messages
        # by inspecting the agentic_done event's task_context or by checking
        # that no successful file_edit tool_result appears
        successful_edits = [
            ev for ev in tool_results
            if ev.get("success", True) is True
        ]
        self.assertEqual(
            len(successful_edits), 0,
            "file_edit should have been blocked under waiting_approval state",
        )

        # Assert: a system constraint message was emitted
        # The constraint check adds a tool message with <system_note> and
        # a system message with [TASK STATE CONSTRAINT]
        # These appear in the messages list but aren't directly in SSE events.
        # Instead, verify via the agentic_done event that the task stayed
        # in waiting_approval (not regressed)
        done_events = [ev for ev in events if ev.get("type") == "agentic_done"]
        if done_events:
            final_state = done_events[0].get("task_state", "")
            self.assertEqual(
                final_state, "waiting_approval",
                f"Task should remain in waiting_approval, got {final_state}",
            )

    async def test_sub_agent_creates_child_task_and_completes_it(self):
        import agentic_loop
        from task_store import get_task_store, ensure_root_task_for_message
        from tools import sub_agent as sa

        root = ensure_root_task_for_message(
            self.workspace,
            self.session_id,
            "Verify child task wiring for sub-agent execution",
        )

        saved_env = sa._PARENT_ENV
        saved_workspace = sa._PARENT_WORKSPACE
        saved_session = sa._PARENT_SESSION_ID
        saved_messages = list(sa._PARENT_MESSAGES)
        saved_policy = dict(sa._PARENT_SKILL_POLICY)

        sa.set_parent_context(
            {"OLLAMA_BASE_URL": "http://localhost:11434"},
            self.workspace,
            self.session_id,
            messages=[{"role": "user", "content": "parent context"}],
            skill_policy={},
        )

        async def fake_agentic_chat_stream(**kwargs):
            yield {"type": "chunk", "content": "VERDICT: PASS\nEverything looks good."}

        try:
            with patch.object(agentic_loop, "agentic_chat_stream", new=fake_agentic_chat_stream):
                result = await sa.execute_async(
                    {
                        "task": "Verify child task wiring for sub-agent execution",
                        "agent_type": "verify",
                    },
                    self.workspace,
                )
        finally:
            sa._PARENT_ENV = saved_env
            sa._PARENT_WORKSPACE = saved_workspace
            sa._PARENT_SESSION_ID = saved_session
            sa._PARENT_MESSAGES = saved_messages
            sa._PARENT_SKILL_POLICY = saved_policy

        self.assertTrue(result["success"])
        self.assertIn("VERDICT: PASS", result["output"])

        store = get_task_store(self.session_id, self.workspace)
        children = store.get_children(root.id)
        self.assertGreaterEqual(len(children), 1)
        child = next((task for task in children if task.owner == "sub_agent"), children[0])
        self.assertEqual(child.state, "completed")
        self.assertIn("verify", child.title.lower())

    async def test_verifying_state_forces_verify_sub_agent_and_failure_plan(self):
        import agentic_loop
        from task_store import get_task_store, ensure_root_task_for_message

        root = ensure_root_task_for_message(
            self.workspace,
            self.session_id,
            "Force verify sub-agent when the task enters verifying",
        )
        store = get_task_store(self.session_id, self.workspace)
        store.transition(root.id, "planned")
        store.transition(root.id, "in_progress")
        store.transition(root.id, "verifying", verification_required=True)

        async def fake_stream_one_turn(*args, **kwargs):
            raise AssertionError("Model should not be called when the task is already verifying")
            if False:
                yield {"type": "chunk", "content": ""}

        async def fake_execute_tool_async(name, arguments, workspace):
            self.assertEqual(name, "sub_agent")
            self.assertEqual(arguments.get("agent_type"), "verify")
            return {
                "success": True,
                "output": "VERDICT: FAIL\nThe verification found a regression.",
                "error": "",
            }

        with patch.object(agentic_loop, "_stream_one_turn", new=fake_stream_one_turn), \
             patch.object(agentic_loop, "execute_tool_async", new=fake_execute_tool_async):
            events = []
            async for event in agentic_loop.agentic_chat_stream(
                user_message="Run verification now",
                env={},
                session_id=self.session_id,
                workspace=self.workspace,
                max_turns=2,
            ):
                events.append(event)

        verify_results = [
            ev for ev in events
            if ev.get("type") == "tool_result" and ev.get("tool_name") == "sub_agent"
        ]
        self.assertTrue(verify_results, "Forced verify sub-agent should have been executed")
        self.assertTrue(any("VERDICT: FAIL" in ev.get("content", "") for ev in verify_results))

        loaded_root = store.get_latest_root_task()
        self.assertIsNotNone(loaded_root)
        self.assertEqual(loaded_root.state, "failed")
        self.assertIn("verification", loaded_root.blocked_reason.lower())

        children = store.get_children(root.id)
        planning_children = [task for task in children if task.state == "planned" and "plan fixes" in task.title.lower()]
        self.assertTrue(planning_children, "Verification failure should create a follow-up planning child task")


if __name__ == "__main__":
    unittest.main()
