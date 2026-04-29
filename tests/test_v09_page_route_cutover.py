# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 5b.4 B' loop — page-route cut-over.

When the bound presentation provider for `presentation-base.page` is
CSR-only, visiting `/<slug>` (the natural URL) returns the B' shell
HTML instead of the SSR-Tailwind pipeline. When no provider is bound,
or when the bound provider supports SSR, the legacy SSR pipeline still
runs — backwards-compatible default.

This test set covers the gate decision and the cut-over behavior end
to end. Skips when termin-spectrum-provider isn't installed (CSR-only
provider needed to exercise the CSR-only path).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from termin_runtime import create_termin_app
from termin_runtime.bootstrap import page_should_use_shell

# CSR-only provider needed for the cut-over branch — skip the
# integration tests if termin-spectrum-provider isn't installed in
# this environment.
spectrum = pytest.importorskip("termin_spectrum")

from helpers import extract_ir_from_pkg


# ── Gate decision ──

class _Ctx:
    def __init__(self, presentation_providers=()):
        self.presentation_providers = list(presentation_providers)


def _make_provider(modes):
    """Helper — a provider double with the given render_modes tuple."""
    p = MagicMock()
    p.render_modes = tuple(modes)
    return p


def test_gate_returns_false_when_no_provider_bound():
    """Default state — no CSR provider, SSR pipeline runs."""
    assert page_should_use_shell(_Ctx()) is False


def test_gate_returns_false_when_provider_supports_ssr():
    """Tailwind-default supports SSR. Even once it adds CSR mode the
    gate stays false: bundles enhance SSR, they don't replace it."""
    ssr_only = _make_provider(("ssr",))
    both = _make_provider(("ssr", "csr"))
    assert page_should_use_shell(_Ctx([
        ("presentation-base.page", "tailwind-default", ssr_only),
    ])) is False
    assert page_should_use_shell(_Ctx([
        ("presentation-base.page", "tailwind-default", both),
    ])) is False


def test_gate_returns_true_when_provider_csr_only():
    """The Spectrum case — render_modes = ('csr',) only."""
    csr_only = _make_provider(("csr",))
    assert page_should_use_shell(_Ctx([
        ("presentation-base.page", "spectrum", csr_only),
    ])) is True


def test_gate_only_inspects_page_contract():
    """Gate looks at the `presentation-base.page` binding specifically.
    A CSR-only binding for `presentation-base.text` alone shouldn't
    flip the page route — page contract decides."""
    csr_only = _make_provider(("csr",))
    assert page_should_use_shell(_Ctx([
        ("presentation-base.text", "spectrum", csr_only),
    ])) is False


def test_gate_robust_to_provider_without_render_modes_attr():
    """Defensive — a provider missing render_modes should not crash
    the gate; treat absence as 'we don't know, fall back to SSR'."""
    bare = MagicMock(spec=[])  # spec=[] strips all default attrs
    assert page_should_use_shell(_Ctx([
        ("presentation-base.page", "weird", bare),
    ])) is False


# ── Page-route cut-over (integration) ──

@pytest.fixture
def hello_pkg(compiled_packages):
    return compiled_packages["hello"]


def test_hello_slug_serves_shell_when_spectrum_bound(hello_pkg):
    """`GET /hello` returns the B' shell HTML — embedded bootstrap,
    spectrum bundle script tag, no SSR-Tailwind markup."""
    deploy = {
        "version": "1.0.0",
        "bindings": {
            "presentation": {
                "presentation-base": {"provider": "spectrum", "config": {}},
            }
        },
    }
    ir = json.dumps(extract_ir_from_pkg(hello_pkg))
    app = create_termin_app(ir, deploy_config=deploy, strict_channels=False)
    with TestClient(app) as client:
        resp = client.get("/hello")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        # Shell signatures: termin-root container, embedded bootstrap,
        # spectrum bundle script tag.
        assert 'id="termin-root"' in resp.text
        assert "__termin_bootstrap" in resp.text
        assert "/_termin/providers/spectrum/bundle.js" in resp.text


def test_hello_slug_serves_ssr_when_no_csr_provider(hello_pkg):
    """Default deploy (no spectrum binding) — `/hello` still serves
    the SSR-Tailwind pipeline. Backwards-compatible default; no
    regression for apps that aren't using a CSR provider."""
    ir = json.dumps(extract_ir_from_pkg(hello_pkg))
    app = create_termin_app(ir, strict_channels=False)
    with TestClient(app) as client:
        resp = client.get("/hello")
        assert resp.status_code == 200
        # SSR signatures: termin-root NOT present (or only via shell);
        # the SSR template includes the literal page text and Tailwind
        # classes. The literal text is the simplest reliable marker.
        assert "Hello, World" in resp.text
        # And the embedded bootstrap data-island is NOT in the SSR
        # response — that's only in the B' shell path.
        assert "__termin_bootstrap" not in resp.text


def test_explicit_shell_url_still_works_alongside_cutover(hello_pkg):
    """`/_termin/shell?path=/hello` still serves the shell directly —
    the page-route cut-over doesn't replace it. Useful for dev /
    debugging / mode-switching at request time."""
    deploy = {
        "version": "1.0.0",
        "bindings": {
            "presentation": {
                "presentation-base": {"provider": "spectrum", "config": {}},
            }
        },
    }
    ir = json.dumps(extract_ir_from_pkg(hello_pkg))
    app = create_termin_app(ir, deploy_config=deploy, strict_channels=False)
    with TestClient(app) as client:
        slug_resp = client.get("/hello")
        shell_resp = client.get("/_termin/shell", params={"path": "/hello"})
        assert slug_resp.status_code == shell_resp.status_code == 200
        # Both paths produce equivalent shell HTML — same bootstrap
        # JSON, same script tags. Modulo any per-route headers, the
        # body should match.
        assert slug_resp.text == shell_resp.text
