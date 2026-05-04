"""
Shared utilities for all Nanobot tool modules.

Contains:
  - _subprocess_run:  Bypasses secure_interceptor's monkey-patch
  - _resolve_path:    Resolves workspace-relative paths to absolute
  - DANGEROUS_PATTERNS: Blocklist for shell commands
"""
import logging
import os
import subprocess
from pathlib import Path
from utils.lru_cache import LRUCache

logger = logging.getLogger("nanobot.tools")

# ── subprocess bypass (secure_interceptor) ──────────────────────
# agentic_loop's tools need unfiltered shell access; the secure_interceptor
# patches subprocess.run/Popen at import time.  We grab the originals
# before the patch and restore Popen around each call.

_original_popen = None
try:
    from secure_interceptor import _original_subprocess_run, _original_subprocess_popen
    _subprocess_run_orig = _original_subprocess_run
    _original_popen = _original_subprocess_popen
except ImportError:
    _subprocess_run_orig = subprocess.run


def _subprocess_run(*args, **kwargs):
    """Run subprocess with original (unpatched) Popen to bypass secure_interceptor.

    Timeout behaviour (audit R6 documentation):
      When ``timeout=`` is set, ``subprocess.run()`` calls ``process.kill()``
      (SIGKILL on Unix) if the child does not finish in time.  SIGKILL cannot
      be caught, blocked, or ignored — the child terminates immediately with
      **no cleanup opportunity** (no signal handlers, no ``atexit``, no file
      descriptor flushing).

      Implications for callers:
        - Commands SHOULD be stateless.  File mutations belong in dedicated
          tools (file_edit / file_write) which operate atomically.
        - If the killed process was writing to disk, partially-written files
          may remain.  SQLite WAL is crash-safe by design and will recover
          on the next connection.
        - Temporary files created by the child will NOT be cleaned up.
    """
    if _original_popen is not None:
        saved = subprocess.Popen
        subprocess.Popen = _original_popen
        try:
            return _subprocess_run_orig(*args, **kwargs)
        finally:
            subprocess.Popen = saved
    else:
        return _subprocess_run_orig(*args, **kwargs)


# ── P43: Network availability probe (lazy, cached) ─────────────
# Inspired by VSCode's network detection and Claw's conditional tool filtering.
# Probes once on first web tool call, caches for _NET_CACHE_TTL seconds.
# If offline, web_search/web_fetch return helpful local alternatives.

import socket as _socket
import time as _time

_NET_CACHE_TTL = 120  # re-probe every 2 minutes
_net_cache: dict = {"available": None, "checked_at": 0.0}


def check_network_available() -> bool:
    """Check if external network is reachable (cached for 120s).

    Uses a non-blocking TCP connect to Cloudflare DNS (1.1.1.1:80)
    with a 3-second timeout. Falls back to Google DNS (8.8.8.8:53).
    This is a pure TCP check — no HTTP request, no DNS resolution needed.
    """
    now = _time.monotonic()
    if _net_cache["available"] is not None and (now - _net_cache["checked_at"]) < _NET_CACHE_TTL:
        return _net_cache["available"]

    for host, port in [("1.1.1.1", 80), ("8.8.8.8", 53)]:
        try:
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect((host, port))
            sock.close()
            _net_cache["available"] = True
            _net_cache["checked_at"] = now
            logger.info(f"[P43] Network probe: online (connected to {host}:{port})")
            return True
        except (OSError, _socket.timeout):
            continue

    _net_cache["available"] = False
    _net_cache["checked_at"] = now
    logger.info("[P43] Network probe: OFFLINE (all endpoints unreachable)")
    return False


def network_offline_error(tool_name: str, query_or_url: str = "") -> dict:
    """Return a structured error for offline web tools with local alternatives.

    Provides actionable suggestions so the model can still complete the task
    using local tools instead of re-trying the failed web tool.
    """
    suggestions = [
        "This environment has no external network access.",
        "Local alternatives:",
        "  - Use grep_search to search local files and documentation",
        "  - Use file_read to read local docs, READMEs, or man pages",
        "  - Use find_by_name to locate local documentation files",
        "  - Use shell_execute with 'man <command>' for built-in docs",
    ]
    if tool_name == "web_search" and query_or_url:
        suggestions.append(f"  - Try: grep_search(pattern=\"{query_or_url[:60]}\", path=\".\")")
    elif tool_name == "web_fetch" and query_or_url:
        suggestions.append(f"  - If the URL points to a GitHub repo, the code may be available locally")
    return {
        "success": False,
        "output": "\n".join(suggestions),
        "error": f"Network unavailable — {tool_name} cannot reach external servers. See suggestions above.",
    }


# ── Path resolution ─────────────────────────────────────────────

def _resolve_path(path_str: str, workspace: Path) -> Path:
    """Resolve a path, making it absolute relative to workspace if needed.

    Security: rejects paths that escape allowed directories.
    Raises ValueError on blocked paths so callers return a clear error
    to the model (no silent degradation).
    """
    p = Path(path_str)
    if p.is_absolute():
        resolved = p.resolve()
        ws_resolved = workspace.resolve()
        _allowed_roots = (
            ws_resolved,
            Path.home(),
            Path("/tmp"),
        )
        if any(str(resolved).startswith(str(root)) for root in _allowed_roots):
            return resolved
        logger.warning(f"[PathSecurity] Blocked absolute path outside allowed roots: {path_str}")
        raise ValueError(
            f"Path '{path_str}' is outside the allowed directories. "
            f"Use a path within the workspace ({ws_resolved}) instead."
        )
    # Relative paths: resolve and verify they don't escape workspace via '..'
    resolved = (workspace / p).resolve()
    if not str(resolved).startswith(str(workspace.resolve())):
        logger.warning(f"[PathSecurity] Blocked path traversal attempt: {path_str}")
        raise ValueError(
            f"Path '{path_str}' escapes the workspace via '..' traversal. "
            f"Use a path within the workspace instead."
        )
    return workspace / p


# ── P11: ReadFileState tracker (Claw FileEditTool pattern) ─────
# Tracks which files have been read (with mtime at read time) so that
# file_edit can verify the file hasn't been modified since last read.
# This prevents the model from blindly editing files it hasn't seen.
#
# Mirrors Claw's FileEditTool.ts:275-287 readFileState validation.

# Memory-safe LRU: max 200 entries, path-normalized keys.
# Values are tiny (~48 bytes each: mtime float + size int), so no byte limit needed.
_read_file_state: LRUCache = LRUCache(
    max_entries=200, normalize_keys=True,
)


def track_file_read(file_path: Path) -> None:
    """Record that a file was read (called by file_read after successful read).
    Stores the file's mtime so file_edit can detect external modifications."""
    try:
        stat = file_path.stat()
        _read_file_state[str(file_path.resolve())] = {
            "mtime": stat.st_mtime,
            "size": stat.st_size,
        }
    except Exception:
        pass  # stat failure is not fatal


def validate_file_for_edit(file_path: Path) -> str:
    """Validate that a file is safe to edit.

    Returns:
        "" (empty string) if OK to proceed.
        Error message string if edit should be rejected.

    Checks (mirrors Claw FileEditTool.validateInput):
      1. File must have been read first (prevents blind edits).
      2. File mtime must match the recorded read-time mtime
         (prevents editing stale content after external modification).
    """
    key = str(file_path.resolve())
    state = _read_file_state.get(key)

    if state is None:
        return (
            f"You must read this file before editing it. "
            f"Call file_read(path=\"{file_path}\") first, then retry your edit. "
            f"This ensures you are working with the actual file content."
        )

    try:
        current_mtime = file_path.stat().st_mtime
    except Exception as e:
        return f"Cannot stat file: {e}"

    if current_mtime > state["mtime"]:
        return (
            f"File has been modified since you last read it "
            f"(read mtime={state['mtime']:.2f}, current={current_mtime:.2f}). "
            f"Call file_read to see the current content before editing."
        )

    return ""  # OK


def update_file_state_after_edit(file_path: Path) -> None:
    """Update tracked mtime after a successful edit.
    Without this, the next edit would see a mtime mismatch from our own write."""
    track_file_read(file_path)
    # P31: Invalidate session read tracker so next read returns full content
    try:
        from tools.file_read import invalidate_session_reads
        invalidate_session_reads(file_path)
    except ImportError:
        pass


# ── P15: Enhanced Shell Safety (tiered: BLOCKED + WARN) ───────
# BLOCKED = hard reject, command never executes.
# WARN    = command executes, but a caution notice is prepended to output.
# Mirrors Claw's bashSecurity.ts pattern classification.

BLOCKED_PATTERNS = [
    # Filesystem destruction
    "rm -rf /",
    "rm -rf /*",
    "rm -rf ~",
    "rm -rf $HOME",
    # Disk/partition destruction
    "mkfs.",
    "dd if=/dev/zero of=/dev/sd",
    "dd if=/dev/random of=/dev/sd",
    "> /dev/sda",
    "> /dev/nvme",
    # Permission nukes
    "chmod -R 777 /",
    "chmod -R 000 /",
    "chown -R" ,  # root-level chown -R is almost always destructive
    # Fork bomb
    ":(){ :|:& };:",
    # System file destruction
    "rm /etc/passwd",
    "rm /etc/shadow",
    "rm -rf /boot",
    "rm -rf /usr",
    "rm -rf /var",
    # Crypto/ransom
    "openssl enc",
    # Network exfiltration with shell content
    "curl -d @/etc",
    "wget --post-file=/etc",
]

WARN_PATTERNS = [
    # Git destructive operations
    ("git push --force", "Force-push can overwrite remote history."),
    ("git push -f", "Force-push can overwrite remote history."),
    ("git reset --hard", "Hard reset discards uncommitted changes."),
    ("git clean -fd", "git clean removes untracked files permanently."),
    ("git checkout -- .", "Discards all unstaged changes in working tree."),
    # Database destructive
    ("drop table", "DROP TABLE permanently deletes the table."),
    ("drop database", "DROP DATABASE permanently deletes the database."),
    ("truncate ", "TRUNCATE removes all rows from the table."),
    # Service management
    ("systemctl stop", "Stopping a service may affect running applications."),
    ("systemctl disable", "Disabling a service prevents it from starting on boot."),
    ("kill -9", "SIGKILL does not allow graceful shutdown."),
    ("killall", "killall terminates all processes matching the name."),
    ("pkill", "pkill may match more processes than intended."),
    # Recursive operations on broad paths
    ("find / -delete", "Recursive delete from root is extremely dangerous."),
    ("find / -exec rm", "Recursive rm from root is extremely dangerous."),
]

# P15+: sed/awk in-place edit patterns — these bypass file_edit safety gates
_SED_AWK_BLOCKED = [
    "sed -i",       # also matches sed -i.bak, sed -i'' (substring match)
    "sed --in-place",
    "perl -i",      # also matches perl -i.bak
    "perl -pi",
    "awk -i inplace",
]

_SED_AWK_WARN = [
    ("sed -e", "sed -e can modify piped content; prefer file_edit for tracked edits."),
    ("awk '{print", "Consider file_read for reading and file_edit for modifications."),
]

# Backward compat: old tests import DANGEROUS_PATTERNS
DANGEROUS_PATTERNS = BLOCKED_PATTERNS


def _split_command_chain(cmd: str) -> list:
    """Split a compound command into individual segments for independent checking.

    Splits on &&, ||, ;, and | (pipe).  Handles quoted strings so that
    delimiters inside single/double quotes are not treated as separators.
    Returns a list of stripped command segments (empty segments dropped).
    """
    segments: list = []
    current: list = []
    i = 0
    n = len(cmd)
    in_single = False
    in_double = False

    while i < n:
        ch = cmd[i]

        # Handle backslash escapes (outside single quotes only)
        if ch == '\\' and not in_single and i + 1 < n:
            current.append(ch)
            current.append(cmd[i + 1])
            i += 2
            continue

        # Toggle quote state
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
            i += 1
            continue

        # Skip delimiters inside quotes
        if in_single or in_double:
            current.append(ch)
            i += 1
            continue

        # Check two-char delimiters first: && and ||
        if i + 1 < n and cmd[i:i+2] in ("&&", "||"):
            seg = "".join(current).strip()
            if seg:
                segments.append(seg)
            current = []
            i += 2
            continue

        # Single-char delimiters: ; and |
        if ch in (";", "|"):
            seg = "".join(current).strip()
            if seg:
                segments.append(seg)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    # Flush remaining
    seg = "".join(current).strip()
    if seg:
        segments.append(seg)

    return segments if segments else [cmd.strip()]


def _extract_subshell_contents(cmd: str) -> list:
    """Extract contents of $(...) and `...` substitutions, supporting nesting.

    Uses bracket-depth counting for $() to handle nested $(echo $(rm -rf /)).
    Returns list of inner command strings.
    """
    contents: list = []

    # Extract $() with bracket-depth counting
    i = 0
    while i < len(cmd) - 1:
        if cmd[i] == '$' and cmd[i + 1] == '(':
            depth = 1
            start = i + 2
            j = start
            while j < len(cmd) and depth > 0:
                if cmd[j] == '(' and j > 0 and cmd[j - 1] == '$':
                    depth += 1
                elif cmd[j] == '(':
                    depth += 1
                elif cmd[j] == ')':
                    depth -= 1
                j += 1
            if depth == 0:
                contents.append(cmd[start:j - 1])
            i = j
        else:
            i += 1

    # Extract backtick `...` (non-nestable by POSIX)
    import re as _re
    for match in _re.finditer(r'`([^`]+)`', cmd):
        contents.append(match.group(1))

    return contents


def _detect_subshell_injection(cmd: str, _depth: int = 0) -> bool:
    """Detect potentially dangerous command substitution patterns.

    Recursively extracts $(...) and `...` substitutions and checks embedded
    commands against safety rules.  Handles nested $() up to depth 5.
    e.g.  echo $(rm -rf /)  or  echo $(echo $(rm -rf /))
    """
    if _depth > 5:
        return True  # excessive nesting is itself suspicious

    subshell_contents = _extract_subshell_contents(cmd)
    if not subshell_contents:
        return False
    for sub_cmd in subshell_contents:
        action, _ = _check_single_command(sub_cmd)
        if action == "block":
            return True
        # Recurse into nested subshells
        if _detect_subshell_injection(sub_cmd, _depth + 1):
            return True
    return False


def _pattern_match(pattern: str, cmd_lower: str) -> bool:
    """Check if pattern matches in cmd with boundary awareness.

    Patterns ending with '/' (like 'rm -rf /') must be followed by
    end-of-string, whitespace, '*', '&', ';', '|', or newline.
    This prevents 'rm -rf /' from matching 'rm -rf /tmp/safe_dir'.
    """
    idx = cmd_lower.find(pattern)
    while idx >= 0:
        end = idx + len(pattern)
        if end >= len(cmd_lower):
            return True  # pattern at end of string → match
        next_char = cmd_lower[end]
        # If pattern ends with '/' it should only match root '/' not '/tmp/...'
        if pattern.endswith('/'):
            if next_char in (' ', '\t', '\n', '*', '&', ';', '|', ')'):
                return True
            # Not a boundary → keep searching
        else:
            return True  # non-slash patterns use simple substring match
        idx = cmd_lower.find(pattern, end)
    return False


def _check_single_command(cmd: str) -> tuple:
    """Check a single (non-compound) command against safety rules.

    Returns:
        (action, message) where:
          action = "block"  → command must not execute
          action = "warn"   → command executes, but prepend warning to output
          action = "ok"     → no issues detected
    """
    cmd_lower = cmd.lower().strip()
    # BLOCKED patterns
    for pattern in BLOCKED_PATTERNS:
        if _pattern_match(pattern.lower(), cmd_lower):
            return ("block", f"Blocked dangerous command: {cmd[:80]}")
    # sed/awk in-place edit block (bypass file_edit tracking)
    for pattern in _SED_AWK_BLOCKED:
        if pattern in cmd_lower:
            return ("block", f"Blocked: '{pattern}' bypasses file_edit tracking. Use file_edit tool instead.")
    # find with execution capabilities — blocks -exec, -execdir, -ok, -okdir, -delete
    if cmd_lower.startswith("find ") or cmd_lower.startswith("find\t"):
        for dangerous_flag in ("-exec", "-execdir", "-ok", "-okdir", "-delete"):
            if dangerous_flag in cmd_lower:
                return ("block", f"Blocked: 'find {dangerous_flag}' can execute arbitrary commands. Use shell_execute with explicit commands instead.")
    # WARN patterns
    for pattern, message in WARN_PATTERNS:
        if pattern.lower() in cmd_lower:
            return ("warn", f"⚠️ CAUTION: {message}")
    # sed/awk warn
    for pattern, message in _SED_AWK_WARN:
        if pattern in cmd_lower:
            return ("warn", f"⚠️ CAUTION: {message}")
    return ("ok", "")


def check_shell_safety(cmd: str) -> tuple:
    """Check a shell command against safety rules.

    Handles compound commands (&&, ||, ;, |) by splitting into segments
    and checking each independently.  The most severe result wins:
    block > warn > ok.

    Also detects dangerous command substitution via $() and backticks.

    Returns:
        (action, message) where:
          action = "block"  → command must not execute
          action = "warn"   → command executes, but prepend warning to output
          action = "ok"     → no issues detected
    """
    # Phase 0: Check the FULL raw command against BLOCKED_PATTERNS before splitting.
    # This catches patterns like fork bombs :(){ :|:& };: which contain shell
    # delimiters (;, |) that would break if split first.
    cmd_lower_full = cmd.lower().strip()
    for pattern in BLOCKED_PATTERNS:
        if _pattern_match(pattern.lower(), cmd_lower_full):
            return ("block", f"Blocked dangerous command: {cmd[:80]}")

    # Phase 1: Subshell injection check
    if _detect_subshell_injection(cmd):
        return ("block", f"Blocked: dangerous command inside subshell substitution: {cmd[:80]}")

    # Phase 2: Split compound commands and check each segment
    segments = _split_command_chain(cmd)
    worst_action = "ok"
    worst_msg = ""

    for segment in segments:
        action, msg = _check_single_command(segment)
        if action == "block":
            return ("block", msg)  # Immediate reject
        if action == "warn" and worst_action == "ok":
            worst_action = "warn"
            worst_msg = msg

    return (worst_action, worst_msg)
