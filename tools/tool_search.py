"""tool_search — Search available tools by name or description.

Lets the model discover tools dynamically when it needs a capability
but isn't sure which tool provides it.  Returns matching tool names,
descriptions, and parameter schemas ranked by relevance.

Design mirrors Claw's ToolSearchTool pattern.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("nanobot.tools.tool_search")

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "tool_search",
        "description": (
            "Search available tools by name or description keyword. "
            "Use this when you need a capability but are unsure which tool provides it. "
            "Returns matching tool names, descriptions, and parameter schemas."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword(s) to fuzzy-match against tool names and descriptions."
                },
            },
            "required": ["query"]
        }
    }
}

ALIASES = ["search_tools", "find_tool"]
IS_READONLY = True

GUIDANCE = {
    "replaces_shell": [],
    "shell_never": "",
    "tips": [
        "Use tool_search when you're unsure which tool handles a task.",
        "Search by capability (e.g. 'edit file') rather than exact tool names.",
    ],
}

# Maximum number of results to return
_MAX_RESULTS = 10


def _get_all_tool_defs() -> List[Dict[str, Any]]:
    """Lazy-import tool registry to avoid circular imports."""
    from tools import _ALL_TOOL_MODULES
    return [m.TOOL_DEF for m in _ALL_TOOL_MODULES]


def _score_tool(tool_def: Dict[str, Any], tokens: List[str]) -> int:
    """Score a tool against query tokens. Higher = more relevant.

    Scoring:
      - Name exact match with a token: +10
      - Name contains a token:          +5
      - Description contains a token:   +2
    """
    func = tool_def.get("function", {})
    name = func.get("name", "").lower()
    desc = func.get("description", "").lower()
    score = 0
    for tok in tokens:
        if tok == name:
            score += 10
        elif tok in name:
            score += 5
        if tok in desc:
            score += 2
    return score


def _summarize_params(params: Dict[str, Any]) -> List[str]:
    """Return a compact list of parameter names with types."""
    props = params.get("properties", {})
    required = set(params.get("required", []))
    result = []
    for pname, pschema in props.items():
        ptype = pschema.get("type", "any")
        marker = " (required)" if pname in required else ""
        result.append(f"{pname}: {ptype}{marker}")
    return result


def execute(args: dict, workspace: Path) -> dict:
    """Execute tool_search — find tools matching the query."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"success": False, "output": "", "error": "No query provided. Specify a keyword to search tools."}

    tokens = [t.lower() for t in query.split() if t]
    all_tools = _get_all_tool_defs()

    scored: List[tuple] = []
    for tdef in all_tools:
        s = _score_tool(tdef, tokens)
        if s > 0:
            scored.append((s, tdef))

    # Sort by score descending, then by name for stability
    scored.sort(key=lambda x: (-x[0], x[1]["function"]["name"]))

    results = []
    for _score, tdef in scored[:_MAX_RESULTS]:
        func = tdef["function"]
        results.append({
            "name": func["name"],
            "description": func.get("description", ""),
            "parameters": _summarize_params(func.get("parameters", {})),
        })

    if not results:
        output = f"No tools found matching '{query}'. Try broader keywords."
    else:
        parts = [f"Found {len(results)} tool(s) matching '{query}':\n"]
        for r in results:
            parts.append(f"**{r['name']}** — {r['description']}")
            if r["parameters"]:
                parts.append(f"  Parameters: {', '.join(r['parameters'])}")
            parts.append("")
        output = "\n".join(parts)

    logger.info(f"[ToolSearch] query='{query}' matches={len(results)}")

    return {"success": True, "output": output, "error": ""}
