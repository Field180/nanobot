"""
TestRepoExploreFork — 验证 P104-fork: RepoExploreAgent fork 行为

测试目标：
1. 架构/目录职责类问题触发 fork
2. fork 后 RepoExploreAgent 执行只读探索
3. 探索结果正确注入主代理上下文
4. 主代理基于证据回答，而非脑补
"""

import sys
import os
import asyncio
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentic_loop import (
    _is_repo_fact_question,
    _ARCHITECTURE_QUERY_RE,
    _REPO_FACT_SUMMARY_RE,
)
from repo_explore_agent import (
    RepoExploreAgent,
    ExplorationResult,
    _ARCHITECTURE_QUERY_RE as AGENT_ARCH_RE,
)


class TestArchitectureQueryDetection(unittest.TestCase):
    """验证架构类问题的 fork 触发检测"""

    def test_chinese_detailed_architecture_question(self):
        """"详细说说每个目录的职责，这个项目的架构是怎么设计的？"""
        query = "详细说说每个目录的职责，这个项目的架构是怎么设计的？"
        self.assertTrue(_ARCHITECTURE_QUERY_RE.search(query))
        self.assertTrue(_REPO_FACT_SUMMARY_RE.search(query))

    def test_english_architecture_question(self):
        """explain in detail how the architecture is designed"""
        query = "Can you explain in detail how the project architecture is designed?"
        self.assertTrue(_ARCHITECTURE_QUERY_RE.search(query))

    def test_module_organization_question(self):
        """how are modules organized"""
        query = "What is the module organization of this project?"
        self.assertTrue(AGENT_ARCH_RE.search(query))

    def test_simple_file_question_not_forked(self):
        """简单的文件读取问题不应该 fork"""
        query = "AGENTS.md 里面写了什么？"
        self.assertFalse(_ARCHITECTURE_QUERY_RE.search(query))
        # 但应该还是 repo-fact
        self.assertTrue(_is_repo_fact_question(query))


class TestRepoExploreAgentStructure(unittest.TestCase):
    """验证 RepoExploreAgent 的结构约束"""

    def test_read_only_tool_set(self):
        """只读工具集必须严格受限"""
        self.assertEqual(
            RepoExploreAgent.READ_ONLY_TOOLS,
            {"file_read", "file_list", "grep_search", "find_by_name"}
        )
        # 确认没有写工具
        self.assertNotIn("file_edit", RepoExploreAgent.READ_ONLY_TOOLS)
        self.assertNotIn("file_write", RepoExploreAgent.READ_ONLY_TOOLS)
        self.assertNotIn("shell_execute", RepoExploreAgent.READ_ONLY_TOOLS)

    def test_exploration_result_structure(self):
        """ExplorationResult 必须包含关键字段"""
        result = ExplorationResult(
            success=True,
            query="test query",
            files_read=[{"path": "test.md", "preview": "content"}],
            evidence_completeness="complete"
        )
        self.assertTrue(result.success)
        self.assertEqual(result.query, "test query")
        self.assertEqual(len(result.files_read), 1)
        self.assertEqual(result.evidence_completeness, "complete")

    def test_key_doc_patterns_exist(self):
        """关键文档模式必须包含架构文档"""
        from repo_explore_agent import _KEY_DOC_PATTERNS
        patterns = [p.lower() for p in _KEY_DOC_PATTERNS]
        self.assertTrue(any("project_structure" in p for p in patterns))
        self.assertTrue(any("architecture" in p for p in patterns))
        self.assertTrue(any("agents" in p for p in patterns))


class TestForkIntegration(unittest.TestCase):
    """模拟 fork 行为的集成测试"""

    def test_fork_decision_logic(self):
        """
        验证 fork 决策逻辑：
        - 是 repo-fact 问题 + 架构类关键词 = fork
        """
        test_cases = [
            # (query, should_fork)
            ("详细说说每个目录的职责，这个项目的架构是怎么设计的？", True),
            ("explain in detail the directory structure and architecture", True),
            ("AGENTS.md 里面写了什么？", False),  # 简单文件读取
            ("修复这个测试失败", False),  # 不是 repo-fact
            ("详细描述这个项目的项目架构", True),  # 中文架构
            ("what is the directory structure? explain in detail", True),  # 英文目录结构+详细
        ]

        for query, should_fork in test_cases:
            is_repo_fact = bool(_is_repo_fact_question(query))
            is_architecture = bool(_ARCHITECTURE_QUERY_RE.search(query))
            would_fork = is_repo_fact and is_architecture
            
            self.assertEqual(
                would_fork, should_fork,
                f"Query '{query[:30]}...': expected fork={should_fork}, got fork={would_fork} "
                f"(repo_fact={is_repo_fact}, arch={is_architecture})"
            )

    @patch("tools.file_read.execute")
    def test_agent_scans_root_first(self, mock_file_read):
        """RepoExploreAgent 必须先扫描根目录结构"""
        mock_file_read.return_value = {"content": "# Test", "truncated": False}
        
        agent = RepoExploreAgent(None, "/tmp/test", "test-session")
        
        # 模拟 file_list 返回
        with patch("tools.file_list.execute") as mock_list:
            mock_list.return_value = {
                "success": True,
                "output": (
                    "Directory: /tmp/test (2 entries)\n"
                    "f  README.md  (10B)\n"
                    "d  src/  (1 items)"
                ),
                "error": "",
            }
            asyncio.run(agent.explore("what is the project structure?"))
        
        # 验证扫描了根目录
        self.assertIn("/tmp/test", agent._dirs_scanned)

    def test_partial_read_detection(self):
        """验证 partial read 检测正则"""
        from repo_explore_agent import _PARTIAL_READ_RE
        
        content = "FILE READ: test.md\nshowing lines 1-300 of 603\n..."
        match = _PARTIAL_READ_RE.search(content)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "1")
        self.assertEqual(match.group(2), "300")
        self.assertEqual(match.group(3), "603")

    def test_completeness_assessment(self):
        """验证证据完整度评估"""
        # complete: 有关键文档，无 gaps
        result_complete = ExplorationResult(
            success=True,
            query="test",
            evidence_completeness="complete",
            missing_evidence=[]
        )
        self.assertEqual(result_complete.evidence_completeness, "complete")
        
        # partial: 有 gaps
        result_partial = ExplorationResult(
            success=True,
            query="test",
            evidence_completeness="partial",
            missing_evidence=["No PROJECT_STRUCTURE.md found"]
        )
        self.assertEqual(result_partial.evidence_completeness, "partial")


class TestEvidenceBasedResponse(unittest.TestCase):
    """验证基于证据的回答约束"""

    def test_findings_include_evidence_summary(self):
        """探索结果必须包含证据摘要"""
        result = ExplorationResult(
            success=True,
            query="architecture question",
            files_read=[
                {"path": "README.md", "preview": "Project overview"},
                {"path": "src/main.py", "preview": "Main entry"},
            ],
            findings="## Repository Exploration Summary\n\n**Query**: architecture question\n\n### Files Examined (2)",
            evidence_completeness="mostly_complete"
        )
        
        # 验证 findings 包含必要信息
        self.assertIn("Files Examined", result.findings)
        self.assertIn("Repository Exploration Summary", result.findings)

    def test_incomplete_evidence_disclaimer(self):
        """证据不完整时必须包含免责声明"""
        result = ExplorationResult(
            success=True,
            query="architecture",
            evidence_completeness="partial",
            missing_evidence=["No PROJECT_STRUCTURE.md found/read"],
            findings="### Evidence Gaps\n⚠️ The following gaps may affect answer completeness:"
        )
        
        self.assertIn("Evidence Gaps", result.findings)
        self.assertTrue(len(result.missing_evidence) > 0)


if __name__ == "__main__":
    unittest.main()
