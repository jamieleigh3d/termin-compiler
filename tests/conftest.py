# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Shared pytest fixtures and helpers for the termin-compiler test suite.

The session-scoped compiled_packages fixture compiles all examples/*.termin
once per pytest run into a temporary directory and returns a {name: pkg_path}
dict. Tests that need IR JSON call extract_ir_from_pkg(pkg_path) to open the
package and extract the IR — no pre-compiled JSON files required.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from helpers import extract_ir_from_pkg

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


@pytest.fixture(scope="session")
def compiled_packages(tmp_path_factory):
    """Compile all examples/*.termin once per test session.

    Returns a dict mapping example name (stem, no extension) to the Path of
    the compiled .termin.pkg in a temporary directory. Compilation failures
    are surfaced as test failures immediately.
    """
    out_dir = tmp_path_factory.mktemp("compiled_packages")
    packages = {}
    for termin_file in sorted(EXAMPLES_DIR.glob("*.termin")):
        name = termin_file.stem
        pkg_out = out_dir / f"{name}.termin.pkg"
        cmd = [
            sys.executable, "-m", "termin.cli", "compile",
            str(termin_file), "-o", str(pkg_out),
        ]
        seed_path = EXAMPLES_DIR / f"{name}_seed.json"
        if seed_path.exists():
            cmd += ["--seed", str(seed_path)]
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=EXAMPLES_DIR.parent
        )
        if result.returncode != 0:
            pytest.fail(
                f"Compilation failed for {termin_file.name}:\n{result.stderr}"
            )
        packages[name] = pkg_out
    return packages
