"""
P15 — Repair wizard business logic.

Extracted from server_final.py endpoints:
  /api/fix/scan
  /api/fix/rules
  /api/fix/check/{rule_id}
  /api/fix/apply/{rule_id}
  /api/fix/apply-all

The FixEngine instance is passed in as a parameter (dependency injection)
so these functions stay pure / testable without sys.path manipulation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def classify_scan_results(issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyse a list of scanned issues and return a classified summary.

    Parameters
    ----------
    issues : list[dict]
        Raw issue dicts from ``engine.scan_all()``.

    Returns
    -------
    dict
        Keys: *success*, *status_level*, *status_name*, *status_color*,
        *total_issues*, *categories*, *issues*, *summary*.
    """
    # 分类统计
    categories: Dict[str, List[Dict[str, Any]]] = {}
    for issue in issues:
        cat = issue.get("category", "unknown")
        categories.setdefault(cat, []).append(issue)

    # 计算各等级问题数量
    critical_count = sum(1 for i in issues if i.get("severity") == "critical")
    high_count = sum(1 for i in issues if i.get("severity") == "high")
    medium_count = sum(1 for i in issues if i.get("severity") == "medium")
    low_count = sum(1 for i in issues if i.get("severity") == "low")

    # 确定整体状态等级
    if critical_count > 0:
        status_level, status_name, status_color = "CRITICAL", "系统严重故障", "red"
    elif high_count > 0:
        status_level, status_name, status_color = "ERROR", "系统错误", "orange"
    elif medium_count > 0:
        status_level, status_name, status_color = "WARNING", "系统警告", "yellow"
    else:
        status_level, status_name, status_color = "INFO", "系统状态", "green"

    return {
        "success": True,
        "status_level": status_level,
        "status_name": status_name,
        "status_color": status_color,
        "total_issues": len(issues),
        "categories": categories,
        "issues": issues,
        "summary": {
            "critical": critical_count,
            "high": high_count,
            "medium": medium_count,
            "low": low_count,
        },
    }


def list_rules(engine: Any) -> Dict[str, Any]:
    """Return serialised list of available fix rules.

    Parameters
    ----------
    engine : FixEngine
        The loaded FixEngine instance.
    """
    rules: List[Dict[str, Any]] = []
    for rule_id, rule in engine.rules.items():
        rules.append({
            "id": rule.id,
            "name": rule.name,
            "description": rule.description,
            "category": rule.category,
            "severity": rule.severity,
            "auto_fixable": rule.auto_fixable,
            "requires_restart": rule.requires_restart,
        })
    return {"success": True, "rules": rules}


def check_issue(engine: Any, rule_id: str) -> Dict[str, Any]:
    """Dry-run a single fix rule and return the result."""
    return engine.fix_issue(rule_id, dry_run=True)


def apply_issue(engine: Any, rule_id: str) -> Dict[str, Any]:
    """Apply a single fix rule and return the result."""
    return engine.fix_issue(rule_id)


def apply_all_issues(engine: Any, auto_only: bool = True) -> Dict[str, Any]:
    """Apply all discovered fixes and return a summary."""
    results = engine.fix_all(auto_only=auto_only)
    success_count = sum(1 for r in results if r.get("success"))
    return {
        "success": True,
        "results": results,
        "summary": {
            "total": len(results),
            "success": success_count,
            "failed": len(results) - success_count,
        },
    }
