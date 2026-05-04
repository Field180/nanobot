#!/bin/bash
# Nanobot LLM 107s→16s 优化启动脚本
# 使用方法: ./launch_fast.sh

echo "🚀 Nanobot 极速模式启动 (目标: 16s LLM响应)"
echo "================================================"

# 默认使用快速模型
export NANOBOT_LLM_MODEL="${NANOBOT_LLM_MODEL:-qwen2.5-coder:14b}"

# 其他性能优化环境变量
export NANOBOT_FAST_MODE="1"
export NANOBOT_MAX_TOKENS="2048"  # 减少生成长度

echo "📊 当前配置:"
echo "  LLM模型: $NANOBOT_LLM_MODEL"
echo "  快速模式: $NANOBOT_FAST_MODE"
echo "  最大Token: $NANOBOT_MAX_TOKENS"
echo ""
echo "💡 模型性能参考:"
echo "  qwen2.5-coder:14b  → 12-18s (推荐)"
echo "  qwen3-coder:30b    → 25-35s"
echo "  qwen3-coder:51b    → 90-120s (原配置)"
echo ""

# 启动服务
./launch.sh
