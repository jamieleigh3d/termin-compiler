# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Termin App Factory — creates a configured FastAPI app from IR JSON.

This is the main entry point for the Termin runtime. It reads the IR,
creates all subsystems, registers routes, and returns a FastAPI app.

Subsystem modules:
  - context.py: RuntimeContext shared state
  - websocket_manager.py: ConnectionManager + WS multiplexer
  - boundaries.py: Block C boundary containment
  - validation.py: D-19 dependent values + constraints
  - compute_runner.py: LLM/Agent/CEL compute execution + D-20 audit
  - transitions.py: Toast/banner feedback + generic transition endpoint
  - routes.py: CRUD, reflection, channel, webhook endpoints
  - pages.py: Page rendering + form POST
"""

import asyncio
import json
import os
import threading

from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse

from .context import RuntimeContext
from .expression import ExpressionEvaluator
from .errors import TerminAtor
from .events import EventBus
from .identity import make_get_current_user, make_require_scope, make_get_user_from_websocket
from .providers import (
    Category, ContractRegistry, ProviderRegistry, initial_deploy_diff,
)
from .providers.builtins import register_builtins as register_builtin_providers
from .storage import get_db, init_db, create_record, insert_raw, count_records
from .reflection import ReflectionEngine, register_reflection_with_expr_eval
from .channels import ChannelDispatcher, load_deploy_config, check_deploy_config_warnings
from .scheduler import Scheduler, parse_schedule_interval

# Subsystem modules
from .websocket_manager import ConnectionManager, register_websocket_routes
from .boundaries import build_boundary_maps
from .transitions import build_transition_feedback, register_transition_routes
from .routes import (
    register_crud_routes, register_reflection_routes, register_channel_routes,
    register_sse_routes, register_runtime_endpoints,
)
from .pages import register_page_routes
from .compute_runner import execute_compute, register_compute_endpoint


import re

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate_env_vars(value):
    """Recursively replace ${VAR} placeholders with env var values.

    Strings are scanned for ${VAR} patterns; each is replaced with
    `os.environ.get(VAR, "")`. Nested dicts and lists recurse. Other
    types pass through. Used by the compute provider resolver in
    create_termin_app to substitute deploy-config secrets at
    construction time per BRD §5.1 (source must not name product
    internals; deploy config carries the env-shaped placeholders)."""
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _interpolate_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env_vars(v) for v in value]
    return value


def _discover_external_providers(provider_registry, contract_registry) -> None:
    """Discover providers exposed via Python entry points.

    Third-party provider packages (termin-spectrum-provider,
    termin-carbon-provider, etc.) advertise themselves via the
    `termin.providers` entry-point group; the value is a function
    `register_<product>(provider_registry, contract_registry)` that
    matches the same shape used by termin_runtime.providers.builtins.

    Per BRD §10, this is the same loading path as built-in providers.
    The only difference is discovery — built-ins are explicitly imported
    in `register_builtins`; externals are discovered at runtime so users
    can install a provider package via pip without modifying core.

    Entry-point registration in a provider package's setup.py:

        entry_points={
            "termin.providers": [
                "spectrum = termin_spectrum:register_spectrum",
            ],
        }

    Failures during a single provider's registration are logged but
    do not abort startup — a broken optional provider should not take
    down a service that doesn't depend on it. Deploy-time binding
    resolution will then fail-closed if the misregistered product
    appears in the active deploy config.
    """
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover — py3.8+ ships this
        return
    try:
        eps = entry_points(group="termin.providers")
    except TypeError:  # pragma: no cover — pre-3.10 API
        eps = entry_points().get("termin.providers", [])
    for ep in eps:
        try:
            register_fn = ep.load()
            register_fn(provider_registry, contract_registry)
        except Exception as exc:
            print(
                f"[termin] WARNING: failed to load provider entry-point "
                f"{ep.name!r}: {type(exc).__name__}: {exc}"
            )


def _populate_presentation_providers(
    ctx, deploy_config: dict, provider_registry, contract_registry
) -> None:
    """Populate `ctx.presentation_providers` from deploy_config bindings.

    Deploy-config bindings come in two shapes:

      bindings.presentation.<contract>:        # per-contract
        provider: "<product>"
        config: {...}

      presentation.bindings.<namespace-or-contract>:    # per-namespace
        provider: "<product>"
        config: {...}

    Either is accepted. Namespace bindings (e.g. `presentation-base`)
    expand to all contracts in that namespace; per-contract bindings
    target one. Sub-contract bindings win over namespace bindings
    when both apply (BRD #2 §11.3).

    The function caches one provider instance per product across
    contracts — calling the factory ten times with the same config
    would create ten redundant instances.
    """
    from termin_runtime.providers.contracts import Category
    from termin_runtime.providers.presentation_contract import (
        PRESENTATION_BASE_CONTRACTS,
    )

    # Two locations where bindings might live, see BRD §11.2.
    flat = (deploy_config.get("bindings", {}) or {}).get("presentation", {})
    nested = (deploy_config.get("presentation", {}) or {}).get("bindings", {})
    bindings = {**(nested or {}), **(flat or {})}

    # v0.9 Phase 5b.3: when no explicit binding exists for the
    # presentation-base namespace, synthesize one to tailwind-default
    # so the dispatch table is symmetric with the explicit-binding
    # case. The legacy SSR Jinja path still drives actual page
    # rendering — but downstream consumers (`page_should_use_shell`,
    # the bundle-discovery endpoint, conformance manifests) read
    # `ctx.presentation_providers` and benefit from a uniform shape
    # whether or not the deploy config names a provider.
    has_base_binding = (
        "presentation-base" in bindings
        or any(k.startswith("presentation-base.") for k in bindings)
    )
    if not has_base_binding:
        bindings = {
            **bindings,
            "presentation-base": {"provider": "tailwind-default", "config": {}},
        }
    if not bindings:
        return

    instances: dict = {}  # product_name -> instance, cached across contracts

    def _get_or_create(product: str, config: dict):
        if product not in instances:
            # Factory lookup: any registered (PRESENTATION, *, product)
            # record's factory will do — they all wrap the same product.
            for record in provider_registry.all_records():
                if (record.category == Category.PRESENTATION
                        and record.product_name == product):
                    instances[product] = record.factory(config or {})
                    break
        return instances.get(product)

    # Per-contract bindings first, then namespace fallback.
    # v0.9 Phase 5c.3: namespace expansion consults the contract-
    # package registry when the namespace isn't presentation-base.
    # This lets `bindings.presentation.airlock-components` map a
    # provider product to all contracts declared by the
    # airlock-components contract package, exactly the way
    # presentation-base namespace bindings already work.
    pkg_registry = getattr(ctx, "contract_package_registry", None)
    contract_bindings: dict[str, dict] = {}
    for key, binding in bindings.items():
        if not isinstance(binding, dict):
            continue
        if "." in key:
            contract_bindings[key] = binding
            continue
        # Namespace binding.
        if key == "presentation-base":
            shorts: tuple[str, ...] = PRESENTATION_BASE_CONTRACTS
        elif pkg_registry is not None and key in pkg_registry.namespaces():
            # Look up the contracts declared by this package and
            # fan the binding out to each. The registry's
            # `get_contract` API is by full name; iterate the
            # private packages map to enumerate all contracts in
            # the namespace.
            pkg = pkg_registry._packages.get(key)
            shorts = tuple(c.name for c in pkg.contracts) if pkg else ()
        else:
            # Unknown namespace — quietly skip. Deploy-time
            # validation in BRD #2 §8.5 (required_contracts) is the
            # right place to fail-closed; this populator is purely
            # advisory.
            shorts = ()
        for short in shorts:
            full = f"{key}.{short}"
            contract_bindings.setdefault(full, binding)

    # Materialize: one (contract, product, instance) triple per
    # bound contract. Skip products that have no registered factory.
    for contract, binding in contract_bindings.items():
        product = binding.get("provider")
        if not product:
            continue
        instance = _get_or_create(product, binding.get("config") or {})
        if instance is None:
            continue
        ctx.presentation_providers.append((contract, product, instance))


def _load_contract_packages(ctx, deploy_config: dict) -> None:
    """v0.9 Phase 5c.1: load contract packages declared in deploy
    config and attach a populated `ContractPackageRegistry` to ctx.

    Deploy-config shape:
        {
          "contract_packages": [
            "contract_packages/airlock-components.yaml",
            ... more package paths ...
          ]
        }

    Paths are resolved relative to the deploy config's parent
    directory if a `_deploy_config_path` is set on the deploy_config
    dict (the CLI sets this when loading the config); otherwise
    relative to the current working directory.

    On any load failure (malformed YAML, verb collision, missing
    file), the runtime fails closed at startup — a deploy declaring
    a package can't proceed without it. Per BRD #2 §10.4, the
    `Using "<ns>.<contract>"` source forms are mandatory references
    to a loaded package; running without the package would make
    those references unresolvable.

    No-op when `contract_packages` is missing or empty — apps that
    only use the `presentation-base` namespace need no packages.
    """
    raw = deploy_config.get("contract_packages")
    if not raw:
        return
    if not isinstance(raw, list):
        raise RuntimeError(
            "deploy_config.contract_packages must be a list of YAML "
            "file paths (got %s)" % type(raw).__name__
        )

    from termin.contract_packages import (
        ContractPackageError,
        load_contract_packages_into_registry,
    )

    base_dir = None
    cfg_path = deploy_config.get("_deploy_config_path")
    if cfg_path:
        from pathlib import Path
        base_dir = Path(cfg_path).resolve().parent

    resolved_paths: list = []
    for entry in raw:
        if isinstance(entry, dict):
            # Future-shape: support {"path": "...", "version": "..."}
            # gracefully — only `path` is mandatory in 5c.1.
            entry = entry.get("path")
        if not isinstance(entry, str) or not entry:
            raise RuntimeError(
                "deploy_config.contract_packages entries must be "
                "non-empty strings (or objects with a `path` field)"
            )
        from pathlib import Path
        p = Path(entry)
        if not p.is_absolute() and base_dir is not None:
            p = base_dir / p
        resolved_paths.append(p)

    try:
        ctx.contract_package_registry = load_contract_packages_into_registry(
            resolved_paths
        )
    except ContractPackageError as exc:
        raise RuntimeError(
            f"Failed to load contract package(s) declared in deploy "
            f"config: {exc}"
        ) from exc


def _resolve_directive_sources(computes: list, deploy_config: dict) -> None:
    """v0.9 Phase 6c (BRD #3 §6.2): resolve deploy-config-sourced
    Directive and Objective text at application startup.

    Mutates each compute dict in place. For computes with
    `directive_source.kind == "deploy_config"`, reads the value from
    `deploy_config[<key>]` and assigns it into `comp["directive"]`.
    Same for `objective_source`. Inline-literal directives (where
    `directive_source` is None) are left unchanged. Field-ref
    directives (`kind == "field"`) are deferred to invocation time
    and resolved by `compute_runner._resolve_directive_at_invocation`.

    Missing keys resolve to empty strings so the prompt-build path
    skips them gracefully — same forgiving stance the rest of the
    runtime takes for absent inline directives.
    """
    for comp in computes:
        d_src = comp.get("directive_source")
        if isinstance(d_src, dict) and d_src.get("kind") == "deploy_config":
            comp["directive"] = str(deploy_config.get(d_src.get("key", ""), ""))
        o_src = comp.get("objective_source")
        if isinstance(o_src, dict) and o_src.get("kind") == "deploy_config":
            comp["objective"] = str(deploy_config.get(o_src.get("key", ""), ""))


def create_termin_app(ir_json: str, db_path: str = None, seed_data: dict = None,
                      deploy_config: dict = None, deploy_config_path: str = None,
                      strict_channels: bool = True) -> FastAPI:
    """Create a fully configured FastAPI app from an IR JSON string.

    Args:
        ir_json: The IR JSON string.
        db_path: Path to the SQLite database file.
        seed_data: Optional dict of {content_name: [record_dicts]} to seed on first run.
        deploy_config: Optional deploy config dict. Overrides file loading.
        deploy_config_path: Explicit path to deploy config file.
        strict_channels: If True, refuse to start if non-internal channels lack
                         deploy config. Set False for testing or dev mode.
    """
    ir = json.loads(ir_json)
    app_name = ir.get("name", "Termin App")

    # ── Build RuntimeContext ──
    # Resolve db_path immediately so ctx.db_path is never None — every
    # runtime caller passes ctx.db_path through to get_db, and we want
    # the value visible at app construction (not buried in storage's
    # fallback).
    from .storage import default_db_path_for_app
    # Resolve to absolute. Relative paths bind against cwd at every
    # aiosqlite.connect() — if any caller (notably tests, which may
    # change cwd between invocations) flips cwd after app startup,
    # subsequent queries open a different file. Locking the path to
    # absolute here gives stable per-app storage regardless of cwd.
    #
    # Resolution precedence (highest first):
    #   1. Explicit `db_path` argument to create_termin_app.
    #   2. TERMIN_DB_PATH environment variable. Useful for ops
    #      pipelines that want to point a deployed app at a
    #      specific path without editing the compiled app.py.
    #   3. default_db_path_for_app(ir) — derives "<slug>__<id8>.db"
    #      from the app's name + UUID so multiple apps in the same
    #      cwd never collide and re-serving the same .pkg keeps its
    #      data (CLI upgrade scenarios).
    if db_path:
        resolved_db_path = db_path
    elif os.environ.get("TERMIN_DB_PATH"):
        resolved_db_path = os.environ["TERMIN_DB_PATH"]
    else:
        resolved_db_path = default_db_path_for_app(ir)
    if resolved_db_path != ":memory:" and not os.path.isabs(resolved_db_path):
        resolved_db_path = os.path.abspath(resolved_db_path)
    ctx = RuntimeContext(ir=ir, ir_json=ir_json, db_path=resolved_db_path)

    # Subsystem initialization
    ctx.expr_eval = ExpressionEvaluator()
    ctx.terminator = TerminAtor()
    ctx.event_bus = EventBus()
    ctx.reflection = ReflectionEngine(ir_json)

    # Deploy config
    has_external_channels = any(
        ch.get("direction", "") != "INTERNAL"
        for ch in ir.get("channels", []))
    has_llm_computes = any(
        (c.get("provider") or "") in ("llm", "ai-agent")
        for c in ir.get("computes", []))
    needs_deploy_config = has_external_channels or has_llm_computes
    # v0.9 Phase 5b.4 B' loop: presentation bindings live in deploy
    # config too (e.g., bindings.presentation.<contract>.provider).
    # The original `needs_deploy_config` gate predated that and only
    # loaded the file when channels or LLMs required it — so apps
    # with neither (like hello.termin) had their presentation
    # bindings silently dropped. Now: load whenever a path is given,
    # OR when channels/LLMs need it. The legacy auto-discovery of a
    # sibling deploy.json (no explicit path) still keys off
    # needs_deploy_config — apps that don't need it skip the lookup.
    if deploy_config is None and (deploy_config_path or needs_deploy_config):
        app_snake = ir.get("name", "app").lower().replace(" ", "_").replace("-", "_")
        deploy_config = load_deploy_config(path=deploy_config_path, app_name=app_snake)
    elif deploy_config is None:
        deploy_config = {}
    # v0.9 Phase 5c.1: stash the path so contract-package paths in
    # deploy config can be resolved relative to the deploy file (the
    # natural authoring location). Idempotent if already set
    # (caller-provided dict may have it).
    if deploy_config_path and "_deploy_config_path" not in deploy_config:
        deploy_config["_deploy_config_path"] = str(deploy_config_path)
    # v0.9 Phase 5a.3: source presentation defaults out of deploy_config
    # so request handlers (theme preference endpoints, render-time
    # PrincipalContext construction) can apply them without re-reading
    # the file. Per BRD #2 §6.2 and design §3.13.
    presentation_defaults = (
        deploy_config.get("presentation", {}).get("defaults", {})
        if isinstance(deploy_config, dict) else {}
    )
    ctx.theme_default = presentation_defaults.get("theme_default")
    ctx.theme_locked = presentation_defaults.get("theme_locked")
    # v0.9 Phase 5b.4 platform: stash deploy_config on ctx so the
    # bundle-discovery endpoint can read per-binding overrides.
    ctx.deploy_config = deploy_config or {}
    # v0.9 Phase 4: pass provider_registry so the dispatcher can wire
    # channel providers at startup (channels with provider_contract).
    # The registry is built earlier in this function (Phase 1 identity).
    ctx.channel_dispatcher = ChannelDispatcher(ir, deploy_config, ctx.provider_registry)

    # v0.9 Phase 6c (BRD #3 §6.2): resolve `Directive from deploy
    # config "<key>"` and `Objective from deploy config "<key>"` at
    # application startup. Deploy-config-sourced prompts are reused
    # for all invocations until the application restarts.
    _resolve_directive_sources(ir.get("computes", []), deploy_config or {})

    # Compute indexes
    for comp in ir.get("computes", []):
        ctx.compute_specs[comp["name"]["snake"]] = comp
        ctx.compute_lookup[comp["name"]["snake"]] = comp
        trigger = comp.get("trigger") or ""
        if trigger.startswith("event "):
            ctx.trigger_computes.append(comp)
        else:
            interval = parse_schedule_interval(trigger)
            if interval is not None:
                ctx.schedule_computes.append((comp, interval))

    # Boundary maps (Block C)
    ctx.boundary_for_content, ctx.boundary_for_compute, ctx.boundary_identity_scopes = \
        build_boundary_maps(ir)

    # Identity
    for role in ir.get("auth", {}).get("roles", []):
        ctx.roles[role["name"]] = role["scopes"]
    # v0.9: canonical Anonymous role name is capitalized "Anonymous".
    # If the source declared no anonymous role, synthesize an empty
    # one under the canonical name so role-key comparisons (template,
    # reflection, identity resolution) stay consistent.
    if not any(k.lower() == "anonymous" for k in ctx.roles):
        ctx.roles["Anonymous"] = []

    # v0.9 Phase 1: instantiate the bound IdentityProvider via the
    # provider registry. The runtime ships first-party providers
    # through the same registration path third-party providers will
    # use (BRD §10). Deploy config selects which product to bind;
    # in v0.9 the catalog is "stub" only (one product per contract,
    # no real auth providers yet) — Phase 2+ adds real bindings.
    ctx.contract_registry = ContractRegistry.default()
    ctx.provider_registry = ProviderRegistry()
    register_builtin_providers(ctx.provider_registry, ctx.contract_registry)

    # v0.9 Phase 5b.4 B' loop: discover and register external provider
    # packages via Python entry points. Sibling packages (e.g.,
    # termin-spectrum-provider) declare an entry point under group
    # `termin.providers` whose value is a `register_<product>` function;
    # we load and call each one. Per BRD §10 same loading path as
    # built-ins. A provider failing to register is logged but does
    # not crash the app — a misconfigured optional provider should
    # not take down a service that doesn't depend on it.
    _discover_external_providers(ctx.provider_registry, ctx.contract_registry)
    # v0.9 Phase 5b.4 B' loop: populate ctx.presentation_providers
    # from deploy_config.bindings.presentation so bundle discovery
    # (`/_termin/presentation/bundles`) and bundle serving (`/_termin/
    # providers/<product>/bundle.js`) have something to read from.
    # This is the slim B'-only cut-over of the deferred 5b.3 work
    # (full per-render dispatch is still later).
    # v0.9 Phase 5c.1: load contract packages declared in deploy
    # config so the two-pass compiler (5c.2) and the runtime
    # contract-package dispatch (5c.3) can resolve `Using
    # "<ns>.<contract>"` references at startup. Idempotent no-op
    # when no packages are declared. Must run BEFORE
    # _populate_presentation_providers so the populator can expand
    # non-presentation-base namespace bindings using the package
    # registry's namespace catalog.
    _load_contract_packages(ctx, deploy_config or {})
    _populate_presentation_providers(
        ctx, deploy_config or {},
        ctx.provider_registry, ctx.contract_registry,
    )
    identity_binding = (deploy_config or {}).get("bindings", {}).get("identity", {})
    identity_product = identity_binding.get("provider") or "stub"
    identity_config = identity_binding.get("config") or {}
    identity_record = ctx.provider_registry.get(
        Category.IDENTITY, "default", identity_product
    )
    if identity_record is None:
        # Per BRD §6.1 fail-closed: an unregistered identity product
        # is a deploy misconfiguration, not a fall-back-to-stub case.
        # Refuse to start so the operator catches the binding error
        # at deploy time rather than at the first auth-required
        # request. In production this is critical: a deploy that
        # silently falls back to a dev stub when the configured
        # SSO product is missing would be a security incident.
        available = ctx.provider_registry.list_products(
            Category.IDENTITY, "default"
        )
        raise RuntimeError(
            f"Identity provider {identity_product!r} is not registered. "
            f"Available: {sorted(available) or '<none>'}. "
            f"Either register the provider before calling "
            f"create_termin_app(), or update the deploy config "
            f"bindings.identity.provider to a registered product."
        )
    ctx.identity_provider = identity_record.factory(identity_config)

    # v0.9 Phase 2: instantiate the bound StorageProvider via the
    # provider registry. Same loading path as identity (BRD §10
    # "One loading path for all providers"). Deploy config selects
    # which product to bind; v0.9 ships "sqlite" only. The provider
    # holds its own db_path so multiple apps in one process never
    # share storage state — fixes the v0.8 _db_path module-global
    # contamination class.
    storage_binding = (deploy_config or {}).get("bindings", {}).get("storage", {})
    storage_product = storage_binding.get("provider") or "sqlite"
    storage_config = dict(storage_binding.get("config") or {})
    # Honor the legacy db_path argument by feeding it into the
    # provider config when no explicit binding override exists.
    # Tests pass db_path positionally; production callers pass it
    # through the deploy config.
    if "db_path" not in storage_config:
        storage_config["db_path"] = resolved_db_path
    storage_record = ctx.provider_registry.get(
        Category.STORAGE, "default", storage_product
    )
    if storage_record is None:
        # Fail-closed: storage is Tier 1 — app down if down. An
        # unregistered product at deploy time is a misconfiguration
        # that should surface immediately, not at the first CRUD call.
        available = ctx.provider_registry.list_products(
            Category.STORAGE, "default"
        )
        raise RuntimeError(
            f"Storage provider {storage_product!r} is not registered. "
            f"Available: {sorted(available) or '<none>'}. "
            f"Either register the provider before calling "
            f"create_termin_app(), or update the deploy config "
            f"bindings.storage.provider to a registered product."
        )
    ctx.storage = storage_record.factory(storage_config)

    # v0.9 Phase 3: pre-resolve compute providers per the source's
    # ComputeSpec list. Each LLM/agent compute looks up its binding
    # in deploy_config.bindings.compute["<compute-snake>"] and
    # constructs the provider via the registry. default-CEL computes
    # are NOT resolved here — those route through ctx.expr_eval for
    # trigger filters / postconditions / route-handler CEL.
    #
    # Per BRD §5.1 leak-free: source declares `Provider is "llm"`;
    # deploy config picks the product (`anthropic` / `stub` / etc.).
    # An LLM/agent compute with no binding in deploy config is a
    # configuration error — fail-closed at deploy time, not at first
    # event-trigger.
    compute_bindings = (deploy_config or {}).get(
        "bindings", {}
    ).get("compute", {}) or {}
    for comp in ir.get("computes", []):
        contract = comp.get("provider")
        if contract not in ("llm", "ai-agent"):
            continue
        comp_snake = comp["name"]["snake"]
        binding = compute_bindings.get(comp_snake)
        if binding is None:
            # Skip silently when ANTHROPIC_API_KEY is unset / no
            # deploy config — matches the v0.8 "AI provider not
            # configured, skipped" behavior. The runtime logs this
            # at lifespan startup.
            continue
        product = (binding or {}).get("provider")
        if not product:
            raise RuntimeError(
                f"Compute {comp['name']['display']!r} has Provider is "
                f"{contract!r} but bindings.compute[{comp_snake!r}] "
                f"has no 'provider' key."
            )
        cfg = dict((binding or {}).get("config") or {})
        # Env-var interpolation for ${...} placeholders so providers
        # see resolved values at construction time. Mirrors the
        # ChannelDispatcher's interpolation behavior.
        cfg = _interpolate_env_vars(cfg)
        record = ctx.provider_registry.get(Category.COMPUTE, contract, product)
        if record is None:
            available = ctx.provider_registry.list_products(
                Category.COMPUTE, contract
            )
            raise RuntimeError(
                f"Compute provider {product!r} is not registered for "
                f"contract {contract!r}. Available: "
                f"{sorted(available) or '<none>'}. Either register the "
                f"provider before calling create_termin_app(), or "
                f"update bindings.compute[{comp_snake!r}].provider."
            )
        ctx.compute_providers[comp_snake] = record.factory(cfg)

    # v0.9 Phase 3 slice (c): build a ToolSurface for every
    # ai-agent compute from its source-declared grants. This is the
    # closed tool surface the agent provider sees at runtime;
    # downstream gate functions (slice d's tool-dispatch rewrite)
    # consult it to authorize each tool call. Stashed on ctx keyed
    # by compute snake-name. Skipped for llm and default-CEL
    # computes — those don't have a tool surface.
    from .providers.compute_contract import ToolSurface as _ToolSurface
    for comp in ir.get("computes", []):
        if comp.get("provider") != "ai-agent":
            continue
        comp_snake = comp["name"]["snake"]
        ctx.compute_tool_surfaces[comp_snake] = _ToolSurface(
            content_rw=tuple(comp.get("accesses") or ()),
            content_ro=tuple(comp.get("reads") or ()),
            channels=tuple(comp.get("sends_to") or ()),
            events=tuple(comp.get("emits") or ()),
            computes=tuple(comp.get("invokes") or ()),
        )

    app_id = ir.get("app_id", "") or ir.get("name", "") or ""
    ctx.get_current_user = make_get_current_user(
        ctx.roles, ctx.identity_provider, app_id, ctx=ctx,
    )
    ctx.get_user_from_ws = make_get_user_from_websocket(
        ctx.roles, ctx.identity_provider, app_id, ctx=ctx,
    )
    ctx.require_scope = make_require_scope(ctx.get_current_user)

    # Content lookups
    for cs in ir.get("content", []):
        snake = cs["name"]["snake"]
        ctx.content_lookup[snake] = cs
        if cs.get("singular"):
            ctx.singular_lookup[snake] = cs["singular"]

    # State machine lookup — v0.9 multi-SM shape.
    # Maps content_ref -> list[{machine_name, column, initial, transitions}].
    # A content with two state machines (e.g. lifecycle + approval status)
    # appears once with two list entries; the legacy one-SM-per-content
    # overwriting bug from v0.8 (sm_by_content[content] = sm) is gone.
    from collections import defaultdict
    _sm_by_content = defaultdict(list)
    for sm in ir.get("state_machines", []):
        col = sm["machine_name"]   # already snake_case in IR
        trans_dict = {
            (t["from_state"], t["to_state"]): t.get("required_scope", "")
            for t in sm.get("transitions", [])
        }
        _sm_by_content[sm["content_ref"]].append({
            "machine_name": col,
            "column": col,           # same as machine_name
            "initial": sm.get("initial_state", ""),
            "transitions": trans_dict,
        })
    ctx.sm_lookup = dict(_sm_by_content)

    # Transition feedback
    ctx.transition_feedback = build_transition_feedback(ir)

    # Register reflection with expression evaluator
    register_reflection_with_expr_eval(ctx.reflection, ctx.expr_eval)

    # WebSocket connection manager
    ctx.conn_manager = ConnectionManager()
    # v0.9 Phase 6a.6 (BRD #3 §3.6): cascade ownership filtering onto
    # WebSocket subscriptions — owned content fans out only to the
    # owning principal. Build the per-content lookup once at startup;
    # the manager consults it in broadcast_to_subscribers and on the
    # subscribe / request initial-data load.
    _ownership_lookup = {}
    for cs in ir.get("contents", []):
        own = cs.get("ownership")
        if own and own.get("field"):
            _ownership_lookup[cs.get("name", {}).get("snake", "")] = own["field"]
    ctx.conn_manager.set_content_ownership(_ownership_lookup)

    # ── Event handlers (needs access to ctx for singular_lookup, expr_eval, etc.) ──
    async def run_event_handlers(db, content_name: str, trigger: str, record: dict):
        for ev in ir.get("events", []):
            if ev.get("trigger") == "expr" and ev.get("condition_expr"):
                if content_name == ev.get("source_content", ""):
                    evctx = dict(record)
                    for k, v in list(record.items()):
                        parts = k.split("_")
                        camel = parts[0] + "".join(w.capitalize() for w in parts[1:])
                        evctx[camel] = v
                    snake_singular = ctx.singular_lookup.get(content_name, "")
                    if not snake_singular:
                        snake_singular = content_name.rstrip("s") if content_name.endswith("s") else content_name
                    parts = snake_singular.split("_")
                    camel_prefix = parts[0] + "".join(w.capitalize() for w in parts[1:])
                    prefixed = dict(evctx)
                    prefixed["updated"] = True
                    prefixed["created"] = True
                    evctx[camel_prefix] = prefixed
                    try:
                        if ctx.expr_eval.evaluate(ev["condition_expr"], evctx):
                            action = ev.get("action")
                            if action and action.get("column_mapping"):
                                insert_data = {p[0]: record.get(p[1], "") for p in action["column_mapping"]}
                                await insert_raw(db, action["target_content"], insert_data)
                            elif action and action.get("send_channel"):
                                def _sync_send(_action=action, _record=dict(record), _ev=ev):
                                    import httpx as _httpx
                                    ch_name = _action["send_channel"]
                                    try:
                                        config = ctx.channel_dispatcher.get_config(ch_name)
                                        if not config or not config.url:
                                            print(f"[Termin] Channel '{ch_name}': no deploy config, send skipped")
                                            return
                                        headers = ctx.channel_dispatcher._build_headers(config)
                                        resp = _httpx.post(config.url, json=_record, headers=headers,
                                                           timeout=config.timeout_ms / 1000.0)
                                        log = _ev.get("log_level", "INFO")
                                        print(f"[Termin] [{log}] Event sent {_action.get('send_content', 'record')} to channel '{ch_name}' (HTTP {resp.status_code})")
                                    except Exception as e:
                                        print(f"[Termin] [ERROR] Channel send to '{ch_name}' failed: {e}")
                                threading.Thread(target=_sync_send, daemon=True).start()
                            await ctx.event_bus.publish({
                                "type": f"{ev.get('source_content', '')}_event",
                                "log_level": ev.get("log_level", "INFO")})
                    except Exception as _ev_err:
                        print(f"[Termin] [WARN] Event handler error: {_ev_err}")

        # Event-triggered Computes (G6)
        event_type = f"{content_name.rstrip('s') if content_name.endswith('s') else content_name}.{trigger}"
        singular = ctx.singular_lookup.get(content_name, "")
        event_type_singular = f"{singular}.{trigger}" if singular else event_type

        for comp in ctx.trigger_computes:
            trigger_spec = comp.get("trigger", "")
            if trigger_spec.startswith("event "):
                trigger_event = trigger_spec[len("event "):].strip().strip('"')
                if trigger_event in (event_type, event_type_singular, f"{content_name}.{trigger}"):
                    where_expr = comp.get("trigger_where")
                    if where_expr:
                        wctx = dict(record)
                        snake_sing = ctx.singular_lookup.get(
                            content_name,
                            content_name.rstrip("s") if content_name.endswith("s") else content_name)
                        prefixed = dict(wctx)
                        prefixed["created"] = True
                        prefixed["updated"] = True
                        wctx[snake_sing] = prefixed
                        try:
                            if not ctx.expr_eval.evaluate(where_expr, wctx):
                                continue
                        except Exception:
                            continue

                    _main_loop = asyncio.get_event_loop()

                    def _run_compute(_comp=comp, _record=dict(record),
                                     _content=content_name, _loop=_main_loop):
                        import asyncio as _aio
                        bg_loop = _aio.new_event_loop()
                        try:
                            bg_loop.run_until_complete(
                                execute_compute(ctx, _comp, _record, _content, _loop))
                        except Exception as e:
                            print(f"[Termin] [ERROR] Compute '{_comp['name']['display']}' failed: {e}")
                        finally:
                            bg_loop.close()
                    threading.Thread(target=_run_compute, daemon=True).start()

    ctx.run_event_handlers = run_event_handlers
    ctx.execute_compute = lambda comp, record=None, content_name="", main_loop=None: \
        execute_compute(ctx, comp, record or {}, content_name, main_loop)

    # Content schemas for storage init
    schemas = list(ir.get("content", []))

    # ── Lifespan ──
    @asynccontextmanager
    async def lifespan(app):
        print(f"[Termin] Phase 0: Bootstrap")
        print(f"[Termin] Phase 1: TerminAtor initialized")
        print(f"[Termin] Phase 2: Expression evaluator ready")
        print(f"[Termin] Phase 3: Initializing storage")
        # v0.9 Phase 2.x (b): full migration flow — read current
        # schema, compute classified diff, fold operator-declared
        # renames, downgrade for empty tables, gate on ack, create
        # backup if high risk, apply atomically with validation.
        # See docs/migration-classifier-design.md for the design.
        from .migrations import (
            compute_migration_diff, apply_rename_mappings,
            downgrade_for_empty_tables, ack_covers,
            format_blocked_error, format_unacked_error,
        )
        from .migrations.errors import (
            MigrationBlockedError, MigrationAckRequiredError,
            MigrationBackupRefusedError,
        )
        # Migration policy from deploy config (operator-controlled).
        migrations_cfg = (
            deploy_config.get("migrations", {})
            if isinstance(deploy_config, dict) else {}
        )
        # 1. Read current schema (None on first-ever-deploy; v0.8
        #    DBs are detected via PRAGMA introspection inside the
        #    provider).
        current_schemas = await ctx.storage.read_schema_metadata()
        # 2. Compute pure diff.
        diff = compute_migration_diff(current_schemas, schemas)
        # 3. Apply operator-declared rename mappings.
        diff = apply_rename_mappings(
            diff,
            rename_fields=migrations_cfg.get("rename_fields", ()),
            rename_contents=migrations_cfg.get("rename_contents", ()),
        )
        # 4. Empty-table downgrade.
        diff = await downgrade_for_empty_tables(diff, ctx.storage)
        # 5. Block / ack gating.
        if diff.is_blocked:
            raise MigrationBlockedError(format_blocked_error(diff))
        if diff.needs_ack and not ack_covers(diff, migrations_cfg):
            raise MigrationAckRequiredError(
                format_unacked_error(diff, migrations_cfg))
        # 6. Backup if high-risk.
        backup_id = None
        if diff.has_high_risk:
            backup_id = await ctx.storage.create_backup()
            if backup_id is None:
                raise MigrationBackupRefusedError(
                    "Provider cannot create a backup. High-risk "
                    "migration refused. Back up externally before "
                    "retrying."
                )
            print(f"[Termin] Backup created before high-risk "
                  f"migration: {backup_id}")
            print(f"[Termin] Backup retention is your responsibility — "
                  f"delete or archive once you're confident in the new "
                  f"app behavior.")
        # 7. Apply atomically; provider's internal validation step
        #    gates the COMMIT.
        await ctx.storage.migrate(diff)
        # 8. Persist new last-known-good schema.
        await ctx.storage.write_schema_metadata(schemas)
        if any(c.kind != "added" for c in diff.changes):
            print(f"[Termin] Migration committed: "
                  f"{len(diff.changes)} change(s) applied "
                  f"(overall classification: {diff.overall_classification})")

        # v0.9 Phase 5a.3: lazy-create the runtime-managed
        # `_termin_principal_preferences` table alongside the
        # other private tables. Endpoints lazy-create on first
        # write too, but creating up-front avoids a one-shot delay
        # on the first preference set.
        import sqlite3 as _sqlite3
        from .preferences import ensure_preferences_table
        _conn = _sqlite3.connect(resolved_db_path)
        try:
            ensure_preferences_table(_conn)
            _conn.commit()
        finally:
            _conn.close()

        # Seed data
        if seed_data:
            db = await get_db(resolved_db_path)
            try:
                for content_name, records in seed_data.items():
                    cnt = await count_records(db, content_name)
                    if cnt == 0:
                        for record in records:
                            await insert_raw(db, content_name, record)
                        print(f"[Termin] Seeded {len(records)} records into {content_name}")
            finally:
                await db.close()

        print(f"[Termin] Phase 4: Registering primitives")

        # Inbound WebSocket handler for channel dispatcher
        async def _handle_inbound_ws(channel_name: str, data: dict):
            spec = ctx.channel_dispatcher.get_spec(channel_name)
            if not spec:
                return
            carries = spec.get("carries_content", "")
            if not carries:
                return
            schema = ctx.content_lookup.get(carries)
            if not schema:
                return
            known_cols = set()
            for f in schema.get("fields", []):
                fname = f.get("name", "")
                known_cols.add(fname if isinstance(fname, str) else fname.get("snake", ""))
            record_data = {k: v for k, v in data.items() if k in known_cols}
            if not record_data:
                return
            db = await get_db(resolved_db_path)
            try:
                record = await create_record(db, carries, record_data, ctx.sm_lookup.get(carries, []))
                await run_event_handlers(db, carries, "created", record)
                await ctx.event_bus.publish({
                    "channel_id": f"content.{carries}.created", "data": record})
                print(f"[Termin] Inbound WS '{channel_name}': created {carries} record (id={record.get('id', '?')})")
            finally:
                await db.close()

        ctx.channel_dispatcher.on_ws_message(_handle_inbound_ws)
        await ctx.channel_dispatcher.startup(strict=strict_channels)

        # v0.9 Phase 3: compute provider summary. Providers were
        # constructed at create_termin_app time; here we just log
        # what's bound for ops visibility.
        if ctx.compute_providers:
            print(
                f"[Termin] Phase 4b: {len(ctx.compute_providers)} "
                f"compute provider(s) bound: "
                f"{', '.join(sorted(ctx.compute_providers))}"
            )
        elif ctx.trigger_computes:
            unbound = [
                c["name"]["display"]
                for c in ctx.trigger_computes
                if c.get("provider") in ("llm", "ai-agent")
                and c["name"]["snake"] not in ctx.compute_providers
            ]
            if unbound:
                print(
                    f"[Termin] Phase 4b: {len(unbound)} LLM/agent "
                    f"Compute(s) have no deploy binding and will be "
                    f"skipped: {', '.join(unbound)}"
                )

        config_warnings = check_deploy_config_warnings(deploy_config, ir)
        for w in config_warnings:
            print(f"[Termin] WARNING: {w}")

        # Scheduler
        scheduler = Scheduler()
        for comp, interval in ctx.schedule_computes:
            scheduler.register(comp, interval, ctx.execute_compute)
        if scheduler.task_count:
            await scheduler.start()
            print(f"[Termin] Phase 4c: Scheduler started ({scheduler.task_count} task(s))")

        configured_channels = [
            ch["name"]["display"] for ch in ir.get("channels", [])
            if ctx.channel_dispatcher.is_configured(ch["name"]["display"])]
        if configured_channels:
            print(f"[Termin] Phase 4a: Channels connected: {', '.join(configured_channels)}")
        elif ir.get("channels"):
            internal_only = all(ch.get("direction") == "INTERNAL" for ch in ir.get("channels", []))
            if internal_only:
                print(f"[Termin] Phase 4a: {len(ir['channels'])} internal channel(s)")
            else:
                print(f"[Termin] Phase 4a: {len(ir['channels'])} channel(s) declared (no deploy config)")

        print(f"[Termin] Phase 5a: Starting WebSocket forwarder")

        async def _ws_forwarder():
            q = ctx.event_bus.subscribe()
            try:
                while True:
                    event = await q.get()
                    ch_id = event.get("channel_id")
                    if ch_id:
                        await ctx.conn_manager.broadcast_to_subscribers(ch_id, event)
            except asyncio.CancelledError:
                pass
            finally:
                ctx.event_bus.unsubscribe(q)

        forwarder = asyncio.create_task(_ws_forwarder())
        print(f"[Termin] Phase 5: Ready to serve")
        yield
        forwarder.cancel()
        await scheduler.stop()
        await ctx.channel_dispatcher.shutdown()
        print(f"[Termin] Shutting down...")

    # ── Create FastAPI app ──
    app = FastAPI(title=app_name, lifespan=lifespan)

    # Slice 7.2 of Phase 7 (2026-04-30): translate framework-agnostic
    # TerminRuntimeError exceptions raised by code in termin-core
    # (validation, state machines, transitions, …) into FastAPI
    # responses. The handler matches the legacy HTTPException(detail=…)
    # response body shape so conformance tests don't see a contract
    # change. ``extra`` rides through unchanged, letting clients see
    # structured fields (`field`, `allowed`, etc.) when the runtime
    # error carries them.
    from fastapi.responses import JSONResponse
    from termin_core.errors import TerminRuntimeError

    @app.exception_handler(TerminRuntimeError)
    async def _termin_runtime_error_handler(request, exc: TerminRuntimeError):
        body: dict = {"detail": exc.detail}
        if exc.extra:
            body.update(exc.extra)
        return JSONResponse(status_code=exc.status_code, content=body)

    # Set-role endpoint
    @app.post("/set-role")
    async def set_role(role: str = Form(...), user_name: str = Form("")):
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie("termin_role", role)
        if user_name:
            response.set_cookie("termin_user_name", user_name)
        return response

    # ── Register all subsystem routes ──
    register_runtime_endpoints(app, ctx)
    register_websocket_routes(app, ctx)
    register_crud_routes(app, ctx)
    register_reflection_routes(app, ctx)
    register_compute_endpoint(app, ctx)
    register_transition_routes(app, ctx)
    register_sse_routes(app, ctx)
    register_page_routes(app, ctx)
    register_channel_routes(app, ctx)

    # Stash the RuntimeContext on app.state for introspection by
    # tests, debugging tools, and runtime extension code that wants
    # to access ctx.identity_provider / ctx.contract_registry / etc.
    # Not part of the public ASGI contract — consumers using this
    # accept that the field is runtime-internal.
    app.state.ctx = ctx

    return app
