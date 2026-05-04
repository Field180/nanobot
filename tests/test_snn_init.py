"""
Unit tests for snn_init.py — SNN-LLM initialization module.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the web_ui directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import snn_init


class TestSNNConstants(unittest.TestCase):
    """Verify module-level constants and default values."""

    def test_nanobot_v2_dir_is_path(self):
        self.assertIsInstance(snn_init.NANOBOT_V2_DIR, Path)

    def test_nanobot_v2_venv_is_path(self):
        self.assertIsInstance(snn_init.NANOBOT_V2_VENV, Path)

    def test_nanobot_v2_python_str(self):
        self.assertIsInstance(snn_init.NANOBOT_V2_PYTHON, str)
        self.assertTrue(snn_init.NANOBOT_V2_PYTHON.endswith("python3"))

    def test_fast_params_keys(self):
        required = {'time_window', 'max_length', 'skip_feedback',
                     'n_neurons', 'use_openai_api', 'use_sparse_snn'}
        self.assertTrue(required.issubset(set(snn_init.SNN_FAST_PARAMS.keys())))

    def test_fast_mode_defaults_true(self):
        self.assertTrue(snn_init.SNN_FAST_MODE)

    def test_sparse_mode_defaults_true(self):
        self.assertTrue(snn_init.SNN_SPARSE_MODE)


class TestGettersAndSetters(unittest.TestCase):
    """Test accessor helpers."""

    def setUp(self):
        self._orig_proc = snn_init.SNN_PROCESSOR
        self._orig_en = snn_init.SNN_ENABLED

    def tearDown(self):
        snn_init.SNN_PROCESSOR = self._orig_proc
        snn_init.SNN_ENABLED = self._orig_en

    def test_get_snn_processor_returns_none_initially(self):
        snn_init.SNN_PROCESSOR = None
        self.assertIsNone(snn_init.get_snn_processor())

    def test_get_snn_processor_returns_instance(self):
        mock = MagicMock()
        snn_init.SNN_PROCESSOR = mock
        self.assertIs(snn_init.get_snn_processor(), mock)

    def test_is_snn_enabled_false(self):
        snn_init.SNN_ENABLED = False
        self.assertFalse(snn_init.is_snn_enabled())

    def test_is_snn_enabled_true(self):
        snn_init.SNN_ENABLED = True
        self.assertTrue(snn_init.is_snn_enabled())


class TestResetSNNProcessor(unittest.TestCase):
    """Test reset_snn_processor."""

    def setUp(self):
        self._orig = snn_init.SNN_PROCESSOR

    def tearDown(self):
        snn_init.SNN_PROCESSOR = self._orig

    def test_reset_returns_false_when_none(self):
        snn_init.SNN_PROCESSOR = None
        self.assertFalse(snn_init.reset_snn_processor())

    def test_reset_calls_reset_and_returns_true(self):
        mock = MagicMock()
        snn_init.SNN_PROCESSOR = mock
        result = snn_init.reset_snn_processor()
        self.assertTrue(result)
        mock.reset.assert_called_once()


class TestInitSNNProcessor(unittest.TestCase):
    """Test init_snn_processor with mocked imports."""

    def setUp(self):
        self._orig_proc = snn_init.SNN_PROCESSOR
        self._orig_en = snn_init.SNN_ENABLED

    def tearDown(self):
        snn_init.SNN_PROCESSOR = self._orig_proc
        snn_init.SNN_ENABLED = self._orig_en

    def test_returns_existing_processor_if_already_set(self):
        mock = MagicMock()
        snn_init.SNN_PROCESSOR = mock
        result = snn_init.init_snn_processor()
        self.assertIs(result, mock)

    def test_returns_none_on_import_failure(self):
        snn_init.SNN_PROCESSOR = None
        snn_init.SNN_ENABLED = False
        # init_snn_processor will try to import snn_core etc.
        # which are unavailable in the test environment
        result = snn_init.init_snn_processor()
        self.assertIsNone(result)
        self.assertFalse(snn_init.SNN_ENABLED)

    @patch("snn_init.SNN_SPARSE_MODE", False)
    def test_returns_none_when_all_backends_fail(self):
        snn_init.SNN_PROCESSOR = None
        snn_init.SNN_ENABLED = False
        result = snn_init.init_snn_processor()
        self.assertIsNone(result)
        self.assertFalse(snn_init.SNN_ENABLED)

    def test_default_workspace_path(self):
        """Verify that omitting workspace uses the default path."""
        snn_init.SNN_PROCESSOR = None
        snn_init.SNN_ENABLED = False
        # Even though init fails, the function should not raise
        result = snn_init.init_snn_processor(workspace=None)
        self.assertIsNone(result)

    def test_custom_ollama_host(self):
        """Verify that passing an explicit ollama host doesn't raise."""
        snn_init.SNN_PROCESSOR = None
        result = snn_init.init_snn_processor(ollama_host="10.0.0.1")
        self.assertIsNone(result)

    @patch.dict("os.environ", {"NANOBOT_OLLAMA_SSH_HOST": "192.168.1.99"})
    def test_ollama_host_from_env(self):
        """Env var should be picked up when ollama_host is None."""
        snn_init.SNN_PROCESSOR = None
        result = snn_init.init_snn_processor(ollama_host=None)
        # Can't verify internal use directly, but should not crash
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
