"""
Configuration Manager Module (P9 extraction from server_final.py)
==================================================================
Centralized prompts, rate limiting, response caching, and tool-verb mappings.
"""

import time
import logging
from collections import defaultdict
from hashlib import md5
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ========== 请求限流配置 ==========
RATE_LIMITS = {
    "default": {"requests": 100, "window": 60},  # 100请求/分钟
    "chat": {"requests": 30, "window": 60},       # 30请求/分钟
    "stream": {"requests": 10, "window": 60},     # 10请求/分钟
}
rate_limit_store: Dict[str, list] = defaultdict(list)  # IP -> [timestamp1, timestamp2, ...]


def check_rate_limit(client_ip: str, endpoint_type: str = "default") -> tuple:
    """检查请求限流，返回 (allowed, remaining_seconds)"""
    config = RATE_LIMITS.get(endpoint_type, RATE_LIMITS["default"])
    now = time.time()
    window_start = now - config["window"]

    # 清理过期记录
    rate_limit_store[client_ip] = [
        t for t in rate_limit_store[client_ip] if t > window_start
    ]

    current_count = len(rate_limit_store[client_ip])

    if current_count >= config["requests"]:
        oldest = min(rate_limit_store[client_ip])
        remaining = int(oldest + config["window"] - now) + 1
        return False, remaining

    rate_limit_store[client_ip].append(now)
    return True, config["requests"] - current_count - 1


# ========== 🚀 Phase 1: 响应缓存 ==========
_RESPONSE_CACHE: Dict[str, dict] = {}
_CACHE_MAX_SIZE = 100


def _get_cache_key(message: str, session_id: str) -> str:
    """生成缓存键"""
    return md5(f"{session_id}:{message}".encode()).hexdigest()[:16]


def _get_cached_response(message: str, session_id: str) -> Optional[dict]:
    """获取缓存的响应"""
    key = _get_cache_key(message, session_id)
    return _RESPONSE_CACHE.get(key)


def _cache_response(message: str, session_id: str, response: dict):
    """缓存响应"""
    global _RESPONSE_CACHE

    # LRU 淘汰
    if len(_RESPONSE_CACHE) >= _CACHE_MAX_SIZE:
        # 移除最旧的键
        oldest_key = list(_RESPONSE_CACHE.keys())[0]
        del _RESPONSE_CACHE[oldest_key]

    key = _get_cache_key(message, session_id)
    _RESPONSE_CACHE[key] = response


# ========== 工具动词映射 ==========
TOOL_VERBS = {
    "file_list": "列出文件",
    "file_read": "读取文件",
    "file_write": "写入文件",
    "file_edit": "编辑文件",
    "shell_execute": "执行命令",
    "web_search": "搜索网络",
    "web_fetch": "获取网页",
    "list_skills": "查询技能",
    "code_search": "搜索代码",
    "grep_search": "搜索内容",
    "find_by_name": "查找文件",
}


def get_tool_summary(tool_name: str, tool_input: dict) -> str:
    """生成工具调用的友好描述（来自 claw）"""
    verb = TOOL_VERBS.get(tool_name, tool_name)
    target = (
        tool_input.get("path", "") or
        tool_input.get("file_path", "") or
        tool_input.get("pattern", "") or
        tool_input.get("command", "")[:60] if tool_input.get("command") else "" or
        tool_input.get("query", "") or
        ""
    )
    if target:
        return f"{verb} {target}"
    return verb


# ========== 安全审计分析专用提示模板 ==========
SECURITY_AUDIT_PROMPT = """你是安全审计专家。分析安全问题时：

1. 使用所有可用工具深入调查
2. 列出每个恶意技能的具体行为（后门、数据窃取、恶意通信等）
3. 按以下格式输出：

### 📛 [技能名称]
**风险等级**: 🔴严重 / 🟠高危 / 🟡中危 / 🟢低危
**恶意类型**: [RCE/数据窃取/后门等]
**行为描述**: [具体做了什么]
**代码分析**: [关键代码片段及分析]
**防御建议**: [如何检测/防范]

4. 尽可能多列举具体技能名称和详细行为，不要只说无法获取"""


# ========== 思考过程提示 ==========
THINKING_PROMPT = """【重要】每次回答前，先用 <thinking>...</thinking> 标签包裹你的思考过程。格式：
<thinking>
1. 用户问题分析...
2. 解决思路...
3. 关键点...
</thinking>
最终答案...

【代码格式要求 - 极其重要】输出代码时必须使用 Markdown 代码块格式：
```python
print("Hello, World!")
```

```javascript
console.log("Hello, World!");
```

**强制规则**：
1. 每个代码块必须以 ```语言名 开头
2. 每个代码块必须以 ``` 结尾（单独一行）
3. 禁止在代码块中间插入其他内容
4. 多个代码块时，每个都必须完整闭合后再开始下一个
5. 绝不允许输出未闭合的代码块！"""


# ========== 行动指南 ==========
ACTIONS_PROMPT = """# 执行行动指南

在执行操作时，请考虑以下原则：

1. **可逆性原则**：本地可逆操作（编辑文件、运行测试）可以自由执行；但对于难以逆转或影响共享系统的操作，请先与用户确认。

2. **风险操作确认**：以下操作需要用户确认：
   - 破坏性操作：删除文件/分支、清空数据库、终止进程
   - 难以逆转操作：强制推送、重置提交、修改 CI/CD
   - 影响他人操作：推送代码、创建/关闭 PR、发送消息

3. **遇到障碍时**：不要使用破坏性操作作为捷径。尝试识别根本原因并修复底层问题，而不是绕过安全检查。

4. **工具调用格式**：当需要调用工具时，请使用以下友好格式：
   ---
   **步骤 N**: [操作描述]
   
   使用 [工具名] 工具：
   - 参数1: 值1
   - 参数2: 值2
   
   ---

5. **并行工具调用**：如果多个工具调用之间没有依赖关系，请在同一次响应中并行调用，以提高效率。"""


# ========== 工具使用指南 ==========
TOOLS_GUIDE_PROMPT = """# 工具使用指南

1. **文件操作**：
   - 读取文件：使用 `file_read` 而非 `cat`、`head`、`tail`
   - 编辑文件：使用 `file_edit` 而非 `sed` 或 `awk`
   - 创建文件：使用 `file_write` 而非 `echo` 重定向

2. **搜索操作**：
   - 搜索文件：使用 `find_by_name` 或 `code_search`
   - 搜索内容：使用 `grep_search` 而非 `grep` 或 `rg`

3. **命令执行**：
   - 仅在需要 shell 执行时使用 `shell_execute`
   - 如果有专用工具可用，优先使用专用工具

4. **任务管理**：
   - 使用 `list_skills` 查看可用技能
   - 复杂任务可分解为多个步骤

5. **输出格式**：
   - 工具调用结果会以友好格式呈现
   - 请根据工具结果继续分析或回答用户问题"""
