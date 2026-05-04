"""
SNN-LLM Initialization Module (P9b extraction from server_final.py)
====================================================================
Initializes the SNN-LLM hybrid processor with C++, sparse-Python, or OSBrain backend.
"""

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ========== SNN-LLM 混合架构配置 ==========
NANOBOT_V2_DIR = Path("/home/field/nanobot_v2")
NANOBOT_V2_VENV = Path("/home/field/nanobot_v2/venv_cpu")
NANOBOT_V2_PYTHON = str(NANOBOT_V2_VENV / "bin" / "python3")

# 全局SNN状态
SNN_PROCESSOR = None
SNN_ENABLED = False

# 快速模式配置
SNN_FAST_MODE = True
SNN_SPARSE_MODE = True
SNN_FAST_PARAMS = {
    'time_window': 100,      # SNN仿真时间 (ms)
    'max_length': 4096,     # 增加LLM输出长度 (400→4096)
    'skip_feedback': False,  # 启用反馈闭环
    'n_neurons': 6_000_000,       # 600万神经元 - 仿生学优化版
    'use_openai_api': True, # 使用OpenAI兼容接口
    'use_sparse_snn': True,  # 使用稀疏事件驱动SNN
}


def get_snn_processor():
    """获取全局 SNN 处理器实例"""
    return SNN_PROCESSOR


def is_snn_enabled():
    """检查 SNN 是否启用"""
    return SNN_ENABLED


def reset_snn_processor():
    """重置 SNN 状态"""
    global SNN_PROCESSOR
    if SNN_PROCESSOR:
        SNN_PROCESSOR.reset()
        return True
    return False


def init_snn_processor(fast_mode: bool = None, workspace: Path = None,
                       ollama_host: str = None):
    """初始化SNN-LLM混合处理器 - 使用nanobot_v2的venv"""
    global SNN_PROCESSOR, SNN_ENABLED
    if SNN_PROCESSOR is not None:
        return SNN_PROCESSOR

    # 使用快速模式配置
    if fast_mode is None:
        fast_mode = SNN_FAST_MODE

    if workspace is None:
        workspace = Path("/home/field/.nanobot/workspace")

    if ollama_host is None:
        ollama_host = os.environ.get("NANOBOT_OLLAMA_SSH_HOST", "192.168.140.1")

    try:
        # 使用nanobot_v2的venv_cpu中的site-packages
        venv_site_packages = NANOBOT_V2_VENV / "lib" / "python3.12" / "site-packages"
        if venv_site_packages.exists():
            sys.path.insert(0, str(venv_site_packages))
            logger.info(f"添加venv路径: {venv_site_packages}")

        # 先确保distutils可用 (Python 3.12需要)
        try:
            import distutils
        except ImportError:
            import setuptools
            import setuptools._distutils
            sys.modules['distutils'] = setuptools._distutils
            logger.info("已通过setuptools启用distutils兼容")

        sys.path.insert(0, str(NANOBOT_V2_DIR))

        # 选择SNN实现：C++核心（优先）> Python稀疏 > 原OSBrain
        try:
            # 尝试加载C++ SNN核心（最快，600万神经元<1000ms）
            sys.path.insert(0, str(workspace / "web_ui"))
            from snn_core import FastSparseSNNCore
            from interface.semantic_encoder import SemanticBridge
            from interface.hybrid_processor import HybridProcessor
            from models.ollama_integration import OllamaLLM

            logger.info(f"🚀 初始化C++ SNN核心... (600万神经元，目标<1000ms)")

            # 1000ms优化配置：20步 + 2连接/神经元
            snn = FastSparseSNNCore(
                n_neurons=6_000_000,
                n_steps=20,           # 20步（vs 原100步）
                dt=0.5                # 0.5ms/步，总10ms仿真
            )
            snn.build_sparse_weights(connections_per_neuron=2)  # 仅2连接

            n_neurons = 6_000_000
            time_window = 10  # 10ms有效仿真

            logger.info(f"✅ C++ SNN核心初始化完成 (20步x2连接，目标<1000ms)")

        except ImportError:
            # C++模块不可用，回退到Python稀疏SNN
            logger.warning("⚠️ C++ SNN核心不可用，回退到Python稀疏SNN")

            if SNN_SPARSE_MODE:
                from sparse_snn import SparseSNNConfig, SparseSNNWrapper
                from interface.semantic_encoder import SemanticBridge
                from interface.hybrid_processor import HybridProcessor
                from models.ollama_integration import OllamaLLM

                logger.info(f"🧠 初始化Python稀疏SNN... (60万神经元，目标<100ms)")

                # 降级配置：60万神经元确保性能
                config = SparseSNNConfig()
                config.sensory_neurons = 90_000
                config.association_neurons = 180_000
                config.decision_neurons = 150_000
                config.prefrontal_neurons = 90_000
                config.motor_neurons = 90_000

                n_neurons = 600_000
                time_window = config.time_window

                snn = SparseSNNWrapper(config, name="sparse_web_os_brain")
                snn.build(seed=42)

                logger.info(f"✅ Python稀疏SNN初始化完成 (60万神经元)")

            else:
                # 使用OSBrain 600万神经元仿生学配置（原版）
                from nanobot_v3_os_brain import OSBrain, OSBrainConfig
                from interface.semantic_encoder import SemanticBridge
                from interface.hybrid_processor import HybridProcessor
                from models.ollama_integration import OllamaLLM

                logger.info(f"🧠 初始化SNN-LLM混合处理器... (600万神经元仿生学模式)")

                # 使用600万神经元仿生学配置
                config = OSBrainConfig()
                n_neurons = (config.sensory_neurons + config.association_neurons +
                            config.decision_neurons + config.prefrontal_neurons +
                            config.motor_neurons)
                time_window = config.simulation_duration

                # SNN - 使用OSBrain 600万神经元
                snn = OSBrain(config, name="web_os_brain")
                snn.build(seed=42)

        # 语义桥接器
        bridge = SemanticBridge(embedding_dim=768, n_neurons=config.association_neurons, time_window=time_window)

        # LLM (连接到Windows Ollama)
        llm = OllamaLLM(
            model_name='qwen3-coder-next:q4_K_M',
            embedding_model='nomic-embed-text',
            base_url=f'http://{ollama_host}:11434',
            max_tokens=SNN_FAST_PARAMS['max_length'],
            timeout=600,  # 增加超时时间
            use_openai_api=SNN_FAST_PARAMS['use_openai_api']
        )

        # 混合处理器 - 使用600万神经元配置
        SNN_PROCESSOR = HybridProcessor(
            snn=snn, llm=llm, semantic_bridge=bridge,
            max_history=50, context_window=16000, use_compression=True
        )
        SNN_ENABLED = True
        logger.info(f"✅ SNN-LLM混合处理器初始化成功 (总计{n_neurons:,}神经元)")
        logger.info(f"   感觉:{config.sensory_neurons:,} | 联合:{config.association_neurons:,} | 决策:{config.decision_neurons:,} | 运动:{config.motor_neurons:,}")
        return SNN_PROCESSOR

    except Exception as e:
        logger.error(f"❌ SNN-LLM初始化失败: {e}", exc_info=True)
        SNN_ENABLED = False
        return None
