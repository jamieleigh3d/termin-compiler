"""Termin CLI: termin compile <file.termin> [-o output.py] [--backend fastapi]"""

import json
import sys
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
        ir_json = json.dumps(asdict(spec), indent=2, default=_ir_json_default)
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

    # Write requirements.txt alongside output
    req_path = output_path.parent / "requirements.txt"
    if not req_path.exists():
        deps = backend.required_dependencies()
        req_path.write_text("\n".join(deps) + "\n", encoding="utf-8")
        click.echo(f"Created {req_path.name}")


if __name__ == "__main__":
    main()
