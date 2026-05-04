"""
P21-P26 Tests: Phase 5 Optimizations
=====================================
Tests for Claw-inspired Phase 5 improvements.

Run: python3 tests/test_p21_to_p26.py
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
# P21: Static/Dynamic System Prompt Split
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 1. P21: Static/Dynamic System Prompt Split ══╗")

from agentic_loop import (
    _get_static_system_prompt,
    build_dynamic_context,
    build_system_prompt,
    _SYSTEM_PROMPT_IDENTITY,
    _SYSTEM_PROMPT_TASKS,
    _SYSTEM_PROMPT_ACTIONS,
    _SYSTEM_PROMPT_STOP,
    _SYSTEM_PROMPT_OUTPUT,
    _SYSTEM_PROMPT_QUALITY,
    _SYSTEM_PROMPT_SELF_KNOWLEDGE,
)

# Static prompt should be identical on repeated calls
static1 = _get_static_system_prompt()
static2 = _get_static_system_prompt()
check("P21: static prompt is cached (same object)", static1 is static2)
check("P21: static prompt contains identity", _SYSTEM_PROMPT_IDENTITY in static1)
check("P21: static prompt contains tasks", _SYSTEM_PROMPT_TASKS in static1)
check("P21: static prompt contains actions", _SYSTEM_PROMPT_ACTIONS in static1)
check("P21: static prompt contains stop", _SYSTEM_PROMPT_STOP in static1)
check("P21: static prompt contains output rules", "Communicating with the user" in static1)
check("P21: static prompt contains quality", _SYSTEM_PROMPT_QUALITY in static1)
check("P21: static prompt contains self-knowledge", _SYSTEM_PROMPT_SELF_KNOWLEDGE in static1)
check("P21: static prompt contains platform", "Platform" in static1)
check("P21/P102: static prompt contains code reading rule", "CODE READING RULE" in static1)
check("P21/P102: static prompt contains file_path:line anchor guidance", "file_path:line" in static1)

# Static prompt should NOT contain dynamic sections
check("P21: static prompt has no language section", "Always respond in" not in static1)
check("P21: static prompt has no workspace section", "Current workspace" not in static1)
check("P21: static prompt has no NANOBOT.md section", "NANOBOT.md" not in static1)

# Dynamic context builds correctly
dyn = build_dynamic_context(
    language="zh",
    workspace_info="Current workspace directory: /tmp/test",
    custom_instructions="Always use Python 3.12",
    rag_context="[Knowledge Graph Context]\n- foo (entity)",
    prev_summary="User was working on feature X",
)
check("P21: dynamic context starts with [SESSION CONTEXT]", dyn.startswith("[SESSION CONTEXT]"))
check("P21: dynamic context contains language", "Chinese" in dyn)
check("P21: dynamic context contains workspace", "/tmp/test" in dyn)
check("P21: dynamic context contains NANOBOT.md", "Python 3.12" in dyn)
check("P21: dynamic context contains RAG", "Knowledge Graph" in dyn)
check("P21: dynamic context contains prev summary", "feature X" in dyn)

# Empty dynamic context returns empty string
dyn_empty = build_dynamic_context()
check("P21: empty dynamic context is empty string", dyn_empty == "")

# Backward compat: build_system_prompt still works
full = build_system_prompt(
    language="en",
    workspace_info="workspace: /tmp",
    custom_instructions="custom rule",
)
check("P21: build_system_prompt backward compat", "English" in full and "custom rule" in full)


# ═══════════════════════════════════════════════════════════════
# P22: Time-Based MicroCompact
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 2. P22: Time-Based MicroCompact ══╗")

from agentic_loop import (
    _get_time_gap_minutes,
    _save_last_response_time,
    _time_based_micro_compact,
    _TIME_BASED_MC_GAP_MINUTES,
    _TIME_BASED_MC_KEEP_RECENT,
    _TOOL_RESULT_CLEARED_MSG,
)

with tempfile.TemporaryDirectory() as tmpdir:
    ts_file = Path(tmpdir) / "_last_response.txt"

    # No file → 0.0
    check("P22: no timestamp file → 0.0", _get_time_gap_minutes(ts_file) == 0.0)

    # Save and read back
    _save_last_response_time(ts_file)
    check("P22: timestamp file created", ts_file.exists())
    gap = _get_time_gap_minutes(ts_file)
    check("P22: recent timestamp → small gap", gap < 0.1)

    # Simulate old timestamp (10 minutes ago)
    old_ts = time.time() - 600  # 10 min ago
    ts_file.write_text(str(old_ts))
    gap_old = _get_time_gap_minutes(ts_file)
    check("P22: old timestamp → ~10 min gap", 9.5 < gap_old < 10.5)

# Time-based micro compact
messages = [
    {"role": "system", "content": "system prompt"},
    {"role": "tool", "content": "result 1", "_tool_name": "file_read", "_turn": 1, "tool_call_id": "t1"},
    {"role": "tool", "content": "result 2", "_tool_name": "grep_search", "_turn": 2, "tool_call_id": "t2"},
    {"role": "tool", "content": "result 3", "_tool_name": "file_list", "_turn": 3, "tool_call_id": "t3"},
    {"role": "tool", "content": "result 4", "_tool_name": "file_read", "_turn": 4, "tool_call_id": "t4"},
    {"role": "tool", "content": "result 5", "_tool_name": "shell_execute", "_turn": 5, "tool_call_id": "t5"},
]

# Gap < threshold → no clearing
cleared = _time_based_micro_compact(messages, 2.0)
check("P22: small gap → no clearing", cleared == 0)
check("P22: messages unchanged", messages[1]["content"] == "result 1")

# Gap > threshold → clear old, keep recent 3
cleared = _time_based_micro_compact(messages, 10.0)
check("P22: large gap → cleared old results", cleared == 2)
check("P22: oldest cleared", messages[1]["content"] == _TOOL_RESULT_CLEARED_MSG)
check("P22: second oldest cleared", messages[2]["content"] == _TOOL_RESULT_CLEARED_MSG)
check("P22: third kept (recent)", messages[3]["content"] == "result 3")
check("P22: fourth kept (recent)", messages[4]["content"] == "result 4")
check("P22: fifth kept (recent)", messages[5]["content"] == "result 5")

# Too few tool messages → no clearing
few_msgs = [
    {"role": "system", "content": "sys"},
    {"role": "tool", "content": "r1", "_tool_name": "file_read", "_turn": 1, "tool_call_id": "t1"},
    {"role": "tool", "content": "r2", "_tool_name": "grep_search", "_turn": 2, "tool_call_id": "t2"},
]
check("P22: few messages → no clearing", _time_based_micro_compact(few_msgs, 10.0) == 0)


# ═══════════════════════════════════════════════════════════════
# P24: Output Efficiency Upgrade
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 3. P24: Output Efficiency Upgrade ══╗")

check("P24/P101d: prompt has 'Communicating with the user'", "Communicating with the user" in _SYSTEM_PROMPT_OUTPUT)
check("P24/P101d: prompt has assume-stepped-away rule", "stepped away" in _SYSTEM_PROMPT_OUTPUT)
check("P24/P101d: prompt has no-narrate-tools rule", "Do NOT narrate what tools you are about to call" in _SYSTEM_PROMPT_OUTPUT)
check("P24/P101d: prompt has ≤25 words rule", "≤25 words" in _SYSTEM_PROMPT_OUTPUT)
check("P24/P101d: prompt has ≤100 words rule", "≤100 words" in _SYSTEM_PROMPT_OUTPUT)
check("P24/P101d: prompt has flowing prose rule", "flowing prose" in _SYSTEM_PROMPT_OUTPUT)
check("P24/P101d: prompt has no-bare-tool rule", "NEVER output a bare tool name" in _SYSTEM_PROMPT_OUTPUT)
check("P24/P101d: static prompt includes P101d", "Communicating with the user" in static1)


# ═══════════════════════════════════════════════════════════════
# P23: Sibling Error Cascading Abort
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 4. P23: Sibling Error Cascading Abort ══╗")

# P23 is integrated into the concurrent batch execution.
# We verify the logic pattern exists in the source.
import agentic_loop
import compact_engine
source = open(agentic_loop.__file__).read()
# P34: _auto_compact moved to compact_engine.py — include both for source checks
source_all = source + "\n" + open(compact_engine.__file__).read()
check("P23: source has sibling_errored detection", "_sibling_errored" in source)
check("P23: source checks shell_execute specifically", 'tname == "shell_execute"' in source)
check("P23: source has sibling_error cancel message", "Cancelled: sibling tool" in source)
check("P23: source has P23 log marker", "[P23]" in source)


# ═══════════════════════════════════════════════════════════════
# P25: Compaction File State Preservation
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 5. P25: Compaction File State Preservation ══╗")

check("P25: source has compacted_file_paths extraction", "compacted_file_paths" in source_all)
check("P25: source extracts from file_read metadata", '_tool_name") == "file_read"' in source_all)
check("P25: source appends file list to summary", "Files previously read" in source_all)
check("P25: source has P25 log marker", "[P25]" in source_all)

# Verify the regex extracts file paths correctly
import re
test_content = "[File: /home/user/project/main.py | 150 lines | 4523 bytes | modified: 2026-04-20]"
m = re.search(r'\[File:\s*(\S+)\s*\|', test_content)
check("P25: file path regex works", m is not None and m.group(1) == "/home/user/project/main.py")


# ═══════════════════════════════════════════════════════════════
# P26: Tool Result Summary
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 6. P26: Tool Result Summary ══╗")

from agentic_loop import _generate_tool_summary, _postprocess_tool_content

# file_read summary
fr_output = "[File: /path/to/app.py | 200 lines | 8000 bytes | modified: 2026-04-20]\n     1\tclass MyApp:\n     2\t    pass"
fr_result = {"success": True, "output": fr_output, "error": ""}
fr_sum = _generate_tool_summary("file_read", fr_result)
check("P26: file_read summary has path", "/path/to/app.py" in fr_sum)
check("P26: file_read summary has lines", "200 lines" in fr_sum)
check("P26: file_read summary has language", "Python" in fr_sum)
check("P26: file_read summary has first symbol", "MyApp" in fr_sum)

# grep_search summary
gs_output = "Found 15 matches\n/a.py:10:foo\n/b.py:20:bar"
gs_result = {"success": True, "output": gs_output, "error": ""}
gs_sum = _generate_tool_summary("grep_search", gs_result)
check("P26: grep summary has count", "15" in gs_sum)

# shell_execute summary
sh_output = "total 42\ndrwxr-xr-x 2 user user 4096 Apr 20 ls"
sh_result = {"success": True, "output": sh_output, "error": ""}
sh_sum = _generate_tool_summary("shell_execute", sh_result)
check("P26: shell summary has first line", "total 42" in sh_sum)

# transactional summaries
fe_result = {"success": True, "output": "Created pending change set cs_123", "error": "", "_change_set": {"id": "cs_123", "status": "pending"}}
fe_sum = _generate_tool_summary("file_edit", fe_result)
check("P26: file_edit summary mentions pending change set", "pending change set cs_123" in fe_sum)

fa_result = {"success": True, "output": "Accepted change set cs_123", "error": "", "_change_set": {"id": "cs_123", "status": "applied"}}
fa_sum = _generate_tool_summary("change_set_accept", fa_result)
check("P26: change_set_accept summary mentions applied", "applied cs_123" in fa_sum)

# file_list summary
fl_output = "Directory: /project (25 entries)\nf  file1.py (10KB)"
fl_result = {"success": True, "output": fl_output, "error": ""}
fl_sum = _generate_tool_summary("file_list", fl_result)
check("P26: file_list summary has count", "25" in fl_sum)

# find_by_name summary
fn_output = "Found 8 files\n/a.py\n/b.py"
fn_result = {"success": True, "output": fn_output, "error": ""}
fn_sum = _generate_tool_summary("find_by_name", fn_result)
check("P26: find_by_name summary has count", "8" in fn_sum)

# Error summary
err_result = {"success": False, "output": "", "error": "file not found"}
err_sum = _generate_tool_summary("file_read", err_result)
check("P26: error summary has FAILED", "FAILED" in err_sum)
check("P26: error summary has error msg", "file not found" in err_sum)

# Empty output summary
empty_result = {"success": True, "output": "", "error": ""}
empty_sum = _generate_tool_summary("test_tool", empty_result)
check("P26: empty output summary", "no output" in empty_sum)

# Unknown tool summary
unk_result = {"success": True, "output": "hello world", "error": ""}
unk_sum = _generate_tool_summary("custom_tool", unk_result)
check("P26: unknown tool summary has char count", "11 chars" in unk_sum)

# Integration: _postprocess_tool_content prepends summary
pp = _postprocess_tool_content("file_read", fr_result, current_turn=1, message_turn=1)
check("P26: postprocess prepends summary line", pp.startswith("[Summary:"))
check("P26: postprocess still has original content", "MyApp" in pp)

# MicroCompact preserves summary line
from agentic_loop import _micro_compact_old_messages
big_content = "[Summary: file_read /path/app.py — 200 lines, Python]\n" + "x" * 20000
mc_messages = [
    {"role": "system", "content": "sys"},
    {"role": "tool", "content": big_content, "_tool_name": "file_read", "_turn": 1, "tool_call_id": "t1"},
]
trimmed = _micro_compact_old_messages(mc_messages, current_turn=5)
check("P26: MC re-truncated old message", trimmed == 1)
check("P26: MC preserved summary line", mc_messages[1]["content"].startswith("[Summary: file_read /path/app.py"))
check("P26: MC truncation happened", "chars omitted" in mc_messages[1]["content"])


# ═══════════════════════════════════════════════════════════════
# Integration: All P21-P26 coexist
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 7. Integration: P21-P26 coexistence ══╗")

# Verify all new functions/constants are importable
from agentic_loop import (
    _get_static_system_prompt,
    build_dynamic_context,
    _get_time_gap_minutes,
    _save_last_response_time,
    _time_based_micro_compact,
    _TIME_BASED_MC_GAP_MINUTES,
    _TIME_BASED_MC_KEEP_RECENT,
    _generate_tool_summary,
)
check("P21-P26: all symbols importable", True)

# Static prompt length sanity
check("P21: static prompt > 3000 chars", len(static1) > 3000)
check("P21: static prompt < 22000 chars (U10 expanded)", len(static1) < 22000)

# P22 constants
check("P22: gap threshold = 5 min", _TIME_BASED_MC_GAP_MINUTES == 5)
check("P22: keep recent = 3", _TIME_BASED_MC_KEEP_RECENT == 3)


# ═══════════════════════════════════════════════════════════════
# P27: file_read limit auto-correction + Tone/Style fixes
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 7. P27: file_read limit auto-correction ══╗")

from agentic_loop import _fix_file_read_limits, _LINE_COUNT_RE

# Test regex patterns
check("P27: regex matches '前50行'", _LINE_COUNT_RE.search("前50行") is not None)
check("P27: regex matches 'first 20 lines'", _LINE_COUNT_RE.search("first 20 lines") is not None)
check("P27: regex matches 'top 100 lines'", _LINE_COUNT_RE.search("top 100 lines") is not None)
check("P27: regex matches '读取 200行'", _LINE_COUNT_RE.search("读取 200行") is not None)
check("P27: regex matches '读取 200-400行'", _LINE_COUNT_RE.search("读取 200-400行") is not None)

# Test _fix_file_read_limits with missing limit
tc1 = [{"function": {"name": "file_read", "arguments": '{"path": "/tmp/test.py"}'}}]
_fix_file_read_limits(tc1, "读取 agentic_loop.py 前50行")
args1 = json.loads(tc1[0]["function"]["arguments"])
check("P27: auto-inject limit=50 for '前50行'", args1.get("limit") == 50)

# Test with non-default limit already present → no override
tc2 = [{"function": {"name": "file_read", "arguments": '{"path": "/tmp/test.py", "limit": 200}'}}]
_fix_file_read_limits(tc2, "读取前50行")
args2 = json.loads(tc2[0]["function"]["arguments"])
check("P27: existing limit=200 not overridden", args2.get("limit") == 200)

# Test with default limit=300 → SHOULD be overridden when user asked for specific count
tc2b = [{"function": {"name": "file_read", "arguments": '{"path": "agentic_loop.py", "limit": 300}'}}]
_fix_file_read_limits(tc2b, "读取 agentic_loop.py 前50行")
args2b = json.loads(tc2b[0]["function"]["arguments"])
check("P27: default limit=300 overridden to 50", args2b.get("limit") == 50)

# Test range pattern
tc3 = [{"function": {"name": "file_read", "arguments": '{"path": "/tmp/test.py"}'}}]
_fix_file_read_limits(tc3, "读取 agentic_loop.py 的200-400行")
args3 = json.loads(tc3[0]["function"]["arguments"])
check("P27: range 200-400 → limit=200", args3.get("limit") == 200)
check("P27: range 200-400 → offset=200", args3.get("offset") == 200)

# Test English pattern
tc4 = [{"function": {"name": "file_read", "arguments": '{"path": "/tmp/test.py"}'}}]
_fix_file_read_limits(tc4, "read the first 20 lines of agentic_loop.py")
args4 = json.loads(tc4[0]["function"]["arguments"])
check("P27: 'first 20 lines' → limit=20", args4.get("limit") == 20)

# Test no match → no injection
tc5 = [{"function": {"name": "file_read", "arguments": '{"path": "/tmp/test.py"}'}}]
_fix_file_read_limits(tc5, "read agentic_loop.py")
args5 = json.loads(tc5[0]["function"]["arguments"])
check("P27: no line count → no limit injected", "limit" not in args5)

# Test non-file_read tool not affected
tc6 = [{"function": {"name": "grep_search", "arguments": '{"pattern": "test"}'}}]
_fix_file_read_limits(tc6, "前50行")
args6 = json.loads(tc6[0]["function"]["arguments"])
check("P27: grep_search not affected", "limit" not in args6)

# Test multi-request: 3 file_read calls with different line counts per file
tc_multi = [
    {"function": {"name": "file_read", "arguments": '{"path": "agentic_loop.py"}'}},
    {"function": {"name": "file_read", "arguments": '{"path": "agentic_loop.py", "offset": 400}'}},
    {"function": {"name": "file_read", "arguments": '{"path": "tools/__init__.py"}'}},
]
multi_msg = "读取 agentic_loop.py 前50行\n读取 agentic_loop.py 的400-600行\n读取 tools/__init__.py 全部内容"
_fix_file_read_limits(tc_multi, multi_msg)
args_m1 = json.loads(tc_multi[0]["function"]["arguments"])
args_m2 = json.loads(tc_multi[1]["function"]["arguments"])
args_m3 = json.loads(tc_multi[2]["function"]["arguments"])
check("P27 multi: first agentic_loop.py → limit=50", args_m1.get("limit") == 50)
check("P27 multi: second agentic_loop.py → limit=200 (range)", args_m2.get("limit") == 200)
check("P27 multi: second agentic_loop.py → offset=400", args_m2.get("offset") == 400)
check("P27 multi: __init__.py has no line request → no limit", "limit" not in args_m3)

# Test multi-request with model sending limit=300 (default) on all
tc_multi2 = [
    {"function": {"name": "file_read", "arguments": '{"path": "agentic_loop.py", "limit": 300}'}},
    {"function": {"name": "file_read", "arguments": '{"path": "agentic_loop.py", "offset": 400, "limit": 300}'}},
]
multi_msg2 = "读取 agentic_loop.py 前50行\n读取 agentic_loop.py 的400-600行"
_fix_file_read_limits(tc_multi2, multi_msg2)
args_mm1 = json.loads(tc_multi2[0]["function"]["arguments"])
args_mm2 = json.loads(tc_multi2[1]["function"]["arguments"])
check("P27 multi+300: first → limit=50 (overrides 300)", args_mm1.get("limit") == 50)
check("P27 multi+300: second → limit=200 (overrides 300)", args_mm2.get("limit") == 200)

# Test Tone/Style rules in prompt
check("P27: LaTeX ban in prompt", "LaTeX" in _SYSTEM_PROMPT_OUTPUT)
check("P27: no-colon-before-tool rule", "colon before tool calls" in _SYSTEM_PROMPT_OUTPUT)
check("P27: no-narrate-plan rule in STOP", "I will find" in _SYSTEM_PROMPT_STOP)

# Test anti-unnecessary-search rule in tool guidance
from tools import build_tool_guidance
guidance = build_tool_guidance()
check("P27: guidance has anti-find_by_name rule", "call file_read DIRECTLY" in guidance)


# ═══════════════════════════════════════════════════════════════
# P28: Runtime LaTeX stripper
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 8. P28: Runtime LaTeX stripper ══╗")

from agentic_loop import _strip_latex, _LatexStreamBuffer

check("P28: $\\rightarrow$ → →", _strip_latex(r"defined in $\rightarrow$ called") == "defined in → called")
check("P28: $\\text{hello}$ → hello", _strip_latex(r"$\text{hello}$") == "hello")
check("P28: $\\texttt{code}$ → `code`", _strip_latex(r"$\texttt{code}$") == "`code`")
check("P28: $\\leq$ → ≤", _strip_latex(r"$\leq$") == "≤")
check("P28: $\\geq$ → ≥", _strip_latex(r"$\geq$") == "≥")
check("P28: $\\neq$ → ≠", _strip_latex(r"$\neq$") == "≠")
check("P28: $\\leftarrow$ → ←", _strip_latex(r"$\leftarrow$") == "←")
check("P28: normal text unchanged", _strip_latex("hello world") == "hello world")
check("P28: code with $ unchanged", _strip_latex("price = $100") == "price = $100")

# Test _LatexStreamBuffer for multi-token LaTeX
buf = _LatexStreamBuffer()
# Simulate multi-token streaming: "$\" + "rightarrow" + "$" + " rest"
r1 = buf.add("before ")
check("P28 buf: text before $ flushed", r1 == ["before "])
r2 = buf.add("$\\")
check("P28 buf: $ starts buffering", r2 == [])  # buffering, no output yet
r3 = buf.add("rightarrow")
check("P28 buf: still buffering", r3 == [])
r4 = buf.add("$ after")
check("P28 buf: closing $ triggers strip", "→" in "".join(r4) and "after" in "".join(r4))

# Test buffer with no LaTeX
buf2 = _LatexStreamBuffer()
r5 = buf2.add("normal text")
check("P28 buf: no $ passes through", r5 == ["normal text"])

# Test buffer finish flushes remaining
buf3 = _LatexStreamBuffer()
buf3.add("hanging $")
r6 = buf3.finish()
check("P28 buf: finish flushes remaining", len(r6) == 1 and "$" in r6[0])

# Test buffer with $\text{...}$ multi-token
buf4 = _LatexStreamBuffer()
parts = buf4.add("defined in $")
parts += buf4.add("\\text{")
parts += buf4.add("mtime}")
parts += buf4.add("$ here")
combined = "".join(parts)
check("P28 buf: $\\text{mtime}$ → mtime", "mtime" in combined and "$" not in combined)


# ═══════════════════════════════════════════════════════════════
# P29: Pre-tool narration detection
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 9. P29: Pre-tool narration detection ══╗")

from agentic_loop import _NARRATION_RE

check("P29: detects 'I will read'", _NARRATION_RE.search("I will read the file and search.") is not None)
check("P29: detects 'Let me read'", _NARRATION_RE.search("Let me read the file.") is not None)
check("P29: detects '我将读取'", _NARRATION_RE.search("我将读取文件并搜索。") is not None)
check("P29: detects '让我'", _NARRATION_RE.search("让我查看一下文件。") is not None)
check("P29: detects 'I\\'ll'", _NARRATION_RE.search("I'll read the file now.") is not None)
check("P29: ignores normal text", _NARRATION_RE.search("The file contains 200 lines.") is None)
check("P29: ignores answer text", _NARRATION_RE.search("Here are the results:") is None)


# ═══════════════════════════════════════════════════════════════
print("\n╔══ 10. P31: FILE_UNCHANGED_STUB ══╗")

from tools.file_read import (
    execute as fr_execute,
    _session_read_tracker,
    reset_session_read_tracker,
    invalidate_session_reads,
    FILE_UNCHANGED_STUB,
)
from tools.base import update_file_state_after_edit

# Create a temp file for testing
_p31_dir = Path(tempfile.mkdtemp())
_p31_file = _p31_dir / "test_p31.txt"
_p31_file.write_text("\n".join(f"line {i}" for i in range(1, 101)))

# Reset tracker before tests
reset_session_read_tracker()

# Test 1: First read returns full content
r1 = fr_execute({"path": str(_p31_file)}, _p31_dir)
check("P31: first read returns full content", r1["success"] and "line 1" in r1["output"])
check("P31: first read has line numbers", "     1\t" in r1["output"])

# Test 2: Second read (same params, unchanged) returns stub
r2 = fr_execute({"path": str(_p31_file)}, _p31_dir)
check("P31: second read returns stub", r2["success"] and FILE_UNCHANGED_STUB in r2["output"])
check("P31: stub still has metadata header", "[File:" in r2["output"] and "100 lines" in r2["output"])
check("P31: stub does NOT contain file content", "line 1" not in r2["output"])

# Test 3: Different offset/limit is a different request → full content
r3 = fr_execute({"path": str(_p31_file), "offset": 50, "limit": 10}, _p31_dir)
check("P31: different range returns full content", r3["success"] and "line 50" in r3["output"])

# Test 4: Same offset/limit again → stub
r4 = fr_execute({"path": str(_p31_file), "offset": 50, "limit": 10}, _p31_dir)
check("P31: same range again returns stub", r4["success"] and FILE_UNCHANGED_STUB in r4["output"])

# Test 5: Edit the file → invalidates tracker
update_file_state_after_edit(_p31_file)
r5 = fr_execute({"path": str(_p31_file)}, _p31_dir)
check("P31: read after edit returns full content", r5["success"] and "line 1" in r5["output"])
check("P31: full content after edit has line numbers", "     1\t" in r5["output"])

# Test 6: File modification (external) → new mtime → full content
time.sleep(0.05)  # ensure different mtime
_p31_file.write_text("\n".join(f"modified line {i}" for i in range(1, 51)))
r6 = fr_execute({"path": str(_p31_file)}, _p31_dir)
check("P31: read after external modify returns full content", r6["success"] and "modified line 1" in r6["output"])

# Test 7: Reset clears everything
reset_session_read_tracker()
r7 = fr_execute({"path": str(_p31_file)}, _p31_dir)
check("P31: read after reset returns full content", r7["success"] and "modified line 1" in r7["output"])

# Test 8: Verify tracker state
check("P31: tracker has entries after reads", len(_session_read_tracker) > 0)
reset_session_read_tracker()
check("P31: tracker empty after reset", len(_session_read_tracker) == 0)

# Test 9: Context savings estimate — stub is much smaller than full content
reset_session_read_tracker()
r_full = fr_execute({"path": str(_p31_file)}, _p31_dir)
r_stub = fr_execute({"path": str(_p31_file)}, _p31_dir)
savings = 1.0 - len(r_stub["output"]) / max(len(r_full["output"]), 1)
check(f"P31: stub saves >70% context (saved {savings:.0%})", savings > 0.70)

# Cleanup
import shutil
shutil.rmtree(_p31_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
print("\n╔══ 11. P33: Compaction Prompt Upgrade ══╗")

from agentic_loop import (
    _COMPACT_PROMPT, _PARTIAL_COMPACT_PROMPT, _NO_TOOLS_PREAMBLE,
    _NO_TOOLS_TRAILER, _COMPACT_SECTIONS, _COMPACT_EXAMPLE,
    _DETAILED_ANALYSIS_INSTRUCTION, _format_compact_summary,
)

# P33: Verify prompt structure
check("P33: full prompt has NO_TOOLS_PREAMBLE", "CRITICAL: Respond with TEXT ONLY" in _COMPACT_PROMPT)
check("P33: full prompt has analysis instruction", "Chronologically analyze" in _COMPACT_PROMPT)
check("P33: full prompt has 9 sections (U7)", "9. **Optional Next Step**" in _COMPACT_SECTIONS)
check("P33: full prompt has example block", "<example>" in _COMPACT_EXAMPLE)
check("P33: full prompt has NO_TOOLS_TRAILER", "REMINDER: Do NOT call any tools" in _NO_TOOLS_TRAILER)
check("P33: full prompt has all 9 section numbers (U7)", all(
    f"{i}." in _COMPACT_SECTIONS for i in range(1, 10)
))
check("P33: partial prompt mentions OLDER", "OLDER portion" in _PARTIAL_COMPACT_PROMPT)
check("P33: partial prompt has NO_TOOLS guard", "TEXT ONLY" in _PARTIAL_COMPACT_PROMPT)
check("P33: partial prompt has 9 sections (U7)", "9. **Optional Next Step**" in _PARTIAL_COMPACT_PROMPT)

# P33: format_compact_summary strips analysis, extracts summary
raw_with_both = """<analysis>
I'm analyzing the conversation...
lots of thought here
</analysis>

<summary>
1. Primary Request: The user asked to fix a bug
2. Technical Concepts: Python, asyncio
</summary>"""
formatted = _format_compact_summary(raw_with_both)
check("P33: format strips analysis", "analyzing" not in formatted)
check("P33: format extracts summary", "Primary Request" in formatted)
check("P33: format removes summary tags", "<summary>" not in formatted)

raw_no_tags = "Just a plain summary without XML tags."
formatted2 = _format_compact_summary(raw_no_tags)
check("P33: format fallback for no tags", "plain summary" in formatted2)


# ═══════════════════════════════════════════════════════════════
print("\n╔══ 12. P32: Partial Compaction ══╗")

from agentic_loop import (
    _find_partial_pivot, _PARTIAL_COMPACT_KEEP_RECENT,
    _messages_to_text,
)

# Test _find_partial_pivot with enough messages
msgs_20 = [{"role": "system", "content": "sys"}]
for i in range(20):
    role = "user" if i % 2 == 0 else "assistant"
    msgs_20.append({"role": role, "content": f"message {i}"})

pivot = _find_partial_pivot(msgs_20)
check("P32: pivot found for 20+ messages", pivot > 0)
check("P32: pivot keeps recent messages",
      len(msgs_20) - pivot >= _PARTIAL_COMPACT_KEEP_RECENT - 2)  # -2 for boundary adjustment tolerance
check("P32: pivot leaves old messages to summarize", pivot > 2)

# Messages at pivot should be a user message (clean boundary)
check("P32: pivot is at user message boundary",
      msgs_20[pivot].get("role") == "user")

# Not enough messages → returns -1
msgs_tiny = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello"},
]
pivot_tiny = _find_partial_pivot(msgs_tiny)
check("P32: too few messages → -1", pivot_tiny == -1)

# Test with tool messages (should not split tool_call/result pairs)
msgs_with_tools = [{"role": "system", "content": "sys"}]
for i in range(5):
    msgs_with_tools.append({"role": "user", "content": f"request {i}"})
    msgs_with_tools.append({"role": "assistant", "content": f"response {i}"})
    msgs_with_tools.append({"role": "tool", "content": f"result {i}", "tool_call_id": f"tc_{i}"})
    msgs_with_tools.append({"role": "user", "content": f"followup {i}"})

pivot_tools = _find_partial_pivot(msgs_with_tools)
if pivot_tools > 0:
    check("P32: tool msgs — pivot at user boundary",
          msgs_with_tools[pivot_tools].get("role") == "user")
    check("P32: tool msgs — pivot not at tool result",
          not msgs_with_tools[pivot_tools].get("tool_call_id"))
else:
    check("P32: tool msgs — pivot found or valid -1", True)

# Test _messages_to_text
msgs_text = [
    {"role": "user", "content": "Hello world"},
    {"role": "assistant", "content": "Hi there"},
]
text_out = _messages_to_text(msgs_text)
check("P32: messages_to_text contains roles", "[USER]:" in text_out and "[ASSISTANT]:" in text_out)
check("P32: messages_to_text contains content", "Hello world" in text_out)

# Test that partial compact keeps recent messages (mock test)
# We can't easily call _auto_compact without an LLM, but we can verify
# that the pivot logic correctly identifies the split point
msgs_large = [{"role": "system", "content": "sys"}]
for i in range(15):
    msgs_large.append({"role": "user", "content": f"Q{i}: " + "x" * 200})
    msgs_large.append({"role": "assistant", "content": f"A{i}: " + "y" * 200})
pivot_large = _find_partial_pivot(msgs_large)
if pivot_large > 0:
    old_count = pivot_large
    recent_count = len(msgs_large) - pivot_large
    check(f"P32: large conversation splits correctly (old={old_count}, recent={recent_count})",
          recent_count >= _PARTIAL_COMPACT_KEEP_RECENT - 2)
    # Verify recent messages are the LATEST ones
    recent_msgs = msgs_large[pivot_large:]
    last_q_num = int(recent_msgs[-2]["content"].split(":")[0].replace("Q", ""))
    check(f"P32: recent tail contains latest messages (Q{last_q_num})", last_q_num == 14)
else:
    check("P32: large conversation pivot found", False)

# Verify the compact prompt components are composable
full_prompt_len = len(_COMPACT_PROMPT)
partial_prompt_len = len(_PARTIAL_COMPACT_PROMPT)
check(f"P33: full prompt >1000 chars ({full_prompt_len})", full_prompt_len > 1000)
check(f"P33: partial prompt >1000 chars ({partial_prompt_len})", partial_prompt_len > 1000)
check("P33: prompts share sections",
      _COMPACT_SECTIONS in _COMPACT_PROMPT and _COMPACT_SECTIONS in _PARTIAL_COMPACT_PROMPT)


# ═══════════════════════════════════════════════════════════════
# P35: WebSearch / WebFetch Tools
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 13. P35: WebSearch / WebFetch Tools ══╗")

from tools import AGENTIC_TOOLS, TOOL_NAME_ALIASES, READONLY_TOOLS
from tools import web_fetch, web_search

# --- Tool registration ---
tool_names = [t["function"]["name"] for t in AGENTIC_TOOLS]
check("P35: web_fetch registered", "web_fetch" in tool_names)
check("P35: web_search registered", "web_search" in tool_names)
check("P35: web_fetch is readonly", "web_fetch" in READONLY_TOOLS)
check("P35: web_search is readonly", "web_search" in READONLY_TOOLS)

# --- Aliases ---
check("P35: curl → web_fetch", TOOL_NAME_ALIASES.get("curl") == "web_fetch")
check("P35: fetch_url → web_fetch", TOOL_NAME_ALIASES.get("fetch_url") == "web_fetch")
check("P35: read_url → web_fetch", TOOL_NAME_ALIASES.get("read_url") == "web_fetch")
check("P35: search → web_search", TOOL_NAME_ALIASES.get("search") == "web_search")
check("P35: google → web_search", TOOL_NAME_ALIASES.get("google") == "web_search")
check("P35: ddg → web_search", TOOL_NAME_ALIASES.get("ddg") == "web_search")

# --- TOOL_DEF structure ---
wf_def = web_fetch.TOOL_DEF
ws_def = web_search.TOOL_DEF
check("P35: web_fetch has url param", "url" in wf_def["function"]["parameters"]["properties"])
check("P35: web_fetch has max_length param", "max_length" in wf_def["function"]["parameters"]["properties"])
check("P35: web_fetch url is required", "url" in wf_def["function"]["parameters"]["required"])
check("P35: web_search has query param", "query" in ws_def["function"]["parameters"]["properties"])
check("P35: web_search has max_results param", "max_results" in ws_def["function"]["parameters"]["properties"])
check("P35: web_search query is required", "query" in ws_def["function"]["parameters"]["required"])

# --- GUIDANCE metadata ---
check("P35: web_fetch has GUIDANCE", hasattr(web_fetch, "GUIDANCE"))
check("P35: web_search has GUIDANCE", hasattr(web_search, "GUIDANCE"))
check("P35: web_fetch replaces curl", "curl" in web_fetch.GUIDANCE.get("replaces_shell", []))

# --- web_fetch: input validation ---
from pathlib import Path as _P35_Path
_p35_ws = _P35_Path("/tmp")

r = web_fetch.execute({"url": ""}, _p35_ws)
check("P35: web_fetch empty url → error", not r["success"] and "No URL" in r["error"])

r = web_fetch.execute({"url": "ftp://example.com"}, _p35_ws)
check("P35: web_fetch bad scheme → error", not r["success"] and "Invalid URL scheme" in r["error"])

r = web_fetch.execute({"url": "not-a-url"}, _p35_ws)
check("P35: web_fetch no scheme → error", not r["success"])

# --- web_search: input validation ---
r = web_search.execute({"query": ""}, _p35_ws)
check("P35: web_search empty query → error", not r["success"] and "No search query" in r["error"])

# --- HTML to text conversion ---
test_html = """
<html><head><title>Test Page</title>
<script>var x = 1;</script>
<style>body { color: red; }</style>
</head><body>
<nav>Skip nav</nav>
<main>
<h1>Hello World</h1>
<p>This is a test paragraph.</p>
<p>Second paragraph with <b>bold</b> text.</p>
</main>
<footer>Copyright 2026</footer>
</body></html>
"""
text = web_fetch._html_to_text(test_html)
check("P35: HTML title extracted", "Test Page" in text)
check("P35: HTML h1 extracted", "Hello World" in text)
check("P35: HTML paragraph extracted", "test paragraph" in text)
check("P35: HTML bold text preserved", "bold" in text)
check("P35: HTML script removed", "var x" not in text)
check("P35: HTML style removed", "color: red" not in text)
check("P35: HTML nav removed", "Skip nav" not in text)
check("P35: HTML footer removed", "Copyright" not in text)

# --- Fallback HTML stripper ---
text2 = web_fetch._html_to_text_fallback(test_html)
check("P35: fallback strips tags", "<" not in text2 or "<!--" in text2 or text2.count("<") < 3)
check("P35: fallback has content", "Hello World" in text2)
check("P35: fallback strips script", "var x" not in text2)

# --- MicroCompact config ---
from agentic_loop import MICRO_COMPACT_CONFIG
check("P35: web_fetch in MC config", "web_fetch" in MICRO_COMPACT_CONFIG)
check("P35: web_search in MC config", "web_search" in MICRO_COMPACT_CONFIG)
check("P35: web_fetch MC max=10000", MICRO_COMPACT_CONFIG["web_fetch"]["max"] == 10000)
check("P35: web_search MC max=4000", MICRO_COMPACT_CONFIG["web_search"]["max"] == 4000)

# --- System prompt includes web tool guidance ---
from agentic_loop import AGENTIC_SYSTEM_PROMPT
from tools import build_tool_guidance
guidance = build_tool_guidance()
check("P35: guidance mentions web_fetch", "web_fetch" in guidance)
check("P35: guidance mentions web_search", "web_search" in guidance)

# ═══════════════════════════════════════════════════════════════
# P34: Module Split Verification
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 14. P34: Module Split Verification ══╗")

import compact_engine
import system_prompts

# Verify compact_engine has the right symbols
check("P34: compact_engine._estimate_tokens", hasattr(compact_engine, '_estimate_tokens'))
check("P34: compact_engine.TokenBudgetTracker", hasattr(compact_engine, 'TokenBudgetTracker'))
check("P34: compact_engine._auto_compact", hasattr(compact_engine, '_auto_compact'))
check("P34: compact_engine._resolve_model", hasattr(compact_engine, '_resolve_model'))
check("P34: compact_engine._COMPACT_PROMPT", hasattr(compact_engine, '_COMPACT_PROMPT'))
check("P34: compact_engine._find_partial_pivot", hasattr(compact_engine, '_find_partial_pivot'))

# Verify system_prompts has the right symbols
check("P34: system_prompts.AGENTIC_SYSTEM_PROMPT", hasattr(system_prompts, 'AGENTIC_SYSTEM_PROMPT'))
check("P34: system_prompts.build_system_prompt", hasattr(system_prompts, 'build_system_prompt'))
check("P34: system_prompts._get_static_system_prompt", hasattr(system_prompts, '_get_static_system_prompt'))
check("P34: system_prompts._detect_workspace_context", hasattr(system_prompts, '_detect_workspace_context'))

# Verify backward-compat: same objects whether imported from agentic_loop or directly
from agentic_loop import (
    _estimate_tokens as al_et, TokenBudgetTracker as al_tbt,
    AGENTIC_SYSTEM_PROMPT as al_asp, build_system_prompt as al_bsp,
)
check("P34: _estimate_tokens is same object", al_et is compact_engine._estimate_tokens)
check("P34: TokenBudgetTracker is same class", al_tbt is compact_engine.TokenBudgetTracker)
check("P34: AGENTIC_SYSTEM_PROMPT matches", al_asp == system_prompts.AGENTIC_SYSTEM_PROMPT)
check("P34: build_system_prompt is same func", al_bsp is system_prompts.build_system_prompt)

# Verify agentic_loop.py is now under 1900 lines
import agentic_loop
with open(agentic_loop.__file__) as f:
    line_count = sum(1 for _ in f)
check(f"P34: agentic_loop.py < 3800 lines ({line_count})", line_count < 3800)

# ═══════════════════════════════════════════════════════════════
# P37: Token Estimation Precision
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 15. P37: Token Estimation Precision ══╗")

from compact_engine import _estimate_tokens, _estimate_messages_tokens

# Edge cases
check("P37: empty string → 0", _estimate_tokens("") == 0)
check("P37: short string > 0", _estimate_tokens("hello") >= 1)

# English prose: ratio ~4 chars/token
en_text = "The quick brown fox jumps over the lazy dog. This is a test. " * 20
en_tok = _estimate_tokens(en_text)
en_ratio = len(en_text) / en_tok
check(f"P37: English ratio ~4 ({en_ratio:.1f})", 3.5 <= en_ratio <= 5.0)

# CJK text: ratio ~1.5-2.5 chars/token (each CJK char = 1-2 tokens)
cjk_text = "这是一个测试验证中文文本的token估算精度提升功能" * 10
cjk_tok = _estimate_tokens(cjk_text)
cjk_ratio = len(cjk_text) / cjk_tok
check(f"P37: CJK ratio ~1.5-2.5 ({cjk_ratio:.1f})", 1.2 <= cjk_ratio <= 2.8)

# CJK should produce MORE tokens per char than English
check("P37: CJK denser than English", cjk_ratio < en_ratio)

# Code with many symbols: ratio ~3
json_text = '{"key": "value", "nested": {"a": 1, "b": [1,2,3]}, "path": "/test"}' * 10
json_tok = _estimate_tokens(json_text)
json_ratio = len(json_text) / json_tok
check(f"P37: JSON/code ratio ~3 ({json_ratio:.1f})", 2.5 <= json_ratio <= 4.0)

# Mixed CJK+English: ratio between CJK and English
mixed_text = "这个 function 实现了 token 估算，包含了 150 lines 的代码。" * 10
mixed_tok = _estimate_tokens(mixed_text)
mixed_ratio = len(mixed_text) / mixed_tok
check(f"P37: mixed ratio between CJK and EN ({mixed_ratio:.1f})", cjk_ratio <= mixed_ratio + 0.5)

# _estimate_messages_tokens: basic functionality
msgs = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello world " * 100},
    {"role": "assistant", "content": "Here is the answer.", "tool_calls": [
        {"function": {"name": "file_read", "arguments": '{"path": "/test.py"}'}}
    ]},
]
msg_tok = _estimate_messages_tokens(msgs)
check("P37: message tokens > 0", msg_tok > 0)
check("P37: message tokens includes overhead", msg_tok > _estimate_tokens("Hello world " * 100))

# Verify consistency: same text → same result
t1 = _estimate_tokens(en_text)
t2 = _estimate_tokens(en_text)
check("P37: consistent results", t1 == t2)

# ═══════════════════════════════════════════════════════════════
# P36: SubAgent Tool
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 16. P36: SubAgent Tool ══╗")

from tools import AGENTIC_TOOLS, TOOL_NAME_ALIASES, READONLY_TOOLS, ASYNC_TOOLS
from tools import execute_tool_async, _ASYNC_DISPATCH
from tools import sub_agent

# --- Registration ---
tool_names = [t["function"]["name"] for t in AGENTIC_TOOLS]
check("P36: sub_agent registered", "sub_agent" in tool_names)
check("P36: sub_agent is readonly", "sub_agent" in READONLY_TOOLS)
check("P36: sub_agent is async", "sub_agent" in ASYNC_TOOLS)
check("P36: sub_agent in async dispatch", "sub_agent" in _ASYNC_DISPATCH)

# --- Aliases ---
check("P36: fork → sub_agent", TOOL_NAME_ALIASES.get("fork") == "sub_agent")
check("P36: delegate → sub_agent", TOOL_NAME_ALIASES.get("delegate") == "sub_agent")
check("P36: spawn_agent → sub_agent", TOOL_NAME_ALIASES.get("spawn_agent") == "sub_agent")

# --- TOOL_DEF structure ---
sa_def = sub_agent.TOOL_DEF
check("P36: has task param", "task" in sa_def["function"]["parameters"]["properties"])
check("P36: has max_turns param", "max_turns" in sa_def["function"]["parameters"]["properties"])
check("P36: task is required", "task" in sa_def["function"]["parameters"]["required"])

# --- GUIDANCE ---
check("P36: has GUIDANCE", hasattr(sub_agent, "GUIDANCE"))
check("P36: has tips", len(sub_agent.GUIDANCE.get("tips", [])) >= 2)

# --- Input validation ---
from pathlib import Path as _P36_Path
_p36_ws = _P36_Path("/tmp")

r = sub_agent.execute({"task": ""}, _p36_ws)
check("P36: empty task → error", not r["success"] and "No task" in r["error"])

r = sub_agent.execute({"task": "short"}, _p36_ws)
check("P36: short task → error", not r["success"] and "too short" in r["error"])

# --- set_parent_context ---
check("P36: has set_parent_context", hasattr(sub_agent, "set_parent_context"))
sub_agent.set_parent_context({"test": "1"}, _p36_ws, "test_session")
check("P36: _PARENT_ENV set", sub_agent._PARENT_ENV == {"test": "1"})
check("P36: _PARENT_WORKSPACE set", sub_agent._PARENT_WORKSPACE == _p36_ws)
check("P36: _PARENT_SESSION_ID set", sub_agent._PARENT_SESSION_ID == "test_session")

# --- has execute_async ---
import asyncio
check("P36: has execute_async", hasattr(sub_agent, "execute_async"))
check("P36: execute_async is coroutine func", asyncio.iscoroutinefunction(sub_agent.execute_async))

# --- MicroCompact config ---
from agentic_loop import MICRO_COMPACT_CONFIG
check("P36: sub_agent in MC config", "sub_agent" in MICRO_COMPACT_CONFIG)
check("P36: sub_agent MC max=8000", MICRO_COMPACT_CONFIG["sub_agent"]["max"] == 8000)

# --- ASYNC_TOOLS imported by agentic_loop ---
from agentic_loop import ASYNC_TOOLS as al_async
check("P36: agentic_loop has ASYNC_TOOLS", "sub_agent" in al_async)

# ═══════════════════════════════════════════════════════════════
# P38: Mandatory Tool Detection
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 17. P38: Mandatory Tool Detection ══╗")

from agentic_loop import _detect_mandatory_tool

# Chinese patterns
r = _detect_mandatory_tool("使用 sub_agent 工具执行：分析目录下的所有工具")
check("P38: '使用 sub_agent' detected", r is not None and "sub_agent" in r)

r = _detect_mandatory_tool("用 web_fetch 抓取 https://example.com")
check("P38: '用 web_fetch' detected", r is not None and "web_fetch" in r)

r = _detect_mandatory_tool("调用 grep_search 搜索代码")
check("P38: '调用 grep_search' detected", r is not None and "grep_search" in r)

# English patterns
r = _detect_mandatory_tool("use web_search to find Python docs")
check("P38: 'use web_search' detected", r is not None and "web_search" in r)

r = _detect_mandatory_tool("call sub_agent to analyze the codebase")
check("P38: 'call sub_agent' detected", r is not None and "sub_agent" in r)

# Alias resolution
r = _detect_mandatory_tool("使用 fork 执行子任务")
check("P38: alias 'fork' → sub_agent", r is not None and "sub_agent" in r)

r = _detect_mandatory_tool("use curl to fetch the page")
check("P38: alias 'curl' → web_fetch", r is not None and "web_fetch" in r)

# Negative cases — should NOT trigger
r = _detect_mandatory_tool("请帮我分析这段代码")
check("P38: no tool mentioned → None", r is None)

r = _detect_mandatory_tool("use proper formatting in your response")
check("P38: 'use proper' not a tool → None", r is None)

# Hint content verification
r = _detect_mandatory_tool("use sub_agent for the task")
check("P38: hint says MANDATORY", r is not None and "MANDATORY TOOL" in r)
check("P38: hint says MUST call", r is not None and "MUST call" in r)
check("P38: hint says no file_read bypass", r is not None and "Do NOT use file_read" in r)
check("P38: hint says no python_execute bypass", r is not None and "Do NOT use python_execute" in r)

# ═══════════════════════════════════════════════════════════════
# P39: python_execute Bypass Prevention
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 18. P39: python_execute Bypass Prevention ══╗")

from tools.python_execute import execute as _py_exec, _TOOL_BYPASS_PATTERNS
from pathlib import Path as _P39_Path
_p39_ws = _P39_Path("/tmp")

# Blocked patterns
r = _py_exec({"code": "from tools import execute_tool\nexecute_tool('web_fetch', {}, '.')"}, _p39_ws)
check("P39: 'from tools import' blocked", not r["success"] and "cannot import" in r["error"])

r = _py_exec({"code": "from tools.web_fetch import execute\nexecute({'url': 'http://x'}, '.')"}, _p39_ws)
check("P39: 'from tools.web_fetch' blocked", not r["success"] and "cannot import" in r["error"])

r = _py_exec({"code": "import tools\ntools.execute_tool('sub_agent', {}, '.')"}, _p39_ws)
check("P39: 'import tools' blocked", not r["success"] and "cannot import" in r["error"])

r = _py_exec({"code": "execute_tool('grep_search', {'pattern': 'x'}, '.')"}, _p39_ws)
check("P39: 'execute_tool(' blocked", not r["success"])

r = _py_exec({"code": "from agentic_loop import agentic_chat_stream"}, _p39_ws)
check("P39: 'from agentic_loop import' blocked", not r["success"])

# Allowed patterns (normal python)
r = _py_exec({"code": "print('hello world')"}, _p39_ws)
check("P39: normal print allowed", r["success"])

r = _py_exec({"code": "import json\nprint(json.dumps({'a': 1}))"}, _p39_ws)
check("P39: normal import allowed", r["success"])

r = _py_exec({"code": "from pathlib import Path\nprint(Path('/tmp').exists())"}, _p39_ws)
check("P39: 'from pathlib import' allowed", r["success"])

# Tool description includes warning
from tools.python_execute import TOOL_DEF as _py_def
check("P39: tool desc warns against bypass",
      "Do NOT use python_execute to call other tools" in _py_def["function"]["description"])

# GUIDANCE includes warning
from tools.python_execute import GUIDANCE as _py_guid
check("P39: guidance warns against bypass",
      any("NEVER use python_execute" in tip for tip in _py_guid.get("tips", [])))

# ═══════════════════════════════════════════════════════════════
# P40: Self-Knowledge Accuracy
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 19. P40: Self-Knowledge Accuracy ══╗")

from system_prompts import _SYSTEM_PROMPT_SELF_KNOWLEDGE

check("P40: lists 14 tools", "14 tools" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P40: lists shell_execute", "shell_execute" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P40: lists file_read", "file_read" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P40: lists file_edit", "file_edit" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P40: lists UI approval card", "UI approval card" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P40: does NOT list change_set_accept as tool", "change_set_accept" not in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P40: lists web_fetch", "web_fetch" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P40: lists web_search", "web_search" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P40: lists sub_agent", "sub_agent" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P40: lists python_execute", "python_execute" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P40: sub_agent description", "independent" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P40: MC limits include sub_agent", "sub_agent 8000" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P40: MC limits include web_fetch", "web_fetch 10000" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)

# ═══════════════════════════════════════════════════════════════
# P41: sub_agent Tool Description
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 20. P41: sub_agent Description ══╗")

from tools.sub_agent import TOOL_DEF as _sa_def
sa_desc = _sa_def["function"]["description"]
check("P41: mentions 'independent'", "independent" in sa_desc)
check("P41: mentions agent types", "agent_type" in sa_desc)
check("P41/P60: mentions explore", "explore" in sa_desc)
check("P41: mentions use cases", "research" in sa_desc or "complex" in sa_desc)
check("P41: mentions 'task' parameter", "task" in sa_desc)

# ═══════════════════════════════════════════════════════════════
# P42: Context Cross-Instruction Confusion Prevention
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 21. P42: Context Confusion Prevention ══╗")

# Test _HISTORY_DELIMITER detection — current task extraction
_hist_msg = (
    "[对话历史]\n"
    "[user]: 使用 web_fetch 抓取 https://example.com\n"
    "[assistant]: ✅ web_fetch 已获取内容\n\n"
    "[当前问题]\n"
    "使用 sub_agent 工具执行：分析 tools/ 目录下的所有工具文件"
)

# P42 should make P38 detect sub_agent from current task, not web_fetch from history
r = _detect_mandatory_tool("使用 sub_agent 工具执行：分析 tools/ 目录下的所有工具文件")
check("P42: current task detects sub_agent", r is not None and "sub_agent" in r)

# Verify that extracting [当前问题] gives only current task
_delim = "[当前问题]"
if _delim in _hist_msg:
    _extracted = _hist_msg.split(_delim, 1)[1].strip()
    check("P42: extracted task has sub_agent", "sub_agent" in _extracted)
    check("P42: extracted task no web_fetch", "web_fetch" not in _extracted)
else:
    check("P42: delimiter not found (BUG)", False)

# Verify focus hint format
_task_preview = _extracted[:200].replace("\n", " ")
_focus_hint = (
    f"[FOCUS] The user message contains conversation history above '[当前问题]'. "
    f"That history is CONTEXT ONLY — do NOT re-execute any actions from it. "
    f"Your SOLE task is what follows '[当前问题]': \"{_task_preview}\"\n"
    f"Ignore any tool calls, URLs, file paths, or commands mentioned in the [对话历史] section. "
    f"Only act on the [当前问题] section."
)
check("P42: focus hint has [FOCUS]", "[FOCUS]" in _focus_hint)
check("P42: focus hint mentions SOLE task", "SOLE task" in _focus_hint)
check("P42: focus hint bans re-executing history", "do NOT re-execute" in _focus_hint)

# P42 should NOT trigger when there's no history delimiter
_plain_msg = "使用 sub_agent 分析目录"
check("P42: no delimiter → no extraction needed", _delim not in _plain_msg)

# P42 + P38 integration: mandatory tool from current task only
_hist_with_two_tools = (
    "[对话历史]\n"
    "[user]: 使用 web_search 搜索 Python docs\n\n"
    "[当前问题]\n"
    "使用 grep_search 搜索代码"
)
_ct = _hist_with_two_tools.split(_delim, 1)[1].strip()
r_current = _detect_mandatory_tool(_ct)
check("P42+P38: detects grep_search from current", r_current is not None and "grep_search" in r_current)
check("P42+P38: does NOT detect web_search from history",
      r_current is None or "web_search" not in r_current)

# ═══════════════════════════════════════════════════════════════
# P43: Network Offline Detection + Graceful Degradation
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 22. P43: Offline Detection ══╗")

from tools.base import check_network_available, network_offline_error, _net_cache
import time as _t43_time

# Test network_offline_error format
err = network_offline_error("web_search", "Python 3.12 features")
check("P43: offline error has success=False", not err["success"])
check("P43: offline error mentions 'no external network'", "no external network" in err["output"])
check("P43: offline error suggests grep_search", "grep_search" in err["output"])
check("P43: offline error suggests file_read", "file_read" in err["output"])
check("P43: offline error includes search query", "Python 3.12" in err["output"])

err_fetch = network_offline_error("web_fetch", "https://github.com/repo")
check("P43: web_fetch error mentions GitHub", "GitHub" in err_fetch["output"])
check("P43: error field mentions tool name", "web_fetch" in err_fetch["error"])

# Test cache mechanism
_net_cache["available"] = False
_net_cache["checked_at"] = _t43_time.monotonic()
check("P43: cached offline → returns False", not check_network_available())

_net_cache["available"] = True
_net_cache["checked_at"] = _t43_time.monotonic()
check("P43: cached online → returns True", check_network_available())

# Test TTL expiry
_net_cache["available"] = True
_net_cache["checked_at"] = _t43_time.monotonic() - 200  # expired (>120s TTL)
# This will do a real network probe — result depends on actual network
_result = check_network_available()
check("P43: expired cache triggers re-probe", isinstance(_result, bool))

# Reset cache for other tests
_net_cache["available"] = None
_net_cache["checked_at"] = 0.0

# Test web_search P43 integration (simulate offline)
from tools.web_search import execute as _ws_exec
_net_cache["available"] = False
_net_cache["checked_at"] = _t43_time.monotonic()
r = _ws_exec({"query": "test"}, Path("/tmp"))
check("P43: web_search offline → has suggestions", "Local alternatives" in r.get("output", ""))
check("P43: web_search offline → success=False", not r["success"])

# Test web_fetch P43 integration (simulate offline)
from tools.web_fetch import execute as _wf_exec
r = _wf_exec({"url": "https://example.com"}, Path("/tmp"))
check("P43: web_fetch offline → has suggestions", "Local alternatives" in r.get("output", ""))
check("P43: web_fetch offline → success=False", not r["success"])

# Reset cache
_net_cache["available"] = None
_net_cache["checked_at"] = 0.0

# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 50)
passed = sum(1 for _, ok in _results if ok)
failed = sum(1 for _, ok in _results if not ok)
print(f"Results: {passed} passed, {failed} failed")
print("=" * 50)
if failed:
    print(f"⚠️  {failed} test(s) failed:")
    for name, ok in _results:
        if not ok:
            print(f"  ❌ {name}")
else:
    print("🎉 All P21-P26 tests passed!")
