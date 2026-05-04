"""
安全HTTP头部配置
集成CSP和其他安全相关头部
"""

from fastapi import Request, Response
from fastapi.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    安全头部中间件
    
    为所有响应添加安全相关的HTTP头部
    """
    
    def __init__(self, app, csp_mode: str = "production"):
        super().__init__(app)
        self.csp_mode = csp_mode
        
        # 尝试导入CSP策略
        try:
            from csp_policy import CSPPolicy
            self.csp_policy = CSPPolicy(mode=csp_mode)
            self.csp_header = self.csp_policy.build_header()
        except ImportError:
            logger.warning("CSP策略不可用，使用默认CSP")
            self.csp_header = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'; upgrade-insecure-requests"
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # 添加安全头部
        self._add_security_headers(response)
        
        return response
    
    def _add_security_headers(self, response: Response):
        """添加所有安全相关的HTTP头部"""
        
        # 1. Content-Security-Policy (CSP)
        response.headers["Content-Security-Policy"] = self.csp_header
        
        # 2. X-Content-Type-Options (防止MIME类型混淆)
        response.headers["X-Content-Type-Options"] = "nosniff"
        
        # 3. X-Frame-Options (点击劫持防护)
        response.headers["X-Frame-Options"] = "DENY"
        
        # 4. X-XSS-Protection (浏览器XSS过滤器)
        response.headers["X-XSS-Protection"] = "1; mode=block"
        
        # 5. Referrer-Policy ( referrer 信息控制)
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # 6. Permissions-Policy (功能权限限制)
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), "
            "payment=(), usb=(), magnetometer=(), gyroscope=(), "
            "speaker=(), vibrate=(), fullscreen=(self)"
        )
        
        # 7. Strict-Transport-Security (HSTS) - 仅在HTTPS时启用
        # response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        
        # 8. Cross-Origin-Resource-Policy (CORP)
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        
        # 9. Cross-Origin-Opener-Policy (COOP)
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        
        # 10. Cross-Origin-Embedder-Policy (COEP)
        # response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"


def configure_secure_cookies(response: Response, session_id: str, max_age: int = 3600):
    """
    配置安全的Cookie设置
    
    Args:
        response: FastAPI响应对象
        session_id: 会话ID
        max_age: Cookie最大存活时间（秒）
    """
    import os
    from datetime import datetime, timedelta
    
    # 计算过期时间
    expires = datetime.utcnow() + timedelta(seconds=max_age)
    
    # 设置安全Cookie
    response.set_cookie(
        key="session_id",
        value=session_id,
        max_age=max_age,
        expires=expires.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        path="/",
        domain=None,  # 仅限当前域名
        secure=True,  # 仅通过HTTPS传输
        httponly=True,  # 禁止JavaScript访问
        samesite="Lax",  # CSRF防护
    )
    
    # 可选：添加额外的CSRF防护token
    csrf_token = os.urandom(32).hex()
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        max_age=max_age,
        path="/",
        secure=True,
        httponly=False,  # 前端需要读取以包含在请求头中
        samesite="Strict",
    )
    
    return csrf_token


def get_security_headers_dict(csp_mode: str = "production") -> dict:
    """
    获取安全头部字典（用于非中间件场景）
    
    Args:
        csp_mode: CSP策略模式
        
    Returns:
        安全头部字典
    """
    try:
        from csp_policy import CSPPolicy
        csp_policy = CSPPolicy(mode=csp_mode)
        csp_header = csp_policy.build_header()
    except ImportError:
        csp_header = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
    
    return {
        "Content-Security-Policy": csp_header,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": (
            "camera=(), microphone=(), geolocation=(), "
            "payment=(), usb=(), magnetometer=(), gyroscope=(), "
            "speaker=(), vibrate=(), fullscreen=(self)"
        ),
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Cross-Origin-Opener-Policy": "same-origin",
    }


# 便捷中间件工厂函数
def create_security_headers_middleware(csp_mode: str = "production"):
    """
    创建安全头部中间件
    
    Args:
        csp_mode: CSP策略模式
        
    Returns:
        中间件类
    """
    def _init_app(app):
        return SecurityHeadersMiddleware(app, csp_mode=csp_mode)
    return _init_app


if __name__ == "__main__":
    # 测试头部生成
    headers = get_security_headers_dict()
    print("=== Security Headers ===")
    for key, value in headers.items():
        print(f"{key}: {value}")
