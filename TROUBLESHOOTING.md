# Nanobot 权限系统故障排查指南

## 🚨 常见问题与解决方案

---

### 1. 安全拦截器未生效

**症状**: `subprocess.run`、`os.system`、`eval`、`exec` 等调用未被拦截

**原因**:
- 拦截器未在程序启动时安装
- 环境变量 `NANOBOT_ENFORCE_SECURITY` 未设置

**解决方案**:

```bash
# 方法1: 设置环境变量
export NANOBOT_ENFORCE_SECURITY=1
python server_final.py

# 方法2: 在代码中手动安装
from web_ui.secure_interceptor import install_interceptors
install_interceptors()
```

**验证拦截器状态**:

```python
from web_ui.secure_interceptor import is_interceptors_installed
print(f"拦截器已安装: {is_interceptors_installed()}")
```

---

### 2. Docker 沙箱启动失败

**症状**: 日志显示 "Docker不可用，使用本地沙箱模式"

**排查步骤**:

```bash
# 1. 检查 Docker 是否安装
docker --version

# 2. 检查 Docker 服务状态
sudo systemctl status docker

# 3. 检查用户权限
groups $USER  # 应包含 docker

# 4. 添加用户到 docker 组
sudo usermod -aG docker $USER
# 需要重新登录生效

# 5. 测试 Docker
docker run --rm hello-world
```

**降级策略配置**:

```python
# Docker 不可用时拒绝执行
executor = ToolExecutor(
    workspace=workspace,
    sandbox_enabled=True,
    sandbox_fallback='deny',  # 或 'direct'
)
```

---

### 3. 审批请求无响应/超时

**症状**: 工具调用卡住，无审批弹窗

**可能原因**:

1. **WebSocket 连接断开**
   ```javascript
   // 前端检查连接状态
   if (ws.readyState !== WebSocket.OPEN) {
       ws = new WebSocket('ws://localhost:8080/ws/permission/session_id');
   }
   ```

2. **审批超时配置过短**
   ```python
   executor = ToolExecutor(
       workspace=workspace,
       approval_timeout=60,  # 增加到60秒
   )
   ```

3. **静默模式未启用**
   ```python
   # 超时自动拒绝
   executor = ToolExecutor(
       workspace=workspace,
       silent_mode=True,
   )
   ```

**查看审批状态**:

```bash
# 使用 CLI 查看
python policy_cli.py audit 1
```

---

### 4. 策略不生效

**症状**: 配置的策略未被匹配

**排查步骤**:

```bash
# 1. 查看所有策略
python policy_cli.py list

# 2. 测试策略匹配
python policy_cli.py test shell_execute '{"command": "ls"}'

# 3. 检查策略优先级
python policy_cli.py show <policy_id>
```

**常见错误**:

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 策略被跳过 | 优先级过低 | 提高优先级 `priority: 200` |
| 策略未启用 | `enabled: false` | `python policy_cli.py enable <id>` |
| 参数模式不匹配 | 通配符格式错误 | 使用 `*` 作为通配符 |

**策略示例**:

```json
{
  "id": "allow_workspace",
  "name": "允许工作区操作",
  "tool_pattern": "file_*",
  "param_patterns": {
    "path": "/workspace/*"
  },
  "decision": "allow",
  "priority": 100,
  "enabled": true
}
```

---

### 5. 审计日志缺失

**症状**: `~/.nanobot/audit/` 目录为空或日志不全

**排查**:

```bash
# 1. 检查日志目录权限
ls -la ~/.nanobot/audit/

# 2. 检查磁盘空间
df -h ~/.nanobot/

# 3. 查看审计统计
python policy_cli.py audit 24
```

**手动刷新日志**:

```python
from web_ui.audit_logger import get_audit_logger
audit = get_audit_logger()
audit._flush()  # 强制刷新缓冲区
```

---

### 6. 容器池性能问题

**症状**: 沙箱执行延迟高

**解决方案**:

```python
from web_ui.sandbox_executor import SandboxConfig, SandboxExecutor

config = SandboxConfig(
    pool_enabled=True,
    pool_size=3,           # 预热3个容器
    pool_reuse_strategy="session",  # 会话级复用
    pool_idle_timeout=600, # 10分钟空闲后清理
)

sandbox = SandboxExecutor(config)

# 查看池状态
stats = sandbox.get_pool_stats()
print(f"命中率: {stats['hit_rate']:.2%}")
```

---

### 7. 告警回调不触发

**症状**: 高风险事件未收到告警

**配置告警回调**:

```python
from web_ui.audit_logger import get_audit_logger

def my_alert_handler(alert, event):
    print(f"🚨 告警: {alert['message']}")
    # 发送邮件/webhook
    # requests.post('https://hooks.example.com/alert', json=alert)

audit = get_audit_logger()
audit.add_alert_callback(my_alert_handler)

# 设置告警阈值
audit.set_alert_threshold('high_risk', count=3, window=1800)
```

---

### 8. 权限拒绝但无日志

**症状**: 操作被拒绝但审计日志中没有记录

**检查权限系统状态**:

```python
executor = ToolExecutor(workspace=workspace)

# 检查权限系统是否启用
print(f"权限系统: {executor.permission_enabled}")

# 检查审批处理器
print(f"审批处理器: {executor.approval_handler}")

# 检查审计日志
print(f"审计日志: {executor.audit_logger}")
```

---

## 📊 诊断命令速查

| 场景 | 命令 |
|------|------|
| 查看策略列表 | `python policy_cli.py list` |
| 测试策略匹配 | `python policy_cli.py test <tool> '<params>'` |
| 查看审计摘要 | `python policy_cli.py audit 24` |
| 导出策略 | `python policy_cli.py export policies.json` |
| 检查 Docker | `docker info` |
| 检查拦截器 | `python -c "from web_ui.secure_interceptor import is_interceptors_installed; print(is_interceptors_installed())"` |

---

## 🔧 调试模式

启用详细日志:

```python
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
```

环境变量调试:

```bash
# 启用安全强制模式
export NANOBOT_ENFORCE_SECURITY=1

# 启用调试日志
export NANOBOT_DEBUG=1

# 设置审批超时
export NANOBOT_APPROVAL_TIMEOUT=60
```

---

## 📞 获取帮助

1. 查看日志文件: `~/.nanobot/audit/audit_*.jsonl`
2. 查看服务器日志: 控制台输出
3. 运行诊断: `python policy_cli.py audit 1`
4. 提交问题: 附带审计日志和错误信息

---

## 🔄 多进程与子进程拦截

### 问题: 子进程中的操作未被拦截

**症状**: 使用 `multiprocessing` 创建的子进程中，`subprocess.run` 等调用未被拦截

**原因**: `multiprocessing` 默认使用 `fork` 模式（Linux），子进程不会继承拦截器

**解决方案**:

```python
# 在程序入口处设置 spawn 模式
import multiprocessing

if __name__ == '__main__':
    # 必须在创建任何 Process 之前调用
    multiprocessing.set_start_method('spawn')
    
    # 安装拦截器
    from web_ui.secure_interceptor import install_interceptors
    install_interceptors()
    
    # 然后启动主程序
    main()
```

**验证当前模式**:

```python
import multiprocessing
print(f"当前启动模式: {multiprocessing.get_start_method()}")

# 或使用诊断函数
from web_ui.secure_interceptor import check_interceptor_status
status = check_interceptor_status()
print(f"multiprocessing 模式: {status['multiprocessing_method']}")
print(f"警告: {status['warnings']}")
```

### 问题: os.fork() 调用警告

**症状**: 日志中出现 "检测到 os.fork() 调用" 警告

**原因**: 直接调用 `os.fork()` 创建的子进程不会继承拦截器

**解决方案**:

- 避免直接使用 `os.fork()`
- 改用 `multiprocessing.Process` 并设置 `spawn` 模式
- 如必须使用 `fork`，在子进程中手动调用 `install_interceptors()`

```python
import os
import sys

# 不推荐
pid = os.fork()
if pid == 0:
    # 子进程需要手动安装拦截器
    from web_ui.secure_interceptor import install_interceptors
    install_interceptors()
    # 子进程逻辑
```

### 环境变量配置

```bash
# 强制启用安全模式（子进程自动安装拦截器）
export NANOBOT_ENFORCE_SECURITY=1

# Python 内存分配器（某些情况下有助于 fork 安全）
export PYTHONMALLOC=malloc

# 设置 multiprocessing 默认模式
export MP_START_METHOD=spawn
```

---

## 🖥️ 跨平台兼容性

### 问题: 文件锁在 Windows 上失败

**症状**: 策略文件保存时报错 "fcntl 不可用"

**原因**: Windows 不支持 `fcntl`，需要使用 `msvcrt`

**解决方案**: 系统已自动检测平台并选择合适的锁实现

```python
# 自动检测已在 PermissionPolicyManager 中实现
from web_ui.permission_policy import PermissionPolicyManager

manager = PermissionPolicyManager()
print(f"文件锁支持: {manager._lock_supported}")
```

**手动验证**:

```python
import sys
if sys.platform == 'win32':
    try:
        import msvcrt
        print("Windows 文件锁可用")
    except ImportError:
        print("⚠️ msvcrt 不可用，文件锁已禁用")
else:
    try:
        import fcntl
        print("Unix 文件锁可用")
    except ImportError:
        print("⚠️ fcntl 不可用，文件锁已禁用")
```

### 问题: 容器池并发访问死锁

**症状**: 高并发场景下程序卡住

**原因**: 在 asyncio 环境中使用了同步锁

**解决方案**: 根据运行环境选择正确的锁

| 场景 | 使用锁 | 说明 |
|------|--------|------|
| 多线程 | `threading.RLock` | `sandbox_executor._pool_lock` |
| asyncio | `asyncio.Lock` | `sandbox_executor._async_lock` |
| 混合 | 两者都需要 | 分别用于同步/异步方法 |

```python
# 纯同步环境
executor.get_or_create_container(session_id="sync_session")

# 纯异步环境
container_id, is_new = await executor.get_or_create_container_async(
    session_id="async_session"
)
```

---

## ⚡ 性能问题排查

### 问题: 容器池命中率低

**症状**: 日志显示大量 "pool miss"

**排查**:

```bash
# 运行性能测试
python benchmark_container_pool.py

# 查看池状态
python -c "
from web_ui.sandbox_executor import SandboxExecutor, SandboxConfig
executor = SandboxExecutor(SandboxConfig(pool_enabled=True, pool_size=10))
print(executor.get_pool_stats())
"
```

**优化建议**:

1. 增加 `pool_size` 配置
2. 使用会话亲和性（相同 session_id 复用容器）
3. 调整 `idle_timeout` 减少容器回收

### 问题: 审批响应慢

**症状**: 工具执行等待时间长

**排查**:

```bash
# 检查审批超时配置
python -c "
from web_ui.tool_executor import ToolExecutor
executor = ToolExecutor(workspace='.', safe_mode=True)
print(f'审批超时: {executor.approval_timeout}秒')
"
```

**优化**:

- 使用策略自动决策减少审批请求
- 配置会话级缓存 (`ALLOW_SESSION`)
- 使用 WebSocket 实时审批而非 CLI 轮询
