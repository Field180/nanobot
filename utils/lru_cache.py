"""
LRU Cache with count + bytes dual eviction (inspired by Claw fileStateCache.ts)
================================================================================
Provides a memory-safe LRU cache that evicts entries based on both:
  - max_entries: maximum number of items (like Claw's `max`)
  - max_size_bytes: total byte budget for all values (like Claw's `maxSize`)

Path normalization is built in: all string keys are resolved via
os.path.normpath + os.path.realpath to prevent duplicate entries for
/foo/../bar vs /bar.

Usage:
    from utils.lru_cache import LRUCache

    cache = LRUCache(max_entries=100, max_size_bytes=25*1024*1024,
                     size_func=lambda v: len(v.get("output", "")))
    cache.set("/some/path", {"output": "...", "mtime": 1234})
    hit = cache.get("/some/path")

Thread/async safety: single-threaded asyncio — safe without locks as long as
no `await` occurs between get/set pairs. Same contract as server_state.py.
"""
from __future__ import annotations

import os
from collections import OrderedDict
from typing import Any, Callable, Generator, Hashable, Optional


def _normalize_path_key(key: str) -> str:
    """Normalize file path keys to prevent cache duplication.
    Mirrors Claw's normalize(key) pattern in fileStateCache.ts."""
    return os.path.normpath(os.path.realpath(key))


class LRUCache:
    """LRU cache with count + bytes dual upper bound.

    Args:
        max_entries: Maximum number of cached items. 0 = unlimited.
        max_size_bytes: Maximum total size in bytes. 0 = unlimited (count-only mode).
        size_func: Callable that returns byte-size for a given value.
                   Defaults to 0 (count-only mode if max_size_bytes is also 0).
        normalize_keys: If True, keys are treated as file paths and normalized.
    """

    def __init__(
        self,
        max_entries: int = 100,
        max_size_bytes: int = 0,
        size_func: Optional[Callable[[Any], int]] = None,
        normalize_keys: bool = True,
    ) -> None:
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._max_entries = max(0, max_entries)
        self._max_size_bytes = max(0, max_size_bytes)
        self._size_func = size_func or (lambda _v: 0)
        self._normalize = normalize_keys
        self._current_size_bytes = 0
        # Per-key size tracking for accurate eviction
        self._sizes: dict[str, int] = {}

    # ── Key normalization ─────────────────────────────────────

    def _norm(self, key: Any) -> Any:
        if self._normalize and isinstance(key, str):
            return _normalize_path_key(key)
        return key

    # ── Public API (mirrors Claw FileStateCache interface) ────

    def get(self, key: Hashable, default: Any = None) -> Any:
        """Retrieve value and promote to most-recently-used."""
        nk = self._norm(key)
        if nk not in self._data:
            return default
        self._data.move_to_end(nk)
        return self._data[nk]

    def set(self, key: Hashable, value: Any) -> None:
        """Insert or update an entry, evicting LRU items if limits exceeded."""
        nk = self._norm(key)
        entry_size = max(1, self._size_func(value)) if self._max_size_bytes else 0

        # Remove old entry if updating (adjust tracked size)
        if nk in self._data:
            self._current_size_bytes -= self._sizes.get(nk, 0)
            del self._data[nk]
            del self._sizes[nk]

        # Evict until both limits are satisfied
        self._evict_for(entry_size)

        self._data[nk] = value
        self._sizes[nk] = entry_size
        self._current_size_bytes += entry_size
        self._data.move_to_end(nk)

    def has(self, key: Hashable) -> bool:
        return self._norm(key) in self._data

    def delete(self, key: Hashable) -> bool:
        nk = self._norm(key)
        if nk not in self._data:
            return False
        self._current_size_bytes -= self._sizes.pop(nk, 0)
        del self._data[nk]
        return True

    def clear(self) -> None:
        self._data.clear()
        self._sizes.clear()
        self._current_size_bytes = 0

    def pop(self, key: Hashable, default: Any = None) -> Any:
        nk = self._norm(key)
        if nk not in self._data:
            return default
        self._current_size_bytes -= self._sizes.pop(nk, 0)
        return self._data.pop(nk)

    # ── Dict-protocol compatibility ──────────────────────────

    def __contains__(self, key: Hashable) -> bool:
        return self.has(key)

    def __getitem__(self, key: Hashable) -> Any:
        nk = self._norm(key)
        if nk not in self._data:
            raise KeyError(key)
        self._data.move_to_end(nk)
        return self._data[nk]

    def __setitem__(self, key: Hashable, value: Any) -> None:
        self.set(key, value)

    def __delitem__(self, key: Hashable) -> None:
        if not self.delete(key):
            raise KeyError(key)

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Generator[str, None, None]:
        yield from self._data

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    # ── Introspection (mirrors Claw's size/max/calculatedSize) ─

    @property
    def size(self) -> int:
        """Current number of entries."""
        return len(self._data)

    @property
    def max_entries(self) -> int:
        return self._max_entries

    @property
    def max_size_bytes(self) -> int:
        return self._max_size_bytes

    @property
    def calculated_size_bytes(self) -> int:
        """Current total size in bytes."""
        return self._current_size_bytes

    # ── Internal ─────────────────────────────────────────────

    def _evict_for(self, incoming_size: int) -> None:
        """Evict least-recently-used entries until both limits can accept incoming_size."""
        # Count-based eviction
        if self._max_entries:
            while len(self._data) >= self._max_entries:
                self._evict_oldest()

        # Size-based eviction
        if self._max_size_bytes and incoming_size:
            while (self._current_size_bytes + incoming_size > self._max_size_bytes
                   and self._data):
                self._evict_oldest()

    def _evict_oldest(self) -> None:
        """Remove the least-recently-used (first) entry."""
        if not self._data:
            return
        oldest_key, _ = self._data.popitem(last=False)
        self._current_size_bytes -= self._sizes.pop(oldest_key, 0)
