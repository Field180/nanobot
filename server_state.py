"""
Server State Module (P0-1 extraction from server_final.py)

Shared constants and mutable state used by both server_final.py and route modules.
Avoids circular imports by centralizing state that multiple modules need.

Async Safety Contract
---------------------
FastAPI route handlers run as coroutines on a **single-threaded** asyncio event
loop.  Coroutines yield control only at ``await`` points.  Therefore:

* A sequence of *synchronous* dict operations with **no** ``await`` in between
  is inherently atomic — no other coroutine can interleave.
* If a compound operation (check-then-act, read-modify-write) spans an
  ``await``, it **must** be wrapped with the appropriate ``asyncio.Lock``
  (``sessions_lock`` / ``stream_processes_lock``).
* Prefer the ``SessionStore`` methods (``session_store.get_or_create``, etc.)
  which hold the lock internally.
* **Never** perform blocking I/O while holding a lock — use ``await`` with
  the lock acquired only for fast in-memory operations.
"""
import asyncio
import logging
import os
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Optional

# ── Core constants ──────────────────────────────────────────
APP_DIR = Path(__file__).parent
WORKSPACE = Path("/home/field/.nanobot/workspace")
VENV = Path("/home/field/nanobotProjects/nanobot/.venv")
PYTHON = str(VENV / "bin" / "python3")

SESSION_STORE_DIR = WORKSPACE / "sessions"
SESSION_STORE_DIR.mkdir(exist_ok=True)

ARTIFACTS_DIR = WORKSPACE / "artifacts"
ARTIFACTS_DIR.mkdir(exist_ok=True)

UPLOADS_DIR = WORKSPACE / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

LOG_DIR = WORKSPACE / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── Logging ─────────────────────────────────────────────────
# Each module should use its own ``logging.getLogger(__name__)``.
# This module-level logger is only for server_state's own utility functions.
logger = logging.getLogger(__name__)


# ── SessionStore: structured access to shared session state ──

class SessionStore:
    """Encapsulates the global sessions dict behind a lock-guarded API.

    Preferred usage (in async route handlers)::

        sess = await session_store.get_or_create(sid)
        await session_store.delete(sid)

    The underlying ``_data`` dict is intentionally accessible via the
    ``sessions`` module alias for **backward compatibility only**.  New code
    should always use the class methods.

    Deprecation notices
    ~~~~~~~~~~~~~~~~~~~
    Set the env var ``NANOBOT_DEPRECATE_DIRECT_SESSION=1`` to emit
    ``DeprecationWarning`` on every **mutating** dict-protocol call:
    ``__setitem__``, ``__delitem__``, ``pop``, ``clear``, ``update``,
    ``setdefault``.  Useful in development or CI to locate call-sites that
    bypass the lock-guarded API.

    Read-path methods (``__getitem__``, ``get``, ``__contains__``,
    ``__len__``, ``__iter__``, ``keys/values/items``) are **not** warned
    — single-key reads are atomic on the asyncio event loop, and warning
    them would generate excessive noise (e.g. ``len(sessions)`` in
    ``/api/status``).
    """

    _WARN = os.environ.get("NANOBOT_DEPRECATE_DIRECT_SESSION", "") == "1"

    def _warn_mutation(self, op: str) -> None:
        # Callers MUST pre-check ``self._WARN`` to avoid f-string overhead
        # when the flag is off; this method assumes the flag is on.
        import warnings
        warnings.warn(
            f"Direct sessions.{op} is deprecated — it bypasses the lock guard. "
            "Use 'async with session_store.lock:' or session_store.get_or_create/delete().",
            DeprecationWarning, stacklevel=3,
        )

    def __init__(self) -> None:
        self._data: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()

    # ── Locked helpers (use from async code) ─────────────────

    async def get_or_create(self, session_id: str, defaults: Optional[Dict] = None) -> Dict:
        """Return existing session or atomically create one with *defaults*."""
        async with self._lock:
            if session_id not in self._data:
                self._data[session_id] = defaults if defaults is not None else {"history": []}
            return self._data[session_id]

    async def delete(self, session_id: str) -> Optional[Dict]:
        """Atomically remove and return a session, or ``None``."""
        async with self._lock:
            return self._data.pop(session_id, None)

    # ── Direct dict-protocol for backward compatibility ──────
    # These allow ``sessions[sid]``, ``sid in sessions``, ``del sessions[sid]``
    # to keep working without modifying 44K lines of server_final.py.

    def __getitem__(self, key: str) -> Dict:
        return self._data[key]

    def __setitem__(self, key: str, value: Dict) -> None:
        if self._WARN:
            self._warn_mutation(f"__setitem__('{key}')")
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        if self._WARN:
            self._warn_mutation(f"__delitem__('{key}')")
        del self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def pop(self, key: str, *args):
        if self._WARN:
            self._warn_mutation(f"pop('{key}')")
        return self._data.pop(key, *args)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def clear(self):
        if self._WARN:
            self._warn_mutation("clear")
        self._data.clear()

    def update(self, *args, **kwargs):
        if self._WARN:
            self._warn_mutation("update")
        self._data.update(*args, **kwargs)

    def setdefault(self, key: str, default=None):
        if self._WARN:
            self._warn_mutation(f"setdefault('{key}')")
        return self._data.setdefault(key, default)

    def copy(self):
        return self._data.copy()

    @property
    def lock(self) -> asyncio.Lock:
        """Expose the lock for callers that need multi-step atomic blocks."""
        return self._lock


session_store = SessionStore()

# Backward-compatible module-level alias.  Existing code that does
# ``from server_state import sessions`` will receive the SessionStore
# instance, which implements ``__getitem__`` / ``__setitem__`` / ``__contains__``
# so ``sessions[sid]`` / ``sid in sessions`` keeps working.
sessions = session_store

stream_processes: Dict[str, subprocess.Popen] = {}
stream_processes_lock = asyncio.Lock()

# Legacy aliases — kept so old imports don't break.
sessions_lock = session_store.lock


async def safe_get_or_create_session(session_id: str, defaults: Optional[Dict] = None) -> Dict:
    """Convenience wrapper — delegates to ``session_store.get_or_create``."""
    return await session_store.get_or_create(session_id, defaults)


async def safe_delete_session(session_id: str) -> Optional[Dict]:
    """Convenience wrapper — delegates to ``session_store.delete``."""
    return await session_store.delete(session_id)


# ── Utility functions shared across routes ──────────────────

def clean_ansi(text: str) -> str:
    """清理 ANSI 转义码"""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def get_system_resources() -> Dict:
    """获取系统资源使用情况"""
    import psutil
    try:
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
            "memory_used_gb": round(psutil.virtual_memory().used / (1024**3), 1),
            "disk_percent": psutil.disk_usage('/').percent,
            "disk_total_gb": round(psutil.disk_usage('/').total / (1024**3), 1),
            "disk_used_gb": round(psutil.disk_usage('/').used / (1024**3), 1),
        }
    except Exception:
        return {}


# ── Rate limiting (shared across routes) ────────────────────
RATE_LIMITS = {
    "default": {"requests": 100, "window": 60},
    "chat": {"requests": 30, "window": 60},
    "stream": {"requests": 10, "window": 60},
}
rate_limit_store = defaultdict(list)


def check_rate_limit(client_ip: str, endpoint_type: str = "default") -> tuple:
    """检查请求限流，返回 (allowed, remaining_seconds)"""
    config = RATE_LIMITS.get(endpoint_type, RATE_LIMITS["default"])
    now = time.time()
    window_start = now - config["window"]

    rate_limit_store[client_ip] = [
        t for t in rate_limit_store[client_ip] if t > window_start
    ]

    current_count = len(rate_limit_store[client_ip])

    if current_count >= config["requests"]:
        oldest = min(rate_limit_store[client_ip])
        remaining = int(oldest + config["window"] - now) + 1
        return False, remaining

    rate_limit_store[client_ip].append(now)
    return True, config["requests"] - current_count - 1
