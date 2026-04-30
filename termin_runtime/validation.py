# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Slice 7.3 of Phase 7 (2026-04-30) moved this module to
``termin_server.validation``. Re-export shim — drops in slice 7.5.

``from termin_server.validation import *`` carries the public
API; the PEP 562 ``__getattr__`` forwards the rest (underscore-
prefixed helpers tests reach into directly, and any name added
after this shim was generated).
"""

from termin_server.validation import *  # noqa: F401, F403
import termin_server.validation as _src


def __getattr__(name):
    return getattr(_src, name)
