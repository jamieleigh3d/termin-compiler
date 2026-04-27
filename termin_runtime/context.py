# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""RuntimeContext — shared state container for all runtime subsystems.

Passed to each module's registration functions so they can access
shared state without globals or deep closure nesting.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine


@dataclass
class RuntimeContext:
    """All shared state needed by runtime subsystems."""

    # Core IR data
    ir: dict = field(default_factory=dict)
    ir_json: str = ""

    # Database
    db_path: str | None = None

    # Subsystems
    expr_eval: Any = None          # ExpressionEvaluator
    terminator: Any = None         # TerminAtor
    event_bus: Any = None          # EventBus
    reflection: Any = None         # ReflectionEngine
    channel_dispatcher: Any = None # ChannelDispatcher
    conn_manager: Any = None       # ConnectionManager (set after creation)

    # v0.9 Phase 2: storage provider (StorageProvider Protocol).
    # Set by app.py from the bound storage provider — every CRUD
    # path in the runtime goes through this instance, never through
    # direct storage.py imports. Per-app instance (no globals);
    # see providers/builtins/storage_sqlite.py for the SQLite impl.
    storage: Any = None            # StorageProvider

    # v0.9 Phase 3: compute provider registry + per-compute provider
    # cache. The registry holds factories (one per registered product);
    # `compute_providers` holds constructed instances keyed by compute
    # snake-name, pre-built at app startup from `bindings.compute`.
    # default-CEL providers are NOT in the cache — those route through
    # `expr_eval` for trigger filters / postconditions / route-handler
    # CEL. The cache is just the LLM/agent dispatch table for
    # compute_runner.execute_compute.
    provider_registry: Any = None        # ProviderRegistry
    contract_registry: Any = None        # ContractRegistry
    compute_providers: dict = field(default_factory=dict)
    # snake compute name -> provider instance (LlmComputeProvider |
    # AiAgentComputeProvider). default-CEL computes are absent.

    # v0.9 Phase 3 slice (c): per-compute closed tool surface,
    # computed at app startup from the ComputeSpec's Accesses /
    # Reads / Sends to / Emits / Invokes declarations. Frozen
    # ToolSurface dataclass; the agent's gate function reads it at
    # tool-dispatch time. snake compute name -> ToolSurface.
    compute_tool_surfaces: dict = field(default_factory=dict)

    # Identity functions
    get_current_user: Callable = None
    get_user_from_ws: Callable = None
    require_scope: Callable = None
    roles: dict = field(default_factory=dict)  # role_name -> [scopes]

    # Content lookups
    content_lookup: dict = field(default_factory=dict)   # snake -> schema dict
    singular_lookup: dict = field(default_factory=dict)   # snake -> singular string
    sm_lookup: dict = field(default_factory=dict)         # content_ref -> list[{machine_name, column, initial, transitions}]

    # Compute indexes
    compute_specs: dict = field(default_factory=dict)     # snake -> compute IR dict
    compute_lookup: dict = field(default_factory=dict)    # snake -> compute IR dict
    trigger_computes: list = field(default_factory=list)  # computes with event triggers
    schedule_computes: list = field(default_factory=list) # (comp, interval) pairs

    # Boundary maps (Block C)
    boundary_for_content: dict = field(default_factory=dict)   # content_snake -> boundary_snake
    boundary_for_compute: dict = field(default_factory=dict)   # compute_snake -> boundary_snake
    boundary_identity_scopes: dict = field(default_factory=dict)  # content_snake -> [scopes]

    # Transition feedback (D-06)
    transition_feedback: dict = field(default_factory=dict)  # (content, from, to) -> [specs]

    # Callbacks set during init (avoid circular deps)
    run_event_handlers: Callable = None   # async (db, content_name, trigger, record)
    execute_compute: Callable = None      # async (comp, record, content_name, main_loop)

    def scope_for_content_verb(self, content_snake: str, verb: str) -> str | None:
        """Look up the scope required for a verb on a content type."""
        for g in self.ir.get("access_grants", []):
            if g["content"] == content_snake and verb in g["verbs"]:
                return g["scope"]
        return None
