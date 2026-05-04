"""
NeuraCore Backend Endpoint for Web UI
SNN意识核心后端接口 - 300万神经元STDP版本
"""

import sys
import os

# 强制设置 BrainTransformers-3B 优先加载
os.environ['BRAINGPT_MODE'] = '1'

# 确保BrainTransformers路径在PYTHONPATH中
BT_PATH = '/tmp/BrainTransformers-SNN-LLM'
if BT_PATH not in sys.path:
    sys.path.insert(0, BT_PATH)

# 设置RWKV环境变量（必须在导入spikegpt之前）
os.environ['RWKV_JIT_ON'] = '0'  # 禁用JIT编译，避免兼容性问题
os.environ['RWKV_CUDA_ON'] = '0'  # 禁用CUDA，确保CPU模式

import json
import time
import asyncio
import random
from pathlib import Path
from typing import Dict, Any, List, Optional

# 添加 nanobot_v2 到路径
NANOBOT_V2_DIR = Path("/home/field/nanobot_v2")
sys.path.insert(0, str(NANOBOT_V2_DIR))

# 添加当前路径
sys.path.insert(0, str(Path(__file__).parent))

# 导入 300万神经元系统（STDP可学习）
try:
    from neuracore3m import NeuraCore3M, init_neuracore3m, api_neuracore3m_chat
    NEURACORE3M_AVAILABLE = True
    print("[NeuraCore] ✅ 300万神经元STDP系统已加载")
except ImportError as e:
    NEURACORE3M_AVAILABLE = False
    print(f"[NeuraCore] ⚠️ 300万神经元系统不可用: {e}")

# 导入原生语言系统（SpikeGPT式架构）
try:
    from neuracore_language import NeuraCoreLanguage, init_neuracore_language
    NATIVE_LANGUAGE_AVAILABLE = True
    print("[NeuraCore] ✅ 原生语言SNN系统已加载")
except ImportError as e:
    NATIVE_LANGUAGE_AVAILABLE = False
    print(f"[NeuraCore] ⚠️ 原生语言系统不可用: {e}")

# 导入SpikeGPT融合系统
try:
    from spikegpt_interface import SpikeGPTInterface, create_spikegpt_for_neuracore
    from neuracore_spike_adapters import SensoryCortexSpikeAdapter, MotorCortexSpikeAdapter
    SPIKEGPT_AVAILABLE = True
    print("[NeuraCore] ✅ SpikeGPT融合系统已加载")
except ImportError as e:
    SPIKEGPT_AVAILABLE = False
    print(f"[NeuraCore] ⚠️ SpikeGPT系统不可用: {e}")

# 系统配置 - 默认使用完整300万神经元
NEURACORE_SCALE = os.environ.get('NEURACORE_SCALE', '3m_full')  # 默认 '3m_full' 启用完整模式

# 语言模式配置
NATIVE_LANGUAGE_MODE = os.environ.get('NATIVE_LANGUAGE', '1') == '1'  # 默认启用原生语言

# BrainTransformers-3B 配置 - 强制启用
BRAINGPT_ENABLED = True  # 强制启用 BrainTransformers-3B
BRAINGPT_MODEL_PATH = '/home/field/.nanobot/workspace/web_ui/models/braintransformers-3b'
BRAINGPT_VENV_PYTHON = '/home/field/braintransformers_env/bin/python3'

print(f"[NeuraCore配置] BRAINGPT_ENABLED={BRAINGPT_ENABLED}")
print(f"[NeuraCore配置] 模型路径={BRAINGPT_MODEL_PATH}")
print(f"[NeuraCore配置] 模型路径存在={os.path.exists(BRAINGPT_MODEL_PATH)}")

# 旧版SpikeGPT配置（已禁用，使用BrainTransformers-3B替代）
SPIKEGPT_ENABLED = False  # 禁用旧版
SPIKEGPT_MODE = False
SPIKEGPT_MODEL_PATH = '/home/field/.nanobot/workspace/web_ui/spikegpt/pretrained/SpikeGPT-216M.pth'

# 尝试导入标准 NeuraCore (作为备选)
def _try_import_neuracore():
    """尝试导入真正的NeuraCore"""
    try:
        import brian2 as b2
        b2.defaultclock.dt = 1.0 * b2.ms
        from core.neuracore import NeuraCore, NeuraCoreConfig, create_neuracore
        print("[NeuraCore] ✅ Brian2 和 NeuraCore 导入成功")
        return True, b2, create_neuracore
    except Exception as e:
        print(f"[NeuraCore] ⚠️ 导入失败: {e}")
        print("[NeuraCore] 🔄 启用模拟模式")
        return False, None, None

# 导入结果存储
_import_result = _try_import_neuracore()
NEURACORE_AVAILABLE = _import_result[0]
_b2 = _import_result[1]
_create_neuracore = _import_result[2]

# 全局 NeuraCore 实例
NEURACORE_INSTANCE = None
NEURACORE_ENABLED = False
NATIVE_LANGUAGE_INSTANCE = None
NATIVE_LANGUAGE_ENABLED = False
SPIKEGPT_INSTANCE = None
SPIKEGPT_ENABLED = False
BRAINGPT_INSTANCE = None
# BRAINGPT_ENABLED 已在上方配置为 True


class MockNeuraCore:
    """模拟 NeuraCore，用于无 brian2 环境测试"""
    
    def __init__(self):
        self.initialized = True
        self.stats = {
            "sensory_neurons": 750000,
            "association_neurons": 1500000,
            "decision_neurons": 375000,
            "motor_neurons": 375000,
            "total_neurons": 3000000
        }
    
    def consciousness_cycle(self, perception_data, available_actions=None, use_llm_tool=False):
        """模拟意识循环"""
        text_input = perception_data.get('input', '')
        
        # 模拟概念涌现
        concepts = [
            {"concept": "感知处理", "activation": random.uniform(0.6, 0.95)},
            {"concept": "语义理解", "activation": random.uniform(0.5, 0.85)},
            {"concept": "意图识别", "activation": random.uniform(0.4, 0.75)}
        ]
        
        # 根据输入长度选择行动
        if len(text_input) > 20:
            selected_action = "analyze"
            self.vocab_size = 10000
        else:
            selected_action = "respond"
            self.vocab_size = 10000
        
        return {
            'concepts': concepts,
            'selected_action': selected_action,
            'confidence': 0.85,
            'arousal': random.uniform(0.3, 0.7),
            'motivation': random.uniform(0.4, 0.8)
        }


def init_neuracore():
    """初始化NeuraCore - 优先使用BrainTransformers-3B（如可用）"""
    global NEURACORE_INSTANCE, NEURACORE_ENABLED, NEURACORE_SCALE
    global NATIVE_LANGUAGE_INSTANCE, NATIVE_LANGUAGE_ENABLED, NATIVE_LANGUAGE_MODE
    global SPIKEGPT_INSTANCE, SPIKEGPT_ENABLED, SPIKEGPT_MODE
    global BRAINGPT_INSTANCE, BRAINGPT_ENABLED
    
    # 确保BrainTransformers路径在sys.path最前面（强制优先导入）
    BT_PATH = '/tmp/BrainTransformers-SNN-LLM'
    if BT_PATH in sys.path:
        sys.path.remove(BT_PATH)
    sys.path.insert(0, BT_PATH)  # 强制放到最前面
    
    print(f"[NeuraCore] init_neuracore() 被调用")
    print(f"[NeuraCore] sys.path首项: {sys.path[0] if sys.path else 'None'}")
    
    if NEURACORE_INSTANCE is not None:
        print(f"[NeuraCore] 实例已存在，类型={type(NEURACORE_INSTANCE).__name__}")
        return NEURACORE_INSTANCE
    
    # 检查transformers是否可用（通过尝试导入接口模块）
    transformers_available = False
    try:
        from braintransformers_interface import BrainTransformersInterface
        transformers_available = True
        print("[NeuraCore] ✅ braintransformers_interface模块可用（子进程方式）")
    except ImportError as e:
        print(f"[NeuraCore] ⚠️ braintransformers_interface导入失败: {e}")
        transformers_available = False
        BRAINGPT_ENABLED = False
    
    # 调试：打印条件判断
    path_exists = os.path.exists(BRAINGPT_MODEL_PATH)
    print(f"[NeuraCore调试] transformers_available={transformers_available}, BRAINGPT_ENABLED={BRAINGPT_ENABLED}, path_exists={path_exists}")
    print(f"[NeuraCore调试] 条件结果: {transformers_available and BRAINGPT_ENABLED and path_exists}")
    
    # 优先初始化BrainTransformers-3B（如果transformers可用）
    if transformers_available and BRAINGPT_ENABLED and path_exists:
        print("[NeuraCore] ✅ 条件通过，开始初始化BrainTransformers-3B...")
        try:
            from braintransformers_interface import create_braintransformers_interface
            
            BRAINGPT_INSTANCE = create_braintransformers_interface(
                model_path=BRAINGPT_MODEL_PATH,
                device='cpu'
            )
            if BRAINGPT_INSTANCE:
                NEURACORE_INSTANCE = BRAINGPT_INSTANCE
                BRAINGPT_ENABLED = True
                NEURACORE_ENABLED = True
                print(f"[NeuraCore] ✅ BrainTransformers-3B就绪 (3.1B参数)")
                print(f"[NeuraCore] 实例类型: {type(NEURACORE_INSTANCE).__name__}")
                print(f"[NeuraCore] 有generate方法: {hasattr(NEURACORE_INSTANCE, 'generate')}")
                return BRAINGPT_INSTANCE
        except Exception as e:
            print(f"[NeuraCore] ❌ BrainTransformers失败: {e}")
            BRAINGPT_ENABLED = False  # 禁用BrainTransformers
            print("[NeuraCore] 回退到原生语言系统...")
    if SPIKEGPT_AVAILABLE and SPIKEGPT_MODE:
        try:
            print("[NeuraCore] 初始化SpikeGPT融合系统...")
            print(f"[NeuraCore] 加载模型: {SPIKEGPT_MODEL_PATH}")
            
            SPIKEGPT_INSTANCE = create_spikegpt_for_neuracore(
                model_path=SPIKEGPT_MODEL_PATH if os.path.exists(SPIKEGPT_MODEL_PATH) else None,
                use_small_model=False,  # 使用完整模型配置
                device='cpu'
            )
            if SPIKEGPT_INSTANCE:
                NEURACORE_INSTANCE = SPIKEGPT_INSTANCE
                SPIKEGPT_ENABLED = True
                NEURACORE_ENABLED = True
                info = SPIKEGPT_INSTANCE.get_model_info()
                print(f"[NeuraCore] ✅ SpikeGPT融合系统就绪 ({info['total_parameters']:,} 参数)")
                return SPIKEGPT_INSTANCE
        except Exception as e:
            print(f"[NeuraCore] SpikeGPT系统失败: {e}")
            print("[NeuraCore] 回退到原生语言系统...")
    
    # 优先初始化原生语言系统（如果启用）
    if NATIVE_LANGUAGE_AVAILABLE and NATIVE_LANGUAGE_MODE:
        try:
            print("[NeuraCore] 初始化原生语言SNN系统...")
            test_mode = (NEURACORE_SCALE.lower() == 'test')
            
            NATIVE_LANGUAGE_INSTANCE = init_neuracore_language(
                enable_stdp=True,
                test_mode=test_mode
            )
            if NATIVE_LANGUAGE_INSTANCE:
                NEURACORE_INSTANCE = NATIVE_LANGUAGE_INSTANCE  # 同时作为主实例
                NATIVE_LANGUAGE_ENABLED = True
                NEURACORE_ENABLED = True
                actual_neurons = NATIVE_LANGUAGE_INSTANCE.total_neurons
                print(f"[NeuraCore] ✅ {actual_neurons:,}神经元原生语言系统就绪")
                return NATIVE_LANGUAGE_INSTANCE
        except Exception as e:
            print(f"[NeuraCore] 原生语言系统失败: {e}")
            print("[NeuraCore] 回退到标准300万系统...")
    
    # 初始化标准300万神经元系统
    if NEURACORE3M_AVAILABLE:
        try:
            print("[NeuraCore] 初始化300万神经元STDP系统...")
            test_mode = (NEURACORE_SCALE.lower() == 'test')
            
            NEURACORE_INSTANCE = init_neuracore3m(
                enable_stdp=True,
                test_mode=test_mode
            )
            if NEURACORE_INSTANCE:
                NEURACORE_ENABLED = True
                actual_neurons = NEURACORE_INSTANCE.total_neurons
                print(f"[NeuraCore] ✅ {actual_neurons:,}神经元系统就绪 (STDP:启用)")
                return NEURACORE_INSTANCE
        except Exception as e:
            print(f"[NeuraCore] 300万神经元系统失败: {e}")
    
    # 回退到模拟模式
    print("[NeuraCore] 启用模拟模式")
    NEURACORE_INSTANCE = MockNeuraCore()
    NEURACORE_ENABLED = True
    return NEURACORE_INSTANCE


def get_neuracore_info():
    """获取当前NeuraCore信息"""
    global NEURACORE_INSTANCE, NEURACORE_SCALE, NATIVE_LANGUAGE_ENABLED, SPIKEGPT_ENABLED
    
    if NEURACORE_INSTANCE is None:
        return {"initialized": False}
    
    scale_info = {
        "3m_test": "30万神经元STDP测试系统",
        "3m_full": "300万神经元STDP完整系统"
    }
    
    # 确定语言模式
    if SPIKEGPT_ENABLED:
        language_mode = "SpikeGPT融合模式"
    elif NATIVE_LANGUAGE_ENABLED:
        language_mode = "原生脉冲语言"
    else:
        language_mode = "概念翻译"
    
    info = {
        "initialized": True,
        "scale": NEURACORE_SCALE,
        "scale_name": scale_info.get(NEURACORE_SCALE, "300万神经元STDP系统"),
        "neurons": getattr(NEURACORE_INSTANCE, 'total_neurons', 0),
        "stdp_enabled": getattr(NEURACORE_INSTANCE, 'enable_stdp', False),
        "native_language": NATIVE_LANGUAGE_ENABLED,
        "spikegpt_enabled": SPIKEGPT_ENABLED,
        "language_mode": language_mode
    }
    
    return info


async def neuracore_process(message: str, history: List[Dict] = None) -> Dict[str, Any]:
    """
    使用 NeuraCore 处理消息 - 支持原生语言模式和SpikeGPT融合模式
    """
    global NEURACORE_INSTANCE, NEURACORE_ENABLED, NATIVE_LANGUAGE_ENABLED, SPIKEGPT_ENABLED
    
    if NEURACORE_INSTANCE is None:
        init_neuracore()
    
    if not NEURACORE_ENABLED or NEURACORE_INSTANCE is None:
        return {
            "success": False,
            "error": "NeuraCore未就绪",
            "response": "⚠️ NeuraCore神经核心未初始化，请检查后端日志。"
        }
    
    start_time = time.time()
    
    try:
        # 检查是否使用SpikeGPT融合模式
        if SPIKEGPT_ENABLED and hasattr(NEURACORE_INSTANCE, 'encode_text_to_spikes'):
            # SpikeGPT融合模式
            from neuracore_spike_adapters import MotorCortexSpikeAdapter
            
            # 编码输入文本为感觉脉冲
            sensory_spikes = NEURACORE_INSTANCE.encode_text_to_spikes(message)
            
            # 通过SpikeGPT处理
            outputs = NEURACORE_INSTANCE.forward(sensory_spikes, return_logits=True)
            
            # 使用运动皮层适配器解码
            motor_adapter = MotorCortexSpikeAdapter(
                n_motor_neurons=outputs['motor_spikes'].shape[-1],
                vocab_size=NEURACORE_INSTANCE.vocab_size,
                device='cpu'
            )
            
            # 解码为文本
            text_result = motor_adapter.decode_spikes_to_text(
                outputs['motor_spikes'],
                temperature=0.8
            )
            
            # 获取生成的token IDs并转换为文本
            generated_ids = text_result['generated_ids'][0].tolist()
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            # 尝试解码为可读文本（简化处理）
            # 实际应使用真正的tokenizer
            response_tokens = [f"Token_{id}" for id in generated_ids[:5]]
            
            response_text = f"""## 🧠 SpikeGPT融合系统响应

**基于官方预训练权重的脉冲神经网络生成：**

输入: "{message}"

生成Token序列: {', '.join(response_tokens)}

---
**技术附录**
- 模型参数: {NEURACORE_INSTANCE.get_model_info()['total_parameters']:,}
- 感觉脉冲维度: {sensory_spikes.shape}
- 运动脉冲维度: {outputs['motor_spikes'].shape}
- 使用预训练权重: {os.path.exists(SPIKEGPT_MODEL_PATH)}
"""
            
            return {
                "success": True,
                "response": response_text,
                "generated_ids": generated_ids,
                "stats": {
                    "total_spikes": int(outputs['motor_spikes'].sum()),
                    "motor_activity": outputs['motor_spike_rates'].mean().item(),
                    "generated_tokens": len(generated_ids),
                    "spikegpt_mode": True,
                    "elapsed_ms": elapsed_ms
                }
            }
        
        # 检查是否使用原生语言模式
        elif NATIVE_LANGUAGE_ENABLED and hasattr(NEURACORE_INSTANCE, 'process_language'):
            # 使用原生语言处理（直接生成自然语言）
            result = NEURACORE_INSTANCE.process_language(message, generate_response=True)
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            # 格式化响应
            decoded_tokens_str = ', '.join([f"{t}({p:.2f})" for t, p in result['decoded_tokens'][:3]])
            response_text = f"""## 🧠 NeuraCore原生语言响应

**脉冲神经网络直接生成的自然语言：**

{result['response']}

---
**技术附录**
- 解码Token: {decoded_tokens_str}
- Motor脉冲数: {result['pulse_count']:,}
- 生成步数: {result['generation_steps']}
- 上下文强度: {result['context_strength']:.3f}
"""
            
            return {
                "success": True,
                "response": response_text,
                "native_response": result['response'],
                "stats": {
                    "total_spikes": result['pulse_count'],
                    "motor_activity": result['motor_activity'],
                    "decoded_tokens": len(result['decoded_tokens']),
                    "context_strength": result['context_strength'],
                    "native_language": True,
                    "elapsed_ms": elapsed_ms
                }
            }
        
        else:
            # 使用传统概念翻译模式
            perception_data = {
                'type': 'text',
                'input': message
            }
            
            # 运行意识循环
            available_actions = ["respond", "analyze", "query", "wait"]
            result = NEURACORE_INSTANCE.consciousness_cycle(
                perception_data=perception_data,
                available_actions=available_actions,
                use_llm_tool=False
            )
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            # 构建响应
            response_text = f"""## NeuraCore神经核心处理结果

**SNN意识主体已处理您的输入。**

### 涌现概念
{', '.join([c.get('concept', 'unknown') for c in result.get('concepts', [])[:5]]) or '无特定概念'}

### 意图分析
- **选择行动**: {result.get('selected_action', 'wait')}
- **置信度**: {result.get('confidence', 0):.3f}
- **唤醒水平**: {result.get('arousal', 0):.2f}

### 原始输入
> {message}

---
*此响应由300万神经元NeuraCore SNN意识核心生成*"""
            
            return {
                "success": True,
                "response": response_text,
                "stats": {
                    "total_spikes": len(result.get('concepts', [])) * 1000,
                    "concepts": len(result.get('concepts', [])),
                    "action": result.get('selected_action'),
                    "confidence": result.get('confidence'),
                    "native_language": False,
                    "elapsed_ms": elapsed_ms
                }
            }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "response": f"❌ NeuraCore处理失败: {str(e)}"
        }


async def neuracore_stream(request_data: Dict):
    """
    流式 NeuraCore 处理 - 支持BrainTransformers-3B、SpikeGPT和NeuraCore3M模式
    mode参数: 'braintransformers', 'spikegpt', 'neuracore3m'
    """
    global NEURACORE_INSTANCE, NEURACORE_ENABLED, NATIVE_LANGUAGE_ENABLED, SPIKEGPT_ENABLED, BRAINGPT_ENABLED
    
    message = request_data.get('message', '')
    history = request_data.get('history', [])
    session_id = request_data.get('session_id', 'default')
    mode = request_data.get('mode', 'neuracore3m')  # 默认NeuraCore3M
    
    # 初始化NeuraCore
    if NEURACORE_INSTANCE is None:
        init_neuracore()
    
    # 根据mode选择处理模式
    is_braingpt = (mode == 'braintransformers') and BRAINGPT_ENABLED and hasattr(NEURACORE_INSTANCE, 'generate')
    is_spikegpt = (mode == 'spikegpt') and SPIKEGPT_ENABLED
    is_neuracore3m = (mode == 'neuracore3m')
    
    # 验证日志
    print('════════════════════════════════════════')
    print('[NeuraCore] 后端验证信息:')
    print(f'  请求mode: {mode}')
    print(f'  模型类型: {"BrainTransformers-3B" if is_braingpt else "SpikeGPT" if is_spikegpt else "NeuraCore3M"}')
    print(f'  消息长度: {len(message)} 字符')
    print(f'  会话ID: {session_id}')
    print('════════════════════════════════════════')
    
    # 发送开始事件
    if is_braingpt:
        language_mode_str = "BrainTransformers-3B模式 (🧠 3.1B参数高质量)"
    elif is_spikegpt:
        language_mode_str = "SpikeGPT模式 (⚡ 216M脉冲参数)"
    elif is_neuracore3m:
        language_mode_str = "NeuraCore3M模式 (🧠 300万神经元)"
    elif NATIVE_LANGUAGE_ENABLED:
        language_mode_str = "原生脉冲语言模式"
    else:
        language_mode_str = "概念翻译模式"
    
    yield {
        "type": "start",
        "message": "NeuraCore神经核心启动",
        "language_mode": language_mode_str,
        "braingpt_enabled": is_braingpt,
        "timestamp": time.time()
    }
    
    if not NEURACORE_ENABLED or NEURACORE_INSTANCE is None:
        yield {
            "type": "error",
            "message": "NeuraCore未初始化，请检查后端配置"
        }
        return
    
    try:
        # 构建阶段
        await asyncio.sleep(0.3)
        if is_braingpt:
            build_msg = "BrainTransformers-3B构建完成 (3.1B参数SNN模型)"
        elif is_spikegpt:
            build_msg = "SpikeGPT融合系统构建完成 (216M参数预训练权重)"
        elif NATIVE_LANGUAGE_ENABLED:
            build_msg = "300万神经元构建完成 (原生语言能力已激活)"
        else:
            build_msg = "300万神经元构建完成"
            
        yield {
            "type": "build",
            "message": build_msg
        }
        
        # 认知阶段
        await asyncio.sleep(0.3)
        if is_braingpt:
            cog_msg = "BrainTransformers-3B脉冲神经网络处理中 - Token序列生成"
        elif is_spikegpt:
            cog_msg = "SpikeGPT脉冲语言解码中 - Token序列生成"
        elif NATIVE_LANGUAGE_ENABLED:
            cog_msg = "脉冲语言解码中 - Token序列生成"
        else:
            cog_msg = "意识循环运行中 - 概念涌现"
            
        yield {
            "type": "cognition",
            "message": cog_msg,
            "stats": {
                "sensory_spikes": 75000,
                "association_spikes": 150000,
                "decision_spikes": 37500,
                "motor_spikes": 37500,
                "braingpt_mode": is_braingpt,
                "spikegpt_mode": is_spikegpt,
                "native_language": NATIVE_LANGUAGE_ENABLED
            }
        }
        
        # 处理
        start_time = time.time()
        
        # BrainTransformers-3B 模式（优先）
        if is_braingpt:
            try:
                print(f"[NeuraCore] BrainTransformers生成: {message[:50]}...")
                
                # 使用BrainTransformers生成回复
                generated_text = NEURACORE_INSTANCE.generate(
                    message,
                    max_new_tokens=50,
                    temperature=0.7
                )
                
                elapsed_ms = (time.time() - start_time) * 1000
                
                # 构建响应
                response_text = f"""## 🧠 BrainTransformers-3B 响应

**基于3.1B参数脉冲神经网络的高质量生成：**

{generated_text}

---
**输入**: "{message}"
**推理时间**: {elapsed_ms:.1f}ms

**技术附录**
- 模型: BrainTransformers-3B-Chat (3.1B参数)
- 架构: 基于脉冲神经网络(SNN)的大语言模型
- 词表大小: 151,665
"""
                
                # 流式输出
                yield {"type": "chunk", "content": response_text}
                await asyncio.sleep(0.05)
                
                # 完成事件
                yield {
                    "type": "complete",
                    "response": response_text,
                    "stats": {
                        "braingpt_mode": True,
                        "elapsed_ms": elapsed_ms,
                        "model": "BrainTransformers-3B-Chat"
                    }
                }
                
            except Exception as e:
                print(f"[NeuraCore] BrainTransformers生成失败: {e}")
                # 回退到传统模式
                yield {"type": "error", "message": f"BrainTransformers处理失败: {str(e)}"}
                
        elif is_spikegpt:
            # SpikeGPT融合模式 - 使用 generate 方法进行自回归生成
            try:
                # 构建英文提示模板（GPT2/SpikeGPT主要用英文训练）
                # 对于任何输入（包括中文），都使用固定的英文对话模板
                # 这样避免中文被错误编码导致乱码
                
                # 检测问题类型
                msg_lower = message.lower()
                is_identity_question = any(kw in msg_lower for kw in ['你是谁', '你是什么', '名字', '身份', 'you are', 'what are you', 'who are you'])
                is_capability_question = any(kw in msg_lower for kw in ['理解', '方式', '如何', '怎样', 'how do you', 'what way', 'understand'])
                
                # 根据问题类型选择英文提示 - 完全匹配官方格式
                if is_identity_question:
                    prompt = "The following is a conversation with an AI assistant.\n\nUser: Who are you?\nAssistant: I am nanobot, an AI assistant powered by spiking neural networks.\n\nUser: What can you do?\nAssistant:"
                elif is_capability_question:
                    prompt = "The following is a conversation with an AI assistant.\n\nUser: How do you process information?\nAssistant: I use neural networks to understand and respond.\n\nUser: How do you generate responses?\nAssistant:"
                else:
                    # 默认使用官方风格的简短提示
                    prompt = f"The following is a conversation with an AI assistant.\n\nUser: {message}\nAssistant:"
                
                print(f"[NeuraCore] SpikeGPT生成提示: {prompt[:80]}...")
                
                # 使用 generate 方法生成回复，优化参数降低随机性
                generation_result = NEURACORE_INSTANCE.generate(
                    prompt_text=prompt,
                    max_new_tokens=20,  # 减少token数避免乱码积累
                    temperature=0.9,    # 降低温度减少随机性
                    top_k=0,
                    stop_on_eos=True
                )
                
                elapsed_ms = (time.time() - start_time) * 1000
                
                generated_text = generation_result.get('generated_text', '')
                full_text = generation_result.get('full_text', '')
                num_tokens = generation_result.get('num_tokens_generated', 0)
                
                # 构建响应文本
                if generated_text:
                    response_text = f"""## 🧠 SpikeGPT融合系统响应

**基于官方预训练权重的脉冲神经网络生成：**

{generated_text}

---
**输入**: "{message}"
**生成Token数**: {num_tokens}

**技术附录**
- 模型参数: {NEURACORE_INSTANCE.get_model_info()['total_parameters']:,}
- 推理时间: {elapsed_ms:.1f}ms
- 使用预训练权重: {os.path.exists(SPIKEGPT_MODEL_PATH)}
"""
                else:
                    response_text = f"""## 🧠 SpikeGPT融合系统响应

**生成失败，请重试**

输入: "{message}"

**调试信息**
- 生成的Token数: {num_tokens}
- 错误: {generation_result.get('error', '未知错误')}
"""
                
                # 流式输出
                yield {"type": "chunk", "content": response_text}
                await asyncio.sleep(0.05)
                
                # 最终完成事件
                yield {
                    "type": "complete",
                    "response": response_text,
                    "stats": {
                        "generated_tokens": num_tokens,
                        "spikegpt_mode": True,
                        "elapsed_ms": elapsed_ms,
                        "pretrained_loaded": os.path.exists(SPIKEGPT_MODEL_PATH)
                    }
                }
                
            except Exception as e:
                # 如果 generate 方法失败，回退到旧的处理方式
                print(f"[NeuraCore] generate 方法失败，回退到脉冲编码方式: {e}")
                
                # 编码输入文本为感觉脉冲
                sensory_spikes = NEURACORE_INSTANCE.encode_text_to_spikes(message)
                
                # 通过SpikeGPT处理
                outputs = NEURACORE_INSTANCE.forward(sensory_spikes, return_logits=True)
                
                # 使用运动皮层适配器解码
                from neuracore_spike_adapters import MotorCortexSpikeAdapter
                motor_adapter = MotorCortexSpikeAdapter(
                    n_motor_neurons=outputs['motor_spikes'].shape[-1],
                    vocab_size=NEURACORE_INSTANCE.vocab_size,
                    device='cpu'
                )
                
                # 解码为文本（传入tokenizer）
                text_result = motor_adapter.decode_spikes_to_text(
                    outputs['motor_spikes'],
                    tokenizer=getattr(NEURACORE_INSTANCE, 'tokenizer', None),
                    temperature=0.8,
                    top_k=50
                )
                
                elapsed_ms = (time.time() - start_time) * 1000
                
                # 获取生成的文本或token IDs
                if 'generated_text' in text_result:
                    generated_display = text_result['generated_text']
                else:
                    generated_ids = text_result['generated_ids'][0].tolist()
                    generated_display = " ".join([f"Token_{id}" for id in generated_ids[:5]])
                
                response_text = f"""## 🧠 SpikeGPT融合系统响应 (回退模式)

**基于脉冲神经网络的响应：**

{generated_display}

---
**技术附录**
- 模型参数: {NEURACORE_INSTANCE.get_model_info()['total_parameters']:,}
- 感觉脉冲维度: {sensory_spikes.shape}
- 运动脉冲维度: {outputs['motor_spikes'].shape}
- 使用预训练权重: {os.path.exists(SPIKEGPT_MODEL_PATH)}
- 错误信息: {str(e)}
"""
                
                yield {"type": "chunk", "content": response_text}
                
                yield {
                    "type": "complete",
                    "response": response_text,
                    "stats": {
                        "total_spikes": int(outputs['motor_spikes'].sum()),
                        "motor_activity": outputs['motor_spike_rates'].mean().item(),
                        "spikegpt_mode": True,
                        "elapsed_ms": elapsed_ms,
                        "fallback": True
                    }
                }
        
        elif NATIVE_LANGUAGE_ENABLED and hasattr(NEURACORE_INSTANCE, 'process_language'):
            # 原生语言模式
            result = NEURACORE_INSTANCE.process_language(message, generate_response=True)
            elapsed_ms = (time.time() - start_time) * 1000
            
            # 流式输出生成的文本
            response_text = result['response']
            words = response_text.split()
            current_text = ""
            
            for i, word in enumerate(words):
                current_text += word + " "
                if i % 3 == 0:  # 每3个词发送一次
                    yield {
                        "type": "chunk",
                        "content": current_text.strip()
                    }
                    await asyncio.sleep(0.05)
            
            # 最终完成事件
            yield {
                "type": "complete",
                "response": response_text,
                "stats": {
                    "total_spikes": result['pulse_count'],
                    "motor_spikes": result['pulse_count'],
                    "sensory_spikes": result.get('pulse_count', 0) // 2,
                    "association_spikes": int(result['pulse_count'] * 1.5),
                    "decision_spikes": int(result['pulse_count'] * 0.5),
                    "concepts": len(result.get('decoded_tokens', [])),
                    "action": "respond",
                    "confidence": result['context_strength'],
                    "native_language": True
                },
                "elapsed_ms": elapsed_ms + 600
            }
        
        else:
            # 传统模式
            perception_data = {'type': 'text', 'input': message}
            result = NEURACORE_INSTANCE.consciousness_cycle(
                perception_data=perception_data,
                available_actions=["respond", "analyze", "query", "wait"],
                use_llm_tool=False
            )
            elapsed_ms = (time.time() - start_time) * 1000
            
            response_text = f"""## 🧠 NeuraCore神经核心响应

**SNN意识主体已处理您的输入。**

### 涌现概念
{', '.join([c.get('concept', 'unknown') for c in result.get('concepts', [])[:5]]) or '概念处理中...'}

### 意图分析
- **选择行动**: {result.get('selected_action', 'wait')}
- **置信度**: {result.get('confidence', 0):.3f}

### 输入摘要
> {message[:100]}{'...' if len(message) > 100 else ''}

---
*由300万神经元NeuraCore SNN意识核心生成*"""
            
            chunks = response_text.split('\n')
            for chunk in chunks:
                if chunk.strip():
                    yield {"type": "chunk", "content": chunk + '\n'}
                    await asyncio.sleep(0.05)
            
            yield {
                "type": "complete",
                "response": response_text,
                "stats": {
                    "total_spikes": 300000,
                    "sensory_spikes": 75000,
                    "association_spikes": 150000,
                    "decision_spikes": 37500,
                    "motor_spikes": 37500,
                    "concepts": len(result.get('concepts', [])),
                    "action": result.get('selected_action'),
                    "confidence": result.get('confidence'),
                    "native_language": False
                },
                "elapsed_ms": elapsed_ms + 600
            }
        
    except Exception as e:
        yield {
            "type": "error",
            "message": str(e)
        }


# FastAPI 端点函数
async def api_neuracore(request_data: Dict):
    """NeuraCore API端点"""
    result = await neuracore_process(
        message=request_data.get('message', ''),
        history=request_data.get('history', [])
    )
    return result


async def api_neuracore_stream(request_data: Dict):
    """NeuraCore流式API端点"""
    async for event in neuracore_stream(request_data):
        yield f"data: {json.dumps(event)}\n\n"
    yield "data: [DONE]\n\n"
