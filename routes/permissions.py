"""
Permission Routes (P0-1 extraction from server_final.py)

Permission management, approval, and WebSocket endpoints.
"""
import json
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from permission_manager import PermissionManager, PermissionType, PermissionDecision

router = APIRouter(tags=["permissions"])

# Module-level singleton — same instance used for the lifetime of the process
permission_manager = PermissionManager()


@router.get("/api/permission/pending")
async def get_pending_permissions(session_id: Optional[str] = None):
    """获取待处理的权限请求"""
    requests = permission_manager.get_pending_requests(session_id)
    return {
        "success": True,
        "requests": [
            {
                "request_id": r.request_id,
                "permission_type": r.permission_type.value,
                "title": r.title,
                "description": r.description,
                "details": r.details,
                "session_id": r.session_id,
                "created_at": r.created_at,
                "timeout": r.timeout,
                "status": r.status
            }
            for r in requests
        ]
    }


@router.post("/api/permission/request")
async def create_permission_request(request: dict):
    """创建权限请求（由后端模型调用）"""
    perm_type = PermissionType(request.get("permission_type", "internet"))

    req = permission_manager.create_request(
        permission_type=perm_type,
        title=request.get("title", "请求权限"),
        description=request.get("description", ""),
        details=request.get("details", ""),
        session_id=request.get("session_id", "default"),
        timeout=request.get("timeout", 30)
    )

    return {
        "success": True,
        "request_id": req.request_id,
        "status": "pending"
    }


@router.post("/api/permission/decide")
async def decide_permission(request: dict):
    """做出权限决策"""
    request_id = request.get("request_id")
    decision = PermissionDecision(request.get("decision", "deny"))

    success = permission_manager.make_decision(request_id, decision)

    return {
        "success": success,
        "request_id": request_id,
        "decision": decision.value
    }


@router.get("/api/permission/rules")
async def get_permission_rules():
    """获取所有权限规则"""
    rules = permission_manager.get_permission_rules()
    return {"success": True, "rules": rules}


@router.delete("/api/permission/rules/{index}")
async def delete_permission_rule(index: int):
    """删除权限规则"""
    success = permission_manager.delete_rule(index)
    return {"success": success}


@router.post("/api/permission/check")
async def check_permission(request: dict):
    """检查是否有自动授权（供后端模型调用）"""
    perm_type = PermissionType(request.get("permission_type", "internet"))
    details = request.get("details", "")
    session_id = request.get("session_id", "default")

    decision = permission_manager.check_auto_permission(perm_type, details, session_id)

    return {
        "success": True,
        "has_permission": decision is not None,
        "decision": decision.value if decision else None
    }


@router.websocket("/ws/permission/{session_id}")
async def permission_websocket(websocket: WebSocket, session_id: str = "default"):
    """权限请求WebSocket（实时推送）- 增强版"""
    from permission_websocket import (
        get_ws_approval_manager, handle_permission_websocket
    )

    manager = get_ws_approval_manager()
    await manager.register_connection(websocket, session_id)

    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "")

                if msg_type == "subscribe" or msg.get("action") == "subscribe":
                    sid = msg.get("session_id", session_id)
                    await manager.register_connection(websocket, sid)
                    pending = permission_manager.get_pending_requests(sid)
                    await websocket.send_json({
                        "type": "pending_list",
                        "requests": [
                            {
                                "request_id": r.request_id,
                                "permission_type": r.permission_type.value,
                                "title": r.title,
                                "description": r.description,
                                "details": r.details,
                                "risk_level": getattr(r, 'risk_level', 'medium'),
                                "timeout": r.timeout
                            }
                            for r in pending
                        ]
                    })


                elif msg_type == "permission_decision" or msg.get("action") == "decide":
                    request_id = msg.get("request_id")
                    decision_str = msg.get("decision", "deny")
                    decision = PermissionDecision(decision_str)

                    manager.receive_decision(request_id, decision_str)

                    success = permission_manager.make_decision(request_id, decision)

                    await websocket.send_json({
                        "type": "decision_result",
                        "request_id": request_id,
                        "success": success,
                        "decision": decision.value
                    })


                elif msg_type == "get_pending":
                    pending = manager.get_pending_requests(session_id)
                    await websocket.send_json({
                        "type": "pending_list",
                        "requests": pending
                    })


                elif msg_type == "ping" or msg.get("action") == "ping":
                    await websocket.send_json({"type": "pong"})

            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "无效的JSON格式"
                })

    except WebSocketDisconnect:
        pass
