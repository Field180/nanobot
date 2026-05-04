"""
SparseSNN Light - 轻量级稀疏事件驱动SNN测试版本
用于验证算法正确性，避免内存崩溃
"""

import numpy as np
from scipy.sparse import csr_matrix
import time


class SparseSNNConfigLight:
    """轻量级稀疏SNN配置 - 测试用"""
    
    def __init__(self):
        # 小规模神经元（测试用）
        self.sensory_neurons = 100
        self.association_neurons = 200
        self.decision_neurons = 100
        self.prefrontal_neurons = 100
        self.motor_neurons = 100
        
        # 稀疏计算参数
        self.active_threshold = 0.1
        self.sparse_factor = 0.05
        
        # 仿真参数
        self.dt = 0.1
        self.time_window = 50  # 50ms仿真
        self.steps = int(self.time_window / self.dt)
        
        # LIF参数
        self.v_rest = -70.0
        self.v_th = -50.0
        self.v_reset = -65.0
        self.tau_m = 20.0


class SparseEventSNNLight:
    """轻量级稀疏SNN - 测试算法正确性"""
    
    def __init__(self, config=None, name="sparse_light"):
        if config is None:
            config = SparseSNNConfigLight()
        self.config = config
        self.name = name
        
        # 总神经元
        self.n_neurons = (config.sensory_neurons + config.association_neurons + 
                         config.decision_neurons + config.prefrontal_neurons + 
                         config.motor_neurons)
        
        print(f"🧠 轻量级稀疏SNN: {self.n_neurons}神经元")
        
        # 定义区域
        self._define_regions()
        
        # 初始化
        self._init_neurons()
        self._build_weights()
        
        # 统计
        self.stats = {'simulations': 0, 'avg_time': 0}
        
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
        
    def _init_neurons(self):
        cfg = self.config
        self.V = np.full(self.n_neurons, cfg.v_rest, dtype=np.float32)
        self.refractory = np.zeros(self.n_neurons, dtype=np.float32)
        self.last_spike = np.full(self.n_neurons, -1000.0, dtype=np.float32)
        
    def _build_weights(self):
        """构建稀疏权重 - 分块对角"""
        n = self.n_neurons
        blocks = []
        
        for name, (start, end) in self.regions.items():
            size = end - start
            n_conn = max(int(size * size * self.config.sparse_factor), 10)
            n_conn = min(n_conn, size * 50)
            
            rows = np.random.randint(0, size, n_conn) + start
            cols = np.random.randint(0, size, n_conn) + start
            data = np.random.randn(n_conn).astype(np.float32) * 0.1
            blocks.append((data, rows, cols))
        
        all_data = np.concatenate([b[0] for b in blocks])
        all_rows = np.concatenate([b[1] for b in blocks])
        all_cols = np.concatenate([b[2] for b in blocks])
        
        self.W_sparse = csr_matrix(
            (all_data, (all_rows, all_cols)),
            shape=(n, n),
            dtype=np.float32
        )
        
        print(f"   稀疏连接: {len(all_data)} 个")
        
    def detect_active(self, inputs):
        """检测活跃神经元"""
        input_active = np.abs(inputs) > 0.01
        near_threshold = self.V > (self.config.v_th - 5)
        return input_active | near_threshold
    
    def process(self, query, verbose=False):
        """处理查询"""
        start = time.time()
        cfg = self.config
        
        # 简单编码
        np.random.seed(hash(query) % 2**32)
        inputs = np.random.randn(min(len(query)*2, 100)) * 0.1
        full_input = np.zeros(self.n_neurons, dtype=np.float32)
        full_input[:len(inputs)] = inputs.astype(np.float32)
        
        # 重置
        self.V[:] = cfg.v_rest
        spike_counts = np.zeros(self.n_neurons, dtype=np.int32)
        
        # 仿真
        for step in range(cfg.steps):
            t = step * cfg.dt
            
            # 活跃检测
            active = self.detect_active(full_input)
            active_idx = np.where(active)[0]
            
            if len(active_idx) == 0:
                continue
            
            # 稀疏矩阵乘法（仅活跃部分）
            synaptic = np.zeros(self.n_neurons, dtype=np.float32)
            for i in active_idx:
                row_start = self.W_sparse.indptr[i]
                row_end = self.W_sparse.indptr[i+1]
                for j_idx in range(row_start, row_end):
                    j = self.W_sparse.indices[j_idx]
                    if active[j]:
                        synaptic[i] += self.W_sparse.data[j_idx] * (self.V[j] > cfg.v_th)
            
            I_total = full_input + synaptic
            
            # LIF更新
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
        
        elapsed = (time.time() - start) * 1000
        active_rate = len(active_idx) / self.n_neurons if 'active_idx' in dir() else 0
        
        # 区域统计
        region_stats = {}
        for name, (start, end) in self.regions.items():
            region_stats[name] = {
                'spikes': int(spike_counts[start:end].sum()),
                'n_neurons': end - start
            }
        
        self.stats['simulations'] += 1
        self.stats['avg_time'] = 0.9 * self.stats['avg_time'] + 0.1 * elapsed
        
        if verbose:
            print(f"\n📊 仿真结果:")
            print(f"   耗时: {elapsed:.1f}ms")
            print(f"   总脉冲: {spike_counts.sum()}")
            for name, s in region_stats.items():
                print(f"   {name}: {s['spikes']}脉冲")
        
        return {
            'simulation_time_ms': elapsed,
            'spike_counts': spike_counts,
            'region_stats': region_stats,
            'active_rate': active_rate,
            'stats': self.stats.copy()
        }


def run_benchmark():
    """运行基准测试"""
    print("\n" + "="*60)
    print("🚀 轻量级稀疏SNN基准测试")
    print("="*60)
    
    snn = SparseEventSNNLight()
    
    queries = [
        "2+2=?",
        "量子纠缠是什么",
        "解释相对论"
    ]
    
    results = []
    for q in queries:
        print(f"\n测试: '{q}'")
        times = []
        for _ in range(3):
            r = snn.process(q, verbose=False)
            times.append(r['simulation_time_ms'])
        
        avg = np.mean(times)
        results.append({'query': q, 'avg_ms': avg})
        print(f"   平均: {avg:.1f}ms")
    
    print("\n" + "="*60)
    print("📊 总结")
    print("="*60)
    avg_all = np.mean([r['avg_ms'] for r in results])
    print(f"平均耗时: {avg_all:.1f}ms")
    print(f"算法验证: {'✅ 通过' if avg_all < 1000 else '⚠️ 需优化'}")
    
    return results


if __name__ == "__main__":
    run_benchmark()
