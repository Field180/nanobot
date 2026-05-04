# Nanobot Web UI - 使用指南

## 快速启动

### 1. 启动 Web UI 服务器

```bash
cd ~/.nanobot/workspace/web_ui
./start.sh
```

或手动启动：
```bash
cd ~/.nanobot/workspace/web_ui
/home/field/nanobotProjects/nanobot/.venv/bin/python3 server_final.py
```

### 2. 浏览器访问
打开: http://localhost:8080

## 文件说明

| 文件 | 说明 |
|------|------|
| `index.html` | 前端界面 |
| `server_final.py` | 后端服务（FastAPI） |
| `static/js/app.js` | 前端逻辑 |
| `start.sh` | 启动脚本 |

## 状态检查

### 检查服务状态
```bash
curl http://localhost:8080/api/status
```

### 测试聊天
```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","session_id":"test"}'
```

## 故障排除

### 问题: 模型响应超时
**原因**: Ollama 模型加载需要时间（首次约 30-60 秒）

**解决**:
1. 先测试 Ollama 是否工作:
   ```bash
   curl http://192.168.140.1:11434/api/tags
   ```

2. 测试模型:
   ```bash
   curl -X POST http://192.168.140.1:11434/api/generate \
     -d '{"model":"qwen3-coder-next:q4_K_M","prompt":"Hi"}'
   ```

3. 如果 Ollama 正常，检查 nanobot 配置:
   - 模型名称: `openai/qwen3-coder-next:q4_K_M`
   - API Base: `http://192.168.140.1:11434/v1`

### 问题: Web UI 无法访问
**检查端口占用**:
```bash
lsof -i :8080
```

**更换端口**:
修改 `server_final.py` 最后一行:
```python
uvicorn.run(app, host="127.0.0.1", port=8081)  # 换为8081
```

## 模型配置

编辑 `~/.nanobot/config.json`:

```json
{
  "agents": {
    "defaults": {
      "model": "openai/qwen3-coder-next:q4_K_M",
      "maxTokens": 4096,
      "temperature": 0.7
    }
  },
  "providers": {
    "openai": {
      "apiKey": "ollama",
      "apiBase": "http://192.168.140.1:11434/v1"
    }
  }
}
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 主页面 |
| `/api/status` | GET | 服务状态 |
| `/api/sessions` | POST | 创建会话 |
| `/api/chat` | POST | 发送消息 |
| `/api/sessions/{id}` | GET | 获取会话历史 |
| `/health` | GET | 健康检查 |

## 技术架构

```
浏览器 → Web UI (8080) → nanobot agent → Ollama API (11434)
```

## 当前状态

✅ **已完成**:
- 前端界面（HTML/CSS/JS）
- 后端 API（FastAPI）
- 会话管理
- 历史记录
- 启动脚本

⚠️ **待优化**:
- 模型首次加载时间较长
- 需要正确配置 Ollama 连接

## 下一步

1. 确保 Ollama 服务运行: `ollama serve`
2. 确认模型已下载: `ollama list`
3. 测试 API 连接: `curl http://192.168.140.1:11434/api/tags`
4. 启动 Web UI: `./start.sh`
5. 访问: http://localhost:8080
