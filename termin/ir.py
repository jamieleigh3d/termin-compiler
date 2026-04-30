# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Slice 7.1 of Phase 7 (2026-04-30) moved this module to
``termin_core.ir.types``. This file is a re-export shim that lets
existing ``from termin.ir import X`` imports continue to work
for v0.9. Drop the shim in slice 7.5.

The compiler's lowering pass (``termin/lower.py``) still builds
these types; the types themselves now live in the framework-free
``termin-core`` package so any conforming runtime can read a
compiled ``.termin.pkg`` and reconstruct typed IR without taking
a dependency on the compiler.
"""

from termin_core.ir.types import *  # noqa: F401, F403
