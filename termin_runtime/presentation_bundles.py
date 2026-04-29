# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 5b.4 platform: CSR bundle discovery for presentation
providers.

Per BRD #2 §7.4 + JL-resolved options (c) and (d) from the 2026-04-27
briefings round:

  (c) Provider declares its bundle URL via `csr_bundle_url()` on the
      PresentationProvider Protocol; deploy config can override per-
      contract via
      `bindings.presentation.<contract>.config.bundle_url_override`.
  (d) termin.js gets a per-contract registration API:
      `Termin.registerRenderer(contract, fn)` / `Termin.getRenderer`.

This module ships:

  * `collect_csr_bundles(bound_providers, deploy_config)` — pure
    function that walks bound presentation providers, calls
    `csr_bundle_url()` on each CSR-mode entry, applies the deploy
    override if present, and returns a deterministic JSON-friendly
    list one entry per (contract, provider, url) triple.

  * `register_presentation_bundle_endpoint(app, ctx)` — registers
    `GET /_termin/presentation/bundles` returning `{"bundles": [...]}`.
    termin.js fetches this at boot and injects `<script>` tags for
    each entry so CSR bundles can call `Termin.registerRenderer`.

The full provider dispatch cut-over (5b.3, deferred) is a separate
slice. This module operates on whatever bound-provider list the ctx
exposes; today that list is empty until 5b.3 lands. Carbon (5b.4) and
GOV.UK (5b.5) consume this scaffolding directly when they ship.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


def collect_csr_bundles(
    bound_providers: Iterable[tuple[str, str, object]],
    deploy_config: dict,
) -> list[dict]:
    """Build the bundle-discovery list from the runtime's bound
    presentation providers.

    Args:
      bound_providers: iterable of `(qualified_contract_name,
        product_name, provider_instance)` triples. Typically sourced
        from `ctx.presentation_providers` once 5b.3 cut-over lands;
        callers can pass any iterable for testing.
      deploy_config: the active deploy config dict. Per-contract
        overrides come from
        `bindings.presentation.<contract>.config.bundle_url_override`.

    Returns:
      A list of dicts shaped `{"contract": str, "provider": str,
      "url": str}` — one per CSR-mode binding. SSR-only providers
      (those whose `csr_bundle_url()` returns None) are excluded.
      Order is the iteration order of `bound_providers`.
    """
    presentation_bindings = (
        (deploy_config or {}).get("bindings", {}).get("presentation", {})
    )

    bundles: list[dict] = []
    for contract, product, provider in bound_providers:
        # The provider may be SSR-only — `csr_bundle_url` returning
        # None is the contract for "no bundle to load."
        get_url = getattr(provider, "csr_bundle_url", None)
        url = get_url() if callable(get_url) else None
        if not url:
            continue

        # Per-contract deploy override wins over the provider's
        # declared URL — operator can pin a vendored / mirrored /
        # CSP-allowlisted location without changing source.
        override = (
            presentation_bindings.get(contract, {})
            .get("config", {})
            .get("bundle_url_override")
        )
        if override:
            url = override

        bundles.append({
            "contract": contract,
            "provider": product,
            "url": url,
        })
    return bundles


def register_presentation_bundle_endpoint(app: FastAPI, ctx) -> None:
    """Register `GET /_termin/presentation/bundles` on `app`.

    The context object must expose `presentation_providers` (an
    iterable of `(contract, product, instance)` triples) and
    `deploy_config` (the active dict). The endpoint is unauthenticated
    — the bundle list is read by termin.js at every page boot, and the
    URLs themselves are public asset references. Provider config
    (which may contain secrets) lives elsewhere and is not surfaced.
    """

    @app.get("/_termin/presentation/bundles")
    async def list_presentation_bundles():
        return {
            "bundles": collect_csr_bundles(
                bound_providers=getattr(ctx, "presentation_providers", []),
                deploy_config=getattr(ctx, "deploy_config", {}) or {},
            )
        }


def _provider_bundle_path(provider) -> Optional[Path]:
    """Resolve the on-disk path of a provider's CSR bundle.

    The convention: the provider's package ships its built JS bundle as
    `static/bundle.js` adjacent to the provider class's defining module.
    This works for sibling-installed packages (pip install -e ...) and
    wheel-installed ones identically — `Path(module.__file__).parent` is
    the package directory either way.

    Returns None if the bundle file isn't present (provider failed to
    build, or shipped without it). The route surfaces that as a 404.
    """
    module_name = provider.__class__.__module__
    module = sys.modules.get(module_name)
    if module is None or not getattr(module, "__file__", None):
        return None
    package_dir = Path(module.__file__).resolve().parent
    bundle = package_dir / "static" / "bundle.js"
    return bundle if bundle.is_file() else None


def register_provider_bundle_route(app: FastAPI, ctx) -> None:
    """Register `GET /_termin/providers/{product}/bundle.js`.

    Looks up the named product in `ctx.presentation_providers` and
    serves its bundle from the provider package's `static/bundle.js`
    file. The default `csr_bundle_url()` on Spectrum (and the
    convention for any CSR-mode first-party provider) returns this
    URL shape; the route resolves it without any per-provider routing
    code.

    Operators who CDN-host the bundle override `csr_bundle_url` via
    `bindings.presentation.<contract>.config.bundle_url_override` —
    the CDN URL appears in the bundle-discovery list, the browser
    fetches it from the CDN, and this route is never called. The
    route is the self-hosted path; the override is the CDN escape
    hatch.
    """

    @app.get("/_termin/providers/{product}/bundle.js")
    async def serve_provider_bundle(product: str):
        for _contract, prod_name, provider in getattr(
            ctx, "presentation_providers", []
        ):
            if prod_name == product:
                path = _provider_bundle_path(provider)
                if path is None:
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            f"Provider {product!r} is registered but its "
                            f"bundle file is not built. Run the provider "
                            f"package's build (e.g., `npm run build`) "
                            f"and reload."
                        ),
                    )
                return FileResponse(
                    path,
                    media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"},
                )
        raise HTTPException(
            status_code=404,
            detail=f"No registered presentation provider with product name {product!r}",
        )
