"""
高级HTML净化模块 - 支持bleach/lxml和正则双模式
优先使用bleach进行结构化清理，不可用时回退到正则
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 尝试导入bleach，不可用则标记
try:
    import bleach
    from bleach.sanitizer import Cleaner
    BLEACH_AVAILABLE = True
except ImportError:
    BLEACH_AVAILABLE = False
    logger.warning("bleach未安装，使用正则回退模式。建议: pip install bleach lxml")

# 危险标签列表（用于bleach和正则）
DANGEROUS_TAGS = {
    'script', 'object', 'embed', 'iframe', 'frame',
    'form', 'input', 'button', 'textarea', 'select',
    'applet', 'link', 'meta', 'base', 'style'
}

# 危险属性列表
DANGEROUS_ATTRIBUTES = {
    'onclick', 'onload', 'onerror', 'onmouseover', 'onmouseout',
    'onkeydown', 'onkeypress', 'onkeyup', 'onfocus', 'onblur',
    'onchange', 'onsubmit', 'onreset', 'onselect', 'onabort',
    'ondblclick', 'onmousemove', 'onmouseup', 'onmousedown',
    'srcdoc', 'action'
}

# CSS危险模式
DANGEROUS_CSS_PATTERNS = [
    (r'expression\s*\([^)]*\)', '[removed]'),  # CSS表达式
    (r'url\s*\(\s*["\']?javascript:[^\)]+\)', 'url(blocked:)'),  # url(js)
    (r'javascript:\s*[^\s"\']*', 'blocked:'),  # javascript协议
    (r'vbscript:\s*[^\s"\']*', 'blocked:'),  # vbscript协议
    (r'data:(?:text/html|image/svg)[^\s"\']*', 'blocked:'),  # data URI
]

# HTML实体转义映射
HTML_ESCAPES = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#x27;',
}


class AdvancedHTMLSanitizer:
    """
    高级HTML净化器
    
    优先使用bleach进行结构化清理，不可用时回退到正则模式
    """
    
    def __init__(self, use_bleach: bool = True):
        """
        初始化净化器
        
        Args:
            use_bleach: 是否尝试使用bleach（如果可用）
        """
        self.use_bleach = use_bleach and BLEACH_AVAILABLE
        self.bleach_cleaner = None
        
        if self.use_bleach:
            try:
                # 配置bleach：允许基本格式标签，禁止危险标签
                self.bleach_cleaner = Cleaner(
                    tags=['p', 'br', 'strong', 'em', 'u', 'h1', 'h2', 'h3',
                          'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'a', 'img',
                          'code', 'pre', 'blockquote', 'table', 'thead',
                          'tbody', 'tr', 'th', 'td'],
                    attributes={
                        'a': ['href', 'title'],
                        'img': ['src', 'alt', 'title'],
                        'code': ['class'],
                        'pre': ['class'],
                        'th': ['colspan', 'rowspan'],
                        'td': ['colspan', 'rowspan'],
                        '*': ['class', 'id']  # 全局属性
                    },
                    protocols=['http', 'https', 'mailto'],  # 仅允许安全协议
                    strip=True,  # 移除不允许的标签而非转义
                    filters=[]  # 可添加自定义过滤器
                )
                logger.info("bleach净化器已启用")
            except Exception as e:
                logger.error(f"bleach初始化失败: {e}")
                self.use_bleach = False
    
    def sanitize(self, content: str) -> str:
        """
        净化HTML内容
        
        Args:
            content: 原始内容
            
        Returns:
            净化后的安全内容
        """
        if not content:
            return content
        
        # 记录原始内容长度（用于审计）
        original_length = len(content)
        
        # 第一阶段：使用bleach或正则清理HTML结构
        if self.use_bleach and self.bleach_cleaner:
            try:
                sanitized = self._sanitize_with_bleach(content)
            except Exception as e:
                logger.error(f"bleach净化失败，回退到正则: {e}")
                sanitized = self._sanitize_with_regex(content)
        else:
            sanitized = self._sanitize_with_regex(content)
        
        # 第二阶段：清理CSS危险模式
        sanitized = self._sanitize_css_patterns(sanitized)
        
        # 第三阶段：HTML实体转义（最终防线）
        # 注意：这里仅转义未在标签中的危险字符
        sanitized = self._escape_html_entities(sanitized)
        
        # 记录净化结果
        if len(sanitized) != original_length:
            logger.debug(f"内容已净化: {original_length} -> {len(sanitized)} 字符")
        
        return sanitized
    
    def _sanitize_with_bleach(self, content: str) -> str:
        """使用bleach进行结构化清理"""
        # bleach会移除危险标签、过滤危险属性、限制协议
        return self.bleach_cleaner.clean(content)
    
    def _sanitize_with_regex(self, content: str) -> str:
        """使用正则进行清理（bleach不可用时的回退）"""
        sanitized = content
        
        # 移除危险标签（包括内容）
        for tag in DANGEROUS_TAGS:
            # 移除 <tag>...</tag>
            sanitized = re.sub(
                rf'<{tag}[^>]*>.*?</{tag}>',
                '',
                sanitized,
                flags=re.IGNORECASE | re.DOTALL
            )
            # 自闭合标签 <tag />
            sanitized = re.sub(
                rf'<{tag}[^>]*/?>',
                '',
                sanitized,
                flags=re.IGNORECASE
            )
        
        # 移除事件处理器属性
        for attr in DANGEROUS_ATTRIBUTES:
            sanitized = re.sub(
                rf'\s+{attr}\s*=\s*["\'][^"\']*["\']',
                '',
                sanitized,
                flags=re.IGNORECASE
            )
        
        return sanitized
    
    def _sanitize_css_patterns(self, content: str) -> str:
        """清理CSS中的危险模式"""
        sanitized = content
        
        for pattern, replacement in DANGEROUS_CSS_PATTERNS:
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
        
        return sanitized
    
    def _escape_html_entities(self, content: str) -> str:
        """
        转义HTML实体
        
        策略：仅转义不在合法标签内的 < > & 符号
        这是一个简化实现，完整实现需要解析HTML结构
        """
        # 简单转义（适用于已经过滤标签后的内容）
        # 按顺序转义避免双重转义
        sanitized = content
        for char, entity in HTML_ESCAPES.items():
            sanitized = sanitized.replace(char, entity)
        
        return sanitized
    
    def get_stats(self) -> dict:
        """获取净化器状态信息"""
        return {
            'bleach_available': BLEACH_AVAILABLE,
            'bleach_enabled': self.use_bleach,
            'dangerous_tags_count': len(DANGEROUS_TAGS),
            'dangerous_attrs_count': len(DANGEROUS_ATTRIBUTES),
            'css_patterns_count': len(DANGEROUS_CSS_PATTERNS)
        }


# 全局净化器实例（单例模式）
_sanitizer_instance: Optional[AdvancedHTMLSanitizer] = None


def get_sanitizer(use_bleach: bool = True) -> AdvancedHTMLSanitizer:
    """获取全局净化器实例"""
    global _sanitizer_instance
    if _sanitizer_instance is None:
        _sanitizer_instance = AdvancedHTMLSanitizer(use_bleach=use_bleach)
    return _sanitizer_instance


def sanitize_html_advanced(content: str, use_bleach: bool = True) -> str:
    """
    高级HTML净化函数（便捷接口）
    
    Args:
        content: 原始HTML内容
        use_bleach: 是否使用bleach（如果可用）
        
    Returns:
        净化后的安全内容
    """
    sanitizer = get_sanitizer(use_bleach)
    return sanitizer.sanitize(content)


# 向后兼容的简化接口
def sanitize_html_content(content: str) -> str:
    """
    向后兼容的净化函数
    替换 server_final.py 中的同名函数
    """
    return sanitize_html_advanced(content, use_bleach=True)


if __name__ == "__main__":
    # 测试净化器
    test_cases = [
        '<script>alert(1)</script>',
        '<div onclick="alert(1)">click me</div>',
        '<div style="expression(alert(1))">test</div>',
        '<div style="background:url(\'javascript:alert(1)\')">test</div>',
        '<object data="evil.swf"></object>',
        '<img src="javascript:alert(1)">',
        'Normal text with <b>bold</b> and <a href="http://example.com">link</a>',
    ]
    
    sanitizer = get_sanitizer()
    stats = sanitizer.get_stats()
    print(f"净化器状态: {stats}")
    print("="*60)
    
    for test in test_cases:
        result = sanitizer.sanitize(test)
        print(f"输入:  {test[:50]}")
        print(f"输出:  {result[:50]}")
        print("-"*60)
