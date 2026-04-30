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


# Slice 7.3 of Phase 7 (2026-04-30) moved static assets to termin-server.
# Read termin.js from its installed package location so the path
# survives the v0.9 → v1.0 cleanup that drops the legacy
# `termin_runtime/static/` directory.
import termin_server
_JS_PATH = Path(termin_server.__file__).parent / "static" / "termin.js"


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


def test_handleFrame_dispatches_to_provider_subscriptions():
    """v0.9 Phase 5b.4 B' loop: when a WebSocket push arrives,
    handleFrame must dispatch to BOTH the legacy notifySubscribers
    surface (SSR-mode hydrators) AND the provider-subscription
    surface (`_dispatchToProviderSubscriptions`). Without the
    second call, B'-mode CSR providers' Termin.subscribe(...)
    handlers never fire on push events — the WebSocket is open,
    frames arrive, the bundle is silent.

    Regression test for the wiring landed 2026-04-29 alongside the
    Phase A spectrum slice. Greps the source rather than running
    the JS in a browser; the pattern (push branch in handleFrame
    calls `_dispatchToProviderSubscriptions`) is structural and
    a textual check is enough.
    """
    src = _js_source()
    # Find the handleFrame function and assert the dispatch call
    # is present in its push branch.
    handle_idx = src.find("function handleFrame")
    assert handle_idx != -1, "handleFrame should exist in termin.js"
    # Look at the next ~40 lines (push branch lives here)
    end_idx = src.find("} else if (op === \"response\")", handle_idx)
    assert end_idx != -1
    push_branch = src[handle_idx:end_idx]
    assert "_dispatchToProviderSubscriptions" in push_branch, (
        "handleFrame's push branch must call _dispatchToProviderSubscriptions "
        "so provider-registered Termin.subscribe handlers fire on WebSocket "
        "push events. See termin.js line ~200."
    )


def test_addSubscription_does_not_poison_legacy_state():
    """v0.9 Phase 5b.4 Spectrum chat regression: `_addSubscription`
    (the public Termin.subscribe entry point) must NOT pipe through
    the legacy `subscribe(channel)` helper without a callback. That
    helper stores its second argument in `state.subscriptions` —
    when called with `undefined` it pollutes the Set, and the next
    `notifySubscribers` invocation does `cbs.forEach(cb => cb(...))`
    which throws "cb is not a function" inside the WS message
    dispatcher. Surfaced as "[Termin] Bad frame: cb is not a function"
    spam any time a content.* push arrived.

    Fix: send the WebSocket subscribe frame directly via sendFrame
    inside `_addSubscription`, bypassing the legacy helper. The two
    subscription stores (state.subscriptions for SSR hydrators,
    _subscriptionHandlers for provider bundles) stay cleanly
    separated. Caught 2026-04-29 while wiring Spectrum chat.
    """
    src = _js_source()
    add_idx = src.find("function _addSubscription")
    assert add_idx != -1, "_addSubscription should exist in termin.js"
    end_idx = src.find("\nfunction ", add_idx + 1)
    assert end_idx != -1
    body = src[add_idx:end_idx]
    # The legacy helper is `subscribe(channelId, callback)` — calling
    # it with one arg is the regression. The new code uses sendFrame
    # for the subscribe-frame side effect.
    assert "sendFrame(\"subscribe\"" in body, (
        "_addSubscription must use sendFrame to open the server-side "
        "subscription, not the legacy `subscribe()` helper which "
        "would pollute state.subscriptions with undefined callbacks."
    )
    # Belt-and-braces: explicit guard against the regression. The
    # legacy single-arg `subscribe(channel);` call was the bug.
    assert "    subscribe(channel);" not in body, (
        "Don't call the legacy SSR-helper subscribe(channel) without "
        "a callback — it stores undefined in state.subscriptions and "
        "breaks notifySubscribers."
    )


def test_ws_onopen_replays_provider_subscriptions():
    """v0.9 Phase 5b.4 Spectrum chat regression #2: when the
    WebSocket (re)connects, `state.ws.onopen` must re-send subscribe
    frames for BOTH `state.subscriptions` (legacy SSR hydrators)
    AND `_subscriptionHandlers` (provider bundles).

    Provider-registered subscriptions typically race the WebSocket
    open event during initial mount: the React component's useEffect
    runs before the WS finishes its handshake, so `_addSubscription`'s
    in-band sendFrame call is a silent no-op (sendFrame returns null
    when ws.readyState !== OPEN). Without the onopen replay loop,
    those subscriptions never reach the server — the bundle subscribes,
    but no pushes arrive. Caught 2026-04-29 with the same Spectrum
    chat slice.
    """
    src = _js_source()
    onopen_idx = src.find("state.ws.onopen")
    assert onopen_idx != -1, "state.ws.onopen handler should exist"
    end_idx = src.find("};", onopen_idx)
    assert end_idx != -1
    onopen_body = src[onopen_idx:end_idx]
    # Both replay loops must be present.
    assert "state.subscriptions" in onopen_body, (
        "onopen must replay legacy SSR-hydrator subscriptions."
    )
    assert "_subscriptionHandlers" in onopen_body, (
        "onopen must also replay provider-registered subscriptions, "
        "or React-mounted Termin.subscribe handlers made before WS-open "
        "are silently dropped."
    )
