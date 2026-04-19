# Copyright 2026 Jamie-Leigh Blake
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Termin Runtime Conformance Test Suite — MOVED.

The conformance test suite has been moved to its own repository:

    https://github.com/clarityintelligence/termin-conformance

That repository contains:
  - 201+ behavioral tests across 13 test files
  - Runtime adapter pattern (any runtime, not just the reference)
  - 6 test fixture apps (.termin.pkg + raw IR JSON)
  - Three-tier testing methodology (API, presentation, round-trip)
  - IR JSON Schema, Runtime Implementer's Guide, Package Format spec

To run the conformance suite against the reference runtime:

    cd termin-conformance
    pip install -r requirements.txt
    TERMIN_ADAPTER=reference pytest tests/ -v

This file is kept as a pointer. Do not add tests here.
"""
