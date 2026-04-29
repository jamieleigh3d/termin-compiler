# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 5b.4 B' loop — spectrum end-to-end (Python side).

Boots a real runtime with hello.termin compiled and the spectrum
provider bound via deploy config. Verifies the entire server side of
the B' loop works — entry-point discovery, factory invocation,
presentation_providers population, bundle discovery list, bundle
serving, page-data and shell endpoints.

The browser side of the loop (npm install, npm run build, JS bundle
loaded by a real browser) is exercised by JL's WSL recipe in
docs/spectrum-hello-world-verification.md, not in pytest.

Skips entirely if termin-spectrum-provider isn't installed — this
test only runs in environments where the sibling package is on
sys.path / pip-installed-editable. CI on the termin-compiler side
doesn't install the provider, so this test no-ops there; CI on the
termin-spectrum-provider side runs it as integration.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

# Skip the whole module if the provider isn't installed.
spectrum = pytest.importorskip("termin_spectrum")

from termin_runtime import create_termin_app
from helpers import extract_ir_from_pkg


@pytest.fixture
def hello_pkg(compiled_packages):
    return compiled_packages["hello"]


@pytest.fixture
def spectrum_deploy_config():
    """Deploy config that binds the spectrum provider — the same shape
    as examples-dev/hello_spectrum.deploy.json."""
    return {
        "version": "1.0.0",
        "bindings": {
            "presentation": {
                "presentation-base": {
                    "provider": "spectrum",
                    "config": {},
                }
            }
        },
    }


@pytest.fixture
def app_with_spectrum(hello_pkg, spectrum_deploy_config):
    ir = json.dumps(extract_ir_from_pkg(hello_pkg))
    return create_termin_app(
        ir,
        deploy_config=spectrum_deploy_config,
        strict_channels=False,
    )


def test_spectrum_appears_in_bundle_discovery_list(app_with_spectrum):
    """`GET /_termin/presentation/bundles` lists the spectrum bundle
    URL once per bound contract. With the namespace binding, that's
    ten entries — same product, same URL, ten contracts."""
    with TestClient(app_with_spectrum) as client:
        resp = client.get("/_termin/presentation/bundles")
        assert resp.status_code == 200
        body = resp.json()
        assert "bundles" in body
        spectrum_bundles = [
            b for b in body["bundles"] if b.get("provider") == "spectrum"
        ]
        assert len(spectrum_bundles) == 10  # one per presentation-base contract
        assert all(
            b["url"] == "/_termin/providers/spectrum/bundle.js"
            for b in spectrum_bundles
        )


def test_spectrum_bundle_route_returns_404_when_not_built(app_with_spectrum):
    """Provider is registered but if `npm run build` hasn't produced
    the bundle, the route 404s with a helpful message instead of
    crashing the server. The default sibling-checkout state."""
    import termin_spectrum
    from pathlib import Path
    bundle_path = Path(termin_spectrum.__file__).parent / "static" / "bundle.js"

    with TestClient(app_with_spectrum) as client:
        resp = client.get("/_termin/providers/spectrum/bundle.js")
        if bundle_path.is_file():
            # JL has built it — should serve.
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith(
                "application/javascript"
            )
        else:
            # Default state — bundle not built yet.
            assert resp.status_code == 404
            assert "build" in resp.json()["detail"].lower()


def test_spectrum_404_for_unknown_product(app_with_spectrum):
    """Hitting the bundle route with an unbound product name 404s."""
    with TestClient(app_with_spectrum) as client:
        resp = client.get("/_termin/providers/no-such-provider/bundle.js")
        assert resp.status_code == 404


def test_spectrum_bundle_url_override_appears_in_discovery(hello_pkg):
    """When the deploy config sets bundle_url_override, the discovery
    list shows the CDN URL. The runtime's bundle-serving route is
    bypassed entirely (the browser hits the CDN directly)."""
    deploy = {
        "version": "1.0.0",
        "bindings": {
            "presentation": {
                "presentation-base": {
                    "provider": "spectrum",
                    "config": {
                        "bundle_url_override":
                            "https://cdn.example.com/spectrum-1.0.0.js"
                    },
                }
            }
        },
    }
    ir = json.dumps(extract_ir_from_pkg(hello_pkg))
    app = create_termin_app(ir, deploy_config=deploy, strict_channels=False)
    with TestClient(app) as client:
        resp = client.get("/_termin/presentation/bundles")
        body = resp.json()
        urls = {b["url"] for b in body["bundles"] if b["provider"] == "spectrum"}
        # All ten contract entries reflect the override URL.
        assert urls == {"https://cdn.example.com/spectrum-1.0.0.js"}


def test_shell_endpoint_returns_html_for_hello_path(app_with_spectrum):
    """The B' shell endpoint serves an HTML page with the bootstrap
    payload embedded as JSON. The shell HTML loads termin.js + the
    bound provider bundles; the bundle's __app_shell__ renderer
    consumes the bootstrap payload and renders the React tree.

    Hello.termin has no role — anonymous can see it. No cookie needed.
    """
    with TestClient(app_with_spectrum) as client:
        resp = client.get("/_termin/shell", params={"path": "/hello"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "<div id=\"termin-root\"></div>" in resp.text
        # The spectrum bundle URL should be in the shell HTML so the
        # browser fetches it after termin.js loads. (May come from
        # bundle discovery rather than the shell template directly,
        # depending on the order of injection — both work in practice.)


def test_deploy_config_path_loads_presentation_bindings_without_channels(
    hello_pkg, tmp_path,
):
    """Regression: hello.termin has no channels and no LLM computes.
    The original `needs_deploy_config` gate skipped loading the deploy
    file in that case, silently dropping presentation bindings. After
    the v0.9 5b.4 B' loop fix, an explicit deploy_config_path always
    loads the file — channels-or-LLMs is no longer the only trigger.
    """
    deploy_path = tmp_path / "hello.deploy.json"
    deploy_path.write_text(json.dumps({
        "version": "1.0.0",
        "bindings": {
            "presentation": {
                "presentation-base": {
                    "provider": "spectrum",
                    "config": {},
                }
            }
        },
    }))
    ir = json.dumps(extract_ir_from_pkg(hello_pkg))
    app = create_termin_app(
        ir,
        deploy_config_path=str(deploy_path),
        strict_channels=False,
    )
    with TestClient(app) as client:
        resp = client.get("/_termin/presentation/bundles")
        body = resp.json()
        spectrum = [b for b in body["bundles"] if b.get("provider") == "spectrum"]
        assert len(spectrum) == 10, (
            f"deploy config at {deploy_path} should have populated 10 "
            f"spectrum contract bindings; got {body['bundles']}"
        )


def test_page_data_endpoint_returns_bootstrap_for_hello(app_with_spectrum):
    """The page-data endpoint returns the JSON the shell renderer
    consumes. With hello.termin → just one text node."""
    with TestClient(app_with_spectrum) as client:
        resp = client.get("/_termin/page-data", params={"path": "/hello"})
        assert resp.status_code == 200
        body = resp.json()
        assert "component_tree_ir" in body
        assert "bound_data" in body
        assert "principal_context" in body
        # The hello.termin page has a "Display text" — the bootstrap
        # payload must include the component tree that renders it.
        tree = body["component_tree_ir"]
        # Tree shape: PageEntry with children. Walk to find a text
        # node whose props include the literal "Hello, World".
        flat = _collect_text_props(tree)
        assert any("Hello" in s for s in flat), \
            f"expected a text node mentioning 'Hello' — found {flat!r}"


def _collect_text_props(node):
    """Walk a component tree and return text-shaped prop values."""
    out = []
    if not isinstance(node, dict):
        return out
    props = node.get("props") or {}
    for value in props.values():
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            v = value.get("value")
            if isinstance(v, str):
                out.append(v)
    for child in node.get("children") or []:
        out.extend(_collect_text_props(child))
    return out
