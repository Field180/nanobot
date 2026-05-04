"""
SpikeGPT-Style RWKV-SNN Module for NeuraCore
基于UCSC SpikeGPT论文的RWKV线性注意力机制实现

核心创新：
- RWKV线性注意力: O(N)复杂度，支持长序列
- 脉冲驱动: 二值脉冲激活，保持稀疏性
- 时间混合: token序列按时间步流式处理
- 通道混合: 跨维度特征融合

论文参考: https://arxiv.org/abs/2302.13948 (RWKV)
开源代码: https://github.com/ridgerchu/SpikeGPT
"""

import numpy as np
from typing import Dict, List, Tuple, Optional, Any
import torch
import torch.nn as nn
import torch.nn.functional as F


class RWKVTimeMix(nn.Module):
    """
    RWKV时间混合模块 - 替代Transformer自注意力
    
    关键公式（简化版）:
    wkv_t = (sum_{i=1}^{t} e^{-(t-1-i)w + k_i} v_i) / (sum_{i=1}^{t} e^{-(t-1-i)w + k_i})
    
    复杂度: O(N) 每token，而非O(N²)
    """
    
    def __init__(self, n_embd: int, n_layer: int, layer_id: int):
        super().__init__()
        self.layer_id = layer_id
        self.n_embd = n_embd
        
        # 时间衰减参数（可学习）
        self.time_decay = nn.Parameter(torch.ones(n_embd) * 0.5)
        
        # 时间混合的初始偏移
        self.time_first = nn.Parameter(torch.ones(n_embd) * 0.3)
        
        # 线性投影层
        self.key = nn.Linear(n_embd, n_embd, bias=False)
        self.value = nn.Linear(n_embd, n_embd, bias=False)
        self.receptance = nn.Linear(n_embd, n_embd, bias=False)
        self.output = nn.Linear(n_embd, n_embd, bias=False)
        
    def forward(self, x: torch.Tensor, state: Optional[torch.Tensor] = None):
        """
        前向传播
        
        Args:
            x: 输入 [batch, seq_len, n_embd]
            state: 前一时刻状态 (用于递归)
            
        Returns:
            output: [batch, seq_len, n_embd]
            new_state: 更新后的状态
        """
        B, T, C = x.size()
        
        # 计算R, K, V
        r = torch.sigmoid(self.receptance(x))  # Receptance: 接受度门控
        k = self.key(x)  # Key
        v = self.value(x)  # Value
        
        # RWKV核心计算（简化线性注意力）
        # 使用指数衰减的累积和
        w = -torch.exp(self.time_decay)  # 衰减因子 [C]
        
        # 初始化状态
        if state is None:
            num_state = torch.zeros(B, C)
            den_state = torch.zeros(B, C)
            max_state = torch.zeros(B, C)
        else:
            num_state, den_state, max_state = state
        
        outputs = []
        for t in range(T):
            # 当前token
            kt = k[:, t]  # [B, C]
            vt = v[:, t]  # [B, C]
            rt = r[:, t]  # [B, C]
            
            # 更新max（数值稳定性）
            max_prev = max_state
            max_curr = torch.maximum(max_prev, kt)
            
            # 衰减并累积
            exp_prev = torch.exp(max_prev - max_curr) * den_state
            exp_curr = torch.exp(kt - max_curr)
            
            # 更新分子和分母
            num_state = torch.exp(w) * num_state * exp_prev + exp_curr * vt
            den_state = torch.exp(w) * den_state * exp_prev + exp_curr
            max_state = max_curr
            
            # 计算wkv
            wkv = num_state / (den_state + 1e-6)
            
            # 门控输出
            out = rt * wkv
            outputs.append(out)
        
        output = torch.stack(outputs, dim=1)  # [B, T, C]
        output = self.output(output)
        
        return output, (num_state, den_state, max_state)


class RWKVChannelMix(nn.Module):
    """
    RWKV通道混合模块 - 跨维度特征融合
    类似前馈网络，但采用特殊的门控机制
    """
    
    def __init__(self, n_embd: int, n_layer: int, layer_id: int):
        super().__init__()
        self.layer_id = layer_id
        self.n_embd = n_embd
        
        # 通道混合参数
        self.time_mix_k = nn.Parameter(torch.ones(n_embd) * 0.5)
        self.time_mix_r = nn.Parameter(torch.ones(n_embd) * 0.5)
        
        # 线性层
        self.key = nn.Linear(n_embd, n_embd * 4, bias=False)
        self.receptance = nn.Linear(n_embd, n_embd, bias=False)
        self.value = nn.Linear(n_embd * 4, n_embd, bias=False)
        
    def forward(self, x: torch.Tensor, state: Optional[torch.Tensor] = None):
        """
        Args:
            x: [batch, seq_len, n_embd]
            state: 前一时刻输入
        """
        B, T, C = x.size()
        
        if state is None:
            x_prev = torch.zeros(B, C)
        else:
            x_prev = state
        
        outputs = []
        for t in range(T):
            xt = x[:, t]
            
            # 时间混合（当前与前一时刻的插值）
            xk = xt * self.time_mix_k + x_prev * (1 - self.time_mix_k)
            xr = xt * self.time_mix_r + x_prev * (1 - self.time_mix_r)
            
            # 计算K和R
            k = torch.relu(self.key(xk)) ** 2  # 平方ReLU
            r = torch.sigmoid(self.receptance(xr))
            
            # 门控输出
            kv = self.value(k)
            out = r * kv
            
            outputs.append(out)
            x_prev = xt
        
        output = torch.stack(outputs, dim=1)
        return output, x_prev


class SpikingRWKVLayer(nn.Module):
    """
    脉冲驱动的RWKV层
    
    将连续值转换为脉冲序列，保持稀疏性和事件驱动特性
    """
    
    def __init__(self, n_embd: int, n_layer: int, layer_id: int, 
                 threshold: float = 1.0, decay: float = 0.5):
        super().__init__()
        self.layer_id = layer_id
        self.n_embd = n_embd
        self.threshold = threshold
        self.decay = decay
        
        # RWKV子模块
        self.timemix = RWKVTimeMix(n_embd, n_layer, layer_id)
        self.channelmix = RWKVChannelMix(n_embd, n_layer, layer_id)
        
        # 层归一化
        self.ln1 = nn.LayerNorm(n_embd)
        self.ln2 = nn.LayerNorm(n_embd)
        
        # LIF神经元参数
        self.v_threshold = threshold
        self.tau = decay
        
    def lif_spike(self, x: torch.Tensor, v_mem: torch.Tensor):
        """
        Leaky Integrate-and-Fire神经元
        
        Args:
            x: 输入电流 [B, C]
            v_mem: 膜电位 [B, C]
            
        Returns:
            spike: 脉冲输出 {0, 1}
            v_mem_new: 更新后的膜电位
        """
        # 更新膜电位
        v_mem = v_mem * self.tau + x
        
        # 发放脉冲
        spike = (v_mem >= self.v_threshold).float()
        
        # 重置
        v_mem = v_mem * (1 - spike)
        
        return spike, v_mem
    
    def forward(self, x: torch.Tensor, 
                time_state: Optional[torch.Tensor] = None,
                channel_state: Optional[torch.Tensor] = None,
                v_mem: Optional[torch.Tensor] = None):
        """
        Args:
            x: 输入脉冲序列 [B, T, C]（二值或连续）
            time_state: 时间混合状态
            channel_state: 通道混合状态
            v_mem: 膜电位状态
            
        Returns:
            spike_output: 输出脉冲 [B, T, C]
            states: 更新后的状态元组
        """
        B, T, C = x.size()
        
        if v_mem is None:
            v_mem = torch.zeros(B, C)
        
        # 时间混合（残差连接）
        x_norm = self.ln1(x)
        time_out, new_time_state = self.timemix(x_norm, time_state)
        x = x + time_out
        
        # 通道混合（残差连接）
        x_norm = self.ln2(x)
        channel_out, new_channel_state = self.channelmix(x_norm, channel_state)
        x = x + channel_out
        
        # 转换为脉冲输出
        spikes = []
        for t in range(T):
            spike, v_mem = self.lif_spike(x[:, t], v_mem)
            spikes.append(spike)
        
        spike_output = torch.stack(spikes, dim=1)
        
        return spike_output, (new_time_state, new_channel_state, v_mem)


class SpikeGPTLanguageModel(nn.Module):
    """
    SpikeGPT风格语言模型 - 纯脉冲驱动的生成模型
    
    架构:
    - Token嵌入 → 脉冲编码
    - N层SpikingRWKVLayer堆叠
    - 脉冲解码 → Token预测
    """
    
    def __init__(self, vocab_size: int = 50000, n_embd: int = 768,
                 n_layer: int = 12, n_motor: int = 375000):
        super().__init__()
        
        self.vocab_size = vocab_size
        self.n_embd = n_embd
        self.n_layer = n_layer
        
        # 词嵌入层
        self.embedding = nn.Embedding(vocab_size, n_embd)
        
        # SpikeGPT层堆叠
        self.blocks = nn.ModuleList([
            SpikingRWKVLayer(n_embd, n_layer, i)
            for i in range(n_layer)
        ])
        
        # 最终层归一化
        self.ln_f = nn.LayerNorm(n_embd)
        
        # 语言建模头
        self.lm_head = nn.Linear(n_embd, vocab_size, bias=False)
        
        # 脉冲→Motor映射（连接SNN和语言模型）
        # 将脉冲激活模式映射到motor层
        self.spike_to_motor = nn.Linear(n_embd, 256)  # 压缩到语义空间
        
        print(f"[SpikeGPT] 语言模型初始化完成")
        print(f"  词表大小: {vocab_size}")
        print(f"  嵌入维度: {n_embd}")
        print(f"  层数: {n_layer}")
        print(f"  参数量: {sum(p.numel() for p in self.parameters())/1e6:.2f}M")
        
    def forward(self, input_ids: torch.Tensor, 
                states: Optional[List] = None,
                return_spikes: bool = False):
        """
        前向传播
        
        Args:
            input_ids: 输入token IDs [B, T]
            states: 层状态列表
            return_spikes: 是否返回脉冲序列
            
        Returns:
            logits: 预测logits [B, T, vocab_size]
            spikes: 脉冲序列（如果return_spikes=True）
            new_states: 更新后的状态
        """
        B, T = input_ids.size()
        
        # Token嵌入
        x = self.embedding(input_ids)  # [B, T, n_embd]
        
        # 初始化状态
        if states is None:
            states = [None] * self.n_layer
        
        # 逐层处理
        new_states = []
        all_spikes = []
        
        for i, block in enumerate(self.blocks):
            spike_out, state = block(x, *states[i] if states[i] else (None, None, None))
            x = spike_out  # 脉冲作为下一层输入
            new_states.append(state)
            all_spikes.append(spike_out)
        
        # 最终归一化
        x = self.ln_f(x)
        
        # 语言建模头
        logits = self.lm_head(x)  # [B, T, vocab_size]
        
        # 计算motor层激活（语义向量）
        motor_activity = self.spike_to_motor(x[:, -1])  # 取最后一个token [B, 256]
        
        if return_spikes:
            return logits, all_spikes, motor_activity, new_states
        return logits, motor_activity, new_states
    
    def generate(self, input_ids: torch.Tensor, 
                 max_new_tokens: int = 100,
                 temperature: float = 1.0,
                 top_k: int = 50) -> torch.Tensor:
        """
        自回归生成
        
        Args:
            input_ids: 起始token序列 [B, T]
            max_new_tokens: 最大生成长度
            temperature: 采样温度
            top_k: top-k采样
            
        Returns:
            generated_ids: 生成的token序列 [B, T+max_new_tokens]
        """
        B, T = input_ids.size()
        generated = input_ids.clone()
        
        # 初始化状态
        states = None
        
        for _ in range(max_new_tokens):
            # 前向传播（只处理最后一个token）
            logits, _, states = self.forward(generated[:, -1:], states)
            
            # 采样下一个token
            logits = logits[:, -1, :] / temperature  # [B, vocab_size]
            
            # Top-k过滤
            if top_k > 0:
                indices_to_remove = logits < torch.topk(logits, top_k)[0][..., -1, None]
                logits[indices_to_remove] = float('-inf')
            
            probs = F.softmax(logits, dim=-1)
            next_token = torch.multinomial(probs, num_samples=1)
            
            # 追加到生成序列
            generated = torch.cat([generated, next_token], dim=1)
            
            # 检查是否生成结束符
            if next_token.item() == 2:  # EOS token
                break
        
        return generated


# 简单的tokenizer接口适配
class SpikeGPTTokenizer:
    """适配现有SpikeTokenizer到SpikeGPT"""
    
    def __init__(self, base_tokenizer):
        self.base = base_tokenizer
        self.vocab_size = base_tokenizer.vocab_size
        
    def encode(self, text: str) -> List[int]:
        """文本 → token IDs - 直接使用_tokenize获取token列表"""
        # 使用 _tokenize 获取 token 字符串列表，而不是脉冲矩阵
        tokens = self.base._tokenize(text)
        # 转换为 token IDs
        result = []
        for token in tokens:
            token_id = self.base.token_to_id.get(token, 1)  # 1 = <UNK>
            result.append(int(token_id))
        return result
    
    def decode(self, token_ids: List[int]) -> str:
        """Token IDs → 文本"""
        tokens = [self.base.id_to_token.get(i, '<UNK>') for i in token_ids]
        return self.base._detokenize(tokens)


if __name__ == "__main__":
    # 测试
    print("=" * 60)
    print("SpikeGPT-RWKV 模块测试")
    print("=" * 60)
    
    # 创建模型
    model = SpikeGPTLanguageModel(
        vocab_size=1000,  # 小词表测试
        n_embd=256,
        n_layer=4
    )
    
    # 测试前向传播
    batch_size = 2
    seq_len = 10
    input_ids = torch.randint(0, 1000, (batch_size, seq_len))
    
    print(f"\n输入形状: {input_ids.shape}")
    
    logits, motor_activity, states = model(input_ids)
    print(f"输出logits形状: {logits.shape}")
    print(f"Motor活动形状: {motor_activity.shape}")
    print(f"状态数: {len(states)}")
    
    # 测试生成
    print("\n测试生成...")
    start_ids = torch.randint(0, 1000, (1, 5))
    generated = model.generate(start_ids, max_new_tokens=20, temperature=0.8)
    print(f"生成序列形状: {generated.shape}")
    
    print("\n✅ SpikeGPT模块测试通过!")
    print("=" * 60)
