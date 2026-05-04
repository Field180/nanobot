"""
P7: Tests for project_detector.py — project detection and rule generation.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_WEB_UI = Path(__file__).resolve().parent.parent
if str(_WEB_UI) not in sys.path:
    sys.path.insert(0, str(_WEB_UI))

from project_detector import detect_project, generate_nanobot_rules, scan_workspace


class TestDetectPython(unittest.TestCase):
    """Python project detection."""

    def test_requirements_txt(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "requirements.txt").write_text("flask>=2.0\npytest\ncelery\n")
            (Path(td) / ".git").mkdir()
            info = detect_project(td)
            self.assertEqual(info["language"], "python")
            self.assertEqual(info["package_manager"], "pip")
            self.assertIn("Flask", info["frameworks"])
            self.assertIn("Celery", info["frameworks"])
            self.assertEqual(info["test_framework"], "pytest")
            self.assertTrue(info["has_git"])
            self.assertIn("__pycache__/", info["recommended_ignore_patterns"])

    def test_pyproject_toml(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "pyproject.toml").write_text('[project]\nname = "myapp"\n')
            info = detect_project(td)
            self.assertEqual(info["language"], "python")
            self.assertEqual(info["package_manager"], "pip/poetry")

    def test_fastapi_detection(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "requirements.txt").write_text("fastapi>=0.100\nuvicorn\npydantic\n")
            info = detect_project(td)
            self.assertIn("FastAPI", info["frameworks"])
            self.assertIn("Pydantic", info["frameworks"])

    def test_django_detection(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "requirements.txt").write_text("django>=4.0\n")
            (Path(td) / "manage.py").write_text("#!/usr/bin/env python\n")
            info = detect_project(td)
            self.assertIn("Django", info["frameworks"])
            self.assertIn("manage.py", info["entry_points"])

    def test_pipenv(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "Pipfile").write_text("[packages]\nflask = '*'\n")
            info = detect_project(td)
            self.assertEqual(info["package_manager"], "pipenv")

    def test_pytest_from_tests_dir(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "setup.py").write_text("from setuptools import setup\n")
            (Path(td) / "tests").mkdir()
            info = detect_project(td)
            self.assertEqual(info["test_framework"], "pytest")


class TestDetectNode(unittest.TestCase):
    """Node.js/JavaScript project detection."""

    def test_npm_react(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = {"dependencies": {"react": "^18.0", "next": "^14.0"},
                   "devDependencies": {"jest": "^29.0", "typescript": "^5.0"}}
            (Path(td) / "package.json").write_text(json.dumps(pkg))
            info = detect_project(td)
            self.assertEqual(info["language"], "typescript")  # typescript in devDeps
            self.assertEqual(info["package_manager"], "npm")
            self.assertIn("React", info["frameworks"])
            self.assertIn("Next.js", info["frameworks"])
            self.assertEqual(info["test_framework"], "Jest")
            self.assertIn("node_modules/", info["recommended_ignore_patterns"])

    def test_yarn(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = {"dependencies": {"vue": "^3.0"}}
            (Path(td) / "package.json").write_text(json.dumps(pkg))
            (Path(td) / "yarn.lock").write_text("")
            info = detect_project(td)
            self.assertEqual(info["package_manager"], "yarn")
            self.assertIn("Vue.js", info["frameworks"])

    def test_pnpm(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = {"dependencies": {"express": "^4.0"}, "devDependencies": {"vitest": "^1.0"}}
            (Path(td) / "package.json").write_text(json.dumps(pkg))
            (Path(td) / "pnpm-lock.yaml").write_text("")
            info = detect_project(td)
            self.assertEqual(info["package_manager"], "pnpm")
            self.assertIn("Express", info["frameworks"])
            self.assertEqual(info["test_framework"], "Vitest")

    def test_bun(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = {"dependencies": {"@nestjs/core": "^10.0"}}
            (Path(td) / "package.json").write_text(json.dumps(pkg))
            (Path(td) / "bun.lockb").write_text("")
            info = detect_project(td)
            self.assertEqual(info["package_manager"], "bun")
            self.assertIn("NestJS", info["frameworks"])

    def test_tailwind(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = {"devDependencies": {"tailwindcss": "^3.0"}}
            (Path(td) / "package.json").write_text(json.dumps(pkg))
            info = detect_project(td)
            self.assertIn("Tailwind CSS", info["frameworks"])


class TestDetectRust(unittest.TestCase):
    """Rust project detection."""

    def test_basic_rust(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "Cargo.toml").write_text('[package]\nname = "myapp"\n\n[dependencies]\ntokio = "1"\nactix-web = "4"\n')
            info = detect_project(td)
            self.assertEqual(info["language"], "rust")
            self.assertEqual(info["package_manager"], "cargo")
            self.assertEqual(info["test_framework"], "cargo test")
            self.assertIn("Actix", info["frameworks"])
            self.assertIn("Tokio", info["frameworks"])
            self.assertIn("target/", info["recommended_ignore_patterns"])


class TestDetectGo(unittest.TestCase):
    """Go project detection."""

    def test_basic_go(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "go.mod").write_text("module myapp\n\nrequire github.com/gin-gonic/gin v1.9\n")
            info = detect_project(td)
            self.assertEqual(info["language"], "go")
            self.assertEqual(info["package_manager"], "go modules")
            self.assertEqual(info["test_framework"], "go test")
            self.assertIn("Gin", info["frameworks"])


class TestDetectJava(unittest.TestCase):
    """Java project detection."""

    def test_maven(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "pom.xml").write_text("<project></project>")
            info = detect_project(td)
            self.assertEqual(info["language"], "java")
            self.assertEqual(info["package_manager"], "maven")
            self.assertEqual(info["test_framework"], "JUnit")

    def test_gradle(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "build.gradle.kts").write_text("plugins { }")
            info = detect_project(td)
            self.assertEqual(info["language"], "java")
            self.assertEqual(info["package_manager"], "gradle")


class TestDetectCpp(unittest.TestCase):
    """C/C++ project detection."""

    def test_cmake(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)")
            info = detect_project(td)
            self.assertEqual(info["language"], "cpp")
            self.assertEqual(info["package_manager"], "cmake")
            self.assertIn("build/", info["recommended_ignore_patterns"])


class TestDetectMixed(unittest.TestCase):
    """Multi-language and edge cases."""

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            info = detect_project(td)
            self.assertEqual(info["language"], "unknown")
            self.assertEqual(info["frameworks"], [])

    def test_nonexistent_dir(self):
        info = detect_project("/nonexistent/path/xyz123")
        self.assertEqual(info["language"], "unknown")
        self.assertIn("error", info)

    def test_docker_and_ci(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "Dockerfile").write_text("FROM python:3.12")
            (Path(td) / ".github").mkdir()
            info = detect_project(td)
            self.assertTrue(info["has_docker"])
            self.assertTrue(info["has_ci"])
            self.assertIn("Docker", info["markers"])
            self.assertIn("CI/CD", info["markers"])

    def test_python_plus_node(self):
        """Multi-language project — Python detected first."""
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "requirements.txt").write_text("flask\n")
            pkg = {"dependencies": {"react": "^18.0"}}
            (Path(td) / "package.json").write_text(json.dumps(pkg))
            info = detect_project(td)
            # Python is detected first (priority)
            self.assertEqual(info["language"], "python")
            # But Node frameworks should still be detected
            self.assertIn("React", info["frameworks"])
            self.assertIn("Flask", info["frameworks"])

    def test_typescript_only(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "tsconfig.json").write_text("{}")
            info = detect_project(td)
            self.assertEqual(info["language"], "typescript")
            self.assertIn("TypeScript", info["markers"])


class TestGenerateRules(unittest.TestCase):
    """Test NANOBOT.md rule generation."""

    def test_python_rules(self):
        info = {
            "language": "python",
            "frameworks": ["FastAPI", "Pydantic"],
            "package_manager": "pip",
            "test_framework": "pytest",
            "recommended_ignore_patterns": ["__pycache__/", "*.pyc"],
            "entry_points": ["main.py"],
        }
        rules = generate_nanobot_rules(info)
        self.assertIn("Python", rules)
        self.assertIn("FastAPI", rules)
        self.assertIn("PEP 8", rules)
        self.assertIn("pytest", rules)
        self.assertIn("__pycache__/", rules)
        self.assertIn("main.py", rules)
        self.assertIn("Pydantic models", rules)

    def test_node_react_rules(self):
        info = {
            "language": "typescript",
            "frameworks": ["React", "Next.js", "Tailwind CSS"],
            "package_manager": "npm",
            "test_framework": "Jest",
            "recommended_ignore_patterns": ["node_modules/", "dist/"],
            "entry_points": [],
        }
        rules = generate_nanobot_rules(info)
        self.assertIn("Typescript", rules)
        self.assertIn("React", rules)
        self.assertIn("functional components", rules)
        self.assertIn("Tailwind utility classes", rules)
        self.assertIn("node_modules/", rules)

    def test_unknown_project(self):
        info = {"language": "unknown", "frameworks": [], "package_manager": None,
                "test_framework": None, "recommended_ignore_patterns": [], "entry_points": []}
        rules = generate_nanobot_rules(info)
        # Should still produce something (header at minimum)
        self.assertIn("auto-detected", rules)

    def test_go_rules(self):
        info = {
            "language": "go",
            "frameworks": ["Gin"],
            "package_manager": "go modules",
            "test_framework": "go test",
            "recommended_ignore_patterns": ["vendor/"],
            "entry_points": [],
        }
        rules = generate_nanobot_rules(info)
        self.assertIn("Go", rules)
        self.assertIn("go test", rules)
        self.assertIn("gofmt", rules)

    def test_rust_rules(self):
        info = {
            "language": "rust",
            "frameworks": ["Tokio", "Axum"],
            "package_manager": "cargo",
            "test_framework": "cargo test",
            "recommended_ignore_patterns": ["target/"],
            "entry_points": [],
        }
        rules = generate_nanobot_rules(info)
        self.assertIn("Rust", rules)
        self.assertIn("cargo test", rules)
        self.assertIn("cargo clippy", rules)


class TestScanWorkspace(unittest.TestCase):
    """Test the combined scan_workspace function."""

    def test_full_scan(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "requirements.txt").write_text("django>=4.0\n")
            (Path(td) / ".git").mkdir()
            result = scan_workspace(td)
            self.assertTrue(result["success"])
            self.assertEqual(result["workspace"], td)
            self.assertEqual(result["project_info"]["language"], "python")
            self.assertIn("Django", result["suggested_rules"])

    def test_invalid_path(self):
        result = scan_workspace("/nonexistent/abc123")
        self.assertFalse(result["success"])


class TestTuiIntegration(unittest.TestCase):
    """Test TUI project scanning integration."""

    def test_scan_project_function(self):
        from tui import _scan_project
        # Scan the actual workspace
        result = _scan_project(Path("/home/field/.nanobot/workspace"))
        # Should detect Python at minimum (we have .py files)
        self.assertIn("Python", result)

    def test_scan_project_empty_dir(self):
        from tui import _scan_project
        with tempfile.TemporaryDirectory() as td:
            result = _scan_project(Path(td))
            self.assertEqual(result, "")

    def test_scan_project_invalid(self):
        from tui import _scan_project
        result = _scan_project(Path("/nonexistent/xyz"))
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
