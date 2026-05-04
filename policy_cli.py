#!/usr/bin/env python3
"""
Nanobot CLI 策略管理工具

用法:
    nanobot policy list                    # 列出所有策略
    nanobot policy add <file.json>         # 从文件添加策略
    nanobot policy remove <id>             # 删除策略
    nanobot policy enable <id>             # 启用策略
    nanobot policy disable <id>            # 禁用策略
    nanobot policy show <id>               # 显示策略详情
    nanobot policy export <file.json>      # 导出所有策略
    nanobot policy audit [hours]           # 查看审计摘要
    nanobot policy test <tool> <params>    # 测试策略匹配

版本: 1.0.0
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any, Optional

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from web_ui.permission_policy import (
    PermissionPolicyManager, PermissionPolicy, PolicyDecision
)
from web_ui.permission_manager import PermissionType
from web_ui.audit_logger import get_audit_logger, AuditEventType


# 默认策略文件路径
DEFAULT_POLICY_PATH = Path.home() / ".nanobot" / "policies.json"


def get_policy_manager() -> PermissionPolicyManager:
    """获取策略管理器"""
    return PermissionPolicyManager(storage_path=DEFAULT_POLICY_PATH)


def cmd_list(args):
    """列出所有策略"""
    manager = get_policy_manager()
    policies = manager.list_policies()
    
    if not policies:
        print("📋 暂无策略")
        return 0
    
    print(f"📋 共 {len(policies)} 条策略:\n")
    print(f"{'ID':<25} {'名称':<30} {'工具':<20} {'决策':<10} {'优先级':<8} {'状态'}")
    print("-" * 100)
    
    for p in policies:
        status = "✅ 启用" if p.enabled else "❌ 禁用"
        decision = p.decision.value if hasattr(p.decision, 'value') else str(p.decision)
        print(f"{p.id:<25} {p.name:<30} {p.tool_pattern:<20} {decision:<10} {p.priority:<8} {status}")
    
    return 0


def cmd_add(args):
    """从文件添加策略"""
    file_path = Path(args.file)
    
    if not file_path.exists():
        print(f"❌ 文件不存在: {file_path}")
        return 1
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ JSON解析失败: {e}")
        return 1
    
    manager = get_policy_manager()
    
    # 支持单个策略或策略数组
    if isinstance(data, list):
        policies = data
    else:
        policies = [data]
    
    added = 0
    for p_data in policies:
        try:
            policy = PermissionPolicy(
                id=p_data.get('id', f"policy_{int(time.time() * 1000)}"),
                name=p_data.get('name', '未命名策略'),
                tool_pattern=p_data.get('tool_pattern', '*'),
                param_patterns=p_data.get('param_patterns', {}),
                decision=PolicyDecision(p_data.get('decision', 'ask')),
                priority=p_data.get('priority', 100),
                description=p_data.get('description', ''),
                enabled=p_data.get('enabled', True),
                expires_at=p_data.get('expires_at'),
            )
            manager.add_policy(policy)
            print(f"✅ 添加策略: {policy.id} - {policy.name}")
            added += 1
        except Exception as e:
            print(f"❌ 添加失败: {e}")
    
    print(f"\n成功添加 {added} 条策略")
    return 0


def cmd_remove(args):
    """删除策略"""
    manager = get_policy_manager()
    
    if manager.remove_policy(args.id):
        print(f"✅ 已删除策略: {args.id}")
        return 0
    else:
        print(f"❌ 策略不存在: {args.id}")
        return 1


def cmd_enable(args):
    """启用策略"""
    manager = get_policy_manager()
    
    if manager.enable_policy(args.id):
        print(f"✅ 已启用策略: {args.id}")
        return 0
    else:
        print(f"❌ 策略不存在: {args.id}")
        return 1


def cmd_disable(args):
    """禁用策略"""
    manager = get_policy_manager()
    
    if manager.disable_policy(args.id):
        print(f"✅ 已禁用策略: {args.id}")
        return 0
    else:
        print(f"❌ 策略不存在: {args.id}")
        return 1


def cmd_show(args):
    """显示策略详情"""
    manager = get_policy_manager()
    policy = manager.get_policy(args.id)
    
    if not policy:
        print(f"❌ 策略不存在: {args.id}")
        return 1
    
    print(f"\n📋 策略详情: {policy.id}\n")
    print(f"  名称: {policy.name}")
    print(f"  工具模式: {policy.tool_pattern}")
    print(f"  参数模式: {json.dumps(policy.param_patterns, ensure_ascii=False, indent=4)}")
    print(f"  决策: {policy.decision.value}")
    print(f"  优先级: {policy.priority}")
    print(f"  状态: {'启用' if policy.enabled else '禁用'}")
    print(f"  描述: {policy.description}")
    if policy.expires_at:
        from datetime import datetime
        print(f"  过期时间: {datetime.fromtimestamp(policy.expires_at)}")
    print()
    
    return 0


def cmd_export(args):
    """导出所有策略"""
    manager = get_policy_manager()
    policies = manager.list_policies()
    
    data = []
    for p in policies:
        data.append({
            'id': p.id,
            'name': p.name,
            'tool_pattern': p.tool_pattern,
            'param_patterns': p.param_patterns,
            'decision': p.decision.value,
            'priority': p.priority,
            'description': p.description,
            'enabled': p.enabled,
            'expires_at': p.expires_at,
        })
    
    file_path = Path(args.file)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump({
            'version': '1.0',
            'policies': data
        }, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 已导出 {len(policies)} 条策略到: {file_path}")
    return 0


def cmd_audit(args):
    """查看审计摘要"""
    import time as t
    hours = args.hours or 24
    
    audit = get_audit_logger()
    summary = audit.get_summary(hours=hours)
    
    print(f"\n📊 审计摘要 (最近 {hours} 小时)\n")
    print(f"  总事件数: {summary.get('total_events', 0)}")
    print(f"  权限请求: {summary.get('by_type', {}).get('permission_request', 0)}")
    print(f"  工具执行: {summary.get('by_type', {}).get('tool_success', 0)}")
    print(f"  沙箱执行: {summary.get('sandbox_count', 0)}")
    print(f"  安全违规: {summary.get('by_type', {}).get('security_violation', 0)}")
    print(f"  审批拒绝: {summary.get('by_type', {}).get('permission_denied', 0)}")
    print(f"  审批超时: {summary.get('by_type', {}).get('permission_timeout', 0)}")
    
    # 高风险事件
    high_risk = audit.query_events(risk_level='high', limit=10)
    if high_risk:
        print(f"\n⚠️  高风险事件 ({len(high_risk)} 条):")
        for event in high_risk[:5]:
            print(f"  - {event.get('event_type')}: {event.get('tool_name', 'N/A')}")
    
    print()
    return 0


def cmd_test(args):
    """测试策略匹配"""
    manager = get_policy_manager()
    
    # 解析参数
    params = {}
    if args.params:
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError:
            # 简单格式: key=value,key2=value2
            for pair in args.params.split(','):
                if '=' in pair:
                    k, v = pair.split('=', 1)
                    params[k.strip()] = v.strip()
    
    decision = manager.get_decision(args.tool, params)
    
    print(f"\n🔍 策略匹配测试\n")
    print(f"  工具: {args.tool}")
    print(f"  参数: {json.dumps(params, ensure_ascii=False)}")
    print(f"  决策: {decision.value if hasattr(decision, 'value') else decision}")
    print()
    
    return 0


def cmd_interactive(args):
    """交互式添加策略"""
    print("\n📝 交互式添加策略\n")
    
    policy_id = input("策略ID (留空自动生成): ").strip() or f"policy_{int(time.time() * 1000)}"
    name = input("策略名称: ").strip() or "未命名策略"
    tool_pattern = input("工具模式 (如 shell_execute, file_*): ").strip() or "*"
    
    print("\n参数模式 (输入 key=value 格式，空行结束):")
    param_patterns = {}
    while True:
        line = input("  ").strip()
        if not line:
            break
        if '=' in line:
            k, v = line.split('=', 1)
            param_patterns[k.strip()] = v.strip()
    
    print("\n决策选项:")
    print("  1. allow - 允许")
    print("  2. deny - 拒绝")
    print("  3. sandbox - 沙箱执行")
    print("  4. ask - 询问用户")
    decision_choice = input("选择 (1-4): ").strip()
    decision_map = {'1': 'allow', '2': 'deny', '3': 'sandbox', '4': 'ask'}
    decision = decision_map.get(decision_choice, 'ask')
    
    priority = int(input("优先级 (数字越大越优先，默认100): ").strip() or "100")
    description = input("描述: ").strip()
    
    # 创建策略
    policy = PermissionPolicy(
        id=policy_id,
        name=name,
        tool_pattern=tool_pattern,
        param_patterns=param_patterns,
        decision=PolicyDecision(decision),
        priority=priority,
        description=description,
        enabled=True,
    )
    
    manager = get_policy_manager()
    manager.add_policy(policy)
    
    print(f"\n✅ 策略已添加: {policy_id}")
    return 0


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description='Nanobot 策略管理 CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  nanobot policy list
  nanobot policy add policy.json
  nanobot policy remove deny_rm_rf
  nanobot policy test shell_execute '{"command": "ls"}'
  nanobot policy audit 24
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='命令')
    
    # list
    subparsers.add_parser('list', help='列出所有策略')
    
    # add
    add_parser = subparsers.add_parser('add', help='从文件添加策略')
    add_parser.add_argument('file', help='JSON文件路径')
    
    # remove
    remove_parser = subparsers.add_parser('remove', help='删除策略')
    remove_parser.add_argument('id', help='策略ID')
    
    # enable
    enable_parser = subparsers.add_parser('enable', help='启用策略')
    enable_parser.add_argument('id', help='策略ID')
    
    # disable
    disable_parser = subparsers.add_parser('disable', help='禁用策略')
    disable_parser.add_argument('id', help='策略ID')
    
    # show
    show_parser = subparsers.add_parser('show', help='显示策略详情')
    show_parser.add_argument('id', help='策略ID')
    
    # export
    export_parser = subparsers.add_parser('export', help='导出所有策略')
    export_parser.add_argument('file', help='导出文件路径')
    
    # audit
    audit_parser = subparsers.add_parser('audit', help='查看审计摘要')
    audit_parser.add_argument('hours', nargs='?', type=int, default=24, help='时间范围(小时)')
    
    # test
    test_parser = subparsers.add_parser('test', help='测试策略匹配')
    test_parser.add_argument('tool', help='工具名称')
    test_parser.add_argument('params', nargs='?', help='参数JSON或key=value格式')
    
    # interactive
    subparsers.add_parser('interactive', help='交互式添加策略')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 0
    
    # 命令映射
    commands = {
        'list': cmd_list,
        'add': cmd_add,
        'remove': cmd_remove,
        'enable': cmd_enable,
        'disable': cmd_disable,
        'show': cmd_show,
        'export': cmd_export,
        'audit': cmd_audit,
        'test': cmd_test,
        'interactive': cmd_interactive,
    }
    
    handler = commands.get(args.command)
    if handler:
        import time
        return handler(args)
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
