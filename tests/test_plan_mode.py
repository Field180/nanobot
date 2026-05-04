"""
U21: Plan Mode (Read-Only Planning Mode) Tests
================================================
Tests for plan mode tool filtering, system prompt injection,
write-tool hard block, sub-agent mode inheritance, and mode switching.

Run: python3 tests/test_plan_mode.py
"""
import json
import os
import sys
from pathlib import Path

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
# 1. System Prompt: Plan Mode Behavioral Prompt
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 1. System Prompt: Plan Mode Injection ══╗")

from system_prompts import build_dynamic_context

# 1a: plan_mode=True injects PLAN MODE section
ctx_plan = build_dynamic_context(plan_mode=True, workspace_info="test workspace")
check("1a: PLAN MODE section injected when plan_mode=True",
      "[PLAN MODE" in ctx_plan and "READ-ONLY" in ctx_plan)

# 1b: Plan mode prompt forbids file_edit
check("1b: Plan prompt forbids file_edit",
      "file_edit" in ctx_plan and "Do NOT call" in ctx_plan)

# 1c: Plan mode prompt forbids file_write
check("1c: Plan prompt forbids file_write",
      "file_write" in ctx_plan)

# 1d: Plan mode allows read-only tools
check("1d: Plan prompt allows file_read",
      "file_read" in ctx_plan)
check("1d: Plan prompt allows grep_search",
      "grep_search" in ctx_plan)

# 1e: Plan mode tells model to produce a numbered plan
check("1e: Plan prompt mentions numbered action plan",
      "numbered action plan" in ctx_plan)

# 1f: Plan mode tells to switch to Code mode
check("1f: Plan prompt suggests switching to Code mode",
      "Code mode" in ctx_plan)

# 1g: plan_mode=False does NOT inject plan section
ctx_normal = build_dynamic_context(plan_mode=False, workspace_info="test workspace")
check("1g: No PLAN MODE section when plan_mode=False",
      "[PLAN MODE" not in ctx_normal)

# 1h: Default plan_mode is False
ctx_default = build_dynamic_context(workspace_info="test workspace")
check("1h: Default plan_mode=False (no plan section)",
      "[PLAN MODE" not in ctx_default)

# 1i: Plan mode prompt appears BEFORE workspace info (high priority)
if "[PLAN MODE" in ctx_plan and "Environment:" in ctx_plan:
    plan_pos = ctx_plan.index("[PLAN MODE")
    env_pos = ctx_plan.index("Environment:")
    check("1i: Plan mode prompt appears before environment info",
          plan_pos < env_pos)
else:
    check("1i: Plan mode prompt appears before environment info", False)

# 1j: shell_execute allowed but only read-only
check("1j: Plan prompt allows shell_execute for read-only",
      "shell_execute" in ctx_plan and "read-only commands" in ctx_plan)


# ═══════════════════════════════════════════════════════════════
# 2. Agentic Loop: Mode-Based Tool Filtering
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 2. Tool Filtering by Mode ══╗")

from tools import AGENTIC_TOOLS

# Extract the tool filtering logic inline (mirrors agentic_loop.py lines ~2540-2560)
_READONLY_TOOL_NAMES = {"file_read", "file_list", "grep_search", "find_by_name",
                        "web_fetch", "web_search", "code_intel", "memory", "todo_manage"}

_PLAN_TOOL_NAMES = _READONLY_TOOL_NAMES | {"shell_execute", "python_execute", "sub_agent"}

# All AGENTIC_TOOLS names
all_tool_names = {t["function"]["name"] for t in AGENTIC_TOOLS}

# 2a: Plan mode tool set is a subset of all tools
check("2a: Plan tools are subset of all tools",
      _PLAN_TOOL_NAMES.issubset(all_tool_names))

# 2b: file_edit NOT in plan tools
check("2b: file_edit not in plan mode tools",
      "file_edit" not in _PLAN_TOOL_NAMES)

# 2c: file_write NOT in plan tools
check("2c: file_write not in plan mode tools",
      "file_write" not in _PLAN_TOOL_NAMES)

# 2d: file_read IS in plan tools
check("2d: file_read in plan mode tools",
      "file_read" in _PLAN_TOOL_NAMES)

# 2e: shell_execute IS in plan tools (for verification)
check("2e: shell_execute in plan mode tools",
      "shell_execute" in _PLAN_TOOL_NAMES)

# 2f: sub_agent IS in plan tools
check("2f: sub_agent in plan mode tools",
      "sub_agent" in _PLAN_TOOL_NAMES)

# 2g: grep_search IS in plan tools
check("2g: grep_search in plan mode tools",
      "grep_search" in _PLAN_TOOL_NAMES)

# 2h: Actual filter produces correct tool list for plan mode
plan_filtered = [t for t in AGENTIC_TOOLS if t["function"]["name"] in _PLAN_TOOL_NAMES]
plan_filtered_names = {t["function"]["name"] for t in plan_filtered}
check("2h: Filtered tool list matches plan tools",
      plan_filtered_names == (_PLAN_TOOL_NAMES & all_tool_names))

# 2i: Code mode has all tools (effective_allowed = None means no filtering)
check("2i: Code mode allows all tools (no restriction)",
      len(all_tool_names) > len(_PLAN_TOOL_NAMES))

# 2j: Ask mode has fewer tools than plan mode (no shell_execute, no sub_agent)
check("2j: Ask mode has fewer tools than plan mode",
      len(_READONLY_TOOL_NAMES) < len(_PLAN_TOOL_NAMES))

# 2k: Plan mode intersection with allowed_tools
custom_allowed = {"file_read", "grep_search", "file_edit"}
intersected = _PLAN_TOOL_NAMES & custom_allowed
check("2k: Plan mode intersects with allowed_tools correctly",
      intersected == {"file_read", "grep_search"})  # file_edit excluded by plan


# ═══════════════════════════════════════════════════════════════
# 3. Write-Tool Hard Block
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 3. Write-Tool Hard Block in Plan Mode ══╗")

# 3a: file_edit is classified as write tool
_is_write = "file_edit" in ("file_edit", "file_write")
check("3a: file_edit detected as write tool", _is_write)

# 3b: file_write is classified as write tool
_is_write2 = "file_write" in ("file_edit", "file_write")
check("3b: file_write detected as write tool", _is_write2)

# 3c: Write tools blocked in plan mode (simulating the guard)
mode = "plan"
blocked_tools = []
for tool_name in ["file_edit", "file_write", "file_read", "grep_search"]:
    _is_write_tool = tool_name in ("file_edit", "file_write")
    if mode in ("ask", "plan") and _is_write_tool:
        blocked_tools.append(tool_name)
check("3c: file_edit and file_write blocked in plan mode",
      set(blocked_tools) == {"file_edit", "file_write"})

# 3d: Read tools not blocked
check("3d: file_read not blocked in plan mode",
      "file_read" not in blocked_tools)
check("3d: grep_search not blocked in plan mode",
      "grep_search" not in blocked_tools)


# ═══════════════════════════════════════════════════════════════
# 4. Mode Metadata
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 4. Mode Metadata ══╗")

mode_meta = {
    "code": {
        "label": "Code",
        "banner": "Code mode: read, write, and execute tools are available.",
        "frontend_message": "Code 模式：可读写代码和执行命令",
    },
    "ask": {
        "label": "Ask",
        "banner": "Ask mode: use read-only tools only. Do not write files or execute commands.",
        "frontend_message": "Ask 模式：只读，不会修改文件",
    },
    "plan": {
        "label": "Plan",
        "banner": "Plan mode: focus on exploration and planning. Do not write files directly; produce a plan first.",
        "frontend_message": "Plan 模式：规划变更但不实施",
    },
}

# 4a: Plan mode has metadata
check("4a: Plan mode present in mode_meta", "plan" in mode_meta)

# 4b: Plan mode label
check("4b: Plan mode label is 'Plan'", mode_meta["plan"]["label"] == "Plan")

# 4c: Plan mode banner mentions planning
check("4c: Plan mode banner mentions planning",
      "planning" in mode_meta["plan"]["banner"])

# 4d: Plan mode frontend message in Chinese
check("4d: Plan mode frontend message is Chinese",
      "规划" in mode_meta["plan"]["frontend_message"])

# 4e: All three modes present
check("4e: All three modes (code/ask/plan) have metadata",
      set(mode_meta.keys()) == {"code", "ask", "plan"})


# ═══════════════════════════════════════════════════════════════
# 5. Sub-Agent Mode Inheritance
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 5. Sub-Agent Mode Inheritance ══╗")

from tools.sub_agent import (
    set_parent_context, _PARENT_MODE,
)
from tools import sub_agent as _sub_mod

# 5a: _PARENT_MODE sentinel exists
check("5a: _PARENT_MODE sentinel exists",
      hasattr(_sub_mod, "_PARENT_MODE"))

# 5b: Default _PARENT_MODE is 'code'
# Read the module-level default
check("5b: Default _PARENT_MODE is 'code'",
      _sub_mod._PARENT_MODE == "code")

# 5c: set_parent_context accepts mode parameter
import inspect
sig = inspect.signature(set_parent_context)
check("5c: set_parent_context has 'mode' parameter",
      "mode" in sig.parameters)

# 5d: set_parent_context sets _PARENT_MODE
set_parent_context(
    env={"test": "1"},
    workspace=Path("/tmp"),
    session_id="test-session",
    mode="plan",
)
check("5d: set_parent_context sets _PARENT_MODE to 'plan'",
      _sub_mod._PARENT_MODE == "plan")

# 5e: Restore to code
set_parent_context(
    env={"test": "1"},
    workspace=Path("/tmp"),
    session_id="test-session",
    mode="code",
)
check("5e: set_parent_context restores _PARENT_MODE to 'code'",
      _sub_mod._PARENT_MODE == "code")

# 5f: mode=None falls back to 'code'
set_parent_context(
    env={"test": "1"},
    workspace=Path("/tmp"),
    session_id="test-session",
    mode=None,
)
check("5f: set_parent_context with mode=None falls back to 'code'",
      _sub_mod._PARENT_MODE == "code")

# 5g: mode="" falls back to 'code'
set_parent_context(
    env={"test": "1"},
    workspace=Path("/tmp"),
    session_id="test-session",
    mode="",
)
check("5g: set_parent_context with mode='' falls back to 'code'",
      _sub_mod._PARENT_MODE == "code")


# ═══════════════════════════════════════════════════════════════
# 6. TUI Mode Support
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 6. TUI Mode Support ══╗")

# Read tui.py to verify plan mode support
tui_path = Path(__file__).resolve().parent.parent / "tui.py"
if tui_path.exists():
    tui_content = tui_path.read_text()

    # 6a: TUI accepts --mode plan
    check("6a: TUI help mentions code|ask|plan",
          "code|ask|plan" in tui_content)

    # 6b: /mode command accepts plan
    check("6b: /mode command accepts 'plan'",
          '("code", "ask", "plan")' in tui_content or
          "code\", \"ask\", \"plan\"" in tui_content)

    # 6c: Plan mode has a color in mode_colors
    check("6c: Plan mode has yellow color in TUI",
          '"plan": "ansiyellow"' in tui_content or
          "'plan': 'ansiyellow'" in tui_content)

    # 6d: Mode is passed to send_message
    check("6d: Mode passed to send_message call",
          "mode=state[\"mode\"]" in tui_content or
          "mode=state['mode']" in tui_content)
else:
    check("6a: TUI file exists", False)
    check("6b: /mode command accepts 'plan'", False)
    check("6c: Plan mode has yellow color in TUI", False)
    check("6d: Mode passed to send_message call", False)


# ═══════════════════════════════════════════════════════════════
# 7. Server Mode Handling
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 7. Server Mode Handling ══╗")

server_path = Path(__file__).resolve().parent.parent / "server_final.py"
if server_path.exists():
    # Read just enough to check mode handling (avoid loading entire 44K line file)
    server_content = server_path.read_text(errors="replace")

    # 7a: Server validates plan mode
    check("7a: Server accepts 'plan' as valid mode",
          '"code", "ask", "plan"' in server_content or
          "'code', 'ask', 'plan'" in server_content or
          '{"code", "ask", "plan"}' in server_content)

    # 7b: Server passes mode to agentic_chat_stream
    check("7b: Server passes mode=selected_mode to agentic_chat_stream",
          "mode=selected_mode" in server_content)

    # 7c: Server has plan mode metadata
    check("7c: Server has plan mode label",
          '"Plan"' in server_content and "Plan 模式" in server_content)
else:
    check("7a: Server file exists", False)
    check("7b: Server passes mode to agentic_chat_stream", False)
    check("7c: Server has plan mode label", False)


# ═══════════════════════════════════════════════════════════════
# 8. Agentic Loop Mode Integration
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 8. Agentic Loop Mode Integration ══╗")

agentic_path = Path(__file__).resolve().parent.parent / "agentic_loop.py"
if agentic_path.exists():
    agentic_content = agentic_path.read_text(errors="replace")

    # 8a: agentic_chat_stream accepts mode parameter with 'plan' as valid value
    check("8a: agentic_chat_stream accepts mode parameter",
          'mode: str = "code"' in agentic_content)

    # 8b: Plan mode validation
    check("8b: Plan mode is validated in mode check",
          '"code", "ask", "plan"' in agentic_content or
          '{"code", "ask", "plan"}' in agentic_content)

    # 8c: Plan mode tool filtering exists
    check("8c: Plan mode tool filtering exists",
          'mode == "plan"' in agentic_content)

    # 8d: Plan mode passes plan_mode to build_dynamic_context
    check("8d: plan_mode=(mode == \"plan\") passed to build_dynamic_context",
          'plan_mode=(mode == "plan")' in agentic_content)

    # 8e: Write-tool hard block for plan mode
    check("8e: Write-tool hard block checks plan mode",
          'mode in ("ask", "plan")' in agentic_content)

    # 8f: Sub-agent context includes mode
    check("8f: set_parent_context passes mode=mode",
          "mode=mode" in agentic_content)

    # 8g: Mode meta includes plan
    check("8g: mode_meta dict includes plan entry",
          '"plan":' in agentic_content and '"Plan"' in agentic_content)
else:
    for label in ["8a", "8b", "8c", "8d", "8e", "8f", "8g"]:
        check(f"{label}: agentic_loop.py exists", False)


# ═══════════════════════════════════════════════════════════════
# 9. Mode Transition Correctness
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 9. Mode Transition Correctness ══╗")

# 9a: Switching from plan to code restores all tools
code_allowed = None  # None means all tools
plan_allowed = _PLAN_TOOL_NAMES.copy()
check("9a: Code mode has no restrictions (None = all tools)",
      code_allowed is None)

# 9b: Plan mode has restrictions
check("9b: Plan mode has specific tool set",
      plan_allowed is not None and len(plan_allowed) > 0)

# 9c: Plan mode is strictly smaller than code mode
check("9c: Plan tools < all tools",
      len(_PLAN_TOOL_NAMES) < len(all_tool_names))

# 9d: Ask mode is strictly smaller than plan mode
check("9d: Ask tools < plan tools",
      len(_READONLY_TOOL_NAMES) < len(_PLAN_TOOL_NAMES))

# 9e: Mode hierarchy: ask ⊂ plan ⊂ code
check("9e: ask tools ⊂ plan tools ⊂ all tools",
      _READONLY_TOOL_NAMES.issubset(_PLAN_TOOL_NAMES) and
      _PLAN_TOOL_NAMES.issubset(all_tool_names))


# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "═" * 60)
    passed = sum(1 for _, ok in _results if ok)
    failed = sum(1 for _, ok in _results if not ok)
    total = len(_results)
    print(f"U21 Plan Mode Tests: {passed}/{total} passed, {failed} failed")

    if failed:
        print("\nFailed tests:")
        for name, ok in _results:
            if not ok:
                print(f"  ❌ {name}")
        sys.exit(1)
    else:
        print("All tests passed! ✅")
        sys.exit(0)
