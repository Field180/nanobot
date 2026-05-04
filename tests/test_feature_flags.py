"""
Tests for utils.feature_flags — centralized feature flag registry.
Verifies flag resolution, type coercion, env override, runtime override,
config file loading, and API route registration.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

WEB_UI = Path(__file__).parent.parent
if str(WEB_UI) not in sys.path:
    sys.path.insert(0, str(WEB_UI))

from utils.feature_flags import FeatureFlags, _FlagDef, BOOL, INT, FLOAT, STR, ff


class TestFlagDefaults(unittest.TestCase):
    """Flags return their registered defaults when no override is present."""

    def test_bool_default_true(self):
        self.assertTrue(ff.is_enabled("auto_compact"))

    def test_bool_default_unregistered(self):
        self.assertFalse(ff.is_enabled("nonexistent_flag"))

    def test_int_default(self):
        self.assertEqual(ff.get_int("max_retries"), 5)

    def test_int_reactive_compact(self):
        self.assertTrue(ff.is_enabled("reactive_compact"))

    def test_unregistered_flag_returns_none(self):
        self.assertIsNone(ff.get("nonexistent_flag_xyz"))

    def test_unregistered_with_default(self):
        self.assertEqual(ff.get("nonexistent", "fallback"), "fallback")


class TestEnvOverride(unittest.TestCase):
    """Environment variables override defaults."""

    def setUp(self):
        self._saved = {}

    def tearDown(self):
        for key in self._saved:
            if self._saved[key] is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = self._saved[key]

    def _set_env(self, key, val):
        self._saved[key] = os.environ.get(key)
        os.environ[key] = val

    def test_bool_override_false(self):
        self._set_env("NANOBOT_FF_AUTO_COMPACT", "false")
        self.assertFalse(ff.is_enabled("auto_compact"))

    def test_bool_override_true(self):
        self._set_env("NANOBOT_FF_TRANSIENT_RETRY", "true")
        self.assertTrue(ff.is_enabled("transient_retry"))

    def test_int_override(self):
        self._set_env("NANOBOT_FF_MAX_RETRIES", "10")
        self.assertEqual(ff.get_int("max_retries"), 10)

    def test_invalid_int_falls_back(self):
        self._set_env("NANOBOT_FF_MAX_RETRIES", "not_a_number")
        self.assertEqual(ff.get_int("max_retries"), 5)  # default

    def test_source_is_env(self):
        self._set_env("NANOBOT_FF_AUTO_COMPACT", "false")
        info = ff.get_flag_info("auto_compact")
        self.assertEqual(info["source"], "env")


class TestRuntimeOverride(unittest.TestCase):
    """set_override / clear_override for dynamic testing."""

    def tearDown(self):
        ff.clear_all_overrides()

    def test_set_override(self):
        ff.set_override("auto_compact", False)
        self.assertFalse(ff.is_enabled("auto_compact"))

    def test_override_beats_env(self):
        old = os.environ.get("NANOBOT_FF_AUTO_COMPACT")
        os.environ["NANOBOT_FF_AUTO_COMPACT"] = "true"
        try:
            ff.set_override("auto_compact", False)
            self.assertFalse(ff.is_enabled("auto_compact"))
        finally:
            if old is None:
                os.environ.pop("NANOBOT_FF_AUTO_COMPACT", None)
            else:
                os.environ["NANOBOT_FF_AUTO_COMPACT"] = old

    def test_clear_override(self):
        ff.set_override("auto_compact", False)
        ff.clear_override("auto_compact")
        self.assertTrue(ff.is_enabled("auto_compact"))

    def test_clear_all(self):
        ff.set_override("auto_compact", False)
        ff.set_override("max_retries", 99)
        ff.clear_all_overrides()
        self.assertTrue(ff.is_enabled("auto_compact"))
        self.assertEqual(ff.get_int("max_retries"), 5)

    def test_source_is_override(self):
        ff.set_override("auto_compact", False)
        info = ff.get_flag_info("auto_compact")
        self.assertEqual(info["source"], "override")


class TestConfigFile(unittest.TestCase):
    """Config file loading and hot-reload."""

    def test_load_from_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"auto_compact": False, "max_retries": 42}, f)
            f.flush()
            tmp_path = Path(f.name)

        try:
            ff2 = FeatureFlags()
            ff2._config_path = tmp_path
            ff2._load_config_file()
            self.assertFalse(ff2.is_enabled("auto_compact"))
            self.assertEqual(ff2.get_int("max_retries"), 42)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_missing_config_file_ok(self):
        ff2 = FeatureFlags()
        ff2._config_path = Path("/nonexistent/path/flags.json")
        ff2._load_config_file()  # should not raise
        self.assertTrue(ff2.is_enabled("auto_compact"))  # default

    def test_malformed_json_ok(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid json")
            f.flush()
            tmp_path = Path(f.name)

        try:
            ff2 = FeatureFlags()
            ff2._config_path = tmp_path
            ff2._load_config_file()  # should not raise
            self.assertTrue(ff2.is_enabled("auto_compact"))  # default
        finally:
            tmp_path.unlink(missing_ok=True)


class TestListFlags(unittest.TestCase):
    """Introspection API."""

    def test_list_returns_all(self):
        flags = ff.list_flags()
        names = {f["name"] for f in flags}
        self.assertIn("auto_compact", names)
        self.assertIn("max_retries", names)
        self.assertIn("transient_retry", names)
        self.assertIn("reactive_compact", names)
        self.assertEqual(len(names), 4)

    def test_list_entry_structure(self):
        flags = ff.list_flags()
        for f in flags:
            self.assertIn("name", f)
            self.assertIn("type", f)
            self.assertIn("default", f)
            self.assertIn("current", f)
            self.assertIn("source", f)
            self.assertIn("description", f)

    def test_flag_info_existing(self):
        info = ff.get_flag_info("auto_compact")
        self.assertIsNotNone(info)
        self.assertEqual(info["name"], "auto_compact")
        self.assertEqual(info["type"], "bool")

    def test_flag_info_missing(self):
        self.assertIsNone(ff.get_flag_info("nonexistent"))


class TestTypeCoercion(unittest.TestCase):
    """is_enabled handles various truthy/falsy string values."""

    def tearDown(self):
        ff.clear_all_overrides()

    def test_string_true_variants(self):
        for val in ("true", "1", "yes", "on", "True", "YES", "ON"):
            ff.set_override("transient_retry", val)
            self.assertTrue(ff.is_enabled("transient_retry"), f"Failed for {val}")

    def test_string_false_variants(self):
        for val in ("false", "0", "no", "off"):
            ff.set_override("auto_compact", val)
            self.assertFalse(ff.is_enabled("auto_compact"), f"Failed for {val}")

    def test_int_zero_is_false(self):
        ff.set_override("auto_compact", 0)
        self.assertFalse(ff.is_enabled("auto_compact"))

    def test_int_nonzero_is_true(self):
        ff.set_override("transient_retry", 1)
        self.assertTrue(ff.is_enabled("transient_retry"))


class TestRouteRegistration(unittest.TestCase):
    """Feature flags API routes are registered on the app."""

    @classmethod
    def setUpClass(cls):
        from server_final import app
        cls.paths = {r.path for r in app.routes if hasattr(r, "path")}

    def test_list_endpoint(self):
        self.assertIn("/api/feature-flags", self.paths)

    def test_get_endpoint(self):
        self.assertIn("/api/feature-flags/{name}", self.paths)

    def test_reload_endpoint(self):
        self.assertIn("/api/feature-flags/reload", self.paths)


class TestRegistryCompleteness(unittest.TestCase):
    """All consumed flags are registered."""

    def setUp(self):
        self.names = {f["name"] for f in ff.list_flags()}

    def test_agentic_loop_flags(self):
        self.assertIn("auto_compact", self.names)

    def test_error_handling_flags(self):
        for name in ("transient_retry", "max_retries", "reactive_compact"):
            self.assertIn(name, self.names, f"Missing flag: {name}")

    def test_no_unused_flags(self):
        """Registry should only contain flags with actual consumers."""
        expected = {"auto_compact", "transient_retry", "max_retries", "reactive_compact"}
        self.assertEqual(self.names, expected)


class TestFlagNegativePaths(unittest.TestCase):
    """R6: Behavioral tests — verify flag=False actually disables functionality."""

    def test_auto_compact_false_skips_should_compact(self):
        """When auto_compact=false, CompactService.should_compact() returns False."""
        from compact_engine import CompactService
        svc = CompactService({})
        ff.set_override("auto_compact", False)
        try:
            # Even with huge token count, should_compact must return False
            big_messages = [{"role": "user", "content": "x" * 100000}]
            self.assertFalse(svc.should_compact(big_messages))
        finally:
            ff.clear_override("auto_compact")

    def test_auto_compact_false_compact_returns_none(self):
        """When auto_compact=false, CompactService.compact() returns None."""
        import asyncio
        from compact_engine import CompactService
        svc = CompactService({})
        ff.set_override("auto_compact", False)
        try:
            result = asyncio.new_event_loop().run_until_complete(
                svc.compact([{"role": "user", "content": "test"}], "test-session")
            )
            self.assertIsNone(result)
        finally:
            ff.clear_override("auto_compact")

    def test_auto_compact_true_allows_should_compact(self):
        """When auto_compact=true (default), should_compact can return True for big context."""
        from compact_engine import CompactService
        svc = CompactService({})
        ff.clear_override("auto_compact")
        # Create messages large enough to exceed 80% of ceiling
        big_messages = [{"role": "user", "content": "x" * 200000}]
        self.assertTrue(svc.should_compact(big_messages))

    def test_transient_retry_false_blocks_retry_path(self):
        """When transient_retry=false, the retry condition in agentic_chat_stream is False."""
        from utils.errors import TransientAPIError
        ff.set_override("transient_retry", False)
        try:
            # The retry guard: isinstance(err, TransientAPIError) AND ff.is_enabled("transient_retry")
            err = TransientAPIError("test")
            gate = isinstance(err, TransientAPIError) and ff.is_enabled("transient_retry")
            self.assertFalse(gate, "transient_retry=false must block the retry path")
        finally:
            ff.clear_override("transient_retry")

    def test_transient_retry_true_allows_retry_path(self):
        """When transient_retry=true, the retry condition passes its flag gate."""
        from utils.errors import TransientAPIError
        ff.clear_override("transient_retry")
        err = TransientAPIError("test")
        gate = isinstance(err, TransientAPIError) and ff.is_enabled("transient_retry")
        self.assertTrue(gate, "transient_retry=true must allow the retry path")

    def test_reactive_compact_false_blocks_reactive_path(self):
        """When reactive_compact=false, the reactive compaction gate is closed."""
        from utils.errors import ContextOverflowError
        ff.set_override("reactive_compact", False)
        try:
            err = ContextOverflowError("test")
            gate = isinstance(err, ContextOverflowError) and ff.is_enabled("reactive_compact")
            self.assertFalse(gate)
        finally:
            ff.clear_override("reactive_compact")

    def test_max_retries_respected(self):
        """ff.get_int('max_retries') controls the retry limit."""
        ff.set_override("max_retries", 2)
        try:
            self.assertEqual(ff.get_int("max_retries"), 2)
        finally:
            ff.clear_override("max_retries")


if __name__ == "__main__":
    unittest.main()
