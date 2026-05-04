# Nanobot 下阶段 Roadmap

**基准版本**: P0–P9b 完成 | **日期**: 2026-05-02  
**当前状态**: 1,359 tests ✅ | server_final.py 47,425 行 | 11 模块已提取 | P15 服务层提取已完成

---

## 优先级总览

| 优先级 | 方向 | 价值 | 复杂度 | 预计工期 |
|:------:|------|:----:|:------:|:--------:|
| 🥇 1 | CI/CD 管道建设 | 高 | 低 | 1–2 天 |
| ✅ 2 | 集成测试增强 (P11 已完成) | 高 | 中 | ~~3–5 天~~ 已交付 58 测试 |
| ✅ 3 | server_final.py 服务层提取 (P15 已完成) | 中 | 中 | ~~5–7 天~~ 已交付 4 服务模块 |
| 4 | 前端模块化重构 | 高 | 高 | 1–2 周 |
| 5 | MCP SSE/HTTP Transport | 中 | 中 | 3–5 天 |
| 6 | 多模态支持（图片理解） | 中 | 中 | 3–5 天 |

---

## 详细说明

### 🥇 P10: CI/CD 管道建设

| 维度 | 评估 |
|------|------|
| **价值** | 高 — 每次提交自动验证 1,238 测试 + mypy，杜绝回归 |
| **复杂度** | 低 — 工具链已就绪（unittest + mypy 1.20.2），仅需配置文件 |
| **工期** | 1–2 天 |

**内容**：
- GitHub Actions 或 GitLab CI 配置文件
- 两阶段流水线：`test`（unittest discover）→ `typecheck`（mypy 核心模块）
- 可选：覆盖率报告（coverage.py + badge）
- 可选：PR 自动检查 + 阻止合并

**收益**：将当前手动运行的验证流程自动化，开发者提交即得反馈。

---

### 🥈 P11: 集成测试增强

| 维度 | 评估 |
|------|------|
| **价值** | 高 — 当前最大覆盖缺口是 server_final.py 47K 行无专属测试 |
| **复杂度** | 中 — 需要 HTTPX AsyncClient 模拟 FastAPI 端点 |
| **工期** | 3–5 天 |

**内容**：
- 使用 `httpx.AsyncClient` + FastAPI `TestClient` 编写端到端测试
- 覆盖核心 API 路径：
  - SSE 流聊天 `/api/chat`
  - 会话 CRUD `/api/sessions/*`
  - 配置管理 `/api/config/*`
  - 导出/批量操作 `/api/export/*`、`/api/batch/*`
  - SNN 端点 `/api/snn/*`
- 目标：50+ 个集成测试，覆盖 server_final.py 主要端点

**收益**：消除项目最大风险点——48K 行无直接测试覆盖。

---

### 🥉 P12: server_final.py 路由层拆分

| 维度 | 评估 |
|------|------|
| **价值** | 中 — IDE 性能改善，团队协作友好 |
| **复杂度** | 中 — FastAPI APIRouter 机制成熟，但端点数量大 |
| **工期** | 5–7 天 |

**内容**：
- 创建 `routes/` 目录，按功能域拆分：
  - `routes/chat.py` — SSE 聊天端点
  - `routes/sessions.py` — 会话管理
  - `routes/config.py` — 配置/系统
  - `routes/export.py` — 导出/批量操作
  - `routes/snn.py` — SNN 相关端点
  - `routes/tools.py` — 工具/MCP 管理
- 抽取中间件到 `middleware/` 目录
- 目标：server_final.py 降至 30K 行以下

**收益**：单文件从 48K → <30K，每个路由文件独立可测。

---

### P13: 前端模块化重构

| 维度 | 评估 |
|------|------|
| **价值** | 高 — 前端 6.3K 行 HTML 含内联 JS/CSS，难以维护 |
| **复杂度** | 高 — 需要构建工具引入或大规模重构 |
| **工期** | 1–2 周 |

**内容**：
- **轻量方案**：ES Module 拆分（无构建工具）
  - `static/js/chat.js` — 聊天核心
  - `static/js/ui.js` — UI 交互
  - `static/js/sse.js` — SSE 连接管理
  - `static/js/config.js` — 配置常量
- **进阶方案**：Preact/Svelte + Vite
  - 组件化（ChatPanel, ToolCard, TaskTree, StatsModal）
  - HMR 热更新开发体验
- 独立 CSS 文件提取

**收益**：前端可维护性从「不可碰」升级为「可迭代」。

---

### P14: MCP SSE/HTTP Transport

| 维度 | 评估 |
|------|------|
| **价值** | 中 — 扩展 MCP 生态兼容性，对接更多外部工具 |
| **复杂度** | 中 — MCP 规范已定义 SSE transport，需扩展 mcp_client.py |
| **工期** | 3–5 天 |

**内容**：
- `tools/mcp_client.py` 新增 SSE transport 支持（当前仅 stdio）
- HTTP transport 适配远程 MCP 服务器
- 自动探测 transport 类型
- 连接池与重试策略

**收益**：支持云端 MCP 服务器（如 Cloudflare Workers MCP）。

---

### P15: 多模态支持

| 维度 | 评估 |
|------|------|
| **价值** | 中 — 支持图片理解，拓宽使用场景 |
| **复杂度** | 中 — LLM 侧已支持 vision，需打通上传通道 |
| **工期** | 3–5 天 |

**内容**：
- Web UI 图片上传组件（drag & drop + paste）
- `agentic_loop.py` 构建 multimodal message（base64 / URL）
- TUI 支持 `/image <path>` 命令
- 模型能力检测（自动判断是否支持 vision）

**收益**：从纯文本 Agent 升级为多模态 Agent。

---

## 依赖关系

```
P10 (CI/CD) ─── 无依赖，可立即开始
     │
     ▼
P11 (集成测试) ─── 受益于 CI 自动运行
     │
     ▼
P12 (路由拆分) ─── 有集成测试保障后更安全
     │
     ├──→ P13 (前端重构) ─── 可并行，独立于后端
     │
     ├──→ P14 (MCP SSE) ─── 可并行
     │
     └──→ P15 (多模态) ─── 可并行
```

---

## 里程碑规划

| 里程碑 | 包含 | 预计完成 | 关键产出 |
|--------|------|:--------:|----------|
| M1: 工程基础 | P10 + P11 | 1 周 | CI 管道 + 50 集成测试 |
| M2: 架构优化 | P12 + P13 | 2 周 | 路由拆分 + 前端模块化 |
| M3: 能力拓展 | P14 + P15 | 1 周 | MCP SSE + 多模态 |

---

*基于 P0–P9b 完成后的项目状态生成。详细指标见 [ITERATION_SUMMARY.md](ITERATION_SUMMARY.md)。*

---

## `_current_task_text` 解耦路线图 (audit-mandated, 2026-05-03)

> **背景**: CTO 审计要求将 `_current_task_text` 中心化变量的剩余依赖按难度排序，
> 分批次解耦。POC 已完成（`_run_preflight_detections`），模式已固化到
> [ONBOARDING.md §10](ONBOARDING.md#10-decoupling-global-variable-dependencies-team-norm)。

### 已完成 ✅

| 批次 | 依赖点 | 引用数 | 方式 |
|:----:|--------|:------:|------|
| POC | P7, P102, P104, P104-fork, P38 | 5→1 | `_run_preflight_detections()` |
| Easy-1a | P92 | 1→1 | `_get_memory_trigger()` — single-purpose, memory domain |
| Easy-1b | P98c | 2→1 | `_match_skill_intent()` — single-purpose, skill domain |
| Hard-1 | P27/B15/B16 | 4→1 | `_apply_file_read_corrections(tool_calls, task_text, workspace)` — file_read 域 |

### 第一批（剩余）：简单 — 单次调用，可直接包装 (预计 1–2 天)

| 依赖点 | 当前引用行 | 说明 | 建议 |
|--------|:----------:|------|------|
| D4 `_extract_user_target_files` | ~3065 | 单次调用 | 并入 pre-flight 或新建 `_run_d4_extraction(task_text)` |
| P104-route `_build_repo_fact_completion_gate` | ~4375 | 第三参数 | 已是显式参数，仅需从 bundled 结果获取 |
| P36 `_build_completeness_nudge` | ~5617 | 第一参数 | 已是显式参数，仅需确认传参方式 |

### 第二批：中等 — 多引用或含状态逻辑 (预计 2–3 天)

| 依赖点 | 当前引用行 | 说明 | 建议 |
|--------|:----------:|------|------|
| P33 numbered_tasks | ~3035–3045 | 3 个 `re.findall` 调用 | 提取为 `_parse_numbered_tasks(task_text)` |
| U11d @explore lock | ~3699–3700 | 2 引用 + 状态赋值 | 提取为 `_check_explore_lock(task_text)` → `(bool, str)` |
| B1 find_by_name 转换 | ~4981 | `pattern in _current_task_text` | 改为参数传入 |
| P104-fork agent | ~3766–3772 | 3 引用（log/yield/run） | 提取为 `_run_explore_agent(task_text, ...)` |

### 第三批：困难 — ✅ 已完成

P27/B15/B16 已解耦为 `_apply_file_read_corrections(tool_calls, task_text, workspace)`。
发现的 P27 fallback 交叉污染 bug 已登记到 backlog（`_fix_file_read_limits_fallback`，deadline 2026-07-15）。

#### P27/B15/B16 技术预研（审计要求 2026-05-03）

**现状分析**：4 个 `_current_task_text` 引用集中在 `agentic_loop.py` ~4170-4181，
位于主循环的 `tool_calls` 事件处理分支内。它们的作用：

```
L4174: _has_line_request = _LINE_COUNT_RE.search(_current_task_text)   # 检测用户是否指定行数
L4175: _has_full_file = _is_full_file_request(_current_task_text)       # 检测用户是否要求全文
L4177: _fix_file_read_limits(tool_calls_result, _current_task_text)     # 注入 limit/offset
L4180: _fix_full_file_reads(tool_calls_result, _current_task_text)      # 注入全文 limit
```

**难点**：
1. 这些调用嵌套在 `async for event in stream:` 循环的 `elif evt_type == "tool_calls":`
   分支内，不是简单的前置调用——它们在**每个 LLM turn** 的工具调用事件中触发。
2. `_fix_file_read_limits` 和 `_fix_full_file_reads` 已经接受 `user_message: str`
   作为显式参数（函数签名已解耦），问题仅在于调用点仍传入闭包变量。
3. `_is_full_file_request` 在 ~3129 处还有一个单独的消费点（P33 预规划），
   意味着解耦需同时覆盖两个调用点。

**解耦路线（推荐方案）**：
创建 `_apply_file_read_corrections(tool_calls, task_text)` 函数，封装上述 4 行逻辑：
```python
def _apply_file_read_corrections(tool_calls: list, task_text: str) -> list:
    has_line = _LINE_COUNT_RE.search(task_text)
    has_full = _is_full_file_request(task_text)
    if has_line:
        _fix_file_read_limits(tool_calls, task_text)
    if has_full:
        _fix_full_file_reads(tool_calls, task_text)
        tool_calls = _shard_full_file_reads(tool_calls, workspace)
    return tool_calls
```
**问题**：`_shard_full_file_reads` 还需要 `workspace` 参数，因此函数签名须为
`_apply_file_read_corrections(tool_calls, task_text, workspace)`。

**替代方案**：
不创建新函数，仅在主循环入口处将 `_current_task_text` 赋值给一个作用域更窄的
局部变量 `_task_for_file_corrections = _current_task_text`，但这不减少依赖，仅增加
可读性，不推荐。

**风险评估**：
- 安全影响：这些函数控制 file_read 的 limit/offset 参数，错误的重构可能导致
  模型读取过多/过少内容。需要完整的 P27/B15/B16 回归测试覆盖。
- 测试覆盖：`test_small_model_behavior.py` 和 `test_p21_to_p26.py` 已有 P27 和 B15 测试。
  解耦后需确保所有现有测试不回归。

**安全测试策略（审计要求 2026-05-03）**：
安全负面测试已添加至 `test_audit_fixes.py`（`p27_security_*` 系列），验证：
1. 无行数请求时 file_read 不被注入 limit（防误注入）
2. 用户指定 limit=50 时不被篡改为更大值（防越权读取）
3. 多文件请求中限制只应用于匹配的文件（防交叉污染）
4. 非 file_read 工具（如 grep_search）的参数不受 limit 注入影响（防误伤）
这些测试在解耦前后都必须通过，作为 P27/B15/B16 重构的安全门禁。

**阻塞项解决（2026-05-03）**：
审查确认 ~3129 处 `_is_full_file_request(t)` 的参数 `t` 是**局部变量**
（`for num, t in numbered_tasks:` 循环中的任务文本），**并非** `_current_task_text`。
因此 P33 消费点与 P27/B15/B16 解耦完全独立，无需同步处理。
阻塞项已清除，推荐方案可直接执行。

**结论**：推荐方案可行，预计 1-2 天实现。阻塞项已清除。

### 选取原则

1. **域内聚合** — 只有同一业务域的消费者才能捆绑为一个函数（如 pre-flight 检测）
2. **独立消费者用单一用途函数** — 跨域消费者各自独立包装（如 P92 记忆 ≠ P98c 技能）
3. **已显式参数优先** — 函数已接受 `task_text` 参数的（P36、P104-route）最先处理
4. **安全相关最后** — P27/B15/B16 涉及文件读取限制，解耦需配套安全回归测试

### 验收标准

- 每批完成后运行 `python3 tests/test_audit_fixes.py`，依赖同步测试必须通过
- 完成后将符号从 `_BACKLOG` 移至 `_DECOUPLED`（在 `test_audit_fixes.py` 中）
- 遵循 ONBOARDING.md §10 中的团队规范（域内聚合，非计数驱动）
- 过期的 backlog 条目将导致 CI 失败，需及时解耦或延期
