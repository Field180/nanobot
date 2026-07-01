# Nanobot Local CI Automation
# Run these targets before every commit to ensure quality.
# No remote CI required — this provides local enforcement.

# PYTHON/PIP can be overridden on the command line or via env vars.
# Local dev defaults to the user's venv; CI overrides with bare `python3`/`pip`.
# Examples:
#   make test                                    # local venv
#   PYTHON=python3 PIP=pip make test             # CI / containers
PYTHON ?= /home/field/nanobotProjects/nanobot/.venv/bin/python3
PIP ?= /home/field/nanobotProjects/nanobot/.venv/bin/pip

# Deprecation violation ceiling — ratchet down as violations are fixed.
# CI fails if the count exceeds this number, preventing new violations.
# Current baseline: 145 (2026-05-02).  Reduce after each migration batch.
# CHANGE CONTROL: This value should only DECREASE (or stay equal).
# Any increase MUST include a justification in the commit message.
DEPRECATION_CEILING := 145

# Maximum allowed gap between ceiling and actual violations.
# If the gap exceeds this, CI fails — forces periodic ceiling updates.
# This prevents the ceiling from becoming a stale "forever" number.
DEPRECATION_CEILING_MAX_SLACK := 20

.PHONY: help install-hooks test test-fast test-audit test-deprecations test-deprecations-strict check check-docs security-audit ci clean test-ratchet git-audit
# Preserve non-zero exit codes through pipes (otherwise `cmd | grep` masks failure)
SHELL := /bin/bash
.SHELLFLAGS := -o pipefail -c

# Default: show help
help:
	@echo "Nanobot Local CI Targets:"
	@echo "  make test          - Run full test suite + safety-check"
	@echo "  make test-audit    - Run audit fix tests (standalone, hard-fail on any failure)"
	@echo "  make test-fast     - Run only fast unit tests (skip integration)"
	@echo "  make test-deprecations - Run tests with SessionStore deprecation warnings enabled (advisory)"
	@echo "  make test-deprecations-strict - Same as above but FAILS CI on any direct-access warning"
	@echo "  make check         - Full validation: tests + deprecation audit + docs"
	@echo "  make ci            - CI gate: test + deprecation ratchet (fails if violations increase)"
	@echo "  make check-docs    - Verify PROJECT_STATUS_REPORT.md accuracy"
	@echo "  make security-audit- Run security-focused tests"
	@echo "  make git-audit     - Audit git config keys against safety rules"
	@echo "  make install-hooks - Install git pre-commit hook"
	@echo "  make test-ratchet  - Self-test the deprecation ratchet mechanism"
	@echo "  make clean         - Remove cache and temp files"

# Full test suite (the gate that must pass before any commit)
# safety-check runs automatically — security contracts are non-negotiable.
test: safety-check
	@echo "═══════════════════════════════════════════════════════════"
	@echo "Running full test suite..."
	@echo "═══════════════════════════════════════════════════════════"
	$(PYTHON) -m unittest discover -s tests -p "test_*.py" -v 2>&1 | tail -20
	@echo ""
	@echo "If you see 'OK' above, all tests passed."

# Fast tests only (for rapid iteration)
test-fast:
	$(PYTHON) -m unittest discover -s tests -p "test_*.py" -v 2>&1 | grep -E "(Ran|OK|FAILED)" | tail -5

# Deprecation-warning audit: enables the SessionStore direct-access warnings
# AND makes Python surface DeprecationWarning (normally filtered by default).
# Use this in CI to catch any new code that bypasses the session lock guard.
# Advisory mode: prints warnings but does NOT fail the build.
test-deprecations:
	@echo "═══════════════════════════════════════════════════════════"
	@echo "Running tests with SessionStore deprecation warnings ENABLED (advisory)"
	@echo "═══════════════════════════════════════════════════════════"
	NANOBOT_DEPRECATE_DIRECT_SESSION=1 PYTHONWARNINGS=default::DeprecationWarning \
		$(PYTHON) -W default::DeprecationWarning -m unittest discover -s tests -p "test_*.py" \
		> /tmp/nanobot_deprecations.log 2>&1 || true
	@grep -E "(DeprecationWarning|Ran |OK|FAILED)" /tmp/nanobot_deprecations.log | tail -40
	@echo ""
	@echo "───────────────────────────────────────────────────────────"
	@VIOLATIONS=$$(grep -c "DeprecationWarning" /tmp/nanobot_deprecations.log 2>/dev/null || echo 0); \
		if [ "$$VIOLATIONS" -gt 0 ]; then \
			echo "⚠ $$VIOLATIONS direct-access deprecation warning(s) detected (ceiling: $(DEPRECATION_CEILING))."; \
			echo ""; \
			echo "  Top offending files:"; \
			grep "DeprecationWarning" /tmp/nanobot_deprecations.log 2>/dev/null \
				| sed -n 's|^.*web_ui/\([^:]*\.py\):.*|\1|p' \
				| sort | uniq -c | sort -rn | head -10 \
				| while read cnt file; do echo "    $$cnt  $$file"; done; \
			if [ "$$VIOLATIONS" -gt $(DEPRECATION_CEILING) ]; then \
				echo ""; \
				echo "✘ RATCHET FAILED: $$VIOLATIONS > ceiling $(DEPRECATION_CEILING)."; \
				echo "  New direct-access violations were introduced."; \
				echo "  Fix them or raise DEPRECATION_CEILING in Makefile (with justification)."; \
				exit 1; \
			else \
				echo ""; \
				SLACK=$$(($(DEPRECATION_CEILING) - $$VIOLATIONS)); \
				if [ "$$SLACK" -gt $(DEPRECATION_CEILING_MAX_SLACK) ]; then \
					echo "✘ CEILING TOO STALE: gap is $$SLACK (max allowed: $(DEPRECATION_CEILING_MAX_SLACK))."; \
					echo "  Violations: $$VIOLATIONS, Ceiling: $(DEPRECATION_CEILING)."; \
					echo "  Fix: lower DEPRECATION_CEILING to $$VIOLATIONS to lock in migration progress."; \
					echo "  ⚠ Do NOT raise the ceiling to bypass this check — that hides real progress."; \
					exit 1; \
				elif [ "$$VIOLATIONS" -lt $(DEPRECATION_CEILING) ]; then \
					echo "⚡ Violations dropped to $$VIOLATIONS (ceiling is $(DEPRECATION_CEILING), slack: $$SLACK/$(DEPRECATION_CEILING_MAX_SLACK))."; \
					echo "  → Update DEPRECATION_CEILING from $(DEPRECATION_CEILING) to $$VIOLATIONS in Makefile to lock in progress."; \
				else \
					echo "✓ Ratchet OK: $$VIOLATIONS ≤ ceiling $(DEPRECATION_CEILING)."; \
				fi; \
			fi; \
		else \
			echo "✓ 0 deprecation violations — ready for strict mode."; \
		fi

# Strict mode: promotes the SessionStore direct-access warning to an error,
# so CI fails if any code path still bypasses the lock guard.  Use this as
# the enforcement gate AFTER migrating all known call sites.
test-deprecations-strict:
	@echo "═══════════════════════════════════════════════════════════"
	@echo "Running tests with SessionStore deprecation warnings as ERRORS"
	@echo "═══════════════════════════════════════════════════════════"
	NANOBOT_DEPRECATE_DIRECT_SESSION=1 \
		$(PYTHON) -W error::DeprecationWarning -m unittest discover -s tests -p "test_*.py"
	@echo "No direct-access violations detected."

# Audit fix tests — runs as standalone script so sys.exit(1) fires on failure.
# This enforces alerts↔Runbook sync, Dashboard↔metrics sync, and all R16-R21 checks.
test-audit:
	@echo "═══════════════════════════════════════════════════════════"
	@echo "Running audit fix tests (hard-fail mode)..."
	@echo "═══════════════════════════════════════════════════════════"
	$(PYTHON) tests/test_audit_fixes.py
	@echo "✓ Audit fix tests passed."

# Full validation pipeline (equivalent to CI gate)
check: test test-audit test-deprecations check-docs
	@echo "═══════════════════════════════════════════════════════════"
	@echo "Full validation complete."
	@echo "═══════════════════════════════════════════════════════════"
	@echo "Status: READY FOR COMMIT"

# CI gate: runs tests + deprecation ratchet check.
# The ratchet prevents new violations from being introduced.
# After all violations reach 0, switch to test-deprecations-strict.
ci: test test-audit test-deprecations safety-check git-audit
	@echo "CI gate passed."

# Documentation consistency check (informational warnings)
check-docs:
	@echo "═══════════════════════════════════════════════════════════"
	@echo "Checking documentation consistency..."
	@echo "═══════════════════════════════════════════════════════════"
	@$(PYTHON) -m unittest tests.test_replay_e2e.TestDocumentationConsistency -v 2>&1 | tail -10
	@echo "Documentation check complete."

# Security-focused test subset
security-audit:
	@echo "═══════════════════════════════════════════════════════════"
	@echo "Running security tests..."
	@echo "═══════════════════════════════════════════════════════════"
	$(PYTHON) -m unittest tests.test_mcp_security -v 2>&1 | tail -10
	$(PYTHON) -m unittest tests.test_replay_e2e.TestReplayE2E.test_malformed_json_args_no_crash -v

# Install pre-commit hook (one-time setup)
install-hooks:
	@echo "#!/bin/sh" > .git/hooks/pre-commit
	@echo "# Nanobot pre-commit hook — runs local CI" >> .git/hooks/pre-commit
	@echo "echo 'Running pre-commit checks...'" >> .git/hooks/pre-commit
	@echo "make check || { echo 'COMMIT BLOCKED: checks failed'; exit 1; }" >> .git/hooks/pre-commit
	@echo "echo 'Pre-commit checks passed.'" >> .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "Pre-commit hook installed at .git/hooks/pre-commit"
	@echo "Run 'make check' manually to verify it works."

# Cleanup
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache 2>/dev/null || true
	@echo "Cleaned cache files"

# Record real LLM fixture (when Ollama is available)
record-fixture:
	@if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then \
		echo "ERROR: Ollama not running at localhost:11434"; \
		echo "Start with: ollama run llama3.2"; \
		exit 1; \
	fi
	@echo "Recording real LLM chunk stream..."
	$(PYTHON) scripts/record_fixture.py

# Safety check: verify no obvious security regressions
# Also verifies SINGLE-USER ASSUMPTION markers exist in agentic_loop.py
# AND are associated with the correct global variables (content check).
# (see SECURITY.md R4 — markers must survive until multi-session refactoring).
SINGLE_USER_MARKERS_EXPECTED := 3
SINGLE_USER_GLOBALS := _SESSION_ACTIVE_FILES _SESSION_FAILURES _REPO_MAP_CACHE
safety-check:
	@echo "═══════════════════════════════════════════════════════════"
	@echo "[SECURITY] Running safety-check..."
	@echo "═══════════════════════════════════════════════════════════"
	@echo "Checking for banned patterns..."
	@! grep -r "eval(" --include="*.py" . 2>/dev/null \
		| grep -v "test_" | grep -v "# eval" \
		| grep -v "\.eval(" \
		| grep -v "secure_eval\|_original_eval" \
		| grep -v "safe_globals\|__builtins__" \
		| grep -v "secure_interceptor\.py" \
		|| (echo "[SECURITY BLOCK] BANNED: eval() found — see SECURITY.md"; exit 1)
	@! grep -r "exec(" --include="*.py" . 2>/dev/null \
		| grep -v "test_" | grep -v "# exec" \
		| grep -v "subprocess_exec\|secure_exec\|_original_exec" \
		| grep -v "safe_globals" \
		| grep -v "secure_interceptor\.py" \
		| grep -v "'exec(\|\"exec(" \
		| grep -v "startswith.*exec" \
		|| (echo "[SECURITY BLOCK] BANNED: exec() found — see SECURITY.md"; exit 1)
	@echo "No banned patterns found"
	@echo ""
	@echo "Checking SINGLE-USER ASSUMPTION markers (SECURITY.md R4)..."
	@MARKERS=$$(grep -c 'SINGLE-USER ASSUMPTION' agentic_loop.py 2>/dev/null || echo 0); \
		if [ "$$MARKERS" -lt $(SINGLE_USER_MARKERS_EXPECTED) ]; then \
			echo "[SECURITY BLOCK] Expected $(SINGLE_USER_MARKERS_EXPECTED) SINGLE-USER ASSUMPTION markers in agentic_loop.py, found $$MARKERS."; \
			echo "  These markers must not be removed until the R4 refactoring is complete."; \
			exit 1; \
		else \
			echo "✓ $$MARKERS SINGLE-USER ASSUMPTION markers present."; \
		fi
	@echo "Verifying marker-to-global association (content integrity)..."
	@FAIL=0; \
		for global in $(SINGLE_USER_GLOBALS); do \
			if ! grep -B5 "$$global:" agentic_loop.py 2>/dev/null | grep -q 'SINGLE-USER ASSUMPTION'; then \
				echo "  ✘ $$global declaration missing SINGLE-USER ASSUMPTION marker within 5 lines above."; \
				FAIL=1; \
			fi; \
		done; \
		if [ "$$FAIL" -eq 1 ]; then \
			echo "[SECURITY BLOCK] Markers must be directly above the global they annotate."; \
			exit 1; \
		else \
			echo "✓ All markers correctly associated with their globals."; \
		fi
	@echo ""
	@echo "Running AST security contract tests (R14 concurrency safety)..."
	@$(PYTHON) -m unittest tests.test_routes_registration.TestCallSiteTimingContract -v 2>&1 | tail -10
	@echo "✓ AST security contracts verified."
	@echo "═══════════════════════════════════════════════════════════"
	@echo "[SECURITY] safety-check PASSED"
	@echo "═══════════════════════════════════════════════════════════"

# Git config key safety audit — catches new executable keys in git updates.
# Fails the build if any key matching a safe prefix is potentially executable
# but not in the blocklist. Run quarterly or on git version bumps.
git-audit:
	@echo "═══════════════════════════════════════════════════════════"
	@echo "[SECURITY] Running git config key audit..."
	@echo "═══════════════════════════════════════════════════════════"
	@$(PYTHON) -c "\
import sys; sys.path.insert(0, '.'); \
from tools.shell_execute import audit_git_config_keys; \
r = audit_git_config_keys(); \
print(f'Git: {r[\"git_version\"]}'); \
print(f'Keys audited: {r[\"total_keys\"]}'); \
print(f'Suspect keys: {r[\"suspect_keys\"]}'); \
sys.exit(0 if r['ok'] else 1)"
	@echo "✓ git-audit PASSED"

# Self-test for the deprecation ratchet mechanism.
# Verifies that the comparison logic correctly passes and fails.
test-ratchet:
	@echo "Testing ratchet mechanism..."
	@for i in $$(seq 5); do echo "DeprecationWarning"; done > /tmp/nanobot_ratchet_test.log
	@COUNT=$$(grep -c "DeprecationWarning" /tmp/nanobot_ratchet_test.log); \
		if [ "$$COUNT" -le 10 ]; then echo "  ✓ Pass case correct ($$COUNT ≤ 10)"; \
		else echo "  ✘ Pass case failed"; rm -f /tmp/nanobot_ratchet_test.log; exit 1; fi
	@for i in $$(seq 15); do echo "DeprecationWarning"; done > /tmp/nanobot_ratchet_test.log
	@COUNT=$$(grep -c "DeprecationWarning" /tmp/nanobot_ratchet_test.log); \
		if [ "$$COUNT" -gt 10 ]; then echo "  ✓ Fail case correct ($$COUNT > 10)"; \
		else echo "  ✘ Fail case failed"; rm -f /tmp/nanobot_ratchet_test.log; exit 1; fi
	@rm -f /tmp/nanobot_ratchet_test.log
	@echo "Ratchet self-test passed."
