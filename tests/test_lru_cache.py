"""
Tests for utils.lru_cache — memory-safe LRU with count + bytes dual eviction.
Verifies Claw FileStateCache parity: count limit, byte limit, path normalization,
LRU promotion, tuple keys, clear/delete, and integration with file_read/base.
"""
import sys
import unittest
from pathlib import Path

WEB_UI = Path(__file__).parent.parent
if str(WEB_UI) not in sys.path:
    sys.path.insert(0, str(WEB_UI))

from utils.lru_cache import LRUCache


class TestLRUCacheCountEviction(unittest.TestCase):
    """Entries beyond max_entries are evicted LRU-first."""

    def test_evicts_oldest_when_full(self):
        c = LRUCache(max_entries=3, normalize_keys=False)
        c.set("a", 1); c.set("b", 2); c.set("c", 3)
        c.set("d", 4)
        self.assertEqual(len(c), 3)
        self.assertIsNone(c.get("a"))
        self.assertEqual(c.get("d"), 4)

    def test_lru_promotion_on_get(self):
        c = LRUCache(max_entries=3, normalize_keys=False)
        c.set("a", 1); c.set("b", 2); c.set("c", 3)
        c.get("a")  # promote a
        c.set("d", 4)  # evicts b (now LRU)
        self.assertEqual(c.get("a"), 1)
        self.assertIsNone(c.get("b"))

    def test_update_does_not_grow(self):
        c = LRUCache(max_entries=3, normalize_keys=False)
        c.set("a", 1); c.set("b", 2); c.set("c", 3)
        c.set("a", 10)  # update, not insert
        self.assertEqual(len(c), 3)
        self.assertEqual(c.get("a"), 10)


class TestLRUCacheByteEviction(unittest.TestCase):
    """Entries exceeding max_size_bytes are evicted LRU-first."""

    def setUp(self):
        self.cache = LRUCache(
            max_entries=100,
            max_size_bytes=100,
            size_func=lambda v: len(v),
            normalize_keys=False,
        )

    def test_evicts_when_bytes_exceeded(self):
        self.cache.set("x", "a" * 40)
        self.cache.set("y", "b" * 40)
        self.assertEqual(len(self.cache), 2)
        self.cache.set("z", "c" * 40)  # 120 > 100 → evict x
        self.assertEqual(len(self.cache), 2)
        self.assertIsNone(self.cache.get("x"))
        self.assertEqual(self.cache.calculated_size_bytes, 80)

    def test_large_single_entry(self):
        self.cache.set("big", "x" * 90)
        self.assertEqual(len(self.cache), 1)
        self.cache.set("extra", "y" * 20)  # 110 > 100 → evict big
        self.assertEqual(len(self.cache), 1)
        self.assertIsNone(self.cache.get("big"))

    def test_update_adjusts_size(self):
        self.cache.set("a", "x" * 50)
        self.assertEqual(self.cache.calculated_size_bytes, 50)
        self.cache.set("a", "y" * 30)
        self.assertEqual(self.cache.calculated_size_bytes, 30)


class TestPathNormalization(unittest.TestCase):
    """Normalized keys prevent cache duplication for equivalent paths."""

    def test_redundant_segments(self):
        c = LRUCache(max_entries=10, normalize_keys=True)
        c.set("/foo/bar/../baz", "val1")
        self.assertEqual(c.get("/foo/baz"), "val1")

    def test_no_normalize_for_tuples(self):
        c = LRUCache(max_entries=10, normalize_keys=False)
        key = ("/path/to/file", 0, None)
        c.set(key, {"data": 1})
        self.assertEqual(c.get(key), {"data": 1})


class TestTupleKeys(unittest.TestCase):
    """Tuple keys work correctly (for file_read_cache compatibility)."""

    def test_tuple_crud(self):
        c = LRUCache(max_entries=5, normalize_keys=False)
        k = ("/some/file.py", 100, 200)
        c.set(k, {"mtime": 1.0, "result": "data"})
        self.assertTrue(c.has(k))
        self.assertEqual(c.get(k)["mtime"], 1.0)
        c.delete(k)
        self.assertFalse(c.has(k))

    def test_iterate_and_filter_tuples(self):
        """invalidate_session_reads iterates keys and filters by k[0]."""
        c = LRUCache(max_entries=10, normalize_keys=False)
        c.set(("/a.py", 0, None), 1)
        c.set(("/a.py", 100, 200), 2)
        c.set(("/b.py", 0, None), 3)
        to_remove = [k for k in c if k[0] == "/a.py"]
        for k in to_remove:
            c.delete(k)
        self.assertEqual(len(c), 1)
        self.assertTrue(c.has(("/b.py", 0, None)))


class TestDictProtocol(unittest.TestCase):
    """Dict-protocol (__contains__, __getitem__, __setitem__, __delitem__)."""

    def test_contains(self):
        c = LRUCache(max_entries=5, normalize_keys=False)
        c.set("x", 1)
        self.assertIn("x", c)
        self.assertNotIn("y", c)

    def test_bracket_access(self):
        c = LRUCache(max_entries=5, normalize_keys=False)
        c["x"] = 1
        self.assertEqual(c["x"], 1)

    def test_bracket_delete(self):
        c = LRUCache(max_entries=5, normalize_keys=False)
        c["x"] = 1
        del c["x"]
        self.assertNotIn("x", c)

    def test_missing_key_raises(self):
        c = LRUCache(max_entries=5, normalize_keys=False)
        with self.assertRaises(KeyError):
            _ = c["missing"]

    def test_clear(self):
        c = LRUCache(max_entries=5, max_size_bytes=100,
                     size_func=lambda v: len(v), normalize_keys=False)
        c.set("a", "hello")
        c.set("b", "world")
        c.clear()
        self.assertEqual(len(c), 0)
        self.assertEqual(c.calculated_size_bytes, 0)


class TestIntegrationFileRead(unittest.TestCase):
    """Verify file_read module caches are LRUCache instances with correct limits."""

    def test_file_read_cache_type_and_limits(self):
        from tools.file_read import _file_read_cache
        self.assertIsInstance(_file_read_cache, LRUCache)
        self.assertEqual(_file_read_cache.max_entries, 50)
        self.assertEqual(_file_read_cache.max_size_bytes, 25 * 1024 * 1024)

    def test_session_read_tracker_type(self):
        from tools.file_read import _session_read_tracker
        self.assertIsInstance(_session_read_tracker, LRUCache)
        self.assertEqual(_session_read_tracker.max_entries, 200)

    def test_read_file_state_type(self):
        from tools.base import _read_file_state
        self.assertIsInstance(_read_file_state, LRUCache)
        self.assertEqual(_read_file_state.max_entries, 200)

    def test_reset_clears_tracker(self):
        from tools.file_read import _session_read_tracker, reset_session_read_tracker
        reset_session_read_tracker()  # clear any pollution from prior tests
        _session_read_tracker.set(("test", 0, None), {"mtime": 1.0, "total_lines": 10})
        self.assertEqual(len(_session_read_tracker), 1)
        reset_session_read_tracker()
        self.assertEqual(len(_session_read_tracker), 0)


if __name__ == "__main__":
    unittest.main()
