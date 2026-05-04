#!/usr/bin/env python3
"""
容器池性能测试脚本

测试容器池在高并发场景下的表现：
1. 并发获取容器
2. 容器池命中率
3. LRU 清理效率
4. 内存和资源使用

运行方式:
    # 运行所有测试（包括性能测试）
    python -m pytest benchmark_container_pool.py -v
    
    # 只运行性能测试（跳过普通测试）
    python -m pytest benchmark_container_pool.py -v -m perf
    
    # 运行并生成报告
    python benchmark_container_pool.py

依赖:
    pip install pytest-benchmark psutil
"""

import time
import threading
import asyncio
import statistics
from pathlib import Path
from typing import Dict, List, Any
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import pytest

# 尝试导入 psutil（可选）
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    print("⚠️ psutil 未安装，内存监控将不可用")


# ============================================================================
# 性能基线配置（可从环境变量覆盖）
# ============================================================================

import os

def _get_baseline_value(env_key: str, default: float) -> float:
    """从环境变量获取基线值"""
    value = os.environ.get(env_key)
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    return default

PERFORMANCE_BASELINE = {
    'min_throughput': _get_baseline_value('PERF_MIN_THROUGHPUT', 1000),
    'max_avg_latency': _get_baseline_value('PERF_MAX_AVG_LATENCY', 5.0),
    'min_hit_rate': _get_baseline_value('PERF_MIN_HIT_RATE', 0.7),
    'max_p99_latency': _get_baseline_value('PERF_MAX_P99_LATENCY', 50.0),
}

# 基线配置说明
BASELINE_DESCRIPTION = {
    'min_throughput': '最小吞吐量 (req/s)',
    'max_avg_latency': '最大平均延迟 (ms)',
    'min_hit_rate': '最小命中率 (0-1)',
    'max_p99_latency': '最大 P99 延迟 (ms)',
}


@dataclass
class BenchmarkResult:
    """性能测试结果"""
    name: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    throughput_per_sec: float
    pool_hits: int = 0
    pool_misses: int = 0
    hit_rate: float = 0.0
    memory_peak_mb: float = 0.0
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'total_requests': self.total_requests,
            'successful_requests': self.successful_requests,
            'failed_requests': self.failed_requests,
            'avg_latency_ms': round(self.avg_latency_ms, 2),
            'p50_latency_ms': round(self.p50_latency_ms, 2),
            'p95_latency_ms': round(self.p95_latency_ms, 2),
            'p99_latency_ms': round(self.p99_latency_ms, 2),
            'throughput_per_sec': round(self.throughput_per_sec, 2),
            'pool_hits': self.pool_hits,
            'pool_misses': self.pool_misses,
            'hit_rate': round(self.hit_rate, 4),
            'memory_peak_mb': round(self.memory_peak_mb, 2),
            'errors': self.errors[:10],  # 只保留前10个错误
        }


class ContainerPoolBenchmark:
    """容器池性能测试"""
    
    def __init__(self):
        self.results: List[BenchmarkResult] = []
        self.memory_samples: List[float] = []
    
    def _get_memory_mb(self) -> float:
        """获取当前进程内存使用（MB）"""
        if HAS_PSUTIL:
            return psutil.Process().memory_info().rss / (1024 * 1024)
        return 0.0
    
    def _calculate_percentile(self, data: List[float], percentile: float) -> float:
        """计算百分位数"""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = int(len(sorted_data) * percentile / 100)
        return sorted_data[min(index, len(sorted_data) - 1)]
    
    def benchmark_concurrent_access(
        self,
        num_threads: int = 20,
        requests_per_thread: int = 50,
        pool_size: int = 10,
    ) -> BenchmarkResult:
        """
        测试并发访问容器池
        
        Args:
            num_threads: 并发线程数
            requests_per_thread: 每个线程的请求数
            pool_size: 容器池大小
        """
        from sandbox_executor import SandboxConfig, SandboxExecutor
        
        config = SandboxConfig(
            pool_enabled=True,
            pool_size=pool_size,
        )
        
        executor = SandboxExecutor(config)
        executor.docker_available = False  # Mock 模式
        
        latencies: List[float] = []
        errors: List[str] = []
        success_count = 0
        fail_count = 0
        lock = threading.Lock()
        
        def worker(thread_id: int):
            nonlocal success_count, fail_count
            
            for i in range(requests_per_thread):
                start = time.time()
                
                try:
                    session_id = f"session_{thread_id % (pool_size // 2)}"
                    
                    # 模拟获取容器
                    pool_key = executor._get_pool_key(session_id=session_id)
                    
                    with executor._pool_lock:
                        if pool_key in executor._pool:
                            executor._pool[pool_key]['last_used'] = time.time()
                            executor.stats['pool_hits'] += 1
                        else:
                            if len(executor._pool) < config.pool_size:
                                executor._pool[pool_key] = {
                                    'container_id': f'container_{thread_id}_{i}',
                                    'last_used': time.time(),
                                    'session_id': session_id,
                                }
                            executor.stats['pool_misses'] += 1
                    
                    latency = (time.time() - start) * 1000
                    
                    with lock:
                        latencies.append(latency)
                        success_count += 1
                        self.memory_samples.append(self._get_memory_mb())
                
                except Exception as e:
                    with lock:
                        fail_count += 1
                        errors.append(str(e))
        
        # 运行测试
        start_time = time.time()
        
        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        total_time = time.time() - start_time
        
        # 计算结果
        pool_stats = executor.get_pool_stats()
        
        result = BenchmarkResult(
            name=f"concurrent_{num_threads}threads_{requests_per_thread}req",
            total_requests=num_threads * requests_per_thread,
            successful_requests=success_count,
            failed_requests=fail_count,
            avg_latency_ms=statistics.mean(latencies) if latencies else 0,
            p50_latency_ms=self._calculate_percentile(latencies, 50),
            p95_latency_ms=self._calculate_percentile(latencies, 95),
            p99_latency_ms=self._calculate_percentile(latencies, 99),
            throughput_per_sec=success_count / total_time if total_time > 0 else 0,
            pool_hits=pool_stats['pool_hits'],
            pool_misses=pool_stats['pool_misses'],
            hit_rate=pool_stats['hit_rate'],
            memory_peak_mb=max(self.memory_samples) if self.memory_samples else 0,
            errors=errors,
        )
        
        self.results.append(result)
        return result
    
    def benchmark_async_access(
        self,
        num_coroutines: int = 50,
        requests_per_coroutine: int = 100,
        pool_size: int = 10,
    ) -> BenchmarkResult:
        """
        测试异步访问容器池
        
        Args:
            num_coroutines: 协程数量
            requests_per_coroutine: 每个协程的请求数
            pool_size: 容器池大小
        """
        from sandbox_executor import SandboxConfig, SandboxExecutor
        
        config = SandboxConfig(
            pool_enabled=True,
            pool_size=pool_size,
        )
        
        executor = SandboxExecutor(config)
        executor.docker_available = False
        
        latencies: List[float] = []
        errors: List[str] = []
        success_count = 0
        fail_count = 0
        
        async def worker(coroutine_id: int):
            nonlocal success_count, fail_count
            
            for i in range(requests_per_coroutine):
                start = time.time()
                
                try:
                    session_id = f"session_{coroutine_id % (pool_size // 2)}"
                    pool_key = executor._get_pool_key(session_id=session_id)
                    
                    # 使用异步方法
                    container_id = await executor._get_pooled_container_async(pool_key)
                    
                    if not container_id:
                        await executor._add_to_pool_async(
                            pool_key,
                            f'container_{coroutine_id}_{i}',
                            session_id
                        )
                    
                    latency = (time.time() - start) * 1000
                    latencies.append(latency)
                    success_count += 1
                
                except Exception as e:
                    fail_count += 1
                    errors.append(str(e))
        
        # 运行异步测试
        async def run_test():
            start_time = time.time()
            
            tasks = [worker(i) for i in range(num_coroutines)]
            await asyncio.gather(*tasks)
            
            return time.time() - start_time
        
        total_time = asyncio.run(run_test())
        
        pool_stats = executor.get_pool_stats()
        
        result = BenchmarkResult(
            name=f"async_{num_coroutines}coros_{requests_per_coroutine}req",
            total_requests=num_coroutines * requests_per_coroutine,
            successful_requests=success_count,
            failed_requests=fail_count,
            avg_latency_ms=statistics.mean(latencies) if latencies else 0,
            p50_latency_ms=self._calculate_percentile(latencies, 50),
            p95_latency_ms=self._calculate_percentile(latencies, 95),
            p99_latency_ms=self._calculate_percentile(latencies, 99),
            throughput_per_sec=success_count / total_time if total_time > 0 else 0,
            pool_hits=pool_stats['pool_hits'],
            pool_misses=pool_stats['pool_misses'],
            hit_rate=pool_stats['hit_rate'],
            memory_peak_mb=max(self.memory_samples) if self.memory_samples else 0,
            errors=errors,
        )
        
        self.results.append(result)
        return result
    
    def benchmark_lru_eviction(
        self,
        pool_size: int = 5,
        total_containers: int = 20,
    ) -> BenchmarkResult:
        """
        测试 LRU 清理效率
        """
        from sandbox_executor import SandboxConfig, SandboxExecutor
        
        config = SandboxConfig(
            pool_enabled=True,
            pool_size=pool_size,
        )
        
        executor = SandboxExecutor(config)
        executor.docker_available = False
        
        latencies: List[float] = []
        evicted_count = 0
        
        # 添加超过池大小的容器
        for i in range(total_containers):
            start = time.time()
            executor._add_to_pool(f'session_{i}', f'container_{i}', f'session_{i}')
            latencies.append((time.time() - start) * 1000)
        
        # 验证池大小被限制
        final_pool_size = len(executor._pool)
        
        result = BenchmarkResult(
            name=f"lru_eviction_pool{pool_size}_total{total_containers}",
            total_requests=total_containers,
            successful_requests=final_pool_size,
            failed_requests=0,
            avg_latency_ms=statistics.mean(latencies) if latencies else 0,
            p50_latency_ms=self._calculate_percentile(latencies, 50),
            p95_latency_ms=self._calculate_percentile(latencies, 95),
            p99_latency_ms=self._calculate_percentile(latencies, 99),
            throughput_per_sec=total_containers / sum(latencies) * 1000 if latencies else 0,
            pool_hits=executor.stats['pool_hits'],
            pool_misses=executor.stats['pool_misses'],
            hit_rate=pool_size / total_containers,
            errors=[],
        )
        
        self.results.append(result)
        return result
    
    def run_all_benchmarks(self) -> Dict[str, Any]:
        """运行所有性能测试"""
        print("=" * 60)
        print("🚀 容器池性能测试")
        print("=" * 60)
        
        # 测试 1: 低并发
        print("\n📊 测试 1: 低并发场景 (10 线程)")
        result1 = self.benchmark_concurrent_access(
            num_threads=10,
            requests_per_thread=100,
            pool_size=5,
        )
        self._print_result(result1)
        
        # 测试 2: 中等并发
        print("\n📊 测试 2: 中等并发场景 (50 线程)")
        result2 = self.benchmark_concurrent_access(
            num_threads=50,
            requests_per_thread=50,
            pool_size=10,
        )
        self._print_result(result2)
        
        # 测试 3: 高并发
        print("\n📊 测试 3: 高并发场景 (100 线程)")
        result3 = self.benchmark_concurrent_access(
            num_threads=100,
            requests_per_thread=20,
            pool_size=20,
        )
        self._print_result(result3)
        
        # 测试 4: 异步访问
        print("\n📊 测试 4: 异步访问场景 (50 协程)")
        result4 = self.benchmark_async_access(
            num_coroutines=50,
            requests_per_coroutine=100,
            pool_size=10,
        )
        self._print_result(result4)
        
        # 测试 5: LRU 清理
        print("\n📊 测试 5: LRU 清理效率")
        result5 = self.benchmark_lru_eviction(
            pool_size=5,
            total_containers=50,
        )
        self._print_result(result5)
        
        # 汇总报告
        print("\n" + "=" * 60)
        print("📋 性能测试汇总")
        print("=" * 60)
        
        summary = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'results': [r.to_dict() for r in self.results],
            'summary': {
                'max_throughput': max(r.throughput_per_sec for r in self.results),
                'min_avg_latency': min(r.avg_latency_ms for r in self.results if r.avg_latency_ms > 0),
                'max_hit_rate': max(r.hit_rate for r in self.results),
            }
        }
        
        print(f"\n最大吞吐量: {summary['summary']['max_throughput']:.2f} req/s")
        print(f"最小平均延迟: {summary['summary']['min_avg_latency']:.2f} ms")
        print(f"最高命中率: {summary['summary']['max_hit_rate']:.2%}")
        
        # 保存结果
        output_path = Path.home() / ".nanobot" / "benchmark_results.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        print(f"\n结果已保存到: {output_path}")
        
        return summary
    
    def _print_result(self, result: BenchmarkResult):
        """打印单个测试结果"""
        print(f"  总请求: {result.total_requests}")
        print(f"  成功: {result.successful_requests} | 失败: {result.failed_requests}")
        print(f"  平均延迟: {result.avg_latency_ms:.2f} ms")
        print(f"  P50/P95/P99: {result.p50_latency_ms:.2f}/{result.p95_latency_ms:.2f}/{result.p99_latency_ms:.2f} ms")
        print(f"  吞吐量: {result.throughput_per_sec:.2f} req/s")
        print(f"  池命中率: {result.hit_rate:.2%}")
        if result.memory_peak_mb > 0:
            print(f"  内存峰值: {result.memory_peak_mb:.2f} MB")
    
    def check_baseline(self, result: BenchmarkResult) -> Dict[str, bool]:
        """检查性能是否满足基线要求"""
        checks = {
            'throughput': result.throughput_per_sec >= PERFORMANCE_BASELINE['min_throughput'],
            'avg_latency': result.avg_latency_ms <= PERFORMANCE_BASELINE['max_avg_latency'],
            'hit_rate': result.hit_rate >= PERFORMANCE_BASELINE['min_hit_rate'],
            'p99_latency': result.p99_latency_ms <= PERFORMANCE_BASELINE['max_p99_latency'],
        }
        return checks


# ============================================================================
# Pytest 测试类
# ============================================================================

@pytest.mark.perf
class TestContainerPoolPerformance:
    """容器池性能测试（pytest 标记）"""
    
    @pytest.mark.perf
    def test_concurrent_low(self):
        """低并发性能测试"""
        benchmark = ContainerPoolBenchmark()
        result = benchmark.benchmark_concurrent_access(
            num_threads=10,
            requests_per_thread=100,
            pool_size=5,
        )
        
        # 基线检查
        checks = benchmark.check_baseline(result)
        assert checks['throughput'], f"吞吐量 {result.throughput_per_sec} 低于基线 {PERFORMANCE_BASELINE['min_throughput']}"
        assert result.failed_requests == 0, f"存在失败请求: {result.errors}"
    
    @pytest.mark.perf
    def test_concurrent_high(self):
        """高并发性能测试"""
        benchmark = ContainerPoolBenchmark()
        result = benchmark.benchmark_concurrent_access(
            num_threads=100,
            requests_per_thread=20,
            pool_size=20,
        )
        
        # 高并发允许更宽松的延迟
        assert result.avg_latency_ms <= PERFORMANCE_BASELINE['max_avg_latency'] * 2, \
            f"高并发平均延迟 {result.avg_latency_ms} 超过阈值"
        assert result.throughput_per_sec >= PERFORMANCE_BASELINE['min_throughput'], \
            f"吞吐量 {result.throughput_per_sec} 低于基线"
    
    @pytest.mark.perf
    @pytest.mark.asyncio
    async def test_async_performance(self):
        """异步性能测试"""
        benchmark = ContainerPoolBenchmark()
        result = benchmark.benchmark_async_access(
            num_coroutines=50,
            requests_per_coroutine=100,
            pool_size=10,
        )
        
        checks = benchmark.check_baseline(result)
        assert checks['throughput'], f"异步吞吐量 {result.throughput_per_sec} 低于基线"
    
    @pytest.mark.perf
    def test_lru_eviction(self):
        """LRU 清理测试"""
        benchmark = ContainerPoolBenchmark()
        result = benchmark.benchmark_lru_eviction(
            pool_size=5,
            total_containers=50,
        )
        
        # 验证池大小被限制
        assert result.successful_requests <= 5, "LRU 清理未正确限制池大小"
    
    def test_baseline_compliance(self):
        """基线合规测试（非性能测试，默认运行）"""
        benchmark = ContainerPoolBenchmark()
        
        # 运行一个简单测试
        result = benchmark.benchmark_concurrent_access(
            num_threads=5,
            requests_per_thread=50,
            pool_size=3,
        )
        
        checks = benchmark.check_baseline(result)
        
        # 打印合规报告
        print("\n📊 性能基线合规报告:")
        for name, passed in checks.items():
            status = "✅" if passed else "❌"
            print(f"  {status} {name}")
        
        # 所有检查都应通过
        assert all(checks.values()), f"性能基线检查失败: {checks}"


if __name__ == '__main__':
    benchmark = ContainerPoolBenchmark()
    benchmark.run_all_benchmarks()
