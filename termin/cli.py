"""Termin CLI: termin compile <file.termin> [-o output.py] [--backend fastapi]"""

import sys
from pathlib import Path

import click

from .parser import parse
from .analyzer import analyze
from .lower import lower
from .backends.fastapi import FastApiBackend


# Built-in backends
BACKENDS = {
    "fastapi": FastApiBackend,
}


@click.group()
def main():
    """Termin: A secure-by-construction application compiler."""
    pass


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.option("-o", "--output", default="app.py", help="Output file path")
@click.option("-b", "--backend", "backend_name", default="fastapi",
              help="Backend to use for code generation (default: fastapi)")
def compile(source: str, output: str, backend_name: str):
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
