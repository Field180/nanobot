"""
U23 Phase 2: /status command — full handler migration.

Migrated from server_final.py's intent routing (status_query → system_info).
Produces the same output format as the old path for backward compatibility.
"""
import logging
from typing import Dict

from commands.base import CommandDefinition
from commands import register

logger = logging.getLogger("nanobot.commands")


def handle_status(session_id: str = "", args: str = "", **kwargs) -> Dict:
    """Query system status (CPU, memory, disk) via psutil.

    Returns:
        {"handled": True, "message": str}
    """
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        msg = (
            f"[系统] 系统状态:\n"
            f"CPU: {cpu}%\n"
            f"内存: {mem.percent}%\n"
            f"磁盘: {disk.percent}%\n\n"
            f"[用户] 请基于真实数据总结。"
        )
        return {"handled": True, "message": msg}
    except Exception:
        return {
            "handled": True,
            "message": "[系统] 无法获取系统状态\n\n[用户] 请告知用户状态查询失败。",
        }


# ── Register (replaces metadata-only entry from help.py) ──
register(CommandDefinition(
    name="status",
    description="Show system status (CPU, memory, disk)",
    aliases=["system_status", "show_status", "view_status"],
    category="system",
    handler=handle_status,
))
