"""
P92: Memory Auto-Extraction Triggers.

Detects patterns in user messages that should trigger memory saves.
Returns hint text to inject into the agentic loop context.

Two trigger categories:
  1. Explicit: user says "记住", "remember this", "save this preference"
  2. Implicit: user corrects behavior ("不要", "stop doing", "别再")

The hint is injected as a system message nudge, NOT as automatic tool calls.
The model still decides what to save and how — we just make sure it doesn't
miss obvious save opportunities.
"""
import re
from typing import Optional

# ═══════════════════════════════════════════════════════════════
# Explicit save triggers (user directly requests memory save)
# ═══════════════════════════════════════════════════════════════
_EXPLICIT_SAVE_RE = re.compile(
    r"(?:记住|记下|保存.*(?:偏好|设置|习惯)|"
    r"remember\s+(?:this|that)|"
    r"save\s+(?:this|that|my)\s+(?:preference|setting|note)|"
    r"don'?t\s+forget|"
    r"keep\s+(?:this|that)\s+in\s+mind|"
    r"note\s+(?:this|that)\s+for\s+(?:next|future|later))",
    re.IGNORECASE,
)

# ═══════════════════════════════════════════════════════════════
# Implicit feedback triggers (user corrects behavior)
# ═══════════════════════════════════════════════════════════════
_FEEDBACK_RE = re.compile(
    r"(?:不要再?|别再?|停止|以后(?:不要|别)|"
    r"stop\s+(?:doing|using|adding)|"
    r"don'?t\s+(?:do|use|add|put|include|repeat)\b|"
    r"never\s+(?:do|use|add)\b|"
    r"I\s+(?:prefer|always\s+want|like\s+it\s+when)|"
    r"from\s+now\s+on\b|"
    r"going\s+forward\b)",
    re.IGNORECASE,
)

# ═══════════════════════════════════════════════════════════════
# Positive confirmation triggers (validates an approach)
# ═══════════════════════════════════════════════════════════════
_CONFIRM_RE = re.compile(
    r"(?:就是这样|对了?就是|很好继续|exactly|perfect|"
    r"yes\s+exactly|keep\s+doing\s+(?:that|this)|"
    r"that'?s?\s+(?:exactly|perfect|right)\s+(?:what|how))",
    re.IGNORECASE,
)


def detect_memory_trigger(user_message: str) -> Optional[str]:
    """Detect if a user message should trigger a memory save.

    Returns a hint string to inject as a system message, or None.
    The hint reminds the model to use the memory tool — it does NOT
    automatically save anything.

    Args:
        user_message: The current user message text

    Returns:
        Hint string or None
    """
    # Strip conversation history delimiters if present
    text = user_message
    if "[当前问题]" in text:
        text = text.split("[当前问题]", 1)[-1]

    text = text.strip()
    if not text or len(text) < 3:
        return None

    # Check explicit save triggers
    if _EXPLICIT_SAVE_RE.search(text):
        return (
            "[MEMORY HINT] The user explicitly asked you to remember something. "
            "Use the memory tool (action='save') to store this. Choose the most "
            "appropriate type (user/feedback/project/reference). Include a specific "
            "description for future relevance matching."
        )

    # Check feedback/correction triggers
    if _FEEDBACK_RE.search(text):
        return (
            "[MEMORY HINT] The user appears to be correcting your behavior or "
            "stating a preference. Consider saving this as a 'feedback' memory "
            "using the memory tool (action='save', type='feedback'). Include "
            "WHY the user wants this and HOW to apply it in future conversations."
        )

    # Check positive confirmation
    if _CONFIRM_RE.search(text):
        return (
            "[MEMORY HINT] The user confirmed that your approach was correct. "
            "Consider saving this as a 'feedback' memory to remember what worked. "
            "Use the memory tool (action='save', type='feedback')."
        )

    return None
