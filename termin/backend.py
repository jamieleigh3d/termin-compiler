# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Backend protocol and plugin discovery for the Termin compiler.

The compiler's first-party output is `.termin.pkg` (built directly
from the AppSpec IR by `termin.cli.compile`). Third-party backends
that emit foreign-language outputs (e.g., a Go-runtime emitter, a
Postgres-DDL emitter, etc.) implement the Backend protocol and
register via entry points:

    [project.entry-points."termin.backends"]
    my_backend = "my_pkg.backend:MyBackend"

The CLI discovers backends at runtime via importlib.metadata.

The historical first-party "runtime" backend that emitted a slim
`app.py` shell was retired in v0.9 — `.termin.pkg` is the canonical
output and `termin serve <pkg>` is the canonical run command.
"""

from typing import Protocol, runtime_checkable
from .ir import AppSpec


@runtime_checkable
class Backend(Protocol):
    """Interface that all Termin backends must implement."""

    name: str

    def generate(self, spec: AppSpec, source_file: str = "") -> str:
        """Generate output from the given AppSpec.

        Returns the generated code/configuration as a string.
        """
        ...

    def required_dependencies(self) -> list[str]:
        """Return pip package names needed to run the generated output."""
        ...


def discover_backends() -> dict[str, type]:
    """Discover installed backends via entry points."""
    import importlib.metadata
    backends = {}
    try:
        eps = importlib.metadata.entry_points()
        # Python 3.12+ returns a SelectableGroups, 3.10-3.11 returns a dict
        if hasattr(eps, 'select'):
            group = eps.select(group="termin.backends")
        else:
            group = eps.get("termin.backends", [])
        for ep in group:
            try:
                backends[ep.name] = ep.load()
            except Exception:
                pass
    except Exception:
        pass
    return backends


def get_backend(name: str) -> Backend:
    """Get a backend instance by name. Falls back to built-in backends."""
    # Try entry points first
    backends = discover_backends()
    if name in backends:
        cls = backends[name]
        return cls()

    raise ValueError(f"Unknown backend: {name}. Available: {list(backends.keys())}")
