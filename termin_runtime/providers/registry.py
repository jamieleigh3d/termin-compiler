# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Slice 7.1 of Phase 7 (2026-04-30) moved this module to
``termin_core.providers.registry``. This file is a re-export shim
that lets existing imports continue to work for v0.9. Drop the
shim in slice 7.5.
"""

from termin_core.providers.registry import *  # noqa: F401, F403
