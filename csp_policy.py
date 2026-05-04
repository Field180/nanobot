"""
Content Security Policy (CSP) 配置
为 Nanobot Web UI 定义严格的内容安全策略
"""

from typing import Dict, List, Optional


class CSPPolicy:
    """
    CSP 策略管理器
    
    提供严格的默认策略，同时允许根据环境调整
    """
    
    # 默认安全策略（最严格）
    DEFAULT_DIRECTIVES: Dict[str, List[str]] = {
        # 默认：仅允许同源资源
        "default-src": ["'self'"],
        
        # 脚本：同源 + 内联 + CDN（highlight.js, marked, DOMPurify）
        "script-src": ["'self'", "'unsafe-inline'", "https://cdnjs.cloudflare.com"],
        
        # 样式：同源 + 内联 + CDN（highlight.js CSS）+ Google Fonts
        "style-src": ["'self'", "'unsafe-inline'", "https://cdnjs.cloudflare.com", "https://fonts.googleapis.com"],
        
        # 图片：同源 + data URI（Markdown图片需要）+ https
        "img-src": ["'self'", "data:", "https:"],
        
        # 字体：同源 + CDN（Font Awesome）+ Google Fonts
        "font-src": ["'self'", "https://cdnjs.cloudflare.com", "https://fonts.gstatic.com"],
        
        # 连接：同源 + 后端API
        "connect-src": ["'self'", "https://localhost:*", "wss://localhost:*"],
        
        # 媒体：同源
        "media-src": ["'self'"],
        
        # 对象：禁止（防止Flash/插件）
        "object-src": ["'none'"],
        
        # 框架：禁止点击劫持
        "frame-ancestors": ["'none'"],
        
        # 表单提交：同源
        "form-action": ["'self'"],
        
        # 基础URI：同源
        "base-uri": ["'self'"],
        
        # 升级不安全请求（自动将http转为https）
        "upgrade-insecure-requests": [],
    }
    
    # 开发环境宽松策略（仅开发使用）
    DEVELOPMENT_DIRECTIVES: Dict[str, List[str]] = {
        "default-src": ["'self'"],
        "script-src": ["'self'", "'unsafe-inline'", "'unsafe-eval'"],
        "style-src": ["'self'", "'unsafe-inline'"],
        "img-src": ["'self'", "data:", "https:", "http:"],
        "font-src": ["'self'", "data:"],
        "connect-src": ["'self'", "*"],
        "media-src": ["'self'"],
        "object-src": ["'none'"],
    }
    
    def __init__(self, mode: str = "production"):
        """
        初始化 CSP 策略
        
        Args:
            mode: "production" | "development" | "strict"
        """
        self.mode = mode
        self.directives = self._get_directives(mode)
    
    def _get_directives(self, mode: str) -> Dict[str, List[str]]:
        """根据模式获取策略指令"""
        if mode == "strict":
            # 最严格模式：移除所有unsafe-inline
            directives = self.DEFAULT_DIRECTIVES.copy()
            directives["script-src"] = ["'self'"]
            directives["style-src"] = ["'self'"]
            return directives
        elif mode == "development":
            return self.DEVELOPMENT_DIRECTIVES.copy()
        else:  # production
            return self.DEFAULT_DIRECTIVES.copy()
    
    def build_header(self) -> str:
        """
        构建 CSP HTTP 头值
        
        Returns:
            Content-Security-Policy 头值字符串
        """
        parts = []
        for directive, values in self.directives.items():
            if values:
                parts.append(f"{directive} {' '.join(values)}")
            else:
                parts.append(directive)
        
        return "; ".join(parts)
    
    def add_source(self, directive: str, source: str):
        """
        添加允许的资源源
        
        Args:
            directive: CSP 指令名称（如 "connect-src"）
            source: 资源源（如 "https://api.example.com"）
        """
        if directive in self.directives:
            if source not in self.directives[directive]:
                self.directives[directive].append(source)
    
    def remove_source(self, directive: str, source: str):
        """
        移除允许的资源源
        """
        if directive in self.directives:
            if source in self.directives[directive]:
                self.directives[directive].remove(source)
    
    def generate_meta_tag(self) -> str:
        """
        生成 HTML meta 标签（备用方案）
        
        Returns:
            HTML meta 标签字符串
        """
        content = self.build_header()
        return f'<meta http-equiv="Content-Security-Policy" content="{content}">'
    
    def get_nonce(self, length: int = 16) -> str:
        """
        生成 CSP nonce（用于内联脚本/样式）
        
        Args:
            length: nonce 长度
            
        Returns:
            随机 nonce 字符串
        """
        import secrets
        return secrets.token_urlsafe(length)
    
    @staticmethod
    def get_recommended_report_uri() -> str:
        """获取推荐的 CSP 报告端点"""
        return "/api/security/csp-report"


# 预定义策略实例
CSP_STRICT = CSPPolicy(mode="strict")
CSP_PRODUCTION = CSPPolicy(mode="production")
CSP_DEVELOPMENT = CSPPolicy(mode="development")


def get_csp_header(mode: str = "production") -> str:
    """
    便捷函数：获取 CSP 头值
    
    Args:
        mode: 策略模式
        
    Returns:
        CSP 头值
    """
    policy = CSPPolicy(mode=mode)
    return policy.build_header()


if __name__ == "__main__":
    # 测试 CSP 策略生成
    print("=== Production CSP ===")
    print(CSP_PRODUCTION.build_header())
    print()
    
    print("=== Strict CSP ===")
    print(CSP_STRICT.build_header())
    print()
    
    print("=== Development CSP ===")
    print(CSP_DEVELOPMENT.build_header())
    print()
    
    print("=== HTML Meta Tag ===")
    print(CSP_PRODUCTION.generate_meta_tag())
