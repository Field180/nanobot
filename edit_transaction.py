import json
import logging
import os
import tempfile
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_DEFAULT_STORE_DIR = Path.home() / ".nanobot" / "change_sets"
_DEFAULT_PENDING_TTL_SECONDS = 24 * 60 * 60
_DEFAULT_MAX_PENDING = 50
_GLOBAL_SESSION_ID = "global"
_STORE_LOCK = threading.RLock()
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: Dict[str, threading.RLock] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store_dir() -> Path:
    base = os.environ.get("NANOBOT_CHANGESET_DIR", "")
    store = Path(base) if base else _DEFAULT_STORE_DIR
    store.mkdir(parents=True, exist_ok=True)
    return store


def _store_path(change_set_id: str) -> Path:
    return _store_dir() / f"{change_set_id}.json"


def _normalize_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _diff_labels(path: str, before_exists: bool, after_exists: bool) -> tuple[str, str]:
    normalized = Path(path).as_posix()
    before_label = f"a/{normalized}" if before_exists else "/dev/null"
    after_label = f"b/{normalized}" if after_exists else "/dev/null"
    return before_label, after_label


def build_unified_diff(
    before_text: str,
    after_text: str,
    path: str,
    before_exists: bool = True,
    after_exists: bool = True,
) -> str:
    import difflib

    before_label, after_label = _diff_labels(path, before_exists, after_exists)
    return "\n".join(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=before_label,
            tofile=after_label,
            lineterm="",
        )
    )


def _normalize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("plan must be a dict")
    if not plan.get("path"):
        raise ValueError("plan.path is required")
    if "after_text" not in plan:
        raise ValueError("plan.after_text is required")
    normalized = deepcopy(plan)
    normalized["path"] = _normalize_path(str(normalized["path"]))
    normalized["before_text"] = normalized.get("before_text", "") or ""
    normalized["after_text"] = normalized.get("after_text", "") or ""
    normalized["before_exists"] = bool(normalized.get("before_exists", False))
    normalized["after_exists"] = bool(normalized.get("after_exists", True))
    normalized["created"] = bool(normalized.get("created", False))
    normalized["tool_name"] = str(normalized.get("tool_name", "file_edit") or "file_edit")
    normalized["summary"] = str(normalized.get("summary", "") or "")
    normalized["diff"] = normalized.get("diff") or build_unified_diff(
        normalized["before_text"],
        normalized["after_text"],
        normalized["path"],
        normalized["before_exists"],
        normalized["after_exists"],
    )
    return normalized


def _save_change_set(change_set: Dict[str, Any]) -> None:
    path = _store_path(change_set["id"])
    payload = json.dumps(change_set, ensure_ascii=False, indent=2)
    with _STORE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
            tmp.write(payload)
            temp_path = Path(tmp.name)
        os.replace(temp_path, path)


def _load_change_set(change_set_id: str) -> Dict[str, Any]:
    path = _store_path(change_set_id)
    if not path.exists():
        raise FileNotFoundError(f"Change set not found: {change_set_id}")
    with _STORE_LOCK:
        return json.loads(path.read_text(encoding="utf-8"))


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except Exception:
        return default
    return max(0, value)


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pending_ttl() -> timedelta:
    return timedelta(seconds=_env_int("NANOBOT_CHANGESET_TTL_SECONDS", _DEFAULT_PENDING_TTL_SECONDS))


def _max_pending_change_sets() -> int:
    return _env_int("NANOBOT_CHANGESET_MAX_PENDING", _DEFAULT_MAX_PENDING)


def _discard_change_set(path: Path, reason: str) -> None:
    """Mark a pending change set as 'discarded' instead of deleting it.

    This preserves the change set on disk so that task_constraint_recovery can
    distinguish between user-rejected and silently-discarded change sets.
    """
    try:
        change_set = json.loads(path.read_text(encoding="utf-8"))
        change_set["status"] = "discarded"
        change_set["updated_at"] = _utc_now()
        change_set["discard_reason"] = reason
        payload = json.dumps(change_set, ensure_ascii=False, indent=2)
        with _STORE_LOCK:
            temp_path = str(path) + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(temp_path, path)
        logger.info(
            "[EditTx] Change set %s marked as discarded: %s",
            change_set.get("id", "?"), reason,
        )
    except Exception as exc:
        logger.debug("[EditTx] Failed to mark change set as discarded: %s", exc)
        # Fallback: delete as before to avoid blocking
        try:
            path.unlink()
        except Exception:
            pass


def _cleanup_pending_change_sets() -> None:
    now = datetime.now(timezone.utc)
    ttl = _pending_ttl()
    max_pending = _max_pending_change_sets()
    pending_items: List[tuple[datetime, Path]] = []
    with _STORE_LOCK:
        for path in _store_dir().glob("cs_*.json"):
            try:
                change_set = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if change_set.get("status") != "pending":
                continue
            created_at = _parse_timestamp(change_set.get("created_at")) or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if ttl.total_seconds() > 0 and now - created_at > ttl:
                # Mark as discarded instead of deleting — preserves audit trail
                _discard_change_set(path, f"TTL expired ({ttl.total_seconds():.0f}s)")
                continue
            pending_items.append((created_at, path))
        if max_pending and len(pending_items) > max_pending:
            for _, extra_path in sorted(pending_items, key=lambda item: item[0])[:-max_pending]:
                _discard_change_set(extra_path, f"max pending overflow ({max_pending})")


def _list_pending_change_sets_no_cleanup(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List pending change sets WITHOUT triggering TTL/max cleanup.

    Used by the pending-guard in create_pending_change_set so that the
    existence check doesn't itself cause cleanup that could discard the
    very change sets we're trying to protect.
    """
    pending: List[Dict[str, Any]] = []
    for path in sorted(_store_dir().glob("cs_*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            with _STORE_LOCK:
                change_set = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if change_set.get("status") != "pending":
            continue
        if session_id and change_set.get("session_id") != session_id:
            continue
        pending.append(change_set)
    return pending


def _get_path_lock(path: str) -> threading.RLock:
    normalized = _normalize_path(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(normalized)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[normalized] = lock
        return lock


def _read_disk_state(path: Path) -> tuple[bool, str, str]:
    if not path.exists():
        return False, "", ""
    if not path.is_file():
        return False, "", f"Path is not a file: {path}"
    try:
        return True, path.read_text(encoding="utf-8", errors="replace"), ""
    except Exception as exc:
        return False, "", f"Read error for {path}: {exc}"


def _snapshot_matches(path: Path, expected_exists: bool, expected_text: str, reason: str) -> str:
    exists, text, error = _read_disk_state(path)
    if error:
        return error
    if exists != expected_exists:
        if expected_exists:
            return f"File changed since {reason}: expected existing file at {path}."
        return f"File changed since {reason}: expected {path} to be absent."
    if exists and text != expected_text:
        return f"File changed since {reason}: {path}. This change set is stale."
    return ""


def _try_rebase_edit(file_entry: Dict[str, Any], conflict_msg: str) -> bool:
    """Try to rebase a file_edit onto the current disk state.

    When a sibling change_set was already applied to the same file, the
    before_text snapshot is stale. This function checks if the edit can
    still be applied to the current file content by computing the diff
    between before_text and after_text, then applying that diff to the
    current file.

    Returns True if rebase succeeded (file_entry updated in-place).
    """
    path = Path(file_entry["path"])
    before_text = file_entry.get("before_text", "") or ""
    after_text = file_entry.get("after_text", "") or ""

    # Read current file content
    exists, current_text, error = _read_disk_state(path)
    if error or not exists:
        logger.debug(f"[TxRebase] Cannot rebase: file read error or missing: {error}")
        return False

    # If before_text == current_text, no rebase needed (shouldn't happen here)
    if before_text == current_text:
        return True

    # Compute the diff between before and after (what this edit wants to change)
    import difflib
    before_lines = before_text.splitlines(keepends=True)
    after_lines = after_text.splitlines(keepends=True)
    current_lines = current_text.splitlines(keepends=True)

    # Apply the diff to current content using a simple approach:
    # Find the changed regions in before→after, and apply them to current
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
    patches = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        # This is a changed region: before[i1:i2] should become after[j1:j2]
        old_chunk = before_lines[i1:i2]
        new_chunk = after_lines[j1:j2]
        patches.append((old_chunk, new_chunk, i1, i2))

    if not patches:
        # No actual changes in this edit — just update snapshot
        file_entry["before_text"] = current_text
        file_entry["after_text"] = current_text
        return True

    # Apply patches to current content
    result_lines = list(current_lines)
    applied_any = False
    skipped_already_applied = 0
    for old_chunk, new_chunk, orig_i1, orig_i2 in patches:
        # Find where old_chunk appears in current content
        old_text = ''.join(old_chunk)
        current_joined = ''.join(result_lines)

        # Find the old chunk in current file
        pos = current_joined.find(old_text)
        if pos == -1:
            # Check if the NEW text is already in the current file
            # (meaning this hunk was already applied by a sibling change_set)
            new_text = ''.join(new_chunk)
            if new_text in current_joined:
                # Already applied — skip this hunk
                skipped_already_applied += 1
                continue
            # True conflict: neither old nor new text found
            logger.debug(f"[TxRebase] Cannot rebase: old text not found and new text not present")
            return False

        # Convert character position to line position
        char_count = 0
        line_idx = 0
        for line_idx, line in enumerate(result_lines):
            if char_count + len(line) > pos:
                break
            char_count += len(line)
        else:
            line_idx = len(result_lines)

        # Count lines in old_chunk
        old_chunk_lines = len(old_chunk)
        start_line = line_idx
        end_line = start_line + old_chunk_lines

        # Verify the match
        actual_chunk = ''.join(result_lines[start_line:end_line])
        if actual_chunk != old_text:
            logger.debug(f"[TxRebase] Chunk verification failed")
            return False

        # Replace
        result_lines[start_line:end_line] = new_chunk
        applied_any = True

    if not applied_any and skipped_already_applied > 0:
        # All hunks were already applied by sibling change_sets
        logger.info(f"[TxRebase] All {skipped_already_applied} hunks already applied, marking as current")
        file_entry["before_text"] = current_text
        file_entry["after_text"] = current_text
        return True

    # Update file_entry with rebased content
    new_after_text = ''.join(result_lines)
    file_entry["before_text"] = current_text
    file_entry["after_text"] = new_after_text
    file_entry["diff"] = build_unified_diff(
        current_text, new_after_text, str(path),
        bool(file_entry.get("before_exists", False)),
        bool(file_entry.get("after_exists", True)),
    )
    logger.info(f"[TxRebase] Successfully rebased edit for {path} ({applied_any} hunks applied, {skipped_already_applied} skipped as already-done)")
    return True


def _make_temp_path(parent: Path, suffix: str) -> Path:
    fd, temp_name = tempfile.mkstemp(prefix=".nanobot_tx_", suffix=suffix, dir=parent)
    os.close(fd)
    return Path(temp_name)


def _prepare_staged_write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _make_temp_path(path.parent, ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    return temp_path


def _refresh_file_state(path: Path) -> None:
    try:
        if path.exists():
            from tools.base import update_file_state_after_edit

            update_file_state_after_edit(path)
    except Exception:
        pass
    try:
        from tools.file_read import invalidate_session_reads

        invalidate_session_reads(path)
    except Exception:
        pass


def _build_file_ops(change_set: Dict[str, Any], target: str) -> List[Dict[str, Any]]:
    file_ops: List[Dict[str, Any]] = []
    for file_entry in change_set.get("files", []):
        if target == "after":
            desired_exists = bool(file_entry.get("after_exists", True))
            desired_text = file_entry.get("after_text", "") or ""
        else:
            desired_exists = bool(file_entry.get("before_exists", False))
            desired_text = file_entry.get("before_text", "") or ""
        file_ops.append({
            "path": Path(file_entry["path"]),
            "desired_exists": desired_exists,
            "desired_text": desired_text,
        })
    return file_ops


def _commit_file_operations(file_ops: List[Dict[str, Any]]) -> tuple[bool, str]:
    processed: List[Dict[str, Any]] = []
    staged_paths: List[Path] = []
    try:
        for op in file_ops:
            if op["desired_exists"]:
                staged_path = _prepare_staged_write(op["path"], op["desired_text"])
                op["staged_path"] = staged_path
                staged_paths.append(staged_path)
        for op in file_ops:
            path = op["path"]
            backup_path = None
            original_exists = path.exists()
            if original_exists:
                if not path.is_file():
                    raise OSError(f"Cannot modify non-file path: {path}")
                backup_path = _make_temp_path(path.parent, ".bak")
                if backup_path.exists():
                    backup_path.unlink()
                os.replace(path, backup_path)
            op["backup_path"] = backup_path
            op["original_exists"] = original_exists
            processed.append(op)
            if op["desired_exists"]:
                os.replace(op["staged_path"], path)
        for op in processed:
            backup_path = op.get("backup_path")
            if backup_path and Path(backup_path).exists():
                Path(backup_path).unlink()
            _refresh_file_state(op["path"])
        return True, ""
    except Exception as exc:
        for op in reversed(processed):
            path = op["path"]
            backup_path = op.get("backup_path")
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except Exception:
                pass
            try:
                if backup_path and Path(backup_path).exists():
                    os.replace(backup_path, path)
            except Exception:
                pass
            _refresh_file_state(path)
        return False, f"Transaction apply failed: {exc}"
    finally:
        for staged_path in staged_paths:
            try:
                if staged_path.exists():
                    staged_path.unlink()
            except Exception:
                pass
        for op in file_ops:
            backup_path = op.get("backup_path")
            try:
                if backup_path and Path(backup_path).exists():
                    Path(backup_path).unlink()
            except Exception:
                pass


def _public_file_entry(file_entry: Dict[str, Any]) -> Dict[str, Any]:
    public = deepcopy(file_entry)
    public.pop("before_text", None)
    public.pop("after_text", None)
    return public


def sanitize_change_set(change_set: Dict[str, Any]) -> Dict[str, Any]:
    public = deepcopy(change_set)
    public["files"] = [_public_file_entry(item) for item in change_set.get("files", [])]
    return public


def _build_rollback_conflict_change_set(change_set: Dict[str, Any]) -> Dict[str, Any]:
    review_plans: List[Dict[str, Any]] = []
    for file_entry in change_set.get("files", []):
        path = Path(file_entry["path"])
        current_exists, current_text, _ = _read_disk_state(path)
        target_exists = bool(file_entry.get("before_exists", False))
        target_text = file_entry.get("before_text", "") or ""
        review_plans.append({
            "path": str(path),
            "before_text": current_text if current_exists else "",
            "after_text": target_text,
            "before_exists": current_exists,
            "after_exists": target_exists,
            "created": not current_exists and target_exists,
            "tool_name": "rollback_review",
            "summary": f"Review rollback for {path.name}",
            "diff": build_unified_diff(
                current_text if current_exists else "",
                target_text,
                str(path),
                before_exists=current_exists,
                after_exists=target_exists,
            ),
        })
    return create_pending_change_set(
        review_plans,
        session_id=change_set.get("session_id") or _GLOBAL_SESSION_ID,
        source="rollback_review",
    )


def cleanup_orphaned_tempfiles(workspace: Path | str) -> Dict[str, Any]:
    root = Path(workspace).expanduser().resolve()
    removed: List[str] = []
    failed: List[str] = []
    skipped: List[str] = []
    if not root.exists():
        return {"success": True, "removed": removed, "failed": failed, "skipped": skipped}
    active_parent_dirs: set[str] = set()
    for change_path in sorted(_store_dir().glob("cs_*.json")):
        try:
            with _STORE_LOCK:
                change_set = json.loads(change_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if change_set.get("status") != "pending":
            continue
        for file_entry in change_set.get("files", []):
            try:
                active_parent_dirs.add(str(Path(file_entry.get("path", "")).expanduser().resolve().parent))
            except Exception:
                continue
    for pattern in (".nanobot_tx_*.tmp", ".nanobot_tx_*.bak"):
        for path in root.rglob(pattern):
            try:
                if not path.is_file():
                    continue
                if str(path.parent.resolve()) in active_parent_dirs:
                    skipped.append(str(path))
                    continue
                path.unlink()
                removed.append(str(path))
            except Exception as exc:
                failed.append(f"{path}: {exc}")
    return {"success": len(failed) == 0, "removed": removed, "failed": failed, "skipped": skipped}


def create_pending_change_set(
    plans: Dict[str, Any] | List[Dict[str, Any]],
    session_id: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    _cleanup_pending_change_sets()
    plan_list = plans if isinstance(plans, list) else [plans]
    normalized_files = [_normalize_plan(plan) for plan in plan_list]
    resolved_session_id = session_id or normalized_files[0].get("session_id") or _GLOBAL_SESSION_ID
    is_rollback_review = (source == "rollback_review" or normalized_files[0].get("tool_name") == "rollback_review")

    # ── Guard: block new edits when un-reviewed pending change sets exist ──
    # Rollback_review change sets are exempt (system-created conflict resolution).
    # This prevents the agent from silently overwriting/discarding old pending
    # change sets that the user hasn't reviewed yet.
    if not is_rollback_review:
        existing_pending = _list_pending_change_sets_no_cleanup(session_id=resolved_session_id)
        if existing_pending:
            existing_ids = [cs.get("id", "?") for cs in existing_pending]
            existing_files = []
            for cs in existing_pending:
                for f in cs.get("files", []):
                    existing_files.append(f.get("path", "?"))
            raise ValueError(
                f"Cannot create new change set: {len(existing_pending)} pending change set(s) "
                f"already await approval for session {resolved_session_id}: {existing_ids}. "
                f"Files: {existing_files}. "
                f"The user must accept or reject them first."
            )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    change_set_id = f"cs_{stamp}_{uuid.uuid4().hex[:8]}"
    change_set = {
        "id": change_set_id,
        "session_id": resolved_session_id,
        "source": source or normalized_files[0].get("tool_name") or "file_edit",
        "type": "rollback_review" if is_rollback_review else "direct",
        "status": "pending",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "files": normalized_files,
    }
    _save_change_set(change_set)
    return sanitize_change_set(change_set)


def accept_change_set(change_set_id: str) -> Dict[str, Any]:
    try:
        change_set = _load_change_set(change_set_id)
    except FileNotFoundError:
        return {"success": False, "error": "Change set not found"}
    if change_set.get("status") == "rejected":
        return {"success": False, "error": f"Change set {change_set_id} was rejected and cannot be accepted.", "change_set": sanitize_change_set(change_set)}
    if change_set.get("status") == "reverted":
        return {"success": False, "error": f"Change set {change_set_id} was already reverted.", "change_set": sanitize_change_set(change_set)}
    if change_set.get("status") == "applied":
        return {"success": True, "change_set": sanitize_change_set(change_set)}

    file_entries = change_set.get("files", [])
    locks = [_get_path_lock(item["path"]) for item in sorted(file_entries, key=lambda entry: entry["path"])]
    for lock in locks:
        lock.acquire()
    try:
        # Re-check status after acquiring locks to close TOCTOU window
        # (another thread may have applied this CS while we waited for locks)
        try:
            change_set = _load_change_set(change_set_id)
        except FileNotFoundError:
            logger.warning("[TxAccept] Change set %s was removed during lock acquisition", change_set_id)
            return {"success": False, "error": "Change set is no longer available"}
        if change_set.get("status") == "applied":
            return {"success": True, "change_set": sanitize_change_set(change_set)}
        for file_entry in file_entries:
            conflict = _snapshot_matches(
                Path(file_entry["path"]),
                bool(file_entry.get("before_exists", False)),
                file_entry.get("before_text", "") or "",
                "change set creation",
            )
            if conflict:
                # Snapshot mismatch — likely a sibling change_set was already applied.
                # Try to rebase: if the edit (before→after diff) can still apply to
                # the current file content, update the snapshot and proceed.
                rebased = _try_rebase_edit(file_entry, conflict)
                if rebased:
                    logger.info(f"[TxRebase] Rebased change_set {change_set_id} onto current file state")
                else:
                    change_set["last_error"] = conflict
                    change_set["updated_at"] = _utc_now()
                    _save_change_set(change_set)
                    return {"success": False, "error": conflict, "change_set": sanitize_change_set(change_set)}
        ok, error = _commit_file_operations(_build_file_ops(change_set, "after"))
        if not ok:
            change_set["last_error"] = error
            change_set["updated_at"] = _utc_now()
            _save_change_set(change_set)
            return {"success": False, "error": error, "change_set": sanitize_change_set(change_set)}
    finally:
        for lock in reversed(locks):
            lock.release()

    change_set["status"] = "applied"
    change_set["updated_at"] = _utc_now()
    change_set["applied_at"] = _utc_now()
    change_set.pop("last_error", None)
    _save_change_set(change_set)
    return {"success": True, "change_set": sanitize_change_set(change_set)}


def reject_change_set(change_set_id: str) -> Dict[str, Any]:
    try:
        change_set = _load_change_set(change_set_id)
    except FileNotFoundError:
        return {"success": False, "error": "Change set not found"}
    status = change_set.get("status")
    if status == "rejected":
        return {"success": True, "change_set": sanitize_change_set(change_set)}
    if status == "reverted":
        return {"success": True, "change_set": sanitize_change_set(change_set)}
    if status != "applied":
        change_set["status"] = "rejected"
        change_set["updated_at"] = _utc_now()
        change_set["rejected_at"] = _utc_now()
        change_set.pop("last_error", None)
        _save_change_set(change_set)
        return {"success": True, "change_set": sanitize_change_set(change_set)}

    file_entries = change_set.get("files", [])
    locks = [_get_path_lock(item["path"]) for item in sorted(file_entries, key=lambda entry: entry["path"])]
    for lock in locks:
        lock.acquire()
    try:
        for file_entry in file_entries:
            conflict = _snapshot_matches(
                Path(file_entry["path"]),
                bool(file_entry.get("after_exists", True)),
                file_entry.get("after_text", "") or "",
                "change set application",
            )
            if conflict:
                review_change_set = _build_rollback_conflict_change_set(change_set)
                change_set["last_error"] = conflict
                change_set["updated_at"] = _utc_now()
                change_set["rollback_review_change_set_id"] = review_change_set["id"]
                _save_change_set(change_set)
                return {
                    "success": False,
                    "error": f"{conflict} A rollback review change set has been created.",
                    "change_set": sanitize_change_set(change_set),
                    "review_change_set": review_change_set,
                }
        ok, error = _commit_file_operations(_build_file_ops(change_set, "before"))
        if not ok:
            change_set["last_error"] = error
            change_set["updated_at"] = _utc_now()
            _save_change_set(change_set)
            return {"success": False, "error": error, "change_set": sanitize_change_set(change_set)}
    finally:
        for lock in reversed(locks):
            lock.release()

    change_set["status"] = "reverted"
    change_set["updated_at"] = _utc_now()
    change_set["reverted_at"] = _utc_now()
    change_set.pop("last_error", None)
    _save_change_set(change_set)
    return {"success": True, "change_set": sanitize_change_set(change_set)}


def list_pending_change_sets(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    _cleanup_pending_change_sets()
    pending: List[Dict[str, Any]] = []
    for path in sorted(_store_dir().glob("cs_*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            with _STORE_LOCK:
                change_set = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if change_set.get("status") != "pending":
            continue
        if session_id and change_set.get("session_id") != session_id:
            continue
        pending.append(sanitize_change_set(change_set))
    return pending


def list_discarded_change_sets(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List change sets that were silently discarded by cleanup (TTL or overflow).

    Used by task_constraint_recovery to distinguish between:
    - User explicitly rejected → legitimate empty state → allow recovery
    - Silently discarded by TTL/overflow → suspicious empty state → block recovery
    """
    discarded: List[Dict[str, Any]] = []
    for path in sorted(_store_dir().glob("cs_*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            with _STORE_LOCK:
                change_set = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if change_set.get("status") != "discarded":
            continue
        if session_id and change_set.get("session_id") != session_id:
            continue
        discarded.append(sanitize_change_set(change_set))
    return discarded
