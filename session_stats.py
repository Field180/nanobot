"""
P5: Session-level statistics aggregation for cost & performance observability.

Collects per-turn metrics (tokens, tool calls, elapsed time) and aggregates
them by session_id. Provides a simple API for querying cumulative stats.

No external dependencies. Thread-safe is not required (single asyncio loop).
"""
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════


class TurnRecord:
    """Immutable record for a single agentic turn."""
    __slots__ = (
        "timestamp", "prompt_tokens", "completion_tokens",
        "total_tokens", "tool_calls", "tools_used",
        "elapsed_seconds", "model", "turns",
    )

    def __init__(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        tool_calls: int = 0,
        tools_used: Optional[List[str]] = None,
        elapsed_seconds: float = 0.0,
        model: str = "",
        turns: int = 1,
    ):
        self.timestamp = time.time()
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens or (prompt_tokens + completion_tokens)
        self.tool_calls = tool_calls
        self.tools_used = tools_used or []
        self.elapsed_seconds = elapsed_seconds
        self.model = model
        self.turns = turns


# ═══════════════════════════════════════════════════════════════
# Stats store
# ═══════════════════════════════════════════════════════════════


class SessionStatsStore:
    """In-memory session statistics aggregator.

    Usage:
        store = SessionStatsStore()
        store.add_turn("sess-1", prompt_tokens=1000, completion_tokens=200, ...)
        stats = store.get_stats("sess-1")
    """

    def __init__(self, max_sessions: int = 100, max_turns_per_session: int = 200):
        self._sessions: Dict[str, List[TurnRecord]] = {}
        self._max_sessions = max_sessions
        self._max_turns = max_turns_per_session

    def add_turn(
        self,
        session_id: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        tool_calls: int = 0,
        tools_used: Optional[List[str]] = None,
        elapsed_seconds: float = 0.0,
        model: str = "",
        turns: int = 1,
    ):
        """Record a completed agentic exchange."""
        if session_id not in self._sessions:
            # Evict oldest if at capacity
            if len(self._sessions) >= self._max_sessions:
                oldest = min(self._sessions.keys(),
                             key=lambda k: self._sessions[k][0].timestamp if self._sessions[k] else 0)
                del self._sessions[oldest]
            self._sessions[session_id] = []

        records = self._sessions[session_id]
        records.append(TurnRecord(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            tool_calls=tool_calls,
            tools_used=tools_used,
            elapsed_seconds=elapsed_seconds,
            model=model,
            turns=turns,
        ))

        # Cap per-session history
        if len(records) > self._max_turns:
            self._sessions[session_id] = records[-self._max_turns:]

    def get_stats(self, session_id: str) -> Dict[str, Any]:
        """Return aggregated stats for a session."""
        records = self._sessions.get(session_id, [])
        if not records:
            return {
                "session_id": session_id,
                "total_exchanges": 0,
                "total_turns": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "total_tool_calls": 0,
                "tools_distribution": {},
                "total_elapsed_seconds": 0.0,
                "avg_tokens_per_exchange": 0,
                "avg_elapsed_per_exchange": 0.0,
                "models_used": [],
                "first_activity": None,
                "last_activity": None,
            }

        total_prompt = sum(r.prompt_tokens for r in records)
        total_completion = sum(r.completion_tokens for r in records)
        total_tokens = sum(r.total_tokens for r in records)
        total_tool_calls = sum(r.tool_calls for r in records)
        total_elapsed = sum(r.elapsed_seconds for r in records)
        total_turns = sum(r.turns for r in records)

        # Tool distribution
        tools_dist: Dict[str, int] = defaultdict(int)
        for r in records:
            for tool in r.tools_used:
                tools_dist[tool] += 1

        # Models used (unique, ordered by first use)
        models = list(dict.fromkeys(r.model for r in records if r.model))

        n = len(records)
        return {
            "session_id": session_id,
            "total_exchanges": n,
            "total_turns": total_turns,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "total_tool_calls": total_tool_calls,
            "tools_distribution": dict(sorted(tools_dist.items(), key=lambda x: -x[1])),
            "total_elapsed_seconds": round(total_elapsed, 2),
            "avg_tokens_per_exchange": round(total_tokens / n, 1) if n else 0,
            "avg_elapsed_per_exchange": round(total_elapsed / n, 2) if n else 0.0,
            "avg_tokens_per_second": round(total_completion / max(total_elapsed, 0.001), 1),
            "models_used": models,
            "first_activity": records[0].timestamp,
            "last_activity": records[-1].timestamp,
        }

    def get_all_sessions(self) -> List[Dict[str, Any]]:
        """Return summary stats for all active sessions."""
        summaries = []
        for sid in sorted(self._sessions.keys(), key=lambda k: (
            self._sessions[k][-1].timestamp if self._sessions[k] else 0
        ), reverse=True):
            records = self._sessions[sid]
            if not records:
                continue
            summaries.append({
                "session_id": sid,
                "exchanges": len(records),
                "total_tokens": sum(r.total_tokens for r in records),
                "total_tool_calls": sum(r.tool_calls for r in records),
                "last_activity": records[-1].timestamp,
            })
        return summaries

    def reset(self, session_id: str):
        """Clear stats for a session."""
        self._sessions.pop(session_id, None)

    def get_session_count(self) -> int:
        """Return number of tracked sessions."""
        return len(self._sessions)


# ═══════════════════════════════════════════════════════════════
# Global singleton
# ═══════════════════════════════════════════════════════════════

_global_store: Optional[SessionStatsStore] = None


def get_stats_store() -> SessionStatsStore:
    """Get or create the global stats store singleton."""
    global _global_store
    if _global_store is None:
        _global_store = SessionStatsStore()
    return _global_store


def reset_global_store():
    """Reset the global store (for testing)."""
    global _global_store
    _global_store = None
