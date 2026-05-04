"""Unit tests for services/repair_service.py"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.repair_service import (
    classify_scan_results,
    list_rules,
    check_issue,
    apply_issue,
    apply_all_issues,
)


class TestClassifyScanResults(unittest.TestCase):

    def test_empty_issues(self):
        result = classify_scan_results([])
        self.assertTrue(result["success"])
        self.assertEqual(result["total_issues"], 0)
        self.assertEqual(result["status_level"], "INFO")
        self.assertEqual(result["status_color"], "green")

    def test_critical_issues(self):
        issues = [{"severity": "critical", "category": "system"}]
        result = classify_scan_results(issues)
        self.assertEqual(result["status_level"], "CRITICAL")
        self.assertEqual(result["status_color"], "red")
        self.assertEqual(result["summary"]["critical"], 1)

    def test_high_issues(self):
        issues = [{"severity": "high", "category": "config"}]
        result = classify_scan_results(issues)
        self.assertEqual(result["status_level"], "ERROR")
        self.assertEqual(result["status_color"], "orange")

    def test_medium_issues(self):
        issues = [{"severity": "medium", "category": "perf"}]
        result = classify_scan_results(issues)
        self.assertEqual(result["status_level"], "WARNING")
        self.assertEqual(result["status_color"], "yellow")

    def test_low_issues(self):
        issues = [{"severity": "low", "category": "misc"}]
        result = classify_scan_results(issues)
        self.assertEqual(result["status_level"], "INFO")
        self.assertEqual(result["status_color"], "green")

    def test_categories_grouped(self):
        issues = [
            {"severity": "low", "category": "a"},
            {"severity": "low", "category": "a"},
            {"severity": "low", "category": "b"},
        ]
        result = classify_scan_results(issues)
        self.assertEqual(len(result["categories"]["a"]), 2)
        self.assertEqual(len(result["categories"]["b"]), 1)

    def test_mixed_severities(self):
        issues = [
            {"severity": "critical", "category": "x"},
            {"severity": "high", "category": "x"},
            {"severity": "medium", "category": "x"},
            {"severity": "low", "category": "x"},
        ]
        result = classify_scan_results(issues)
        self.assertEqual(result["status_level"], "CRITICAL")
        self.assertEqual(result["summary"]["critical"], 1)
        self.assertEqual(result["summary"]["high"], 1)
        self.assertEqual(result["summary"]["medium"], 1)
        self.assertEqual(result["summary"]["low"], 1)


class TestListRules(unittest.TestCase):

    def test_list_rules(self):
        rule = MagicMock()
        rule.id = "r1"
        rule.name = "Rule One"
        rule.description = "desc"
        rule.category = "cat"
        rule.severity = "high"
        rule.auto_fixable = True
        rule.requires_restart = False
        engine = MagicMock()
        engine.rules = {"r1": rule}
        result = list_rules(engine)
        self.assertTrue(result["success"])
        self.assertEqual(len(result["rules"]), 1)
        self.assertEqual(result["rules"][0]["id"], "r1")


class TestCheckAndApply(unittest.TestCase):

    def test_check_issue(self):
        engine = MagicMock()
        engine.fix_issue.return_value = {"checked": True}
        result = check_issue(engine, "rule1")
        engine.fix_issue.assert_called_with("rule1", dry_run=True)
        self.assertEqual(result["checked"], True)

    def test_apply_issue(self):
        engine = MagicMock()
        engine.fix_issue.return_value = {"applied": True}
        result = apply_issue(engine, "rule1")
        engine.fix_issue.assert_called_with("rule1")
        self.assertEqual(result["applied"], True)


class TestApplyAllIssues(unittest.TestCase):

    def test_apply_all(self):
        engine = MagicMock()
        engine.fix_all.return_value = [
            {"success": True}, {"success": False}, {"success": True}
        ]
        result = apply_all_issues(engine)
        self.assertTrue(result["success"])
        self.assertEqual(result["summary"]["total"], 3)
        self.assertEqual(result["summary"]["success"], 2)
        self.assertEqual(result["summary"]["failed"], 1)

    def test_apply_all_auto_only_flag(self):
        engine = MagicMock()
        engine.fix_all.return_value = []
        apply_all_issues(engine, auto_only=False)
        engine.fix_all.assert_called_with(auto_only=False)


if __name__ == "__main__":
    unittest.main()
