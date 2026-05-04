"""
Unit tests for ssh_remote module (P9 extraction).
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ssh_remote


class TestCmd(unittest.TestCase):
    def test_wraps_with_cmd_c(self):
        self.assertEqual(ssh_remote._cmd("echo hi"), 'cmd /c "echo hi"')


class TestWindowsQuote(unittest.TestCase):
    def test_wraps_with_quotes(self):
        self.assertEqual(ssh_remote._windows_quote("foo"), '"foo"')


class TestRunSshCommand(unittest.TestCase):
    def test_returns_error_on_none_client(self):
        code, out, err = ssh_remote.run_ssh_command(None, "ls")
        self.assertEqual(code, 255)
        self.assertEqual(err, "SSH client is None")

    def test_success(self):
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"hello"
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)

        code, out, err = ssh_remote.run_ssh_command(mock_client, "echo hello")
        self.assertEqual(code, 0)
        self.assertEqual(out, "hello")

    def test_exception(self):
        mock_client = MagicMock()
        mock_client.exec_command.side_effect = RuntimeError("boom")
        code, out, err = ssh_remote.run_ssh_command(mock_client, "fail")
        self.assertEqual(code, 255)
        self.assertIn("boom", err)


class TestSshConnect(unittest.TestCase):
    @patch("ssh_remote.paramiko.SSHClient")
    def test_returns_client_on_success(self, mock_cls):
        instance = mock_cls.return_value
        result = ssh_remote.ssh_connect()
        self.assertEqual(result, instance)
        instance.connect.assert_called_once()

    @patch("ssh_remote.paramiko.SSHClient")
    def test_returns_none_on_failure(self, mock_cls):
        instance = mock_cls.return_value
        instance.connect.side_effect = Exception("fail")
        result = ssh_remote.ssh_connect()
        self.assertIsNone(result)


class TestSshClient(unittest.TestCase):
    @patch("ssh_remote.ssh_connect")
    def test_context_manager_closes(self, mock_connect):
        mock_client = MagicMock()
        mock_connect.return_value = mock_client
        with ssh_remote.ssh_client() as client:
            self.assertEqual(client, mock_client)
        mock_client.close.assert_called_once()

    @patch("ssh_remote.ssh_connect")
    def test_context_manager_none(self, mock_connect):
        mock_connect.return_value = None
        with ssh_remote.ssh_client() as client:
            self.assertIsNone(client)


class TestStopRemoteOllama(unittest.TestCase):
    @patch("ssh_remote.ssh_client")
    def test_returns_false_no_client(self, mock_ctx):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=None)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.return_value = ctx
        self.assertFalse(ssh_remote.stop_remote_ollama())


class TestKillRemoteOllama(unittest.TestCase):
    @patch("ssh_remote.stop_remote_ollama", return_value=True)
    def test_delegates(self, mock_stop):
        self.assertTrue(ssh_remote.kill_remote_ollama())
        mock_stop.assert_called_once()


class TestIsRemoteOllamaRunning(unittest.TestCase):
    @patch("ssh_remote.ssh_client")
    def test_returns_false_on_exception(self, mock_ctx):
        mock_ctx.side_effect = Exception("fail")
        self.assertFalse(ssh_remote.is_remote_ollama_running())


if __name__ == "__main__":
    unittest.main()
