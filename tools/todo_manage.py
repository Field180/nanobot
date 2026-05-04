"""todo_manage tool — Create and manage task lists for multi-step work.

P62: Inspired by Claw's TodoWriteTool.
Tracks progress through complex tasks with pending/in_progress/completed states.
The todo list is stored per-session and streamed to the frontend via SSE.
"""
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nanobot.tools.todo_manage")

from task_store import (
    coerce_todo_payloads,
    get_task_store,
    promote_root_from_children,
    set_task_context,
)

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "todo_manage",
        "description": (
            "Create and manage a task list for the current session. "
            "Use proactively for multi-step tasks (3+ steps) to track progress. "
            "Each todo has: id, content (what to do), status (pending/in_progress/completed). "
            "Rules: exactly ONE task should be in_progress at a time. "
            "Mark tasks completed IMMEDIATELY after finishing — don't batch completions. "
            "Do NOT use this for single trivial tasks or purely informational questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": (
                        "Full list of todo items. Always send the COMPLETE list — "
                        "this replaces the previous state entirely."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Unique identifier for the todo item (e.g. '1', 'setup', 'test')"
                            },
                            "content": {
                                "type": "string",
                                "description": "What needs to be done (imperative form, e.g. 'Run tests')"
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "Current status of the task"
                            },
                        },
                        "required": ["id", "content", "status"]
                    }
                }
            },
            "required": ["todos"]
        }
    }
}

ALIASES = ["todo", "task_list", "plan"]
IS_READONLY = True  # Does not modify filesystem

GUIDANCE = {
    "replaces_shell": [],
    "shell_never": "",
    "tips": [
        "Use for multi-step tasks (3+ steps) to track progress.",
        "Always send the COMPLETE todo list — it replaces previous state.",
        "Mark tasks completed IMMEDIATELY after finishing each one.",
        "Keep exactly ONE task as in_progress at any time.",
        "Do NOT use for single trivial tasks or informational questions.",
    ],
}

# ── Session-scoped todo storage (backward compatible: only id/content/status) ──
_SESSION_TODOS: Dict[str, List[Dict[str, str]]] = {}
_SESSION_ID: str = ""
_SESSION_WORKSPACE: Optional[Path] = None  # Bound workspace for multi-workspace support


def _task_to_legacy_todo(task) -> Dict[str, str]:
    """Convert Task to legacy todo format with only id/content/status."""
    status = "pending"
    if hasattr(task, 'state'):
        state = task.state
        if state in ("completed", "done"):
            status = "completed"
        elif state in ("in_progress", "active"):
            status = "in_progress"
    return {
        "id": str(getattr(task, 'id', '')),
        "content": str(getattr(task, 'title', getattr(task, 'content', ''))),
        "status": status,
    }


def set_session_id(session_id: str, workspace: Optional[Path] = None) -> None:
    """Called by agentic_loop to set current session for todo storage.
    
    Args:
        session_id: The session identifier
        workspace: Optional workspace path. If provided, binds this workspace to the session
                  for subsequent get_todos() calls without explicit workspace.
    """
    global _SESSION_ID, _SESSION_WORKSPACE
    _SESSION_ID = session_id
    if workspace is not None:
        _SESSION_WORKSPACE = Path(workspace).resolve()


def get_todos(session_id: str = "", workspace: Optional[Path] = None) -> List[Dict[str, str]]:
    """Get current todo list for a session (legacy format: id/content/status only).
    
    Args:
        session_id: Session identifier. Uses bound session if not provided.
        workspace: Workspace path. Uses bound workspace if not provided,
                 falls back to task_store's current workspace context.
    """
    sid = session_id or _SESSION_ID
    # Use explicit workspace > bound workspace > None (let task_store handle default)
    ws = workspace or _SESSION_WORKSPACE
    try:
        store = get_task_store(sid, ws)
        root = store.get_latest_root_task()
        if root is None:
            return []
        # Only return legacy three-field format for backward compatibility
        todos = [_task_to_legacy_todo(task) for task in store.get_children(root.id)]
        _SESSION_TODOS[sid] = todos
        return todos
    except Exception:
        # Fallback to cached legacy format
        return _SESSION_TODOS.get(sid, [])


def execute(args: dict, workspace: Path) -> dict:
    """Execute todo_manage: validate and store the todo list."""
    todos_raw = args.get("todos", [])

    if not isinstance(todos_raw, list):
        return {"success": False, "output": "", "error": "todos must be an array"}

    if len(todos_raw) == 0:
        return {"success": False, "output": "", "error": "todos array is empty"}

    if len(todos_raw) > 20:
        return {"success": False, "output": "", "error": "Too many todos (max 20). Break into smaller milestones."}

    sid = _SESSION_ID or "default"
    set_task_context(sid, workspace)
    store = get_task_store(sid, workspace)

    # Validate raw payloads before coercing into Task objects so we preserve
    # the old todo_manage contract and reject empty content explicitly.
    for index, item in enumerate(todos_raw):
        if not isinstance(item, dict):
            return {"success": False, "output": "", "error": f"Todo item {index} is not an object"}
        raw_content = str(item.get("content", "")).strip()
        if not raw_content:
            tid = str(item.get("id", index + 1))
            return {"success": False, "output": "", "error": f"Todo item {tid} has empty content"}

    root = store.get_latest_root_task()
    if root is None:
        root = store.create_task(
            id=f"root-{sid}",
            title="Current session task",
            objective="Session todo list",
            state="created",
            owner="main_agent",
            priority=5,
        )

    try:
        tasks = coerce_todo_payloads(todos_raw, parent_id=root.id)
    except Exception as exc:
        return {"success": False, "output": "", "error": str(exc)}

    if not tasks:
        return {"success": False, "output": "", "error": "No valid todo items provided"}

    seen_ids = set()
    in_progress_count = 0
    for task in tasks:
        if task.id in seen_ids:
            return {"success": False, "output": "", "error": f"Duplicate todo id: {task.id}"}
        seen_ids.add(task.id)
        if not task.title:
            return {"success": False, "output": "", "error": f"Todo item {task.id} has empty content"}
        if task.state == "in_progress":
            in_progress_count += 1

    tasks = store.replace_children(root.id, tasks)
    root.current_step = tasks[0].title if tasks else root.current_step
    root.steps = [task.title for task in tasks]

    # Determine next state based on children states (use internal _set_state for computed states)
    next_state = root.state
    if any(task.state == "in_progress" for task in tasks):
        next_state = "in_progress"
    elif all(task.state == "completed" for task in tasks):
        next_state = "completed"
    elif any(task.state == "blocked" for task in tasks):
        next_state = "blocked"
    elif any(task.state == "waiting_approval" for task in tasks):
        next_state = "waiting_approval"
    elif any(task.state == "backgrounded" for task in tasks):
        next_state = "backgrounded"
    elif root.state == "created":
        next_state = "planned"

    # Use _set_state to respect terminal state protection
    store._set_state(root, next_state, source="todo_manage", reason="task list summary updated")
    store.upsert_task(root)
    promote_root_from_children(sid, workspace)
    # Store only legacy format for backward compatibility
    _SESSION_TODOS[sid] = [_task_to_legacy_todo(task) for task in tasks]

    # Build summary (use legacy format for status check)
    completed = sum(1 for t in tasks if t.state == "completed")
    pending = sum(1 for t in tasks if _task_to_legacy_todo(t)["status"] == "pending")
    in_progress = sum(1 for t in tasks if t.state == "in_progress")
    total = len(tasks)

    lines = [f"📋 Todo list updated: {completed}/{total} completed"]
    for t in tasks:
        legacy = _task_to_legacy_todo(t)
        icon = {"completed": "✅", "in_progress": "🔄", "pending": "⬚"}.get(legacy["status"], "⬚")
        lines.append(f"  {icon} [{t.id}] {t.title}")

    if in_progress_count > 1:
        lines.append(f"\n⚠️ Warning: {in_progress_count} tasks are in_progress — ideally only 1 should be active.")

    output = "\n".join(lines)
    logger.info(f"[TodoManage] Session {sid}: {completed}/{total} completed, {in_progress} active, {pending} pending")

    # Build return data with strict separation:
    # - _todo_update: legacy format (id/content/status only) for backward compatibility
    # - _task_update: full Task objects with new fields
    legacy_todos = [_task_to_legacy_todo(task) for task in tasks]
    return {
        "success": True,
        "output": output,
        "error": "",
        # Backward compatible: only legacy three-field format
        "_todo_update": legacy_todos,
        # New format: full Task data with all fields
        "_task_update": {
            "root_task": root.to_dict(),
            "tasks": [task.to_dict() for task in tasks],
        },
    }
