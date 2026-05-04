"""sub_agent tool — Fork a subtask to an independent agentic context.

Spawns a lightweight sub-agent with its own message history that can
use all available tools. Useful for isolating complex sub-tasks to
prevent context pollution in the parent conversation.

P60: Built-in agent types (explore, verify, plan) with specialized
system prompts and tool allow/deny lists.
P61: Verification agent with adversarial prompt from Claw.

The sub-agent runs a mini agentic loop (default 5 turns), collects
the final text response, and returns it as a tool result.
"""
import asyncio
import logging
import os
import re as _re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

logger = logging.getLogger("nanobot.tools.sub_agent")

# ══════════════════════════════════════════════════════════════
# P60: Built-in Agent Type Definitions
# ══════════════════════════════════════════════════════════════

_EXPLORE_SYSTEM_PROMPT = (
    "You are a FAST codebase exploration agent. READ-ONLY — never create/modify/delete files.\n\n"
    "=== HARD RULES ===\n"
    "1. You have a BUDGET of 3 turns. Use them wisely.\n"
    "2. Turn 1: call file_list / find_by_name / grep_search to locate targets. "
    "Call MULTIPLE tools in the SAME turn (parallel).\n"
    "3. Turn 2: batch-read the files you found. Again, multiple file_read calls in ONE turn.\n"
    "4. Turn 3 (if needed): one final check. Then STOP.\n"
    "5. If you already have the answer after Turn 1, STOP. Do NOT explore further.\n\n"
    "=== OUTPUT FORMAT (MANDATORY) ===\n"
    "- Bullet points ONLY. No paragraphs, no essays, no headers.\n"
    "- Maximum 15 lines of output.\n"
    "- Each bullet: `- path/to/file — what it does (N lines)`\n"
    "- If something doesn't exist, say so in ONE line: `- package.json: not found (Python project)`\n"
    "- Do NOT explain your reasoning. Do NOT narrate what you will do. Just do it, then report.\n\n"
    "=== FORBIDDEN ===\n"
    "- Creating, modifying, or deleting files\n"
    "- shell_execute for anything except: ls, cat, head, tail, find, grep, git log, git diff\n"
    "- Writing more than 15 lines of output\n"
    "- Using more than 3 turns"
)

# U11b: Maximum output chars for explore agent (hard truncation)
_EXPLORE_MAX_OUTPUT_CHARS = 2000

_VERIFY_SYSTEM_PROMPT = (
    "You are a verification specialist. Your job is not to confirm the implementation "
    "works — it's to try to BREAK it.\n\n"
    "You have two documented failure patterns. First, verification avoidance: when "
    "faced with a check, you find reasons not to run it — you read code, narrate what "
    "you would test, write 'PASS,' and move on. Second, being seduced by the first 80%: "
    "you see a polished UI or a passing test suite and feel inclined to pass it, not "
    "noticing half the buttons do nothing, the state vanishes on refresh, or the backend "
    "crashes on bad input. The first 80% is the easy part. Your entire value is in "
    "finding the last 20%. The caller may spot-check your commands by re-running them — "
    "if a PASS step has no command output, or output that doesn't match re-execution, "
    "your report gets rejected.\n\n"
    #
    # === READ-ONLY MODE ===
    #
    "=== CRITICAL: DO NOT MODIFY THE PROJECT ===\n"
    "You are STRICTLY PROHIBITED from:\n"
    "- Creating, modifying, or deleting any files IN THE PROJECT DIRECTORY\n"
    "- Installing dependencies or packages\n"
    "- Running git write operations (add, commit, push)\n"
    "You MAY write ephemeral test scripts to /tmp via shell_execute when inline "
    "commands aren't sufficient — e.g., a multi-step test harness. Clean up after.\n\n"
    #
    # === WHAT YOU RECEIVE ===
    #
    "=== WHAT YOU RECEIVE ===\n"
    "You will receive: the original task description, files changed, approach taken, "
    "and optionally a plan file path.\n\n"
    #
    # === TYPE-SPECIFIC VERIFICATION STRATEGY ===
    #
    "=== VERIFICATION STRATEGY ===\n"
    "Adapt your strategy based on what was changed:\n\n"
    "**Backend/API changes**: Start server → curl/fetch endpoints → verify response "
    "shapes against expected values (not just status codes) → test error handling → "
    "check edge cases\n"
    "**CLI/script changes**: Run with representative inputs → verify stdout/stderr/"
    "exit codes → test edge inputs (empty, malformed, boundary) → verify --help / "
    "usage output is accurate\n"
    "**Infrastructure/config changes**: Validate syntax → dry-run where possible "
    "(docker build, nginx -t) → check env vars are actually referenced, not just defined\n"
    "**Library/package changes**: Build → full test suite → import the library from "
    "a fresh context and exercise the public API as a consumer would\n"
    "**Bug fixes**: Reproduce the original bug → verify fix → run regression tests → "
    "check related functionality for side effects\n"
    "**Refactoring (no behavior change)**: Existing test suite MUST pass unchanged → "
    "diff the public API surface (no new/removed exports) → spot-check observable "
    "behavior is identical (same inputs → same outputs)\n"
    "**Python changes**: Run pytest/unittest → check for import errors → test with "
    "python3 -c 'import module' → verify __main__ blocks → check type hints with mypy "
    "if configured\n"
    "**Other change types**: The pattern is always: (a) exercise the change directly, "
    "(b) check outputs against expectations, (c) try to break it with inputs the "
    "implementer didn't test.\n\n"
    #
    # === REQUIRED BASELINE STEPS ===
    #
    "=== REQUIRED STEPS (universal baseline) ===\n"
    "1. Read the project's README/NANOBOT.md for build/test commands and conventions. "
    "If the implementer pointed you to a plan or spec file, read it.\n"
    "2. Run the build (if applicable). A broken build is an automatic FAIL.\n"
    "3. Run the project's test suite (if it has one). Failing tests = automatic FAIL.\n"
    "4. Run linters/type-checkers if configured (eslint, tsc, mypy, flake8, etc.).\n"
    "5. Check for regressions in related code.\n\n"
    "Then apply the type-specific strategy above. Match rigor to stakes: a one-off "
    "script doesn't need race-condition probes; production code needs everything.\n\n"
    "Test suite results are context, not evidence. Run the suite, note pass/fail, "
    "then move on to your REAL verification. The implementer is an LLM too — its "
    "tests may be heavy on mocks, circular assertions, or happy-path coverage that "
    "proves nothing about whether the system actually works end-to-end.\n\n"
    #
    # === RECOGNIZE RATIONALIZATIONS ===
    #
    "=== RECOGNIZE YOUR OWN RATIONALIZATIONS ===\n"
    "You will feel the urge to skip checks. These are the exact excuses you reach "
    "for — recognize them and do the opposite:\n"
    '- "The code looks correct based on my reading" — reading is not verification. Run it.\n'
    '- "The implementer\'s tests already pass" — the implementer is an LLM. Verify independently.\n'
    '- "This is probably fine" — probably is not verified. Run it.\n'
    '- "Let me start the server and check the code" — no. Start the server AND HIT the endpoint.\n'
    '- "This would take too long" — not your call.\n'
    "If you catch yourself writing an explanation instead of a command, STOP. "
    "Run the command.\n\n"
    #
    # === ADVERSARIAL PROBES ===
    #
    "=== ADVERSARIAL PROBES (adapt to the change type) ===\n"
    "Functional tests confirm the happy path. Also try to break it:\n"
    "- **Concurrency** (servers/APIs): parallel requests to create-if-not-exists "
    "paths — duplicate sessions? lost writes?\n"
    "- **Boundary values**: 0, -1, empty string, very long strings, unicode, MAX_INT\n"
    "- **Idempotency**: same mutating request twice — duplicate created? error? correct no-op?\n"
    "- **Orphan operations**: delete/reference IDs that don't exist\n"
    "These are seeds, not a checklist — pick the ones that fit what you're verifying.\n\n"
    #
    # === BEFORE ISSUING PASS ===
    #
    "=== BEFORE ISSUING PASS ===\n"
    "Your report must include at least ONE adversarial probe you ran and its result — "
    "even if the result was 'handled correctly.' If all your checks are 'returns 200' "
    "or 'test suite passes,' you have confirmed the happy path, not verified "
    "correctness. Go back and try to break something.\n\n"
    #
    # === BEFORE ISSUING FAIL ===
    #
    "=== BEFORE ISSUING FAIL ===\n"
    "You found something that looks broken. Before reporting FAIL, check:\n"
    "- **Already handled**: is there defensive code elsewhere (validation upstream, "
    "error recovery downstream) that prevents this?\n"
    "- **Intentional**: does README / comments / commit message explain this as deliberate?\n"
    "- **Not actionable**: is this a real limitation but unfixable without breaking an "
    "external contract? If so, note it as an observation, not a FAIL.\n"
    "Don't use these as excuses to wave away real issues — but don't FAIL on "
    "intentional behavior either.\n\n"
    #
    # === OUTPUT FORMAT ===
    #
    "=== OUTPUT FORMAT (REQUIRED) ===\n"
    "Every check MUST follow this structure. A check without a Command run block "
    "is not a PASS — it's a skip.\n\n"
    "### Check: [what you're verifying]\n"
    "**Command run:**\n"
    "  [exact command you executed]\n"
    "**Output observed:**\n"
    "  [actual terminal output — copy-paste, not paraphrased. Truncate if very long "
    "but keep the relevant part.]\n"
    "**Result: PASS** (or FAIL — with Expected vs Actual)\n\n"
    "End with exactly this line (parsed by caller):\n"
    "VERDICT: PASS\n"
    "or\n"
    "VERDICT: FAIL\n"
    "or\n"
    "VERDICT: PARTIAL\n\n"
    "PARTIAL is for environmental limitations only (no test framework, tool "
    "unavailable, server can't start) — not for 'I'm unsure whether this is a bug.' "
    "If you can run the check, you must decide PASS or FAIL.\n"
    "Use the literal string 'VERDICT: ' followed by exactly one of PASS, FAIL, PARTIAL. "
    "No markdown bold, no punctuation, no variation."
)

# U2: Critical system reminder injected at end of verify context
_VERIFY_CRITICAL_REMINDER = (
    "CRITICAL: This is a VERIFICATION-ONLY task. You CANNOT edit, write, or create "
    "files IN THE PROJECT DIRECTORY (/tmp is allowed for ephemeral test scripts). "
    "You MUST end with VERDICT: PASS, VERDICT: FAIL, or VERDICT: PARTIAL."
)

_PLAN_SYSTEM_PROMPT = (
    "You are a planning specialist. Analyze the task, explore the codebase, "
    "and produce a detailed execution plan.\n\n"
    "=== CRITICAL: READ-ONLY MODE ===\n"
    "You are STRICTLY PROHIBITED from modifying any files. "
    "Your only job is to analyze and plan.\n\n"
    "=== OUTPUT FORMAT ===\n"
    "1. **Task Analysis**: What exactly needs to be done\n"
    "2. **Codebase Findings**: Relevant files, functions, patterns discovered\n"
    "3. **Execution Plan**: Numbered steps, each with:\n"
    "   - File(s) to modify\n"
    "   - What to change and why\n"
    "   - Potential risks or side effects\n"
    "4. **Testing Plan**: How to verify the changes work\n\n"
    "Be specific: include file paths, line numbers, function names."
)

# P103: Coding skill-aware agent types

_RESEARCH_SYSTEM_PROMPT = (
    "You are a code research specialist. Your job is deep analysis of code "
    "structure, dependencies, and behavior — producing a structured report "
    "that a coordinating agent can act on.\n\n"
    "=== CRITICAL: READ-ONLY MODE — NO FILE MODIFICATIONS ===\n"
    "You are STRICTLY PROHIBITED from creating, modifying, or deleting any files.\n"
    "You may ONLY use read tools (file_read, grep_search, find_by_name, file_list).\n\n"
    "=== STRATEGY ===\n"
    "1. Start broad: map the directory structure around the target\n"
    "2. Search targeted: grep for symbols, imports, references\n"
    "3. Read key files: entry points, then follow call chains\n"
    "4. Batch reads: call multiple file_read/grep_search in the SAME turn\n"
    "5. Be fast — you have limited turns\n\n"
    "=== OUTPUT FORMAT (REQUIRED) ===\n"
    "### Scope\n"
    "What area was researched.\n\n"
    "### Key Findings\n"
    "Numbered list of important discoveries with file:line references.\n\n"
    "### Structure Map\n"
    "Entry points → callees → dependencies, with file paths.\n\n"
    "### Risks / Unknowns\n"
    "What's unclear or needs further investigation.\n\n"
    "=== ANTI-HALLUCINATION ===\n"
    "- Every claim must cite a file:line from a tool result\n"
    "- If results are truncated, say so — don't infer from what's not shown\n"
    "- If you can't find something, say 'not found' — don't fabricate"
)

# D5: Edit sub-agent operates in DRAFT mode — proposes patches, never applies them
_EDIT_SYSTEM_PROMPT = (
    "You are a **code editing specialist** operating in DRAFT mode.\n\n"
    "## CRITICAL RULE\n"
    "You are FORBIDDEN from using file_edit or file_write. "
    "You MUST NOT make any changes directly.\n\n"
    "Instead, analyze the required change and return a **patch draft** "
    "in the exact unified diff format below.\n\n"
    "## STRATEGY\n"
    "1. Read each target file before proposing changes\n"
    "2. Understand the surrounding context (imports, callers, tests)\n"
    "3. Produce a minimal, precise diff — no unrelated changes\n"
    "4. If the plan is ambiguous, report the ambiguity instead of guessing\n\n"
    "## Output Format (REQUIRED)\n"
    "Your response MUST contain a fenced code block with the unified diff:\n"
    "```diff\n"
    "--- a/path/to/file\n"
    "+++ b/path/to/file\n"
    "@@ -start,count +start,count @@\n"
    " unchanged context line\n"
    "-old line to remove\n"
    "+new line to add\n"
    " more context\n"
    "```\n\n"
    "Before the diff, briefly explain:\n"
    "- What you are changing and why\n"
    "- Any behavior assumptions preserved\n"
    "- Any residual risks or items needing manual verification\n\n"
    "## RULES\n"
    "- YOU DO NOT APPLY THE PATCH. Only propose it.\n"
    "- One logical change per diff hunk — don't batch unrelated changes\n"
    "- Preserve existing code style (indentation, naming conventions)\n"
    "- Never add comments, docstrings, or features beyond the plan\n"
    "- Do NOT refactor, improve, or extend beyond the plan\n"
    "- If you encounter unexpected code, STOP and report — don't improvise"
)

# P60: Agent type registry
BUILT_IN_AGENTS: Dict[str, Dict[str, Any]] = {
    "explore": {
        "system_prompt": _EXPLORE_SYSTEM_PROMPT,
        "allowed_tools": {"file_read", "grep_search", "find_by_name", "file_list", "shell_execute"},
        "description": "Fast read-only codebase search and exploration",
        "max_turns_default": 3,  # U11b: hard budget — 3 turns max
        "parallel_tool_calls": True,  # U11: hint to model to batch tool calls
        "max_output_chars": _EXPLORE_MAX_OUTPUT_CHARS,  # U11b: truncate verbose output
    },
    "verify": {
        "system_prompt": _VERIFY_SYSTEM_PROMPT,
        "allowed_tools": {"file_read", "grep_search", "find_by_name", "file_list",
                          "shell_execute", "python_execute"},
        "disallowed_tools": ["file_edit", "file_write"],
        "description": "Adversarial verification — tries to break your implementation, returns VERDICT",
        "max_turns_default": 8,
        "write_policy": "forbid",
        "critical_reminder": _VERIFY_CRITICAL_REMINDER,
    },
    "plan": {
        "system_prompt": _PLAN_SYSTEM_PROMPT,
        "allowed_tools": {"file_read", "grep_search", "find_by_name", "file_list"},
        "description": "Analysis and planning only, no modifications",
        "max_turns_default": 5,
    },
    # P103: Coding skill-aware agent types
    "research": {
        "system_prompt": _RESEARCH_SYSTEM_PROMPT,
        "allowed_tools": {"file_read", "grep_search", "find_by_name", "file_list", "code_intel"},
        "description": "Deep code structure analysis — read-only, returns structured report",
        "max_turns_default": 6,
        "write_policy": "forbid",
    },
    # D5: Edit sub-agent operates in DRAFT/patch-only mode
    "edit": {
        "system_prompt": _EDIT_SYSTEM_PROMPT,
        "allowed_tools": {"file_read", "grep_search", "find_by_name", "file_list"},
        "disallowed_tools": ["file_edit", "file_write", "shell_execute"],
        "description": "Patch draft specialist — proposes unified diffs, never applies them",
        "max_turns_default": 6,
        "write_policy": "forbid",
        "requires_verification": False,
    },
}

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "sub_agent",
        "description": (
            "Fork a subtask to a NEW independent agentic context. "
            "The sub-agent has its own fresh conversation and returns its final text response. "
            "Use this when: (1) a task requires many tool calls that would clutter the main conversation, "
            "(2) you need to research a codebase area, (3) you want to isolate a complex analysis. "
            "Built-in agent types: "
            "agent_type='explore' (fast read-only codebase search), "
            "agent_type='verify' (adversarial verification — tries to break your code, returns VERDICT), "
            "agent_type='plan' (analysis and planning only). "
            "Omit agent_type for a general-purpose sub-agent with all tools. "
            "Brief the agent like a smart colleague who just walked into the room — "
            "explain what you're trying to accomplish, what you've already done, and what to look for. "
            "NEVER delegate understanding: don't write 'based on your findings, fix the bug' — "
            "include file paths, line numbers, and what specifically to check or change."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "A clear, self-contained task description for the sub-agent. "
                        "Be specific about what files to examine and what output format you expect."
                    )
                },
                "agent_type": {
                    "type": "string",
                    "enum": ["explore", "verify", "plan", "research", "edit"],
                    "description": (
                        "Built-in agent type. 'explore': read-only codebase search. "
                        "'verify': adversarial verification (returns VERDICT). "
                        "'plan': analysis and planning only. "
                        "'research': deep code structure analysis (read-only, structured report). "
                        "'edit': patch draft specialist (read-only, proposes unified diffs for approval). "
                        "Omit for general-purpose agent with all tools."
                    )
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Maximum agentic turns for the sub-agent (default depends on type, max 8)"
                },
                "inherit_context": {
                    "type": "boolean",
                    "description": (
                        "If true, the sub-agent inherits a summary of the parent conversation "
                        "(recent messages with tool results replaced by summaries). "
                        "Use when the sub-task references files or decisions from the parent. "
                        "Default: false."
                    )
                },
                "background": {
                    "type": "boolean",
                    "description": (
                        "If true, the sub-agent runs in the BACKGROUND and returns immediately "
                        "with a task_id. You can continue working while it runs. "
                        "When it finishes, you will receive a <task-notification> with the results. "
                        "Use for long-running verification or research that should not block you. "
                        "Default: false (synchronous — waits for sub-agent to finish)."
                    )
                },
            },
            "required": ["task"]
        }
    }
}

ALIASES = ["fork", "delegate", "spawn_agent"]

# ══════════════════════════════════════════════════════════════
# U3: Background Task Registry
# ══════════════════════════════════════════════════════════════

# Limits
_MAX_CONCURRENT_BACKGROUND = 3
_MAX_FORK_DEPTH = 1  # background sub-agents cannot spawn further background tasks


class BackgroundTask:
    """Tracks a single background sub-agent task."""
    __slots__ = (
        "task_id", "agent_type", "task_preview", "asyncio_task",
        "created_at", "completed_at", "result", "notified",
        "session_id", "fork_depth",
    )

    def __init__(self, task_id: str, agent_type: str, task_preview: str,
                 asyncio_task: asyncio.Task, session_id: str, fork_depth: int = 0):
        self.task_id = task_id
        self.agent_type = agent_type
        self.task_preview = task_preview
        self.asyncio_task = asyncio_task
        self.created_at = time.time()
        self.completed_at: Optional[float] = None
        self.result: Optional[dict] = None
        self.notified = False
        self.session_id = session_id
        self.fork_depth = fork_depth

    @property
    def is_done(self) -> bool:
        return self.asyncio_task.done()

    @property
    def elapsed(self) -> float:
        end = self.completed_at or time.time()
        return end - self.created_at

    def collect_result(self) -> dict:
        """Collect the result from the completed asyncio task."""
        if not self.is_done:
            return {"success": False, "output": "", "error": "Task still running"}
        if self.result is not None:
            return self.result
        try:
            self.result = self.asyncio_task.result()
            self.completed_at = time.time()
        except Exception as e:
            self.result = {"success": False, "output": "", "error": f"Background task failed: {str(e)[:300]}"}
            self.completed_at = time.time()
        return self.result


# Registry: session_id -> list of BackgroundTask
_BACKGROUND_TASKS: Dict[str, List["BackgroundTask"]] = {}

# Current fork depth (set during execution to prevent recursive background forks)
_CURRENT_FORK_DEPTH: int = 0


def _get_session_tasks(session_id: str) -> List[BackgroundTask]:
    """Get background tasks for a session."""
    if session_id not in _BACKGROUND_TASKS:
        _BACKGROUND_TASKS[session_id] = []
    return _BACKGROUND_TASKS[session_id]


def get_completed_background_tasks(session_id: str) -> List[BackgroundTask]:
    """Get completed but un-notified background tasks for a session.

    Called by agentic_loop before each LLM turn to check for finished tasks.
    Returns tasks that are done and haven't been notified yet.
    """
    tasks = _get_session_tasks(session_id)
    completed = []
    for t in tasks:
        if t.is_done and not t.notified:
            t.collect_result()
            completed.append(t)
    return completed


def mark_task_notified(task_id: str, session_id: str) -> None:
    """Mark a background task as notified (result has been injected into conversation)."""
    for t in _get_session_tasks(session_id):
        if t.task_id == task_id:
            t.notified = True
            break


def get_active_background_count(session_id: str) -> int:
    """Count still-running background tasks for a session."""
    return sum(1 for t in _get_session_tasks(session_id) if not t.is_done)


def reset_background_tasks(session_id: str) -> None:
    """Cancel and clear all background tasks for a session."""
    for t in _get_session_tasks(session_id):
        if not t.is_done:
            t.asyncio_task.cancel()
    _BACKGROUND_TASKS.pop(session_id, None)

IS_READONLY = True  # sub-agent can use write tools, but the tool call itself is idempotent

# Mailbox integration: sub-agents register and check for messages
_MAILBOX_ENABLED = os.environ.get("NANOBOT_AGENT_MAILBOX", "1") != "0"

GUIDANCE = {
    "replaces_shell": [],
    "shell_never": "",
    "tips": [
        "Use sub_agent for complex research tasks requiring many tool calls.",
        "The sub-agent has its own context — it won't see parent conversation history.",
        "Write a complete, self-contained task description including file paths.",
        "Keep sub-agent tasks focused — one clear objective per call.",
    ],
}


# ══════════════════════════════════════════════════════════════
# P2: Swarm — SessionAgentPool & Message Mailbox
# ══════════════════════════════════════════════════════════════

class _AgentEntry:
    """Tracks a single agent within the pool."""
    __slots__ = (
        "agent_id", "agent_type", "task_preview", "asyncio_task",
        "created_at", "completed_at", "result", "child_task_id",
        "inbox",
    )

    def __init__(self, agent_id: str, agent_type: str, task_preview: str,
                 asyncio_task: asyncio.Task, child_task_id: str = ""):
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.task_preview = task_preview
        self.asyncio_task = asyncio_task
        self.created_at = time.time()
        self.completed_at: Optional[float] = None
        self.result: Optional[dict] = None
        self.child_task_id = child_task_id
        self.inbox: asyncio.Queue = asyncio.Queue()

    @property
    def is_done(self) -> bool:
        return self.asyncio_task.done()

    def collect_result(self) -> dict:
        if not self.is_done:
            return {"success": False, "output": "", "error": "Agent still running"}
        if self.result is not None:
            return self.result
        try:
            if self.asyncio_task.cancelled():
                self.result = {"success": False, "output": "", "error": "Agent timed out"}
            else:
                self.result = self.asyncio_task.result()
            self.completed_at = time.time()
        except Exception as e:
            self.result = {"success": False, "output": "", "error": f"Agent failed: {str(e)[:300]}"}
            self.completed_at = time.time()
        return self.result


class SessionAgentPool:
    """Session-scoped pool for managing parallel sub-agents with message mailboxes.

    P2: Swarm multi-agent parallelism.
    Each session gets one pool instance. The pool tracks running agents,
    provides message routing between them, and supports wait_all/wait_any.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._agents: Dict[str, _AgentEntry] = {}
        self._counter = 0

    def _next_id(self, agent_type: str) -> str:
        self._counter += 1
        return f"swarm_{agent_type or 'general'}_{self._counter}_{int(time.time() * 1000) % 100000}"

    def start_agent(
        self,
        agent_type: str,
        task: str,
        *,
        inherit_context: bool = False,
        max_turns: int = 5,
        workspace: Path = Path("."),
        env: Dict[str, str] = None,
        parent_session_id: str = "",
        messages: list = None,
        skill_policy: dict = None,
        child_task_id: str = "",
    ) -> str:
        """Start a sub-agent asynchronously in the pool. Returns agent_id immediately."""
        agent_id = self._next_id(agent_type)
        args = {
            "task": task,
            "agent_type": agent_type or "",
            "inherit_context": inherit_context,
            "max_turns": max_turns,
            "background": False,
            "_child_task_id": child_task_id,
            # P2: attach pool context so execute_async can find messages
            "_pool_session_id": self.session_id,
            "_pool_agent_id": agent_id,
        }

        async def _pool_run():
            # Snapshot parent context for this agent (isolation)
            _saved = (_PARENT_ENV, _PARENT_WORKSPACE, _PARENT_SESSION_ID,
                      _PARENT_MESSAGES, _PARENT_SKILL_POLICY, _PARENT_MODE)
            try:
                set_parent_context(
                    env or _PARENT_ENV,
                    workspace,
                    parent_session_id or _PARENT_SESSION_ID,
                    messages=messages or _PARENT_MESSAGES,
                    skill_policy=skill_policy or _PARENT_SKILL_POLICY,
                    mode=_PARENT_MODE,
                )
                return await execute_async(args, workspace)
            finally:
                # Restore parent context
                set_parent_context(*_saved)

        atask = asyncio.ensure_future(_pool_run())
        entry = _AgentEntry(
            agent_id=agent_id,
            agent_type=agent_type or "general",
            task_preview=task[:100],
            asyncio_task=atask,
            child_task_id=child_task_id,
        )
        self._agents[agent_id] = entry
        logger.info(f"[P2/Pool] Started agent {agent_id}: {agent_type or 'general'}, "
                     f"task={task[:80]}...")
        return agent_id

    def get_agent_result(self, agent_id: str) -> Optional[dict]:
        """Get result for a completed agent. Returns None if still running."""
        entry = self._agents.get(agent_id)
        if entry is None:
            return {"success": False, "output": "", "error": f"Unknown agent_id: {agent_id}"}
        if not entry.is_done:
            return None
        return entry.collect_result()

    async def wait_all(self, timeout: float = 300.0) -> Dict[str, dict]:
        """Wait for all running agents to complete. Returns {agent_id: result}."""
        pending = {aid: e for aid, e in self._agents.items() if not e.is_done}
        if not pending:
            return {aid: e.collect_result() for aid, e in self._agents.items()}

        tasks = [e.asyncio_task for e in pending.values()]
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[P2/Pool] wait_all timed out after {timeout}s, "
                           f"{sum(1 for e in pending.values() if not e.is_done)} agents still running")

        results = {}
        for aid, entry in self._agents.items():
            if entry.is_done:
                results[aid] = entry.collect_result()
            else:
                results[aid] = {"success": False, "output": "", "error": "Agent timed out"}
                entry.asyncio_task.cancel()
        return results

    async def wait_any(self, timeout: float = 120.0) -> Optional[str]:
        """Wait for the first agent to complete. Returns agent_id or None on timeout."""
        pending = [e.asyncio_task for e in self._agents.values() if not e.is_done]
        if not pending:
            # All already done — return the most recently completed
            done = [(aid, e) for aid, e in self._agents.items() if e.is_done]
            return done[-1][0] if done else None

        try:
            done_set, _ = await asyncio.wait(
                pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.TimeoutError:
            return None

        for aid, entry in self._agents.items():
            if entry.asyncio_task in done_set:
                entry.collect_result()
                return aid
        return None

    def send_message(self, from_agent: str, to_agent: str, content: str) -> bool:
        """Send a message from one agent to another's inbox."""
        target = self._agents.get(to_agent)
        if target is None:
            logger.warning(f"[P2/Mailbox] Cannot send to unknown agent {to_agent}")
            return False
        msg = {"from": from_agent, "content": content, "timestamp": time.time()}
        target.inbox.put_nowait(msg)
        logger.debug(f"[P2/Mailbox] {from_agent} → {to_agent}: {content[:80]}")
        return True

    def get_agent_messages(self, agent_id: str) -> List[dict]:
        """Drain all pending messages from an agent's inbox."""
        entry = self._agents.get(agent_id)
        if entry is None:
            return []
        messages = []
        while not entry.inbox.empty():
            try:
                messages.append(entry.inbox.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages

    def list_agents(self) -> List[dict]:
        """List all agents in the pool with their status."""
        return [
            {
                "agent_id": e.agent_id,
                "agent_type": e.agent_type,
                "task_preview": e.task_preview,
                "is_done": e.is_done,
                "child_task_id": e.child_task_id,
                "elapsed": (e.completed_at or time.time()) - e.created_at,
            }
            for e in self._agents.values()
        ]

    def reset(self) -> None:
        """Cancel all running agents and clear the pool."""
        for entry in self._agents.values():
            if not entry.is_done:
                entry.asyncio_task.cancel()
        self._agents.clear()
        self._counter = 0

    @property
    def active_count(self) -> int:
        return sum(1 for e in self._agents.values() if not e.is_done)

    @property
    def total_count(self) -> int:
        return len(self._agents)


# Session → pool registry
_AGENT_POOLS: Dict[str, SessionAgentPool] = {}


def get_agent_pool(session_id: str) -> SessionAgentPool:
    """Get or create the agent pool for a session."""
    if session_id not in _AGENT_POOLS:
        _AGENT_POOLS[session_id] = SessionAgentPool(session_id)
    return _AGENT_POOLS[session_id]


def reset_agent_pool(session_id: str) -> None:
    """Reset and remove the agent pool for a session."""
    pool = _AGENT_POOLS.pop(session_id, None)
    if pool:
        pool.reset()


async def execute_parallel(
    agent_specs: List[Dict[str, Any]],
    workspace: Path,
    session_id: str = "",
    timeout: float = 300.0,
) -> Dict[str, dict]:
    """P2: Execute multiple sub-agent tasks in parallel via the session agent pool.

    Args:
        agent_specs: List of dicts, each with keys matching execute_async args
                     (task, agent_type, inherit_context, max_turns, etc.)
        workspace: Workspace path
        session_id: Parent session ID
        timeout: Max seconds to wait for all agents

    Returns:
        Dict mapping agent_id → result dict
    """
    sid = session_id or _PARENT_SESSION_ID
    pool = get_agent_pool(sid)

    # Start all agents
    agent_ids = []
    for spec in agent_specs:
        task = spec.get("task", "").strip()
        if not task or len(task) < 10:
            continue
        agent_type = spec.get("agent_type", "")
        child_task_id = spec.get("_child_task_id", "")

        aid = pool.start_agent(
            agent_type=agent_type,
            task=task,
            inherit_context=spec.get("inherit_context", False),
            max_turns=spec.get("max_turns", 5),
            workspace=workspace,
            child_task_id=child_task_id,
        )
        agent_ids.append(aid)

    if not agent_ids:
        return {}

    logger.info(f"[P2] execute_parallel: {len(agent_ids)} agents started, waiting (timeout={timeout}s)")

    # Wait for all to complete
    results = await pool.wait_all(timeout=timeout)

    # Filter to only the agents we started
    return {aid: results.get(aid, {"success": False, "output": "", "error": "Missing result"})
            for aid in agent_ids}


# Sentinel: set by agentic_loop before execution to pass env/workspace
_PARENT_ENV: Dict[str, str] = {}
_PARENT_WORKSPACE: Path = Path(".")
_PARENT_SESSION_ID: str = ""
_PARENT_MESSAGES: list = []  # P46: parent conversation messages
_PARENT_SKILL_POLICY: Dict[str, Any] = {}  # P103: active skill policy from parent
_PARENT_MODE: str = "code"  # U21: parent agentic mode (code/ask/plan)


def set_parent_context(
    env: Dict[str, str],
    workspace: Path,
    session_id: str,
    messages: list = None,
    skill_policy: dict = None,
    mode: str = "code",
) -> None:
    """Called by the agentic loop to set the parent context for sub-agent execution."""
    global _PARENT_ENV, _PARENT_WORKSPACE, _PARENT_SESSION_ID, _PARENT_MESSAGES, _PARENT_SKILL_POLICY, _PARENT_MODE
    _PARENT_ENV = env
    _PARENT_WORKSPACE = workspace
    _PARENT_SESSION_ID = session_id
    _PARENT_MESSAGES = messages or []
    _PARENT_SKILL_POLICY = skill_policy or {}
    _PARENT_MODE = mode or "code"


def _build_inherited_context(messages: list, max_messages: int = 10) -> str:
    """P46: Build a condensed context from parent conversation for sub-agent inheritance.

    Takes recent non-system messages, replaces tool result content with
    [Summary: ...] lines (if available) or short stubs. This gives the
    sub-agent awareness of what the parent has done without bloating context.

    Mirrors Claw's forkSubagent.ts buildChildMessage() pattern:
    - Full tool_result content replaced with summary placeholders
    - User and assistant text kept intact
    - Capped at max_messages most recent entries
    """
    if not messages:
        return ""

    # Take last max_messages non-system messages
    relevant = [m for m in messages if m.get("role") != "system"][-max_messages:]
    if not relevant:
        return ""

    parts = []
    for msg in relevant:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")

        if msg.get("role") == "tool":
            # Replace full tool content with summary line
            tool_name = msg.get("_tool_name", "tool")
            if content.startswith("[Summary:"):
                # Already has summary — use first line only
                first_line = content.split("\n", 1)[0]
                parts.append(f"[TOOL RESULT {tool_name}]: {first_line}")
            else:
                # Extract first line as ad-hoc summary
                first_line = content.split("\n", 1)[0][:150]
                parts.append(f"[TOOL RESULT {tool_name}]: {first_line}")
        elif msg.get("tool_calls"):
            # Assistant tool calls — show which tools were called
            calls = []
            for tc in msg.get("tool_calls", []):
                func = tc.get("function", {})
                name = func.get("name", "?")
                args_preview = func.get("arguments", "")[:100]
                calls.append(f"{name}({args_preview})")
            if content:
                parts.append(f"[{role}]: {content[:200]}")
            parts.append(f"[{role} TOOL_CALLS]: {'; '.join(calls)}")
        elif content:
            # Regular user/assistant message — keep text (truncated)
            truncated = content[:500] if len(content) > 500 else content
            parts.append(f"[{role}]: {truncated}")

    return "\n\n".join(parts)


# ══════════════════════════════════════════════════════════════
# U11b: Quick-path pre-check for explore tasks
# ══════════════════════════════════════════════════════════════

# U11c/d: Expanded patterns for quick-path matching (EN + CN variants)
# U11d: Two alternatives — with extension OR known extensionless files
_QUICK_FIND_NOEXT = (
    r"(?:Dockerfile|Makefile|README|LICENSE|CHANGELOG|CONTRIBUTING|"
    r"Procfile|Vagrantfile|Gemfile|Rakefile|Brewfile|Taskfile|Justfile|"
    r"\.env|\.gitignore|\.dockerignore|\.editorconfig|\.eslintrc|\.prettierrc|"
    r"NANOBOT\.md|AGENTS\.md|MEMORY\.md|IDENTITY\.md)"
)
_QUICK_FIND_RE = _re.compile(
    r"(?:find|locate|look for|check if|does|is there|exist|where is|"
    r"查找|寻找|有没有|找一下|找找|帮我找|帮我看看有没有|帮我查|找)"
    r".*?"
    r"(?:(" + _QUICK_FIND_NOEXT + r")|([a-zA-Z0-9_\-./]+\.[a-zA-Z]{1,10}))",
    _re.IGNORECASE,
)
_QUICK_LIST_RE = _re.compile(
    r"(?:list|show|browse|what.s in|look at|contents? of|"
    r"查看|列出|浏览|看看|帮我看|显示)"
    r".*?"
    r"(?:director|folder|root|目录|文件夹|结构|structure|文件结构)",
    _re.IGNORECASE,
)
# U11c: Detect external absolute paths (outside workspace)
_EXTERNAL_PATH_RE = _re.compile(
    r"(?:read|list|show|browse|查看|读取|打开|浏览|看看|look at|contents? of)"
    r".*?"
    r"(/(?:tmp|home|var|etc|usr|opt|mnt|root|proc|sys)[a-zA-Z0-9_\-./]*)",
    _re.IGNORECASE,
)


async def _explore_quick_path(task: str, workspace: Path) -> Optional[dict]:
    """U11b: Attempt to resolve simple explore tasks without launching a full sub-agent.

    Returns a result dict if the task was handled, or None to fall through to full sub-agent.
    """
    t0 = time.time()
    task_lower = task.lower()

    # U11c: Pattern 0 — external absolute path (outside workspace)
    ext_match = _EXTERNAL_PATH_RE.search(task)
    if ext_match:
        ext_path = ext_match.group(1).rstrip("/.")
        # Safety: only check existence, never list workspace root as fallback
        elapsed = time.time() - t0
        if os.path.exists(ext_path):
            if os.path.isdir(ext_path):
                try:
                    entries = sorted(os.listdir(ext_path))[:30]
                    items = "\n".join(
                        f"- {e}/" if os.path.isdir(os.path.join(ext_path, e)) else f"- {e}"
                        for e in entries if not e.startswith(".")
                    )
                    output = f"[Quick-path: listing {ext_path} ({elapsed:.1f}s)]\n{items}"
                except PermissionError:
                    output = f"[Quick-path: '{ext_path}' exists but permission denied ({elapsed:.1f}s)]"
            else:
                sz = os.path.getsize(ext_path)
                output = f"[Quick-path: '{ext_path}' is a file ({sz} bytes)]"
        else:
            output = f"[Quick-path: '{ext_path}' does not exist ({elapsed:.1f}s)]"
        logger.info(f"[U11c] Quick-path external path '{ext_path}' in {elapsed:.1f}s")
        return {"success": True, "output": output, "error": "", "_quick_path": True}

    # Pattern 1: "Find <filename>" → direct file search
    find_match = _QUICK_FIND_RE.search(task)
    if find_match:
        target = find_match.group(1) or find_match.group(2)  # U11d: noext or with-ext
        # Search for the file
        found = []
        for root, dirs, files in os.walk(str(workspace)):
            # Skip hidden dirs and common noise
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                       ('node_modules', '__pycache__', '.git', 'venv', '.venv')]
            for f in files:
                if f == target or f.endswith(target):
                    rel = os.path.relpath(os.path.join(root, f), str(workspace))
                    found.append(rel)
                    if len(found) >= 10:
                        break
            if len(found) >= 10:
                break

        elapsed = time.time() - t0
        if found:
            items = "\n".join(f"- {p}" for p in found[:10])
            output = f"[Quick-path: found {len(found)} match(es) in {elapsed:.1f}s]\n{items}"
        else:
            output = f"[Quick-path: '{target}' not found in workspace ({elapsed:.1f}s)]"

        logger.info(f"[U11b] Quick-path find '{target}': {len(found)} results in {elapsed:.1f}s")
        return {"success": True, "output": output, "error": "", "_quick_path": True}

    # Pattern 2: "List directory / show structure" → direct file_list of root
    if _QUICK_LIST_RE.search(task):
        try:
            entries = []
            for item in sorted(os.listdir(str(workspace))):
                full = os.path.join(str(workspace), item)
                if item.startswith('.'):
                    continue
                if os.path.isdir(full):
                    # Count children
                    try:
                        n = len(os.listdir(full))
                    except OSError:
                        n = 0
                    entries.append(f"- {item}/ ({n} items)")
                else:
                    sz = os.path.getsize(full)
                    entries.append(f"- {item} ({sz} bytes)")

            elapsed = time.time() - t0
            items = "\n".join(entries[:30])
            output = f"[Quick-path: workspace root listing in {elapsed:.1f}s]\n{items}"
            logger.info(f"[U11b] Quick-path list: {len(entries)} entries in {elapsed:.1f}s")
            return {"success": True, "output": output, "error": "", "_quick_path": True}
        except Exception as e:
            logger.debug(f"[U11b] Quick-path list failed: {e}")
            return None  # Fall through to full sub-agent

    # No quick-path match — fall through
    return None


async def execute_async(args: dict, workspace: Path) -> dict:
    """Execute a sub-agent task asynchronously.

    This is an async tool — the agentic loop detects and awaits it.
    P46: supports inherit_context=true to pass parent conversation summary.
    P60: supports agent_type for specialized built-in agents.
    P61: agent_type='verify' runs adversarial verification.
    U3: supports background=true for non-blocking background execution.
    """
    global _CURRENT_FORK_DEPTH

    task = args.get("task", "").strip()
    agent_type = args.get("agent_type", "").strip().lower() or None
    inherit_context = bool(args.get("inherit_context", False))
    background = bool(args.get("background", False))
    child_task_id = str(args.get("_child_task_id", "") or "").strip()

    # P60: Resolve agent type config
    agent_config = BUILT_IN_AGENTS.get(agent_type) if agent_type else None
    if agent_type and agent_config is None:
        valid = ", ".join(BUILT_IN_AGENTS.keys())
        return {"success": False, "output": "", "error": f"Unknown agent_type '{agent_type}'. Valid types: {valid}"}

    default_turns = agent_config["max_turns_default"] if agent_config else 5
    max_turns = min(int(args.get("max_turns", default_turns)), 8)

    if not task:
        return {"success": False, "output": "", "error": "No task provided for sub-agent"}

    if len(task) < 10:
        return {"success": False, "output": "", "error": "Task description too short — provide a clear, specific task"}

    # U11b: Quick-path pre-check for simple explore tasks
    # For trivial file/directory lookups, run directly without sub-agent overhead
    if agent_type == "explore":
        quick_result = await _explore_quick_path(task, workspace)
        if quick_result is not None:
            return quick_result

    # Use parent context (set by agentic_loop before tool execution)
    env = _PARENT_ENV
    ws = _PARENT_WORKSPACE or workspace

    # U3: Background fork path
    if background:
        # Anti-recursion: background sub-agents cannot spawn further background tasks
        if _CURRENT_FORK_DEPTH >= _MAX_FORK_DEPTH:
            return {
                "success": False, "output": "",
                "error": (
                    f"Cannot spawn background task: fork depth limit ({_MAX_FORK_DEPTH}) reached. "
                    "Background sub-agents cannot spawn further background tasks."
                ),
            }
        # Anti-flood: cap concurrent background tasks per session
        session_id = _PARENT_SESSION_ID
        active_count = get_active_background_count(session_id)
        if active_count >= _MAX_CONCURRENT_BACKGROUND:
            return {
                "success": False, "output": "",
                "error": (
                    f"Cannot spawn background task: {active_count}/{_MAX_CONCURRENT_BACKGROUND} "
                    "concurrent background tasks already running. Wait for one to finish."
                ),
            }

        if not env:
            return {"success": False, "output": "", "error": "Sub-agent context not initialized (internal error)"}

        # Create/attach a child task for this background sub-agent execution.
        from task_store import create_child_task, create_session_root_task, get_task_store

        parent_store = get_task_store(session_id, ws)
        parent_root = parent_store.get_latest_root_task()
        if parent_root is None:
            parent_root = create_session_root_task(
                ws,
                session_id,
                title="Sub-agent session root",
                objective=task,
                owner="sub_agent",
                priority=5,
                metadata={"source": "sub_agent_background_fallback"},
            )

        child_task = parent_store.get_task(child_task_id) if child_task_id else None
        if child_task is None:
            child_task = create_child_task(
                ws,
                session_id,
                parent_root.id,
                title=f"{agent_type or 'general'}: {task[:48]}".strip(),
                objective=task,
                owner="sub_agent",
                priority=parent_root.priority,
                state="backgrounded",
                metadata={
                    "agent_type": agent_type or "general",
                    "inherit_context": inherit_context,
                    "background": background,
                },
            )
            child_task_id = child_task.id

        # Create a non-background copy of args for the actual execution
        sync_args = dict(args)
        sync_args["background"] = False
        sync_args["_child_task_id"] = child_task_id

        # Wrap in asyncio.Task — runs in the same event loop but doesn't block
        task_id = f"bg_{agent_type or 'general'}_{int(time.time() * 1000) % 100000}"
        child_depth = _CURRENT_FORK_DEPTH + 1

        async def _bg_run():
            global _CURRENT_FORK_DEPTH
            saved_depth = _CURRENT_FORK_DEPTH
            _CURRENT_FORK_DEPTH = child_depth
            try:
                return await execute_async(sync_args, workspace)
            finally:
                _CURRENT_FORK_DEPTH = saved_depth

        atask = asyncio.ensure_future(_bg_run())
        bg_task = BackgroundTask(
            task_id=task_id,
            agent_type=agent_type or "general",
            task_preview=task[:100],
            asyncio_task=atask,
            session_id=session_id,
            fork_depth=child_depth,
        )
        _get_session_tasks(session_id).append(bg_task)

        logger.info(
            f"[U3] Background task {task_id} started: {agent_type or 'general'}, "
            f"depth={child_depth}, active={active_count + 1}/{_MAX_CONCURRENT_BACKGROUND}"
        )

        return {
            "success": True,
            "output": (
                f"[Background task launched]\n"
                f"task_id: {task_id}\n"
                f"agent_type: {agent_type or 'general'}\n"
                f"task: {task[:200]}\n\n"
                f"The sub-agent is now running in the background. "
                f"You will receive a <task-notification> when it finishes. "
                f"Continue with your current work."
            ),
            "error": "",
            "_background_task_id": task_id,
        }

    if not env:
        return {"success": False, "output": "", "error": "Sub-agent context not initialized (internal error)"}

    # Create/attach a child task for this foreground sub-agent execution.
    from task_store import create_child_task, create_session_root_task, get_task_store

    parent_store = get_task_store(_PARENT_SESSION_ID, ws)
    parent_root = parent_store.get_latest_root_task()
    if parent_root is None:
        parent_root = create_session_root_task(
            ws,
            _PARENT_SESSION_ID,
            title="Sub-agent session root",
            objective=task,
            owner="sub_agent",
            priority=5,
            metadata={"source": "sub_agent_foreground_fallback"},
        )

    child_task = parent_store.get_task(child_task_id) if child_task_id else None
    if child_task is None:
        child_task = create_child_task(
            ws,
            _PARENT_SESSION_ID,
            parent_root.id,
            title=f"{agent_type or 'general'}: {task[:48]}".strip(),
            objective=task,
            owner="sub_agent",
            priority=parent_root.priority,
            state="in_progress",
            metadata={
                "agent_type": agent_type or "general",
                "inherit_context": inherit_context,
                "background": background,
            },
        )
        child_task_id = child_task.id
    elif child_task.state in ("created", "planned"):
        try:
            child_task = parent_store.transition(
                child_task_id,
                "in_progress",
                source="execute_async",
                reason="sub-agent started",
            )
        except Exception as exc:
            logger.debug(f"[SubAgent] Failed to mark child task in_progress: {exc}")

    child_task_context = (
        f"[SUB-TASK CONTEXT]\n"
        f"你的任务ID是 {child_task_id}。Your task ID is {child_task_id}.\n"
        f"你的工作是完成以下子任务，不要偏离该目标：\n"
    )

    # P46: Build inherited context if requested
    context_prefix = ""
    if inherit_context and _PARENT_MESSAGES:
        inherited = _build_inherited_context(_PARENT_MESSAGES)
        if inherited:
            context_prefix = (
                "[PARENT CONTEXT — for reference only, do NOT re-execute these actions]\n"
                f"{inherited}\n\n"
                "[YOUR TASK — focus exclusively on this]\n"
            )
            logger.info(f"[SubAgent] P46: Inherited {len(inherited)} chars of parent context")

    # P103: Inject parent skill policy context for coding skill-aware agents
    policy_prefix = ""
    parent_mode = _PARENT_SKILL_POLICY.get("mode", "")
    if parent_mode and agent_type in ("research", "edit", "verify"):
        policy_prefix = (
            f"[SKILL CONTEXT] Parent agent is in /{parent_mode} mode. "
        )
        wp = _PARENT_SKILL_POLICY.get("write_policy", "")
        if wp:
            policy_prefix += f"Parent write_policy={wp}. "
        if agent_type == "research":
            policy_prefix += "You are a read-only research specialist — produce findings only.\n\n"
        elif agent_type == "edit":
            policy_prefix += "You are in DRAFT mode — propose patches only, never apply them directly.\n\n"
        elif agent_type == "verify":
            policy_prefix += "You are verifying the parent's changes. Try to break them.\n\n"

    full_task = f"{policy_prefix}{context_prefix}{child_task_context}{task}" if (policy_prefix or context_prefix or child_task_context) else task

    agent_label = f"[{agent_type}]" if agent_type else "[general]"
    logger.info(f"[SubAgent] {agent_label} Starting ({max_turns} max turns, inherit={inherit_context}"
                f"{f', parent_mode={parent_mode}' if parent_mode else ''}): {task[:100]}...")
    t0 = time.time()

    try:
        # Import agentic_chat_stream lazily to avoid circular imports
        from agentic_loop import agentic_chat_stream

        # Generate a sub-session ID to isolate state
        sub_session = f"{_PARENT_SESSION_ID}_sub_{int(time.time() * 1000) % 100000}"

        # Mailbox: register this sub-agent so it can receive messages
        _agent_id = args.get("_pool_agent_id", sub_session)
        _mb_session = _PARENT_SESSION_ID  # session-scoped isolation
        if _MAILBOX_ENABLED:
            try:
                from services.agent_mailbox import get_mailbox
                get_mailbox().register_agent(_agent_id, session_id=_mb_session)
            except Exception as _mb_err:
                logger.debug(f"[Mailbox] Failed to register {_agent_id}: {_mb_err}")

        # P60: Build kwargs for specialized agent types
        # U21: Inherit parent mode (plan/ask/code) so sub-agents respect mode restrictions
        stream_kwargs: Dict[str, Any] = {
            "user_message": full_task,
            "env": env,
            "session_id": sub_session,
            "workspace": ws,
            "max_turns": max_turns,
            "mode": _PARENT_MODE,
        }
        if agent_config:
            # U2: Append critical_reminder to system prompt for reinforcement
            _sys_prompt = agent_config["system_prompt"]
            _reminder = agent_config.get("critical_reminder", "")
            if _reminder:
                _sys_prompt = f"{_sys_prompt}\n\n{_reminder}"
            stream_kwargs["agent_system_prompt"] = _sys_prompt
            stream_kwargs["allowed_tools"] = agent_config["allowed_tools"]

        # Collect all text chunks from the sub-agent's response
        text_parts = []
        tool_calls_count = 0
        tools_used = set()
        turns_used = 0
        blocked_count = 0
        verification_ran = False

        # Mailbox: check for incoming messages before streaming
        if _MAILBOX_ENABLED:
            try:
                from services.agent_mailbox import get_mailbox, MAILBOX_INJECT_MAX_CHARS
                _inbox = get_mailbox().check_messages(_agent_id, session_id=_mb_session)
                if _inbox:
                    _mb_lines = [f"[MAILBOX — {len(_inbox)} message(s) received]"]
                    for _m in _inbox:
                        _mb_lines.append(f"From {_m['from']}: {_m['content']}")
                    _mb_context = "\n".join(_mb_lines)
                    # Token budget: truncate injected content
                    if len(_mb_context) > MAILBOX_INJECT_MAX_CHARS:
                        _mb_context = _mb_context[:MAILBOX_INJECT_MAX_CHARS] + (
                            f"\n[...truncated, {len(_mb_context) - MAILBOX_INJECT_MAX_CHARS} chars omitted]")
                    full_task = f"{_mb_context}\n\n{full_task}"
                    stream_kwargs["user_message"] = full_task
                    logger.info(f"[Mailbox] Injected {len(_inbox)} message(s) into {_agent_id} "
                                f"({len(_mb_context)} chars)")
            except Exception as _mb_err:
                logger.debug(f"[Mailbox] check failed for {_agent_id}: {_mb_err}")

        async for event in agentic_chat_stream(**stream_kwargs):
            evt_type = event.get("type", "")
            if evt_type == "chunk":
                text_parts.append(event.get("content", ""))
            elif evt_type == "tool_result":
                tool_calls_count += 1
                tools_used.add(event.get("tool_name") or event.get("name", ""))
                # P103: Count policy-blocked tool calls
                result_content = event.get("content", "")
                if "[BLOCKED by" in result_content:
                    blocked_count += 1
            elif evt_type == "agentic_done":
                turns_used = event.get("turns", 0)
                verification_ran = event.get("verification_ran", False)
            elif evt_type == "error":
                error_msg = event.get("message", "Unknown error")
                logger.error(f"[SubAgent] {agent_label} Error: {error_msg}")
                return {"success": False, "output": "", "error": f"Sub-agent error: {error_msg}"}

        elapsed = time.time() - t0
        final_text = "".join(text_parts).strip()

        if not final_text:
            final_text = "(Sub-agent completed but produced no text output)"

        # U11b: Hard output truncation for explore agents (prevent over-delivery)
        max_chars = agent_config.get("max_output_chars") if agent_config else None
        if max_chars and len(final_text) > max_chars:
            truncated_text = final_text[:max_chars]
            # Cut at last newline to avoid mid-line truncation
            last_nl = truncated_text.rfind("\n")
            if last_nl > max_chars // 2:
                truncated_text = truncated_text[:last_nl]
            final_text = truncated_text + f"\n\n[Truncated: {len(final_text)} → {len(truncated_text)} chars]"
            logger.info(f"[U11b] Explore output truncated: {len(final_text)} chars")

        # Build metadata header
        type_tag = f" type={agent_type}" if agent_type else ""
        extras = []
        if blocked_count > 0:
            extras.append(f"{blocked_count} blocked")
        if verification_ran:
            extras.append("verified")
        if parent_mode:
            extras.append(f"parent=/{parent_mode}")
        extras_str = f", {', '.join(extras)}" if extras else ""
        meta = (
            f"[Sub-agent {agent_type or 'general'} summary: "
            f"{turns_used} turns, {tool_calls_count} tool calls "
            f"({', '.join(sorted(tools_used)) or 'none'}), "
            f"{elapsed:.1f}s{extras_str}]"
        )

        # Mailbox: unregister agent on completion
        if _MAILBOX_ENABLED:
            try:
                from services.agent_mailbox import get_mailbox
                get_mailbox().unregister_agent(_agent_id, session_id=_mb_session)
            except Exception:
                pass

        logger.info(f"[SubAgent] {agent_label} Completed: {turns_used} turns, "
                     f"{tool_calls_count} tools ({blocked_count} blocked), "
                     f"{elapsed:.1f}s, {len(final_text)} chars")
        try:
            if child_task_id:
                completion_store = get_task_store(_PARENT_SESSION_ID, ws)
                child_task = completion_store.get_task(child_task_id)
                if child_task is not None and not child_task.is_terminal:
                    completion_store.transition(
                        child_task_id,
                        "completed",
                        result_summary=final_text[:500],
                        source="execute_async",
                        reason="sub-agent completed successfully",
                    )
        except Exception as _task_exc:
            logger.debug(f"[SubAgent] Failed to mark child task completed: {_task_exc}")
        return {
            "success": True,
            "output": f"{meta}\n\n{final_text}",
            "error": "",
            "_child_task_id": child_task_id,
        }

    except Exception as e:
        elapsed = time.time() - t0
        error_msg = str(e)[:500]
        logger.error(f"[SubAgent] {agent_label} Failed after {elapsed:.1f}s: {error_msg}")
        try:
            if child_task_id:
                from task_store import get_task_store
                failure_store = get_task_store(_PARENT_SESSION_ID, ws)
                failure_child = failure_store.get_task(child_task_id)
                if failure_child is not None and not failure_child.is_terminal:
                    failure_store.transition(
                        child_task_id,
                        "failed",
                        blocked_reason=error_msg,
                        result_summary=error_msg,
                        source="execute_async",
                        reason="sub-agent failed",
                    )
        except Exception as _task_exc:
            logger.debug(f"[SubAgent] Failed to mark child task failed: {_task_exc}")
        return {"success": False, "output": "", "error": f"Sub-agent failed: {error_msg}"}


def execute(args: dict, workspace: Path) -> dict:
    """Synchronous wrapper — creates or reuses an event loop to run the async execute."""
    try:
        loop = asyncio.get_running_loop()
        # We're already in an async context — this shouldn't happen normally
        # because the agentic loop should call execute_async directly
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, execute_async(args, workspace))
            return future.result(timeout=120)
    except RuntimeError:
        # No running loop — safe to use asyncio.run
        return asyncio.run(execute_async(args, workspace))
