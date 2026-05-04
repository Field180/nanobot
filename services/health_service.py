"""
P15 — Health & diagnostics business logic.

Extracted from server_final.py endpoints:
  /api/errors/stats
  /api/errors/reset
  /api/performance/stats
  /api/performance/timeline
  /api/performance/reset

Global state (PERFORMANCE_METRICS, ERROR_HANDLER) is injected via parameters.
"""

from __future__ import annotations

import time
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


# ── Error stats ─────────────────────────────────────────────────────

def get_error_stats(error_handler: Any) -> Dict[str, Any]:
    """Return error statistics from the error handler."""
    return {"success": True, "stats": error_handler.get_error_stats()}


def reset_error_stats(error_handler: Any) -> Dict[str, Any]:
    """Reset error statistics and return confirmation."""
    error_handler.reset_stats()
    return {"success": True, "message": "错误统计已重置"}


# ── Performance stats ───────────────────────────────────────────────

def get_performance_stats(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Compute and return performance statistics.

    Parameters
    ----------
    metrics : dict
        The ``PERFORMANCE_METRICS`` global dict with keys
        *request_times*, *snn_times*, *tool_calls*,
        *total_requests*, *start_time*.
    """
    import psutil

    # 计算平均响应时间
    req_times = metrics["request_times"]
    avg_request_time = 0.0
    if req_times:
        recent = req_times[-100:]
        avg_request_time = sum(recent) / len(recent)

    snn_times = metrics["snn_times"]
    avg_snn_time = 0.0
    if snn_times:
        recent = snn_times[-100:]
        avg_snn_time = sum(recent) / len(recent)

    # 系统资源
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()

    # 运行时间
    uptime = time.time() - metrics["start_time"]

    return {
        "success": True,
        "stats": {
            "uptime_seconds": round(uptime),
            "uptime_human": f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m",
            "total_requests": metrics["total_requests"],
            "avg_response_time_ms": round(avg_request_time * 1000, 2),
            "avg_snn_time_ms": round(avg_snn_time * 1000, 2),
            "tool_calls": dict(metrics["tool_calls"]),
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_used_gb": round(memory.used / (1024 ** 3), 2),
                "memory_total_gb": round(memory.total / (1024 ** 3), 2),
            },
        },
    }


def get_performance_timeline(
    metrics: Dict[str, Any], minutes: int = 5
) -> Dict[str, Any]:
    """Return request count in the last *minutes* minutes."""
    cutoff = time.time() - minutes * 60
    recent_requests = [t for t in metrics["request_times"] if t > cutoff]
    return {
        "success": True,
        "timeline": {
            "minutes": minutes,
            "request_count": len(recent_requests),
            "requests_per_minute": round(len(recent_requests) / max(1, minutes), 2),
        },
    }


def reset_performance_stats(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Clear all performance counters and reset start_time."""
    metrics["request_times"].clear()
    metrics["snn_times"].clear()
    metrics["tool_calls"].clear()
    metrics["total_requests"] = 0
    metrics["start_time"] = time.time()
    return {"success": True, "message": "性能统计已重置"}
