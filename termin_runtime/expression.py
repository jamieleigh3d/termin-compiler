# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Slice 7.1 of Phase 7 (2026-04-30) moved this module to
``termin_core.expression.cel``. This file is a re-export shim that
lets existing ``from termin_runtime.expression import X`` imports
continue to work for v0.9. Drop the shim in slice 7.5.
"""

from termin_core.expression.cel import *  # noqa: F401, F403
from termin_core.expression.cel import (  # noqa: F401
    ExpressionEvaluator,
    SYSTEM_FUNCTIONS,
    # Underscore-prefixed names pre-existing tests import directly.
    # `import *` doesn't pull them; explicit re-export preserves
    # their visibility for v0.9.
    _cel_to_python,
    _rewrite_the_user,
    _cel_sum, _cel_avg, _cel_min, _cel_max,
    _cel_flatten, _cel_unique, _cel_first, _cel_last, _cel_sort,
    _cel_days_between, _cel_days_until, _cel_add_days,
    _cel_upper, _cel_lower, _cel_trim, _cel_replace,
    _cel_round, _cel_floor, _cel_ceil, _cel_abs, _cel_clamp,
)
