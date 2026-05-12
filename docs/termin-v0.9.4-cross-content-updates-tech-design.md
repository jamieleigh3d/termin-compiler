# Termin v0.9.4 — Cross-Content Updates in When-Rules and Transform Computes

**Status:** Design draft v2 for next-session implementation. v2 incorporates JL feedback: state-entered When-rule trigger added as companion grammar, multi-match becomes a compile-time error, restricted to singular target, anonymous-principal semantics clarified.
**Date:** 2026-05-11.
**Author:** Claude Anthropic, after JL approved Option 1 of the A4 scope discussion.
**Companion:** `termin-v0.9.4-airlock-on-termin-tech-design.md` — the application-layer consumer that motivated this work (slice A4: profile_aggregator).
**Aligns with:** v0.9.4 A3a's `Update` action in EventActionSpec (the prior slice that introduced `Update sessions: <field> = ...` for same-record updates).

---

## 1. Purpose & Scope

This document specifies the language and runtime work needed to express
**"update a different record than the one the event fired on"** inside
a `When` rule (and, by extension, inside a `Transform` compute's
output handling).

The motivation is the v0.9.4 Airlock-on-Termin port's **slice A4**
(meta-evaluator + profile aggregator). The aggregator's job is: when
a session enters the `complete` lifecycle state, project that
session's scores into the player's persistent profile (best-of per
axis, badge union, attempt count, last-updated timestamp). The
session and the profile are different content types, related only by
the `player_principal` identity field. The current v0.9.4 grammar
and runtime cannot express this update.

**This document specifies:**

- A new grammar form `Update the user's <singular>: <field> = `<cel>`, ...`
  for use inside `When` rule bodies. Restricted to singular target — the
  ownership field on the target content must declare `unique`. (The
  multi-match case is a compile-time error per §3 goal 2.)
- A new When-rule trigger form `When <content-singular> <field> enters <state>:`
  bound to the state-machine `entered` event class — companion grammar
  needed to express the airlock A4 case naturally. Without it, the
  motivation example would have to use a CEL-predicate When-rule with
  a once-shot guard flag (matching the OVERSEER pattern), which is the
  ugly form this slice exists to retire.
- IR additions to `EventActionSpec` distinguishing same-record updates
  (the v0.9.4 A3a behavior) from owner-keyed updates (this slice).
- IR additions to `EventSpec` for the new state-entered trigger.
- Runtime semantics: target lookup by ownership-field equality, upsert
  behavior when no target record exists yet, error handling for the
  invariants the lookup requires.
- Test coverage at every level (compiler IR shape, conformance
  behavioral tests, end-to-end through the airlock app).

**This document does NOT:**

- Specify the more general `Update <content> where <predicate>: ...`
  form. That's a v0.10 candidate; the airlock case only needs the
  ownership-keyed lookup. (See §5.5 for the rejected alternatives.)
- Specify the plural form `Update the user's <plural>: ...` — multi-row
  updates require a different design (which records to update? all of
  them? a filtered subset? in what order?) and aren't needed by the
  airlock app. Reserved for v0.10. The compiler enforces singular-only
  by validating that the ownership field is `unique` (which precludes
  multi-row).
- Re-design the `Transform` compute output path for default-CEL
  computes. The airlock A4 work routes through `When` rules and the
  Update action; cross-content output for `Transform` computes
  inherits the same target-resolution semantics as a free
  side-benefit but is not the primary deliverable.
- Touch refresh / re-run semantics. A profile_aggregator that runs
  twice on the same session double-counts attempts. Idempotency is
  out of scope for this slice — the new state-entered trigger
  (`When session lifecycle enters complete:`) fires once per
  transition by construction, providing single-firing for free.
- Re-litigate the `=` vs `becomes` assignment-syntax question. The
  v0.9.4 A3a `Update <content>: <field> = `<cel>`` grammar uses `=`;
  this slice reuses it for consistency. A grammar-wide assignment
  readability refactor is a separate v0.10 conversation.

---

## 2. Motivation: The airlock A4 problem

The airlock app declares two related content types:

```
Content called "profiles":
  Each profile has a player_principal which is principal, required, unique
  Each profile is owned by player_principal
  Each profile has best_of_level which is a whole number, defaults to 0
  Each profile has best_gc_level which is one of: "none", "self", "emergent", "active"
  Each profile has best_bf_level which is one of: "none", "compliant",
                                                  "curious", "probing", "adversarial"
  Each profile has all_badges which is text       (* JSON-text array of badge keys *)
  Each profile has total_attempts which is a whole number, defaults to 0
  Each profile has updated_at which is automatic
  ...

Content called "sessions":
  Each session has a player_principal which is principal, required
  Each session is owned by player_principal
  Each session has scores which is structured     (* the evaluator writes here *)
  Each session has lifecycle which is state:
    lifecycle starts as survey
    lifecycle can also be scenario or scoring or complete
  ...
```

The intended A4 wiring: **when a session enters `complete`, project
its scores into the player's profile.** Specifically:

- `best_of_level = max(profile.best_of_level, session.scores.of_level)`
- `best_gc_level = best_of(profile.best_gc_level, session.scores.gc_level)`
  (best_of-by-ordinal — the GC ordering is `none < self < emergent < active`)
- `best_bf_level = best_of(profile.best_bf_level, session.scores.bf_level)`
- `all_badges = json(union(parse_json(profile.all_badges), session.scores.badges))`
- `total_attempts = profile.total_attempts + 1`
- `updated_at = now()`

The natural expression in v0.9.4 grammar — once this slice lands —
would be:

```
When session lifecycle enters complete:
  Update the user's profile:
    best_of_level = `max(profile.best_of_level, session.scores.of_level)`
    best_gc_level = `gc_max(profile.best_gc_level, session.scores.gc_level)`
    best_bf_level = `bf_max(profile.best_bf_level, session.scores.bf_level)`
    all_badges = `json(union(parse_json(profile.all_badges), session.scores.badges))`
    total_attempts = `profile.total_attempts + 1`
    updated_at = `now()`
```

Two language gaps prevent that today:

**Gap A (the trigger):** v0.9.4 When-rules support two trigger forms —
`When `<cel-predicate>`:` (event_expr_line) and
`When a <content> is <created|updated|deleted>:` (event_v1_line).
There is no When-rule equivalent for the state-machine `entered`
event class that *computes* already use via
`Trigger on event "<content>.<machine>.<state>.entered"`. Without it,
the airlock case has to use a CEL predicate with a once-shot guard
flag — the OVERSEER pattern, which exists precisely because the
clean trigger form is missing:

```
When `appended_entry.kind == "system_event" && session.lifecycle == "complete" && session.aggregator_fired != "yes"`:
  Update the user's profile: ...
  Update sessions: aggregator_fired = `"yes"`
```

That's the ugly form this slice retires by adding the natural
`When session lifecycle enters complete:` trigger.

**Gap B (the action):** Even with the right trigger, the
v0.9.4 A3a Update action looks up the target by `record["id"]`
where `record` is the event's source record (the session). For a
cross-content update like `Update the user's profile`, this is
wrong — `profiles.id == session.id` won't match. The runtime
silently discards the patch. This slice's owner-keyed Update
action fills in: target lookup by ownership-field equality,
upsert when missing, single-target by construction.

### 2.1 What `the user's X` resolves to

Inside the `When` rule body for an event triggered by user action
(an HTTP write, a WebSocket frame, an append, a state transition),
**"the user"** is the principal that caused the event:

- For `<content>.<field>.appended` events: the principal that
  appended (the session's owner, in airlock's case).
- For `<content>.<verb>` CRUD events (`created`, `updated`,
  `deleted`): the principal that issued the request.
- For state-machine `entered` / `exited` events: the principal that
  triggered the transition.

For events with no human originator (scheduled triggers, system
broadcasts), the "user" is the runtime's anonymous-system principal
and `the user's <content>` returns no record — see §6.4 for the
no-target case.

`the user's <content>` requires the target content to declare an
ownership field (`Each X is owned by <field>` — v0.9 §6.5
multi-row ownership grammar). The compiler validates this at lower
time; sources that say `Update the user's profile` when `profiles`
has no `is owned by` declaration fail with a clear error.

### 2.2 What this slice unlocks beyond airlock

The same shape — "an event on record A → update record B related to A's
owner" — appears in any app with per-user persistent state alongside
per-attempt transient state:

- **Quiz / assessment apps:** sessions → user_progress
- **Task / habit trackers:** completed_task → daily_stats
- **Subscription / billing:** payment_received → subscription_status
- **Game-shaped apps:** match → player_rating

Without the new grammar, every such app either (a) hand-writes an
ai-agent compute to do the projection (LLM tokens for deterministic
work), (b) blurs the model by stuffing everything onto the user's
profile (loses per-attempt history), or (c) writes an out-of-band
script that polls the database.

---

## 3. Design Goals

1. **Express the airlock A4 case with the same ergonomic feel as
   the same-record `Update` from v0.9.4 A3a.** The A3a syntax is
   `Update <content>: <field> = `<cel>``; the cross-content syntax
   should be a one-word change at the call site, not a new
   construct.
2. **Resolve the target deterministically — proven at compile time.**
   Single record lookup by ownership-field equality. To make
   "single" provable, the target content's ownership field must
   declare `unique` (which v0.9 §6.5 multi-row ownership grammar
   already supports). The compiler rejects sources where the
   ownership field on the target lacks `unique` (TERMIN-A104).
   Termin's deterministic-zone tenet says runtime errors are for
   the AI-agent zone only; structural invariants like "exactly
   one target row" must be guaranteed by the type system at lower
   time. The plural form `Update the user's <plural>: ...` would
   not satisfy this and is out of scope (see §1 / §5.5).
3. **Upsert by default.** First-attempt apps (airlock's
   first-ever session per player) need create-on-missing.
   Strict-update-only is a footgun for the common case.
4. **Fail loud at compile time when the target lacks ownership.**
   The lookup convention requires the target to declare `owned by`.
   Sources that misuse the grammar should not reach the runtime.
5. **Stay storage-Protocol-agnostic.** Target lookup goes through
   `ctx.storage.query` (no SQL). Adapters for non-SQL stores
   (DynamoDB, document stores) inherit the behavior.
6. **Preserve A3a backward compatibility.** Existing
   `Update <content>: ...` actions continue to target the source
   record's id — no behavior change to deployed code.

---

## 4. Reference: how the v0.9.4 A3a Update action works today

Slice A3a (commit `e748cf1`, 2026-05-09) added the `Update <content>:`
action verb to `EventActionSpec`. The compiled IR shape:

```python
EventActionSpec(
    update_content="sessions",                  # snake_case content name
    update_assignments=(
        ("message_count", "session.message_count + 1"),
    ),
    # other discriminator fields (create/send/append) are empty
)
```

The runtime path (`termin-server/termin_server/app.py:920`):

```python
elif action.get("update_content"):
    target_id = record.get("id")               # The event's source record id.
    if not target_id:
        log_warn(...); continue
    patch = {col: ctx.expr_eval.evaluate(cel, evctx)
             for (col, cel) in action["update_assignments"]}
    await ctx.storage.update(action["update_content"], target_id, patch)
```

This wires correctly when the target content is the same as the
event's source content (the airlock OVERSEER rules updating the
session's own `overseer_X_fired` flags). It silently fails when the
target is different — the lookup uses the source record's id against
the wrong table.

---

## 5. Grammar

Two grammar additions. Both live in `termin/termin.peg` next to
existing event grammar.

### 5.1 New action form: `Update the user's <singular>:`

```
# v0.9.4 cross-content Update action. Source form:
#   Update the user's <singular>: <field> = `<cel-expression>`
# Resolves the target by querying <singular>'s plural for the
# record whose ownership field equals the event's "user" principal
# id (see §7.1 for the resolution algorithm). The target's
# ownership field must declare `unique` — analyzer error
# TERMIN-A104 otherwise.
update_owned_action_line
    = 'Update' 'the' 'user' "'" 's' singular:word ':'
      field:word '=' value:expr $
    ;
```

The `<singular>` slot uses the content's singular form (the
analyzer maps it to the plural for storage operations). Singular
matches the prose register `the user's profile` rather than the
plural-noun feel of `the user's profiles` — and incidentally
prevents the multi-row reading at the grammar layer.

The trailing assignment shape (`<field> = `<cel>``) is identical
to the v0.9.4 A3a `update_action_line`. Reusing the same shape
keeps the surface consistent — author who learned A3a's syntax
needs only to learn the `the user's <singular>` prefix to write
cross-content updates.

### 5.2 New trigger form: `When <content-singular> <field> enters <state>:`

```
# v0.9.4 state-machine entered When-rule trigger. Source form:
#   When <singular> <state-field> enters <state-name>:
# Fires once per state transition into <state-name> on the
# matching state machine of <singular>'s content. Bound to the
# `<plural>.<state-field>.<state-name>.entered` event class —
# the same event class computes already use via `Trigger on event`.
event_state_entered_line
    = 'When' singular:word state_field:word 'enters' state:word_or_quoted ':' $
    ;
```

The trigger is symmetric to the compute-side `Trigger on event
"<plural>.<machine>.<state>.entered"` declaration that already
works for state-machine-driven computes (see airlock evaluator
+ profile_aggregator declarations). This slice extends the same
event class to the When-rule reactive path so the natural
`when this state is entered, do this` pattern is expressible
without the once-shot guard flag boilerplate.

State names with spaces use the quoted form:
`When ticket lifecycle enters "in progress":`.

### 5.3 Examples

**Single-line:**

```
When session lifecycle enters complete:
  Update the user's profile: total_attempts = `profile.total_attempts + 1`
```

**Multi-assignment (multi-line continuation form):**

```
When session lifecycle enters complete:
  Update the user's profile:
    best_of_level = `max(profile.best_of_level, session.scores.of_level)`
    total_attempts = `profile.total_attempts + 1`
    updated_at = `now()`
```

**Mixed with same-record update (both grammars coexist in one rule body):**

```
When session lifecycle enters complete:
  Update sessions: completed_at = `now()`
  Update the user's profile: total_attempts = `profile.total_attempts + 1`
```

### 5.4 CEL evaluation context

The CEL expressions on the right-hand side run with this scope:

- The event source record bound by its content singular
  (`session` for an event on `sessions`).
- `appended_entry` bound when the event is a `<content>.<field>.appended`
  event (per v0.9.2 §13).
- `now` bound to a CEL timestamp at evaluation time.
- **The target record bound by its content singular** (`profile`
  for an `Update the user's profile`). When the target doesn't
  exist yet, `profile` is bound to a default-valued record (each
  field gets the schema's `defaults to` value, or zero / empty
  string / empty list / `none` for state enums).

The default-valued binding is what makes upsert work: `max(profile.best_of_level, session.scores.of_level)` evaluates correctly to `session.scores.of_level` when the profile is being created.

### 5.5 Rejected forms

- **`Update <content> where <predicate>: ...`** — more general but
  invites the question "what if the predicate matches zero or
  multiple records?" The owner-keyed singular form has a single
  canonical lookup (the unique ownership field) that always
  returns 0 or 1. v0.10 candidate.

- **`Update <content> for <field> == <expr>: ...`** — same expressive
  power as the predicate form, slightly less verbose, same
  multi-match question. v0.10 candidate.

- **`Update the user's <plural>: ...`** (multi-row variant) — would
  require resolving "all of the user's <plural>" and applying the
  patch to each. Pushes the multi-match question into runtime
  (which records? in what order? error-on-empty or no-op?), violates
  the deterministic-zone tenet (§3 goal 2). The unique-constraint
  enforcement on the singular form precludes multi-row at the
  grammar layer. v0.10 candidate if a real use case appears.

- **`On user, update profile: ...`** — flips the target to the
  subject. Less Termin-house-style ("update the user's profile"
  reads like English; "on user, update profile" reads like SQL).

- **Implicit target via type matching** (e.g.,
  `Update profile: ...` infers "the user's profile" because
  profiles is owned). Magic — too easy to write and have it work
  in the airlock case but fail in any case where the source
  content also has an ownership relationship to the target.
  Prefer explicit.

---

## 6. IR

### 6.0 EventSpec trigger discriminator

The state-entered trigger lands as a new shape on `EventSpec`
(`termin-core/termin_core/ir/types.py`):

```python
@dataclass
class EventSpec:
    # ... existing fields (trigger, condition_expr, source_content, ...) ...
    # v0.9.4 cross-content slice — new trigger discriminator:
    trigger_state_field: str = ""    # snake_case state-machine column name,
                                     # e.g. "lifecycle". Empty for non-state-
                                     # entered triggers.
    trigger_state_value: str = ""    # state name being entered (with spaces
                                     # preserved, matching the multi-word
                                     # states convention). Empty for non-
                                     # state-entered triggers.
```

When `trigger_state_field` is non-empty, the runtime treats the
event as a state-entered subscription: it fires the rule's actions
on the same `<plural>.<state-field>.<state-value>.entered` event
class that computes already subscribe to via `Trigger on event`.
The `source_content` field continues to carry the snake-case
plural so the runtime knows which event bus to subscribe to.

### 6.1 EventActionSpec field additions

Two new optional fields on the existing `EventActionSpec` dataclass
(`termin-core/termin_core/ir/types.py`):

```python
@dataclass
class EventActionSpec:
    # ... existing fields (create / send / append / update) ...
    update_content: str = ""
    update_assignments: tuple[tuple[str, str], ...] = ()
    # v0.9.4 cross-content update — new fields:
    update_target_kind: str = "source-record"      # | "owner-keyed"
    update_target_owner: str = ""                  # source content's "user" — usually
                                                   # equals the event source content's snake name
                                                   # (sessions are owned by user, the user updates
                                                   # their own profile). Empty when
                                                   # update_target_kind == "source-record".
```

`update_target_kind` discriminator values:

- `"source-record"` (default — preserves A3a behavior). Target id
  is `record["id"]` from the event's source record. Used by every
  existing `Update <content>: ...` action.
- `"owner-keyed"` (new). Target is found by querying
  `update_content` for the record whose ownership field equals the
  current event's "user" principal id. See §7 for the runtime
  resolution.

### 6.2 Validation in the analyzer

The compiler raises a `SemanticError` when:

- The `update_content` named in an `Update the user's <X>` action
  doesn't exist (`TERMIN-A101`).
- The `update_content` exists but has no `is owned by` declaration
  (`TERMIN-A102`). The error message names the content and
  suggests adding the ownership clause.
- An assignment's column name isn't a field on the target content
  (`TERMIN-A103`). Catches typos at compile time.
- The `update_content` exists, has `is owned by <field>`, but
  `<field>` does not declare `unique` on its FieldSpec
  (`TERMIN-A104`). This makes the §3-goal-2 deterministic-resolve
  invariant a compile-time guarantee — without `unique`, multiple
  records could match the lookup, violating the single-target
  contract. The error message names the field and suggests adding
  `unique` to its declaration. (For airlock: `Each profile has a
  player_principal which is principal, required, unique` —
  satisfies the constraint. The session content's
  `player_principal` is not unique, which is correct because
  `Update the user's session` is intentionally not expressible.)

For the new state-entered When-rule trigger:

- The named state-machine field doesn't exist on the named content
  (`TERMIN-A105`).
- The named state isn't a valid state for the matched state machine
  (`TERMIN-A106`).

These errors mirror the existing analyzer error vocabulary for
`Update <content>: ...` (TERMIN-A09x range, per the airlock A3a
design).

### 6.3 IR JSON schema update

`docs/termin-ir-schema.json` adds the two fields to the
`EventActionSpec` definition. Both are optional with documented
defaults. Adapters that don't recognize the new fields fall back
to the A3a single-record behavior — backward-compatible at the IR
schema level.

---

## 7. Runtime behavior

### 7.1 Target resolution

In `termin-server/termin_server/app.py:920` (the existing Update
action dispatch), after the `update_content` check, branch on
`update_target_kind`:

```python
elif action.get("update_content"):
    target_kind = action.get("update_target_kind", "source-record")
    if target_kind == "source-record":
        target_id = record.get("id")           # A3a behavior, unchanged.
    elif target_kind == "owner-keyed":
        target_id, target_record = await _resolve_owner_keyed_target(
            ctx, action, record, evctx, principal_id,
        )
    else:
        log_warn(...); continue
    if target_id is None:
        # No target found AND upsert-create failed. Fail loud.
        continue
    patch = {col: ctx.expr_eval.evaluate(cel, evctx_with(target_record))
             for (col, cel) in action["update_assignments"]}
    await ctx.storage.update(action["update_content"], target_id, patch)
```

`_resolve_owner_keyed_target` (new helper in
`termin-server/termin_server/event_actions.py` or similar):

1. Determine the principal_id from the event context. Order:
   - `evctx.get("invoked_by_principal_id")` if present (set by the
     event-dispatch path when the originating request had auth).
   - `record.get(ownership_field)` of the source content as a
     fallback (the source record's owner, which equals the
     originating user for owned-by contexts).
   - Anonymous principal id otherwise.
2. Look up the ownership field name on the `update_content`
   (precomputed at startup into `ctx.ownership_field_for`).
3. Query the target: `ctx.storage.query(update_content,
   Eq(field=ownership_field, value=principal_id),
   QueryOptions(limit=1))`.
4. If a record exists, return `(record["id"], record)`.
5. If no record exists AND the target content has a `unique`
   constraint on the ownership field (the airlock case), proceed
   to upsert: build a default-valued record from the schema and
   call `ctx.storage.create(update_content, default_record)`.
   Return the new record's id and the default-valued shape.
6. If no record exists AND no `unique` constraint: log loud and
   skip the action. Multi-row ownership without uniqueness is an
   authoring choice that shouldn't be magic-upserted.

### 7.2 CEL evaluation context for assignments

After target resolution, the assignment expressions evaluate against
an extended context:

- All v0.9.4 A3a bindings (event source record by singular,
  `appended_entry`, `now`, etc.).
- **The target record bound by its content singular.** For
  `Update the user's profile`, `profile` is in scope. The CEL
  body can reference both sides: `max(profile.best_of_level,
  session.scores.of_level)`.

This binding is a NEW context shape — A3a actions don't have a
target binding because the target IS the source. The runtime
adds the binding only when `target_kind == "owner-keyed"`.

### 7.3 Atomicity

Each target update runs as a single `storage.update` call (or
`storage.create` for the upsert path). If multiple owner-keyed
updates target the same content from the same When-rule firing,
they execute sequentially — not in a single transaction. Race
conditions between simultaneous events on the same target are
not addressed in this slice; v0.10 may revisit if the airlock app
exposes them.

### 7.4 No-target case

When `_resolve_owner_keyed_target` returns `(None, None)` — the
event has no resolvable user (anonymous, system-triggered) and the
target content isn't unique-by-owner — the runtime logs a warning
and skips the action. The When-rule continues with subsequent
actions (don't-cascade-the-failure semantics matching the existing
Update action behavior).

---

## 8. Edge cases

### 8.1 Profile already exists at session creation

The upsert path is skipped — query returns the existing profile,
the patch applies normally. First-attempt and Nth-attempt are the
same code path.

### 8.2 Profile has stale defaults

Common case: the player's profile was created on first session,
they completed it, and now they're completing session #2. The
existing profile has real values for `best_of_level` etc.; the
`max(...)` in the assignment correctly picks the higher of old vs
new.

### 8.3 Anonymous principals — bare vs session-bearing

Termin's identity layer distinguishes two anonymous shapes (per
`termin-core/termin_core/providers/identity_contract.py`):

- **Bare anonymous** (`id="anonymous"`) — the sentinel for
  truly identity-less requests. No session marker. Every
  bare-anonymous caller has the same id.
- **Session-bearing anonymous** (`id="anonymous:<marker>"`) —
  produced by `make_anonymous_principal(marker)` when a session
  marker is available (e.g., the `termin_user_name` cookie). Each
  player gets a unique principal id distinguishable from others
  in the audit log.

**Airlock plays as session-bearing anonymous.** Every player gets
a unique `anonymous:<marker>` principal, so `profile.player_principal`
is unique-per-player and the owner-keyed lookup behaves exactly
the same as for authenticated principals.

**Bare anonymous + owner-keyed update is a no-op.** If a request
arrives with the bare-anonymous principal (no session marker), the
lookup queries `profile.player_principal == "anonymous"` and finds
either zero records (first call from any bare-anonymous client) or
the wrong shared one (subsequent calls share the same id). To
prevent the second case, the runtime treats `id == "anonymous"`
specifically as "no resolvable user" and skips the action with a
log warning. Apps that legitimately want bare-anonymous
single-singleton state should use a different grammar (none
exists yet — out of scope here).

If the source record's ownership field is missing or `None` (a
data-quality bug, not an expected case), the resolution falls
through to bare-anonymous and is skipped per the rule above.

### 8.4 Multiple When-rules updating the same target

Two When-rules in the same source file might both want to update
`the user's profile` (e.g., one for badges, one for score
projection). They're independent runtime invocations; each does its
own resolve + update. The target lookup is cheap (single-row
indexed query); duplicating the work is acceptable for v0.9.4.
v0.10 may add request-scoped target caching.

### 8.5 Ownership field renamed or removed

If a future grammar refactor renames or removes the
`is owned by <field>` clause on a content that has an existing
`Update the user's <X>` reference, the analyzer error
`TERMIN-A102` fires at compile time. No silent runtime breakage.

### 8.6 Reverse direction (profile-event → session update) is out of scope

Sessions in airlock have `player_principal which is principal,
required` — explicitly NOT `unique`, because each player has many
sessions over time. So `Update the user's session` would fail
TERMIN-A104 at compile time (no `unique` on the ownership field).
That's correct: a profile-triggered When-rule cannot pick "the"
session unambiguously, and updating ALL of them is the multi-row
case this slice deferred to v0.10.

The shape "find the most-recent session and update it" is a
different feature (selector primitive — see the existing v0.10
backlog item "Explicit picker binding for chat-driving tables")
that needs its own grammar. Not this slice.

---

## 9. Backward compatibility

- **Existing source files using `Update <content>: ...` are
  unchanged.** The compiler emits `update_target_kind="source-record"`
  by default (or omits the field, which the runtime treats as
  source-record per the field default).
- **Existing IR JSON consumers that don't recognize
  `update_target_kind`** see the field as unknown and fall back to
  source-record behavior (the v0.9.4 A3a path).
- **Conformance fixtures regenerate cleanly.** Existing apps
  (warehouse, helpdesk, projectboard, hrportal,
  agent_chatbot, security_agent) have no `Update the user's X`
  source today; the IR for those apps is byte-identical pre and
  post this slice.

---

## 10. Test plan

Three test layers, each enforced TDD-first per workspace
conventions.

### 10.1 Compiler tests (`termin-compiler/tests/`)

Two new test files: one for the trigger grammar, one for the
action grammar. Single file would also work; splitting by
slice keeps fail-localization tighter.

`tests/test_when_state_entered_v094.py`:

- Grammar: `When session lifecycle enters complete:` parses to
  an `Event` AST node carrying `trigger_state_field="lifecycle"`
  and `trigger_state_value="complete"`.
- Grammar: multi-word state names with quotes —
  `When ticket lifecycle enters "in progress":` — parse correctly.
- Analyzer: state field doesn't exist raises `TERMIN-A105`.
- Analyzer: state value isn't a valid state for the matched
  machine raises `TERMIN-A106`.
- Lower: produces `EventSpec` with `trigger_state_field` +
  `trigger_state_value` populated and `condition_expr` empty.
- Lower: existing CEL-predicate When-rules continue to produce
  the legacy `EventSpec` shape (regression guard).

`tests/test_update_owned_action_v094.py`:

- Grammar: `Update the user's profile: best_of_level = `<cel>``
  parses to an `EventAction` AST node carrying the singular +
  field + CEL.
- Analyzer: target singular doesn't resolve to a content raises
  `TERMIN-A101`.
- Analyzer: target lacks `is owned by` raises `TERMIN-A102`.
- Analyzer: assignment field doesn't exist on target raises
  `TERMIN-A103`.
- Analyzer: target's ownership field lacks `unique` raises
  `TERMIN-A104` (the deterministic-resolve gate).
- Analyzer: scheduled-trigger context with owner-keyed Update
  raises `TERMIN-A107` (forward-prep for v0.10).
- Lower: produces `EventActionSpec` with
  `update_target_kind="owner-keyed"` +
  `update_target_owner=<ownership_field_snake>` and the right
  `update_assignments`.
- Lower: same-record `Update <content>: ...` continues to
  produce `update_target_kind="source-record"` (regression guard).
- IR JSON schema validation: the new fields are accepted by the
  v0.9.4 schema.

Combined estimate: ~16 tests, ~250 LOC.

### 10.2 Runtime tests (`termin-server/tests/`)

`tests/test_update_owner_keyed_v094.py` (new file):

- Owner-keyed update against an existing target updates the right
  record (lookup by ownership field).
- Owner-keyed update against a missing target with a `unique`
  ownership field upserts (creates a default-valued record then
  applies the patch).
- Owner-keyed update against a missing target without a `unique`
  ownership field logs and skips.
- Upsert builds defaults from the schema (`defaults to` honored;
  enum first value when no default; empty string for text; 0 for
  numbers; empty list for structured).
- CEL eval failures inside an assignment fail-loud-skip without
  partial application (atomic per assignment-batch).
- The target record is bound in CEL scope by its singular
  (`profile.X` resolves correctly).
- Multiple assignments on the same call apply as a single
  `storage.update`.

Estimate: ~12 tests, ~250 LOC.

### 10.3 Conformance tests (`termin-conformance/tests/`)

`tests/test_v094_owner_keyed_update.py` (new file):

A new fixture (`fixtures/owner_keyed_update.termin.pkg`) covering
both the new trigger and the new action with the smallest possible
shape that exercises each:

```
Application: Owner Keyed Update Test
Description: Trigger-by-create + trigger-by-state-entered, each
projecting into a singleton owner-keyed profile.

Identity:
  Scopes are "play"
  A "player" has "play"

Content called "profiles":
  Each profile has a player_principal which is principal, required, unique
  Each profile is owned by player_principal
  Each profile has best_score which is a whole number, defaults to 0
  Each profile has games_played which is a whole number, defaults to 0
  Anyone with "play" can view their own profiles
  Anyone with "play" can update their own profiles
  Anyone with "play" can create profiles

Content called "rounds":
  Each round has a player_principal which is principal, required
  Each round is owned by player_principal
  Each round has points which is a whole number, defaults to 0
  Each round has a status which is state:
    status starts as in_progress
    status can also be done
    in_progress can become done if the user has "play"
  Anyone with "play" can view their own rounds
  Anyone with "play" can create rounds

# Existing trigger form + new action form:
When a round is created:
  Update the user's profile: games_played = `profile.games_played + 1`

# New trigger form + new action form:
When round status enters done:
  Update the user's profile: best_score = `max(profile.best_score, round.points)`
```

Conformance tests (cross-runtime):

- POST /api/v1/rounds creates a round; subsequent GET /api/v1/profiles
  shows `games_played == 1` (upsert path on the existing trigger
  + new owner-keyed action).
- POST /_transition/rounds/status/{id}/done with `points: 50`
  updates `best_score` to 50 (state-entered trigger fires; max()
  correct against default 0).
- A second round transitioned to `done` with lower points doesn't
  decrease `best_score` (max() correctness, existing-profile path).
- Two players each create+complete a round; their profiles update
  independently (target resolution is owner-scoped, not global).
- The new state-entered trigger fires once per state transition
  (single-firing semantics; the same content's `created` trigger
  fires once per row).

Estimate: ~7 tests, ~200 LOC including fixture.

### 10.4 End-to-end smoke against the airlock app

After the runtime + compiler land, replace the stub default-CEL
`Compute called "profile_aggregator"` declaration in
`examples-dev/airlock.termin` with a When-rule using both new
grammars:

```
When session lifecycle enters complete:
  Update the user's profile:
    best_of_level = `max(profile.best_of_level, session.scores.of_level)`
    best_gc_level = `gc_max(profile.best_gc_level, session.scores.gc_level)`
    best_bf_level = `bf_max(profile.best_bf_level, session.scores.bf_level)`
    all_badges = `json(union(parse_json(profile.all_badges), session.scores.badges))`
    total_attempts = `profile.total_attempts + 1`
    updated_at = `now()`
```

Then verify:

- A complete session writes scores via the evaluator.
- The When-rule fires on `sessions.lifecycle.complete.entered`.
- The player's profile reflects the new best-of values.
- A second session that scores LOWER doesn't decrease the
  profile values.
- A second session with NEW badges adds them to the existing
  set rather than replacing.
- The When-rule fires exactly once per session lifecycle entry
  (no double-counting on `total_attempts`).

This is the airlock A4 deliverable. Lives in
`termin-compiler/tests/test_airlock_profile_aggregator_v094.py`
(or as part of an existing airlock smoke file once the structure
exists).

The `gc_max` / `bf_max` ordinal-comparison helpers and `union`
list helper need to exist in the CEL surface. v0.9.4 may need to
add them as part of this slice if they don't exist yet — check
during B7 implementation. (`max(int, int)` already exists;
`parse_json` and `json` builtins exist for serialized field
round-tripping. The ordinal-on-string-enum helpers are the only
likely net-new builtins.)

---

## 11. Slice breakdown for the next session

Follow workspace TDD discipline (per `E:\ClaudeWorkspace\CLAUDE.md`):
write the failing test first, then the fix, then verify.

| Slice | Scope | Effort |
|-------|-------|--------|
| **B1a** — Trigger grammar + parser | PEG rule for `When <singular> <field> enters <state>:`. AST node + parse_handlers wiring. Failing test in `tests/test_parser.py`. | 1h |
| **B1b** — Action grammar + parser | PEG rule for `Update the user's <singular>: <field> = `<cel>``. AST node + parse_handlers wiring. Failing test in `tests/test_parser.py`. | 1h |
| **B2** — Analyzer | Validation errors A101–A106 (target exists, ownership declared, `unique` on ownership, column exists, state-field exists, state-name valid). Failing test in `tests/test_analyzer.py`. | 1.5h |
| **B3** — Lower | Trigger: emit `EventSpec` with `trigger_state_field` + `trigger_state_value`. Action: emit `EventActionSpec` with `update_target_kind="owner-keyed"` + `update_target_owner=<ownership_field_snake>`. IR schema bump. Failing test in `tests/test_ir.py`. | 1h |
| **B4** — Runtime trigger subscription | Wire `EventSpec`s with `trigger_state_field` non-empty to subscribe to the `<plural>.<state-field>.<state-value>.entered` channel. The state-machine engine already publishes that event class for computes; this slice extends the When-rule subscriber side. Failing tests in `termin-server/tests/test_when_state_entered_v094.py`. | 1.5h |
| **B5** — Runtime resolve+update | `_resolve_owner_keyed_target` helper + dispatch branch in `app.py`. Default-valued record builder for upsert. Bare-anonymous skip. Failing tests in `termin-server/tests/test_update_owner_keyed_v094.py`. | 2–3h |
| **B6** — Conformance | Fixture + behavioral tests + IR schema validation update. Regenerate via `util/release.py`. | 1.5h |
| **B7** — Airlock A4 wiring | Replace the stub `Compute called "profile_aggregator"` with a When-rule using the new trigger + action: `When session lifecycle enters complete: Update the user's profile: ...`. End-to-end smoke. Update CHANGELOGs across all five repos. | 1–2h |

**Total:** 10–13h, mid-range ~11h. One-and-a-half days serial; just
under a day with two parallel agents (B1a/B1b/B4 vs B1b/B5).

The slice ends with a runnable airlock app where completing a
session updates the profile, plus the cross-runtime conformance
tests proving the contract. Ready for v0.9.4 release prep.

---

## 12. Risks & open questions

### 12.1 What "the user" resolves to when the actor differs from the source-record's owner

§2.1 specifies "the user" as the principal that triggered the
event. For most airlock When-rules this matches the source
record's owner — players act on their own sessions, append to
their own conversations, transition their own lifecycles. The two
collapse to the same principal id.

But the platform supports cross-user actions in principle: an
admin role could (with the right access grants) write to
another player's session, transition another player's lifecycle,
or append a system_event to another player's conversation. In
that case **the actor and the owner differ**, and the design
needs a clear answer:

- Path A — **owner wins:** `Update the user's profile` resolves to
  *the session's owner's* profile. Models the airlock semantics
  cleanly (admin-triggered scoring still updates the player's
  profile, not the admin's).
- Path B — **actor wins:** resolves to *the admin's* profile. Only
  meaningful if admins also have profiles in the same content
  type. Unlikely to be what authors want.

Path A is the right semantic: the cross-content update is "this
user's projection of this event," and "this user" means the
owner of the source record. The implementation (§7.1 step 1)
already does this — it reads `record.get(ownership_field)` of
the source content's owner field rather than the actor's
principal id. The §7.1 fallback chain ordering should make this
explicit.

There's no v0.9.4 airlock surface that exposes the
actor-vs-owner divergence (no admin path), so the choice doesn't
affect today's tests. Documenting the rule now prevents drift
later.

### 12.2 What "the user" means in scheduled triggers

v0.10 will add scheduled trigger sources (per the v0.10 BRD).
A scheduled compute has no human originator — `the user`
cannot resolve to anyone meaningful, and `the user's <content>`
cannot find a single target.

**Decision:** for v0.9.4, the analyzer rejects `Update the user's
<X>` from scheduled-trigger contexts at compile time with
TERMIN-A107: *"Cross-content update via 'the user' is not yet
supported in scheduled-trigger contexts. The action assumes a
user principal in the event scope, which scheduled triggers do
not provide."* This is forward-prep — v0.9.4 has no scheduled
triggers to test against, so the gate just sits dormant in the
analyzer until v0.10 introduces scheduled triggers. Better to
land the gate now alongside the rest of the validation than to
forget it later and have a silent runtime skip ship in v0.10.

### 12.3 Multi-owner content (composite ownership)

Future grammar may allow `Each X is owned by <a> and <b>` (joint
ownership). The owner-keyed lookup currently assumes a single
ownership field. If composite ownership lands later (v0.10
candidate per BRD #3 Appendix B), this slice's
`_resolve_owner_keyed_target` needs to extend to handle the
composite predicate. Not a concern for v0.9.4.

### 12.4 Should the upsert default-valued record be visible?

The CEL assignment context binds the target record. When the
record was just upsert-created with all defaults, `profile.X`
returns the default value (0 for number fields, "" for text,
etc.). This is consistent with how `defaults to` declarations
resolve elsewhere in the runtime. No author-visible weirdness.

### 12.5 Are there v0.9.x apps relying on the broken behavior?

No. The v0.9.4 A3a Update action with cross-content `update_content`
silently does the wrong thing today (looks up by source-record's
id). No production code depends on this misbehavior — there's no
way for it to have ever worked. Safe to fix.

---

## 13. References

- `termin-v0.9.4-airlock-on-termin-tech-design.md` — the A4 slice
  description that motivated this work.
- `termin-v0.9.2-conversation-field-type-tech-design.md` §13 —
  `When` rule semantics for non-LLM listeners (the reactive layer
  this work extends).
- `airlock-termin-sketch.md` §6 — the original v0.9-era plan for
  the profile aggregator that flagged the cross-content lookup
  need.
- v0.9.4 A3a commit `e748cf1` — the same-record Update action this
  work parallels.
- BRD #3 §3.5 / §6.5 — multi-row ownership (the foundation for
  "the user's X" lookup).
- `tenets.md` — the five standing tenets. Tenet 3 (audience over
  capability) and tenet 5 (declared agents over ambient agents)
  both inform the grammar choice (explicit `the user's X` over
  implicit type matching).

---

*End of design draft. Hand to next-session Claude (or JL) for review and slice-by-slice implementation per §11.*
