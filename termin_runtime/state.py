# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Slice 7.2.c of Phase 7 (2026-04-30) moved this module to
``termin_core.state.machine``. This file is a re-export shim that
lets existing ``from termin_runtime.state import do_state_transition``
imports continue to work for v0.9. Drop the shim in slice 7.5.

The runtime behavior changed in one observable way: state-machine
failures now raise the framework-agnostic exception types from
``termin_core.errors`` (:class:`TerminBadRequestError`,
:class:`TerminScopeError`, :class:`TerminNotFoundError`,
:class:`TerminConflictError`) instead of ``fastapi.HTTPException``
directly. The FastAPI exception handler registered in ``app.py``
translates them to the same status codes (400 / 403 / 404 / 409)
the conformance tests saw before.
"""

from termin_core.state import *  # noqa: F401, F403
from termin_core.state import do_state_transition  # noqa: F401
