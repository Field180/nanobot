#!/usr/bin/env python3
"""
安全增强工具执行器 - SecureToolExecutor

集成核心安全模块，对所有工具调用进行安全检查：
1. 输入验证 - 防止注入攻击
2. 输出审计 - 防止敏感信息泄露
3. 命令审计 - 阻止危险命令
4. 网络请求代理 - 防止数据外泄
5. 主人身份验证 - 确保只听授权用户命令

版本: 2.0.0
"""

import os
import subprocess
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import logging

# 添加工作目录到路径
workspace = Path.home() / ".nanobot" / "workspace"
sys.path.insert(0, str(workspace / "core"))

# 导入核心安全模块
try:
    from core_security_guard import (
        CoreSecurityGuard, SecurityAction, SecurityDecision
    )
    SECURITY_AVAILABLE = True
except ImportError:
    SECURITY_AVAILABLE = False
    logging.warning("核心安全模块不可用，将使用基础安全模式")

logger = logging.getLogger(__name__)


class SecureToolExecutor:
    """
    安全增强工具执行器
    
    所有工具调用都经过安全检查：
    - 输入净化
    - 输出审计
    - 权限验证
    - 行为日志
    """
    
    # 工具权限等级要求
    TOOL_TRUST_REQUIREMENTS = {
        'shell_execute': 3,      # 需要中等信任等级
        'file_read': 2,          # 基础信任
        'file_write': 3,         # 中等信任
        'http_request': 4,       # 高信任（可能泄露数据）
        'code_execute': 3,       # 中等信任
        'file_list': 1,          # 最低信任
        'file_search': 2,        # 基础信任
        'system_info': 1,        # 最低信任
        'json_parse': 0,         # 无需信任
        'search_web': 2,         # 基础信任
    }
    
    # 敏感文件路径（禁止读取）
    SENSITIVE_PATHS = [
        '.nanobot/config.json',
        '.ssh/id_rsa',
        '.ssh/id_ed25519',
        '.gnupg/',
        '.netrc',
        '.pgpass',
        'credentials.json',
        'secrets.json',
        '.env',
    ]
    
    def __init__(self, workspace: Path, safe_mode: bool = True,
                 session_id: str = None, user_id: str = 'unknown'):
        self.workspace = workspace
        self.safe_mode = safe_mode
        self.session_id = session_id
        self.user_id = user_id
        self.max_output_length = 5000
        
        # 初始化安全模块
        if SECURITY_AVAILABLE:
            self.security = CoreSecurityGuard(workspace)
        else:
            self.security = None
        
        # 安全日志
        self.security_log: List[Dict] = []
        self.blocked_actions: List[Dict] = []
        
        # 基础危险命令黑名单（备用）
        self.dangerous_commands = [
            'rm -rf', 'mkfs', 'dd if=', 'chmod 777', 'chown root',
            'wget | bash', 'curl | bash', 'curl | sh', '> /dev/sd',
            'killall -9', 'shutdown', 'reboot', 'init 0', 'init 6',
            'systemctl stop', 'iptables -F', 'ufw disable',
            'passwd', 'useradd', 'userdel', 'history -c',
        ]
    
    def _log_action(self, action: str, tool: str, params: Dict, 
                   result: str, details: Dict = None):
        """记录安全日志"""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'tool': tool,
            'params_summary': str(params)[:100],
            'result': result,
            'user_id': self.user_id,
            'session_id': self.session_id,
            'details': details or {}
        }
        self.security_log.append(entry)
        
        # 保持日志大小
        if len(self.security_log) > 1000:
            self.security_log = self.security_log[-500:]
    
    def _log_blocked(self, tool: str, params: Dict, reason: str, 
                    severity: str = 'high'):
        """记录被阻止的操作"""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'tool': tool,
            'params_summary': str(params)[:100],
            'reason': reason,
            'severity': severity,
            'user_id': self.user_id,
        }
        self.blocked_actions.append(entry)
        logger.warning(f"🚫 阻止操作: {tool} - {reason}")
    
    def _get_trust_level(self) -> int:
        """获取当前用户信任等级"""
        if self.security:
            return self.security.master_auth.get_trust_level(self.user_id)
        # 无安全模块时，默认低信任
        return 1 if self.user_id != 'unknown' else 0
    
    def _check_tool_permission(self, tool_name: str) -> Tuple[bool, str]:
        """检查工具调用权限"""
        required_trust = self.TOOL_TRUST_REQUIREMENTS.get(tool_name, 0)
        current_trust = self._get_trust_level()
        
        if current_trust < required_trust:
            return False, f"权限不足: 需要{required_trust}级信任，当前{current_trust}级"
        return True, "权限验证通过"
    
    def _audit_output(self, output: str) -> Tuple[str, bool]:
        """
        审计输出内容，脱敏敏感信息并净化HTML
        
        Returns:
            (处理后的输出, 是否进行了脱敏/净化)
        """
        if not output:
            return output, False
        
        was_modified = False
        
        # 1. HTML内容净化 (XSS防护)
        sanitized_output = self._sanitize_html_content(output)
        if sanitized_output != output:
            was_modified = True
            self._log_action('output_html_sanitized', 'output_audit',
                           {'original_length': len(output)},
                           'HTML内容已净化')
        
        # 2. 安全模块检查
        if self.security:
            decision, redacted = self.security.check_output(sanitized_output)
            if decision.action == SecurityAction.REDACT:
                self._log_action('output_redacted', 'output_audit', 
                               {'original_length': len(sanitized_output)},
                               f'脱敏了{len(sanitized_output)}字符输出')
                return redacted, True
            elif decision.action == SecurityAction.BLOCK:
                self._log_blocked('output', {}, decision.reason, 'critical')
                return "[输出被安全模块阻止: 包含关键敏感信息]", True
        
        # 3. 基础脱敏 (API密钥等)
        final_output, was_redacted = self._basic_output_sanitization(sanitized_output)
        
        return final_output, was_modified or was_redacted
    
    def _sanitize_html_content(self, content: str) -> str:
        """
        净化HTML内容，防止XSS攻击
        
        功能：
        - 移除危险HTML标签 (object, embed, iframe, script)
        - 移除CSS表达式 (expression())
        - 过滤事件处理器 (onclick, onload等)
        - 转义HTML实体 (< > & " ')
        
        Returns:
            净化后的安全内容
        """
        if not content:
            return content
        
        sanitized = content
        
        # 1. 移除危险标签 (object, embed, iframe, script, form)
        dangerous_tags = [
            (r'<object[^>]*>.*?</object>', ''),
            (r'<embed[^>]*>.*?</embed>', ''),
            (r'<iframe[^>]*>.*?</iframe>', ''),
            (r'<script[^>]*>.*?</script>', ''),
            (r'<form[^>]*>.*?</form>', ''),
            (r'<input[^>]*>', ''),
            (r'<button[^>]*>.*?</button>', ''),
        ]
        
        for pattern, replacement in dangerous_tags:
            sanitized = re.sub(pattern, replacement, sanitized, 
                             flags=re.IGNORECASE | re.DOTALL)
        
        # 2. 移除CSS表达式 (IE旧版本XSS向量)
        sanitized = re.sub(
            r'expression\s*\([^)]*\)', 
            '[removed]', 
            sanitized, 
            flags=re.IGNORECASE
        )
        
        # 3. 移除javascript: 协议
        sanitized = re.sub(
            r'javascript:\s*[^\s"\']*',
            'blocked:',
            sanitized,
            flags=re.IGNORECASE
        )
        
        # 3.5 移除 url(javascript:...) - CSS中的XSS向量
        sanitized = re.sub(
            r'url\s*\(\s*["\']?javascript:[^\)]+\)',
            'url(blocked:)',
            sanitized,
            flags=re.IGNORECASE
        )
        
        # 4. 移除data:text/html 和 data:image/svg (SVG XSS)
        sanitized = re.sub(
            r'data:(?:text/html|image/svg)[^\s"\']*',
            'blocked:',
            sanitized,
            flags=re.IGNORECASE
        )
        
        # 5. 移除事件处理器 (on事件)
        sanitized = re.sub(
            r'\s+on\w+\s*=\s*["\'][^"\']*["\']',
            '',
            sanitized,
            flags=re.IGNORECASE
        )
        
        # 6. HTML实体转义 (防止 < > & 等字符被解析为HTML)
        html_escapes = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#x27;',
        }
        
        # 先转义 & 避免双重转义，然后转义其他字符
        for char, entity in html_escapes.items():
            sanitized = sanitized.replace(char, entity)
        
        return sanitized
    
    def _basic_output_sanitization(self, output: str) -> Tuple[str, bool]:
        """基础输出脱敏（无安全模块时使用）"""
        sanitized = output
        redacted = False
        
        # API密钥模式
        patterns = [
            (r'nvapi-[a-zA-Z0-9]{20,}', '[NVIDIA_KEY_REDACTED]'),
            (r'sk-[a-zA-Z0-9]{20,}', '[API_KEY_REDACTED]'),
            (r'Bearer\s+[a-zA-Z0-9_\-\.]+', 'Bearer [TOKEN_REDACTED]'),
            (r'(password|passwd|pwd)["\s:=]+["\']?([^\s"\']{4,})["\']?', 
             r'\1=[REDACTED]'),
        ]
        
        for pattern, replacement in patterns:
            if re.search(pattern, output, re.IGNORECASE):
                sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
                redacted = True
        
        return sanitized, redacted
    
    def execute(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        安全执行工具
        
        所有工具调用都经过：
        1. 权限检查
        2. 输入验证
        3. 执行监控
        4. 输出审计
        """
        # 1. 权限检查
        allowed, reason = self._check_tool_permission(tool_name)
        if not allowed:
            self._log_blocked(tool_name, params, reason, 'permission')
            return {
                'success': False,
                'error': reason,
                'output': None,
                'blocked': True,
                'block_reason': 'permission_denied'
            }
        
        # 2. 输入安全检查
        if self.security:
            input_check = self.security.check_input(
                json.dumps(params), 
                self.user_id, 
                self.session_id
            )
            if input_check.action == SecurityAction.BLOCK:
                self._log_blocked(tool_name, params, input_check.reason, 'input')
                return {
                    'success': False,
                    'error': f"输入安全检查失败: {input_check.reason}",
                    'output': None,
                    'blocked': True,
                    'block_reason': 'input_violation'
                }
        
        # 3. 执行工具
        executors = {
            'shell_execute': self._shell_execute,
            'file_read': self._file_read,
            'file_write': self._file_write,
            'file_list': self._file_list,
            'file_search': self._file_search,
            'http_request': self._http_request,
            'search_web': self._search_web,
            'code_execute': self._code_execute,
            'json_parse': self._json_parse,
            'system_info': self._system_info
        }
        
        if tool_name not in executors:
            return {
                'success': False,
                'error': f'未知工具: {tool_name}',
                'output': None
            }
        
        try:
            result = executors[tool_name](params)
            
            # 4. 输出审计
            if result.get('output'):
                audited_output, was_redacted = self._audit_output(result['output'])
                result['output'] = audited_output
                if was_redacted:
                    result['output_redacted'] = True
            
            # 记录成功执行
            self._log_action('executed', tool_name, params, 
                           'success' if result.get('success') else 'failed')
            
            return result
            
        except Exception as e:
            logger.error(f'工具执行失败 {tool_name}: {e}')
            self._log_action('error', tool_name, params, str(e))
            return {
                'success': False,
                'error': str(e),
                'output': None
            }
    
    def _shell_execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """安全执行Shell命令"""
        command = params.get('command', '')
        timeout = params.get('timeout', 30)
        
        if not command:
            return {'success': False, 'error': '命令为空', 'output': None}
        
        # 安全检查
        if self.security:
            decision = self.security.check_command(command, self.user_id, self.session_id)
            if decision.action == SecurityAction.BLOCK:
                self._log_blocked('shell_execute', {'command': command}, 
                                decision.reason, 'command')
                return {
                    'success': False,
                    'error': f"安全限制: {decision.reason}",
                    'output': None,
                    'blocked': True
                }
        elif self.safe_mode:
            # 基础安全检查
            for dangerous in self.dangerous_commands:
                if dangerous in command:
                    self._log_blocked('shell_execute', {'command': command},
                                    f"危险命令模式: {dangerous}", 'command')
                    return {
                        'success': False,
                        'error': '安全限制: 禁止执行危险命令',
                        'output': None,
                        'blocked': True
                    }
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.workspace)
            )
            
            output = result.stdout
            if result.stderr:
                output += f"\n[STDERR] {result.stderr}"
            
            if len(output) > self.max_output_length:
                output = output[:self.max_output_length] + "\n... (输出已截断)"
            
            return {
                'success': result.returncode == 0,
                'output': output.strip(),
                'return_code': result.returncode
            }
            
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'error': f'命令超时 ({timeout}秒)',
                'output': None
            }
    
    def _file_read(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """安全读取文件"""
        path = params.get('path', '')
        encoding = params.get('encoding', 'utf-8')
        
        if not path:
            return {'success': False, 'error': '路径为空', 'output': None}
        
        # 敏感路径检查
        for sensitive in self.SENSITIVE_PATHS:
            if sensitive in path:
                # 高信任等级可以读取
                if self._get_trust_level() < 5:
                    self._log_blocked('file_read', {'path': path},
                                    f"敏感文件路径: {sensitive}", 'sensitive_file')
                    return {
                        'success': False,
                        'error': f'安全限制: 禁止读取敏感文件',
                        'output': None,
                        'blocked': True
                    }
        
        # 解析路径
        if path.startswith('/'):
            full_path = Path(path).resolve()
            # 检查是否在工作区外
            if not str(full_path).startswith(str(self.workspace.resolve())):
                if self.safe_mode and self._get_trust_level() < 4:
                    return {
                        'success': False,
                        'error': '安全限制: 不允许访问工作区外的文件',
                        'output': None,
                        'blocked': True
                    }
        else:
            full_path = self.workspace / path
        
        if not full_path.exists():
            return {'success': False, 'error': f'文件不存在: {path}', 'output': None}
        
        try:
            with open(full_path, 'r', encoding=encoding) as f:
                content = f.read()
            
            # 输出审计
            content, _ = self._audit_output(content)
            
            if len(content) > self.max_output_length:
                content = content[:self.max_output_length] + "\n... (内容已截断)"
            
            return {
                'success': True,
                'output': content,
                'path': str(full_path),
                'size': full_path.stat().st_size
            }
            
        except UnicodeDecodeError:
            return {'success': False, 'error': '文件编码错误或非文本文件', 'output': None}
        except Exception as e:
            return {'success': False, 'error': str(e), 'output': None}
    
    def _file_write(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """安全写入文件"""
        path = params.get('path', '')
        content = params.get('content', '')
        mode = params.get('mode', 'w')
        
        if not path:
            return {'success': False, 'error': '路径为空', 'output': None}
        
        # 安全检查
        if self.safe_mode:
            # 禁止写入敏感路径
            for sensitive in self.SENSITIVE_PATHS:
                if sensitive in path:
                    if self._get_trust_level() < 5:
                        self._log_blocked('file_write', {'path': path},
                                        f"敏感文件路径: {sensitive}", 'sensitive_file')
                        return {
                            'success': False,
                            'error': '安全限制: 禁止修改敏感文件',
                            'output': None,
                            'blocked': True
                        }
            
            # 禁止路径遍历
            if '..' in path:
                return {'success': False, 'error': '安全限制: 不允许路径遍历', 
                       'output': None, 'blocked': True}
        
        full_path = self.workspace / path
        
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(full_path, mode, encoding='utf-8') as f:
                f.write(content)
            
            return {
                'success': True,
                'output': f'文件已写入: {path}',
                'path': str(full_path),
                'size': len(content)
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e), 'output': None}
    
    def _file_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """列出目录内容"""
        path = params.get('path', '.')
        
        if path == '.':
            full_path = self.workspace
        else:
            full_path = self.workspace / path
        
        if not full_path.exists():
            return {'success': False, 'error': f'目录不存在: {path}', 'output': None}
        
        if not full_path.is_dir():
            return {'success': False, 'error': f'不是目录: {path}', 'output': None}
        
        try:
            items = []
            for item in full_path.iterdir():
                item_type = '📁' if item.is_dir() else '📄'
                size = item.stat().st_size if item.is_file() else '-'
                items.append(f"{item_type} {item.name} ({size})")
            
            output = f"目录: {path}\n" + "\n".join(items[:50])
            if len(items) > 50:
                output += f"\n... 共 {len(items)} 项"
            
            return {
                'success': True,
                'output': output,
                'count': len(items)
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e), 'output': None}
    
    def _file_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """搜索文件内容"""
        pattern = params.get('pattern', '')
        path = params.get('path', '.')
        file_pattern = params.get('file_pattern', '*.py')
        max_results = params.get('max_results', 20)
        
        if not pattern:
            return {'success': False, 'error': '搜索模式为空', 'output': None}
        
        # 检查搜索模式是否包含敏感关键词
        sensitive_patterns = ['password', 'secret', 'token', 'api_key', 'private_key']
        for sp in sensitive_patterns:
            if sp in pattern.lower() and self._get_trust_level() < 5:
                self._log_blocked('file_search', {'pattern': pattern},
                                f"敏感搜索模式: {sp}", 'sensitive_search')
                return {
                    'success': False,
                    'error': '安全限制: 禁止搜索敏感信息',
                    'output': None,
                    'blocked': True
                }
        
        if path == '.':
            search_path = self.workspace
        else:
            search_path = self.workspace / path
        
        if not search_path.exists():
            return {'success': False, 'error': f'路径不存在: {path}', 'output': None}
        
        try:
            results = []
            for file_path in search_path.rglob(file_pattern):
                if 'venv' in str(file_path) or 'env' in str(file_path):
                    continue
                if file_path.name.startswith('.'):
                    continue
                
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line_num, line in enumerate(f, 1):
                            if pattern.lower() in line.lower():
                                rel_path = file_path.relative_to(self.workspace)
                                results.append(f"{rel_path}:{line_num}: {line.strip()[:80]}")
                                if len(results) >= max_results:
                                    break
                except:
                    continue
                
                if len(results) >= max_results:
                    break
            
            if results:
                output = f"🔍 搜索 '{pattern}' 结果:\n" + "\n".join(results)
            else:
                output = f"未找到匹配 '{pattern}' 的内容"
            
            return {
                'success': True,
                'output': output,
                'count': len(results)
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e), 'output': None}
    
    def _http_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """安全HTTP请求"""
        import requests as req
        
        url = params.get('url', '')
        method = params.get('method', 'GET').upper()
        headers = params.get('headers', {})
        body = params.get('body', '')
        timeout = params.get('timeout', 30)
        
        if not url:
            return {'success': False, 'error': 'URL为空', 'output': None}
        
        # 安全检查
        if self.security:
            decision = self.security.check_network_request(url, method, headers, body)
            if decision.action == SecurityAction.BLOCK:
                self._log_blocked('http_request', {'url': url}, 
                                decision.reason, 'network')
                return {
                    'success': False,
                    'error': f"安全限制: {decision.reason}",
                    'output': None,
                    'blocked': True
                }
        elif self.safe_mode:
            # 基础检查：禁止内网访问
            blocked_hosts = ['localhost', '127.0.0.1', '0.0.0.0', '192.168.', '10.', '172.']
            for blocked in blocked_hosts:
                if blocked in url:
                    return {
                        'success': False,
                        'error': '安全限制: 不允许访问内网地址',
                        'output': None,
                        'blocked': True
                    }
        
        try:
            response = req.request(
                method=method,
                url=url,
                headers=headers,
                data=body if body else None,
                timeout=timeout
            )
            
            text = response.text
            if len(text) > self.max_output_length:
                text = text[:self.max_output_length] + "\n... (响应已截断)"
            
            # 输出审计
            text, _ = self._audit_output(text)
            
            return {
                'success': True,
                'output': text,
                'status_code': response.status_code,
                'headers': dict(response.headers)
            }
            
        except req.Timeout:
            return {'success': False, 'error': f'请求超时 ({timeout}秒)', 'output': None}
        except Exception as e:
            return {'success': False, 'error': str(e), 'output': None}
    
    def _search_web(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """网络搜索（需要配置API）"""
        query = params.get('query', '')
        num_results = params.get('num_results', 5)
        
        if not query:
            return {'success': False, 'error': '搜索词为空', 'output': None}
        
        return {
            'success': True,
            'output': f"搜索 '{query}' 需要配置搜索API",
            'query': query,
            'note': '需要配置搜索API'
        }
    
    def _code_execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """安全执行Python代码"""
        code = params.get('code', '')
        globals_dict = params.get('globals', {})
        
        if not code:
            return {'success': False, 'error': '代码为空', 'output': None}
        
        # 安全检查
        if self.safe_mode:
            dangerous_imports = ['os.system', 'subprocess', 'eval', 'exec', 
                               '__import__', 'open(', 'input(']
            for dangerous in dangerous_imports:
                if dangerous in code:
                    # 高信任等级可以执行
                    if self._get_trust_level() < 4:
                        self._log_blocked('code_execute', {'code': code[:50]},
                                        f"危险代码模式: {dangerous}", 'dangerous_code')
                        return {
                            'success': False,
                            'error': '安全限制: 不允许执行危险代码',
                            'output': None,
                            'blocked': True
                        }
        
        try:
            # 创建安全执行环境
            safe_globals = {
                '__builtins__': __builtins__,
                'print': print, 'len': len, 'range': range,
                'list': list, 'dict': dict, 'str': str,
                'int': int, 'float': float, 'bool': bool,
                'sum': sum, 'min': min, 'max': max,
                'sorted': sorted, 'enumerate': enumerate,
                'zip': zip, 'map': map, 'filter': filter,
            }
            safe_globals.update(globals_dict)
            
            # 捕获输出
            import io
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            
            try:
                exec(code, safe_globals)
                output = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            
            # 输出审计
            output, _ = self._audit_output(output)
            
            if len(output) > self.max_output_length:
                output = output[:self.max_output_length] + "\n... (输出已截断)"
            
            return {
                'success': True,
                'output': output if output else '代码执行成功（无输出）'
            }
            
        except Exception as e:
            return {'success': False, 'error': f'执行错误: {str(e)}', 'output': None}
    
    def _json_parse(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """解析JSON"""
        text = params.get('text', '')
        
        if not text:
            return {'success': False, 'error': 'JSON文本为空', 'output': None}
        
        try:
            data = json.loads(text)
            return {
                'success': True,
                'output': json.dumps(data, indent=2, ensure_ascii=False),
                'data': data
            }
        except json.JSONDecodeError as e:
            return {'success': False, 'error': f'JSON解析错误: {str(e)}', 'output': None}
    
    def _system_info(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """获取系统信息"""
        try:
            import platform
            import psutil
            
            cpu_percent = psutil.cpu_percent(interval=0.5)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            info = f"""📊 系统信息:
操作系统: {platform.system()} {platform.release()}
CPU使用率: {cpu_percent}%
内存使用: {memory.percent}% ({memory.used // 1024 // 1024}MB / {memory.total // 1024 // 1024}MB)
磁盘使用: {disk.percent}%
"""
            
            return {
                'success': True,
                'output': info,
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e), 'output': None}
    
    def get_security_report(self) -> Dict[str, Any]:
        """获取安全报告"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'user_id': self.user_id,
            'trust_level': self._get_trust_level(),
            'total_actions': len(self.security_log),
            'blocked_actions': len(self.blocked_actions),
            'security_module_available': SECURITY_AVAILABLE,
            'recent_blocked': self.blocked_actions[-10:],
        }
        
        if self.security:
            report['security_module_report'] = self.security.get_security_report()
        
        return report


# ============================================================================
# 工厂函数
# ============================================================================

def create_secure_executor(workspace: Path = None, 
                          session_id: str = None,
                          user_id: str = 'unknown') -> SecureToolExecutor:
    """创建安全工具执行器"""
    if workspace is None:
        workspace = Path.home() / ".nanobot" / "workspace"
    
    return SecureToolExecutor(workspace, session_id=session_id, user_id=user_id)


# ============================================================================
# CLI 测试
# ============================================================================

if __name__ == "__main__":
    # 测试安全执行器
    executor = create_secure_executor()
    
    print("🔒 安全工具执行器测试")
    print("=" * 50)
    
    # 测试1: 正常命令
    print("\n测试1: 正常命令")
    result = executor.execute('shell_execute', {'command': 'echo "Hello World"'})
    print(f"结果: {result}")
    
    # 测试2: 危险命令
    print("\n测试2: 危险命令")
    result = executor.execute('shell_execute', {'command': 'rm -rf /'})
    print(f"结果: {result}")
    
    # 测试3: 敏感输出
    print("\n测试3: 敏感输出")
    result = executor.execute('shell_execute', 
                             {'command': 'echo "API Key: sk-test123456789"'})
    print(f"结果: {result}")
    
    # 安全报告
    print("\n" + "=" * 50)
    print("安全报告:")
    print(json.dumps(executor.get_security_report(), indent=2, ensure_ascii=False))
