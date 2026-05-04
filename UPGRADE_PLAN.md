# Nanobot 升级计划：对标 Claw 架构深度分析

> 基于 `/home/field/claw`（Claw/Claude Code 源码）与 `/home/field/.nanobot`（Nanobot）的逐模块对比分析

---

## 一、架构对比总览

| 维度 | Claw (TypeScript) | Nanobot (Python) | 差距 |
|------|-------------------|-------------------|------|
| **工具数量** | 42+ (含 MCP 工具) | 11 | ★★★ |
| **Sub-Agent 类型** | 5 内置 (explore/verify/plan/general/guide) + 自定义 | 1 通用 sub_agent | ★★★ |
| **上下文压缩** | 多层级 (MicroCompact + AutoCompact + CachedMC + File State) | MicroCompact + AutoCompact + P25 | ★★ |
| **System Prompt** | 动态分段 + Global/Session 缓存分界 + Section Builder | 静态+动态分离 (P21) | ★ |
| **权限系统** | 3 层 (Permission Mode + Sandbox + Hooks) | 2 层 (secure_interceptor + shell safety) | ★★ |
| **记忆系统** | MEMORY.md + Auto-Memory + Team Memory | NANOBOT.md (仅读取) | ★★★ |
| **任务管理** | TodoWriteTool + TaskCreate/Get/Update/List/Stop | 无 | ★★★ |
| **代码智能** | LSP Tool (goToDefinition, findReferences, hover...) | 无 | ★★★ |
| **技能系统** | /skills 目录 + SkillTool + /commit /simplify /verify | 无 | ★★ |
| **Git 操作** | 完整 commit/PR/branch 工作流 | shell_execute 裸调 | ★★ |
| **并行执行** | 独立工具并行 + 后台 Agent | READONLY_TOOLS 并行 | ★ |
| **验证机制** | Verification Agent (adversarial) | 无 | ★★★ |
| **Worktree 隔离** | git worktree 隔离执行 | 无 | ★★ |
| **MCP 协议** | 完整 MCP Client 支持 | 无 | ★★ |

---

## 二、高优先级改进项（P60-P70）

### P60: 专用 Agent 类型系统 ★★★

**现状**: Nanobot 的 `sub_agent` 只有一种通用类型，prompt 写什么就做什么。

**Claw 做法**: 5 种内置 Agent，每种有专用 system prompt、工具白名单/黑名单、独立模型选择。

**改进方案**:
```python
# tools/sub_agent.py 新增 agent_type 参数
BUILT_IN_AGENTS = {
    "explore": {
        "system_prompt": EXPLORE_SYSTEM_PROMPT,
        "allowed_tools": ["file_read", "grep_search", "find_by_name", "file_list", "shell_execute"],
        "disallowed_tools": ["file_write", "file_edit", "sub_agent"],
        "description": "快速代码库搜索探索，只读模式",
    },
    "verify": {
        "system_prompt": VERIFY_SYSTEM_PROMPT,
        "allowed_tools": ["file_read", "grep_search", "shell_execute", "python_execute"],
        "disallowed_tools": ["file_write", "file_edit", "sub_agent"],
        "description": "对抗性验证：尝试破坏实现，不修改项目文件",
    },
    "plan": {
        "system_prompt": PLAN_SYSTEM_PROMPT,
        "allowed_tools": ["file_read", "grep_search", "find_by_name", "file_list"],
        "disallowed_tools": ["file_write", "file_edit", "shell_execute"],
        "description": "分析任务、制定执行计划",
    },
}
```

**关键 Claw 源码参考**:
- `src/tools/AgentTool/built-in/verificationAgent.ts` — 对抗性验证 prompt
- `src/tools/AgentTool/built-in/exploreAgent.ts` — 只读搜索 agent
- `src/tools/AgentTool/prompt.ts` — "Writing the prompt" 指南

**实现文件**: `tools/sub_agent.py`, `system_prompts.py`

---

### P61: 验证 Agent (Verification) ★★★

**现状**: Nanobot 没有任何自动验证机制。模型写完代码后可以声称"完成"而不运行任何验证。

**Claw 做法**: `verificationAgent.ts` 有完整的对抗性验证 prompt：
- "Your job is not to confirm the implementation works — it's to try to break it."
- 必须运行命令并贴出输出（不能只看代码）
- 必须包含至少一个对抗性探测（并发、边界值、幂等性）
- 最终输出 `VERDICT: PASS/FAIL/PARTIAL`

**改进方案**:
- 在 `agentic_loop.py` 的主循环中，当检测到 3+ 文件编辑后自动触发验证
- 验证 agent 继承主 agent 上下文，但禁止写文件
- system prompt 直接翻译 Claw 的验证 prompt（含 "RECOGNIZE YOUR OWN RATIONALIZATIONS" 段）

**关键 Claw 源码**: `src/tools/AgentTool/built-in/verificationAgent.ts:10-129`

---

### P62: 任务管理工具 (TodoWriteTool) ★★★

**现状**: Nanobot 没有任务跟踪。复杂任务时模型容易丢步骤。

**Claw 做法**: `TodoWriteTool` + `TaskCreate/Get/Update/List/Stop` 完整任务系统：
- 3+ 步骤任务主动创建 todo list
- 每次只有 1 个 in_progress 任务
- 完成后立即标记 completed
- 前端实时展示进度

**改进方案**:
```python
# tools/todo_manage.py
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "todo_manage",
        "description": "创建和管理当前任务的 todo list，跟踪多步骤任务进度",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "update", "list"]},
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "content": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                        }
                    }
                }
            }
        }
    }
}
```

**前端**: 在 `app.js` 中添加 todo 面板渲染（SSE event type: `todo_update`）

---

### P63: 持久记忆系统 (Memory) ★★★

**现状**: Nanobot 只能读 `NANOBOT.md`，无法写入或自动积累记忆。

**Claw 做法**: `memdir/` 系统：
- `MEMORY.md` 入口文件（最多 200 行）
- Auto-Memory: 自动从对话中提取项目约定、用户偏好
- 按类型分类（style_guide, tool_config, project_convention, user_preference）
- 每次会话开始注入相关记忆

**改进方案**:
```python
# memory_manager.py
class MemoryManager:
    MEMORY_FILE = WORKSPACE / "MEMORY.md"
    MAX_LINES = 200

    def load(self) -> str:
        """加载记忆内容注入 system prompt"""

    def save(self, category: str, content: str):
        """追加记忆条目"""

    def auto_extract(self, conversation_history: list) -> list:
        """从对话中提取值得记忆的内容"""
```

新增 `memory_write` 工具：允许模型主动保存发现的项目约定。

---

### P64: LSP 工具集成 ★★★

**现状**: Nanobot 查找定义/引用只能用 `grep_search`，容易误匹配。

**Claw 做法**: `LSPTool` 支持 9 种操作：
- goToDefinition / findReferences / hover
- documentSymbol / workspaceSymbol
- goToImplementation / prepareCallHierarchy / incomingCalls / outgoingCalls

**改进方案**:
```python
# tools/lsp.py — 通过 pylsp / typescript-language-server 实现
TOOL_DEF = {
    "function": {
        "name": "lsp_query",
        "parameters": {
            "properties": {
                "operation": {"enum": ["definition", "references", "hover", "symbols", "implementations"]},
                "file_path": {"type": "string"},
                "line": {"type": "integer"},
                "character": {"type": "integer"},
            }
        }
    }
}
```

利用 Python 的 `pygls` 库作为 LSP client，连接 workspace 中已安装的语言服务器。

---

## 三、中优先级改进项（P70-P80）

### P70: Skill 系统 ★★

**Claw 做法**: `skills/bundled/` 目录含 `/commit`, `/simplify`, `/verify`, `/debug` 等内置技能，用户可自定义。

**改进方案**:
- 在 workspace 中支持 `.nanobot/skills/` 目录
- 每个 skill 是一个 `.md` 文件（类似 Claw 的 workflow）
- 内置 `/commit`（Git 提交工作流）、`/simplify`（代码审查）
- skill 可组合 Agent（如 `/simplify` 启动 3 个并行审查 agent）

### P71: 强化 Git 工作流 ★★

**Claw 做法**: Bash prompt 中有完整的 Git 安全协议 + commit/PR 详细步骤：
- "NEVER run destructive git commands unless explicitly requested"
- "NEVER skip hooks (--no-verify)"
- "ALWAYS pass commit message via HEREDOC"
- "Create NEW commits rather than amending"

**改进方案**: 在 `system_prompts.py` 的 `_SYSTEM_PROMPT_ACTIONS` 中添加 Git 安全规则段。

### P72: Sandbox 命令执行 ★★

**Claw 做法**: `BashTool` 支持 filesystem/network 沙箱，限制读写路径和网络访问。

**改进方案**: `shell_execute.py` 增加 workspace 限制模式：
- 只允许写入 workspace 目录和 /tmp
- 可选的 network 限制（通过 unshare 或 firejail）

### P73: 后台命令执行 ★★

**Claw 做法**: `run_in_background` 参数 — 命令在后台运行，完成后通知。

**改进方案**: `shell_execute.py` 增加 `background=true` 参数：
- 命令在 asyncio subprocess 中后台运行
- 完成后通过 SSE 推送结果
- 模型可继续处理其他任务

### P74: 上下文智能压缩升级 ★★

**Claw 做法**: `compact.ts` 多层压缩：
- File state cache（记住哪些文件已读过及其内容摘要）
- Tool search discovery（记住已发现的工具名）
- Plan preservation（压缩后保留计划文件路径）
- Cached MicroCompact（缓存已压缩的块，避免重复 LLM 调用）

**改进方案**: 增强 `compact_engine.py`:
- 压缩时保留最近 5 个文件的完整路径+行数+关键函数名（P25 升级版）
- 缓存已压缩的摘要，避免重复 LLM 调用
- 压缩后自动注入 "Files you've seen" 附件消息

### P75: Fork Agent (上下文继承) ★★

**Claw 做法**: `forkSubagent.ts` — Agent 可以 fork 自身，继承完整对话上下文：
- "Fork yourself when intermediate tool output isn't worth keeping in context"
- Fork 共享 prompt cache（高效）
- 完成后通知父 agent

**改进方案**: `sub_agent.py` 增加 `fork=true` 模式：
- 将当前 messageHistory 序列化传给子 agent
- 子 agent 结果通过 SSE `fork_complete` 事件返回
- 主 agent 可继续处理用户交互

---

## 四、低优先级但有价值的改进

### P80: MCP 协议支持
- 实现 MCP client，允许连接外部工具服务器
- 参考 `src/services/mcp/`

### P81: Plan Mode（规划模式）
- 参考 `EnterPlanModeTool` / `ExitPlanModeTool`
- 在规划模式下只允许读取工具，强制先规划再执行

### P82: AskUserQuestion 工具
- 当模型真正不确定时，结构化地向用户提问
- 提供选项让用户选择（类似 Claw 的 `AskUserQuestionTool`）

### P83: ToolSearch 工具
- 当工具太多时，模型可以搜索最相关的工具
- 参考 `src/tools/ToolSearchTool/`

### P84: 输出风格配置
- 参考 `src/constants/outputStyles.ts`
- 用户可选择 concise / detailed / code-only 等风格

---

## 五、实施路线图

### Phase 1（本周）— 核心能力
1. **P62 TodoWriteTool** — 立即可用，提升复杂任务质量
2. **P60 Agent 类型系统** — 改造 sub_agent.py 支持 agent_type
3. **P61 Verification Agent** — 翻译 Claw 的验证 prompt

### Phase 2（下周）— 工程质量
4. **P71 Git 安全协议** — 纯 prompt 改动
5. **P63 记忆系统** — MEMORY.md 读写 + 自动提取
6. **P73 后台命令** — shell_execute background 模式

### Phase 3（后续）— 高级特性
7. **P64 LSP 集成** — 需要安装语言服务器
8. **P70 Skill 系统** — .nanobot/skills/ 框架
9. **P72 Sandbox** — 安全隔离
10. **P74 上下文压缩升级** — 缓存 + 文件状态
11. **P75 Fork Agent** — 上下文继承

---

## 六、Claw 核心 Prompt 精华（可直接移植到 Nanobot）

### 1. "Never delegate understanding"
> Don't write "based on your findings, fix the bug." Write prompts that prove you understood: include file paths, line numbers, what specifically to change.

### 2. "Recognize your own rationalizations"
> - "The code looks correct based on my reading" — reading is not verification. Run it.
> - "The implementer's tests already pass" — the implementer is an LLM. Verify independently.
> - "This is probably fine" — probably is not verified. Run it.

### 3. "Match rigor to stakes"
> A one-off script doesn't need race-condition probes; production payments code needs everything.

### 4. "Don't add features beyond what was asked"
> A bug fix doesn't need surrounding code cleaned up. A simple feature doesn't need extra configurability. Three similar lines of code is better than a premature abstraction.

### 5. Agent prompt writing
> Brief the agent like a smart colleague who just walked into the room. Explain what you're trying to accomplish and why. Describe what you've already learned or ruled out.

### 6. "Report outcomes faithfully"
> Never claim "all tests pass" when output shows failures. Never suppress or simplify failing checks. Equally, don't hedge confirmed results with unnecessary disclaimers.
