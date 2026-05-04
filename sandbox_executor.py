#!/usr/bin/env python3
"""
沙箱执行器 - SandboxExecutor

提供安全的隔离执行环境：
1. Docker容器隔离
2. 资源限制 (CPU/内存/磁盘)
3. 网络隔离 (白名单域名)
4. 文件系统隔离 (仅工作目录)
5. 超时控制

版本: 1.0.0
"""

import os
import json
import subprocess
import tempfile
import shutil
import time
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# Security verification metrics — queryable via get_security_metrics()
_SECCOMP_RETRIES = 0
_SECCOMP_PERMANENT_FAILURES = 0
_SECCOMP_TRANSIENT_FAILURES = 0
_SECCOMP_SUCCESSES = 0


def get_security_metrics() -> dict:
    """Return security verification metrics for monitoring/alerting."""
    return {
        "seccomp_retries": _SECCOMP_RETRIES,
        "seccomp_permanent_failures": _SECCOMP_PERMANENT_FAILURES,
        "seccomp_transient_failures": _SECCOMP_TRANSIENT_FAILURES,
        "seccomp_successes": _SECCOMP_SUCCESSES,
    }


# Throttle: emit success-path metrics at most once per interval to limit I/O.
import threading as _threading
_METRICS_LOG_INTERVAL_S = 60.0
_LAST_METRICS_LOG_TIME = 0.0
_METRICS_LOG_LOCK = _threading.Lock()


def _emit_security_metrics(force: bool = False) -> None:
    """Emit security metrics as structured log for external collection.

    force=True always emits (used on failure paths).
    Otherwise, emits at most once per _METRICS_LOG_INTERVAL_S seconds.
    Thread-safe via _METRICS_LOG_LOCK.
    """
    global _LAST_METRICS_LOG_TIME
    import time as _t
    now = _t.monotonic()
    with _METRICS_LOG_LOCK:
        if not force and (now - _LAST_METRICS_LOG_TIME) < _METRICS_LOG_INTERVAL_S:
            return
        _LAST_METRICS_LOG_TIME = now
    logger.info(
        "[Sandbox/Metrics] seccomp_successes=%d seccomp_retries=%d seccomp_permanent_failures=%d seccomp_transient_failures=%d",
        _SECCOMP_SUCCESSES, _SECCOMP_RETRIES, _SECCOMP_PERMANENT_FAILURES, _SECCOMP_TRANSIENT_FAILURES,
    )


@dataclass
class SandboxConfig:
    """沙箱配置"""
    # 资源限制
    cpu_limit: float = 1.0          # CPU核心数
    memory_limit: str = "512M"      # 内存限制
    disk_limit: str = "100M"        # 磁盘限制
    
    # 超时设置
    execution_timeout: int = 30     # 执行超时(秒)
    idle_timeout: int = 60          # 空闲超时(秒)
    
    # 网络设置
    network_enabled: bool = False   # 是否允许网络
    allowed_domains: List[str] = None  # 允许的域名白名单
    
    # 文件系统设置
    read_only_paths: List[str] = None   # 只读路径
    writable_paths: List[str] = None    # 可写路径
    
    # 安全设置
    allow_internet: bool = False    # 是否允许互联网访问
    allow_subprocess: bool = True   # 是否允许子进程（shell命令）
    allow_file_write: bool = True   # 是否允许文件写入
    
    # ========== 容器池设置 ==========
    pool_size: int = 2              # 容器池大小
    pool_enabled: bool = True       # 是否启用容器池
    pool_reuse_strategy: str = "session"  # none/session/tool
    pool_idle_timeout: int = 300    # 空闲容器超时(秒)
    
    def __post_init__(self):
        if self.allowed_domains is None:
            self.allowed_domains = [
                'api.openai.com',
                'api.anthropic.com',
                'huggingface.co',
                'github.com',
                'pypi.org',
            ]
        if self.read_only_paths is None:
            self.read_only_paths = ['/config', '/app']
        if self.writable_paths is None:
            self.writable_paths = ['/workspace', '/tmp']


class SandboxExecutor:
    """
    沙箱执行器
    
    使用Docker容器提供隔离的执行环境
    """
    
    # 禁止的Python模块
    FORBIDDEN_MODULES = {
        'os.system', 'os.popen', 'os.spawn',
        'subprocess', 'multiprocessing',
        'socket', 'requests', 'urllib',
        'ctypes', 'cffi',
        'importlib', '__import__',
        'eval', 'exec', 'compile',
        'pickle', 'marshal', 'shelve',
        'shutil.rmtree', 'tempfile.mktemp',
    }
    
    # 禁止的文件操作
    FORBIDDEN_PATHS = {
        '/etc/passwd', '/etc/shadow', '/etc/sudoers',
        '/root', '/home', '/var/log',
        '.ssh', '.gnupg', '.netrc',
        'credentials', 'secrets', '.env',
    }
    
    def __init__(self, config: SandboxConfig = None):
        self.config = config or SandboxConfig()
        self.container_id: Optional[str] = None
        self.workspace_path: Optional[Path] = None
        self.is_running: bool = False
        
        # 检查Docker是否可用
        self.docker_available = self._check_docker()
        
        # ========== 容器池（并发安全）==========
        import threading
        self._pool: Dict[str, dict] = {}  # {pool_key: {container_id, last_used, session_id}}
        self._pool_lock = threading.RLock()  # 可重入锁，支持嵌套调用
        self._async_lock = None  # 异步锁（延迟初始化）
        self._pool_cleaner_task = None
        self._cleaner_running = False
        
        # 统计信息
        self.stats = {
            'executions': 0,
            'total_time': 0,
            'errors': 0,
            'timeouts': 0,
            'pool_hits': 0,  # 容器池命中次数
            'pool_misses': 0,  # 容器池未命中次数
            'concurrent_access': 0,  # 并发访问计数
        }
    
    def _check_docker(self) -> bool:
        """检查Docker是否可用"""
        try:
            result = subprocess.run(
                ['docker', '--version'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                logger.info(f"[DockerSandbox] Docker可用: {result.stdout.strip()}")
                # 检查 Docker daemon 是否运行
                daemon_check = subprocess.run(
                    ['docker', 'info'],
                    capture_output=True, text=True, timeout=5
                )
                if daemon_check.returncode == 0:
                    logger.info("[DockerSandbox] Docker daemon 运行正常")
                else:
                    logger.warning(f"[DockerSandbox] Docker daemon 可能未运行: {daemon_check.stderr[:100]}")
                return True
        except Exception as e:
            logger.warning(f"[DockerSandbox] Docker不可用: {e}")
        return False
    
    def start_container(self, workspace: Path = None) -> bool:
        """启动沙箱容器"""
        if not self.docker_available:
            logger.warning("Docker不可用，使用本地沙箱模式")
            self._setup_local_sandbox(workspace)
            return True
        
        try:
            # 创建工作目录
            self.workspace_path = workspace or Path(tempfile.mkdtemp(prefix='sandbox_'))
            self.workspace_path.mkdir(parents=True, exist_ok=True)
            
            # 构建Docker命令（最小权限原则）
            cmd = [
                'docker', 'run', '-d',
                '--name', f'nanobot-sandbox-{int(time.time())}',
                '--rm',  # 退出时自动删除
                
                # ========== 资源限制 ==========
                '--cpus', str(self.config.cpu_limit),
                '--memory', self.config.memory_limit,
                '--memory-swap', self.config.memory_limit,  # 禁止swap
                '--pids-limit', '100',  # 限制进程数
                '--ulimit', 'nofile=100:100',  # 限制文件描述符
                
                # ========== 安全选项（最小权限）==========
                '--security-opt', 'no-new-privileges:true',
                # Use Docker's default seccomp profile (blocks ~44 dangerous syscalls)
                # Do NOT use seccomp=unconfined — it disables all syscall filtering.
                '--cap-drop', 'ALL',  # 移除所有能力
                # 不添加任何能力，保持最小权限
                
                # ========== 文件系统安全 ==========
                '--read-only',  # 只读根文件系统
                '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',  # 临时目录
                '--tmpfs', '/var/tmp:rw,noexec,nosuid,size=32m',
                '--tmpfs', '/run:rw,noexec,nosuid,size=16m',
                
                # ========== 网络设置 ==========
                '--network', 'none' if not self.config.network_enabled else 'bridge',
                
                # ========== 挂载工作目录 ==========
                '-v', f'{self.workspace_path}:/workspace:rw',
                # 挂载 tools 目录（包含 memory_manager.py 等）
                '-v', f'{self.workspace_path}/tools:/tools:ro',
                # 挂载 skills 目录
                '-v', f'{self.workspace_path}/skills:/skills:ro',
                
                # ========== 用户权限 ==========
                '--user', '1000:1000',  # 使用宿主机用户 ID，避免权限问题
                
                # ========== 环境变量 ==========
                '-e', 'NANOBOT_SANDBOX=true',
                '-e', 'PYTHONUNBUFFERED=1',
                '-e', 'HOME=/tmp',
                
                # ========== 使用Python镜像 ==========
                'python:3.12-slim',
                'tail', '-f', '/dev/null'  # 保持容器运行
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                self.container_id = result.stdout.strip()
                self.is_running = True
                logger.info(f"沙箱容器已启动: {self.container_id[:12]}")
                if not self._verify_container_security():
                    logger.error(
                        "[Sandbox/Security] ABORTING: container %s failed security verification, destroying",
                        self.container_id[:12],
                    )
                    self.stop_container()
                    return False
                return True
            else:
                logger.error(f"启动容器失败: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"启动沙箱失败: {e}")
            return False
    
    def _verify_container_security(self) -> bool:
        """Verify container security configuration after start.

        Checks:
        - Seccomp mode is enabled (Seccomp: 2 = SECCOMP_MODE_FILTER)
        - Capabilities are dropped (CapEff = 0)
        - Runs as non-root (uid != 0)

        Distinguishes transient errors (check could not execute, empty output)
        from permanent failures (check returned definitively bad value).
        Retries up to 3 times on transient errors with 1s delay.
        Returns False immediately on permanent failure — no retry.
        """
        if not self.container_id:
            return True  # no container to check

        import time as _time
        _MAX_RETRIES = 3
        _RETRY_DELAY_S = 1.0

        checks = [
            ("seccomp", "cat /proc/self/status | grep Seccomp"),
            ("caps", "cat /proc/self/status | grep CapEff"),
            ("uid", "id -u"),
        ]

        global _SECCOMP_RETRIES, _SECCOMP_PERMANENT_FAILURES, _SECCOMP_TRANSIENT_FAILURES, _SECCOMP_SUCCESSES
        for attempt in range(_MAX_RETRIES):
            all_passed = True
            any_transient = False

            for check_name, check_cmd in checks:
                try:
                    result = subprocess.run(
                        ['docker', 'exec', self.container_id, 'sh', '-c', check_cmd],
                        capture_output=True, text=True, timeout=5,
                    )
                    output = result.stdout.strip()

                    if result.returncode != 0 or not output:
                        # Command failed or empty output — transient
                        logger.warning(
                            "[Sandbox/Security] check '%s' returned empty/error (attempt %d/%d, container=%s)",
                            check_name, attempt + 1, _MAX_RETRIES, self.container_id[:12],
                        )
                        any_transient = True
                        all_passed = False
                        continue

                    if check_name == "seccomp":
                        if "2" not in output:
                            logger.error(
                                "[Sandbox/Security] PERMANENT FAIL: seccomp NOT in filter mode: %s (container=%s)",
                                output, self.container_id[:12],
                            )
                            _SECCOMP_PERMANENT_FAILURES += 1
                            _emit_security_metrics(force=True)
                            return False  # permanent — no retry
                        logger.info("[Sandbox/Security] seccomp=filter verified (container=%s)", self.container_id[:12])
                    elif check_name == "caps":
                        if output.endswith("0000000000000000"):
                            logger.info("[Sandbox/Security] capabilities=none verified (container=%s)", self.container_id[:12])
                        else:
                            logger.error(
                                "[Sandbox/Security] PERMANENT FAIL: capabilities NOT fully dropped: %s (container=%s)",
                                output, self.container_id[:12],
                            )
                            _SECCOMP_PERMANENT_FAILURES += 1
                            _emit_security_metrics(force=True)
                            return False  # permanent — no retry
                    elif check_name == "uid":
                        if output == "0":
                            logger.warning("[Sandbox/Security] container running as ROOT (container=%s)", self.container_id[:12])
                        else:
                            logger.info("[Sandbox/Security] uid=%s verified non-root (container=%s)", output, self.container_id[:12])

                except Exception as e:
                    logger.warning(
                        "[Sandbox/Security] check '%s' could not execute (attempt %d/%d): %s",
                        check_name, attempt + 1, _MAX_RETRIES, e,
                    )
                    any_transient = True
                    all_passed = False

            if all_passed:
                _SECCOMP_SUCCESSES += 1
                _emit_security_metrics()
                return True
            if not any_transient:
                # All failures were permanent — no point retrying
                return False
            if attempt < _MAX_RETRIES - 1:
                _SECCOMP_RETRIES += 1
                logger.info(
                    "[Sandbox/Security] Transient check failure, retrying in %.0fs (attempt %d/%d)",
                    _RETRY_DELAY_S, attempt + 1, _MAX_RETRIES,
                )
                _time.sleep(_RETRY_DELAY_S)

        _SECCOMP_TRANSIENT_FAILURES += 1
        _emit_security_metrics(force=True)
        logger.error(
            "[Sandbox/Security] Security verification incomplete after %d attempts (container=%s)",
            _MAX_RETRIES, self.container_id[:12],
        )
        return False

    def _setup_local_sandbox(self, workspace: Path = None):
        """设置本地沙箱（无Docker时）"""
        self.workspace_path = workspace or Path(tempfile.mkdtemp(prefix='sandbox_'))
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        self.is_running = True
        logger.info(f"本地沙箱已设置: {self.workspace_path}")
    
    def stop_container(self):
        """停止沙箱容器"""
        if self.container_id and self.docker_available:
            try:
                subprocess.run(
                    ['docker', 'stop', self.container_id],
                    capture_output=True, timeout=10
                )
                logger.info(f"沙箱容器已停止: {self.container_id[:12]}")
            except Exception as e:
                logger.warning(f"停止容器失败: {e}")
        
        self.container_id = None
        self.is_running = False
    
    # ========== 容器池管理 ==========
    
    def _get_pool_key(self, session_id: str = None, tool_name: str = None) -> str:
        """获取容器池键"""
        strategy = self.config.pool_reuse_strategy
        
        if strategy == "none":
            return f"unique_{time.time()}"
        elif strategy == "session":
            return f"session_{session_id or 'default'}"
        elif strategy == "tool":
            return f"tool_{tool_name or 'generic'}"
        else:
            return f"session_{session_id or 'default'}"
    
    def _get_pooled_container(self, pool_key: str) -> Optional[str]:
        """从池中获取可用容器（线程安全）"""
        if not self.config.pool_enabled:
            return None
        
        with self._pool_lock:
            self.stats['concurrent_access'] += 1
            
            if pool_key in self._pool:
                entry = self._pool[pool_key]
                container_id = entry['container_id']
                
                # 检查容器是否仍在运行
                try:
                    result = subprocess.run(
                        ['docker', 'inspect', '--format={{.State.Running}}', container_id],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and 'true' in result.stdout:
                        entry['last_used'] = time.time()
                        self.stats['pool_hits'] += 1
                        logger.info(f"[Pool] 命中容器: {container_id[:12]}")
                        return container_id
                except Exception as e:
                    logger.warning(f"[Pool] 容器检查失败: {e}")
                
                # 容器不可用，移除
                del self._pool[pool_key]
            
            self.stats['pool_misses'] += 1
            return None
    
    def _add_to_pool(self, pool_key: str, container_id: str, session_id: str = None):
        """将容器添加到池中（线程安全）"""
        if not self.config.pool_enabled:
            return
        
        with self._pool_lock:
            if len(self._pool) >= self.config.pool_size:
                oldest_key = min(self._pool.keys(), key=lambda k: self._pool[k]['last_used'])
                self._remove_from_pool_unlocked(oldest_key)
            
            self._pool[pool_key] = {
                'container_id': container_id,
                'last_used': time.time(),
                'session_id': session_id,
                'created_at': time.time(),
            }
            logger.info(f"[Pool] 添加容器: {container_id[:12]} -> {pool_key}")
    
    def _remove_from_pool(self, pool_key: str):
        """从池中移除容器（线程安全）"""
        with self._pool_lock:
            self._remove_from_pool_unlocked(pool_key)
    
    def _remove_from_pool_unlocked(self, pool_key: str):
        """从池中移除容器（无锁版本，内部使用）"""
        if pool_key in self._pool:
            entry = self._pool[pool_key]
            container_id = entry['container_id']
            
            try:
                subprocess.run(['docker', 'stop', container_id], capture_output=True, timeout=10)
                logger.info(f"[Pool] 停止容器: {container_id[:12]}")
            except Exception as e:
                logger.warning(f"[Pool] 停止容器失败: {e}")
            
            del self._pool[pool_key]
    
    def _clean_idle_containers(self):
        """清理空闲超时的容器（线程安全）"""
        current_time = time.time()
        idle_timeout = self.config.pool_idle_timeout
        
        with self._pool_lock:
            keys_to_remove = [k for k, v in self._pool.items() if current_time - v['last_used'] > idle_timeout]
            for key in keys_to_remove:
                logger.info(f"[Pool] 清理空闲容器: {key}")
                self._remove_from_pool_unlocked(key)
    
    def get_or_create_container(self, workspace: Path = None, session_id: str = None, tool_name: str = None) -> Tuple[Optional[str], bool]:
        """获取或创建容器，返回(container_id, is_new)"""
        pool_key = self._get_pool_key(session_id, tool_name)
        
        container_id = self._get_pooled_container(pool_key)
        if container_id:
            self.container_id = container_id
            self.is_running = True
            return container_id, False
        
        if self.start_container(workspace):
            self._add_to_pool(pool_key, self.container_id, session_id)
            return self.container_id, True
        
        return None, False
    
    def get_pool_stats(self) -> Dict:
        """获取容器池统计"""
        total = self.stats['pool_hits'] + self.stats['pool_misses']
        return {
            'pool_size': len(self._pool),
            'max_pool_size': self.config.pool_size,
            'pool_enabled': self.config.pool_enabled,
            'pool_hits': self.stats['pool_hits'],
            'pool_misses': self.stats['pool_misses'],
            'hit_rate': self.stats['pool_hits'] / total if total > 0 else 0,
        }
    
    # ========================================================================
    # 异步容器池支持
    # ========================================================================
    
    def _get_async_lock(self):
        """获取异步锁（延迟初始化）"""
        if self._async_lock is None:
            import asyncio
            self._async_lock = asyncio.Lock()
        return self._async_lock
    
    async def _get_pooled_container_async(self, pool_key: str) -> Optional[str]:
        """从池中获取可用容器（异步版本）"""
        if not self.config.pool_enabled:
            return None
        
        async with self._get_async_lock():
            self.stats['concurrent_access'] += 1
            
            if pool_key in self._pool:
                entry = self._pool[pool_key]
                container_id = entry['container_id']
                
                # 检查容器是否仍在运行
                try:
                    result = subprocess.run(
                        ['docker', 'inspect', '--format={{.State.Running}}', container_id],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and 'true' in result.stdout:
                        entry['last_used'] = time.time()
                        self.stats['pool_hits'] += 1
                        logger.info(f"[Pool] 命中容器: {container_id[:12]}")
                        return container_id
                except Exception as e:
                    logger.warning(f"[Pool] 容器检查失败: {e}")
                
                # 容器不可用，移除
                del self._pool[pool_key]
            
            self.stats['pool_misses'] += 1
            return None
    
    async def _add_to_pool_async(self, pool_key: str, container_id: str, session_id: str = None):
        """将容器添加到池中（异步版本）"""
        if not self.config.pool_enabled:
            return
        
        async with self._get_async_lock():
            if len(self._pool) >= self.config.pool_size:
                oldest_key = min(self._pool.keys(), key=lambda k: self._pool[k]['last_used'])
                await self._remove_from_pool_async(oldest_key)
            
            self._pool[pool_key] = {
                'container_id': container_id,
                'last_used': time.time(),
                'session_id': session_id,
                'created_at': time.time(),
            }
            logger.info(f"[Pool] 添加容器: {container_id[:12]} -> {pool_key}")
    
    async def _remove_from_pool_async(self, pool_key: str):
        """从池中移除容器（异步版本）"""
        if pool_key in self._pool:
            entry = self._pool[pool_key]
            container_id = entry['container_id']
            
            try:
                subprocess.run(['docker', 'stop', container_id], capture_output=True, timeout=10)
                logger.info(f"[Pool] 停止容器: {container_id[:12]}")
            except Exception as e:
                logger.warning(f"[Pool] 停止容器失败: {e}")
            
            del self._pool[pool_key]
    
    async def get_or_create_container_async(
        self, 
        workspace: Path = None, 
        session_id: str = None, 
        tool_name: str = None
    ) -> Tuple[Optional[str], bool]:
        """获取或创建容器（异步版本），返回(container_id, is_new)"""
        pool_key = self._get_pool_key(session_id, tool_name)
        
        container_id = await self._get_pooled_container_async(pool_key)
        if container_id:
            self.container_id = container_id
            self.is_running = True
            return container_id, False
        
        # 创建新容器（同步操作，Docker CLI 不支持异步）
        if self.start_container(workspace):
            await self._add_to_pool_async(pool_key, self.container_id, session_id)
            return self.container_id, True
        
        return None, False
    
    async def cleanup_pool_async(self):
        """清理所有池中容器（异步版本）"""
        async with self._get_async_lock():
            for key in list(self._pool.keys()):
                await self._remove_from_pool_async(key)
        logger.info("[Pool] 所有容器已清理")
    
    def cleanup_pool(self):
        """清理所有池中容器"""
        for key in list(self._pool.keys()):
            self._remove_from_pool(key)
        logger.info("[Pool] 所有容器已清理")
    
    def _validate_code(self, code: str) -> Tuple[bool, str]:
        """验证代码安全性"""
        # 检查禁止的模块
        for forbidden in self.FORBIDDEN_MODULES:
            if forbidden in code:
                return False, f"禁止使用: {forbidden}"
        
        # 检查禁止的路径
        for forbidden_path in self.FORBIDDEN_PATHS:
            if forbidden_path in code:
                return False, f"禁止访问路径: {forbidden_path}"
        
        return True, "验证通过"
    
    def _validate_path(self, path: str) -> Tuple[bool, str]:
        """验证路径安全性"""
        # 检查路径遍历
        if '..' in path:
            return False, "禁止路径遍历"
        
        # 检查禁止的路径
        for forbidden in self.FORBIDDEN_PATHS:
            if forbidden in path:
                return False, f"禁止访问: {forbidden}"
        
        return True, "验证通过"
    
    def execute_code(self, code: str, timeout: int = None) -> Dict[str, Any]:
        """
        在沙箱中执行Python代码
        
        Args:
            code: Python代码
            timeout: 超时秒数
            
        Returns:
            执行结果字典
        """
        timeout = timeout or self.config.execution_timeout
        start_time = time.time()
        
        # 验证代码
        valid, reason = self._validate_code(code)
        if not valid:
            return {
                'success': False,
                'error': f"安全验证失败: {reason}",
                'output': None,
                'blocked': True
            }
        
        self.stats['executions'] += 1
        
        try:
            if self.docker_available and self.container_id:
                # Docker容器执行
                result = self._execute_in_docker(code, timeout)
            else:
                # 本地沙箱执行
                result = self._execute_locally(code, timeout)
            
            self.stats['total_time'] += time.time() - start_time
            return result
            
        except subprocess.TimeoutExpired:
            self.stats['timeouts'] += 1
            return {
                'success': False,
                'error': f'执行超时 ({timeout}秒)',
                'output': None,
                'timeout': True
            }
        except Exception as e:
            self.stats['errors'] += 1
            return {
                'success': False,
                'error': str(e),
                'output': None
            }
    
    def _execute_in_docker(self, code: str, timeout: int) -> Dict[str, Any]:
        """在Docker容器中执行代码"""
        # 创建临时脚本文件
        script_path = self.workspace_path / 'script.py'
        script_path.write_text(code)
        
        logger.info(f"[DockerSandbox] 执行代码 (容器: {self.container_id[:12] if self.container_id else 'N/A'})")
        logger.debug(f"[DockerSandbox] 代码内容: {code[:100]}...")
        
        # 执行命令（不使用 --timeout，用 subprocess timeout 控制）
        cmd = [
            'docker', 'exec',
            self.container_id,
            'python', '/workspace/script.py'
        ]
        
        logger.info(f"[DockerSandbox] 执行命令: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout + 5
            )
            
            logger.info(f"[DockerSandbox] 执行结果: returncode={result.returncode}")
            if result.stdout:
                logger.debug(f"[DockerSandbox] stdout: {result.stdout[:200]}")
            if result.stderr:
                logger.warning(f"[DockerSandbox] stderr: {result.stderr[:200]}")
            
            return {
                'success': result.returncode == 0,
                'output': result.stdout,
                'error': result.stderr if result.returncode != 0 else None,
                'return_code': result.returncode
            }
        except subprocess.TimeoutExpired:
            logger.error(f"[DockerSandbox] 执行超时 ({timeout + 5}秒)")
            return {
                'success': False,
                'error': f'执行超时 ({timeout + 5}秒)',
                'output': None,
                'timeout': True
            }
        except Exception as e:
            logger.error(f"[DockerSandbox] 执行异常: {e}")
            return {
                'success': False,
                'error': str(e),
                'output': None
            }
    
    def _execute_locally(self, code: str, timeout: int) -> Dict[str, Any]:
        """在本地沙箱中执行代码"""
        import io
        import sys
        
        # 创建安全的执行环境
        safe_globals = {
            '__builtins__': {
                'print': print, 'len': len, 'range': range,
                'list': list, 'dict': dict, 'str': str,
                'int': int, 'float': float, 'bool': bool,
                'sum': sum, 'min': min, 'max': max,
                'sorted': sorted, 'enumerate': enumerate,
                'zip': zip, 'map': map, 'filter': filter,
                'abs': abs, 'round': round, 'pow': pow,
                'isinstance': isinstance, 'type': type,
                'True': True, 'False': False, 'None': None,
            }
        }
        
        # 捕获输出
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        
        try:
            exec(code, safe_globals)
            output = sys.stdout.getvalue()
            return {
                'success': True,
                'output': output if output else '代码执行成功（无输出）'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'执行错误: {str(e)}',
                'output': None
            }
        finally:
            sys.stdout = old_stdout
    
    def execute_command(self, command: str, timeout: int = None) -> Dict[str, Any]:
        """
        在沙箱中执行Shell命令
        
        Args:
            command: Shell命令
            timeout: 超时秒数
            
        Returns:
            执行结果字典
        """
        if not self.config.allow_subprocess:
            return {
                'success': False,
                'error': '沙箱禁止执行Shell命令',
                'output': None,
                'blocked': True
            }
        
        timeout = timeout or self.config.execution_timeout
        
        if self.docker_available and self.container_id:
            # 在 /workspace 目录下执行命令，设置 NANOBOT_WORKSPACE 环境变量
            cmd = ['docker', 'exec', '-w', '/workspace',
                   '-e', 'NANOBOT_WORKSPACE=/workspace',
                   '-e', 'HOME=/tmp',
                   self.container_id, 'sh', '-c', command]
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout
                )
                logger.info(f"[DockerSandbox] 命令执行: {command[:50]}... -> returncode={result.returncode}")
                if result.stderr and result.returncode != 0:
                    logger.warning(f"[DockerSandbox] stderr: {result.stderr[:200]}")
                return {
                    'success': result.returncode == 0,
                    'output': result.stdout,
                    'error': result.stderr if result.returncode != 0 else None,
                    'return_code': result.returncode
                }
            except subprocess.TimeoutExpired:
                return {
                    'success': False,
                    'error': f'命令超时 ({timeout}秒)',
                    'output': None,
                    'timeout': True
                }
        else:
            return {
                'success': False,
                'error': '本地沙箱不支持Shell命令执行',
                'output': None
            }
    
    def read_file(self, path: str) -> Dict[str, Any]:
        """在沙箱中读取文件"""
        valid, reason = self._validate_path(path)
        if not valid:
            return {'success': False, 'error': reason, 'output': None}
        
        full_path = self.workspace_path / path
        
        if not full_path.exists():
            return {'success': False, 'error': f'文件不存在: {path}', 'output': None}
        
        try:
            content = full_path.read_text()
            return {'success': True, 'output': content, 'path': str(full_path)}
        except Exception as e:
            return {'success': False, 'error': str(e), 'output': None}
    
    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        """在沙箱中写入文件"""
        if not self.config.allow_file_write:
            return {
                'success': False,
                'error': '沙箱禁止文件写入',
                'output': None,
                'blocked': True
            }
        
        valid, reason = self._validate_path(path)
        if not valid:
            return {'success': False, 'error': reason, 'output': None}
        
        full_path = self.workspace_path / path
        
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
            return {
                'success': True,
                'output': f'文件已写入: {path}',
                'path': str(full_path),
                'size': len(content)
            }
        except Exception as e:
            return {'success': False, 'error': str(e), 'output': None}
    
    def get_stats(self) -> Dict[str, Any]:
        """获取沙箱统计信息"""
        return {
            **self.stats,
            'docker_available': self.docker_available,
            'container_running': self.is_running,
            'container_id': self.container_id[:12] if self.container_id else None,
            'workspace': str(self.workspace_path) if self.workspace_path else None,
            'config': {
                'cpu_limit': self.config.cpu_limit,
                'memory_limit': self.config.memory_limit,
                'network_enabled': self.config.network_enabled,
            }
        }
    
    def cleanup(self):
        """清理沙箱资源"""
        self.stop_container()
        
        if self.workspace_path and self.workspace_path.exists():
            try:
                shutil.rmtree(self.workspace_path)
                logger.info(f"沙箱工作目录已清理: {self.workspace_path}")
            except Exception as e:
                logger.warning(f"清理工作目录失败: {e}")
        
        self.workspace_path = None


# ============================================================================
# 工厂函数
# ============================================================================

def create_sandbox(config: SandboxConfig = None) -> SandboxExecutor:
    """创建沙箱执行器"""
    return SandboxExecutor(config)


def quick_sandbox_execute(code: str, timeout: int = 30) -> Dict[str, Any]:
    """
    快速沙箱执行（一次性）
    
    创建临时沙箱，执行代码，然后清理
    """
    sandbox = SandboxExecutor()
    sandbox.start_container()
    
    try:
        return sandbox.execute_code(code, timeout)
    finally:
        sandbox.cleanup()


# ============================================================================
# 示例用法
# ============================================================================

if __name__ == '__main__':
    # 创建沙箱
    config = SandboxConfig(
        cpu_limit=0.5,
        memory_limit="256M",
        network_enabled=False,
    )
    
    sandbox = SandboxExecutor(config)
    
    print("启动沙箱...")
    if sandbox.start_container():
        print(f"沙箱状态: {sandbox.get_stats()}")
        
        # 执行代码
        result = sandbox.execute_code('print("Hello from sandbox!")')
        print(f"执行结果: {result}")
        
        # 清理
        sandbox.cleanup()
    else:
        print("沙箱启动失败")
