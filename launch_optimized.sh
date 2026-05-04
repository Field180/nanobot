#!/bin/bash
# Nanobot V3 极速优化启动脚本
# 不更换模型（保持51GB qwen3-coder-next:q4_K_M），通过参数优化提速

echo "🚀 Nanobot V3 - 极速优化模式"
echo "========================================"
echo "模型: qwen3-coder-next:q4_K_M (51GB - 保持不变)"
echo "目标: 107s → 50-70s (通过参数优化)"
echo "========================================"

# === 生成参数优化 ===
export NANOBOT_MAX_TOKENS=2048          # 减少生成长度（原4096）
export NANOBOT_USE_GPU=1                # 启用GPU加速
export NANOBOT_NUM_GPU=40               # GPU层数

# === 缓存系统优化 ===
export NANOBOT_CACHE_ENABLED=1          # 启用语义缓存

# === 模型选择（可选切换，默认保持51GB） ===
# export NANOBOT_LLM_MODEL=qwen3-coder-next:q4_K_M  # 保持默认大模型

echo ""
echo "📊 优化配置:"
echo "  Max Tokens:    $NANOBOT_MAX_TOKENS (↓50%)"
echo "  GPU加速:       $NANOBOT_USE_GPU ($NANOBOT_NUM_GPU层)"
echo "  语义缓存:      $NANOBOT_CACHE_ENABLED"
echo "  提示词压缩:    已启用 (~30%↓)"
echo "  温度:          0.6 (收敛更快)"
echo "  Top-P:         0.85"
echo "  Top-K:         40"
echo "  重复惩罚:      1.05"
echo ""
echo "💡 缓存说明:"
echo "  - 首次查询: 正常速度 (107s)"
echo "  - 重复查询: 秒级响应 (<1s)"
echo "  - 相似查询: 快速响应 + 相似度标记"
echo ""
echo "启动服务..."
echo ""

# 启动
cd "$(dirname "$0")"
./launch.sh
