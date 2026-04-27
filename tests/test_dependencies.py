# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests that setup.py declares all required third-party dependencies.

Scans import statements in termin/ and termin_runtime/ and verifies that
every third-party package is listed in setup.py install_requires.
Prevents the class of bug where a new dependency is used but not declared.
"""

import ast
import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent

# Packages that are part of the Python standard library (3.10+)
STDLIB = {
    "abc", "argparse", "ast", "asyncio", "base64", "builtins", "collections",
    "concurrent", "contextlib", "copy", "csv", "dataclasses", "datetime",
    "decimal", "enum", "fnmatch", "functools", "glob", "hashlib", "html",
    "http", "importlib", "inspect", "io", "itertools", "json", "logging",
    "math", "multiprocessing", "operator", "os", "pathlib", "pickle", "zipfile",
    "platform", "pprint", "queue", "random", "re", "shutil", "signal",
    "socket", "sqlite3", "string", "struct", "subprocess", "sys",
    "tempfile", "textwrap", "threading", "time", "traceback", "types",
    "typing", "unittest", "urllib", "uuid", "warnings", "weakref", "xml",
}

# Map of import name -> pip package name (when they differ)
IMPORT_TO_PACKAGE = {
    "click": "click",
    "tatsu": "tatsu",
    "jinja2": "jinja2",
    "celpy": "cel-python",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "aiosqlite": "aiosqlite",
    "websockets": "websockets",
    "starlette": "fastapi",  # starlette comes with fastapi
    "multipart": "python-multipart",
    "markdown_it": "markdown-it-py",  # v0.9 Step Zero: markdown sanitizer
}


def _get_setup_requires() -> set[str]:
    """Parse setup.py to extract install_requires package names."""
    setup_py = PROJECT_ROOT / "setup.py"
    text = setup_py.read_text(encoding="utf-8")
    # Extract package names from install_requires (strip version specifiers)
    names = set()
    in_requires = False
    for line in text.split("\n"):
        if "install_requires" in line:
            in_requires = True
            continue
        if in_requires:
            if "]" in line:
                in_requires = False
            m = re.search(r'"([a-zA-Z0-9_-]+)', line)
            if m:
                names.add(m.group(1).lower().replace("-", "_"))
    return names


def _scan_imports(package_dir: Path) -> set[str]:
    """Scan all .py files in a directory for third-party imports."""
    third_party = set()
    for py_file in package_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in STDLIB and not top.startswith("_"):
                        third_party.add(top)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:  # absolute imports only
                    top = node.module.split(".")[0]
                    if top not in STDLIB and not top.startswith("_"):
                        third_party.add(top)
    return third_party


class TestCompilerDependencies:
    """All third-party imports in termin/ must be in setup.py."""

    def test_compiler_deps_declared(self):
        declared = _get_setup_requires()
        imported = _scan_imports(PROJECT_ROOT / "termin")
        # Remove self-references (termin importing termin)
        imported -= {"termin", "termin_runtime"}

        missing = []
        for pkg in sorted(imported):
            pip_name = IMPORT_TO_PACKAGE.get(pkg, pkg).lower().replace("-", "_")
            if pip_name not in declared:
                missing.append(f"{pkg} (pip: {pip_name})")

        assert not missing, (
            f"Compiler imports these packages but setup.py doesn't declare them:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )


class TestRuntimeDependencies:
    """All third-party imports in termin_runtime/ must be in setup.py."""

    # Optional AI provider packages — imported lazily, only needed when configured
    OPTIONAL_RUNTIME_DEPS = {"anthropic", "openai"}

    def test_runtime_deps_declared(self):
        declared = _get_setup_requires()
        imported = _scan_imports(PROJECT_ROOT / "termin_runtime")
        # Remove self-references and optional deps
        imported -= {"termin", "termin_runtime"}
        imported -= self.OPTIONAL_RUNTIME_DEPS

        missing = []
        for pkg in sorted(imported):
            pip_name = IMPORT_TO_PACKAGE.get(pkg, pkg).lower().replace("-", "_")
            if pip_name not in declared:
                missing.append(f"{pkg} (pip: {pip_name})")

        assert not missing, (
            f"Runtime imports these packages but setup.py doesn't declare them:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )


# RuntimeBackend was retired in v0.9 — `.termin.pkg` is the
# canonical compile output and the runtime is consumed via the
# `termin_runtime` package install, not via codegen-emitted
# dependency lists. The old TestRuntimeBackendDependencies class
# is gone with it.
