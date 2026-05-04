"""
P8: Tests for tools/tool_schema.py — Pydantic tool definitions.
"""
import sys
import unittest
from pathlib import Path

_WEB_UI = Path(__file__).resolve().parent.parent
if str(_WEB_UI) not in sys.path:
    sys.path.insert(0, str(_WEB_UI))

from tools.tool_schema import ToolDef, ToolParam, ToolRegistry, wrap_existing_tools


class TestToolParam(unittest.TestCase):
    """Test ToolParam model."""

    def test_basic_creation(self):
        p = ToolParam(name="path", type="string", description="File path", required=True)
        self.assertEqual(p.name, "path")
        self.assertEqual(p.type, "string")
        self.assertTrue(p.required)

    def test_to_property_schema(self):
        p = ToolParam(name="query", type="string", description="Search query", required=True)
        schema = p.to_property_schema()
        self.assertEqual(schema, {"type": "string", "description": "Search query"})

    def test_enum_param(self):
        p = ToolParam(name="action", type="string", enum=["read", "write", "delete"])
        schema = p.to_property_schema()
        self.assertEqual(schema["enum"], ["read", "write", "delete"])

    def test_array_param(self):
        p = ToolParam(name="tags", type="array", items={"type": "string"})
        schema = p.to_property_schema()
        self.assertEqual(schema["type"], "array")
        self.assertEqual(schema["items"], {"type": "string"})

    def test_default_value(self):
        p = ToolParam(name="limit", type="integer", default=10)
        schema = p.to_property_schema()
        self.assertEqual(schema["default"], 10)

    def test_no_description(self):
        p = ToolParam(name="x", type="number")
        schema = p.to_property_schema()
        self.assertNotIn("description", schema)


class TestToolDef(unittest.TestCase):
    """Test ToolDef model."""

    def test_basic_creation(self):
        td = ToolDef(
            name="test_tool",
            description="A test tool",
            parameters=[
                ToolParam(name="query", type="string", description="Search", required=True),
                ToolParam(name="limit", type="integer", description="Max results"),
            ],
            is_readonly=True,
        )
        self.assertEqual(td.name, "test_tool")
        self.assertTrue(td.is_readonly)
        self.assertEqual(len(td.parameters), 2)

    def test_to_openai_schema(self):
        td = ToolDef(
            name="file_read",
            description="Read a file",
            parameters=[
                ToolParam(name="path", type="string", description="File path", required=True),
                ToolParam(name="offset", type="integer", description="Start line"),
            ],
        )
        schema = td.to_openai_schema()
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "file_read")
        self.assertEqual(schema["function"]["description"], "Read a file")
        self.assertIn("path", schema["function"]["parameters"]["properties"])
        self.assertEqual(schema["function"]["parameters"]["required"], ["path"])

    def test_no_required_params(self):
        td = ToolDef(
            name="list_all",
            description="List everything",
            parameters=[
                ToolParam(name="filter", type="string"),
            ],
        )
        schema = td.to_openai_schema()
        self.assertNotIn("required", schema["function"]["parameters"])

    def test_from_openai_schema(self):
        original = {
            "type": "function",
            "function": {
                "name": "grep_search",
                "description": "Search files",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Pattern"},
                        "path": {"type": "string", "description": "Dir"},
                    },
                    "required": ["pattern", "path"],
                },
            },
        }
        td = ToolDef.from_openai_schema(original, is_readonly=True)
        self.assertEqual(td.name, "grep_search")
        self.assertTrue(td.is_readonly)
        self.assertEqual(len(td.parameters), 2)
        self.assertTrue(td.get_param("pattern").required)
        self.assertTrue(td.get_param("path").required)

    def test_roundtrip(self):
        td = ToolDef(
            name="example",
            description="Example tool",
            parameters=[
                ToolParam(name="input", type="string", description="Input data", required=True),
                ToolParam(name="verbose", type="boolean", description="Verbose mode"),
            ],
            is_readonly=True,
            aliases=["ex", "example_alias"],
        )
        schema = td.to_openai_schema()
        td2 = ToolDef.from_openai_schema(schema, is_readonly=True, aliases=["ex"])
        self.assertEqual(td2.name, "example")
        self.assertEqual(td2.get_required_params(), ["input"])

    def test_get_param(self):
        td = ToolDef(
            name="t",
            description="t",
            parameters=[ToolParam(name="a", type="string"), ToolParam(name="b", type="integer")],
        )
        self.assertEqual(td.get_param("a").type, "string")
        self.assertEqual(td.get_param("b").type, "integer")
        self.assertIsNone(td.get_param("c"))

    def test_get_required_params(self):
        td = ToolDef(
            name="t",
            description="t",
            parameters=[
                ToolParam(name="a", type="string", required=True),
                ToolParam(name="b", type="string", required=False),
                ToolParam(name="c", type="string", required=True),
            ],
        )
        self.assertEqual(td.get_required_params(), ["a", "c"])

    def test_summary_line(self):
        td = ToolDef(
            name="grep_search",
            description="Search for patterns in files",
            parameters=[
                ToolParam(name="pattern", type="string", required=True),
                ToolParam(name="path", type="string", required=True),
            ],
            is_readonly=True,
        )
        summary = td.summary_line()
        self.assertIn("grep_search", summary)
        self.assertIn("pattern, path", summary)
        self.assertIn("[readonly]", summary)


class TestToolRegistry(unittest.TestCase):
    """Test ToolRegistry collection."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.tool1 = ToolDef(name="tool_a", description="A", is_readonly=True, aliases=["ta"])
        self.tool2 = ToolDef(name="tool_b", description="B", aliases=["tb"])
        self.registry.register(self.tool1)
        self.registry.register(self.tool2)

    def test_len(self):
        self.assertEqual(len(self.registry), 2)

    def test_get_by_name(self):
        self.assertEqual(self.registry.get("tool_a").name, "tool_a")

    def test_get_by_alias(self):
        self.assertEqual(self.registry.get("ta").name, "tool_a")
        self.assertEqual(self.registry.get("tb").name, "tool_b")

    def test_get_unknown(self):
        self.assertIsNone(self.registry.get("unknown"))

    def test_contains(self):
        self.assertIn("tool_a", self.registry)
        self.assertIn("ta", self.registry)
        self.assertNotIn("tool_c", self.registry)

    def test_readonly_tools(self):
        self.assertEqual(self.registry.readonly_tools, frozenset(["tool_a"]))

    def test_to_openai_schemas(self):
        schemas = self.registry.to_openai_schemas()
        self.assertEqual(len(schemas), 2)
        self.assertEqual(schemas[0]["function"]["name"], "tool_a")

    def test_all_tools(self):
        tools = self.registry.all_tools()
        self.assertEqual(len(tools), 2)
        self.assertIsInstance(tools[0], ToolDef)


class TestWrapExistingTools(unittest.TestCase):
    """Test wrapping actual tool modules into typed registry."""

    def test_wrap_all_modules(self):
        from tools import _ALL_TOOL_MODULES
        registry = wrap_existing_tools(_ALL_TOOL_MODULES)
        self.assertEqual(len(registry), len(_ALL_TOOL_MODULES))

    def test_file_read_wrapped(self):
        from tools import _ALL_TOOL_MODULES
        registry = wrap_existing_tools(_ALL_TOOL_MODULES)
        td = registry.get("file_read")
        self.assertIsNotNone(td)
        self.assertTrue(td.is_readonly)
        self.assertIn("path", td.get_required_params())
        self.assertEqual(td.aliases, ["read_file"])

    def test_shell_execute_wrapped(self):
        from tools import _ALL_TOOL_MODULES
        registry = wrap_existing_tools(_ALL_TOOL_MODULES)
        td = registry.get("shell_execute")
        self.assertIsNotNone(td)
        self.assertFalse(td.is_readonly)
        self.assertIn("command", td.get_required_params())

    def test_roundtrip_all_schemas(self):
        """Verify all tools can roundtrip through typed schema."""
        from tools import _ALL_TOOL_MODULES
        registry = wrap_existing_tools(_ALL_TOOL_MODULES)
        for td in registry.all_tools():
            schema = td.to_openai_schema()
            self.assertEqual(schema["type"], "function")
            self.assertEqual(schema["function"]["name"], td.name)
            self.assertIn("parameters", schema["function"])

    def test_readonly_matches_original(self):
        from tools import _ALL_TOOL_MODULES, READONLY_TOOLS
        registry = wrap_existing_tools(_ALL_TOOL_MODULES)
        self.assertEqual(registry.readonly_tools, READONLY_TOOLS)

    def test_guidance_preserved(self):
        from tools import _ALL_TOOL_MODULES
        registry = wrap_existing_tools(_ALL_TOOL_MODULES)
        td = registry.get("file_read")
        self.assertIsNotNone(td.guidance)
        self.assertIn("replaces_shell", td.guidance)


class TestMypyConfig(unittest.TestCase):
    """Verify mypy.ini exists and is valid."""

    def test_config_exists(self):
        config = _WEB_UI / "mypy.ini"
        self.assertTrue(config.exists())

    def test_config_has_key_settings(self):
        config = (_WEB_UI / "mypy.ini").read_text()
        self.assertIn("python_version", config)
        self.assertIn("ignore_missing_imports = True", config)
        self.assertIn("tools/tool_schema.py", config)
        self.assertIn("task_store.py", config)


if __name__ == "__main__":
    unittest.main()
