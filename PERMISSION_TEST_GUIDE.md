# Nanobot 权限系统测试指南

## 概述

本文档说明如何通过 WebUI 界面测试权限审批功能。权限系统支持 8 种权限类型，每种对应不同的风险等级。

---

## 测试前准备

1. **启动 WebUI 服务器**
   ```bash
   cd /home/field/.nanobot/workspace/web_ui
   python server_final.py
   ```

2. **访问 WebUI**
   - 主界面: http://localhost:8080/
   - 测试页面: http://localhost:8080/static/test_permission.html

3. **打开 Dashboard**
   - 点击左上角 "Nanobot" 按钮
   - 查看 "🔐 权限管理" 区域

---

## 测试提示词分类

### 1. 🌐 互联网访问权限 (internet)

**触发条件**: 模型需要访问网络资源

**测试提示词**:

| 提示词 | 预期行为 |
|--------|----------|
| `请帮我访问 https://example.com 获取网页内容` | 弹出互联网访问权限请求 |
| `搜索一下今天的新闻` | 弹出互联网访问权限请求 |
| `帮我下载 https://github.com/repo/file.zip` | 弹出互联网访问权限请求 |
| `查看 https://api.example.com/data 的API响应` | 弹出互联网访问权限请求 |

**验证点**:
- 弹窗显示 "🌐 互联网访问" 图标
- 详情显示请求的 URL
- 四个决策按钮可用

---

### 2. 🖥️ 浏览网页权限 (web_browse)

**触发条件**: 模型需要使用浏览器工具

**测试提示词**:

| 提示词 | 预期行为 |
|--------|----------|
| `用浏览器打开 https://www.baidu.com 并截图` | 弹出浏览网页权限请求 |
| `帮我查看这个网站的内容: https://docs.python.org` | 弹出浏览网页权限请求 |
| `打开网页并提取文章标题` | 弹出浏览网页权限请求 |

**验证点**:
- 弹窗显示 "🖥️ 浏览网页" 图标
- 详情显示目标网站

---

### 3. 📄 读取文件权限 (file_read)

**触发条件**: 模型需要读取本地文件

**测试提示词**:

| 提示词 | 预期行为 |
|--------|----------|
| `读取 /home/field/.nanobot/config.json 的内容` | 弹出读取文件权限请求 |
| `查看 ~/.bashrc 文件` | 弹出读取文件权限请求 |
| `帮我分析 /var/log/syslog 日志文件` | 弹出读取文件权限请求 |
| `打开 /etc/passwd 文件看看内容` | 弹出读取文件权限请求 |

**验证点**:
- 弹窗显示 "📄 读取文件" 图标
- 详情显示文件路径
- 风险等级为 "中风险"

---

### 4. ✏️ 写入文件权限 (file_write)

**触发条件**: 模型需要创建或修改文件

**测试提示词**:

| 提示词 | 预期行为 |
|--------|----------|
| `创建一个文件 /tmp/test.txt 内容是 hello world` | 弹出写入文件权限请求 |
| `把当前对话保存到 ~/chat_export.md` | 弹出写入文件权限请求 |
| `修改 /home/field/.nanobot/config.json 添加新配置` | 弹出写入文件权限请求 |
| `在桌面创建一个 README.md 文件` | 弹出写入文件权限请求 |

**验证点**:
- 弹窗显示 "✏️ 写入文件" 图标
- 详情显示目标文件路径
- 风险等级为 "高风险"

---

### 5. 💻 执行代码权限 (code_exec)

**触发条件**: 模型需要运行代码

**测试提示词**:

| 提示词 | 预期行为 |
|--------|----------|
| `运行 Python 代码: print('hello')` | 弹出执行代码权限请求 |
| `执行这个脚本: python3 -c "import os; print(os.getcwd())"` | 弹出执行代码权限请求 |
| `帮我写一个 Python 脚本并运行它` | 弹出执行代码权限请求 |
| `用 Node.js 执行 console.log('test')` | 弹出执行代码权限请求 |

**验证点**:
- 弹窗显示 "💻 执行代码" 图标
- 详情显示要执行的代码
- 风险等级为 "高风险"

---

### 6. ⚙️ 系统命令权限 (system_cmd)

**触发条件**: 模型需要执行系统级命令

**测试提示词**:

| 提示词 | 预期行为 |
|--------|----------|
| `执行 ls -la 命令查看当前目录` | 弹出系统命令权限请求 |
| `运行 ps aux 查看进程列表` | 弹出系统命令权限请求 |
| `使用 df -h 查看磁盘空间` | 弹出系统命令权限请求 |
| `执行 sudo apt update 更新系统` | 弹出系统命令权限请求 |
| `运行 docker ps 查看容器状态` | 弹出系统命令权限请求 |

**验证点**:
- 弹窗显示 "⚙️ 系统命令" 图标
- 详情显示要执行的命令
- 风险等级为 "危险"

---

### 7. 🔌 API调用权限 (api_call)

**触发条件**: 模型需要调用外部 API

**测试提示词**:

| 提示词 | 预期行为 |
|--------|----------|
| `调用 OpenAI API 生成一张图片` | 弹出 API 调用权限请求 |
| `使用天气 API 查询北京天气` | 弹出 API 调用权限请求 |
| `发送 HTTP POST 请求到 https://api.example.com` | 弹出 API 调用权限请求 |
| `调用 GitHub API 获取仓库信息` | 弹出 API 调用权限请求 |

**验证点**:
- 弹窗显示 "🔌 API调用" 图标
- 详情显示 API 端点

---

### 8. ⚠️ 危险操作权限 (dangerous)

**触发条件**: 模型检测到潜在危险操作

**测试提示词**:

| 提示词 | 预期行为 |
|--------|----------|
| `删除 /home/field/test 目录下的所有文件` | 弹出危险操作权限请求 |
| `清空数据库中的 users 表` | 弹出危险操作权限请求 |
| `格式化 /dev/sdb1 分区` | 弹出危险操作权限请求 |
| `执行 rm -rf /tmp/* 清理临时文件` | 弹出危险操作权限请求 |

**验证点**:
- 弹窗显示 "⚠️ 危险操作" 图标
- 详情显示危险操作内容
- 风险等级为 "危险"（红色）

---

## 决策按钮测试

### 测试四种决策类型

1. **拒绝 (deny)**
   - 点击 "拒绝" 按钮
   - 弹窗消失，模型收到拒绝响应
   - Dashboard 统计不变

2. **本次允许 (allow_once)**
   - 点击 "本次" 按钮
   - 弹窗消失，模型继续执行
   - 不创建持久规则

3. **会话允许 (allow_session)**
   - 点击 "会话" 按钮
   - 弹窗消失，模型继续执行
   - Dashboard "会话规则" 统计 +1
   - 同一会话内相同请求自动批准

4. **始终允许 (allow_always)**
   - 点击 "始终" 按钮
   - 弹窗消失，模型继续执行
   - Dashboard "规则数" +1
   - 创建持久化规则，永久自动批准

---

## Dashboard 功能测试

### 1. 权限统计卡片
- 验证数字实时更新
- 点击 "刷新" 按钮更新数据
- 点击 "清除" 按钮清空规则

### 2. 自动授权规则
- 添加新规则
- 查看规则列表
- 删除单个规则

### 3. 待处理请求
- 查看待处理列表
- 批量批准/拒绝
- 单独处理请求

### 4. 权限设置
- 超时自动拒绝开关
- 超时时间滑块
- 弹窗通知开关
- 提示音开关

---

## 自动化测试脚本

使用测试页面进行自动化测试:

```
http://localhost:8080/static/test_permission.html
```

功能:
- API 连接测试
- WebSocket 连接测试
- 不同类型权限请求测试
- 决策按钮测试
- 规则 CRUD 测试
- 批量请求测试
- 压力测试

---

## 手动 API 测试

### 创建权限请求
```bash
curl -X POST http://localhost:8080/api/permission/request \
  -H "Content-Type: application/json" \
  -d '{
    "permission_type": "internet",
    "title": "请求访问网页",
    "description": "模型需要访问网页获取信息",
    "details": "https://example.com",
    "session_id": "test_session"
  }'
```

### 查看待处理请求
```bash
curl http://localhost:8080/api/permission/pending
```

### 做出决策
```bash
curl -X POST http://localhost:8080/api/permission/decide \
  -H "Content-Type: application/json" \
  -d '{"request_id": "xxx", "decision": "allow_once"}'
```

### 查看规则列表
```bash
curl http://localhost:8080/api/permission/rules
```

---

## 注意事项

1. **权限系统需要模型配合**
   - 当前权限系统已实现前后端
   - 需要修改 nanobot 核心代码，在执行敏感操作前调用权限 API
   - 参考 `permission_manager.py` 中的 `create_request` 和 `wait_for_decision`

2. **集成点**
   - 文件操作: `file_manager.py`
   - 代码执行: `code_executor.py`
   - 网络请求: `web_browser.py`
   - 系统命令: `shell_executor.py`

3. **测试模式**
   - 使用测试页面可完整测试 UI 和 API
   - 实际聊天测试需要模型支持

---

## 下一步开发

1. **集成到 nanobot 核心**
   - 在工具执行前检查权限
   - 调用 `permission_manager.check_auto_permission()`
   - 无自动权限时创建请求并等待

2. **增强模型提示**
   - 添加系统提示告知模型权限机制
   - 模型在需要权限时输出特定标记

3. **WebSocket 实时推送**
   - 已实现 `/ws/permission` 端点
   - 前端已实现 WebSocket 连接
   - 可实时接收权限请求
