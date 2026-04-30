# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Slice 7.2.b of Phase 7 (2026-04-30) moved this module to
``termin_core.validation``. This file is a re-export shim that lets
existing ``from termin_runtime.validation import X`` imports continue
to work for v0.9. Drop the shim in slice 7.5.

The runtime behavior changed in one observable way: validation
failures now raise :class:`termin_core.errors.TerminValidationError`
instead of ``fastapi.HTTPException`` directly. The FastAPI exception
handler registered in ``app.py`` translates the new exception type
to the same 422 response body shape ``HTTPException(detail=…)``
produced before, so conformance tests don't see a contract change.
"""

from termin_core.validation import *  # noqa: F401, F403
from termin_core.validation import (  # noqa: F401
    validate_dependent_values,
    validate_enum_constraints,
    validate_min_max_constraints,
    evaluate_field_defaults,
    strip_unknown_fields,
)
