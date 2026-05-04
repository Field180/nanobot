from pathlib import Path

from edit_transaction import accept_change_set

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "change_set_accept",
        "description": (
            "Accept and apply a pending transactional change set that was previously created by file_edit or file_write. "
            "Use this after the user explicitly confirms the proposed edit."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "change_set_id": {
                    "type": "string",
                    "description": "The pending change set id, such as cs_20260426_031156_cee9118c"
                }
            },
            "required": ["change_set_id"]
        }
    }
}

ALIASES = ["accept_change_set", "approve_change_set"]
IS_READONLY = False

GUIDANCE = {
    "tips": [
        "Use only after the user explicitly approves a pending change set.",
        "Do not claim a file was modified until this tool succeeds.",
    ],
}


def execute(args: dict, workspace: Path) -> dict:
    change_set_id = str(args.get("change_set_id", "") or "").strip()
    if not change_set_id:
        return {"success": False, "output": "", "error": "change_set_id is required"}

    result = accept_change_set(change_set_id)
    if not result.get("success"):
        return {
            "success": False,
            "output": "",
            "error": result.get("error", f"Failed to accept {change_set_id}"),
            "_change_set": result.get("change_set"),
        }

    change_set = result.get("change_set", {})
    files = change_set.get("files", [])
    return {
        "success": True,
        "output": f"Accepted change set {change_set_id}. Applied {len(files)} file(s).",
        "error": "",
        "_change_set": change_set,
    }
