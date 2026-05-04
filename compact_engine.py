"""
P34: Compact Engine — extracted from agentic_loop.py, upgraded to Service pattern

Contains:
- CompactService class — stateful service wrapping all compaction logic
- Token estimation and budget tracking
- Model resolution and API credentials
- Compaction prompts (P33)
- Partial compaction logic (P32)
- Auto-compact with heartbeat
- Session summary persistence (P20)
- CW2: Pre-flight Token Guard for compact requests
- CW6: 4-level token warning state (Claw calculateTokenWarningState)

Service pattern (Claw services/compact/):
  - CompactService encapsulates mutable state (counters, circuit breaker)
  - Feature-flag-gated operations via utils.feature_flags.ff
  - Clean public API: compact(), compact_with_heartbeat(), should_compact()
  - Backward-compatible module-level function aliases for existing callers
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

logger = logging.getLogger(__name__)

_CW2_NORMAL_TRIMS_TOTAL = 0   # Prometheus: CW2 normal 30% batch truncations
_CW2_FALLBACK_TRIMS_TOTAL = 0  # Prometheus: CW2 emergency fallback truncations


# ═══════════════════════════════════════════════════════════════
# Token Estimation
# ═══════════════════════════════════════════════════════════════

def _estimate_tokens(text: str) -> int:
    """P37: Content-type-aware token estimation.

    Different content types have very different chars-per-token ratios:
    - English prose: ~4 chars/token (BPE merges common words)
    - CJK text: ~1.5 chars/token (each character is often 1-2 tokens)
    - Code/JSON: ~3 chars/token (keywords merge, but symbols don't)
    - Mixed: weighted blend based on character composition

    This is still a heuristic — real tokenizer would be O(n) per call
    and add a dependency.  The blend approach gets within ~15% of real
    token counts vs the old flat //3 which was ~30% off for CJK-heavy text.
    """
    if not text:
        return 0
    n = len(text)
    if n < 20:
        # Very short strings: use conservative ratio
        return max(1, n // 3)

    # Sample a window (up to 500 chars) to detect content mix
    sample = text[:500] if n > 500 else text
    sample_len = len(sample)

    # Count character classes
    cjk = 0
    ascii_alpha = 0
    digits = 0
    symbols = 0  # braces, brackets, operators
    for ch in sample:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
                0xF900 <= cp <= 0xFAFF or 0x3000 <= cp <= 0x303F or
                0xFF00 <= cp <= 0xFFEF or 0xAC00 <= cp <= 0xD7AF):
            cjk += 1
        elif ch.isascii() and ch.isalpha():
            ascii_alpha += 1
        elif ch.isdigit():
            digits += 1
        elif ch in '{}[]();:=<>+-*/&|!@#$%^~`"\',.\\_':
            symbols += 1

    # Compute weighted ratio (chars per token)
    # Higher ratio = fewer tokens per char = more efficient encoding
    cjk_frac = cjk / sample_len if sample_len else 0
    code_frac = symbols / sample_len if sample_len else 0

    if cjk_frac > 0.3:
        # CJK-heavy: ~1.5 chars/token (each CJK char is 1-2 tokens)
        ratio = 1.5 + (1 - cjk_frac) * 2.0  # blend toward 3.5 for mixed
    elif code_frac > 0.15:
        # Code/JSON-heavy: ~3 chars/token
        ratio = 3.0
    else:
        # English prose: ~4 chars/token
        ratio = 4.0

    return max(1, int(n / ratio))


def _estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """Estimate total tokens across all messages including tool_calls.
    Uses P37 content-aware estimation."""
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        total += _estimate_tokens(content) + 4  # message overhead
        # Tool call arguments (usually JSON — code-like ratio)
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            args_str = func.get("arguments", "")
            total += _estimate_tokens(func.get("name", "")) + max(1, len(args_str) // 3)
    return total


def _get_context_ceiling(env: Dict[str, str]) -> int:
    """Get the effective context ceiling in tokens.
    For llama.cpp / Ollama: num_ctx (default 32768 — modern GGUF models
    support 32K+ context; 8K is too small for agentic file-reading workloads).
    Reserve 15% for output tokens."""
    num_ctx = int(env.get("OLLAMA_NUM_CTX", "32768"))
    max_output = int(env.get("NANOBOT_AGENTS__DEFAULTS__MAX_TOKENS", "16384"))
    # Effective input budget = context window - reserved output (15% or max_output, whichever is smaller)
    reserved = min(max_output, int(num_ctx * 0.15))
    return max(2000, num_ctx - reserved)


# ═══════════════════════════════════════════════════════════════
# P5: Token Budget Tracker
# ═══════════════════════════════════════════════════════════════

class TokenBudgetTracker:
    """Track cumulative token usage across an agentic session.

    Uses real LLM `usage` when the provider returns it (OpenAI, llama.cpp
    with --metrics), otherwise falls back to character-based estimation.

    Stats are injected into the `agentic_done` SSE event so the frontend
    can display total consumption.
    """
    __slots__ = (
        "ceiling", "cumulative_input", "cumulative_output",
        "compaction_count", "tokens_saved", "_use_real",
    )

    def __init__(self, ceiling: int):
        self.ceiling = ceiling
        self.cumulative_input = 0
        self.cumulative_output = 0
        self.compaction_count = 0
        self.tokens_saved = 0
        self._use_real = False

    def update_from_llm_usage(self, stream_stats: Optional[Dict[str, Any]]) -> None:
        """Update from LLM response usage dict (prompt_tokens, completion_tokens)."""
        if not stream_stats:
            return
        prompt_tokens = stream_stats.get("prompt_tokens", 0)
        completion_tokens = stream_stats.get("completion_tokens", 0)
        if prompt_tokens > 0:
            self._use_real = True
            self.cumulative_input += prompt_tokens
            self.cumulative_output += completion_tokens

    def update_from_estimate(self, messages: List[Dict[str, Any]], output_text: str) -> None:
        """Fallback: estimate tokens from messages and output text.
        Ignored once real usage data has arrived."""
        if self._use_real:
            return
        self.cumulative_input = _estimate_messages_tokens(messages)
        self.cumulative_output += _estimate_tokens(output_text)

    def record_compaction(self, old_tokens: int, new_tokens: int) -> None:
        """Record a compaction event."""
        self.compaction_count += 1
        self.tokens_saved += max(0, old_tokens - new_tokens)

    # CW6: 4-level token warning thresholds (Claw calculateTokenWarningState)
    # Configurable via env vars; defaults match Claw.
    _WARNING_RATIO = float(os.environ.get("CW6_WARNING_RATIO", "0.70"))
    _AUTOCOMPACT_RATIO = float(os.environ.get("CW6_AUTOCOMPACT_RATIO", "0.80"))
    _ERROR_RATIO = float(os.environ.get("CW6_ERROR_RATIO", "0.90"))
    _BLOCKING_RATIO = float(os.environ.get("CW6_BLOCKING_RATIO", "0.95"))

    def should_compact(self, current_tokens: int) -> bool:
        """Check if current context exceeds 80% of ceiling."""
        return current_tokens > int(self.ceiling * self._AUTOCOMPACT_RATIO)

    def get_token_warning_state(self, current_tokens: int) -> str:
        """CW6: Return 4-level warning state based on context pressure.

        Returns one of: 'normal', 'warning', 'autoCompact', 'error', 'blocking'.
        Matches Claw calculateTokenWarningState (autoCompact.ts:93-145).
        """
        if current_tokens > int(self.ceiling * self._BLOCKING_RATIO):
            return "blocking"
        if current_tokens > int(self.ceiling * self._ERROR_RATIO):
            return "error"
        if current_tokens > int(self.ceiling * self._AUTOCOMPACT_RATIO):
            return "autoCompact"
        if current_tokens > int(self.ceiling * self._WARNING_RATIO):
            return "warning"
        return "normal"

    def get_stats(self) -> Dict[str, Any]:
        return {
            "token_budget": {
                "ceiling": self.ceiling,
                "cumulative_input": self.cumulative_input,
                "cumulative_output": self.cumulative_output,
                "cumulative_total": self.cumulative_input + self.cumulative_output,
                "compaction_count": self.compaction_count,
                "tokens_saved": self.tokens_saved,
                "source": "real" if self._use_real else "estimated",
            }
        }


# ═══════════════════════════════════════════════════════════════
# Model Resolution & API Credentials
# ═══════════════════════════════════════════════════════════════

def _resolve_model(env: Dict[str, str]) -> str:
    """Resolve the model name from env."""
    model = env.get("NANOBOT_AGENTS__DEFAULTS__MODEL")
    if model:
        return model
    try:
        config_path = Path.home() / ".nanobot" / "config.json"
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            configured = data.get("agents", {}).get("defaults", {}).get("model")
            if configured:
                return configured
    except Exception:
        pass
    return "openai/qwen3.5:35b"


def _get_api_credentials(env: Dict[str, str], model: str) -> tuple:
    """Get API key and base URL from env or config."""
    api_key = env.get("OPENAI_API_KEY")
    api_base = env.get("OPENAI_API_BASE")
    if not api_key or not api_base:
        try:
            config_path = Path.home() / ".nanobot" / "config.json"
            if config_path.exists():
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                provider_name = model.split("/", 1)[0] if "/" in model else model
                if provider_name == "ollama_chat":
                    provider_name = "ollama"
                provider_cfg = cfg.get("providers", {}).get(provider_name, {})
                if not api_key:
                    api_key = provider_cfg.get("apiKey")
                if not api_base:
                    api_base = provider_cfg.get("apiBase")
        except Exception as e:
            logger.warning(f"[AgenticLoop] Failed to read provider config: {e}")
    return api_key, api_base


# ═══════════════════════════════════════════════════════════════
# P33: Compaction Prompts — Claw prompt.ts full port
# Two variants: BASE (full conversation) and PARTIAL (recent only)
# Both use <analysis> scratchpad + <summary> XML structure
# ═══════════════════════════════════════════════════════════════

_NO_TOOLS_PREAMBLE = (
    "CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.\n"
    "Tool calls will be REJECTED and your turn will be wasted.\n"
    "Your entire response must be plain text: "
    "an <analysis> scratchpad followed by a <summary> block.\n"
    "Do NOT output function_call, tool_use, or any JSON tool invocation.\n\n"
)

_DETAILED_ANALYSIS_INSTRUCTION = (
    "Before providing your final summary, wrap your analysis in <analysis> tags "
    "to organize your thoughts and ensure you've covered all necessary points. "
    "In your analysis process:\n\n"
    "1. Chronologically analyze each message and section of the conversation. "
    "For each section thoroughly identify:\n"
    "   - The user's explicit requests and intents\n"
    "   - Your approach to addressing the user's requests\n"
    "   - Key decisions, technical concepts and code patterns\n"
    "   - Specific details like:\n"
    "     - file names and FULL absolute paths\n"
    "     - full code snippets (especially recent edits)\n"
    "     - function signatures and class names\n"
    "     - file edits (old_string → new_string changes)\n"
    "     - match counts, line numbers, error messages\n"
    "   - Errors that you ran into and how you fixed them\n"
    "   - Pay special attention to specific user feedback that you received, "
    "especially if the user told you to do something differently.\n"
    "2. List every user message verbatim (not tool results). These preserve intent.\n"
    "3. Double-check for technical accuracy and completeness, "
    "addressing each required element thoroughly.\n"
    "4. Verify you have not dropped any file paths, error messages, or "
    "numeric values (counts, line numbers) from tool results.\n"
    "5. Do NOT record information derivable from the code itself "
    "(file structure, git history, function bodies). Focus on decisions, intent, "
    "and context that would be lost without this summary."
)

# U7: 9-section Claw-style compact template
_COMPACT_SECTIONS = (
    "Your summary MUST include exactly these 9 sections in order:\n\n"
    "1. **Primary Request and Intent**: Capture ALL of the user's explicit requests and intents in detail. "
    "Include the original motivation and any evolving requirements.\n"
    "2. **Key Technical Concepts**: List all important technical concepts, technologies, patterns, "
    "and frameworks discussed. Only include concepts NOT derivable from the code itself.\n"
    "3. **Files and Code Sections**: Enumerate specific files and code sections examined, modified, or created. "
    "Include FULL absolute paths, line ranges, and a summary of WHY each file matters. "
    "For recent edits, include the old_string → new_string change.\n"
    "4. **Errors and Fixes**: List all errors encountered and how they were resolved. "
    "Include user feedback that corrected your approach — these are high-value signals.\n"
    "5. **Problem Solving**: Document problems solved, root cause analysis, and ongoing troubleshooting.\n"
    "6. **All User Messages**: List ALL user messages VERBATIM (not tool results). "
    "Copy the exact text. These preserve intent and are critical for understanding changing requirements.\n"
    "7. **Pending Tasks**: Outline pending tasks with status: COMPLETED / IN PROGRESS / PENDING. "
    "For IN PROGRESS, describe exactly where you left off with direct quotes.\n"
    "8. **Current Work**: Describe precisely what was being worked on immediately before compaction. "
    "Include file names, code snippets, and the specific step you were on.\n"
    "9. **Optional Next Step**: List the next step ONLY if it is directly in line with the user's "
    "most recent explicit request. Include direct quotes showing the task context. "
    "If the last task was concluded, state that clearly."
)

# U7: Compact example with 9 sections
_COMPACT_EXAMPLE = """
Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process — walk through the conversation chronologically,
ensure all 9 sections are covered thoroughly and accurately]
</analysis>

<summary>
1. **Primary Request and Intent**:
   [Detailed description of what the user asked for and why]

2. **Key Technical Concepts**:
   - [Concept 1]
   - [Concept 2]

3. **Files and Code Sections**:
   - `/absolute/path/to/file.py` (lines 10-50)
     - Why: [reason this file matters]
     - Changes: [old_string → new_string summary]
   - `/absolute/path/to/other.py`
     - [Code snippet]

4. **Errors and Fixes**:
   - [Error description]: [How fixed] [User feedback if any]

5. **Problem Solving**:
   [Root cause analysis and resolution]

6. **All User Messages**:
   - "[Exact verbatim user message 1]"
   - "[Exact verbatim user message 2]"

7. **Pending Tasks**:
   - [Task 1]: COMPLETED
   - [Task 2]: IN PROGRESS — [where you left off]
   - [Task 3]: PENDING

8. **Current Work**:
   [Precise description of current work with file names and code]

9. **Optional Next Step**:
   [Next step or "Previous task concluded"]

</summary>
</example>
"""

_NO_TOOLS_TRAILER = (
    "\n\nREMINDER: Do NOT call any tools. Respond with plain text only — "
    "an <analysis> block followed by a <summary> block."
)

# Full compaction prompt (summarizes entire conversation)
_COMPACT_PROMPT = (
    _NO_TOOLS_PREAMBLE
    + "Your task is to create a detailed summary of the conversation so far, "
    "paying close attention to the user's explicit requests and your previous actions.\n"
    "This summary should be thorough in capturing technical details, code patterns, "
    "and architectural decisions that would be essential for continuing development "
    "work without losing context.\n\n"
    + _DETAILED_ANALYSIS_INSTRUCTION + "\n\n"
    + _COMPACT_SECTIONS + "\n"
    + _COMPACT_EXAMPLE
    + "Please provide your summary based on the conversation so far, "
    "following this structure and ensuring precision and thoroughness."
    + _NO_TOOLS_TRAILER
)

# P32: Partial compaction prompt (summarizes only OLD messages, recent kept intact)
_PARTIAL_COMPACT_PROMPT = (
    _NO_TOOLS_PREAMBLE
    + "Your task is to create a detailed summary of the OLDER portion of the conversation — "
    "the messages shown below. Recent messages are being kept intact and do NOT need to be "
    "summarized. Focus your summary on what was discussed, learned, and accomplished in "
    "these older messages only.\n\n"
    + _DETAILED_ANALYSIS_INSTRUCTION + "\n\n"
    + _COMPACT_SECTIONS + "\n"
    + _COMPACT_EXAMPLE
    + "Please provide your summary based on the OLDER messages only "
    "(recent messages are preserved separately), following this structure."
    + _NO_TOOLS_TRAILER
)

# P32: How many recent messages to keep intact during partial compaction
# 6 = typically 3 user+assistant exchanges (the most recent work context)
_PARTIAL_COMPACT_KEEP_RECENT = 6


def _format_compact_summary(raw: str) -> str:
    """Strip <analysis> scratchpad and extract <summary> content (Claw pattern).
    Also cleans up extra whitespace between sections."""
    import re
    # Strip analysis block
    text = re.sub(r'<analysis>[\s\S]*?</analysis>', '', raw)
    # Extract summary content
    match = re.search(r'<summary>([\s\S]*?)</summary>', text)
    if match:
        text = match.group(1).strip()
    else:
        text = text.strip()
    # Clean up extra whitespace
    text = re.sub(r'\n\n+', '\n\n', text)
    return text


def _find_partial_pivot(messages: List[Dict[str, Any]]) -> int:
    """Find the pivot index for partial compaction (P32).

    Walks backward from the end, counting user+assistant role boundaries.
    Returns the index where old messages end and recent messages begin.
    The goal: keep the last _PARTIAL_COMPACT_KEEP_RECENT messages intact.

    If not enough messages to split, returns -1 (fall back to full compact).
    """
    n = len(messages)
    # Skip system messages at the start
    first_non_system = 0
    for i, msg in enumerate(messages):
        if msg.get("role") != "system":
            first_non_system = i
            break

    # We need at least KEEP_RECENT messages after the pivot AND at least 2 before
    min_old = 2  # need at least 2 messages to summarize
    keep = _PARTIAL_COMPACT_KEEP_RECENT

    if n - first_non_system <= keep + min_old:
        return -1  # not enough messages for partial compaction

    pivot = n - keep
    # Ensure pivot doesn't split a tool_call + tool_result pair
    # Walk backward to find a clean boundary (user message start)
    while pivot > first_non_system + min_old:
        msg = messages[pivot]
        role = msg.get("role", "")
        # Good boundary: start of a user message (not a tool result)
        if role == "user" and not msg.get("tool_call_id"):
            break
        pivot -= 1

    if pivot <= first_non_system + min_old:
        return -1  # couldn't find a clean split

    return pivot


def _messages_to_text(messages: List[Dict[str, Any]]) -> str:
    """Convert messages list to readable text for the summarizer."""
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        if content:
            # Truncate very long messages for the summarizer
            if len(content) > 2000:
                content = content[:1500] + f"\n... ({len(content)} chars total, truncated)"
            parts.append(f"[{role}]: {content}")
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            parts.append(f"[{role} TOOL_CALL]: {func.get('name', '?')}({func.get('arguments', '')[:200]})")
    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# P20: Session Summary Persistence
# ═══════════════════════════════════════════════════════════════

def _save_session_summary(workspace: Path, session_id: str, summary: str) -> None:
    """Save a compaction summary to disk for cross-session memory."""
    try:
        summary_dir = workspace / ".session_summaries"
        summary_dir.mkdir(parents=True, exist_ok=True)
        summary_file = summary_dir / f"{session_id}.md"
        tmp_file = summary_file.with_suffix(summary_file.suffix + ".tmp")
        tmp_file.write_text(
            f"# Session Summary ({session_id})\n"
            f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"{summary}\n",
            encoding="utf-8",
        )
        tmp_file.replace(summary_file)
        logger.info(f"[P20] Saved session summary ({len(summary)} chars) → {summary_file}")
    except Exception as e:
        logger.warning(f"[P20] Failed to save session summary: {e}")


def _load_previous_summary(workspace: Path, current_session_id: str) -> str:
    """Load the most recent session summary (excluding current session) for context.

    Returns the summary content or empty string if none found.
    Only loads the MOST RECENT previous summary to keep context lean.
    """
    try:
        summary_dir = workspace / ".session_summaries"
        if not summary_dir.is_dir():
            return ""

        # Find all summary files, sorted by mtime descending
        summaries = sorted(
            summary_dir.glob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for s in summaries:
            if s.stem == current_session_id:
                continue  # skip current session
            content = s.read_text(encoding="utf-8", errors="replace").strip()
            if content and len(content) > 50:
                # Cap at 2K to avoid bloating the prompt
                if len(content) > 2000:
                    content = content[:2000] + "\n... [summary truncated]"
                logger.info(f"[P20] Loaded previous session summary from {s.name} ({len(content)} chars)")
                return content
        return ""
    except Exception as e:
        logger.warning(f"[P20] Failed to load previous summary: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════
# Auto-Compact (P32 Partial + P33 Upgraded Prompt)
# ═══════════════════════════════════════════════════════════════

async def _auto_compact(
    messages: List[Dict[str, Any]],
    env: Dict[str, str],
    session_id: str,
    workspace: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """P32/P33: Summarize older messages via LLM when tokens exceed ceiling.
    Returns event dict if compaction happened, None otherwise.

    Strategy (Claw partialCompact pattern):
    1. Try PARTIAL compaction: keep last N messages intact, only summarize old.
    2. If not enough messages for partial split → fall back to FULL compaction.
    3. Fallback on LLM failure: snip oldest messages (no LLM call).
    """
    ceiling = _get_context_ceiling(env)
    current_tokens = _estimate_messages_tokens(messages)

    # Threshold: compact when at 80% of ceiling
    threshold = int(ceiling * 0.80)

    if current_tokens <= threshold:
        return None

    if len(messages) < 4:
        return None  # Not enough messages to compact

    # P32: Try partial compaction first
    pivot = _find_partial_pivot(messages)
    is_partial = pivot > 0
    if is_partial:
        # Partial: summarize messages[first_non_system:pivot], keep messages[pivot:]
        system_msgs = [m for m in messages[:pivot] if m.get("role") == "system"]
        old_msgs = [m for m in messages[:pivot] if m.get("role") != "system"]
        recent_tail = messages[pivot:]
        prompt = _PARTIAL_COMPACT_PROMPT
        mode_label = "Partial"
    else:
        # Full: summarize everything, keep last 2
        system_msgs = [messages[0]] if messages[0].get("role") == "system" else []
        old_msgs = [m for m in messages if m.get("role") != "system"]
        recent_tail = messages[-2:] if len(messages) >= 2 else messages[-1:]
        prompt = _COMPACT_PROMPT
        mode_label = "Full"

    logger.warning(
        f"[AutoCompact] {mode_label}: {current_tokens} tokens > threshold {threshold} "
        f"(ceiling={ceiling}), {len(old_msgs)} msgs to summarize, {len(recent_tail)} kept"
    )

    # CW2: Pre-flight Token Guard — truncate old_msgs if compact request
    # itself would exceed the model's context window.  This prevents the
    # compact LLM call from silently failing or truncating on local models
    # (llama.cpp/Ollama) that don't return explicit PTL errors.
    global _CW2_NORMAL_TRIMS_TOTAL, _CW2_FALLBACK_TRIMS_TOTAL
    _compact_ceiling = int(env.get("OLLAMA_NUM_CTX", "8192"))
    _cw2_limit = int(_compact_ceiling * 0.90)  # 90% of model ctx for compact request
    _cw2_prompt_overhead = _estimate_tokens(prompt) + 100  # prompt + system msg overhead
    for _cw2_round in range(2):  # max 2 truncation rounds
        _summarize_content = _messages_to_text(
            system_msgs + old_msgs if is_partial else messages
        )
        _cw2_request_tokens = _estimate_tokens(_summarize_content) + _cw2_prompt_overhead
        if _cw2_request_tokens <= _cw2_limit:
            break
        # Trim oldest 30% of old_msgs
        _trim_count = max(1, len(old_msgs) * 30 // 100)
        _CW2_NORMAL_TRIMS_TOTAL += 1
        logger.info(
            f"[CW2] Pre-flight guard round {_cw2_round + 1}: compact request ~{_cw2_request_tokens} tokens "
            f"> limit {_cw2_limit}, trimming {_trim_count} oldest message(s)"
        )
        old_msgs = old_msgs[_trim_count:]
        if not old_msgs:
            break

    # CW2 fallback: if still over limit after 2 rounds, force-truncate to fit
    if old_msgs:
        _summarize_content = _messages_to_text(
            system_msgs + old_msgs if is_partial else messages
        )
        _cw2_request_tokens = _estimate_tokens(_summarize_content) + _cw2_prompt_overhead
        if _cw2_request_tokens > _cw2_limit:
            _cw2_hard_limit = int(_compact_ceiling * 0.80)  # 80% absolute safety ceiling
            while old_msgs and _cw2_request_tokens > _cw2_hard_limit:
                old_msgs = old_msgs[1:]  # drop one message at a time
                _summarize_content = _messages_to_text(
                    system_msgs + old_msgs if is_partial else messages
                )
                _cw2_request_tokens = _estimate_tokens(_summarize_content) + _cw2_prompt_overhead
            # Single-message edge case: if last remaining message still exceeds
            # hard limit, truncate its content to fit within budget
            if old_msgs and _cw2_request_tokens > _cw2_hard_limit:
                _avail_chars = max(200, int((_cw2_hard_limit - _cw2_prompt_overhead) * 3))
                old_msgs[0] = dict(old_msgs[0])  # avoid mutating original
                old_msgs[0]["content"] = old_msgs[0].get("content", "")[:_avail_chars] + "\n[truncated by CW2 guard]"
                logger.critical(
                    f"[CW2] Single-message exceeds hard limit: content truncated to {_avail_chars} chars"
                )
            _CW2_FALLBACK_TRIMS_TOTAL += 1
            logger.error(
                f"[CW2] Fallback force-truncation: compact request still ~{_cw2_request_tokens} tokens "
                f"after 2 rounds, force-trimmed old_msgs to {len(old_msgs)} message(s)"
            )

    # Build compact request
    compact_messages = [
        {"role": "system", "content": "You are a conversation summarizer. Produce a precise summary."},
        {"role": "user", "content": prompt + "\n\nConversation to summarize:\n" + _messages_to_text(
            system_msgs + old_msgs if is_partial else messages
        )},
    ]

    try:
        from litellm import acompletion
    except ModuleNotFoundError:
        py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
        venv_site = Path.home() / ".nanobot" / "venv" / "lib" / py_ver / "site-packages"
        if venv_site.exists() and str(venv_site) not in sys.path:
            sys.path.insert(0, str(venv_site))
        from litellm import acompletion

    model = _resolve_model(env)
    api_key, api_base = _get_api_credentials(env, model)

    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": compact_messages,
        "max_tokens": 4000,  # Claw uses 20K; we use 4K for smaller models
        "temperature": 0.3,
        "stream": False,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base

    model_lower = model.lower()
    if "ollama" in model_lower or "ollama_chat" in model_lower:
        kwargs["num_ctx"] = int(env.get("OLLAMA_NUM_CTX", "8192"))

    try:
        response = await asyncio.wait_for(acompletion(**kwargs), timeout=30)
        raw_summary = response.choices[0].message.content or ""
        summary = _format_compact_summary(raw_summary)

        if len(summary) < 50:
            logger.warning("[AutoCompact] Summary too short, skipping compaction")
            return None

        # P25: Extract file paths from summarized messages to preserve read state
        compacted_file_paths = []
        summarized_msgs = (system_msgs + old_msgs) if is_partial else messages
        for msg in summarized_msgs:
            if msg.get("_tool_name") == "file_read":
                content = msg.get("content", "")
                import re as _re25
                m = _re25.search(r'\[File:\s*(\S+)\s*\|', content)
                if m:
                    compacted_file_paths.append(m.group(1))
        if compacted_file_paths:
            file_list_str = ", ".join(compacted_file_paths[:20])
            summary += f"\n\nFiles previously read (still available for editing): {file_list_str}"
            logger.info(f"[P25] Preserved {len(compacted_file_paths)} file read states across compaction")

        # A1: Extract sub-agent summaries from compacted messages and preserve them
        import re as _reA1
        _sub_agent_summaries = []
        for msg in summarized_msgs:
            content = msg.get("content", "")
            if _reA1.search(r'\[Sub-agent\s+\w+\s+summary:', content):
                # Extract the header line + first 500 chars of the body
                header_end = content.find("\n\n")
                if header_end > 0:
                    header = content[:header_end]
                    body = content[header_end + 2:]
                    # Keep first 500 chars of body as essential findings
                    body_preview = body[:500]
                    if len(body) > 500:
                        body_preview += "..."
                    _sub_agent_summaries.append(f"{header}\n{body_preview}")
                else:
                    _sub_agent_summaries.append(content[:600])
        if _sub_agent_summaries:
            summary += "\n\n[Preserved sub-agent findings from earlier in this session]:\n"
            summary += "\n\n".join(_sub_agent_summaries[:5])  # max 5 summaries
            logger.info(f"[A1] Preserved {len(_sub_agent_summaries)} sub-agent summary(ies) across compaction")

        # P100e: Preserve invoked skill prompts across compaction
        try:
            from skills import get_invoked_skills
            invoked = get_invoked_skills()
            if invoked:
                skill_names = list(invoked.keys())[:5]  # cap at 5 skills
                summary += f"\n\nActive skill context: {', '.join('/' + n for n in skill_names)}"
                # Include truncated skill prompt so model retains awareness
                for sn in skill_names:
                    sp = invoked[sn]
                    if len(sp) > 500:
                        sp = sp[:500] + "..."
                    summary += f"\n[/{sn} prompt excerpt]: {sp}"
                logger.info(f"[P100e] Preserved {len(skill_names)} invoked skill(s) across compaction")
        except Exception as e:
            logger.debug(f"[P100e] Skill preservation skipped: {e}")

        # U1: Inject session memory notes into compact summary
        try:
            from session_memory import get_session_memory_for_compact
            _sm_notes = get_session_memory_for_compact(session_id)
            if _sm_notes:
                summary += f"\n\n[SESSION MEMORY — structured notes from this session]:\n{_sm_notes}"
                logger.info(f"[U1] Injected {len(_sm_notes)} chars of session memory into compact summary")
        except Exception as _e_sm:
            logger.debug(f"[U1] Session memory injection skipped: {_e_sm}")

        # Build post-compact summary message (Claw getCompactUserSummaryMessage)
        summary_content = (
            "This session is being continued from a previous conversation that ran out of context. "
            "The summary below covers the earlier portion of the conversation.\n\n"
            f"Summary:\n{summary}"
        )
        if is_partial:
            summary_content += "\n\nRecent messages are preserved verbatim."
        summary_content += (
            "\n\nContinue from where we left off. Resume directly — "
            "do not acknowledge the summary, do not recap what was happening."
        )

        new_messages = list(system_msgs)
        new_messages.append({
            "role": "user",
            "content": summary_content,
        })
        new_messages.append({
            "role": "assistant",
            "content": "Understood. I have the context. Continuing.",
        })
        new_messages.extend(recent_tail)

        # U9: Post-Compact File Restore — inject hints for recently active files
        # so the model knows which key files to re-read without losing context
        try:
            from agentic_loop import get_active_files_snapshot
            _recent = get_active_files_snapshot(max_files=5)
            if _recent:
                _file_hints = []
                for _path, _info in _recent:
                    _desc = _info.get("summary", "")
                    _lines = _info.get("lines", 0)
                    _hint = f"- `{_path}` ({_lines} lines)"
                    if _desc:
                        _hint += f" — {_desc}"
                    _file_hints.append(_hint)
                if _file_hints:
                    new_messages.append({
                        "role": "system",
                        "content": (
                            "[Post-compact file restore] These files were active before compaction. "
                            "Re-read any that are needed for the current task:\n"
                            + "\n".join(_file_hints)
                        ),
                    })
                    logger.info(f"[U9] Injected {len(_file_hints)} file restore hints post-compact")
        except Exception as _e_u9:
            logger.debug(f"[U9] File restore skipped: {_e_u9}")

        new_tokens = _estimate_messages_tokens(new_messages)
        logger.info(
            f"[AutoCompact] {mode_label}: {current_tokens} → {new_tokens} tokens "
            f"({len(messages)} → {len(new_messages)} messages)"
        )

        # P20: Persist summary to disk for cross-session memory
        if workspace:
            _save_session_summary(workspace, session_id, summary)

        return {
            "compacted": True,
            "new_messages": new_messages,
            "old_tokens": current_tokens,
            "new_tokens": new_tokens,
            "summary_length": len(summary),
            "mode": "partial" if is_partial else "full",
        }
    except asyncio.TimeoutError:
        logger.error("[AutoCompact] LLM summary timed out (120s), falling back to snip")
        # Fallback: snip oldest messages (keep system + last 60%)
        keep_count = max(3, int(len(messages) * 0.6))
        system_msg = messages[0] if messages[0].get("role") == "system" else None
        snipped = messages[-keep_count:]
        if system_msg and snipped[0].get("role") != "system":
            snipped = [system_msg] + snipped
        new_tokens = _estimate_messages_tokens(snipped)
        logger.info(f"[AutoCompact] Fallback snip (timeout): {current_tokens} → {new_tokens} tokens")
        return {
            "compacted": True,
            "new_messages": snipped,
            "old_tokens": current_tokens,
            "new_tokens": new_tokens,
            "summary_length": 0,
            "mode": "timeout_fallback",
        }
    except Exception as e:
        logger.error(f"[AutoCompact] LLM summary failed: {e}")
        # Fallback: snip oldest messages (keep system + last 60%)
        keep_count = max(3, int(len(messages) * 0.6))
        system_msg = messages[0] if messages[0].get("role") == "system" else None
        snipped = messages[-keep_count:]
        if system_msg and snipped[0].get("role") != "system":
            snipped = [system_msg] + snipped
        new_tokens = _estimate_messages_tokens(snipped)
        logger.info(f"[AutoCompact] Fallback snip: {current_tokens} → {new_tokens} tokens")
        return {
            "compacted": True,
            "new_messages": snipped,
            "old_tokens": current_tokens,
            "new_tokens": new_tokens,
            "summary_length": 0,
            "mode": "fallback",
        }


async def _auto_compact_with_heartbeat(
    messages: List[Dict[str, Any]],
    env: Dict[str, str],
    session_id: str,
    heartbeat_interval: float = 10.0,
    workspace: Optional[Path] = None,
):
    """Run _auto_compact in background while yielding heartbeat events.
    
    Yields:
        {"type": "heartbeat", ...} periodically during LLM compaction
        {"type": "compact_result", "result": <dict or None>} when done
    """
    compact_task = asyncio.ensure_future(_auto_compact(messages, env, session_id, workspace=workspace))
    _start = asyncio.get_running_loop().time()
    
    while not compact_task.done():
        try:
            await asyncio.wait_for(asyncio.shield(compact_task), timeout=heartbeat_interval)
        except asyncio.TimeoutError:
            # Task still running — yield heartbeat to keep SSE alive
            _elapsed = int(asyncio.get_running_loop().time() - _start)
            yield {"type": "heartbeat", "status": "compacting", "elapsed": _elapsed}
        except Exception:
            break  # Task raised — will be caught below
    
    # Retrieve the result (or exception)
    try:
        result = compact_task.result()
    except Exception as exc:
        logger.error(f"[AutoCompact] Unexpected error: {exc}")
        result = None
    
    yield {"type": "compact_result", "result": result}


# ═══════════════════════════════════════════════════════════════
# CompactService — stateful service wrapping all compaction logic
# Mirrors Claw services/compact/ pattern with encapsulated state
# ═══════════════════════════════════════════════════════════════

class CompactService:
    """Stateful compaction service (Claw services/compact/ pattern).

    Encapsulates:
      - Circuit breaker state (CW3)
      - CW2 trim counters
      - Feature flag gating
      - Token budget tracking integration
      - Clean public API for agentic_loop.py

    Usage:
        svc = CompactService(env)
        if svc.should_compact(messages):
            result = await svc.compact(messages, session_id, workspace)
    """

    def __init__(self, env: Dict[str, str]):
        self._env = env
        self._ceiling = _get_context_ceiling(env)
        self._compact_failures = 0      # CW3: consecutive failure counter
        self._breaker_open = False       # CW3: circuit breaker state
        self._breaker_open_turn = 0     # CW3: turn when breaker opened
        self._total_compactions = 0     # Lifetime compaction counter
        self._total_tokens_saved = 0    # Lifetime tokens saved

    @property
    def ceiling(self) -> int:
        return self._ceiling

    @property
    def is_breaker_open(self) -> bool:
        return self._breaker_open

    @property
    def stats(self) -> Dict[str, Any]:
        """Service-level stats for introspection."""
        return {
            "ceiling": self._ceiling,
            "compact_failures": self._compact_failures,
            "breaker_open": self._breaker_open,
            "total_compactions": self._total_compactions,
            "total_tokens_saved": self._total_tokens_saved,
        }

    def should_compact(self, messages: List[Dict[str, Any]]) -> bool:
        """Check if messages exceed compaction threshold (80% of ceiling).

        Respects feature flag: if auto_compact is disabled, always returns False.
        Also respects circuit breaker state (CW3).
        """
        try:
            from utils.feature_flags import ff
            if not ff.is_enabled("auto_compact"):
                return False
        except ImportError:
            pass

        if self._breaker_open:
            return False

        current_tokens = _estimate_messages_tokens(messages)
        threshold = int(self._ceiling * 0.80)
        return current_tokens > threshold

    def get_token_pressure(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get current token pressure metrics."""
        current_tokens = _estimate_messages_tokens(messages)
        return {
            "current_tokens": current_tokens,
            "ceiling": self._ceiling,
            "threshold": int(self._ceiling * 0.80),
            "utilization": round(current_tokens / max(1, self._ceiling), 3),
            "messages": len(messages),
        }

    async def compact(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
        workspace: Optional[Path] = None,
    ) -> Optional[Dict[str, Any]]:
        """Execute compaction (delegates to _auto_compact).

        Manages circuit breaker state: opens after 3 consecutive failures,
        half-open after 5 turns for probing.

        Returns:
            Compaction result dict, or None if skipped/failed.
        """
        try:
            from utils.feature_flags import ff
            if not ff.is_enabled("auto_compact"):
                return None
        except ImportError:
            pass

        if self._breaker_open:
            logger.debug("[CompactService] Circuit breaker open, skipping compaction")
            return None

        try:
            result = await _auto_compact(messages, self._env, session_id, workspace=workspace)
            if result and result.get("compacted"):
                self._compact_failures = 0  # Reset on success
                self._total_compactions += 1
                old_t = result.get("old_tokens", 0)
                new_t = result.get("new_tokens", 0)
                self._total_tokens_saved += max(0, old_t - new_t)
                return result
            return result
        except Exception as e:
            self._compact_failures += 1
            logger.warning(f"[CompactService] Compact failed ({self._compact_failures}/3): {e}")
            if self._compact_failures >= 3:
                self._breaker_open = True
                logger.error("[CompactService] Circuit breaker OPENED after 3 consecutive failures")
            return None

    async def compact_with_heartbeat(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
        workspace: Optional[Path] = None,
        heartbeat_interval: float = 10.0,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Compact with heartbeat events (delegates to _auto_compact_with_heartbeat).

        Yields heartbeat events during compaction, then yields compact_result.
        Manages circuit breaker state.
        """
        try:
            from utils.feature_flags import ff
            if not ff.is_enabled("auto_compact"):
                yield {"type": "compact_result", "result": None}
                return
        except ImportError:
            pass

        if self._breaker_open:
            yield {"type": "compact_result", "result": None}
            return

        result = None
        async for event in _auto_compact_with_heartbeat(
            messages, self._env, session_id,
            heartbeat_interval=heartbeat_interval, workspace=workspace,
        ):
            if event.get("type") == "compact_result":
                result = event.get("result")
                if result and result.get("compacted"):
                    self._compact_failures = 0
                    self._total_compactions += 1
                    old_t = result.get("old_tokens", 0)
                    new_t = result.get("new_tokens", 0)
                    self._total_tokens_saved += max(0, old_t - new_t)
                elif result is None:
                    self._compact_failures += 1
                    if self._compact_failures >= 3:
                        self._breaker_open = True
                        logger.error("[CompactService] Circuit breaker OPENED after 3 consecutive failures")
            yield event

    def probe_half_open(self, current_turn: int) -> None:
        """CW3: Attempt half-open probe after 5 turns of breaker being open."""
        if self._breaker_open and (current_turn - self._breaker_open_turn) >= 5:
            logger.info("[CompactService] Circuit breaker half-open, allowing probe")
            self._breaker_open = False
            self._breaker_open_turn = current_turn

    def reset(self) -> None:
        """Reset service state (e.g. on session start)."""
        self._compact_failures = 0
        self._breaker_open = False
        self._breaker_open_turn = 0
