# Nanobot Changelog

## P0~P9 Iteration (2026-04 ~ 2026-05)

### P0: MCP Integration
- `tools/mcp_client.py` — Model Context Protocol client for external tool servers
- Dynamic tool injection into agentic loop via `refresh_mcp_tools()`
- `/api/mcp/status` endpoint

### P1: Frontend Token Status Bar
- Real-time token consumption display in Web UI
- Task panel and tool card visual upgrades
- SSE `done` event with detailed `stats` payload

### P2: Swarm Multi-Agent
- `swarm.py` — parallel agent execution with message mailbox
- Fan-out/fan-in task decomposition
- 41 tests in `test_swarm.py`

### P3: Slash Command Completion
- Frontend autocomplete for `/skill` commands
- `/api/skills` endpoint returning all registered skills
- Conditional skills, forked execution, allowed tools enforcement (P99a-f)

### P5: Cost & Performance Observability
- `session_stats.py` — `SessionStatsStore` with per-turn token/tool/time aggregation
- `/api/stats?session_id=xxx` endpoint returning cumulative session metrics
- TUI `/stats` command with Rich tables (token summary + tool distribution)
- Stats collection integrated at `agentic_done` event in both server and TUI

### P6: Terminal TUI Mode
- `tui.py` — full terminal interface using Rich + prompt_toolkit
- Streaming event renderer, session history, slash command handling
- `build_env()` for environment configuration
- `/help`, `/stats`, `/model`, `/mode`, `/clear`, `/history` commands

### P7: Project Detection & Config Guidance
- `project_detector.py` — workspace scanning for tech stack auto-detection
- Detects: Python/JS/TS/Rust/Go/Java/C++ languages, 15+ frameworks, package managers, test frameworks
- `generate_nanobot_rules()` produces recommended `NANOBOT.md` content
- `/api/workspace/scan` endpoint
- TUI welcome banner shows detected stack

### P8: Engineering & Type Safety
- `tools/tool_schema.py` — Pydantic `ToolDef`/`ToolParam`/`ToolRegistry` models
- `ToolDef.to_openai_schema()` / `from_openai_schema()` for typed tool definitions
- `wrap_existing_tools()` bridge for gradual migration
- `mypy.ini` configuration targeting core modules (0 errors in P5-P8 modules)
- mypy 1.20.2 installed as dev dependency

### P9a: server_final.py Modular Extraction — Phase 1
- `config_manager.py` (200 行) — 配置加载/保存/热重载
- `session_persistence.py` (138 行) — 会话持久化存储
- `ssh_remote.py` (140 行) — SSH 远程连接管理
- `model_discovery.py` (292 行) — Ollama 模型发现与管理
- server_final.py: ~49,000 → ~48,379 行
- 69 new tests across 4 test files

### P9b: server_final.py Modular Extraction — Phase 2
- `task_scheduler_core.py` (105 行) — 定时任务调度核心
- `snn_init.py` (188 行) — SNN-LLM 初始化与全局状态管理
- `batch_operations.py` (255 行) — 批量删除/预热/执行 + 会话/日志/统计导出
- server_final.py: 48,379 → 47,980 行
- 58 new tests across 3 test files
- All `global SNN_*` replaced with module-level state via `import snn_init as _snn_mod`

### P11: Integration Tests for server_final.py
- `tests/test_server_integration.py` — 58 integration tests using Starlette TestClient
- Covers: sessions (10), config (8), export/batch (10), system/health (8), SNN (6), SSE/audit/cache/security (16)
- Bug fix: added missing `Response` import in `server_final.py` (CSV export was broken)
- Bug fix: `BACKUP_DIR` → `BACKUP_MANAGER.backup_dir` in batch backup delete endpoint

### P15: Service Layer Extraction
- Created `services/` package with 4 service modules (716 lines total)
- `system_service.py` (173 lines) — `/api/status`, `/api/version`, `/api/system/status`
- `repair_service.py` (120 lines) — `/api/fix/*` endpoints
- `health_service.py` (112 lines) — `/api/performance/*`, `/api/errors/*`
- `scheduler_service.py` (305 lines) — `/api/tasks/*` endpoints, task description heuristics
- Pure functions with dependency injection (no direct FastAPI coupling)
- 63 new service unit tests (system: 11, repair: 13, health: 9, scheduler: 30)
- server_final.py: 47,980 → 47,425 lines (−555)

### Infrastructure
- `task_store.py` — session-scoped task tree with state machine
- `system_prompts.py` — 9-section structured system prompt (P12-P14, P101a-e)
- `skills/` — bundled + user-defined skills with conditional activation
- 1,359 total tests across 30 test suites, 0 errors, 0 failures
- 14 pre-existing test errors fixed (asyncio 3.12, import, SystemExit)
