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


def extract_seed_from_pkg(pkg_path: Path):
    """Extract and parse seed data dict from a .termin.pkg, or
    return None if the pkg has no seed."""
    with zipfile.ZipFile(pkg_path, "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
        if not manifest.get("seed"):
            return None
        try:
            return json.loads(zf.read(manifest["seed"]).decode("utf-8"))
        except (KeyError, json.JSONDecodeError):
            return None


def make_app_from_pkg(pkg_path: Path, db_path: str, **kwargs):
    """Build a TestClient-ready FastAPI app from a .termin.pkg.

    Replaces the legacy `python -m termin.cli compile -o app.py`
    + importlib pattern (retired in v0.9 along with
    RuntimeBackend). Most tests want:

        from helpers import make_app_from_pkg
        app = make_app_from_pkg(pkg, db_path=str(tmp_path / "app.db"))
        with TestClient(app) as client:
            ...
    """
    from termin_server import create_termin_app
    ir_json = json.dumps(extract_ir_from_pkg(pkg_path))
    seed_data = extract_seed_from_pkg(pkg_path)
    kwargs.setdefault("strict_channels", False)
    return create_termin_app(
        ir_json, seed_data=seed_data, db_path=db_path, **kwargs)
