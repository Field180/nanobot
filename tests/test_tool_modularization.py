"""
P2 Tool Modularization Tests
=============================
Verifies the tools/ package architecture, registry, dispatch,
backward-compat re-exports, and each tool module's contract.

Run: python3 tests/test_tool_modularization.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Ensure web_ui is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0

os.environ["NANOBOT_CHANGESET_DIR"] = tempfile.mkdtemp(prefix="nanobot_changes_")


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} — {detail}")


# ═══════════════════════════════════════════════════════════════
# Section 1: Package Structure
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 1. Package Structure ══╗")

import tools
import tools.base
import tools.shell_execute
import tools.file_read
import tools.file_write
import tools.file_edit
import tools.change_set_accept
import tools.change_set_reject
import tools.file_list
import tools.grep_search
import tools.find_by_name
import tools.python_execute
from tools.base import track_file_read

check("tools package importable", True)
check("tools.base importable", True)
check("all core tool modules importable", True)


# ═══════════════════════════════════════════════════════════════
# Section 2: base.py Shared Utilities
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 2. base.py Shared Utilities ══╗")

from tools.base import _subprocess_run, _resolve_path, DANGEROUS_PATTERNS, logger

check("_subprocess_run is callable", callable(_subprocess_run))
check("_resolve_path is callable", callable(_resolve_path))
check("DANGEROUS_PATTERNS is list", isinstance(DANGEROUS_PATTERNS, list))
check("DANGEROUS_PATTERNS has entries", len(DANGEROUS_PATTERNS) >= 5)
check("logger name", logger.name == "nanobot.tools")

# _resolve_path tests
ws = Path("/workspace")
check("resolve absolute path under /tmp", _resolve_path("/tmp/hosts", ws) == Path("/tmp/hosts"))
check("resolve relative path", _resolve_path("foo/bar.py", ws) == Path("/workspace/foo/bar.py"))
check("resolve empty → workspace", _resolve_path("", ws) == Path("/workspace/"))

# _subprocess_run test (should bypass secure_interceptor)
result = _subprocess_run(["echo", "hello"], capture_output=True, text=True)
check("_subprocess_run echo works", result.stdout.strip() == "hello")


# ═══════════════════════════════════════════════════════════════
# Section 3: Tool Module Contract
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 3. Tool Module Contract ══╗")

EXPECTED_MODULES = [
    tools.shell_execute,
    tools.file_read,
    tools.file_write,
    tools.file_edit,
    tools.change_set_accept,
    tools.change_set_reject,
    tools.file_list,
    tools.grep_search,
    tools.find_by_name,
    tools.python_execute,
]

for mod in EXPECTED_MODULES:
    name = mod.__name__.split(".")[-1]
    check(f"{name}: has TOOL_DEF", hasattr(mod, "TOOL_DEF"))
    check(f"{name}: has ALIASES", hasattr(mod, "ALIASES"))
    check(f"{name}: has IS_READONLY", hasattr(mod, "IS_READONLY"))
    check(f"{name}: has execute()", hasattr(mod, "execute") and callable(mod.execute))

    # TOOL_DEF structure
    td = mod.TOOL_DEF
    check(f"{name}: TOOL_DEF.type == function", td.get("type") == "function")
    func = td.get("function", {})
    check(f"{name}: has function.name", "name" in func)
    check(f"{name}: has function.description", "description" in func)
    check(f"{name}: has function.parameters", "parameters" in func)

    # IS_READONLY is bool
    check(f"{name}: IS_READONLY is bool", isinstance(mod.IS_READONLY, bool))

    # ALIASES is list
    check(f"{name}: ALIASES is list", isinstance(mod.ALIASES, list))


# ═══════════════════════════════════════════════════════════════
# Section 4: Registry Aggregation
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 4. Registry Aggregation ══╗")

from tools import AGENTIC_TOOLS, TOOL_NAME_ALIASES, READONLY_TOOLS, execute_tool, _partition_tool_calls
from edit_transaction import accept_change_set

check("AGENTIC_TOOLS has 18 tools (change_set tools excluded from model)", len(AGENTIC_TOOLS) == 18)

tool_names = [t["function"]["name"] for t in AGENTIC_TOOLS]
expected_names = ["shell_execute", "file_read", "file_write",
                  "file_list", "grep_search", "find_by_name", "python_execute", "file_edit",
                  "web_fetch", "web_search", "sub_agent", "todo_manage", "memory",
                  "code_intel", "ask_user", "task_manage", "tool_search", "send_message"]
check("correct tool names", set(tool_names) == set(expected_names),
      f"got {tool_names}")

# Aliases
check("TOOL_NAME_ALIASES count >= 10", len(TOOL_NAME_ALIASES) >= 10)
check("bash → shell_execute", TOOL_NAME_ALIASES.get("bash") == "shell_execute")
check("read_file → file_read", TOOL_NAME_ALIASES.get("read_file") == "file_read")
check("edit_file → file_edit", TOOL_NAME_ALIASES.get("edit_file") == "file_edit")
check("str_replace → file_edit", TOOL_NAME_ALIASES.get("str_replace") == "file_edit")
# change_set_accept/reject aliases removed from AGENTIC_TOOLS (model can't auto-call them)
# but dispatch still works for REST API
check("change_set_accept still dispatchable", execute_tool("change_set_accept", {"change_set_id": "nonexistent"}, Path("/tmp"))["success"] == False)
check("change_set_reject still dispatchable", execute_tool("change_set_reject", {"change_set_id": "nonexistent"}, Path("/tmp"))["success"] == False)
check("grep → grep_search", TOOL_NAME_ALIASES.get("grep") == "grep_search")
check("find → find_by_name", TOOL_NAME_ALIASES.get("find") == "find_by_name")
check("python → python_execute", TOOL_NAME_ALIASES.get("python") == "python_execute")

# Readonly
check("file_read is readonly", "file_read" in READONLY_TOOLS)
check("file_list is readonly", "file_list" in READONLY_TOOLS)
check("grep_search is readonly", "grep_search" in READONLY_TOOLS)
check("find_by_name is readonly", "find_by_name" in READONLY_TOOLS)
check("shell_execute NOT readonly", "shell_execute" not in READONLY_TOOLS)
check("file_write NOT readonly", "file_write" not in READONLY_TOOLS)
check("file_edit NOT readonly", "file_edit" not in READONLY_TOOLS)
check("change_set_accept NOT readonly", "change_set_accept" not in READONLY_TOOLS)
check("change_set_reject NOT readonly", "change_set_reject" not in READONLY_TOOLS)
check("python_execute NOT readonly", "python_execute" not in READONLY_TOOLS)


# ═══════════════════════════════════════════════════════════════
# Section 5: execute_tool Dispatch
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 5. execute_tool Dispatch ══╗")

ws = Path(tempfile.mkdtemp())

# Unknown tool
r = execute_tool("nonexistent_tool", {}, ws)
check("unknown tool → failure", not r["success"])
check("unknown tool → error msg", "Unknown tool" in r["error"])

# file_write + file_read roundtrip
test_file = ws / "dispatch_test.txt"
r = execute_tool("file_write", {"path": str(test_file), "content": "line1\nline2\nline3"}, ws)
check("file_write dispatch OK", r["success"])
check("file_write created change set", isinstance(r.get("_change_set"), dict))
check("file_write defers disk write", not test_file.exists())
approve_result = accept_change_set(r["_change_set"]["id"])
check("file_write accept OK", approve_result["success"])

r = execute_tool("file_read", {"path": str(test_file)}, ws)
check("file_read dispatch OK", r["success"])
check("file_read has metadata", "3 lines" in r["output"])
check("file_read has content", "line2" in r["output"])

# Alias dispatch
r = execute_tool("read_file", {"path": str(test_file)}, ws)
check("alias read_file → file_read", r["success"])

r = execute_tool("bash", {"command": "echo dispatch_test"}, ws)
check("alias bash → shell_execute", r["success"] and "dispatch_test" in r["output"])

# file_edit dispatch
r = execute_tool("file_edit", {"path": str(test_file), "old_string": "line2", "new_string": "LINE_TWO"}, ws)
check("file_edit dispatch OK", r["success"])
check("file_edit has change set", isinstance(r.get("_change_set"), dict))
content = test_file.read_text()
check("file_edit unchanged before approval", "LINE_TWO" not in content and "line2" in content)
approve_result = accept_change_set(r["_change_set"]["id"])
check("file_edit accept OK", approve_result["success"])
content = test_file.read_text()
check("file_edit actually changed file", "LINE_TWO" in content and "line2" not in content)

r = execute_tool("file_edit", {"path": str(test_file), "old_string": "LINE_TWO", "new_string": "line_two_v2"}, ws)
check("second file_edit dispatch OK", r["success"])
check("second file_edit created change set", isinstance(r.get("_change_set"), dict))
approve_via_tool = execute_tool("change_set_accept", {"change_set_id": r["_change_set"]["id"]}, ws)
check("change_set_accept dispatch OK", approve_via_tool["success"])
check("change_set_accept returns applied status", approve_via_tool.get("_change_set", {}).get("status") == "applied")
check("change_set_accept writes file", "line_two_v2" in test_file.read_text() and "LINE_TWO" not in test_file.read_text())

r = execute_tool("file_edit", {"path": str(test_file), "old_string": "line_two_v2", "new_string": "line_two_v3"}, ws)
check("third file_edit dispatch OK", r["success"])
reject_via_tool = execute_tool("change_set_reject", {"change_set_id": r["_change_set"]["id"]}, ws)
check("change_set_reject dispatch OK", reject_via_tool["success"])
check("change_set_reject returns rejected status", reject_via_tool.get("_change_set", {}).get("status") == "rejected")
check("change_set_reject leaves file unchanged", "line_two_v3" not in test_file.read_text() and "line_two_v2" in test_file.read_text())

# file_edit via alias
r = execute_tool("str_replace", {"path": str(test_file), "old_string": "line_two_v2", "new_string": "line_2"}, ws)
check("alias str_replace → file_edit", r["success"])
approve_result = accept_change_set(r["_change_set"]["id"])
check("alias str_replace accept OK", approve_result["success"])

# grep_search dispatch
r = execute_tool("grep_search", {"pattern": "line_2", "path": str(ws)}, ws)
check("grep_search dispatch OK", r["success"])
check("grep_search found match", "line_2" in r["output"])

# file_list dispatch
r = execute_tool("file_list", {"path": str(ws)}, ws)
check("file_list dispatch OK", r["success"])
check("file_list shows file", "dispatch_test.txt" in r["output"])

# python_execute dispatch
r = execute_tool("python_execute", {"code": "print(2+3)"}, ws)
check("python_execute dispatch OK", r["success"])
check("python_execute output", "5" in r["output"])

# find_by_name dispatch
r = execute_tool("find_by_name", {"pattern": "*.txt", "path": str(ws)}, ws)
check("find_by_name dispatch OK", r["success"])
check("find_by_name found file", "dispatch_test.txt" in r["output"])


# ═══════════════════════════════════════════════════════════════
# Section 6: _partition_tool_calls
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 6. _partition_tool_calls ══╗")

def make_tc(name):
    return {"function": {"name": name, "arguments": "{}"}, "id": f"id_{name}"}

# All readonly → single concurrent batch
tcs = [make_tc("file_read"), make_tc("grep_search"), make_tc("find_by_name")]
batches = _partition_tool_calls(tcs)
check("3 readonly → 1 batch", len(batches) == 1)
check("batch is concurrent", batches[0][0] is True)
check("batch has 3 items", len(batches[0][1]) == 3)

# All write → 3 serial batches
tcs = [make_tc("shell_execute"), make_tc("file_write"), make_tc("file_edit")]
batches = _partition_tool_calls(tcs)
check("3 write → 3 batches", len(batches) == 3)
check("all serial", all(not b[0] for b in batches))

# Mixed: read, read, write, read
tcs = [make_tc("file_read"), make_tc("grep_search"), make_tc("file_edit"), make_tc("file_read")]
batches = _partition_tool_calls(tcs)
check("mixed → 3 batches", len(batches) == 3)
check("batch 0 concurrent", batches[0][0] is True)
check("batch 0 has 2", len(batches[0][1]) == 2)
check("batch 1 serial", batches[1][0] is False)
check("batch 2 concurrent", batches[2][0] is True)

# Alias resolution in partition
tcs = [make_tc("read_file"), make_tc("grep")]
batches = _partition_tool_calls(tcs)
check("aliases resolved: 1 concurrent batch", len(batches) == 1 and batches[0][0] is True)

# Empty
check("empty → empty", _partition_tool_calls([]) == [])


# ═══════════════════════════════════════════════════════════════
# Section 7: Backward-Compat Re-exports from agentic_loop
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 7. Backward-Compat Re-exports ══╗")

from agentic_loop import (
    AGENTIC_TOOLS as AT_LOOP,
    TOOL_NAME_ALIASES as TNA_LOOP,
    READONLY_TOOLS as RT_LOOP,
    execute_tool as ET_LOOP,
    _partition_tool_calls as PTC_LOOP,
    _exec_file_edit as EFE_LOOP,
    _subprocess_run as SR_LOOP,
    _resolve_path as RP_LOOP,
    DANGEROUS_PATTERNS as DP_LOOP,
    AGENTIC_SYSTEM_PROMPT,
    _estimate_tokens,
    agentic_chat_stream,
)

# These should be the SAME objects (not copies)
check("AGENTIC_TOOLS is same object", AT_LOOP is AGENTIC_TOOLS)
check("TOOL_NAME_ALIASES is same object", TNA_LOOP is TOOL_NAME_ALIASES)
check("READONLY_TOOLS is same object", RT_LOOP is READONLY_TOOLS)
check("execute_tool is same function", ET_LOOP is execute_tool)
check("_partition_tool_calls is same", PTC_LOOP is _partition_tool_calls)
check("_subprocess_run is same", SR_LOOP is _subprocess_run)
check("_resolve_path is same", RP_LOOP is _resolve_path)
check("DANGEROUS_PATTERNS is same", DP_LOOP is DANGEROUS_PATTERNS)
check("_exec_file_edit is tools.file_edit.execute", EFE_LOOP is tools.file_edit.execute)
check("AGENTIC_SYSTEM_PROMPT accessible", len(AGENTIC_SYSTEM_PROMPT) > 100)
check("_estimate_tokens accessible", callable(_estimate_tokens))
check("agentic_chat_stream accessible", callable(agentic_chat_stream))


# ═══════════════════════════════════════════════════════════════
# Section 8: server_final.py Import Compatibility
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 8. server_final Import Compat ══╗")

# Simulate what server_final.py does
try:
    from agentic_loop import agentic_chat_stream as acs, AGENTIC_TOOLS as at
    check("server_final import pattern works", True)
    check("agentic_chat_stream is async generator func",
          hasattr(acs, '__call__'))
    check("AGENTIC_TOOLS is list of 18", len(at) == 18)
except ImportError as e:
    check("server_final import pattern works", False, str(e))


# ═══════════════════════════════════════════════════════════════
# Section 9: Tool Isolation (each tool only imports from base)
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 9. Tool Isolation ══╗")

import inspect

for mod in EXPECTED_MODULES:
    name = mod.__name__.split(".")[-1]
    src = inspect.getsource(mod)
    # No tool module should import from agentic_loop (avoids circular deps)
    has_circular = "from agentic_loop" in src or "import agentic_loop" in src
    check(f"{name}: no circular import from agentic_loop", not has_circular)


# ═══════════════════════════════════════════════════════════════
# Section 10: Edge Cases
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 10. Edge Cases ══╗")

# shell_execute: dangerous command blocking
r = execute_tool("shell_execute", {"command": "rm -rf /"}, ws)
check("dangerous cmd blocked", not r["success"])
check("blocked error msg", "Blocked" in r["error"])

# file_read: nonexistent file
r = execute_tool("file_read", {"path": "/nonexistent/file.txt"}, ws)
check("read nonexistent → error", not r["success"])

# file_edit: no-op (old == new)
r = execute_tool("file_edit", {"path": str(test_file), "old_string": "x", "new_string": "x"}, ws)
check("no-op edit rejected", not r["success"])
check("no-op error msg", "identical" in r["error"])

# file_edit: create new file via old_string=''
new_file = ws / "new_created.txt"
r = execute_tool("file_edit", {"path": str(new_file), "old_string": "", "new_string": "brand new\n"}, ws)
check("create via file_edit OK", r["success"])
check("create via file_edit deferred", not new_file.exists())
approve_result = accept_change_set(r["_change_set"]["id"])
check("create via file_edit accept OK", approve_result["success"])
check("created file exists", new_file.exists())
check("created file content", new_file.read_text() == "brand new\n")

# file_edit: multiple occurrences without replace_all
multi_file = ws / "multi.txt"
multi_file.write_text("aaa bbb aaa ccc aaa")
track_file_read(multi_file)  # P11: must read before edit
r = execute_tool("file_edit", {"path": str(multi_file), "old_string": "aaa", "new_string": "XXX"}, ws)
check("multi-match without replace_all → error", not r["success"])
check("multi-match error mentions count", "3 occurrences" in r["error"])

# file_edit: replace_all
r = execute_tool("file_edit", {"path": str(multi_file), "old_string": "aaa", "new_string": "XXX", "replace_all": True}, ws)
check("replace_all succeeds", r["success"])
approve_result = accept_change_set(r["_change_set"]["id"])
check("replace_all accept OK", approve_result["success"])
check("replace_all applied", multi_file.read_text() == "XXX bbb XXX ccc XXX")


# ═══════════════════════════════════════════════════════════════
# Section 11: Dynamic Prompt Assembly (build_tool_guidance)
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 11. Dynamic Prompt Assembly ══╗")

from tools import build_tool_guidance

guidance_text = build_tool_guidance()
check("returns string", isinstance(guidance_text, str))
check("starts with heading", guidance_text.startswith("# Using your tools"))
check("non-trivial length", len(guidance_text) > 500)

# Every tool with replaces_shell should appear as "use <tool> (not ...)"
for mod in [tools.file_read, tools.grep_search, tools.find_by_name, tools.file_list, tools.file_edit]:
    name = mod.TOOL_DEF["function"]["name"]
    check(f"guidance mentions {name}", name in guidance_text)

# Shell blocklist auto-generated
check("NEVER use shell_execute line present", "NEVER use shell_execute for:" in guidance_text)
check("blocklist includes file_read", "use file_read" in guidance_text)
check("blocklist includes grep_search", "use grep_search" in guidance_text)
check("blocklist includes find_by_name", "use find_by_name" in guidance_text)
check("blocklist includes file_edit", "use file_edit" in guidance_text)
check("blocklist includes file_list", "use file_list" in guidance_text)

# Prefer rules auto-generated
check("prefer file_edit over file_write", "Prefer file_edit over file_write" in guidance_text)

# Concurrency hint auto-generated
check("concurrency hint present", "concurrently" in guidance_text)
check("readonly tools listed", "file_read" in guidance_text and "grep_search" in guidance_text)

# Shell fallback tip present
check("shell_execute fallback tip", "Reserve shell_execute ONLY" in guidance_text)

# Tips from individual modules appear
check("file_read metadata tip", "metadata header" in guidance_text)
check("file_read limit tip", "limit=N" in guidance_text)
check("file_edit exact match tip", "EXACTLY" in guidance_text)

# AGENTIC_SYSTEM_PROMPT uses dynamic assembly
from agentic_loop import AGENTIC_SYSTEM_PROMPT, build_system_prompt
check("AGENTIC_SYSTEM_PROMPT == build_system_prompt()", AGENTIC_SYSTEM_PROMPT == build_system_prompt())
check("prompt includes identity", "Nanobot" in AGENTIC_SYSTEM_PROMPT)
check("prompt includes tool guidance", "Using your tools" in AGENTIC_SYSTEM_PROMPT)
check("prompt includes tasks", "Doing tasks" in AGENTIC_SYSTEM_PROMPT)
check("prompt includes stop rules", "STOP calling tools" in AGENTIC_SYSTEM_PROMPT)
check("prompt includes quality", "Response quality" in AGENTIC_SYSTEM_PROMPT)

# GUIDANCE schema validation for all modules
for mod in [tools.shell_execute, tools.file_read, tools.file_write, tools.file_edit,
            tools.file_list, tools.grep_search, tools.find_by_name, tools.python_execute]:
    name = mod.__name__.split(".")[-1]
    g = getattr(mod, "GUIDANCE", None)
    check(f"{name}: has GUIDANCE", g is not None)
    if g:
        check(f"{name}: GUIDANCE is dict", isinstance(g, dict))
        if "tips" in g:
            check(f"{name}: tips is list", isinstance(g["tips"], list))
        if "replaces_shell" in g:
            check(f"{name}: replaces_shell is list", isinstance(g["replaces_shell"], list))
            check(f"{name}: has shell_never", "shell_never" in g or "is_fallback" in g,
                  "tools with replaces_shell should have shell_never for clean blocklist text")


# ═══════════════════════════════════════════════════════════════
# 12. Tool Post-Processing and Completeness Nudge
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 12. Tool Post-Processing & Completeness Nudge ══╗")

from agentic_loop import _postprocess_tool_content, _build_completeness_nudge

# --- _postprocess_tool_content ---
# Normal success
res_normal = {"success": True, "output": "some output", "error": ""}
check("postprocess: normal passthrough", "some output" in _postprocess_tool_content("test", res_normal))

# Empty output → explicit marker
res_empty = {"success": True, "output": "", "error": ""}
check("postprocess: empty → marker", "(test completed with no output)" in _postprocess_tool_content("test", res_empty))

# Whitespace-only output → also marker
res_ws = {"success": True, "output": "   \n  ", "error": ""}
check("postprocess: whitespace → marker", "(test completed with no output)" in _postprocess_tool_content("test", res_ws))

# Error output
res_err = {"success": False, "output": "partial", "error": "something broke"}
processed_err = _postprocess_tool_content("test", res_err)
check("postprocess: error includes message", "something broke" in processed_err)
check("postprocess: error includes output", "partial" in processed_err)

# Large output → truncation notice
big_output = "x" * 20000
res_big = {"success": True, "output": big_output, "error": ""}
processed_big = _postprocess_tool_content("test", res_big)
check("postprocess: large truncated", "characters omitted" in processed_big)
check("postprocess: large has anti-guess", "Do NOT guess" in processed_big)
check("postprocess: large shorter than original", len(processed_big) < len(big_output))

# --- _build_completeness_nudge ---
# Multi-part question
multi_q = "请帮我分析：\n1. 列出文件\n2. 读取内容\n3. 搜索关键词\n4. 总结"
nudge = _build_completeness_nudge(multi_q, ["file_list"], turn=2)
check("nudge: multi-part detected", nudge is not None)
check("nudge: mentions sub-tasks", "4 sub-tasks" in nudge)
check("nudge: includes task text", "列出文件" in nudge)

# Single question → no nudge
single_q = "请读取 tools/__init__.py"
check("nudge: single → None", _build_completeness_nudge(single_q, ["file_read"], turn=2) is None)

# Turn 1 → no nudge (too early)
check("nudge: turn 1 → None", _build_completeness_nudge(multi_q, ["file_list"], turn=1) is None)

# Turn 7+ → no nudge (too late)
check("nudge: turn 7 → None", _build_completeness_nudge(multi_q, ["file_list"], turn=7) is None)

# Nudge includes tool-specific suggestions
check("nudge: suggests grep_search", "grep_search" in nudge, "nudge should suggest grep_search for '搜索'")
check("nudge: marks file_list done", "file_list already called" in nudge, "file_list was in tools_used, should show ✓")
check("nudge: suggests file_read", "file_read" in nudge, "nudge should suggest file_read for '读取'")

# Nudge with all tools already used → all marked done
nudge_done = _build_completeness_nudge(multi_q, ["file_list", "file_read", "grep_search"], turn=3)
check("nudge: done tools marked ✓", nudge_done is not None and "already called" in nudge_done)

# System prompt includes new planning rules
check("prompt: PLANNING rule", "PLANNING" in AGENTIC_SYSTEM_PROMPT)
check("prompt: TABLE RULE", "TABLE RULE" in AGENTIC_SYSTEM_PROMPT)
check("prompt: Report outcomes faithfully", "Report outcomes faithfully" in AGENTIC_SYSTEM_PROMPT)

# --- tool_registry_summary ---
from tools import tool_registry_summary
summary = tool_registry_summary()
check("registry: has header", "Tool Registry Metadata" in summary)
check("registry: lists 18 tools", "Total: 18 tools" in summary)
check("registry: shell_execute aliases=3", "shell_execute" in summary and "3" in summary)
check("registry: file_read readonly yes", "file_read" in summary and "✅ yes" in summary)
check("registry: file_edit aliases", "edit_file, str_replace, text_editor" in summary)
check("registry: shows total aliases", "42 total aliases" in summary)

# file_list auto-appends registry when listing tools/
from tools.file_list import execute as _fl_exec
fl_result = _fl_exec({"path": "tools"}, ws.parent)  # ws.parent = workspace root
# Only appends if tools/__init__.py exists in the listed dir
fl_tools_dir = ws.parent / "tools"
if fl_tools_dir.exists() and (fl_tools_dir / "__init__.py").exists():
    check("file_list: auto-appends registry", "Tool Registry Metadata" in fl_result["output"])
else:
    check("file_list: no registry for non-tools dir", "Tool Registry Metadata" not in fl_result.get("output", ""))

# file_read auto-appends registry when reading tools/__init__.py
from tools.file_read import execute as _fr_exec
init_path = ws.parent / "tools" / "__init__.py"
if init_path.exists():
    fr_result = _fr_exec({"path": "tools/__init__.py"}, ws.parent)
    check("file_read: auto-appends registry", "Tool Registry Metadata" in fr_result["output"])

# file_read does NOT append registry for non-tools files
fr_base = _fr_exec({"path": "tools/base.py"}, ws.parent)
check("file_read: no registry for base.py", "Tool Registry Metadata" not in fr_base["output"])

# Pre-flight planning regex works on real user message
import re as _re
real_msg = """请帮我分析 tools/ 目录的架构：
1. 列出 tools/ 目录所有文件
2. 读取 tools/__init__.py 和 tools/base.py
3. 搜索哪些文件里有 "IS_READONLY = True"
4. 用表格总结每个工具模块的名称、是否只读、别名数量"""
numbered = _re.findall(r'(?:^|\n)\s*(\d+)[.)]\s*(.+)', real_msg)
check("preflight: detects 4 tasks", len(numbered) == 4)
check("preflight: task 1 is list", "列出" in numbered[0][1])
check("preflight: task 3 is search", "搜索" in numbered[2][1])
check("preflight: task 4 is table", "表格" in numbered[3][1])


# ═══════════════════════════════════════════════════════════════
# Section 13: P3 — Tiered MicroCompact
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 13. P3: Tiered MicroCompact ══╗")

from agentic_loop import (
    MICRO_COMPACT_CONFIG, _DEFAULT_MC, _MC_AGE_DECAY_TURNS,
    _get_mc_limits, _micro_compact_old_messages,
)

# Per-tool configs exist
check("P3: file_read has own config", "file_read" in MICRO_COMPACT_CONFIG)
check("P3: file_read max > default", MICRO_COMPACT_CONFIG["file_read"]["max"] > _DEFAULT_MC["max"])
check("P3: file_list max < default", MICRO_COMPACT_CONFIG["file_list"]["max"] < _DEFAULT_MC["max"])
check("P3: grep_search max < default", MICRO_COMPACT_CONFIG["grep_search"]["max"] < _DEFAULT_MC["max"])

# _get_mc_limits returns per-tool values
max_c, head_c, tail_c = _get_mc_limits("file_read", current_turn=1, message_turn=1)
check("P3: file_read max=12000", max_c == 12000)
check("P3: file_read head=5000", head_c == 5000)
check("P3: file_read tail=3000", tail_c == 3000)

# _get_mc_limits falls back to default for unknown tools
max_c2, _, _ = _get_mc_limits("unknown_tool", current_turn=1, message_turn=1)
check("P3: unknown tool → default max", max_c2 == _DEFAULT_MC["max"])

# Age-based decay: limits halved after 3 turns
max_c3, head_c3, tail_c3 = _get_mc_limits("file_read", current_turn=5, message_turn=1)
check("P3: aged max halved", max_c3 == 12000 // 2)
check("P3: aged head halved", head_c3 == 5000 // 2)
check("P3: aged tail halved", tail_c3 == 3000 // 2)

# No decay if age < threshold
max_c4, _, _ = _get_mc_limits("file_read", current_turn=3, message_turn=1)
check("P3: no decay at age=2", max_c4 == 12000)

# _postprocess_tool_content uses per-tool limits
big_output = "x" * 15000
result_big = {"success": True, "output": big_output, "error": ""}
# file_read has 12000 max → should truncate at 12000
pp_fr = _postprocess_tool_content("file_read", result_big, current_turn=1, message_turn=1)
check("P3: file_read truncates at 12K", len(pp_fr) < 15000)
check("P3: file_read anti-guess", "Do NOT guess" in pp_fr)
# file_list has 4000 max → should truncate shorter
pp_fl = _postprocess_tool_content("file_list", result_big, current_turn=1, message_turn=1)
check("P3: file_list truncates shorter than file_read", len(pp_fl) < len(pp_fr))

# _micro_compact_old_messages re-truncates old tool msgs
test_msgs = [
    {"role": "tool", "content": "x" * 10000, "_tool_name": "file_read", "_turn": 1},
    {"role": "tool", "content": "y" * 2000, "_tool_name": "grep_search", "_turn": 4},
    {"role": "user", "content": "hello"},
]
trimmed = _micro_compact_old_messages(test_msgs, current_turn=5)
check("P3: micro_compact trimmed 1 msg", trimmed == 1)
check("P3: old msg got shorter", len(test_msgs[0]["content"]) < 10000)
check("P3: recent msg untouched", len(test_msgs[1]["content"]) == 2000)

# ═══════════════════════════════════════════════════════════════
# Section 14: P4 — Dynamic System Prompt Assembly
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 14. P4: Dynamic System Prompt Assembly ══╗")

from agentic_loop import build_system_prompt, _load_nanobot_md

# Default prompt (no extras)
default_prompt = build_system_prompt()
check("P4: default has identity", "Nanobot" in default_prompt)
check("P4: default has tool guidance", "Using your tools" in default_prompt)
check("P4: default no Language section", "# Language" not in default_prompt)
check("P4: default no Environment section", "# Environment" not in default_prompt)
check("P4: default no User Instructions", "NANOBOT.md" not in default_prompt)

# With language
zh_prompt = build_system_prompt(language="zh")
check("P4: zh adds Language section", "# Language" in zh_prompt)
check("P4: zh mentions Chinese", "Chinese" in zh_prompt or "中文" in zh_prompt)

en_prompt = build_system_prompt(language="en")
check("P4: en adds English", "English" in en_prompt)

# With workspace_info
ws_prompt = build_system_prompt(workspace_info="CWD: /tmp/test\nOS: Linux")
check("P4: workspace adds Environment", "# Environment" in ws_prompt)
check("P4: workspace includes path", "/tmp/test" in ws_prompt)

# With custom_instructions
ci_prompt = build_system_prompt(custom_instructions="Always use TypeScript.\nPrefer pnpm.")
check("P4: custom adds User Instructions", "# User Instructions" in ci_prompt)
check("P4: custom includes content", "TypeScript" in ci_prompt)
check("P4: custom mentions NANOBOT.md", "NANOBOT.md" in ci_prompt)

# Combined
full_prompt = build_system_prompt(language="zh", workspace_info="CWD: /x", custom_instructions="Use Rust")
check("P4: full has all sections", all(s in full_prompt for s in ["# Language", "# Environment", "# User Instructions"]))
check("P4: full prompt longer than default", len(full_prompt) > len(default_prompt))

# _load_nanobot_md returns "" for non-existent workspace
import tempfile
_tmp_ws = Path(tempfile.mkdtemp())
check("P4: _load_nanobot_md empty for no file", _load_nanobot_md(_tmp_ws) == "")
# Create a NANOBOT.md and verify it loads
(_tmp_ws / "NANOBOT.md").write_text("Test instructions\nLine 2")
loaded = _load_nanobot_md(_tmp_ws)
check("P4: _load_nanobot_md reads file", "Test instructions" in loaded)
import shutil as _sh
_sh.rmtree(_tmp_ws, ignore_errors=True)

# ═══════════════════════════════════════════════════════════════
# Section 15: P5 — Token Budget Tracker
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 15. P5: Token Budget Tracker ══╗")

from agentic_loop import TokenBudgetTracker, _estimate_tokens

# Constructor
budget = TokenBudgetTracker(ceiling=32000)
check("P5: ceiling set", budget.ceiling == 32000)
check("P5: initial cumulative=0", budget.cumulative_input == 0 and budget.cumulative_output == 0)
check("P5: initial compaction=0", budget.compaction_count == 0)
check("P5: source is estimated initially", budget.get_stats()["token_budget"]["source"] == "estimated")

# update_from_estimate
budget.update_from_estimate(
    [{"content": "hello world"}],
    "response text here"
)
check("P5: estimate updates input", budget.cumulative_input > 0)
check("P5: estimate updates output", budget.cumulative_output > 0)

# update_from_llm_usage with real data
budget2 = TokenBudgetTracker(ceiling=32000)
budget2.update_from_llm_usage({"prompt_tokens": 500, "completion_tokens": 200})
check("P5: real usage input=500", budget2.cumulative_input == 500)
check("P5: real usage output=200", budget2.cumulative_output == 200)
check("P5: source flips to real", budget2.get_stats()["token_budget"]["source"] == "real")

# After real usage, estimate is ignored
budget2.update_from_estimate([{"content": "ignored"}], "also ignored")
check("P5: estimate ignored after real", budget2.cumulative_input == 500)

# Cumulative across multiple calls
budget2.update_from_llm_usage({"prompt_tokens": 300, "completion_tokens": 100})
check("P5: cumulative input=800", budget2.cumulative_input == 800)
check("P5: cumulative output=300", budget2.cumulative_output == 300)

# record_compaction
budget2.record_compaction(old_tokens=5000, new_tokens=2000)
check("P5: compaction_count=1", budget2.compaction_count == 1)
check("P5: tokens_saved=3000", budget2.tokens_saved == 3000)
budget2.record_compaction(old_tokens=4000, new_tokens=1500)
check("P5: compaction_count=2", budget2.compaction_count == 2)
check("P5: tokens_saved=5500", budget2.tokens_saved == 5500)

# should_compact
check("P5: should_compact at 90%", budget2.should_compact(28800))
check("P5: no compact at 50%", not budget2.should_compact(16000))

# get_stats structure
stats = budget2.get_stats()
tb = stats["token_budget"]
check("P5: stats has ceiling", tb["ceiling"] == 32000)
check("P5: stats has cumulative_total", tb["cumulative_total"] == 800 + 300)
check("P5: stats has compaction_count", tb["compaction_count"] == 2)
check("P5: stats has tokens_saved", tb["tokens_saved"] == 5500)

# None usage is safe
budget3 = TokenBudgetTracker(ceiling=10000)
budget3.update_from_llm_usage(None)
check("P5: None usage safe", budget3.cumulative_input == 0)
budget3.update_from_llm_usage({})
check("P5: empty dict safe", budget3.cumulative_input == 0)


# ═══════════════════════════════════════════════════════════════
# Section 16: P7 — Table Fill Intelligence
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 16. P7: Table Fill Detection ══╗")

from agentic_loop import _detect_table_fill_request

# Pattern A: Tab-separated header + bare items (exact user scenario)
msg_tabs = "解释一下工具\t作用max\t\nfile_read\t\ngrep_search\t\nshell_execute\t\npython_execute\t\nfile_list\t\nfind_by_name\t\nfile_edit\t\nfile_write\t"
hint_a = _detect_table_fill_request(msg_tabs)
check("P7: tab-separated detected", hint_a is not None)
check("P7: hint mentions TABLE FILL", "[TABLE FILL]" in (hint_a or ""))
check("P7: hint lists items", "file_read" in (hint_a or ""))
check("P7: hint says use tools", "tools" in (hint_a or "").lower())
check("P7: hint says no guess", "guess" in (hint_a or "").lower() or "fabricate" in (hint_a or "").lower())

# Pattern B: Markdown table with empty cells
msg_md = """请帮我填完这个表格:
| 工具 | 最大字符数 | 说明 |
|------|-----------|------|
| file_read | | |
| grep_search | | |
| shell_execute | | |
| file_edit | | |
"""
hint_b = _detect_table_fill_request(msg_md)
check("P7: markdown table detected", hint_b is not None)
check("P7: markdown hint has TABLE FILL", "[TABLE FILL]" in (hint_b or ""))

# Pattern C: List of bare code identifiers
msg_ids = """这些函数分别做什么:
_get_mc_limits
_postprocess_tool_content
_micro_compact_old_messages
_detect_table_fill_request
build_system_prompt"""
hint_c = _detect_table_fill_request(msg_ids)
check("P7: code identifier list detected", hint_c is not None)

# Negative: normal question should NOT trigger
msg_normal = "你好，请帮我分析一下这个项目"
check("P7: normal question → None", _detect_table_fill_request(msg_normal) is None)

# Negative: short message
msg_short = "file_read\ngrep_search"
check("P7: short message → None", _detect_table_fill_request(msg_short) is None)

# Negative: numbered tasks (should be handled by pre-flight, not table fill)
msg_numbered = "1. 列出文件\n2. 读取 __init__.py\n3. 搜索 IS_READONLY\n4. 总结"
check("P7: numbered tasks → None", _detect_table_fill_request(msg_numbered) is None)


# ═══════════════════════════════════════════════════════════════
# Section 17: P8 — Context Awareness
# ═══════════════════════════════════════════════════════════════
print(f"\n╔══ 17. P8: Context Awareness ══╗")

from agentic_loop import (
    _SYSTEM_PROMPT_SELF_KNOWLEDGE, _build_platform_info, build_system_prompt
)
import platform as _platform

# Self-knowledge section contents
check("P8: no sandbox mentioned", "no sandbox" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P8: no container", "no container" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P8: python_execute bypass warning", "do NOT use it to call other tools" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P8: tool max values present", "12000" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P8: grep max present", "6000" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P8: file_edit max present", "3000" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P8: instructs to use file_read for self-knowledge",
      "file_read" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)
check("P8: anti-guess", "Do NOT describe features" in _SYSTEM_PROMPT_SELF_KNOWLEDGE)

# Platform info
pi = _build_platform_info()
check("P8: platform has OS", "Operating System:" in pi)
check("P8: platform has Python", "Python:" in pi)
actual_os = _platform.system()
check(f"P8: platform detects {actual_os}", actual_os in pi)
if actual_os == "Windows":
    check("P8: Windows → PowerShell note", "PowerShell" in pi)
elif actual_os == "Linux":
    check("P8: Linux → Bash note", "Bash" in pi)
elif actual_os == "Darwin":
    check("P8: macOS → zsh note", "zsh" in pi)

# Both sections appear in build_system_prompt default
default_p = build_system_prompt()
check("P8: default prompt has self-knowledge", "About yourself" in default_p)
check("P8: default prompt has platform", "Operating System:" in default_p)
check("P8: default prompt has no sandbox", "no sandbox" in default_p)

# Verify sections appear AFTER quality but BEFORE optional extras
sections_order = default_p.split("\n\n")
quality_idx = next((i for i, s in enumerate(sections_order) if "Response quality" in s), -1)
self_idx = next((i for i, s in enumerate(sections_order) if "About yourself" in s), -1)
platform_idx = next((i for i, s in enumerate(sections_order) if "Operating System" in s), -1)
check("P8: self-knowledge after quality", self_idx > quality_idx)
check("P8: platform after self-knowledge", platform_idx > self_idx)


# ═══════════════════════════════════════════════════════════════
# Cleanup & Summary
# ═══════════════════════════════════════════════════════════════
import shutil
shutil.rmtree(ws, ignore_errors=True)

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed")
print(f"{'='*50}")
if FAIL == 0:
    print("🎉 All tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) failed")
    if __name__ == "__main__":
        sys.exit(1)
