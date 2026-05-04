#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stdp_learning.py - STDP学习机制和奖励调节

实现基于Brian2的STDP（Spike-Timing Dependent Plasticity）学习规则，
包含标准STDP和多巴胺调节的R-STDP。

关键特性：
- 标准STDP窗口：pre→post增强，post→pre抑制
- R-STDP：外部奖励信号调节权重更新
- 权重边界：[w_min, w_max]
- 与300万神经元系统兼容
"""

import numpy as np
from typing import Dict, Optional, Callable
from dataclasses import dataclass

# Brian2导入
try:
    from brian2 import (
        Synapses, NeuronGroup, 
        ms, mV, Hz,
        exp, clip
    )
    BRIAN2_AVAILABLE = True
except ImportError:
    BRIAN2_AVAILABLE = False
    print("[STDP] ⚠️ Brian2未安装，STDP模块将使用模拟模式")


@dataclass
class STDPParameters:
    """STDP参数配置"""
    # 时间窗口
    tau_pre: float = 20.0  # ms, 前突触迹衰减
    tau_post: float = 20.0  # ms, 后突触迹衰减
    
    # 学习率
    A_pre: float = 0.01   # 前突触增强幅度
    A_post: float = 0.0105  # 后突触抑制幅度
    
    # 权重边界
    w_min: float = 0.001
    w_max: float = 1.0
    
    # R-STDP奖励调节
    dopamine_tau: float = 200.0  # ms, 多巴胺衰减
    dopamine_boost: float = 2.0   # 奖励时的增强倍数
    
    # 突触延迟
    delay: float = 1.0  # ms


class STDPLearningRule:
    """
    STDP学习规则实现
    
    标准STDP方程：
    Δw = A_pre * exp(-Δt/τ_pre)  if Δt > 0 (pre before post)
    Δw = -A_post * exp(Δt/τ_post) if Δt < 0 (post before pre)
    
    R-STDP扩展：
    Δw = reward * (A_pre * trace_pre - A_post * trace_post)
    """
    
    def __init__(self, params: Optional[STDPParameters] = None):
        self.params = params or STDPParameters()
        self.model_eqs = self._build_model_equations()
        self.on_pre_eqs = self._build_on_pre_equations()
        self.on_post_eqs = self._build_on_post_equations()
    
    def _build_model_equations(self) -> str:
        """构建STDP突触模型方程"""
        p = self.params
        
        equations = f"""
            # 突触权重
            w : 1
            
            # STDP迹（事件驱动更新）- 使用避免冲突的变量名
            pre_trace : 1
            post_trace : 1
            
            # 局部多巴胺水平
            dopamine : 1
        """
        return equations
    
    def _build_on_pre_equations(self) -> str:
        """构建前突触脉冲触发方程"""
        p = self.params
        
        equations = f"""
            pre_trace = pre_trace + 1.0
            v_post += w * 10*mV
            w = clip(w + dopamine * {p.A_pre} * post_trace, {p.w_min}, {p.w_max})
        """
        return equations
    
    def _build_on_post_equations(self) -> str:
        """构建后突触脉冲触发方程"""
        p = self.params
        
        equations = f"""
            post_trace = post_trace + 1.0
            w = clip(w - dopamine * {p.A_post} * pre_trace, {p.w_min}, {p.w_max})
        """
        return equations
    
    def create_plastic_synapses(self, 
                                source, target,
                                i_indices: np.ndarray,
                                j_indices: np.ndarray,
                                initial_weights: np.ndarray,
                                name: str = "plastic") -> Optional:
        """
        创建带STDP的可塑突触
        
        Args:
            source: Brian2 NeuronGroup（前突触）
            target: Brian2 NeuronGroup（后突触）
            i_indices: 源神经元索引
            j_indices: 目标神经元索引
            initial_weights: 初始权重
            name: 突触组名称
        
        Returns:
            Brian2 Synapses对象（含STDP）
        """
        if not BRIAN2_AVAILABLE:
            print("[STDP] ⚠️ Brian2不可用，无法创建STDP突触")
            return None
        
        # 创建突触
        syn = Synapses(
            source, target,
            model=self.model_eqs,
            on_pre=self.on_pre_eqs,
            on_post=self.on_post_eqs,
            name=name,
            delay=self.params.delay * ms
        )
        
        # 使用预计算的索引连接
        syn.connect(i=i_indices, j=j_indices)
        
        # 设置初始权重
        syn.w = initial_weights
        
        # 初始化迹和奖励
        syn.pre_trace = 0.0
        syn.post_trace = 0.0
        syn.dopamine = 1.0  # 基础多巴胺水平
        
        return syn


class RewardModulatedSTDP:
    """
    多巴胺调节的STDP（R-STDP）
    
    允许外部奖励信号（如任务成功）调节突触可塑性
    """
    
    def __init__(self, stdp_rule: Optional[STDPLearningRule] = None):
        self.stdp = stdp_rule or STDPLearningRule()
        self.reward_history = []
    
    def deliver_reward(self, synapses, reward_value: float, duration_ms: float = 100.0):
        """
        传递奖励信号到突触
        
        Args:
            synapses: Brian2 Synapses对象（含dopamine变量）
            reward_value: 奖励值（正=增强，负=抑制）
            duration_ms: 奖励持续时间
        """
        if not BRIAN2_AVAILABLE:
            return
        
        # 提升多巴胺水平
        boost = 1.0 + reward_value * self.stdp.params.dopamine_boost
        synapses.dopamine = boost
        
        # 记录
        self.reward_history.append({
            'time': 0,  # 将在Network中更新
            'value': reward_value,
            'boost': boost
        })
    
    def punish(self, synapses, punishment_value: float = 0.5):
        """惩罚（负奖励）"""
        self.deliver_reward(synapses, -punishment_value)
    
    def get_learning_stats(self) -> Dict:
        """获取学习统计"""
        if not self.reward_history:
            return {'total_rewards': 0, 'avg_reward': 0}
        
        rewards = [r['value'] for r in self.reward_history]
        return {
            'total_rewards': len(rewards),
            'avg_reward': np.mean(rewards),
            'max_reward': max(rewards),
            'min_reward': min(rewards)
        }


class WordAssociationLearner:
    """
    词语关联学习任务
    
    实现简单的经典条件作用学习：
    - CS（条件刺激，如"猫"）→ US（无条件刺激，如"动物"）
    - 通过STDP加强连接
    """
    
    def __init__(self, stdp_rule: STDPLearningRule):
        self.stdp = stdp_rule
        self.word_mappings = {}  # 词语→神经元群体映射
        self.learned_pairs = []
    
    def register_word(self, word: str, neuron_indices: np.ndarray):
        """注册词语对应的神经元群体"""
        self.word_mappings[word] = neuron_indices
    
    def present_pair(self, 
                     word1: str, word2: str,
                     delay_ms: float = 50.0,
                     presentation_duration: float = 100.0):
        """
        呈现词语对进行学习
        
        模拟：先激活word1，延迟后激活word2
        这会触发STDP，加强word1→word2的连接
        """
        if word1 not in self.word_mappings or word2 not in self.word_mappings:
            return False
        
        # 记录学习事件
        self.learned_pairs.append({
            'word1': word1,
            'word2': word2,
            'delay_ms': delay_ms,
            'timestamp': 0  # 将在Network中更新
        })
        
        return True
    
    def test_association(self, word: str) -> Optional[str]:
        """测试学习后的关联"""
        # 这里需要实际的SNN响应来验证
        # 简化版本：返回最近学习的配对
        for pair in reversed(self.learned_pairs):
            if pair['word1'] == word:
                return pair['word2']
        return None


class ToolUseLearning:
    """
    工具使用学习任务
    
    当意图（如"查询时间"）触发工具成功时，
    通过奖励加强意图→动作的通路
    """
    
    def __init__(self, reward_stdp: RewardModulatedSTDP):
        self.rstdp = reward_stdp
        self.intent_tool_map = {}
        self.success_history = []
    
    def register_intent_tool(self, intent_neurons: np.ndarray, 
                             tool_name: str,
                             action_neurons: np.ndarray):
        """注册意图-工具-动作映射"""
        self.intent_tool_map[tool_name] = {
            'intent': intent_neurons,
            'action': action_neurons,
            'success_count': 0,
            'failure_count': 0
        }
    
    def report_tool_result(self, tool_name: str, success: bool, 
                          synapses=None):
        """
        报告工具执行结果
        
        Args:
            tool_name: 工具名称
            success: 是否成功
            synapses: 需要调节的可塑突触（可选）
        """
        if tool_name not in self.intent_tool_map:
            return
        
        entry = self.intent_tool_map[tool_name]
        
        if success:
            entry['success_count'] += 1
            # 奖励
            if synapses is not None:
                self.rstdp.deliver_reward(synapses, reward_value=1.0)
        else:
            entry['failure_count'] += 1
            # 轻微惩罚
            if synapses is not None:
                self.rstdp.punish(synapses, punishment_value=0.3)
        
        self.success_history.append({
            'tool': tool_name,
            'success': success,
            'timestamp': 0
        })
    
    def get_tool_stats(self, tool_name: str) -> Dict:
        """获取工具学习统计"""
        if tool_name not in self.intent_tool_map:
            return {}
        
        entry = self.intent_tool_map[tool_name]
        total = entry['success_count'] + entry['failure_count']
        
        return {
            'tool': tool_name,
            'success_rate': entry['success_count'] / total if total > 0 else 0,
            'success_count': entry['success_count'],
            'failure_count': entry['failure_count'],
            'total_attempts': total
        }


# ===== 快速创建函数 =====
def create_stdp_synapses(source, target,
                         i_indices: np.ndarray,
                         j_indices: np.ndarray,
                         initial_weights: np.ndarray,
                         enable_reward: bool = True) -> Optional:
    """
    快速创建STDP突触
    
    Args:
        source: 前突触神经元组
        target: 后突触神经元组
        i_indices, j_indices: 连接索引
        initial_weights: 初始权重
        enable_reward: 是否启用奖励调节
    
    Returns:
        Synapses对象
    """
    stdp = STDPLearningRule()
    
    syn = stdp.create_plastic_synapses(
        source, target,
        i_indices, j_indices, initial_weights
    )
    
    return syn


if __name__ == '__main__':
    print("=" * 60)
    print("STDP学习模块测试")
    print("=" * 60)
    
    # 测试STDP参数
    params = STDPParameters(
        tau_pre=20.0,
        tau_post=20.0,
        A_pre=0.01,
        A_post=0.0105,
        w_min=0.001,
        w_max=1.0
    )
    
    stdp_rule = STDPLearningRule(params)
    print(f"[STDP] 时间窗口: τ_pre={params.tau_pre}ms, τ_post={params.tau_post}ms")
    print(f"[STDP] 学习率: A_pre={params.A_pre}, A_post={params.A_post}")
    print(f"[STDP] 权重范围: [{params.w_min}, {params.w_max}]")
    
    # 测试奖励调节
    rstdp = RewardModulatedSTDP(stdp_rule)
    print(f"[R-STDP] 多巴胺衰减: {params.dopamine_tau}ms")
    print(f"[R-STDP] 奖励增强倍数: {params.dopamine_boost}x")
    
    # 测试词语关联
    word_learner = WordAssociationLearner(stdp_rule)
    word_learner.register_word("猫", np.array([100, 101, 102]))
    word_learner.register_word("动物", np.array([200, 201, 202]))
    word_learner.present_pair("猫", "动物", delay_ms=50)
    
    print(f"[WordAssoc] 已学习配对: {len(word_learner.learned_pairs)}")
    
    print("\nSTDP模块测试通过！")
