"""
NeuraCore 感觉皮层 - SpikeGPT 适配器
将感觉皮层输出转换为 SpikeGPT 可接受的脉冲张量
"""

import torch
import torch.nn as nn
import numpy as np
from typing import List, Dict, Tuple, Optional


class SensoryCortexSpikeAdapter:
    """
    感觉皮层脉冲适配器
    
    将 NeuraCore 的词汇到神经元群映射转换为 SpikeGPT 输入格式
    """
    
    def __init__(
        self,
        vocab_size: int = 50277,
        n_neurons_per_token: int = 100,
        temporal_window: int = 10,
        spike_threshold: float = 0.5,
        device: str = 'cpu'
    ):
        """
        初始化适配器
        
        Args:
            vocab_size: 词表大小
            n_neurons_per_token: 每个 token 对应的神经元数
            temporal_window: 时间窗口大小 (模拟时间步)
            spike_threshold: 脉冲发放阈值
            device: 计算设备
        """
        self.vocab_size = vocab_size
        self.n_neurons_per_token = n_neurons_per_token
        self.temporal_window = temporal_window
        self.spike_threshold = spike_threshold
        self.device = device
        
        # 词到神经元群的映射矩阵
        # 每个词激活一组特定的神经元
        self.token_to_neuron = nn.Embedding(
            vocab_size, 
            n_neurons_per_token,
            sparse=True
        ).to(device)
        
        # 初始化：正交化词向量
        with torch.no_grad():
            nn.init.orthogonal_(self.token_to_neuron.weight)
            # 归一化到 0-1 范围
            self.token_to_neuron.weight.abs_()
        
        # 时间编码器 (模拟时间延迟)
        self.temporal_encoder = nn.Sequential(
            nn.Linear(n_neurons_per_token, n_neurons_per_token),
            nn.LayerNorm(n_neurons_per_token),
            nn.Sigmoid()
        ).to(device)
        
        print(f"[SensoryAdapter] Initialized: vocab={vocab_size}, neurons_per_token={n_neurons_per_token}")
    
    def encode_tokens_to_spikes(
        self,
        token_ids: List[int],
        return_rates: bool = True
    ) -> torch.Tensor:
        """
        将 token 序列编码为脉冲张量
        
        Args:
            token_ids: Token ID 列表
            return_rates: 返回发放率 (True) 或二进制脉冲 (False)
        
        Returns:
            脉冲张量 (1, seq_len, temporal_window, n_neurons_per_token)
        """
        seq_len = len(token_ids)
        
        # 获取 token 对应的神经元激活模式
        tokens = torch.tensor(token_ids, device=self.device)
        neuron_patterns = self.token_to_neuron(tokens)  # (seq_len, n_neurons)
        
        # 扩展时间维度
        spikes = torch.zeros(
            1, seq_len, self.temporal_window, self.n_neurons_per_token,
            device=self.device
        )
        
        # 为每个时间步生成脉冲模式
        for t in range(seq_len):
            base_pattern = neuron_patterns[t]  # (n_neurons,)
            
            for time_step in range(self.temporal_window):
                # 时间衰减：早期时间步激活更强
                time_factor = 1.0 - (time_step / self.temporal_window) * 0.3
                
                # 添加时间噪声 (模拟生物神经元的随机性)
                noise = torch.randn_like(base_pattern) * 0.1
                temporal_pattern = self.temporal_encoder(
                    base_pattern * time_factor + noise
                )
                
                # 生成脉冲
                if return_rates:
                    spikes[0, t, time_step, :] = temporal_pattern
                else:
                    spikes[0, t, time_step, :] = torch.bernoulli(temporal_pattern)
        
        return spikes
    
    def encode_text_to_spikes(
        self,
        text: str,
        tokenizer=None,
        return_rates: bool = True
    ) -> Dict[str, torch.Tensor]:
        """
        将文本编码为脉冲
        
        Args:
            text: 输入文本
            tokenizer: 分词器 (如果为None，使用字符级编码)
            return_rates: 返回发放率或二进制脉冲
        
        Returns:
            Dict 包含:
                - 'spikes': 脉冲张量
                - 'token_ids': token IDs
                - 'spike_rates': 平均发放率
        """
        if tokenizer is not None:
            # 使用分词器
            token_ids = tokenizer.encode(text)
        else:
            # 字符级编码 (简化版)
            token_ids = [ord(c) % self.vocab_size for c in text]
        
        spikes = self.encode_tokens_to_spikes(token_ids, return_rates)
        
        # 展平时间维度用于 SpikeGPT 输入
        # (1, seq_len, temporal_window, n_neurons) -> (1, seq_len, temporal_window * n_neurons)
        flat_spikes = spikes.reshape(1, len(token_ids), -1)
        
        return {
            'spikes': flat_spikes,
            'token_ids': token_ids,
            'spike_rates': spikes.mean().item()
        }
    
    def create_sensory_spike_tensor(
        self,
        batch_size: int,
        seq_len: int,
        n_sensory: int,
        activation_pattern: str = 'random'
    ) -> torch.Tensor:
        """
        创建模拟的感觉皮层脉冲
        
        Args:
            batch_size: 批次大小
            seq_len: 序列长度
            n_sensory: 感觉皮层神经元数
            activation_pattern: 激活模式 ('random', 'sequential', 'burst')
        
        Returns:
            模拟的脉冲张量 (batch_size, seq_len, n_sensory)
        """
        spikes = torch.zeros(batch_size, seq_len, n_sensory, device=self.device)
        
        if activation_pattern == 'random':
            # 随机稀疏激活 (模拟真实神经活动)
            for b in range(batch_size):
                for t in range(seq_len):
                    # 随机选择 1-5% 的神经元发放
                    n_active = np.random.randint(n_sensory // 100, n_sensory // 20)
                    active_indices = torch.randperm(n_sensory)[:n_active]
                    spikes[b, t, active_indices] = 1.0
                    
        elif activation_pattern == 'sequential':
            # 顺序激活 (模拟序列处理)
            for b in range(batch_size):
                for t in range(seq_len):
                    # 每个时间步激活不同的神经元群
                    start_idx = (t * n_sensory // seq_len) % n_sensory
                    end_idx = min(start_idx + n_sensory // seq_len, n_sensory)
                    spikes[b, t, start_idx:end_idx] = 1.0
                    
        elif activation_pattern == 'burst':
            # 突发激活 (模拟强刺激)
            for b in range(batch_size):
                burst_time = np.random.randint(0, seq_len)
                spikes[b, burst_time, :] = torch.rand(n_sensory, device=self.device) > 0.5
        
        return spikes
    
    def project_to_embedding_space(
        self,
        sensory_spikes: torch.Tensor,
        target_dim: int
    ) -> torch.Tensor:
        """
        将高维感觉脉冲投影到低维嵌入空间
        
        Args:
            sensory_spikes: 感觉皮层脉冲 (B, T, n_sensory)
            target_dim: 目标维度 (SpikeGPT n_embd)
        
        Returns:
            投影后的张量 (B, T, target_dim)
        """
        B, T, n_sensory = sensory_spikes.shape
        
        # 创建投影矩阵 (如果未创建)
        if not hasattr(self, 'projection'):
            self.projection = nn.Linear(n_sensory, target_dim).to(self.device)
            nn.init.xavier_uniform_(self.projection.weight)
        
        # 展平批次和时间维度
        flat_spikes = sensory_spikes.reshape(B * T, n_sensory)
        
        # 投影
        projected = self.projection(flat_spikes)
        
        # 恢复形状
        projected = projected.reshape(B, T, target_dim)
        
        # 归一化
        projected = torch.layer_norm(projected, projected.shape[-1:])
        
        return projected


class MotorCortexSpikeAdapter:
    """
    运动皮层脉冲适配器
    
    将 SpikeGPT 输出解码为运动皮层动作/文本
    """
    
    def __init__(
        self,
        n_motor_neurons: int = 375000,
        n_action_classes: int = 100,  # 工具调用类别数
        vocab_size: int = 50277,
        temporal_integration: int = 5,  # 时间积分窗口
        device: str = 'cpu'
    ):
        """
        初始化适配器
        
        Args:
            n_motor_neurons: 运动皮层神经元数
            n_action_classes: 动作类别数
            vocab_size: 词表大小 (用于文本生成)
            temporal_integration: 时间积分窗口
            device: 计算设备
        """
        self.n_motor_neurons = n_motor_neurons
        self.n_action_classes = n_action_classes
        self.vocab_size = vocab_size
        self.temporal_integration = temporal_integration
        self.device = device
        
        # 运动皮层 -> 动作空间映射
        self.motor_to_action = nn.Sequential(
            nn.Linear(n_motor_neurons, n_motor_neurons // 4),
            nn.LayerNorm(n_motor_neurons // 4),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(n_motor_neurons // 4, n_action_classes)
        ).to(device)
        
        # 运动皮层 -> 文本空间映射
        self.motor_to_text = nn.Sequential(
            nn.Linear(n_motor_neurons, n_motor_neurons // 4),
            nn.LayerNorm(n_motor_neurons // 4),
            nn.GELU(),
            nn.Linear(n_motor_neurons // 4, vocab_size)
        ).to(device)
        
        # 脉冲积分器 (模拟神经元的膜电位累积)
        self.spike_integrator = nn.LSTM(
            input_size=n_motor_neurons,
            hidden_size=n_motor_neurons // 2,
            num_layers=1,
            batch_first=True
        ).to(device)
        
        print(f"[MotorAdapter] Initialized: motor_neurons={n_motor_neurons}, actions={n_action_classes}")
    
    def decode_spikes_to_action(
        self,
        motor_spikes: torch.Tensor,
        integration_window: Optional[int] = None
    ) -> Dict[str, torch.Tensor]:
        """
        将运动皮层脉冲解码为动作
        
        Args:
            motor_spikes: 运动皮层脉冲 (B, T, n_motor_neurons)
            integration_window: 时间积分窗口
        
        Returns:
            Dict 包含:
                - 'action_logits': 动作 logits
                - 'action_probs': 动作概率
                - 'selected_action': 选择的动作
        """
        if integration_window is None:
            integration_window = self.temporal_integration
        
        B, T, _ = motor_spikes.shape
        
        # 时间积分 (使用 LSTM)
        if T >= integration_window:
            # 取最后 integration_window 个时间步
            window_spikes = motor_spikes[:, -integration_window:, :]
        else:
            # 填充
            padding = integration_window - T
            window_spikes = torch.cat([
                torch.zeros(B, padding, self.n_motor_neurons, device=self.device),
                motor_spikes
            ], dim=1)
        
        # LSTM 积分
        lstm_out, _ = self.spike_integrator(window_spikes)
        
        # 取最后时刻的隐藏状态
        final_state = lstm_out[:, -1, :]  # (B, hidden_size)
        
        # 扩展回原始维度 (简化处理)
        final_state_expanded = torch.cat([
            final_state,
            torch.zeros(B, self.n_motor_neurons - final_state.shape[1], device=self.device)
        ], dim=1)
        
        # 映射到动作空间
        action_logits = self.motor_to_action(final_state_expanded)
        action_probs = torch.softmax(action_logits, dim=-1)
        
        # 选择动作
        selected_action = torch.argmax(action_probs, dim=-1)
        
        return {
            'action_logits': action_logits,
            'action_probs': action_probs,
            'selected_action': selected_action,
            'confidence': action_probs.max(dim=-1)[0]
        }
    
    def decode_spikes_to_text(
        self,
        motor_spikes: torch.Tensor,
        tokenizer=None,
        temperature: float = 1.0,
        top_k: int = 50
    ) -> Dict[str, any]:
        """
        将运动皮层脉冲解码为文本
        
        Args:
            motor_spikes: 运动皮层脉冲 (B, T, n_motor_neurons)
            tokenizer: 分词器 (推荐 GPT2Tokenizer)
            temperature: 采样温度
            top_k: top-k 采样限制
        
        Returns:
            Dict 包含:
                - 'token_logits': token logits
                - 'generated_ids': 生成的 token IDs
                - 'text': 生成的文本 (如果有 tokenizer)
        """
        B, T, _ = motor_spikes.shape
        
        # 对每个时间步预测下一个 token
        token_logits_list = []
        for t in range(T):
            logits = self.motor_to_text(motor_spikes[:, t, :])
            token_logits_list.append(logits.unsqueeze(1))
        
        # 拼接
        token_logits = torch.cat(token_logits_list, dim=1)  # (B, T, vocab_size)
        
        # 应用温度
        token_logits = token_logits / temperature
        
        # Top-k 采样
        if top_k > 0:
            for b in range(B):
                for t in range(T):
                    top_k_values, top_k_indices = torch.topk(token_logits[b, t], top_k)
                    token_logits[b, t, :] = float('-inf')
                    token_logits[b, t, top_k_indices] = top_k_values
        
        # 采样 token IDs
        token_probs = torch.softmax(token_logits, dim=-1)
        generated_ids = torch.multinomial(
            token_probs.view(-1, token_probs.size(-1)),
            num_samples=1
        ).view(B, T)
        
        result = {
            'token_logits': token_logits,
            'generated_ids': generated_ids,
            'token_probs': token_probs
        }
        
        # 如果提供了 tokenizer，解码文本
        if tokenizer is not None:
            texts = []
            for b in range(B):
                ids = generated_ids[b].tolist()
                # 过滤掉特殊 token
                if hasattr(tokenizer, 'eos_token_id') and tokenizer.eos_token_id:
                    ids = [id for id in ids if id != tokenizer.eos_token_id]
                if hasattr(tokenizer, 'pad_token_id') and tokenizer.pad_token_id:
                    ids = [id for id in ids if id != tokenizer.pad_token_id]
                
                # 解码为文本
                try:
                    text = tokenizer.decode(ids, skip_special_tokens=True)
                except:
                    # 如果解码失败，显示 token IDs
                    text = " ".join([str(id) for id in ids])
                texts.append(text)
            result['texts'] = texts
            result['generated_text'] = texts[0] if B == 1 else texts
        
        return result
    
    def decode_to_motor_command(
        self,
        motor_spikes: torch.Tensor,
        command_type: str = 'discrete'
    ) -> torch.Tensor:
        """
        将脉冲解码为运动命令
        
        Args:
            motor_spikes: 运动皮层脉冲 (B, T, n_motor)
            command_type: 命令类型 ('discrete', 'continuous')
        
        Returns:
            运动命令
        """
        # 计算发放率
        firing_rates = motor_spikes.mean(dim=1)  # (B, n_motor)
        
        if command_type == 'discrete':
            # 离散命令：选择发放率最高的区域
            return torch.argmax(firing_rates, dim=-1)
        else:
            # 连续命令：归一化发放率
            return torch.sigmoid(firing_rates)


# 便捷函数
def create_neuracore_spike_adapters(
    n_sensory: int = 750000,
    n_motor: int = 375000,
    vocab_size: int = 50277,
    device: str = 'cpu'
) -> Tuple[SensoryCortexSpikeAdapter, MotorCortexSpikeAdapter]:
    """
    创建 NeuraCore 脉冲适配器对
    
    Args:
        n_sensory: 感觉皮层神经元数
        n_motor: 运动皮层神经元数
        vocab_size: 词表大小
        device: 设备
    
    Returns:
        (感觉适配器, 运动适配器)
    """
    sensory_adapter = SensoryCortexSpikeAdapter(
        vocab_size=vocab_size,
        n_neurons_per_token=100,
        temporal_window=10,
        device=device
    )
    
    motor_adapter = MotorCortexSpikeAdapter(
        n_motor_neurons=n_motor,
        n_action_classes=100,
        vocab_size=vocab_size,
        device=device
    )
    
    return sensory_adapter, motor_adapter


if __name__ == '__main__':
    print("Testing adapters...")
    
    # 创建适配器
    sensory, motor = create_neuracore_spike_adapters(
        n_sensory=1000,  # 小规模测试
        n_motor=500,
        device='cpu'
    )
    
    # 测试感觉编码
    print("\n[Sensory] Testing text encoding...")
    result = sensory.encode_text_to_spikes("Hello World")
    print(f"  Spikes shape: {result['spikes'].shape}")
    print(f"  Average spike rate: {result['spike_rates']:.3f}")
    
    # 测试运动解码
    print("\n[Motor] Testing spike decoding...")
    test_spikes = torch.randn(1, 10, 500)  # 模拟脉冲
    
    action_result = motor.decode_spikes_to_action(test_spikes)
    print(f"  Action shape: {action_result['action_logits'].shape}")
    print(f"  Selected action: {action_result['selected_action'].item()}")
    
    text_result = motor.decode_spikes_to_text(test_spikes)
    print(f"  Text logits shape: {text_result['token_logits'].shape}")
    
    print("\nAdapter tests completed!")
