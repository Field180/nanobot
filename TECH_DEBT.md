# Technical Debt Tracker

Tracks deferred items from audit rounds. Items are classified by root cause:

- **Architecture Defects** — design-time decisions that require redesign (not refactoring)
- **Evolution Debt** — accumulated through iteration; needs dedicated refactoring windows
- **Immediate Fixes** — single-file/function changes that can be done in any sprint

## Architecture Defects

> These are **design-level** issues from project inception. Resolution requires design review + multi-week execution with full regression testing. They are not "tech debt accumulated over time."

| ID | Description | Severity | Audit Round | Status |
|----|-------------|----------|-------------|--------|
| AD-1 | `agentic_chat_stream` 6000+ line monolith — split into composable modules | High | R1 | Deferred: requires design doc |
| AD-2 | Tool execution permission checks scattered across 3 locations — consolidate | Medium | R2 | Deferred: needs threat model review |

## Evolution Debt

> Accumulated through iteration. Addressed via periodic refactoring windows.

| ID | Description | Severity | Audit Round | Status |
|----|-------------|----------|-------------|--------|
| ED-1 | SQLite connection pooling — currently open/close per query | Low | R4 | Deferred: query frequency is low (1/turn) |
| ~~ED-2~~ | WAL checkpoint strategy — no explicit `wal_checkpoint(PASSIVE)` scheduling | Low | R4→R10 | **Resolved**: `_open_db` runs time-driven WAL checkpoint + vacuum (max once/30s), with failure logging and `/health/detailed` observability |
| ED-3 | `auto_vacuum=INCREMENTAL` ineffective on pre-existing databases | Low | R4 | Accepted: best-effort on new DBs, runtime verification added R6 |
| ED-4 | Metrics/tracing system — no Prometheus/OpenTelemetry integration | Medium | R4 | Deferred: requires infra beyond code |
| ED-5 | Structured logging — WARNING logs lack structured fields | Low | R5 | Deferred: functional for current scale |
| ED-6 | Code coverage reporting — no `coverage.py` integration | Medium | R4-R5 | Deferred: 1735+ tests pass but % unknown |
| ED-7 | write-redirect bypass via inline interpreters (`python -c`, `perl -e`) — regex cannot detect | Low | R11 | Deferred: evaluate OS-level restrictions (seccomp/namespace) in future audit. Current regex is documented as best-effort guardrail, not security boundary |

## Resolved Items

| ID | Description | Resolved In | Resolution | Verification |
|----|-------------|-------------|------------|-------------|
| ~~TD-A~~ | `sys.exit()` in test files breaking unittest discover | R3 | Guarded with `if __name__ == "__main__"` | unittest discover succeeds |
| ~~TD-B~~ | `_SESSION_STORE_DIR` uninitialized crash | R4 | `_ensure_initialized()` fallback | Behavioral: test without init() succeeds |
| ~~TD-C~~ | `_legacy_infrastructure.py` 37K dead code | R4 | Deleted | File absent from tree |
| ~~TD-D~~ | 24 unused feature flags | R4 | Removed from registry | R6: 4 remaining flags have behavioral negative-path tests |
| ~~TD-E~~ | SQLite missing WAL + busy_timeout | R4-R6 | `_open_db()` context manager with PRAGMA + runtime verification | R6: behavioral test reads back PRAGMA values, verifies conn closure |
| ~~TD-F~~ | SQLite connection leak on exception | R5→R6 | `with _open_db() as conn:` context manager (structural guarantee) | R6: behavioral test confirms conn closed after `with` and on exception |
| ~~TD-G~~ | LLM API timeout not explicit on `create()` | R5 | `create(**kwargs, timeout=timeout)` | Source: single callsite verified *(pre-R8 standard)* |
| ~~TD-H~~ | Unknown feature flag config silently ignored | R4-R5 | WARNING log for unregistered flags | Source: _load_config_file logs warning *(pre-R8 standard)* |
| ~~TD-I~~ | Timeout events lack session/turn correlation | R5 | Added `session_id`, `turn`, `timeout_type` fields | Source: both yield sites include fields *(pre-R8 standard)* |
| ~~TD-J~~ | Feature Flag consumption not behaviorally verified | R6 | 7 negative-path tests in TestFlagNegativePaths | Behavioral: flag=false → CompactService returns None, retry gate closed |
| ~~TD-K~~ | `_subprocess_run` SIGKILL behavior undocumented | R6→R8 | Docstring + runtime warning with command context in shell_execute timeout output | Behavioral: monkey-patch timeout → assert output contains SIGKILL + command string + file_read hint |
| ~~TD-L~~ | PRAGMA degradation invisible to monitoring | R7→R8 | `/health/detailed` exposes PRAGMA state via read-only URI (`file:path?mode=ro`) | AST-structural: function constants contain journal_mode, busy_timeout, mode=ro |
| ~~TD-M~~ | `@contextmanager` nested exception propagation untested | R7 | Behavioral test: conn.close() double-call + original ValueError propagated | Behavioral: test_audit_fixes r7_original_exception_propagated |
| ~~TD-N~~ | `/health/detailed` SQLite reads should use read-only URI | R8 | `file:path?mode=ro` with `uri=True` — prevents TOCTOU file creation | AST-structural: r7_health_uses_readonly_uri |
| ~~TD-O~~ | WAL checkpoint strategy undefined | R9→R10 | `_open_db` finally block: time-driven `wal_checkpoint(PASSIVE)` + `incremental_vacuum(64)` (max once/30s); failures logged as WARNING with count | Behavioral: r10_time_driven_skip + r10_time_driven_runs; Source: r10_has_interval_guard |
| ~~TD-P~~ | WAL file growth invisible to monitoring | R9 | `/health/detailed` reports `wal_size_bytes` per DB, alerts >10 MB | AST-structural: r9_health_has_wal_size |
| ~~TD-Q~~ | `shell_execute` allows file-write redirections | R9 | `_detect_write_redirect` blocks `>`, `>>`, `tee`, `dd of=` (excludes `/dev/null`, fd redirects); suggests `file_edit`. **Coverage limits documented**: cp/mv, heredoc, fd indirection, inline interpreters not detected (by design) | Behavioral: 9 pattern tests + r9_execute_blocks_write_redirect |
| ~~TD-R~~ | WAL maintenance failure not observable | R10 | Failures logged as WARNING (first 3 + every 10th); `/health/detailed` exposes `maintenance_fail_count` + `maintenance_last_success` per DB; alerts at >5 consecutive failures | Behavioral: r10_maint_failure_logged; AST-structural: r10_health_has_maint_fields |
| ~~TD-S~~ | checkpoint/vacuum runs per-connection (unnecessary overhead) | R10→R11 | Hybrid strategy: time-driven (`_DB_MAINT_INTERVAL=30s`) + WAL-size-driven (`_DB_MAINT_WAL_THRESHOLD=1MB` bypasses interval). Adapts to write-heavy workloads | Behavioral: r10_time_driven_skip + r10_time_driven_runs + r11_wal_size_bypass |
| ~~TD-T~~ | git initial commit contained build artifacts and test residuals | R11 | `git rm --cached` removed 430+ `__pycache__/`, `.nanobot_state/`, `*.bak`, `*.lock` files; `.gitignore` expanded to prevent re-tracking | Verified: `git ls-files --cached \| grep __pycache__` returns 0 |

## Policy

### RESOLVED Minimum Verification Standard

Every item marked RESOLVED **must** have at least one of:
1. **Behavioral test** (preferred) — exercises the code path and asserts the expected outcome
2. **Source inspection** (fallback) — only acceptable when behavioral testing is impractical (e.g., docstring content)

Items verified only by `grep` text matching are **not eligible** for RESOLVED status. They must be upgraded to at least source inspection via `inspect.getsource()`.

Items resolved before R8 (marked "pre-R8 standard") retain their original verification. When any such item is revisited in a future audit, its verification must be upgraded to meet the current standard before re-confirmation.

### Audit Errata

| Round | Errata | Root Cause | Resolution |
|-------|--------|------------|------------|
| R6 (Audit) | Two "致命" findings (connection leak + implicit file creation in `/health/detailed`) were false positives | **Root cause indeterminate**: without git history, cannot distinguish between (a) auditor did not see existing `try/finally` + `is_file()` context, or (b) protections were added after audit and retroactively claimed as pre-existing. Both possibilities are recorded. | R8 added `mode=ro` URI as defense-in-depth regardless of root cause; R10 initialized git repo to prevent future ambiguity |
| R6→R8 impact | R6 misreport referenced in R7/R8 audit discussions as evidence of "connection leak" risk, which motivated the `mode=ro` URI hardening and `@contextmanager` exception propagation test (TD-M). **Net impact: positive** — the false positive drove defense-in-depth improvements that would not have been prioritized otherwise. No unnecessary code removals resulted from the misreport. | Retroactive assessment: no code was damaged by acting on the false positive. | No corrective action needed beyond existing defense-in-depth. |

**Process improvement**: All audit findings must reference exact line numbers. Developer responses must include the original code at those lines. Starting R10, this workspace is git-tracked — `git blame` provides the authoritative evidence chain for all future disputes.

### Escalation / De-escalation Criteria

| Category | Escalation trigger | De-escalation trigger |
|----------|-------------------|----------------------|
| Architecture Defects | 3 audit rounds without design doc → auto-escalate to Critical | Design doc approved → reclassify as Evolution Debt |
| Evolution Debt | 3 audit rounds without any progress → escalate to Architecture Defect | Partial progress (≥1 sub-item resolved) → reset counter |
| Immediate Fixes | Present in 2 audit rounds → escalate to Evolution Debt | N/A (should not survive 2 rounds) |

### Standing Rules

- **New flags**: Every feature flag added to `_REGISTRY` must have a corresponding `ff.is_enabled()` or `ff.get_*()` call in production code before merge, **plus** a negative-path test verifying flag=false disables the feature.
- **"Not fixing" decisions**: Must be recorded here with rationale. Reviewed quarterly.
- **Promotion**: Items reaching 3 audit rounds without resolution are auto-escalated to "High" severity.
- **Policy violations**: Each violation must be recorded with date and justification in the item's Status column.
