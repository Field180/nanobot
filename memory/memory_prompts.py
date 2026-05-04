"""
P91: Memory Prompt Builder — System prompt injection for Nanobot's memory system.

Mirrors Claw's buildMemoryPrompt() and buildMemoryLines():
  - Builds behavioral instructions (4 memory types, when to save/recall)
  - Injects truncated MEMORY.md content into system prompt
  - Provides DIR_EXISTS_GUIDANCE to prevent wasted mkdir/ls turns
"""
import logging
from pathlib import Path
from typing import Optional

from memory.memory_manager import (
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_LINES,
    MAX_ENTRYPOINT_BYTES,
    MEMORY_TYPES,
    get_memory_dir,
    read_index,
    truncate_entrypoint_content,
)

logger = logging.getLogger("nanobot.memory")

# ═══════════════════════════════════════════════════════════════
# Guidance constants (from Claw's memdir.ts)
# ═══════════════════════════════════════════════════════════════
DIR_EXISTS_GUIDANCE = (
    "This directory already exists — write to it directly with save_memory "
    "(do not run mkdir or check for its existence)."
)

# ═══════════════════════════════════════════════════════════════
# Frontmatter example (from Claw's memoryTypes.ts)
# ═══════════════════════════════════════════════════════════════
MEMORY_FRONTMATTER_EXAMPLE = (
    "```markdown\n"
    "---\n"
    "name: {{memory name}}\n"
    "description: {{one-line description — used to decide relevance in future conversations, so be specific}}\n"
    f"type: {{{{{', '.join(MEMORY_TYPES)}}}}}\n"
    "---\n"
    "\n"
    "{{memory content — for feedback/project types, structure as: rule/fact, then **Why:** and **How to apply:** lines}}\n"
    "```"
)

# ═══════════════════════════════════════════════════════════════
# Memory type taxonomy (from Claw's memoryTypes.ts INDIVIDUAL mode)
# ═══════════════════════════════════════════════════════════════
TYPES_SECTION = """## Types of memory

There are several discrete types of memory that you can store:

**user** — Information about the user's role, goals, responsibilities, and knowledge. Great user memories help you tailor your future behavior to the user's preferences and perspective.
- *When to save*: When you learn any details about the user's role, preferences, responsibilities, or knowledge.
- *How to use*: When your work should be informed by the user's profile or perspective.

**feedback** — Guidance the user has given you about how to approach work — both what to avoid and what to keep doing. Record from failure AND success.
- *When to save*: Any time the user corrects your approach ("don't", "stop doing X") OR confirms a non-obvious approach worked ("yes exactly", "perfect"). Include *why* so you can judge edge cases later.
- *How to use*: Let these memories guide your behavior so the user does not need to offer the same guidance twice.
- *Structure*: Lead with the rule, then a **Why:** line and a **How to apply:** line.

**project** — Information about ongoing work, goals, bugs, or incidents NOT derivable from the code or git history.
- *When to save*: When you learn who is doing what, why, or by when. Convert relative dates to absolute dates (e.g., "Thursday" → "2026-03-05").
- *How to use*: Understand the broader context and motivation behind the user's request.
- *Structure*: Lead with the fact/decision, then **Why:** and **How to apply:** lines.

**reference** — Pointers to where information can be found in external systems.
- *When to save*: When you learn about resources in external systems and their purpose.
- *How to use*: When the user references an external system or information that may be in an external system."""

# ═══════════════════════════════════════════════════════════════
# What NOT to save (from Claw's memoryTypes.ts)
# ═══════════════════════════════════════════════════════════════
WHAT_NOT_TO_SAVE = """## What NOT to save in memory

- Code patterns, conventions, architecture, file paths, or project structure — these can be derived by reading the current project state.
- Git history, recent changes, or who-changed-what — `git log` / `git blame` are authoritative.
- Debugging solutions or fix recipes — the fix is in the code; the commit message has the context.
- Anything already documented in NANOBOT.md files.
- Ephemeral task details: in-progress work, temporary state, current conversation context.

These exclusions apply even when the user explicitly asks you to save. If they ask you to save a PR list or activity summary, ask what was *surprising* or *non-obvious* about it — that is the part worth keeping."""

# ═══════════════════════════════════════════════════════════════
# When to access / trust memories (from Claw's memoryTypes.ts)
# ═══════════════════════════════════════════════════════════════
WHEN_TO_ACCESS = """## When to access memories
- When memories seem relevant, or the user references prior-conversation work.
- You MUST access memory when the user explicitly asks you to check, recall, or remember.
- If the user says to *ignore* or *not use* memory: proceed as if MEMORY.md were empty.
- Memory records can become stale over time. Before answering based solely on memory, verify that the memory is still correct by reading the current state of files or resources. If a recalled memory conflicts with current information, trust what you observe now — and update or remove the stale memory."""

TRUSTING_RECALL = """## Before recommending from memory

A memory that names a specific function, file, or flag is a claim that it existed *when the memory was written*. It may have been renamed, removed, or never merged. Before recommending it:

- If the memory names a file path: check the file exists.
- If the memory names a function or flag: grep for it.
- If the user is about to act on your recommendation, verify first.

"The memory says X exists" is not the same as "X exists now."

A memory that summarizes repo state is frozen in time. If the user asks about *recent* or *current* state, prefer `git log` or reading the code over recalling the snapshot."""

# ═══════════════════════════════════════════════════════════════
# How to save (from Claw's memdir.ts buildMemoryLines)
# ═══════════════════════════════════════════════════════════════
HOW_TO_SAVE = f"""## How to save memories

Saving a memory is a two-step process:

**Step 1** — Use the `memory` tool with action="save", providing topic, type, content, and description.

**Step 2** — The tool automatically updates `{ENTRYPOINT_NAME}` with a pointer to the file. Each entry should be one line, under ~150 characters.

- `{ENTRYPOINT_NAME}` is always loaded into your conversation context — lines after {MAX_ENTRYPOINT_LINES} will be truncated, so keep the index concise
- Organize memory semantically by topic, not chronologically
- Update or remove memories that turn out to be wrong or outdated
- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one."""


def build_memory_prompt(memory_dir: Optional[Path] = None) -> str:
    """Build the complete memory system prompt section.

    This is injected into the system prompt at session start.
    Mirrors Claw's buildMemoryPrompt().

    Returns empty string if memory is not enabled.
    """
    if memory_dir is None:
        memory_dir = get_memory_dir()

    mem_dir_str = str(memory_dir)

    lines = [
        "# Persistent Memory",
        "",
        f"You have a persistent, file-based memory system at `{mem_dir_str}`. {DIR_EXISTS_GUIDANCE}",
        "",
        "You should build up this memory system over time so that future conversations have a "
        "complete picture of who the user is, how they'd like to collaborate with you, what "
        "behaviors to avoid or repeat, and the context behind the work the user gives you.",
        "",
        "If the user explicitly asks you to remember something, save it immediately as whichever "
        "type fits best. If they ask you to forget something, find and remove the relevant entry.",
        "",
        TYPES_SECTION,
        "",
        WHAT_NOT_TO_SAVE,
        "",
        HOW_TO_SAVE,
        "",
        WHEN_TO_ACCESS,
        "",
        TRUSTING_RECALL,
        "",
        "## Memory and other forms of persistence",
        "Memory is one of several persistence mechanisms. The distinction is that memory can be "
        "recalled in future conversations and should not be used for persisting information only "
        "useful within the current conversation.",
        "- When to use a plan instead of memory: For implementation approaches, use Plans or Tasks. "
        "Memory should be reserved for information useful in future conversations.",
        "- When to use tasks instead of memory: For discrete steps or tracking progress in the "
        "current conversation, use todo_manage instead of saving to memory.",
        "",
    ]

    # Read and inject MEMORY.md content
    index_content = read_index(memory_dir)
    if index_content.strip():
        t = truncate_entrypoint_content(index_content)
        if t.was_line_truncated or t.was_byte_truncated:
            logger.warning(
                f"[Memory] MEMORY.md truncated: {t.line_count} lines, "
                f"{t.byte_count} bytes (line_trunc={t.was_line_truncated}, "
                f"byte_trunc={t.was_byte_truncated})"
            )
        lines.extend([f"## {ENTRYPOINT_NAME}", "", t.content])
    else:
        lines.extend([
            f"## {ENTRYPOINT_NAME}",
            "",
            f"Your {ENTRYPOINT_NAME} is currently empty. When you save new memories, they will appear here.",
        ])

    prompt = "\n".join(lines)
    logger.info(f"[Memory] Built memory prompt: {len(prompt)} chars, index={'present' if index_content.strip() else 'empty'}")
    return prompt


def build_memory_context_for_query(
    memory_dir: Path,
    query: str,
    max_memories: int = 5,
) -> str:
    """Build a context block of relevant memories for a specific query.

    Called at the start of each conversation to inject relevant memories.
    Returns a formatted string to add to the dynamic context, or empty string.
    """
    from memory.memory_manager import find_relevant_memories

    memories = find_relevant_memories(memory_dir, query, max_results=max_memories)

    if not memories:
        return ""

    parts = [f"[RECALLED MEMORIES — {len(memories)} relevant to your query]"]
    for m in memories:
        parts.append(
            f"\n### [{m['type']}] {m['name']}\n"
            f"*{m['description']}*\n\n"
            f"{m['content']}"
        )
    parts.append("\n[END RECALLED MEMORIES]")

    result = "\n".join(parts)
    logger.info(f"[Memory] Recalled {len(memories)} memories for query ({len(result)} chars)")
    return result
