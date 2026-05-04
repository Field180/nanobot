"""
P2: Swarm Multi-Agent Parallelism Tests
========================================
Tests for:
  - SessionAgentPool lifecycle (start, wait_all, wait_any, reset)
  - Message mailbox (send_message, get_agent_messages)
  - execute_parallel (parallel execution, timing, result collection)
  - Task tree sync (child task creation, state transitions)
  - Backward compatibility (single sub_agent unchanged)
  - Pool registry (get_agent_pool, reset_agent_pool)
"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.sub_agent import (
    SessionAgentPool,
    _AgentEntry,
    get_agent_pool,
    reset_agent_pool,
    execute_parallel,
    execute_async,
    set_parent_context,
    _AGENT_POOLS,
    BUILT_IN_AGENTS,
    TOOL_DEF,
)


def _run(coro):
    """Helper to run async code in tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════
# 1. SessionAgentPool — Basic Lifecycle
# ═══════════════════════════════════════════════════════════════

class TestSessionAgentPoolLifecycle(unittest.TestCase):
    """Test pool creation, agent start, wait_all, wait_any, reset."""

    def setUp(self):
        self.pool = SessionAgentPool("test_session_lifecycle")

    def tearDown(self):
        self.pool.reset()

    def test_pool_init(self):
        self.assertEqual(self.pool.session_id, "test_session_lifecycle")
        self.assertEqual(self.pool.total_count, 0)
        self.assertEqual(self.pool.active_count, 0)

    def test_start_agent_returns_id(self):
        """start_agent should return a unique agent_id string."""
        async def _t():
            async def _dummy():
                await asyncio.sleep(0.01)
                return {"success": True, "output": "done", "error": ""}

            # Manually create an entry to test ID generation
            aid = self.pool._next_id("explore")
            self.assertIn("swarm_explore_", aid)
            aid2 = self.pool._next_id("explore")
            self.assertNotEqual(aid, aid2)
        _run(_t())

    def test_wait_all_with_simple_agents(self):
        """Two mock agents should both complete via wait_all."""
        async def _t():
            async def _agent_a():
                await asyncio.sleep(0.05)
                return {"success": True, "output": "A done", "error": ""}

            async def _agent_b():
                await asyncio.sleep(0.05)
                return {"success": True, "output": "B done", "error": ""}

            # Manually add entries
            task_a = asyncio.ensure_future(_agent_a())
            task_b = asyncio.ensure_future(_agent_b())
            self.pool._agents["a1"] = _AgentEntry("a1", "explore", "task A", task_a)
            self.pool._agents["a2"] = _AgentEntry("a2", "explore", "task B", task_b)

            self.assertEqual(self.pool.active_count, 2)
            results = await self.pool.wait_all(timeout=5.0)
            self.assertEqual(len(results), 2)
            self.assertTrue(results["a1"]["success"])
            self.assertTrue(results["a2"]["success"])
            self.assertEqual(results["a1"]["output"], "A done")
            self.assertEqual(results["a2"]["output"], "B done")
            self.assertEqual(self.pool.active_count, 0)

        _run(_t())

    def test_wait_all_timeout(self):
        """Agents exceeding timeout should be cancelled and return error."""
        async def _t():
            async def _slow():
                await asyncio.sleep(10)
                return {"success": True, "output": "slow", "error": ""}

            task = asyncio.ensure_future(_slow())
            self.pool._agents["slow1"] = _AgentEntry("slow1", "general", "slow task", task)

            results = await self.pool.wait_all(timeout=0.1)
            self.assertIn("slow1", results)
            self.assertFalse(results["slow1"]["success"])
            self.assertIn("timed out", results["slow1"]["error"])

        _run(_t())

    def test_wait_any_returns_first(self):
        """wait_any should return the first completed agent."""
        async def _t():
            async def _fast():
                await asyncio.sleep(0.01)
                return {"success": True, "output": "fast", "error": ""}

            async def _slow():
                await asyncio.sleep(5)
                return {"success": True, "output": "slow", "error": ""}

            task_fast = asyncio.ensure_future(_fast())
            task_slow = asyncio.ensure_future(_slow())
            self.pool._agents["fast"] = _AgentEntry("fast", "explore", "fast task", task_fast)
            self.pool._agents["slow"] = _AgentEntry("slow", "explore", "slow task", task_slow)

            first = await self.pool.wait_any(timeout=2.0)
            self.assertEqual(first, "fast")
            # Cleanup
            task_slow.cancel()
            try:
                await task_slow
            except asyncio.CancelledError:
                pass

        _run(_t())

    def test_wait_any_all_done(self):
        """wait_any when all agents already done returns the last one."""
        async def _t():
            async def _instant():
                return {"success": True, "output": "done", "error": ""}

            task = asyncio.ensure_future(_instant())
            await task  # Let it complete
            self.pool._agents["d1"] = _AgentEntry("d1", "general", "done", task)

            first = await self.pool.wait_any(timeout=1.0)
            self.assertEqual(first, "d1")

        _run(_t())

    def test_reset_cancels_running(self):
        """reset() should cancel running agents and clear the pool."""
        async def _t():
            async def _forever():
                await asyncio.sleep(100)
                return {"success": True, "output": "", "error": ""}

            task = asyncio.ensure_future(_forever())
            self.pool._agents["r1"] = _AgentEntry("r1", "general", "long task", task)
            self.assertEqual(self.pool.active_count, 1)

            self.pool.reset()
            self.assertEqual(self.pool.total_count, 0)
            self.assertEqual(self.pool.active_count, 0)
            # Let the event loop process the cancellation
            await asyncio.sleep(0)
            self.assertTrue(task.cancelled())

        _run(_t())

    def test_get_agent_result_running(self):
        """get_agent_result returns None for still-running agent."""
        async def _t():
            async def _slow():
                await asyncio.sleep(10)
                return {"success": True, "output": "", "error": ""}

            task = asyncio.ensure_future(_slow())
            self.pool._agents["s1"] = _AgentEntry("s1", "general", "slow", task)

            result = self.pool.get_agent_result("s1")
            self.assertIsNone(result)

            # Unknown agent
            result2 = self.pool.get_agent_result("nonexistent")
            self.assertFalse(result2["success"])

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        _run(_t())

    def test_get_agent_result_completed(self):
        """get_agent_result returns result for completed agent."""
        async def _t():
            async def _done():
                return {"success": True, "output": "hello", "error": ""}

            task = asyncio.ensure_future(_done())
            await task
            self.pool._agents["c1"] = _AgentEntry("c1", "explore", "done", task)

            result = self.pool.get_agent_result("c1")
            self.assertIsNotNone(result)
            self.assertTrue(result["success"])
            self.assertEqual(result["output"], "hello")

        _run(_t())

    def test_list_agents(self):
        """list_agents returns status of all agents."""
        async def _t():
            async def _done():
                return {"success": True, "output": "", "error": ""}

            task = asyncio.ensure_future(_done())
            await task
            self.pool._agents["l1"] = _AgentEntry("l1", "explore", "test task", task)

            agents = self.pool.list_agents()
            self.assertEqual(len(agents), 1)
            self.assertEqual(agents[0]["agent_id"], "l1")
            self.assertEqual(agents[0]["agent_type"], "explore")
            self.assertTrue(agents[0]["is_done"])

        _run(_t())


# ═══════════════════════════════════════════════════════════════
# 2. Message Mailbox
# ═══════════════════════════════════════════════════════════════

class TestMessageMailbox(unittest.TestCase):
    """Test inter-agent message passing."""

    def setUp(self):
        self.pool = SessionAgentPool("test_session_mailbox")

    def tearDown(self):
        self.pool.reset()

    def test_send_and_receive(self):
        """Agent A can send a message to Agent B's inbox."""
        async def _t():
            async def _noop():
                await asyncio.sleep(10)
                return {"success": True, "output": "", "error": ""}

            task_a = asyncio.ensure_future(_noop())
            task_b = asyncio.ensure_future(_noop())
            self.pool._agents["agentA"] = _AgentEntry("agentA", "explore", "A", task_a)
            self.pool._agents["agentB"] = _AgentEntry("agentB", "research", "B", task_b)

            # Send message from A to B
            ok = self.pool.send_message("agentA", "agentB", "I found the interface definition")
            self.assertTrue(ok)

            # B receives it
            msgs = self.pool.get_agent_messages("agentB")
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0]["from"], "agentA")
            self.assertEqual(msgs[0]["content"], "I found the interface definition")
            self.assertIn("timestamp", msgs[0])

            # Inbox is now empty
            msgs2 = self.pool.get_agent_messages("agentB")
            self.assertEqual(len(msgs2), 0)

            # A's inbox is empty too
            msgs3 = self.pool.get_agent_messages("agentA")
            self.assertEqual(len(msgs3), 0)

            task_a.cancel()
            task_b.cancel()
            try:
                await asyncio.gather(task_a, task_b, return_exceptions=True)
            except:
                pass

        _run(_t())

    def test_send_to_unknown_agent(self):
        """Sending to nonexistent agent returns False."""
        ok = self.pool.send_message("x", "nonexistent", "hello")
        self.assertFalse(ok)

    def test_get_messages_unknown_agent(self):
        """Getting messages for nonexistent agent returns empty list."""
        msgs = self.pool.get_agent_messages("nonexistent")
        self.assertEqual(msgs, [])

    def test_multiple_messages_fifo(self):
        """Messages are received in FIFO order."""
        async def _t():
            async def _noop():
                await asyncio.sleep(10)
                return {"success": True, "output": "", "error": ""}

            task = asyncio.ensure_future(_noop())
            self.pool._agents["recv"] = _AgentEntry("recv", "general", "receiver", task)

            self.pool.send_message("s1", "recv", "first")
            self.pool.send_message("s2", "recv", "second")
            self.pool.send_message("s3", "recv", "third")

            msgs = self.pool.get_agent_messages("recv")
            self.assertEqual(len(msgs), 3)
            self.assertEqual(msgs[0]["content"], "first")
            self.assertEqual(msgs[1]["content"], "second")
            self.assertEqual(msgs[2]["content"], "third")

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        _run(_t())

    def test_bidirectional_messaging(self):
        """Two agents can exchange messages in both directions."""
        async def _t():
            async def _noop():
                await asyncio.sleep(10)
                return {"success": True, "output": "", "error": ""}

            task_a = asyncio.ensure_future(_noop())
            task_b = asyncio.ensure_future(_noop())
            self.pool._agents["a"] = _AgentEntry("a", "explore", "A", task_a)
            self.pool._agents["b"] = _AgentEntry("b", "research", "B", task_b)

            self.pool.send_message("a", "b", "request from A")
            self.pool.send_message("b", "a", "response from B")

            msgs_a = self.pool.get_agent_messages("a")
            msgs_b = self.pool.get_agent_messages("b")
            self.assertEqual(len(msgs_a), 1)
            self.assertEqual(msgs_a[0]["from"], "b")
            self.assertEqual(len(msgs_b), 1)
            self.assertEqual(msgs_b[0]["from"], "a")

            task_a.cancel()
            task_b.cancel()
            try:
                await asyncio.gather(task_a, task_b, return_exceptions=True)
            except:
                pass

        _run(_t())


# ═══════════════════════════════════════════════════════════════
# 3. Parallel Execution Timing
# ═══════════════════════════════════════════════════════════════

class TestParallelExecution(unittest.TestCase):
    """Verify truly parallel execution — total time ≈ max(individual), not sum."""

    def setUp(self):
        self.pool = SessionAgentPool("test_session_parallel")

    def tearDown(self):
        self.pool.reset()

    def test_parallel_faster_than_serial(self):
        """Two 0.1s agents running in parallel should complete in ~0.1s, not 0.2s."""
        async def _t():
            DELAY = 0.1

            async def _agent():
                await asyncio.sleep(DELAY)
                return {"success": True, "output": "done", "error": ""}

            task_a = asyncio.ensure_future(_agent())
            task_b = asyncio.ensure_future(_agent())
            self.pool._agents["p1"] = _AgentEntry("p1", "explore", "task1", task_a)
            self.pool._agents["p2"] = _AgentEntry("p2", "explore", "task2", task_b)

            t0 = time.time()
            results = await self.pool.wait_all(timeout=5.0)
            elapsed = time.time() - t0

            self.assertEqual(len(results), 2)
            self.assertTrue(all(r["success"] for r in results.values()))
            # Parallel: elapsed should be ~0.1s, definitely < 0.19s (serial would be ~0.2s)
            self.assertLess(elapsed, DELAY * 1.9,
                            f"Parallel agents took {elapsed:.3f}s, expected < {DELAY * 1.9:.3f}s")

        _run(_t())

    def test_three_agents_parallel(self):
        """Three agents with varying delays should complete in time of longest."""
        async def _t():
            async def _fast():
                await asyncio.sleep(0.02)
                return {"success": True, "output": "fast", "error": ""}

            async def _medium():
                await asyncio.sleep(0.05)
                return {"success": True, "output": "medium", "error": ""}

            async def _slow():
                await asyncio.sleep(0.1)
                return {"success": True, "output": "slow", "error": ""}

            self.pool._agents["f"] = _AgentEntry("f", "explore", "fast", asyncio.ensure_future(_fast()))
            self.pool._agents["m"] = _AgentEntry("m", "research", "medium", asyncio.ensure_future(_medium()))
            self.pool._agents["s"] = _AgentEntry("s", "verify", "slow", asyncio.ensure_future(_slow()))

            t0 = time.time()
            results = await self.pool.wait_all(timeout=5.0)
            elapsed = time.time() - t0

            self.assertEqual(len(results), 3)
            # Should complete in ~0.1s (slowest), not 0.17s (sum)
            self.assertLess(elapsed, 0.16)

        _run(_t())


# ═══════════════════════════════════════════════════════════════
# 4. execute_parallel Function
# ═══════════════════════════════════════════════════════════════

class TestExecuteParallel(unittest.TestCase):
    """Test the execute_parallel() convenience function."""

    def test_empty_specs(self):
        """Empty spec list returns empty results."""
        async def _t():
            results = await execute_parallel([], Path("/tmp"), "test_ep_empty")
            self.assertEqual(results, {})
        _run(_t())

    def test_short_task_filtered(self):
        """Tasks with < 10 chars are filtered out."""
        async def _t():
            results = await execute_parallel(
                [{"task": "short"}],
                Path("/tmp"),
                "test_ep_short",
            )
            self.assertEqual(results, {})
        _run(_t())

    def test_execute_parallel_starts_agents(self):
        """execute_parallel should start agents via the pool and return results."""
        async def _t():
            # Mock execute_async to avoid real LLM calls
            with patch("tools.sub_agent.execute_async") as mock_exec:
                async def _mock_execute(args, ws):
                    await asyncio.sleep(0.02)
                    task_text = args.get("task", "")
                    return {"success": True, "output": f"Result for: {task_text[:20]}", "error": ""}

                mock_exec.side_effect = _mock_execute

                # Set minimal parent context
                set_parent_context({"PATH": "/usr/bin"}, Path("/tmp"), "test_ep_start")

                specs = [
                    {"task": "Research the database schema in detail", "agent_type": "explore"},
                    {"task": "Verify the API endpoints are correct", "agent_type": "verify"},
                ]
                results = await execute_parallel(specs, Path("/tmp"), "test_ep_start", timeout=10.0)

                self.assertEqual(len(results), 2)
                for aid, res in results.items():
                    self.assertIn("swarm_", aid)
                    self.assertTrue(res["success"])
                    self.assertIn("Result for:", res["output"])

        _run(_t())
        # Cleanup
        reset_agent_pool("test_ep_start")


# ═══════════════════════════════════════════════════════════════
# 5. Task Tree Sync
# ═══════════════════════════════════════════════════════════════

class TestTaskTreeSync(unittest.TestCase):
    """Verify child tasks are created and transitioned by sub-agent pool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.workspace = Path(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        reset_agent_pool("test_task_sync")

    def test_child_task_created_on_execute(self):
        """execute_async should create a child task in task_store."""
        from task_store import get_task_store, create_session_root_task

        async def _t():
            session_id = "test_task_sync"
            # Create root task
            root = create_session_root_task(
                self.workspace, session_id,
                title="Parent task",
                objective="Test task tree sync",
            )

            # Mock the agentic_chat_stream to return immediately
            async def _mock_stream(**kwargs):
                yield {"type": "chunk", "content": "Test result"}
                yield {"type": "agentic_done", "turns": 1}

            set_parent_context(
                {"PATH": "/usr/bin"},
                self.workspace,
                session_id,
            )

            with patch("agentic_loop.agentic_chat_stream", side_effect=_mock_stream):
                result = await execute_async(
                    {"task": "Test child task creation in task store", "agent_type": "explore"},
                    self.workspace,
                )

            self.assertTrue(result["success"])

            # Check task store has a child task
            store = get_task_store(session_id, self.workspace)
            children = store.get_children(root.id)
            self.assertGreaterEqual(len(children), 1)
            child = children[0]
            self.assertIn("explore", child.title)
            # Child should be completed since the agent finished successfully
            self.assertEqual(child.state, "completed")

        _run(_t())

    def test_child_task_failed_on_error(self):
        """Failed sub-agent should transition child task to 'failed'."""
        from task_store import get_task_store, create_session_root_task

        async def _t():
            session_id = "test_task_sync"
            root = create_session_root_task(
                self.workspace, session_id,
                title="Parent task",
                objective="Test failure sync",
            )

            set_parent_context(
                {"PATH": "/usr/bin"},
                self.workspace,
                session_id,
            )

            with patch("agentic_loop.agentic_chat_stream", side_effect=Exception("LLM error")):
                result = await execute_async(
                    {"task": "This task will fail due to LLM error", "agent_type": "explore"},
                    self.workspace,
                )

            self.assertFalse(result["success"])

            store = get_task_store(session_id, self.workspace)
            children = store.get_children(root.id)
            self.assertGreaterEqual(len(children), 1)
            child = children[0]
            self.assertEqual(child.state, "failed")

        _run(_t())


# ═══════════════════════════════════════════════════════════════
# 6. Backward Compatibility
# ═══════════════════════════════════════════════════════════════

class TestBackwardCompatibility(unittest.TestCase):
    """Ensure single sub_agent calls work exactly as before."""

    def test_tool_def_unchanged(self):
        """TOOL_DEF should still define sub_agent with existing parameters."""
        fn = TOOL_DEF["function"]
        self.assertEqual(fn["name"], "sub_agent")
        props = fn["parameters"]["properties"]
        self.assertIn("task", props)
        self.assertIn("agent_type", props)
        self.assertIn("max_turns", props)
        self.assertIn("inherit_context", props)
        self.assertIn("background", props)
        self.assertEqual(fn["parameters"]["required"], ["task"])

    def test_built_in_agents_intact(self):
        """All built-in agent types should still exist."""
        for atype in ("explore", "verify", "plan", "research", "edit"):
            self.assertIn(atype, BUILT_IN_AGENTS)
            self.assertIn("system_prompt", BUILT_IN_AGENTS[atype])
            self.assertIn("allowed_tools", BUILT_IN_AGENTS[atype])

    def test_execute_async_single_agent(self):
        """Single sub_agent via execute_async should work as before."""
        async def _t():
            async def _mock_stream(**kwargs):
                yield {"type": "chunk", "content": "single agent result"}
                yield {"type": "agentic_done", "turns": 1}

            set_parent_context(
                {"PATH": "/usr/bin"},
                Path("/tmp"),
                "test_compat_single",
            )

            with patch("agentic_loop.agentic_chat_stream", side_effect=_mock_stream):
                result = await execute_async(
                    {"task": "This is a single agent backward compatibility test"},
                    Path("/tmp"),
                )

            self.assertTrue(result["success"])
            self.assertIn("single agent result", result["output"])

        _run(_t())

    def test_quick_path_still_works(self):
        """Explore quick-path should still work for simple file lookups."""
        async def _t():
            with tempfile.TemporaryDirectory() as tmpdir:
                # Create a target file
                (Path(tmpdir) / "package.json").write_text("{}")

                set_parent_context(
                    {"PATH": "/usr/bin"},
                    Path(tmpdir),
                    "test_compat_quick",
                )

                result = await execute_async(
                    {"task": "Find package.json in the workspace", "agent_type": "explore"},
                    Path(tmpdir),
                )

                self.assertTrue(result["success"])
                self.assertIn("package.json", result["output"])
                self.assertTrue(result.get("_quick_path", False))

        _run(_t())

    def test_invalid_agent_type_still_errors(self):
        """Invalid agent_type should still return an error."""
        async def _t():
            result = await execute_async(
                {"task": "Test with invalid agent type parameter", "agent_type": "nonexistent"},
                Path("/tmp"),
            )
            self.assertFalse(result["success"])
            self.assertIn("Unknown agent_type", result["error"])

        _run(_t())

    def test_short_task_still_errors(self):
        """Tasks < 10 chars should still return an error."""
        async def _t():
            result = await execute_async(
                {"task": "short"},
                Path("/tmp"),
            )
            self.assertFalse(result["success"])
            self.assertIn("too short", result["error"])

        _run(_t())


# ═══════════════════════════════════════════════════════════════
# 7. Pool Registry
# ═══════════════════════════════════════════════════════════════

class TestPoolRegistry(unittest.TestCase):
    """Test global pool registry functions."""

    def tearDown(self):
        reset_agent_pool("test_reg_1")
        reset_agent_pool("test_reg_2")

    def test_get_agent_pool_creates_new(self):
        """get_agent_pool creates a pool if none exists."""
        pool = get_agent_pool("test_reg_1")
        self.assertIsInstance(pool, SessionAgentPool)
        self.assertEqual(pool.session_id, "test_reg_1")

    def test_get_agent_pool_returns_same(self):
        """Subsequent calls return the same pool."""
        pool1 = get_agent_pool("test_reg_1")
        pool2 = get_agent_pool("test_reg_1")
        self.assertIs(pool1, pool2)

    def test_different_sessions_different_pools(self):
        """Different session IDs get different pools."""
        pool1 = get_agent_pool("test_reg_1")
        pool2 = get_agent_pool("test_reg_2")
        self.assertIsNot(pool1, pool2)

    def test_reset_agent_pool_removes(self):
        """reset_agent_pool removes the pool from the registry."""
        pool = get_agent_pool("test_reg_1")
        reset_agent_pool("test_reg_1")
        # Getting again should create a new one
        pool2 = get_agent_pool("test_reg_1")
        self.assertIsNot(pool, pool2)

    def test_reset_nonexistent_pool_no_error(self):
        """Resetting a non-existent pool should not raise."""
        reset_agent_pool("nonexistent_session_xyz")  # Should not raise


# ═══════════════════════════════════════════════════════════════
# 8. _AgentEntry
# ═══════════════════════════════════════════════════════════════

class TestAgentEntry(unittest.TestCase):
    """Test the _AgentEntry data class."""

    def test_entry_is_done(self):
        """is_done reflects asyncio task state."""
        async def _t():
            async def _done():
                return {"success": True, "output": "", "error": ""}

            task = asyncio.ensure_future(_done())
            entry = _AgentEntry("e1", "explore", "test", task)
            # Wait for task to complete
            await task
            self.assertTrue(entry.is_done)

        _run(_t())

    def test_entry_collect_result_success(self):
        async def _t():
            async def _done():
                return {"success": True, "output": "hello", "error": ""}

            task = asyncio.ensure_future(_done())
            await task
            entry = _AgentEntry("e2", "explore", "test", task)

            result = entry.collect_result()
            self.assertTrue(result["success"])
            self.assertEqual(result["output"], "hello")
            self.assertIsNotNone(entry.completed_at)

        _run(_t())

    def test_entry_collect_result_exception(self):
        async def _t():
            async def _fail():
                raise ValueError("boom")

            task = asyncio.ensure_future(_fail())
            try:
                await task
            except ValueError:
                pass
            entry = _AgentEntry("e3", "explore", "test", task)

            result = entry.collect_result()
            self.assertFalse(result["success"])
            self.assertIn("boom", result["error"])

        _run(_t())

    def test_entry_collect_result_not_done(self):
        async def _t():
            async def _slow():
                await asyncio.sleep(10)
                return {"success": True, "output": "", "error": ""}

            task = asyncio.ensure_future(_slow())
            entry = _AgentEntry("e4", "explore", "test", task)

            result = entry.collect_result()
            self.assertFalse(result["success"])
            self.assertIn("still running", result["error"])

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        _run(_t())

    def test_entry_has_inbox(self):
        """Each agent entry should have an asyncio.Queue inbox."""
        async def _t():
            async def _noop():
                return {"success": True, "output": "", "error": ""}

            task = asyncio.ensure_future(_noop())
            entry = _AgentEntry("e5", "explore", "test", task)
            self.assertIsInstance(entry.inbox, asyncio.Queue)
            self.assertTrue(entry.inbox.empty())
            await task

        _run(_t())


# ═══════════════════════════════════════════════════════════════
# 9. Agentic Loop Integration — P2 imports
# ═══════════════════════════════════════════════════════════════

class TestAgenticLoopImports(unittest.TestCase):
    """Verify P2 functions are importable from agentic_loop's perspective."""

    def test_pool_functions_importable(self):
        """get_agent_pool, execute_parallel, reset_agent_pool must be importable."""
        from tools.sub_agent import get_agent_pool, execute_parallel, reset_agent_pool
        self.assertTrue(callable(get_agent_pool))
        self.assertTrue(callable(execute_parallel))
        self.assertTrue(callable(reset_agent_pool))

    def test_session_agent_pool_class_importable(self):
        from tools.sub_agent import SessionAgentPool
        pool = SessionAgentPool("import_test")
        self.assertEqual(pool.session_id, "import_test")
        pool.reset()

    def test_partition_tool_calls_sub_agent_readonly(self):
        """sub_agent should be classified as read-only for batch partitioning."""
        from tools import READONLY_TOOLS
        self.assertIn("sub_agent", READONLY_TOOLS)


if __name__ == "__main__":
    unittest.main()
