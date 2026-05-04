# Nanobot Web UI

🌐 Nanobot 网页端聊天界面

## 功能特性

- 💬 实时对话交互
- 🎨 现代化深色主题界面
- 📝 Markdown 渲染与代码高亮
- 💾 对话历史保存（本地存储）
- 📤 对话导出功能
- 🔌 与 Nanobot Gateway 集成
- 🖼️ 视觉分析支持

## 快速启动

### 方法一：一键启动（推荐）

```bash
cd ~/.nanobot/workspace/web_ui
./start.sh
```

然后访问: http://localhost:8080

### 方法二：手动启动

```bash
# 1. 确保依赖已安装
cd ~/.nanobot/workspace/web_ui
/home/field/nanobotProjects/nanobot/.venv/bin/python3 -m pip install fastapi uvicorn

# 2. 启动服务
/home/field/nanobotProjects/nanobot/.venv/bin/python3 server.py
```

## 前置要求

1. **Nanobot Gateway 已启动**（必需）
   ```bash
   # 在另一个终端
   nanobot gateway
   ```

2. **Python 依赖**
   - FastAPI
   - Uvicorn

## 架构说明

```
Web UI (http://localhost:8080)
    ↓
server.py (FastAPI 代理)
    ↓
Nanobot Gateway (http://localhost:18790)
    ↓
Nanobot Agent
```

## 文件结构

```
web_ui/
├── index.html          # 主页面
├── server.py           # 后端服务
├── start.sh            # 启动脚本
├── README.md           # 本文件
└── static/
    ├── css/            # 样式文件（内联在HTML中）
    └── js/
        └── app.js      # 前端逻辑
```

## 使用说明

### 基本对话

1. 在输入框中输入消息
2. 按 Enter 或点击发送按钮
3. 等待 Nanobot 回复

### 快捷键

- `Enter` - 发送消息
- `Shift + Enter` - 换行
- `Ctrl + C` - 复制选中内容

### 功能按钮

- **新对话** - 开始新的聊天会话
- **清空** - 清空当前对话
- **导出** - 将对话导出为文本文件

## 配置选项

编辑 `static/js/app.js` 修改配置:

```javascript
const CONFIG = {
    GATEWAY_URL: 'http://localhost:18790',
    WS_URL: 'ws://localhost:18790/ws',
    DEFAULT_USER: 'web_user',
    MAX_HISTORY: 50
};
```

## 故障排除

### 问题: "无法连接到 Gateway"

**解决:** 确保 Gateway 已启动
```bash
nanobot gateway
```

### 问题: "Agent 无响应"

**解决:** 
1. 检查 Gateway 状态: `curl http://localhost:18790/status`
2. 重启 Web UI 服务
3. 检查 nanobot 是否正常工作: `nanobot agent`

### 问题: 端口被占用

**解决:** 修改 server.py 中的端口
```python
uvicorn.run(app, host="127.0.0.1", port=8081)  # 改为其他端口
```

## 开发计划

- [ ] WebSocket 实时通信
- [ ] 多会话管理
- [ ] 文件上传支持
- [ ] 语音输入/输出
- [ ] 移动端适配优化
- [ ] 主题切换（暗黑/明亮）

## 技术栈

- **前端**: HTML5, CSS3, JavaScript (Vanilla)
- **后端**: Python, FastAPI, Uvicorn
- **UI 库**: Font Awesome, Highlight.js, Marked.js
- **通信**: HTTP REST API (WebSocket 待实现)

## 安全说明

⚠️ 当前版本为本地开发使用，请注意:
- 不要暴露到公网
- 在生产环境使用反向代理（如 Nginx）
- 添加身份验证机制

---

## 🔒 安全特性

Nanobot 包含完整的权限审批和安全沙箱系统：

### 核心安全组件

| 组件 | 说明 | 文档 |
|------|------|------|
| **权限审批** | 工具执行前请求用户确认 | [SECURITY_CONFIG_GUIDE.md](SECURITY_CONFIG_GUIDE.md) |
| **沙箱执行** | Docker 容器隔离危险操作 | [sandbox_executor.py](sandbox_executor.py) |
| **安全拦截器** | 拦截危险系统调用 | [secure_interceptor.py](secure_interceptor.py) |
| **审计日志** | 记录所有安全相关事件 | [audit_logger.py](audit_logger.py) |
| **策略管理** | 自动匹配权限规则 | [permission_policy.py](permission_policy.py) |

### 快速启用安全功能

```bash
# 1. 检查拦截器状态
python -c "from secure_interceptor import print_interceptor_status; print_interceptor_status()"

# 2. 配置审计告警 (编辑 ~/.nanobot/config.json)
{
  "audit": {
    "alerts": [
      {"type": "log", "file": "~/.nanobot/alerts.log"},
      {"type": "webhook", "url": "https://your-server.com/alert"}
    ],
    "min_level": "medium"
  }
}

# 3. 启动 WebSocket 审批服务
uvicorn fastapi_approval_example:app --port 8080
```

### 详细文档

- 📖 [安全配置指南](SECURITY_CONFIG_GUIDE.md) - 完整配置说明
- 🔧 [故障排查](TROUBLESHOOTING.md) - 常见问题解决
- 🎯 [权限测试指南](PERMISSION_TEST_GUIDE.md) - 测试用例说明
- 🌐 [前端审批 Demo](approval_demo.html) - WebSocket 审批界面
- 📊 [升级总览 P0–P9](ITERATION_SUMMARY.md) - 全迭代升级指标与架构图
- 📋 [项目状态报告](PROJECT_STATUS_REPORT.md) - 详细模块/测试/风险分析
- 🗺️ [下阶段 Roadmap](NANOBOT_ROADMAP.md) - P10–P15 规划与里程碑

### CLI 策略管理

```bash
# 列出所有策略
python policy_cli.py list

# 添加策略
python policy_cli.py add policy.json

# 测试策略匹配
python policy_cli.py test shell_execute '{"command": "ls -la"}'

# 查看审计摘要
python policy_cli.py audit 24
```

### 性能测试

```bash
# 运行容器池性能测试
python benchmark_container_pool.py

# 运行单元测试
python -m pytest test_e2e_permission_flow.py -v

# 运行测试并生成覆盖率报告
python -m pytest test_e2e_permission_flow.py --cov=. --cov-report=html
```

## 贡献

欢迎提交 Issue 和 PR！

## 许可证

MIT License
