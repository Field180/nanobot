"""ask_user tool — Structured user question with optional choices.

P82 (UPGRADE_PLAN): When the model is genuinely uncertain, it can ask the user
a structured question with optional predefined options.  This replaces ad-hoc
"Which do you prefer?" text with a machine-parseable request that the frontend
can render as clickable buttons or a selection list.

Design mirrors Claw's AskUserQuestionTool:
  - question (required): the question text
  - options (optional): list of option objects with label + description
  - allow_multiple (optional): whether user can select >1 option
  - default_option (optional): pre-selected option index

The tool blocks the agentic loop — the model must wait for the user's answer
before continuing.  The answer is returned as the tool result.
"""
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("nanobot.tools.ask_user")

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Ask the user a structured question when you are genuinely uncertain "
            "about how to proceed. Provide clear options when possible. "
            "Use this instead of guessing — the user's explicit choice prevents wasted work. "
            "Do NOT use this for rhetorical questions or confirmations you can infer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user. Be specific and concise."
                },
                "options": {
                    "type": "array",
                    "description": (
                        "Optional list of predefined choices. Each option has a 'label' "
                        "(short, shown as button text) and 'description' (longer explanation). "
                        "Omit if the question is open-ended."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {
                                "type": "string",
                                "description": "Short label for the option (e.g. 'Yes', 'Option A')"
                            },
                            "description": {
                                "type": "string",
                                "description": "Longer description explaining what this option means"
                            }
                        },
                        "required": ["label"]
                    }
                },
                "allow_multiple": {
                    "type": "boolean",
                    "description": "If true, user can select multiple options. Default: false."
                },
                "default_option": {
                    "type": "integer",
                    "description": "0-based index of the default/recommended option."
                }
            },
            "required": ["question"]
        }
    }
}

ALIASES = ["ask_user_question", "AskUserQuestion", "user_question"]
IS_READONLY = True

GUIDANCE = {
    "replaces_shell": [],
    "shell_never": "",
    "tips": [
        "Use ask_user only when genuinely uncertain — do NOT ask for confirmation on routine actions.",
        "Provide 2-4 concrete options when the choice is enumerable.",
        "For open-ended questions (e.g. 'What is the project name?'), omit options.",
        "The user's response will be returned as the tool result text.",
    ],
}

# Pending questions registry: session_id -> {question_id: question_data}
_PENDING_QUESTIONS: Dict[str, Dict[str, Any]] = {}


def _generate_question_id() -> str:
    return f"ask_{int(time.time() * 1000) % 100000}"


def execute(args: dict, workspace: Path) -> dict:
    """Execute ask_user — formats the question for the frontend.

    The frontend (app.js / REST API) is responsible for:
    1. Rendering the question with options as interactive UI
    2. Collecting the user's response
    3. Returning it as the tool result

    In the current synchronous flow, this returns a formatted question
    that the agentic loop surfaces to the user.  The user's next message
    is treated as the answer.
    """
    question = args.get("question", "").strip()
    if not question:
        return {"success": False, "output": "", "error": "No question provided"}

    options = args.get("options", [])
    allow_multiple = bool(args.get("allow_multiple", False))
    default_option = args.get("default_option")

    session_id = args.get("_session_id", "global")
    question_id = _generate_question_id()

    # Build formatted question output
    parts = [f"❓ **{question}**"]

    if options:
        parts.append("")
        for i, opt in enumerate(options):
            label = opt.get("label", f"Option {i + 1}")
            desc = opt.get("description", "")
            marker = "→ " if default_option == i else "  "
            if desc:
                parts.append(f"{marker}**[{i + 1}]** {label} — {desc}")
            else:
                parts.append(f"{marker}**[{i + 1}]** {label}")

        if allow_multiple:
            parts.append("\n_(You can select multiple options, e.g. '1, 3')_")
        else:
            parts.append("\n_(Please select one option or type your answer)_")
    else:
        parts.append("\n_(Please type your answer)_")

    output = "\n".join(parts)

    # Store pending question for session tracking
    _PENDING_QUESTIONS.setdefault(session_id, {})[question_id] = {
        "question": question,
        "options": options,
        "allow_multiple": allow_multiple,
        "default_option": default_option,
        "created_at": time.time(),
    }

    logger.info(f"[AskUser] Question {question_id}: {question[:80]}... "
                f"({len(options)} options, session={session_id})")

    return {
        "success": True,
        "output": output,
        "error": "",
        "_ask_user": True,
        "_question_id": question_id,
        "_options_count": len(options),
        "_requires_response": True,
    }


def get_pending_question(session_id: str) -> Dict[str, Any]:
    """Get the most recent pending question for a session (used by frontend)."""
    session_qs = _PENDING_QUESTIONS.get(session_id, {})
    if not session_qs:
        return {}
    # Return most recent
    latest_id = max(session_qs.keys(), key=lambda k: session_qs[k]["created_at"])
    return {"question_id": latest_id, **session_qs[latest_id]}


def clear_pending_question(session_id: str, question_id: str = "") -> None:
    """Clear a pending question after user responds."""
    session_qs = _PENDING_QUESTIONS.get(session_id, {})
    if question_id:
        session_qs.pop(question_id, None)
    else:
        session_qs.clear()
