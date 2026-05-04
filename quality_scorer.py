"""
回答质量评分系统 - 自我改进机制
"""
import json
import time
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class QualityScore:
    """质量评分"""
    response_id: str
    question: str
    response: str
    timestamp: float
    
    # 自动评分指标
    length_score: float = 0.0  # 长度合理性 (0-1)
    structure_score: float = 0.0  # 结构完整性 (0-1)
    relevance_score: float = 0.0  # 相关性 (0-1)
    
    # 用户反馈
    user_rating: Optional[int] = None  # 1-5星
    user_feedback: Optional[str] = None
    
    # 综合评分
    overall_score: float = 0.0
    
    # SNN指标
    snn_spikes: int = 0
    attention_score: float = 0.0
    processing_time: float = 0.0
    
    metadata: Dict = field(default_factory=dict)


class QualityScorer:
    """回答质量评分器"""
    
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.scores_file = storage_path / "quality_scores.jsonl"
        self.stats_file = storage_path / "quality_stats.json"
        
        # 确保目录存在
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # 加载统计数据
        self.stats = self._load_stats()
    
    def _load_stats(self) -> Dict:
        """加载统计数据"""
        if self.stats_file.exists():
            try:
                return json.loads(self.stats_file.read_text())
            except:
                pass
        return {
            'total_responses': 0,
            'avg_score': 0.0,
            'avg_user_rating': 0.0,
            'improvement_trend': [],
            'common_issues': {}
        }
    
    def _save_stats(self):
        """保存统计数据"""
        tmp = self.stats_file.with_suffix(self.stats_file.suffix + ".tmp")
        tmp.write_text(json.dumps(self.stats, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.stats_file)
    
    def auto_score(self, question: str, response: str, 
                   snn_spikes: int = 0, attention: float = 0.0,
                   processing_time: float = 0.0) -> QualityScore:
        """自动评分"""
        
        response_id = f"resp_{int(time.time() * 1000)}"
        
        # 1. 长度评分 (理想长度: 100-2000字)
        length = len(response)
        if length < 50:
            length_score = 0.3  # 太短
        elif length < 100:
            length_score = 0.6
        elif length <= 2000:
            length_score = 1.0  # 理想
        elif length <= 4000:
            length_score = 0.8
        else:
            length_score = 0.5  # 太长
        
        # 2. 结构评分 (检查是否有分段、列表、表格等)
        structure_score = 0.0
        
        # 检查分段
        paragraphs = response.count('\n\n') + 1
        if paragraphs >= 2:
            structure_score += 0.3
        
        # 检查列表
        if '-' in response or '*' in response or '1.' in response:
            structure_score += 0.3
        
        # 检查表格
        if '|' in response and '---' in response:
            structure_score += 0.2
        
        # 检查标题
        if '###' in response or '##' in response:
            structure_score += 0.2
        
        structure_score = min(1.0, structure_score)
        
        # 3. 相关性评分 (增强版)
        relevance_score = 0.5  # 默认中等
        
        # 清理回答内容：移除SNN状态文本（这些与问题无关）
        clean_response = response
        snn_patterns = [
            r'⚡\s*\d+脉冲',
            r'🎯\s*注意力[：:]?\s*[\d\.]+',
            r'⏱️\s*[\d\.]+s',
            r'SNN生成\d+脉冲.*?输出[。\.]*',
            r'LLM解码输出[。\.]*',
            r'🔧\s*已执行工具[：:]?[^\n]*\n*',
        ]
        import re
        for pattern in snn_patterns:
            clean_response = re.sub(pattern, '', clean_response, flags=re.IGNORECASE)
        clean_response = clean_response.strip()
        
        # 检查是否回答了问题
        question_lower = question.lower()
        response_lower = clean_response.lower()
        
        # 中文问题类型识别
        is_test_query = any(kw in question_lower for kw in ['测试', 'test', '验证', '运行测试'])
        is_snn_query = any(kw in question_lower for kw in ['snn', '脉冲', '神经网络', 'spike'])
        is_capability_query = any(kw in question_lower for kw in ['能力', '功能', '能做', 'capability'])
        is_module_query = any(kw in question_lower for kw in ['模块', '文件', '目录', 'module'])
        
        # 根据问题类型调整相关性评分
        if is_test_query:
            # 测试类问题：检查是否包含测试结果
            test_indicators = ['测试', '通过', '失败', '分数', '平均', '项', '%']
            matched = sum(1 for ind in test_indicators if ind in response_lower)
            if matched >= 4:
                relevance_score = 0.95
            elif matched >= 2:
                relevance_score = 0.85
            elif matched >= 1:
                relevance_score = 0.70
            else:
                relevance_score = 0.50
        elif is_snn_query:
            # SNN类问题：检查是否包含SNN相关信息
            snn_indicators = ['脉冲', 'snn', '注意力', '神经', 'spike', 'attention']
            matched = sum(1 for ind in snn_indicators if ind in response_lower)
            if matched >= 3:
                relevance_score = 0.90
            elif matched >= 1:
                relevance_score = 0.75
        elif is_capability_query:
            # 能力类问题
            cap_indicators = ['能力', '功能', '支持', '可以', '提供']
            matched = sum(1 for ind in cap_indicators if ind in response_lower)
            if matched >= 3:
                relevance_score = 0.90
            elif matched >= 1:
                relevance_score = 0.75
        else:
            # 通用问题：关键词匹配
            # 中文分词简化处理
            import re
            # 提取中文词汇（2-4字）
            question_words = re.findall(r'[\u4e00-\u9fa5]{2,4}', question_lower)
            # 提取英文单词
            question_words += re.findall(r'[a-z]{3,}', question_lower)
            
            if question_words:
                matched_words = sum(1 for w in question_words if w in response_lower)
                match_ratio = matched_words / len(question_words) if question_words else 0
                
                if match_ratio >= 0.6:
                    relevance_score = 0.90
                elif match_ratio >= 0.3:
                    relevance_score = 0.75
                elif match_ratio >= 0.1:
                    relevance_score = 0.60
        
        # 检查是否拒绝回答
        if '无法' in response_lower or '不知道' in response_lower or '不能' in response_lower:
            relevance_score *= 0.5
        
        # 综合评分 (加权平均)
        overall_score = (
            length_score * 0.2 +
            structure_score * 0.3 +
            relevance_score * 0.5
        )
        
        # 如果有SNN指标，加入考量
        if attention > 0.7:
            overall_score *= 1.1  # 高关注度加分
        overall_score = min(1.0, overall_score)
        
        score = QualityScore(
            response_id=response_id,
            question=question,
            response=response[:500],  # 只存储前500字符
            timestamp=time.time(),
            length_score=length_score,
            structure_score=structure_score,
            relevance_score=relevance_score,
            overall_score=overall_score,
            snn_spikes=snn_spikes,
            attention_score=attention,
            processing_time=processing_time
        )
        
        # 保存评分
        self._save_score(score)
        
        # 更新统计
        self._update_stats(score)
        
        return score
    
    def _save_score(self, score: QualityScore):
        """保存评分记录"""
        with open(self.scores_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(asdict(score), ensure_ascii=False) + '\n')
    
    def _update_stats(self, score: QualityScore):
        """更新统计数据"""
        self.stats['total_responses'] += 1
        
        # 更新平均分
        n = self.stats['total_responses']
        old_avg = self.stats['avg_score']
        self.stats['avg_score'] = old_avg + (score.overall_score - old_avg) / n
        
        # 记录趋势
        self.stats['improvement_trend'].append({
            'timestamp': score.timestamp,
            'score': score.overall_score
        })
        
        # 只保留最近100条趋势
        if len(self.stats['improvement_trend']) > 100:
            self.stats['improvement_trend'] = self.stats['improvement_trend'][-100:]
        
        self._save_stats()
    
    def add_user_feedback(self, response_id: str, rating: int, 
                          feedback: Optional[str] = None) -> bool:
        """添加用户反馈"""
        # 查找评分记录
        if not self.scores_file.exists():
            return False
        
        updated = False
        lines = []
        
        with open(self.scores_file, 'r', encoding='utf-8') as f:
            for line in f:
                data = json.loads(line.strip())
                if data['response_id'] == response_id:
                    data['user_rating'] = rating
                    data['user_feedback'] = feedback
                    updated = True
                    
                    # 更新统计
                    if rating:
                        old_avg = self.stats.get('avg_user_rating', 0.0)
                        count = self.stats.get('user_rating_count', 0)
                        self.stats['avg_user_rating'] = old_avg + (rating - old_avg) / (count + 1)
                        self.stats['user_rating_count'] = count + 1
                        self._save_stats()
                
                lines.append(json.dumps(data, ensure_ascii=False))
        
        if updated:
            tmp = self.scores_file.with_suffix(self.scores_file.suffix + ".tmp")
            tmp.write_text('\n'.join(lines), encoding='utf-8')
            tmp.replace(self.scores_file)
        
        return updated
    
    def get_improvement_suggestions(self) -> List[str]:
        """获取改进建议"""
        suggestions = []
        
        avg_score = self.stats.get('avg_score', 0)
        
        if avg_score < 0.5:
            suggestions.append("整体质量偏低，建议优化prompt结构")
        
        # 分析最近趋势
        trend = self.stats.get('improvement_trend', [])
        if len(trend) >= 10:
            recent = trend[-10:]
            avg_recent = sum(t['score'] for t in recent) / len(recent)
            
            if avg_recent < avg_score:
                suggestions.append("近期质量下降，检查是否有配置变更")
        
        # 用户反馈分析
        avg_user = self.stats.get('avg_user_rating', 0)
        if avg_user > 0 and avg_user < 3:
            suggestions.append("用户评分较低，需改进回答相关性")
        
        return suggestions
    
    def get_quality_report(self, days: int = 7) -> Dict:
        """获取质量报告"""
        cutoff = time.time() - days * 86400
        
        recent_scores = []
        if self.scores_file.exists():
            with open(self.scores_file, 'r', encoding='utf-8') as f:
                for line in f:
                    data = json.loads(line.strip())
                    if data['timestamp'] >= cutoff:
                        recent_scores.append(data)
        
        if not recent_scores:
            return {
                'period_days': days,
                'total_responses': 0,
                'avg_score': 0,
                'avg_user_rating': 0,
                'improvement_suggestions': ['暂无数据']
            }
        
        return {
            'period_days': days,
            'total_responses': len(recent_scores),
            'avg_score': sum(s['overall_score'] for s in recent_scores) / len(recent_scores),
            'avg_length_score': sum(s['length_score'] for s in recent_scores) / len(recent_scores),
            'avg_structure_score': sum(s['structure_score'] for s in recent_scores) / len(recent_scores),
            'avg_relevance_score': sum(s['relevance_score'] for s in recent_scores) / len(recent_scores),
            'avg_user_rating': self.stats.get('avg_user_rating', 0),
            'improvement_suggestions': self.get_improvement_suggestions()
        }
