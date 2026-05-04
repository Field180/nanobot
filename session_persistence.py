"""
Session Persistence Module (P9 extraction from server_final.py)
================================================================
File-backed session storage: load / save / touch.
Pure functions that receive configuration via module-level init().
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------- module-level state (injected by init()) ----------
_SESSION_STORE_DIR: Path | None = None
_SESSION_ACTIVITY: dict | None = None          # shared ref from server_final
_SESSION_TIMEOUT_CONFIG: dict | None = None    # shared ref from server_final


def init(
    store_dir: Path,
    activity_dict: dict,
    timeout_config: dict,
) -> None:
    """Call once at startup to wire the module to server globals."""
    global _SESSION_STORE_DIR, _SESSION_ACTIVITY, _SESSION_TIMEOUT_CONFIG
    _SESSION_STORE_DIR = store_dir
    _SESSION_ACTIVITY = activity_dict
    _SESSION_TIMEOUT_CONFIG = timeout_config


# ---------- helpers ----------

def _ensure_initialized() -> None:
    """Auto-init with defaults if init() was never called (test / CLI fallback)."""
    global _SESSION_STORE_DIR, _SESSION_ACTIVITY, _SESSION_TIMEOUT_CONFIG
    if _SESSION_STORE_DIR is not None:
        return
    _SESSION_STORE_DIR = Path.home() / ".nanobot" / "workspace" / "sessions"
    _SESSION_STORE_DIR.mkdir(parents=True, exist_ok=True)
    if _SESSION_ACTIVITY is None:
        _SESSION_ACTIVITY = {}
    if _SESSION_TIMEOUT_CONFIG is None:
        _SESSION_TIMEOUT_CONFIG = {"enabled": True, "default_timeout_minutes": 30}
    logger.warning("[SessionPersist] init() was not called — using default path: %s", _SESSION_STORE_DIR)


def session_store_path(session_id: str) -> Path:
    _ensure_initialized()
    return _SESSION_STORE_DIR / f"web_ui_{session_id}.json"


def _persist_session_payload(session_id: str, payload: dict) -> None:
    import fcntl
    path = session_store_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(lock_path, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                tmp_path.write_text(
                    json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8",
                )
                tmp_path.replace(path)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except BlockingIOError:
        # Lock held by another writer — fall back to direct write
        # to avoid data loss; concurrent writes are rare in practice
        logger.warning("[SessionPersist] Lock contention for session=%s, falling back to direct write", session_id)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.error("[SessionPersist] File lock unavailable (%s) for session=%s, falling back to direct write", exc, session_id)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _parse_session_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except Exception:
            return None
    try:
        text = str(value).strip()
        if not text:
            return None
        return datetime.fromisoformat(text)
    except Exception:
        return None


# ---------- public API ----------

def touch_session_activity(session_id: str, *, persist: bool = True) -> datetime:
    _ensure_initialized()
    now = datetime.now()
    _SESSION_ACTIVITY[session_id] = now
    if not persist:
        return now

    try:
        payload = load_session_payload(session_id)
        if payload:
            payload = dict(payload)
            payload.setdefault("session_id", session_id)
            payload.setdefault("created_at", now.isoformat())
            payload["last_activity"] = now.isoformat()
            timeout_minutes = int(_SESSION_TIMEOUT_CONFIG.get("default_timeout_minutes", 30))
            payload["expires_at"] = (now + timedelta(minutes=timeout_minutes)).isoformat()
            _persist_session_payload(session_id, payload)
    except Exception as exc:
        logger.debug(f"刷新会话活动失败: session={session_id}, error={exc}")
    return now


def load_session_payload(session_id: str) -> dict:
    try:
        path = session_store_path(session_id)
        if not path.exists():
            return {}
        payload = json.loads(path.read_text())
        if isinstance(payload, dict):
            last_activity = _parse_session_timestamp(payload.get("last_activity") or payload.get("saved_at") or payload.get("created_at"))
            if last_activity:
                _SESSION_ACTIVITY.setdefault(session_id, last_activity)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        logger.error(f"读取会话失败: session={session_id}, error={exc}")
        return {}


def save_session_payload(session_id: str, payload: dict) -> None:
    try:
        payload = dict(payload)
        now = datetime.now()
        payload.setdefault("session_id", session_id)
        payload.setdefault("created_at", now.isoformat())
        payload["last_activity"] = now.isoformat()
        timeout_minutes = int(_SESSION_TIMEOUT_CONFIG.get("default_timeout_minutes", 30))
        payload["expires_at"] = (now + timedelta(minutes=timeout_minutes)).isoformat()
        _SESSION_ACTIVITY[session_id] = now
        _persist_session_payload(session_id, payload)
    except Exception as exc:
        logger.error(f"保存会话失败: session={session_id}, error={exc}")


def save_session_history(session_id: str, history: list) -> None:
    try:
        payload = load_session_payload(session_id)
        payload.update({
            "session_id": session_id,
            "history": history,
            "saved_at": datetime.now().isoformat()
        })
        save_session_payload(session_id, payload)
    except Exception as exc:
        logger.error(f"保存会话失败: session={session_id}, error={exc}")


def load_session_history(session_id: str) -> list | None:
    try:
        payload = load_session_payload(session_id)
        if payload:
            touch_session_activity(session_id, persist=True)
        return payload.get("history")
    except Exception as exc:
        logger.error(f"读取会话失败: session={session_id}, error={exc}")
        return None
