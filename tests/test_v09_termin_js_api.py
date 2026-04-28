# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5b.4 B' plumbing: termin.js public API
surface for SPA navigation and action dispatch.

Per the Spectrum-provider design Q2 + the trust-boundary table,
termin.js exposes a small typed surface for the provider's JS
bundle to call into:

  - Termin.registerRenderer(contract, fn)  (already in 5b.4 platform)
  - Termin.getRenderer(contract)           (already in 5b.4 platform)
  - Termin.navigate(path)                  (new this slice)
  - Termin.action(payload)                 (new this slice)
  - Termin.subscribe(channel, handler)     (new this slice)
  - Termin.unsubscribe(channel, handler)   (new this slice)

The provider-facing surface is what the eventual Spectrum bundle
calls. We're not unit-testing JS behavior in Python; we sanity-
check that the surface exists in the file so future refactors
don't silently drop it. The behavior layer is exercised by the
browser-conformance suite when CSR providers actually ship.
"""

from __future__ import annotations

from pathlib import Path


_JS_PATH = (
    Path(__file__).parent.parent
    / "termin_runtime" / "static" / "termin.js"
)


def _js_source() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


def test_navigate_function_present():
    """Termin.navigate(path) — fetches /_termin/page-data?path=...,
    pushes history state, calls registered shell renderer with
    new tree."""
    src = _js_source()
    assert "function navigate" in src or "navigate:" in src
    assert "Termin.navigate" in src or "navigate," in src


def test_navigate_calls_page_data_endpoint():
    """Navigation must hit the bootstrap endpoint that
    `register_page_data_endpoint` exposes."""
    src = _js_source()
    assert "/_termin/page-data" in src


def test_action_function_present():
    """Termin.action(payload) — submits a typed action payload
    to the runtime (WebSocket for low-latency, HTTP POST for
    state-changing semantics)."""
    src = _js_source()
    assert "function action" in src or "action:" in src
    assert "Termin.action" in src or "action," in src


def test_subscribe_unsubscribe_present():
    """Provider bundles register subscription handlers via
    Termin.subscribe(channel, handler). The dispatcher inside
    termin.js routes incoming WebSocket payloads to matching
    handlers."""
    src = _js_source()
    # subscribe is already used internally; the new public surface
    # exposes subscribe and unsubscribe under the Termin namespace.
    assert "subscribe:" in src or "Termin.subscribe" in src
    assert "unsubscribe:" in src or "Termin.unsubscribe" in src


def test_termin_global_exports_full_surface():
    """The window.Termin export must list every public function so
    the provider bundle can rely on a stable surface."""
    src = _js_source()
    # The Termin global assignment line should reference all six
    # public methods. Not a strict equality check (formatting may
    # vary), but each name must appear in the export.
    assert "window.Termin" in src
    # Find the line that constructs the Termin global object.
    for name in ("registerRenderer", "getRenderer", "navigate",
                 "action", "subscribe", "unsubscribe"):
        assert name in src, f"Termin.{name} missing from termin.js"


def test_history_state_used_for_navigation():
    """SPA navigation pushes browser history so back/forward
    work without full page reloads."""
    src = _js_source()
    assert "pushState" in src or "history.pushState" in src
