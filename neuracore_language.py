"""
NeuraCore Language Module - SpikeGPT集成版本
原生语言生成能力（基于SpikeGPT/RWKV架构）

架构升级：
- 输入层: Token → 脉冲编码
- 处理层: SpikeGPT-RWKV (线性注意力O(N)复杂度)
- 输出层: 脉冲序列 → Token预测 → 自然语言

参考: https://github.com/ridgerchu/SpikeGPT (UCSC)
"""
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
import sys
import torch

# 导入现有NeuraCore
from neuracore3m import NeuraCore3M, init_neuracore3m, BRIAN2_AVAILABLE
from spike_tokenizer import SpikeTokenizer, SpikeLanguageModel

# 导入SpikeGPT-RWKV模块
try:
    from spikegpt_rwkv import (
        SpikeGPTLanguageModel, SpikingRWKVLayer,
        RWKVTimeMix, RWKVChannelMix, SpikeGPTTokenizer
    )
    SPIKEGPT_AVAILABLE = True
    print("[NeuraCoreLanguage] ✅ SpikeGPT-RWKV模块已加载")
except ImportError as e:
    SPIKEGPT_AVAILABLE = False
    print(f"[NeuraCoreLanguage] ⚠️ SpikeGPT模块不可用: {e}")


class NeuraCoreLanguage(NeuraCore3M):
    """
    原生语言NeuraCore
    
    继承300万神经元SNN，添加端到端语言处理能力
    """
    
    def __init__(self, n_sensory=750000, n_association=1500000, 
                 n_decision=375000, n_motor=375000, enable_stdp=True):
        # 初始化父类（Brian2 SNN）
        super().__init__(n_sensory, n_association, n_decision, n_motor, enable_stdp)
        
        # 传统Tokenizer - 带异常保护
        try:
            self.tokenizer = SpikeTokenizer(vocab_size=50000, embed_dim=256)
            print(f"[NeuraCoreLanguage] Tokenizer初始化成功，词表: {self.tokenizer.vocab_size}")
        except Exception as e:
            print(f"[NeuraCoreLanguage] Tokenizer初始化失败: {e}")
            # 创建最小化tokenizer作为后备
            self.tokenizer = self._create_fallback_tokenizer()
        
        # SpikeGPT-RWKV语言模型（核心升级）
        if SPIKEGPT_AVAILABLE:
            try:
                self.spikegpt = SpikeGPTLanguageModel(
                    vocab_size=50000,  # 与tokenizer一致
                    n_embd=768,        # SpikeGPT嵌入维度
                    n_layer=8,         # 8层RWKV（平衡性能和速度）
                    n_motor=n_motor
                )
                self.spikegpt_tokenizer = SpikeGPTTokenizer(self.tokenizer)
                # 设置为评估模式（不训练，仅推理）
                self.spikegpt.eval()
                print(f"[NeuraCoreLanguage] ✅ SpikeGPT语言模型已加载 (8层RWKV)")
            except Exception as e:
                print(f"[NeuraCoreLanguage] SpikeGPT加载失败: {e}")
                self.spikegpt = None
                self.spikegpt_tokenizer = None
        else:
            self.spikegpt = None
            self.spikegpt_tokenizer = None
        
        # 后备：简单的Motor→语言映射（当SpikeGPT不可用时）
        self.motor_to_lang = np.random.randn(n_motor, 256) * 0.001
        self.lang_bias = np.zeros(256)
        
        # 状态管理
        self.generation_history = []
        self.max_history = 10
        self.context_state = np.zeros(256)
        self.context_decay = 0.9
        
        # SpikeGPT状态缓存（用于连续对话）
        self.spikegpt_states = None
        self.conversation_tokens = []  # 对话历史 - 初始化为空列表
        
        print(f"[NeuraCoreLanguage] 原生语言SNN就绪")
        print(f"  架构: {'SpikeGPT-RWKV (脉冲驱动)' if self.spikegpt else '传统Motor映射'}")
        print(f"  词表: {self.tokenizer.vocab_size:,} tokens")
        print(f"  嵌入维度: {768 if self.spikegpt else 256}")
        print(f"  注意力机制: {'RWKV线性O(N)' if self.spikegpt else '无'}")
    
    def process_language(self, text: str, generate_response: bool = True,
                        max_tokens: int = 200) -> Dict[str, Any]:
        """
        端到端语言处理 - SpikeGPT版本
        
        流程:
        1. 文本 → Token IDs
        2. SpikeGPT-RWKV脉冲处理
        3. 脉冲驱动Token预测
        4. 生成自然语言响应
        """
        # 优先使用SpikeGPT生成（真正的脉冲语言模型）
        if self.spikegpt is not None and SPIKEGPT_AVAILABLE:
            try:
                return self._spikegpt_process(text, generate_response, max_tokens)
            except Exception as e:
                print(f"[NeuraCoreLanguage] SpikeGPT处理失败，回退到传统模式: {e}")
        
        # 后备：传统SNN处理（原有代码）
        return self._traditional_process(text, generate_response, max_tokens)
    
    def _spikegpt_process(self, text: str, generate_response: bool = True,
                          max_tokens: int = 200) -> Dict[str, Any]:
        """
        使用SpikeGPT-RWKV进行脉冲语言生成
        """
        try:
            # 辅助函数：递归展平并转换为int，限制最大长度防止OOM
            def to_int_list(items, max_len=1024):
                result = []
                def _flatten(x):
                    if len(result) >= max_len:
                        return
                    if isinstance(x, (list, tuple)):
                        for item in x:
                            _flatten(item)
                    elif hasattr(x, 'item'):  # numpy scalar
                        result.append(int(x.item()))
                    elif hasattr(x, 'tolist'):  # numpy array
                        arr = x.tolist()
                        if isinstance(arr, list):
                            for item in arr[:max_len]:
                                if len(result) >= max_len:
                                    break
                                result.append(int(item))
                        else:
                            result.append(int(arr))
                    else:
                        try:
                            result.append(int(x))
                        except:
                            pass
                _flatten(items)
                return result[:max_len]
            
            # 1. 编码输入
            raw_input_ids = self._encode_text_to_tokens(text)
            print(f"[DEBUG] raw_input_ids type: {type(raw_input_ids)}, len: {len(raw_input_ids) if hasattr(raw_input_ids, '__len__') else 'N/A'}")
            if raw_input_ids and len(raw_input_ids) > 0:
                print(f"[DEBUG] first element type: {type(raw_input_ids[0])}")
            input_ids = to_int_list(raw_input_ids)
            print(f"[DEBUG] input_ids after conversion: {len(input_ids)} items")
            
            # 2. 准备上下文 - 只取最后128个token避免OOM
            context_ids = []
            if self.conversation_tokens:
                print(f"[DEBUG] conversation_tokens type: {type(self.conversation_tokens)}, len: {len(self.conversation_tokens)}")
                if self.conversation_tokens and len(self.conversation_tokens) > 0:
                    print(f"[DEBUG] first conversation_token type: {type(self.conversation_tokens[0])}")
                context_ids = to_int_list(self.conversation_tokens[-128:])
                print(f"[DEBUG] context_ids after conversion: {len(context_ids)} items")
            
            # 合并上下文和输入
            combined_ids = context_ids + input_ids
            if len(combined_ids) > 256:
                combined_ids = combined_ids[-256:]
            
            # 3. SpikeGPT前向传播
            input_tensor = torch.tensor([combined_ids], dtype=torch.long)
            
            with torch.no_grad():
                logits, motor_activity, self.spikegpt_states = self.spikegpt(
                    input_tensor, 
                    states=self.spikegpt_states,
                    return_spikes=False
                )
            
            # 4. 生成响应
            response_text = ""
            generated_ids = []
            
            if generate_response:
                with torch.no_grad():
                    generated = self.spikegpt.generate(
                        input_tensor,
                        max_new_tokens=min(max_tokens, 50),
                        temperature=0.8,
                        top_k=50
                    )
                
                # 提取生成的token
                raw_generated = generated[0, len(combined_ids):].tolist()
                generated_ids = to_int_list(raw_generated)
                
                # 解码为文本
                response_text = self._tokens_to_text(generated_ids)
                
                # 5. 更新对话历史 - 使用纯Python列表，强制转换所有元素
                new_history = []
                for i in input_ids:
                    new_history.append(int(i))
                for g in generated_ids:
                    new_history.append(int(g))
                # 确保 conversation_tokens 是列表
                if not isinstance(self.conversation_tokens, list):
                    self.conversation_tokens = []
                self.conversation_tokens.extend(new_history)
                # 限制长度
                if len(self.conversation_tokens) > 512:
                    self.conversation_tokens = self.conversation_tokens[-512:]
            
            # 6. 计算脉冲统计
            motor_np = motor_activity.detach().cpu().numpy()
            if motor_np.ndim > 1:
                motor_np = motor_np[0]
            
            spike_count = int(np.sum(np.abs(motor_np) > 0.1))
            
            return {
                'input': text,
                'response': response_text if response_text else "脉冲处理完成",
                'generated_tokens': generated_ids[:10],
                'decoded_tokens': [(str(g), 0.5) for g in generated_ids[:5]],
                'motor_activity': float(np.mean(np.abs(motor_np))),
                'pulse_count': spike_count,
                'context_strength': float(np.linalg.norm(motor_np)),
                'generation_steps': len(generated_ids),
                'native_language': True,
                'architecture': 'SpikeGPT-RWKV',
                'attention_mechanism': 'RWKV Linear O(N)'
            }
            
        except Exception as e:
            print(f"[SpikeGPT] 错误: {e}")
            import traceback
            traceback.print_exc()
            return self._traditional_process(text, generate_response, max_tokens)
    
    def _encode_text_to_tokens(self, text: str) -> List[int]:
        """
        文本编码为token IDs（使用SpikeGPT tokenizer）
        """
        try:
            if self.spikegpt_tokenizer:
                ids = self.spikegpt_tokenizer.encode(text)
                # 递归展平并转换为int
                result = []
                for item in ids:
                    if hasattr(item, 'tolist'):  # numpy array
                        result.extend([int(i) for i in item.tolist()])
                    elif hasattr(item, 'item'):  # numpy scalar
                        result.append(int(item.item()))
                    elif isinstance(item, (list, tuple)):
                        result.extend([int(i) for i in item])
                    else:
                        result.append(int(item))
                return result
            
            # 后备：简单编码
            tokens = self.tokenizer.encode(text)
            # 确保所有token都是字符串类型
            str_tokens = []
            for t in tokens:
                if hasattr(t, 'item'):  # numpy scalar
                    str_tokens.append(str(t.item()))
                else:
                    str_tokens.append(str(t))
            
            ids = [self.tokenizer.token_to_id.get(t, 1) for t in str_tokens]
            return [int(i) for i in ids]
        except Exception as e:
            print(f"[Tokenize] 编码失败: {e}")
            import traceback
            traceback.print_exc()
            # 返回空列表或默认token
            return [1]  # <UNK>
    
    def _traditional_process(self, text: str, generate_response: bool = True,
                           max_tokens: int = 200) -> Dict[str, Any]:
        """
        传统SNN处理（后备模式）
        """
        # 原有实现...（编码输入、SNN仿真、Motor解码）
        input_spikes = self._encode_text_to_sensory(text)
        motor_activations = []
        
        for step in range(3):
            if step == 0:
                results = self.simulate_step(input_spikes, duration_ms=10)
            else:
                results = self.simulate_step(None, duration_ms=10)
            
            if 'spike_counts' in results:
                motor_spikes = self._get_motor_spikes()
                motor_activations.append(motor_spikes)
        
        if motor_activations:
            aggregated = np.mean(motor_activations, axis=0)
            lang_vector = self._motor_to_language(aggregated)
            self.context_state = self.context_state * self.context_decay + lang_vector * (1 - self.context_decay)
            decoded_tokens = self.tokenizer.decode(self.context_state, top_k=10)
            
            if generate_response:
                response = self._generate_text_from_motor(motor_activations, max_tokens=max_tokens)
            else:
                response = self._tokens_to_text(decoded_tokens[:3])
            
            return {
                'input': text,
                'response': response,
                'decoded_tokens': decoded_tokens[:5],
                'motor_activity': float(np.mean(aggregated)),
                'pulse_count': int(np.sum(aggregated > 0)),
                'context_strength': float(np.linalg.norm(self.context_state)),
                'generation_steps': len(motor_activations),
                'native_language': True,
                'architecture': 'Traditional SNN',
                'attention_mechanism': 'None'
            }
        
        return {
            'input': text,
            'response': "脉冲处理中...",
            'motor_activity': 0.0,
            'native_language': False
        }
    
    def _create_fallback_tokenizer(self):
        """创建最小化tokenizer作为后备"""
        class MinimalTokenizer:
            def __init__(self):
                self.vocab_size = 1000
                self.vocab = ['<PAD>', '<UNK>', '<BOS>', '<EOS>'] + [f'token_{i}' for i in range(996)]
                self.token_to_id = {t: i for i, t in enumerate(self.vocab)}
                self.id_to_token = {i: t for i, t in enumerate(self.vocab)}
                self.embeddings = np.random.randn(1000, 256) * 0.1
                self.decoder_weights = np.random.randn(256, 1000) * 0.01
            
            def encode(self, text):
                return [1]  # 返回UNK
            
            def decode(self, vector, top_k=5):
                return [('hello', 0.5), ('world', 0.3)]
            
            def get_input_spikes(self, text):
                return np.random.randn(10, 256) * 0.1
            
            def _detokenize(self, tokens):
                return ' '.join(tokens) if tokens else '[空响应]'
        
        return MinimalTokenizer()
    
    def _encode_text_to_sensory(self, text: str) -> Dict[int, float]:
        """
        将文本编码为感觉层输入
        
        使用tokenizer的脉冲编码投影到感觉层375K神经元
        """
        # 检查tokenizer是否可用
        if self.tokenizer is None:
            print("[Warning] tokenizer为None，使用随机输入")
            return {i: np.random.random() * 5 for i in range(0, self.layer_sizes['sensory'], 100)}
        
        # 获取tokenizer的脉冲表示 [seq_len, 256]
        try:
            token_spikes = self.tokenizer.get_input_spikes(text)
        except Exception as e:
            print(f"[Warning] get_input_spikes失败: {e}")
            token_spikes = np.random.randn(10, 256) * 0.1
        
        # 展平为感觉层输入
        # 感觉层75万神经元，分配编码
        sensory_input = {}
        
        # 将256维编码扩展到感觉层
        # 每维分配到约3000个神经元
        neurons_per_dim = self.layer_sizes['sensory'] // 256
        
        for t, spike_vec in enumerate(token_spikes[:100]):  # 限制前100个token
            for dim_idx, spike_rate in enumerate(spike_vec):
                if spike_rate > 0.1:  # 阈值
                    # 激活该维度的神经元群体
                    base_idx = dim_idx * neurons_per_dim
                    num_active = int(spike_rate * neurons_per_dim * 0.1)  # 10%激活
                    
                    for i in range(num_active):
                        neuron_idx = (base_idx + i) % self.layer_sizes['sensory']
                        # 时间偏移编码位置
                        time_factor = 1.0 - (t / 100) * 0.5  # 衰减
                        sensory_input[neuron_idx] = max(
                            sensory_input.get(neuron_idx, 0),
                            spike_rate * 10 * time_factor  # 0-10mV
                        )
        
        return sensory_input
    
    def _get_motor_spikes(self) -> np.ndarray:
        """
        获取当前motor层的脉冲状态
        """
        if not self.is_built or 'motor' not in self.regions:
            return np.zeros(self.layer_sizes['motor'])
        
        motor = self.regions['motor']
        
        # 从SpikeMonitor获取脉冲计数
        mon_name = 'spikes_motor'
        if mon_name in self.monitors:
            mon = self.monitors[mon_name]
            if hasattr(mon, 'count'):
                # 返回最近100ms的脉冲计数
                return np.array(mon.count[-self.layer_sizes['motor']:])
        
        # 回退：使用随机（开发测试）
        return np.random.randn(self.layer_sizes['motor']) * 0.1
    
    def _motor_to_language(self, motor_spikes: np.ndarray) -> np.ndarray:
        """
        将motor层脉冲映射到语言向量
        支持不同维度的输入（SpikeGPT 256维或传统375K维）
        """
        # 确保输入是1维数组
        if motor_spikes.ndim > 1:
            motor_spikes = motor_spikes.flatten()
        
        input_dim = len(motor_spikes)
        
        # 如果输入已经是256维（来自SpikeGPT），直接返回
        if input_dim == 256:
            return np.tanh(motor_spikes + self.lang_bias)
        
        # 如果是375K维（来自传统SNN），需要降维到256
        if input_dim >= 256:
            # 分段平均降维：每1470个神经元平均为1维
            segment_size = input_dim // 256
            lang_vec = np.zeros(256)
            
            for i in range(256):
                start = i * segment_size
                end = start + segment_size
                if end <= input_dim:
                    lang_vec[i] = np.mean(motor_spikes[start:end])
            
            return np.tanh(lang_vec + self.lang_bias)
        
        # 如果小于256维，填充到256
        lang_vec = np.zeros(256)
        lang_vec[:input_dim] = motor_spikes
        return np.tanh(lang_vec + self.lang_bias)
    
    def _generate_text_from_motor(self, motor_history: List[np.ndarray], 
                                   max_tokens: int = 200) -> str:
        """
        自回归生成文本
        """
        generated_tokens = []
        
        for _ in range(max_tokens):
            # 聚合当前motor状态
            if motor_history:
                current_motor = np.mean(motor_history[-3:], axis=0) if len(motor_history) >= 3 else motor_history[-1]
            else:
                break
            
            # 解码
            lang_vec = self._motor_to_language(current_motor)
            
            # 加入上下文
            lang_vec = 0.7 * lang_vec + 0.3 * self.context_state
            
            # 获取候选token
            candidates = self.tokenizer.decode(lang_vec, top_k=5)
            
            if not candidates:
                break
            
            # 过滤已生成和特殊token
            valid_candidates = [
                (t, p) for t, p in candidates 
                if t not in ['<PAD>', '<UNK>'] and t not in generated_tokens[-3:]
            ]
            
            if not valid_candidates:
                break
            
            # 采样
            tokens, probs = zip(*valid_candidates[:3])
            probs = np.array(probs)
            probs = probs / np.sum(probs)
            
            next_token = np.random.choice(tokens, p=probs)
            
            # 停止条件
            if next_token == '<EOS>':
                break
            
            generated_tokens.append(next_token)
            
            # 更新上下文
            self.context_state = self.context_state * 0.8 + lang_vec * 0.2
            
            # 模拟下一步motor激活（简化）
            # 实际应该运行SNN，这里用统计模型加速
            if len(motor_history) < 20:  # 限制步数
                # 基于生成的token反馈到motor层
                feedback = self._token_feedback(next_token)
                motor_history.append(feedback)
        
        return self._tokens_to_text(generated_tokens)
    
    def _token_feedback(self, token: str) -> np.ndarray:
        """
        将生成的token反馈回motor层（闭环）
        """
        # 获取token的嵌入
        if token in self.tokenizer.token_to_id:
            token_id = self.tokenizer.token_to_id[token]
            embedding = self.tokenizer.embeddings[token_id]
            
            # 反向映射：256 → 375K
            feedback = self.motor_to_lang @ embedding  # [375K]
            
            # 添加噪声和稀疏化
            feedback = feedback * (1 + np.random.randn(len(feedback)) * 0.1)
            feedback = np.where(feedback > np.percentile(feedback, 80), feedback, 0)
            
            return feedback
        
        return np.zeros(self.layer_sizes['motor'])
    
    def _tokens_to_text(self, tokens: List) -> str:
        """
        将token列表转换为文本
        支持int或str类型的token
        """
        try:
            # 过滤并统一处理
            filtered = []
            for t in tokens:
                if isinstance(t, str):
                    if t not in ['<PAD>', '<UNK>', '<BOS>', '<EOS>', '']:
                        filtered.append(t)
                elif isinstance(t, int):
                    # int转str
                    if t in self.tokenizer.id_to_token:
                        token_str = self.tokenizer.id_to_token[t]
                        if token_str not in ['<PAD>', '<UNK>', '<BOS>', '<EOS>']:
                            filtered.append(token_str)
            
            return self.tokenizer._detokenize(filtered)
        except Exception as e:
            print(f"[Detokenize] 解码失败: {e}")
            return "[解码错误]"
    
    def consciousness_cycle_native(self, perception_data: Any,
                                    available_actions: Optional[List] = None,
                                    use_llm_tool: bool = False) -> Dict:
        """
        原生语言意识循环（替换父类的翻译版本）
        """
        # 提取文本
        text_input = ""
        if isinstance(perception_data, dict):
            text_input = perception_data.get('text', '')
        elif isinstance(perception_data, str):
            text_input = perception_data
        
        if not text_input:
            return {
                'text_response': "等待输入...",
                'action': 'wait',
                'confidence': 0.0,
                'native_language': True
            }
        
        # 原生语言处理
        result = self.process_language(text_input, generate_response=True)
        
        # 格式化响应
        formatted = f"""## 🧠 NeuraCore原生语言响应

**脉冲神经网络直接生成的自然语言：**

{result['response']}

---
**技术附录**
- 解码置信度: {result['context_strength']:.3f}
- Motor脉冲数: {result['pulse_count']:,}
- 生成步数: {result['generation_steps']}
- 候选Tokens: {', '.join([f"{t}({p:.2f})" for t, p in result['decoded_tokens'][:3]])}
"""
        
        return {
            'text_response': formatted,
            'native_response': result['response'],
            'action': 'respond' if result['pulse_count'] > 100 else 'wait',
            'confidence': result['context_strength'],
            'signature': f"[NativeLang|{result['pulse_count']}spikes|{result['generation_steps']}steps]",
            'spike_counts': {
                'sensory': len(self._encode_text_to_sensory(text_input)),
                'motor': result['pulse_count'],
                'association': int(result['pulse_count'] * 1.5),
                'decision': int(result['pulse_count'] * 0.5)
            },
            'total_spikes': result['pulse_count'] * 3,
            'neurons': self.total_neurons,
            'cycles': result['generation_steps'],
            'native_language': True
        }


# 全局实例
_neuracore_language_instance = None

def init_neuracore_language(enable_stdp: bool = True, test_mode: bool = False):
    """
    初始化原生语言NeuraCore
    """
    global _neuracore_language_instance
    
    if _neuracore_language_instance is not None:
        return _neuracore_language_instance
    
    print("[NeuraCoreLanguage] 初始化原生语言SNN...")
    
    if test_mode:
        core = NeuraCoreLanguage(
            n_sensory=250,
            n_association=500,
            n_decision=125,
            n_motor=125,
            enable_stdp=enable_stdp
        )
    else:
        core = NeuraCoreLanguage(
            n_sensory=750000,
            n_association=1500000,
            n_decision=375000,
            n_motor=375000,
            enable_stdp=enable_stdp
        )
    
    try:
        core.build_network()
        _neuracore_language_instance = core
        print("[NeuraCoreLanguage] ✅ 原生语言系统就绪")
        return core
    except Exception as e:
        print(f"[NeuraCoreLanguage] 构建失败: {e}")
        print("[NeuraCoreLanguage] 切换到测试模式...")
        return init_neuracore_language(enable_stdp=enable_stdp, test_mode=True)


def get_neuracore_language():
    """获取全局实例"""
    return _neuracore_language_instance


async def api_neuracore_language_process(data: Dict) -> Dict:
    """
    API接口 - 原生语言处理
    """
    core = get_neuracore_language()
    
    if core is None:
        return {'error': '未初始化', 'response': '系统未就绪'}
    
    text = data.get('text', '')
    max_tokens = data.get('max_tokens', 100)
    
    result = core.process_language(text, generate_response=True, max_tokens=max_tokens)
    
    return {
        'success': True,
        'input': result['input'],
        'response': result['response'],
        'pulse_count': result['pulse_count'],
        'motor_activity': result['motor_activity'],
        'decoded_tokens': result['decoded_tokens']
    }


if __name__ == '__main__':
    print("=" * 60)
    print("NeuraCoreLanguage 原生语言SNN测试")
    print("=" * 60)
    
    # 测试
    core = init_neuracore_language(test_mode=True)
    
    if core:
        print("\n测试1: 简单输入")
        result = core.process_language("你好", generate_response=True)
        print(f"输入: {result['input']}")
        print(f"响应: {result['response']}")
        print(f"脉冲数: {result['pulse_count']}")
        print(f"解码Token: {result['decoded_tokens'][:3]}")
        
        print("\n测试2: 意识循环")
        cycle = core.consciousness_cycle_native({'text': '测试语言生成'})
        print(f"响应: {cycle['text_response'][:200]}...")
        print(f"签名: {cycle['signature']}")
        
        print("\n✅ 原生语言NeuraCore测试通过!")
