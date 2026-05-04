"""Tests for code_intel diagnose action (P4-Lite).

Covers:
  - diagnose_file(): unused imports, long functions, bare excepts, deep nesting, shadowed builtins
  - format_diagnostics(): output formatting
  - execute() dispatch: diagnose action + backward compatibility
"""

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.code_intel import (
    diagnose_file,
    format_diagnostics,
    execute,
    extract_symbols,
    find_references,
    analyze_file,
    TOOL_DEF,
    _LONG_FUNCTION_THRESHOLD,
    _DEEP_NESTING_THRESHOLD,
    _SHADOWABLE_BUILTINS,
)


# ── diagnose_file unit tests ─────────────────────────────────

class TestDiagnoseUnusedImports(unittest.TestCase):

    def test_unused_import_detected(self):
        source = "import os\nx = 1\n"
        diags = diagnose_file(source)
        w001 = [d for d in diags if d["code"] == "W001"]
        self.assertEqual(len(w001), 1)
        self.assertIn("os", w001[0]["message"])

    def test_used_import_no_warning(self):
        source = "import os\nprint(os.getcwd())\n"
        diags = diagnose_file(source)
        w001 = [d for d in diags if d["code"] == "W001"]
        self.assertEqual(len(w001), 0)

    def test_used_via_attribute(self):
        source = "import json\njson.loads('{}')\n"
        diags = diagnose_file(source)
        w001 = [d for d in diags if d["code"] == "W001"]
        self.assertEqual(len(w001), 0)

    def test_aliased_import_unused(self):
        source = "import numpy as np\nx = 1\n"
        diags = diagnose_file(source)
        w001 = [d for d in diags if d["code"] == "W001"]
        self.assertEqual(len(w001), 1)
        self.assertIn("np", w001[0]["message"])

    def test_from_import_unused(self):
        source = "from os.path import join\nx = 1\n"
        diags = diagnose_file(source)
        w001 = [d for d in diags if d["code"] == "W001"]
        self.assertEqual(len(w001), 1)

    def test_star_import_ignored(self):
        source = "from os import *\nx = 1\n"
        diags = diagnose_file(source)
        w001 = [d for d in diags if d["code"] == "W001"]
        self.assertEqual(len(w001), 0)


class TestDiagnoseLongFunctions(unittest.TestCase):

    def test_short_function_no_warning(self):
        source = "def foo():\n    pass\n"
        diags = diagnose_file(source)
        i001 = [d for d in diags if d["code"] == "I001"]
        self.assertEqual(len(i001), 0)

    def test_long_function_detected(self):
        body = "\n".join(f"    x_{i} = {i}" for i in range(_LONG_FUNCTION_THRESHOLD + 5))
        source = f"def big_func():\n{body}\n"
        diags = diagnose_file(source)
        i001 = [d for d in diags if d["code"] == "I001"]
        self.assertEqual(len(i001), 1)
        self.assertIn("big_func", i001[0]["message"])

    def test_async_long_function(self):
        body = "\n".join(f"    x_{i} = {i}" for i in range(_LONG_FUNCTION_THRESHOLD + 5))
        source = f"async def big_async():\n{body}\n"
        diags = diagnose_file(source)
        i001 = [d for d in diags if d["code"] == "I001"]
        self.assertEqual(len(i001), 1)
        self.assertIn("big_async", i001[0]["message"])


class TestDiagnoseBareExcept(unittest.TestCase):

    def test_bare_except_detected(self):
        source = textwrap.dedent("""\
            try:
                x = 1
            except:
                pass
        """)
        diags = diagnose_file(source)
        w002 = [d for d in diags if d["code"] == "W002"]
        self.assertEqual(len(w002), 1)
        self.assertIn("Bare", w002[0]["message"])

    def test_typed_except_no_warning(self):
        source = textwrap.dedent("""\
            try:
                x = 1
            except ValueError:
                pass
        """)
        diags = diagnose_file(source)
        w002 = [d for d in diags if d["code"] == "W002"]
        self.assertEqual(len(w002), 0)

    def test_except_exception_no_warning(self):
        source = textwrap.dedent("""\
            try:
                x = 1
            except Exception:
                pass
        """)
        diags = diagnose_file(source)
        w002 = [d for d in diags if d["code"] == "W002"]
        self.assertEqual(len(w002), 0)


class TestDiagnoseDeepNesting(unittest.TestCase):

    def test_shallow_nesting_no_warning(self):
        source = textwrap.dedent("""\
            def foo():
                if True:
                    for x in []:
                        pass
        """)
        diags = diagnose_file(source)
        i002 = [d for d in diags if d["code"] == "I002"]
        self.assertEqual(len(i002), 0)

    def test_deep_nesting_detected(self):
        # Build nesting > threshold
        indent = ""
        lines = ["def deep():"]
        for i in range(_DEEP_NESTING_THRESHOLD + 2):
            indent += "    "
            lines.append(f"{indent}if True:")
        indent += "    "
        lines.append(f"{indent}pass")
        source = "\n".join(lines) + "\n"
        diags = diagnose_file(source)
        i002 = [d for d in diags if d["code"] == "I002"]
        self.assertGreaterEqual(len(i002), 1)


class TestDiagnoseShadowedBuiltins(unittest.TestCase):

    def test_shadow_list(self):
        source = "list = [1, 2, 3]\n"
        diags = diagnose_file(source)
        w003 = [d for d in diags if d["code"] == "W003"]
        self.assertEqual(len(w003), 1)
        self.assertIn("list", w003[0]["message"])

    def test_shadow_via_function(self):
        source = "def id(x):\n    return x\n"
        diags = diagnose_file(source)
        w003 = [d for d in diags if d["code"] == "W003"]
        self.assertEqual(len(w003), 1)
        self.assertIn("id", w003[0]["message"])

    def test_no_shadow_for_normal_names(self):
        source = "my_list = [1, 2, 3]\n"
        diags = diagnose_file(source)
        w003 = [d for d in diags if d["code"] == "W003"]
        self.assertEqual(len(w003), 0)

    def test_shadowable_builtins_has_common_names(self):
        for name in ("list", "dict", "str", "int", "type", "id", "print", "len"):
            self.assertIn(name, _SHADOWABLE_BUILTINS)


class TestDiagnoseSyntaxError(unittest.TestCase):

    def test_syntax_error_reported(self):
        source = "def foo(\n"
        diags = diagnose_file(source)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]["severity"], "error")
        self.assertEqual(diags[0]["code"], "E001")
        self.assertIn("SyntaxError", diags[0]["message"])

    def test_clean_file_no_issues(self):
        source = textwrap.dedent("""\
            import os
            
            def greet(name: str) -> str:
                return f"Hello {os.path.basename(name)}"
        """)
        diags = diagnose_file(source)
        self.assertEqual(len(diags), 0)


class TestDiagnoseSortedByLine(unittest.TestCase):

    def test_diagnostics_sorted(self):
        source = textwrap.dedent("""\
            import os
            list = []
            try:
                pass
            except:
                pass
            x = 1
        """)
        diags = diagnose_file(source)
        lines = [d["line"] for d in diags]
        self.assertEqual(lines, sorted(lines))


# ── format_diagnostics tests ─────────────────────────────────

class TestFormatDiagnostics(unittest.TestCase):

    def test_no_issues(self):
        output = format_diagnostics([], filename="test.py")
        self.assertIn("No issues", output)
        self.assertIn("test.py", output)

    def test_has_header(self):
        diags = [{"line": 1, "severity": "warning", "code": "W001", "message": "test"}]
        output = format_diagnostics(diags, filename="foo.py")
        self.assertIn("Diagnostics: foo.py", output)

    def test_summary_counts(self):
        diags = [
            {"line": 1, "severity": "warning", "code": "W001", "message": "a"},
            {"line": 2, "severity": "info", "code": "I001", "message": "b"},
            {"line": 3, "severity": "warning", "code": "W002", "message": "c"},
        ]
        output = format_diagnostics(diags)
        self.assertIn("2 warning(s)", output)
        self.assertIn("1 info(s)", output)
        self.assertIn("Total: 3", output)


# ── execute() dispatch tests ─────────────────────────────────

class TestExecuteDiagnose(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ws = Path(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_diagnose_clean_file(self):
        f = self.ws / "clean.py"
        f.write_text("import os\nprint(os.getcwd())\n")
        result = execute({"action": "diagnose", "path": str(f)}, self.ws)
        self.assertTrue(result["success"])
        self.assertIn("No issues", result["output"])
        self.assertEqual(len(result["diagnostics"]), 0)

    def test_diagnose_with_issues(self):
        f = self.ws / "messy.py"
        f.write_text("import os\nlist = []\n")
        result = execute({"action": "diagnose", "path": str(f)}, self.ws)
        self.assertTrue(result["success"])
        self.assertGreater(len(result["diagnostics"]), 0)

    def test_diagnose_non_python(self):
        f = self.ws / "test.js"
        f.write_text("const x = 1;")
        result = execute({"action": "diagnose", "path": str(f)}, self.ws)
        self.assertFalse(result["success"])
        self.assertIn("Python", result["error"])

    def test_diagnose_missing_file(self):
        result = execute({"action": "diagnose", "path": "nonexistent.py"}, self.ws)
        self.assertFalse(result["success"])


# ── Backward compatibility tests ─────────────────────────────

class TestBackwardCompat(unittest.TestCase):
    """Ensure existing actions still work after adding diagnose."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ws = Path(self.tmpdir)
        self.pyfile = self.ws / "sample.py"
        self.pyfile.write_text(textwrap.dedent("""\
            import os
            
            class Foo:
                pass
            
            def bar(x: int) -> str:
                return str(x)
        """))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_symbols_still_works(self):
        result = execute({"action": "symbols", "path": str(self.pyfile)}, self.ws)
        self.assertTrue(result["success"])
        self.assertIn("Foo", result["output"])

    def test_analyze_still_works(self):
        result = execute({"action": "analyze", "path": str(self.pyfile)}, self.ws)
        self.assertTrue(result["success"])
        self.assertIn("Analysis", result["output"])

    def test_unknown_action_error(self):
        result = execute({"action": "bogus", "path": str(self.pyfile)}, self.ws)
        self.assertFalse(result["success"])
        self.assertIn("diagnose", result["error"])

    def test_tool_def_has_diagnose(self):
        enum = TOOL_DEF["function"]["parameters"]["properties"]["action"]["enum"]
        self.assertIn("diagnose", enum)
        # Old actions still present
        for a in ("symbols", "refs", "analyze"):
            self.assertIn(a, enum)

    def test_tool_def_description_mentions_diagnose(self):
        desc = TOOL_DEF["function"]["description"]
        self.assertIn("diagnose", desc)


if __name__ == "__main__":
    unittest.main()
