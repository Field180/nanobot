# Nanobot Security Onboarding — 10 Minute Guide

> **Purpose**: Get new contributors up to speed on Nanobot's security architecture.
> For full details, see [SECURITY.md](SECURITY.md).
>
> Last updated: 2026-05-02

> **⚠️ CRITICAL CONSTRAINT**: Nanobot assumes **single-user, single-process** deployment.
> All session state is stored in module-level globals with no locking. Do NOT design
> multi-user features without first completing the R4 refactoring described in
> [SECURITY.md §R4](SECURITY.md#r4-global-state-coupling-session-leak).
> Search for `SINGLE-USER ASSUMPTION` in `agentic_loop.py` to find all affected globals.

---

## 1. Core Security Principles

| Principle | Rule | Enforced By |
|-----------|------|-------------|
| **Defense in Depth** | No single point of failure | MCP 3-layer filter; multiple tool validation |
| **Default Secure** | Unsafe ops require explicit authorization | Plan Mode = read-only tools; shell needs user approval |
| **Least Privilege** | Tools only get what they need | `task_constraints.py` limits tools per task state |
| **Observable Security** | All security decisions are auditable | `audit_logger.py` records all tool calls |

---

## 2. Security Red Lines (Never Do These)

1. **Never store secrets in code** — use environment variables or `.env` files
2. **Never disable path validation** — `_resolve_path()` in `tools/base.py` is the guardian
3. **Never modify global `_SESSION_*` variables from a worker thread** — main thread only
4. **Never trust MCP tool descriptions** — they are always sanitized by `mcp_client.py`
5. **Never remove `SINGLE-USER ASSUMPTION` comments** without completing R4 refactoring

> **First time seeing `[SECURITY BLOCK]` in your test output?** Jump to
> [§9 Troubleshooting](#9-troubleshooting-common-security-failures) for
> explanations and fixes.

---

## 3. Key Security Mechanisms

### 3.1 MCP External Tool Injection Defense

```
[External MCP Server] → tools/list response
    ↓
[Layer 1] _SAFE_NAME_RE — regex whitelist for tool names
    ↓
[Layer 2] _sanitize_description() — char-level sanitization + truncation
    ↓
[Layer 3] inputSchema.properties — parameter descriptions recursively filtered
    ↓
[Safe tool list] → injected into OpenAI function-calling schema
```

**Code locations**:
- `tools/mcp_client.py:888` — `_SAFE_NAME_RE` regex definition
- `tools/mcp_client.py:892` — `_sanitize_description()` implementation
- `tools/mcp_client.py:904-913` — Layer 1 name validation in `_rebuild_tool_map()`

**Owner**: `tools/mcp_client.py`  
**Tests**: `tests/test_mcp.py` (58 tests)

### 3.2 File Path Traversal Defense

- `_resolve_path()` in `tools/base.py` validates ALL LLM-generated paths
- Absolute paths checked against allowlist (workspace, `$HOME`, `/tmp`)
- Relative paths resolved and verified to stay under workspace
- Blocked paths produce explicit `ValueError` (no silent degradation)

**Code location**: `tools/base.py:112` — `_resolve_path()` function

**Tests**: `tests/test_replay_e2e.py::TestPathTraversalDefense` (7 tests)

### 3.3 Concurrency Safety

- **Architecture**: Single-user, single-process, asyncio event loop
- **Guards**: 5× `assert threading.current_thread() is threading.main_thread()`
- **Defensive**: `list()` snapshots on all dict iterations over globals
- **Markers**: `SINGLE-USER ASSUMPTION` comments on all global state declarations

**Code locations** (5 guarded functions in `agentic_loop.py`):
- `:1819` — `_track_file_activity()`
- `:1903` — `_reset_session_file_reads()`
- `:1937` — `_record_tool_failure()`
- `:1979` — `_reset_session_failures()`
- `:2126` — `_get_repo_map()`

**Global state markers** (`SINGLE-USER ASSUMPTION` in `agentic_loop.py`):
- `:1798` — `_SESSION_ACTIVE_FILES`
- `:1930` — `_SESSION_FAILURES`
- `:1987` — `_REPO_MAP_CACHE`

**Tests**: `tests/test_routes_registration.py::TestCallSiteTimingContract` (AST security contract tests)

---

## 4. Code Contracts (Must Follow)

### Adding a New Tool

1. Define tool in `tools/<name>.py` with `TOOL_DEF` dict and `execute()` function
2. Validate through `tools/tool_schema.py` Pydantic model
3. All file paths MUST go through `_resolve_path()`
4. Mark `IS_READONLY = True` if the tool doesn't modify state

### Modifying Global State

1. Add `SINGLE-USER ASSUMPTION` comment to any new module-level variable
2. Update `SECURITY.md` R4 section with the new variable
3. Add `assert threading.current_thread() is threading.main_thread()` to mutation functions
4. Run `make safety-check` to verify markers
5. The AST auto-discovery test (`test_auto_discover_mutation_functions_have_assertions`
   in `tests/test_routes_registration.py`) will **automatically detect** new functions
   that write to guarded globals without assertions — CI will fail if step 3 is skipped

### Adding a Command

1. Follow `commands/` package pattern (see `commands/status.py` as reference)
2. Create `CommandDefinition` with handler + metadata
3. Register via `register()` — auto-discovered by `_auto_discover()`
4. Old commands in `server_final.py` if-elif chain are being gradually migrated

---

## 5. Risk Register Quick Reference

| ID | Risk | Status |
|----|------|--------|
| R1 | MCP Prompt Injection | ✅ Mitigated |
| R2 | MCP Parameter Schema Injection | ⚠️ Partial |
| R4 | Global State (multi-user) | ⚠️ Partial (single-user OK) |
| R5 | Parser Fault Tolerance | ✅ Mitigated |
| R6 | Prompt/Model Regression | ❌ Not Covered |
| R8 | Path Traversal | ✅ Mitigated |
| R9 | API Error Boundary | ✅ Mitigated |
| R14 | Calling Sequence Degradation | ✅ Mitigated (see caveats) |

Full details: [SECURITY.md](SECURITY.md)

---

## 6. Pre-Release Checklist

```bash
# 1. Run all tests
make test

# 2. Check security markers
make safety-check

# 3. Verify deprecation ceiling
make ci

# 4. Grep for single-user markers
grep -rn "SINGLE-USER ASSUMPTION" agentic_loop.py

# 5. Review SECURITY.md for any ❌ risks
```

---

## 7. Architecture Evolution Status

| Phase | Status | Key Deliverables |
|-------|--------|-----------------|
| Phase 1: Crisis Response | ✅ | MCP injection defense, path traversal, session state |
| Phase 2: Debt Management | ✅ | Global state decoupling, deprecation ratchet |
| Phase 3: Structure Hardening | ✅ | U23 commands/ package, Plan Mode, security docs |
| Phase 4: Platform Evolution | 🟡 | CI pipeline, full command migration, multi-session |

---

## 8. Mechanism Dependencies

Some security mechanisms depend on each other. Breaking one weakens the other:

| Mechanism | Depends On | If Broken |
|-----------|-----------|----------|
| R14 concurrency safety | Main-thread assertions **AND** AST contract tests | Either alone is insufficient: assertions can be disabled by `python -O`; AST tests only cover 5 known functions |
| R4 session isolation | `_reset_session_*()` called at session start **AND** single-process deployment | If concurrent sessions share process, resets cause cross-session data loss |
| R8 path traversal defense | `_resolve_path()` called by every file tool | If a new tool bypasses `_resolve_path()`, the defense has a gap |
| MCP injection defense (R1) | All 3 layers (name regex + description sanitizer + param filter) | Layer 1 alone blocks most attacks; layers 2-3 are defense-in-depth |

---

## 9. Troubleshooting Common Security Failures

**"assert ... main_thread() check" test failure**:
- A function that writes to `_SESSION_ACTIVE_FILES`, `_SESSION_FAILURES`,
  `_REPO_MAP_CACHE`, or `_REPO_MAP_CACHE_TURN` is missing a main-thread assertion.
- Fix: add `assert threading.current_thread() is threading.main_thread()` as the
  first line of that function. See existing examples in `agentic_loop.py:1819`.

**"Functions that write to guarded globals but lack main-thread assertion" test failure**:
- The auto-discovery AST test found a **new** function mutating globals without an assertion.
- This means the known-list test didn't catch it, but the auto-discovery did.
- Fix: same as above — add the assertion to the flagged function(s).

**"[SECURITY] Python running with -O flag" warning at startup**:
- The application detected `python -O` (optimized mode), which disables all `assert`
  statements including the R14 main-thread guards.
- Fix: run without `-O` flag. Never deploy with `python -O` in production.

**AST test passes but security contract is still violated**:
- The AST test only detects writes via subscript assignment, `.clear()`, `.append()`,
  `.pop()`, `.update()`, and `del`. If a new mutation pattern is used (e.g., passing
  the global to another function that mutates it), the AST test may miss it.
- Fix: add the function to the known-list in `test_all_mutation_functions_have_thread_assertions`.

---

## 10. Decoupling Global Variable Dependencies (Team Norm)

> **Audit-mandated guideline** (2026-05-03): Codifies the pattern proven by `_run_preflight_detections()`.

### When to decouple

Apply this pattern whenever a **closure variable** (e.g. `_current_task_text`) is referenced
by ≥3 call sites within the same long function. Signs of trouble:

- Multiple function calls in a row receive the same local variable as their sole argument.
- A code change to the variable's definition requires grepping for all consumers.
- The `DEPENDENCY NOTICE` comment for the variable lists the consumer.

### How to decouple

Two approaches, chosen by **domain cohesion** (not by reference count):

**A. Bundling function** — when ≥3 consumers share the same business domain:
1. Create a function that receives `task_text: str` and returns a `dict`.
2. Move the related calls inside — preserve logging and return values exactly.
3. Replace call sites with a single call + dict destructure.

**B. Single-purpose wrapper** — when a consumer is domain-independent:
1. Create a function that receives `task_text: str` and returns the result directly.
2. Replace the call site with the wrapper call.

In both cases:
1. **Update the DEPENDENCY NOTICE** in `agentic_loop.py`.
2. **Register in the backlog** in `test_audit_fixes.py` (move to `_DECOUPLED` set).
3. **Run** `python3 tests/test_audit_fixes.py` — it must pass.

### Selection principles

When choosing which dependency to decouple next:

1. **Domain cohesion first** — only bundle consumers that belong to the same feature.
   Never bundle unrelated features just to reduce a count.
2. **Security-sensitive last** — deps involving security checks (P27 file_read limits,
   B15 full-file override) need extra care and dedicated test coverage.
3. **Already-explicit first** — functions that already accept `task_text` as a parameter
   (e.g. `_build_completeness_nudge`) are trivially decoupled.

### Reference implementations

```python
# Bundling (domain-cohesive): _run_preflight_detections(task_text) -> dict
# Bundles P7, P102, P104, P104-fork, P38 — all pre-flight hint detections.

# Single-purpose wrappers (domain-independent):
# _get_memory_trigger(task_text) -> Optional[str]   — P92 memory detection
# _match_skill_intent(task_text) -> Optional[tuple]  — P98c skill matching
```

### Backlog maintenance rules

The decoupling backlog (`_BACKLOG` in `test_audit_fixes.py`) enforces deadlines on
unresolved `_current_task_text` dependencies. Expired entries cause CI failure.

**Adding a new entry:**
1. Add the symbol to `_BACKLOG` with `(target_date, owner, reason)`.
2. `target_date` must be ≤60 days from today.
3. `reason` must be a specific technical justification (not "will do later").

**Requesting a deadline extension:**
1. Update `target_date` in the backlog entry.
2. Add a comment on the same line: `# extension-approved-by-cto: <reason>`
3. Extension must not exceed 30 additional days from the original deadline.
4. The extension comment is required — bare date changes without it should be
   rejected in code review.

**Code review standards for `reason` quality:**
Reviewers must verify that each `reason` field answers *one* of these:
- Why can't this be decoupled now? (e.g. "depends on P33 refactor completing first")
- What is blocking? (e.g. "needs security regression test suite for B15/B16")
- What is the decoupling plan? (e.g. "extract to `_check_explore_lock(task_text)`")

Reject entries with vague reasons like "TBD", "will fix", or "low priority".

### Anti-patterns (do NOT do)

- ❌ Bundling unrelated business domains (e.g. memory + skills) just to reduce ref count.
- ❌ Adding the consumer's name to the DEPENDENCY NOTICE without understanding the contract.
- ❌ Passing the entire `locals()` dict to avoid listing parameters.
- ❌ Creating a God-class to hold all extracted variables.
- ❌ Extending backlog deadlines without `# extension-approved-by-cto` comment.

---

## Questions?

- Security concerns → file in `SECURITY.md` as a new risk row
- Architecture questions → check `PROJECT_STATUS_REPORT.md`
- Command system → see `commands/` package and `commands/status.py` reference
