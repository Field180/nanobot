# Nanobot Alert Runbook

Fault-handling procedures for all Prometheus alert rules defined in `alerts.yml`.
Each section covers: what the alert means, diagnostic commands, resolution steps, and escalation.

> **Prerequisite**: Prometheus scraping `<nanobot_host>:<port>/api/metrics`.
> WAL files are located at `~/.nanobot/rag_vectors.db-wal` and `~/.nanobot/knowledge_graph.db-wal`.

---

## 1. NanobotWalSizeCritical

**Severity**: critical  
**Fires when**: WAL file exceeds 50 MB for >1 minute.

### Diagnosis

```bash
# Check WAL file sizes
ls -lh ~/.nanobot/*.db-wal

# Check for long-running SQLite readers (open file descriptors)
lsof ~/.nanobot/rag_vectors.db 2>/dev/null | head -20

# Check disk space
df -h ~/.nanobot/
```

### Resolution

1. **Identify blocking readers** — long-running read transactions prevent WAL checkpointing. Kill or complete them.
2. **Force checkpoint** (if safe): `sqlite3 ~/.nanobot/rag_vectors.db "PRAGMA wal_checkpoint(TRUNCATE);"` — only when no active writes.
3. **Check disk I/O** — slow disks can cause checkpoint backlog: `iostat -x 1 5`.
4. **Restart Nanobot** as last resort — process restart forces WAL cleanup on reconnect.

### Escalation

If WAL exceeds 100 MB or disk is >90% full, escalate to on-call. Risk: disk exhaustion can corrupt all databases.

---

## 2. NanobotWalSizeWarning

**Severity**: warning  
**Fires when**: WAL file exceeds 10 MB for >5 minutes.

### Diagnosis

Same as NanobotWalSizeCritical. This is an early warning — WAL is growing but not yet critical.

### Resolution

1. **Monitor trend** — check if WAL is growing or stable. Stable at 10-20 MB under load may be normal.
2. If growing: follow NanobotWalSizeCritical resolution steps.
3. **Verify maintenance loop is running** — check `nanobot_wal_maint_last_check_seconds`. If stale, see alert #5.

### Escalation

Escalate if WAL has been >10 MB for >30 minutes and growing. May indicate checkpoint is permanently blocked.

---

## 3. NanobotWalMaintenanceFailing

**Severity**: warning  
**Fires when**: >5 consecutive WAL maintenance failures for >2 minutes.

### Diagnosis

```bash
# Check Nanobot logs for WAL maintenance errors
grep -i "wal.*maint\|checkpoint.*fail" ~/.nanobot/logs/*.log 2>/dev/null | tail -20

# Check database file permissions
ls -la ~/.nanobot/*.db ~/.nanobot/*.db-wal ~/.nanobot/*.db-shm

# Check disk health
dmesg | grep -i "error\|fault" | tail -10
```

### Resolution

1. **Fix permissions** — Nanobot process must own the .db, .db-wal, and .db-shm files.
2. **Check disk space** — maintenance fails if disk is full.
3. **Check file locks** — another process may hold an exclusive lock: `fuser ~/.nanobot/rag_vectors.db`.
4. **Restart Nanobot** — clears stale file locks and reinitializes connections.

### Escalation

If failures persist after restart, the database file may be corrupted. Back up and run `sqlite3 <db> "PRAGMA integrity_check;"`.

---

## 4. NanobotWalCheckpointIneffective

**Severity**: warning  
**Fires when**: ≥5 consecutive ineffective checkpoints for >5 minutes.

### Diagnosis

```bash
# Check for active readers holding shared locks
lsof ~/.nanobot/rag_vectors.db 2>/dev/null

# Check the ineffective count metric
curl -s http://localhost:<port>/api/metrics | grep ineffective
```

### Resolution

1. **Identify readers** — PASSIVE checkpoint cannot proceed while readers hold shared locks. This is normal under heavy read load.
2. **Wait for readers to finish** — the alert auto-clears when a checkpoint succeeds.
3. **Consider TRUNCATE checkpoint** (downtime required): stops all readers, then runs `PRAGMA wal_checkpoint(TRUNCATE);`.
4. **If WAL is also large** (>10 MB): prioritize clearing readers. Combine with NanobotWalSizeWarning steps.

### Escalation

If ineffective for >1 hour with growing WAL, a reader may be stuck. Identify the PID via `lsof` and decide whether to terminate it.

---

## 5. NanobotWalMaintenanceStale

**Severity**: warning  
**Fires when**: Last successful maintenance was >600 seconds ago, for >2 minutes.

### Diagnosis

```bash
# Check if the Nanobot process is running
pgrep -f "server_final\|nanobot" || echo "Process not running!"

# Check metrics endpoint
curl -s http://localhost:<port>/api/metrics | grep maint_last_check

# Check if system was idle (no recent API calls)
curl -s http://localhost:<port>/health/detailed | python3 -m json.tool
```

### Resolution

1. **If process is not running** — restart Nanobot. This is the most common cause.
2. **If process is running but idle** — this is expected in low-traffic deployments. Consider increasing the threshold in `alerts.yml` (e.g., `> 1800` for 30-min tolerance).
3. **If process is running and active** — the maintenance loop may be stuck. Check logs for errors or deadlocks.
4. **If this fires at startup** — the alert uses process uptime as fallback when maintenance has never succeeded. Wait for the first maintenance cycle (up to 30 seconds after first DB connection).

### Escalation

If stale for >1 hour on an active system, the maintenance goroutine may have crashed silently. Restart required.

---

## 6. NanobotShellBlockRatioLow

**Severity**: warning  
**Fires when**: Block ratio < 1% with >100 total commands, for >10 minutes.

### Diagnosis

```bash
# Check shell metrics
curl -s http://localhost:<port>/api/metrics | grep nanobot_shell

# Check recent shell commands in logs
grep "shell_execute" ~/.nanobot/logs/*.log 2>/dev/null | tail -30
```

### Resolution

1. **Evaluate expected ratio** — in normal operation, the safety filter should block some write-redirect attempts (>, >>, tee, dd). A near-zero block ratio may mean:
   - The LLM model has learned to avoid blocked patterns (benign).
   - The safety filter is not working correctly (check `_detect_write_redirect` in code).
2. **Review recent commands** — look for write patterns that should have been blocked.
3. **Adjust threshold** — if your workload genuinely produces no write redirects, increase the `for:` duration or lower the ratio threshold.

### Escalation

If you suspect the safety filter is bypassed, audit recent shell commands immediately and consider temporarily disabling shell access.

---

## 7. NanobotShellErrorRateHigh

**Severity**: warning  
**Fires when**: Error rate exceeds 30% over 5-minute window, for >5 minutes.

### Diagnosis

```bash
# Check current error metrics
curl -s http://localhost:<port>/api/metrics | grep nanobot_shell

# Check recent errors in logs
grep -i "shell.*error\|command.*fail" ~/.nanobot/logs/*.log 2>/dev/null | tail -20

# Check system resources
free -h && df -h / && uptime
```

### Resolution

1. **Check resource exhaustion** — high error rate often correlates with low memory, full disk, or high load.
2. **Check command patterns** — the LLM may be generating malformed commands. Review the recent session.
3. **Check PATH and dependencies** — shell commands may fail if expected tools (git, grep, find) are not installed.
4. **Transient vs persistent** — if the rate drops after a brief spike, the issue may have been a temporary resource constraint.

### Escalation

If error rate stays >50% for >15 minutes, the system may be in a degraded state. Check for infrastructure issues (disk failure, network partition, OOM kills).

---

## General Tips

- **Metrics endpoint**: `GET /api/metrics` (requires auth token in `Authorization` header).
- **Health endpoint**: `GET /health/detailed` (no auth, returns JSON with issues array).
- **WAL files**: Located at `~/.nanobot/<dbname>.db-wal`. Safe to delete **only** when the process is stopped.
- **Safe restart**: `kill -TERM <pid>` allows graceful shutdown with WAL cleanup.
- **Never**: `kill -9` the process with open WAL files — may leave WAL in inconsistent state.
