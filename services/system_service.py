"""
P15 — System status / version business logic.

Extracted from server_final.py endpoints:
  /api/status
  /api/system/status
  /api/version
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── /api/status ──────────────────────────────────────────────────────

def get_basic_status(
    python_path: str,
    workspace: Path,
    session_count: int,
) -> Dict[str, Any]:
    """Return basic API health status (nanobot availability check)."""
    try:
        result = subprocess.run(
            [python_path, "-m", "nanobot", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        nanobot_ok = result.returncode == 0
    except Exception:
        nanobot_ok = False

    return {
        "status": "ok",
        "nanobot_available": nanobot_ok,
        "python": python_path,
        "workspace": str(workspace),
        "sessions": session_count,
        "metrics_endpoint_enabled": bool(os.environ.get("NANOBOT_METRICS_TOKEN", "")),
    }


# ── /api/version ─────────────────────────────────────────────────────

API_VERSION = "3.1.0"
API_VERSIONS: Dict[str, Dict[str, Any]] = {
    "3.1.0": {
        "release_date": "2026-03-12",
        "features": ["WebSocket", "Backup", "Audit", "Cache", "Config Hot-reload"],
        "deprecated": [],
        "breaking_changes": [],
    },
    "3.0.0": {
        "release_date": "2026-03-01",
        "features": ["SNN Integration", "Tool Execution", "Rate Limiting"],
        "deprecated": ["/api/v1/chat"],
        "breaking_changes": [],
    },
    "2.0.0": {
        "release_date": "2026-02-01",
        "features": ["Streaming Output", "Session Management"],
        "deprecated": [],
        "breaking_changes": ["API endpoint restructuring"],
    },
}


def get_api_version_info() -> Dict[str, Any]:
    """Return current API version metadata."""
    return {
        "success": True,
        "version": API_VERSION,
        "release_date": API_VERSIONS[API_VERSION]["release_date"],
        "features": API_VERSIONS[API_VERSION]["features"],
    }


# ── /api/system/status ───────────────────────────────────────────────

def get_detailed_system_status(workspace: Path) -> Dict[str, Any]:
    """Collect detailed system status for the status-center panel.

    Parameters
    ----------
    workspace : Path
        Typically ``~/.nanobot/workspace``.

    Returns a dict with keys *success*, *timestamp*, *tasks*, *health*,
    *preferences*, *resources*.
    """
    try:
        # 1. Task scheduler data
        tasks_file = workspace / "scheduled_tasks.json"
        tasks_data: Dict[str, Any] = {
            "total": 0, "enabled": 0, "pending": 0, "running": 0, "tasks": [],
        }
        if tasks_file.exists():
            try:
                with open(tasks_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    all_tasks = data.get("tasks", [])
                    tasks_data["total"] = len(all_tasks)
                    tasks_data["enabled"] = len([t for t in all_tasks if t.get("enabled", False)])
                    tasks_data["pending"] = len([t for t in all_tasks if t.get("status") == "pending"])
                    tasks_data["running"] = len([t for t in all_tasks if t.get("status") == "running"])
                    for task in all_tasks[:5]:
                        tasks_data["tasks"].append({
                            "id": task.get("id", "unknown"),
                            "name": task.get("name", "未命名"),
                            "type": task.get("schedule_type", "interval"),
                            "status": task.get("status", "pending"),
                            "next_run": task.get("next_run"),
                            "description": task.get("command", ""),
                        })
            except Exception as exc:
                logger.warning("[SystemStatus] 读取任务文件失败: %s", exc)

        # 2. Health report
        health_file = workspace / "health_reports.json"
        health_data: Dict[str, Any] = {"score": None, "level": "unknown", "last_check": None}
        if health_file.exists():
            try:
                with open(health_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    reports = data.get("reports", [])
                    if reports:
                        latest = reports[-1]
                        health_data["score"] = latest.get("score", 0)
                        health_data["level"] = latest.get("level", "unknown")
                        health_data["last_check"] = latest.get("timestamp")
            except Exception as exc:
                logger.warning("[SystemStatus] 读取健康报告失败: %s", exc)

        # 3. User preferences
        memory_file = workspace / "MEMORY.md"
        preferences: List[str] = []
        if memory_file.exists():
            try:
                content = memory_file.read_text(encoding="utf-8")
                if "markdown" in content.lower() or "代码块" in content:
                    preferences.append("代码块格式规范")
                if "yaml" in content.lower():
                    preferences.append("YAML配置高亮")
                if "json" in content.lower():
                    preferences.append("JSON格式验证")
            except Exception as exc:
                logger.warning("[SystemStatus] 读取MEMORY失败: %s", exc)

        # 4. System resources
        import psutil
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        return {
            "success": True,
            "timestamp": datetime.now().isoformat(),
            "tasks": tasks_data,
            "health": health_data,
            "preferences": preferences,
            "resources": {
                "memory_percent": memory.percent,
                "memory_used_gb": round(memory.used / (1024 ** 3), 2),
                "memory_total_gb": round(memory.total / (1024 ** 3), 2),
                "disk_percent": disk.percent,
                "disk_free_gb": round(disk.free / (1024 ** 3), 2),
            },
        }
    except Exception as exc:
        return {"success": False, "message": f"获取系统状态失败: {str(exc)}"}
