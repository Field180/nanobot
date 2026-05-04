# Nanobot 项目状态报告

**日期**: 2026-05-02  
**版本**: P0-P17 迭代完成后（含三轮安全审计修复）  
**Python**: 3.12 | **Pydantic**: 2.12.5 | **mypy**: 1.20.2

---

## 1. 项目规模统计

### 总览

| 指标 | 数值 |
|------|------|
| Python 文件数 | 155 |
| Python 总代码行数 | 124,119 |
| JavaScript/HTML/CSS 行数 | 27,227 |
| 测试方法总数 | 1,468 |
| 测试套件数 | 42 |

### 核心模块行数

| 文件 | 行数 | 说明 |
|------|-----:|------|
| server_final.py | 47,425 | HTTP 服务器（FastAPI），SSE 流，API 端点 |
| agentic_loop.py | 5,463 | 核心 agentic 循环，LLM 交互，工具执行 |
| skills/__init__.py | 1,107 | Skill 注册/执行/条件激活 |
| system_prompts.py | 914 | 9 段式系统提示词构建 |
| tui.py | 857 | 终端 TUI (Rich + prompt_toolkit) |
| task_store.py | 766 | 会话级任务树 + 状态机 |
| project_detector.py | 543 | 工作区技术栈自动检测 |
| skills/bundled.py | 520 | 内置 skill 定义 |
| skills/coding_skills.py | 444 | 编码辅助 skills |
| session_stats.py | 205 | 会话统计聚合 |
| task_scheduler_core.py | 105 | 定时任务调度核心（P9b 提取） |
| snn_init.py | 188 | SNN-LLM 初始化/全局状态（P9b 提取） |
| batch_operations.py | 255 | 批量删除/预热/执行/导出操作（P9b 提取） |

### 工具模块行数

| 文件 | 行数 | 只读 |
|------|-----:|:----:|
| tools/sub_agent.py | 1,415 | ✅ |
| tools/code_intel.py | 1,024 | ✅ |
| tools/mcp_client.py | 1,087 | — |
| tools/__init__.py | 492 | — |
| tools/file_read.py | 431 | ✅ |
| tools/base.py | 294 | — |
| tools/file_edit.py | 266 | — |
| tools/todo_manage.py | 261 | ✅ |
| tools/tool_schema.py | 213 | — |
| tools/memory.py | 217 | — |
| tools/web_fetch.py | 204 | ✅ |
| tools/web_search.py | 161 | ✅ |
| 其余工具 | ~500 | — |

---

## 2. 测试覆盖分析

### 测试套件汇总（按用例数排序）

| 排名 | 测试套件 | 用例数 | 行数 | 覆盖范围 |
|------|----------|-------:|-----:|----------|
| 1 | test_skills | 451 | 4,605 | Skills 注册/执行/条件/fork/allowed tools |
| 2 | test_small_model_behavior | 114 | 1,144 | 小模型纠正：叙述/探索/快路径/回归 |
| 3 | test_memory | 97 | 874 | 持久记忆 CRUD/相似度/标签 |
| 4 | test_u12_integration_v2 | 48 | 1,013 | U12 集成场景 v2 |
| 5 | test_code_intel | 44 | 478 | 代码智能：符号提取/引用/分析 |
| 6 | test_mcp | 62 | 772 | MCP 客户端/连接/资源/工具映射/安全过滤 |
| 7 | test_swarm | 41 | 848 | 多 Agent 并行/消息邮箱 |
| 8 | test_u12_integration | 41 | 645 | U12 集成场景 v1 |
| 9 | test_tui | 39 | 308 | TUI 渲染器/命令/历史/横幅 |
| 10 | test_approval_recovery | 37 | 771 | Change set 审批/恢复流程 |
| 11 | test_p60_p62_p71 | 37 | 500 | 高级提示词/上下文特性 |
| 12 | test_project_detector | 31 | 340 | 项目检测 + 规则生成 |
| 13 | test_tool_schema | 30 | 278 | Pydantic 工具 Schema |
| 14 | test_session_stats | 19 | 238 | 会话统计聚合 |
| 15 | test_task_integration | 13 | 558 | 任务存储集成 |
| 16 | test_repo_explore_fork | 13 | 213 | 仓库探索 fork |
| 17 | test_frontend_contract | 9 | 179 | 前端 SSE 约定 |
| 18 | test_final_answer_ui_contract | 5 | 46 | 最终回答 UI 约定 |
| 19 | test_task_scheduler_core | 10 | 140 | 定时任务调度模块（P9b） |
| 20 | test_snn_init | 18 | 149 | SNN 初始化模块（P9b） |
| 21 | test_batch_operations | 30 | 301 | 批量操作+导出模块（P9b） |

### 测试薄弱区域

| 模块 | 行数 | 专属测试 | 风险评估 |
|------|-----:|:--------:|----------|
| **server_final.py** | 47,980 | ✅ 58 个集成测试 (P11) | 中 — 核心 API 端点已覆盖 |
| **agentic_loop.py** | 5,449 | ❌ 无 | ⚠️ 中 — 通过 test_p44/p60/p21/small_model 间接覆盖 |
| **system_prompts.py** | 911 | ❌ 无 | 低 — 通过 test_p44/p101 间接覆盖 |
| **task_store.py** | 766 | ❌ 无 | 低 — 通过 test_task_integration 覆盖 |
| **approval_handler.py** | — | ❌ 无 | 低 — 通过 test_approval_recovery 覆盖 |

> 注：虽然没有专属测试文件，但 agentic_loop/system_prompts 的功能通过 22+ 个测试文件间接验证。server_final.py 是最大的覆盖缺口。

---

## 3. 遗留问题清单

### ✅ 已全部修复（原 14 个错误 → 0 个）

| 类别 | 数量 | 文件 | 原因 | 修复方式 |
|------|-----:|------|------|----------|
| ~~事件循环冲突~~ | 11 | test_mcp.py | `asyncio.get_event_loop()` 在 Python 3.12 已弃用 | ✅ 替换为 `asyncio.run()` |
| ~~导入失败~~ | 1 | test_p44_to_p51.py | `_recent_tool_calls.append(call_sig)` 子串已变为 `_b9_norm_sig` | ✅ 更新断言子串 |
| ~~导入失败~~ | 1 | test_file_edit_and_concurrency.py | 模块级 `asyncio.get_event_loop()` 在 3.12 失败 | ✅ 替换为 `asyncio.run()` + `get_running_loop()` |
| ~~SystemExit~~ | 1 | test_p44_to_p51.py | 模块级 `sys.exit(0)` 导致 discover 报错 | ✅ 加 `__main__` 守卫 |

**当前状态**: `python3 -m unittest discover` → **1,468 tests, 0 errors, 0 failures**

**本地 CI**: `make test` 一键运行全量测试，无需远程仓库

---

## 4. 安全审计修复（P16-P17，2026-05-02）

### 三轮安全修复汇总

| 轮次 | 修复项 | 严重等级 | 文件 |
|------|--------|:--------:|------|
| 1 | MCP 工具名正则校验 `_SAFE_NAME_RE` | 高 | tools/mcp_client.py |
| 2 | MCP description 截断+换行转义 `_sanitize_description()` | 高 | tools/mcp_client.py |
| 2 | `max_total_tool_calls` 硬上限（默认 80） | 低 | agentic_loop.py |
| 3 | MCP inputSchema 参数描述过滤 | 高 | tools/mcp_client.py |
| 3 | 硬上限触发引导消息 `[TOOL LIMIT REACHED]` | 低 | agentic_loop.py |

### P17: 解析管道集成测试（Parser Pipeline Integration Tests）

> ⚠️ **范围限定**：本测试验证 `_stream_one_turn` 的**解析管道**，而非 LLM 行为或提示词效果。
> - ✅ 覆盖：SSE 分片解析、工具调用 delta 累积、畸形 JSON 兜底、LaTeX 过滤
> - ❌ 不覆盖：真实 LLM 推理质量、提示词变更影响、模型行为退化
> - 详见 `tests/test_replay_e2e.py` 模块级文档字符串和 `SECURITY.md` R6 风险说明

- `tests/fixtures/sample_conversation.json` — 最小对话录制 fixture
- `tests/test_replay_e2e.py` — 6 个解析管道测试（工具调用 + 工具错误 + 纯文本 + split delta 重组 + 畸形 JSON 容错 + 多工具并发交错）+ 3 个文档一致性测试
- `tests/fixtures/tool_error_conversation.json` — 工具执行失败场景 fixture
- Mock 层：`openai.AsyncOpenAI`（HTTP 传输层），返回真实 `ChatCompletionChunk` 对象
- 真实执行：`_stream_one_turn`（SSE 解析、工具 delta 累积、LaTeX 缓冲、特殊 token 清理）、工具调度、结果注入

### 已知安全风险与缓解状态

| 风险 | 严重等级 | 缓解状态 | 说明 |
|------|:--------:|:--------:|------|
| MCP 提示词注入（工具名/描述/参数） | 高 | ✅ 已缓解 | `_SAFE_NAME_RE` + `_sanitize_description()` + inputSchema 参数过滤 |
| MCP 参数描述注入面未完全封闭 | 中 | ⚠️ 部分缓解 | inputSchema 的 `description` 字段已过滤，但 `enum`/`default` 值中的注入向量未处理。影响：低（需 MCP 服务器合谋） |
| 工具调用无限循环 | 中 | ✅ 已缓解 | `max_total_tool_calls` 硬上限 + 引导消息 |
| 全局状态耦合（session leak） | 中 | ⚠️ 部分缓解 | `_reset_session_*` 在会话开始时重置，但 `_SESSION_FILE_READS` / `_SESSION_FAILURES` 为模块级全局变量，无跨进程隔离。影响：同进程多会话并发时状态串扰 |
| _stream_one_turn 异常输入容错 | 中 | ✅ 已缓解 | JSON 解析失败时 `_parsed_args={}` 兜底；录播测试已覆盖畸形 JSON 场景 |
| 提示词退化检测 | 中 | ⚠️ 未覆盖 | 录播测试覆盖了解析管道（split delta、LaTeX 缓冲），但 LLM 行为退化需真实模型录制 fixture。计划：首次部署时录制一次完整 chunk 流 |
| CI 管道缺失 | 高 | ⚠️ 部分缓解 | `Makefile` 提供 `make test/check` 本地一键门禁 + `make install-hooks` 配置 pre-commit hook。远程 CI 待建仓后补充 |

> **风险表维护流程**：每轮外部审计后强制更新本表。状态标记：✅ 已缓解 / ⚠️ 部分缓解 / ❌ 未缓解。

### 文档一致性自动化

- `TestDocumentationConsistency`（3 个测试）自动验证 PROJECT_STATUS_REPORT.md 中的关键指标
- 检查项：Python 文件数（±5）、总行数（±2000）、关键模块行数（±100）
- 容差设计：±100 允许正常重构（函数提取、注释增减），同时仍能捕获大规模未记录变更

---

## 5. 性能与架构风险

### 5.1 代码膨胀

| 风险 | 位置 | 严重性 | 说明 |
|------|------|:------:|------|
| **server_final.py 48K 行** | server_final.py | ⚠️ 高 | 单文件巨型模块（P9b 已提取 3 模块 ~548 行 + 导出函数），IDE 编辑/加载缓慢 |
| **agentic_loop.py 5.4K 行** | agentic_loop.py | 中 | 职责过多：流式 LLM、工具执行、纠正注入、预检、compact |
| **全局状态** | task_store.py, session_stats.py | 低 | 模块级缓存/单例，测试隔离依赖手动 reset |

### 5.2 耦合热点

- **server_final.py ↔ agentic_loop.py**: 通过 SSE 事件紧耦合，event 格式变更需两侧同步
- **agentic_loop.py ↔ system_prompts.py**: 系统提示词中引用了 agentic_loop 内部逻辑（纠正注入、B1-B9 行为修正）
- **tools/__init__.py ↔ 所有工具模块**: TOOL_DEF dict 协议是硬约定，工具 Schema (P8) 提供了类型安全桥接

### 5.3 性能观察

| 观察 | 影响 | 建议 |
|------|------|------|
| 每次请求构建完整系统提示词（911行源码 → ~8K chars） | 低 — 字符串拼接快 | 可缓存但收益有限 |
| MCP 工具动态注册每次 refresh 重建完整列表 | 低 | 已用 `refresh_mcp_tools()` 控制频率 |
| 工具执行无并发上限 | 中 | Claw 式 concurrent batch 已有 _partition_tool_calls |
| server_final.py 导入时间（49K行解析） | 中 | 启动时一次性成本，可接受 |

---

## 6. 与 Claw 的差距分析

| 能力 | Claw | Nanobot | 差距 | 价值 |
|------|:----:|:-------:|------|:----:|
| 多模型路由 | ✅ 自动选择 | ❌ 单模型 | 无动态 model 切换策略 | 高 |
| LSP 实时诊断 | ✅ 深度集成 | ⚠️ code_intel 基础 | 无实时编辑反馈 | 中 |
| 多模态（图片） | ✅ 截图理解 | ❌ | 无图片输入处理 | 中 |
| 插件市场 | ✅ 社区扩展 | ⚠️ skills/ 本地 | 无远程分享/安装 | 低 |
| Cron/定时任务 | ✅ 后台 schedule | ❌ | 无自动触发 | 低 |
| 语音 I/O | ✅ whisper+TTS | ❌ | 超出当前范围 | 低 |
| 文件监控 | ✅ fswatch | ⚠️ 基础 | 无增量 re-index | 中 |
| 权限粒度 | ✅ 细分 scope | ⚠️ P47 粗分 | 无 per-tool 权限 UI | 低 |
| Git 集成深度 | ✅ blame/diff/PR | ⚠️ shell | 无 native git 操作 | 中 |
| 会话持久化 | ✅ 跨重启 | ⚠️ task_store JSON | 无历史查询/搜索 | 中 |

---

## 7. 推荐路线图（未来 2-4 周）

### P9: server_final.py 拆分重构

| 维度 | 评估 |
|------|------|
| **用户价值** | 中 — 开发者体验改善，间接提升迭代速度 |
| **复杂度** | 高 |
| **工期** | 1 周 |
| **依赖** | 无 |

将 49K 行拆分为：`routes/`, `middleware/`, `sse_handler.py`, `file_ops.py` 等。使用 FastAPI router 机制。优先拆出 API 端点和 SSE 流处理。

**P9b 已完成模块提取**（Phase 2）：
- `task_scheduler_core.py` (105 行) — 定时任务调度
- `snn_init.py` (188 行) — SNN-LLM 初始化与全局状态
- `batch_operations.py` (255 行) — 批量删除/预热/执行 + 会话/日志/统计导出
- 新增 58 个专属单元测试，全部通过，零回归

---

### P10: 多模型路由与 Fallback

| 维度 | 评估 |
|------|------|
| **用户价值** | 高 — 成本优化、质量提升、可用性保障 |
| **复杂度** | 中 |
| **工期** | 3-5 天 |
| **依赖** | litellm 已支持多 provider |

实现 model routing layer：按任务复杂度/token 预算自动选模型。简单问答用小模型，复杂编码用大模型。Fallback 链：主模型超时 → 备用模型。

---

### P11: 会话持久化与历史搜索

| 维度 | 评估 |
|------|------|
| **用户价值** | 高 — 跨会话知识保留，"上次我们做了什么" |
| **复杂度** | 中 |
| **工期** | 3-4 天 |
| **依赖** | task_store.py 基础已有 |

扩展 task_store 为完整会话日志（消息 + 工具结果摘要）。SQLite 存储，支持全文搜索。TUI `/history search <keyword>` 命令。

---

### P12: 遗留测试修复 + 覆盖率提升

| 维度 | 评估 |
|------|------|
| **用户价值** | 中 — 质量保障 |
| **复杂度** | 低 |
| **工期** | 1-2 天 |
| **依赖** | 无 |

修复 14 个预存在错误（主要是 test_mcp 迁移到 IsolatedAsyncioTestCase）。为 server_final.py 添加 API 路由冒烟测试（使用 TestClient）。目标：0 errors。

---

### P13: 多模态图片理解

| 维度 | 评估 |
|------|------|
| **用户价值** | 高 — 截图/设计图/错误截图分析 |
| **复杂度** | 中 |
| **工期** | 3-4 天 |
| **依赖** | 模型需支持 vision（GPT-4o/Qwen-VL） |

添加图片上传通道（base64 或 URL），agentic_loop 构建 multimodal message。TUI 支持 `/image <path>` 命令。Web UI 已有上传基础设施。

---

## 8. 总结

```
项目健康度: ★★★★☆ (4/5)
- 功能完备度: 高（14 个工具 + MCP + Swarm + TUI + 检测 + 统计 + 类型安全）
- 测试覆盖: 高（1,468 测试，核心 API 集成测试 + P9b 模块专属测试 + MCP 安全测试 + 解析管道测试）
- 本地 CI: ✅ Makefile 提供 `make test/check`，pre-commit hook 支持
- 代码质量: 中（server_final.py 膨胀，无循环导入，P8 类型安全起步）
- 可维护性: 中（需拆分大文件，文档已补充）
- 性能: 良好（无明显瓶颈，Claw 式并发已实现）
```

**推荐优先级**: P10 (多模型路由) → P11 (持久化) → P9 (拆分)
