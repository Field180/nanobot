"""
Rate Limit Routes (P0-1 extraction from server_final.py)

Model-level rate limiting / cooldown endpoints.
"""
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter

router = APIRouter(tags=["rate-limit"])

_TOOLS_PATH = str(Path(__file__).parent.parent.parent / "tools")


def _ensure_tools_path():
    if _TOOLS_PATH not in sys.path:
        sys.path.insert(0, _TOOLS_PATH)


@router.get("/api/rate-limit/status")
async def get_rate_limit_status(model: Optional[str] = None):
    """获取限流状态"""
    _ensure_tools_path()
    from rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()

    if model:
        status = limiter.check_rate_limit(model)
        return {
            "success": True,
            "model": model,
            "is_limited": status.is_limited,
            "remaining_seconds": status.remaining_seconds,
            "stage": status.stage.value,
            "error_count": status.error_count,
            "message": status.message
        }
    else:
        return {
            "success": True,
            "all_status": limiter.get_all_status()
        }


@router.post("/api/rate-limit/trigger")
async def trigger_rate_limit(request: dict):
    """触发限流冷却"""
    _ensure_tools_path()
    from rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()

    model = request.get("model")
    http_status = request.get("http_status", 429)
    error_msg = request.get("error_msg", "")

    status = limiter.trigger_cooldown(model, http_status, error_msg)

    return {
        "success": True,
        "model": model,
        "is_limited": status.is_limited,
        "remaining_seconds": status.remaining_seconds,
        "stage": status.stage.value
    }


@router.post("/api/rate-limit/reset")
async def reset_rate_limit(model: Optional[str] = None):
    """重置限流"""
    _ensure_tools_path()
    from rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()

    if model:
        limiter.reset_cooldown(model)
    else:
        limiter.reset_all()

    return {"success": True, "model": model or "all"}


@router.post("/api/rate-limit/success")
async def record_rate_limit_success(model: str):
    """记录成功请求"""
    _ensure_tools_path()
    from rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()
    limiter.success(model)

    return {"success": True, "model": model}
