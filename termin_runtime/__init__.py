# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Termin Runtime — the execution environment for compiled Termin applications.

Usage:
    from termin_runtime import create_termin_app
    app = create_termin_app(ir_json_string)
"""

from .app import create_termin_app

__all__ = ["create_termin_app"]
