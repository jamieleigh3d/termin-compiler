# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Slice 7.1 of Phase 7 (2026-04-30) moved the provider Protocols,
contract registry, deploy-config parser, and binding resolver to
``termin_core.providers``. This package now re-exports the same
public surface so existing ``from termin_runtime.providers import X``
imports continue to work for v0.9. Drop the shim in slice 7.5.

The concrete provider implementations (``builtins/``) — SQLite
storage, Anthropic LLM/agent, Tailwind SSR renderer, channel
webhook/email/messaging stubs — are NOT moved by this slice. They
stay in ``termin_runtime`` because they need IO (network, file
system, framework integration). They get extracted to
``termin-server`` in slice 7.3 of Phase 7.
"""

from termin_core.providers import *  # noqa: F401, F403
from termin_core.providers import __all__  # noqa: F401
