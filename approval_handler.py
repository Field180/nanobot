#!/usr/bin/env python3
"""
审批处理器 - ApprovalHandler

提供权限审批的交互层抽象：
1. ApprovalHandler 接口定义
2. CLIApprovalHandler - 终端交互版
3. WebSocketApprovalHandler - Web实时推送版
4. SilentApprovalHandler - 静默模式（自动拒绝/允许）

版本: 1.0.0
"""

import asyncio
import json
import logging
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger(__name__)


# ============================================================================
# 数据模型
# ============================================================================

class ApprovalDecision(str, Enum):
    """审批决策"""
    DENY = "deny"                   # 拒绝
    ALLOW_ONCE = "allow_once"       # 仅本次允许
    ALLOW_SESSION = "allow_session" # 本次会话允许
    ALLOW_ALWAYS = "allow_always"   # 始终允许
    SANDBOX = "sandbox"             # 在沙箱中执行


@dataclass
class ApprovalRequest:
    """审批请求"""
    request_id: str
    tool_name: str
    permission_type: str
    title: str
    description: str
    details: str
    risk_level: str                 # low, medium, high, critical
    session_id: str
    created_at: float
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ApprovalResponse:
    """审批响应"""
    request_id: str
    decision: ApprovalDecision
    reason: Optional[str] = None
    decided_at: float = None
    user_id: str = "default"
    
    def __post_init__(self):
        if self.decided_at is None:
            self.decided_at = datetime.now().timestamp()


# ============================================================================
# 审批处理器接口
# ============================================================================

class ApprovalHandler(ABC):
    """
    审批处理器抽象基类
    
    不同环境（CLI、Web、API）可以实现自己的交互方式
    """
    
    @abstractmethod
    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        """
        请求审批
        
        Args:
            request: 审批请求
            
        Returns:
            审批响应
        """
        pass
    
    @abstractmethod
    def cancel_request(self, request_id: str):
        """取消待处理的请求"""
        pass
    
    @abstractmethod
    def get_pending_requests(self) -> List[ApprovalRequest]:
        """获取待处理的请求列表"""
        pass
    
    def format_request(self, request: ApprovalRequest) -> str:
        """格式化请求为可读文本"""
        risk_emoji = {
            'low': '🟢',
            'medium': '🟡',
            'high': '🟠',
            'critical': '🔴'
        }
        emoji = risk_emoji.get(request.risk_level, '⚪')
        
        lines = [
            f"\n{'='*60}",
            f"  {emoji} 权限审批请求",
            f"{'='*60}",
            f"  工具: {request.tool_name}",
            f"  类型: {request.permission_type}",
            f"  风险: {request.risk_level.upper()}",
            f"{'-'*60}",
            f"  {request.title}",
            f"  {request.description}",
            f"  详情: {request.details}",
            f"{'='*60}",
        ]
        return '\n'.join(lines)


# ============================================================================
# CLI 审批处理器（终端交互）
# ============================================================================

class CLIApprovalHandler(ApprovalHandler):
    """
    终端交互审批处理器
    
    在终端显示请求并等待用户输入
    """
    
    def __init__(self, auto_timeout: int = 60, default_decision: ApprovalDecision = None):
        """
        Args:
            auto_timeout: 自动超时秒数（超时后使用默认决策）
            default_decision: 超时时的默认决策（None表示必须等待用户）
        """
        self.auto_timeout = auto_timeout
        self.default_decision = default_decision
        self.pending_requests: Dict[str, ApprovalRequest] = {}
        self.session_decisions: Dict[str, ApprovalDecision] = {}  # 会话级决策缓存
        self.always_decisions: Dict[str, ApprovalDecision] = {}   # 永久决策缓存
        
        # 统计
        self.stats = {
            'total_requests': 0,
            'allowed': 0,
            'denied': 0,
            'sandboxed': 0,
            'timeouts': 0,
        }
    
    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        """请求审批（终端交互）"""
        self.pending_requests[request.request_id] = request
        self.stats['total_requests'] += 1
        
        # 检查是否有缓存的决策
        cached = self._check_cached_decision(request)
        if cached:
            logger.info(f"[Approval] 使用缓存决策: {cached.decision.value}")
            return cached
        
        try:
            # 显示请求
            print(self.format_request(request))
            
            # 显示选项
            print("\n  选择操作:")
            print("  [1] 拒绝 (deny)")
            print("  [2] 本次允许 (allow_once)")
            print("  [3] 会话允许 (allow_session)")
            print("  [4] 始终允许 (allow_always)")
            print("  [5] 沙箱执行 (sandbox)")
            print("  [0] 取消")
            
            # 等待用户输入
            response = await self._wait_for_input(request)
            
            # 更新统计
            if response.decision == ApprovalDecision.DENY:
                self.stats['denied'] += 1
            elif response.decision == ApprovalDecision.SANDBOX:
                self.stats['sandboxed'] += 1
            else:
                self.stats['allowed'] += 1
            
            return response
            
        except asyncio.TimeoutError:
            self.stats['timeouts'] += 1
            if self.default_decision:
                return ApprovalResponse(
                    request_id=request.request_id,
                    decision=self.default_decision,
                    reason="超时自动决策"
                )
            return ApprovalResponse(
                request_id=request.request_id,
                decision=ApprovalDecision.DENY,
                reason="审批超时"
            )
        finally:
            self.pending_requests.pop(request.request_id, None)
    
    def _check_cached_decision(self, request: ApprovalRequest) -> Optional[ApprovalResponse]:
        """检查是否有缓存的决策"""
        # 检查永久决策
        cache_key = f"{request.tool_name}:{request.permission_type}"
        if cache_key in self.always_decisions:
            return ApprovalResponse(
                request_id=request.request_id,
                decision=self.always_decisions[cache_key],
                reason="使用永久决策规则"
            )
        
        # 检查会话决策
        session_key = f"{request.session_id}:{cache_key}"
        if session_key in self.session_decisions:
            return ApprovalResponse(
                request_id=request.request_id,
                decision=self.session_decisions[session_key],
                reason="使用会话决策规则"
            )
        
        return None
    
    async def _wait_for_input(self, request: ApprovalRequest) -> ApprovalResponse:
        """等待用户输入"""
        loop = asyncio.get_running_loop()
        
        def get_input():
            while True:
                try:
                    choice = input("\n  请选择 [0-5]: ").strip()
                    return self._parse_choice(choice, request)
                except KeyboardInterrupt:
                    return ApprovalResponse(
                        request_id=request.request_id,
                        decision=ApprovalDecision.DENY,
                        reason="用户取消"
                    )
                except Exception as e:
                    print(f"  无效输入: {e}")
                    continue
        
        # 使用线程池运行阻塞的输入
        if self.auto_timeout > 0 and self.default_decision:
            try:
                response = await asyncio.wait_for(
                    loop.run_in_executor(None, get_input),
                    timeout=self.auto_timeout
                )
                return response
            except asyncio.TimeoutError:
                raise
        else:
            return await loop.run_in_executor(None, get_input)
    
    def _parse_choice(self, choice: str, request: ApprovalRequest) -> ApprovalResponse:
        """解析用户选择"""
        decision_map = {
            '0': ApprovalDecision.DENY,
            '1': ApprovalDecision.DENY,
            '2': ApprovalDecision.ALLOW_ONCE,
            '3': ApprovalDecision.ALLOW_SESSION,
            '4': ApprovalDecision.ALLOW_ALWAYS,
            '5': ApprovalDecision.SANDBOX,
        }
        
        decision = decision_map.get(choice)
        if not decision:
            # 尝试解析文本
            choice_lower = choice.lower()
            if choice_lower in ['y', 'yes', 'allow', '允许']:
                decision = ApprovalDecision.ALLOW_ONCE
            elif choice_lower in ['n', 'no', 'deny', '拒绝']:
                decision = ApprovalDecision.DENY
            elif choice_lower in ['s', 'sandbox', '沙箱']:
                decision = ApprovalDecision.SANDBOX
            elif choice_lower in ['a', 'always', '始终']:
                decision = ApprovalDecision.ALLOW_ALWAYS
            else:
                decision = ApprovalDecision.DENY
        
        # 缓存决策
        cache_key = f"{request.tool_name}:{request.permission_type}"
        if decision == ApprovalDecision.ALLOW_ALWAYS:
            self.always_decisions[cache_key] = decision
        elif decision == ApprovalDecision.ALLOW_SESSION:
            session_key = f"{request.session_id}:{cache_key}"
            self.session_decisions[session_key] = decision
        
        return ApprovalResponse(
            request_id=request.request_id,
            decision=decision
        )
    
    def cancel_request(self, request_id: str):
        """取消请求"""
        self.pending_requests.pop(request_id, None)
    
    def get_pending_requests(self) -> List[ApprovalRequest]:
        """获取待处理请求"""
        return list(self.pending_requests.values())
    
    def clear_session_cache(self, session_id: str = None):
        """清除会话缓存"""
        if session_id:
            keys_to_remove = [k for k in self.session_decisions if k.startswith(f"{session_id}:")]
            for k in keys_to_remove:
                del self.session_decisions[k]
        else:
            self.session_decisions.clear()
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            **self.stats,
            'pending_count': len(self.pending_requests),
            'session_rules': len(self.session_decisions),
            'permanent_rules': len(self.always_decisions),
        }


# ============================================================================
# WebSocket 审批处理器（Web实时推送）
# ============================================================================

class WebSocketApprovalHandler(ApprovalHandler):
    """
    WebSocket 实时推送审批处理器
    
    通过 WebSocket 推送请求到前端，等待前端响应
    """
    
    def __init__(self, websocket_manager=None, timeout: int = 120):
        """
        Args:
            websocket_manager: WebSocket 连接管理器
            timeout: 等待响应超时时间
        """
        self.websocket_manager = websocket_manager
        self.timeout = timeout
        self.pending_requests: Dict[str, ApprovalRequest] = {}
        self.response_events: Dict[str, asyncio.Event] = {}
        self.responses: Dict[str, ApprovalResponse] = {}
        
        # 统计
        self.stats = {
            'total_requests': 0,
            'websocket_sent': 0,
            'websocket_failed': 0,
            'timeouts': 0,
        }
    
    def set_websocket_manager(self, manager):
        """设置 WebSocket 管理器"""
        self.websocket_manager = manager
    
    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        """请求审批（WebSocket推送）"""
        self.pending_requests[request.request_id] = request
        self.stats['total_requests'] += 1
        
        # 创建等待事件
        event = asyncio.Event()
        self.response_events[request.request_id] = event
        
        try:
            # 推送到 WebSocket
            if self.websocket_manager:
                await self._push_to_websocket(request)
                self.stats['websocket_sent'] += 1
            else:
                logger.warning("WebSocket管理器未设置，使用默认拒绝")
                return ApprovalResponse(
                    request_id=request.request_id,
                    decision=ApprovalDecision.DENY,
                    reason="WebSocket未连接"
                )
            
            # 等待响应
            try:
                await asyncio.wait_for(event.wait(), timeout=self.timeout)
                return self.responses.pop(request.request_id, ApprovalResponse(
                    request_id=request.request_id,
                    decision=ApprovalDecision.DENY,
                    reason="响应丢失"
                ))
            except asyncio.TimeoutError:
                self.stats['timeouts'] += 1
                return ApprovalResponse(
                    request_id=request.request_id,
                    decision=ApprovalDecision.DENY,
                    reason="等待响应超时"
                )
                
        except Exception as e:
            self.stats['websocket_failed'] += 1
            logger.error(f"WebSocket推送失败: {e}")
            return ApprovalResponse(
                request_id=request.request_id,
                decision=ApprovalDecision.DENY,
                reason=f"推送失败: {str(e)}"
            )
        finally:
            self.pending_requests.pop(request.request_id, None)
            self.response_events.pop(request.request_id, None)
    
    async def _push_to_websocket(self, request: ApprovalRequest):
        """推送到 WebSocket"""
        message = {
            'type': 'permission_request',
            'data': request.to_dict()
        }
        await self.websocket_manager.broadcast(json.dumps(message))
    
    def receive_response(self, response: ApprovalResponse):
        """接收响应（由 WebSocket 处理器调用）"""
        self.responses[response.request_id] = response
        event = self.response_events.get(response.request_id)
        if event:
            event.set()
    
    def cancel_request(self, request_id: str):
        """取消请求"""
        self.pending_requests.pop(request_id, None)
        event = self.response_events.pop(request_id, None)
        if event:
            event.set()  # 解除等待
    
    def get_pending_requests(self) -> List[ApprovalRequest]:
        """获取待处理请求"""
        return list(self.pending_requests.values())
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            **self.stats,
            'pending_count': len(self.pending_requests),
        }


# ============================================================================
# 静默审批处理器（自动模式）
# ============================================================================

class SilentApprovalHandler(ApprovalHandler):
    """
    静默审批处理器
    
    自动决策，不进行交互（用于自动化测试或可信环境）
    """
    
    def __init__(self, default_decision: ApprovalDecision = ApprovalDecision.DENY):
        """
        Args:
            default_decision: 默认决策
        """
        self.default_decision = default_decision
        self.pending_requests: Dict[str, ApprovalRequest] = {}
        self.stats = {
            'total_requests': 0,
            'auto_decisions': 0,
        }
    
    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        """自动决策"""
        self.pending_requests[request.request_id] = request
        self.stats['total_requests'] += 1
        self.stats['auto_decisions'] += 1
        
        logger.info(f"[SilentApproval] 自动决策: {self.default_decision.value} for {request.tool_name}")
        
        return ApprovalResponse(
            request_id=request.request_id,
            decision=self.default_decision,
            reason="静默模式自动决策"
        )
    
    def cancel_request(self, request_id: str):
        """取消请求"""
        self.pending_requests.pop(request_id, None)
    
    def get_pending_requests(self) -> List[ApprovalRequest]:
        """获取待处理请求"""
        return list(self.pending_requests.values())
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return self.stats


# ============================================================================
# 复合审批处理器
# ============================================================================

class CompositeApprovalHandler(ApprovalHandler):
    """
    复合审批处理器
    
    根据环境或配置选择不同的处理器
    """
    
    def __init__(self, handlers: Dict[str, ApprovalHandler], default_handler: str = 'cli'):
        """
        Args:
            handlers: 处理器字典 {'cli': CLIApprovalHandler(), 'web': WebSocketApprovalHandler()}
            default_handler: 默认处理器名称
        """
        self.handlers = handlers
        self.default_handler = default_handler
        self.current_handler = default_handler
    
    def set_handler(self, handler_name: str):
        """切换处理器"""
        if handler_name in self.handlers:
            self.current_handler = handler_name
        else:
            logger.warning(f"处理器 '{handler_name}' 不存在，使用默认")
    
    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        """使用当前处理器请求审批"""
        handler = self.handlers.get(self.current_handler)
        if not handler:
            handler = self.handlers.get(self.default_handler)
        return await handler.request_approval(request)
    
    def cancel_request(self, request_id: str):
        """取消请求"""
        for handler in self.handlers.values():
            handler.cancel_request(request_id)
    
    def get_pending_requests(self) -> List[ApprovalRequest]:
        """获取所有待处理请求"""
        all_pending = []
        for handler in self.handlers.values():
            all_pending.extend(handler.get_pending_requests())
        return all_pending
    
    def get_all_stats(self) -> Dict:
        """获取所有处理器统计"""
        return {
            name: handler.get_stats() 
            for name, handler in self.handlers.items()
        }


# ============================================================================
# 工厂函数
# ============================================================================

def create_approval_handler(
    mode: str = 'cli',
    **kwargs
) -> ApprovalHandler:
    """
    创建审批处理器
    
    Args:
        mode: 模式 ('cli', 'websocket', 'silent')
        **kwargs: 处理器参数
        
    Returns:
        审批处理器实例
    """
    if mode == 'cli':
        return CLIApprovalHandler(**kwargs)
    elif mode == 'websocket':
        return WebSocketApprovalHandler(**kwargs)
    elif mode == 'silent':
        return SilentApprovalHandler(**kwargs)
    else:
        raise ValueError(f"未知的审批模式: {mode}")


# ============================================================================
# 示例用法
# ============================================================================

async def demo_cli_approval():
    """CLI审批演示"""
    handler = CLIApprovalHandler(auto_timeout=60)
    
    request = ApprovalRequest(
        request_id="req_001",
        tool_name="http_request",
        permission_type="INTERNET",
        title="🌐 请求访问网络",
        description="模型请求访问外部网站",
        details="https://example.com",
        risk_level="medium",
        session_id="session_001",
        created_at=datetime.now().timestamp()
    )
    
    response = await handler.request_approval(request)
    print(f"\n审批结果: {response.decision.value}")
    print(f"统计: {handler.get_stats()}")


if __name__ == '__main__':
    asyncio.run(demo_cli_approval())
