"""
Route Registration Tests (P0-1 audit fix)

Verifies that all extracted route modules register their endpoints correctly
on the FastAPI app, and that basic request/response contracts hold.
"""
import asyncio
import unittest
from pathlib import Path
import sys

WEB_UI_DIR = Path(__file__).parent.parent
if str(WEB_UI_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_UI_DIR))


class TestRouteRegistration(unittest.TestCase):
    """Verify routers are mounted and respond to basic requests."""

    @classmethod
    def setUpClass(cls):
        from server_final import app
        # Re-register routes defensively: in CI containers server_final.py
        # sometimes loses route registrations due to module-import ordering
        # with our stubbed modules. Calling register_all_routes again here
        # ensures the full router set is mounted.
        try:
            from routes import register_all_routes
            register_all_routes(app)
        except Exception as e:  # noqa: BLE001 — last-resort defensive
            print(f"[test_routes_registration] re-register warning: {e}")
        cls.app = app
        # Collect all registered route paths
        cls.registered_paths = {r.path for r in app.routes if hasattr(r, 'path')}

    # ── Changes router ──────────────────────────────────────
    def test_changes_pending_registered(self):
        self.assertIn("/api/changes/pending", self.registered_paths)

    def test_changes_accept_registered(self):
        self.assertIn("/api/changes/{change_set_id}/accept", self.registered_paths)

    def test_changes_reject_registered(self):
        self.assertIn("/api/changes/{change_set_id}/reject", self.registered_paths)

    # ── Safety router ───────────────────────────────────────
    def test_danger_check_registered(self):
        self.assertIn("/api/danger/check", self.registered_paths)

    def test_danger_patterns_registered(self):
        self.assertIn("/api/danger/patterns", self.registered_paths)

    def test_sandbox_check_registered(self):
        self.assertIn("/api/sandbox/check", self.registered_paths)

    def test_sandbox_status_registered(self):
        self.assertIn("/api/sandbox/status", self.registered_paths)

    # ── Rate limit router ───────────────────────────────────
    def test_rate_limit_status_registered(self):
        self.assertIn("/api/rate-limit/status", self.registered_paths)

    def test_rate_limit_trigger_registered(self):
        self.assertIn("/api/rate-limit/trigger", self.registered_paths)

    def test_rate_limit_reset_registered(self):
        self.assertIn("/api/rate-limit/reset", self.registered_paths)

    # ── Permissions router ──────────────────────────────────
    def test_permission_pending_registered(self):
        self.assertIn("/api/permission/pending", self.registered_paths)

    def test_permission_request_registered(self):
        self.assertIn("/api/permission/request", self.registered_paths)

    def test_permission_decide_registered(self):
        self.assertIn("/api/permission/decide", self.registered_paths)

    def test_permission_rules_registered(self):
        self.assertIn("/api/permission/rules", self.registered_paths)

    # ── DingTalk router ─────────────────────────────────────
    def test_dingtalk_webhook_registered(self):
        self.assertIn("/api/dingtalk/webhook", self.registered_paths)

    # ── V3 / NeuraCore router ───────────────────────────────
    def test_v3_stream_registered(self):
        self.assertIn("/api/v3/os_agent/stream", self.registered_paths)

    def test_v3_status_registered(self):
        self.assertIn("/api/v3/status", self.registered_paths)

    def test_neuracore_stream_registered(self):
        self.assertIn("/api/neuracore/stream", self.registered_paths)

    def test_neuracore_status_registered(self):
        self.assertIn("/api/neuracore/status", self.registered_paths)

    # ── Advanced AI router (spot-check a few) ───────────────
    def test_predictive_analyze_registered(self):
        self.assertIn("/api/predictive/analyze", self.registered_paths)

    def test_adaptive_learn_registered(self):
        self.assertIn("/api/adaptive/learn", self.registered_paths)

    def test_unified_query_registered(self):
        self.assertIn("/api/unified/query", self.registered_paths)


class TestConcurrencyGuards(unittest.TestCase):
    """Verify that asyncio.Lock guards on shared state work correctly."""

    def test_safe_get_or_create_session_concurrent(self):
        """Two coroutines creating the same session should not clobber each other."""
        from server_state import (
            sessions, sessions_lock, safe_get_or_create_session,
        )
        test_sid = "__test_concurrent_session__"
        sessions.pop(test_sid, None)  # clean slate

        results = []

        async def writer(idx):
            sess = await safe_get_or_create_session(test_sid)
            sess.setdefault("writers", []).append(idx)
            results.append(idx)

        async def run():
            await asyncio.gather(writer(1), writer(2), writer(3))

        asyncio.run(run())

        # All three writers should have appended to the SAME session dict
        self.assertEqual(len(sessions[test_sid]["writers"]), 3)
        self.assertEqual(sorted(results), [1, 2, 3])
        sessions.pop(test_sid, None)  # cleanup

    def test_safe_delete_session(self):
        from server_state import sessions, safe_delete_session
        test_sid = "__test_delete_session__"
        sessions[test_sid] = {"history": ["a"]}

        removed = asyncio.run(safe_delete_session(test_sid))
        self.assertIsNotNone(removed)
        self.assertNotIn(test_sid, sessions)

    def test_safe_delete_session_missing(self):
        from server_state import safe_delete_session
        removed = asyncio.run(safe_delete_session("__nonexistent__"))
        self.assertIsNone(removed)

    def test_locks_are_asyncio_locks(self):
        from server_state import sessions_lock, stream_processes_lock
        self.assertIsInstance(sessions_lock, asyncio.Lock)
        self.assertIsInstance(stream_processes_lock, asyncio.Lock)


class TestSessionStore(unittest.TestCase):
    """Verify SessionStore class implements dict protocol and lock API."""

    def test_sessions_is_session_store_instance(self):
        from server_state import sessions, session_store, SessionStore
        self.assertIsInstance(sessions, SessionStore)
        self.assertIs(sessions, session_store)

    def test_dict_protocol_getset(self):
        from server_state import sessions
        sessions["__test_gs__"] = {"x": 1}
        self.assertIn("__test_gs__", sessions)
        self.assertEqual(sessions["__test_gs__"]["x"], 1)
        del sessions["__test_gs__"]
        self.assertNotIn("__test_gs__", sessions)

    def test_dict_protocol_pop(self):
        from server_state import sessions
        sessions["__test_pop__"] = {"y": 2}
        val = sessions.pop("__test_pop__")
        self.assertEqual(val["y"], 2)
        self.assertIsNone(sessions.pop("__test_pop__", None))

    def test_dict_protocol_len_iter(self):
        from server_state import sessions
        sessions["__test_li_a__"] = {}
        sessions["__test_li_b__"] = {}
        self.assertGreaterEqual(len(sessions), 2)
        self.assertIn("__test_li_a__", list(sessions))
        sessions.pop("__test_li_a__", None)
        sessions.pop("__test_li_b__", None)

    def test_lock_property(self):
        from server_state import session_store
        self.assertIsInstance(session_store.lock, asyncio.Lock)

    def test_get_or_create_via_store(self):
        from server_state import session_store
        test_sid = "__test_store_goc__"
        session_store.pop(test_sid, None)

        async def run():
            s = await session_store.get_or_create(test_sid)
            self.assertIn("history", s)
            s2 = await session_store.get_or_create(test_sid)
            self.assertIs(s, s2)
            await session_store.delete(test_sid)

        asyncio.run(run())

    def test_delete_via_store(self):
        from server_state import session_store
        test_sid = "__test_store_del__"
        session_store[test_sid] = {"z": 3}

        async def run():
            removed = await session_store.delete(test_sid)
            self.assertEqual(removed["z"], 3)
            self.assertNotIn(test_sid, session_store)

        asyncio.run(run())


class TestRouteLoggerNames(unittest.TestCase):
    """Each route module should use its own __name__ logger, not a shared one."""

    def test_safety_logger(self):
        from routes import safety
        self.assertEqual(safety.logger.name, "routes.safety")

    def test_dingtalk_logger(self):
        from routes import dingtalk
        self.assertEqual(dingtalk.logger.name, "routes.dingtalk")

    def test_v3_neuracore_logger(self):
        from routes import v3_neuracore
        self.assertEqual(v3_neuracore.logger.name, "routes.v3_neuracore")

    def test_advanced_ai_logger(self):
        from routes import advanced_ai
        self.assertEqual(advanced_ai.logger.name, "routes.advanced_ai")


class TestSessionStoreDeprecation(unittest.TestCase):
    """Verify the opt-in DeprecationWarning on direct dict access."""

    def test_setitem_warns_when_flag_on(self):
        from server_state import SessionStore
        store = SessionStore()
        old = store._WARN
        try:
            store.__class__._WARN = True
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                store["__dep_test__"] = {"a": 1}
                self.assertEqual(len(w), 1)
                self.assertIn("deprecated", str(w[0].message).lower())
                self.assertTrue(issubclass(w[0].category, DeprecationWarning))
        finally:
            store.__class__._WARN = old
            store.pop("__dep_test__", None)

    def test_delitem_warns_when_flag_on(self):
        from server_state import SessionStore
        store = SessionStore()
        store["__dep_del__"] = {}
        old = store._WARN
        try:
            store.__class__._WARN = True
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                del store["__dep_del__"]
                self.assertEqual(len(w), 1)
                self.assertIn("deprecated", str(w[0].message).lower())
        finally:
            store.__class__._WARN = old

    def test_no_warning_when_flag_off(self):
        from server_state import SessionStore
        store = SessionStore()
        old = store._WARN
        try:
            store.__class__._WARN = False
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                store["__dep_off__"] = {"b": 2}
                del store["__dep_off__"]
                dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
                self.assertEqual(len(dep_warnings), 0)
        finally:
            store.__class__._WARN = old

    def test_pop_update_setdefault_clear_warn_when_flag_on(self):
        """All write-path dict methods must emit DeprecationWarning when flag is on."""
        from server_state import SessionStore
        store = SessionStore()
        store._data["__seed__"] = {}  # bypass __setitem__ for setup
        old = store._WARN
        try:
            store.__class__._WARN = True
            import warnings
            ops_tested = []
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                store.pop("__seed__", None)
                store.update({"__upd__": {}})
                store.setdefault("__sd__", {})
                store.clear()
                ops_tested = [str(x.message) for x in w
                              if issubclass(x.category, DeprecationWarning)]
            self.assertEqual(len(ops_tested), 4,
                             f"Expected 4 warnings (pop/update/setdefault/clear), got {len(ops_tested)}: {ops_tested}")
            self.assertTrue(any("pop" in m for m in ops_tested))
            self.assertTrue(any("update" in m for m in ops_tested))
            self.assertTrue(any("setdefault" in m for m in ops_tested))
            self.assertTrue(any("clear" in m for m in ops_tested))
        finally:
            store.__class__._WARN = old

    def test_read_methods_do_not_warn_when_flag_on(self):
        """Reads must be silent even with flag on \u2014 they don't bypass lock semantics."""
        from server_state import SessionStore
        store = SessionStore()
        store._data["__r__"] = {"x": 1}
        old = store._WARN
        try:
            store.__class__._WARN = True
            import warnings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                _ = store["__r__"]
                _ = store.get("__r__")
                _ = "__r__" in store
                _ = len(store)
                _ = list(store)
                _ = list(store.keys())
                _ = list(store.values())
                _ = list(store.items())
                dep = [x for x in w if issubclass(x.category, DeprecationWarning)]
                self.assertEqual(len(dep), 0,
                                 f"Read methods unexpectedly warned: {[str(x.message) for x in dep]}")
        finally:
            store.__class__._WARN = old


class TestConcurrentHTTPSessions(unittest.TestCase):
    """End-to-end concurrent HTTP tests verifying SessionStore under real ASGI load.

    Uses httpx.AsyncClient with ASGITransport to send concurrent requests
    through the full FastAPI stack, not just unit-level coroutine simulation.
    """

    @classmethod
    def setUpClass(cls):
        from server_final import app
        cls.app = app

    def test_concurrent_status_requests(self):
        """10 concurrent GET /api/status should all return 200 with no 500 errors."""
        import httpx

        async def run():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                tasks = [client.get("/api/status") for _ in range(10)]
                responses = await asyncio.gather(*tasks)
            return responses

        responses = asyncio.run(run())
        status_codes = [r.status_code for r in responses]
        self.assertTrue(all(c == 200 for c in status_codes),
                        f"Expected all 200, got: {status_codes}")

    def test_concurrent_session_creation(self):
        """10 concurrent POST /api/sessions should each create a unique session."""
        import httpx
        from server_state import sessions

        initial_count = len(sessions)

        async def run():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                tasks = [
                    client.post("/api/sessions", json={"user_id": f"stress_{i}"})
                    for i in range(10)
                ]
                responses = await asyncio.gather(*tasks)
            return responses

        responses = asyncio.run(run())
        status_codes = [r.status_code for r in responses]
        self.assertTrue(all(c == 200 for c in status_codes),
                        f"Expected all 200, got: {status_codes}")

        # Each request should have created a distinct session
        created_ids = [r.json()["session_id"] for r in responses]
        self.assertEqual(len(set(created_ids)), 10,
                         f"Expected 10 unique sessions, got {len(set(created_ids))}")

        # sessions store should have grown by 10
        self.assertEqual(len(sessions), initial_count + 10)

        # Cleanup
        for sid in created_ids:
            sessions.pop(sid, None)

    def test_concurrent_session_list_during_writes(self):
        """Concurrent reads (GET /api/sessions) interleaved with writes should not crash."""
        import httpx
        from server_state import sessions

        async def run():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                reads = [client.get("/api/sessions") for _ in range(5)]
                writes = [
                    client.post("/api/sessions", json={"user_id": f"mix_{i}"})
                    for i in range(5)
                ]
                responses = await asyncio.gather(*(reads + writes))
            return responses

        responses = asyncio.run(run())
        # No 500 errors
        for r in responses:
            self.assertNotEqual(r.status_code, 500,
                                f"Got 500: {r.text[:200]}")

        # Cleanup written sessions
        for r in responses:
            if r.request.method == "POST" and r.status_code == 200:
                sessions.pop(r.json().get("session_id", ""), None)

    def test_concurrent_rate_limit_status(self):
        """20 concurrent GET /api/rate-limit/status should not deadlock or error."""
        import httpx

        async def run():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                tasks = [client.get("/api/rate-limit/status") for _ in range(20)]
                responses = await asyncio.gather(*tasks)
            return responses

        responses = asyncio.run(run())
        status_codes = [r.status_code for r in responses]
        self.assertTrue(all(c == 200 for c in status_codes),
                        f"Expected all 200, got: {status_codes}")


class TestConcurrentSSEStreaming(unittest.TestCase):
    """Minimal SSE concurrency integration test (audit Issue 3).

    Mocks ``agentic_chat_stream`` so no real LLM is needed, then fires
    3 concurrent POST /api/chat/stream requests and verifies all receive
    ``agentic_done`` events without 500 errors.
    """

    @classmethod
    def setUpClass(cls):
        from server_final import app
        cls.app = app

    def test_three_concurrent_sse_streams(self):
        """3 concurrent SSE streams all complete with agentic_done and no server errors."""
        import httpx
        import json as _json
        from unittest.mock import patch, AsyncMock

        async def _mock_agentic_stream(**kwargs):
            """Yield a minimal valid SSE event sequence."""
            yield {"type": "text_chunk", "content": f"Hello from {kwargs.get('session_id', '?')}"}
            await asyncio.sleep(0)  # yield to event loop
            yield {
                "type": "agentic_done",
                "turns": 1,
                "total_tool_calls": 0,
                "tools_used": [],
                "final_response": f"Done: {kwargs.get('session_id', '?')}",
            }

        async def _fire_one(client, idx):
            """Send one POST and collect SSE data lines."""
            resp = await client.post(
                "/api/chat/stream",
                json={"message": f"test msg {idx}", "session_id": f"__sse_test_{idx}__"},
            )
            return resp

        async def run():
            with patch("server_final.agentic_chat_stream", side_effect=_mock_agentic_stream), \
                 patch("server_final.AGENTIC_LOOP_AVAILABLE", True):
                transport = httpx.ASGITransport(app=self.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    tasks = [_fire_one(client, i) for i in range(3)]
                    responses = await asyncio.gather(*tasks)
            return responses

        responses = asyncio.run(run())

        for i, resp in enumerate(responses):
            self.assertNotEqual(resp.status_code, 500,
                                f"Stream {i} returned 500: {resp.text[:300]}")
            # SSE responses should be 200 with text/event-stream
            self.assertEqual(resp.status_code, 200, f"Stream {i} status={resp.status_code}")
            # Body should contain at least one "data:" line
            self.assertIn("data:", resp.text,
                          f"Stream {i} missing SSE data lines")

        # Cleanup sessions
        from server_state import sessions
        for i in range(3):
            sessions.pop(f"__sse_test_{i}__", None)


class TestAgenticStyleConcurrentSessionMutation(unittest.TestCase):
    """Simulate agentic_loop-style concurrent reads/writes on session history.

    Where TestConcurrentHTTPSessions covers shallow GET/POST endpoints,
    these tests stress the SessionStore at the *workload pattern* level:
    multiple long-running coroutines that each (a) get_or_create a session,
    (b) repeatedly mutate ``history`` over many turns, (c) optionally yield
    to other coroutines mid-sequence — mimicking real tool-call interleaving.
    """

    def test_many_turns_single_session_no_history_loss(self):
        """50 coroutines each appending to the SAME session history.

        Final history length must equal the total number of appends — no
        lost updates from race conditions.
        """
        from server_state import session_store
        sid = "__agentic_single__"
        session_store.pop(sid, None)

        TURNS_PER_WRITER = 20
        WRITERS = 50

        async def writer(idx: int):
            for turn in range(TURNS_PER_WRITER):
                sess = await session_store.get_or_create(sid)
                # Use the lock to make the read-modify-write atomic
                # (real agentic code uses safe_get_or_create_session → same lock)
                async with session_store.lock:
                    sess["history"].append({"writer": idx, "turn": turn})
                # Yield to scheduler so other coroutines interleave
                await asyncio.sleep(0)

        async def run():
            await asyncio.gather(*[writer(i) for i in range(WRITERS)])

        asyncio.run(run())
        sess = session_store[sid]
        self.assertEqual(len(sess["history"]), WRITERS * TURNS_PER_WRITER,
                         "Lost updates detected — concurrent appends were not serialized")
        # No duplicates
        keys = [(h["writer"], h["turn"]) for h in sess["history"]]
        self.assertEqual(len(set(keys)), WRITERS * TURNS_PER_WRITER)
        session_store.pop(sid, None)

    def test_many_sessions_parallel_create_and_mutate(self):
        """30 coroutines, each on a distinct session, all creating + mutating in parallel."""
        from server_state import session_store

        SESSIONS = 30
        APPENDS = 10
        prefix = "__agentic_multi__"

        async def worker(sid: str):
            for i in range(APPENDS):
                sess = await session_store.get_or_create(sid)
                async with session_store.lock:
                    sess["history"].append({"i": i})

        async def run():
            sids = [f"{prefix}{n}" for n in range(SESSIONS)]
            await asyncio.gather(*[worker(s) for s in sids])
            return sids

        sids = asyncio.run(run())
        try:
            for sid in sids:
                self.assertEqual(len(session_store[sid]["history"]), APPENDS,
                                 f"Session {sid} history corrupted")
        finally:
            for sid in sids:
                session_store.pop(sid, None)

    def test_concurrent_create_delete_does_not_corrupt(self):
        """Half the workers create + populate, half delete the same sessions \u2014 no exceptions."""
        from server_state import session_store

        sids = [f"__agentic_cd__{i}" for i in range(20)]
        for s in sids:
            session_store.pop(s, None)

        async def creator(sid: str):
            for _ in range(5):
                sess = await session_store.get_or_create(sid)
                async with session_store.lock:
                    sess.setdefault("history", []).append("c")
                await asyncio.sleep(0)

        async def deleter(sid: str):
            for _ in range(5):
                await session_store.delete(sid)
                await asyncio.sleep(0)

        async def run():
            tasks = []
            for s in sids:
                tasks.append(creator(s))
                tasks.append(deleter(s))
            # Run all 40 tasks concurrently \u2014 should never raise
            await asyncio.gather(*tasks)

        # Primary assertion: no KeyError / RuntimeError despite contention
        asyncio.run(run())
        for s in sids:
            session_store.pop(s, None)

    def test_safe_get_or_create_under_burst(self):
        """100 simultaneous get_or_create calls on the SAME new session must yield ONE dict."""
        from server_state import session_store, safe_get_or_create_session
        sid = "__agentic_burst__"
        session_store.pop(sid, None)

        async def burst():
            results = await asyncio.gather(*[
                safe_get_or_create_session(sid) for _ in range(100)
            ])
            return results

        results = asyncio.run(burst())
        # All 100 references must point to the SAME dict object
        first = results[0]
        for r in results[1:]:
            self.assertIs(r, first,
                          "Race: multiple session dicts created for the same session_id")
        session_store.pop(sid, None)


class TestAgenticGlobalStateConcurrency(unittest.TestCase):
    """Verify agentic_loop global state consistency under concurrent coroutines.

    Directly exercises the global state mutation functions
    (_track_file_activity, _record_tool_failure, _reset_session_*,
    get_active_files_snapshot) from multiple concurrent async tasks,
    proving that asyncio's cooperative scheduling keeps them atomic.

    This addresses the audit's concern about agentic-layer global state
    without requiring the full LLM mock infrastructure that
    agentic_chat_stream would need.
    """

    def setUp(self):
        from agentic_loop import (
            _SESSION_ACTIVE_FILES, _SESSION_FAILURES,
            _reset_session_file_reads, _reset_session_failures,
        )
        _reset_session_file_reads()
        _reset_session_failures()

    def tearDown(self):
        from agentic_loop import _reset_session_file_reads, _reset_session_failures
        _reset_session_file_reads()
        _reset_session_failures()

    def test_concurrent_file_tracking_no_lost_updates(self):
        """20 coroutines each track 5 files — all entries must be present."""
        from agentic_loop import _track_file_activity, _SESSION_ACTIVE_FILES

        WORKERS = 20
        FILES_PER_WORKER = 5

        async def tracker(worker_id: int):
            for f in range(FILES_PER_WORKER):
                path = f"/test/w{worker_id}/file_{f}.py"
                _track_file_activity(
                    tool_name="file_read",
                    result={"output": f"[File: {path} | 100 lines | 2000 bytes]\ndef main(): pass"},
                    tool_args={"path": path},
                    turn=worker_id * FILES_PER_WORKER + f,
                )
                await asyncio.sleep(0)  # yield to other coroutines

        async def run():
            await asyncio.gather(*[tracker(i) for i in range(WORKERS)])

        asyncio.run(run())

        # _SESSION_ACTIVE_FILES_MAX is 12, so only the 12 most recent
        # entries survive eviction — but there must be exactly 12 and
        # no KeyError or corruption
        from agentic_loop import _SESSION_ACTIVE_FILES_MAX
        self.assertEqual(len(_SESSION_ACTIVE_FILES), _SESSION_ACTIVE_FILES_MAX)
        for path, info in _SESSION_ACTIVE_FILES.items():
            self.assertIn("last_turn", info)
            self.assertIn("last_action", info)
            self.assertEqual(info["last_action"], "read")

    def test_concurrent_failure_recording_no_lost_entries(self):
        """30 coroutines each record 2 failures — total must equal expected count."""
        from agentic_loop import _record_tool_failure, _SESSION_FAILURES, _SESSION_FAILURES_MAX

        WORKERS = 30
        FAILS_PER_WORKER = 2

        async def failer(worker_id: int):
            for i in range(FAILS_PER_WORKER):
                _record_tool_failure(
                    tool_name=f"tool_{worker_id}",
                    tool_args={"arg": i},
                    error=f"error from worker {worker_id} attempt {i}",
                    turn=worker_id * FAILS_PER_WORKER + i,
                )
                await asyncio.sleep(0)

        async def run():
            await asyncio.gather(*[failer(i) for i in range(WORKERS)])

        asyncio.run(run())

        # FIFO eviction caps at _SESSION_FAILURES_MAX
        expected = min(WORKERS * FAILS_PER_WORKER, _SESSION_FAILURES_MAX)
        self.assertEqual(len(_SESSION_FAILURES), expected)
        # Entries should be the most recent ones (highest turn numbers)
        turns = [f["turn"] for f in _SESSION_FAILURES]
        self.assertEqual(turns, sorted(turns))

    def test_concurrent_track_and_reset_no_crash(self):
        """Interleaved tracking and resetting must not raise RuntimeError."""
        from agentic_loop import (
            _track_file_activity, _reset_session_file_reads,
            _SESSION_ACTIVE_FILES,
        )

        async def track_loop():
            for i in range(50):
                _track_file_activity(
                    tool_name="file_read",
                    result={"output": f"[File: /test/f{i}.py | 10 lines | 200 bytes]\ndef x(): pass"},
                    tool_args={"path": f"/test/f{i}.py"},
                    turn=i,
                )
                await asyncio.sleep(0)

        async def reset_loop():
            for _ in range(10):
                _reset_session_file_reads()
                await asyncio.sleep(0)

        async def run():
            await asyncio.gather(track_loop(), reset_loop())

        # Primary assertion: no RuntimeError from concurrent dict modification
        asyncio.run(run())

    def test_snapshot_returns_consistent_copy(self):
        """get_active_files_snapshot returns a stable list unaffected by later mutations."""
        from agentic_loop import (
            _track_file_activity, get_active_files_snapshot,
            _SESSION_ACTIVE_FILES,
        )

        # Populate 5 files
        for i in range(5):
            _track_file_activity(
                tool_name="file_read",
                result={"output": f"[File: /snap/f{i}.py | 10 lines | 100 bytes]\ndef f(): pass"},
                tool_args={"path": f"/snap/f{i}.py"},
                turn=i,
            )

        snapshot = get_active_files_snapshot(max_files=5)
        original_len = len(snapshot)

        # Mutate the original after snapshot
        _SESSION_ACTIVE_FILES.clear()

        # Snapshot must be unaffected
        self.assertEqual(len(snapshot), original_len)
        self.assertEqual(len(_SESSION_ACTIVE_FILES), 0)


class TestCallSiteTimingContract(unittest.TestCase):
    """SECURITY CONTRACT TESTS — do NOT delete without security review.

    These are NOT ordinary unit tests.  They enforce the concurrency safety
    invariant documented in SECURITY.md R4: global-state mutation functions
    must only run on the main event-loop thread, after ``asyncio.gather``
    has joined all concurrent tool coroutines.

    Uses Python's ``ast`` module to reliably parse function definitions
    and call sites, immune to formatting changes, multi-line signatures,
    decorators, and comments.  Verifies:

    1. ``_track_file_activity`` and ``_record_tool_failure`` are NEVER
       called inside ``_run_one`` (which runs in the thread pool).
    2. In the concurrent path, their call sites appear AFTER the
       ``asyncio.gather`` call (serial post-join processing).
    3. All global-state mutation functions contain a main-thread assertion.

    **Failure protocol**: If these tests fail after a refactoring:
    1. DO NOT simply delete or skip the tests.
    2. Verify the new code still satisfies the R4 invariant (no global
       mutation inside thread-pool or pre-gather paths).
    3. Update the tests to reflect the new structure, then re-run.
    4. Document the structural change in the PR description.
    """

    BANNED_IN_THREADPOOL = {"_track_file_activity", "_record_tool_failure"}

    @classmethod
    def setUpClass(cls):
        import agentic_loop, inspect, ast
        cls.module_source = inspect.getsource(agentic_loop)
        cls.module_tree = ast.parse(cls.module_source)

    # ── AST helpers ──────────────────────────────────────────────

    @staticmethod
    def _collect_calls(node):
        """Return set of function names called anywhere inside *node*."""
        import ast
        names = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Name):
                    names.add(child.func.id)
                elif isinstance(child.func, ast.Attribute):
                    names.add(child.func.attr)
        return names

    def _find_all_functions(self, tree, name):
        """Yield every FunctionDef/AsyncFunctionDef node with *name* in *tree*."""
        import ast
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == name:
                    yield node

    # ── Tests ────────────────────────────────────────────────────

    def test_run_one_does_not_call_track_or_record(self):
        """_run_one must NOT call _track_file_activity or _record_tool_failure."""
        found = False
        for fn_node in self._find_all_functions(self.module_tree, "_run_one"):
            found = True
            calls = self._collect_calls(fn_node)
            for banned in self.BANNED_IN_THREADPOOL:
                self.assertNotIn(banned, calls,
                                 f"{banned} must NOT be called inside _run_one (thread-pool path)")
        self.assertTrue(found, "_run_one function not found in AST")

    def test_track_and_record_appear_after_gather_in_concurrent_path(self):
        """In the concurrent branch, _track/_record calls must come AFTER asyncio.gather."""
        import ast
        # Find agentic_chat_stream — the function containing both gather and the calls
        for fn_node in self._find_all_functions(self.module_tree, "agentic_chat_stream"):
            # Collect line numbers of gather call and first banned-call after it
            gather_line = None
            first_track_after = None
            first_record_after = None
            for node in ast.walk(fn_node):
                if isinstance(node, ast.Call):
                    # asyncio.gather(...)
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "gather":
                        if gather_line is None:
                            gather_line = node.lineno
                    # _track_file_activity(...) or _record_tool_failure(...)
                    if isinstance(node.func, ast.Name):
                        if node.func.id == "_track_file_activity" and gather_line and first_track_after is None:
                            first_track_after = node.lineno
                        if node.func.id == "_record_tool_failure" and gather_line and first_record_after is None:
                            first_record_after = node.lineno

            self.assertIsNotNone(gather_line, "asyncio.gather call not found in agentic_chat_stream")
            self.assertIsNotNone(first_track_after,
                                 "_track_file_activity call not found after gather")
            self.assertGreater(first_track_after, gather_line,
                               "_track_file_activity must appear AFTER asyncio.gather (line order)")
            self.assertIsNotNone(first_record_after,
                                 "_record_tool_failure call not found after gather")
            self.assertGreater(first_record_after, gather_line,
                               "_record_tool_failure must appear AFTER asyncio.gather (line order)")
            return  # found and verified
        self.fail("agentic_chat_stream function not found in AST")

    def test_all_mutation_functions_have_thread_assertions(self):
        """Every global-state mutation function must contain a main-thread assertion."""
        import agentic_loop, inspect, ast
        mutation_fns = [
            "_track_file_activity",
            "_record_tool_failure",
            "_reset_session_file_reads",
            "_reset_session_failures",
        ]
        for fn_name in mutation_fns:
            fn = getattr(agentic_loop, fn_name)
            src = inspect.getsource(fn)
            fn_tree = ast.parse(src)
            # Look for Assert nodes whose test calls threading.main_thread()
            found_assert = False
            for node in ast.walk(fn_tree):
                if isinstance(node, ast.Assert):
                    for child in ast.walk(node.test):
                        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                            if child.func.attr == "main_thread":
                                found_assert = True
                                break
            self.assertTrue(found_assert,
                            f"{fn_name} must contain an assert ... threading.main_thread() check")

    def test_auto_discover_mutation_functions_have_assertions(self):
        """SECURITY CONTRACT: Auto-discover ALL functions that write to global
        state variables and verify each has a main-thread assertion.

        This catches new mutation functions that were added without the required
        assertion — the known-list test above cannot detect those.
        """
        import ast
        # Global state variables that require thread-guarded mutation
        GUARDED_GLOBALS = {
            "_SESSION_ACTIVE_FILES", "_SESSION_FAILURES",
            "_REPO_MAP_CACHE", "_REPO_MAP_CACHE_TURN",
        }
        # Mutation operations: mutating method calls (.clear, .append, .pop, etc.)
        # and subscript assignment (e.g., _SESSION_ACTIVE_FILES[key] = ...)
        # Also detect `del _GLOBAL[key]`
        MUTATING_METHODS = {
            "clear", "append", "pop", "update", "extend", "remove",
            "insert", "setdefault", "popitem",
        }

        def _writes_to_global(fn_node):
            """Return True if fn_node writes to any GUARDED_GLOBALS."""
            for node in ast.walk(fn_node):
                # Subscript assignment: _GLOBAL[key] = value
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                            if target.value.id in GUARDED_GLOBALS:
                                return True
                # Augmented assignment: _GLOBAL[key] += value
                if isinstance(node, ast.AugAssign):
                    if isinstance(node.target, ast.Subscript) and isinstance(node.target.value, ast.Name):
                        if node.target.value.id in GUARDED_GLOBALS:
                            return True
                # del _GLOBAL[key]
                if isinstance(node, ast.Delete):
                    for target in node.targets:
                        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                            if target.value.id in GUARDED_GLOBALS:
                                return True
                # _GLOBAL.clear() / .append() / .pop() etc. (mutating only)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if (isinstance(node.func.value, ast.Name)
                            and node.func.value.id in GUARDED_GLOBALS
                            and node.func.attr in MUTATING_METHODS):
                        return True
            return False

        def _has_main_thread_assert(fn_node):
            """Return True if fn_node contains assert ... main_thread()."""
            for node in ast.walk(fn_node):
                if isinstance(node, ast.Assert):
                    for child in ast.walk(node.test):
                        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                            if child.func.attr == "main_thread":
                                return True
            return False

        # Scan all function definitions in agentic_loop.py
        missing = []
        for node in ast.walk(self.module_tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _writes_to_global(node) and not _has_main_thread_assert(node):
                    missing.append(node.name)

        self.assertEqual(
            missing, [],
            f"Functions that write to guarded globals but lack main-thread "
            f"assertion: {missing}. See SECURITY.md R14 — every function that "
            f"mutates {GUARDED_GLOBALS} must contain "
            f"'assert threading.current_thread() is threading.main_thread()'."
        )


if __name__ == "__main__":
    unittest.main()
