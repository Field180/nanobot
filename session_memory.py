"""
U1: Session Memory — Automatic session notes for context preservation.

Mirrors Claw's services/SessionMemory/ pattern:
- Maintains a structured markdown note that captures key session state
- Updated periodically (every N tool calls) from conversation messages
- Injected into compact summary to survive context compaction
- Stored in-memory per session (no disk I/O during extraction)

The notes use Claw's template structure:
  # Session Title
  # Current State
  # Task Specification
  # Files and Functions
  # Workflow
  # Errors & Corrections
  # Key Results
  # Worklog
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nanobot.session_memory")

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

# Extract session notes every N tool calls
EXTRACT_INTERVAL_TOOL_CALLS = 8

# Maximum section length in characters
MAX_SECTION_CHARS = 2000

# Maximum total session memory size
MAX_TOTAL_CHARS = 12000

# Template (mirrors Claw's DEFAULT_SESSION_MEMORY_TEMPLATE)
SESSION_MEMORY_TEMPLATE = """# Session Title
_A short 5-10 word descriptive title for the session_

# Current State
_What is actively being worked on right now? Pending tasks not yet completed. Immediate next steps._

# Task Specification
_What did the user ask to build? Any design decisions or other explanatory context_

# Files and Functions
_What are the important files? In short, what do they contain and why are they relevant?_

# Workflow
_What commands are usually run and in what order? How to interpret their output if not obvious?_

# Errors & Corrections
_Errors encountered and how they were fixed. What did the user correct? What approaches failed?_

# Key Results
_If the user asked for a specific output (answer, table, analysis), repeat the exact result here_

# Worklog
_Step by step, what was attempted and done? Very terse summary for each step_
"""


# ═══════════════════════════════════════════════════════════════
# Session Memory State (per-session, in-memory)
# ═══════════════════════════════════════════════════════════════

class SessionMemoryState:
    """Tracks session notes and extraction triggers for one session."""

    __slots__ = (
        "session_id", "notes", "tool_calls_since_extract",
        "total_tool_calls", "last_extract_time", "extract_count",
    )

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.notes: Dict[str, str] = {}  # section_name -> content
        self.tool_calls_since_extract = 0
        self.total_tool_calls = 0
        self.last_extract_time = 0.0
        self.extract_count = 0

    def should_extract(self) -> bool:
        """Check if it's time to extract session notes."""
        if self.tool_calls_since_extract < EXTRACT_INTERVAL_TOOL_CALLS:
            return False
        # Also rate-limit: at least 30s between extractions
        if time.time() - self.last_extract_time < 30.0:
            return False
        return True

    def record_tool_call(self) -> None:
        """Record a tool call — increments counters."""
        self.tool_calls_since_extract += 1
        self.total_tool_calls += 1

    def get_notes_text(self) -> str:
        """Render current notes as structured text."""
        if not self.notes:
            return ""
        parts = []
        for section, content in self.notes.items():
            if content.strip():
                parts.append(f"## {section}\n{content.strip()}")
        return "\n\n".join(parts)


# Module-level session state registry
_SESSIONS: Dict[str, SessionMemoryState] = {}


def get_session_state(session_id: str) -> SessionMemoryState:
    """Get or create session memory state."""
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = SessionMemoryState(session_id)
    return _SESSIONS[session_id]


def reset_session_state(session_id: str) -> None:
    """Reset session memory state (called at session start)."""
    _SESSIONS.pop(session_id, None)


# ═══════════════════════════════════════════════════════════════
# Extraction — Build notes from conversation messages
# ═══════════════════════════════════════════════════════════════

# Patterns for extracting structured info from messages
_FILE_PATH_RE = re.compile(r'(?:File|Path|file_path)[:\s]+["\']?(/[^\s\'">,]+)', re.IGNORECASE)
_ERROR_RE = re.compile(r'(?:Error|Exception|FAIL|error|failed|traceback)', re.IGNORECASE)
_TOOL_RESULT_FILE_RE = re.compile(r'\[File:\s*(\S+)\s*\|')


def extract_session_notes(
    messages: List[Dict[str, Any]],
    state: SessionMemoryState,
) -> None:
    """Extract session notes from conversation messages.

    This is a lightweight in-process extraction (no LLM call).
    It parses messages to populate the structured notes sections.
    """
    t0 = time.time()

    # Reset extraction counters
    state.tool_calls_since_extract = 0
    state.last_extract_time = time.time()
    state.extract_count += 1

    # Collect data from messages
    user_messages = []
    files_seen = {}  # path -> summary
    errors_found = []
    tool_actions = []
    current_work = ""

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        if role == "user" and not content.startswith("[SESSION CONTEXT]"):
            # Skip dynamic context injections, keep real user messages
            if not content.startswith("Understood.") and len(content) > 5:
                user_messages.append(content[:500])

        elif role == "tool":
            tool_name = msg.get("_tool_name", "")
            # Track files
            m = _TOOL_RESULT_FILE_RE.search(content)
            if m:
                fpath = m.group(1)
                # Extract a brief summary (first meaningful line after header)
                lines = content.split("\n")
                summary_line = ""
                for line in lines[1:5]:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("[") and len(stripped) > 10:
                        summary_line = stripped[:120]
                        break
                files_seen[fpath] = summary_line

            # Track errors
            if _ERROR_RE.search(content[:500]):
                error_preview = content[:200].replace("\n", " ").strip()
                errors_found.append(f"[{tool_name}] {error_preview}")

            # Track tool actions for worklog
            if tool_name in ("file_edit", "file_write", "shell_execute", "python_execute"):
                action = content[:150].replace("\n", " ").strip()
                tool_actions.append(f"- {tool_name}: {action}")

        elif role == "assistant" and content:
            # Latest assistant text is "current work"
            if len(content) > 20:
                current_work = content[:500]

    # ── Populate sections ──

    # Session Title — derived from first user message
    if user_messages and not state.notes.get("Session Title"):
        first_msg = user_messages[0][:100]
        # Truncate to a short title
        title = first_msg.split("\n")[0][:80]
        state.notes["Session Title"] = title

    # Current State — latest assistant response + pending context
    if current_work:
        state.notes["Current State"] = _truncate(current_work, MAX_SECTION_CHARS)

    # Task Specification — first user message(s)
    if user_messages:
        task_spec = "\n".join(user_messages[:3])
        state.notes["Task Specification"] = _truncate(task_spec, MAX_SECTION_CHARS)

    # Files and Functions
    if files_seen:
        file_lines = []
        for fpath, summary in list(files_seen.items())[-20:]:  # last 20 files
            if summary:
                file_lines.append(f"- `{fpath}`: {summary}")
            else:
                file_lines.append(f"- `{fpath}`")
        state.notes["Files and Functions"] = _truncate(
            "\n".join(file_lines), MAX_SECTION_CHARS
        )

    # Errors & Corrections
    if errors_found:
        state.notes["Errors & Corrections"] = _truncate(
            "\n".join(errors_found[-10:]), MAX_SECTION_CHARS  # last 10 errors
        )

    # Worklog — tool actions
    if tool_actions:
        # Keep most recent actions, append to existing
        existing = state.notes.get("Worklog", "")
        new_actions = "\n".join(tool_actions[-15:])
        combined = f"{existing}\n{new_actions}" if existing else new_actions
        state.notes["Worklog"] = _truncate(combined, MAX_SECTION_CHARS)

    elapsed = time.time() - t0
    logger.info(
        f"[U1] Session memory extracted in {elapsed:.3f}s "
        f"(extract #{state.extract_count}, {len(files_seen)} files, "
        f"{len(errors_found)} errors, {len(tool_actions)} actions)"
    )


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, preserving line boundaries."""
    if len(text) <= max_chars:
        return text
    # Find last newline before limit
    truncated = text[:max_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars * 0.5:
        truncated = truncated[:last_nl]
    return truncated + "\n[...truncated]"


# ═══════════════════════════════════════════════════════════════
# Injection — Format notes for compact engine
# ═══════════════════════════════════════════════════════════════

def get_session_memory_for_compact(session_id: str) -> str:
    """Get formatted session memory for injection into compact summary.

    Returns empty string if no notes exist. Called by compact_engine
    after generating the LLM summary.
    """
    state = _SESSIONS.get(session_id)
    if not state:
        return ""
    notes_text = state.get_notes_text()
    if not notes_text or len(notes_text) < 50:
        return ""

    # Enforce total size limit
    if len(notes_text) > MAX_TOTAL_CHARS:
        notes_text = notes_text[:MAX_TOTAL_CHARS] + "\n[...session notes truncated]"

    return notes_text


def get_session_memory_for_context(session_id: str) -> str:
    """Get session memory formatted for dynamic context injection.

    Used by agentic_loop to inject session awareness into the
    conversation (lighter than full notes — just current state).
    """
    state = _SESSIONS.get(session_id)
    if not state or not state.notes:
        return ""

    # For context injection, only include Current State and Files
    parts = []
    current = state.notes.get("Current State", "")
    if current:
        parts.append(f"Current state: {current[:300]}")
    files = state.notes.get("Files and Functions", "")
    if files:
        parts.append(f"Active files:\n{files[:500]}")
    errors = state.notes.get("Errors & Corrections", "")
    if errors:
        parts.append(f"Recent errors:\n{errors[:300]}")

    if not parts:
        return ""

    return "[SESSION MEMORY]\n" + "\n\n".join(parts)
