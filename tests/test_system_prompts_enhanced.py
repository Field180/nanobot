"""Tests for P16 system prompt enhancements — structural assertions.

Verifies that key prompt fragments appear in the assembled system prompt,
that MCP resource hints are conditional, and that no duplicate/conflicting
rules exist. Addresses audit finding: "no test verifies prompt content".
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from system_prompts import (
    _get_static_system_prompt,
    build_system_prompt,
    _SYSTEM_PROMPT_TASKS,
    _SYSTEM_PROMPT_SELF_KNOWLEDGE,
)
from tools import build_tool_guidance


class TestEditWorkflowPresence(unittest.TestCase):
    """Audit fix: verify EDIT WORKFLOW checklist is in the static prompt."""

    def test_edit_workflow_in_static_prompt(self):
        prompt = _get_static_system_prompt()
        self.assertIn("EDIT WORKFLOW", prompt)

    def test_diagnose_action_in_edit_workflow(self):
        prompt = _get_static_system_prompt()
        self.assertIn("code_intel action='diagnose'", prompt)

    def test_file_read_verify_after_edit(self):
        """Post-edit verification step present."""
        self.assertIn("After editing: file_read to verify", _SYSTEM_PROMPT_TASKS)

    def test_grep_search_for_bulk_renames(self):
        self.assertIn("grep_search all references first", _SYSTEM_PROMPT_TASKS)

    def test_edit_workflow_appears_exactly_once(self):
        """Audit finding: no duplicate rules."""
        prompt = _get_static_system_prompt()
        count = prompt.count("EDIT WORKFLOW")
        self.assertEqual(count, 1, f"EDIT WORKFLOW appears {count} times, expected 1")


class TestSelfKnowledgeDiagnose(unittest.TestCase):
    """Audit fix: code_intel self-knowledge mentions diagnose."""

    def test_diagnose_quality_in_self_knowledge(self):
        self.assertIn("diagnose quality", _SYSTEM_PROMPT_SELF_KNOWLEDGE)

    def test_code_intel_listed_in_self_knowledge(self):
        self.assertIn("code_intel", _SYSTEM_PROMPT_SELF_KNOWLEDGE)


class TestReadBeforeEditNoConflict(unittest.TestCase):
    """Audit finding: check overlap between general read-before-edit and EDIT WORKFLOW."""

    def test_general_read_rule_present(self):
        self.assertIn("Read and understand existing code", _SYSTEM_PROMPT_TASKS)

    def test_edit_workflow_complements_general(self):
        """EDIT WORKFLOW adds diagnose step, not a duplicate of the general rule."""
        prompt = _SYSTEM_PROMPT_TASKS
        # General rule says "read before modify" — broad
        self.assertIn("Read and understand existing code before suggesting modifications", prompt)
        # EDIT WORKFLOW says "file_read + diagnose" — specific
        self.assertIn("code_intel action='diagnose'", prompt)
        # They are complementary: general is about understanding, specific is about tool workflow


class TestMCPResourceHintConditional(unittest.TestCase):
    """Audit fix: MCP resource hint only appears when resources are available."""

    def test_no_mcp_no_resource_hint(self):
        """Without MCP connections, resource hint must not appear."""
        guidance = build_tool_guidance()
        # No MCP servers connected by default in test env
        self.assertNotIn("MCP resources available", guidance)

    def test_no_mcp_tools_no_mcp_section(self):
        """Without MCP tools, entire MCP section absent."""
        guidance = build_tool_guidance()
        self.assertNotIn("mcp__", guidance)

    def test_mcp_resource_hint_with_resources(self):
        """When MCP resources exist, hint should include concrete names."""
        mock_mgr = MagicMock()
        mock_mgr.get_all_tool_definitions.return_value = [
            {"function": {"name": "mcp__db__query", "description": "Query database"}}
        ]
        mock_mgr.get_all_resources.return_value = [
            {"server": "db", "name": "schema", "uri": "db://schema", "description": "DB schema", "mimeType": "text/plain"},
            {"server": "api", "name": "openapi", "uri": "api://spec", "description": "API spec", "mimeType": "application/json"},
        ]

        with patch("tools.get_mcp_manager", return_value=mock_mgr):
            guidance = build_tool_guidance()

        # Should mention concrete resource names instead of generic advice
        self.assertIn("schema", guidance)
        self.assertIn("openapi", guidance)


class TestToolGuidanceDiagnosePresent(unittest.TestCase):
    """Verify code_intel guidance mentions diagnose."""

    def test_code_intel_guidance_has_diagnose(self):
        guidance = build_tool_guidance()
        self.assertIn("diagnose", guidance)


class TestBuildSystemPromptBasics(unittest.TestCase):
    """Ensure build_system_prompt backward compat is intact."""

    def test_returns_string(self):
        prompt = build_system_prompt()
        self.assertIsInstance(prompt, str)

    def test_contains_identity(self):
        prompt = build_system_prompt()
        self.assertIn("Nanobot", prompt)

    def test_contains_tool_guidance(self):
        prompt = build_system_prompt()
        self.assertIn("Using your tools", prompt)

    def test_language_injection(self):
        prompt = build_system_prompt(language="zh")
        self.assertIn("Chinese", prompt)

    def test_custom_instructions(self):
        prompt = build_system_prompt(custom_instructions="Always use pytest")
        self.assertIn("Always use pytest", prompt)


class TestPromptSizeGuard(unittest.TestCase):
    """Audit finding: track prompt size for regression detection."""

    def test_static_prompt_under_30k_chars(self):
        prompt = _get_static_system_prompt()
        self.assertLess(len(prompt), 30000,
                        f"Static prompt is {len(prompt)} chars — exceeds 30K guard")

    def test_static_prompt_above_20k_chars(self):
        """Sanity: prompt should have reasonable substance."""
        prompt = _get_static_system_prompt()
        self.assertGreater(len(prompt), 20000,
                           f"Static prompt is {len(prompt)} chars — suspiciously small")


if __name__ == "__main__":
    unittest.main()
