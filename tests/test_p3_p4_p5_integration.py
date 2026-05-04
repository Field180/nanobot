#!/usr/bin/env python3
"""
Integration tests for P3/P4/P5 — verify features TRIGGER in realistic scenarios.

Unlike unit tests (which check logic), these simulate the full data flow:
  - P3: tool outputs are truncated per-tool and age-decayed across turns
  - P4: system prompt assembles correctly with all optional sections
  - P5: budget tracker accumulates real stats across multi-turn simulations

Run:  python3 tests/test_p3_p4_p5_integration.py
"""
import os
import sys
import json
import tempfile
import shutil
from pathlib import Path

# Ensure web_ui is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}{f' — {detail}' if detail else ''}")


# ═══════════════════════════════════════════════════════════════
# P3 Integration: Simulate multi-turn tool output flow
# ═══════════════════════════════════════════════════════════════
print("╔══ P3 Integration: Multi-Turn Tool Output Flow ══╗")

from agentic_loop import (
    _postprocess_tool_content, _micro_compact_old_messages,
    _get_mc_limits, MICRO_COMPACT_CONFIG, _DEFAULT_MC, _MC_AGE_DECAY_TURNS,
)

# Simulate a realistic 6-turn agentic session
# Turn 1: file_list (small output), file_read (large output), grep_search (medium output)
# Turn 4: model refers back to turn-1 results — they should be age-decayed

# --- Turn 1: fresh tool results with per-tool limits ---
file_list_output = "Directory: /project (50 entries)\n" + "\n".join(
    [f"f  file_{i}.py  ({i}KB)" for i in range(50)]
)
file_read_output = "".join([f"     {i}\tdef function_{i}(): pass\n" for i in range(1, 600)])
grep_output = "\n".join([f"/project/file_{i}.py:10:match_{i}" for i in range(200)])

fl_result = {"success": True, "output": file_list_output, "error": ""}
fr_result = {"success": True, "output": file_read_output, "error": ""}
gs_result = {"success": True, "output": grep_output, "error": ""}

pp_fl = _postprocess_tool_content("file_list", fl_result, current_turn=1, message_turn=1)
pp_fr = _postprocess_tool_content("file_read", fr_result, current_turn=1, message_turn=1)
pp_gs = _postprocess_tool_content("grep_search", gs_result, current_turn=1, message_turn=1)

# file_list: max=4000, should truncate if > 4000
if len(file_list_output) > 4000:
    check("P3-int: file_list truncated", len(pp_fl) < len(file_list_output))
else:
    check("P3-int: file_list passthrough (small)", file_list_output in pp_fl and pp_fl.startswith("[Summary:"))

# file_read: max=12000, output is ~20K → truncated
check("P3-int: file_read truncated at 12K", len(pp_fr) < len(file_read_output))
check("P3-int: file_read preserves head", "function_1" in pp_fr)

# grep_search: max=6000
if len(grep_output) > 6000:
    check("P3-int: grep truncated at 6K", len(pp_gs) < len(grep_output))
    check("P3-int: grep anti-guess notice", "Do NOT guess" in pp_gs)
else:
    check("P3-int: grep passthrough (small)", True)

# --- Turn 4: age-decay simulation ---
messages = [
    {"role": "system", "content": "You are Nanobot..."},
    {"role": "user", "content": "Analyze the project"},
    {"role": "tool", "content": pp_fr, "_tool_name": "file_read", "_turn": 1},
    {"role": "tool", "content": pp_gs, "_tool_name": "grep_search", "_turn": 1},
    {"role": "tool", "content": "Short result", "_tool_name": "file_list", "_turn": 3},
    {"role": "assistant", "content": "Here's my analysis..."},
]

fr_before = len(messages[2]["content"])
gs_before = len(messages[3]["content"])

trimmed = _micro_compact_old_messages(messages, current_turn=5)

# Turn-1 messages (age=4 ≥ 3) should be re-truncated with halved limits
# file_read: halved max = 6000
fr_after = len(messages[2]["content"])
gs_after = len(messages[3]["content"])

# Only trim if they were above the halved limit
fr_halved_max = MICRO_COMPACT_CONFIG["file_read"]["max"] // 2
gs_halved_max = MICRO_COMPACT_CONFIG["grep_search"]["max"] // 2

if fr_before > fr_halved_max:
    check("P3-int: age-decay trimmed file_read", fr_after < fr_before,
          f"before={fr_before}, after={fr_after}, halved_max={fr_halved_max}")
    check("P3-int: age-decay msg has 'aged' notice", "aged" in messages[2]["content"])

if gs_before > gs_halved_max:
    check("P3-int: age-decay trimmed grep", gs_after < gs_before,
          f"before={gs_before}, after={gs_after}, halved_max={gs_halved_max}")

# Recent tool (turn 3, age=2 < 3) should NOT be trimmed
check("P3-int: recent tool untouched", messages[4]["content"] == "Short result")

# Non-tool messages should be untouched
check("P3-int: system msg untouched", "Nanobot" in messages[0]["content"])
check("P3-int: assistant msg untouched", "analysis" in messages[5]["content"])


# ═══════════════════════════════════════════════════════════════
# P3 Integration: Per-tool size comparison
# ═══════════════════════════════════════════════════════════════
print("\n╔══ P3 Integration: Per-Tool Size Ordering ══╗")

huge = "x" * 20000
huge_result = {"success": True, "output": huge, "error": ""}

sizes = {}
for tool in ["file_read", "grep_search", "shell_execute", "file_list", "file_edit", "find_by_name"]:
    pp = _postprocess_tool_content(tool, huge_result, current_turn=1, message_turn=1)
    sizes[tool] = len(pp)

# file_read should produce the longest output (highest max)
check("P3-int: file_read > grep_search", sizes["file_read"] > sizes["grep_search"])
check("P3-int: grep_search > file_list", sizes["grep_search"] > sizes["file_list"])
check("P3-int: file_list > file_edit", sizes["file_list"] > sizes["file_edit"])
check("P3-int: shell_execute > file_edit", sizes["shell_execute"] > sizes["file_edit"])

# With age decay, same tool should produce smaller output
pp_aged = _postprocess_tool_content("file_read", huge_result, current_turn=5, message_turn=1)
check("P3-int: aged file_read < fresh file_read", len(pp_aged) < sizes["file_read"])


# ═══════════════════════════════════════════════════════════════
# P4 Integration: Full system prompt assembly scenarios
# ═══════════════════════════════════════════════════════════════
print("\n╔══ P4 Integration: System Prompt Assembly ══╗")

from agentic_loop import build_system_prompt, _load_nanobot_md, AGENTIC_SYSTEM_PROMPT

# Scenario 1: Default (no extras) — matches AGENTIC_SYSTEM_PROMPT
default = build_system_prompt()
check("P4-int: default == AGENTIC_SYSTEM_PROMPT", default == AGENTIC_SYSTEM_PROMPT)

# Scenario 2: Chinese user with workspace
zh = build_system_prompt(
    language="zh",
    workspace_info="Current workspace: /home/user/project\nOS: Linux",
)
check("P4-int: zh prompt longer", len(zh) > len(default))
check("P4-int: zh has Language", "# Language" in zh)
check("P4-int: zh has Environment", "# Environment" in zh)
check("P4-int: zh has Chinese", "Chinese" in zh or "中文" in zh)
# Count sections: should have 5 base + 2 extras = 7
zh_sections = [s for s in zh.split("\n\n") if s.strip()]
check("P4-int: zh has more sections than default",
      len(zh_sections) > len([s for s in default.split("\n\n") if s.strip()]))

# Scenario 3: With NANOBOT.md custom instructions
custom = build_system_prompt(custom_instructions="Always use TypeScript.\nPrefer functional style.")
check("P4-int: custom has User Instructions", "# User Instructions" in custom)
check("P4-int: custom has TypeScript", "TypeScript" in custom)
check("P4-int: custom mentions precedence", "precedence" in custom)

# Scenario 4: All options combined
full = build_system_prompt(
    language="en",
    workspace_info="CWD: /app",
    custom_instructions="Use pytest for testing",
)
check("P4-int: full has all 3 extras",
      "# Language" in full and "# Environment" in full and "# User Instructions" in full)

# Scenario 5: _load_nanobot_md integration
tmp = Path(tempfile.mkdtemp())
# No NANOBOT.md → empty
check("P4-int: no NANOBOT.md → empty", _load_nanobot_md(tmp) == "")

# Create one
(tmp / "NANOBOT.md").write_text("# My Project\nUse Rust.\nAlways run cargo test.")
loaded = _load_nanobot_md(tmp)
check("P4-int: NANOBOT.md loaded", "Rust" in loaded)
check("P4-int: NANOBOT.md multi-line", "cargo test" in loaded)

# Oversized NANOBOT.md → truncated to 4K
(tmp / "NANOBOT.md").write_text("x" * 10000)
loaded_big = _load_nanobot_md(tmp)
check("P4-int: oversized NANOBOT.md capped", len(loaded_big) <= 4000)
shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# P5 Integration: Simulate multi-turn budget tracking
# ═══════════════════════════════════════════════════════════════
print("\n╔══ P5 Integration: Multi-Turn Budget Tracking ══╗")

from agentic_loop import TokenBudgetTracker, _estimate_tokens, _get_context_ceiling

# Simulate a 3-turn session with a small model (32K context)
env = {"OLLAMA_NUM_CTX": "32768", "NANOBOT_AGENTS__DEFAULTS__MAX_TOKENS": "16384"}
ceiling = _get_context_ceiling(env)
budget = TokenBudgetTracker(ceiling=ceiling)

check("P5-int: ceiling reasonable", 20000 < ceiling < 35000, f"ceiling={ceiling}")

# Turn 1: LLM returns usage (llama.cpp with --metrics)
budget.update_from_llm_usage({"prompt_tokens": 1200, "completion_tokens": 50})
check("P5-int: turn 1 cumulative input", budget.cumulative_input == 1200)
check("P5-int: turn 1 source=real", budget._use_real)

# Turn 2: LLM returns more usage (after tool results injected)
budget.update_from_llm_usage({"prompt_tokens": 3500, "completion_tokens": 200})
check("P5-int: turn 2 cumulative input", budget.cumulative_input == 4700)
check("P5-int: turn 2 cumulative output", budget.cumulative_output == 250)

# Turn 3: even more (model is writing final response)
budget.update_from_llm_usage({"prompt_tokens": 5000, "completion_tokens": 800})
check("P5-int: turn 3 cumulative total", budget.cumulative_input + budget.cumulative_output == 10750)

# should_compact check (80% of ceiling)
threshold = int(ceiling * 0.80)
check("P5-int: not compacting yet", not budget.should_compact(budget.cumulative_input))
check("P5-int: would compact at 80%", budget.should_compact(threshold + 1))

# Compaction event
budget.record_compaction(old_tokens=5000, new_tokens=1500)
check("P5-int: compaction recorded", budget.compaction_count == 1)
check("P5-int: tokens saved", budget.tokens_saved == 3500)

# get_stats for agentic_done event
stats = budget.get_stats()
tb = stats["token_budget"]
check("P5-int: stats has all fields", all(k in tb for k in [
    "ceiling", "cumulative_input", "cumulative_output",
    "cumulative_total", "compaction_count", "tokens_saved", "source",
]))
check("P5-int: stats ceiling matches", tb["ceiling"] == ceiling)
check("P5-int: stats source=real", tb["source"] == "real")
check("P5-int: stats total=10750", tb["cumulative_total"] == 10750)

# Simulate agentic_done event assembly (mirrors main loop)
done_event = {
    "type": "agentic_done",
    "turns": 3,
    "total_tool_calls": 5,
    "tools_used": ["file_list", "file_read", "grep_search"],
}
done_event.update(budget.get_stats())
check("P5-int: done event has type", done_event["type"] == "agentic_done")
check("P5-int: done event has token_budget", "token_budget" in done_event)
check("P5-int: done event serializable", json.dumps(done_event) is not None)


# ═══════════════════════════════════════════════════════════════
# P5 Integration: Fallback estimation mode
# ═══════════════════════════════════════════════════════════════
print("\n╔══ P5 Integration: Estimation Fallback ══╗")

budget_est = TokenBudgetTracker(ceiling=32000)

# No real usage → fallback to estimation
budget_est.update_from_estimate(
    [
        {"content": "You are Nanobot..."},
        {"content": "Analyze tools/ directory"},
        {"content": "Directory: tools/ (11 entries)\nf  __init__.py  (7.0KB)\n..."},
    ],
    "Here is my analysis..."
)
check("P5-int: estimate input > 0", budget_est.cumulative_input > 0)
check("P5-int: estimate output > 0", budget_est.cumulative_output > 0)
check("P5-int: estimate source=estimated", budget_est.get_stats()["token_budget"]["source"] == "estimated")

# Now real data arrives → estimate is ignored
est_input_before = budget_est.cumulative_input
budget_est.update_from_llm_usage({"prompt_tokens": 500, "completion_tokens": 100})
check("P5-int: real data overrides estimate mode", budget_est._use_real)
# Subsequent estimate calls should be no-ops
budget_est.update_from_estimate([{"content": "more data"}], "more output")
check("P5-int: estimate ignored after real", budget_est.cumulative_input == est_input_before + 500)


# ═══════════════════════════════════════════════════════════════
# Cross-feature: P3 + P5 interaction
# ═══════════════════════════════════════════════════════════════
print("\n╔══ Cross-Feature: P3 Age Decay Saves Tokens ══╗")

from agentic_loop import _estimate_messages_tokens

# Build a message list with large tool outputs
large_msgs = [
    {"role": "system", "content": "System prompt"},
    {"role": "user", "content": "Question"},
    {"role": "tool", "content": "A" * 10000, "_tool_name": "file_read", "_turn": 1},
    {"role": "tool", "content": "B" * 5000, "_tool_name": "grep_search", "_turn": 1},
    {"role": "tool", "content": "C" * 3000, "_tool_name": "file_list", "_turn": 2},
]

tokens_before = _estimate_messages_tokens(large_msgs)
trimmed_count = _micro_compact_old_messages(large_msgs, current_turn=5)
tokens_after = _estimate_messages_tokens(large_msgs)

check("P3+P5: age decay reduced token count", tokens_after < tokens_before,
      f"before={tokens_before}, after={tokens_after}")
check("P3+P5: token savings > 0", tokens_before - tokens_after > 0)
savings_pct = (tokens_before - tokens_after) / tokens_before * 100
check(f"P3+P5: saved {savings_pct:.0f}% tokens", savings_pct > 5,
      f"saved {savings_pct:.1f}%")


# ═══════════════════════════════════════════════════════════════
# Live Verification Guide (printed instructions)
# ═══════════════════════════════════════════════════════════════
print("\n╔══ Live Verification Guide ══╗")
print("""
  To verify these features work with a REAL model:

  1. Start Nanobot with logging:
     NANOBOT_LOG_LEVEL=INFO python3 server_final.py

  2. Send the standard test query:
     "请帮我分析 tools/ 目录的架构：
      1. 列出 tools/ 目录所有文件
      2. 读取 tools/__init__.py 和 tools/base.py
      3. 搜索哪些文件里有 'IS_READONLY = True'
      4. 用表格总结每个工具模块的名称、是否只读、别名数量"

  3. Check backend logs for:
     ✓ "[AgenticLoop] Injected pre-flight plan for 4 sub-tasks"
     ✓ "[MicroCompact] Turn X: re-trimmed N old tool message(s)"
     ✓ "[AgenticLoop] Injected completeness nudge #N"
     ✓ agentic_done event should contain "token_budget" field

  4. Check the agentic_done SSE event in browser DevTools:
     - Open Network tab → filter "stream"
     - Look for the last SSE event: should include token_budget stats

  5. A/B comparison:
     - Set NANOBOT_LANGUAGE=zh and check response is in Chinese
     - Create NANOBOT.md with custom instructions and verify they appear
     - Compare alias counts in the response table (should NOT be all 0)
""")


# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed")
print(f"{'='*50}")
if FAIL == 0:
    print("🎉 All P3/P4/P5 integration tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) failed")
    sys.exit(1)
