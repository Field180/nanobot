#!/usr/bin/env python3
"""
Tests for P96: Code Intelligence (code_intel tool).

Covers:
  - P96a: Symbol extraction (Python ast + regex fallback)
  - P96b: Reference search with classification
  - P96c: Type analysis
  - Tool execute() entry point
"""
import os
import sys
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ═══════════════════════════════════════════════════════════════
# Sample Python source for testing
# ═══════════════════════════════════════════════════════════════
SAMPLE_PY = textwrap.dedent("""\
    import os
    from pathlib import Path
    from typing import List, Optional

    MAX_SIZE: int = 100
    DEFAULT_NAME = "nanobot"

    class BaseProcessor:
        \"\"\"Base class for processors.\"\"\"

        def __init__(self, name: str):
            self.name = name

        def process(self, data: List[str]) -> Optional[str]:
            \"\"\"Process data.\"\"\"
            if not data:
                return None
            return data[0]

    class AdvancedProcessor(BaseProcessor):
        \"\"\"Advanced processor with extra features.\"\"\"

        @staticmethod
        def validate(data: List[str]) -> bool:
            return len(data) > 0

        async def async_process(self, data: List[str]) -> str:
            result = self.process(data)
            return result or ""

    def helper_function(x: int, y: int = 10) -> int:
        \"\"\"A helper function.\"\"\"
        return x + y

    _private = helper_function(1, 2)
""")

SAMPLE_JS = textwrap.dedent("""\
    const API_URL = 'http://localhost';

    class AppController {
        constructor() {
            this.state = {};
        }
    }

    function sendMessage(text) {
        return fetch(API_URL + '/send', { body: text });
    }

    export default AppController;
""")


class TestP96aSymbolExtraction(unittest.TestCase):
    """P96a: Symbol extraction via ast."""

    def test_extract_classes(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols(SAMPLE_PY)
        classes = [s for s in symbols if s["type"] == "class"]
        names = [c["name"] for c in classes]
        self.assertIn("BaseProcessor", names)
        self.assertIn("AdvancedProcessor", names)

    def test_class_bases(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols(SAMPLE_PY)
        adv = next(s for s in symbols if s["name"] == "AdvancedProcessor")
        self.assertIn("BaseProcessor", adv.get("bases", []))

    def test_extract_functions(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols(SAMPLE_PY)
        funcs = [s for s in symbols if s["type"] in ("function", "async_function")]
        names = [f["name"] for f in funcs]
        self.assertIn("__init__", names)
        self.assertIn("process", names)
        self.assertIn("helper_function", names)
        self.assertIn("async_process", names)

    def test_async_function_type(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols(SAMPLE_PY)
        ap = next(s for s in symbols if s["name"] == "async_process")
        self.assertEqual(ap["type"], "async_function")

    def test_function_signature(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols(SAMPLE_PY)
        helper = next(s for s in symbols if s["name"] == "helper_function")
        self.assertIn("x: int", helper["signature"])
        self.assertIn("y: int=10", helper["signature"])
        self.assertIn("-> int", helper["signature"])

    def test_extract_variables(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols(SAMPLE_PY)
        variables = [s for s in symbols if s["type"] == "variable"]
        names = [v["name"] for v in variables]
        self.assertIn("MAX_SIZE", names)
        self.assertIn("DEFAULT_NAME", names)

    def test_variable_annotation(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols(SAMPLE_PY)
        max_size = next(s for s in symbols if s["name"] == "MAX_SIZE")
        self.assertEqual(max_size.get("annotation"), "int")

    def test_extract_imports(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols(SAMPLE_PY)
        imports = [s for s in symbols if s["type"] == "import"]
        names = [i["name"] for i in imports]
        self.assertIn("os", names)
        self.assertIn("Path", names)
        self.assertIn("List", names)

    def test_docstrings(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols(SAMPLE_PY)
        bp = next(s for s in symbols if s["name"] == "BaseProcessor")
        self.assertIn("Base class", bp.get("docstring", ""))

    def test_decorators(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols(SAMPLE_PY)
        validate = next(s for s in symbols if s["name"] == "validate")
        self.assertIn("staticmethod", validate.get("decorators", []))

    def test_parent_tracking(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols(SAMPLE_PY)
        init = next(s for s in symbols if s["name"] == "__init__" and s.get("parent") == "BaseProcessor")
        self.assertEqual(init["parent"], "BaseProcessor")

    def test_filter_by_name(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols(SAMPLE_PY, filter_name="process")
        names = [s["name"] for s in symbols]
        self.assertIn("process", names)
        self.assertIn("BaseProcessor", names)
        self.assertIn("AdvancedProcessor", names)
        self.assertNotIn("helper_function", names)

    def test_syntax_error(self):
        from tools.code_intel import extract_symbols
        symbols = extract_symbols("def broken(:\n    pass")
        self.assertTrue(len(symbols) == 1 and "error" in symbols[0])

    def test_format_symbols(self):
        from tools.code_intel import extract_symbols, format_symbols
        symbols = extract_symbols(SAMPLE_PY)
        output = format_symbols(symbols, "test.py")
        self.assertIn("Classes", output)
        self.assertIn("Functions", output)
        self.assertIn("Variables", output)
        self.assertIn("Imports", output)
        self.assertIn("Total:", output)


class TestP96aRegexFallback(unittest.TestCase):
    """P96a: Regex-based symbol extraction for non-Python files."""

    def test_js_functions(self):
        from tools.code_intel import _extract_symbols_regex
        symbols = _extract_symbols_regex(SAMPLE_JS, ".js")
        names = [s["name"] for s in symbols]
        self.assertIn("sendMessage", names)

    def test_js_classes(self):
        from tools.code_intel import _extract_symbols_regex
        symbols = _extract_symbols_regex(SAMPLE_JS, ".js")
        classes = [s for s in symbols if s["type"] == "class"]
        self.assertTrue(any(c["name"] == "AppController" for c in classes))

    def test_unsupported_ext(self):
        from tools.code_intel import _extract_symbols_regex
        symbols = _extract_symbols_regex("something", ".xyz")
        self.assertEqual(symbols, [])


class TestP96bReferenceSearch(unittest.TestCase):
    """P96b: Reference search with classification."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        # Write sample files
        (self.tmpdir / "main.py").write_text(textwrap.dedent("""\
            from utils import helper_function
            
            result = helper_function(1, 2)
            print(result)
        """))
        (self.tmpdir / "utils.py").write_text(textwrap.dedent("""\
            def helper_function(x, y):
                return x + y
            
            _cached = helper_function(0, 0)
        """))
        (self.tmpdir / "test.py").write_text(textwrap.dedent("""\
            from utils import helper_function
            
            class TestHelper:
                def test_basic(self):
                    assert helper_function(1, 2) == 3
        """))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_find_all_refs(self):
        from tools.code_intel import find_references
        refs = find_references("helper_function", self.tmpdir)
        self.assertGreaterEqual(len(refs), 4)

    def test_classify_definition(self):
        from tools.code_intel import find_references
        refs = find_references("helper_function", self.tmpdir)
        defs = [r for r in refs if r["ref_type"] == "definition"]
        self.assertGreaterEqual(len(defs), 1)

    def test_classify_import(self):
        from tools.code_intel import find_references
        refs = find_references("helper_function", self.tmpdir)
        imports = [r for r in refs if r["ref_type"] == "import"]
        self.assertGreaterEqual(len(imports), 2)

    def test_classify_call(self):
        from tools.code_intel import find_references
        refs = find_references("helper_function", self.tmpdir)
        calls = [r for r in refs if r["ref_type"] == "call"]
        self.assertGreaterEqual(len(calls), 2)

    def test_format_references(self):
        from tools.code_intel import find_references, format_references
        refs = find_references("helper_function", self.tmpdir)
        output = format_references(refs, "helper_function")
        self.assertIn("References to", output)
        self.assertIn("Definition", output)
        self.assertIn("Import", output)

    def test_classify_assignment(self):
        from tools.code_intel import _classify_reference
        self.assertEqual(_classify_reference("x", "x = 42"), "assignment")

    def test_classify_decorator(self):
        from tools.code_intel import _classify_reference
        self.assertEqual(_classify_reference("staticmethod", "@staticmethod"), "decorator")

    def test_classify_attribute(self):
        from tools.code_intel import _classify_reference
        self.assertEqual(_classify_reference("name", "self.name"), "attribute")


class TestP96cTypeAnalysis(unittest.TestCase):
    """P96c: ast-based type analysis."""

    def test_analyze_basic(self):
        from tools.code_intel import analyze_file
        result = analyze_file(SAMPLE_PY)
        self.assertNotIn("error", result)
        self.assertIn("type_coverage", result)
        self.assertIn("functions", result)
        self.assertIn("imports", result)
        self.assertIn("unresolved", result)

    def test_type_coverage(self):
        from tools.code_intel import analyze_file
        result = analyze_file(SAMPLE_PY)
        # SAMPLE_PY has good type annotations
        self.assertGreater(result["type_coverage"], 0.5)

    def test_function_analysis(self):
        from tools.code_intel import analyze_file
        result = analyze_file(SAMPLE_PY)
        funcs = result["functions"]
        helper = next((f for f in funcs if f["name"] == "helper_function"), None)
        self.assertIsNotNone(helper)
        self.assertTrue(helper["has_return_type"])
        self.assertEqual(helper["return_type"], "int")

    def test_complexity(self):
        from tools.code_intel import analyze_file
        source = textwrap.dedent("""\
            def complex_func(x):
                if x > 0:
                    if x > 10:
                        for i in range(x):
                            if i % 2:
                                pass
                elif x < 0:
                    while x < 0:
                        x += 1
                return x
        """)
        result = analyze_file(source)
        funcs = result["functions"]
        self.assertEqual(len(funcs), 1)
        self.assertGreater(funcs[0]["complexity"], 3)

    def test_unresolved_names(self):
        from tools.code_intel import analyze_file
        source = textwrap.dedent("""\
            x = some_undefined_function()
            y = another_undefined()
        """)
        result = analyze_file(source)
        self.assertIn("some_undefined_function", result["unresolved"])
        self.assertIn("another_undefined", result["unresolved"])

    def test_imports_tracked(self):
        from tools.code_intel import analyze_file
        result = analyze_file(SAMPLE_PY)
        modules = [i["module"] for i in result["imports"]]
        self.assertIn("os", modules)

    def test_focus_name(self):
        from tools.code_intel import analyze_file
        result = analyze_file(SAMPLE_PY, focus_name="helper")
        funcs = result["functions"]
        names = [f["name"] for f in funcs]
        self.assertIn("helper_function", names)
        # Other functions should NOT be in the analysis
        self.assertNotIn("process", names)

    def test_format_analysis(self):
        from tools.code_intel import analyze_file, format_analysis
        result = analyze_file(SAMPLE_PY)
        output = format_analysis(result, "test.py")
        self.assertIn("Type Coverage", output)
        self.assertIn("Functions", output)

    def test_syntax_error_analyze(self):
        from tools.code_intel import analyze_file
        result = analyze_file("def broken(:\n  pass")
        self.assertIn("error", result)


class TestP96ToolExecute(unittest.TestCase):
    """P96: Tool execute() entry point."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "sample.py").write_text(SAMPLE_PY)
        (self.tmpdir / "app.js").write_text(SAMPLE_JS)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_symbols_action(self):
        from tools.code_intel import execute
        result = execute({"action": "symbols", "path": "sample.py"}, self.tmpdir)
        self.assertTrue(result["success"])
        self.assertIn("Classes", result["output"])
        self.assertIn("BaseProcessor", result["output"])

    def test_symbols_with_filter(self):
        from tools.code_intel import execute
        result = execute({"action": "symbols", "path": "sample.py", "name": "helper"}, self.tmpdir)
        self.assertTrue(result["success"])
        self.assertIn("helper_function", result["output"])

    def test_symbols_js(self):
        from tools.code_intel import execute
        result = execute({"action": "symbols", "path": "app.js"}, self.tmpdir)
        self.assertTrue(result["success"])
        self.assertIn("AppController", result["output"])

    def test_symbols_file_not_found(self):
        from tools.code_intel import execute
        result = execute({"action": "symbols", "path": "nope.py"}, self.tmpdir)
        self.assertFalse(result["success"])

    def test_refs_action(self):
        from tools.code_intel import execute
        (self.tmpdir / "caller.py").write_text("from sample import helper_function\nhelper_function(1, 2)")
        result = execute({"action": "refs", "path": ".", "name": "helper_function"}, self.tmpdir)
        self.assertTrue(result["success"])
        self.assertIn("References", result["output"])

    def test_refs_missing_name(self):
        from tools.code_intel import execute
        result = execute({"action": "refs", "path": "."}, self.tmpdir)
        self.assertFalse(result["success"])
        self.assertIn("Missing", result["error"])

    def test_analyze_action(self):
        from tools.code_intel import execute
        result = execute({"action": "analyze", "path": "sample.py"}, self.tmpdir)
        self.assertTrue(result["success"])
        self.assertIn("Type Coverage", result["output"])

    def test_analyze_non_python(self):
        from tools.code_intel import execute
        result = execute({"action": "analyze", "path": "app.js"}, self.tmpdir)
        self.assertFalse(result["success"])
        self.assertIn("only supports Python", result["error"])

    def test_invalid_action(self):
        from tools.code_intel import execute
        result = execute({"action": "nope", "path": "."}, self.tmpdir)
        self.assertFalse(result["success"])


# ═══════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []

    test_classes = [
        TestP96aSymbolExtraction,
        TestP96aRegexFallback,
        TestP96bReferenceSearch,
        TestP96cTypeAnalysis,
        TestP96ToolExecute,
    ]

    for cls in test_classes:
        suite = unittest.TestLoader().loadTestsFromTestCase(cls)
        print(f"\n╔══ {cls.__name__} ══╗")
        for test in suite:
            try:
                test.setUp() if hasattr(test, "setUp") else None
                test_method = getattr(test, test._testMethodName)
                test_method()
                test.tearDown() if hasattr(test, "tearDown") else None
                print(f"  ✅ {test._testMethodName}")
                passed += 1
            except Exception as e:
                try:
                    test.tearDown() if hasattr(test, "tearDown") else None
                except Exception:
                    pass
                print(f"  ❌ {test._testMethodName}: {e}")
                failed += 1
                errors.append((test._testMethodName, str(e)))

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print(f"\nFailed tests:")
        for name, err in errors:
            print(f"  - {name}: {err}")
    print(f"{'=' * 60}")

    if failed == 0:
        print("🎉 All code intelligence tests passed!")

    sys.exit(0 if failed == 0 else 1)
