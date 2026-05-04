"""file_edit tool — Claw-style precise find-and-replace editing."""
from pathlib import Path
import re
from edit_transaction import create_pending_change_set, build_unified_diff
from tools.base import _resolve_path, validate_file_for_edit, update_file_state_after_edit

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "file_edit",
        "description": (
            "Make a precise edit to a file by replacing an exact string match. "
            "Provide old_string (the text to find) and new_string (the replacement). "
            "old_string MUST be unique in the file unless replace_all is true. "
            "Use this instead of file_write for modifying existing files — it is safer "
            "and uses far fewer tokens. "
            "To create a new file, use old_string='' with the full content as new_string."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or workspace-relative file path"
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to find and replace (must be unique in file, or set replace_all=true)"
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text (must differ from old_string)"
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "If true, replace ALL occurrences of old_string. Default: false."
                }
            },
            "required": ["path", "old_string", "new_string"]
        }
    }
}

ALIASES = ["edit_file", "str_replace", "text_editor"]
IS_READONLY = False

GUIDANCE = {
    "replaces_shell": ["sed -i", "awk"],
    "shell_never": "editing files",
    "prefer_over": {
        "file_write": "for modifying existing files — file_edit is safer and uses far fewer tokens",
    },
    "tips": [
        "old_string must match the file content EXACTLY, including indentation and whitespace.",
        "To create a new file, use old_string='' with the full content as new_string.",
        "If old_string appears multiple times and you want to replace all, set replace_all=true.",
        "When RENAMING anything, you MUST first call grep_search to find ALL occurrences, then call file_edit for EACH one in the SAME turn. Incomplete renames will trigger a WARNING.",
        "If a file needs multiple DISTINCT edits (different old_string values), call file_edit MULTIPLE times in one turn — one call per edit location. All pending edits will be grouped into a single approval.",
    ],
}


def build_edit_plan(args: dict, workspace: Path) -> dict:
    path = _resolve_path(args.get("path", ""), workspace)
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = args.get("replace_all", False)

    if old_string == new_string:
        return {"success": False, "output": "", "error": "old_string and new_string are identical — nothing to change."}

    if old_string == "":
        before_exists = path.exists()
        before_text = ""
        if before_exists:
            try:
                before_text = path.read_text(encoding="utf-8", errors="replace")
                if before_text.strip():
                    return {"success": False, "output": "", "error": "Cannot create file — file already exists and is non-empty. Use a non-empty old_string to edit it."}
            except Exception:
                pass
        diff_text = build_unified_diff(before_text, new_string, str(path), before_exists=before_exists)
        return {
            "success": True,
            "plan": {
                "path": str(path),
                "before_text": before_text,
                "after_text": new_string,
                "before_exists": before_exists,
                "created": True,
                "tool_name": "file_edit",
                "summary": f"Create {path}",
                "diff": diff_text,
            },
            "output": f"Prepared create plan for {path}",
            "error": "",
        }

    if not path.exists():
        return {"success": False, "output": "", "error": f"File not found: {path}"}
    if not path.is_file():
        return {"success": False, "output": "", "error": f"Not a file: {path}"}
    if path.stat().st_size > 10_000_000:
        return {"success": False, "output": "", "error": f"File too large for editing ({path.stat().st_size} bytes). Max 10MB."}

    validation_error = validate_file_for_edit(path)
    if validation_error:
        return {"success": False, "output": "", "error": validation_error}

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"success": False, "output": "", "error": f"Read error: {e}"}

    count = content.count(old_string)
    if count == 0:
        stripped_old = "\n".join(line.rstrip() for line in old_string.split("\n"))
        stripped_content = "\n".join(line.rstrip() for line in content.split("\n"))
        if stripped_content.count(stripped_old) > 0:
            return {"success": False, "output": "",
                    "error": "old_string not found (exact match). A match exists if trailing whitespace is ignored. Please copy the exact text from the file, including whitespace."}
        return {"success": False, "output": "", "error": f"old_string not found in {path.name}. Make sure it matches the file content exactly."}

    if count > 1 and not replace_all:
        return {"success": False, "output": "",
                "error": f"Found {count} occurrences of old_string, but replace_all is false. "
                         f"Set replace_all=true to replace all, or provide more context to make old_string unique."}

    rename_autofix = _build_single_identifier_rename_autofix(content, old_string, new_string)
    if rename_autofix:
        old_identifier, new_identifier, identifier_count = rename_autofix
        new_content = re.sub(rf'\b{re.escape(old_identifier)}\b', new_identifier, content)
        replacements = identifier_count
    elif replace_all:
        new_content = content.replace(old_string, new_string)
        replacements = count
    else:
        new_content = content.replace(old_string, new_string, 1)
        replacements = 1

    old_lines = old_string.count('\n') + 1
    new_lines = new_string.count('\n') + 1
    diff_text = build_unified_diff(content, new_content, str(path), before_exists=True)
    return {
        "success": True,
        "plan": {
            "path": str(path),
            "before_text": content,
            "after_text": new_content,
            "before_exists": True,
            "created": False,
            "tool_name": "file_edit",
            "summary": f"Edited {path} — {replacements} replacement(s)",
            "diff": diff_text,
            "rename_autofix": bool(rename_autofix),
        },
        "output": f"Prepared edit plan for {path} (  -{old_lines} lines / +{new_lines} lines)",
        "error": "",
    }


def apply_edit_plan(plan: dict) -> dict:
    path = Path(plan.get("path", ""))
    after_text = plan.get("after_text", "")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(after_text, encoding="utf-8")
    except Exception as e:
        return {"success": False, "output": "", "error": f"Write error: {e}"}

    update_file_state_after_edit(path)
    try:
        from tools.file_read import invalidate_session_reads
        invalidate_session_reads(path)
    except ImportError:
        pass
    return {"success": True, "output": f"Applied edit plan to {path}", "error": ""}


def _build_single_identifier_rename_autofix(content: str, old_string: str, new_string: str):
    old_words = set(re.findall(r'\b[a-zA-Z_]\w{2,}\b', old_string))
    new_words = set(re.findall(r'\b[a-zA-Z_]\w{2,}\b', new_string))
    removed = old_words - new_words
    added = new_words - old_words
    if len(removed) != 1 or len(added) != 1:
        return None
    old_identifier = next(iter(removed))
    new_identifier = next(iter(added))
    old_pattern = rf'\b{re.escape(old_identifier)}\b'
    new_pattern = rf'\b{re.escape(new_identifier)}\b'
    if not re.search(old_pattern, old_string) or not re.search(new_pattern, new_string):
        return None
    identifier_count = len(re.findall(old_pattern, content))
    if identifier_count <= 1:
        return None
    return old_identifier, new_identifier, identifier_count


def _detect_incomplete_rename(content: str, old_string: str, new_string: str, path: str, replace_all: bool = False) -> str:
    """Detect if a file_edit might be an incomplete rename and return a warning."""
    if replace_all:
        return ""  # replace_all handles all occurrences automatically
    old_words = set(re.findall(r'\b[a-zA-Z_]\w{2,}\b', old_string))
    new_words = set(re.findall(r'\b[a-zA-Z_]\w{2,}\b', new_string))
    removed = old_words - new_words
    if not removed:
        return ""
    warnings = []
    for word in removed:
        count = content.count(word)
        if count > 1:
            warnings.append(
                f"WARNING: '{word}' appears {count} times in {path}. "
                "If you are renaming it, you MUST edit ALL occurrences (definition AND all call sites) in the SAME turn."
            )
    return "\n".join(warnings)


def execute(args: dict, workspace: Path) -> dict:
    """Claw-style precise find-and-replace edit.

    Behaviour (mirrors Claw FileEditTool):
      - old_string must be unique in the file (unless replace_all=True)
      - old_string == new_string → rejected (no-op)
      - old_string == '' on non-existent file → create new file with new_string
      - old_string == '' on existing non-empty file → error
      - Generates a mini unified-diff snippet in the output for user review
    """
    plan_result = build_edit_plan(args, workspace)
    if not plan_result.get("success"):
        return plan_result

    session_id = args.get("_session_id") or args.get("session_id") or "global"
    try:
        change_set = create_pending_change_set(
            plan_result["plan"],
            session_id=session_id,
            source="file_edit",
        )
    except ValueError as exc:
        return {"success": False, "output": "", "error": str(exc)}

    # Check for incomplete renames so the model can catch them before approval
    path = plan_result["plan"].get("path", "")
    warning = ""
    if plan_result["plan"].get("before_exists") and plan_result["plan"].get("before_text"):
        warning = _detect_incomplete_rename(
            plan_result["plan"]["before_text"],
            args.get("old_string", ""),
            args.get("new_string", ""),
            path,
            args.get("replace_all", False),
        )

    output = f"Created pending change set {change_set['id']} for {change_set['files'][0]['path']}. Awaiting approval."
    if plan_result["plan"].get("rename_autofix"):
        output += "\nAuto-completed identifier rename across the whole file so the approval diff includes all call sites."
    if warning:
        output += f"\n{warning}"

    return {
        "success": True,
        "output": output,
        "error": "",
        "_change_set": change_set,
    }
