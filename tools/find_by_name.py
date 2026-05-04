"""find_by_name tool — Find files by name pattern."""
import subprocess
from pathlib import Path
from tools.base import _subprocess_run, _resolve_path

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "find_by_name",
        "description": "Find files by name pattern in a directory tree. Only use this when you do NOT already know the file path. If the user provides a path, use file_read directly.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Filename glob (e.g. '*.py', 'config*')"
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in"
                }
            },
            "required": ["pattern", "path"]
        }
    }
}

ALIASES = ["find"]
IS_READONLY = True

GUIDANCE = {
    "replaces_shell": ["find", "locate", "ls -R"],
    "shell_never": "finding files by name",
    "tips": [
        "Only use when you do NOT already know the file path.",
        "If the user provides a filename or path, use file_read directly instead.",
    ],
}


def execute(args: dict, workspace: Path) -> dict:
    pattern = args.get("pattern", "")
    path = _resolve_path(args.get("path", "."), workspace)

    if not pattern:
        return {"success": False, "output": "", "error": "No pattern provided"}

    cmd = ["find", str(path), "-maxdepth", "5",
           "-not", "-path", "*/.git/*",
           "-not", "-path", "*/__pycache__/*",
           "-not", "-path", "*/.venv/*",
           "-not", "-path", "*/.tool_results/*",
           "-not", "-path", "*/.nanobot_state/*",
           "-not", "-path", "*/.nanobot_memory/*",
           "-name", pattern, "-type", "f"]
    try:
        result = _subprocess_run(
            cmd, capture_output=True, text=True, timeout=15,
            cwd=str(workspace)
        )
        raw_output = result.stdout[:50000] if result.stdout else ""
        lines = raw_output.strip().split("\n") if raw_output.strip() else []
        total = len(lines)

        if total == 0:
            return {"success": True, "output": "No files found", "error": ""}

        if total > 50:
            output = "\n".join(lines[:50])
            output += f"\n(Results are truncated. Consider using a more specific pattern or path.)"
        else:
            output = "\n".join(lines)
        output = f"Found {total} file{'s' if total != 1 else ''}\n{output}"
        return {"success": True, "output": output, "error": ""}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "Find timed out (15s)"}
