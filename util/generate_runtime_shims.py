"""One-shot helper for slice 7.3 — replace every termin_runtime/*.py
file with a re-export shim that forwards to termin_server.X.

Run from the termin-compiler repo root. Idempotent (re-running on a
tree that already contains shims rewrites them with the same content).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Modules whose tests import underscore-prefixed names directly. The
# shim must re-export those names explicitly because `from X import *`
# skips underscore names by convention.
EXPLICIT_UNDERSCORE = {
    "compute_runner": [
        "_build_llm_prompts",
        "_build_agent_prompts",
        "_build_agent_set_output",
        "_execute_agent_compute",
        "_resolve_directive_at_invocation",
        "_build_llm_audit_metadata",
        "_build_agent_audit_metadata",
    ],
    "storage": ["_q"],
    "app": [
        "_populate_presentation_providers",
        "_load_contract_packages",
        "_resolve_directive_sources",
    ],
    "identity": [
        "_build_user_dict",
        "_build_the_user_object",
        "_hydrate_principal_preferences",
        "_resolve_principal_and_scopes",
    ],
    "websocket_manager": ["_filter_owned_rows"],
}


HEADER = (
    "# Copyright 2026 Jamie-Leigh Blake and Termin project contributors\n"
    "# Licensed under the Apache License, Version 2.0 (the \"License\");\n"
    "# you may not use this file except in compliance with the License.\n"
    "\n"
)


def shim_for(dotted_path: str, underscores: list[str] | None = None) -> str:
    """Build the shim text for a single module.

    ``dotted_path`` is the path under termin_server (e.g. "app",
    "providers.builtins.storage_sqlite"). The shim does
    ``from termin_server.X import *`` plus a PEP 562 ``__getattr__``
    that forwards every other attribute (including underscore-prefixed
    helpers tests reach into directly). Same shim works for every
    module — no per-module underscore list needed.
    """
    body = (
        f'"""Slice 7.3 of Phase 7 (2026-04-30) moved this module to\n'
        f'``termin_server.{dotted_path}``. Re-export shim — drops in slice 7.5.\n\n'
        f'``from termin_server.{dotted_path} import *`` carries the public\n'
        f'API; the PEP 562 ``__getattr__`` forwards the rest (underscore-\n'
        f'prefixed helpers tests reach into directly, and any name added\n'
        f'after this shim was generated).\n'
        f'"""\n\n'
        f'from termin_server.{dotted_path} import *  # noqa: F401, F403\n'
        f'import termin_server.{dotted_path} as _src\n'
        f'\n'
        f'\n'
        f'def __getattr__(name):\n'
        f'    return getattr(_src, name)\n'
    )
    return HEADER + body


def package_init_shim(dotted_path: str) -> str:
    """Build the __init__.py shim for a package (also forwards subpackages
    and underscore names via PEP 562)."""
    body = (
        f'"""Slice 7.3 of Phase 7 (2026-04-30) moved this package to\n'
        f'``termin_server.{dotted_path}``. Re-export shim — drops in slice 7.5.\n'
        f'"""\n\n'
        f'from termin_server.{dotted_path} import *  # noqa: F401, F403\n'
        f'import termin_server.{dotted_path} as _src\n'
        f'\n'
        f'\n'
        f'def __getattr__(name):\n'
        f'    return getattr(_src, name)\n'
    )
    return HEADER + body


def main() -> int:
    root = Path("termin_runtime")
    if not root.is_dir():
        print(f"FAIL: not in termin-compiler repo (no {root}/)", file=sys.stderr)
        return 1

    rewritten = 0
    skipped = 0

    for py in sorted(root.rglob("*.py")):
        rel = py.relative_to(root)
        # dotted: "app", "providers.builtins.storage_sqlite", etc.
        parts = list(rel.with_suffix("").parts)
        is_init = parts[-1] == "__init__"
        if is_init:
            parts = parts[:-1]
            if not parts:
                # Top-level termin_runtime/__init__.py — keep its
                # special create_termin_app re-export, but route
                # through termin_server.
                content = HEADER + (
                    '"""Termin runtime — slice 7.3 of Phase 7 (2026-04-30) moved\n'
                    'the implementation to ``termin_server``. This module remains\n'
                    'as a re-export shim so existing\n'
                    '``from termin_runtime import create_termin_app`` keeps\n'
                    'working for v0.9. Drops in slice 7.5.\n'
                    '"""\n\n'
                    'from termin_server import create_termin_app  # noqa: F401\n\n'
                    '__all__ = ["create_termin_app"]\n'
                )
                py.write_text(content, encoding="utf-8")
                rewritten += 1
                continue
            dotted = ".".join(parts)
            content = package_init_shim(dotted)
        else:
            dotted = ".".join(parts)
            underscores = EXPLICIT_UNDERSCORE.get(dotted)
            content = shim_for(dotted, underscores)

        py.write_text(content, encoding="utf-8")
        rewritten += 1

    print(f"Rewrote {rewritten} files. Skipped {skipped}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
