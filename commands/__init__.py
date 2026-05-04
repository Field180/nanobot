"""
U23: Command Registry — central dispatch for slash commands.

Provides:
  - register(cmd)       — add a CommandDefinition
  - get_command(name)   — lookup by name or alias
  - list_commands(cat)  — list all (optionally filtered by category)
  - _auto_discover()    — import all modules in commands/ at init

Design:
  - Commands with a `handler` execute directly (no LLM round-trip).
  - Commands with a `skill` delegate to the skills framework.
  - Unknown commands fall through to the existing agentic loop.
"""
import importlib
import logging
import pkgutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

from commands.base import CommandDefinition

logger = logging.getLogger("nanobot.commands")

# ═══════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════

_registry: Dict[str, CommandDefinition] = {}
_alias_map: Dict[str, str] = {}  # alias -> canonical name


def register(cmd: CommandDefinition) -> None:
    """Register a command. Overwrites if name collides."""
    canonical = cmd.name.lower().lstrip("/")
    if canonical in _registry:
        logger.debug(f"[U23] Overwriting command: {canonical}")
    _registry[canonical] = cmd
    for alias in cmd.aliases:
        _alias_map[alias.lower().lstrip("/")] = canonical
    logger.debug(f"[U23] Registered command: /{canonical} "
                 f"(aliases={cmd.aliases}, cat={cmd.category})")


def get_command(name: str) -> Optional[CommandDefinition]:
    """Look up a command by name or alias. Returns None if unknown."""
    key = name.lower().lstrip("/")
    if key in _registry:
        return _registry[key]
    canonical = _alias_map.get(key)
    if canonical:
        return _registry.get(canonical)
    return None


def list_commands(category: Optional[str] = None) -> List[CommandDefinition]:
    """Return all registered commands, optionally filtered by category."""
    cmds = list(_registry.values())
    if category:
        cmds = [c for c in cmds if c.category == category]
    return sorted(cmds, key=lambda c: c.name)


# ═══════════════════════════════════════════════════════════════
# Auto-discovery: import all sibling modules in commands/
# ═══════════════════════════════════════════════════════════════

def _auto_discover() -> None:
    """Import (or reload) every .py module in the commands/ package (except __init__, base)."""
    pkg_dir = Path(__file__).resolve().parent
    skip = {"__init__", "base"}
    for info in pkgutil.iter_modules([str(pkg_dir)]):
        if info.name in skip:
            continue
        mod_name = f"commands.{info.name}"
        try:
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
            else:
                importlib.import_module(mod_name)
        except Exception as exc:
            logger.warning(f"[U23] Failed to import {mod_name}: {exc}")


_auto_discover()
