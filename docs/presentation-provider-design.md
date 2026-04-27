# Presentation Provider Design — v0.9 Phase 5

**Status:** DRAFT, awaiting JL review.
**Author:** Claude Anthropic (session 2026-04-26 night).
**Source-of-truth BRDs:**
- `docs/termin-presentation-provider-brd-v0.9.md` (BRD #2) — primary.
- `docs/termin-source-refinements-brd-v0.9.md` (BRD #3) — Phase 6a is a hard prerequisite for 5a.
- `docs/termin-provider-system-brd-v0.9.md` (BRD #1) — provider scaffolding + Tier classification.

This document is the implementation-level design for **Phase 5 (Presentation provider system)**, mirroring the precedent set by `docs/compute-provider-design.md`. BRD #2 §12 specifies three sub-phases (5a, 5b, 5c); this doc resolves the open implementation questions inside each sub-phase, defines the contract surface, and lays out the commit slices.

**Sequence agreed with JL (2026-04-26 night):**

> design doc → **Phase 6a** (BRD #3 §§3–4: Principal type, ownership, the user, `preferences` map) → **Step Zero** (markdown sanitizer) → **Phase 5a** → **Phase 5b** → **Phase 5c** → **Phase 6b** → **Phase 6c** → **Phase 6d**.

Phase 6a comes first because BRD #3 §4 supersedes BRD #2 §6.2's location of theme preference; landing 5a's theme plumbing without 6a's `preferences.theme` would create transitional code we'd have to retire (the v0.9 Phase 3 `.legacy` accessor pattern is the cautionary tale, now on the technical-debt ledger).

The Phase 4 (Channels) agent is working in a different repository; we'll rebase the two trees onto each other when ready. This design assumes Phase 4 has not landed and uses no channel-related Presentation features (which is correct; Phase 5 is structurally independent of Phase 4 per the BRDs).

---

## 1. Goal

Bring the Termin runtime's presentation layer under the same provider-contract pattern that landed for Identity (Phase 1), Storage (Phases 2 and 2.x), and Compute (Phase 3). Specifically:

- A closed contract surface (`presentation-base`, ten contracts) with one or more first-party providers.
- A grammar extensibility mechanism (`Using "<ns>.<contract>"`) for non-default namespaces.
- Two-pass compilation that lets contract packages contribute new source verbs.
- Per-principal theme preference plumbed through the runtime to every render.
- Field-level redaction enforced before data reaches the provider.
- Compile-time and deploy-time validation that every required contract resolves to a binding.

The end state: any conforming runtime can swap `tailwind-default` for `carbon` or `gov-uk-design-system` (or any third-party provider) by editing deploy config, with no source changes. Apps that need wholly-new component types ship a contract package + provider and reference them via `Using`.

**Non-goals for v0.9:**

- Plugin loading sandboxing (BRD #2 §7.5 — implementation choice deferred).
- Versioned namespace `Using` references (BRD #2 Appendix B item 1 — operator-pinned at deploy).
- The `chart` contract (BRD #2 §5.1 — defer until forcing function arrives).
- Multi-level `extends` chains (BRD #2 Appendix B item 8 — locked at one level).
- Verb aliasing for collisions (BRD #2 §4.5 — hard stop in v0.9).

---

## 2. Current state

What exists in the runtime today (`feature/v0.9-presentation` branch, 2026-04-26 night):

### Existing presentation pipeline (`termin_runtime/presentation.py`, 1061 lines)

- A dispatch-table renderer over the Component Tree IR (Presentation v2 per `docs/termin-presentation-ir-spec-v2.md`).
- Per-component renderer functions (`_render_text`, `_render_data_table`, `_render_form`, `_render_chat`, `_render_aggregation`, `_render_action_button`, etc.) that emit Jinja2 template fragments inline.
- All visual treatment is Tailwind CSS, hard-coded. Class strings are baked into the renderers.
- No notion of a "provider" — the dispatch table IS the renderer. There is no separation between "contract" and "implementation."

This is what Phase 5a turns into the `tailwind-default` provider behind the new contract surface.

### Component tree IR (`termin/ir.py`, `termin/lower.py`)

- `ComponentNode` — `type: str`, `props: dict`, `style: dict`, `layout: dict`, `children: tuple`.
- `PageEntry` — top-level container with `children: tuple[ComponentNode, ...]`.
- `PropValue(value, is_expr)` — wraps CEL-expression props vs literal props.
- Component types currently rendered: `text`, `markdown_view`, `data_table`, `form`, `field_input`, `section`, `aggregation`, `stat_breakdown`, `chart`, `filter`, `search`, `highlight`, `subscribe`, `related`, `action_button`, `chat`, `edit_modal`, `semantic_mark`.

The current set is broader than BRD #2's ten — several are modifiers per BRD §4.2 (`filter`, `search`, `highlight`, `subscribe` are modifiers on `data-table`; `field_input` is internal to `form`; `aggregation` and `stat_breakdown` collapse into `metric`; `chart` is deferred). Lowering needs to fold these into the closed ten plus modifier sets; see §3.10 below.

### Client embodiment (`termin_runtime/static/termin.js`, 853 lines)

- ES module. WebSocket multiplexing. Subscription registration. Form-submit AJAX. Flash notifications (toast/banner). General streaming hydrator (`compute.stream.<inv_id>.field.<name>` → `[data-termin-row-id=X] [data-termin-field=Y]`).
- No notion of pluggable client-side renderers. CSR is not a current pattern — everything is SSR with Jinja2 + client-side hydration.
- No `Termin.registerRenderer(...)` API. Phase 5b adds it.

### Provider scaffolding (already in place from Phases 0–3)

- `termin_runtime/providers/contracts.py` — `ContractRegistry` keyed by `(Category, contract_name, product)`.
- `termin_runtime/providers/registry.py` — `ProviderRegistry` keyed by `(Category, contract_name, product)` with factory invocation at app startup.
- `termin_runtime/providers/builtins/__init__.py` — `register_builtins(registry, contracts)` registers identity-stub, storage-sqlite, and the five compute products.
- Contract Protocols already exist for Identity, Storage, and the three Compute contracts.
- Deploy-config shape is `{version, bindings: {identity, storage, presentation, compute, channels}, runtime}` with per-binding `provider` and `config`. The `presentation` slot exists but is currently unbound — Phase 5 fills it.

### What's missing

- No `presentation_contract.py` Protocol surface.
- No registered presentation providers.
- No `required_contracts` IR field.
- No `Using` grammar.
- No two-pass compilation.
- No markdown sanitizer (Step Zero builds it).
- No theme-preference storage table.
- No field-level redaction sentinel — confidentiality currently filters at the storage/CRUD layer (rows hidden) but not at the field level (no per-field redaction marker reaches the renderer).

---

## 3. Design decisions

Numbered for cross-reference. Each one is a discrete implementation-level decision; rationale and alternatives where relevant. JL-resolved questions are tagged at the bottom of the doc (§7).

### 3.1 One Protocol, multiple contracts (not one-Protocol-per-contract)

Compute Phase 3 used **three Protocols** because the three contracts (`default-CEL`, `llm`, `ai-agent`) have genuinely different method signatures (`evaluate(ctx)` vs `complete(prompt) → CompletionResult` vs `invoke(context) → AgentResult` + streaming). Presentation does not have that variance. All ten `presentation-base` contracts share the same conceptual shape: given a component-IR fragment + bound data + principal context, produce rendered output.

Therefore: **one Protocol** for Presentation providers. The Protocol exposes:

```python
@runtime_checkable
class PresentationProvider(Protocol):
    declared_contracts: tuple[str, ...]      # fully-qualified contract names
    render_modes: tuple[RenderMode, ...]      # ("ssr",) | ("csr",) | ("ssr", "csr")

    def render_ssr(
        self,
        contract: str,
        ir_fragment: ComponentNode,
        data: PresentationData,
        principal_context: PrincipalContext,
    ) -> str:
        """Return rendered HTML string. Called only if 'ssr' in render_modes."""

    def csr_bundle_url(self) -> Optional[str]:
        """URL of the JS bundle termin.js loads. Called only if 'csr' in render_modes."""
```

The discriminator on `contract` lets a single provider serve multiple contracts; `declared_contracts` advertises which.

This matches BRD #2 §7.4 (one contract supports both render modes; provider picks).

### 3.2 First-party providers — Tailwind-default in 5a, others in 5b

BRD #2 §9.4 mandates three first-party providers. Per JL's direction:

- **5a:** `tailwind-default` only. SSR mode. (CSR can wait — current pipeline is SSR-only and converting Tailwind to CSR is a separate engineering effort.)
- **5b:** Add `carbon` (CSR primary) and `gov-uk-design-system` (SSR primary) once `Using` override mechanism is in place.

The first-party `tailwind-default` provider extracts the existing `termin_runtime/presentation.py` renderer functions into the new provider class — minimal rewrite, just hook them onto the new dispatch surface (§4.2 below).

A `presentation-stub` is **not** introduced. BRD #2 §9.1 explicitly says `tailwind-default` subsumes the stub role (matches BRD #1 §10's stub-required policy). Phase 5a registers `tailwind-default` as the implicit default — bound to `presentation-base` whenever the deploy config doesn't bind anything else.

### 3.3 Theme preference storage — runtime-managed table, not Storage-bound

BRD #2 §6.2 specifies "runtime-managed per-principal preference store, not visible to applications via the Storage primitive's normal surface." This rules out exposing it as a Content type.

Implementation: a private sqlite table `_termin_principal_preferences` with shape `(principal_id TEXT, key TEXT, value TEXT, PRIMARY KEY (principal_id, key))`. Theme-preference operations go through helper functions in `termin_runtime/preferences.py`, not through the StorageProvider Protocol. Mirrors the `_termin_idempotency` and `_termin_schema` tables from Phase 2.x.

Phase 6a defines `Principal.preferences: map<text, value>` (BRD #3 §4.2). The runtime hydrates `preferences.theme` from this private table on every request and exposes it through the Principal record. The runtime also exposes `set_theme_preference(value)` and `get_theme_preference()` operations callable from termin.js (§3.4).

**This is the Phase 5a / Phase 6a coordination point.** Phase 6a adds `preferences` to the Principal type and the IR. Phase 5a adds the storage table + getter/setter operations + theme-context plumbing. Both pieces meet at: Principal record carries `preferences.theme`, sourced from the runtime-managed table, passed to every render.

### 3.4 termin.js theme operations

Two new endpoints invoked via fetch from termin.js:

- `POST /_termin/preferences/theme` — body `{ value: "light"|"dark"|"auto"|"high-contrast" }`. Authenticated principal sets their own. Anonymous sessions get a session-cookie-scoped store. **Not audit-logged** per BRD §6.2.
- `GET /_termin/preferences/theme` — returns effective value (after `theme_locked` resolution).

Underscore prefix on the path (`/_termin/...`) marks it as runtime-private (parallel to `_transition`). Not a Content auto-CRUD route.

### 3.5 Field-level redaction sentinel — dedicated `Redacted` class

BRD #2 §7.6 says "Claude Code implementation choice" between three shapes: typed-null sidecar, wrapper object `{__redacted: true}`, dedicated class.

**Decision: dedicated `Redacted` sentinel class.**

```python
@dataclass(frozen=True)
class Redacted:
    field_name: str            # which field this stood in for
    expected_type: str         # "text" | "number" | "boolean" | "currency" | ...
    reason: Optional[str] = None  # optional human-readable cause; provider-displayable
```

Type-safe, distinguishable from any natural value (no real cell holds a `Redacted` instance), serializes to JSON with a custom encoder as `{"__redacted": true, "field": ..., "expected_type": ..., "reason": ...}` so termin.js / CSR providers see the same wire shape as SSR providers. Lands in slice 5a alongside the contract Protocol — confidentiality system already has all the metadata; this is just the marker.

**Why not typed-null sidecar:** loses field/type metadata; provider has to reach back into a parallel channel.

**Why not wrapper object:** structurally indistinguishable from a record that happens to have `__redacted` as a field name. Dedicated class enforces the type discrimination.

### 3.6 `required_contracts` — top-level IR field, sorted strings

BRD #2 §8.5 specifies the manifest. Implementation:

- New field on `AppSpec`: `required_contracts: tuple[str, ...]`, alphabetically sorted, deduplicated.
- Populated at lowering time. The lowerer walks every `ComponentNode` in every `PageEntry`, maps the component type to its implicit `presentation-base.<contract>` (per BRD §4.2 mapping table), then unions in any explicit `Using` references.
- IR JSON schema gets a top-level `required_contracts` array.
- Future-extensibility hook (BRD §8.5): treat bare strings as `{contract: <string>}` shorthand if/when richer per-site metadata is needed.

Service-shaped apps (no presentation verbs) emit `required_contracts: []`. Deploy-time validation skips presentation binding lookup entirely in that case (BRD §4.4).

### 3.7 Two-pass compilation — invoked only when `Using` references exist

BRD §4.5 mandates two-pass compilation for grammar extensibility. Implementation strategy:

**Pass 1 — `Using` discovery.** Regex scan over the source file: `Using\s+"([^.]+)\.([^"]+)"`. Collect into a set. Tolerant — does not parse anything else; indented sub-blocks whose verbs may be from not-yet-loaded packages are not interrogated.

**Pass 2 — full PEG parse.** If pass 1 found references to namespaces other than `presentation-base`, load each contract package, extend the parser's verb table, then run the full TatSu PEG parse against the extended grammar. If pass 1 found nothing outside `presentation-base`, skip directly to the existing single-pass PEG parse. Apps without contract packages incur zero overhead.

**Slice landing:** Two-pass compilation lands in **slice 5b**, not 5c. Reason: 5b's override-mode `Using` (BRD §3 "drop-in replacement middle") allows source like `Using "acme-ui.premium-table"`. Even though `acme-ui` is a third-party namespace and the actual `acme-ui` package only becomes loadable once 5c's package format ships, the *grammar* must accept the `Using` clause in 5b so that compile-time validation can reject unknown namespaces with a specific error rather than a parse failure. The two-pass machinery in 5b loads only `presentation-base` (built-in); 5c extends pass 2 to load arbitrary contract packages.

Slice 5a stays single-pass — no `Using` grammar at all in 5a.

### 3.8 Markdown sanitizer (Step Zero, between 6a and 5a)

Standalone module `termin_runtime/markdown_sanitizer.py`. Pure utility, no provider seam. Lands as its own commit before any 5a code. ~100 lines + tests.

Coverage envelope per BRD §7.3 / §5.1.3 with JL's morning corrections (Q3, Q4):

- **Allowed:** bold (`**` / `__`), italic (`*` / `_`), strike-through (`~~`), links (`[text](url)`), headers (`#` through `######`), horizontal rules (`---`), ordered lists (`1.` `2.` etc.), unordered lists (`-` / `*` / `+`).
- **Stripped/rejected:** raw HTML (escape entities), script tags, embedded media (images), code blocks (`` ``` `` and ` ``), tables, underline (no markdown-standard syntax; deferred per Q4 resolution).
- **URL safety:** links to `http://`, `https://`, `mailto:` only. Reject `javascript:`, `data:`, anything else.

Implementation: don't roll our own markdown parser. Use `markdown-it-py` (mature, widely used, configurable) with a strict allowlist. Configure tokens, sanitize URLs in a renderer rule, return safe HTML.

Used by: the future `tailwind-default` provider's `presentation-base.markdown` rendering. Sanitizer runs in the runtime layer **before** data reaches the provider — matches BRD §7.3 "contract-specified, not provider discretion."

### 3.9 Existing `presentation.py` — extract into Tailwind-default, don't rewrite

The 1061-line `presentation.py` is functionally correct and has 1525+ tests covering its output. Slice 5a's task is to wrap it in the new provider seam, not to rewrite it.

Plan:

1. Create `termin_runtime/providers/builtins/presentation_tailwind_default.py`.
2. Define `class TailwindDefaultProvider(PresentationProvider)` with `declared_contracts = (...) # all ten`, `render_modes = ("ssr",)`.
3. Implement `render_ssr(contract, ir_fragment, data, principal_context)` as a dispatch table that calls into the existing `_render_text`, `_render_data_table`, etc. functions in `presentation.py`.
4. Adapt the function signatures gradually — the existing functions take `node: dict` and return HTML strings; the new contract takes a `ComponentNode` and returns a string. Translation layer is a few lines.
5. The runtime's render path (`termin_runtime/pages.py` or wherever the page assembly currently happens) changes from "call presentation.render_component_tree(...)" to "for each ComponentNode, look up the provider for its contract, call provider.render_ssr(...)."
6. Theme-preference field on PrincipalContext threads through; the existing renderers ignore it for v0.9 5a (Tailwind doesn't change visually based on theme yet — that's the polish pass once `light`/`dark` CSS variables are added).

This keeps the diff small, the test surface stable, and the provider seam shippable without a full presentation rewrite.

### 3.10 Folding existing component types into the closed ten

Current IR has component types that don't 1:1 map to BRD §5.1's ten. Mapping:

| Current IR type | BRD §5.1 contract | Treatment |
|---|---|---|
| `text` | `presentation-base.text` | Direct map. |
| `markdown_view` | `presentation-base.markdown` | Direct map. |
| `data_table` | `presentation-base.data-table` | Direct map. |
| `field_input` | (modifier child of `form`) | Already nested under `form` in the IR. |
| `form` | `presentation-base.form` | Direct map. |
| `chat` | `presentation-base.chat` | Direct map. |
| `aggregation` | `presentation-base.metric` (field-grouped) | Map. Fold `groupby` into the field-grouped form. |
| `stat_breakdown` | `presentation-base.metric` (explicit-listed) | Map. Cap-3 enforced at parse time per BRD §5.1. |
| `chart` | (deferred) | **Remove.** No example currently uses it; if any do, error at lowering time. |
| `filter` / `search` / `highlight` / `subscribe` / `semantic_mark` | (modifier child of `data-table`) | Already nested. |
| `section` | (no presentation contract) | Internal layout container, lower into an HTML wrapper inside `page`. Not a separate contract. |
| `related` | (no presentation contract) | Internal layout for child-table rendering. Not a separate contract. |
| `action_button` | (modifier child of `data-table`) | Per-row action; lives under `data-table.For each ... show actions` modifier. |
| `edit_modal` | (modifier child of `data-table`) | Per-row edit affordance. |
| `nav_bar` (if present) | `presentation-base.nav-bar` | Direct map. |

`page` is implicit — `PageEntry` already serves as the container. Pages map to `presentation-base.page`.

The lowerer's job in 5a: tag every `ComponentNode` with its `contract: str` field at lowering time. The provider dispatch then keys off `contract` rather than off `type`. (This is a small additive change to `ComponentNode` and all the lowerers that emit it.)

### 3.11 PrincipalContext shape

Passed to every `render_ssr(...)` invocation. Shape:

```python
@dataclass(frozen=True)
class PrincipalContext:
    principal_id: str
    principal_type: str           # "user" | "system" | "anonymous"
    role_set: frozenset[str]
    scope_set: frozenset[str]
    theme_preference: str          # resolved via get_theme_preference (theme_locked already applied)
    claims: Mapping[str, Any]      # opaque identity claims (depends on Identity provider)
    preferences: Mapping[str, str] # full preferences map (theme is one entry)
```

Phase 6a defines the source-side `Principal` type. This `PrincipalContext` is the runtime-side projection passed to providers. `theme_preference` is denormalized from `preferences["theme"]` for ergonomic access (it's the field providers use most).

### 3.12 Conformance Tier

Per BRD §8.4: Presentation is **Tier 2** across the board. Tier 2 = visual surface, bounded blast radius, operational impact = "users see degraded UI." Conformance tests live in the conformance repo's `tests/test_v09_presentation_*.py` files; ship as a separate commit after slices land.

### 3.13 Deploy-config shape

Per BRD §11.2:

```yaml
presentation:
  bindings:
    "presentation-base":
      provider: "tailwind-default"
      config: {}
    # Optional: per-contract or per-package overrides
    "presentation-base.data-table":
      provider: "deluxe-table-renderer"
      config: {}
    "airlock-components":
      provider: "airlock-renderer"
      config: { effects_quality: "high" }
  defaults:
    theme_default: "auto"
    theme_locked: null    # or "dark" to lock
```

Resolution rules per BRD §11.3 (sub-contract wins over namespace; fail closed if no binding resolves). The `deploy_config.py` parser already accepts `bindings.presentation` from Phase 1; slice 5a just adds the resolver.

---

## 4. Layer-by-layer plan

### 4.1 New module: `termin_runtime/providers/presentation_contract.py`

Defines:
- `PresentationProvider` Protocol (§3.1).
- `PresentationData` — typed dict of bound row/field data passed at render time.
- `PrincipalContext` (§3.11).
- `Redacted` sentinel class (§3.5).
- `RenderMode = Literal["ssr", "csr"]`.
- Contract registry helper: `register_presentation_base_contracts(contracts: ContractRegistry)` — registers the ten `presentation-base.*` contract names.
- Custom JSON encoder for `Redacted` so termin.js sees `{"__redacted": true, ...}`.

### 4.2 New module: `termin_runtime/providers/builtins/presentation_tailwind_default.py`

The `TailwindDefaultProvider` class (§3.9). Wraps `termin_runtime/presentation.py` renderer functions. Registers as `(Category.PRESENTATION, "presentation-base.*", "tailwind-default")` for all ten contracts in one factory.

### 4.3 Theme preference storage and operations

- `termin_runtime/preferences.py` — `_termin_principal_preferences` table creation, `set_theme_preference(principal_id, value)`, `get_theme_preference(principal_id) → str` (with `theme_locked` resolution).
- `termin_runtime/routes.py` — register `POST /_termin/preferences/theme` and `GET /_termin/preferences/theme` (or wherever runtime-private routes live).

### 4.4 Markdown sanitizer (Step Zero, lands before 5a)

- `termin_runtime/markdown_sanitizer.py` — `sanitize_markdown(text: str) -> str` using `markdown-it-py` with the BRD §7.3 allowlist.
- `tests/test_markdown_sanitizer.py` — comprehensive coverage: allowed markdown stays, disallowed gets stripped, URL allowlist, edge cases (mixed safe + unsafe, malformed input).

### 4.5 Runtime cut-over: register and dispatch

- `termin_runtime/providers/builtins/__init__.py` — extend `register_builtins` to register `tailwind-default` for all ten `presentation-base` contracts.
- `termin_runtime/context.py` — add `presentation_providers: Mapping[str, PresentationProvider]` keyed by contract name.
- `termin_runtime/app.py` — at startup, walk `ir.required_contracts`, resolve each via deploy config (defaulting to `tailwind-default`), populate `ctx.presentation_providers`.
- `termin_runtime/pages.py` (or equivalent) — replace the existing `presentation.render_component_tree(...)` call with a tree walk that, per `ComponentNode`, looks up `ctx.presentation_providers[node.contract]` and calls `provider.render_ssr(...)`.

### 4.6 IR / lowering changes

- `termin/ir.py` — add `AppSpec.required_contracts: tuple[str, ...]`. Add `ComponentNode.contract: str`.
- `termin/lower.py` — populate `ComponentNode.contract` from the type-to-contract map in §3.10. Aggregate `required_contracts` across all components. Sort + dedup.
- `docs/termin-ir-schema.json` — add `required_contracts` and `contract` to the schema.

### 4.7 Field-level redaction wiring

- `termin_runtime/confidentiality.py` (existing) — when redacting a field, replace value with `Redacted(field_name=..., expected_type=...)` instead of `None`/empty.
- Renderer functions in `presentation.py` (now `tailwind-default`) — detect `Redacted` and render as `<span class="text-gray-400 italic" data-termin-redacted>—</span>` or similar. Provider's choice — Tailwind-default just needs *a* visible treatment.
- JSON encoder in `presentation_contract.py` so the wire shape is consistent for any future CSR providers.

### 4.8 Conformance check: colorblind-safety (5a exit criterion)

- `tests/test_presentation_colorblind.py` — render a fixture with severity-colored elements (toast severity, banner severity, table-row highlights), apply deuteranopia / protanopia / tritanopia color-blindness filters via `colour-science` library, verify minimum luminance contrast (WCAG AA) between adjacent severity levels.
- The test is allowed to **fail initially** if Tailwind-default's palette needs adjustment — that's the whole point per BRD §6.4. Fix Tailwind classes to pass the test in the same slice.

### 4.9 Conformance test pack (separate commit after slices)

`tests/test_v09_presentation_provider.py` in compiler repo (~30 tests) plus equivalent in conformance repo (~40 tests). Coverage: contract surface, theme preference plumbing, redaction, deploy-time validation, fail-closed behavior, multi-provider dispatch (5b/5c).

---

## 5. Test plan

### 5.1 New tests (compiler repo)

Per slice. Numbers approximate; final count tracks actual at commit time.

- `tests/test_markdown_sanitizer.py` — ~25 tests, Step Zero.
- `tests/test_v09_presentation_contract.py` — ~20 tests, slice 5a. Protocol shape, Redacted sentinel, PrincipalContext, JSON encoding.
- `tests/test_v09_presentation_tailwind.py` — ~25 tests, slice 5a. Tailwind-default provider conformance for each of ten contracts.
- `tests/test_v09_theme_preference.py` — ~12 tests, slice 5a. set/get, theme_locked, anonymous session, audit-not-logged.
- `tests/test_v09_required_contracts.py` — ~10 tests, slice 5a. IR field, sort/dedup, service-shape empty case.
- `tests/test_v09_field_redaction.py` — ~12 tests, slice 5a. Sentinel placement, type discrimination, JSON wire shape.
- `tests/test_presentation_colorblind.py` — ~6 tests, slice 5a. Severity contrast on three CVD types.
- `tests/test_v09_using_grammar.py` — ~15 tests, slice 5b. Grammar parses, override-mode validation, two-pass compilation.
- `tests/test_v09_carbon.py` — ~25 tests, slice 5b. Carbon provider conformance.
- `tests/test_v09_govuk.py` — ~25 tests, slice 5b. GOV.UK provider conformance.
- `tests/test_v09_contract_packages.py` — ~20 tests, slice 5c. Package format, grammar extension, verb collision.
- `tests/test_v09_airlock_renderer.py` — ~15 tests, slice 5c. Airlock package as proving ground.

### 5.2 Migrated tests

- `tests/test_e2e.py`, `test_helpdesk.py`, `test_projectboard.py`, `test_compiler_fidelity.py`, `test_runtime.py` — existing tests that exercise rendering. Should pass unchanged after slice 5a (Tailwind-default delegates to the same renderer functions). If any fail, that's a real regression in the cut-over.

### 5.3 Conformance tests (conformance repo)

Separate commit after slices land. ~40 tests across: provider conformance (each first-party provider passes the full `presentation-base` battery), required_contracts manifest validation, deploy-time fail-closed, theme preference round-trip, redaction sentinel handling.

### 5.4 Per-fix verification

When something breaks during slice work, follow the v0.9 pattern: trace the assumption, fix the root cause, add a comment pointing at the BRD section that drove the change.

---

## 6. Implementation slice / commit strategy

Each slice is independently committable, tests stay green at every commit boundary, and the runtime is strictly more capable after each.

### Step Zero — markdown sanitizer

`termin_runtime/markdown_sanitizer.py` + tests. ~100 lines + ~25 tests. Self-contained, no provider seam yet. Commit message: `feat(v0.9 step-zero): markdown sanitizer for presentation-base.markdown contract`.

### Slice 5a — presentation contract surface + Tailwind-default + theme preference + colorblind-safety

The big one. Sub-divides naturally:

**5a.1** — Contract Protocol + `Redacted` sentinel + `PrincipalContext` + JSON encoder + `required_contracts` IR field + `ComponentNode.contract` tagging + lowering map. No runtime cut-over yet; existing `presentation.py` still drives rendering.

**5a.2** — `TailwindDefaultProvider` extracted from `presentation.py`. Registers in `register_builtins`. Runtime cut-over: `ctx.presentation_providers` populated at startup; render path goes through provider lookup.

**5a.3** — Theme preference table + endpoints + Principal-context plumbing. Coordinates with Phase 6a's `Principal.preferences`. **Landed.** `termin_runtime/preferences.py` module + `_termin_principal_preferences` table; `GET`/`POST /_termin/preferences/theme` endpoints; `_hydrate_principal_preferences` in `identity.py` enriches `Principal.preferences` on each authenticated request (anonymous bypass; theme-locked masking applied). 29 tests under `tests/test_v09_theme_preference.py`.

**5a.4** — Field-level redaction sentinel wiring (confidentiality system → Redacted instances → renderer).

**5a.5** — Colorblind-safety conformance test + Tailwind palette adjustments to pass it.

Each lands as its own commit (or a tight series). Test count grows ~80 over the five sub-slices.

**Exit criteria (per BRD §12.1):** All v0.9 examples run on Tailwind-default. Theme preference round-trips. Deploy fails closed when a required `presentation-base` contract is unbound and no default is configured. Colorblind-safety test passes for Tailwind-default's severity palette.

### Slice 5b — Override mechanism + Carbon + GOV.UK

**5b.1** — `Using "<ns>.<contract>"` grammar: PEG rule, parse handler, AST node carries the override target. Compile-time validation of `<contract>` against the namespace's known contracts (only `presentation-base` known until 5c).

**5b.2** — Two-pass compilation machinery (regex pass 1, full-PEG pass 2). For 5b, pass 1 is effectively a no-op since `presentation-base` is implicit; the machinery is in place for 5c.

**5b.3** — Multi-provider dispatch in `ctx.presentation_providers`: per-site contract resolution, sub-contract bindings winning over namespace.

**5b.4** — Carbon first-party provider (`presentation_carbon.py`). CSR-primary. termin.js gains `Termin.registerRenderer(...)` API for CSR bundles.

**5b.5** — GOV.UK first-party provider (`presentation_govuk.py`). SSR-primary.

**Exit criteria (per BRD §12.2):** App can use Carbon for everything and Tailwind-default for one overridden `data-table`. Third-party narrow provider integrates correctly. Compile-time + deploy-time validation catches mismatches.

### Slice 5c — Contract packages + grammar extensibility + Airlock

**5c.1** — Contract package format finalization (per BRD §10.2). YAML or Termin-flavored DSL. Loader. Pass 1 / Pass 2 wired to actually load packages.

**5c.2** — Grammar table extension at parse time. Verb collision detection.

**5c.3** — Runtime: contract-package providers loaded at deploy. Multi-provider rendering dispatches IR fragments to correct provider by namespace.

**5c.4** — Airlock contract package — `cosmic-orb`, `airlock-terminal`, `scenario-narrative` contract definitions. (No actual visual rendering for the cosmic-orb yet — that needs UX/design work outside this scope. Ship the contract definitions and a placeholder Airlock provider that renders each as a labeled `<div data-airlock-component="...">`. JL or another agent picks up real visual rendering separately.)

**5c.5** — Conformance for grammar extension + multi-provider rendering.

**Exit criteria (per BRD §12.3):** Airlock app compiles and runs end-to-end (with placeholder rendering). Reviewer can grep `Using "airlock-components.cosmic-orb"` to find every use site. Verb collision is a hard compile error with both packages named.

### Slice 5d (implicit) — Phase 6 follow-up: 6b, 6c, 6d

After 5c lands, work continues on Phase 6 per BRD #3 §7:

- **6b** — State-machine transition events (`<content>.<field>.<state>.entered/exited`).
- **6c** — Agent directive sourcing (`Directive from deploy config "..."`, `Directive from <content>.<field>`).
- **6d** — Hardening + migration (existing examples to `principal` business type, doc cross-links).

These don't directly touch Presentation but were part of the agreed sequence.

---

## 6.1 Parallelism analysis

Within this branch, slices are sequential — each depends on the previous. Across branches, this work is isolated from Phase 4 (channels) — the only shared surface is `bindings.compute` vs `bindings.channels` vs `bindings.presentation` in deploy config, and the binding resolvers are independent.

**Phase 6a sequencing:** 6a needs to land *before* slice 5a.3 (theme-preference plumbing), since 5a.3 depends on `Principal.preferences`. Concretely: 5a.1 + 5a.2 (contract surface, Tailwind extracted) can land before 6a; 5a.3 onward needs 6a complete. Reasonable to do 6a first per JL's direction, then all of 5a uninterrupted.

---

## 7. Resolved decisions (2026-04-27 morning)

JL reviewed and resolved all eight questions in the morning session. All resolutions captured here for the implementing Claude (or future-me).

- **Q1 — RESOLVED.** Field-level redaction sentinel = `Redacted` dataclass per §3.5.
- **Q2 — RESOLVED.** Markdown library = `markdown-it-py`. JL deferred to recommendation. Adds to `setup.py` extras during Step Zero.
- **Q3 — RESOLVED.** Lists in markdown envelope: **both ordered and unordered are IN.** JL flagged the BRD's omission as an oversight. Sanitizer allows `1.` `2.` ordered and `-` / `*` / `+` unordered.
- **Q4 — RESOLVED.** Underline in markdown envelope: **OUT.** JL flagged the BRD's inclusion as an accidental conclusion (no markdown-standard syntax for underline). Sanitizer strips any underline construct.
- **Q5 — RESOLVED.** `chart` component: option (b) — **migrate `warehouse.termin:100`** to a non-chart shape (likely `Display count of reorder alerts grouped by date` or removed entirely). Honors BRD §5.1 deferral. Lowering should reject `ShowChart` AST emission with a TERMIN-S-class error in slice 5a; the warehouse migration lands alongside.
- **Q6 — RESOLVED.** Airlock 5c.4 ships placeholder rendering. Real cosmic-orb visuals deferred as a separate UX/design task; not blocking 5c exit.
- **Q7 — RESOLVED.** Tailwind-default CSR is **deferred**. Slice 5a ships SSR-only for Tailwind. Carbon (5b) brings CSR machinery. Tailwind CSR is added to the technical debt list (`docs/termin-roadmap.md` Technical Debt section).
- **Q8 — RESOLVED.** Conformance pack lands as one commit after 5c, matching the Phase 3 pattern.

---

## 8. What's deliberately out of scope

For Phase 5 entirely:

- **`chart` contract** — deferred per BRD §5.1.
- **Plugin sandboxing** — deferred per BRD §7.5.
- **Versioned `Using` references** — operator-pinned at deploy per BRD Appendix B.1.
- **Multi-level `extends` chains** — locked at one level per BRD Appendix B.8.
- **Verb aliasing for collisions** — hard stop in v0.9 per BRD §4.5.
- **CSR mode for Tailwind-default** — not strictly required (BRD §9.1 says "ships both" but JL's Q7 path defers).
- **Real Airlock visual rendering** — placeholder only in 5c; UX/design work separate.
- **Migration of existing `chart` IR uses** — assumed to be zero; if not, opens a sub-task.

For Phase 6:

- **Composite ownership** (transitive ownership through references) — deferred to v0.10 per BRD #3 Appendix B.
- **Sub-language escape** for triple-backtick interpolation — deferred to v1.0 per BRD #3 Appendix B.
- **Declarative-trigger form for state-machine events** (BRD #3 §5.6) — deferred.
- **Composition of agent directives** (BRD #3 §6.5) — deferred.

---

## 9. Migration notes

### v0.8 → v0.9 deploy configs

Existing v0.8 deploy configs have no `presentation:` section (presentation is hard-coded to the inline Tailwind renderer). The v0.9 deploy template generator (Phase 1 step 4 already added bindings nesting) will need to start emitting:

```yaml
presentation:
  bindings:
    "presentation-base":
      provider: "tailwind-default"
      config: {}
  defaults:
    theme_default: "auto"
```

Apps redeploying without this section default to `tailwind-default` automatically (the resolver falls back to it as the implicit default). No breaking change.

### Existing examples

All 14 examples in `examples/` should compile and run unchanged. None use `Using` (5b grammar). None use contract packages (5c). The `chart` IR component type, if any examples emit it, needs to be migrated to `metric` or removed (per Q5).

---

## 10. References

- BRD #2: `docs/termin-presentation-provider-brd-v0.9.md`
- BRD #3: `docs/termin-source-refinements-brd-v0.9.md`
- BRD #1: `docs/termin-provider-system-brd-v0.9.md`
- Phase 3 design doc (precedent shape): `docs/compute-provider-design.md`
- Component tree IR: `docs/termin-presentation-ir-spec-v2.md`
- Existing renderer: `termin_runtime/presentation.py`
- Existing client runtime: `termin_runtime/static/termin.js`

---

**End of design doc. Awaiting JL review of the eight open questions in §7. Step Zero (markdown sanitizer) and Phase 6a do not depend on any Q resolution and can proceed immediately upon design-doc approval.**
