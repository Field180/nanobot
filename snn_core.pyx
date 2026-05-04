#cython: language=c++
#cython: boundscheck=False
#cython: wraparound=False
#cython: initializedcheck=False
#cython: cdivision=True
"""
SNN Core - C++加速的稀疏脉冲神经网络
"""

import numpy as np
cimport numpy as np
cimport cython
from libc.math cimport fabsf
from libcpp.vector cimport vector
from libcpp cimport bool

cdef extern from "stdlib.h":
    int rand()
    double RAND_MAX

cdef class FastSparseSNNCore:
    cdef int n_neurons
    cdef int n_steps
    cdef float dt
    cdef float v_rest, v_th, v_reset, tau_m
    
    # 用于兼容Python的属性
    cdef public object config
    cdef public str name
    
    cdef vector[float] V
    cdef vector[float] refractory
    cdef vector[int] spike_counts
    cdef vector[float] W_data
    cdef vector[int] W_indices
    cdef vector[int] W_indptr
    
    def __init__(self, int n_neurons=6000000, int n_steps=100, float dt=0.1):
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
        self.config = None  # 用于兼容
        self.name = "cpp_snn"
        self.reset()
        print(f"C++ SNN: {n_neurons:,}神经元")
    
    def build_sparse_weights(self, int connections_per_neuron=5):
        cdef int n = self.n_neurons
        cdef int total_conn = n * connections_per_neuron
        print(f"构建权重: {total_conn:,}连接...")
        
        self.W_data.reserve(total_conn)
        self.W_indices.reserve(total_conn)
        self.W_indptr.resize(n + 1)
        
        cdef int i, j, current_ptr = 0
        self.W_indptr[0] = 0
        
        for i in range(n):
            for _ in range(connections_per_neuron):
                j = (i + 1 + (rand() % (n - 1))) % n
                self.W_data.push_back((<float>(rand()) / RAND_MAX - 0.5) * 0.2)
                self.W_indices.push_back(j)
                current_ptr += 1
            self.W_indptr[i + 1] = current_ptr
        print(f"完成")
    
    cpdef reset(self):
        cdef int i
        for i in range(self.n_neurons):
            self.V[i] = self.v_rest
            self.refractory[i] = 0.0
            self.spike_counts[i] = 0
    
    cpdef dict process(self, object input_data):
        import time
        start_time = time.time()
        
        cdef float[:] input_array
        if isinstance(input_data, str):
            np.random.seed(hash(input_data) % 2**32)
            input_np = np.zeros(self.n_neurons, dtype=np.float32)
            n_input = min(len(input_data) * 3, 100)
            input_np[:n_input] = np.random.randn(n_input) * 0.1
            input_array = input_np
        else:
            input_np = np.array(input_data, dtype=np.float32)
            if len(input_np) < self.n_neurons:
                padded = np.zeros(self.n_neurons, dtype=np.float32)
                padded[:len(input_np)] = input_np
                input_array = padded
            else:
                input_array = input_np
        
        self.reset()
        
        cdef float[:] I_total = np.zeros(self.n_neurons, dtype=np.float32)
        cdef float[:] synaptic = np.zeros(self.n_neurons, dtype=np.float32)
        cdef bool[:] spike_mask = np.zeros(self.n_neurons, dtype=np.bool_)
        cdef bool[:] active_mask = np.zeros(self.n_neurons, dtype=np.bool_)
        
        cdef int step, i, j_idx, start_idx, end_idx
        cdef float v, dv, sum_val
        cdef float p_rest = self.v_rest
        cdef float p_th = self.v_th
        cdef float p_reset = self.v_reset
        cdef float p_tau = self.tau_m
        cdef float p_dt = self.dt
        
        for step in range(self.n_steps):
            for i in range(self.n_neurons):
                spike_mask[i] = self.V[i] >= p_th
            
            for i in range(self.n_neurons):
                sum_val = 0.0
                start_idx = self.W_indptr[i]
                end_idx = self.W_indptr[i + 1]
                for j_idx in range(start_idx, end_idx):
                    if spike_mask[self.W_indices[j_idx]]:
                        sum_val += self.W_data[j_idx]
                synaptic[i] = sum_val
            
            for i in range(self.n_neurons):
                I_total[i] = input_array[i] + synaptic[i]
            
            for i in range(self.n_neurons):
                active_mask[i] = (fabsf(I_total[i]) > 0.01) or (self.V[i] > p_th - 10.0)
            
            for i in range(self.n_neurons):
                if not active_mask[i]:
                    continue
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
        
        elapsed_ms = (time.time() - start_time) * 1000
        total_spikes = 0
        for i in range(self.n_neurons):
            total_spikes += self.spike_counts[i]
        
        return {
            'simulation_time_ms': elapsed_ms,
            'total_spikes': total_spikes
        }


def benchmark_cpp_core():
    print("\n" + "="*60)
    print("C++ SNN基准测试")
    print("="*60)
    
    snn = FastSparseSNNCore(n_neurons=6000000, n_steps=100, dt=0.1)
    snn.build_sparse_weights(connections_per_neuron=5)
    
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
    print(f"平均耗时: {avg_time:.1f}ms")
    print(f"平均加速: {avg_speedup:.1f}x")
    print(f"目标<100ms: {'✅' if avg_time < 100 else '❌'}")
    return avg_time < 100
