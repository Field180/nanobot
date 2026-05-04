#!/usr/bin/env python3
"""
权限审批 WebSocket 集成模块

将 WebSocketApprovalHandler 与 FastAPI 服务器集成：
1. 实时推送权限请求到前端
2. 接收前端审批决策
3. 与 ToolExecutor 联动

版本: 1.0.0
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Set
from datetime import datetime
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class PermissionRequestMessage:
    """权限请求消息（发送到前端）"""
    type: str = "permission_request"
    request_id: str = ""
    tool_name: str = ""
    permission_type: str = ""
    title: str = ""
    description: str = ""
    details: str = ""
    risk_level: str = "medium"
    timestamp: float = 0.0
    timeout: int = 60
    
    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class PermissionDecisionMessage:
    """权限决策消息（从前端接收）"""
    type: str = "permission_decision"
    request_id: str = ""
    decision: str = "deny"  # deny, allow_once, allow_session, allow_always, sandbox
    reason: str = ""
    
    @classmethod
    def from_json(cls, json_str: str) -> 'PermissionDecisionMessage':
        data = json.loads(json_str)
        return cls(
            type=data.get('type', 'permission_decision'),
            request_id=data.get('request_id', ''),
            decision=data.get('decision', 'deny'),
            reason=data.get('reason', ''),
        )


# ============================================================================
# WebSocket 审批管理器
# ============================================================================

class WebSocketApprovalManager:
    """
    WebSocket 审批管理器
    
    管理权限请求的推送和决策接收
    """
    
    def __init__(self):
        # 活跃的 WebSocket 连接 {session_id: [websockets]}
        self.connections: Dict[str, List] = {}
        
        # 待处理的请求 {request_id: (event, request)}
        self.pending_requests: Dict[str, tuple] = {}
        
        # 决策响应 {request_id: decision}
        self.decisions: Dict[str, str] = {}
        
        # 会话订阅 {session_id: set of websocket}
        self.session_subscribers: Dict[str, Set] = {}
        
        # 统计
        self.stats = {
            'total_requests': 0,
            'total_decisions': 0,
            'timeouts': 0,
            'active_connections': 0,
        }
    
    async def register_connection(self, websocket, session_id: str = "default"):
        """注册 WebSocket 连接"""
        if session_id not in self.connections:
            self.connections[session_id] = []
        self.connections[session_id].append(websocket)
        
        if session_id not in self.session_subscribers:
            self.session_subscribers[session_id] = set()
        self.session_subscribers[session_id].add(websocket)
        
        self.stats['active_connections'] = sum(len(v) for v in self.connections.values())
        logger.info(f"[WSApproval] 连接注册: session={session_id}, 活跃连接={self.stats['active_connections']}")
    
    async def unregister_connection(self, websocket, session_id: str = "default"):
        """注销 WebSocket 连接"""
        if session_id in self.connections:
            if websocket in self.connections[session_id]:
                self.connections[session_id].remove(websocket)
        
        if session_id in self.session_subscribers:
            self.session_subscribers[session_id].discard(websocket)
        
        self.stats['active_connections'] = sum(len(v) for v in self.connections.values())
        logger.info(f"[WSApproval] 连接注销: session={session_id}")
    
    async def push_request(
        self,
        request_id: str,
        tool_name: str,
        permission_type: str,
        title: str,
        description: str,
        details: str,
        risk_level: str,
        session_id: str = "default",
        timeout: int = 60,
    ) -> str:
        """
        推送权限请求到前端
        
        Returns:
            request_id
        """
        self.stats['total_requests'] += 1
        
        message = PermissionRequestMessage(
            request_id=request_id,
            tool_name=tool_name,
            permission_type=permission_type,
            title=title,
            description=description,
            details=details,
            risk_level=risk_level,
            timestamp=datetime.now().timestamp(),
            timeout=timeout,
        )
        
        # 创建等待事件
        event = asyncio.Event()
        self.pending_requests[request_id] = (event, message)
        
        # 推送到订阅该会话的所有连接
        if session_id in self.connections:
            json_msg = message.to_json()
            for ws in self.connections[session_id]:
                try:
                    await ws.send_text(json_msg)
                    logger.info(f"[WSApproval] 推送请求: {request_id} -> session={session_id}")
                except Exception as e:
                    logger.error(f"[WSApproval] 推送失败: {e}")
        
        return request_id
    
    async def wait_for_decision(
        self,
        request_id: str,
        timeout: int = 60,
    ) -> Optional[str]:
        """
        等待前端决策
        
        Returns:
            decision: deny, allow_once, allow_session, allow_always, sandbox
            None: 超时
        """
        if request_id not in self.pending_requests:
            return None
        
        event, _ = self.pending_requests[request_id]
        
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            decision = self.decisions.pop(request_id, None)
            self.stats['total_decisions'] += 1
            return decision
        except asyncio.TimeoutError:
            self.stats['timeouts'] += 1
            logger.warning(f"[WSApproval] 决策超时: {request_id}")
            return None
        finally:
            self.pending_requests.pop(request_id, None)
    
    def receive_decision(self, request_id: str, decision: str):
        """接收前端决策（由 WebSocket 处理器调用）"""
        self.decisions[request_id] = decision
        
        # 触发等待事件
        if request_id in self.pending_requests:
            event, _ = self.pending_requests[request_id]
            event.set()
        
        logger.info(f"[WSApproval] 收到决策: {request_id} -> {decision}")
    
    def get_pending_requests(self, session_id: str = None) -> List[Dict]:
        """获取待处理的请求列表"""
        requests = []
        for request_id, (event, message) in self.pending_requests.items():
            requests.append({
                'request_id': request_id,
                'tool_name': message.tool_name,
                'permission_type': message.permission_type,
                'title': message.title,
                'risk_level': message.risk_level,
                'timestamp': message.timestamp,
            })
        return requests
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            **self.stats,
            'pending_count': len(self.pending_requests),
        }


# ============================================================================
# 全局实例
# ============================================================================

_ws_approval_manager: Optional[WebSocketApprovalManager] = None


def get_ws_approval_manager() -> WebSocketApprovalManager:
    """获取全局 WebSocket 审批管理器"""
    global _ws_approval_manager
    if _ws_approval_manager is None:
        _ws_approval_manager = WebSocketApprovalManager()
    return _ws_approval_manager


# ============================================================================
# FastAPI WebSocket 端点
# ============================================================================

async def handle_permission_websocket(websocket, session_id: str = "default"):
    """
    处理权限审批 WebSocket 连接
    
    用法:
        @app.websocket("/ws/permission")
        async def permission_ws(websocket: WebSocket):
            await websocket.accept()
            await handle_permission_websocket(websocket)
    """
    manager = get_ws_approval_manager()
    await manager.register_connection(websocket, session_id)
    
    try:
        while True:
            # 接收消息
            data = await websocket.receive_text()
            
            try:
                message = json.loads(data)
                msg_type = message.get('type', '')
                
                if msg_type == 'permission_decision':
                    # 处理决策
                    request_id = message.get('request_id', '')
                    decision = message.get('decision', 'deny')
                    manager.receive_decision(request_id, decision)
                    
                    # 发送确认
                    await websocket.send_text(json.dumps({
                        'type': 'decision_received',
                        'request_id': request_id,
                        'success': True,
                    }))
                
                elif msg_type == 'subscribe':
                    # 订阅特定会话
                    sid = message.get('session_id', session_id)
                    await manager.register_connection(websocket, sid)
                    
                    # 发送当前待处理请求
                    pending = manager.get_pending_requests(sid)
                    await websocket.send_text(json.dumps({
                        'type': 'pending_list',
                        'requests': pending,
                    }))
                
                elif msg_type == 'get_pending':
                    # 获取待处理请求
                    pending = manager.get_pending_requests(session_id)
                    await websocket.send_text(json.dumps({
                        'type': 'pending_list',
                        'requests': pending,
                    }))
                
                elif msg_type == 'ping':
                    await websocket.send_text(json.dumps({'type': 'pong'}))
                
                else:
                    await websocket.send_text(json.dumps({
                        'type': 'error',
                        'message': f'未知消息类型: {msg_type}',
                    }))
                    
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    'type': 'error',
                    'message': '无效的JSON格式',
                }))
                
    except Exception as e:
        logger.error(f"[WSApproval] WebSocket错误: {e}")
    finally:
        await manager.unregister_connection(websocket, session_id)


# ============================================================================
# 与 ApprovalHandler 集成
# ============================================================================

try:
    from approval_handler import (
        ApprovalHandler, ApprovalRequest, ApprovalResponse, ApprovalDecision
    )
    APPROVAL_HANDLER_AVAILABLE = True
except ImportError:
    APPROVAL_HANDLER_AVAILABLE = False
    ApprovalHandler = object  # 占位符


class IntegratedWebSocketApprovalHandler(ApprovalHandler):
    """
    集成 WebSocket 审批处理器
    
    结合 WebSocket 推送和本地 CLI 回退
    """
    
    def __init__(
        self,
        ws_manager: WebSocketApprovalManager = None,
        cli_fallback: bool = True,
        timeout: int = 60,
    ):
        self.ws_manager = ws_manager or get_ws_approval_manager()
        self.cli_fallback = cli_fallback
        self.timeout = timeout
        
        # CLI 回退处理器
        self._cli_handler = None
        if cli_fallback and APPROVAL_HANDLER_AVAILABLE:
            try:
                from approval_handler import CLIApprovalHandler
                self._cli_handler = CLIApprovalHandler(auto_timeout=timeout)
            except ImportError:
                pass
    
    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        """请求审批"""
        # 检查是否有 WebSocket 连接
        has_connection = len(self.ws_manager.connections.get(request.session_id, [])) > 0
        
        if has_connection:
            # 使用 WebSocket 推送
            return await self._ws_approval(request)
        elif self.cli_fallback and self._cli_handler:
            # 回退到 CLI
            return await self._cli_handler.request_approval(request)
        else:
            # 无连接且无回退，拒绝
            return ApprovalResponse(
                request_id=request.request_id,
                decision=ApprovalDecision.DENY,
                reason="无可用审批通道",
            )
    
    async def _ws_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        """WebSocket 审批"""
        # 推送请求
        await self.ws_manager.push_request(
            request_id=request.request_id,
            tool_name=request.tool_name,
            permission_type=request.permission_type,
            title=request.title,
            description=request.description,
            details=request.details,
            risk_level=request.risk_level,
            session_id=request.session_id,
            timeout=self.timeout,
        )
        
        # 等待决策
        decision_str = await self.ws_manager.wait_for_decision(
            request.request_id,
            timeout=self.timeout,
        )
        
        if decision_str is None:
            # 超时
            return ApprovalResponse(
                request_id=request.request_id,
                decision=ApprovalDecision.DENY,
                reason="审批超时",
            )
        
        # 转换决策
        decision_map = {
            'deny': ApprovalDecision.DENY,
            'allow_once': ApprovalDecision.ALLOW_ONCE,
            'allow_session': ApprovalDecision.ALLOW_SESSION,
            'allow_always': ApprovalDecision.ALLOW_ALWAYS,
            'sandbox': ApprovalDecision.SANDBOX,
        }
        
        decision = decision_map.get(decision_str, ApprovalDecision.DENY)
        
        return ApprovalResponse(
            request_id=request.request_id,
            decision=decision,
        )
    
    def cancel_request(self, request_id: str):
        """取消请求"""
        self.ws_manager.pending_requests.pop(request_id, None)
    
    def get_pending_requests(self) -> List[ApprovalRequest]:
        """获取待处理请求"""
        pending = self.ws_manager.get_pending_requests()
        return [
            ApprovalRequest(
                request_id=r['request_id'],
                tool_name=r['tool_name'],
                permission_type=r['permission_type'],
                title=r['title'],
                description='',
                details='',
                risk_level=r['risk_level'],
                session_id='',
                created_at=r['timestamp'],
            )
            for r in pending
        ]


# ============================================================================
# 初始化函数
# ============================================================================

def create_integrated_approval_handler(
    use_cli_fallback: bool = True,
    timeout: int = 60,
) -> IntegratedWebSocketApprovalHandler:
    """创建集成的审批处理器"""
    return IntegratedWebSocketApprovalHandler(
        ws_manager=get_ws_approval_manager(),
        cli_fallback=use_cli_fallback,
        timeout=timeout,
    )


# ============================================================================
# 示例用法
# ============================================================================

"""
# 在 server_final.py 中集成:

from web_ui.permission_websocket import (
    get_ws_approval_manager,
    handle_permission_websocket,
    create_integrated_approval_handler,
)

# WebSocket 端点
@app.websocket("/ws/permission/{session_id}")
async def permission_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    await handle_permission_websocket(websocket, session_id)

# 创建审批处理器
approval_handler = create_integrated_approval_handler()

# 在 ToolExecutor 中使用
executor = ToolExecutor(
    workspace=workspace,
    approval_handler=approval_handler,
)
"""

if __name__ == '__main__':
    import asyncio
    
    async def test_ws_manager():
        manager = WebSocketApprovalManager()
        
        # 模拟推送
        request_id = await manager.push_request(
            tool_name="shell_execute",
            permission_type="SYSTEM_CMD",
            title="执行命令",
            description="测试",
            details="ls -la",
            risk_level="medium",
            session_id="test",
        )
        
        print(f"推送请求: {request_id}")
        print(f"待处理: {manager.get_pending_requests()}")
        print(f"统计: {manager.get_stats()}")
        
        # 模拟决策
        manager.receive_decision(request_id, "allow_once")
        print(f"决策后: {manager.get_stats()}")
    
    asyncio.run(test_ws_manager())
