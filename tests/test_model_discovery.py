"""
Unit tests for model_discovery module (P9 extraction).
"""
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure web_ui is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model_discovery import (
    check_remote_service,
    fetch_ollama_models,
    fetch_llamacpp_models,
    discover_remote_models,
    format_bytes,
    guess_model_type,
    parse_ollama_ps_models,
    STATIC_LLAMACPP_MODELS,
    STATIC_OLLAMA_MODELS,
)


class TestFormatBytes(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_bytes(0), "")

    def test_gb(self):
        self.assertEqual(format_bytes(2 * 1024**3), "2.0GB")

    def test_mb(self):
        self.assertEqual(format_bytes(512 * 1024**2), "512MB")

    def test_falsy(self):
        self.assertEqual(format_bytes(None), "")


class TestGuessModelType(unittest.TestCase):
    def test_code(self):
        self.assertEqual(guess_model_type("qwen3-coder-next"), "code")

    def test_reasoning(self):
        self.assertEqual(guess_model_type("deepseek-r1:70b"), "reasoning")

    def test_vision(self):
        self.assertEqual(guess_model_type("llava-vl-13b"), "vision")

    def test_uncensored(self):
        self.assertEqual(guess_model_type("dolphin-uncensored"), "uncensored")

    def test_chat_default(self):
        self.assertEqual(guess_model_type("llama3"), "chat")


class TestParseOllamaPsModels(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(parse_ollama_ps_models(""), [])

    def test_header_only(self):
        self.assertEqual(parse_ollama_ps_models("NAME  ID  SIZE  PROCESSOR"), [])

    def test_single_model(self):
        ps = (
            "NAME                       ID              SIZE     PROCESSOR          CONTEXT    UNTIL\n"
            "qwen3-coder-next:q4_K_M    ca06e9e4087c    55 GB    58%/42% CPU/GPU    32768      About a minute from now\n"
        )
        result = parse_ollama_ps_models(ps)
        self.assertEqual(result, ["qwen3-coder-next:q4_K_M"])

    def test_multiple_models(self):
        ps = (
            "NAME                 ID              SIZE\n"
            "model-a:latest       abc123          10 GB\n"
            "model-b:7b           def456          7 GB\n"
        )
        result = parse_ollama_ps_models(ps)
        self.assertEqual(len(result), 2)
        self.assertIn("model-a:latest", result)
        self.assertIn("model-b:7b", result)


class TestCheckRemoteService(unittest.TestCase):
    @patch("model_discovery.socket.socket")
    def test_reachable(self, mock_sock_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_sock_cls.return_value = mock_sock
        self.assertTrue(check_remote_service("127.0.0.1", 11434))
        mock_sock.close.assert_called_once()

    @patch("model_discovery.socket.socket")
    def test_unreachable(self, mock_sock_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 1
        mock_sock_cls.return_value = mock_sock
        self.assertFalse(check_remote_service("127.0.0.1", 11434))

    @patch("model_discovery.socket.socket")
    def test_exception(self, mock_sock_cls):
        mock_sock_cls.side_effect = OSError("fail")
        self.assertFalse(check_remote_service("127.0.0.1", 11434))


class TestFetchOllamaModels(unittest.TestCase):
    @patch("model_discovery.check_remote_service", return_value=False)
    def test_service_unreachable(self, _mock):
        result = fetch_ollama_models("127.0.0.1", 11434)
        self.assertFalse(result["available"])
        self.assertIsNotNone(result["error"])

    @patch("model_discovery.urllib.request.urlopen")
    @patch("model_discovery.check_remote_service", return_value=True)
    def test_success(self, _svc, mock_urlopen):
        tags_resp = MagicMock()
        tags_resp.read.return_value = json.dumps({
            "models": [{"name": "llama3", "size": 4000000000}]
        }).encode()
        tags_resp.__enter__ = lambda s: s
        tags_resp.__exit__ = MagicMock(return_value=False)

        ps_resp = MagicMock()
        ps_resp.read.return_value = json.dumps({
            "models": [{"name": "llama3"}]
        }).encode()
        ps_resp.__enter__ = lambda s: s
        ps_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [tags_resp, ps_resp]

        result = fetch_ollama_models("127.0.0.1", 11434)
        self.assertTrue(result["available"])
        self.assertEqual(len(result["models"]), 1)
        self.assertIn("llama3", result["running_models"])


class TestFetchLlamacppModels(unittest.TestCase):
    @patch("model_discovery.check_remote_service", return_value=False)
    def test_service_unreachable(self, _mock):
        result = fetch_llamacpp_models("127.0.0.1", 8090)
        self.assertFalse(result["available"])
        self.assertIsNotNone(result["error"])


class TestStaticModels(unittest.TestCase):
    def test_static_llamacpp_not_empty(self):
        self.assertTrue(len(STATIC_LLAMACPP_MODELS) > 0)

    def test_static_ollama_not_empty(self):
        self.assertTrue(len(STATIC_OLLAMA_MODELS) > 0)


class TestDiscoverRemoteModels(unittest.TestCase):
    @patch("model_discovery.fetch_llamacpp_models")
    @patch("model_discovery.fetch_ollama_models")
    def test_basic_structure(self, mock_ollama, mock_llamacpp):
        mock_ollama.return_value = {
            "available": False, "models": [], "running_models": [], "error": "offline"
        }
        mock_llamacpp.return_value = {
            "available": False, "models": [], "error": "offline"
        }
        result = discover_remote_models()
        self.assertIn("ollama", result)
        self.assertIn("llamacpp", result)
        self.assertIn("recommendations", result)
        self.assertIn("timestamp", result)


if __name__ == "__main__":
    unittest.main()
