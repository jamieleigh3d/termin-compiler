"""Termin CLI: termin compile, termin serve"""

import hashlib
import json
import sys
import uuid
import zipfile
from dataclasses import asdict
from enum import Enum
from pathlib import Path

import click

from .peg_parser import parse_peg as parse
from .analyzer import analyze
from .lower import lower
from .backends.runtime import RuntimeBackend


@click.group()
def main():
    """Termin: A secure-by-construction application compiler."""
    pass


def _ir_json_default(obj):
    """JSON serializer for IR dataclasses (Enums, frozensets)."""
    if isinstance(obj, Enum):
        return obj.name
    if isinstance(obj, (frozenset, set)):
        return sorted((o.name if isinstance(o, Enum) else o) for o in obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _simplify_props(obj):
    """Simplify PropValue dicts: {value: x, is_expr: false} → bare x."""
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, dict) and set(v.keys()) == {"value", "is_expr"}:
                if not v["is_expr"]:
                    obj[k] = v["value"]
            elif isinstance(v, (dict, list)):
                _simplify_props(v)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, dict) and set(item.keys()) == {"value", "is_expr"}:
                if not item["is_expr"]:
                    obj[i] = item["value"]
            elif isinstance(item, (dict, list)):
                _simplify_props(item)


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _compile_source(source_path: Path):
    """Compile a .termin source file. Returns (program, spec, source_text)."""
    source_text = source_path.read_text(encoding="utf-8")

    # Parse
    program, parse_errors = parse(source_text)
    if not parse_errors.ok:
        click.echo(parse_errors.format(), err=True)
        sys.exit(1)

    # Generate and write back app ID if missing
    if program.application and not program.application.app_id:
        new_id = str(uuid.uuid4())
        program.application.app_id = new_id
        try:
            if source_path.exists() and not source_path.name.startswith("<"):
                lines = source_text.splitlines(keepends=True)
                insert_idx = None
                for idx, line in enumerate(lines):
                    stripped = line.strip().lower()
                    if stripped.startswith("description:"):
                        insert_idx = idx + 1
                    elif stripped.startswith("application:") and insert_idx is None:
                        insert_idx = idx + 1
                if insert_idx is not None:
                    lines.insert(insert_idx, f"Id: {new_id}\n")
                    source_path.write_text("".join(lines), encoding="utf-8")
                    click.echo(f"Generated app ID: {new_id}")
                    source_text = source_path.read_text(encoding="utf-8")
                    program, parse_errors = parse(source_text)
        except (PermissionError, OSError):
            click.echo(f"Warning: Could not write app ID back to {source_path.name} (read-only)", err=True)
            click.echo(f"Add this line to your .termin file: Id: {new_id}", err=True)

    if program.application and not program.application.app_id:
        click.echo("Error: Source file has no Id: field and is not writable.", err=True)
        click.echo("Add an Id: line to your .termin file header (e.g., Id: " + str(uuid.uuid4()) + ")", err=True)
        sys.exit(1)

    # Analyze
    analysis_errors = analyze(program)
    if not analysis_errors.ok:
        click.echo(analysis_errors.format(), err=True)
        sys.exit(1)

    # Lower to IR
    spec = lower(program)

    return program, spec, source_text


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("-o", "--output", default=None, help="Output path (default: <name>.termin.pkg)")
@click.option("--seed", "seed_path", default=None, type=click.Path(exists=True),
              help="Seed data JSON file to include in package")
@click.option("--assets", "assets_path", default=None, type=click.Path(exists=True),
              help="Assets directory to include in package")
@click.option("--version", "app_version", default=None, help="Set app version (semver)")
@click.option("--emit-ir", "ir_output", default=None, type=click.Path(),
              help="Also dump the IR JSON to this file (for debugging)")
@click.option("--legacy", is_flag=True, help="Output legacy .py + .json instead of .termin.pkg")
def compile(source: str, output: str | None, seed_path: str | None,
            assets_path: str | None, app_version: str | None,
            ir_output: str | None, legacy: bool):
    """Compile a .termin file into a .termin.pkg package."""
    source_path = Path(source)
    program, spec, source_text = _compile_source(source_path)

    # Build IR JSON
    ir_dict = asdict(spec)
    _simplify_props(ir_dict)
    ir_json = json.dumps(ir_dict, indent=2, default=_ir_json_default)

    # Optionally dump IR separately
    if ir_output:
        ir_path = Path(ir_output)
        ir_path.write_text(ir_json, encoding="utf-8")
        click.echo(f"IR dumped to {ir_path.name}")

    # Legacy mode: output .py + .json (backward compat)
    # Auto-detect: if output path ends in .py, use legacy mode
    if output and Path(output).suffix == ".py":
        legacy = True
    if legacy:
        backend = RuntimeBackend()
        code = backend.generate(spec, source_file=source_path.name)
        output_path = Path(output or "app.py")
        output_path.write_text(code, encoding="utf-8")
        click.echo(f"Compiled {source_path.name} -> {output_path.name}")
        if hasattr(backend, '_ir_json'):
            ir_companion = output_path.with_suffix(".json")
            ir_companion.write_text(backend._ir_json, encoding="utf-8")
            click.echo(f"IR written to {ir_companion.name}")
        seed_source = source_path.with_name(source_path.stem + "_seed.json")
        if seed_source.exists():
            seed_dest = output_path.with_name(output_path.stem + "_seed.json")
            seed_dest.write_text(seed_source.read_text(encoding="utf-8"), encoding="utf-8")
            click.echo(f"Seed data: {seed_dest.name}")
        return

    # ── Build .termin.pkg ──
    stem = source_path.stem
    pkg_path = Path(output or f"{stem}.termin.pkg")

    # Determine revision: load existing .pkg if present
    revision = 1
    existing_version = "1.0.0"
    if pkg_path.exists():
        try:
            with zipfile.ZipFile(pkg_path, 'r') as zf:
                old_manifest = json.loads(zf.read("manifest.json"))
                old_id = old_manifest.get("app", {}).get("id", "")
                current_id = spec.app_id or ""
                if old_id == current_id:
                    revision = old_manifest.get("app", {}).get("revision", 0) + 1
                    existing_version = old_manifest.get("app", {}).get("version", "1.0.0")
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError):
            pass  # Corrupted or non-pkg file — start fresh

    # Resolve version
    version = app_version or existing_version

    # Resolve seed data
    seed_data = None
    seed_filename = None
    if seed_path:
        seed_filename = Path(seed_path).name
        seed_data = Path(seed_path).read_bytes()
    else:
        auto_seed = source_path.with_name(source_path.stem + "_seed.json")
        if auto_seed.exists():
            seed_filename = auto_seed.name
            seed_data = auto_seed.read_bytes()

    # Build file contents
    ir_filename = f"{stem}.ir.json"
    source_filename = source_path.name
    ir_bytes = ir_json.encode("utf-8")
    source_bytes = source_text.encode("utf-8")

    # Build checksums
    checksums = {
        ir_filename: _sha256(ir_bytes),
        source_filename: _sha256(source_bytes),
    }
    if seed_data and seed_filename:
        checksums[seed_filename] = _sha256(seed_data)

    # Build manifest
    manifest = {
        "manifest_version": "1.0.0",
        "app": {
            "id": spec.app_id,
            "name": spec.name,
            "version": version,
            "revision": revision,
            "description": spec.description,
        },
        "ir": {
            "version": spec.ir_version,
            "entry": ir_filename,
        },
        "source": {
            "files": [source_filename],
            "entry": source_filename,
        },
        "seed": seed_filename,
        "assets": None,
        "checksums": checksums,
    }

    # Write ZIP
    with zipfile.ZipFile(pkg_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr(ir_filename, ir_json)
        zf.writestr(source_filename, source_text)
        if seed_data and seed_filename:
            zf.writestr(seed_filename, seed_data)
        # TODO: assets directory

    click.echo(f"Compiled {source_path.name} -> {pkg_path.name} "
               f"(v{version} rev{revision}, id={spec.app_id[:8]}...)")


@main.command()
@click.argument("package", type=click.Path(exists=True))
@click.option("-p", "--port", default=8000, type=int, help="Port to serve on (default: 8000)")
@click.option("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
@click.option("--deploy", default=None, type=click.Path(exists=True), help="Deploy config file (default: {app_name}.deploy.json)")
@click.option("--no-strict-channels", is_flag=True, help="Allow missing deploy config for external channels (dev mode)")
def serve(package: str, port: int, host: str, deploy: str, no_strict_channels: bool):
    """Serve a .termin.pkg package as a running application."""
    import uvicorn
    from termin_runtime import create_termin_app

    pkg_path = Path(package)

    # Read package
    if pkg_path.suffix == ".pkg" or pkg_path.name.endswith(".termin.pkg"):
        # .termin.pkg format
        with zipfile.ZipFile(pkg_path, 'r') as zf:
            manifest = json.loads(zf.read("manifest.json"))
            ir_json = zf.read(manifest["ir"]["entry"]).decode("utf-8")
            seed_data = None
            if manifest.get("seed"):
                try:
                    seed_json = zf.read(manifest["seed"]).decode("utf-8")
                    seed_data = json.loads(seed_json)
                except (KeyError, json.JSONDecodeError):
                    pass

        app_name = manifest["app"]["name"]
        app_version = manifest["app"]["version"]
        revision = manifest["app"]["revision"]
        click.echo(f"[Termin] Loading {app_name} v{app_version} (rev{revision})")

        # Verify checksums
        with zipfile.ZipFile(pkg_path, 'r') as zf:
            checksums = manifest.get("checksums", {})
            for filename, expected in checksums.items():
                actual = _sha256(zf.read(filename))
                if actual != expected:
                    click.echo(f"WARNING: Checksum mismatch for {filename}", err=True)

    elif pkg_path.suffix == ".json":
        # Raw IR JSON (backward compat)
        ir_json = pkg_path.read_text(encoding="utf-8")
        seed_data = None
        seed_path = pkg_path.with_name(pkg_path.stem.replace("_ir", "") + "_seed.json")
        if seed_path.exists():
            seed_data = json.loads(seed_path.read_text(encoding="utf-8"))
        ir = json.loads(ir_json)
        app_name = ir.get("name", "App")
        click.echo(f"[Termin] Loading {app_name} from raw IR")
    else:
        click.echo(f"Error: Unrecognized file type: {pkg_path.name}", err=True)
        click.echo("Expected .termin.pkg or .json", err=True)
        sys.exit(1)

    # Create and serve
    try:
        app = create_termin_app(
            ir_json, seed_data=seed_data,
            deploy_config_path=deploy,
            strict_channels=not no_strict_channels,
        )
    except Exception as e:
        if "channel" in str(e).lower() and "deploy config" in str(e).lower():
            click.echo(f"\nError: {e}", err=True)
            click.echo(f"\nTo fix: create a deploy config file or use --no-strict-channels for dev mode.", err=True)
            sys.exit(1)
        raise

    click.echo(f"[Termin] Serving on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
