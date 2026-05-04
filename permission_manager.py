"""
权限请求管理器
处理模型请求权限的审核、存储和响应
"""

import json
import time
import uuid
import asyncio
from typing import Dict, List, Optional, Literal
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class PermissionType(str, Enum):
    """权限类型"""
    INTERNET = "internet"           # 访问互联网
    WEB_BROWSE = "web_browse"       # 浏览网页
    FILE_READ = "file_read"         # 读取文件
    FILE_WRITE = "file_write"       # 写入文件
    CODE_EXEC = "code_exec"         # 执行代码
    SYSTEM_CMD = "system_cmd"       # 系统命令
    API_CALL = "api_call"           # 调用外部API
    DANGEROUS = "dangerous"         # 危险操作
    # 新增权限类型
    DOCKER_EXEC = "docker_exec"     # Docker容器操作
    DATABASE_ACCESS = "database"    # 数据库访问
    PLUGIN_LOAD = "plugin_load"     # 加载插件
    MODEL_CALL = "model_call"       # 调用外部模型
    PIPELINE_RUN = "pipeline_run"   # 运行流水线
    BACKUP_RESTORE = "backup"       # 备份/恢复操作


class PermissionDecision(str, Enum):
    """权限决策"""
    DENY = "deny"                   # 拒绝
    ALLOW_ONCE = "allow_once"       # 仅本次允许
    ALLOW_SESSION = "allow_session" # 本次会话允许
    ALLOW_ALWAYS = "allow_always"   # 始终允许


@dataclass
class PermissionRequest:
    """权限请求"""
    request_id: str
    permission_type: PermissionType
    title: str
    description: str
    details: str                    # 详细信息（如URL、文件路径等）
    session_id: str
    created_at: float
    timeout: int = 30               # 超时秒数
    status: str = "pending"         # pending, approved, denied, timeout
    decision: Optional[PermissionDecision] = None
    decided_at: Optional[float] = None


@dataclass
class PermissionRule:
    """权限规则（持久化的决策）"""
    permission_type: PermissionType
    pattern: str                    # 匹配模式（如域名、路径模式）
    decision: PermissionDecision
    created_at: float
    expires_at: Optional[float] = None  # None表示永不过期


class PermissionManager:
    """权限管理器"""
    
    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or Path.home() / ".nanobot" / "permissions.json"
        self.pending_requests: Dict[str, PermissionRequest] = {}
        self.session_permissions: Dict[str, Dict[str, PermissionDecision]] = {}
        self.persistent_rules: List[PermissionRule] = []
        self._request_events: Dict[str, asyncio.Event] = {}
        
        self._load_rules()
    
    def _load_rules(self):
        """加载持久化规则"""
        try:
            if self.storage_path.exists():
                data = json.loads(self.storage_path.read_text())
                for rule_data in data.get("rules", []):
                    rule = PermissionRule(
                        permission_type=PermissionType(rule_data["permission_type"]),
                        pattern=rule_data["pattern"],
                        decision=PermissionDecision(rule_data["decision"]),
                        created_at=rule_data["created_at"],
                        expires_at=rule_data.get("expires_at")
                    )
                    self.persistent_rules.append(rule)
                logger.info(f"加载了 {len(self.persistent_rules)} 条权限规则")
        except Exception as e:
            logger.warning(f"加载权限规则失败: {e}")
    
    def _save_rules(self):
        """保存持久化规则"""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "rules": [asdict(rule) for rule in self.persistent_rules]
            }
            tmp = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.storage_path)
        except Exception as e:
            logger.error(f"保存权限规则失败: {e}")
    
    def create_request(
        self,
        permission_type: PermissionType,
        title: str,
        description: str,
        details: str,
        session_id: str,
        timeout: int = 30
    ) -> PermissionRequest:
        """创建权限请求"""
        request_id = str(uuid.uuid4())[:8]
        
        request = PermissionRequest(
            request_id=request_id,
            permission_type=permission_type,
            title=title,
            description=description,
            details=details,
            session_id=session_id,
            created_at=time.time(),
            timeout=timeout
        )
        
        self.pending_requests[request_id] = request
        self._request_events[request_id] = asyncio.Event()
        
        logger.info(f"创建权限请求: {request_id} - {permission_type.value} - {title}")
        return request
    
    def check_auto_permission(
        self,
        permission_type: PermissionType,
        details: str,
        session_id: str
    ) -> Optional[PermissionDecision]:
        """检查是否有自动授权规则"""
        
        # 1. 检查持久化规则
        for rule in self.persistent_rules:
            if rule.permission_type == permission_type:
                # 检查模式匹配
                if self._match_pattern(rule.pattern, details):
                    # 检查是否过期
                    if rule.expires_at and time.time() > rule.expires_at:
                        continue
                    return rule.decision
        
        # 2. 检查会话级权限
        if session_id in self.session_permissions:
            key = f"{permission_type.value}:{details}"
            if key in self.session_permissions[session_id]:
                return self.session_permissions[session_id][key]
        
        return None
    
    def _match_pattern(self, pattern: str, details: str) -> bool:
        """匹配模式"""
        import fnmatch
        # 支持通配符匹配
        if fnmatch.fnmatch(details, pattern):
            return True
        # 支持包含匹配
        if pattern in details:
            return True
        # 支持域名匹配
        if pattern.startswith("*."):
            domain = pattern[2:]
            if domain in details:
                return True
        return False
    
    async def wait_for_decision(
        self,
        request_id: str,
        timeout: Optional[float] = None
    ) -> Optional[PermissionDecision]:
        """等待权限决策"""
        if request_id not in self.pending_requests:
            return None
        
        request = self.pending_requests[request_id]
        event = self._request_events.get(request_id)
        
        if not event:
            return None
        
        timeout = timeout or request.timeout
        
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            request.status = "timeout"
            logger.warning(f"权限请求超时: {request_id}")
            return None
        
        return request.decision
    
    def make_decision(
        self,
        request_id: str,
        decision: PermissionDecision
    ) -> bool:
        """做出权限决策"""
        if request_id not in self.pending_requests:
            return False
        
        request = self.pending_requests[request_id]
        request.decision = decision
        request.decided_at = time.time()
        request.status = "approved" if decision != PermissionDecision.DENY else "denied"
        
        # 根据决策类型保存规则
        if decision == PermissionDecision.ALLOW_SESSION:
            # 保存到会话权限
            if request.session_id not in self.session_permissions:
                self.session_permissions[request.session_id] = {}
            key = f"{request.permission_type.value}:{request.details}"
            self.session_permissions[request.session_id][key] = decision
        
        elif decision == PermissionDecision.ALLOW_ALWAYS:
            # 保存到持久化规则
            rule = PermissionRule(
                permission_type=request.permission_type,
                pattern=request.details,
                decision=decision,
                created_at=time.time()
            )
            self.persistent_rules.append(rule)
            self._save_rules()
        
        # 触发事件
        if request_id in self._request_events:
            self._request_events[request_id].set()
        
        logger.info(f"权限决策: {request_id} -> {decision.value}")
        return True
    
    def get_pending_requests(self, session_id: Optional[str] = None) -> List[PermissionRequest]:
        """获取待处理的请求"""
        requests = list(self.pending_requests.values())
        if session_id:
            requests = [r for r in requests if r.session_id == session_id]
        return [r for r in requests if r.status == "pending"]
    
    def clear_session_permissions(self, session_id: str):
        """清除会话权限"""
        if session_id in self.session_permissions:
            del self.session_permissions[session_id]
    
    def get_permission_rules(self) -> List[Dict]:
        """获取所有权限规则"""
        return [asdict(rule) for rule in self.persistent_rules]
    
    def delete_rule(self, index: int) -> bool:
        """删除权限规则"""
        if 0 <= index < len(self.persistent_rules):
            del self.persistent_rules[index]
            self._save_rules()
            return True
        return False
    
    def cleanup_expired(self):
        """清理过期的规则和请求"""
        now = time.time()
        
        # 清理过期规则
        self.persistent_rules = [
            rule for rule in self.persistent_rules
            if not rule.expires_at or rule.expires_at > now
        ]
        
        # 清理超时请求
        for request_id, request in list(self.pending_requests.items()):
            if request.status == "pending" and now - request.created_at > request.timeout:
                request.status = "timeout"
                if request_id in self._request_events:
                    self._request_events[request_id].set()
        
        self._save_rules()


# 权限类型配置
PERMISSION_CONFIG = {
    PermissionType.INTERNET: {
        "icon": "internet",
        "title_template": "请求访问互联网",
        "risk_level": "low"
    },
    PermissionType.WEB_BROWSE: {
        "icon": "internet",
        "title_template": "请求浏览网页",
        "risk_level": "low"
    },
    PermissionType.FILE_READ: {
        "icon": "file",
        "title_template": "请求读取文件",
        "risk_level": "medium"
    },
    PermissionType.FILE_WRITE: {
        "icon": "file",
        "title_template": "请求写入文件",
        "risk_level": "high"
    },
    PermissionType.CODE_EXEC: {
        "icon": "code",
        "title_template": "请求执行代码",
        "risk_level": "high"
    },
    PermissionType.SYSTEM_CMD: {
        "icon": "system",
        "title_template": "请求执行系统命令",
        "risk_level": "danger"
    },
    PermissionType.API_CALL: {
        "icon": "internet",
        "title_template": "请求调用外部API",
        "risk_level": "medium"
    },
    PermissionType.DANGEROUS: {
        "icon": "danger",
        "title_template": "请求执行危险操作",
        "risk_level": "danger"
    }
}


def get_permission_config(perm_type: PermissionType) -> Dict:
    """获取权限类型配置"""
    return PERMISSION_CONFIG.get(perm_type, {
        "icon": "system",
        "title_template": "请求权限",
        "risk_level": "medium"
    })
