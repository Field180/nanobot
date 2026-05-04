#!/usr/bin/env python3
"""
权限策略管理器 - PermissionPolicyManager

提供权限策略的持久化存储和自动匹配：
1. 策略存储 (policies.json)
2. 策略匹配（正则、通配符）
3. 策略优先级
4. 策略过期管理

版本: 1.0.0
"""

import json
import re
import fnmatch
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class PolicyDecision(str, Enum):
    """策略决策"""
    ALLOW = "allow"           # 允许
    DENY = "deny"             # 拒绝
    SANDBOX = "sandbox"       # 沙箱执行
    ASK = "ask"               # 询问用户


@dataclass
class PermissionPolicy:
    """权限策略"""
    id: str                           # 策略ID
    name: str                         # 策略名称
    tool_pattern: str                 # 工具名称模式（支持通配符）
    param_patterns: Dict[str, str]    # 参数模式 {param_name: pattern}
    decision: PolicyDecision          # 决策
    priority: int = 0                 # 优先级（越高越优先）
    created_at: float = None          # 创建时间
    expires_at: Optional[float] = None  # 过期时间（None=永不过期）
    description: str = ""             # 描述
    enabled: bool = True              # 是否启用
    match_count: int = 0              # 匹配次数统计
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().timestamp()
    
    def matches(self, tool_name: str, params: Dict[str, Any]) -> bool:
        """检查是否匹配工具调用"""
        # 检查工具名称
        if not self._match_pattern(tool_name, self.tool_pattern):
            return False
        
        # 检查参数模式
        for param_name, pattern in self.param_patterns.items():
            param_value = params.get(param_name)
            if param_value is None:
                return False
            if not self._match_pattern(str(param_value), pattern):
                return False
        
        return True
    
    def _match_pattern(self, value: str, pattern: str) -> bool:
        """匹配模式（支持通配符和正则）"""
        # 检查是否是正则表达式（以 ^ 开头或 $ 结尾）
        if pattern.startswith('^') or pattern.endswith('$'):
            try:
                return bool(re.match(pattern, value))
            except re.error:
                return False
        
        # 使用通配符匹配
        return fnmatch.fnmatch(value, pattern)
    
    def is_expired(self) -> bool:
        """检查是否过期"""
        if self.expires_at is None:
            return False
        return datetime.now().timestamp() > self.expires_at
    
    def to_dict(self) -> Dict:
        return {
            **asdict(self),
            'decision': self.decision.value,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'PermissionPolicy':
        return cls(
            id=data['id'],
            name=data['name'],
            tool_pattern=data['tool_pattern'],
            param_patterns=data.get('param_patterns', {}),
            decision=PolicyDecision(data['decision']),
            priority=data.get('priority', 0),
            created_at=data.get('created_at'),
            expires_at=data.get('expires_at'),
            description=data.get('description', ''),
            enabled=data.get('enabled', True),
            match_count=data.get('match_count', 0),
        )


class PermissionPolicyManager:
    """
    权限策略管理器
    
    管理权限策略的存储、匹配和执行
    """
    
    # 默认策略
    DEFAULT_POLICIES = [
        {
            'id': 'allow_read_workspace',
            'name': '允许读取工作区文件',
            'tool_pattern': 'file_read',
            'param_patterns': {'path': '/workspace/*'},
            'decision': PolicyDecision.ALLOW,
            'priority': 10,
            'description': '允许读取工作区内的文件',
        },
        {
            'id': 'allow_list_dirs',
            'name': '允许列出目录',
            'tool_pattern': 'file_list',
            'param_patterns': {},
            'decision': PolicyDecision.ALLOW,
            'priority': 5,
            'description': '允许列出任何目录',
        },
        {
            'id': 'sandbox_code_exec',
            'name': '代码执行强制沙箱',
            'tool_pattern': 'code_execute',
            'param_patterns': {},
            'decision': PolicyDecision.SANDBOX,
            'priority': 100,
            'description': '所有代码执行都在沙箱中运行',
        },
        {
            'id': 'sandbox_shell',
            'name': 'Shell命令强制沙箱',
            'tool_pattern': 'shell_execute',
            'param_patterns': {},
            'decision': PolicyDecision.SANDBOX,
            'priority': 100,
            'description': '所有Shell命令都在沙箱中运行',
        },
        {
            'id': 'deny_dangerous_commands',
            'name': '拒绝危险命令',
            'tool_pattern': 'shell_execute',
            'param_patterns': {'command': '*rm -rf*'},
            'decision': PolicyDecision.DENY,
            'priority': 200,
            'description': '拒绝危险的rm -rf命令',
        },
        {
            'id': 'deny_internal_network',
            'name': '拒绝内网访问',
            'tool_pattern': 'http_request',
            'param_patterns': {'url': '*localhost*'},
            'decision': PolicyDecision.DENY,
            'priority': 150,
            'description': '拒绝访问localhost',
        },
    ]
    
    def __init__(self, storage_path: Path = None):
        self.storage_path = storage_path or Path.home() / ".nanobot" / "policies.json"
        self.policies: List[PermissionPolicy] = []
        self._file_lock = None
        self._lock_supported = self._check_lock_support()
        self._load_policies()
    
    def _check_lock_support(self) -> bool:
        """检查文件锁支持"""
        import sys
        if sys.platform == 'win32':
            try:
                import msvcrt
                return True
            except ImportError:
                logger.warning("[Policy] Windows 下 msvcrt 不可用，文件锁已禁用")
                return False
        else:
            try:
                import fcntl
                return True
            except ImportError:
                logger.warning("[Policy] fcntl 不可用，文件锁已禁用")
                return False
    
    def _acquire_lock(self, exclusive: bool = True):
        """获取文件锁（跨平台）"""
        import sys
        import os
        
        lock_path = self.storage_path.with_suffix('.lock')
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._file_lock = open(lock_path, 'w')
        
        if not self._lock_supported:
            return  # 无锁模式
        
        try:
            if sys.platform == 'win32':
                # Windows: 使用 msvcrt.locking
                import msvcrt
                lock_mode = msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK
                msvcrt.locking(self._file_lock.fileno(), lock_mode, 1)
            else:
                # Unix: 使用 fcntl.flock
                import fcntl
                lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(self._file_lock.fileno(), lock_type)
        except Exception as e:
            logger.warning(f"[Policy] 获取文件锁失败: {e}")
    
    def _release_lock(self):
        """释放文件锁（跨平台）"""
        if not self._file_lock:
            return
        
        import sys
        
        if self._lock_supported:
            try:
                if sys.platform == 'win32':
                    import msvcrt
                    self._file_lock.seek(0)
                    msvcrt.locking(self._file_lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._file_lock.fileno(), fcntl.LOCK_UN)
            except Exception as e:
                logger.warning(f"[Policy] 释放文件锁失败: {e}")
        
        self._file_lock.close()
        self._file_lock = None
    
    def _load_policies(self):
        """加载策略"""
        # 加载默认策略
        for policy_data in self.DEFAULT_POLICIES:
            policy = PermissionPolicy.from_dict(policy_data)
            self.policies.append(policy)
        
        # 加载用户策略（带文件锁）
        try:
            if self.storage_path.exists():
                self._acquire_lock(exclusive=False)
                try:
                    data = json.loads(self.storage_path.read_text())
                    for policy_data in data.get('policies', []):
                        policy = PermissionPolicy.from_dict(policy_data)
                        # 避免重复
                        if not any(p.id == policy.id for p in self.policies):
                            self.policies.append(policy)
                    logger.info(f"加载了 {len(data.get('policies', []))} 条用户策略")
                finally:
                    self._release_lock()
        except Exception as e:
            logger.warning(f"加载策略失败: {e}")
    
    def _save_policies(self):
        """保存用户策略（带文件锁）"""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            
            self._acquire_lock(exclusive=True)
            try:
                # 只保存非默认策略
                user_policies = [
                    p for p in self.policies 
                    if not any(d['id'] == p.id for d in self.DEFAULT_POLICIES)
                ]
                
                data = {
                    'version': '1.0',
                    'updated_at': datetime.now().isoformat(),
                    'policies': [p.to_dict() for p in user_policies]
                }
                
                self.storage_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
                logger.info(f"保存了 {len(user_policies)} 条策略")
            finally:
                self._release_lock()
        except Exception as e:
            logger.error(f"保存策略失败: {e}")
    
    def add_policy(self, policy: PermissionPolicy) -> bool:
        """添加策略"""
        # 检查ID是否已存在
        if any(p.id == policy.id for p in self.policies):
            logger.warning(f"策略ID已存在: {policy.id}")
            return False
        
        self.policies.append(policy)
        self._save_policies()
        logger.info(f"添加策略: {policy.name}")
        return True
    
    def remove_policy(self, policy_id: str) -> bool:
        """删除策略"""
        for i, policy in enumerate(self.policies):
            if policy.id == policy_id:
                # 不允许删除默认策略
                if any(d['id'] == policy_id for d in self.DEFAULT_POLICIES):
                    logger.warning(f"不能删除默认策略: {policy_id}")
                    return False
                
                del self.policies[i]
                self._save_policies()
                logger.info(f"删除策略: {policy_id}")
                return True
        return False
    
    def update_policy(self, policy_id: str, updates: Dict) -> bool:
        """更新策略"""
        for policy in self.policies:
            if policy.id == policy_id:
                for key, value in updates.items():
                    if key == 'decision' and isinstance(value, str):
                        value = PolicyDecision(value)
                    if hasattr(policy, key):
                        setattr(policy, key, value)
                self._save_policies()
                logger.info(f"更新策略: {policy_id}")
                return True
        return False
    
    def match_policy(self, tool_name: str, params: Dict[str, Any]) -> Optional[PermissionPolicy]:
        """
        匹配策略
        
        返回优先级最高的匹配策略
        """
        matching_policies = []
        
        for policy in self.policies:
            if not policy.enabled:
                continue
            if policy.is_expired():
                continue
            if policy.matches(tool_name, params):
                matching_policies.append(policy)
        
        if not matching_policies:
            return None
        
        # 按优先级排序，返回最高的
        matching_policies.sort(key=lambda p: p.priority, reverse=True)
        best_match = matching_policies[0]
        
        # 更新匹配计数
        best_match.match_count += 1
        
        return best_match
    
    def get_decision(self, tool_name: str, params: Dict[str, Any]) -> PolicyDecision:
        """
        获取决策
        
        如果没有匹配的策略，返回 ASK
        """
        policy = self.match_policy(tool_name, params)
        if policy:
            return policy.decision
        return PolicyDecision.ASK
    
    def get_policy(self, policy_id: str) -> Optional[PermissionPolicy]:
        """获取策略"""
        for policy in self.policies:
            if policy.id == policy_id:
                return policy
        return None
    
    def list_policies(self, enabled_only: bool = True) -> List[PermissionPolicy]:
        """列出所有策略"""
        if enabled_only:
            return [p for p in self.policies if p.enabled and not p.is_expired()]
        return list(self.policies)
    
    def cleanup_expired(self) -> int:
        """清理过期策略"""
        expired_count = 0
        self.policies = [
            p for p in self.policies 
            if not p.is_expired() or any(d['id'] == p.id for d in self.DEFAULT_POLICIES)
        ]
        self._save_policies()
        return expired_count
    
    def export_policies(self) -> str:
        """导出策略为JSON字符串"""
        return json.dumps(
            [p.to_dict() for p in self.policies],
            indent=2, ensure_ascii=False
        )
    
    def import_policies(self, json_str: str, merge: bool = True) -> int:
        """从JSON字符串导入策略"""
        try:
            policies_data = json.loads(json_str)
            imported = 0
            
            for policy_data in policies_data:
                policy = PermissionPolicy.from_dict(policy_data)
                
                if merge:
                    # 合并模式：更新现有或添加新策略
                    existing = self.get_policy(policy.id)
                    if existing:
                        self.update_policy(policy.id, policy.to_dict())
                    else:
                        self.add_policy(policy)
                else:
                    # 覆盖模式：直接添加
                    self.policies.append(policy)
                
                imported += 1
            
            self._save_policies()
            return imported
        except Exception as e:
            logger.error(f"导入策略失败: {e}")
            return 0
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            'total_policies': len(self.policies),
            'enabled_policies': len([p for p in self.policies if p.enabled]),
            'expired_policies': len([p for p in self.policies if p.is_expired()]),
            'total_matches': sum(p.match_count for p in self.policies),
            'by_decision': {
                decision.value: len([p for p in self.policies if p.decision == decision])
                for decision in PolicyDecision
            }
        }


# ============================================================================
# 快捷函数
# ============================================================================

def create_policy(
    tool_pattern: str,
    decision: PolicyDecision,
    param_patterns: Dict[str, str] = None,
    name: str = "",
    priority: int = 0,
    expires_days: int = None,
) -> PermissionPolicy:
    """
    快捷创建策略
    
    Args:
        tool_pattern: 工具名称模式
        decision: 决策
        param_patterns: 参数模式
        name: 策略名称
        priority: 优先级
        expires_days: 过期天数
        
    Returns:
        策略对象
    """
    import uuid
    
    policy_id = f"policy_{uuid.uuid4().hex[:8]}"
    
    expires_at = None
    if expires_days:
        from datetime import timedelta
        expires_at = (datetime.now() + timedelta(days=expires_days)).timestamp()
    
    return PermissionPolicy(
        id=policy_id,
        name=name or f"Policy for {tool_pattern}",
        tool_pattern=tool_pattern,
        param_patterns=param_patterns or {},
        decision=decision,
        priority=priority,
        expires_at=expires_at,
    )


# ============================================================================
# 示例用法
# ============================================================================

if __name__ == '__main__':
    # 创建策略管理器
    manager = PermissionPolicyManager()
    
    print("当前策略:")
    for policy in manager.list_policies():
        print(f"  [{policy.priority}] {policy.name}: {policy.decision.value}")
    
    # 测试匹配
    test_cases = [
        ('file_read', {'path': '/workspace/test.txt'}),
        ('shell_execute', {'command': 'rm -rf /'}),
        ('code_execute', {'code': 'print("hello")'}),
        ('http_request', {'url': 'http://localhost:8080'}),
    ]
    
    print("\n测试匹配:")
    for tool, params in test_cases:
        decision = manager.get_decision(tool, params)
        print(f"  {tool}({params}) -> {decision.value}")
    
    print(f"\n统计: {manager.get_stats()}")
