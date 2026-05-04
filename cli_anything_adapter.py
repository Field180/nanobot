"""
CLI-Anything 适配器 - 将 CLI-Anything 工具集成到 Nanobot 工具执行框架

功能：
1. 加载 CLI-Anything registry.json 中的工具定义
2. 动态注册 CLI 工具到 Nanobot 工具系统
3. 统一执行接口，将 CLI 输出转换为 Nanobot 结果格式
"""

import json
import subprocess
import shutil
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# CLI-Anything 默认路径
CLI_ANYTHING_DEFAULT_PATH = Path.home() / "opencode" / "CLI-Anything"
REGISTRY_FILE = "registry.json"

# ComfyUI 远程服务器配置（Win11 主机）
COMFYUI_REMOTE_URL = "http://192.168.140.1:8188"


class CLIAnythingAdapter:
    """CLI-Anything 工具适配器"""
    
    def __init__(self, cli_anything_path: Optional[Path] = None):
        """
        初始化适配器
        
        Args:
            cli_anything_path: CLI-Anything 仓库路径，默认使用 ~/opencode/CLI-Anything
        """
        self.cli_anything_path = cli_anything_path or CLI_ANYTHING_DEFAULT_PATH
        self.registry: Dict[str, Any] = {}
        self.installed_tools: Dict[str, Dict] = {}
        self._load_registry()
    
    def _load_registry(self) -> bool:
        """加载 CLI-Anything registry.json"""
        registry_path = self.cli_anything_path / REGISTRY_FILE
        
        if not registry_path.exists():
            logger.warning(f"CLI-Anything registry 不存在: {registry_path}")
            return False
        
        try:
            with open(registry_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.registry = data.get('clis', [])
                logger.info(f"已加载 {len(self.registry)} 个 CLI-Anything 工具定义")
                return True
        except Exception as e:
            logger.error(f"加载 registry 失败: {e}")
            return False
    
    def get_available_tools(self) -> List[Dict[str, Any]]:
        """获取所有可用的 CLI-Anything 工具"""
        return self.registry
    
    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """获取指定工具的信息"""
        for tool in self.registry:
            if tool.get('name') == tool_name:
                return tool
        return None
    
    def check_tool_installed(self, entry_point: str) -> bool:
        """检查工具是否已安装"""
        return shutil.which(entry_point) is not None
    
    def scan_installed_tools(self) -> Dict[str, bool]:
        """扫描所有工具的安装状态"""
        status = {}
        for tool in self.registry:
            entry_point = tool.get('entry_point', '')
            name = tool.get('name', '')
            if entry_point:
                status[name] = self.check_tool_installed(entry_point)
                if status[name]:
                    self.installed_tools[name] = tool
        return status
    
    def execute(
        self,
        tool_name: str,
        command: str,
        args: List[str] = None,
        json_output: bool = True,
        timeout: int = 60,
        cwd: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        执行 CLI-Anything 工具命令
        
        Args:
            tool_name: 工具名称（如 'gimp', 'blender'）
            command: 子命令（如 'layer', 'export'）
            args: 额外参数列表
            json_output: 是否使用 --json 输出格式
            timeout: 执行超时时间（秒）
            cwd: 工作目录
        
        Returns:
            Nanobot 标准结果格式:
            {
                'success': bool,
                'output': str | dict,
                'error': str | None,
                'tool': str,
                'command': str
            }
        """
        tool_info = self.get_tool_info(tool_name)
        if not tool_info:
            return {
                'success': False,
                'output': None,
                'error': f'未知 CLI-Anything 工具: {tool_name}',
                'tool': tool_name,
                'command': command
            }
        
        entry_point = tool_info.get('entry_point', '')
        if not self.check_tool_installed(entry_point):
            install_cmd = tool_info.get('install_cmd', '')
            return {
                'success': False,
                'output': None,
                'error': f'工具未安装: {entry_point}。安装命令: {install_cmd}',
                'tool': tool_name,
                'command': command,
                'install_hint': install_cmd
            }
        
        # 构建命令
        cmd = [entry_point]
        
        if json_output:
            cmd.append('--json')
        
        if command:
            cmd.append(command)
        
        if args:
            cmd.extend(args)
        
        logger.info(f"执行 CLI-Anything 命令: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd
            )
            
            output = result.stdout.strip()
            error_output = result.stderr.strip()
            
            # 尝试解析 JSON 输出
            parsed_output = None
            if json_output and output:
                try:
                    parsed_output = json.loads(output)
                except json.JSONDecodeError:
                    parsed_output = output
            
            if result.returncode == 0:
                return {
                    'success': True,
                    'output': parsed_output or output,
                    'error': None,
                    'tool': tool_name,
                    'command': command,
                    'returncode': result.returncode
                }
            else:
                return {
                    'success': False,
                    'output': parsed_output or output,
                    'error': error_output or f'命令返回非零状态: {result.returncode}',
                    'tool': tool_name,
                    'command': command,
                    'returncode': result.returncode
                }
                
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'output': None,
                'error': f'命令执行超时 ({timeout}秒)',
                'tool': tool_name,
                'command': command
            }
        except Exception as e:
            return {
                'success': False,
                'output': None,
                'error': f'执行异常: {str(e)}',
                'tool': tool_name,
                'command': command
            }
    
    def get_tool_schemas(self) -> Dict[str, Dict]:
        """
        生成所有已安装工具的 JSON Schema（用于 LLM function calling）
        
        Returns:
            工具名称 -> Schema 映射
        """
        schemas = {}
        
        for tool in self.registry:
            name = tool.get('name', '')
            entry_point = tool.get('entry_point', '')
            
            if not self.check_tool_installed(entry_point):
                continue
            
            # 基础 schema
            schema = {
                'name': f'cli_{name}',
                'description': tool.get('description', ''),
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'command': {
                            'type': 'string',
                            'description': f'{name} 子命令'
                        },
                        'args': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': '命令参数列表'
                        },
                        'json_output': {
                            'type': 'boolean',
                            'default': True,
                            'description': '是否使用 JSON 输出格式'
                        }
                    },
                    'required': ['command']
                }
            }
            
            schemas[f'cli_{name}'] = schema
        
        return schemas


# 全局适配器实例
_adapter_instance: Optional[CLIAnythingAdapter] = None


def get_adapter() -> CLIAnythingAdapter:
    """获取全局适配器实例"""
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = CLIAnythingAdapter()
    return _adapter_instance


def execute_cli_anything(
    tool_name: str,
    command: str,
    args: List[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    便捷函数：执行 CLI-Anything 工具
    
    Args:
        tool_name: 工具名称
        command: 子命令
        args: 参数列表
        **kwargs: 其他参数传递给 execute
    
    Returns:
        执行结果
    """
    adapter = get_adapter()
    return adapter.execute(tool_name, command, args, **kwargs)


# CLI-Anything 工具权限映射
CLI_ANYTHING_PERMISSION_MAP = {
    # 图像处理类
    'cli_gimp': 'file_write',
    'cli_blender': 'file_write',
    'cli_comfyui': 'api_call',
    'cli_drawio': 'file_write',
    'cli_inkscape': 'file_write',
    'cli_krita': 'file_write',
    
    # 音视频处理类
    'cli_audacity': 'file_write',
    'cli_shotcut': 'file_write',
    'cli_obs-studio': 'file_write',
    'cli_kdenlive': 'file_write',
    
    # 网络/API 类
    'cli_browser': 'internet',
    'cli_adguardhome': 'api_call',
    'cli_ollama': 'model_call',
    'cli_novita': 'api_call',
    
    # 文档处理类
    'cli_libreoffice': 'file_write',
    'cli_musescore': 'file_write',
    
    # 其他
    'cli_anygen': 'api_call',
    'cli_freecad': 'file_write',
}


def get_permission_type(tool_name: str) -> Optional[str]:
    """获取 CLI-Anything 工具对应的权限类型"""
    return CLI_ANYTHING_PERMISSION_MAP.get(tool_name)


def execute_comfyui_remote(
    command: str,
    args: List[str] = None,
    base_url: str = None,
    **kwargs
) -> Dict[str, Any]:
    """
    执行远程 ComfyUI 命令（通过 REST API）
    
    这个函数直接通过 HTTP 与远程 ComfyUI 服务器通信，
    无需本地安装 ComfyUI，适合无 GPU 的虚拟机环境。
    
    Args:
        command: ComfyUI 命令
            - 'status': 获取服务器状态
            - 'models': 列出可用模型
            - 'queue': 队列操作
            - 'images': 图片操作
        args: 参数列表
        base_url: ComfyUI 服务器地址，默认使用 COMFYUI_REMOTE_URL
        **kwargs: 其他参数
    
    Returns:
        执行结果字典
    """
    import requests
    
    base_url = base_url or COMFYUI_REMOTE_URL
    
    # 命令到 API 端点的映射
    api_endpoints = {
        'status': ('GET', '/system_stats'),
        'models_checkpoints': ('GET', '/object_info/CheckpointLoaderSimple'),
        'queue': ('GET', '/queue'),
        'queue_clear': ('POST', '/queue'),
        'history': ('GET', '/history'),
    }
    
    # 解析命令
    cmd_key = command.replace(' ', '_')
    if cmd_key not in api_endpoints:
        return {
            'success': False,
            'error': f'未知 ComfyUI 命令: {command}',
            'available_commands': list(api_endpoints.keys())
        }
    
    method, endpoint = api_endpoints[cmd_key]
    url = f"{base_url.rstrip('/')}{endpoint}"
    
    try:
        if method == 'GET':
            resp = requests.get(url, timeout=30)
        else:
            resp = requests.post(url, json=args[0] if args else {}, timeout=30)
        
        resp.raise_for_status()
        
        return {
            'success': True,
            'output': resp.json() if resp.content else {'status': 'ok'},
            'command': command,
            'server': base_url
        }
        
    except requests.exceptions.ConnectionError:
        return {
            'success': False,
            'error': f'无法连接 ComfyUI 服务器: {base_url}',
            'hint': '请确保 Win11 主机上的 ComfyUI 正在运行 (python main.py --listen 0.0.0.0)'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'command': command
        }


def download_comfyui_image(
    filename: str,
    output_path: str,
    base_url: str = None
) -> Dict[str, Any]:
    """
    从远程 ComfyUI 下载生成的图片
    
    Args:
        filename: ComfyUI 输出目录中的文件名
        output_path: 本地保存路径
        base_url: ComfyUI 服务器地址
    
    Returns:
        下载结果
    """
    import requests
    
    base_url = base_url or COMFYUI_REMOTE_URL
    url = f"{base_url}/view?filename={filename}"
    
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        
        # 保存图片
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'wb') as f:
            f.write(resp.content)
        
        return {
            'success': True,
            'output': output_path,
            'filename': filename,
            'size_bytes': len(resp.content)
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'filename': filename
        }


if __name__ == '__main__':
    # 测试适配器
    adapter = get_adapter()
    
    print("=== CLI-Anything 工具状态 ===")
    status = adapter.scan_installed_tools()
    for name, installed in status.items():
        mark = "✓" if installed else "✗"
        print(f"  {mark} {name}")
    
    print("\n=== 可用工具 Schema ===")
    schemas = adapter.get_tool_schemas()
    for name, schema in list(schemas.items())[:3]:
        print(f"  {name}: {schema['description'][:50]}...")
