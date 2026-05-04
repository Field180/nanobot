#cython: language=c++
#cython: boundscheck=False
#cython: wraparound=False
#cython: initializedcheck=False
#cython: cdivision=True
"""
SNN Core - 极速版本
优化：10步仿真 + OpenMP并行
"""

import numpy as np
cimport numpy as np
cimport cython
from libc.math cimport fabsf
from libcpp.vector cimport vector
from libcpp cimport bool
from cython.parallel cimport prange

cdef extern from "stdlib.h":
    int rand()
    double RAND_MAX

cdef class FastSNNCore:
    cdef int n_neurons
    cdef int n_steps
    cdef float dt, v_rest, v_th, v_reset, tau_m
    cdef vector[float] V, refractory, W_data
    cdef vector[int] spike_counts, W_indices, W_indptr
    
    def __init__(self, int n_neurons=6000000, int n_steps=10, float dt=1.0):
        self.n_neurons = n_neurons
        self.n_steps = n_steps
        self.dt = dt
        self.v_rest = -70.0
        self.v_th = -50.0
        self.v_reset = -65.0
        self.tau_m = 20.0
        
        self.V.resize(n_neurons)
        self.refractory.resize(n_neurons)
        self.spike_counts.resize(n_neurons)
        self.reset()
        print(f"🚀 极速SNN: {n_neurons:,}神经元 x {n_steps}步")
    
    def build_weights(self, int conn_per_neuron=1):
        cdef int n = self.n_neurons
        cdef int total = n * conn_per_neuron
        print(f"构建极简权重: {total:,}连接...")
        
        self.W_data.reserve(total)
        self.W_indices.reserve(total)
        self.W_indptr.resize(n + 1)
        
        cdef int i, j, ptr = 0
        self.W_indptr[0] = 0
        
        for i in range(n):
            for _ in range(conn_per_neuron):
                j = (i + 1) % n
                self.W_data.push_back(0.1)
                self.W_indices.push_back(j)
                ptr += 1
            self.W_indptr[i + 1] = ptr
        print(f"✅ 完成")
    
    cpdef reset(self):
        cdef int i
        for i in prange(self.n_neurons, nogil=True):
            self.V[i] = self.v_rest
            self.refractory[i] = 0.0
            self.spike_counts[i] = 0
    
    cpdef dict process(self, object input_data):
        import time
        start = time.time()
        
        # 准备输入
        cdef float[:] inp = np.zeros(self.n_neurons, dtype=np.float32)
        if isinstance(input_data, str):
            np.random.seed(hash(input_data) % 2**32)
            n_in = min(len(input_data) * 3, 100)
            for i in range(n_in):
                inp[i] = <float>np.random.randn() * 0.1
        
        self.reset()
        
        cdef float[:] I_total = np.zeros(self.n_neurons, dtype=np.float32)
        cdef float[:] synaptic = np.zeros(self.n_neurons, dtype=np.float32)
        
        cdef int step, i, j_idx, start_idx, end_idx
        cdef float v, dv, total
        cdef float p_rest = self.v_rest
        cdef float p_th = self.v_th
        cdef float p_reset = self.v_reset
        cdef float p_tau = self.tau_m
        cdef float p_dt = self.dt
        
        # 仿真 - 并行版本
        for step in range(self.n_steps):
            # 稀疏乘法（并行）
            for i in prange(self.n_neurons, nogil=True):
                total = 0.0
                start_idx = self.W_indptr[i]
                end_idx = self.W_indptr[i + 1]
                for j_idx in range(start_idx, end_idx):
                    if self.V[self.W_indices[j_idx]] >= p_th:
                        total += self.W_data[j_idx]
                synaptic[i] = total
            
            # 合并输入
            for i in prange(self.n_neurons, nogil=True):
                I_total[i] = inp[i] + synaptic[i]
            
            # LIF更新（并行）
            for i in prange(self.n_neurons, nogil=True):
                v = self.V[i]
                if self.refractory[i] > 0:
                    self.V[i] = p_reset
                    self.refractory[i] -= p_dt
                else:
                    dv = (-(v - p_rest) + I_total[i]) / p_tau
                    v += dv * p_dt
                    if v >= p_th:
                        self.V[i] = p_reset
                        self.refractory[i] = 2.0
                        self.spike_counts[i] += 1
                    else:
                        self.V[i] = v
        
        elapsed = (time.time() - start) * 1000
        total_spikes = sum(self.spike_counts)
        
        return {'simulation_time_ms': elapsed, 'total_spikes': total_spikes}


def benchmark():
    print("\n" + "="*60)
    print("⚡ 极速C++ SNN测试 (10步并行)")
    print("="*60)
    
    snn = FastSNNCore(n_neurons=6000000, n_steps=10, dt=1.0)
    snn.build_weights(conn_per_neuron=1)
    
    queries = ["2+2=?", "量子纠缠", "相对论"]
    results = []
    
    for q in queries:
        print(f"\n测试: '{q}'")
        times = []
        for _ in range(3):
            r = snn.process(q)
            times.append(r['simulation_time_ms'])
        avg = sum(times) / len(times)
        speedup = 1600 / avg
        results.append((q, avg, speedup))
        print(f"  {avg:.1f}ms (加速{speedup:.1f}x)")
    
    avg_time = sum([r[1] for r in results]) / len(results)
    avg_speedup = sum([r[2] for r in results]) / len(results)
    
    print("\n" + "="*60)
    print(f"平均: {avg_time:.1f}ms | 加速: {avg_speedup:.1f}x")
    print(f"<100ms: {'✅' if avg_time < 100 else '❌'}")
    return avg_time < 100
