"""
远程 ComfyUI 诊断模块

功能：
1. 通过 SSH 远程执行命令获取 Win11 主机 ComfyUI 状态
2. 通过 ComfyUI REST API 获取系统信息
3. 读取远程日志文件
4. 诊断远程依赖问题

前提条件：
- Win11 主机启用 OpenSSH Server
- 配置 SSH 密钥认证（免密登录）
- 或 VMware 共享文件夹已挂载
"""

import subprocess
import json
import re
import logging
import requests
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 远程配置
REMOTE_HOST = "192.168.140.1"
REMOTE_USER = "field"  # Win11 用户名，需根据实际情况修改
REMOTE_COMFYUI_PATH = "C:/ComfyUI"  # Win11 上 ComfyUI 路径
COMFYUI_API_URL = f"http://{REMOTE_HOST}:8188"

# VMware 共享文件夹路径
VMWARE_SHARED_PATH = "/mnt/hgfs"


@dataclass
class RemoteDiagnosisResult:
    """远程诊断结果"""
    success: bool
    method: str  # 'ssh', 'api', 'shared_folder'
    data: Dict[str, Any]
    error: Optional[str] = None


class RemoteComfyUIDiagnostics:
    """远程 ComfyUI 诊断器"""
    
    def __init__(
        self,
        host: str = REMOTE_HOST,
        user: str = REMOTE_USER,
        comfyui_path: str = REMOTE_COMFYUI_PATH,
        api_url: str = COMFYUI_API_URL
    ):
        self.host = host
        self.user = user
        self.comfyui_path = comfyui_path
        self.api_url = api_url
        self.ssh_available = False
        self.api_available = False
        self.shared_folder_available = False
    
    def check_connectivity(self) -> Dict[str, bool]:
        """检查各种连接方式可用性"""
        results = {
            'ssh': self._check_ssh(),
            'api': self._check_api(),
            'shared_folder': self._check_shared_folder(),
            'ping': self._check_ping()
        }
        
        self.ssh_available = results['ssh']
        self.api_available = results['api']
        self.shared_folder_available = results['shared_folder']
        
        return results
    
    def _check_ping(self) -> bool:
        """检查网络连通性"""
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '2', self.host],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def _check_ssh(self) -> bool:
        """检查 SSH 连接"""
        try:
            result = subprocess.run(
                ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                 '-o', 'StrictHostKeyChecking=no',
                 f'{self.user}@{self.host}', 'echo ok'],
                capture_output=True,
                text=True,
                timeout=10
            )
            return 'ok' in result.stdout
        except Exception as e:
            logger.debug(f"SSH 检查失败: {e}")
            return False
    
    def _check_api(self) -> bool:
        """检查 ComfyUI API 可用性"""
        try:
            resp = requests.get(f"{self.api_url}/system_stats", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False
    
    def _check_shared_folder(self) -> bool:
        """检查 VMware 共享文件夹"""
        return Path(VMWARE_SHARED_PATH).exists()
    
    # ==================== SSH 远程执行 ====================
    
    def ssh_execute(self, command: str, timeout: int = 30) -> Tuple[bool, str, str]:
        """
        通过 SSH 执行远程命令
        
        Args:
            command: 要执行的命令
            timeout: 超时时间
        
        Returns:
            (success, stdout, stderr)
        """
        if not self.ssh_available:
            # 先尝试连接
            if not self._check_ssh():
                return False, '', 'SSH 连接不可用，请配置 SSH 密钥认证'
        
        try:
            result = subprocess.run(
                ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                 '-o', 'StrictHostKeyChecking=no',
                 f'{self.user}@{self.host}', command],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode == 0, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, '', 'SSH 命令超时'
        except Exception as e:
            return False, '', str(e)
    
    def get_remote_python_packages(self) -> RemoteDiagnosisResult:
        """获取远程 ComfyUI 的 Python 包列表"""
        # Windows 使用 pip list
        cmd = f'cd {self.comfyui_path} && python -m pip list --format=json'
        success, stdout, stderr = self.ssh_execute(cmd)
        
        if success:
            try:
                packages = json.loads(stdout)
                return RemoteDiagnosisResult(
                    success=True,
                    method='ssh',
                    data={'packages': packages, 'count': len(packages)}
                )
            except json.JSONDecodeError:
                pass
        
        return RemoteDiagnosisResult(
            success=False,
            method='ssh',
            data={},
            error=stderr or '无法获取包列表'
        )
    
    def get_remote_cuda_info(self) -> RemoteDiagnosisResult:
        """获取远程 CUDA 信息"""
        cmd = 'nvidia-smi --query-gpu=name,memory.total,memory.free --format=json'
        success, stdout, stderr = self.ssh_execute(cmd)
        
        if success:
            try:
                gpu_info = json.loads(stdout)
                return RemoteDiagnosisResult(
                    success=True,
                    method='ssh',
                    data={'gpu': gpu_info}
                )
            except:
                pass
        
        return RemoteDiagnosisResult(
            success=False,
            method='ssh',
            data={},
            error=stderr or '无法获取 CUDA 信息'
        )
    
    def get_remote_comfyui_log(self, lines: int = 50) -> RemoteDiagnosisResult:
        """获取远程 ComfyUI 日志"""
        # Windows 使用 type 或 Get-Content
        log_path = f"{self.comfyui_path}/comfyui.log"
        cmd = f'powershell "Get-Content {log_path} -Tail {lines}"'
        
        success, stdout, stderr = self.ssh_execute(cmd)
        
        if success:
            return RemoteDiagnosisResult(
                success=True,
                method='ssh',
                data={'log': stdout, 'lines': lines}
            )
        
        return RemoteDiagnosisResult(
            success=False,
            method='ssh',
            data={},
            error=stderr or '无法读取日志'
        )
    
    def check_remote_custom_nodes(self) -> RemoteDiagnosisResult:
        """检查远程自定义节点"""
        cmd = f'dir /b {self.comfyui_path}\\custom_nodes'
        success, stdout, stderr = self.ssh_execute(cmd)
        
        if success:
            nodes = [n.strip() for n in stdout.strip().split('\n') if n.strip()]
            return RemoteDiagnosisResult(
                success=True,
                method='ssh',
                data={'custom_nodes': nodes, 'count': len(nodes)}
            )
        
        return RemoteDiagnosisResult(
            success=False,
            method='ssh',
            data={},
            error=stderr or '无法获取自定义节点列表'
        )
    
    def install_remote_package(self, package: str) -> RemoteDiagnosisResult:
        """远程安装 Python 包"""
        cmd = f'cd {self.comfyui_path} && python -m pip install {package}'
        success, stdout, stderr = self.ssh_execute(cmd, timeout=120)
        
        return RemoteDiagnosisResult(
            success=success,
            method='ssh',
            data={'output': stdout, 'package': package},
            error=stderr if not success else None
        )
    
    def install_remote_custom_node(self, repo_url: str) -> RemoteDiagnosisResult:
        """远程安装自定义节点"""
        custom_nodes_path = f"{self.comfyui_path}\\custom_nodes"
        cmd = f'cd {custom_nodes_path} && git clone {repo_url}'
        success, stdout, stderr = self.ssh_execute(cmd, timeout=60)
        
        return RemoteDiagnosisResult(
            success=success,
            method='ssh',
            data={'output': stdout, 'repo': repo_url},
            error=stderr if not success else None
        )
    
    # ==================== ComfyUI API ====================
    
    def get_system_stats(self) -> RemoteDiagnosisResult:
        """通过 API 获取系统状态"""
        try:
            resp = requests.get(f"{self.api_url}/system_stats", timeout=10)
            if resp.status_code == 200:
                return RemoteDiagnosisResult(
                    success=True,
                    method='api',
                    data=resp.json()
                )
        except Exception as e:
            pass
        
        return RemoteDiagnosisResult(
            success=False,
            method='api',
            data={},
            error='ComfyUI API 不可用'
        )
    
    def get_object_info(self) -> RemoteDiagnosisResult:
        """通过 API 获取所有节点信息"""
        try:
            resp = requests.get(f"{self.api_url}/object_info", timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return RemoteDiagnosisResult(
                    success=True,
                    method='api',
                    data={'nodes': list(data.keys()), 'count': len(data)}
                )
        except Exception as e:
            pass
        
        return RemoteDiagnosisResult(
            success=False,
            method='api',
            data={},
            error='无法获取节点信息'
        )
    
    def get_queue_status(self) -> RemoteDiagnosisResult:
        """获取队列状态"""
        try:
            resp = requests.get(f"{self.api_url}/queue", timeout=10)
            if resp.status_code == 200:
                return RemoteDiagnosisResult(
                    success=True,
                    method='api',
                    data=resp.json()
                )
        except Exception as e:
            pass
        
        return RemoteDiagnosisResult(
            success=False,
            method='api',
            data={},
            error='无法获取队列状态'
        )
    
    # ==================== 综合诊断 ====================
    
    def full_diagnosis(self) -> Dict[str, Any]:
        """
        执行完整诊断
        
        Returns:
            综合诊断报告
        """
        report = {
            'connectivity': self.check_connectivity(),
            'system': {},
            'packages': {},
            'custom_nodes': {},
            'errors': []
        }
        
        # API 方式获取系统状态
        if self.api_available:
            stats = self.get_system_stats()
            if stats.success:
                report['system']['api'] = stats.data
        
        # SSH 方式获取详细信息
        if self.ssh_available:
            # Python 包
            pkg_result = self.get_remote_python_packages()
            if pkg_result.success:
                report['packages'] = pkg_result.data
            
            # 自定义节点
            nodes_result = self.check_remote_custom_nodes()
            if nodes_result.success:
                report['custom_nodes'] = nodes_result.data
            
            # CUDA 信息
            cuda_result = self.get_remote_cuda_info()
            if cuda_result.success:
                report['system']['cuda'] = cuda_result.data
            
            # 日志
            log_result = self.get_remote_comfyui_log()
            if log_result.success:
                report['logs'] = log_result.data
        
        return report
    
    def diagnose_error(self, error_log: str) -> Dict[str, Any]:
        """
        诊断错误并生成修复方案
        
        Args:
            error_log: 错误日志
        
        Returns:
            诊断结果和修复方案
        """
        from comfyui_diagnostics import diagnose_comfyui_error
        
        diagnosis = diagnose_comfyui_error(error_log)
        
        result = {
            'diagnosis': diagnosis,
            'fix_available': False,
            'fix_command': None
        }
        
        # 如果可自动修复，生成远程修复命令
        if diagnosis['auto_fixable']:
            if diagnosis['error_type'].startswith('module_not_found'):
                module = diagnosis.get('missing_module')
                if module:
                    result['fix_available'] = True
                    result['fix_command'] = f"ssh {self.user}@{self.host} 'cd {self.comfyui_path} && python -m pip install {module}'"
            
            elif diagnosis['error_type'] in ['node_not_found', 'custom_node_import_error']:
                node = diagnosis.get('missing_node')
                if node:
                    # 查找仓库
                    from comfyui_diagnostics import CUSTOM_NODES_REGISTRY
                    for key, repo in CUSTOM_NODES_REGISTRY.items():
                        if key.lower() in node.lower():
                            result['fix_available'] = True
                            result['fix_command'] = f"ssh {self.user}@{self.host} 'cd {self.comfyui_path}\\custom_nodes && git clone {repo}'"
                            break
        
        return result


# ==================== SSH 配置指南 ====================

SSH_SETUP_GUIDE = """
# Win11 主机 SSH 配置指南

## 1. 安装 OpenSSH Server (Win11)

以管理员身份运行 PowerShell:

```powershell
# 安装 OpenSSH Server
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# 启动服务
Start-Service sshd

# 设置开机自启
Set-Service -Name sshd -StartupType 'Automatic'

# 确认防火墙允许 SSH (端口 22)
New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

## 2. 配置 SSH 密钥认证 (Ubuntu)

在 Ubuntu 虚拟机中执行:

```bash
# 生成密钥对（如果还没有）
ssh-keygen -t ed25519 -C "ubuntu-vm"

# 复制公钥到 Win11 主机
ssh-copy-id field@192.168.140.1

# 或者手动复制
cat ~/.ssh/id_ed25519.pub | ssh field@192.168.140.1 "cat >> .ssh/authorized_keys"

# 测试连接
ssh field@192.168.140.1 "echo 连接成功"
```

## 3. Win11 用户目录权限

确保 Win11 上的 .ssh 目录权限正确:

```powershell
# 在 Win11 PowerShell 中执行
icacls C:\\Users\\field\\.ssh /inheritance:r
icacls C:\\Users\\field\\.ssh /grant:r "field:(OI)(CI)F"
icacls C:\\Users\\field\\.ssh\\authorized_keys /inheritance:r
icacls C:\\Users\\field\\.ssh\\authorized_keys /grant:r "field:F"
```

## 4. 测试 SSH 连接

```bash
# 在 Ubuntu 中测试
ssh field@192.168.140.1 "python --version"
ssh field@192.168.140.1 "nvidia-smi"
ssh field@192.168.140.1 "cd C:/ComfyUI && python -m pip list"
```
"""


def setup_ssh_guide():
    """打印 SSH 配置指南"""
    print(SSH_SETUP_GUIDE)


def quick_diagnosis() -> Dict[str, Any]:
    """
    快速诊断远程 ComfyUI
    
    自动检测可用连接方式并执行诊断
    """
    diagnostics = RemoteComfyUIDiagnostics()
    return diagnostics.full_diagnosis()


if __name__ == '__main__':
    print("=== 远程 ComfyUI 连接诊断 ===\n")
    
    diagnostics = RemoteComfyUIDiagnostics()
    
    # 检查连接
    print("1. 检查连接方式...")
    connectivity = diagnostics.check_connectivity()
    for method, available in connectivity.items():
        mark = "✓" if available else "✗"
        print(f"   {mark} {method}")
    
    # 如果 API 可用，获取系统状态
    if connectivity.get('api'):
        print("\n2. ComfyUI 系统状态 (API)...")
        stats = diagnostics.get_system_stats()
        if stats.success:
            print(f"   系统信息: {json.dumps(stats.data.get('system', {}), indent=2)[:200]}...")
    
    # 如果 SSH 可用，获取详细信息
    if connectivity.get('ssh'):
        print("\n3. 远程包信息 (SSH)...")
        packages = diagnostics.get_remote_python_packages()
        if packages.success:
            print(f"   已安装 {packages.data.get('count', 0)} 个 Python 包")
    
    # 如果都不可用，显示配置指南
    if not connectivity.get('ssh') and not connectivity.get('api'):
        print("\n" + "="*50)
        print("SSH 和 API 都不可用，请按以下步骤配置:")
        print("="*50)
        setup_ssh_guide()
