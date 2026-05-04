"""task_manage tool — Expose task_store CRUD to the model.

AP-5 (Audit): The task_store backend (767 lines) has full Task lifecycle
management (state machine, parent-child trees, persistence) but was never
exposed as a model-callable tool.  The model could only interact with tasks
indirectly via todo_manage.

This tool gives the model direct access to:
  - task_list:   list tasks (optionally filtered by state)
  - task_create: create a new child task under the current root
  - task_update: update task state, title, or blocked_reason
  - task_get:    get detailed info about a specific task

CTO audit concern addressed: "模型仍然处于通信孤岛状态" — this tool lets
the model actively manage and synchronize task state, enabling sub-agents
to report progress and the main agent to coordinate work.
"""
import logging
import time
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("nanobot.tools.task_manage")

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "task_manage",
        "description": (
            "Manage session tasks: list, create, update, or inspect tasks. "
            "Tasks track work progress with states (created, planned, in_progress, "
            "blocked, verifying, completed, failed, cancelled). "
            "Use this to coordinate multi-step work, track sub-agent progress, "
            "and maintain a clear record of what has been done."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "create", "update", "get", "tree"],
                    "description": (
                        "Action to perform: "
                        "'list' — list all tasks (optional state filter); "
                        "'create' — create a new child task; "
                        "'update' — change task state or metadata; "
                        "'get' — get detailed info about one task; "
                        "'tree' — show full task tree from root"
                    )
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (required for 'update', 'get', 'tree')"
                },
                "title": {
                    "type": "string",
                    "description": "Task title (for 'create' or 'update')"
                },
                "objective": {
                    "type": "string",
                    "description": "Task objective/description (for 'create')"
                },
                "state": {
                    "type": "string",
                    "enum": [
                        "created", "planned", "in_progress", "blocked",
                        "waiting_approval", "verifying", "completed",
                        "failed", "cancelled"
                    ],
                    "description": "Target state (for 'update')"
                },
                "blocked_reason": {
                    "type": "string",
                    "description": "Reason for blocking (when state='blocked')"
                },
                "result_summary": {
                    "type": "string",
                    "description": "Result summary (when state='completed')"
                },
                "filter_state": {
                    "type": "string",
                    "description": "Filter tasks by state (for 'list')"
                },
                "priority": {
                    "type": "integer",
                    "description": "Task priority 1-10 (default 5, for 'create')"
                }
            },
            "required": ["action"]
        }
    }
}

ALIASES = ["task", "manage_task", "task_control"]
IS_READONLY = False

GUIDANCE = {
    "replaces_shell": [],
    "shell_never": "",
    "tips": [
        "Use task_manage to track multi-step work — create child tasks for each step.",
        "Update task state as you progress: planned → in_progress → completed.",
        "Set state='blocked' with blocked_reason when waiting on external input.",
        "Use action='tree' to see the full task hierarchy.",
    ],
}


def execute(args: dict, workspace: Path) -> dict:
    """Execute task_manage — dispatch to the appropriate action."""
    action = args.get("action", "").strip().lower()
    if not action:
        return {"success": False, "output": "", "error": "No action specified. Use: list, create, update, get, tree"}

    session_id = args.get("_session_id", "global")

    try:
        from task_store import (
            get_task_store, create_child_task, ensure_root_task_for_message,
            TASK_STATES, ALLOWED_TRANSITIONS,
        )
    except ImportError as e:
        return {"success": False, "output": "", "error": f"task_store not available: {e}"}

    store = get_task_store(session_id, workspace)

    if action == "list":
        return _action_list(store, args)
    elif action == "create":
        return _action_create(store, args, workspace, session_id)
    elif action == "update":
        return _action_update(store, args)
    elif action == "get":
        return _action_get(store, args)
    elif action == "tree":
        return _action_tree(store, args)
    else:
        return {"success": False, "output": "", "error": f"Unknown action: {action}. Use: list, create, update, get, tree"}


def _action_list(store, args: dict) -> dict:
    """List tasks with optional state filter."""
    filter_state = args.get("filter_state", "").strip().lower()
    tasks = store.list_tasks()

    if filter_state:
        tasks = [t for t in tasks if t.state == filter_state]

    if not tasks:
        msg = f"No tasks found" + (f" with state '{filter_state}'" if filter_state else "")
        return {"success": True, "output": msg, "error": ""}

    lines = [f"Tasks ({len(tasks)} total" + (f", filtered: {filter_state}" if filter_state else "") + "):"]
    for t in tasks:
        parent_tag = f" [child of {t.parent_id[:16]}]" if t.parent_id else " [root]"
        lines.append(
            f"  [{t.state}] {t.id} — {t.title}{parent_tag}"
            + (f" (blocked: {t.blocked_reason[:40]})" if t.blocked_reason else "")
        )

    return {"success": True, "output": "\n".join(lines), "error": ""}


def _action_create(store, args: dict, workspace, session_id: str) -> dict:
    """Create a new child task under the current root task."""
    title = args.get("title", "").strip()
    objective = args.get("objective", "").strip()
    priority = int(args.get("priority", 5) or 5)

    if not title:
        return {"success": False, "output": "", "error": "title is required for action='create'"}

    # Get or create root task
    root = store.get_latest_root_task()
    if root is None:
        from task_store import ensure_root_task_for_message
        root = ensure_root_task_for_message(
            workspace, session_id, title,
            metadata={"source": "task_manage_create"},
        )

    try:
        from task_store import create_child_task
        child = create_child_task(
            workspace, session_id, root.id,
            title=title,
            objective=objective or title,
            priority=priority,
            state="planned",
            metadata={"source": "task_manage"},
        )
        logger.info(f"[TaskManage] Created child task {child.id}: {title[:50]}")
        return {
            "success": True,
            "output": (
                f"Task created:\n"
                f"  ID: {child.id}\n"
                f"  Title: {child.title}\n"
                f"  State: {child.state}\n"
                f"  Parent: {root.id}\n"
                f"  Priority: {child.priority}"
            ),
            "error": "",
        }
    except Exception as e:
        return {"success": False, "output": "", "error": f"Failed to create task: {e}"}


def _action_update(store, args: dict) -> dict:
    """Update a task's state or metadata."""
    task_id = args.get("task_id", "").strip()
    if not task_id:
        return {"success": False, "output": "", "error": "task_id is required for action='update'"}

    task = store.get_task(task_id)
    if task is None:
        return {"success": False, "output": "", "error": f"Task not found: {task_id}"}

    new_state = args.get("state", "").strip().lower()
    title = args.get("title", "").strip()
    blocked_reason = args.get("blocked_reason", "").strip()
    result_summary = args.get("result_summary", "").strip()

    if not new_state and not title and not blocked_reason and not result_summary:
        return {"success": False, "output": "", "error": "Provide at least one of: state, title, blocked_reason, result_summary"}

    try:
        updates = {}
        if title:
            updates["title"] = title
        if blocked_reason:
            updates["blocked_reason"] = blocked_reason
        if result_summary:
            updates["result_summary"] = result_summary

        if new_state:
            task = store.transition(
                task_id, new_state,
                source="task_manage",
                reason=result_summary or blocked_reason or f"Updated via task_manage",
                **updates,
            )
        else:
            # Just update fields without state change
            for key, value in updates.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            task.update_timestamp()
            store.save()

        # Promote root state if this is a child task
        if task.parent_id:
            store.promote_root_state_from_children(task.parent_id)

        logger.info(f"[TaskManage] Updated task {task_id}: state={task.state}")
        return {
            "success": True,
            "output": (
                f"Task updated:\n"
                f"  ID: {task.id}\n"
                f"  Title: {task.title}\n"
                f"  State: {task.state}\n"
                + (f"  Blocked: {task.blocked_reason}\n" if task.blocked_reason else "")
                + (f"  Result: {task.result_summary[:100]}\n" if task.result_summary else "")
            ),
            "error": "",
        }
    except (KeyError, ValueError) as e:
        return {"success": False, "output": "", "error": str(e)}
    except Exception as e:
        return {"success": False, "output": "", "error": f"Failed to update task: {e}"}


def _action_get(store, args: dict) -> dict:
    """Get detailed info about a specific task."""
    task_id = args.get("task_id", "").strip()
    if not task_id:
        return {"success": False, "output": "", "error": "task_id is required for action='get'"}

    task = store.get_task(task_id)
    if task is None:
        return {"success": False, "output": "", "error": f"Task not found: {task_id}"}

    children = store.get_children(task_id)
    lines = [
        f"Task: {task.id}",
        f"  Title: {task.title}",
        f"  State: {task.state}",
        f"  Priority: {task.priority}",
        f"  Owner: {task.owner}",
        f"  Created: {task.created_at}",
        f"  Updated: {task.updated_at}",
    ]
    if task.objective:
        lines.append(f"  Objective: {task.objective[:200]}")
    if task.parent_id:
        lines.append(f"  Parent: {task.parent_id}")
    if task.current_step:
        lines.append(f"  Current step: {task.current_step}")
    if task.blocked_reason:
        lines.append(f"  Blocked: {task.blocked_reason}")
    if task.result_summary:
        lines.append(f"  Result: {task.result_summary[:200]}")
    if task.artifacts:
        lines.append(f"  Artifacts: {', '.join(task.artifacts[:5])}")
    if children:
        lines.append(f"  Children ({len(children)}):")
        for child in children:
            lines.append(f"    [{child.state}] {child.id} — {child.title}")

    return {"success": True, "output": "\n".join(lines), "error": ""}


def _action_tree(store, args: dict) -> dict:
    """Show the full task tree from a root task."""
    task_id = args.get("task_id", "").strip()

    if not task_id:
        root = store.get_latest_root_task()
        if root is None:
            return {"success": True, "output": "No tasks in this session.", "error": ""}
        task_id = root.id

    tree = store.build_task_tree(task_id)
    if tree is None:
        return {"success": False, "output": "", "error": f"Task not found: {task_id}"}

    lines = []

    def _render_node(node: dict, indent: int = 0) -> None:
        task_data = node["task"]
        prefix = "  " * indent
        state = task_data.get("state", "?")
        title = task_data.get("title", "?")
        tid = task_data.get("id", "?")
        lines.append(f"{prefix}[{state}] {tid} — {title}")
        for child in node.get("children", []):
            _render_node(child, indent + 1)

    _render_node(tree)

    return {"success": True, "output": "\n".join(lines), "error": ""}
