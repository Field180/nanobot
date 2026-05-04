"""
ComfyUI 诊断与修复模块

功能：
1. 解析 ComfyUI 错误日志
2. 识别缺失的 Python 包和自定义节点
3. 自动安装依赖或提供安装命令
4. 远程执行修复操作（针对 Win11 主机）

使用场景：
- 工作流加载失败
- 节点执行报错
- 模型/依赖缺失
"""

import re
import json
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 远程 ComfyUI 配置
COMFYUI_REMOTE_URL = "http://192.168.140.1:8188"

# ComfyUI 自定义节点仓库映射
CUSTOM_NODES_REGISTRY = {
    # 常用自定义节点
    'ComfyUI_IPAdapter_plus': 'https://github.com/cubiq/ComfyUI_IPAdapter_plus',
    'ComfyUI_Comfyroll_CustomNodes': 'https://github.com/RockOfFire/ComfyUI_Comfyroll_CustomNodes',
    'ComfyUI_WAS_Nodes': 'https://github.com/WASasquatch/ComfyUI_WAS_Nodes',
    'ComfyUI_AnimateDiff': 'https://github.com/ArtVentureX/comfyui-animatediff',
    'ComfyUI_ControlNet': 'https://github.com/Kosinkadink/ComfyUI-Controlnet',
    'ComfyUI_VideoHelperSuite': 'https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite',
    'ComfyUI_UltimateSDUpscale': 'https://github.com/ssitu/ComfyUI_UltimateSDUpscale',
    'ComfyUI_SegmentAnything': 'https://github.com/storyblock/comfyUI_segment_anything',
    'was-node-suite-comfyui': 'https://github.com/WASasquatch/was-node-suite-comfyui',
    'comfyui-impact-pack': 'https://github.com/ltdrdata/ComfyUI-Impact-Pack',
    'ComfyUI-Advanced-ControlNet': 'https://github.com/kenjiqq/ComfyUI-Advanced-ControlNet',
    'ComfyUI_FaceAnalysis': 'https://github.com/cubiq/ComfyUI_FaceAnalysis',
    'ComfyUI_InstantID': 'https://github.com/cubiq/ComfyUI_InstantID',
}

# Python 包到 pip 包名的映射
PACKAGE_MAPPING = {
    'xformers': 'xformers',
    'torch': 'torch',
    'diffusers': 'diffusers',
    'transformers': 'transformers',
    'accelerate': 'accelerate',
    'safetensors': 'safetensors',
    'opencv': 'opencv-python',
    'cv2': 'opencv-python',
    'PIL': 'Pillow',
    'pillow': 'Pillow',
    'onnx': 'onnx',
    'onnxruntime': 'onnxruntime',
    'insightface': 'insightface',
    'segment_anything': 'segment-anything',
    'groundingdino': 'groundingdino-py',
    'ultralytics': 'ultralytics',
    'yolov8': 'ultralytics',
    'einops': 'einops',
    'omegaconf': 'omegaconf',
    'pytorch_lightning': 'pytorch-lightning',
    'kornia': 'kornia',
    'facexlib': 'facexlib',
    'realesrgan': 'realesrgan',
    'gfpgan': 'gfpgan',
    'clip': 'git+https://github.com/openai/CLIP.git',
    'open_clip': 'open-clip-torch',
    'bitsandbytes': 'bitsandbytes',
    'scipy': 'scipy',
    'numpy': 'numpy',
    'tqdm': 'tqdm',
    'huggingface_hub': 'huggingface-hub',
    'safetensors': 'safetensors',
}


@dataclass
class ComfyUIError:
    """ComfyUI 错误信息"""
    error_type: str  # 'module_not_found', 'node_not_found', 'model_not_found', 'cuda_error', 'unknown'
    raw_message: str
    missing_module: Optional[str] = None
    missing_node: Optional[str] = None
    missing_model: Optional[str] = None
    suggested_fix: Optional[str] = None
    auto_fixable: bool = False


class ComfyUIDiagnostics:
    """ComfyUI 诊断器"""
    
    def __init__(self, comfyui_path: Optional[str] = None):
        """
        初始化诊断器
        
        Args:
            comfyui_path: ComfyUI 安装路径（本地或远程）
        """
        self.comfyui_path = comfyui_path
        self.error_patterns = self._build_error_patterns()
    
    def _build_error_patterns(self) -> List[Tuple[str, re.Pattern, str]]:
        """构建错误匹配模式"""
        return [
            # Python 模块缺失
            (
                'module_not_found',
                re.compile(r"ModuleNotFoundError: No module named '([^']+)'"),
                'pip_install'
            ),
            (
                'module_not_found_v2',
                re.compile(r"ImportError: cannot import name '([^']+)' from"),
                'pip_install'
            ),
            (
                'module_not_found_v3',
                re.compile(r"ImportError: No module named '([^']+)'"),
                'pip_install'
            ),
            # 自定义节点缺失
            (
                'node_not_found',
                re.compile(r"Failed to load node ([^:]+):"),
                'custom_node_install'
            ),
            (
                'node_not_found_v2',
                re.compile(r"Cannot find node type '([^']+)'"),
                'custom_node_install'
            ),
            (
                'custom_node_import_error',
                re.compile(r"Failed to import custom node '([^']+)'"),
                'custom_node_install'
            ),
            # 模型缺失
            (
                'model_not_found',
                re.compile(r"Checkpoint ([^ ]+) not found"),
                'model_download'
            ),
            (
                'model_not_found_v2',
                re.compile(r"Model file does not exist: ([^\n]+)"),
                'model_download'
            ),
            (
                'lora_not_found',
                re.compile(r"Lora not found: ([^\n]+)"),
                'model_download'
            ),
            # CUDA 错误
            (
                'cuda_oom',
                re.compile(r"CUDA out of memory"),
                'reduce_batch_size'
            ),
            (
                'cuda_error',
                re.compile(r"CUDA error: ([^\n]+)"),
                'cuda_debug'
            ),
            # 版本冲突
            (
                'version_conflict',
                re.compile(r"([a-zA-Z0-9_-]+)>=([0-9.]+) required, but ([0-9.]+) installed"),
                'version_upgrade'
            ),
        ]
    
    def parse_error(self, error_log: str) -> ComfyUIError:
        """
        解析 ComfyUI 错误日志
        
        Args:
            error_log: 错误日志文本
        
        Returns:
            ComfyUIError 对象
        """
        for error_type, pattern, fix_type in self.error_patterns:
            match = pattern.search(error_log)
            if match:
                error = ComfyUIError(
                    error_type=error_type,
                    raw_message=error_log
                )
                
                # 填充具体信息
                if error_type == 'module_not_found' or error_type.startswith('module_not_found'):
                    error.missing_module = match.group(1)
                    error.suggested_fix = self._get_pip_install_cmd(match.group(1))
                    error.auto_fixable = True
                
                elif error_type.startswith('node_not_found') or error_type == 'custom_node_import_error':
                    error.missing_node = match.group(1)
                    error.suggested_fix = self._get_custom_node_install_cmd(match.group(1))
                    error.auto_fixable = True
                
                elif error_type.startswith('model_not_found') or error_type == 'lora_not_found':
                    error.missing_model = match.group(1)
                    error.suggested_fix = f"请下载模型: {match.group(1)}"
                    error.auto_fixable = False  # 模型通常需要手动下载
                
                elif error_type == 'cuda_oom':
                    error.suggested_fix = "减少 batch_size 或使用 --lowvram 参数"
                    error.auto_fixable = False
                
                elif error_type == 'version_conflict':
                    pkg = match.group(1)
                    required = match.group(2)
                    error.suggested_fix = f"pip install --upgrade {pkg}"
                    error.auto_fixable = True
                
                return error
        
        # 未知错误
        return ComfyUIError(
            error_type='unknown',
            raw_message=error_log,
            auto_fixable=False
        )
    
    def _get_pip_install_cmd(self, module_name: str) -> str:
        """获取 pip 安装命令"""
        pip_name = PACKAGE_MAPPING.get(module_name, module_name)
        return f"pip install {pip_name}"
    
    def _get_custom_node_install_cmd(self, node_name: str) -> str:
        """获取自定义节点安装命令"""
        # 尝试匹配已知仓库
        for key, repo in CUSTOM_NODES_REGISTRY.items():
            if key.lower() in node_name.lower() or node_name.lower() in key.lower():
                return f"cd custom_nodes && git clone {repo}"
        
        # 通用建议
        return f"请在 custom_nodes 目录下安装包含 '{node_name}' 的节点"
    
    def diagnose_workflow(self, workflow: Dict) -> Dict[str, Any]:
        """
        诊断工作流依赖
        
        Args:
            workflow: ComfyUI 工作流 JSON
        
        Returns:
            诊断结果
        """
        required_nodes = set()
        required_models = set()
        
        # 遍历工作流节点
        for node_id, node_data in workflow.get('nodes', {}).items():
            node_type = node_data.get('class_type', node_data.get('type', ''))
            required_nodes.add(node_type)
            
            # 检查模型输入
            inputs = node_data.get('inputs', {})
            for key, value in inputs.items():
                if key in ['ckpt_name', 'checkpoint', 'model_name', 'lora_name']:
                    if isinstance(value, str):
                        required_models.add(value)
        
        return {
            'required_nodes': list(required_nodes),
            'required_models': list(required_models),
            'estimated_missing': self._estimate_missing(required_nodes, required_models)
        }
    
    def _estimate_missing(self, nodes: set, models: set) -> Dict:
        """预估缺失的依赖"""
        # 这里可以连接远程 ComfyUI 检查实际缺失
        return {
            'nodes': [],
            'models': []
        }


class ComfyUIFixer:
    """ComfyUI 自动修复器"""
    
    def __init__(self, comfyui_path: str, is_remote: bool = False):
        """
        初始化修复器
        
        Args:
            comfyui_path: ComfyUI 路径
            is_remote: 是否为远程 ComfyUI
        """
        self.comfyui_path = comfyui_path
        self.is_remote = is_remote
        self.diagnostics = ComfyUIDiagnostics(comfyui_path)
    
    def fix_error(self, error: ComfyUIError) -> Dict[str, Any]:
        """
        尝试修复错误
        
        Args:
            error: ComfyUIError 对象
        
        Returns:
            修复结果
        """
        if not error.auto_fixable:
            return {
                'success': False,
                'error': '此错误无法自动修复',
                'suggested_fix': error.suggested_fix
            }
        
        if error.error_type.startswith('module_not_found'):
            return self._install_pip_package(error.missing_module)
        
        elif error.error_type.startswith('node_not_found') or error.error_type == 'custom_node_import_error':
            return self._install_custom_node(error.missing_node)
        
        elif error.error_type == 'version_conflict':
            return self._upgrade_package(error.suggested_fix)
        
        return {
            'success': False,
            'error': '未知的修复类型'
        }
    
    def _install_pip_package(self, module_name: str) -> Dict[str, Any]:
        """安装 pip 包"""
        pip_name = PACKAGE_MAPPING.get(module_name, module_name)
        
        if self.is_remote:
            # 远程 ComfyUI - 生成 SSH 命令
            return {
                'success': False,
                'requires_ssh': True,
                'command': f"pip install {pip_name}",
                'hint': f'请在 Win11 主机上执行: pip install {pip_name}'
            }
        
        # 本地安装
        try:
            result = subprocess.run(
                ['pip', 'install', pip_name],
                capture_output=True,
                text=True,
                timeout=120
            )
            
            return {
                'success': result.returncode == 0,
                'output': result.stdout,
                'error': result.stderr if result.returncode != 0 else None
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _install_custom_node(self, node_name: str) -> Dict[str, Any]:
        """安装自定义节点"""
        # 查找匹配的仓库
        repo_url = None
        for key, repo in CUSTOM_NODES_REGISTRY.items():
            if key.lower() in node_name.lower() or node_name.lower() in key.lower():
                repo_url = repo
                break
        
        if not repo_url:
            return {
                'success': False,
                'error': f'未找到自定义节点仓库: {node_name}',
                'hint': '请手动在 custom_nodes 目录下安装相应节点'
            }
        
        if self.is_remote:
            return {
                'success': False,
                'requires_ssh': True,
                'command': f"cd custom_nodes && git clone {repo_url}",
                'hint': f'请在 Win11 主机上执行: cd ComfyUI/custom_nodes && git clone {repo_url}'
            }
        
        # 本地安装
        custom_nodes_path = Path(self.comfyui_path) / 'custom_nodes'
        custom_nodes_path.mkdir(parents=True, exist_ok=True)
        
        try:
            result = subprocess.run(
                ['git', 'clone', repo_url],
                cwd=str(custom_nodes_path),
                capture_output=True,
                text=True,
                timeout=60
            )
            
            return {
                'success': result.returncode == 0,
                'output': result.stdout,
                'error': result.stderr if result.returncode != 0 else None,
                'installed_path': str(custom_nodes_path / repo_url.split('/')[-1].replace('.git', ''))
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _upgrade_package(self, suggested_fix: str) -> Dict[str, Any]:
        """升级包"""
        # 从建议中提取包名
        match = re.search(r'pip install --upgrade ([^\s]+)', suggested_fix)
        if not match:
            return {
                'success': False,
                'error': '无法解析升级命令'
            }
        
        pkg_name = match.group(1)
        
        if self.is_remote:
            return {
                'success': False,
                'requires_ssh': True,
                'command': f"pip install --upgrade {pkg_name}",
                'hint': f'请在 Win11 主机上执行: pip install --upgrade {pkg_name}'
            }
        
        try:
            result = subprocess.run(
                ['pip', 'install', '--upgrade', pkg_name],
                capture_output=True,
                text=True,
                timeout=120
            )
            
            return {
                'success': result.returncode == 0,
                'output': result.stdout,
                'error': result.stderr if result.returncode != 0 else None
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }


def diagnose_comfyui_error(error_log: str) -> Dict[str, Any]:
    """
    便捷函数：诊断 ComfyUI 错误
    
    Args:
        error_log: 错误日志
    
    Returns:
        诊断结果和建议修复方案
    """
    diagnostics = ComfyUIDiagnostics()
    error = diagnostics.parse_error(error_log)
    
    return {
        'error_type': error.error_type,
        'missing_module': error.missing_module,
        'missing_node': error.missing_node,
        'missing_model': error.missing_model,
        'suggested_fix': error.suggested_fix,
        'auto_fixable': error.auto_fixable,
        'raw_message': error.raw_message
    }


def fix_comfyui_error(error_log: str, comfyui_path: str = None, is_remote: bool = True) -> Dict[str, Any]:
    """
    便捷函数：修复 ComfyUI 错误
    
    Args:
        error_log: 错误日志
        comfyui_path: ComfyUI 路径
        is_remote: 是否为远程 ComfyUI
    
    Returns:
        修复结果
    """
    diagnostics = ComfyUIDiagnostics(comfyui_path)
    error = diagnostics.parse_error(error_log)
    
    if not error.auto_fixable:
        return {
            'success': False,
            'diagnosis': {
                'error_type': error.error_type,
                'suggested_fix': error.suggested_fix
            }
        }
    
    fixer = ComfyUIFixer(comfyui_path or '', is_remote)
    return fixer.fix_error(error)


if __name__ == '__main__':
    # 测试诊断功能
    test_errors = [
        "ModuleNotFoundError: No module named 'xformers'",
        "Failed to import custom node 'ComfyUI_IPAdapter_plus'",
        "Checkpoint sd_xl_base.safetensors not found",
        "CUDA out of memory. Tried to allocate 2.00 GiB",
    ]
    
    print("=== ComfyUI 错误诊断测试 ===\n")
    for error in test_errors:
        result = diagnose_comfyui_error(error)
        print(f"错误: {error[:50]}...")
        print(f"  类型: {result['error_type']}")
        print(f"  可自动修复: {result['auto_fixable']}")
        print(f"  建议: {result['suggested_fix']}")
        print()
