"""python_execute tool — Execute Python 3 code.

P39: Includes bypass prevention to block importing other registered tools.
"""
import re as _re39
import subprocess
import sys
from pathlib import Path
from tools.base import _subprocess_run

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "python_execute",
        "description": (
            "Execute a Python 3 script and return stdout/stderr. "
            "The code runs in the workspace directory. "
            "IMPORTANT: Do NOT use python_execute to call other tools "
            "(file_read, web_fetch, sub_agent, etc.) — call those tools directly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute"
                }
            },
            "required": ["code"]
        }
    }
}

ALIASES = ["python", "code_execute"]
IS_READONLY = False

GUIDANCE = {
    "tips": [
        "Runs in the workspace directory with a 60-second timeout.",
        "Use for data processing, calculations, or testing code snippets.",
        "NEVER use python_execute to call other tools (web_fetch, sub_agent, etc.) — call them directly.",
    ],
}

# P39: Patterns that indicate tool bypass via python_execute
_TOOL_BYPASS_PATTERNS = _re39.compile(
    r'from\s+tools\s+import|'
    r'from\s+tools\.|'
    r'import\s+tools|'
    r'execute_tool\s*\(|'
    r'execute_tool_async\s*\(|'
    r'from\s+agentic_loop\s+import|'
    r'AGENTIC_TOOLS|'
    r'_DISPATCH\[',
    _re39.IGNORECASE,
)


def execute(args: dict, workspace: Path) -> dict:
    code = args.get("code", "")
    if not code:
        return {"success": False, "output": "", "error": "No code provided"}

    # P39: Block python_execute from importing and calling registered tools
    if _TOOL_BYPASS_PATTERNS.search(code):
        return {
            "success": False,
            "output": "",
            "error": (
                "python_execute cannot import or call registered tools (from tools import ...). "
                "Call the tool directly instead — e.g. call web_fetch, sub_agent, grep_search "
                "as a tool call, not via python_execute."
            ),
        }

    try:
        result = _subprocess_run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=60,
            cwd=str(workspace)
        )
        output = result.stdout[:10000] if result.stdout else ""
        if result.stderr:
            output = f"{output}\n[stderr]\n{result.stderr[:5000]}" if output else f"[stderr]\n{result.stderr[:5000]}"
        return {
            "success": result.returncode == 0,
            "output": output.strip() or "(no output)",
            "error": "" if result.returncode == 0 else f"Exit code: {result.returncode}"
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "Python execution timed out (60s)"}
