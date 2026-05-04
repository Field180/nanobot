from pathlib import Path

from edit_transaction import reject_change_set

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "change_set_reject",
        "description": (
            "Reject or roll back a pending/applied transactional change set that was previously created by file_edit or file_write. "
            "Use this after the user explicitly declines the proposed edit or asks to revert it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "change_set_id": {
                    "type": "string",
                    "description": "The change set id to reject or revert"
                }
            },
            "required": ["change_set_id"]
        }
    }
}

ALIASES = ["reject_change_set", "decline_change_set"]
IS_READONLY = False

GUIDANCE = {
    "tips": [
        "Use when the user explicitly rejects a pending change set or asks to roll back an applied one.",
        "If rollback hits a conflict, inspect the returned review change set carefully.",
    ],
}


def execute(args: dict, workspace: Path) -> dict:
    change_set_id = str(args.get("change_set_id", "") or "").strip()
    if not change_set_id:
        return {"success": False, "output": "", "error": "change_set_id is required"}

    result = reject_change_set(change_set_id)
    if not result.get("success"):
        output = ""
        review_change_set = result.get("review_change_set")
        if review_change_set:
            output = f"Rollback conflict for {change_set_id}. Review change set {review_change_set.get('id', '')} was created."
        return {
            "success": False,
            "output": output,
            "error": result.get("error", f"Failed to reject {change_set_id}"),
            "_change_set": result.get("change_set"),
            "_review_change_set": review_change_set,
        }

    change_set = result.get("change_set", {})
    status = change_set.get("status", "rejected")
    return {
        "success": True,
        "output": f"Updated change set {change_set_id} to status {status}.",
        "error": "",
        "_change_set": change_set,
    }
