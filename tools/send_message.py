"""send_message tool — Send a message to another agent's mailbox.

Allows agents to communicate asynchronously via the global AgentMailbox.
Each agent is identified by its agent_id (set in execution context).
Messages are queued and delivered when the target agent checks its inbox.

Design mirrors Claw's SendMessageTool / teammateMailbox pattern.
"""
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("nanobot.tools.send_message")

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "send_message",
        "description": (
            "Send a message to another agent by agent_id. "
            "Use this to share findings, coordinate tasks, or request information "
            "from a sibling agent running in the same session. "
            "The message is queued and delivered when the target agent checks its inbox."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to_agent": {
                    "type": "string",
                    "description": "The agent_id of the target agent to send the message to."
                },
                "content": {
                    "type": "string",
                    "description": "The message content to send."
                },
            },
            "required": ["to_agent", "content"]
        }
    }
}

ALIASES = ["message_agent", "agent_message"]
IS_READONLY = True

GUIDANCE = {
    "replaces_shell": [],
    "shell_never": "",
    "tips": [
        "Use send_message to share findings with sibling agents in parallel tasks.",
        "Include enough context in the message for the recipient to act on it.",
    ],
}


def execute(args: dict, workspace: Path) -> dict:
    """Execute send_message — deliver a message to another agent's mailbox."""
    to_agent = (args.get("to_agent") or "").strip()
    content = (args.get("content") or "").strip()
    from_agent = args.get("_agent_id", args.get("_session_id", "parent"))
    session_id = args.get("_session_id", "")

    if not to_agent:
        return {"success": False, "output": "", "error": "No to_agent specified."}
    if not content:
        return {"success": False, "output": "", "error": "No content provided."}
    if not session_id:
        logger.warning("[SendMessage] No session_id available — message scoped globally")


    from services.agent_mailbox import get_mailbox
    mailbox = get_mailbox()
    _reject: list = []
    msg_id = mailbox.send_message(from_agent, to_agent, content,
                                  session_id=session_id,
                                  reject_out=_reject)

    if msg_id:
        logger.info("[SendMessage] %s → %s [%s] (%d chars)",
                    from_agent, to_agent, msg_id, len(content))
        return {
            "success": True,
            "output": f"Message sent to {to_agent} ({len(content)} chars, id={msg_id}).",
            "error": "",
            "_msg_id": msg_id,
        }
    reason = _reject[0] if _reject else "DELIVERY_ERROR"
    return {
        "success": False,
        "output": "",
        "error": f"{reason}: delivery to '{to_agent}' rejected by mailbox.",
    }
