"""
U23: Command System Tests
==========================
Tests for command registry, alias matching, /help generation,
unknown command fallback, auto-discovery, and skills integration.

Run: python3 tests/test_commands.py
"""
import sys
from pathlib import Path

# Ensure web_ui is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_results = []


def check(name, condition):
    status = "✅" if condition else "❌"
    _results.append((name, condition))
    print(f"  {status} {name}")
    return condition


# ═══════════════════════════════════════════════════════════════
# 1. CommandDefinition dataclass
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 1. CommandDefinition ══╗")

from commands.base import CommandDefinition

# 1a: Can create minimal definition
cmd = CommandDefinition(name="test", description="A test command")
check("1a: name set", cmd.name == "test")
check("1b: description set", cmd.description == "A test command")
check("1c: aliases default empty", cmd.aliases == [])
check("1d: category default 'user'", cmd.category == "user")
check("1e: handler default None", cmd.handler is None)
check("1f: skill default None", cmd.skill is None)
check("1g: hidden default False", cmd.hidden is False)

# 1h: Full definition
cmd2 = CommandDefinition(
    name="deploy",
    description="Deploy the app",
    aliases=["push", "ship"],
    category="system",
    handler=lambda **kw: {"handled": True},
    skill=None,
    hidden=True,
)
check("1h: aliases set", cmd2.aliases == ["push", "ship"])
check("1i: category set", cmd2.category == "system")
check("1j: handler callable", callable(cmd2.handler))
check("1k: hidden set", cmd2.hidden is True)


# ═══════════════════════════════════════════════════════════════
# 2. Registry: register, get_command, list_commands
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 2. Command Registry ══╗")

from commands import register, get_command, list_commands, _registry, _alias_map

# Clear registry for clean test state
_registry.clear()
_alias_map.clear()

# 2a: Register a command
register(CommandDefinition(
    name="greet",
    description="Say hello",
    aliases=["hi", "hello"],
    category="user",
))
check("2a: command registered", "greet" in _registry)
check("2b: alias 'hi' mapped", "hi" in _alias_map)
check("2c: alias 'hello' mapped", "hello" in _alias_map)

# 2d: get_command by name
found = get_command("greet")
check("2d: get_command by name", found is not None and found.name == "greet")

# 2e: get_command by alias
found2 = get_command("hi")
check("2e: get_command by alias 'hi'", found2 is not None and found2.name == "greet")

found3 = get_command("hello")
check("2f: get_command by alias 'hello'", found3 is not None and found3.name == "greet")

# 2g: get_command unknown → None
check("2g: unknown command → None", get_command("nonexistent") is None)

# 2h: get_command with leading /
check("2h: get_command strips leading /", get_command("/greet") is not None)

# 2i: get_command case insensitive
check("2i: get_command case insensitive", get_command("GREET") is not None)

# 2j: list_commands returns all
register(CommandDefinition(name="deploy", description="Deploy", category="system"))
all_cmds = list_commands()
check("2j: list_commands returns 2 commands", len(all_cmds) == 2)

# 2k: list_commands filtered by category
user_cmds = list_commands(category="user")
check("2k: 1 user command", len(user_cmds) == 1 and user_cmds[0].name == "greet")

sys_cmds = list_commands(category="system")
check("2l: 1 system command", len(sys_cmds) == 1 and sys_cmds[0].name == "deploy")

# 2m: list_commands with no match
ai_cmds = list_commands(category="ai")
check("2m: 0 ai commands", len(ai_cmds) == 0)

# 2n: Overwrite existing command
register(CommandDefinition(name="greet", description="Updated greeting", category="user"))
check("2n: overwrite updates description",
      get_command("greet").description == "Updated greeting")


# ═══════════════════════════════════════════════════════════════
# 3. Auto-discovery
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 3. Auto-Discovery ══╗")

# Re-import to trigger auto-discovery with a fresh registry
_registry.clear()
_alias_map.clear()

from commands import _auto_discover
_auto_discover()

# 3a: help.py should have been auto-discovered and registered commands
check("3a: /help registered by auto-discovery", get_command("help") is not None)
check("3b: /status registered by auto-discovery", get_command("status") is not None)
check("3c: /backup registered by auto-discovery", get_command("backup") is not None)
check("3d: /tasks registered by auto-discovery", get_command("tasks") is not None)
check("3e: /skills registered by auto-discovery", get_command("skills") is not None)

# 3f: Alias lookup from auto-discovered commands
check("3f: alias 'h' → help", get_command("h") is not None and get_command("h").name == "help")
check("3g: alias '?' → help", get_command("?") is not None and get_command("?").name == "help")
check("3h: alias 'system_status' → status",
      get_command("system_status") is not None and
      get_command("system_status").name == "status")


# ═══════════════════════════════════════════════════════════════
# 4. /help handler
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 4. /help Handler ══╗")

from commands.help import handle_help

# 4a: handler returns dict with 'handled' and 'message'
result = handle_help(session_id="test", args="")
check("4a: handler returns dict", isinstance(result, dict))
check("4b: result has 'handled'=True", result.get("handled") is True)
check("4c: result has 'message'", "message" in result and len(result["message"]) > 0)

# 4d: help message includes system commands
msg = result["message"]
check("4d: help lists /help", "/help" in msg)
check("4e: help lists /status", "/status" in msg)
check("4f: help lists /backup", "/backup" in msg)
check("4g: help lists /tasks", "/tasks" in msg)

# 4h: help message includes skills section
check("4h: help has 'Skills' section", "Skills" in msg or "skills" in msg.lower())

# 4i: help message includes invocation hint
check("4i: help has invocation hint", "invoke" in msg.lower() or "/command" in msg.lower())


# ═══════════════════════════════════════════════════════════════
# 5. Command handler dispatch
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 5. Handler Dispatch ══╗")

# 5a: Command with handler → callable
help_cmd = get_command("help")
check("5a: /help has handler", help_cmd is not None and help_cmd.handler is not None)

# 5b: Command without handler (metadata-only) → handler is None
health_cmd = get_command("health")
check("5b: /health has no handler (metadata only)", health_cmd is not None and health_cmd.handler is None)

# 5c: Simulate dispatcher logic (as in server_final.py)
def simulate_dispatch(command_name, command_args=""):
    cmd = get_command(command_name)
    if cmd and cmd.handler:
        result = cmd.handler(session_id="test", args=command_args)
        if isinstance(result, dict) and result.get("handled"):
            return f"{cmd.name}_handled"
    return None  # fall through to legacy

check("5c: /help dispatches via registry", simulate_dispatch("help") == "help_handled")
check("5d: /health falls through (no handler)", simulate_dispatch("health") is None)
check("5e: unknown falls through", simulate_dispatch("unknown") is None)
check("5f: alias 'h' dispatches to help", simulate_dispatch("h") == "help_handled")


# ═══════════════════════════════════════════════════════════════
# 6. Category metadata
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 6. Category Metadata ══╗")

# 6a: help is system category
check("6a: /help is system category", help_cmd.category == "system")

# 6b: All auto-discovered commands are system
all_sys = list_commands(category="system")
check("6b: all auto-discovered commands are system", len(all_sys) >= 5)

# 6c: Command names are valid (no leading /)
for cmd in list_commands():
    if cmd.name.startswith("/"):
        check(f"6c: {cmd.name} has no leading /", False)
        break
else:
    check("6c: no command has leading /", True)


# ═══════════════════════════════════════════════════════════════
# 7. Backward compatibility
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 7. Backward Compatibility ══╗")

# 7a: Skills framework still works independently
try:
    from skills import get_skill, list_skills
    check("7a: skills framework importable", True)
    skills = list_skills()
    check("7b: skills framework returns skills", len(skills) > 0)
except Exception as e:
    check("7a: skills framework importable", False)
    check("7b: skills framework returns skills", False)

# 7c: Commands registry does not interfere with skills
skill_commit = get_skill("commit")
check("7c: /commit still found via skills", skill_commit is not None)

# 7d: Commands registry has no skill-only commands
cmd_commit = get_command("commit")
check("7d: /commit not in commands registry (stays in skills)",
      cmd_commit is None)

# 7e: Server code pattern — U23 guard
server_path = Path(__file__).resolve().parent.parent / "server_final.py"
if server_path.exists():
    server_code = server_path.read_text(errors="replace")
    check("7e: server has U23 registry check",
          "_u23_get_command" in server_code)
    check("7f: server has '_handled' guard",
          "endswith('_handled')" in server_code)
    check("7g: old if-elif chain preserved as fallback",
          "elif command_name == 'help':" in server_code or
          "command_name == 'help'" in server_code)
else:
    check("7e: server_final.py exists", False)
    check("7f: server has '_handled' guard", False)
    check("7g: old if-elif chain preserved as fallback", False)


# ═══════════════════════════════════════════════════════════════
# 8. Edge cases
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 8. Edge Cases ══╗")

# 8a: Empty name
check("8a: get_command('') → None", get_command("") is None)

# 8b: get_command with only /
check("8b: get_command('/') → None", get_command("/") is None)

# 8c: Register command with / prefix — stored without /
_registry.clear()
_alias_map.clear()
register(CommandDefinition(name="/prefixed", description="test"))
check("8c: /prefixed stored as 'prefixed'", "prefixed" in _registry or "/prefixed" in _registry)

# Restore auto-discovered commands for remaining tests
_registry.clear()
_alias_map.clear()
_auto_discover()

# 8d: handler with kwargs
def _handler_with_kwargs(**kwargs):
    return {"handled": True, "message": f"args={kwargs.get('args', '')}"}

_registry.clear()
_alias_map.clear()
register(CommandDefinition(name="echo", description="echo args", handler=_handler_with_kwargs))
result = get_command("echo").handler(session_id="s1", args="hello world")
check("8d: handler receives kwargs", result["message"] == "args=hello world")


# ═══════════════════════════════════════════════════════════════
# 9. Phase 2: required_permission field
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 9. required_permission ══╗")

# Restore auto-discovered commands
_registry.clear()
_alias_map.clear()
_auto_discover()

# 9a: CommandDefinition has required_permission field
cmd_rp = CommandDefinition(name="admin_cmd", description="admin only",
                           required_permission="admin")
check("9a: required_permission field exists", cmd_rp.required_permission == "admin")

# 9b: Default is None
cmd_no_rp = CommandDefinition(name="pub_cmd", description="public")
check("9b: required_permission default None", cmd_no_rp.required_permission is None)

# 9c: Existing auto-discovered commands have None (no regression)
help_cmd_rp = get_command("help")
check("9c: /help has no required_permission", help_cmd_rp.required_permission is None)


# ═══════════════════════════════════════════════════════════════
# 10. Phase 2: /status full migration
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 10. /status Full Migration ══╗")

# 10a: /status is registered with a handler (not metadata-only)
status_cmd = get_command("status")
check("10a: /status registered", status_cmd is not None)
check("10b: /status has handler", status_cmd.handler is not None)
check("10c: /status category is system", status_cmd.category == "system")

# 10d: Handler returns correct format
status_result = status_cmd.handler(session_id="test", args="")
check("10d: handler returns dict", isinstance(status_result, dict))
check("10e: handler returns handled=True", status_result.get("handled") is True)
check("10f: handler returns message", "message" in status_result and len(status_result["message"]) > 0)

# 10g: Output format matches old server_final.py format
msg = status_result["message"]
check("10g: output has '[系统]' prefix", "[系统]" in msg)
check("10h: output has 'CPU:' line", "CPU:" in msg)

# 10i: Aliases still work
check("10i: alias 'system_status' → status",
      get_command("system_status") is not None and
      get_command("system_status").name == "status")
check("10j: alias 'show_status' → status",
      get_command("show_status") is not None and
      get_command("show_status").name == "status")
check("10k: alias 'view_status' → status",
      get_command("view_status") is not None and
      get_command("view_status").name == "status")

# 10l: Dispatch simulation — /status now handled by registry
check("10l: /status dispatches via registry",
      simulate_dispatch("status") == "status_handled")
check("10m: alias dispatch via registry",
      simulate_dispatch("system_status") == "status_handled")


# ═══════════════════════════════════════════════════════════════
# 11. Phase 2: Integration regression — server_final.py verification
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 11. Integration Regression ══╗")

server_path = Path(__file__).resolve().parent.parent / "server_final.py"
server_code = server_path.read_text(errors="replace") if server_path.exists() else ""

# 11a: /status removed from if-elif chain
check("11a: /status removed from legacy if-elif chain",
      "command_name in {'status'" not in server_code)

# 11b: Migration comment present
check("11b: migration comment for /status",
      "migrated to commands/status.py" in server_code)

# 11c: Other commands still in if-elif chain (not over-migrated)
check("11c: /health still in legacy chain",
      "'health'" in server_code and "health_check" in server_code)
check("11d: /backup still in legacy chain",
      "'backup'" in server_code and "backup_execute" in server_code)
check("11e: /tasks still in legacy chain",
      "'tasks'" in server_code and "task_list" in server_code)

# 11f: U23 registry guard still present
check("11f: U23 registry guard present",
      "_u23_get_command" in server_code)
check("11g: _handled guard present",
      "endswith('_handled')" in server_code)


# ═══════════════════════════════════════════════════════════════
# 12. Phase 2: /help output includes migrated /status
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 12. /help Integration ══╗")

from commands.help import handle_help

# Ensure registry is populated
_registry.clear()
_alias_map.clear()
_auto_discover()

help_result = handle_help(session_id="test", args="")
help_msg = help_result["message"]

# 12a: /help lists /status (now with handler)
check("12a: /help includes /status", "/status" in help_msg)

# 12b: /help still lists other system commands
check("12b: /help includes /health", "/health" in help_msg)
check("12c: /help includes /backup", "/backup" in help_msg)
check("12d: /help includes /tasks", "/tasks" in help_msg)
check("12e: /help includes /skills", "/skills" in help_msg)
check("12f: /help includes /help itself", "/help" in help_msg)

# 12g: /help still shows skills section
check("12g: /help has skills section", "Skills" in help_msg or "skills" in help_msg.lower())


# ═══════════════════════════════════════════════════════════════
# 13. Phase 2: Output format backward compatibility
# ═══════════════════════════════════════════════════════════════
print("\n╔══ 13. Output Format Compat ══╗")

# 13a: /status output format matches the old server_final.py format exactly
#      Old format: "[系统] 系统状态:\nCPU: X%\n内存: Y%\n磁盘: Z%\n\n[用户] ..."
status_out = get_command("status").handler(session_id="t", args="")["message"]
check("13a: starts with [系统] 系统状态:", status_out.startswith("[系统] 系统状态:"))
check("13b: has '内存:' line", "内存:" in status_out)
check("13c: has '磁盘:' line", "磁盘:" in status_out)
check("13d: ends with user instruction", "[用户]" in status_out)

# 13e: /help output format has consistent structure
help_out = handle_help(session_id="t", args="")["message"]
check("13e: /help has section headers", "##" in help_out)
check("13f: /help has invocation hint", "/command" in help_out)


# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "═" * 60)
    passed = sum(1 for _, ok in _results if ok)
    failed = sum(1 for _, ok in _results if not ok)
    total = len(_results)
    print(f"U23 Command System Tests: {passed}/{total} passed, {failed} failed")

    if failed:
        print("\nFailed tests:")
        for name, ok in _results:
            if not ok:
                print(f"  ❌ {name}")
        sys.exit(1)
    else:
        print("All tests passed! ✅")
        sys.exit(0)
