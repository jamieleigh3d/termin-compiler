# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5b.4 B' loop: provider bundle serving route.

Pairs with `register_presentation_bundle_endpoint` (which lists URLs)
and `register_provider_bundle_route` (which serves bytes from a
provider package's `static/bundle.js`). Verifies:

  - 404 when the product name is not registered
  - 404 when the product is registered but the bundle file is absent
  - 200 + correct bytes when the bundle file exists
  - bundle-URL override path bypasses this route entirely
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from termin_server.presentation_bundles import (
    _provider_bundle_path,
    register_provider_bundle_route,
    collect_csr_bundles,
)


# ── Test doubles ──

class _FakeProvider:
    """Minimal CSR provider — declares a bundle URL, claims a module."""

    def __init__(self, bundle_url="/_termin/providers/fake/bundle.js"):
        self._url = bundle_url

    def csr_bundle_url(self):
        return self._url


class _SsrOnlyProvider:
    """No `csr_bundle_url` method — exercises the SSR-only branch."""


class _Ctx:
    def __init__(self, presentation_providers=(), deploy_config=None):
        self.presentation_providers = list(presentation_providers)
        self.deploy_config = deploy_config or {}


# ── Bundle path resolution ──

def test_provider_bundle_path_returns_none_when_module_missing(tmp_path):
    class Detached:
        pass
    Detached.__module__ = "nonexistent.module.path"
    assert _provider_bundle_path(Detached()) is None


def test_provider_bundle_path_returns_none_when_static_missing(tmp_path, monkeypatch):
    """If the provider's package is on sys.modules but has no static/
    bundle.js next to it, the resolver returns None and the route
    surfaces a 404."""
    import sys, types
    pkg = types.ModuleType("fake_provider_pkg")
    pkg.__file__ = str(tmp_path / "fake_provider_pkg" / "__init__.py")
    (tmp_path / "fake_provider_pkg").mkdir()
    Path(pkg.__file__).touch()
    sys.modules["fake_provider_pkg"] = pkg
    try:
        class Provider:
            pass
        Provider.__module__ = "fake_provider_pkg"
        assert _provider_bundle_path(Provider()) is None
    finally:
        del sys.modules["fake_provider_pkg"]


def test_provider_bundle_path_finds_bundle(tmp_path):
    """When static/bundle.js exists alongside the provider's package,
    the resolver returns its absolute path."""
    import sys, types
    pkg_dir = tmp_path / "fake_pkg"
    (pkg_dir / "static").mkdir(parents=True)
    bundle = pkg_dir / "static" / "bundle.js"
    bundle.write_text("/* fake bundle */")
    pkg = types.ModuleType("fake_pkg")
    pkg.__file__ = str(pkg_dir / "__init__.py")
    sys.modules["fake_pkg"] = pkg
    try:
        class Provider:
            pass
        Provider.__module__ = "fake_pkg"
        result = _provider_bundle_path(Provider())
        assert result == bundle.resolve()
    finally:
        del sys.modules["fake_pkg"]


# ── Route behavior ──

def _make_app(ctx):
    app = FastAPI()
    register_provider_bundle_route(app, ctx)
    return app


def test_route_404_for_unregistered_product():
    ctx = _Ctx()
    with TestClient(_make_app(ctx)) as client:
        resp = client.get("/_termin/providers/nope/bundle.js")
        assert resp.status_code == 404


def test_route_404_when_bundle_file_missing(tmp_path):
    """Provider is registered but the static/bundle.js doesn't exist —
    e.g., the operator forgot to run the build before starting the
    runtime. The error message points at the build step."""
    import sys, types
    pkg = types.ModuleType("fake_no_bundle_pkg")
    pkg.__file__ = str(tmp_path / "fake_no_bundle_pkg" / "__init__.py")
    (tmp_path / "fake_no_bundle_pkg").mkdir()
    Path(pkg.__file__).touch()
    sys.modules["fake_no_bundle_pkg"] = pkg
    try:
        class P:
            pass
        P.__module__ = "fake_no_bundle_pkg"
        ctx = _Ctx(presentation_providers=[
            ("presentation-base.text", "fake", P()),
        ])
        with TestClient(_make_app(ctx)) as client:
            resp = client.get("/_termin/providers/fake/bundle.js")
            assert resp.status_code == 404
            # Must mention the build action so operators get a clear cue.
            assert "build" in resp.json()["detail"].lower()
    finally:
        del sys.modules["fake_no_bundle_pkg"]


def test_route_serves_bundle_bytes_with_correct_content_type(tmp_path):
    import sys, types
    pkg_dir = tmp_path / "served_pkg"
    (pkg_dir / "static").mkdir(parents=True)
    bundle_text = "console.log('hello from fake bundle');"
    (pkg_dir / "static" / "bundle.js").write_text(bundle_text)
    pkg = types.ModuleType("served_pkg")
    pkg.__file__ = str(pkg_dir / "__init__.py")
    sys.modules["served_pkg"] = pkg
    try:
        class P:
            pass
        P.__module__ = "served_pkg"
        ctx = _Ctx(presentation_providers=[
            ("presentation-base.text", "served", P()),
        ])
        with TestClient(_make_app(ctx)) as client:
            resp = client.get("/_termin/providers/served/bundle.js")
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith(
                "application/javascript"
            )
            assert resp.text == bundle_text
    finally:
        del sys.modules["served_pkg"]


# ── Discovery list interaction ──

def test_collect_csr_bundles_includes_self_hosted_default():
    """Without a deploy override, the discovery list should reflect
    the provider's `csr_bundle_url()` — which by convention is the
    self-hosted route this slice serves."""
    bundles = collect_csr_bundles(
        bound_providers=[
            ("presentation-base.text", "spectrum",
             _FakeProvider("/_termin/providers/spectrum/bundle.js")),
        ],
        deploy_config={},
    )
    assert len(bundles) == 1
    assert bundles[0]["url"] == "/_termin/providers/spectrum/bundle.js"
    assert bundles[0]["provider"] == "spectrum"


def test_collect_csr_bundles_honors_cdn_override():
    """When deploy config overrides the bundle URL, the discovery list
    points at the CDN — the runtime's bundle-serving route is bypassed."""
    bundles = collect_csr_bundles(
        bound_providers=[
            ("presentation-base.text", "spectrum",
             _FakeProvider("/_termin/providers/spectrum/bundle.js")),
        ],
        deploy_config={
            "bindings": {
                "presentation": {
                    "presentation-base.text": {
                        "config": {
                            "bundle_url_override":
                                "https://cdn.example.com/spectrum-1.0.0.js"
                        }
                    }
                }
            }
        },
    )
    assert bundles[0]["url"] == "https://cdn.example.com/spectrum-1.0.0.js"


def test_collect_csr_bundles_excludes_ssr_only_providers():
    """A provider whose `csr_bundle_url()` returns None (or is missing)
    must not appear in the list — there's no bundle to load."""
    bundles = collect_csr_bundles(
        bound_providers=[
            ("presentation-base.text", "tailwind-default", _SsrOnlyProvider()),
        ],
        deploy_config={},
    )
    assert bundles == []
