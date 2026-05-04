#!/usr/bin/env python3
"""
测试脚本：诊断"立即执行"按钮任务查找问题
"""

import json
import sys
from pathlib import Path

# 添加web_ui路径
workspace = Path.home() / ".nanobot" / "workspace"
web_ui_path = workspace / "web_ui"
sys.path.insert(0, str(web_ui_path))

print("="*60)
print("测试：立即执行任务查找")
print("="*60)

# 1. 读取 scheduled_tasks.json 文件
tasks_file = workspace / "scheduled_tasks.json"
print(f"\n1. 任务文件: {tasks_file}")
print(f"   文件存在: {tasks_file.exists()}")

file_tasks = []
if tasks_file.exists():
    try:
        with open(tasks_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            file_tasks = data.get('tasks', [])
        print(f"   文件中任务数量: {len(file_tasks)}")
        for t in file_tasks:
            print(f"     - ID: {t.get('id')}, 名称: {t.get('name')}, 状态: {t.get('status')}")
    except Exception as e:
        print(f"   读取失败: {e}")
else:
    print("   文件不存在！")

# 2. 检查内存中的 SCHEDULED_TASKS
print(f"\n2. 内存中的 SCHEDULED_TASKS:")
try:
    # 导入 server_final 模块（不启动服务器）
    import importlib.util
    spec = importlib.util.spec_from_file_location("server_final", web_ui_path / "server_final.py")
    server_module = importlib.util.module_from_spec(spec)
    
    # 只加载模块不执行，检查 SCHEDULED_TASKS
    print(f"   尝试导入 server_final.py...")
    
    # 由于会启动服务器，我们直接读取文件中的 SCHEDULED_TASKS 定义
    server_file = web_ui_path / "server_final.py"
    content = server_file.read_text(encoding='utf-8')
    
    # 查找 SCHEDULED_TASKS 定义
    if 'SCHEDULED_TASKS = {}' in content:
        print("   SCHEDULED_TASKS 初始化为空字典 {}")
        print("   问题找到！：SCHEDULED_TASKS 是空的，但 execute_task_now API 从中查找任务")
    
    # 查找有多少处修改 SCHEDULED_TASKS
    import re
    assignments = re.findall(r'SCHEDULED_TASKS\[.*?\]', content)
    print(f"   文件中 SCHEDULED_TASKS 赋值次数: {len(assignments)}")
    
except Exception as e:
    print(f"   检查失败: {e}")

# 3. 测试 execute_task_now 的查找逻辑
print(f"\n3. 测试任务查找逻辑:")
print(f"   execute_task_now API 中的查找代码:")
print(f"   ```")
print(f"   for t in SCHEDULED_TASKS.values():  # 在内存字典中查找")
print(f"       if t.get('id') == task_id:")
print(f"           task = t")
print(f"           break")
print(f"   ```")

# 4. 问题诊断
print(f"\n4. 问题诊断:")
print(f"   前端显示的任务来自: scheduled_tasks.json 文件")
print(f"   后端查找任务的位置: SCHEDULED_TASKS 内存字典")
print(f"   ")
print(f"   如果 SCHEDULED_TASKS 为空，则找不到任务！")
print(f"   ")
print(f"   解决方案:")
print(f"   a) 让 execute_task_now 从 scheduled_tasks.json 读取任务")
print(f"   b) 或者从 /api/tasks/status 返回的数据中传递完整任务信息")

# 5. 修复建议
print(f"\n5. 修复建议:")
print(f"   修改 executeTaskNow 前端函数，传递完整任务信息:")
print(f"   ```javascript")
print(f"   async function executeTaskNow(taskId, taskName, taskCommand) {{")
print(f"       // 从界面获取完整任务信息并传递给后端")
print(f"   }}")
print(f"   ```")
print(f"   ")
print(f"   或修改后端，从文件读取任务:")
print(f"   ```python")
print(f"   # 在 execute_task_now 中从文件读取任务")
print(f"   tasks_file = workspace / 'scheduled_tasks.json'")
print(f"   with open(tasks_file, 'r') as f:")
print(f"       data = json.load(f)")
print(f"       tasks = data.get('tasks', [])")
print(f"   # 在 tasks 列表中查找 task_id")
print(f"   ```")

print(f"\n" + "="*60)
print("测试完成")
print("="*60)
