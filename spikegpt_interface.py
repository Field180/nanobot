"""
SpikeGPT Interface for NeuraCore Integration
将 SpikeGPT 集成到 NeuraCore 系统的接口层
"""

import torch
import torch.nn as nn
import numpy as np
from typing import List, Dict, Optional, Tuple
import sys
import os

# 添加 spikegpt 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'spikegpt'))

from model import GPT, GPTConfig, Block
from model_run import RWKV_RNN


class SpikeGPTInterface:
    """
    SpikeGPT 接口类 - 连接 NeuraCore 感觉皮层和运动皮层
    
    架构流程:
    感觉皮层脉冲 (B, T, n_sensory) 
        -> SpikeGPT 核心 (联合+决策皮层) 
        -> 运动皮层脉冲特征 (B, T, n_motor)
    """
    
    def __init__(
        self,
        model_path: Optional[str] = None,
        vocab_size: int = 50277,
        ctx_len: int = 1024,
        n_layer: int = 18,
        n_embd: int = 768,
        n_sensory: int = 750000,  # NeuraCore 感觉皮层神经元数
        n_motor: int = 375000,    # NeuraCore 运动皮层神经元数
        device: str = 'cpu',
        dtype: str = 'fp32'
    ):
        """
        初始化 SpikeGPT 接口
        
        Args:
            model_path: 预训练模型路径 (.pth 或 .pt)
            vocab_size: 词表大小
            ctx_len: 上下文长度
            n_layer: 网络层数
            n_embd: 嵌入维度
            n_sensory: 感觉皮层维度
            n_motor: 运动皮层维度
            device: 运行设备 ('cpu' 或 'cuda')
            dtype: 数据类型 ('fp32', 'fp16', 'bf16')
        """
        self.device = device
        self.dtype = dtype
        self.vocab_size = vocab_size
        self.ctx_len = ctx_len
        self.n_embd = n_embd
        self.n_sensory = n_sensory
        self.n_motor = n_motor
        
        # 加载 tokenizer (使用 tiktoken 作为 GPT2 的替代)
        try:
            import tiktoken
            enc = tiktoken.get_encoding('gpt2')
            # 包装 tiktoken 编码器以兼容 transformers API
            self.tokenizer = type('TiktokenWrapper', (), {})()
            self.tokenizer.enc = enc
            self.tokenizer.n_vocab = enc.n_vocab
            self.tokenizer.vocab_size = enc.n_vocab
            self.tokenizer.eos_token_id = 50256  # GPT2 EOS token
            self.tokenizer.pad_token_id = 50256
            self.tokenizer.unk_token_id = 50256
            # 定义方法
            def encode_wrapper(text, add_special_tokens=False):
                return enc.encode(text)
            def decode_wrapper(ids):
                if isinstance(ids, torch.Tensor):
                    ids = ids.tolist()
                return enc.decode(ids)
            
            self.tokenizer.encode = encode_wrapper
            self.tokenizer.decode = decode_wrapper
            print(f"[SpikeGPT] ✅ Tiktoken GPT2 tokenizer 加载成功，词表大小: {enc.n_vocab}")
        except Exception as e:
            print(f"[SpikeGPT] ⚠️ 无法加载 tokenizer: {e}")
            self.tokenizer = None
        
        # 配置
        self.config = GPTConfig(
            vocab_size=vocab_size,
            ctx_len=ctx_len,
            model_type='RWKV',
            n_layer=n_layer,
            n_embd=n_embd
        )
        
        # 创建或加载模型
        # 转换为绝对路径
        if model_path:
            model_path = os.path.abspath(model_path)
        print(f"[SpikeGPT] Checking model_path: {model_path}")
        print(f"[SpikeGPT] model_path exists: {os.path.exists(model_path) if model_path else False}")
        
        # 始终创建 GPT 模型用于 forward() 方法
        self.model = GPT(self.config)
        
        if model_path and os.path.exists(model_path):
            print(f"[SpikeGPT] Loading pretrained RWKV_RNN from {model_path}")
            # 加载 RWKV_RNN 用于正确的文本生成
            self._load_rwkv_for_generation(model_path)
        else:
            print(f"[SpikeGPT] Creating new model (layers={n_layer}, embd={n_embd})")
            self.rwkv_model = None
            
        if device == 'cuda':
            self.model = self.model.cuda()
        
        # 感觉皮层到 SpikeGPT 的投影层
        # 将高维感觉脉冲映射到低维嵌入空间
        self.sensory_projection = nn.Sequential(
            nn.Linear(n_sensory, n_embd * 2),
            nn.LayerNorm(n_embd * 2),
            nn.GELU(),
            nn.Linear(n_embd * 2, n_embd)
        ).to(device)
        
        # SpikeGPT 到运动皮层的投影层
        # 将嵌入特征映射到运动皮层脉冲
        self.motor_projection = nn.Sequential(
            nn.Linear(n_embd, n_embd // 2),
            nn.LayerNorm(n_embd // 2),
            nn.GELU(),
            nn.Linear(n_embd // 2, n_motor)
        ).to(device)
        
        # 脉冲激活函数 (模拟 LIF 神经元)
        self.spike_activation = nn.Sequential(
            nn.LayerNorm(n_motor),
            nn.Sigmoid()  # 输出脉冲概率 0-1
        ).to(device)
        
        if self.model is not None:
            self.model.eval()
        print(f"[SpikeGPT] Interface initialized on {device}")
    
    def _load_rwkv_for_generation(self, model_path: str):
        """加载 RWKV_RNN 用于正确的文本生成"""
        try:
            import types
            args = types.SimpleNamespace()
            args.RUN_DEVICE = self.device
            args.FLOAT_MODE = "fp32"
            args.MODEL_NAME = model_path.replace('.pth', '')  # 去掉扩展名
            args.n_layer = self.config.n_layer
            args.n_embd = self.config.n_embd
            args.ctx_len = self.config.ctx_len
            args.vocab_size = self.config.vocab_size
            args.head_qk = 0
            args.pre_ffn = 0
            args.grad_cp = 0
            args.my_pos_emb = 0
            
            print(f"[SpikeGPT] Loading RWKV_RNN from: {args.MODEL_NAME}")
            
            self.rwkv_model = RWKV_RNN(args)
            self.rwkv_model.eval()
            print(f"[SpikeGPT] ✅ RWKV_RNN 加载成功，用于文本生成")
        except Exception as e:
            print(f"[SpikeGPT] ❌ RWKV_RNN 加载失败: {e}")
            import traceback
            traceback.print_exc()
            self.rwkv_model = None
        
    def _load_pretrained(self, model_path: str) -> GPT:
        """加载预训练权重，支持多种checkpoint格式"""
        print(f"[SpikeGPT] Loading pretrained weights from {model_path}")
        
        # 创建模型架构
        model = GPT(self.config)
        
        # 加载checkpoint
        checkpoint = torch.load(model_path, map_location=self.device)
        
        # 处理不同格式的checkpoint
        state_dict = None
        
        if isinstance(checkpoint, dict):
            # 检查常见的键名
            if 'model' in checkpoint:
                state_dict = checkpoint['model']
                print(f"[SpikeGPT] Found 'model' key in checkpoint")
            elif 'state_dict' in checkpoint:
                state_dict = checkpoint['state_dict']
                print(f"[SpikeGPT] Found 'state_dict' key in checkpoint")
            elif 'module' in checkpoint:
                state_dict = checkpoint['module']
                print(f"[SpikeGPT] Found 'module' key in checkpoint")
            else:
                # 可能是直接的state_dict
                state_dict = checkpoint
                print(f"[SpikeGPT] Using checkpoint as state_dict directly")
        else:
            state_dict = checkpoint
        
        # 处理键名前缀（如 'module.' 来自DataParallel）
        new_state_dict = {}
        for k, v in state_dict.items():
            # 移除 'module.' 前缀
            name = k.replace('module.', '')
            # 处理 'transformer.' 前缀（如果有）
            name = name.replace('transformer.', '')
            new_state_dict[name] = v
        
        # 尝试加载，允许部分权重不匹配（用于微调场景）
        missing_keys, unexpected_keys = model.load_state_dict(new_state_dict, strict=False)
        
        if missing_keys:
            print(f"[SpikeGPT] Warning: Missing keys: {len(missing_keys)}")
        if unexpected_keys:
            print(f"[SpikeGPT] Warning: Unexpected keys: {len(unexpected_keys)}")
        
        loaded_params = len(new_state_dict) - len(unexpected_keys)
        total_params = sum(p.numel() for p in model.parameters())
        
        print(f"[SpikeGPT] Successfully loaded {loaded_params}/{len(new_state_dict)} parameters")
        print(f"[SpikeGPT] Model has {total_params:,} total parameters")
        
        if self.device == 'cuda':
            model = model.cuda()
            
        return model
    
    def forward(
        self, 
        sensory_spikes: torch.Tensor,
        return_motor_spikes: bool = True,
        return_logits: bool = False
    ) -> Dict[str, torch.Tensor]:
        """
        前向推理
        
        Args:
            sensory_spikes: 感觉皮层脉冲 (B, T, n_sensory)
                           可以是二进制脉冲 (0/1) 或连续值 (发放率)
            return_motor_spikes: 是否返回运动皮层脉冲
            return_logits: 是否返回语言模型 logits
        
        Returns:
            Dict 包含:
                - 'motor_spikes': 运动皮层脉冲 (B, T, n_motor)
                - 'hidden_states': 隐藏层状态 (B, T, n_embd)
                - 'logits': 语言模型输出 (B, T, vocab_size) [可选]
        """
        B, T, _ = sensory_spikes.shape
        assert T <= self.ctx_len, f"序列长度 {T} 超过上下文限制 {self.ctx_len}"
        
        # 1. 感觉皮层 -> SpikeGPT 嵌入空间
        x = self.sensory_projection(sensory_spikes)  # (B, T, n_embd)
        
        # 2. 通过 SpikeGPT 核心 (联合+决策皮层)
        with torch.no_grad():
            # 使用 atan 激活 (SpikeGPT 原版)
            x = self.model.atan(x)
            x = self.model.blocks(x)  # 通过所有 Block 层
            x = self.model.ln_out(x)   # 最终层归一化
            
            # 可选: 获取语言模型 logits
            logits = None
            if return_logits:
                logits = self.model.head(x)  # (B, T, vocab_size)
        
        # 3. SpikeGPT 输出 -> 运动皮层
        motor_activation = self.motor_projection(x)  # (B, T, n_motor)
        
        # 4. 转换为脉冲 (发放率)
        motor_spike_rates = self.spike_activation(motor_activation)  # (B, T, n_motor)
        
        # 5. 二值化脉冲 (可选，用于真实脉冲输出)
        if return_motor_spikes:
            # 概率采样生成二进制脉冲
            motor_spikes = torch.bernoulli(motor_spike_rates)
        else:
            motor_spikes = motor_spike_rates
        
        result = {
            'motor_spikes': motor_spikes,
            'hidden_states': x,
            'motor_spike_rates': motor_spike_rates
        }
        
        if logits is not None:
            result['logits'] = logits
            
        return result
    
    def generate(
        self,
        prompt_text: str,
        max_new_tokens: int = 50,
        temperature: float = 0.8,
        top_k: int = 50,
        stop_on_eos: bool = True
    ) -> Dict:
        """
        自回归文本生成 - 使用 GPT2 tokenizer
        
        Args:
            prompt_text: 输入提示文本
            max_new_tokens: 最大新生成 token 数
            temperature: 采样温度
            top_k: top-k 采样限制
            stop_on_eos: 遇到 EOS token 时是否停止
        
        Returns:
            包含生成文本和 token IDs 的字典
        """
        if self.tokenizer is None:
            print("[SpikeGPT] 错误: tokenizer 未加载")
            return {'generated_text': '', 'generated_ids': [], 'error': 'No tokenizer'}
        
        if self.rwkv_model is None:
            print("[SpikeGPT] 错误: RWKV_RNN 模型未加载，无法生成文本")
            return {'generated_text': '', 'generated_ids': [], 'error': 'RWKV model not loaded'}
        
        self.rwkv_model.eval()
        
        # 编码输入
        token_ids = self.tokenizer.encode(prompt_text)
        src_len = len(token_ids)
        prompt_length = src_len
        
        # RNN 状态初始化（与官方代码一致）
        init_state = None
        init_out = None
        state = None
        mem1 = None
        mem2 = None
        
        generated_ids = token_ids.copy()
        
        with torch.no_grad():
            # 预处理 prompt（与官方 run.py 一致）
            for i in range(src_len):
                x = generated_ids[: i + 1]
                if i == src_len - 1:
                    # 最后一个 token，获取输出
                    init_out, init_state, mem1, mem2 = self.rwkv_model.forward(x, init_state, mem1, mem2)
                else:
                    # 中间 token，只更新状态
                    init_state, mem1, mem2 = self.rwkv_model.forward(x, init_state, mem1, mem2, preprocess_only=True)
            
            # 生成新 token
            for i in range(max_new_tokens):
                x = generated_ids[: src_len + i + 1]
                x = x[-self.ctx_len:]  # 截断到上下文长度
                
                if i == 0:
                    out = init_out.clone()
                    state = init_state.clone()
                else:
                    out, state, mem1, mem2 = self.rwkv_model.forward(x, state, mem1, mem2)
                
                # 禁用 token 0 (与官方代码一致)
                out[0] = -999999999
                
                # 使用官方的 sample_logits 逻辑
                probs = torch.softmax(out, dim=-1)
                
                # top_p = 0.7 (官方默认值)
                top_p = 0.7
                sorted_probs, sorted_indices = torch.sort(probs, descending=True)
                cumulative_probs = torch.cumsum(sorted_probs, dim=-1).cpu().numpy()
                cutoff = float(sorted_probs[np.argmax(cumulative_probs > top_p)])
                
                probs[probs < cutoff] = 0
                
                # 温度缩放 (与官方一致)
                if temperature != 1.0:
                    probs = probs.pow(1.0 / temperature)
                
                # 采样
                next_token = torch.multinomial(probs, num_samples=1).item()
                
                generated_ids.append(next_token)
                
                # 检查 EOS
                if stop_on_eos and next_token == 50256:
                    break
        
        # 解码 - 逐token解码并跳过无效字符（与官方代码一致）
        new_token_ids = generated_ids[prompt_length:]
        
        # 逐个解码新token，过滤无效字符
        generated_text = ""
        for i, token_id in enumerate(new_token_ids):
            # 解码单个token
            try:
                char = self.tokenizer.decode([token_id])
                # 检查是否是无效UTF8字符
                if '\ufffd' not in char:
                    generated_text += char
            except:
                pass  # 跳过解码失败的token
        
        # 完整文本解码
        full_text = self.tokenizer.decode(generated_ids)
        
        return {
            'generated_text': generated_text,
            'full_text': full_text,
            'prompt': prompt_text,
            'generated_ids': new_token_ids,
            'num_tokens_generated': len(new_token_ids)
        }
    
    def generate_text(
        self,
        prompt_sensory: torch.Tensor,
        max_length: int = 100,
        temperature: float = 1.0,
        top_k: int = 50
    ) -> List[int]:
        """
        自回归文本生成（基于脉冲输入）
        
        Args:
            prompt_sensory: 提示词的脉冲表示 (1, T, n_sensory)
            max_length: 最大生成长度
            temperature: 采样温度
            top_k: top-k 采样
        
        Returns:
            生成的 token ID 列表
        """
        self.model.eval()
        generated_tokens = []
        
        with torch.no_grad():
            # 初始前向
            current_input = prompt_sensory
            
            for _ in range(max_length):
                # 前向推理
                outputs = self.forward(
                    current_input,
                    return_motor_spikes=False,
                    return_logits=True
                )
                
                # 获取下一个 token 的概率
                logits = outputs['logits'][:, -1, :] / temperature  # (B, vocab_size)
                
                # Top-k 采样
                if top_k > 0:
                    indices_to_remove = logits < torch.topk(logits, top_k)[0][..., -1, None]
                    logits[indices_to_remove] = float('-inf')
                
                probs = torch.softmax(logits, dim=-1)
                next_token = torch.multinomial(probs, num_samples=1).item()
                
                generated_tokens.append(next_token)
                
                # 检查 EOS
                if self.tokenizer and next_token == self.tokenizer.eos_token_id:
                    break
                
                # 将生成的 token 转换回脉冲输入
                if hasattr(self.model, 'emb'):
                    next_embedding = self.model.emb(
                        torch.tensor([[next_token]], device=self.device)
                    )
                    # 扩展到感觉皮层维度
                    next_sensory = next_embedding.squeeze(1).expand(-1, self.n_sensory)
                    
                    # 更新输入（滑动窗口）
                    if current_input.shape[1] >= self.ctx_len:
                        current_input = torch.cat([
                            current_input[:, 1:, :],
                            next_sensory.unsqueeze(1)
                        ], dim=1)
                    else:
                        current_input = torch.cat([
                            current_input,
                            next_sensory.unsqueeze(1)
                        ], dim=1)
                else:
                    break
        
        return generated_tokens
    
    def encode_text_to_spikes(
        self,
        text: str,
        tokenizer=None
    ) -> torch.Tensor:
        """
        将文本编码为感觉皮层脉冲
        
        使用 GPT2 tokenizer 将文本转换为 token IDs，
        然后将 token IDs 映射到感觉皮层脉冲模式
        
        Args:
            text: 输入文本
            tokenizer: 可选的分词器（如果为None则使用 self.tokenizer）
        
        Returns:
            感觉皮层脉冲张量 (1, T, n_sensory)
        """
        # 使用 GPT2 tokenizer 编码文本
        tok = tokenizer if tokenizer else self.tokenizer
        if tok is None:
            # 回退到字符级编码
            tokens = [ord(c) % 256 for c in text[:self.ctx_len]]
        else:
            # 使用 tokenizer 获取 token IDs
            token_ids = tok.encode(text, add_special_tokens=False)
            tokens = token_ids[:self.ctx_len]  # 截断到上下文长度
        
        T = len(tokens)
        
        # 创建脉冲张量
        spikes = torch.zeros(1, T, self.n_sensory, device=self.device)
        
        # 每个 token 激活一组神经元
        for t, token in enumerate(tokens):
            # 使用散列将 token 映射到神经元群
            neuron_indices = [(token * 7 + i * 13) % self.n_sensory 
                            for i in range(min(self.n_sensory // 256, 100))]
            spikes[0, t, neuron_indices] = 1.0
        
        return spikes
    
    def decode_spikes_to_text(
        self,
        motor_spikes: torch.Tensor,
        tokenizer=None,
        temperature: float = 0.8,
        top_k: int = 50
    ) -> Dict:
        """
        将运动皮层脉冲解码为文本
        
        使用运动皮层脉冲特征采样生成 token IDs，
        然后使用 tokenizer 解码为文本
        
        Args:
            motor_spikes: 运动皮层脉冲 (B, T, n_motor)
            tokenizer: 可选的分词器
            temperature: 采样温度
            top_k: top-k 采样限制
        
        Returns:
            包含生成文本和 token IDs 的字典
        """
        tok = tokenizer if tokenizer else self.tokenizer
        
        B, T, _ = motor_spikes.shape
        
        # 将运动皮层脉冲映射到 logits
        # 使用一个简单的线性投影：运动脉冲 -> 词汇表
        if not hasattr(self, '_motor_to_vocab'):
            # 动态创建投影层
            self._motor_to_vocab = nn.Linear(
                self.n_motor, 
                self.vocab_size if tok is None else len(tok)
            ).to(self.device)
        
        with torch.no_grad():
            # 对每个时间步的脉冲应用投影
            logits = self._motor_to_vocab(motor_spikes)  # (B, T, vocab_size)
            
            # 温度缩放
            logits = logits / temperature
            
            # Top-k 采样
            if top_k > 0:
                for b in range(B):
                    for t in range(T):
                        top_k_values, top_k_indices = torch.topk(logits[b, t], top_k)
                        logits[b, t, :] = float('-inf')
                        logits[b, t, top_k_indices] = top_k_values
            
            # 采样 token IDs
            probs = torch.softmax(logits, dim=-1)
            generated_ids = torch.multinomial(
                probs.view(-1, probs.size(-1)), 
                num_samples=1
            ).view(B, T)
        
        # 解码为文本
        if tok is not None:
            texts = []
            for ids in generated_ids:
                # 过滤掉特殊 token
                valid_ids = [id.item() for id in ids if id.item() not in [tok.eos_token_id, tok.pad_token_id, tok.unk_token_id]]
                text = tok.decode(valid_ids)
                texts.append(text)
            generated_text = texts[0] if B == 1 else texts
        else:
            # 回退：显示 token IDs
            generated_text = " ".join([f"Token_{id.item()}" for id in generated_ids[0]])
        
        return {
            'generated_ids': generated_ids,
            'generated_text': generated_text,
            'spike_rates': motor_spikes.mean(dim=-1)
        }
    
    def get_model_info(self) -> Dict:
        """获取模型信息"""
        total_params = sum(p.numel() for p in self.model.parameters())
        trainable_params = sum(p.numel() for p in self.model.parameters() if p.requires_grad)
        
        return {
            'total_parameters': total_params,
            'trainable_parameters': trainable_params,
            'n_layer': self.config.n_layer,
            'n_embd': self.config.n_embd,
            'ctx_len': self.config.ctx_len,
            'vocab_size': self.config.vocab_size,
            'device': self.device,
            'model_size_mb': total_params * 4 / 1024 / 1024  # fp32
        }
    
    def save(self, path: str):
        """保存接口状态"""
        torch.save({
            'model_state': self.model.state_dict(),
            'sensory_projection': self.sensory_projection.state_dict(),
            'motor_projection': self.motor_projection.state_dict(),
            'spike_activation': self.spike_activation.state_dict(),
            'config': self.config
        }, path)
        print(f"[SpikeGPT] Saved to {path}")
    
    def load(self, path: str):
        """加载接口状态"""
        checkpoint = torch.load(path, map_location=self.device)
        self.model.load_state_dict(checkpoint['model_state'])
        self.sensory_projection.load_state_dict(checkpoint['sensory_projection'])
        self.motor_projection.load_state_dict(checkpoint['motor_projection'])
        self.spike_activation.load_state_dict(checkpoint['spike_activation'])
        print(f"[SpikeGPT] Loaded from {path}")


class NeuraCoreSpikeGPTBridge:
    """
    NeuraCore 与 SpikeGPT 的桥接层
    
    将 NeuraCore 的 300万神经元分层结构映射到 SpikeGPT
    """
    
    def __init__(
        self,
        sensory_size: int = 750000,
        association_size: int = 1500000,
        decision_size: int = 750000,
        motor_size: int = 375000,
        spikegpt_interface: Optional[SpikeGPTInterface] = None
    ):
        """
        初始化桥接层
        
        Args:
            sensory_size: 感觉皮层神经元数
            association_size: 联合皮层神经元数  
            decision_size: 决策皮层神经元数
            motor_size: 运动皮层神经元数
            spikegpt_interface: SpikeGPT 接口实例
        """
        self.sensory_size = sensory_size
        self.association_size = association_size
        self.decision_size = decision_size
        self.motor_size = motor_size
        
        # 创建或复用 SpikeGPT 接口
        if spikegpt_interface is None:
            # 小规模模型用于测试
            self.interface = SpikeGPTInterface(
                n_layer=6,
                n_embd=512,
                n_sensory=sensory_size,
                n_motor=motor_size,
                device='cpu'
            )
        else:
            self.interface = spikegpt_interface
        
        print("[Bridge] NeuraCore-SpikeGPT Bridge initialized")
    
    def process(
        self,
        sensory_input: torch.Tensor,
        task_type: str = 'text_generation'
    ) -> Dict[str, torch.Tensor]:
        """
        处理 NeuraCore 输入
        
        Args:
            sensory_input: 感觉皮层输入脉冲 (B, T, sensory_size)
            task_type: 任务类型 ('text_generation', 'tool_use', 'reasoning')
        
        Returns:
            处理结果，包含运动皮层输出
        """
        # 通过 SpikeGPT 处理
        outputs = self.interface.forward(sensory_input)
        
        # 根据任务类型后处理
        if task_type == 'text_generation':
            # 文本生成任务
            return {
                'motor_output': outputs['motor_spikes'],
                'text_logits': outputs.get('logits'),
                'confidence': outputs['motor_spike_rates'].mean()
            }
        elif task_type == 'tool_use':
            # 工具调用任务
            # 运动皮层前部映射到工具选择
            tool_selection = outputs['motor_spikes'][:, :, :1000].sum(dim=-1)
            return {
                'motor_output': outputs['motor_spikes'],
                'tool_selection': tool_selection,
                'confidence': outputs['motor_spike_rates'].mean()
            }
        else:
            return outputs


# 便捷函数
def create_spikegpt_for_neuracore(
    model_path: Optional[str] = None,
    use_small_model: bool = False,  # 改为默认False以匹配预训练权重
    device: str = 'cpu'
) -> SpikeGPTInterface:
    """
    为 NeuraCore 创建 SpikeGPT 接口
    
    Args:
        model_path: 预训练模型路径
        use_small_model: 使用小规模模型 (适合 CPU 快速测试，但无法加载预训练权重)
        device: 运行设备
    
    Returns:
        SpikeGPTInterface 实例
    """
    if use_small_model:
        # 小规模模型配置 (适合快速测试，但无法加载预训练权重)
        print("[SpikeGPT] 使用小规模模型配置 (n_layer=4, n_embd=256)")
        print("[Warning] 小规模模型无法加载 216M 预训练权重，将使用随机初始化")
        return SpikeGPTInterface(
            model_path=None,  # 小规模模型不使用预训练权重
            vocab_size=50277,
            ctx_len=512,
            n_layer=4,
            n_embd=256,
            n_sensory=10000,   # 简化版
            n_motor=5000,
            device=device
        )
    else:
        # 完整规模模型 - 匹配预训练权重架构
        print("[SpikeGPT] 使用完整模型配置 (n_layer=18, n_embd=768)")
        return SpikeGPTInterface(
            model_path=model_path,
            vocab_size=50277,
            ctx_len=1024,
            n_layer=18,        # 匹配预训练权重
            n_embd=768,        # 匹配预训练权重
            n_sensory=10000,   # CPU友好，可根据需要调整
            n_motor=5000,      # CPU友好
            device=device
        )


if __name__ == '__main__':
    # 测试接口
    print("Testing SpikeGPTInterface...")
    
    # 创建小规模接口
    interface = create_spikegpt_for_neuracore(use_small_model=True, device='cpu')
    
    # 打印模型信息
    info = interface.get_model_info()
    print(f"\nModel Info:")
    for k, v in info.items():
        print(f"  {k}: {v}")
    
    # 测试编码
    text = "Hello NeuraCore"
    sensory = interface.encode_text_to_spikes(text)
    print(f"\nEncoded '{text}' to sensory spikes: {sensory.shape}")
    
    # 测试前向
    outputs = interface.forward(sensory)
    print(f"\nForward output shapes:")
    for k, v in outputs.items():
        if isinstance(v, torch.Tensor):
            print(f"  {k}: {v.shape}")
    
    print("\nTest completed!")
