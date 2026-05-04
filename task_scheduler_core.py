"""
Task Scheduler Core Module (P9b extraction from server_final.py)
================================================================
Background scheduled-task loop and auto-start logic for the task scheduler daemon.
"""

import asyncio
import logging
import subprocess
import time as time_module
from datetime import datetime, timedelta
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

# ========== 定时任务调度器 ==========
SCHEDULED_TASKS: dict = {}  # task_id -> {name, interval, enabled, last_run, next_run, callback}


async def run_scheduled_tasks():
    """后台任务调度器"""
    while True:
        now = datetime.now()

        for task_id, task in list(SCHEDULED_TASKS.items()):
            if not task["enabled"]:
                continue

            next_run = task.get("next_run")
            if next_run and now >= next_run:
                try:
                    # 执行任务
                    if task["callback"]:
                        await task["callback"]()

                    task["last_run"] = now.isoformat()
                    task["next_run"] = (now + timedelta(seconds=task["interval"])).isoformat()
                    logger.info(f"[定时任务] 执行: {task['name']}")
                except Exception as e:
                    logger.error(f"[定时任务] 错误: {task['name']} - {e}")

        await asyncio.sleep(10)  # 每10秒检查一次


async def auto_start_task_scheduler(workspace: Path = None):
    """自动启动任务调度器进程"""
    try:
        if workspace is None:
            workspace = Path.home() / ".nanobot" / "workspace"

        scheduler_script = workspace / "scheduler.py"

        # 检查调度器是否已在运行
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline', [])
                    if cmdline and any('scheduler.py' in str(c) for c in cmdline):
                        logger.info(f"[任务调度器] 调度器已在运行 (PID: {proc.info['pid']})")
                        return
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            logger.warning(f"[任务调度器] 检查进程失败: {e}")

        # 检查是否有任务配置文件
        tasks_file = workspace / "scheduled_tasks.json"
        if not tasks_file.exists():
            logger.info("[任务调度器] 无任务配置文件，跳过自动启动")
            return

        # 检查调度器脚本
        if not scheduler_script.exists():
            # 使用 tools/scheduler.py
            tools_scheduler = workspace / "tools" / "scheduler.py"
            if tools_scheduler.exists():
                scheduler_script = tools_scheduler
            else:
                logger.warning("[任务调度器] 调度器脚本不存在，跳过自动启动")
                return

        # 启动调度器进程
        try:
            process = subprocess.Popen(
                ['python3', str(scheduler_script), 'daemon'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(workspace),
                start_new_session=True
            )

            time_module.sleep(1)

            if process.poll() is None:
                logger.info(f"[任务调度器] 自动启动成功 (PID: {process.pid})")
            else:
                logger.warning("[任务调度器] 调度器启动后立即退出")

        except Exception as e:
            logger.error(f"[任务调度器] 自动启动失败: {e}")

    except Exception as e:
        logger.error(f"[任务调度器] 自动启动异常: {e}")
