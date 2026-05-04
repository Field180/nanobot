"""web_fetch tool — Fetch and extract text content from a URL.

Converts HTML to readable text, stripping navigation, scripts, and styling.
Useful for reading documentation, articles, API docs, and web pages.
"""
import logging
import re
import sys
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger("nanobot.tools.web_fetch")

# Ensure nanobot venv packages are importable
_venv_site = Path.home() / ".nanobot" / "venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
if _venv_site.exists() and str(_venv_site) not in sys.path:
    sys.path.insert(0, str(_venv_site))

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Fetch a web page and extract its text content. "
            "Returns the page as clean readable text (HTML tags removed). "
            "Use this to read documentation, articles, API references, or any URL. "
            "For search queries, use web_search instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch (must start with http:// or https://)"
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum characters to return (default 15000). Use a smaller value for quick lookups."
                },
            },
            "required": ["url"]
        }
    }
}

ALIASES = ["fetch_url", "read_url", "curl"]
IS_READONLY = True

GUIDANCE = {
    "replaces_shell": ["curl", "wget"],
    "shell_never": "fetching web pages or URLs",
    "tips": [
        "Use web_fetch to read documentation pages, GitHub READMEs, API docs, or articles.",
        "Use web_search first if you need to find the right URL.",
        "Returns plain text — HTML is stripped automatically.",
    ],
}

# Tags whose content should be removed entirely (not just the tag)
_REMOVE_TAGS = {
    "script", "style", "nav", "header", "footer", "aside",
    "noscript", "svg", "iframe", "form",
}

# Tags that should add a newline when closed
_BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "h1", "h2", "h3",
    "h4", "h5", "h6", "li", "tr", "br", "hr", "blockquote", "pre",
    "table", "thead", "tbody", "ul", "ol", "dl", "dt", "dd",
}


def _html_to_text(html: str) -> str:
    """Convert HTML to clean readable text using BeautifulSoup."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Remove unwanted tags entirely
        for tag_name in _REMOVE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # Extract title
        title = ""
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

        # Get main content area if available (prefer article/main over full body)
        main_content = soup.find("main") or soup.find("article") or soup.find("body") or soup
        text = main_content.get_text(separator="\n", strip=False)

        # Clean up whitespace
        lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
            elif lines and lines[-1] != "":
                lines.append("")  # keep single blank lines between sections

        text = "\n".join(lines)
        # Collapse multiple blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)

        if title:
            text = f"# {title}\n\n{text}"

        return text.strip()

    except ImportError:
        # Fallback: basic regex-based HTML stripping
        return _html_to_text_fallback(html)


def _html_to_text_fallback(html: str) -> str:
    """Fallback HTML→text when BeautifulSoup is unavailable."""
    # Remove script/style content
    text = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", "", html, flags=re.IGNORECASE)
    # Convert br/p/div to newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|h[1-6]|li|tr)>", "\n", text, flags=re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    # Clean whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def execute(args: dict, workspace: Path) -> dict:
    """Fetch URL and return extracted text content."""
    url = args.get("url", "").strip()
    max_length = int(args.get("max_length", 15000))

    if not url:
        return {"success": False, "output": "", "error": "No URL provided"}

    if not url.startswith(("http://", "https://")):
        return {"success": False, "output": "", "error": f"Invalid URL scheme — must start with http:// or https://. Got: {url[:50]}"}

    # P43: Check network availability before attempting fetch
    from tools.base import check_network_available, network_offline_error
    if not check_network_available():
        return network_offline_error("web_fetch", url)

    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Nanobot/1.0; +https://github.com/nanobot)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        }

        response = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        content_length = len(response.content)

        # Plain text (markdown, txt, json, xml, etc.)
        if "text/plain" in content_type or "application/json" in content_type:
            text = response.text[:max_length]
            meta = f"[URL: {url} | {content_length:,} bytes | {content_type.split(';')[0]}]"
            if len(response.text) > max_length:
                text += f"\n\n... [{len(response.text) - max_length:,} chars truncated]"
            return {"success": True, "output": f"{meta}\n\n{text}", "error": ""}

        # HTML → text extraction
        if "text/html" in content_type or "application/xhtml" in content_type or not content_type:
            raw_html = response.text
            text = _html_to_text(raw_html)

            meta = f"[URL: {url} | {content_length:,} bytes | text extracted from HTML]"
            if len(text) > max_length:
                text = text[:max_length] + f"\n\n... [{len(text) - max_length:,} chars truncated]"
            if not text or len(text) < 20:
                return {"success": True, "output": f"{meta}\n\n(Page returned minimal text content. It may be JavaScript-rendered.)", "error": ""}
            return {"success": True, "output": f"{meta}\n\n{text}", "error": ""}

        # Binary content — just report metadata
        return {
            "success": True,
            "output": f"[URL: {url} | {content_length:,} bytes | {content_type}]\n\n(Binary content — cannot extract text. Download the file with shell_execute if needed.)",
            "error": ""
        }

    except requests.exceptions.Timeout:
        return {"success": False, "output": "", "error": f"Request timed out after 20s: {url}"}
    except requests.exceptions.ConnectionError as e:
        # P43: Cache offline state when connection fails
        from tools.base import network_offline_error, _net_cache
        import time as _t43
        _net_cache["available"] = False
        _net_cache["checked_at"] = _t43.monotonic()
        return network_offline_error("web_fetch", url)
    except requests.exceptions.HTTPError as e:
        return {"success": False, "output": "", "error": f"HTTP {e.response.status_code}: {url}"}
    except Exception as e:
        return {"success": False, "output": "", "error": f"Fetch failed: {str(e)[:300]}"}
