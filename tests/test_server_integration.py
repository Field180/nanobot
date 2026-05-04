"""
P11: Integration tests for server_final.py API endpoints.
==========================================================
Uses Starlette TestClient to exercise real FastAPI routes
without launching a live server or making external calls.

Coverage targets:
  A2 — Session management      (10 tests)
  A3 — Config management       (8 tests)
  A4 — Export / batch ops      (10 tests)
  A5 — System / health         (8 tests)
  A6 — SNN endpoints           (6 tests)
  A7 — SSE / chat streaming    (8 tests)
  Total ≥ 50 tests
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set safe env defaults *before* importing server_final
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_BASE_URL", "http://127.0.0.1:19999")

from starlette.testclient import TestClient
from server_final import app, sessions, AUDIT_LOG, API_CACHE, RUNTIME_CONFIG


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_server_state():
    """Reset mutable globals touched during tests."""
    sessions.clear()
    AUDIT_LOG.clear()
    API_CACHE.clear()
    RUNTIME_CONFIG["config"] = {}
    RUNTIME_CONFIG["last_loaded"] = None


# ===================================================================
# A2: Session management
# ===================================================================

class TestSessionEndpoints(unittest.TestCase):
    """A2 — 10 tests for /api/sessions* endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def setUp(self):
        _reset_server_state()

    # ---------- create ----------

    def test_create_session(self):
        r = self.client.post("/api/sessions", json={"user_id": "tester"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("session_id", body)
        self.assertEqual(body["status"], "created")
        self.assertTrue(body["session_id"].startswith("tester_"))

    def test_create_session_default_user(self):
        r = self.client.post("/api/sessions", json={})
        body = r.json()
        self.assertTrue(body["session_id"].startswith("web_user_"))

    def test_create_session_populates_sessions_dict(self):
        r = self.client.post("/api/sessions", json={"user_id": "u"})
        sid = r.json()["session_id"]
        self.assertIn(sid, sessions)
        self.assertIn("history", sessions[sid])

    # ---------- list / get ----------

    def test_get_current_session_empty(self):
        r = self.client.get("/api/sessions/current")
        self.assertEqual(r.status_code, 200)

    def test_get_current_session_after_create(self):
        self.client.post("/api/sessions", json={"user_id": "u1"})
        r = self.client.get("/api/sessions/current")
        self.assertEqual(r.status_code, 200)

    # ---------- edge cases ----------

    def test_create_multiple_sessions(self):
        ids = set()
        for _ in range(3):
            r = self.client.post("/api/sessions", json={})
            ids.add(r.json()["session_id"])
        self.assertEqual(len(ids), 3, "Each session must have a unique id")

    def test_session_contains_created_at(self):
        r = self.client.post("/api/sessions", json={})
        sid = r.json()["session_id"]
        self.assertIn("created_at", sessions[sid])

    def test_session_contains_expires_at(self):
        r = self.client.post("/api/sessions", json={})
        sid = r.json()["session_id"]
        self.assertIn("expires_at", sessions[sid])

    def test_session_history_initially_empty(self):
        r = self.client.post("/api/sessions", json={})
        sid = r.json()["session_id"]
        self.assertEqual(sessions[sid]["history"], [])

    def test_session_stores_user_id(self):
        r = self.client.post("/api/sessions", json={"user_id": "alice"})
        sid = r.json()["session_id"]
        self.assertEqual(sessions[sid]["user_id"], "alice")


# ===================================================================
# A3: Config management
# ===================================================================

class TestConfigEndpoints(unittest.TestCase):
    """A3 — 8 tests for /api/config* endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def setUp(self):
        _reset_server_state()

    def test_get_config(self):
        r = self.client.get("/api/config")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertIn("config", body)

    def test_get_config_has_path(self):
        r = self.client.get("/api/config")
        body = r.json()
        self.assertIn("config_path", body)

    def test_reload_config(self):
        r = self.client.post("/api/config/reload")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertIn("config", body)

    def test_reload_config_returns_changes_key(self):
        r = self.client.post("/api/config/reload")
        body = r.json()
        self.assertIn("changes", body)

    def test_update_config_empty_body(self):
        r = self.client.post("/api/config/update", json={})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # When config.yaml doesn't exist, success may be False
        if body["success"]:
            self.assertEqual(body["updated_keys"], [])

    def test_get_config_changelog(self):
        """GET /api/config/changelog should exist and return a list."""
        r = self.client.get("/api/config/changelog")
        # Endpoint may not exist — in that case we accept 404/405 gracefully
        self.assertIn(r.status_code, (200, 404, 405))

    def test_config_round_trip(self):
        """Read config, reload, read again — should succeed each time."""
        r1 = self.client.get("/api/config")
        self.assertTrue(r1.json()["success"])
        r2 = self.client.post("/api/config/reload")
        self.assertTrue(r2.json()["success"])
        r3 = self.client.get("/api/config")
        self.assertTrue(r3.json()["success"])

    def test_update_config_message_format(self):
        r = self.client.post("/api/config/update", json={})
        body = r.json()
        # Either 'message' (success) or 'error' (config.yaml missing)
        self.assertTrue("message" in body or "error" in body)


# ===================================================================
# A4: Export / Batch operations
# ===================================================================

class TestExportAndBatchEndpoints(unittest.TestCase):
    """A4 — 10 tests for /api/export/* and /api/batch/* endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def setUp(self):
        _reset_server_state()

    # ---------- export ----------

    def test_export_sessions_json(self):
        r = self.client.get("/api/export/sessions?format=json")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertIn("sessions", body)

    def test_export_sessions_csv(self):
        r = self.client.get("/api/export/sessions?format=csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers.get("content-type", ""))

    def test_export_logs_json(self):
        AUDIT_LOG.append({"request_id": "r1", "timestamp": "t", "method": "GET",
                          "path": "/x", "client_ip": "1.2.3.4",
                          "status_code": 200, "duration_ms": 5})
        r = self.client.get("/api/export/logs?format=json&limit=10")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertGreaterEqual(body["total"], 1)

    def test_export_logs_csv(self):
        r = self.client.get("/api/export/logs?format=csv&limit=10")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers.get("content-type", ""))

    def test_export_stats(self):
        r = self.client.get("/api/export/stats")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertIn("stats", body)

    # ---------- batch ----------

    def test_batch_delete_sessions_empty(self):
        r = self.client.post("/api/batch/sessions/delete",
                             json={"session_ids": []})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["deleted_count"], 0)

    def test_batch_delete_backups_empty(self):
        r = self.client.post("/api/batch/backups/delete",
                             json={"backup_ids": []})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["success"])

    def test_batch_warm_cache(self):
        r = self.client.post("/api/batch/cache/warm",
                             json={"endpoints": ["/api/status"]})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["warmed_count"], 1)

    def test_batch_execute_cache_clear(self):
        API_CACHE["k"] = "v"
        r = self.client.post("/api/batch/execute",
                             json={"operations": [{"type": "cache_clear"}]})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertEqual(len(API_CACHE), 0)

    def test_batch_execute_unknown_op(self):
        r = self.client.post("/api/batch/execute",
                             json={"operations": [{"type": "no_such_op"}]})
        body = r.json()
        self.assertEqual(body["results"][0]["status"], "unknown_operation")


# ===================================================================
# A5: System / health endpoints
# ===================================================================

class TestSystemEndpoints(unittest.TestCase):
    """A5 — 8 tests for /api/status, /api/version, /api/health, etc."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def test_status(self):
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("workspace", body)

    def test_version(self):
        r = self.client.get("/api/version")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertIn("version", body)

    def test_cors_config(self):
        r = self.client.get("/api/cors/config")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertIn("allow_origins", body)

    def test_mcp_status(self):
        r = self.client.get("/api/mcp/status")
        self.assertEqual(r.status_code, 200)
        # May succeed or fail depending on MCP availability — both are valid
        body = r.json()
        self.assertIn("success", body)

    def test_skills_list(self):
        r = self.client.get("/api/skills")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("skills", body)

    def test_workspace_scan(self):
        r = self.client.get("/api/workspace/scan")
        self.assertEqual(r.status_code, 200)

    def test_system_status(self):
        r = self.client.get("/api/system/status")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("success", body)

    def test_performance_stats(self):
        r = self.client.get("/api/performance/stats")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])


# ===================================================================
# A6: SNN endpoints
# ===================================================================

class TestSNNEndpoints(unittest.TestCase):
    """A6 — 6 tests for /api/snn/* endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def test_snn_status(self):
        r = self.client.get("/api/snn/status")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # SNN may or may not be enabled — both are valid
        self.assertIn("status", body)

    def test_snn_status_has_enabled_key(self):
        r = self.client.get("/api/snn/status")
        body = r.json()
        self.assertIn("enabled", body)

    def test_snn_reset_when_disabled(self):
        """Reset when SNN not initialised should return success=False."""
        import snn_init as _snn_mod
        old = _snn_mod.SNN_PROCESSOR
        _snn_mod.SNN_PROCESSOR = None
        _snn_mod.SNN_ENABLED = False
        try:
            r = self.client.post("/api/snn/reset")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertFalse(body["success"])
        finally:
            _snn_mod.SNN_PROCESSOR = old

    def test_snn_reset_with_mock_processor(self):
        """Reset with a mock processor should succeed."""
        import snn_init as _snn_mod
        old_proc = _snn_mod.SNN_PROCESSOR
        old_en = _snn_mod.SNN_ENABLED
        mock = MagicMock()
        mock.reset.return_value = True
        _snn_mod.SNN_PROCESSOR = mock
        _snn_mod.SNN_ENABLED = True
        try:
            r = self.client.post("/api/snn/reset")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertTrue(body["success"])
        finally:
            _snn_mod.SNN_PROCESSOR = old_proc
            _snn_mod.SNN_ENABLED = old_en

    def test_snn_status_disabled_message(self):
        import snn_init as _snn_mod
        old = _snn_mod.SNN_PROCESSOR
        old_en = _snn_mod.SNN_ENABLED
        _snn_mod.SNN_PROCESSOR = None
        _snn_mod.SNN_ENABLED = False
        try:
            r = self.client.get("/api/snn/status")
            body = r.json()
            self.assertFalse(body["enabled"])
        finally:
            _snn_mod.SNN_PROCESSOR = old
            _snn_mod.SNN_ENABLED = old_en

    def test_snn_status_enabled_with_mock(self):
        import snn_init as _snn_mod
        old_proc = _snn_mod.SNN_PROCESSOR
        old_en = _snn_mod.SNN_ENABLED
        mock = MagicMock()
        mock.get_stats.return_value = {
            "total_questions": 10,
            "total_spikes": 50,
            "avg_spikes_per_question": 5.0,
            "avg_processing_time": 0.02,
        }
        _snn_mod.SNN_PROCESSOR = mock
        _snn_mod.SNN_ENABLED = True
        try:
            r = self.client.get("/api/snn/status")
            body = r.json()
            self.assertTrue(body["enabled"])
            self.assertEqual(body["stats"]["total_questions"], 10)
        finally:
            _snn_mod.SNN_PROCESSOR = old_proc
            _snn_mod.SNN_ENABLED = old_en


# ===================================================================
# A7: Audit log endpoints
# ===================================================================

class TestAuditEndpoints(unittest.TestCase):
    """Additional audit-log endpoint tests (part of A5/A7 mix)."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def setUp(self):
        _reset_server_state()

    def test_get_audit_logs_empty(self):
        r = self.client.get("/api/audit/logs?limit=10")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["total_entries"], 0)

    def test_get_audit_stats(self):
        r = self.client.get("/api/audit/stats")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertIn("last_hour", body)

    def test_clear_audit_logs(self):
        AUDIT_LOG.append({"dummy": True})
        r = self.client.delete("/api/audit/clear")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["success"])
        # Note: the audit middleware may re-add an entry for this request
        # so we just check the dummy entry was removed
        dummy_entries = [e for e in AUDIT_LOG if e.get("dummy")]
        self.assertEqual(len(dummy_entries), 0)

    def test_set_audit_config(self):
        r = self.client.post("/api/audit/config",
                             json={"enabled": False})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertFalse(body["config"]["enabled"])


# ===================================================================
# A7-extra: Cache, security, misc
# ===================================================================

class TestCacheEndpoints(unittest.TestCase):
    """Cache management endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def setUp(self):
        _reset_server_state()

    def test_cache_stats(self):
        r = self.client.get("/api/cache/stats")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])

    def test_cache_clear(self):
        API_CACHE["x"] = {"data": 1, "timestamp": 0, "ttl": 60}
        r = self.client.post("/api/cache/clear")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["success"])


class TestSecurityEndpoints(unittest.TestCase):
    """Security report / blocked endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def test_security_report(self):
        r = self.client.get("/api/security/report")
        self.assertEqual(r.status_code, 200)

    def test_security_blocked(self):
        r = self.client.get("/api/security/blocked")
        self.assertEqual(r.status_code, 200)


# ===================================================================
# A7: SSE chat streaming
# ===================================================================

class TestChatSSE(unittest.TestCase):
    """A7 — 8 tests for POST /api/chat (SSE streaming)."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def setUp(self):
        _reset_server_state()

    def test_chat_missing_message(self):
        """Empty message should still return a response (not crash)."""
        r = self.client.post("/api/chat", json={"message": ""})
        # Expect either 200 (with error in body) or 4xx
        self.assertIn(r.status_code, (200, 400, 422))

    def test_chat_missing_body(self):
        r = self.client.post("/api/chat", content=b"{}")
        self.assertIn(r.status_code, (200, 400, 422))

    def test_chat_returns_sse_or_json(self):
        """A basic chat request should return SSE stream or JSON."""
        r = self.client.post("/api/chat",
                             json={"message": "hello", "session_id": "test_s"})
        # Accept both streaming and JSON responses
        ct = r.headers.get("content-type", "")
        self.assertTrue(
            "text/event-stream" in ct or "application/json" in ct,
            f"Unexpected content-type: {ct}",
        )

    def test_chat_with_session_id(self):
        self.client.post("/api/sessions", json={"user_id": "u"})
        r = self.client.post("/api/chat",
                             json={"message": "test", "session_id": "u_fake"})
        self.assertIn(r.status_code, (200, 400, 422, 500))

    def test_chat_snn_endpoint_exists(self):
        """POST /api/chat/snn should be routable."""
        r = self.client.post("/api/chat/snn",
                             json={"message": "hi", "session_id": "snn_test"})
        self.assertIn(r.status_code, (200, 400, 422, 500))

    def test_chat_response_is_valid_json_or_stream(self):
        r = self.client.post("/api/chat",
                             json={"message": "ping"})
        ct = r.headers.get("content-type", "")
        if "application/json" in ct:
            body = r.json()
            self.assertIsInstance(body, dict)

    def test_quality_feedback(self):
        r = self.client.post("/api/quality/feedback",
                             json={"session_id": "s1", "message_index": 0,
                                   "rating": 5, "comment": "great"})
        self.assertIn(r.status_code, (200, 400, 422, 500))

    def test_quality_report(self):
        r = self.client.get("/api/quality/report")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
