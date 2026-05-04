"""
U23: /help command — unified help listing.

Merges system commands (from commands registry) and AI skills
(from skills framework) into a single, auto-generated help output.
"""
import logging
from typing import Dict, List

from commands.base import CommandDefinition
from commands import register, list_commands

logger = logging.getLogger("nanobot.commands")


def handle_help(session_id: str = "", args: str = "", **kwargs) -> Dict:
    """Build a unified help response.

    Returns:
        {"handled": True, "message": str}  — message is injected
        as the user-visible help text (can be rendered by LLM or TUI).
    """
    lines: List[str] = []

    # ── 1. System commands from commands/ registry ──
    sys_cmds = list_commands(category="system")
    user_cmds = list_commands(category="user")
    ai_cmds = list_commands(category="ai")

    if sys_cmds or user_cmds:
        lines.append("## System & User Commands")
        for cmd in sys_cmds + user_cmds:
            if cmd.hidden:
                continue
            aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
            lines.append(f"- **/{cmd.name}**{aliases} — {cmd.description}")
        lines.append("")

    if ai_cmds:
        lines.append("## AI Commands")
        for cmd in ai_cmds:
            if cmd.hidden:
                continue
            aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
            lines.append(f"- **/{cmd.name}**{aliases} — {cmd.description}")
        lines.append("")

    # ── 2. Skills from skills framework ──
    try:
        from skills import list_skills
        skills = list_skills()
        if skills:
            lines.append("## AI Skills (routed to LLM)")
            for s in sorted(skills, key=lambda x: x.name):
                aliases = f" ({', '.join(s.aliases)})" if s.aliases else ""
                hint = f" {s.argument_hint}" if s.argument_hint else ""
                lines.append(f"- **/{s.name}{hint}**{aliases} — {s.description}")
            lines.append("")
    except Exception:
        pass

    if not lines:
        lines.append("No commands or skills registered.")

    lines.append("Use `/command [args]` to invoke.")
    return {"handled": True, "message": "\n".join(lines)}


# ── Register the /help command ──
_SYSTEM_COMMANDS_META = [
    # Existing system commands (metadata-only, handlers stay in server_final)
    CommandDefinition(
        name="help",
        description="Show all available commands and skills",
        aliases=["h", "?"],
        category="system",
        handler=handle_help,
    ),
    # /status — fully migrated to commands/status.py (Phase 2)
    CommandDefinition(
        name="health",
        description="Run a health check",
        aliases=["check_health"],
        category="system",
    ),
    CommandDefinition(
        name="logs",
        description="View recent log entries",
        aliases=["log", "view_logs", "show_logs"],
        category="system",
    ),
    CommandDefinition(
        name="config",
        description="View or validate configuration",
        aliases=["view_config", "show_config"],
        category="system",
    ),
    CommandDefinition(
        name="test",
        description="Run tests",
        aliases=["tests", "run_test", "run_tests"],
        category="system",
    ),
    CommandDefinition(
        name="backup",
        description="Create, list, restore, or verify backups",
        aliases=["backups", "create_backup", "make_backup"],
        category="system",
    ),
    CommandDefinition(
        name="tasks",
        description="List or execute scheduled tasks",
        aliases=["task", "list_tasks", "show_tasks"],
        category="system",
    ),
    CommandDefinition(
        name="skills",
        description="List all registered AI skills",
        aliases=[],
        category="system",
    ),
]

for _cmd in _SYSTEM_COMMANDS_META:
    register(_cmd)
