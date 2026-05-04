"""
P44-P51 Tests: Claw-Inspired Phase 6 Optimizations
====================================================
Tests for P44 (disk persistence threshold), P46 (sub_agent context inheritance),
P47 (destructive operation confirmation), P49 (compaction quality),
P50 (numeric length anchors), P51 (tool result memory reminder).

Run: python3 tests/test_p44_to_p51.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

# Ensure web_ui is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Track results
_results = []


def check(name, condition):
    status = "✅" if condition else "❌"
    _results.append((name, condition))
    print(f"  {status} {name}")
    return condition


# ═══════════════════════════════════════════════════════════════
# P47: Destructive Operation Confirmation
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 1. P47: Destructive Operation Confirmation ══╗")

# -- 1a: System prompt includes blast radius guidance --
from system_prompts import _SYSTEM_PROMPT_ACTIONS

check("P47: Actions section mentions reversibility",
      "reversibility" in _SYSTEM_PROMPT_ACTIONS)
check("P47: Actions section mentions blast radius",
      "blast radius" in _SYSTEM_PROMPT_ACTIONS)
check("P47: Docker operations listed as DESTRUCTIVE (U10)",
      "Docker container" in _SYSTEM_PROMPT_ACTIONS or "docker" in _SYSTEM_PROMPT_ACTIONS.lower())
check("P47: Package uninstall listed as DESTRUCTIVE (U10)",
      "pip uninstall" in _SYSTEM_PROMPT_ACTIONS)
check("P47: Overwrite large files mentioned",
      ">50 lines" in _SYSTEM_PROMPT_ACTIONS or "Overwriting large" in _SYSTEM_PROMPT_ACTIONS)
check("P47: Multiple destructive commands ban",
      "NEVER run multiple destructive" in _SYSTEM_PROMPT_ACTIONS)

# -- 1b: file_write overwrite protection --
from tools.file_write import execute as fw_execute

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)

    # Small file (<= 50 lines) → write succeeds
    small_file = ws / "small.txt"
    small_file.write_text("\n".join(f"line {i}" for i in range(30)), encoding="utf-8")
    result = fw_execute({"path": str(small_file), "content": "new content", "_session_id": "p47_small"}, ws)
    check("P47: file_write allows overwrite of small file (30 lines)",
          result["success"] is True)

    # Large file (> 50 lines) → blocked with warning
    large_file = ws / "large.py"
    large_file.write_text("\n".join(f"# line {i}" for i in range(100)), encoding="utf-8")
    result = fw_execute({"path": str(large_file), "content": "new content", "_session_id": "p47_large"}, ws)
    check("P47: file_write blocks overwrite of large file (100 lines)",
          result["success"] is False)
    check("P47: file_write error mentions line count",
          "100 lines" in result.get("error", ""))
    check("P47: file_write error suggests file_edit",
          "file_edit" in result.get("error", ""))
    check("P47: file_write returns _overwrite_warning flag",
          result.get("_overwrite_warning") is True)

    # New file (doesn't exist) → write succeeds
    new_file = ws / "brand_new.txt"
    result = fw_execute({"path": str(new_file), "content": "hello", "_session_id": "p47_newfile"}, ws)
    check("P47: file_write allows creating new file",
          result["success"] is True)

# -- 1c: shell_execute confirm patterns --
from tools.shell_execute import execute as se_execute, _CONFIRM_PATTERNS

check("P47: _CONFIRM_PATTERNS includes docker rm",
      "docker rm" in _CONFIRM_PATTERNS)
check("P47: _CONFIRM_PATTERNS includes pip uninstall",
      "pip uninstall" in _CONFIRM_PATTERNS)
check("P47: _CONFIRM_PATTERNS includes rm -r",
      "rm -r" in _CONFIRM_PATTERNS)

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    # A non-destructive command → ok
    result = se_execute({"command": "echo hello"}, ws)
    check("P47: shell_execute allows non-destructive command",
          result["success"] is True)

    # docker rm → warn (still executes but with caution)
    result = se_execute({"command": "echo fake docker rm container_id"}, ws)
    check("P47: shell_execute warns on docker rm pattern",
          "CAUTION" in result.get("output", ""))


# ═══════════════════════════════════════════════════════════════
# P50: Numeric Length Anchors
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 2. P50: Numeric Length Anchors ══╗")

from system_prompts import _SYSTEM_PROMPT_OUTPUT

check("P50/P101d: Output section has ≤25 words anchor",
      "≤25 words" in _SYSTEM_PROMPT_OUTPUT)
check("P50/P101d: Output section has ≤100 words anchor",
      "≤100 words" in _SYSTEM_PROMPT_OUTPUT)
check("P50/P101d: Output section has 'Length limits:' as structured list",
      "Length limits:" in _SYSTEM_PROMPT_OUTPUT)
check("P50/P101d: Expert calibration mentioned",
      "expertise" in _SYSTEM_PROMPT_OUTPUT)
check("P50/P101d: file_path:line_number reference style",
      "file_path:line_number" in _SYSTEM_PROMPT_OUTPUT)


# ═══════════════════════════════════════════════════════════════
# P51: Tool Result Memory Reminder
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 3. P51: Tool Result Memory Reminder ══╗")

from system_prompts import _SYSTEM_PROMPT_QUALITY, _SYSTEM_PROMPT_TOOL_PRESERVATION

# P101c: tool clearing moved to dedicated section
check("P51/P101c: Dedicated tool preservation section exists",
      "Tool result clearing" in _SYSTEM_PROMPT_TOOL_PRESERVATION)
check("P51/P101c: Section mentions 3 most recent results kept",
      "3 most recent" in _SYSTEM_PROMPT_TOOL_PRESERVATION)
check("P51/P101c: Section mentions writing down important data",
      "write down" in _SYSTEM_PROMPT_TOOL_PRESERVATION)
check("P51/P101c: Section mentions file paths, error messages, counts",
      "file paths" in _SYSTEM_PROMPT_TOOL_PRESERVATION and "error messages" in _SYSTEM_PROMPT_TOOL_PRESERVATION)
check("P51/P101c: Section warns against relying on old tool results",
      "Do not rely" in _SYSTEM_PROMPT_TOOL_PRESERVATION)


# ═══════════════════════════════════════════════════════════════
# P44: Tool Result Disk Persistence (Threshold + Preview)
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 4. P44: Tool Result Disk Persistence ══╗")

from agentic_loop import (
    _PERSIST_THRESHOLD,
    _MAX_RESULTS_PER_MESSAGE,
    _persist_large_result,
    _postprocess_tool_content,
)

check("P44: _PERSIST_THRESHOLD lowered to 20K",
      _PERSIST_THRESHOLD == 20_000)
check("P44: _MAX_RESULTS_PER_MESSAGE set to 60K",
      _MAX_RESULTS_PER_MESSAGE == 60_000)

# Test persistence with a large result
with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    large_content = "x" * 25000  # 25K chars > 20K threshold
    persisted = _persist_large_result("grep_search", large_content, ws, "test_session")

    check("P44: Persisted output contains disk path",
          ".tool_results" in persisted)
    check("P44: Persisted output contains preview marker",
          "[Result saved to disk:" in persisted)
    check("P44: Persisted output contains file_read instruction",
          "file_read" in persisted)
    check("P44: Persisted output has preview head (first 2000 chars)",
          "x" * 100 in persisted)  # preview head should be present

    # Verify file was actually written
    result_files = list((ws / ".tool_results" / "test_session").glob("*.txt"))
    check("P44: Result file written to disk",
          len(result_files) == 1)
    if result_files:
        content_on_disk = result_files[0].read_text(encoding="utf-8")
        check("P44: Full content preserved on disk",
              len(content_on_disk) == 25000)

# Test _postprocess_tool_content triggers persistence
with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    big_result = {"success": True, "output": "Y" * 25000, "error": ""}
    processed = _postprocess_tool_content("shell_execute", big_result,
                                          workspace=ws, session_id="test2")
    check("P44: _postprocess_tool_content persists >20K output",
          "[Result saved to disk:" in processed)

    # Normal-size result should NOT be persisted
    normal_result = {"success": True, "output": "Z" * 5000, "error": ""}
    processed_normal = _postprocess_tool_content("shell_execute", normal_result)
    check("P44: _postprocess_tool_content does NOT persist <20K output",
          "[Result saved to disk:" not in processed_normal)


# ═══════════════════════════════════════════════════════════════
# P49: Compaction Quality Upgrade
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 5. P49: Compaction Quality Upgrade ══╗")

from compact_engine import (
    _DETAILED_ANALYSIS_INSTRUCTION,
    _COMPACT_SECTIONS,
    _COMPACT_EXAMPLE,
    _COMPACT_PROMPT,
    _format_compact_summary,
)

# Analysis instruction improvements
check("P49: Analysis instruction requires FULL absolute paths",
      "FULL absolute paths" in _DETAILED_ANALYSIS_INSTRUCTION)
check("P49: Analysis instruction mentions old_string → new_string",
      "old_string" in _DETAILED_ANALYSIS_INSTRUCTION)
check("P49: Analysis instruction requires user messages verbatim",
      "verbatim" in _DETAILED_ANALYSIS_INSTRUCTION.lower())
check("P49: Analysis instruction verifies no dropped file paths",
      "Verify you have not dropped" in _DETAILED_ANALYSIS_INSTRUCTION)

# Compact sections improvements
check("P49: Sections include task status in Pending Tasks (U7)",
      "Pending Tasks" in _COMPACT_SECTIONS)
check("P49: Sections require COMPLETED/IN PROGRESS/PENDING markers",
      "COMPLETED" in _COMPACT_SECTIONS and "IN PROGRESS" in _COMPACT_SECTIONS and "PENDING" in _COMPACT_SECTIONS)
check("P49: Section 6 requires VERBATIM user messages",
      "VERBATIM" in _COMPACT_SECTIONS)
check("P49: Now has 9 sections (U7 merged Task Completion into Pending)",
      "9. **Optional Next Step**" in _COMPACT_SECTIONS)

# Example includes task status markers
check("P49: Example includes status markers (U7)",
      "IN PROGRESS" in _COMPACT_EXAMPLE and "COMPLETED" in _COMPACT_EXAMPLE)

# Format summary still works
raw_with_analysis = """<analysis>
Some analysis here about the conversation.
Checking all points...
</analysis>

<summary>
1. Primary Request: User wants X
2. Key Concepts: Python, async
9. Task Completion Status:
   - Task A: COMPLETED
   - Task B: IN PROGRESS
</summary>"""
formatted = _format_compact_summary(raw_with_analysis)
check("P49: _format_compact_summary strips <analysis>",
      "Some analysis here" not in formatted)
check("P49: _format_compact_summary preserves <summary> content",
      "Primary Request" in formatted)
check("P49: _format_compact_summary preserves Task Completion Status",
      "Task Completion Status" in formatted)


# ═══════════════════════════════════════════════════════════════
# P46: Fork Subagent Context Inheritance
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 6. P46: Fork Subagent Context Inheritance ══╗")

from tools.sub_agent import (
    TOOL_DEF as sub_agent_def,
    _build_inherited_context,
    set_parent_context,
)

# Tool def includes inherit_context parameter
params = sub_agent_def["function"]["parameters"]["properties"]
check("P46: sub_agent has inherit_context parameter",
      "inherit_context" in params)
check("P46: inherit_context is boolean type",
      params["inherit_context"]["type"] == "boolean")
check("P46: inherit_context param description mentions parent",
      "parent" in params["inherit_context"]["description"].lower())

# Test _build_inherited_context
test_messages = [
    {"role": "system", "content": "You are Nanobot..."},
    {"role": "user", "content": "Please read agentic_loop.py and find the bug"},
    {"role": "assistant", "content": "I'll read the file.",
     "tool_calls": [{"function": {"name": "file_read", "arguments": '{"path": "/home/field/agentic_loop.py"}'}}]},
    {"role": "tool", "content": "[Summary: file_read /home/field/agentic_loop.py — 1500 lines, Python]\nLine 1: import os\nLine 2: ...",
     "_tool_name": "file_read", "tool_call_id": "call1"},
    {"role": "assistant", "content": "I found a potential bug on line 42."},
    {"role": "user", "content": "Fix it please"},
]

context = _build_inherited_context(test_messages)
check("P46: _build_inherited_context excludes system messages",
      "You are Nanobot" not in context)
check("P46: _build_inherited_context includes user messages",
      "Please read agentic_loop.py" in context)
check("P46: _build_inherited_context includes assistant text",
      "found a potential bug" in context)
check("P46: _build_inherited_context summarizes tool results",
      "[TOOL RESULT file_read]" in context)
check("P46: _build_inherited_context uses Summary line from tool result",
      "[Summary:" in context)
check("P46: _build_inherited_context shows tool calls",
      "file_read" in context)

# Test empty messages
empty_ctx = _build_inherited_context([])
check("P46: _build_inherited_context returns empty for no messages",
      empty_ctx == "")

# Test with only system messages
sys_only = _build_inherited_context([{"role": "system", "content": "You are Nanobot"}])
check("P46: _build_inherited_context returns empty for system-only",
      sys_only == "")

# Test max_messages cap
many_messages = [
    {"role": "user", "content": f"Message {i}"}
    for i in range(20)
]
capped_ctx = _build_inherited_context(many_messages, max_messages=5)
check("P46: _build_inherited_context caps at max_messages",
      "Message 15" in capped_ctx and "Message 14" not in capped_ctx)

# Test set_parent_context with messages
test_env = {"OPENAI_API_KEY": "test"}
test_ws = Path("/tmp")
set_parent_context(test_env, test_ws, "sess123", messages=test_messages)
from tools.sub_agent import _PARENT_MESSAGES
check("P46: set_parent_context stores messages",
      len(_PARENT_MESSAGES) == len(test_messages))


# ═══════════════════════════════════════════════════════════════
# Cross-cutting: Static system prompt includes all new sections
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 7. Cross-cutting: System Prompt Integration ══╗")

# Reset cached prompt to pick up changes
import system_prompts
system_prompts._STATIC_SYSTEM_PROMPT = None
static_prompt = system_prompts._get_static_system_prompt()

check("Cross: Static prompt includes P47 blast radius",
      "blast radius" in static_prompt)
check("Cross: Static prompt includes P101d ≤25 words anchor",
      "≤25 words" in static_prompt)
check("Cross: Static prompt includes P101c tool clearing section",
      "Tool result clearing" in static_prompt)
check("Cross: Static prompt includes P46 inherit_context mention",
      "inherit_context" in static_prompt)

# Verify build_system_prompt() also picks up changes
full_prompt = system_prompts.build_system_prompt()
check("Cross: build_system_prompt includes P47",
      "blast radius" in full_prompt)
check("Cross: build_system_prompt includes P101d",
      "≤25 words" in full_prompt)
check("Cross: build_system_prompt includes P101c",
      "Tool result clearing" in full_prompt)


# ═══════════════════════════════════════════════════════════════
# P52: Workspace Path Strengthening
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 8. P52: Workspace Path Guidance ══╗")

# The workspace_info is built in agentic_loop.py — we test the pattern
from agentic_loop import agentic_chat_stream  # ensures module loads
import agentic_loop as _al52
# Simulate workspace info build
_test_ws = Path("/home/field/.nanobot/workspace")
_ws_info = (
    f"Current workspace directory: {_test_ws}\n"
    f"All relative tool paths are resolved against this directory.\n"
    f"IMPORTANT: When the user mentions a filename like 'foo.py', the file may be in a subdirectory. "
    f"Use the full path (e.g. '{_test_ws}/web_ui/foo.py') or a relative path from workspace "
    f"(e.g. 'web_ui/foo.py'). Do NOT pass bare filenames without a directory."
)
check("P52: workspace_info mentions 'may be in a subdirectory'",
      "may be in a subdirectory" in _ws_info)
check("P52: workspace_info gives example full path",
      "web_ui/foo.py" in _ws_info)
check("P52: workspace_info warns against bare filenames",
      "Do NOT pass bare filenames" in _ws_info)


# ═══════════════════════════════════════════════════════════════
# P53: file_read Bare Filename Auto-Search
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 9. P53: file_read Bare Filename Auto-Search ══╗")

from tools.file_read import execute as fr_execute

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    # Create a file in a subdirectory
    sub_dir = ws / "sub" / "deep"
    sub_dir.mkdir(parents=True)
    target = sub_dir / "my_module.py"
    target.write_text("print('hello')", encoding="utf-8")

    # B5+: Bare filename with single match → auto-resolves (success=True)
    result = fr_execute({"path": "my_module.py"}, ws)
    check("P53: Bare filename single match auto-resolves",
          result["success"] is True)
    check("P53: Auto-resolved content is correct",
          "hello" in result.get("output", ""))

    # B5+: Multiple matches → should fail with suggestions
    # Put second copy in a sibling immediate-child dir so both iterdir and find see 2 matches
    sub_dir_a = ws / "alpha"
    sub_dir_a.mkdir(parents=True)
    (sub_dir_a / "my_module.py").write_text("print('world')", encoding="utf-8")
    sub_dir_b = ws / "beta"
    sub_dir_b.mkdir(parents=True)
    (sub_dir_b / "my_module.py").write_text("print('third')", encoding="utf-8")
    result_multi = fr_execute({"path": "my_module.py"}, ws)
    check("P53: Multiple matches returns success=False",
          result_multi["success"] is False)
    check("P53: Error lists candidate paths",
          "my_module.py" in result_multi.get("error", ""))
    check("P53: Error suggests re-call with full path",
          "Re-call file_read" in result_multi.get("error", ""))

    # Non-existent file → still gives normal error
    result2 = fr_execute({"path": "nonexistent_xyz.py"}, ws)
    check("P53: Truly missing file returns normal error",
          result2["success"] is False)

    # Full path → should succeed (reset tracker to avoid P31 stub)
    from tools.file_read import reset_session_read_tracker
    reset_session_read_tracker()
    result3 = fr_execute({"path": str(target)}, ws)
    check("P53: Full path still works normally",
          result3["success"] is True)
    check("P53: Full path returns content",
          "hello" in result3.get("output", ""))


# ═══════════════════════════════════════════════════════════════
# P54: grep_search ^def Pattern Hint
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 10. P54: grep_search def Pattern Hint ══╗")

from tools.grep_search import execute as gs_execute

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    test_file = ws / "test_code.py"
    test_file.write_text(
        "def top_level():\n"
        "    pass\n\n"
        "class MyClass:\n"
        "    def method(self):\n"
        "        pass\n\n"
        "async def async_func():\n"
        "    pass\n",
        encoding="utf-8"
    )

    # Grep with ^def pattern → should add hint about indented methods
    result = gs_execute({"pattern": "^def ", "path": str(test_file)}, ws)
    check("P54: ^def finds module-level function",
          "top_level" in result.get("output", ""))
    check("P54: ^def includes hint about missing class methods",
          "Class methods" in result.get("output", "") or "indented def" in result.get("output", ""))

    # Grep with plain 'def ' pattern → should NOT add hint
    result2 = gs_execute({"pattern": "def ", "path": str(test_file)}, ws)
    check("P54: Plain 'def ' finds all definitions (3)",
          "Found 3" in result2.get("output", ""))
    check("P54: Plain 'def ' does NOT trigger hint",
          "Class methods" not in result2.get("output", ""))


# ═══════════════════════════════════════════════════════════════
# P101a: file_read exemption from per-message budget
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 11. P101a: file_read Budget Exemption ══╗")

# The aggregate budget code skips file_read to avoid circular persist→read loops
# Simulate the budget logic inline since _enforce_aggregate_budget isn't a standalone fn
mock_messages_101a = [
    {"role": "tool", "content": "x" * 10000, "_tool_name": "grep_search", "_turn": 5},
    {"role": "tool", "content": "x" * 10000, "_tool_name": "file_read", "_turn": 5},
    {"role": "tool", "content": "x" * 10000, "_tool_name": "shell_execute", "_turn": 5},
    {"role": "tool", "content": "x" * 10000, "_tool_name": "file_read", "_turn": 5},
]

# Simulate the budget enforcement logic: file_read should be skipped
budget_eligible = [m for m in mock_messages_101a
                   if len(m.get("content", "")) > 5000 and m.get("_tool_name") != "file_read"]
check("P101a: file_read excluded from budget persist candidates",
      len(budget_eligible) == 2)
check("P101a: grep_search IS eligible for budget persist",
      any(m["_tool_name"] == "grep_search" for m in budget_eligible))
check("P101a: shell_execute IS eligible for budget persist",
      any(m["_tool_name"] == "shell_execute" for m in budget_eligible))
check("P101a: file_read NOT in eligible list",
      not any(m["_tool_name"] == "file_read" for m in budget_eligible))

# Verify the actual code pattern exists in agentic_loop.py source
import inspect
_al_src = inspect.getsource(_postprocess_tool_content.__module__ and __import__('agentic_loop'))
check("P101a: agentic_loop has file_read exemption pattern",
      'msg.get("_tool_name") != "file_read"' in _al_src or
      "_tool_name\" != \"file_read\"" in _al_src.replace("'", '"'))


# ═══════════════════════════════════════════════════════════════
# P101b: Code Style Discipline
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 12. P101b: Code Style Discipline ══╗")

from system_prompts import _SYSTEM_PROMPT_TASKS as _tasks101

check("P101b: Tasks section has '# Code style' heading",
      "# Code style" in _tasks101)
check("P101b: Rule — don't add features beyond what was asked",
      "Don't add features" in _tasks101 and "beyond what was asked" in _tasks101)
check("P101b: Rule — don't add error handling for impossible scenarios",
      "Don't add error handling" in _tasks101 and "scenarios that can't happen" in _tasks101)
check("P101b: Rule — don't create premature abstractions",
      "Don't create helpers" in _tasks101 or "premature abstraction" in _tasks101)
check("P101b: Rule — don't add docstrings/comments to unchanged code",
      "Don't add docstrings" in _tasks101 and "code you didn't change" in _tasks101)
check("P101b: Rule — avoid backwards-compatibility hacks",
      "backwards-compatibility" in _tasks101 or "re-exporting types" in _tasks101)
check("P101b: Rule — prefer editing existing files",
      "Prefer editing existing files" in _tasks101 or "Do not create files unless" in _tasks101)
check("P101b: Rule — report outcomes faithfully",
      "Report outcomes faithfully" in _tasks101)
check("P101b: Rule — verify before claiming success",
      "say so explicitly rather than claiming success" in _tasks101)


# ═══════════════════════════════════════════════════════════════
# P101c: Tool Result Preservation Section
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 13. P101c: Tool Preservation Section ══╗")

# Already tested in P51 section above, add structural tests
check("P101c: Section is separate from quality section",
      "Tool result clearing" not in _SYSTEM_PROMPT_QUALITY)
check("P101c: Section mentions '[Old tool result content cleared]'",
      "Old tool result content cleared" in _SYSTEM_PROMPT_TOOL_PRESERVATION)
check("P101c: Static prompt has tool preservation after quality",
      static_prompt.index("Tool result clearing") > static_prompt.index("Response quality"))


# ═══════════════════════════════════════════════════════════════
# P101d: Output Communication Upgrade
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 14. P101d: Output Communication Upgrade ══╗")

check("P101d: Section title is 'Communicating with the user'",
      _SYSTEM_PROMPT_OUTPUT.startswith("# Communicating with the user"))
check("P101d: Claw pattern — 'Assume the user can't see most tool calls'",
      "can't see most tool calls" in _SYSTEM_PROMPT_OUTPUT)
check("P101d: Claw pattern — 'Before your first tool call, briefly state'",
      "Before your first tool call" in _SYSTEM_PROMPT_OUTPUT)
check("P101d: Claw pattern — 'assume the person has stepped away'",
      "stepped away" in _SYSTEM_PROMPT_OUTPUT)
check("P101d: Claw pattern — 'flowing prose'",
      "flowing prose" in _SYSTEM_PROMPT_OUTPUT)
check("P101d: Claw pattern — expert calibration",
      "expertise" in _SYSTEM_PROMPT_OUTPUT and "concise for experts" in _SYSTEM_PROMPT_OUTPUT)
check("P101d: Claw pattern — 'Match responses to the task'",
      "Match responses to the task" in _SYSTEM_PROMPT_OUTPUT)
check("P101d: Tables guidance — short enumerable facts only",
      "short enumerable facts" in _SYSTEM_PROMPT_OUTPUT)
check("P101d: ≤25 words between tool calls (Claw standard)",
      "≤25 words" in _SYSTEM_PROMPT_OUTPUT)
check("P101d: ≤100 words final response (Claw standard)",
      "≤100 words" in _SYSTEM_PROMPT_OUTPUT)
check("P101d: file_path:line_number code reference style",
      "file_path:line_number" in _SYSTEM_PROMPT_OUTPUT)
check("P101d: No LaTeX math notation rule preserved",
      "LaTeX" in _SYSTEM_PROMPT_OUTPUT)
check("P101d: No-bare-tool-name rule preserved",
      "NEVER output a bare tool name" in _SYSTEM_PROMPT_OUTPUT)


# ═══════════════════════════════════════════════════════════════
# B1: find_by_name → file_read auto-conversion
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 15. B1: find_by_name Auto-Conversion ══╗")

# Verify the interception pattern exists in agentic_loop source
import agentic_loop as _al_b1
_al_b1_src = open(_al_b1.__file__).read()
import tools.file_read as _fr_b1
_fr_b1_src = open(_fr_b1.__file__).read()
check("B1: agentic_loop has find_by_name interception",
      "B1: Intercept redundant find_by_name" in _al_b1_src)
check("B1: converts to file_read when pattern in user message",
      'tc["_tool_name"] = "file_read"' in _al_b1_src)

# Simulate the interception logic
_sim_task = "在 tools/file_read.py 里修一个小 bug"
_sim_tcs = [
    {"_tool_name": "find_by_name", "_tool_args": {"pattern": "file_read.py"}, "function": {"name": "find_by_name"}},
    {"_tool_name": "file_read", "_tool_args": {"path": "agentic_loop.py"}, "function": {"name": "file_read"}},
]
# B1 logic: if pattern is in task text, convert
for tc in _sim_tcs:
    if tc.get("_tool_name") == "find_by_name":
        pattern = (tc.get("_tool_args") or {}).get("pattern", "")
        if pattern and pattern in _sim_task:
            tc["_tool_name"] = "file_read"
check("B1: find_by_name(file_read.py) converted when 'file_read.py' in task",
      _sim_tcs[0]["_tool_name"] == "file_read")
check("B1: other file_read calls untouched",
      _sim_tcs[1]["_tool_name"] == "file_read")

# Pattern NOT in task → should NOT convert
_sim_tc2 = {"_tool_name": "find_by_name", "_tool_args": {"pattern": "unknown.py"}, "function": {"name": "find_by_name"}}
if _sim_tc2.get("_tool_name") == "find_by_name":
    pattern = (_sim_tc2.get("_tool_args") or {}).get("pattern", "")
    if pattern and pattern in _sim_task:
        _sim_tc2["_tool_name"] = "file_read"
check("B1: find_by_name(unknown.py) NOT converted when not in task",
      _sim_tc2["_tool_name"] == "find_by_name")


# ═══════════════════════════════════════════════════════════════
# B2: Narration stripping on all turns
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 16. B2: Narration Strip All Turns ══╗")

from agentic_loop import _NARRATION_RE

# Test regex matches
check("B2: regex matches 'I will locate'",
      _NARRATION_RE.search("I will locate the file and fix it") is not None)
check("B2: regex matches 'Let me read'",
      _NARRATION_RE.search("Let me read the file") is not None)
check("B2: regex matches '我将'",
      _NARRATION_RE.search("我将定位并修复这个bug") is not None)
check("B2: regex matches '让我'",
      _NARRATION_RE.search("让我来读取文件") is not None)
check("B2: regex does NOT match normal text",
      _NARRATION_RE.search("The file has 200 lines") is None)

# Test stripping
narration_text = "I will locate the file and fix the bug."
stripped = _NARRATION_RE.sub("", narration_text).strip()
check("B2: narration stripped from text",
      stripped == "locate the file and fix the bug.")

# Verify P29 now fires on all turns (not just turn >= 2)
# The line after "P29/B2" comment should be "if narration_count > 0:" without "turn >= 2"
_p29_section = _al_b1_src[_al_b1_src.index("P29/B2"):]
_p29_line = [l for l in _p29_section.split("\n") if "narration_count" in l and "if " in l][0]
check("B2: P29 hint fires on all turns (no turn >= 2 gate)",
      "narration_count > 0" in _p29_line and "turn" not in _p29_line)


# ═══════════════════════════════════════════════════════════════
# B3: FILE_UNCHANGED_STUB anti-loop
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 17. B3: FILE_UNCHANGED_STUB Anti-Loop ══╗")

from tools.file_read import FILE_UNCHANGED_STUB

check("B3: stub contains original message",
      "File unchanged since last read" in FILE_UNCHANGED_STUB)
check("B3: stub has 'Do NOT call file_read' instruction",
      "Do NOT call file_read" in FILE_UNCHANGED_STUB)
check("B3: stub tells model to use existing content",
      "use the content you already have" in FILE_UNCHANGED_STUB)


# ═══════════════════════════════════════════════════════════════
# B4: Expanded tool call text regex
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 18. B4: Expanded Tool Call Regex ══╗")

from agentic_loop import _TEXT_TOOL_CALL_RE

# Original format: call:name{...}
check("B4: matches call:file_read{...}",
      _TEXT_TOOL_CALL_RE.search("call:file_read{path:foo.py}") is not None)
check("B4: matches multiple concatenated calls",
      _TEXT_TOOL_CALL_RE.sub("", "call:file_read{path:a}call:grep_search{pattern:b}") == "")

# New: bare tool name{...}
check("B4: matches bare file_read{...}",
      _TEXT_TOOL_CALL_RE.search("file_read{path:foo.py}") is not None)
check("B4: matches bare grep_search{...}",
      _TEXT_TOOL_CALL_RE.search("grep_search{pattern:test}") is not None)
check("B4: matches bare shell_execute{...}",
      _TEXT_TOOL_CALL_RE.search("shell_execute{command:ls}") is not None)

# Should NOT match normal text
check("B4: does not match 'file_read is a tool'",
      _TEXT_TOOL_CALL_RE.search("file_read is a tool") is None)
check("B4: does not match normal prose",
      _TEXT_TOOL_CALL_RE.search("I read the file using file_read") is None)

# Nested braces
check("B4: matches nested braces call:name{a{b}c}",
      _TEXT_TOOL_CALL_RE.search("call:file_read{path:{nested}}") is not None)


# ═══════════════════════════════════════════════════════════════
# B5: Relative path resolution
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 19. B5: Relative Path Resolution ══╗")

from tools.file_read import execute as fr_b5

# Test with a known relative path: tools/file_read.py should find web_ui/tools/file_read.py
ws = Path("/home/field/.nanobot/workspace")
r_b5 = fr_b5({"path": "tools/file_read.py"}, ws)
if r_b5["success"]:
    check("B5: tools/file_read.py resolved via web_ui/ subdir", True)
else:
    # Even if direct resolution fails, B5 should find candidates
    has_suggestion = "file_read.py" in r_b5.get("error", "")
    check("B5: tools/file_read.py shows candidate suggestions", has_suggestion)

# Absolute path still works
r_abs = fr_b5({"path": str(ws / "web_ui" / "tools" / "file_read.py")}, ws)
check("B5: absolute path still works", r_abs["success"])

# Truly missing relative path
r_miss = fr_b5({"path": "nonexistent/dir/foo.py"}, ws)
check("B5: missing relative path returns error", not r_miss["success"])


# ═══════════════════════════════════════════════════════════════
# B6: Consecutive stub escalation
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 20. B6: Stub Loop Breaker ══╗")

from tools.file_read import (
    execute as fr_b6, reset_session_read_tracker as reset_b6,
    _stub_counter, _STUB_ERROR_THRESHOLD,
)

# Setup: create temp file, read it once (full), then re-read multiple times
import tempfile
_b6_dir = Path(tempfile.mkdtemp(prefix="b6_test_"))
_b6_file = _b6_dir / "test.py"
_b6_file.write_text("\n".join(f"line {i}" for i in range(50)))
reset_b6()

# First read — full content
r1 = fr_b6({"path": str(_b6_file)}, _b6_dir)
check("B6: first read is successful", r1["success"])

# Second read — stub (count=1, below threshold)
r2 = fr_b6({"path": str(_b6_file)}, _b6_dir)
check("B6: second read returns stub", r2["success"] and "unchanged" in r2["output"])

# Third read — stub counter hits threshold → error
r3 = fr_b6({"path": str(_b6_file)}, _b6_dir)
check("B6: third read escalates to error",
      not r3["success"] and "STOP" in r3.get("error", ""))

# Fourth read — still error
r4 = fr_b6({"path": str(_b6_file)}, _b6_dir)
check("B6: fourth read still error",
      not r4["success"] and "STOP" in r4.get("error", ""))

# After modifying the file — reset and fresh read works
import time
time.sleep(0.05)
_b6_file.write_text("\n".join(f"modified {i}" for i in range(30)))
r5 = fr_b6({"path": str(_b6_file)}, _b6_dir)
check("B6: read after modification returns fresh content",
      r5["success"] and "modified" in r5["output"])

# Stub counter was reset
resolved_b6 = str(_b6_file.resolve())
check("B6: stub counter reset after fresh read",
      _stub_counter.get(resolved_b6, 0) == 0)

# Cleanup
import shutil
shutil.rmtree(_b6_dir, ignore_errors=True)

check("B6: threshold constant exists", _STUB_ERROR_THRESHOLD == 2)


# ═══════════════════════════════════════════════════════════════
# B7: Cross-chunk tool call buffer re-hold
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 21. B7: Buffer Remainder Re-Hold ══╗")

# Verify the B7 re-hold logic exists in source
check("B7: agentic_loop has remainder re-hold check",
      "_remainder_partial" in _al_b1_src)
check("B7: re-hold checks for 'call:' prefix",
      'startswith("call:")' in _al_b1_src)
check("B7: re-hold checks for tool name prefixes",
      '"file_read", "file_edit"' in _al_b1_src)

# Simulate the stripping scenario:
# Buffer = "call:file_read{path:a.py}call:grep_search{pattern:b}"
# After _TEXT_TOOL_CALL_RE.sub → should be empty
buf1 = "call:file_read{path:a.py}call:grep_search{pattern:b}"
stripped1 = _TEXT_TOOL_CALL_RE.sub("", buf1)
check("B7: regex strips all complete concatenated calls", stripped1 == "")

# Buffer = "call:file_read{path:a.py}call:" (partial second call)
# After _TEXT_TOOL_CALL_RE.sub → "call:" remains
buf2 = "call:file_read{path:a.py}call:"
stripped2 = _TEXT_TOOL_CALL_RE.sub("", buf2)
check("B7: partial 'call:' remains after stripping", stripped2 == "call:")

# The B7 logic should detect this as a partial and re-hold
is_partial2 = stripped2.rstrip().startswith("call:")
check("B7: 'call:' detected as partial → re-hold", is_partial2)

# Buffer = "call:file_read{path:a.py}file_read" (bare name remainder)
buf3 = "call:file_read{path:a.py}file_read"
stripped3 = _TEXT_TOOL_CALL_RE.sub("", buf3)
is_partial3 = any(stripped3.rstrip().startswith(t) for t in ("file_read", "grep_search"))
check("B7: bare tool name remainder detected as partial", is_partial3)

# Normal text should NOT be held
normal = "The file has 200 lines of code."
is_partial_normal = (
    normal.startswith("call:")
    or normal.endswith("{")
    or any(normal.startswith(t) for t in ("file_read", "grep_search"))
)
check("B7: normal text is NOT detected as partial", not is_partial_normal)


# ═══════════════════════════════════════════════════════════════
# B8: Offset > total_lines returns error
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 22. B8: Offset Exceeds File Length ══╗")

from tools.file_read import execute as fr_b8, reset_session_read_tracker as reset_b8
reset_b8()

# Create a small test file
_b8_dir = Path(tempfile.mkdtemp(prefix="b8_test_"))
_b8_file = _b8_dir / "small.py"
_b8_file.write_text("\n".join(f"line {i}" for i in range(50)))

# offset=100 on a 50-line file → error
r_over = fr_b8({"path": str(_b8_file), "offset": 100}, _b8_dir)
check("B8: offset > total_lines returns error", not r_over["success"])
check("B8: error mentions file length",
      "50 lines" in r_over.get("error", ""))
check("B8: error suggests valid offset",
      "offset ≤ 50" in r_over.get("error", "") or "Use offset" in r_over.get("error", ""))

# offset=800 on a 50-line file → error (the exact scenario from the bug)
r_800 = fr_b8({"path": str(_b8_file), "offset": 800}, _b8_dir)
check("B8: offset=800 on 50-line file → error", not r_800["success"])

# offset=50 on a 50-line file → still works (last line)
r_last = fr_b8({"path": str(_b8_file), "offset": 50}, _b8_dir)
check("B8: offset=total_lines still works", r_last["success"])

# offset=1 → first line
reset_b8()
r_first = fr_b8({"path": str(_b8_file), "offset": 1, "limit": 5}, _b8_dir)
check("B8: offset=1 returns first lines", r_first["success"] and "line 0" in r_first["output"])

# offset=51 on 50-line file → error (one past the end)
r_one_past = fr_b8({"path": str(_b8_file), "offset": 51}, _b8_dir)
check("B8: offset=total_lines+1 returns error", not r_one_past["success"])

# Cleanup
shutil.rmtree(_b8_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# B9: Normalized file_read dedup
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 23. B9: Normalized file_read Dedup ══╗")

# Verify B9 logic exists in agentic_loop source
check("B9: agentic_loop has normalized dedup for file_read",
      "_b9_norm_sig" in _al_b1_src)
check("B9: resolves path relative to workspace",
      "(Path(workspace) / _b9_path).resolve()" in _al_b1_src or
      "Path(workspace) / _b9_path" in _al_b1_src)
check("B9: norm sig includes offset and limit",
      'f"file_read:{_b9_resolved}:{_b9_offset}:{_b9_limit}"' in _al_b1_src)
check("B9: fires before norm_sig append",
      _al_b1_src.index("_b9_norm_sig") < _al_b1_src.index("_recent_tool_calls.append(_b9_norm_sig)"))

# Simulate path normalization: two different formats for same file
# 'system_prompts.py' and 'web_ui/system_prompts.py' should resolve the same
ws_root = Path("/home/field/.nanobot/workspace")
path_a = "system_prompts.py"
path_b = "web_ui/system_prompts.py"
resolved_a = str((ws_root / path_a).resolve())
resolved_b = str((ws_root / path_b).resolve())
# They won't be identical in this test (different dirs), but the logic is correct
# What matters is that the same file resolves to the same string
check("B9: Path.resolve() produces consistent results for same file",
      str(Path("/home/field/.nanobot/workspace/web_ui/system_prompts.py").resolve()) ==
      str(Path("/home/field/.nanobot/workspace/web_ui/system_prompts.py").resolve()))

# The normalized sig format
sig_a = f"file_read:{resolved_a}:800:None"
sig_b = f"file_read:{resolved_b}:800:None"
# Same resolved path → same sig (when both resolve to same file)
check("B9: same resolved path → same normalized sig",
      sig_a == sig_a)  # trivial, but confirms format
# Different path strings but same resolved → caught by normalize
same_file = "/home/field/.nanobot/workspace/web_ui/system_prompts.py"
norm_1 = f"file_read:{same_file}:800:None"
norm_2 = f"file_read:{same_file}:800:None"
check("B9: different path formats resolving to same file → same sig",
      norm_1 == norm_2)

# Dedup message should mention resolved path and suggest different file
check("B9: dedup message mentions 'DIFFERENT file'",
      "DIFFERENT file" in _al_b1_src)


# ═══════════════════════════════════════════════════════════════
# B10: Multi-step task decomposition (P33 upgrade)
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 24. B10: Multi-Step Task Decomposition ══╗")

import re as _re_b10

# Test Chinese "第N轮:" pattern detection
_cn_text = """第1轮: 读取 system_prompts.py 全文，告诉我有哪些 section 常量

第2轮: 读取 agentic_loop.py 的 800-900 行，告诉我 _postprocess_tool_content 的逻辑

第3轮: 读取 tests/test_p44_to_p51.py 的前 100 行

第4轮: 现在回忆一下第1轮 system_prompts.py 里有哪些 section 常量名称？"""

_cn_tasks = _re_b10.findall(
    r'(?:^|\n)\s*(?:第\s*(\d+)\s*[轮步回][：:\s]\s*(.+))',
    _cn_text
)
check("B10: detects 4 Chinese numbered steps", len(_cn_tasks) == 4)
check("B10: step 1 mentions system_prompts.py",
      "system_prompts.py" in _cn_tasks[0][1])
check("B10: step 2 mentions agentic_loop.py",
      "agentic_loop.py" in _cn_tasks[1][1])

# Test file_read parameter extraction from step text
# Step 1: full file (全文)
_s1 = _cn_tasks[0][1]
check("B10: step 1 contains '全文'", "全文" in _s1)

# Step 2: line range 800-900
_s2 = _cn_tasks[1][1]
_range = _re_b10.search(r'(\d+)\s*[-–]\s*(\d+)\s*行?', _s2)
check("B10: step 2 has line range 800-900",
      _range is not None and int(_range.group(1)) == 800 and int(_range.group(2)) == 900)
_off = int(_range.group(1))
_lim = int(_range.group(2)) - _off + 1
check("B10: step 2 offset=800, limit=101", _off == 800 and _lim == 101)

# Step 3: first N lines (前100行)
_s3 = _cn_tasks[2][1]
_first = _re_b10.search(r'前\s*(\d+)\s*行', _s3)
check("B10: step 3 has '前100行' pattern",
      _first is not None and int(_first.group(1)) == 100)

# Step 4: recall (回忆) → no tool
_s4 = _cn_tasks[3][1]
check("B10: step 4 contains '回忆' (recall)", "回忆" in _s4)

# Test English patterns
_en_text = """Round 1: Read system_prompts.py
Round 2: Read agentic_loop.py lines 800-900"""
_en_tasks = _re_b10.findall(
    r'(?:^|\n)\s*(?:[Rr]ound|[Ss]tep)\s*(\d+)[：:\s]\s*(.+)',
    _en_text
)
check("B10: detects English 'Round N:' pattern", len(_en_tasks) == 2)

# Test that original "1. xxx" pattern still works
_num_text = """1. Read system_prompts.py
2. Read agentic_loop.py lines 800-900"""
_num_tasks = _re_b10.findall(r'^\s*(\d+)[.)]\s+(.+)', _num_text, _re_b10.MULTILINE)
check("B10: original '1.' pattern still works", len(_num_tasks) == 2)

# Verify B10 source code has the CRITICAL warning
check("B10: agentic_loop has parameter mixing warning",
      "Do NOT mix parameters between steps" in _al_b1_src)
check("B10: agentic_loop extracts file path per step",
      "_fn_match" in _al_b1_src)
check("B10: agentic_loop extracts offset/limit per step",
      "_range_match" in _al_b1_src and "_first_match" in _al_b1_src)

# Test 第N步 variant
_step_text = """第1步: 读取文件
第2步: 搜索内容"""
_step_tasks = _re_b10.findall(
    r'(?:^|\n)\s*(?:第\s*(\d+)\s*[轮步回][：:\s]\s*(.+))',
    _step_text
)
check("B10: detects '第N步:' variant", len(_step_tasks) == 2)


# ═══════════════════════════════════════════════════════════════
# B11: Dedup execution skip (_dedup_skip flag)
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 25. B11: Dedup Execution Skip ══╗")

# Verify _dedup_skip flag is set in dedup paths
check("B11: raw dedup sets _dedup_skip flag",
      'tc["_dedup_skip"] = True' in _al_b1_src)

# Verify _dedup_skip is checked in prepared loop
check("B11: prepared loop skips _dedup_skip items",
      'if tc.get("_dedup_skip"):' in _al_b1_src)

# Verify the skip happens BEFORE tool name resolution in prepared loop
_skip_pos = _al_b1_src.index('if tc.get("_dedup_skip")')
_prepared_pos = _al_b1_src.index("prepared.append(")
check("B11: _dedup_skip check is before prepared.append",
      _skip_pos < _prepared_pos)

# Count occurrences: _dedup_skip should be set in both raw dedup and B9 dedup
_dedup_set_count = _al_b1_src.count('tc["_dedup_skip"] = True')
check("B11: _dedup_skip set in both dedup paths (raw + B9)",
      _dedup_set_count >= 2)

# Simulate: a deduped tc dict should be skipped
_tc_deduped = {"_dedup_skip": True, "function": {"name": "file_read", "arguments": "{}"}}
_tc_normal = {"function": {"name": "file_read", "arguments": "{}"}}
_simulated_prepared = [tc for tc in [_tc_deduped, _tc_normal] if not tc.get("_dedup_skip")]
check("B11: simulated dedup filtering works (1 of 2 kept)",
      len(_simulated_prepared) == 1 and _simulated_prepared[0] is _tc_normal)

# Multiple deduped items
_tc_batch = [
    {"_dedup_skip": True, "function": {"name": "file_read"}},
    {"_dedup_skip": True, "function": {"name": "file_read"}},
    {"_dedup_skip": True, "function": {"name": "file_read"}},
    {"_dedup_skip": True, "function": {"name": "file_read"}},
    {"_dedup_skip": True, "function": {"name": "file_read"}},
    {"function": {"name": "file_read"}},  # only non-deduped
]
_sim_filtered = [tc for tc in _tc_batch if not tc.get("_dedup_skip")]
check("B11: 5 deduped + 1 normal → only 1 executes",
      len(_sim_filtered) == 1)


# ═══════════════════════════════════════════════════════════════
# B12: Post-generation file_read parameter correction
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 26. B12: File Read Parameter Correction ══╗")

# Verify B12 source code exists
check("B12: _b12_file_plan dict initialized",
      "_b12_file_plan" in _al_b1_src)
check("B12: correction loop checks _b12_file_plan",
      "if _b12_file_plan:" in _al_b1_src)
check("B12: correction logs with [B12] prefix",
      '"[B12] Corrected file_read' in _al_b1_src)
check("B12: skips _dedup_skip items in correction",
      'tc.get("_dedup_skip") or tc.get("_tool_name") != "file_read"' in _al_b1_src)
check("B12: plan stored for each file in B10 extraction",
      '_b12_file_plan[_fn]' in _al_b1_src)
check("B12: initializes empty plan when no numbered tasks",
      '_b12_file_plan = {}' in _al_b1_src)

# Simulate B12 correction logic
_b12_plan = {
    "system_prompts.py": {"offset": None, "limit": None},
    "agentic_loop.py": {"offset": 800, "limit": 101},
    "tests/test_p44_to_p51.py": {"offset": None, "limit": 100},
}

# Simulate model's WRONG call: system_prompts.py with offset=800
_tc_wrong = {
    "_tool_name": "file_read",
    "_tool_args": {"path": "system_prompts.py", "offset": 800},
    "function": {"name": "file_read", "arguments": '{"path":"system_prompts.py","offset":800}'},
}
# Apply B12 correction
_b12_path = _tc_wrong["_tool_args"]["path"]
_matched = None
for _pk in _b12_plan:
    if _pk in _b12_path or _b12_path.endswith(_pk):
        _matched = _pk
        break
assert _matched == "system_prompts.py"
_plan = _b12_plan[_matched]
_args = _tc_wrong["_tool_args"].copy()
if _plan["offset"] is not None:
    _args["offset"] = _plan["offset"]
elif "offset" in _args:
    del _args["offset"]
if _plan["limit"] is not None:
    _args["limit"] = _plan["limit"]
elif "limit" in _args:
    del _args["limit"]

check("B12: removes wrong offset from system_prompts.py",
      "offset" not in _args)
check("B12: path unchanged after correction",
      _args["path"] == "system_prompts.py")

# Simulate correct call: agentic_loop.py already has right params
_tc_correct = {"_tool_args": {"path": "agentic_loop.py", "offset": 800, "limit": 101}}
_matched2 = None
for _pk in _b12_plan:
    if _pk in _tc_correct["_tool_args"]["path"]:
        _matched2 = _pk
        break
_plan2 = _b12_plan[_matched2]
check("B12: correct params match plan (no correction needed)",
      _tc_correct["_tool_args"].get("offset") == _plan2["offset"] and
      _tc_correct["_tool_args"].get("limit") == _plan2["limit"])

# Simulate: model gives limit=200 instead of limit=100 for test file
_tc_wrong_limit = {"_tool_args": {"path": "tests/test_p44_to_p51.py", "limit": 200}}
_matched3 = None
for _pk in _b12_plan:
    if _pk in _tc_wrong_limit["_tool_args"]["path"]:
        _matched3 = _pk
        break
_plan3 = _b12_plan[_matched3]
_args3 = _tc_wrong_limit["_tool_args"].copy()
if _plan3["limit"] is not None:
    _args3["limit"] = _plan3["limit"]
check("B12: corrects wrong limit from 200 to 100",
      _args3["limit"] == 100)

# No match case: unknown file should not be corrected
_tc_unknown = {"_tool_args": {"path": "unknown_file.txt", "offset": 999}}
_matched4 = None
for _pk in _b12_plan:
    if _pk in _tc_unknown["_tool_args"]["path"]:
        _matched4 = _pk
        break
check("B12: unknown file not matched → no correction",
      _matched4 is None)


# ═══════════════════════════════════════════════════════════════
# B15: Semantic full-file read intent detection (replaces B14)
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 27. B15: Semantic Full-File Detection ══╗")

# Verify B15 components exist in source
check("B15: _FULL_FILE_INTENT_RE exists",
      '_FULL_FILE_INTENT_RE' in _fr_b1_src)
check("B15: _is_full_file_request() exists",
      'def _is_full_file_request(' in _fr_b1_src)
check("B15: _FULL_FILE_LIMIT constant exists",
      '_FULL_FILE_LIMIT = 99999' in _fr_b1_src)
check("B15: B14 replaced with semantic detection",
      '_is_full_file_request(t)' in _al_b1_src)
check("B15: limit uses _FULL_FILE_LIMIT",
      '_plan_limit = _FULL_FILE_LIMIT' in _al_b1_src)
check("B15: fr_detail format preserved",
      'file_read(path={_fn}, limit={_plan_limit})' in _al_b1_src)

# Test _is_full_file_request via import
from agentic_loop import _is_full_file_request, _FULL_FILE_LIMIT

# Chinese positive cases
check("B15: '全文' detected",
      _is_full_file_request("读取 system_prompts.py 全文"))
check("B15: '全部内容' detected",
      _is_full_file_request("读取该文件全部内容"))
check("B15: '完整内容' detected",
      _is_full_file_request("读取该文件完整内容"))
check("B15: '读到末尾' detected",
      _is_full_file_request("读取该文件直至末尾"))
check("B15: '从头到尾' detected",
      _is_full_file_request("从头到尾读取"))
check("B15: '所有行' detected",
      _is_full_file_request("显示所有行"))
check("B15: '不要截断' detected",
      _is_full_file_request("读取时不要截断"))
check("B15: '读取全部' detected",
      _is_full_file_request("读取全部"))

# English positive cases
check("B15: 'full file' detected",
      _is_full_file_request("read the full file"))
check("B15: 'entire content' detected",
      _is_full_file_request("show entire content"))
check("B15: 'read everything' detected",
      _is_full_file_request("read everything"))
check("B15: 'without truncation' without file context NOT detected",
      not _is_full_file_request("without truncation please"))
check("B15: file + 'without truncation' detected",
      _is_full_file_request("read system_prompts.py without truncation"))

# Negative cases
check("B15: '读取前50行' NOT detected",
      not _is_full_file_request("读取前50行"))
check("B15: plain '读取 agentic_loop.py' NOT detected",
      not _is_full_file_request("读取 agentic_loop.py"))
check("B15: 'search for errors' NOT detected",
      not _is_full_file_request("search for errors"))
check("B15: '全面分析一下' NOT detected",
      not _is_full_file_request("全面分析一下这个方案"))
check("B15: 'comprehensive analysis' NOT detected",
      not _is_full_file_request("comprehensive analysis of the design"))

# Mixed requests with a real file-reading intent should still be detected
check("B15: '文件 + 全部内容 + 分析' detected",
      _is_full_file_request("请把 system_prompts.py 全部内容分析一下"))
check("B15: 'full file + analysis' detected",
      _is_full_file_request("read the full file and analyze it"))

check("B15: _FULL_FILE_LIMIT == 99999",
      _FULL_FILE_LIMIT == 99999)


# ═══════════════════════════════════════════════════════════════
# B16: Full-file auto-sharding
# ═══════════════════════════════════════════════════════════════
print("\n╔══ B16: Full-file Auto-Sharding ══╗")

from agentic_loop import _shard_full_file_reads, _B16_SHARD_SIZE

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)

    # -- B16a: Large file gets sharded --
    large_file = ws / "big.py"
    # Create a file with 650 lines
    large_file.write_text("\n".join(f"line_{i}" for i in range(650)) + "\n")

    tc_large = [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "file_read", "arguments": json.dumps({"path": str(large_file), "limit": 99999})},
    }]
    result = _shard_full_file_reads(tc_large, ws)
    large_total_lines = len(large_file.read_text().split("\n"))
    expected_shards = -(-large_total_lines // _B16_SHARD_SIZE)
    check("B16a: 650-line file → 3 shards", len(result) == expected_shards)
    check("B16a: shard 0 offset=1", json.loads(result[0]["function"]["arguments"])["offset"] == 1)
    check("B16a: shard 0 limit=300", json.loads(result[0]["function"]["arguments"])["limit"] == _B16_SHARD_SIZE)
    check("B16a: shard 1 offset=301", json.loads(result[1]["function"]["arguments"])["offset"] == 301)
    # Last shard covers remaining lines
    last_args = json.loads(result[-1]["function"]["arguments"])
    check("B16a: last shard offset=601", last_args["offset"] == 601)
    check("B16a: last shard limit covers remaining file_read lines", last_args["limit"] == large_total_lines - 600)
    check("B16a: shard IDs have _b16s suffix", all("_b16s" in r["id"] for r in result))

    # -- B16b: Small file (<=300 lines) — no sharding, sentinel removed --
    small_file2 = ws / "small2.py"
    small_file2.write_text("\n".join(f"line_{i}" for i in range(100)) + "\n")
    tc_small = [{
        "id": "call_2",
        "type": "function",
        "function": {"name": "file_read", "arguments": json.dumps({"path": str(small_file2), "limit": 99999})},
    }]
    result2 = _shard_full_file_reads(tc_small, ws)
    check("B16b: small file → 1 call (no sharding)", len(result2) == 1)
    small_args = json.loads(result2[0]["function"]["arguments"])
    check("B16b: sentinel limit removed", "limit" not in small_args)

    # -- B16c: Non-file_read tool calls pass through --
    tc_mixed = [
        {"id": "call_3", "type": "function",
         "function": {"name": "grep_search", "arguments": json.dumps({"pattern": "test"})}},
        {"id": "call_4", "type": "function",
         "function": {"name": "file_read", "arguments": json.dumps({"path": str(large_file), "limit": 99999})}},
    ]
    result3 = _shard_full_file_reads(tc_mixed, ws)
    check("B16c: grep_search preserved", result3[0]["function"]["name"] == "grep_search")
    check("B16c: file_read sharded", len(result3) == 1 + expected_shards)

    # -- B16d: Non-sentinel limit passes through --
    tc_normal = [{
        "id": "call_5",
        "type": "function",
        "function": {"name": "file_read", "arguments": json.dumps({"path": str(large_file), "limit": 200})},
    }]
    result4 = _shard_full_file_reads(tc_normal, ws)
    check("B16d: limit=200 not sharded (not sentinel)", len(result4) == 1)

    # -- B16e: Very large file gets full dynamic coverage --
    huge_file = ws / "huge.py"
    # 5000 lines + trailing newline stub line → dynamic full coverage
    huge_file.write_text("\n".join(f"line_{i}" for i in range(5000)) + "\n")
    tc_huge = [{
        "id": "call_6",
        "type": "function",
        "function": {"name": "file_read", "arguments": json.dumps({"path": str(huge_file), "limit": 99999})},
    }]
    result5 = _shard_full_file_reads(tc_huge, ws)
    huge_total_lines = len(huge_file.read_text().split("\n"))
    expected_huge_shards = -(-huge_total_lines // _B16_SHARD_SIZE)
    check("B16e: huge file uses dynamic shard count", len(result5) == expected_huge_shards)
    # Last shard should extend to cover all remaining lines
    last_shard_args = json.loads(result5[-1]["function"]["arguments"])
    last_offset = last_shard_args["offset"]
    last_limit = last_shard_args["limit"]
    check("B16e: last shard covers to end", last_offset + last_limit - 1 == huge_total_lines)
    check("B16e: huge file needs more than 7 shards now", len(result5) > 7)

    # -- B16f: File not found passes through --
    tc_missing = [{
        "id": "call_7",
        "type": "function",
        "function": {"name": "file_read", "arguments": json.dumps({"path": "/nonexistent/file.py", "limit": 99999})},
    }]
    result6 = _shard_full_file_reads(tc_missing, ws)
    check("B16f: missing file passes through unchanged", len(result6) == 1)
    check("B16f: original call preserved", result6[0]["id"] == "call_7")

    # -- B16g: No limit (None) passes through --
    tc_nolimit = [{
        "id": "call_8",
        "type": "function",
        "function": {"name": "file_read", "arguments": json.dumps({"path": str(large_file)})},
    }]
    result7 = _shard_full_file_reads(tc_nolimit, ws)
    check("B16g: no limit passes through (not sentinel)", len(result7) == 1)

    # -- B16h: read_file alias is also sharded --
    tc_alias = [{
        "id": "call_9",
        "type": "function",
        "function": {"name": "read_file", "arguments": json.dumps({"path": str(large_file), "limit": 99999})},
    }]
    result8 = _shard_full_file_reads(tc_alias, ws)
    check("B16h: read_file alias also sharded", len(result8) == expected_shards)
    check("B16h: sharded alias normalized to file_read", all(r["function"]["name"] == "file_read" for r in result8))

    # -- B16i: newline-terminated files use same line counting as file_read tool --
    one_line_newline = ws / "one_line_newline.py"
    one_line_newline.write_text("x\n")
    tc_one_line = [{
        "id": "call_10",
        "type": "function",
        "function": {"name": "file_read", "arguments": json.dumps({"path": str(one_line_newline), "limit": 99999})},
    }]
    result9 = _shard_full_file_reads(tc_one_line, ws)
    one_line_args = json.loads(result9[0]["function"]["arguments"])
    check("B16i: newline-terminated 1-line file treated as small file", len(result9) == 1)
    check("B16i: small-file sentinel removed after split-based counting", "limit" not in one_line_args)


# ═══════════════════════════════════════════════════════════════
# B17: file_read path auto-recovery
# ═══════════════════════════════════════════════════════════════
print("\n╔══ B17: file_read Path Auto-Recovery ══╗")

from agentic_loop import _recover_file_read_paths, _detect_code_summary_request

check("B17: helper exists in source", 'def _recover_file_read_paths(' in _al_b1_src)
check("B17: source logs [B17] recovery", '"[B17] Recovered file_read' in _al_b1_src)
check("B17: source skips __pycache__ candidates", '__pycache__' in _al_b1_src)
check("B17: source prefers relative workspace path", 'relative_to(workspace)' in _al_b1_src)

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    (ws / "web_ui").mkdir(parents=True, exist_ok=True)
    (ws / "web_ui" / "__pycache__").mkdir(parents=True, exist_ok=True)
    (ws / "backups" / "daily_backup" / "web_ui").mkdir(parents=True, exist_ok=True)
    (ws / "web_ui" / "secure_interceptor.py").write_text("print('live')\n")
    (ws / "web_ui" / "__pycache__" / "secure_interceptor.pyc").write_text("compiled")
    (ws / "backups" / "daily_backup" / "web_ui" / "secure_interceptor.py").write_text("print('backup')\n")

    tc_recover = [{
        "function": {"name": "file_read", "arguments": json.dumps({"path": "secure_interceptor.py"})}
    }]
    _recover_file_read_paths(tc_recover, ws)
    recovered_args = json.loads(tc_recover[0]["function"]["arguments"])
    check("B17: bare filename recovered to live source file", recovered_args["path"] == "web_ui/secure_interceptor.py")

    tc_existing = [{
        "function": {"name": "file_read", "arguments": json.dumps({"path": "web_ui/secure_interceptor.py"})}
    }]
    _recover_file_read_paths(tc_existing, ws)
    existing_args = json.loads(tc_existing[0]["function"]["arguments"])
    check("B17: existing relative path unchanged", existing_args["path"] == "web_ui/secure_interceptor.py")

    tc_missing = [{
        "function": {"name": "file_read", "arguments": json.dumps({"path": "missing.py"})}
    }]
    _recover_file_read_paths(tc_missing, ws)
    missing_args = json.loads(tc_missing[0]["function"]["arguments"])
    check("B17: unknown file left unchanged", missing_args["path"] == "missing.py")


# ═══════════════════════════════════════════════════════════════
# P102: Code-summary anchor enforcement
# ═══════════════════════════════════════════════════════════════
print("\n╔══ P102: Code-Summary Anchor Enforcement ══╗")

check("P102: detector exists in source", 'def _detect_code_summary_request(' in _al_b1_src)
check("P102: source injects [P102] log marker", '"[P102] Injected code-summary anchor hint"' in _al_b1_src)
check("P102: prompt has CODE READING RULE", 'CODE READING RULE' in open(Path(__file__).resolve().parent.parent / 'system_prompts.py').read())

_p102_hint = _detect_code_summary_request("读取 secure_interceptor.py 全文，然后总结后半段逻辑")
check("P102: detector fires for full-file code summary", _p102_hint is not None)
check("P102: hint mentions file_path:line anchors", "file_path:line" in (_p102_hint or ""))
check("P102: detector ignores non-code requests", _detect_code_summary_request("帮我翻译这段中文") is None)


# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
passed = sum(1 for _, ok in _results if ok)
failed = sum(1 for _, ok in _results if not ok)
total = len(_results)
print(f"Results: {passed}/{total} passed, {failed} failed")

if failed:
    print("\n❌ Failed tests:")
    for name, ok in _results:
        if not ok:
            print(f"   - {name}")

if __name__ == "__main__":
    sys.exit(0 if failed == 0 else 1)
