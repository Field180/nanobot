"""CI-only stub for unified_tool_parser.

The real implementation lives at /home/field/.nanobot/workspace/tools/unified_tool_parser.py
on developer machines but is not part of the web_ui repo. CI containers don't
have it, so we provide a minimal stub that satisfies the imports done in
server_final.py without exercising the real parser.

Only the symbols imported by server_final.py are defined:
- ToolCallRouter (with .parse() and config_path kwarg in __init__)
- ParseResult (dataclass-like with .tool_calls)
- ToolCall (dataclass-like)

Tests that actually parse model output will fail on this stub — that's
expected; integration tests that need the real parser should run locally.
"""

from dataclasses import dataclass, field
from typing import List, Any, Optional


@dataclass
class ToolCall:
    """Minimal stub for the real ToolCall dataclass."""
    name: str = ""
    arguments: Any = None
    id: str = ""


@dataclass
class ParseResult:
    """Minimal stub for the real ParseResult dataclass."""
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw_text: str = ""
    model_name: str = ""


class ToolCallRouter:
    """Minimal stub. Real router picks the right parser per model; the stub
    just returns an empty ParseResult for any input."""

    def __init__(self, config_path: Optional[str] = None, **_kwargs):
        self.config_path = config_path

    def parse(self, text: str, model_name: str = "") -> ParseResult:
        return ParseResult(tool_calls=[], raw_text=text, model_name=model_name)