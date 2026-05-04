import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from edit_transaction import build_unified_diff, create_pending_change_set
from server_final import _build_frontend_tool_result_payload, _normalize_task_update_payload


class TestFrontendToolResultContract(unittest.TestCase):
    def make_plan(self, path: Path, before_text: str, after_text: str, tool_name: str = "file_edit") -> dict:
        return {
            "path": str(path),
            "before_text": before_text,
            "after_text": after_text,
            "before_exists": True,
            "after_exists": True,
            "created": False,
            "tool_name": tool_name,
            "summary": f"Change {path.name}",
            "diff": build_unified_diff(before_text, after_text, str(path), before_exists=True, after_exists=True),
        }

    def test_tool_result_payload_includes_change_set_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_store = os.environ.get("NANOBOT_CHANGESET_DIR")
            os.environ["NANOBOT_CHANGESET_DIR"] = str(Path(tmpdir) / "changes")
            try:
                target = Path(tmpdir) / "demo.txt"
                target.write_text("before\n", encoding="utf-8")
                change_set = create_pending_change_set(
                    self.make_plan(target, "before\n", "after\n"),
                    session_id="frontend-contract",
                    source="file_edit",
                )
                payload = _build_frontend_tool_result_payload({
                    "tool_name": "file_edit",
                    "success": True,
                    "content": "x" * 2500,
                    "elapsed": 0.125,
                    "turn": 2,
                    "change_set": change_set,
                })
            finally:
                if previous_store is None:
                    os.environ.pop("NANOBOT_CHANGESET_DIR", None)
                else:
                    os.environ["NANOBOT_CHANGESET_DIR"] = previous_store

        self.assertEqual(payload["type"], "tool_result")
        self.assertEqual(payload["name"], "file_edit")
        self.assertTrue(payload["success"])
        self.assertEqual(payload["elapsed_ms"], 125)
        self.assertEqual(payload["turn"], 2)
        self.assertEqual(len(payload["output"]), 2000)
        self.assertEqual(payload["change_set"]["status"], "pending")
        self.assertEqual(payload["change_set"]["type"], "direct")
        self.assertTrue(payload["change_set"]["id"].startswith("cs_"))
        self.assertIn("diff", payload["change_set"]["files"][0])

    def test_tool_result_payload_preserves_rollback_review_type(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_store = os.environ.get("NANOBOT_CHANGESET_DIR")
            os.environ["NANOBOT_CHANGESET_DIR"] = str(Path(tmpdir) / "changes")
            try:
                target = Path(tmpdir) / "rollback.txt"
                target.write_text("after\n", encoding="utf-8")
                change_set = create_pending_change_set(
                    self.make_plan(target, "after\n", "before\n", tool_name="rollback_review"),
                    session_id="frontend-review",
                    source="rollback_review",
                )
                payload = _build_frontend_tool_result_payload({
                    "tool_name": "file_edit",
                    "success": False,
                    "output": "rollback review pending",
                    "elapsed_ms": 37,
                    "turn": 1,
                    "change_set": change_set,
                })
            finally:
                if previous_store is None:
                    os.environ.pop("NANOBOT_CHANGESET_DIR", None)
                else:
                    os.environ["NANOBOT_CHANGESET_DIR"] = previous_store

        self.assertEqual(payload["change_set"]["type"], "rollback_review")
        self.assertEqual(payload["change_set"]["files"][0]["path"], str(target.resolve()))
        self.assertEqual(payload["elapsed_ms"], 37)
        self.assertFalse(payload["success"])

    def test_app_js_prefers_incoming_terminal_status_and_hides_actions(self):
        app_js = (Path(__file__).resolve().parent.parent / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("status: changeSet.status || state.status || 'pending'", app_js)
        self.assertIn("const isLocked = Boolean(meta.locked);", app_js)
        self.assertIn("disabled: isBusy || isLocked,", app_js)
        self.assertIn("faded: isLocked,", app_js)

    def test_app_js_persists_change_set_cards_and_resyncs_pending_sets(self):
        app_js = (Path(__file__).resolve().parent.parent / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("let host = messageContent.querySelector(':scope > .change-set-host') || messageDiv.querySelector(':scope > .change-set-host');", app_js)
        self.assertIn("messageContent.appendChild(host);", app_js)
        self.assertIn("messageDiv._changeSets = existing;", app_js)
        self.assertIn("rerenderStoredChangeSetCards(messageDiv);", app_js)
        self.assertIn("/api/changes/pending?session_id=", app_js)
        self.assertIn("syncPendingChangeSetsForSession(currentSession || currentChatId, messageDiv);", app_js)

    def test_app_js_rehydrates_message_scoped_change_set_ids_from_history(self):
        app_js = (Path(__file__).resolve().parent.parent / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("pendingChangeSetIds: msg.pending_change_set_ids || []", app_js)
        self.assertIn("const structuredPendingChangeSetIds = displayRole === 'bot' && Array.isArray(meta?.pendingChangeSetIds)", app_js)
        self.assertIn("meta.changeSets.filter((changeSet) => structuredPendingChangeSetIds.includes(changeSet?.id))", app_js)
        self.assertIn("setMessagePendingChangeSetIds(messageDiv, structuredPendingChangeSetIds);", app_js)

    def test_app_js_filters_remote_and_stored_change_sets_by_message_scope(self):
        app_js = (Path(__file__).resolve().parent.parent / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function filterChangeSetsForMessage(messageDiv, changeSets = [])", app_js)
        self.assertIn("const stored = filterChangeSetsForMessage(messageDiv, Array.isArray(messageDiv._changeSets) ? messageDiv._changeSets : []);", app_js)
        self.assertIn("const filteredRemoteChangeSets = filterChangeSetsForMessage(targetMessage, data.change_sets || []);", app_js)
        self.assertIn("const mergedChangeSets = mergeChangeSetsById(targetMessage._changeSets || [], filteredRemoteChangeSets);", app_js)

    def test_app_js_persists_full_scoped_change_set_ids_for_refresh_and_resume(self):
        app_js = (Path(__file__).resolve().parent.parent / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("setMessagePendingChangeSetIds(messageDiv, data.pending_change_set_ids || []);", app_js)
        self.assertIn("lastHistoryMsg.pending_change_set_ids = lastHistoryMsg.change_sets", app_js)
        self.assertIn("pending_change_set_ids: messageDiv._changeSets", app_js)
        self.assertIn("chatHistory[i].pending_change_set_ids = scopedIds;", app_js)
        self.assertIn("assistantEntry.pending_change_set_ids = assistantEntry.change_sets", app_js)

    def test_task_update_normalizer_emits_complete_schema(self):
        payload = _normalize_task_update_payload({
            "session_id": "frontend-contract",
            "root_task": {
                "id": "root-123",
                "title": "Implement task status bar",
                "state": "verifying",
                "current_step": "Waiting for verify verdict",
                "steps": ["plan", "build", "verify"],
                "result_summary": "VERDICT: PASS\nLooks good",
            },
            "tasks": [
                {"id": "child-1", "title": "plan", "state": "completed"},
                {"id": "child-2", "title": "verify", "state": "completed", "metadata": {"agent_type": "verify"}},
            ],
        })

        self.assertEqual(payload["type"], "task_update")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["root_task"]["id"], "root-123")
        self.assertEqual(payload["child_tasks"], payload["tasks"])
        self.assertEqual(payload["progress"]["total"], 2)
        self.assertEqual(payload["progress"]["completed"], 2)
        self.assertEqual(payload["verification"]["verdict"], "PASS")
        self.assertEqual(payload["verification"]["task_id"], "child-2")

    def test_app_js_includes_task_status_bar_and_intervention_states(self):
        app_js = (Path(__file__).resolve().parent.parent / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function renderTaskStatusBar(data = {}, options = {})", app_js)
        self.assertIn("function applyTaskUpdateEvent(data, sessionId = currentSession || currentChatId)", app_js)
        self.assertIn("syncTaskStatusBarForActiveSession(currentSession || currentChatId, { force: true })", app_js)
        self.assertIn("function preloadCurrentSessionSnapshot()", app_js)
        self.assertIn("function cacheCurrentTaskIdFromResponse(response, source = '')", app_js)
        self.assertIn("task-status-bar__tree-root", app_js)
        self.assertIn("task-status-bar__task-link", app_js)
        self.assertIn("function openTaskHistoryPanel(taskId)", app_js)
        self.assertIn("function loadTaskHistoryForTask(taskId)", app_js)
        self.assertIn("taskHistoryModal", app_js)
        self.assertIn("task-status-bar--warning", app_js)
        self.assertIn("task-status-bar__status-pill--waiting_approval", app_js)
        self.assertIn("task-status-bar__status-pill--blocked", app_js)
        self.assertIn("verificationVerdict", app_js)
        self.assertIn("applyTaskUpdateEvent(data, currentSession || currentChatId)", app_js)


if __name__ == "__main__":
    unittest.main()
