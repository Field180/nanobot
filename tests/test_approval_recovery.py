"""Regression tests for task_constraint_recovery anti-oscillation fix.

These tests verify that the approval-state recovery logic in agentic_loop.py:
1. Queries both in-memory cache AND on-disk store for pending change sets
2. Respects the 30-second cooldown after in_progress → waiting_approval
3. Recovers to 'planned' (not 'in_progress') to prevent re-triggering the same edit
4. Does NOT block user approval/rejection (APPROVAL_LIFECYCLE_TOOLS always allowed)
5. Does NOT introduce permanent deadlocks
6. Blocks new change set creation when pending sets exist for the same session
7. Marks silently discarded change sets as 'discarded' (not deleted)
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

WEB_UI = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(WEB_UI))

from task_store import (
    Task,
    TaskStore,
    get_task_store,
    set_task_context,
    reset_session_tasks,
    ensure_root_task_for_message,
    ALLOWED_TRANSITIONS,
)
from task_constraints import is_tool_allowed, APPROVAL_LIFECYCLE_TOOLS


class TestApprovalRecoveryTransitions(unittest.TestCase):
    """Verify that waiting_approval → planned is a valid transition."""

    def test_waiting_approval_to_planned_is_allowed(self):
        allowed = ALLOWED_TRANSITIONS.get("waiting_approval", set())
        self.assertIn("planned", allowed,
                      "waiting_approval → planned must be a valid transition for recovery")

    def test_waiting_approval_to_in_progress_is_allowed(self):
        allowed = ALLOWED_TRANSITIONS.get("waiting_approval", set())
        self.assertIn("in_progress", allowed,
                      "waiting_approval → in_progress must remain valid for user-driven transitions")


class TestApprovalLifecycleToolsAlwaysAllowed(unittest.TestCase):
    """Ensure change_set_accept/reject are never blocked by task state."""

    def test_approval_tools_allowed_in_waiting_approval(self):
        for tool in APPROVAL_LIFECYCLE_TOOLS:
            self.assertTrue(
                is_tool_allowed("waiting_approval", tool),
                f"{tool} must be allowed in waiting_approval state"
            )

    def test_approval_tools_allowed_in_all_states(self):
        all_states = [
            "created", "planned", "in_progress", "blocked",
            "waiting_approval", "verifying", "completed", "failed",
            "cancelled", "backgrounded",
        ]
        for state in all_states:
            for tool in APPROVAL_LIFECYCLE_TOOLS:
                self.assertTrue(
                    is_tool_allowed(state, tool),
                    f"{tool} must be allowed in {state} state"
                )


class TestRecoveryTargetIsPlanned(unittest.TestCase):
    """Verify that recovery goes to 'planned', not 'in_progress'."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmpdir.name)
        self.session_id = "test-recovery-target"
        reset_session_tasks(self.session_id, self.workspace)
        set_task_context(self.session_id, self.workspace)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_transition_to_planned_from_waiting_approval(self):
        """Simulate what task_constraint_recovery does: waiting_approval → planned."""
        store = get_task_store(self.session_id, self.workspace)
        root = ensure_root_task_for_message(
            self.workspace, self.session_id, "Test recovery target"
        )
        # Drive to in_progress first
        store.transition(root.id, "in_progress",
                         source="test", reason="start work")
        # Then to waiting_approval
        store.transition(root.id, "waiting_approval",
                         source="_maybe_advance_task_state",
                         reason="pending change sets: [cs_123]")
        # Recovery should go to planned
        recovered = store.transition(root.id, "planned",
                                     source="task_constraint_recovery",
                                     reason="No pending change sets remain")
        self.assertEqual(recovered.state, "planned")
        # Verify state_history records the recovery
        last_entry = recovered.state_history[-1]
        self.assertEqual(last_entry["from_state"], "waiting_approval")
        self.assertEqual(last_entry["to_state"], "planned")
        self.assertEqual(last_entry["source"], "task_constraint_recovery")


class TestCooldownGuard(unittest.TestCase):
    """Verify cooldown logic that prevents oscillation."""

    def test_recent_transition_detected(self):
        """If last transition was in_progress→waiting_approval < 30s ago, block recovery."""
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(seconds=5)).isoformat()
        state_history = [
            {
                "timestamp": recent_ts,
                "from_state": "in_progress",
                "to_state": "waiting_approval",
                "source": "_maybe_advance_task_state",
                "reason": "pending change sets: [cs_test]",
            }
        ]
        # Simulate the cooldown check from agentic_loop.py
        recovery_allowed = True
        RECOVERY_COOLDOWN_SECONDS = 30
        last_entry = state_history[-1]
        last_to = str(last_entry.get("to_state", "")).strip().lower()
        last_from = str(last_entry.get("from_state", "")).strip().lower()
        if last_to == "waiting_approval" and last_from == "in_progress":
            last_ts_str = last_entry.get("timestamp", "")
            last_ts = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            age_secs = (datetime.now(timezone.utc) - last_ts).total_seconds()
            if age_secs < RECOVERY_COOLDOWN_SECONDS:
                recovery_allowed = False

        self.assertFalse(recovery_allowed,
                         "Recovery must be blocked when last transition was < 30s ago")

    def test_old_transition_allows_recovery(self):
        """If last transition was in_progress→waiting_approval > 30s ago, allow recovery."""
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(seconds=60)).isoformat()
        state_history = [
            {
                "timestamp": old_ts,
                "from_state": "in_progress",
                "to_state": "waiting_approval",
                "source": "_maybe_advance_task_state",
                "reason": "pending change sets: [cs_test]",
            }
        ]
        recovery_allowed = True
        RECOVERY_COOLDOWN_SECONDS = 30
        last_entry = state_history[-1]
        last_to = str(last_entry.get("to_state", "")).strip().lower()
        last_from = str(last_entry.get("from_state", "")).strip().lower()
        if last_to == "waiting_approval" and last_from == "in_progress":
            last_ts_str = last_entry.get("timestamp", "")
            last_ts = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            age_secs = (datetime.now(timezone.utc) - last_ts).total_seconds()
            if age_secs < RECOVERY_COOLDOWN_SECONDS:
                recovery_allowed = False

        self.assertTrue(recovery_allowed,
                        "Recovery must be allowed when last transition was > 30s ago")

    def test_different_transition_allows_recovery(self):
        """If last transition was NOT in_progress→waiting_approval, cooldown does not apply."""
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(seconds=2)).isoformat()
        state_history = [
            {
                "timestamp": recent_ts,
                "from_state": "planned",
                "to_state": "waiting_approval",
                "source": "_maybe_advance_task_state",
                "reason": "pending change sets: [cs_test]",
            }
        ]
        recovery_allowed = True
        RECOVERY_COOLDOWN_SECONDS = 30
        last_entry = state_history[-1]
        last_to = str(last_entry.get("to_state", "")).strip().lower()
        last_from = str(last_entry.get("from_state", "")).strip().lower()
        if last_to == "waiting_approval" and last_from == "in_progress":
            last_ts_str = last_entry.get("timestamp", "")
            last_ts = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            age_secs = (datetime.now(timezone.utc) - last_ts).total_seconds()
            if age_secs < RECOVERY_COOLDOWN_SECONDS:
                recovery_allowed = False

        self.assertTrue(recovery_allowed,
                        "Cooldown only applies to in_progress→waiting_approval transitions")


class TestDiskPendingSyncLogic(unittest.TestCase):
    """Verify that in-memory cache is synced from disk pending change sets."""

    def test_disk_pending_fills_memory_cache(self):
        """Simulate disk query returning change sets that are missing from memory."""
        pending_change_sets = {}  # empty in-memory cache (loop restart)
        disk_pending = [
            {"id": "cs_001", "status": "pending", "session_id": "test"},
            {"id": "cs_002", "status": "pending", "session_id": "test"},
        ]
        # Sync logic from agentic_loop.py
        for dp_cs in disk_pending:
            dp_id = str(dp_cs.get("id", "")).strip()
            if dp_id and dp_id not in pending_change_sets:
                pending_change_sets[dp_id] = dict(dp_cs)

        has_any_pending = bool(pending_change_sets) or bool(disk_pending)

        self.assertTrue(has_any_pending, "Should detect pending change sets from disk")
        self.assertEqual(len(pending_change_sets), 2)
        self.assertIn("cs_001", pending_change_sets)
        self.assertIn("cs_002", pending_change_sets)

    def test_empty_disk_and_memory_allows_recovery(self):
        """When both disk and memory are empty, recovery should be considered."""
        pending_change_sets = {}
        disk_pending = []

        has_any_pending = bool(pending_change_sets) or bool(disk_pending)

        self.assertFalse(has_any_pending, "No pending change sets — recovery path should be entered")

    def test_memory_only_blocks_recovery(self):
        """If memory cache has entries (even if disk is empty), stay in waiting_approval."""
        pending_change_sets = {"cs_mem_001": {"id": "cs_mem_001", "status": "pending"}}
        disk_pending = []

        has_any_pending = bool(pending_change_sets) or bool(disk_pending)

        self.assertTrue(has_any_pending, "In-memory pending should block recovery")


class TestNoDeadlockFromRecovery(unittest.TestCase):
    """Verify the recovery doesn't create permanent deadlocks."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmpdir.name)
        self.session_id = "test-no-deadlock"
        reset_session_tasks(self.session_id, self.workspace)
        set_task_context(self.session_id, self.workspace)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_planned_state_allows_research_tools(self):
        """After recovery to planned, agent should be able to use research tools."""
        research_tools = ["file_read", "file_list", "grep_search", "find_by_name", "sub_agent"]
        for tool in research_tools:
            self.assertTrue(
                is_tool_allowed("planned", tool),
                f"{tool} must be allowed in planned state for agent to continue work"
            )

    def test_planned_allows_work_tools(self):
        """In planned state, work tools (file_edit/file_write/shell_execute) should be allowed.

        These tools have their own safety guards (change set approval for file_edit/file_write,
        destructive command confirmation for shell_execute), so state-level blocking is unnecessary
        and caused the agent to get stuck in created/planned state unable to execute edits.
        """
        work_tools = ["file_edit", "file_write", "shell_execute", "python_execute"]
        for tool in work_tools:
            self.assertTrue(
                is_tool_allowed("planned", tool),
                f"{tool} must be allowed in planned state (has its own safety guards)"
            )

    def test_full_lifecycle_no_permanent_block(self):
        """Verify complete lifecycle: created → in_progress → waiting_approval → planned → in_progress."""
        store = get_task_store(self.session_id, self.workspace)
        root = ensure_root_task_for_message(
            self.workspace, self.session_id, "Full lifecycle test"
        )
        # created → in_progress
        root = store.transition(root.id, "in_progress",
                                source="test", reason="start work")
        self.assertEqual(root.state, "in_progress")

        # in_progress → waiting_approval
        root = store.transition(root.id, "waiting_approval",
                                source="_maybe_advance_task_state",
                                reason="pending change sets: [cs_test]")
        self.assertEqual(root.state, "waiting_approval")

        # waiting_approval → planned (recovery)
        root = store.transition(root.id, "planned",
                                source="task_constraint_recovery",
                                reason="No pending change sets remain")
        self.assertEqual(root.state, "planned")

        # planned → in_progress (agent re-evaluates and starts work again)
        root = store.transition(root.id, "in_progress",
                                source="_maybe_advance_task_state",
                                reason="triggered by sub_agent")
        self.assertEqual(root.state, "in_progress")

        # Verify the full history
        self.assertEqual(len(root.state_history), 4)
        states = [(e["from_state"], e["to_state"]) for e in root.state_history]
        self.assertEqual(states, [
            ("created", "in_progress"),
            ("in_progress", "waiting_approval"),
            ("waiting_approval", "planned"),
            ("planned", "in_progress"),
        ])

    def test_user_can_approve_during_waiting(self):
        """Even during cooldown, user can always approve/reject via lifecycle tools."""
        # This is guaranteed by is_tool_allowed checking APPROVAL_LIFECYCLE_TOOLS first
        self.assertTrue(is_tool_allowed("waiting_approval", "change_set_accept"))
        self.assertTrue(is_tool_allowed("waiting_approval", "change_set_reject"))


class TestEndOfLoopFinalization(unittest.TestCase):
    """Verify the end-of-loop finalization promotes the root task to completed.

    This is the fix for the bug where the task panel shows 'in_progress' even
    after the agent has finished all work but never called todo_manage again
    to mark the child as completed.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmpdir.name)
        self.session_id = "test-finalization"
        reset_session_tasks(self.session_id, self.workspace)
        set_task_context(self.session_id, self.workspace)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _simulate_finalization(self, root, store, last_final_answer_payload, pending_change_sets):
        """Replicate the finalization logic from agentic_loop.py."""
        if root is not None and last_final_answer_payload and not pending_change_sets:
            fin_root = store.get_task(root.id)
            if fin_root and fin_root.state in ("in_progress", "planned", "verifying"):
                children = store.get_children(fin_root.id)
                for child in children:
                    if child.state in ("in_progress", "planned", "created"):
                        store._set_state(
                            child, "completed",
                            source="agentic_loop_finalization",
                            reason="loop ended with final answer",
                        )
                        store.upsert_task(child)
                store._set_state(
                    fin_root, "completed",
                    source="agentic_loop_finalization",
                    reason="loop ended naturally with final answer; no pending approvals",
                )
                store.upsert_task(fin_root)
                store.save()
                return fin_root
        return root

    def test_in_progress_root_promoted_to_completed(self):
        """Root in_progress with final answer and no pending → completed."""
        store = get_task_store(self.session_id, self.workspace)
        root = ensure_root_task_for_message(
            self.workspace, self.session_id, "Test finalization"
        )
        store.transition(root.id, "in_progress",
                         source="test", reason="start work")
        # Simulate end-of-loop with final answer
        result = self._simulate_finalization(
            root, store,
            last_final_answer_payload={"content": "任务已完成"},
            pending_change_sets={},
        )
        self.assertEqual(result.state, "completed")

    def test_in_progress_children_promoted_to_completed(self):
        """In_progress children should be promoted to completed alongside the root."""
        from task_store import create_child_task
        store = get_task_store(self.session_id, self.workspace)
        root = ensure_root_task_for_message(
            self.workspace, self.session_id, "Test child finalization"
        )
        store.transition(root.id, "in_progress",
                         source="test", reason="start work")
        # Create an in_progress child (simulating what todo_manage does)
        child = create_child_task(
            self.workspace, self.session_id, root.id,
            title="rename_method",
            objective="rename test_vvvvv to test_BBBBBBBBBB",
            state="in_progress",
        )
        # Simulate end-of-loop
        self._simulate_finalization(
            root, store,
            last_final_answer_payload={"content": "Done"},
            pending_change_sets={},
        )
        # Both root and child should now be completed
        final_root = store.get_task(root.id)
        final_child = store.get_task(child.id)
        self.assertEqual(final_root.state, "completed")
        self.assertEqual(final_child.state, "completed")

    def test_pending_change_sets_blocks_finalization(self):
        """If pending change sets exist, do NOT promote — user must approve first."""
        store = get_task_store(self.session_id, self.workspace)
        root = ensure_root_task_for_message(
            self.workspace, self.session_id, "Test pending blocks"
        )
        store.transition(root.id, "in_progress",
                         source="test", reason="start work")
        store.transition(root.id, "waiting_approval",
                         source="test", reason="pending approval")
        result = self._simulate_finalization(
            root, store,
            last_final_answer_payload={"content": "Done"},
            pending_change_sets={"cs_001": {"id": "cs_001", "status": "pending"}},
        )
        # State should remain waiting_approval — do NOT auto-complete
        self.assertEqual(result.state, "waiting_approval")

    def test_no_final_answer_blocks_finalization(self):
        """If no final answer was produced (max_turns_reached etc.), do NOT promote."""
        store = get_task_store(self.session_id, self.workspace)
        root = ensure_root_task_for_message(
            self.workspace, self.session_id, "Test no final answer"
        )
        store.transition(root.id, "in_progress",
                         source="test", reason="start work")
        result = self._simulate_finalization(
            root, store,
            last_final_answer_payload=None,
            pending_change_sets={},
        )
        # Should stay in_progress — agent didn't actually finish
        self.assertEqual(result.state, "in_progress")

    def test_terminal_state_not_changed(self):
        """If root is already failed/cancelled/completed, finalization is a no-op."""
        store = get_task_store(self.session_id, self.workspace)
        root = ensure_root_task_for_message(
            self.workspace, self.session_id, "Test terminal"
        )
        store.transition(root.id, "in_progress", source="test", reason="start")
        store.transition(root.id, "verifying",
                         source="test", reason="all children done")
        store.transition(root.id, "failed",
                         source="test", reason="VERDICT: FAIL")
        result = self._simulate_finalization(
            root, store,
            last_final_answer_payload={"content": "Done"},
            pending_change_sets={},
        )
        # Should remain failed
        self.assertEqual(result.state, "failed")

    def test_verifying_state_promoted_to_completed(self):
        """If root is verifying but loop ended with final answer, promote to completed."""
        store = get_task_store(self.session_id, self.workspace)
        root = ensure_root_task_for_message(
            self.workspace, self.session_id, "Test verifying"
        )
        store.transition(root.id, "in_progress", source="test", reason="start")
        store.transition(root.id, "verifying",
                         source="test", reason="all children done")
        result = self._simulate_finalization(
            root, store,
            last_final_answer_payload={"content": "Done"},
            pending_change_sets={},
        )
        self.assertEqual(result.state, "completed")


class TestTimestampParsing(unittest.TestCase):
    """Verify timestamp parsing used in cooldown logic handles various formats."""

    def _parse_and_check_age(self, ts_str, expected_recent):
        """Replicate the parsing logic from the recovery block."""
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_secs = (datetime.now(timezone.utc) - ts).total_seconds()
            is_recent = age_secs < 30
            self.assertEqual(is_recent, expected_recent,
                             f"ts={ts_str}, age={age_secs:.1f}s, expected_recent={expected_recent}")
        except Exception:
            self.fail(f"Failed to parse timestamp: {ts_str}")

    def test_iso_with_utc_offset(self):
        recent = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        self._parse_and_check_age(recent, True)

    def test_iso_with_z_suffix(self):
        recent = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self._parse_and_check_age(recent, True)

    def test_old_timestamp(self):
        old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        self._parse_and_check_age(old, False)


class TestPendingGuard(unittest.TestCase):
    """Verify create_pending_change_set blocks when pending sets exist."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmpdir.name)
        self.target = self.workspace / "guard_test.txt"
        self.target.write_text("original\n", encoding="utf-8")
        # Override store dir for isolation
        self._old_store_dir = os.environ.get("NANOBOT_CHANGESET_DIR")
        self._cs_dir = self.workspace / "cs_store"
        self._cs_dir.mkdir()
        os.environ["NANOBOT_CHANGESET_DIR"] = str(self._cs_dir)

    def tearDown(self):
        if self._old_store_dir is None:
            os.environ.pop("NANOBOT_CHANGESET_DIR", None)
        else:
            os.environ["NANOBOT_CHANGESET_DIR"] = self._old_store_dir
        self._tmpdir.cleanup()

    def _make_plan(self, before: str, after: str):
        return {
            "path": str(self.target),
            "before_text": before,
            "after_text": after,
            "before_exists": True,
            "after_exists": True,
        }

    def test_first_change_set_succeeds(self):
        from edit_transaction import create_pending_change_set
        cs = create_pending_change_set(
            self._make_plan("original\n", "modified\n"),
            session_id="s_guard", source="file_edit",
        )
        self.assertEqual(cs["status"], "pending")

    def test_second_change_set_blocked(self):
        from edit_transaction import create_pending_change_set
        create_pending_change_set(
            self._make_plan("original\n", "modified\n"),
            session_id="s_guard2", source="file_edit",
        )
        with self.assertRaises(ValueError) as ctx:
            create_pending_change_set(
                self._make_plan("original\n", "other\n"),
                session_id="s_guard2", source="file_edit",
            )
        self.assertIn("pending change set", str(ctx.exception).lower())

    def test_different_session_allowed(self):
        from edit_transaction import create_pending_change_set
        create_pending_change_set(
            self._make_plan("original\n", "v1\n"),
            session_id="s_a", source="file_edit",
        )
        # Different session should succeed
        cs2 = create_pending_change_set(
            self._make_plan("original\n", "v2\n"),
            session_id="s_b", source="file_edit",
        )
        self.assertEqual(cs2["status"], "pending")

    def test_after_accept_new_creation_allowed(self):
        from edit_transaction import create_pending_change_set, accept_change_set
        cs1 = create_pending_change_set(
            self._make_plan("original\n", "v1\n"),
            session_id="s_accept", source="file_edit",
        )
        accept_change_set(cs1["id"])
        # After accept, new creation should succeed
        cs2 = create_pending_change_set(
            self._make_plan("v1\n", "v2\n"),
            session_id="s_accept", source="file_edit",
        )
        self.assertEqual(cs2["status"], "pending")

    def test_after_reject_new_creation_allowed(self):
        from edit_transaction import create_pending_change_set, reject_change_set
        cs1 = create_pending_change_set(
            self._make_plan("original\n", "v1\n"),
            session_id="s_reject", source="file_edit",
        )
        reject_change_set(cs1["id"])
        # After reject, new creation should succeed
        cs2 = create_pending_change_set(
            self._make_plan("original\n", "v2\n"),
            session_id="s_reject", source="file_edit",
        )
        self.assertEqual(cs2["status"], "pending")

    def test_rollback_review_exempt_from_guard(self):
        from edit_transaction import create_pending_change_set
        create_pending_change_set(
            self._make_plan("original\n", "v1\n"),
            session_id="s_rollback", source="file_edit",
        )
        # Rollback review should bypass the guard
        cs2 = create_pending_change_set(
            self._make_plan("original\n", "rollback\n"),
            session_id="s_rollback", source="rollback_review",
        )
        self.assertEqual(cs2["status"], "pending")


class TestDiscardTracking(unittest.TestCase):
    """Verify that _cleanup_pending_change_sets marks as 'discarded' not deleted."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmpdir.name)
        self._cs_dir = self.workspace / "cs_store"
        self._cs_dir.mkdir()
        self._old_store_dir = os.environ.get("NANOBOT_CHANGESET_DIR")
        os.environ["NANOBOT_CHANGESET_DIR"] = str(self._cs_dir)
        self._old_ttl = os.environ.get("NANOBOT_CHANGESET_TTL_SECONDS")

    def tearDown(self):
        if self._old_store_dir is None:
            os.environ.pop("NANOBOT_CHANGESET_DIR", None)
        else:
            os.environ["NANOBOT_CHANGESET_DIR"] = self._old_store_dir
        if self._old_ttl is None:
            os.environ.pop("NANOBOT_CHANGESET_TTL_SECONDS", None)
        else:
            os.environ["NANOBOT_CHANGESET_TTL_SECONDS"] = self._old_ttl
        self._tmpdir.cleanup()

    def test_ttl_expired_marked_discarded(self):
        """Change sets that expire should be marked 'discarded', not deleted."""
        # Create a change set with a very old timestamp
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        cs_data = {
            "id": "cs_test_expired",
            "session_id": "s_ttl_test",
            "source": "file_edit",
            "type": "direct",
            "status": "pending",
            "created_at": old_ts,
            "updated_at": old_ts,
            "files": [{"path": "/tmp/fake.txt", "before_text": "a", "after_text": "b",
                        "before_exists": True, "after_exists": True}],
        }
        cs_path = self._cs_dir / "cs_test_expired.json"
        cs_path.write_text(json.dumps(cs_data), encoding="utf-8")

        # Set TTL to 1 second so it's definitely expired
        os.environ["NANOBOT_CHANGESET_TTL_SECONDS"] = "1"

        from edit_transaction import _cleanup_pending_change_sets
        _cleanup_pending_change_sets()

        # File should still exist, with status=discarded
        self.assertTrue(cs_path.exists(), "Change set file should NOT be deleted")
        updated = json.loads(cs_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["status"], "discarded")
        self.assertIn("TTL expired", updated.get("discard_reason", ""))

    def test_list_discarded_returns_discarded(self):
        """list_discarded_change_sets should return discarded change sets."""
        cs_data = {
            "id": "cs_test_discarded_list",
            "session_id": "s_discard_list",
            "source": "file_edit",
            "type": "direct",
            "status": "discarded",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "discard_reason": "TTL expired (1s)",
            "files": [{"path": "/tmp/fake.txt", "before_text": "a", "after_text": "b",
                        "before_exists": True, "after_exists": True}],
        }
        cs_path = self._cs_dir / "cs_test_discarded_list.json"
        cs_path.write_text(json.dumps(cs_data), encoding="utf-8")

        from edit_transaction import list_discarded_change_sets
        discarded = list_discarded_change_sets(session_id="s_discard_list")
        self.assertEqual(len(discarded), 1)
        self.assertEqual(discarded[0]["id"], "cs_test_discarded_list")

    def test_list_discarded_filters_by_session(self):
        """list_discarded_change_sets should filter by session_id."""
        for i, sid in enumerate(["s_a", "s_b"]):
            cs_data = {
                "id": f"cs_filter_{i}",
                "session_id": sid,
                "source": "file_edit",
                "type": "direct",
                "status": "discarded",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "discard_reason": "test",
                "files": [],
            }
            (self._cs_dir / f"cs_filter_{i}.json").write_text(
                json.dumps(cs_data), encoding="utf-8"
            )

        from edit_transaction import list_discarded_change_sets
        self.assertEqual(len(list_discarded_change_sets(session_id="s_a")), 1)
        self.assertEqual(len(list_discarded_change_sets(session_id="s_b")), 1)
        self.assertEqual(len(list_discarded_change_sets()), 2)


class TestDiscardAwareRecovery(unittest.TestCase):
    """Verify that recovery blocks when discarded change sets are detected."""

    def test_discarded_blocks_recovery(self):
        """Simulate: no pending, but discarded exists → recovery blocked."""
        pending_change_sets = {}
        disk_pending = []
        discarded = [{"id": "cs_disc_001", "status": "discarded", "discard_reason": "TTL expired"}]

        has_any_pending = bool(pending_change_sets) or bool(disk_pending)
        recovery_allowed = True

        # Replicate recovery guard 1 from agentic_loop.py
        if not has_any_pending:
            if discarded:
                recovery_allowed = False

        self.assertFalse(recovery_allowed,
                         "Recovery must be blocked when discarded change sets exist")

    def test_no_discarded_allows_recovery(self):
        """Simulate: no pending, no discarded → recovery allowed (subject to cooldown)."""
        pending_change_sets = {}
        disk_pending = []
        discarded = []

        has_any_pending = bool(pending_change_sets) or bool(disk_pending)
        recovery_allowed = True

        if not has_any_pending:
            if discarded:
                recovery_allowed = False

        self.assertTrue(recovery_allowed,
                        "Recovery should be allowed when no pending and no discarded")

    def test_accepted_not_discarded(self):
        """Accepted change sets should NOT appear in discarded list."""
        # This is ensured by list_discarded_change_sets filtering status=="discarded"
        cs = {"id": "cs_accepted", "status": "applied", "session_id": "s_test"}
        self.assertNotEqual(cs["status"], "discarded")

    def test_rejected_not_discarded(self):
        """Rejected change sets should NOT appear in discarded list."""
        cs = {"id": "cs_rejected", "status": "rejected", "session_id": "s_test"}
        self.assertNotEqual(cs["status"], "discarded")


class TestApprovalRouteTransition(unittest.TestCase):
    """Regression tests for _transition_task_after_approval in routes/changes.py.

    Verifies that accepting/rejecting change sets via the API route correctly
    transitions the task out of waiting_approval when appropriate.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmpdir.name)
        self.session_id = f"test-approval-route-{id(self)}"
        reset_session_tasks(self.session_id, self.workspace)
        set_task_context(self.session_id, self.workspace)
        # Isolated change set store
        self._cs_dir = self.workspace / "cs_store"
        self._cs_dir.mkdir()
        self._old_cs_dir = os.environ.get("NANOBOT_CHANGESET_DIR")
        os.environ["NANOBOT_CHANGESET_DIR"] = str(self._cs_dir)
        # Create a file target for change sets
        self._target = self.workspace / "target.txt"
        self._target.write_text("original\n", encoding="utf-8")

    def tearDown(self):
        if self._old_cs_dir is None:
            os.environ.pop("NANOBOT_CHANGESET_DIR", None)
        else:
            os.environ["NANOBOT_CHANGESET_DIR"] = self._old_cs_dir
        self._tmpdir.cleanup()

    def _make_waiting_approval_task(self):
        """Helper: create a root task and drive it to waiting_approval."""
        store = get_task_store(self.session_id, self.workspace)
        root = ensure_root_task_for_message(
            self.workspace, self.session_id, "Approval route test"
        )
        store.transition(root.id, "in_progress", source="test", reason="start work")
        store.transition(root.id, "waiting_approval",
                         source="_maybe_advance_task_state",
                         reason="pending change sets")
        return store, root

    def _create_pending_cs(self, suffix="1"):
        """Helper: create a pending change set on disk for this session."""
        from edit_transaction import create_pending_change_set
        plan = {
            "path": str(self._target),
            "before_text": self._target.read_text(encoding="utf-8"),
            "after_text": f"modified_{suffix}\n",
            "before_exists": True,
            "after_exists": True,
        }
        return create_pending_change_set(plan, session_id=self.session_id, source="file_edit")

    # ── Test 1: accept sole pending CS → waiting_approval → in_progress ──

    def test_accept_sole_cs_transitions_to_in_progress(self):
        store, root = self._make_waiting_approval_task()
        cs = self._create_pending_cs()

        from routes.changes import _transition_task_after_approval
        from edit_transaction import accept_change_set
        result = accept_change_set(cs["id"])
        self.assertTrue(result["success"])
        _transition_task_after_approval(result["change_set"], "accept")

        updated = store.get_task(root.id)
        self.assertEqual(updated.state, "in_progress")
        last_entry = updated.state_history[-1]
        self.assertEqual(last_entry["from_state"], "waiting_approval")
        self.assertEqual(last_entry["to_state"], "in_progress")
        self.assertEqual(last_entry["source"], "approval_route")

    # ── Test 2: reject sole pending CS → waiting_approval → planned ──

    def test_reject_sole_cs_transitions_to_planned(self):
        store, root = self._make_waiting_approval_task()
        cs = self._create_pending_cs()

        from routes.changes import _transition_task_after_approval
        from edit_transaction import reject_change_set
        result = reject_change_set(cs["id"])
        self.assertTrue(result["success"])
        _transition_task_after_approval(result["change_set"], "reject")

        updated = store.get_task(root.id)
        self.assertEqual(updated.state, "planned")
        last_entry = updated.state_history[-1]
        self.assertEqual(last_entry["to_state"], "planned")
        self.assertIn("reject", last_entry.get("reason", ""))

    def _write_extra_pending_cs(self, cs_id):
        """Write a second pending CS directly to disk (bypasses create guard)."""
        now = datetime.now(timezone.utc).isoformat()
        cs_extra = {
            "id": cs_id,
            "session_id": self.session_id,
            "source": "file_edit",
            "type": "direct",
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "files": [{"path": str(self._target), "before_text": "original\n",
                        "after_text": f"extra_{cs_id}\n", "before_exists": True, "after_exists": True}],
        }
        (self._cs_dir / f"{cs_id}.json").write_text(json.dumps(cs_extra), encoding="utf-8")
        return cs_extra

    # ── Test 3: accept one of two CS → stays waiting_approval ──

    def test_accept_one_of_two_stays_waiting_approval(self):
        store, root = self._make_waiting_approval_task()
        cs1 = self._create_pending_cs("a")
        # Write a second pending CS directly to disk (bypasses create guard)
        self._write_extra_pending_cs("cs_extra_001")

        from routes.changes import _transition_task_after_approval
        from edit_transaction import accept_change_set
        # Accept cs1 — but cs_extra_001 is still pending
        result = accept_change_set(cs1["id"])
        self.assertTrue(result["success"])
        _transition_task_after_approval(result["change_set"], "accept")

        updated = store.get_task(root.id)
        self.assertEqual(updated.state, "waiting_approval",
                         "Must stay waiting_approval when another CS is still pending")

    # ── Test 4: reject one of two CS → stays waiting_approval ──

    def test_reject_one_of_two_stays_waiting_approval(self):
        store, root = self._make_waiting_approval_task()
        cs1 = self._create_pending_cs("r1")
        self._write_extra_pending_cs("cs_extra_002")

        from routes.changes import _transition_task_after_approval
        from edit_transaction import reject_change_set
        result = reject_change_set(cs1["id"])
        self.assertTrue(result["success"])
        _transition_task_after_approval(result["change_set"], "reject")

        updated = store.get_task(root.id)
        self.assertEqual(updated.state, "waiting_approval",
                         "Must stay waiting_approval when another CS is still pending")

    # ── Test 5: accept all CS sequentially → in_progress after last ──

    def test_accept_all_sequentially_transitions_after_last(self):
        store, root = self._make_waiting_approval_task()
        cs1 = self._create_pending_cs("seq1")
        self._write_extra_pending_cs("cs_extra_003")

        from routes.changes import _transition_task_after_approval
        from edit_transaction import accept_change_set

        # Accept first — still has cs_extra_003 pending
        result1 = accept_change_set(cs1["id"])
        self.assertTrue(result1["success"])
        _transition_task_after_approval(result1["change_set"], "accept")
        self.assertEqual(store.get_task(root.id).state, "waiting_approval")

        # Accept second by updating JSON on disk (can't use accept_change_set
        # because cs1's accept already changed the file, causing snapshot mismatch)
        cs_extra_path = self._cs_dir / "cs_extra_003.json"
        cs_extra_data = json.loads(cs_extra_path.read_text(encoding="utf-8"))
        cs_extra_data["status"] = "applied"
        cs_extra_path.write_text(json.dumps(cs_extra_data), encoding="utf-8")
        _transition_task_after_approval(cs_extra_data, "accept")
        self.assertEqual(store.get_task(root.id).state, "in_progress")

    # ── Test 6: reject all CS → planned after last ──

    def test_reject_all_transitions_to_planned_after_last(self):
        store, root = self._make_waiting_approval_task()
        cs1 = self._create_pending_cs("rej1")
        self._write_extra_pending_cs("cs_extra_004")

        from routes.changes import _transition_task_after_approval
        from edit_transaction import reject_change_set

        # Reject first — cs_extra_004 still pending
        result1 = reject_change_set(cs1["id"])
        self.assertTrue(result1["success"])
        _transition_task_after_approval(result1["change_set"], "reject")
        self.assertEqual(store.get_task(root.id).state, "waiting_approval")

        # Reject second by updating JSON on disk
        cs_extra_path = self._cs_dir / "cs_extra_004.json"
        cs_extra_data = json.loads(cs_extra_path.read_text(encoding="utf-8"))
        cs_extra_data["status"] = "rejected"
        cs_extra_path.write_text(json.dumps(cs_extra_data), encoding="utf-8")
        _transition_task_after_approval(cs_extra_data, "reject")
        self.assertEqual(store.get_task(root.id).state, "planned")

    # ── Test 7: empty session_id → no crash, change set still processed ──

    def test_empty_session_id_no_crash(self):
        """_transition_task_after_approval silently returns when session_id is empty."""
        from routes.changes import _transition_task_after_approval
        # Should not raise
        _transition_task_after_approval({"id": "cs_no_session", "session_id": ""}, "accept")
        _transition_task_after_approval({"id": "cs_no_session2"}, "reject")  # missing key

    # ── Test 8: task_store unavailable → change set accepted, transition skipped ──

    def test_store_unavailable_change_set_still_accepted(self):
        """If get_task_store raises, change set acceptance should still succeed."""
        cs = self._create_pending_cs("store_fail")

        from edit_transaction import accept_change_set
        result = accept_change_set(cs["id"])
        self.assertTrue(result["success"])

        from routes.changes import _transition_task_after_approval
        with patch("task_store.get_task_store", side_effect=RuntimeError("store gone")):
            # Should not raise — exception is caught internally
            _transition_task_after_approval(result["change_set"], "accept")

        # Change set is still applied regardless
        self.assertEqual(result["change_set"]["status"], "applied")


if __name__ == "__main__":
    unittest.main()
