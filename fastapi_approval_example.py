#!/usr/bin/env python3
"""
FastAPI WebSocket 审批集成示例

展示如何将权限审批系统集成到 FastAPI 应用中

运行方式:
    pip install fastapi uvicorn
    uvicorn fastapi_approval_example:app --reload --port 8080
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional
import asyncio
import json
from pathlib import Path

# 导入权限系统组件
from permission_websocket import WebSocketApprovalManager
from tool_executor import ToolExecutor
from audit_logger import get_audit_logger_with_config

app = FastAPI(title="Nanobot Approval API")

# 初始化组件
approval_manager = WebSocketApprovalManager()
audit_logger = get_audit_logger_with_config()


# ============================================================================
# REST API 端点
# ============================================================================

class ToolExecuteRequest(BaseModel):
    tool_name: str
    params: Dict[str, Any]
    session_id: Optional[str] = None


class ApprovalDecision(BaseModel):
    request_id: str
    decision: str  # 'allow_once', 'allow_session', 'allow_always', 'deny'


@app.post("/api/tool/execute")
async def execute_tool(request: ToolExecuteRequest):
    """
    执行工具（带权限审批）
    
    如果需要审批，返回 permission_required: true 和 request_id
    客户端应通过 WebSocket 等待审批结果
    """
    executor = ToolExecutor(
        workspace=Path.cwd(),
        session_id=request.session_id,
        approval_handler=approval_manager,
    )
    
    try:
        result = executor.execute(request.tool_name, request.params)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/policies")
async def list_policies():
    """列出所有权限策略"""
    from permission_policy import PermissionPolicyManager
    manager = PermissionPolicyManager()
    return {"policies": [p.to_dict() for p in manager.list_policies()]}


@app.get("/api/audit/summary")
async def get_audit_summary(hours: int = 24):
    """获取审计摘要"""
    return audit_logger.get_summary(hours=hours)


@app.post("/api/approval/decide")
async def make_decision(decision: ApprovalDecision):
    """
    通过 REST API 做出审批决策
    
    也可以通过 WebSocket 做决策（推荐）
    """
    success = approval_manager.notify_decision(
        decision.request_id,
        decision.decision
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="请求不存在或已过期")
    
    return {"success": True, "decision": decision.decision}


# ============================================================================
# WebSocket 端点
# ============================================================================

@app.websocket("/ws/permission/{session_id}")
async def permission_websocket(websocket: WebSocket, session_id: str):
    """
    权限审批 WebSocket 端点
    
    消息格式:
    
    客户端 -> 服务器:
    {
        "type": "subscribe",
        "session_id": "xxx"
    }
    
    {
        "type": "decision",
        "request_id": "req_xxx",
        "decision": "allow_once"
    }
    
    服务器 -> 客户端:
    {
        "type": "permission_request",
        "request_id": "req_xxx",
        "tool_name": "shell_execute",
        "description": "执行命令",
        "params": {...},
        "timeout": 60
    }
    
    {
        "type": "decision_ack",
        "request_id": "req_xxx",
        "decision": "allow_once"
    }
    """
    await websocket.accept()
    
    try:
        # 注册连接
        await approval_manager.register_connection(websocket, session_id)
        
        # 发送连接确认
        await websocket.send_json({
            "type": "connected",
            "session_id": session_id,
            "message": "已连接到权限审批服务"
        })
        
        # 监听消息
        while True:
            data = await websocket.receive_json()
            
            if data.get("type") == "subscribe":
                # 订阅特定会话的审批请求
                await approval_manager.subscribe_session(websocket, data.get("session_id", session_id))
                await websocket.send_json({
                    "type": "subscribed",
                    "session_id": data.get("session_id", session_id)
                })
            
            elif data.get("type") == "decision":
                # 处理审批决策
                request_id = data.get("request_id")
                decision = data.get("decision")
                
                success = approval_manager.notify_decision(request_id, decision)
                
                await websocket.send_json({
                    "type": "decision_ack" if success else "error",
                    "request_id": request_id,
                    "decision": decision,
                    "message": "决策已处理" if success else "请求不存在或已过期"
                })
            
            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    
    except WebSocketDisconnect:
        pass
    finally:
        await approval_manager.unregister_connection(websocket, session_id)


# ============================================================================
# 前端页面
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """返回前端 Demo 页面"""
    html_path = Path(__file__).parent / "approval_demo.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text())
    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html>
    <head><title>Nanobot Approval</title></head>
    <body>
        <h1>Nanobot 权限审批服务</h1>
        <p>请打开 <a href="/docs">API 文档</a> 查看可用端点</p>
        <p>WebSocket 端点: <code>ws://localhost:8080/ws/permission/{session_id}</code></p>
    </body>
    </html>
    """)


# ============================================================================
# Flask 集成示例（注释形式）
# ============================================================================

"""
# Flask 集成示例
# 
# pip install flask flask-sock
# 
# from flask import Flask, request, jsonify
# from flask_sock import Sock
# 
# app = Flask(__name__)
# sock = Sock(app)
# 
# # 初始化
# approval_manager = WebSocketApprovalManager()
# 
# # REST API
# @app.route('/api/tool/execute', methods=['POST'])
# def execute_tool():
#     data = request.json
#     executor = ToolExecutor(
#         workspace=Path.cwd(),
#         session_id=data.get('session_id'),
#         approval_handler=approval_manager,
#     )
#     return jsonify(executor.execute(data['tool_name'], data['params']))
# 
# # WebSocket
# @sock.route('/ws/permission/<session_id>')
# def permission_ws(ws, session_id):
#     import json
#     
#     # 注册连接
#     approval_manager.register_connection_sync(ws, session_id)
#     ws.send(json.dumps({"type": "connected", "session_id": session_id}))
#     
#     while True:
#         data = json.loads(ws.receive())
#         
#         if data.get('type') == 'decision':
#             success = approval_manager.notify_decision(
#                 data['request_id'],
#                 data['decision']
#             )
#             ws.send(json.dumps({
#                 "type": "decision_ack" if success else "error",
#                 "request_id": data['request_id']
#             }))
# 
# if __name__ == '__main__':
#     app.run(port=8080, debug=True)
"""


# ============================================================================
# 启动配置
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)
