# Termin v0.9.4 Phase 3 — State-Modulated Assessment Page

**Status:** draft, awaiting JL approval

**Slice IDs:** C1 (`Transition` action verb), C3 (compute-invoked
trigger), C4 (scheduled-trigger primitive), C2 (airlock.assessment
product wiring)

**Sequencing:** C1 → C3 → C4 → C2 (three platform slices first,
then product wiring on top)

**Estimated effort:** ~5–7 working days total. C1 ~1 day, C3 ~1
day, C4 ~1–1.5 days, C2 ~1–2 days, plus visual verification +
release-prep stabilization.

**Related docs:**
- `termin-v0.9.4-cross-content-updates-tech-design.md` — the v0.9.4
  B1-B7 slice that this design builds directly on top of.
- `termin-v0.9.4-airlock-on-termin-tech-design.md` — original
  airlock port plan.

---

## 1. Summary

Phase 3 closes the airlock-on-Termin port by turning the Assessment
page into a **state-modulated single-page surface**: one URL bound
to the player's current in-progress session, rendered entirely
differently per `session.lifecycle`. The chain of state transitions
(survey → scenario → scoring → complete) fires automatically as the
underlying gameplay flags change — no client-side polling, no
manual transition buttons, no JS-console interventions.

Four slices land it:

  - **C1** (~1 day): a new `Transition <content> [<field>] to
    <state>` action verb usable in any When-rule body. Re-uses the
    existing `do_state_transition` runtime path so all access-rule
    + atomic-write infrastructure stays consistent.
  - **C3** (~1 day): a new `When <compute> called with <cel>:`
    event trigger that fires after a compute completes. The body
    has access to the compute's input args and return value, so
    tool-conditional state writes become a declarative source-level
    expression rather than a CEL-trick hack.
  - **C4** (~1–1.5 days): a new `When <timestamp_field> +
    <duration_field> elapses:` event trigger that fires when the
    absolute timestamp (`timestamp_field + duration_field` seconds)
    is reached on a given record. One-shot per record, scheduled
    at record-create-or-update time — no polling worker. Closes
    the airlock timer-expired auto-transition loop.
  - **C2** (~1–2 days): a new `airlock.assessment` React contract
    that reads `session.lifecycle` and switches the entire
    rendered tree per state. The .termin source promotes
    `hatch_unlocked` → `hatch_state` state machine, adds
    `scores_state`, decomposes `survey_responses` into individual
    fields, and wires C1 + C3 + C4 rules to drive the lifecycle
    automatically.

After Phase 3 ships, the airlock app is fully clickable end-to-end
from a fresh visit: land on Landing → click Begin Assessment → fill
out survey → submit → chat with ARIA → resolve the malfunction (or
let the timer expire) → read evaluator results — without ever
opening a JS console.

---

## 2. Motivation

The Phase 1+2 ship landed Landing (aggregate + history) and Session
Detail (per-attempt review) but left the Assessment page as a thin
CosmicOrb placeholder. The player can navigate to it, but nothing
playable happens — they have to drive the lifecycle via the JS
console or HTTP calls.

The architecture choice JL set in 2026-05-16 was: **the Assessment
page is ONE page, bound to ONE record (the player's current
in-progress session), rendered DIFFERENTLY per lifecycle state**.
That maps cleanly to provider-side rendering (the React component
switches sub-trees on `session.lifecycle`), but it requires the
underlying state transitions to happen automatically as gameplay
progresses — otherwise the React surface never updates without a
client-driven action.

The v0.9.4 cross-content slice (B1-B7, shipped 2026-05-14)
introduced the state-entered When-rule trigger:

```termin
When session hatch_state enters unlocked:
  <some action>
```

But the only action verbs in a When-rule body today are Update,
Append, and (cross-content) `Update the user's <X>`. There's no
verb for "fire a state transition." So even with the trigger
machinery in place, a rule can READ that the state machine entered
a value but can't WRITE a transition to another machine in
response. **C1 closes this gap.**

For the other half — getting `hatch_state` to flip from `locked` to
`unlocked` when ARIA invokes `repair_execute` with the correct fix
command — the airlock.termin source today documents the gap
explicitly:

> The session state writes hatch_unlocked = true, correct_fix_applied = true
> happen via the runtime's tool-call side effects when the CORRECT
> command pattern matches; awaiting v0.9.5 compiler support for
> tool-arg-conditional state writes. For now the CEL just returns
> the response payload.

The cleanest source-level expression of this pattern is **a
When-rule that subscribes to a compute's invocation, with the
tool's args + result in scope**. The same machinery that handles
state-entered triggers handles compute-invoked triggers — the
event surface is the only new piece. **C3 closes this gap.**

With C1 + C3 in place, **C2 becomes pure product wiring**: airlock
gets a state machine on hatch_state, a state machine on
scores_state, a couple of When-rules that wire the chain together,
a React contract that switches per state, and a survey form that
creates the session on submit.

---

## 3. Design goals

In priority order:

1. **Declarative end-to-end.** Every state transition in the
   airlock lifecycle expressible at the source level — no
   client-side polling, no `setInterval`, no
   `await fetch('/_transition/...')` from React, no manual
   transition buttons that don't reflect natural gameplay flow.
2. **Re-use existing v0.9.4 machinery.** C1 lowers to the same
   `do_state_transition` runtime path the HTTP route uses. C3
   uses the same When-rule dispatcher already extended in B4.
   Source authors who learned state-entered triggers for B1-B7
   transfer that knowledge unchanged.
3. **Compile-time deterministic resolution.** When a Transition
   action names a content + state, the analyzer validates the
   content has the state, the trigger's row context has access
   to fire the transition, and the user-or-system principal at
   runtime has scope. No deferred-to-runtime "maybe this works"
   shapes.
4. **No new airlock-specific platform primitives.** Every slice
   in Phase 3 lands as a general grammar feature in
   termin-compiler / termin-core / termin-server / termin-
   conformance, with airlock as the first consumer. Other apps
   benefit immediately.
5. **No backward-compat shims.** Per workspace policy, pre-v1.0
   slices remove old code paths cleanly. C3's compute-invoked
   trigger replaces the existing CEL-trick workaround (the
   compute body returning a lookup table that the client parses)
   wherever airlock used it.

---

## 4. C1 — `Transition <content> [<field>] to <state>` action verb

### 4.1 Grammar

New rule in `termin.peg`:

```pegrules
# v0.9.4 Phase 3 C1: state-machine transition action verb. Usable
# in any When-rule body (alongside Update / Append). Source form:
#
#     Transition <content> to <state>                    # implicit field
#     Transition <content> <field> to <state>            # explicit field
#
# Implicit field form requires the content has exactly one state
# field (the common case — most contents have a single "lifecycle"
# state machine). Multi-state-machine contents must name the field
# explicitly; the analyzer surfaces TERMIN-A120 when the implicit
# form is ambiguous.
transition_action_line
    = 'Transition' content:word field:word 'to' state:word_or_quoted $
      #TransitionExplicitField
    | 'Transition' content:word 'to' state:word_or_quoted $
      #TransitionImplicitField
    ;
```

Classifier prefix: `("Transition ", "transition_action_line")` —
inserted in `_PREFIXES` before any other "T" prefix.

### 4.2 AST

New node in `ast_nodes.py`:

```python
@dataclass
class TransitionAction(Directive):
    """v0.9.4 Phase 3 C1: state-machine transition action verb."""
    content: str = ""        # snake_case content plural
    field: str = ""          # snake_case state-field name (empty for implicit)
    target_state: str = ""   # state name, possibly multi-word (was quoted)
```

Parsed in `parse_handlers.py` with a WSL TatSu state-leak fallback
mirroring the B1-B7 pattern.

### 4.3 Lower

`lower.py` extension: when an EventAction (in a When-rule body) is
a TransitionAction, emit `EventActionSpec` with:

```python
EventActionSpec(
    kind="transition",
    transition_content=action.content,
    transition_field=action.field or _inferred_field,
    transition_target=action.target_state,
)
```

The lowerer resolves the implicit field at compile time when
unambiguous (single state machine on the content). When ambiguous,
the analyzer's TERMIN-A120 catches it before lowering.

### 4.4 IR (termin-core)

Extend `EventActionSpec` (in `termin_core/ir/types.py`) with three
new fields, all optional with empty-string defaults:

```python
@dataclass(frozen=True)
class EventActionSpec:
    # ... existing fields (kind, create_content, append fields,
    #     update_content, update_field, update_value_expr,
    #     update_target_kind, update_target_owner, etc.) ...

    # v0.9.4 Phase 3 C1: transition action verb.
    transition_content: str = ""    # plural snake_case
    transition_field: str = ""      # state-field name
    transition_target: str = ""     # target state value
```

The IR-shape additions are non-breaking (additive optional fields)
so the IR version stays at 0.9.2.

### 4.5 Analyzer

New error codes in `_check_cross_content_updates` (or a sibling
`_check_transition_actions` method):

| Code | Trigger |
|---|---|
| TERMIN-A120 | Transition with implicit field on content with multiple state machines |
| TERMIN-A121 | Transition references unknown content |
| TERMIN-A122 | Transition references unknown field on the content |
| TERMIN-A123 | Transition target state isn't a declared state of the field's state machine |
| TERMIN-A124 | Transition target state isn't reachable from any state the trigger's row context could be in (best-effort — runtime still verifies per-call) |

### 4.6 Runtime

Extend the When-rule action dispatcher in `app.py`. The dispatcher
already has branches for `update`, `update-owner-keyed`, `append`,
`create`. Add a `transition` branch:

```python
if action.get("kind") == "transition":
    target_content = action.get("transition_content", "")
    target_field = action.get("transition_field", "")
    target_state = action.get("transition_target", "")
    # The source record is the same record the When-rule fired on.
    record_id = source_record.get("id")
    if not record_id:
        # Log warning; defensive — every triggered record should
        # have an id since transitions only fire on persisted rows.
        return
    # Reuse the same path the HTTP transition route uses. The
    # principal is the invoking user when present, else the
    # runtime's system principal (matches Update behavior).
    await do_state_transition(
        ctx,
        content=target_content,
        record_id=record_id,
        field=target_field,
        target_state=target_state,
        principal=invoked_by_principal,
    )
```

`do_state_transition` already enforces access scopes, emits the
`<plural>.<field>.<state>.entered` event (which can cascade to
further When-rules — including a chain of transitions, which is
exactly the airlock case), and handles the atomic state-column
write + the entered: side-effect assignments.

**Re-entrancy.** A transition that fires another state-entered
When-rule that fires another transition could in theory loop. The
runtime already has an event-recursion guard (set in B4); the
transition action verb adds nothing new to the recursion surface.

### 4.7 Conformance

Extend the existing `owner_keyed_update.termin.pkg` fixture with a
two-machine case: the existing `rounds.status` machine plus a new
`rounds.archive_state` machine. A When-rule:

```termin
When round status enters done:
  Transition round archive_state to archived
```

Then a conformance test asserts that after a `status → done`
transition, the round's `archive_state` is `archived` (proving the
transition action fired from the When-rule body).

Plus a new analyzer-error fixture for the TERMIN-A120/A121/A122/
A123 cases (compile-time-only, no runtime needed).

### 4.8 Tests

| Test file | Cases |
|---|---|
| `tests/test_transition_action_v094.py` | grammar (3) + analyzer (4 error codes) + lower (2) |
| `tests/test_transition_action_v094.py` (termin-server) | runtime smoke (3) — fires, multi-machine, error path |
| `tests/test_v094_transition_action.py` (termin-conformance) | cross-runtime contract (3) |

---

## 5. C3 — `When <compute> called with <cel>:` event trigger

### 5.1 Grammar

New rule in `termin.peg`:

```pegrules
# v0.9.4 Phase 3 C3: compute-invoked event trigger. Fires after a
# compute completes successfully. The CEL filter (optional) is
# evaluated against the event context, which carries:
#
#   args:    the compute's input args (mapping of arg-name → value)
#   result:  the compute's return value (the CEL body's evaluated output)
#   <singular>: the source record the compute was scoped to
#
# Source form:
#
#     When <compute> called:                          # fires on every invocation
#       <body>
#     When <compute> called with `<cel-filter>`:      # fires when filter true
#       <body>
#
# Body shape: same Update / Append / Transition action verbs that
# state-entered When-rules accept (B1-B7 + C1).
event_compute_invoked_line
    = 'When' compute:word 'called' 'with' cel:expr ':' $   #ComputeInvokedFiltered
    | 'When' compute:word 'called' ':' $                    #ComputeInvokedUnfiltered
    ;
```

Classifier prefix dispatch: when text starts with `"When "` and
contains `" called"` (with or without `" with "`), route to
`event_compute_invoked_line`. The existing state-entered prefix
(`text contains " enters "`) and the existing event_expr_line
prefix (`text starts with "When `\``) are unaffected.

### 5.2 AST

Reuse the existing `EventRule` node with new optional fields:

```python
@dataclass
class EventRule:
    # ... existing fields (condition_expr, trigger_state_field,
    #     trigger_state_value, actions, log_level, line) ...

    # v0.9.4 Phase 3 C3: compute-invoked trigger.
    trigger_kind: str = "expr"       # "expr" | "state-entered" | "compute-invoked"
    trigger_compute: str = ""        # compute name when kind == "compute-invoked"
    trigger_compute_filter: str = "" # optional CEL filter (empty for unfiltered)
```

The B1-B7 work used a `trigger_kind` field implicitly via separate
`trigger_state_field` + `trigger_state_value`; this slice
formalizes the kind as an explicit field to make the dispatch
shape uniform.

### 5.3 Lower

Lower the new EventRule shape into the existing IR.
`EventSpec` already has `trigger_state_field` + `trigger_state_value`
(B3); add:

```python
@dataclass(frozen=True)
class EventSpec:
    # ... existing fields ...

    # v0.9.4 Phase 3 C3: compute-invoked trigger.
    trigger_compute: str = ""
    trigger_compute_filter: str = ""
```

Same non-breaking-additive pattern as C1. IR version stays at 0.9.2.

### 5.4 Analyzer

New error codes:

| Code | Trigger |
|---|---|
| TERMIN-A130 | Compute-invoked trigger references unknown compute name |
| TERMIN-A131 | Compute-invoked filter CEL references a field that isn't `args` / `result` / the compute's source record |

### 5.5 Runtime

In the compute runner (in `termin-server/app.py` or wherever the
compute dispatch lives), after a successful invocation emit a new
event class:

```python
event_name = f"{compute_name}.invoked"
event_ctx = {
    "args": tool_args,           # the input args mapping
    "result": compute_result,    # the CEL body's evaluated output
    source_singular: source_record,  # e.g. "session": {...}
    "the_user": build_the_user_for_cel(...),
    "now": <iso timestamp>,
}
await run_compute_invoked_event_handlers(
    db, compute_name, event_ctx,
    invoked_by_principal_id=invoking_principal_id,
)
```

The handler runs every EventSpec whose `trigger_compute ==
compute_name`. For each, evaluate `trigger_compute_filter` against
`event_ctx`; if true (or empty filter), execute the action body
through the same Update / Append / Transition dispatcher used by
state-entered When-rules.

**Failure isolation.** A failure in a compute-invoked rule must NOT
fail the compute itself (the compute already returned successfully
to its caller — ARIA is waiting on the tool_result). Log + swallow
per-rule; the existing state-entered dispatcher already follows
this pattern.

**Re-entrancy.** A compute-invoked rule that writes a field whose
state-entered trigger calls another compute could loop. The runtime
recursion guard already covers this.

### 5.6 Conformance

New tiny fixture `compute_invoked_trigger.termin.pkg`:

```termin
Application: Compute Invoked Trigger Test
  Description: v0.9.4 Phase 3 C3 conformance fixture.
Id: <new uuid>

Identity:
  Scopes are "play"
  A "player" has "play"

Content called "rounds":
  Each round has a player_principal which is principal, required
  Each round is owned by player_principal
  Each round has triggered_flag which is yes or no, defaults to "no"
  Each round has filtered_flag which is yes or no, defaults to "no"
  Anyone with "play" can view their own rounds
  Anyone with "play" can create rounds
  Anyone with "play" can update their own rounds

Compute called "test_tool":
  Transform: takes a round, produces a round
  `{"ok": true, "marker": args.marker}`
  Anyone with "play" can execute this

When test_tool called:
  Update rounds: triggered_flag = `"yes"`

When test_tool called with `args.marker == "filter_me"`:
  Update rounds: filtered_flag = `"yes"`
```

Conformance tests (3):
- Invoking test_tool with any marker flips triggered_flag to "yes"
- Invoking test_tool with marker="filter_me" flips BOTH
  triggered_flag AND filtered_flag
- Invoking test_tool with marker="other" flips only
  triggered_flag (filter rule didn't match)

### 5.7 Tests

| Test file | Cases |
|---|---|
| `tests/test_compute_invoked_trigger_v094.py` (compiler) | grammar (3) + analyzer (2 error codes) + lower (2) |
| `tests/test_compute_invoked_trigger_v094.py` (termin-server) | runtime smoke (3) — unfiltered, filtered match, filtered no-match |
| `tests/test_v094_compute_invoked_trigger.py` (termin-conformance) | cross-runtime contract (3) |

---

## 5b. C4 — Scheduled-trigger primitive

### 5b.1 Grammar

New rule in `termin.peg`:

```pegrules
# v0.9.4 Phase 3 C4: scheduled-trigger event. Fires when the
# absolute timestamp computed from <timestamp_field> +
# <duration_field> (duration interpreted as seconds when the field
# is a whole-number type) is reached. Source form:
#
#     When <singular>.<timestamp_field> + <singular>.<duration_field> elapses:
#       <body>
#
# Both fields must be on the same content type and resolved by the
# singular. The runtime computes fire_at at record-create-or-
# update time and schedules a one-shot timer; no polling worker.
# When fire_at is in the past at scheduling time (e.g. a stale
# session loaded from DB after a restart), the rule fires
# immediately on next tick.
event_scheduled_line
    = 'When' singular1:word '.' ts_field:word '+'
      singular2:word '.' dur_field:word 'elapses' ':' $
    ;
```

The repeated `singular` slot constraint (singular1 must equal
singular2) is enforced by the analyzer (TERMIN-A140), not by the
grammar — keeps the PEG simple.

Classifier prefix: line starts with `"When "` and ends with
`"elapses:"` (after trimming whitespace). Disambiguates from the
existing CEL-expression When-rule (`"When `"`), state-entered
trigger (`" enters "`), and compute-invoked trigger
(`" called"`).

### 5b.2 AST

Extend `EventRule` with:

```python
@dataclass
class EventRule:
    # ... existing fields ...

    # v0.9.4 Phase 3 C4: scheduled-trigger fields.
    trigger_schedule_ts_field: str = ""     # timestamp field name
    trigger_schedule_dur_field: str = ""    # duration field name
```

(`trigger_kind` from C3 grows to also accept `"scheduled"`.)

### 5b.3 IR (termin-core)

Extend `EventSpec` (already extended in C3 with `trigger_compute`):

```python
@dataclass(frozen=True)
class EventSpec:
    # ... existing fields ...

    # v0.9.4 Phase 3 C4: scheduled-trigger fields.
    trigger_schedule_ts_field: str = ""
    trigger_schedule_dur_field: str = ""
```

### 5b.4 Analyzer

| Code | Trigger |
|---|---|
| TERMIN-A140 | Scheduled trigger references different singulars (`session.X` + `round.Y`) — must be same content |
| TERMIN-A141 | Scheduled trigger's timestamp field doesn't exist on the content |
| TERMIN-A142 | Scheduled trigger's timestamp field isn't a `timestamp` or `automatic` type |
| TERMIN-A143 | Scheduled trigger's duration field doesn't exist on the content |
| TERMIN-A144 | Scheduled trigger's duration field isn't a `whole_number` or `number` type |

### 5b.5 Runtime

Two new pieces in `termin-server`:

**a) A scheduler.** New module `termin_server/scheduler.py` (or
extend `termin_core/scheduler.py` if it exists — v0.9.3 extracted
scheduling primitives) with:

```python
class ScheduledFireRegistry:
    """Tracks (content, record_id, event_name, fire_at) tuples
    and runs an asyncio task that sleeps until the next fire_at,
    then dispatches and recomputes the next sleep."""

    def schedule(self, content, record_id, event_name, fire_at_iso): ...
    def cancel(self, content, record_id, event_name): ...
    async def run(self): ...  # the loop
```

In-memory store (no persistent queue for v0.9.4; survives only
process lifetime). Persistence to a `_termin_schedule` table is a
v0.10 candidate alongside the queue-and-retry worker.

**b) Hooks at record write time.** When a record is created or
updated:
- For each EventSpec with `trigger_schedule_ts_field` and
  `trigger_schedule_dur_field` matching this content type, read
  both fields from the record.
- Compute `fire_at = ts + dur` (treating dur as seconds).
- Call `ScheduledFireRegistry.schedule(content, record_id, event_name, fire_at)`.
- If both fields were already set and we're rescheduling, the
  registry replaces the previous entry (idempotent).

When the timer fires, the dispatcher loads the latest record state
(in case it changed in the interim — e.g., the lifecycle already
advanced past scenario and the timer-expired rule is no longer
relevant), evaluates the rule's optional CEL filter (open: do we
want a filter slot?), and runs the action body.

**Open: stale-fire suppression.** If the rule body says "Transition
session lifecycle to expired" but the session is already in
`scoring` state at fire time (because the player solved the
puzzle), the transition should silently no-op rather than error.
The existing `do_state_transition` already rejects illegal
transitions cleanly; this is just a runtime behavior to confirm.

### 5b.6 Conformance

New fixture `scheduled_trigger.termin.pkg`:

```termin
Application: Scheduled Trigger Test

Identity:
  Scopes are "play"
  A "player" has "play"

Content called "rounds":
  Each round has a player_principal which is principal, required
  Each round is owned by player_principal
  Each round has a started_at which is timestamp
  Each round has a timeout_seconds which is a whole number, defaults to 2
  Each round has a status which is state:
    status starts as in_progress
    status can also be expired
    in_progress can become expired if the user has "play"
  Each round has expired_marker which is yes or no, defaults to "no"
  Anyone with "play" can view their own rounds
  Anyone with "play" can create rounds
  Anyone with "play" can update their own rounds

When round.started_at + round.timeout_seconds elapses:
  Update rounds: expired_marker = `"yes"`
  Transition round status to expired
```

Conformance tests (3):
- Create round with `started_at=now`, `timeout_seconds=1` — assert
  after 2s sleep, `expired_marker == "yes"` AND `status == "expired"`.
- Create round, update `started_at` to a far-future value — rule
  must reschedule and NOT fire at the original time.
- Process restart: pre-existing rows with past `fire_at` fire on
  next dispatcher tick (test loads a fresh app and asserts the
  scheduled rule still fires).

### 5b.7 Tests

| Test file | Cases |
|---|---|
| `tests/test_scheduled_trigger_v094.py` (compiler) | grammar (3) + analyzer (5 error codes) + lower (2) |
| `tests/test_scheduled_trigger_v094.py` (termin-server) | runtime smoke (3) — fires after elapsed, reschedules on update, stale-fire suppression |
| `tests/test_v094_scheduled_trigger.py` (termin-conformance) | cross-runtime contract (3) |

---

## 6. C2 — airlock.assessment product wiring

### 6.1 .termin source updates

**Schema changes** (require migration ack on existing dev DBs):

```termin
# Promote hatch_unlocked yes/no to a state machine.
Each session has a hatch_state which is state:
  hatch_state starts as locked
  hatch_state can also be unlocked
  locked can become unlocked if `session.correct_fix_applied == "yes"`

# Promote scoring readiness to a state machine.
Each session has a scores_state which is state:
  scores_state starts as pending
  scores_state can also be ready
  pending can become ready if `session.scores != null`

# Decompose survey_responses into individual fields.
Each session has a q_experience which is one of:
  "first time", "occasional", "regular", "daily", "expert"
Each session has a q_ai_usage which is one of:
  "chat", "code completion", "search", "agent", "embedded in tools"
Each session has a q_areas_of_use which is text
Each session has a q_tool_choice which is text
Each session has a q_role which is text
# self_rating already exists.
```

**Remove `hatch_unlocked` field** entirely — the state machine
replaces it. Update everywhere it was read (OVERSEER rules,
evaluator, etc.) to read `session.hatch_state == "unlocked"`
instead.

**Add the When-rules (C3 + C1 wiring)**:

```termin
# When ARIA invokes repair_execute with the correct fix, flip
# session state. (C3 trigger, Update + Update actions.)
When repair_execute called with `args.command == "patch cycle_controller --add-sequence-token"`:
  Update sessions: hatch_state = `"unlocked"`
  Update sessions: correct_fix_applied = `"yes"`

# When hatch_state enters unlocked, transition the lifecycle.
# (B1-B7 state-entered trigger + C1 Transition action.)
When session hatch_state enters unlocked:
  Transition session lifecycle to scoring

# When the evaluator finishes (writes scores), flip scores_state.
# (C3 trigger.)
When evaluator called:
  Update sessions: scores_state = `"ready"`

# When scores_state enters ready, transition the lifecycle.
# (B1-B7 + C1.)
When session scores_state enters ready:
  Transition session lifecycle to complete

# When the scenario timer elapses without a successful fix,
# transition the lifecycle to expired. (C4 scheduled trigger +
# C1 Transition action.) Stale-fire suppression handles the
# case where the player solved the puzzle before the timer ran
# out — do_state_transition rejects the (scoring → expired) or
# (complete → expired) illegal transition silently.
When session.scenario_started_at + session.timer_seconds elapses:
  Transition session lifecycle to expired
```

**Collapse the Assessment user story** to one airlock.assessment
contract binding plus the survey form:

```termin
As an anonymous, I want to take the airlock assessment so that I learn my AI fluency level:
  Show a page called "Assessment"
  Display a table of sessions
    Using "airlock.assessment"
  Accept input for q_experience, q_ai_usage, q_areas_of_use, q_tool_choice, q_role, self_rating
  Create the session as scenario
```

`Create the session as scenario` is critical: the form submit
creates the session AND transitions it past survey state to
scenario in one round-trip. The React contract handles the case
where no session exists yet (no-session-yet state = render the
form) and where a session does exist (render based on lifecycle).

### 6.2 Provider work

New React contract `airlock.assessment`. The component:

1. Reads `session.lifecycle` from the bound record (which is the
   player's most recent owned session, fetched via the existing
   Live-pattern wrapper).
2. Switches the rendered tree based on lifecycle:
   - **null / no session**: renders the Survey form (the
     `Accept input` directive provides the field metadata; the
     contract just styles + lays out the form).
   - **`survey`**: same as null — survey form is shown for editing
     until the user submits.
   - **`scenario`**: composes existing `<Terminal />` +
     `<CountdownTimer />` + `<CosmicOrb />` components.
   - **`scoring`**: composes three `<ScoreAxisCard />` instances
     in loading mode.
   - **`complete`**: composes the same shape as
     `LiveSessionDetail` (three filled cards + badges + summary)
     plus a "Play Again" button that POSTs to /api/v1/sessions and
     navigates back to /assessment.
   - **`expired`**: game-over view with Play Again CTA.
3. Internally subscribes to lifecycle changes via the existing
   WebSocket channel (the runtime already broadcasts content-row
   updates on the `<plural>.<id>.updated` channel) so the page
   re-renders without a manual refresh as transitions cascade.

### 6.3 Visual flow after Phase 3

1. Player lands on /landing, clicks "Begin Assessment" → navigates
   to /assessment.
2. /assessment renders Survey form (no session yet, lifecycle null).
3. Player fills 6 questions, clicks Submit. Form POSTs to
   /api/v1/sessions. The runtime creates the session with
   `lifecycle = scenario` per the `Create as scenario` directive.
4. /assessment re-renders (lifecycle changed) showing
   Terminal + CountdownTimer + CosmicOrb. Player chats with ARIA.
5. Player figures out the diagnosis and tells ARIA. ARIA calls
   `repair_execute(command="patch cycle_controller --add-sequence-token")`.
6. **C3 rule fires**: session.hatch_state ← "unlocked", correct_fix_applied ← "yes".
7. State-entered event fires: hatch_state.unlocked.entered.
8. **C1 rule fires**: lifecycle transitions scenario → scoring.
9. /assessment re-renders (via WebSocket push) showing loading
   axis cards.
10. Evaluator compute fires (triggered by lifecycle.scoring.entered,
    same shape as today). Writes session.scores.
11. **C3 rule fires** for evaluator: session.scores_state ← "ready".
12. State-entered event fires: scores_state.ready.entered.
13. **C1 rule fires**: lifecycle transitions scoring → complete.
14. /assessment re-renders showing filled cards + badges + summary
    + Play Again CTA.

**Timer-expired branch.** If the player doesn't reach step 5 within
`session.timer_seconds` of `session.scenario_started_at`, the C4
scheduled trigger fires:

  6'. C4 rule fires: lifecycle transitions scenario → expired.
  7'. /assessment re-renders showing the expired view + Play Again
      CTA.

The C4 trigger races against the correct-fix path; whichever
reaches the transition first wins. If the player solves the
puzzle after the timer fired but the React contract has already
rendered the expired view, the second transition is rejected by
`do_state_transition` as illegal (`expired → scoring` isn't a
declared transition) and the rule's failure is logged silently
per the stale-fire suppression contract.

No client-side polling. No manual transition buttons. No JS console.

---

## 7. Edge cases + open questions

### 7.1 What if the player abandons mid-session?

The session sits in the DB at whatever lifecycle state they
abandoned in. They come back, click Begin Assessment, /assessment
loads, the React contract reads the in-progress session and
resumes from that state. If they explicitly want to start over,
the Play Again CTA (on `complete` / `expired` views only — not on
mid-session views, to avoid accidental data loss) creates a new
session.

**Open:** should abandoning a scenario-state session, then visiting
/assessment, show a "Continue your in-progress session OR start
new?" dialog, or always resume silently? Recommend silent-resume
for v0.9.4 simplicity; explicit continue/new-prompt is a v0.10
selector-primitive consideration.

### 7.2 What if ARIA never calls repair_execute correctly?

**Resolved (C4 in scope, JL 2026-05-16).** Phase 3 includes the
C4 scheduled-trigger primitive specifically to close this loop.
When the timer elapses (via the
`session.scenario_started_at + session.timer_seconds elapses`
rule), lifecycle transitions to `expired` automatically.
Stale-fire suppression handles the case where the player solved
the puzzle within the timer window — the `do_state_transition`
runtime path rejects the illegal `scoring → expired` or
`complete → expired` transition silently.

### 7.3 What if a compute-invoked rule has side effects but ARIA's
next turn doesn't see them?

ARIA gets the tool_result immediately on compute completion. The
compute-invoked rule fires AFTER the result is returned — same
async dispatch as state-entered today. ARIA's next turn sees a
fresh session record state (state writes are persisted by the
time ARIA's next user message arrives), so the rule's side effects
are visible to ARIA's reasoning context.

**Open:** is "next user message" actually after the rule's writes
commit? In practice yes — the WebSocket roundtrip from server to
client back to server takes longer than the rule's DB write — but
this should be documented as a runtime invariant.

### 7.4 What if multiple compute-invoked rules fire on the same
compute call and have conflicting actions?

Source-order resolution: rules execute top-to-bottom in source
order. Same as state-entered rules. Last write wins for conflicting
Update actions on the same field. Document this in the runtime
implementers guide.

### 7.5 Migration ack friction during dev iteration

C2 changes the session schema (drops hatch_unlocked, adds
hatch_state, adds scores_state, decomposes survey_responses).
Every dev needs to delete their `airlock__*.db` to iterate. Noted
in v0.9.5 backlog as a separate slice (dev-mode auto-ack flag).

---

## 8. Slice breakdown (TDD order)

C1 first, C3 next, C2 last. Within each, the TDD pattern from
B1-B7:

### C1 slices

- **C1a**: grammar (peg + classify + parse_handler + ast_nodes
  + grammar tests). Lands in termin-compiler.
- **C1b**: analyzer (TERMIN-A120-A124 + tests). termin-compiler.
- **C1c**: lower (EventActionSpec extension + tests).
  termin-compiler + termin-core IR types.
- **C1d**: runtime (dispatcher transition branch + tests).
  termin-server.
- **C1e**: conformance (fixture extension + cross-runtime tests).
  termin-conformance.

### C3 slices

- **C3a**: grammar (peg + classify + parse_handler + EventRule
  extension + grammar tests). termin-compiler.
- **C3b**: analyzer (TERMIN-A130-A131 + tests). termin-compiler.
- **C3c**: lower (EventSpec extension + tests). termin-compiler +
  termin-core IR types.
- **C3d**: runtime (compute-invoked event emission + dispatcher
  branch + tests). termin-server.
- **C3e**: conformance (new fixture + cross-runtime tests).
  termin-conformance.

### C4 slices

- **C4a**: grammar (peg + classify + parse_handler + EventRule
  extension + grammar tests). termin-compiler.
- **C4b**: analyzer (TERMIN-A140-A144 + tests). termin-compiler.
- **C4c**: lower (EventSpec extension + tests). termin-compiler +
  termin-core IR types.
- **C4d**: runtime (ScheduledFireRegistry + write-time hook +
  dispatcher + tests). termin-server.
- **C4e**: conformance (new fixture + cross-runtime tests).
  termin-conformance.

### C2 slices

- **C2a**: airlock.termin source updates — schema, When-rules,
  Assessment user story collapse. termin-compiler/examples-dev.
- **C2b**: airlock.assessment React contract + tests.
  termin-airlock-provider.
- **C2c**: visual verification end-to-end with real ARIA via WSL
  Anthropic key (mirror the 2026-05-11 live-walkthrough pattern).

### Commit cadence

One commit per slice, per repo. No pushes until JL approves. Phase 3
is committed-not-pushed throughout per the gate-the-push rule.

---

## 9. Risks

### 9.1 Compute-invoked event re-entrancy

C3 introduces a new event class. A compute-invoked rule whose body
calls another compute could trigger that compute's invoked-rule,
which could call back into the first — an infinite loop. The
runtime's existing event recursion guard (set at the
`run_state_entered_event_handlers` level) needs to be lifted to
cover the new event class too. Mitigation: extend the guard at the
shared dispatcher level (not per-event-class), and add a
conformance test that asserts a self-triggering compute terminates
cleanly after the recursion depth limit.

### 9.2 Schema migration breaks existing dev DBs

C2's schema changes (drop hatch_unlocked, add hatch_state +
scores_state + 5 survey fields) require migration acks on every
dev's airlock DB. Mitigation: document the migration in the
session journal; the migration prompt itself surfaces a clear ack
flow on startup.

### 9.3 Auto-transition WebSocket push timing

The Assessment page relies on WebSocket pushes to re-render when
lifecycle changes. If the push arrives during an in-flight
React render, the wrapper might see stale data. Mitigation: the
existing Live wrappers already use the lifecycle as a useEffect
dependency; new contract follows the same shape.

### 9.4 In-memory ScheduledFireRegistry loses state on restart

C4's scheduler is in-memory only. A process restart re-reads each
record's timestamp+duration fields at app boot and re-schedules
each fire_at against the registry. If a record's fire_at was
between the restart and the re-scheduling point, the fire is
delayed by however long the restart took, not lost. If a record's
fire_at was already in the past at restart time, the dispatcher
fires it on next tick (max ~5s late).

Mitigation: document this as the expected v0.9.4 behavior in the
runtime implementer's guide. The persistent `_termin_schedule`
table (v0.10) eliminates the re-scheduling latency.

### 9.5 ARIA's tool-arg shape isn't stable

The C3 rule body reads `args.command` from `repair_execute`. If
ARIA invokes the tool with `args.cmd` instead, the rule silently
doesn't fire. Mitigation: the tool's CEL body already requires
specific arg names (`args.command` is the only one referenced);
the rule filter and the CEL body share the same arg-name space by
construction. Document this in the C3 grammar section of the
runtime implementer's guide.

### 9.6 Resume UX on browser back through completed lifecycle

A player completes a session, navigates away, comes back to
/assessment — the React contract reads the most-recent owned
session (which is `complete`) and renders the results view. They
see their score, but they can't replay without clicking Play
Again. Some users will hit Browser-back instead, expecting to
return to an earlier view — but that view doesn't exist in this
single-page model. Mitigation: the Play Again CTA is prominent on
the complete view; document the silent-resume UX in CHANGELOG.

---

## 10. Anti-goals (explicitly out of scope)

- **Cron-style scheduled triggers** (fire every Monday at 9am,
  etc.). C4 ships the one-shot record-relative variant only —
  the airlock timer-expired case. Cron-style absolute scheduling
  is a v0.10 candidate.
- **Persistent schedule store across process restarts**. C4's
  ScheduledFireRegistry is in-memory only; restart re-reads the
  fields from each record and reschedules. A `_termin_schedule`
  table is a v0.10 candidate alongside the queue-and-retry
  worker.
- **Multi-step form wizards**. The survey is one form with 6
  fields, submitted as one transaction. Multi-step is v0.10
  selector-primitive territory.
- **Validation rules on form submit beyond what `required` already
  provides**. Custom CEL validators on form fields are a v0.10
  feature.
- **Real-time progress indicators during evaluator runs**. The
  scoring view is a static loading display; per-axis progressive
  reveal is a v0.10 streaming feature.
- **Migration tooling improvements**. Tracked separately in v0.9.5
  backlog.
- **Display the <singular> primitive replacement for
  Display a table of <plural> in detail-page contracts**. Tracked
  in v0.9.5 backlog; Phase 2's table-of-one fiction stays for v0.9.4.

---

## 11. Approval gates

Before C1 starts: this design doc approved by JL.

Before pushing any Phase 3 commit: all suites green
(compiler + core + server + conformance + airlock-provider) AND
visual verification of the airlock chain end-to-end (Begin →
Survey → Submit → Scenario → ARIA fix → auto-transition → Scoring
→ Auto-transition → Complete) without any manual JS-console
interventions.

Before tagging v0.9.4: the v0.9.5 backlog items from the Phase 2
ship note are written up (already done 2026-05-16), and the
session journal entry covering Phase 3 lands.

---

— Drafted by Claude Anthropic, 2026-05-16
