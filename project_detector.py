"""
P7: Project auto-detection and configuration guidance.

Scans a workspace directory for marker files to identify language, framework,
package manager, test framework, and generate recommended NANOBOT.md rules.

Pure stdlib — no external dependencies required.
"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nanobot.project_detector")

# ═══════════════════════════════════════════════════════════════
# Detection logic
# ═══════════════════════════════════════════════════════════════


def detect_project(workspace: str) -> Dict[str, Any]:
    """Scan workspace and return structured project info.

    Args:
        workspace: Absolute path to the project root directory.

    Returns:
        Dict with keys: language, frameworks, package_manager, test_framework,
        has_git, has_docker, has_ci, markers, recommended_ignore_patterns,
        entry_points, detected_dependencies.
    """
    ws = Path(workspace)
    if not ws.is_dir():
        return {"error": f"Not a directory: {workspace}", "language": "unknown"}

    # Collect root-level file/dir names for fast lookup
    try:
        entries = set(os.listdir(ws))
    except OSError as e:
        return {"error": str(e), "language": "unknown"}

    result: Dict[str, Any] = {
        "language": "unknown",
        "frameworks": [],
        "package_manager": None,
        "test_framework": None,
        "has_git": ".git" in entries,
        "has_docker": "Dockerfile" in entries or "docker-compose.yml" in entries or "docker-compose.yaml" in entries,
        "has_ci": any(f in entries for f in [".github", ".gitlab-ci.yml", "Jenkinsfile", ".circleci"]),
        "markers": [],
        "recommended_ignore_patterns": [],
        "entry_points": [],
        "detected_dependencies": [],
    }

    # ── Language detection (priority order) ──
    _detect_python(ws, entries, result)
    _detect_node(ws, entries, result)
    _detect_rust(ws, entries, result)
    _detect_go(ws, entries, result)
    _detect_java(ws, entries, result)
    _detect_cpp(ws, entries, result)

    # ── Supplementary markers ──
    if "tsconfig.json" in entries:
        result["markers"].append("TypeScript")
        if result["language"] == "unknown":
            result["language"] = "typescript"

    if ".env" in entries or ".env.local" in entries:
        result["markers"].append("env config")

    if "Makefile" in entries:
        result["markers"].append("Makefile")

    if "README.md" in entries or "README.rst" in entries:
        result["markers"].append("has_readme")

    # ── Docker & CI details ──
    if result["has_docker"]:
        result["markers"].append("Docker")
        result["recommended_ignore_patterns"].append("*.log")

    if result["has_ci"]:
        result["markers"].append("CI/CD")

    # ── Deduplicate ──
    result["frameworks"] = list(dict.fromkeys(result["frameworks"]))
    result["markers"] = list(dict.fromkeys(result["markers"]))
    result["recommended_ignore_patterns"] = list(dict.fromkeys(result["recommended_ignore_patterns"]))

    return result


# ═══════════════════════════════════════════════════════════════
# Language-specific detectors
# ═══════════════════════════════════════════════════════════════


def _detect_python(ws: Path, entries: set, result: dict):
    """Detect Python project indicators."""
    py_markers = ["requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"]
    found = [m for m in py_markers if m in entries]
    if not found:
        return

    if result["language"] == "unknown":
        result["language"] = "python"
    result["markers"].append(f"Python ({', '.join(found)})")

    # Package manager
    if "Pipfile" in entries:
        result["package_manager"] = "pipenv"
    elif "pyproject.toml" in entries:
        result["package_manager"] = "pip/poetry"
    else:
        result["package_manager"] = "pip"

    # Ignore patterns
    result["recommended_ignore_patterns"].extend([
        "__pycache__/", "*.pyc", ".venv/", "*.egg-info/", ".mypy_cache/",
    ])

    # Detect frameworks from requirements/pyproject
    deps = _read_python_deps(ws, entries)
    result["detected_dependencies"] = deps

    frameworks_map = {
        "django": "Django",
        "fastapi": "FastAPI",
        "flask": "Flask",
        "starlette": "Starlette",
        "tornado": "Tornado",
        "celery": "Celery",
        "sqlalchemy": "SQLAlchemy",
        "pydantic": "Pydantic",
        "streamlit": "Streamlit",
        "gradio": "Gradio",
    }
    for dep in deps:
        dep_lower = dep.lower().split("[")[0].split(">=")[0].split("==")[0].strip()
        if dep_lower in frameworks_map:
            result["frameworks"].append(frameworks_map[dep_lower])

    # Test framework
    test_map = {"pytest": "pytest", "unittest": "unittest", "nose": "nose2", "tox": "tox"}
    for dep in deps:
        dep_lower = dep.lower().split("[")[0].split(">=")[0].split("==")[0].strip()
        if dep_lower in test_map:
            result["test_framework"] = test_map[dep_lower]
            break

    # Default to pytest if tests/ exists
    if not result["test_framework"]:
        if (ws / "tests").is_dir() or (ws / "test").is_dir():
            result["test_framework"] = "pytest"

    # Entry points
    for ep in ["manage.py", "app.py", "main.py", "run.py", "wsgi.py"]:
        if ep in entries:
            result["entry_points"].append(ep)


def _detect_node(ws: Path, entries: set, result: dict):
    """Detect Node.js/JavaScript project indicators."""
    if "package.json" not in entries:
        return

    if result["language"] == "unknown":
        result["language"] = "javascript"

    # Package manager
    if "bun.lockb" in entries:
        result["package_manager"] = "bun"
        result["markers"].append("Node.js (Bun)")
    elif "yarn.lock" in entries:
        result["package_manager"] = "yarn"
        result["markers"].append("Node.js (Yarn)")
    elif "pnpm-lock.yaml" in entries:
        result["package_manager"] = "pnpm"
        result["markers"].append("Node.js (pnpm)")
    else:
        result["package_manager"] = "npm"
        result["markers"].append("Node.js (npm)")

    result["recommended_ignore_patterns"].extend([
        "node_modules/", "dist/", ".next/", "build/",
    ])

    # Parse package.json for frameworks
    pkg = _read_package_json(ws)
    if pkg:
        all_deps = {}
        all_deps.update(pkg.get("dependencies", {}))
        all_deps.update(pkg.get("devDependencies", {}))

        node_frameworks = {
            "react": "React",
            "next": "Next.js",
            "vue": "Vue.js",
            "nuxt": "Nuxt.js",
            "@angular/core": "Angular",
            "svelte": "Svelte",
            "express": "Express",
            "fastify": "Fastify",
            "koa": "Koa",
            "nestjs": "NestJS",
            "@nestjs/core": "NestJS",
            "electron": "Electron",
            "tailwindcss": "Tailwind CSS",
        }
        for dep, name in node_frameworks.items():
            if dep in all_deps:
                result["frameworks"].append(name)

        # Test framework
        test_deps = {"jest": "Jest", "mocha": "Mocha", "vitest": "Vitest", "cypress": "Cypress"}
        for dep, name in test_deps.items():
            if dep in all_deps:
                result["test_framework"] = name
                break

        # TypeScript detection
        if "typescript" in all_deps:
            result["markers"].append("TypeScript")
            if result["language"] == "javascript":
                result["language"] = "typescript"


def _detect_rust(ws: Path, entries: set, result: dict):
    """Detect Rust project indicators."""
    if "Cargo.toml" not in entries:
        return
    if result["language"] == "unknown":
        result["language"] = "rust"
    result["package_manager"] = "cargo"
    result["markers"].append("Rust (Cargo)")
    result["test_framework"] = "cargo test"
    result["recommended_ignore_patterns"].extend(["target/", "*.rlib"])

    # Check for common frameworks
    cargo_text = _safe_read(ws / "Cargo.toml", max_bytes=8000)
    if cargo_text:
        if "actix" in cargo_text:
            result["frameworks"].append("Actix")
        if "tokio" in cargo_text:
            result["frameworks"].append("Tokio")
        if "axum" in cargo_text:
            result["frameworks"].append("Axum")
        if "rocket" in cargo_text:
            result["frameworks"].append("Rocket")


def _detect_go(ws: Path, entries: set, result: dict):
    """Detect Go project indicators."""
    if "go.mod" not in entries:
        return
    if result["language"] == "unknown":
        result["language"] = "go"
    result["package_manager"] = "go modules"
    result["markers"].append("Go (go.mod)")
    result["test_framework"] = "go test"
    result["recommended_ignore_patterns"].append("vendor/")

    # Check for common frameworks
    gomod_text = _safe_read(ws / "go.mod", max_bytes=4000)
    if gomod_text:
        if "gin-gonic" in gomod_text:
            result["frameworks"].append("Gin")
        if "echo" in gomod_text:
            result["frameworks"].append("Echo")
        if "fiber" in gomod_text:
            result["frameworks"].append("Fiber")


def _detect_java(ws: Path, entries: set, result: dict):
    """Detect Java/Kotlin project indicators."""
    if "pom.xml" in entries:
        if result["language"] == "unknown":
            result["language"] = "java"
        result["package_manager"] = "maven"
        result["markers"].append("Java (Maven)")
        result["test_framework"] = "JUnit"
        result["recommended_ignore_patterns"].extend(["target/", "*.class"])
    elif "build.gradle" in entries or "build.gradle.kts" in entries:
        if result["language"] == "unknown":
            result["language"] = "java"
        result["package_manager"] = "gradle"
        result["markers"].append("Java/Kotlin (Gradle)")
        result["test_framework"] = "JUnit"
        result["recommended_ignore_patterns"].extend(["build/", "*.class", ".gradle/"])


def _detect_cpp(ws: Path, entries: set, result: dict):
    """Detect C/C++ project indicators."""
    if "CMakeLists.txt" in entries:
        if result["language"] == "unknown":
            result["language"] = "cpp"
        result["package_manager"] = "cmake"
        result["markers"].append("C/C++ (CMake)")
        result["recommended_ignore_patterns"].extend(["build/", "*.o", "*.so", "*.a"])
    elif "Makefile" in entries and result["language"] == "unknown":
        result["language"] = "c/cpp"
        result["markers"].append("C/C++ (Makefile)")


# ═══════════════════════════════════════════════════════════════
# Helpers — dependency parsing
# ═══════════════════════════════════════════════════════════════


def _safe_read(path: Path, max_bytes: int = 8000) -> str:
    """Read a file up to max_bytes, return empty string on failure."""
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            return text[:max_bytes]
    except Exception:
        pass
    return ""


def _read_python_deps(ws: Path, entries: set) -> List[str]:
    """Extract Python dependency names from requirements.txt or pyproject.toml."""
    deps: List[str] = []

    # requirements.txt
    if "requirements.txt" in entries:
        text = _safe_read(ws / "requirements.txt")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                # Extract package name (before ==, >=, etc.)
                name = line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].split("[")[0].strip()
                if name:
                    deps.append(name)

    # pyproject.toml (basic parsing — look for dependencies list)
    if "pyproject.toml" in entries:
        text = _safe_read(ws / "pyproject.toml")
        in_deps = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped in ("dependencies = [", "[project.dependencies]",
                            "[tool.poetry.dependencies]"):
                in_deps = True
                continue
            if in_deps:
                if stripped.startswith("]") or (stripped.startswith("[") and "depend" not in stripped.lower()):
                    in_deps = False
                    continue
                # Extract quoted dependency name
                if '"' in stripped:
                    dep = stripped.strip('" ,').split(">=")[0].split("==")[0].split('"')[0].strip()
                    if dep and not dep.startswith("#"):
                        deps.append(dep)
                elif "=" in stripped and not stripped.startswith("["):
                    # poetry style: package = "^version"
                    name = stripped.split("=")[0].strip()
                    if name and name != "python":
                        deps.append(name)

    return list(dict.fromkeys(deps))  # deduplicate preserving order


def _read_package_json(ws: Path) -> Optional[Dict[str, Any]]:
    """Parse package.json, return dict or None."""
    try:
        text = (ws / "package.json").read_text(encoding="utf-8")
        result: Dict[str, Any] = json.loads(text)
        return result
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# Rule generation
# ═══════════════════════════════════════════════════════════════

# Language-specific rule templates
_RULES_TEMPLATES = {
    "python": {
        "style": "Follow PEP 8 conventions. Use type hints for function signatures.",
        "test": "Run tests with `pytest`. Prefer `pytest` fixtures over `setUp`/`tearDown`.",
        "imports": "Group imports: stdlib → third-party → local. Use absolute imports.",
    },
    "javascript": {
        "style": "Follow project ESLint config. Use consistent quotes and semicolons.",
        "test": "Run tests with the project's configured test runner.",
        "imports": "Group imports: packages → relative. Prefer named exports.",
    },
    "typescript": {
        "style": "Follow project TSConfig strict mode. Avoid `any` types.",
        "test": "Run tests with the project's configured test runner.",
        "imports": "Use ES module imports. Avoid circular dependencies.",
    },
    "rust": {
        "style": "Follow Rust conventions (snake_case, ownership). Run `cargo clippy`.",
        "test": "Run tests with `cargo test`. Use `#[cfg(test)]` modules.",
        "imports": "Use `use` statements at module top. Prefer explicit imports over globs.",
    },
    "go": {
        "style": "Follow Go conventions (gofmt, effective Go). Short variable names in limited scope.",
        "test": "Run tests with `go test ./...`. Use table-driven tests.",
        "imports": "Group: stdlib → external → internal. Use goimports.",
    },
    "java": {
        "style": "Follow project code style (Google Java Style or similar).",
        "test": "Run tests with Maven/Gradle. Use JUnit 5 assertions.",
        "imports": "Organize imports. Avoid wildcard imports.",
    },
}


def generate_nanobot_rules(project_info: Dict[str, Any]) -> str:
    """Generate a recommended NANOBOT.md content snippet from project detection results.

    Args:
        project_info: Dict returned by detect_project().

    Returns:
        Human-readable markdown text suitable for NANOBOT.md.
    """
    lang = project_info.get("language", "unknown")
    frameworks = project_info.get("frameworks", [])
    pkg_mgr = project_info.get("package_manager")
    test_fw = project_info.get("test_framework")
    ignore_patterns = project_info.get("recommended_ignore_patterns", [])
    entry_points = project_info.get("entry_points", [])

    sections: List[str] = []

    # Header
    sections.append("# Project Configuration (auto-detected)")
    sections.append("")

    # Tech stack summary
    stack_parts = []
    if lang != "unknown":
        stack_parts.append(f"**Language**: {lang.capitalize()}")
    if frameworks:
        stack_parts.append(f"**Frameworks**: {', '.join(frameworks)}")
    if pkg_mgr:
        stack_parts.append(f"**Package manager**: {pkg_mgr}")
    if test_fw:
        stack_parts.append(f"**Test framework**: {test_fw}")

    if stack_parts:
        sections.append("## Tech Stack")
        sections.extend(stack_parts)
        sections.append("")

    # Coding rules
    lang_key = lang if lang in _RULES_TEMPLATES else None
    if lang_key:
        rules = _RULES_TEMPLATES[lang_key]
        sections.append("## Coding Rules")
        sections.append(f"- {rules['style']}")
        sections.append(f"- {rules['imports']}")
        sections.append("")

    # Framework-specific guidance
    framework_rules = _get_framework_rules(frameworks)
    if framework_rules:
        sections.append("## Framework Guidelines")
        for rule in framework_rules:
            sections.append(f"- {rule}")
        sections.append("")

    # Testing
    if test_fw:
        sections.append("## Testing")
        test_rule = _RULES_TEMPLATES.get(lang_key or "", {}).get("test", "")
        if test_rule:
            sections.append(f"- {test_rule}")
        else:
            sections.append(f"- Run tests with `{test_fw}`.")
        sections.append("- Write tests for new functionality before merging.")
        sections.append("")

    # Ignore patterns
    if ignore_patterns:
        sections.append("## Ignore Patterns")
        sections.append("Do not read, modify, or suggest changes in:")
        for pat in ignore_patterns:
            sections.append(f"- `{pat}`")
        sections.append("")

    # Entry points
    if entry_points:
        sections.append("## Entry Points")
        for ep in entry_points:
            sections.append(f"- `{ep}`")
        sections.append("")

    return "\n".join(sections).strip()


def _get_framework_rules(frameworks: List[str]) -> List[str]:
    """Return framework-specific coding guidelines."""
    rules: List[str] = []
    fw_set = {f.lower() for f in frameworks}

    if "django" in fw_set:
        rules.append("Follow Django conventions: fat models, thin views. Use ORM instead of raw SQL.")
    if "fastapi" in fw_set:
        rules.append("Use Pydantic models for request/response validation. Prefer async endpoints.")
    if "flask" in fw_set:
        rules.append("Use Flask blueprints for modular routing. Register error handlers.")
    if "react" in fw_set:
        rules.append("Prefer functional components with hooks. Avoid prop drilling — use context or state management.")
    if "next.js" in fw_set:
        rules.append("Use App Router conventions. Prefer Server Components where possible.")
    if "vue.js" in fw_set:
        rules.append("Use Composition API with `<script setup>`. Follow Vue style guide priority A/B rules.")
    if "express" in fw_set:
        rules.append("Use middleware pattern. Handle errors with error-handling middleware.")
    if "tailwind css" in fw_set:
        rules.append("Use Tailwind utility classes. Avoid custom CSS unless absolutely necessary.")
    if "sqlalchemy" in fw_set:
        rules.append("Use SQLAlchemy 2.0 style. Prefer Session.execute() over legacy Query API.")
    if "celery" in fw_set:
        rules.append("Keep tasks idempotent. Use retry with exponential backoff.")

    return rules


# ═══════════════════════════════════════════════════════════════
# Convenience: one-shot scan + rules
# ═══════════════════════════════════════════════════════════════


def scan_workspace(workspace: str) -> Dict[str, Any]:
    """Full scan: detect project + generate rules. Returns API-ready dict."""
    info = detect_project(workspace)
    rules = generate_nanobot_rules(info)
    return {
        "success": "error" not in info,
        "workspace": workspace,
        "project_info": info,
        "suggested_rules": rules,
    }
