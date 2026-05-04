"""
P0 + P1 Tests: file_edit tool + Tool Concurrency Partitioning
==============================================================
Tests for Claw-inspired improvements to agentic_loop.py.

Run: python3 tests/test_file_edit_and_concurrency.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure web_ui is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentic_loop import (
    _exec_file_edit,
    _partition_tool_calls,
    execute_tool,
    READONLY_TOOLS,
    TOOL_NAME_ALIASES,
    AGENTIC_TOOLS,
)
from edit_transaction import accept_change_set
from tools.file_edit import build_edit_plan, apply_edit_plan
from tools.base import track_file_read, validate_file_for_edit, _read_file_state

os.environ["NANOBOT_CHANGESET_DIR"] = tempfile.mkdtemp(prefix="nanobot_changes_")

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} — {detail}")


# ═══════════════════════════════════════════════════════════════
# Section 1: file_edit Tool Definition
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 1. file_edit Tool Definition ══╗")

tool_names = [t["function"]["name"] for t in AGENTIC_TOOLS]
check("file_edit in AGENTIC_TOOLS", "file_edit" in tool_names)

fe_tool = next(t for t in AGENTIC_TOOLS if t["function"]["name"] == "file_edit")
fe_params = fe_tool["function"]["parameters"]["properties"]
check("has path param", "path" in fe_params)
check("has old_string param", "old_string" in fe_params)
check("has new_string param", "new_string" in fe_params)
check("has replace_all param", "replace_all" in fe_params)

required = fe_tool["function"]["parameters"]["required"]
check("path is required", "path" in required)
check("old_string is required", "old_string" in required)
check("new_string is required", "new_string" in required)
check("replace_all is NOT required", "replace_all" not in required)


# ═══════════════════════════════════════════════════════════════
# Section 2: file_edit Aliases
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 2. file_edit Aliases ══╗")

check("edit_file alias", TOOL_NAME_ALIASES.get("edit_file") == "file_edit")
check("str_replace alias", TOOL_NAME_ALIASES.get("str_replace") == "file_edit")
check("text_editor alias", TOOL_NAME_ALIASES.get("text_editor") == "file_edit")


# ═══════════════════════════════════════════════════════════════
# Section 3: file_edit Execution — Basic Operations
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 3. file_edit Basic Operations ══╗")

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)

    # Test 1: Basic single replacement
    f1 = ws / "test1.py"
    f1.write_text("hello world\nfoo bar\n", encoding="utf-8")
    track_file_read(f1)  # P11: must read before edit
    result = build_edit_plan({"path": str(f1), "old_string": "foo bar", "new_string": "baz qux"}, ws)
    check("basic replace: success", result["success"])
    apply_result = apply_edit_plan(result["plan"])
    check("basic replace: apply success", apply_result["success"])
    check("basic replace: content correct", f1.read_text() == "hello world\nbaz qux\n")
    check("basic replace: output has diff", "@@" in result["plan"]["diff"] or "baz qux" in result["plan"]["diff"])

    # Test 2: No-op (old == new)
    result = _exec_file_edit({"path": str(f1), "old_string": "baz qux", "new_string": "baz qux"}, ws)
    check("no-op: rejected", not result["success"])
    check("no-op: error message", "identical" in result["error"])

    # Test 3: old_string not found
    result = _exec_file_edit({"path": str(f1), "old_string": "nonexistent", "new_string": "x"}, ws)
    check("not found: rejected", not result["success"])
    check("not found: error message", "not found" in result["error"])

    # Test 4: Multiple occurrences without replace_all
    f2 = ws / "test2.py"
    f2.write_text("aaa\nbbb\naaa\n", encoding="utf-8")
    track_file_read(f2)  # P11: must read before edit
    result = _exec_file_edit({"path": str(f2), "old_string": "aaa", "new_string": "xxx"}, ws)
    check("multi-match: rejected without replace_all", not result["success"])
    check("multi-match: mentions 2 occurrences", "2" in result["error"])

    # Test 5: Multiple occurrences with replace_all
    result = build_edit_plan({"path": str(f2), "old_string": "aaa", "new_string": "xxx", "replace_all": True}, ws)
    check("replace_all: success", result["success"])
    apply_result = apply_edit_plan(result["plan"])
    check("replace_all: apply success", apply_result["success"])
    check("replace_all: all replaced", f2.read_text() == "xxx\nbbb\nxxx\n")
    check("replace_all: output mentions 2 replacements", "2 replacement" in result["plan"]["summary"])

    # Test 6: Single-identifier rename autofix updates definition + call site in one plan
    f3 = ws / "test3.py"
    f3.write_text(
        "def test_OOO():\n"
        "    print('ok')\n\n"
        "if __name__ == '__main__':\n"
        "    test_OOO()\n",
        encoding="utf-8"
    )
    track_file_read(f3)
    result = build_edit_plan(
        {
            "path": str(f3),
            "old_string": "def test_OOO():\n    print('ok')",
            "new_string": "def test_PPMP():\n    print('ok')",
        },
        ws,
    )
    check("rename autofix: success", result["success"])
    check("rename autofix: marker set", result["plan"].get("rename_autofix") is True)
    check("rename autofix: summary mentions 2 replacements", "2 replacement" in result["plan"]["summary"])
    check("rename autofix: diff updates definition", "+def test_PPMP():" in result["plan"]["diff"])
    check("rename autofix: diff updates call site", "+    test_PPMP()" in result["plan"]["diff"])


# ═══════════════════════════════════════════════════════════════
# Section 4: file_edit — New File Creation
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 4. file_edit New File Creation ══╗")

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)

    # Create new file (old_string empty, file doesn't exist)
    new_file = ws / "subdir" / "newfile.py"
    result = build_edit_plan({"path": str(new_file), "old_string": "", "new_string": "print('hello')\n"}, ws)
    check("create new file: success", result["success"])
    apply_result = apply_edit_plan(result["plan"])
    check("create new file: apply success", apply_result["success"])
    check("create new file: file exists", new_file.exists())
    check("create new file: content correct", new_file.read_text() == "print('hello')\n")
    check("create new file: creates parent dirs", (ws / "subdir").is_dir())

    # Reject creation if file exists and is non-empty
    result = _exec_file_edit({"path": str(new_file), "old_string": "", "new_string": "overwrite"}, ws)
    check("create existing non-empty: rejected", not result["success"])
    check("create existing non-empty: error", "already exists" in result["error"])

    # Allow creation on empty file
    empty_file = ws / "empty.py"
    empty_file.write_text("", encoding="utf-8")
    result = build_edit_plan({"path": str(empty_file), "old_string": "", "new_string": "content\n"}, ws)
    check("create on empty file: success", result["success"])


# ═══════════════════════════════════════════════════════════════
# Section 5: file_edit — Edge Cases
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 5. file_edit Edge Cases ══╗")

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)

    # File not found
    result = _exec_file_edit({"path": str(ws / "nope.py"), "old_string": "x", "new_string": "y"}, ws)
    check("file not found: rejected", not result["success"])

    # Trailing whitespace hint
    f = ws / "spaces.py"
    f.write_text("hello   \nworld\n", encoding="utf-8")
    track_file_read(f)  # P11: must read before edit
    result = _exec_file_edit({"path": str(f), "old_string": "hello\nworld", "new_string": "hi\nworld"}, ws)
    check("whitespace hint: gives suggestion", "whitespace" in result["error"].lower())

    # Large file rejection (>10MB) — P11 validation runs before size check,
    # but large files also need to be tracked. We track first, then test.
    big = ws / "big.bin"
    big.write_bytes(b"x" * 11_000_000)
    track_file_read(big)  # P11: track even though it's too large
    result = _exec_file_edit({"path": str(big), "old_string": "x", "new_string": "y"}, ws)
    check("big file: rejected", not result["success"])
    check("big file: error mentions size", "too large" in result["error"].lower())

    # Not a file (directory)
    d = ws / "adir"
    d.mkdir()
    result = _exec_file_edit({"path": str(d), "old_string": "x", "new_string": "y"}, ws)
    check("directory: rejected", not result["success"])


# ═══════════════════════════════════════════════════════════════
# Section 6: file_edit via execute_tool dispatcher
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 6. file_edit via execute_tool ══╗")

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    f = ws / "dispatch.py"
    f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    track_file_read(f)  # P11: must read before edit

    result = execute_tool("file_edit", {"path": str(f), "old_string": "beta", "new_string": "BETA"}, ws)
    check("dispatch: success", result["success"])
    check("dispatch: has change set", isinstance(result.get("_change_set"), dict))
    check("dispatch: file unchanged before approval", f.read_text() == "alpha\nbeta\ngamma\n")
    approve_result = accept_change_set(result["_change_set"]["id"])
    check("dispatch: accept success", approve_result["success"])
    check("dispatch: content correct", f.read_text() == "alpha\nBETA\ngamma\n")

    # Test alias dispatch
    f2 = ws / "alias.py"
    f2.write_text("old text\n", encoding="utf-8")
    track_file_read(f2)  # P11: must read before edit
    result = execute_tool("str_replace", {"path": str(f2), "old_string": "old text", "new_string": "new text"}, ws)
    check("alias str_replace: dispatched to file_edit", result["success"])
    approve_result = accept_change_set(result["_change_set"]["id"])
    check("alias str_replace: accept success", approve_result["success"])
    check("alias str_replace: content correct", f2.read_text() == "new text\n")


# ═══════════════════════════════════════════════════════════════
# Section 7: READONLY_TOOLS Classification
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 7. READONLY_TOOLS Classification ══╗")

check("file_read is readonly", "file_read" in READONLY_TOOLS)
check("file_list is readonly", "file_list" in READONLY_TOOLS)
check("grep_search is readonly", "grep_search" in READONLY_TOOLS)
check("find_by_name is readonly", "find_by_name" in READONLY_TOOLS)
check("file_write is NOT readonly", "file_write" not in READONLY_TOOLS)
check("file_edit is NOT readonly", "file_edit" not in READONLY_TOOLS)
check("shell_execute is NOT readonly", "shell_execute" not in READONLY_TOOLS)
check("python_execute is NOT readonly", "python_execute" not in READONLY_TOOLS)


# ═══════════════════════════════════════════════════════════════
# Section 8: _partition_tool_calls
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 8. _partition_tool_calls ══╗")


def _make_tc(name, args="{}"):
    return {"function": {"name": name, "arguments": args}, "id": f"tc_{name}"}


# 8a: All read-only → single concurrent batch
tcs_readonly = [_make_tc("file_read"), _make_tc("grep_search"), _make_tc("file_read")]
batches = _partition_tool_calls(tcs_readonly)
check("all readonly → 1 batch", len(batches) == 1)
check("all readonly → concurrent=True", batches[0][0] is True)
check("all readonly → 3 items", len(batches[0][1]) == 3)

# 8b: All write → N serial batches (each is its own batch)
tcs_write = [_make_tc("file_write"), _make_tc("file_edit"), _make_tc("shell_execute")]
batches = _partition_tool_calls(tcs_write)
check("all write → 3 batches", len(batches) == 3)
check("all write → each concurrent=False", all(not b[0] for b in batches))

# 8c: Mixed sequence: [read, read, write, read, read]
tcs_mixed = [_make_tc("file_read"), _make_tc("grep_search"),
             _make_tc("file_edit"),
             _make_tc("file_read"), _make_tc("find_by_name")]
batches = _partition_tool_calls(tcs_mixed)
check("mixed → 3 batches", len(batches) == 3)
check("mixed batch 0: concurrent (2 reads)", batches[0][0] and len(batches[0][1]) == 2)
check("mixed batch 1: serial (1 write)", not batches[1][0] and len(batches[1][1]) == 1)
check("mixed batch 2: concurrent (2 reads)", batches[2][0] and len(batches[2][1]) == 2)

# 8d: Single tool → single batch
batches = _partition_tool_calls([_make_tc("file_read")])
check("single read → 1 batch", len(batches) == 1)
batches = _partition_tool_calls([_make_tc("file_write")])
check("single write → 1 batch", len(batches) == 1)

# 8e: Empty → empty
batches = _partition_tool_calls([])
check("empty → 0 batches", len(batches) == 0)

# 8f: Alias resolution in partition
tcs_alias = [_make_tc("read_file"), _make_tc("search")]  # aliases for file_read, grep_search
batches = _partition_tool_calls(tcs_alias)
check("aliases → resolved to readonly → 1 concurrent batch", len(batches) == 1 and batches[0][0])


# ═══════════════════════════════════════════════════════════════
# Section 9: Concurrent Execution Performance
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 9. Concurrent Execution Smoke Test ══╗")

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    # Create 5 test files
    for i in range(5):
        (ws / f"file{i}.txt").write_text(f"Content of file {i}\n" * 50, encoding="utf-8")

    # Serial execution timing
    serial_start = time.time()
    for i in range(5):
        execute_tool("file_read", {"path": str(ws / f"file{i}.txt")}, ws)
    serial_time = time.time() - serial_start

    # Concurrent execution timing
    async def run_concurrent():
        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(None, execute_tool, "file_read", {"path": str(ws / f"file{i}.txt")}, ws)
            for i in range(5)
        ]
        return await asyncio.gather(*tasks)

    concurrent_start = time.time()
    results = asyncio.run(run_concurrent())
    concurrent_time = time.time() - concurrent_start

    check("5 concurrent file_reads: all successful", all(r["success"] for r in results))
    check("5 concurrent file_reads: all have output", all(len(r["output"]) > 0 for r in results))
    # Note: on fast local disks the difference may be small, so we just check it doesn't fail
    print(f"    Serial: {serial_time:.4f}s, Concurrent: {concurrent_time:.4f}s")


# ═══════════════════════════════════════════════════════════════
# Section 10: P11 — ReadFileState Tracking
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 10. P11: ReadFileState Tracking ══╗")

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    # Clear state from previous tests
    _read_file_state.clear()

    # Test: edit without read → rejected
    f = ws / "unread.py"
    f.write_text("content\n", encoding="utf-8")
    result = build_edit_plan({"path": str(f), "old_string": "content", "new_string": "changed"}, ws)
    check("P11: edit without read rejected", not result["success"])
    check("P11: error says 'read'", "read" in result["error"].lower())

    # Test: read then edit → succeeds
    track_file_read(f)
    result = build_edit_plan({"path": str(f), "old_string": "content", "new_string": "changed"}, ws)
    check("P11: edit after read succeeds", result["success"])
    apply_result = apply_edit_plan(result["plan"])
    check("P11: apply after read succeeds", apply_result["success"])

    # Test: external modification → rejected
    time.sleep(0.02)  # ensure mtime changes
    f.write_text("externally modified\n", encoding="utf-8")
    result = build_edit_plan({"path": str(f), "old_string": "externally", "new_string": "internally"}, ws)
    check("P11: stale edit rejected", not result["success"])
    check("P11: stale error says 'modified'", "modified" in result["error"].lower())

    # Test: re-read then edit → succeeds
    track_file_read(f)
    result = build_edit_plan({"path": str(f), "old_string": "externally", "new_string": "internally"}, ws)
    check("P11: edit after re-read succeeds", result["success"])
    apply_result = apply_edit_plan(result["plan"])
    check("P11: apply after re-read succeeds", apply_result["success"])

    # Test: consecutive edits on same file work (update_file_state_after_edit)
    result = build_edit_plan({"path": str(f), "old_string": "internally", "new_string": "finally"}, ws)
    check("P11: consecutive edit succeeds", result["success"])
    apply_result = apply_edit_plan(result["plan"])
    check("P11: consecutive apply succeeds", apply_result["success"])
    check("P11: consecutive edit content correct", f.read_text() == "finally modified\n")

    # Test: validate_file_for_edit returns empty string for OK
    track_file_read(f)
    check("P11: validate OK → empty string", validate_file_for_edit(f) == "")

    # Test: validate_file_for_edit returns error for untracked
    _read_file_state.clear()
    err = validate_file_for_edit(f)
    check("P11: validate untracked → non-empty", len(err) > 0)


# ═══════════════════════════════════════════════════════════════
# Section 11: System Prompt mentions file_edit
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 11. System Prompt Updates ══╗")

from agentic_loop import AGENTIC_SYSTEM_PROMPT

check("system prompt mentions file_edit", "file_edit" in AGENTIC_SYSTEM_PROMPT)
check("system prompt: prefer file_edit over file_write",
      "Prefer file_edit over file_write" in AGENTIC_SYSTEM_PROMPT
      or "file_edit (not file_write" in AGENTIC_SYSTEM_PROMPT)
check("system prompt: file_edit for existing files",
      "existing file" in AGENTIC_SYSTEM_PROMPT.lower()
      or "edit existing files" in AGENTIC_SYSTEM_PROMPT.lower())


# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════
print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed")
print(f"{'=' * 50}")
if FAIL == 0:
    print("🎉 All tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) failed")
    sys.exit(1)
