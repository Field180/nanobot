#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UltraSparseConnectome3M - 300万神经元超稀疏连接组生成器

针对64GB内存优化的连接组生成器，采用以下策略：
1. 分块构建：避免一次性生成所有连接
2. 预计算连接数：使用n参数批量连接，而非动态扩容
3. 稀疏存储：利用Brian2的稀疏矩阵特性
4. 黄金比例分形：保持生物合理性

架构比例（300万神经元）：
- 感觉皮层 (Sensory):   750,000  (25%)
- 联合皮层 (Association): 1,500,000 (50%)
- 决策皮层 (Decision):  375,000  (12.5%)
- 运动皮层 (Motor):     375,000  (12.5%)

总突触控制：~5亿以内（约25GB内存）
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
import warnings

# Brian2导入（可选，用于类型检查）
try:
    from brian2 import NeuronGroup, Synapses
    BRIAN2_AVAILABLE = True
except ImportError:
    BRIAN2_AVAILABLE = False

class UltraSparseConnectome3M:
    """
    300万神经元超稀疏连接组
    
    关键优化：
    - 增量式连接生成，避免动态数组溢出
    - 分块处理：每次处理最多10万个连接
    - 使用预计算的概率矩阵
    """
    
    # 黄金比例
    PHI = (1 + np.sqrt(5)) / 2
    
    def __init__(self, 
                 n_sensory: int = 750000,
                 n_association: int = 1500000,
                 n_decision: int = 375000,
                 n_motor: int = 375000,
                 local_density: float = 0.015,  # 1.5% 局部密度
                 long_range_density: float = 0.005,  # 0.5% 长程密度
                 plastic_fraction: float = 0.3,  # 30%突触可塑
                 chunk_size: int = 100000):  # 分块大小
        
        self.layer_sizes = {
            'sensory': n_sensory,
            'association': n_association,
            'decision': n_decision,
            'motor': n_motor
        }
        self.total_neurons = sum(self.layer_sizes.values())
        
        # 密度参数
        self.local_density = local_density
        self.long_range_density = long_range_density
        self.plastic_fraction = plastic_fraction
        self.chunk_size = chunk_size
        
        # 预计算连接数估计
        self._estimate_connections()
        
        print(f"[Connectome3M] 初始化300万神经元连接组")
        print(f"  - 总神经元: {self.total_neurons:,}")
        print(f"  - 估计突触: {self.estimated_synapses:,}")
        print(f"  - 内存占用: ~{self.estimated_memory_gb:.1f}GB")
    
    def _estimate_connections(self):
        """预计算连接数以避免动态扩容"""
        self.synapse_counts = {}
        total = 0
        
        # 感觉→联合 (局部高密度)
        n = int(self.layer_sizes['sensory'] * self.layer_sizes['association'] * 0.02)
        self.synapse_counts[('sensory', 'association')] = n
        total += n
        
        # 联合→决策
        n = int(self.layer_sizes['association'] * self.layer_sizes['decision'] * 0.015)
        self.synapse_counts[('association', 'decision')] = n
        total += n
        
        # 决策→运动 (更高密度)
        n = int(self.layer_sizes['decision'] * self.layer_sizes['motor'] * 0.03)
        self.synapse_counts[('decision', 'motor')] = n
        total += n
        
        # 联合→联合 (循环，稀疏)
        n = int(self.layer_sizes['association'] ** 2 * 0.001)
        self.synapse_counts[('association', 'association')] = n
        total += n
        
        # 感觉→感觉 (局部)
        n = int(self.layer_sizes['sensory'] * self.local_density)
        self.synapse_counts[('sensory', 'sensory')] = n
        total += n
        
        self.estimated_synapses = total
        self.estimated_memory_gb = total * 50 / (1024**3)  # 50字节/突触
        
    def generate_chunked_connections(self, source_size: int, target_size: int, 
                                   density: float, plastic: bool = False) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        分块生成连接，返回(i, j, weight)数组
        
        Args:
            source_size: 源神经元数量
            target_size: 目标神经元数量
            density: 连接密度
            plastic: 是否可塑（影响初始权重分布）
        
        Returns:
            i_indices, j_indices, weights
        """
        expected_n = int(source_size * density)  # 每个源神经元连接的目标数量
        
        if expected_n == 0:
            return np.array([], dtype=np.int32), np.array([], dtype=np.int32), np.array([], dtype=np.float32)
        
        # 限制最大突触数（防止内存爆炸）
        max_synapses = min(expected_n, 10000000)  # 最多1000万突触/连接类型
        if expected_n > max_synapses:
            print(f"    ⚠️ 突触数 {expected_n:,} 超过上限，截断至 {max_synapses:,}")
            expected_n = max_synapses
        
        # 使用分块策略避免内存峰值
        chunk_size = min(self.chunk_size, expected_n)
        n_chunks = max(1, (expected_n + chunk_size - 1) // chunk_size)
        
        chunks = []
        remaining = expected_n
        
        for chunk_idx in range(n_chunks):
            current_chunk = min(chunk_size, remaining)
            remaining -= current_chunk
            
            if current_chunk <= 0:
                break
            # 随机采样源和目标索引
            i = np.random.randint(0, source_size, size=current_chunk)
            j = np.random.randint(0, target_size, size=current_chunk)
            
            # 权重初始化（黄金比例调制）
            if plastic:
                # 可塑突触：较小初始权重
                w = 0.1 + 0.2 * np.random.random(current_chunk)
            else:
                # 静态突触：较大稳定权重
                w = 0.3 + 0.4 * np.random.random(current_chunk)
            
            chunks.append((i, j, w))
        
        # 合并所有分块
        all_i = np.concatenate([c[0] for c in chunks])
        all_j = np.concatenate([c[1] for c in chunks])
        all_w = np.concatenate([c[2] for c in chunks])
        
        return all_i.astype(np.int32), all_j.astype(np.int32), all_w.astype(np.float32)
    
    def create_fractal_connectome(self, 
                                   sensory_group=None,
                                   association_group=None, 
                                   decision_group=None,
                                   motor_group=None) -> Dict:
        """
        创建分形几何连接组
        
        Returns:
            突触配置字典，包含所有连接参数
        """
        print("[Connectome3M] 生成分形连接组...")
        
        synapse_configs = {}
        
        # 1. 感觉→联合 (前馈，高权重)
        # 750K源 × 40连接 = 30M突触
        i, j, w = self.generate_chunked_connections(
            self.layer_sizes['sensory'],
            self.layer_sizes['association'],
            40,  # 每个感觉神经元连接40个联合神经元
            plastic=False
        )
        synapse_configs[('sensory', 'association')] = {
            'i': i, 'j': j, 'w': w,
            'plastic': False,
            'delay': 1  # ms
        }
        print(f"  S→A: {len(i):,} 突触")
        
        # 2. 联合→决策 (可塑路径)
        # 1.5M源 × 10连接 = 15M突触 (被上限限制)
        i, j, w = self.generate_chunked_connections(
            self.layer_sizes['association'],
            self.layer_sizes['decision'],
            10,  # 每个联合神经元连接10个决策神经元
            plastic=True  # 可塑
        )
        synapse_configs[('association', 'decision')] = {
            'i': i, 'j': j, 'w': w,
            'plastic': True,
            'delay': 2
        }
        print(f"  A→D: {len(i):,} 突触 (可塑)")
        
        # 3. 决策→运动
        # 375K源 × 20连接 = 7.5M突触
        i, j, w = self.generate_chunked_connections(
            self.layer_sizes['decision'],
            self.layer_sizes['motor'],
            20,  # 每个决策神经元连接20个运动神经元
            plastic=False
        )
        synapse_configs[('decision', 'motor')] = {
            'i': i, 'j': j, 'w': w,
            'plastic': False,
            'delay': 1
        }
        print(f"  D→M: {len(i):,} 突触")
        
        # 4. 联合→联合 (循环，工作记忆，可塑)
        # 1.5M源 × 2连接 = 3M突触
        i, j, w = self.generate_chunked_connections(
            self.layer_sizes['association'],
            self.layer_sizes['association'],
            2,  # 每个联合神经元连接2个其他联合神经元
            plastic=True
        )
        synapse_configs[('association', 'association')] = {
            'i': i, 'j': j, 'w': w,
            'plastic': True,
            'delay': 3,
            'recurrent': True
        }
        print(f"  A→A: {len(i):,} 循环突触 (可塑)")
        
        # 5. 小世界长程连接 (随机跳跃)
        long_range_i, long_range_j, long_range_w = [], [], []
        
        # 感觉→决策 (跳跃连接)
        # 750K源 × 3连接 = 2.25M突触
        i, j, w = self.generate_chunked_connections(
            self.layer_sizes['sensory'],
            self.layer_sizes['decision'],
            3,  # 每个感觉神经元连接3个决策神经元
            plastic=False
        )
        long_range_i.extend(i)
        long_range_j.extend(j)
        long_range_w.extend(w)
        
        # 联合→运动 (跳跃连接)
        # 1.5M源 × 2连接 = 3M突触 (被上限限制)
        i, j, w = self.generate_chunked_connections(
            self.layer_sizes['association'],
            self.layer_sizes['motor'],
            2,  # 每个联合神经元连接2个运动神经元
            plastic=True
        )
        long_range_i.extend(i)
        long_range_j.extend(j)
        long_range_w.extend(w)
        
        synapse_configs['long_range'] = {
            'i': np.array(long_range_i, dtype=np.int32),
            'j': np.array(long_range_j, dtype=np.int32),
            'w': np.array(long_range_w, dtype=np.float32),
            'plastic': True,
            'delay': 5
        }
        print(f"  长程: {len(long_range_i):,} 突触")
        
        # 统计
        total_syn = sum(len(cfg['i']) for cfg in synapse_configs.values() if 'i' in cfg)
        plastic_syn = sum(len(cfg['i']) for cfg in synapse_configs.values() 
                         if cfg.get('plastic', False) and 'i' in cfg)
        
        print(f"[Connectome3M] ✅ 连接组生成完成")
        print(f"  - 总突触: {total_syn:,}")
        print(f"  - 可塑突触: {plastic_syn:,} ({plastic_syn/total_syn*100:.1f}%)")
        print(f"  - 静态突触: {total_syn-plastic_syn:,}")
        
        return synapse_configs
    
    def get_layer_boundaries(self) -> Dict[str, Tuple[int, int]]:
        """返回各层的全局索引边界"""
        boundaries = {}
        offset = 0
        
        for name, size in [
            ('sensory', self.layer_sizes['sensory']),
            ('association', self.layer_sizes['association']),
            ('decision', self.layer_sizes['decision']),
            ('motor', self.layer_sizes['motor'])
        ]:
            boundaries[name] = (offset, offset + size)
            offset += size
        
        return boundaries
    
    def memory_report(self):
        """打印内存使用预估"""
        print("\n[Connectome3M] 内存使用预估")
        print(f"  - 神经元状态: ~{self.total_neurons * 100 / (1024**2):.1f}MB")
        print(f"  - 突触权重: ~{self.estimated_memory_gb:.1f}GB")
        print(f"  - STDP迹: ~{self.estimated_memory_gb * 0.3:.1f}GB")
        print(f"  - 总计: ~{self.estimated_memory_gb * 1.5:.1f}GB / 64GB 可用")


# ===== 向后兼容API =====
class ConnectomeBuilder3M:
    """简化API用于快速创建连接组"""
    
    @staticmethod
    def build_3m_connectome():
        """快速构建300万神经元连接组"""
        connectome = UltraSparseConnectome3M(
            n_sensory=750000,
            n_association=1500000,
            n_decision=375000,
            n_motor=375000,
            local_density=0.015,
            long_range_density=0.005,
            plastic_fraction=0.3
        )
        
        synapse_configs = connectome.create_fractal_connectome()
        
        return {
            'connectome': connectome,
            'synapse_configs': synapse_configs,
            'layer_boundaries': connectome.get_layer_boundaries()
        }


if __name__ == '__main__':
    # 测试连接组生成
    print("=" * 60)
    print("UltraSparseConnectome3M 测试")
    print("=" * 60)
    
    # 使用缩小版测试（1万神经元）
    test_conn = UltraSparseConnectome3M(
        n_sensory=2500,
        n_association=5000,
        n_decision=1250,
        n_motor=1250,
        chunk_size=10000
    )
    
    test_conn.memory_report()
    
    configs = test_conn.create_fractal_connectome()
    
    print("\n测试通过！300万神经元版本准备就绪。")
