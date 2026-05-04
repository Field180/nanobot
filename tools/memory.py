"""
P90: memory tool — Persistent memory management for Nanobot.

Allows the AI to save, recall, update, forget, and list memories
that persist across conversations. Implements the "Index + Topic Files"
pattern from Claw's memdir system.

Memory directory: ~/.nanobot/memory/
  - MEMORY.md: Index file (≤200 lines, ≤25KB)
  - *.md: Topic files with YAML frontmatter (type, description)
"""
import logging
from pathlib import Path

logger = logging.getLogger("nanobot.tools.memory")

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "memory",
        "description": (
            "Manage persistent memories that survive across conversations. "
            "Use to save user preferences, feedback, project context, and references. "
            "Actions: save (create/overwrite), recall (read), update (modify existing), "
            "forget (delete), list (show all), search (find by keyword). "
            "Memories are stored as Markdown files with YAML frontmatter. "
            "4 types: user (role/preferences), feedback (guidance/corrections), "
            "project (ongoing work/goals), reference (external system pointers)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "The memory operation to perform. "
                        "save: Create or overwrite a memory. "
                        "recall: Read a specific memory by topic. "
                        "update: Modify an existing memory's content. "
                        "forget: Delete a memory. "
                        "list: Show all stored memories. "
                        "search: Find memories by keyword."
                    ),
                    "enum": ["save", "recall", "update", "forget", "list", "search"],
                },
                "topic": {
                    "type": "string",
                    "description": (
                        "The topic/title of the memory. Used as the filename (slugified). "
                        "Required for: save, recall, update, forget. "
                        "For search: the search query."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "The memory content to save or update. "
                        "For feedback/project types, structure as: rule/fact, then **Why:** and **How to apply:** lines. "
                        "Required for: save, update."
                    ),
                },
                "type": {
                    "type": "string",
                    "description": (
                        "Memory type. user: role/preferences/knowledge. "
                        "feedback: corrections/confirmations from the user. "
                        "project: ongoing work/goals/deadlines. "
                        "reference: pointers to external systems."
                    ),
                    "enum": ["user", "feedback", "project", "reference"],
                },
                "description": {
                    "type": "string",
                    "description": (
                        "One-line description for the memory index. "
                        "Used for relevance matching in future conversations. Be specific. "
                        "Optional — auto-generated from content if omitted."
                    ),
                },
            },
            "required": ["action"],
        },
    },
}

# Aliases
ALIASES = ["save_memory", "recall_memory", "remember"]
READONLY = False

# Guidance for the model
GUIDANCE = {
    "when_to_use": (
        "When the user says 'remember this', 'save this preference', or corrects your behavior. "
        "Also use when you learn something about the user, project, or workflow that would be "
        "useful in future conversations."
    ),
    "tips": [
        "Always check existing memories before saving to avoid duplicates",
        "Use 'feedback' type when the user corrects you — include WHY",
        "Keep descriptions specific — they're used for relevance matching",
        "Convert relative dates to absolute dates in project memories",
    ],
}


def execute(args: dict, workspace: Path) -> dict:
    """Execute the memory tool."""
    from memory.memory_manager import (
        get_memory_dir,
        save_memory,
        recall_memory,
        forget_memory,
        list_memories,
        update_memory,
        find_relevant_memories,
        format_memory_manifest,
    )

    action = args.get("action", "")
    topic = args.get("topic", "")
    content = args.get("content", "")
    mem_type = args.get("type", "user")
    description = args.get("description", "")

    memory_dir = get_memory_dir(workspace)

    if action == "save":
        if not topic:
            return {"success": False, "error": "Missing 'topic' for save action"}
        if not content:
            return {"success": False, "error": "Missing 'content' for save action"}
        result = save_memory(memory_dir, topic, content, mem_type, description)
        if result.get("success"):
            return {
                "success": True,
                "output": result["message"],
            }
        return {"success": False, "error": result.get("error", "Save failed")}

    elif action == "recall":
        if not topic:
            return {"success": False, "error": "Missing 'topic' for recall action"}
        result = recall_memory(memory_dir, topic)
        if result.get("success"):
            header = f"[{result['type']}] {result['name']}"
            if result.get("description"):
                header += f" — {result['description']}"
            return {
                "success": True,
                "output": f"{header}\n\n{result['content']}",
            }
        return {"success": False, "error": result.get("error", "Recall failed")}

    elif action == "update":
        if not topic:
            return {"success": False, "error": "Missing 'topic' for update action"}
        if not content:
            return {"success": False, "error": "Missing 'content' for update action"}
        result = update_memory(memory_dir, topic, content, description)
        if result.get("success"):
            return {
                "success": True,
                "output": result["message"],
            }
        return {"success": False, "error": result.get("error", "Update failed")}

    elif action == "forget":
        if not topic:
            return {"success": False, "error": "Missing 'topic' for forget action"}
        result = forget_memory(memory_dir, topic)
        if result.get("success"):
            return {
                "success": True,
                "output": result["message"],
            }
        return {"success": False, "error": result.get("error", "Forget failed")}

    elif action == "list":
        result = list_memories(memory_dir)
        if not result.get("memories"):
            return {
                "success": True,
                "output": "No memories saved yet. Use action='save' to create your first memory.",
            }
        manifest = format_memory_manifest(result["memories"])
        return {
            "success": True,
            "output": f"Found {result['count']} memories:\n\n{manifest}",
        }

    elif action == "search":
        if not topic:
            return {"success": False, "error": "Missing 'topic' (search query) for search action"}
        results = find_relevant_memories(memory_dir, topic, max_results=5)
        if not results:
            return {
                "success": True,
                "output": f"No memories found matching '{topic}'",
            }
        parts = [f"Found {len(results)} memories matching '{topic}':"]
        for m in results:
            parts.append(f"\n### [{m['type']}] {m['name']}")
            if m.get("description"):
                parts.append(f"*{m['description']}*")
            parts.append(f"\n{m['content'][:500]}")
            if len(m.get("content", "")) > 500:
                parts.append("... (truncated, use recall to see full content)")
        return {
            "success": True,
            "output": "\n".join(parts),
        }

    else:
        return {
            "success": False,
            "error": f"Unknown action '{action}'. Must be one of: save, recall, update, forget, list, search",
        }
