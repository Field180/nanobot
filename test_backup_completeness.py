#!/usr/bin/env python3
"""
测试备份完备性 - 模拟前端触发 backup_daily 任务
验证所有关键文件和目录是否被正确备份
"""

import sys
import json
import shutil
from pathlib import Path
from datetime import datetime

# 添加路径
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from backup_system import BackupSystem

# 工作区路径
WORKSPACE = Path.home() / ".nanobot" / "workspace"
BACKUP_DIR = WORKSPACE / "backups"

# 预期备份的关键文件（与 backup_system.py 保持一致）
EXPECTED_FILES = [
    # 记忆系统
    "memory/KNOWLEDGE_GRAPH.json",
    "reflections/reflection_history.json",
    "skill_chains.json",
    "MEMORY.md",
    "HISTORY.md",
    "SOUL.md",
    # 配置文件
    "config.json",
    "neuro_config.yaml",
    "scheduled_tasks.json",
    # 知识和决策
    "knowledge_base.json",
    "decisions.json",
    "contexts.json",
    "workflows.json",
    "long_term_goals.json",
    # 学习和改进
    "quality_scores.jsonl",
    "improvement_history.jsonl",
    "learning_data.json",
    "improvement_learning.json",
    # 其他重要数据
    "health_reports.json",
    "performance_data.json",
    "quality_stats.json",
    "notifications.json",
    # 数据库文件
    "context_compression.db",
    # 项目文档
    "PROJECT_STRUCTURE.md",
    "CHANGELOG.md",
    "SECURITY.md",
    "SECURITY_HARDENING.md",
    "requirements.txt"
]

# 预期备份的关键目录
EXPECTED_DIRS = [
    # 核心模块（不可删除）
    "core",
    # 记忆和反思
    "memory",
    "reflections",
    # 配置
    "config",
    # 技能和工具
    "skills",
    "tools",
    # Web UI（关键）
    "web_ui",
    # 安全
    "security",
    # 会话
    "sessions",
    "web_sessions",
    # 日志和审计
    "logs",
    "audit_logs",
    # 监控和报告
    "monitoring",
    "reports",
    # 项目文档
    "docs",
    # 数据和脚本
    "data",
    "scripts",
    # 评估基准
    "evals"
]


def test_backup_completeness():
    """测试备份完备性"""
    print("=" * 60)
    print("📋 备份完备性测试")
    print("=" * 60)
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 创建备份系统实例
    backup = BackupSystem()
    
    # 创建测试备份
    test_backup_name = f"test_completeness_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"🚀 创建测试备份: {test_backup_name}")
    print()
    
    try:
        backup_name = backup.create_backup(test_backup_name, "full")
        backup_path = BACKUP_DIR / backup_name
        
        if not backup_path.exists():
            print("❌ 备份目录不存在！")
            return False
        
        print(f"✅ 备份目录创建成功: {backup_path}")
        print()
        
        # 检查文件备份
        print("📁 检查文件备份...")
        print("-" * 40)
        
        files_backed = []
        files_missing = []
        files_not_exist_in_source = []
        
        for file_path in EXPECTED_FILES:
            src = WORKSPACE / file_path
            dst = backup_path / file_path
            
            if not src.exists():
                files_not_exist_in_source.append(file_path)
                status = "⚪ 源不存在"
            elif dst.exists():
                files_backed.append(file_path)
                status = "✅ 已备份"
            else:
                files_missing.append(file_path)
                status = "❌ 未备份"
            
            print(f"  {status}: {file_path}")
        
        print()
        
        # 检查目录备份
        print("📂 检查目录备份...")
        print("-" * 40)
        
        dirs_backed = []
        dirs_missing = []
        dirs_not_exist_in_source = []
        
        for dir_path in EXPECTED_DIRS:
            src_dir = WORKSPACE / dir_path
            dst_dir = backup_path / dir_path
            
            if not src_dir.exists():
                dirs_not_exist_in_source.append(dir_path)
                status = "⚪ 源不存在"
            elif dst_dir.exists():
                dirs_backed.append(dir_path)
                status = "✅ 已备份"
            else:
                dirs_missing.append(dir_path)
                status = "❌ 未备份"
            
            print(f"  {status}: {dir_path}/")
        
        print()
        
        # 统计结果
        print("=" * 60)
        print("📊 测试结果统计")
        print("=" * 60)
        
        total_files = len(EXPECTED_FILES)
        total_dirs = len(EXPECTED_DIRS)
        
        print(f"\n📄 文件统计:")
        print(f"  - 预期文件: {total_files}")
        print(f"  - 成功备份: {len(files_backed)} ✅")
        print(f"  - 未被备份: {len(files_missing)} ❌")
        print(f"  - 源不存在: {len(files_not_exist_in_source)} ⚪")
        
        print(f"\n📁 目录统计:")
        print(f"  - 预期目录: {total_dirs}")
        print(f"  - 成功备份: {len(dirs_backed)} ✅")
        print(f"  - 未被备份: {len(dirs_missing)} ❌")
        print(f"  - 源不存在: {len(dirs_not_exist_in_source)} ⚪")
        
        # 计算完备率
        files_exist = len(files_backed) + len(files_missing)
        dirs_exist = len(dirs_backed) + len(dirs_missing)
        
        file_rate = (len(files_backed) / files_exist * 100) if files_exist > 0 else 100
        dir_rate = (len(dirs_backed) / dirs_exist * 100) if dirs_exist > 0 else 100
        
        print(f"\n📈 完备率:")
        print(f"  - 文件完备率: {file_rate:.1f}%")
        print(f"  - 目录完备率: {dir_rate:.1f}%")
        
        # 列出问题
        if files_missing:
            print(f"\n❌ 未备份的文件:")
            for f in files_missing:
                print(f"  - {f}")
        
        if dirs_missing:
            print(f"\n❌ 未备份的目录:")
            for d in dirs_missing:
                print(f"  - {d}/")
        
        # 检查备份信息文件
        info_path = backup_path / "backup_info.json"
        if info_path.exists():
            print(f"\n📄 备份信息文件存在: ✅")
            info = json.loads(info_path.read_text())
            print(f"  - 名称: {info.get('name')}")
            print(f"  - 类型: {info.get('type')}")
            print(f"  - 文件数: {info.get('files_count')}")
            print(f"  - 大小: {info.get('size_bytes', 0) / 1024:.1f} KB")
        else:
            print(f"\n📄 备份信息文件: ❌ 不存在")
        
        print()
        
        # 最终判定
        print("=" * 60)
        if files_missing or dirs_missing:
            print("❌ 测试失败: 存在未备份的关键文件/目录")
            print("=" * 60)
            return False
        else:
            print("✅ 测试通过: 所有关键文件和目录均已备份")
            print("=" * 60)
            return True
            
    except Exception as e:
        print(f"❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # 清理测试备份
        test_backup_path = BACKUP_DIR / test_backup_name
        if test_backup_path.exists():
            print(f"\n🧹 清理测试备份: {test_backup_name}")
            shutil.rmtree(test_backup_path)


def test_api_trigger():
    """模拟前端 API 触发备份"""
    print("\n" + "=" * 60)
    print("🔗 模拟前端 API 触发备份")
    print("=" * 60)
    
    import requests
    
    # 测试本地 API
    api_url = "http://localhost:8765"
    
    try:
        # 创建备份
        response = requests.post(f"{api_url}/api/backup/create", json={
            "type": "full",
            "description": "测试备份完备性"
        }, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ API 调用成功")
            print(f"  - 备份名称: {result.get('backup_name')}")
            print(f"  - 备份路径: {result.get('backup_path')}")
            print(f"  - 文件大小: {result.get('size_bytes', 0) / 1024:.1f} KB")
        else:
            print(f"❌ API 调用失败: HTTP {response.status_code}")
            
    except requests.exceptions.ConnectionError:
        print("⚠️ 无法连接到 API 服务器 (可能未启动)")
        print("   使用本地备份系统测试...")
        return test_backup_completeness()
    except Exception as e:
        print(f"❌ API 测试异常: {e}")
        return test_backup_completeness()


if __name__ == "__main__":
    print("\n" + "🚀 " * 20)
    print("立即执行任务: backup_daily (ID: test_backup_completeness)")
    print("🚀 " * 20 + "\n")
    
    # 运行测试
    success = test_backup_completeness()
    
    # 如果本地测试失败，尝试 API 测试
    if not success:
        print("\n尝试 API 测试...")
        success = test_api_trigger()
    
    # 返回退出码
    sys.exit(0 if success else 1)
