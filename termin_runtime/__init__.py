# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Termin runtime — slice 7.3 of Phase 7 (2026-04-30) moved
the implementation to ``termin_server``. This module remains
as a re-export shim so existing
``from termin_runtime import create_termin_app`` keeps
working for v0.9. Drops in slice 7.5.
"""

from termin_server import create_termin_app  # noqa: F401

__all__ = ["create_termin_app"]
