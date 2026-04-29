# Spectrum Provider Design — v0.9 Phase 5b.4

**Status:** DRAFT, decisions locked 2026-04-28 by JL through a five-question briefing pass.
**Scope:** Implementation-level design for the first first-party CSR presentation provider, replacing the original "Carbon" choice. Adobe Spectrum 2 is the chosen renderer library.
**Source-of-truth BRDs:** BRD #2 (`termin-presentation-provider-brd-v0.9.md`).
**Companion docs:**
- `presentation-provider-design.md` — Phase 5 overall design (5a/5b/5c structure).
- `airlock-termin-sketch.md` — example consumer of a non-`presentation-base` namespace.

This document captures the five architectural decisions that shape Spectrum's
landing in Termin and the broader CSR-provider ecosystem. Each section follows
the format: *executive summary, options considered, decision, rationale*. All
decisions are JL-resolved.

---

## Q1. Build pipeline location

**Decision: Per-provider packages.** Each provider — first-party or
third-party — ships as its own package with its own build pipeline and
release cadence. The `termin-compiler` repository hosts the compiler, the
reference runtime, and the contract Protocols. Provider implementations
(including Spectrum, Carbon, GOV.UK, and any future first-party providers)
live in their own repositories with their own CI.

**Rationale.** Tenet 4 (providers over primitives) is enforced
operationally by this separation. Keeping CSR providers' Node toolchains
out of `termin-compiler` keeps the compiler repo Python-only. The asymmetry
between first-party and third-party providers that hybrid options would
preserve violates BRD #1 §10's commitment that built-in providers load
through the same registry as third-party. Per-provider packaging is the
inevitable end-state once a real second runtime exists; locking it in as
the v0.9 standard pays the extraction cost once instead of twice.

**Options considered:**

- (A) Termin owns the build chain — adds Node toolchain to `termin-compiler`.
- (B) Per-provider packages, self-built. **Selected.**
- (C) Hybrid — first-party in-tree, third-party external.

**Local development convention** (documented in `CONTRIBUTING.md`):
sibling-checkout layout with editable installs (`pip install -e
../sibling-repo`, `npm link` for Node). Explicitly not git submodules
or environment variables.

---

## Q2. Architecture model — who owns the React tree?

**Decision: B' — server-authoritative + JS-as-renderer (LiveView-shaped).**
The runtime is authoritative for state, data, auth, confidentiality,
ownership, and routing-of-data. The provider's JS bundle is authoritative
for the visible surface — full React tree including page boundaries,
navigation chrome, layout, animations, focus management. Page navigation
is server-driven (the runtime resolves `<path>` → component-tree-IR →
bound-data) but feels like SPA navigation to the user (no full-page
reload; React tree swaps in place).

**Rationale.** A full SPA (option B as originally framed) gives away the
trust-plane benefits Termin's tenets require — confidentiality and
ownership cascade need to live in the runtime, not the client. A
per-contract renderer (option A) creates a fight between
`termin.js`'s DOM patching and React's reconciler over the same DOM nodes.
B' takes the LiveView pattern (Phoenix LiveView, Hotwire/Turbo): server
has authoritative state, client renders, "page navigation" is a
server-side fetch + client-side tree swap. This is trend-aligned (the
2026 industry consensus is server-authoritative + selective hydration,
not pure SPA) while preserving Carbon/Spectrum-style component-library
ergonomics on the client.

**Options considered:**

- (A) Per-contract renderer (BRD §7.4 as written).
- (B) Full SPA — provider owns the entire tree, runtime is data backend.
- (B') Server-authoritative + JS-as-renderer (LiveView-shaped). **Selected.**
- (C) Hybrid — SPA for some providers, per-contract for others.

**Trust boundary (load-bearing):**

| Owned by runtime (trust plane) | Owned by provider (presentation plane) |
|---|---|
| Authentication, scope checks | Visible HTML/DOM/React tree |
| Confidentiality redaction (rows + fields, `Redacted` sentinel) | Page boundaries, layout, navigation chrome |
| Row-level ownership filtering (Phase 6a.6) | URL → page-IR routing on the *server* (which page-IR to render); client swaps in place |
| Storage CRUD, Predicate AST, audit log | Animations, focus management, accessibility behaviors |
| WebSocket connection lifecycle, subscription routing | Form-state-while-typing, optimistic updates |
| Event bus, transition events | Anything visual that could change without server round-trip |

**Wire shape — the bootstrap JSON payload:**

```jsonc
{
  "component_tree_ir": { /* PageEntry IR for this path */ },
  "bound_data": { /* records, fields — already redacted, ownership-filtered */ },
  "principal_context": { /* id, scopes, preferences, role_set */ },
  "subscriptions_to_open": [ /* channel ids to subscribe via WebSocket */ ]
}
```

---

## Q3. Bundle hosting

**Decision: Self-hosted by default with deploy-config CDN override.**
The pip package `termin-spectrum-provider` ships the built `.js` file as
package data. The Python factory advertises a self-hosted URL
(`/runtime/providers/spectrum/bundle.js`) by default. Operators in
CDN-friendly environments override via
`bindings.presentation.<contract>.config.bundle_url_override` (the
mechanism shipped in 5b.4 platform).

**Rationale.** Air-gapped enterprise deployments (Kazoo's Amazon-internal
context, regulated environments, public-sector) need everything
self-contained. The audience tenet (compliance reviewers / security
officers read source) is hard to satisfy if the runtime fetches code from
`cdn.jsdelivr.net` at every page load. The deploy-config override path is
the existing 5b.4 mechanism; using it costs zero new architecture. The
pip-package size cost (~120KB gzipped per CSR provider) is real but
bounded — many production Python packages are larger.

**Options considered:**

- (A) Self-hosted as runtime static asset.
- (B) Public CDN (jsdelivr / unpkg).
- (C) Self-hosted by default with deploy-config CDN override. **Selected.**

---

## Q4. Theme variant mapping

**Decision: Augmented dark theme + explicit high-contrast token override
layer.** Spectrum 2 ships two themes (light, dark). Termin's enum has
four values (`light | dark | auto | high-contrast`). Mapping:

| Termin value | Spectrum behavior |
|---|---|
| `light` | `spectrum-light` |
| `dark` | `spectrum-dark` |
| `auto` | use `prefers-color-scheme` media query at render time |
| `high-contrast` | `spectrum-dark` + provider-shipped HC token override layer (~50-100 lines of CSS overriding Spectrum tokens with WCAG AAA contrast ratios) |

**Rationale.** A user who deliberately picks "high contrast" expects more
contrast, not the dark theme renamed. The minimal mapping (HC ≡ dark, rely
on browser `@media (forced-colors)` for the rest) leaves macular-degeneration
and low-vision users with no actual contrast bump unless their OS is in
forced-colors mode. The override layer authoring is cheap given the existing
CVD simulation + WCAG luminance helpers from Phase 5a.5 — iterative
paste-into-test-suite-and-tune-until-green workflow with the colorblind-safety
battery as the gate.

Forced-colors stacking still works: choosing this option doesn't disable
Spectrum's `@media (forced-colors)` handling. A Windows-HC user who *also*
picks Termin's `high-contrast` gets both layers; either alone gets one.

**Options considered:**

- (A) Minimal — HC falls back to `spectrum-dark`, browser handles forced-colors.
- (B) Augmented — HC = dark + explicit token override layer. **Selected.**
- (C) Drop HC from Termin's enum — would break BRD §6.2 contract.

---

## Q5. Bundle composition

**Decision: Single all-in-one bundle.** One `bundle.js` containing
React + ReactDOM + Spectrum + Termin renderer glue. Built with no
externals. One `<script>` tag, one cache entry, one version per provider
release.

**Rationale.** The cross-provider browser-cache argument for multi-file
bundling (importmap or externals-as-globals) is theoretical until
multiple production providers exist *and* coordinate on shared dependency
URLs. Per-provider packages with self-hosted-default URLs means each
provider's bundle URL is unique; even if both bundle React 19, the browser
caches them separately. Until that coordination point exists, all-in-one
is simpler operationally — atomic versioning, no cross-file ordering
hazards, one CSP allowlist entry.

When the provider ecosystem actually has multiple production providers,
revisit this and migrate to importmap-based externals. That migration is
a build-config change plus a runtime-served-vendor-files addition; no
source changes required.

**Options considered:**

- (A) Single all-in-one bundle. **Selected.**
- (B) ES modules + importmap.
- (C) Traditional externals as globals.

**Estimated bundle size:** ~150KB gzipped (~400KB minified). Within
typical enterprise-app first-load budgets; subsequent navigations on the
same origin hit the browser cache.

---

## Q-extra. Action API surface — server endpoint vs JS-side dispatch

**Decision: JS-side dispatch (no server endpoint).** `Termin.action(payload)`
in `termin.js` translates each `kind` into the appropriate existing REST
endpoint (CRUD on `/api/v1/<content>`, transition on `/_transition/...`,
compute on `/api/v1/compute/...`). The runtime exposes no new
`/_termin/action` route.

**Rationale.** A short-lived `/_termin/action` endpoint shipped in 5b.4 B'
plumbing as a typed validation facade with no dispatch logic. Two ways to
satisfy the same goal (one stable JS-side API surface for providers):

| | Server facade | JS-side dispatch (selected) |
|---|---|---|
| New runtime endpoint | yes (`/_termin/action`) | no |
| Existing REST endpoints used | dispatched-to via facade | called directly |
| Kazoo / second-runtime work | implement facade + dispatch table | zero (just BRD §11 surface) |
| URL-convention coupling on provider bundle | none (facade abstracts) | none (`termin.js` abstracts) |

The facade only earns its keep if it batches, multiplexes over WebSocket,
or owns idempotency-key handling that the REST endpoints don't. None of
those exist in v0.9 and none are planned. WebSocket low-latency optimistic
flows are a possible future direction; if that becomes a concrete
requirement, a dedicated WebSocket frame type lands then. Until then,
JS-side dispatch is strictly less infrastructure for the same provider
ergonomics.

**Options considered:**

- (A) Server facade `/_termin/action` — typed validation + dispatch on the
  runtime. Provider-facing JS calls one URL.
- (B) JS-side dispatch in `termin.js` — provider-facing JS still calls one
  function; that function maps `kind` to existing REST endpoints
  client-side. **Selected.**

**Wire shape — `Termin.action(payload)`:**

```js
Termin.action({ kind: "create",     content, payload })            // POST   /api/v1/<content>
Termin.action({ kind: "update",     content, id, payload })        // PUT    /api/v1/<content>/<id>
Termin.action({ kind: "delete",     content, id })                 // DELETE /api/v1/<content>/<id>
Termin.action({ kind: "transition", content, id, machine_name,
                target_state })                                    // POST   /_transition/<content>/<machine>/<id>/<state>
Termin.action({ kind: "compute",    compute_name, input })         // POST   /api/v1/compute/<compute_name>
```

Returns a `{ ok, status, kind, data }` (or `{ ok: false, kind, error }`)
shape regardless of which endpoint was dispatched, so callers don't have
to special-case per-kind response shapes.

---

## Open implementation questions (smaller — autonomous-decidable)

These don't need JL input but are recorded so the implementing Claude
documents the calls:

- **Build tool** — esbuild vs rollup vs vite vs webpack. Recommendation:
  esbuild (fast, minimal config, modern, well-suited to single-bundle
  output). Rollup if tree-shaking nuances appear.
- **Termin.js refactor scope** — how much of the existing 853-line client
  runtime gets replaced vs preserved when B' mode is active. Default
  position: keep the WebSocket multiplexer + subscription manager, replace
  the DOM-patching / hydration code with the Provider's renderer
  delegation.
- **Action API surface** — **RESOLVED 2026-04-29.** `Termin.action(payload)`
  is **client-side dispatch** to the existing REST surface every conforming
  runtime implements per BRD #2 §11. There is no `/_termin/action` server
  endpoint — see "Q-extra" below. The JS provider gets a stable typed seam,
  the runtime gets zero new plumbing, and alternate runtimes (e.g. Kazoo)
  inherit it for free.
- **Component composition for missing Spectrum equivalents** — `markdown`,
  `chat`, `metric`, possibly `nav-bar` need composition from Spectrum
  primitives. Each is a small design call when Spectrum work begins;
  none requires architectural input.
- **Initial-render strategy** — first paint while bundle loads. Options:
  spinner placeholder, server-rendered fallback skeleton, or just blank
  until JS hydrates. Default position: lightweight skeleton with the
  page title and navigation chrome from the bootstrap data.

---

## Implementation plan (post-decisions)

### Phase 5b.4 platform (already shipped — 2026-04-27)

CSR bundle discovery endpoint (`/_termin/presentation/bundles`),
`Termin.registerRenderer` JS API, deploy-config bundle-URL override, all
landed in commit `b241750`.

### Phase 5b.4 B' plumbing (next, autonomous-decidable)

Runtime additions to support B'-mode rendering:

1. **Bootstrap data builder.** Given a path + principal, produce the
   `{component_tree_ir, bound_data, principal_context, subscriptions_to_open}`
   payload. Pure function; consumes existing IR + storage layer + identity.
2. **`GET /_termin/page-data?path=<path>` endpoint.** Returns the bootstrap
   payload as JSON for SPA navigation requests. Auth-gated (same identity
   resolution as page requests). Drives the `Termin.navigate(...)` flow.
3. **HTML shell mode.** Alternative to the current SSR-composited HTML
   response. Emits a minimal shell with the provider's bundle URL +
   embedded bootstrap data. Triggered when the bound presentation
   provider declares B' mode (a new `render_modes` value, `"shell"`, or
   the absence of `"ssr"` from a CSR-only provider).
4. **`Termin.navigate(path)` and `Termin.action(payload)`** in termin.js.
   Navigate fetches `/_termin/page-data` and calls the provider's
   registered shell renderer with the new tree. Action dispatches
   client-side to the existing REST surface (CRUD / transition /
   compute) per Q-extra.
5. **Conformance tests** for each piece.

### Phase 5b.4 Spectrum provider (separate repo, next-next)

Once the B' plumbing lands, the `termin-spectrum-provider` repo can be
created and the actual Spectrum renderer authored. This is JL-eyes-on
work — repo creation and Spectrum-specific design should be interactive,
not autonomous.

---

*End of design doc.*
