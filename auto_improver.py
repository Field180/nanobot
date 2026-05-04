"""
自动改进系统 - 基于质量评分自动优化回答格式
支持：多规则检测、学习机制、持久化、统计分析、自适应阈值
"""
import json
import time
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import Counter
import logging

logger = logging.getLogger(__name__)


@dataclass
class ImprovementRule:
    """改进规则"""
    name: str
    condition: str  # 检测条件
    action: str     # 改进动作
    threshold: float
    enabled: bool = True
    hit_count: int = 0      # 触发次数
    success_count: int = 0  # 成功改进次数


@dataclass 
class ImprovementRecord:
    """改进记录"""
    timestamp: float
    question: str
    response: str
    issues: List[Dict]
    quality_before: float
    quality_after: float = 0.0
    improved: bool = False


class AutoImprover:
    """自动改进器 - 增强版"""
    
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.rules_file = storage_path / "improvement_rules.json"
        self.history_file = storage_path / "improvement_history.jsonl"
        self.stats_file = storage_path / "improvement_stats.json"
        self.learning_file = storage_path / "improvement_learning.json"
        
        # 确保目录存在
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # 默认改进规则
        self.rules = self._init_rules()
        self.history: List[Dict] = []
        self.learning_data: Dict = {}
        
        # 加载历史数据
        self._load_history()
        self._load_learning_data()
        
    def _init_rules(self) -> List[ImprovementRule]:
        """初始化改进规则 - 扩展版"""
        return [
            # 格式规则
            ImprovementRule("禁止表格", "contains_table", "强化禁止表格约束", 0.0),
            ImprovementRule("减少空行", "excessive_newlines", "压缩换行", 3.0),
            ImprovementRule("控制字数", "word_count_exceeds", "精简回答", 200.0),
            ImprovementRule("禁止HTML", "contains_html", "移除HTML标签", 0.0),
            
            # 内容规则
            ImprovementRule("模块数量准确", "wrong_module_count", "修正模块数量", 0.0),
            ImprovementRule("避免重复", "repetitive_content", "去重", 0.3),
            ImprovementRule("回答相关性", "off_topic", "聚焦问题", 0.5),
            
            # 结构规则
            ImprovementRule("先给答案", "answer_at_end", "调整顺序", 0.0),
            ImprovementRule("SNN过程精简", "snn_too_long", "精简SNN描述", 50.0),
        ]
    
    def _load_history(self):
        """加载历史记录"""
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r') as f:
                    for line in f:
                        if line.strip():
                            self.history.append(json.loads(line))
            except Exception as e:
                logger.warning(f"加载历史记录失败: {e}")
    
    def _load_learning_data(self):
        """加载学习数据"""
        if self.learning_file.exists():
            try:
                self.learning_data = json.loads(self.learning_file.read_text())
            except Exception as e:
                logger.warning(f"加载学习数据失败: {e}")
                self.learning_data = {}
    
    def _save_learning_data(self):
        """保存学习数据"""
        try:
            tmp = self.learning_file.with_suffix(self.learning_file.suffix + ".tmp")
            tmp.write_text(json.dumps(self.learning_data, indent=2), encoding="utf-8")
            tmp.replace(self.learning_file)
        except Exception as e:
            logger.warning(f"保存学习数据失败: {e}")
    
    def analyze_response(self, response: str, quality_score: float, question: str = "") -> Dict:
        """分析回答，检测问题 - 增强版"""
        issues = []
        
        # 1. 检测表格（严格）
        table_matches = re.findall(r'\|[^\n]+\|', response)
        if table_matches:
            issues.append({
                'rule': '禁止表格',
                'severity': 'critical',
                'detail': f'检测到{len(table_matches)}行表格',
                'hint': '❌❌❌ 绝对禁止使用|...|格式的Markdown表格'
            })
        
        # 2. 检测空行
        newline_groups = re.findall(r'\n{3,}', response)
        if newline_groups:
            issues.append({
                'rule': '减少空行',
                'severity': 'high',
                'detail': f'检测到{len(newline_groups)}处连续空行',
                'hint': '❌ 段落间无空行，列表项间无空行'
            })
        
        # 3. 检测字数
        word_count = len(response)
        if word_count > 200:
            issues.append({
                'rule': '控制字数',
                'severity': 'medium',
                'detail': f'字数{word_count}超过200',
                'hint': f'❌ 回答控制在200字以内，当前{word_count}字'
            })
        
        # 4. 检测HTML标签
        html_tags = re.findall(r'<[^>]+>', response)
        if html_tags:
            issues.append({
                'rule': '禁止HTML',
                'severity': 'high',
                'detail': f'检测到{len(html_tags)}个HTML标签',
                'hint': '❌ 禁止输出HTML标签'
            })
        
        # 5. 检测错误模块数量
        if re.search(r'110[\+个]', response):
            issues.append({
                'rule': '模块数量准确',
                'severity': 'critical',
                'detail': '模块数量描述错误(110+)',
                'hint': '❌ 模块数量回答"约15个Python文件"'
            })
        
        # 6. 检测重复内容
        sentences = response.split('。')
        if len(sentences) > 2:
            sentence_counts = Counter(s.strip() for s in sentences if s.strip())
            duplicates = [s for s, c in sentence_counts.items() if c > 1]
            if duplicates:
                issues.append({
                    'rule': '避免重复',
                    'severity': 'medium',
                    'detail': f'检测到{len(duplicates)}处重复',
                    'hint': '❌ 避免重复表述'
                })
        
        # 7. 检测SNN过程过长
        snn_match = re.search(r'### 🧠.*?(?=###|$)', response, re.DOTALL)
        if snn_match:
            snn_len = len(snn_match.group(0))
            if snn_len > 200:
                issues.append({
                    'rule': 'SNN过程精简',
                    'severity': 'medium',
                    'detail': f'SNN过程{snn_len}字过长',
                    'hint': '❌ SNN过程最多1句话'
                })
        
        # 8. 检测答案位置（是否在最后）
        if '最终确认' in response or '总结' in response:
            answer_pos = response.rfind('最终确认')
            if answer_pos > len(response) * 0.7:
                issues.append({
                    'rule': '先给答案',
                    'severity': 'low',
                    'detail': '答案在回答末尾',
                    'hint': '❌ 先给出核心答案，再解释过程'
                })
        
        return {
            'issues': issues,
            'quality_score': quality_score,
            'needs_improvement': len(issues) > 0,
            'question': question,
            'response': response
        }
    
    def generate_improvement_hints(self, analysis: Dict) -> str:
        """生成改进提示 - 增强版"""
        if not analysis['needs_improvement']:
            return ""
        
        # 按严重程度排序
        severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        sorted_issues = sorted(
            analysis['issues'], 
            key=lambda x: severity_order.get(x['severity'], 4)
        )
        
        hints = []
        for issue in sorted_issues[:5]:  # 最多5条提示
            hints.append(issue['hint'])
        
        return "\n".join(hints)
    
    def record_improvement(self, analysis: Dict, quality_after: float = 0.0):
        """记录改进 - 增强版"""
        record = {
            'timestamp': time.time(),
            'question': analysis.get('question', '')[:100],
            'issues': analysis['issues'],
            'quality_before': analysis['quality_score'],
            'quality_after': quality_after,
            'improved': quality_after > analysis['quality_score']
        }
        self.history.append(record)
        
        # 更新规则统计
        for issue in analysis['issues']:
            for rule in self.rules:
                if rule.name == issue['rule']:
                    rule.hit_count += 1
                    if quality_after > analysis['quality_score']:
                        rule.success_count += 1
        
        # 保存到文件
        try:
            with open(self.history_file, 'a') as f:
                f.write(json.dumps(record) + '\n')
        except Exception as e:
            logger.warning(f"保存记录失败: {e}")
        
        # 更新学习数据
        self._update_learning(analysis, quality_after)
    
    def _update_learning(self, analysis: Dict, quality_after: float):
        """更新学习数据"""
        for issue in analysis['issues']:
            rule_name = issue['rule']
            if rule_name not in self.learning_data:
                self.learning_data[rule_name] = {
                    'total_hits': 0,
                    'improvements': 0,
                    'contexts': []
                }
            
            self.learning_data[rule_name]['total_hits'] += 1
            if quality_after > analysis['quality_score']:
                self.learning_data[rule_name]['improvements'] += 1
            
            # 记录上下文（用于模式识别）
            context = {
                'question_type': self._classify_question(analysis.get('question', '')),
                'severity': issue['severity']
            }
            if len(self.learning_data[rule_name]['contexts']) < 100:
                self.learning_data[rule_name]['contexts'].append(context)
        
        self._save_learning_data()
    
    def _classify_question(self, question: str) -> str:
        """分类问题类型"""
        question = question.lower()
        if any(kw in question for kw in ['模块', '文件', '目录']):
            return 'structure'
        elif any(kw in question for kw in ['能做', '能力', '功能']):
            return 'capability'
        elif any(kw in question for kw in ['api', '接口', '技术']):
            return 'technical'
        elif any(kw in question for kw in ['为什么', '怎么', '如何']):
            return 'howto'
        else:
            return 'general'
    
    def get_improvement_stats(self) -> Dict:
        """获取改进统计 - 增强版"""
        total = len(self.history)
        improved = sum(1 for h in self.history if h.get('improved', False))
        
        # 问题类型统计
        issue_counts = Counter()
        for h in self.history:
            for issue in h.get('issues', []):
                issue_counts[issue['rule']] += 1
        
        # 规则效率统计
        rule_stats = {}
        for rule in self.rules:
            if rule.hit_count > 0:
                rule_stats[rule.name] = {
                    'hits': rule.hit_count,
                    'successes': rule.success_count,
                    'success_rate': rule.success_count / rule.hit_count
                }
        
        return {
            'total_improvements': total,
            'successful_improvements': improved,
            'success_rate': improved / total if total > 0 else 0,
            'issue_distribution': dict(issue_counts.most_common(10)),
            'rule_efficiency': rule_stats,
            'recent_issues': [h['issues'] for h in self.history[-5:]]
        }
    
    def get_adaptive_threshold(self, rule_name: str) -> float:
        """获取自适应阈值"""
        for rule in self.rules:
            if rule.name == rule_name:
                # 如果规则触发频繁但改进效果差，提高阈值
                if rule.hit_count > 10:
                    success_rate = rule.success_count / rule.hit_count
                    if success_rate < 0.3:
                        return rule.threshold * 1.5
                    elif success_rate > 0.7:
                        return rule.threshold * 0.8
                return rule.threshold
        return 0.0
    
    def get_recent_hints(self, limit: int = 3) -> str:
        """获取最近的改进提示（用于注入prompt）"""
        if not self.history:
            return ""
        
        recent_issues = []
        for record in reversed(self.history[-10:]):
            for issue in record.get('issues', []):
                if issue not in recent_issues:
                    recent_issues.append(issue)
                if len(recent_issues) >= limit:
                    break
            if len(recent_issues) >= limit:
                break
        
        hints = [issue['hint'] for issue in recent_issues if 'hint' in issue]
        return "\n".join(hints) if hints else ""


# 全局实例
AUTO_IMPROVER = None

def init_auto_improver(storage_path: Path):
    """初始化自动改进器"""
    global AUTO_IMPROVER
    AUTO_IMPROVER = AutoImprover(storage_path)
    return AUTO_IMPROVER

def get_auto_improver():
    """获取自动改进器实例"""
    return AUTO_IMPROVER
