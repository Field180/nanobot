
#!/bin/bash
# Nanobot V3 - CPU + 128GB内存优化启动脚本
# 51GB模型纯CPU推理优化配置

echo "🖥️  Nanobot V3 - CPU高性能模式"
echo "========================================"
echo "模型: qwen3-coder-next:q4_K_M (51GB)"
echo "内存: 128GB"
echo "目标: 107s → 60-80s (CPU多核+内存优化)"
echo "========================================"

# === CPU核心配置 ===
CPU_CORES=$(nproc)
echo "检测到CPU核心数: $CPU_CORES"

# === CPU优化参数 ===
export NANOBOT_CPU_MODE=1               # 启用CPU模式
export NANOBOT_NUM_THREAD=$CPU_CORES    # 使用所有CPU核心
export NANOBOT_USE_MMAP=1               # 启用内存映射（减少内存拷贝）
export NANOBOT_USE_MLOCK=1              # 锁定内存（防止交换到磁盘）
export NANOBOT_NUM_BATCH=512            # 批处理大小（影响提示词处理速度）

# === 生成质量参数（优化配置） ===
export NANOBOT_MAX_TOKENS=32768          # 32K生成长度（代码生成需要更长输出）
export NANOBOT_NUM_CTX=131072            # 128K上下文（充分利用96GB VRAM + 128GB RAM）
export OLLAMA_NUM_CTX=131072             # Ollama 上下文同步
export OLLAMA_KEEP_ALIVE=60m             # 保持模型加载60分钟

# === 缓存系统 ===
export NANOBOT_CACHE_ENABLED=1            # 启用语义缓存

# === 模型选择 ===
export NANOBOT_LLM_MODEL=qwen3-coder-next:q4_K_M  # 保持51GB模型

# === 禁用GPU ===
export NANOBOT_USE_GPU=0
export NANOBOT_NUM_GPU=0

echo ""
echo "📊 CPU优化配置:"
echo "  线程数:        $NANOBOT_NUM_THREAD (全部核心)"
echo "  内存映射:      已启用 (mmap减少拷贝)"
echo "  内存锁定:      已启用 (mlock防止交换)"
echo "  批处理大小:    $NANOBOT_NUM_BATCH"
echo ""
echo "📊 生成质量配置:"
echo "  Max Tokens:    $NANOBOT_MAX_TOKENS"
echo "  上下文窗口:    $NANOBOT_NUM_CTX (128K)"
echo "  Ollama上下文:  $OLLAMA_NUM_CTX (128K)"
echo "  语义缓存:      已启用"
echo ""
echo "💡 内存使用预估 (96GB VRAM + 128GB RAM):"
echo "  模型:          ~51GB (内存映射)"
echo "  KV Cache:      ~50GB (128K上下文, Q8量化)"
echo "  其他:          ~10GB (SNN等组件)"
echo "  总计:          ~111GB / 224GB (安全)"
echo ""
echo "⚡ 速度优化原理:"
echo "  1. 多线程并行: 所有CPU核心参与推理"
echo "  2. 内存映射: 模型文件直接映射，减少加载时间"
echo "  3. 内存锁定: 防止Windows交换模型到磁盘"
echo "  4. 语义缓存: 重复查询秒级响应"
echo ""
echo "启动服务..."
echo ""

# 启动
cd "$(dirname "$0")"
./launch.sh
