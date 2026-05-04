"""
P95/P97: Bundled Skills — Ship with Nanobot.

7 skills inspired by Claw's most valuable bundled skills:
  1. /simplify — Code review and cleanup (3 parallel sub_agents)
  2. /remember — Memory review and organization
  3. /verify — Adversarial verification of changes
  4. /commit — Auto-generate commit messages
  5. /debug — Diagnose issues in the current session
  6. /batch — Parallel work orchestration (research + plan + N agents)
  7. /skillify — Capture session workflow as reusable SKILL.md
"""
from skills import SkillDefinition, register_skill


# ═══════════════════════════════════════════════════════════════
# 1. /simplify — Code Review and Cleanup
# ═══════════════════════════════════════════════════════════════
_SIMPLIFY_PROMPT = """# Simplify: Code Review and Cleanup

Review all changed files for reuse, quality, and efficiency. Fix any issues found.

## Phase 1: Identify Changes

Run `git diff` (or `git diff HEAD` if there are staged changes) to see what changed. If there are no git changes, review the most recently modified files that the user mentioned or that you edited earlier in this conversation.

## Phase 2: Launch Three Review Agents in Parallel

Use the sub_agent tool to launch all three agents concurrently in a single message. Pass each agent the full diff so it has the complete context.

### Agent 1: Code Reuse Review (agent_type='explore')

For each change:
1. **Search for existing utilities and helpers** that could replace newly written code. Look for similar patterns elsewhere in the codebase.
2. **Flag any new function that duplicates existing functionality.** Suggest the existing function to use instead.
3. **Flag any inline logic that could use an existing utility** — hand-rolled string manipulation, manual path handling, custom type guards.

### Agent 2: Code Quality Review (agent_type='explore')

Review the same changes for hacky patterns:
1. **Redundant state**: state that duplicates existing state, cached values that could be derived
2. **Parameter sprawl**: adding new parameters instead of generalizing
3. **Copy-paste with slight variation**: near-duplicate code blocks that should be unified
4. **Leaky abstractions**: exposing internal details that should be encapsulated
5. **Unnecessary comments**: comments explaining WHAT (well-named identifiers already do that) — keep only non-obvious WHY

### Agent 3: Efficiency Review (agent_type='explore')

Review the same changes for efficiency:
1. **Unnecessary work**: redundant computations, repeated file reads, duplicate API calls, N+1 patterns
2. **Missed concurrency**: independent operations run sequentially when they could run in parallel
3. **Hot-path bloat**: new blocking work added to startup or per-request paths
4. **Memory**: unbounded data structures, missing cleanup, event listener leaks
5. **Overly broad operations**: reading entire files when only a portion is needed

## Phase 3: Fix Issues

Wait for all three agents to complete. Aggregate their findings and fix each issue directly. If a finding is a false positive, note it and move on.

When done, briefly summarize what was fixed (or confirm the code was already clean).
"""


def _build_simplify_prompt(args: str) -> str:
    prompt = _SIMPLIFY_PROMPT
    if args:
        prompt += f"\n## Additional Focus\n\n{args}"
    return prompt


# ═══════════════════════════════════════════════════════════════
# 2. /remember — Memory Review and Organization
# ═══════════════════════════════════════════════════════════════
_REMEMBER_PROMPT = """# Memory Review

## Goal
Review your memory landscape and produce a clear report of proposed changes. Do NOT apply changes — present proposals for user approval.

## Steps

### 1. Gather all memory layers
Use the memory tool (action='list') to see all stored memories. Also check if NANOBOT.md exists in the workspace root.

### 2. Classify each memory entry
For each memory, determine if it should:
- **Stay** — still relevant and correctly categorized
- **Update** — content is outdated or description needs improvement
- **Delete** — no longer relevant, duplicated, or contradicted by newer info
- **Promote** — should be in NANOBOT.md instead (project-wide conventions)

### 3. Identify cleanup opportunities
Scan for:
- **Duplicates**: memories covering the same topic
- **Outdated**: memories contradicted by current project state
- **Conflicts**: contradictions between memories
- **Type mismatches**: memories categorized as wrong type

### 4. Present the report
Output a structured report grouped by action:
1. **No action needed** — memories that are fine
2. **Updates** — memories to modify, with proposed changes
3. **Deletions** — memories to remove, with rationale
4. **Promotions** — entries to move to NANOBOT.md

## Rules
- Present ALL proposals before making any changes
- Do NOT modify memories without explicit user approval
- Ask about ambiguous entries — don't guess
"""


def _build_remember_prompt(args: str) -> str:
    prompt = _REMEMBER_PROMPT
    if args:
        prompt += f"\n## Additional context from user\n\n{args}"
    return prompt


# ═══════════════════════════════════════════════════════════════
# 3. /verify — Adversarial Verification
# ═══════════════════════════════════════════════════════════════
_VERIFY_PROMPT = """# Verify: Adversarial Code Verification

Your job is to verify that recent code changes are correct. Act as a skeptical reviewer — try to break the implementation.

## Phase 1: Identify what changed

Run `git diff` to see the changes. If no git changes, ask the user what to verify.

## Phase 2: Verification checks

For each changed file, verify:

### Correctness
- Does the code do what the commit message / user intent says?
- Are there edge cases not handled? (empty inputs, None values, boundary conditions)
- Do error paths work correctly? (try/catch, error returns, fallbacks)

### Compatibility
- Do the changes break any existing callers? grep for function/class usages
- Are imports correct and all referenced names defined?
- Do type signatures match between callers and callees?

### Testing
- Run existing tests: `python -m pytest` or the project's test command
- If tests fail, report which ones and why
- If there are no tests for the changed code, note this as a gap

### Regression risks
- Could these changes affect performance? (hot paths, loops, I/O)
- Could they affect other features? (shared state, global config)

## Phase 3: Report

Use the sub_agent tool with agent_type='verify' to run the adversarial verification.

Output a clear verdict:
- ✅ **PASS** — changes look correct
- ⚠️ **WARN** — changes work but have concerns (list them)
- ❌ **FAIL** — found issues that need fixing (list them with specific locations)
"""


def _build_verify_prompt(args: str) -> str:
    prompt = _VERIFY_PROMPT
    if args:
        prompt += f"\n## User Request\n\n{args}"
    return prompt


# ═══════════════════════════════════════════════════════════════
# 4. /commit — Auto-generate Commit Message
# ═══════════════════════════════════════════════════════════════
_COMMIT_PROMPT = """# Commit: Generate and Execute Git Commit

## Steps

### 1. Gather changes
Run `git diff --staged` first. If nothing is staged, run `git diff` to see unstaged changes.
If there are unstaged changes, stage them with `git add -A` (ask user first if there are untracked files).

### 2. Analyze the diff
Understand what changed:
- Which files were modified/added/deleted
- What is the logical grouping of changes
- What is the primary purpose

### 3. Generate commit message
Follow the Conventional Commits format:

```
<type>(<scope>): <description>

<body>
```

Types: feat, fix, refactor, test, docs, chore, perf, style, ci, build
- `<description>`: imperative mood, lowercase, no period, ≤72 chars
- `<body>`: optional, explain WHY not WHAT (the diff shows WHAT)

### 4. Present for approval
Show the generated commit message and the list of files. Ask the user to approve before running `git commit`.

### 5. Execute (only after approval)
Run `git commit -m "<message>"` with the approved message.
"""


def _build_commit_prompt(args: str) -> str:
    prompt = _COMMIT_PROMPT
    if args:
        prompt += f"\n## Additional Instructions\n\n{args}"
    return prompt


# ═══════════════════════════════════════════════════════════════
# 5. /debug — Diagnose Issues
# ═══════════════════════════════════════════════════════════════
_DEBUG_PROMPT = """# Debug: Diagnose Current Issues

Help the user debug an issue they're encountering.

## Steps

### 1. Understand the problem
Read the user's description carefully. If no description is provided, check for:
- Recent error output in terminal
- Failed tests
- Git status for recent changes that might have introduced bugs

### 2. Gather evidence
- Read relevant log files or error output
- Check recent git changes: `git log --oneline -5` and `git diff`
- Run failing tests if identified
- Read the specific files mentioned in error traces

### 3. Root cause analysis
- Identify the root cause, not just symptoms
- Trace the error path through the code
- Check for common issues: import errors, type mismatches, missing config, race conditions

### 4. Propose fix
- Suggest a specific fix with code changes
- If unsure, suggest diagnostic steps to narrow down the issue
- If the fix is straightforward, implement it directly (with user approval for destructive changes)

### 5. Verify
After fixing, run the relevant tests or reproduce the original error to confirm the fix works.
"""


def _build_debug_prompt(args: str) -> str:
    prompt = _DEBUG_PROMPT
    if args:
        prompt += f"\n## Issue Description\n\n{args}"
    else:
        prompt += (
            "\n## Issue Description\n\n"
            "The user did not describe a specific issue. "
            "Check for recent errors, failed tests, or notable issues in the workspace."
        )
    return prompt


# ═══════════════════════════════════════════════════════════════
# 6. /batch — Parallel Work Orchestration
# ═══════════════════════════════════════════════════════════════
_BATCH_PROMPT = """# Batch: Parallel Work Orchestration

You are orchestrating a large, parallelizable change across this codebase.

## Phase 1: Research and Plan

1. **Understand the scope.** Use sub_agent (agent_type='explore') to deeply research what this instruction touches. Find all the files, patterns, and call sites that need to change. Understand existing conventions so the migration is consistent.

2. **Decompose into independent units.** Break the work into 3-10 self-contained units. Each unit must:
   - Be independently implementable without shared state with sibling units
   - Be mergeable on its own without depending on another unit landing first
   - Be roughly uniform in size (split large units, merge trivial ones)

   Scale the count to the actual work: few files → closer to 3; many files → closer to 10.

3. **Write the plan.** Present:
   - A summary of what you found during research
   - A numbered list of work units — for each: a short title, the list of files/directories it covers, and a one-line description of the change
   - The verification steps each worker should run after making changes

4. **Ask for approval** before proceeding to Phase 2.

## Phase 2: Spawn Workers

Once the plan is approved, spawn one sub_agent per work unit. **Launch them all in a single response so they run in parallel.**

For each agent, the prompt must be fully self-contained. Include:
- The overall goal (the user's instruction)
- This unit's specific task (title, file list, change description — copied from your plan)
- Any codebase conventions you discovered that the worker needs to follow
- Verification instructions

Worker completion checklist:
1. **Implement** the change for this unit
2. **Run tests** — `python -m pytest` or the project's test command
3. **Report** — end with a clear summary of what was changed

## Phase 3: Track Progress

After launching all workers, render a status table:

| # | Unit | Status |
|---|------|--------|
| 1 | <title> | running |
| 2 | <title> | running |

As agents complete, update the table. When all agents have reported, render the final table and a one-line summary (e.g., "8/10 units completed successfully").
"""

_BATCH_NO_ARGS = """Provide an instruction describing the batch change you want to make.

Examples:
  /batch migrate all print() calls to use logging
  /batch add type annotations to all untyped function parameters
  /batch replace all uses of os.path with pathlib"""


def _build_batch_prompt(args: str) -> str:
    if not args.strip():
        return _BATCH_NO_ARGS
    return _BATCH_PROMPT + f"\n## User Instruction\n\n{args}"


# ═══════════════════════════════════════════════════════════════
# 7. /skillify — Capture Session as Reusable Skill
# ═══════════════════════════════════════════════════════════════
_SKILLIFY_PROMPT = """# Skillify: Capture This Session as a Reusable Skill

You are capturing this session's repeatable process as a reusable skill.

## Step 1: Analyze the Session

Before asking any questions, analyze the conversation so far to identify:
- What repeatable process was performed
- What the inputs/parameters were
- The distinct steps (in order)
- The success criteria for each step
- Where the user corrected or steered you
- What tools were needed

## Step 2: Interview the User

Ask clarifying questions to refine the skill:

**Round 1: High-level confirmation**
- Suggest a name and description for the skill based on your analysis
- Confirm the high-level goal and success criteria

**Round 2: Details**
- Present the steps you identified as a numbered list
- Ask if the skill needs arguments (parameters the user provides each time)
- Ask where to save: personal (`~/.nanobot/skills/`) or project (`.nanobot/skills/`)

**Round 3: Per-step refinement**
For each step, if not obvious, ask:
- What does this step produce that later steps need?
- What proves this step succeeded?
- Are any steps independent and could run in parallel?
- What are the hard constraints or preferences?

IMPORTANT: Pay special attention to places where the user corrected you during the session — these become rules in the skill.

Stop interviewing once you have enough information. Don't over-ask for simple processes!

## Step 3: Write the SKILL.md

Create the skill directory and file at the chosen location.

Use this format:

```markdown
---
name: {{skill-name}}
description: {{one-line description}}
aliases: {{comma-separated aliases}}
argument-hint: "{{hint showing argument placeholders}}"
arguments:
  - arg_name_1
  - arg_name_2
when_to_use: {{when Claude should suggest this skill}}
---

# {{Skill Title}}

Description of what this skill does.

## Inputs
- `$arg_name_1`: Description of this input

## Goal
Clearly stated goal for this workflow.

## Steps

### 1. Step Name
What to do in this step. Be specific and actionable.

**Success criteria**: What shows this step is done.

### 2. Next Step
...
```

**Frontmatter rules:**
- `when_to_use` is CRITICAL — tells the model when to auto-suggest. Start with "Use when..." and include trigger phrases.
- `arguments`: Only include if the skill takes parameters. Use `$name` in the body for substitution.
- `aliases`: Short alternative names for invoking the skill.

## Step 4: Confirm and Save

Show the complete SKILL.md content for review. After user approval, write the file and tell the user:
- Where the skill was saved
- How to invoke it: `/{{skill-name}} [arguments]`
- That they can edit SKILL.md directly to refine it
"""


def _build_skillify_prompt(args: str) -> str:
    prompt = _SKILLIFY_PROMPT
    if args:
        prompt += f"\n## User Description\n\nThe user described this process as: \"{args}\""
    return prompt


# ═══════════════════════════════════════════════════════════════
# Registration
# ═══════════════════════════════════════════════════════════════

def register_all_bundled_skills() -> None:
    """Register all bundled skills. Called from skills/__init__.py."""

    register_skill(SkillDefinition(
        name="simplify",
        description="Review changed code for reuse, quality, and efficiency, then fix issues found.",
        prompt_builder=_build_simplify_prompt,
        aliases=["review", "cleanup", "clean"],
        uses_sub_agent=True,
        argument_hint="[focus area]",
        when_to_use="After making code changes, to review quality before committing.",
    ))

    register_skill(SkillDefinition(
        name="remember",
        description="Review and organize persistent memories. Detect outdated, conflicting, and duplicate entries.",
        prompt_builder=_build_remember_prompt,
        aliases=["memory_review", "mem"],
        uses_sub_agent=False,
        argument_hint="[focus area]",
        when_to_use="When the user wants to review, organize, or clean up their memories.",
    ))

    # P101: /verify is now a runtime-enforced coding skill (skills/coding_skills.py).
    # This legacy version is kept as /verify_legacy for backward compatibility.
    register_skill(SkillDefinition(
        name="verify_legacy",
        description="[Legacy] Adversarial verification of recent code changes — tries to break the implementation.",
        prompt_builder=_build_verify_prompt,
        aliases=["verify_old"],
        uses_sub_agent=True,
        argument_hint="[what to verify]",
        when_to_use="",
        disable_model_invocation=True,
    ))

    register_skill(SkillDefinition(
        name="commit",
        description="Auto-generate a Conventional Commits message from staged/unstaged changes and commit.",
        prompt_builder=_build_commit_prompt,
        aliases=["gc", "git_commit"],
        uses_sub_agent=False,
        argument_hint="[extra instructions]",
        when_to_use="When ready to commit changes to git.",
    ))

    # P101: /debug is now a runtime-enforced coding skill (skills/coding_skills.py).
    # This legacy version is kept as /debug_legacy for backward compatibility.
    register_skill(SkillDefinition(
        name="debug_legacy",
        description="[Legacy] Diagnose and fix issues — reads errors, traces root cause, proposes fixes.",
        prompt_builder=_build_debug_prompt,
        aliases=["debug_old"],
        uses_sub_agent=False,
        argument_hint="[issue description]",
        when_to_use="",
        allowed_tools=["file_read", "grep_search", "find_by_name", "shell_execute"],
        disable_model_invocation=True,
    ))

    register_skill(SkillDefinition(
        name="batch",
        description="Research and plan a large-scale change, then execute it in parallel across multiple sub_agents.",
        prompt_builder=_build_batch_prompt,
        aliases=["parallel", "mass_change"],
        uses_sub_agent=True,
        argument_hint="<instruction>",
        when_to_use="When the user wants to make a sweeping change across many files (migrations, refactors, bulk renames) that can be decomposed into independent parallel units.",
        # P99e: Claw pattern — batch must be explicitly invoked
        disable_model_invocation=True,
    ))

    register_skill(SkillDefinition(
        name="skillify",
        description="Capture this session's repeatable process into a reusable SKILL.md file.",
        prompt_builder=_build_skillify_prompt,
        aliases=["capture_skill", "save_skill"],
        uses_sub_agent=False,
        argument_hint="[description of the process to capture]",
        when_to_use="When the user wants to turn a workflow they just performed into a reusable skill that can be invoked later with a slash command.",
        # P99d/P99e: Claw pattern — restrict tools, must be explicitly invoked
        allowed_tools=["file_read", "file_write", "file_edit", "grep_search",
                        "find_by_name", "shell_execute"],
        disable_model_invocation=True,
    ))
