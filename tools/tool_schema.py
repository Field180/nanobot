"""
P8: Typed Tool Schema using Pydantic.

Provides ToolParam and ToolDef models for defining tool schemas with full
type safety. ToolDef.to_openai_schema() generates the OpenAI function-calling
dict format, reducing manual dict maintenance.

Backward compatible: existing TOOL_DEF dicts still work. This module provides
a typed alternative and utilities for converting between formats.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field


class ToolParam(BaseModel):
    """A single parameter in a tool's schema."""

    name: str
    type: Literal["string", "integer", "number", "boolean", "array", "object"] = "string"
    description: str = ""
    required: bool = False
    enum: Optional[List[str]] = None
    default: Optional[Any] = None
    items: Optional[Dict[str, Any]] = None  # For array types

    def to_property_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI JSON Schema property format."""
        prop: Dict[str, Any] = {"type": self.type}
        if self.description:
            prop["description"] = self.description
        if self.enum:
            prop["enum"] = self.enum
        if self.default is not None:
            prop["default"] = self.default
        if self.items and self.type == "array":
            prop["items"] = self.items
        return prop


class ToolDef(BaseModel):
    """Typed definition for a Nanobot tool.

    Replaces the manually maintained TOOL_DEF dict with a validated model.
    """

    name: str
    description: str
    parameters: List[ToolParam] = Field(default_factory=list)
    is_readonly: bool = False
    aliases: List[str] = Field(default_factory=list)

    # Optional guidance metadata (P17)
    guidance: Optional[Dict[str, Any]] = None

    def to_openai_schema(self) -> Dict[str, Any]:
        """Generate OpenAI function-calling schema dict.

        Returns the standard format:
        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": { "type": "object", "properties": {...}, "required": [...] }
            }
        }
        """
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for param in self.parameters:
            properties[param.name] = param.to_property_schema()
            if param.required:
                required.append(param.name)

        params_schema: Dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            params_schema["required"] = required

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params_schema,
            },
        }

    @classmethod
    def from_openai_schema(cls, schema: Dict[str, Any], **kwargs: Any) -> "ToolDef":
        """Create a ToolDef from an existing OpenAI function-calling schema dict.

        This enables gradual migration: wrap existing TOOL_DEF dicts.
        """
        func = schema.get("function", schema)
        name = func["name"]
        description = func.get("description", "")

        params_schema = func.get("parameters", {})
        properties = params_schema.get("properties", {})
        required_names = set(params_schema.get("required", []))

        parameters: List[ToolParam] = []
        for param_name, prop in properties.items():
            parameters.append(ToolParam(
                name=param_name,
                type=prop.get("type", "string"),
                description=prop.get("description", ""),
                required=param_name in required_names,
                enum=prop.get("enum"),
                default=prop.get("default"),
                items=prop.get("items"),
            ))

        return cls(
            name=name,
            description=description,
            parameters=parameters,
            **kwargs,
        )

    def get_required_params(self) -> List[str]:
        """Return names of required parameters."""
        return [p.name for p in self.parameters if p.required]

    def get_param(self, name: str) -> Optional[ToolParam]:
        """Get a parameter by name."""
        for p in self.parameters:
            if p.name == name:
                return p
        return None

    def summary_line(self) -> str:
        """One-line summary: name(required_params) — description[:60]."""
        req = ", ".join(self.get_required_params())
        desc = self.description[:60]
        ro = " [readonly]" if self.is_readonly else ""
        return f"{self.name}({req}){ro} — {desc}"


# ═══════════════════════════════════════════════════════════════
# Registry helpers
# ═══════════════════════════════════════════════════════════════


class ToolRegistry:
    """Typed collection of ToolDefs with lookup and schema generation.

    Usage:
        registry = ToolRegistry()
        registry.register(my_tool_def)
        schemas = registry.to_openai_schemas()
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDef] = {}
        self._aliases: Dict[str, str] = {}

    def register(self, tool: ToolDef) -> None:
        """Register a tool definition."""
        self._tools[tool.name] = tool
        for alias in tool.aliases:
            self._aliases[alias] = tool.name

    def get(self, name: str) -> Optional[ToolDef]:
        """Get tool by name or alias."""
        canonical = self._aliases.get(name, name)
        return self._tools.get(canonical)

    def all_tools(self) -> List[ToolDef]:
        """Return all registered tools in insertion order."""
        return list(self._tools.values())

    def to_openai_schemas(self) -> List[Dict[str, Any]]:
        """Generate OpenAI schemas for all registered tools."""
        return [t.to_openai_schema() for t in self._tools.values()]

    @property
    def readonly_tools(self) -> frozenset:
        """Return frozenset of readonly tool names."""
        return frozenset(name for name, t in self._tools.items() if t.is_readonly)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        canonical = self._aliases.get(name, name)
        return canonical in self._tools


def wrap_existing_tools(modules: Sequence[Any]) -> ToolRegistry:
    """Wrap existing tool modules (with TOOL_DEF dicts) into a typed registry.

    This is a bridge for gradual migration — doesn't change module behavior.
    """
    registry = ToolRegistry()
    for mod in modules:
        if not hasattr(mod, "TOOL_DEF"):
            continue
        tool_def = ToolDef.from_openai_schema(
            mod.TOOL_DEF,
            is_readonly=getattr(mod, "IS_READONLY", False),
            aliases=getattr(mod, "ALIASES", []),
            guidance=getattr(mod, "GUIDANCE", None),
        )
        registry.register(tool_def)
    return registry
