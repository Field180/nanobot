"""
P98a: Skill Usage Tracking
============================
Inspired by Claw's skillUsageTracking.ts.

Tracks skill invocation count and last-used timestamp.
Uses exponential decay scoring (7-day half-life) to rank skills
by recency and frequency — recently used skills appear first.

Storage: JSON file at ~/.nanobot/skill_usage.json
"""
import json
import logging
import math
import time
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("nanobot.skills")

# Decay constant: score halves every 7 days
_HALF_LIFE_DAYS = 7.0
_DEBOUNCE_SECONDS = 60  # Don't write more than once per minute per skill
_MIN_RECENCY_FACTOR = 0.1  # Floor so old-but-heavy skills don't vanish

# In-memory state
_usage_data: Dict[str, dict] = {}
_last_write_time: Dict[str, float] = {}
_loaded = False
_usage_file: Optional[Path] = None


def _get_usage_file() -> Path:
    """Get the path to the usage tracking file."""
    global _usage_file
    if _usage_file is None:
        _usage_file = Path.home() / ".nanobot" / "skill_usage.json"
    return _usage_file


def _load_usage() -> None:
    """Load usage data from disk (lazy, once per process)."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    path = _get_usage_file()
    if not path.exists():
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _usage_data.clear()
        _usage_data.update(data)
    except Exception as e:
        logger.warning(f"[P98a] Failed to load skill usage: {e}")


def _save_usage() -> None:
    """Persist usage data to disk."""
    path = _get_usage_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_usage_data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[P98a] Failed to save skill usage: {e}")


def record_skill_usage(skill_name: str) -> None:
    """Record that a skill was invoked. Debounced to ≤1 write/min per skill."""
    _load_usage()

    now = time.time()
    last = _last_write_time.get(skill_name, 0)
    if now - last < _DEBOUNCE_SECONDS:
        return

    _last_write_time[skill_name] = now

    existing = _usage_data.get(skill_name, {})
    _usage_data[skill_name] = {
        "usage_count": existing.get("usage_count", 0) + 1,
        "last_used_at": now,
    }
    _save_usage()


def get_skill_usage_score(skill_name: str) -> float:
    """Calculate a usage score for ranking.

    Uses exponential decay: score = count * max(0.5^(days/7), 0.1).
    Higher scores → more frequently/recently used.
    """
    _load_usage()

    usage = _usage_data.get(skill_name)
    if not usage:
        return 0.0

    count = usage.get("usage_count", 0)
    last_used = usage.get("last_used_at", 0)
    if count == 0 or last_used == 0:
        return 0.0

    days_since = (time.time() - last_used) / 86400.0
    recency = math.pow(0.5, days_since / _HALF_LIFE_DAYS)
    recency = max(recency, _MIN_RECENCY_FACTOR)

    return count * recency


def get_all_usage_scores() -> Dict[str, float]:
    """Get scores for all tracked skills."""
    _load_usage()
    return {name: get_skill_usage_score(name) for name in _usage_data}


def reset_usage_tracking() -> None:
    """Reset all usage data (for testing)."""
    global _loaded
    _usage_data.clear()
    _last_write_time.clear()
    _loaded = True  # Prevent re-loading stale data from disk
