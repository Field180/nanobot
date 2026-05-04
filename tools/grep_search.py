"""grep_search tool — Search file contents with grep."""
import subprocess
from pathlib import Path
from tools.base import _subprocess_run, _resolve_path

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "grep_search",
        "description": (
            "Search for a text pattern in files using grep. "
            "Returns matching lines with file paths and line numbers. "
            "Supports regex patterns."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (regex or fixed string)"
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in"
                },
                "include": {
                    "type": "string",
                    "description": "File glob to filter (e.g. '*.py'). Optional."
                }
            },
            "required": ["pattern", "path"]
        }
    }
}

ALIASES = ["search", "grep"]
IS_READONLY = True

GUIDANCE = {
    "replaces_shell": ["grep", "rg", "ag"],
    "shell_never": "searching file content",
    "tips": [
        "Supports regex patterns. Use 'include' to filter by file extension (e.g. '*.py').",
    ],
}


def execute(args: dict, workspace: Path) -> dict:
    pattern = args.get("pattern", "")
    path = _resolve_path(args.get("path", "."), workspace)
    include = args.get("include", "")

    if not pattern:
        return {"success": False, "output": "", "error": "No pattern provided"}

    cmd = ["grep", "-rnE", "--color=never", "-I",
           "--exclude-dir=.git", "--exclude-dir=node_modules",
           "--exclude-dir=__pycache__", "--exclude-dir=.venv",
           "--exclude-dir=.tool_results", "--exclude-dir=.nanobot_state",
           "--exclude-dir=.nanobot_memory"]
    if include:
        cmd.extend(["--include", include])
    cmd.extend(["-e", pattern, str(path)])

    try:
        result = _subprocess_run(
            cmd, capture_output=True, text=True, timeout=30,
            cwd=str(workspace)
        )
        raw_output = result.stdout[:50000] if result.stdout else ""
        lines = raw_output.strip().split("\n") if raw_output.strip() else []
        total_matches = len(lines)

        if total_matches == 0:
            output = "No matches found"
        elif total_matches > 100:
            output = "\n".join(lines[:100])
            remaining = total_matches - 100
            output += f"\n\n... [{remaining} matches truncated] ...\n(Results are truncated. Use a more specific pattern or 'include' filter to narrow results. Do NOT infer content from matches not shown.)"
        else:
            output = "\n".join(lines)

        # Prepend match count header (like Claw's numFiles/numMatches)
        if total_matches > 0:
            output = f"Found {total_matches} match{'es' if total_matches != 1 else ''}\n{output}"

        # P54: Hint when searching for function definitions — warn about indented methods
        if total_matches > 0 and "def " in pattern and "^def" in pattern:
            output += (
                "\n\n[Note: Pattern '^def ' only matches module-level functions. "
                "Class methods (indented def) and 'async def' are NOT included. "
                "Use pattern 'def ' (without ^) to match all function definitions.]"
            )

        # Surface permission errors so model knows some dirs were skipped
        if result.stderr:
            perm_errors = [l for l in result.stderr.strip().split("\n") if "权限不够" in l or "Permission denied" in l]
            if perm_errors:
                output += f"\n\n[Note: {len(perm_errors)} file(s) skipped due to permission denied]"

        return {"success": True, "output": output, "error": ""}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "Search timed out (30s)"}
