#!/usr/bin/env python3
"""
审计日志模块 - AuditLogger

提供结构化的安全审计日志：
1. JSON格式日志记录
2. 日志轮转
3. 敏感信息脱敏
4. 查询接口

版本: 1.0.0
"""

import json
import gzip
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum
import re

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    """审计事件类型"""
    # 权限相关
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_APPROVED = "permission_approved"
    PERMISSION_DENIED = "permission_denied"
    PERMISSION_TIMEOUT = "permission_timeout"
    PERMISSION_DECISION = "permission_decision"  # 通用决策事件
    
    # 工具执行
    TOOL_EXECUTE = "tool_execute"
    TOOL_SUCCESS = "tool_success"
    TOOL_FAILURE = "tool_failure"
    TOOL_BLOCKED = "tool_blocked"
    TOOL_FAILED = "tool_failed"  # 别名
    
    # 沙箱相关
    SANDBOX_START = "sandbox_start"
    SANDBOX_EXECUTE = "sandbox_execute"
    SANDBOX_STOP = "sandbox_stop"
    SANDBOX_ERROR = "sandbox_error"
    
    # 策略相关
    POLICY_MATCH = "policy_match"
    POLICY_ADD = "policy_add"
    POLICY_REMOVE = "policy_remove"
    
    # 安全事件
    SECURITY_VIOLATION = "security_violation"
    DANGEROUS_COMMAND = "dangerous_command"
    SENSITIVE_ACCESS = "sensitive_access"


@dataclass
class AuditEvent:
    """审计事件"""
    event_id: str
    event_type: AuditEventType
    timestamp: float
    session_id: str
    user_id: str
    tool_name: Optional[str] = None
    params: Optional[Dict] = None
    decision: Optional[str] = None
    result: Optional[str] = None
    details: Optional[Dict] = None
    risk_level: str = "low"
    duration_ms: Optional[float] = None
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        data['event_type'] = self.event_type.value
        # 脱敏参数
        if self.params:
            data['params'] = self._sanitize_params(self.params)
        return data
    
    def _sanitize_params(self, params: Dict) -> Dict:
        """脱敏参数中的敏感信息"""
        sanitized = {}
        sensitive_patterns = [
            (r'(password|passwd|pwd|secret|token|api_key|key)', '[REDACTED]'),
            (r'sk-[a-zA-Z0-9]{20,}', '[API_KEY_REDACTED]'),
            (r'nvapi-[a-zA-Z0-9]{20,}', '[NVIDIA_KEY_REDACTED]'),
            (r'Bearer\s+[a-zA-Z0-9_\-\.]+', 'Bearer [TOKEN_REDACTED]'),
        ]
        
        for key, value in params.items():
            if isinstance(value, str):
                sanitized_value = value
                for pattern, replacement in sensitive_patterns:
                    sanitized_value = re.sub(pattern, replacement, sanitized_value, flags=re.IGNORECASE)
                sanitized[key] = sanitized_value
            else:
                sanitized[key] = value
        
        return sanitized


class AuditLogger:
    """
    审计日志记录器
    
    记录所有安全相关事件，支持告警回调
    """
    
    # 告警级别阈值
    ALERT_THRESHOLDS = {
        'high_risk_count': 5,      # 高风险事件数量阈值
        'high_risk_window': 3600,  # 时间窗口(秒)
        'denied_count': 10,        # 拒绝次数阈值
        'denied_window': 3600,
        'timeout_count': 5,        # 超时次数阈值
        'timeout_window': 1800,
    }
    
    def __init__(
        self,
        log_dir: Path = None,
        max_file_size: int = 10 * 1024 * 1024,  # 10MB
        max_files: int = 10,
        flush_interval: int = 100,
        alert_callbacks: List[callable] = None,
    ):
        self.log_dir = log_dir or Path.home() / ".nanobot" / "audit"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self.max_file_size = max_file_size
        self.max_files = max_files
        self.flush_interval = flush_interval
        
        self.current_file: Optional[Path] = None
        self.current_size: int = 0
        self.event_count: int = 0
        self.buffer: List[AuditEvent] = []
        
        # ========== 告警回调 ==========
        self.alert_callbacks = alert_callbacks or []
        self._alert_history: List[Dict] = []  # 告警历史
        self._recent_events: List[Dict] = []  # 最近事件缓存(用于告警检测)
        
        # 统计
        self.stats = {
            'total_events': 0,
            'by_type': {},
            'by_risk': {'low': 0, 'medium': 0, 'high': 0, 'critical': 0},
            'alerts_triggered': 0,
        }
        
        self._init_log_file()
    
    def _init_log_file(self):
        """初始化日志文件"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        self.current_file = self.log_dir / f"audit_{date_str}.jsonl"
        self.current_size = 0
        
        if self.current_file.exists():
            self.current_size = self.current_file.stat().st_size
    
    def _rotate_if_needed(self):
        """检查并执行日志轮转"""
        if self.current_size > self.max_file_size:
            # 压缩当前文件
            self._compress_file(self.current_file)
            
            # 创建新文件
            self._init_log_file()
            
            # 清理旧文件
            self._cleanup_old_files()
    
    def _compress_file(self, file_path: Path):
        """压缩日志文件"""
        compressed_path = file_path.with_suffix('.jsonl.gz')
        with open(file_path, 'rb') as f_in:
            with gzip.open(compressed_path, 'wb') as f_out:
                f_out.writelines(f_in)
        file_path.unlink()
        logger.info(f"压缩日志: {compressed_path}")
    
    def _cleanup_old_files(self):
        """清理旧日志文件"""
        log_files = sorted(self.log_dir.glob("audit_*.jsonl*"))
        while len(log_files) > self.max_files:
            oldest = log_files.pop(0)
            oldest.unlink()
            logger.info(f"删除旧日志: {oldest}")
    
    def _generate_event_id(self) -> str:
        """生成事件ID"""
        import uuid
        return f"evt_{uuid.uuid4().hex[:12]}"
    
    def log_event(self, event: AuditEvent):
        """记录事件"""
        import time
        current_time = time.time()
        
        self.stats['total_events'] += 1
        self.stats['by_type'][event.event_type.value] = \
            self.stats['by_type'].get(event.event_type.value, 0) + 1
        self.stats['by_risk'][event.risk_level] = \
            self.stats['by_risk'].get(event.risk_level, 0) + 1
        
        # 添加到最近事件缓存
        self._recent_events.append({
            'event': event.to_dict(),
            'timestamp': current_time,
        })
        
        # 清理过期事件（保留1小时）
        self._recent_events = [
            e for e in self._recent_events 
            if current_time - e['timestamp'] < 3600
        ]
        
        # 写入缓冲区
        self.buffer.append(event)
        self.event_count += 1
        
        # 检查告警条件
        self._check_alerts(event)
        
        # 定期刷新
        if len(self.buffer) >= self.flush_interval:
            self._flush()
    
    # ========================================================================
    # 告警系统
    # ========================================================================
    
    def add_alert_callback(self, callback: callable):
        """添加告警回调函数"""
        self.alert_callbacks.append(callback)
    
    def remove_alert_callback(self, callback: callable):
        """移除告警回调函数"""
        if callback in self.alert_callbacks:
            self.alert_callbacks.remove(callback)
    
    def _check_alerts(self, event: AuditEvent):
        """检查是否触发告警"""
        import time
        current_time = time.time()
        
        alerts = []
        
        # 1. 高风险事件告警
        if event.risk_level in ['high', 'critical']:
            high_risk_count = sum(
                1 for e in self._recent_events
                if e['event'].get('risk_level') in ['high', 'critical']
                and current_time - e['timestamp'] < self.ALERT_THRESHOLDS['high_risk_window']
            )
            
            if high_risk_count >= self.ALERT_THRESHOLDS['high_risk_count']:
                alerts.append({
                    'type': 'high_risk_spike',
                    'message': f'高风险事件激增: {high_risk_count}次/小时',
                    'count': high_risk_count,
                    'threshold': self.ALERT_THRESHOLDS['high_risk_count'],
                    'risk_level': 'high',
                })
        
        # 2. 权限拒绝激增
        if event.event_type in [AuditEventType.PERMISSION_DENIED, AuditEventType.TOOL_BLOCKED]:
            denied_count = sum(
                1 for e in self._recent_events
                if e['event'].get('event_type') in ['permission_denied', 'tool_blocked']
                and current_time - e['timestamp'] < self.ALERT_THRESHOLDS['denied_window']
            )
            
            if denied_count >= self.ALERT_THRESHOLDS['denied_count']:
                alerts.append({
                    'type': 'denied_spike',
                    'message': f'权限拒绝激增: {denied_count}次/小时',
                    'count': denied_count,
                    'threshold': self.ALERT_THRESHOLDS['denied_count'],
                    'risk_level': 'medium',
                })
        
        # 3. 审批超时激增
        if event.event_type == AuditEventType.PERMISSION_TIMEOUT:
            timeout_count = sum(
                1 for e in self._recent_events
                if e['event'].get('event_type') == 'permission_timeout'
                and current_time - e['timestamp'] < self.ALERT_THRESHOLDS['timeout_window']
            )
            
            if timeout_count >= self.ALERT_THRESHOLDS['timeout_count']:
                alerts.append({
                    'type': 'timeout_spike',
                    'message': f'审批超时激增: {timeout_count}次/30分钟',
                    'count': timeout_count,
                    'threshold': self.ALERT_THRESHOLDS['timeout_count'],
                    'risk_level': 'medium',
                })
        
        # 4. 危险命令检测
        if event.event_type == AuditEventType.DANGEROUS_COMMAND:
            alerts.append({
                'type': 'dangerous_command',
                'message': f'检测到危险命令: {event.tool_name}',
                'details': event.details,
                'risk_level': 'critical',
            })
        
        # 5. 敏感路径访问
        if event.event_type == AuditEventType.SENSITIVE_ACCESS:
            alerts.append({
                'type': 'sensitive_access',
                'message': f'尝试访问敏感路径',
                'details': event.details,
                'risk_level': 'high',
            })
        
        # 触发告警回调
        for alert in alerts:
            self._trigger_alert(alert, event)
    
    def _trigger_alert(self, alert: Dict, event: AuditEvent):
        """触发告警"""
        import time
        
        # 记录告警历史
        alert_record = {
            'alert': alert,
            'event_id': event.event_id,
            'timestamp': time.time(),
            'session_id': event.session_id,
        }
        self._alert_history.append(alert_record)
        self.stats['alerts_triggered'] += 1
        
        # 调用告警回调
        for callback in self.alert_callbacks:
            try:
                callback(alert, event.to_dict())
            except Exception as e:
                logger.error(f"告警回调执行失败: {e}")
        
        logger.warning(f"[Alert] {alert['type']}: {alert['message']}")
    
    def get_alert_history(self, limit: int = 50) -> List[Dict]:
        """获取告警历史"""
        return self._alert_history[-limit:]
    
    def get_alert_stats(self) -> Dict:
        """获取告警统计"""
        return {
            'total_alerts': self.stats['alerts_triggered'],
            'recent_alerts': len([a for a in self._alert_history if time.time() - a['timestamp'] < 3600]),
            'callbacks_registered': len(self.alert_callbacks),
        }
    
    def set_alert_threshold(self, alert_type: str, count: int, window: int = None):
        """设置告警阈值"""
        key = f'{alert_type}_count'
        if key in self.ALERT_THRESHOLDS:
            self.ALERT_THRESHOLDS[key] = count
        
        window_key = f'{alert_type}_window'
        if window and window_key in self.ALERT_THRESHOLDS:
            self.ALERT_THRESHOLDS[window_key] = window

    # ========================================================================
    # 便捷方法（供 tool_executor 调用）
    # ========================================================================
    
    def log_permission_request(
        self,
        tool_name: str,
        params: Dict,
        session_id: str,
        risk_level: str = 'medium',
        user_id: str = 'unknown',
    ) -> str:
        """
        记录权限请求事件
        
        Returns:
            event_id: 事件ID
        """
        event_id = self._generate_event_id()
        
        event = AuditEvent(
            event_id=event_id,
            event_type=AuditEventType.PERMISSION_REQUEST,
            timestamp=datetime.now(),
            session_id=session_id,
            user_id=user_id,
            tool_name=tool_name,
            params=params,
            risk_level=risk_level,
            details={'status': 'pending'},
        )
        
        self.log_event(event)
        return event_id
    
    def log_permission_decision(
        self,
        event_id: str,
        decision: str,
        tool_name: str = None,
        session_id: str = None,
        user_id: str = 'unknown',
    ):
        """记录权限决策事件"""
        event = AuditEvent(
            event_id=event_id,
            event_type=AuditEventType.PERMISSION_DECISION,
            timestamp=datetime.now(),
            session_id=session_id or 'unknown',
            user_id=user_id,
            tool_name=tool_name,
            risk_level='low' if decision in ['allow_once', 'allow_session', 'allow_always'] else 'medium',
            details={'decision': decision},
        )
        
        self.log_event(event)
    
    def log_tool_execution(
        self,
        tool_name: str,
        params: Dict,
        result: Dict,
        session_id: str,
        duration_ms: float = 0,
        sandbox: bool = False,
        user_id: str = 'unknown',
    ):
        """记录工具执行事件"""
        success = result.get('success', False) if result else False
        
        event = AuditEvent(
            event_id=self._generate_event_id(),
            event_type=AuditEventType.TOOL_EXECUTE if success else AuditEventType.TOOL_FAILED,
            timestamp=datetime.now(),
            session_id=session_id,
            user_id=user_id,
            tool_name=tool_name,
            params=params,
            risk_level='low' if success else 'medium',
            details={
                'success': success,
                'duration_ms': duration_ms,
                'sandbox': sandbox,
                'output_preview': str(result.get('output', ''))[:200] if result else None,
            },
        )
        
        self.log_event(event)
    
    def log_security_violation(
        self,
        violation_type: str,
        details: Dict,
        session_id: str,
        risk_level: str = 'high',
        user_id: str = 'unknown',
    ):
        """记录安全违规事件"""
        event = AuditEvent(
            event_id=self._generate_event_id(),
            event_type=AuditEventType.SECURITY_VIOLATION,
            timestamp=datetime.now(),
            session_id=session_id,
            user_id=user_id,
            risk_level=risk_level,
            details={
                'violation_type': violation_type,
                **details,
            },
        )
        
        self.log_event(event)
    
    def log_sandbox_event(
        self,
        event_type: 'AuditEventType',
        details: Dict,
        session_id: str,
        user_id: str = 'unknown',
    ):
        """记录沙箱事件"""
        event = AuditEvent(
            event_id=self._generate_event_id(),
            event_type=event_type,
            timestamp=datetime.now(),
            session_id=session_id,
            user_id=user_id,
            risk_level='low',
            details=details,
        )
        
        self.log_event(event)
    
    def query_events(
        self,
        event_type: 'AuditEventType' = None,
        tool_name: str = None,
        session_id: str = None,
        risk_level: str = None,
        limit: int = 100,
    ) -> List[Dict]:
        """查询事件"""
        results = []
        
        for event_data in self._recent_events:
            event = event_data['event']
            
            if event_type and event.get('event_type') != event_type.value:
                continue
            if tool_name and event.get('tool_name') != tool_name:
                continue
            if session_id and event.get('session_id') != session_id:
                continue
            if risk_level and event.get('risk_level') != risk_level:
                continue
            
            results.append(event)
            
            if len(results) >= limit:
                break
        
        return results
    
    def get_summary(self) -> Dict:
        """获取摘要"""
        return {
            'total_events': self.stats['total_events'],
            'by_type': dict(self.stats['by_type']),
            'by_risk': dict(self.stats['by_risk']),
            'alerts_triggered': self.stats['alerts_triggered'],
        }
    
    def close(self):
        """关闭并刷新缓冲区"""
        if self.buffer:
            self._flush()
        logger.info("AuditLogger 已关闭")


# ============================================================================
# 默认告警处理器
# ============================================================================

class WebhookAlertHandler:
    """Webhook 告警处理器"""
    
    def __init__(
        self,
        webhook_url: str = None,
        timeout: int = 10,
        headers: Dict = None,
        min_level: str = 'medium',  # 最低告警级别
    ):
        self.webhook_url = webhook_url
        self.timeout = timeout
        self.headers = headers or {'Content-Type': 'application/json'}
        self.min_level = min_level
        self._level_order = {'low': 0, 'medium': 1, 'high': 2, 'critical': 3}
    
    def __call__(self, alert: Dict, event: Dict):
        """处理告警"""
        if not self.webhook_url:
            return
        
        # 检查告警级别
        alert_level = alert.get('risk_level', 'low')
        if self._level_order.get(alert_level, 0) < self._level_order.get(self.min_level, 0):
            return
        
        try:
            import urllib.request
            import urllib.error
            
            payload = json.dumps({
                'alert': alert,
                'event': event,
                'timestamp': time.time(),
            }).encode('utf-8')
            
            req = urllib.request.Request(
                self.webhook_url,
                data=payload,
                headers=self.headers,
                method='POST',
            )
            
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status >= 200 and response.status < 300:
                    logger.info(f"[WebhookAlert] 告警已发送: {alert['type']}")
                else:
                    logger.warning(f"[WebhookAlert] 发送失败: HTTP {response.status}")
        
        except urllib.error.URLError as e:
            logger.error(f"[WebhookAlert] 网络错误: {e}")
        except Exception as e:
            logger.error(f"[WebhookAlert] 发送失败: {e}")


class LogAlertHandler:
    """日志文件告警处理器"""
    
    def __init__(self, log_file: Path = None, min_level: str = 'medium'):
        self.log_file = log_file or Path.home() / ".nanobot" / "alerts.log"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.min_level = min_level
        self._level_order = {'low': 0, 'medium': 1, 'high': 2, 'critical': 3}
    
    def __call__(self, alert: Dict, event: Dict):
        """处理告警"""
        alert_level = alert.get('risk_level', 'low')
        if self._level_order.get(alert_level, 0) < self._level_order.get(self.min_level, 0):
            return
        
        try:
            from datetime import datetime
            timestamp = datetime.now().isoformat()
            line = f"[{timestamp}] [{alert['risk_level'].upper()}] {alert['type']}: {alert['message']}\n"
            
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(line)
            
            logger.info(f"[LogAlert] 告警已记录: {alert['type']}")
        except Exception as e:
            logger.error(f"[LogAlert] 写入失败: {e}")


class SlackAlertHandler:
    """Slack 告警处理器"""
    
    def __init__(self, webhook_url: str, channel: str = None, min_level: str = 'high'):
        self.webhook_url = webhook_url
        self.channel = channel
        self.min_level = min_level
        self._level_order = {'low': 0, 'medium': 1, 'high': 2, 'critical': 3}
    
    def __call__(self, alert: Dict, event: Dict):
        """处理告警"""
        alert_level = alert.get('risk_level', 'low')
        if self._level_order.get(alert_level, 0) < self._level_order.get(self.min_level, 0):
            return
        
        try:
            import urllib.request
            
            # 构建Slack消息
            color_map = {'low': '#36a64f', 'medium': '#f2c744', 'high': '#e74c3c', 'critical': '#8b0000'}
            
            payload = {
                'attachments': [{
                    'color': color_map.get(alert['risk_level'], '#808080'),
                    'title': f"🚨 {alert['type']}",
                    'text': alert['message'],
                    'fields': [
                        {'title': 'Risk Level', 'value': alert['risk_level'].upper(), 'short': True},
                        {'title': 'Count', 'value': str(alert.get('count', 'N/A')), 'short': True},
                    ],
                    'footer': 'Nanobot Security',
                    'ts': int(time.time()),
                }]
            }
            
            if self.channel:
                payload['channel'] = self.channel
            
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    logger.info(f"[SlackAlert] 告警已发送: {alert['type']}")
        
        except Exception as e:
            logger.error(f"[SlackAlert] 发送失败: {e}")


def setup_default_alerts(
    webhook_url: str = None,
    slack_url: str = None,
    log_file: Path = None,
    min_level: str = 'medium',
) -> List[callable]:
    """
    设置默认告警处理器
    
    Args:
        webhook_url: 通用 Webhook URL
        slack_url: Slack Webhook URL
        log_file: 告警日志文件路径
        min_level: 最低告警级别
    
    Returns:
        处理器列表
    """
    handlers = []
    
    # 日志处理器（始终启用）
    log_handler = LogAlertHandler(log_file=log_file, min_level=min_level)
    handlers.append(log_handler)
    
    # Webhook 处理器
    if webhook_url:
        webhook_handler = WebhookAlertHandler(webhook_url=webhook_url, min_level=min_level)
        handlers.append(webhook_handler)
    
    # Slack 处理器
    if slack_url:
        slack_handler = SlackAlertHandler(webhook_url=slack_url, min_level='high')
        handlers.append(slack_handler)
    
    return handlers


def get_audit_logger_with_alerts(
    log_dir: Path = None,
    webhook_url: str = None,
    slack_url: str = None,
    min_alert_level: str = 'medium',
) -> AuditLogger:
    """
    获取带有默认告警的审计日志器
    
    Args:
        log_dir: 日志目录
        webhook_url: Webhook URL
        slack_url: Slack Webhook URL
        min_alert_level: 最低告警级别
    
    Returns:
        配置好的 AuditLogger
    """
    # 设置默认告警处理器
    handlers = setup_default_alerts(
        webhook_url=webhook_url,
        slack_url=slack_url,
        min_level=min_alert_level,
    )
    
    # 创建审计日志器
    audit = AuditLogger(log_dir=log_dir, alert_callbacks=handlers)
    
    return audit


def load_alerts_from_config(config_path: Path = None) -> List[callable]:
    """
    从配置文件加载告警处理器
    
    配置文件格式 (config.json):
    {
        "audit": {
            "alerts": [
                {"type": "webhook", "url": "https://...", "headers": {"X-Token": "xxx"}},
                {"type": "slack", "webhook_url": "https://hooks.slack.com/..."},
                {"type": "log", "file": "/path/to/alerts.log"}
            ],
            "min_level": "medium"
        }
    }
    
    Args:
        config_path: 配置文件路径，默认为 ~/.nanobot/config.json
    
    Returns:
        告警处理器列表
    """
    if config_path is None:
        config_path = Path.home() / ".nanobot" / "config.json"
    
    if not config_path.exists():
        return []
    
    try:
        config = json.loads(config_path.read_text())
        audit_config = config.get('audit', {})
        alerts_config = audit_config.get('alerts', [])
        min_level = audit_config.get('min_level', 'medium')
        
        handlers = []
        
        for alert_cfg in alerts_config:
            alert_type = alert_cfg.get('type', '').lower()
            
            if alert_type == 'webhook':
                handler = WebhookAlertHandler(
                    webhook_url=alert_cfg.get('url'),
                    headers=alert_cfg.get('headers'),
                    min_level=alert_cfg.get('min_level', min_level),
                )
                handlers.append(handler)
                logger.info(f"[Audit] 加载 Webhook 告警器: {alert_cfg.get('url', 'N/A')[:50]}...")
            
            elif alert_type == 'slack':
                handler = SlackAlertHandler(
                    webhook_url=alert_cfg.get('webhook_url'),
                    channel=alert_cfg.get('channel'),
                    min_level=alert_cfg.get('min_level', 'high'),
                )
                handlers.append(handler)
                logger.info("[Audit] 加载 Slack 告警器")
            
            elif alert_type == 'log':
                log_file = alert_cfg.get('file')
                handler = LogAlertHandler(
                    log_file=Path(log_file) if log_file else None,
                    min_level=alert_cfg.get('min_level', min_level),
                )
                handlers.append(handler)
                logger.info(f"[Audit] 加载日志告警器: {log_file or '默认路径'}")
        
        return handlers
    
    except Exception as e:
        logger.error(f"[Audit] 加载告警配置失败: {e}")
        return []


def get_audit_logger_with_config(config_path: Path = None) -> AuditLogger:
    """
    获取带配置文件告警的审计日志器
    
    自动从 ~/.nanobot/config.json 加载告警配置
    
    Args:
        config_path: 配置文件路径
    
    Returns:
        配置好的 AuditLogger
    """
    # 从配置文件加载告警
    handlers = load_alerts_from_config(config_path)
    
    # 创建审计日志器
    audit = AuditLogger(alert_callbacks=handlers if handlers else None)
    
    return audit


# ============================================================================
# 全局实例
# ============================================================================

_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """获取全局审计日志记录器"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


# ============================================================================
# 示例用法
# ============================================================================

if __name__ == '__main__':
    audit = AuditLogger()
    
    # 记录权限请求
    event_id = audit.log_permission_request(
        tool_name="shell_execute",
        params={"command": "ls -la"},
        session_id="test_session",
        risk_level="medium",
    )
    
    # 记录决策
    audit.log_permission_decision(
        event_id=event_id,
        decision="allow_once",
        tool_name="shell_execute",
        session_id="test_session",
    )
    
    # 记录工具执行
    audit.log_tool_execution(
        tool_name="shell_execute",
        params={"command": "ls -la"},
        result={"success": True, "output": "file1.txt\nfile2.txt"},
        session_id="test_session",
        duration_ms=150.5,
        sandbox=True,
    )
    
    # 记录安全违规
    audit.log_security_violation(
        violation_type="dangerous_command",
        details={"command": "rm -rf /"},
        session_id="test_session",
        risk_level="critical",
    )
    
    # 查询事件
    print("查询结果:")
    events = audit.query_events(tool_name="shell_execute", limit=10)
    for e in events:
        print(f"  {e['event_type']}: {e.get('tool_name')}")
    
    print(f"\n摘要: {audit.get_summary()}")
    print(f"统计: {audit.get_stats()}")
    
    audit.close()
