# Termin v0.9 Source Refinements — Business Requirements Document

**Version:** 0.9.0-draft (BRD #3; synthesized 2026-04-26, revised same day)
**Status:** Formative — companion to BRD #1 (`termin-provider-system-brd-v0.9.md`) and BRD #2 (`termin-presentation-provider-brd-v0.9.md`). Together the three BRDs specify the v0.9 source surface end-to-end, alongside the existing confidentiality system (`termin-confidentiality-brd.md` and `termin-confidentiality-spec.md`).
**Audience:** Claude Code instances implementing the v0.9 source-grammar refinements; reviewers of the source-authoring story; future authors of `.termin` applications.

**Relationship to BRDs #1, #2, and the confidentiality BRD:** BRD #1 specified the four non-Presentation primitives (Identity, Storage, Compute, Channels) and the v0.9 Identity block. BRD #2 specified Presentation. The confidentiality BRD specifies field-level scope-based redaction. All three were stress-tested by sketching the Airlock app in v0.9 source (see `airlock-termin-sketch.md`). The sketch surfaced gaps that BRD #3 closes. **BRD #3 layers on top of BRDs #1, #2, and the confidentiality BRD without modifying them — except where explicitly noted (BRD #2 §6.2's theme-preference Principal field is superseded; see §4.2).**

**Revision log (2026-04-26, clarifications round 3):**
- Appendix B open questions all resolved as deferred. Composite/transitive ownership (Q1), directive composition (Q3), and the `the agent` source-level symbol (Q4) deferred to v0.10. Sub-language escape mechanism (Q2) deferred to v1.0 and added to the v1.0 roadmap (`termin-roadmap.md`) as a substantive future BRD topic.
- Composite ownership specifically blocked on Identity-for-Teams: composite ownership has nothing to bind to without a Teams concept in the Principal record, so it cannot meaningfully ship before that exists.
- Appendix B restructured: "Open questions" subsection renamed to "Deferred to future versions" with v0.10 and v1.0 sub-buckets. The "Hotly debated topics (resolved)" and "Considered and rejected" subsections are unchanged.
- No body changes in this round.

**Revision log (2026-04-26, clarifications round 2):**
- §3 ownership: introduced the new `principal` business field type — text-implementation, type-checked at the business level, opaque to applications. Identity-keyed fields use this type instead of bare `text`.
- §3 ownership: cross-primitive implications spelled out — channels cascade ownership from carried content; compute outputs follow §3 if the output content is owned; events, boundaries, and presentation are unaffected.
- §3 ownership: agent on-behalf-of semantics specified. Every action carries two principals — `on_behalf_of` (the user the action is for) and `invoked_by` (the principal that performed it, possibly an agent). Ownership filters use `on_behalf_of`; scope checks use `invoked_by`. Source vocabulary: `the user` is `on_behalf_of`.
- §4 renamed and reframed: `current_principal` → `the user` throughout. Symbol available in Termin source contexts only, NOT in sub-language strings (Directive/Objective bodies, CEL bodies). Sub-language escape mechanisms are a separate provider-grammar topic, deferred to a future BRD.
- §4 the-user's-content lookup: `the current <content>` renamed to `the user's <content>`. Compiler requires the ownership field to be `unique` — the multiple-row case is prevented at the storage level. Read semantics: returns null if no row exists. Update semantics: upserts. No runtime error path.
- §4 Principal type: `theme_preference` top-level field replaced by an extensible `preferences: map<text, value>` store; theme is `preferences.theme`. **This supersedes BRD #2 §6.2's top-level theme-preference field on Principal.**
- §4 every-context-has-a-principal: Anonymous has a principal; system events use a synthetic system principal (`is_system: true`). Source that uses `the user` always resolves to a non-null Principal. The runtime-error path I had originally drafted is removed.
- §5 (was: field-level confidentiality predicates) **dropped from BRD #3**. The existing confidentiality system (`termin-confidentiality-brd.md`) covers field-level visibility via `confidentiality is "<scope>"` (see `examples/hrportal.termin`). The Airlock case (tool_calls visible to Evaluator, redacted to Player) is solved with a single line on the existing grammar; no new BRD #3 spec needed.
- §5 (now §6) state-machine events: state-name sanitization removed. Event names use state names as declared (preserving spaces and mixed case). The PEG already restricts state names to `[\w\- ]+` plus quoted strings, which keeps event names dot-tokenizable.
- §5 (now §6) payload: replaced `triggered_by: Principal | null` with two non-null fields: `on_behalf_of: Principal` and `invoked_by: Principal` (equal except for agent actions). System-triggered events use a synthetic system principal. There is no null-principal case anywhere.
- §7 (now §6) and §6.5 directive sourcing: "runtime startup" corrected to "application startup" — the per-application restart triggers deploy-config re-read, not a full AppFabric restart.
- §8 (was: contract dual-mode invocation) **dropped from BRD #3 body**. Moved to Appendix B as considered-and-rejected: audience-tenet objection (two ways to do the same thing) plus an unresolved modifier-application semantics question. Revisit if a forcing function emerges.
- Hotly debated state-machine event naming (Appendix B): **Option A locked** (`<content>.<field>.<state>.<verb>`).
- Implementation plan: now 4 sub-phases (6a-6d), down from 5.

**Revision log (2026-04-26, original synthesis):**
- Six gaps identified in `airlock-termin-sketch.md` §9 lifted into formal sections.
- Implementation plan structured as Phase 6 of v0.9 (sub-phases 6a–6e at original synthesis).
- Five open questions and one hotly debated topic captured in Appendix B at original synthesis.

---

## 1. Context and Tenets

The exercise that produced this BRD was sketching the Airlock app's happy path (Landing → Survey → Scenario → Scoring → Results) in v0.9 source. The sketch was meant to validate BRDs #1, #2, and the confidentiality BRD by writing a real-app shape against them. The sketch *did* validate them — the partition between `presentation-base` and `airlock-components` came out clean, the Identity block worked, the state-as-field grammar worked, and the four agents fit naturally.

But four gaps surfaced that are not BRD #1, #2, or confidentiality-BRD errors; they are absences. The source author had to reach for free-form prose ("their own sessions"; "the user's profile"; "trigger the Evaluator when the lifecycle enters scoring"; "load this directive from a file") that no current PEG rule parses. Each gap has a clear shape; none invalidates the prior BRDs.

The five tenets continue to govern. The audience tenet (Tenet 3) is the strongest constraint here — most of these refinements add declarative source-level vocabulary so reviewers can read source instead of guessing intent from agent objective bodies or deploy config.

The four gaps:

1. **Principal-keyed record ownership and "their own" filtering** (§3). Source needs to express "this row is owned by *this principal*" — a row-level filter distinct from scope-membership gating, with proper handling of agents acting on behalf of humans.
2. **Source-level "the user" vocabulary** (§4). Source needs to express "the row keyed by the user" without imperative content.read calls.
3. **State-machine transition events** (§5). Compute needs to subscribe to "this state was just entered"; the current grammar declares transitions but does not formalize the events they emit.
4. **Agent directive sourcing** (§6). Inline triple-backtick directives don't scale to multi-page system prompts; deploy-config and field-reference sourcing forms are needed.

Two further gaps surfaced in the original synthesis but are not in BRD #3's final scope:

- **Field-level confidentiality predicates** — already covered by the existing confidentiality system. The Airlock case I had drafted against this gap (tool_calls visible to Evaluator, redacted to Player) is solved by `confidentiality is "airlock.session.audit"` on the existing grammar. See revision log.
- **Contract dual-mode invocation** — considered and rejected for v0.9. See Appendix B.

Each remaining gap gets its own section below.

---

## 2. Personas

The seven personas from BRD #1 §2 carry over unchanged. For BRD #3 specifically:

- **App author** writes the new source forms (`their own <content>`, `the user`, `the user's <content>`, `Directive from`).
- **Provider author** is largely unaffected, except for two points: (a) Identity providers must support a `principal` business type that maps to text storage but exposes typed Principal records to the application (§3.2); (b) Sub-language providers (a future grammar-extension topic, deferred) will need to specify their own escape-to-host-language mechanism if they want host-language values inside sub-language strings — see §4 footnote.
- **Boundary administrator** is unaffected — these refinements are source-level, not deploy-config-level.
- **Reviewer** reads the new forms. The audience tenet drove the shape: every new form is meant to be readable on a first pass.
- **The runtime** enforces the new semantics: row-level filters, the-user resolution, transition-event emission, directive resolution, and the on-behalf-of model for agent actions.

---

## 3. Principal-Keyed Record Ownership

### 3.1 Problem

v0.9 grammar admits permission lines of the shape `Anyone with "<scope>" can view <content>`. The gate is scope membership. But many applications need ownership — "Anyone with `airlock.session.read` can view *their own* sessions" — where the predicate is per-row and depends on the principal making the request, not just the principal's scope set.

Ownership is also a cross-primitive concern. Channels carry content; if the content is owned, channel subscriptions should respect that ownership. Agents act on behalf of users; ownership filters need to know whether the agent's principal or the user's principal is the owner. The current grammar handles none of this.

### 3.2 The `principal` business field type

Identity-keyed fields are not bare text. They are typed references to a Principal. v0.9 introduces a new business field type:

```
Each session has a player_principal which is principal, required
```

The `principal` business type:
- **Storage:** opaque text (the principal id as issued by the bound Identity provider).
- **Type system:** typed Principal-reference at the business layer. The compiler enforces that values flowing into a `principal` field came from a Principal source (the Identity provider, an `on_behalf_of` reference, the result of `the user.id`, etc.) and not from arbitrary text.
- **Provenance:** opaque to the application. The application does not know whether the underlying string is an Okta sub claim, a UUID, an email, or anything else. The Identity provider owns the format.
- **Comparison:** Principal-typed fields compare with each other (e.g., `session.player_principal == the user.id` is legal because both sides are principal-typed). Comparison with bare text is a compile error — applications must not synthesize principal ids from text input.

Existing fields that should be `principal` and currently are `text` (in pre-v0.9 examples like `moderation_agent`'s `principal_id` and `user_reputation.principal_id`) are migration candidates. v0.9 will accept `text` for backward source compatibility within v0.9 but the migration path to `principal` is recommended; v1.0 may require the typed form.

### 3.3 Content-level ownership declaration

A new sub-line legal inside a Content body:

```
Content called "sessions":
  Each session has a player_principal which is principal, required, unique
  Each session is owned by player_principal
  Each session has a self_rating which is whole number
  ...
  Anyone with "airlock.session.create" can create sessions
  Anyone with "airlock.session.read" can view their own sessions
```

The `Each <singular> is owned by <field>` clause names which field carries the owning principal's id. Constraints on the named field:

- **Type.** Must be `principal` (per §3.2).
- **Cardinality.** Must be `unique` (i.e., declared with the `unique` modifier). This guarantees at most one row per principal — the multiple-row case for ownership lookup (§4.3) is prevented at the storage layer, not at runtime.
- **Required.** Must be `required`. A row with no owning principal cannot exist on an owned content type.

The compiler emits an `ownership: { field: <name> }` block on the ContentSchema IR.

**At most one ownership field per content type.** Multi-field (composite) ownership is out of scope for v0.9 (see Appendix B).

### 3.4 The "their own" permission verb

Permission lines may use `their own <content>` in place of `<content>`:

```
Anyone with "<scope>" can view their own sessions
Anyone with "<scope>" can update their own sessions
Anyone with "<scope>" can view, create, or update their own sessions
```

Legal only when `<content>` declares ownership. Compile error otherwise. The compiler lowers each `their own` permission line to a RouteSpec carrying a `RowFilter { kind: "ownership", field: <name> }` that the runtime evaluates against `the user.id` at query time.

### 3.5 Agents acting on behalf of users

Every action in a Termin application carries two principals:

- **`on_behalf_of`** — the user the action is *for*. The principal whose data is being read or written.
- **`invoked_by`** — the principal that *performed* the action. May be the same as `on_behalf_of` (direct user actions) or different (agent compute, scheduled jobs).

The two principals are distinct for these cases:

| Action shape | `on_behalf_of` | `invoked_by` |
|---|---|---|
| Human posts a request directly | the human | the human (= on_behalf_of) |
| Agent reads/writes during a triggered invocation | the human who triggered the invocation | the agent's synthetic principal |
| CEL-expression state transition fires | the human whose write tripped the predicate | the human (= on_behalf_of) |
| Scheduled job fires (v0.10+ feature) | a synthetic system principal | a synthetic system principal |

The runtime applies them differently:

- **Ownership filters use `on_behalf_of`.** When ARIA writes a message during a player's session, the new message row's `player_principal` field is set to the player's id, not ARIA's. Reads through "their own" filter by `on_behalf_of.id`.
- **Scope checks use `invoked_by`.** When a state transition declares `if the user has "<scope>"`, the scope check is against `invoked_by.scopes`. ARIA may hold `airlock.session.audit` (granting visibility into redacted fields) even if the human player does not.
- **Field-level confidentiality (existing system) uses `invoked_by`.** Field redaction is a function of the principal *seeing* the data, which is the principal performing the read.
- **Audit trails record both.** Per BRD #1 §6.3.4, the audit log carries `invoked_by_principal_id` / `invoked_by_display_name` and `on_behalf_of_principal_id`.

The source-level symbol `the user` (§4) always resolves to `on_behalf_of`. Agents do not have a separate source-level symbol for their own principal in v0.9 — the audit trail captures `invoked_by` and that is enough for v0.9 use cases. (A future `the agent` symbol is a v0.10+ candidate if a forcing function arises.)

**Sandbox: every Compute is delegate-mode by default.** This is consistent with the confidentiality BRD §3.6 / §3.7 and FR-14. A compute that needs to act with elevated scope (service identity, e.g., the `Calculate Team Bonus Pool` example in `hrportal.termin`) opts in via the existing `Identity: service` declaration. Service identity does not change `on_behalf_of` — it only changes `invoked_by` to a system principal with the declared scopes. Ownership filters still apply against the human's `on_behalf_of`.

### 3.6 Cross-primitive implications

Ownership is intrinsic to a Content type. Other primitives consume content; their ownership semantics cascade.

- **Channels.** A channel that carries an owned content type filters subscriptions automatically: subscribers receive only records they own. The Airlock's `tool output stream` channel carries `messages`; if `messages` declares `is owned by player_principal`, the channel cascades the filter — each player receives only messages from sessions they own. No additional source-level declaration needed. If the carried content is *not* owned, the channel admits all subscribers up to scope. If the channel author needs to override (e.g., admin sees all messages on a moderation channel), v0.10+ may add an explicit-channel-ownership-override mechanism; for v0.9, override the underlying scope check instead.

- **Compute.** Compute *invocations* are not records. Compute *outputs* may be records (when a compute writes to a Content type via `Accesses`). If the output Content declares ownership, writes by the compute set the ownership field to `on_behalf_of.id` per §3.5. The audit log records both `invoked_by` and `on_behalf_of`.

- **State-machine events** (§5). Events emitted by transitions carry `on_behalf_of` and `invoked_by` in the payload. Subscribers receive both.

- **Presentation.** Renderers consume data already filtered by ownership at the storage layer; no presentation-level ownership concept is needed. Field-level redaction (existing confidentiality system) applies to fields visible in the rendered output.

- **Boundaries.** Boundary records describe deploy-time configuration, not user data. No ownership concept.

- **Identity.** Identity owns the Principal type itself (§4.2). The Principal type's fields are not ownership-declared content — they are returned by the Identity provider per request.

### 3.7 IR shape

New ContentSchema field:

```
ownership: { field: <name> } | null
```

New RouteSpec field:

```
row_filter: RowFilter | null

RowFilter {
  kind: "ownership" | "scope" | ...,
  field?: <name>,        # for kind=ownership
  scope?: <scope>,       # for kind=scope (pre-existing for `Scoped to "<scope>"`)
}
```

New FieldSpec type variant: `business_type: "principal"` (text storage, typed at the business layer per §3.2).

---

## 4. Source-Level "The User" Vocabulary

### 4.1 Problem

Source needs to express "the row owned by the user" without dropping into imperative `content.read(...)` calls. The Airlock sketch's Evaluator updates "the user's profile" as a side effect of scoring — a concept the model carries but the source vocabulary cannot.

A second concern: the language already uses `the user` in state-machine transition gates (`<from> can become <to> if the user has <scope>`). The vocabulary for the current authenticated principal should be unified across source — one phrase, used consistently.

### 4.2 The `the user` reserved phrase and the Principal type

`the user` is a reserved phrase legal in source contexts that admit a Principal reference:

- Permission lines (`Anyone with X can view their own Y` already implicitly uses "the user" via the ownership filter).
- CEL expressions in transition predicates (`<from> can become <to> if \`the user.id == record.player_principal\``, where `the user` is the on-behalf-of principal).
- Visibility predicates on rendering sites (per the existing `visible to all` form's principal-context dependency).
- The-user's-content lookup (§4.3).

`the user` evaluates to a typed Principal record:

```
Principal {
  id: principal,                            # principal-typed (§3.2)
  display_name: text | null,
  is_anonymous: boolean,
  is_system: boolean,                       # true for synthetic system principals
  scopes: list of text,
  preferences: map of text to value         # extensible key-value store
}
```

The `preferences` field is an extensible key-value store. Theme preference (BRD #2 §6.2) is now `the user.preferences.theme`, with values in `light | dark | auto | high-contrast` per BRD #2's enumeration. Other preference keys may be added by Identity providers, application configuration, or future BRDs without breaking source that doesn't reference them. **This supersedes BRD #2 §6.2's top-level `theme_preference` field on Principal.**

The Identity provider populates the Principal record at request time (per BRD #1 Identity Phase 1's `Principal` dataclass, extended to include `preferences` and `is_system`).

**Sub-language barrier.** `the user` is *not* available inside sub-language strings — Directive/Objective bodies (triple-backtick), CEL expressions in places where the surrounding grammar treats them as opaque, etc. Sub-languages are passed verbatim to their respective providers. If an agent author wants `the user.display_name` interpolated into a system prompt, the sub-language would need its own escape-to-host-language mechanism. **This is a separate concern, deferred to a future BRD on provider-defined sub-language grammars.** v0.9's pattern for parameterizing agent directives is the field-reference form `Directive from <content>.<field>` (§6) — the source author writes a session-prep compute that interpolates Principal fields into the field, then ARIA reads it.

### 4.3 The `the user's <content>` form

Legal in source contexts that admit a content reference, when `<content>` declares `is owned by <field>`:

```
Update the user's profile:
  best_of_level: max(prior, new)
  total_attempts: prior + 1
```

The compiler resolves `the user's <content>` to "the row of `<content>` where `<owning-field> = the user.id`":

- **Existence semantics.** Because the ownership field is required to be `unique` (§3.3), the lookup returns at most one row. There is no multi-row case — it is prevented at the storage layer.
- **Read semantics.** When used in a read context, returns the row if it exists, or null if it does not. Source must handle the null case explicitly (e.g., `if the user's profile != null then ... else ...`).
- **Update semantics.** When used in an update context (the verb is `Update` or `Set`), upserts: creates the row with default field values if it does not exist, then applies the update.
- **Permission.** The lookup respects the same `their own` permission gates that apply to any owned-content access.

### 4.4 Every context has a principal

There is no source context where `the user` resolves to null:

- **Authenticated requests.** `the user` is the authenticated principal.
- **Anonymous requests.** `the user` is the Anonymous principal (per BRD #1 Identity Phase 1: a singleton with `is_anonymous: true`, empty scopes, null display_name).
- **Agent invocations.** `the user` is `on_behalf_of` — the human (or upstream principal) the agent is acting for, not the agent's own principal.
- **CEL-expression-triggered state transitions.** `the user` is the principal whose write tripped the predicate (the principal of the triggering write).
- **Scheduled jobs / system events** (v0.10+). `the user` is a synthetic system principal with `is_system: true`.

There is no runtime-error path for null principal access. Earlier drafts of this BRD proposed one; it is removed.

---

## 5. State-Machine Transition Events

### 5.1 Problem

State-as-field declarations admit two transition forms (`<from> can become <to> if the user has <scope>` and `<from> can become <to> if <CEL-expr>`). Compute can subscribe to events via `Trigger on event "<name>"`. The bridge — *which events do transitions emit* — is not formalized. The Airlock sketch's Evaluator subscribes to a speculative `"sessions.lifecycle.scoring.entered"` event with no source-of-truth backing.

### 5.2 Spec

**Every transition emits two events.** When state-machine field `<field>` on content type `<content>` transitions from state `<from-state>` to state `<to-state>`:

1. **Exit event:** `<content>.<field>.<from-state>.exited` — fires *before* the runtime writes the new state.
2. **Entry event:** `<content>.<field>.<to-state>.entered` — fires *after* the runtime writes the new state.

State names are embedded **as declared** in the source — preserving spaces, mixed case, and hyphens. The PEG grammar restricts state names to `[\w\- ]+` plus quoted strings, which keeps event names dot-tokenizable: state names cannot contain dots, so the dot-separated event-name shape is unambiguous. State names with spaces appear in event names with spaces (`sessions.lifecycle.in progress.entered`); subscribers write the event name as a quoted string (`Trigger on event "sessions.lifecycle.in progress.entered"`), which preserves the spaces.

**Compute subscribes via the existing string form:**

```
Compute called "evaluator":
  ...
  Trigger on event "sessions.lifecycle.scoring.entered"
```

**Naming convention locked.** Option A (`<content>.<field>.<state>.<verb>`) was selected from four considered alternatives (see Appendix B). State sits in front of verb; reads English-naturally ("scoring entered"); glob-friendly (`sessions.lifecycle.*.entered` catches every entry).

### 5.3 Payload shape

Both `entered` and `exited` events carry a typed payload:

```
{
  record_id: <text>,                     # the id of the record that transitioned
  from_state: <text>,                    # the prior state, as declared
  to_state: <text>,                      # the new state, as declared
  on_behalf_of: Principal,               # the user the action was for; never null
  invoked_by: Principal,                 # the principal that performed the action; never null
  triggered_at: <timestamp>,             # event emission time
  trigger_kind: "user_action"            # for `<from> can become <to> if the user has <scope>`
              | "cel_expression"         # for `<from> can become <to> if <CEL-expr>`
              | "agent_action"           # for transitions caused by agent compute writes
              | "system",                # for runtime-initiated transitions (v0.10+)
}
```

`on_behalf_of` and `invoked_by` are both non-null (§3.5). For direct user actions, they are equal. For agent-caused transitions (an agent writes a field and trips a CEL-expr), `invoked_by` is the agent's principal and `on_behalf_of` is the user the agent was acting for. For system-triggered transitions, both are a synthetic system principal.

### 5.4 Multi-state-machine semantics

A content type with multiple state-machine fields emits independent events per field. The Approval Workflow example (`approval_workflow.termin`) has both `lifecycle` and `approval status` fields:

- `documents.lifecycle.draft.exited` / `documents.lifecycle.published.entered` — lifecycle transitions.
- `documents.approval status.pending.exited` / `documents.approval status.approved.entered` — approval-status transitions (multi-word field name preserved verbatim).

The dot-separated event-name shape disambiguates which field a subscriber means.

### 5.5 Compile-time validation

The compiler validates `Trigger on event "<name>"` against declared state machines. A subscriber to `"sessions.lifecycle.scoring.entered"` is a compile error if `sessions` has no `lifecycle` state-machine field, or if `lifecycle` has no `scoring` state, or if the verb is anything other than `entered` or `exited`.

This catches typos before deploy and connects transition declarations to their subscribers in the IR.

### 5.6 Future declarative-trigger form (deferred)

A v0.10 candidate: `Trigger when <content> enters <state>` as syntactic sugar for the string form. v0.9 ships the string form only.

---

## 6. Agent Directive Sourcing

### 6.1 Problem

Agent compute declares its system prompt via `Directive is \`\`\`...\`\`\``. The triple-backtick form works for hundreds of words but not thousands. Real-world agent prompts (e.g., the live Airlock's ARIA prompt) are multi-page documents that need to be loaded from external files, frozen at session start for reproducibility, and possibly versioned.

### 6.2 Spec

**Three forms for `Directive is`** (one per declaration; mutually exclusive):

1. **Inline literal** (existing form, unchanged):
   ```
   Compute called "simple agent":
     Provider is "ai-agent"
     Directive is ```
       You are a helpful agent.
     ```
   ```
2. **Deploy-config reference:**
   ```
   Compute called "ARIA":
     Provider is "ai-agent"
     Directive from deploy config "aria_system_prompt"
   ```
   The deploy config supplies the directive text under the named key. Resolved at **application startup** (the per-application restart triggers deploy-config re-read; the surrounding AppFabric does not need to restart). Reused for all invocations until the application restarts.

3. **Field reference:**
   ```
   Compute called "ARIA":
     Provider is "ai-agent"
     Directive from sessions.aria_system_prompt
   ```
   The directive text is read from the named field on the triggering record at every invocation. Used for session-frozen prompts: at session-start, an event-handler compute populates `session.aria_system_prompt` from a baseline (often the deploy-config value); subsequent invocations re-read from the field, surviving deploy-config updates and preserving reproducibility for completed sessions.

### 6.3 The same three forms apply to `Objective is`

```
Objective is ``` ... ```                          # inline literal
Objective from deploy config "<key>"              # deploy-config reference
Objective from <content>.<field>                  # field reference
```

### 6.4 Versioning (recommendation, not requirement)

Deploy-config-loaded directives may include a version key:

```
aria_system_prompt:
  ref: "configs/aria-v3.md"
  version: "3.0"
```

The compute records the resolved version in its audit trail. v0.9 does not require versioning; it is a recommendation for production deployments where prompt changes are auditable events.

### 6.5 Composition (deferred)

A baseline-plus-overlay form (e.g., `Directive is "<baseline>" with overlay "<overlay>"`) is a real production pattern but out of scope for v0.9. Open question in Appendix B if a forcing function emerges.

### 6.6 Lowering

ComputeSpec gets new fields:

```
ComputeSpec {
  ...
  directive: DirectiveSource,
  objective: DirectiveSource,
}

DirectiveSource = InlineLiteral { text: string }
                | DeployConfigRef { key: string }
                | FieldRef { content: string, field: string }
```

The runtime resolves `directive` and `objective` independently — a compute may use deploy-config directive with an inline objective, etc.

---

## 7. Implementation Plan

Phase 6 of the v0.9 milestone (after Phase 5 / Presentation per BRD #2). Sub-phased; each sub-phase ends with a strictly more capable runtime, all prior conformance tests still green, new test suite added.

### 7.1 Phase 6a — Principal type, ownership, and "the user" (§§3, 4)

**Scope:**
- Add the `principal` business field type. Type-check at the compiler; storage layer continues to use text. Migration story for existing `text` fields that should become `principal` is documented; no breaking changes.
- Implement `is owned by <field>` content-level declaration with required-uniqueness check.
- Implement `their own <content>` permission verb.
- Reserve `the user` as a source-level phrase. Resolve in CEL contexts and the-user's-content lookups.
- Implement `the user's <content>` form with read (returns null if missing) and update (upserts) semantics.
- Extend the Principal record with `preferences: map<text, value>` and `is_system: boolean`. Migrate BRD #2 §6.2's top-level `theme_preference` reference to `preferences.theme`.
- Implement on-behalf-of model: every action carries `on_behalf_of` and `invoked_by` principals; ownership filters use `on_behalf_of`; scope checks use `invoked_by`.
- Implement channel-subscription cascade: subscriptions to channels carrying owned content filter by ownership.
- IR additions: `ownership` block on ContentSchema; `row_filter` field on RouteSpec; `business_type: "principal"` variant on FieldSpec.
- Conformance tests for ownership-filtered reads, writes, and updates; for the-user's-content read/upsert; for agent on-behalf-of writes; for channel-subscription cascade.
- Compile-time error tests for invalid ownership references, non-unique ownership fields, principal-text comparisons.

**Exit criteria:**
- Every `their own` access by a principal returns only their rows; the on-behalf-of model works correctly for agent compute.
- The-user's-content upserts on update.
- The Airlock sketch's six "their own" phrases compile.
- The Principal record's `preferences.theme` flows correctly through to the renderer per BRD #2 §6.2's theme semantics (now backed by the new field).

### 7.2 Phase 6b — State-machine transition events (§5)

**Scope:**
- Implement transition-event emission for all state-machine transitions.
- Wire `<content>.<field>.<state>.entered` and `.exited` events into the event bus.
- Implement payload shape (record_id, from_state, to_state, on_behalf_of, invoked_by, triggered_at, trigger_kind).
- Compile-time validation of `Trigger on event "<name>"` against declared state machines.
- Conformance tests for event emission, payload shape, multi-state-machine independence, multi-word state name preservation.

**Exit criteria:**
- The Airlock sketch's Evaluator triggers on `"sessions.lifecycle.scoring.entered"` and receives a payload with both `on_behalf_of` and `invoked_by`.
- The Approval Workflow example's two state-machine fields emit independent events with correct names.
- Multi-word state names (e.g., `helpdesk.termin`'s `in progress`) appear verbatim in event names without sanitization.

### 7.3 Phase 6c — Agent directive sourcing (§6)

**Scope:**
- Implement `Directive from deploy config "<key>"` and `Directive from <content>.<field>` forms.
- Same for `Objective`.
- Resolution at application startup (deploy-config) vs invocation (field).
- Versioning hook in deploy config (recommendation, not required).
- Conformance tests for each form, plus session-freeze-via-event-handler pattern.

**Exit criteria:**
- A multi-page system prompt loads from deploy config.
- Session-frozen agents read from `<content>.<field>` at every invocation.

### 7.4 Phase 6d — Hardening and migration

**Scope:**
- Migrate existing examples to use `principal` business type where appropriate (the moderation_agent example's `principal_id` fields, the user_reputation example).
- Migrate Airlock sketch references in `airlock-termin-sketch.md` to match the locked grammar.
- Audit BRD #2 references to `theme_preference` and update them to `preferences.theme`.
- Conformance fixtures regenerated; cross-BRD links audited.

**Exit criteria:**
- All examples in `examples/` and `examples-dev/` compile cleanly under the v0.9 grammar.
- BRD #2 §6.2 annotated with the supersession.
- Conformance suite covers all new grammar paths.

### 7.5 Cadence

One sub-phase per minor release recommended. v0.9 ships when 6d is complete. v1.0 is reserved for the post-v0.9 hardening pass after BRDs #1–#3 and the confidentiality BRD are all in production use.

---

## Appendix A — The Airlock Revisited

Each refinement applied to the Airlock sketch (`airlock-termin-sketch.md`):

### A.1 Principal type and ownership

The sketch's `sessions` content gets:

```
Each session has a player_principal which is principal, required, unique
Each session is owned by player_principal
```

Same for `messages` (denormalized): `Each message has a player_principal which is principal, required` plus `is owned by player_principal`. Transitive ownership through `session` is a v0.10+ candidate (Appendix B).

### A.2 The user

The Evaluator's Objective body, currently:

> `4. Update profile (keyed by player_principal): best_of_level, ...`

becomes:

> `4. Update the user's profile: best_of_level: max(prior, new), ...`

The compiler resolves `the user's profile` because `profiles` declares `is owned by principal_id`. Update semantics upserts on first invocation (when no profile exists), updates on subsequent invocations.

### A.3 Field-level confidentiality (covered by existing system)

`messages.tool_calls` becomes:

```
Each message has tool_calls which is structured, confidentiality is "airlock.session.audit"
```

Using the existing confidentiality grammar (per `examples/hrportal.termin`). The Evaluator (which holds `airlock.session.audit` via deploy config) sees full tool calls; the Player (who holds only `airlock.session.read`) sees the redaction marker. No new BRD #3 spec required.

### A.4 State-machine events

The Evaluator's trigger:

```
Trigger on event "sessions.lifecycle.scoring.entered"
```

is now grammatically backed. The runtime emits the event when `lifecycle` transitions to `scoring` via the CEL-expr transition `scenario can become scoring if \`session.hatch_unlocked\``. The payload includes `on_behalf_of: <player>` and `invoked_by: <ARIA-agent-principal>` — the player triggered ARIA's invocation, ARIA wrote `hatch_unlocked = true`, the CEL-expr predicate flipped, the transition fired.

### A.5 Agent directive sourcing

ARIA's directive becomes:

```
Compute called "ARIA":
  ...
  Directive from sessions.aria_system_prompt
```

A separate event-handler compute populates `session.aria_system_prompt` from the deploy-config baseline at session start (lifecycle entering `scenario`). Subsequent ARIA invocations re-read from the field, preserving reproducibility.

### A.6 Channel-subscription cascade

`Anyone with "airlock.session.read" can subscribe to "tool output stream" channel for their own sessions` becomes simply:

```
Anyone with "airlock.session.read" can subscribe to "tool output stream" channel
```

The cascade from the carried content's ownership (messages owned by player_principal) handles the filter automatically. The `for their own sessions` clause in the original sketch is no longer needed — and indeed was never grammatically backed.

---

## Appendix B — Open Questions, Hotly Debated Topics, and Considered-and-Rejected

### Deferred to future versions

All four of the original BRD #3 open questions were resolved as deferred (2026-04-26). They are captured here so they are not lost; none requires further v0.9 work.

**Deferred to v0.10:**

1. **Composite ownership and transitive ownership.** Should a content type be able to declare ownership across multiple fields (e.g., "owned by user_id AND team_id"), or transitively through a foreign key (e.g., "messages owned through session.player_principal")? Currently spec'd at one direct field. Forcing functions: shared access between an individual and a group; denormalization avoidance for related content. **Decision deferred until the Identity story for Teams exists** — composite ownership has nothing to bind to without a Teams concept in the Principal record, so it cannot meaningfully ship before that does.

2. **Directive composition.** Should baseline-plus-overlay composition (`Directive is "<baseline>" with overlay "<overlay>"`) be a v0.9 form? Real production pattern; complicates the runtime resolution path. **Deferred to v0.10.** Forcing function: multiple agent prompts in real deployments needing per-session customization beyond what the field-reference form (§6) covers.

3. **`the agent` source-level symbol.** v0.9 captures the agent's principal in audit trails as `invoked_by` but does not expose a source-level symbol for it. **Deferred to v0.10.** Forcing function: an application where source needs to express "the agent's preferences" or "the agent's scope set" rather than the user's.

**Deferred to v1.0:**

4. **Sub-language escape mechanism.** Sub-languages embedded in source — Directive bodies, Objective bodies, CEL expressions inside opaque-string contexts, future provider-defined sub-languages — need a unified way to interpolate host-language values (Principal fields, content references, etc.) into sub-language strings. v0.9 sidesteps this by relying on the field-reference form (§6) plus a session-prep compute that writes interpolated text to a field at the right moment. **Added to the v1.0 roadmap (`termin-roadmap.md` v1.0 Backlog) as a substantive future BRD topic.** Each provider-defined sub-language will declare its own escape form; the BRD will specify the unified host-language reference grammar.

### Hotly debated topics (resolved)

1. **State-machine event naming convention (§5.2).** **Resolved 2026-04-26: Option A locked** — `<content>.<field>.<state>.<verb>`. State sits in front of verb; reads English-naturally; glob-friendly. The four options considered (A, B, C, D) and their trade-offs are preserved in this BRD's revision history; see clarifications round 1 commentary.

### Considered and rejected (for v0.9)

1. **Field-level confidentiality predicates** (was BRD #3 §5 in clarifications round 1). Existing confidentiality system (`termin-confidentiality-brd.md`, `termin-confidentiality-spec.md`) covers field-level visibility via `confidentiality is "<scope>"`. The Airlock case (tool_calls visible to Evaluator, redacted to Player) is solved by adding `confidentiality is "airlock.session.audit"` to the existing `messages.tool_calls` field declaration. No new BRD #3 spec needed. Multi-scope OR semantics (`visible to "<scope1>" or "<scope2>"`) is a v0.10+ candidate if a forcing function emerges.

2. **Contract dual-mode invocation** (was BRD #3 §8 in clarifications round 1). Considered: allow a contract to declare both `extends` and a `source-verb`, supporting both override-mode and new-verb-mode use sites. Rejected for v0.9 on two grounds:
   - **Two ways to do the same thing.** The audience tenet (Tenet 3) values readable source. A reviewer encountering one component invoked two different ways must mentally unify them; that's friction the spec should not add.
   - **Modifier semantics open question.** When a dual-mode contract is invoked via its `source-verb`, do the base contract's modifiers still apply (e.g., the `with V1 vs V2 breakdown` clause from `presentation-base.metric` still legal on a `Show a score-axis card for X` site)? Both answers have downsides — yes is confusing because the new verb has its own modifier set; no breaks the override-equivalence promise. A clean resolution is unclear.

   Revisit if a forcing function emerges. The Airlock's `score-axis-card` ships as new-verb-mode-only in BRD #2's worked example.

---

*End of BRD #3.*
