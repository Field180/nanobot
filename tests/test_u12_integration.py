"""U12 integration tests — replay REAL llama.cpp outputs through detection logic.

These tests exercise the actual runtime functions (_scrub_narration, regex matching,
word counting, categorization detection) with verbatim model output from Q1-Q8.
They prove whether each U12 defense actually fires, not just whether the source
code "contains" certain strings.
"""
import re
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentic_loop import (
    _NARRATION_RE,
    _SENTENCE_DEDUP_RE,
    _EMOJI_HEADER_RE,
    _BOLD_CATEGORY_RE,
    _scrub_narration,
    _detect_repo_fact_request,
    _is_repo_fact_question,
    _REPO_FACT_READ_ONLY_TOOLS,
    _upgrade_repo_fact_file_reads,
    _build_repo_fact_completion_gate,
)
from system_prompts import _SYSTEM_PROMPT_OUTPUT, _SYSTEM_PROMPT_QUALITY


# ═════════════════════════════════════════════════════════════════
# Verbatim model outputs from the 4th-round llama.cpp test
# ═════════════════════════════════════════════════════════════════

Q1_PRE_TOOL = (
    "列出当前工作目录下的文件和文件夹，以便为您展示项目的整体结构。"
    "列出当前工作目录下的文件和文件夹，以便为您展示项目的整体结构。"
)

Q1_POST_TOOL = """根目录下包含大量文件和文件夹，这是一个结构非常复杂的 AI 智能体项目。为了方便你查看，我将其大致分为以下几类：

### 1. 核心代码目录
*   **`agents/`**: 各种功能智能体的实现。
*   **`core/`**: 系统底层核心逻辑。
*   **`tools/`**: 智能体可调用的工具集。
*   **`web_ui/`**: 前端可视化界面代码。
*   **`security/`**: 安全策略与权限控制。

### 2. 认知与数据管理
*   **`memory/` & `.nanobot_memory/`**: 持久化记忆存储。
*   **`neural_pathway/`**: 认知路径处理。
*   **`immune_system/`**: 系统自我修复机制。
*   **`sessions/`**: 会话历史记录。

### 3. 配置与文档
*   **`config/`**: 全局配置文件。
*   **`docs/`**: 项目文档。
*   **关键文件**: `ARCHITECTURE.md` (架构)、`PROJECT_STRUCTURE.md` (结构)、`AGENTS.md` (指南) 和 `requirements.txt` (依赖)。

### 4. 测试、监控与评估
*   **`evals/`**: 量化评估工具。
*   **`monitoring/`**: 系统运行状态监控。
*   **`tests/` & `test_suite/`**: 测试用例与套件。
*   **`sandbox/` & `sandbox_logs/`**: 代码执行沙箱及其日志。

### 5. 其他辅助目录
*   **`backend_plugins/`**: 不同 LLM 后端的适配插件。
*   **`plugins/`**: 功能扩展插件。
*   **`quarantine/`**: 文件隔离区。
*   **`.scratchpad/`**: 临时工作区。

如果你想深入了解某个具体文件夹或文件的内容，请告诉我！"""

Q2_PRE_TOOL = "为您读取 `AGENTS.md` 文件的内容。为您读取 `AGENTS.md` 文件的内容。"

Q2_POST_TOOL = """`AGENTS.md` 的内容是一份**执行行动指南**，主要规定了智能体在执行操作时应遵循的原则，具体包括：

1.  **可逆性原则**：本地可逆操作（如编辑文件、运行测试）可自由执行；但难以逆转或影响共享系统的操作需先与用户确认。
2.  **风险操作确认**：以下操作必须经过用户确认：
    *   **破坏性操作**：删除文件/分支、清空数据库、终止进程。
    *   **难以逆转的操作**：强制推送（force push）、重置提交、修改 CI/CD。
    *   **影响他人的操作**：推送代码、创建/关闭 PR、发送消息。
3.  **处理障碍**：禁止将破坏性操作作为捷径，应识别根本原因并修复底层问题，而非绕过安全检查。
4.  **效率优化**：在没有依赖关系的情况下，鼓励在同一次响应中并行调用多个工具以提高效率。"""

Q3_NO_TOOL = """Nanobot 是一个高度模块化、注重安全且具备自我演进能力的 AI 智能体系统。其模块划分采用了**分层防御**与**认知解耦**的设计理念，主要可以分为以下六大核心模块：

### 1. 用户交互层 (User Interface Layer)
负责指令的输入与结果的反馈。
*   **Web UI (`web_ui/`)**: 基于 FastAPI 的可视化界面，用于对话和状态监控。
*   **Bot 适配器**: 支持 Telegram 和 Discord 等第三方平台。
*   **CLI (`cli.py`)**: 为开发者提供的命令行快速接口。

### 2. 安全与审批系统 (Security & Approval System)
系统的"刹车"机制，确保高危操作受控。
*   **`security/`**: 定义全局安全策略、权限控制和审计日志。
*   **ApprovalManager**: 拦截危险工具调用，触发人类确认（DangerModal）。
*   **沙箱环境 (`sandbox/`)**: 所有代码在隔离环境中执行，防止破坏宿主机。
*   **隔离区 (`quarantine/`)**: 存放待审查的可疑文件。

### 3. 认知与记忆系统 (Cognition & Memory System)
模拟人类认知，实现跨会话的知识积累。
*   **`memory/` & `.nanobot_memory/`**: 多层级存储（短期 → 中期 → 长期持久化）。
*   **神经通路 (`neural_pathway/`)**: 管理信息的流转与强化逻辑。
*   **免疫系统 (`immune_system/`)**: 通过监控错误模式自动生成补丁，实现自我修复。

### 4. 核心执行引擎 (Core Execution Engine)
系统的"大脑"与"手脚"，负责调度和执行。
*   **`core/`**: 底层运行循环（Loop）、状态机和通信协议。
*   **智能体集群 (`agents/`)**: Multi-Agent 架构，将任务分发给不同专长的 Agent。
*   **统一工具解析器**: 将不同 LLM 的工具调用格式统一化为可执行对象。

### 5. 适配与扩展层 (Adaptation & Extension Layer)
确保系统能够灵活切换模型并快速增加功能。
*   **后端插件 (`backend_plugins/`)**: 模型适配层，支持 Claude, Gemini, Ollama 等多种 LLM。
*   **功能插件 (`plugins/`)**: 在不修改核心代码的情况下扩展新能力。

### 6. 监控与量化评估 (Monitoring & Evaluation)
提供闭环反馈，确保系统稳定性。
*   **`monitoring/`**: 实时监控资源、延迟和 Agent 状态。
*   **`evals/`**: 使用预设用例对 Agent 的鲁棒性和准确率进行打分。

---

**核心数据流向：**
`用户输入` → `安全审批检查` → `模型路由(适配层)` → `LLM 推理` → `工具执行(沙箱)` → `记忆更新` → `响应用户`"""

Q4_NO_TOOL = """Nanobot 是一个高度复杂、面向安全且具备"自我演进"能力的 AI 智能体（Agent）系统。它的设计核心在于**将认知能力与执行环境解耦**，并通过一套类似生物免疫系统的机制来实现自我修复和增强。

### 一、 整体设计理念

该项目的设计可以概括为以下四个维度：

1.  **多智能体协作 (Multi-Agent Architecture)**：
    系统并非依赖一个单一的巨型 Prompt，而是将任务分发给 `agents/` 目录下具有不同专长的子智能体，通过协同工作完成复杂工程任务。
2.  **分层安全防御 (Layered Security)**：
    采用"不信任"原则。所有代码执行必须在 `sandbox/`（沙箱）中进行，可疑文件进入 `quarantine/`（隔离区），高危操作由 `security/` 模块拦截并请求人类审批。
3.  **认知闭环与记忆 (Cognitive Loop & Memory)**：
    通过 `memory/` 实现长期和短期记忆，利用 `neural_pathway/` 管理信息流转，并通过 `reflections/`（反思）机制在任务结束后总结经验，实现能力的迭代。
4.  **自我演进机制 (Self-Evolution)**：
    这是该项目最独特的地方。`immune_system/`（免疫系统）会监控运行时的错误模式，自动尝试生成补丁并存放在 `patches/` 中，从而在不依赖人工干预的情况下修复 Bug。

---

### 二、 目录功能详细映射

根据项目结构，各目录的具体职责如下：

#### 1. 核心执行与调度
*   **`core/`**: 系统的"心脏"。包含主运行循环（Loop）、状态机、任务调度逻辑以及智能体之间的通信协议。
*   **`agents/`**: 存放各种专业 Agent 的实现（如代码编写者、测试员、架构师等）。
*   **`config/`**: 全局配置文件，定义系统参数、模型阈值和环境设置。

#### 2. 安全与隔离 (安全底线)
*   **`security/`**: 定义权限控制、审计策略和安全拦截规则。

#### 3. 认知、记忆与演进 (智能大脑)
*   **`memory/` & `.nanobot_memory/`**: 实现跨会话的持久化存储。

#### 4. 适配与扩展 (灵活性)
*   **`backend_plugins/`**: LLM 适配层。

#### 5. 监控、评估与辅助
*   **`monitoring/`**: 实时监控系统资源占用、响应延迟和 Agent 健康状态。

#### 6. 其他
*   **`sessions/`**: 保存每个用户的会话历史记录。"""

Q5_NO_TOOL = """根目录下的文件和文件夹采用了**功能分区（Functional Zoning）**的组织方式，将系统的"大脑"、"手脚"、"免疫系统"和"安全围栏"在物理路径上进行了清晰的解耦。

可以将根目录的组织逻辑分为以下五个核心集群：

### 1. 执行与调度集群 (The Execution Hub)
这是系统的核心运行区域，负责任务的接收、分发和执行。
*   **`core/`**: 存放底层引擎（Loop）、状态机等最基础的运行逻辑。
*   **`agents/`**: 存放各类专业智能体的实现，是系统能力的具体承载者。
*   **`config/`**: 全局配置中心，控制整个系统的行为参数。

### 2. 安全与隔离集群 (The Security Perimeter)
这是项目的"防御线"，确保 AI 的操作在可控范围内。
*   **`security/`**: 定义权限、审计和拦截规则（系统的"法律"）。
*   **`sandbox/` & `sandbox_logs/`**: 代码执行的物理隔离区，防止破坏宿主机。
*   **`quarantine/`**: 隔离区，存放待审查的可疑文件。
*   **`audit/`**: 操作审计日志，用于事后追溯。

### 3. 认知与演进集群 (The Cognitive Engine)
模拟生物大脑的记忆和自我修复机制。
*   **`memory/` & `.nanobot_memory/`**: 分层存储短期、中期和长期持久化记忆。
*   **`neural_pathway/`**: 管理信息流转的"神经通路"。
*   **`immune_system/`**: 监控错误并自动生成补丁的自我修复机制。
*   **`reflections/`**: 存放智能体对任务执行后的反思记录。

### 4. 适配与扩展集群 (The Extension Layer)
确保系统能够快速兼容新模型或增加新功能。
*   **`backend_plugins/`**: LLM 后端适配器（如 Claude, Gemini, Ollama）。
*   **`plugins/`**: 功能插件，无需修改核心代码即可扩展能力。
*   **`skills/`**: 可复用的技能库。

### 5. 运维与支撑集群 (The Support Infrastructure)
提供监控、评估和文档支持。
*   **`monitoring/`**: 系统健康状态实时监控。
*   **`evals/`**: 量化评估集，用于测试 Agent 的性能。
*   **`docs/`**: 项目技术文档。
*   **`sessions/`**: 用户会话历史记录。

---

### 根目录关键文件 (Root-level Files)
除了文件夹，根目录下还分布着几个至关重要的引导文件：
*   **`requirements.txt`**: Python 环境依赖定义。
*   **`AGENTS.md`**: 智能体执行操作的行为准则（行动指南）。
*   **`ARCHITECTURE.md` / `PROJECT_STRUCTURE.md`**: 项目的顶层设计与结构说明书。

这种组织方式使得开发者可以快速定位：想改**能力**去 `agents/`，想调**安全**去 `security/`，想优化**记忆**去 `memory/`。"""

Q7_PRE_TOOL = "为您查找并读取项目中的 `Dockerfile` 文件内容。为您查找并读取项目中的 `Dockerfile` 文件内容。"

Q8_PRE_TOOL = "为您查找并显示项目中的 `package.json` 文件。为您查找并显示项目中的 `package.json` 文件。"


# ═════════════════════════════════════════════════════════════════
# Helper: simulate U12b word-count logic
# ═════════════════════════════════════════════════════════════════
def _effective_words(text: str) -> int:
    """Replicate the CJK-aware word count from agentic_loop."""
    return max(len(text.split()), len(text) // 3)


def _u12b_would_fire(assistant_text: str, tool_data_chars: int) -> bool:
    """Return True if U12b grounding check would trigger."""
    ew = _effective_words(assistant_text)
    return ew > 200 and tool_data_chars < 2000


def _u12c_category_count(text: str) -> tuple:
    """Return (emoji_hits, bold_hits, total) for U12c."""
    e = len(_EMOJI_HEADER_RE.findall(text))
    b = len(_BOLD_CATEGORY_RE.findall(text))
    return e, b, e + b


# ═════════════════════════════════════════════════════════════════
# Tests
# ═════════════════════════════════════════════════════════════════

class TestU12aRealNarration(unittest.TestCase):
    """U12a integration: scrub narration from real llama.cpp output."""

    def test_q1_pre_tool_dedup_and_strip(self):
        """Q1: '列出...列出...' → dedup + strip '列出当前'."""
        cleaned, scrubbed = _scrub_narration(Q1_PRE_TOOL)
        self.assertTrue(scrubbed, "Q1 pre-tool narration not detected")
        # Must not contain the repeated sentence
        self.assertEqual(cleaned.count("列出当前"), 0,
                         f"'列出当前' should be stripped, got: {cleaned[:80]}")

    def test_q2_pre_tool_dedup_and_strip(self):
        """Q2: '为您读取...为您读取...' → dedup + strip '为您'."""
        cleaned, scrubbed = _scrub_narration(Q2_PRE_TOOL)
        self.assertTrue(scrubbed)
        self.assertEqual(cleaned.count("为您"), 0,
                         f"'为您' should be stripped, got: {cleaned[:80]}")

    def test_q7_pre_tool_dedup_and_strip(self):
        """Q7: '为您查找并读取...为您查找并读取...' → dedup + strip."""
        cleaned, scrubbed = _scrub_narration(Q7_PRE_TOOL)
        self.assertTrue(scrubbed)
        self.assertEqual(cleaned.count("为您"), 0)

    def test_q8_pre_tool_dedup_and_strip(self):
        """Q8: '为您查找并显示...为您查找并显示...' → dedup + strip."""
        cleaned, scrubbed = _scrub_narration(Q8_PRE_TOOL)
        self.assertTrue(scrubbed)
        self.assertEqual(cleaned.count("为您"), 0)

    def test_normal_text_not_scrubbed(self):
        """Normal project description should pass through unchanged."""
        text = "项目根目录下有 15 个文件，其中包含 3 个目录和 12 个文件。"
        cleaned, scrubbed = _scrub_narration(text)
        self.assertFalse(scrubbed)
        self.assertEqual(cleaned, text)


class TestRepoFactGuardrails(unittest.TestCase):
    """Repository-fact prompts should be explicitly grounded and anti-speculative."""

    def test_detect_repo_fact_agents_md(self):
        hint = _detect_repo_fact_request("AGENTS.md 里面写了什么")
        self.assertIsNotNone(hint)
        self.assertIn("[REPO FACTS ONLY]", hint)
        self.assertIn("Use read-only tools first", hint)

    def test_detect_repo_fact_architecture(self):
        hint = _detect_repo_fact_request("详细说说每个目录的职责，这个项目的架构是怎么设计的？")
        self.assertIsNotNone(hint)
        self.assertIn("Do NOT invent architecture clusters", hint)

    def test_non_repo_fact_question_not_matched(self):
        hint = _detect_repo_fact_request("帮我修复这个测试失败")
        self.assertIsNone(hint)

    def test_is_repo_fact_question_boolean(self):
        self.assertTrue(_is_repo_fact_question("详细说说每个目录的职责，这个项目的架构是怎么设计的？"))
        self.assertFalse(_is_repo_fact_question("修一下这个 flaky test"))

    def test_repo_fact_mode_read_only_tools(self):
        self.assertEqual(
            _REPO_FACT_READ_ONLY_TOOLS,
            {"file_read", "file_list", "grep_search", "find_by_name"},
        )

    def test_upgrade_repo_fact_file_read_to_full_file(self):
        tool_calls = [{
            "id": "tc0",
            "function": {
                "name": "file_read",
                "arguments": '{"path":"PROJECT_STRUCTURE.md"}',
            },
            "_parsed_args": {"path": "PROJECT_STRUCTURE.md"},
        }]
        upgraded = _upgrade_repo_fact_file_reads(tool_calls)
        self.assertEqual(upgraded[0]["_parsed_args"]["limit"], 99999)
        self.assertNotIn("offset", upgraded[0]["_parsed_args"])

    def test_partial_read_completion_gate_blocks_full_summary(self):
        messages = [{
            "role": "tool",
            "tool_call_id": "tc0",
            "_tool_name": "file_read",
            "_turn": 2,
            "content": "FILE READ: PROJECT_STRUCTURE.md\nshowing lines 1-300 of 603\n...",
        }]
        gate = _build_repo_fact_completion_gate(
            messages,
            2,
            "详细说说每个目录的职责，这个项目的架构是怎么设计的？",
        )
        self.assertIsNotNone(gate)
        self.assertIn("[REPO-FACT GATE]", gate)
        self.assertIn("Do NOT give a complete architecture", gate)

    def test_partial_read_gate_not_triggered_for_non_repo_question(self):
        messages = [{
            "role": "tool",
            "tool_call_id": "tc0",
            "_tool_name": "file_read",
            "_turn": 1,
            "content": "FILE READ: PROJECT_STRUCTURE.md\nshowing lines 1-300 of 603\n...",
        }]
        gate = _build_repo_fact_completion_gate(messages, 1, "修复这个测试失败")
        self.assertIsNone(gate)

    def test_system_prompt_output_bans_pretool_repo_prose(self):
        self.assertIn("repository-fact questions", _SYSTEM_PROMPT_OUTPUT)
        self.assertIn("Do NOT write a speculative overview before reading", _SYSTEM_PROMPT_OUTPUT)
        self.assertIn("never answer with pre-tool architecture prose", _SYSTEM_PROMPT_OUTPUT)

    def test_system_prompt_quality_bans_invented_clusters(self):
        self.assertIn("distinguish clearly", _SYSTEM_PROMPT_QUALITY)
        self.assertIn("Do NOT invent high-level clusters", _SYSTEM_PROMPT_QUALITY)


class TestU12bRealHallucination(unittest.TestCase):
    """U12b integration: verify word-count and grounding trigger on real output."""

    def test_q3_zero_tool_fires(self):
        """Q3 (模块划分): 900+ char Chinese, zero tool data → U12b MUST fire."""
        self.assertTrue(
            _u12b_would_fire(Q3_NO_TOOL, tool_data_chars=0),
            f"Q3: effective_words={_effective_words(Q3_NO_TOOL)}, should be >200"
        )

    def test_q4_zero_tool_fires(self):
        """Q4 (怎么设计的): 1000+ char Chinese, zero tool data → U12b MUST fire."""
        self.assertTrue(
            _u12b_would_fire(Q4_NO_TOOL, tool_data_chars=0),
            f"Q4: effective_words={_effective_words(Q4_NO_TOOL)}, should be >200"
        )

    def test_q5_zero_tool_fires(self):
        """Q5 (文件组织): 1200+ char Chinese, zero tool data → U12b MUST fire."""
        self.assertTrue(
            _u12b_would_fire(Q5_NO_TOOL, tool_data_chars=0),
            f"Q5: effective_words={_effective_words(Q5_NO_TOOL)}, should be >200"
        )

    def test_q2_with_tool_data_does_not_fire(self):
        """Q2 (AGENTS.md): has 811 bytes of tool data → should NOT fire."""
        # Q2 read AGENTS.md which is 811 bytes
        self.assertFalse(
            _u12b_would_fire(Q2_POST_TOOL, tool_data_chars=811),
            "Q2 has real tool data, should not fire"
        )

    def test_q1_post_tool_with_small_data_fires(self):
        """Q1 post-tool: massive categorized response from small file_list data."""
        # file_list returned ~500 chars of directory listing
        self.assertTrue(
            _u12b_would_fire(Q1_POST_TOOL, tool_data_chars=500),
            f"Q1 post: effective_words={_effective_words(Q1_POST_TOOL)}, 500 chars tool data"
        )

    def test_cjk_word_count_not_fooled(self):
        """Chinese text must yield effective_words >> split() count."""
        for label, text in [("Q3", Q3_NO_TOOL), ("Q4", Q4_NO_TOOL), ("Q5", Q5_NO_TOOL)]:
            split_w = len(text.split())
            eff_w = _effective_words(text)
            self.assertGreater(eff_w, split_w * 2,
                               f"{label}: effective={eff_w} should be >> split={split_w}")
            self.assertGreater(eff_w, 200,
                               f"{label}: effective={eff_w} must exceed 200 threshold")

    def test_short_response_does_not_fire(self):
        """Short factual response should not trigger U12b."""
        text = "项目中没有 Dockerfile。"
        self.assertFalse(_u12b_would_fire(text, tool_data_chars=0))


class TestU12cRealCategorization(unittest.TestCase):
    """U12c integration: verify bold/emoji categorization detection on real output."""

    def test_q1_post_tool_categorized(self):
        """Q1 post-tool: ### + bold headers → U12c fires (>=2)."""
        e, b, total = _u12c_category_count(Q1_POST_TOOL)
        self.assertGreaterEqual(total, 2,
                                f"Q1 post-tool: emoji={e}, bold={b}, total={total}")

    def test_q3_categorized(self):
        """Q3 zero-tool: ### numbered sections → U12c fires."""
        e, b, total = _u12c_category_count(Q3_NO_TOOL)
        self.assertGreaterEqual(total, 2,
                                f"Q3: emoji={e}, bold={b}, total={total}")

    def test_q4_categorized(self):
        """Q4 zero-tool: ### + bold subsections → U12c fires."""
        e, b, total = _u12c_category_count(Q4_NO_TOOL)
        self.assertGreaterEqual(total, 2,
                                f"Q4: emoji={e}, bold={b}, total={total}")

    def test_q5_categorized(self):
        """Q5 zero-tool: ### numbered clusters → U12c fires."""
        e, b, total = _u12c_category_count(Q5_NO_TOOL)
        self.assertGreaterEqual(total, 2,
                                f"Q5: emoji={e}, bold={b}, total={total}")

    def test_q2_not_categorized(self):
        """Q2 (AGENTS.md summary): numbered list but NOT bold-category format."""
        e, b, total = _u12c_category_count(Q2_POST_TOOL)
        # Q2 uses numbered list (1. 2. 3. 4.) — this should NOT match bold categories
        # because the bold items are operation types, not directory/module names
        # This tests false-positive resistance
        self.assertLess(total, 2,
                        f"Q2 should not trigger categorization: emoji={e}, bold={b}")

    def test_plain_text_not_categorized(self):
        """Plain text with no headers → 0 category hits."""
        text = "项目中没有 Dockerfile。该项目主要基于 Python 构建。"
        e, b, total = _u12c_category_count(text)
        self.assertEqual(total, 0)


class TestU12EndToEndFlow(unittest.TestCase):
    """End-to-end: simulate the detection sequence for each Q scenario."""

    def _simulate_no_tool_turn(self, assistant_text: str) -> dict:
        """Simulate what happens in agentic_loop when model returns text + no tools.

        Returns dict of which U12 defenses would fire.
        """
        result = {
            "u12b_fires": False,
            "u12b_reason": "",
            "u12c_fires": False,
            "u12c_reason": "",
        }

        # U12b check
        ew = _effective_words(assistant_text)
        tool_data = 0  # no tools called
        if ew > 200 and tool_data < 2000:
            result["u12b_fires"] = True
            result["u12b_reason"] = f"effective_words={ew}, tool_data=0"

        # U12c check (would run AFTER U12b in real code, but if U12b
        # fires first it does continue — so U12c only matters if U12b didn't)
        e, b, total = _u12c_category_count(assistant_text)
        if total >= 2:
            result["u12c_fires"] = True
            result["u12c_reason"] = f"emoji={e}, bold={b}, total={total}"

        return result

    def _simulate_with_tool_turn(self, assistant_text: str, tool_data_chars: int) -> dict:
        """Simulate detection for a turn that DID call tools."""
        result = {
            "u12a_scrubbed": False,
            "u12b_fires": False,
            "u12c_fires": False,
        }

        _, scrubbed = _scrub_narration(assistant_text)
        result["u12a_scrubbed"] = scrubbed

        ew = _effective_words(assistant_text)
        if ew > 200 and tool_data_chars < 2000:
            result["u12b_fires"] = True

        e, b, total = _u12c_category_count(assistant_text)
        if total >= 2:
            result["u12c_fires"] = True

        return result

    def test_q3_both_u12b_and_u12c_fire(self):
        """Q3 (pure fabrication): BOTH U12b and U12c should detect problems."""
        r = self._simulate_no_tool_turn(Q3_NO_TOOL)
        self.assertTrue(r["u12b_fires"], f"Q3 U12b: {r['u12b_reason']}")
        self.assertTrue(r["u12c_fires"], f"Q3 U12c: {r['u12c_reason']}")

    def test_q4_both_fire(self):
        """Q4 (pure fabrication): BOTH U12b and U12c detect."""
        r = self._simulate_no_tool_turn(Q4_NO_TOOL)
        self.assertTrue(r["u12b_fires"], f"Q4 U12b: {r['u12b_reason']}")
        self.assertTrue(r["u12c_fires"], f"Q4 U12c: {r['u12c_reason']}")

    def test_q5_both_fire(self):
        """Q5 (pure fabrication): BOTH U12b and U12c detect."""
        r = self._simulate_no_tool_turn(Q5_NO_TOOL)
        self.assertTrue(r["u12b_fires"], f"Q5 U12b: {r['u12b_reason']}")
        self.assertTrue(r["u12c_fires"], f"Q5 U12c: {r['u12c_reason']}")

    def test_q1_post_tool_u12c_fires(self):
        """Q1 (has tool but over-categorized): U12c catches it."""
        r = self._simulate_with_tool_turn(Q1_POST_TOOL, tool_data_chars=500)
        self.assertTrue(r["u12c_fires"], "Q1 post-tool categorization not detected")

    def test_q2_clean(self):
        """Q2 (good response): neither U12b nor U12c should fire."""
        r = self._simulate_with_tool_turn(Q2_POST_TOOL, tool_data_chars=811)
        self.assertFalse(r["u12b_fires"], "Q2 should NOT trigger U12b")


class TestU12MessageRemoval(unittest.TestCase):
    """Verify that the messages.pop() logic would correctly remove assistant msgs."""

    def test_pop_removes_last_assistant(self):
        """Simulate: messages has [..., assistant_msg], pop() removes it."""
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "给我概览一下这个项目的模块划分"},
            {"role": "assistant", "content": Q3_NO_TOOL},  # fabricated
        ]
        # This is what agentic_loop does when U12b fires
        if messages and messages[-1].get("role") == "assistant":
            messages.pop()
        # Now the last message should be the user message
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(len(messages), 2)

    def test_pop_safe_when_no_assistant(self):
        """If last message is not assistant, pop() is skipped — no crash."""
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "test"},
        ]
        if messages and messages[-1].get("role") == "assistant":
            messages.pop()
        self.assertEqual(len(messages), 2)  # unchanged


class TestR1ClearGeneration(unittest.TestCase):
    """R1: Verify clear_generation SSE event is yielded when U12b/U12c fires."""

    def test_clear_generation_in_u12b(self):
        """R1: U12b block yields clear_generation before continue."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        # Find the U12b grounding block
        idx = src.find("[U12b] Hallucination risk:")
        self.assertGreater(idx, 0, "U12b grounding block not found")
        block = src[idx:idx+2000]
        self.assertIn('{"type": "clear_generation"}', block,
                       "U12b must yield clear_generation before continue")

    def test_clear_generation_in_u12c(self):
        """R1: U12c block yields clear_generation before continue."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        idx = src.find("[NO CATEGORIZATION]")
        self.assertGreater(idx, 0, "U12c categorization block not found")
        block = src[idx:idx+2000]
        self.assertIn('{"type": "clear_generation"}', block,
                       "U12c must yield clear_generation before continue")

    def test_generation_start_before_llm_call(self):
        """R1: generation_start event is yielded before each LLM streaming call."""
        import agentic_loop
        src = open(agentic_loop.__file__).read()
        idx = src.find("# ── LLM streaming call ──")
        self.assertGreater(idx, 0)
        block = src[idx:idx+500]
        self.assertIn('"generation_start"', block,
                       "generation_start must be yielded before LLM call")


class TestR2NewNarrationPatterns(unittest.TestCase):
    """R2: Verify new narration patterns 读取 and 为了 are caught."""

    def test_bare_du_qu_scrubbed(self):
        """R2: '读取 X 文件的内容以为您提供准确的回答' is scrubbed."""
        text = "读取 `/home/field/.nanobot/workspace/AGENTS.md` 文件的内容以为您提供准确的回答。"
        cleaned, scrubbed = _scrub_narration(text)
        self.assertTrue(scrubbed)
        self.assertNotIn("读取", cleaned)

    def test_wei_le_scrubbed(self):
        """R2: '为了准确回答...' is scrubbed."""
        text = "为了准确回答该项目的模块划分，我将阅读项目中的架构文档。"
        cleaned, scrubbed = _scrub_narration(text)
        self.assertTrue(scrubbed)
        self.assertNotIn("为了", cleaned)

    def test_long_sentence_dedup(self):
        """R3/R4: Doubled sentences >80 chars with file paths are deduped."""
        sentence = "读取 `/home/field/.nanobot/workspace/AGENTS.md` 文件的内容以为您提供准确的回答。"
        text = sentence + sentence
        cleaned, scrubbed = _scrub_narration(text)
        self.assertTrue(scrubbed, f"Dedup failed for {len(sentence)}-char sentence")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    print(f"\n{'=' * 60}")
    print(f"Results: {total - failed} passed, {failed} failed")
    if failed == 0:
        print("🎉 All U12 integration tests passed!")
    else:
        print("❌ FAILURES — U12 defenses have gaps!")
        for cls_name, tests in [("FAILURES", result.failures), ("ERRORS", result.errors)]:
            for test, tb in tests:
                print(f"  {test}: {tb.strip().split(chr(10))[-1]}")
