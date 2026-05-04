# NeuraCore 300万神经元 STDP学习系统

基于Brian2的300万神经元可学习脉冲神经网络系统，支持STDP突触可塑性和奖励调节学习。

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                  NeuraCore3M (300万神经元)                   │
├─────────────────────────────────────────────────────────────┤
│  感觉皮层 (Sensory)        750,000 神经元 (25%)               │
│     ↓ (2%密度, 静态)                                        │
│  联合皮层 (Association)  1,500,000 神经元 (50%) ← 主学习区 │
│     ↓ (1.5%密度, 可塑STDP)                                  │
│  决策皮层 (Decision)       375,000 神经元 (12.5%)          │
│     ↓ (3%密度, 静态)                                        │
│  运动皮层 (Motor)          375,000 神经元 (12.5%)          │
└─────────────────────────────────────────────────────────────┘

突触总数: ~5亿 (控制在64GB内存内)
可塑突触: ~30% (联合皮层学习区域)
```

## 文件结构

```
web_ui/
├── neuracore3m.py                  # 300万神经元核心
├── ultra_sparse_connectome_3m.py   # 连接组生成器
├── stdp_learning.py               # STDP学习规则
├── test_stdp_learning.py          # 测试脚本
├── neuracore_backend.py           # 后端API (已更新)
└── README_3M.md                   # 本文档
```

## 系统要求

### 硬件
- **内存**: 64GB RAM (必须)
- **CPU**: 8核以上 (推荐16核)
- **磁盘**: 50GB空闲空间

### 软件
```bash
Ubuntu 20.04+
Python 3.12
Brian2 >= 2.5
NumPy >= 1.24
```

## 快速开始

### 1. 环境变量配置

```bash
# 默认使用1万神经元轻量系统
export NEURACORE_SCALE=10k

# 启用300万神经元测试模式 (10万神经元，推荐)
export NEURACORE_SCALE=3M

# 启用完整300万神经元 (需要64GB，启动较慢)
export NEURACORE_SCALE=3M
export NEURACORE_3M_FULL=true
```

### 2. 启动Web UI

```bash
cd /home/field/.nanobot/workspace/web_ui
./launch.sh
```

### 3. 快速测试

```bash
# 测试10万神经元系统
python3 test_stdp_learning.py --quick

# 完整测试套件
python3 test_stdp_learning.py --full
```

## 核心功能

### STDP学习

```python
from neuracore3m import init_neuracore3m
from stdp_learning import STDPLearningRule, STDPParameters

# 初始化带STDP的核心
core = init_neuracore3m(enable_stdp=True, test_mode=True)

# 运行仿真并应用奖励
result = core.simulate_step(
    sensory_input={100: 5.0, 200: 3.0},
    duration_ms=100,
    reward_signal=1.0  # 正奖励强化学习
)
```

### 词语关联学习

```python
from stdp_learning import WordAssociationLearner

learner = WordAssociationLearner(stdp_rule)

# 注册词语映射
learner.register_word("猫", np.arange(0, 100))
learner.register_word("动物", np.arange(100, 200))

# 呈现学习对 (CS → US)
learner.present_pair("猫", "动物", delay_ms=50)

# 测试学习效果
associated = learner.test_association("猫")  # 返回: "动物"
```

### 工具使用学习 (R-STDP)

```python
from stdp_learning import ToolUseLearning, RewardModulatedSTDP

rstdp = RewardModulatedSTDP(stdp_rule)
tool_learner = ToolUseLearning(rstdp)

# 注册工具
tool_learner.register_intent_tool(
    intent_neurons=np.arange(0, 50),
    tool_name="time_query",
    action_neurons=np.arange(0, 30)
)

# 报告工具执行结果
tool_learner.report_tool_result("time_query", success=True, synapses=plastic_syn)

# 查看学习统计
stats = tool_learner.get_tool_stats("time_query")
print(f"成功率: {stats['success_rate']*100:.1f}%")
```

## 内存优化策略

### 分块构建

为避免Brian2的`_dynamic_array`溢出，采用分块策略：

```python
from ultra_sparse_connectome_3m import UltraSparseConnectome3M

connectome = UltraSparseConnectome3M(
    n_sensory=750000,
    n_association=1500000,
    n_decision=375000,
    n_motor=375000,
    chunk_size=100000  # 每块10万连接
)

# 预计算连接数，避免动态扩容
synapse_configs = connectome.create_fractal_connectome()
```

### 稀疏矩阵

Brian2自动使用稀疏存储。关键参数：
- **局部密度**: 1.5% (默认)
- **长程密度**: 0.5%
- **突触/神经元**: ~170 (远低于生物密度)

## 性能监控

### 内存监控

```bash
# 实时内存监控
watch -n 1 free -h

# Python内存分析
python3 -c "
import psutil
import time
while True:
    mem = psutil.virtual_memory()
    print(f'内存: {mem.percent}% | 可用: {mem.available/1024**3:.1f}GB')
    time.sleep(1)
"
```

### 仿真速度

```python
import time

# 测试单步速度
start = time.time()
result = core.simulate_step(duration_ms=100)
elapsed = time.time() - start

print(f"单步仿真: {elapsed*1000:.1f}ms")
print(f"脉冲统计: {result['spike_counts']}")
```

## 故障排查

### 问题1: Brian2动态数组溢出

**症状**: `RuntimeError: _dynamic_array_synapses__synaptic_pre.resize()`

**解决**:
1. 使用测试模式: `test_mode=True`
2. 减小`chunk_size`到50000
3. 降低连接密度到1%

### 问题2: 内存不足

**症状**: `MemoryError` 或系统卡顿

**解决**:
```bash
# 检查当前内存
free -h

# 减小系统规模
export NEURACORE_SCALE=10k

# 或启用交换分区
sudo swapon /swapfile
```

### 问题3: 构建时间过长

**症状**: 构建超过10分钟

**解决**:
- 使用分块构建: `build_incremental=True`
- 减少突触密度
- 考虑使用NEST替代Brian2 (见下文)

## 备选方案: NEST Simulator

如果Brian2无法承载300万神经元，可切换到NEST：

```python
# nest_alternative.py (概念代码)
import nest

# NEST支持分布式计算
def build_3m_nest():
    # 创建神经元
    sensory = nest.Create("iaf_psc_alpha", 750000)
    association = nest.Create("iaf_psc_alpha", 1500000)
    
    # 使用MPI分布式
    nest.SetKernelStatus({"total_num_virtual_procs": 16})
    
    # 创建连接
    nest.Connect(sensory, association, 
                 conn_spec={"rule": "fixed_outdegree", "outdegree": 30},
                 syn_spec={"model": "stdp_synapse"})
```

## API接口

### 状态查询

```bash
GET /api/neuracore/status

# 返回
{
    "enabled": true,
    "initialized": true,
    "neurons": 10000,
    "scale": "3m_test",
    "scale_name": "30万神经元STDP测试系统",
    "stdp_enabled": true
}
```

### 流式对话

```bash
POST /api/neuracore/stream
Content-Type: application/json

{
    "text": "你好，请查询时间",
    "use_reflex": false,
    "stream": true
}
```

## 权重保存与加载

```python
# 保存学习后的权重
core.save_weights("trained_weights.npz")

# 加载预训练权重
core.load_weights("trained_weights.npz")
```

## 开发路线图

1. ✅ **Phase 1**: 300万神经元基础架构
2. ✅ **Phase 2**: STDP学习机制
3. 🔄 **Phase 3**: 涌现行为验证
4. ⏳ **Phase 4**: 工作记忆集成
5. ⏳ **Phase 5**: 视觉-语言多模态

## 参考文献

1. Brian2 Documentation: https://brian2.readthedocs.io/
2. STDP Learning Rule: Bi & Poo (1998)
3. Dopamine-modulated STDP: Izhikevich (2007)
4. Sparse Connectome: Song et al. (2005)

## 作者与许可证

- 作者: Nanobot Development Team
- 许可证: MIT
- 版本: v3.1.0-3M

---

**注意**: 300万神经元完整系统需要64GB内存。建议使用测试模式(10万神经元)进行开发和验证，然后再扩展到完整规模。
