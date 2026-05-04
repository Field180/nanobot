"""
P90: Memory Manager — Core CRUD for Nanobot's persistent memory system.

Implements the "Index + Topic Files" pattern from Claw's memdir.ts:
  - MEMORY.md as a concise index (≤200 lines, ≤25KB)
  - Individual topic .md files with YAML frontmatter
  - 4 memory types: user, feedback, project, reference
  - Truncation with warnings when index exceeds limits

Directory layout:
  ~/.nanobot/memory/
  ├── MEMORY.md              # Index file — one-line pointers
  ├── user_preferences.md    # Topic file
  ├── feedback_testing.md    # Topic file
  └── ...
"""
import datetime
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("nanobot.memory")

# ═══════════════════════════════════════════════════════════════
# Constants (mirrored from Claw's memdir.ts)
# ═══════════════════════════════════════════════════════════════
ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000
MEMORY_TYPES = ("user", "feedback", "project", "reference")
MAX_MEMORY_FILES = 200
FRONTMATTER_MAX_LINES = 30

# Default memory directory
_DEFAULT_MEMORY_DIR = Path.home() / ".nanobot" / "memory"

# ═══════════════════════════════════════════════════════════════
# U6: Memory Type System — auto-classification + protection policy
# ═══════════════════════════════════════════════════════════════
# Type weights for retrieval scoring (higher = more boost when matching)
MEMORY_TYPE_WEIGHTS = {
    "user": 1.5,       # permanent — user identity/preferences
    "feedback": 1.4,   # permanent — behavioral corrections
    "project": 1.0,    # overwritable — work context
    "reference": 0.8,  # lowest — external pointers
}

# Types protected from accidental deletion (require explicit confirmation)
PROTECTED_TYPES = ("user", "feedback")

# Keyword patterns for auto-classification
_TYPE_KEYWORDS = {
    "user": (
        "prefer", "preference", "role", "i am", "i'm", "my name",
        "偏好", "角色", "我是", "我喜欢", "知识水平",
        "always want", "i like", "i hate", "my style",
    ),
    "feedback": (
        "don't", "stop", "never", "always do", "from now on",
        "不要", "别", "停止", "以后", "纠正",
        "correction", "going forward", "remember to",
    ),
    "project": (
        "deadline", "sprint", "milestone", "goal", "bug", "task",
        "项目", "目标", "进度", "决策", "截止",
        "version", "release", "plan", "roadmap",
    ),
    "reference": (
        "url", "http", "api", "endpoint", "documentation",
        "链接", "文档", "地址", "external", "wiki",
    ),
}


def classify_memory_type(content: str, topic: str = "") -> str:
    """U6: Auto-classify memory type from content and topic.

    Uses keyword matching to suggest the best type.
    Returns one of MEMORY_TYPES, defaulting to 'project' for ambiguous content.
    """
    text = f"{topic} {content}".lower()
    scores = {t: 0 for t in MEMORY_TYPES}

    for mem_type, keywords in _TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                scores[mem_type] += 1

    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
    return "project"  # safe default for ambiguous content


def get_memory_dir(workspace: Optional[Path] = None) -> Path:
    """Get the memory directory path. Creates it if it doesn't exist."""
    if workspace:
        mem_dir = workspace / ".nanobot_memory"
    else:
        mem_dir = _DEFAULT_MEMORY_DIR
    mem_dir.mkdir(parents=True, exist_ok=True)
    return mem_dir


# ═══════════════════════════════════════════════════════════════
# Frontmatter parsing
# ═══════════════════════════════════════════════════════════════
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)


def parse_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    """Parse YAML-like frontmatter from a markdown file.

    Returns (frontmatter_dict, body_text).
    Supports simple key: value pairs only (no nested YAML).
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}, content

    fm_text = m.group(1)
    body = content[m.end():]

    fm = {}
    for line in fm_text.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()

    return fm, body


def build_frontmatter(name: str, description: str, mem_type: str) -> str:
    """Build YAML frontmatter block for a memory file."""
    return (
        f"---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"type: {mem_type}\n"
        f"---\n\n"
    )


# ═══════════════════════════════════════════════════════════════
# Index (MEMORY.md) operations
# ═══════════════════════════════════════════════════════════════

class EntrypointTruncation:
    """Result of truncating MEMORY.md content."""
    __slots__ = ("content", "line_count", "byte_count",
                 "was_line_truncated", "was_byte_truncated")

    def __init__(self, content: str, line_count: int, byte_count: int,
                 was_line_truncated: bool, was_byte_truncated: bool):
        self.content = content
        self.line_count = line_count
        self.byte_count = byte_count
        self.was_line_truncated = was_line_truncated
        self.was_byte_truncated = was_byte_truncated


def truncate_entrypoint_content(raw: str) -> EntrypointTruncation:
    """Truncate MEMORY.md content to line AND byte caps.

    Mirrors Claw's truncateEntrypointContent():
    - Line-truncates first (natural boundary)
    - Then byte-truncates at last newline before cap
    - Appends warning naming which cap fired
    """
    trimmed = raw.strip()
    lines = trimmed.split("\n")
    line_count = len(lines)
    byte_count = len(trimmed)

    was_line_truncated = line_count > MAX_ENTRYPOINT_LINES
    was_byte_truncated = byte_count > MAX_ENTRYPOINT_BYTES

    if not was_line_truncated and not was_byte_truncated:
        return EntrypointTruncation(
            content=trimmed,
            line_count=line_count,
            byte_count=byte_count,
            was_line_truncated=False,
            was_byte_truncated=False,
        )

    # Line-truncate first
    truncated = "\n".join(lines[:MAX_ENTRYPOINT_LINES]) if was_line_truncated else trimmed

    # Then byte-truncate at last newline
    if len(truncated) > MAX_ENTRYPOINT_BYTES:
        cut_at = truncated.rfind("\n", 0, MAX_ENTRYPOINT_BYTES)
        truncated = truncated[:cut_at] if cut_at > 0 else truncated[:MAX_ENTRYPOINT_BYTES]

    # Build reason string
    if was_byte_truncated and not was_line_truncated:
        reason = f"{byte_count} bytes (limit: {MAX_ENTRYPOINT_BYTES}) — index entries are too long"
    elif was_line_truncated and not was_byte_truncated:
        reason = f"{line_count} lines (limit: {MAX_ENTRYPOINT_LINES})"
    else:
        reason = f"{line_count} lines and {byte_count} bytes"

    warning = (
        f"\n\n> WARNING: {ENTRYPOINT_NAME} is {reason}. "
        f"Only part of it was loaded. Keep index entries to one line under ~200 chars; "
        f"move detail into topic files."
    )

    return EntrypointTruncation(
        content=truncated + warning,
        line_count=line_count,
        byte_count=byte_count,
        was_line_truncated=was_line_truncated,
        was_byte_truncated=was_byte_truncated,
    )


def read_index(memory_dir: Path) -> str:
    """Read MEMORY.md content. Returns empty string if not found."""
    index_path = memory_dir / ENTRYPOINT_NAME
    try:
        return index_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_index(memory_dir: Path, content: str) -> None:
    """Write MEMORY.md content."""
    index_path = memory_dir / ENTRYPOINT_NAME
    index_path.write_text(content, encoding="utf-8")
    logger.info(f"[Memory] Updated index: {len(content)} bytes")


def _add_index_entry(memory_dir: Path, filename: str, title: str, description: str) -> None:
    """Add or update an entry in MEMORY.md."""
    index_content = read_index(memory_dir)
    entry_line = f"- [{title}]({filename}) — {description}"

    # Check if entry for this filename already exists
    lines = index_content.split("\n") if index_content.strip() else []
    updated = False
    for i, line in enumerate(lines):
        if f"]({filename})" in line:
            lines[i] = entry_line
            updated = True
            break

    if not updated:
        lines.append(entry_line)

    write_index(memory_dir, "\n".join(lines) + "\n")


def _remove_index_entry(memory_dir: Path, filename: str) -> None:
    """Remove an entry from MEMORY.md by filename."""
    index_content = read_index(memory_dir)
    if not index_content.strip():
        return

    lines = index_content.split("\n")
    lines = [l for l in lines if f"]({filename})" not in l]
    write_index(memory_dir, "\n".join(lines) + "\n")


# ═══════════════════════════════════════════════════════════════
# Topic file operations
# ═══════════════════════════════════════════════════════════════

def _slugify(text: str) -> str:
    """Convert a title to a safe filename slug."""
    # Lowercase, replace non-alphanumeric with underscore, collapse multiple
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:60] if slug else "untitled"


def save_memory(
    memory_dir: Path,
    topic: str,
    content: str,
    mem_type: str = "user",
    description: str = "",
) -> Dict[str, str]:
    """Save a memory to a topic file and update the index.

    Args:
        memory_dir: Path to memory directory
        topic: Human-readable title for the memory
        content: Memory body content
        mem_type: One of: user, feedback, project, reference
        description: One-line description for the index (used for relevance matching)

    Returns:
        dict with success status and details
    """
    if mem_type not in MEMORY_TYPES:
        return {
            "success": False,
            "error": f"Invalid memory type '{mem_type}'. Must be one of: {', '.join(MEMORY_TYPES)}",
        }

    if not description:
        # Auto-generate from first line of content
        description = content.split("\n")[0][:150]

    filename = f"{_slugify(topic)}.md"
    filepath = memory_dir / filename

    # Check if file already exists — update it
    action = "Updated" if filepath.exists() else "Created"

    # Build file content with frontmatter
    file_content = build_frontmatter(topic, description, mem_type) + content

    filepath.write_text(file_content, encoding="utf-8")
    _add_index_entry(memory_dir, filename, topic, description)

    logger.info(f"[Memory] {action} {mem_type} memory: {filename} ({len(content)} chars)")
    return {
        "success": True,
        "action": action.lower(),
        "filename": filename,
        "type": mem_type,
        "message": f"{action} memory '{topic}' ({mem_type}) → {filename}",
    }


def recall_memory(memory_dir: Path, topic: str) -> Dict[str, str]:
    """Recall a specific memory by topic name or filename.

    Returns the full content of the topic file.
    """
    # Try exact filename first
    if topic.endswith(".md"):
        filepath = memory_dir / topic
    else:
        filepath = memory_dir / f"{_slugify(topic)}.md"

    if not filepath.exists():
        # Fuzzy search — find files containing the topic keyword
        matches = _search_topic_files(memory_dir, topic, max_results=1)
        if matches:
            filepath = matches[0]["path"]
        else:
            return {
                "success": False,
                "error": f"No memory found matching '{topic}'",
                "suggestion": "Use list_memories() to see available memories",
            }

    try:
        content = filepath.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(content)
        return {
            "success": True,
            "filename": filepath.name,
            "type": fm.get("type", "unknown"),
            "name": fm.get("name", filepath.stem),
            "description": fm.get("description", ""),
            "content": body.strip(),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def forget_memory(memory_dir: Path, topic: str, force: bool = False) -> Dict[str, str]:
    """Delete a memory by topic name or filename.

    Removes both the topic file and its index entry.

    U6: Protected types (user, feedback) return a warning instead of deleting
    unless force=True. This prevents accidental loss of permanent preferences.
    """
    if topic.endswith(".md"):
        filename = topic
    else:
        filename = f"{_slugify(topic)}.md"

    filepath = memory_dir / filename

    if not filepath.exists():
        # Fuzzy search
        matches = _search_topic_files(memory_dir, topic, max_results=1)
        if matches:
            filepath = Path(matches[0]["path"])
            filename = filepath.name
        else:
            return {"success": False, "error": f"No memory found matching '{topic}'"}

    # U6: Check protection policy
    if not force:
        try:
            content = filepath.read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(content)
            mem_type = fm.get("type", "")
            if mem_type in PROTECTED_TYPES:
                return {
                    "success": False,
                    "error": (
                        f"Memory '{topic}' is type '{mem_type}' (protected). "
                        f"User/feedback memories contain permanent preferences and corrections. "
                        f"Ask the user to confirm deletion before proceeding."
                    ),
                    "protected": True,
                    "type": mem_type,
                }
        except Exception:
            pass  # If we can't read it, allow deletion

    filepath.unlink()
    _remove_index_entry(memory_dir, filename)
    logger.info(f"[Memory] Deleted memory: {filename}")
    return {
        "success": True,
        "message": f"Deleted memory '{topic}' ({filename})",
    }


def list_memories(memory_dir: Path) -> Dict[str, object]:
    """List all memories with their metadata.

    Returns a structured list sorted newest-first.
    """
    if not memory_dir.exists():
        return {"success": True, "memories": [], "count": 0}

    memories = []
    for filepath in sorted(memory_dir.glob("*.md")):
        if filepath.name == ENTRYPOINT_NAME:
            continue

        try:
            content = filepath.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(content)
            stat = filepath.stat()
            memories.append({
                "filename": filepath.name,
                "path": str(filepath),
                "name": fm.get("name", filepath.stem),
                "type": fm.get("type", "unknown"),
                "description": fm.get("description", ""),
                "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "mtime_ms": stat.st_mtime * 1000,
                "size": stat.st_size,
            })
        except Exception:
            continue

    # Sort newest first
    memories.sort(key=lambda m: m.get("mtime_ms", 0), reverse=True)

    return {
        "success": True,
        "memories": memories[:MAX_MEMORY_FILES],
        "count": len(memories),
    }


def update_memory(
    memory_dir: Path,
    topic: str,
    content: str,
    description: str = "",
) -> Dict[str, str]:
    """Update an existing memory's content (preserving type).

    If the memory doesn't exist, returns an error suggesting save_memory.
    """
    if topic.endswith(".md"):
        filename = topic
    else:
        filename = f"{_slugify(topic)}.md"

    filepath = memory_dir / filename

    if not filepath.exists():
        # Fuzzy search
        matches = _search_topic_files(memory_dir, topic, max_results=1)
        if matches:
            filepath = Path(matches[0]["path"])
            filename = filepath.name
        else:
            return {
                "success": False,
                "error": f"No memory found matching '{topic}'. Use save_memory to create a new one.",
            }

    # Read existing to preserve type
    old_content = filepath.read_text(encoding="utf-8")
    fm, _ = parse_frontmatter(old_content)
    mem_type = fm.get("type", "user")
    name = fm.get("name", topic)

    if not description:
        description = fm.get("description", content.split("\n")[0][:150])

    file_content = build_frontmatter(name, description, mem_type) + content
    filepath.write_text(file_content, encoding="utf-8")
    _add_index_entry(memory_dir, filename, name, description)

    logger.info(f"[Memory] Updated memory: {filename}")
    return {
        "success": True,
        "action": "updated",
        "filename": filename,
        "message": f"Updated memory '{name}' ({filename})",
    }


# ═══════════════════════════════════════════════════════════════
# Search / relevance
# ═══════════════════════════════════════════════════════════════

def _search_topic_files(
    memory_dir: Path,
    query: str,
    max_results: int = 5,
) -> List[Dict[str, object]]:
    """Search topic files by keyword matching on filename, name, and description.

    Simple keyword-based search (no LLM needed — optimized for local models).
    """
    if not memory_dir.exists():
        return []

    query_lower = query.lower()
    keywords = query_lower.split()

    scored = []
    for filepath in memory_dir.glob("*.md"):
        if filepath.name == ENTRYPOINT_NAME:
            continue

        try:
            # Read frontmatter only (first 30 lines)
            with filepath.open("r", encoding="utf-8") as f:
                head = "".join(f.readline() for _ in range(FRONTMATTER_MAX_LINES))

            fm, _ = parse_frontmatter(head)
            name = fm.get("name", filepath.stem).lower()
            desc = fm.get("description", "").lower()
            fname = filepath.stem.lower()

            # Score: exact match > partial match > keyword match
            score = 0
            searchable = f"{name} {desc} {fname}"

            if query_lower in searchable:
                score += 10  # Exact substring match
            for kw in keywords:
                if kw in searchable:
                    score += 3

            if score > 0:
                # U6: Apply type-based weight boost
                mem_type = fm.get("type", "unknown")
                type_weight = MEMORY_TYPE_WEIGHTS.get(mem_type, 1.0)
                weighted_score = score * type_weight
                stat = filepath.stat()
                scored.append({
                    "path": filepath,
                    "filename": filepath.name,
                    "name": fm.get("name", filepath.stem),
                    "type": mem_type,
                    "description": fm.get("description", ""),
                    "score": weighted_score,
                    "mtime_ms": stat.st_mtime * 1000,
                })
        except Exception:
            continue

    scored.sort(key=lambda x: (-x["score"], -x.get("mtime_ms", 0)))
    return scored[:max_results]


def find_relevant_memories(
    memory_dir: Path,
    query: str,
    max_results: int = 5,
    already_surfaced: Optional[set] = None,
) -> List[Dict[str, str]]:
    """Find memories relevant to a query for context injection.

    Mirrors Claw's findRelevantMemories but uses keyword matching
    instead of an LLM side-query (optimized for local/small models).

    Returns list of {filename, path, name, type, description, content}.
    """
    if already_surfaced is None:
        already_surfaced = set()

    matches = _search_topic_files(memory_dir, query, max_results=max_results + len(already_surfaced))

    results = []
    for m in matches:
        if str(m["path"]) in already_surfaced:
            continue

        try:
            content = Path(m["path"]).read_text(encoding="utf-8")
            _, body = parse_frontmatter(content)
            results.append({
                "filename": m["filename"],
                "path": str(m["path"]),
                "name": m["name"],
                "type": m["type"],
                "description": m["description"],
                "content": body.strip(),
            })
        except Exception:
            continue

        if len(results) >= max_results:
            break

    return results


def format_memory_manifest(memories: List[Dict]) -> str:
    """Format memory headers as a text manifest.

    Mirrors Claw's formatMemoryManifest():
    One line per file: [type] filename (timestamp): description
    """
    lines = []
    for m in memories:
        tag = f"[{m.get('type', '')}] " if m.get("type") else ""
        ts = m.get("modified", "")
        desc = m.get("description", "")
        line = f"- {tag}{m.get('filename', '')} ({ts})"
        if desc:
            line += f": {desc}"
        lines.append(line)
    return "\n".join(lines)
