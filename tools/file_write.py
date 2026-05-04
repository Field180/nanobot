"""file_write tool — Write content to a file."""
from pathlib import Path
from edit_transaction import create_pending_change_set, build_unified_diff
from tools.base import _resolve_path

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "file_write",
        "description": (
            "Write content to a file. Creates parent directories if needed. "
            "Overwrites the file if it already exists."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to write to"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file"
                }
            },
            "required": ["path", "content"]
        }
    }
}

ALIASES = ["write_file"]
IS_READONLY = False

GUIDANCE = {
    "tips": [
        "Use for creating NEW files or FULLY overwriting existing files.",
        "For modifying part of an existing file, use file_edit instead — it is safer.",
    ],
}


def build_write_plan(args: dict, workspace: Path) -> dict:
    path = _resolve_path(args.get("path", ""), workspace)
    content = args.get("content", "")
    before_exists = path.is_file()
    before_text = ""

    try:
        if before_exists:
            before_text = path.read_text(encoding="utf-8", errors="replace")
            existing_lines = before_text.count("\n") + 1
            if existing_lines > 50:
                return {
                    "success": False,
                    "output": "",
                    "error": (
                        f"file_write would OVERWRITE {path} ({existing_lines} lines). "
                        f"For partial changes, use file_edit instead — it is safer. "
                        f"If you truly need to replace the entire file, confirm with the user first "
                        f"or re-call file_write after acknowledging this warning."
                    ),
                    "_overwrite_warning": True,
                }
    except Exception:
        before_text = ""

    diff_text = build_unified_diff(before_text, content, str(path), before_exists=before_exists)
    return {
        "success": True,
        "plan": {
            "path": str(path),
            "before_text": before_text,
            "after_text": content,
            "before_exists": before_exists,
            "created": not before_exists,
            "tool_name": "file_write",
            "summary": f"Write {path}",
            "diff": diff_text,
        },
        "output": f"Prepared write plan for {path}",
        "error": "",
    }


def apply_write_plan(plan: dict) -> dict:
    path = Path(plan.get("path", ""))
    content = plan.get("after_text", "")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        try:
            from tools.file_read import invalidate_session_reads
            invalidate_session_reads(path)
        except ImportError:
            pass
        return {"success": True, "output": f"Written {len(content)} chars to {path}", "error": ""}
    except Exception as e:
        return {"success": False, "output": "", "error": f"Write error: {e}"}


def execute(args: dict, workspace: Path) -> dict:
    plan_result = build_write_plan(args, workspace)
    if not plan_result.get("success"):
        return plan_result

    session_id = args.get("_session_id") or args.get("session_id") or "global"
    try:
        change_set = create_pending_change_set(
            plan_result["plan"],
            session_id=session_id,
            source="file_write",
        )
    except ValueError as exc:
        return {"success": False, "output": "", "error": str(exc)}
    return {
        "success": True,
        "output": f"Created pending change set {change_set['id']} for {change_set['files'][0]['path']}. Awaiting approval.",
        "error": "",
        "_change_set": change_set,
    }
