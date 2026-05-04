"""
错误处理器 - 统一的错误处理和友好提示
"""
import logging
from typing import Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """错误类型枚举"""
    TIMEOUT = "timeout"
    NETWORK = "network"
    MODEL = "model"
    SNN = "snn"
    TOOL = "tool"
    FILE = "file"
    SESSION = "session"
    VALIDATION = "validation"
    UNKNOWN = "unknown"


# 友好错误提示模板
ERROR_MESSAGES = {
    ErrorType.TIMEOUT: {
        "title": "⏱️ 请求超时",
        "suggestions": [
            "模型正在深度思考，请稍后重试",
            "问题较复杂，尝试简化描述",
            "检查网络连接状态"
        ]
    },
    ErrorType.NETWORK: {
        "title": "🌐 网络连接问题",
        "suggestions": [
            "检查Ollama服务是否运行",
            "确认API地址配置正确",
            "尝试重启服务"
        ]
    },
    ErrorType.MODEL: {
        "title": "🤖 模型处理问题",
        "suggestions": [
            "模型可能未加载，请稍等",
            "尝试使用更简单的提示词",
            "检查模型配置"
        ]
    },
    ErrorType.SNN: {
        "title": "⚡ SNN处理问题",
        "suggestions": [
            "SNN处理器初始化中",
            "尝试切换到普通模式",
            "检查SNN配置参数"
        ]
    },
    ErrorType.TOOL: {
        "title": "🔧 工具执行问题",
        "suggestions": [
            "检查工具参数是否正确",
            "确认操作权限",
            "查看详细错误日志"
        ]
    },
    ErrorType.FILE: {
        "title": "📁 文件操作问题",
        "suggestions": [
            "检查文件路径是否正确",
            "确认文件权限",
            "确保磁盘空间充足"
        ]
    },
    ErrorType.SESSION: {
        "title": "💬 会话问题",
        "suggestions": [
            "尝试创建新会话",
            "清除浏览器缓存",
            "检查会话是否过期"
        ]
    },
    ErrorType.VALIDATION: {
        "title": "✅ 输入验证问题",
        "suggestions": [
            "检查输入格式",
            "确保必填字段完整",
            "避免特殊字符"
        ]
    },
    ErrorType.UNKNOWN: {
        "title": "❓ 未知错误",
        "suggestions": [
            "请稍后重试",
            "查看服务器日志",
            "联系管理员"
        ]
    }
}


def classify_error(error: Exception) -> ErrorType:
    """根据异常类型分类错误"""
    error_str = str(error).lower()
    
    if "timeout" in error_str or "timed out" in error_str:
        return ErrorType.TIMEOUT
    elif "connection" in error_str or "network" in error_str or "refused" in error_str:
        return ErrorType.NETWORK
    elif "model" in error_str or "ollama" in error_str or "llm" in error_str:
        return ErrorType.MODEL
    elif "snn" in error_str or "spike" in error_str or "pulse" in error_str:
        return ErrorType.SNN
    elif "tool" in error_str or "execute" in error_str or "command" in error_str:
        return ErrorType.TOOL
    elif "file" in error_str or "path" in error_str or "not found" in error_str:
        return ErrorType.FILE
    elif "session" in error_str:
        return ErrorType.SESSION
    elif "validation" in error_str or "invalid" in error_str or "required" in error_str:
        return ErrorType.VALIDATION
    else:
        return ErrorType.UNKNOWN


def format_error_response(
    error: Exception,
    error_type: Optional[ErrorType] = None,
    context: Optional[str] = None,
    include_technical: bool = False
) -> Dict[str, Any]:
    """
    格式化错误响应
    
    Args:
        error: 异常对象
        error_type: 错误类型（自动推断）
        context: 错误上下文描述
        include_technical: 是否包含技术细节
    
    Returns:
        格式化的错误响应字典
    """
    if error_type is None:
        error_type = classify_error(error)
    
    template = ERROR_MESSAGES.get(error_type, ERROR_MESSAGES[ErrorType.UNKNOWN])
    
    response = {
        "success": False,
        "error": {
            "type": error_type.value,
            "title": template["title"],
            "message": context or str(error),
            "suggestions": template["suggestions"]
        }
    }
    
    if include_technical:
        response["error"]["technical"] = {
            "exception": type(error).__name__,
            "details": str(error)
        }
    
    # 记录错误日志
    logger.error(f"[{error_type.value.upper()}] {context or str(error)}", exc_info=True)
    
    return response


def create_user_friendly_message(error_type: ErrorType, context: str = "") -> str:
    """创建用户友好的错误消息"""
    template = ERROR_MESSAGES.get(error_type, ERROR_MESSAGES[ErrorType.UNKNOWN])
    
    message = f"{template['title']}\n\n"
    if context:
        message += f"详情: {context}\n\n"
    message += "建议:\n"
    for i, suggestion in enumerate(template["suggestions"], 1):
        message += f"{i}. {suggestion}\n"
    
    return message


class ErrorHandler:
    """错误处理器类"""
    
    def __init__(self, include_technical: bool = False):
        self.include_technical = include_technical
        self.error_counts = {}
    
    def handle(self, error: Exception, context: str = "") -> Dict[str, Any]:
        """处理错误并返回响应"""
        error_type = classify_error(error)
        
        # 统计错误次数
        self.error_counts[error_type.value] = self.error_counts.get(error_type.value, 0) + 1
        
        return format_error_response(
            error,
            error_type=error_type,
            context=context,
            include_technical=self.include_technical
        )
    
    def get_error_stats(self) -> Dict[str, int]:
        """获取错误统计"""
        return self.error_counts.copy()
    
    def reset_stats(self):
        """重置统计"""
        self.error_counts.clear()


# 全局错误处理器实例
_global_handler = ErrorHandler()


def get_error_handler() -> ErrorHandler:
    """获取全局错误处理器"""
    return _global_handler
