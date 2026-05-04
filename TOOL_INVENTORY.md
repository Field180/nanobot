# Nanobot 工具清单与权限映射

> 完整扫描所有涉及网络访问、文件操作、命令执行的工具入口点
> 最后更新: 2026-05-03
>
> **竞品工具数对比备注** (audit-mandated, 2026-05-03):
> Claw (Claude Code Local fork) 完整工具池约 50 个，但大量通过 feature flag DCE
> 禁用（KAIROS/PROACTIVE/COORDINATOR_MODE 等）。本地版默认启用约 19–21 个，
> 与 Nanobot 的 18 个基本持平。此前 "42+" 的估计包含了全部条件加载工具，
> 不反映本地运行时的实际可用数量。

---

## 📊 扫描统计

| 类别 | 匹配数 | 文件数 | 风险等级 |
|------|--------|--------|----------|
| 🌐 网络访问 | 8782 | 263 | 高 |
| 📁 文件操作 | 1416 | 327 | 中-高 |
| ⚙️ 命令执行 | 3059 | 176 | 高 |

---

## 1. 核心工具执行器 (`tool_executor.py`)

### 1.1 已集成权限审批的工具

| 工具名称 | 权限类型 | 风险等级 | 描述 |
|----------|----------|----------|------|
| `http_request` | `INTERNET` | 🔴 高 | 发送HTTP请求，访问网络资源 |
| `search_web` | `WEB_BROWSE` | 🟠 中 | 网络搜索（需配置API） |
| `file_read` | `FILE_READ` | 🟡 中 | 读取文件内容 |
| `file_write` | `FILE_WRITE` | 🔴 高 | 写入/创建文件 |
| `file_list` | `FILE_READ` | 🟢 低 | 列出目录内容 |
| `file_search` | `FILE_READ` | 🟡 中 | 搜索文件内容（grep功能） |
| `shell_execute` | `SYSTEM_CMD` | 🔴 高 | 执行Shell命令 |
| `code_execute` | `CODE_EXEC` | 🟠 中 | 执行Python代码 |
| `json_parse` | 无 | ⚪ 无 | 解析JSON（无风险） |
| `system_info` | 无 | ⚪ 无 | 获取系统信息（只读） |

### 1.2 工具参数详情

```python
# http_request
{
    'url': str,           # 目标URL
    'method': str,        # GET/POST/PUT/DELETE
    'headers': dict,      # 请求头
    'body': str,          # 请求体
    'timeout': int        # 超时秒数
}

# file_read / file_write
{
    'path': str,          # 文件路径
    'content': str,       # 内容（写入时）
    'mode': str,          # w/a（写入模式）
    'encoding': str       # 编码
}

# shell_execute
{
    'command': str,       # Shell命令
    'timeout': int        # 超时秒数
}

# code_execute
{
    'code': str,          # Python代码
    'globals': dict       # 全局变量
}
```

---

## 2. 安全工具执行器 (`secure_tool_executor.py`)

### 2.1 额外安全检查

| 检查项 | 描述 | 触发条件 |
|--------|------|----------|
| 信任等级 | 用户信任等级验证 | 0-5级 |
| 敏感路径 | 禁止读取敏感文件 | `.ssh/`, `.env`, `credentials.json` |
| 危险命令 | 阻止危险Shell命令 | `rm -rf`, `mkfs`, `curl | bash` |
| 输出审计 | 脱敏API密钥等敏感信息 | `sk-*`, `nvapi-*`, `Bearer *` |
| XSS防护 | 净化HTML输出 | `<script>`, `onclick`, `javascript:` |

### 2.2 工具信任等级要求

```python
TOOL_TRUST_REQUIREMENTS = {
    'shell_execute': 3,    # 中等信任
    'file_read': 2,        # 基础信任
    'file_write': 3,       # 中等信任
    'http_request': 4,     # 高信任
    'code_execute': 3,     # 中等信任
    'file_list': 1,        # 最低信任
    'file_search': 2,      # 基础信任
    'system_info': 1,      # 最低信任
    'json_parse': 0,       # 无需信任
    'search_web': 2,       # 基础信任
}
```

---

## 3. 其他敏感操作入口点

### 3.1 网络访问 (高频文件)

| 文件路径 | 匹配数 | 主要功能 | 权限需求 |
|----------|--------|----------|----------|
| `web_ui/server_final.py` | 3195 | 主服务器，API调用 | INTERNET |
| `agents/agent_framework_hub.py` | 383 | Agent框架，外部API | INTERNET |
| `core/nanobot_web_v2.py` | 77 | Web接口 | INTERNET |
| `skills/data_connector/rss.py` | - | RSS订阅获取 | INTERNET |
| `skills/data_connector/github.py` | - | GitHub API调用 | INTERNET |
| `skills/data_connector/wecom.py` | - | 企业微信API | INTERNET |

### 3.2 文件操作 (高频文件)

| 文件路径 | 匹配数 | 主要功能 | 权限需求 |
|----------|--------|----------|----------|
| `web_ui/server_final.py` | 118 | 配置/日志读写 | FILE_READ/WRITE |
| `tools/backup_scheduler.py` | 17 | 备份文件操作 | FILE_READ/WRITE |
| `tools/data_backup.py` | 14 | 数据备份 | FILE_READ/WRITE |
| `tools/knowledge_extractor.py` | 7 | 知识提取 | FILE_READ |
| `tools/state_sync.py` | 30 | 状态同步 | FILE_READ/WRITE |

### 3.3 命令执行 (高频文件)

| 文件路径 | 匹配数 | 主要功能 | 权限需求 |
|----------|--------|----------|----------|
| `agents/agent_framework_hub.py` | 597 | Agent执行器 | SYSTEM_CMD |
| `agents/smolagents_executor.py` | 66 | SmolAgents执行 | SYSTEM_CMD/CODE_EXEC |
| `agents/xagent2_autonomous.py` | 70 | XAgent自主执行 | SYSTEM_CMD |
| `tools/command_router.py` | 57 | 命令路由 | SYSTEM_CMD |
| `tools/deploy_pipeline.py` | 27 | 部署流水线 | SYSTEM_CMD |
| `tools/orchestrator.py` | 49 | 编排执行 | SYSTEM_CMD |

---

## 4. 权限类型完整定义

```python
class PermissionType(str, Enum):
    """权限类型"""
    INTERNET = "internet"           # 🌐 访问互联网
    WEB_BROWSE = "web_browse"       # 🌐 浏览网页
    FILE_READ = "file_read"         # 📄 读取文件
    FILE_WRITE = "file_write"       # ✏️ 写入文件
    CODE_EXEC = "code_exec"         # 💻 执行代码
    SYSTEM_CMD = "system_cmd"       # ⚙️ 执行系统命令
    API_CALL = "api_call"           # 🔌 调用外部API
    DANGEROUS = "dangerous"         # ⚠️ 危险操作
```

### 4.1 权限风险等级

| 等级 | 权限类型 | 默认策略 | 说明 |
|------|----------|----------|------|
| 🔴 高危 | `DANGEROUS`, `SYSTEM_CMD`, `FILE_WRITE` | 总是询问 | 可能造成不可逆损害 |
| 🟠 中危 | `INTERNET`, `API_CALL`, `CODE_EXEC` | 会话询问 | 可能泄露数据或执行恶意代码 |
| 🟡 低危 | `FILE_READ`, `WEB_BROWSE` | 会话自动 | 只读操作，风险较低 |
| 🟢 安全 | `json_parse`, `system_info` | 自动允许 | 无外部影响 |

---

## 5. 需要权限控制的模块清单

### 5.1 必须控制 (高危)

```
✅ web_ui/tool_executor.py          # 已集成权限
✅ web_ui/secure_tool_executor.py   # 已集成权限
❌ agents/agent_framework_hub.py    # 需要集成
❌ agents/smolagents_executor.py    # 需要集成
❌ agents/xagent*.py                # 需要集成
❌ agents/autogpt*.py               # 需要集成
❌ agents/opendevin*.py             # 需要集成
❌ tools/command_router.py          # 需要集成
❌ tools/deploy_pipeline.py         # 需要集成
❌ skills/data_connector/*.py       # 需要集成
```

### 5.2 建议控制 (中危)

```
❌ tools/backup_*.py                # 文件操作
❌ tools/data_*.py                  # 数据处理
❌ tools/knowledge_*.py             # 知识管理
❌ core/nanobot_web*.py             # Web接口
❌ monitoring/*.py                  # 监控系统
```

### 5.3 可选控制 (低危)

```
⚪ tools/config_*.py                # 配置管理
⚪ tools/scheduler*.py              # 调度器
⚪ tools/notification_system.py     # 通知系统
```

---

## 6. 权限集成优先级

### Phase 1: 核心工具 (已完成)
- [x] `tool_executor.py` 权限检查
- [x] `secure_tool_executor.py` 权限检查
- [x] 前端权限弹窗UI
- [x] 后端权限API
- [x] WebSocket实时推送

### Phase 2: Agent框架 (待完成)
- [ ] `agent_framework_hub.py` 权限拦截
- [ ] `smolagents_executor.py` 权限拦截
- [ ] 所有 `agents/*.py` 敏感操作拦截

### Phase 3: 工具模块 (待完成)
- [ ] `tools/command_router.py` 权限拦截
- [ ] `tools/deploy_pipeline.py` 权限拦截
- [ ] `skills/data_connector/*.py` 权限拦截

### Phase 4: 沙箱隔离 (待设计)
- [ ] Docker容器隔离
- [ ] 资源限制 (CPU/内存/磁盘)
- [ ] 网络隔离 (仅白名单域名)
- [ ] 文件系统隔离 (仅工作目录)

---

## 7. 沙箱改造方案

### 7.1 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    用户请求                              │
└─────────────────────┬───────────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────┐
│              权限审批系统                                │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐   │
│  │ 弹窗UI  │  │ 规则库  │  │ 决策API │  │ 日志    │   │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘   │
└─────────────────────┬───────────────────────────────────┘
                      ▼ 允许
┌─────────────────────────────────────────────────────────┐
│              沙箱执行环境                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Docker容器                                      │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐            │   │
│  │  │网络代理 │ │文件隔离│ │资源限制│            │   │
│  │  └─────────┘ └─────────┘ └─────────┘            │   │
│  │                                                  │   │
│  │  Python执行环境 (无os.system, 无subprocess)     │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 7.2 Docker沙箱配置

```yaml
# docker-compose.sandbox.yml
version: '3.8'
services:
  nanobot-sandbox:
    image: python:3.12-slim
    container_name: nanobot-sandbox
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE
    resources:
      limits:
        cpus: '1.0'
        memory: 512M
      reservations:
        cpus: '0.25'
        memory: 128M
    networks:
      - nanobot-isolated
    volumes:
      - ./workspace:/workspace:rw
      - ./config:/config:ro
    environment:
      - NANOBOT_SANDBOX=true
      - PYTHONUNBUFFERED=1
    read_only: false
    tmpfs:
      - /tmp:size=100M,mode=1777

networks:
  nanobot-isolated:
    driver: bridge
    internal: true  # 禁止外部网络访问
```

### 7.3 网络白名单

```python
# 允许访问的域名白名单
NETWORK_WHITELIST = [
    'api.openai.com',
    'api.anthropic.com',
    'ollama.local',
    'huggingface.co',
    'github.com',
    'pypi.org',
]

# 禁止访问的内网地址
NETWORK_BLACKLIST = [
    'localhost',
    '127.0.0.0/8',
    '192.168.0.0/16',
    '10.0.0.0/8',
    '172.16.0.0/12',
]
```

### 7.4 文件系统隔离

```python
# 允许访问的路径
ALLOWED_PATHS = [
    '/workspace',      # 工作目录
    '/tmp',            # 临时文件
    '/config',         # 配置（只读）
]

# 禁止访问的路径
FORBIDDEN_PATHS = [
    '/etc/passwd',
    '/etc/shadow',
    '/root',
    '/home',
    '/var/log',
    '.ssh',
    '.gnupg',
    '.env',
    'credentials',
    'secrets',
]
```

---

## 8. 实施检查清单

### 8.1 权限系统检查

- [ ] 所有网络请求都经过权限检查
- [ ] 所有文件操作都经过权限检查
- [ ] 所有命令执行都经过权限检查
- [ ] 权限弹窗正确显示风险等级
- [ ] 四种决策按钮功能正常
- [ ] 规则持久化存储正常
- [ ] WebSocket实时推送正常

### 8.2 安全检查

- [ ] 敏感文件路径被阻止
- [ ] 危险命令被阻止
- [ ] API密钥被脱敏
- [ ] XSS攻击被防护
- [ ] 路径遍历被阻止
- [ ] 内网访问被阻止

### 8.3 沙箱检查

- [ ] 容器资源限制生效
- [ ] 网络隔离生效
- [ ] 文件系统隔离生效
- [ ] 危险Python模块被禁用
- [ ] 执行超时正常终止

---

## 9. 附录：完整文件列表

### 9.1 网络访问文件 (Top 20)

1. `web_ui/server_final.py` (3195)
2. `agents/agent_framework_hub.py` (383)
3. `core/nanobot_web_v2.py` (77)
4. `web_ui/tool_executor.py` (58)
5. `agents/causal_agent.py` (45)
6. `core/inference_engine.py` (43)
7. `tools/skills_system.py` (34)
8. `core/nanobot_web.py` (30)
9. `evals/test_integration_fix.py` (30)
10. `web_ui/neuracore_backend.py` (30)

### 9.2 命令执行文件 (Top 20)

1. `agents/agent_framework_hub.py` (597)
2. `web_ui/server_final.py` (135)
3. `web_ui/tool_executor.py` (86)
4. `core/skill_orchestrator.py` (76)
5. `agents/xagent2_autonomous.py` (70)
6. `agents/smolagents_executor.py` (66)
7. `evals/test_nanobot.py` (59)
8. `tools/command_router.py` (57)
9. `agents/agentzero2_autonomous.py` (49)
10. `experimental/neuro_cognitive_engine.py` (49)

---

**维护者**: Nanobot Security Team
**版本**: 1.0.0
