"""web_search tool — Search the web using DuckDuckGo.

No API key required. Returns structured search results with titles,
URLs, and snippets. Use web_fetch to read full page content.
"""
import logging
import sys
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger("nanobot.tools.web_search")

# Ensure nanobot venv packages are importable
_venv_site = Path.home() / ".nanobot" / "venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
if _venv_site.exists() and str(_venv_site) not in sys.path:
    sys.path.insert(0, str(_venv_site))

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web using DuckDuckGo. Returns titles, URLs, and snippets. "
            "Use this to find documentation, solutions, package info, or any web content. "
            "Then use web_fetch to read the full content of a specific result."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 8, max 20)"
                },
            },
            "required": ["query"]
        }
    }
}

ALIASES = ["search", "google", "ddg"]
IS_READONLY = True

GUIDANCE = {
    "replaces_shell": [],
    "shell_never": "web searching",
    "tips": [
        "Use web_search to find documentation, error solutions, package versions, etc.",
        "After searching, use web_fetch to read the full content of a promising result.",
        "Be specific in queries — include language/framework name and error messages.",
    ],
}


def _search_ddgs(query: str, max_results: int) -> List[Dict[str, str]]:
    """Search using duckduckgo-search library."""
    from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    return results


def _search_fallback(query: str, max_results: int) -> List[Dict[str, str]]:
    """Fallback: scrape DuckDuckGo HTML directly (no extra deps)."""
    import urllib.request
    import urllib.parse
    import re

    encoded_query = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Nanobot/1.0)"
    }
    req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    results = []
    # Extract result blocks: <a class="result__a" href="...">title</a> + <a class="result__snippet">...</a>
    blocks = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )

    for href, title, snippet in blocks[:max_results]:
        # Clean HTML tags from title and snippet
        clean_title = re.sub(r"<[^>]+>", "", title).strip()
        clean_snippet = re.sub(r"<[^>]+>", "", snippet).strip()
        # DuckDuckGo redirects through uddg= param
        import urllib.parse as _up
        parsed = _up.parse_qs(_up.urlparse(href).query)
        actual_url = parsed.get("uddg", [href])[0]
        results.append({
            "title": clean_title,
            "href": actual_url,
            "body": clean_snippet,
        })

    return results


def execute(args: dict, workspace: Path) -> dict:
    """Execute a web search and return formatted results."""
    query = args.get("query", "").strip()
    max_results = min(int(args.get("max_results", 8)), 20)

    if not query:
        return {"success": False, "output": "", "error": "No search query provided"}

    # P43: Check network availability before attempting search
    from tools.base import check_network_available, network_offline_error
    if not check_network_available():
        return network_offline_error("web_search", query)

    try:
        # Try duckduckgo-search library first
        try:
            results = _search_ddgs(query, max_results)
        except ImportError:
            logger.info("[web_search] duckduckgo-search not available, using HTML fallback")
            results = _search_fallback(query, max_results)

        if not results:
            return {"success": True, "output": f"No results found for: {query}", "error": ""}

        # Format results
        lines = [f"Web search: \"{query}\" — {len(results)} results\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "No title")
            url = r.get("href", r.get("link", ""))
            snippet = r.get("body", r.get("snippet", ""))
            lines.append(f"{i}. **{title}**")
            lines.append(f"   {url}")
            if snippet:
                # Truncate long snippets
                if len(snippet) > 300:
                    snippet = snippet[:300] + "..."
                lines.append(f"   {snippet}")
            lines.append("")

        lines.append("Use web_fetch to read the full content of any result.")
        output = "\n".join(lines)
        return {"success": True, "output": output, "error": ""}

    except Exception as e:
        error_msg = str(e)[:400]
        logger.error(f"[web_search] Search failed: {error_msg}")
        # P43: Detect network errors and return graceful offline fallback
        _NET_ERROR_HINTS = ("connect", "timeout", "unreachable", "network", "refused", "resolve", "getaddrinfo")
        if any(hint in error_msg.lower() for hint in _NET_ERROR_HINTS):
            from tools.base import network_offline_error, _net_cache
            import time as _t43
            _net_cache["available"] = False
            _net_cache["checked_at"] = _t43.monotonic()
            return network_offline_error("web_search", query)
        return {"success": False, "output": "", "error": f"Search failed: {error_msg}"}
