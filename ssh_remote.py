"""
SSH Remote Management Module (P9 extraction from server_final.py)
==================================================================
SSH helpers for controlling remote Ollama instances.
"""

import logging
import os
from contextlib import contextmanager
from pathlib import Path

import paramiko

from model_discovery import parse_ollama_ps_models

logger = logging.getLogger(__name__)

# ---------- SSH config (from env, same defaults as server_final) ----------
OLLAMA_REMOTE_HOST = os.environ.get("NANOBOT_OLLAMA_SSH_HOST", "192.168.140.1")
OLLAMA_REMOTE_USER = os.environ.get("NANOBOT_OLLAMA_SSH_USER", "field")
OLLAMA_REMOTE_SSH_KEY = os.environ.get("NANOBOT_OLLAMA_SSH_KEY", str(Path.home() / ".ssh" / "id_rsa"))
OLLAMA_REMOTE_SSH_PORT = int(os.environ.get("NANOBOT_OLLAMA_SSH_PORT", "22"))
OLLAMA_REMOTE_SSH_TIMEOUT = int(os.environ.get("NANOBOT_OLLAMA_SSH_TIMEOUT", "10"))


# ---------- core helpers ----------

def ssh_connect():
    """建立 SSH 连接"""
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=OLLAMA_REMOTE_HOST,
            port=OLLAMA_REMOTE_SSH_PORT,
            username=OLLAMA_REMOTE_USER,
            key_filename=OLLAMA_REMOTE_SSH_KEY,
            timeout=OLLAMA_REMOTE_SSH_TIMEOUT,
            allow_agent=False,
            look_for_keys=False
        )
        return client
    except Exception as e:
        logger.error(f"SSH 连接失败: {e}")
        return None


@contextmanager
def ssh_client():
    """SSH 连接上下文管理器"""
    client = ssh_connect()
    try:
        yield client
    finally:
        if client:
            client.close()


def run_ssh_command(client: "paramiko.SSHClient", command: str, timeout: int = 20) -> tuple[int, str, str]:
    """执行 SSH 命令并返回 (exit_code, stdout, stderr)"""
    if not client:
        return 255, "", "SSH client is None"
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        exit_status = stdout.channel.recv_exit_status()
        return exit_status, out, err
    except Exception as exc:
        return 255, "", str(exc)


def _cmd(command: str) -> str:
    """
    在 Windows 上，直接执行某些命令（如 `ollama`）可能不会被正确识别，
    需要通过 `cmd /c` 来执行。
    """
    return f'cmd /c "{command}"'


def _windows_quote(text: str) -> str:
    """为 Windows 命令行参数添加双引号"""
    return f'"{text}"'


def stop_remote_ollama() -> bool:
    """通过 SSH 尝试停止远程 Ollama 的正在运行模型；必要时再 taskkill。"""
    try:
        with ssh_client() as client:
            if not client:
                logger.error("stop_remote_ollama: SSH client unavailable")
                return False

            # 0) 尝试定位 ollama 可执行文件路径（避免 PATH 不存在导致 stop/ps 不工作）
            where_code, where_out, where_err = run_ssh_command(client, _cmd("where ollama"))
            logger.info(f"[remote ollama] where ollama exit={where_code} stdout={where_out} stderr={where_err}")
            ollama_path = ""
            if where_code == 0 and where_out:
                ollama_path = where_out.splitlines()[0].strip()
            ollama = _windows_quote(ollama_path) if ollama_path else "ollama"

            # 1) 优先使用 `ollama stop <model>`
            code, ps_out, ps_err = run_ssh_command(client, _cmd(f"{ollama} ps"))
            logger.info(f"[remote ollama] ollama ps exit={code} stdout={ps_out} stderr={ps_err}")
            models = parse_ollama_ps_models(ps_out)

            stopped_any = False
            for model in models:
                stop_code, stop_out, stop_err = run_ssh_command(client, _cmd(f"{ollama} stop {_windows_quote(model)}"))
                logger.info(
                    f"[remote ollama] ollama stop model={model} exit={stop_code} stdout={stop_out} stderr={stop_err}"
                )
                if stop_code == 0:
                    stopped_any = True

            # 折中方案：仅按模型停止（不杀全局 ollama 进程）
            if models:
                return stopped_any

            # 如果没有任何运行中模型，则无需停止
            return False
    except Exception as e:
        logger.error(f"stop_remote_ollama failed: {e}")
        return False


def kill_remote_ollama() -> bool:
    return stop_remote_ollama()


def is_remote_ollama_running() -> bool:
    """检查远程 Ollama 是否运行"""
    try:
        with ssh_client() as client:
            stdin, stdout, stderr = client.exec_command("tasklist | findstr ollama.exe")
            output = stdout.read().decode().strip()
            return bool(output)
    except Exception as e:
        logger.error(f"检查远程 Ollama 状态失败: {e}")
        return False
