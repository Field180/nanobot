"""
工作记忆压缩系统 - 长对话上下文优化
"""
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import logging
import re

logger = logging.getLogger(__name__)


@dataclass
class MemorySegment:
    """记忆片段"""
    id: str
    content: str
    timestamp: float
    importance: float  # 0-1
    access_count: int = 0
    last_access: float = 0.0
    compressed: bool = False
    summary: Optional[str] = None


class WorkingMemoryCompressor:
    """工作记忆压缩器"""
    
    def __init__(self, max_tokens: int = 32768, compression_ratio: float = 0.3):
        """
        Args:
            max_tokens: 最大token预算 (默认32K，匹配128K上下文配置)
            compression_ratio: 压缩比例 (0.3 = 保留30%)
        """
        self.max_tokens = max_tokens
        self.compression_ratio = compression_ratio
        
        self.segments: List[MemorySegment] = []
        self.summary_cache: Dict[str, str] = {}
        
        # 统计
        self.stats = {
            'total_compressions': 0,
            'tokens_saved': 0,
            'avg_compression_time': 0.0
        }
    
    def estimate_tokens(self, text: str) -> int:
        """估算token数量 (简单启发式: 1 token ≈ 2字符)"""
        return len(text) // 2
    
    def add_memory(self, content: str, importance: float = 0.5) -> str:
        """添加记忆"""
        segment_id = f"mem_{int(time.time() * 1000)}"
        
        segment = MemorySegment(
            id=segment_id,
            content=content,
            timestamp=time.time(),
            importance=importance,
            last_access=time.time()
        )
        
        self.segments.append(segment)
        
        # 检查是否需要压缩
        if self._should_compress():
            self.compress()
        
        return segment_id
    
    def _should_compress(self) -> bool:
        """检查是否需要压缩"""
        total_tokens = sum(self.estimate_tokens(s.content) for s in self.segments)
        return total_tokens > self.max_tokens
    
    def compress(self) -> Dict:
        """压缩工作记忆"""
        start_time = time.time()
        
        if not self.segments:
            return {'compressed': 0, 'tokens_saved': 0}
        
        # 1. 计算每个片段的优先级分数
        scored_segments = []
        for seg in self.segments:
            score = self._calculate_priority(seg)
            scored_segments.append((score, seg))
        
        # 2. 按优先级排序
        scored_segments.sort(key=lambda x: x[0], reverse=True)
        
        # 3. 选择要压缩的片段 (低优先级)
        total_tokens = sum(self.estimate_tokens(s.content) for s in self.segments)
        target_tokens = int(self.max_tokens * self.compression_ratio)
        
        compressed_count = 0
        tokens_saved = 0
        
        for score, seg in scored_segments:
            if total_tokens <= target_tokens:
                break
            
            if not seg.compressed:
                # 压缩此片段
                summary = self._summarize(seg.content)
                original_tokens = self.estimate_tokens(seg.content)
                summary_tokens = self.estimate_tokens(summary)
                
                seg.summary = summary
                seg.compressed = True
                seg.content = summary  # 用摘要替换原内容
                
                total_tokens -= (original_tokens - summary_tokens)
                tokens_saved += (original_tokens - summary_tokens)
                compressed_count += 1
        
        # 更新统计
        self.stats['total_compressions'] += 1
        self.stats['tokens_saved'] += tokens_saved
        elapsed = time.time() - start_time
        old_avg = self.stats['avg_compression_time']
        n = self.stats['total_compressions']
        self.stats['avg_compression_time'] = old_avg + (elapsed - old_avg) / n
        
        return {
            'compressed': compressed_count,
            'tokens_saved': tokens_saved,
            'remaining_tokens': total_tokens
        }
    
    def _calculate_priority(self, segment: MemorySegment) -> float:
        """计算片段优先级"""
        score = segment.importance
        
        # 访问频率加分
        score += min(segment.access_count * 0.1, 0.3)
        
        # 新近度加分
        age = time.time() - segment.timestamp
        if age < 60:  # 1分钟内
            score += 0.2
        elif age < 300:  # 5分钟内
            score += 0.1
        
        # 已压缩的片段优先级降低
        if segment.compressed:
            score *= 0.5
        
        return score
    
    def _summarize(self, content: str) -> str:
        """生成摘要 (简单提取式摘要)"""
        # 检查缓存
        cache_key = content[:100]
        if cache_key in self.summary_cache:
            return self.summary_cache[cache_key]
        
        # 简单摘要策略
        lines = content.split('\n')
        
        # 提取关键句
        key_sentences = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 关键词检测
            if any(kw in line for kw in ['结论', '结果', '答案', '重要', '关键', '总结', '✅', '💡']):
                key_sentences.append(line)
            elif line.startswith('#') or line.startswith('- ') or line.startswith('* '):
                key_sentences.append(line)
        
        # 如果关键句太少，取前几句
        if len(key_sentences) < 2:
            key_sentences = [l.strip() for l in lines[:3] if l.strip()]
        
        # 限制长度
        summary = ' | '.join(key_sentences[:5])
        if len(summary) > 300:
            summary = summary[:300] + '...'
        
        # 缓存
        self.summary_cache[cache_key] = summary
        
        return summary
    
    def get_context(self, max_tokens: Optional[int] = None) -> str:
        """获取当前上下文"""
        if max_tokens is None:
            max_tokens = self.max_tokens
        
        # 按优先级排序
        scored = [(self._calculate_priority(s), s) for s in self.segments]
        scored.sort(key=lambda x: x[0], reverse=True)
        
        # 构建上下文
        context_parts = []
        current_tokens = 0
        
        for score, seg in scored:
            content = seg.summary if seg.compressed else seg.content
            tokens = self.estimate_tokens(content)
            
            if current_tokens + tokens <= max_tokens:
                context_parts.append(content)
                current_tokens += tokens
                seg.access_count += 1
                seg.last_access = time.time()
        
        return '\n\n'.join(context_parts)
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            **self.stats,
            'total_segments': len(self.segments),
            'compressed_segments': sum(1 for s in self.segments if s.compressed),
            'current_tokens': sum(self.estimate_tokens(s.content) for s in self.segments)
        }
    
    def clear_old_memories(self, max_age_hours: int = 24):
        """清理旧记忆"""
        cutoff = time.time() - max_age_hours * 3600
        
        old_count = len(self.segments)
        self.segments = [s for s in self.segments if s.timestamp >= cutoff]
        new_count = len(self.segments)
        
        return {
            'removed': old_count - new_count,
            'remaining': new_count
        }
    
    def export_state(self) -> Dict:
        """导出状态"""
        return {
            'segments': [
                {
                    'id': s.id,
                    'content': s.content,
                    'timestamp': s.timestamp,
                    'importance': s.importance,
                    'access_count': s.access_count,
                    'compressed': s.compressed,
                    'summary': s.summary
                }
                for s in self.segments
            ],
            'stats': self.stats
        }
    
    def import_state(self, state: Dict):
        """导入状态"""
        self.segments = []
        for seg_data in state.get('segments', []):
            segment = MemorySegment(
                id=seg_data['id'],
                content=seg_data['content'],
                timestamp=seg_data['timestamp'],
                importance=seg_data['importance'],
                access_count=seg_data.get('access_count', 0),
                compressed=seg_data.get('compressed', False),
                summary=seg_data.get('summary')
            )
            self.segments.append(segment)
        
        self.stats = state.get('stats', self.stats)


class ContextWindowManager:
    """上下文窗口管理器"""
    
    def __init__(self, max_context: int = 16000, reserved_for_response: int = 4000):
        self.max_context = max_context
        self.reserved_for_response = reserved_for_response
        self.available_for_context = max_context - reserved_for_response
        
        self.memory_compressor = WorkingMemoryCompressor(
            max_tokens=self.available_for_context
        )
        
        # 对话历史
        self.conversation_history: List[Dict] = []
    
    def add_turn(self, role: str, content: str, importance: float = 0.5):
        """添加对话轮次"""
        self.conversation_history.append({
            'role': role,
            'content': content,
            'timestamp': time.time()
        })
        
        # 添加到工作记忆
        self.memory_compressor.add_memory(
            f"{role}: {content}",
            importance=importance
        )
    
    def build_context(self, system_prompt: str = "") -> str:
        """构建上下文"""
        # 获取压缩后的记忆
        memory_context = self.memory_compressor.get_context()
        
        # 构建完整上下文
        parts = []
        
        if system_prompt:
            parts.append(system_prompt)
        
        if memory_context:
            parts.append(f"## 对话历史摘要\n{memory_context}")
        
        return '\n\n'.join(parts)
    
    def get_stats(self) -> Dict:
        """获取统计"""
        return {
            'conversation_turns': len(self.conversation_history),
            'memory_stats': self.memory_compressor.get_stats(),
            'max_context': self.max_context,
            'available_for_context': self.available_for_context
        }
