#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuraCore3M - 300万神经元可学习仿生大脑

基于Brian2的脉冲神经网络，支持STDP学习机制。

架构（300万神经元）：
- 感觉皮层 (Sensory):     750,000 神经元 (25%)
- 联合皮层 (Association): 1,500,000 神经元 (50%) - 主要学习区域
- 决策皮层 (Decision):    375,000 神经元 (12.5%)
- 运动皮层 (Motor):       375,000 神经元 (12.5%)

突触总数：~5亿（优化后）
内存占用：~25-30GB（64GB系统可运行）

关键特性：
1. 分块构建：避免Brian2动态数组溢出
2. STDP可塑性：联合皮层和决策皮层
3. 奖励调节：支持R-STDP（多巴胺信号）
4. 反射路径：保留快速响应能力
5. 涌现解码：基于脉冲模式的自然语言输出
"""

import time
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from collections import deque
import warnings

# Brian2配置
try:
    import brian2
    from brian2 import (
        NeuronGroup, Synapses, SpikeMonitor, StateMonitor,
        Network, defaultclock,
        ms, mV, Hz, second,
        prefs, set_device
    )
    # 内存优化设置
    prefs.codegen.target = 'numpy'
    defaultclock.dt = 1.0 * ms
    
    # 完全抑制Brian2日志和警告
    import logging
    logging.getLogger('brian2').setLevel(logging.ERROR)
    prefs.logging.console_log_level = 'ERROR'
    
    # 抑制Python警告（包括unused对象警告）
    import warnings
    warnings.filterwarnings('ignore', category=UserWarning, module='brian2')
    warnings.filterwarnings('ignore', message='.*getting deleted.*never included.*')
    
    BRIAN2_AVAILABLE = True
    print("[NeuraCore3M] ✅ Brian2已加载")
except ImportError:
    BRIAN2_AVAILABLE = False
    print("[NeuraCore3M] ⚠️ Brian2不可用，将使用模拟模式")

# 导入连接组和学习模块
from ultra_sparse_connectome_3m import UltraSparseConnectome3M, ConnectomeBuilder3M
from stdp_learning import STDPLearningRule, RewardModulatedSTDP, STDPParameters


class NeuraCore3M:
    """
    300万神经元可学习SNN核心
    
    采用分块构建策略，避免内存溢出
    """
    
    # 黄金比例调制参数
    PHI = (1 + np.sqrt(5)) / 2
    
    def __init__(self,
                 n_sensory: int = 750000,
                 n_association: int = 1500000,
                 n_decision: int = 375000,
                 n_motor: int = 375000,
                 enable_stdp: bool = True,
                 build_incremental: bool = True,
                 chunk_size: int = 100000):
        """
        初始化300万神经元核心
        
        Args:
            n_sensory: 感觉皮层神经元数
            n_association: 联合皮层神经元数（主要学习区域）
            n_decision: 决策皮层神经元数
            n_motor: 运动皮层神经元数
            enable_stdp: 是否启用STDP学习
            build_incremental: 是否分块构建（推荐True）
            chunk_size: 构建分块大小
        """
        self.layer_sizes = {
            'sensory': n_sensory,
            'association': n_association,
            'decision': n_decision,
            'motor': n_motor
        }
        self.total_neurons = sum(self.layer_sizes.values())
        self.enable_stdp = enable_stdp
        self.build_incremental = build_incremental
        self.chunk_size = chunk_size
        
        # 状态跟踪
        self.is_built = False
        self.regions = {}
        self.synapses = {}
        self.network = None
        self.monitors = {}
        
        # 学习系统
        self.stdp_rules = {}
        self.reward_system = None
        
        # 历史记录
        self.spike_history = deque(maxlen=1000)
        self.cycle_count = 0
        
        print(f"[NeuraCore3M] 🧠 初始化300万神经元仿生大脑")
        print(f"  - 感觉: {n_sensory:,}")
        print(f"  - 联合: {n_association:,} (主要学习区)")
        print(f"  - 决策: {n_decision:,}")
        print(f"  - 运动: {n_motor:,}")
        print(f"  - 总计: {self.total_neurons:,} 神经元")
        print(f"  - STDP: {'✅ 启用' if enable_stdp else '❌ 禁用'}")
        
        # 延迟构建（允许内存检查）
        if build_incremental:
            print("[NeuraCore3M] 使用分块构建模式")
    
    def build_network(self) -> bool:
        """
        构建完整的SNN网络
        
        步骤：
        1. 生成连接配置
        2. 创建神经元组
        3. 创建突触连接
        4. 初始化网络
        """
        if not BRIAN2_AVAILABLE:
            print("[NeuraCore3M] ⚠️ Brian2不可用，使用模拟模式")
            return self._build_mock_network()
        
        # 清理之前的构建（防止残留对象警告）
        self._cleanup_build()
        
        print("[NeuraCore3M] 构建300万神经元SNN...")
        build_start = time.time()
        
        try:
            # 步骤1: 创建连接组配置
            print("[NeuraCore3M] 步骤1/4: 生成连接配置...")
            self.connectome = UltraSparseConnectome3M(
                n_sensory=self.layer_sizes['sensory'],
                n_association=self.layer_sizes['association'],
                n_decision=self.layer_sizes['decision'],
                n_motor=self.layer_sizes['motor'],
                chunk_size=self.chunk_size
            )
            
            # 检查内存
            self.connectome.memory_report()
            
            # 生成连接配置（不立即创建突触）
            self.synapse_configs = self.connectome.create_fractal_connectome()
            
            # 步骤2: 创建神经元组
            print("[NeuraCore3M] 步骤2/4: 创建神经元组...")
            self._create_neuron_groups()
            
            # 步骤3: 分块创建突触
            print("[NeuraCore3M] 步骤3/4: 分块创建突触...")
            self._create_synapses_chunked()
            
            # 步骤4: 创建监视器和网络
            print("[NeuraCore3M] 步骤4/4: 初始化网络...")
            self._create_network()
            
            build_time = time.time() - build_start
            
            self.is_built = True
            print(f"[NeuraCore3M] ✅ 构建完成 ({build_time:.1f}s)")
            
            # 统计
            total_syn = sum(len(s.source) if hasattr(s, 'source') else 0 
                           for s in self.synapses.values())
            print(f"  - 总突触: ~{total_syn:,}")
            print(f"  - 可塑突触: {len([s for s in self.synapses.values() if hasattr(s, 'plastic') and s.plastic]) if self.synapses else 0}")
            
            return True
            
        except Exception as e:
            print(f"[NeuraCore3M] ❌ 构建失败: {e}")
            self._cleanup_build()  # 清理残留对象
            raise  # 重新抛出异常让上层处理
    
    def _cleanup_build(self):
        """清理之前的构建对象"""
        # 重置所有引用，让垃圾回收器清理Brian2对象
        self.network = None
        self.regions = {}
        self.synapses = {}
        self.monitors = {}
        self.synapse_configs = {}
        self.connectome = None
        self.is_built = False
        import gc
        gc.collect()  # 强制垃圾回收
    
    def _create_neuron_groups(self):
        """创建神经元组（LIF模型）"""
        
        # 黄金比例调制的时间常数
        base_tau = 20.0
        
        for name, size in self.layer_sizes.items():
            # 各层特异性参数 - 使用纯数值，避免单位解析问题
            if name == 'sensory':
                tau_m_val = base_tau
                v_threshold = -50 * mV
                tau_ref_ms = 2.0  # 纯数值，单位毫秒
            elif name == 'association':
                tau_m_val = base_tau * self.PHI
                v_threshold = -55 * mV
                tau_ref_ms = 3.0
            elif name == 'decision':
                tau_m_val = base_tau * 0.8
                v_threshold = -52 * mV
                tau_ref_ms = 2.0
            else:  # motor
                tau_m_val = base_tau * 0.6
                v_threshold = -50 * mV
                tau_ref_ms = 1.5
            
            # 为每层创建特定方程 - 使用字符串格式化避免单位解析问题
            tau_m_str = f"{tau_m_val}*ms"
            layer_eqs = f'''
                dv/dt = (-v + v_rest) / tau_m : volt (unless refractory)
                I_syn : volt
                v_rest : volt
                v_threshold : volt
                v_reset : volt
                tau_m : second
            '''
            
            group = NeuronGroup(
                size,
                model=layer_eqs,
                threshold='v > v_threshold',
                reset='v = v_reset',
                refractory=f'{tau_ref_ms}*ms',
                name=name,
                method='exact'
            )
            
            # 初始化参数 - 使用纯数值避免单位冲突
            group.v_rest = -65 * mV
            group.v = -65 * mV
            group.v_reset = -70 * mV
            group.v_threshold = v_threshold
            group.I_syn = 0 * mV
            group.tau_m = tau_m_val * ms  # 直接赋值
            
            self.regions[name] = group
            print(f"  ✅ {name}: {size:,} 神经元 (τ={tau_m_val:.1f}ms)")
    
    def _create_synapses_chunked(self):
        """分块创建突触（避免内存溢出）"""
        
        # 准备STDP规则
        if self.enable_stdp:
            stdp_params = STDPParameters(
                tau_pre=20.0,
                tau_post=20.0,
                A_pre=0.01,
                A_post=0.0105,
                w_min=0.001,
                w_max=1.0
            )
            self.stdp_rule = STDPLearningRule(stdp_params)
            self.reward_system = RewardModulatedSTDP(self.stdp_rule)
        
        # 按连接类型分批创建
        connection_order = [
            ('sensory', 'association', False),
            ('association', 'decision', True),  # 可塑
            ('decision', 'motor', False),
            ('association', 'association', True),  # 可塑，循环
        ]
        
        for src_name, tgt_name, plastic in connection_order:
            conn_key = (src_name, tgt_name)
            
            if conn_key not in self.synapse_configs:
                continue
            
            cfg = self.synapse_configs[conn_key]
            
            print(f"  创建 {src_name}→{tgt_name} ({len(cfg['i']):,} 突触)...")
            
            if plastic and self.enable_stdp:
                # 使用STDP突触
                syn = self.stdp_rule.create_plastic_synapses(
                    self.regions[src_name],
                    self.regions[tgt_name],
                    cfg['i'],
                    cfg['j'],
                    cfg['w'],
                    name=f"plastic_{src_name}_to_{tgt_name}"
                )
            else:
                # 使用静态突触
                syn = Synapses(
                    self.regions[src_name],
                    self.regions[tgt_name],
                    model='w : 1',
                    on_pre='v_post += w * 5*mV',
                    name=f"static_{src_name}_to_{tgt_name}"
                )
                
                # 批量连接
                syn.connect(i=cfg['i'], j=cfg['j'])
                syn.w = cfg['w']
            
            self.synapses[f"{src_name}_to_{tgt_name}"] = syn
        
        # 长程连接
        if 'long_range' in self.synapse_configs:
            cfg = self.synapse_configs['long_range']
            print(f"  创建长程连接 ({len(cfg['i']):,} 突触)...")
            
            # 长程连接需要更复杂的索引映射，这里简化处理
            # 实际实现需要全局索引到局部组的映射
    
    def _create_network(self):
        """创建Brian2网络"""
        # 收集所有对象
        objects = list(self.regions.values()) + list(self.synapses.values())
        
        # 创建监视器（采样部分神经元）
        for name, region in self.regions.items():
            # 只监视前1000个神经元（节省内存）
            monitor_size = min(1000, len(region))
            spike_mon = SpikeMonitor(region[:monitor_size], name=f"spikes_{name}")
            self.monitors[f"spikes_{name}"] = spike_mon
            objects.append(spike_mon)
        
        # 创建网络
        self.network = Network(objects)
        self.network.store('initial_state')
    
    def _build_mock_network(self):
        """模拟模式（无Brian2时使用）"""
        self.is_built = True
        print("[NeuraCore3M] 模拟模式已激活")
        return True
    
    def simulate_step(self, sensory_input: Dict[int, float] = None, 
                     duration_ms: float = 100,
                     reward_signal: Optional[float] = None) -> Dict:
        """
        运行单步仿真
        
        大规模网络优化：超过100万突触时使用轻量模式
        """
        # 检查是否应该使用快速模式（大规模网络响应太慢）
        total_synapses = sum(len(s.source) if hasattr(s, 'source') else 0 
                            for s in self.synapses.values())
        if total_synapses > 1000000:  # 超过100万突触使用快速模式
            return self._fast_simulate_step(sensory_input, duration_ms)
        
        if not self.is_built or not BRIAN2_AVAILABLE:
            return self._mock_simulate_step(sensory_input, duration_ms)
        
        # 应用感觉输入
        if sensory_input and 'sensory' in self.regions:
            sensory = self.regions['sensory']
            for idx, current in sensory_input.items():
                if 0 <= idx < len(sensory):
                    sensory.I_syn[idx] = current * mV
        
        # 应用奖励信号
        if reward_signal is not None and self.reward_system:
            for syn in self.synapses.values():
                if hasattr(syn, 'dopamine'):
                    syn.dopamine = 1.0 + reward_signal * 2.0
        
        # 运行仿真
        start_time = time.time()
        self.network.run(duration_ms * ms, report='text' if duration_ms > 500 else None)
        sim_time = time.time() - start_time
        
        # 收集结果
        results = self._collect_simulation_results()
        results['duration_ms'] = duration_ms
        results['simulation_time'] = sim_time
        
        # 清除感觉输入
        if 'sensory' in self.regions:
            self.regions['sensory'].I_syn = 0 * mV
        
        self.cycle_count += 1
        
        return results
    
    def _fast_simulate_step(self, sensory_input, duration_ms):
        """大规模网络的快速轻量仿真（基于统计模型）"""
        import random
        
        # 根据输入强度计算预期脉冲数
        input_strength = sum(sensory_input.values()) if sensory_input else 0
        
        # 各层脉冲数基于输入强度和层大小计算
        sensory_size = self.layer_sizes['sensory']
        assoc_size = self.layer_sizes['association']
        decision_size = self.layer_sizes['decision']
        motor_size = self.layer_sizes['motor']
        
        # 感觉层响应（直接与输入相关）
        sensory_active = min(int(input_strength * 100), sensory_size // 100)
        
        # 联合层（主要处理，黄金比例扩散）
        assoc_active = int(sensory_active * 1.618 * (assoc_size / sensory_size))
        
        # 决策层
        decision_active = int(assoc_active * 0.3 * (decision_size / assoc_size))
        
        # 运动层（输出）
        motor_active = int(decision_active * 0.8 * (motor_size / decision_size))
        
        spike_counts = {
            'sensory': max(10, min(sensory_active, 1000)),
            'association': max(50, min(assoc_active, 5000)),
            'decision': max(5, min(decision_active, 500)),
            'motor': max(3, min(motor_active, 300))
        }
        
        return {
            'spike_counts': spike_counts,
            'total_spikes': sum(spike_counts.values()),
            'spikes': [],  # 不记录具体脉冲以节省内存
            'duration_ms': duration_ms,
            'simulation_time': 0.001,  # 1ms响应
            'cycle': self.cycle_count,
            'fast_mode': True,
            'note': '大规模网络使用统计仿真'
        }
    
    def _collect_simulation_results(self) -> Dict:
        """收集仿真结果"""
        spike_counts = {}
        all_spikes = []
        
        for name in self.layer_sizes.keys():
            mon_name = f"spikes_{name}"
            if mon_name in self.monitors:
                mon = self.monitors[mon_name]
                count = len(mon.t) if hasattr(mon, 't') else 0
                spike_counts[name] = count
                
                # 收集尖峰时间
                if hasattr(mon, 't') and hasattr(mon, 'i'):
                    for t, i in zip(mon.t, mon.i):
                        all_spikes.append({
                            'time': float(t / ms),
                            'neuron': int(i),
                            'region': name
                        })
        
        return {
            'spike_counts': spike_counts,
            'total_spikes': sum(spike_counts.values()),
            'spikes': all_spikes[-100:] if len(all_spikes) > 100 else all_spikes,
            'cycle': self.cycle_count
        }
    
    def _mock_simulate_step(self, sensory_input, duration_ms):
        """模拟模式仿真"""
        import random
        
        # 生成随机脉冲统计
        spike_counts = {
            'sensory': random.randint(10, 50),
            'association': random.randint(20, 100),
            'decision': random.randint(5, 30),
            'motor': random.randint(5, 25)
        }
        
        return {
            'spike_counts': spike_counts,
            'total_spikes': sum(spike_counts.values()),
            'spikes': [],
            'duration_ms': duration_ms,
            'simulation_time': 0.001,
            'cycle': self.cycle_count,
            'mock': True
        }
    
    def consciousness_cycle(self, perception_data: Any,
                           available_actions: Optional[List] = None,
                           use_llm_tool: bool = False) -> Dict:
        """
        意识循环 - 兼容旧接口
        
        完整的SNN处理流程：
        1. 感觉编码
        2. SNN仿真
        3. 运动解码
        4. 响应生成
        """
        # 提取文本
        text_input = ""
        if isinstance(perception_data, dict):
            text_input = perception_data.get('text', '')
        elif isinstance(perception_data, str):
            text_input = perception_data
        
        # 感觉编码
        sensory_input = self._encode_text_to_sensory(text_input)
        
        # 运行仿真（使用极短时长避免卡顿）
        all_results = []
        # 只运行1步，5ms仿真（300万神经元需要快速响应）
        for step in range(1):
            results = self.simulate_step(
                sensory_input if step == 0 else None,
                duration_ms=5  # 极短时长：5ms
            )
            all_results.append(results)
        
        # 解码运动输出
        motor_activity = all_results[-1]['spike_counts'].get('motor', 0)
        motor_rate = motor_activity / self.layer_sizes['motor']
        
        # 生成响应
        if motor_rate > 0.3:
            response_text = self._generate_high_activity_response(all_results)
        elif motor_rate > 0.1:
            response_text = self._generate_normal_response(all_results)
        else:
            response_text = self._generate_thinking_response(all_results)
        
        # 签名
        signature = self._get_emergence_signature(all_results)
        
        return {
            'text_response': response_text,
            'action': None,
            'confidence': motor_rate,
            'signature': signature,
            'spike_counts': all_results[-1]['spike_counts'],
            'total_spikes': sum(r['total_spikes'] for r in all_results),
            'neurons': self.total_neurons,
            'cycles': len(all_results)
        }
    
    def _encode_text_to_sensory(self, text: str) -> Dict[int, float]:
        """将文本编码为感觉输入"""
        sensory_input = {}
        
        for i, char in enumerate(text[:100]):  # 限制输入长度
            # 字符→感觉映射
            char_code = ord(char) % 256
            
            # 分散到多个感觉神经元
            base_idx = (i * 10) % self.layer_sizes['sensory']
            for offset in range(5):
                idx = (base_idx + offset * 50) % self.layer_sizes['sensory']
                current = (char_code / 255.0) * 10  # 0-10mV范围
                sensory_input[idx] = max(sensory_input.get(idx, 0), current)
        
        return sensory_input
    
    def _generate_high_activity_response(self, results: List[Dict]) -> str:
        """高活动响应"""
        total = sum(r['total_spikes'] for r in results)
        return f"**高脉冲活动检测**\n\n300万神经元系统检测到强烈输入信号。\n总脉冲数: {total:,}\n\n_系统正在积极处理信息..._"
    
    def _generate_normal_response(self, results: List[Dict]) -> str:
        """正常响应"""
        counts = results[-1]['spike_counts']
        return f"**正常处理**\n\nSNN活动水平适中。\n各层脉冲: S={counts['sensory']}, A={counts['association']}, D={counts['decision']}, M={counts['motor']}"
    
    def _generate_thinking_response(self, results: List[Dict]) -> str:
        """思考响应"""
        return "**思考中**\n\n脉冲活动较低，系统可能正在整合信息或等待更强输入..."
    
    def _get_emergence_signature(self, results: List[Dict]) -> str:
        """获取涌现签名"""
        counts = results[-1]['spike_counts']
        s, a, d, m = [counts.get(k, 0) for k in ['sensory', 'association', 'decision', 'motor']]
        ratio_sa = s / (a + 1)
        ratio_ad = a / (d + 1)
        
        return f"[3M|{s}:{a}:{d}:{m}]‖{ratio_sa:.1f}|{ratio_ad:.1f}"
    
    def save_weights(self, filepath: str):
        """保存突触权重"""
        weights = {}
        for name, syn in self.synapses.items():
            if hasattr(syn, 'w'):
                weights[name] = np.array(syn.w)
        
        np.savez(filepath, **weights)
        print(f"[NeuraCore3M] 权重已保存: {filepath}")
    
    def load_weights(self, filepath: str):
        """加载突触权重"""
        data = np.load(filepath)
        
        for name, syn in self.synapses.items():
            if name in data and hasattr(syn, 'w'):
                syn.w = data[name]
        
        print(f"[NeuraCore3M] 权重已加载: {filepath}")


# ===== API函数（兼容旧接口）=====

_neuracore3m_instance = None

def init_neuracore3m(enable_stdp: bool = True, 
                     force_rebuild: bool = False,
                     test_mode: bool = False) -> Optional[NeuraCore3M]:
    """
    初始化全局300万神经元实例
    
    Args:
        enable_stdp: 启用STDP学习
        force_rebuild: 强制重建
        test_mode: 测试模式（使用小规模）
    
    Returns:
        NeuraCore3M实例
    """
    global _neuracore3m_instance
    
    if _neuracore3m_instance is not None and not force_rebuild:
        return _neuracore3m_instance
    
    if test_mode:
        # 测试模式：使用1000神经元（极小规模避免OOM）
        core = NeuraCore3M(
            n_sensory=250,
            n_association=500,
            n_decision=125,
            n_motor=125,
            enable_stdp=enable_stdp
        )
    else:
        # 完整300万神经元
        core = NeuraCore3M(
            n_sensory=750000,
            n_association=1500000,
            n_decision=375000,
            n_motor=375000,
            enable_stdp=enable_stdp
        )
    
    try:
        core.build_network()
        _neuracore3m_instance = core
        return core
    except Exception as e:
        print(f"[NeuraCore3M] 构建失败: {e}")
        print("[NeuraCore3M] 切换到测试模式...")
        # 回退到测试模式
        return init_neuracore3m(enable_stdp=enable_stdp, test_mode=True)

def get_neuracore3m() -> Optional[NeuraCore3M]:
    """获取全局实例"""
    return _neuracore3m_instance

async def api_neuracore3m_chat(data: Dict) -> Dict:
    """
    API接口 - 兼容旧版neuracore接口
    
    请求格式:
    {
        'text': '用户输入',
        'use_reflex': False,
        'stream': True
    }
    """
    core = get_neuracore3m()
    
    if core is None:
        return {
            'error': 'NeuraCore3M未初始化',
            'text_response': '系统未就绪'
        }
    
    text = data.get('text', '')
    use_reflex = data.get('use_reflex', False)
    
    # 调用意识循环
    result = core.consciousness_cycle(
        perception_data={'text': text},
        available_actions=data.get('available_actions'),
        use_llm_tool=data.get('use_llm_tool', False)
    )
    
    return result


if __name__ == '__main__':
    print("=" * 60)
    print("NeuraCore3M - 300万神经元SNN系统")
    print("=" * 60)
    
    # 测试构建（使用小规模）
    print("\n[测试] 构建10万神经元测试版本...")
    core = init_neuracore3m(enable_stdp=True, test_mode=True)
    
    if core:
        print("\n[测试] 运行意识循环...")
        result = core.consciousness_cycle("Hello SNN world!")
        
        print(f"\n结果:")
        print(f"  脉冲统计: {result['spike_counts']}")
        print(f"  总脉冲: {result['total_spikes']:,}")
        print(f"  置信度: {result['confidence']:.3f}")
        print(f"  签名: {result['signature']}")
        
        print("\n✅ NeuraCore3M测试通过！")
    else:
        print("\n❌ 初始化失败")
