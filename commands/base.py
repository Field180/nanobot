"""
U23: Command System — Base definitions.

CommandDefinition is the metadata type for all slash commands,
whether they execute directly (handler) or delegate to a skill.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class CommandDefinition:
    """Metadata for a slash command."""
    name: str                                    # primary name (without /)
    description: str                             # one-line help text
    aliases: List[str] = field(default_factory=list)
    category: str = "user"                       # user | system | ai
    handler: Optional[Callable[..., Any]] = None # direct handler (non-LLM)
    skill: Optional[str] = None                  # associated skill name (LLM-routed)
    hidden: bool = False                         # if True, omit from /help
    required_permission: Optional[str] = None    # future: permission gate (e.g. "admin")
