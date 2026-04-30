# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Slice 7.1 of Phase 7 (2026-04-30) moved this module to
``termin_core.confidentiality.redaction``. This file is a
re-export shim that lets existing imports continue to work for
v0.9. Drop the shim in slice 7.5.
"""

from termin_core.confidentiality.redaction import *  # noqa: F401, F403
from termin_core.confidentiality import (  # noqa: F401
    effective_scopes,
    redact_record,
    redact_records,
    is_redacted,
    check_write_access,
    check_compute_access,
    check_taint_integrity,
    enforce_output_taint,
    check_for_redacted_values,
)
