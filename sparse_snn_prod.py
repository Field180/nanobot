"""
SparseSNN Production - 600万神经元生产版本
分块处理策略，避免内存崩溃
"""

import numpy as np
from scipy.sparse import csr_matrix
import time
from typing import Dict, List, Tuple
import gc


class SparseSNNConfig:
    """生产级600万神经元配置"""
    
    def __init__(self, lite_mode=False):
        if lite_mode:
            # 测试模式
            self.sensory_neurons = 100
            self.association_neurons = 200
            self.decision_neurons = 100
            self.prefrontal_neurons = 100
            self.motor_neurons = 100
        else:
            # 生产模式
            self.sensory_neurons = 900_000
            self.association_neurons = 1_800_000
            self.decision_neurons = 1_500_000
            self.prefrontal_neurons = 900_000
            self.motor_neurons = 900_000
        
        self.active_threshold = 0.05
        self.sparse_factor = 0.02
        
        self.dt = 0.1
        self.time_window = 100
        self.steps = int(self.time_window / self.dt)
        
        self.v_rest = -70.0
        self.v_th = -50.0
        self.v_reset = -65.0
        self.tau_m = 20.0


class SparseEventSNN:
    """
    生产级稀疏事件驱动SNN
    600万神经元，目标<100ms
    使用分块策略避免内存爆炸
    """
    
    def __init__(self, config: SparseSNNConfig, name="sparse_prod"):
        self.config = config
        self.name = name
        
        self.n_neurons = (config.sensory_neurons + config.association_neurons + 
                         config.decision_neurons + config.prefrontal_neurons + 
                         config.motor_neurons)
        
        print(f"\n{'='*60}")
        print(f"🧠 稀疏事件驱动SNN初始化")
        print(f"{'='*60}")
        print(f"总神经元: {self.n_neurons:,}")
        print(f"稀疏因子: {config.sparse_factor*100}%")
        print(f"仿真步数: {config.steps}")
        
        # 定义区域
        self._define_regions()
        
        # 初始化（分块）
        self._init_neurons_chunked()
        self._build_weights_chunked()
        
        # 性能统计
        self.stats = {
            'simulations': 0,
            'avg_time_ms': 0,
            'avg_active_rate': 0
        }
        
        print(f"✅ 初始化完成，预期加速10-20x")
        
    def _define_regions(self):
        s = self.config.sensory_neurons
        a = self.config.association_neurons
        d = self.config.decision_neurons
        p = self.config.prefrontal_neurons
        
        self.regions = {
            'sensory': (0, s),
            'association': (s, s+a),
            'decision': (s+a, s+a+d),
            'prefrontal': (s+a+d, s+a+d+p),
            'motor': (s+a+d+p, self.n_neurons)
        }
        
        self.region_names = list(self.regions.keys())
        self.n_regions = len(self.region_names)
        
    def _init_neurons_chunked(self):
        """分块初始化神经元"""
        cfg = self.config
        
        # 使用float32减少内存
        self.V = np.full(self.n_neurons, cfg.v_rest, dtype=np.float32)
        self.refractory = np.zeros(self.n_neurons, dtype=np.float32)
        self.last_spike = np.full(self.n_neurons, -1000.0, dtype=np.float32)
        self.is_excitatory = np.random.random(self.n_neurons) < 0.8
        
    def _build_weights_chunked(self):
        """分块构建稀疏权重 - 避免内存爆炸"""
        print("构建分块稀疏权重矩阵...")
        
        self.region_weights = {}  # 每个区域独立的CSR矩阵
        total_connections = 0
        
        for name, (start, end) in self.regions.items():
            size = end - start
            # 区域内部稀疏连接
            n_conn = int(size * size * self.config.sparse_factor)
            n_conn = min(n_conn, size * 20)  # 上限：每个神经元20连接（降低内存）
            n_conn = max(n_conn, 50)  # 下限
            
            # 生成连接
            rows = np.random.randint(0, size, n_conn)
            cols = np.random.randint(0, size, n_conn)
            data = np.random.randn(n_conn).astype(np.float32) * 0.1
            
            # 创建区域内部权重矩阵
            W_local = csr_matrix(
                (data, (rows, cols)),
                shape=(size, size),
                dtype=np.float32
            )
            
            self.region_weights[name] = W_local
            total_connections += n_conn
            
            print(f"   {name}: {size:,}神经元, {n_conn:,}连接")
        
        # 区域间连接（简化：仅相邻区域）
        self.inter_weights = {}
        for i in range(len(self.region_names) - 1):
            name1 = self.region_names[i]
            name2 = self.region_names[i + 1]
            
            start1, end1 = self.regions[name1]
            start2, end2 = self.regions[name2]
            size1 = end1 - start1
            size2 = end2 - start2
            
            # 区域间稀疏连接
            n_inter = min(int(size1 * size2 * 0.001), 10000)
            rows = np.random.randint(0, size1, n_inter)
            cols = np.random.randint(0, size2, n_inter)
            data = np.random.randn(n_inter).astype(np.float32) * 0.05
            
            self.inter_weights[(name1, name2)] = csr_matrix(
                (data, (rows, cols)),
                shape=(size1, size2),
                dtype=np.float32
            )
        
        print(f"✅ 权重构建完成: {total_connections:,}总连接")
        
    def detect_active(self, inputs: np.ndarray) -> np.ndarray:
        """检测活跃神经元（向量化）"""
        cfg = self.config
        
        # 输入驱动
        input_active = np.abs(inputs) > 0.01
        
        # 膜电位接近阈值
        near_th = self.V > (cfg.v_th - 5.0)
        
        # 最近发放（50ms内）
        current_t = time.time()
        recent = (current_t - self.last_spike) < 0.05
        
        active = input_active | near_th | recent
        
        # 限制最大活跃率
        active_idx = np.where(active)[0]
        max_active = int(self.n_neurons * 0.1)
        
        if len(active_idx) > max_active:
            # 选择膜电位最接近阈值的
            priorities = np.abs(self.V[active_idx] - cfg.v_th)
            top_k = np.argpartition(priorities, -max_active)[-max_active:]
            selected = active_idx[top_k]
            
            active.fill(False)
            active[selected] = True
        
        return active
    
    def _compute_synaptic_input(self, active: np.ndarray) -> np.ndarray:
        """计算突触输入（分块计算）"""
        synaptic = np.zeros(self.n_neurons, dtype=np.float32)
        spike_mask = (self.V > self.config.v_th).astype(np.float32)
        
        # 区域内部连接
        for name, (start, end) in self.regions.items():
            W = self.region_weights[name]
            region_spikes = spike_mask[start:end]
            
            # 稀疏矩阵乘法
            if np.any(region_spikes):
                output = W.dot(region_spikes)
                synaptic[start:end] += output
        
        # 区域间连接（前向传递）
        for (name1, name2), W_inter in self.inter_weights.items():
            start1, end1 = self.regions[name1]
            start2, end2 = self.regions[name2]
            
            spikes1 = spike_mask[start1:end1]
            if np.any(spikes1):
                output = W_inter.dot(spikes1)
                synaptic[start2:end2] += output
        
        return synaptic
    
    def process(self, input_data, verbose=False) -> Dict:
        """
        处理输入
        目标性能：<100ms（对比原1.6秒=1600ms）
        """
        start_time = time.time()
        cfg = self.config
        
        # 准备输入
        if isinstance(input_data, str):
            np.random.seed(hash(input_data) % 2**32)
            inputs_small = np.random.randn(min(len(input_data) * 10, 1000)) * 0.1
            inputs = np.zeros(self.n_neurons, dtype=np.float32)
            inputs[:len(inputs_small)] = inputs_small.astype(np.float32)
        else:
            inputs = np.array(input_data, dtype=np.float32)
            if len(inputs) < self.n_neurons:
                padded = np.zeros(self.n_neurons, dtype=np.float32)
                padded[:len(inputs)] = inputs
                inputs = padded
        
        # 重置状态
        self.V[:] = cfg.v_rest
        self.refractory[:] = 0
        
        spike_counts = np.zeros(self.n_neurons, dtype=np.int32)
        
        # 仿真循环
        for step in range(cfg.steps):
            t = step * cfg.dt
            
            # 检测活跃神经元
            active = self.detect_active(inputs)
            active_rate = np.mean(active)
            
            # 计算突触输入
            synaptic = self._compute_synaptic_input(active)
            I_total = inputs + synaptic
            
            # LIF更新（仅活跃神经元）
            active_idx = np.where(active)[0]
            
            for i in active_idx:
                if self.refractory[i] > 0:
                    self.V[i] = cfg.v_reset
                    self.refractory[i] -= cfg.dt
                else:
                    dv = (-(self.V[i] - cfg.v_rest) + I_total[i]) / cfg.tau_m
                    self.V[i] += dv * cfg.dt
                    
                    if self.V[i] >= cfg.v_th:
                        spike_counts[i] += 1
                        self.V[i] = cfg.v_reset
                        self.refractory[i] = 2.0
                        self.last_spike[i] = t
        
        elapsed_ms = (time.time() - start_time) * 1000
        
        # 区域统计
        region_stats = {}
        for name, (start, end) in self.regions.items():
            region_spikes = int(spike_counts[start:end].sum())
            region_stats[name] = {
                'spikes': region_spikes,
                'rate': region_spikes / (end - start) / cfg.steps * 1000
            }
        
        # 更新统计
        self.stats['simulations'] += 1
        self.stats['avg_time_ms'] = 0.9 * self.stats['avg_time_ms'] + 0.1 * elapsed_ms
        self.stats['avg_active_rate'] = 0.9 * self.stats['avg_active_rate'] + 0.1 * active_rate
        
        if verbose:
            print(f"\n📊 仿真统计:")
            print(f"   耗时: {elapsed_ms:.1f}ms")
            print(f"   活跃率: {active_rate*100:.2f}%")
            print(f"   总脉冲: {spike_counts.sum():,}")
            for name, s in region_stats.items():
                print(f"   {name}: {s['spikes']:,}脉冲 ({s['rate']:.1f}Hz)")
        
        return {
            'simulation_time_ms': elapsed_ms,
            'spike_counts': spike_counts,
            'region_stats': region_stats,
            'active_rate': active_rate,
            'total_spikes': int(spike_counts.sum()),
            'stats': self.stats.copy()
        }


class SparseSNNWrapper:
    """API兼容包装类"""
    
    def __init__(self, config=None, name="sparse_wrapper", lite_mode=False):
        if config is None:
            config = SparseSNNConfig(lite_mode=lite_mode)
        self.snn = SparseEventSNN(config, name)
        self.config = config
        
    def build(self, seed=42):
        np.random.seed(seed)
        return self
    
    def process(self, input_data, verbose=False):
        return self.snn.process(input_data, verbose)


def run_production_benchmark():
    """生产级基准测试"""
    print("\n" + "="*60)
    print("🚀 生产级稀疏SNN基准测试 (600万神经元)")
    print("="*60)
    
    # 使用测试模式避免内存问题
    config = SparseSNNConfig(lite_mode=False)
    snn = SparseEventSNN(config)
    
    queries = [
        "2+2=?",
        "量子纠缠是什么",
        "解释相对论与量子力学的矛盾"
    ]
    
    results = []
    for query in queries:
        print(f"\n测试: '{query}'")
        
        # 多次运行
        times = []
        for _ in range(3):
            r = snn.process(query, verbose=False)
            times.append(r['simulation_time_ms'])
        
        avg_time = np.mean(times)
        speedup = 1600 / avg_time if avg_time > 0 else 0
        
        results.append({
            'query': query,
            'avg_ms': avg_time,
            'speedup': speedup
        })
        
        print(f"   平均耗时: {avg_time:.1f}ms")
        print(f"   加速比: {speedup:.1f}x")
    
    print("\n" + "="*60)
    print("📊 总结")
    print("="*60)
    avg_time = np.mean([r['avg_ms'] for r in results])
    avg_speedup = np.mean([r['speedup'] for r in results])
    
    print(f"平均耗时: {avg_time:.1f}ms")
    print(f"平均加速比: {avg_speedup:.1f}x")
    print(f"目标100ms: {'✅ 达成' if avg_time < 100 else '❌ 未达成'}")
    print(f"目标10x加速: {'✅ 达成' if avg_speedup > 10 else '❌ 未达成'}")
    
    return results


if __name__ == "__main__":
    run_production_benchmark()
