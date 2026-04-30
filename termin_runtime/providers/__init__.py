# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Slice 7.3 of Phase 7 (2026-04-30) moved this package to
``termin_server.providers``. Re-export shim — drops in slice 7.5.
"""

from termin_server.providers import *  # noqa: F401, F403
import termin_server.providers as _src


def __getattr__(name):
    return getattr(_src, name)
