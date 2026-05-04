import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from edit_transaction import (
    create_pending_change_set,
    accept_change_set,
    reject_change_set,
    list_pending_change_sets,
    build_unified_diff,
    cleanup_orphaned_tempfiles,
)

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


store_dir = tempfile.mkdtemp(prefix="nanobot_changes_")
os.environ["NANOBOT_CHANGESET_DIR"] = store_dir

print("\n╔══ edit_transaction smoke tests ══╗")


def make_plan(path: Path, before_text: str, after_text: str, before_exists: bool = True, after_exists: bool = True, tool_name: str = "file_edit"):
    return {
        "path": str(path),
        "before_text": before_text,
        "after_text": after_text,
        "before_exists": before_exists,
        "after_exists": after_exists,
        "created": not before_exists and after_exists,
        "tool_name": tool_name,
        "summary": f"Change {path.name}",
        "diff": build_unified_diff(before_text, after_text, str(path), before_exists=before_exists, after_exists=after_exists),
    }

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    target = ws / "demo.txt"
    target.write_text("before\n", encoding="utf-8")
    plan = {
        "path": str(target),
        "before_text": "before\n",
        "after_text": "after\n",
        "before_exists": True,
        "created": False,
        "tool_name": "file_edit",
        "summary": "Edit demo.txt",
        "diff": build_unified_diff("before\n", "after\n", str(target), before_exists=True),
    }
    change_set = create_pending_change_set(plan, session_id="s1", source="file_edit")
    check("create pending returns id", change_set["id"].startswith("cs_"))
    check("create pending status", change_set["status"] == "pending")
    check("create pending type defaults to direct", change_set["type"] == "direct")
    check("create pending hides before_text", "before_text" not in change_set["files"][0])
    pending = list_pending_change_sets("s1")
    check("list pending includes change set", any(item["id"] == change_set["id"] for item in pending))
    check("target unchanged before accept", target.read_text(encoding="utf-8") == "before\n")

    accept_result = accept_change_set(change_set["id"])
    check("accept succeeds", accept_result["success"])
    check("accept writes file", target.read_text(encoding="utf-8") == "after\n")
    check("accepted no longer pending", not any(item["id"] == change_set["id"] for item in list_pending_change_sets("s1")))

    reject_result = reject_change_set(change_set["id"])
    check("reject succeeds after apply", reject_result["success"])
    check("reject restores file", target.read_text(encoding="utf-8") == "before\n")
    check("reject after apply marks reverted", reject_result["change_set"]["status"] == "reverted")

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    target = ws / "reject_then_accept.txt"
    target.write_text("alpha\n", encoding="utf-8")
    change_set = create_pending_change_set(make_plan(target, "alpha\n", "beta\n"), session_id="s_reject", source="file_edit")
    reject_result = reject_change_set(change_set["id"])
    check("reject pending succeeds", reject_result["success"])
    check("reject pending leaves file unchanged", target.read_text(encoding="utf-8") == "alpha\n")
    accept_result = accept_change_set(change_set["id"])
    check("accept rejected fails", not accept_result["success"])

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    target = ws / "rollback_review.txt"
    target.write_text("after\n", encoding="utf-8")
    review = create_pending_change_set(make_plan(target, "after\n", "before\n", tool_name="rollback_review"), session_id="s_review", source="rollback_review")
    check("rollback review type propagated", review["type"] == "rollback_review")

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    target = ws / "stale.txt"
    target.write_text("base\n", encoding="utf-8")
    change_set = create_pending_change_set(make_plan(target, "base\n", "planned\n"), session_id="s_stale", source="file_edit")
    target.write_text("external\n", encoding="utf-8")
    accept_result = accept_change_set(change_set["id"])
    check("accept stale fails", not accept_result["success"])
    check("accept stale mentions changed", "changed" in accept_result["error"].lower() or "stale" in accept_result["error"].lower())
    check("stale accept preserves external file", target.read_text(encoding="utf-8") == "external\n")

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    target = ws / "chain.txt"
    target.write_text("v1\n", encoding="utf-8")
    cs1 = create_pending_change_set(make_plan(target, "v1\n", "v2\n"), session_id="s_chain", source="file_edit")
    # Guard: creating a second pending change set for same session must fail
    try:
        create_pending_change_set(make_plan(target, "v1\n", "v3\n"), session_id="s_chain", source="file_edit")
        check("pending guard blocks second change set", False)
    except ValueError as e:
        check("pending guard blocks second change set", "pending change set" in str(e).lower())
    # Accept first, then create second (sequential workflow)
    accept1 = accept_change_set(cs1["id"])
    check("second transaction accepts", accept1["success"])
    check("second transaction writes v2", target.read_text(encoding="utf-8") == "v2\n")
    # Now a new change set can be created (cs1 is no longer pending)
    cs2 = create_pending_change_set(make_plan(target, "v2\n", "v3\n"), session_id="s_chain", source="file_edit")
    accept2 = accept_change_set(cs2["id"])
    check("chain second step accepts", accept2["success"])
    check("chain second step writes v3", target.read_text(encoding="utf-8") == "v3\n")

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    target = ws / "new_file.txt"
    plan = make_plan(target, "", "created\n", before_exists=False, after_exists=True, tool_name="file_write")
    change_set = create_pending_change_set(plan, session_id="s2", source="file_write")
    check("new file pending remains absent", not target.exists())
    accept_result = accept_change_set(change_set["id"])
    check("new file accept succeeds", accept_result["success"])
    check("new file written", target.exists() and target.read_text(encoding="utf-8") == "created\n")
    reject_result = reject_change_set(change_set["id"])
    check("new file reject succeeds", reject_result["success"])
    check("new file removed on reject", not target.exists())

with tempfile.TemporaryDirectory() as tmpdir:
    ws = Path(tmpdir)
    orphan_tmp = ws / ".nanobot_tx_test.tmp"
    orphan_bak = ws / ".nanobot_tx_test.bak"
    normal_tmp = ws / "keep.tmp"
    orphan_tmp.write_text("temp", encoding="utf-8")
    orphan_bak.write_text("backup", encoding="utf-8")
    normal_tmp.write_text("keep", encoding="utf-8")
    cleanup_result = cleanup_orphaned_tempfiles(ws)
    check("cleanup removes orphan tmp", str(orphan_tmp) in cleanup_result["removed"] and not orphan_tmp.exists())
    check("cleanup removes orphan bak", str(orphan_bak) in cleanup_result["removed"] and not orphan_bak.exists())
    check("cleanup preserves unrelated tmp", normal_tmp.exists())

# ── TOCTOU re-check: FileNotFoundError during lock-held reload ──
import edit_transaction as _et

with tempfile.TemporaryDirectory() as tmpdir:
    os.environ["NANOBOT_CHANGESET_DIR"] = os.path.join(tmpdir, "cs")
    os.makedirs(os.environ["NANOBOT_CHANGESET_DIR"])
    target = Path(tmpdir) / "race.txt"
    target.write_text("before", encoding="utf-8")
    cs = create_pending_change_set(
        {"path": str(target), "before_text": "before", "after_text": "after",
         "before_exists": True, "after_exists": True},
        session_id="race_test", source="test",
    )
    cs_id = cs["id"]
    # Monkey-patch to delete CS file on 2nd load (simulates TTL race during lock wait)
    _orig_load = _et._load_change_set
    _race_calls = [0]
    def _race_load(cid):
        _race_calls[0] += 1
        if _race_calls[0] == 2:
            os.unlink(_et._store_path(cid))
        return _orig_load(cid)
    _et._load_change_set = _race_load
    try:
        result = accept_change_set(cs_id)
    finally:
        _et._load_change_set = _orig_load
    check("TOCTOU race: FileNotFoundError returns structured error", not result["success"])
    check("TOCTOU race: error is generic (no internal detail)", "no longer available" in result.get("error", ""))

# ── Initial FileNotFoundError: accept and reject return generic message ──
result_accept_404 = accept_change_set("cs_nonexistent_id_12345")
check("accept nonexistent CS: returns failure", not result_accept_404["success"])
check("accept nonexistent CS: generic error", result_accept_404["error"] == "Change set not found")

result_reject_404 = reject_change_set("cs_nonexistent_id_12345")
check("reject nonexistent CS: returns failure", not result_reject_404["success"])
check("reject nonexistent CS: generic error", result_reject_404["error"] == "Change set not found")

# ── Concurrent accept/reject: state consistency under contention ──
import threading

for trial in range(5):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["NANOBOT_CHANGESET_DIR"] = os.path.join(tmpdir, "cs")
        os.makedirs(os.environ["NANOBOT_CHANGESET_DIR"])
        target = Path(tmpdir) / "concurrent.txt"
        target.write_text("original", encoding="utf-8")
        cs = create_pending_change_set(
            {"path": str(target), "before_text": "original", "after_text": "modified",
             "before_exists": True, "after_exists": True},
            session_id="conc_test", source="test",
        )
        cs_id = cs["id"]
        results = [None, None]
        barrier = threading.Barrier(2)

        def do_accept():
            barrier.wait()
            results[0] = accept_change_set(cs_id)

        def do_reject():
            barrier.wait()
            results[1] = reject_change_set(cs_id)

        t1 = threading.Thread(target=do_accept)
        t2 = threading.Thread(target=do_reject)
        t1.start(); t2.start()
        t1.join(timeout=5); t2.join(timeout=5)

        # Exactly one must succeed with the operation it intended
        a_ok = results[0] and results[0].get("success")
        r_ok = results[1] and results[1].get("success")

        # Both may succeed (accept applied, then reject reverted) or
        # one succeeds and the other fails — but no deadlock and no crash
        check(f"concurrent trial {trial}: no deadlock (threads joined)", not t1.is_alive() and not t2.is_alive())
        check(f"concurrent trial {trial}: both returned results", results[0] is not None and results[1] is not None)

        # Final CS state must be consistent: load and verify
        final_cs = _et._load_change_set(cs_id)
        final_status = final_cs.get("status")
        valid_terminal = final_status in ("applied", "rejected", "reverted")
        check(f"concurrent trial {trial}: terminal state ({final_status})", valid_terminal)

        # File system must match metadata
        file_content = target.read_text(encoding="utf-8") if target.exists() else None
        if final_status == "applied":
            check(f"concurrent trial {trial}: file matches applied state", file_content == "modified")
        elif final_status in ("rejected",):
            # rejected from pending = file unchanged
            check(f"concurrent trial {trial}: file unchanged for rejected-from-pending", file_content == "original")
        elif final_status == "reverted":
            check(f"concurrent trial {trial}: file reverted to original", file_content == "original")

if __name__ == "__main__":
    print(f"\n{'=' * 50}")
    print(f"Results: {PASS} passed, {FAIL} failed")
    print(f"{'=' * 50}")
    if FAIL == 0:
        print("🎉 All tests passed!")
    else:
        print(f"⚠️  {FAIL} test(s) failed")
        sys.exit(1)
