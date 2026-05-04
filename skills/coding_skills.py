"""
P101: Coding Skills — Claude Code-style runtime-enforced programming modes.

4 skills that are NOT just prompt templates — they drive runtime behavior:
  1. /analyze — Code understanding. Write=forbid. Read-only tools only.
  2. /debug   — Root cause finding. Write=explicit_only. Evidence-first.
  3. /verify  — Test/lint/validate. Write=forbid. Must produce verdict.
  4. /refactor — Structure changes. Write=allowed_with_verification.

Key difference from regular skills:
  - `mode` drives tool filtering and write gating in agentic_loop.py
  - `write_policy` is ENFORCED at runtime, not suggested via prompt
  - `requires_verification` triggers pending_verification state machine
  - `completion_criteria` gates final response (model can't just skip steps)
  - `output_fields` structures the response (not optional)

Design references:
  - Claw /verify: SKILL.md + examples/ + file extraction (verify.ts)
  - Claw /simplify: 3 parallel sub-agents for review (simplify.ts)
  - Claw /debug: allowedTools=['Read','Grep','Glob'], reads session log (debug.ts)
  - Claw /batch: plan → approve → parallel workers (batch.ts)
  - Nanobot existing /verify in bundled.py: adversarial verification
  - Nanobot existing /debug in bundled.py: generic issue diagnosis

These 4 skills REPLACE the /debug and /verify in bundled.py with
runtime-enforced versions. The originals remain accessible as
/debug_legacy and /verify_legacy if needed.
"""
from skills import SkillDefinition, register_skill


# ═══════════════════════════════════════════════════════════════
# Tool sets (based on real Nanobot tool names from tools/__init__.py)
# ═══════════════════════════════════════════════════════════════

# Read-only tools — safe for any mode
_READ_TOOLS = [
    "file_read", "grep_search", "find_by_name", "file_list", "code_intel",
]

# Write tools — restricted by mode
_WRITE_TOOLS = ["file_edit", "file_write"]

# Execution tools — controlled per mode
_EXEC_TOOLS = ["shell_execute", "python_execute"]

# Agent tools
_AGENT_TOOLS = ["sub_agent"]

# Utility tools — generally allowed everywhere
_UTIL_TOOLS = ["todo_manage", "memory"]


# ═══════════════════════════════════════════════════════════════
# 1. /analyze — Code Understanding & Call Chain Analysis
# ═══════════════════════════════════════════════════════════════

_ANALYZE_PROMPT = """# /analyze — Code Understanding Mode

You are now in **analyze mode**. Your job is to understand code structure,
not to change it.

## System Constraints (enforced by runtime — you cannot override these)
- **Write tools are BLOCKED** — file_edit, file_write will be rejected
- **Shell commands are BLOCKED** unless explicitly unlocked
- Focus exclusively on reading, searching, and understanding

## Strategy
1. **Start broad**: Use find_by_name and file_list to map the area
2. **Search targeted**: Use grep_search for specific symbols, call sites, imports
3. **Read key files**: Use file_read on entry points, then follow the call chain
4. **Build the map**: Track entrypoints → callees → dependencies → side effects
5. **Batch reads**: Call multiple read-only tools in the same turn for speed

## Anti-hallucination Rules
- Do NOT guess the content of files you haven't read
- Do NOT claim a function does X unless you've seen the code
- If grep results are truncated, say so — don't infer from what's not shown
- If you can't find something, say "not found" — don't fabricate

## Output Structure — Adaptive (D9)
Choose the output format based on the user's actual question:

**For architecture / call-chain / deep-dive questions** → use the full structured report:
### Scope / ### Entrypoints / ### Call Flow / ### Key Files / ### Risks / ### Next Reads

**For simple questions** (count functions, list imports, read a file, check a value, etc.)
→ answer DIRECTLY and concisely. Do NOT force the full report structure.
A one-paragraph answer or a short list is perfectly fine for simple questions.

**Rule of thumb**: If the answer fits in ≤5 lines, just give the answer.
"""


def _build_analyze_prompt(args: str) -> str:
    prompt = _ANALYZE_PROMPT
    if args:
        prompt += f"\n## Analysis Target\n\n{args}"
    else:
        prompt += (
            "\n## Analysis Target\n\n"
            "No specific target provided. Ask the user what code area, "
            "module, or question they want analyzed."
        )
    return prompt


# ═══════════════════════════════════════════════════════════════
# 2. /debug — Root Cause Analysis
# ═══════════════════════════════════════════════════════════════

_DEBUG_PROMPT = """# /debug — Root Cause Analysis Mode

You are now in **debug mode**. Your job is to find the root cause of a bug,
not to immediately fix it.

## System Constraints (enforced by runtime)
- **Write tools are BLOCKED by default** — no fixing until root cause is found
- **Shell is ALLOWED** — for reproducing errors, reading logs, running tests
- If you identify a clear root cause AND the user's request implies fixing,
  say "I've identified the root cause. Switching to fix mode." — the runtime
  will then unlock write tools for targeted fixes only

## Strategy (follow this order)
1. **Clarify symptom**: What exactly fails? Error message? Wrong behavior?
2. **Reproduce**: Run the failing command/test to see the actual error
3. **Trace the path**: From error location, trace backwards through call chain
4. **Collect evidence**: Read relevant code, check git log, grep for related patterns
5. **Form hypothesis**: State your best guess for root cause with evidence
6. **Verify hypothesis**: Find code evidence that confirms or rejects it
7. **If fixing**: Make minimal targeted fix, then immediately verify

## Anti-hallucination Rules
- Do NOT guess the cause before reading the relevant code
- Do NOT say "the bug is probably in X" without evidence from a tool result
- Do NOT skip reproduction — always try to see the actual error first
- Track rejected hypotheses so you don't loop back to them

## Required Output Structure

### Symptom
What the user reported or what you observed.

### Reproduction
Command run and output observed (or why reproduction wasn't possible).

### Evidence
Specific code/log lines that point to the cause, with file:line references.

### Likely Root Cause
Your diagnosis, with the evidence chain that supports it.

### Fix Options
1-3 concrete fix approaches, ranked by risk/simplicity.

### Validation Plan
How to confirm the fix works (specific test command or verification step).
"""


def _build_debug_prompt(args: str) -> str:
    prompt = _DEBUG_PROMPT
    if args:
        prompt += f"\n## Bug Report\n\n{args}"
    else:
        prompt += (
            "\n## Bug Report\n\n"
            "No specific issue described. Check for:\n"
            "- Recent error output or failed tests\n"
            "- `git log --oneline -5` for recent changes that might have introduced bugs\n"
            "- `git diff` for uncommitted changes that might be broken"
        )
    return prompt


# ═══════════════════════════════════════════════════════════════
# 3. /verify — Test, Lint, and Behavior Verification
# ═══════════════════════════════════════════════════════════════

_VERIFY_PROMPT = """# /verify — Verification Mode

You are now in **verify mode**. Your job is to produce evidence that code
changes work correctly. You are a skeptical reviewer — assume nothing.

## System Constraints (enforced by runtime)
- **Write tools are BLOCKED** — you verify, you don't fix
- **Shell is ALLOWED** — for running tests, lint, build, syntax checks
- You MUST run at least one verification command before finishing
- You CANNOT finish with just "looks good" — you need evidence

## Verification Priority (strongest → weakest evidence)
1. **Targeted test pass/fail** — run tests that cover the changed code
2. **Full test suite** — run the project's test command
3. **Lint / type check / build** — `python -m py_compile`, `mypy`, `ruff`, etc.
4. **Shell command output** — run the changed code and inspect output
5. **Static code review** — read the diff and check for obvious issues
6. **Blocked** — explain why you can't verify and what's needed

## Strategy
1. **Find test entry**: Look for pytest.ini, Makefile, package.json scripts, etc.
2. **Run minimal relevant tests first**: Don't run the full suite if targeted tests exist
3. **On failure**: Read the failure output carefully, report it accurately
4. **Check the diff**: `git diff` to see what actually changed
5. **Cross-reference**: grep for callers of changed functions — are they affected?

## Anti-hallucination Rules
- Do NOT say "tests should pass" without running them
- Do NOT say "the code looks correct" as your only evidence
- If a test fails, report the EXACT failure message — don't summarize loosely
- If you can't find tests, say so explicitly — don't pretend they exist

## Required Output Structure

### Verification Scope
What was verified and what was NOT verified.

### Commands Run
Exact commands executed and their exit codes.

### Observed Results
Actual output from each verification step.

### Status
One of: **PASSED** | **FAILED** | **PARTIALLY_VERIFIED** | **BLOCKED**

### Failures / Risks
Any failures found, with exact error messages and file:line if available.

### Recommended Next Step
What should happen next based on the verification results.
"""


def _build_verify_prompt(args: str) -> str:
    prompt = _VERIFY_PROMPT
    if args:
        prompt += f"\n## What to Verify\n\n{args}"
    else:
        prompt += (
            "\n## What to Verify\n\n"
            "No specific target. Verify the most recent changes:\n"
            "1. Run `git diff` to see what changed\n"
            "2. Find and run relevant tests\n"
            "3. Report results"
        )
    return prompt


# ═══════════════════════════════════════════════════════════════
# 4. /refactor — Structure Modification with Mandatory Verification
# ═══════════════════════════════════════════════════════════════

_REFACTOR_PROMPT = """# /refactor — Refactor Mode (Write + Verify)

You are now in **refactor mode**. You can modify code, but every write
operation triggers a mandatory verification step before you can finish.

## System Constraints (enforced by runtime)
- **Write tools are ALLOWED** — but each write sets pending_verification=true
- **You CANNOT finish until verification passes** — the runtime blocks it
- After writing, you MUST run at least one verification step
- If verification fails, you must fix or revert before finishing

## Strategy (follow this order)
1. **Analyze first**: Read the code area, understand the current structure
2. **Plan the change**: State what you'll change and why (one goal at a time)
3. **Check impact**: grep for callers/references before modifying signatures
4. **Make minimal edits**: One logical change per edit — don't batch unrelated changes
5. **Verify immediately**: After each edit batch, run tests or verify
6. **Summarize**: List all changed files and remaining risks

## Rules
- **One refactor goal at a time** — don't combine unrelated changes
- **Never change behavior** — refactor means same behavior, better structure
- **If behavior change is needed**, stop and ask the user first
- **If verification fails**, fix the issue before making more changes
- **Don't hide risk** — if you're unsure about an impact, say so
- **E2 RENAME COMPLETENESS**: When renaming a function/variable/class, also update:
  docstrings, comments, string literals, log messages, and error messages that reference
  the old name. A rename is not complete until ALL textual references are consistent.

## Anti-hallucination Rules
- Do NOT claim "this change is safe" without checking callers
- Do NOT skip verification — the runtime will block you
- If tests fail after your change, that's a real failure — don't dismiss it
- If you can't find tests, run at minimum: syntax check + read-back the edited file

## Required Output Structure

### Refactor Goal
What structural improvement was attempted and why.

### Files Changed
List of every file modified, with one-line description of the change.

### What Changed
Detailed description of the actual modifications made.

### Behavior Assumptions
What existing behavior is assumed to be preserved, and why you believe it is.

### Verification
Commands run and their results (PASSED/FAILED).

### Residual Risks
Anything that might still break, callers not yet checked, tests not available.
"""


def _build_refactor_prompt(args: str) -> str:
    prompt = _REFACTOR_PROMPT
    if args:
        prompt += f"\n## Refactor Target\n\n{args}"
    else:
        prompt += (
            "\n## Refactor Target\n\n"
            "No specific target provided. Ask the user what code they want "
            "to refactor and what improvement they're looking for."
        )
    return prompt


# ═══════════════════════════════════════════════════════════════
# Registration
# ═══════════════════════════════════════════════════════════════

def register_coding_skills() -> None:
    """Register 4 Claude Code-style coding skills with runtime enforcement.

    Called from skills/__init__.py after bundled skills.
    These REPLACE /debug and /verify from bundled.py with enforced versions.
    """

    # ── /analyze ──
    register_skill(SkillDefinition(
        name="analyze",
        description="Code understanding: map structure, trace call chains, identify dependencies. Read-only — cannot modify files.",
        prompt_builder=_build_analyze_prompt,
        aliases=["analyse", "understand", "trace", "map"],
        uses_sub_agent=False,
        argument_hint="<code area or question>",
        when_to_use="When the user wants to understand code structure, trace a call chain, find dependencies, or map a module's architecture.",
        # P101: Runtime enforcement
        mode="analyze",
        write_policy="forbid",
        requires_verification=False,
        allowed_tools=_READ_TOOLS + _AGENT_TOOLS + _UTIL_TOOLS,
        disallowed_tools=_WRITE_TOOLS + _EXEC_TOOLS,
        completion_criteria=[
            "Has answered the user's structural/understanding question",
            "Has listed key entry points, dependencies, and risk areas",
            "Has stated what is still unknown if analysis is incomplete",
        ],
        output_fields=[
            "scope", "entrypoints", "call_flow", "key_files",
            "risks_or_unknowns",
        ],
        default_subagent="research",
    ))

    # ── /debug ──
    # NOTE: This replaces the existing /debug in bundled.py.
    # The old version is re-registered as /debug_legacy below.
    register_skill(SkillDefinition(
        name="debug",
        description="Root cause analysis: reproduce, trace, diagnose bugs. Write blocked until cause is found.",
        prompt_builder=_build_debug_prompt,
        aliases=["diagnose", "rootcause", "bisect"],
        uses_sub_agent=False,
        argument_hint="<bug description or error message>",
        when_to_use="When encountering errors, test failures, unexpected behavior, or when the user says 'debug', 'fix bug', 'why does this fail'.",
        disable_model_invocation=True,
        # P101: Runtime enforcement
        mode="debug",
        write_policy="explicit_only",
        requires_verification=False,
        allowed_tools=_READ_TOOLS + ["shell_execute"] + _AGENT_TOOLS + _UTIL_TOOLS,
        disallowed_tools=["file_edit", "file_write", "python_execute"],
        completion_criteria=[
            "Has identified a likely root cause with code evidence",
            "OR has narrowed the problem to a specific module/function",
            "OR has explicitly stated why further diagnosis is blocked",
        ],
        output_fields=[
            "symptom", "reproduction", "evidence",
            "likely_root_cause", "fix_options",
        ],
        default_subagent="research",
    ))

    # ── /verify ──
    # NOTE: This replaces the existing /verify in bundled.py.
    # The old version is re-registered as /verify_legacy below.
    register_skill(SkillDefinition(
        name="verify",
        description="Test, lint, validate code changes. Must produce evidence — cannot just say 'looks good'.",
        prompt_builder=_build_verify_prompt,
        aliases=["check", "test_changes", "validate", "lint"],
        uses_sub_agent=False,
        argument_hint="<what to verify>",
        when_to_use="After implementing changes, to verify correctness. When the user says 'verify', 'test this', 'check if it works', 'run tests'.",
        # P101: Runtime enforcement
        mode="verify",
        write_policy="forbid",
        requires_verification=False,
        allowed_tools=_READ_TOOLS + ["shell_execute"] + _AGENT_TOOLS + _UTIL_TOOLS,
        disallowed_tools=["file_edit", "file_write", "python_execute"],
        completion_criteria=[
            "Has run at least one verification command (test/lint/build/syntax check)",
            "Has output an explicit status: PASSED/FAILED/PARTIALLY_VERIFIED/BLOCKED",
        ],
        output_fields=[
            "verification_scope", "commands_run", "observed_results",
            "status", "failures_or_risks", "recommended_next",
        ],
        default_subagent="verify",
    ))

    # ── /refactor ──
    register_skill(SkillDefinition(
        name="refactor",
        description="Structure modification with mandatory post-write verification. Cannot finish until verification passes.",
        prompt_builder=_build_refactor_prompt,
        aliases=["restructure", "cleanup_code", "reorganize"],
        uses_sub_agent=False,
        argument_hint="<refactor target and goal>",
        when_to_use="When the user wants to restructure code without changing behavior — rename, extract, move, simplify, reduce duplication.",
        # P101: Runtime enforcement
        mode="refactor",
        write_policy="allowed_with_verification",
        requires_verification=True,
        allowed_tools=_READ_TOOLS + _WRITE_TOOLS + ["shell_execute"] + _AGENT_TOOLS + _UTIL_TOOLS,
        disallowed_tools=["python_execute"],
        completion_criteria=[
            "Has completed the planned structural change",
            "Has run at least one verification and it passed (or failures are addressed)",
            "Has listed all modified files and residual risks",
        ],
        output_fields=[
            "refactor_goal", "files_changed", "what_changed",
            "behavior_assumptions", "verification", "residual_risks",
        ],
        default_subagent="research",
    ))
