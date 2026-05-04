"""Tests for MCP HTTP transport (P13).

Covers:
  - MCPHttpConnection initialization and properties
  - HTTP connect/disconnect lifecycle (mocked httpx)
  - Tool calling over HTTP
  - Resource reading over HTTP
  - MCPManager transport dispatch (stdio vs http)
  - Config validation for HTTP transport
  - Backward compatibility (stdio still works)
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.mcp_client import (
    MCPConnection,
    MCPHttpConnection,
    MCPManager,
    get_mcp_manager,
    reset_mcp_manager,
    MCP_PROTOCOL_VERSION,
    MCP_CLIENT_NAME,
    DEFAULT_TOOL_TIMEOUT,
)


# ── MCPHttpConnection unit tests ─────────────────────────────

class TestMCPHttpConnectionInit(unittest.TestCase):
    """Test MCPHttpConnection initialization and default state."""

    def test_default_state(self):
        conn = MCPHttpConnection("test", "http://localhost:3000/mcp")
        self.assertEqual(conn.name, "test")
        self.assertEqual(conn.url, "http://localhost:3000/mcp")
        self.assertFalse(conn.connected)
        self.assertEqual(conn._tools, [])
        self.assertEqual(conn._resources, [])
        self.assertIsNone(conn._client)

    def test_url_trailing_slash_stripped(self):
        conn = MCPHttpConnection("test", "http://localhost:3000/mcp/")
        self.assertEqual(conn.url, "http://localhost:3000/mcp")

    def test_custom_headers(self):
        conn = MCPHttpConnection("test", "http://localhost:3000",
                                 headers={"Authorization": "Bearer sk-xxx"})
        self.assertEqual(conn.headers["Authorization"], "Bearer sk-xxx")

    def test_custom_timeout(self):
        conn = MCPHttpConnection("test", "http://localhost:3000", tool_timeout=60)
        self.assertEqual(conn.tool_timeout, 60)

    def test_not_connected_list_tools_empty(self):
        conn = MCPHttpConnection("test", "http://localhost:3000")
        result = asyncio.run(conn.list_tools())
        self.assertEqual(result, [])

    def test_not_connected_call_tool_error(self):
        conn = MCPHttpConnection("test", "http://localhost:3000")
        result = asyncio.run(conn.call_tool("test_tool", {}))
        self.assertFalse(result["success"])
        self.assertIn("not connected", result["error"])

    def test_not_connected_read_resource_error(self):
        conn = MCPHttpConnection("test", "http://localhost:3000")
        result = asyncio.run(conn.read_resource("test://res"))
        self.assertFalse(result["success"])
        self.assertIn("not connected", result["error"])

    def test_not_connected_list_resources_empty(self):
        conn = MCPHttpConnection("test", "http://localhost:3000")
        result = asyncio.run(conn.list_resources())
        self.assertEqual(result, [])


class TestMCPHttpConnectionConnect(unittest.TestCase):
    """Test MCPHttpConnection.connect() with mocked httpx."""

    def _make_mock_response(self, json_data, status_code=200):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data
        resp.raise_for_status = MagicMock()
        if status_code >= 400:
            resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
        return resp

    def test_connect_success(self):
        """Successful initialize handshake over HTTP."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": {"name": "test-server", "version": "1.0"},
                "capabilities": {},
            },
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=self._make_mock_response(init_response))
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("httpx.Timeout"):
                conn = MCPHttpConnection("test", "http://localhost:3000/mcp")
                ok, err = asyncio.run(conn.connect())

        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertTrue(conn.connected)
        self.assertEqual(conn.server_info["name"], "test-server")

    def test_connect_server_error(self):
        """Initialize returns JSON-RPC error."""
        error_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "Invalid request"},
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=self._make_mock_response(error_response))
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("httpx.Timeout"):
                conn = MCPHttpConnection("test", "http://localhost:3000/mcp")
                ok, err = asyncio.run(conn.connect())

        self.assertFalse(ok)
        self.assertIn("initialize error", err)

    def test_connect_http_failure(self):
        """HTTP request itself fails (network error)."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("Connection refused"))
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("httpx.Timeout"):
                conn = MCPHttpConnection("test", "http://localhost:3000/mcp")
                ok, err = asyncio.run(conn.connect())

        self.assertFalse(ok)
        self.assertIn("initialize failed", err)

    def test_disconnect(self):
        """Disconnect closes the HTTP client."""
        conn = MCPHttpConnection("test", "http://localhost:3000")
        conn._client = AsyncMock()
        conn._client.aclose = AsyncMock()
        conn._connected = True

        asyncio.run(conn.disconnect())
        self.assertFalse(conn.connected)
        self.assertIsNone(conn._client)


class TestMCPHttpConnectionTools(unittest.TestCase):
    """Test tool calling over HTTP transport."""

    def _make_connected_conn(self):
        conn = MCPHttpConnection("test", "http://localhost:3000")
        conn._client = AsyncMock()
        conn._connected = True
        return conn

    def _make_mock_response(self, json_data):
        resp = MagicMock()
        resp.json.return_value = json_data
        resp.raise_for_status = MagicMock()
        return resp

    def test_call_tool_success(self):
        conn = self._make_connected_conn()
        tool_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "Hello, world!"}],
                "isError": False,
            },
        }
        conn._client.post = AsyncMock(return_value=self._make_mock_response(tool_response))

        result = asyncio.run(conn.call_tool("greet", {"name": "world"}))
        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "Hello, world!")
        self.assertEqual(result["error"], "")

    def test_call_tool_error_response(self):
        conn = self._make_connected_conn()
        error_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -1, "message": "Tool not found"},
        }
        conn._client.post = AsyncMock(return_value=self._make_mock_response(error_response))

        result = asyncio.run(conn.call_tool("missing", {}))
        self.assertFalse(result["success"])
        self.assertIn("Tool not found", result["error"])

    def test_call_tool_is_error_flag(self):
        conn = self._make_connected_conn()
        tool_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "Execution failed"}],
                "isError": True,
            },
        }
        conn._client.post = AsyncMock(return_value=self._make_mock_response(tool_response))

        result = asyncio.run(conn.call_tool("failing_tool", {}))
        self.assertFalse(result["success"])
        self.assertIn("Execution failed", result["error"])

    def test_call_tool_http_exception(self):
        conn = self._make_connected_conn()
        conn._client.post = AsyncMock(side_effect=Exception("timeout"))

        result = asyncio.run(conn.call_tool("slow_tool", {}))
        self.assertFalse(result["success"])
        self.assertIn("error", result["error"].lower())

    def test_call_tool_image_content(self):
        conn = self._make_connected_conn()
        tool_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "image", "mimeType": "image/png"}],
                "isError": False,
            },
        }
        conn._client.post = AsyncMock(return_value=self._make_mock_response(tool_response))

        result = asyncio.run(conn.call_tool("screenshot", {}))
        self.assertTrue(result["success"])
        self.assertIn("Image", result["output"])


class TestMCPHttpConnectionResources(unittest.TestCase):
    """Test resource reading over HTTP transport."""

    def _make_connected_conn(self):
        conn = MCPHttpConnection("test", "http://localhost:3000")
        conn._client = AsyncMock()
        conn._connected = True
        return conn

    def _make_mock_response(self, json_data):
        resp = MagicMock()
        resp.json.return_value = json_data
        resp.raise_for_status = MagicMock()
        return resp

    def test_read_resource_success(self):
        conn = self._make_connected_conn()
        resource_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "contents": [{"text": "Resource content here", "mimeType": "text/plain"}],
            },
        }
        conn._client.post = AsyncMock(return_value=self._make_mock_response(resource_response))

        result = asyncio.run(conn.read_resource("test://doc"))
        self.assertTrue(result["success"])
        self.assertEqual(result["content"], "Resource content here")
        self.assertEqual(result["mimeType"], "text/plain")

    def test_read_resource_empty(self):
        conn = self._make_connected_conn()
        resource_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"contents": []},
        }
        conn._client.post = AsyncMock(return_value=self._make_mock_response(resource_response))

        result = asyncio.run(conn.read_resource("test://empty"))
        self.assertTrue(result["success"])
        self.assertEqual(result["content"], "")


# ── MCPManager transport dispatch tests ──────────────────────

class TestMCPManagerHttpDispatch(unittest.TestCase):
    """Test that MCPManager creates the right connection type based on transport."""

    def setUp(self):
        reset_mcp_manager()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_http_config_creates_http_connection(self):
        """Manager should create MCPHttpConnection for transport=http."""
        config = {
            "mcpServers": {
                "remote-server": {
                    "transport": "http",
                    "url": "http://localhost:3000/mcp",
                    "headers": {"Authorization": "Bearer test-token"},
                }
            }
        }
        config_path = Path(self.tmpdir) / "nanobot_mcp.json"
        with open(config_path, "w") as f:
            json.dump(config, f)

        mgr = MCPManager()
        mgr.load_config(Path(self.tmpdir))

        # Mock the connect to avoid real HTTP call
        with patch.object(MCPHttpConnection, "connect", new_callable=AsyncMock,
                          return_value=(True, "")):
            errors = asyncio.run(mgr.connect_all())

        self.assertEqual(errors, {})
        self.assertIn("remote-server", mgr.connections)
        self.assertIsInstance(mgr.connections["remote-server"], MCPHttpConnection)

    def test_stdio_config_creates_stdio_connection(self):
        """Manager should create MCPConnection for transport=stdio (or default)."""
        config = {
            "mcpServers": {
                "local-server": {
                    "command": "echo",
                    "args": ["hello"],
                }
            }
        }
        config_path = Path(self.tmpdir) / "nanobot_mcp.json"
        with open(config_path, "w") as f:
            json.dump(config, f)

        mgr = MCPManager()
        mgr.load_config(Path(self.tmpdir))

        with patch.object(MCPConnection, "connect", new_callable=AsyncMock,
                          return_value=(True, "")):
            errors = asyncio.run(mgr.connect_all())

        self.assertEqual(errors, {})
        self.assertIn("local-server", mgr.connections)
        self.assertIsInstance(mgr.connections["local-server"], MCPConnection)

    def test_http_missing_url_error(self):
        """HTTP transport without url should report error."""
        config = {
            "mcpServers": {
                "bad-http": {
                    "transport": "http",
                }
            }
        }
        config_path = Path(self.tmpdir) / "nanobot_mcp.json"
        with open(config_path, "w") as f:
            json.dump(config, f)

        mgr = MCPManager()
        mgr.load_config(Path(self.tmpdir))
        errors = asyncio.run(mgr.connect_all())

        self.assertIn("bad-http", errors)
        self.assertIn("url", errors["bad-http"].lower())

    def test_mixed_transports(self):
        """Manager should handle mixed stdio + http configs."""
        config = {
            "mcpServers": {
                "local": {
                    "command": "echo",
                    "args": [],
                },
                "remote": {
                    "transport": "http",
                    "url": "http://example.com/mcp",
                },
            }
        }
        config_path = Path(self.tmpdir) / "nanobot_mcp.json"
        with open(config_path, "w") as f:
            json.dump(config, f)

        mgr = MCPManager()
        mgr.load_config(Path(self.tmpdir))

        with patch.object(MCPConnection, "connect", new_callable=AsyncMock,
                          return_value=(True, "")):
            with patch.object(MCPHttpConnection, "connect", new_callable=AsyncMock,
                              return_value=(True, "")):
                errors = asyncio.run(mgr.connect_all())

        self.assertEqual(errors, {})
        self.assertIsInstance(mgr.connections["local"], MCPConnection)
        self.assertIsInstance(mgr.connections["remote"], MCPHttpConnection)

    def test_http_with_custom_timeout(self):
        """HTTP connection should respect toolTimeout from config."""
        config = {
            "mcpServers": {
                "slow-server": {
                    "transport": "http",
                    "url": "http://localhost:3000",
                    "toolTimeout": 120,
                }
            }
        }
        config_path = Path(self.tmpdir) / "nanobot_mcp.json"
        with open(config_path, "w") as f:
            json.dump(config, f)

        mgr = MCPManager()
        mgr.load_config(Path(self.tmpdir))

        with patch.object(MCPHttpConnection, "connect", new_callable=AsyncMock,
                          return_value=(True, "")):
            asyncio.run(mgr.connect_all())

        conn = mgr.connections["slow-server"]
        self.assertEqual(conn.tool_timeout, 120)


# ── Backward compatibility ───────────────────────────────────

class TestBackwardCompat(unittest.TestCase):
    """Ensure existing stdio behavior is unchanged."""

    def test_stdio_connection_class_unchanged(self):
        """MCPConnection constructor still works with positional args."""
        conn = MCPConnection("test", "echo", ["hello"])
        self.assertEqual(conn.name, "test")
        self.assertEqual(conn.command, "echo")
        self.assertEqual(conn.args, ["hello"])

    def test_manager_default_transport_is_stdio(self):
        """Config without transport field defaults to stdio."""
        tmpdir = tempfile.mkdtemp()
        config = {
            "mcpServers": {
                "legacy": {
                    "command": "node",
                    "args": ["server.js"],
                }
            }
        }
        config_path = Path(tmpdir) / "nanobot_mcp.json"
        with open(config_path, "w") as f:
            json.dump(config, f)

        mgr = MCPManager()
        mgr.load_config(Path(tmpdir))

        with patch.object(MCPConnection, "connect", new_callable=AsyncMock,
                          return_value=(True, "")):
            asyncio.run(mgr.connect_all())

        self.assertIsInstance(mgr.connections["legacy"], MCPConnection)

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_http_connection_importable(self):
        """MCPHttpConnection is importable from mcp_client."""
        from tools.mcp_client import MCPHttpConnection as Cls
        self.assertTrue(callable(Cls))


if __name__ == "__main__":
    unittest.main()
