"""
Model Discovery Module (P9 extraction from server_final.py)
============================================================
Remote model discovery for Ollama and llama.cpp backends.
Pure functions with no global state dependency.
"""

import json
import os
import re
import socket
import urllib.request
import urllib.error
from datetime import datetime
from typing import List


def check_remote_service(host: str, port: int, timeout: float = 2.0) -> bool:
    """检查远程服务是否可达"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def fetch_ollama_models(host: str = "192.168.140.1", port: int = 11434) -> dict:
    """获取 Ollama 可用模型列表"""
    result = {
        "available": False,
        "models": [],
        "running_models": [],
        "error": None
    }
    
    try:
        # 检查服务是否可达
        if not check_remote_service(host, port):
            result["error"] = f"无法连接到 {host}:{port}"
            return result
        
        # 获取已安装模型列表
        url = f"http://{host}:{port}/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            models = data.get("models", [])
            result["models"] = [
                {
                    "name": m.get("name", ""),
                    "size": m.get("size", 0),
                    "modified_at": m.get("modified_at", ""),
                    "details": m.get("details", {})
                }
                for m in models
            ]
        
        # 获取正在运行的模型
        url = f"http://{host}:{port}/api/ps"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            running = data.get("models", [])
            result["running_models"] = [m.get("name", "") for m in running]
        
        result["available"] = True
    except urllib.error.URLError as e:
        result["error"] = f"网络错误: {str(e)}"
    except Exception as e:
        result["error"] = f"获取模型列表失败: {str(e)}"
    
    return result


def fetch_llamacpp_models(host: str = "192.168.140.1", port: int = 8090) -> dict:
    """获取 llama.cpp 可用模型列表"""
    result = {
        "available": False,
        "models": [],
        "error": None
    }
    
    try:
        # 检查服务是否可达
        if not check_remote_service(host, port):
            result["error"] = f"无法连接到 {host}:{port}"
            return result
        
        # llama.cpp OpenAI 兼容 API
        url = f"http://{host}:{port}/v1/models"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", "Bearer sk-llamacpp")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            models = data.get("data", [])
            result["models"] = [
                {
                    "id": m.get("id", ""),
                    "owned_by": m.get("owned_by", "llama.cpp")
                }
                for m in models
            ]
        
        result["available"] = True
    except urllib.error.URLError as e:
        result["error"] = f"网络错误: {str(e)}"
    except Exception as e:
        result["error"] = f"获取模型列表失败: {str(e)}"
    
    return result


# 静态配置的模型列表（当无法自动发现时使用）
STATIC_LLAMACPP_MODELS = [
    {"id": "DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf", "size": "18GB", "type": "reasoning"},
    {"id": "Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf", "size": "22GB", "type": "chat"},
    {"id": "Qwen3-Coder-Next-Q4_K_M-00001-of-00004.gguf", "size": "13GB", "type": "code"},
    {"id": "Qwen3-Coder-Next-Q4_K_M-00002-of-00004.gguf", "size": "13GB", "type": "code"},
    {"id": "Qwen3-Coder-Next-Q4_K_M-00003-of-00004.gguf", "size": "13GB", "type": "code"},
    {"id": "Qwen3-Coder-Next-Q4_K_M-00004-of-00004.gguf", "size": "13GB", "type": "code"},
    {"id": "Qwen3.5-122B-A10B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf", "size": "48GB", "type": "chat"},
]

STATIC_OLLAMA_MODELS = [
    {"name": "deepseek-r1:70b", "size": "42GB", "type": "reasoning"},
    {"name": "qwen3-coder-next:q4_K_M", "size": "51GB", "type": "code"},
]


def format_bytes(bytes: int) -> str:
    """格式化字节数"""
    if not bytes:
        return ""
    gb = bytes / (1024 * 1024 * 1024)
    if gb >= 1:
        return f"{gb:.1f}GB"
    mb = bytes / (1024 * 1024)
    return f"{mb:.0f}MB"


def guess_model_type(name: str) -> str:
    """根据模型名称猜测类型"""
    name_lower = name.lower()
    if "coder" in name_lower or "code" in name_lower:
        return "code"
    if "r1" in name_lower or "reasoning" in name_lower or "deepseek-r1" in name_lower:
        return "reasoning"
    if "vl" in name_lower or "vision" in name_lower:
        return "vision"
    if "uncensored" in name_lower:
        return "uncensored"
    return "chat"


def parse_ollama_ps_models(ps_output: str) -> list[str]:
    """从 `ollama ps` 输出中解析模型名称列表（NAME 列）"""
    models: list[str] = []
    if not ps_output:
        return models
    lines = [ln.strip() for ln in ps_output.splitlines() if ln.strip()]
    if not lines:
        return models

    # 示例：
    # NAME                       ID              SIZE     PROCESSOR          CONTEXT    UNTIL
    # qwen3-coder-next:q4_K_M    ca06e9e4087c    55 GB    58%/42% CPU/GPU    32768      About a minute from now
    for ln in lines:
        if ln.upper().startswith("NAME"):
            continue
        parts = re.split(r"\s{2,}", ln)
        if parts and parts[0]:
            models.append(parts[0].strip())
    return models


def discover_remote_models() -> dict:
    """发现所有远程模型并返回状态"""
    # 配置
    ollama_host = os.environ.get("NANOBOT_OLLAMA_HOST", "192.168.140.1")
    ollama_port = int(os.environ.get("NANOBOT_OLLAMA_PORT", "11434"))
    llamacpp_host = os.environ.get("NANOBOT_LLAMACPP_HOST", "192.168.140.1")
    llamacpp_port = int(os.environ.get("NANOBOT_LLAMACPP_PORT", "8090"))
    
    # 获取模型信息
    ollama_info = fetch_ollama_models(ollama_host, ollama_port)
    llamacpp_info = fetch_llamacpp_models(llamacpp_host, llamacpp_port)
    
    # 合并静态配置模型
    # Ollama: 合并自动发现和静态配置
    all_ollama_models = []
    discovered_names = set()
    
    # 先添加自动发现的模型
    if ollama_info["available"] and ollama_info["models"]:
        for m in ollama_info["models"]:
            all_ollama_models.append({
                "name": m["name"],
                "size": m.get("size", 0),
                "size_display": format_bytes(m.get("size", 0)),
                "type": guess_model_type(m["name"]),
                "discovered": True,
                "running": m["name"] in ollama_info.get("running_models", [])
            })
            discovered_names.add(m["name"])
    
    # 添加静态配置中未发现的模型
    for static_m in STATIC_OLLAMA_MODELS:
        if static_m["name"] not in discovered_names:
            all_ollama_models.append({
                "name": static_m["name"],
                "size": 0,
                "size_display": static_m["size"],
                "type": static_m["type"],
                "discovered": False,
                "running": False
            })
    
    # llama.cpp: 合并自动发现和静态配置
    all_llamacpp_models = []
    discovered_ids = set()
    
    # 先添加自动发现的模型
    if llamacpp_info["available"] and llamacpp_info["models"]:
        for m in llamacpp_info["models"]:
            all_llamacpp_models.append({
                "id": m["id"],
                "size_display": "已加载",
                "type": guess_model_type(m["id"]),
                "discovered": True,
                "running": True
            })
            discovered_ids.add(m["id"])
    
    # 添加静态配置中未发现的模型
    for static_m in STATIC_LLAMACPP_MODELS:
        if static_m["id"] not in discovered_ids:
            all_llamacpp_models.append({
                "id": static_m["id"],
                "size_display": static_m["size"],
                "type": static_m["type"],
                "discovered": False,
                "running": False
            })
    
    # 更新 info
    ollama_info["all_models"] = all_ollama_models
    llamacpp_info["all_models"] = all_llamacpp_models
    
    # 构建推荐默认选择
    default_backend = None
    default_model = None
    
    # 优先选择正在运行的模型
    running_ollama = [m for m in all_ollama_models if m.get("running")]
    running_llamacpp = [m for m in all_llamacpp_models if m.get("running")]
    
    if running_ollama:
        default_backend = "ollama"
        default_model = running_ollama[0]["name"]
    elif running_llamacpp:
        default_backend = "ollm"
        default_model = running_llamacpp[0]["id"]
    elif ollama_info["available"] and all_ollama_models:
        default_backend = "ollama"
        # 选择最大的模型作为默认
        discovered_models = [m for m in all_ollama_models if m.get("discovered")]
        if discovered_models:
            default_model = discovered_models[0]["name"]
        else:
            default_model = all_ollama_models[0]["name"]
    elif llamacpp_info["available"] and all_llamacpp_models:
        default_backend = "ollm"
        default_model = all_llamacpp_models[0]["id"]
    elif all_ollama_models:
        default_backend = "ollama"
        default_model = all_ollama_models[0]["name"]
    elif all_llamacpp_models:
        default_backend = "ollm"
        default_model = all_llamacpp_models[0]["id"]
    
    return {
        "ollama": ollama_info,
        "llamacpp": llamacpp_info,
        "recommendations": {
            "default_backend": default_backend,
            "default_model": default_model
        },
        "timestamp": datetime.now().isoformat()
    }
