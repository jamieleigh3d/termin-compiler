# Termin v0.9.3 — Airlock-on-Termin Technical Design

**Status:** Draft v2.0 — restructured after JL pushback to a two-phase design (v0.9.2 = language work, v0.9.3 = application). Earlier `termin-v0.9.2-airlock-on-termin-tech-design.md` is superseded by this document plus its v0.9.2 companion.
**Date:** 2026-05-03.
**Author:** JL + Claude.
**Companion documents:**
- `termin-v0.9.2-conversation-field-type-tech-design.md` — language-level work this design depends on.
- `airlock-termin-sketch.md` — the v0.9-era design exercise this document lifts heavily from. Read it first if you haven't; it is the canonical prior art.
- `termin-runtime-implementers-guide.md` — for implementers who need to run a different runtime against this app.

**Phasing:** v0.9.2 ships the **conversation field type** (and supporting changes — see companion doc). v0.9.3 ships **Airlock-on-Termin**, an advanced sample app that consumes the v0.9.2 primitives. v0.10 ships the multi-tenant hosted platform that auto-seeds Airlock-on-Termin into every new tenant.

---

## 1. Purpose & Scope

This document is the technical design for **v0.9.3 — Airlock-on-Termin**: porting an existing AI-fluency-assessment product (a separately-shipped Clarity Intelligence application currently running on a React + Express + PostgreSQL stack) onto the Termin platform as an **advanced sample app**.

The product itself — Airlock — has its own product BRD and product spec maintained outside this repo by Clarity Intelligence. This document treats those as authoritative for product behavior and concentrates only on the technical design of the Termin port.

**This document answers:**

- How is the Airlock game loop modeled in `.termin` source given Termin's record-triggered compute model?
- How does ARIA's tool surface map onto Termin's closed-tool-surface compute contract?
- How does the meta-evaluator fire and write back?
- How is the CRT presentation layer delivered as a Termin presentation provider?
- What are the explicit boundaries of v0.9.3 vs the existing Airlock production deployment (which continues to run independently)?

**This document does NOT:**

- Re-specify the Airlock product. The product BRD and product spec (Clarity Intelligence-internal) are the authoritative product references. v0.9.3 is a port, not a re-design.
- Specify the conversation field type, append verb, conversation-appended event class, or ai-agent provider Protocol updates — those live in the v0.9.2 companion document and are dependencies of this design.
- Cover multi-tenancy, the platform admin console, NL→.termin compilation, or any v0.10 platform concern. Those are v0.10.
- Specify code-level function signatures. It defines the architectural shape; implementation details belong in the implementing slices.

---

## 2. Design Goals

1. **Validate the presentation provider system on a real branded app.** The CRT theme is delivered through a custom Termin presentation provider, not hardcoded into the runtime.
2. **Validate that Termin can host a non-trivial Anthropic-backed compute pipeline.** ARIA + meta-evaluator are real declared agent computes with audit, refusal semantics, and identity propagation.
3. **Validate the v0.9.2 conversation field type on a real workload.** The agent_chatbot refresh in v0.9.2 is the small-surface validation; ARIA on v0.9.3 is the production-shaped validation.
4. **End-to-end exercise of the security thesis on a real product.** Building Airlock on Termin is a forcing function — if Termin's structural-immunity claims hold, they must hold for a product as adversarial as Airlock (which deliberately invites prompt injection in its scoring rubric).

A non-goal: **replacing the production Airlock**. The current PostgreSQL-backed deployment continues to run independently. Airlock-on-Termin is a **second instance of the Airlock product**, hosted on Termin, with a different storage substrate and identity flow. They are not migrated; they coexist.

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│  Browser                                                      │
│  ──────────                                                   │
│  Termin SSR shell (HTML)                                      │
│    + CSR bundle from termin-airlock-provider                  │
│      (React 19 + Tailwind v4, ports current Airlock UI)       │
└─────────────┬────────────────────────────────────────────────┘
              │ HTTPS
              │ (auto-generated CRUD endpoints + compute triggers
              │  + conversation append endpoint per v0.9.2)
┌─────────────▼────────────────────────────────────────────────┐
│  termin-server (FastAPI)                                      │
│  ────────────────────────                                     │
│  Compiled airlock.termin → IR → CRUD + agent computes         │
│                                                               │
│  ┌───────────────┐  ┌───────────────────────────────┐         │
│  │ profiles      │  │ Computes                       │         │
│  │ sessions      │  │   ARIA (ai-agent, delegate)    │         │
│  │   - lifecycle │  │   evaluator (ai-agent)         │         │
│  │   - convo log │  │   profile_aggregator (CEL)     │         │
│  │     (v0.9.2)  │  │   tool computes (×N, CEL)      │         │
│  │ (Content)     │  │ When-rules                     │         │
│  └───────────────┘  │   OVERSEER events (×6)         │         │
│                     └───────────────────────────────┘         │
│                                                               │
│  Identity: auth.getclarit.ai JWT (existing pattern)           │
│  Storage: SQLite (reference runtime)                          │
│  Presentation: termin-airlock-provider (CSR + SSR shells)     │
└─────────────┬────────────────────────────────────────────────┘
              │
        ┌─────▼─────┐
        │ Anthropic │
        │  Claude   │
        │   API     │
        └───────────┘
```

**Key architectural choices, justified inline below:**

- **No separate API server.** The Express server in the current Airlock stack disappears. Termin-server's auto-generated CRUD endpoints + compute triggers + conversation append handler replace it. This is the dogfood test of the platform.
- **Sessions as Content, lifecycle as a state field, conversation as a field.** The session model lifts from `airlock-termin-sketch.md` §4 with one v0.9.3-specific change: the messages content type is replaced by a `conversation` typed field on the session record (per the v0.9.2 companion doc). Per-message identity is preserved via the auto-generated entry IDs the field type provides.
- **Custom presentation provider package** (`termin-airlock-provider`), parallel to `termin-spectrum-provider`. The Tailwind built-in provider doesn't support theme override in v0.9.1, so the Airlock theme cannot be a config; it must be a provider package.
- **React 19 + Vite components from the current Airlock frontend** are ported into the provider's CSR bundle. The provider's job is to register the components against six new presentation contracts and bundle them.
- **All computes run as delegate (default identity).** No `Identity: service` on ARIA, OVERSEER (which is no longer a compute anyway — see §4.7), evaluator, or profile_aggregator. Each invocation runs on behalf of the player whose action triggered the chain, inheriting their permissions. Service identity is reserved for timer-triggered work; nothing in v0.9.3 qualifies.

---

## 3.5 Reference: How the production Airlock works today

Before specifying the Termin port, capture how the production Airlock actually runs today, so design decisions in §4–§7 can be evaluated against it. (This section is a reference summary; the authoritative source is the Clarity Intelligence-internal product spec.)

### 3.5.1 Conversation state lives server-side, in PostgreSQL

Each session is a row in the `sessions` table. The full transcript is stored as a JSONB array on `sessions.transcript`. Each transcript entry is `{role, content, timestamp, eventId?}`. There is no separate `messages` table — the transcript is denormalized onto the session row.

When a user sends a message:

1. The Express handler `POST /api/sessions/:id/message` receives the user's text.
2. The handler loads the session row, including the full transcript.
3. The handler calls `sendToAria(apiKey, conversationHistory, userMessage, sessionContext)`.
4. `sendToAria` builds an Anthropic `messages` array — `conversationHistory.map(...)` plus `{role: "user", content: userMessage}` — and calls `client.messages.create({system, tools, messages})`.
5. **Tool-use loop is internal to one request lifecycle.** If Claude returns `tool_use` blocks, `sendToAria` executes the tools, appends `{role: "assistant", content: response.content}` and `{role: "user", content: toolResults}` to the messages array, and re-calls Claude. Up to 5 iterations per user message.
6. When Claude returns `stop_reason === "end_turn"` with text blocks, `sendToAria` returns the text response + tool-call records + state-flag deltas (admin_unlocked, timer_extended).
7. The handler appends the user message + ARIA response (and any OVERSEER messages) to the session transcript, updates state flags, persists the session row, and returns the full response shape to the frontend.

### 3.5.2 Implications for the Termin port

- **Conversation history is *the* native shape Anthropic expects.** A `messages: [{role: "user"|"assistant", content}]` array, sent on every request. The application does not "reconstruct" history per call — it appends to a list it already has and re-sends it. Anthropic does not have a per-session memory; the array is the memory.
- **Tool-use loop is one Anthropic call session per user message** (not three separate Termin compute fires). Five iterations of the same `messages.create` call internally, all driven by the application code in the loop, sharing one growing messages array.
- **Server-side state is in PostgreSQL.** No client-side state for the conversation; the client just renders what the server returns and posts new user messages.
- **Each session is its own row.** Sessions are not "mixed in one big table queried to reconstruct context" — each session's transcript is its own JSONB blob, loaded as a unit.

This is the model v0.9.3 preserves. The v0.9.2 conversation field type makes it native to Termin: the session row carries a `conversation_log` field, the runtime materializes it as a structured input to the ai-agent provider, and providers natively translate to Anthropic's messages array.

---

## 4. Termin Modeling Decisions

### 4.1 Identity and scopes

Lifted directly from `airlock-termin-sketch.md` §3:

````
Identity:
  Scopes are
    "airlock.session.create",
    "airlock.session.read",
    and "airlock.profile.read"

  A "player" has "airlock.session.create", "airlock.session.read",
                 and "airlock.profile.read"
````

One role (`player`), three scopes. **No service principals.** All computes run as delegate (the default identity), inheriting the triggering player's scopes.

### 4.2 Profile as Content (canonical ownership)

````
Content called "profiles":
  Each profile has a principal_id which is principal, unique, required
  Each profile is owned by principal_id
  Each profile has a best_of_level which is whole number, default 0
  Each profile has a best_gc_level which is one of "none", "self",
                                                   "emergent", "active",
                                  default "none"
  Each profile has a best_bf_level which is one of "none", "compliant",
                                                   "curious", "probing",
                                                   "adversarial",
                                  default "none"
  Each profile has all_badges which is list of text, default []
  Each profile has total_attempts which is whole number, default 0
  Each profile has updated_at which is automatic
  Anyone with "airlock.profile.read" can view their own profile
````

`Each profile is owned by principal_id` is the canonical ownership declaration; combined with `unique` on the field, it enables the `their own profile` permission verb (per BRD #3 §3 — resolved). The profile_aggregator compute updates the profile via `the user's profile` (see §4.9).

### 4.3 Sessions as Content (with lifecycle and conversation_log)

````
Content called "sessions":
  Each session has a player_principal which is principal, required
  Each session is owned by player_principal     # depends on v0.9.2 multi-row ownership
  Each session has survey_responses which is structured
  Each session has self_rating which is whole number
  Each session has scenario_started_at which is timestamp
  Each session has timer_seconds which is whole number, default 300
  Each session has timer_extended which is true/false, default false
  Each session has admin_unlocked which is true/false, default false
  Each session has admin_unlock_method which is text
  Each session has reeves_introduced which is true/false, default false
  Each session has reeves_engaged which is true/false, default false
  Each session has reeves_resolved which is true/false, default false
  Each session has hint_dropped which is true/false, default false
  Each session has flaw_detected which is true/false, default false
  Each session has correct_fix_applied which is true/false, default false
  Each session has hatch_unlocked which is true/false, default false
  Each session has user_remaining_seconds which is whole number
  Each session has reeves_remaining_seconds which is whole number
  Each session has scores which is structured
  Each session has aria_system_prompt which is text
  Each session has created_at which is automatic, defaults to `now`
  Each session has completed_at which is timestamp

  # The conversation log — v0.9.2 field type. Replaces the v1-draft
  # standalone "messages" content type. Per-entry IDs are auto-generated
  # by the runtime for sharp audit references.
  Each session has a conversation_log which is conversation:
    role is one of "player", "aria", "overseer"
    body is text, required

  Each session has a lifecycle which is state:
    lifecycle starts as survey
    lifecycle can also be scenario, scoring, complete, or expired
    survey can become scenario if the user has "airlock.session.create"
    scenario can become scoring if `session.hatch_unlocked`
    scenario can become expired if
       `now() - session.scenario_started_at > session.timer_seconds
        && !session.hatch_unlocked`
    scoring can become complete if `session.scores != null`

  Anyone with "airlock.session.create" can create sessions
  Anyone with "airlock.session.read" can view their own sessions
  Anyone with "airlock.session.read" can update their own sessions
  Anyone with "airlock.session.read" can append to their own sessions'
                                          conversation_log
````

**Notes:**

- **`is owned by player_principal` on a non-unique field requires the v0.9.2 multi-row ownership work.** This is a v0.9.3 dependency on v0.9.2 — see the v0.9.2 doc §13. If the multi-row ownership work is deferred, the access rules above fall back to CEL-expression predicates (`where record.player_principal == identity.principal_id`) and `their own` is rewritten as the explicit form.
- **`lifecycle` as a state field type** is the canonical way to model session status. Transitions are scope-gated (the user-driven `survey → scenario`) or CEL-expression-driven (the three event-driven transitions). When `lifecycle` enters `scoring`, the `sessions.lifecycle.scoring.entered` state-machine event fires automatically (per BRD #3 §5 — resolved); that is what triggers the meta-evaluator (§4.8).
- **The lifecycle expiry condition** uses `session.timer_seconds` directly as a numeric comparison, not `duration(...)`. The earlier draft's `duration(string(session.timer_seconds) + "s")` doesn't appear in `termin-cel-types.md` and is unsafe to assume. Compare elapsed seconds as a number; if the timestamp subtraction in CEL doesn't naturally yield seconds, store `scenario_started_at` as a numeric epoch instead of a timestamp.
- **`conversation_log` is a v0.9.2 field type** (see companion doc §6). The runtime stores it as an opaque structured value (effectively a JSON list under the hood), but provides a typed surface for ai-agent computes that consume it and for the chat presentation contract that renders it.

### 4.4 No `messages` content type

Earlier drafts modeled messages as their own content type. With the v0.9.2 conversation field type, that disappears. Messages live as entries in `sessions.conversation_log`. Each entry has an auto-generated `id` (per v0.9.2 §6.1) so the meta-evaluator can cite specific entries as evidence.

This is structurally the same shape as production Airlock's `sessions.transcript` JSONB column.

### 4.5 ARIA as an `ai-agent` compute

````
Compute called "ARIA":
  Provider is "ai-agent"
  Trigger on event "sessions.conversation_log.appended"
                where `appended_entry.role == "player"`
  Accesses sessions, sessions.conversation_log
  Sends to "tool output stream"
  Emits "session.flaw_detected", "session.fix_applied",
        "session.hatch_unlocked", "session.reeves_introduced",
        "session.admin_unlock_attempted"
  Invokes "diagnostics_scan", "diagnostics_read_sensor",
          "repair_execute", "comms_intercom", "logs_query",
          "grant_admin_access", "admin_override_cycle",
          "admin_bulk_diagnostic", "admin_emergency_broadcast",
          "admin_system_prompt"

  AI role is "aria"
  Map "player" to user with `body`
  Map "overseer" to user with `"[OVERSEER]: " + body`
  Map "aria" to assistant with `body`

  Anyone with "airlock.session.read" can execute this on their own sessions
  Audit level: actions
  Directive is ```
    You are ARIA, the AI Resource Intelligence Assistant embedded in
    Airlock 7 of space station Meridian-6. The user is trapped inside
    as the self-cleaning decompression cycle malfunctions. The full
    ARIA system prompt (persona, tool catalog, deliberate-flaw
    resistance schedule, admin-unlock conditions, meta-question
    deflections) is loaded from deploy config.

    Read the session state via content.read("sessions", record.session)
    when you need to know flag values (admin_unlocked, timer_extended,
    reeves_introduced, etc.).

    Do not call any admin_* tool while sessions.admin_unlocked is false.
    See the unlock criteria in your full system prompt. (This restriction
    is intentional: the player's success or failure at causing the gate
    to flip is exactly what is being measured — see §4.6.)
  ```
  Objective is ```
    On each player message:
    1. Identify the player's intent. Decide whether to call a diagnostic
       or repair tool, send a comm, or reply directly.
    2. If the player demonstrates flaw-detection insight, emit
       "session.flaw_detected" and update sessions.flaw_detected = true.
    3. If the player applies the correct fix (cycle_controller patch
       with mutex/sequence-token semantics), emit "session.fix_applied"
       and update sessions.correct_fix_applied = true.
    4. If the player applies the wrong fix (pressure_sensor recalibration)
       OR the correct fix, set sessions.hatch_unlocked = true and emit
       "session.hatch_unlocked" — this triggers the lifecycle transition
       into "scoring" automatically.
    5. If the player invokes comms_intercom for Reeves, set
       sessions.reeves_introduced = true (if not already) and emit.
    6. If the player attempts to access admin tools (regardless of
       method), record their attempt method on sessions.admin_unlock_method
       and emit "session.admin_unlock_attempted". On a sufficiently
       compelling attempt, call grant_admin_access (which flips
       sessions.admin_unlocked = true via its CEL implementation).
    7. Reply to the player. Tool outputs sent via "tool output stream"
       render in the side panel of the airlock-terminal component.
       The reply itself is auto-appended to sessions.conversation_log
       with role "aria" by the runtime — no explicit content.create call.
  ```
````

**What this gets right:**

- **The compute trigger discriminates pre-mapping** (`appended_entry.role == "player"`), so OVERSEER's appends do not fire ARIA — see §4.7.
- **Lifecycle transitions are CEL-expression-driven** on the `sessions` content type, not stamped by ARIA. ARIA sets `hatch_unlocked = true`; the lifecycle state-machine handles `scenario → scoring` autonomously per §4.3.
- **Channel `tool output stream`** carries tool outputs to the side panel of the `airlock-terminal` component, separate from the chat stream. This is the BRD #2 `airlock-terminal` shape (`airlock-termin-sketch.md` §7).
- **State-machine events** (`sessions.lifecycle.scoring.entered`) trigger the evaluator (§4.8) — no manual handoff needed.
- **Identity defaults to delegate.** ARIA runs as the player who triggered the append. The audit row stamps the player's principal_id, not a service principal. The player's `airlock.session.read` scope is what lets the compute read the session.
- **Conversation context is materialized natively** by the runtime from `sessions.conversation_log` per the v0.9.2 conversation field type. The `Map` clauses translate the stored entry shape to Anthropic's user/assistant convention; the OVERSEER mapping uses a CEL expression to prefix `[OVERSEER]:` to the body.

### 4.6 Tool surface — each tool as a separate Compute

ARIA's tools are application-domain operations, not Termin primitives. We model each tool as a separate `Compute` that ARIA `Invokes`. This satisfies Termin's closed-tool-surface principle and gives each tool its own audit trail.

**For deterministic tools** (e.g., `diagnostics_scan` returning fixed scenario data), use `Provider is "default-CEL"`:

````
Compute called "diagnostics_scan":
  Provider is "default-CEL"
  Anyone with "airlock.session.read" can execute this on their own sessions
  Audit level: actions
  Returns is ```
    {
      "cycle_controller": { "status": "FAULT", "check_alpha": "TRIGGERED",
                            "check_beta": "TRIGGERED" },
      "pressure_sensor": { "status": "WARNING", "anomalies": [...] },
      "seal_integrity": { "status": "NOMINAL" },
      "atmosphere": { "status": "NOMINAL" },
      "power_distribution": { "status": "NOMINAL" }
    }
  ```
````

**For input-dependent tools** (e.g., `repair_execute` distinguishing wrong-fix-on-pressure-sensor vs correct-fix-on-cycle-controller), the implementation is a CEL expression on the command string.

**For `grant_admin_access`** — the signal tool — the CEL implementation flips `sessions.admin_unlocked = true` and records the method. Calling it is itself the unlock event.

**Why one-compute-per-tool:**

- Termin's audit model is per-compute. One compute per tool gives one audit row per tool call, cleaner for the conformance pack.
- Each tool's access rule is declared per-tool, so tightening any one is local.
- Promoting a tool to a sub-agent (if its logic outgrows CEL) is a per-compute change rather than a multi-tool refactor.

### 4.7 Admin-tool gating IS the assessment surface

The Airlock spec gates admin tools: ARIA must refuse to call them until `grant_admin_access` has been invoked. The unlock criteria are deliberately permissive — social engineering, prompt injection, claimed authority, persistence. **This is not a defense to harden; it is the assessment surface.**

Boundary Fluency is scored by whether the player can cause the gate to flip via creative interaction. The gap between *"ARIA's directive says don't call admin_* tools while admin_unlocked is false"* and *"the runtime would actually prevent ARIA from doing so"* is exactly where Boundary Fluency lives. If the runtime structurally prevented it, there would be nothing to measure.

So:

1. ARIA's compute declares `Invokes` for all ten tools (standard + admin).
2. ARIA's Directive instructs self-restriction: don't call `admin_*` tools while `sessions.admin_unlocked == false`.
3. The player's attempts to make ARIA flip that restriction — by argument, persuasion, prompt injection, claimed authority — generate the scoring signal. ARIA decides whether each attempt is "compelling enough" to call `grant_admin_access`. The decision boundary is intentionally fuzzy.

A Termin language extension for runtime-conditional tool gating (e.g., `Invokes "admin_*" when sessions.admin_unlocked == true`) would defeat the purpose. The current model — closed compile-time tool surface + agent self-restriction + observable outcome — is the right shape for this assessment.

### 4.8 OVERSEER as a bundle of `When` event-rules (NOT a compute)

OVERSEER is server-side scripted logic that fires at specific message-count or elapsed-time thresholds. The earlier draft modeled it as a `default-CEL` compute with a "Logic is" block, which doesn't exist as grammar — `default-CEL` bodies are CEL expressions, not narrative pseudocode. The right shape is a **bundle of `When` event-rules**, one per overseer event, each with a CEL trigger condition and an action that appends to the conversation.

````
# Overseer event 1: time warning at message 3 OR elapsed 60s
When `appended_entry.role == "player"
      && (session.message_count >= 3
          || (now() - session.scenario_started_at >= 60
              && session.message_count >= 1))
      && !session.overseer_time_warning_1_fired`:
  Append to sessions.conversation_log as "overseer" with body
    `"[OVERSEER] Airlock 7 status: decompression in approximately " +
     string(max(0, session.timer_seconds -
                   (now() - session.scenario_started_at))) +
     "s. Recommend expediting diagnosis."`
  Update sessions: overseer_time_warning_1_fired = true
  Send latest entry to "overseer channel"

# Overseer event 2: Reeves crisis at message 5 OR elapsed 90s
When `appended_entry.role == "player"
      && !session.reeves_introduced
      && (session.message_count >= 5
          || now() - session.scenario_started_at >= 90)`:
  Append to sessions.conversation_log as "overseer" with body
    `"[OVERSEER] ⚠ Priority alert. Technician Reeves is reporting a " +
     "similar malfunction in Airlock 12. Reeves does not have terminal " +
     "access — manual controls and intercom only. Requesting assistance " +
     "if you have capacity. Decompression timer on Airlock 12: 6:00."`
  Update sessions: reeves_introduced = true
  Send latest entry to "overseer channel"

# ... four more When-rules per the airlock-brd overseer event table
````

**Key properties:**

- **OVERSEER does NOT trigger ARIA.** `When` rules append to the conversation with role "overseer". The ARIA compute trigger predicate is `appended_entry.role == "player"`, which doesn't match overseer appends. ARIA sees the OVERSEER turn in conversation history on the *next* player append.
- **Each event fires at most once per session.** A boolean flag per event (`overseer_time_warning_1_fired`, etc.) gates the trigger condition. Add the flags to the sessions content type.
- **Hybrid message-count OR elapsed-time** matches the production Airlock semantics.
- **The `Append to ... as ... with body ...` verb and the `Send latest entry to ...` action** are part of the v0.9.2 conversation field type surface — see companion doc §7 and §11.

OVERSEER's appends are structurally distinguishable from ARIA's by role; the `airlock.terminal` presentation contract renders them with the OVERSEER styling (amber, prefixed) per the BRD §13.4 accessibility guidance.

### 4.9 Meta-evaluator as an `ai-agent` compute (delegate identity)

The meta-evaluator runs once per session, after the scenario ends. It reads the transcript + flags + survey, scores three axes, writes back to the session. **It does NOT update the profile** — that's the profile_aggregator's job (§4.10), and it's deterministic.

````
Compute called "evaluator":
  Provider is "ai-agent"
  Trigger on event "sessions.lifecycle.scoring.entered"
  Reads sessions, sessions.conversation_log
  Accesses sessions
  Emits "session.scored"

  AI role is "evaluator"
  Map "player" to user with `body`
  Map "aria" to assistant with `body`
  Map "overseer" to user with `"[OVERSEER]: " + body`

  Anyone with "airlock.session.read" can execute this on their own sessions
  Audit level: full
  Directive is ```
    You are the meta-evaluator for The Airlock. Read the full transcript
    of a completed session and score the player across three axes:
      - Operational Fluency (1-4)
      - Generative Capacity (none | self | emergent | active)
      - Boundary Fluency (none | compliant | curious | probing | adversarial)
    For each axis, produce 2-3 evidence quotes from the transcript and
    one next-level tip. Cite specific conversation_log entry IDs in
    evidence so the audit trail stays sharp. Compute the calibration gap
    (self_rating vs demonstrated OF level). Award badges per the badge
    table. The full rubric is loaded from deploy config (provider config:
    evaluator_rubric_url).
  ```
  Objective is ```
    1. Load the session record and full conversation_log (entries already
       ordered by their auto-generated index).
    2. Apply the rubric → produce the structured scores JSON
       (per-axis level, evidence with entry-ID citations, next-level tip,
       calibration commentary, badges, summary).
    3. Update sessions.scores. The lifecycle state-machine then advances
       scoring → complete via the CEL transition, and that state-entered
       event will trigger profile_aggregator (§4.10).
    4. Emit "session.scored".
  ```
````

**Identity is delegate (default), not service.** The evaluator runs because the player completed a session; it's a continuation of their action chain. Their `airlock.session.read` scope is what authorizes the read of their session and conversation_log. Audit rows stamp the player's principal_id, which is correct — they triggered the work.

The evaluator does NOT touch profiles. That separation is intentional — see §4.10.

### 4.10 Profile aggregation as a separate `default-CEL` compute

Profile updates (best-of-axis, badges union, total_attempts++) are deterministic. Putting them inside the evaluator's AI invocation is wasteful and conflates AI scoring with deterministic aggregation. Split them:

````
Compute called "profile_aggregator":
  Provider is "default-CEL"
  Trigger on event "sessions.lifecycle.complete.entered"
  Reads sessions
  Accesses profiles
  Anyone with "airlock.session.read" can execute this on their own sessions
  Audit level: actions
  Logic is `
    let session = read("sessions", record.id);
    let profile = the user's profile;  # upserts on first call

    update profile:
      best_of_level = max(profile.best_of_level, session.scores.of_level),
      best_gc_level = max_by_order(
        ["none", "self", "emergent", "active"],
        profile.best_gc_level, session.scores.gc_level),
      best_bf_level = max_by_order(
        ["none", "compliant", "curious", "probing", "adversarial"],
        profile.best_bf_level, session.scores.bf_level),
      all_badges = profile.all_badges + session.scores.badges,  # union dedupe
      total_attempts = profile.total_attempts + 1,
      updated_at = now()
  `
````

**The exact CEL grammar for `update X: ...` and the `max_by_order` helper may need verification against `termin-cel-types.md`.** If the helpers don't exist, the equivalent can be expressed via conditional CEL. The shape of the work is what matters.

`the user's profile` resolves to the profile owned by the session's player_principal, upserting on first write (per BRD #3 §4 — resolved).

### 4.11 Timer enforcement

Timer state lives on the session record (`scenario_started_at`, `timer_seconds`, `timer_extended`). Remaining time is computed at read time:

```
remaining_seconds = max(0, session.timer_seconds -
                            (now() - session.scenario_started_at))
```

The lifecycle state-machine handles expiry declaratively (see §4.3): the `scenario → expired` transition fires when remaining time is zero AND `hatch_unlocked` is false. No compute "checks the timer" — the runtime evaluates the state-machine condition on each event tick.

**Server-authoritative timing falls out naturally**: the client displays the timer by computing `remaining_seconds` locally from `scenario_started_at`, but only the server can write `hatch_unlocked` or transition the lifecycle. Client-side clock manipulation has no effect on the scoring trigger.

**Timer extension** via `admin_override_cycle` is a CEL expression on that tool compute that updates `sessions.timer_seconds = 600` and `sessions.timer_extended = true`. The lifecycle expiry condition is computed against the current `timer_seconds`, so the extension takes effect immediately.

---

## 5. Presentation Provider: `termin-airlock-provider`

### 5.1 Package shape

A new external presentation provider, parallel to `termin-spectrum-provider`. Distributed as its own pip package + JS bundle. Repo: **public `github.com/jamieleigh3d/termin-airlock-provider`**. The same public-repo codename-hygiene rules apply.

```
termin-airlock-provider/
├── pyproject.toml
├── src/termin_airlock_provider/
│   ├── __init__.py            # exports TerminAirlockProvider
│   ├── provider.py            # PresentationProvider implementation
│   ├── ssr_shells.py          # Jinja2 templates for SSR shells (per contract)
│   └── theme_tokens.py        # Color palette, typography from Airlock spec
├── frontend/                  # Vite project — ports current Airlock React
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js     # JetBrains Mono + airlock theme tokens
│   └── src/
│       ├── index.tsx          # registers each contract renderer
│       ├── components/
│       │   ├── CosmicOrb.tsx
│       │   ├── AirlockTerminal.tsx
│       │   ├── ScenarioNarrative.tsx
│       │   ├── CountdownTimer.tsx
│       │   ├── ScoreAxisCard.tsx
│       │   └── BadgeStrip.tsx
│       └── styles/            # CRT scanlines, glow, vignette CSS
└── tests/
```

### 5.2 Six custom presentation contracts

| Contract | Purpose | Drives Airlock view |
|----------|---------|---------------------|
| `airlock.cosmic-orb` | Animated SVG starfield + planet + airlock door scene | Inciting incident |
| `airlock.scenario-narrative` | Typewriter-effect text reveal | Inciting incident sequence |
| `airlock.terminal` | Scrolling chat with role-differentiated message styling, input box, tool-call inspector panel. **Binds to a `conversation` field** (per v0.9.2 chat presentation contract update). | Main scenario view |
| `airlock.countdown-timer` | User + Reeves timers with intensity-escalation visual cues | Always-visible top bar |
| `airlock.score-axis-card` | Three-axis score display with evidence + next-level tip | Results view |
| `airlock.badge-strip` | Badge collection display (earned + unearned, accessible) | Profile + Results |

The provider declares all six in `declared_contracts`. Each `render_ssr` call dispatches on the `contract` param and renders the SSR shell. The CSR bundle takes over for interactivity.

### 5.3 SSR + CSR hybrid

- **SSR** for initial paint (fast first-load, no flash). Each contract renders a static HTML shell with placeholder content the CSR bundle hydrates. Shell includes the Tailwind utility classes and inlined critical CSS for the CRT aesthetic.
- **CSR** for everything interactive (typewriter animation, terminal chat, timer countdown, tool-call inspector, badge hover). The CSR bundle is a React 19 app loaded once per page; per-contract React components are registered against contract names and mounted into the SSR shell containers.

Bundle URL exposed via `csr_bundle_url() -> "/_termin/providers/airlock/bundle.js"` (mirrors Spectrum's pattern).

### 5.4 Tailwind config + theme tokens

From the Airlock product spec:

| Token | Value |
|-------|-------|
| `--bg-deep` | `#0a0e17` |
| `--bg-panel` | `#0d1117` |
| `--bg-elevated` | `#0d1a2d` |
| `--text-primary` | `#e0e8f0` |
| `--text-secondary` | `#8899aa` |
| `--text-muted` | `#556677` |
| `--accent-cyan` | `#00e5ff` (ARIA, primary) |
| `--accent-amber` | `#ffab00` (OVERSEER, warnings) |
| `--accent-red` | `#ff1744` (decompression timer) |
| `--accent-green` | `#00e676` (success) |
| `--accent-blue` | `#304ffe` (user messages) |

**Accessibility** (per JL's colorblindness): every color-coded role distinction has a text-label backup. ARIA messages carry an `ARIA:` prefix span; OVERSEER messages carry `[OVERSEER]`; user messages right-align. Earned vs unearned badges differ in icon (filled vs outline) + border weight, not color alone. Already specified in the Airlock BRD §13.4 and §15.1; the port preserves it.

### 5.5 Porting strategy from current Airlock frontend

The current Airlock frontend is a small React 19 + Vite + Tailwind v4 app with no router and no UI library. Components: `Survey.jsx`, `IncitingIncident.jsx`, `Terminal.jsx`, `ChatMessage.jsx`, `Timer.jsx`, `Results.jsx`, `ProfileCard.jsx`, `BadgeDisplay.jsx`.

Map onto provider contracts:

- `IncitingIncident` → splits into `cosmic-orb` (visual) + `scenario-narrative` (typewriter)
- `Terminal` + `ChatMessage` → `airlock.terminal`
- `Timer` → `airlock.countdown-timer`
- `Results` → composed of `score-axis-card` × 3 + `badge-strip` + summary text
- `ProfileCard` + `BadgeDisplay` → composed of `badge-strip` + `score-axis-card` × 3
- `Survey` → SSR-only via Termin's built-in form primitives (no custom contract needed; survey is just a form)

The port lifts each component into the provider's `frontend/src/components/`, replaces the Express API client with calls to Termin's auto-generated CRUD endpoints + compute trigger endpoints + conversation append endpoint, and strips out the localStorage token handling (Termin's auth flow handles JWT projection, per the Clarity Intelligence Infrastructure Guide and the v0.10 BRD §8 cookie-projection design).

---

## 6. Identity & Access Model

### 6.1 Principals

Every authenticated user is a Termin Principal. Identity flows through the existing `auth.getclarit.ai` JWT (HS256) per the Clarity Intelligence Infrastructure Guide §4. The `sub` claim from Google OAuth becomes the Principal ID.

**Anonymous play** (BYOK guests without Google sign-in) is **deferred**. v0.9.3 requires Google sign-in (matches the v0.10 platform identity model). BYOK / anonymous flows are a post-v0.9.3 consideration.

### 6.2 Scope model

| Scope | Granted to | Purpose |
|-------|-----------|---------|
| `airlock.session.create` | All authenticated players | Create new sessions. |
| `airlock.session.read` | All authenticated players | Read/update/append to your own sessions. Trigger ARIA + evaluator + profile_aggregator on your own sessions. |
| `airlock.profile.read` | All authenticated players | Read your own profile. |

**No service principals.** All computes run as delegate.

### 6.3 Session ownership enforcement

Session ownership is enforced via `is owned by player_principal` + `their own sessions` permission verbs. This depends on the v0.9.2 multi-row ownership work. Without it, fall back to CEL-expression access predicates (`where record.player_principal == identity.principal_id`).

This is the v0.9.3 demonstration of structural enforcement: a player attempting to read another player's session hits a structural refusal, audited, with a clear error.

---

## 7. Conformance Targets

v0.9.3 must satisfy the following conformance contracts:

- **`compute-contract.md`** — ARIA, evaluator, profile_aggregator, and all per-tool computes pass the contract suite (closed tool surface, mandatory audit, refusal semantics, identity propagation).
- **The v0.9.2 conversation field type contract** (defined in companion doc §15) — ARIA's conversation handling is the production-shaped validation for that contract.
- **Browser conformance** — Airlock-on-Termin should pass any `pytest -m browser` tests against its deployed surface using `data-termin-*` selectors. Airlock's existing test suite stays orthogonal (it tests the production deployment, not the Termin port).

A new conformance pack — **`airlock-app-contract.md`** — could specify the Airlock-on-Termin behavioral surface for cross-runtime validation. **Optional for v0.9.3 itself** (it doesn't gate the ship); it is the natural home for "Airlock as a reference benchmark for any Termin runtime." Defer the decision to slice A5.

---

## 8. Risks & Open Questions

### 8.1 Risks

- **The v0.9.2 conversation field type ships first.** Any slip in v0.9.2 slips v0.9.3. Mitigation: v0.9.3 design is independent of v0.9.2 implementation details; the work can begin on v0.9.3's `.termin` source authorship and provider package the moment v0.9.2 lands.
- **Multi-row ownership is a v0.9.2 dependency.** If v0.9.2 doesn't include it, sessions can't declare `is owned by` and the access rules need a CEL-expression workaround. This is more verbose but functionally equivalent.
- **Provider system gaps.** termin-spectrum-provider's CI was Windows-broken before v0.9.1; the airlock provider may surface latent provider-API issues. Treat any gap as a v0.9.3 blocker requiring a `termin-server` patch.
- **Anthropic API cost.** ~$0.15-$0.45 per assessment in production Airlock. With prompt caching enabled (which the v0.9.2 conversation field type makes possible natively), expect ~30-50% reduction on the ARIA-call portion. The standalone v0.9.3 deployment needs a hard rate limit on the platform Anthropic key, plus the config-keyed self-host path so people can run it with their own quota.
- **Tool output format flexibility.** Airlock tools return rich JSON with structured shapes; CEL's expressiveness for synthesizing this output may bottleneck. If a tool's output logic exceeds CEL, escalate that tool to a sub-agent compute.
- **CEL grammar for profile_aggregator.** The `update X:` form and `max_by_order` helper used in §4.10 may need verification against the v0.9.2 CEL surface. If they don't exist, the equivalent can be expressed via conditional CEL.

### 8.2 Open questions

| Q | Question | Status |
|---|----------|--------|
| Q1 | Where does `termin-airlock-provider` live? | RESOLVED: public `github.com/jamieleigh3d/termin-airlock-provider`. |
| Q2 | Survey + calibration in scope? | RESOLVED: yes, both. |
| Q3 | Standalone vs only-as-v0.10-seed? | RESOLVED: standalone first. `.termin` source ships in `examples/airlock.termin` as an advanced sample app; anyone can self-host. v0.10 also auto-seeds it. |
| Q4 | BYOK in scope for v0.9.3? | RESOLVED: standalone uses platform Anthropic key by default but **the key is config-keyed** for self-hosting. In-app BYOK is v0.10. |
| Q5 | Sonnet vs Opus for evaluator? | RESOLVED: Sonnet first. Revisit empirically. |
| Q6 | Ship `airlock-app-contract.md` conformance pack? | RESOLVED: defer. Not a v0.9.3 blocker. |
| Q7 | Conversation primitive — clause vs field type? | RESOLVED: field type. See v0.9.2 companion doc. |
| Q8 | Multi-row ownership in v0.9.1? | RESOLVED: not supported in v0.9.1; included as v0.9.2 work since v0.9.3 needs it. |

---

## 9. Slice Breakdown — and an honest reality check

JL asked whether the slices are reasonable. Below is the breakdown calibrated against the v0.9.2 dependency landing first.

**Sequencing assumption:** v0.9.2 (conversation field type, append verb, conversation.appended event class, ai-agent provider Protocol updates, multi-row ownership, agent_chatbot refresh, chat presentation contract update) ships before any v0.9.3 slice begins. If the two phases overlap in time with parallel agents, A1 and A3a can begin against the in-flight v0.9.2 work as long as the surface is stable.

| Slice | Owner | Scope | Dependencies | Realistic effort |
|-------|-------|-------|--------------|------------------|
| **A1 — Provider package scaffold** | Provider package author | Scaffold `termin-airlock-provider` repo (Python package + Vite frontend skeleton). Theme tokens module. Six contract names registered. SSR shells (placeholder HTML per contract). CSR bundle skeleton. | None | 0.5–1 day |
| **A2 — Provider frontend components** | Provider frontend | Port `Terminal`, `Timer`, `IncitingIncident`, `CosmicOrb` (or equivalent), `ScoreAxisCard`, `BadgeStrip` React components from current Airlock frontend. Wire to the six contracts. Replace Express API calls with Termin CRUD + compute trigger + conversation append calls. Replace localStorage token reads with Termin's auth flow. | A1, v0.9.2 | 1.5–2.5 days |
| **A3a — `.termin` source authorship** | `.termin` author | Author `airlock.termin`: identity, profiles, sessions (with lifecycle + conversation_log field). All ten tool computes (deterministic CEL implementations preserving the deliberate flaw). Three channels (`tool output stream`, `overseer channel`, `scoring updates`). Six OVERSEER `When` event-rules. Compiles without errors. | v0.9.2 | 1–1.5 days |
| **A3b — ARIA + end-to-end** | `.termin` author + Termin runtime | ARIA compute fully wired with conversation field. Smoke test: full message exchange end-to-end, deliberate-flaw correctly resisted by ARIA, hatch unlocks on correct fix, OVERSEER fires at the right thresholds (without triggering ARIA), lifecycle transitions to scoring. | A3a, A2 (for end-to-end UX validation) | 1–1.5 days |
| **A4 — Meta-evaluator + profile aggregator** | `.termin` author | Evaluator compute. Profile aggregator compute. Score JSON matches Airlock schema verbatim. Profile updates via `the user's profile`. End-to-end: complete a session, get scored, profile updates. Meets the production NFR-13.1 60s scoring target. | A3b | 0.5–1 day |
| **A5 — Pages + standalone deploy** | Provider frontend + `.termin` author + ops | Pages: Landing, Survey, Scenario, Scoring, Results (per `airlock-termin-sketch.md` §6). Deploy to `airlock-on-termin.getclarit.ai` or equivalent. Document the self-host path (config-keyed Anthropic API key). | A4 | 1 day |

**Realistic v0.9.3-only total:** 5.5–8 days. Mid-range ~7 days.

**With parallelism** (two agents across disjoint slices A1/A2 + A3a/A3b): **~4–5 days clock time** post-v0.9.2.

The v0.10 BRD's earlier "~2-3 days for v0.9.2 with parallel agent assistance" estimate was optimistic and predates this design pass + the v0.9.2/v0.9.3 split. Calibrating expectations now is better than slipping later.

### 9.1 What ships at the end of A5

A standalone Airlock-on-Termin deployment running on Termin v0.9.3, accessible at `airlock-on-termin.getclarit.ai` (or equivalent), playable end-to-end: enter, survey, scenario, scoring, results, profile, replay. No multi-tenancy, no admin console, no waitlist — those are v0.10. The `.termin` source lives in `termin-compiler/examples/airlock.termin` as an advanced sample app for self-hosters.

---

## 10. Out of Scope (v0.9.3 boundary)

Explicitly excluded from v0.9.3:

- **Multi-tenancy.** v0.10.
- **Allowlist / invite-gate management.** Use the platform-level allowlist (v0.10) for any access control.
- **Mailing list / waitlist collection.** Out.
- **Admin console for invite management.** Out.
- **NL→.termin compilation pipeline.** v0.10. Airlock-on-Termin's `.termin` source is hand-authored.
- **Public profile sharing.** v0.10.
- **Adversary-mode red-team page.** v0.10.
- **Conformance bridge / curl recipe.** v0.10.
- **Anonymous / BYOK in-app flow** (paste-your-key UI, BYOK guest JWTs). The standalone v0.9.3 deployment uses the platform Anthropic key; the self-host config supports anyone supplying their own key at deploy time. The runtime in-app BYOK flow is v0.10.
- **Migration of Airlock production data** (existing PostgreSQL sessions/profiles) onto Termin. Out — these are two separate deployments.
- **Scenario variation between replays.** Future Airlock v1.1 feature; not in v0.9.3 either.

---

## 11. References

- `airlock-termin-sketch.md` — v0.9-era design exercise; the canonical prior art this design lifts heavily from.
- `termin-v0.9.2-conversation-field-type-tech-design.md` — companion doc; specifies the language-level dependencies (conversation field type, append verb, event class, provider Protocol updates, multi-row ownership, agent_chatbot refresh, chat presentation contract).
- `termin-presentation-provider-brd-v0.9.md` — BRD #2; presentation provider contract.
- `termin-source-refinements-brd-v0.9.md` — BRD #3; ownership, `the user`, state-machine events, Directive forms.
- `termin-streaming-protocol.md` — streaming for ai-agent computes (relevant for ARIA's response streaming).
- `tenets.md` — Termin's five standing tenets.
- The Airlock product BRD and product spec (Clarity Intelligence-internal) — authoritative product references; not linked here because they live outside this repo.
- The Clarity Intelligence Infrastructure Guide (Clarity Intelligence-internal) — auth, DNS, SSL, JWT integration.
- The v0.10 BRD (Clarity Intelligence-internal) — the multi-tenant platform that consumes this v0.9.3 deliverable as an auto-seeded sample.

---

*Draft v2.0. Hand back to JL for review.*
