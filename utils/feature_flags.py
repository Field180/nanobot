"""
Feature Flag Runtime Switch (inspired by Claw growthbook.ts + Bun feature())
=============================================================================
Provides a centralized, type-safe feature flag registry that replaces scattered
`env.get("NANOBOT_*")` calls with a single source of truth.

Supports:
  - Boolean flags (on/off)
  - Numeric flags (thresholds, limits)
  - String flags (model names, modes)
  - Runtime override via env vars (NANOBOT_FF_<FLAG_NAME>=value)
  - Hot-reload from config file without restart
  - Default values baked into the registry

Usage:
    from utils.feature_flags import ff

    if ff.is_enabled("auto_compact"):
        ...
    max_retries = ff.get_int("max_retries")
    model = ff.get_str("default_model")

    # Override at runtime via env:
    #   NANOBOT_FF_AUTO_COMPACT=false
    #   NANOBOT_FF_MAX_RETRIES=10

Thread/async safety: reads are lock-free (dict lookup). Writes (reload) are
atomic dict replacement. Same single-thread asyncio contract as server_state.py.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

# ── Flag type constants ──────────────────────────────────────
BOOL = "bool"
INT = "int"
FLOAT = "float"
STR = "str"


class _FlagDef:
    """Internal flag definition."""
    __slots__ = ("name", "type", "default", "description")

    def __init__(self, name: str, type_: str, default: Any, description: str = ""):
        self.name = name
        self.type = type_
        self.default = default
        self.description = description


# ══════════════════════════════════════════════════════════════
# Flag Registry — single source of truth for all feature flags
# ══════════════════════════════════════════════════════════════
# Add new flags here. Convention: snake_case name, NANOBOT_FF_<UPPER> env override.
_REGISTRY: list[_FlagDef] = [
    # ── Agentic loop ─────────────────────────────────────────
    _FlagDef("auto_compact", BOOL, True,
             "Enable automatic context compaction when token budget exceeded"),

    # ── Error handling / retry ───────────────────────────────
    _FlagDef("transient_retry", BOOL, True,
             "Enable exponential backoff retry for transient API errors"),
    _FlagDef("max_retries", INT, 5,
             "Maximum retry attempts for transient API errors"),
    _FlagDef("reactive_compact", BOOL, True,
             "Enable P10 reactive compaction on context overflow"),
]


class FeatureFlags:
    """Centralized feature flag manager.

    Resolution order (highest priority first):
      1. Environment variable: NANOBOT_FF_<UPPER_NAME>=value
      2. Config file: ~/.nanobot/feature_flags.json
      3. Registry default

    Mirrors Claw's getFeatureValue_CACHED_MAY_BE_STALE pattern:
    reads are instant (dict lookup), no network calls.
    """

    def __init__(self) -> None:
        self._defs: Dict[str, _FlagDef] = {d.name: d for d in _REGISTRY}
        self._overrides: Dict[str, Any] = {}
        self._config_path = Path.home() / ".nanobot" / "feature_flags.json"
        self._load_config_file()

    # ── Public API ───────────────────────────────────────────

    def is_enabled(self, name: str) -> bool:
        """Check if a boolean flag is enabled."""
        val = self._resolve(name)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes", "on")
        return bool(val)

    def get_int(self, name: str) -> int:
        """Get an integer flag value."""
        val = self._resolve(name)
        try:
            return int(val)
        except (ValueError, TypeError):
            return self._defs[name].default if name in self._defs else 0

    def get_float(self, name: str) -> float:
        """Get a float flag value."""
        val = self._resolve(name)
        try:
            return float(val)
        except (ValueError, TypeError):
            return self._defs[name].default if name in self._defs else 0.0

    def get_str(self, name: str) -> str:
        """Get a string flag value."""
        val = self._resolve(name)
        return str(val) if val is not None else ""

    def get(self, name: str, default: Any = None) -> Any:
        """Get raw flag value with optional default."""
        if name not in self._defs and default is not None:
            return self._resolve_raw(name, default)
        return self._resolve(name)

    def set_override(self, name: str, value: Any) -> None:
        """Set a runtime override (for testing or dynamic changes)."""
        self._overrides[name] = value

    def clear_override(self, name: str) -> None:
        """Remove a runtime override."""
        self._overrides.pop(name, None)

    def clear_all_overrides(self) -> None:
        """Remove all runtime overrides."""
        self._overrides.clear()

    def reload(self) -> None:
        """Hot-reload config file without restart."""
        self._load_config_file()
        logger.info("[FeatureFlags] Config reloaded from %s", self._config_path)

    # ── Introspection ────────────────────────────────────────

    def list_flags(self) -> list[dict]:
        """Return all registered flags with current resolved values."""
        result = []
        for d in _REGISTRY:
            result.append({
                "name": d.name,
                "type": d.type,
                "default": d.default,
                "current": self._resolve(d.name),
                "source": self._get_source(d.name),
                "description": d.description,
            })
        return result

    def get_flag_info(self, name: str) -> Optional[dict]:
        """Get detailed info for a single flag."""
        d = self._defs.get(name)
        if not d:
            return None
        return {
            "name": d.name,
            "type": d.type,
            "default": d.default,
            "current": self._resolve(d.name),
            "source": self._get_source(d.name),
            "description": d.description,
        }

    # ── Internal resolution ──────────────────────────────────

    def _resolve(self, name: str) -> Any:
        """Resolve flag value with priority: override > env > config > default."""
        # 1. Runtime override (set_override)
        if name in self._overrides:
            return self._overrides[name]

        # 2. Environment variable: NANOBOT_FF_<UPPER>
        env_key = f"NANOBOT_FF_{name.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return self._coerce(name, env_val)

        # 3. Config file
        if name in self._config_values:
            return self._config_values[name]

        # 4. Registry default
        if name in self._defs:
            return self._defs[name].default

        return None

    def _resolve_raw(self, name: str, default: Any) -> Any:
        """Resolve with caller-provided default for unregistered flags."""
        val = self._resolve(name)
        return val if val is not None else default

    def _coerce(self, name: str, raw: str) -> Any:
        """Coerce string env value to the flag's registered type."""
        d = self._defs.get(name)
        if not d:
            return raw

        if d.type == BOOL:
            return raw.lower() in ("true", "1", "yes", "on")
        if d.type == INT:
            try:
                return int(raw)
            except ValueError:
                return d.default
        if d.type == FLOAT:
            try:
                return float(raw)
            except ValueError:
                return d.default
        return raw  # STR

    def _get_source(self, name: str) -> str:
        """Determine where the current value comes from."""
        if name in self._overrides:
            return "override"
        env_key = f"NANOBOT_FF_{name.upper()}"
        if os.environ.get(env_key) is not None:
            return "env"
        if name in self._config_values:
            return "config"
        return "default"

    def _load_config_file(self) -> None:
        """Load feature flags from JSON config file."""
        self._config_values: Dict[str, Any] = {}
        if self._config_path.is_file():
            try:
                raw = self._config_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    self._config_values = data
                    logger.debug("[FeatureFlags] Loaded %d flags from %s",
                                 len(data), self._config_path)
                    unknown = set(data.keys()) - set(self._defs.keys())
                    if unknown:
                        logger.warning(
                            "[FeatureFlags] Config contains %d unregistered flag(s) "
                            "that will be IGNORED: %s. Remove them from %s or "
                            "register them in _REGISTRY.",
                            len(unknown), sorted(unknown), self._config_path,
                        )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("[FeatureFlags] Failed to load %s: %s",
                               self._config_path, e)


# ── Singleton instance ───────────────────────────────────────
ff = FeatureFlags()
