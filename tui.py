#!/usr/bin/env python3
"""
P6: Nanobot Terminal UI (TUI) — Rich + prompt_toolkit frontend.

A standalone terminal interface that calls agentic_chat_stream() directly,
bypassing the FastAPI HTTP layer. Designed for SSH, headless, and
lightweight environments.

Usage:
    python3 web_ui/tui.py [--workspace DIR] [--model MODEL] [--mode code|ask|plan]

Dependencies (already in project venv):
    rich>=13.0, prompt_toolkit>=3.0
"""
import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Ensure web_ui is on sys.path so local imports work ──
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

# ── Rich imports ──
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme
except ImportError:
    print("ERROR: 'rich' is required.  pip install rich>=13.0")
    sys.exit(1)

# ── prompt_toolkit imports ──
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
except ImportError:
    print("ERROR: 'prompt_toolkit' is required.  pip install prompt_toolkit>=3.0")
    sys.exit(1)

# ── Logging ──
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("nanobot.tui")

# ── Rich theme ──
_THEME = Theme({
    "tool.name": "bold yellow",
    "tool.ok": "green",
    "tool.fail": "bold red",
    "turn": "bold cyan",
    "stat": "dim",
    "mode.label": "bold magenta",
    "greeting": "bold blue",
    "error": "bold red",
})

console = Console(theme=_THEME)

# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

DEFAULT_WORKSPACE = Path("/home/field/.nanobot/workspace")
HISTORY_FILE = Path.home() / ".nanobot_tui_history"
VENV_SITE = Path("/home/field/nanobotProjects/nanobot/.venv/lib/python3.12/site-packages")

# ═══════════════════════════════════════════════════════════════
# Environment builder — mirrors server_final.py env construction
# ═══════════════════════════════════════════════════════════════

def build_env(
    workspace: Path,
    model: str = "",
    mode: str = "code",
    max_turns: int = 10,
    backend: str = "ollama",
) -> Dict[str, str]:
    """Build the env dict that agentic_chat_stream expects."""
    env: Dict[str, str] = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "NANOBOT_WORKSPACE": str(workspace),
        "NANOBOT_MAX_TOOL_ITERATIONS": str(max_turns),
        "NANOBOT_ENABLE_ALL_TOOLS": "true",
        "NANOBOT_AGENTIC_LOOP": "true",
        "NANOBOT_BACKEND": backend,
    }

    if model:
        if backend == "ollama" and "/" not in model:
            env["NANOBOT_AGENTS__DEFAULTS__MODEL"] = f"ollama_chat/{model}"
        else:
            env["NANOBOT_AGENTS__DEFAULTS__MODEL"] = model
        env["NANOBOT_TELEMETRY_MODEL_NAME"] = model

    # Model-specific defaults (matching server_final.py model_config)
    model_config = {
        "qwen3.5:35b":  {"max_tokens": 16384, "timeout": 600, "ctx": 262144},
        "qwen3.5:122b": {"max_tokens": 16384, "timeout": 900, "ctx": 131072},
    }
    base_model = model.split("/")[-1] if "/" in model else model
    if base_model in model_config:
        cfg = model_config[base_model]
        env["NANOBOT_AGENTS__DEFAULTS__MAX_TOKENS"] = str(cfg["max_tokens"])
        env["REQUEST_TIMEOUT"] = str(cfg["timeout"])
        env["OLLAMA_NUM_CTX"] = str(cfg["ctx"])
        env["NANOBOT_TELEMETRY_CONTEXT_LENGTH"] = str(cfg["ctx"])
        env["NANOBOT_TELEMETRY_OUTPUT_LIMIT"] = str(cfg["max_tokens"])

    return env


# ═══════════════════════════════════════════════════════════════
# Slash-command detection (reuses skills registry)
# ═══════════════════════════════════════════════════════════════

def detect_slash_command(text: str) -> Optional[str]:
    """If text starts with /cmd, return cmd name if it's a known skill."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split(None, 1)
    cmd = parts[0][1:]  # remove leading /
    try:
        from skills import get_skill
        skill = get_skill(cmd)
        return skill.name if skill else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# Event renderer — converts agentic_chat_stream events to Rich
# ═══════════════════════════════════════════════════════════════

class EventRenderer:
    """Stateful renderer that accumulates streaming chunks and renders events."""

    def __init__(self):
        self._chunks: List[str] = []
        self._tool_count = 0
        self._turn = 0
        self._tools_used: List[str] = []

    def reset(self):
        self._chunks.clear()
        self._tool_count = 0
        self._turn = 0
        self._tools_used.clear()

    def render(self, event: Dict[str, Any]):
        etype = event.get("type", "")

        if etype == "chunk":
            token = event.get("content", "")
            if token:
                self._chunks.append(token)
                # Print raw token for streaming effect
                console.print(token, end="", highlight=False)

        elif etype == "turn_start":
            self._turn = event.get("turn", 0)
            if self._turn > 1:
                console.print()
                console.print(f"── Turn {self._turn} ──", style="turn")

        elif etype == "tool_start":
            self._tool_count += 1
            name = event.get("name", "?")
            self._tools_used.append(name)
            args = event.get("arguments", {})
            args_brief = self._brief_args(args)
            console.print(f"\n🔧 {name}", style="tool.name", end="")
            if args_brief:
                console.print(f"  {args_brief}", style="dim", end="")
            console.print()

        elif etype == "tool_result":
            name = event.get("name", "?")
            success = event.get("success", True)
            elapsed = event.get("elapsed_ms", 0)
            output = event.get("output", "") or event.get("result", "")
            status = "✓" if success else "✗"
            style = "tool.ok" if success else "tool.fail"
            console.print(f"   {status} {name}", style=style, end="")
            if elapsed:
                console.print(f"  ({elapsed}ms)", style="stat", end="")
            console.print()

            # Show brief output for failures or short results
            if output and (not success or len(str(output)) < 500):
                out_str = str(output)[:800]
                if out_str.strip():
                    console.print(Panel(
                        Text(out_str, overflow="fold"),
                        title=name,
                        border_style="dim",
                        expand=False,
                        width=min(console.width, 100),
                    ))

        elif etype == "mode":
            label = event.get("label", event.get("mode", "code"))
            msg = event.get("message", "")
            console.print(f"  📋 {label}", style="mode.label", end="")
            if msg:
                console.print(f"  {msg}", style="dim", end="")
            console.print()

        elif etype == "final_answer":
            content = event.get("content", "")
            if content and content.strip():
                self._flush_chunks()
                console.print()
                try:
                    console.print(Markdown(content))
                except Exception:
                    console.print(content)

        elif etype == "agentic_done":
            self._flush_chunks()
            turns = event.get("turns", 1)
            tc = event.get("total_tool_calls", 0)
            tools = event.get("tools_used", [])
            console.print()
            console.print(
                f"─ Done: {turns} turn(s), {tc} tool call(s)"
                + (f"  [{', '.join(tools)}]" if tools else ""),
                style="stat",
            )

        elif etype == "agentic_stop":
            self._flush_chunks()
            console.print("\n⏹  Stopped", style="stat")

        elif etype == "max_turns_reached":
            console.print(
                f"\n⚠  Max turns reached ({event.get('turns', 0)})",
                style="error",
            )

        elif etype == "context_compact":
            old = event.get("old_tokens", 0)
            new = event.get("new_tokens", 0)
            console.print(
                f"  🗜  Context compacted: {old}→{new} tokens",
                style="stat",
            )

        elif etype == "todo_update":
            todos = event.get("todos", [])
            if todos:
                lines = []
                for t in todos:
                    icon = {"completed": "✓", "in_progress": "▶", "pending": "○"}.get(
                        t.get("status", ""), "·"
                    )
                    lines.append(f"  {icon} {t.get('content', '')}")
                console.print(Panel(
                    "\n".join(lines),
                    title="📋 Tasks",
                    border_style="dim",
                    expand=False,
                ))

        elif etype == "skill_mode":
            mode_name = event.get("mode", "")
            console.print(f"  ⚡ Skill mode: /{mode_name}", style="mode.label")

        elif etype == "error":
            msg = event.get("message", event.get("error", "Unknown error"))
            console.print(f"❌ {msg}", style="error")

        # Silently ignore: heartbeat, sub_agent_start, sub_agent_end, task_update, etc.

    def _flush_chunks(self):
        """If we have accumulated raw chunks, ensure a newline."""
        if self._chunks:
            text = "".join(self._chunks)
            if text and not text.endswith("\n"):
                console.print()
            self._chunks.clear()

    def get_full_response(self) -> str:
        return "".join(self._chunks)

    @staticmethod
    def _brief_args(args: dict, max_len: int = 80) -> str:
        if not args:
            return ""
        parts = []
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 40:
                sv = sv[:37] + "..."
            parts.append(f"{k}={sv}")
        result = ", ".join(parts)
        return result[:max_len]


# ═══════════════════════════════════════════════════════════════
# Token stats renderer
# ═══════════════════════════════════════════════════════════════

def render_stats(stream_stats: Dict[str, Any], elapsed: float):
    """Print token usage after a completed exchange."""
    pt = stream_stats.get("prompt_tokens", 0)
    ct = stream_stats.get("completion_tokens", 0)
    tt = stream_stats.get("total_tokens", 0) or (pt + ct)
    tps = round(ct / max(elapsed, 0.001), 1)
    model = stream_stats.get("model_display", stream_stats.get("model", "?"))
    console.print(
        f"  📊 {model}  prompt={pt} completion={ct} total={tt}  "
        f"{tps} tok/s  {elapsed:.1f}s",
        style="stat",
    )


# ═══════════════════════════════════════════════════════════════
# Session history (minimal — stores conversation pairs for context)
# ═══════════════════════════════════════════════════════════════

class SessionHistory:
    """In-memory conversation history for multi-turn context."""

    def __init__(self, max_pairs: int = 20):
        self._pairs: List[Dict[str, str]] = []
        self._max = max_pairs

    def add(self, role: str, content: str):
        self._pairs.append({"role": role, "content": content})
        # Keep bounded
        if len(self._pairs) > self._max * 2:
            self._pairs = self._pairs[-self._max * 2:]

    def build_context_message(self, current: str) -> str:
        """Build full_message with history, matching server_final.py pattern."""
        if not self._pairs:
            return current
        context_lines = []
        for msg in self._pairs:
            role_label = "用户" if msg["role"] == "user" else "助手"
            text = msg["content"]
            if len(text) > 500:
                text = text[:500] + "..."
            context_lines.append(f"{role_label}: {text}")
        history_block = "\n".join(context_lines)
        return f"[对话历史]\n{history_block}\n\n[当前问题]\n{current}"

    def clear(self):
        self._pairs.clear()


# ═══════════════════════════════════════════════════════════════
# Built-in TUI commands (not LLM-routed)
# ═══════════════════════════════════════════════════════════════

BUILTIN_COMMANDS = {
    "exit":   "Exit the TUI",
    "quit":   "Exit the TUI",
    "clear":  "Clear conversation history",
    "reset":  "Clear screen and history",
    "help":   "Show available commands",
    "skills": "List registered skills",
    "mode":   "Switch mode (code/ask/plan)",
    "model":  "Show or set the active model",
    "stats":  "Show session statistics",
}


def handle_builtin(cmd: str, args_str: str, state: dict) -> bool:
    """Handle a builtin command. Returns True if handled."""
    cmd = cmd.lower()

    if cmd in ("exit", "quit"):
        console.print("\n👋 再见！", style="greeting")
        raise SystemExit(0)

    elif cmd == "clear":
        state["history"].clear()
        console.print("  ✓ History cleared", style="tool.ok")
        return True

    elif cmd == "reset":
        state["history"].clear()
        console.clear()
        _print_banner(state)
        return True

    elif cmd == "help":
        lines = ["[bold]Built-in commands:[/]"]
        for name, desc in BUILTIN_COMMANDS.items():
            lines.append(f"  [bold cyan]/{name:<10}[/] {desc}")
        try:
            from skills import list_skills
            skills = list_skills()
            if skills:
                lines.append("\n[bold]Skill commands (routed to LLM):[/]")
                for s in sorted(skills, key=lambda x: x.name):
                    aliases = f" ({', '.join(s.aliases)})" if s.aliases else ""
                    hint = f" {s.argument_hint}" if s.argument_hint else ""
                    lines.append(
                        f"  [bold yellow]/{s.name}{hint}[/]{aliases}  {s.description[:60]}"
                    )
        except Exception:
            pass
        console.print("\n".join(lines))
        return True

    elif cmd == "skills":
        try:
            from skills import list_skills
            skills = list_skills()
            if not skills:
                console.print("  No skills registered", style="dim")
                return True
            for s in sorted(skills, key=lambda x: x.name):
                aliases = f"  aliases: {', '.join(s.aliases)}" if s.aliases else ""
                console.print(
                    f"  [bold yellow]/{s.name}[/]  {s.description[:70]}{aliases}",
                )
        except Exception as e:
            console.print(f"  Error listing skills: {e}", style="error")
        return True

    elif cmd == "mode":
        if args_str and args_str in ("code", "ask", "plan"):
            state["mode"] = args_str
            console.print(f"  ✓ Mode set to: {args_str}", style="tool.ok")
        else:
            console.print(f"  Current mode: {state['mode']}")
            console.print("  Usage: /mode code|ask|plan", style="dim")
        return True

    elif cmd == "model":
        if args_str:
            state["model"] = args_str
            state["env"] = build_env(
                state["workspace"], model=args_str,
                mode=state["mode"], backend=state.get("backend", "ollama"),
            )
            console.print(f"  ✓ Model set to: {args_str}", style="tool.ok")
        else:
            console.print(f"  Current model: {state.get('model', '(default)')}")
        return True

    elif cmd == "stats":
        console.print(f"  Session: {state['session_id']}")
        console.print(f"  Mode: {state['mode']}")
        console.print(f"  Model: {state.get('model', '(default)')}")
        console.print(f"  Workspace: {state['workspace']}")
        console.print(f"  Exchanges: {state.get('exchange_count', 0)}")

        # P5: Show detailed aggregated stats from session_stats store
        try:
            from session_stats import get_stats_store
            store = get_stats_store()
            stats = store.get_stats(state["session_id"])
            if stats["total_exchanges"] > 0:
                console.print()
                # Token summary table
                tbl = Table(title="📊 Session Statistics", border_style="dim", expand=False)
                tbl.add_column("Metric", style="bold")
                tbl.add_column("Value", justify="right")
                tbl.add_row("Total exchanges", str(stats["total_exchanges"]))
                tbl.add_row("Total turns", str(stats["total_turns"]))
                tbl.add_row("Prompt tokens", f"{stats['total_prompt_tokens']:,}")
                tbl.add_row("Completion tokens", f"{stats['total_completion_tokens']:,}")
                tbl.add_row("Total tokens", f"{stats['total_tokens']:,}")
                tbl.add_row("Avg tokens/exchange", f"{stats['avg_tokens_per_exchange']:,.0f}")
                tbl.add_row("Avg tok/s", f"{stats['avg_tokens_per_second']:.1f}")
                tbl.add_row("Total time", f"{stats['total_elapsed_seconds']:.1f}s")
                tbl.add_row("Avg time/exchange", f"{stats['avg_elapsed_per_exchange']:.1f}s")
                tbl.add_row("Total tool calls", str(stats["total_tool_calls"]))
                console.print(tbl)

                # Tool distribution
                tools_dist = stats.get("tools_distribution", {})
                if tools_dist:
                    console.print()
                    ttbl = Table(title="🔧 Tool Usage", border_style="dim", expand=False)
                    ttbl.add_column("Tool", style="yellow")
                    ttbl.add_column("Calls", justify="right")
                    for tool, count in list(tools_dist.items())[:10]:
                        ttbl.add_row(tool, str(count))
                    console.print(ttbl)
        except Exception:
            pass

        return True

    return False


# ═══════════════════════════════════════════════════════════════
# Main chat loop
# ═══════════════════════════════════════════════════════════════

async def chat_once(
    user_input: str,
    env: Dict[str, str],
    session_id: str,
    workspace: Path,
    history: SessionHistory,
    renderer: EventRenderer,
    mode: str = "code",
) -> Optional[str]:
    """Send one message through agentic_chat_stream, render events, return response."""
    from agentic_loop import agentic_chat_stream

    full_message = history.build_context_message(user_input)

    stream_stats: Dict[str, Any] = {
        "model": env.get("NANOBOT_TELEMETRY_MODEL_NAME", ""),
        "model_display": env.get("NANOBOT_TELEMETRY_MODEL_NAME", ""),
        "phase": "Reading / Generation",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "context_length": int(env.get("NANOBOT_TELEMETRY_CONTEXT_LENGTH",
                                       env.get("OLLAMA_NUM_CTX", "8192"))),
        "output_limit": int(env.get("NANOBOT_TELEMETRY_OUTPUT_LIMIT",
                                     env.get("NANOBOT_AGENTS__DEFAULTS__MAX_TOKENS", "16384"))),
    }

    _stop_flag = False

    def _is_stop():
        return _stop_flag

    renderer.reset()
    start_time = time.time()

    try:
        async for event in agentic_chat_stream(
            user_message=full_message,
            env=env,
            session_id=session_id,
            workspace=workspace,
            stream_stats=stream_stats,
            is_stop_fn=_is_stop,
            max_turns=int(env.get("NANOBOT_MAX_TOOL_ITERATIONS", "10")),
            mode=mode,
        ):
            renderer.render(event)

    except KeyboardInterrupt:
        _stop_flag = True
        console.print("\n⏹  Interrupted", style="stat")
    except Exception as e:
        console.print(f"\n❌ Error: {e}", style="error")
        logger.exception("agentic_chat_stream error")

    elapsed = time.time() - start_time
    render_stats(stream_stats, elapsed)

    # P5: Record stats for TUI /stats command
    try:
        from session_stats import get_stats_store
        _ss = get_stats_store()
        _ss.add_turn(
            session_id=session_id,
            prompt_tokens=int(stream_stats.get("prompt_tokens") or 0),
            completion_tokens=int(stream_stats.get("completion_tokens") or 0),
            total_tokens=int(stream_stats.get("total_tokens") or 0),
            tool_calls=renderer._tool_count,
            tools_used=renderer._tools_used,
            elapsed_seconds=elapsed,
            model=stream_stats.get("model", ""),
            turns=renderer._turn or 1,
        )
    except Exception:
        pass

    response = renderer.get_full_response()
    if response:
        history.add("user", user_input)
        history.add("assistant", response[:2000])

    return response


def _scan_project(workspace: Path) -> str:
    """P7: Run project detection and return a short summary line."""
    try:
        from project_detector import detect_project
        info = detect_project(str(workspace))
        parts = []
        lang = info.get("language", "unknown")
        if lang != "unknown":
            parts.append(lang.capitalize())
        frameworks = info.get("frameworks", [])
        if frameworks:
            parts.append(", ".join(frameworks[:3]))
        pkg = info.get("package_manager")
        if pkg:
            parts.append(pkg)
        test_fw = info.get("test_framework")
        if test_fw:
            parts.append(f"tests: {test_fw}")
        if info.get("has_git"):
            parts.append("Git")
        if info.get("has_docker"):
            parts.append("Docker")
        return " · ".join(parts) if parts else ""
    except Exception:
        return ""


def _print_banner(state: dict):
    """Print the welcome banner."""
    project_line = _scan_project(state["workspace"])

    console.print()
    banner_lines = [
        "[bold blue]Nanobot TUI[/]  —  Terminal interface for Nanobot\n",
        f"  Workspace: [dim]{state['workspace']}[/]",
        f"  Model:     [dim]{state.get('model', '(default)')}[/]",
        f"  Mode:      [dim]{state['mode']}[/]",
        f"  Session:   [dim]{state['session_id']}[/]",
    ]
    if project_line:
        banner_lines.insert(2, f"  Stack:     [bold green]{project_line}[/]")
    banner_lines.append("")
    banner_lines.append(
        "Type [bold cyan]/help[/] for commands.  "
        "[dim]Shift+Enter[/] for newlines.  "
        "[bold cyan]/exit[/] to quit."
    )
    console.print(
        Panel(
            "\n".join(banner_lines),
            title="🤖 Nanobot",
            border_style="blue",
            expand=False,
            width=min(console.width, 80),
        )
    )
    console.print()


# ── prompt_toolkit key bindings ──
_kb = KeyBindings()


@_kb.add(Keys.Enter)
def _submit(event):
    """Enter submits (unless buffer is empty)."""
    buf = event.current_buffer
    if buf.text.strip():
        buf.validate_and_handle()
    else:
        # Empty — insert newline instead (noop for blank submit)
        buf.insert_text("\n")


@_kb.add(Keys.Escape, Keys.Enter)
def _newline_alt(event):
    """Alt+Enter inserts a newline."""
    event.current_buffer.insert_text("\n")


async def main_loop(args):
    """Main interactive loop."""
    workspace = Path(args.workspace).resolve()
    model = args.model or os.environ.get("NANOBOT_MODEL", "")
    mode = args.mode
    backend = args.backend

    if not workspace.exists():
        console.print(f"❌ Workspace not found: {workspace}", style="error")
        sys.exit(1)

    # Ensure venv site-packages is importable
    if VENV_SITE.exists() and str(VENV_SITE) not in sys.path:
        sys.path.insert(0, str(VENV_SITE))

    # Verify core import
    try:
        from agentic_loop import agentic_chat_stream
    except ImportError as e:
        console.print(f"❌ Cannot import agentic_loop: {e}", style="error")
        console.print(f"   Ensure you run from: {_THIS_DIR}", style="dim")
        sys.exit(1)

    session_id = f"tui-{uuid.uuid4().hex[:8]}"
    env = build_env(workspace, model=model, mode=mode, backend=backend)
    history = SessionHistory()
    renderer = EventRenderer()

    state = {
        "workspace": workspace,
        "session_id": session_id,
        "mode": mode,
        "model": model,
        "env": env,
        "history": history,
        "exchange_count": 0,
        "backend": backend,
    }

    _print_banner(state)

    # prompt_toolkit session with file history
    prompt_history = FileHistory(str(HISTORY_FILE))
    session = PromptSession(
        history=prompt_history,
        key_bindings=_kb,
        multiline=True,
        enable_open_in_editor=False,
    )

    mode_colors = {"code": "ansigreen", "ask": "ansicyan", "plan": "ansiyellow"}

    while True:
        try:
            mode_c = mode_colors.get(state["mode"], "ansiwhite")
            prompt_text = HTML(
                f'<b><{mode_c}>{state["mode"]}</{mode_c}></b>'
                f'<ansibrightblack> › </ansibrightblack>'
            )

            user_input = await session.prompt_async(
                prompt_text,
                placeholder=HTML('<ansigray>Type a message or /help...</ansigray>'),
            )
        except KeyboardInterrupt:
            console.print("\n👋 再见！", style="greeting")
            break
        except EOFError:
            console.print("\n👋 再见！", style="greeting")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # ── Check for slash commands ──
        if user_input.startswith("/"):
            parts = user_input.split(None, 1)
            cmd = parts[0][1:]  # strip leading /
            args_str = parts[1] if len(parts) > 1 else ""

            # Try builtin first
            if cmd.lower() in BUILTIN_COMMANDS:
                handle_builtin(cmd, args_str, state)
                continue

            # Check if it's a known skill — if so, pass through to LLM
            skill_name = detect_slash_command(user_input)
            if skill_name:
                console.print(f"  ⚡ Skill: /{skill_name}", style="mode.label")

        # ── Send to agentic loop ──
        state["exchange_count"] = state.get("exchange_count", 0) + 1
        console.print()

        await chat_once(
            user_input=user_input,
            env=state["env"],
            session_id=state["session_id"],
            workspace=state["workspace"],
            history=state["history"],
            renderer=renderer,
            mode=state["mode"],
        )

        console.print()


# ═══════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(
        description="Nanobot Terminal UI (TUI)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 tui.py                          # default workspace\n"
            "  python3 tui.py --workspace /my/project   # custom workspace\n"
            "  python3 tui.py --model qwen3.5:35b       # specific model\n"
            "  python3 tui.py --mode ask                # ask-only mode\n"
        ),
    )
    parser.add_argument(
        "--workspace", "-w",
        default=str(DEFAULT_WORKSPACE),
        help=f"Workspace directory (default: {DEFAULT_WORKSPACE})",
    )
    parser.add_argument(
        "--model", "-m",
        default="",
        help="Model name (e.g. qwen3.5:35b). Uses env/config default if empty.",
    )
    parser.add_argument(
        "--mode",
        choices=["code", "ask", "plan"],
        default="code",
        help="Operating mode (default: code)",
    )
    parser.add_argument(
        "--backend", "-b",
        default="ollama",
        help="Backend: ollama or ollm (default: ollama)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=10,
        help="Max agentic turns per exchange (default: 10)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        asyncio.run(main_loop(args))
    except (KeyboardInterrupt, SystemExit):
        console.print("\n👋 再见！", style="greeting")
    except Exception as e:
        console.print(f"\n❌ Fatal: {e}", style="error")
        if args.debug:
            console.print_exception()
        sys.exit(1)


if __name__ == "__main__":
    main()
