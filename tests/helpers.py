"""Shared test helpers for the termin-compiler test suite."""

import json
import zipfile
from pathlib import Path


def extract_ir_from_pkg(pkg_path: Path) -> dict:
    """Extract and parse IR JSON from a .termin.pkg ZIP package."""
    with zipfile.ZipFile(pkg_path, "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
        ir_bytes = zf.read(manifest["ir"]["entry"])
        return json.loads(ir_bytes)
