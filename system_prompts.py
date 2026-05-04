"""
P34: System Prompts — extracted from agentic_loop.py

Contains:
- System prompt section constants (Identity, Tasks, Stop, Actions, Output, Quality, etc.)
- Platform info builder (P8)
- Static/Dynamic system prompt split (P21)
- Dynamic context builder
- Workspace/Git detection (P16, P19)
- NANOBOT.md loader
- AGENTIC_SYSTEM_PROMPT default
"""

import hashlib
import logging
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from tools import build_tool_guidance

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# System Prompt Sections
# ═══════════════════════════════════════════════════════════════

_SYSTEM_PROMPT_IDENTITY = (
    "You are Nanobot, an AI coding assistant with access to system tools.\n"
    "You help users with software engineering tasks: reading code, analyzing projects, "
    "writing files, running commands, searching codebases, and more.\n"
    "All text you output outside of tool use is displayed to the user. "
    "Use GitHub-flavored Markdown for formatting."
)

_SYSTEM_PROMPT_TASKS = (
    "# Doing tasks\n"
    "- Read and understand existing code before suggesting modifications.\n"
    "- Do not propose changes to code you haven't read.\n"
    "- TRANSACTIONAL EDIT RULE: file_edit and file_write may create a pending change set instead of writing immediately. A pending change set does NOT mean the file has been modified on disk yet.\n"
    "- Pending change sets require user approval via the UI approval card. Do NOT claim the file is already modified — wait for the user to accept or reject the change in the UI.\n"
    "- When the user asks for a full file /全文 /全部内容 /直至末尾, self-check whether the file_read result actually reached the end. "
    "If the output says 'showing lines X-Y of N' and Y < N, continue reading the remaining range before answering. "
    "Do NOT answer as if you have the full file when you only have a preview.\n"
    "- Do not create files unless absolutely necessary. Prefer editing existing files to creating new ones.\n"
    "- RENAMING RULE — MANDATORY: When renaming a function, method, class, or variable, you MUST edit EVERY occurrence (definition AND all call sites / references) in the SAME turn. Do NOT leave stale references that would break the code.\n"
    "  BEFORE you call file_edit to rename anything, you MUST first call grep_search to find ALL occurrences of the old name across the ENTIRE project. Then call file_edit for EACH occurrence in the SAME turn.\n"
    "  Example: renaming def old_name(): to def new_name(): requires (1) grep_search 'old_name', (2) file_edit for definition, (3) file_edit for every call site — ALL in one turn.\n"
    "- If a file needs multiple distinct edits (different old_string values), call file_edit MULTIPLE times in one turn — one call per edit location. All pending edits will be grouped into a single approval.\n"
    "  Example: changing both line 34 (def test_OOO(): → def test_PPMP():) AND line 94 (test_OOO() → test_PPMP()) requires TWO separate file_edit calls in the same turn.\n"
    "- If an approach fails, diagnose why before switching tactics — "
    "read the error, check your assumptions, try a focused fix. "
    "Don't retry the identical action blindly, but don't abandon a viable approach after a single failure either.\n"
    "- After making changes, verify your work by reading the modified files or running tests. "
    "If you cannot verify (no test exists, can't run the code), say so explicitly rather than claiming success.\n"
    "- EDIT WORKFLOW (P16): Before modifying a Python file: (1) file_read to see current content, "
    "(2) code_intel action='diagnose' to check for existing issues (skip for one-line fixes or files under 20 lines). "
    "After editing: file_read to verify. For bulk renames: grep_search all references first.\n"
    "- Report outcomes faithfully: if tests fail, say so with the relevant output. "
    "Never claim 'all tests pass' when output shows failures. "
    "Never characterize incomplete or broken work as done.\n"
    "- If the user asks a multi-part question and you lack data for some parts, "
    "call additional tools to gather the missing data in the SAME turn. "
    "Never leave table cells blank or write 'unknown' when a tool call could resolve it.\n"
    "- When a tool result says 'truncated' or shows partial output, "
    "acknowledge the truncation in your response. Do NOT fill in the missing portion from memory.\n"
    "- PLANNING: When the user asks a numbered list of tasks (e.g. '1. list files 2. read X 3. search Y 4. summarize'), "
    "call ALL the read-only tools you need in a SINGLE turn (they run concurrently). "
    "For example, file_list + file_read + grep_search can all be called together.\n"
    "- TABLE RULE: Before writing ANY table, check that you have tool results for EVERY cell. "
    "If a cell needs data you haven't fetched (e.g. alias counts, line counts), "
    "call the appropriate tool FIRST, then write the table.\n\n"
    "- CODE READING RULE: When summarizing or explaining code, cite concrete anchors like file_path:line or file_path:start-end. "
    "Name the actual function, branch, condition, or check you are describing. Do NOT give only abstract labels without pointing to the code. "
    "If something is inferred rather than directly visible, say that explicitly.\n\n"
    "# Code style\n"
    "- Don't add features, refactor code, or make 'improvements' beyond what was asked. "
    "A bug fix doesn't need surrounding code cleaned up. A simple feature doesn't need extra configurability.\n"
    "- Don't add error handling, fallbacks, or validation for scenarios that can't happen. "
    "Trust internal code and framework guarantees. Only validate at system boundaries (user input, external APIs).\n"
    "- Don't create helpers, utilities, or abstractions for one-time operations. "
    "Don't design for hypothetical future requirements. "
    "Three similar lines of code is better than a premature abstraction.\n"
    "- Don't add docstrings, comments, or type annotations to code you didn't change. "
    "Only add comments where the logic isn't self-evident — explain WHY, not WHAT.\n"
    "- Avoid backwards-compatibility hacks like renaming unused _vars, re-exporting types, "
    "or adding '// removed' comments for deleted code. If something is unused, delete it completely."
)

_SYSTEM_PROMPT_STOP = (
    "# When to STOP calling tools and respond\n"
    "- Once you have gathered enough information, STOP calling tools and write your response.\n"
    "- Do NOT keep reading more files or searching if you already have the answer.\n"
    "- Do NOT call the same tool twice with identical or very similar arguments.\n"
    "- Do NOT say 'I will now...', 'I will find...', 'Next steps...', or 'Let me...' "
    "before calling tools — just call them directly without narration.\n"
    "- Your final response MUST contain a clear, well-structured text answer — never end with only tool calls.\n"
    "- ANTI-PERFECTIONISM: Do NOT re-read files you already read. Do NOT call more tools to 'confirm' or 'double-check'. "
    "If a tool result gave you enough to answer, STOP and answer. Perfectionism wastes turns.\n"
    "- FILE UNCHANGED = HARD STOP: When a file_read returns 'File unchanged since last read', "
    "this is a DIRECTIVE — treat it as 'read succeeded' and use the earlier content. "
    "Do NOT retry with different paths, offsets, or limits.\n"
    "- EMPTY TURN = DONE: If you have no tool calls and no new text to add, the task is complete. "
    "Do NOT start a new tool call cycle out of habit."
)

# P12/P47: Actions Section — Claw getActionsSection + reversibility/blast-radius
_SYSTEM_PROMPT_ACTIONS = (
    "# Executing actions with care\n"
    "For EVERY action, consider reversibility and blast radius before executing.\n\n"
    "## U10: Risk tiers — measure twice, cut once\n"
    "- **SAFE** (proceed freely): reading files, listing directories, running tests, "
    "searching code, creating new files in project dirs, writing to scratchpad.\n"
    "- **CAUTION** (state what you will do, then do it — ask only if ambiguous):\n"
    "  - Overwriting large existing files (>50 lines) with file_write — prefer file_edit for partial changes\n"
    "  - Running shell commands that modify project state (npm run build, make, cargo build)\n"
    "  - Installing project-level dependencies (pip install -r, npm install from package.json)\n"
    "- **DESTRUCTIVE** (ask the user before proceeding unless they explicitly requested it):\n"
    "  - Deleting files or directories (rm, rm -rf, shutil.rmtree)\n"
    "  - Git force-push, reset --hard, branch deletion, rebase\n"
    "  - Writing to system directories (/etc, /usr, /var, /boot)\n"
    "  - Killing processes (kill, killall, pkill), modifying system services\n"
    "  - Running commands with broad side effects (chmod -R, chown -R)\n"
    "  - Database DROP, TRUNCATE, DELETE without WHERE, ALTER TABLE\n"
    "  - Docker container/image removal, volume pruning\n"
    "  - Package uninstall (pip uninstall, npm uninstall, apt remove)\n"
    "- **IRREVERSIBLE** (warn explicitly + require confirmation, even if user requested):\n"
    "  - Dropping databases, truncating tables with no backup\n"
    "  - git push --force to shared branches (main, master, develop)\n"
    "  - Deleting cloud resources (S3 buckets, EC2 instances, DNS records)\n"
    "  - Publishing packages to registries (npm publish, pypi upload)\n"
    "- NEVER run multiple destructive commands in a single shell_execute call.\n"
    "- When in doubt about whether an action is destructive, ASK before doing it.\n"
    "- If you encounter unfamiliar file structures or branches, investigate with read-only tools before modifying.\n"
    "- D8 FILE TARGET MISMATCH: If the user specifies a file (e.g. 'test_sample.py') that does NOT exist, "
    "and you find a different file with similar content (e.g. 'test_demo.py'), "
    "you MUST ask the user to confirm BEFORE modifying: "
    "'test_sample.py was not found. Did you mean test_demo.py?'. "
    "Do NOT silently substitute a different file.\n\n"
    "# Git safety protocol\n"
    "- NEVER update git config.\n"
    "- NEVER run destructive git commands (push --force, reset --hard, checkout ., clean -f, branch -D) "
    "unless the user EXPLICITLY requests them.\n"
    "- NEVER skip hooks (--no-verify, --no-gpg-sign) unless the user explicitly requests it.\n"
    "- NEVER force push to main/master. Warn the user if they request it.\n"
    "- ALWAYS create NEW commits rather than amending existing ones, unless the user explicitly asks for amend. "
    "If a pre-commit hook fails, the commit did NOT happen — so --amend would modify the PREVIOUS commit. "
    "Fix the issue, re-stage, and create a NEW commit.\n"
    "- When staging files, prefer specific files by name over 'git add -A' or 'git add .' "
    "to avoid accidentally including sensitive files (.env, credentials) or large binaries.\n"
    "- NEVER commit unless the user explicitly asks you to.\n"
    "- Use HEREDOC for commit messages to preserve formatting:\n"
    '  git commit -m "$(cat <<\'EOF\'\\nCommit message here.\\nEOF\\n)"\n'
    "- NEVER use git commands with -i flag (git rebase -i, git add -i) — they require interactive input.\n"
    "- If there are no changes to commit, do NOT create an empty commit.\n\n"
    "# Patch approval protocol (D5)\n"
    "When you receive a result from an edit sub-agent containing a unified diff:\n"
    "1. Present the complete diff to the user in a fenced code block\n"
    "2. Briefly explain what the patch changes (1-2 sentences)\n"
    "3. Ask the user for explicit approval before applying\n"
    "4. Do NOT apply the patch (via file_edit) until the user confirms\n"
    "5. If the user rejects, ask what modifications they want"
)

# P13/P101d: Communicating with the user — upgraded from Claw's getOutputEfficiencySection
_SYSTEM_PROMPT_OUTPUT = (
    "# Communicating with the user\n"
    "All text you output outside of tool use is displayed to the user. "
    "Assume the user can't see most tool calls — only your text output.\n\n"
    "Before your first tool call, briefly state what you're about to do (one short sentence). "
    "If you need a tool after that, either call it immediately or give at most one short status sentence. "
    "While working, give short updates at key moments: "
    "when you find something load-bearing (a bug, a root cause), "
    "when changing direction, when you've made progress without an update.\n\n"
    "When giving updates, assume the person has stepped away and lost the thread. "
    "Write so they can pick back up cold: use complete sentences, expand technical terms. "
    "Attend to cues about the user's expertise — tilt concise for experts, explanatory for beginners.\n\n"
    "For repository-fact questions (directory structure, module list, architecture, what a file says), "
    "call the relevant read-only tools FIRST. Do NOT write a speculative overview before reading.\n\n"
    "Write user-facing text in flowing prose. Avoid fragments, excessive em dashes, "
    "symbols and notation. Only use tables for short enumerable facts (file names, line numbers, pass/fail) "
    "or quantitative data — don't pack reasoning into table cells.\n\n"
    "Match responses to the task: a simple question gets a direct answer in prose, not headers and numbered sections. "
    "If you can say it in one sentence, don't use three. "
    "This does not apply to code or tool calls.\n\n"
    "Length limits:\n"
    "  - Text between tool calls: ≤25 words\n"
    "  - Final response: ≤100 words unless the task requires more detail\n\n"
    "# Final response structure (brief-first + interaction guidance)\n"
    "- Lead with a 1-2 sentence TL;DR summary, then expand with details.\n"
    "- End every final response with a brief follow-up question that advances the conversation, "
    "e.g. '需要我展开 agents/ 目录下集成的具体框架列表吗？' or 'Should I dig deeper into the core/ module?'\n"
    "- This helps the user continue debugging instead of receiving a dead-end wall of text.\n\n"
    "# Tone and style\n"
    "- Do NOT narrate what tools you are about to call. "
    "Do NOT write 'I will read the file, search for X, and list the directory.' — just call the tools.\n"
    "- Do not use a colon before tool calls. Text like 'Let me read the file:' followed by "
    "a read tool call should just be 'Let me read the file.' with a period.\n"
    "- For repo questions like '这个项目有哪些模块', '根目录结构', 'AGENTS.md 里面写了什么', "
    "or '这个项目的架构', never answer with pre-tool architecture prose, category clusters, or directory-purpose guesses. "
    "Read first, then summarize what the tool results support.\n"
    "- After tools complete, report the key findings concisely. "
    "Do NOT re-describe each tool result verbatim — the user can already see the tool output.\n"
    "- Only use emojis if the user explicitly requests it.\n"
    "- When referencing specific code, include file_path:line_number to allow easy navigation.\n"
    "- Never use LaTeX math notation ($, \\rightarrow, \\text{}) in responses. "
    "Use plain Unicode arrows (→) and backtick code formatting instead.\n"
    "- NEVER output a bare tool name or tool call syntax as text — use the function calling API."
)

_SYSTEM_PROMPT_QUALITY = (
    "# Response quality\n"
    "- Be thorough: answer ALL parts of the user's question.\n"
    "- NEVER fabricate file names, line counts, match counts, or code content. "
    "Every specific claim (file exists, N matches found, function defined at line X) "
    "must come from a tool result in this conversation.\n"
    "- For repository structure and architecture questions, distinguish clearly between: "
    "(a) facts directly seen in tool results, and (b) limited inferences from names or layout. "
    "If a directory's purpose is not stated in a document or visible in code, say it appears to be X rather than asserting it as fact.\n"
    "- Do NOT invent high-level clusters like '认知层', '执行闭环', or '企业级隔离' unless those exact ideas are supported by the files you read.\n"
    "- If you intended to read a whole file, self-check that the tool output reached the end. "
    "When the result still shows a partial range (for example 'showing lines 1-300 of 561'), "
    "continue reading the remaining lines or explicitly say the answer is partial.\n"
    "- When a tool result includes a count (e.g. 'Found 15 matches', '42 entries'), "
    "use that exact number. Do NOT round, estimate, or substitute a different number.\n"
    "- If a tool returned truncated output, explicitly say the data is partial. "
    "Do NOT extrapolate, guess, or invent content beyond what was shown.\n"
    "- If you lack information to complete a table cell or answer, call more tools "
    "or write 'N/A' — never fill in guesses.\n"
    "- Use Markdown structure: headings, bullet lists, code blocks with syntax highlighting.\n"
    "- Be concise but complete. Do not pad with unnecessary disclaimers."
)

# P101c: Tool result preservation — Claw's SUMMARIZE_TOOL_RESULTS_SECTION +
# getFunctionResultClearingSection combined. Dedicated section because this
# directly affects model behavior during tool-heavy sessions.
_SYSTEM_PROMPT_TOOL_PRESERVATION = (
    "# Tool result clearing\n"
    "Old tool results are automatically cleared from context to free up space. "
    "Only the 3 most recent results are kept — older ones show '[Old tool result content cleared]'.\n\n"
    "IMPORTANT: When working with tool results, immediately write down any important information "
    "you might need later in your text response (file paths, error messages, match counts, "
    "key code snippets, decisions made). The original tool result may be cleared by the next turn. "
    "Do not rely on being able to re-read old tool results later."
)


_SYSTEM_PROMPT_SELF_KNOWLEDGE = (
    "# About yourself (Nanobot internals)\n"
    "- You run as a Python web server with direct filesystem access — no sandbox, no container.\n"
    "- You have 14 tools available:\n"
    "  1. shell_execute — run shell commands (aliases: bash, run_command, execute_command)\n"
    "  2. file_read — read files with metadata header (alias: read_file)\n"
    "  3. file_write — write/create files (alias: write_file)\n"
    "  4. file_edit — precise find-and-replace editing (aliases: edit_file, str_replace, text_editor)\n"
    "  5. file_list — list directory contents (aliases: list_dir, ls)\n"
    "  6. grep_search — search file contents with regex (aliases: grep, search)\n"
    "  7. find_by_name — find files by name pattern (alias: find)\n"
    "  8. python_execute — run Python 3 code (aliases: python, code_execute)\n"
    "  9. web_fetch — fetch and extract text from a URL (aliases: fetch_url, read_url, curl)\n"
    "  10. web_search — search the web via DuckDuckGo (aliases: search, google, ddg)\n"
    "  11. sub_agent — fork a subtask to an independent context (aliases: fork, delegate, spawn_agent)\n"
    "  12. todo_manage — create and track task lists for multi-step work (aliases: todo, task_list, plan)\n"
    "  13. memory — persistent memory system that survives across conversations (aliases: save_memory, recall_memory, remember)\n"
    "  14. code_intel — code intelligence: extract symbols, find references, analyze types, diagnose quality (aliases: symbols, references, find_symbol)\n"
    "- file_edit and file_write can return pending change sets. Those edits are NOT on disk until the user approves them via the UI approval card.\n"
    "- python_execute runs code via subprocess — do NOT use it to call other tools. "
    "Call tools directly instead.\n"
    "- shell_execute runs system commands via subprocess — use commands appropriate for the ACTUAL OS shown in the Platform section.\n"
    "- sub_agent spawns a fresh agentic loop with independent message history. "
    "Three built-in agent types: agent_type='explore' (read-only codebase search), "
    "agent_type='verify' (adversarial verification — tries to break your implementation), "
    "agent_type='plan' (analysis and planning only). "
    "Omit agent_type for a general-purpose sub-agent. "
    "Set inherit_context=true when the sub-task references files or decisions from the current conversation. "
    "Without inherit_context, include all necessary context in the task parameter.\n"
    "- todo_manage tracks multi-step task progress. Use it proactively for tasks with 3+ steps. "
    "Always send the COMPLETE todo list (it replaces previous state). "
    "Keep exactly ONE task in_progress at a time. Mark completed IMMEDIATELY after finishing.\n"
    "- Your tool outputs are truncated per-tool (MicroCompact): "
    "file_read max 12000 chars, grep_search 6000, shell_execute/python_execute 8000, "
    "sub_agent 8000, web_fetch 10000, web_search 4000, "
    "file_list/find_by_name 4000, file_edit/file_write 3000, todo_manage 2000. "
    "Results older than 3 turns are further halved to save context.\n"
    "- You have a Skill system with 7 built-in skills invokable via /command:\n"
    "  /simplify, /verify, /commit, /debug, /remember, /batch, /skillify\n"
    "  Users can also create custom skills by placing SKILL.md files in ~/.nanobot/skills/ "
    "or .nanobot/skills/. Skills are listed with /skills.\n"
    "- When asked about your own internals, configuration, or source code, "
    "use file_read/grep_search on your own codebase (web_ui/ directory) to give accurate answers. "
    "Do NOT describe features you don't have or guess internal values."
)


def _build_platform_info() -> str:
    """Generate platform awareness section (P8).

    Prevents the model from assuming Linux on Windows, or vice versa.
    Uses runtime detection so it's always accurate.
    """
    os_name = platform.system()   # 'Linux', 'Windows', 'Darwin'
    os_release = platform.release()
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    lines = [
        "# Platform",
        f"Operating System: {os_name} {os_release}",
        f"Python: {py_version}",
    ]

    if os_name == "Windows":
        lines.append("Shell commands run in Windows cmd/PowerShell, NOT Bash. Use Windows-compatible commands.")
    elif os_name == "Darwin":
        lines.append("Shell commands run in macOS zsh/bash.")
    else:
        lines.append("Shell commands run in Bash.")

    return "\n".join(lines)


# ── P21: Static system prompt prefix (cached across turns for KV cache) ──
_STATIC_SYSTEM_PROMPT: Optional[str] = None

# ═══════════════════════════════════════════════════════════════
# U5: Section-Level Hash Caching
# ═══════════════════════════════════════════════════════════════
# Track each section's content hash independently. Only rebuild the
# concatenated string when at least one section changes. This avoids
# re-joining ~10 sections every turn when nothing changed.
_SECTION_NAMES = [
    "identity", "tool_guidance", "tasks", "actions", "stop",
    "output", "quality", "tool_preservation", "self_knowledge", "platform_info",
]
_SECTION_HASHES: Dict[str, str] = {}     # name → md5 hex
_SECTION_CONTENTS: Dict[str, str] = {}   # name → content string
_STATIC_PROMPT_HASH: Optional[str] = None  # combined hash of all sections


def _hash_section(content: str) -> str:
    """U5: Fast MD5 hash of a section string."""
    return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()


def _build_sections_list() -> List[tuple]:
    """U5: Build (name, content) pairs for all static sections."""
    return [
        ("identity",          _SYSTEM_PROMPT_IDENTITY),
        ("tool_guidance",     build_tool_guidance()),
        ("tasks",             _SYSTEM_PROMPT_TASKS),
        ("actions",           _SYSTEM_PROMPT_ACTIONS),
        ("stop",              _SYSTEM_PROMPT_STOP),
        ("output",            _SYSTEM_PROMPT_OUTPUT),
        ("quality",           _SYSTEM_PROMPT_QUALITY),
        ("tool_preservation", _SYSTEM_PROMPT_TOOL_PRESERVATION),
        ("self_knowledge",    _SYSTEM_PROMPT_SELF_KNOWLEDGE),
        ("platform_info",     _build_platform_info()),
    ]


def get_section_names() -> List[str]:
    """U5: Return the ordered list of static system prompt section names."""
    return list(_SECTION_NAMES)


def get_section_hash(section_name: str) -> str:
    """U5: Return the cached hash for a section, or '' if not yet built."""
    return _SECTION_HASHES.get(section_name, "")


def get_prompt_section_count() -> int:
    """U5: Return the number of sections in the static system prompt."""
    return len(_SECTION_NAMES)


def _get_static_system_prompt() -> str:
    """Return the static portion of the system prompt (P21).

    This is invariant across turns within a session AND across sessions,
    so Ollama / llama.cpp can reuse its KV cache for this prefix.
    Cached at module level after first build.

    U5: Uses section-level hash caching — only rebuilds the concatenated
    string when at least one section's content hash changes.
    """
    global _STATIC_SYSTEM_PROMPT, _STATIC_PROMPT_HASH

    sections_list = _build_sections_list()

    # Check if any section changed
    new_hashes = {}
    changed = False
    for name, content in sections_list:
        h = _hash_section(content)
        new_hashes[name] = h
        if h != _SECTION_HASHES.get(name, ""):
            changed = True

    if _STATIC_SYSTEM_PROMPT is not None and not changed:
        return _STATIC_SYSTEM_PROMPT

    # Rebuild: update caches
    contents = []
    for name, content in sections_list:
        _SECTION_HASHES[name] = new_hashes[name]
        _SECTION_CONTENTS[name] = content
        contents.append(content)

    _STATIC_SYSTEM_PROMPT = "\n\n".join(contents)
    _STATIC_PROMPT_HASH = _hash_section(_STATIC_SYSTEM_PROMPT)
    logger.debug(f"[U5] Static system prompt rebuilt ({len(contents)} sections, "
                 f"{len(_STATIC_SYSTEM_PROMPT)} chars)")
    return _STATIC_SYSTEM_PROMPT


def _detect_user_language(text: str) -> str:
    """D7: Detect the dominant language of user text from Unicode character ranges.

    Returns a language code: "zh", "ja", "ko", "en", or "auto" (unable to detect).
    Uses character counting — no external library needed.
    """
    if not text or len(text.strip()) < 2:
        return "auto"

    # Strip code blocks, paths, URLs — these shouldn't influence detection
    _clean = re.sub(r'```[\s\S]*?```', '', text)
    _clean = re.sub(r'`[^`]+`', '', _clean)
    _clean = re.sub(r'https?://\S+', '', _clean)
    _clean = re.sub(r'[\w./\\]+\.\w{1,5}', '', _clean)  # file paths
    _clean = re.sub(r'/\w+', '', _clean)  # slash commands like /analyze

    cjk = 0      # Chinese characters (CJK Unified Ideographs)
    hiragana = 0  # Japanese Hiragana/Katakana
    hangul = 0    # Korean Hangul
    latin = 0     # ASCII letters

    for ch in _clean:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            cjk += 1
        elif 0x3040 <= cp <= 0x30FF:
            hiragana += 1
        elif 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF:
            hangul += 1
        elif 0x0041 <= cp <= 0x005A or 0x0061 <= cp <= 0x007A:
            latin += 1

    total = cjk + hiragana + hangul + latin
    if total < 3:
        return "auto"

    # Japanese: has hiragana/katakana (even mixed with CJK)
    if hiragana > 0 and hiragana >= total * 0.05:
        return "ja"
    # Korean: has hangul
    if hangul > 0 and hangul >= total * 0.1:
        return "ko"
    # Chinese: CJK dominant (no hiragana/hangul)
    if cjk >= total * 0.15:
        return "zh"
    # English / Latin: fallback when Latin dominant
    if latin >= total * 0.5:
        return "en"

    return "auto"


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _score_planned_skill_recommendations(task_state: str, objective_text: str) -> List[Dict[str, str]]:
    """Return a small ranked set of Skill suggestions for a planned task.

    The goal is to treat Skills like a toolbox: once a task is planned, the
    model should see 1-2 concrete slash commands that match the objective.
    """
    state = _normalize_text(task_state)
    if state != "planned":
        return []

    text = _normalize_text(objective_text)
    if not text:
        return []

    rules = [
        {
            "name": "simplify",
            "score": 0,
            "zh": "先用 /simplify 简化代码并检查复用、重复和结构清晰度。",
            "en": "Start with /simplify to reduce duplication, improve reuse, and clean up structure.",
            "keywords": ["refactor", "simplify", "cleanup", "clean up", "dedup", "duplicate", "reuse", "optimiz", "improve structure", "streamline", "简化", "重构", "去重", "复用", "整理", "清理"],
        },
        {
            "name": "debug",
            "score": 0,
            "zh": "如果目标是排查错误或定位根因，可以先用 /debug。",
            "en": "If the objective involves bugs or root cause analysis, use /debug first.",
            "keywords": ["bug", "error", "fail", "failure", "crash", "exception", "trace", "traceback", "diagnos", "root cause", "issue", "repro", "fix", "报错", "错误", "异常", "失败", "定位根因", "排查", "调试", "问题"],
        },
        {
            "name": "verify",
            "score": 0,
            "zh": "如果任务要求确认正确性或回归风险，/verify 可以先做验证。",
            "en": "If the task needs correctness checks or regression confidence, /verify is a good next step.",
            "keywords": ["test", "verify", "validate", "lint", "check", "confirm", "regression", "assert", "qa", "测试", "验证", "回归", "检查", "确认"],
        },
        {
            "name": "remember",
            "score": 0,
            "zh": "如果目标涉及记忆、复盘或知识整理，可以先用 /remember。",
            "en": "If the objective is about memories or knowledge cleanup, /remember is useful.",
            "keywords": ["memory", "remember", "recall", "notes", "organize", "cleanup memories", "knowledge", "记忆", "复盘", "笔记", "整理知识", "知识"],
        },
        {
            "name": "commit",
            "score": 0,
            "zh": "完成修改后，/commit 可以帮助整理提交信息。",
            "en": "After the edits are done, /commit can help draft a clean commit message.",
            "keywords": ["commit", "git", "push", "release", "changelog", "stage", "提交", "发布", "变更记录", "暂存"],
        },
    ]

    for rule in rules:
        for keyword in rule["keywords"]:
            if keyword in text:
                rule["score"] += 1

    ranked = [rule for rule in rules if rule["score"] > 0]
    ranked.sort(key=lambda item: (-int(item["score"]), [r["name"] for r in rules].index(item["name"])))
    return ranked


def build_task_skill_toolbox_prompt(
    task_state: str = "",
    task_title: str = "",
    task_objective: str = "",
    task_current_step: str = "",
    language: str = "auto",
    max_suggestions: int = 2,
) -> str:
    """Build a concise planned-task toolbox prompt that suggests relevant Skills."""
    suggestions = _score_planned_skill_recommendations(
        task_state,
        "\n".join([task_title, task_objective, task_current_step]),
    )
    if not suggestions:
        return ""

    try:
        from skills import get_skill
    except Exception:
        get_skill = None  # type: ignore[assignment]

    language_code = _normalize_text(language)
    if language_code == "auto":
        language_code = _detect_user_language("\n".join([task_title, task_objective, task_current_step]))
    is_zh = language_code in {"zh", "zh-cn", "zh-hans", "auto"}
    lines: List[str] = []

    header = "# 任务工具箱建议" if is_zh else "# Planned task toolbox"
    intro = (
        "当前任务处于 planned 阶段。根据任务 objective，你可以先试用以下 Skills："
        if is_zh
        else "The current task is in the planned stage. Based on the objective, consider these Skills first:"
    )
    lines.extend([header, intro, ""])

    emitted = 0
    for item in suggestions:
        name = item["name"]
        if get_skill is not None and get_skill(name) is None:
            continue
        reason = item["zh"] if is_zh else item["en"]
        if is_zh:
            lines.append(f"- 你可以先尝试用 `/{name}`。{reason}")
        else:
            lines.append(f"- You can start with `/{name}`. {reason}")
        emitted += 1
        if emitted >= max_suggestions:
            break

    if emitted == 0:
        return ""

    closing = (
        "把 Skills 当作任务工具箱：先选最贴近 objective 的命令，再继续推进。"
        if is_zh
        else "Treat Skills like a toolbox: pick the command that best matches the objective, then continue."
    )
    lines.extend(["", closing])
    return "\n".join(lines)


def build_dynamic_context(
    language: str = "auto",
    workspace_info: str = "",
    custom_instructions: str = "",
    rag_context: str = "",
    prev_summary: str = "",
    memory_context: str = "",
    skill_context: str = "",
    task_state: str = "",
    task_title: str = "",
    task_objective: str = "",
    task_current_step: str = "",
    repo_map: str = "",
    active_files_summary: str = "",
    user_message: str = "",
    scratchpad_path: str = "",
    mcp_resources_context: str = "",
    plan_mode: bool = False,
) -> str:
    """Build the dynamic context block (P21).

    This is injected as a [SESSION CONTEXT] user message rather than
    appended to the system prompt.  Keeping it separate means the
    system-prompt prefix never changes across turns, maximising
    Ollama/llama.cpp KV cache reuse.

    Returns empty string if there is no dynamic content to inject.
    """
    parts: List[str] = []

    # D7: Resolve language — auto-detect from user message if needed
    _resolved_lang = language
    if _resolved_lang == "auto" and user_message:
        _resolved_lang = _detect_user_language(user_message)

    if _resolved_lang and _resolved_lang != "auto":
        _LANG_MAP = {
            "zh": "Chinese (中文)", "en": "English", "ja": "Japanese (日本語)",
            "ko": "Korean (한국어)", "es": "Spanish", "fr": "French",
        }
        lang_name = _LANG_MAP.get(_resolved_lang, _resolved_lang)
        parts.append(
            f"Language: ALWAYS respond in {lang_name}. "
            f"All headings, explanations, and prose MUST be in {lang_name}. "
            f"Only code, tool names, file paths, and technical identifiers stay in their original form."
        )

    # U21: Plan Mode behavioral prompt — injected early (high priority)
    if plan_mode:
        parts.append(
            "[PLAN MODE — READ-ONLY]\n"
            "You are in PLAN MODE. Your job is to analyze, explore, and produce a plan.\n"
            "RULES:\n"
            "- Do NOT call file_edit, file_write, or any tool that modifies files.\n"
            "- Do NOT execute destructive or state-changing shell commands.\n"
            "- You MAY use: file_read, grep_search, find_by_name, file_list, web_search, web_fetch, code_intel, memory.\n"
            "- You MAY use shell_execute ONLY for read-only commands (e.g. git log, ls, cat, grep, find, wc).\n"
            "- Produce a numbered action plan with specific file paths and changes needed.\n"
            "- If the task requires code changes, describe WHAT to change and WHERE, but do not make the changes.\n"
            "- End with: 'To implement this plan, switch to Code mode.'"
        )

    if workspace_info:
        parts.append(f"Environment:\n{workspace_info}")

    # A5: Repo map for workspace orientation
    if repo_map:
        parts.append(f"Project structure:\n{repo_map}")

    # D3: Active files workbench (injected after repo map for spatial context)
    if active_files_summary:
        parts.append(active_files_summary)

    if custom_instructions:
        parts.append(
            f"User Instructions (from NANOBOT.md):\n{custom_instructions}"
        )

    # P91: Inject persistent memory prompt
    if memory_context:
        parts.append(memory_context)

    # P97d: Inject skill awareness
    if skill_context:
        parts.append(skill_context)

    task_toolbox_context = build_task_skill_toolbox_prompt(
        task_state=task_state,
        task_title=task_title,
        task_objective=task_objective,
        task_current_step=task_current_step,
        language=_resolved_lang,
    )
    if task_toolbox_context:
        parts.append(task_toolbox_context)

    # U8: Scratchpad directory
    if scratchpad_path:
        parts.append(
            f"Scratchpad: `{scratchpad_path}` — a temporary directory for this session. "
            "Write drafts, test scripts, intermediate outputs, or experimental code here freely. "
            "No confirmation needed. This directory is auto-cleaned between sessions."
        )

    # MCP: Inject external resource context from connected MCP servers
    if mcp_resources_context:
        parts.append(mcp_resources_context)

    if rag_context:
        parts.append(rag_context)

    if prev_summary:
        parts.append(f"Previous session context (use if relevant):\n{prev_summary}")

    if not parts:
        return ""
    return "[SESSION CONTEXT]\n" + "\n\n".join(parts)


def build_system_prompt(
    tools: Optional[List[Dict]] = None,
    language: str = "auto",
    workspace_info: str = "",
    custom_instructions: str = "",
) -> str:
    """Dynamically assemble the system prompt (P4).

    Composable sections — only includes what's relevant:
      - Identity (always)
      - Tool guidance (auto-generated from loaded tool modules)
      - Task rules, stop rules, quality rules (always)
      - Language preference (if not "auto")
      - Workspace context (if provided)
      - Custom user instructions from NANOBOT.md (if provided)

    When ``tools`` is None, uses all registered tools. When an explicit
    list is given, the tool guidance section is still the full set (since
    the model needs to know what NOT to use shell for), but we note which
    tools are currently active.

    NOTE: This function is kept for backward compatibility.
    The main loop (P21) now uses _get_static_system_prompt() + build_dynamic_context()
    separately for better KV cache utilisation.

    U5: Now reuses the cached static base from _get_static_system_prompt()
    and only appends dynamic sections (language, workspace, custom) if present.

    Args:
        tools:                Tool schema list (None = all AGENTIC_TOOLS)
        language:             "auto" | "zh" | "en" | ...
        workspace_info:       Workspace path + env note
        custom_instructions:  User's NANOBOT.md content
    """
    # U5: Reuse cached static base instead of re-joining 10 sections
    base = _get_static_system_prompt()
    extras = []

    if language != "auto":
        _LANG_MAP = {
            "zh": "Chinese (中文)", "en": "English", "ja": "Japanese (日本語)",
            "ko": "Korean (한국어)", "es": "Spanish", "fr": "French",
        }
        lang_name = _LANG_MAP.get(language, language)
        extras.append(f"# Language\nAlways respond in {lang_name}.")

    if workspace_info:
        extras.append(f"# Environment\n{workspace_info}")

    if custom_instructions:
        extras.append(
            f"# User Instructions (from NANOBOT.md)\n"
            f"The following are user-provided instructions that take precedence "
            f"over default behaviors:\n{custom_instructions}"
        )

    if not extras:
        return base
    return base + "\n\n" + "\n\n".join(extras)


def _load_nanobot_md(workspace: Path) -> str:
    """Load user custom instructions from NANOBOT.md (like Claw's CLAUDE.md).

    Searches for NANOBOT.md in:
      1. workspace root
      2. ~/.nanobot/NANOBOT.md (global)

    Returns content string or empty string if not found.
    """
    candidates = [
        workspace / "NANOBOT.md",
        Path.home() / ".nanobot" / "NANOBOT.md",
    ]
    for path in candidates:
        try:
            if path.is_file():
                content = path.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    logger.info(f"[SystemPrompt] Loaded NANOBOT.md from {path} ({len(content)} chars)")
                    return content[:4000]  # cap at 4K to avoid bloating prompt
        except Exception as e:
            logger.warning(f"[SystemPrompt] Failed to read {path}: {e}")
    return ""


def _detect_workspace_context(workspace: Path) -> str:
    """P16: Auto-detect project type from marker files and generate tech stack summary.

    Scans the workspace root for common project indicators and builds a short
    context string that helps the model understand the project without needing
    to call file_list/file_read first. Similar to Claw's computeEnvInfo().
    """
    if not workspace or not workspace.is_dir():
        return ""

    markers = []

    # Python
    if (workspace / "requirements.txt").exists():
        markers.append("Python (requirements.txt)")
    elif (workspace / "pyproject.toml").exists():
        markers.append("Python (pyproject.toml)")
    elif (workspace / "setup.py").exists():
        markers.append("Python (setup.py)")
    elif (workspace / "Pipfile").exists():
        markers.append("Python (Pipfile)")

    # Node.js
    if (workspace / "package.json").exists():
        markers.append("Node.js (package.json)")
        if (workspace / "bun.lockb").exists():
            markers.append("Bun runtime")
        elif (workspace / "yarn.lock").exists():
            markers.append("Yarn")
        elif (workspace / "pnpm-lock.yaml").exists():
            markers.append("pnpm")

    # Rust
    if (workspace / "Cargo.toml").exists():
        markers.append("Rust (Cargo.toml)")

    # Go
    if (workspace / "go.mod").exists():
        markers.append("Go (go.mod)")

    # Java/Kotlin
    if (workspace / "pom.xml").exists():
        markers.append("Java/Maven (pom.xml)")
    elif (workspace / "build.gradle").exists() or (workspace / "build.gradle.kts").exists():
        markers.append("Java/Gradle")

    # C/C++
    if (workspace / "CMakeLists.txt").exists():
        markers.append("C/C++ (CMake)")
    elif (workspace / "Makefile").exists():
        markers.append("Makefile project")

    # Docker
    if (workspace / "Dockerfile").exists() or (workspace / "docker-compose.yml").exists():
        markers.append("Docker")

    # Git
    if (workspace / ".git").is_dir():
        markers.append("Git repo")

    # Frameworks
    if (workspace / "next.config.js").exists() or (workspace / "next.config.mjs").exists():
        markers.append("Next.js")
    elif (workspace / "nuxt.config.ts").exists() or (workspace / "nuxt.config.js").exists():
        markers.append("Nuxt.js")
    if (workspace / "tsconfig.json").exists():
        markers.append("TypeScript")
    if (workspace / ".env").exists() or (workspace / ".env.local").exists():
        markers.append("env config present")

    if not markers:
        return ""

    return f"Detected project stack: {', '.join(markers)}"


def _detect_git_context(workspace: Path) -> str:
    """P19: Detect git branch, status, and recent commits for context injection.

    Runs lightweight git commands (fast, read-only) to give the model
    immediate awareness of the repo state without needing tool calls.
    Similar to Claw's git-aware context in computeEnvInfo().
    """
    if not workspace or not (workspace / ".git").is_dir():
        return ""

    parts = []

    try:
        # Current branch
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(workspace)
        )
        if branch.returncode == 0 and branch.stdout.strip():
            parts.append(f"Branch: {branch.stdout.strip()}")

        # Dirty status (staged + unstaged counts)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, cwd=str(workspace)
        )
        if status.returncode == 0:
            lines = [l for l in status.stdout.strip().split("\n") if l.strip()]
            if lines:
                parts.append(f"Uncommitted changes: {len(lines)} file(s)")
            else:
                parts.append("Working tree: clean")

        # Recent commits (last 3, one-line)
        log = subprocess.run(
            ["git", "log", "--oneline", "-3", "--no-decorate"],
            capture_output=True, text=True, timeout=5, cwd=str(workspace)
        )
        if log.returncode == 0 and log.stdout.strip():
            commits = log.stdout.strip().split("\n")
            parts.append(f"Recent commits: {' | '.join(commits)}")
    except Exception:
        pass  # git not available or other error — silently skip

    if not parts:
        return ""
    return "Git: " + " · ".join(parts[:2]) + ("\n" + parts[2] if len(parts) > 2 else "")


# Quick-access default (no workspace context or custom instructions)
AGENTIC_SYSTEM_PROMPT = build_system_prompt()
