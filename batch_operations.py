"""
Batch Operations Module (P9b extraction from server_final.py)
=============================================================
Core logic for batch delete/warm/execute/export operations.
Endpoints remain in server_final.py; this module provides the reusable logic.
"""

import csv
import io
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def batch_delete_files(
    ids: List[str],
    base_dir: str | Path,
    suffix: str = ".json",
) -> Dict[str, Any]:
    """Delete multiple files from *base_dir* by id.

    Returns dict with ``deleted`` and ``failed`` lists.
    """
    base = Path(base_dir)
    deleted: List[str] = []
    failed: List[Dict[str, str]] = []

    for fid in ids:
        try:
            target = base / f"{fid}{suffix}"
            if target.exists():
                target.unlink()
                deleted.append(fid)
            else:
                failed.append({"id": fid, "reason": "not_found"})
        except Exception as e:
            failed.append({"id": fid, "reason": str(e)})

    return {
        "success": True,
        "deleted_count": len(deleted),
        "failed_count": len(failed),
        "deleted": deleted,
        "failed": failed,
    }


def batch_warm_cache(
    endpoints: List[str],
    cache: Dict[str, Any],
    ttl: int = 300,
) -> Dict[str, Any]:
    """Simulate cache warming for a list of *endpoints*.

    Each endpoint gets a cache entry with a TTL.
    """
    warmed: List[str] = []
    failed: List[Dict[str, str]] = []

    for endpoint in endpoints:
        try:
            cache_key = f"warm_{endpoint}"
            cache[cache_key] = {
                "data": {"endpoint": endpoint, "warmed": True},
                "timestamp": time.time(),
                "ttl": ttl,
            }
            warmed.append(endpoint)
        except Exception as e:
            failed.append({"endpoint": endpoint, "reason": str(e)})

    return {
        "success": True,
        "warmed_count": len(warmed),
        "failed_count": len(failed),
        "warmed": warmed,
        "failed": failed,
    }


def batch_execute_operations(
    operations: List[Dict[str, Any]],
    cache: Dict[str, Any],
    audit_log: List[Any],
    load_config_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Execute a list of batch operations.

    Supported op types: ``cache_clear``, ``audit_clear``, ``config_reload``.
    """
    results: List[Dict[str, str]] = []

    for op in operations:
        op_type = op.get("type")
        try:
            if op_type == "cache_clear":
                cache.clear()
                results.append({"type": op_type, "status": "success"})
            elif op_type == "audit_clear":
                audit_log.clear()
                results.append({"type": op_type, "status": "success"})
            elif op_type == "config_reload":
                if load_config_fn:
                    load_config_fn()
                results.append({"type": op_type, "status": "success"})
            else:
                results.append({"type": op_type, "status": "unknown_operation"})
        except Exception as e:
            results.append({"type": op_type, "status": "error", "error": str(e)})

    return {
        "success": True,
        "total_operations": len(operations),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def export_sessions_data(
    session_store_dir: str | Path,
    output_format: str = "json",
) -> Dict[str, Any]:
    """Collect session metadata from *session_store_dir*.

    Returns a dict with ``sessions`` list.  When *output_format* is ``"csv"``
    the dict also contains a ``csv_content`` string ready to be wrapped in a
    Response by the caller.
    """
    base = Path(session_store_dir)
    sessions: List[Dict[str, Any]] = []

    if base.exists():
        for f in base.glob("web_ui_*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    sessions.append({
                        "session_id": f.stem,
                        "created": datetime.fromtimestamp(f.stat().st_ctime).isoformat(),
                        "message_count": len(data.get("history", [])),
                        "size_bytes": f.stat().st_size,
                    })
            except Exception:
                pass

    result: Dict[str, Any] = {
        "success": True,
        "total": len(sessions),
        "sessions": sessions,
    }

    if output_format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["session_id", "created", "message_count", "size_bytes"])
        for s in sessions:
            writer.writerow([s["session_id"], s["created"], s["message_count"], s["size_bytes"]])
        result["csv_content"] = buf.getvalue()

    return result


def export_logs_data(
    audit_log: List[Dict[str, Any]],
    output_format: str = "json",
    limit: int = 100,
) -> Dict[str, Any]:
    """Slice and optionally CSV-format audit log entries.

    Returns a dict; when *output_format* is ``"csv"`` the dict includes a
    ``csv_content`` key.
    """
    logs = audit_log[-limit:] if limit else list(audit_log)

    result: Dict[str, Any] = {
        "success": True,
        "total": len(logs),
        "logs": logs,
    }

    if output_format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["request_id", "timestamp", "method", "path",
                         "client_ip", "status_code", "duration_ms"])
        for log in logs:
            writer.writerow([
                log.get("request_id", ""),
                log.get("timestamp", ""),
                log.get("method", ""),
                log.get("path", ""),
                log.get("client_ip", ""),
                log.get("status_code", ""),
                log.get("duration_ms", ""),
            ])
        result["csv_content"] = buf.getvalue()

    return result


def export_stats_data(
    session_store_dir: str | Path,
    backup_manager: Any,
    ws_manager: Any,
    performance_metrics: Dict[str, Any],
    audit_log: List[Any],
    api_cache: Dict[str, Any],
) -> Dict[str, Any]:
    """Gather system stats for export.

    ``psutil`` is imported lazily so the module stays lightweight when stats
    are not requested.
    """
    import psutil

    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    base = Path(session_store_dir)

    return {
        "success": True,
        "export_time": datetime.now().isoformat(),
        "stats": {
            "system": {
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "memory_percent": memory.percent,
                "disk_percent": disk.percent,
            },
            "sessions": {
                "total": len(list(base.glob("web_ui_*.json"))) if base.exists() else 0,
            },
            "backups": {
                "total": len(backup_manager.list_backups()),
            },
            "websocket": ws_manager.get_stats(),
            "performance": {
                "total_requests": performance_metrics["total_requests"],
                "tool_calls": dict(performance_metrics["tool_calls"]),
            },
            "audit": {
                "total_entries": len(audit_log),
            },
            "cache": {
                "total_entries": len(api_cache),
            },
        },
    }
