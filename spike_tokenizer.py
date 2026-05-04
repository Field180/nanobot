"""
SpikeTokenizer - 原生语言编码/解码系统
将文本与脉冲信号直接互转，无需事后翻译

参考：SpikeGPT (2023), SpikingBrain/瞬悉1.0 (2024)
"""
import numpy as np
from typing import List, Dict, Tuple, Optional, Set
from collections import Counter
import re


class SpikeTokenizer:
    """
    脉冲神经网络原生词表系统
    
    核心创新：
    1. Temporal Coding - 用脉冲时间编码token位置
    2. Rate-Population Hybrid - 频率+群体编码结合
    3. Direct Spike-to-Token Mapping - 脉冲直接映射词表
    """
    
    def __init__(self, vocab_size: int = 50000, embed_dim: int = 256):
        self.vocab_size = vocab_size
        self.embed_dim = embed_dim
        self.max_seq_len = 2048
        
        # 基础词表（中文+英文高频词）
        self.vocab = self._build_bilingual_vocab()
        self.token_to_id = {token: i for i, token in enumerate(self.vocab)}
        self.id_to_token = {i: token for i, token in enumerate(self.vocab)}
        
        # 脉冲编码参数
        self.time_resolution = 100  # 100 time steps per token
        self.spike_threshold = 0.5
        
        # 嵌入矩阵：将token ID映射到脉冲发放模式
        self.embeddings = np.random.randn(vocab_size, embed_dim) * 0.1
        
        # 解码器权重：将motor层脉冲解码为token概率
        self.decoder_weights = np.random.randn(embed_dim, vocab_size) * 0.01
        
        print(f"[SpikeTokenizer] 初始化完成")
        print(f"  词表大小: {len(self.vocab):,}")
        print(f"  嵌入维度: {embed_dim}")
        print(f"  时间分辨率: {self.time_resolution} steps/token")
    
    def _build_bilingual_vocab(self) -> List[str]:
        """构建双语词表"""
        vocab = []
        
        # 特殊token
        special = ['<PAD>', '<UNK>', '<BOS>', '<EOS>', '<MASK>', '<SEP>']
        vocab.extend(special)
        
        # 中文字符（常用3500字）
        chinese_common = [
            '的', '一', '是', '在', '不', '了', '有', '和', '人', '这', '中', '大', '为', '上', '个', '国', '我', '以', '要', '他',
            '时', '来', '用', '们', '生', '到', '作', '地', '于', '出', '就', '分', '对', '成', '会', '可', '主', '发', '年', '动',
            '同', '工', '也', '能', '下', '过', '子', '说', '产', '种', '面', '而', '方', '后', '多', '定', '行', '学', '法', '所',
            '民', '得', '经', '十', '三', '之', '进', '着', '等', '部', '度', '家', '电', '力', '里', '如', '水', '化', '高', '自',
            '二', '理', '起', '小', '物', '现', '实', '加', '量', '都', '两', '体', '制', '机', '当', '使', '点', '从', '业', '本',
            '去', '把', '性', '好', '应', '开', '它', '合', '还', '因', '由', '其', '些', '然', '前', '外', '天', '政', '四', '日',
            '那', '社', '义', '事', '平', '形', '相', '全', '表', '间', '样', '与', '各', '关', '新', '线', '内', '数', '正', '心',
            '反', '你', '明', '看', '原', '又', '么', '利', '比', '或', '但', '质', '气', '第', '向', '道', '命', '此', '变', '条',
            '只', '没', '结', '解', '问', '意', '建', '月', '公', '无', '系', '军', '很', '情', '最', '何', '这', '知', '长', '位',
            '次', '将', '感', '指', '带', '活', '调', '正', '文', '总', '技', '术', '期', '眼', '意', '门', '第', '元', '神', '语',
        ]
        vocab.extend(chinese_common)
        
        # 英文字母和数字
        for c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
            vocab.append(c)
        
        # 英文常见词根
        english_roots = [
            'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have', 'I', 'it', 'for', 'not', 'on', 'with',
            'he', 'as', 'you', 'do', 'at', 'this', 'but', 'his', 'by', 'from', 'they', 'we', 'say', 'her',
            'she', 'or', 'an', 'will', 'my', 'one', 'all', 'would', 'there', 'their', 'what', 'so', 'up',
            'out', 'if', 'about', 'who', 'get', 'which', 'go', 'me', 'when', 'make', 'can', 'like', 'time',
            'no', 'just', 'him', 'know', 'take', 'people', 'into', 'year', 'your', 'good', 'some', 'could',
            'them', 'see', 'other', 'than', 'then', 'now', 'look', 'only', 'come', 'its', 'over', 'think',
            'also', 'back', 'after', 'use', 'two', 'how', 'our', 'work', 'first', 'well', 'way', 'even',
            'new', 'want', 'because', 'any', 'these', 'give', 'day', 'most', 'us', 'is', 'was', 'are', 'were',
            # 技术词汇
            'neuron', 'spike', 'brain', 'network', 'learning', 'model', 'data', 'code', 'system', 'function',
            'python', 'ai', 'intelligence', 'machine', 'deep', 'neural', 'synapse', 'cognitive', 'conscious',
        ]
        vocab.extend(english_roots)
        
        # 标点符号
        punctuations = ['，', '。', '！', '？', '、', '；', '：', '「', '」', '『', '』', '（', '）', '【', '】',
                       ',', '.', '!', '?', ';', ':', '"', '"', ''', ''', '(', ')', '[', ']', '{', '}',
                       '-', '_', '+', '=', '*', '/', '\\', '|', '<', '>', '~', '`', '@', '#', '$', '%', '^', '&']
        vocab.extend(punctuations)
        
        # 填充剩余词表位置
        current_size = len(vocab)
        if current_size < self.vocab_size:
            # 添加中文低频字填充
            for i in range(self.vocab_size - current_size):
                vocab.append(f'<CHAR_{i}>')
        
        return vocab[:self.vocab_size]
    
    def encode(self, text: str, max_length: int = 512) -> np.ndarray:
        """
        将文本编码为脉冲时序模式
        
        输出: [max_length, embed_dim] 脉冲矩阵
              1 = 发放脉冲, 0 = 静息
        """
        # 分词（简单字符级+词典匹配）
        tokens = self._tokenize(text)
        
        # 截断或填充
        if len(tokens) > max_length:
            tokens = tokens[:max_length]
        else:
            tokens.extend(['<PAD>'] * (max_length - len(tokens)))
        
        # 转换为脉冲时序
        spike_pattern = np.zeros((max_length, self.time_resolution, self.embed_dim))
        
        for t, token in enumerate(tokens):
            token_id = self.token_to_id.get(token, self.token_to_id['<UNK>'])
            
            # 基于嵌入向量的脉冲编码
            embedding = self.embeddings[token_id]
            
            # Temporal coding: 在100个时间步内编码
            for step in range(self.time_resolution):
                # 相位编码 - 不同维度在不同时间发放
                phase = (step / self.time_resolution) * 2 * np.pi
                activation = np.sin(phase + embedding * np.pi)
                spike_pattern[t, step] = (activation > self.spike_threshold).astype(float)
        
        return spike_pattern
    
    def decode(self, motor_spikes: np.ndarray, top_k: int = 5) -> List[Tuple[str, float]]:
        """
        将motor层脉冲解码为候选token
        
        输入: motor_spikes [embed_dim] 脉冲计数或发放率
        输出: [(token, probability), ...] 按概率排序
        """
        # 计算logits
        logits = motor_spikes @ self.decoder_weights
        
        # Softmax概率
        exp_logits = np.exp(logits - np.max(logits))
        probs = exp_logits / np.sum(exp_logits)
        
        # 获取top-k
        top_indices = np.argsort(probs)[-top_k:][::-1]
        
        results = []
        for idx in top_indices:
            token = self.id_to_token.get(idx, '<UNK>')
            prob = probs[idx]
            results.append((token, float(prob)))
        
        return results
    
    def decode_sequence(self, motor_spike_history: List[np.ndarray], 
                       temperature: float = 1.0) -> str:
        """
        将脉冲序列解码为完整文本
        
        输入: [seq_len, embed_dim] 每时间步的motor脉冲
        输出: 生成的文本字符串
        """
        tokens = []
        
        for spikes in motor_spike_history:
            candidates = self.decode(spikes, top_k=1)
            if candidates and candidates[0][1] > 0.1:  # 概率阈值
                token = candidates[0][0]
                if token not in ['<PAD>', '<UNK>', '<BOS>', '<EOS>']:
                    tokens.append(token)
        
        return self._detokenize(tokens)
    
    def _tokenize(self, text: str) -> List[str]:
        """简单分词：优先匹配词表中的长词"""
        tokens = []
        i = 0
        while i < len(text):
            # 尝试最长匹配
            matched = False
            for length in range(min(10, len(text) - i), 0, -1):
                substr = text[i:i+length]
                if substr in self.token_to_id:
                    tokens.append(substr)
                    i += length
                    matched = True
                    break
            
            if not matched:
                # 单字符
                tokens.append(text[i])
                i += 1
        
        return tokens
    
    def _detokenize(self, tokens: List[str]) -> str:
        """将token列表合并为文本"""
        # 简单拼接（中文不需要空格，英文需要）
        result = ''
        for i, token in enumerate(tokens):
            if token.startswith('<') or len(token) == 1 and token.isascii():
                result += token
            else:
                # 中文词
                result += token
        
        return result
    
    def get_input_spikes(self, text: str) -> np.ndarray:
        """
        获取输入文本的脉冲表示（用于输入到SNN）
        输出: [seq_len, embed_dim] 展平后的脉冲编码
        """
        spike_pattern = self.encode(text, max_length=512)
        # 展平时间维度: [seq_len, time_resolution, embed_dim] -> [seq_len, embed_dim]
        # 使用发放率表示
        rate_coding = np.mean(spike_pattern, axis=1)  # [seq_len, embed_dim]
        return rate_coding


class LinearSpikeAttention:
    """
    线性复杂度脉冲自注意力
    
    参考SpikeGPT：O(N)复杂度替代传统Transformer's O(N²)
    使用Recurrent Spiking Neurons实现序列建模
    """
    
    def __init__(self, dim: int = 256, num_heads: int = 8):
        self.dim = dim
        self.num_heads = num_heads
        self.head_dim = dim // num_heads
        
        # 脉冲神经元参数（LIF模型）
        self.tau = 20.0  # 时间常数
        self.v_thresh = 1.0
        self.v_reset = 0.0
        
        # 线性投影权重
        self.w_q = np.random.randn(dim, dim) * 0.01
        self.w_k = np.random.randn(dim, dim) * 0.01
        self.w_v = np.random.randn(dim, dim) * 0.01
        self.w_o = np.random.randn(dim, dim) * 0.01
        
        print(f"[LinearSpikeAttention] 初始化")
        print(f"  维度: {dim}, 头数: {num_heads}")
        print(f"  复杂度: O(seq_len) 替代 O(seq_len²)")
    
    def forward(self, x: np.ndarray, state: Optional[Dict] = None) -> Tuple[np.ndarray, Dict]:
        """
        前向传播
        
        输入: x [seq_len, dim] 脉冲发放率
        输出: out [seq_len, dim], 更新后的状态
        """
        seq_len, dim = x.shape
        
        # 初始化状态
        if state is None:
            state = {
                'v': np.zeros((seq_len, self.num_heads, self.head_dim)),  # 膜电位
                'h': np.zeros((seq_len, self.num_heads, self.head_dim)),  # 隐状态
            }
        
        # 线性投影
        q = x @ self.w_q  # [seq_len, dim]
        k = x @ self.w_k
        v = x @ self.w_v
        
        # 分头
        q = q.reshape(seq_len, self.num_heads, self.head_dim)
        k = k.reshape(seq_len, self.num_heads, self.head_dim)
        v = v.reshape(seq_len, self.num_heads, self.head_dim)
        
        # 递归脉冲注意力（线性复杂度）
        # 每步只依赖前一步状态，不计算成对注意力
        output = np.zeros((seq_len, self.num_heads, self.head_dim))
        
        for t in range(seq_len):
            # 更新膜电位（LIF动力学）
            v_t = state['v'][t] * (1 - 1/self.tau) + q[t] * k[t] / np.sqrt(self.head_dim)
            
            # 发放脉冲
            spike = (v_t >= self.v_thresh).astype(float)
            
            # 重置
            v_t = np.where(spike > 0, self.v_reset, v_t)
            
            # 输出 = 脉冲 * 值向量
            output[t] = spike * v[t]
            
            # 更新状态
            state['v'][t] = v_t
            state['h'][t] = spike
        
        # 合并头
        output = output.reshape(seq_len, dim)
        
        # 输出投影
        out = output @ self.w_o
        
        return out, state


class SpikeLanguageModel:
    """
    端到端脉冲语言模型
    输入文本 → 脉冲编码 → SNN处理 → 脉冲解码 → 输出文本
    """
    
    def __init__(self, vocab_size: int = 50000, embed_dim: int = 256):
        self.tokenizer = SpikeTokenizer(vocab_size, embed_dim)
        self.attention = LinearSpikeAttention(embed_dim, num_heads=8)
        
        # 输出投影（motor层）
        self.output_proj = np.random.randn(embed_dim, embed_dim) * 0.01
        
        print(f"[SpikeLanguageModel] 原生语言SNN就绪")
        print(f"  输入: 文本 → 脉冲")
        print(f"  处理: 线性复杂度自注意力")
        print(f"  输出: 脉冲 → Token → 文本")
    
    def generate(self, prompt: str, max_tokens: int = 100, 
                temperature: float = 1.0) -> str:
        """
        生成文本（自回归）
        """
        generated = list(prompt)
        
        for _ in range(max_tokens):
            # 编码当前文本
            input_spikes = self.tokenizer.get_input_spikes(''.join(generated))
            
            # SNN处理
            output_spikes, _ = self.attention.forward(input_spikes)
            
            # 取最后一个位置预测下一个token
            last_spike = output_spikes[-1] @ self.output_proj
            
            # 解码
            candidates = self.tokenizer.decode(last_spike, top_k=5)
            
            # 采样
            if candidates:
                tokens, probs = zip(*candidates)
                probs = np.array(probs) / temperature
                probs = np.exp(probs) / np.sum(np.exp(probs))
                
                next_token = np.random.choice(tokens, p=probs)
                
                if next_token == '<EOS>':
                    break
                
                generated.append(next_token)
            else:
                break
        
        return self.tokenizer._detokenize(generated)
    
    def process(self, text: str) -> Dict:
        """
        处理输入并返回结构化结果
        """
        # 编码
        input_spikes = self.tokenizer.get_input_spikes(text)
        
        # SNN处理
        output_spikes, state = self.attention.forward(input_spikes)
        
        # 解码每个位置的预测
        predictions = []
        for spike in output_spikes:
            candidates = self.tokenizer.decode(spike @ self.output_proj, top_k=3)
            predictions.append(candidates)
        
        # 生成响应
        generated = self.generate(text, max_tokens=50)
        
        return {
            'input': text,
            'generated': generated,
            'pulse_count': int(np.sum(output_spikes > 0)),
            'active_neurons': int(np.sum(np.any(output_spikes > 0, axis=0))),
            'predictions': predictions[:5],  # 前5个位置的预测
            'attention_state': state
        }


if __name__ == '__main__':
    # 测试
    print("=" * 60)
    print("SpikeTokenizer 测试")
    print("=" * 60)
    
    tokenizer = SpikeTokenizer(vocab_size=1000, embed_dim=64)
    
    text = "Hello，这是一个测试。"
    print(f"\n输入: {text}")
    
    spikes = tokenizer.encode(text, max_length=20)
    print(f"脉冲形状: {spikes.shape}")
    print(f"脉冲密度: {np.mean(spikes):.4f}")
    
    # 测试解码
    motor_out = np.random.randn(64)
    motor_out[10:20] = 5.0  # 激活部分神经元
    candidates = tokenizer.decode(motor_out, top_k=5)
    print(f"\n解码候选: {candidates}")
    
    print("\n" + "=" * 60)
    print("SpikeLanguageModel 测试")
    print("=" * 60)
    
    model = SpikeLanguageModel(vocab_size=1000, embed_dim=64)
    result = model.process("Hello")
    print(f"处理结果: {result}")
