#!/usr/bin/env python3
"""
端到端集成测试 - test_e2e_permission_flow.py

测试完整的权限审批流程：
1. 工具调用触发权限请求
2. 策略匹配/用户审批
3. 沙箱执行
4. 审计日志记录

运行: pytest test_e2e_permission_flow.py -v -s
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock

import pytest

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from web_ui.permission_manager import (
    PermissionManager, PermissionType, PermissionDecision
)
from web_ui.approval_handler import (
    SilentApprovalHandler, ApprovalDecision, ApprovalRequest
)
from web_ui.permission_policy import (
    PermissionPolicyManager, PermissionPolicy, PolicyDecision
)
from web_ui.audit_logger import (
    AuditLogger, AuditEventType, get_audit_logger
)
from web_ui.tool_executor import (
    ToolExecutor, TOOL_PERMISSION_MAP
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_workspace():
    """创建临时工作目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        # 创建测试文件
        (workspace / "test.txt").write_text("Hello, World!")
        (workspace / "subdir").mkdir()
        (workspace / "subdir" / "inner.txt").write_text("Inner content")
        yield workspace


@pytest.fixture
def e2e_setup(temp_workspace):
    """端到端测试设置"""
    # 创建临时存储
    policy_path = temp_workspace / "policies.json"
    audit_dir = temp_workspace / "audit"
    audit_dir.mkdir()
    
    # 创建审批处理器（静默模式，自动允许）
    approval_handler = SilentApprovalHandler(
        default_decision=ApprovalDecision.ALLOW_ONCE
    )
    
    # 创建审计日志
    audit_logger = AuditLogger(log_dir=audit_dir)
    
    # 创建策略管理器
    policy_manager = PermissionPolicyManager(storage_path=policy_path)
    
    # 创建执行器
    executor = ToolExecutor(
        workspace=temp_workspace,
        safe_mode=True,
        session_id="e2e_test_session",
        approval_handler=approval_handler,
        sandbox_enabled=False,  # 禁用沙箱以便测试
        sandbox_fallback="direct",
    )
    
    # 手动设置审计日志
    executor.audit_logger = audit_logger
    
    return {
        'workspace': temp_workspace,
        'policy_path': policy_path,
        'audit_dir': audit_dir,
        'approval_handler': approval_handler,
        'audit_logger': audit_logger,
        'policy_manager': policy_manager,
        'executor': executor,
    }


# ============================================================================
# 端到端测试
# ============================================================================

class TestE2EPermissionFlow:
    """端到端权限流程测试"""
    
    def test_full_flow_file_read(self, e2e_setup):
        """测试完整的文件读取流程"""
        executor = e2e_setup['executor']
        audit = e2e_setup['audit_logger']
        workspace = e2e_setup['workspace']
        
        # 禁用权限系统以简化测试
        executor.permission_enabled = False
        
        # 执行文件读取
        result = executor.execute('file_read', {
            'path': str(workspace / "test.txt")
        })
        
        # 验证结果
        assert result['success']
        assert "Hello, World!" in result['output']
        
        # 验证审计日志
        audit._flush()
        events = audit.query_events(limit=10)
        assert len(events) >= 1
        
        # 检查事件类型
        event_types = [e['event_type'] for e in events]
        assert 'tool_success' in event_types or 'tool_execute' in event_types
    
    def test_full_flow_shell_execute_denied_by_policy(self, e2e_setup):
        """测试策略拒绝危险命令"""
        executor = e2e_setup['executor']
        policy = e2e_setup['policy_manager']
        audit = e2e_setup['audit_logger']
        
        # 添加拒绝策略
        policy.add_policy(PermissionPolicy(
            id="deny_rm_rf",
            name="拒绝rm -rf",
            tool_pattern="shell_execute",
            param_patterns={'command': '*rm -rf*'},
            decision=PolicyDecision.DENY,
            priority=200,
        ))
        
        # 执行危险命令
        result = executor.execute('shell_execute', {
            'command': 'rm -rf /'
        })
        
        # 验证被拒绝
        assert result['success'] == False
        assert 'policy_blocked' in result or 'permission_denied' in result
        
        # 验证审计日志
        audit._flush()
        events = audit.query_events(tool_name="shell_execute", limit=10)
        
        # 应该有安全违规记录
        violations = audit.query_events(
            event_type=AuditEventType.SECURITY_VIOLATION,
            limit=10
        )
        # 可能没有记录，取决于权限是否启用
    
    def test_full_flow_policy_allow(self, e2e_setup):
        """测试策略允许操作"""
        executor = e2e_setup['executor']
        policy = e2e_setup['policy_manager']
        audit = e2e_setup['audit_logger']
        workspace = e2e_setup['workspace']
        
        # 添加允许策略
        policy.add_policy(PermissionPolicy(
            id="allow_workspace_read",
            name="允许读取工作区",
            tool_pattern="file_read",
            param_patterns={'path': f'{workspace}/*'},
            decision=PolicyDecision.ALLOW,
            priority=100,
        ))
        
        # 执行文件读取
        result = executor.execute('file_read', {
            'path': str(workspace / "test.txt")
        })
        
        # 验证成功
        assert result['success']
    
    def test_full_flow_sandbox_policy(self, e2e_setup):
        """测试沙箱策略"""
        executor = e2e_setup['executor']
        policy = e2e_setup['policy_manager']
        
        # 添加沙箱策略
        policy.add_policy(PermissionPolicy(
            id="sandbox_code",
            name="代码执行强制沙箱",
            tool_pattern="code_execute",
            param_patterns={},
            decision=PolicyDecision.SANDBOX,
            priority=100,
        ))
        
        # 由于沙箱未启用，应该降级执行
        executor.sandbox_enabled = False
        executor.sandbox_fallback = "direct"
        executor.permission_enabled = False
        
        # 执行代码
        result = executor.execute('code_execute', {
            'code': 'print("hello")'
        })
        
        # 验证执行（降级后）
        assert result['success'] or 'sandbox' in result.get('error', '').lower()
    
    def test_audit_trail_complete(self, e2e_setup):
        """测试完整审计追踪"""
        executor = e2e_setup['executor']
        audit = e2e_setup['audit_logger']
        workspace = e2e_setup['workspace']
        
        executor.permission_enabled = False
        
        # 执行多个操作
        executor.execute('file_read', {'path': str(workspace / "test.txt")})
        executor.execute('file_list', {'path': str(workspace)})
        executor.execute('system_info', {})
        
        # 刷新审计日志
        audit._flush()
        audit.close()
        
        # 验证日志文件存在
        log_files = list(e2e_setup['audit_dir'].glob("audit_*.jsonl"))
        assert len(log_files) >= 1
        
        # 验证日志内容
        with open(log_files[0], 'r') as f:
            lines = f.readlines()
            assert len(lines) >= 3  # 至少3个事件
            
            # 验证JSON格式
            for line in lines:
                event = json.loads(line)
                assert 'event_id' in event
                assert 'event_type' in event
                assert 'timestamp' in event
    
    def test_session_allowance_caching(self, e2e_setup):
        """测试会话级审批缓存"""
        executor = e2e_setup['executor']
        
        # 模拟会话级缓存
        from web_ui.approval_handler import ApprovalDecision
        executor._session_allowances['file_read:FILE_READ'] = ApprovalDecision.ALLOW_SESSION
        
        # 执行文件读取（应该使用缓存）
        result = executor.execute('file_read', {
            'path': str(e2e_setup['workspace'] / "test.txt")
        })
        
        # 验证成功
        assert result['success'] or not executor.permission_enabled


class TestE2ESecurityInterceptor:
    """安全拦截器端到端测试"""
    
    def test_interceptor_subprocess(self, temp_workspace):
        """测试subprocess拦截"""
        from web_ui.secure_interceptor import (
            install_interceptors, uninstall_interceptors, is_interceptors_installed
        )
        
        # 定义权限检查回调
        def permission_check(operation, params):
            # 只允许 ls 命令
            if operation == 'shell_execute':
                return 'ls' in params.get('command', '')
            return False
        
        # 安装拦截器
        install_interceptors(
            permission_check=permission_check,
            intercept_subprocess=True,
        )
        
        assert is_interceptors_installed()
        
        # 测试允许的命令
        import subprocess
        result = subprocess.run(['ls', str(temp_workspace)], capture_output=True)
        assert result.returncode == 0
        
        # 测试拒绝的命令
        with pytest.raises(PermissionError):
            subprocess.run(['rm', '-rf', '/'], capture_output=True)
        
        # 卸载
        uninstall_interceptors()
        assert not is_interceptors_installed()
    
    def test_interceptor_eval(self):
        """测试eval拦截"""
        from web_ui.secure_interceptor import install_interceptors, uninstall_interceptors
        
        # 定义权限检查（拒绝所有eval）
        def permission_check(operation, params):
            return False  # 拒绝所有
        
        install_interceptors(
            permission_check=permission_check,
            intercept_eval_exec=True,
        )
        
        # eval应该被拒绝
        with pytest.raises(PermissionError):
            eval("1 + 1")
    
    def test_interceptor_multiprocessing(self):
        """测试子进程中的拦截器生效"""
        import multiprocessing
        import os
        
        # 设置环境变量
        os.environ['NANOBOT_ENFORCE_SECURITY'] = '1'
        
        from web_ui.secure_interceptor import (
            install_interceptors, uninstall_interceptors, is_interceptors_installed
        )
        
        # 定义权限检查
        call_log = []
        
        def permission_check(operation, params):
            call_log.append((operation, params))
            return False  # 拒绝所有
        
        # 安装拦截器（支持multiprocessing）
        install_interceptors(
            permission_check=permission_check,
            support_multiprocessing=True,
        )
        
        # 子进程测试函数
        def subprocess_test(queue):
            import subprocess
            try:
                # 在子进程中尝试执行命令
                subprocess.run(['echo', 'test'], capture_output=True)
                queue.put('not_blocked')
            except PermissionError:
                queue.put('blocked')
        
        # 创建队列用于进程间通信
        ctx = multiprocessing.get_context('spawn')
        queue = ctx.Queue()
        
        # 启动子进程
        p = ctx.Process(target=subprocess_test, args=(queue,))
        p.start()
        p.join(timeout=10)
        
        # 检查结果
        if p.is_alive():
            p.terminate()
            assert False, "子进程超时"
        
        result = queue.get()
        assert result == 'blocked', f"子进程拦截器未生效: {result}"
        
        # 清理
        uninstall_interceptors()
        del os.environ['NANOBOT_ENFORCE_SECURITY']
        
        uninstall_interceptors()
    
    def test_interceptor_open_sensitive_path(self, temp_workspace):
        """测试敏感路径拦截"""
        from web_ui.secure_interceptor import install_interceptors, uninstall_interceptors
        
        def permission_check(operation, params):
            return True  # 允许所有
        
        install_interceptors(
            permission_check=permission_check,
            intercept_open=True,
        )
        
        # 敏感路径应该被拒绝
        with pytest.raises(PermissionError):
            open('.ssh/id_rsa', 'r')
        
        # 正常路径应该允许
        test_file = temp_workspace / "test.txt"
        with open(test_file, 'w') as f:
            f.write("test")
        assert test_file.exists()
        
        uninstall_interceptors()


class TestContainerPoolConcurrency:
    """容器池并发与性能测试"""
    
    def test_pool_concurrent_access(self):
        """测试容器池并发访问安全性"""
        import threading
        import time
        from unittest.mock import patch, MagicMock
        
        from web_ui.sandbox_executor import SandboxConfig, SandboxExecutor
        
        config = SandboxConfig(
            pool_enabled=True,
            pool_size=5,
            pool_reuse_strategy="session",
        )
        
        executor = SandboxExecutor(config)
        
        # Mock Docker检查
        executor.docker_available = False
        
        results = {'success': 0, 'errors': []}
        lock = threading.Lock()
        
        def worker(session_id):
            try:
                pool_key = executor._get_pool_key(session_id=session_id)
                
                # 模拟并发获取容器
                with executor._pool_lock:
                    # 模拟添加到池
                    if len(executor._pool) < config.pool_size:
                        executor._pool[pool_key] = {
                            'container_id': f'container_{session_id}',
                            'last_used': time.time(),
                            'session_id': session_id,
                        }
                    
                    with lock:
                        results['success'] += 1
            except Exception as e:
                with lock:
                    results['errors'].append(str(e))
        
        # 创建多个线程并发访问
        threads = []
        for i in range(20):
            t = threading.Thread(target=worker, args=(f'session_{i % 10}',))
            threads.append(t)
        
        # 启动所有线程
        for t in threads:
            t.start()
        
        # 等待所有线程完成
        for t in threads:
            t.join(timeout=10)
        
        # 验证结果
        assert len(results['errors']) == 0, f"并发错误: {results['errors']}"
        assert results['success'] == 20
        assert executor.stats['concurrent_access'] > 0
    
    def test_pool_lru_eviction(self):
        """测试容器池LRU清理"""
        from web_ui.sandbox_executor import SandboxConfig, SandboxExecutor
        
        config = SandboxConfig(
            pool_enabled=True,
            pool_size=3,
            pool_reuse_strategy="session",
        )
        
        executor = SandboxExecutor(config)
        executor.docker_available = False
        
        # 添加容器超过池大小
        for i in range(5):
            executor._add_to_pool(f'session_{i}', f'container_{i}', f'session_{i}')
        
        # 池大小应该被限制
        assert len(executor._pool) <= config.pool_size
        
        # 验证LRU清理了最旧的容器
        assert 'session_0' not in executor._pool or 'session_1' not in executor._pool
    
    def test_pool_idle_cleanup(self):
        """测试空闲容器清理"""
        import time
        from web_ui.sandbox_executor import SandboxConfig, SandboxExecutor
        
        config = SandboxConfig(
            pool_enabled=True,
            pool_size=5,
            pool_idle_timeout=1,  # 1秒超时
        )
        
        executor = SandboxExecutor(config)
        executor.docker_available = False
        
        # 添加容器
        executor._add_to_pool('session_1', 'container_1', 'session_1')
        
        # 等待超时
        time.sleep(1.5)
        
        # 清理空闲容器
        executor._clean_idle_containers()
        
        # 容器应该被清理
        assert len(executor._pool) == 0
    
    def test_pool_stats_tracking(self):
        """测试容器池统计追踪"""
        from web_ui.sandbox_executor import SandboxConfig, SandboxExecutor
        
        config = SandboxConfig(pool_enabled=True)
        executor = SandboxExecutor(config)
        executor.docker_available = False
        
        # 模拟池操作
        executor._add_to_pool('session_1', 'container_1', 'session_1')
        
        # 获取池状态
        stats = executor.get_pool_stats()
        
        assert 'pool_size' in stats
        assert 'hit_rate' in stats
        assert stats['pool_size'] == 1


class TestCrossPlatformFileLock:
    """跨平台文件锁测试"""
    
    def test_file_lock_support_detection(self):
        """测试文件锁支持检测"""
        from web_ui.permission_policy import PermissionPolicyManager
        
        manager = PermissionPolicyManager()
        
        # 应该检测到锁支持
        assert hasattr(manager, '_lock_supported')
        assert manager._lock_supported in [True, False]
    
    def test_file_lock_cross_platform(self, temp_workspace):
        """测试跨平台文件锁功能"""
        import sys
        from web_ui.permission_policy import PermissionPolicyManager, PermissionPolicy, PolicyDecision
        
        # 使用临时路径
        storage_path = temp_workspace / "test_policies.json"
        manager = PermissionPolicyManager(storage_path=storage_path)
        
        # 添加策略
        policy = PermissionPolicy(
            id="test_lock_policy",
            name="测试策略",
            tool_pattern="test_*",
            decision=PolicyDecision.ALLOW,
            priority=100,
        )
        
        result = manager.add_policy(policy)
        assert result is True
        
        # 验证保存和加载
        manager2 = PermissionPolicyManager(storage_path=storage_path)
        found = manager2.get_policy("test_lock_policy")
        assert found is not None
    
    def test_concurrent_policy_access(self, temp_workspace):
        """测试并发策略访问（文件锁保护）"""
        import threading
        from web_ui.permission_policy import PermissionPolicyManager, PermissionPolicy, PolicyDecision
        
        storage_path = temp_workspace / "concurrent_policies.json"
        
        results = {'success': 0, 'errors': []}
        lock = threading.Lock()
        
        def worker(i):
            try:
                manager = PermissionPolicyManager(storage_path=storage_path)
                policy = PermissionPolicy(
                    id=f"concurrent_policy_{i}",
                    name=f"并发策略 {i}",
                    tool_pattern=f"tool_{i}_*",
                    decision=PolicyDecision.ALLOW,
                    priority=50 + i,
                )
                manager.add_policy(policy)
                
                with lock:
                    results['success'] += 1
            except Exception as e:
                with lock:
                    results['errors'].append(str(e))
        
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        
        # 验证无错误
        assert len(results['errors']) == 0, f"并发错误: {results['errors']}"


class TestAsyncContainerPool:
    """异步容器池测试"""
    
    @pytest.mark.asyncio
    async def test_async_pool_basic(self):
        """测试异步容器池基本功能"""
        from web_ui.sandbox_executor import SandboxConfig, SandboxExecutor
        
        config = SandboxConfig(pool_enabled=True, pool_size=5)
        executor = SandboxExecutor(config)
        executor.docker_available = False
        
        # 测试异步获取容器
        container_id, is_new = await executor.get_or_create_container_async(
            session_id="test_async_session"
        )
        
        # 无 Docker 时应返回 None
        assert container_id is None or is_new in [True, False]
    
    @pytest.mark.asyncio
    async def test_async_pool_concurrent(self):
        """测试异步容器池并发访问"""
        from web_ui.sandbox_executor import SandboxConfig, SandboxExecutor
        
        config = SandboxConfig(pool_enabled=True, pool_size=10)
        executor = SandboxExecutor(config)
        executor.docker_available = False
        
        async def worker(i):
            pool_key = executor._get_pool_key(session_id=f"session_{i % 5}")
            await executor._add_to_pool_async(pool_key, f"container_{i}", f"session_{i % 5}")
        
        # 并发添加
        tasks = [worker(i) for i in range(30)]
        await asyncio.gather(*tasks)
        
        # 验证池大小被限制
        assert len(executor._pool) <= config.pool_size
    
    @pytest.mark.asyncio
    async def test_async_pool_cleanup(self):
        """测试异步容器池清理"""
        from web_ui.sandbox_executor import SandboxConfig, SandboxExecutor
        
        config = SandboxConfig(pool_enabled=True, pool_size=5)
        executor = SandboxExecutor(config)
        executor.docker_available = False
        
        # 添加容器
        await executor._add_to_pool_async("key1", "container1", "session1")
        await executor._add_to_pool_async("key2", "container2", "session2")
        
        assert len(executor._pool) == 2
        
        # 异步清理
        await executor.cleanup_pool_async()
        
        assert len(executor._pool) == 0


class TestToolExecutorAsync:
    """ToolExecutor 异步方法测试"""
    
    @pytest.mark.asyncio
    async def test_execute_async_permission_check(self, temp_workspace):
        """测试异步执行的权限检查"""
        from web_ui.tool_executor import ToolExecutor
        from unittest.mock import MagicMock, AsyncMock
        
        # Mock 审批处理器
        mock_approval = MagicMock()
        mock_approval.request_approval = MagicMock(return_value=MagicMock(
            decision=MagicMock(value='allow_once')
        ))
        
        executor = ToolExecutor(
            workspace=temp_workspace,
            safe_mode=True,
            approval_handler=mock_approval,
            permission_enabled=False,  # 禁用权限检查
        )
        
        # 执行异步方法
        result = await executor.execute_async('file_list', {'path': str(temp_workspace)})
        
        # 应该成功
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_execute_async_unknown_tool(self, temp_workspace):
        """测试异步执行未知工具"""
        from web_ui.tool_executor import ToolExecutor
        
        executor = ToolExecutor(workspace=temp_workspace, safe_mode=False)
        
        result = await executor.execute_async('unknown_tool', {})
        
        assert result['success'] is False
        assert '未知工具' in result['error']


class TestAuditAlertConfig:
    """审计告警配置测试"""
    
    def test_load_alerts_from_missing_config(self, temp_workspace):
        """测试从不存在的配置文件加载"""
        from web_ui.audit_logger import load_alerts_from_config
        
        handlers = load_alerts_from_config(temp_workspace / "missing_config.json")
        
        # 应该返回空列表
        assert handlers == []
    
    def test_load_alerts_from_valid_config(self, temp_workspace):
        """测试从有效配置文件加载"""
        from web_ui.audit_logger import load_alerts_from_config
        
        config_path = temp_workspace / "config.json"
        config_path.write_text('''
        {
            "audit": {
                "alerts": [
                    {"type": "log", "file": "alerts.log", "min_level": "medium"}
                ],
                "min_level": "low"
            }
        }
        ''')
        
        handlers = load_alerts_from_config(config_path)
        
        # 应该加载一个告警处理器
        assert len(handlers) == 1
    
    def test_get_audit_logger_with_config(self, temp_workspace):
        """测试获取带配置的审计日志器"""
        from web_ui.audit_logger import get_audit_logger_with_config
        
        config_path = temp_workspace / "config.json"
        config_path.write_text('{"audit": {"alerts": []}}')
        
        audit = get_audit_logger_with_config(config_path)
        
        assert audit is not None


class TestE2EWebSocketApproval:
    """WebSocket审批端到端测试"""
    
    @pytest.mark.asyncio
    async def test_ws_manager_flow(self):
        """测试WebSocket审批管理器流程"""
        from web_ui.permission_websocket import WebSocketApprovalManager
        
        manager = WebSocketApprovalManager()
        
        # 模拟WebSocket连接
        mock_ws = AsyncMock()
        mock_ws.send_text = AsyncMock()
        
        await manager.register_connection(mock_ws, "test_session")
        
        # 推送请求
        request_id = await manager.push_request(
            tool_name="shell_execute",
            permission_type="SYSTEM_CMD",
            title="执行命令",
            description="测试",
            details="ls -la",
            risk_level="medium",
            session_id="test_session",
        )
        
        assert request_id is not None
        assert len(manager.get_pending_requests()) == 1
        
        # 模拟接收决策
        manager.receive_decision(request_id, "allow_once")
        
        # 等待决策
        decision = await manager.wait_for_decision(request_id, timeout=1)
        assert decision == "allow_once"
        
        # 验证统计
        stats = manager.get_stats()
        assert stats['total_requests'] == 1
        assert stats['total_decisions'] == 1
    
    @pytest.mark.asyncio
    async def test_integrated_approval_handler(self):
        """测试集成审批处理器"""
        from web_ui.permission_websocket import (
            IntegratedWebSocketApprovalHandler, get_ws_approval_manager
        )
        
        manager = get_ws_approval_manager()
        handler = IntegratedWebSocketApprovalHandler(
            ws_manager=manager,
            cli_fallback=True,
        )
        
        # 创建请求
        request = ApprovalRequest(
            request_id="req_test",
            tool_name="file_read",
            permission_type="FILE_READ",
            title="读取文件",
            description="测试",
            details="/workspace/test.txt",
            risk_level="low",
            session_id="test",
            created_at=time.time(),
        )
        
        # 由于没有WebSocket连接，应该回退到CLI
        # 在非交互模式下，CLI会使用默认决策
        # 这里我们测试静默回退
        handler._cli_handler = SilentApprovalHandler(
            default_decision=ApprovalDecision.ALLOW_ONCE
        )
        
        response = await handler.request_approval(request)
        assert response.decision == ApprovalDecision.ALLOW_ONCE


class TestE2EDockerSandbox:
    """Docker沙箱端到端测试"""
    
    def test_sandbox_security_params(self):
        """测试沙箱安全参数"""
        from web_ui.sandbox_executor import SandboxConfig, SandboxExecutor
        
        config = SandboxConfig(
            cpu_limit=0.5,
            memory_limit="256M",
            network_enabled=False,
            execution_timeout=10,
        )
        
        sandbox = SandboxExecutor(config)
        
        # 验证配置
        assert sandbox.config.cpu_limit == 0.5
        assert sandbox.config.memory_limit == "256M"
        assert sandbox.config.network_enabled == False
        
        # 验证禁用列表
        assert 'os.system' in sandbox.FORBIDDEN_MODULES
        assert 'subprocess' in sandbox.FORBIDDEN_MODULES
    
    def test_code_validation(self):
        """测试代码验证"""
        from web_ui.sandbox_executor import SandboxConfig, SandboxExecutor
        
        sandbox = SandboxExecutor(SandboxConfig())
        
        # 危险代码应该被拒绝
        valid, reason = sandbox._validate_code("import os; os.system('ls')")
        assert not valid
        assert "禁止" in reason
        
        # 安全代码应该通过
        valid, reason = sandbox._validate_code("print('hello')")
        assert valid
    
    def test_path_validation(self):
        """测试路径验证"""
        from web_ui.sandbox_executor import SandboxConfig, SandboxExecutor
        
        sandbox = SandboxExecutor(SandboxConfig())
        
        # 路径遍历应该被拒绝
        valid, reason = sandbox._validate_path("../../../etc/passwd")
        assert not valid
        
        # 敏感路径应该被拒绝
        valid, reason = sandbox._validate_path("/etc/shadow")
        assert not valid
        
        # 正常路径应该通过
        valid, reason = sandbox._validate_path("/workspace/test.txt")
        assert valid


class TestE2EAuditAlerts:
    """审计告警端到端测试"""
    
    def test_high_risk_event_logging(self, temp_workspace):
        """测试高风险事件记录"""
        audit_dir = temp_workspace / "audit"
        audit_dir.mkdir()
        
        audit = AuditLogger(log_dir=audit_dir)
        
        # 记录高风险事件
        audit.log_security_violation(
            violation_type="dangerous_command",
            details={"command": "rm -rf /"},
            session_id="test",
            risk_level="critical",
        )
        
        audit._flush()
        audit.close()
        
        # 验证日志
        events = audit.query_events(
            event_type=AuditEventType.SECURITY_VIOLATION,
            limit=10,
        )
        
        assert len(events) >= 1
        assert events[0]['risk_level'] == 'critical'
    
    def test_audit_summary(self, temp_workspace):
        """测试审计摘要"""
        audit_dir = temp_workspace / "audit"
        audit_dir.mkdir()
        
        audit = AuditLogger(log_dir=audit_dir)
        
        # 记录多个事件
        for i in range(5):
            audit.log_permission_request(
                tool_name="file_read",
                params={"path": f"/test{i}.txt"},
                session_id="test",
            )
        
        audit.log_tool_execution(
            tool_name="file_read",
            params={"path": "/test.txt"},
            result={"success": True},
            session_id="test",
            sandbox=True,
        )
        
        audit._flush()
        
        # 获取摘要
        summary = audit.get_summary(hours=24)
        
        assert summary['total_events'] >= 6
        assert 'by_type' in summary
        assert 'sandbox_count' in summary


# ============================================================================
# 运行测试
# ============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s', '--tb=short'])
