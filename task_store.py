"""Session-scoped task storage for Nanobot.

Phase 1 of the task-centric control plane keeps the implementation light:
- Every user intent gets a root Task object.
- todo_manage stores step tasks as Task objects under that root task.
- The store is session-scoped and persisted to JSON so task state survives
  across turns and compaction.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

logger = logging.getLogger("nanobot.task_store")

# Lock contention metrics — queryable via get_lock_metrics()
_LOCK_RETRIES = 0
_LOCK_FAILURES = 0
_LOCK_SUCCESSES = 0


def get_lock_metrics() -> dict:
    """Return lock contention metrics for monitoring/alerting."""
    return {
        "lock_retries": _LOCK_RETRIES,
        "lock_failures": _LOCK_FAILURES,
        "lock_successes": _LOCK_SUCCESSES,
    }


def _emit_lock_metrics() -> None:
    """Emit lock metrics as structured log for external collection."""
    logger.info(
        "[TaskStore/Metrics] lock_successes=%d lock_retries=%d lock_failures=%d",
        _LOCK_SUCCESSES, _LOCK_RETRIES, _LOCK_FAILURES,
    )

TASK_STATES = (
    "created",
    "planned",
    "in_progress",
    "blocked",
    "waiting_approval",
    "verifying",
    "completed",
    "failed",
    "cancelled",
    "backgrounded",
)

TERMINAL_TASK_STATES = {"completed", "failed", "cancelled"}

TODO_STATUS_TO_TASK_STATE = {
    "pending": "planned",
    "in_progress": "in_progress",
    "completed": "completed",
}

TASK_STATE_TO_TODO_STATUS = {
    "created": "pending",
    "planned": "pending",
    "in_progress": "in_progress",
    "blocked": "pending",
    "waiting_approval": "pending",
    "verifying": "in_progress",
    "completed": "completed",
    "failed": "pending",
    "cancelled": "pending",
    "backgrounded": "in_progress",
}

# `waiting_approval` may still appear on a root task whose current state is
# `blocked`, but only via computed promotion from child tasks in
# `promote_root_state_from_children()`. We do not allow a blocked root task to
# transition directly to `waiting_approval` through its own tool activity,
# because blocked tasks have no active edit-producing path that can create a new
# pending change set on their own.
ALLOWED_TRANSITIONS = {
    "created": {"planned", "in_progress", "blocked", "waiting_approval", "cancelled", "failed", "backgrounded"},
    "planned": {"in_progress", "blocked", "waiting_approval", "completed", "cancelled", "failed", "backgrounded"},
    "in_progress": {"planned", "blocked", "waiting_approval", "verifying", "completed", "failed", "cancelled", "backgrounded"},
    "blocked": {"planned", "in_progress", "failed", "cancelled"},
    "waiting_approval": {"planned", "in_progress", "blocked", "completed", "failed", "cancelled"},
    "verifying": {"in_progress", "completed", "failed", "blocked", "cancelled"},
    "completed": set(),
    "failed": {"planned", "cancelled"},
    "cancelled": set(),
    "backgrounded": {"in_progress", "completed", "failed", "cancelled"},
}

_CURRENT_SESSION_ID = ""
_CURRENT_WORKSPACE: Optional[Path] = None
_STORE_CACHE: Dict[Tuple[str, str], "TaskStore"] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_state(state: str) -> str:
    state = str(state or "created").strip().lower()
    if state not in TASK_STATES:
        return "created"
    return state


def _derive_task_title(text: str, max_len: int = 56) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return "Current session task"
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + "…"


def _clone_state_history(history: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [dict(entry) for entry in history if isinstance(entry, dict)]


def _infer_transition_source(default: str = "unknown") -> str:
    frame = inspect.currentframe()
    try:
        if frame is None or frame.f_back is None or frame.f_back.f_back is None:
            return default
        caller = frame.f_back.f_back
        if getattr(caller.f_code, "co_name", "") == "transition" and caller.f_back is not None:
            caller = caller.f_back
        return str(getattr(caller.f_code, "co_name", default) or default)
    finally:
        del frame


def _build_state_change_entry(
    task: "Task",
    from_state: str,
    to_state: str,
    *,
    source: str,
    reason: str = "",
) -> Dict[str, Any]:
    return {
        "timestamp": _now_iso(),
        "task_id": task.id,
        "task_title": task.title,
        "parent_id": task.parent_id,
        "from_state": from_state,
        "to_state": to_state,
        "source": source or "unknown",
        "reason": str(reason or "").strip(),
    }


@dataclass
class Task:
    """A task node in the session task tree."""

    id: str
    title: str
    objective: str = ""
    state: str = "created"
    priority: int = 5
    owner: str = "main_agent"
    current_step: str = ""
    steps: List[str] = field(default_factory=list)
    blocked_reason: str = ""
    approval_required: bool = False
    verification_required: bool = False
    artifacts: List[str] = field(default_factory=list)
    result_summary: str = ""
    parent_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    state_history: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.id = str(self.id).strip() or f"task-{uuid4().hex[:8]}"
        self.title = str(self.title).strip() or self.id
        self.objective = str(self.objective).strip()
        self.state = _normalise_state(self.state)
        self.owner = str(self.owner or "main_agent")
        self.current_step = str(self.current_step or "").strip()
        self.blocked_reason = str(self.blocked_reason or "").strip()
        self.result_summary = str(self.result_summary or "").strip()
        self.parent_id = str(self.parent_id or "").strip()
        self.created_at = str(self.created_at or _now_iso())
        self.updated_at = str(self.updated_at or self.created_at)
        self.steps = [str(step).strip() for step in (self.steps or []) if str(step).strip()]
        self.artifacts = [str(item).strip() for item in (self.artifacts or []) if str(item).strip()]
        self.metadata = dict(self.metadata or {})
        self.state_history = _clone_state_history(self.state_history or [])

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_TASK_STATES

    def update_timestamp(self) -> None:
        self.updated_at = _now_iso()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["is_terminal"] = self.is_terminal
        return payload

    def to_todo_dict(self) -> Dict[str, Any]:
        status = TASK_STATE_TO_TODO_STATUS.get(self.state, "pending")
        return {
            "id": self.id,
            "content": self.title,
            "status": status,
            "task_id": self.id,
            "title": self.title,
            "objective": self.objective,
            "state": self.state,
            "priority": self.priority,
            "owner": self.owner,
            "current_step": self.current_step,
            "steps": list(self.steps),
            "blocked_reason": self.blocked_reason,
            "approval_required": self.approval_required,
            "verification_required": self.verification_required,
            "artifacts": list(self.artifacts),
            "result_summary": self.result_summary,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Task":
        return cls(
            id=payload.get("id", ""),
            title=payload.get("title", payload.get("content", "")),
            objective=payload.get("objective", ""),
            state=payload.get("state", "created"),
            priority=int(payload.get("priority", 5) or 5),
            owner=payload.get("owner", "main_agent"),
            current_step=payload.get("current_step", ""),
            steps=list(payload.get("steps", []) or []),
            blocked_reason=payload.get("blocked_reason", ""),
            approval_required=bool(payload.get("approval_required", False)),
            verification_required=bool(payload.get("verification_required", False)),
            artifacts=list(payload.get("artifacts", []) or []),
            result_summary=payload.get("result_summary", ""),
            parent_id=payload.get("parent_id", ""),
            created_at=payload.get("created_at", ""),
            updated_at=payload.get("updated_at", ""),
            metadata=dict(payload.get("metadata", {}) or {}),
            state_history=list(payload.get("state_history", []) or []),
        )

    @classmethod
    def from_todo(
        cls,
        payload: Dict[str, Any],
        *,
        parent_id: str = "",
        owner: str = "main_agent",
        index: int = 0,
    ) -> "Task":
        todo_id = str(payload.get("task_id") or payload.get("id") or f"step-{index + 1}")
        title = str(payload.get("title") or payload.get("content") or todo_id).strip() or todo_id
        objective = str(payload.get("objective") or title).strip() or title
        status = str(payload.get("status") or payload.get("state") or "pending").lower()
        state = payload.get("state") or TODO_STATUS_TO_TASK_STATE.get(status, "planned")
        if state not in TASK_STATES:
            state = TODO_STATUS_TO_TASK_STATE.get(status, "planned")
        return cls(
            id=todo_id,
            title=title,
            objective=objective,
            state=state,
            priority=int(payload.get("priority", 5) or 5),
            owner=str(payload.get("owner") or owner),
            current_step=str(payload.get("current_step", "")),
            steps=list(payload.get("steps", []) or []),
            blocked_reason=str(payload.get("blocked_reason", "")),
            approval_required=bool(payload.get("approval_required", False)),
            verification_required=bool(payload.get("verification_required", False)),
            artifacts=list(payload.get("artifacts", []) or []),
            result_summary=str(payload.get("result_summary", "")),
            parent_id=str(payload.get("parent_id") or parent_id),
            metadata={
                "source_status": status,
                "source_content": str(payload.get("content", "")),
            },
            state_history=[],
        )


class TaskStore:
    """Session-scoped task store backed by a JSON file."""

    def __init__(self, workspace: Path, session_id: str):
        self.workspace = Path(workspace).resolve()
        self.session_id = str(session_id or "default")
        self._tasks: List[Task] = []
        self._loaded = False

    @property
    def storage_path(self) -> Path:
        return self.workspace / ".nanobot_state" / "tasks" / f"{self.session_id}.json"

    def load(self) -> None:
        if self._loaded:
            return
        path = self.storage_path
        try:
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                tasks = payload.get("tasks", []) if isinstance(payload, dict) else []
                self._tasks = [Task.from_dict(item) for item in tasks if isinstance(item, dict)]
            else:
                self._tasks = []
        except Exception as exc:
            logger.warning("[TaskStore] Failed to load %s: %s", path, exc)
            self._tasks = []
        self._loaded = True

    def save(self) -> None:
        import fcntl
        import time as _time
        path = self.storage_path
        logger.info("[TASK_STORE_SAVE] path=%s, tasks=%d", path, len(self._tasks))
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": self.session_id,
            "workspace": str(self.workspace),
            "updated_at": _now_iso(),
            "tasks": [task.to_dict() for task in self._tasks],
        }
        lock_path = path.with_suffix(path.suffix + ".lock")
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        _MAX_RETRIES = 3
        _RETRY_DELAY_S = 0.2
        global _LOCK_RETRIES, _LOCK_FAILURES, _LOCK_SUCCESSES
        for attempt in range(_MAX_RETRIES):
            try:
                with open(lock_path, "w") as lock_fd:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        tmp_path.write_text(
                            json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        tmp_path.replace(path)
                        _LOCK_SUCCESSES += 1
                        return  # success
                    finally:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except BlockingIOError:
                _LOCK_RETRIES += 1
                # Lock held by another process — retry with backoff
                if attempt < _MAX_RETRIES - 1:
                    _delay = _RETRY_DELAY_S * (attempt + 1)
                    logger.info(
                        "[TaskStore] Lock contention attempt %d/%d, retrying in %.1fs",
                        attempt + 1, _MAX_RETRIES, _delay,
                    )
                    _time.sleep(_delay)
                    continue
                _LOCK_FAILURES += 1
                _emit_lock_metrics()
                logger.critical(
                    "[TaskStore] Lock contention persisted after %d retries — write REFUSED to prevent corruption",
                    _MAX_RETRIES,
                )
                raise
            except OSError as exc:
                _LOCK_FAILURES += 1
                _emit_lock_metrics()
                # Filesystem does not support flock — refuse to write
                logger.critical(
                    "[TaskStore] File lock unavailable (%s) — write REFUSED to prevent data corruption",
                    exc,
                )
                raise

    def clear(self) -> None:
        self._tasks = []
        self._loaded = True
        try:
            if self.storage_path.exists():
                self.storage_path.unlink()
        except Exception as exc:
            logger.debug("[TaskStore] Failed to clear %s: %s", self.storage_path, exc)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def list_tasks(self) -> List[Task]:
        self._ensure_loaded()
        return list(self._tasks)

    def get_task(self, task_id: str) -> Optional[Task]:
        self._ensure_loaded()
        for task in self._tasks:
            if task.id == task_id:
                return task
        return None

    def get_root_tasks(self) -> List[Task]:
        self._ensure_loaded()
        return [task for task in self._tasks if not task.parent_id]

    def get_latest_root_task(self) -> Optional[Task]:
        roots = self.get_root_tasks()
        if not roots:
            return None
        return max(roots, key=lambda task: task.updated_at or task.created_at)

    def get_children(self, parent_id: str) -> List[Task]:
        self._ensure_loaded()
        return [task for task in self._tasks if task.parent_id == parent_id]

    def create_task(self, **kwargs: Any) -> Task:
        self._ensure_loaded()
        task = Task(**kwargs)
        self._tasks.append(task)
        self.save()
        return task

    def upsert_task(self, task: Task) -> Task:
        self._ensure_loaded()
        task.update_timestamp()
        for idx, existing in enumerate(self._tasks):
            if existing.id == task.id:
                self._tasks[idx] = task
                self.save()
                return task
        self._tasks.append(task)
        self.save()
        return task

    def replace_children(self, parent_id: str, tasks: Iterable[Task]) -> List[Task]:
        self._ensure_loaded()
        parent_id = str(parent_id or "").strip()
        children = [Task.from_dict(task.to_dict()) for task in tasks]
        child_ids = {task.id for task in children}
        self._tasks = [task for task in self._tasks if task.parent_id != parent_id or task.id in child_ids]
        for child in children:
            child.parent_id = parent_id
            child.update_timestamp()
            self._tasks = [existing for existing in self._tasks if existing.id != child.id]
            self._tasks.append(child)
        self.save()
        return children

    def transition(self, task_id: str, new_state: str, **updates: Any) -> Task:
        """Public state transition with full validation.
        Respects terminal state protection (completed/failed/cancelled are sticky)."""
        self._ensure_loaded()
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        normalized = _normalise_state(new_state)
        # Terminal state protection: cannot transition out of terminal states
        if task.state in TERMINAL_TASK_STATES and normalized != task.state:
            raise ValueError(f"Cannot transition from terminal state {task.state} -> {normalized}")
        allowed = ALLOWED_TRANSITIONS.get(task.state, set())
        if normalized != task.state and normalized not in allowed:
            raise ValueError(f"Invalid task transition: {task.state} -> {normalized}")
        self._set_state(task, normalized, **updates)
        self.save()
        return task

    def _set_state(self, task: Task, new_state: str, **updates: Any) -> Task:
        """Internal state setter with terminal state protection.
        Use this when you need to bypass transition validation (e.g., for computed states)."""
        normalized = _normalise_state(new_state)
        # Terminal state protection: completed/failed/cancelled are sticky
        if task.state in TERMINAL_TASK_STATES and normalized != task.state:
            logger.debug("[TaskStore] Blocked state change from terminal state %s -> %s for task %s", task.state, normalized, task.id)
            return task

        source = str(updates.pop("source", "") or updates.pop("_source", "") or _infer_transition_source()).strip() or "unknown"
        reason = str(
            updates.pop("reason", "")
            or updates.pop("state_reason", "")
            or updates.get("blocked_reason", "")
            or updates.get("result_summary", "")
            or ""
        ).strip()

        from_state = task.state
        task.state = normalized
        for key, value in updates.items():
            if hasattr(task, key):
                setattr(task, key, value)
        if from_state != normalized:
            task.state_history.append(
                _build_state_change_entry(
                    task,
                    from_state,
                    normalized,
                    source=source,
                    reason=reason,
                )
            )
        task.update_timestamp()
        return task

    def promote_root_state_from_children(self, root_id: str) -> Optional[Task]:
        """Promote a root task state based on its children.

        Promotion rules:
        - Any child in `in_progress` keeps the root in `in_progress`.
        - Any child in `blocked` promotes the root to `blocked`.
        - Any child in `waiting_approval` promotes the root to `waiting_approval`.
        - If all children are `completed` and the root is currently `in_progress`,
          promote to `verifying` so the forced verification gate can run.
        - If all children are `completed` and the root is not currently
          `in_progress`, promote directly to `completed`.
        - Otherwise, fall back to `planned`.
        """
        root = self.get_task(root_id)
        if root is None:
            return None
        # Terminal state protection: don't re-evaluate if root is already terminal
        if root.state in TERMINAL_TASK_STATES:
            return root
        children = self.get_children(root_id)
        if not children:
            return root
        active_child = next((child for child in children if child.state == "in_progress"), None)
        if active_child:
            root.current_step = active_child.title
        elif root.current_step and root.current_step not in {child.title for child in children}:
            root.current_step = children[0].title
        elif not root.current_step:
            root.current_step = children[0].title
        child_states = {child.state for child in children}
        if "in_progress" in child_states:
            next_state = "in_progress"
            next_reason = f"active child task {active_child.id}" if active_child else "active child task in_progress"
        elif "blocked" in child_states:
            next_state = "blocked"
            blocked_child = next((child for child in children if child.state == "blocked"), None)
            next_reason = blocked_child.blocked_reason if blocked_child and blocked_child.blocked_reason else "blocked child task"
        elif "waiting_approval" in child_states:
            next_state = "waiting_approval"
            next_reason = "pending approval in child task"
        elif all(child.state == "completed" for child in children):
            if root.state == "in_progress":
                next_state = "verifying"
                next_reason = "all children completed; verification required"
            else:
                next_state = "completed"
                next_reason = "all children completed"
        else:
            next_state = "planned"
            next_reason = "children exist but no active task"
        if root.state != next_state:
            # Use internal _set_state to bypass transition validation for computed states
            self._set_state(root, next_state, source="promote_root_state_from_children", reason=next_reason)
            self.save()
        return root

    def build_task_tree(self, task_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_loaded()
        root = self.get_task(task_id)
        if root is None:
            return None

        def _build_node(node: Task) -> Dict[str, Any]:
            children = self.get_children(node.id)
            return {
                "task": node.to_dict(),
                "children": [_build_node(child) for child in children],
            }

        return _build_node(root)

    def find_task(self, task_id: str) -> Optional[Task]:
        self._ensure_loaded()
        return self.get_task(task_id)

    def build_context_summary(self, max_children: int = 5) -> str:
        self._ensure_loaded()
        root = self.get_latest_root_task()
        if root is None:
            return ""
        lines = [
            "[TASK CONTEXT]",
            f"Task ID: {root.id}",
            f"Title: {root.title}",
            f"State: {root.state}",
        ]
        if root.objective:
            lines.append(f"Objective: {root.objective}")
        if root.current_step:
            lines.append(f"Current step: {root.current_step}")
        if root.blocked_reason:
            lines.append(f"Blocked reason: {root.blocked_reason}")
        children = self.get_children(root.id)
        if children:
            lines.append("Steps:")
            for child in children[:max_children]:
                status = child.to_todo_dict()["status"]
                lines.append(f"- [{status}] {child.title} (state={child.state}, id={child.id})")
            if len(children) > max_children:
                lines.append(f"- ... and {len(children) - max_children} more step(s)")
        return "\n".join(lines)

    def build_done_summary(self) -> Dict[str, Any]:
        self._ensure_loaded()
        root = self.get_latest_root_task()
        if root is None:
            return {"found": False, "tasks": []}
        children = self.get_children(root.id)
        return {
            "found": True,
            "root_task": root.to_dict(),
            "root_todo": root.to_todo_dict(),
            "tasks": [task.to_dict() for task in children],
            "todos": [task.to_todo_dict() for task in children],
        }


def ensure_root_task_for_message(
    workspace: Path,
    session_id: str,
    user_message: str,
    *,
    owner: str = "main_agent",
    priority: int = 5,
    metadata: Optional[Dict[str, Any]] = None,
    force_new: bool = False,
) -> Task:
    """Create or reuse the current root task for a user intent.

    Root Task Reuse Strategy:
        If the current session has an existing root task in a non-terminal state
        (i.e., not completed, failed, or cancelled), this function reuses that
        task by default. This supports multi-turn conversations to complete a
        single task without creating a new root task on each turn.

        If the user's new intent represents a completely different topic or
        task, the caller should either:
        1. Explicitly end the current task (transition to terminal state), or
        2. Pass force_new=True to create a new root task regardless of existing state.

        Future enhancement: Intent-switching detection logic could be added here
        to automatically determine when a new root task should be created based
        on semantic analysis of user_message vs. the current task objective.

    Phase 1 keeps root-task creation conservative: reuse the latest active
    root task when possible, but create a new one when there is no active
    task or the previous root task is terminal.
    """
    store = get_task_store(session_id, workspace)
    root = store.get_latest_root_task()
    if root is not None and not root.is_terminal and not force_new:
        return root

    title = _derive_task_title(user_message)
    objective = re.sub(r"\s+", " ", str(user_message or "")).strip()[:400]
    return store.create_task(
        id=f"root-{uuid4().hex[:12]}",
        title=title,
        objective=objective or title,
        state="created",
        owner=owner,
        priority=priority,
        metadata=metadata or {},
    )


def _cache_key(session_id: str, workspace: Path) -> Tuple[str, str]:
    return (str(Path(workspace).resolve()), str(session_id or "default"))


def set_task_context(session_id: str, workspace: Path) -> None:
    """Remember the current session workspace for convenience helpers."""
    global _CURRENT_SESSION_ID, _CURRENT_WORKSPACE
    _CURRENT_SESSION_ID = str(session_id or "default")
    _CURRENT_WORKSPACE = Path(workspace).resolve()
    get_task_store(_CURRENT_SESSION_ID, _CURRENT_WORKSPACE)


def get_task_store(session_id: Optional[str] = None, workspace: Optional[Path] = None) -> TaskStore:
    sid = str(session_id or _CURRENT_SESSION_ID or "default")
    ws = Path(workspace or _CURRENT_WORKSPACE or Path.cwd()).resolve()
    key = _cache_key(sid, ws)
    store = _STORE_CACHE.get(key)
    if store is None:
        store = TaskStore(ws, sid)
        _STORE_CACHE[key] = store
    return store


def reset_session_tasks(session_id: Optional[str] = None, workspace: Optional[Path] = None) -> None:
    """Clear cached and persisted tasks for a session."""
    sid = str(session_id or _CURRENT_SESSION_ID or "default")
    ws = Path(workspace or _CURRENT_WORKSPACE or Path.cwd()).resolve()
    key = _cache_key(sid, ws)
    store = _STORE_CACHE.pop(key, None)
    if store is None:
        store = TaskStore(ws, sid)
    store.clear()


def create_session_root_task(
    workspace: Path,
    session_id: str,
    *,
    title: str,
    objective: str = "",
    owner: str = "main_agent",
    priority: int = 5,
    metadata: Optional[Dict[str, Any]] = None,
) -> Task:
    """Create a new root task for the current user intent."""
    store = get_task_store(session_id, workspace)
    root = store.create_task(
        id=f"root-{uuid4().hex[:12]}",
        title=title,
        objective=objective or title,
        state="created",
        owner=owner,
        priority=priority,
        metadata=metadata or {},
    )
    return root


def create_child_task(
    workspace: Path,
    session_id: str,
    parent_id: str,
    *,
    title: str,
    objective: str = "",
    owner: str = "sub_agent",
    priority: int = 5,
    state: str = "created",
    metadata: Optional[Dict[str, Any]] = None,
) -> Task:
    """Create a child task under an existing parent task.

    This is used by sub-agents and verification flows to make their work
    visible in the task tree and to preserve task state transitions through
    the TaskStore state machine.
    """
    store = get_task_store(session_id, workspace)
    parent = store.get_task(parent_id)
    if parent is None:
        raise KeyError(f"Parent task not found: {parent_id}")

    child_title = str(title or "").strip() or _derive_task_title(objective or parent.title)
    child_objective = str(objective or child_title).strip() or child_title
    child = store.create_task(
        id=f"{parent_id}-child-{uuid4().hex[:12]}",
        title=child_title,
        objective=child_objective,
        state=state,
        owner=owner,
        priority=priority,
        parent_id=parent_id,
        metadata=metadata or {},
    )
    return child


def find_task_record(task_id: str, workspace: Optional[Path] = None) -> Optional[Tuple[TaskStore, Task]]:
    """Find a task across cached stores and persisted session files.

    Returns the TaskStore and Task so callers can inspect the surrounding tree.
    """
    task_id = str(task_id or "").strip()
    if not task_id:
        return None

    seen: set[Tuple[str, str]] = set()

    def _scan_store(store: TaskStore) -> Optional[Tuple[TaskStore, Task]]:
        key = (str(store.workspace.resolve()), store.session_id)
        if key in seen:
            return None
        seen.add(key)
        task = store.get_task(task_id)
        if task is not None:
            return store, task
        return None

    for store in list(_STORE_CACHE.values()):
        found = _scan_store(store)
        if found is not None:
            return found

    if workspace is not None:
        ws = Path(workspace).resolve()
        tasks_dir = ws / ".nanobot_state" / "tasks"
        if tasks_dir.is_dir():
            for task_file in tasks_dir.glob("*.json"):
                session_id = task_file.stem
                found = _scan_store(get_task_store(session_id, ws))
                if found is not None:
                    return found

    return None


def get_current_task_summary(session_id: Optional[str] = None, workspace: Optional[Path] = None) -> str:
    store = get_task_store(session_id, workspace)
    return store.build_context_summary()


def coerce_todo_payloads(payloads: Iterable[Dict[str, Any]], parent_id: str) -> List[Task]:
    tasks: List[Task] = []
    for index, payload in enumerate(payloads):
        if not isinstance(payload, dict):
            continue
        tasks.append(Task.from_todo(payload, parent_id=parent_id, index=index))
    return tasks


def promote_root_from_children(session_id: Optional[str] = None, workspace: Optional[Path] = None) -> Optional[Task]:
    store = get_task_store(session_id, workspace)
    root = store.get_latest_root_task()
    if root is None:
        return None
    return store.promote_root_state_from_children(root.id)
