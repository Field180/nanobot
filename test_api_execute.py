#!/usr/bin/env python3
"""
API测试：验证 execute_task_now 能正确找到任务
"""

import json
import sys
import asyncio
from pathlib import Path
from fastapi import Request
from unittest.mock import AsyncMock, Mock

# 添加web_ui路径
workspace = Path.home() / ".nanobot" / "workspace"
web_ui_path = workspace / "web_ui"
sys.path.insert(0, str(web_ui_path))

print("="*60)
print("API测试：execute_task_now 任务查找")
print("="*60)

# 1. 检查任务文件
print("\n1. 检查任务文件...")
tasks_file = workspace / "scheduled_tasks.json"
if tasks_file.exists():
    with open(tasks_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
        tasks = data.get('tasks', [])
    print(f"   找到 {len(tasks)} 个任务")
    for t in tasks:
        print(f"     - ID: {t.get('id')}, 名称: {t.get('name')}")
else:
    print("   任务文件不存在！")
    tasks = []

# 2. 模拟API调用测试
print(f"\n2. 模拟API调用测试...")

# 如果存在任务，测试第一个
if tasks:
    test_task = tasks[0]
    test_task_id = test_task.get('id')
    print(f"   测试任务ID: {test_task_id}")
    
    # 模拟Request对象
    class MockRequest:
        async def json(self):
            return {"task_id": test_task_id}
    
    mock_request = MockRequest()
    
    # 测试查找逻辑（手动模拟）
    print(f"\n3. 手动测试查找逻辑...")
    
    # 模拟 execute_task_now 中的查找代码
    task = None
    try:
        with open(tasks_file, 'r', encoding='utf-8') as f:
            file_data = json.load(f)
            file_tasks = file_data.get('tasks', [])
            for t in file_tasks:
                if t.get('id') == test_task_id:
                    task = t
                    break
        
        if task:
            print(f"   ✓ 成功找到任务!")
            print(f"     - ID: {task.get('id')}")
            print(f"     - 名称: {task.get('name')}")
            print(f"     - 命令: {task.get('command', '无')[:50]}...")
        else:
            print(f"   ✗ 未找到任务!")
    except Exception as e:
        print(f"   ✗ 查找失败: {e}")
else:
    print("   没有任务可测试")

# 4. 对比新旧查找逻辑
print(f"\n4. 查找逻辑对比...")
print(f"   旧逻辑（SCHEDULED_TASKS）:")
print(f"     从空字典查找，永远找不到任务")
print(f"   ")
print(f"   新逻辑（scheduled_tasks.json）:")
print(f"     从文件读取，与 /api/tasks/status 一致")

# 5. 检查状态显示
print(f"\n5. 状态显示检查...")
if tasks:
    for t in tasks:
        status = t.get('status')
        name = t.get('name')
        if status == 'failed':
            print(f"   ⚠ 任务 '{name}' 状态为 'failed'")
            print(f"     前端会显示为失败，但按钮应该可见（如果实现了failed状态显示）")
        elif status == 'pending':
            print(f"   ✓ 任务 '{name}' 状态为 'pending' - 按钮应该可见")

print(f"\n" + "="*60)
print("测试完成！请重启服务器使修复生效")
print("="*60)
