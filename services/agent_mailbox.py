"""agent_mailbox — Thread-safe inter-agent message mailbox.

Provides a global mailbox where agents can send messages to each other
by agent_id. Messages are stored in per-agent queues protected by a
threading lock for multi-agent concurrent safety.

Audit hardening:
  - Session-scoped keys prevent cross-session message leakage.
  - Message IDs + delivery ACK counters for observability.
  - Discard events are logged and counted.
  - Atomic JSON persistence (save after mutating ops, load on init).

Semantic note — at-least-once delivery:
  Dead-letter failure records use at-least-once semantics.  After a crash,
  ``query_delivery_failures()`` may return records that were already seen.
  Agents MUST handle duplicate failure notifications idempotently.

Design mirrors Claw's teammateMailbox / SendMessageTool pattern.
"""
import collections
import json as _json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nanobot.services.agent_mailbox")

# Maximum messages kept per agent inbox (FIFO, oldest discarded)
_DEFAULT_MAX_MESSAGES = 50

# Internal separator used in scoped keys — agent_ids must NEVER contain this.
_KEY_SEPARATOR = ":"

# Reserved sender name — only internal dead-letter injection may use this.
_RESERVED_SENDER = "_system"

# Maximum chars of mailbox content injected into agent context per turn
MAILBOX_INJECT_MAX_CHARS = 2000

# Persistence path
_MAILBOX_SNAPSHOT_PATH = Path(os.environ.get(
    "NANOBOT_MAILBOX_SNAPSHOT",
    str(Path.home() / ".nanobot" / ".mailbox_snapshot.json"),
))


def _scoped_key(session_id: str, agent_id: str) -> str:
    """Build a session-scoped inbox key to prevent cross-session leakage.

    Raises ValueError if *agent_id* contains the internal key separator,
    which would allow forged cross-session addressing.
    """
    if _KEY_SEPARATOR in agent_id:
        raise ValueError(
            f"agent_id must not contain '{_KEY_SEPARATOR}': {agent_id!r}"
        )
    if session_id:
        return f"{session_id}{_KEY_SEPARATOR}{agent_id}"
    return agent_id


class AgentMailbox:
    """Thread-safe inter-agent message mailbox.

    Each agent has an inbox keyed by ``session_id:agent_id``.  Messages are
    simple dicts with from_agent, content, timestamp, and msg_id fields.
    Inboxes are capped at ``max_messages``; oldest messages are discarded
    when full (with logging and counter increment).
    """

    # Max delivered msg_ids to track for is_delivered() queries
    _DELIVERED_IDS_CAP = 500
    # Max failed-delivery records kept for query_delivery_failures()
    _FAILED_DELIVERIES_CAP = 100

    def __init__(self, max_messages: int = _DEFAULT_MAX_MESSAGES,
                 persist: bool = False):
        self._lock = threading.Lock()
        self._inboxes: Dict[str, List[Dict[str, Any]]] = {}
        self._max_messages = max_messages
        self._persist = persist
        # Observability counters
        self._msg_counter = 0
        self.sent_total = 0
        self.delivered_total = 0
        self.discarded_total = 0
        self.recovery_loops = 0  # times a save-failure restore was attempted
        self.dead_letters_total = 0  # dead-letter notifications sent
        self.failed_deliveries_dropped = 0  # records lost to FIFO cap
        self.delivery_failures_queried = 0  # times query_delivery_failures returned results
        self.snapshot_slow_count = 0  # snapshots exceeding _SNAPSHOT_SLOW_MS
        self._pending_counter = 0  # incremental pending count (O(1) for Prometheus)
        # Circuit breaker: consecutive save failures per key
        self._recovery_counts: Dict[str, int] = {}
        # Failed delivery records — persisted when snapshot layer recovers
        self._failed_deliveries: List[Dict[str, Any]] = []
        # Bounded set of delivered msg_ids for ACK queries
        self._delivered_ids: collections.deque = collections.deque(maxlen=self._DELIVERED_IDS_CAP)
        # Load persisted state
        if persist:
            self._load_snapshot()

    # ── Session-scoped send / receive ──────────────────────────

    def send_message(self, from_agent: str, to_agent: str, content: str,
                     session_id: str = "",
                     reject_out: Optional[List[str]] = None) -> str:
        """Append a message to *to_agent*'s inbox.

        Returns a non-empty ``msg_id`` on success, or ``""`` on failure.
        If *reject_out* is provided, appends a structured error code on
        rejection (e.g. ``INVALID_AGENT_ID``, ``RESERVED_SENDER``).
        """
        if not to_agent or not content:
            return ""
        # Reject reserved sender names to prevent spoofing system messages
        if from_agent == _RESERVED_SENDER:
            logger.warning("[Mailbox] send_message REJECTED: reserved sender %r", from_agent)
            if reject_out is not None:
                reject_out.append("RESERVED_SENDER")
            return ""
        # Validate at the authoritative layer — reject forged agent_ids
        try:
            _scoped_key(session_id, to_agent)
        except ValueError as e:
            logger.warning("[Mailbox] send_message REJECTED: %s", e)
            if reject_out is not None:
                reject_out.append("INVALID_AGENT_ID")
            return ""
        with self._lock:
            self._msg_counter += 1
            msg_id = f"msg_{self._msg_counter}_{int(time.time() * 1000) % 100000}"
            msg = {
                "msg_id": msg_id,
                "from": from_agent or "unknown",
                "content": content,
                "timestamp": time.time(),
            }
            key = _scoped_key(session_id, to_agent)
            inbox = self._inboxes.setdefault(key, [])
            inbox.append(msg)
            self._pending_counter += 1
            # Evict oldest if over capacity
            if len(inbox) > self._max_messages:
                overflow = len(inbox) - self._max_messages
                del inbox[:overflow]
                self.discarded_total += overflow
                self._pending_counter -= overflow
                logger.warning("[Mailbox] %s: discarded %d oldest message(s) (cap=%d)",
                               key, overflow, self._max_messages)
            self.sent_total += 1
        logger.debug("[Mailbox] %s → %s [%s] (%d chars)",
                     from_agent, key, msg_id, len(content))
        if self._persist:
            self._save_snapshot()
        return msg_id

    def check_messages(self, agent_id: str,
                       session_id: str = "") -> List[Dict[str, Any]]:
        """Return and clear all pending messages for *agent_id* (ACK).

        Persistence safety: snapshot is saved BEFORE messages are removed
        from memory. On write failure, messages are restored to prevent
        permanent data loss.
        """
        key = _scoped_key(session_id, agent_id)
        with self._lock:
            messages = self._inboxes.pop(key, [])
            if not messages:
                return []
            self.delivered_total += len(messages)
            self._pending_counter -= len(messages)
            for m in messages:
                mid = m.get("msg_id")
                if mid:
                    self._delivered_ids.append(mid)
            if self._persist:
                if not self._save_snapshot():
                    # Circuit breaker: track consecutive failures per key
                    prev_fails = self._recovery_counts.get(key, 0) + 1
                    self._recovery_counts[key] = prev_fails
                    self.recovery_loops += 1
                    if prev_fails >= 3:
                        # Breaker tripped — discard to prevent infinite replay
                        self._recovery_counts.pop(key, None)
                        logger.error(
                            "[Mailbox] CIRCUIT BREAKER: %s save failed %d times, "
                            "discarding %d message(s) to prevent infinite replay",
                            key, prev_fails, len(messages))
                        # Dead letter: notify unique senders of permanent failure
                        _notified: set = set()
                        for m in messages:
                            sender = m.get("from", "")
                            if not sender or sender == "unknown" or sender in _notified:
                                continue
                            _notified.add(sender)
                            try:
                                sender_key = _scoped_key(session_id, sender)
                            except ValueError:
                                continue
                            dl_msg = {
                                "msg_id": f"dl_{m.get('msg_id', '')}",
                                "from": "_system",
                                "content": (
                                    f"delivery_failed: your message to "
                                    f"'{agent_id}' was permanently discarded "
                                    f"after {prev_fails} persistence failures"
                                ),
                                "timestamp": time.time(),
                            }
                            self._inboxes.setdefault(sender_key, []).append(dl_msg)
                            self._pending_counter += 1
                            self.dead_letters_total += 1
                        # Record failures for query_delivery_failures()
                        for m in messages:
                            _f_sender = m.get("from", "")
                            if _f_sender and _f_sender != "unknown":
                                try:
                                    _f_key = _scoped_key(session_id, _f_sender)
                                except ValueError:
                                    _f_key = _f_sender
                                self._failed_deliveries.append({
                                    "sender_key": _f_key,
                                    "target": agent_id,
                                    "msg_id": m.get("msg_id", ""),
                                    "timestamp": time.time(),
                                })
                        # Cap failed deliveries (FIFO eviction with logging)
                        if len(self._failed_deliveries) > self._FAILED_DELIVERIES_CAP:
                            _dropped = len(self._failed_deliveries) - self._FAILED_DELIVERIES_CAP
                            self._failed_deliveries = self._failed_deliveries[-self._FAILED_DELIVERIES_CAP:]
                            self.failed_deliveries_dropped += _dropped
                            logger.warning(
                                "[Mailbox] failed_deliveries cap reached: "
                                "dropped %d oldest record(s) (cap=%d)",
                                _dropped, self._FAILED_DELIVERIES_CAP)
                    else:
                        # Restore — write failed, don't lose messages yet
                        self._inboxes[key] = messages
                        self.delivered_total -= len(messages)
                        self._pending_counter += len(messages)
                        for m in messages:
                            mid = m.get("msg_id")
                            if mid and self._delivered_ids and self._delivered_ids[-1] == mid:
                                self._delivered_ids.pop()
                        logger.warning(
                            "[Mailbox] ACK aborted for %s: snapshot save failed "
                            "(%d/%d), messages restored",
                            key, prev_fails, 3)
                    return messages
                else:
                    # Save succeeded — reset failure counter for this key
                    self._recovery_counts.pop(key, None)
        logger.debug("[Mailbox] %s ACK: %d message(s) delivered", key, len(messages))
        return messages

    def is_delivered(self, msg_id: str) -> bool:
        """Check whether a message has been delivered (ACK'd by receiver)."""
        return msg_id in self._delivered_ids

    def peek_messages(self, agent_id: str,
                      session_id: str = "") -> List[Dict[str, Any]]:
        """Return pending messages without clearing (for inspection)."""
        key = _scoped_key(session_id, agent_id)
        with self._lock:
            return list(self._inboxes.get(key, []))

    def pending_count(self, agent_id: str,
                      session_id: str = "") -> int:
        """Number of pending messages for *agent_id*."""
        key = _scoped_key(session_id, agent_id)
        with self._lock:
            return len(self._inboxes.get(key, []))

    def query_delivery_failures(self, agent_id: str,
                                session_id: str = "") -> List[Dict[str, Any]]:
        """Return and clear failed-delivery records for *agent_id* (as sender).

        Allows agents to proactively discover if any of their sent messages
        were permanently discarded by the circuit breaker.  Records survive
        persistence recovery since they are included in the snapshot.

        **At-least-once semantics**: if the process crashes after clearing
        but before the snapshot is persisted, records reappear on restart.
        Callers MUST treat returned records idempotently.
        """
        key = _scoped_key(session_id, agent_id)
        with self._lock:
            matching = [d for d in self._failed_deliveries if d.get("sender_key") == key]
            if matching:
                self._failed_deliveries = [
                    d for d in self._failed_deliveries if d.get("sender_key") != key
                ]
                self.delivery_failures_queried += 1
                # Persist immediately to tighten clear-vs-crash window
                if self._persist:
                    self._save_snapshot()
            return matching

    def register_agent(self, agent_id: str,
                       session_id: str = "") -> None:
        """Ensure an inbox exists for *agent_id* (idempotent)."""
        key = _scoped_key(session_id, agent_id)
        with self._lock:
            self._inboxes.setdefault(key, [])

    def unregister_agent(self, agent_id: str,
                         session_id: str = "") -> List[Dict[str, Any]]:
        """Remove an agent's inbox. Returns any unread messages."""
        key = _scoped_key(session_id, agent_id)
        with self._lock:
            removed = self._inboxes.pop(key, [])
            self._pending_counter -= len(removed)
            return removed

    def reset(self) -> None:
        """Clear all inboxes and counters (for testing / session reset)."""
        with self._lock:
            self._inboxes.clear()
            self.sent_total = 0
            self.delivered_total = 0
            self.discarded_total = 0
            self.recovery_loops = 0
            self.dead_letters_total = 0
            self.failed_deliveries_dropped = 0
            self.delivery_failures_queried = 0
            self.snapshot_slow_count = 0
            self._pending_counter = 0
            self._msg_counter = 0
            self._delivered_ids.clear()
            self._recovery_counts.clear()
            self._failed_deliveries.clear()

    @property
    def agent_count(self) -> int:
        with self._lock:
            return len(self._inboxes)

    @property
    def total_pending(self) -> int:
        with self._lock:
            return self._pending_counter

    def _pending_count_locked(self) -> int:
        """Return pending count (caller must hold self._lock)."""
        return self._pending_counter

    def get_stats(self) -> Dict[str, int]:
        """Return observability counters."""
        with self._lock:
            return {
                "sent_total": self.sent_total,
                "delivered_total": self.delivered_total,
                "discarded_total": self.discarded_total,
                "recovery_loops": self.recovery_loops,
                "dead_letters_total": self.dead_letters_total,
                "failed_deliveries_dropped": self.failed_deliveries_dropped,
                "delivery_failures_queried": self.delivery_failures_queried,
                "snapshot_slow_count": self.snapshot_slow_count,
                "pending": self._pending_count_locked(),
                "agents": len(self._inboxes),
            }

    # ── Persistence (atomic JSON snapshot) ─────────────────────

    # Snapshot duration threshold (ms) — exceeding this triggers a warning
    _SNAPSHOT_SLOW_MS = int(os.environ.get("NANOBOT_MAILBOX_SLOW_MS", "100"))

    def _save_snapshot(self) -> bool:
        """Persist current mailbox state to disk (atomic temp+rename).

        Returns True on success, False on failure.
        Logs a warning and increments ``snapshot_slow_count`` when the
        operation exceeds ``_SNAPSHOT_SLOW_MS`` milliseconds.
        """
        _t0 = time.monotonic()
        try:
            data = {
                "inboxes": self._inboxes,
                "msg_counter": self._msg_counter,
                "sent_total": self.sent_total,
                "delivered_total": self.delivered_total,
                "discarded_total": self.discarded_total,
                "failed_deliveries": self._failed_deliveries,
            }
            _MAILBOX_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _MAILBOX_SNAPSHOT_PATH.with_suffix(".tmp")
            tmp.write_text(_json.dumps(data), encoding="utf-8")
            os.chmod(str(tmp), 0o600)
            tmp.rename(_MAILBOX_SNAPSHOT_PATH)
            _elapsed_ms = (time.monotonic() - _t0) * 1000
            if _elapsed_ms > self._SNAPSHOT_SLOW_MS:
                self.snapshot_slow_count += 1
                logger.warning(
                    "[Mailbox] snapshot save slow: %.1fms (threshold=%dms)",
                    _elapsed_ms, self._SNAPSHOT_SLOW_MS)
            return True
        except Exception as e:
            logger.warning("[Mailbox] snapshot save failed: %s", e)
            return False

    def _load_snapshot(self) -> None:
        """Load persisted mailbox state from disk."""
        try:
            if not _MAILBOX_SNAPSHOT_PATH.exists():
                return
            data = _json.loads(_MAILBOX_SNAPSHOT_PATH.read_text(encoding="utf-8"))
            with self._lock:
                self._inboxes = data.get("inboxes", {})
                self._msg_counter = data.get("msg_counter", 0)
                self.sent_total = data.get("sent_total", 0)
                self.delivered_total = data.get("delivered_total", 0)
                self.discarded_total = data.get("discarded_total", 0)
                self._failed_deliveries = data.get("failed_deliveries", [])
                self._pending_counter = sum(len(v) for v in self._inboxes.values())
            pending = self._pending_counter
            if pending:
                logger.info("[Mailbox] restored snapshot: %d pending message(s)", pending)
        except Exception as e:
            logger.warning("[Mailbox] snapshot load failed: %s", e)


# ── Global singleton ──────────────────────────────────────────
_GLOBAL_MAILBOX: Optional[AgentMailbox] = None
_GLOBAL_LOCK = threading.Lock()


def get_mailbox() -> AgentMailbox:
    """Get or create the global AgentMailbox singleton (with persistence)."""
    global _GLOBAL_MAILBOX
    if _GLOBAL_MAILBOX is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_MAILBOX is None:
                _GLOBAL_MAILBOX = AgentMailbox(persist=True)
    return _GLOBAL_MAILBOX
