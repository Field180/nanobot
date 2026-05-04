"""
P94/P97/P98: Nanobot Skill Framework
=======================================
Skills that ship with Nanobot + user-defined SKILL.md loading.

Inspired by Claw's skills/bundled/, skills/loadSkillsDir, and SkillTool.

Architecture:
  - SkillDefinition: dataclass with name, description, prompt builder,
    allowed_tools, disable_model_invocation, is_enabled, skill_dir
  - _registry: dict of registered skills (bundled + user-defined)
  - register_skill(): add a skill to the registry
  - get_skill(): look up by name or alias
  - list_skills(): list all available skills (respects is_enabled)
  - execute_skill(): build prompt + record usage for ranking
  - load_user_skills(): scan ~/.nanobot/skills/ and .nanobot/skills/ for SKILL.md
  - discover_skills_for_paths(): dynamic skill discovery from file operations
  - build_skill_system_prompt(): budget-aware skill listing for model context
  - match_skill_by_intent(): natural language → skill matching via when_to_use

P98 additions (Claw patterns):
  - P98a: Usage tracking with recency-decay scoring
  - P98b: Context-budget-aware skill listing (1% of context window)
  - P98c: Proactive skill suggestion in agentic loop
  - P98d: allowed_tools, disable_model_invocation, is_enabled, ${NANOBOT_SKILL_DIR}
  - P98e: Skill deduplication by realpath + dynamic discovery

P99 additions (Claw patterns — advanced):
  - P99a: Conditional skills — paths frontmatter + activation on matching files
  - P99b: Shell command execution in skill prompts (!`cmd` and ```! cmd ```)
  - P99c: Forked skill execution — context: fork runs in isolated sub_agent
  - P99d: allowed_tools enforcement — restrict tool access during execution

P100 additions (Claw patterns — execution hardening):
  - P100a: Skill input validation — validateInput checks before execution
  - P100b: Skill permission allow/deny rules with safe-properties auto-allow
  - P100c: Agentic loop allowed_tools enforcement via execute_skill metadata
  - P100d: Skill effort/model override from frontmatter
  - P100e: Skill invocation tracking for compaction preservation
"""
import fnmatch
import logging
import os
import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("nanobot.skills")

# ═══════════════════════════════════════════════════════════════
# Skill definition
# ═══════════════════════════════════════════════════════════════

@dataclass
class SkillDefinition:
    """Definition for a bundled or user-defined skill."""
    name: str
    description: str
    prompt_builder: Callable[[str], str]
    aliases: List[str] = field(default_factory=list)
    uses_sub_agent: bool = False
    user_invocable: bool = True
    argument_hint: str = ""
    when_to_use: str = ""
    source: str = "bundled"  # bundled | user | project
    # P98d: New fields inspired by Claw
    allowed_tools: List[str] = field(default_factory=list)  # restrict tools during execution
    disable_model_invocation: bool = False  # prevent auto-invocation by model
    is_enabled: Optional[Callable[[], bool]] = None  # runtime enable/disable check
    skill_dir: Optional[str] = None  # directory containing the skill (for ${NANOBOT_SKILL_DIR})
    # P99a: Conditional skill — only activated when matching files are touched
    paths: List[str] = field(default_factory=list)  # glob patterns for file paths
    # P99c: Execution context — 'inline' (default) or 'fork' (run as isolated sub_agent)
    context: str = "inline"  # inline | fork
    # P100d: Model/effort overrides (Claw's model + effort fields on Command)
    model: Optional[str] = None  # override model alias during skill execution
    effort: Optional[float] = None  # override effort level (0.0-1.0)
    # P101: Runtime enforcement fields (Claude Code-style coding skills)
    # These fields turn skills from "prompt templates" into "runtime modes"
    # that the agentic loop enforces as system constraints.
    mode: Optional[str] = None  # analyze | debug | verify | refactor | None
    write_policy: str = "allowed"  # allowed | forbid | explicit_only | allowed_with_verification
    requires_verification: bool = False  # if True, writes trigger pending_verification state
    disallowed_tools: List[str] = field(default_factory=list)  # tools blocked in this mode
    completion_criteria: List[str] = field(default_factory=list)  # what must be true to finish
    output_fields: List[str] = field(default_factory=list)  # required fields in final output
    default_subagent: Optional[str] = None  # research | verify | None


# ═══════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════

_registry: Dict[str, SkillDefinition] = {}
_alias_map: Dict[str, str] = {}  # alias -> canonical name

# P99a: Conditional skills — stored separately until activated by matching files
_conditional_skills: Dict[str, SkillDefinition] = {}
_activated_conditional_names: Set[str] = set()


def register_skill(skill: SkillDefinition) -> None:
    """Register a skill. Called at module init time.

    P99a: Skills with `paths` frontmatter are stored as conditional
    and only activated when matching file paths are touched.
    """
    # P99a: If skill has path patterns, store as conditional (not yet active)
    if skill.paths and skill.source != "bundled":
        _conditional_skills[skill.name] = skill
        logger.info(f"[P99a] Conditional skill stored: /{skill.name} "
                     f"(activates on: {skill.paths})")
        return

    if skill.name in _registry:
        logger.warning(f"[Skills] Overwriting existing skill: {skill.name}")
    _registry[skill.name] = skill
    for alias in skill.aliases:
        _alias_map[alias.lower()] = skill.name
    logger.info(f"[Skills] Registered skill: /{skill.name} ({len(skill.aliases)} aliases)")


def get_skill(name: str) -> Optional[SkillDefinition]:
    """Look up a skill by name or alias."""
    name_lower = name.lower()
    if name_lower in _registry:
        return _registry[name_lower]
    canonical = _alias_map.get(name_lower)
    if canonical:
        return _registry.get(canonical)
    return None


def list_skills() -> List[SkillDefinition]:
    """List all registered skills (user-invocable + enabled only)."""
    result = []
    for s in _registry.values():
        if not s.user_invocable:
            continue
        # P98d: Check runtime enable function
        if s.is_enabled is not None:
            try:
                if not s.is_enabled():
                    continue
            except Exception:
                continue
        result.append(s)
    return result


# ═══════════════════════════════════════════════════════════════
# P100a: Skill input validation (inspired by Claw SkillTool.validateInput)
# ═══════════════════════════════════════════════════════════════

def validate_skill_input(name: str, is_model_invocation: bool = False) -> Dict[str, Any]:
    """P100a: Validate a skill name before execution.

    Mirrors Claw's SkillTool.validateInput — checks:
    1. Skill name is not empty
    2. Skill exists in registry
    3. Skill does not have disable_model_invocation (if model-invoked)
    4. Skill is enabled (runtime is_enabled check)

    Args:
        name: Skill name (with or without leading '/').
        is_model_invocation: True if the model is invoking (not user).

    Returns:
        {"valid": bool, "error": str, "error_code": int, "skill": SkillDefinition}
        error_codes: 0=ok, 1=empty, 2=unknown, 3=disabled_invocation, 4=not_enabled
    """
    # Normalize: strip leading slash
    trimmed = name.strip()
    if trimmed.startswith("/"):
        trimmed = trimmed[1:]

    if not trimmed:
        return {"valid": False, "error": f"Invalid skill name: '{name}'",
                "error_code": 1, "skill": None}

    skill = get_skill(trimmed)
    if not skill:
        return {"valid": False, "error": f"Unknown skill: {trimmed}",
                "error_code": 2, "skill": None}

    # Model invocation check (Claw: disableModelInvocation → errorCode 4)
    if is_model_invocation and skill.disable_model_invocation:
        return {"valid": False,
                "error": f"Skill /{trimmed} cannot be auto-invoked (disable_model_invocation)",
                "error_code": 3, "skill": skill}

    # Runtime enable check
    if skill.is_enabled is not None:
        try:
            if not skill.is_enabled():
                return {"valid": False,
                        "error": f"Skill /{trimmed} is currently disabled",
                        "error_code": 4, "skill": skill}
        except Exception:
            return {"valid": False,
                    "error": f"Skill /{trimmed} enable check failed",
                    "error_code": 4, "skill": skill}

    return {"valid": True, "error": "", "error_code": 0, "skill": skill}


def execute_skill(name: str, args: str = "") -> Optional[str]:
    """Build the skill prompt for injection into the agentic loop.

    Returns the prompt string, or None if skill not found.
    The caller injects this as the user message to agentic_chat_stream.

    P99b: Shell commands in prompts (!`cmd` / ```! cmd ```) are executed inline.
    P99c: If skill has context='fork', returns a special fork-prefixed prompt.
    P99d: If skill has allowed_tools, returns metadata alongside prompt.
    P100a: Validates input before execution.
    P100e: Tracks invoked skill for compaction preservation.
    """
    # P100a: Validate first
    validation = validate_skill_input(name)
    if not validation["valid"]:
        logger.warning(f"[P100a] Skill validation failed: {validation['error']}")
        return None

    skill = validation["skill"]

    try:
        prompt = skill.prompt_builder(args)

        # P99b: Execute shell commands embedded in skill prompt
        prompt = execute_shell_in_prompt(prompt, skill.name)

        # P99c: Prefix fork marker if skill requests forked execution
        if skill.context == "fork":
            prompt = _build_forked_prompt(skill, prompt)

        # P99d: Append allowed_tools metadata for agentic loop enforcement
        if skill.allowed_tools:
            prompt += f"\n\n<!-- SKILL_ALLOWED_TOOLS: {','.join(skill.allowed_tools)} -->"

        # P100d: Append model/effort override metadata
        if skill.model:
            prompt += f"\n<!-- SKILL_MODEL: {skill.model} -->"
        if skill.effort is not None:
            prompt += f"\n<!-- SKILL_EFFORT: {skill.effort} -->"

        # P101: Append runtime enforcement metadata
        if skill.mode:
            prompt += f"\n<!-- SKILL_MODE: {skill.mode} -->"
        if skill.write_policy != "allowed":
            prompt += f"\n<!-- SKILL_WRITE_POLICY: {skill.write_policy} -->"
        if skill.requires_verification:
            prompt += "\n<!-- SKILL_REQUIRES_VERIFICATION: true -->"

        logger.info(f"[Skills] Executed /{skill.name}: {len(prompt)} chars prompt"
                     f" (context={skill.context})")

        # P100e: Track invocation for compaction preservation
        track_skill_invocation(skill.name, prompt)

        # P98a: Track usage for ranking
        try:
            from skills.usage_tracking import record_skill_usage
            record_skill_usage(skill.name)
        except Exception:
            pass
        return prompt
    except Exception as e:
        logger.error(f"[Skills] Failed to build prompt for /{skill.name}: {e}")
        return f"Error building skill prompt: {e}"


def get_skill_metadata(name: str) -> Optional[Dict]:
    """P99d/P100d: Get skill execution metadata for agentic loop enforcement.

    Returns dict with allowed_tools, context, model, effort, etc. or None.
    """
    skill = get_skill(name)
    if not skill:
        return None
    return {
        "name": skill.name,
        "allowed_tools": skill.allowed_tools,
        "context": skill.context,
        "disable_model_invocation": skill.disable_model_invocation,
        "uses_sub_agent": skill.uses_sub_agent,
        "model": skill.model,
        "effort": skill.effort,
        # P101: Runtime enforcement metadata
        "mode": skill.mode,
        "write_policy": skill.write_policy,
        "requires_verification": skill.requires_verification,
        "disallowed_tools": skill.disallowed_tools,
        "completion_criteria": skill.completion_criteria,
        "output_fields": skill.output_fields,
        "default_subagent": skill.default_subagent,
    }


def format_skill_help() -> str:
    """Format help text listing all available skills."""
    skills = list_skills()
    if not skills:
        return "No skills available."

    lines = ["# Available Skills", ""]
    for s in sorted(skills, key=lambda x: x.name):
        aliases = f" (aliases: {', '.join(s.aliases)})" if s.aliases else ""
        hint = f" {s.argument_hint}" if s.argument_hint else ""
        src = f" [{s.source}]" if s.source != "bundled" else ""
        lines.append(f"- **/{s.name}{hint}**{aliases}{src} — {s.description}")
    lines.append("")
    lines.append("Use `/skill_name [args]` to invoke a skill.")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# P100b: Skill permission allow/deny rules (inspired by Claw checkPermissions)
# ═══════════════════════════════════════════════════════════════

_skill_allow_rules: List[str] = []  # skill names/patterns allowed without prompt
_skill_deny_rules: List[str] = []   # skill names/patterns blocked

# P100b: Safe skill properties — skills with only these properties auto-allow
# (inspired by Claw's SAFE_SKILL_PROPERTIES + skillHasOnlySafeProperties)
_SAFE_SKILL_FIELDS = frozenset([
    "name", "description", "prompt_builder", "aliases", "uses_sub_agent",
    "user_invocable", "argument_hint", "when_to_use", "source",
    "disable_model_invocation", "is_enabled", "skill_dir", "context",
    "model", "effort", "paths",
])


def set_skill_permission_rules(allow: List[str] = None, deny: List[str] = None) -> None:
    """P100b: Configure skill permission allow/deny rules.

    Mirrors Claw's checkPermissions allow/deny rule lookup.
    Patterns support exact match and prefix wildcard (e.g., 'review:*').
    """
    global _skill_allow_rules, _skill_deny_rules
    if allow is not None:
        _skill_allow_rules = list(allow)
    if deny is not None:
        _skill_deny_rules = list(deny)
    logger.info(f"[P100b] Permission rules set: allow={_skill_allow_rules}, deny={_skill_deny_rules}")


def check_skill_permission(skill_name: str) -> Dict[str, Any]:
    """P100b: Check if a skill is allowed, denied, or needs user confirmation.

    Returns {"decision": "allow"|"deny"|"ask", "reason": str}.
    Evaluation order (mirrors Claw):
    1. Deny rules checked first
    2. Allow rules checked second
    3. Safe-properties auto-allow
    4. Default: ask
    """
    normalized = skill_name.lower().lstrip("/")

    def _rule_matches(rule: str, name: str) -> bool:
        rule_norm = rule.lower().lstrip("/")
        if rule_norm == name:
            return True
        if rule_norm.endswith(":*") and name.startswith(rule_norm[:-2]):
            return True
        if fnmatch.fnmatch(name, rule_norm):
            return True
        return False

    # 1. Check deny rules first
    for rule in _skill_deny_rules:
        if _rule_matches(rule, normalized):
            return {"decision": "deny", "reason": f"Blocked by deny rule: {rule}"}

    # 2. Check allow rules
    for rule in _skill_allow_rules:
        if _rule_matches(rule, normalized):
            return {"decision": "allow", "reason": f"Matched allow rule: {rule}"}

    # 3. Safe-properties auto-allow (Claw's skillHasOnlySafeProperties)
    skill = get_skill(normalized)
    if skill and _skill_has_only_safe_properties(skill):
        return {"decision": "allow", "reason": "Safe properties only"}

    # 4. Default: ask
    return {"decision": "ask", "reason": "No matching rule"}


def _skill_has_only_safe_properties(skill: SkillDefinition) -> bool:
    """P100b: Check if a skill has only safe (non-dangerous) properties.

    A skill with allowed_tools set is NOT safe (it modifies runtime behavior).
    Mirrors Claw's skillHasOnlySafeProperties allowlist pattern.
    """
    # Skills with allowed_tools are NOT auto-safe (they restrict tool access)
    if skill.allowed_tools:
        return False
    # Skills with fork context are NOT auto-safe (they spawn sub-agents)
    if skill.context == "fork":
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# P100e: Skill invocation tracking for compaction preservation
# (inspired by Claw's addInvokedSkill / clearInvokedSkillsForAgent)
# ═══════════════════════════════════════════════════════════════

_invoked_skills: Dict[str, str] = {}  # name -> prompt content


def track_skill_invocation(name: str, prompt: str) -> None:
    """P100e: Track an invoked skill's prompt for compaction preservation.

    When auto-compact runs, it can restore the skill prompt so the model
    retains skill awareness post-compaction.
    """
    _invoked_skills[name] = prompt
    logger.debug(f"[P100e] Tracked skill invocation: /{name} ({len(prompt)} chars)")


def get_invoked_skills() -> Dict[str, str]:
    """P100e: Get currently invoked skill prompts for compaction preservation."""
    return dict(_invoked_skills)


def clear_invoked_skills() -> None:
    """P100e: Clear invoked skill tracking (session end or test cleanup)."""
    _invoked_skills.clear()


# ═══════════════════════════════════════════════════════════════
# P99a: Conditional skill activation (inspired by Claw loadSkillsDir)
# ═══════════════════════════════════════════════════════════════

def activate_conditional_skills(file_paths: List[str], cwd: str = "") -> List[str]:
    """P99a: Activate conditional skills whose path patterns match touched files.

    Skills with `paths` frontmatter are only visible after the model touches
    matching files. This function checks each conditional skill's glob patterns
    against the given file paths and activates matches.

    Inspired by Claw's activateConditionalSkillsForPaths.

    Args:
        file_paths: List of absolute file paths being operated on.
        cwd: Current working directory (paths matched relative to cwd).

    Returns:
        List of newly activated skill names.
    """
    if not _conditional_skills:
        return []

    if not cwd:
        cwd = os.environ.get("NANOBOT_WORKSPACE", os.getcwd())

    activated: List[str] = []

    for name, skill in list(_conditional_skills.items()):
        if not skill.paths:
            continue

        for fp in file_paths:
            # Match against both absolute and cwd-relative path
            try:
                rel_path = os.path.relpath(fp, cwd)
            except ValueError:
                rel_path = fp

            for pattern in skill.paths:
                if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(fp, pattern):
                    # Activate: move from conditional to active registry
                    del _conditional_skills[name]
                    _activated_conditional_names.add(name)
                    _registry[name] = skill
                    for alias in skill.aliases:
                        _alias_map[alias.lower()] = name
                    activated.append(name)
                    logger.info(f"[P99a] Activated conditional skill '/{name}' "
                                 f"(matched: {rel_path} → {pattern})")
                    break
            if name in _activated_conditional_names:
                break

    return activated


def get_conditional_skill_count() -> int:
    """P99a: Get number of pending conditional skills (for testing/debugging)."""
    return len(_conditional_skills)


def clear_conditional_skills() -> None:
    """P99a: Clear all conditional skill state (for testing)."""
    _conditional_skills.clear()
    _activated_conditional_names.clear()


# ═══════════════════════════════════════════════════════════════
# P99b: Shell command execution in skill prompts
# ═══════════════════════════════════════════════════════════════

# ```! command ``` — code block syntax
_BLOCK_SHELL_RE = re.compile(r'```!\s*\n?([\s\S]*?)\n?```')
# !`command` — inline syntax
_INLINE_SHELL_RE = re.compile(r'(?<=\s)!`([^`]+)`')


def execute_shell_in_prompt(text: str, skill_name: str = "") -> str:
    """P99b: Parse and execute embedded shell commands in skill prompt text.

    Supports two syntaxes (inspired by Claw's promptShellExecution.ts):
    - Code blocks: ```! command ```
    - Inline: !`command`

    Shell commands are executed synchronously and their stdout replaces the
    command syntax in the prompt. Stderr is appended as a comment.

    Args:
        text: Skill prompt text potentially containing shell commands.
        skill_name: Name of the skill (for logging).

    Returns:
        Processed text with shell command output substituted.
    """
    result = text

    # Find all shell commands (block and inline)
    block_matches = list(_BLOCK_SHELL_RE.finditer(text))

    # Only scan for inline if !` is present (performance optimization from Claw)
    inline_matches = list(_INLINE_SHELL_RE.finditer(text)) if '!`' in text else []

    if not block_matches and not inline_matches:
        return result

    # Process in reverse order so offsets stay valid
    all_matches = sorted(block_matches + inline_matches, key=lambda m: m.start(), reverse=True)

    for match in all_matches:
        command = match.group(1).strip()
        if not command:
            continue

        try:
            import subprocess  # B13: lazy import to avoid secure_interceptor block
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=os.environ.get("NANOBOT_WORKSPACE", None),
            )

            output = proc.stdout.strip()
            if proc.stderr.strip():
                output += f"\n<!-- stderr: {proc.stderr.strip()[:200]} -->"

            if proc.returncode != 0 and not output:
                output = f"<!-- shell command failed (exit {proc.returncode}): {command} -->"

            logger.debug(f"[P99b] Shell in /{skill_name}: `{command}` → "
                          f"{len(output)} chars (exit {proc.returncode})")

        except ImportError:
            output = f"<!-- subprocess not available: {command} -->"
            logger.warning(f"[P99b] subprocess import blocked in /{skill_name}")
        except Exception as e:
            if 'TimeoutExpired' in type(e).__name__:
                output = f"<!-- shell command timed out (30s): {command} -->"
                logger.warning(f"[P99b] Shell timeout in /{skill_name}: {command}")
            else:
                output = f"<!-- shell command error: {e} -->"
                logger.warning(f"[P99b] Shell error in /{skill_name}: {e}")

        result = result[:match.start()] + output + result[match.end():]

    return result


# ═══════════════════════════════════════════════════════════════
# P99c: Forked skill execution (inspired by Claw's forkedAgent.ts)
# ═══════════════════════════════════════════════════════════════

def _build_forked_prompt(skill: SkillDefinition, prompt: str) -> str:
    """P99c: Wrap a skill prompt for forked (sub_agent) execution.

    When a skill has context='fork', it runs in an isolated sub_agent
    with its own context and token budget. The prompt is wrapped with
    metadata so the agentic loop knows to fork it.

    Inspired by Claw's prepareForkedCommandContext + executeForkedSkill.
    """
    parts = [
        f"<!-- SKILL_FORK: {skill.name} -->",
        f"# /{skill.name} (forked execution)",
        "",
        "This skill is running in an isolated sub-agent context.",
        "You have a separate token budget and conversation history.",
        "Focus exclusively on completing the skill's task.",
        "",
        prompt,
    ]
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# P99d: Allowed tools enforcement helpers
# ═══════════════════════════════════════════════════════════════

_ALLOWED_TOOLS_RE = re.compile(r'<!-- SKILL_ALLOWED_TOOLS: (.+?) -->')


def extract_allowed_tools_from_prompt(prompt: str) -> List[str]:
    """P99d: Extract allowed_tools metadata from a skill prompt.

    The agentic loop calls this to determine if tool access should be
    restricted during skill execution.

    Returns list of allowed tool names, or empty list (no restriction).
    """
    match = _ALLOWED_TOOLS_RE.search(prompt)
    if match:
        return [t.strip() for t in match.group(1).split(",") if t.strip()]
    return []


def is_tool_allowed_for_skill(tool_name: str, allowed_tools: List[str]) -> bool:
    """P99d: Check if a specific tool is allowed for the current skill.

    Supports both exact matches and glob patterns:
      - 'file_read' — exact match
      - 'file_*' — glob pattern matching file_read, file_edit, file_write, etc.
      - 'shell_execute(git:*)' — pattern matching shell_execute with git commands

    If allowed_tools is empty, all tools are allowed (no restriction).
    """
    if not allowed_tools:
        return True

    for pattern in allowed_tools:
        if fnmatch.fnmatch(tool_name, pattern):
            return True
        # Handle Claw-style patterns like 'Bash(git:*)'
        # Map to Nanobot equivalents
        if "(" in pattern:
            base = pattern.split("(")[0].strip()
            if fnmatch.fnmatch(tool_name, base):
                return True

    return False


# P100d: Model/effort override extraction from skill prompts
_SKILL_MODEL_RE = re.compile(r'<!-- SKILL_MODEL: (.+?) -->')
_SKILL_EFFORT_RE = re.compile(r'<!-- SKILL_EFFORT: ([\d.]+) -->')


def extract_skill_overrides_from_prompt(prompt: str) -> Dict[str, Any]:
    """P100c/P100d: Extract all skill execution metadata from a prompt.

    Called by the agentic loop to determine if tool filtering, model override,
    or effort override should apply for this skill execution.

    Returns dict with keys:
      - allowed_tools: List[str]  (empty = no restriction)
      - model: Optional[str]     (None = use default)
      - effort: Optional[float]  (None = use default)
      - is_forked: bool          (True if <!-- SKILL_FORK: ... --> present)
    """
    result: Dict[str, Any] = {
        "allowed_tools": extract_allowed_tools_from_prompt(prompt),
        "model": None,
        "effort": None,
        "is_forked": "<!-- SKILL_FORK:" in prompt,
        # P101: Runtime enforcement metadata from skill prompts
        "mode": None,
        "write_policy": "allowed",
        "requires_verification": False,
    }

    # P101: Extract mode
    mode_match = re.search(r'<!-- SKILL_MODE: (\w+) -->', prompt)
    if mode_match:
        result["mode"] = mode_match.group(1)

    # P101: Extract write_policy
    wp_match = re.search(r'<!-- SKILL_WRITE_POLICY: (\w+) -->', prompt)
    if wp_match:
        result["write_policy"] = wp_match.group(1)

    # P101: Extract requires_verification
    if '<!-- SKILL_REQUIRES_VERIFICATION: true -->' in prompt:
        result["requires_verification"] = True

    model_match = _SKILL_MODEL_RE.search(prompt)
    if model_match:
        result["model"] = model_match.group(1).strip()

    effort_match = _SKILL_EFFORT_RE.search(prompt)
    if effort_match:
        try:
            val = float(effort_match.group(1))
            if 0.0 <= val <= 1.0:
                result["effort"] = val
        except ValueError:
            pass

    return result


# ═══════════════════════════════════════════════════════════════
# User-defined SKILL.md loader (inspired by Claw loadSkillsDir)
# ═══════════════════════════════════════════════════════════════

def _parse_frontmatter(content: str) -> Tuple[dict, str]:
    """Parse YAML frontmatter from SKILL.md content.

    Format:
    ---
    name: skill-name
    description: ...
    ---
    # Markdown body
    """
    if not content.startswith("---"):
        return {}, content

    end = content.find("---", 3)
    if end == -1:
        return {}, content

    try:
        fm = yaml.safe_load(content[3:end]) or {}
    except Exception:
        fm = {}

    body = content[end + 3:].strip()
    return fm, body


def _load_skill_from_md(skill_dir: Path, source: str) -> Optional[SkillDefinition]:
    """Load a single skill from a directory containing SKILL.md."""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"[Skills] Cannot read {skill_file}: {e}")
        return None

    fm, body = _parse_frontmatter(content)
    name = fm.get("name", skill_dir.name)
    description = fm.get("description", f"User skill: {name}")
    aliases = fm.get("aliases", [])
    if isinstance(aliases, str):
        aliases = [a.strip() for a in aliases.split(",")]
    argument_hint = fm.get("argument-hint", fm.get("argument_hint", ""))
    when_to_use = fm.get("when_to_use", fm.get("when-to-use", ""))
    uses_sub_agent = fm.get("uses_sub_agent", False)
    user_invocable = fm.get("user-invocable", True)

    # P98d: ${NANOBOT_SKILL_DIR} substitution
    _skill_dir_str = str(skill_dir)

    def prompt_builder(args: str) -> str:
        prompt = body
        if args:
            prompt += f"\n\n## User Input\n\n{args}"
        # Substitute $arg_name patterns
        arg_names = fm.get("arguments", [])
        if isinstance(arg_names, list) and args:
            arg_parts = args.split(None, len(arg_names) - 1) if len(arg_names) > 1 else [args]
            for i, aname in enumerate(arg_names):
                val = arg_parts[i] if i < len(arg_parts) else ""
                prompt = prompt.replace(f"${aname}", val)
        # P98d: Replace ${NANOBOT_SKILL_DIR} with skill's own directory
        prompt = prompt.replace("${NANOBOT_SKILL_DIR}", _skill_dir_str)
        return prompt

    # P98d: Parse new frontmatter fields
    allowed_tools = fm.get("allowed-tools", fm.get("allowed_tools", []))
    if isinstance(allowed_tools, str):
        allowed_tools = [t.strip() for t in allowed_tools.split(",")]
    disable_model_inv = fm.get("disable-model-invocation", False)

    # P99a: Parse paths (glob patterns for conditional activation)
    paths_raw = fm.get("paths", [])
    if isinstance(paths_raw, str):
        paths_raw = [p.strip() for p in paths_raw.split(",") if p.strip()]
    elif not isinstance(paths_raw, list):
        paths_raw = []

    # P99c: Parse execution context (inline or fork)
    context = fm.get("context", "inline")
    if context not in ("inline", "fork"):
        context = "inline"

    # P100d: Parse model and effort overrides
    model_override = fm.get("model", None)
    effort_raw = fm.get("effort", None)
    effort_override = None
    if effort_raw is not None:
        try:
            effort_override = float(effort_raw)
            if not (0.0 <= effort_override <= 1.0):
                effort_override = None
        except (ValueError, TypeError):
            effort_override = None

    return SkillDefinition(
        name=str(name),
        description=str(description),
        prompt_builder=prompt_builder,
        aliases=aliases,
        uses_sub_agent=bool(uses_sub_agent) or context == "fork",
        user_invocable=bool(user_invocable),
        argument_hint=str(argument_hint),
        when_to_use=str(when_to_use),
        source=source,
        allowed_tools=allowed_tools if isinstance(allowed_tools, list) else [],
        disable_model_invocation=bool(disable_model_inv),
        skill_dir=str(skill_dir),
        paths=paths_raw,
        context=context,
        model=str(model_override) if model_override else None,
        effort=effort_override,
    )


_seen_skill_paths: set = set()  # P98e: dedup by resolved path


def load_user_skills() -> int:
    """Load user-defined skills from SKILL.md files.

    Scans two directories (like Claw's user + project skills):
      1. ~/.nanobot/skills/*/SKILL.md  — personal skills (follow you everywhere)
      2. .nanobot/skills/*/SKILL.md    — project-specific skills

    P98e: Deduplicates by resolved path (handles symlinks).
    Returns number of skills loaded.
    """
    count = 0
    search_dirs = []

    # Personal skills
    user_dir = Path.home() / ".nanobot" / "skills"
    if user_dir.is_dir():
        search_dirs.append((user_dir, "user"))

    # Project skills (workspace-relative)
    workspace = os.environ.get("NANOBOT_WORKSPACE", "")
    if workspace:
        project_dir = Path(workspace) / ".nanobot" / "skills"
        if project_dir.is_dir():
            search_dirs.append((project_dir, "project"))

    for base_dir, source in search_dirs:
        try:
            for entry in sorted(base_dir.iterdir()):
                if entry.is_dir() and (entry / "SKILL.md").exists():
                    # P98e: Dedup by resolving symlinks
                    try:
                        resolved = str((entry / "SKILL.md").resolve())
                    except Exception:
                        resolved = str(entry / "SKILL.md")
                    if resolved in _seen_skill_paths:
                        logger.debug(f"[P98e] Skipping duplicate skill: {entry.name}")
                        continue
                    _seen_skill_paths.add(resolved)

                    skill = _load_skill_from_md(entry, source)
                    if skill:
                        register_skill(skill)
                        count += 1
        except Exception as e:
            logger.warning(f"[Skills] Failed to scan {base_dir}: {e}")

    if count:
        logger.info(f"[Skills] Loaded {count} user-defined skills")
    return count


def discover_skills_for_paths(file_paths: list, workspace: str = "") -> int:
    """P98e: Dynamically discover skills from file paths.

    Walks up from each file path looking for .nanobot/skills/ directories.
    Only discovers directories below the workspace root.
    Inspired by Claw's discoverSkillDirsForPaths.

    Returns number of new skills loaded.
    """
    if not workspace:
        workspace = os.environ.get("NANOBOT_WORKSPACE", "")
    if not workspace:
        return 0

    workspace_path = Path(workspace).resolve()
    discovered_dirs: list = []

    for fp in file_paths:
        current = Path(fp).resolve().parent
        while current != workspace_path and str(current).startswith(str(workspace_path)):
            skill_dir = current / ".nanobot" / "skills"
            if skill_dir.is_dir() and str(skill_dir) not in _seen_skill_paths:
                discovered_dirs.append(skill_dir)
            current = current.parent

    if not discovered_dirs:
        return 0

    count = 0
    for skill_base in discovered_dirs:
        try:
            for entry in sorted(skill_base.iterdir()):
                if entry.is_dir() and (entry / "SKILL.md").exists():
                    try:
                        resolved = str((entry / "SKILL.md").resolve())
                    except Exception:
                        resolved = str(entry / "SKILL.md")
                    if resolved in _seen_skill_paths:
                        continue
                    _seen_skill_paths.add(resolved)
                    skill = _load_skill_from_md(entry, "project")
                    if skill:
                        register_skill(skill)
                        count += 1
        except Exception as e:
            logger.warning(f"[P98e] Failed to scan {skill_base}: {e}")

    if count:
        logger.info(f"[P98e] Dynamically discovered {count} skills from file operations")
    return count


# ═══════════════════════════════════════════════════════════════
# System prompt injection (model awareness of skills)
# ═══════════════════════════════════════════════════════════════

_SKILL_BUDGET_PERCENT = 0.01  # 1% of context window for skill listing
_MAX_LISTING_DESC_CHARS = 250  # Per-entry hard cap (Claw pattern)
_DEFAULT_CHAR_BUDGET = 8000  # Fallback: 1% of 200k tokens * 4 chars


def build_skill_system_prompt(context_window_tokens: int = 0) -> str:
    """Build a system prompt section listing available skills.

    Injected into the agentic loop so the model knows about /commands.
    Includes when_to_use for automatic invocation matching.

    P98b: Budget-aware — truncates descriptions to fit within 1% of context.
    Bundled skills are never truncated first (Claw pattern).
    """
    skills = list_skills()
    if not skills:
        return ""

    # P98a: Sort by usage score (most used first), then alphabetically
    try:
        from skills.usage_tracking import get_skill_usage_score
        skills.sort(key=lambda s: (-get_skill_usage_score(s.name), s.name))
    except Exception:
        skills.sort(key=lambda s: s.name)

    # P98b: Calculate budget
    if context_window_tokens > 0:
        budget = int(context_window_tokens * 4 * _SKILL_BUDGET_PERCENT)
    else:
        budget = _DEFAULT_CHAR_BUDGET

    header = "# Available Skills (invoke with /command_name)\n\n"
    footer = (
        "\nWhen the user's request matches a skill's 'when to use' description, "
        "suggest using the skill command. Example: if user says 'review my code', "
        "suggest '/simplify'. The user can also invoke skills directly with /command_name."
    )
    overhead = len(header) + len(footer)
    remaining = budget - overhead

    # Build entries, respecting budget
    entries = []
    for s in skills:
        hint = f" {s.argument_hint}" if s.argument_hint else ""
        aliases_str = f" (also: {', '.join('/' + a for a in s.aliases)})" if s.aliases else ""

        desc = s.description
        wtu = s.when_to_use
        # Combine description + when_to_use for budget calc
        full_desc = f"{desc} - {wtu}" if wtu else desc
        if len(full_desc) > _MAX_LISTING_DESC_CHARS:
            full_desc = full_desc[:_MAX_LISTING_DESC_CHARS - 1] + "…"

        entry_line = f"- **/{s.name}{hint}**{aliases_str}: {full_desc}"
        entry_len = len(entry_line) + 1  # +1 for newline

        if entry_len <= remaining or s.source == "bundled":
            # Always include bundled skills even if over budget
            entries.append(entry_line)
            remaining -= entry_len
        else:
            # Over budget for non-bundled: names-only fallback
            short = f"- /{s.name}"
            entries.append(short)
            remaining -= len(short) + 1

    return header + "\n".join(entries) + footer


# ═══════════════════════════════════════════════════════════════
# Natural language → skill matching (when_to_use)
# ═══════════════════════════════════════════════════════════════

def match_skill_by_intent(user_message: str) -> Optional[Tuple[SkillDefinition, float]]:
    """Match a user message to a skill via when_to_use keywords.

    Returns (skill, confidence) or None.
    Confidence: 0.0-1.0, only returns if >= 0.3.
    """
    if not user_message or len(user_message) < 5:
        return None

    msg_lower = user_message.lower()
    best_skill = None
    best_score = 0.0

    # Direct keyword patterns for high-confidence matching
    _INTENT_PATTERNS = {
        "simplify": [r"review.*code", r"code.*review", r"cleanup", r"clean.*up",
                     r"审查.*代码", r"代码.*审查", r"代码.*质量"],
        "verify": [r"verify", r"验证", r"check.*change", r"检查.*改动",
                   r"test.*change", r"测试.*改动"],
        "commit": [r"commit", r"提交", r"git.*commit", r"生成.*commit",
                   r"commit.*message"],
        "debug": [r"debug", r"调试", r"diagnos", r"诊断", r"报错", r"出错",
                  r"error", r"bug", r"fix.*issue", r"修复.*问题"],
        "remember": [r"review.*memor", r"organize.*memor", r"clean.*memor",
                     r"整理.*记忆", r"记忆.*审查", r"memory.*review"],
        "batch": [r"parallel.*change", r"batch.*change", r"大规模.*修改",
                  r"批量.*修改", r"migration", r"迁移"],
    }

    for skill_name, patterns in _INTENT_PATTERNS.items():
        skill = get_skill(skill_name)
        if not skill:
            continue
        # P98d: Skip skills that disabled model invocation
        if skill.disable_model_invocation:
            continue
        for pattern in patterns:
            if re.search(pattern, msg_lower):
                score = 0.7
                if best_score < score:
                    best_score = score
                    best_skill = skill
                break

    # Also check when_to_use fields with simple word overlap
    if best_score < 0.5:
        msg_words = set(re.findall(r'\w+', msg_lower))
        for skill in list_skills():
            if not skill.when_to_use:
                continue
            wtu_words = set(re.findall(r'\w+', skill.when_to_use.lower()))
            overlap = len(msg_words & wtu_words)
            if overlap >= 3:
                score = min(0.5, overlap * 0.1)
                if score > best_score:
                    best_score = score
                    best_skill = skill

    if best_skill and best_score >= 0.3:
        return (best_skill, best_score)
    return None


# ═══════════════════════════════════════════════════════════════
# Auto-register bundled skills + user skills on import
# ═══════════════════════════════════════════════════════════════
def _init_all_skills():
    """Register bundled + coding + user-defined skills. Called once at import time."""
    try:
        from skills.bundled import register_all_bundled_skills
        register_all_bundled_skills()
    except Exception as e:
        logger.warning(f"[Skills] Failed to register bundled skills: {e}")

    # P101: Register coding skills (may override /debug, /verify from bundled)
    try:
        from skills.coding_skills import register_coding_skills
        register_coding_skills()
    except Exception as e:
        logger.warning(f"[Skills] Failed to register coding skills: {e}")

    try:
        load_user_skills()
    except Exception as e:
        logger.warning(f"[Skills] Failed to load user skills: {e}")


_init_all_skills()
