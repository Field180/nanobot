#!/usr/bin/env python3
"""测试脚本：模拟前端向模型提问"""

import requests
import json
import time

BASE_URL = "http://127.0.0.1:8080"

def test_chat_stream(message: str, session_id: str = "test_session_001"):
    """测试流式聊天端点"""
    print(f"\n{'='*60}")
    print(f"测试消息: {message}")
    print(f"Session ID: {session_id}")
    print(f"{'='*60}\n")
    
    url = f"{BASE_URL}/api/chat/stream"
    
    payload = {
        "message": message,
        "session_id": session_id,
        "backend": "ollm",
        "model": "qwen3.5:35b"
    }
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, stream=True, timeout=60)
        
        print(f"状态码: {response.status_code}")
        print(f"\n响应内容:\n")
        
        full_response = ""
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                if line_str.startswith('data: '):
                    data_str = line_str[6:]  # 去掉 'data: ' 前缀
                    if data_str.strip():
                        try:
                            data = json.loads(data_str)
                            if 'content' in data:
                                content = data['content']
                                print(content, end='', flush=True)
                                full_response += content
                            elif 'error' in data:
                                print(f"\n❌ 错误: {data['error']}")
                            elif 'done' in data and data['done']:
                                print("\n\n[流式传输完成]")
                        except json.JSONDecodeError:
                            print(f"[非JSON数据: {data_str[:100]}]")
        
        return full_response
        
    except requests.exceptions.RequestException as e:
        print(f"❌ 请求错误: {e}")
        return None

def test_status():
    """测试状态端点"""
    url = f"{BASE_URL}/api/status"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        print(f"服务状态: {data}")
        return data
    except Exception as e:
        print(f"❌ 状态检查失败: {e}")
        return None

if __name__ == "__main__":
    # 先检查服务状态
    print("检查服务状态...")
    status = test_status()
    
    if not status:
        print("服务未运行，退出测试")
        exit(1)
    
    print("\n服务正常运行，开始测试...\n")
    
    # 测试1：简单问候
    print("\n" + "="*60)
    print("测试 1: 简单问候")
    print("="*60)
    response1 = test_chat_stream("你好，请简单介绍一下你自己", "test_001")
    
    time.sleep(2)
    
    # 测试2：多轮对话
    print("\n" + "="*60)
    print("测试 2: 多轮对话")
    print("="*60)
    response2 = test_chat_stream("刚才我说了什么？", "test_001")
    
    time.sleep(2)
    
    # 测试3：新会话
    print("\n" + "="*60)
    print("测试 3: 新会话测试")
    print("="*60)
    response3 = test_chat_stream("1+1等于多少？", "test_002")
    
    print("\n\n" + "="*60)
    print("测试完成")
    print("="*60)
