"""Task-state tool access rules for Nanobot.

This module defines the default action policy for each TaskState so the
agentic loop can make state-driven tool decisions.

State Transition Priority Strategy (agentic_loop.py _maybe_advance_task_state):
    0. Pending change sets exist → waiting_approval (highest priority, blocks all)
    1. Non-regressible guard → skip if in waiting_approval/verifying/terminal
    2. Work execution tools (sub_agent/todo_manage/fork/etc) from created/planned → in_progress
       - Atomic upgrade: created → in_progress skips planned when both file_read + work tools
    3. file_read from created → planned (research phase, lower priority than work tools)
    4. All children completed from in_progress → verifying (triggers forced verification)
    5. Consecutive failures → blocked (failure handling)

Approval-State Recovery (anti-oscillation, agentic_loop.py task_constraint_recovery):
    When the task is in waiting_approval but no pending change sets exist (both in-memory
    cache AND on-disk store are empty), recovery MAY occur subject to these guards:
    - Guard 1 (Discard detection): If change sets were silently discarded by TTL or
      overflow cleanup (status="discarded" on disk), block recovery entirely and set
      metadata.pending_changes_discarded=True on the root task. The user must explicitly
      accept or reject to unblock. This prevents treating cleanup races as user action.
    - Guard 2 (Cooldown): If the last transition was in_progress → waiting_approval
      within the last 30 seconds, do NOT recover. Prevents ping-pong oscillation.
    - Guard 3 (Recovery target): transitions to "planned" (NOT in_progress) so the
      agent re-evaluates rather than immediately re-creating the same edit.
    - User exit: change_set_accept / change_set_reject are always allowed regardless of
      task state (via APPROVAL_LIFECYCLE_TOOLS), so users can always unblock the task.

Change Set Creation Guard (edit_transaction.py create_pending_change_set):
    When the agent tries to create a new change set while un-reviewed pending change
    sets already exist for the same session, creation is BLOCKED with a ValueError.
    This prevents the agent from silently overwriting/discarding old pending change sets.
    Rollback_review change sets are exempt (system-created conflict resolution).

Note: created and planned allow all common tools including file_edit/file_write
(which produce change sets requiring user approval) and shell_execute/python_execute
(which have their own destructive-command safety guards). The transition priority
ensures that once work execution begins, the task immediately advances to in_progress.
"""

from __future__ import annotations

from typing import Dict, List

APPROVAL_LIFECYCLE_TOOLS = {
    "change_set_accept",
    "change_set_reject",
}

STATE_ACTION_RULES: Dict[str, List[str]] = {
    # Note: created and planned have the same tool set, but transition priority differs:
    # - created + file_read → planned (research phase)
    # - created/planned + work tools (file_edit/file_write/sub_agent/todo_manage/etc) → in_progress
    # file_edit/file_write are safe to allow here because they produce change sets requiring
    # user approval (not immediately applied), and shell_execute/python_execute have their
    # own destructive-command safety guards.
    "created": ["file_read", "file_list", "grep_search", "find_by_name", "sub_agent", "todo_manage",
                 "file_edit", "file_write", "shell_execute", "python_execute"],
    "planned": ["file_read", "file_list", "grep_search", "find_by_name", "sub_agent", "todo_manage",
                 "file_edit", "file_write", "shell_execute", "python_execute"],
    # in_progress allows all tools and is the gateway to verifying (via all-children-completed)
    "in_progress": ["*"],
    "blocked": [],
    "waiting_approval": [],
    "verifying": ["shell_execute", "python_execute", "file_read", "sub_agent"],
    "completed": [],
    "failed": [],
    "cancelled": [],
    "backgrounded": ["*"],
}


def is_tool_allowed(state: str, tool_name: str) -> bool:
    """Return True when a tool is permitted under the given task state."""
    normalized_state = str(state or "").strip().lower()
    normalized_tool = str(tool_name or "").strip().lower()

    if normalized_tool in APPROVAL_LIFECYCLE_TOOLS:
        return True

    allowed_tools = STATE_ACTION_RULES.get(normalized_state, [])
    if not allowed_tools:
        return False
    if "*" in allowed_tools:
        return True
    return normalized_tool in {tool.lower() for tool in allowed_tools}
