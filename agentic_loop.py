"""
Nanobot Agentic Loop — 自主工具调用循环

核心设计（参考 Claw queryLoop）：
  while turn < max_turns:
    1. 调用 LLM (带 tools 参数，OpenAI function calling)
    2. 流式输出文本 token → SSE chunk
    3. 如果模型请求 tool_calls → 执行工具 → 注入结果 → 继续循环
    4. 如果模型停止（无 tool_calls）→ 返回最终回复

优化历史（Claw 灵感）：
  P0:  Tool concurrency — read-only tools run in parallel
  P3:  Tiered MicroCompact — per-tool truncation with age decay
  P5:  Token budget tracker
  P7:  Table fill detection + tool nudge
  P9:  Large result disk persistence
  P10: Reactive compaction on context overflow
  P22: Time-based microcompact
  P23: Sibling error cascading abort
  P24: Output efficiency upgrade
  P25: Compaction file state preservation
  P26: Tool result summary
  P27: file_read limit auto-correction
  P28: Runtime LaTeX stripper
  P29: Pre-tool narration scrub
  P30: Post-tool conciseness injection
  P33: Pre-flight planning
  P34: System prompt modularization
  P38: Mandatory tool detection
  P42: Context cross-instruction confusion prevention
  P44: Tool result disk persistence upgrade
  P52: Workspace path strengthening
  B16: Full-file auto-sharding for file_read
  CW1: Token-weighted microcompact eviction (audit-driven)
  CW3: AutoCompact circuit breaker (3 consecutive failures → skip)
  CW4: Post-compact cleanup centralization (Claw runPostCompactCleanup)
  CW5: Compaction warning SSE events (token pressure → frontend)
  CW6: Token warning state 4-level thresholds (Claw calculateTokenWarningState)
"""
import json
import logging
import os
import platform
import re
import subprocess
import sys
import threading
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional
from utils.errors import (
    classify_llm_error, get_retry_delay, should_retry,
    NanobotAPIError, TransientAPIError, RateLimitError, ContextOverflowError,
    AuthenticationError, ConnectionError_, TimeoutError_, ToolCallTypeError,
    DEFAULT_MAX_RETRIES,
)
from utils.feature_flags import ff

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Tool imports
# ═══════════════════════════════════════════════════════════════
from tools import (
    AGENTIC_TOOLS,
    TOOL_NAME_ALIASES,
    READONLY_TOOLS,
    ASYNC_TOOLS,
    execute_tool,
    execute_tool_async,
    _partition_tool_calls,
    build_tool_guidance,
)
from tools.base import _subprocess_run, _resolve_path, DANGEROUS_PATTERNS
from tools.sub_agent import get_agent_pool, execute_parallel, reset_agent_pool
from tools.file_edit import execute as _exec_file_edit
from tools.file_read import (
    _FULL_FILE_LIMIT,
    _is_full_file_request,
    _fix_full_file_reads,
)
from task_store import (
    ensure_root_task_for_message,
    get_current_task_summary,
    get_task_store,
    set_task_context,
)
from task_constraints import is_tool_allowed
from edit_transaction import list_pending_change_sets, list_discarded_change_sets

# Task status footer helper for debug visibility in chat transcripts.
def _build_task_status_footer_instruction(task_state: str, language: str = "auto") -> str:
    normalized_state = str(task_state or "").strip().lower()
    if normalized_state in {"completed", "failed", "cancelled"}:
        return ""

    if language.lower().startswith("zh"):
        state_labels = {
            "created": "待创建",
            "planned": "计划中",
            "in_progress": "执行中",
            "waiting_approval": "等待审批",
            "verifying": "验证中",
            "blocked": "阻塞",
        }
        state_label = state_labels.get(normalized_state, normalized_state or "未知")
        return (
            "[静默指令]\n"
            f"当前任务状态为「{state_label}」。如果任务尚未完成，请在每次回复末尾附上一行简洁脚注，"
            f"格式示例：`[任务状态: {state_label}...]`。任务完成后不要再输出该脚注。"
        )

    state_labels = {
        "created": "created",
        "planned": "planned",
        "in_progress": "in_progress",
        "waiting_approval": "waiting_approval",
        "verifying": "verifying",
        "blocked": "blocked",
    }
    state_label = state_labels.get(normalized_state, normalized_state or "unknown")
    return (
        "[Quiet instruction]\n"
        f"The current task state is `{state_label}`. If the task is not complete, end every reply with a single concise footnote line, "
        f"for example: `[Task status: {state_label}...]`. Do not add the footnote once the task is complete."
    )


# ═══════════════════════════════════════════════════════════════
# P104-fork: RepoExploreAgent — 仿 claw exploreAgent 的只读子代理
# ═══════════════════════════════════════════════════════════════
from repo_explore_agent import (
    RepoExploreAgent,
    run_repo_explore_subagent,
    create_repo_explore_system_prompt,
    ExplorationResult,
)

# ═══════════════════════════════════════════════════════════════
# Compact engine imports (P34: modularized)
# ═══════════════════════════════════════════════════════════════
from compact_engine import (
    _estimate_tokens,
    _estimate_messages_tokens,
    _get_context_ceiling,
    TokenBudgetTracker,
    CompactService,
    _resolve_model,
    _get_api_credentials,
    _NO_TOOLS_PREAMBLE,
    _DETAILED_ANALYSIS_INSTRUCTION,
    _COMPACT_SECTIONS,
    _COMPACT_EXAMPLE,
    _NO_TOOLS_TRAILER,
    _COMPACT_PROMPT,
    _PARTIAL_COMPACT_PROMPT,
    _PARTIAL_COMPACT_KEEP_RECENT,
    _format_compact_summary,
    _find_partial_pivot,
    _messages_to_text,
    _save_session_summary,
    _load_previous_summary,
    _auto_compact,
    _auto_compact_with_heartbeat,
)

# ═══════════════════════════════════════════════════════════════
# System prompt imports (P34: modularized)
# ═══════════════════════════════════════════════════════════════
from system_prompts import (
    _SYSTEM_PROMPT_IDENTITY,
    _SYSTEM_PROMPT_TASKS,
    _SYSTEM_PROMPT_STOP,
    _SYSTEM_PROMPT_ACTIONS,
    _SYSTEM_PROMPT_OUTPUT,
    _SYSTEM_PROMPT_QUALITY,
    _SYSTEM_PROMPT_TOOL_PRESERVATION,
    _SYSTEM_PROMPT_SELF_KNOWLEDGE,
    _build_platform_info,
    _get_static_system_prompt,
    build_dynamic_context,
    build_task_skill_toolbox_prompt,
    build_system_prompt,
    _load_nanobot_md,
    _detect_workspace_context,
    _detect_git_context,
    _detect_user_language,
    AGENTIC_SYSTEM_PROMPT,
)

# ═══════════════════════════════════════════════════════════════
# P28: Special token / LaTeX stripping
# ═══════════════════════════════════════════════════════════════
_SPECIAL_TOKEN_RE = re.compile(
    r'<\|tool_call\|?>|<tool_call\|>|<\|\"\|>|'
    r'<\|(?:start|end)_of_turn\|?>|<\|(?:im_start|im_end)\|?>|'
    r'<\|eot_id\|?>|<\|(?:begin|end)_of_text\|?>|<\|pad\|>|</?s>'
)
# B4: Expanded regex — catch nested braces, bare funcname{}, python-style calls
_TEXT_TOOL_CALL_RE = re.compile(
    r'call:\w+\{[^}]*(?:\{[^}]*\}[^}]*)?\}'  # call:name{...} with optional nested {}
    r'|(?:file_read|file_edit|file_write|file_list|grep_search|find_by_name|'
    r'shell_execute|python_execute|web_fetch|web_search|sub_agent|todo_manage'
    r')\{[^}]*\}'  # bare tool_name{...} without "call:" prefix
)

_LATEX_SYMBOL_MAP = {
    "rightarrow": "→", "Rightarrow": "→", "to": "→",
    "leftarrow": "←", "Leftarrow": "←",
    "leftrightarrow": "↔", "Leftrightarrow": "↔",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠", "approx": "≈",
    "times": "×", "div": "÷", "pm": "±",
    "infty": "∞", "alpha": "α", "beta": "β", "gamma": "γ",
    "delta": "δ", "epsilon": "ε", "lambda": "λ", "mu": "μ",
    "pi": "π", "sigma": "σ", "theta": "θ", "omega": "ω",
}

_LATEX_REPLACEMENTS = [
    (re.compile(r'\$\\text\{([^}]*)\}\$'), r'\1'),
    (re.compile(r'\$\\texttt\{([^}]*)\}\$'), r'`\1`'),
    (re.compile(r'\$\\mathtt\{([^}]*)\}\$'), r'`\1`'),
    (re.compile(r'\$\\([a-zA-Z]+)\$'), lambda m: _LATEX_SYMBOL_MAP.get(m.group(1), m.group(1))),
]


def _strip_latex(text: str) -> str:
    """P28: Remove LaTeX math notation from streaming text."""
    for pattern, replacement in _LATEX_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


class _LatexStreamBuffer:
    """Buffer for LaTeX-aware streaming text.
    
    Problem: LaTeX like $\\rightarrow$ may span multiple streaming tokens
    (e.g. "$\\" + "rightarrow" + "$"), so per-token _strip_latex() misses them.
    
    Solution: When a '$' is seen, buffer subsequent tokens until:
      - A closing '$' arrives → apply _strip_latex() on the whole span
      - Buffer exceeds 50 chars → flush as-is (not LaTeX)
      - finish() is called → flush remaining buffer
    """

    def __init__(self):
        self._buffer = ""
        self._in_latex = False

    def add(self, text: str) -> list:
        """Add text, return list of cleaned text chunks ready to emit."""
        self._buffer += text
        return self._flush()

    def _flush(self) -> list:
        results = []
        while self._buffer:
            if not self._in_latex:
                dollar_pos = self._buffer.find("$")
                if dollar_pos == -1:
                    results.append(_strip_latex(self._buffer))
                    self._buffer = ""
                else:
                    if dollar_pos > 0:
                        results.append(_strip_latex(self._buffer[:dollar_pos]))
                    self._buffer = self._buffer[dollar_pos:]
                    self._in_latex = True
            else:
                # In latex mode — look for closing $
                second_dollar = self._buffer.find("$", 1)
                if second_dollar != -1:
                    latex_span = self._buffer[:second_dollar + 1]
                    self._buffer = self._buffer[second_dollar + 1:]
                    self._in_latex = False
                    cleaned = _strip_latex(latex_span)
                    results.append(cleaned)
                elif len(self._buffer) > 50:
                    # Too long to be LaTeX — flush
                    results.append(self._buffer)
                    self._buffer = ""
                    self._in_latex = False
                else:
                    break  # wait for more input
        return results

    def finish(self) -> list:
        """Flush any remaining buffered content."""
        if self._buffer:
            cleaned = _strip_latex(self._buffer)
            self._buffer = ""
            self._in_latex = False
            return [cleaned]
        return []


# ═══════════════════════════════════════════════════════════════
# P102: Skill mode runtime enforcement helpers
# ═══════════════════════════════════════════════════════════════

# Verification command patterns — commands that count as "verification"
# for write-after-verify state machine and debug diagnostic unlock
_VERIFICATION_CMD_RE = re.compile(
    r'(?:^|\s|&&|\|\||;)(?:'
    r'(?<!install\s)pytest|'
    r'python[3]?\s+-m\s+(?:pytest|unittest|py_compile|mypy|flake8|ruff|pylint|pydocstyle)|'
    r'python[3]?\s+\S*test\S*|'
    r'npm\s+(?:test|run\s+(?:test|lint|check|typecheck|build))|'
    r'yarn\s+(?:test|lint|check|typecheck|build)|'
    r'pnpm\s+(?:test|lint|check|typecheck|build)|'
    r'make\s+(?:test|check|lint|build|verify)|'
    r'cargo\s+(?:test|check|clippy|build)|'
    r'go\s+(?:test|vet|build)|'
    r'mvn\s+(?:test|verify|compile)|'
    r'gradle\s+(?:test|check|build)|'
    r'tsc|mypy|flake8|ruff|pylint|eslint|prettier\s+--check|'
    r'black\s+--check|isort\s+--check|'
    r'git\s+diff|git\s+status|'
    r'python[3]?\s+-c\s|'
    r'(?<!install\s)unittest|nose2|tox|nox'
    r')',
    re.IGNORECASE,
)

def _is_verification_command(cmd: str) -> bool:
    """P102: Check if a shell command qualifies as a verification step.

    Returns True for: test runners, linters, type checkers, build commands,
    git diff/status, and compilation. Returns False for: cd, ls, cat, echo,
    file navigation, package installs, etc.
    """
    if not cmd or len(cmd.strip()) < 3:
        return False
    return bool(_VERIFICATION_CMD_RE.search(cmd))


# Completion criteria keywords for each skill mode
_COMPLETION_CRITERIA_CHECKS = {
    "verify": {
        # Must contain at least one status verdict
        "status_keywords": ["PASSED", "FAILED", "PARTIALLY_VERIFIED", "BLOCKED"],
        "hint": (
            "[INCOMPLETE OUTPUT] Verify mode requires an explicit status verdict. "
            "Your response must include one of: **PASSED**, **FAILED**, "
            "**PARTIALLY_VERIFIED**, or **BLOCKED**. Also list the exact "
            "commands you ran and their results."
        ),
    },
    "analyze": {
        # Must mention key structural elements
        "status_keywords": ["entry", "depend", "call", "structure", "import",
                            "module", "function", "class", "file"],
        "min_keyword_count": 2,  # at least 2 structural terms
        "hint": (
            "[INCOMPLETE OUTPUT] Analyze mode requires structural analysis. "
            "Your response should identify entry points, dependencies, "
            "call flow, and key files. Please complete the analysis."
        ),
    },
}

def _check_completion_criteria(mode: str, response_text: str) -> str:
    """P102: Check if the model's final response meets the skill's output requirements.

    Returns empty string if OK, or a hint message to inject if criteria not met.
    """
    if not mode or not response_text:
        return ""
    check = _COMPLETION_CRITERIA_CHECKS.get(mode)
    if not check:
        return ""

    text_upper = response_text.upper()
    keywords = check.get("status_keywords", [])
    min_count = check.get("min_keyword_count", 1)

    hits = sum(1 for kw in keywords if kw.upper() in text_upper)
    if hits < min_count:
        return check.get("hint", "")
    return ""


# ═══════════════════════════════════════════════════════════════
# P29 + U12a: Pre-tool narration detection (enhanced for small models)
# ═══════════════════════════════════════════════════════════════
_NARRATION_RE = re.compile(
    r"^(I will |Let me |I\'ll |I am going to |I\'m going to "
    r"|我将|我来|让我|接下来我|下面我"
    r"|为您|首先|先在|先查找|先读取|先搜索|先列出"
    r"|列出当前|查找并|读取并|搜索并|显示并|查看并"
    r"|读取|为了)",
    re.IGNORECASE | re.MULTILINE,
)

# U12a-dedup: detect exact sentence repetition ("读取X。读取X。")
_SENTENCE_DEDUP_RE = re.compile(
    r'(.{6,200}[。．.！!？?])\s*\1',
)


def _scrub_narration(text: str) -> tuple:
    """U12a: Strip leading narration sentences + exact sentence dedup.

    Returns (cleaned_text, was_scrubbed).
    """
    original = text.strip()
    # Step 1: Strip sentence-level repetition ("读取X。读取X。" → "读取X。")
    cleaned = _SENTENCE_DEDUP_RE.sub(r'\1', original)
    # Step 2: Strip leading narration prefixes
    cleaned = _NARRATION_RE.sub("", cleaned).strip()
    return cleaned, (cleaned != original)


_FINAL_META_PREFIX_RE = re.compile(
    r'^(?:根据(?:以上(?:工具结果)?|工具结果|已(?:读取|完整读取)的内容|刚才(?:读取)?的结果)[，,:：\s]*'
    r'|基于(?:以上(?:工具结果)?|工具结果|已(?:读取|完整读取)的内容)[，,:：\s]*'
    r'|(?:Based on|From) the tool results[:,]?\s*'
    r'|Here is (?:a )?summary[:,]?\s*)',
    re.IGNORECASE,
)
_FINAL_SUMMARY_PREFIX_RE = re.compile(
    r'^(?:总结|总之|简而言之|TL;DR|In short|Briefly)[:：，,\s-]*',
    re.IGNORECASE,
)
_FINAL_META_ONLY_RE = re.compile(
    r'^(?:我来|下面|现在)?(?:直接)?(?:总结|概括|回答)(?:一下|如下)?[。.!！:：\s]*$',
    re.IGNORECASE,
)
_FINAL_BRIEF_MARKER_RE = re.compile(r'^(?:TL;DR|摘要|总结|简而言之|In short|Briefly)[:：，,\s-]*', re.IGNORECASE)


def _has_high_text_overlap(text_a: str, text_b: str, threshold: float = 0.72) -> bool:
    norm_a = _normalize_repo_fact_answer(text_a)
    norm_b = _normalize_repo_fact_answer(text_b)
    if not norm_a or not norm_b:
        return False
    if norm_a in norm_b or norm_b in norm_a:
        return True
    tokens_a = set(re.findall(r'[a-zA-Z_][\w/]*|[\u4e00-\u9fff]{2,}', text_a or ""))
    tokens_b = set(re.findall(r'[a-zA-Z_][\w/]*|[\u4e00-\u9fff]{2,}', text_b or ""))
    if len(tokens_a) >= 4 and len(tokens_b) >= 4:
        overlap = len(tokens_a & tokens_b) / max(1, min(len(tokens_a), len(tokens_b)))
        if overlap >= threshold:
            return True
    return False


def _finalize_final_response(text: str) -> tuple[str, bool]:
    original = (text or "").strip()
    if not original:
        return original, False
    cleaned, _ = _scrub_narration(original)
    cleaned = _TEXT_TOOL_CALL_RE.sub("", cleaned).strip()
    cleaned = _FINAL_META_PREFIX_RE.sub("", cleaned).strip()
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', cleaned) if p.strip()]
    while len(paragraphs) > 1 and _FINAL_META_PREFIX_RE.match(paragraphs[0]) and len(paragraphs[1]) >= 20:
        paragraphs.pop(0)
    while len(paragraphs) > 1 and _FINAL_META_ONLY_RE.match(paragraphs[0]):
        paragraphs.pop(0)
    if len(paragraphs) >= 2 and _FINAL_SUMMARY_PREFIX_RE.match(paragraphs[-1]):
        if _has_high_text_overlap(paragraphs[-1], paragraphs[-2]):
            paragraphs.pop()
    cleaned = "\n\n".join(paragraphs).strip() if paragraphs else cleaned
    cleaned = _dedup_paragraphs(cleaned)
    return cleaned, (cleaned != original)


def _extract_brief_summary(text: str, max_sentences: int = 2, max_chars: int = 140) -> tuple[str, int]:
    source = (text or "").strip()
    if not source:
        return "", 0
    parts = []
    consumed = 0
    for match in re.finditer(r'[^。！？.!?\n]+[。！？.!?]?\s*', source):
        piece = match.group(0).strip()
        if not piece:
            continue
        projected = "".join(parts) + piece
        if parts and len(projected) > max_chars:
            break
        parts.append(piece)
        consumed = match.end()
        if len(parts) >= max_sentences:
            break
    if parts:
        return "".join(parts).strip(), consumed
    first_para = re.split(r'\n\s*\n', source, maxsplit=1)[0].strip()
    clipped = first_para[:max_chars].strip()
    return clipped, min(len(source), len(first_para))


def _build_final_answer_payload(text: str) -> dict:
    cleaned = (text or "").strip()
    if not cleaned:
        return {
            "content": "",
            "summary": "",
            "details": "",
            "brief_first": False,
        }
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', cleaned) if p.strip()]
    first_para = paragraphs[0] if paragraphs else cleaned
    has_brief_marker = bool(_FINAL_BRIEF_MARKER_RE.match(first_para))
    summary, consumed = _extract_brief_summary(cleaned)
    details = ""
    content = cleaned
    brief_first = False
    if has_brief_marker:
        summary = _FINAL_BRIEF_MARKER_RE.sub("", first_para).strip() or summary
        details = "\n\n".join(paragraphs[1:]).strip() if len(paragraphs) > 1 else ""
    elif summary and (len(cleaned) > 180 or (len(paragraphs) > 1 and len(first_para) > 80)):
        details = cleaned[consumed:].strip()
        if not details and len(paragraphs) > 1:
            details = "\n\n".join(paragraphs[1:]).strip()
        if details:
            content = f"TL;DR: {summary}\n\n{details}"
            brief_first = (content != cleaned)
    return {
        "content": content,
        "summary": summary,
        "details": details,
        "brief_first": brief_first,
    }

# U12c: Emoji categorization detection (small models expand simple lists into reports)
_EMOJI_HEADER_RE = re.compile(
    r'(?:^|\n)\s*(?:📁|📂|📦|🔧|⚙️|📝|🗂️|💻|🧪|📊|🔬|🚀|📄|🎯|✨|🔥|💡|🏗️|📋|🛠️|🔒|🌐)\s*\**\S',
)
# U12c-bold: Detect numbered/bold categorization headers (no emoji)
# Real patterns from llama.cpp: "### 1. 核心代码目录", "### 一、整体设计",
# "*   **`agents/`**: 描述", "*   **`core/`**: 描述"
_BOLD_CATEGORY_RE = re.compile(
    r'(?:^|\n)\s*#{2,4}\s+(?:\d+[.、]|[一二三四五六七八九十]+[、.])\s*\S'
    r'|(?:^|\n)\s*\*\s+\*\*[`*]',
)

# ═══════════════════════════════════════════════════════════════
# U11c: @explore lock — detect user's explicit @explore prefix
# ═══════════════════════════════════════════════════════════════
_re_explore_lock = re.compile(r'^@explore\b', re.IGNORECASE)

# ═══════════════════════════════════════════════════════════════
# P27: file_read limit auto-correction
# ═══════════════════════════════════════════════════════════════
_LINE_COUNT_RE = re.compile(
    r'(?:读取?|read)\s+.*?(\d+)\s*-\s*(\d+)\s*(?:行|lines?)'
    r'|(?:前|first|top|头)\s*(\d+)\s*(?:行|lines?|rows?)'
    r'|(?:读取?|read)\s+.*?(\d+)\s*(?:行|lines?)'
    r'|(\d+)\s*(?:行|lines?)\s*(?:内容|content)?$',
    re.IGNORECASE | re.MULTILINE,
)

_TRANSACTIONAL_EDIT_SUCCESS_RE = re.compile(
    r'(?:\b(?:modified|updated|edited|fixed|renamed|changed|completed|done|applied|saved)\b'
    r'|已(?:修改|更新|修复|完成|应用|保存)'
    r'|已经(?:修改|更新|修复|完成|应用)'
    r'|我已经将[^\n]{0,120}(?:改为|修改为|更新为|重命名为)'
    r'|已将[^\n]{0,120}(?:方法|函数|文件|内容)?[^\n]{0,120}(?:改为|修改为|更新为|重命名为)'
    r'|修改已(?:完成|应用)'
    r'|修复已(?:完成|应用)'
    r'|已将[^\n]{0,80}(?:改为|修改为|更新为))',
    re.IGNORECASE,
)

_TRANSACTIONAL_EDIT_PENDING_RE = re.compile(
    r'(?:pending change set|awaiting approval|needs approval|pending review'
    r'|待审批|等待审批|待处理变更集|待处理修改|请确认是否应用|等待确认应用'
    r'|尚未(?:应用|落盘|修改|生效)|未(?:应用|落盘|生效)'
    r'|not yet (?:applied|on disk|written)|not applied yet)',
    re.IGNORECASE,
)


def _has_premature_transactional_edit_claim(text: str) -> bool:
    if not text:
        return False
    if not _TRANSACTIONAL_EDIT_SUCCESS_RE.search(text):
        return False
    return True


def _build_transactional_pending_nudge(change_set_ids: List[str]) -> str:
    visible = ", ".join(change_set_ids[:3])
    suffix = "" if len(change_set_ids) <= 3 else f" and {len(change_set_ids) - 3} more"
    return (
        "[TRANSACTIONAL EDIT STILL PENDING] One or more file edits are NOT on disk yet. "
        f"Pending change sets: {visible}{suffix}. "
        "Do NOT claim the file is already modified, fixed, renamed, or complete. "
        "The user must approve or reject the change via the UI approval card. "
        "STOP generating now. Do NOT ask '需要我运行该测试脚本吗？' or repeat yourself. "
        "Simply state ONCE that changes are pending user approval, then END your response. "
        "Example: '修改已提交，请在界面中审批。' — nothing more."
    )


def _fix_file_read_limits(tool_calls: list, user_message: str) -> list:
    """Auto-inject limit into file_read calls when the user specified line counts.
    
    For multi-request messages (e.g. "read X first 50 lines\nread Y lines 200-400"),
    we parse EACH LINE separately to build a per-file line count map, then match
    each file_read call by its path argument.
    """
    if not tool_calls or not user_message:
        return tool_calls

    # Parse line requests from user message (per-line to support multi-file)
    line_requests = []  # list of (file_hint, offset, limit)
    for line in user_message.split("\n"):
        m = _LINE_COUNT_RE.search(line)
        if not m:
            continue
        # Extract a file-name hint from the same line
        file_hint_m = re.search(r'[\w./\\-]+\.[\w]+', line)
        file_hint = file_hint_m.group(0) if file_hint_m else ""

        if m.group(1) and m.group(2):
            # Range: "lines 100-200"
            start, end = int(m.group(1)), int(m.group(2))
            line_requests.append((file_hint, start, end - start))
        elif m.group(3):
            count = int(m.group(3))
            line_requests.append((file_hint, None, count))
        elif m.group(4):
            count = int(m.group(4))
            line_requests.append((file_hint, None, count))
        elif m.group(5):
            count = int(m.group(5))
            line_requests.append((file_hint, None, count))

    if not line_requests:
        return tool_calls

    # Match file_read calls to line requests
    used_requests = set()
    for tc in tool_calls:
        func = tc.get("function", {})
        if func.get("name") not in ("file_read", "read_file"):
            continue
        args = tc.get("_parsed_args")
        if not args:
            try:
                args = json.loads(func.get("arguments", "{}"))
                tc["_parsed_args"] = args
            except (json.JSONDecodeError, ValueError):
                args = {}
        if not isinstance(args, dict):
            continue
        # Skip if the model set a non-default limit (default is ~300)
        existing_limit = args.get("limit")
        if existing_limit and existing_limit != 300:
            continue  # user/model explicitly set a non-default limit

        tc_path = args.get("path", "")

        # Find best matching line request
        best_idx = None
        fallback_idx = None
        for i, (file_hint, tc_offset, count) in enumerate(line_requests):
            if i in used_requests:
                continue
            if file_hint and file_hint in tc_path:
                best_idx = i
                break
            if fallback_idx is None:
                fallback_idx = i

        req = best_idx if best_idx is not None else fallback_idx
        if req is not None and req not in used_requests:
            used_requests.add(req)
            _, offset, count = line_requests[req]
            args["limit"] = count
            if offset is not None:
                args["offset"] = offset
            func["arguments"] = json.dumps(args)
            tc["_parsed_args"] = args
            logger.info(
                f"[P27] Auto-injected limit={count}"
                + (f", offset={offset}" if offset else "")
                + f" into file_read({tc_path})"
            )

    return tool_calls


_CODE_SUMMARY_REQUEST_RE = re.compile(
    r'(?:总结|概括|解释|分析|后半段|逻辑|读取.*全文|read.*full|summari[sz]e|explain|analy[sz]e|latter half|second half)',
    re.IGNORECASE,
)


def _detect_code_summary_request(user_message: str) -> Optional[str]:
    if not user_message:
        return None
    if not _CODE_SUMMARY_REQUEST_RE.search(user_message):
        return None
    if not re.search(r'[\w./\\-]+\.\w{1,10}|代码|函数|class|method|file', user_message, re.IGNORECASE):
        return None
    return (
        "[CODE SUMMARY] When summarizing code, anchor every major claim to concrete evidence. "
        "Name the function, branch, or check you are describing. Cite code locations using "
        "file_path:line or file_path:start-end. Do NOT give only abstract labels like 'security engine' "
        "or 'lifecycle management' without naming the actual code. Separate what is directly seen in code "
        "from what is only inferred."
    )


def _recover_file_read_paths(tool_calls: list, workspace: Path) -> list:
    if not tool_calls:
        return tool_calls

    def _score_candidate(candidate: Path, raw_path: str) -> tuple:
        rel = str(candidate.relative_to(workspace))
        basename = candidate.name
        raw_base = Path(raw_path).name
        return (
            0 if basename == raw_base else 1,
            0 if "__pycache__" not in rel else 1,
            0 if not candidate.suffix.endswith("c") else 1,
            0 if not any(part in rel for part in ("backup", "backups", ".git", ".venv", "node_modules")) else 1,
            len(candidate.parts),
            rel,
        )

    for tc in tool_calls:
        func = tc.get("function", {})
        raw_name = func.get("name", "")
        if raw_name not in ("file_read", "read_file"):
            continue
        args = tc.get("_parsed_args")
        if not args:
            try:
                args = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, ValueError):
                continue
        if not isinstance(args, dict):
            continue
        raw_path = (args.get("path") or "").strip()
        if not raw_path:
            continue
        if Path(raw_path).is_absolute() or raw_path.startswith("~"):
            continue
        if "/" in raw_path or "\\" in raw_path:
            probe = workspace / raw_path
            if probe.exists():
                continue
        else:
            probe = workspace / raw_path
            if probe.exists():
                continue

        pattern = Path(raw_path).name or raw_path
        candidates = []
        try:
            for candidate in workspace.rglob(pattern):
                if not candidate.is_file():
                    continue
                rel = str(candidate.relative_to(workspace))
                if any(part in rel for part in ("__pycache__", ".git/", ".venv/", "node_modules/")):
                    continue
                candidates.append(candidate)
        except Exception:
            continue

        if not candidates:
            continue

        candidates.sort(key=lambda p: _score_candidate(p, raw_path))
        best = candidates[0]
        rel_best = str(best.relative_to(workspace))
        args["path"] = rel_best
        tc["_parsed_args"] = args
        func["arguments"] = json.dumps(args)
        logger.info(f"[B17] Recovered file_read({raw_path}) → {rel_best}")

    return tool_calls


# ═══════════════════════════════════════════════════════════════
# B16: Full-file auto-sharding
# ═══════════════════════════════════════════════════════════════
_B16_SHARD_SIZE = 300   # lines per shard — matches file_read's default display cap


def _shard_full_file_reads(tool_calls: list, workspace: Path) -> list:
    """B16: Replace single full-file file_read calls with multiple sharded calls.

    When a file_read has limit=_FULL_FILE_LIMIT (B15 sentinel, 99999) and the
    target file has >_B16_SHARD_SIZE lines, replace it with the exact number of
    parallel sharded calls needed to cover the entire file. Each shard reads
    _B16_SHARD_SIZE lines except the last, which extends to cover the remaining
    lines so no content is missed.

    The P0 concurrency system automatically runs all shards in parallel
    (file_read is read-only), so the model sees the ENTIRE file in one turn
    instead of just the first 300 lines.

    Returns a new tool_calls list (original items are preserved for non-sharded calls).
    """
    result = []
    sharded_count = 0

    for idx, tc in enumerate(tool_calls):
        func = tc.get("function", {})
        name = func.get("name", "")
        if name not in ("file_read", "read_file"):
            result.append(tc)
            continue

        # Parse args
        args = tc.get("_parsed_args")
        if not args:
            try:
                args = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, ValueError):
                result.append(tc)
                continue

        # Only shard full-file requests (limit == _FULL_FILE_LIMIT sentinel)
        limit = args.get("limit")
        if limit != _FULL_FILE_LIMIT:
            result.append(tc)
            continue

        # Resolve path and count lines
        raw_path = args.get("path", "")
        try:
            file_path = _resolve_path(raw_path, workspace)
        except ValueError:
            result.append(tc)
            continue
        if not file_path.exists() or not file_path.is_file():
            result.append(tc)
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            total_lines = len(content.split("\n"))
        except Exception:
            result.append(tc)
            continue

        # Small files don't need sharding — remove sentinel, let default handle it
        if total_lines <= _B16_SHARD_SIZE:
            args.pop("limit", None)
            args.pop("offset", None)
            func["arguments"] = json.dumps(args)
            tc["_parsed_args"] = args
            result.append(tc)
            continue

        # B16.1: Calculate exact shard count needed for full coverage
        num_shards = -(-total_lines // _B16_SHARD_SIZE)

        # Generate sharded calls
        base_id = tc.get("id", f"call_{idx}")
        offset = 1
        for shard_num in range(num_shards):
            is_last = (shard_num == num_shards - 1)
            shard_limit = (total_lines - offset + 1) if is_last else _B16_SHARD_SIZE

            shard_args = {"path": raw_path, "offset": offset, "limit": shard_limit}
            shard_tc = {
                "id": f"{base_id}_b16s{shard_num}",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": json.dumps(shard_args),
                },
                "_parsed_args": shard_args,
            }
            result.append(shard_tc)
            offset += _B16_SHARD_SIZE

        sharded_count += 1
        logger.info(
            f"[B16] Sharded file_read({raw_path}) → {num_shards} shards "
            f"({total_lines} lines, {_B16_SHARD_SIZE} lines/shard)"
        )

    if sharded_count:
        logger.info(f"[B16] Total: {sharded_count} file(s) sharded, {len(result)} tool calls in result")

    return result


def _apply_file_read_corrections(
    tool_calls: list, task_text: str, workspace: Path
) -> list:
    """P27/B15/B16: Apply all file_read corrections based on the user's task text.

    Combines line-count limit injection (P27), full-file override (B15),
    and full-file sharding (B16) into a single explicit-parameter function.
    Returns the (possibly replaced) tool_calls list.
    """
    if not any(
        tc.get("function", {}).get("name") in ("file_read", "read_file")
        for tc in tool_calls
    ):
        return tool_calls

    _has_line_request = _LINE_COUNT_RE.search(task_text)
    _has_full_file = _is_full_file_request(task_text)

    if _has_line_request:
        _fix_file_read_limits(tool_calls, task_text)
        logger.info("[P27] Auto-corrected file_read limits")
    if _has_full_file:
        _fix_full_file_reads(tool_calls, task_text)
        logger.info("[B15] Full-file override applied")
        tool_calls = _shard_full_file_reads(tool_calls, workspace)

    return tool_calls


# ═══════════════════════════════════════════════════════════════
# P38: Mandatory tool detection
# ═══════════════════════════════════════════════════════════════
_ALL_TOOL_NAMES_FOR_DETECT: set = set()
try:
    _at38 = AGENTIC_TOOLS
    _ta38 = TOOL_NAME_ALIASES
    for _t in _at38:
        _ALL_TOOL_NAMES_FOR_DETECT.add(_t["function"]["name"])
    _ALL_TOOL_NAMES_FOR_DETECT.update(_ta38.keys())
except ImportError:
    pass

_MANDATORY_TOOL_RE = re.compile(
    r'(?:使用|调用|用|call|use|invoke|run|execute|test)\s+'
    r'(?:工具\s*)?(?:the\s+)?(\w+)(?:\s+(?:工具|tool))?',
    re.IGNORECASE,
)


def _detect_mandatory_tool(user_message: str) -> Optional[str]:
    """P38: Detect if the user explicitly requested a specific tool by name.

    Returns a system message hint if a mandatory tool is detected, None otherwise.
    Matches patterns like:
      - "使用 sub_agent 工具执行..."
      - "use web_fetch to..."
      - "call grep_search for..."
      - "用 web_search 搜索..."
    """
    matches = _MANDATORY_TOOL_RE.findall(user_message)
    for raw_name in matches:
        tool_name = raw_name.lower().strip()
        if tool_name in _ALL_TOOL_NAMES_FOR_DETECT:
            canonical = TOOL_NAME_ALIASES.get(tool_name, tool_name)
            return (
                f"[MANDATORY TOOL] The user explicitly requested tool '{canonical}'. "
                f"You MUST call {canonical} before answering unless it is impossible."
            )
    return None


# ═══════════════════════════════════════════════════════════════
# Pre-flight detection bundle (decoupling POC — audit-mandated)
# ═══════════════════════════════════════════════════════════════

def _run_preflight_detections(task_text: str) -> dict:
    """Bundle pre-flight hint detections into a single entry point.

    This is the decoupling proof-of-concept required by the CTO audit.
    Instead of 5 scattered call sites referencing _current_task_text,
    this function receives the task text as an explicit parameter and
    returns all detection results as a dict.

    Returns:
        dict with keys: table_hint, code_summary_hint, repo_fact_hint,
        repo_explore_fork, mandatory_hint
    """
    table_hint = _detect_table_fill_request(task_text)
    if table_hint:
        logger.info("[AgenticLoop] Injected table-fill hint (P7)")

    code_summary_hint = _detect_code_summary_request(task_text)
    if code_summary_hint:
        logger.info("[P102] Injected code-summary anchor hint")

    repo_fact_hint = _detect_repo_fact_request(task_text)
    if repo_fact_hint:
        logger.info("[P104] Injected repo-fact grounding hint")

    repo_explore_fork = False
    if repo_fact_hint and _ARCHITECTURE_QUERY_RE.search(task_text):
        repo_explore_fork = True
        logger.info("[P104-fork] Architecture/directory question detected — forking to RepoExploreAgent")

    mandatory_hint = _detect_mandatory_tool(task_text)
    if mandatory_hint:
        logger.info("[P38] Injected mandatory tool hint")

    return {
        "table_hint": table_hint,
        "code_summary_hint": code_summary_hint,
        "repo_fact_hint": repo_fact_hint,
        "repo_explore_fork": repo_explore_fork,
        "mandatory_hint": mandatory_hint,
    }


def _get_memory_trigger(task_text: str):
    """P92: Detect memory-save triggers in user message.

    Decoupled from _current_task_text — receives task text explicitly.

    Returns:
        str hint to inject as system message, or None.
    """
    try:
        from memory.memory_triggers import detect_memory_trigger
        hint = detect_memory_trigger(task_text)
        if hint:
            logger.info("[P92] Injected memory save hint")
        return hint
    except Exception as e:
        logger.debug(f"[P92] Memory trigger detection failed: {e}")
        return None


def _match_skill_intent(task_text: str):
    """P98c: Match user message to a registered skill.

    Decoupled from _current_task_text — receives task text explicitly.
    Skips matching when message starts with ``/`` (explicit invocation).

    Returns:
        (SkillDefinition, float) tuple, or None.
    """
    if task_text.lstrip().startswith("/"):
        return None
    try:
        from skills import match_skill_by_intent
        match = match_skill_by_intent(task_text)
        if match:
            logger.info(f"[P98c] Skill hint: /{match[0].name} ({match[1]:.1f})")
        return match
    except Exception as e:
        logger.debug(f"[P98c] Skill matching failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# P104: Repository-fact question detection
# ═══════════════════════════════════════════════════════════════
_REPO_FACT_REQUEST_RE = re.compile(
    r'(AGENTS\.md|PROJECT_STRUCTURE\.md|ARCHITECTURE\.md|'
    r'根目录|目录结构|文件夹|有哪些模块|主要模块|项目结构|项目架构|架构是怎么设计|'
    r'what\s+a\s+file\s+says|directory\s+structure|module\s+list|architecture)',
    re.IGNORECASE,
)


def _detect_repo_fact_request(user_message: str) -> Optional[str]:
    if not user_message:
        return None
    if not _REPO_FACT_REQUEST_RE.search(user_message):
        return None
    return (
        "[REPO FACTS ONLY] This is a repository-fact question. Use read-only tools first "
        "(file_read, file_list, grep_search as needed), then answer ONLY from what the tool "
        "results support. Do NOT write pre-tool exposition. Do NOT invent architecture clusters, "
        "directory purposes, or system design claims from names alone. For files like AGENTS.md, "
        "summarize the document directly. For directory/module questions, separate observed facts "
        "from limited inference and keep the answer plain."
    )


_REPO_FACT_FULL_READ_RE = re.compile(
    r'(AGENTS\.md|PROJECT_STRUCTURE\.md|ARCHITECTURE\.md|README(?:\.md)?|'
    r'架构|目录结构|项目结构|项目架构|模块|职责)',
    re.IGNORECASE,
)
_REPO_FACT_SUMMARY_RE = re.compile(
    r'(架构|architecture|目录职责|directory\s+responsibilit|目录结构|项目结构|模块|module)',
    re.IGNORECASE,
)
# P104-fork: 架构/结构类问题检测（触发 sub-agent fork）
_ARCHITECTURE_QUERY_RE = re.compile(
    r'(详细说说|详细说明|详细描述|explain\s+in\s+detail|'
    r'每个目录|directory\s+responsibilities|'
    r'架构是怎么设计|how\s+is.*architecture\s+designed|'
    r'模块划分|module\s+organization)',
    re.IGNORECASE,
)
_PARTIAL_FILE_READ_RE = re.compile(
    r'showing\s+lines\s+(\d+)-(\d+)\s+of\s+(\d+)',
    re.IGNORECASE,
)
_REPO_FACT_READ_ONLY_TOOLS = {
    "file_read", "file_list", "grep_search", "find_by_name"
}


def _is_repo_fact_question(user_message: str) -> bool:
    return bool(_detect_repo_fact_request(user_message or ""))


def _upgrade_repo_fact_file_reads(tool_calls: list) -> list:
    """Force key repo-fact document reads to full-file mode.

    This delegates large-file coverage to B16 sharding instead of relying on the
    model to remember offset/limit follow-ups after seeing only the first 300 lines.
    """
    if not tool_calls:
        return tool_calls
    for tc in tool_calls:
        func = tc.get("function", {})
        raw_name = func.get("name", "")
        tool_name = TOOL_NAME_ALIASES.get(raw_name, raw_name)
        if tool_name != "file_read":
            continue
        args = tc.get("_parsed_args")
        if not args:
            try:
                args = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, ValueError):
                continue
        path = str(args.get("path", ""))
        if not path:
            continue
        if not _REPO_FACT_FULL_READ_RE.search(path):
            continue
        if args.get("limit") == _FULL_FILE_LIMIT:
            continue
        args.pop("offset", None)
        args["limit"] = _FULL_FILE_LIMIT
        tc["_parsed_args"] = args
        func["arguments"] = json.dumps(args)
        logger.info(f"[P104-route] Upgraded repo-fact file_read({path}) to full-file mode")
    return tool_calls


def _get_repo_fact_partial_reads(messages: list, turn: int, lookback_turns: int = 1) -> list:
    partials = []
    min_turn = max(1, turn - lookback_turns)
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        msg_turn = msg.get("_turn")
        if msg_turn is None or msg_turn < min_turn or msg_turn > turn:
            continue
        if msg.get("_tool_name") != "file_read":
            continue
        content = msg.get("content", "") or ""
        m = _PARTIAL_FILE_READ_RE.search(content)
        if not m:
            continue
        partials.append({
            "tool_call_id": msg.get("tool_call_id", ""),
            "content": content,
            "from": int(m.group(1)),
            "to": int(m.group(2)),
            "total": int(m.group(3)),
        })
    return partials


def _build_repo_fact_completion_gate(messages: list, turn: int, user_message: str) -> Optional[str]:
    if not _is_repo_fact_question(user_message):
        return None
    if not _REPO_FACT_SUMMARY_RE.search(user_message or ""):
        return None
    partials = _get_repo_fact_partial_reads(messages, turn, lookback_turns=1)
    if not partials:
        return None
    doc_names = []
    for item in partials:
        content = item["content"]
        first_line = (content.splitlines() or [""])[0]
        if ":" in first_line:
            candidate = first_line.split(":", 1)[-1].strip()
            if candidate:
                doc_names.append(candidate)
    doc_label = ", ".join(doc_names[:3]) if doc_names else "the relevant document(s)"
    return (
        "[REPO-FACT GATE] You only read PART of "
        f"{doc_label}. Do NOT give a complete architecture/module/directory-duty summary yet. "
        "Call file_read again to read the remaining lines or use the full-file read path. "
        "If you must answer now, explicitly label it as partial and limited to the lines already read."
    )


def _normalize_repo_fact_answer(text: str) -> str:
    text = re.sub(r'[`*_#>\-\s]+', '', text or '')
    text = re.sub(r'[^\w\u4e00-\u9fff/]+', '', text)
    return text.lower()


def _is_duplicate_repo_fact_answer(messages: list, assistant_text: str) -> bool:
    current = _normalize_repo_fact_answer(assistant_text)
    if len(current) < 80:
        return False
    previous = []
    for msg in messages[:-1]:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if not content:
            continue
        norm = _normalize_repo_fact_answer(content)
        if len(norm) >= 80:
            previous.append((norm, content))
    if not previous:
        return False
    current_tokens = set(re.findall(r'[a-zA-Z_][\w/]*|[\u4e00-\u9fff]{2,}', current))
    current_terms = {
        term.lower()
        for term in re.findall(r'`?([a-zA-Z_][\w/]*\/?)`?', assistant_text or "")
        if len(term) >= 3
    }
    for prev, prev_content in previous[-3:]:
        if current == prev:
            return True
        prev_tokens = set(re.findall(r'[a-zA-Z_][\w/]*|[\u4e00-\u9fff]{2,}', prev))
        if len(current_tokens) >= 8 and len(prev_tokens) >= 8:
            overlap = len(current_tokens & prev_tokens) / max(1, min(len(current_tokens), len(prev_tokens)))
            if overlap >= 0.55:
                return True
        prev_terms = {
            term.lower()
            for term in re.findall(r'`?([a-zA-Z_][\w/]*\/?)`?', prev_content or "")
            if len(term) >= 3
        }
        if len(current_terms) >= 5 and len(prev_terms) >= 5:
            term_overlap = len(current_terms & prev_terms) / max(1, min(len(current_terms), len(prev_terms)))
            if term_overlap >= 0.75:
                return True
    return False


def _repo_fact_answer_terms(text: str) -> set:
    return {
        term.lower()
        for term in re.findall(r'`?([a-zA-Z_][\w/]*\/?)`?', text or "")
        if len(term) >= 3
    }


def _find_repo_fact_one_shot_final(messages: list, user_message: str) -> Optional[str]:
    if not _is_repo_fact_question(user_message):
        return None
    if not _REPO_FACT_SUMMARY_RE.search(user_message or ""):
        return None
    candidates = []
    for msg in messages:
        if msg.get("role") == "assistant":
            content = msg.get("content", "") or ""
            if len(_normalize_repo_fact_answer(content)) >= 80:
                candidates.append(content)
    for content in reversed(candidates[-6:]):
        if not _REPO_FACT_SUMMARY_RE.search(content):
            continue
        if not re.search(r'(PROJECT_STRUCTURE\.md|ARCHITECTURE\.md|README|AGENTS\.md|项目结构|项目架构|目录结构)', content, re.I):
            continue
        if len(_repo_fact_answer_terms(content)) >= 5 or re.search(r'(主要模块|模块包括|模块组成|目录职责)', content):
            return content
    return None


def _build_dev_annotation(tool_name: str, result: dict, tool_content: str) -> str:
    if not result.get("success", True):
        return f"❌ {tool_name} failed: {(result.get('error') or 'unknown')[:80]}"
    output = tool_content or result.get("output", "") or ""
    if tool_name == "file_read":
        _file_match = re.search(r'\[File:\s*(\S+)', output)
        _line_match = re.search(r'\|\s*(\d+)\s*lines', output)
        fname = Path(_file_match.group(1)).name if _file_match else "file"
        lines = _line_match.group(1) if _line_match else "?"
        if "File unchanged since last read" in output:
            return f"📝 {fname} unchanged ({lines} lines) — reuse previous content"
        defs = _extract_file_defs(output)
        if defs:
            return f"📝 Read {fname} ({lines} lines) — key defs: {defs}"
        return f"📝 Read {fname} ({lines} lines)"
    if tool_name == "file_list":
        _count = re.search(r'(\d+)\s*entries', output)
        return f"📝 Listed {(_count.group(1) if _count else '?')} entries"
    if tool_name == "grep_search":
        _count = re.search(r'(\d+)\s*match', output)
        return f"📝 Found {(_count.group(1) if _count else '0')} matches"
    preview = output[:80].replace("\n", " ").strip()
    return f"📝 {tool_name}: {preview}{'…' if len(output) > 80 else ''}"


def _dedup_paragraphs(text: str) -> str:
    if not text or len(text) < 50:
        return text
    paragraphs = text.split("\n\n")
    seen_normalized = []
    kept = []
    for para in paragraphs:
        norm = re.sub(r'[`*_#>\-]', '', para).strip().lower()
        norm = re.sub(r'\s+', ' ', norm)
        if not norm:
            kept.append(para)
            continue
        is_dup = False
        for prev_norm in seen_normalized:
            if norm == prev_norm:
                is_dup = True
                break
            if len(norm) > 20 and len(prev_norm) > 20:
                words_curr = set(norm.split())
                words_prev = set(prev_norm.split())
                if len(words_curr) >= 3 and len(words_prev) >= 3:
                    overlap = len(words_curr & words_prev) / max(1, min(len(words_curr), len(words_prev)))
                    if overlap > 0.80:
                        is_dup = True
                        break
                if not is_dup and len(norm) > 30 and len(prev_norm) > 30:
                    n = 3
                    ngrams_curr = set(norm[i:i+n] for i in range(len(norm) - n + 1))
                    ngrams_prev = set(prev_norm[i:i+n] for i in range(len(prev_norm) - n + 1))
                    if len(ngrams_curr) >= 5 and len(ngrams_prev) >= 5:
                        overlap = len(ngrams_curr & ngrams_prev) / max(1, min(len(ngrams_curr), len(ngrams_prev)))
                        if overlap > 0.80:
                            is_dup = True
                            break
        if is_dup:
            continue
        seen_normalized.append(norm)
        kept.append(para)
    cleaned = "\n\n".join(kept)
    last_line = cleaned.rstrip().split("\n")[-1].rstrip() if cleaned.rstrip() else ""
    if last_line and not re.search(r'[。！？.!?：:)]\s*$', last_line):
        if not re.match(r'^[-*]\s|^\d+\.\s|^#{1,3}\s|^\|', last_line):
            sentences = re.split(r'([。！？.!?])', last_line)
            if len(sentences) >= 3:
                rebuilt = ""
                for i in range(0, len(sentences) - 1, 2):
                    rebuilt += sentences[i] + (sentences[i + 1] if i + 1 < len(sentences) else "")
                lines = cleaned.split("\n")
                lines[-1] = rebuilt.rstrip()
                cleaned = "\n".join(lines)
    return cleaned


# ═══════════════════════════════════════════════════════════════
# RAG + Knowledge Graph — local SQLite-backed retrieval
# ═══════════════════════════════════════════════════════════════
_RAG_DB_PATH = Path.home() / ".nanobot" / "rag_vectors.db"
_KG_DB_PATH = Path.home() / ".nanobot" / "knowledge_graph.db"


import time as _time_mod_db
import threading as _threading_db

# R10: Time-driven checkpoint/vacuum state — shared across all _open_db calls.
# Prevents per-query overhead while ensuring maintenance runs periodically.
_DB_MAINT_LOCK = _threading_db.Lock()
_DB_MAINT_INTERVAL = 30.0  # seconds — checkpoint/vacuum at most once per interval
_db_maint_last: dict[str, float] = {}  # path_str → last success monotonic time
_db_maint_fail_count: dict[str, int] = {}  # path_str → consecutive failure count
_db_maint_last_success: dict[str, float] = {}  # path_str → last success wall-clock time


def _open_db(path: Path):
    """Open SQLite with WAL journal, busy timeout, and best-effort auto-vacuum.

    Returns a context manager — use with ``with _open_db(path) as conn:``.
    Connection is automatically closed on exit (including exceptions).

    PRAGMA order matters: auto_vacuum must precede journal_mode=WAL because
    WAL initialisation on a fresh DB locks the auto_vacuum setting to 0.

    R10: WAL checkpoint + incremental vacuum are time-driven (max once per 30s)
    rather than per-connection. Failures are logged and counted for observability.
    """
    import sqlite3
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        conn = sqlite3.connect(str(path), timeout=5.0)
        try:
            # auto_vacuum MUST be set before journal_mode=WAL on new DBs
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            # Runtime verification — confirm PRAGMAs actually took effect
            _jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
            _bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            if _jm != "wal":
                logger.warning("[SQLite] journal_mode=%s (expected wal) on %s", _jm, path)
            if _bt != 5000:
                logger.warning("[SQLite] busy_timeout=%d (expected 5000) on %s", _bt, path)
            yield conn
        finally:
            # R10: Time-driven WAL checkpoint + incremental vacuum.
            # Runs at most once per _DB_MAINT_INTERVAL to avoid per-query overhead.
            _path_key = str(path)
            _now = _time_mod_db.monotonic()
            _should_maint = False
            with _DB_MAINT_LOCK:
                _last = _db_maint_last.get(_path_key, 0.0)
                if _now - _last >= _DB_MAINT_INTERVAL:
                    _db_maint_last[_path_key] = _now
                    _should_maint = True
            if _should_maint:
                try:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    conn.execute("PRAGMA incremental_vacuum(64)")
                    with _DB_MAINT_LOCK:
                        _db_maint_fail_count[_path_key] = 0
                        _db_maint_last_success[_path_key] = _time_mod_db.time()
                except Exception as _maint_exc:
                    with _DB_MAINT_LOCK:
                        _fc = _db_maint_fail_count.get(_path_key, 0) + 1
                        _db_maint_fail_count[_path_key] = _fc
                    if _fc <= 3 or _fc % 10 == 0:
                        logger.warning(
                            "[SQLite] WAL maintenance failed (count=%d) on %s: %s",
                            _fc, _path_key, _maint_exc,
                        )
            conn.close()

    return _ctx()


def _query_knowledge_graph(query: str, limit: int = 5) -> list:
    """Query the knowledge graph for entities and relations relevant to the query.
    Returns a list of {entity, type, relations} dicts."""
    try:
        if not _KG_DB_PATH.is_file():
            return []
        with _open_db(_KG_DB_PATH) as conn:
            w = query.lower().split()
            keywords = [kw for kw in w if len(kw) > 2]
            if not keywords:
                return []
            conditions = " OR ".join(["LOWER(name) LIKE ?" for kw in keywords])
            params = [f"%{kw}%" for kw in keywords]
            entities = conn.execute(
                f"SELECT entity_id, name, entity_type, attributes, importance "
                f"FROM entities WHERE {conditions} "
                f"ORDER BY importance DESC, access_count DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            results = []
            for eid, name, etype, attrs, importance in entities:
                relations = conn.execute(
                    "SELECT r.relation_type, e.name as target_name "
                    "FROM relations r JOIN entities e ON r.target_id = e.entity_id "
                    "WHERE r.source_id = ? LIMIT 10",
                    (eid,),
                ).fetchall()
                results.append({
                    "entity": name,
                    "type": etype,
                    "relations": [{"relation": rt, "target": tn} for rt, tn in relations],
                })
            return results
    except Exception as e:
        logger.warning(f"[KG] Query failed: {e}")
        return []


def _query_rag_vectors(query: str, limit: int = 5) -> list:
    """Query the RAG vector store for relevant text chunks.
    Uses keyword matching as fallback when no embedding model is available."""
    try:
        if not _RAG_DB_PATH.is_file():
            return []
        with _open_db(_RAG_DB_PATH) as conn:
            count = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
            if count == 0:
                return []
            w = query.lower().split()
            keywords = [kw for kw in w if len(kw) > 2]
            if not keywords:
                return []
            conditions = " OR ".join(["LOWER(content) LIKE ?" for kw in keywords])
            params = [f"%{kw}%" for kw in keywords]
            rows = conn.execute(
                f"SELECT chunk_id, content, metadata FROM vectors WHERE {conditions} "
                f"ORDER BY importance DESC, access_count DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            results = []
            for chunk_id, content, metadata in rows:
                r = {"content": content[:500], "metadata": metadata}
                results.append(r)
                try:
                    conn.execute(
                        "UPDATE vectors SET access_count = access_count + 1, "
                        "accessed_at = CURRENT_TIMESTAMP WHERE chunk_id = ?",
                        (chunk_id,),
                    )
                except Exception:
                    pass
            conn.commit()
            return results
    except Exception as e:
        logger.warning(f"[RAG] Query failed: {e}")
        return []


def _build_rag_context(user_message: str) -> str:
    """Build a context injection block from RAG and KG results."""
    parts = []
    kg_results = _query_knowledge_graph(user_message)
    if kg_results:
        kg_lines = []
        for r in kg_results:
            line = f"- {r['entity']} ({r['type']})"
            rels = ", ".join(f"{rel['relation']}→{rel['target']}" for rel in r.get("relations", []))
            if rels:
                line += f": {rels}"
            kg_lines.append(line)
        parts.append("[Knowledge Graph Context]\n" + "\n".join(kg_lines))

    rag_results = _query_rag_vectors(user_message)
    if rag_results:
        rag_lines = []
        for r in rag_results:
            meta = r.get("metadata", "")
            rag_lines.append(f"- [{meta}] {r['content'][:200]}")
        parts.append("[Retrieved Context]\n" + "\n".join(rag_lines))

    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# _stream_one_turn: Single LLM streaming call
# ═══════════════════════════════════════════════════════════════

async def _stream_one_turn(
    messages: List[Dict[str, Any]],
    env: Dict[str, str],
    tools: list,
    stream_stats: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Execute one LLM streaming call. Yields event dicts:
      {"type": "chunk", "content": "..."}           — text token
      {"type": "tool_calls_complete", "tool_calls": [...]}  — accumulated tool calls
      {"type": "usage", ...}                         — usage stats
    """
    try:
        from openai import AsyncOpenAI
    except ImportError:
        py_ver = sys.version_info
        venv_site = Path(f"/home/field/nanobotProjects/nanobot/.venv/lib/python{py_ver.major}.{py_ver.minor}/site-packages")
        if venv_site.is_dir():
            sys.path.insert(0, str(venv_site))
        from openai import AsyncOpenAI

    model = _resolve_model(env)
    max_tokens = int(env.get("NANOBOT_MAX_TOKENS", "4096"))
    temperature = float(env.get("NANOBOT_TEMPERATURE", "0.2"))
    api_key, api_base = _get_api_credentials(env, model)

    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools

    timeout = 120
    model_lower = model.lower()
    if "llama" in model_lower or "qwen" in model_lower or "deepseek" in model_lower:
        timeout = 300

    acompletion = AsyncOpenAI(api_key=api_key, base_url=api_base, timeout=timeout)

    try:
        stream = await acompletion.chat.completions.create(**kwargs, timeout=timeout)
    except Exception as api_err:
        # Sanitize: truncate error message to avoid leaking sensitive data
        _err_msg = str(api_err)[:300]
        logger.error(f"[_stream_one_turn] API call failed: {type(api_err).__name__}: {_err_msg}")
        logger.debug("[_stream_one_turn] Full exception detail:", exc_info=True)
        raise  # re-raise so caller's error handling (P80/P10/retry) can classify and act
    tool_calls_acc = {}
    _emitted_ready = False
    _latex_buf = _LatexStreamBuffer()
    # P80: Buffer for detecting cross-chunk text tool calls (e.g. "call:file_read{...}")
    _tcall_buf = ""

    async for chunk in stream:
        choices = chunk.choices if hasattr(chunk, "choices") else []
        if not choices:
            # Usage info
            usage_obj = getattr(chunk, "usage", None)
            if usage_obj:
                pt = getattr(usage_obj, "prompt_tokens", 0)
                ct = getattr(usage_obj, "completion_tokens", 0)
                tt = getattr(usage_obj, "total_tokens", 0)
                if stream_stats is not None:
                    stream_stats["prompt_tokens"] = pt
                    stream_stats["completion_tokens"] = ct
                    stream_stats["total_tokens"] = tt
                yield {"type": "usage", "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
            continue

        delta = choices[0].delta if hasattr(choices[0], "delta") else None
        if delta is None:
            continue

        delta_obj = delta
        delta_text = getattr(delta_obj, "content", None) or ""

        # Strip special tokens and text tool calls
        if delta_text:
            delta_text = _SPECIAL_TOKEN_RE.sub("", delta_text)
            delta_text = _TEXT_TOOL_CALL_RE.sub("", delta_text)
            if not delta_text:
                pass  # fully stripped
            else:
                # P80: Buffer text that looks like a tool call being formed
                _tcall_buf += delta_text
                # Check if buffer contains a complete tool call — strip it
                _tcall_buf = _TEXT_TOOL_CALL_RE.sub("", _tcall_buf)
                # If buffer is accumulating a partial tool call, hold it
                # B4: Also hold bare tool names being formed (e.g. "file_read{...")
                _is_partial = (
                    ("call:" in _tcall_buf or _tcall_buf.rstrip().endswith("{"))
                    and "}" not in _tcall_buf
                    and len(_tcall_buf) < 500
                )
                if _is_partial:
                    continue  # keep buffering
                # If buffer got too large or has no "call:" prefix, flush it
                if _tcall_buf:
                    _tcall_buf = _TEXT_TOOL_CALL_RE.sub("", _tcall_buf)  # final strip
                    # B7: After stripping complete calls, re-check if remainder
                    # is a partial tool call (e.g. "call:" left after stripping
                    # "call:file_read{...}call:" from concatenated calls)
                    if _tcall_buf:
                        _remainder_partial = (
                            _tcall_buf.rstrip().startswith("call:")
                            or _tcall_buf.rstrip().endswith("{")
                            or any(
                                _tcall_buf.rstrip().startswith(t)
                                for t in (
                                    "file_read", "file_edit", "file_write",
                                    "file_list", "grep_search", "find_by_name",
                                    "shell_execute", "python_execute",
                                    "web_fetch", "web_search", "sub_agent",
                                    "todo_manage",
                                )
                            )
                        )
                        if _remainder_partial and len(_tcall_buf) < 500:
                            continue  # keep buffering
                        cleaned_parts = _latex_buf.add(_tcall_buf)
                        for cleaned in cleaned_parts:
                            if cleaned:
                                yield {"type": "chunk", "content": cleaned}
                _tcall_buf = ""

        # Tool calls accumulation
        tool_calls_delta = getattr(delta_obj, "tool_calls", None)
        if tool_calls_delta:
            for tc_delta in tool_calls_delta:
                idx = tc_delta.index
                tc_id = getattr(tc_delta, "id", None)
                tc_func = getattr(tc_delta, "function", None)
                tc_name = getattr(tc_func, "name", None) if tc_func else None
                tc_args = getattr(tc_func, "arguments", None) if tc_func else None

                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {"id": tc_id or f"call_{idx}", "type": "function", "function": {"name": "", "arguments": ""}}
                tc_entry = tool_calls_acc[idx]
                tc_fn = tc_entry["function"]
                if tc_id:
                    tc_entry["id"] = tc_id
                if tc_name:
                    tc_fn["name"] = tc_name
                if tc_args:
                    tc_fn["arguments"] += tc_args

    # P80: Flush tool call text buffer
    if _tcall_buf:
        _tcall_buf = _TEXT_TOOL_CALL_RE.sub("", _tcall_buf).strip()
        if _tcall_buf:
            for cleaned in _latex_buf.add(_tcall_buf):
                if cleaned:
                    yield {"type": "chunk", "content": cleaned}

    # Flush LaTeX buffer
    for cleaned in _latex_buf.finish():
        if cleaned:
            yield {"type": "chunk", "content": cleaned}

    # Emit accumulated tool calls
    if tool_calls_acc:
        sorted_tcs = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]
        # Parse arguments
        for tc in sorted_tcs:
            try:
                tc["_parsed_args"] = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, ValueError):
                tc["_parsed_args"] = {}
        yield {"type": "tool_calls_complete", "tool_calls": sorted_tcs}


# ═══════════════════════════════════════════════════════════════
# P9/P44: Large Tool Result Disk Persistence
# ═══════════════════════════════════════════════════════════════
#
# Large tool results are saved to disk instead of being silently truncated.
# The model receives a preview + file path and can use file_read to access
# the full output later.  This prevents information loss from head+tail
# truncation while keeping the context window lean.
#
# Per-message aggregate budget prevents parallel tool calls from
# collectively blowing the context window.
#
_PERSIST_THRESHOLD = 20_000          # P44: lowered from 50K for 32K context models
_MAX_RESULTS_PER_MESSAGE = 60_000    # P44: aggregate budget — 60K keeps ~30% of 32K for other content
_TOOL_RESULT_CLEARED_MSG = "[Old tool result content cleared]"

# ═══════════════════════════════════════════════════════════════
# P22: Time-Based MicroCompact
# ═══════════════════════════════════════════════════════════════
# When the user returns after a long idle period, Ollama's KV cache is
# cold.  Old tool results are wasting tokens with zero cache benefit.
# Clear them proactively — matches Claw's maybeTimeBasedMicrocompact().

_TIME_BASED_MC_GAP_MINUTES = 5   # idle gap threshold (configurable)
_TIME_BASED_MC_KEEP_RECENT = 3   # keep the N most recent tool results


def _get_time_gap_minutes(last_response_file: Path) -> float:
    """Read the last response timestamp and return minutes since then.
    Returns 0.0 if no timestamp file exists."""
    try:
        if last_response_file.is_file():
            ts = float(last_response_file.read_text(encoding="utf-8").strip())
            gap = (time.time() - ts) / 60.0
            return max(0.0, gap)
    except Exception:
        pass
    return 0.0


def _save_last_response_time(last_response_file: Path) -> None:
    """Write the current timestamp for P22 idle detection."""
    try:
        last_response_file.parent.mkdir(parents=True, exist_ok=True)
        last_response_file.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


def _time_based_micro_compact(
    messages: List[Dict[str, Any]],
    gap_minutes: float,
) -> int:
    """P22: Clear old tool results when session has been idle.

    When gap_minutes > _TIME_BASED_MC_GAP_MINUTES, content-clear all
    compactable tool results except the most recent N.
    Returns the number of messages cleared.

    Mirrors Claw's microCompact.ts maybeTimeBasedMicrocompact().
    """
    if gap_minutes < _TIME_BASED_MC_GAP_MINUTES:
        return 0

    # Collect indices of tool messages (oldest first)
    tool_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "tool" and m.get("_tool_name")
    ]

    if len(tool_indices) <= _TIME_BASED_MC_KEEP_RECENT:
        return 0  # not enough to clear

    # Keep the last N, clear the rest
    clear_indices = set(tool_indices[:-_TIME_BASED_MC_KEEP_RECENT])
    cleared = 0
    for idx in clear_indices:
        msg = messages[idx]
        content = msg.get("content", "")
        # A1: Protect high-value content (sub-agent summaries) from clearing
        if _is_high_value_content(content):
            continue
        if content and content != _TOOL_RESULT_CLEARED_MSG:
            msg["content"] = _TOOL_RESULT_CLEARED_MSG
            cleared += 1

    if cleared:
        logger.info(
            f"[P22 TimeMC] Gap {gap_minutes:.1f}min > {_TIME_BASED_MC_GAP_MINUTES}min, "
            f"cleared {cleared} old tool result(s), kept last {_TIME_BASED_MC_KEEP_RECENT}"
        )
    return cleared


# ═══════════════════════════════════════════════════════════════
# CW1: Token-Weighted MicroCompact Eviction
# ═══════════════════════════════════════════════════════════════
# Instead of count-based triggers, greedily evict the largest (by token)
# old tool results when context pressure is high.  Addresses audit
# findings: uses token estimation with 120% safety margin, and treats
# same-turn tool results as atomic groups (all-or-nothing).

_TW_MC_KEEP_RECENT_TURNS = 2   # protect tool results from the last N turns
_TW_MC_TOKEN_SAFETY_MARGIN = 1.2  # 120% — compensate for estimation error
_TW_MC_TARGET_RATIO = 0.70  # target: reduce to 70% of ceiling after eviction
_CW1_EVICTIONS_TOTAL = 0      # Prometheus: cumulative evicted tool results
_CW1_TOKENS_FREED_TOTAL = 0   # Prometheus: cumulative tokens freed by CW1

# CW3: AutoCompact Circuit Breaker (Claw MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES)
_CW3_MAX_CONSECUTIVE_FAILURES = 3
_CW3_HALF_OPEN_PROBE_INTERVAL = 5  # turns between recovery probe attempts


def _token_weighted_micro_compact(
    messages: List[Dict[str, Any]],
    current_turn: int,
    ceiling: int,
) -> int:
    """CW1: Token-weighted greedy eviction of old tool results.

    Called when context pressure is between 70-80% of ceiling (before
    auto-compact would trigger).  Greedily clears the largest tool result
    groups (by estimated token count) until context drops below target.

    Audit-mandated safeguards:
      - Token safety margin (120%): ``_estimate_tokens(content) * 1.2``
        to avoid underestimating and missing eviction opportunities.
      - Atomic turn groups: all tool results from the same turn are
        evicted together to prevent orphaned conversation fragments.
      - High-value protection: sub-agent summaries are never evicted.

    Returns the number of messages cleared.
    """
    current_tokens = _estimate_messages_tokens(messages)
    target = int(ceiling * _TW_MC_TARGET_RATIO)
    if current_tokens <= target:
        return 0

    # Phase 1: Collect eviction candidates grouped by turn
    # Each group = {turn: int, indices: [int], tokens: int}
    turn_groups: Dict[int, Dict[str, Any]] = {}
    recent_cutoff = current_turn - _TW_MC_KEEP_RECENT_TURNS

    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        msg_turn = msg.get("_turn", 0)
        if msg_turn <= 0 or msg_turn > recent_cutoff:
            continue  # protect recent turns
        content = msg.get("content", "")
        if not content or content == _TOOL_RESULT_CLEARED_MSG:
            continue
        if _is_high_value_content(content):
            continue
        # Token estimate with safety margin
        est = int(_estimate_tokens(content) * _TW_MC_TOKEN_SAFETY_MARGIN)
        if msg_turn not in turn_groups:
            turn_groups[msg_turn] = {"turn": msg_turn, "indices": [], "tokens": 0}
        turn_groups[msg_turn]["indices"].append(i)
        turn_groups[msg_turn]["tokens"] += est

    if not turn_groups:
        return 0

    # Phase 2: Sort groups by token size descending (greedy: biggest first)
    sorted_groups = sorted(turn_groups.values(), key=lambda g: g["tokens"], reverse=True)

    # Phase 3: Evict groups until we reach the target
    cleared = 0
    tokens_freed = 0
    for group in sorted_groups:
        if current_tokens - tokens_freed <= target:
            break  # enough freed
        for idx in group["indices"]:
            messages[idx]["content"] = _TOOL_RESULT_CLEARED_MSG
            cleared += 1
        tokens_freed += group["tokens"]

    if cleared:
        global _CW1_EVICTIONS_TOTAL, _CW1_TOKENS_FREED_TOTAL
        _CW1_EVICTIONS_TOTAL += cleared
        _CW1_TOKENS_FREED_TOTAL += tokens_freed
        groups_evicted = sum(1 for g in sorted_groups if any(
            messages[idx]["content"] == _TOOL_RESULT_CLEARED_MSG
            for idx in g["indices"]
        ))
        logger.info(
            f"[CW1 TW-MC] Evicted {cleared} tool result(s) from {groups_evicted} turn group(s), "
            f"~{tokens_freed} tokens freed (target: {current_tokens}→{target}, ceiling={ceiling})"
        )
    return cleared


def _persist_large_result(
    tool_name: str,
    result_content: str,
    workspace: Path,
    session_id: str,
) -> str:
    """Save a large tool result to disk and return a preview with file path.

    Mirrors Claw's `maybePersistLargeToolResult` in toolResultStorage.ts:
      - Writes full output to  <workspace>/.tool_results/<session>/<tool>_<ts>.txt
      - Returns first 3000 chars + a <persisted-output> tag with the path
      - Model can use file_read on the path to access the full output

    This is ONLY called for outputs exceeding _PERSIST_THRESHOLD (20K chars).
    Smaller outputs go through the normal MicroCompact head+tail truncation.
    """
    try:
        result_dir = workspace / ".tool_results" / session_id
        result_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{tool_name}_{int(time.time())}.txt"
        result_path = result_dir / filename
        result_path.write_text(result_content, encoding="utf-8")
        total = len(result_content)
        # P44: Preview = first 2000 + last 500 chars
        preview_head = result_content[:2000]
        preview_tail = result_content[-500:] if total > 2500 else ""
        tail_section = f"\n...\n{preview_tail}" if preview_tail else ""
        return (
            f"[Result saved to disk: {result_path} ({total:,} chars). Preview:]\n"
            f"{preview_head}{tail_section}\n\n"
            f"[Use file_read(path=\"{result_path}\") to access the full output]"
        )
    except Exception as e:
        logger.warning(f"[Persist] Failed to save result to disk: {e}")
        # Fallback: do normal head+tail truncation
        head = result_content[:3000]
        tail = result_content[-2000:]
        return (
            f"{head}\n\n"
            f"... [{len(result_content) - 5000} chars omitted (disk persist failed: {e})] ...\n\n"
            f"{tail}"
        )


# ═══════════════════════════════════════════════════════════════
# P3: Tiered MicroCompact — per-tool truncation config
# ═══════════════════════════════════════════════════════════════
_DEFAULT_MC = {"max": 8000, "head": 3000, "tail": 2000}

MICRO_COMPACT_CONFIG: Dict[str, dict] = {
    "file_read":        {"max": 12000, "head": 5000, "tail": 3000},
    "grep_search":      {"max": 6000,  "head": 3000, "tail": 2000},
    "shell_execute":    {"max": 8000,  "head": 3000, "tail": 2000},
    "file_list":        {"max": 4000,  "head": 2000, "tail": 1000},
    "file_edit":        {"max": 3000,  "head": 2000, "tail": 500},
    "file_write":       {"max": 3000,  "head": 2000, "tail": 500},
    "python_execute":   {"max": 8000,  "head": 3000, "tail": 2000},
    "find_by_name":     {"max": 4000,  "head": 2000, "tail": 1000},
    "web_fetch":        {"max": 10000, "head": 5000, "tail": 2000},
    "web_search":       {"max": 4000,  "head": 2000, "tail": 1000},
    "sub_agent":        {"max": 8000,  "head": 3000, "tail": 2000},
    "todo_manage":      {"max": 2000,  "head": 1000, "tail": 500},
}

_MC_AGE_DECAY_TURNS = 3  # after N turns, halve limits

# ═══════════════════════════════════════════════════════════════
# U4: Cache-Aware MicroCompact — hint-based context management
# ═══════════════════════════════════════════════════════════════
# Instead of mutating old tool messages (which invalidates KV cache),
# insert a lightweight system hint listing which turns' results are stale.
# The model treats the hint as instruction to deprioritize those results.
_U4_CACHE_AWARE = True  # master switch
_U4_STALE_TURN_THRESHOLD = 3  # tool results older than N turns are "stale"
_U4_HINT_TAG = "[CONTEXT MANAGEMENT]"


def _build_cache_aware_hint(messages: List[Dict[str, Any]], current_turn: int) -> str:
    """U4: Build a hint listing stale tool results for model to deprioritize.

    Instead of modifying/clearing old tool result content (which breaks KV cache),
    we insert a lightweight system message that tells the model:
    - Which turns have stale tool results
    - That it should rely on summaries/memory for those turns

    Returns the hint string, or "" if no stale results exist.
    """
    if current_turn <= _U4_STALE_TURN_THRESHOLD:
        return ""

    stale_cutoff = current_turn - _U4_STALE_TURN_THRESHOLD
    # Collect (turn, tool_name) pairs for stale tool results
    stale_turns: Dict[int, List[str]] = {}
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        msg_turn = msg.get("_turn", 0)
        if msg_turn <= 0 or msg_turn > stale_cutoff:
            continue
        content = msg.get("content", "")
        if not content or content == _TOOL_RESULT_CLEARED_MSG:
            continue
        # A1: Don't mark high-value content as stale
        if _is_high_value_content(content):
            continue
        tool_name = msg.get("_tool_name", "tool")
        stale_turns.setdefault(msg_turn, []).append(tool_name)

    if not stale_turns:
        return ""

    # Build compact hint
    parts = []
    for t in sorted(stale_turns.keys()):
        tools = stale_turns[t]
        tool_summary = ", ".join(sorted(set(tools)))
        parts.append(f"  turn {t}: {tool_summary}")

    hint = (
        f"{_U4_HINT_TAG}\n"
        f"The following tool results from earlier turns are STALE. "
        f"Do NOT rely on their detailed content — use file_read or grep_search "
        f"to get fresh data if needed:\n"
        + "\n".join(parts) + "\n"
        f"Focus on the most recent tool results (turns {stale_cutoff + 1}-{current_turn})."
    )
    return hint

# ═══════════════════════════════════════════════════════════════
# A1: High-value content detection for compaction protection
# ═══════════════════════════════════════════════════════════════
_SUB_AGENT_SUMMARY_RE = re.compile(r'\[Sub-agent\s+\w+\s+summary:')


def _is_high_value_content(content: str) -> bool:
    """A1: Check if content should be protected from compaction/clearing.

    High-value content:
    - Sub-agent summaries (research/edit/verify results)
    - Skill invocation markers
    Returns True if the content should be preserved during micro-compact.
    """
    if not content:
        return False
    return bool(_SUB_AGENT_SUMMARY_RE.search(content))


# ═══════════════════════════════════════════════════════════════
# D4: File-target cross-check — prevent editing wrong files
# ═══════════════════════════════════════════════════════════════
_USER_FILE_RE = re.compile(
    r'(?:在|in|of|from|to|file|文件)\s+'
    r'[`"\']?([a-zA-Z0-9_/\\.-]+\.(?:py|js|ts|tsx|jsx|css|html|json|md|yaml|yml|toml|cfg|txt|sh|rs|go|java|c|cpp|h|rb))[`"\']?',
    re.IGNORECASE,
)

def _extract_user_target_files(task_text: str) -> List[str]:
    """Extract filenames/paths the user explicitly mentioned in their request."""
    matches = _USER_FILE_RE.findall(task_text)
    return [m.strip("'\"`") for m in matches] if matches else []


def _check_edit_target_mismatch(
    edit_path: str, user_files: List[str], workspace: str = ""
) -> str:
    """Return warning string if edit_path doesn't match any user-mentioned file, else ''."""
    if not user_files or not edit_path:
        return ""
    edit_basename = os.path.basename(edit_path)
    for uf in user_files:
        uf_base = os.path.basename(uf)
        # Match by basename or by suffix
        if edit_basename == uf_base:
            return ""
        if edit_path.endswith(uf) or uf.endswith(edit_basename):
            return ""
    return (
        f"[TARGET MISMATCH WARNING] The user asked to modify '{user_files[0]}' "
        f"but you are editing '{edit_path}'. STOP and verify you have the correct file. "
        f"Use find_by_name to locate '{user_files[0]}' before proceeding."
    )


# ═══════════════════════════════════════════════════════════════
# A2/D3: Session file activity tracker — active workbench awareness
# ═══════════════════════════════════════════════════════════════
# Maps file_path → {"last_action": "read"|"write", "last_turn": int,
#                    "write_success": bool|None, "lines": int, "summary": str}
#
# Concurrency note (see SECURITY.md R4):
# These globals are mutated only by _track_file_activity() /
# _record_tool_failure(), which are called **synchronously** in the main
# event loop thread AFTER `asyncio.gather(...)` has joined all concurrent
# tool coroutines.  There is no concurrent write path under the current
# asyncio-only architecture.  Session scoping (one process = one logical
# session) is a separate architectural limitation tracked as R4.
#
# SINGLE-USER ASSUMPTION: These module-level dicts are shared across all
# sessions in the same process.  If the architecture evolves to support
# concurrent multi-user sessions, they MUST be replaced with per-session
# state (e.g. Dict[session_id, Dict]) or passed via dependency injection.
# See SECURITY.md R4 for the specific failure scenario and upgrade path.
_SESSION_ACTIVE_FILES: Dict[str, Dict[str, Any]] = {}
_SESSION_ACTIVE_FILES_MAX = 12  # max files to track
# Backward-compat aliases for A2 post-compact injection
_SESSION_FILE_READS = _SESSION_ACTIVE_FILES
_SESSION_FILE_READS_MAX = _SESSION_ACTIVE_FILES_MAX
# CTO-audit: Observability counter — tracks re-reads of already-hinted files
_HINT_REREAD_COUNT = 0  # model re-read a file already in [Preserved file context]
_HINT_REREAD_LOGGED: set = set()  # per-file throttle: only log first re-read per path
_HINT_REREAD_WARN_THRESHOLD = 3  # emit WARNING when cumulative re-reads exceed this


def _extract_file_defs(content: str) -> str:
    """Extract key definitions from file content for summary."""
    defs = re.findall(r'(?:def |class |function |const |export )\w+', content[:3000])
    return ", ".join(d.split()[-1] for d in defs[:6]) if defs else ""


def _track_file_activity(tool_name: str, result: dict, tool_args: dict, turn: int) -> None:
    """D3: Record file read/write activity for workbench awareness.
    Supersedes A2 _track_file_read — handles both reads and writes."""
    assert threading.current_thread() is threading.main_thread(), \
        f"_track_file_activity must run on the main thread (see SECURITY.md R4), got {threading.current_thread().name!r}"
    output = result.get("output", "") or ""
    is_read = tool_name == "file_read"
    is_write = tool_name in ("file_edit", "file_write")
    if not (is_read or is_write):
        return
    if is_read and (not output or result.get("error")):
        return

    path = tool_args.get("path", "")
    if not path and is_read:
        m = re.search(r'\[File:\s*(\S+)\s*\|', output)
        if m:
            path = m.group(1)
    if not path:
        return

    # CTO-audit: Detect re-reads of already-known files (prompt hint may have been ignored)
    global _HINT_REREAD_COUNT
    if is_read and path in _SESSION_ACTIVE_FILES:
        prev = _SESSION_ACTIVE_FILES[path]
        if prev.get("last_action") == "read":
            _HINT_REREAD_COUNT += 1
            if path not in _HINT_REREAD_LOGGED:
                _HINT_REREAD_LOGGED.add(path)
                logger.info(f"[Observability] Re-read of known file {path} (prev turn {prev.get('last_turn')}, now turn {turn}, cumulative re-reads: {_HINT_REREAD_COUNT})")

    action = "read" if is_read else "write"
    entry = _SESSION_ACTIVE_FILES.get(path, {
        "last_action": action, "last_turn": turn,
        "write_success": None, "lines": 0, "summary": "",
    })
    entry["last_action"] = action
    entry["last_turn"] = turn

    if is_read:
        m_lines = re.search(r'(\d+)\s*lines', output)
        entry["lines"] = int(m_lines.group(1)) if m_lines else 0
        defs_str = _extract_file_defs(output)
        entry["summary"] = f"defines: {defs_str}" if defs_str else ""
    elif is_write:
        entry["write_success"] = result.get("success", False) if result else False

    _SESSION_ACTIVE_FILES[path] = entry

    # Evict oldest if over limit
    if len(_SESSION_ACTIVE_FILES) > _SESSION_ACTIVE_FILES_MAX:
        oldest_key = min(_SESSION_ACTIVE_FILES, key=lambda k: _SESSION_ACTIVE_FILES[k]["last_turn"])
        del _SESSION_ACTIVE_FILES[oldest_key]


# Backward-compat wrapper: old call sites still use this name
_track_file_read = _track_file_activity


def _get_active_files_summary(workspace: str = "", max_files: int = 6) -> str:
    """D3: Build active workbench summary for per-turn injection.
    Shows recently operated files with action icons and status."""
    if not _SESSION_ACTIVE_FILES:
        return ""
    recent = sorted(_SESSION_ACTIVE_FILES.items(),
                    key=lambda kv: kv[1]["last_turn"], reverse=True)[:max_files]
    lines = [
        "[When the user asks about active/recent files, refer to this list.]",
        "Active files in this session:",
    ]
    for path, info in recent:
        rel = os.path.relpath(path, workspace) if workspace and os.path.isabs(path) else path
        icon = "📖" if info["last_action"] == "read" else "✏️"
        status = ""
        if info["last_action"] == "write":
            status = " ✅" if info.get("write_success") else " ❌"
        lines.append(f"  {icon} {rel}{status}")
        if info.get("summary"):
            lines.append(f"     {info['summary'][:120]}")
    return "\n".join(lines)


def _get_preserved_files_summary(max_files: int = 5) -> str:
    """A2 compat: Build summary of recently read files for post-compact injection."""
    if not _SESSION_ACTIVE_FILES:
        return ""
    items = sorted(_SESSION_ACTIVE_FILES.items(), key=lambda kv: kv[1]["last_turn"], reverse=True)
    result_lines = []
    for _, v in items[:max_files]:
        # Reconstruct A2-style summary from D3 entry
        defs = f", {v['summary']}" if v.get("summary") else ""
        result_lines.append(f"- ({v.get('lines', 0)} lines{defs})")
    return "\n".join(result_lines)


def _reset_observability_state() -> None:
    """Reset hint re-read observability counters (CTO-audit lifecycle binding)."""
    global _HINT_REREAD_COUNT
    _HINT_REREAD_COUNT = 0
    _HINT_REREAD_LOGGED.clear()


def _run_post_compact_cleanup() -> None:
    """CW4: Centralized post-compact cleanup (Claw runPostCompactCleanup pattern).

    Called after both P5 auto-compact and P10 reactive compact to reset
    session state that becomes stale after context compaction.
    """
    # 1. Reset file_read session tracker — model must re-read files after compact
    try:
        from tools.file_read import reset_session_read_tracker
        reset_session_read_tracker()
    except ImportError:
        pass

    # 2. Reset observability counters (hint re-read tracking)
    _reset_observability_state()

    logger.info("[CW4] Post-compact cleanup completed")


def _reset_session_file_reads() -> None:
    """Reset the file activity tracker (called at session start)."""
    assert threading.current_thread() is threading.main_thread(), \
        f"_reset_session_file_reads must run on the main thread (see SECURITY.md R4), got {threading.current_thread().name!r}"
    _SESSION_ACTIVE_FILES.clear()
    _reset_observability_state()


def get_active_files_snapshot(max_files: int = 5) -> list:
    """Return a snapshot of recently active files, sorted by recency.

    Public API for external consumers (e.g. compact_engine) so they
    don't need to import the private ``_SESSION_ACTIVE_FILES`` global.

    Returns:
        List of (path, info_dict) tuples, most-recent first, capped
        at *max_files*.  The returned list is a shallow copy — safe
        to iterate without risking ``RuntimeError`` from concurrent
        modification.
    """
    return sorted(
        list(_SESSION_ACTIVE_FILES.items()),
        key=lambda kv: kv[1].get("last_turn", 0),
        reverse=True,
    )[:max_files]


# ═══════════════════════════════════════════════════════════════
# A3: Session failure log — prevents repeating failed approaches
# ═══════════════════════════════════════════════════════════════
# SINGLE-USER ASSUMPTION: shared across all sessions — see note above.
_SESSION_FAILURES: List[Dict[str, Any]] = []
_SESSION_FAILURES_MAX = 10  # max failures to track


def _record_tool_failure(tool_name: str, tool_args: dict, error: str, turn: int) -> None:
    """A3: Record a tool execution failure for later injection."""
    assert threading.current_thread() is threading.main_thread(), \
        f"_record_tool_failure must run on the main thread (see SECURITY.md R4), got {threading.current_thread().name!r}"
    if not error:
        return
    entry = {
        "tool": tool_name,
        "args_summary": str(tool_args)[:200],
        "error": error[:300],
        "turn": turn,
    }
    _SESSION_FAILURES.append(entry)
    if len(_SESSION_FAILURES) > _SESSION_FAILURES_MAX:
        _SESSION_FAILURES.pop(0)  # evict oldest


def _get_relevant_failures(tool_name: str = "", context: str = "", max_results: int = 3) -> str:
    """A3: Retrieve recent failures relevant to current context."""
    if not _SESSION_FAILURES:
        return ""
    # Filter by tool name or keyword match
    relevant = []
    for f in reversed(_SESSION_FAILURES):  # most recent first
        if tool_name and f["tool"] == tool_name:
            relevant.append(f)
        elif context:
            # Check if any keyword from the failure appears in context
            error_words = set(f["error"].lower().split()[:10])
            context_words = set(context.lower().split()[:20])
            if error_words & context_words:
                relevant.append(f)
        if len(relevant) >= max_results:
            break
    if not relevant:
        return ""
    lines = ["[Previous failures in this session — avoid repeating these approaches]:"]
    for f in relevant:
        lines.append(f"- Turn {f['turn']}: {f['tool']}({f['args_summary'][:80]}) → {f['error'][:150]}")
    return "\n".join(lines)


def _reset_session_failures() -> None:
    """Reset the failure log (called at session start)."""
    assert threading.current_thread() is threading.main_thread(), \
        f"_reset_session_failures must run on the main thread (see SECURITY.md R4), got {threading.current_thread().name!r}"
    _SESSION_FAILURES.clear()


# ═══════════════════════════════════════════════════════════════
# A5: Lightweight repo map for context injection
# ═══════════════════════════════════════════════════════════════
# SINGLE-USER ASSUMPTION: keyed by workspace path, not session — safe
# for single-user (all sessions share workspace), but would need
# per-session keying if different users have different workspaces.
_REPO_MAP_CACHE: Dict[str, str] = {}  # workspace_path → cached map
_REPO_MAP_CACHE_TURN: Dict[str, int] = {}  # workspace_path → turn when cached
_REPO_MAP_REGEN_INTERVAL = 20  # regenerate every N turns

_REPO_MAP_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".egg-info",
    ".nanobot_state", ".session_summaries", ".nanobot_memory",
    ".tool_results", "backups", "artifacts", "audit_logs",
    "logs", "cache", ".cache", ".sessions", "web_sessions",
}
_REPO_MAP_KEY_FILES = {
    "README.md", "setup.py", "pyproject.toml", "package.json",
    "Makefile", "Dockerfile", "requirements.txt", "Cargo.toml",
    "go.mod", "tsconfig.json", ".env", "NANOBOT.md",
}
_REPO_MAP_CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".rb", ".php", ".swift", ".kt",
}
_REPO_MAP_ALLOW_DOT = {".env", ".nanobot", ".github", ".vscode"}


def _generate_repo_map(
    workspace: Path,
    max_depth: int = 3,
    max_entries: int = 80,
    max_items_per_dir: int = 15,
) -> str:
    """A5: Generate a lightweight repo map showing directory structure.

    Produces a concise tree with:
    - Key config/README files always shown
    - Code files summarized by extension when > max_items_per_dir
    - Subdirectories with shallow file counts (no deep rglob)
    - Noise directories (.git, node_modules, backups, etc.) skipped
    """
    if not workspace or not workspace.is_dir():
        return ""

    lines = [f"Repo: {workspace.name}/"]
    entry_count = 0

    def _walk(path: Path, prefix: str, depth: int):
        nonlocal entry_count
        if depth > max_depth or entry_count >= max_entries:
            return

        try:
            items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (PermissionError, OSError):
            return

        dirs = []
        files = []
        for item in items:
            name = item.name
            # Skip all dot-dirs except explicitly allowed ones
            if name.startswith(".") and name not in _REPO_MAP_ALLOW_DOT:
                continue
            if name in _REPO_MAP_SKIP_DIRS:
                continue
            if item.is_dir():
                dirs.append(item)
            elif item.is_file():
                files.append(item)

        code_files = [f for f in files if f.suffix in _REPO_MAP_CODE_EXTS]
        key_files = [f for f in files if f.name in _REPO_MAP_KEY_FILES]
        key_set = set(kf.name for kf in key_files)

        # 1) Always show key files first
        for kf in key_files:
            if entry_count >= max_entries:
                break
            lines.append(f"{prefix}{kf.name}")
            entry_count += 1

        # 2) Show subdirectories (before code files for better orientation)
        for d in dirs:
            if entry_count >= max_entries:
                break
            # Shallow count: only direct children, not recursive rglob
            try:
                sub_count = sum(1 for c in d.iterdir() if c.is_file())
                sub_dirs = sum(1 for c in d.iterdir() if c.is_dir()
                               and c.name not in _REPO_MAP_SKIP_DIRS
                               and not (c.name.startswith(".") and c.name not in _REPO_MAP_ALLOW_DOT))
            except (PermissionError, OSError):
                sub_count = 0
                sub_dirs = 0
            parts = []
            if sub_count > 0:
                parts.append(f"{sub_count} files")
            if sub_dirs > 0:
                parts.append(f"{sub_dirs} dirs")
            count_str = f" ({', '.join(parts)})" if parts else ""
            lines.append(f"{prefix}{d.name}/{count_str}")
            entry_count += 1
            _walk(d, prefix + "  ", depth + 1)

        # 3) Code files: summarize if too many, else list individually
        non_key_code = [cf for cf in code_files if cf.name not in key_set]
        if len(non_key_code) > max_items_per_dir:
            ext_counts: Dict[str, int] = {}
            for cf in non_key_code:
                ext_counts[cf.suffix] = ext_counts.get(cf.suffix, 0) + 1
            ext_str = ", ".join(
                f"{c} {e}" for e, c in sorted(ext_counts.items(), key=lambda x: -x[1])
            )
            lines.append(f"{prefix}({len(non_key_code)} code files: {ext_str})")
            entry_count += 1
        else:
            for cf in non_key_code:
                if entry_count >= max_entries:
                    break
                lines.append(f"{prefix}{cf.name}")
                entry_count += 1

    _walk(workspace, "  ", 0)

    if entry_count >= max_entries:
        lines.append(f"  ... (truncated at {max_entries} entries)")

    return "\n".join(lines)


def _get_repo_map(workspace: Path, current_turn: int = 0) -> str:
    """A5: Get cached repo map, regenerating if stale."""
    ws_key = os.path.abspath(workspace)
    cached_turn = _REPO_MAP_CACHE_TURN.get(ws_key, -999)

    if ws_key in _REPO_MAP_CACHE and (current_turn - cached_turn) < _REPO_MAP_REGEN_INTERVAL:
        return _REPO_MAP_CACHE[ws_key]

    repo_map = _generate_repo_map(workspace)
    assert threading.current_thread() is threading.main_thread(), \
        f"_get_repo_map must run on the main thread (see SECURITY.md R4), got {threading.current_thread().name!r}"
    _REPO_MAP_CACHE[ws_key] = repo_map
    _REPO_MAP_CACHE_TURN[ws_key] = current_turn
    return repo_map


def _get_mc_limits(
    tool_name: str,
    current_turn: int = 0,
    message_turn: int = 0,
) -> tuple:
    """Get (max_chars, head_chars, tail_chars) for a tool, with age-based decay.

    If the tool result is older than _MC_AGE_DECAY_TURNS, limits are halved.
    This keeps recent results detailed while older ones are compressed,
    freeing context window budget for the model's reasoning.
    """
    cfg = MICRO_COMPACT_CONFIG.get(tool_name, _DEFAULT_MC)
    max_c, head_c, tail_c = cfg["max"], cfg["head"], cfg["tail"]
    age = current_turn - message_turn
    if age > _MC_AGE_DECAY_TURNS:
        max_c //= 2
        head_c //= 2
        tail_c //= 2
    return max_c, head_c, tail_c


def _postprocess_tool_content(
    tool_name: str,
    result: dict,
    current_turn: int = 0,
    message_turn: int = 0,
    workspace: Optional[Path] = None,
    session_id: str = "",
) -> str:
    """Post-process tool output before injecting into conversation messages.

    Two-tier strategy (P9 + P3):
      Tier 1 — Disk persistence (P9/P44): outputs > _PERSIST_THRESHOLD (20K)
               are saved to disk; the model gets a preview + file path.
      Tier 2 — MicroCompact (P3): per-tool truncation with age decay for
               outputs that fit below the persist threshold.

    Mirrors Claw's toolResultStorage.ts patterns:
      1. Empty output → explicit "(tool_name completed with no output)" message
         so the model doesn't interpret silence as an error or end its turn.
      2. Very large output → persisted to disk with preview (P9).
      3. Large output → head + tail preview with truncation notice (P3).
      4. Normal output → pass through unchanged.
    """
    max_c, head_c, tail_c = _get_mc_limits(tool_name, current_turn, message_turn)

    content = result.get("output", "") or ""
    if result.get("error"):
        content = content + "\n[ERROR] " + result["error"] if content else "[ERROR] " + result["error"]

    output = content.strip()

    # P26: Generate one-line summary
    summary_line = _generate_tool_summary(tool_name, result)

    if not output:
        return f"({tool_name} completed with no output)"

    # P9 Tier 1: Very large output → persist to disk
    if len(output) > _PERSIST_THRESHOLD and workspace and session_id:
        logger.info(f"[Persist] {tool_name} output ({len(output):,} chars) exceeds threshold, saving to disk")
        persisted = _persist_large_result(tool_name, output, workspace, session_id)
        return f"{summary_line}\n{persisted}" if summary_line else persisted

    # P3 Tier 2: Normal MicroCompact truncation
    if len(output) > max_c:
        head = output[:head_c]
        tail = output[-tail_c:]
        total = len(output)
        truncated = (
            f"{head}\n\n"
            f"... [{total - head_c - tail_c} characters omitted] ...\n"
            f"(Do NOT guess content of truncated section. Use file_read or grep_search for details.)\n\n"
            f"{tail}"
        )
        return f"{summary_line}\n{truncated}" if summary_line else truncated

    return f"{summary_line}\n{output}" if summary_line else output


def _generate_tool_summary(tool_name: str, result: dict) -> str:
    """Generate a one-line summary for a tool result (P26).

    The summary captures the essential semantics of the result without
    needing the full output.  Used as the first line of tool messages
    so MicroCompact preserves it when truncating aged results.

    Returns a [Summary: ...] line or empty string.
    """
    output = result.get("output", "") or ""
    err = result.get("error", "")

    if err and not output:
        return f"[Summary: {tool_name} FAILED: {err[:100]}]"

    if not output:
        return f"[Summary: {tool_name} — no output]"

    _re26 = re.compile(r'(?:def |class |function |const |export (?:default )?(?:function |class ))(\w+)')

    if tool_name == "file_read":
        # Extract file path and line count from metadata header
        m = re.search(r'\[File: ([^\]|]+)', output)
        path = m.group(1).strip() if m else "?"
        m = re.search(r'(\d+) lines', output)
        lines = m.group(1) if m else "?"
        ext = Path(path).suffix if path != "?" else ""
        lang_map = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
                    ".jsx": "React", ".tsx": "React/TS", ".json": "JSON",
                    ".md": "Markdown", ".yaml": "YAML", ".yml": "YAML",
                    ".html": "HTML", ".css": "CSS", ".sh": "Shell"}
        lang = lang_map.get(ext, ext.lstrip(".") if ext else "text")
        func_match = _re26.findall(output[:3000])
        hint = f", defines: {', '.join(func_match[:5])}" if func_match else ""
        return f"[Summary: file_read {path} — {lines} lines, {lang}{hint}]"

    elif tool_name == "grep_search":
        _re26g = re.search(r'Found (\d+) match', output)
        count = _re26g.group(1) if _re26g else "?"
        first_line = output.split("\n")[1] if "\n" in output else output[:80]
        return f"[Summary: grep_search found {count} matches, first: {first_line[:80]}]"

    elif tool_name in ("shell_execute", "python_execute"):
        first_line = output.split("\n")[0][:100]
        return f"[Summary: {tool_name} → {first_line}]"

    elif tool_name == "file_edit":
        if result.get("_change_set"):
            change_set = result.get("_change_set") or {}
            return f"[Summary: file_edit created pending change set {change_set.get('id', '?')}]"
        return f"[Summary: file_edit completed]"

    elif tool_name == "file_write":
        if result.get("_change_set"):
            change_set = result.get("_change_set") or {}
            return f"[Summary: file_write created pending change set {change_set.get('id', '?')}]"
        return f"[Summary: file_write completed]"

    elif tool_name == "change_set_accept":
        change_set = result.get("_change_set") or {}
        return f"[Summary: change_set_accept applied {change_set.get('id', '?')}]"

    elif tool_name == "change_set_reject":
        change_set = result.get("_change_set") or {}
        return f"[Summary: change_set_reject updated {change_set.get('id', '?')} to {change_set.get('status', 'unknown')}]"

    elif tool_name == "file_list":
        _re26e = re.search(r'(\d+)\s*(?:entries|items|files)', output)
        _count = _re26e.group(1) if _re26e else "?"
        first_line = output.split("\n")[0][:100]
        return f"[Summary: file_list — {_count} entries, {first_line}]"

    elif tool_name == "find_by_name":
        _re26l = re.search(r'Found (\d+)', output)
        _re26f = output.split("\n")[1] if "\n" in output else ""
        return f"[Summary: find_by_name found {_re26l.group(1) if _re26l else '?'} files, first: {_re26f[:80]}]"

    elif tool_name in ("web_search", "web_fetch"):
        first_line = output.split("\n")[0][:100]
        return f"[Summary: {tool_name} → {first_line}]"

    elif tool_name == "sub_agent":
        first_line = output.split("\n")[0][:100]
        return f"[Summary: sub_agent → {first_line}]"

    return f"[Summary: {tool_name} — {len(output)} chars]"


# ═══════════════════════════════════════════════════════════════
# AP-2: Compression Layer Decision Tree
# ═══════════════════════════════════════════════════════════════
#
# Nanobot uses 5 compression layers, each with a distinct trigger:
#
#  Layer 0 — P44 Inline Truncation (per-tool, immediate)
#    Trigger: tool result > max_chars threshold at emission time
#    Effect:  head+tail truncation with "[N chars omitted]"
#    Scope:   single tool result message, applied in _build_tool_summary()
#
#  Layer 1 — AP-2 Context Collapse (per-turn, pre-LLM)
#    Trigger: ≥3 consecutive old read-only tool results from same turn
#    Effect:  collapses into 1 summary line: "[Collapsed N reads: ...]"
#    Scope:   consecutive file_read/grep_search/file_list/find_by_name
#    NEW:     Added by AP-2 audit fix
#
#  Layer 2 — P3 MicroCompact Age Decay (per-turn, pre-LLM)
#    Trigger: turn > _MC_AGE_DECAY_TURNS AND message older than threshold
#    Effect:  re-truncates old tool results with halved limits
#    Scope:   all tool messages, skips high-value content
#
#  Layer 3 — U4 Cache-Aware Hints (per-turn, pre-LLM, non-destructive)
#    Trigger: cache-aware mode enabled
#    Effect:  inserts system hint about stale results (no mutation)
#    Scope:   entire message history
#    Note:    mutually exclusive with Layer 2 (U4 XOR P3)
#
#  Layer 4 — P5/P10 AutoCompact (periodic, heavy)
#    Trigger: budget.should_compact() returns True (>80% ceiling)
#    Effect:  LLM-based summarization of entire conversation
#    Scope:   all messages, replaces history with summary
#
# Execution order per turn: Layer 1 → Layer 2/3 → Layer 4 (if needed)
# Layer 0 runs at tool result emission time, not per-turn.
# ═══════════════════════════════════════════════════════════════

# Collapsible read-only tool names for context collapse
_COLLAPSE_TOOL_NAMES = frozenset({"file_read", "grep_search", "file_list", "find_by_name"})

# Minimum number of consecutive read results to trigger collapse
_COLLAPSE_MIN_SEQUENCE = 3

# Only collapse results older than this many turns
_COLLAPSE_AGE_TURNS = 3


def _collapse_read_search_sequences(
    messages: List[Dict[str, Any]],
    current_turn: int,
) -> int:
    """AP-2: Collapse consecutive old read-only tool results into summary lines.

    Claw-style "context collapse": when the model has done a burst of file_read
    and grep_search calls in an older turn, the individual verbose results add
    little value.  This function finds runs of ≥3 consecutive collapsible tool
    messages from the same turn, and replaces them with a single compact summary.

    Preserves:
    - Recent results (within _COLLAPSE_AGE_TURNS of current turn)
    - Non-tool messages (assistant, user, system)
    - High-value content (checked via _is_high_value_content)
    - The last result in each sequence (most likely to be the "final answer")

    Returns the number of messages that were collapsed.
    """
    if current_turn < _COLLAPSE_AGE_TURNS + 1:
        return 0

    collapsed_count = 0
    i = 0
    while i < len(messages):
        msg = messages[i]
        # Only consider tool messages
        if msg.get("role") != "tool":
            i += 1
            continue

        msg_turn = msg.get("_turn", 0)
        tool_name = msg.get("_tool_name", "")

        # Skip recent or non-collapsible
        if (current_turn - msg_turn) < _COLLAPSE_AGE_TURNS or tool_name not in _COLLAPSE_TOOL_NAMES:
            i += 1
            continue

        # Find the run of consecutive collapsible tool messages from same turn
        run_start = i
        run_indices = [i]
        j = i + 1
        while j < len(messages):
            next_msg = messages[j]
            if (next_msg.get("role") == "tool"
                    and next_msg.get("_tool_name", "") in _COLLAPSE_TOOL_NAMES
                    and next_msg.get("_turn", 0) == msg_turn):
                run_indices.append(j)
                j += 1
            else:
                break

        if len(run_indices) < _COLLAPSE_MIN_SEQUENCE:
            i = j
            continue

        # Build collapse summary from the run (keep last result intact)
        summaries = []
        indices_to_remove = []
        for idx in run_indices[:-1]:  # Keep last one
            m = messages[idx]
            content = m.get("content", "")
            # Skip if already collapsed or high-value
            if content.startswith("[Collapsed") or _is_high_value_content(content):
                continue
            tn = m.get("_tool_name", "unknown")
            # Extract first meaningful line for summary
            first_line = ""
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("[Summary:") and not stripped.startswith("[File:"):
                    first_line = stripped[:80]
                    break
            summaries.append(f"  - {tn}: {first_line}" if first_line else f"  - {tn}: ({len(content)} chars)")
            indices_to_remove.append(idx)

        if len(indices_to_remove) < _COLLAPSE_MIN_SEQUENCE - 1:
            i = j
            continue

        # Replace first message in run with collapse summary
        collapse_msg = (
            f"[Collapsed {len(indices_to_remove)} read-only tool results from turn {msg_turn}]\n"
            + "\n".join(summaries[:10])
        )
        if len(summaries) > 10:
            collapse_msg += f"\n  ... and {len(summaries) - 10} more"

        messages[run_start] = {
            "role": "tool",
            "content": collapse_msg,
            "tool_call_id": messages[run_start].get("tool_call_id", "collapsed"),
            "_turn": msg_turn,
            "_tool_name": "collapsed_reads",
            "_collapsed": True,
        }

        # Remove the other messages (reverse order to preserve indices)
        for idx in sorted(indices_to_remove[1:], reverse=True):
            if idx < len(messages):
                messages.pop(idx)

        collapsed_count += len(indices_to_remove) - 1
        # Re-scan from the collapse point
        i = run_start + 1

    if collapsed_count:
        remaining_tool_msgs = sum(1 for m in messages if m.get("role") == "tool")
        logger.info(
            "[AP-2/Collapse] turn=%d collapsed=%d remaining_tool_msgs=%d total_msgs=%d",
            current_turn, collapsed_count, remaining_tool_msgs, len(messages),
        )

    return collapsed_count


def _micro_compact_old_messages(
    messages: List[Dict[str, Any]],
    current_turn: int,
) -> int:
    """Second-pass slimming of old tool results in the message history.

    Before auto-compact triggers, this does a lighter-weight pass: re-truncate
    tool messages older than _MC_AGE_DECAY_TURNS using halved limits.

    Returns the number of messages that were re-truncated.
    """
    trimmed = 0
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        msg_turn = msg.get("_turn", 0)
        content = msg.get("content", "")
        if not content or content == _TOOL_RESULT_CLEARED_MSG:
            continue
        # A1: Protect high-value content from age-decay truncation
        if _is_high_value_content(content):
            continue
        tool_name = msg.get("_tool_name", "")
        max_c, head_c, tail_c = _get_mc_limits(tool_name, current_turn, msg_turn)
        if len(content) <= max_c:
            continue

        # Preserve summary line
        summary_prefix = ""
        if content.startswith("[Summary:"):
            first_nl = content.find("\n")
            if first_nl != -1:
                summary_prefix = content[:first_nl + 1]
                content = content[first_nl + 1:]

        head = content[:head_c]
        tail = content[-tail_c:] if tail_c else ""
        total = len(content)
        msg["content"] = (
            f"{summary_prefix}{head}\n\n"
            f"... [{total - head_c - tail_c} chars omitted (aged)] ...\n\n"
            f"{tail}"
        )
        trimmed += 1

    if trimmed:
        logger.info(f"[MicroCompact] Re-truncated {trimmed} old tool result(s) at turn {current_turn}")
    return trimmed


# ═══════════════════════════════════════════════════════════════
# P7: Table Fill Detection
# ═══════════════════════════════════════════════════════════════

def _detect_table_fill_request(user_message: str) -> Optional[str]:
    """Detect if user provided a table/list with empty cells to fill (P7).

    Small models tend to fabricate data when asked to fill tables.
    By detecting the pattern and injecting a hint, we force the model
    to use tools first.

    Patterns detected:
      A) Tab-separated header + bare item rows (e.g. "Name\\tValue\\n...")
      B) Markdown pipe table with empty cells
      C) Numbered/bulleted list with blank placeholders
    """
    l = user_message.strip()
    if not l:
        return None

    lines = l.split("\n")

    # Pattern A: Tab-separated header + data rows
    header = lines[0] if lines else ""
    header_cols = [c for c in header.split("\t") if c.strip()]
    if len(header_cols) >= 2:
        bare_items = 0
        for line in lines[1:]:
            cols = line.split("\t")
            if len(cols) >= 2 and any(not c.strip() for c in cols[1:]):
                bare_items += 1
        if bare_items >= 2:
            col_names = ", ".join(header_cols)
            return (
                f"[TABLE FILL] The user provided a table with columns {col_names}"
                f".\nYou MUST use tools (grep_search, file_read) to look up the "
                f"ACTUAL values for each column BEFORE writing the table. Do NOT "
                f"guess or fabricate numbers.\nIf the values come from source code "
                f"(e.g. config dicts, constants), read the relevant file first with file_read."
            )

    # Pattern B: Markdown pipe table with empty cells
    pipe_lines = [ln for ln in lines if "|" in ln and not ln.strip().startswith("```")]
    if len(pipe_lines) >= 3:  # header + separator + at least one data line
        data_lines = pipe_lines[2:]  # skip header and separator
        empty_count = 0
        for dl in data_lines:
            cells = dl.split("|")
            inner_cells = [c.strip() for c in cells[1:-1]] if len(cells) > 2 else [c.strip() for c in cells]
            if any(not c for c in inner_cells):
                empty_count += 1
        if empty_count >= 1:
            header_cols = [c.strip() for c in pipe_lines[0].split("|")[1:-1]]
            col_names = ", ".join(c for c in header_cols if c)
            return (
                f"[TABLE FILL] The user provided a Markdown table with {empty_count}"
                f" rows having empty cells. Use tools (file_read, grep_search) to "
                f"look up actual values. Write 'N/A' for anything you cannot verify "
                f"from tool results."
            )

    # Pattern C: List of bare code identifiers (function names, variables, etc.)
    # Detect lines that are bare identifiers: underscore-prefixed, camelCase, or snake_case
    bare_id_count = 0
    for line in lines:
        stripped = line.strip()
        # Skip header/question lines
        if stripped.endswith((":", "：", "?", "？")) or not stripped:
            continue
        # Bare identifier: starts with _ or is snake_case/camelCase without spaces
        if re.match(r'^_?\w+$', stripped) and ('_' in stripped or stripped[0] == '_'):
            bare_id_count += 1
        # Numbered/bulleted bare identifier
        elif re.match(r'^[\d]+[.)]\s+_?\w+\s*$', stripped):
            bare_id_count += 1
        elif re.match(r'^[-*]\s+_?\w+\s*$', stripped):
            bare_id_count += 1
    if bare_id_count >= 3:
        return (
            f"The user listed {bare_id_count} code identifiers and expects data for each. "
            f"Use tools (file_read, grep_search) to look up actual values from source code. Do NOT guess."
        )

    return None


def _build_completeness_nudge(
    user_message: str,
    tools_used_this_turn: list,
    turn: int,
) -> Optional[str]:
    """Detect numbered sub-tasks in the user's question and nudge the model
    to address any parts that likely haven't been covered yet.

    Returns a system hint string or None.
    Only active for turns 2-6 (turn 1 is too early, turn 7+ is too late).
    """
    # Only nudge between turns 2 and 6
    if turn < 2 or turn > 6:
        return None

    # Count numbered tasks  (e.g. "1. ...\n2. ...\n3. ...")
    numbered = re.findall(r'^\s*(\d+)[.)]\s+(.+)', user_message, re.MULTILINE)
    if len(numbered) < 2:
        return None

    # Build a task→tool mapping suggestion
    _TASK_TOOL_HINTS = {
        "读": "file_read", "read": "file_read",
        "搜": "grep_search", "search": "grep_search", "find": "grep_search",
        "统计": "grep_search", "count": "grep_search",
        "列出": "file_list", "list": "file_list",
        "运行": "shell_execute", "run": "shell_execute", "execute": "shell_execute",
    }
    tools_used_set = set(tools_used_this_turn)
    parts = []
    for num, text in numbered:
        task_text = text.strip()
        hint = ""
        for pattern, tool in _TASK_TOOL_HINTS.items():
            if pattern in task_text.lower():
                if tool in tools_used_set:
                    hint = f" → {tool} already called"
                else:
                    hint = f" → {tool}"
                break
        parts.append(f"  {num}. {task_text[:80]}{hint}")

    # Check if all suggested tools are done
    all_done = all(
        any(tool in tools_used_set for tool in _TASK_TOOL_HINTS.values()
            if any(p in text.strip().lower() for p in [k for k, v in _TASK_TOOL_HINTS.items() if v == tool]))
        for _, text in numbered
        if any(p in text.strip().lower() for p in _TASK_TOOL_HINTS.keys())
    )

    parts_str = "\n".join(parts)
    return (
        f"The user's request has {len(numbered)} sub-tasks. Suggested tool mapping:\n"
        f"{parts_str}"
        f"\nCall ALL read-only tools (file_list, file_read, grep_search) in your "
        f"FIRST turn — they run in parallel.\n"
        f"For the summary table, read each tool file to get ALIASES before writing the table."
    )


# ═══════════════════════════════════════════════════════════════
# Main agentic loop
# ═══════════════════════════════════════════════════════════════

async def agentic_chat_stream(
    user_message: str,
    env: Dict[str, str],
    session_id: str,
    workspace: Path,
    stream_stats: Optional[Dict[str, Any]] = None,
    is_stop_fn: Optional[callable] = None,
    max_turns: int = 0,
    agent_system_prompt: Optional[str] = None,
    allowed_tools: Optional[set] = None,
    mode: str = "code",
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Main agentic loop. Yields SSE-compatible event dicts.

    The loop calls the LLM with tool definitions, streams text chunks,
    detects tool_calls, executes them, injects results, and loops until
    the model stops requesting tools or max_turns is reached.

    Args:
        user_message:  The full user prompt (may include history context)
        env:           Environment dict with model/API config
        session_id:    Session identifier
        workspace:     Working directory for tool execution
        stream_stats:  Mutable dict for tracking token usage
        is_stop_fn:    Callable returning True if user requested stop
        max_turns:     Max agentic turns (0 = use env default)
        agent_system_prompt:  P60: Override system prompt for specialized agents
        allowed_tools:        P60: Set of allowed tool names (None = all tools)
        mode:           Code/Ask/Plan mode — filters available tools
    """
    # Security: warn if Python optimization mode disables assert-based guards (R14)
    # Once-per-process guard prevents log flooding in long-running workers.
    # MAINTENANCE: _o_flag_warned is a function attribute (not a global) to keep
    # the guard co-located with its check.  Do NOT remove during refactoring —
    # it ensures the warning fires exactly once per process lifetime.
    if sys.flags.optimize and not getattr(agentic_chat_stream, "_o_flag_warned", False):
        agentic_chat_stream._o_flag_warned = True  # type: ignore[attr-defined]
        logger.warning(
            "[SECURITY] Python running with -O flag — assert-based security guards "
            "(R14 main-thread checks) are DISABLED. See SECURITY.md R14."
        )

    if max_turns <= 0:
        max_turns = int(env.get("NANOBOT_MAX_TOOL_ITERATIONS", "10"))

    mode = (mode or "code").strip().lower()
    if mode not in {"code", "ask", "plan"}:
        logger.warning(f"[Mode] Unknown mode '{mode}', falling back to code")
        mode = "code"

    # ── Mode-based tool filtering ──
    # Code: all tools (read + write + execute)
    # Ask:  read-only tools only (no file_edit, file_write, shell_execute)
    # Plan: read-only + shell_execute (for verification), but file_edit/file_write create plan-only change sets
    _READONLY_TOOL_NAMES = {"file_read", "file_list", "grep_search", "find_by_name",
                            "web_fetch", "web_search", "code_intel", "memory", "todo_manage"}
    if mode == "ask":
        effective_allowed = _READONLY_TOOL_NAMES
        logger.info(f"[Mode:Ask] Restricted to read-only tools: {sorted(effective_allowed)}")
    elif mode == "plan":
        # Plan mode: can read + execute verification, but edits are plan-only (not written to disk)
        effective_allowed = _READONLY_TOOL_NAMES | {"shell_execute", "python_execute", "sub_agent"}
        logger.info(f"[Mode:Plan] Tools: read-only + verify (no direct file writes): {sorted(effective_allowed)}")
    else:
        effective_allowed = None  # code mode: all tools

    if effective_allowed is not None:
        # Merge with any existing allowed_tools (intersection)
        if allowed_tools is not None:
            effective_allowed = effective_allowed & allowed_tools
        allowed_tools = effective_allowed
    mode_meta = {
        "code": {
            "label": "Code",
            "banner": "Code mode: read, write, and execute tools are available.",
            "frontend_message": "Code 模式：可读写代码和执行命令",
        },
        "ask": {
            "label": "Ask",
            "banner": "Ask mode: use read-only tools only. Do not write files or execute commands.",
            "frontend_message": "Ask 模式：只读，不会修改文件",
        },
        "plan": {
            "label": "Plan",
            "banner": "Plan mode: focus on exploration and planning. Do not write files directly; produce a plan first.",
            "frontend_message": "Plan 模式：规划变更但不实施",
        },
    }
    if mode not in mode_meta:
        logger.warning(f"[Mode] Unknown mode '{mode}', falling back to code")
        mode = "code"
    mode_config = mode_meta[mode]

    # ── P21: Static/Dynamic prompt split for KV cache optimization ──
    # P60: If agent_system_prompt is provided, use it instead of default static prompt
    if agent_system_prompt:
        static_prompt = agent_system_prompt
    else:
        static_prompt = _get_static_system_prompt()

    # P16: Auto-detect project type for context injection
    # P19: Detect git branch/status/commits
    project_stack = _detect_workspace_context(workspace)
    git_context = _detect_git_context(workspace)
    workspace_info = (
        f"Current workspace directory: {workspace}\n"
        f"All relative tool paths are resolved against this directory.\n"
        f"IMPORTANT: When the user mentions a filename like 'foo.py', the file may be in a subdirectory. "
        f"Use the full path (e.g. '{workspace}/web_ui/foo.py') or a relative path from workspace "
        f"(e.g. 'web_ui/foo.py'). Do NOT pass bare filenames without a directory."
    )
    if project_stack:
        workspace_info += f"\n{project_stack}"
    if git_context:
        workspace_info += f"\n{git_context}"
    workspace_info += (
        f"\n\n[ACTIVE MODE] {mode_config['label']}\n"
        f"{mode_config['banner']}"
    )
    custom_instructions = _load_nanobot_md(workspace)
    language = env.get("NANOBOT_LANGUAGE", "auto")

    # RAG context injection
    rag_context = _build_rag_context(user_message)
    if rag_context:
        logger.info(f"[RAG] Injected {len(rag_context)} chars of retrieved context")

    # P20: Load previous session summary for cross-session memory
    prev_summary = _load_previous_summary(workspace, session_id)

    # P22: Time-based microcompact detection
    _last_response_file = workspace / ".nanobot_state" / session_id / "last_response_file"
    _time_gap_minutes = _get_time_gap_minutes(_last_response_file)

    # P31: Reset file read session tracker
    try:
        from tools.file_read import reset_session_read_tracker
        reset_session_read_tracker()
    except ImportError:
        pass

    # Phase 1: prepare the session task state without clearing prior turns
    try:
        set_task_context(session_id, workspace)
    except Exception as e:
        logger.debug(f"[TaskStore] set_task_context failed: {e}")

    # A2/A3: Reset context engineering session trackers
    _reset_session_file_reads()
    _reset_session_failures()

    # U1: Reset session memory state
    try:
        from session_memory import reset_session_state as _reset_sm, get_session_state as _get_sm
        _reset_sm(session_id)
        _session_mem = _get_sm(session_id)
    except ImportError:
        _session_mem = None

    # U3: Reset background tasks from previous session
    try:
        from tools.sub_agent import reset_background_tasks as _reset_bg
        _reset_bg(session_id)
    except ImportError:
        pass

    # P2: Reset agent pool from previous session
    try:
        reset_agent_pool(session_id)
    except Exception:
        pass

    # U8: Create per-session scratchpad directory
    _scratchpad_dir = workspace / ".scratchpad" / session_id
    try:
        _scratchpad_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[U8] Scratchpad ready: {_scratchpad_dir}")
    except Exception as _e_sp:
        logger.warning(f"[U8] Could not create scratchpad: {_e_sp}")
        _scratchpad_dir = None

    # P91: Build memory prompt + recall relevant memories
    _memory_context = ""
    try:
        from memory.memory_prompts import build_memory_prompt, build_memory_context_for_query
        from memory.memory_manager import get_memory_dir
        _mem_dir = get_memory_dir(workspace)
        _memory_prompt = build_memory_prompt(_mem_dir)
        _recalled = build_memory_context_for_query(_mem_dir, user_message, max_memories=5)
        _memory_context = _memory_prompt
        if _recalled:
            _memory_context += "\n\n" + _recalled
    except Exception as e:
        logger.warning(f"[P91] Memory prompt build failed: {e}")

    # P97d: Build skill awareness prompt
    _skill_context = ""
    try:
        from skills import build_skill_system_prompt
        _skill_context = build_skill_system_prompt()
    except Exception as e:
        logger.warning(f"[P97] Skill prompt build failed: {e}")

    # A5: Generate lightweight repo map for context orientation
    _repo_map = ""
    try:
        _repo_map = _get_repo_map(workspace, current_turn=0)
        if _repo_map:
            logger.info(f"[A5] Generated repo map ({len(_repo_map)} chars)")
    except Exception as e:
        logger.debug(f"[A5] Repo map generation failed: {e}")

    # D3: Build active files workbench summary for context injection
    _active_files_summary = _get_active_files_summary(workspace=workspace, max_files=6)
    if _active_files_summary:
        logger.info(f"[D3] Active files summary: {len(_SESSION_ACTIVE_FILES)} files tracked")

    # MCP: Fetch resource context from connected MCP servers (once per session)
    _mcp_resources_ctx = ""
    try:
        from tools.mcp_client import get_mcp_manager as _get_mcp_mgr
        _mcp_mgr = _get_mcp_mgr()
        if _mcp_mgr.get_resource_count() > 0:
            _mcp_resources_ctx = await _mcp_mgr.read_all_resources_content(max_total_chars=4000)
    except Exception as _mcp_err:
        logger.debug(f"[MCP] Resource fetch skipped: {_mcp_err}")

    # Build dynamic context block (injected as user message, not system prompt)
    dynamic_ctx = build_dynamic_context(
        language=language,
        workspace_info=workspace_info,
        custom_instructions=custom_instructions,
        rag_context=rag_context,
        prev_summary=prev_summary,
        mcp_resources_context=_mcp_resources_ctx,
        plan_mode=(mode == "plan"),
    )

    # Phase 1: inject current task context so the model can see lifecycle state
    _task_context_summary = ""
    try:
        _root_task = ensure_root_task_for_message(
            workspace,
            session_id,
            user_message,
            metadata={"source": "agentic_loop", "language": language},
        )
        _task_context_summary = get_current_task_summary(session_id, workspace)
        logger.info("[TaskStore] Root task ready: %s (%s)", _root_task.id, _root_task.state)
    except Exception as e:
        logger.warning(f"[TaskStore] Failed to prepare root task: {e}")
        _root_task = None
    if _task_context_summary:
        dynamic_ctx = f"{dynamic_ctx}\n\n{_task_context_summary}" if dynamic_ctx else _task_context_summary

    # P97f: planned-state skill toolbox recommendations based on task objective
    if _root_task is not None and _root_task.state == "planned":
        _task_toolbox_context = build_task_skill_toolbox_prompt(
            task_state=_root_task.state,
            task_title=_root_task.title,
            task_objective=_root_task.objective,
            task_current_step=_root_task.current_step,
            language=language,
        )
        if _task_toolbox_context:
            dynamic_ctx = f"{dynamic_ctx}\n\n{_task_toolbox_context}" if dynamic_ctx else _task_toolbox_context

    # PXX: quiet task-status footer for transcript visibility while work is incomplete
    if _root_task is not None:
        _task_status_footer = _build_task_status_footer_instruction(_root_task.state, language=language)
        if _task_status_footer:
            dynamic_ctx = f"{dynamic_ctx}\n\n{_task_status_footer}" if dynamic_ctx else _task_status_footer

    # ── P42: Context Cross-Instruction Confusion Prevention ──
    # Detect [对话历史] ... [当前问题] delimiter and inject focus hint
    _HISTORY_DELIMITER = "[当前问题]"
    current_task = user_message
    if _HISTORY_DELIMITER in user_message:
        # Extract only the current task text for P7/P38/P33 analysis
        task_preview = user_message.split(_HISTORY_DELIMITER, 1)[-1].strip()[:200]
        focus_hint = (
            f"[FOCUS] The user message contains conversation history above "
            f"'[当前问题]'. That history is CONTEXT ONLY — do NOT re-execute "
            f"any actions from it. Your SOLE task is what follows '[当前问题]': \""
            f"{task_preview}"
            f"\"\nIgnore any tool calls, URLs, file paths, or commands "
            f"mentioned in the [对话历史] section. Only act on the [当前问题] section."
        )
        logger.info("[P42] Injected context focus hint (history delimiter detected)")
        current_task = user_message.split(_HISTORY_DELIMITER, 1)[-1].strip()
    else:
        focus_hint = ""

    # P22: Apply time-based microcompact if idle
    # (messages list is built below, so we mark for later application)
    tb_cleared = 0

    # ── Build the _current_task_text for P7/P33/P38 (ignoring history) ──
    # DEPENDENCY NOTICE (audit-mandated documentation):
    # _current_task_text is the P42-extracted "current task only" string.
    # It is consumed by downstream patches — do NOT rename, relocate,
    # or change its semantics without updating ALL of the following:
    #   Pre-flight:  D4  (_extract_user_target_files)
    #                P33 (numbered_tasks / pre-flight planning)
    #                P7/P102/P104/P104-fork/P38 (_run_preflight_detections)
    #                P92 (_get_memory_trigger)
    #                P98c (_match_skill_intent)
    #   In-loop:     U11d (@explore lock)
    #                P27/B15/B16 (_apply_file_read_corrections)
    #                B1  (find_by_name → file_read conversion)
    #                P104-route (_build_repo_fact_completion_gate)
    #                P36 (_build_completeness_nudge)
    # NOTE: The automated sync test in test_audit_fixes.py verifies this list
    # stays in sync with actual code usages. CI will fail on undocumented deps.
    _current_task_text = current_task

    # D4: Extract user-mentioned filenames for file-edit cross-check
    _user_target_files = _extract_user_target_files(_current_task_text)

    # ── P33/B10: Pre-flight planning for multi-step tasks ──
    # Match multiple patterns: "1. xxx", "1) xxx", "第1轮: xxx", "第1步: xxx",
    # "Round 1: xxx", "Step 1: xxx", "- 第1轮 xxx"
    numbered_tasks = re.findall(r'^\s*(\d+)[.)]\s+(.+)', _current_task_text, re.MULTILINE)
    if not numbered_tasks:
        numbered_tasks = re.findall(
            r'(?:^|\n)\s*(?:第\s*(\d+)\s*[轮步回][：:\s]\s*(.+))',
            _current_task_text
        )
    if not numbered_tasks:
        numbered_tasks = re.findall(
            r'(?:^|\n)\s*(?:[Rr]ound|[Ss]tep)\s*(\d+)[：:\s]\s*(.+)',
            _current_task_text
        )
    plan_lines = []
    if len(numbered_tasks) >= 2:
        _tool_keywords = {"读": "file_read", "read": "file_read",
                          "搜": "grep_search", "search": "grep_search",
                          "统计": "grep_search", "count": "grep_search",
                          "列": "file_list", "list": "file_list",
                          "运行": "shell_execute", "run": "shell_execute",
                          "回忆": "(no tool)", "recall": "(no tool)"}
        # B10/B12: Extract explicit file_read parameters per step
        # B12: Also build structured plan for post-generation correction
        _has_file_reads = False
        _b12_file_plan = {}  # path_fragment → {"offset": int|None, "limit": int|None}
        for num, text in numbered_tasks:
            t = text.strip()[:120]
            hint = ""
            for pattern, tool in _tool_keywords.items():
                if pattern in t.lower():
                    hint = f" → {tool}"
                    break
            # B10: Extract file path and offset/limit from step text
            _fr_detail = ""
            if "file_read" in hint:
                _has_file_reads = True
                # Extract filename: look for .py, .js, .ts, .md etc.
                _fn_match = re.search(r'([\w./\-]+\.\w{1,4})', t)
                _fn = _fn_match.group(1) if _fn_match else "?"
                # Extract line range: "800-900行", "lines 800-900", "前100行", "first 100 lines"
                _range_match = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*行?', t)
                _first_match = re.search(r'前\s*(\d+)\s*行', t)
                _first_en_match = re.search(r'first\s+(\d+)\s+lines?', t, re.IGNORECASE)
                _plan_offset = None
                _plan_limit = None
                if _range_match:
                    _plan_offset = int(_range_match.group(1))
                    _plan_limit = int(_range_match.group(2)) - _plan_offset + 1
                    _fr_detail = f" file_read(path={_fn}, offset={_plan_offset}, limit={_plan_limit})"
                elif _first_match:
                    _plan_limit = int(_first_match.group(1))
                    _fr_detail = f" file_read(path={_fn}, limit={_plan_limit})"
                elif _first_en_match:
                    _plan_limit = int(_first_en_match.group(1))
                    _fr_detail = f" file_read(path={_fn}, limit={_plan_limit})"
                elif _is_full_file_request(t):
                    # B15: Semantic full-file detection (replaces hardcoded "全文")
                    _plan_limit = _FULL_FILE_LIMIT
                    _fr_detail = f" file_read(path={_fn}, limit={_plan_limit})"
                else:
                    _fr_detail = f" file_read(path={_fn})"
                # B12: Store structured plan for this file
                if _fn != "?":
                    _b12_file_plan[_fn] = {"offset": _plan_offset, "limit": _plan_limit}
            plan_lines.append(f"  Step {num}. {t}{hint}{_fr_detail}")

        # Build planning message
        planning_msg = (
            f"[TASK STEPS] The user has {len(numbered_tasks)} sequential tasks:\n"
            + "\n".join(plan_lines)
        )
        if _has_file_reads:
            # B10: Explicit parameter warning to prevent cross-step confusion
            planning_msg += (
                "\n\nCRITICAL: Each step references a DIFFERENT file with DIFFERENT parameters. "
                "Do NOT mix parameters between steps. "
                "Match each file_read call to its CORRECT step — check the file name AND offset/limit "
                "shown above before calling."
            )
        planning_msg += (
            "\nCall the read-only tools in parallel (they can run concurrently), "
            "then write your response after gathering ALL data."
        )
        logger.info(f"[P33/B10] Injected pre-flight plan for {len(numbered_tasks)} tasks")
    else:
        planning_msg = ""
        _b12_file_plan = {}

    # P7/P102/P104/P104-fork/P38: Bundled pre-flight detections
    # (decoupling POC — audit-mandated: reduces 5 _current_task_text refs → 1)
    _preflight = _run_preflight_detections(_current_task_text)
    table_hint = _preflight["table_hint"]
    code_summary_hint = _preflight["code_summary_hint"]
    repo_fact_hint = _preflight["repo_fact_hint"]
    _repo_fact_mode = bool(repo_fact_hint)
    _repo_explore_fork = _preflight["repo_explore_fork"]
    mandatory_hint = _preflight["mandatory_hint"]

    # D2/P102: Early defaults for skill mode variables (used by P103 below,
    # re-assigned later in the P101/P102 skill extraction block)
    _skill_mode: str = ""
    _skill_write_policy: str = "allowed"
    _skill_requires_verification: bool = False
    _skill_disallowed_tools: list = []

    # P36/P46/P103: Set parent context for sub_agent tool
    # P103: Include active skill policy so sub-agents inherit enforcement metadata
    _skill_policy_for_sub = {}
    if _skill_mode:
        _skill_policy_for_sub = {
            "mode": _skill_mode,
            "write_policy": _skill_write_policy,
            "requires_verification": _skill_requires_verification,
            "disallowed_tools": _skill_disallowed_tools,
        }
    try:
        from tools.sub_agent import set_parent_context
        set_parent_context(env, workspace, session_id, messages=[],
                           skill_policy=_skill_policy_for_sub, mode=mode)
    except ImportError:
        pass

    # P62: Set session ID for todo_manage tool
    try:
        from tools.todo_manage import set_session_id as _set_todo_session
        _set_todo_session(session_id)
    except ImportError:
        pass

    # ── Assemble messages ──
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": static_prompt},
    ]
    # F1: System-level language reinforcement for local models (llama.cpp/Ollama)
    # Dynamic context has language in user-turn, but local models follow system messages more
    if language and language not in ("auto", "en"):
        _F1_LANG_MAP = {
            "zh": "Chinese (中文)", "ja": "Japanese (日本語)",
            "ko": "Korean (한국어)", "es": "Spanish", "fr": "French",
        }
        _f1_lang_name = _F1_LANG_MAP.get(language, language)
        messages.append({
            "role": "system",
            "content": (
                f"[LANGUAGE RULE] You MUST respond in {_f1_lang_name}. "
                f"All headings, explanations, analysis, and prose MUST be in {_f1_lang_name}. "
                f"Only code, tool names, file paths, and technical identifiers stay in English."
            ),
        })
        logger.info(f"[F1] Injected system-level language instruction: {language}")
    if dynamic_ctx:
        messages.append({"role": "user", "content": dynamic_ctx})
        messages.append({"role": "assistant", "content": "Understood. I have the session context. How can I help?"})
    if focus_hint:
        messages.append({"role": "system", "content": focus_hint})
    messages.append({"role": "user", "content": user_message})

    # Inject planning/table/mandatory hints
    if planning_msg:
        messages.append({"role": "system", "content": planning_msg})
    if table_hint:
        messages.append({"role": "system", "content": table_hint})
    if code_summary_hint:
        messages.append({"role": "system", "content": code_summary_hint})
    if repo_fact_hint:
        messages.append({"role": "system", "content": repo_fact_hint})
    if mandatory_hint:
        messages.append({"role": "system", "content": mandatory_hint})

    # P92: Memory trigger detection (decoupled — explicit param)
    _memory_hint = _get_memory_trigger(_current_task_text)
    if _memory_hint:
        messages.append({"role": "system", "content": _memory_hint})

    # P98c: Skill intent matching (decoupled — explicit param)
    _skill_match = _match_skill_intent(_current_task_text)
    if _skill_match:
        _matched_skill, _confidence = _skill_match
        _skill_hint = (
            f"[SKILL HINT] The user's request matches the /{_matched_skill.name} "
            f"skill (confidence: {_confidence:.1f}). "
            f"Consider suggesting: \"You can use `/{_matched_skill.name}` "
            f"to {_matched_skill.description.lower().rstrip('.')}.\""
        )
        messages.append({"role": "system", "content": _skill_hint})

    # P36/P46/P103: Update sub_agent parent context with actual messages + skill policy
    try:
        from tools.sub_agent import set_parent_context
        set_parent_context(env, workspace, session_id, messages=messages,
                           skill_policy=_skill_policy_for_sub, mode=mode)
    except ImportError:
        pass

    # ── P100c: Skill allowed_tools enforcement ──
    # If the user_message is a skill prompt with SKILL_ALLOWED_TOOLS metadata,
    # extract and enforce tool restrictions for the entire session
    _skill_allowed_tools: list = []
    try:
        from skills import extract_allowed_tools_from_prompt, is_tool_allowed_for_skill
        _skill_allowed_tools = extract_allowed_tools_from_prompt(user_message)
        _is_tool_allowed = lambda tname, at: is_tool_allowed_for_skill(tname, at)
        if _skill_allowed_tools:
            logger.info(f"[P100c] Skill tool restriction active: {_skill_allowed_tools}")
    except Exception:
        _is_tool_allowed = lambda tname, at: True

    # ── P101/P102: Skill mode runtime enforcement ──
    # Extract mode, write_policy, requires_verification, disallowed_tools
    _skill_mode: str = ""
    _skill_write_policy: str = "allowed"
    _skill_requires_verification: bool = False
    _skill_disallowed_tools: list = []  # P102: tools explicitly blocked by skill
    _skill_completion_criteria: list = []  # P102: output requirements
    _pending_verification: bool = False  # set True after writes in requires_verification mode
    _has_run_verification: bool = False  # set True after shell_execute runs tests/lint
    _write_count_since_verify: int = 0  # P102: count writes needing verification
    try:
        from skills import extract_skill_overrides_from_prompt, get_skill_metadata
        _skill_overrides = extract_skill_overrides_from_prompt(user_message)
        _skill_mode = _skill_overrides.get("mode") or ""
        _skill_write_policy = _skill_overrides.get("write_policy", "allowed")
        _skill_requires_verification = _skill_overrides.get("requires_verification", False)
        if _skill_mode:
            # Get full metadata including disallowed_tools and completion_criteria
            _meta = get_skill_metadata(_skill_mode)
            if _meta:
                _skill_disallowed_tools = _meta.get("disallowed_tools", [])
                _skill_completion_criteria = _meta.get("completion_criteria", [])
            logger.info(f"[P102] Skill mode active: mode={_skill_mode}, "
                        f"write_policy={_skill_write_policy}, "
                        f"requires_verification={_skill_requires_verification}, "
                        f"disallowed={_skill_disallowed_tools}")
    except Exception:
        pass

    # ── P100e: Clear stale invoked skills at session start ──
    try:
        from skills import clear_invoked_skills
        clear_invoked_skills()
    except Exception:
        pass

    # ── State trackers ──
    tools_used: list = []
    total_tool_calls = 0
    _recent_tool_calls: list = []   # for duplicate detection
    _consecutive_empty_searches = 0
    _MAX_EMPTY_SEARCHES = 3
    _compact_failures = 0
    _transient_retries = 0  # Structured retry counter for transient/rate-limit/timeout errors
    _cw3_autocompact_failures = 0  # CW3: consecutive auto-compact failures (circuit breaker)
    _cw3_breaker_open_turn = 0       # CW3: turn when breaker opened (for half-open probing)
    _consecutive_tool_only_turns = 0
    _d2_idle_count = 0
    _last_final_answer_payload: Optional[Dict[str, Any]] = None
    _verbose_turns = 0
    _file_read_failed_paths: set = set()  # U12d: track not-found paths for pre-emption
    _p102_criteria_nudges = 0  # P102: rate-limit completion criteria nudges
    _pending_change_sets: Dict[str, Dict[str, Any]] = {}
    _consecutive_tool_failures = 0
    _pending_forced_verify: Optional[Dict[str, Any]] = None
    _cw5_last_warning_state: str = "normal"  # CW5: throttle — only emit on state change

    # States that should not regress to earlier states via routine tool actions
    _NON_REGRESSIBLE_STATES = frozenset({"waiting_approval", "verifying", "completed", "failed", "cancelled"})

    def _maybe_advance_task_state(
        tool_name: str,
        result: Dict[str, Any],
        tool_args: Dict[str, Any],
        pending_change_set_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Advance task state based on tool execution results.

        Priority Rules (in order):
        0. Pending change sets exist → waiting_approval (highest priority, blocks all others)
        1. Non-regressible state guard → skip if already in terminal/non-reversible state
        2. file_read from created → planned (research/understanding phase)
        3. Work execution tools (todo_manage/sub_agent/fork/etc) from created/planned → in_progress
           - If both file_read and work tools execute, created goes directly to in_progress (atomic upgrade)
        4. All children completed from in_progress → verifying (triggers forced verification)
        5. Consecutive failures → blocked (failure handling)

        Returns:
            Always returns a dict with:
            - triggered (bool): whether a state transition happened
            - state (str): new state if triggered, else current state
            - reason (str): reason for the transition
            - _forced_verify (dict|None): verify signal when entering verifying state
        """
        nonlocal _root_task, _consecutive_tool_failures, _pending_forced_verify
        _no_change = {"triggered": False, "state": (_root_task.state if _root_task else ""), "reason": "", "_forced_verify": None}
        if _root_task is None:
            return _no_change

        store = get_task_store(session_id, workspace)
        root = store.get_task(_root_task.id) or _root_task
        current_state = root.state
        _no_change["state"] = current_state
        pending_change_set_ids = pending_change_set_ids or []

        # Work execution tools that indicate active work has started
        _WORK_EXECUTION_TOOLS = {"todo_manage", "sub_agent", "fork", "delegate", "spawn_agent",
                                  "file_edit", "file_write", "shell_execute", "python_execute"}

        def _triggered(new_state: str, reason: str, forced_verify: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            return {"triggered": True, "state": new_state, "reason": reason, "_forced_verify": forced_verify}

        if result.get("success", True):
            _consecutive_tool_failures = 0

            # Priority 0: pending change sets → waiting_approval (highest priority)
            # But do NOT regress from waiting_approval/verifying/terminal states
            if pending_change_set_ids and current_state not in _NON_REGRESSIBLE_STATES:
                try:
                    root = store.transition(
                        root.id,
                        "waiting_approval",
                        approval_required=True,
                        metadata={**dict(root.metadata or {}), "pending_change_set_ids": pending_change_set_ids},
                        source="_maybe_advance_task_state",
                        reason=f"pending change sets: {pending_change_set_ids}",
                    )
                    _root_task = root
                    _pending_forced_verify = None
                    logger.info("[TaskAdvance] %s → waiting_approval (pending change sets: %s)",
                                current_state, pending_change_set_ids)
                    return _triggered("waiting_approval", f"pending change sets: {pending_change_set_ids}")
                except Exception as exc:
                    logger.debug("[TaskAdvance] waiting_approval transition skipped: %s", exc)

            # Rule 1: Non-regressible guard — if already in waiting_approval/verifying/terminal,
            # do NOT allow file_read or other routine tools to push state backward.
            if current_state in _NON_REGRESSIBLE_STATES:
                # Only allow forward transitions from these states, not backward
                # Exception: pending_change_sets already handled above
                logger.debug("[TaskAdvance] State %s is non-regressible, skipping %s-driven transition",
                             current_state, tool_name)
                return _no_change

            # Priority 2: Work execution tools from created/planned → in_progress
            # This handles atomic upgrade: created → in_progress (skipping planned) when
            # both file_read and work tools are called in the same batch
            if current_state in {"created", "planned"} and tool_name in _WORK_EXECUTION_TOOLS:
                try:
                    root = store.transition(
                        root.id,
                        "in_progress",
                        source="_maybe_advance_task_state",
                        reason=f"triggered by {tool_name}",
                    )
                    _root_task = root
                    logger.info("[TaskAdvance] %s → in_progress (triggered by %s)", current_state, tool_name)
                    return _triggered("in_progress", f"triggered by {tool_name}")
                except Exception as exc:
                    logger.debug("[TaskAdvance] %s→in_progress transition skipped: %s", current_state, exc)
                return _no_change

            # Priority 2a: file_read from created → planned (only if not already handled above)
            # Note: If file_read and work tools execute together, created → in_progress takes priority
            if current_state == "created" and tool_name == "file_read":
                try:
                    root = store.transition(
                        root.id,
                        "planned",
                        source="_maybe_advance_task_state",
                        reason="triggered by file_read",
                    )
                    _root_task = root
                    logger.info("[TaskAdvance] created → planned (triggered by file_read)")
                    return _triggered("planned", "triggered by file_read")
                except Exception as exc:
                    logger.debug("[TaskAdvance] created→planned transition skipped: %s", exc)
                return _no_change

            # Fallback for planned + work tools (if somehow missed above - defensive)
            if current_state == "planned" and tool_name in _WORK_EXECUTION_TOOLS:
                try:
                    root = store.transition(
                        root.id,
                        "in_progress",
                        source="_maybe_advance_task_state",
                        reason=f"triggered by {tool_name}",
                    )
                    _root_task = root
                    logger.info("[TaskAdvance] planned → in_progress (triggered by %s)", tool_name)
                    return _triggered("in_progress", f"triggered by {tool_name}")
                except Exception as exc:
                    logger.debug("[TaskAdvance] planned→in_progress transition skipped: %s", exc)
                return _no_change

            if current_state == "in_progress":
                try:
                    latest_root = store.get_task(root.id) or root
                    child_tasks = store.get_children(latest_root.id)
                    if child_tasks and all(child.state == "completed" for child in child_tasks):
                        root = store.transition(
                            root.id,
                            "verifying",
                            verification_required=True,
                            source="_maybe_advance_task_state",
                            reason="triggered by all children completed",
                        )
                        _root_task = root
                        _pending_forced_verify = {
                            "root_task_id": root.id,
                            "reason": "all_children_completed",
                        }
                        logger.info("[TaskAdvance] in_progress → verifying (all children completed; forcing verify sub-agent)")
                        return _triggered("verifying", "all children completed", _pending_forced_verify)
                except Exception as exc:
                    logger.debug("[TaskAdvance] in_progress→verifying check skipped: %s", exc)
            return _no_change

        _consecutive_tool_failures += 1
        if _consecutive_tool_failures > 2:
            _block_reason = str(result.get("error") or f"Tool {tool_name} failed repeatedly")
            try:
                root = store.transition(
                    root.id,
                    "blocked",
                    blocked_reason=_block_reason,
                    source="_maybe_advance_task_state",
                    reason=_block_reason,
                )
                _root_task = root
                _pending_forced_verify = None
                logger.info("[TaskAdvance] %s → blocked (%d consecutive failures, last: %s)",
                            current_state, _consecutive_tool_failures, tool_name)
                return _triggered("blocked", _block_reason)
            except Exception as exc:
                logger.debug("[TaskAdvance] blocked transition skipped: %s", exc)
        return _no_change

    def _build_task_update_payload() -> Dict[str, Any]:
        """Build a task_update SSE payload from current TaskStore state.

        Returns a dict matching the format expected by the frontend's
        normalizeTaskUpdatePayload (same shape as todo_manage's _task_update).
        """
        if _root_task is None:
            return {"root_task": {}, "tasks": []}
        try:
            store = get_task_store(session_id, workspace)
            root = store.get_task(_root_task.id)
            if root is None:
                return {"root_task": _root_task.to_dict(), "tasks": []}
            children = store.get_children(root.id)
            return {
                "root_task": root.to_dict(),
                "tasks": [child.to_dict() for child in children],
            }
        except Exception as exc:
            logger.debug("[TaskAdvance] _build_task_update_payload failed: %s", exc)
            return {"root_task": _root_task.to_dict() if _root_task else {}, "tasks": []}

    def _parse_verify_verdict(output: str) -> tuple[str, str]:
        text = str(output or "").strip()
        match = re.search(r"VERDICT:\s*(PASS|FAIL|PARTIAL)\b", text, re.IGNORECASE)
        verdict = match.group(1).upper() if match else ""
        reason = text
        if match:
            tail = text[match.end():].strip(" \n:-")
            if tail:
                reason = tail
        return verdict, reason

    def _record_change_set_state(change_set: Optional[Dict[str, Any]]) -> str:
        if not change_set:
            return ""
        change_set_id = str(change_set.get("id", "") or "").strip()
        if not change_set_id:
            return ""
        status = str(change_set.get("status", "") or "").strip().lower()
        logger.info(
            "[ApprovalDiag] record_change_set session=%s id=%s status=%s type=%s files=%s",
            session_id,
            change_set_id,
            status,
            str(change_set.get("type", "") or ""),
            len(change_set.get("files", []) or []),
        )
        if status == "pending":
            _pending_change_sets[change_set_id] = dict(change_set)
        elif status in ("applied", "rejected", "reverted"):
            _pending_change_sets.pop(change_set_id, None)
        logger.info(
            "[ApprovalDiag] pending_cache session=%s ids=%s",
            session_id,
            sorted(_pending_change_sets.keys()),
        )
        return change_set_id

    async def _run_forced_verify_subagent(turn: int, trigger: Optional[Dict[str, Any]] = None):
        """Force a verify sub-agent when the main task is in verifying state.

        This is an async generator that emits the same SSE-style events the
        normal tool execution path would produce, so the UI stays consistent.
        """
        nonlocal _root_task, _pending_forced_verify, total_tool_calls
        if _root_task is None:
            return

        store = get_task_store(session_id, workspace)
        root = store.get_task(_root_task.id) or _root_task
        if root.state != "verifying":
            return

        verify_context = store.build_context_summary(max_children=10)
        verify_task = (
            "Verify the current task implementation and return an explicit verdict.\n"
            f"Root task ID: {root.id}\n"
            f"Root title: {root.title}\n"
            f"Objective: {root.objective or root.title}\n"
            f"Current state: {root.state}\n"
            f"Trigger: {trigger.get('reason', 'manual') if trigger else 'manual'}\n\n"
            f"Task context:\n{verify_context}\n\n"
            "You MUST end with one of these exact verdicts: VERDICT: PASS, VERDICT: FAIL, or VERDICT: PARTIAL."
        )
        verify_args = {
            "task": verify_task,
            "agent_type": "verify",
            "inherit_context": True,
            "max_turns": 8,
        }

        verify_start = time.time()
        tool_call_id = f"verify-{root.id}"
        yield {
            "type": "tool_start",
            "name": "sub_agent",
            "arguments": {"agent_type": "verify", "task": verify_task[:120], "inherit_context": True},
            "turn": turn,
        }
        yield {
            "type": "sub_agent_start",
            "agent_type": "verify",
            "task_preview": verify_task[:100],
            "turn": turn,
        }

        result = await execute_tool_async("sub_agent", verify_args, workspace)
        elapsed = time.time() - verify_start

        yield {
            "type": "sub_agent_end",
            "agent_type": "verify",
            "success": result.get("success", True),
            "elapsed": elapsed,
            "turn": turn,
        }

        tool_content = _postprocess_tool_content("sub_agent", result, turn, turn, workspace, session_id)
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": tool_content,
            "_tool_name": "sub_agent",
            "_turn": turn,
        })
        tools_used.append("sub_agent")
        total_tool_calls += 1

        yield {
            "type": "tool_result",
            "tool_name": "sub_agent",
            "tool_call_id": tool_call_id,
            "content": tool_content,
            "success": result.get("success", True),
            "elapsed": elapsed,
            "dev_annotation": _build_dev_annotation("sub_agent", result, tool_content),
            "change_set": result.get("_change_set"),
        }

        verdict, reason = _parse_verify_verdict(result.get("output", ""))
        verify_reason = reason or result.get("error") or ""
        try:
            if result.get("success", True) and verdict == "PASS":
                updated_root = store.transition(
                    root.id,
                    "completed",
                    result_summary=(verify_reason or "Verification passed")[:500],
                    source="_run_forced_verify_subagent",
                    reason=f"VERDICT: PASS ({verify_reason or 'Verification passed'})",
                )
                _root_task = updated_root
                logger.info("[VerifyGate] VERDICT: PASS → completed for task %s", root.id)
            else:
                fail_reason = verify_reason or result.get("error") or (
                    "Verify sub-agent did not return an explicit PASS/FAIL/PARTIAL verdict"
                )
                updated_root = store.transition(
                    root.id,
                    "failed",
                    blocked_reason=fail_reason[:500],
                    result_summary=(verdict or "UNKNOWN")[:32] + (f": {fail_reason[:450]}" if fail_reason else ""),
                    source="_run_forced_verify_subagent",
                    reason=f"VERDICT: {verdict or 'UNKNOWN'} ({fail_reason})",
                )
                _root_task = updated_root
                logger.info("[VerifyGate] VERDICT: %s → failed for task %s", verdict or "UNKNOWN", root.id)
                try:
                    from task_store import create_child_task

                    create_child_task(
                        workspace,
                        session_id,
                        root.id,
                        title="Plan fixes after verification failure",
                        objective=f"Address verification failure: {fail_reason}",
                        owner="main_agent",
                        priority=root.priority,
                        state="planned",
                        metadata={
                            "source": "verify_failure",
                            "verdict": verdict or "UNKNOWN",
                            "verify_task_id": tool_call_id,
                        },
                    )
                except Exception as exc:
                    logger.debug("[VerifyGate] Failed to create planning child task: %s", exc)
        except Exception as exc:
            logger.debug("[VerifyGate] Failed to apply verify verdict transition: %s", exc)

        _pending_forced_verify = None

    # P5: Token budget tracking
    budget = TokenBudgetTracker(_get_context_ceiling(env))

    # P22: Apply time-based microcompact (skipped when U4 cache-aware mode is on)
    if _time_gap_minutes > 0 and not _U4_CACHE_AWARE:
        tb_cleared = _time_based_micro_compact(messages, _time_gap_minutes)
        if tb_cleared:
            logger.info(f"[P22] Cleared {tb_cleared} old tool results after {round(_time_gap_minutes, 1)}min idle")

    # ═══════════════════════════════════════════════════════════════
    # D2: Emit skill_mode event if skill mode is active (once, before loop)
    # ═══════════════════════════════════════════════════════════════
    if _skill_mode:
        yield {
            "type": "skill_mode",
            "mode": _skill_mode,
            "write_policy": _skill_write_policy,
            "requires_verification": _skill_requires_verification,
            "disallowed_tools": _skill_disallowed_tools,
            "completion_criteria": _skill_completion_criteria,
        }

    # ═══════════════════════════════════════════════════════════════
    # U11d: @explore pre-emption — run quick-path BEFORE LLM call
    # If user typed @explore, try quick-path first (file find / dir list / ext path).
    # If resolved → yield result + suppression, then let model summarize briefly.
    # If not resolved → inject MANDATORY sub_agent hint to prevent model from
    #   handling with direct file_read/file_list (the bypass vector).
    # ═══════════════════════════════════════════════════════════════
    _u11d_explore_preempted = False
    _u11d_explore_task = ""
    if _re_explore_lock.match(_current_task_text.strip()):
        _u11d_explore_task = _re_explore_lock.sub("", _current_task_text.strip()).strip()
        logger.info(f"[U11d] @explore detected, task: {_u11d_explore_task!r}")
        try:
            from tools.sub_agent import _explore_quick_path
            _u11d_qp = await _explore_quick_path(_u11d_explore_task, workspace)
            if _u11d_qp is not None:
                # Quick-path resolved — inject result and suppress elaboration
                _u11d_explore_preempted = True
                _u11d_output = _u11d_qp.get("output", "")
                # Yield the result as tool_result event
                yield {
                    "type": "tool_result",
                    "tool_name": "explore_quick_path",
                    "tool_call_id": "u11d_qp",
                    "content": _u11d_output,
                    "success": True,
                    "elapsed": 0.0,
                }
                # Inject result + strong suppression into messages
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "u11d_qp",
                        "type": "function",
                        "function": {"name": "explore_quick_path", "arguments": "{}"},
                    }],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": "u11d_qp",
                    "content": _u11d_output,
                })
                messages.append({
                    "role": "system",
                    "content": (
                        "[EXPLORE QUICK-PATH RESULT] The query was already answered above. "
                        "Present the result to the user in 1-3 SHORT sentences. Do NOT:"
                        "\n- Re-organize, re-format, or add tables/emoji headers"
                        "\n- Add architecture analysis or module descriptions"
                        "\n- Expand a file list into a categorized breakdown"
                        "\n- Read, list, or search for any files (the answer is complete)"
                        "\nJust relay the answer directly."
                    ),
                })
                logger.info(f"[U11d] Quick-path pre-empted @explore — result injected")
        except Exception as e:
            logger.debug(f"[U11d] Quick-path pre-emption failed: {e}")

        if not _u11d_explore_preempted:
            # Complex task — force model to use sub_agent with explore type
            messages.append({
                "role": "system",
                "content": (
                    f"[MANDATORY TOOL] The user explicitly requested @explore. "
                    f"You MUST call sub_agent with agent_type='explore' and task='{_u11d_explore_task}'. "
                    f"Do NOT handle this yourself with file_read, file_list, find_by_name, or shell_execute. "
                    f"Do NOT narrate what you will do. Just call sub_agent immediately."
                ),
            })
            logger.info(f"[U11d] Injected mandatory sub_agent(explore) hint")

    # ═══════════════════════════════════════════════════════════════
    # P104-fork: RepoExploreAgent fork for architecture/directory questions
    # ═══════════════════════════════════════════════════════════════
    if _repo_explore_fork:
        logger.info(f"[P104-fork] Running RepoExploreAgent for: {_current_task_text[:50]}...")
        yield {"type": "explore_agent_start", "query": _current_task_text}
        
        try:
            # Run the read-only explore subagent
            explore_result = await run_repo_explore_subagent(
                query=_current_task_text,
                env=env,
                workspace=workspace,
                session_id=session_id,
            )
            
            yield {
                "type": "explore_agent_complete",
                "files_examined": len(explore_result.files_read),
                "completeness": explore_result.evidence_completeness,
            }
            
            # Inject explore results as system context for main agent
            # The main agent will now respond based on these findings, not speculation
            messages.append({
                "role": "system",
                "content": (
                    f"[REPO-EXPLORE RESULT] The following repository exploration "
                    f"was performed by a dedicated read-only agent:\n\n"
                    f"{explore_result.findings}\n\n"
                    f"Evidence completeness: {explore_result.evidence_completeness}\n"
                    f"Files examined: {len(explore_result.files_read)}\n"
                    f"You MUST base your response on these findings only. "
                    f"Do NOT invent directory purposes or architecture not supported by the evidence."
                ),
                "_turn": 0,
                "_repo_explore_result": True,
            })
            
            # Also inject the usage guidance
            messages.append({
                "role": "system",
                "content": create_repo_explore_system_prompt(),
                "_turn": 0,
            })
            
            # Now continue to main loop - but the main agent starts WITH exploration results
            # This is the key difference: model sees evidence first, not blank slate
            logger.info(
                f"[P104-fork] Explore complete: {len(explore_result.files_read)} files, "
                f"entering main loop with pre-populated evidence"
            )
            
        except Exception as e:
            logger.error(f"[P104-fork] RepoExploreAgent failed: {e}")
            # Fallback: disable fork and proceed with normal loop
            _repo_explore_fork = False
            _repo_fact_mode = True  # Still use repo-fact mode as fallback
            yield {"type": "explore_agent_error", "error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # Main agentic turn loop
    # ═══════════════════════════════════════════════════════════════
    _session_timeout = int(env.get("NANOBOT_SESSION_TIMEOUT", "300"))  # seconds
    _session_start = time.time()

    for turn in range(1, max_turns + 1):
        # Total session timeout guard — prevents infinite hangs
        if time.time() - _session_start > _session_timeout:
            logger.warning("[Timeout] Session %s exceeded %ds total timeout at turn %d", session_id, _session_timeout, turn)
            yield {"type": "error", "content": f"Session timeout ({_session_timeout}s) exceeded. Ending agentic loop.", "session_id": session_id, "turn": turn, "timeout_type": "session"}
            break
        # D2: Emit turn_start event for UI turn indicator
        yield {"type": "turn_start", "turn": turn}

        # If the task is already in verifying, do not wait for the model.
        # Force an immediate verify sub-agent run and resolve the task state.
        try:
            current_root = _root_task
            if current_root is not None:
                store = get_task_store(session_id, workspace)
                loaded_root = store.get_task(current_root.id) or current_root
                if loaded_root.state == "verifying" and _pending_forced_verify is None:
                    _pending_forced_verify = {"root_task_id": loaded_root.id, "reason": "preexisting_verifying"}
            if _pending_forced_verify:
                async for ev in _run_forced_verify_subagent(turn, _pending_forced_verify):
                    yield ev
                if _root_task is not None and _root_task.is_terminal:
                    break
                continue
        except Exception as _verify_exc:
            logger.warning(f"[VerifyGate] Forced verify path failed non-fatally: {_verify_exc}")

        if _repo_fact_mode and turn == 1:
            _one_shot_final = _find_repo_fact_one_shot_final(messages, user_message)
            if _one_shot_final:
                logger.info("[P104-one-shot] Existing repo-fact final answer found — skipping repeat generation")
                _last_final_answer_payload = {
                    "content": (
                        "已在上一次回答中给出该 repo-fact 结论；我不会基于同一证据重复生成第二版终稿。"
                        "如需继续，请提出新的具体文件、目录或差异点。"
                    ),
                    "summary": "已复用上一次 repo-fact 终稿。",
                    "details": "",
                    "brief_first": False,
                }
                yield {
                    "type": "chunk",
                    "content": _last_final_answer_payload["content"],
                }
                yield {"type": "final_answer", **_last_final_answer_payload, "turn": turn}
                break

        # U3: Check for completed background tasks and inject notifications
        try:
            from tools.sub_agent import get_completed_background_tasks, mark_task_notified
            _bg_completed = get_completed_background_tasks(session_id)
            for _bg_task in _bg_completed:
                _bg_result = _bg_task.result or {}
                _bg_success = _bg_result.get("success", False)
                _bg_output = _bg_result.get("output", "")
                _bg_error = _bg_result.get("error", "")

                if _bg_success:
                    _notification = (
                        f"<task-notification task_id=\"{_bg_task.task_id}\" "
                        f"agent_type=\"{_bg_task.agent_type}\" "
                        f"status=\"completed\" elapsed=\"{_bg_task.elapsed:.1f}s\">\n"
                        f"{_bg_output}\n"
                        f"</task-notification>"
                    )
                else:
                    _notification = (
                        f"<task-notification task_id=\"{_bg_task.task_id}\" "
                        f"agent_type=\"{_bg_task.agent_type}\" "
                        f"status=\"failed\" elapsed=\"{_bg_task.elapsed:.1f}s\">\n"
                        f"Error: {_bg_error}\n"
                        f"</task-notification>"
                    )

                messages.append({
                    "role": "system",
                    "content": _notification,
                    "_turn": turn,
                })
                mark_task_notified(_bg_task.task_id, session_id)

                # Emit SSE event for frontend
                yield {
                    "type": "background_task_done",
                    "task_id": _bg_task.task_id,
                    "agent_type": _bg_task.agent_type,
                    "success": _bg_success,
                    "elapsed": _bg_task.elapsed,
                    "turn": turn,
                }
                logger.info(
                    f"[U3] Background task {_bg_task.task_id} notification injected "
                    f"(success={_bg_success}, {_bg_task.elapsed:.1f}s)"
                )
        except ImportError:
            pass
        except Exception as _e_bg:
            logger.debug(f"[U3] Background task check failed: {_e_bg}")

        # AP-2 Layer 1: Collapse consecutive old read-only tool results
        _collapse_read_search_sequences(messages, turn)

        # U4/P3: Cache-aware vs destructive microcompact
        if _U4_CACHE_AWARE:
            # U4: Insert hint about stale results instead of mutating messages
            _u4_hint = _build_cache_aware_hint(messages, turn)
            if _u4_hint:
                # Remove previous U4 hint to avoid accumulation
                messages[:] = [
                    m for m in messages
                    if not (m.get("role") == "system" and _U4_HINT_TAG in m.get("content", ""))
                ]
                messages.append({"role": "system", "content": _u4_hint, "_turn": turn})
                logger.debug(f"[U4] Cache-aware hint injected at turn {turn}")
        elif turn > _MC_AGE_DECAY_TURNS:
            # P3 fallback: destructive second-pass microcompact for old messages
            mc_trimmed = _micro_compact_old_messages(messages, turn)

        # CW1: Token-weighted microcompact — pre-emptive eviction before auto-compact
        _cw1_tokens = _estimate_messages_tokens(messages)
        _cw1_threshold = int(budget.ceiling * _TW_MC_TARGET_RATIO)
        if _cw1_tokens > _cw1_threshold and not budget.should_compact(_cw1_tokens):
            _token_weighted_micro_compact(messages, turn, budget.ceiling)

        # P5/P10: Check if auto-compact is needed
        compact_result = None
        compact_event = None
        _compact_mode = budget.should_compact(_estimate_messages_tokens(messages))
        # CW3: Circuit breaker — skip auto-compact after MAX consecutive failures
        # Half-open: allow one probe attempt every _CW3_HALF_OPEN_PROBE_INTERVAL turns
        if _compact_mode and _cw3_autocompact_failures >= _CW3_MAX_CONSECUTIVE_FAILURES:
            _turns_since_open = turn - _cw3_breaker_open_turn
            if _turns_since_open >= _CW3_HALF_OPEN_PROBE_INTERVAL:
                logger.info(
                    f"[CW3] Circuit breaker HALF-OPEN: probing auto-compact at turn {turn} "
                    f"({_turns_since_open} turns since breaker opened)"
                )
            else:
                logger.error(
                    f"[CW3] Circuit breaker OPEN: {_cw3_autocompact_failures} consecutive failures, "
                    f"skipping compaction at turn {turn} (next probe in {_CW3_HALF_OPEN_PROBE_INTERVAL - _turns_since_open} turns)"
                )
                _compact_mode = None
        if _compact_mode:
            logger.info(f"[P5] Budget indicates compaction needed at turn {turn}")
            try:
                async for _hb_event in _auto_compact_with_heartbeat(
                    messages, env, session_id, workspace=workspace
                ):
                    if _hb_event.get("type") == "heartbeat":
                        yield _hb_event
                    elif _hb_event.get("type") == "compact_result":
                        compact_result = _hb_event.get("result")
                if compact_result and compact_result.get("compacted"):
                    compact_event = compact_result.get("event")
                    if compact_event:
                        yield compact_event
                    budget.record_compaction(
                        compact_result.get("old_tokens", 0),
                        compact_result.get("new_tokens", 0),
                    )
                    _cw3_autocompact_failures = 0  # CW3: reset on success (breaker CLOSED)
                    _run_post_compact_cleanup()  # CW4: centralized post-compact reset
                    _file_read_failed_paths.clear()  # CW4: reset local state
                else:
                    _cw3_autocompact_failures += 1
                    if _cw3_autocompact_failures >= _CW3_MAX_CONSECUTIVE_FAILURES:
                        _cw3_breaker_open_turn = turn  # lock timer on breach or failed probe
                    logger.warning(f"[CW3] Auto-compact returned no result (failure {_cw3_autocompact_failures}/{_CW3_MAX_CONSECUTIVE_FAILURES})")
            except Exception as e:
                _cw3_autocompact_failures += 1
                if _cw3_autocompact_failures >= _CW3_MAX_CONSECUTIVE_FAILURES:
                    _cw3_breaker_open_turn = turn  # lock timer on breach or failed probe
                logger.warning(f"[CW3] Auto-compact failed ({_cw3_autocompact_failures}/{_CW3_MAX_CONSECUTIVE_FAILURES}): {e}")

        # CW5/CW6: Emit token_warning SSE event for frontend pressure indicator
        # Throttled: only emit when state changes (avoids flooding frontend)
        _cw6_tokens = _estimate_messages_tokens(messages)
        _cw6_state = budget.get_token_warning_state(_cw6_tokens)
        if _cw6_state != _cw5_last_warning_state:
            _cw5_last_warning_state = _cw6_state
            yield {
                "type": "token_warning",
                "state": _cw6_state,
                "current_tokens": _cw6_tokens,
                "ceiling": budget.ceiling,
                "usage_pct": round(_cw6_tokens / budget.ceiling * 100, 1) if budget.ceiling else 0,
            }

        # Check stop request
        force_text_only = False
        if is_stop_fn and is_stop_fn():
            logger.info(f"[AgenticLoop] Stop requested at turn {turn}")
            break

        # Audit: Hard cap on total tool calls to prevent runaway accumulation
        _MAX_TOTAL_TOOL_CALLS = int(env.get("NANOBOT_MAX_TOTAL_TOOL_CALLS", "80"))
        if total_tool_calls >= _MAX_TOTAL_TOOL_CALLS:
            logger.warning(f"[SafetyGate] total_tool_calls={total_tool_calls} reached cap {_MAX_TOTAL_TOOL_CALLS}, forcing text-only")
            force_text_only = True
            messages.append({
                "role": "system",
                "content": (
                    "[TOOL LIMIT REACHED] You have used the maximum number of tool calls "
                    "for this session. Respond using the information you have already gathered. "
                    "Do NOT request any more tools."
                ),
            })

        # P35: Force text-only after too many tool-only turns
        if _consecutive_tool_only_turns >= 5:
            force_text_only = True
            logger.info(f"{_consecutive_tool_only_turns} consecutive tool-only turns, forcing text-only")
            messages.append({
                "role": "system",
                "content": (
                    "[RESPOND NOW] You have called tools for several turns without "
                    "producing a text response. You MUST now respond with a clear, "
                    "well-structured text answer summarizing your findings. Do NOT "
                    "call any more tools."
                ),
            })

        # ── LLM streaming call ──
        # R1: Signal the frontend to save a rollback checkpoint — if U12b/U12c
        # later rejects this generation, we yield clear_generation to undo it.
        yield {"type": "generation_start"}
        assistant_text_parts = []
        tool_calls_result = None

        # P60: Filter tools for specialized agent types
        # P100c: Skill allowed_tools enforcement — restrict tools when skill has allowed_tools
        if force_text_only:
            _turn_tools = []
        elif _repo_fact_mode:
            _turn_tools = [
                t for t in AGENTIC_TOOLS
                if t["function"]["name"] in _REPO_FACT_READ_ONLY_TOOLS
            ]
        elif allowed_tools is not None:
            _turn_tools = [t for t in AGENTIC_TOOLS
                           if t["function"]["name"] in allowed_tools]
        elif _skill_allowed_tools:
            _turn_tools = [t for t in AGENTIC_TOOLS
                           if _is_tool_allowed(t["function"]["name"], _skill_allowed_tools)]
        else:
            _turn_tools = AGENTIC_TOOLS
        # P102: Additionally filter out disallowed_tools (belt-and-suspenders with allowed_tools)
        if _skill_disallowed_tools and _turn_tools:
            _turn_tools = [t for t in _turn_tools
                           if t["function"]["name"] not in _skill_disallowed_tools]

        # A2: After compaction, inject preserved file context summary
        if turn > 1 and _SESSION_FILE_READS:
            _a2_existing = any(
                m.get("role") == "system" and "[Preserved file context]" in m.get("content", "")
                for m in messages
            )
            if not _a2_existing:
                _a2_summary = _get_preserved_files_summary(max_files=5)
                if _a2_summary:
                    messages.insert(1, {
                        "role": "system",
                        "content": f"[Preserved file context]\nFiles read earlier in this session (still available via file_read):\n{_a2_summary}",
                    })
                    logger.info(f"[A2] Injected preserved file context ({len(_SESSION_FILE_READS)} files)")

        # A3: Inject failure warnings if relevant failures exist
        if _SESSION_FAILURES and turn > 1:
            _a3_last_user = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    _a3_last_user = m.get("content", "")[:200]
                    break
            _a3_warning = _get_relevant_failures(context=_a3_last_user)
            if _a3_warning:
                _a3_existing = any(
                    m.get("role") == "system" and "[Previous failures" in m.get("content", "")
                    for m in messages[-5:]
                )
                if not _a3_existing:
                    messages.append({"role": "system", "content": _a3_warning})
                    logger.info(f"[A3] Injected {len(_SESSION_FAILURES)} failure warning(s)")

        async def _llm_producer():
            nonlocal tool_calls_result
            try:
                async for ev in _stream_one_turn(
                    messages, env,
                    _turn_tools,
                    stream_stats,
                ):
                    await llm_queue.put(ev)
            except Exception as exc:
                await llm_queue.put(("error", exc))
            await llm_queue.put(None)  # sentinel

        async def _heartbeat_producer():
            while not done_event.is_set():
                await asyncio.sleep(15)
                await llm_queue.put({"type": "heartbeat"})

        llm_queue = asyncio.Queue()
        done_event = asyncio.Event()
        llm_task = asyncio.ensure_future(_llm_producer())
        hb_task = asyncio.ensure_future(_heartbeat_producer())
        llm_error = None
        _U12A_BUFFER_SIZE = 200  # U12a-stream: buffer first N chars for narration scrub

        try:
            while True:
                event = await llm_queue.get()
                if event is None:
                    # U12a-stream: Flush any remaining buffer on LLM completion
                    if assistant_text_parts and sum(len(p) for p in assistant_text_parts) < _U12A_BUFFER_SIZE:
                        _buffer = "".join(assistant_text_parts)
                        _cleaned_buf, _was_scrubbed = _scrub_narration(_buffer)
                        if _was_scrubbed:
                            logger.info(f"[U12a-stream] Scrubbed narration from final buffer flush ({len(_buffer)} → {len(_cleaned_buf)} chars)")
                        if _cleaned_buf:
                            yield {"type": "chunk", "content": _cleaned_buf}
                        assistant_text_parts.clear()
                        assistant_text_parts.append(_cleaned_buf)
                    break
                if isinstance(event, tuple) and event[0] == "error":
                    llm_error = event[1]
                    break
                evt_type = event.get("type", "")
                if evt_type == "chunk":
                    # U12a-stream: Buffer-then-scrub approach for narration removal.
                    # llama.cpp streams per-token ("我", "将", "搜", ...), so regex
                    # can't match until enough tokens accumulate. We buffer the first
                    # ~80 chars, scrub narration, then flush the cleaned buffer.
                    _chunk_text = event["content"]
                    _total_buffered = sum(len(p) for p in assistant_text_parts)

                    if _total_buffered < _U12A_BUFFER_SIZE:
                        # Still buffering — accumulate but don't yield yet
                        assistant_text_parts.append(_chunk_text)
                        _new_total = _total_buffered + len(_chunk_text)
                        if _new_total >= _U12A_BUFFER_SIZE:
                            # Buffer full — flush with narration scrubbed
                            _buffer = "".join(assistant_text_parts)
                            _cleaned_buf, _was_scrubbed = _scrub_narration(_buffer)
                            if _was_scrubbed:
                                logger.info(f"[U12a-stream] Scrubbed narration from buffer ({len(_buffer)} → {len(_cleaned_buf)} chars)")
                            if _cleaned_buf:
                                yield {**event, "content": _cleaned_buf}
                            # Replace parts with the cleaned content for history
                            assistant_text_parts.clear()
                            assistant_text_parts.append(_cleaned_buf)
                        continue  # don't yield individual buffered chunks
                    else:
                        # Past buffer phase — stream normally
                        assistant_text_parts.append(_chunk_text)
                        yield event
                elif evt_type == "tool_calls_complete":
                    # U12a-stream: Flush buffer if tools arrive while still buffering
                    if assistant_text_parts and sum(len(p) for p in assistant_text_parts) < _U12A_BUFFER_SIZE:
                        _buffer = "".join(assistant_text_parts)
                        _cleaned_buf, _ = _scrub_narration(_buffer)
                        if _cleaned_buf:
                            yield {"type": "chunk", "content": _cleaned_buf}
                        assistant_text_parts.clear()
                        assistant_text_parts.append(_cleaned_buf)
                    tool_calls_result = event["tool_calls"]
                    # P27/B15/B16: fix file_read limits, full-file override, sharding
                    tool_calls_result = _apply_file_read_corrections(
                        tool_calls_result, _current_task_text, workspace
                    )
                elif evt_type == "usage":
                    budget.update_from_llm_usage(stream_stats)
                    yield event
                elif evt_type == "heartbeat":
                    yield {"type": "heartbeat"}
        finally:
            done_event.set()
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass

        # Handle LLM errors — structured classification (Claw errors.ts pattern)
        if llm_error:
            typed_err = classify_llm_error(llm_error)
            logger.error(f"[AgenticLoop] LLM error at turn {turn}: {typed_err.error_type} "
                         f"(status={typed_err.status_code}, retryable={typed_err.is_retryable}): "
                         f"{str(llm_error)[:200]}")

            # P80: Tool call type repair (llama.cpp "Missing tool call type")
            if isinstance(typed_err, ToolCallTypeError):
                logger.warning("[P80] Detected ToolCallTypeError — repairing tool_calls in messages")
                _repaired = 0
                for _msg in messages:
                    if _msg.get("role") == "assistant" and "tool_calls" in _msg:
                        for _tc in _msg["tool_calls"]:
                            if "type" not in _tc:
                                _tc["type"] = "function"
                                _repaired += 1
                if _repaired:
                    logger.info(f"[P80] Repaired {_repaired} tool_calls, retrying turn {turn}")
                    continue  # retry the turn with repaired messages

            # P10: Context overflow → reactive compaction (gated by ff.reactive_compact)
            if isinstance(typed_err, ContextOverflowError) and _compact_failures < 3 and ff.is_enabled("reactive_compact"):
                _compact_failures += 1
                logger.info(f"[P10 ReactiveCompact] {typed_err.error_type} detected, "
                            f"forcing compaction (attempt {_compact_failures})")
                try:
                    compact_result = await _auto_compact(messages, env, workspace, session_id)
                    if compact_result:
                        _run_post_compact_cleanup()  # CW4: centralized post-compact reset
                        _file_read_failed_paths.clear()  # CW4: reset local state
                        logger.info(f"[P10] Reactive compaction succeeded, retrying turn {turn}")
                        continue  # retry the turn
                    else:
                        logger.warning("[P10] Reactive compaction returned no result")
                except Exception as e2:
                    logger.warning(f"[P10] Reactive compaction failed: {e2}")

            if _compact_failures >= 3:
                logger.error(f"{_compact_failures} consecutive compact failures, giving up")
                yield {"type": "error", "content": f"Context too large and compaction failed after {_compact_failures} attempts."}
                break

            # Transient / rate-limit / timeout / connection errors → exponential backoff retry
            # Gated by ff.transient_retry; max_retries configurable via ff.max_retries
            if isinstance(typed_err, (TransientAPIError, RateLimitError, ConnectionError_, TimeoutError_)) and ff.is_enabled("transient_retry"):
                _transient_retries += 1
                _ff_max_retries = ff.get_int("max_retries")
                if should_retry(typed_err, _transient_retries, _ff_max_retries):
                    delay = get_retry_delay(_transient_retries, typed_err.retry_after)
                    logger.info(f"[Retry] {typed_err.error_type} — attempt {_transient_retries}/{DEFAULT_MAX_RETRIES}, "
                                f"waiting {delay:.1f}s before retry")
                    yield {"type": "status", "content": f"API {typed_err.error_type} error, retrying in {delay:.0f}s..."}
                    await asyncio.sleep(delay)
                    continue  # retry the turn
                else:
                    logger.warning(f"[Retry] Max retries ({DEFAULT_MAX_RETRIES}) exceeded for {typed_err.error_type}")

            # Authentication errors → surface immediately, no retry
            if isinstance(typed_err, AuthenticationError):
                yield {"type": "error", "content": f"Authentication error: {str(llm_error)[:200]}"}
                break

            # Fallback: try without tools (model may not support function calling)
            try:
                logger.info("[AgenticLoop] Retrying without tools (model may not support function calling)")
                async for ev in _stream_one_turn(messages, env, [], stream_stats):
                    if ev.get("type") == "chunk":
                        yield ev
                break
            except Exception as e:
                logger.error(f"[AgenticLoop] Retry also failed: {e}")
                yield {"type": "error", "content": str(e)}
                break

        # ── Assemble assistant text ──
        assistant_text = "".join(assistant_text_parts).strip()
        # P80: Second-pass strip of leaked tool call text (may span chunks)
        assistant_text = _TEXT_TOOL_CALL_RE.sub("", assistant_text).strip()

        # P24: Verbosity detection — warn if text between tool calls is too long
        if tool_calls_result and assistant_text:
            inter_tool_words = len(assistant_text.split())
            if inter_tool_words > 50:
                _verbose_turns += 1
                if _verbose_turns >= 2:
                    messages.append({
                        "role": "system",
                        "content": (
                            "[BE CONCISE] You are being too verbose between tool calls. "
                            "Keep inter-tool text to ≤25 words."
                        ),
                    })
                    logger.info(f"[P24] Injected BE CONCISE nudge at turn {turn}")

        # B2 + U12a: Strip narration from stored assistant text on ALL turns
        # The text was already streamed via SSE so user saw it, but stripping
        # from history prevents model from reinforcing the "I will..." pattern.
        _u12a_scrubbed = False
        if assistant_text and tool_calls_result:
            assistant_text, _u12a_scrubbed = _scrub_narration(assistant_text)
            if _u12a_scrubbed:
                logger.info(f"[U12a] Narration scrubbed from assistant text at turn {turn}")
                # U12a: Inject immediate conciseness nudge on FIRST narration detection
                _has_narr_nudge = any(
                    "[CONCISENESS]" in m.get("content", "")
                    for m in messages if m.get("role") == "system"
                )
                if not _has_narr_nudge:
                    messages.append({
                        "role": "system",
                        "content": (
                            "[CONCISENESS] 直接调用工具或给出结果，不要开场白。"
                            "Do NOT start with 'I will...', '我将...' — just call tools directly."
                        ),
                        "_turn": turn,
                    })

        # U12d: Pre-tool verbosity enforcement — when the model generates
        # massive fabricated text alongside tool_calls (common pattern: model
        # writes 500-word architecture analysis THEN calls file_list/file_read),
        # truncate the text to empty.  P13 says "≤25 words between tool calls".
        # The text was already streamed via SSE, so we send clear_generation
        # to tell the frontend to erase the fabricated portion.
        _U12D_PRETOOL_THRESHOLD = 50  # effective words
        if tool_calls_result and assistant_text:
            _pre_ew = max(len(assistant_text.split()), len(assistant_text) // 3)
            if _pre_ew > _U12D_PRETOOL_THRESHOLD:
                logger.info(
                    f"[U12d] Pre-tool text too verbose: {_pre_ew} effective words "
                    f"(threshold {_U12D_PRETOOL_THRESHOLD}) — truncating to empty"
                )
                assistant_text = ""
                yield {"type": "clear_generation"}

        # Add assistant message to history
        assistant_msg = {"role": "assistant", "content": assistant_text or ""}
        if tool_calls_result:
            # Sanitize tool_calls for LLM consumption: strip internal fields,
            # ensure "type": "function" is present (required by llama.cpp)
            clean_tcs = []
            for tc in tool_calls_result:
                clean_tc = {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": tc.get("function", {"name": "", "arguments": ""}),
                }
                clean_tcs.append(clean_tc)
            assistant_msg["tool_calls"] = clean_tcs
        messages.append(assistant_msg)
        _final_output_rewrite = False

        # ── If no tool calls, model is done ──
        if not tool_calls_result:
            _pending_change_set_ids = sorted(_pending_change_sets.keys())
            # ApprovalGate: If pending change sets exist, stop the loop immediately.
            # User must approve/reject first. No more turns regardless of what model says.
            if _pending_change_set_ids:
                # If model prematurely claimed the edit is done, clear that text
                if assistant_text and _has_premature_transactional_edit_claim(assistant_text):
                    if messages and messages[-1] is assistant_msg:
                        messages.pop()
                    yield {"type": "clear_generation"}
                logger.info(f"[ApprovalGate] Stopping agentic loop at turn {turn}; "
                            f"pending change sets awaiting approval: {_pending_change_set_ids}")
                break
            if assistant_text:
                _finalized_text, _finalized = _finalize_final_response(assistant_text)
                if _finalized and _finalized_text:
                    assistant_text = _finalized_text
                    if messages and messages[-1].get("role") == "assistant":
                        messages[-1]["content"] = assistant_text
                    _final_output_rewrite = True

            # P102: Completion gate — multi-layer checks before allowing finish

            # Layer 1: Block finish if pending_verification (writes not yet verified)
            if _pending_verification and _skill_requires_verification:
                logger.info(f"[P102] Completion blocked — pending_verification=True, "
                            f"{_write_count_since_verify} write(s) unverified (mode={_skill_mode})")
                messages.append({
                    "role": "system",
                    "content": (
                        f"[VERIFICATION REQUIRED] You made {_write_count_since_verify} code "
                        f"change(s) but haven't verified them. In /{_skill_mode} mode, you "
                        f"MUST run verification (tests, lint, build) before finishing. "
                        f"Call shell_execute now."
                    ),
                })
                _consecutive_tool_only_turns = 0
                continue  # Force another turn

            # Layer 2: Completion criteria check (output quality gating)
            if _skill_mode and assistant_text:
                _criteria_hint = _check_completion_criteria(_skill_mode, assistant_text)
                if _criteria_hint and _p102_criteria_nudges < 2:
                    _p102_criteria_nudges += 1
                    logger.info(f"[P102] Completion criteria not met for /{_skill_mode} "
                                f"(nudge {_p102_criteria_nudges}/2)")
                    messages.append({"role": "system", "content": _criteria_hint})
                    _consecutive_tool_only_turns = 0
                    continue  # Force another turn to meet criteria

            # P104-route: Repo-fact completion gate — block complete architecture
            # summaries when the model has only seen partial file_read output.
            if _repo_fact_mode and assistant_text:
                _repo_gate = _build_repo_fact_completion_gate(messages, turn, _current_task_text)
                if _repo_gate:
                    logger.info("[P104-route] Repo-fact completion blocked due to partial file read")
                    if messages and messages[-1].get("role") == "assistant":
                        messages.pop()
                    messages.append({"role": "system", "content": _repo_gate, "_turn": turn})
                    _consecutive_tool_only_turns = 0
                    yield {"type": "clear_generation"}
                    continue

                if _is_duplicate_repo_fact_answer(messages, assistant_text):
                    logger.info("[P104-repeat] Duplicate repo-fact final answer detected — keeping first answer only")
                    if messages and messages[-1].get("role") == "assistant":
                        messages.pop()
                    yield {"type": "clear_generation"}
                    _consecutive_tool_only_turns = 0
                    break

            # U12b: Anti-hallucination anchoring — detect disproportionately long
            # final text relative to tool data (common when small models fabricate
            # architecture analysis from directory names alone)
            if assistant_text and turn >= 1:
                # CJK-aware word count: Chinese has no spaces between words, so
                # len(split()) returns ~80 for 500 chars. Use char_count//3 as
                # fallback (1 CJK char ≈ 0.3 EN word), take the larger estimate.
                _split_words = len(assistant_text.split())
                _char_words = len(assistant_text) // 3
                _response_words = max(_split_words, _char_words)
                logger.info(f"[U12b-diag] turn={turn}, split={_split_words}, "
                            f"chars={len(assistant_text)}, effective_words={_response_words}")
                # Only count THIS turn's tool data — historical results from previous
                # turns should not inflate the threshold (Q4-type: model saw file_list
                # in turn 1, fabricates 500-word analysis in turn 2 with no new tools)
                _tool_data_chars = sum(
                    len(m.get("content", ""))
                    for m in messages
                    if m.get("role") == "tool" and m.get("_turn") == turn
                )
                # If model wrote >200 words but THIS turn's tool data is <2000 chars,
                # the response likely contains fabricated details
                if _response_words > 200 and _tool_data_chars < 2000:
                    _has_ground = any(
                        "[GROUNDING]" in m.get("content", "")
                        for m in messages if m.get("role") == "system"
                    )
                    if not _has_ground:
                        logger.info(f"[U12b] Hallucination risk: {_response_words} words from "
                                    f"{_tool_data_chars} chars of tool data — injecting grounding nudge")
                        # Remove the fabricated assistant message we just appended
                        # (line 2854) — if the model sees its own hallucination,
                        # it will just abbreviate rather than switch to tool calls.
                        if messages and messages[-1].get("role") == "assistant":
                            messages.pop()
                            logger.info("[U12b] Removed fabricated assistant message from history")
                        _grounding_msg = (
                            "[GROUNDING] Your response is much longer than the tool data supports. "
                            "ONLY state facts that appear VERBATIM in tool results above. "
                            "Do NOT infer module purposes from filenames or fabricate architecture descriptions. "
                            "If you lack information, say so — do NOT fill gaps with plausible-sounding guesses."
                        )
                        if _tool_data_chars == 0:
                            _grounding_msg += (
                                "\nYou have NOT called any tools this turn. "
                                "Call file_read, file_list, or grep_search FIRST to get real data, "
                                "then answer based on those results."
                            )
                        messages.append({
                            "role": "system",
                            "content": _grounding_msg,
                            "_turn": turn,
                        })
                        _consecutive_tool_only_turns = 0
                        # R1: Tell frontend to erase the rejected text
                        yield {"type": "clear_generation"}
                        continue  # Force a shorter, grounded re-response

            # U12c: Categorization detection — small models expand simple file lists
            # into categorized reports with fabricated descriptions (emoji or bold headers)
            _emoji_hits = len(_EMOJI_HEADER_RE.findall(assistant_text)) if assistant_text else 0
            _bold_hits = len(_BOLD_CATEGORY_RE.findall(assistant_text)) if assistant_text else 0
            _category_count = _emoji_hits + _bold_hits
            if _category_count > 0:
                logger.info(f"[U12c-diag] Category headers: {_emoji_hits} emoji + {_bold_hits} bold = {_category_count}, threshold=2")
            if assistant_text and _category_count >= 2:
                _has_emoji_nudge = any(
                    "[NO CATEGORIZATION]" in m.get("content", "")
                    for m in messages if m.get("role") == "system"
                )
                if not _has_emoji_nudge:
                    logger.info(f"[U12c] Categorization detected ({_category_count} headers: "
                                f"{_emoji_hits} emoji + {_bold_hits} bold) — injecting condensation nudge")
                    # Remove the categorized assistant message so model doesn't
                    # see its own over-elaborated response and repeat the pattern
                    if messages and messages[-1].get("role") == "assistant":
                        messages.pop()
                        logger.info("[U12c] Removed categorized assistant message from history")
                    messages.append({
                        "role": "system",
                        "content": (
                            "[NO CATEGORIZATION] Your response uses categorized headers "
                            "(emoji or bold sections) to organize a simple result. "
                            "This is NOT what the user asked for. "
                            "Rewrite: just list the items plainly without category headers, "
                            "without descriptions for each category, and under 5 lines total."
                        ),
                        "_turn": turn,
                    })
                    _consecutive_tool_only_turns = 0
                    # R1: Tell frontend to erase the categorized text
                    yield {"type": "clear_generation"}
                    continue  # Force re-response without categorization
                else:
                    # U12c-hardclean: Nudge was already sent but model persists —
                    # physically remove emoji/bold header lines as last resort
                    _lines = assistant_text.split("\n")
                    _cleaned_lines = [
                        ln for ln in _lines
                        if not _EMOJI_HEADER_RE.match(ln) and not _BOLD_CATEGORY_RE.match(ln)
                    ]
                    assistant_text = "\n".join(_cleaned_lines).strip()
                    if messages and messages[-1].get("role") == "assistant":
                        messages[-1]["content"] = assistant_text
                    _final_output_rewrite = True
                    logger.info(f"[U12c-hardclean] Hard-scrubbed {_category_count} category headers from final response")

            if assistant_text and len(assistant_text) > 50:
                _before = len(assistant_text)
                assistant_text = _dedup_paragraphs(assistant_text)
                if len(assistant_text) != _before and messages and messages[-1].get("role") == "assistant":
                    messages[-1]["content"] = assistant_text
                    _final_output_rewrite = True
                    logger.info(f"[D4] Deduped final response: {_before} → {len(assistant_text)} chars")

            if assistant_text:
                _final_answer_payload = _build_final_answer_payload(assistant_text)
                if _final_answer_payload["content"] and _final_answer_payload["content"] != assistant_text:
                    assistant_text = _final_answer_payload["content"]
                    if messages and messages[-1].get("role") == "assistant":
                        messages[-1]["content"] = assistant_text
                    _final_output_rewrite = True
                _last_final_answer_payload = _final_answer_payload

            if _final_output_rewrite and assistant_text:
                logger.info(f"[Claw-finalize] Replaying cleaned final response at turn {turn}")
                yield {"type": "clear_generation"}
                yield {"type": "chunk", "content": assistant_text}

            if assistant_text and _last_final_answer_payload:
                yield {"type": "final_answer", **_last_final_answer_payload, "turn": turn}

            _consecutive_tool_only_turns = 0
            break

        # U12c-bis: Categorization detection also in the WITH-tool-calls path.
        # Small models produce categorized text alongside tool results.
        if assistant_text and tool_calls_result:
            _emoji_count_bis = len(_EMOJI_HEADER_RE.findall(assistant_text))
            _bold_count_bis = len(_BOLD_CATEGORY_RE.findall(assistant_text))
            _cat_count_bis = _emoji_count_bis + _bold_count_bis
            if _cat_count_bis >= 2:
                # Strip emoji/bold category lines from stored assistant text
                _lines = assistant_text.split("\n")
                _cleaned_lines = [
                    ln for ln in _lines
                    if not _EMOJI_HEADER_RE.match(ln) and not _BOLD_CATEGORY_RE.match(ln)
                ]
                assistant_text = "\n".join(_cleaned_lines).strip()
                # Update the message in history
                if messages and messages[-1].get("role") == "assistant":
                    messages[-1]["content"] = assistant_text
                logger.info(f"[U12c-bis] Hard-scrubbed {_cat_count_bis} category headers ({_emoji_count_bis} emoji + {_bold_count_bis} bold) from inter-tool text")

        _consecutive_tool_only_turns += 1 if not assistant_text else 0
        dedup_skipped = 0

        _d2_final_answer = False
        if assistant_text and tool_calls_result:
            _d2_has_structure = bool(re.search(r'^(#{1,3}\s|[-*]\s|\d+\.\s)', assistant_text, re.MULTILINE))
            _d2_has_narration = bool(_NARRATION_RE.match(assistant_text.strip()))
            _d2_word_count = max(len(assistant_text.split()), len(assistant_text) // 3)
            if len(assistant_text) > 200 and _d2_has_structure and not _d2_has_narration and _d2_word_count > 50:
                _d2_final_answer = True
        if _d2_final_answer and tool_calls_result:
            logger.info(f"[D2] Comprehensive final answer detected at turn {turn} — dropping tool calls")
            if messages and messages[-1].get("role") == "assistant":
                messages[-1].pop("tool_calls", None)
            tool_calls_result = None
            _consecutive_tool_only_turns = 0
            break

        if dedup_skipped > 0 and not assistant_text:
            _d2_idle_count += 1
        else:
            _d2_idle_count = 0
        if _d2_idle_count >= 2:
            logger.info(f"[D2] {_d2_idle_count} idle turns after dedup — stopping loop")
            break

        # ── Execute tool calls ──
        # P0: Partition into read-only concurrent batches vs sequential
        # P23: Sibling error cascading abort
        _turn_pending_change_set_ids: List[str] = []

        # Task-state constraint check: filter tools not allowed under current root task state
        _task_constraint_notes: List[str] = []
        try:
            _tc_store = get_task_store(session_id, workspace)
            _tc_root = _tc_store.get_latest_root_task()
            if _tc_root is not None:
                if _tc_root.state == "waiting_approval":
                    # ── Approval-state recovery (anti-oscillation) ──
                    # Check BOTH in-memory cache AND authoritative on-disk store.
                    # The in-memory dict may be empty after loop restart, so we
                    # must also query edit_transaction's disk store.
                    _disk_pending = []
                    try:
                        _disk_pending = list_pending_change_sets(session_id=session_id)
                    except Exception as _dp_exc:
                        logger.debug("[TaskConstraint] disk pending query failed: %s", _dp_exc)

                    # Reconcile in-memory cache with disk truth:
                    # 1. Add any disk-pending entries missing from memory
                    # 2. Remove stale memory entries no longer pending on disk
                    #    (e.g., user accepted/rejected via API while loop was idle)
                    _disk_pending_ids = set()
                    for _dp_cs in _disk_pending:
                        _dp_id = str(_dp_cs.get("id", "")).strip()
                        if _dp_id:
                            _disk_pending_ids.add(_dp_id)
                            if _dp_id not in _pending_change_sets:
                                _pending_change_sets[_dp_id] = dict(_dp_cs)
                    _stale_ids = [k for k in _pending_change_sets if k not in _disk_pending_ids]
                    for _stale_id in _stale_ids:
                        _pending_change_sets.pop(_stale_id, None)
                        logger.info("[TaskConstraint] Evicted stale in-memory CS %s (no longer pending on disk)", _stale_id)

                    _has_any_pending = bool(_pending_change_sets)

                    if _has_any_pending:
                        # Pending change sets exist — stay in waiting_approval.
                        logger.debug(
                            "[TaskConstraint] waiting_approval: pending change sets exist "
                            "(memory=%d, disk=%d) — no recovery",
                            len(_pending_change_sets), len(_disk_pending),
                        )
                    else:
                        # No pending change sets anywhere. Check guards before recovery:
                        #
                        # Guard 1: Discarded change sets — if change sets were silently
                        # removed by TTL/overflow cleanup rather than user action, this
                        # is suspicious. Block recovery and log a warning.
                        #
                        # Guard 2: Cooldown — if the last transition was
                        # in_progress → waiting_approval within the last 30 seconds,
                        # do NOT recover (it was just set intentionally).
                        #
                        # Guard 3: Recovery target is always "planned" (not in_progress)
                        # so the agent re-evaluates rather than re-creating the same edit.
                        _recovery_allowed = True
                        _RECOVERY_COOLDOWN_SECONDS = 30

                        # Guard 1: Check for silently discarded change sets
                        _discarded = []
                        try:
                            _discarded = list_discarded_change_sets(session_id=session_id)
                        except Exception:
                            pass
                        if _discarded:
                            _recovery_allowed = False
                            # Record in task metadata that change sets were discarded
                            try:
                                _tc_meta = dict(_tc_root.metadata or {})
                                _tc_meta["pending_changes_discarded"] = True
                                _tc_meta["discarded_change_set_ids"] = [
                                    cs.get("id", "?") for cs in _discarded
                                ]
                                _tc_root.metadata = _tc_meta
                                _tc_store.upsert_task(_tc_root)
                                _tc_store.save()
                            except Exception:
                                pass
                            logger.warning(
                                "[TaskConstraint] waiting_approval recovery BLOCKED: "
                                "%d change set(s) were silently discarded (not user-rejected). "
                                "IDs: %s. User must explicitly accept or reject to unblock.",
                                len(_discarded),
                                [cs.get("id", "?") for cs in _discarded],
                            )

                        # Guard 2: Cooldown
                        if _recovery_allowed:
                            _tc_history = getattr(_tc_root, "state_history", []) or []
                            if _tc_history:
                                _last_entry = _tc_history[-1]
                                _last_to = str(_last_entry.get("to_state", "")).strip().lower()
                                _last_from = str(_last_entry.get("from_state", "")).strip().lower()
                                if _last_to == "waiting_approval" and _last_from == "in_progress":
                                    _last_ts_str = _last_entry.get("timestamp", "")
                                    try:
                                        _last_ts = datetime.fromisoformat(_last_ts_str.replace("Z", "+00:00"))
                                        if _last_ts.tzinfo is None:
                                            _last_ts = _last_ts.replace(tzinfo=timezone.utc)
                                        _age_secs = (datetime.now(timezone.utc) - _last_ts).total_seconds()
                                        if _age_secs < _RECOVERY_COOLDOWN_SECONDS:
                                            _recovery_allowed = False
                                            logger.info(
                                                "[TaskConstraint] waiting_approval recovery blocked by cooldown "
                                                "(%.1fs < %ds since in_progress→waiting_approval)",
                                                _age_secs, _RECOVERY_COOLDOWN_SECONDS,
                                            )
                                    except Exception:
                                        pass  # timestamp parse failure — allow recovery

                        if _recovery_allowed:
                            # Guard 3: Recovery target is planned (NOT in_progress)
                            try:
                                _tc_root = _tc_store.transition(
                                    _tc_root.id,
                                    "planned",
                                    source="task_constraint_recovery",
                                    reason="No pending change sets remain (disk+memory); "
                                           "recovering to planned to avoid re-triggering same edit",
                                )
                                _root_task = _tc_root
                                logger.info(
                                    "[TaskConstraint] waiting_approval → planned "
                                    "(no pending change sets on disk or in memory)"
                                )
                            except Exception as _tc_recover_exc:
                                logger.warning(
                                    "[TaskConstraint] waiting_approval→planned recovery failed: %s "
                                    "[session=%s, task=%s]",
                                    _tc_recover_exc, session_id, _tc_root.id,
                                )
                _tc_state = _tc_root.state
                for _tc_idx, _tc_item in enumerate(tool_calls_result):
                    if _tc_item.get("_dedup_skip"):
                        continue
                    _tc_raw = _tc_item.get("function", {}).get("name", "")
                    _tc_tname = TOOL_NAME_ALIASES.get(_tc_raw, _tc_raw)
                    if not is_tool_allowed(_tc_state, _tc_tname):
                        _tc_item["_dedup_skip"] = True
                        _tc_note = (
                            f"<system_note>当前任务状态为 {_tc_state}，工具 {_tc_tname} 已被暂时禁用。"
                            f"请先调整任务状态，或改用 file_read 检查当前状态。</system_note>"
                        )
                        _task_constraint_notes.append(_tc_note)
                        messages.append({
                            "role": "tool", "tool_call_id": _tc_item.get("id", ""),
                            "content": _tc_note,
                            "_tool_name": _tc_tname, "_turn": turn,
                        })
                        logger.info(f"[TaskConstraint] Tool {_tc_tname} blocked by state {_tc_state} "
                                    f"(task={_tc_root.id})")
        except Exception as _tc_exc:
            logger.debug(f"[TaskConstraint] Check failed (non-fatal): {_tc_exc}")

        if _task_constraint_notes:
            messages.append({
                "role": "system",
                "content": (
                    "[TASK STATE CONSTRAINT] Some tools were blocked by the current task state. "
                    "Review the <system_note> messages above for details. "
                    "You may use allowed tools or wait for the task state to change."
                ),
                "_turn": turn,
            })

        for tc in tool_calls_result:
            tc_id = tc.get("id", "")
            func = tc.get("function", {})
            raw_name = func.get("name", "")
            tool_name = TOOL_NAME_ALIASES.get(raw_name, raw_name)

            # Duplicate detection
            call_sig = f"{tool_name}:{func.get('arguments', '')}"
            if call_sig in _recent_tool_calls[-20:]:
                dedup_skipped += 1
                logger.info(f"[AgenticLoop] Duplicate tool call: {tool_name}")
                tc["_dedup_skip"] = True
                messages.append({
                    "role": "tool", "tool_call_id": tc_id,
                    "content": (
                        f"[DUPLICATE] You already called {tool_name}"
                        f" with these exact arguments. Results are unchanged. "
                        f"Try DIFFERENT arguments or answer with info you have."
                    ),
                    "_tool_name": tool_name, "_turn": turn,
                })
                continue
            # B9: Normalized dedup for file_read — different path formats
            # (e.g., 'system_prompts.py' vs 'web_ui/system_prompts.py') resolve
            # to the same file, bypassing the raw call_sig check above.
            if tool_name == "file_read":
                try:
                    _b9_args = json.loads(func.get("arguments", "{}"))
                    _b9_path = _b9_args.get("path", "")
                    _b9_offset = _b9_args.get("offset")
                    _b9_limit = _b9_args.get("limit")
                    # Normalize: resolve path relative to workspace
                    _b9_resolved = str((Path(workspace) / _b9_path).resolve()) if _b9_path else ""
                    _b9_norm_sig = f"file_read:{_b9_resolved}:{_b9_offset}:{_b9_limit}"
                    if _b9_norm_sig in _recent_tool_calls[-20:]:
                        dedup_skipped += 1
                        logger.info(f"[B9] Normalized dedup for file_read: {_b9_path} → {_b9_resolved}")
                        tc["_dedup_skip"] = True
                        messages.append({
                            "role": "tool", "tool_call_id": tc_id,
                            "content": (
                                f"[DUPLICATE] You already called file_read on this same file "
                                f"(resolved: {_b9_resolved}) with offset={_b9_offset}. "
                                f"The result is the same regardless of path format. "
                                f"Use the result you already have, or try a DIFFERENT file."
                            ),
                            "_tool_name": tool_name, "_turn": turn,
                        })
                        continue
                    _d1_already_read = False
                    # Snapshot items before iterating: defends against future
                    # threaded refactors where _SESSION_ACTIVE_FILES could be
                    # mutated concurrently (see SECURITY.md R4).
                    for _d1_key, _d1_entry in list(_SESSION_ACTIVE_FILES.items()):
                        if _d1_entry.get("last_action") != "read":
                            continue
                        _d1_resolved = str(Path(_d1_key).resolve()) if "/" in _d1_key else _d1_key
                        if _d1_resolved == _b9_resolved and _d1_entry.get("lines", 0) > 0:
                            _d1_already_read = True
                            break
                    if _d1_already_read:
                        dedup_skipped += 1
                        logger.info(f"[D1] Session-aware file_read block: {_b9_path} already read")
                        tc["_dedup_skip"] = True
                        messages.append({
                            "role": "tool", "tool_call_id": tc_id,
                            "content": (
                                f"[STOP RE-READING] You already read {_b9_path} in this session. "
                                f"Use the earlier content instead of calling file_read again. "
                                f"If you need more information, answer now or read a DIFFERENT file."
                            ),
                            "_tool_name": tool_name, "_turn": turn,
                        })
                        continue
                    _recent_tool_calls.append(_b9_norm_sig)
                except (json.JSONDecodeError, ValueError, OSError):
                    pass
            # NOTE: Do NOT append call_sig here. It is recorded AFTER
            # successful execution so that failed calls (e.g. file_edit
            # rejected by "must read file first") can be retried.
            tc["_call_sig"] = call_sig

            # Parse tool args
            tool_args = tc.get("_parsed_args", {})
            if not tool_args:
                try:
                    tool_args = json.loads(func.get("arguments", "{}"))
                except (json.JSONDecodeError, ValueError):
                    logger.warning(f"[AgenticLoop] Invalid JSON args for {tool_name}")
                    tool_args = {}

            tc["_tool_name"] = tool_name
            tc["_tool_args"] = tool_args

            # ── Mode write-tool hard block ──
            # Even if the model hallucinates a write tool call, Ask/Plan must not execute it.
            _is_write_tool = tool_name in ("file_edit", "file_write")
            if mode in ("ask", "plan") and _is_write_tool:
                logger.info(f"[Mode:{mode.title()}] BLOCKED {tool_name} — write tools are unavailable in this mode")
                tc["_dedup_skip"] = True
                messages.append({
                    "role": "tool", "tool_call_id": tc_id,
                    "content": (
                        f"[BLOCKED by {mode.title()} mode] Tool {tool_name} is not available in this mode. "
                        f"{mode_meta[mode]['banner']} Please provide analysis, a safer plan, or ask the user to switch to Code mode for editing."
                    ),
                    "_tool_name": tool_name, "_turn": turn,
                })
                continue

            # ── P102: Full skill mode tool enforcement ──
            if _skill_mode:
                # 1) Disallowed tools hard block — catches ANY blocked tool, not just writes
                if tool_name in _skill_disallowed_tools:
                    logger.info(f"[P102] BLOCKED {tool_name} — in disallowed_tools (mode={_skill_mode})")
                    tc["_dedup_skip"] = True
                    messages.append({
                        "role": "tool", "tool_call_id": tc_id,
                        "content": (
                            f"[BLOCKED by /{_skill_mode} mode] Tool {tool_name} is not "
                            f"available in this mode. Allowed tools: "
                            f"{', '.join(_skill_allowed_tools[:8]) if _skill_allowed_tools else 'see skill definition'}."
                        ),
                        "_tool_name": tool_name, "_turn": turn,
                    })
                    continue

                # 2) Execution-time allowlist guardrail — catch hallucinated tool calls
                #    that bypass the _turn_tools filter (e.g., model invents a tool name)
                if _skill_allowed_tools and not _is_tool_allowed(tool_name, _skill_allowed_tools):
                    logger.info(f"[P102] BLOCKED {tool_name} — not in allowed_tools (mode={_skill_mode})")
                    tc["_dedup_skip"] = True
                    messages.append({
                        "role": "tool", "tool_call_id": tc_id,
                        "content": (
                            f"[BLOCKED by /{_skill_mode} mode] Tool {tool_name} is not in "
                            f"the allowed tools list for this mode."
                        ),
                        "_tool_name": tool_name, "_turn": turn,
                    })
                    continue

                # 3) Write policy enforcement
                _is_write_tool = tool_name in ("file_edit", "file_write")
                if _is_write_tool and _skill_write_policy == "forbid":
                    logger.info(f"[P102] BLOCKED {tool_name} — write_policy=forbid (mode={_skill_mode})")
                    tc["_dedup_skip"] = True
                    messages.append({
                        "role": "tool", "tool_call_id": tc_id,
                        "content": (
                            f"[BLOCKED by /{_skill_mode} mode] Write operation {tool_name} is "
                            f"not allowed in this mode (write_policy=forbid). This mode is "
                            f"read-only. Use /refactor if you need to make changes."
                        ),
                        "_tool_name": tool_name, "_turn": turn,
                    })
                    continue
                elif _is_write_tool and _skill_write_policy == "explicit_only":
                    if not _has_run_verification:
                        logger.info(f"[P102] BLOCKED {tool_name} — write_policy=explicit_only, "
                                    f"no diagnostic step run yet (mode={_skill_mode})")
                        tc["_dedup_skip"] = True
                        messages.append({
                            "role": "tool", "tool_call_id": tc_id,
                            "content": (
                                f"[BLOCKED by /{_skill_mode} mode] Write operation {tool_name} is "
                                f"blocked until you run diagnostic commands first. Use shell_execute "
                                f"to run tests/repro/analysis, then write tools will unlock."
                            ),
                            "_tool_name": tool_name, "_turn": turn,
                        })
                        continue
                elif _is_write_tool and _skill_requires_verification:
                    _pending_verification = True
                    _write_count_since_verify += 1
                    logger.debug(f"[P102] Write #{_write_count_since_verify} in requires_verification "
                                 f"mode — pending_verification=True")

                # D4: File-target cross-check — warn if editing wrong file
                if _is_write_tool and _user_target_files:
                    _edit_path = tool_args.get("path", "")
                    _mismatch_warn = _check_edit_target_mismatch(_edit_path, _user_target_files, str(workspace))
                    if _mismatch_warn:
                        logger.warning(f"[D4] Target mismatch: editing {_edit_path!r} "
                                       f"but user asked for {_user_target_files}")
                        tc["_dedup_skip"] = True
                        messages.append({
                            "role": "tool", "tool_call_id": tc_id,
                            "content": _mismatch_warn,
                            "_tool_name": tool_name, "_turn": turn,
                        })
                        continue

                # 4) Smart verification tracking — only count actual test/lint/build commands
                if tool_name == "shell_execute" and _skill_mode in ("debug", "verify", "refactor"):
                    _cmd = tool_args.get("command", "")
                    if _is_verification_command(_cmd):
                        _has_run_verification = True
                        if _pending_verification:
                            _pending_verification = False
                            _write_count_since_verify = 0
                            logger.debug(f"[P102] Verification command detected — "
                                         f"pending_verification cleared: {_cmd[:80]}")
                    else:
                        logger.debug(f"[P102] shell_execute is not a verification command: {_cmd[:80]}")

        # B12: Post-generation file_read parameter correction
        # When B10 extracted explicit parameters from user's numbered steps,
        # correct model's wrong parameters (e.g., offset=800 on wrong file).
        if _b12_file_plan:
            for tc in tool_calls_result:
                if tc.get("_dedup_skip") or tc.get("_tool_name") != "file_read":
                    continue
                _b12_args = tc.get("_tool_args", {})
                _b12_path = _b12_args.get("path", "")
                if not _b12_path:
                    continue
                # Find matching plan entry by filename fragment
                _b12_matched_key = None
                for _plan_key in _b12_file_plan:
                    # Match if plan key is in the path or path ends with plan key
                    if _plan_key in _b12_path or _b12_path.endswith(_plan_key):
                        _b12_matched_key = _plan_key
                        break
                    # Also match basename
                    _plan_base = _plan_key.rsplit("/", 1)[-1]
                    _path_base = _b12_path.rsplit("/", 1)[-1]
                    if _plan_base == _path_base:
                        _b12_matched_key = _plan_key
                        break
                if _b12_matched_key is None:
                    continue
                _b12_plan = _b12_file_plan[_b12_matched_key]
                _b12_model_off = _b12_args.get("offset")
                _b12_model_lim = _b12_args.get("limit")
                _b12_plan_off = _b12_plan["offset"]
                _b12_plan_lim = _b12_plan["limit"]
                # Check if model's params differ from plan
                if _b12_model_off != _b12_plan_off or _b12_model_lim != _b12_plan_lim:
                    _b12_old = f"offset={_b12_model_off}, limit={_b12_model_lim}"
                    # Apply plan's correct parameters
                    if _b12_plan_off is not None:
                        _b12_args["offset"] = _b12_plan_off
                    elif "offset" in _b12_args:
                        del _b12_args["offset"]
                    if _b12_plan_lim is not None:
                        _b12_args["limit"] = _b12_plan_lim
                    elif "limit" in _b12_args:
                        del _b12_args["limit"]
                    tc["_tool_args"] = _b12_args
                    tc["function"]["arguments"] = json.dumps(_b12_args)
                    _b12_new = f"offset={_b12_plan_off}, limit={_b12_plan_lim}"
                    logger.info(
                        f"[B12] Corrected file_read({_b12_path}): "
                        f"{_b12_old} → {_b12_new} (from user's step plan)"
                    )

        _recover_file_read_paths(tool_calls_result, workspace)
        if _repo_fact_mode:
            _upgrade_repo_fact_file_reads(tool_calls_result)

        # B1: Intercept redundant find_by_name when user already gave a path
        # Small models ignore "call file_read DIRECTLY" guidance — auto-convert.
        for tc in tool_calls_result:
            if tc.get("_tool_name") != "find_by_name":
                continue
            pattern = (tc.get("_tool_args") or {}).get("pattern", "")
            if not pattern:
                continue
            # Check if the user message contains this filename/path
            if pattern in _current_task_text:
                logger.info(f"[B1] Converting find_by_name({pattern!r}) → file_read (user already gave path)")
                tc["_tool_name"] = "file_read"
                tc["function"] = {"name": "file_read", "arguments": json.dumps({"path": pattern})}
                tc["_tool_args"] = {"path": pattern}
                # Update the call signature for dedup tracking
                new_sig = f"file_read:{json.dumps({'path': pattern})}"
                if new_sig not in _recent_tool_calls[-20:]:
                    _recent_tool_calls.append(new_sig)

        # Stop check
        _stop_requested = is_stop_fn and is_stop_fn()
        if _stop_requested:
            break

        # Partition into batches (P0: concurrent read-only)
        batches = _partition_tool_calls(tool_calls_result)

        for batch_idx, (is_concurrent, batch_tcs) in enumerate(batches):
            if _stop_requested:
                break
            # Intra-turn timeout check — catch cumulative tool execution exceeding session budget
            if time.time() - _session_start > _session_timeout:
                logger.warning("[Timeout] Session %s exceeded %ds during tool execution at turn %d batch %d", session_id, _session_timeout, turn, batch_idx)
                yield {"type": "error", "content": f"Session timeout ({_session_timeout}s) exceeded during tool execution.", "session_id": session_id, "turn": turn, "batch": batch_idx, "timeout_type": "intra_turn"}
                _stop_requested = True
                break

            # Prepare (tool_call_item, resolved_name, parsed_args) tuples
            # B11: Skip items already handled by dedup (they have _dedup_skip=True)
            prepared = []
            for tc in batch_tcs:
                if tc.get("_dedup_skip"):
                    continue
                raw_name = tc.get("function", {}).get("name", "")
                tool_name = tc.get("_tool_name") or TOOL_NAME_ALIASES.get(raw_name, raw_name)
                raw_args = tc.get("_tool_args", {})
                if not raw_args:
                    try:
                        raw_args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                    except (json.JSONDecodeError, ValueError):
                        raw_args = {}
                prepared.append((tc, tool_name, raw_args))

            # U12d-preempt: Auto-replace file_read with find_by_name when the
            # target path has already failed in a previous turn. Small models
            # ignore [TOOL CORRECTION] hints and retry the same path repeatedly.
            for _idx, (_pc_tc, _pc_tn, _pc_args) in enumerate(prepared):
                if _pc_tn == "file_read" and _pc_args.get("path") in _file_read_failed_paths:
                    _old_path = _pc_args["path"]
                    _search_name = os.path.basename(_old_path)
                    if _search_name:
                        # Replace file_read with find_by_name
                        _new_args = {"pattern": _search_name}
                        _pc_tc["_tool_name"] = "find_by_name"
                        _pc_tc["_tool_args"] = _new_args
                        _pc_tc["function"] = {
                            "name": "find_by_name",
                            "arguments": json.dumps(_new_args),
                        }
                        prepared[_idx] = (_pc_tc, "find_by_name", _new_args)
                        logger.info(f"[U12d-preempt] Auto-replaced file_read('{_old_path}') → "
                                    f"find_by_name(pattern='{_search_name}') (path already failed)")

            # U11c: Lock @explore — if user typed @explore, force agent_type="explore"
            _explore_lock = _re_explore_lock.match(_current_task_text.strip())
            if _explore_lock:
                for _pc_tc, _pc_tn, _pc_args in prepared:
                    if _pc_tn in ("sub_agent", "fork", "delegate", "spawn_agent"):
                        _old_type = _pc_args.get("agent_type", "")
                        if _old_type and _old_type != "explore":
                            _pc_args["agent_type"] = "explore"
                            logger.info(f"[U11c] Locked @explore: overrode agent_type '{_old_type}' → 'explore'")

            # ── Execute batch ──
            if is_concurrent and len(prepared) > 1:
                # P0: Concurrent read-only batch
                batch_start = time.time()

                # P2: Detect parallel sub_agent calls in this batch
                _SUB_AGENT_NAMES = frozenset({"sub_agent", "fork", "delegate", "spawn_agent"})
                _p2_sub_count = sum(1 for _, tn, _ in prepared if tn in _SUB_AGENT_NAMES)
                if _p2_sub_count >= 2:
                    logger.info(f"[P2] Swarm parallel dispatch: {_p2_sub_count} sub_agents in concurrent batch")

                # D2: Emit tool_start for all tools in concurrent batch
                for tc_item, tname, targs in prepared:
                    _args_preview = {}
                    try:
                        for k, v in list(targs.items())[:3]:
                            _args_preview[k] = str(v)[:120] if isinstance(v, str) else v
                    except Exception:
                        pass
                    yield {
                        "type": "tool_start",
                        "name": tname,
                        "arguments": _args_preview,
                        "turn": turn,
                    }
                    # P2: Emit sub_agent_start for sub_agent tools in concurrent batch
                    if tname in _SUB_AGENT_NAMES:
                        yield {
                            "type": "sub_agent_start",
                            "agent_type": targs.get("agent_type", "general"),
                            "task_preview": targs.get("task", "")[:100],
                            "turn": turn,
                        }

                async def _run_one(tc_item, tname, targs):
                    tc_id = tc_item.get("id", "")
                    t0 = time.time()
                    exec_args = dict(targs)
                    exec_args["_session_id"] = session_id
                    if tname in ASYNC_TOOLS:
                        res = await execute_tool_async(tname, exec_args, workspace)
                    else:
                        res = await asyncio.get_running_loop().run_in_executor(
                            None, execute_tool, tname, exec_args, workspace
                        )
                    logger.info(f"[AgenticLoop] {tname} completed in {time.time()-t0:.1f}s")
                    return tc_item, tname, res

                try:
                    logger.info(f"[AgenticLoop] Concurrent batch of {len(prepared)} read-only tools")
                    concurrent_tasks = [_run_one(tc, tn, ta) for tc, tn, ta in prepared]
                    results = await asyncio.gather(*concurrent_tasks, return_exceptions=True)
                    batch_elapsed = time.time() - batch_start

                    _sibling_errored = False
                    _sibling_error_tool = ""
                    _sibling_cancelled = False
                    for item in results:
                        if isinstance(item, Exception):
                            logger.warning(f"[AgenticLoop] Concurrent tool error: {item}")
                            continue
                        tc_item, tname, res = item
                        # P23: If a sibling already failed, cancel remaining
                        if _sibling_cancelled:
                            res = {"success": False, "output": "", "error": f"Cancelled: sibling tool {_sibling_error_tool} failed"}
                            logger.info(f"[P23] Cancelled {tname} due to sibling {_sibling_error_tool} failure")
                        elif _sibling_errored:
                            res["error"] = (res.get("error", "") or "") + f" (Note: {_sibling_error_tool} also failed in this batch)"
                        if res.get("error") and not res.get("output"):
                            _sibling_errored = True
                            _sibling_error_tool = tname
                            # P23: shell_execute failure aborts all remaining siblings
                            if tname == "shell_execute":
                                _sibling_cancelled = True
                                logger.info(f"[P23] shell_execute failed, cancelling remaining siblings")

                        result = res
                        tool_content = _postprocess_tool_content(
                            tname, result, turn, turn, workspace, session_id
                        )
                        messages.append({
                            "role": "tool", "tool_call_id": tc_item.get("id", ""),
                            "content": tool_content,
                            "_tool_name": tname, "_turn": turn,
                        })
                        tools_used.append(tname)
                        total_tool_calls += 1

                        yield {
                            "type": "tool_result",
                            "tool_name": tname,
                            "tool_call_id": tc_item.get("id", ""),
                            "content": tool_content,
                            "success": result.get("success", True),
                            "elapsed": batch_elapsed,
                            "dev_annotation": _build_dev_annotation(tname, result, tool_content),
                            "change_set": result.get("_change_set"),
                        }
                        _tracked_change_set_id = _record_change_set_state(result.get("_change_set"))
                        if _tracked_change_set_id and (result.get("_change_set") or {}).get("status") == "pending":
                            _turn_pending_change_set_ids.append(_tracked_change_set_id)
                        # P62: Emit todo_update SSE event
                        if result.get("_todo_update"):
                            yield {"type": "todo_update", "todos": result["_todo_update"]}
                        _tool_already_pushed_task_update = False
                        if result.get("_task_update"):
                            yield {"type": "task_update", **result["_task_update"]}
                            _tool_already_pushed_task_update = True
                        _task_advance_signal = _maybe_advance_task_state(tname, res, tc_item.get("_tool_args", {}), _turn_pending_change_set_ids)
                        if _task_advance_signal.get("_forced_verify"):
                            _pending_forced_verify = _task_advance_signal["_forced_verify"]
                        if _task_advance_signal.get("triggered") and not _tool_already_pushed_task_update:
                            yield {"type": "task_update", **_build_task_update_payload()}
                        # P2: Emit sub_agent_end and post-processing for sub_agent in concurrent batch
                        _is_sub_agent_conc = tname in _SUB_AGENT_NAMES
                        if _is_sub_agent_conc:
                            _sa_type_c = tc_item.get("_tool_args", {}).get("agent_type", "general")
                            yield {
                                "type": "sub_agent_end",
                                "agent_type": _sa_type_c,
                                "success": res.get("success", True),
                                "elapsed": batch_elapsed,
                                "turn": turn,
                            }
                            # U3: background_task_start
                            if res.get("_background_task_id"):
                                yield {
                                    "type": "background_task_start",
                                    "task_id": res["_background_task_id"],
                                    "agent_type": _sa_type_c,
                                    "task_preview": tc_item.get("_tool_args", {}).get("task", "")[:100],
                                    "turn": turn,
                                }
                            # D5: Patch approval nudge for edit sub-agent
                            if _sa_type_c == "edit" and res.get("success"):
                                _patch_out_c = res.get("output", "")
                                if "```diff" in _patch_out_c or "--- a/" in _patch_out_c:
                                    messages.append({
                                        "role": "system",
                                        "content": (
                                            "[PATCH APPROVAL REQUIRED] The edit sub-agent has proposed "
                                            "a patch in unified diff format. You MUST:\n"
                                            "1. Present the complete diff to the user clearly\n"
                                            "2. Explain what the patch does in 1-2 sentences\n"
                                            "3. Ask the user for explicit approval before applying it\n"
                                            "4. Do NOT apply the patch (via file_edit) until the user says yes\n"
                                            "If the user approves, apply the patch using file_edit."
                                        ),
                                        "_turn": turn,
                                    })
                            # U11c: Explore output suppression
                            if _sa_type_c == "explore" and res.get("success"):
                                _explore_out_c = res.get("output", "")
                                _is_quick_c = res.get("_quick_path", False) or "Quick-path" in _explore_out_c
                                if "produced no text output" in _explore_out_c or len(_explore_out_c.strip()) < 50:
                                    messages.append({
                                        "role": "system",
                                        "content": (
                                            "[EXPLORE DONE] The explore sub-agent finished but returned minimal output. "
                                            "Synthesize a direct answer for the user. "
                                            "Do NOT re-run the exploration or call more tools — just answer."
                                        ),
                                        "_turn": turn,
                                    })
                                else:
                                    _suppress_c = (
                                        "[EXPLORE COMPLETE] Present the sub-agent's findings directly. "
                                        "Relay the KEY INFORMATION ONLY — do NOT:"
                                    )
                                    if _is_quick_c:
                                        _suppress_c += (
                                            "\n- Re-format, re-organize, or add tables/headers"
                                            "\n- Add architecture analysis or module descriptions"
                                            "\n- Expand a simple file list into a detailed breakdown"
                                            "\nJust state the answer in 1-3 sentences."
                                        )
                                    else:
                                        _suppress_c += (
                                            "\n- Duplicate the sub-agent's bullet points"
                                            "\n- Add your own analysis or expand on the findings"
                                            "\n- Create tables or formatted summaries"
                                            "\nKeep your response under 5 lines."
                                        )
                                    messages.append({"role": "system", "content": _suppress_c, "_turn": turn})
                            # U2: Verify verdict nudge
                            if _sa_type_c == "verify" and res.get("success"):
                                _verify_out_c = res.get("output", "")
                                if "VERDICT: FAIL" in _verify_out_c:
                                    messages.append({
                                        "role": "system",
                                        "content": (
                                            "[VERIFICATION FAILED] The verify sub-agent found issues. You MUST:\n"
                                            "1. Present the FAIL findings to the user clearly\n"
                                            "2. Identify the root cause of each failure\n"
                                            "3. Fix the issues, then re-verify\n"
                                            "Do NOT claim the task is complete until verification passes."
                                        ),
                                        "_turn": turn,
                                    })
                                elif "VERDICT: PARTIAL" in _verify_out_c:
                                    messages.append({
                                        "role": "system",
                                        "content": (
                                            "[VERIFICATION PARTIAL] The verify sub-agent could not fully verify. "
                                            "Report what was verified and what could not be checked, "
                                            "so the user can manually verify the remaining items."
                                        ),
                                        "_turn": turn,
                                    })

                        # A2/D3: Track file activity (reads + writes) for workbench awareness
                        _track_file_activity(tname, res, tc_item.get("_tool_args", {}), turn)
                        # A3: Track failures for rollback memory
                        if res.get("error") and not res.get("success", True):
                            _record_tool_failure(tname, tc_item.get("_tool_args", {}), res["error"], turn)
                        else:
                            # Record successful call for dedup (failed calls can be retried)
                            _dedup_sig = tc_item.get("_call_sig")
                            if _dedup_sig and _dedup_sig not in _recent_tool_calls[-20:]:
                                _recent_tool_calls.append(_dedup_sig)
                except Exception as e:
                    logger.error(f"[AgenticLoop] Concurrent batch error: {e}")
            else:
                # Sequential execution
                for tc_item, tname, targs in prepared:
                    tc_id = tc_item.get("id", "")
                    # D2: Emit tool_start event
                    _args_preview_seq = {}
                    try:
                        for k, v in list(targs.items())[:3]:
                            _args_preview_seq[k] = str(v)[:120] if isinstance(v, str) else v
                    except Exception:
                        pass
                    yield {
                        "type": "tool_start",
                        "name": tname,
                        "arguments": _args_preview_seq,
                        "turn": turn,
                    }
                    # D2: Emit sub_agent_start for UI banner
                    _is_sub_agent = tname in ("sub_agent", "fork", "delegate", "spawn_agent")
                    if _is_sub_agent:
                        _sa_type = targs.get("agent_type", "general")
                        _sa_task_preview = targs.get("task", "")[:100]
                        yield {
                            "type": "sub_agent_start",
                            "agent_type": _sa_type,
                            "task_preview": _sa_task_preview,
                            "turn": turn,
                        }

                    exec_start = time.time()
                    exec_args = dict(targs)
                    exec_args["_session_id"] = session_id
                    try:
                        if tname in ASYNC_TOOLS:
                            result = await execute_tool_async(tname, exec_args, workspace)
                        else:
                            result = await asyncio.get_running_loop().run_in_executor(
                                None, execute_tool, tname, exec_args, workspace
                            )
                    except Exception as e:
                        result = {"success": False, "output": "", "error": str(e)}

                    exec_elapsed = time.time() - exec_start

                    # D2: Emit sub_agent_end when done
                    if _is_sub_agent:
                        yield {
                            "type": "sub_agent_end",
                            "agent_type": _sa_type,
                            "success": result.get("success", True),
                            "elapsed": exec_elapsed,
                            "turn": turn,
                        }

                    tool_content = _postprocess_tool_content(
                        tname, result, turn, turn, workspace, session_id
                    )
                    messages.append({
                        "role": "tool", "tool_call_id": tc_id,
                        "content": tool_content,
                        "_tool_name": tname, "_turn": turn,
                    })
                    tools_used.append(tname)
                    total_tool_calls += 1

                    yield {
                        "type": "tool_result",
                        "tool_name": tname,
                        "tool_call_id": tc_id,
                        "content": tool_content,
                        "success": result.get("success", True),
                        "elapsed": exec_elapsed,
                        "dev_annotation": _build_dev_annotation(tname, result, tool_content),
                        "change_set": result.get("_change_set"),
                    }
                    _tracked_change_set_id = _record_change_set_state(result.get("_change_set"))
                    if _tracked_change_set_id and (result.get("_change_set") or {}).get("status") == "pending":
                        _turn_pending_change_set_ids.append(_tracked_change_set_id)
                    # P62: Emit todo_update SSE event
                    if result.get("_todo_update"):
                        yield {"type": "todo_update", "todos": result["_todo_update"]}
                    _tool_already_pushed_task_update = False
                    if result.get("_task_update"):
                        yield {"type": "task_update", **result["_task_update"]}
                        _tool_already_pushed_task_update = True
                    _task_advance_signal = _maybe_advance_task_state(tname, result, targs, _turn_pending_change_set_ids)
                    if _task_advance_signal.get("_forced_verify"):
                        _pending_forced_verify = _task_advance_signal["_forced_verify"]
                    if _task_advance_signal.get("triggered") and not _tool_already_pushed_task_update:
                        yield {"type": "task_update", **_build_task_update_payload()}

                    # U3: Emit background_task_start when sub_agent launches a background task
                    if _is_sub_agent and result.get("_background_task_id"):
                        yield {
                            "type": "background_task_start",
                            "task_id": result["_background_task_id"],
                            "agent_type": _sa_type,
                            "task_preview": targs.get("task", "")[:100],
                            "turn": turn,
                        }

                    # D5: Inject approval nudge when edit sub-agent returns a patch
                    if _is_sub_agent and _sa_type == "edit" and result.get("success"):
                        _patch_output = result.get("output", "")
                        if "```diff" in _patch_output or "--- a/" in _patch_output:
                            messages.append({
                                "role": "system",
                                "content": (
                                    "[PATCH APPROVAL REQUIRED] The edit sub-agent has proposed "
                                    "a patch in unified diff format. You MUST:\n"
                                    "1. Present the complete diff to the user clearly\n"
                                    "2. Explain what the patch does in 1-2 sentences\n"
                                    "3. Ask the user for explicit approval before applying it\n"
                                    "4. Do NOT apply the patch (via file_edit) until the user says yes\n"
                                    "If the user approves, apply the patch using file_edit."
                                ),
                                "_turn": turn,
                            })
                            logger.info(f"[D5] Injected patch approval nudge for edit sub-agent at turn {turn}")

                    # U11c: Explore output suppression — prevent main model from re-analyzing
                    if _is_sub_agent and _sa_type == "explore" and result.get("success"):
                        _explore_output = result.get("output", "")
                        _is_quick = result.get("_quick_path", False) or "Quick-path" in _explore_output
                        if "produced no text output" in _explore_output or len(_explore_output.strip()) < 50:
                            # Empty/minimal output — tell model to conclude from what it knows
                            messages.append({
                                "role": "system",
                                "content": (
                                    "[EXPLORE DONE] The explore sub-agent finished but returned minimal output. "
                                    "Synthesize a direct answer for the user. "
                                    "Do NOT re-run the exploration or call more tools — just answer."
                                ),
                                "_turn": turn,
                            })
                            logger.info(f"[U11c] Explore empty output — injected conclude nudge")
                        else:
                            # Normal output — suppress verbose re-analysis
                            _suppress_msg = (
                                "[EXPLORE COMPLETE] Present the sub-agent's findings directly. "
                                "Relay the KEY INFORMATION ONLY — do NOT:"
                            )
                            if _is_quick:
                                _suppress_msg += (
                                    "\n- Re-format, re-organize, or add tables/headers"
                                    "\n- Add architecture analysis or module descriptions"
                                    "\n- Expand a simple file list into a detailed breakdown"
                                    "\nJust state the answer in 1-3 sentences."
                                )
                            else:
                                _suppress_msg += (
                                    "\n- Duplicate the sub-agent's bullet points"
                                    "\n- Add your own analysis or expand on the findings"
                                    "\n- Create tables or formatted summaries"
                                    "\nKeep your response under 5 lines."
                                )
                            messages.append({
                                "role": "system", "content": _suppress_msg, "_turn": turn,
                            })
                            logger.info(f"[U11c] Explore output suppression injected (quick={_is_quick})")

                    # U2: Inject verdict nudge when verify sub-agent returns
                    if _is_sub_agent and _sa_type == "verify" and result.get("success"):
                        _verify_output = result.get("output", "")
                        if "VERDICT: FAIL" in _verify_output:
                            messages.append({
                                "role": "system",
                                "content": (
                                    "[VERIFICATION FAILED] The verify sub-agent found issues. You MUST:\n"
                                    "1. Present the FAIL findings to the user clearly\n"
                                    "2. Identify the root cause of each failure\n"
                                    "3. Fix the issues, then re-verify\n"
                                    "Do NOT claim the task is complete until verification passes."
                                ),
                                "_turn": turn,
                            })
                            logger.info(f"[U2] Verify sub-agent returned FAIL — injected fix nudge at turn {turn}")
                        elif "VERDICT: PASS" in _verify_output:
                            logger.info(f"[U2] Verify sub-agent returned PASS at turn {turn}")
                        elif "VERDICT: PARTIAL" in _verify_output:
                            messages.append({
                                "role": "system",
                                "content": (
                                    "[VERIFICATION PARTIAL] The verify sub-agent could not fully verify. "
                                    "Report what was verified and what could not be checked, "
                                    "so the user can manually verify the remaining items."
                                ),
                                "_turn": turn,
                            })
                            logger.info(f"[U2] Verify sub-agent returned PARTIAL at turn {turn}")

                    # A2/D3: Track file activity (reads + writes) for workbench awareness
                    _track_file_activity(tname, result, targs, turn)
                    # A3: Track failures for rollback memory
                    if result.get("error") and not result.get("success", True):
                        _record_tool_failure(tname, targs, result["error"], turn)
                    else:
                        # Record successful call for dedup (failed calls can be retried)
                        _dedup_sig = tc_item.get("_call_sig")
                        if _dedup_sig and _dedup_sig not in _recent_tool_calls[-20:]:
                            _recent_tool_calls.append(_dedup_sig)

                    # U12d: Tool selection correction — when file_read fails with
                    # "not found", inject proactive guidance to use find_by_name
                    _err_lower = (result.get("error", "") or "").lower()
                    if tname == "file_read" and ("not found" in _err_lower or "no such file" in _err_lower):
                        _failed_path = targs.get("path", "")
                        _fname = os.path.basename(_failed_path) if _failed_path else ""
                        if _failed_path:
                            _file_read_failed_paths.add(_failed_path)
                        if _fname:
                            messages.append({
                                "role": "system",
                                "content": (
                                    f"[TOOL CORRECTION] file_read failed because '{_failed_path}' "
                                    f"does not exist. Do NOT guess another path. Instead call: "
                                    f"find_by_name(pattern=\"{_fname}\") to locate it first, "
                                    f"then file_read the correct path from the results."
                                ),
                                "_turn": turn,
                            })
                            logger.info(f"[U12d] file_read not-found → injected find_by_name correction for '{_fname}'")

                    # Track empty grep searches
                    if tname == "grep_search":
                        if "No matches found" in (result.get("output", "") or ""):
                            _consecutive_empty_searches += 1
                        else:
                            _consecutive_empty_searches = 0

        if _pending_forced_verify:
            try:
                async for ev in _run_forced_verify_subagent(turn, _pending_forced_verify):
                    yield ev
                if _root_task is not None and _root_task.is_terminal:
                    break
                continue
            except Exception as _verify_exc:
                logger.warning(f"[VerifyGate] Forced verify execution failed: {_verify_exc}")

        if _turn_pending_change_set_ids:
            # ApprovalGate: pending change_sets were created this turn.
            # Stop the loop immediately — user must approve/reject before any more turns.
            if assistant_text and _has_premature_transactional_edit_claim(assistant_text):
                if messages and messages[-1] is assistant_msg:
                    messages.pop()
                yield {"type": "clear_generation"}
            logger.info(f"[ApprovalGate] Stopping agentic loop at turn {turn}; "
                        f"pending change sets created this turn: {sorted(set(_turn_pending_change_set_ids))}")
            logger.info(
                "[ApprovalDiag] gate_stop session=%s turn=%s turn_pending_ids=%s pending_cache=%s assistant_len=%s",
                session_id,
                turn,
                sorted(set(_turn_pending_change_set_ids)),
                sorted(_pending_change_sets.keys()),
                len(assistant_text or ""),
            )
            break

        # ── Post-batch checks ──
        # P44: Aggregate budget — if total tool output this turn exceeds budget, persist excess
        last_msgs = [m for m in messages if m.get("_turn") == turn and m.get("role") == "tool"]
        total_turn_chars = sum(len(m.get("content", "")) for m in last_msgs)
        if total_turn_chars > _MAX_RESULTS_PER_MESSAGE and workspace and session_id:
            # Sort by size descending, persist the largest until under budget
            sized = sorted(last_msgs, key=lambda m: len(m.get("content", "")), reverse=True)
            reduced = 0
            for msg in sized:
                if total_turn_chars <= _MAX_RESULTS_PER_MESSAGE:
                    break
                content = msg.get("content", "")
                # P101a: Skip file_read — it self-bounds and persisting creates
                # a circular read→file→read loop (Claw's Infinity pattern)
                if len(content) > 5000 and msg.get("_tool_name") != "file_read":
                    persisted = _persist_large_result(
                        msg.get("_tool_name", "tool"), content, workspace, session_id
                    )
                    old_len = len(content)
                    msg["content"] = persisted
                    total_turn_chars -= (old_len - len(persisted))
                    reduced += 1
                    logger.info(f"[P44] Persisted {msg.get('_tool_name', '?')} ({old_len:,} chars) to meet aggregate budget")

        # U11d: If @explore active but model handled directly (no sub_agent call),
        # inject output suppression to prevent verbose re-analysis
        if _u11d_explore_task and not _u11d_explore_preempted:
            _had_sub_agent_this_turn = any(
                tn in ("sub_agent", "fork", "delegate", "spawn_agent")
                for _, tn, _ in prepared
            )
            if not _had_sub_agent_this_turn and last_msgs:
                _already_suppressed = any(
                    "EXPLORE COMPLETE" in m.get("content", "") or "EXPLORE QUICK-PATH" in m.get("content", "")
                    for m in messages if m.get("role") == "system"
                )
                if not _already_suppressed:
                    messages.append({
                        "role": "system",
                        "content": (
                            "[EXPLORE COMPLETE] You handled the @explore request directly. "
                            "Present results in 1-3 SHORT sentences. Do NOT:"
                            "\n- Re-organize results into categories with emoji headers"
                            "\n- Add architecture analysis or module descriptions"
                            "\n- Expand a simple file listing into a detailed breakdown"
                            "\n- Describe dependency categories or analyze file contents"
                            "\nJust state the factual answer briefly."
                        ),
                        "_turn": turn,
                    })
                    logger.info(f"[U11d] Direct-path @explore suppression injected at turn {turn}")

        # U1: Track tool calls for session memory extraction
        if _session_mem is not None:
            _batch_tool_count = len(last_msgs) if last_msgs else 0
            for _ in range(_batch_tool_count):
                _session_mem.record_tool_call()
            if _session_mem.should_extract():
                try:
                    from session_memory import extract_session_notes
                    extract_session_notes(messages, _session_mem)
                except Exception as _e_sm:
                    logger.debug(f"[U1] Session memory extraction failed: {_e_sm}")

        # P36: Completeness nudge — remind model about unaddressed sub-tasks
        has_hint = False
        nudge = _build_completeness_nudge(_current_task_text, tools_used, turn)
        if nudge and turn <= 3:
            nudge_count = sum(1 for m in messages if m.get("role") == "system" and "sub-tasks" in m.get("content", ""))
            if nudge_count < 2:
                messages.append({"role": "system", "content": nudge})
                has_hint = True
                logger.info(f"[AgenticLoop] Injected completeness nudge #{nudge_count + 1}")

        # P29: Scrub pre-tool narration (I will..., Let me...)
        narration_count = 0
        if assistant_text and _NARRATION_RE.search(assistant_text):
            narration_count += 1

        # Empty search hint
        if _consecutive_empty_searches >= _MAX_EMPTY_SEARCHES:
            messages.append({
                "role": "system",
                "content": (
                    f"{_consecutive_empty_searches} consecutive grep searches returned "
                    f"no results. STOP searching with similar patterns. Instead: "
                    f"1) Try file_read on likely files to inspect them directly, OR "
                    f"2) Answer the user with the information you already have. "
                    f"Do NOT call grep_search again with a variation of the same keywords."
                ),
            })
            logger.info(f"[AgenticLoop] Injected search hint after {_consecutive_empty_searches} empty searches")
            _consecutive_empty_searches = 0

        # P29/B2: Inject narration scrub hint (all turns, not just turn >= 2)
        if narration_count > 0:
            # Check if we already have a narration hint
            if not any(m.get("content", "").startswith("[NO NARRATION]") for m in messages if m.get("role") == "system"):
                messages.append({
                    "role": "system",
                    "content": (
                        "[NO NARRATION] Do NOT write 'I will...', 'Let me...', '我将...' "
                        "before tool calls. Just call tools directly. Do NOT repeat tool "
                        "output in your response — the user already sees tool results. "
                        "Give a BRIEF summary (≤100 words) with key findings only."
                    ),
                })
                logger.info(f"[P29] Scrubbed pre-tool narration ({narration_count} instances)")

        # P30: Post-tool conciseness injection
        conciseness_count = sum(
            1 for m in messages
            if m.get("role") == "system" and "[CONCISE RESPONSE]" in m.get("content", "")
        )
        if turn >= 3 and conciseness_count == 0 and total_tool_calls >= 3:
            messages.append({
                "role": "system",
                "content": (
                    "[CONCISE RESPONSE] CRITICAL OUTPUT RULES:\n"
                    "1. The user ALREADY sees all tool outputs above. Do NOT repeat file contents or search results.\n"
                    "2. Do NOT write section-by-section analysis of each tool result.\n"
                    "3. If the user asked for a table, write ONLY the table — no extra paragraphs before or after.\n"
                    "4. If the user asked a question, write ONLY the answer — 1-3 sentences max.\n"
                    "5. If the user asked to do something (edit/create/fix), confirm what you did in ≤50 words.\n"
                    "6. NEVER start with 'Based on the tool results...' or 'Here is a summary...'\n"
                    "7. NEVER explain what each tool found — the user already sees the results.\n"
                    "8. Your text response should add INSIGHT, not repeat data.\n"
                    "9. Total response ≤150 words unless user explicitly asked for a detailed analysis."
                ),
            })
            logger.info(f"[P30] Injected conciseness reminder at turn {turn}")

        # Update budget estimate
        budget.update_from_estimate(messages, assistant_text)

    # ── Loop finished — save state ──
    _save_last_response_time(_last_response_file)

    # ── End-of-loop task completion promotion ──
    # When the loop ends naturally with a final answer and no pending approval,
    # promote the root task (and its in-progress children) to completed.
    # This prevents the task panel from showing "in_progress" after the agent
    # has clearly finished all work.
    if _root_task is not None and _last_final_answer_payload and not _pending_change_sets:
        try:
            _fin_store = get_task_store(session_id, workspace)
            _fin_root = _fin_store.get_task(_root_task.id)
            if _fin_root and _fin_root.state in ("in_progress", "planned", "verifying"):
                # Also mark any in_progress/planned children as completed
                _fin_children = _fin_store.get_children(_fin_root.id)
                for _fin_child in _fin_children:
                    if _fin_child.state in ("in_progress", "planned", "created"):
                        _fin_store._set_state(
                            _fin_child, "completed",
                            source="agentic_loop_finalization",
                            reason="loop ended with final answer",
                        )
                        _fin_store.upsert_task(_fin_child)
                # Promote root to completed
                _fin_store._set_state(
                    _fin_root, "completed",
                    source="agentic_loop_finalization",
                    reason="loop ended naturally with final answer; no pending approvals",
                )
                _fin_store.upsert_task(_fin_root)
                _fin_store.save()
                _root_task = _fin_root
                logger.info(
                    "[TaskFinalize] %s → completed (loop ended with final answer, "
                    "no pending change sets)",
                    _fin_root.id,
                )
        except Exception as _fin_exc:
            logger.debug("[TaskFinalize] End-of-loop promotion failed (non-fatal): %s", _fin_exc)

    # Yield final stats
    _pending_change_set_ids = sorted(_pending_change_sets.keys())
    _done_event = {
        "type": "agentic_done",
        "tools_used": tools_used,
        "total_tool_calls": total_tool_calls,
        "turns": locals().get("turn", 0),
        "budget_stats": budget.get_stats(),
        "approval_pending": bool(_pending_change_set_ids),
        "pending_change_set_ids": _pending_change_set_ids,
        "hint_reread_count": _HINT_REREAD_COUNT,
    }
    if _HINT_REREAD_COUNT > _HINT_REREAD_WARN_THRESHOLD:
        logger.warning(f"[Observability] Session ended with {_HINT_REREAD_COUNT} file re-reads (threshold {_HINT_REREAD_WARN_THRESHOLD}) — model may be ignoring context hints")
    try:
        _done_event["task_context"] = get_current_task_summary(session_id, workspace)
        if _root_task is not None:
            _done_event["task_id"] = _root_task.id
            _done_event["task_state"] = _root_task.state
    except Exception as e:
        logger.debug(f"[TaskStore] Failed to attach task summary to done event: {e}")
    if _last_final_answer_payload:
        _done_event["final_answer"] = _last_final_answer_payload
    logger.info(
        "[ApprovalDiag] done_event session=%s approval_pending=%s pending_ids=%s final_answer_len=%s tools=%s turns=%s",
        session_id,
        _done_event["approval_pending"],
        _done_event["pending_change_set_ids"],
        len((_last_final_answer_payload or {}).get("content", "") or ""),
        tools_used,
        _done_event["turns"],
    )
    # P102: Include skill mode metadata and enforcement stats if active
    if _skill_mode:
        _done_event["skill_mode"] = _skill_mode
        _done_event["write_policy"] = _skill_write_policy
        _done_event["verification_ran"] = _has_run_verification
        _done_event["criteria_nudges"] = _p102_criteria_nudges
        _done_event["disallowed_tools"] = _skill_disallowed_tools
        # D6: Suggest natural next mode transition
        _next_mode_map = {
            "analyze": "refactor",   # analyzed → ready to change
            "refactor": "verify",    # changed  → should verify
            "debug": "refactor",     # diagnosed → ready to fix
            "verify": None,          # verified  → done
        }
        _suggested = _next_mode_map.get(_skill_mode)
        if _suggested:
            _done_event["suggested_next_mode"] = _suggested
    yield _done_event
