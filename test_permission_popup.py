#!/usr/bin/env python3
"""
权限审批弹窗测试脚本

模拟前端UI用户输入，测试权限审批弹窗是否正常触发。

使用方法:
1. 确保 server_final.py 正在运行
2. 运行此脚本: python3 test_permission_popup.py
3. 观察前端是否弹出审批窗口
"""

import asyncio
import json
import time
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    print("⚠️ 需要安装 websockets: pip install websockets")


# ============================================================================
# 测试配置
# ============================================================================

SERVER_URL = "ws://localhost:8080"
SESSION_ID = f"test_session_{int(time.time())}"

# 测试用例：触发不同类型权限请求
TEST_CASES = [
    {
        "name": "网络访问 - HTTP请求",
        "message": "帮我访问 https://example.com 获取网页内容",
        "expected_permission": "internet",
        "expected_tool": "http_request",
    },
    {
        "name": "系统命令 - Shell执行",
        "message": "执行 ls -la 命令查看当前目录",
        "expected_permission": "system_cmd",
        "expected_tool": "shell_execute",
    },
    {
        "name": "文件读取 - 敏感文件",
        "message": "读取 /etc/passwd 文件内容",
        "expected_permission": "file_read",
        "expected_tool": "file_read",
    },
    {
        "name": "文件写入 - 创建文件",
        "message": "创建文件 /tmp/test.txt 内容是 hello world",
        "expected_permission": "file_write",
        "expected_tool": "file_write",
    },
    {
        "name": "代码执行 - Python",
        "message": "运行 Python 代码: print('hello world')",
        "expected_permission": "code_exec",
        "expected_tool": "code_execute",
    },
]


# ============================================================================
# WebSocket 客户端
# ============================================================================

class PermissionTestClient:
    """权限审批测试客户端"""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.ws = None
        self.received_requests = []
        self.decision_made = False
        
    async def connect(self):
        """连接到权限审批 WebSocket"""
        url = f"{SERVER_URL}/ws/permission/{self.session_id}"
        print(f"🔌 连接到: {url}")
        
        try:
            self.ws = await websockets.connect(url)
            
            # 订阅会话
            await self.ws.send(json.dumps({
                "type": "subscribe",
                "session_id": self.session_id
            }))
            
            # 等待确认
            response = await self.ws.recv()
            data = json.loads(response)
            print(f"✅ 已连接: {data}")
            
            return True
            
        except Exception as e:
            print(f"❌ 连接失败: {e}")
            return False
    
    async def listen_for_requests(self, timeout: int = 30):
        """监听权限请求"""
        print(f"👂 监听权限请求 (超时: {timeout}秒)...")
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # 非阻塞接收，设置较短超时
                message = await asyncio.wait_for(
                    self.ws.recv(),
                    timeout=1.0
                )
                
                data = json.loads(message)
                msg_type = data.get("type")
                
                print(f"\n📨 收到消息类型: {msg_type}")
                
                if msg_type == "permission_request":
                    # 权限请求！
                    self.received_requests.append(data)
                    print("\n" + "="*60)
                    print("🔔🔔🔔 权限审批请求弹出！")
                    print("="*60)
                    print(f"  工具: {data.get('tool_name', 'N/A')}")
                    print(f"  权限类型: {data.get('permission_type', 'N/A')}")
                    print(f"  描述: {data.get('description', 'N/A')}")
                    print(f"  风险等级: {data.get('risk_level', 'N/A')}")
                    print(f"  请求ID: {data.get('request_id', 'N/A')}")
                    print("="*60)
                    
                    # 自动批准（测试用）
                    await self.send_decision(
                        data.get("request_id"),
                        "allow_once"
                    )
                    
                elif msg_type == "pending_list":
                    print(f"📋 待处理请求列表: {len(data.get('requests', []))} 个")
                    
                elif msg_type == "decision_result":
                    print(f"✅ 决策结果: {data}")
                    self.decision_made = True
                    
                else:
                    print(f"📝 其他消息: {json.dumps(data, indent=2, ensure_ascii=False)[:200]}")
                    
            except asyncio.TimeoutError:
                # 继续等待
                continue
            except Exception as e:
                print(f"❌ 接收消息错误: {e}")
                break
        
        return self.received_requests
    
    async def send_decision(self, request_id: str, decision: str):
        """发送审批决策"""
        print(f"\n📤 发送决策: {decision} (请求ID: {request_id})")
        
        await self.ws.send(json.dumps({
            "type": "permission_decision",
            "request_id": request_id,
            "decision": decision
        }))
    
    async def close(self):
        """关闭连接"""
        if self.ws:
            await self.ws.close()
            print("🔌 连接已关闭")


# ============================================================================
# HTTP 客户端（发送聊天消息）
# ============================================================================

async def send_chat_message(message: str, session_id: str):
    """发送聊天消息到服务器"""
    import aiohttp
    
    url = f"{SERVER_URL.replace('ws', 'http')}/api/chat/stream"
    
    print(f"\n💬 发送聊天消息: {message[:50]}...")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={
                    "message": message,
                    "session_id": session_id,
                    "stream": True
                },
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                
                if response.status == 200:
                    print("✅ 消息已发送，等待响应...")
                    
                    # 读取流式响应
                    async for line in response.content:
                        if line:
                            try:
                                data = json.loads(line.decode().replace("data: ", "").strip())
                                if data.get("type") == "tool_result":
                                    print(f"🔧 工具执行结果: {data.get('tools_executed', [])}")
                            except:
                                pass
                else:
                    print(f"❌ 发送失败: HTTP {response.status}")
                    
    except Exception as e:
        print(f"❌ 请求错误: {e}")


# ============================================================================
# 测试运行器
# ============================================================================

async def run_test_case(test_case: dict, session_id: str):
    """运行单个测试用例"""
    print(f"\n{'='*60}")
    print(f"🧪 测试: {test_case['name']}")
    print(f"📝 消息: {test_case['message']}")
    print(f"🎯 预期权限: {test_case['expected_permission']}")
    print(f"{'='*60}")
    
    # 创建客户端
    client = PermissionTestClient(session_id)
    
    # 连接 WebSocket
    if not await client.connect():
        return {"success": False, "error": "连接失败"}
    
    # 启动监听任务
    listen_task = asyncio.create_task(
        client.listen_for_requests(timeout=30)
    )
    
    # 等待 WebSocket 准备好
    await asyncio.sleep(1)
    
    # 发送聊天消息
    chat_task = asyncio.create_task(
        send_chat_message(test_case["message"], session_id)
    )
    
    # 等待结果
    await asyncio.gather(listen_task, chat_task, return_exceptions=True)
    
    # 关闭连接
    await client.close()
    
    # 验证结果
    success = len(client.received_requests) > 0
    
    return {
        "test_name": test_case["name"],
        "success": success,
        "requests_received": len(client.received_requests),
        "expected_permission": test_case["expected_permission"],
        "actual_permissions": [
            r.get("permission_type") for r in client.received_requests
        ]
    }


async def run_all_tests():
    """运行所有测试"""
    print("\n" + "="*60)
    print("🚀 权限审批弹窗测试")
    print("="*60)
    print(f"服务器: {SERVER_URL}")
    print(f"会话ID: {SESSION_ID}")
    print("="*60)
    
    results = []
    
    for test_case in TEST_CASES:
        # 每个测试用例使用不同的会话ID
        test_session_id = f"{SESSION_ID}_{test_case['expected_permission']}"
        
        result = await run_test_case(test_case, test_session_id)
        results.append(result)
        
        # 测试间隔
        await asyncio.sleep(2)
    
    # 打印汇总
    print("\n" + "="*60)
    print("📊 测试结果汇总")
    print("="*60)
    
    for result in results:
        status = "✅ 通过" if result["success"] else "❌ 失败"
        print(f"{status} | {result['test_name']}")
        if result["success"]:
            print(f"       权限类型: {result['actual_permissions']}")
    
    # 统计
    passed = sum(1 for r in results if r["success"])
    total = len(results)
    
    print(f"\n总计: {passed}/{total} 通过")
    
    return results


# ============================================================================
# 简化测试（直接调用工具执行器）
# ============================================================================

def test_tool_executor_directly():
    """直接测试工具执行器权限检查"""
    print("\n" + "="*60)
    print("🔧 直接测试 ToolExecutor 权限检查")
    print("="*60)
    
    from tool_executor import ToolExecutor, analyze_and_execute_tools
    from pathlib import Path
    
    workspace = Path("/home/field/.nanobot/workspace")
    session_id = f"direct_test_{int(time.time())}"
    
    # 测试网络请求
    print("\n🧪 测试1: 网络访问权限")
    print("-" * 40)
    
    result = analyze_and_execute_tools(
        message="帮我访问 https://example.com 获取网页内容",
        snn_result=None,
        workspace=workspace,
        session_id=session_id
    )
    
    print(f"执行的工具: {result.get('tools_executed', [])}")
    print(f"输出: {result.get('combined_output', '')[:200]}")
    
    # 测试文件读取
    print("\n🧪 测试2: 文件读取权限")
    print("-" * 40)
    
    result = analyze_and_execute_tools(
        message="读取文件 /etc/passwd",
        snn_result=None,
        workspace=workspace,
        session_id=f"{session_id}_file"
    )
    
    print(f"执行的工具: {result.get('tools_executed', [])}")
    
    # 测试命令执行
    print("\n🧪 测试3: 系统命令权限")
    print("-" * 40)
    
    result = analyze_and_execute_tools(
        message="执行命令 ls -la",
        snn_result=None,
        workspace=workspace,
        session_id=f"{session_id}_shell"
    )
    
    print(f"执行的工具: {result.get('tools_executed', [])}")


def test_tool_executor_with_auto_approval():
    """测试带自动审批的工具执行器"""
    print("\n" + "="*60)
    print("🤖 自动审批模式测试")
    print("="*60)
    
    from tool_executor import ToolExecutor
    from approval_handler import AutoApprovalHandler, ApprovalDecision
    from pathlib import Path
    
    workspace = Path("/home/field/.nanobot/workspace")
    session_id = f"auto_test_{int(time.time())}"
    
    # 创建自动审批处理器（始终允许）
    auto_handler = AutoApprovalHandler(
        default_decision=ApprovalDecision.ALLOW_ONCE
    )
    
    executor = ToolExecutor(
        workspace=workspace,
        safe_mode=True,
        session_id=session_id,
        approval_handler=auto_handler
    )
    
    # 测试网络请求
    print("\n🧪 测试: 网络访问（自动允许）")
    print("-" * 40)
    
    result = executor.execute('http_request', {'url': 'https://example.com'})
    print(f"成功: {result.get('success')}")
    print(f"输出: {str(result.get('output', ''))[:100]}")
    
    # 测试命令执行
    print("\n🧪 测试: 系统命令（自动允许）")
    print("-" * 40)
    
    result = executor.execute('shell_execute', {'command': 'ls -la'})
    print(f"成功: {result.get('success')}")
    print(f"输出: {str(result.get('output', ''))[:100]}")


# ============================================================================
# 主入口
# ============================================================================

def main():
    """主函数"""
    print("""
╔══════════════════════════════════════════════════════════╗
║       Nanobot 权限审批弹窗测试                            ║
║                                                          ║
║  此脚本测试以下功能:                                       ║
║  1. WebSocket 连接到权限审批端点                           ║
║  2. 发送触发权限请求的消息                                 ║
║  3. 验证审批弹窗是否触发                                   ║
╚══════════════════════════════════════════════════════════╝
    """)
    
    import argparse
    
    parser = argparse.ArgumentParser(description="权限审批弹窗测试")
    parser.add_argument(
        "--direct", "-d",
        action="store_true",
        help="直接测试 ToolExecutor（不需要服务器运行）"
    )
    parser.add_argument(
        "--websocket", "-w",
        action="store_true",
        help="测试 WebSocket 连接（需要服务器运行）"
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="运行所有测试"
    )
    
    args = parser.parse_args()
    
    if args.direct:
        # 直接测试
        test_tool_executor_directly()
        
    elif args.websocket or args.all:
        # WebSocket 测试
        if not WEBSOCKETS_AVAILABLE:
            print("❌ 需要安装 websockets: pip install websockets")
            return
        
        try:
            import aiohttp
        except ImportError:
            print("❌ 需要安装 aiohttp: pip install aiohttp")
            return
        
        asyncio.run(run_all_tests())
        
    else:
        # 默认：直接测试
        print("运行直接测试模式（不需要服务器）...")
        print("使用 --websocket 或 -w 测试完整流程\n")
        test_tool_executor_directly()


if __name__ == "__main__":
    main()
