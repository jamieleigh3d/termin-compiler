# Termin Presentation Provider System — Business Requirements Document

**Version:** 0.9.0-draft (BRD #2; synthesized 2026-04-25)
**Status:** Formative — companion to `termin-provider-system-brd-v0.9.md`. Together they specify the v0.9 provider system end-to-end.
**Audience:** Claude Code instances implementing the Presentation provider subsystem; reviewers of the architecture; future authors of Presentation providers and component packages.

**Relationship to BRD #1:** BRD #1 specified Identity, Storage, Compute, and Channels. It deferred Presentation due to scope. BRD #2 covers Presentation in full and **supersedes BRD #1 §3** (the "Three Customization Levels (Preview)" section). The level-based framing is replaced by a single contract-binding mechanism applied along an axis of declared customization.

**Revision log (2026-04-26, clarifications round 2):**
- §4.5 extended: verb-collision rule. If pass 2 finds two included packages declaring the same source-verb, it is a compile error naming both packages and the colliding verb; v0.10+ may introduce aliasing or namespace-qualified verb references as a resolution path.
- §5.1 `metric` repartitioned: the contract now displays one primary number plus zero-to-many subdivision numbers. **Explicit-listed** form (`with V1 vs V2 vs V3 breakdown`) capped at 3 by the parser; **field-grouped** form (`Display count of <X> grouped by <field>`) uncapped at the source level, with a recommended provider-side display limit of 10 and provider's-choice overflow handling. Both forms remain text-and-numbers — neither becomes a chart. §4.2 grammar table updated to enumerate both shapes.
- §6.2 extended: theme-preference runtime operations specified — `set_theme_preference(value)` and `get_theme_preference()`. Any authenticated principal can set their own preference; no scope, no audit. Storage is runtime-managed (not visible via the Storage primitive's surface). `theme_locked` resolution happens at read time; writes still succeed under lock so that removal of the lock restores the user's stored preference.
- §8.5 added: `required_contracts` manifest format. List of fully-qualified `<namespace>.<contract>` strings, deduplicated, alphabetically sorted, top-level field of the application IR. Future-extensible to richer per-site metadata without breaking v0.9 consumers.
- Appendix B item 9 resolved: the field-grouped form is preserved; the cap is reformulated to apply only to explicit-listed breakdowns. Item updated from open-question to resolved status.

**Revision log (2026-04-25, clarifications round 1):**
- §4.3 reframed: `Using` has two modes (override / new-verb), classified by the verb at the use site. The `extends` requirement is scoped to override mode; new-verb mode has no implicit base to extend against.
- §4.5 added: explicit two-pass compilation. Pass 1 scans for `Using` references with a tolerant scanner; pass 2 loads contract packages, extends the verb table, and runs the full parse. Unloadable namespaces fail closed at pass 2.
- §5.1 `metric` locked and expanded with explicit source-form examples for 0, 1, 2, and 3 breakdowns plus matching data-shape sketches (running example: `tickets` × `priority`). §4.2 grammar row enumerates the three breakdown-clause shapes; cap of 3 is enforced at parse time. The corpus form `Display count of <X> grouped by <field>` is captured as Appendix B item 9 for separate resolution under the cap.
- §6.2 clarified: theme defaults live only under `presentation.defaults`, not inside per-binding `config`. Theme preference is a runtime concept, not per-provider — it must reach every provider rendering, which a per-binding setting can't guarantee.
- §6.4 added: colorblind-safety is a horizontal conformance requirement across all four theme values, not a fifth enum value. First-party providers must pass deuteranopia / protanopia / tritanopia simulation plus luminance-contrast checks on information-carrying palettes. Conformance check added to Phase 5a (§12.1).
- §7.6 added: field-level redaction. Runtime replaces redacted values with a typed sentinel before the record reaches the provider. The field is present in the record (not absent); the marker is type-safe; providers decide rendering.
- §10.2 clarified: modifier validation against the union of base-via-extends-chain modifiers and contract additions. Additions are name-disjoint from base — extensions may add modifiers but may not redefine existing ones. The one-level `extends` chain cap is now locked (Appendix B item 8).
- Appendix A.3 corrected: removed redundant `theme_default` / `theme_locked` from the per-binding `config` blocks. They live under `presentation.defaults` only.
- Appendix B item 8 locked from "Recommendation" to "Locked": one-level `extends` chain for v0.9.

**Revision log (2026-04-25, original synthesis):**
- Customization-level framing dropped in favor of the contract-binding mechanism applied uniformly. There are no "levels"; there is one mechanism (contract binding) along one axis (delegation).
- The `Presentation:` block and `Includes` keyword introduced and then removed before locking. Source has no top-level Presentation block. Namespace dependency is derived by the compiler from `Using "ns.contract"` references.
- `presentation-base` confirmed as implicit, never declared, always available.
- Component vocabulary fixed at ten contracts: `page`, `text`, `data-table`, `form`, `chat`, `metric`, `nav-bar`, `toast`, `banner`, `markdown`. `chart` deferred to a later release.
- Theme preference treated as a semantic principal-context field (`light | dark | auto | high-contrast`), never a source-level construct.
- Three first-party providers ratified: `tailwind-default` (subsumes stub role; ships as the default if nothing is bound), `carbon`, `gov-uk-design-system`. All three implement the full `presentation-base` namespace as a quality bar.
- `Using "<ns>.<contract>"` confirmed as a sub-clause (modifier line) on rendering statements.

---

## 1. Context and Tenets

The v0.9 milestone introduces the **Presentation provider system**: the open extension surface through which Termin runtimes integrate with environment-specific implementations of UI rendering. Every Termin application that has a front-end depends on a Presentation provider; a Termin application can also have no front-end at all (a service-shaped app) and bind no Presentation contracts.

The Presentation provider system is the load-bearing architectural decision behind the goal of a Termin AppFabric whose visual surface can adapt to any organization's design system without source-level change. Source declares semantic intent; providers render. The same `.termin` file renders on Carbon, GOV.UK Design System, the Tailwind-default reference renderer, an Airlock CRT-styled renderer, or any future first-party or third-party provider, with no source modification.

The five tenets continue to govern (priority stack):

1. **Audit over authorship.** The Presentation contracts shrink the visual review surface to a fixed component vocabulary plus declared overrides. Reviewers read source for what's rendered and read deploy config for which provider renders it.
2. **Enforcement over vigilance.** Confidentiality filtering, principal scope enforcement, and action authorization happen in the runtime core, never in the provider. A buggy or malicious Presentation provider can render badly; it cannot leak data it never received or fire actions the source did not declare.
3. **Audience over capability.** The contract vocabulary uses semantic verbs (`Display a table of`, `success shows toast`) that product managers, security reviewers, and compliance officers can read without UI engineering background.
4. **Providers over primitives.** The eight primitives are closed; the Presentation provider surface is open. Termin extends through new design system providers, new component packages, and new render strategies — never through new primitives.
5. **Declared agents over ambient agents.** Where AI agents render or interact with UI, they do so through declared component types and contract-bounded actions, never through unbounded canvas access.

---

## 2. Personas

The seven personas from BRD #1 §2 carry over unchanged. For Presentation specifically:

- **App author** declares UI in user-story bodies (`Show a page called "X"`, `Display a table of`, etc.). Adds `Using "<ns>.<contract>"` modifiers when overriding the default contract for a rendering site. Never names providers.
- **Package author** publishes contract packages declaring new component types in a new namespace. Ships a provider implementing those contracts. Examples: an organization shipping `acme-ui.premium-table`; the Airlock package shipping `airlock-components.cosmic-orb`.
- **Provider author** ships a Presentation provider implementing one or more contracts. May be first-party (Carbon, GOV.UK, Tailwind-default), package-shipped (the Airlock renderer), or third-party (a Polaris provider, a Bootstrap provider).
- **Boundary administrator** binds Presentation contracts to providers at root, organization, or application level via deploy config. Sets default theme preference at boundary level if desired.
- **Runtime operator** runs a Termin runtime with a particular set of Presentation providers installed and registered. Advertises which contracts each registered provider declares conformance for.
- **Reviewer** reads source, derives the namespace dependency set from `Using` references, reads deploy config, audits provider bindings.
- **The runtime itself** validates required contracts against bound provider conformance at deploy, fails closed on gaps, and renders by dispatching component IR fragments to the appropriate provider with bound data and principal context.

---

## 3. The Customization Axis

The Presentation provider system places every Termin application on a single axis:

- **Default end:** App has no opinion about Presentation. Source uses `presentation-base` verbs only. Deploy config either binds nothing (Tailwind-default kicks in) or binds a single provider for the whole namespace. Most apps live here.
- **Drop-in replacement middle:** App needs one or more rendering sites to use a different visual treatment than the rest. Source adds `Using "<ns>.<contract>"` sub-clauses at those sites. Deploy config binds the new contracts. Source still uses base verbs; the override is a per-site contract substitution.
- **New-vocabulary far end:** App needs component types that do not exist in `presentation-base`. Package authors declare new contracts in a new namespace; source uses the new verbs (mandatory `Using`); deploy config binds the namespace. Airlock lives here.

There are no discrete steps, no level numbers, no tiers in this sense (Tier classification continues to refer only to operational impact per BRD #1 §10). An app's position is determined by exactly one thing: which contracts its source references, derived by the compiler from `Using` modifiers and from the verb set used in user-story bodies.

The mechanism is uniform across the axis. There is one mechanism (contract binding); there is one source construct (`Using`); there is one deploy-config shape (namespace and per-contract bindings). The differences across the axis are entirely in *which* contracts get named and bound.

---

## 4. Source Grammar

### 4.1 No top-level Presentation block

Termin source has no `Presentation:` top-level block. The compiler derives the set of Presentation namespaces an app depends on by scanning `Using "<ns>.<contract>"` references in user-story bodies and in toast/banner emissions. `presentation-base` is implicit; any other namespace appears in source only via `Using`.

This is a deliberate departure from earlier proposals. The block was redundant with the per-site `Using` references and added boilerplate to the common case.

### 4.2 Component verbs and modifiers

Every `presentation-base` contract has one canonical source verb. The compiler validates that verb usage matches the contract's declared shape. Each contract declares a closed set of modifiers; modifier usage outside the declared set is a parse error.

The current set of source-level UI clauses (extracted from existing example files, validated for v0.9):

| Source clause | Contract | Notes |
|---|---|---|
| `Show a page called "<title>"` | `presentation-base.page` | Container for other components |
| `Display text "<literal>"` or `Display text \`<expr>\`` | `presentation-base.text` | Plain or expression-bound text |
| `Display markdown from <field-ref>` | `presentation-base.markdown` | Bound to markdown-typed Content |
| `Display a table of <content-name>` | `presentation-base.data-table` | Followed by indented modifiers |
| `Display total <X> count` / `Display total <expr>` | `presentation-base.metric` (explicit-listed) | Optional breakdown clause: `with <V1> breakdown`, `with <V1> vs <V2> breakdown`, or `with <V1> vs <V2> vs <V3> breakdown` (cap 3; §5.1) |
| `Display count of <X> grouped by <field>` | `presentation-base.metric` (field-grouped) | One breakdown entry per distinct value of `<field>`; no source-level cap; recommended provider display limit of 10 with provider's-choice overflow (§5.1) |
| `Show a chat for <content-name>` | `presentation-base.chat` | With `role "<field>", content "<field>"` |
| `Accept input for <field>, <field>, ...` | `presentation-base.form` | Inferred from referenced content type |
| `Navigation bar:` | `presentation-base.nav-bar` | Top-level block, link list with visibility |
| `success shows toast "..."` (state machine) | `presentation-base.toast` | Severity inferred (`success`, `error`) |
| `success shows banner "..."` (state machine) | `presentation-base.banner` | Optional `dismiss after N seconds` |

Modifiers on `data-table` (closed set; not separate components):

| Modifier | Effect |
|---|---|
| `with columns: <field>, <field>, ...` | Column projection |
| `Highlight rows where <predicate>` | Per-row emphasis flag in row metadata |
| `Allow filtering by <field>, ...` | Provider exposes filter UI for these columns |
| `Allow searching by <field>, ...` | Provider exposes search input bound to these fields |
| `Allow inline editing of <field>, ...` | Provider exposes inline edit controls for these fields |
| `For each <X>, show actions: ...` | Per-row actions (state transitions, edits, deletes) |
| `This table subscribes to <X> changes` | Live update declaration |
| `Using "<ns>.<contract>"` | Override the contract for this rendering site |

The `Using` sub-clause is a sub-clause valid on every rendering statement, not just `data-table`. By convention it appears as the first modifier line when present.

### 4.3 The `Using` sub-clause

Syntax:

```
Display a table of tickets with columns: title, priority, status
  Using "acme-ui.premium-table"
  Highlight rows where `priority == "critical"`
  Allow filtering by status
```

Semantics:

- The string after `Using` is a contract reference. Format: `<namespace>.<contract>`.
- For sites without `Using`, the contract is inferred from the verb (e.g., `Display a table of` → `presentation-base.data-table`).
- A `Using` reference operates in one of two modes, classified by the verb at the use site:
  - **Override mode.** The verb has an implicit `presentation-base` contract (`Display a table of` → `presentation-base.data-table`). The `Using` target must declare `extends "<implicit-contract>"` in its contract definition (§10.2). A target that does not extend the implicit contract is a compile error. This is the "drop-in replacement" path of the customization axis (§3).
  - **New-verb mode.** The verb at the use site is only legal because some included package declared it (`Show a cosmic orb of` is legal only when `airlock-components.cosmic-orb` is loaded — see §4.5 and §10.3). The `Using` target must be the contract that declared the verb. No `extends` is required, and there is no implicit base to extend against. This is the "new-vocabulary" path of the customization axis (§3).
- The compiler aggregates all `Using` references across the source file and emits the set of required namespaces as part of the IR.
- `Using` is the only source-level construct that names contracts outside `presentation-base`. There is no other path. Reviewers grep for `Using` to find every override or extension site.

### 4.4 Service-shaped applications

An application with no presentation verbs in source (no `Show a page`, no `Navigation bar`, no toast/banner emissions) declares no Presentation dependencies. The compiler emits an empty Presentation namespace set; deploy config has no `presentation:` section. The application exposes its REST/WebSocket surface (per BRD #1 distributed runtime) and is reachable as a service.

The `headless_service.termin` example file in the v0.9 corpus is canonical for this shape.

### 4.5 Two-pass compilation

The grammar-extension property of contract packages (§10.3) creates a chicken-and-egg problem: the compiler cannot know which verbs are legal until it knows which packages are referenced, and `Using` references appear inside the source it is parsing. Compilation therefore runs in two passes:

- **Pass 1 — `Using` discovery.** Scans the source for the literal `Using "<namespace>.<contract>"` pattern (regex or simple state machine), without attempting to understand which verb each `Using` modifies. Tolerant: indented sub-block lines whose verbs may belong to not-yet-loaded packages are skipped over rather than parsed. Unknown verbs are not errors in pass 1. The output is the set of referenced namespaces.
- **Pass 2 — full parse.** Loads contract packages for every namespace from pass 1. Extends the parser's verb table with each loaded package's declared source-verbs (§10.3). Runs the full PEG parse against the extended grammar. Reports verb errors, modifier errors, `Using`-mode mismatches (§4.3), and modifier-set violations (§10.2).

A `Using` reference to a namespace that pass 2 cannot load — network failure, missing package, version mismatch — is a deploy-blocking error with a specific message naming the namespace and the cause. Recovery is operator action (install the package, fix the deploy environment), not source change. The two-pass split is invisible to source authors; it is purely an implementation property of the compiler.

**Verb collision.** If pass 2 encounters two included packages that declare the same source-verb, this is a compile error. The error must name both colliding packages and the colliding verb. Collision surfaces at compile time, not deploy time, because the parser cannot disambiguate two packages claiming the same verb and cannot proceed past the collision. v0.10 and later may introduce explicit verb aliasing (e.g., a package-level alias declaration, or syntax of the form `Using "<ns>.<contract>" as <local-name>`) or namespace-qualified verb references as a resolution path; for v0.9, verb collision is a hard stop with no resolution other than removing one of the colliding packages from the deploy.

---

## 5. Component Vocabulary: `presentation-base`

The `presentation-base` namespace ships ten contracts. They are independently bindable; a deploy config can bind the full namespace to one provider and selectively override individual contracts to other providers. First-party providers (§9) implement all ten as a quality bar.

### 5.1 The ten contracts

1. **`presentation-base.page`** — top-level layout container; renders title, role-based visibility, and child components arranged in declaration order. Subscribes to nothing directly; child subscriptions propagate.
2. **`presentation-base.text`** — literal or expression-bound text rendering. No formatting.
3. **`presentation-base.markdown`** — markdown-content rendering. Sanitization envelope is part of the contract (§7.3); not provider discretion. Coverage: bold, italic, underline, strike-through, links, headers, horizontal rules. **Not** in v0.9: tables-in-markdown, images-in-markdown, code blocks, raw HTML.
4. **`presentation-base.data-table`** — tabular display of a content type's records, with the modifiers listed in §4.2. Receives column-projected, confidentiality-filtered records and per-row metadata (highlighted, action availability per principal scope).
5. **`presentation-base.form`** — typed input collection bound to a content type or compute input. Receives field metadata (type, required, defaults, confidentiality). Submission is wired to runtime operations, never directly to storage.
6. **`presentation-base.chat`** — message-stream display with role-and-content field bindings. Receives a record stream and the field bindings; emits a message-create action through the runtime.
7. **`presentation-base.metric`** — text-and-numbers dashboard tile. Displays one primary number plus zero-to-many labeled subdivision numbers. No axes, no bars, no scales, no visual encoding of quantities — anything that requires visual encoding belongs to the deferred `chart` contract. Data shape: `{ primary: <number>, breakdown: [{ label: <text>, value: <number> }, ...] }`. Composable from existing design-system primitives when no first-class component exists.

   The contract has two source forms that lower to the same data shape but bound the breakdown count differently. Both forms render text-and-numbers only; neither becomes a chart.

   **Explicit-listed form.** Author lists the categorical values inline. Source: `Display total <X> count` (or `Display total <expr>`), optionally followed by `with <V1> breakdown`, `with <V1> vs <V2> breakdown`, or `with <V1> vs <V2> vs <V3> breakdown`. **Capped at 3 entries by the parser.** Source like `with critical vs high vs medium vs low breakdown` is a parse error.

   Using a helpdesk-style `tickets` content type with a `priority` field (values `critical`, `high`, `medium`, `low`, `info`) and 142 total tickets:

   ```
   # 0 breakdowns — primary number only
   Display total ticket count
   →  { primary: 142, breakdown: [] }

   # 1 breakdown — primary plus one subdivision
   Display total ticket count with critical breakdown
   →  { primary: 142,
        breakdown: [ { label: "critical", value: 12 } ] }

   # 2 breakdowns — primary plus two subdivisions
   Display total ticket count with critical vs high breakdown
   →  { primary: 142,
        breakdown: [ { label: "critical", value: 12 },
                     { label: "high",     value: 28 } ] }

   # 3 breakdowns — primary plus three subdivisions (cap for explicit-listed form)
   Display total ticket count with critical vs high vs medium breakdown
   →  { primary: 142,
        breakdown: [ { label: "critical", value: 12 },
                     { label: "high",     value: 28 },
                     { label: "medium",   value: 47 } ] }
   ```

   The expression form `Display total <expr>` (e.g., `Display total stock value` followed by a backtick CEL expression `` `sum(quantity * unit_cost)` ``) takes the same optional breakdown clause; the labels are values of an inferred or named field on the content type the expression operates over.

   **Field-grouped form.** Author names a field; the runtime produces one breakdown entry per distinct value of that field at query time. Source: `Display count of <X> grouped by <field>` and equivalents (used in `warehouse.termin`, `channel_demo.termin`, `hrportal.termin`, `security_agent.termin`). **No source-level cap** on breakdown count — the cardinality is data-driven.

   Example with an `incidents` content type and 87 total incidents:

   ```
   # Field-grouped — one entry per distinct value of `severity`
   Display count of incidents grouped by severity
   →  { primary: 87,
        breakdown: [ { label: "critical", value: 5  },
                     { label: "high",     value: 14 },
                     { label: "medium",   value: 31 },
                     { label: "low",      value: 28 },
                     { label: "info",     value: 9  } ] }
   ```

   Because the breakdown count is data-driven and unbounded, providers should apply a display-side limit. **Recommended provider behavior:** display the top 10 entries by `value`, descending, with the long tail handled at the provider's discretion — a `+N more` affordance, an "Other" aggregation row (provider-internal, not part of the contract data shape), or a scrollable container. The contract delivers the full unsorted breakdown to the provider; sort, truncation, and overflow rendering are provider concerns.

   **Cap summary.** The smallness assumption is held by the parser only for the explicit-listed form. The field-grouped form is uncapped at the source and contract level; the smallness comes from data shape and provider display limits. Sites whose data would benefit from visual encoding of quantities — bars, lines, areas, scales — belong to the deferred `chart` contract (Appendix B item 2), regardless of breakdown count.
8. **`presentation-base.nav-bar`** — link list with per-link visibility predicates pre-evaluated against the current principal, optional badge values bound to expressions. Theme toggle UI conventionally lives here; provider's choice.
9. **`presentation-base.toast`** — transient notification. Severity (`success`, `error`, `warning`, `info`), optional duration. Auto-dismissed by default.
10. **`presentation-base.banner`** — persistent notification. Severity, optional `dismiss after N seconds`, optional dismissibility.

`chart` is intentionally not in v0.9. Chart rendering varies wildly across design systems and the existing example corpus uses it once. Defer until a forcing function arrives.

### 5.2 Contract anatomy

Every contract in `presentation-base` declares four shapes:

1. **Source-level shape** — the verb that introduces the component and the closed set of modifiers it accepts. Compiler validates source against this.
2. **Data shape** — the typed data the runtime fetches, filters, and passes to the provider at render time. Confidentiality filtering and principal scoping happen *before* data reaches the provider.
3. **Action shape** — the closed set of actions the provider may emit back through termin.js to the runtime. For `data-table`: row selection, action invocation, inline edit submission, search/filter changes. For `form`: submission. For `chat`: message-create. Actions outside the declared set are rejected by termin.js, never reach the runtime.
4. **Principal-context shape** — the per-principal context the provider may use for rendering decisions. Always includes role set, scope membership, and theme preference (§6).

The full shape definitions are specified by Claude Code at implementation time per the rough format in §10.

### 5.3 Modifiers vs components

Modifier-vs-component is settled for v0.9: filtering, searching, inline editing, row highlighting, per-row actions, and subscriptions are modifiers on `data-table`, not separate components. They constrain rendering and event handling but do not introduce a separate render surface. Future contracts (e.g., `tree-table`, `kanban`) may declare different modifier sets.

---

## 6. Theme Preference and Semantic Fields

### 6.1 Tokens are provider-internal

Colors, fonts, spacing, radii, motion durations, and any other design tokens are entirely internal to the provider. They never appear in source, never appear in contract data shapes, never cross the runtime↔provider boundary. Carbon's tokens are Carbon's; GOV.UK's are GOV.UK's; Tailwind-default's are its own.

There is no source-level theme/style mechanism. There is no `style` escape hatch. A future proposal to add either should cite a forcing function and re-open BRD #2.

### 6.2 Theme preference is a principal-context field

> **Supersession (BRD #3 §4.2):** The Principal-record location for theme preference is `Principal.preferences.theme`, not a top-level `Principal.theme_preference` field. The `preferences: map<text, value>` store is extensible and lets future preference keys land without changing the Principal type. The semantics, enumeration, runtime operations, and deploy-config defaults specified below are unchanged; only the storage location on the Principal record is relocated. Phase 6a has shipped the relocation; Phase 5a's runtime operations read and write through `preferences.theme` accordingly.

User-selectable themes are common (light/dark, accessibility modes), and consistency across pages and re-renders requires the runtime to know which preference is in effect. Theme preference is therefore a semantic field on principal context.

**Enumeration:** `light | dark | auto | high-contrast`.

- `light` and `dark` are explicit user selections.
- `auto` follows the operating-system or browser preference; the provider asks the rendering environment at render time.
- `high-contrast` is an accessibility mode. Providers that do not have a distinct high-contrast variant should fall back to their highest-contrast existing mode and document the fallback.

Preference storage is a runtime concern (per-principal, persistent, settable through a runtime operation that termin.js exposes). The toggle UI is a provider-side decision: a provider's `nav-bar` implementation conventionally ships a theme toggle, but it could instead live in a settings page, a system menu, or a keyboard shortcut. None of this surfaces in source.

**Runtime operations.** termin.js exposes two operations for theme preference:

- `set_theme_preference(value)` — `value` is one of `light | dark | auto | high-contrast`. Any authenticated principal can call this to set their own preference; no scope is required (this is a UI preference, not a privileged operation). Anonymous principals get session-scoped storage that clears on session end. Calls always succeed — including under `theme_locked` (see below) — so that the user's stored preference is preserved against future lock removal.
- `get_theme_preference()` — returns the *effective* value after `theme_locked` resolution: if the boundary's effective `theme_locked` is set, the operation returns the locked value regardless of the principal's stored preference. If `theme_locked` is unset, the operation returns the stored preference, falling back to the boundary's effective `theme_default` if the principal has never set one. This is the value the runtime passes through principal-context to every provider rendering for that request.

Both operations are **not audit-logged** — high-frequency, low-stakes; auditing every theme change adds noise without value. Storage is in a runtime-managed per-principal preference store, not visible to applications via the Storage primitive's normal surface — applications cannot read or modify other principals' theme preferences, and the preference store is not a Content type.

Deploy config sets these under `presentation.defaults` (§11.2) at any boundary level — `theme_default: "auto"` for the runtime default, `theme_locked: "dark"` to pin a value for environments where user choice is undesired (e.g., an always-dark NOC tool). Theme preference is a runtime concept, not a per-provider concept: it is set once in `presentation.defaults` and flows in principal-context to every provider rendering. Putting it inside a per-binding `config` block would scope it to one provider, which breaks the multi-provider case — Airlock running Tailwind-default and the Airlock renderer simultaneously must produce the same effective preference for both. Providers may still carry their own *internal* visual variant in their `config` (Carbon's `variant: "g100"` selecting Carbon's dark variant when the runtime says "dark"); that is a provider-internal mapping, not a principal preference, and is unrelated to the `theme_default` / `theme_locked` keys. Boundary merge follows the standard root → org → app/leaf-wins rule from BRD #1 §8.

### 6.3 Semantic fields, not styling

Source carries semantic intent; the provider decides what it looks like. Examples already present in the example corpus:

- `success shows toast` — severity is `success`. Provider decides what success looks like in its visual language.
- `Highlight rows where priority == "critical"` — emphasis is semantic. Provider decides what emphasis looks like.
- `"Alerts" links to "Reorder Alerts" visible to all, badge: open alert count` — badge is semantic. Provider decides badge appearance.
- `For each ticket, show actions: ... if available, hide otherwise` — visibility is a semantic predicate. Provider renders visible actions, omits hidden ones.

Contract data shapes carry these as typed fields. A `toast` data shape includes `{ message, severity, duration? }`. A `data-table` row metadata includes `{ highlighted: bool, available_actions: [...], visible_actions: [...] }`. A `nav-bar` link entry includes `{ label, target, visible: bool, badge_value?: number|string }` with the visibility predicate already evaluated against the current principal.

This is the right partition: **source declares what something is and what is true about it; the provider decides what it looks like.**

### 6.4 Colorblind-safety is a horizontal conformance requirement

Colorblind-safety is not a fifth theme value. All four theme values (`light`, `dark`, `auto`, `high-contrast`) must use colorblind-safe color choices for any color that carries information — severity (`success`, `error`, `warning`, `info`), status, highlight, badge, and any other field where color encodes meaning rather than decoration.

This is a provider conformance requirement, raised as a floor rather than added as a column on the theme enum. First-party providers (§9) must satisfy it. The Phase 5a (§12.1) conformance test suite includes a colorblind-safety check on first-party providers' information-carrying palettes: deuteranopia, protanopia, and tritanopia simulation plus luminance-contrast verification. Reviewers reject third-party providers that fail this check.

Where a provider uses color to convey information, it must also signal the same information through luminance, shape, label, or icon — never through hue alone. This is the audience tenet applied to visual perception: every reviewer and every principal must be able to read the rendered surface, regardless of color vision.

---

## 7. The Render-Side Provider Contract

### 7.1 The runtime has a client embodiment: termin.js

Termin runtimes have a client-side embodiment, conventionally called termin.js, owned by the runtime. termin.js handles all network behavior: WebSocket multiplex connections, REST and WebSocket endpoints, auth token lifecycle, subscription registration and re-render triggering, form submission, navigation, initial-data hydration. termin.js is not a Presentation provider concern.

### 7.2 Provider responsibilities

The Presentation provider is pure rendering. The runtime invokes the provider with three inputs:

1. **A fragment of Presentation IR** — the compiled-down representation of one component instance from source: which contract, which modifiers, which bindings.
2. **The bound data for that specific component instance** — already fetched, already confidentiality-filtered, already scoped to the current principal, already projected to declared columns/fields. The provider receives only the data bound to its rendering site, not the full app data set.
3. **Principal context** — role set, scope membership, theme preference, and any contract-specific principal fields declared by the contract.

The provider returns rendered output (form depends on provider's render mode, §7.4) plus action-handler registrations. termin.js wires the provider's actions back to runtime operations. Confidentiality filtering, principal scope checks, action authorization, audit logging, state-transition enforcement, and subscription fan-out all stay in the runtime core.

### 7.3 Markdown sanitization is contract-specified, not provider discretion

The `markdown` contract specifies the sanitization envelope: which markdown features are accepted, which are stripped, which are rejected. v0.9 envelope: bold, italic, underline, strike-through, links, headers, horizontal rules. Stripped: raw HTML, script tags, embedded media, code blocks. Provider conformance includes sanitization compliance; a provider that renders unsanitized HTML from markdown fails the conformance test.

### 7.4 Two render modes, one contract

The same contract supports two execution models. Provider authors pick based on their design system's natural shape:

- **SSR-style provider** runs server-side at request time. termin.js (server-side variant) calls the provider with IR + bound data, wraps the result with the client-side termin.js bootstrap, returns to the browser. Subsequent updates flow through termin.js's subscription mechanism; the provider re-renders fragments as requested.
- **CSR-style provider** ships a client-side bundle. termin.js loads it; it registers render functions keyed by IR contract. termin.js calls those functions whenever a component needs to render or re-render. All data flows through termin.js.

Both modes are pure rendering. Neither knows the network surface. Provider authors pick one or implement both. GOV.UK Design System fits SSR cleanly; Carbon's React bindings fit CSR cleanly; Tailwind-default ships both modes for reference.

### 7.5 Plugin mechanism deferred

How termin.js loads provider bundles, how providers register, dynamic vs static linking, hot reload, sandboxing — implementation concerns. Specified by Claude Code at implementation time. The contract surface is what BRD #2 specifies; the loading mechanism is outside scope.

### 7.6 Field-level redaction

Confidentiality filtering happens at two levels with different semantics:

- **Row-level filtering** (Storage): when a Content type is scoped (e.g., `Scoped to "salary.access"` per BRD #1), records the principal cannot view are absent from the result set. The provider never sees them.
- **Field-level redaction** (post-Storage, pre-provider): when a single field on an otherwise-visible record is restricted from the current principal, the runtime replaces the value with a typed redaction sentinel before the record reaches the provider. The **field is present in the record, not absent** — the field is part of the contract data shape, and consumers expect it.

The sentinel is type-safe: a numeric field's redaction marker is distinguishable from `0`; a text field's redaction marker is distinguishable from `""`; a boolean field's is distinguishable from both `true` and `false`. The exact wire representation — typed-null with sidecar metadata, wrapper object `{ __redacted: true }`, dedicated `Redacted` type, or another shape — is a Claude Code implementation choice. The constraint is that providers can detect redaction without ambiguity and without inspecting parallel metadata channels.

The provider reads the marker and decides rendering. Render-as-blank, render-as-`[redacted]`, render-as-lock-icon, render-as-`••••` — any of these is valid per the provider's design system. The runtime does not dictate visual treatment of redaction. The audit trail (BRD #1 §6.3.4) records the principal context that produced the redaction, not the provider-side rendering choice.

---

## 8. Conformance Advertisement and Validation

### 8.1 Provider declares conformance in package metadata

Every Presentation provider package ships metadata that declares the contracts it implements. The declaration is a list of fully-qualified contract names from any namespace.

Example provider metadata (Carbon):

```yaml
provider:
  name: "carbon"
  kind: "presentation"
  conforms_to:
    - "presentation-base.page"
    - "presentation-base.text"
    - "presentation-base.markdown"
    - "presentation-base.data-table"
    - "presentation-base.form"
    - "presentation-base.chat"
    - "presentation-base.metric"
    - "presentation-base.nav-bar"
    - "presentation-base.toast"
    - "presentation-base.banner"
  render_modes: ["csr"]
  config_schema: "carbon-config.schema.json"
```

A third-party narrow provider (e.g., a premium-table renderer) declares only the contracts it implements:

```yaml
provider:
  name: "deluxe-table-renderer"
  kind: "presentation"
  conforms_to:
    - "acme-ui.premium-table"
  render_modes: ["csr"]
```

Conformance is declared, not discovered. The runtime trusts the declaration but verifies it via the conformance test suite (§11).

### 8.2 Compile-time validation

The compiler validates that every contract referenced in source resolves to a declared contract:

- For `presentation-base.*` references (implicit from verb usage), the compiler checks against the built-in `presentation-base` contract definitions.
- For `Using "ns.contract"` references, the compiler resolves `ns` to a contract package and checks that `contract` is declared in that package's contract list.
- A misspelled reference (`Using "acme-ui.premium-tabel"`) is a compile error with a specific message.

This catches typos and broken references before deploy and before runtime.

### 8.3 Deploy-time validation

At deploy time:

1. The compiler emits the `required_contracts` set as part of the application IR — the union of `presentation-base.*` contracts derived from verb usage and explicit `Using` references.
2. The runtime reads the deploy config Presentation bindings.
3. For each required contract, the runtime resolves which binding covers it. Sub-contract bindings (`presentation-base.data-table`) win over namespace bindings (`presentation-base`).
4. For each resolved binding, the runtime verifies the bound provider's `conforms_to` list contains the required contract.
5. If every required contract resolves to a provider that declares it, deploy proceeds.
6. If any required contract has no resolving binding, or resolves to a provider that does not declare it, deploy fails closed with a specific error: `App requires "presentation-base.markdown"; bound provider "gov-uk-design-system-narrow" does not declare it.`

Fail-closed is mandatory. The runtime does not attempt graceful degradation for missing contracts in v0.9.

### 8.4 Tier classification

Presentation is **Tier 2** across the board. Visual surface; bounded blast radius (provider cannot leak data, fire unauthorized actions, or escalate privilege); operational impact is "users see degraded UI" rather than "system is down" or "data is leaked". This matches the BRD #1 §10 classification.

Identity remains the only Tier 0 contract category.

### 8.5 The `required_contracts` manifest

The `required_contracts` set referenced in §8.3 step 1 is a top-level field of the application IR, parallel to other manifest sets emitted under BRD #1 (Identity scopes, Storage content types, Compute shapes, Channel directives).

**Format.** A list of fully-qualified contract names, each a string of shape `<namespace>.<contract>`. Example for the Airlock app of §10.5:

```json
"required_contracts": [
  "airlock-components.airlock-terminal",
  "airlock-components.cosmic-orb",
  "airlock-components.scenario-narrative",
  "presentation-base.page"
]
```

**Composition.** The compiler emits one entry for every `presentation-base.<contract>` derived from a base verb encountered in user-story bodies (verb → implicit contract per §4.2), plus every distinct `<ns>.<contract>` from a `Using` reference (§4.3). Duplicates collapse — a contract referenced ten times in source still appears once in the manifest.

**Order.** Alphabetical, ascending. The order has no runtime semantics; sorting is for diff-friendliness across builds, so reviewers see meaningful changes when source adds or removes a contract reference.

**Future extensibility.** v0.9 ships the list-of-strings shape. If Phase 5b or 5c surfaces a need for per-site metadata — diagnostic messages, source-location tracking for error reporting, modifier-specific bindings — the manifest can grow to a list-of-objects shape (`{ contract: <string>, sites?: [...] }`) without breaking v0.9 consumers, by treating bare strings as `{ contract: <string> }` shorthand. The runtime currently consumes only the contract names; richer metadata would surface in tooling first.

---

## 9. First-Party Providers

The reference Termin runtime ships three first-party Presentation providers. All three implement the full `presentation-base` namespace as a quality bar; where a design system lacks a native equivalent for a contract (e.g., Carbon's lack of a canonical `metric` component), the provider composes it from existing primitives.

### 9.1 `tailwind-default`

The implicit default. Used if no Presentation provider is bound. Subsumes the stub-provider role required by BRD #1 — minimal styling, debug-friendly output, full coverage of `presentation-base`.

- **Theme support:** `light`, `dark`, `auto` via CSS custom properties; `high-contrast` via media-query-extended palette.
- **Render modes:** SSR and CSR both shipped.
- **Markdown:** the v0.9 envelope rendered with default Tailwind prose classes.
- **Use case:** local development, headless environments, any deployment that has not made an explicit design-system choice.

### 9.2 `carbon`

IBM Carbon Design System bindings.

- **Theme support:** Carbon's white, g10, g90, g100 themes mapped to `light`/`dark`/auto; high-contrast mapped to a Carbon high-contrast variant.
- **Render mode:** CSR primary (Carbon's React bindings).
- **Audience:** enterprise applications, AWS-internal Kazoo deployments, organizations standardizing on Carbon.
- **Composition notes:** `metric` composed from Carbon's number-display patterns + supporting card; `chat` composed from list + input primitives; `markdown` rendered with Carbon typography tokens.

### 9.3 `gov-uk-design-system`

UK Government Digital Service Design System bindings.

- **Theme support:** light variant primary; dark and high-contrast variants per GOV.UK accessibility guidance.
- **Render mode:** SSR primary (GOV.UK is server-side-leaning, accessibility-first, opinionated about progressive enhancement).
- **Audience:** public-sector applications, transparency-oriented deployments, accessibility-critical contexts.
- **Composition notes:** `chat` and `metric` composed from GOV.UK pattern primitives; nav-bar mapped to GOV.UK's header pattern.

### 9.4 Why two real design systems plus a default

Carbon (CSR-leaning, enterprise) and GOV.UK (SSR-leaning, public-sector) are different enough across render mode, aesthetic, and target audience to validate that the contract surface accommodates real-world variation. Tailwind-default fills the role of an always-available baseline and the BRD #1 stub requirement. More first-party providers would risk scope creep; third-party providers can fill any remaining design system (Polaris, Fluent, Material, Bootstrap) once the contract surface is stable.

---

## 10. Contract Packages and New Component Types

### 10.1 The package-author path

When an application needs a component that does not exist in `presentation-base` — Airlock's `cosmic-orb`, an organization's `kanban-board`, a domain-specific `geographic-map` — the package-author persona declares a new contract in a new namespace, ships a contract package, and ships a provider implementing those contracts.

Source then references the new contract via `Using "<ns>.<contract>"` (mandatory for non-default namespaces) and uses the verbs declared by the contract. Deploy config binds the namespace to the provider.

### 10.2 Contract definition shape

Each contract in a package declares:

1. **`source-verb`** — the canonical verb that introduces the component in source (e.g., `Show a cosmic orb of`). The compiler grammar is extended at parse time by the included contract packages; new verbs become legal in source as their packages are referenced via `Using`.
2. **`modifiers`** — closed set of modifier clauses the component accepts (e.g., `"Pulse on event <event-name>"`, `"Color by <field>"`).
3. **`data-shape`** — typed declaration of what the runtime fetches and passes to the provider. Includes the primary content/state binding plus auxiliary bindings (event streams, command lists, etc.).
4. **`actions`** — closed set of actions the provider may emit back through termin.js. Each action has a typed payload shape.
5. **`principal-context`** — the principal-context fields the contract requires (always includes role set and theme preference; may add scope membership, identity claims, etc.).
6. **`extends`** *(optional)* — names a base contract whose verb and modifier set this contract is a drop-in or extension of. Three patterns:
   - **No `extends`** — a wholly-new component type with its own verb (Airlock's `cosmic-orb`). Used in new-verb mode (§4.3).
   - **`extends X` with no additions** — drop-in replacement; source uses base verb, modifier set is identical (a custom `data-table` renderer). Used in override mode (§4.3).
   - **`extends X` with additions** — extension; source uses base verb, modifier set is base ∪ additions (a `data-table` with extra `Show density toggle` modifier). Used in override mode.

   **Modifier validation.** For an implicit `presentation-base` use site (no `Using`), modifiers are validated against the base contract's closed modifier set. For an explicit `Using "ns.contract"` site, modifiers are validated against the union of the base contract's modifier set (walked up the `extends` chain) and `contract`'s own additions. Additions are name-disjoint from the base set: an extension may add new modifiers but may not redefine an existing modifier with different semantics. Per the v0.9 cap (Appendix B item 8), the `extends` chain depth is one — an extension contract may not itself be extended — so the union reduces to base ∪ additions for every override-mode use site.

The exact serialization (YAML, Termin-flavored DSL, JSON Schema) is implementation; the shape above is what the compiler and runtime consume.

### 10.3 Grammar extensibility

Including a contract package extends the Termin compiler grammar with the package's declared source-verbs. Without `Using "airlock-components.cosmic-orb"` in source, the verb `Show a cosmic orb of` is unrecognized; with it, the verb is legal at the use site.

This is a meaningful design property: **the Termin grammar is `presentation-base` verbs plus whatever any included package declares.** Source files declare their grammar dependencies through `Using` references. Reviewers reading source can derive the full set of legal verbs from the `Using` set plus `presentation-base`.

### 10.4 Mandatory `Using` for non-default namespaces

Any contract from a namespace other than `presentation-base` requires explicit `Using` at the source site. This is what makes the namespace-dependency set derivable from source and what gives reviewers a single search target (`grep "Using "`) for every override or extension site.

### 10.5 Worked example: Airlock

The Airlock app (a sci-fi escape-room AI fluency assessment) is the v0.9 forcing function for new component types. Three components have no `presentation-base` analog:

- `cosmic-orb` — animated visual representation of scenario state, pulsing on events, color-coded by tension level
- `airlock-terminal` — CRT-styled command interface for the player to interact with the simulated ship
- `scenario-narrative` — story-beat presentation with timing, voice, and player-gated reveals

These are wholly-new contracts, no `extends`. The Airlock contract package declares them in the `airlock-components` namespace; the Airlock provider implements all three.

Source (excerpt):

```
As a player, I want to experience the airlock scenario:
  Show a page called "Airlock"
    Show a cosmic orb of scenario state
      Using "airlock-components.cosmic-orb"
      Pulse on event "scenario.tension.changed"
      Color by tension_level
      Size by stakes
    Show an airlock terminal for player commands
      Using "airlock-components.airlock-terminal"
      Accept commands open_door, close_door, vent_atmosphere, query_systems, request_help
      History limit 50
    Show scenario narrative from narrative beats
      Using "airlock-components.scenario-narrative"
      Reveal on event "beat.unlocked"
      Voice "ship-computer-calm"
```

Deploy config binds `presentation-base` (for the page, nav, toasts, forms) to one provider and `airlock-components` to the Airlock-shipped provider:

```yaml
presentation:
  bindings:
    "presentation-base":
      provider: "tailwind-default"
      config: { theme_default: "dark" }
    "airlock-components":
      provider: "airlock-renderer"
      config: { effects_quality: "high" }
```

Two providers active simultaneously, each handling its own namespace's render sites. The runtime dispatches each component IR fragment to the provider whose namespace it belongs to.

This case exercises every new mechanism in BRD #2: new namespace, contract package, new contracts without `extends`, new source verbs, mandatory `Using`, multi-provider deployment, grammar extensibility. If Airlock works, the system works.

---

## 11. Boundary Model and Deploy Config

### 11.1 Boundary model carries over

Three levels: root → org → app/leaf. Applications are implicitly leaf boundaries. Conflict resolution is leaf-wins (key-level shallow merge per BRD #1 §8 revision). This applies to Presentation deploy config the same way it applies to Identity, Storage, Compute, and Channels.

A root boundary may set `theme_default: "auto"` and bind `presentation-base` to a corporate-standard provider; an org boundary may override the binding for its sub-tree; an app boundary may override per-contract bindings (e.g., one app's premium-table override).

### 11.2 Deploy config shape

```yaml
version: 0.9.0
boundary:
  parent_path: "<org-or-root-path>"

presentation:
  bindings:
    "<namespace-or-contract>":
      provider: "<provider-name>"
      config: { <provider-specific-opaque-config> }
    "<more-bindings>": ...
  defaults:
    theme_default: "auto" | "light" | "dark" | "high-contrast"
    theme_locked: "<one-of-the-above>"  # optional; if set, user cannot change
```

### 11.3 Binding resolution rules

1. The runtime collects all required contracts from the application IR's `required_contracts` set.
2. For each required contract, the runtime resolves the binding by looking up:
   - First, a contract-specific binding (`presentation-base.data-table`).
   - Then, a namespace binding (`presentation-base`) — applies if the bound provider declares conformance for the contract.
   - If no binding resolves, deploy fails closed.
3. Sub-contract bindings always win over namespace bindings.
4. A namespace binding to a provider that does not implement the full namespace is valid, but only covers contracts the provider declares conformance for. Other contracts in that namespace must have their own bindings or deploy fails.

### 11.4 Provider config is opaque to the runtime

Each binding's `config` block is passed to the provider at initialization unmodified. The runtime does not interpret or validate provider-specific config beyond the conformance checks. This is the same opacity pattern as `connection_string_ref` for Storage providers in BRD #1.

Provider config schemas are advertised in provider metadata (`config_schema` field, §8.1) for IDE assistance and operator validation, but the runtime does not enforce the schema.

### 11.5 No `parent` in the config file

Boundary parentage is determined at deploy by environmental parameters (subdomain, runtime config), not by the config file. Same as BRD #1 §8: lets the same package + same deploy config bind to different parent boundaries across environments.

---

## 12. Implementation Plan

Phase 5 of the v0.9 milestone (per BRD #1 §11). Sub-phased. Each sub-phase ends with a strictly more capable runtime, all prior conformance tests still green, new test suite added.

### 12.1 Phase 5a — Theming infrastructure and first-party providers

**Scope:**

- Implement provider-loading scaffolding for Presentation (parallel to Identity/Storage/Compute/Channels scaffolding from Phase 0).
- Implement `tailwind-default` as the first first-party provider, full `presentation-base` coverage, both SSR and CSR render modes.
- Implement principal-context theme-preference plumbing: storage (per-principal, persistent), runtime operation for setting it (called via termin.js), passing through to provider at render time.
- Implement compile-time validation of `presentation-base.*` contract usage against the built-in contract definitions.
- Implement deploy-time validation of namespace-binding resolution.
- Conformance test suite for `presentation-base` contracts, including the colorblind-safety check (§6.4) on Tailwind-default's information-carrying palettes (deuteranopia / protanopia / tritanopia simulation plus luminance-contrast verification).
- Migrate existing example files (helpdesk, projectboard, warehouse, hrportal, agent_chatbot, hello, hello_user) to confirm v0.9 grammar works without source changes — these apps already have no `Presentation:` block by virtue of the dropped construct, so migration is verifying they parse and run on Tailwind-default.

**Exit criteria:**

- All v0.9 example apps run on Tailwind-default with theme preference working.
- Deploy fails closed when a required `presentation-base` contract is unbound and no default is configured.
- Conformance tests pass for `tailwind-default`.

### 12.2 Phase 5b — Override mechanism and additional first-party providers

**Scope:**

- Implement `Using "<ns>.<contract>"` as a sub-clause modifier on every rendering statement.
- Compiler aggregates `Using` references and emits the namespace dependency set in IR.
- Compile-time validation of `Using` references against contract package definitions.
- Implement Carbon and GOV.UK first-party providers, both with full `presentation-base` coverage including composition for non-native components.
- Multi-instance addressing: a `data-table` site with `Using "acme-ui.premium-table"` resolves to a different provider than other `data-table` sites in the same app.
- Sub-contract bindings (`presentation-base.data-table` overriding `presentation-base` for one contract) work correctly per §11.3.
- Theme-preference plumbing extended to all three first-party providers.
- Conformance test suite extended for Carbon and GOV.UK providers.

**Exit criteria:**

- An app can use Carbon for everything and Tailwind-default for one overridden `data-table`, deployed via deploy config.
- A third-party narrow provider (e.g., a fictional `deluxe-renderer` that only implements `presentation-base.data-table`) integrates correctly.
- Compile-time and deploy-time validation catch mismatches with specific error messages.

### 12.3 Phase 5c — Contract packages and grammar extensibility

**Scope:**

- Contract package format finalized (serialization, shape per §10.2).
- Compiler reads contract packages declared via `Using` references at compile time; grammar is extended with each package's declared source-verbs.
- Compile error for `Using "ns.contract"` where `ns` is not a known package or `contract` is not declared in the package.
- Runtime loads contract-package providers at deploy time; deploy fails closed if a `Using` reference resolves to a contract whose namespace has no provider binding.
- Multi-provider rendering: an app with `Using` references in multiple namespaces dispatches each component IR fragment to the correct provider.
- Airlock contract package authored as the proving ground: `cosmic-orb`, `airlock-terminal`, `scenario-narrative` contracts plus the Airlock renderer provider implementing all three.
- Grammar conflict detection: if two included packages declare the same source-verb, deploy fails with a conflict error.
- Conformance test suite extended for contract-package validation and multi-provider rendering.

**Exit criteria:**

- Airlock app runs end-to-end on the runtime with Tailwind-default for `presentation-base` and the Airlock renderer for `airlock-components`.
- A reviewer can trace any `cosmic-orb` use site in source to its contract definition and to the bound provider via `grep` + deploy config.
- Compile-time grammar extension is fully working: a `Show a cosmic orb of` clause is a parse error in an app without `Using "airlock-components.cosmic-orb"` and legal in an app with it.

### 12.4 Cadence

One sub-phase per minor release recommended. v0.9 ships when 5c is complete. v1.0 is reserved for the post-v0.9 hardening pass after all phases including Presentation are in production use.

---

## Appendix A — Migrated and New Example Files

### A.1 helpdesk.termin with premium-table override (illustrative)

Source-level changes from the v0.9 corpus version: one `Using` sub-clause on the ticket-queue table. No top-level Presentation block. Everything else identical.

```
As a support agent, I want to see all open tickets
  so that I can work on resolving them:
    Show a page called "Ticket Queue"
    Display a table of tickets with columns: title, priority, category, ticket lifecycle, assigned to, created at
      Using "acme-ui.premium-table"
      Highlight rows where `priority == "critical" || priority == "high"`
      Allow filtering by ticket lifecycle, priority, and category
      Allow searching by title or description
      For each ticket, show actions:
        "Start Work" transitions ticket lifecycle to in progress if available, hide otherwise
        ...
      This table subscribes to tickets changes
```

### A.2 airlock.termin (new)

Sketch of the Airlock app source. Authored as a demonstration of grammar extensibility.

```
Application: Airlock
  Description: AI-fluency assessment escape room
Id: <uuid>

Identity:
  Scopes are "airlock.player", "airlock.observer", "airlock.designer"
  A "player" has "airlock.player"
  An "observer" has "airlock.observer"
  A "designer" has "airlock.designer"

Content called "scenario state":
  Each scenario state has a tension_level which is a number
  Each scenario state has a stakes which is a number
  Each scenario state has a current_beat which is text
  Anyone with "airlock.player" can view scenario state

Content called "narrative beats":
  Each beat has a body which is markdown, required
  Each beat has a unlock_condition which is text
  Each beat has a voice which is one of: "ship-computer-calm", "ship-computer-urgent"
  Anyone with "airlock.player" can view narrative beats

As a player, I want to experience the airlock scenario:
  Show a page called "Airlock"
    Show a cosmic orb of scenario state
      Using "airlock-components.cosmic-orb"
      Pulse on event "scenario.tension.changed"
      Color by tension_level
      Size by stakes
    Show an airlock terminal for player commands
      Using "airlock-components.airlock-terminal"
      Accept commands open_door, close_door, vent_atmosphere, query_systems, request_help
      History limit 50
    Show scenario narrative from narrative beats
      Using "airlock-components.scenario-narrative"
      Reveal on event "beat.unlocked"
      Voice "ship-computer-calm"
```

### A.3 Deploy config — Airlock production

```yaml
version: 0.9.0
boundary:
  parent_path: "clarity-intelligence.airlock"

identity:
  provider: "okta"
  config: { ... }
  role_mappings:
    "player": ["okta-group-airlock-players"]
    "observer": ["okta-group-airlock-observers"]
    "designer": ["okta-group-airlock-designers"]

storage:
  provider: "postgres"
  config: { connection_string_ref: "secrets/airlock-db" }

presentation:
  bindings:
    "presentation-base":
      provider: "tailwind-default"
      config: {}
    "airlock-components":
      provider: "airlock-renderer"
      config: { effects_quality: "high" }
  defaults:
    theme_default: "dark"
    theme_locked: "dark"
```

---

## Appendix B — Open Threads for Successor Instances

1. **Versioning of namespace `Using` references in source vs deploy config.** v0.9 ships with no version pinning in source (`Using "acme-ui.premium-table"`, no version). Deploy config could carry version constraints (`acme-ui: { provider: ..., version: "^1.2" }`). If forcing functions emerge (multiple incompatible major versions of a contract package in the same runtime), revisit. For v0.9, packages are pinned at deploy time by the runtime operator.

2. **Chart contract.** Deferred to a future release. Forcing function: a real dashboard use case with stable enough chart-vocabulary requirements to justify a contract surface. v0.9 omits.

3. **Markdown coverage extensions.** v0.9 ships bold, italic, underline, strike-through, links, headers, horizontal rules. Tables-in-markdown, images-in-markdown, code blocks deferred. Each has its own forcing function and its own sanitization complexity.

4. **CMS-style markdown page composition.** The markdown content type plus the `markdown` rendering contract are the foundational primitives. CMS authoring patterns can be built on top in a future BRD when a forcing function exists.

5. **Plugin loading mechanism.** Specified by Claude Code at implementation. Open questions: dynamic vs static linking, hot reload, sandboxing of provider code, version compatibility checks at load time.

6. **Conformance test suite authorship.** The `presentation-base` contracts each need conformance tests. Should be implemented in Phase 5a alongside Tailwind-default. Test shape: given a sample IR fragment + bound data + principal context, the provider's render output must satisfy declared invariants (data fields visible, modifier behaviors correct, action handlers wired, sanitization compliant for markdown).

7. **Provider-side theme toggle UI conventions.** First-party providers all conventionally place the theme toggle in `nav-bar`. Document this as a recommendation; not enforced by contract. Third-party providers may place it elsewhere.

8. **The `extends` chain depth.** Locked at one level for v0.9: a contract may extend a base contract, but an extension may not itself be extended. The compiler enforces. Modifier validation walks the (single-edge) chain to compute the union (§10.2). Revisit if a forcing function for deeper chains emerges; the rule will be a relaxation, not a breaking change.

9. **`Display count of <X> grouped by <field>` under the metric cap.** **Resolved 2026-04-26 (clarifications round 2).** The cap was reformulated rather than tightened: the parser cap of 3 applies only to the **explicit-listed** breakdown form (`with <V1> vs <V2> vs <V3> breakdown`); the **field-grouped** form is uncapped at the source level, with a recommended provider-side display limit of 10 and provider's-choice overflow handling. The corpus uses in `warehouse.termin`, `channel_demo.termin`, `hrportal.termin`, and `security_agent.termin` are preserved as-authored. See §5.1 #7 and §4.2 for the locked spec.

   The original three options considered (compile-time strict, runtime cap with overflow, runtime error) are preserved here as a record of the decision. None were chosen; the partition by source-form turned out to be the cleaner cut. Future revisits could narrow the field-grouped form to a stricter compile-time check when the grouping field is a closed enum, but this is a refinement, not a v0.9 blocker.

---

## Appendix C — Sketch of the Contract Package Format

For Claude Code reference. This is shape-not-serialization; serialization is implementer's choice (YAML / Termin-flavored DSL / JSON Schema).

```
namespace: airlock-components
version: 0.1.0
description: Airlock escape-room presentation components

contracts:

  - name: cosmic-orb
    source-verb: "Show a cosmic orb of <state-ref>"
    modifiers:
      - "Pulse on event <event-name>"
      - "Color by <state-field>"
      - "Size by <numeric-field>"
    data-shape:
      state-record:
        type: "content-record"
        confidentiality-filtered: true
      pulse-events:
        type: "event-stream"
        bound-via: "Pulse on event"
    actions:
      - name: "orb-clicked"
        payload: { state-id: "id" }
      - name: "orb-focused"
        payload: { state-id: "id" }
    principal-context:
      - role-set
      - theme-preference

  - name: airlock-terminal
    source-verb: "Show an airlock terminal for <command-set>"
    modifiers:
      - "History limit <number>"
      - "Accept commands <command-list>"
    data-shape:
      command-history:
        type: "record-stream"
        confidentiality-filtered: true
      available-commands:
        type: "list"
        scoped-by-principal: true
    actions:
      - name: "command-submitted"
        payload: { command: "string", args: "object" }
      - name: "command-cancelled"
        payload: {}
    principal-context:
      - role-set
      - scope-membership
      - theme-preference

  - name: scenario-narrative
    source-verb: "Show scenario narrative from <content-ref>"
    modifiers:
      - "Reveal on event <event-name>"
      - "Gate by scope <scope-name>"
      - "Voice <voice-id>"
    data-shape:
      narrative-record:
        type: "content-record"
        markdown-fields: ["body"]
        confidentiality-filtered: true
      reveal-events:
        type: "event-stream"
        bound-via: "Reveal on event"
    actions:
      - name: "beat-completed"
        payload: { beat-id: "id" }
      - name: "beat-skipped"
        payload: { beat-id: "id" }
    principal-context:
      - role-set
      - scope-membership
      - theme-preference
```

The above is what a contract package author writes. The compiler reads it at compile time when the source includes `Using "airlock-components.cosmic-orb"`; the runtime reads it at deploy time when validating that the bound provider declares conformance for these contracts.

---

*End of BRD #2.*
