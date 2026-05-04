"""
P15 — Task scheduler business logic.

Extracted from server_final.py endpoints:
  /api/tasks/status
  /api/tasks/execute
  /api/tasks/scheduler/start

Heavy subprocess / file-system operations stay in the endpoint layer;
the pure data-shaping logic lives here.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Task description heuristics ─────────────────────────────────────

_ID_PATTERNS: Dict[str, str] = {
    "health_check": "扫描系统状态，检查API连接、磁盘空间、内存使用等关键指标",
    "backup": "自动备份系统配置文件和重要数据到安全位置，防止数据丢失",
    "cache": "清理过期缓存文件，优化系统性能，释放磁盘空间",
    "reflection": "分析今日对话记录，提取关键信息，生成记忆摘要",
    "memory": "整合分散的记忆片段，压缩重复内容，优化存储结构",
    "cleanup": "清理临时文件和过期数据，保持系统整洁",
    "optimize": "优化系统性能，整理资源分配",
    "sync": "同步数据和配置，确保多设备一致性",
    "report": "生成系统运行报告，汇总关键指标",
    "update": "检查并更新系统组件和依赖",
    "compress": "压缩历史数据，节省存储空间",
    "index": "重建搜索索引，提升查询效率",
}

_SCRIPT_PATTERNS: Dict[str, str] = {
    "health": "扫描系统状态，检查API连接、磁盘空间、内存使用等关键指标",
    "backup": "自动备份系统配置文件和重要数据到安全位置，防止数据丢失",
    "cache": "清理过期缓存文件，优化系统性能，释放磁盘空间",
    "clean": "清理临时文件和过期数据，保持系统整洁",
    "optimize": "优化系统性能，整理资源分配",
    "sync": "同步数据和配置，确保多设备一致性",
    "reflection": "分析对话记录，提取关键信息，生成记忆摘要",
    "memory": "整合分散的记忆片段，压缩重复内容",
    "consolidation": "整合分散的记忆片段，压缩重复内容",
    "sleep": "执行夜间维护任务，优化存储结构",
    "report": "生成系统运行报告，汇总关键指标",
    "update": "检查并更新系统组件和依赖",
    "compress": "压缩历史数据，节省存储空间",
    "index": "重建搜索索引，提升查询效率",
    "db": "维护数据库，优化查询性能",
    "vacuum": "优化数据库存储，回收空间碎片",
    "analyze": "分析系统数据，生成统计报告",
}


def generate_task_description(task: Dict[str, Any]) -> str:
    """根据任务信息智能生成中文描述"""
    command = task.get("command", "")
    name = task.get("name", "")
    task_id = task.get("id", "")

    # 1. 首先检查是否已有用户定义的描述
    existing_desc = task.get("description", "")
    if existing_desc and len(existing_desc) > 10 and not existing_desc.startswith("python"):
        return existing_desc

    # 2. 基于ID匹配常见任务
    for pattern, desc in _ID_PATTERNS.items():
        if pattern in task_id.lower() or pattern in name.lower():
            return desc

    # 3. 基于命令内容智能分析
    cmd_lower = command.lower()

    # Python脚本分析
    if "python" in cmd_lower or ".py" in cmd_lower:
        for pattern, desc in _SCRIPT_PATTERNS.items():
            if pattern in cmd_lower:
                return desc

        # 提取脚本文件名
        script_match = re.search(r"(\w+)\.py", command)
        if script_match:
            script_name = script_match.group(1)
            if script_name not in ("python", "python3"):
                return f"执行 {script_name} 脚本任务，维护系统正常运行"

    # Shell命令分析
    if cmd_lower.startswith(("rm ", "del ")):
        return "清理过期或临时文件，释放磁盘空间"
    if cmd_lower.startswith(("cp ", "copy ", "rsync ")):
        return "复制或同步文件到指定位置"
    if cmd_lower.startswith(("mkdir ", "md ")):
        return "创建必要的目录结构"
    if cmd_lower.startswith(("chmod ", "chown ")):
        return "修复文件权限设置，确保系统安全"
    if cmd_lower.startswith(("curl ", "wget ", "http")):
        return "从网络获取数据或检查远程服务状态"
    if cmd_lower.startswith(("find ", "grep ", "awk ", "sed ")):
        return "搜索和处理系统数据"

    # 4. 基于任务名称推断
    name_lower = name.lower()
    _name_heuristics = [
        (["备份", "backup"], "自动备份系统配置文件和重要数据，防止数据丢失"),
        (["健康", "检查", "health", "check"], "扫描系统状态，检查关键指标和连接状态"),
        (["缓存", "cache", "清理", "clean"], "清理过期缓存文件，优化系统性能，释放磁盘空间"),
        (["反思", "reflection", "总结"], "分析对话记录，提取关键信息，生成记忆摘要"),
        (["记忆", "memory", "整合"], "整合分散的记忆片段，压缩重复内容，优化存储"),
        (["优化", "optimize", "整理"], "优化系统性能，整理资源分配"),
        (["同步", "sync"], "同步数据和配置，确保一致性"),
        (["报告", "report", "统计"], "生成系统运行报告，汇总关键指标"),
        (["更新", "update", "升级"], "检查并更新系统组件和依赖"),
    ]
    for keywords, desc in _name_heuristics:
        if any(w in name_lower for w in keywords):
            return desc

    # 5. 通用描述
    if command:
        return f'执行系统维护任务: {command[:40]}{"..." if len(command) > 40 else ""}'

    return "执行预定的系统维护任务，保持系统健康运行"


# ── Read & process tasks ────────────────────────────────────────────

def read_tasks_file(tasks_file: Path) -> List[Dict[str, Any]]:
    """Read scheduled tasks from JSON file; return empty list on failure."""
    if not tasks_file.exists():
        return []
    try:
        with open(tasks_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("tasks", [])
    except Exception as exc:
        logger.warning("[TasksAPI] 读取任务文件失败: %s", exc)
        return []


def enrich_tasks(
    tasks: List[Dict[str, Any]],
    scheduler_running: bool,
) -> List[Dict[str, Any]]:
    """Fill in defaults, generate descriptions, and update overdue statuses.

    Mutates the task dicts in-place and returns the same list.
    """
    now = datetime.now()

    for task in tasks:
        # 确保所有任务都有必要字段
        task.setdefault("id", f"task_{hash(task.get('name', 'unknown'))}")
        task.setdefault("name", "未命名任务")
        task.setdefault("command", "")
        task.setdefault("status", "pending")
        task.setdefault("schedule_type", "interval")
        task.setdefault("interval_seconds", 3600)
        task.setdefault("last_run", None)
        task.setdefault("next_run", None)
        # 智能生成描述
        task["description"] = generate_task_description(task)

    # 更新任务状态（基于时间）
    for task in tasks:
        if task.get("next_run"):
            try:
                next_run = datetime.fromisoformat(
                    task["next_run"].replace("Z", "+00:00")
                )
                if next_run <= now and task.get("status") == "pending":
                    if not scheduler_running:
                        task["status"] = "failed"
                        task["error"] = (
                            f"任务过期未执行 (计划时间: {next_run.strftime('%Y-%m-%d %H:%M')})"
                        )
                    else:
                        schedule_type = task.get("schedule_type", "interval")
                        interval_seconds = task.get("interval_seconds", 3600)
                        if schedule_type == "daily":
                            next_run = now + timedelta(days=1)
                            next_run = next_run.replace(
                                hour=next_run.hour, minute=0, second=0, microsecond=0
                            )
                        else:
                            next_run = now + timedelta(seconds=interval_seconds)
                        task["next_run"] = next_run.isoformat()
                        task["last_run"] = now.isoformat()
            except Exception as exc:
                logger.debug("[TasksAPI] 更新任务时间失败: %s", exc)

    return tasks


# ── Execute a task ──────────────────────────────────────────────────

def execute_task(
    task_id: str,
    tasks_file: Path,
    workspace: Path,
    timeout: int = 120,
) -> Dict[str, Any]:
    """Find *task_id* in *tasks_file*, execute its command, persist status.

    Returns a result dict with *success* and supporting fields.
    """
    if not task_id:
        return {"success": False, "error": "缺少task_id参数"}
    if not tasks_file.exists():
        return {"success": False, "error": "任务文件不存在"}

    # Read
    try:
        with open(tasks_file, "r", encoding="utf-8") as f:
            file_data = json.load(f)
    except Exception as exc:
        return {"success": False, "error": f"读取任务文件失败: {exc}"}

    tasks = file_data.get("tasks", [])
    task: Optional[Dict[str, Any]] = None
    for t in tasks:
        if t.get("id") == task_id:
            task = t
            break
    if task is None:
        return {"success": False, "error": f"未找到任务: {task_id}"}

    command = task.get("command", "")
    task_name = task.get("name", "未命名任务")
    if not command:
        return {"success": False, "error": "任务没有可执行的命令"}

    # Mark running
    task["status"] = "running"
    task["last_run"] = datetime.now().isoformat()
    _save_tasks(tasks_file, file_data)

    # Execute
    cmd_parts = command.split()
    if not cmd_parts:
        return {"success": False, "error": "命令格式无效"}

    try:
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workspace),
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"命令执行超时（{timeout}秒）", "status": "failed", "task_id": task_id}
    except FileNotFoundError:
        return {"success": False, "error": f"命令或脚本不存在: {cmd_parts[0]}", "status": "failed", "task_id": task_id}
    except Exception as exc:
        return {"success": False, "error": f"执行异常: {exc}", "status": "failed", "task_id": task_id}

    output = result.stdout.strip() if result.stdout else ""
    stderr = result.stderr.strip() if result.stderr else ""

    # Update status
    task["status"] = "completed" if result.returncode == 0 else "failed"
    task["last_run"] = datetime.now().isoformat()
    if result.returncode == 0:
        task["run_count"] = task.get("run_count", 0) + 1
    else:
        task["error_count"] = task.get("error_count", 0) + 1
        task["last_error"] = stderr[:100] if stderr else "执行失败"

    _save_tasks(tasks_file, file_data)

    if result.returncode == 0:
        return {
            "success": True,
            "message": f"任务 {task_name} 执行完成",
            "task_id": task_id,
            "result": output[:2000],
            "stderr": stderr[:500] if stderr else None,
            "status": "completed",
        }
    return {
        "success": False,
        "error": stderr[:500] if stderr else "命令执行失败",
        "stdout": output[:500] if output else None,
        "exit_code": result.returncode,
        "status": "failed",
        "task_id": task_id,
    }


def _save_tasks(tasks_file: Path, data: Dict[str, Any]) -> None:
    """Persist task data back to disk (best-effort)."""
    try:
        with open(tasks_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("[ExecuteTask] 保存状态失败: %s", exc)
