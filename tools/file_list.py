"""file_list tool — List files and directories."""
from pathlib import Path
from tools.base import _resolve_path

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "file_list",
        "description": (
            "List files and directories in the given path. "
            "Returns a formatted listing with type indicators (d=directory, f=file) and sizes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list"
                }
            },
            "required": ["path"]
        }
    }
}

ALIASES = ["list_dir", "list_directory"]
IS_READONLY = True

GUIDANCE = {
    "replaces_shell": ["ls", "dir", "tree"],
    "shell_never": "listing directories",
    "tips": [
        "Returns formatted listing with type indicators (d=directory, f=file) and sizes.",
    ],
}


def execute(args: dict, workspace: Path) -> dict:
    path = _resolve_path(args.get("path", "."), workspace)

    if not path.exists():
        return {"success": False, "output": "", "error": f"Path not found: {path}"}
    if not path.is_dir():
        return {"success": False, "output": "", "error": f"Not a directory: {path}"}

    try:
        entries = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        lines = []
        for entry in entries[:100]:
            if entry.is_dir():
                count = sum(1 for _ in entry.iterdir()) if entry.exists() else 0
                lines.append(f"d  {entry.name}/  ({count} items)")
            else:
                size = entry.stat().st_size
                if size > 1_000_000:
                    size_str = f"{size / 1_000_000:.1f}MB"
                elif size > 1_000:
                    size_str = f"{size / 1_000:.1f}KB"
                else:
                    size_str = f"{size}B"
                lines.append(f"f  {entry.name}  ({size_str})")

        total = len(entries)
        truncated = total > 100
        header = f"Directory: {path} ({total} entries{', showing first 100' if truncated else ''})"
        result = header + "\n" + "\n".join(lines)
        if truncated:
            result += "\n(Results are truncated. Use find_by_name or a more specific path to narrow results.)"

        # Auto-append tool registry metadata when listing the tools directory
        # This saves the model from needing to file_read each tool module
        if path.name == "tools" and (path / "__init__.py").exists():
            try:
                from tools import tool_registry_summary
                result += "\n" + tool_registry_summary()
            except Exception:
                pass  # graceful fallback if not in the right package context

        return {"success": True, "output": result, "error": ""}
    except Exception as e:
        return {"success": False, "output": "", "error": f"List error: {e}"}
