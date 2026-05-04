"""shell_execute tool — Execute shell commands."""
import hashlib
import json as _json
import logging
import os
import re as _re
import subprocess
import threading
import time as _time_mod
from pathlib import Path
from tools.base import _subprocess_run, check_shell_safety

logger = logging.getLogger("nanobot.tools.shell_execute")

# ── Shell execution metrics ─────────────────────────────────
_SHELL_METRICS_LOCK = threading.Lock()
_SHELL_TOTAL = 0
_SHELL_ERRORS = 0
_SHELL_BLOCKED = 0
_SHELL_PASSED = 0  # R13: commands that passed all safety checks and executed
_SHELL_LATENCY_SUM_MS = 0.0
_SHELL_LATENCY_COUNT = 0
# Histogram buckets (upper bound in ms). +Inf is implicit.
_HISTOGRAM_BOUNDS = (10, 50, 100, 500, 1000, 5000)
_SHELL_LATENCY_BUCKETS = [0] * len(_HISTOGRAM_BOUNDS)  # cumulative counts per bucket


def _observe_latency(ms: float) -> None:
    """Record a latency observation into histogram buckets (thread-safe)."""
    global _SHELL_LATENCY_SUM_MS, _SHELL_LATENCY_COUNT
    with _SHELL_METRICS_LOCK:
        _SHELL_LATENCY_SUM_MS += ms
        _SHELL_LATENCY_COUNT += 1
        for i, bound in enumerate(_HISTOGRAM_BOUNDS):
            if ms <= bound:
                _SHELL_LATENCY_BUCKETS[i] += 1


def _record_execution(elapsed_ms: float, success: bool) -> None:
    """Atomically update all per-execution metrics in a single lock acquisition."""
    global _SHELL_TOTAL, _SHELL_ERRORS, _SHELL_PASSED, _SHELL_LATENCY_SUM_MS, _SHELL_LATENCY_COUNT
    with _SHELL_METRICS_LOCK:
        _SHELL_TOTAL += 1
        _SHELL_PASSED += 1
        _SHELL_LATENCY_SUM_MS += elapsed_ms
        _SHELL_LATENCY_COUNT += 1
        for i, bound in enumerate(_HISTOGRAM_BOUNDS):
            if elapsed_ms <= bound:
                _SHELL_LATENCY_BUCKETS[i] += 1
        if not success:
            _SHELL_ERRORS += 1


def get_shell_metrics() -> dict:
    """Return shell execution metrics for Prometheus exposition (thread-safe snapshot)."""
    with _SHELL_METRICS_LOCK:
        return {
            "shell_total": _SHELL_TOTAL,
            "shell_errors": _SHELL_ERRORS,
            "shell_blocked": _SHELL_BLOCKED,
            "shell_passed": _SHELL_PASSED,
            "shell_latency_ms_sum": _SHELL_LATENCY_SUM_MS,
            "shell_latency_ms_count": _SHELL_LATENCY_COUNT,
            "shell_latency_ms_buckets": list(zip(_HISTOGRAM_BOUNDS, _SHELL_LATENCY_BUCKETS)),
        }


# ── Metrics snapshot persistence ───────────────────────────
_METRICS_SNAPSHOT_PATH = Path(os.environ.get(
    "NANOBOT_METRICS_SNAPSHOT",
    str(Path.home() / ".nanobot" / ".metrics_snapshot.json"),
))
_METRICS_SNAPSHOT_INTERVAL = int(os.environ.get("NANOBOT_METRICS_SNAPSHOT_INTERVAL", "300"))


_SNAPSHOT_FAILURES = 0  # exposed for Prometheus
_PROCESS_START_TIME = _time_mod.time()  # gauge for Prometheus restart detection

# Callback-based access to auth failure metrics in server_final.
# Avoids circular import: tools/__init__ → shell_execute → server_final → agentic_loop → tools
_AUTH_METRICS_GET = None   # () -> int
_AUTH_METRICS_SET = None   # (int) -> None


def register_auth_metrics_callbacks(get_fn, set_fn):
    """Called by server_final at startup to register auth metrics accessors."""
    global _AUTH_METRICS_GET, _AUTH_METRICS_SET
    _AUTH_METRICS_GET = get_fn
    _AUTH_METRICS_SET = set_fn


def _collect_external_metrics() -> dict:
    """Collect metrics from other modules for unified snapshot (lazy import)."""
    ext = {}
    try:
        from task_store import get_lock_metrics
        ext["lock"] = get_lock_metrics()
    except Exception:
        pass
    try:
        from sandbox_executor import get_security_metrics
        ext["seccomp"] = get_security_metrics()
    except Exception:
        pass
    try:
        if _AUTH_METRICS_GET is not None:
            ext["auth_failures"] = _AUTH_METRICS_GET()
    except Exception:
        pass
    return ext


def _apply_external_metrics(ext: dict) -> None:
    """Restore external module metrics from snapshot data."""
    try:
        lock_data = ext.get("lock")
        if lock_data:
            import task_store as _ts
            _ts._LOCK_RETRIES = lock_data.get("lock_retries", 0)
            _ts._LOCK_FAILURES = lock_data.get("lock_failures", 0)
            _ts._LOCK_SUCCESSES = lock_data.get("lock_successes", 0)
    except Exception:
        pass
    try:
        sec_data = ext.get("seccomp")
        if sec_data:
            import sandbox_executor as _se
            _se._SECCOMP_RETRIES = sec_data.get("seccomp_retries", 0)
            _se._SECCOMP_PERMANENT_FAILURES = sec_data.get("seccomp_permanent_failures", 0)
            _se._SECCOMP_TRANSIENT_FAILURES = sec_data.get("seccomp_transient_failures", 0)
            _se._SECCOMP_SUCCESSES = sec_data.get("seccomp_successes", 0)
    except Exception:
        pass
    try:
        auth_val = ext.get("auth_failures")
        if auth_val is not None:
            if _AUTH_METRICS_SET is not None:
                _AUTH_METRICS_SET(auth_val)
            else:
                logger.warning("[Metrics] auth_failures=%d in snapshot but callback not registered; value lost", auth_val)
    except Exception:
        pass


def _save_metrics_snapshot() -> None:
    """Write all metrics to disk (atomic via temp+rename, chmod 0o600)."""
    global _SNAPSHOT_FAILURES
    with _SHELL_METRICS_LOCK:
        data = {
            "total": _SHELL_TOTAL, "errors": _SHELL_ERRORS, "blocked": _SHELL_BLOCKED, "passed": _SHELL_PASSED,
            "latency_sum": _SHELL_LATENCY_SUM_MS, "latency_count": _SHELL_LATENCY_COUNT,
            "buckets": _SHELL_LATENCY_BUCKETS[:],
        }
    data["external"] = _collect_external_metrics()
    data["snapshot_failures"] = _SNAPSHOT_FAILURES
    try:
        _METRICS_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _METRICS_SNAPSHOT_PATH.with_suffix(".tmp")
        tmp.write_text(_json.dumps(data), encoding="utf-8")
        os.chmod(str(tmp), 0o600)
        tmp.rename(_METRICS_SNAPSHOT_PATH)
    except Exception as _e:
        _SNAPSHOT_FAILURES += 1
        logger.warning("[Metrics] snapshot save failed (count=%d): %s", _SNAPSHOT_FAILURES, _e)


def _load_metrics_snapshot() -> None:
    """Restore all metrics from disk on startup."""
    global _SHELL_TOTAL, _SHELL_ERRORS, _SHELL_BLOCKED, _SHELL_PASSED
    global _SHELL_LATENCY_SUM_MS, _SHELL_LATENCY_COUNT
    try:
        if not _METRICS_SNAPSHOT_PATH.exists():
            return
        data = _json.loads(_METRICS_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        with _SHELL_METRICS_LOCK:
            _SHELL_TOTAL = data.get("total", 0)
            _SHELL_ERRORS = data.get("errors", 0)
            _SHELL_BLOCKED = data.get("blocked", 0)
            _SHELL_PASSED = data.get("passed", 0)
            _SHELL_LATENCY_SUM_MS = data.get("latency_sum", 0.0)
            _SHELL_LATENCY_COUNT = data.get("latency_count", 0)
            buckets = data.get("buckets", [])
            for i in range(min(len(buckets), len(_SHELL_LATENCY_BUCKETS))):
                _SHELL_LATENCY_BUCKETS[i] = buckets[i]
        _apply_external_metrics(data.get("external", {}))
        global _SNAPSHOT_FAILURES
        _SNAPSHOT_FAILURES = data.get("snapshot_failures", 0)
        logger.info("[Metrics] restored snapshot: total=%d errors=%d snap_fail=%d ext_keys=%s",
                    _SHELL_TOTAL, _SHELL_ERRORS, _SNAPSHOT_FAILURES, list(data.get("external", {}).keys()))
    except (_json.JSONDecodeError, KeyError, TypeError) as _e:
        logger.warning("[Metrics] snapshot load failed (corrupt file): %s", _e)
    except Exception as _e:
        logger.warning("[Metrics] snapshot load failed: %s", _e)


def _snapshot_loop() -> None:
    """Background loop: periodically save metrics snapshot."""
    while True:
        _time_mod.sleep(_METRICS_SNAPSHOT_INTERVAL)
        _save_metrics_snapshot()


# Restore on import; start background saver
_load_metrics_snapshot()
_snapshot_thread = threading.Thread(target=_snapshot_loop, daemon=True, name="metrics-snapshot")
_snapshot_thread.start()


# ── Sandbox integration (lazy import to avoid hard dependency) ────
_sandbox_executor = None
_SANDBOX_ENABLED = True  # set False to globally disable sandbox routing


def _get_sandbox():
    """Lazy-init the global SandboxExecutor singleton."""
    global _sandbox_executor
    if _sandbox_executor is not None:
        return _sandbox_executor
    try:
        from sandbox_executor import SandboxExecutor, SandboxConfig
        config = SandboxConfig(
            execution_timeout=120,
            network_enabled=False,
            pool_enabled=True,
            pool_reuse_strategy="session",
        )
        _sandbox_executor = SandboxExecutor(config)
        if _sandbox_executor.docker_available:
            logger.info("[ShellExecute] SandboxExecutor initialized (Docker available)")
        else:
            logger.info("[ShellExecute] SandboxExecutor initialized (Docker NOT available, fallback mode)")
        return _sandbox_executor
    except ImportError:
        logger.debug("[ShellExecute] sandbox_executor not available")
        return None


# ── Read-only fast-path: these commands skip Docker for performance ──
# Security audit: only commands that CANNOT produce write/execute effects.
# Explicitly excluded: find (-exec), locate (system enumeration), cat (reads
# arbitrary files outside sandbox), tree (may follow symlinks).
_READONLY_CMD_PREFIXES = frozenset({
    # Pure info commands — no file content, no side effects
    "echo", "pwd", "whoami", "which", "env", "printenv",
    "date", "uname", "hostname", "id", "type",
    "realpath", "basename", "dirname",
    # Stat-only commands — metadata, never file content
    "wc", "stat", "du", "df", "file",
    # Version checks
    "python --version", "python3 --version", "node --version",
    "npm --version", "pip --version", "pip3 --version",
    # NOTE: git is NOT here — handled separately via _GIT_READONLY_SUBCOMMANDS
})

# Git subcommands that are ALWAYS read-only regardless of arguments.
_GIT_ALWAYS_READONLY = frozenset({
    "log", "status", "diff", "show",
    "rev-parse", "rev-list", "shortlog", "describe",
})

# Git subcommands that are read-only ONLY with certain argument patterns.
# Audit R4: git tag -a (create), git remote add (write), git branch <name> (create)
# are write operations. Each entry defines write-mode indicators.
_GIT_CONDITIONAL_READONLY = {
    "tag": {
        # Flags that indicate tag creation/deletion/signing
        "write_flags": frozenset({
            "-a", "-d", "-s", "-u", "-f", "-m",
            "--annotate", "--delete", "--sign", "--force",
            "--message", "--create-reflog", "--cleanup",
        }),
        # Without these flags, bare positional args create lightweight tags.
        # Filter/sort flags take positional args that are NOT tag names to create.
        "readonly_proof": frozenset({
            "-l", "--list", "--sort", "--contains", "--no-contains",
            "--points-at", "--merged", "--no-merged",
        }),
    },
    "branch": {
        "write_flags": frozenset({
            "-d", "-D", "-m", "-M", "-c", "-C", "-f",
            "--delete", "--move", "--copy", "--force",
            "--set-upstream-to", "--unset-upstream", "--edit-description",
            "--track", "--no-track",
        }),
        # Without list-mode/filter flags, bare positional args create branches.
        # Filter flags take positional args that are NOT branch names to create.
        "readonly_proof": frozenset({
            "-l", "--list", "-a", "--all", "-r", "--remotes",
            "-v", "--verbose", "--contains", "--no-contains",
            "--merged", "--no-merged", "--sort", "--points-at",
        }),
    },
    "remote": {
        # Subcommands of 'git remote' that perform writes
        "write_subcmds": frozenset({
            "add", "remove", "rm", "rename", "set-url",
            "set-head", "set-branches", "prune", "update",
        }),
    },
}

# All git subcommands accepted by fast-path (union of always + conditional)
_GIT_READONLY_SUBCOMMANDS = _GIT_ALWAYS_READONLY | frozenset(_GIT_CONDITIONAL_READONLY.keys())

# Additional git flags that disqualify from fast-path even if subcommand is safe.
_GIT_DANGEROUS_FLAGS = frozenset({
    "--exec", "--output", "--work-tree", "--git-dir",
    "--upload-pack", "--receive-pack",
})

# Safe config key prefixes allowed with git -c on fast-path.
# Keys outside this set cause the command to be routed to sandbox.
# NOTE: core.pager and pager.* are EXCLUDED because they execute shell commands.
_GIT_SAFE_CONFIG_PREFIXES = (
    "color.", "log.", "diff.", "i18n.",
    "core.quotepath", "core.abbrev",
    "format.", "pretty.", "column.", "tag.sort", "branch.sort",
)

# Keys that match a safe prefix but execute external commands — always deny.
# diff.tool / diff.external / diff.guitool invoke external diff programs.
# Only keys with proven command execution risk are listed here.
_GIT_UNSAFE_CONFIG_KEYS = frozenset({
    "diff.tool", "diff.external", "diff.guitool",
})


def audit_git_config_keys() -> dict:
    """Auto-audit git config keys against current safety rules.

    Extracts all config keys from `git help -c`, cross-references with
    _GIT_SAFE_CONFIG_PREFIXES and _GIT_UNSAFE_CONFIG_KEYS to find keys
    that match a safe prefix but may execute external programs.

    Returns dict with: ok (bool), suspect_keys (list), git_version (str),
    total_keys (int). Designed for CI/cron scheduling.
    """
    _EXEC_PATTERNS = {
        "command", "cmd", "helper", "tool", "editor", "pager",
        "askpass", "sshcommand", "hookspath", "external", "guitool",
        "scriptpath", "program", "proxy", "driver",
    }
    _KNOWN_SAFE = {"color.pager", "http.proxy", "https.proxy"}
    result = {"ok": True, "suspect_keys": [], "git_version": "", "total_keys": 0}
    try:
        ver = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)
        result["git_version"] = ver.stdout.strip()
        proc = subprocess.run(["git", "help", "-c"], capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            result["ok"] = True
            result["git_version"] += " (git help -c unsupported)"
            return result
        all_keys = [l.strip() for l in proc.stdout.splitlines() if l.strip() and "." in l]
        result["total_keys"] = len(all_keys)
        for k in all_keys:
            if "<" in k or ">" in k or k in _KNOWN_SAFE:
                continue
            parts = k.lower().replace(".", " ").replace("-", " ").split()
            if any(p in _EXEC_PATTERNS for p in parts):
                if k.startswith(_GIT_SAFE_CONFIG_PREFIXES) and k not in _GIT_UNSAFE_CONFIG_KEYS:
                    result["suspect_keys"].append(k)
        result["ok"] = len(result["suspect_keys"]) == 0
    except FileNotFoundError:
        result["git_version"] = "git not installed"
    except Exception as e:
        result["git_version"] = f"error: {e}"
    return result


def _git_config_is_safe(tokens: list) -> bool:
    """Check all -c / --config values in git command against safe key prefixes.

    Two-layer check: key must match a safe prefix AND not be in the unsafe keys set.
    Returns False if any -c key fails either check.
    """
    i = 0
    while i < len(tokens):
        tok = tokens[i].lower()
        if tok in ("-c", "--config") and i + 1 < len(tokens):
            config_val = tokens[i + 1].lower()
            key = config_val.split("=", 1)[0] if "=" in config_val else config_val
            if not key.startswith(_GIT_SAFE_CONFIG_PREFIXES):
                return False
            if key in _GIT_UNSAFE_CONFIG_KEYS:
                return False
            i += 2  # skip -c and its value
        else:
            i += 1
    return True


def _git_subcmd_is_readonly(tokens: list, subcmd_idx: int = 1) -> bool:
    """Validate that a git command with a conditionally-readonly subcommand
    is actually used in read-only mode by checking argument patterns.

    subcmd_idx: index of the subcommand token (may be >1 if global options precede it).
    Returns True only if no write-mode flags or positional args are detected.
    """
    subcmd = tokens[subcmd_idx].lower()
    rules = _GIT_CONDITIONAL_READONLY.get(subcmd)
    if rules is None:
        return True  # not conditional — handled by _GIT_ALWAYS_READONLY

    remaining = [t.lower() for t in tokens[subcmd_idx + 1:]]

    # Check write subcmds (for 'git remote add ...')
    write_subcmds = rules.get("write_subcmds", frozenset())
    if remaining and remaining[0] in write_subcmds:
        return False

    # Check write flags
    write_flags = rules.get("write_flags", frozenset())
    for token in remaining:
        # Handle --flag=value
        flag_part = token.split("=", 1)[0]
        if flag_part in write_flags:
            return False

    # For subcommands that create via positional args (tag <name>, branch <name>):
    # require a readonly-proof flag when positional args are present
    readonly_proof = rules.get("readonly_proof", frozenset())
    if readonly_proof:
        has_proof = any(t.split("=", 1)[0] in readonly_proof for t in remaining)
        # Positional args = tokens not starting with '-'
        has_positional = any(not t.startswith("-") for t in remaining)
        if has_positional and not has_proof:
            return False

    return True

# Args that turn otherwise-safe commands into dangerous ones.
# If any of these appear in the command, route to sandbox regardless.
_DANGEROUS_FAST_PATH_ARGS = frozenset({
    "-exec", "-execdir", "-ok", "-okdir", "-delete",  # find abuse
    "--output", "--format",  # git log --output=file
    "-o ",  # output redirection flags
})

# Shell metacharacters that indicate the command may have side effects
# beyond the apparent first token (redirection, subshell, etc.)
_DANGEROUS_METACHAR = frozenset({">", ">>", "$(", "`"})

# Environment variables injected when git runs via fast-path on the host.
# Isolates git from system/user configs that may contain malicious aliases or hooks.
_GIT_ISOLATION_VARS = {
    "GIT_CONFIG_NOSYSTEM": "1",      # skip /etc/gitconfig
    "GIT_CONFIG_GLOBAL": "/dev/null", # skip ~/.gitconfig (Git 2.32+)
    "GIT_TERMINAL_PROMPT": "0",       # no interactive auth prompts
}


# GIT_* variables that are safe to preserve for read-only operations.
# These are required by git alternates / object sharing and do not affect config.
_GIT_SAFE_PASSTHROUGH_VARS = frozenset({
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
})


def _build_git_env() -> dict:
    """Build a sanitized env for git fast-path execution.

    Strips all GIT_* variables from os.environ EXCEPT known-safe ones needed for
    read-only object resolution, then injects isolation vars.
    """
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("GIT_") or k in _GIT_SAFE_PASSTHROUGH_VARS
    }
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    env.update(_GIT_ISOLATION_VARS)
    return env


def _is_readonly_command(cmd: str) -> bool:
    """Check if a command is read-only and safe to run without Docker sandbox.

    A command qualifies only if:
    1. Its first token is in _READONLY_CMD_PREFIXES, OR it is a whitelisted git subcommand
    2. It does NOT contain dangerous arguments or shell metacharacters

    Git is handled separately: only subcommands in _GIT_READONLY_SUBCOMMANDS pass.
    """
    cmd_stripped = cmd.strip()
    if not cmd_stripped:
        return False

    tokens = cmd_stripped.split()
    first_token = tokens[0] if tokens else ""

    # Phase 1a: git subcommand whitelist check
    if first_token == "git":
        if len(tokens) < 2:
            return False  # bare 'git' — not readonly
        # Skip global options before the subcommand (e.g. git -c key=val <subcmd>)
        _idx = 1
        while _idx < len(tokens):
            _tok_lower = tokens[_idx].lower()
            if _tok_lower in ("-c", "--config") and _idx + 1 < len(tokens):
                _idx += 2  # skip -c and its value
            elif _tok_lower.startswith("-"):
                _idx += 1  # skip other global flags
            else:
                break
        if _idx >= len(tokens):
            return False  # no subcommand found after global options
        git_subcmd = tokens[_idx].lower()
        if git_subcmd not in _GIT_READONLY_SUBCOMMANDS:
            logger.info(
                "[ShellExecute] Fast-path denied: git subcommand '%s' not in whitelist (cmd_len=%d)",
                git_subcmd, len(cmd_stripped),
            )
            return False
        # Phase 1a-ii: argument-level readonly validation for conditional subcommands
        if git_subcmd in _GIT_CONDITIONAL_READONLY:
            if not _git_subcmd_is_readonly(tokens, _idx):
                logger.info(
                    "[ShellExecute] Fast-path denied: git %s has write-mode args (cmd_len=%d, cmd='%s')",
                    git_subcmd, len(cmd_stripped), cmd_stripped[:120],
                )
                return False
        # Check git-specific dangerous flags (token-level for short flags)
        cmd_lower = cmd_stripped.lower()
        lower_tokens = cmd_lower.split()
        for flag in _GIT_DANGEROUS_FLAGS:
            if len(flag) <= 2:
                if flag in lower_tokens:
                    logger.info(
                        "[ShellExecute] Fast-path denied: git flag '%s' in '%s' (cmd_len=%d)",
                        flag, cmd_stripped[:80], len(cmd_stripped),
                    )
                    return False
            else:
                if flag in cmd_lower:
                    logger.info(
                        "[ShellExecute] Fast-path denied: git flag '%s' in '%s' (cmd_len=%d)",
                        flag, cmd_stripped[:80], len(cmd_stripped),
                    )
                    return False
        # Check -c / --config: allow safe keys, deny dangerous ones
        if "-c" in lower_tokens or "--config" in lower_tokens:
            if not _git_config_is_safe(tokens):
                logger.info(
                    "[ShellExecute] Fast-path denied: unsafe git -c config key (cmd_len=%d, cmd='%s')",
                    len(cmd_stripped), cmd_stripped[:120],
                )
                return False
        # Git subcommand passed whitelist + arg validation — fall through to Phase 2/3
    else:
        # Phase 1b: standard prefix match
        matched = False
        for prefix in _READONLY_CMD_PREFIXES:
            if ' ' in prefix:
                if cmd_stripped.startswith(prefix):
                    matched = True
                    break
            else:
                if first_token == prefix:
                    matched = True
                    break
        if not matched:
            return False

    # Phase 2: secondary argument safety scan
    cmd_lower = cmd_stripped.lower()
    for dangerous_arg in _DANGEROUS_FAST_PATH_ARGS:
        if dangerous_arg in cmd_lower:
            logger.info(
                "[ShellExecute] Fast-path denied: arg '%s' (cmd_len=%d, cmd='%s')",
                dangerous_arg, len(cmd_stripped), cmd_stripped[:80],
            )
            return False

    # Phase 3: shell metacharacter check
    for meta in _DANGEROUS_METACHAR:
        if meta in cmd_stripped:
            logger.info(
                "[ShellExecute] Fast-path denied: metachar '%s' (cmd_len=%d, cmd='%s')",
                meta, len(cmd_stripped), cmd_stripped[:80],
            )
            return False

    return True


def _wrap_sandbox_error(raw_error: str, command: str) -> str:
    """Wrap Docker/sandbox errors into actionable user-friendly messages.

    CTO audit concern: raw Docker stderr is confusing for the model.
    """
    err_lower = raw_error.lower()
    if "no such container" in err_lower or "is not running" in err_lower:
        return (f"Sandbox container unavailable. Command ran in fallback mode. "
                f"Original error: {raw_error[:200]}")
    if "oom" in err_lower or "out of memory" in err_lower:
        return f"Command exceeded sandbox memory limit (512MB). Try a less memory-intensive approach."
    if "timeout" in err_lower or "timed out" in err_lower:
        return f"Command timed out in sandbox (120s). Try a simpler command or add a timeout flag."
    if "permission denied" in err_lower:
        return (f"Permission denied inside sandbox. The sandbox runs with restricted privileges. "
                f"Error: {raw_error[:200]}")
    if "command not found" in err_lower or "not found" in err_lower:
        return f"Command not found in sandbox container. Available: standard Linux + Python 3.12. Error: {raw_error[:200]}"
    # Generic fallback
    return f"Sandbox execution failed: {raw_error[:300]}"

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "shell_execute",
        "description": (
            "Execute a shell command on the Linux system. "
            "Use for: running programs, checking system status, git operations, "
            "package management, process management, etc. "
            "The command runs in the workspace directory with a 120-second timeout."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute (bash syntax)"
                }
            },
            "required": ["command"]
        }
    }
}

ALIASES = ["bash", "run_command", "execute_command"]
IS_READONLY = False

GUIDANCE = {
    "tips": [
        "Reserve shell_execute ONLY for commands that have NO dedicated tool "
        "(e.g. git, make, npm, pip, curl, docker, systemctl).",
    ],
    "is_fallback": True,  # signals prompt assembler to generate "NEVER use shell for X" rules
}


# R9: Regex patterns for file-write redirections that should use file_edit/file_write.
# Matches: >, >>, tee <file>, dd of=<file>.  Excludes /dev/null (harmless).
# R10: Known coverage limits (by design — complete shell write detection is impossible):
#   NOT detected: cp/mv, heredoc (<<EOF >file), fd indirection (exec 3>file),
#   install(1), inline interpreters (python -c "open(...).write(...)").
#   This is a best-effort guardrail, not a security boundary.
_WRITE_REDIRECT_RE = _re.compile(
    r'(?:'
    r'(?<!\d)>>\s*(?!/dev/null)\S'       # >> to a real file (not /dev/null, not fd>>)
    r'|(?<![>\d])>(?!>|&)\s*(?!/dev/null)\S'  # single > (not >>, not >&, not fd>, not /dev/null)
    r'|\btee\s+(?!-\S)(?!/dev/null)\S'  # tee <file> (not tee -a /dev/null)
    r'|\bdd\b[^|;]*\bof=(?!/dev/null)\S'  # dd of=<file>
    r')'
)


def _detect_write_redirect(cmd: str) -> str:
    """Return the matched write-redirect pattern, or empty string if none."""
    m = _WRITE_REDIRECT_RE.search(cmd)
    if m:
        return m.group(0).strip()[:40]
    return ""


_CONFIRM_PATTERNS = [
    "docker rm", "docker rmi", "docker system prune", "docker volume prune",
    "pip uninstall", "npm uninstall", "apt remove", "apt purge", "apt-get remove",
    "rm -r", "rm -f",
]


def _execute_direct(cmd: str, workspace: Path, extra_env: dict | None = None,
                    full_env: dict | None = None) -> dict:
    """Execute command directly on host (no sandbox).

    full_env: if provided, replaces os.environ entirely (used for git isolation).
    extra_env: if provided, overlays onto os.environ (additive).
    """
    if full_env is not None:
        env = full_env
    else:
        env = {**os.environ, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        if extra_env:
            env.update(extra_env)
    result = _subprocess_run(
        cmd, shell=True, capture_output=True, text=True,
        timeout=120, cwd=str(workspace),
        env=env,
    )
    output = result.stdout[:10000] if result.stdout else ""
    if result.stderr:
        stderr_text = result.stderr[:5000]
        output = f"{output}\n[stderr]\n{stderr_text}" if output else f"[stderr]\n{stderr_text}"
    return {
        "success": result.returncode == 0,
        "output": output.strip() or "(no output)",
        "error": "" if result.returncode == 0 else f"Exit code: {result.returncode}"
    }


def _execute_in_sandbox(cmd: str, workspace: Path, session_id: str = "") -> dict:
    """Execute command inside Docker sandbox with error wrapping.

    Falls back to direct execution if sandbox is unavailable.
    CTO audit: permission flow (check_shell_safety) runs BEFORE this is called.
    """
    sandbox = _get_sandbox()
    if sandbox is None or not sandbox.docker_available:
        logger.debug("[ShellExecute] Sandbox unavailable, falling back to direct execution")
        return _execute_direct(cmd, workspace)

    try:
        container_id, is_new = sandbox.get_or_create_container(
            workspace=workspace, session_id=session_id or "default"
        )
        if container_id is None:
            logger.warning("[ShellExecute] Failed to get/create sandbox container, fallback to direct")
            return _execute_direct(cmd, workspace)

        result = sandbox.execute_command(cmd, timeout=120)

        if not result.get("success") and result.get("error"):
            raw_error = result["error"]
            # If sandbox itself failed (not the command), fallback
            if result.get("timeout"):
                return {"success": False, "output": "", "error": "Command timed out in sandbox (120s)"}
            # Wrap Docker-specific errors
            result["error"] = _wrap_sandbox_error(raw_error, cmd)

        output = result.get("output", "") or ""
        if len(output) > 10000:
            output = output[:10000] + "\n... (output truncated)"

        return {
            "success": result.get("success", False),
            "output": output.strip() or "(no output)",
            "error": result.get("error", "") or (
                "" if result.get("success") else f"Exit code: {result.get('return_code', 1)}"
            ),
        }
    except Exception as e:
        logger.error(f"[ShellExecute] Sandbox execution exception: {e}, falling back to direct")
        return _execute_direct(cmd, workspace)


def execute(args: dict, workspace: Path) -> dict:
    cmd = args.get("command", "")
    if not cmd:
        return {"success": False, "output": "", "error": "No command provided"}

    session_id = args.get("_session_id", "")
    global _SHELL_TOTAL, _SHELL_ERRORS, _SHELL_BLOCKED, _SHELL_PASSED

    # P15: Tiered shell safety check (BLOCKED → reject, WARN → execute with caution)
    # CTO audit: This runs BEFORE sandbox routing — approval flow is preserved.
    _cmd_hash = hashlib.sha256(cmd.encode()).hexdigest()[:16]
    action, safety_msg = check_shell_safety(cmd)
    if action == "block":
        with _SHELL_METRICS_LOCK:
            _SHELL_TOTAL += 1
            _SHELL_BLOCKED += 1
        logger.info(
            "[ShellExecute] BLOCKED session=%s cmd_len=%d cmd_hash=%s cmd='%s' reason='%s'",
            session_id, len(cmd), _cmd_hash, cmd[:512], safety_msg[:200],
        )
        return {"success": False, "output": "", "error": safety_msg}

    # R9: Reject shell commands with explicit file-write redirections.
    # These should use file_edit / file_write tools instead, which operate
    # atomically and leave no partial writes on crash.
    _write_redirect_hit = _detect_write_redirect(cmd)
    if _write_redirect_hit:
        with _SHELL_METRICS_LOCK:
            _SHELL_TOTAL += 1
            _SHELL_BLOCKED += 1
        _wr_msg = (
            f"Rejected: command contains file write redirection ('{_write_redirect_hit}'). "
            "Use the file_edit or file_write tool instead — they operate atomically "
            "and avoid partial writes on timeout/crash."
        )
        logger.info("[ShellExecute] WRITE_REDIRECT_BLOCKED session=%s cmd_hash=%s cmd='%s'",
                     session_id, _cmd_hash, cmd[:512])
        return {"success": False, "output": "", "error": _wr_msg}

    # P47: Confirm-level check for destructive patterns not caught by WARN
    cmd_lower = cmd.lower()
    for pattern in _CONFIRM_PATTERNS:
        if pattern in cmd_lower and action != "warn":
            action = "warn"
            safety_msg = f"⚠️ CAUTION: '{pattern}' is a destructive operation. Confirm with user before running again."
            break

    if action == "warn":
        logger.info("[ShellExecute] WARNED session=%s cmd_len=%d cmd_hash=%s cmd='%s'", session_id, len(cmd), _cmd_hash, cmd[:512])

    try:
        import time as _time
        _t0 = _time.monotonic()
        # AP-1b: Route to sandbox or direct execution
        _is_ro = _is_readonly_command(cmd)
        _use_sandbox = _SANDBOX_ENABLED and not _is_ro
        _route = "sandbox" if _use_sandbox else ("fast-path" if _is_ro else "direct")
        if _use_sandbox:
            result = _execute_in_sandbox(cmd, workspace, session_id)
        else:
            # Git fast-path: build sanitized env stripping all GIT_* vars
            if cmd.strip().startswith("git "):
                result = _execute_direct(cmd, workspace, full_env=_build_git_env())
            else:
                result = _execute_direct(cmd, workspace)
        _elapsed_ms = (_time.monotonic() - _t0) * 1000
        _record_execution(_elapsed_ms, result.get("success", False))
        logger.info(
            "[ShellExecute] route=%s session=%s cmd_len=%d cmd_hash=%s cmd='%s' success=%s elapsed_ms=%.0f",
            _route, session_id, len(cmd), _cmd_hash, cmd[:512], result.get("success"), _elapsed_ms,
        )

        # P15: Prepend caution notice for WARN-level commands
        if action == "warn" and result.get("output"):
            result["output"] = f"{safety_msg}\n\n{result['output']}"
        elif action == "warn":
            result["output"] = safety_msg

        return result
    except subprocess.TimeoutExpired:
        _elapsed_ms = (_time.monotonic() - _t0) * 1000
        _record_execution(_elapsed_ms, False)
        # R7/R8: Inject integrity warning into LLM-consumable output so the model
        # knows SIGKILL may have left partially-written files on disk.
        # Include the command itself so the LLM can infer which files may be affected.
        _cmd_preview = cmd[:300] + ("..." if len(cmd) > 300 else "")
        _timeout_warning = (
            f"[WARNING] This command was killed by timeout (SIGKILL): {_cmd_preview}\n"
            "Any file writes in progress may be incomplete or corrupted. "
            "If this command was writing to a file, use file_read to verify "
            "its content before relying on it."
        )
        return {"success": False, "output": _timeout_warning, "error": "Command timed out (120s)"}
    except (OSError, subprocess.SubprocessError) as _exc:
        _elapsed_ms = (_time.monotonic() - _t0) * 1000
        _record_execution(_elapsed_ms, False)
        logger.error("[ShellExecute] error session=%s cmd_hash=%s: %s", session_id, _cmd_hash, _exc)
        return {"success": False, "output": "", "error": f"Shell execution error: {_exc}"}
    except Exception as _exc:
        _elapsed_ms = (_time.monotonic() - _t0) * 1000
        _record_execution(_elapsed_ms, False)
        logger.error("[ShellExecute] unexpected error session=%s cmd_hash=%s: %s", session_id, _cmd_hash, _exc)
        raise
