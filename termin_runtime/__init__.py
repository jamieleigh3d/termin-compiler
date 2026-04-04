"""Termin Runtime — the execution environment for compiled Termin applications.

Usage:
    from termin_runtime import create_termin_app
    app = create_termin_app(ir_json_string)
"""

from .app import create_termin_app

__all__ = ["create_termin_app"]
