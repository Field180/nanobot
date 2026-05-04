"""
P96: code_intel tool — Code Intelligence for Nanobot.

Three actions built on Python's stdlib `ast` module (zero external deps):

  symbols  — Extract all classes, functions, variables from a file
             with line numbers, types, signatures, and hierarchy.
  refs     — Find all references to a symbol across a project,
             classified as definition/call/import/assignment/other.
  analyze  — Lightweight type analysis via ast: return types,
             argument types, unresolved names, import graph.
             Falls back to pyright/mypy if installed.

Why ast instead of ctags/tree-sitter:
  - Already in Python stdlib — no install required
  - Gives full AST (not just tags) — enables type inference, scope, hierarchy
  - Python-first (Nanobot's own codebase), with regex fallback for other langs
"""
import ast
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from tools.base import _resolve_path

logger = logging.getLogger("nanobot.tools.code_intel")

TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "code_intel",
        "description": (
            "Code intelligence: extract symbols, find references, analyze types, diagnose quality. "
            "Actions: symbols (list classes/functions/vars in a file), "
            "refs (find all references to a symbol across files), "
            "analyze (type analysis and import graph for a file), "
            "diagnose (code quality checks: unused imports, long functions, bare excepts). "
            "Works on Python files via AST; other languages use enhanced grep."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "symbols: Extract all definitions from a file. "
                        "refs: Find all references to a name across files. "
                        "analyze: Type analysis, unresolved names, import graph. "
                        "diagnose: Code quality checks (unused imports, long functions, bare excepts, deep nesting)."
                    ),
                    "enum": ["symbols", "refs", "analyze", "diagnose"],
                },
                "path": {
                    "type": "string",
                    "description": (
                        "File path for 'symbols' and 'analyze'. "
                        "Directory path for 'refs' (searches recursively)."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Symbol name to search for (required for 'refs'). "
                        "Optional for 'symbols' (filters results). "
                        "Optional for 'analyze' (focuses on one symbol)."
                    ),
                },
            },
            "required": ["action", "path"],
        },
    },
}

ALIASES = ["symbols", "references", "find_symbol"]
IS_READONLY = True

GUIDANCE = {
    "when_to_use": (
        "When you need to understand code structure without reading entire files. "
        "Use 'symbols' to see all definitions, 'refs' to find where something is used, "
        "'analyze' to check types and imports, 'diagnose' to check code quality."
    ),
    "tips": [
        "symbols is much faster than reading a whole file to find function signatures",
        "refs classifies each match as def/call/import/assignment — more useful than raw grep",
        "analyze detects missing imports and unresolved names",
        "diagnose finds unused imports, long functions, bare excepts, deep nesting, shadowed builtins",
    ],
}


# ═══════════════════════════════════════════════════════════════
# Symbol Extraction (via ast)
# ═══════════════════════════════════════════════════════════════

def _get_signature(node: ast.FunctionDef) -> str:
    """Extract function signature as string."""
    args = node.args
    parts = []
    # Positional args
    defaults_offset = len(args.args) - len(args.defaults)
    for i, arg in enumerate(args.args):
        name = arg.arg
        annotation = ""
        if arg.annotation:
            annotation = f": {ast.unparse(arg.annotation)}"
        default = ""
        default_idx = i - defaults_offset
        if default_idx >= 0:
            default = f"={ast.unparse(args.defaults[default_idx])}"
        parts.append(f"{name}{annotation}{default}")

    # *args
    if args.vararg:
        va = args.vararg
        ann = f": {ast.unparse(va.annotation)}" if va.annotation else ""
        parts.append(f"*{va.arg}{ann}")
    elif args.kwonlyargs:
        parts.append("*")

    # keyword-only args
    kw_defaults_offset = 0
    for i, kw in enumerate(args.kwonlyargs):
        ann = f": {ast.unparse(kw.annotation)}" if kw.annotation else ""
        default = ""
        if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
            default = f"={ast.unparse(args.kw_defaults[i])}"
        parts.append(f"{kw.arg}{ann}{default}")

    # **kwargs
    if args.kwarg:
        ka = args.kwarg
        ann = f": {ast.unparse(ka.annotation)}" if ka.annotation else ""
        parts.append(f"**{ka.arg}{ann}")

    ret = ""
    if node.returns:
        ret = f" -> {ast.unparse(node.returns)}"

    return f"({', '.join(parts)}){ret}"


def _extract_decorators(node) -> List[str]:
    """Extract decorator names."""
    decorators = []
    for d in getattr(node, "decorator_list", []):
        try:
            decorators.append(ast.unparse(d))
        except Exception:
            decorators.append("@?")
    return decorators


def extract_symbols(source: str, filename: str = "<file>", filter_name: str = "") -> List[Dict[str, Any]]:
    """Extract all symbols from Python source code.

    Returns list of dicts: {name, type, line, end_line, signature, parent, decorators, docstring}
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return [{"error": f"SyntaxError: {e}"}]

    symbols = []

    def _visit(node, parent_name: str = ""):
        if isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases] if node.bases else []
            sym = {
                "name": node.name,
                "type": "class",
                "line": node.lineno,
                "end_line": node.end_lineno or node.lineno,
                "bases": bases,
                "parent": parent_name,
                "decorators": _extract_decorators(node),
                "docstring": ast.get_docstring(node) or "",
            }
            if not filter_name or filter_name.lower() in node.name.lower():
                symbols.append(sym)
            for child in ast.iter_child_nodes(node):
                _visit(child, parent_name=node.name)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            is_async = isinstance(node, ast.AsyncFunctionDef)
            sym = {
                "name": node.name,
                "type": "async_function" if is_async else "function",
                "line": node.lineno,
                "end_line": node.end_lineno or node.lineno,
                "signature": _get_signature(node),
                "parent": parent_name,
                "decorators": _extract_decorators(node),
                "docstring": ast.get_docstring(node) or "",
            }
            if not filter_name or filter_name.lower() in node.name.lower():
                symbols.append(sym)
            for child in ast.iter_child_nodes(node):
                _visit(child, parent_name=f"{parent_name}.{node.name}" if parent_name else node.name)

        elif isinstance(node, ast.Assign):
            # Module-level or class-level variable assignments
            if not parent_name or "." not in parent_name:  # skip nested function locals
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        # Infer type from value
                        type_hint = ""
                        if isinstance(node.value, ast.Constant):
                            type_hint = type(node.value.value).__name__
                        elif isinstance(node.value, (ast.List, ast.ListComp)):
                            type_hint = "list"
                        elif isinstance(node.value, (ast.Dict, ast.DictComp)):
                            type_hint = "dict"
                        elif isinstance(node.value, (ast.Set, ast.SetComp)):
                            type_hint = "set"
                        elif isinstance(node.value, ast.Tuple):
                            type_hint = "tuple"
                        elif isinstance(node.value, ast.Call):
                            try:
                                type_hint = ast.unparse(node.value.func)
                            except Exception:
                                pass

                        sym = {
                            "name": target.id,
                            "type": "variable",
                            "line": node.lineno,
                            "end_line": node.end_lineno or node.lineno,
                            "value_type": type_hint,
                            "parent": parent_name,
                        }
                        if not filter_name or filter_name.lower() in target.id.lower():
                            symbols.append(sym)

        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            ann = ast.unparse(node.annotation) if node.annotation else ""
            sym = {
                "name": node.target.id,
                "type": "variable",
                "line": node.lineno,
                "end_line": node.end_lineno or node.lineno,
                "annotation": ann,
                "parent": parent_name,
            }
            if not filter_name or filter_name.lower() in node.target.id.lower():
                symbols.append(sym)

        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # Track imports
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            for alias in node.names:
                real_name = alias.asname or alias.name
                sym = {
                    "name": real_name,
                    "type": "import",
                    "line": node.lineno,
                    "end_line": node.lineno,
                    "module": module if module else alias.name,
                    "original": alias.name if alias.asname else "",
                    "parent": parent_name,
                }
                if not filter_name or filter_name.lower() in real_name.lower():
                    symbols.append(sym)

        else:
            for child in ast.iter_child_nodes(node):
                _visit(child, parent_name)

    for child in ast.iter_child_nodes(tree):
        _visit(child)

    return symbols


def format_symbols(symbols: List[Dict], filename: str = "") -> str:
    """Format symbols into readable output."""
    if not symbols:
        return f"No symbols found{' in ' + filename if filename else ''}."

    if "error" in symbols[0]:
        return symbols[0]["error"]

    header = f"# Symbols in {filename}\n" if filename else "# Symbols\n"
    lines = [header]

    # Group by type
    classes = [s for s in symbols if s["type"] == "class"]
    functions = [s for s in symbols if s["type"] in ("function", "async_function")]
    variables = [s for s in symbols if s["type"] == "variable"]
    imports = [s for s in symbols if s["type"] == "import"]

    if classes:
        lines.append(f"## Classes ({len(classes)})")
        for c in classes:
            bases = f"({', '.join(c.get('bases', []))})" if c.get("bases") else ""
            parent = f"  [{c['parent']}]" if c.get("parent") else ""
            decorators = ", ".join(f"@{d}" for d in c.get("decorators", []))
            dec_str = f"  {decorators}" if decorators else ""
            doc = f"  — {c['docstring'][:80]}" if c.get("docstring") else ""
            lines.append(f"  L{c['line']:>4} class {c['name']}{bases}{parent}{dec_str}{doc}")

    if functions:
        lines.append(f"\n## Functions ({len(functions)})")
        for f in functions:
            prefix = "async def" if f["type"] == "async_function" else "def"
            sig = f.get("signature", "()")
            parent = f"  [{f['parent']}]" if f.get("parent") else ""
            decorators = ", ".join(f"@{d}" for d in f.get("decorators", []))
            dec_str = f"  {decorators}" if decorators else ""
            doc = f"  — {f['docstring'][:60]}" if f.get("docstring") else ""
            lines.append(f"  L{f['line']:>4} {prefix} {f['name']}{sig}{parent}{dec_str}{doc}")

    if variables:
        lines.append(f"\n## Variables ({len(variables)})")
        for v in variables:
            vtype = v.get("annotation") or v.get("value_type", "")
            type_str = f": {vtype}" if vtype else ""
            parent = f"  [{v['parent']}]" if v.get("parent") else ""
            lines.append(f"  L{v['line']:>4} {v['name']}{type_str}{parent}")

    if imports:
        lines.append(f"\n## Imports ({len(imports)})")
        for i in imports:
            orig = f" (as {i['name']})" if i.get("original") else ""
            mod = f" from {i['module']}" if i.get("module") else ""
            lines.append(f"  L{i['line']:>4} {i.get('original') or i['name']}{mod}{orig}")

    lines.append(f"\nTotal: {len(symbols)} symbols")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Reference Search (enhanced grep with classification)
# ═══════════════════════════════════════════════════════════════

def find_references(
    name: str,
    search_dir: Path,
    include: str = "*.py",
    max_results: int = 100,
) -> List[Dict[str, Any]]:
    """Find all references to a symbol, classified by context.

    Returns list of {file, line, col, text, ref_type, context}.
    ref_type: definition, call, import, assignment, attribute, other
    """
    refs = []
    # Use grep for speed, then classify each match
    pattern = rf"\b{re.escape(name)}\b"

    try:
        cmd = ["grep", "-rnE", pattern, str(search_dir)]
        if include:
            cmd = ["grep", "-rnE", f"--include={include}", pattern, str(search_dir)]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            cwd=str(search_dir)
        )
        raw_lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    except Exception as e:
        return [{"error": str(e)}]

    for raw in raw_lines[:max_results]:
        # Parse grep output: file:line:text
        match = re.match(r'^(.+?):(\d+):(.*)$', raw)
        if not match:
            continue

        filepath, lineno, text = match.group(1), int(match.group(2)), match.group(3)
        text = text.strip()

        # Classify reference type
        ref_type = _classify_reference(name, text)
        refs.append({
            "file": filepath,
            "line": lineno,
            "text": text[:200],
            "ref_type": ref_type,
        })

    return refs


def _classify_reference(name: str, line_text: str) -> str:
    """Classify a reference as definition/call/import/assignment/other."""
    stripped = line_text.lstrip()

    # Definition patterns
    if re.match(rf'^(async\s+)?def\s+{re.escape(name)}\s*\(', stripped):
        return "definition"
    if re.match(rf'^class\s+{re.escape(name)}\s*[:\(]', stripped):
        return "definition"

    # Import patterns
    if re.match(rf'^from\s+\S+\s+import\s+.*\b{re.escape(name)}\b', stripped):
        return "import"
    if re.match(rf'^import\s+.*\b{re.escape(name)}\b', stripped):
        return "import"

    # Assignment patterns (name = ..., name: type = ...)
    if re.match(rf'^{re.escape(name)}\s*[=:]', stripped):
        return "assignment"
    if re.match(rf'^self\.{re.escape(name)}\s*=', stripped):
        return "assignment"

    # Call patterns (name(...), something.name(...))
    if re.search(rf'\b{re.escape(name)}\s*\(', stripped):
        return "call"

    # Attribute access (something.name)
    if re.search(rf'\.\s*{re.escape(name)}\b', stripped):
        return "attribute"

    # Decorator
    if re.match(rf'^@{re.escape(name)}\b', stripped):
        return "decorator"

    return "other"


def format_references(refs: List[Dict], name: str) -> str:
    """Format references into grouped output."""
    if not refs:
        return f"No references found for '{name}'."

    if "error" in refs[0]:
        return f"Error: {refs[0]['error']}"

    # Group by ref_type
    by_type: Dict[str, List] = {}
    for r in refs:
        rt = r["ref_type"]
        by_type.setdefault(rt, []).append(r)

    lines = [f"# References to `{name}` ({len(refs)} total)\n"]

    type_order = ["definition", "import", "call", "assignment", "attribute", "decorator", "other"]
    type_icons = {
        "definition": "📍", "import": "📦", "call": "📞",
        "assignment": "📝", "attribute": "🔗", "decorator": "🏷️", "other": "❓",
    }

    for rt in type_order:
        group = by_type.get(rt, [])
        if not group:
            continue
        icon = type_icons.get(rt, "")
        lines.append(f"## {icon} {rt.title()} ({len(group)})")
        for r in group:
            lines.append(f"  {r['file']}:{r['line']}  {r['text']}")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Type Analysis (ast-based + optional pyright/mypy)
# ═══════════════════════════════════════════════════════════════

def analyze_file(
    source: str,
    filename: str = "<file>",
    focus_name: str = "",
) -> Dict[str, Any]:
    """Analyze a Python file for type information.

    Returns: {
        imports: [{module, names, line}],
        unresolved: [names used but not defined or imported],
        functions: [{name, args_typed, return_typed, complexity}],
        type_coverage: float (0-1),
        external_check: str (pyright/mypy output if available),
    }
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return {"error": f"SyntaxError: {e}"}

    # Collect defined names and imported names
    defined: Set[str] = set()
    imported: Set[str] = set()
    used: Set[str] = set()
    import_lines: List[Dict] = []
    func_info: List[Dict] = []

    class Analyzer(ast.NodeVisitor):
        def __init__(self):
            self.scope_stack: List[str] = []

        def visit_Import(self, node):
            for alias in node.names:
                name = alias.asname or alias.name
                imported.add(name)
                import_lines.append({
                    "module": alias.name,
                    "alias": alias.asname,
                    "line": node.lineno,
                })
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            module = node.module or ""
            for alias in node.names:
                name = alias.asname or alias.name
                imported.add(name)
                import_lines.append({
                    "module": f"{module}.{alias.name}" if module else alias.name,
                    "alias": alias.asname,
                    "line": node.lineno,
                })
            self.generic_visit(node)

        def visit_ClassDef(self, node):
            defined.add(node.name)
            self.scope_stack.append(node.name)
            self.generic_visit(node)
            self.scope_stack.pop()

        def visit_FunctionDef(self, node):
            defined.add(node.name)
            self._analyze_function(node)
            self.scope_stack.append(node.name)
            self.generic_visit(node)
            self.scope_stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def _analyze_function(self, node):
            if focus_name and focus_name.lower() not in node.name.lower():
                return

            args = node.args
            total_args = len(args.args)
            typed_args = sum(1 for a in args.args if a.annotation is not None)
            # Skip 'self' / 'cls'
            if args.args and args.args[0].arg in ("self", "cls"):
                total_args = max(0, total_args - 1)
                typed_args = max(0, typed_args - (0 if args.args[0].annotation else 0))

            has_return = node.returns is not None
            return_type = ast.unparse(node.returns) if node.returns else ""

            # Simple cyclomatic complexity estimate
            complexity = 1
            for child in ast.walk(node):
                if isinstance(child, (ast.If, ast.While, ast.For, ast.ExceptHandler)):
                    complexity += 1
                elif isinstance(child, ast.BoolOp):
                    complexity += len(child.values) - 1

            func_info.append({
                "name": node.name,
                "line": node.lineno,
                "total_args": total_args,
                "typed_args": typed_args,
                "has_return_type": has_return,
                "return_type": return_type,
                "complexity": complexity,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })

        def visit_Name(self, node):
            if isinstance(node.ctx, (ast.Load,)):
                used.add(node.id)
            elif isinstance(node.ctx, (ast.Store,)):
                defined.add(node.id)
            self.generic_visit(node)

        def visit_Assign(self, node):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
            self.generic_visit(node)

    analyzer = Analyzer()
    analyzer.visit(tree)

    # Built-in names (not exhaustive, but covers common ones)
    builtins_names = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set(dir(__builtins__))
    builtins_names.update({"True", "False", "None", "print", "len", "range", "int", "str",
                           "float", "list", "dict", "set", "tuple", "bool", "type", "super",
                           "isinstance", "issubclass", "hasattr", "getattr", "setattr",
                           "open", "property", "staticmethod", "classmethod", "Exception",
                           "ValueError", "TypeError", "KeyError", "IndexError", "RuntimeError",
                           "AttributeError", "ImportError", "FileNotFoundError", "OSError",
                           "NotImplementedError", "StopIteration", "any", "all", "map",
                           "filter", "zip", "enumerate", "sorted", "reversed", "min", "max",
                           "abs", "sum", "round", "format", "repr", "id", "hash", "callable",
                           "iter", "next", "bytes", "bytearray", "memoryview", "object",
                           "frozenset", "complex", "chr", "ord", "hex", "oct", "bin",
                           "input", "exec", "eval", "compile", "globals", "locals", "vars",
                           "dir", "help", "breakpoint", "exit", "quit", "__name__", "__file__",
                           "__doc__", "__all__", "__builtins__", "__import__"})

    # Unresolved = used but not defined and not imported and not builtin
    unresolved = sorted(used - defined - imported - builtins_names)

    # Type coverage
    total_annotations = 0
    present_annotations = 0
    for fi in func_info:
        total_annotations += fi["total_args"] + 1  # args + return
        present_annotations += fi["typed_args"] + (1 if fi["has_return_type"] else 0)

    type_coverage = present_annotations / total_annotations if total_annotations > 0 else 0.0

    result = {
        "imports": import_lines,
        "unresolved": unresolved[:30],
        "functions": func_info,
        "type_coverage": round(type_coverage, 2),
        "stats": {
            "total_defined": len(defined),
            "total_imported": len(imported),
            "total_used": len(used),
            "total_unresolved": len(unresolved),
        },
    }

    return result


def format_analysis(analysis: Dict, filename: str = "") -> str:
    """Format analysis results."""
    if "error" in analysis:
        return analysis["error"]

    lines = [f"# Analysis: {filename}\n" if filename else "# Analysis\n"]

    # Type coverage
    cov = analysis.get("type_coverage", 0)
    bar = "█" * int(cov * 20) + "░" * (20 - int(cov * 20))
    lines.append(f"## Type Coverage: {cov:.0%}  [{bar}]")

    # Stats
    stats = analysis.get("stats", {})
    lines.append(f"  Defined: {stats.get('total_defined', 0)} | "
                 f"Imported: {stats.get('total_imported', 0)} | "
                 f"Unresolved: {stats.get('total_unresolved', 0)}")

    # Functions with type info
    funcs = analysis.get("functions", [])
    if funcs:
        lines.append(f"\n## Functions ({len(funcs)})")
        for f in funcs:
            async_prefix = "async " if f.get("is_async") else ""
            typed = f["typed_args"]
            total = f["total_args"]
            ret = f"→ {f['return_type']}" if f.get("return_type") else "→ ?"
            cx = f["complexity"]
            cx_label = "simple" if cx <= 5 else ("moderate" if cx <= 10 else "complex")
            lines.append(
                f"  L{f['line']:>4} {async_prefix}{f['name']}  "
                f"args: {typed}/{total} typed  {ret}  "
                f"complexity: {cx} ({cx_label})"
            )

    # Unresolved names
    unresolved = analysis.get("unresolved", [])
    if unresolved:
        lines.append(f"\n## Unresolved Names ({len(unresolved)})")
        lines.append(f"  {', '.join(unresolved[:20])}")
        if len(unresolved) > 20:
            lines.append(f"  ... and {len(unresolved) - 20} more")

    # Import graph
    imports = analysis.get("imports", [])
    if imports:
        lines.append(f"\n## Imports ({len(imports)})")
        for imp in imports[:30]:
            alias = f" as {imp['alias']}" if imp.get("alias") else ""
            lines.append(f"  L{imp['line']:>4} {imp['module']}{alias}")

    return "\n".join(lines)


def _try_external_checker(filepath: str) -> str:
    """Try pyright or mypy if installed. Returns output or empty string."""
    for tool in ["pyright", "mypy"]:
        try:
            result = subprocess.run(
                [tool, filepath],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode >= 0:
                output = (result.stdout + result.stderr).strip()
                if output:
                    return f"\n## {tool} output\n```\n{output[:3000]}\n```"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return ""


# ═══════════════════════════════════════════════════════════════
# Non-Python fallback (regex-based symbol extraction)
# ═══════════════════════════════════════════════════════════════

_LANG_PATTERNS = {
    ".js": {
        "function": r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\(|function))',
        "class": r'class\s+(\w+)',
        "export": r'export\s+(?:default\s+)?(?:function|class|const|let|var)\s+(\w+)',
    },
    ".ts": {
        "function": r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*(?::\s*\w+)?\s*=\s*(?:async\s+)?(?:\(|function))',
        "class": r'class\s+(\w+)',
        "interface": r'interface\s+(\w+)',
        "type": r'type\s+(\w+)',
        "export": r'export\s+(?:default\s+)?(?:function|class|const|let|var|interface|type)\s+(\w+)',
    },
    ".go": {
        "function": r'func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)',
        "type": r'type\s+(\w+)\s+(?:struct|interface)',
    },
    ".rs": {
        "function": r'(?:pub\s+)?fn\s+(\w+)',
        "struct": r'(?:pub\s+)?struct\s+(\w+)',
        "enum": r'(?:pub\s+)?enum\s+(\w+)',
        "trait": r'(?:pub\s+)?trait\s+(\w+)',
    },
}


def _extract_symbols_regex(source: str, ext: str) -> List[Dict]:
    """Fallback symbol extraction for non-Python files."""
    patterns = _LANG_PATTERNS.get(ext, {})
    if not patterns:
        return []

    symbols = []
    for lineno, line in enumerate(source.split("\n"), 1):
        for sym_type, pattern in patterns.items():
            for m in re.finditer(pattern, line):
                name = next((g for g in m.groups() if g), None)
                if name:
                    symbols.append({
                        "name": name,
                        "type": sym_type,
                        "line": lineno,
                        "end_line": lineno,
                    })
    return symbols


# ═══════════════════════════════════════════════════════════════
# Code Quality Diagnostics (pure ast, zero deps)
# ═══════════════════════════════════════════════════════════════

_LONG_FUNCTION_THRESHOLD = 50  # lines
_DEEP_NESTING_THRESHOLD = 4   # levels

# Common builtins that are dangerous to shadow
_SHADOWABLE_BUILTINS = frozenset({
    "list", "dict", "set", "tuple", "int", "str", "float", "bool",
    "type", "id", "input", "open", "range", "map", "filter", "print",
    "len", "max", "min", "sum", "any", "all", "next", "iter", "hash",
    "format", "vars", "dir", "help", "object", "property", "super",
    "bytes", "complex", "frozenset", "sorted", "reversed", "zip",
    "enumerate", "isinstance", "issubclass", "hasattr", "getattr",
    "setattr", "callable",
})


def diagnose_file(source: str, filename: str = "<file>") -> List[Dict[str, Any]]:
    """Run lightweight code quality checks on Python source.

    All checks use stdlib ``ast`` only — no external tools.

    Checks performed:
      1. Unused imports
      2. Functions longer than _LONG_FUNCTION_THRESHOLD lines
      3. Bare ``except:`` (no exception type)
      4. Nesting depth > _DEEP_NESTING_THRESHOLD
      5. Top-level assignments that shadow common builtins

    Returns a list of diagnostic dicts:
        ``[{"line": int, "severity": "warning"|"info", "code": str, "message": str}]``
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return [{"line": e.lineno or 1, "severity": "error", "code": "E001",
                 "message": f"SyntaxError: {e.msg}"}]

    diagnostics: List[Dict[str, Any]] = []

    # ── 1. Unused imports ────────────────────────────────────
    imported_names: Dict[str, int] = {}  # name → line
    used_names: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                local = alias.asname or alias.name
                # Skip star imports
                if local != "*":
                    imported_names.setdefault(local, node.lineno)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            # foo.bar — count 'foo' as used
            inner = node.value
            while isinstance(inner, ast.Attribute):
                inner = inner.value
            if isinstance(inner, ast.Name):
                used_names.add(inner.id)

    for name, line in imported_names.items():
        # Dotted imports: 'os.path' → check 'os'
        base = name.split(".")[0]
        if base not in used_names and name not in used_names:
            # Skip __future__ and TYPE_CHECKING guard imports
            diagnostics.append({
                "line": line,
                "severity": "warning",
                "code": "W001",
                "message": f"Unused import: '{name}'",
            })

    # ── 2. Long functions ────────────────────────────────────
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", None) or node.lineno
            length = end - node.lineno + 1
            if length > _LONG_FUNCTION_THRESHOLD:
                diagnostics.append({
                    "line": node.lineno,
                    "severity": "info",
                    "code": "I001",
                    "message": (
                        f"Function '{node.name}' is {length} lines long "
                        f"(threshold: {_LONG_FUNCTION_THRESHOLD}). "
                        f"Consider splitting into smaller functions."
                    ),
                })

    # ── 3. Bare except ───────────────────────────────────────
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            diagnostics.append({
                "line": node.lineno,
                "severity": "warning",
                "code": "W002",
                "message": "Bare 'except:' catches all exceptions including KeyboardInterrupt. Use 'except Exception:' instead.",
            })

    # ── 4. Deep nesting ──────────────────────────────────────
    def _check_nesting(node: ast.AST, depth: int = 0, parent_func: str = ""):
        """Walk tree tracking nesting depth of control-flow blocks."""
        # Determine current function context
        func_name = parent_func
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_name = node.name
            depth = 0  # reset depth inside new function

        is_block = isinstance(node, (
            ast.If, ast.For, ast.While, ast.With,
            ast.Try, ast.ExceptHandler,
        ))
        new_depth = depth + 1 if is_block else depth

        if is_block and new_depth > _DEEP_NESTING_THRESHOLD:
            ctx = f" in '{func_name}'" if func_name else ""
            diagnostics.append({
                "line": node.lineno,
                "severity": "info",
                "code": "I002",
                "message": (
                    f"Nesting depth {new_depth}{ctx} exceeds threshold "
                    f"({_DEEP_NESTING_THRESHOLD}). Consider early returns or extraction."
                ),
            })
            return  # don't report children — one warning per branch is enough

        for child in ast.iter_child_nodes(node):
            _check_nesting(child, new_depth, func_name)

    _check_nesting(tree)

    # ── 5. Shadowed builtins ─────────────────────────────────
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _SHADOWABLE_BUILTINS:
                    diagnostics.append({
                        "line": node.lineno,
                        "severity": "warning",
                        "code": "W003",
                        "message": f"Top-level assignment shadows builtin '{target.id}'.",
                    })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in _SHADOWABLE_BUILTINS:
                diagnostics.append({
                    "line": node.lineno,
                    "severity": "warning",
                    "code": "W003",
                    "message": f"Function name '{node.name}' shadows builtin.",
                })

    # Sort by line number
    diagnostics.sort(key=lambda d: d["line"])
    return diagnostics


def format_diagnostics(diagnostics: List[Dict[str, Any]], filename: str = "") -> str:
    """Format diagnostics into human-readable output."""
    if not diagnostics:
        return f"No issues found{' in ' + filename if filename else ''}. ✅"

    header = f"# Diagnostics: {filename}\n" if filename else "# Diagnostics\n"
    lines = [header]

    severity_icons = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}
    counts: Dict[str, int] = {"error": 0, "warning": 0, "info": 0}

    for d in diagnostics:
        sev = d["severity"]
        counts[sev] = counts.get(sev, 0) + 1
        icon = severity_icons.get(sev, "")
        lines.append(f"  L{d['line']:>4} {icon} [{d['code']}] {d['message']}")

    lines.append("")
    summary_parts = []
    if counts["error"]:
        summary_parts.append(f"{counts['error']} error(s)")
    if counts["warning"]:
        summary_parts.append(f"{counts['warning']} warning(s)")
    if counts["info"]:
        summary_parts.append(f"{counts['info']} info(s)")
    lines.append(f"Total: {len(diagnostics)} issues ({', '.join(summary_parts)})")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Tool entry point
# ═══════════════════════════════════════════════════════════════

def execute(args: dict, workspace: Path) -> dict:
    """Execute the code_intel tool."""
    action = args.get("action", "")
    path_str = args.get("path", ".")
    name = args.get("name", "")

    resolved = _resolve_path(path_str, workspace)

    if action == "symbols":
        if not os.path.isfile(resolved):
            return {"success": False, "error": f"File not found: {resolved}"}
        try:
            source = Path(resolved).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"success": False, "error": f"Cannot read file: {e}"}

        ext = Path(resolved).suffix.lower()
        if ext == ".py":
            symbols = extract_symbols(source, filename=resolved, filter_name=name)
        else:
            symbols = _extract_symbols_regex(source, ext)
            if name:
                symbols = [s for s in symbols if name.lower() in s["name"].lower()]

        output = format_symbols(symbols, filename=path_str)
        return {"success": True, "output": output}

    elif action == "refs":
        if not name:
            return {"success": False, "error": "Missing 'name' parameter for refs action"}
        search_path = Path(resolved)
        if not search_path.exists():
            return {"success": False, "error": f"Path not found: {resolved}"}
        include = "*.py"  # default
        ext = Path(resolved).suffix.lower()
        if ext in (".js", ".ts", ".go", ".rs"):
            include = f"*{ext}"

        refs = find_references(name, search_path, include=include)
        output = format_references(refs, name)
        return {"success": True, "output": output}

    elif action == "analyze":
        if not os.path.isfile(resolved):
            return {"success": False, "error": f"File not found: {resolved}"}
        ext = Path(resolved).suffix.lower()
        if ext != ".py":
            return {"success": False, "error": f"Type analysis only supports Python files (got {ext})"}
        try:
            source = Path(resolved).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"success": False, "error": f"Cannot read file: {e}"}

        analysis = analyze_file(source, filename=resolved, focus_name=name)
        output = format_analysis(analysis, filename=path_str)

        # Try external checker
        ext_output = _try_external_checker(resolved)
        if ext_output:
            output += ext_output

        return {"success": True, "output": output}

    elif action == "diagnose":
        if not os.path.isfile(resolved):
            return {"success": False, "error": f"File not found: {resolved}"}
        ext = Path(resolved).suffix.lower()
        if ext != ".py":
            return {"success": False, "error": f"Diagnostics only supports Python files (got {ext})"}
        try:
            source = Path(resolved).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"success": False, "error": f"Cannot read file: {e}"}

        diags = diagnose_file(source, filename=resolved)
        output = format_diagnostics(diags, filename=path_str)
        return {"success": True, "output": output, "diagnostics": diags}

    else:
        return {"success": False, "error": f"Unknown action '{action}'. Must be: symbols, refs, analyze, diagnose"}
