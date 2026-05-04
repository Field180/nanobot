"""
MCP (Model Context Protocol) Client for Nanobot
================================================
Manages connections to external MCP servers, exposing their tools
and resources to the Nanobot tool registry.

Transports:
  - **stdio** (default): JSON-RPC 2.0 over stdin/stdout subprocess
  - **http** (P13): JSON-RPC 2.0 over HTTP POST (remote servers)

Protocol: JSON-RPC 2.0 — MCP specification 2024-11-05
Reference: https://modelcontextprotocol.io/specification

Lifecycle:
  1. stdio: Spawn subprocess | http: Create HTTP client
  2. Send `initialize` request → receive server capabilities
  3. Send `initialized` notification
  4. Use `tools/list`, `tools/call`, `resources/list`, `resources/read`
  5. stdio: Close stdin → process exits | http: Close client

Error handling:
  - Connection failures return structured errors (never crash the agent)
  - Subprocess crashes / HTTP errors are detected and reported
  - Timeouts on tool calls default to 30s (configurable)
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("nanobot.mcp")

# ── Constants ─────────────────────────────────────────────────
MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_CLIENT_NAME = "nanobot"
MCP_CLIENT_VERSION = "1.0.0"
DEFAULT_TOOL_TIMEOUT = 30  # seconds
DEFAULT_INIT_TIMEOUT = 10  # seconds


# ── JSON-RPC helpers ──────────────────────────────────────────

_next_id = 0


def _make_request(method: str, params: Optional[dict] = None) -> dict:
    """Build a JSON-RPC 2.0 request."""
    global _next_id
    _next_id += 1
    msg = {"jsonrpc": "2.0", "id": _next_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _make_notification(method: str, params: Optional[dict] = None) -> dict:
    """Build a JSON-RPC 2.0 notification (no id → no response expected)."""
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


# ── MCPConnection — single server connection ─────────────────

class MCPConnection:
    """Manages a single MCP server subprocess and JSON-RPC communication."""

    def __init__(
        self,
        name: str,
        command: str,
        args: List[str],
        env: Optional[Dict[str, str]] = None,
        tool_timeout: float = DEFAULT_TOOL_TIMEOUT,
    ):
        self.name = name
        self.command = command
        self.args = args
        self.env = env or {}
        self.tool_timeout = tool_timeout

        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader_lock = asyncio.Lock()
        self._pending: Dict[int, asyncio.Future] = {}
        self._read_task: Optional[asyncio.Task] = None

        # Cached capabilities from initialize
        self.server_info: Dict[str, Any] = {}
        self.server_capabilities: Dict[str, Any] = {}

        # Cached tool and resource lists
        self._tools: List[Dict[str, Any]] = []
        self._resources: List[Dict[str, Any]] = []

        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._process is not None and self._process.returncode is None

    # ── Lifecycle ──

    async def connect(self) -> Tuple[bool, str]:
        """Spawn the MCP server process and complete the initialize handshake.

        Returns (success, error_message).
        """
        if self.connected:
            return True, ""

        # Build environment
        proc_env = dict(os.environ)
        proc_env.update(self.env)

        try:
            self._process = await asyncio.create_subprocess_exec(
                self.command, *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=proc_env,
            )
        except FileNotFoundError:
            msg = f"MCP server '{self.name}': command not found: {self.command}"
            logger.error(msg)
            return False, msg
        except Exception as e:
            msg = f"MCP server '{self.name}': failed to spawn: {e}"
            logger.error(msg)
            return False, msg

        # Start background reader
        self._read_task = asyncio.create_task(self._reader_loop())

        # Send initialize
        try:
            resp = await self._request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": MCP_CLIENT_NAME,
                        "version": MCP_CLIENT_VERSION,
                    },
                },
                timeout=DEFAULT_INIT_TIMEOUT,
            )
        except Exception as e:
            msg = f"MCP server '{self.name}': initialize failed: {e}"
            logger.error(msg)
            await self.disconnect()
            return False, msg

        if "error" in resp:
            msg = f"MCP server '{self.name}': initialize error: {resp['error']}"
            logger.error(msg)
            await self.disconnect()
            return False, msg

        result = resp.get("result", {})
        self.server_info = result.get("serverInfo", {})
        self.server_capabilities = result.get("capabilities", {})

        # Send initialized notification
        await self._notify("notifications/initialized")

        self._connected = True
        logger.info(
            f"[MCP] Connected to '{self.name}' "
            f"(server: {self.server_info.get('name', '?')} "
            f"v{self.server_info.get('version', '?')})"
        )

        # Pre-fetch tools and resources
        await self._refresh_tools()
        await self._refresh_resources()

        return True, ""

    async def disconnect(self):
        """Gracefully shut down the MCP server subprocess."""
        self._connected = False

        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._process:
            try:
                if self._process.stdin and not self._process.stdin.is_closing():
                    self._process.stdin.close()
                # Give it a moment to exit gracefully
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            except Exception as e:
                logger.warning(f"[MCP] Error shutting down '{self.name}': {e}")
            self._process = None

        # Resolve any pending futures
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("MCP server disconnected"))
        self._pending.clear()

        logger.info(f"[MCP] Disconnected from '{self.name}'")

    # ── Public API ──

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Return cached tool definitions from the MCP server."""
        if not self.connected:
            return []
        return list(self._tools)

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a tool on the MCP server.

        Returns: {"success": bool, "output": str, "error": str}
        """
        if not self.connected:
            return {
                "success": False,
                "output": "",
                "error": f"MCP server '{self.name}' is not connected.",
            }

        try:
            resp = await self._request(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                timeout=self.tool_timeout,
            )
        except asyncio.TimeoutError:
            return {
                "success": False,
                "output": "",
                "error": f"MCP tool '{tool_name}' on '{self.name}' timed out after {self.tool_timeout}s.",
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": f"MCP tool '{tool_name}' on '{self.name}' error: {e}",
            }

        if "error" in resp:
            err = resp["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return {
                "success": False,
                "output": "",
                "error": f"MCP tool '{tool_name}' error: {err_msg}",
            }

        result = resp.get("result", {})
        # MCP tool results have `content` array with {type, text} items
        content_parts = result.get("content", [])
        text_parts = []
        for part in content_parts:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "image":
                    text_parts.append(f"[Image: {part.get('mimeType', 'image')}]")
                else:
                    text_parts.append(str(part))
            else:
                text_parts.append(str(part))

        output = "\n".join(text_parts) if text_parts else str(result)
        is_error = result.get("isError", False)

        return {
            "success": not is_error,
            "output": output,
            "error": output if is_error else "",
        }

    async def list_resources(self) -> List[Dict[str, Any]]:
        """Return cached resource definitions from the MCP server."""
        if not self.connected:
            return []
        return list(self._resources)

    async def read_resource(self, uri: str) -> Dict[str, Any]:
        """Read a resource from the MCP server.

        Returns: {"success": bool, "content": str, "mimeType": str, "error": str}
        """
        if not self.connected:
            return {
                "success": False,
                "content": "",
                "mimeType": "",
                "error": f"MCP server '{self.name}' is not connected.",
            }

        try:
            resp = await self._request(
                "resources/read",
                {"uri": uri},
                timeout=self.tool_timeout,
            )
        except asyncio.TimeoutError:
            return {
                "success": False,
                "content": "",
                "mimeType": "",
                "error": f"MCP resource '{uri}' read timed out.",
            }
        except Exception as e:
            return {
                "success": False,
                "content": "",
                "mimeType": "",
                "error": f"MCP resource '{uri}' error: {e}",
            }

        if "error" in resp:
            err = resp["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return {
                "success": False,
                "content": "",
                "mimeType": "",
                "error": f"MCP resource error: {err_msg}",
            }

        result = resp.get("result", {})
        contents = result.get("contents", [])
        if contents:
            first = contents[0]
            return {
                "success": True,
                "content": first.get("text", first.get("blob", "")),
                "mimeType": first.get("mimeType", "text/plain"),
                "error": "",
            }
        return {
            "success": True,
            "content": "",
            "mimeType": "",
            "error": "",
        }

    # ── Internal: JSON-RPC transport ──

    async def _send(self, msg: dict):
        """Write a JSON-RPC message to the subprocess stdin."""
        if not self._process or not self._process.stdin:
            raise ConnectionError(f"MCP server '{self.name}' stdin not available")
        data = json.dumps(msg) + "\n"
        self._process.stdin.write(data.encode("utf-8"))
        await self._process.stdin.drain()

    async def _request(self, method: str, params: Optional[dict] = None, timeout: float = 30) -> dict:
        """Send a request and wait for the matching response."""
        msg = _make_request(method, params)
        req_id = msg["id"]

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[req_id] = fut

        try:
            await self._send(msg)
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise
        except Exception:
            self._pending.pop(req_id, None)
            raise

    async def _notify(self, method: str, params: Optional[dict] = None):
        """Send a notification (no response expected)."""
        msg = _make_notification(method, params)
        await self._send(msg)

    async def _reader_loop(self):
        """Background task: read JSON-RPC responses from stdout and dispatch."""
        if not self._process or not self._process.stdout:
            return
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break  # EOF — process exited
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f"[MCP] '{self.name}' non-JSON output: {line[:200]}")
                    continue

                # Response to a pending request
                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(msg)
                # Server-initiated notification — log and ignore
                elif "method" in msg and "id" not in msg:
                    logger.debug(f"[MCP] '{self.name}' notification: {msg.get('method')}")
                else:
                    logger.debug(f"[MCP] '{self.name}' unhandled message: {str(msg)[:200]}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"[MCP] '{self.name}' reader error: {e}")
        finally:
            # Mark connection as dead
            if self._connected:
                self._connected = False
                logger.warning(f"[MCP] '{self.name}' reader loop exited — server disconnected")

    async def _refresh_tools(self):
        """Fetch the tool list from the server and cache it."""
        if not self.server_capabilities.get("tools"):
            self._tools = []
            return
        try:
            resp = await self._request("tools/list", timeout=DEFAULT_INIT_TIMEOUT)
            self._tools = resp.get("result", {}).get("tools", [])
            logger.info(f"[MCP] '{self.name}': {len(self._tools)} tools available")
        except Exception as e:
            logger.warning(f"[MCP] '{self.name}': failed to list tools: {e}")
            self._tools = []

    async def _refresh_resources(self):
        """Fetch the resource list from the server and cache it."""
        if not self.server_capabilities.get("resources"):
            self._resources = []
            return
        try:
            resp = await self._request("resources/list", timeout=DEFAULT_INIT_TIMEOUT)
            self._resources = resp.get("result", {}).get("resources", [])
            logger.info(f"[MCP] '{self.name}': {len(self._resources)} resources available")
        except Exception as e:
            logger.warning(f"[MCP] '{self.name}': failed to list resources: {e}")
            self._resources = []


# ── MCPHttpConnection — HTTP/SSE transport ───────────────────

class MCPHttpConnection:
    """Manages a single MCP server connection over HTTP transport.

    Implements the same public API as MCPConnection but communicates
    via HTTP POST (JSON-RPC) instead of subprocess stdio.

    Compatible with MCP specification 2024-11-05 HTTP+SSE transport:
      - Requests: POST {url} with JSON-RPC body
      - Notifications: POST {url} (fire-and-forget)
      - SSE: not implemented (optional, for server-initiated pushes)
    """

    def __init__(
        self,
        name: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        tool_timeout: float = DEFAULT_TOOL_TIMEOUT,
    ):
        self.name = name
        self.url = url.rstrip("/")
        self.headers = headers or {}
        self.tool_timeout = tool_timeout

        self._client = None  # httpx.AsyncClient, created on connect
        self._connected = False

        # Cached capabilities from initialize
        self.server_info: Dict[str, Any] = {}
        self.server_capabilities: Dict[str, Any] = {}

        # Cached tool and resource lists
        self._tools: List[Dict[str, Any]] = []
        self._resources: List[Dict[str, Any]] = []

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None

    # ── Lifecycle ──

    async def connect(self) -> Tuple[bool, str]:
        """Connect to the MCP server over HTTP and complete the initialize handshake.

        Returns (success, error_message).
        """
        if self.connected:
            return True, ""

        try:
            import httpx
        except ImportError:
            msg = f"MCP server '{self.name}': httpx not installed (required for HTTP transport)"
            logger.error(msg)
            return False, msg

        try:
            self._client = httpx.AsyncClient(
                headers={
                    "Content-Type": "application/json",
                    **self.headers,
                },
                timeout=httpx.Timeout(self.tool_timeout, connect=10.0),
            )
        except Exception as e:
            msg = f"MCP server '{self.name}': failed to create HTTP client: {e}"
            logger.error(msg)
            return False, msg

        # Send initialize
        try:
            resp = await self._request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": MCP_CLIENT_NAME,
                        "version": MCP_CLIENT_VERSION,
                    },
                },
                timeout=DEFAULT_INIT_TIMEOUT,
            )
        except Exception as e:
            msg = f"MCP server '{self.name}': initialize failed: {e}"
            logger.error(msg)
            await self.disconnect()
            return False, msg

        if "error" in resp:
            msg = f"MCP server '{self.name}': initialize error: {resp['error']}"
            logger.error(msg)
            await self.disconnect()
            return False, msg

        result = resp.get("result", {})
        self.server_info = result.get("serverInfo", {})
        self.server_capabilities = result.get("capabilities", {})

        # Send initialized notification
        await self._notify("notifications/initialized")

        self._connected = True
        logger.info(
            f"[MCP] Connected to '{self.name}' via HTTP "
            f"(server: {self.server_info.get('name', '?')} "
            f"v{self.server_info.get('version', '?')})"
        )

        # Pre-fetch tools and resources
        await self._refresh_tools()
        await self._refresh_resources()

        return True, ""

    async def disconnect(self):
        """Close the HTTP client."""
        self._connected = False
        if self._client:
            try:
                await self._client.aclose()
            except Exception as e:
                logger.warning(f"[MCP] Error closing HTTP client for '{self.name}': {e}")
            self._client = None
        logger.info(f"[MCP] Disconnected from '{self.name}' (HTTP)")

    # ── Public API (same interface as MCPConnection) ──

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Return cached tool definitions from the MCP server."""
        if not self.connected:
            return []
        return list(self._tools)

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a tool on the MCP server.

        Returns: {"success": bool, "output": str, "error": str}
        """
        if not self.connected:
            return {
                "success": False,
                "output": "",
                "error": f"MCP server '{self.name}' is not connected.",
            }

        try:
            resp = await self._request(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                timeout=self.tool_timeout,
            )
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": f"MCP tool '{tool_name}' on '{self.name}' error: {e}",
            }

        if "error" in resp:
            err = resp["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return {
                "success": False,
                "output": "",
                "error": f"MCP tool '{tool_name}' error: {err_msg}",
            }

        result = resp.get("result", {})
        content_parts = result.get("content", [])
        text_parts = []
        for part in content_parts:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "image":
                    text_parts.append(f"[Image: {part.get('mimeType', 'image')}]")
                else:
                    text_parts.append(str(part))
            else:
                text_parts.append(str(part))

        output = "\n".join(text_parts) if text_parts else str(result)
        is_error = result.get("isError", False)

        return {
            "success": not is_error,
            "output": output,
            "error": output if is_error else "",
        }

    async def list_resources(self) -> List[Dict[str, Any]]:
        """Return cached resource definitions from the MCP server."""
        if not self.connected:
            return []
        return list(self._resources)

    async def read_resource(self, uri: str) -> Dict[str, Any]:
        """Read a resource from the MCP server."""
        if not self.connected:
            return {
                "success": False,
                "content": "",
                "mimeType": "",
                "error": f"MCP server '{self.name}' is not connected.",
            }

        try:
            resp = await self._request(
                "resources/read",
                {"uri": uri},
                timeout=self.tool_timeout,
            )
        except Exception as e:
            return {
                "success": False,
                "content": "",
                "mimeType": "",
                "error": f"MCP resource '{uri}' error: {e}",
            }

        if "error" in resp:
            err = resp["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return {
                "success": False,
                "content": "",
                "mimeType": "",
                "error": f"MCP resource error: {err_msg}",
            }

        result = resp.get("result", {})
        contents = result.get("contents", [])
        if contents:
            first = contents[0]
            return {
                "success": True,
                "content": first.get("text", first.get("blob", "")),
                "mimeType": first.get("mimeType", "text/plain"),
                "error": "",
            }
        return {
            "success": True,
            "content": "",
            "mimeType": "",
            "error": "",
        }

    # ── Internal: JSON-RPC over HTTP ──

    async def _request(self, method: str, params: Optional[dict] = None, timeout: float = 30) -> dict:
        """Send a JSON-RPC request via HTTP POST and return the response."""
        if not self._client:
            raise ConnectionError(f"MCP server '{self.name}' HTTP client not available")

        msg = _make_request(method, params)
        try:
            response = await self._client.post(
                self.url,
                json=msg,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise ConnectionError(f"HTTP request to '{self.name}' failed: {e}")

    async def _notify(self, method: str, params: Optional[dict] = None):
        """Send a JSON-RPC notification via HTTP POST (fire-and-forget)."""
        if not self._client:
            return
        msg = _make_notification(method, params)
        try:
            await self._client.post(self.url, json=msg, timeout=5)
        except Exception as e:
            logger.debug(f"[MCP] '{self.name}' notification failed (non-fatal): {e}")

    async def _refresh_tools(self):
        """Fetch the tool list from the server and cache it."""
        if not self.server_capabilities.get("tools"):
            self._tools = []
            return
        try:
            resp = await self._request("tools/list", timeout=DEFAULT_INIT_TIMEOUT)
            self._tools = resp.get("result", {}).get("tools", [])
            logger.info(f"[MCP] '{self.name}' (HTTP): {len(self._tools)} tools available")
        except Exception as e:
            logger.warning(f"[MCP] '{self.name}' (HTTP): failed to list tools: {e}")
            self._tools = []

    async def _refresh_resources(self):
        """Fetch the resource list from the server and cache it."""
        if not self.server_capabilities.get("resources"):
            self._resources = []
            return
        try:
            resp = await self._request("resources/list", timeout=DEFAULT_INIT_TIMEOUT)
            self._resources = resp.get("result", {}).get("resources", [])
            logger.info(f"[MCP] '{self.name}' (HTTP): {len(self._resources)} resources available")
        except Exception as e:
            logger.warning(f"[MCP] '{self.name}' (HTTP): failed to list resources: {e}")
            self._resources = []


# ══════════════════════════════════════════════════════════════
# MCPManager — orchestrates multiple MCP server connections
# ══════════════════════════════════════════════════════════════

# Module-level singleton
_manager: Optional["MCPManager"] = None


class MCPManager:
    """Manages all MCP server connections for a Nanobot instance.

    Usage:
        manager = MCPManager()
        await manager.load_config(workspace)
        await manager.connect_all()
        tools = manager.get_all_tool_definitions()  # OpenAI function-calling schema
        result = await manager.call_tool("mcp__server__tool", arguments)
        await manager.shutdown()
    """

    # MCP tool names are prefixed to avoid collisions with built-in tools.
    # Format: mcp__{server_name}__{tool_name}
    TOOL_PREFIX = "mcp__"

    def __init__(self):
        self.connections: Dict[str, MCPConnection] = {}
        self._config: Dict[str, Any] = {}
        self._tool_map: Dict[str, Tuple[str, str]] = {}  # canonical_name → (server_name, mcp_tool_name)

    def load_config(self, workspace: Path) -> Dict[str, Any]:
        """Load MCP server configuration from nanobot_mcp.json.

        Searches in order:
          1. {workspace}/nanobot_mcp.json
          2. ~/.nanobot/nanobot_mcp.json

        Returns the loaded config dict (empty if no config file found).
        """
        candidates = [
            workspace / "nanobot_mcp.json",
            Path.home() / ".nanobot" / "nanobot_mcp.json",
        ]
        for path in candidates:
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        self._config = json.load(f)
                    logger.info(f"[MCP] Loaded config from {path}")
                    return self._config
                except Exception as e:
                    logger.error(f"[MCP] Failed to parse {path}: {e}")
                    self._config = {}
                    return self._config

        logger.info("[MCP] No nanobot_mcp.json found — MCP disabled")
        self._config = {}
        return self._config

    async def connect_all(self) -> Dict[str, str]:
        """Connect to all configured (non-disabled) MCP servers.

        Returns: {server_name: error_message} for servers that failed.
        """
        servers = self._config.get("mcpServers", {})
        errors: Dict[str, str] = {}

        for name, cfg in servers.items():
            if cfg.get("disabled", False):
                logger.info(f"[MCP] Skipping disabled server: {name}")
                continue

            transport = cfg.get("transport", "stdio")
            timeout = cfg.get("toolTimeout", DEFAULT_TOOL_TIMEOUT)

            if transport == "http":
                url = cfg.get("url", "")
                if not url:
                    errors[name] = "No url specified for HTTP transport"
                    continue
                headers = cfg.get("headers", {})
                conn = MCPHttpConnection(
                    name=name,
                    url=url,
                    headers=headers,
                    tool_timeout=timeout,
                )
            else:
                # Default: stdio transport
                command = cfg.get("command", "")
                args = cfg.get("args", [])
                env = cfg.get("env", {})
                if not command:
                    errors[name] = "No command specified"
                    continue
                conn = MCPConnection(
                    name=name,
                    command=command,
                    args=args,
                    env=env,
                    tool_timeout=timeout,
                )

            ok, err = await conn.connect()
            if ok:
                self.connections[name] = conn
            else:
                errors[name] = err

        # Rebuild tool map
        self._rebuild_tool_map()
        return errors

    async def shutdown(self):
        """Disconnect all MCP servers."""
        for conn in self.connections.values():
            await conn.disconnect()
        self.connections.clear()
        self._tool_map.clear()
        logger.info("[MCP] All servers shut down")

    # ── Tool integration ──

    # P16-audit: Only allow safe identifiers in tool names to prevent injection
    _SAFE_NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_\-]{0,63}$')
    _MAX_DESC_LEN = 150  # Truncate MCP descriptions to prevent prompt injection

    @staticmethod
    def _sanitize_description(desc: str) -> str:
        """Sanitize MCP tool/resource description to prevent prompt injection."""
        desc = re.sub(r'[\r\n\t]+', ' ', desc)
        desc = re.sub(r'  +', ' ', desc).strip()
        if len(desc) > MCPManager._MAX_DESC_LEN:
            desc = desc[:MCPManager._MAX_DESC_LEN - 3] + '...'
        return desc

    def _rebuild_tool_map(self):
        """Build the canonical_name → (server, mcp_name) mapping from all connections."""
        self._tool_map.clear()
        for server_name, conn in self.connections.items():
            if not self._SAFE_NAME_RE.match(server_name):
                logger.warning("[MCP] Skipping server with unsafe name: %r", server_name)
                continue
            for tool in conn._tools:
                mcp_name = tool.get("name", "")
                if not mcp_name:
                    continue
                if not self._SAFE_NAME_RE.match(mcp_name):
                    logger.warning("[MCP] Skipping tool with unsafe name: %r (server=%s)", mcp_name, server_name)
                    continue
                canonical = f"{self.TOOL_PREFIX}{server_name}__{mcp_name}"
                self._tool_map[canonical] = (server_name, mcp_name)

    def get_all_tool_definitions(self) -> List[Dict[str, Any]]:
        """Convert all MCP tools to OpenAI function-calling schema.

        Each tool is prefixed as mcp__{server}__{name} to avoid collisions.
        """
        definitions = []
        for server_name, conn in self.connections.items():
            for tool in conn._tools:
                mcp_name = tool.get("name", "")
                if not mcp_name:
                    continue
                canonical = f"{self.TOOL_PREFIX}{server_name}__{mcp_name}"
                description = tool.get("description", f"MCP tool from {server_name}")
                description = self._sanitize_description(description)
                # Annotate with server origin
                description = f"[MCP: {server_name}] {description}"

                input_schema = tool.get("inputSchema", {"type": "object", "properties": {}})
                # Sanitize parameter descriptions (deep defense)
                props = input_schema.get("properties", {})
                if props:
                    for pname, pdef in props.items():
                        if "description" in pdef:
                            pdef["description"] = self._sanitize_description(pdef["description"])

                definitions.append({
                    "type": "function",
                    "function": {
                        "name": canonical,
                        "description": description,
                        "parameters": input_schema,
                    },
                })
        return definitions

    def get_canonical_name(self, name: str) -> Optional[str]:
        """If name is an MCP tool (starts with mcp__), return the canonical name, else None."""
        if name.startswith(self.TOOL_PREFIX) and name in self._tool_map:
            return name
        return None

    def is_mcp_tool(self, name: str) -> bool:
        """Check if a tool name belongs to an MCP server."""
        return name.startswith(self.TOOL_PREFIX) and name in self._tool_map

    async def call_tool(self, canonical_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Route a tool call to the correct MCP server.

        Returns: {"success": bool, "output": str, "error": str}
        """
        entry = self._tool_map.get(canonical_name)
        if not entry:
            return {
                "success": False,
                "output": "",
                "error": f"Unknown MCP tool: {canonical_name}",
            }
        server_name, mcp_tool_name = entry
        conn = self.connections.get(server_name)
        if not conn or not conn.connected:
            return {
                "success": False,
                "output": "",
                "error": f"MCP server '{server_name}' is not connected.",
            }
        return await conn.call_tool(mcp_tool_name, arguments)

    # ── Resource integration ──

    def get_all_resources(self) -> List[Dict[str, Any]]:
        """Return all resources from all connected servers, tagged with server name."""
        resources = []
        for server_name, conn in self.connections.items():
            for res in conn._resources:
                resources.append({
                    "server": server_name,
                    "uri": res.get("uri", ""),
                    "name": self._sanitize_description(res.get("name", "")),
                    "description": self._sanitize_description(res.get("description", "")),
                    "mimeType": res.get("mimeType", ""),
                })
        return resources

    async def read_resource(self, uri: str) -> Dict[str, Any]:
        """Read a resource by URI, searching across all connected servers."""
        for conn in self.connections.values():
            for res in conn._resources:
                if res.get("uri") == uri:
                    return await conn.read_resource(uri)
        return {
            "success": False,
            "content": "",
            "mimeType": "",
            "error": f"Resource not found: {uri}",
        }

    async def read_all_resources_content(self, max_total_chars: int = 8000) -> str:
        """Read all resources and format as context string for system prompt injection.

        Returns a formatted string suitable for injection into build_dynamic_context.
        Respects max_total_chars to avoid bloating the context window.
        """
        all_resources = self.get_all_resources()
        if not all_resources:
            return ""

        parts = ["[MCP Resources — external context from connected servers]"]
        total = 0
        for res in all_resources:
            if total >= max_total_chars:
                parts.append(f"... ({len(all_resources) - len(parts) + 1} more resources truncated)")
                break
            result = await self.read_resource(res["uri"])
            if result.get("success") and result.get("content"):
                content = result["content"]
                if total + len(content) > max_total_chars:
                    content = content[:max_total_chars - total] + "\n... (truncated)"
                header = f"\n### {res['name']} ({res['server']}) [{res.get('mimeType', '')}]"
                parts.append(header)
                parts.append(content)
                total += len(content) + len(header)

        if len(parts) <= 1:
            return ""  # No content read successfully
        return "\n".join(parts)

    # ── Status ──

    def get_status(self) -> Dict[str, Any]:
        """Return connection status for all configured servers."""
        servers = self._config.get("mcpServers", {})
        status = {}
        for name in servers:
            conn = self.connections.get(name)
            if conn:
                status[name] = {
                    "connected": conn.connected,
                    "server_info": conn.server_info,
                    "tools": len(conn._tools),
                    "resources": len(conn._resources),
                }
            else:
                status[name] = {
                    "connected": False,
                    "disabled": servers[name].get("disabled", False),
                }
        return status

    def get_tool_count(self) -> int:
        """Return total number of MCP tools across all servers."""
        return len(self._tool_map)

    def get_resource_count(self) -> int:
        """Return total number of MCP resources across all servers."""
        return sum(len(c._resources) for c in self.connections.values())


# ── Module-level access ──────────────────────────────────────

def get_mcp_manager() -> MCPManager:
    """Get or create the module-level MCPManager singleton."""
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager


def reset_mcp_manager():
    """Reset the singleton (for tests)."""
    global _manager
    _manager = None
