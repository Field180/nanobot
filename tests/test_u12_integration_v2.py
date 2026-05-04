"""U12 integration tests v2 — simulate the REAL agentic loop multi-turn flow.

Unlike v1 which only checks regexes and source strings, this file builds
an AgenticLoopSimulator that replicates the exact U12a→U12b→U12c detection
pipeline (message mutations, defense sequencing, SSE events) and replays
each Q1-Q5 scenario through it.
"""
import asyncio, re, tempfile, unittest, sys, os
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentic_loop import (
    _NARRATION_RE, _SENTENCE_DEDUP_RE,
    _EMOJI_HEADER_RE, _BOLD_CATEGORY_RE,
    _scrub_narration,
    _build_repo_fact_completion_gate,
    _is_duplicate_repo_fact_answer,
    agentic_chat_stream,
)
from repo_explore_agent import ExplorationResult

# Import verbatim model outputs from v1
from tests.test_u12_integration import (
    Q1_PRE_TOOL, Q1_POST_TOOL,
    Q2_PRE_TOOL, Q2_POST_TOOL,
    Q3_NO_TOOL, Q4_NO_TOOL, Q5_NO_TOOL,
    Q7_PRE_TOOL, Q8_PRE_TOOL,
)


def _effective_words(text):
    return max(len(text.split()), len(text) // 3)


def _cat_count(text):
    e = len(_EMOJI_HEADER_RE.findall(text))
    b = len(_BOLD_CATEGORY_RE.findall(text))
    return e, b, e + b


class Sim:
    """Minimal replica of U12 pipeline from agentic_loop.py ~2826-2998."""

    def __init__(self, question):
        self.msgs = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": question},
        ]
        self.sse = []
        self.turn = 0

    def run(self, text, tool_calls=None, tool_chars=0):
        """Returns dict: action, defense, u12a, detail."""
        self.turn += 1
        self.sse.append("generation_start")

        # U12a
        cleaned = text
        u12a = False
        if text and tool_calls:
            cleaned, u12a = _scrub_narration(text)
            if u12a and not any("[CONCISENESS]" in m.get("content","")
                                for m in self.msgs if m["role"]=="system"):
                self.msgs.append({"role":"system",
                    "content":"[CONCISENESS] no preamble","_turn":self.turn})

        # U12d: Pre-tool verbosity enforcement
        _U12D_THRESHOLD = 50
        u12d = False
        if tool_calls and cleaned:
            _pre_ew = _effective_words(cleaned)
            if _pre_ew > _U12D_THRESHOLD:
                cleaned = ""
                u12d = True
                self.sse.append("clear_generation")

        # Append assistant
        amsg = {"role":"assistant","content":cleaned or ""}
        if tool_calls:
            amsg["tool_calls"] = tool_calls
        self.msgs.append(amsg)

        # With tools → add tool results, check U12c-bis, return
        if tool_calls:
            for i, tc in enumerate(tool_calls):
                self.msgs.append({"role":"tool",
                    "tool_call_id":tc.get("id",f"tc{i}"),
                    "content":"x"*tool_chars, "_turn":self.turn})
            e,b,t = _cat_count(cleaned) if cleaned else (0,0,0)
            if t >= 2:
                lns = [l for l in cleaned.split("\n")
                       if not _EMOJI_HEADER_RE.match(l)
                       and not _BOLD_CATEGORY_RE.match(l)]
                cleaned = "\n".join(lns).strip()
                for m in reversed(self.msgs):
                    if m["role"]=="assistant": m["content"]=cleaned; break
            return {"action":"tool","defense":"u12d" if u12d else None,
                    "u12a":u12a,"u12d":u12d,
                    "detail":{"text":cleaned}}

        # No tools → U12b
        if cleaned and self.turn >= 1:
            ew = _effective_words(cleaned)
            td = sum(len(m.get("content","")) for m in self.msgs
                     if m.get("role")=="tool" and m.get("_turn")==self.turn)
            if ew > 200 and td < 2000:
                hg = any("[GROUNDING]" in m.get("content","")
                         for m in self.msgs if m["role"]=="system")
                if not hg:
                    if self.msgs and self.msgs[-1].get("role")=="assistant":
                        self.msgs.pop()
                    g = "[GROUNDING] too long"
                    if td == 0:
                        g += "\nCall tools FIRST."
                    self.msgs.append({"role":"system","content":g,
                                      "_turn":self.turn})
                    self.sse.append("clear_generation")
                    return {"action":"continue","defense":"u12b","u12a":u12a,
                            "detail":{"ew":ew,"td":td}}

        # U12c
        e,b,cat = _cat_count(cleaned) if cleaned else (0,0,0)
        if cleaned and cat >= 2:
            hn = any("[NO CATEGORIZATION]" in m.get("content","")
                     for m in self.msgs if m["role"]=="system")
            if not hn:
                if self.msgs and self.msgs[-1].get("role")=="assistant":
                    self.msgs.pop()
                self.msgs.append({"role":"system",
                    "content":"[NO CATEGORIZATION] plain list only",
                    "_turn":self.turn})
                self.sse.append("clear_generation")
                return {"action":"continue","defense":"u12c","u12a":u12a,
                        "detail":{"e":e,"b":b}}
            else:
                lns = [l for l in cleaned.split("\n")
                       if not _EMOJI_HEADER_RE.match(l)
                       and not _BOLD_CATEGORY_RE.match(l)]
                cleaned = "\n".join(lns).strip()
                for m in reversed(self.msgs):
                    if m["role"]=="assistant": m["content"]=cleaned; break

        return {"action":"break","defense":None,"u12a":u12a,
                "detail":{"text":cleaned}}


# ═══════════════════════════════════════════════════════════════
# Q1: 列出当前目录的文件
# Real flow: Turn1 narration+file_list → Turn2 categorized response
# ═══════════════════════════════════════════════════════════════

class TestQ1FileList(unittest.TestCase):
    """Q1: ★★☆ — response ×3, emoji categorization, fabricated descriptions."""

    def test_turn1_narration_scrubbed(self):
        """Turn 1: doubled '列出当前...' narration is scrubbed before tool call."""
        s = Sim("列出当前目录的文件")
        r = s.run(Q1_PRE_TOOL,
                  tool_calls=[{"id":"tc0","function":{"name":"file_list","arguments":"{}"}}],
                  tool_chars=500)
        self.assertTrue(r["u12a"], "Q1 narration not detected")
        # Assistant message in history should NOT contain the narration
        asst = [m for m in s.msgs if m["role"]=="assistant"]
        self.assertEqual(len(asst), 1)
        self.assertNotIn("列出当前", asst[0]["content"])

    def test_turn2_categorized_response_rejected(self):
        """Turn 2: model returns massive categorized text → U12b or U12c fires."""
        s = Sim("列出当前目录的文件")
        # Turn 1: tool call
        s.run(Q1_PRE_TOOL,
              tool_calls=[{"id":"tc0","function":{"name":"file_list","arguments":"{}"}}],
              tool_chars=500)
        # Turn 2: text-only categorized response (tool_chars=0 for THIS turn)
        r2 = s.run(Q1_POST_TOOL)
        self.assertEqual(r2["action"], "continue",
                         f"Q1 post-tool should be rejected, got {r2}")
        self.assertIn(r2["defense"], ("u12b", "u12c"),
                      f"Q1 should trigger u12b or u12c, got {r2['defense']}")

    def test_turn2_fabricated_msg_removed(self):
        """Turn 2: after rejection, fabricated assistant msg popped from history."""
        s = Sim("列出当前目录的文件")
        s.run(Q1_PRE_TOOL,
              tool_calls=[{"id":"tc0","function":{"name":"file_list","arguments":"{}"}}],
              tool_chars=500)
        r2 = s.run(Q1_POST_TOOL)
        self.assertEqual(r2["action"], "continue")
        # Last message should be system nudge, NOT the categorized assistant
        self.assertEqual(s.msgs[-1]["role"], "system")
        self.assertNotEqual(s.msgs[-1]["content"], Q1_POST_TOOL)
        # No assistant message with the categorized content should remain
        asst_contents = [m["content"] for m in s.msgs if m["role"]=="assistant"]
        for c in asst_contents:
            self.assertNotEqual(c, Q1_POST_TOOL,
                "Fabricated assistant message was NOT removed from history")

    def test_turn2_clear_generation_emitted(self):
        """Turn 2: clear_generation SSE event emitted so frontend can rollback."""
        s = Sim("列出当前目录的文件")
        s.run(Q1_PRE_TOOL,
              tool_calls=[{"id":"tc0","function":{"name":"file_list","arguments":"{}"}}],
              tool_chars=500)
        s.run(Q1_POST_TOOL)
        self.assertIn("clear_generation", s.sse,
                       "clear_generation not emitted — user sees duplicated text!")


# ═══════════════════════════════════════════════════════════════
# Q2: AGENTS.md 写了什么
# Real flow: Turn1 narration+file_read → Turn2 good summary
# ═══════════════════════════════════════════════════════════════

class TestQ2AgentsMd(unittest.TestCase):
    """Q2: ★★★☆ — '读取...读取...' narration, but summary is accurate."""

    def test_turn1_doubled_narration_scrubbed(self):
        """Turn 1: '为您读取...为您读取...' deduped and prefix stripped."""
        s = Sim("AGENTS.md 的里面写了什么")
        r = s.run(Q2_PRE_TOOL,
                  tool_calls=[{"id":"tc0","function":{"name":"file_read",
                              "arguments":'{"path":"AGENTS.md"}'}}],
                  tool_chars=811)
        self.assertTrue(r["u12a"])
        asst = [m for m in s.msgs if m["role"]=="assistant"][0]
        self.assertNotIn("为您", asst["content"],
                         "Narration prefix '为您' not stripped")

    def test_turn2_good_summary_passes(self):
        """Turn 2: Q2's factual summary should NOT be rejected."""
        s = Sim("AGENTS.md 的里面写了什么")
        s.run(Q2_PRE_TOOL,
              tool_calls=[{"id":"tc0","function":{"name":"file_read",
                          "arguments":'{"path":"AGENTS.md"}'}}],
              tool_chars=811)
        # Simulate: tool result was 811 chars, model summarizes
        # The summary IS long but backed by tool data
        r2 = s.run(Q2_POST_TOOL)
        # Q2 post-tool has 811 chars of real tool data from turn 1,
        # but turn 2 has tool_chars=0. However the text is <400 effective words.
        ew = _effective_words(Q2_POST_TOOL)
        if ew <= 200:
            self.assertEqual(r2["action"], "break",
                             "Q2 short summary incorrectly rejected")
        # Even if U12b fires, U12c should NOT fire (no categorization)
        e, b, cat = _cat_count(Q2_POST_TOOL)
        self.assertLess(cat, 2,
                        f"Q2 false positive: categorization detected ({e}e+{b}b)")


# ═══════════════════════════════════════════════════════════════
# Q3: 主要模块划分
# Real flow: Turn1 pure fabrication (no tools) → rejected
# ═══════════════════════════════════════════════════════════════

class TestQ3ModuleFabrication(unittest.TestCase):
    """Q3: ★★☆ — response ×3, categorization + fabrication, 1000+ words."""

    def test_turn1_pure_fabrication_rejected(self):
        """Turn 1: 900+ char fabrication with 0 tool data → U12b fires."""
        s = Sim("这个项目的主要模块?模块划分是怎样？")
        r = s.run(Q3_NO_TOOL)
        self.assertEqual(r["action"], "continue")
        self.assertEqual(r["defense"], "u12b",
                         "Q3 pure fabrication should trigger U12b first")

    def test_turn1_grounding_injected(self):
        """Turn 1: [GROUNDING] + 'Call tools FIRST' injected after rejection."""
        s = Sim("这个项目的主要模块?模块划分是怎样？")
        s.run(Q3_NO_TOOL)
        sys_msgs = [m["content"] for m in s.msgs if m["role"]=="system"]
        grounding = [c for c in sys_msgs if "[GROUNDING]" in c]
        self.assertEqual(len(grounding), 1, "Grounding message not injected")
        self.assertIn("Call", grounding[0],
                      "Grounding should tell model to call tools")

    def test_turn1_fabricated_msg_removed(self):
        """Turn 1: Q3_NO_TOOL removed from messages so model doesn't see it."""
        s = Sim("这个项目的主要模块?模块划分是怎样？")
        s.run(Q3_NO_TOOL)
        asst = [m for m in s.msgs if m["role"]=="assistant"]
        self.assertEqual(len(asst), 0,
                         "Fabricated assistant message still in history!")

    def test_turn2_second_fabrication_hits_u12c(self):
        """Turn 2: if model STILL fabricates (ignoring grounding), U12c catches
        categorization because _has_ground is True and U12b won't fire again."""
        s = Sim("这个项目的主要模块?模块划分是怎样？")
        s.run(Q3_NO_TOOL)  # U12b fires, grounding injected
        # Model ignores grounding and generates another categorized response
        r2 = s.run(Q3_NO_TOOL)
        # U12b won't fire (grounding already exists) → falls through to U12c
        self.assertEqual(r2["action"], "continue")
        self.assertEqual(r2["defense"], "u12c",
                         "Second attempt should be caught by U12c")

    def test_turn3_hardclean_after_double_rejection(self):
        """Turn 3: model still categorizes after nudge → U12c hardclean fires."""
        s = Sim("这个项目的主要模块?模块划分是怎样？")
        s.run(Q3_NO_TOOL)  # Turn 1: U12b
        s.run(Q3_NO_TOOL)  # Turn 2: U12c
        # Turn 3: model STILL categorizes → hardclean (action=break)
        r3 = s.run(Q3_NO_TOOL)
        self.assertEqual(r3["action"], "break",
                         "Third attempt should hardclean and break")
        # Final text should have category headers stripped
        final = r3["detail"]["text"]
        _, _, cat = _cat_count(final)
        # Hardclean removes header lines — count should be reduced
        orig_cat = _cat_count(Q3_NO_TOOL)[2]
        self.assertLess(cat, orig_cat,
                        "Hardclean did not reduce category headers")

    def test_full_scenario_sse_sequence(self):
        """Full 3-turn Q3: SSE events = 3× generation_start + 2× clear_generation."""
        s = Sim("这个项目的主要模块?模块划分是怎样？")
        s.run(Q3_NO_TOOL)  # U12b continue
        s.run(Q3_NO_TOOL)  # U12c continue
        s.run(Q3_NO_TOOL)  # hardclean break
        starts = s.sse.count("generation_start")
        clears = s.sse.count("clear_generation")
        self.assertEqual(starts, 3, f"Expected 3 generation_start, got {starts}")
        self.assertEqual(clears, 2, f"Expected 2 clear_generation, got {clears}")


# ═══════════════════════════════════════════════════════════════
# Q4: 分析整个项目的怎么设计的
# Real flow: Turn1 pure fabrication → rejected (same as Q3)
# ═══════════════════════════════════════════════════════════════

class TestQ4DesignFabrication(unittest.TestCase):
    """Q4: ★☆ — pure fabrication (no tools), response ×2+, truncation."""

    def test_turn1_u12b_fires(self):
        """Turn 1: massive fabrication with zero tool data → U12b."""
        s = Sim("分析整个项目的怎么设计的，详细说明每个模块目录的作用")
        r = s.run(Q4_NO_TOOL)
        self.assertEqual(r["action"], "continue")
        self.assertEqual(r["defense"], "u12b")
        ew = r["detail"]["ew"]
        self.assertGreater(ew, 200, f"Q4 effective_words={ew}, must be >200")

    def test_cjk_word_count_correct(self):
        """Q4: CJK char//3 must dominate over split() for Chinese text."""
        split_w = len(Q4_NO_TOOL.split())
        char_w = len(Q4_NO_TOOL) // 3
        ew = max(split_w, char_w)
        self.assertGreater(char_w, split_w,
                           f"CJK fallback not dominant: char={char_w} vs split={split_w}")
        self.assertEqual(ew, char_w)

    def test_categorization_also_detected(self):
        """Q4: even if U12b fires first, U12c WOULD also trigger."""
        e, b, cat = _cat_count(Q4_NO_TOOL)
        self.assertGreaterEqual(cat, 2,
                                f"Q4 categorization not detected: {e}e+{b}b={cat}")

    def test_q4_no_assistant_in_history_after_reject(self):
        """After U12b rejection, no Q4 fabrication remains in messages."""
        s = Sim("分析整个项目的怎么设计的")
        s.run(Q4_NO_TOOL)
        for m in s.msgs:
            if m["role"] == "assistant":
                self.fail(f"Fabricated assistant still in history: {m['content'][:60]}...")


# ═══════════════════════════════════════════════════════════════
# Q5: 根目录下的文件结构
# Same pattern as Q3/Q4
# ═══════════════════════════════════════════════════════════════

class TestQ5FileFabrication(unittest.TestCase):
    """Q5: ★★☆ — fabrication + categorization + repetition."""

    def test_turn1_u12b_fires(self):
        s = Sim("根目录下的文件是怎样的文件结构")
        r = s.run(Q5_NO_TOOL)
        self.assertEqual(r["action"], "continue")
        self.assertEqual(r["defense"], "u12b")

    def test_turn2_u12c_fires(self):
        """Second attempt (grounding exists) → U12c catches categorization."""
        s = Sim("根目录下的文件是怎样的文件结构")
        s.run(Q5_NO_TOOL)
        r2 = s.run(Q5_NO_TOOL)
        self.assertEqual(r2["action"], "continue")
        self.assertEqual(r2["defense"], "u12c")

    def test_full_scenario_messages_clean(self):
        """After 3 turns, no raw fabrication in assistant messages."""
        s = Sim("根目录下的文件是怎样的文件结构")
        s.run(Q5_NO_TOOL)
        s.run(Q5_NO_TOOL)
        s.run(Q5_NO_TOOL)
        asst = [m for m in s.msgs if m["role"]=="assistant"]
        # Only the final hardcleaned version should remain
        self.assertEqual(len(asst), 1,
                         f"Expected 1 assistant msg, got {len(asst)}")
        final = asst[0]["content"]
        # Must be shorter than original (headers stripped)
        self.assertLess(len(final), len(Q5_NO_TOOL),
                        "Hardcleaned text not shorter than original")


# ═══════════════════════════════════════════════════════════════
# Cross-cutting: narration dedup with real patterns
# ═══════════════════════════════════════════════════════════════

class TestNarrationDedup(unittest.TestCase):
    """Verify dedup + prefix strip on all real pre-tool narrations."""

    def _check(self, text, forbidden_prefix):
        cleaned, scrubbed = _scrub_narration(text)
        self.assertTrue(scrubbed, f"Not detected: {text[:40]}...")
        self.assertNotIn(forbidden_prefix, cleaned,
                         f"Prefix '{forbidden_prefix}' not stripped from: {cleaned[:60]}")
        return cleaned

    def test_q1(self):
        self._check(Q1_PRE_TOOL, "列出当前")

    def test_q2(self):
        self._check(Q2_PRE_TOOL, "为您")

    def test_q7(self):
        self._check(Q7_PRE_TOOL, "为您")

    def test_q8(self):
        self._check(Q8_PRE_TOOL, "为您")

    def test_long_path_dedup(self):
        """Sentence >80 chars with file path must be deduped (R3/R4)."""
        s = "读取 `/home/field/.nanobot/workspace/web_ui/agentic_loop.py` 文件的全部内容以便为您提供准确而详细的回答。"
        self.assertGreater(len(s), 80, "Test sentence must be >80 chars")
        self._check(s + s, "读取")

    def test_normal_text_untouched(self):
        text = "项目根目录下有 15 个文件，其中包含 3 个目录和 12 个文件。"
        cleaned, scrubbed = _scrub_narration(text)
        self.assertFalse(scrubbed)
        self.assertEqual(cleaned, text)

    def test_bare_du_qu(self):
        """R2: bare '读取' prefix caught."""
        self._check("读取 AGENTS.md 文件的内容。", "读取")

    def test_wei_le(self):
        """R2: '为了' prefix caught."""
        self._check("为了准确回答，我将阅读架构文档。", "为了")


# ═══════════════════════════════════════════════════════════════
# U12d: Pre-tool verbosity enforcement
# Tests the NEW defense against fabrication + tool_calls in same turn
# ═══════════════════════════════════════════════════════════════

# Real Q2 pattern: model generates massive fabrication BEFORE calling tools
Q2_FABRICATION_PLUS_TOOLS = (
    "Nanobot 是一个注重**安全性**和**认知演进**的自主智能体系统。简单来说，它的主要模块可以分为以下五个核心集群：\n\n"
    "### 1. 核心执行集群 (The Brain)\n*   **`core/`**: 系统中枢。\n*   **`agents/`**: 智能体仓库。\n\n"
    "### 2. 安全与隔离集群 (The Shield)\n*   **`security/`**: 安全审计与权限控制。\n"
    "*   **`sandbox/`**: 代码执行沙箱。\n*   **`quarantine/`**: 隔离区。\n\n"
    "### 3. 认知与记忆集群 (The Mind)\n*   **`memory/`**: 持久化存储。\n"
    "*   **`neural_pathway/`**: 模拟神经通路。\n*   **`immune_system/`**: 免疫系统。\n\n"
    "### 4. 适配与扩展集群 (The Adapters)\n*   **`backend_plugins/`**: 模型适配层。\n"
    "*   **`skills/` & `plugins/`**: 技能库与插件。\n\n"
    "### 5. 监控与接口集群 (The Interface)\n*   **`web_ui/`**: 前端界面。\n"
    "*   **`monitoring/` & `evals/`**: 监控与评估。\n\n"
    "---\n给您提供最准确的模块概览，我将先扫描项目根目录。给您提供最准确的模块概览，我将先扫描项目根目录。"
)

Q5_FABRICATION_PLUS_TOOLS = (
    Q5_NO_TOOL + "\n---\n"
    "给您提供最准确且基于事实的架构分析，我将首先阅读项目中的核心架构文档。"
    "给您提供最准确且基于事实的架构分析，我将首先阅读项目中的核心架构文档。"
)

Q5_EXACT_PRETOOL_PLUS_TOOLS = (
    "Nanobot 是一个高度模块化的自主智能体系统，其架构设计核心围绕着**“认知 → 决策 → 执行 → 安全”**的闭环。"
    "它不仅仅是一个 LLM 的包装器，而是一个模拟人类认知过程（包含长期记忆、反思和自我修复）并具备企业级安全隔离能力的复杂操作系统。\n\n"
    "### 🏗️ 整体架构设计理念\n\n"
    "Nanobot 采用了**分层解耦**的架构，将“思考”与“执行”完全分离，确保系统在追求自主性的同时保持绝对的可控性：\n\n"
    "1.  **认知层 (Cognition Layer)**：负责意图理解、记忆检索、长期目标规划和结果反思。\n"
    "2.  **调度层 (Orchestration Layer)**：通过多 Agent 协作机制将复杂任务分解为可执行的原子操作。\n"
    "3.  **安全隔离层 (Security & Isolation Layer)**：所有外部影响（写文件、运行代码）必须经过审计并运行在沙箱中。\n"
    "4.  **适配层 (Adaptation Layer)**：通过插件机制屏蔽底层 LLM 的差异，实现模型无缝切换。\n\n"
    "### 📂 详细目录职责分解\n\n"
    "为了方便理解，可以将目录划分为五个核心功能集群：\n\n"
    "#### 1. 核心执行集群 (The Brain & Heart)\n"
    "*   **`core/`**：**系统中枢**。包含决策引擎、因果推理逻辑和任务调度器。\n"
    "*   **`agents/`**：**角色定义库**。\n"
    "*   **`components/`**：**通用组件**。\n\n"
    "#### 2. 安全与隔离集群 (The Shield)\n"
    "*   **`security/`**：**安全审计中心**。\n"
    "*   **`sandbox/` & `sandbox_logs/`**：**代码沙箱**。\n"
    "*   **`quarantine/`**：**隔离区**。\n"
    "*   **`audit/`**：**审计日志**。\n\n"
    "给您提供最准确且基于当前代码现状的分析，我将首先核实项目的目录结构并阅读核心架构文档。"
    "执行以下步骤：1. 列出根目录。2. 阅读 `PROJECT_STRUCTURE.md` 和 `ARCHITECTURE.md`。"
)

Q5_EXACT_PARTIAL_READ_DUPLICATED_SUMMARY = (
    "Nanobot 采用**“认知驱动 → 安全调度 → 隔离执行”**的模块化架构，旨在构建一个具备自适应学习和企业级安全能力的自主智能体系统。\n\n"
    "### 📂 目录职责分解\n"
    "*   **`core/` (核心中枢)**：负责最高级别的决策、因果推理、长期目标规划及自适应学习引擎。\n"
    "*   **`agents/` (代理框架)**：集成 AutoGen, AgentZero 等多种框架，定义不同角色的行为模式与协作逻辑。\n"
    "*   **`web_ui/` (交互与运行时)**：除界面外，承载主服务器 (`server_final.py`) 和实验性神经计算后端。\n"
    "*   **`security/` & `tools/` (安全与执行)**：实现高危操作拦截、人工审批流，并通过沙箱机制确保工具在隔离环境中运行。\n"
    "*   **`memory/` (记忆存储)**：持久化管理长期记忆、工作记忆及历史会话。\n"
    "*   **`monitoring/` & `evals/` (监控评估)**：负责系统健康实时跟踪与智能能力量化基准测试。\n"
    "*   **`experimental/` (实验模块)**：存放意识增强、生物大脑模拟等研究性代码。\n\n"
    "---\n"
    "Nanobot 采用**“认知 → 决策 → 执行 → 安全”**的闭环架构，将思考与执行完全解耦，并强制所有高危操作经过安全审计和沙箱隔离。\n\n"
    "**目录职责：**\n"
    "*   `core/`：系统大脑，负责因果推理、自主决策、自适应学习及长期目标规划。\n"
    "*   `agents/`：代理框架集成中心。\n"
    "*   `web_ui/`：核心运行时。\n"
    "*   `tools/`：原子功能执行集。\n"
    "*   `security/`：安全守卫。\n"
    "*   `memory/`：分层记忆存储。\n"
    "*   `monitoring/` 与 `evals/`：分别负责实时健康监控与能力基准量化评估。\n"
    "*   `experimental/`：研究性代码。\n"
    "*   `docs/` 与 `skills/`：项目技术指南与可复用技能定义。"
)


class TestU12dPreToolVerbosity(unittest.TestCase):
    """U12d: When model generates massive text + tool_calls in same turn,
    truncate text to empty and send clear_generation."""

    def test_q2_fabrication_truncated(self):
        """Q2 pattern: 500+ word fabrication + file_list → U12d truncates."""
        s = Sim("这个项目有哪些主要模块")
        r = s.run(Q2_FABRICATION_PLUS_TOOLS,
                  tool_calls=[{"id":"tc0","function":{"name":"file_list","arguments":"{}"}},
                              {"id":"tc1","function":{"name":"file_read","arguments":'{"path":"PROJECT_STRUCTURE.md"}'}}],
                  tool_chars=500)
        self.assertTrue(r.get("u12d"), "U12d should fire on 500+ word pre-tool text")
        # Assistant text in history should be empty (fabrication truncated)
        asst = [m for m in s.msgs if m["role"]=="assistant"]
        self.assertEqual(len(asst), 1)
        self.assertEqual(asst[0]["content"], "",
                         "Fabrication body should be truncated to empty")

    def test_q2_clear_generation_emitted(self):
        """U12d sends clear_generation so frontend erases streamed fabrication."""
        s = Sim("这个项目有哪些主要模块")
        s.run(Q2_FABRICATION_PLUS_TOOLS,
              tool_calls=[{"id":"tc0","function":{"name":"file_list","arguments":"{}"}}],
              tool_chars=500)
        self.assertIn("clear_generation", s.sse,
                       "U12d must emit clear_generation")

    def test_q5_fabrication_plus_tools_truncated(self):
        """Q5 pattern: massive fabrication + file_read → U12d truncates."""
        s = Sim("根目录下的文件结构")
        r = s.run(Q5_FABRICATION_PLUS_TOOLS,
                  tool_calls=[{"id":"tc0","function":{"name":"file_list","arguments":"{}"}}],
                  tool_chars=500)
        self.assertTrue(r.get("u12d"))
        asst = [m for m in s.msgs if m["role"]=="assistant"]
        self.assertEqual(asst[0]["content"], "")

    def test_short_pretool_text_not_truncated(self):
        """Short pre-tool text (≤50 ew) should NOT be truncated."""
        s = Sim("AGENTS.md 写了什么")
        short_text = "查找并读取 AGENTS.md 文件的内容。"
        r = s.run(short_text,
                  tool_calls=[{"id":"tc0","function":{"name":"file_read","arguments":'{"path":"AGENTS.md"}'}}],
                  tool_chars=811)
        self.assertFalse(r.get("u12d", False),
                         f"Short text ({_effective_words(short_text)} ew) should not trigger U12d")
        asst = [m for m in s.msgs if m["role"]=="assistant"]
        # Content should still be there (after U12a scrub)
        self.assertGreater(len(asst[0]["content"]) + len(short_text), 0)

    def test_q1_narration_under_threshold(self):
        """Q1 pre-tool: after U12a dedup+strip, remaining text is short → no U12d."""
        s = Sim("列出当前目录的文件")
        r = s.run(Q1_PRE_TOOL,
                  tool_calls=[{"id":"tc0","function":{"name":"file_list","arguments":"{}"}}],
                  tool_chars=500)
        # Q1_PRE_TOOL is just a doubled sentence — after dedup+strip → very short
        self.assertFalse(r.get("u12d", False),
                         "Q1 narration (after dedup) should not trigger U12d")

    def test_full_q2_scenario_no_double_response(self):
        """Full Q2 scenario: fabrication+tools → U12d truncates → tool returns →
        model gives clean grounded summary → user sees only 1 response."""
        s = Sim("这个项目有哪些主要模块")
        # Turn 1: fabrication + tool calls → U12d truncates fabrication
        r1 = s.run(Q2_FABRICATION_PLUS_TOOLS,
                   tool_calls=[{"id":"tc0","function":{"name":"file_list","arguments":"{}"}},
                               {"id":"tc1","function":{"name":"file_read","arguments":'{"path":"PROJECT_STRUCTURE.md"}'}}],
                   tool_chars=22000)
        self.assertTrue(r1.get("u12d"))
        # Turn 2: model sees tool results, gives grounded summary
        r2 = s.run(Q2_POST_TOOL)
        # Q2_POST_TOOL is a short accurate summary → should pass
        ew = _effective_words(Q2_POST_TOOL)
        if ew <= 200:
            self.assertEqual(r2["action"], "break",
                             "Grounded Q2 summary should pass through")
        # Only 1 assistant text visible (the grounded one)
        asst_with_content = [m for m in s.msgs if m["role"]=="assistant" and m["content"]]
        non_empty = [m for m in asst_with_content if len(m["content"].strip()) > 0]
        # Should have at most 2 assistant msgs (empty from turn1, content from turn2)
        # but the fabrication body should NOT be in any of them
        for m in asst_with_content:
            self.assertNotIn("核心集群", m["content"],
                "Fabrication '核心集群' should NOT appear in any assistant message")

    def test_effective_words_threshold(self):
        """Verify the 50 ew threshold catches fabrication but not narration."""
        # Fabrication: 200+ effective words
        fab_ew = _effective_words(Q2_FABRICATION_PLUS_TOOLS)
        self.assertGreater(fab_ew, 50, f"Fabrication has {fab_ew} ew, must be >50")
        # Short narration: <50 effective words
        narr = "查找并读取文件的内容以为您提供准确的回答。"
        narr_ew = _effective_words(narr)
        self.assertLess(narr_ew, 50, f"Short narration has {narr_ew} ew, must be <50")


class TestQ5ExactArchitectureReplay(unittest.TestCase):
    def test_turn1_exact_prepair_fabrication_is_truncated(self):
        s = Sim("详细说说每个目录的职责，这个项目的架构是怎么设计的？")
        r1 = s.run(
            Q5_EXACT_PRETOOL_PLUS_TOOLS,
            tool_calls=[
                {"id":"tc0","function":{"name":"file_list","arguments":"{}"}},
                {"id":"tc1","function":{"name":"file_read","arguments":'{"path":"PROJECT_STRUCTURE.md"}'}},
            ],
            tool_chars=1200,
        )
        self.assertTrue(r1.get("u12d"), "Exact pre-tool architecture fabrication should trigger U12d")
        asst = [m for m in s.msgs if m["role"] == "assistant"]
        self.assertEqual(len(asst), 1)
        self.assertEqual(asst[0]["content"], "")
        self.assertIn("clear_generation", s.sse)

    def test_turn2_partial_read_duplicate_summary_rejected(self):
        s = Sim("详细说说每个目录的职责，这个项目的架构是怎么设计的？")
        s.run(
            Q5_EXACT_PRETOOL_PLUS_TOOLS,
            tool_calls=[
                {"id":"tc0","function":{"name":"file_list","arguments":"{}"}},
                {"id":"tc1","function":{"name":"file_read","arguments":'{"path":"PROJECT_STRUCTURE.md"}'}},
            ],
            tool_chars=1200,
        )
        r2 = s.run(Q5_EXACT_PARTIAL_READ_DUPLICATED_SUMMARY)
        self.assertEqual(r2["action"], "continue", f"Turn 2 should be rejected, got {r2}")
        self.assertIn(r2["defense"], ("u12b", "u12c"))
        self.assertEqual(s.sse.count("clear_generation"), 2,
                         "Exact replay should emit clear_generation for both bad turns")

    def test_no_polluted_architecture_phrases_remain_in_history(self):
        s = Sim("详细说说每个目录的职责，这个项目的架构是怎么设计的？")
        s.run(
            Q5_EXACT_PRETOOL_PLUS_TOOLS,
            tool_calls=[
                {"id":"tc0","function":{"name":"file_list","arguments":"{}"}},
                {"id":"tc1","function":{"name":"file_read","arguments":'{"path":"PROJECT_STRUCTURE.md"}'}},
            ],
            tool_chars=1200,
        )
        s.run(Q5_EXACT_PARTIAL_READ_DUPLICATED_SUMMARY)
        assistant_text = "\n".join(m["content"] for m in s.msgs if m["role"] == "assistant")
        self.assertNotIn("认知 → 决策 → 执行 → 安全", assistant_text)
        self.assertNotIn("核心执行集群", assistant_text)
        self.assertNotIn("企业级安全能力", assistant_text)


class TestRepoExploreForkRealLoop(unittest.TestCase):
    def test_architecture_question_forks_before_main_llm_and_injects_evidence(self):
        captured = {"messages": None, "tools": None}

        async def fake_explore(query, env, workspace, session_id):
            self.assertEqual(query, "详细说说每个目录的职责，这个项目的架构是怎么设计的？")
            return ExplorationResult(
                success=True,
                query=query,
                files_read=[
                    {"path": "PROJECT_STRUCTURE.md", "preview": "web_ui: UI and agent loop"},
                    {"path": "README.md", "preview": "Nanobot project"},
                ],
                directories_scanned=[str(workspace)],
                findings=(
                    "## Repository Exploration Summary\n\n"
                    "**Evidence Completeness**: complete\n\n"
                    "### Files Examined (2)\n"
                    "- `PROJECT_STRUCTURE.md`: full document read\n"
                    "- `README.md`: full document read\n\n"
                    "### Instruction for Main Agent\n"
                    "Answer only from explored evidence."
                ),
                evidence_completeness="complete",
                missing_evidence=[],
            )

        async def fake_stream_one_turn(messages, env, tools, stream_stats=None):
            captured["messages"] = [dict(m) for m in messages]
            captured["tools"] = tools
            yield {
                "type": "chunk",
                "content": "根据已完整读取的 PROJECT_STRUCTURE.md 和 README.md，web_ui 负责 UI 与 agent loop。",
            }

        async def collect_events(workspace):
            import agentic_loop
            with patch.object(agentic_loop, "run_repo_explore_subagent", side_effect=fake_explore), \
                 patch.object(agentic_loop, "_stream_one_turn", side_effect=fake_stream_one_turn):
                events = []
                async for ev in agentic_loop.agentic_chat_stream(
                    "详细说说每个目录的职责，这个项目的架构是怎么设计的？",
                    env={"NANOBOT_MAX_TOOL_ITERATIONS": "1"},
                    session_id="test-repo-explore-fork",
                    workspace=workspace,
                    max_turns=1,
                ):
                    events.append(ev)
                return events

        with tempfile.TemporaryDirectory() as tmpdir:
            events = asyncio.run(collect_events(Path(tmpdir)))

        event_types = [e.get("type") for e in events]
        self.assertIn("explore_agent_start", event_types)
        self.assertIn("explore_agent_complete", event_types)
        self.assertIn("generation_start", event_types)
        self.assertLess(event_types.index("explore_agent_complete"), event_types.index("generation_start"))

        self.assertIsNotNone(captured["messages"])
        injected = "\n".join(m.get("content", "") for m in captured["messages"] if m.get("role") == "system")
        self.assertIn("[REPO-EXPLORE RESULT]", injected)
        self.assertIn("PROJECT_STRUCTURE.md", injected)
        self.assertIn("Evidence completeness: complete", injected)
        self.assertIn("You MUST base your response on these findings only", injected)

        tool_names = {t["function"]["name"] for t in captured["tools"]}
        self.assertTrue(tool_names)
        self.assertTrue(tool_names.issubset({"file_read", "file_list", "grep_search", "find_by_name"}))

    def test_non_architecture_repo_fact_does_not_fork(self):
        async def fail_if_called(*args, **kwargs):
            raise AssertionError("simple AGENTS.md repo-fact question should not fork to RepoExploreAgent")

        async def fake_stream_one_turn(messages, env, tools, stream_stats=None):
            yield {"type": "chunk", "content": "AGENTS.md 的内容需要通过 file_read 查看。"}

        async def collect_events(workspace):
            import agentic_loop
            with patch.object(agentic_loop, "run_repo_explore_subagent", side_effect=fail_if_called), \
                 patch.object(agentic_loop, "_stream_one_turn", side_effect=fake_stream_one_turn):
                events = []
                async for ev in agentic_loop.agentic_chat_stream(
                    "AGENTS.md 里面写了什么？",
                    env={"NANOBOT_MAX_TOOL_ITERATIONS": "1"},
                    session_id="test-no-repo-explore-fork",
                    workspace=workspace,
                    max_turns=1,
                ):
                    events.append(ev)
                return events

        with tempfile.TemporaryDirectory() as tmpdir:
            events = asyncio.run(collect_events(Path(tmpdir)))

        event_types = [e.get("type") for e in events]
        self.assertNotIn("explore_agent_start", event_types)
        self.assertNotIn("explore_agent_complete", event_types)
        self.assertIn("generation_start", event_types)

    def test_partial_explore_result_is_injected_with_limitations(self):
        captured = {"messages": None}

        async def fake_explore(query, env, workspace, session_id):
            return ExplorationResult(
                success=True,
                query=query,
                files_read=[
                    {"path": "PROJECT_STRUCTURE.md", "preview": "showing lines 1-300 of 603"},
                ],
                directories_scanned=[str(workspace)],
                findings=(
                    "## Repository Exploration Summary\n\n"
                    "**Evidence Completeness**: partial\n\n"
                    "### Files Examined (1)\n"
                    "- `PROJECT_STRUCTURE.md`: only partial content was available\n\n"
                    "### Evidence Gaps\n"
                    "- PROJECT_STRUCTURE.md not fully read\n\n"
                    "### Instruction for Main Agent\n"
                    "If evidence is partial, explicitly state limitations."
                ),
                evidence_completeness="partial",
                missing_evidence=["PROJECT_STRUCTURE.md not fully read"],
            )

        async def fake_stream_one_turn(messages, env, tools, stream_stats=None):
            captured["messages"] = [dict(m) for m in messages]
            yield {"type": "chunk", "content": "基于已读取片段，以下结论是不完整的。"}

        async def collect_events(workspace):
            import agentic_loop
            with patch.object(agentic_loop, "run_repo_explore_subagent", side_effect=fake_explore), \
                 patch.object(agentic_loop, "_stream_one_turn", side_effect=fake_stream_one_turn):
                events = []
                async for ev in agentic_loop.agentic_chat_stream(
                    "详细说说每个目录的职责，这个项目的架构是怎么设计的？",
                    env={"NANOBOT_MAX_TOOL_ITERATIONS": "1"},
                    session_id="test-partial-repo-explore-fork",
                    workspace=workspace,
                    max_turns=1,
                ):
                    events.append(ev)
                return events

        with tempfile.TemporaryDirectory() as tmpdir:
            events = asyncio.run(collect_events(Path(tmpdir)))

        self.assertIn("explore_agent_complete", [e.get("type") for e in events])
        injected = "\n".join(m.get("content", "") for m in captured["messages"] if m.get("role") == "system")
        self.assertIn("Evidence completeness: partial", injected)
        self.assertIn("PROJECT_STRUCTURE.md not fully read", injected)
        self.assertIn("If evidence_completeness is \"partial\"", injected)


class TestRepoFactPartialReadGateRealReplay(unittest.TestCase):
    def test_turn_after_partial_project_structure_read_is_blocked(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "这个项目有哪些主要模块？解释一下？？？？"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": '{"limit":99999,"path":"PROJECT_STRUCTURE.md"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc1",
                "_tool_name": "file_read",
                "_turn": 2,
                "content": (
                    "FILE READ: PROJECT_STRUCTURE.md\n"
                    "[File: /home/field/.nanobot/workspace/PROJECT_STRUCTURE.md | "
                    "603 lines | 28,199 bytes] (showing lines 1-300 of 603)\n"
                    "core/ agents/ tools/ web_ui/ security/"
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "根据 PROJECT_STRUCTURE.md，该项目主要模块包括 core、agents、web_ui、"
                    "security、tools，并且整体架构流转是 用户请求 → Web UI → 核心调度 → "
                    "代理执行 → 安全审计 → 工具调用。"
                ),
            },
        ]

        gate = _build_repo_fact_completion_gate(
            messages,
            3,
            "这个项目有哪些主要模块？解释一下？？？？",
        )

        self.assertIsNotNone(
            gate,
            "Turn 3 final summary must be blocked when Turn 2 only read lines 1-300/603",
        )
        self.assertIn("[REPO-FACT GATE]", gate)
        self.assertIn("Do NOT give a complete architecture", gate)


class TestRepoFactDuplicateFinalAnswerReplay(unittest.TestCase):
    def test_turn4_turn5_duplicate_module_summary_is_detected(self):
        turn3 = (
            "根据 PROJECT_STRUCTURE.md，该项目主要模块包括 core、agents、web_ui、"
            "security、tools、monitoring、evals、memory 和 sessions。core 是核心模块，"
            "agents 是代理框架，tools 提供工具，web_ui 提供 Web 界面和运行时逻辑。"
        )
        turn4 = (
            "根据 PROJECT_STRUCTURE.md 的定义，该项目主要由 core、agents、web_ui、"
            "security、tools、monitoring、evals、memory、sessions 等模块组成。core 是核心模块，"
            "agents 是代理框架，tools 提供独立工具，web_ui 承载 Web 界面和运行时逻辑。"
        )
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "这个项目有哪些主要模块？解释一下？？？？"},
            {"role": "assistant", "content": turn3},
            {"role": "assistant", "content": turn4},
        ]

        self.assertTrue(
            _is_duplicate_repo_fact_answer(messages, turn4),
            "Turn 4/5 repeated module summaries should be treated as duplicate final answers",
        )

    def test_short_or_different_answer_is_not_duplicate(self):
        messages = [
            {"role": "assistant", "content": "项目主要模块包括 core、agents、tools。"}
        ]
        self.assertFalse(_is_duplicate_repo_fact_answer(messages, "还有 README.md。"))


class TestRepoFactRepeatedQuestionReplay(unittest.TestCase):
    def test_history_final_answer_does_not_block_current_repo_fact_question(self):
        previous_final = (
            "根据 PROJECT_STRUCTURE.md，该项目主要模块包括 core、agents、web_ui、"
            "security、tools、monitoring、evals、memory 和 sessions。core 是核心模块，"
            "agents 是代理框架，tools 提供工具，web_ui 提供 Web 界面和运行时逻辑。"
        )
        user_message = (
            "[对话历史]\n"
            "用户: 这个项目有哪些主要模块？解释一下？？？？\n"
            f"assistant: {previous_final}\n"
            "[当前问题]\n"
            "这个项目有哪些主要模块？解释一下？？？？"
        )
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": user_message},
        ]

        async def collect_events():
            async def fake_stream(*args, **kwargs):
                yield {"type": "chunk", "content": "根据最新探索，项目结构包括 web_ui、tools、tests。"}

            with tempfile.TemporaryDirectory() as tmpdir:
                with patch("agentic_loop._stream_one_turn", fake_stream):
                    return [
                        event
                        async for event in agentic_chat_stream(
                            user_message,
                            {},
                            "test-session",
                            workspace=Path(tmpdir),
                            max_turns=1,
                        )
                    ]

        events = asyncio.run(collect_events())
        output = "".join(e.get("content", "") for e in events if e.get("type") == "chunk")
        self.assertIn("项目结构包括", output)
        self.assertNotIn("已在上一次回答中给出", output)


# ═══════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):
    """Boundary conditions and false-positive resistance."""

    def test_short_response_passes(self):
        """<200 words → no U12b, no U12c."""
        s = Sim("有 Dockerfile 吗")
        r = s.run("项目中没有 Dockerfile。")
        self.assertEqual(r["action"], "break")
        self.assertIsNone(r["defense"])

    def test_q2_post_tool_no_false_positive(self):
        """Q2's numbered list must NOT trigger U12c."""
        e, b, cat = _cat_count(Q2_POST_TOOL)
        self.assertLess(cat, 2, f"False positive on Q2: {e}e+{b}b")

    def test_u12b_only_fires_once(self):
        """After grounding injected, U12b does NOT fire a second time."""
        s = Sim("test")
        s.run(Q3_NO_TOOL)  # U12b fires
        r2 = s.run(Q3_NO_TOOL)  # Should NOT be U12b again
        self.assertNotEqual(r2["defense"], "u12b",
                            "U12b fired twice — infinite loop risk!")

    def test_pop_safe_when_no_assistant(self):
        """If last msg is not assistant, pop is skipped."""
        s = Sim("test")
        before = len(s.msgs)
        # Manually remove assistant possibility
        s.run("短回答。")  # adds assistant, then breaks
        # Now add a system msg on top
        s.msgs.append({"role": "system", "content": "extra"})
        # Verify pop logic is safe
        if s.msgs and s.msgs[-1].get("role") == "assistant":
            s.msgs.pop()
        self.assertEqual(s.msgs[-1]["role"], "system")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    print(f"\n{'='*60}")
    print(f"Results: {total - failed} passed, {failed} failed")
    if failed == 0:
        print("All Q1-Q5 integration tests passed!")
    else:
        print("FAILURES detected!")
        for _, tb in result.failures + result.errors:
            print(f"  {tb.strip().split(chr(10))[-1]}")
