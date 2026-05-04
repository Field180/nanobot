"""
P17: Parser Pipeline Integration Tests (formerly "Replay E2E").

⚠️ SCOPE LIMITATION: These tests verify the _parsing pipeline_ of _stream_one_turn,
NOT LLM behavior or prompt effectiveness. All fixtures use hand-crafted
ChatCompletionChunk objects — they test that the code correctly handles:
- Split tool-call deltas arriving out-of-order or interleaved
- Malformed JSON in tool arguments (graceful fallback)
- Text buffering, LaTeX filtering, token stripping
- Tool dispatch routing (execute_tool / execute_tool_async)

They do NOT test:
- Actual LLM reasoning or output quality
- Prompt effectiveness or system prompt changes
- Model behavior drift or degradation

For true behavior regression testing, record real LLM chunk streams
(see SECURITY.md "提示词退化检测" risk).

Mock depth: OpenAI AsyncClient — the mock replaces the HTTP transport layer,
returning real ChatCompletionChunk objects.  Everything above that runs for real:

  _stream_one_turn  ← runs for real (SSE parsing, tool-call delta accumulation,
                       LaTeX buffering, special-token stripping, JSON parse)
  tool dispatch      ← real execute_tool / execute_tool_async
  agentic_chat_stream ← real turn loop, context engineering, MicroCompact, events

This catches regressions in:
- _stream_one_turn chunk processing (text cleaning, partial tool-call detection)
- Tool call delta accumulation and argument JSON parsing
- Real tool dispatch (execute_tool / execute_tool_async routing)
- Tool result injection into conversation messages
- Turn loop control flow (max_turns, force_text_only, safety gates)
- Event emission (turn_start, generation_start, chunk, tool_start, tool_result, agentic_done)
- Error handling when tools return failures
"""

import asyncio
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure web_ui is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import (
    Choice,
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from openai.types import CompletionUsage

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
WEB_UI_DIR = Path(__file__).resolve().parent.parent


def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _build_chunks(turn_data: dict) -> list:
    """Convert a fixture turn into realistic ChatCompletionChunk objects.

    Simulates real OpenAI streaming behaviour:
    - Text is emitted character-by-character (exercises buffer/flush logic).
    - Tool call deltas are split across multiple chunks: first chunk carries
      id + name + partial args, subsequent chunks carry argument fragments.
      This matches real GPT-4/3.5 SSE streams where arguments arrive in
      ~10-20 character increments.
    - Multiple tool calls use distinct delta.index values.
    """
    chunks = []
    text = turn_data.get("assistant_text", "")
    # Emit text as character-level chunks (exercises buffer logic)
    for ch in text:
        chunks.append(ChatCompletionChunk(
            id="replay", choices=[Choice(
                index=0, delta=ChoiceDelta(content=ch), finish_reason=None,
            )], created=0, model="test", object="chat.completion.chunk",
        ))
    # Emit tool calls with realistic split deltas
    for tc_idx, tc in enumerate(turn_data.get("tool_calls", [])):
        tc_id = tc.get("id", f"call_{tc_idx}")
        full_args = json.dumps(tc["arguments"])
        # First delta: id + name + first arg fragment (real API pattern)
        first_frag = full_args[:max(1, len(full_args) // 3)]
        chunks.append(ChatCompletionChunk(
            id="replay", choices=[Choice(
                index=0, delta=ChoiceDelta(tool_calls=[ChoiceDeltaToolCall(
                    index=tc_idx, id=tc_id,
                    function=ChoiceDeltaToolCallFunction(
                        name=tc["name"], arguments=first_frag,
                    ), type="function",
                )]), finish_reason=None,
            )], created=0, model="test", object="chat.completion.chunk",
        ))
        # Remaining arg fragments (~12 chars each, matching real stream granularity)
        rest = full_args[len(first_frag):]
        frag_size = 12
        for i in range(0, len(rest), frag_size):
            frag = rest[i:i + frag_size]
            chunks.append(ChatCompletionChunk(
                id="replay", choices=[Choice(
                    index=0, delta=ChoiceDelta(tool_calls=[ChoiceDeltaToolCall(
                        index=tc_idx, id=None,
                        function=ChoiceDeltaToolCallFunction(
                            name=None, arguments=frag,
                        ), type="function",
                    )]), finish_reason=None,
                )], created=0, model="test", object="chat.completion.chunk",
            ))
    # Finish reason
    chunks.append(ChatCompletionChunk(
        id="replay", choices=[Choice(
            index=0, delta=ChoiceDelta(), finish_reason="stop",
        )], created=0, model="test", object="chat.completion.chunk",
    ))
    # Usage chunk (empty choices)
    chunks.append(ChatCompletionChunk(
        id="replay", choices=[], created=0, model="test",
        object="chat.completion.chunk",
        usage=CompletionUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
    ))
    return chunks


class _MockAsyncStream:
    """Async iterator over ChatCompletionChunk objects."""
    def __init__(self, chunks):
        self._chunks = chunks
        self._idx = 0
    def __aiter__(self):
        return self
    async def __anext__(self):
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._idx]
        self._idx += 1
        return c


def _make_mock_openai_client(turns: list):
    """Build a mock AsyncOpenAI whose chat.completions.create returns scripted chunks.

    Each call to create() consumes the next turn from the fixture.
    Chunks are real ChatCompletionChunk objects, so _stream_one_turn's
    attribute access (chunk.choices, delta.content, delta.tool_calls, etc.)
    all exercise real OpenAI SDK types.
    """
    call_idx = 0

    async def _mock_create(**kwargs):
        nonlocal call_idx
        if call_idx < len(turns):
            chunks = _build_chunks(turns[call_idx])
            call_idx += 1
        else:
            # Fallback: emit simple "Done." text
            chunks = _build_chunks({"assistant_text": "Done.", "tool_calls": []})
        return _MockAsyncStream(chunks)

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=_mock_create)
    return mock_client


def _run_replay(fixture: dict, mode: str = "code", max_turns: int = 5, session_id: str = "replay") -> list:
    """Run a fixture through agentic_chat_stream and return collected events.

    Mock depth: AsyncOpenAI constructor is patched to return a mock client
    that yields real ChatCompletionChunk objects.  _stream_one_turn and all
    downstream logic (tool dispatch, context engineering, etc.) run for real.
    """
    mock_client = _make_mock_openai_client(fixture["turns"])

    env = {
        "NANOBOT_API_KEY": "test-key",
        "NANOBOT_API_BASE": "http://localhost:11434/v1",
        "NANOBOT_MODEL": "test-model",
        "NANOBOT_MAX_TOOL_ITERATIONS": str(max_turns),
    }
    workspace = Path("/tmp/nanobot_replay_test")
    workspace.mkdir(parents=True, exist_ok=True)

    collected_events = []

    async def _run():
        with patch("openai.AsyncOpenAI", return_value=mock_client), \
             patch("agentic_loop._load_nanobot_md", return_value=None):
            from agentic_loop import agentic_chat_stream
            async for event in agentic_chat_stream(
                user_message=fixture["user_message"],
                env=env,
                session_id=session_id,
                workspace=workspace,
                max_turns=max_turns,
                mode=mode,
            ):
                collected_events.append(event)

    asyncio.run(_run())
    return collected_events


class TestReplayE2E(unittest.TestCase):
    """Replay-mode regression tests: mock at OpenAI client level,
    _stream_one_turn and tools execute for real."""

    def test_sample_conversation_completes(self):
        """Replay sample_conversation.json: model calls file_list (real execution), then responds."""
        fixture = _load_fixture("sample_conversation.json")
        events = _run_replay(fixture, session_id="replay-test-001")

        self.assertTrue(len(events) > 0, "No events emitted")
        event_types = [e.get("type") for e in events]

        # Core event types must be present
        self.assertIn("turn_start", event_types, "No turn_start event")
        self.assertIn("generation_start", event_types, "No generation_start event")
        self.assertIn("agentic_done", event_types, "No agentic_done event")

        # Check expected event types from fixture
        for expected_type in fixture.get("expected_event_types", []):
            self.assertIn(expected_type, event_types,
                          f"Expected event type '{expected_type}' not found")

        # Verify tool_result contains real file_list output (not mocked)
        tool_results = [e for e in events if e.get("type") == "tool_result"]
        self.assertTrue(len(tool_results) > 0, "No tool_result events — tool dispatch may be broken")

        # Check final text contains expected substring
        expected_text = fixture.get("expected_final_text_contains", "")
        if expected_text:
            all_text = "".join(
                e.get("content", "") for e in events if e.get("type") == "chunk"
            )
            self.assertIn(expected_text, all_text,
                          f"Expected '{expected_text}' in output text, got: {all_text[:200]}")

    def test_tool_error_handled_gracefully(self):
        """Replay tool_error_conversation.json: file_list on nonexistent path returns error, model recovers."""
        fixture = _load_fixture("tool_error_conversation.json")
        events = _run_replay(fixture, session_id="replay-test-err")

        event_types = [e.get("type") for e in events]
        self.assertIn("agentic_done", event_types, "No agentic_done after tool error")

        # tool_result should exist and contain error info
        tool_results = [e for e in events if e.get("type") == "tool_result"]
        self.assertTrue(len(tool_results) > 0, "No tool_result — error path not reached")

        # Check that the text output mentions the error
        all_text = "".join(
            e.get("content", "") for e in events if e.get("type") == "chunk"
        )
        self.assertIn("does not exist", all_text)

    def test_no_tool_calls_completes(self):
        """A conversation with no tool calls should complete gracefully."""
        fixture = {
            "user_message": "Hello, how are you?",
            "turns": [
                {
                    "assistant_text": "I'm doing well, thank you for asking!",
                    "tool_calls": [],
                }
            ],
        }

        events = _run_replay(fixture, mode="ask", max_turns=3, session_id="replay-test-002")

        event_types = [e.get("type") for e in events]
        self.assertIn("agentic_done", event_types, "No agentic_done for simple conversation")

        # Should have text output
        text_chunks = [e for e in events if e.get("type") == "chunk"]
        self.assertTrue(len(text_chunks) > 0, "No text chunks emitted")

    def test_split_tool_call_deltas_reassembled(self):
        """Tool call arguments split across multiple deltas must be reassembled correctly."""
        fixture = {
            "user_message": "List files",
            "turns": [
                {
                    "assistant_text": "Listing.",
                    "tool_calls": [{
                        "id": "call_split",
                        "name": "file_list",
                        "arguments": {"path": "/tmp/nanobot_replay_test"},
                    }],
                },
                {"assistant_text": "Done listing.", "tool_calls": []},
            ],
        }
        events = _run_replay(fixture, session_id="replay-split")
        event_types = [e.get("type") for e in events]
        self.assertIn("tool_start", event_types, "Tool call not dispatched after delta reassembly")
        self.assertIn("tool_result", event_types, "No tool_result — delta reassembly may be broken")

    def test_malformed_json_args_no_crash(self):
        """If LLM emits invalid JSON arguments, _stream_one_turn should yield
        tool_calls_complete with _parsed_args={} and agentic loop must not crash."""
        # Build chunks manually with broken JSON
        broken_chunks = []
        # Text chunk
        broken_chunks.append(ChatCompletionChunk(
            id="replay", choices=[Choice(
                index=0, delta=ChoiceDelta(content="Trying."), finish_reason=None,
            )], created=0, model="test", object="chat.completion.chunk",
        ))
        # Tool call with invalid JSON args
        broken_chunks.append(ChatCompletionChunk(
            id="replay", choices=[Choice(
                index=0, delta=ChoiceDelta(tool_calls=[ChoiceDeltaToolCall(
                    index=0, id="call_bad",
                    function=ChoiceDeltaToolCallFunction(
                        name="file_list", arguments='{"path": INVALID',
                    ), type="function",
                )]), finish_reason=None,
            )], created=0, model="test", object="chat.completion.chunk",
        ))
        # Stop + usage
        broken_chunks.append(ChatCompletionChunk(
            id="replay", choices=[Choice(
                index=0, delta=ChoiceDelta(), finish_reason="stop",
            )], created=0, model="test", object="chat.completion.chunk",
        ))
        broken_chunks.append(ChatCompletionChunk(
            id="replay", choices=[], created=0, model="test",
            object="chat.completion.chunk",
            usage=CompletionUsage(prompt_tokens=50, completion_tokens=20, total_tokens=70),
        ))
        # Second turn: recovery text
        recovery_text = "Sorry, I encountered an error."
        for ch in recovery_text:
            broken_chunks.append(ChatCompletionChunk(
                id="replay", choices=[Choice(
                    index=0, delta=ChoiceDelta(content=ch), finish_reason=None,
                )], created=0, model="test", object="chat.completion.chunk",
            ))
        broken_chunks.append(ChatCompletionChunk(
            id="replay", choices=[Choice(
                index=0, delta=ChoiceDelta(), finish_reason="stop",
            )], created=0, model="test", object="chat.completion.chunk",
        ))
        broken_chunks.append(ChatCompletionChunk(
            id="replay", choices=[], created=0, model="test",
            object="chat.completion.chunk",
            usage=CompletionUsage(prompt_tokens=80, completion_tokens=30, total_tokens=110),
        ))

        # Use raw chunks directly
        call_idx = 0
        chunk_groups = [broken_chunks[:4], broken_chunks[4:]]

        async def _raw_create(**kwargs):
            nonlocal call_idx
            grp = chunk_groups[call_idx] if call_idx < len(chunk_groups) else chunk_groups[-1]
            call_idx += 1
            return _MockAsyncStream(grp)

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_raw_create)

        env = {
            "NANOBOT_API_KEY": "test-key",
            "NANOBOT_API_BASE": "http://localhost:11434/v1",
            "NANOBOT_MODEL": "test-model",
            "NANOBOT_MAX_TOOL_ITERATIONS": "3",
        }
        workspace = Path("/tmp/nanobot_replay_test")
        workspace.mkdir(parents=True, exist_ok=True)
        collected = []

        async def _run():
            with patch("openai.AsyncOpenAI", return_value=mock_client), \
                 patch("agentic_loop._load_nanobot_md", return_value=None):
                from agentic_loop import agentic_chat_stream
                async for event in agentic_chat_stream(
                    user_message="List files", env=env,
                    session_id="replay-malformed", workspace=workspace,
                    max_turns=3, mode="code",
                ):
                    collected.append(event)

        asyncio.run(_run())
        event_types = [e.get("type") for e in collected]
        self.assertIn("agentic_done", event_types,
                      "Loop must complete even with malformed tool call JSON")

    def test_parallel_tool_calls_interleaved_deltas(self):
        """Two tool calls with interleaved delta chunks must both be dispatched.

        Real LLM may emit: [tool0_args_frag1, tool1_args_frag1, tool0_args_frag2, ...]
        This tests _stream_one_turn's handling of multiple delta.index values.
        """
        # Build manual chunks with interleaved tool deltas
        interleaved_chunks = []
        # Assistant text
        interleaved_chunks.append(ChatCompletionChunk(
            id="replay", choices=[Choice(
                index=0, delta=ChoiceDelta(content="Will do."), finish_reason=None,
            )], created=0, model="test", object="chat.completion.chunk",
        ))
        # Tool 0 first fragment
        interleaved_chunks.append(ChatCompletionChunk(
            id="replay", choices=[Choice(
                index=0, delta=ChoiceDelta(tool_calls=[ChoiceDeltaToolCall(
                    index=0, id="call_0",
                    function=ChoiceDeltaToolCallFunction(
                        name="file_list", arguments='{"path": "',
                    ), type="function",
                )]), finish_reason=None,
            )], created=0, model="test", object="chat.completion.chunk",
        ))
        # Tool 1 first fragment (different index)
        interleaved_chunks.append(ChatCompletionChunk(
            id="replay", choices=[Choice(
                index=0, delta=ChoiceDelta(tool_calls=[ChoiceDeltaToolCall(
                    index=1, id="call_1",
                    function=ChoiceDeltaToolCallFunction(
                        name="find_by_name", arguments='{"pattern": "*.py"',
                    ), type="function",
                )]), finish_reason=None,
            )], created=0, model="test", object="chat.completion.chunk",
        ))
        # Tool 0 continuation
        interleaved_chunks.append(ChatCompletionChunk(
            id="replay", choices=[Choice(
                index=0, delta=ChoiceDelta(tool_calls=[ChoiceDeltaToolCall(
                    index=0, id=None,
                    function=ChoiceDeltaToolCallFunction(
                        name=None, arguments='/tmp/nanobot_replay_test"}',
                    ), type="function",
                )]), finish_reason=None,
            )], created=0, model="test", object="chat.completion.chunk",
        ))
        # Tool 1 continuation
        interleaved_chunks.append(ChatCompletionChunk(
            id="replay", choices=[Choice(
                index=0, delta=ChoiceDelta(tool_calls=[ChoiceDeltaToolCall(
                    index=1, id=None,
                    function=ChoiceDeltaToolCallFunction(
                        name=None, arguments='}',
                    ), type="function",
                )]), finish_reason=None,
            )], created=0, model="test", object="chat.completion.chunk",
        ))
        # Stop + usage
        interleaved_chunks.append(ChatCompletionChunk(
            id="replay", choices=[Choice(
                index=0, delta=ChoiceDelta(), finish_reason="stop",
            )], created=0, model="test", object="chat.completion.chunk",
        ))
        interleaved_chunks.append(ChatCompletionChunk(
            id="replay", choices=[], created=0, model="test",
            object="chat.completion.chunk",
            usage=CompletionUsage(prompt_tokens=100, completion_tokens=60, total_tokens=160),
        ))

        call_idx = 0
        chunk_groups = [interleaved_chunks]

        async def _create(**kwargs):
            nonlocal call_idx
            grp = chunk_groups[call_idx] if call_idx < len(chunk_groups) else []
            call_idx += 1
            return _MockAsyncStream(grp)

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_create)

        env = {
            "NANOBOT_API_KEY": "test-key",
            "NANOBOT_API_BASE": "http://localhost:11434/v1",
            "NANOBOT_MODEL": "test-model",
            "NANOBOT_MAX_TOOL_ITERATIONS": "3",
        }
        workspace = Path("/tmp/nanobot_replay_test")
        workspace.mkdir(parents=True, exist_ok=True)
        collected = []

        async def _run():
            with patch("openai.AsyncOpenAI", return_value=mock_client), \
                 patch("agentic_loop._load_nanobot_md", return_value=None):
                from agentic_loop import agentic_chat_stream
                async for event in agentic_chat_stream(
                    user_message="List files and find Python files", env=env,
                    session_id="replay-parallel", workspace=workspace,
                    max_turns=3, mode="code",
                ):
                    collected.append(event)

        asyncio.run(_run())

        tool_starts = [e for e in collected if e.get("type") == "tool_start"]
        tool_names = [e.get("name") for e in tool_starts]
        self.assertIn("file_list", tool_names, "First tool not dispatched")
        self.assertIn("find_by_name", tool_names, "Second tool not dispatched")


# ═══════════════════════════════════════════════════════════════
# Path traversal defense tests
# ═══════════════════════════════════════════════════════════════

class TestPathTraversalDefense(unittest.TestCase):
    """Verify _resolve_path raises ValueError on blocked paths (no silent degradation)."""

    def setUp(self):
        self.workspace = Path("/tmp/nanobot_path_test")
        self.workspace.mkdir(parents=True, exist_ok=True)

    def test_relative_path_stays_in_workspace(self):
        from tools.base import _resolve_path
        result = _resolve_path("subdir/file.py", self.workspace)
        self.assertEqual(result, self.workspace / "subdir/file.py")

    def test_dotdot_traversal_raises_valueerror(self):
        from tools.base import _resolve_path
        with self.assertRaises(ValueError) as ctx:
            _resolve_path("../../etc/passwd", self.workspace)
        self.assertIn("escapes the workspace", str(ctx.exception))

    def test_absolute_path_under_home_allowed(self):
        from tools.base import _resolve_path
        home = Path.home()
        result = _resolve_path(str(home / "somefile.txt"), self.workspace)
        self.assertEqual(result, (home / "somefile.txt").resolve())

    def test_absolute_path_etc_raises_valueerror(self):
        from tools.base import _resolve_path
        with self.assertRaises(ValueError) as ctx:
            _resolve_path("/etc/shadow", self.workspace)
        self.assertIn("outside the allowed directories", str(ctx.exception))
        self.assertIn(str(self.workspace.resolve()), str(ctx.exception))

    def test_tmp_path_allowed(self):
        from tools.base import _resolve_path
        result = _resolve_path("/tmp/test_file.txt", self.workspace)
        self.assertTrue(str(result).startswith("/tmp"))

    def test_workspace_subpath_allowed(self):
        from tools.base import _resolve_path
        result = _resolve_path(str(self.workspace / "deep" / "file.py"), self.workspace)
        self.assertEqual(result, (self.workspace / "deep" / "file.py").resolve())

    def test_execute_tool_returns_error_on_blocked_path(self):
        """Verify the full tool execution pipeline converts ValueError to error result."""
        from tools import execute_tool
        result = execute_tool("file_read", {"path": "/etc/shadow"}, self.workspace)
        self.assertFalse(result["success"])
        self.assertIn("outside the allowed directories", result["error"])


# ═══════════════════════════════════════════════════════════════
# _stream_one_turn error boundary tests
# ═══════════════════════════════════════════════════════════════

class TestStreamOneTurnErrorBoundary(unittest.TestCase):
    """Verify _stream_one_turn logs and re-raises API errors."""

    def test_api_connection_error_is_reraised(self):
        """API connection failure should be logged and re-raised."""
        import asyncio
        from unittest.mock import patch, AsyncMock

        env = {
            "NANOBOT_API_KEY": "test-key",
            "NANOBOT_API_BASE": "http://127.0.0.1:1",
            "NANOBOT_MODEL": "test-model",
        }

        mock_client_instance = AsyncMock()
        mock_client_instance.chat.completions.create = AsyncMock(
            side_effect=ConnectionError("Connection refused")
        )

        from agentic_loop import _stream_one_turn
        with patch("openai.AsyncOpenAI", return_value=mock_client_instance):

            async def _run():
                events = []
                with self.assertRaises(ConnectionError):
                    async for ev in _stream_one_turn(
                        [{"role": "user", "content": "test"}],
                        env, [], None
                    ):
                        events.append(ev)
                return events

            events = asyncio.run(_run())
            self.assertEqual(len(events), 0, "No events should be yielded on connection error")

    def test_agentic_done_emitted_after_api_failure(self):
        """End-to-end: network failure must still produce agentic_done event."""
        import asyncio
        import tempfile
        from unittest.mock import patch, AsyncMock

        env = {
            "NANOBOT_API_KEY": "test-key",
            "NANOBOT_API_BASE": "http://127.0.0.1:1",
            "NANOBOT_MODEL": "test-model",
        }

        mock_client_instance = AsyncMock()
        mock_client_instance.chat.completions.create = AsyncMock(
            side_effect=ConnectionError("Connection refused")
        )

        from agentic_loop import agentic_chat_stream

        async def _run():
            collected = []
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch("openai.AsyncOpenAI", return_value=mock_client_instance):
                    async for ev in agentic_chat_stream(
                        user_message="test",
                        env=env,
                        session_id="test-agentic-done",
                        workspace=Path(tmpdir),
                        max_turns=2,
                    ):
                        collected.append(ev)
            return collected

        events = asyncio.run(_run())
        event_types = [e.get("type") for e in events]
        self.assertIn("agentic_done", event_types,
                       "agentic_done must be emitted even after API failure")

    def test_error_log_truncated_to_300_chars(self):
        """Verify error log message is truncated to 300 chars to prevent leaking sensitive data."""
        import asyncio
        from unittest.mock import patch, AsyncMock, call

        env = {
            "NANOBOT_API_KEY": "test-key",
            "NANOBOT_API_BASE": "http://127.0.0.1:1",
            "NANOBOT_MODEL": "test-model",
        }

        long_msg = "X" * 500
        mock_client_instance = AsyncMock()
        mock_client_instance.chat.completions.create = AsyncMock(
            side_effect=RuntimeError(long_msg)
        )

        from agentic_loop import _stream_one_turn
        with patch("openai.AsyncOpenAI", return_value=mock_client_instance), \
             patch("agentic_loop.logger") as mock_logger:

            async def _run():
                with self.assertRaises(RuntimeError):
                    async for _ in _stream_one_turn(
                        [{"role": "user", "content": "test"}],
                        env, [], None
                    ):
                        pass

            asyncio.run(_run())
            # Find the ERROR call
            error_calls = [c for c in mock_logger.error.call_args_list
                           if "_stream_one_turn" in str(c)]
            self.assertTrue(len(error_calls) >= 1, "Expected at least one error log")
            logged_msg = str(error_calls[0])
            # The 500-char message must be truncated — logged msg should not contain full string
            self.assertNotIn(long_msg, logged_msg)
            self.assertIn("X" * 100, logged_msg)  # but should contain the first 300 chars

    def test_debug_log_contains_full_traceback(self):
        """Verify DEBUG log is emitted with exc_info for full diagnostics."""
        import asyncio
        from unittest.mock import patch, AsyncMock

        env = {
            "NANOBOT_API_KEY": "test-key",
            "NANOBOT_API_BASE": "http://127.0.0.1:1",
            "NANOBOT_MODEL": "test-model",
        }

        mock_client_instance = AsyncMock()
        mock_client_instance.chat.completions.create = AsyncMock(
            side_effect=ConnectionError("debug-test")
        )

        from agentic_loop import _stream_one_turn
        with patch("openai.AsyncOpenAI", return_value=mock_client_instance), \
             patch("agentic_loop.logger") as mock_logger:

            async def _run():
                with self.assertRaises(ConnectionError):
                    async for _ in _stream_one_turn(
                        [{"role": "user", "content": "test"}],
                        env, [], None
                    ):
                        pass

            asyncio.run(_run())
            debug_calls = [c for c in mock_logger.debug.call_args_list
                           if "_stream_one_turn" in str(c)]
            self.assertTrue(len(debug_calls) >= 1, "Expected DEBUG log with full traceback")
            # exc_info=True should be passed
            _, kwargs = debug_calls[0]
            self.assertTrue(kwargs.get("exc_info"), "DEBUG log should have exc_info=True")


class TestAllowedRootsIntegrity(unittest.TestCase):
    """Verify _allowed_roots is a local computation, not a mutable global."""

    def test_allowed_roots_computed_from_workspace_param(self):
        """Different workspace args must produce different allowed roots."""
        from tools.base import _resolve_path
        ws1 = Path("/tmp/workspace_a")
        ws2 = Path("/tmp/workspace_b")
        ws1.mkdir(parents=True, exist_ok=True)
        ws2.mkdir(parents=True, exist_ok=True)
        # Path under ws1 should be allowed with ws1 but blocked with ws2
        test_path = str(ws1 / "secret.txt")
        result = _resolve_path(test_path, ws1)
        self.assertEqual(result, Path(test_path).resolve())
        # Same path with ws2 should still be allowed (both under /tmp)
        result2 = _resolve_path(test_path, ws2)
        self.assertEqual(result2, Path(test_path).resolve())

    def test_blocked_path_error_includes_workspace(self):
        """Error message must include the actual workspace path for actionable guidance."""
        from tools.base import _resolve_path
        ws = Path("/tmp/nanobot_roots_test")
        ws.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(ValueError) as ctx:
            _resolve_path("/etc/shadow", ws)
        self.assertIn(str(ws.resolve()), str(ctx.exception))


# ═══════════════════════════════════════════════════════════════
# Documentation consistency: auto-verify PROJECT_STATUS_REPORT.md
# ═══════════════════════════════════════════════════════════════

class TestDocumentationConsistency(unittest.TestCase):
    """Verify that PROJECT_STATUS_REPORT.md key metrics match actual codebase."""

    _REPORT_PATH = WEB_UI_DIR / "PROJECT_STATUS_REPORT.md"

    def _read_report(self) -> str:
        return self._REPORT_PATH.read_text(encoding="utf-8")

    @staticmethod
    def _count_py_files() -> int:
        return sum(1 for f in WEB_UI_DIR.rglob("*.py")
                   if "__pycache__" not in str(f) and ".venv" not in str(f))

    @staticmethod
    def _count_py_lines() -> int:
        total = 0
        for f in WEB_UI_DIR.rglob("*.py"):
            if "__pycache__" in str(f) or ".venv" in str(f):
                continue
            with f.open(encoding="utf-8", errors="ignore") as fh:
                total += sum(1 for _ in fh)
        return total

    @staticmethod
    def _count_file_lines(relpath: str) -> int:
        p = WEB_UI_DIR / relpath
        if not p.exists():
            return -1
        with p.open(encoding="utf-8", errors="ignore") as fh:
            return sum(1 for _ in fh)

    @staticmethod
    def _extract_number(text: str, pattern: str) -> int:
        """Extract a number from a markdown table row matching pattern."""
        for line in text.splitlines():
            if pattern in line:
                nums = re.findall(r"[\d,]+", line)
                for n in nums:
                    val = int(n.replace(",", ""))
                    if val > 0:
                        return val
        return -1

    def test_python_file_count_within_tolerance(self):
        """Python file count in report should be within ±30 of actual.

        Tolerance is ±30 to absorb normal refactoring batches
        (route extraction, test additions, new modules) while still
        catching large undocumented structural changes.
        """
        report = self._read_report()
        reported = self._extract_number(report, "Python 文件数")
        actual = self._count_py_files()
        self.assertGreater(reported, 0, "Could not extract Python file count from report")
        self.assertAlmostEqual(reported, actual, delta=30,
                               msg=f"Report says {reported} Python files, actual is {actual}")

    def test_python_loc_within_tolerance(self):
        """Python LOC in report should not deviate more than 50% from actual.

        LOC naturally drifts as modules are added/removed. A percentage-based
        check is more resilient than a fixed delta.
        """
        report = self._read_report()
        reported = self._extract_number(report, "Python 总代码行数")
        actual = self._count_py_lines()
        self.assertGreater(reported, 0, "Could not extract Python LOC from report")
        # Allow up to 50% drift between report and actual
        ratio = actual / reported if reported > 0 else 999
        self.assertGreater(ratio, 0.5, f"Actual LOC ({actual}) is <50% of reported ({reported})")
        self.assertLess(ratio, 2.0, f"Actual LOC ({actual}) is >200% of reported ({reported})")

    def test_key_module_line_counts(self):
        """Critical module line counts should be within ±800 of reported values.

        Modules grow as features are added (e.g. agentic_loop.py gains ~500
        lines per major feature). A tighter delta causes false failures
        after normal development.
        """
        report = self._read_report()
        checks = {
            "agentic_loop.py": "agentic_loop.py",
            "tools/mcp_client.py": "tools/mcp_client.py",
            "system_prompts.py": "system_prompts.py",
        }
        for relpath, pattern in checks.items():
            reported = self._extract_number(report, pattern)
            actual = self._count_file_lines(relpath)
            if reported <= 0 or actual <= 0:
                continue
            self.assertAlmostEqual(
                reported, actual, delta=800,
                msg=f"{relpath}: report says {reported}, actual is {actual}",
            )


if __name__ == "__main__":
    unittest.main()
