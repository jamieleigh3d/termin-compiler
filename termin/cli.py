"""Termin CLI: termin compile <file.termin> [-o output.py] [--backend fastapi]"""

import json
import sys
import uuid
from dataclasses import asdict
from enum import Enum
from pathlib import Path

import click

from .peg_parser import parse_peg as parse
from .analyzer import analyze
from .lower import lower
from .backends.fastapi import FastApiBackend
from .backends.runtime import RuntimeBackend


# Built-in backends
BACKENDS = {
    "fastapi": FastApiBackend,
    "runtime": RuntimeBackend,
}


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


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("-o", "--output", default="app.py", help="Output file path")
@click.option("-b", "--backend", "backend_name", default="fastapi",
              help="Backend to use for code generation (default: fastapi)")
@click.option("--emit-ir", "ir_output", default=None, type=click.Path(),
              help="Dump the AppSpec IR as JSON to this file")
def compile(source: str, output: str, backend_name: str, ir_output: str | None):
    """Compile a .termin file into an application."""
    source_path = Path(source)
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
        # Write ID back to source file (if writable)
        try:
            if source_path.exists() and not source_path.name.startswith("<"):
                # Find insertion point: after Description: line, or after Application: line
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
                    # Re-read and re-parse with the ID now present
                    source_text = source_path.read_text(encoding="utf-8")
                    program, parse_errors = parse(source_text)
        except (PermissionError, OSError):
            click.echo(f"Warning: Could not write app ID back to {source_path.name} (read-only)", err=True)
            click.echo(f"Add this line to your .termin file: Id: {new_id}", err=True)

    # Error if source is non-writable (stdin, pipe) and has no ID
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

    # Optionally dump IR
    if ir_output:
        ir_path = Path(ir_output)
        ir_dict = asdict(spec)
        _simplify_props(ir_dict)
        ir_json = json.dumps(ir_dict, indent=2, default=_ir_json_default)
        ir_path.write_text(ir_json, encoding="utf-8")
        click.echo(f"IR dumped to {ir_path.name}")

    # Get backend
    if backend_name in BACKENDS:
        backend = BACKENDS[backend_name]()
    else:
        # Try plugin discovery
        from .backend import get_backend
        try:
            backend = get_backend(backend_name)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    # Generate
    code = backend.generate(spec, source_file=source_path.name)

    # Write output
    output_path = Path(output)
    output_path.write_text(code, encoding="utf-8")
    click.echo(f"Compiled {source_path.name} -> {output_path.name}")

    # Write companion IR JSON for runtime backend
    if hasattr(backend, '_ir_json'):
        ir_companion = output_path.with_suffix(".json")
        ir_companion.write_text(backend._ir_json, encoding="utf-8")
        click.echo(f"IR written to {ir_companion.name}")

    # Copy seed data file if it exists alongside the source
    seed_source = source_path.with_name(source_path.stem + "_seed.json")
    if seed_source.exists():
        seed_dest = output_path.with_name(output_path.stem + "_seed.json")
        seed_dest.write_text(seed_source.read_text(encoding="utf-8"), encoding="utf-8")
        click.echo(f"Seed data: {seed_dest.name}")

    # Write requirements.txt alongside output
    req_path = output_path.parent / "requirements.txt"
    if not req_path.exists():
        deps = backend.required_dependencies()
        req_path.write_text("\n".join(deps) + "\n", encoding="utf-8")
        click.echo(f"Created {req_path.name}")


if __name__ == "__main__":
    main()
