"""
Nanobot Tool Registry
=====================
Collects all tool modules from tools/ and builds:
  - AGENTIC_TOOLS:      OpenAI function-calling schema list
  - TOOL_NAME_ALIASES:   alias → canonical name mapping
  - READONLY_TOOLS:      frozenset of read-only tool names (for concurrency)
  - execute_tool():      dispatch by canonical name
  - _partition_tool_calls(): Claw-style concurrent/serial batching
  - build_tool_guidance(): dynamic system prompt section from GUIDANCE metadata
"""
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools import (
    shell_execute,
    file_read,
    file_write,
    file_edit,
    change_set_accept,
    change_set_reject,
    file_list,
    grep_search,
    find_by_name,
    python_execute,
    web_fetch,
    web_search,
    sub_agent,
    todo_manage,
    memory,
    code_intel,
    ask_user,
    task_manage,
    tool_search,
    send_message,
)

logger = logging.getLogger("nanobot.tools")

# ── MCP integration ──────────────────────────────────────────
from tools.mcp_client import get_mcp_manager, MCPManager

# ── All registered tool modules (order = order in AGENTIC_TOOLS list) ──
_ALL_TOOL_MODULES = [
    shell_execute,
    file_read,
    file_write,
    # change_set_accept / change_set_reject removed from model tools:
    # approval must come from the user via frontend UI buttons (REST API),
    # NOT from the model auto-calling these tools.
    file_list,
    grep_search,
    find_by_name,
    python_execute,
    file_edit,
    web_fetch,
    web_search,
    sub_agent,
    todo_manage,
    memory,
    code_intel,
    ask_user,
    task_manage,
    tool_search,
    send_message,
]

# Change-set approval tools are still importable and dispatchable for REST API use,
# but excluded from AGENTIC_TOOLS so the model cannot auto-accept/reject.
_CHANGE_SET_TOOLS = [change_set_accept, change_set_reject]

# ── Async tools — these have execute_async() and must be awaited ──
ASYNC_TOOLS: frozenset = frozenset(
    _mod.TOOL_DEF["function"]["name"]
    for _mod in _ALL_TOOL_MODULES
    if hasattr(_mod, "execute_async")
)

# ── Build AGENTIC_TOOLS (OpenAI function-calling schema) ──
# Base tools from built-in modules; MCP tools are appended dynamically.
AGENTIC_TOOLS: List[Dict[str, Any]] = [m.TOOL_DEF for m in _ALL_TOOL_MODULES]

# ── Build TOOL_NAME_ALIASES ──
TOOL_NAME_ALIASES: Dict[str, str] = {}
for _mod in _ALL_TOOL_MODULES:
    _canonical = _mod.TOOL_DEF["function"]["name"]
    for _alias in getattr(_mod, "ALIASES", []):
        TOOL_NAME_ALIASES[_alias] = _canonical

# ── Build READONLY_TOOLS ──
READONLY_TOOLS = frozenset(
    _mod.TOOL_DEF["function"]["name"]
    for _mod in _ALL_TOOL_MODULES
    if getattr(_mod, "IS_READONLY", False)
)

# ── Dispatch table ──
# Includes change_set tools for REST API / test use, even though they're
# excluded from AGENTIC_TOOLS (model cannot auto-call them).
_DISPATCH: Dict[str, Any] = {
    _mod.TOOL_DEF["function"]["name"]: _mod.execute
    for _mod in _ALL_TOOL_MODULES + _CHANGE_SET_TOOLS
}

# ── Async dispatch table — for tools with execute_async ──
_ASYNC_DISPATCH: Dict[str, Any] = {
    _mod.TOOL_DEF["function"]["name"]: _mod.execute_async
    for _mod in _ALL_TOOL_MODULES + _CHANGE_SET_TOOLS
    if hasattr(_mod, "execute_async")
}


def _enrich_error_message(tool_name: str, error: str, args: Dict[str, Any]) -> str:
    """P18: Append actionable recovery suggestions to tool error messages.

    Analyzes common error patterns and provides specific next-step advice,
    similar to Claw's error enrichment in tool handlers.
    """
    err_lower = error.lower()
    hints = []

    # File not found
    if "not found" in err_lower or "no such file" in err_lower:
        path = args.get("path", "")
        hints.append(f"Try: file_list or find_by_name to locate the correct path.")
        if path and not path.startswith("/"):
            hints.append(f"The path '{path}' is relative — ensure the workspace directory is correct.")

    # Permission denied
    elif "permission denied" in err_lower or "errno 13" in err_lower:
        hints.append("Check file permissions with: shell_execute command='ls -la <path>'")

    # Not a file / not a directory
    elif "not a file" in err_lower:
        hints.append("Use file_list to inspect the path — it may be a directory.")
    elif "not a directory" in err_lower:
        hints.append("Use file_read to read it — it may be a file, not a directory.")

    # File too large
    elif "too large" in err_lower:
        hints.append("Use offset/limit parameters to read a specific range, e.g. offset=1, limit=200")

    # JSON parse error (common with file_edit arguments)
    elif "json" in err_lower and "decode" in err_lower:
        hints.append("Check that the tool arguments are valid JSON. Escape special characters in strings.")

    # Command timeout
    elif "timed out" in err_lower or "timeout" in err_lower:
        hints.append("The command exceeded the timeout. Try a simpler command or add a timeout flag.")

    # Blocked command
    elif "blocked" in err_lower:
        hints.append("This command was blocked for safety. Use a less destructive alternative.")

    # file_edit: string not found
    elif "not found in file" in err_lower or "0 occurrences" in err_lower:
        hints.append("The old_string was not found. Use file_read first to verify the exact content, including whitespace and indentation.")

    # file_edit: multiple occurrences
    elif "occurrences" in err_lower and tool_name == "file_edit":
        hints.append("Add more surrounding context to old_string to make it unique, or use replace_all=true.")

    # file_edit: not read yet (P11)
    elif "must read" in err_lower or "not been read" in err_lower:
        hints.append("Call file_read on this file first, then retry the edit.")

    # file_edit: modified since read (P11)
    elif "modified since" in err_lower:
        hints.append("The file changed on disk. Call file_read again to get the current content, then retry.")

    if hints:
        return error + "\n💡 " + " | ".join(hints)
    return error


def execute_tool(name: str, arguments: Dict[str, Any], workspace: Path) -> Dict[str, Any]:
    """
    Execute a single tool call and return result.

    Returns: {"success": bool, "output": str, "error": str}
    """
    name = TOOL_NAME_ALIASES.get(name, name)

    # MCP tool routing — delegate to MCPManager
    mgr = get_mcp_manager()
    if mgr.is_mcp_tool(name):
        try:
            coro = mgr.call_tool(name, arguments or {})
            # Detect if we're inside a running event loop
            try:
                _loop = asyncio.get_running_loop()
                _in_async = True
            except RuntimeError:
                _in_async = False

            if _in_async:
                # Inside an async context — run in a thread to avoid deadlock
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(asyncio.run, coro).result(timeout=60)
            else:
                # No running loop — safe to use asyncio.run()
                result = asyncio.run(coro)
            if not result.get("success") and result.get("error"):
                result["error"] = _enrich_error_message(name, result["error"], arguments or {})
            return result
        except Exception as e:
            logger.error(f"[MCP Tool] {name} execution error: {e}")
            return {"success": False, "output": "", "error": f"MCP tool error: {e}"}

    handler = _DISPATCH.get(name)
    if handler is None:
        return {"success": False, "output": "", "error": f"Unknown tool: {name}"}
    arguments = dict(arguments or {})
    # P16 observability: track code_intel diagnose usage
    if name == "code_intel" and arguments.get("action") == "diagnose":
        logger.info(f"[P16] code_intel diagnose called on: {arguments.get('path', '?')}")
    if not arguments.get("_session_id") and not arguments.get("session_id"):
        arguments["_session_id"] = "global"
    try:
        result = handler(arguments, workspace)
        # P18: Enrich error messages with actionable suggestions
        if not result.get("success") and result.get("error"):
            result["error"] = _enrich_error_message(name, result["error"], arguments)
        return result
    except Exception as e:
        logger.error(f"[Tool] {name} execution error: {e}")
        enriched = _enrich_error_message(name, str(e), arguments)
        return {"success": False, "output": "", "error": enriched}


async def execute_tool_async(name: str, arguments: Dict[str, Any], workspace: Path) -> Dict[str, Any]:
    """P36: Execute a tool that has an async handler (e.g. sub_agent).

    Falls back to synchronous execute_tool for non-async tools.
    Returns: {"success": bool, "output": str, "error": str}
    """
    name = TOOL_NAME_ALIASES.get(name, name)

    # MCP tool routing — already async
    mgr = get_mcp_manager()
    if mgr.is_mcp_tool(name):
        try:
            result = await mgr.call_tool(name, arguments or {})
            if not result.get("success") and result.get("error"):
                result["error"] = _enrich_error_message(name, result["error"], arguments or {})
            return result
        except Exception as e:
            logger.error(f"[MCP Tool] {name} async execution error: {e}")
            return {"success": False, "output": "", "error": f"MCP tool error: {e}"}

    async_handler = _ASYNC_DISPATCH.get(name)
    if async_handler is not None:
        try:
            result = await async_handler(arguments, workspace)
            if not result.get("success") and result.get("error"):
                result["error"] = _enrich_error_message(name, result["error"], arguments)
            return result
        except Exception as e:
            logger.error(f"[Tool] {name} async execution error: {e}")
            enriched = _enrich_error_message(name, str(e), arguments)
            return {"success": False, "output": "", "error": enriched}
    # Fallback to sync
    return execute_tool(name, arguments, workspace)


def _partition_tool_calls(
    tool_calls: List[Dict[str, Any]],
) -> List[tuple]:
    """Partition tool calls into batches: consecutive read-only → concurrent, write → serial.

    Mirrors Claw's toolOrchestration.ts partitionToolCalls():
      - Consecutive read-only tools are grouped into one (True, [...]) batch
      - Each write tool gets its own (False, [tc]) batch
      - Mixed sequences produce alternating batches

    Returns: list of (is_concurrent, [tool_call_dicts])
    """
    batches: List[tuple] = []  # (is_concurrent: bool, tcs: list)
    for tc in tool_calls:
        func = tc.get("function", {})
        raw_name = func.get("name", "unknown")
        name = TOOL_NAME_ALIASES.get(raw_name, raw_name)
        is_readonly = name in READONLY_TOOLS

        if batches and batches[-1][0] and is_readonly:
            # Extend existing concurrent batch
            batches[-1][1].append(tc)
        else:
            batches.append((is_readonly, [tc]))
    return batches


# ═══════════════════════════════════════════════════════════════
# Tool Registry Summary — auto-appended by file_list/file_read
# ═══════════════════════════════════════════════════════════════

def tool_registry_summary() -> str:
    """Generate a concise metadata summary of all registered tools.

    Returns a preformatted table showing each tool's name, IS_READONLY,
    ALIASES count, and alias names.  This is auto-appended when file_list
    or file_read targets the tools/ directory, so the model gets complete
    data in one tool call without reading each module individually.
    """
    lines = [
        "",
        "# Tool Registry Metadata (auto-generated from loaded modules)",
        f"{'Tool Name':<20s} {'Readonly':<10s} {'Aliases':<8s} Alias Names",
        f"{'─'*20} {'─'*10} {'─'*8} {'─'*40}",
    ]
    for mod in _ALL_TOOL_MODULES:
        name = mod.TOOL_DEF["function"]["name"]
        readonly = "✅ yes" if getattr(mod, "IS_READONLY", False) else "❌ no"
        aliases = getattr(mod, "ALIASES", [])
        alias_str = ", ".join(aliases) if aliases else "(none)"
        lines.append(f"{name:<20s} {readonly:<10s} {len(aliases):<8d} {alias_str}")
    lines.append(f"\nTotal: {len(_ALL_TOOL_MODULES)} tools, "
                 f"{sum(1 for m in _ALL_TOOL_MODULES if getattr(m, 'IS_READONLY', False))} readonly, "
                 f"{len(TOOL_NAME_ALIASES)} total aliases")
    # Append MCP tool info
    mgr = get_mcp_manager()
    mcp_count = mgr.get_tool_count()
    if mcp_count > 0:
        lines.append(f"\n# MCP Tools (dynamically loaded from external servers)")
        lines.append(f"{'Tool Name':<40s} {'Server':<15s} Description")
        lines.append(f"{'─'*40} {'─'*15} {'─'*40}")
        for defn in mgr.get_all_tool_definitions():
            fn = defn["function"]
            name = fn["name"]
            desc = fn.get("description", "")[:60]
            # Extract server name from canonical name mcp__{server}__{tool}
            parts = name.split("__")
            server = parts[1] if len(parts) >= 3 else "?"
            lines.append(f"{name:<40s} {server:<15s} {desc}")
        lines.append(f"\nTotal MCP: {mcp_count} tools from {len(mgr.connections)} server(s)")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Dynamic System Prompt — Tool Guidance Assembler
# ═══════════════════════════════════════════════════════════════

def build_tool_guidance() -> str:
    """Dynamically assemble the 'Using your tools' section of the system prompt.

    Reads GUIDANCE metadata from each registered tool module and generates:
      1. Per-tool usage rules with 'replaces_shell' → "use X instead of shell Y"
      2. Per-tool tips
      3. 'prefer_over' rules → "use X instead of Y for Z"
      4. Auto-generated "NEVER use shell_execute for:" blocklist
      5. Concurrency hint for read-only tools

    Adding a new tool with GUIDANCE automatically updates the system prompt.
    No manual editing of the prompt string required.
    """
    lines: List[str] = ["# Using your tools"]
    lines.append(
        "Use the dedicated tools provided — do NOT use shell_execute "
        "for tasks that have dedicated tools:"
    )
    # P27: Explicit anti-unnecessary-search rule (Claw pattern)
    lines.append(
        "  - CRITICAL: If the user mentions a filename or path (e.g. 'read agentic_loop.py'), "
        "call file_read DIRECTLY with that path. Do NOT call find_by_name or file_list first — "
        "you already know the path. Only use find_by_name when you genuinely don't know where a file is."
    )

    # Collect data from all modules
    shell_blocklist: List[str] = []  # "reading files (use file_read)"
    prefer_rules: List[str] = []
    readonly_names: List[str] = []

    for mod in _ALL_TOOL_MODULES:
        guidance = getattr(mod, "GUIDANCE", None)
        if not guidance:
            continue

        name = mod.TOOL_DEF["function"]["name"]
        desc = mod.TOOL_DEF["function"].get("description", "")
        is_fallback = guidance.get("is_fallback", False)

        # Track readonly for concurrency hint
        if getattr(mod, "IS_READONLY", False):
            readonly_names.append(name)

        # Skip the fallback tool's own rule line (shell_execute)
        if is_fallback:
            # Shell tips go directly
            for tip in guidance.get("tips", []):
                lines.append(f"  - {tip}")
            continue

        # "To <action>, use <tool> (not shell <cmds>)"
        replaces = guidance.get("replaces_shell", [])
        if replaces:
            short_desc = desc.split(".")[0].strip()  # first sentence
            shell_cmds = "/".join(replaces)
            lines.append(f"  - To {short_desc.lower()}, use {name} (not {shell_cmds})")
            # Build blocklist entry from explicit shell_never or fallback
            shell_never = guidance.get("shell_never")
            if shell_never:
                shell_blocklist.append(f"{shell_never} (use {name})")

        # prefer_over rules
        for other_tool, reason in guidance.get("prefer_over", {}).items():
            prefer_rules.append(f"  - Prefer {name} over {other_tool} {reason}.")

        # Tool-specific tips
        for tip in guidance.get("tips", []):
            lines.append(f"  - {name}: {tip}")

    # Add preference rules
    if prefer_rules:
        lines.append("")
        for rule in prefer_rules:
            lines.append(rule)

    # Auto-generate "NEVER use shell_execute for:" blocklist
    if shell_blocklist:
        lines.append("")
        blocklist_str = ", ".join(shell_blocklist)
        lines.append(
            f"  - NEVER use shell_execute for: {blocklist_str}."
        )

    # Concurrency hint
    if readonly_names:
        lines.append("")
        ro_str = ", ".join(readonly_names)
        lines.append(
            f"  - Read-only tools ({ro_str}) run concurrently when called in the same turn. "
            f"Batch multiple reads together for speed."
        )

    # MCP tool guidance
    mgr = get_mcp_manager()
    mcp_defs = mgr.get_all_tool_definitions()
    if mcp_defs:
        lines.append("")
        lines.append("# MCP tools (external servers)")
        lines.append(
            "MCP tools are provided by external servers. They are called the same way "
            "as built-in tools. Their names start with 'mcp__'.  Treat them as you would "
            "any built-in tool — the safety and approval mechanisms apply equally."
        )
        for defn in mcp_defs:
            fn = defn["function"]
            desc_short = fn.get("description", "").split(".")[0]
            lines.append(f"  - {fn['name']}: {desc_short}")

        # P16: MCP resource discovery — list concrete names so model knows what to query
        mcp_resources = mgr.get_all_resources()
        if mcp_resources:
            summaries = []
            for r in mcp_resources:
                label = r.get("name", r.get("uri", "?"))
                srv = r.get("server", "")
                summaries.append(f"{label} ({srv})" if srv else label)
            resource_str = ", ".join(summaries)
            if len(resource_str) > 200:
                resource_str = resource_str[:197] + "..."
            lines.append(
                f"  - MCP resources available: {resource_str}. "
                f"Query these when the user's question involves their domain."
            )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# MCP Tool Injection — dynamically updates AGENTIC_TOOLS
# ═══════════════════════════════════════════════════════════════

def refresh_mcp_tools():
    """Rebuild AGENTIC_TOOLS to include current MCP tool definitions.

    Called after MCP servers connect (or reconnect) to make their tools
    visible to the agentic loop's LLM tool-calling schema.
    """
    global AGENTIC_TOOLS
    base_tools = [m.TOOL_DEF for m in _ALL_TOOL_MODULES]
    mgr = get_mcp_manager()
    mcp_tools = mgr.get_all_tool_definitions()
    AGENTIC_TOOLS = base_tools + mcp_tools
    if mcp_tools:
        logger.info(f"[MCP] Injected {len(mcp_tools)} MCP tools into AGENTIC_TOOLS "
                     f"(total: {len(AGENTIC_TOOLS)})")


def get_all_tools() -> List[Dict[str, Any]]:
    """Return the current AGENTIC_TOOLS list (built-in + MCP).

    Prefer this over accessing AGENTIC_TOOLS directly when you need
    a snapshot that includes dynamically loaded MCP tools.
    """
    return list(AGENTIC_TOOLS)
