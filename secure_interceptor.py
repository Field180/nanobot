#!/usr/bin/env python3
"""
安全拦截模块 - SecureInterceptor

拦截并包装危险函数调用：
1. subprocess.run/call/Popen
2. os.system/popen
3. eval/exec
4. open (文件操作)
5. __import__

确保所有敏感操作都经过权限系统审批。

使用方法:
    from web_ui.secure_interceptor import install_interceptors
    install_interceptors()  # 在程序启动时调用

版本: 2.0.0 - 增加系统文件白名单和信任链机制
"""

import os
import sys
import builtins
import subprocess
import logging
import json
from typing import Dict, Any, Optional, Callable, List
from pathlib import Path
from functools import wraps

logger = logging.getLogger(__name__)

# ============================================================================
# 系统文件白名单配置
# ============================================================================

# 默认系统文件白名单（只读访问）
DEFAULT_SYSTEM_WHITELIST = [
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
    "/usr/lib/locale/",
]

# 可信调用者路径（标准库和第三方包）
TRUSTED_CALLER_PATHS = [
    '/usr/lib/python',
    '/usr/local/lib/python',
    '/usr/lib/python3',
    '/usr/local/lib/python3',
    'site-packages',
    'dist-packages',
]

# 可信工作区路径（允许读写）
TRUSTED_WORKSPACE_PATHS = [
    str(Path.home() / '.nanobot'),
    str(Path.home() / '.nanobot' / 'workspace'),
    str(Path.home() / '.nanobot' / 'auth_data'),
    str(Path.home() / '.nanobot' / 'causal_reasoning'),
    str(Path.home() / '.cache' / 'tiktoken'),  # tiktoken 缓存目录
    str(Path.home() / '.cache'),  # 通用缓存目录
]

# 可信命令白名单（允许执行的命令）
TRUSTED_COMMANDS = [
    # ===== Docker 沙箱 =====
    'docker',
    'docker info',
    'docker version',
    'docker ps',
    'docker images',
    'docker run',
    'docker exec',
    'docker stop',
    'docker rm',
    'docker pull',
    
    # ===== 健康检查 =====
    'python3 health_check.py',
    'python3 monitoring/health_check.py',
    'python3 monitoring/health_check.py check',
    'python3 monitoring/health_check.py heartbeat',
    'python3 monitoring/health_check.py liveness',
    'python3 monitoring/health_check.py readiness',
    'python3 monitoring/health_check.py fix',
    'python3 monitoring/health_check.py report',
    
    # ===== 缓存管理 =====
    'python3 smart_cache.py',
    'python3 tools/smart_cache.py',
    'python3 tools/smart_cache.py optimize',
    'python3 tools/smart_cache.py clear',
    'python3 tools/smart_cache.py stats',
    
    # ===== 内存管理 =====
    'python3 memory_manager.py',
    'python3 tools/memory_manager.py',
    'python3 tools/memory_manager.py sleep',
    
    # ===== 反思系统 =====
    'python3 reflection.py',
    'python3 tools/reflection.py',
    'python3 tools/reflection.py reflect',
    
    # ===== 备份系统 =====
    'python3 backup_system.py',
    'python3 tools/backup_system.py',
    'python3 tools/backup_system.py create',
    'python3 tools/backup_system.py restore',
    'python3 tools/backup_system.py list',
    'python3 tools/backup_system.py verify',
    
    # ===== 知识图谱 =====
    'python3 knowledge_graph.py',
    'python3 tools/knowledge_graph.py',
    
    # ===== 配置管理 =====
    'python3 config_manager.py',
    'python3 tools/config_manager.py',
    'python3 tools/config_manager.py list',
    'python3 tools/config_manager.py get',
    'python3 tools/config_manager.py validate',
    'python3 tools/config_manager.py create',
    'python3 tools/config_manager.py export',
    
    # ===== 测试脚本 =====
    'python3 test_task_robustness.py',
    'python3 test_intent_system.py',
    'python3 test_task_execution.py',
    
    # ===== 带绝对路径的脚本 =====
    'python3 /home/field/.nanobot/workspace/test_task_robustness.py',
    'python3 /home/field/.nanobot/workspace/test_intent_system.py',
    'python3 /home/field/.nanobot/workspace/test_task_execution.py',
    'python3 /home/field/.nanobot/workspace/monitoring/health_check.py',
    'python3 /home/field/.nanobot/workspace/tools/smart_cache.py',
    'python3 /home/field/.nanobot/workspace/tools/backup_system.py',
    'python3 /home/field/.nanobot/workspace/tools/memory_manager.py',
    'python3 /home/field/.nanobot/workspace/tools/reflection.py',
    'python3 /home/field/.nanobot/workspace/tools/knowledge_graph.py',
    
    # ===== 调度器 =====
    'python3 scheduler.py',
    'python3 scheduler.py daemon',
    'python3 /home/field/.nanobot/workspace/scheduler.py',
    'python3 /home/field/.nanobot/workspace/scheduler.py daemon',
    
    # ===== Git 命令 =====
    'git status',
    'git log',
    'git branch',
    'git diff',
    'git log -10 --oneline',
    
    # ===== 系统信息（litellm/openai SDK 内部调用） =====
    'uname',
    'uname -p',
    'uname -a',
    'uname -m',
    'uname -s',
    'uname -r',
]

# 全局白名单（运行时可扩展）
_system_whitelist: List[str] = list(DEFAULT_SYSTEM_WHITELIST)

# 动态可信命令白名单（运行时可扩展）
_dynamic_trusted_commands: List[str] = []


def auto_discover_tools(workspace_path: Optional[str] = None) -> List[str]:
    """
    自动发现工具目录中的 Python 脚本并生成可信命令
    
    扫描规则:
    1. tools/*.py - 工具脚本
    2. monitoring/*.py - 监控脚本
    3. *.py (根目录) - 根目录脚本
    
    返回: 可信命令列表
    """
    if workspace_path is None:
        workspace_path = str(Path.home() / '.nanobot' / 'workspace')
    
    discovered_commands = []
    workspace = Path(workspace_path)
    
    # 扫描工具目录
    tools_dir = workspace / 'tools'
    if tools_dir.exists():
        for py_file in tools_dir.glob('*.py'):
            # 添加相对路径和绝对路径两种形式
            script_name = py_file.name
            discovered_commands.extend([
                f'python3 tools/{script_name}',
                f'python3 {py_file}',
            ])
    
    # 扫描监控目录
    monitoring_dir = workspace / 'monitoring'
    if monitoring_dir.exists():
        for py_file in monitoring_dir.glob('*.py'):
            script_name = py_file.name
            discovered_commands.extend([
                f'python3 monitoring/{script_name}',
                f'python3 {py_file}',
            ])
    
    # 扫描根目录脚本
    for py_file in workspace.glob('*.py'):
        script_name = py_file.name
        discovered_commands.extend([
            f'python3 {script_name}',
            f'python3 {py_file}',
        ])
    
    logger.info(f"[SecureInterceptor] 自动发现 {len(discovered_commands)} 个可信命令")
    return discovered_commands


def load_trusted_commands_from_config(config_path: Optional[str] = None) -> List[str]:
    """
    从配置文件加载可信命令白名单
    
    配置格式 (config.json):
    {
        "security": {
            "trusted_commands": [
                "python3 my_script.py",
                "python3 /path/to/script.py"
            ]
        }
    }
    """
    if config_path is None:
        config_path = str(Path.home() / '.nanobot' / 'config.json')
    
    commands = []
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        security_config = config.get('security', {})
        commands = security_config.get('trusted_commands', [])
        
        if commands:
            logger.info(f"[SecureInterceptor] 从配置加载 {len(commands)} 个可信命令")
    except FileNotFoundError:
        logger.debug(f"[SecureInterceptor] 配置文件不存在: {config_path}")
    except json.JSONDecodeError as e:
        logger.warning(f"[SecureInterceptor] 配置文件解析错误: {e}")
    except Exception as e:
        logger.warning(f"[SecureInterceptor] 加载可信命令失败: {e}")
    
    return commands


def register_trusted_command(command: str):
    """
    动态注册可信命令
    
    Args:
        command: 可信命令字符串，如 'python3 tools/my_script.py'
    """
    global _dynamic_trusted_commands, TRUSTED_COMMANDS
    
    # 检查是否已存在
    if command in TRUSTED_COMMANDS or command in _dynamic_trusted_commands:
        return False
    
    _dynamic_trusted_commands.append(command)
    logger.info(f"[SecureInterceptor] 注册可信命令: {command}")
    return True


def get_all_trusted_commands() -> List[str]:
    """获取所有可信命令（静态 + 动态）"""
    return list(set(TRUSTED_COMMANDS + _dynamic_trusted_commands))


def initialize_security_whitelists(workspace_path: Optional[str] = None):
    """
    初始化安全白名单 - 自动发现 + 配置加载
    
    应在程序启动时调用:
        from web_ui.secure_interceptor import initialize_security_whitelists
        initialize_security_whitelists()
    """
    # 1. 自动发现工具脚本
    discovered = auto_discover_tools(workspace_path)
    for cmd in discovered:
        register_trusted_command(cmd)
    
    # 2. 从配置文件加载
    config_commands = load_trusted_commands_from_config()
    for cmd in config_commands:
        register_trusted_command(cmd)
    
    # 3. 加载文件白名单
    load_whitelist_from_config()
    
    total = len(get_all_trusted_commands())
    logger.info(f"[SecureInterceptor] 白名单初始化完成: {total} 个可信命令, {len(_system_whitelist)} 个文件白名单")


def load_whitelist_from_config(config_path: Optional[str] = None):
    """从配置文件加载白名单"""
    global _system_whitelist
    
    if config_path is None:
        config_path = str(Path.home() / '.nanobot' / 'config.json')
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            
        security_config = config.get('security', {})
        custom_whitelist = security_config.get('system_file_whitelist', [])
        
        if custom_whitelist:
            # 合并自定义白名单（去重）
            _system_whitelist = list(set(_system_whitelist + custom_whitelist))
            logger.info(f"[SecureInterceptor] 已加载 {len(custom_whitelist)} 个自定义白名单项")
    except FileNotFoundError:
        logger.debug(f"[SecureInterceptor] 配置文件不存在: {config_path}")
    except json.JSONDecodeError as e:
        logger.warning(f"[SecureInterceptor] 配置文件解析错误: {e}")
    except Exception as e:
        logger.warning(f"[SecureInterceptor] 加载白名单失败: {e}")


def add_to_whitelist(path: str):
    """动态添加白名单项"""
    global _system_whitelist
    if path not in _system_whitelist:
        _system_whitelist.append(path)


def is_system_file_allowed(path: str, mode: str) -> bool:
    """判断是否为允许的系统文件（仅只读）"""
    # 只允许只读模式
    if mode not in ('r', 'rb', 'rt', ''):
        return False
    
    path_str = str(path)
    for allowed in _system_whitelist:
        if path_str == allowed or path_str.startswith(allowed):
            return True
    return False


def is_trusted_caller() -> bool:
    """检查调用者是否来自可信路径（如系统库）"""
    try:
        # 从当前帧向上遍历调用栈
        frame = sys._getframe(2)  # 跳过 secure_open 和 is_trusted_caller 自身
        while frame:
            filename = frame.f_code.co_filename
            
            # 检查是否来自可信路径
            for trusted in TRUSTED_CALLER_PATHS:
                if trusted in filename:
                    return True
            
            # 检查是否来自 Python 标准库前缀
            if hasattr(sys, 'base_prefix') and filename.startswith(sys.base_prefix):
                return True
            if hasattr(sys, 'prefix') and filename.startswith(sys.prefix):
                return True
            
            frame = frame.f_back
    except Exception:
        pass
    
    return False

# 全局状态
_interceptors_installed = False
_permission_check_callback: Optional[Callable] = None
_audit_callback: Optional[Callable] = None

# 原始函数引用
_original_subprocess_run = subprocess.run
_original_subprocess_call = subprocess.call
_original_subprocess_popen = subprocess.Popen
_original_os_system = os.system
_original_os_popen = os.popen
_original_eval = eval
_original_exec = exec
_original_open = open
_original_import = builtins.__import__


def set_permission_check(callback: Callable):
    """设置权限检查回调函数"""
    global _permission_check_callback
    _permission_check_callback = callback


def set_audit_callback(callback: Callable):
    """设置审计日志回调函数"""
    global _audit_callback
    _audit_callback = callback


def _check_permission(operation: str, params: Dict[str, Any]) -> bool:
    """检查权限"""
    if _permission_check_callback:
        return _permission_check_callback(operation, params)
    
    # 无权限检查回调时，默认拒绝危险操作
    logger.warning(f"[SecureInterceptor] 无权限检查回调，拒绝操作: {operation}")
    return False


def _log_audit(event_type: str, details: Dict[str, Any]):
    """记录审计日志"""
    # 拒绝事件使用 WARNING 级别，其他使用 DEBUG 级别
    if 'denied' in event_type or 'rejected' in event_type:
        log_level = logging.WARNING
    else:
        log_level = logging.DEBUG
    
    if _audit_callback:
        _audit_callback(event_type, details)
    else:
        logger.log(log_level, f"[Audit] {event_type}: {details}")


# ============================================================================
# Phase 2: 危险操作定义 - 直接拒绝，不询问
# 基于 claw 的 DANGEROUS_BASH_PATTERNS 设计
# ============================================================================

# 跨平台代码执行入口点 (来自 claw dangerousPatterns.ts)
CROSS_PLATFORM_CODE_EXEC = [
    # 解释器
    'python', 'python3', 'python2', 'node', 'deno', 'tsx', 
    'ruby', 'perl', 'php', 'lua',
    # 包运行器
    'npx', 'bunx', 'npm run', 'yarn run', 'pnpm run', 'bun run',
    # Shell
    'bash', 'sh', 'zsh', 'fish',
    # 远程命令
    'ssh',
]

# 危险 Bash 模式 (来自 claw)
DANGEROUS_BASH_PATTERNS = CROSS_PLATFORM_CODE_EXEC + [
    'eval', 'exec', 'env', 'xargs', 'sudo',
    # 云资源操作
    'kubectl', 'aws', 'gcloud', 'gsutil',
    # 网络工具
    'curl', 'wget', 'nc', 'ncat',
]

DANGEROUS_OPERATIONS = {
    # 文件系统破坏
    "rm -rf": {"level": "critical", "message": "禁止递归强制删除"},
    "rm -rf /": {"level": "critical", "message": "禁止删除根目录"},
    "mkfs": {"level": "critical", "message": "禁止格式化文件系统"},
    "dd if=": {"level": "critical", "message": "禁止磁盘写入"},
    "shred": {"level": "high", "message": "禁止安全删除工具"},
    
    # 权限与用户
    "chmod 777": {"level": "high", "message": "禁止设置危险权限"},
    "chown root": {"level": "high", "message": "禁止更改所有者为 root"},
    "userdel": {"level": "high", "message": "禁止删除用户"},
    "passwd": {"level": "medium", "message": "禁止修改密码"},
    
    # 网络危险
    "iptables -F": {"level": "critical", "message": "禁止清空防火墙规则"},
    "nc -l": {"level": "medium", "message": "禁止开启网络监听"},
    "curl | bash": {"level": "high", "message": "禁止远程脚本执行"},
    "wget | sh": {"level": "high", "message": "禁止远程脚本执行"},
    
    # 系统危险
    "shutdown": {"level": "critical", "message": "禁止关机"},
    "reboot": {"level": "high", "message": "禁止重启"},
    "init 0": {"level": "critical", "message": "禁止关机"},
    "kill -9 1": {"level": "critical", "message": "禁止杀死 init 进程"},
    
    # 敏感文件访问
    "/etc/shadow": {"level": "critical", "message": "禁止访问密码文件"},
    "/etc/passwd": {"level": "high", "message": "禁止访问用户文件"},
    ".ssh/": {"level": "high", "message": "禁止访问 SSH 目录"},
    ".gnupg/": {"level": "high", "message": "禁止访问 GPG 目录"},
    
    # 代码执行危险 (来自 claw)
    "python -c": {"level": "high", "message": "禁止内联 Python 代码执行"},
    "python3 -c": {"level": "high", "message": "禁止内联 Python 代码执行"},
    "node -e": {"level": "high", "message": "禁止内联 Node 代码执行"},
    "perl -e": {"level": "high", "message": "禁止内联 Perl 代码执行"},
    "ruby -e": {"level": "high", "message": "禁止内联 Ruby 代码执行"},
    "eval ": {"level": "critical", "message": "禁止 eval 命令"},
    "exec ": {"level": "critical", "message": "禁止 exec 命令"},
}

# 安全决策枚举
class SecurityDecision:
    """安全决策"""
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"  # 需要用户确认


# ============================================================================
# Phase 2: 权限规则系统 (基于 claw ToolPermissionContext 设计)
# ============================================================================

# 权限规则来源
class PermissionRuleSource:
    """权限规则来源"""
    USER_SETTINGS = "userSettings"
    PROJECT_SETTINGS = "projectSettings"
    LOCAL_SETTINGS = "localSettings"
    CLI_ARG = "cliArg"
    SESSION = "session"


# 权限行为
class PermissionBehavior:
    """权限行为"""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


# 权限规则类型
PermissionRulesBySource = Dict[str, List[str]]


class PermissionRuleManager:
    """
    权限规则管理器 - 基于 claw 的 ToolPermissionContext 设计
    
    支持三层规则:
    - alwaysAllowRules: 始终允许的操作
    - alwaysDenyRules: 始终拒绝的操作
    - alwaysAskRules: 始终询问用户的操作
    """
    
    def __init__(self):
        self.always_allow_rules: PermissionRulesBySource = {}
        self.always_deny_rules: PermissionRulesBySource = {}
        self.always_ask_rules: PermissionRulesBySource = {}
        self._stripped_dangerous_rules: PermissionRulesBySource = {}
        
    def add_allow_rule(self, source: str, rule: str):
        """添加允许规则"""
        if source not in self.always_allow_rules:
            self.always_allow_rules[source] = []
        if rule not in self.always_allow_rules[source]:
            self.always_allow_rules[source].append(rule)
    
    def add_deny_rule(self, source: str, rule: str):
        """添加拒绝规则"""
        if source not in self.always_deny_rules:
            self.always_deny_rules[source] = []
        if rule not in self.always_deny_rules[source]:
            self.always_deny_rules[source].append(rule)
    
    def add_ask_rule(self, source: str, rule: str):
        """添加询问规则"""
        if source not in self.always_ask_rules:
            self.always_ask_rules[source] = []
        if rule not in self.always_ask_rules[source]:
            self.always_ask_rules[source].append(rule)
    
    def check_rule(self, tool_name: str, content: str = "") -> str:
        """
        检查权限规则
        
        Returns:
            PermissionBehavior: allow, deny, ask
        """
        # 构建规则字符串
        rule_str = f"{tool_name}({content})" if content else tool_name
        
        # 检查拒绝规则 (最高优先级)
        for source, rules in self.always_deny_rules.items():
            for rule in rules:
                if self._rule_matches(rule, tool_name, content):
                    logger.info(f"[Permission] 拒绝规则匹配: {rule} (来源: {source})")
                    return PermissionBehavior.DENY
        
        # 检查允许规则
        for source, rules in self.always_allow_rules.items():
            for rule in rules:
                if self._rule_matches(rule, tool_name, content):
                    # 检查是否为危险权限 (需要剥离)
                    if self._is_stripped(rule, source):
                        continue
                    logger.info(f"[Permission] 允许规则匹配: {rule} (来源: {source})")
                    return PermissionBehavior.ALLOW
        
        # 检查询问规则
        for source, rules in self.always_ask_rules.items():
            for rule in rules:
                if self._rule_matches(rule, tool_name, content):
                    logger.info(f"[Permission] 询问规则匹配: {rule} (来源: {source})")
                    return PermissionBehavior.ASK
        
        # 默认询问
        return PermissionBehavior.ASK
    
    def _rule_matches(self, rule: str, tool_name: str, content: str) -> bool:
        """检查规则是否匹配 - 基于 claw 的规则匹配逻辑"""
        rule = rule.strip()
        
        # 简单匹配: 工具名相同 (无内容限制)
        if rule == tool_name:
            return True
        
        # 通配符匹配
        if rule == "*":
            return True
        
        # 解析规则中的工具名和内容
        rule_tool = rule
        rule_content = ""
        
        if "(" in rule and ")" in rule:
            # 格式: Tool(content)
            rule_tool = rule.split("(")[0]
            rule_content = rule.split("(")[1].split(")")[0]
        elif ":" in rule:
            # 格式: Tool:content
            parts = rule.split(":", 1)
            rule_tool = parts[0]
            rule_content = parts[1]
        
        # 工具名必须匹配
        if rule_tool != tool_name:
            return False
        
        # 如果规则没有内容限制，匹配所有该工具的操作
        if not rule_content or rule_content == "*":
            return True
        
        # 内容匹配
        if not content:
            return False
        
        # 前缀匹配: Bash(python:*) 匹配 Bash(python script.py)
        if rule_content.endswith(":*"):
            prefix = rule_content[:-2]
            return content.startswith(prefix)
        
        # 后缀通配符: Bash(ls*) 匹配 Bash(ls -la)
        if rule_content.endswith("*"):
            prefix = rule_content[:-1]
            return content.startswith(prefix)
        
        # 精确匹配
        return content == rule_content or content.startswith(rule_content + " ")
    
    def _is_stripped(self, rule: str, source: str) -> bool:
        """检查规则是否已被剥离"""
        return source in self._stripped_dangerous_rules and rule in self._stripped_dangerous_rules[source]
    
    def strip_dangerous_permissions(self) -> List[str]:
        """
        剥离危险权限 - 基于 claw 的 stripDangerousPermissionsForAutoMode
        
        Returns:
            被剥离的规则列表
        """
        stripped = []
        
        for source, rules in self.always_allow_rules.items():
            for rule in rules:
                # 检查是否为危险的 Bash 权限
                if self._is_dangerous_bash_permission(rule):
                    if source not in self._stripped_dangerous_rules:
                        self._stripped_dangerous_rules[source] = []
                    self._stripped_dangerous_rules[source].append(rule)
                    stripped.append(rule)
                    logger.warning(f"[Permission] 剥离危险权限: {rule} (来源: {source})")
        
        # 从允许规则中移除被剥离的规则
        for source in self._stripped_dangerous_rules:
            self.always_allow_rules[source] = [
                r for r in self.always_allow_rules.get(source, [])
                if r not in self._stripped_dangerous_rules[source]
            ]
        
        return stripped
    
    def _is_dangerous_bash_permission(self, rule: str) -> bool:
        """
        检查是否为危险的 Bash 权限 - 基于 claw 的 isDangerousBashPermission
        """
        if not rule.startswith("Bash"):
            return False
        
        # 提取规则内容
        if "(" in rule and ")" in rule:
            content = rule.split("(")[1].split(")")[0]
        elif ":" in rule:
            content = rule.split(":", 1)[1]
        else:
            content = ""
        
        # 工具级别允许 (无内容) - 允许所有命令
        if not content or content == "*":
            return True
        
        # 检查危险模式
        content_lower = content.lower().rstrip("*").rstrip(":*")
        for pattern in DANGEROUS_BASH_PATTERNS:
            if content_lower == pattern.lower():
                return True
            if content_lower.startswith(pattern.lower()):
                return True
        
        return False
    
    def restore_dangerous_permissions(self):
        """恢复被剥离的危险权限 - 基于 claw 的 restoreDangerousPermissions"""
        for source, rules in self._stripped_dangerous_rules.items():
            for rule in rules:
                self.add_allow_rule(source, rule)
                logger.info(f"[Permission] 恢复权限: {rule} (来源: {source})")
        
        self._stripped_dangerous_rules = {}


# 全局权限规则管理器
_PERMISSION_RULE_MANAGER: Optional[PermissionRuleManager] = None


def get_permission_rule_manager() -> PermissionRuleManager:
    """获取全局权限规则管理器"""
    global _PERMISSION_RULE_MANAGER
    if _PERMISSION_RULE_MANAGER is None:
        _PERMISSION_RULE_MANAGER = PermissionRuleManager()
    return _PERMISSION_RULE_MANAGER


def check_dangerous_operation(command: str) -> dict:
    """
    检查是否为危险操作 - 鲁棒性增强版
    
    基于 claw 的安全层设计:
    1. 输入验证
    2. 模式匹配
    3. 级别分类
    """
    # 输入验证
    if not command or not isinstance(command, str):
        return {"is_dangerous": False, "error": "invalid_input"}
    
    # 清理输入
    try:
        command_clean = command.strip()
        if not command_clean:
            return {"is_dangerous": False}
        
        command_lower = command_clean.lower()
    except Exception as e:
        logger.warning(f"[Security] 命令清理失败: {e}")
        return {"is_dangerous": False, "error": "cleanup_failed"}
    
    # 检查危险模式
    for pattern, info in DANGEROUS_OPERATIONS.items():
        try:
            if pattern.lower() in command_lower:
                return {
                    "is_dangerous": True,
                    "level": info["level"],
                    "message": info["message"],
                    "pattern": pattern,
                    "command_length": len(command_clean)
                }
        except Exception as e:
            logger.warning(f"[Security] 模式匹配失败: {pattern} - {e}")
            continue
    
    # 检查危险 Bash 模式 (命令注入检测)
    for pattern in DANGEROUS_BASH_PATTERNS:
        try:
            # 检查命令是否以危险模式开头
            if command_lower.startswith(pattern.lower() + " ") or command_lower.startswith(pattern.lower() + "\t"):
                return {
                    "is_dangerous": True,
                    "level": "high",
                    "message": f"禁止使用 {pattern} 命令",
                    "pattern": pattern,
                    "type": "dangerous_pattern"
                }
            # 检查管道/分号后的危险模式
            for separator in ["|", ";", "&&", "||", "\n"]:
                if separator in command_lower:
                    parts = command_lower.split(separator)
                    for part in parts:
                        part = part.strip()
                        if part.startswith(pattern.lower() + " "):
                            return {
                                "is_dangerous": True,
                                "level": "high",
                                "message": f"禁止在管道/复合命令中使用 {pattern}",
                                "pattern": pattern,
                                "type": "injection"
                            }
        except Exception as e:
            logger.warning(f"[Security] Bash 模式检查失败: {pattern} - {e}")
            continue
    
    return {"is_dangerous": False}


def security_check(operation: str, params: dict, strict_mode: bool = True) -> str:
    """
    安全检查 - 鲁棒性增强版
    
    基于 claw 的请求生命周期:
    1. 输入验证
    2. 操作类型检查
    3. 参数验证
    4. 危险操作检测
    5. 权限规则检查
    
    Args:
        operation: 操作类型 (shell_execute, file_read, file_write 等)
        params: 操作参数
        strict_mode: 严格模式（危险操作直接拒绝）
    
    Returns:
        SecurityDecision: allow, deny, confirm
    """
    # 输入验证
    if not operation or not isinstance(operation, str):
        logger.warning("[Security] 无效的操作类型")
        return SecurityDecision.DENY
    
    if not params or not isinstance(params, dict):
        logger.warning("[Security] 无效的参数")
        return SecurityDecision.DENY
    
    operation = operation.strip().lower()
    
    # 已知操作类型列表
    VALID_OPERATIONS = {
        "shell_execute", "file_read", "file_write", "file_delete",
        "plugin_load", "network_request", "database_query"
    }
    
    # 未知操作类型 - 保守处理
    if operation not in VALID_OPERATIONS:
        logger.warning(f"[Security] 未知操作类型: {operation}")
        if strict_mode:
            return SecurityDecision.DENY
        return SecurityDecision.CONFIRM
    
    if operation == "shell_execute":
        command = params.get("command", "")
        
        # 空命令检查
        if not command or not isinstance(command, str):
            logger.warning("[Security] 空或无效的命令")
            return SecurityDecision.DENY
        
        # 命令长度检查 (防止 DoS)
        if len(command) > 10000:
            logger.warning(f"[Security] 命令过长: {len(command)} 字符")
            return SecurityDecision.DENY
        
        # 检查危险操作
        danger_check = check_dangerous_operation(command)
        
        if danger_check.get("is_dangerous"):
            level = danger_check.get("level", "medium")
            message = danger_check.get("message", "危险操作")
            pattern = danger_check.get("pattern", "unknown")
            
            logger.warning(f"[Security] 检测到危险操作: {pattern} - {message}")
            
            # 记录审计日志
            _log_audit('dangerous_operation_detected', {
                'command': command[:200],  # 限制日志长度
                'level': level,
                'pattern': pattern,
                'message': message
            })
            
            if strict_mode or level == "critical":
                return SecurityDecision.DENY
            else:
                return SecurityDecision.CONFIRM
        
        # 检查权限规则
        manager = get_permission_rule_manager()
        rule_result = manager.check_rule("Bash", command)
        
        if rule_result == PermissionBehavior.DENY:
            return SecurityDecision.DENY
        elif rule_result == PermissionBehavior.ALLOW:
            return SecurityDecision.ALLOW
        else:
            return SecurityDecision.CONFIRM
        
    # 文件操作检查
    if operation in ("file_read", "file_write", "file_delete"):
        file_path = params.get("path", "") or params.get("file_path", "")
        
        # 空路径检查
        if not file_path or not isinstance(file_path, str):
            logger.warning("[Security] 空或无效的文件路径")
            return SecurityDecision.DENY
        
        # 路径遍历攻击检查
        if ".." in file_path or file_path.startswith("/"):
            # 检查是否为敏感文件
            danger_check = check_dangerous_operation(file_path)
            
            if danger_check.get("is_dangerous"):
                logger.warning(f"[Security] 敏感文件访问被拒绝: {file_path}")
                return SecurityDecision.DENY
        
        # 检查权限规则
        tool_name = "Read" if operation == "file_read" else "Write"
        manager = get_permission_rule_manager()
        rule_result = manager.check_rule(tool_name, file_path)
        
        if rule_result == PermissionBehavior.DENY:
            return SecurityDecision.DENY
        elif rule_result == PermissionBehavior.ALLOW:
            return SecurityDecision.ALLOW
        else:
            return SecurityDecision.CONFIRM
    
    # 网络请求检查
    if operation == "network_request":
        url = params.get("url", "")
        
        # 检查危险 URL 模式
        dangerous_url_patterns = [
            "file://", "ftp://", "data:",
            "javascript:", "vbscript:"
        ]
        
        for pattern in dangerous_url_patterns:
            if pattern in url.lower():
                logger.warning(f"[Security] 危险 URL 模式: {pattern}")
                return SecurityDecision.DENY
        
        return SecurityDecision.CONFIRM
    
    # 默认: 需要确认
    return SecurityDecision.CONFIRM


# ============================================================================
# 拦截器实现
# ============================================================================

def secure_subprocess_run(*args, **kwargs):
    """安全的 subprocess.run 拦截器"""
    command = args[0] if args else kwargs.get('args', [])
    cmd_str = command if isinstance(command, str) else ' '.join(command)
    
    # Phase 2: 首先检查危险操作（直接拒绝）
    danger_check = check_dangerous_operation(cmd_str)
    if danger_check["is_dangerous"]:
        _log_audit('subprocess_run_dangerous', {
            'command': cmd_str,
            'level': danger_check['level'],
            'message': danger_check['message']
        })
        raise PermissionError(f"危险操作被拒绝: {danger_check['message']} ({danger_check['pattern']})")
    
    # 检查是否为可信命令（静态 + 动态）
    all_trusted = get_all_trusted_commands()
    for trusted in all_trusted:
        if cmd_str.startswith(trusted):
            _log_audit('subprocess_run_allowed_trusted', {'command': cmd_str, 'type': 'subprocess_run'})
            return _original_subprocess_run(*args, **kwargs)
    
    # 检查权限
    params = {
        'command': cmd_str,
        'type': 'subprocess_run',
    }
    
    _log_audit('subprocess_run_request', params)
    
    # 使用新的安全检查
    decision = security_check('shell_execute', params)
    if decision == SecurityDecision.DENY:
        _log_audit('subprocess_run_denied', params)
        raise PermissionError(f"subprocess.run 被安全策略拒绝: {cmd_str[:100]}")
    
    if decision == SecurityDecision.CONFIRM:
        # 需要用户确认
        if not _check_permission('shell_execute', params):
            _log_audit('subprocess_run_denied', params)
            raise PermissionError(f"subprocess.run 被权限系统拒绝: {cmd_str[:100]}")
    
    _log_audit('subprocess_run_allowed', params)
    
    # 调用原始函数
    return _original_subprocess_run(*args, **kwargs)


def secure_subprocess_call(*args, **kwargs):
    """安全的 subprocess.call 拦截器"""
    command = args[0] if args else kwargs.get('args', [])
    
    params = {
        'command': command if isinstance(command, str) else ' '.join(command),
        'type': 'subprocess_call',
    }
    
    _log_audit('subprocess_call_request', params)
    
    if not _check_permission('shell_execute', params):
        _log_audit('subprocess_call_denied', params)
        raise PermissionError(f"subprocess.call 被权限系统拒绝")
    
    return _original_subprocess_call(*args, **kwargs)


def secure_subprocess_popen(*args, **kwargs):
    """安全的 subprocess.Popen 拦截器"""
    command = args[0] if args else kwargs.get('args', [])
    cmd_str = command if isinstance(command, str) else ' '.join(command)
    
    # 检查是否为可信命令（静态 + 动态）
    all_trusted = get_all_trusted_commands()
    for trusted in all_trusted:
        if cmd_str.startswith(trusted):
            _log_audit('subprocess_popen_allowed_trusted', {'command': cmd_str, 'type': 'subprocess_popen'})
            return _original_subprocess_popen(*args, **kwargs)
    
    params = {
        'command': cmd_str,
        'type': 'subprocess_popen',
    }
    
    _log_audit('subprocess_popen_request', params)
    
    if not _check_permission('shell_execute', params):
        _log_audit('subprocess_popen_denied', params)
        raise PermissionError(f"subprocess.Popen 被权限系统拒绝")
    
    return _original_subprocess_popen(*args, **kwargs)


def secure_os_system(command: str):
    """安全的 os.system 拦截器"""
    params = {
        'command': command,
        'type': 'os_system',
    }
    
    _log_audit('os_system_request', params)
    
    if not _check_permission('shell_execute', params):
        _log_audit('os_system_denied', params)
        raise PermissionError(f"os.system 被权限系统拒绝: {command[:100]}")
    
    _log_audit('os_system_allowed', params)
    
    return _original_os_system(command)


def secure_os_popen(command: str, mode: str = 'r'):
    """安全的 os.popen 拦截器"""
    params = {
        'command': command,
        'mode': mode,
        'type': 'os_popen',
    }
    
    _log_audit('os_popen_request', params)
    
    if not _check_permission('shell_execute', params):
        _log_audit('os_popen_denied', params)
        raise PermissionError(f"os.popen 被权限系统拒绝")
    
    return _original_os_popen(command, mode)


def secure_eval(source, globals=None, locals=None):
    """安全的 eval 拦截器"""
    # 获取调用来源判断是否为可信系统模块
    frame = sys._getframe(2)
    caller_file = frame.f_code.co_filename if frame else ''
    
    # 可信路径白名单
    trusted_paths = [
        '/usr/lib/python',
        '/usr/local/lib/python',
        'site-packages',
        'dist-packages',
        'uvicorn',
        'starlette',
        'fastapi',
        'pydantic',
        'asyncio',
    ]
    
    is_trusted = any(trusted in caller_file for trusted in trusted_paths)
    
    params = {
        'source': str(source)[:200],
        'type': 'eval',
        'caller': caller_file,
    }
    
    _log_audit('eval_request', params)
    
    # 可信系统模块直接放行
    if is_trusted:
        _log_audit('eval_allowed_trusted', params)
        return _original_eval(source, globals, locals)
    
    # eval 默认拒绝，除非明确允许
    if not _check_permission('code_execute', params):
        _log_audit('eval_denied', params)
        raise PermissionError("eval 被权限系统拒绝 - 代码执行需要沙箱环境")
    
    _log_audit('eval_allowed', params)
    
    return _original_eval(source, globals, locals)


def secure_exec(source, globals=None, locals=None):
    """安全的 exec 拦截器"""
    # 获取调用来源判断是否为可信系统模块
    frame = sys._getframe(2)
    caller_file = frame.f_code.co_filename if frame else ''
    
    # 可信路径白名单（系统标准库和site-packages）
    trusted_paths = [
        '/usr/lib/python',
        '/usr/local/lib/python',
        'site-packages',
        'dist-packages',
        'uvicorn',
        'starlette',
        'fastapi',
        'pydantic',
        'asyncio',
        'tracemalloc',
        'importlib',
    ]
    
    # 如果来源是可信路径，直接放行
    is_trusted = any(trusted in caller_file for trusted in trusted_paths)
    
    params = {
        'source': str(source)[:200],
        'type': 'exec',
        'caller': caller_file,
    }
    
    _log_audit('exec_request', params)
    
    # 可信系统模块直接放行
    if is_trusted:
        _log_audit('exec_allowed_trusted', params)
        return _original_exec(source, globals, locals)
    
    if not _check_permission('code_execute', params):
        _log_audit('exec_denied', params)
        raise PermissionError("exec 被权限系统拒绝 - 代码执行需要沙箱环境")
    
    _log_audit('exec_allowed', params)
    
    return _original_exec(source, globals, locals)


def secure_open(file, mode='r', *args, **kwargs):
    """安全的 open 拦截器"""
    file_path = str(file)
    
    # 判断操作类型
    is_write = any(m in mode for m in ['w', 'a', 'x', '+'])
    is_read = 'r' in mode or mode == ''
    
    params = {
        'path': file_path,
        'mode': mode,
        'type': 'file_write' if is_write else 'file_read',
    }
    
    _log_audit('open_request', params)
    
    # 优先检查：系统文件白名单（只读）
    if is_system_file_allowed(file_path, mode):
        _log_audit('open_allowed_whitelist', params)
        return _original_open(file, mode, *args, **kwargs)
    
    # 检查可信工作区路径（允许读写）
    for trusted_ws in TRUSTED_WORKSPACE_PATHS:
        if file_path.startswith(trusted_ws):
            _log_audit('open_allowed_workspace', params)
            return _original_open(file, mode, *args, **kwargs)
    
    # 检查信任链：可信调用者读取系统配置文件
    if is_trusted_caller() and is_read:
        # 额外检查：确保不是敏感路径
        if not any(s in file_path for s in ['.ssh', '.gnupg', '.netrc', '.pgpass', 'credentials', 'secrets', '.env']):
            _log_audit('open_allowed_trusted', params)
            return _original_open(file, mode, *args, **kwargs)
    
    # 检查敏感路径
    sensitive_paths = [
        '.ssh', '.gnupg', '.netrc', '.pgpass',
        'credentials', 'secrets', '.env',
        '/etc/passwd', '/etc/shadow', '/etc/sudoers',
    ]
    
    for sensitive in sensitive_paths:
        if sensitive in file_path:
            _log_audit('open_sensitive_denied', params)
            raise PermissionError(f"禁止访问敏感路径: {file_path}")
    
    # 检查权限
    permission_type = 'file_write' if is_write else 'file_read'
    if not _check_permission(permission_type, params):
        _log_audit('open_denied', params)
        raise PermissionError(f"文件操作被权限系统拒绝: {file_path}")
    
    _log_audit('open_allowed', params)
    
    return _original_open(file, mode, *args, **kwargs)


def secure_import(name, globals_arg=None, locals_arg=None, fromlist=(), level=0):
    """安全的 __import__ 拦截器"""
    # 获取调用来源判断是否为可信系统模块
    # 遍历调用栈找到真正的调用者（跳过 importlib 内部帧）
    caller_file = ''
    try:
        for depth in range(1, 10):
            frame = sys._getframe(depth)
            fname = frame.f_code.co_filename
            # 跳过 importlib 内部帧和 secure_interceptor 自己
            if 'importlib' in fname or 'secure_interceptor' in fname:
                continue
            caller_file = fname
            break
    except ValueError:
        pass  # 栈深度不够
    
    # 可信路径白名单
    trusted_paths = [
        '/usr/lib/python',
        '/usr/local/lib/python',
        'site-packages',
        'dist-packages',
        'uvicorn',
        'starlette',
        'fastapi',
        'pydantic',
        'asyncio',
        'anyio',
        # Nanobot 核心工具模块（需要执行命令）
        'tool_executor.py',
        'server_final.py',
        'backend_plugin_manager.py',
        'context_engine_manager.py',
        'cli_anything_adapter.py',
        'sandbox_executor.py',
        'secure_tool_executor.py',
        'comfyui_diagnostics.py',
        'remote_comfyui_diagnostics.py',
    ]
    
    is_trusted = any(trusted in caller_file for trusted in trusted_paths)
    
    # 检查危险模块
    dangerous_modules = [
        'subprocess', 'os.system', 'ctypes', 'cffi',
        'multiprocessing', 'threading',  # 可能被滥用
    ]
    
    # 只对危险模块进行权限检查
    if name in dangerous_modules or any(name.startswith(m + '.') for m in dangerous_modules):
        params = {
            'module': name,
            'fromlist': fromlist,
            'type': 'import',
            'caller': caller_file,
        }
        
        _log_audit('import_dangerous_request', params)
        
        # 可信系统模块直接放行
        if is_trusted:
            _log_audit('import_allowed_trusted', params)
            return _original_import(name, globals_arg, locals_arg, fromlist, level)
        
        if not _check_permission('plugin_load', params):
            _log_audit('import_denied', params)
            logger.warning(f"[SecureInterceptor] 拒绝导入危险模块: {name}")
            raise PermissionError(f"导入危险模块被拒绝: {name}")
        
        _log_audit('import_allowed', params)
    
    return _original_import(name, globals_arg, locals_arg, fromlist, level)


# ============================================================================
# 安装/卸载拦截器
# ============================================================================

def _install_in_subprocess():
    """子进程入口点：自动安装拦截器"""
    import os
    if os.environ.get('NANOBOT_ENFORCE_SECURITY') == '1':
        try:
            install_interceptors(
                intercept_subprocess=True,
                intercept_os=True,
                intercept_eval_exec=True,
                intercept_open=True,
                intercept_import=True,
            )
        except Exception as e:
            logger.error(f"[SecureInterceptor] 子进程安装失败: {e}")


def setup_multiprocessing_support():
    """
    配置 multiprocessing 支持
    
    确保子进程继承拦截器设置
    """
    import multiprocessing
    import sys
    
    # 检测当前启动模式
    current_method = multiprocessing.get_start_method()
    if current_method != 'spawn':
        logger.warning(
            f"[SecureInterceptor] ⚠️ 当前 multiprocessing 启动模式为 '{current_method}'，"
            f"建议使用 'spawn' 模式以确保子进程拦截器生效。"
            f"请在程序启动时调用 multiprocessing.set_start_method('spawn')"
        )
    
    # 设置 spawn 模式，确保子进程重新导入模块
    try:
        multiprocessing.set_start_method('spawn', force=True)
        logger.info("[SecureInterceptor] multiprocessing 使用 spawn 模式")
    except RuntimeError:
        # 已经设置过
        pass
    
    # 设置子进程入口点
    original_context = multiprocessing.get_context('spawn')
    
    # 在子进程启动时自动安装拦截器
    class SecureProcess(multiprocessing.Process):
        def _bootstrap(self):
            _install_in_subprocess()
            return super()._bootstrap()
    
    # 替换默认 Process 类
    multiprocessing.Process = SecureProcess
    logger.info("[SecureInterceptor] multiprocessing.Process 已包装")


def check_interceptor_status() -> Dict[str, Any]:
    """
    检查拦截器状态和配置
    
    Returns:
        状态信息字典
    """
    import multiprocessing
    import sys
    import os
    
    status = {
        'installed': _interceptors_installed,
        'multiprocessing_method': multiprocessing.get_start_method(),
        'enforce_security_env': os.environ.get('NANOBOT_ENFORCE_SECURITY', '0'),
        'platform': sys.platform,
        'warnings': [],
        'recommendations': [],
    }
    
    # 检查 multiprocessing 模式
    if status['multiprocessing_method'] != 'spawn':
        status['warnings'].append(
            f"multiprocessing 使用 '{status['multiprocessing_method']}' 模式，"
            f"子进程拦截器可能失效"
        )
        status['recommendations'].append(
            "在程序启动时调用 multiprocessing.set_start_method('spawn')"
        )
    
    # 检查环境变量
    if status['enforce_security_env'] != '1':
        status['warnings'].append(
            "NANOBOT_ENFORCE_SECURITY 环境变量未设置，子进程可能不会自动安装拦截器"
        )
        status['recommendations'].append(
            "设置环境变量: export NANOBOT_ENFORCE_SECURITY=1"
        )
    
    # 检查 fork 风险（Linux）
    if sys.platform.startswith('linux') and status['multiprocessing_method'] == 'fork':
        status['warnings'].append(
            "Linux 下使用 fork 模式，os.fork() 调用不会被拦截"
        )
        status['recommendations'].append(
            "避免直接调用 os.fork()，使用 multiprocessing.Process 代替"
        )
    
    # 检测 os.fork 调用（如果可能）
    if sys.platform.startswith('linux'):
        import os
        original_fork = getattr(os, 'fork', None)
        if original_fork and not getattr(os, '_fork_wrapped', False):
            # 包装 os.fork 以发出警告
            def _wrapped_fork():
                logger.warning(
                    "[SecureInterceptor] ⚠️ 检测到 os.fork() 调用！"
                    "fork 模式下子进程不会继承拦截器，建议使用 multiprocessing.Process"
                )
                return original_fork()
            
            os.fork = _wrapped_fork
            os._fork_wrapped = True
    
    status['secure'] = (
        status['installed'] and 
        status['multiprocessing_method'] == 'spawn' and
        len(status['warnings']) == 0
    )
    
    return status


def print_interceptor_status():
    """打印拦截器状态报告"""
    status = check_interceptor_status()
    
    print("\n" + "=" * 60)
    print("🔒 安全拦截器状态报告")
    print("=" * 60)
    
    print(f"\n已安装: {'✅' if status['installed'] else '❌'}")
    print(f"multiprocessing 模式: {status['multiprocessing_method']}")
    print(f"平台: {status['platform']}")
    print(f"环境变量 NANOBOT_ENFORCE_SECURITY: {status['enforce_security_env']}")
    
    if status['warnings']:
        print("\n⚠️ 警告:")
        for w in status['warnings']:
            print(f"  - {w}")
    
    if status['recommendations']:
        print("\n💡 建议:")
        for r in status['recommendations']:
            print(f"  - {r}")
    
    print(f"\n{'✅ 安全配置正确' if status['secure'] else '⚠️ 需要调整配置'}")
    print("=" * 60 + "\n")
    
    return status


def install_interceptors(
    permission_check: Callable = None,
    audit_callback: Callable = None,
    intercept_subprocess: bool = True,
    intercept_os: bool = True,
    intercept_eval_exec: bool = True,
    intercept_open: bool = True,
    intercept_import: bool = True,
    support_multiprocessing: bool = True,
    load_config: bool = True,
):
    """
    安装安全拦截器
    
    Args:
        permission_check: 权限检查回调函数
        audit_callback: 审计日志回调函数
        intercept_subprocess: 是否拦截 subprocess
        intercept_os: 是否拦截 os.system/os.popen
        intercept_eval_exec: 是否拦截 eval/exec
        intercept_open: 是否拦截 open
        intercept_import: 是否拦截 __import__
        support_multiprocessing: 是否支持 multiprocessing 子进程
        load_config: 是否从配置文件加载白名单
    """
    global _interceptors_installed
    
    if _interceptors_installed:
        logger.warning("[SecureInterceptor] 拦截器已安装，跳过重复安装")
        return
    
    # 加载白名单配置（自动发现 + 配置文件）
    if load_config:
        initialize_security_whitelists()
    
    # 设置回调
    if permission_check:
        set_permission_check(permission_check)
    if audit_callback:
        set_audit_callback(audit_callback)
    
    # 安装拦截器
    if intercept_subprocess:
        subprocess.run = secure_subprocess_run
        subprocess.call = secure_subprocess_call
        subprocess.Popen = secure_subprocess_popen
        logger.info("[SecureInterceptor] subprocess 拦截器已安装")
    
    if intercept_os:
        os.system = secure_os_system
        os.popen = secure_os_popen
        logger.info("[SecureInterceptor] os.system/os.popen 拦截器已安装")
    
    if intercept_eval_exec:
        builtins.eval = secure_eval
        builtins.exec = secure_exec
        logger.info("[SecureInterceptor] eval/exec 拦截器已安装")
    
    if intercept_open:
        builtins.open = secure_open
        logger.info("[SecureInterceptor] open 拦截器已安装")
    
    if intercept_import:
        builtins.__import__ = secure_import
        logger.info("[SecureInterceptor] __import__ 拦截器已安装")
    
    _interceptors_installed = True
    logger.info("[SecureInterceptor] 所有安全拦截器已安装")
    
    # 配置 multiprocessing 支持
    if support_multiprocessing:
        try:
            setup_multiprocessing_support()
        except Exception as e:
            logger.warning(f"[SecureInterceptor] multiprocessing 支持配置失败: {e}")


def uninstall_interceptors():
    """卸载安全拦截器，恢复原始函数"""
    global _interceptors_installed
    
    if not _interceptors_installed:
        return
    
    # 恢复原始函数
    subprocess.run = _original_subprocess_run
    subprocess.call = _original_subprocess_call
    subprocess.Popen = _original_subprocess_popen
    os.system = _original_os_system
    os.popen = _original_os_popen
    builtins.eval = _original_eval
    builtins.exec = _original_exec
    builtins.open = _original_open
    builtins.__import__ = _original_import
    
    _interceptors_installed = False
    logger.info("[SecureInterceptor] 所有安全拦截器已卸载")


def is_interceptors_installed() -> bool:
    """检查拦截器是否已安装"""
    return _interceptors_installed


# ============================================================================
# 便捷函数
# ============================================================================

def create_permission_check_from_executor(executor) -> Callable:
    """
    从 ToolExecutor 创建权限检查回调
    
    Args:
        executor: ToolExecutor 实例
        
    Returns:
        权限检查回调函数
    """
    def permission_check(operation: str, params: Dict[str, Any]) -> bool:
        # 使用 ToolExecutor 的权限检查
        result = executor._check_permission(operation, params)
        return result is None  # None 表示允许
    
    return permission_check


def create_audit_callback_from_logger(audit_logger) -> Callable:
    """
    从 AuditLogger 创建审计回调
    
    Args:
        audit_logger: AuditLogger 实例
        
    Returns:
        审计回调函数
    """
    def audit_callback(event_type: str, details: Dict[str, Any]):
        audit_logger.log_security_violation(
            violation_type=event_type,
            details=details,
            session_id=details.get('session_id', 'interceptor'),
            risk_level='medium',
        )
    
    return audit_callback


# ============================================================================
# 示例用法
# ============================================================================

if __name__ == '__main__':
    print("=== 安全拦截器测试 ===\n")
    
    # 定义简单的权限检查
    def simple_permission_check(operation: str, params: Dict[str, Any]) -> bool:
        print(f"[权限检查] {operation}: {params}")
        
        # 拒绝危险操作
        if operation in ['shell_execute', 'code_execute']:
            print("  -> 拒绝")
            return False
        
        # 允许文件读取
        if operation == 'file_read':
            print("  -> 允许")
            return True
        
        print("  -> 默认拒绝")
        return False
    
    # 定义审计回调
    def simple_audit(event_type: str, details: Dict[str, Any]):
        print(f"[审计] {event_type}")
    
    # 安装拦截器
    install_interceptors(
        permission_check=simple_permission_check,
        audit_callback=simple_audit,
    )
    
    print("\n1. 测试 subprocess.run:")
    try:
        subprocess.run(['echo', 'hello'])
    except PermissionError as e:
        print(f"  被拦截: {e}")
    
    print("\n2. 测试 eval:")
    try:
        eval("1 + 1")
    except PermissionError as e:
        print(f"  被拦截: {e}")
    
    print("\n3. 测试 open (读取):")
    try:
        with open(__file__, 'r') as f:
            print(f"  成功打开文件，读取前10字符: {f.read(10)}...")
    except PermissionError as e:
        print(f"  被拦截: {e}")
    
    print("\n4. 测试 os.system:")
    try:
        os.system("echo test")
    except PermissionError as e:
        print(f"  被拦截: {e}")
    
    # 卸载
    uninstall_interceptors()
    print("\n=== 拦截器已卸载 ===")
