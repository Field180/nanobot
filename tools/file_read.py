"""file_read tool — Read file contents with metadata header."""
import datetime
import json
import logging
import re as _re_mod
from pathlib import Path
from tools.base import _resolve_path, track_file_read
from utils.lru_cache import LRUCache

logger = logging.getLogger(__name__)

# P17: Cross-turn file read cache (memory-safe LRU)
# Key: (resolved_path_str, offset, limit)  Value: {"mtime": float, "result": dict}
# Dual limit: 50 entries AND 25 MB total output size (Claw fileStateCache pattern).
_FILE_READ_CACHE_MAX = 50
_FILE_READ_CACHE_MAX_BYTES = 25 * 1024 * 1024  # 25 MB
_file_read_cache: LRUCache = LRUCache(
    max_entries=_FILE_READ_CACHE_MAX,
    max_size_bytes=_FILE_READ_CACHE_MAX_BYTES,
    size_func=lambda v: len((v.get("result") or {}).get("output", "").encode("utf-8", errors="replace")),
    normalize_keys=False,  # keys are tuples, not paths
)

# ═══════════════════════════════════════════════════════════════
# P31: FILE_UNCHANGED_STUB — Claw FileReadTool pattern
# When the model re-reads the same file with the same offset/limit
# and the file hasn't been modified since, return a short stub
# instead of the full content. This saves enormous context space
# (e.g. 300 lines of agentic_loop.py → 2 lines of stub).
# Key: (resolved_path_str, offset, limit) → {"mtime": float, "total_lines": int}
# Memory-safe LRU: max 200 entries, no byte limit (values are tiny).
# ═══════════════════════════════════════════════════════════════
_session_read_tracker: LRUCache = LRUCache(
    max_entries=200, normalize_keys=False,
)

# B6: Per-file stub counter — escalate to error after repeated re-reads
# Key: resolved_path_str → int (number of consecutive stubs returned)
_stub_counter: dict = {}
_STUB_ERROR_THRESHOLD = 2  # after this many stubs, return error not success

FILE_UNCHANGED_STUB = (
    "File unchanged since last read. The content from the earlier file_read "
    "result in this conversation is still current — refer to that instead of "
    "re-reading. Do NOT call file_read on this file again — use the content "
    "you already have."
)


def reset_session_read_tracker() -> None:
    """Clear session read tracker. Called at session start."""
    _session_read_tracker.clear()
    _stub_counter.clear()


def invalidate_session_reads(file_path: Path) -> None:
    """Remove all session read tracker entries for a file.
    Called after file_edit/file_write so the next read returns full content."""
    key_prefix = str(file_path.resolve())
    to_remove = [k for k in _session_read_tracker if k[0] == key_prefix]
    for k in to_remove:
        _session_read_tracker.delete(k)

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "file_read",
        "description": (
            "Read the contents of a file. Returns numbered lines in cat -n format. "
            "By default reads up to 300 lines from the start. "
            "CRITICAL: When the user asks to read N lines (e.g. 'read first 50 lines', "
            "'前50行', 'top 20 lines'), you MUST pass limit=N. "
            "Without limit, the default 300-line cap applies regardless of what the user asked. "
            "Example: 'read first 50 lines' → file_read(path=..., limit=50). "
            "Example: 'read lines 100-200' → file_read(path=..., offset=100, limit=100). "
            "Example: 'read entire file' or '全文' → file_read(path=..., limit=99999). "
            "If the user provides a file path, use this tool DIRECTLY — do NOT call find_by_name first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or workspace-relative file path"
                },
                "offset": {
                    "type": "integer",
                    "description": "Starting line number (1-indexed). Optional."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read. REQUIRED when user specifies a line count (e.g. 'first 50 lines' → limit=50). Without this, default 300 lines are returned."
                }
            },
            "required": ["path"]
        }
    }
}

ALIASES = ["read_file"]
IS_READONLY = True

# Dynamic prompt guidance — assembled into system prompt automatically
GUIDANCE = {
    "replaces_shell": ["cat", "head", "tail", "sed", "wc", "stat", "ls -l"],
    "shell_never": "reading files, checking file size/line count",
    "tips": [
        "Returns a metadata header with total lines, file size in bytes, and modification time. "
        "Use this instead of shell wc/stat/ls -l.",
        "CRITICAL: If the user asks to read N lines, you MUST pass limit=N. "
        "'read first 50 lines' → limit=50. 'read 200 lines' → limit=200. "
        "'前20行' → limit=20. '全文'/'full file'/'entire content'/'读到末尾' → limit=99999. "
        "Without limit, default 300 lines are returned.",
        "SELF-CHECK: If the user asked for a full file, inspect the tool header. "
        "When the output says 'showing lines X-Y of N' and Y < N, the file is only partially read — "
        "call file_read again for the remaining range before answering. "
        "Do NOT claim you have the whole file when you only saw a preview.",
        "If the user provides a filename or path, call file_read DIRECTLY. "
        "Do NOT call find_by_name or file_list first — you already have the path.",
        "When you need info about multiple files, call file_read for each in the SAME turn — "
        "the system runs read-only tools concurrently.",
    ],
}

# ═══════════════════════════════════════════════════════════════
# B15: Full-file read intent detection (shared with agentic_loop)
# ═══════════════════════════════════════════════════════════════
_FULL_FILE_LIMIT = 99999  # sentinel limit injected for full-file requests

_FULL_FILE_NEGATIVE_RE = _re_mod.compile(
    r'(?:全面分析|综合分析|深入分析|详细分析|整体分析|全量分析|全文解读|complete analysis|comprehensive analysis|full analysis|whole analysis|overall analysis)',
    _re_mod.IGNORECASE,
)

_FULL_FILE_CONTEXT_RE = _re_mod.compile(
    r'(?:[\w./\\-]+\.[\w]{1,10}|文件|档案|file|读取|read|open|show|view|print|行|line|lines|末尾|结尾|末端)',
    _re_mod.IGNORECASE,
)

_FULL_FILE_ULTRA_STRONG_RE = _re_mod.compile(
    r'全文'
    r'|从头到尾|从开头到结尾'
    r'|读[取]?到(?:底|末尾?|结尾|最后)'
    r'|读[取]?至(?:末尾|结尾|最后)'
    r'|直[到至](?:末尾|结尾|文件尾|最后)'
    r'|不[要用]截断|不[要用]省略|不[要能]遗漏'
    r'|(?:to|until|till)\s+the\s+(?:end|bottom)',
    _re_mod.IGNORECASE,
)

_FULL_FILE_CONTEXTUAL_STRONG_RE = _re_mod.compile(
    r'全部内容|完整内容|所有内容'
    r'|整个文件|整份文件'
    r'|所有行|每一行'
    r'|完整读[取]?|读[取]?完整'
    r'|读[取]?\s*全部|全部\s*读[取]+'
    r'|(?:full|entire|complete|whole)\s+(?:file|content|text|source|thing)'
    r'|all\s+(?:lines|content|of\s+(?:it|the\s+file))'
    r'|read\s+(?:everything|it\s*all|the\s+whole|all\s+of)'
    r'|(?:beginning|start)\s+to\s+(?:end|finish)'
    r'|(?:don.?t|no|without)\s+truncat',
    _re_mod.IGNORECASE,
)

_FULL_FILE_INTENT_RE = _re_mod.compile(
    rf'(?:{_FULL_FILE_ULTRA_STRONG_RE.pattern})|(?:{_FULL_FILE_CONTEXTUAL_STRONG_RE.pattern})',
    _re_mod.IGNORECASE,
)


def _is_full_file_request(text: str) -> bool:
    """Detect intent to read a whole file instead of a capped excerpt."""
    normalized = (text or "").strip()
    if not normalized:
        return False
    if _FULL_FILE_NEGATIVE_RE.search(normalized) and not _FULL_FILE_CONTEXT_RE.search(normalized):
        return False
    if _FULL_FILE_ULTRA_STRONG_RE.search(normalized):
        return True
    if _FULL_FILE_CONTEXTUAL_STRONG_RE.search(normalized):
        return bool(_FULL_FILE_CONTEXT_RE.search(normalized))
    return bool(_FULL_FILE_INTENT_RE.search(normalized))


def _fix_full_file_reads(tool_calls: list, user_message: str) -> None:
    """Override file_read limit when the user wants full file content."""
    if not tool_calls or not user_message:
        return

    full_file_hints: list = []
    for line in user_message.split("\n"):
        if _is_full_file_request(line):
            file_hint_m = _re_mod.search(r'[\w./\\-]+\.[\w]+', line)
            file_hint = file_hint_m.group(0) if file_hint_m else ""
            full_file_hints.append(file_hint)

    if not full_file_hints:
        return

    used_hints: set = set()
    for tc in tool_calls:
        func = tc.get("function", {})
        raw_name = func.get("name", "")
        if raw_name not in ("file_read", "read_file"):
            continue
        args = tc.get("_parsed_args")
        if not args:
            try:
                args = json.loads(func.get("arguments", "{}"))
                tc["_parsed_args"] = args
            except (json.JSONDecodeError, ValueError):
                continue
        if not isinstance(args, dict):
            continue

        tc_path = args.get("path", "")
        matched_idx = None
        fallback_idx = None
        for i, hint in enumerate(full_file_hints):
            if i in used_hints:
                continue
            if hint and hint in tc_path:
                matched_idx = i
                break
            if fallback_idx is None:
                fallback_idx = i

        idx = matched_idx if matched_idx is not None else fallback_idx
        if idx is not None and idx not in used_hints:
            used_hints.add(idx)
            args["limit"] = _FULL_FILE_LIMIT
            if "offset" in args:
                del args["offset"]
            func["arguments"] = json.dumps(args)
            tc["_parsed_args"] = args
            logger.info(
                f"[B15] Full-file intent → limit={_FULL_FILE_LIMIT} for file_read({tc_path})"
            )


def _maybe_append_tool_summary(file_path: Path, output: str) -> str:
    """Auto-append tool registry metadata when reading tools/__init__.py.

    This is the key mechanism for small-model intelligence: instead of requiring
    the model to read 8 individual tool files to gather ALIASES data, we inject
    the complete metadata summary directly into the file_read output.
    """
    if file_path.name == "__init__.py" and file_path.parent.name == "tools":
        try:
            from tools import tool_registry_summary
            output += "\n" + tool_registry_summary()
        except Exception:
            pass  # graceful fallback
    return output


def execute(args: dict, workspace: Path) -> dict:
    path = _resolve_path(args.get("path", ""), workspace)
    offset = args.get("offset")
    if offset is not None and offset < 1:
        return {"success": False, "output": "", "error": "Offset must be a positive integer (1-indexed)."}
    limit = args.get("limit")

    if not path.exists():
        # P53/B5: Auto-search for bare filenames or relative paths
        raw_input = args.get("path", "")
        _search_name = raw_input
        if "/" in raw_input and raw_input:
            # B5: Relative path like "tools/file_read.py" — search by basename
            # but also try common subdirectories
            _basename = Path(raw_input).name
            _search_name = _basename
        # B5+: Quick subdir scan — works for BOTH bare names and relative paths
        if not path.exists():
            _subdir_candidates = ["web_ui", "src", "app", "lib", "tests", "tools"]
            _raw_to_try = raw_input  # "test_sample.py" or "tools/file_read.py"
            for subdir in _subdir_candidates:
                candidate = workspace / subdir / _raw_to_try
                if candidate.exists() and candidate.is_file():
                    path = candidate
                    break
        # B5+: Also try immediate children directories (one level deep)
        if not path.exists() and raw_input and "/" not in raw_input:
            try:
                _iterdir_matches = []
                for child in sorted(workspace.iterdir()):
                    if child.is_dir() and not child.name.startswith("."):
                        candidate = child / raw_input
                        if candidate.exists() and candidate.is_file():
                            _iterdir_matches.append(candidate)
                if len(_iterdir_matches) == 1:
                    path = _iterdir_matches[0]
            except OSError:
                pass
        if not path.exists() and _search_name:
            try:
                import subprocess as _sp53
                find_result = _sp53.run(
                    ["find", str(workspace), "-name", _search_name, "-type", "f",
                     "-not", "-path", "*/.git/*", "-not", "-path", "*/__pycache__/*",
                     "-not", "-path", "*/.venv/*", "-not", "-path", "*/.tool_results/*"],
                    capture_output=True, text=True, timeout=5,
                )
                candidates = [l.strip() for l in find_result.stdout.strip().split("\n") if l.strip()]
                # B5: For relative paths, prefer candidates that end with the full relative path
                if "/" in raw_input and candidates:
                    exact = [c for c in candidates if c.endswith("/" + raw_input)]
                    if exact:
                        candidates = exact
                # B5+: If exactly 1 match, auto-resolve instead of returning error
                if len(candidates) == 1:
                    path = Path(candidates[0])
                elif candidates:
                    suggestion = "\n".join(f"  • {c}" for c in candidates[:5])
                    return {
                        "success": False, "output": "",
                        "error": (
                            f"File not found at: {path}\n"
                            f"Found {len(candidates)} file(s) matching '{raw_input}' in workspace:\n"
                            f"{suggestion}\n"
                            f"Re-call file_read with the full path."
                        ),
                    }
            except Exception:
                pass
        if not path.exists():
            return {"success": False, "output": "", "error": f"File not found: {path}"}
    if not path.is_file():
        return {"success": False, "output": "", "error": f"Not a file: {path}"}

    stat = path.stat()
    file_size = stat.st_size
    if file_size > 5_000_000:
        return {"success": False, "output": "", "error": f"File too large ({file_size} bytes). Use offset/limit."}

    # P31: Check if this exact read was already served in this session
    resolved_str = str(path.resolve())
    tracker_key = (resolved_str, offset, limit)
    prev_entry = _session_read_tracker.get(tracker_key)
    if prev_entry is not None and prev_entry["mtime"] == stat.st_mtime:
        # File unchanged since last read — return stub instead of full content
        track_file_read(path)
        # B6: Track consecutive stubs per file — escalate to error
        _stub_counter[resolved_str] = _stub_counter.get(resolved_str, 0) + 1
        if _stub_counter[resolved_str] >= _STUB_ERROR_THRESHOLD:
            return {
                "success": False, "output": "",
                "error": (
                    f"ERROR: You have re-read {path.name} {_stub_counter[resolved_str] + 1} times. "
                    f"The file has NOT changed. STOP calling file_read on this file. "
                    f"Use the content from your first read. Move on to your next task."
                ),
            }
        mtime_str = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        total = prev_entry["total_lines"]
        stub_header = f"[File: {path} | {total} lines | {file_size:,} bytes | modified: {mtime_str}]"
        return {"success": True, "output": stub_header + "\n" + FILE_UNCHANGED_STUB, "error": ""}

    # P17: Cross-turn cache — return cached result if file hasn't changed
    cache_key = (resolved_str, offset, limit)
    cached = _file_read_cache.get(cache_key)
    if cached and cached["mtime"] == stat.st_mtime:
        # File unchanged — reuse cached output, still track for P11
        track_file_read(path)
        # P31: Record this read so next time we return the stub
        # Extract total_lines from cached output header
        _p31_total = 0
        _co = cached["result"].get("output", "")
        _lm = _re_mod.search(r"(\d+) lines", _co)
        if _lm:
            _p31_total = int(_lm.group(1))
        _session_read_tracker[tracker_key] = {"mtime": stat.st_mtime, "total_lines": _p31_total}
        return cached["result"]

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        total_lines = len(lines)

        # B6: Fresh read — reset stub counter for this file
        _stub_counter.pop(resolved_str, None)

        # Metadata header — gives model file stats without needing shell wc
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        meta_header = f"[File: {path} | {total_lines} lines | {file_size:,} bytes | modified: {mtime}]"

        # P11: Track this read for file_edit validation
        track_file_read(path)

        if offset is not None:
            start = max(0, offset - 1)
            # B8: If offset exceeds total lines, return error instead of empty success
            if start >= total_lines:
                return {
                    "success": False, "output": "",
                    "error": (
                        f"Offset {offset} exceeds file length ({total_lines} lines). "
                        f"This file only has {total_lines} lines. Use offset ≤ {total_lines}."
                    ),
                }
            end = start + (limit or 200)
            selected = lines[start:end]
            numbered = [f"{start + i + 1:6d}\t{line}" for i, line in enumerate(selected)]
            shown_end = min(start + len(selected), total_lines)
            meta_header += f" (showing lines {start+1}-{shown_end} of {total_lines})"
            result = {"success": True, "output": meta_header + "\n" + "\n".join(numbered), "error": ""}
        elif limit is not None and limit < total_lines:
            # User explicitly requested a specific number of lines (e.g. "first 50 lines")
            cap = min(limit, total_lines)
            numbered = [f"{i + 1:6d}\t{line}" for i, line in enumerate(lines[:cap])]
            meta_header += f" (showing lines 1-{cap} of {total_lines})"
            remaining = total_lines - cap
            output = meta_header + "\n" + "\n".join(numbered)
            if remaining > 0:
                output += f"\n\n... [{remaining} lines truncated] ...\n(Use offset/limit to read more. Do NOT guess the content of lines not shown.)"
            output = _maybe_append_tool_summary(path, output)
            result = {"success": True, "output": output, "error": ""}
        elif total_lines > 300:
            numbered = [f"{i + 1:6d}\t{line}" for i, line in enumerate(lines[:300])]
            meta_header += f" (showing lines 1-300 of {total_lines})"
            remaining = total_lines - 300
            output = meta_header + "\n" + "\n".join(numbered) + f"\n\n... [{remaining} lines truncated] ...\n(Use offset/limit to read more. Do NOT guess the content of lines not shown.)"
            output = _maybe_append_tool_summary(path, output)
            result = {"success": True, "output": output, "error": ""}
        else:
            numbered = [f"{i + 1:6d}\t{line}" for i, line in enumerate(lines)]
            output = meta_header + "\n" + "\n".join(numbered)
            output = _maybe_append_tool_summary(path, output)
            result = {"success": True, "output": output, "error": ""}

        # P17: Store in cache (LRU handles eviction by count + bytes automatically)
        _file_read_cache.set(cache_key, {"mtime": stat.st_mtime, "result": result})

        # P31: Record this read so next identical request returns stub
        _session_read_tracker[tracker_key] = {"mtime": stat.st_mtime, "total_lines": total_lines}

        return result
    except Exception as e:
        return {"success": False, "output": "", "error": f"Read error: {e}"}
