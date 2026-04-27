# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5b.4 platform: CSR bundle discovery endpoint
and the termin.js extension API surface.

Per BRD #2 §7.4 + JL's option-(c)/option-(d) decisions:

  * Provider declares its bundle URL via `csr_bundle_url()` on the
    PresentationProvider Protocol; deploy config can override via
    `bindings.presentation.<contract>.config.bundle_url_override`.
  * `GET /_termin/presentation/bundles` enumerates the URLs the
    runtime expects termin.js to load at boot.
  * termin.js exposes `Termin.registerRenderer(contract, fn)` and
    `Termin.getRenderer(contract)` for CSR bundles to register their
    per-contract render functions.

This slice ships the platform pieces. Carbon (5b.4 full) and GOV.UK
(5b.5) consume them later — the actual provider implementations are
out of scope here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest


# ── Discovery helper (server-side, pure) ──

def test_collect_csr_bundles_empty_when_no_csr_providers():
    """Tailwind-default ships SSR-only — its csr_bundle_url returns
    None. With only SSR providers registered, the bundle list is
    empty."""
    from termin_runtime.presentation_bundles import collect_csr_bundles

    class SsrOnly:
        declared_contracts = ("presentation-base.data-table",)
        render_modes = ("ssr",)
        def csr_bundle_url(self) -> Optional[str]:
            return None

    bundles = collect_csr_bundles(
        bound_providers=[("presentation-base.data-table", "tailwind-default", SsrOnly())],
        deploy_config={},
    )
    assert bundles == []


def test_collect_csr_bundles_includes_csr_providers():
    """A CSR provider's `csr_bundle_url` is reported under its
    qualified contract name and product."""
    from termin_runtime.presentation_bundles import collect_csr_bundles

    class FakeCarbon:
        declared_contracts = ("presentation-base.data-table",)
        render_modes = ("csr",)
        def csr_bundle_url(self) -> Optional[str]:
            return "https://cdn.example.com/carbon-data-table.js"

    bundles = collect_csr_bundles(
        bound_providers=[("presentation-base.data-table", "carbon", FakeCarbon())],
        deploy_config={},
    )
    assert bundles == [{
        "contract": "presentation-base.data-table",
        "provider": "carbon",
        "url": "https://cdn.example.com/carbon-data-table.js",
    }]


def test_deploy_config_bundle_url_override_wins():
    """JL's option (c): provider declares URL; deploy config can
    override per-contract via
    `bindings.presentation.<contract>.config.bundle_url_override`."""
    from termin_runtime.presentation_bundles import collect_csr_bundles

    class FakeCarbon:
        declared_contracts = ("presentation-base.data-table",)
        render_modes = ("csr",)
        def csr_bundle_url(self) -> Optional[str]:
            return "https://cdn.example.com/default.js"

    deploy_config = {
        "bindings": {
            "presentation": {
                "presentation-base.data-table": {
                    "provider": "carbon",
                    "config": {
                        "bundle_url_override": "https://internal.acme.com/carbon.js",
                    },
                }
            }
        }
    }
    bundles = collect_csr_bundles(
        bound_providers=[("presentation-base.data-table", "carbon", FakeCarbon())],
        deploy_config=deploy_config,
    )
    assert bundles[0]["url"] == "https://internal.acme.com/carbon.js"


def test_collect_csr_bundles_handles_dual_mode_provider():
    """A provider advertising both `ssr` and `csr` is listed only
    when its bundle URL is present (i.e., it has CSR support
    enabled at this binding)."""
    from termin_runtime.presentation_bundles import collect_csr_bundles

    class DualMode:
        declared_contracts = ("presentation-base.text",)
        render_modes = ("ssr", "csr")
        def csr_bundle_url(self) -> Optional[str]:
            return "https://example.com/dual.js"

    bundles = collect_csr_bundles(
        bound_providers=[("presentation-base.text", "dual", DualMode())],
        deploy_config={},
    )
    assert len(bundles) == 1
    assert bundles[0]["url"] == "https://example.com/dual.js"


def test_collect_csr_bundles_dedupes_same_url_across_contracts():
    """One provider serving multiple contracts with one bundle
    surfaces the bundle once per contract — the client may need to
    know the per-contract registration mapping even if the bundle
    is the same JS file. No dedup of URL itself."""
    from termin_runtime.presentation_bundles import collect_csr_bundles

    class MultiContract:
        declared_contracts = (
            "presentation-base.data-table",
            "presentation-base.form",
        )
        render_modes = ("csr",)
        def csr_bundle_url(self) -> Optional[str]:
            return "https://cdn.example.com/carbon-bundle.js"

    instance = MultiContract()
    bundles = collect_csr_bundles(
        bound_providers=[
            ("presentation-base.data-table", "carbon", instance),
            ("presentation-base.form", "carbon", instance),
        ],
        deploy_config={},
    )
    assert len(bundles) == 2
    assert {b["contract"] for b in bundles} == {
        "presentation-base.data-table",
        "presentation-base.form",
    }


# ── HTTP endpoint integration ──

@pytest.fixture
def app_with_carbon_stub():
    """Build a minimal FastAPI app whose context has a fake CSR
    provider in `presentation_providers`. Bypasses the full
    create_termin_app pipeline."""
    from fastapi import FastAPI, Request
    from termin_runtime.presentation_bundles import (
        register_presentation_bundle_endpoint,
    )

    class FakeCarbon:
        declared_contracts = ("presentation-base.data-table",)
        render_modes = ("csr",)
        def csr_bundle_url(self) -> Optional[str]:
            return "https://cdn.example.com/carbon.js"

    class StubCtx:
        presentation_providers = [
            ("presentation-base.data-table", "carbon", FakeCarbon()),
        ]
        deploy_config = {}

    ctx = StubCtx()
    app = FastAPI()
    register_presentation_bundle_endpoint(app, ctx)
    return app


def test_bundles_endpoint_returns_json_list(app_with_carbon_stub):
    from fastapi.testclient import TestClient

    with TestClient(app_with_carbon_stub) as client:
        resp = client.get("/_termin/presentation/bundles")
        assert resp.status_code == 200
        body = resp.json()
        assert "bundles" in body
        assert isinstance(body["bundles"], list)
        assert body["bundles"][0]["contract"] == "presentation-base.data-table"
        assert body["bundles"][0]["url"] == "https://cdn.example.com/carbon.js"


def test_bundles_endpoint_empty_when_only_ssr():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from termin_runtime.presentation_bundles import (
        register_presentation_bundle_endpoint,
    )

    class SsrOnly:
        declared_contracts = ("presentation-base.text",)
        render_modes = ("ssr",)
        def csr_bundle_url(self) -> Optional[str]:
            return None

    class StubCtx:
        presentation_providers = [
            ("presentation-base.text", "tailwind-default", SsrOnly()),
        ]
        deploy_config = {}

    app = FastAPI()
    register_presentation_bundle_endpoint(app, StubCtx())

    with TestClient(app) as client:
        resp = client.get("/_termin/presentation/bundles")
        assert resp.status_code == 200
        assert resp.json() == {"bundles": []}


# ── termin.js client API surface ──

def test_termin_js_exposes_registerRenderer():
    """termin.js's public `Termin` global gains
    `registerRenderer(contract, fn)` and `getRenderer(contract)`
    for CSR bundles to plug in. Sanity-check the surface so future
    refactors don't silently drop it."""
    js_path = (
        Path(__file__).parent.parent
        / "termin_runtime" / "static" / "termin.js"
    )
    src = js_path.read_text(encoding="utf-8")
    assert "registerRenderer" in src
    assert "getRenderer" in src
    # The bundle-loading code must call the discovery endpoint at boot.
    assert "/_termin/presentation/bundles" in src
