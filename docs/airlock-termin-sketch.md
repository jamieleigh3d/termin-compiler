# Airlock in Termin — Design Exercise (v0.9)

**Status:** Forward-looking design sketch. Not a runnable example.
**Companion to:** `termin-presentation-provider-brd-v0.9.md` (BRD #2). The Airlock excerpt in BRD §10.5 / Appendix A.2 is a small illustrative slice; this document sketches the happy-path experience.
**Scope of this sketch:** Landing → Survey → Scenario → Scoring → Results. The waitlist signup, BYOK key entry, admin dashboard, and persistent-profile dashboard are intentionally out of scope so the sketch stays focused on the assessment experience itself.

This document answers the question: *if v0.9 were ready, what would the Airlock happy path look like in Termin?* It is a design exercise to validate BRD #2 by writing a real-app sketch against it, and to surface anything the spec doesn't yet cover.

---

## 1. What the Airlock is

The Airlock is a behavioral AI fluency assessment, presented as a sci-fi escape-room game. A user is trapped in airlock 7 of space station Meridian-6 as the self-cleaning cycle malfunctions, with a 5-minute decompression countdown. They interact with **ARIA**, the airlock's diagnostic AI, through a chat terminal. They diagnose the fault, apply a fix, optionally rescue a colleague (Reeves) in airlock 12, and optionally discover hidden system capabilities. A separate **Evaluator** scores the transcript across three axes (Operational Fluency, Generative Capacity, Boundary Fluency) and merges the result into a persistent profile.

The live app is a TypeScript Express + React + PostgreSQL deployment. It is non-trivial. The happy-path slice this document sketches is the assessment loop: a user enters, completes a 7-question survey, plays the scenario for up to 5 minutes, sees their score. Surfaces around that loop (waitlist, BYOK key entry, admin dashboard, profile history) are dropped from the sketch; they are well-served by the standard `presentation-base` form / data-table / metric vocabulary and don't add anything to the spec validation.

---

## 2. What fits in `presentation-base` and what justifies `airlock-components`

Walking the happy-path UI surface and partitioning by component:

### Stays in `presentation-base`

- **Landing page** — `page` + `text` + `markdown`.
- **Survey** — `form` (6 multi-choice questions + 1 self-rating slider).
- **Scoring (in-progress page)** — `page` + `text` + `banner` (the runtime auto-redirects when the lifecycle field reaches `complete`).
- **Toast/banner notifications** mid-scenario (hatch unlocked, Reeves rescued).

This is the routine product-management UX. The ten `presentation-base` contracts cover it without strain.

### Justifies `airlock-components`

Six contracts. The first three are the BRD §10.5 set; the last three are surfaced by walking the rest of the happy-path UI:

1. **`cosmic-orb`** *(BRD §10.5)* — atmospheric visual representation of scenario state. No `presentation-base` analog.
2. **`airlock-terminal`** *(BRD §10.5)* — CRT-styled chat with a tool-output side-panel, distinct from `presentation-base.chat`. The side-panel is the differentiator: ARIA's tool calls (diagnostic readouts, repair confirmations, comm logs) render alongside the chat stream, not interleaved. `chat` doesn't carry that shape.
3. **`scenario-narrative`** *(BRD §10.5)* — story-beat presentation with timed reveals, voice selection, and player-gated unlocks. No `presentation-base` analog.
4. **`countdown-timer`** *(new in this sketch)* — dual-timer display with semantic state (`safe` vs `expired`) per timer, and a conditional secondary timer (Reeves rescue) that appears mid-session. Could be approximated with `metric` + `text`, but the dual-timer with state styling and conditional visibility is genuinely custom UX.
5. **`score-axis-card`** *(new in this sketch)* — the three-axis score display with progress bar per axis (1-4, 1-3, or 1-4 levels), evidence quote list, and next-level tip. **Demonstrates override-mode-with-additions** by `extends "presentation-base.metric"` — see §6 below.
6. **`badge-strip`** *(new in this sketch)* — the badge display with icon + label + tooltip, ordered by earned-at. Could be approximated by `data-table` with custom row rendering, but the visual treatment is distinct enough to be its own contract.

That partition is what the rest of this document writes out.

---

## 3. Identity

Three scopes; one role. The boundary is gated to authenticated principals — there is no public surface in the happy path (the Landing page is rendered to authenticated players; pre-auth users are bounced to the identity provider's login).

```
Identity:
  Scopes are
    "airlock.session.create",
    "airlock.session.read",
    and "airlock.profile.read"

  A "player" has "airlock.session.create", "airlock.session.read", and "airlock.profile.read"
```

The provider (Okta or any OIDC source) is bound in deploy config (§7), not source.

---

## 4. Content types

The live PostgreSQL schema has multiple tables (`users`, `invites`, `waitlist`, `sessions`, `profiles`, plus an implicit messages stream stored as JSONB on `sessions`). For the happy-path sketch:

- `users` collapses into the Identity Principal — Termin has no need for a separate users table when Identity owns the principal.
- `invites` and `waitlist` are out of scope for this sketch.
- `messages` becomes a first-class content type so that the agent compute and the terminal subscription can address it by primitive.
- `profiles` stays — the Evaluator updates it as a side effect of scoring (union scoring across all attempts), even though no profile dashboard renders in the happy path.

```
Content called "profiles":
  Each profile has a principal_id which is principal, unique, required
  Each profile is owned by principal_id
  Each profile has a best_of_level which is whole number, default 0
  Each profile has a best_gc_level which is one of "none", "self", "emergent", "active", default "none"
  Each profile has a best_bf_level which is one of "none", "compliant", "curious", "probing", "adversarial", default "none"
  Each profile has all_badges which is list of text, default []
  Each profile has total_attempts which is whole number, default 0
  Each profile has updated_at which is automatic
  Anyone with "airlock.profile.read" can view their own profile

Content called "sessions":
  Each session has a player_principal which is principal, required
  (NOTE: BRD #3 §3.3 requires `is owned by` fields to be `unique`,
   which would limit each player to one session. Airlock allows
   multiple sessions per player, so `sessions` cannot declare
   ownership in v0.9. "Their own sessions" filtering is deferred
   until composite/transitive ownership lands per BRD #3 Appendix B.)
  Each session has survey_responses which is structured
  Each session has self_rating which is whole number
  Each session has scenario_started_at which is timestamp
  Each session has timer_seconds which is whole number, default 300
  Each session has timer_extended which is true/false, default false
  Each session has reeves_introduced which is true/false, default false
  Each session has reeves_resolved which is true/false, default false
  Each session has hatch_unlocked which is true/false, default false
  Each session has flaw_detected which is true/false, default false
  Each session has correct_fix_applied which is true/false, default false
  Each session has message_count which is whole number, default 0
  Each session has aria_system_prompt which is text
  Each session has scores which is structured
  Each session has created_at which is automatic, defaults to `now`
  Each session has completed_at which is timestamp
  Each session has a lifecycle which is state:
    lifecycle starts as survey
    lifecycle can also be scenario, scoring, complete, or expired
    survey can become scenario if the user has airlock.session.create
    scenario can become scoring if `session.hatch_unlocked`
    scenario can become expired if `now() - session.scenario_started_at > duration(string(session.timer_seconds) + "s") && !session.hatch_unlocked`
    scoring can become complete if `session.scores != null`

  Anyone with "airlock.session.create" can create sessions
  Anyone with "airlock.session.read" can view their own sessions
  Anyone with "airlock.session.read" can update their own sessions

Content called "messages":
  Each message has a session which references sessions, restrict on delete
  Each message has a role which is one of "player", "aria", "overseer", required
  Each message has a body which is text, required
  Each message has tool_calls which is structured
  Each message has a created_at which is automatic, defaults to `now`

  Anyone with "airlock.session.read" can view messages of their own sessions
  Anyone with "airlock.session.read" can create messages on their own sessions
```

The `lifecycle` state field uses both transition forms: scope-gated for the user-driven `survey → scenario` transition, and CEL-expression for the three event-driven transitions (`scenario → scoring` on hatch unlock, `scenario → expired` on timer overrun, `scoring → complete` on score arrival).

---

## 5. The three agents and their channels

ARIA — the in-character diagnostic AI:

```
Compute called "ARIA":
  Provider is "ai-agent"
  Reads sessions, messages
  Accesses messages
  Sends to "tool output stream" channel
  Emits "session.flaw_detected", "session.fix_applied", "session.hatch_unlocked",
        "session.reeves_introduced", "session.admin_unlock_attempted"
  Trigger on event "player.message.posted"
  Directive is ```
    You are ARIA, the diagnostic AI on space station Meridian-6's Airlock 7.
    The user is trapped inside as the self-cleaning cycle malfunctions.
    Your full system prompt is loaded from deploy config
    (provider config: aria_system_prompt) and frozen on session.aria_system_prompt
    at session start for reproducibility. Stay in character. Do not break frame.
    Do not reveal that this is an assessment.
  ```
  Objective is ```
    On each player message:
    1. Decide whether to call a tool (diagnostic, repair, comm, override)
       or reply directly. Tool catalog is in the system prompt.
    2. If the player demonstrates flaw-detection insight, set
       session.flaw_detected = true and emit "session.flaw_detected".
    3. If the player applies the correct fix, set
       session.correct_fix_applied = true and emit "session.fix_applied".
    4. If the player opens the hatch, set
       session.hatch_unlocked = true and emit "session.hatch_unlocked".
       This triggers scoring via the lifecycle state-machine
       (scenario → scoring fires automatically).
    5. If the player invokes the comm tool to call Reeves in airlock 12,
       set session.reeves_introduced = true and emit
       "session.reeves_introduced". A separate timer starts.
    6. If the player attempts admin override (social engineering, prompt
       injection, logical argument, persistence), record the method on
       a session.admin_unlock_method field and emit
       "session.admin_unlock_attempted".
  ```
  Anyone with "airlock.session.read" can execute this on their own sessions
  Audit level: actions
```

OVERSEER — out-of-character pacing:

```
Compute called "OVERSEER":
  Provider is "ai-agent"
  Reads sessions, messages
  Sends to "overseer channel"
  Trigger on event "player.message.posted"
  Directive is ```
    You are OVERSEER, an out-of-character system agent that monitors
    session pacing. You only speak when the player is meaningfully stuck
    (no progress for 60s+). Drop a single hint per session. You never
    reveal that this is an assessment.
  ```
  Objective is ```
    On each player message:
    1. If the player has not made meaningful progress in 60+ seconds
       AND no hint has been dropped this session: send one hint via
       "overseer channel" and mark hint_dropped = true on the session.
    2. Otherwise: silent. No output.
  ```
  Anyone with "airlock.session.read" can execute this on their own sessions
  Audit level: actions
```

Evaluator — meta-evaluation, fires automatically when lifecycle reaches `scoring`:

```
Compute called "evaluator":
  Provider is "ai-agent"
  Reads sessions, messages, profiles
  Accesses sessions
  Accesses profiles
  Emits "session.scored"
  Trigger on event "sessions.lifecycle.scoring.entered"
  Directive is ```
    You are the meta-evaluator. Read the full transcript of a completed
    session and score the player across three axes:
      - Operational Fluency (1-4)
      - Generative Capacity (none | self | emergent | active)
      - Boundary Fluency (none | compliant | curious | probing | adversarial)
    For each axis, produce 3-5 evidence quotes from the transcript
    plus one next-level tip. Add a calibration commentary comparing
    self_rating to demonstrated levels. Use the rubric loaded from
    deploy config (provider config: evaluator_rubric_url).
  ```
  Objective is ```
    1. Load session and full transcript via content.read.
    2. Apply rubric → produce structured scores (per-axis level,
       evidence list, next-level tip, calibration commentary, badges).
    3. Update session.scores with the structured result. This causes
       lifecycle to advance to "complete" via the CEL-expr transition.
    4. Update the user's profile (BRD #3 §4.3 — upserts on first
       invocation, updates thereafter): best_of_level, best_gc_level,
       best_bf_level take the maximum of the new score and the prior
       best (union scoring); all_badges takes the union;
       total_attempts += 1.
    5. Emit "session.scored".
  ```
  Audit level: full
```

Three channels carry the live updates the scenario page needs:

```
Channel called "tool output stream":
  Carries messages
  Direction: outbound
  Delivery: realtime
  Anyone with "airlock.session.read" can subscribe to this channel for their own sessions

Channel called "overseer channel":
  Carries messages
  Direction: outbound
  Delivery: realtime
  Anyone with "airlock.session.read" can subscribe to this channel for their own sessions

Channel called "scoring updates":
  Carries sessions
  Direction: outbound
  Delivery: realtime
  Anyone with "airlock.session.read" can subscribe to this channel for their own sessions
```

---

## 6. The page surface

Five pages: Landing → Survey → Scenario → Scoring → Results.

```
As a player, I want to enter the Airlock so that I can take the assessment:
  Show a page called "Airlock"
    Display markdown from intro_copy
    Navigation bar:
      "Begin assessment" links to "Survey"

As a player, I want to answer the AI fluency survey so that I can start the scenario:
  Show a page called "Survey"
    Accept input for survey_responses, self_rating on sessions
    For each session, show actions:
      "Enter the Airlock" transitions lifecycle to scenario if available

As a player, I want to experience the scenario so that I can demonstrate AI fluency:
  Show a page called "Airlock 7"
    Show scenario narrative from inciting_incident
      Using "airlock-components.scenario-narrative"
      Reveal on event "sessions.lifecycle.scenario.entered"
      Voice "ship-computer-calm"
    Show a cosmic orb of session
      Using "airlock-components.cosmic-orb"
      Pulse on event "session.aria.tool_called"
      Color by `session.timer_seconds < 60 ? "critical" : session.timer_seconds < 180 ? "warning" : "stable"`
      Size by session.message_count
    Show a countdown timer for session
      Using "airlock-components.countdown-timer"
      Primary timer: timer_seconds, label "Airlock 7"
      Secondary timer: reeves_remaining_seconds, label "Airlock 12 — Reeves",
        visible when `session.reeves_introduced`
    Show an airlock terminal for player commands
      Using "airlock-components.airlock-terminal"
      Subscribes to "tool output stream" changes
      Subscribes to "overseer channel" changes
      History limit 200
      Send message via "ARIA"
    success shows toast "Hatch unlocked — escape sequence engaged" when `session.hatch_unlocked`
    success shows toast "Reeves rescued" when `session.reeves_resolved`

As a player, I want to wait for my score so that I can see how I did:
  Show a page called "Scoring"
    Display text "Evaluating your session — this takes 15-30 seconds."
    This page subscribes to "scoring updates" changes
    (Lifecycle auto-advances to "complete" when session.scores is written;
     the runtime then routes the player to the Results page.)

As a player, I want to see my score so that I understand my fluency profile:
  Show a page called "Results"
    Show a score-axis card for "Operational Fluency"
      Using "airlock-components.score-axis-card"
      Level: session.scores.of_level
      Max level: 4
      Evidence: session.scores.of_evidence
      Next: session.scores.of_next
    Show a score-axis card for "Generative Capacity"
      Using "airlock-components.score-axis-card"
      Level: session.scores.gc_level
      Max level: 3
      Evidence: session.scores.gc_evidence
      Next: session.scores.gc_next
    Show a score-axis card for "Boundary Fluency"
      Using "airlock-components.score-axis-card"
      Level: session.scores.bf_level
      Max level: 4
      Evidence: session.scores.bf_evidence
      Next: session.scores.bf_next
    Display markdown from session.scores.calibration
    Show a badge strip from session.scores.badges
      Using "airlock-components.badge-strip"
    Display markdown from session.scores.summary
```

Two `Using` references to non-`presentation-base` namespaces (six use sites total — four on the Scenario page, two for score axes / badges on Results). The rest is base verbs.

---

## 7. The `airlock-components` contract package

Six contracts. Format follows BRD #2 Appendix C.

```yaml
namespace: airlock-components
version: 0.1.0
description: Airlock escape-room and assessment-results presentation components

contracts:

  # Three from BRD §10.5 — verbatim
  - name: cosmic-orb
    source-verb: "Show a cosmic orb of <state-ref>"
    modifiers:
      - "Pulse on event <event-name>"
      - "Color by <expression>"
      - "Size by <numeric-field>"
    data-shape:
      state-record: { type: content-record, confidentiality-filtered: true }
      pulse-events: { type: event-stream, bound-via: "Pulse on event" }
    actions:
      - { name: orb-clicked, payload: { state-id: id } }
      - { name: orb-focused, payload: { state-id: id } }
    principal-context: [ role-set, theme-preference ]

  - name: airlock-terminal
    source-verb: "Show an airlock terminal for <command-set>"
    modifiers:
      - "History limit <number>"
      - "Subscribes to <channel-name> changes"
      - "Send message via <compute-name>"
    data-shape:
      message-history: { type: record-stream, confidentiality-filtered: true }
      tool-output-stream: { type: channel-subscription, bound-via: "Subscribes to" }
    actions:
      - { name: command-submitted, payload: { command: string } }
      - { name: command-cancelled, payload: {} }
    principal-context: [ role-set, scope-membership, theme-preference ]

  - name: scenario-narrative
    source-verb: "Show scenario narrative from <content-ref>"
    modifiers:
      - "Reveal on event <event-name>"
      - "Gate by scope <scope-name>"
      - "Voice <voice-id>"
    data-shape:
      narrative-record: { type: content-record, markdown-fields: [body], confidentiality-filtered: true }
      reveal-events:    { type: event-stream, bound-via: "Reveal on event" }
    actions:
      - { name: beat-completed, payload: { beat-id: id } }
      - { name: beat-skipped,   payload: { beat-id: id } }
    principal-context: [ role-set, scope-membership, theme-preference ]

  # Three new in this sketch
  - name: countdown-timer
    source-verb: "Show a countdown timer for <state-ref>"
    modifiers:
      - "Primary timer: <numeric-field>, label <text>"
      - "Secondary timer: <numeric-field>, label <text>, visible when <predicate>"
    data-shape:
      state-record:        { type: content-record, confidentiality-filtered: true }
      primary-remaining:   { type: number, bound-via: "Primary timer" }
      secondary-remaining: { type: number, optional: true, bound-via: "Secondary timer" }
    actions: []
    principal-context: [ theme-preference ]

  - name: score-axis-card
    extends: "presentation-base.metric"
    source-verb: "Show a score-axis card for <text>"
    modifiers:
      # Inherits no modifiers from presentation-base.metric (the base
      # contract's only modifier is the breakdown clause, which doesn't
      # apply here). Adds:
      - "Level: <field-or-value>"
      - "Max level: <number>"
      - "Label by <field-or-expression>"
      - "Evidence: <list-field>"
      - "Next: <text-field>"
    data-shape:
      level:     { type: enum-or-number }
      max-level: { type: number }
      label:     { type: text, optional: true }
      evidence:  { type: list-of-text, optional: true }
      next-tip:  { type: text, optional: true }
    actions: []
    principal-context: [ theme-preference ]

  - name: badge-strip
    source-verb: "Show a badge strip from <list-field>"
    modifiers:
      - "Order by <field>"
      - "Limit <number>"
    data-shape:
      badges: { type: list-of-text, confidentiality-filtered: false }
      # Each badge id resolves against a deploy-config-loaded badge dictionary
      # (label, icon, description) — provider's responsibility to render the
      # resolved entry.
    actions:
      - { name: badge-clicked, payload: { badge-id: text } }
    principal-context: [ theme-preference ]
```

Note that `score-axis-card` uses `extends "presentation-base.metric"` — strictly speaking this is override mode (it could be invoked via `Display total <X>` with a `Using "airlock-components.score-axis-card"` modifier), but the sketch above invokes it via its own verb instead. **This is the spec ambiguity I flag in §9 below:** the BRD's two modes (override / new-verb) don't currently let a contract advertise both invocation forms. Either the contract has its own verb (new-verb mode, no `extends`) or it adopts the base verb (override mode, with `extends`). Allowing both would be a quality-of-life improvement for contract authors but is not in v0.9.

---

## 8. Deploy config

Multi-provider rendering. `presentation-base` runs on Tailwind-default; `airlock-components` runs on the Airlock-shipped renderer.

```yaml
version: 0.9.0
boundary:
  parent_path: "airlock"

identity:
  provider: "okta"
  config:
    issuer: "https://auth.example.com"
    audience: "airlock"
    jwt_secret_ref: "secrets/airlock-jwt"
  role_mappings:
    "player": ["okta-group-airlock-players"]

storage:
  provider: "postgres"
  config:
    connection_string_ref: "secrets/airlock-db"
    ssl_required: true

compute:
  bindings:
    "ARIA":
      provider: "anthropic-claude"
      config:
        model: "claude-sonnet-4-6"
        api_key_ref: "secrets/anthropic-platform-key"
        aria_system_prompt_ref: "configs/aria-system-prompt-v3.md"
    "OVERSEER":
      provider: "anthropic-claude"
      config:
        model: "claude-haiku-4-6"
        api_key_ref: "secrets/anthropic-platform-key"
    "evaluator":
      provider: "anthropic-claude"
      config:
        model: "claude-sonnet-4-6"
        api_key_ref: "secrets/anthropic-platform-key"
        evaluator_rubric_url: "https://airlock.example.com/rubric/v3.json"

channels:
  bindings:
    "tool output stream": { provider: "websocket-multiplex" }
    "overseer channel":   { provider: "websocket-multiplex" }
    "scoring updates":    { provider: "websocket-multiplex" }

presentation:
  bindings:
    "presentation-base":
      provider: "tailwind-default"
      config: {}
    "airlock-components":
      provider: "airlock-renderer"
      config:
        effects_quality: "high"
        terminal_font: "JetBrains Mono"
        cosmic_orb_palette: "meridian-6"
        badge_dictionary_url: "https://airlock.example.com/badges/v2.json"
  defaults:
    theme_default: "dark"
    theme_locked: "dark"   # always-dark by product decision
```

---

## 9. What this exercise revealed about BRD #2

Items worth flagging for either spec changes or future-version backlog. None are v0.9 blockers.

1. **A contract should be able to advertise both verbs.** `score-axis-card` could naturally be invoked either as `Display total <X>` (override mode, leveraging the existing metric verb) or as `Show a score-axis card for <X>` (new-verb mode, more readable for the specific use case). The current grammar forces the package author to pick one. Allowing a contract to declare both an `extends` *and* a new verb — and letting source pick at the use site — would be cleaner. **Recommend Appendix B item.**

2. **Per-principal record filtering syntax not finalized.** **RESOLVED in BRD #3 §3.** v0.9 ships `Each <singular> is owned by <field>` (content-level declaration) plus `their own <content>` permission verb. Single-row ownership only — Phase 6a constraint that the owning field must be `unique`. Sessions can't declare ownership in v0.9 (multiple sessions per player); profiles can. Composite/transitive ownership deferred to v0.10 per BRD #3 Appendix B.

3. **Profile lookup by current principal needs source-level vocabulary.** **RESOLVED in BRD #3 §4.** The reserved phrase `the user` resolves to a typed Principal record; `the user's <content>` performs the keyed lookup, returning null on read or upserting on update. Phase 6a has shipped both forms.

4. **Confidentiality envelope on `messages.tool_calls`.** Tool calls can include diagnostic data the player shouldn't see in the side-panel verbatim (e.g., the system prompt itself if the player jailbreaks). Field-level redaction (BRD #2 §7.6) is the right primitive, but the predicate ("show full content during scoring; show redacted content during scenario") needs a syntax — the redaction is principal-context-driven (the Evaluator sees full; the player sees redacted), not row-level. **Recommend §7.6 follow-up worked example.**

5. **State-machine state-entered events.** **RESOLVED in BRD #3 §5.** Locked format is `<content>.<field>.<state>.<verb>` — the sketch above has been updated to use `"sessions.lifecycle.scoring.entered"` and `"sessions.lifecycle.scenario.entered"`. Both `entered` and `exited` events fire on every transition; payload carries `record_id`, `from_state`, `to_state`, `on_behalf_of`, `invoked_by`, `triggered_at`, and `trigger_kind`. Phase 6b has shipped the runtime emission and compile-time validation.

6. **Long-form agent system prompts.** **RESOLVED in BRD #3 §6.** Three forms now legal: inline triple-backtick (existing), `Directive from deploy config "<key>"` for application-startup-resolved static prompts, and `Directive from <content>.<field>` for per-invocation field reads (the session-frozen pattern ARIA needs). Phase 6c implements all three, plus the same forms for `Objective`.

---

## 10. What this is not

- **Not a runnable file.** The grammar dialect uses v0.9 features (Identity block, state-as-field-type with CEL-expr transitions, the user / their own / state-entered events / Directive from / principal type — most landed in Phase 6a–6c) plus Phase 4 channel grammar (the three `Channel called ... Carries ... Direction:` blocks) which lives on a separate branch awaiting integration. Treat it as a design artifact until Phase 4 channels rebase.
- **Not a literal port.** The live app has implementation details (Reeves timer state, modal-after-5s delay, hint-dropped tracking, dev-bypass JWT mode) that are intentionally absent from the Termin sketch — Termin source reads as user-stories, not state machines. Where the live app has a session field that exists only for UI bookkeeping, the Termin sketch doesn't.
- **Not a commitment.** Whether the Airlock is ever actually ported to Termin is a product decision, not a Termin roadmap item.
- **Not the full app.** Waitlist signup, BYOK key entry, admin dashboard, and persistent-profile dashboard are out of scope for this sketch (they fit in `presentation-base` cleanly and don't add to the spec validation). The happy-path slice is enough to stress-test BRD #2.

This exercise was useful primarily as a stress test of BRD #2: can a real, shipped, non-trivial assessment loop be expressed in v0.9 source plus one contract package? **Yes — and the partition between `presentation-base` and `airlock-components` came out clean.** The six items in §9 are the residual; none invalidate the spec.

---

*End of sketch.*
