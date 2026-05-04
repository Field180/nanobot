"""
RepoExploreAgent — 仿 claw exploreAgent 的只读仓库探索子代理

职责：
- 专门处理 repo-fact 类问题（目录结构、架构、模块职责等）
- 严格只读：只能用 file_list, file_read, grep_search, find_by_name
- 强制完整阅读关键文档（PROJECT_STRUCTURE.md, ARCHITECTURE.md 等）
- 禁止在证据不足时给出完整架构总结
- 返回结构化探索结果，由主代理最终呈现
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, AsyncIterator
from pathlib import Path

import logging

logger = logging.getLogger("repo_explore_agent")


@dataclass
class ExplorationResult:
    """探索结果的统一结构"""
    success: bool
    query: str
    files_read: List[Dict[str, Any]] = field(default_factory=list)
    directories_scanned: List[str] = field(default_factory=list)
    searches_performed: List[Dict[str, Any]] = field(default_factory=list)
    findings: str = ""
    evidence_completeness: str = "partial"  # partial | complete
    missing_evidence: List[str] = field(default_factory=list)


# 关键文档模式 - 遇到这些文件必须完整读取
_KEY_DOC_PATTERNS = [
    r'PROJECT_STRUCTURE\.md',
    r'ARCHITECTURE\.md', 
    r'AGENTS\.md',
    r'README(?:\.md)?',
    r'CONTRIBUTING\.md',
    r'\.cursorrules',
]

# 架构/结构相关问题模式
_ARCHITECTURE_QUERY_RE = re.compile(
    r'(架构|architecture|目录结构|directory\s+structure|'
    r'项目结构|project\s+structure|模块|module|职责|responsibilit)',
    re.IGNORECASE
)

# Partial read 检测
_PARTIAL_READ_RE = re.compile(
    r'showing\s+lines\s+(\d+)-(\d+)\s+of\s+(\d+)',
    re.IGNORECASE
)


class RepoExploreAgent:
    """
    只读仓库探索子代理
    
    与主代理的关键区别：
    1. 工具集硬限制为只读
    2. 关键文档必须完整读取（自动分片）
    3. 探索阶段禁止给出"完整架构总结"
    4. 返回结构化结果，不直接回答用户
    """
    
    READ_ONLY_TOOLS = {"file_read", "file_list", "grep_search", "find_by_name"}
    FULL_FILE_LIMIT = 99999  # B16 分片阈值
    MAX_EXPLORE_TURNS = 8    # 探索回合上限，防止无限循环
    
    def __init__(self, env, workspace: str, session_id: str):
        self.env = env
        self.workspace = Path(workspace)
        self.session_id = session_id
        self._files_read: Dict[str, str] = {}  # path -> content_summary
        self._dirs_scanned: List[str] = []
        self._searches: List[Dict] = []
        self._pending_full_reads: List[str] = []  # 需要完整读取的文件
        
    async def explore(
        self, 
        query: str,
        initial_context: Optional[List[Dict]] = None
    ) -> ExplorationResult:
        """
        主入口：执行完整的仓库探索流程
        
        Args:
            query: 用户原始问题（如"详细说说每个目录的职责"）
            initial_context: 可选的初始上下文消息
            
        Returns:
            ExplorationResult: 结构化的探索结果
        """
        logger.info(f"[RepoExplore] Starting exploration for: {query[:50]}...")
        
        # 判断是否需要深度架构探索
        needs_deep_explore = bool(_ARCHITECTURE_QUERY_RE.search(query))
        
        # Phase 1: 发现阶段 - 了解仓库结构
        root_structure = await self._scan_root_structure()
        
        # Phase 2: 关键文档识别与完整读取
        key_docs = self._identify_key_documents(root_structure)
        
        for doc_path in key_docs:
            await self._full_file_read(doc_path)
            
        # Phase 3: 如果需要深度探索，进行定向搜索
        if needs_deep_explore:
            await self._deep_architecture_explore(query)
            
        # Phase 4: 组装探索结果
        result = self._assemble_result(query, needs_deep_explore)
        
        logger.info(
            f"[RepoExplore] Complete: {len(self._files_read)} files, "
            f"completeness={result.evidence_completeness}"
        )
        return result
    
    async def explore_stream(
        self,
        query: str,
        initial_context: Optional[List[Dict]] = None
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        流式探索：实时产出探索进度事件
        
        产出事件:
        - {"type": "explore_start", "query": str}
        - {"type": "phase_start", "phase": str}
        - {"type": "file_read", "path": str, "lines": int}
        - {"type": "directory_scan", "path": str, "items": int}
        - {"type": "search", "pattern": str, "hits": int}
        - {"type": "evidence_gap", "missing": List[str]}
        - {"type": "explore_complete", "result": ExplorationResult}
        """
        yield {"type": "explore_start", "query": query}
        
        needs_deep = bool(_ARCHITECTURE_QUERY_RE.search(query))
        
        # Phase 1
        yield {"type": "phase_start", "phase": "discovery"}
        root_structure = await self._scan_root_structure()
        yield {
            "type": "directory_scan", 
            "path": str(self.workspace),
            "items": len(root_structure.get("items", []))
        }
        
        # Phase 2
        yield {"type": "phase_start", "phase": "key_documents"}
        key_docs = self._identify_key_documents(root_structure)
        
        for doc_path in key_docs:
            content = await self._full_file_read(doc_path)
            lines = len(content.splitlines()) if content else 0
            yield {"type": "file_read", "path": doc_path, "lines": lines}
        
        # 检查是否有未完整读取的关键文档
        gaps = self._check_evidence_gaps()
        if gaps:
            yield {"type": "evidence_gap", "missing": gaps}
            
        # Phase 3
        if needs_deep:
            yield {"type": "phase_start", "phase": "deep_explore"}
            await self._deep_architecture_explore(query)
            
        # Complete
        result = self._assemble_result(query, needs_deep)
        yield {"type": "explore_complete", "result": result}
    
    async def _scan_root_structure(self) -> Dict[str, Any]:
        """扫描根目录结构"""
        try:
            from tools.file_list import execute as file_list_execute
            result = file_list_execute({"path": "."}, self.workspace)
            self._dirs_scanned.append(str(self.workspace))
            output = result.get("output", "") if isinstance(result, dict) else ""
            items = []
            for line in output.splitlines()[1:]:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    name = parts[1].rstrip("/")
                    items.append({"name": name, "is_dir": parts[0] == "d"})
            return {"items": items}
        except Exception as e:
            logger.warning(f"[RepoExplore] Root scan failed: {e}")
            return {"items": []}
    
    def _identify_key_documents(self, root_structure: Dict) -> List[str]:
        """从目录结构中识别关键文档"""
        items = root_structure.get("items", [])
        key_docs = []
        
        for item in items:
            name = item.get("name", "") if isinstance(item, dict) else str(item)
            for pattern in _KEY_DOC_PATTERNS:
                if re.search(pattern, name, re.IGNORECASE):
                    key_docs.append(name)
                    break
                    
        # 也去 .nanobot 目录下找 AGENTS.md
        nanobot_dir = self.workspace / ".nanobot"
        if nanobot_dir.exists():
            for pattern in _KEY_DOC_PATTERNS:
                if "AGENTS" in pattern:
                    agents_path = nanobot_dir / "AGENTS.md"
                    if agents_path.exists():
                        key_docs.append(str(agents_path.relative_to(self.workspace)))
                        
        return key_docs
    
    async def _full_file_read(self, path: str) -> str:
        """
        完整读取文件（自动处理大文件分片）
        
        与主循环的 B16 不同：这里强制收齐所有分片后再返回
        """
        from tools.file_read import execute as file_read_execute
        
        full_content_parts = []
        offset = 1
        
        while True:
            try:
                args = {
                    "path": path,
                    "offset": offset,
                    "limit": self.FULL_FILE_LIMIT,
                }
                result = file_read_execute(
                    args,
                    self.workspace,
                )
                
                if isinstance(result, dict):
                    content = result.get("output", "")
                else:
                    content = str(result)
                
                full_content_parts.append(content)
                
                # 检测是否还有更多内容
                m = _PARTIAL_READ_RE.search(content)
                if m:
                    _, end_line, total_lines = map(int, m.groups())
                    if end_line < total_lines:
                        offset = end_line + 1
                        logger.info(
                            f"[RepoExplore] Continuing partial read: {path} "
                            f"({end_line}/{total_lines})"
                        )
                        continue
                
                # 没有 partial 标记或已读完
                break
                
            except Exception as e:
                logger.warning(f"[RepoExplore] File read failed: {path} - {e}")
                break
        
        full_content = "\n".join(full_content_parts)
        self._files_read[path] = full_content[:2000]  # 存储摘要
        
        logger.info(f"[RepoExplore] Full read complete: {path} ({len(full_content)} chars)")
        return full_content
    
    async def _deep_architecture_explore(self, query: str):
        """
        深度架构探索：针对"模块职责"类问题进行定向搜索
        """
        # 搜索关键架构文件
        search_patterns = [
            r"(src|lib|app|core|utils|tools|components)",
            r"(README|CONTRIBUTING|ARCHITECTURE|DESIGN)",
        ]
        
        from tools.grep_search import execute as grep_search_execute
        
        for pattern in search_patterns:
            try:
                results = grep_search_execute(
                    {
                        "pattern": pattern,
                        "path": ".",
                        "include": "*.md",
                    },
                    self.workspace,
                )
                if isinstance(results, dict) and results.get("success"):
                    output = results.get("output", "")
                    hits = 0
                    m = re.search(r'Found\s+(\d+)\s+match', output)
                    if m:
                        hits = int(m.group(1))
                    self._searches.append({
                        "pattern": pattern,
                        "hits": hits,
                    })
            except Exception as e:
                logger.debug(f"[RepoExplore] Search skipped: {pattern} - {e}")
    
    def _check_evidence_gaps(self) -> List[str]:
        """检查证据缺口"""
        gaps = []
        
        # 检查是否读到关键架构文档
        has_structure_doc = any(
            re.search(r'PROJECT_STRUCTURE|ARCHITECTURE', path, re.I)
            for path in self._files_read.keys()
        )
        
        if not has_structure_doc:
            gaps.append("No PROJECT_STRUCTURE.md or ARCHITECTURE.md found/read")
            
        return gaps
    
    def _assemble_result(self, query: str, needs_deep: bool) -> ExplorationResult:
        """组装最终探索结果"""
        gaps = self._check_evidence_gaps()
        
        # 判断证据完整度
        if gaps and len(self._files_read) < 3:
            completeness = "partial"
        elif gaps:
            completeness = "mostly_complete"
        else:
            completeness = "complete"
            
        # 构建 findings 摘要（供主代理参考，但不是直接答案）
        findings_parts = [
            f"## Repository Exploration Summary",
            f"",
            f"**Query**: {query}",
            f"**Evidence Completeness**: {completeness}",
            f"",
            f"### Files Examined ({len(self._files_read)})",
        ]
        
        for path, content_preview in self._files_read.items():
            lines = len(content_preview.splitlines())
            findings_parts.append(f"- `{path}`: {lines} lines sampled")
            
        if self._dirs_scanned:
            findings_parts.extend([
                f"",
                f"### Directories Scanned",
            ])
            for d in self._dirs_scanned:
                findings_parts.append(f"- `{d}`")
                
        if gaps:
            findings_parts.extend([
                f"",
                f"### Evidence Gaps",
                f"⚠️ The following gaps may affect answer completeness:",
            ])
            for gap in gaps:
                findings_parts.append(f"- {gap}")
                
        findings_parts.extend([
            f"",
            f"### Instruction for Main Agent",
            f"Based on the exploration above, provide a factual response. "
            f"If evidence is marked 'partial', explicitly state limitations. "
            f"Do NOT invent directory purposes or module relationships not "
            f"supported by the examined files.",
        ])
        
        return ExplorationResult(
            success=True,
            query=query,
            files_read=[
                {"path": p, "preview": c[:500]} 
                for p, c in self._files_read.items()
            ],
            directories_scanned=self._dirs_scanned,
            searches_performed=self._searches,
            findings="\n".join(findings_parts),
            evidence_completeness=completeness,
            missing_evidence=gaps,
        )


# ═══════════════════════════════════════════════════════════════════
# 主循环集成接口
# ═══════════════════════════════════════════════════════════════════

async def run_repo_explore_subagent(
    query: str,
    env,
    workspace: str,
    session_id: str,
    stream: bool = False
) -> ExplorationResult:
    """
    供主循环调用的同步风格接口
    
    用法:
        if _is_repo_fact_question(user_message):
            explore_result = await run_repo_explore_subagent(
                user_message, env, workspace, session_id
            )
            # 把 explore_result.findings 注入主代理上下文
    """
    agent = RepoExploreAgent(env, workspace, session_id)
    
    if stream:
        # 流式模式：由调用方处理事件
        return agent.explore_stream(query)
    else:
        return await agent.explore(query)


def create_repo_explore_system_prompt() -> str:
    """
    供主代理使用的 repo-explore 结果解读指南
    
    主代理收到 ExplorationResult 后，应该遵循这些规则生成最终答案
    """
    return """\
[REPO-EXPLORE RESULT RECEIVED]

You have received the output of a dedicated read-only repository exploration. 
Follow these rules when forming your response:

1. EVIDENCE-BASED ONLY: Every claim about directory structure, module 
   responsibilities, or architecture MUST be directly supported by the 
   files listed in "Files Examined".

2. COMPLETENESS DISCLAIMER: If evidence_completeness is "partial", you 
   MUST begin your answer with: 
   "Based on the files I was able to examine..." or similar limitation notice.

3. NO INVENTION: Do NOT infer module relationships, architectural layers, 
   or design patterns from directory names alone. Only describe what the 
   exploration actually found.

4. STRUCTURED BUT PLAIN: Use clear structure (bullet points, short paragraphs) 
   but avoid elaborate categorization schemes (### bold headers for every 
   minor directory).

5. TOOL EVIDENCE PRECEDENCE: If the exploration found specific files, quote 
   or reference them directly. If a key file was not found, state that 
   explicitly rather than guessing.
"""
