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
| ED-7 | write-redirect bypass via inline interpreters (`python -c`, `perl -e`) — regex cannot detect | Low | R11→R12 | Deferred with decision criteria: **Introduce OS-level isolation (seccomp/namespace) IF** (a) LLM generates bypass commands in ≥3 real user sessions, OR (b) shell_execute is exposed to untrusted multi-tenant input. **Retain regex-only IF** single-tenant local deployment remains the only use case. Decision owner: project maintainer. Review at next audit. |

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
| ~~TD-S~~ | checkpoint/vacuum runs per-connection (unnecessary overhead) | R10→R12 | Hybrid strategy: time-driven (`_DB_MAINT_INTERVAL=30s`) + WAL-size-driven (`_DB_MAINT_WAL_THRESHOLD=1MB`) with 10s cooldown on size path to prevent every-connection spam on stale large WAL | Behavioral: r10_time_driven_skip + r10_time_driven_runs + r11_wal_size_bypass + r12_size_trigger_cooldown |
| ~~TD-T~~ | git initial commit contained build artifacts and test residuals | R11→R12 | `git rm --cached` removed 430+ files; `.gitignore` uses `**/` prefix for nested matching + `test_u3_result*.json` pattern; `git gc --aggressive` reclaims object storage | Verified: `git ls-files --cached \| grep __pycache__` returns 0 |
| ~~TD-U~~ | WAL checkpoint effectiveness unknown — PASSIVE may silently fail with active readers | R13→R16 | `wal_checkpoint(PASSIVE)` return value inspected. R14: growth-aware alert. R15: periodic baseline refresh + 50MB critical + Prometheus export. R16: **deviation-based baseline refresh** — if WAL grows >50% from baseline during ineffective streak, baseline refreshed immediately (coexists with periodic refresh). Dual strategy eliminates both slow-drift and fast-spike blind spots. | Source: r16_deviation_refresh + r16_deviation_coexists_with_periodic; prior: r15_* |
| ~~TD-V~~ | shell_execute unblocked command volume invisible | R13→R15 | `_SHELL_PASSED` counter in `/health/detailed` shell component + Prometheus `nanobot_shell_passed` counter. `block_ratio` exposed for external anomaly detection. | Source: r13_metrics_has_passed + r15_prom_shell_passed; AST: r14_health_has_shell_component + r14_health_shell_passed |
| ~~TD-W~~ | No Prometheus alert rules template — monitoring requires manual setup | R16→R18 | `deploy/prometheus/alerts.yml` provides 7 pre-built rules. R18: `deploy/prometheus/example.alertmanager.yml` provides starter notification config with `REPLACE_ME` placeholders (webhook + commented Slack/PagerDuty). Completes the monitoring notification chain without exposing secrets. | File: r16_alerts_yml_exists + r18_alertmanager_example_exists + r18_alertmanager_has_receiver |
| ~~TD-X~~ | WAL maintenance monitoring blind spot during idle + initial periods | R17→R19 | R17: staleness alert at >600s. R18: initial-phase blind spot eliminated. R19: **behavioral tests added** — 7 time-mocked end-to-end scenarios verify alert trigger (>600s), recovery, never-completed (uptime >600s), and grace period (uptime <600s). Calls `detailed_health_check()` directly with manipulated state. | Behavioral: r19_stale_alert_fires + r19_stale_alert_clears_on_recovery + r19_never_completed_alert_fires + r19_no_alert_during_grace_period; Source: r17_* + r18_* |
| ~~TD-Y~~ | alerts.yml structural integrity not CI-validated | R18 | YAML-parsed structural test verifies all rules have `for:` duration and `severity` label. Prevents future audit misunderstandings about missing fields. | Structural: r18_all_rules_have_for + r18_all_rules_have_severity |
| ~~TD-Z~~ | No fault-handling Runbook — first-incident MTTR at risk | R20 | `deploy/prometheus/RUNBOOK.md` documents all 7 Prometheus alerts with Diagnosis (commands), Resolution (steps), and Escalation sections. Eliminates knowledge-silo dependency for on-call response. | File: r20_runbook_exists + r20_runbook_covers_all_alerts + r20_runbook_has_diagnosis + r20_runbook_has_resolution + r20_runbook_has_escalation |
| ~~TD-AA~~ | No Grafana dashboard — monitoring requires manual PromQL construction | R20 | `deploy/grafana/nanobot-dashboard.json` provides 10-panel importable dashboard: WAL size/staleness/failures, shell rate/block-ratio/error-rate/latency, process uptime, mailbox health. Uses `__inputs` datasource variable for portability. | File: r20_dashboard_exists + r20_dashboard_has_panels + r20_dashboard_valid_schema + r20_dashboard_has_datasource_input |

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
| R6→R8 impact | R6 misreport referenced in R7/R8 audit discussions as evidence of "connection leak" risk, which motivated the `mode=ro` URI hardening and `@contextmanager` exception propagation test (TD-M). **Net impact: neutral to negative** — defense-in-depth improvements (`mode=ro`, TD-M) have standalone value, but were prioritized based on a false premise. Opportunity cost: audit time spent on false positives was unavailable for real issues (e.g., WAL strategy, write-redirect bypass). The misreport also set a precedent where unfounded "致命" severity distorts risk prioritization. | Retroactive assessment: no production code was damaged; defense-in-depth code adds marginal maintenance burden (~20 LoC) but no runtime risk. | Existing defense-in-depth retained (removal would create churn for no benefit). |

**Process improvement**: All audit findings must reference exact line numbers. Developer responses must include the original code at those lines. Starting R10, this workspace is git-tracked — `git blame` provides the authoritative evidence chain for all future disputes.

### Audit Test Ownership

| File | Owner | Purpose | Update trigger |
|------|-------|---------|----------------|
| `tests/test_audit_fixes.py` | Project maintainer (hand-maintained) | Regression tests for audit-identified issues | Each audit round that introduces code changes |

**Maintenance contract**: When `_open_db`, `shell_execute`, or `/health/detailed` are refactored, the maintainer must run `python tests/test_audit_fixes.py` and update any source-inspection tests whose string assertions break due to renamed variables or restructured code. Tests that check *behavioral outcomes* (e.g., `r10_time_driven_runs`) are refactor-safe; tests that check *source strings* (e.g., `r13_reads_checkpoint_return`) may need updating.

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
