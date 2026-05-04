#!/usr/bin/env python3
"""
统一神经模型接口
支持: SpikeGPT-216M, BrainTransformers-3B, SpikingBrain-7B
"""

import os
import sys
import torch
from typing import Dict, Optional, List

# 支持的模型配置
SUPPORTED_MODELS = {
    "spikegpt-216m": {
        "name": "SpikeGPT-216M",
        "path": "/home/field/.nanobot/workspace/web_ui/spikegpt/pretrained/SpikeGPT-216M.pth",
        "type": "rwkv",
        "vocab_size": 50277,
        "n_layer": 18,
        "n_embd": 768,
        "ctx_len": 1024,
        "description": "原始脉冲神经网络模型，216M参数"
    },
    "braintransformers-3b": {
        "name": "BrainTransformers-3B-Chat",
        "path": "/home/field/.nanobot/workspace/web_ui/models/braintransformers-3b",
        "type": "transformers",
        "model_id": "LumenscopeAI/BrainTransformers-3B-Chat",
        "description": "3B参数对话优化模型"
    },
    "spikingbrain-7b": {
        "name": "SpikingBrain-7B-SFT",
        "path": "/home/field/.nanobot/workspace/web_ui/models/spikingbrain-7b/Panyuqi/V1-7B-sft-s3-reasoning",
        "type": "modelscope",
        "model_id": "Panyuqi/V1-7B-sft-s3-reasoning",
        "description": "7B参数指令微调模型"
    }
}

class UnifiedNeuralModel:
    """统一神经模型接口"""
    
    def __init__(self, model_key: str = "spikegpt-216m", device: str = "cpu"):
        """
        初始化统一模型接口
        
        Args:
            model_key: 模型标识 (spikegpt-216m, braintransformers-3b, spikingbrain-7b)
            device: 运行设备 (cpu/cuda)
        """
        self.model_key = model_key
        self.device = device
        self.config = SUPPORTED_MODELS.get(model_key)
        
        if not self.config:
            raise ValueError(f"不支持的模型: {model_key}. 可用: {list(SUPPORTED_MODELS.keys())}")
        
        self.model = None
        self.tokenizer = None
        self.model_type = self.config["type"]
        
        print(f"[统一模型] 初始化 {self.config['name']}")
        print(f"[统一模型] 类型: {self.model_type}")
        
        # 加载模型
        self._load_model()
    
    def _load_model(self):
        """根据类型加载模型"""
        if self.model_type == "rwkv":
            self._load_rwkv_model()
        elif self.model_type == "transformers":
            self._load_transformers_model()
        elif self.model_type == "modelscope":
            self._load_modelscope_model()
    
    def _load_rwkv_model(self):
        """加载 SpikeGPT/RWKV 模型"""
        try:
            sys.path.insert(0, '/home/field/SpikeGPT')
            os.environ['RWKV_JIT_ON'] = '0'
            os.environ['RWKV_CUDA_ON'] = '0'
            
            from spikegpt_interface import create_spikegpt_for_neuracore
            
            self.model = create_spikegpt_for_neuracore(
                model_path=self.config["path"],
                use_small_model=False,
                device=self.device
            )
            
            if self.model.rwkv_model is None:
                print(f"[统一模型] ⚠️ RWKV_RNN 未加载，将使用 GPT 模型")
            else:
                print(f"[统一模型] ✅ RWKV_RNN 加载成功")
            
            self.tokenizer = self.model.tokenizer
            
        except Exception as e:
            print(f"[统一模型] ❌ SpikeGPT 加载失败: {e}")
            raise
    
    def _load_transformers_model(self):
        """加载 HuggingFace Transformers 模型"""
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            
            if not os.path.exists(self.config["path"]):
                print(f"[统一模型] ⚠️ 模型未下载: {self.config['path']}")
                print(f"[统一模型] 请先运行: python3 download_braintransformers.py")
                raise FileNotFoundError(f"模型未找到: {self.config['path']}")
            
            print(f"[统一模型] 正在加载 {self.config['name']}...")
            
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.config["path"],
                trust_remote_code=True
            )
            
            self.model = AutoModelForCausalLM.from_pretrained(
                self.config["path"],
                trust_remote_code=True,
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
                device_map="auto" if self.device == "cuda" else None,
                low_cpu_mem_usage=True
            )
            
            if self.device == "cpu":
                self.model = self.model.to("cpu")
            
            print(f"[统一模型] ✅ {self.config['name']} 加载成功")
            
        except Exception as e:
            print(f"[统一模型] ❌ Transformers 模型加载失败: {e}")
            raise
    
    def _load_modelscope_model(self):
        """加载 ModelScope 模型"""
        try:
            from modelscope import AutoModelForCausalLM, AutoTokenizer
            
            if not os.path.exists(self.config["path"]):
                print(f"[统一模型] ⚠️ 模型未下载: {self.config['path']}")
                print(f"[统一模型] 正在尝试从 ModelScope 下载...")
                # 这里可以添加自动下载逻辑
                raise FileNotFoundError(f"模型未找到: {self.config['path']}")
            
            print(f"[统一模型] 正在加载 {self.config['name']}...")
            
            self.tokenizer = AutoTokenizer.from_pretrained(self.config["path"])
            self.model = AutoModelForCausalLM.from_pretrained(
                self.config["path"],
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
                device_map="auto" if self.device == "cuda" else None
            )
            
            print(f"[统一模型] ✅ {self.config['name']} 加载成功")
            
        except Exception as e:
            print(f"[统一模型] ❌ ModelScope 模型加载失败: {e}")
            raise
    
    def generate(self, prompt: str, max_tokens: int = 50, temperature: float = 0.8, **kwargs) -> str:
        """
        统一生成接口
        
        Args:
            prompt: 输入提示
            max_tokens: 最大生成token数
            temperature: 采样温度
            
        Returns:
            生成的文本
        """
        if self.model_type == "rwkv":
            return self._generate_rwkv(prompt, max_tokens, temperature, **kwargs)
        else:
            return self._generate_transformers(prompt, max_tokens, temperature, **kwargs)
    
    def _generate_rwkv(self, prompt: str, max_tokens: int, temperature: float, **kwargs) -> str:
        """使用 RWKV 模型生成"""
        result = self.model.generate(
            prompt_text=prompt,
            max_new_tokens=max_tokens,
            temperature=temperature,
            top_k=0,
            stop_on_eos=True
        )
        return result.get('generated_text', '')
    
    def _generate_transformers(self, prompt: str, max_tokens: int, temperature: float, **kwargs) -> str:
        """使用 Transformers 模型生成"""
        # 构建对话格式
        if hasattr(self.tokenizer, 'apply_chat_template'):
            messages = [{"role": "user", "content": prompt}]
            input_text = self.tokenizer.apply_chat_template(
                messages, 
                tokenize=False, 
                add_generation_prompt=True
            )
        else:
            input_text = f"User: {prompt}\nAssistant:"
        
        inputs = self.tokenizer(input_text, return_tensors="pt")
        
        if self.device == "cuda":
            inputs = inputs.to("cuda")
        
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=temperature,
                do_sample=True,
                top_p=0.9,
                pad_token_id=self.tokenizer.eos_token_id
            )
        
        response = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        
        # 提取生成的部分
        if input_text in response:
            return response.replace(input_text, "").strip()
        return response.strip()
    
    def get_model_info(self) -> Dict:
        """获取模型信息"""
        info = {
            "name": self.config["name"],
            "type": self.model_type,
            "device": self.device,
            "description": self.config.get("description", "")
        }
        
        if self.model_type == "rwkv":
            info["parameters"] = "215M"
            info["loaded"] = self.model.rwkv_model is not None
        else:
            # 尝试获取参数数量
            try:
                params = sum(p.numel() for p in self.model.parameters())
                info["parameters"] = f"{params/1e9:.1f}B" if params > 1e9 else f"{params/1e6:.1f}M"
            except:
                info["parameters"] = "未知"
        
        return info


def create_unified_model(model_key: str = "spikegpt-216m", device: str = "cpu") -> UnifiedNeuralModel:
    """
    创建统一模型接口的便捷函数
    
    Args:
        model_key: 模型标识
        device: 运行设备
        
    Returns:
        UnifiedNeuralModel 实例
    """
    return UnifiedNeuralModel(model_key=model_key, device=device)


# 可用模型列表
def list_available_models():
    """列出所有支持的模型"""
    print("支持的模型列表:")
    print("=" * 60)
    for key, config in SUPPORTED_MODELS.items():
        exists = "✅" if os.path.exists(config.get("path", "")) else "❌"
        print(f"{exists} {key}")
        print(f"   名称: {config['name']}")
        print(f"   类型: {config['type']}")
        print(f"   描述: {config.get('description', 'N/A')}")
        print()


if __name__ == "__main__":
    # 测试代码
    print("=" * 60)
    print("统一神经模型接口测试")
    print("=" * 60)
    
    # 列出可用模型
    list_available_models()
    
    # 尝试加载默认模型
    try:
        model = create_unified_model("spikegpt-216m")
        info = model.get_model_info()
        print(f"\n已加载模型: {info['name']}")
        print(f"参数: {info.get('parameters', 'N/A')}")
        
        # 测试生成
        test_prompt = "Hello, how are you?"
        print(f"\n测试生成: {test_prompt}")
        response = model.generate(test_prompt, max_tokens=20)
        print(f"回复: {response}")
        
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
