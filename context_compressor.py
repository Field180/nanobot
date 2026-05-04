"""
上下文压缩器 - 智能压缩对话历史以适应模型上下文窗口限制

功能：
1. Token计数与阈值检测
2. 滑动窗口+摘要压缩策略
3. 本地SQLite存储压缩历史
4. 与现有安全架构集成
"""

import os
import json
import sqlite3
import logging
import hashlib
import re
import asyncio
import threading
import requests
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Callable, Union
from dataclasses import dataclass, field
from pathlib import Path
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_MAX_TOKENS = 262144  # Qwen3.5-35B 上下文限制
DEFAULT_RESERVE_TOKENS = 8192  # 预留空间给新输入
DEFAULT_KEEP_RECENT_TURNS = 2  # 保留最近轮数
DEFAULT_SUMMARY_MAX_TOKENS = 2048  # 摘要最大token数


@dataclass
class CompressionResult:
    """压缩结果"""
    compressed_messages: List[Dict[str, Any]]
    summary: str
    tokens_saved: int
    compression_ratio: float
    compressed_at: datetime = field(default_factory=datetime.now)
    method: str = "sliding_window"


@dataclass
class Message:
    """消息结构"""
    role: str
    content: str
    tokens: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


class SimpleTokenizer:
    """简单的Token计数器（基于字符估算，实际应使用tiktoken或模型分词器）"""
    
    # 不同语言的字符/Token比率估算
    RATIOS = {
        'chinese': 0.5,  # 中文约2字符/token
        'english': 0.25,  # 英文约4字符/token
        'code': 0.3,  # 代码约3.3字符/token
        'mixed': 0.4,  # 混合约2.5字符/token
    }
    
    def __init__(self, chars_per_token: float = 2.5):
        self.chars_per_token = chars_per_token
    
    def count_tokens(self, text: str) -> int:
        """估算文本token数"""
        if not text:
            return 0
        
        # 检测文本类型
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        total_chars = len(text)
        
        if total_chars == 0:
            return 0
        
        # 根据中文比例调整
        chinese_ratio = chinese_chars / total_chars
        if chinese_ratio > 0.7:
            ratio = self.RATIOS['chinese']
        elif chinese_ratio < 0.1:
            ratio = self.RATIOS['english']
        else:
            ratio = self.RATIOS['mixed']
        
        return int(total_chars * ratio)
    
    def encode(self, text: str) -> List[int]:
        """模拟encode接口（返回伪token列表）"""
        token_count = self.count_tokens(text)
        return list(range(token_count))


class TokenizerInterface(ABC):
    """Tokenizer抽象接口，支持可插拔"""
    
    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """计算文本token数"""
        pass
    
    @abstractmethod
    def encode(self, text: str) -> List[int]:
        """编码文本为token列表"""
        pass


class TiktokenTokenizer(TokenizerInterface):
    """精确Token计数器（使用tiktoken）- 单例模式（Phase1 融合 claw）"""
    
    _instance = None
    _encoder = None
    _available = False
    _fallback = None
    
    def __new__(cls, encoding_name: str = "cl100k_base"):
        """单例模式：确保只创建一个实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._fallback = SimpleTokenizer()
            
            try:
                import tiktoken
                # 设置缓存目录，避免网络下载
                cache_dir = os.path.expanduser('~/.cache/tiktoken')
                os.makedirs(cache_dir, exist_ok=True)
                os.environ['TIKTOKEN_CACHE_DIR'] = cache_dir
                
                cls._encoder = tiktoken.get_encoding(encoding_name)
                cls._available = True
                logger.info(f"[TiktokenTokenizer] tiktoken 单例加载成功: {encoding_name}")
            except ImportError:
                logger.warning("[TiktokenTokenizer] tiktoken未安装，回退到估算模式")
            except Exception as e:
                # 网络错误或其他错误，回退到估算模式
                logger.warning(f"[TiktokenTokenizer] tiktoken加载失败: {e}，回退到估算模式")
        
        return cls._instance
    
    @classmethod
    def get_instance(cls):
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._available:
            return len(self._encoder.encode(text))
        return self._fallback.count_tokens(text)
    
    def encode(self, text: str) -> List[int]:
        if self._available:
            return self._encoder.encode(text)
        return self._fallback.encode(text)


class LLMSummarizer:
    """LLM摘要生成器"""
    
    def __init__(
        self,
        api_base: str = "http://192.168.140.1:8090/v1",
        api_key: str = "sk-llamacpp",
        model: str = None,
        max_summary_tokens: int = 512,
        timeout: int = 30
    ):
        self.api_base = api_base
        self.api_key = api_key
        self.model = model
        self.max_summary_tokens = max_summary_tokens
        self.timeout = timeout
        self._available = False
        self._check_availability()
    
    def _check_availability(self):
        """检查API可用性"""
        try:
            resp = requests.get(
                f"{self.api_base.rstrip('/')}/models",
                timeout=5
            )
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                if models:
                    self.model = self.model or models[0].get("id")
                    self._available = True
                    logger.info(f"[LLMSummarizer] API可用，模型: {self.model}")
        except Exception as e:
            logger.warning(f"[LLMSummarizer] API不可用: {e}")
    
    def summarize(self, text: str, max_length: int = None) -> str:
        """生成摘要"""
        if not self._available or not text:
            return ""
        
        max_length = max_length or self.max_summary_tokens
        
        # 限制输入长度
        if len(text) > 8000:
            text = text[:8000] + "..."
        
        prompt = f"""请将以下对话历史压缩为简洁的摘要，保留关键信息：
- 用户的核心目标和问题
- 已执行的重要动作和工具调用
- 得出的关键结论
- 未解决的问题

对话历史：
{text}

摘要（不超过200字）："""

        try:
            resp = requests.post(
                f"{self.api_base.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "你是一个专业的对话摘要助手，擅长提取关键信息。"},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": max_length,
                    "temperature": 0.3,
                    "stream": False
                },
                timeout=self.timeout
            )
            
            if resp.status_code == 200:
                result = resp.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content.strip()
        except Exception as e:
            logger.error(f"[LLMSummarizer] 摘要生成失败: {e}")
        
        return ""
    
    async def summarize_async(self, text: str, max_length: int = None) -> str:
        """异步生成摘要"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.summarize, text, max_length)


class ContextStorage:
    """本地SQLite存储压缩历史"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.expanduser("~/.nanobot/context_compression.db")
        
        self.db_path = db_path
        self._ensure_db()
    
    def _ensure_db(self):
        """确保数据库和表存在"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS compression_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    compressed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    summary TEXT NOT NULL,
                    compressed_message_ids TEXT,
                    tokens_saved INTEGER,
                    compression_ratio REAL,
                    compression_method TEXT,
                    metadata TEXT
                )
            """)
            
            # 单独创建索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_id ON compression_history(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_compressed_at ON compression_history(compressed_at)")
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    token_count INTEGER,
                    is_active BOOLEAN DEFAULT 1,
                    UNIQUE(session_id, created_at)
                )
            """)
            conn.commit()
    
    def save_compression(self, session_id: str, result: CompressionResult, 
                         compressed_message_ids: List[str] = None):
        """保存压缩记录"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO compression_history 
                (session_id, summary, compressed_message_ids, tokens_saved, 
                 compression_ratio, compression_method, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                result.summary,
                json.dumps(compressed_message_ids or []),
                result.tokens_saved,
                result.compression_ratio,
                result.method,
                json.dumps({
                    'compressed_at': result.compressed_at.isoformat(),
                    'original_count': len(result.compressed_messages)
                })
            ))
            conn.commit()
    
    def save_session_summary(self, session_id: str, summary: str, token_count: int):
        """保存会话摘要"""
        with sqlite3.connect(self.db_path) as conn:
            # 先将旧摘要设为非活跃
            conn.execute("""
                UPDATE session_summaries SET is_active = 0 WHERE session_id = ?
            """, (session_id,))
            
            # 插入新摘要
            conn.execute("""
                INSERT INTO session_summaries (session_id, summary_text, token_count)
                VALUES (?, ?, ?)
            """, (session_id, summary, token_count))
            conn.commit()
    
    def get_latest_summary(self, session_id: str) -> Optional[Tuple[str, int]]:
        """获取最新活跃摘要"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT summary_text, token_count FROM session_summaries
                WHERE session_id = ? AND is_active = 1
                ORDER BY created_at DESC LIMIT 1
            """, (session_id,))
            row = cursor.fetchone()
            if row:
                return row[0], row[1]
            return None
    
    def get_compression_history(self, session_id: str, limit: int = 10) -> List[Dict]:
        """获取压缩历史"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT id, compressed_at, summary, tokens_saved, compression_ratio, method
                FROM compression_history
                WHERE session_id = ?
                ORDER BY compressed_at DESC
                LIMIT ?
            """, (session_id, limit))
            
            return [
                {
                    'id': row[0],
                    'compressed_at': row[1],
                    'summary': row[2][:200] + '...' if len(row[2]) > 200 else row[2],
                    'tokens_saved': row[3],
                    'compression_ratio': row[4],
                    'method': row[5]
                }
                for row in cursor.fetchall()
            ]
    
    def cleanup_old_records(self, days: int = 30):
        """清理旧记录"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                DELETE FROM compression_history
                WHERE compressed_at < datetime('now', ?)
            """, (f'-{days} days',))
            conn.commit()


class ContextCompressor:
    """上下文压缩器"""
    
    def __init__(
        self,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        reserve_tokens: int = DEFAULT_RESERVE_TOKENS,
        keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS,
        summary_max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS,
        storage: ContextStorage = None,
        tokenizer: TokenizerInterface = None,
        llm_summarizer: LLMSummarizer = None,
        use_llm_summary: bool = True,
        async_compression: bool = True
    ):
        self.max_tokens = max_tokens
        self.reserve_tokens = reserve_tokens
        self.threshold = max_tokens - reserve_tokens
        self.keep_recent_turns = keep_recent_turns
        self.summary_max_tokens = summary_max_tokens
        self.storage = storage or ContextStorage()
        
        # 可插拔tokenizer
        if tokenizer is None:
            # 尝试使用tiktoken，失败则回退到估算
            self.tokenizer = TiktokenTokenizer()
        else:
            self.tokenizer = tokenizer
        
        # LLM摘要生成器
        self.llm_summarizer = llm_summarizer
        self.use_llm_summary = use_llm_summary
        
        # 异步压缩
        self.async_compression = async_compression
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._pending_compressions: Dict[str, asyncio.Task] = {}
        
        # 压缩统计
        self.stats = {
            'total_compressions': 0,
            'total_tokens_saved': 0,
            'avg_compression_ratio': 0.0
        }
    
    def count_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """计算消息列表总token数"""
        total = 0
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, str):
                total += self.tokenizer.count_tokens(content)
            elif isinstance(content, list):
                # 处理多模态消息
                for part in content:
                    if isinstance(part, dict) and 'text' in part:
                        total += self.tokenizer.count_tokens(part['text'])
        return total
    
    def needs_compression(self, messages: List[Dict[str, Any]]) -> Tuple[bool, int]:
        """检查是否需要压缩"""
        token_count = self.count_tokens(messages)
        return token_count > self.threshold, token_count
    
    def extract_key_info(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """提取关键信息（规则方法，无需LLM）"""
        key_info = {
            'user_goals': [],
            'actions_taken': [],
            'key_conclusions': [],
            'pending_questions': [],
            'tools_used': [],
            'files_mentioned': set()
        }
        
        for msg in messages:
            content = msg.get('content', '')
            role = msg.get('role', '')
            
            # 提取用户目标（用户消息中的问句或请求）
            if role == 'user':
                questions = re.findall(r'[？?].*?(?=[。！？\n]|$)', content)
                key_info['user_goals'].extend(questions[:2])
                
                # 检测文件引用
                files = re.findall(r'[\'"]([^\s\'"]+\.[a-zA-Z0-9]+)[\'"]', content)
                key_info['files_mentioned'].update(files)
            
            # 提取工具调用
            tool_calls = re.findall(r'工具[：:]\s*(\w+)|调用[：:]\s*(\w+)|tool[：:]\s*(\w+)', content, re.I)
            for match in tool_calls:
                tool_name = next((t for t in match if t), '')
                if tool_name:
                    key_info['tools_used'].append(tool_name)
            
            # 提取结论（助手消息中的关键陈述）
            if role == 'assistant':
                conclusions = re.findall(r'(?:结论[：:]|结果[：:]|答案[：:]|总结[：:])([^。！？\n]{10,100})', content)
                key_info['key_conclusions'].extend(conclusions[:2])
        
        # 转换set为list以便序列化
        key_info['files_mentioned'] = list(key_info['files_mentioned'])
        return key_info
    
    def generate_summary(self, messages: List[Dict[str, Any]], 
                         key_info: Dict[str, Any],
                         use_llm: bool = None) -> str:
        """生成压缩摘要（支持LLM或规则方法）"""
        
        # 决定是否使用LLM
        should_use_llm = use_llm if use_llm is not None else self.use_llm_summary
        
        # 尝试LLM摘要
        if should_use_llm and self.llm_summarizer:
            # 构建对话文本
            conversation_text = "\n".join([
                f"{m.get('role', 'user')}: {m.get('content', '')[:500]}"
                for m in messages[-20:]  # 最近20条
            ])
            
            llm_summary = self.llm_summarizer.summarize(conversation_text)
            if llm_summary:
                logger.info("[ContextCompressor] 使用LLM生成摘要")
                return f"[历史摘要]\n{llm_summary}"
        
        # 规则方法生成摘要
        summary_parts = []
        
        # 用户目标
        if key_info['user_goals']:
            goals = key_info['user_goals'][-5:]  # 最近5个
            summary_parts.append(f"用户目标: {'; '.join(goals)}")
        
        # 已执行动作
        if key_info['actions_taken']:
            summary_parts.append(f"已执行: {', '.join(key_info['actions_taken'][-5:])}")
        
        # 工具使用
        if key_info['tools_used']:
            tools = list(set(key_info['tools_used']))[-5:]
            summary_parts.append(f"使用工具: {', '.join(tools)}")
        
        # 关键结论
        if key_info['key_conclusions']:
            summary_parts.append(f"关键结论: {'; '.join(key_info['key_conclusions'][-3:])}")
        
        # 文件引用
        if key_info['files_mentioned']:
            summary_parts.append(f"相关文件: {', '.join(key_info['files_mentioned'][-5:])}")
        
        if not summary_parts:
            # 如果没有提取到关键信息，生成简单摘要
            msg_count = len(messages)
            user_msgs = sum(1 for m in messages if m.get('role') == 'user')
            assistant_msgs = msg_count - user_msgs
            summary_parts.append(f"历史对话: {msg_count}条消息 (用户{user_msgs}条, 助手{assistant_msgs}条)")
        
        summary = "[历史摘要]\n" + "\n".join(summary_parts)
        
        # 确保摘要不超过限制
        summary_tokens = self.tokenizer.count_tokens(summary)
        if summary_tokens > self.summary_max_tokens:
            # 截断摘要
            max_chars = int(self.summary_max_tokens * 2.5)
            summary = summary[:max_chars] + "..."
        
        return summary
    
    def compress(
        self,
        messages: List[Dict[str, Any]],
        session_id: str = None,
        strategy: str = "sliding_window"
    ) -> CompressionResult:
        """
        压缩消息列表
        
        Args:
            messages: 原始消息列表
            session_id: 会话ID（用于存储）
            strategy: 压缩策略 (sliding_window, key_extraction, recursive)
        
        Returns:
            CompressionResult: 压缩结果
        """
        original_tokens = self.count_tokens(messages)
        
        # 检查是否需要压缩
        if original_tokens <= self.threshold:
            return CompressionResult(
                compressed_messages=messages,
                summary="",
                tokens_saved=0,
                compression_ratio=1.0,
                method="none"
            )
        
        logger.info(f"[ContextCompressor] 开始压缩: 原始tokens={original_tokens}, 阈值={self.threshold}")
        
        # 根据策略选择压缩方法
        if strategy == "sliding_window":
            result = self._compress_sliding_window(messages)
        elif strategy == "key_extraction":
            result = self._compress_key_extraction(messages)
        else:
            result = self._compress_sliding_window(messages)
        
        # 保存压缩记录
        if session_id and result.tokens_saved > 0:
            self.storage.save_compression(session_id, result)
            # 也保存摘要
            if result.summary:
                summary_tokens = self.tokenizer.count_tokens(result.summary)
                self.storage.save_session_summary(session_id, result.summary, summary_tokens)
        
        # 更新统计
        self.stats['total_compressions'] += 1
        self.stats['total_tokens_saved'] += result.tokens_saved
        self.stats['avg_compression_ratio'] = (
            (self.stats['avg_compression_ratio'] * (self.stats['total_compressions'] - 1) + 
             result.compression_ratio) / self.stats['total_compressions']
        )
        
        logger.info(f"[ContextCompressor] 压缩完成: 节省tokens={result.tokens_saved}, 压缩比={result.compression_ratio:.2%}")
        
        return result
    
    def _compress_sliding_window(self, messages: List[Dict[str, Any]]) -> CompressionResult:
        """滑动窗口压缩策略"""
        # 计算要保留的消息数
        # 保留最近N轮（每轮=1个user + 1个assistant）
        keep_count = self.keep_recent_turns * 2
        
        # 确保保留的消息以user开头
        if len(messages) > keep_count:
            recent = messages[-keep_count:]
            # 如果第一条是assistant，从更早开始
            if recent and recent[0].get('role') == 'assistant':
                # 找到最近的user消息位置
                for i in range(len(messages) - keep_count, -1, -1):
                    if messages[i].get('role') == 'user':
                        recent = messages[i:]
                        break
        else:
            recent = messages
        
        # 需要压缩的消息
        to_compress = messages[:-len(recent)] if len(messages) > len(recent) else []
        
        # 生成摘要
        summary = ""
        if to_compress:
            key_info = self.extract_key_info(to_compress)
            summary = self.generate_summary(to_compress, key_info)
        
        # 构建新消息列表
        new_messages = []
        
        # 摘要作为system消息插入（确保在开头）
        if summary:
            new_messages.append({
                'role': 'system',
                'content': summary
            })
        
        new_messages.extend(recent)
        
        # 计算压缩效果
        new_tokens = self.count_tokens(new_messages)
        tokens_saved = max(0, self.count_tokens(messages) - new_tokens)
        compression_ratio = new_tokens / self.count_tokens(messages) if self.count_tokens(messages) > 0 else 1.0
        
        return CompressionResult(
            compressed_messages=new_messages,
            summary=summary,
            tokens_saved=tokens_saved,
            compression_ratio=compression_ratio,
            method="sliding_window"
        )
    
    def _compress_key_extraction(self, messages: List[Dict[str, Any]]) -> CompressionResult:
        """关键信息提取压缩策略"""
        # 提取所有关键信息
        key_info = self.extract_key_info(messages)
        
        # 生成摘要
        summary = self.generate_summary(messages, key_info)
        
        # 只保留摘要和最后一条用户消息
        new_messages = [
            {'role': 'system', 'content': summary}
        ]
        
        # 找到最后一条用户消息
        for msg in reversed(messages):
            if msg.get('role') == 'user':
                new_messages.append(msg)
                break
        
        new_tokens = self.count_tokens(new_messages)
        tokens_saved = max(0, self.count_tokens(messages) - new_tokens)
        compression_ratio = new_tokens / self.count_tokens(messages) if self.count_tokens(messages) > 0 else 1.0
        
        return CompressionResult(
            compressed_messages=new_messages,
            summary=summary,
            tokens_saved=tokens_saved,
            compression_ratio=compression_ratio,
            method="key_extraction"
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """获取压缩统计"""
        return self.stats.copy()
    
    async def compress_async(
        self,
        messages: List[Dict[str, Any]],
        session_id: str = None,
        strategy: str = "sliding_window"
    ) -> CompressionResult:
        """异步压缩（不阻塞响应）"""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            self._executor,
            self.compress,
            messages,
            session_id,
            strategy
        )
        return result
    
    def schedule_background_compression(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        callback: Callable[[CompressionResult], None] = None
    ):
        """调度后台压缩任务"""
        def _compress_and_callback():
            result = self.compress(messages, session_id)
            if callback:
                callback(result)
            return result
        
        future = self._executor.submit(_compress_and_callback)
        return future


# 便捷函数
def create_compressor(
    max_tokens: int = None,
    model_config: Dict[str, Any] = None,
    use_llm_summary: bool = True,
    llm_api_base: str = "http://192.168.140.1:8090/v1"
) -> ContextCompressor:
    """创建压缩器实例"""
    if model_config:
        max_tokens = model_config.get('context_length', DEFAULT_MAX_TOKENS)
    
    # 创建LLM摘要器（可选）
    llm_summarizer = None
    if use_llm_summary:
        llm_summarizer = LLMSummarizer(api_base=llm_api_base)
    
    return ContextCompressor(
        max_tokens=max_tokens or DEFAULT_MAX_TOKENS,
        reserve_tokens=DEFAULT_RESERVE_TOKENS,
        keep_recent_turns=DEFAULT_KEEP_RECENT_TURNS,
        llm_summarizer=llm_summarizer,
        use_llm_summary=use_llm_summary
    )


def maybe_compress_history(
    messages: List[Dict[str, Any]],
    session_id: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    use_llm_summary: bool = True,
    llm_api_base: str = "http://192.168.140.1:8090/v1"
) -> Tuple[List[Dict[str, Any]], bool, str]:
    """
    检查并压缩历史（便捷接口）
    
    Args:
        messages: 消息列表
        session_id: 会话ID
        max_tokens: 最大token数
        use_llm_summary: 是否使用LLM生成摘要
        llm_api_base: LLM API地址
    
    Returns:
        (compressed_messages, was_compressed, summary)
    """
    compressor = create_compressor(
        max_tokens=max_tokens,
        use_llm_summary=use_llm_summary,
        llm_api_base=llm_api_base
    )
    needs_compress, token_count = compressor.needs_compression(messages)
    
    if not needs_compress:
        return messages, False, ""
    
    result = compressor.compress(messages, session_id)
    return result.compressed_messages, True, result.summary
