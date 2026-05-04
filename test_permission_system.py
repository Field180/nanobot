#!/usr/bin/env python3
"""
权限系统测试套件 - test_permission_system.py

全面测试权限系统的各个组件：
1. PermissionManager 测试
2. ApprovalHandler 测试
3. SandboxExecutor 测试
4. PermissionPolicy 测试
5. AuditLogger 测试
6. ToolExecutor 集成测试

运行: pytest test_permission_system.py -v
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
    PermissionManager, PermissionType, PermissionDecision,
    PermissionRequest, PermissionRule
)
from web_ui.approval_handler import (
    ApprovalHandler, CLIApprovalHandler, WebSocketApprovalHandler,
    SilentApprovalHandler, ApprovalRequest, ApprovalDecision
)
from web_ui.sandbox_executor import (
    SandboxExecutor, SandboxConfig
)
from web_ui.permission_policy import (
    PermissionPolicyManager, PermissionPolicy, PolicyDecision
)
from web_ui.audit_logger import (
    AuditLogger, AuditEventType, AuditEvent
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_storage():
    """创建临时存储目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def permission_manager(temp_storage):
    """创建权限管理器"""
    storage_path = temp_storage / "permissions.json"
    return PermissionManager(storage_path=storage_path)


@pytest.fixture
def policy_manager(temp_storage):
    """创建策略管理器"""
    storage_path = temp_storage / "policies.json"
    return PermissionPolicyManager(storage_path=storage_path)


@pytest.fixture
def audit_logger(temp_storage):
    """创建审计日志记录器"""
    log_dir = temp_storage / "audit"
    return AuditLogger(log_dir=log_dir)


@pytest.fixture
def sandbox_config():
    """创建沙箱配置"""
    return SandboxConfig(
        cpu_limit=0.5,
        memory_limit="256M",
        network_enabled=False,
        execution_timeout=10,
    )


# ============================================================================
# PermissionManager 测试
# ============================================================================

class TestPermissionManager:
    """权限管理器测试"""
    
    def test_permission_types(self):
        """测试权限类型定义"""
        assert PermissionType.INTERNET.value == "internet"
        assert PermissionType.FILE_READ.value == "file_read"
        assert PermissionType.FILE_WRITE.value == "file_write"
        assert PermissionType.SYSTEM_CMD.value == "system_cmd"
        assert PermissionType.CODE_EXEC.value == "code_exec"
        assert PermissionType.DOCKER_EXEC.value == "docker_exec"
        assert PermissionType.DATABASE_ACCESS.value == "database"
    
    def test_permission_decisions(self):
        """测试权限决策定义"""
        assert PermissionDecision.DENY.value == "deny"
        assert PermissionDecision.ALLOW_ONCE.value == "allow_once"
        assert PermissionDecision.ALLOW_SESSION.value == "allow_session"
        assert PermissionDecision.ALLOW_ALWAYS.value == "allow_always"
    
    def test_create_permission_request(self, permission_manager):
        """测试创建权限请求"""
        request = permission_manager.create_request(
            permission_type=PermissionType.INTERNET,
            title="测试请求",
            description="测试描述",
            details="https://example.com",
            session_id="test_session"
        )
        
        assert request.permission_type == PermissionType.INTERNET
        assert request.title == "测试请求"
        assert request.status == "pending"
        assert request.request_id.startswith("req_")
    
    def test_make_decision(self, permission_manager):
        """测试权限决策"""
        request = permission_manager.create_request(
            permission_type=PermissionType.FILE_READ,
            title="读取文件",
            description="测试",
            details="/workspace/test.txt",
            session_id="test_session"
        )
        
        # 做出决策
        result = permission_manager.decide(
            request.request_id,
            PermissionDecision.ALLOW_SESSION
        )
        
        assert result is True
        assert request.status == "approved"
        assert request.decision == PermissionDecision.ALLOW_SESSION
    
    def test_session_permissions(self, permission_manager):
        """测试会话级权限"""
        # 创建会话权限
        permission_manager.set_session_permission(
            "test_session",
            PermissionType.FILE_READ,
            PermissionDecision.ALLOW_SESSION
        )
        
        # 检查权限
        decision = permission_manager.check_session_permission(
            "test_session",
            PermissionType.FILE_READ
        )
        
        assert decision == PermissionDecision.ALLOW_SESSION
    
    def test_persistent_rules(self, permission_manager):
        """测试持久化规则"""
        # 添加规则
        rule = PermissionRule(
            permission_type=PermissionType.INTERNET,
            pattern="https://example.com/*",
            decision=PermissionDecision.ALLOW_ALWAYS,
            created_at=time.time()
        )
        permission_manager.add_rule(rule)
        
        # 检查规则
        rules = permission_manager.get_rules()
        assert len(rules) == 1
        assert rules[0].pattern == "https://example.com/*"
    
    def test_rule_persistence(self, permission_manager, temp_storage):
        """测试规则持久化"""
        # 添加规则
        permission_manager.add_rule(PermissionRule(
            permission_type=PermissionType.FILE_READ,
            pattern="/workspace/*",
            decision=PermissionDecision.ALLOW_ALWAYS,
            created_at=time.time()
        ))
        
        # 创建新管理器，验证规则加载
        new_manager = PermissionManager(storage_path=temp_storage / "permissions.json")
        rules = new_manager.get_rules()
        assert len(rules) >= 1


# ============================================================================
# ApprovalHandler 测试
# ============================================================================

class TestApprovalHandler:
    """审批处理器测试"""
    
    @pytest.mark.asyncio
    async def test_silent_approval_handler(self):
        """测试静默审批处理器"""
        handler = SilentApprovalHandler(
            default_decision=ApprovalDecision.ALLOW_ONCE
        )
        
        request = ApprovalRequest(
            request_id="req_001",
            tool_name="file_read",
            permission_type="FILE_READ",
            title="读取文件",
            description="测试",
            details="/workspace/test.txt",
            risk_level="low",
            session_id="test_session",
            created_at=time.time()
        )
        
        response = await handler.request_approval(request)
        
        assert response.decision == ApprovalDecision.ALLOW_ONCE
        assert response.reason == "静默模式自动决策"
    
    @pytest.mark.asyncio
    async def test_cli_approval_handler_with_cache(self):
        """测试CLI审批处理器的缓存功能"""
        handler = CLIApprovalHandler()
        
        # 设置会话级决策
        handler.session_decisions["test_session:file_read:FILE_READ"] = \
            ApprovalDecision.ALLOW_SESSION
        
        request = ApprovalRequest(
            request_id="req_002",
            tool_name="file_read",
            permission_type="FILE_READ",
            title="读取文件",
            description="测试",
            details="/workspace/test.txt",
            risk_level="low",
            session_id="test_session",
            created_at=time.time()
        )
        
        response = await handler.request_approval(request)
        
        # 应该使用缓存决策，不需要用户输入
        assert response.decision == ApprovalDecision.ALLOW_SESSION
        assert "缓存" in response.reason or "会话" in response.reason
    
    @pytest.mark.asyncio
    async def test_websocket_approval_handler(self):
        """测试WebSocket审批处理器"""
        # 创建模拟的WebSocket管理器
        mock_ws = AsyncMock()
        
        handler = WebSocketApprovalHandler(
            websocket_manager=mock_ws,
            timeout=5
        )
        
        request = ApprovalRequest(
            request_id="req_003",
            tool_name="shell_execute",
            permission_type="SYSTEM_CMD",
            title="执行命令",
            description="测试",
            details="ls -la",
            risk_level="medium",
            session_id="test_session",
            created_at=time.time()
        )
        
        # 模拟响应
        def simulate_response():
            time.sleep(0.1)
            handler.receive_response(ApprovalResponse(
                request_id="req_003",
                decision=ApprovalDecision.ALLOW_ONCE
            ))
        
        import threading
        t = threading.Thread(target=simulate_response)
        t.start()
        
        response = await handler.request_approval(request)
        
        assert response.decision == ApprovalDecision.ALLOW_ONCE
        assert mock_ws.broadcast.called
    
    def test_approval_request_to_dict(self):
        """测试审批请求序列化"""
        request = ApprovalRequest(
            request_id="req_004",
            tool_name="http_request",
            permission_type="INTERNET",
            title="网络请求",
            description="测试",
            details="https://example.com",
            risk_level="medium",
            session_id="test_session",
            created_at=time.time()
        )
        
        data = request.to_dict()
        
        assert data['request_id'] == "req_004"
        assert data['tool_name'] == "http_request"
        assert 'created_at' in data


# ============================================================================
# SandboxExecutor 测试
# ============================================================================

class TestSandboxExecutor:
    """沙箱执行器测试"""
    
    def test_sandbox_config(self, sandbox_config):
        """测试沙箱配置"""
        assert sandbox_config.cpu_limit == 0.5
        assert sandbox_config.memory_limit == "256M"
        assert sandbox_config.network_enabled == False
        assert len(sandbox_config.allowed_domains) > 0
    
    def test_code_validation(self, sandbox_config):
        """测试代码验证"""
        sandbox = SandboxExecutor(sandbox_config)
        
        # 危险代码应该被拒绝
        valid, reason = sandbox._validate_code("import os; os.system('ls')")
        assert not valid
        assert "禁止" in reason
        
        # 安全代码应该通过
        valid, reason = sandbox._validate_code("print('hello')")
        assert valid
    
    def test_path_validation(self, sandbox_config):
        """测试路径验证"""
        sandbox = SandboxExecutor(sandbox_config)
        
        # 路径遍历应该被拒绝
        valid, reason = sandbox._validate_path("../../../etc/passwd")
        assert not valid
        
        # 敏感路径应该被拒绝
        valid, reason = sandbox._validate_path("/etc/shadow")
        assert not valid
        
        # 正常路径应该通过
        valid, reason = sandbox._validate_path("/workspace/test.txt")
        assert valid
    
    def test_local_sandbox_execution(self, sandbox_config):
        """测试本地沙箱执行"""
        sandbox = SandboxExecutor(sandbox_config)
        sandbox._setup_local_sandbox()
        
        # 执行简单代码
        result = sandbox.execute_code("result = 2 + 2; print(result)")
        
        assert result['success']
        assert '4' in result['output']
    
    def test_sandbox_stats(self, sandbox_config):
        """测试沙箱统计"""
        sandbox = SandboxExecutor(sandbox_config)
        
        stats = sandbox.get_stats()
        
        assert 'docker_available' in stats
        assert 'container_running' in stats
        assert 'executions' in stats
    
    def test_docker_unavailable_fallback(self, sandbox_config):
        """测试Docker不可用时的降级"""
        sandbox = SandboxExecutor(sandbox_config)
        sandbox.docker_available = False
        
        # 应该使用本地沙箱
        sandbox.start_container()
        
        assert sandbox.is_running
        assert sandbox.workspace_path is not None


# ============================================================================
# PermissionPolicy 测试
# ============================================================================

class TestPermissionPolicy:
    """权限策略测试"""
    
    def test_policy_matching(self, policy_manager):
        """测试策略匹配"""
        # 匹配工作区文件读取
        decision = policy_manager.get_decision(
            'file_read',
            {'path': '/workspace/test.txt'}
        )
        assert decision == PolicyDecision.ALLOW
    
    def test_policy_priority(self, policy_manager):
        """测试策略优先级"""
        # 危险命令应该被拒绝（高优先级）
        decision = policy_manager.get_decision(
            'shell_execute',
            {'command': 'rm -rf /'}
        )
        assert decision == PolicyDecision.DENY
    
    def test_sandbox_policy(self, policy_manager):
        """测试沙箱策略"""
        # 代码执行应该在沙箱中
        decision = policy_manager.get_decision(
            'code_execute',
            {'code': 'print("hello")'}
        )
        assert decision == PolicyDecision.SANDBOX
    
    def test_add_custom_policy(self, policy_manager):
        """测试添加自定义策略"""
        policy = PermissionPolicy(
            id="custom_001",
            name="自定义策略",
            tool_pattern="http_request",
            param_patterns={'url': 'https://api.example.com/*'},
            decision=PolicyDecision.ALLOW,
            priority=50,
        )
        
        result = policy_manager.add_policy(policy)
        assert result
        
        # 测试匹配
        decision = policy_manager.get_decision(
            'http_request',
            {'url': 'https://api.example.com/test'}
        )
        assert decision == PolicyDecision.ALLOW
    
    def test_policy_expiration(self):
        """测试策略过期"""
        policy = PermissionPolicy(
            id="expiring_001",
            name="过期策略",
            tool_pattern="test",
            param_patterns={},
            decision=PolicyDecision.ALLOW,
            expires_at=time.time() - 100  # 已过期
        )
        
        assert policy.is_expired()
    
    def test_wildcard_pattern_matching(self):
        """测试通配符模式匹配"""
        policy = PermissionPolicy(
            id="wildcard_001",
            name="通配符测试",
            tool_pattern="file_*",
            param_patterns={},
            decision=PolicyDecision.ALLOW,
        )
        
        assert policy.matches('file_read', {})
        assert policy.matches('file_write', {})
        assert not policy.matches('shell_execute', {})
    
    def test_regex_pattern_matching(self):
        """测试正则模式匹配"""
        policy = PermissionPolicy(
            id="regex_001",
            name="正则测试",
            tool_pattern=".*",
            param_patterns={'url': '^https://[a-z]+\\.example\\.com/.*$'},
            decision=PolicyDecision.ALLOW,
        )
        
        assert policy.matches('http_request', {'url': 'https://api.example.com/test'})
        assert not policy.matches('http_request', {'url': 'http://example.com/test'})


# ============================================================================
# AuditLogger 测试
# ============================================================================

class TestAuditLogger:
    """审计日志测试"""
    
    def test_log_permission_request(self, audit_logger):
        """测试记录权限请求"""
        event_id = audit_logger.log_permission_request(
            tool_name="shell_execute",
            params={"command": "ls"},
            session_id="test_session",
            risk_level="medium",
        )
        
        assert event_id.startswith("evt_")
        assert audit_logger.stats['total_events'] == 1
    
    def test_log_tool_execution(self, audit_logger):
        """测试记录工具执行"""
        audit_logger.log_tool_execution(
            tool_name="file_read",
            params={"path": "/workspace/test.txt"},
            result={"success": True, "output": "content"},
            session_id="test_session",
            duration_ms=50.5,
            sandbox=True,
        )
        
        assert audit_logger.stats['total_events'] == 1
    
    def test_log_security_violation(self, audit_logger):
        """测试记录安全违规"""
        audit_logger.log_security_violation(
            violation_type="dangerous_command",
            details={"command": "rm -rf /"},
            session_id="test_session",
            risk_level="critical",
        )
        
        assert audit_logger.stats['by_risk']['critical'] == 1
    
    def test_query_events(self, audit_logger):
        """测试查询事件"""
        # 记录多个事件
        for i in range(5):
            audit_logger.log_permission_request(
                tool_name="file_read",
                params={"path": f"/test{i}.txt"},
                session_id="test_session",
            )
        
        audit_logger._flush()
        
        # 查询
        events = audit_logger.query_events(
            tool_name="file_read",
            limit=10
        )
        
        assert len(events) == 5
    
    def test_sensitive_data_sanitization(self, audit_logger):
        """测试敏感数据脱敏"""
        event = AuditEvent(
            event_id="evt_test",
            event_type=AuditEventType.PERMISSION_REQUEST,
            timestamp=time.time(),
            session_id="test",
            user_id="test",
            params={
                'password': 'secret123',
                'api_key': 'sk-12345678901234567890',
                'normal': 'data',
            }
        )
        
        data = event.to_dict()
        
        assert data['params']['password'] == '[REDACTED]'
        assert '[API_KEY_REDACTED]' in data['params']['api_key']
        assert data['params']['normal'] == 'data'
    
    def test_get_summary(self, audit_logger):
        """测试获取摘要"""
        # 记录一些事件
        audit_logger.log_permission_request(
            tool_name="shell_execute",
            params={"command": "ls"},
            session_id="test",
        )
        audit_logger.log_tool_execution(
            tool_name="shell_execute",
            params={"command": "ls"},
            result={"success": True},
            session_id="test",
            sandbox=True,
        )
        
        audit_logger._flush()
        
        summary = audit_logger.get_summary(hours=24)
        
        assert 'total_events' in summary
        assert 'by_type' in summary


# ============================================================================
# ToolExecutor 集成测试
# ============================================================================

class TestToolExecutorIntegration:
    """ToolExecutor集成测试"""
    
    def test_tool_permission_map(self):
        """测试工具权限映射"""
        from web_ui.tool_executor import TOOL_PERMISSION_MAP
        
        # 验证关键工具已映射
        assert 'http_request' in TOOL_PERMISSION_MAP
        assert 'shell_execute' in TOOL_PERMISSION_MAP
        assert 'code_execute' in TOOL_PERMISSION_MAP
        assert 'file_read' in TOOL_PERMISSION_MAP
        assert 'file_write' in TOOL_PERMISSION_MAP
    
    def test_sandbox_policy_config(self):
        """测试沙箱策略配置"""
        from web_ui.tool_executor import ToolExecutor
        
        policy = ToolExecutor.SANDBOX_POLICY
        
        assert 'always_sandbox' in policy
        assert 'never_sandbox' in policy
        assert 'shell_execute' in policy['always_sandbox']
        assert 'code_execute' in policy['always_sandbox']
    
    @pytest.mark.asyncio
    async def test_permission_check_flow(self, temp_storage):
        """测试权限检查流程"""
        from web_ui.tool_executor import ToolExecutor
        
        # 创建执行器
        executor = ToolExecutor(
            workspace=temp_storage,
            safe_mode=True,
            session_id="test_session"
        )
        
        # 禁用权限系统测试基本功能
        executor.permission_enabled = False
        
        # 测试json_parse（无风险）
        result = executor.execute('json_parse', {'text': '{"a": 1}'})
        assert result['success']
    
    def test_sandbox_decision_logic(self, temp_storage):
        """测试沙箱决策逻辑"""
        from web_ui.tool_executor import ToolExecutor, PermissionType
        
        executor = ToolExecutor(workspace=temp_storage)
        
        # 高风险工具应该使用沙箱
        assert executor._should_use_sandbox('shell_execute', {})
        assert executor._should_use_sandbox('code_execute', {})
        
        # 低风险工具不应该使用沙箱
        assert not executor._should_use_sandbox('file_list', {})
        assert not executor._should_use_sandbox('json_parse', {})


# ============================================================================
# 端到端测试
# ============================================================================

class TestEndToEnd:
    """端到端测试"""
    
    @pytest.mark.asyncio
    async def test_full_permission_flow(self, temp_storage):
        """测试完整权限流程"""
        # 1. 创建权限管理器
        perm_manager = PermissionManager(
            storage_path=temp_storage / "permissions.json"
        )
        
        # 2. 创建审批处理器
        approval_handler = SilentApprovalHandler(
            default_decision=ApprovalDecision.ALLOW_ONCE
        )
        
        # 3. 创建审计日志
        audit = AuditLogger(log_dir=temp_storage / "audit")
        
        # 4. 创建请求
        request = perm_manager.create_request(
            permission_type=PermissionType.FILE_READ,
            title="读取测试文件",
            description="测试描述",
            details="/workspace/test.txt",
            session_id="test_session"
        )
        
        # 5. 模拟审批
        perm_manager.decide(request.request_id, PermissionDecision.ALLOW_ONCE)
        
        # 6. 记录审计
        audit.log_permission_decision(
            event_id=request.request_id,
            decision="allow_once",
            tool_name="file_read",
            session_id="test_session"
        )
        
        # 7. 验证
        assert request.status == "approved"
        assert audit.stats['total_events'] == 1
        
        audit.close()
    
    def test_policy_driven_decision(self, temp_storage):
        """测试策略驱动决策"""
        # 创建策略管理器
        policy_manager = PermissionPolicyManager(
            storage_path=temp_storage / "policies.json"
        )
        
        # 添加策略
        policy_manager.add_policy(PermissionPolicy(
            id="test_policy",
            name="测试策略",
            tool_pattern="file_read",
            param_patterns={'path': '/workspace/test/*'},
            decision=PolicyDecision.ALLOW,
            priority=100,
        ))
        
        # 测试决策
        decision = policy_manager.get_decision(
            'file_read',
            {'path': '/workspace/test/file.txt'}
        )
        
        assert decision == PolicyDecision.ALLOW
        
        # 测试不匹配的情况
        decision = policy_manager.get_decision(
            'file_read',
            {'path': '/other/file.txt'}
        )
        
        # 应该回退到默认策略或ASK
        assert decision in [PolicyDecision.ALLOW, PolicyDecision.ASK]


# ============================================================================
# 运行测试
# ============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
