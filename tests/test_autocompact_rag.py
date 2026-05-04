"""
Auto-Compact & RAG Integration Tests
=====================================
验证 agentic_loop.py 中新增的 auto-compact 和 RAG/KG 功能。

运行: python3 tests/test_autocompact_rag.py
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure web_ui is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure litellm is importable (venv path)
_venv_site = Path.home() / ".nanobot" / "venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
if _venv_site.exists() and str(_venv_site) not in sys.path:
    sys.path.insert(0, str(_venv_site))
_nanobot_venv = Path.home() / "nanobotProjects" / "nanobot" / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
if _nanobot_venv.exists() and str(_nanobot_venv) not in sys.path:
    sys.path.insert(0, str(_nanobot_venv))

from agentic_loop import (
    _estimate_tokens,
    _estimate_messages_tokens,
    _get_context_ceiling,
    _format_compact_summary,
    _messages_to_text,
    _auto_compact,
    _query_knowledge_graph,
    _query_rag_vectors,
    _build_rag_context,
    AGENTIC_SYSTEM_PROMPT,
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


# ═══════════════════════════════════════════════════════════════
# Section 1: Token Estimation
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 1. Token Estimation ══╗")

check("empty string = 0 tokens", _estimate_tokens("") == 0)
check("short string > 0", _estimate_tokens("Hello world") > 0)
check("CJK text counted", _estimate_tokens("你好世界，这是一段中文") > 0)

msgs = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hi there!"},
    {"role": "assistant", "content": "Hello! How can I help?"},
]
tok = _estimate_messages_tokens(msgs)
check(f"3 messages = {tok} tokens (>10)", tok > 10)

# With tool calls
msgs_tc = [
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"function": {"name": "grep_search", "arguments": '{"pattern": "test"}'}}
        ],
    }
]
tok_tc = _estimate_messages_tokens(msgs_tc)
check(f"tool_call message counted ({tok_tc} tokens)", tok_tc > 5)


# ═══════════════════════════════════════════════════════════════
# Section 2: Context Ceiling
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 2. Context Ceiling ══╗")

env_8k = {"OLLAMA_NUM_CTX": "8192", "NANOBOT_AGENTS__DEFAULTS__MAX_TOKENS": "4096"}
c = _get_context_ceiling(env_8k)
# 15% reserve: 8192 - min(4096, int(8192*0.15)) = 8192 - 1228 = 6964
check(f"8K ctx → ceiling={c} (expected 6964)", c == 6964)

env_32k = {"OLLAMA_NUM_CTX": "32768", "NANOBOT_AGENTS__DEFAULTS__MAX_TOKENS": "16384"}
c32 = _get_context_ceiling(env_32k)
# 15% reserve: 32768 - min(16384, int(32768*0.15)) = 32768 - 4915 = 27853
check(f"32K ctx → ceiling={c32} (expected 27853)", c32 == 27853)

env_default = {}
cd = _get_context_ceiling(env_default)
check(f"default ctx → ceiling={cd} (>= 2000)", cd >= 2000)


# ═══════════════════════════════════════════════════════════════
# Section 3: Compact Summary Formatting
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 3. Compact Summary Format ══╗")

raw1 = "<analysis>thinking...</analysis>\n<summary>\n1. User wanted X\n2. We did Y\n</summary>"
s1 = _format_compact_summary(raw1)
check("analysis stripped", "<analysis>" not in s1)
check("summary extracted", "1. User wanted X" in s1)

raw2 = "Just plain text without tags"
s2 = _format_compact_summary(raw2)
check("fallback: plain text returned", s2 == "Just plain text without tags")

raw3 = "<analysis>long thinking\nwith\nmultiple lines</analysis><summary>short</summary>"
s3 = _format_compact_summary(raw3)
check("multiline analysis stripped", s3 == "short")


# ═══════════════════════════════════════════════════════════════
# Section 4: Messages to Text Conversion
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 4. Messages to Text ══╗")

text = _messages_to_text(msgs)
check("[SYSTEM] present", "[SYSTEM]" in text)
check("[USER] present", "[USER]" in text)
check("[ASSISTANT] present", "[ASSISTANT]" in text)

# Long message truncation
long_msgs = [{"role": "user", "content": "x" * 5000}]
long_text = _messages_to_text(long_msgs)
check("long message truncated", "truncated" in long_text)
check("truncated < original", len(long_text) < 5000)


# ═══════════════════════════════════════════════════════════════
# Section 5: Auto-Compact Threshold Logic
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 5. Auto-Compact Threshold ══╗")

env = {"OLLAMA_NUM_CTX": "8192", "NANOBOT_AGENTS__DEFAULTS__MAX_TOKENS": "4096"}
ceiling = _get_context_ceiling(env)
threshold = int(ceiling * 0.80)
print(f"  (ceiling={ceiling}, threshold={threshold})")


# Build messages that are UNDER threshold
small_msgs = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hi"},
]
small_tok = _estimate_messages_tokens(small_msgs)
check(f"small msgs ({small_tok} tok) < threshold → no compact", small_tok < threshold)

# Build messages that EXCEED threshold
big_msgs = [{"role": "system", "content": "System prompt"}]
for i in range(30):
    big_msgs.append({"role": "user", "content": f"question {i} " * 80})
    big_msgs.append({"role": "assistant", "content": f"answer {i} " * 120})
big_tok = _estimate_messages_tokens(big_msgs)
check(f"big msgs ({big_tok} tok) > threshold → needs compact", big_tok > threshold)


# ═══════════════════════════════════════════════════════════════
# Section 6: Auto-Compact with Mocked LLM
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 6. Auto-Compact (mocked LLM) ══╗")


async def test_auto_compact_under_threshold():
    """Should return None when under threshold."""
    result = await _auto_compact(small_msgs, env, "test-session")
    return result is None


async def test_auto_compact_over_threshold():
    """Should compact when over threshold (mock LLM response)."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content="<analysis>thinking</analysis><summary>\n1. Primary: User asked questions\n2. Tech: Python\n3. Files: test.py\n4. Errors: none\n5. Solved: all\n6. Messages: Hi\n7. Pending: none\n8. Current: testing\n9. Next: verify\n</summary>"
            )
        )
    ]

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        result = await _auto_compact(big_msgs, env, "test-session")
        return result


async def test_auto_compact_llm_failure():
    """Should fallback to snip when LLM fails."""
    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=Exception("LLM down")):
        result = await _auto_compact(big_msgs, env, "test-session")
        return result


r1 = asyncio.run(test_auto_compact_under_threshold())
check("under threshold → None", r1 is True)

# Test with mocked LLM
try:
    r2 = asyncio.run(test_auto_compact_over_threshold())
    if r2 and r2.get("compacted"):
        check(f"over threshold → compacted ({r2['old_tokens']}→{r2['new_tokens']} tok)", True)
        check("new_messages has system+summary+tail", len(r2["new_messages"]) >= 3)
        check("summary_length > 0", r2.get("summary_length", 0) > 0)
    else:
        check("over threshold → compact attempted", r2 is not None)
except Exception as e:
    check("LLM mock failed (litellm not installed?)", False, str(e))

# Test LLM failure → fallback snip
try:
    r3 = asyncio.run(test_auto_compact_llm_failure())
    if r3 and r3.get("compacted"):
        check(f"LLM failure → fallback snip ({r3['old_tokens']}→{r3['new_tokens']} tok)", True)
        check("fallback summary_length = 0", r3.get("summary_length") == 0)
    else:
        check("fallback snip triggered", r3 is not None)
except Exception as e:
    check("fallback test failed", False, str(e))


# ═══════════════════════════════════════════════════════════════
# Section 7: Knowledge Graph Integration
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 7. Knowledge Graph ══╗")

# Test with empty DB
kg_empty = _query_knowledge_graph("test query")
check("empty KG → 0 results", len(kg_empty) == 0)

# Create temp DB with test data and test
tmpdir = tempfile.mkdtemp()
tmp_kg = Path(tmpdir) / "knowledge_graph.db"

conn = sqlite3.connect(str(tmp_kg))
conn.execute("""CREATE TABLE entities (
    entity_id TEXT, session_id TEXT, name TEXT, entity_type TEXT,
    attributes TEXT, created_at TIMESTAMP, updated_at TIMESTAMP,
    access_count INTEGER DEFAULT 0, importance REAL DEFAULT 0.5
)""")
conn.execute("""CREATE TABLE relations (
    relation_id TEXT, session_id TEXT, source_id TEXT, target_id TEXT,
    relation_type TEXT, weight REAL, attributes TEXT, created_at TIMESTAMP
)""")
conn.execute(
    "INSERT INTO entities VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'), 0, 0.9)",
    ("e1", "s1", "agentic_loop.py", "file", '{"description": "Main agentic loop"}'),
)
conn.execute(
    "INSERT INTO entities VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'), 0, 0.8)",
    ("e2", "s1", "AutoCompact", "class", '{"description": "Token budget manager"}'),
)
conn.execute(
    "INSERT INTO relations VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
    ("r1", "s1", "e2", "e1", "defined_in", 1.0, "{}"),
)
conn.commit()
conn.close()

# Patch the DB path to use temp
import agentic_loop
original_kg_path = agentic_loop._KG_DB_PATH
agentic_loop._KG_DB_PATH = tmp_kg

kg_results = _query_knowledge_graph("agentic_loop AutoCompact")
check(f"KG found {len(kg_results)} entities", len(kg_results) >= 1)
if kg_results:
    check("entity name correct", kg_results[0]["entity"] in ["agentic_loop.py", "AutoCompact"])
    check("entity has type", kg_results[0]["type"] in ["file", "class"])

# Restore
agentic_loop._KG_DB_PATH = original_kg_path


# ═══════════════════════════════════════════════════════════════
# Section 8: RAG Vector Store Integration
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 8. RAG Vector Store ══╗")

rag_empty = _query_rag_vectors("test query")
check("empty RAG → 0 results", len(rag_empty) == 0)

# Create temp RAG DB with test data
tmp_rag = Path(tmpdir) / "rag_vectors.db"
conn = sqlite3.connect(str(tmp_rag))
conn.execute("""CREATE TABLE vectors (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, chunk_id TEXT,
    content TEXT, embedding BLOB, metadata TEXT, created_at TIMESTAMP,
    accessed_at TIMESTAMP, access_count INTEGER DEFAULT 0, importance REAL DEFAULT 0.5
)""")
conn.execute(
    "INSERT INTO vectors (session_id, chunk_id, content, embedding, metadata, created_at, accessed_at, access_count, importance) "
    "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'), 0, 0.9)",
    ("s1", "c1", "The auto-compact feature compresses conversation history when tokens exceed the context window threshold.", b"", '{"source": "docs/compact.md"}'),
)
conn.execute(
    "INSERT INTO vectors (session_id, chunk_id, content, embedding, metadata, created_at, accessed_at, access_count, importance) "
    "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'), 0, 0.7)",
    ("s1", "c2", "RAG retrieval augmented generation provides relevant context from stored documents.", b"", '{"source": "docs/rag.md"}'),
)
conn.commit()
conn.close()

# Patch RAG path
original_rag_path = agentic_loop._RAG_DB_PATH
agentic_loop._RAG_DB_PATH = tmp_rag

rag_results = _query_rag_vectors("auto compact context window")
check(f"RAG found {len(rag_results)} chunks", len(rag_results) >= 1)
if rag_results:
    check("chunk has content", len(rag_results[0]["content"]) > 0)
    check("content is relevant", "compact" in rag_results[0]["content"].lower() or "context" in rag_results[0]["content"].lower())

# Verify access count updated
conn = sqlite3.connect(str(tmp_rag))
row = conn.execute("SELECT access_count FROM vectors WHERE chunk_id = 'c1'").fetchone()
if row:
    check(f"access_count incremented ({row[0]})", row[0] > 0)
conn.close()

# Restore
agentic_loop._RAG_DB_PATH = original_rag_path


# ═══════════════════════════════════════════════════════════════
# Section 9: RAG Context Builder (end-to-end)
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 9. RAG Context Builder ══╗")

# With empty DBs
ctx_empty = _build_rag_context("some question")
check("empty DBs → empty context", ctx_empty == "")

# With populated DBs
agentic_loop._KG_DB_PATH = tmp_kg
agentic_loop._RAG_DB_PATH = tmp_rag

ctx = _build_rag_context("auto compact agentic_loop")
check(f"populated DBs → context ({len(ctx)} chars)", len(ctx) > 0)
if ctx:
    check("contains [Knowledge Graph Context]", "[Knowledge Graph Context]" in ctx or "[Retrieved Context]" in ctx)

# Restore
agentic_loop._KG_DB_PATH = original_kg_path
agentic_loop._RAG_DB_PATH = original_rag_path

# Cleanup temp
import shutil
shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# Section 10: Integration — system prompt injection
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 10. System Prompt Integration ══╗")

check("AGENTIC_SYSTEM_PROMPT defined", len(AGENTIC_SYSTEM_PROMPT) > 50)
check("mentions tools", "tools" in AGENTIC_SYSTEM_PROMPT.lower())
check("mentions Nanobot", "Nanobot" in AGENTIC_SYSTEM_PROMPT)


# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed")
print(f"{'='*50}")
if FAIL == 0:
    print("🎉 All tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) failed — review above")
    sys.exit(1)
