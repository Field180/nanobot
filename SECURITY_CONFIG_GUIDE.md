# Nanobot 权限系统配置指南

## 📋 概述

Nanobot 权限系统提供多层次的安全控制：

1. **策略层** - 自动决策规则（允许/拒绝/沙箱）
2. **审批层** - 用户交互审批（CLI/WebSocket）
3. **沙箱层** - Docker 容器隔离执行
4. **审计层** - 完整操作日志记录

---

## 🚀 快速开始

### 1. 基本配置

```python
from web_ui.tool_executor import ToolExecutor
from web_ui.approval_handler import CLIApprovalHandler
from web_ui.permission_policy import PermissionPolicyManager

# 创建审批处理器
approval_handler = CLIApprovalHandler()

# 创建执行器
executor = ToolExecutor(
    workspace=Path("/workspace"),
    safe_mode=True,
    session_id="user_session_001",
    approval_handler=approval_handler,
    sandbox_enabled=True,
    sandbox_fallback="deny",  # Docker不可用时拒绝执行
)
```

### 2. 安装安全拦截器

```python
from web_ui.secure_interceptor import install_interceptors
from web_ui.secure_interceptor import create_permission_check_from_executor

# 安装拦截器（拦截 subprocess, os.system, eval, exec 等）
install_interceptors(
    permission_check=create_permission_check_from_executor(executor),
    intercept_subprocess=True,
    intercept_os=True,
    intercept_eval_exec=True,
    intercept_open=True,
)
```

---

## 📁 配置文件

### policies.json - 权限策略配置

位置: `~/.nanobot/policies.json`

```json
{
  "version": "1.0",
  "updated_at": "2026-03-28T20:00:00Z",
  "policies": [
    {
      "id": "allow_workspace_read",
      "name": "允许读取工作区文件",
      "tool_pattern": "file_read",
      "param_patterns": {
        "path": "/workspace/*"
      },
      "decision": "allow",
      "priority": 10,
      "description": "允许读取工作区内的所有文件"
    },
    {
      "id": "sandbox_code_exec",
      "name": "代码执行强制沙箱",
      "tool_pattern": "code_execute",
      "param_patterns": {},
      "decision": "sandbox",
      "priority": 100,
      "description": "所有代码执行都在沙箱中运行"
    },
    {
      "id": "deny_dangerous_commands",
      "name": "拒绝危险命令",
      "tool_pattern": "shell_execute",
      "param_patterns": {
        "command": "*rm -rf*"
      },
      "decision": "deny",
      "priority": 200,
      "description": "拒绝危险的 rm -rf 命令"
    },
    {
      "id": "allow_github_api",
      "name": "允许 GitHub API 访问",
      "tool_pattern": "http_request",
      "param_patterns": {
        "url": "https://api.github.com/*"
      },
      "decision": "allow",
      "priority": 50,
      "description": "允许访问 GitHub API"
    },
    {
      "id": "deny_internal_network",
      "name": "拒绝内网访问",
      "tool_pattern": "http_request",
      "param_patterns": {
        "url": "*localhost*"
      },
      "decision": "deny",
      "priority": 150,
      "description": "拒绝访问 localhost"
    }
  ]
}
```

### 策略字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 唯一标识符 |
| `name` | string | 策略名称 |
| `tool_pattern` | string | 工具名称模式（支持通配符 `*`） |
| `param_patterns` | object | 参数匹配规则 |
| `decision` | string | 决策: `allow`, `deny`, `sandbox`, `ask` |
| `priority` | int | 优先级（越高越优先） |
| `expires_at` | float | 过期时间戳（可选） |

---

## 🔧 沙箱配置

### Docker 资源限制

```python
from web_ui.sandbox_executor import SandboxConfig, SandboxExecutor

config = SandboxConfig(
    cpu_limit=0.5,           # CPU 限制 (0.5 = 50%)
    memory_limit="256M",     # 内存限制
    disk_limit="100M",       # 磁盘限制
    network_enabled=False,   # 禁用网络
    allowed_domains=[],      # 允许的域名白名单
    execution_timeout=30,    # 执行超时（秒）
    max_output_size=10000,   # 最大输出大小
)

sandbox = SandboxExecutor(config)
```

### Docker 降级策略

```python
executor = ToolExecutor(
    workspace=workspace,
    sandbox_enabled=True,
    sandbox_fallback="deny",  # 或 "direct"
)
```

| 降级策略 | 说明 |
|---------|------|
| `deny` | Docker 不可用时拒绝执行 |
| `direct` | Docker 不可用时降级为直接执行 |

---

## 🌐 WebSocket 审批集成

### 后端配置

```python
# server_final.py
from web_ui.permission_websocket import (
    get_ws_approval_manager,
    create_integrated_approval_handler,
)

# WebSocket 端点已内置
# @app.websocket("/ws/permission/{session_id}")

# 创建集成审批处理器
approval_handler = create_integrated_approval_handler(
    use_cli_fallback=True,  # 无 WebSocket 连接时回退到 CLI
    timeout=60,
)

executor = ToolExecutor(
    workspace=workspace,
    approval_handler=approval_handler,
)
```

### 前端集成

```javascript
// 连接 WebSocket
const ws = new WebSocket(`ws://localhost:8000/ws/permission/${sessionId}`);

// 接收权限请求
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    if (data.type === 'permission_request') {
        // 显示审批弹窗
        showApprovalDialog({
            requestId: data.request_id,
            toolName: data.tool_name,
            title: data.title,
            description: data.description,
            details: data.details,
            riskLevel: data.risk_level,
        });
    }
};

// 发送决策
function sendDecision(requestId, decision) {
    ws.send(JSON.stringify({
        type: 'permission_decision',
        request_id: requestId,
        decision: decision,  // 'allow_once', 'allow_session', 'deny'
    }));
}
```

---

## 📊 审计日志

### 日志位置

`~/.nanobot/audit/audit_YYYY-MM-DD.jsonl`

### 日志格式

```json
{
  "event_id": "evt_abc123",
  "event_type": "permission_request",
  "timestamp": 1711651200.0,
  "session_id": "user_session_001",
  "user_id": "default",
  "tool_name": "shell_execute",
  "params": {"command": "ls -la"},
  "decision": "allow_once",
  "risk_level": "medium",
  "duration_ms": 150.5
}
```

### 查询审计日志

```python
from web_ui.audit_logger import get_audit_logger

audit = get_audit_logger()

# 查询事件
events = audit.query_events(
    tool_name="shell_execute",
    risk_level="high",
    limit=100,
)

# 获取摘要
summary = audit.get_summary(hours=24)
```

---

## 🛡️ 安全最佳实践

### 1. 策略配置

- ✅ 为所有敏感工具配置默认策略
- ✅ 使用高优先级策略拒绝危险操作
- ✅ 定期审查和更新策略
- ✅ 为临时权限设置过期时间

### 2. 沙箱使用

- ✅ 所有代码执行强制沙箱
- ✅ 所有 Shell 命令强制沙箱
- ✅ 配置合理的资源限制
- ✅ 禁用不必要的网络访问

### 3. 审计监控

- ✅ 定期检查审计日志
- ✅ 监控高风险操作
- ✅ 设置异常告警
- ✅ 保留足够长时间的日志

### 4. 敏感路径保护

系统自动拒绝访问以下敏感路径：

```
.ssh/id_rsa
.ssh/id_ed25519
.gnupg/
.netrc
.pgpass
credentials.json
secrets.json
.env
/etc/passwd
/etc/shadow
/etc/sudoers
```

---

## 🧪 测试

### 运行测试

```bash
cd /home/field/.nanobot/workspace/web_ui
python -m pytest test_permission_system.py -v
```

### 测试覆盖

- PermissionManager 测试
- ApprovalHandler 测试
- SandboxExecutor 测试
- PermissionPolicy 测试
- AuditLogger 测试
- ToolExecutor 集成测试

---

## 📝 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `NANOBOT_SILENT` | 静默模式（跳过审批） | `0` |
| `NANOBOT_SANDBOX_FALLBACK` | 沙箱降级策略 | `deny` |
| `NANOBOT_AUDIT_DIR` | 审计日志目录 | `~/.nanobot/audit` |
| `NANOBOT_POLICIES_PATH` | 策略文件路径 | `~/.nanobot/policies.json` |

---

## 🔍 故障排查

### Docker 不可用

```bash
# 检查 Docker 状态
docker info

# 检查权限
sudo usermod -aG docker $USER
```

### 审批超时

- 检查 WebSocket 连接状态
- 确认前端正确处理审批请求
- 增加超时时间配置

### 策略不生效

- 检查策略优先级
- 确认策略已启用
- 验证参数模式匹配

---

## 📚 API 参考

### ToolExecutor

```python
executor = ToolExecutor(
    workspace: Path,           # 工作目录
    safe_mode: bool = True,    # 安全模式
    session_id: str = None,    # 会话ID
    sandbox_enabled: bool = True,  # 启用沙箱
    approval_handler = None,   # 审批处理器
    sandbox_fallback: str = "deny",  # 降级策略
)

result = executor.execute(tool_name, params)
```

### ApprovalHandler

```python
# CLI 版本
handler = CLIApprovalHandler(timeout=60)

# WebSocket 版本
handler = create_integrated_approval_handler(
    use_cli_fallback=True,
    timeout=60,
)

# 静默版本
handler = SilentApprovalHandler(
    default_decision=ApprovalDecision.ALLOW_ONCE
)
```

---

## � 系统文件白名单配置

### 为什么需要白名单？

安全拦截器会拦截所有文件操作，包括 Python 标准库内部的文件读取（如 `mimetypes` 读取 `/etc/mime.types`）。为了避免误拦截系统必要操作，需要配置白名单。

### 配置文件方式

在 `~/.nanobot/config.json` 中配置：

```json
{
  "security": {
    "system_file_whitelist": [
      "/etc/mime.types",
      "/etc/hosts",
      "/etc/resolv.conf",
      "/etc/localtime",
      "/etc/timezone",
      "/proc/self/status",
      "/proc/self/fd/",
      "/proc/self/cmdline",
      "/proc/self/exe",
      "/proc/self/maps",
      "/proc/cpuinfo",
      "/proc/meminfo",
      "/proc/version",
      "/usr/share/mime/",
      "/usr/share/locale/",
      "/usr/lib/locale/"
    ],
    "trusted_caller_paths": [
      "/usr/lib/python",
      "/usr/local/lib/python",
      "site-packages",
      "dist-packages"
    ]
  }
}
```

### 白名单规则

| 规则类型 | 说明 | 示例 |
|---------|------|------|
| 精确匹配 | 路径完全匹配 | `/etc/mime.types` |
| 前缀匹配 | 路径以指定字符串开头 | `/proc/self/` |
| 只读限制 | 白名单文件只能只读访问 | 模式必须为 `r`/`rb` |

### 信任链机制

除了白名单，拦截器还支持 **信任链检测**：

- 当调用者来自 Python 标准库路径（如 `/usr/lib/python3.12/`）时
- 且操作为只读
- 且目标不是敏感路径（如 `.ssh`、`.env`）

则自动放行，无需显式配置白名单。

### 动态添加白名单

在代码中动态添加：

```python
from web_ui.secure_interceptor import add_to_whitelist

# 添加自定义白名单项
add_to_whitelist("/path/to/your/config/file")
```

### 常见问题排查

**问题**: 访问 Web UI 时出现 `PermissionError: 文件操作被权限系统拒绝: /etc/mime.types`

**原因**: 白名单未包含该文件，或配置文件未正确加载

**解决方案**:
1. 确认 `~/.nanobot/config.json` 包含 `security.system_file_whitelist`
2. 确认拦截器安装时启用了配置加载（默认启用）
3. 检查日志中是否有 `[SecureInterceptor] 已加载 N 个自定义白名单项`

---

## �🔔 审计告警配置

### 配置文件方式

在 `~/.nanobot/config.json` 中配置告警：

```json
{
  "audit": {
    "alerts": [
      {
        "type": "webhook",
        "url": "https://your-server.com/alerts",
        "headers": {"Authorization": "Bearer xxx"},
        "min_level": "medium"
      },
      {
        "type": "slack",
        "webhook_url": "https://hooks.slack.com/services/XXX/YYY/ZZZ",
        "channel": "#security-alerts",
        "min_level": "high"
      },
      {
        "type": "log",
        "file": "~/.nanobot/alerts.log",
        "min_level": "low"
      }
    ],
    "min_level": "medium"
  }
}
```

### 自动加载配置

```python
from web_ui.audit_logger import get_audit_logger_with_config

# 自动从 ~/.nanobot/config.json 加载告警配置
audit = get_audit_logger_with_config()

# 或指定配置文件路径
audit = get_audit_logger_with_config(Path("/path/to/config.json"))
```

### 手动配置告警

```python
from web_ui.audit_logger import (
    AuditLogger,
    WebhookAlertHandler,
    SlackAlertHandler,
    LogAlertHandler,
)

# 创建告警处理器
handlers = [
    WebhookAlertHandler(
        webhook_url="https://your-server.com/alerts",
        min_level="medium"
    ),
    SlackAlertHandler(
        webhook_url="https://hooks.slack.com/...",
        min_level="high"
    ),
    LogAlertHandler(
        log_file=Path.home() / ".nanobot" / "alerts.log"
    ),
]

# 创建审计日志器
audit = AuditLogger(alert_callbacks=handlers)
```

### 告警级别

| 级别 | 说明 | 触发场景 |
|------|------|---------|
| `low` | 低风险 | 普通工具执行、策略匹配 |
| `medium` | 中风险 | 沙箱执行、权限请求 |
| `high` | 高风险 | 敏感操作、拒绝决策 |
| `critical` | 严重 | 安全违规、拦截器触发 |

---

## 🌐 WebSocket 审批集成

### FastAPI 集成

```python
# fastapi_approval_example.py
from fastapi import FastAPI, WebSocket
from web_ui.permission_websocket import WebSocketApprovalManager
from web_ui.tool_executor import ToolExecutor

app = FastAPI()
approval_manager = WebSocketApprovalManager()

@app.websocket("/ws/permission/{session_id}")
async def permission_websocket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    await approval_manager.register_connection(websocket, session_id)
    
    while True:
        data = await websocket.receive_json()
        if data.get("type") == "decision":
            approval_manager.notify_decision(
                data["request_id"],
                data["decision"]
            )

# 运行: uvicorn fastapi_approval_example:app --port 8080
```

### Flask 集成

```python
# 使用 Flask-Sock
from flask import Flask
from flask_sock import Sock
from web_ui.permission_websocket import WebSocketApprovalManager

app = Flask(__name__)
sock = Sock(app)
approval_manager = WebSocketApprovalManager()

@sock.route('/ws/permission/<session_id>')
def permission_ws(ws, session_id):
    import json
    while True:
        data = json.loads(ws.receive())
        if data.get('type') == 'decision':
            approval_manager.notify_decision(
                data['request_id'],
                data['decision']
            )
            ws.send(json.dumps({"type": "decision_ack"}))

# 运行: python app.py
```

### 前端集成

前端示例代码参见 `approval_demo.html`：

```javascript
// 连接 WebSocket
const ws = new WebSocket('ws://localhost:8080/ws/permission/session-123');

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    if (data.type === 'permission_request') {
        // 显示审批对话框
        showApprovalDialog(data);
    }
};

// 发送决策
function sendDecision(requestId, decision) {
    ws.send(JSON.stringify({
        type: 'decision',
        request_id: requestId,
        decision: decision  // 'allow_once', 'deny', etc.
    }));
}
```

### 前端 Demo 界面说明

启动 FastAPI 服务后访问 `http://localhost:8080` 可看到审批 Demo 界面：

```
┌─────────────────────────────────────────────────────────────┐
│                  🔐 Nanobot 权限审批 Demo                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 连接状态: ● 已连接                                   │   │
│  │ 会话 ID: session-123                                 │   │
│  │ WebSocket: ws://localhost:8080/ws/permission/...     │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ ⚙️ 请求执行命令                           [高风险]   │   │
│  │                                                     │   │
│  │ 工具: shell_execute                                 │   │
│  │ 详情: ls -la /home/user                             │   │
│  │ 会话: session-123                                   │   │
│  │                                                     │   │
│  │ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │   │
│  │ │ 允许一次 │ │ 会话允许 │ │ 总是允许 │ │  拒绝  │ │   │
│  │ └──────────┘ └──────────┘ └──────────┘ └────────┘ │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 📜 审批历史                                          │   │
│  │ ─────────────────────────────────────────────────── │   │
│  │ 14:32:05 | shell_execute | 允许一次                 │   │
│  │ 14:31:22 | file_read     | 会话允许                │   │
│  │ 14:30:45 | http_request  | 拒绝                    │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**界面元素说明**:

| 元素 | 说明 |
|------|------|
| **连接状态** | WebSocket 连接状态指示器（绿色=已连接，红色=断开） |
| **审批卡片** | 显示待审批的工具请求，包含工具名、参数详情、风险等级 |
| **决策按钮** | 四种决策选项，点击后自动发送响应 |
| **审批历史** | 显示最近的审批记录，包含时间戳和决策结果 |

**风险等级颜色**:

- 🔴 高风险：红色边框，需特别注意
- 🟠 中风险：橙色边框，建议审阅
- 🟢 低风险：绿色边框，相对安全

**运行 Demo**:

```bash
# 启动 FastAPI 服务
cd ~/.nanobot/workspace/web_ui
uvicorn fastapi_approval_example:app --reload --port 8080

# 浏览器访问
open http://localhost:8080
```

---

## 🔍 拦截器状态检测

### 检查拦截器配置

```python
from web_ui.secure_interceptor import (
    check_interceptor_status,
    print_interceptor_status,
)

# 获取状态字典
status = check_interceptor_status()
print(f"已安装: {status['installed']}")
print(f"multiprocessing 模式: {status['multiprocessing_method']}")
print(f"警告: {status['warnings']}")

# 打印详细报告
print_interceptor_status()
```

### 输出示例

```
============================================================
🔒 安全拦截器状态报告
============================================================

已安装: ✅
multiprocessing 模式: spawn
平台: linux
环境变量 NANOBOT_ENFORCE_SECURITY: 1

⚠️ 警告:
  - NANOBOT_ENFORCE_SECURITY 环境变量未设置

💡 建议:
  - 设置环境变量: export NANOBOT_ENFORCE_SECURITY=1

⚠️ 需要调整配置
============================================================
```

---

## 📊 性能测试

### 运行容器池性能测试

```bash
# 运行完整性能测试
python benchmark_container_pool.py

# 输出示例:
# 📊 测试 1: 低并发场景 (10 线程)
#   总请求: 1000
#   成功: 1000 | 失败: 0
#   平均延迟: 0.12 ms
#   吞吐量: 8500 req/s
#   池命中率: 85.00%
```

### 运行单元测试

```bash
# 运行所有测试
python -m pytest test_e2e_permission_flow.py -v

# 运行特定测试
python -m pytest test_e2e_permission_flow.py::TestContainerPoolConcurrency -v

# 生成覆盖率报告
python -m pytest test_e2e_permission_flow.py --cov=. --cov-report=html
open htmlcov/index.html
```

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.1.0 | 2026-03-28 | 添加异步容器池、跨平台文件锁、配置化告警 |
| 1.0.0 | 2026-03-28 | 初始版本，完整权限系统 |
