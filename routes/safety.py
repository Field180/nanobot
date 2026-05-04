"""
Safety Routes (P0-1 extraction from server_final.py)

High-risk operation interception (danger) and sandbox policy endpoints.
"""
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server_state import WORKSPACE

logger = logging.getLogger(__name__)

router = APIRouter(tags=["safety"])

_TOOLS_PATH = str(Path(__file__).parent.parent.parent / "tools")


def _ensure_tools_path():
    if _TOOLS_PATH not in sys.path:
        sys.path.insert(0, _TOOLS_PATH)


# ── Danger interception ─────────────────────────────────────

@router.get("/api/danger/check")
async def check_danger_command(command: str, session_id: str = "default"):
    """检查命令是否包含危险操作"""
    _ensure_tools_path()
    from danger_interceptor import get_danger_interceptor

    interceptor = get_danger_interceptor()
    should_intercept, danger_match = interceptor.should_intercept(session_id, command)

    return {
        "success": True,
        "should_intercept": should_intercept,
        "danger_match": {
            "matched": danger_match.matched,
            "level": danger_match.level.value,
            "category": danger_match.category,
            "description": danger_match.description,
            "suggestion": danger_match.suggestion
        } if danger_match.matched else None
    }


@router.post("/api/danger/approval/request")
async def request_danger_approval(request: dict):
    """创建高危操作审批请求"""
    _ensure_tools_path()
    from danger_interceptor import get_danger_interceptor, DangerMatch, DangerLevel

    interceptor = get_danger_interceptor()

    session_id = request.get("session_id", "default")
    command = request.get("command", "")
    danger_info = request.get("danger_match", {})

    danger_match = DangerMatch(
        matched=True,
        level=DangerLevel(danger_info.get("level", "medium")),
        category=danger_info.get("category", "unknown"),
        pattern=danger_info.get("pattern", ""),
        description=danger_info.get("description", ""),
        suggestion=danger_info.get("suggestion", "")
    )

    request_id = str(uuid.uuid4())[:8]

    return {
        "success": True,
        "request_id": request_id,
        "status": "pending",
        "message": "审批请求已创建，等待用户决策"
    }


@router.post("/api/danger/approval/decide")
async def decide_danger_approval(request: dict):
    """处理高危操作审批决策"""
    _ensure_tools_path()
    from danger_interceptor import get_danger_interceptor

    interceptor = get_danger_interceptor()

    request_id = request.get("request_id")
    decision = request.get("decision", "deny")

    success = interceptor.decide(request_id, decision)

    return {
        "success": success,
        "request_id": request_id,
        "decision": decision
    }


@router.get("/api/danger/approval/pending")
async def get_pending_danger_approvals(session_id: Optional[str] = None):
    """获取待处理的高危操作审批"""
    _ensure_tools_path()
    from danger_interceptor import get_danger_interceptor

    interceptor = get_danger_interceptor()
    requests = interceptor.get_pending_requests(session_id)

    return {
        "success": True,
        "requests": [
            {
                "id": r.id,
                "session_id": r.session_id,
                "command": r.command,
                "danger_level": r.danger_match.level.value,
                "category": r.danger_match.category,
                "description": r.danger_match.description,
                "suggestion": r.danger_match.suggestion,
                "status": r.status,
                "timestamp": r.timestamp.isoformat()
            }
            for r in requests
        ]
    }


@router.get("/api/danger/patterns")
async def get_danger_patterns():
    """获取所有危险操作模式"""
    _ensure_tools_path()
    from danger_interceptor import get_danger_interceptor

    interceptor = get_danger_interceptor()

    return {
        "success": True,
        "patterns": interceptor.patterns
    }


@router.websocket("/ws/danger/{session_id}")
async def danger_websocket(websocket: WebSocket, session_id: str = "default"):
    """高危操作审批 WebSocket（实时推送）"""
    await websocket.accept()

    _ensure_tools_path()
    from danger_interceptor import get_danger_interceptor

    interceptor = get_danger_interceptor()

    async def ws_callback(data):
        await websocket.send_json(data)

    interceptor.websocket_callback = ws_callback

    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "")

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

                elif msg_type == "decide":
                    request_id = msg.get("request_id")
                    decision = msg.get("decision", "deny")
                    success = interceptor.decide(request_id, decision)
                    await websocket.send_json({
                        "type": "decision_result",
                        "request_id": request_id,
                        "success": success,
                        "decision": decision
                    })

                elif msg_type == "get_pending":
                    reqs = interceptor.get_pending_requests(session_id)
                    await websocket.send_json({
                        "type": "pending_list",
                        "requests": [
                            {
                                "id": r.id,
                                "command": r.command,
                                "danger_level": r.danger_match.level.value,
                                "category": r.danger_match.category,
                                "description": r.danger_match.description
                            }
                            for r in reqs
                        ]
                    })

            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "无效的JSON格式"
                })

    except WebSocketDisconnect:
        interceptor.websocket_callback = None


# ── Sandbox policy ───────────────────────────────────────────

@router.get("/api/sandbox/check")
async def check_sandbox(target: str, session_id: str = "default"):
    """检查沙箱策略"""
    _ensure_tools_path()
    from sandbox_policy import get_sandbox_policy

    policy = get_sandbox_policy()
    result = policy.check(target, session_id)

    return {
        "success": True,
        "allowed": result.allowed,
        "decision": result.decision.value,
        "reason": result.reason,
        "suggestion": result.suggestion
    }


@router.get("/api/sandbox/status")
async def get_sandbox_status():
    """获取沙箱状态"""
    _ensure_tools_path()
    from sandbox_policy import get_sandbox_policy

    policy = get_sandbox_policy()
    return {
        "success": True,
        "status": policy.get_status()
    }


@router.post("/api/sandbox/allow")
async def add_sandbox_allow(request: dict):
    """添加允许规则"""
    _ensure_tools_path()
    from sandbox_policy import get_sandbox_policy

    policy = get_sandbox_policy()
    pattern = request.get("pattern")
    session_id = request.get("session_id")
    permanent = request.get("permanent", False)

    if permanent:
        policy.add_also_allow(pattern)
    elif session_id:
        policy.add_session_allow(session_id, pattern)
    else:
        return {"success": False, "error": "需要 session_id 或 permanent=True"}

    return {"success": True, "pattern": pattern}


@router.delete("/api/sandbox/allow")
async def remove_sandbox_allow(pattern: str):
    """移除允许规则"""
    _ensure_tools_path()
    from sandbox_policy import get_sandbox_policy

    policy = get_sandbox_policy()
    policy.remove_also_allow(pattern)

    return {"success": True, "pattern": pattern}
