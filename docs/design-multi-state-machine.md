# Design: Multi-State Machine per Content (v0.9)

**Status:** v4 — implemented and verified (2026-04-24). All 13 work-order
steps complete; full compiler suite green (1562 passed, 1 skipped).  
**Author:** Claude Anthropic  
**Roadmap ref:** v0.9 backlog item "Multi-state-machine per content"

---

## What changed from v1

- State is declared **inline as a field type** inside content blocks, not in
  standalone `State for X called "Y":` top-level blocks.
- `State for X called "Y":` is **removed from the DSL entirely** — no
  deprecation path, gone at the parse level.
- State on channels and computes is **out of scope for v0.9**. The
  `primitive_type` field on `StateMachineSpec` stays in the IR silently for
  future use; the runtime ignores it.
- Column name = field's snake_case name. No derivation algorithm.
- Articles (`A`/`An`) optional in transition lines.
- Quotes optional on state names and field names throughout.

**Additional changes from v2 review (2026-04-24):**

- Minimal form is the **canonical form**; full form (with articles and quotes)
  is valid but secondary.
- **Multiple `can also be` lines** are supported — states can be listed one per
  line instead of on a single long line. Order of `starts as` / `can also be`
  lines within the sub-block does not matter.
- **Self-transitions are valid** — `draft can become draft` is legal. The
  runtime writes the same value back and fires the event bus. Useful for
  re-confirmation, re-submission, and scope-gated audit-trail actions.
- **Parenthetical comments** (lines whose entire content is wrapped in
  parentheses) are stripped in `_preprocess()` alongside blank lines, so they
  are invisible to sub-block detection regardless of their indentation.

**Resolutions during implementation (v4, 2026-04-24):**

- **Hyphens are legal in state names.** Space-separated is the canonical form
  (`auto fix applied`), but `auto-fix-applied` parses as a single state. New
  PEG terminals `sm_words`, `sm_words_before_or`, `sm_words_before_can`,
  `sm_words_before_if` use `[\w-]+` and are scoped to state-machine contexts;
  field/role/content names elsewhere stay `\w+`. Action button target states
  also accept hyphens; field names in action buttons do not.
- **`RouteSpec` gains an optional `machine_name: Optional[str] = None` field.**
  Populated for `kind == TRANSITION` routes. Two state machines on a Content
  may legitimately share a `target_state` name (e.g. both `lifecycle` and
  `approval_status` could declare `approved`); `machine_name` disambiguates.
  Documented in `docs/termin-ir-schema.json`.
- **Storage skips state-typed fields when emitting columns.** The lowering
  pass keeps each state-typed field in `ContentSchema.fields` so the renderer
  can emit it as `input_type=state`. The state machine block on the same
  schema already emits the SQL column; without a skip in `init_db`, the field
  loop would double-create. Skip detects via `business_type == "state"` or
  via name-match against the state machine column set.
- **`create_record` seeds initial-state values into the returned record dict.**
  The persisted row already gets the SQL DEFAULT, but the in-memory dict
  returned to API clients was missing the state column key when the create
  body didn't carry it. Tests asserting on `r.json()[machine_col]` after
  POST need this seeding — without it, the assertion would key-error.
- **Compute agent `state_transition` tool gains optional `machine_name`.**
  Single-SM Content: agent may omit `machine_name`, runtime falls back to the
  one machine on the Content. Multi-SM: agent must supply `machine_name`,
  otherwise the tool returns an `{"error": ...}` dict matching the existing
  compute-runner error pattern.
- **Reserved keyword list** for state names is now explicit in the analyzer
  (words that are also sub-block grammar keywords).
- Added **§A: State vs other field types** — comparison table and enum vs state
  decision guide.

---

## 1. New DSL syntax

State becomes a **field type**. The state machine definition lives as an
indented sub-block under the field declaration, inside the content block.

### Full form (articles and quotes shown):

```
Content called "products":
  Each product has a name which is text
  Each product has a lifecycle which is state:
    lifecycle starts as "draft"
    lifecycle can also be "active" or "discontinued"
    A draft can become "active" if the user has "catalog.manage"
    An active can become "discontinued" if the user has "catalog.manage"
  Each product has an approval status which is state:
    approval status starts as "pending"
    approval status can also be "approved" or "rejected"
    A pending can become "approved" if the user has "approvals.approve"
    A pending can become "rejected" if the user has "approvals.approve"
```

### Minimal form — canonical (all optional tokens dropped):

```
Content called "products":
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active or discontinued
    draft can become active if the user has catalog.manage
    active can become discontinued if the user has catalog.manage
  Each product has an approval status which is state:
    approval status starts as pending
    approval status can also be approved or rejected
    pending can become approved if the user has approvals.approve
    pending can become rejected if the user has approvals.approve
```

Both forms parse identically. Quotes are allowed for clarity; the parser
finds boundaries from keywords regardless.

### Multiple `can also be` lines (valid):

States can be listed one per line. Order of `starts as` / `can also be` lines
within the sub-block does not matter — they are accumulated before the machine
is assembled.

```
Each document has an approval status which is state:
  approval status can also be approved
  approval status can also be needs revision
  approval status can also be rejected
  approval status starts as pending
  pending can become approved if the user has docs.approve
  pending can become rejected if the user has docs.approve
  rejected can become needs revision if the user has docs.edit
  needs revision can become pending if the user has docs.edit
```

### Self-transitions (valid):

A state can transition to itself. The runtime writes the same value back and
fires the event bus. Useful for scope-gated re-confirmation, re-submission, or
audit-trail actions where state does not change but the act of transitioning
matters.

```
Each order has a lifecycle which is state:
  lifecycle starts as pending
  lifecycle can also be processing or complete
  pending can become pending if the user has ops.resubmit
  pending can become processing if the user has ops.process
  processing can become complete if the user has ops.process
```

`"Resubmit" transitions lifecycle to pending if available` — fires the event
and records the transition even though the state value does not change.

### Action buttons for multi-SM content:

The field name is inserted between `transitions` and `to`. Multi-word field
names work — the parser reads words up to `to` (same `words_before_to`
negative-lookahead pattern used elsewhere in the grammar).

```
For each product, show actions:
  "Publish" transitions lifecycle to active if available
  "Discontinue" transitions lifecycle to discontinued if available, hide otherwise
  "Approve" transitions approval status to approved if available
  "Reject" transitions approval status to rejected if available
```

Multi-word states also work — the parser reads words up to `if`:
```
  "Start Work" transitions lifecycle to in progress if available
```

---

## 2. What's removed

### `State for X called "Y":` — gone

Remove the grammar rule entirely. Any source file using the old syntax fails
at the **parse stage** (not a semantic error — the rule simply doesn't exist).

### Affected grammar rules (all deleted):
- `state_machine_block` (top-level block starting with `State for`)
- `sm_initial_line` (`A {singular} starts as "{state}"`)
- `sm_also_line` (`A {singular} can also be ...`)
- `sm_transition_line` (`A {state} {singular} can become ...`)

### Action button rule change:
```
# Before
"{label}" transitions to "{state}" if available

# After
"{label}" transitions {field_name_words} to {state_words} if available
```

The old `transitions to` (with no field name) is also removed. With a single
state field on a content, you still name it explicitly. This is unambiguous and
consistent.

---

## 3. Grammar additions (`termin.peg` + `peg_parser.py`)

### Field type extension

`state` is added to the set of recognised field types. When the type is
`state`, the parser expects a `:` and a sub-block of state machine lines.

### New sub-block line rules

```peg
sm_starts_as  = field_name_words "starts" "as" state_ref
sm_also       = field_name_words "can" "also" "be" state_list
sm_transition = article? from_ref "can" "become" to_ref
                "if" "the" "user" "has" scope_ref

article          = /[Aa]n?/
field_name_words = /\w+(?:\s+(?!starts\b|can\b)\w+)*/
from_ref         = quoted_string / words_before_can
to_ref           = quoted_string / words_before_if
words_before_can = /\w+(?:\s+(?!can\b)\w+)*/
words_before_if  = /\w+(?:\s+(?!if\b)\w+)*/
state_ref        = quoted_string / bare_word
scope_ref        = quoted_string / bare_ref
state_list       = state_ref ("or" state_ref)*
```

**Discriminating the three sub-block line types:**

| Discriminator | Rule |
|---|---|
| contains `starts as` | `sm_starts_as` |
| contains `can also be` | `sm_also` |
| contains `can become` | `sm_transition` |

These keywords are mutually exclusive within state names — the analyzer
enforces this. See §7 for the full reserved keyword list.

**Line classification in `_classify_line`:** The two-level parser classifies
lines before TatSu. Add three new classifiers checked only when the parser is
inside a state sub-block (tracked as parser context state):

```python
if ctx.in_state_block:
    if "starts as" in line:
        return "sm_starts_as"
    if "can also be" in line:
        return "sm_also"
    if "can become" in line:
        return "sm_transition"
```

**Sub-block boundary rules:**

The sub-block starts on the line after `which is state:` and ends on the first
non-ignored line whose indentation is ≤ the content field's indentation level.

Lines that are **ignored for boundary purposes** (stripped in `_preprocess()`
before classification runs):
- Blank lines (zero non-whitespace characters).
- Parenthetical comment lines — lines whose entire non-whitespace content is
  wrapped in parentheses, e.g. `(this status tracks the review workflow)`. Add
  this stripping to `_preprocess()` alongside blank-line stripping.

Lines at **deeper indentation** than the sub-block are still in the sub-block.
The parser does not currently use deeper indentation for grouping — all
transition lines sit at the same level — but it should not error on extra
indentation. Treat it as the same level.

**Consequence:** a blank line or parenthetical comment between two `can become`
lines does not end the sub-block. An `Each product has ...` line at the
content-field indentation level does end it.

### Action button rule change

```peg
# Before
action_button_transitions = quoted_string "transitions" "to" state_ref availability

# After
action_button_transitions = quoted_string "transitions"
                            field_name_words "to" state_words availability

field_name_words = /\w+(?:\s+(?!to\b)\w+)*/   # words until 'to'
state_words      = /\w+(?:\s+(?!if\b)\w+)*/    # words until 'if'
```

---

## 4. AST changes (`termin/ast_nodes.py`)

`StateMachine` is **not removed** from the AST — it's still the container for
a state machine's data. What changes is where it's populated from.

```python
@dataclass
class StateMachine:
    content_name: str    # snake_case name of the owning content (unchanged)
    machine_name: str    # NOW: the field name ("lifecycle", "approval status")
                         # WAS: the quoted machine name ("product lifecycle")
    initial_state: str
    states: list[str]
    transitions: list[Transition]
```

`machine_name` was previously the quoted name from `State for products called
"product lifecycle"`. Now it's the field name from `Each product has a
lifecycle which is state:`. This is cleaner — the field name is the machine's
identity, its display label, and (snake_cased) its column name.

State machines are still collected into `program.state_machines`. The parser
pushes each inline state machine into this list as it encounters
`which is state:` field declarations during content block parsing.

`ActionButton` AST node gains a `machine_name` field (the field name as parsed
from the `transitions {field_name} to` clause):

```python
@dataclass
class ActionButton:
    label: str
    machine_name: str    # NEW — field name driving this transition
    target_state: str
    visibility: str      # "if available" / "hide otherwise"
    feedback: list       # existing
```

---

## 5. IR changes

### `StateMachineSpec` (`termin/ir.py`)

No structural change. The semantics of `machine_name` change:

```python
@dataclass(frozen=True)
class StateMachineSpec:
    content_ref: str          # snake_case content name (unchanged)
    machine_name: str         # NOW: snake_case field name ("lifecycle",
                              # "approval_status") — also the SQL column name
    initial_state: str        # unchanged
    states: tuple[str, ...]   # unchanged
    transitions: tuple[TransitionSpec, ...]  # unchanged
    primitive_type: str = "content"          # unchanged; runtime ignores non-content
```

Column name = `machine_name` directly. No derivation step anywhere.

### `ContentSchema` (`termin/ir.py`)

```python
@dataclass(frozen=True)
class ContentSchema:
    # ... existing fields ...

    # REMOVED:
    # has_state_machine: bool
    # initial_state: str

    # ADDED:
    state_machines: tuple[dict, ...] = ()
    # Each dict: {"machine_name": str, "initial": str}
    # machine_name is snake_case field name = SQL column name
```

JSON form (consumed by the runtime):

```json
"state_machines": [
  {"machine_name": "lifecycle",        "initial": "draft"},
  {"machine_name": "approval_status",  "initial": "pending"}
]
```

### `ComponentNode` props for `action_button`

The `action_button` component node gains `machine_name` in props:

```python
ComponentNode(
    type="action_button",
    props={
        "label":        PropValue("Publish", is_expr=False),
        "machine_name": PropValue("lifecycle", is_expr=False),    # NEW
        "target_state": PropValue("active", is_expr=False),
        "content":      PropValue("products", is_expr=False),
        "visibility":   PropValue("if_available", is_expr=False),
    }
)
```

### IR JSON schema (`docs/termin-ir-schema.json`)

Update `ContentSchema` definition:
- Remove `has_state_machine` and `initial_state` properties.
- Add `state_machines` array as shown above.
- Add clarifying description to `StateMachineSpec.machine_name`: _"Snake-case
  field name. Also used as the SQL column name by conforming runtimes."_

---

## 6. Lowering (`termin/lower.py`)

### Remove the overwriting dict

```python
# REMOVE — this is the bug site; the new structure makes it obsolete
sm_by_content: dict[str, StateMachine] = {}
for sm in program.state_machines:
    sm_by_content[sm.content_name] = sm  # last one wins — gone
```

Replace with a membership set for any remaining `if content has state machine`
guards:

```python
sm_content_names: set[str] = {sm.content_name for sm in program.state_machines}
```

### ContentSchema lowering

Collect all state machines for each content and emit the `state_machines` list:

```python
content_sms = [
    sm for sm in program.state_machines
    if sm.content_name == c.name  # c is the current Content being lowered
]
sm_specs = tuple(
    {"machine_name": _snake(sm.machine_name), "initial": sm.initial_state}
    for sm in content_sms
)
# ... build ContentSchema with state_machines=sm_specs
```

Note: `_snake(sm.machine_name)` is idempotent — machine_name is already the
field display name ("lifecycle", "approval status"), and `_snake` produces
"lifecycle", "approval_status". The snake form is what lands in the IR and
becomes the column name.

### Action button lowering

Read `machine_name` from the parsed `ActionButton` AST node and emit into props.

---

## 7. Analyzer additions (`termin/analyzer.py`)

1. **State-typed field has a sub-block.** Any `which is state` field must have
   an inline block with at least one `starts as` and at least one `can also be`
   (or the initial state counts as the only state). SemanticError if block is
   empty or `starts as` is missing.

2. **Exactly one `starts as` per machine.** Multiple `starts as` lines on the
   same field is a SemanticError. Multiple `can also be` lines are fine —
   they are accumulated.

3. **`starts as` value is in the states list.** The initial state named in
   `starts as` must appear in the accumulated `can also be` values, or be added
   implicitly. Recommended: add it implicitly and document that the `starts as`
   value does not need to be repeated in `can also be`. SemanticError if
   `starts as` names a value that conflicts with something — but no error for
   omitting it from `can also be`.

4. **Machine names unique per content.** Two state-typed fields on the same
   content must have distinct names (case-insensitive, before snake_casing).
   SemanticError if not.

5. **Reserved keywords in state names.** The following words may not appear
   as standalone tokens in a state name — they are grammar keywords within
   state sub-blocks and action button lines:

   `a`, `an`, `also`, `as`, `become`, `be`, `can`, `has`, `if`, `starts`,
   `the`, `to`, `user`

   SemanticError with message: `"'{word}' is a reserved keyword and cannot
   appear in a state name."` This resolves the `words_before_if` edge case:
   a state name can never contain `if` as a standalone word, so the
   negative-lookahead parser pattern is always safe.

6. **No column name collision.** A state field's snake_case name must not match
   any user-defined field's snake_case name on the same content.
   SemanticError if it does.

7. **Action button field name is valid.** The `machine_name` referenced in
   `transitions {field_name} to {state}` must be a state-typed field on the
   content being acted on. SemanticError if not found.

8. **Action button target state is reachable.** The `target_state` named in
   `transitions {field} to {state}` must appear in the transition table of the
   named machine as a `to_state`. SemanticError if not — catches typos at
   compile time.

9. **Self-transitions are valid.** `draft can become draft` is allowed.
   No guard against `(from, to)` where `from == to`. The analyzer does not
   treat this as an error or even a warning.

10. **No orphan state machines** (safety net). Every entry in
    `program.state_machines` must correspond to a content field. With the new
    grammar this is structurally guaranteed, but the check catches parser bugs.

---

## 8. Runtime changes

### `termin_runtime/storage.py`

```python
# BEFORE
if cs.get("has_state_machine"):
    initial = cs.get("initial_state", "")
    col_defs.append(f'"status" TEXT NOT NULL DEFAULT \'{initial}\'')

# AFTER
for sm in cs.get("state_machines", []):
    col = sm["machine_name"]          # already snake_case; also the column name
    initial = sm["initial"]
    _assert_safe(col, f"state column on {table_name}")
    col_defs.append(f'"{col}" TEXT NOT NULL DEFAULT \'{initial}\'')
```

`create_record` currently special-cases `"status"`:
```python
# BEFORE
d = {k: v for k, v in d.items() if v != "" or k == "status"}

# AFTER
state_cols = {sm["machine_name"] for sm in (sm_info or [])}
d = {k: v for k, v in d.items() if v != "" or k in state_cols}
```

`sm_info` is the list of SM specs for this content, passed from the caller
(same parameter already exists on `create_record`; update its contract).

### `termin_runtime/context.py`

```python
# BEFORE
sm_lookup: dict = field(default_factory=dict)
# content_ref -> {initial, transitions}

# AFTER
sm_lookup: dict = field(default_factory=dict)
# content_ref -> list[{machine_name, column, initial, transitions}]
```

### `termin_runtime/app.py`

```python
# AFTER
from collections import defaultdict
_sm_by_content = defaultdict(list)
for sm in ir.get("state_machines", []):
    col = sm["machine_name"]   # already snake_case
    trans_dict = {
        (t["from_state"], t["to_state"]): t.get("required_scope", "")
        for t in sm.get("transitions", [])
    }
    _sm_by_content[sm["content_ref"]].append({
        "machine_name": col,
        "column":       col,     # same as machine_name; column = machine_name
        "initial":      sm.get("initial_state", ""),
        "transitions":  trans_dict,
    })
ctx.sm_lookup = dict(_sm_by_content)
```

### `termin_runtime/state.py`

`do_state_transition` gains `machine_name` parameter. Looks up the correct SM
entry and reads/writes the correct column:

```python
async def do_state_transition(
    db, table: str, record_id: int,
    machine_name: str,          # NEW — snake_case field/column name
    target_state: str,
    user: dict,
    state_machines: dict,       # now dict[table -> list[sm_dict]]
    terminator=None, event_bus=None
):
    if table not in state_machines:
        raise HTTPException(400, f"No state machine for {table}")

    sm = next(
        (s for s in state_machines[table] if s["machine_name"] == machine_name),
        None
    )
    if sm is None:
        raise HTTPException(400, f"No state machine '{machine_name}' on {table}")

    column = sm["column"]
    record = await get_record_by_id(db, table, record_id)
    if not record:
        raise HTTPException(404, "Record not found")

    current = record.get(column, "")
    key = (current, target_state)
    if key not in sm["transitions"]:
        # ... route through terminator, raise 409
        pass

    required_scope = sm["transitions"][key]
    if required_scope not in user["scopes"]:
        # ... route through terminator, raise 403
        pass

    await update_fields(db, table, record_id, {column: target_state})

    updated = await get_record_by_id(db, table, record_id) \
              or {"id": record_id, column: target_state}

    if event_bus:
        await event_bus.publish({
            "channel_id": f"content.{table}.updated",
            "data": updated,
        })
    return updated
```

The TerminAtor `source` field becomes `f"state:{table}:{machine_name}"` for
finer-grained error routing.

### `termin_runtime/transitions.py`

**Route changes to `/_transition/{content}/{machine_name}/{record_id}/{target_state}`:**

```python
# BEFORE
@app.post("/_transition/{content}/{record_id}/{target_state}")
async def generic_transition(content: str, record_id: int, target_state: str, ...):
    from_state = record.get("status")
    result = await do_state_transition(db, content, record_id, target, user,
                                       ctx.sm_lookup, ...)

# AFTER
@app.post("/_transition/{content}/{machine_name}/{record_id}/{target_state}")
async def generic_transition(content: str, machine_name: str,
                             record_id: int, target_state: str, ...):
    from_state = record.get(machine_name)    # reads correct column
    result = await do_state_transition(db, content, record_id, machine_name,
                                       target, user, ctx.sm_lookup, ...)
```

**Transition feedback key gains `machine_name`:**

```python
# BEFORE
key = (sm["content_ref"], t["from_state"], t["to_state"])

# AFTER
key = (sm["content_ref"], sm["machine_name"], t["from_state"], t["to_state"])
```

`get_feedback` signature gains `machine_name` as second positional argument.
All three call sites (success path, error path, AJAX path) updated.

**AJAX response** currently returns `{"id": ..., "status": target}`. Change to
`{"id": ..., machine_name: target}` using the machine_name from the route.

### `termin_runtime/presentation.py`

**Action button renderer:**

```python
# BEFORE
machine = "status"
url = f"/_transition/{content}/{record_id}/{target_slug}"
current_state = record.get("status", "")

# AFTER
machine = props.get("machine_name", "")
url = f"/_transition/{content}/{machine}/{record_id}/{target_slug}"
current_state = record.get(machine, "")
```

**Edit modal state dropdowns:**

The edit modal must render **one `<select>` per state machine** on the content.
Currently it renders a single state dropdown keyed to `status`. The renderer
iterates `content_schema["state_machines"]` and renders one dropdown per entry,
each showing only the transitions valid for the current user from the current
state in that machine's column.

---

## 9. Error handling

| Scenario | Status | Detail |
|---|---|---|
| Content has no state machines | 400 | `"No state machine for {table}"` |
| Unknown `machine_name` in route | 400 | `"No state machine '{name}' on {table}"` |
| Transition not in table | 409 | `"Cannot transition from '{current}' to '{target}'"` |
| Insufficient scope | 403 | `"Transition requires scope: {scope}"` |
| Record not found | 404 | `"Record not found"` |

---

## 10. Existing example migration

All examples with state machines need mechanical migration. Eight files:
`warehouse`, `helpdesk`, `projectboard`, `compute_demo`, `headless_service`,
`hrportal`, `channel_demo`, `security_agent`.

**Pattern:**

```
# BEFORE (warehouse.termin)
State for products called "product lifecycle":
  A product starts as "draft"
  A product can also be "active" or "discontinued"
  A draft product can become active if the user has "catalog.manage"
  An active product can become discontinued if the user has "catalog.manage"

# AFTER — moved inside the Content block, field type = state
Content called "products":
  Each product has a name which is text
  ...
  Each product has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be active or discontinued
    draft can become active if the user has catalog.manage
    active can become discontinued if the user has catalog.manage
```

Drop the singular noun from transition lines (`draft product` → `draft`).

**`channel_demo.termin` special case:** the state machine was attached to a
channel. Find the content that channel carries; move the state machine to that
content as an inline state field. If there is no suitable content (the state
machine was genuinely on the channel infrastructure), create a minimal content
to hold it — or simplify the example to remove the state machine if it was
only there to exercise the old syntax.

After migration: regenerate all IR dumps (`ir_dumps/*.json`). The `status`
column is gone; state columns are named after fields.

---

## 11. New example (`examples/approval_workflow.termin`)

Exercises the primary v0.9 use case: two state machines on one content.

```
Application: Approval Workflow
  Description: Document lifecycle and approval management

Users authenticate with cookie
Scopes are "docs.edit", "docs.approve", and "docs.admin"
An "Editor" has "docs.edit"
An "Approver" has "docs.approve"
An "Admin" has "docs.edit", "docs.approve", and "docs.admin"

Content called "documents":
  Each document has a title which is required text
  Each document has a body which is text
  Each document has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be published or archived
    draft can become published if the user has docs.edit
    published can become archived if the user has docs.admin
  Each document has an approval status which is state:
    approval status starts as pending
    approval status can also be approved or rejected
    pending can become approved if the user has docs.approve
    pending can become rejected if the user has docs.approve
    rejected can become pending if the user has docs.edit
  Anyone with "docs.edit" can create, read, update, and delete documents
  Anyone with "docs.approve" can read documents

As an Editor, I want to manage documents so that I can publish content:
  Show a page called "Documents"
  Display a table of documents with columns: title, lifecycle, approval status
  Accept input for title and body
  For each document, show actions:
    "Publish" transitions lifecycle to published if available
    "Archive" transitions lifecycle to archived if available, hide otherwise

As an Approver, I want to review documents so that I can control content:
  Show a page called "Review Queue"
  Display a table of documents with columns: title, lifecycle, approval status
  For each document, show actions:
    "Approve" transitions approval status to approved if available
    "Reject" transitions approval status to rejected if available
    "Return for Revision" transitions approval status to pending if available
```

---

## 12. Conformance changes (`termin-conformance`)

### New test file: `tests/test_v09_multi_state_machine.py`

Categories (parametrized over `approval_workflow`):

1. **Schema** — `documents` table has columns `lifecycle` and `approval_status`,
   not `status`; each has the correct initial value; no `status` column exists.
2. **Independent transitions** — advancing `lifecycle` does not change
   `approval_status` and vice versa.
3. **Scope isolation** — `docs.edit` cannot drive `approval_status`;
   `docs.approve` cannot drive `lifecycle`. Both return 403.
4. **Transition validity** — invalid `(from, to)` pairs on each machine return
   409.
5. **Route format** — `POST /_transition/documents/lifecycle/1/published` returns
   200; old route `POST /_transition/documents/1/published` returns 404.
6. **AJAX response shape** — JSON response key is `lifecycle` or
   `approval_status`, not `status`.
7. **WebSocket push** — `content.documents.updated` event includes both state
   columns with correct values.
8. **Edit modal** — page HTML includes two state dropdowns (one per machine),
   each with the correct options for the current user's scopes.

### Updated tests

`test_v08_put_state_machine_gate.py` — all existing tests use the old route
`/_transition/{content}/{record_id}/{target}`. Update to the new route shape
`/_transition/{content}/{machine_name}/{record_id}/{target}`. Read the
`machine_name` from the example's IR dump.

Any conformance test asserting `record["status"]` — update to the
machine-specific column name.

---

## 13. Work order

Sequence that keeps tests green at each step. Write the failing test first at
every step.

1. **Grammar + parser** — add `state` field type, inline sub-block rules, new
   action button rule. Extend `_preprocess()` to strip parenthetical comment
   lines (entire non-whitespace content wrapped in parentheses) alongside
   existing blank-line stripping. Tests: new parser tests for inline state
   blocks; multiple `can also be` lines accumulate correctly; blank lines and
   parenthetical comments inside sub-blocks do not end the block; self-
   transition line (`draft can become draft`) parses correctly; old
   `State for X:` syntax is a parse error.

2. **AST** — `ActionButton` gains `machine_name`; `StateMachine.machine_name`
   is now field name. No new tests beyond confirming existing parser tests pass.

3. **Analyzer** — add all ten checks from §7. Tests: SemanticError for each
   invalid case (duplicate machine name, multiple `starts as`, reserved keyword
   in state name, column conflict, bad action button field reference, bad action
   button target state). Confirm self-transition (`draft can become draft`)
   passes the analyzer without error or warning.

4. **Lowering** — remove `sm_by_content` overwriting dict; update
   `ContentSchema` lowering; thread `machine_name` into action button props.
   Tests: IR tests asserting `state_machines` list shape on ContentSchema;
   action button component node has `machine_name` prop.

5. **Example migration** — convert all eight existing examples to inline state
   syntax; regenerate IR dumps. Tests: all existing IR tests still pass (they
   assert component structure, not column names — update any that do assert
   column names).

6. **Storage** — one column per SM entry; fix `create_record` state-column
   special-case. Tests: runtime tests asserting correct column names; initial
   values; no `status` column.

7. **Context + app init** — rebuild `sm_lookup` as list-of-dicts. No new
   tests; existing runtime init tests updated.

8. **State engine** — add `machine_name` param; correct column read/write.
   Tests: transition tests for each machine independently; wrong machine name
   returns 400.

9. **Transition route** — new route shape; feedback key update; AJAX response
   key fix. Tests: new route returns 200; old route returns 404; AJAX response
   carries correct key.

10. **Renderer** — action button uses `machine_name` prop; edit modal renders
    two dropdowns. Tests: HTML assertions for dropdown count and option values.

11. **New example** — add `approval_workflow.termin`; compile smoke test; add
    to IR dumps.

12. **Conformance** — new `test_v09_multi_state_machine.py`; update
    `test_v08_put_state_machine_gate.py`. Run full conformance suite green.

13. **IR schema** — update `docs/termin-ir-schema.json`; sync to conformance
    repo. Run conformance schema-validation tests.

---

## Appendix A: State vs other field types

### All field types

| Type | DSL form | Validation | Who controls the value |
|---|---|---|---|
| `text` | `which is text` | None | User — any string, freely |
| `number` | `which is a number` | Integer | User — any integer, freely |
| `currency` | `which is currency` | Decimal | User — any decimal, freely |
| `boolean` | `which is a boolean` | true/false | User — either value, freely |
| `date` | `which is a date` | ISO date | User — any valid date, freely |
| `timestamp` | `which is a timestamp` | ISO datetime | User — any valid datetime, freely |
| `automatic` | `which is automatic` | Server-set | Server only — user cannot write |
| `email` | `which is an email` | Format check | User — any valid email, freely |
| `url` | `which is a URL` | Format check | User — any valid URL, freely |
| `reference` | `which is a {content}` | FK integrity | User — any existing record ID, freely |
| `list of {type}` | `which is a list of text` | Type check | User — any list, freely |
| `one of` | `which is one of "a", "b", "c"` | Enum check | User — **any listed value, freely** |
| `state` *(v0.9)* | `which is state: [sub-block]` | Enum + transitions | Runtime — **only via transition endpoint** |

### Enum (`one of`) vs state — when to use which

`one of` is a **vocabulary**. The user can write any listed value in any order,
any time, to any other value. A product's category can jump from `electronics`
to `food` in a single PUT — nothing prevents it. It is a validated free-write.

`state` is a **workflow**. The user can only move from the current state to a
state that the transition table explicitly permits, and only if they hold the
required scope. The platform enforces the rules; the developer does not have to
remember to check them.

They share: both store a TEXT column with a fixed set of valid values.

They differ in everything else:

| | `one of` | `state` |
|---|---|---|
| Value progression | Free — any to any | Controlled — table-gated |
| Scope enforcement | None | Per-transition scope required |
| API surface | PUT field value | `/_transition` endpoint |
| Initial value | Whatever the user sends | Defined by `starts as` |
| Event on change | Only if a `When []:` trigger fires | Always — event bus on every transition |
| Transition feedback | None | Toast/banner messages per transition |
| Edit modal display | Single dropdown, all values | One dropdown per machine, valid next states only |

**Decision rule:** if "going backward" should be forbidden, or if different
users should control different progressions, or if you need an audit trail of
who moved the record through each stage — use `state`. If the value is
just a category or label that any authorized user can freely reassign — use
`one of`.

---

## Appendix B: Test-driven development plan

Tests are written **before** the implementation they cover. Each group below
maps to a work order step. Within each group, failure cases come first — they
are the tests most likely to expose real bugs.

All new tests target 95% line coverage on the code they exercise. Use
`python -m pytest tests/ --cov --cov-report=term-missing` to verify after each
step.

---

### Step 1 — Grammar + parser (`tests/test_parser.py`)

**New class: `TestStateMachineFieldType`**

*Failure cases — written first, all must fail before Step 1 is implemented:*

| Test | Setup | Asserts |
|---|---|---|
| `test_old_state_for_syntax_is_parse_error` | Source with `State for products called "x":` | `ParseError` raised |
| `test_state_block_without_starts_as_is_parse_error` | `which is state:` block with only `can also be` lines, no `starts as` | `ParseError` or empty `initial_state` caught downstream |
| `test_state_block_empty_is_parse_error` | `which is state:` with no sub-block lines | `ParseError` raised |
| `test_action_button_old_syntax_is_parse_error` | `"Publish" transitions to active if available` (no field name) | `ParseError` raised |

*Happy path — basic parsing:*

| Test | Setup | Asserts |
|---|---|---|
| `test_single_state_field_minimal_form` | One state field, no articles, no quotes | `len(program.state_machines) == 1`; `machine_name == "lifecycle"`; `initial_state == "draft"`; `states == ["draft", "active", "discontinued"]` |
| `test_single_state_field_full_form` | Same machine with articles and quotes everywhere | Parses identically to minimal form |
| `test_multi_word_field_name` | `Each document has an approval status which is state:` | `machine_name == "approval status"`; column snake-case = `approval_status` |
| `test_multi_word_state_names` | States `in progress`, `on hold`, `under review` | All three appear in `states` list; transition `in progress can become on hold` parsed correctly |
| `test_self_transition_parses` | `draft can become draft if the user has ops.confirm` | `TransitionSpec(from_state="draft", to_state="draft", required_scope="ops.confirm")` in transitions |
| `test_two_state_fields_on_one_content` | Two `which is state:` fields on `documents` | `len(program.state_machines) == 2`; each has correct `machine_name` and `content_name` |
| `test_action_button_with_machine_name` | `"Publish" transitions lifecycle to active if available` | `ActionButton.machine_name == "lifecycle"`; `ActionButton.target_state == "active"` |
| `test_action_button_multi_word_field_name` | `"Approve" transitions approval status to approved if available` | `ActionButton.machine_name == "approval status"` |
| `test_action_button_multi_word_target_state` | `"Start" transitions lifecycle to in progress if available` | `ActionButton.target_state == "in progress"` |

*Sub-block boundary tests:*

| Test | Setup | Asserts |
|---|---|---|
| `test_blank_line_inside_state_block_ignored` | Blank line between two `can become` lines | Both transitions parsed; sub-block not ended prematurely |
| `test_parenthetical_comment_inside_state_block_ignored` | `(tracks review workflow)` line inside state block | Comment stripped; transitions on either side both parsed |
| `test_parenthetical_comment_at_column_zero_ignored` | Same comment at zero indentation inside block | Same result — stripped before block detection |
| `test_multiple_can_also_be_lines_accumulate` | Three separate `can also be` lines | All states appear in `states` list |
| `test_starts_as_after_can_also_be` | `can also be` lines come before `starts as` in source | Parses correctly; `initial_state` is correct |

---

### Step 3 — Analyzer (`tests/test_analyzer.py`)

**New class: `TestStateMachineAnalyzer`**

*Failure cases — all must raise `SemanticError`:*

| Test | Setup | Expected error contains |
|---|---|---|
| `test_duplicate_machine_name_on_content` | Two state fields both named `lifecycle` on `products` | `"Duplicate"` / `"lifecycle"` |
| `test_duplicate_starts_as` | Two `lifecycle starts as` lines in one block | `"starts as"` / `"once"` |
| `test_reserved_keyword_if_in_state_name` | State named `waiting if ready` | `"if"` / `"reserved"` |
| `test_reserved_keyword_can_in_state_name` | State named `can proceed` | `"can"` / `"reserved"` |
| `test_reserved_keyword_as_in_state_name` | State named `draft as submitted` | `"as"` / `"reserved"` |
| `test_state_column_collides_with_user_field` | Content has both `Each product has a lifecycle which is text` and `Each product has a lifecycle which is state:` | `"collision"` / `"lifecycle"` |
| `test_action_button_references_nonexistent_machine` | `transitions nonexistent to active if available` on `products` | `"nonexistent"` / `"not a state field"` |
| `test_action_button_target_state_not_reachable` | `transitions lifecycle to typo if available` where `typo` is not in any transition's `to_state` | `"typo"` / `"not a valid"` |

*Happy path — must not raise:*

| Test | Setup | Asserts |
|---|---|---|
| `test_self_transition_is_valid` | `draft can become draft if the user has x` | No `SemanticError`; `TransitionSpec` present in output |
| `test_two_machines_different_names_valid` | `lifecycle` and `approval status` on same content | Compiles cleanly |
| `test_starts_as_value_implicit_in_states` | `lifecycle starts as draft` with no explicit `lifecycle can also be draft` | No error; `draft` appears in states list |

---

### Step 4 — Lowering / IR (`tests/test_ir.py`)

**New class: `TestStateMachineIRLowering`**

Tests are parametrized over `approval_workflow` (new example, two SMs) and
over a migrated single-SM example (e.g. `warehouse`).

*Single-SM content:*

| Test | Asserts |
|---|---|
| `test_content_schema_has_state_machines_list` | `cs.state_machines` is a tuple with one entry |
| `test_state_machines_entry_shape` | Entry has `machine_name` (snake_case) and `initial` keys |
| `test_no_has_state_machine_field` | `ContentSchema` has no `has_state_machine` attribute |
| `test_no_status_in_fields` | `cs.fields` contains no `FieldSpec` with `name == "status"` |
| `test_machine_name_is_snake_case_of_field` | Field `approval status` → `machine_name == "approval_status"` |
| `test_state_machine_spec_machine_name` | `StateMachineSpec.machine_name == "lifecycle"` (not `"product lifecycle"`) |

*Multi-SM content (parametrized over `approval_workflow`):*

| Test | Asserts |
|---|---|
| `test_two_state_machines_in_list` | `cs.state_machines` has exactly two entries |
| `test_both_machines_have_correct_initial` | `lifecycle` initial = `draft`; `approval_status` initial = `pending` |
| `test_no_overwriting` | Both machines present; neither silently dropped |

*Action button component nodes:*

| Test | Asserts |
|---|---|
| `test_action_button_has_machine_name_prop` | `ComponentNode(type="action_button")` props contain `"machine_name"` |
| `test_action_button_machine_name_is_snake_case` | `machine_name` prop = `"approval_status"` for `approval status` field |
| `test_two_action_buttons_different_machines` | Two buttons on same content page have distinct `machine_name` props |

---

### Step 5 — Example migration (update `tests/test_ir.py`)

No new test class. Update existing parametrized IR tests:

- Replace all `record["status"]` assertions with the field-specific column name
  (e.g. `record["product_lifecycle"]` for warehouse).
- Assert `has_state_machine` key is **absent** from content schema JSON.
- Assert `state_machines` key is **present** in content schema JSON.
- Verify `approval_workflow` compiles and its IR matches expected shape.

Gate: `termin compile examples/warehouse.termin` must succeed before this step
is declared done.

---

### Step 6 — Storage (`tests/test_runtime.py`)

**New class: `TestMultiStateMachineStorage`**

Uses `approval_workflow` IR fixture throughout.

*Failure cases:*

| Test | Setup | Asserts |
|---|---|---|
| `test_status_column_does_not_exist` | Create table from `documents` schema | `PRAGMA table_info(documents)` has no column named `status` |
| `test_create_record_without_state_column_value` | `create_record(db, "documents", {"title": "X"})` | Row inserted; `lifecycle == "draft"`, `approval_status == "pending"` (defaults) |

*Happy path:*

| Test | Setup | Asserts |
|---|---|---|
| `test_lifecycle_column_created` | Init DB with `approval_workflow` schema | `PRAGMA table_info(documents)` includes `lifecycle TEXT NOT NULL DEFAULT 'draft'` |
| `test_approval_status_column_created` | Same | Includes `approval_status TEXT NOT NULL DEFAULT 'pending'` |
| `test_two_state_columns_have_correct_defaults` | Insert row with no explicit state values | `SELECT lifecycle, approval_status` → `("draft", "pending")` |

---

### Step 8 — State engine (`tests/test_runtime.py`)

**New class: `TestDoStateTransitionMultiSM`**

*Failure cases — written first:*

| Test | Setup | Asserts |
|---|---|---|
| `test_unknown_machine_name_returns_400` | Call `do_state_transition(..., machine_name="nonexistent", ...)` | `HTTPException(400)` |
| `test_invalid_transition_returns_409` | Call transition `pending → draft` where rule does not exist | `HTTPException(409)` |
| `test_insufficient_scope_returns_403` | User has `docs.edit`; attempts `approval_status` transition requiring `docs.approve` | `HTTPException(403)` |
| `test_record_not_found_returns_404` | `record_id=9999` | `HTTPException(404)` |
| `test_content_with_no_state_machines_returns_400` | Content not in `sm_lookup` | `HTTPException(400)` |

*Happy path:*

| Test | Setup | Asserts |
|---|---|---|
| `test_lifecycle_transition_succeeds` | `draft → published` with `docs.edit` scope | Returns record; `record["lifecycle"] == "published"` |
| `test_approval_transition_succeeds` | `pending → approved` with `docs.approve` scope | Returns record; `record["approval_status"] == "approved"` |
| `test_machines_are_independent` | Transition `lifecycle`; check `approval_status` | `approval_status` unchanged after `lifecycle` transition |
| `test_self_transition_succeeds` | `pending → pending` with correct scope | Returns record; value unchanged; no exception |
| `test_event_bus_fires_on_transition` | Mock event bus; run transition | `event_bus.publish` called once with `channel_id == "content.documents.updated"` and updated record |
| `test_event_bus_fires_on_self_transition` | Same with self-transition | Event still fires |
| `test_correct_column_written` | Transition `lifecycle`; read raw row | `lifecycle` column updated; `approval_status` column untouched |

---

### Step 9 — Transition route (`tests/test_runtime.py`)

**New class: `TestTransitionRouteShape`**

*Failure cases:*

| Test | Setup | Asserts |
|---|---|---|
| `test_old_route_returns_404` | `POST /_transition/documents/1/published` | 404 |
| `test_unknown_content_returns_404` | `POST /_transition/ghost/lifecycle/1/published` | 404 |
| `test_unknown_machine_in_route_returns_400` | `POST /_transition/documents/nonexistent/1/published` | 400 |

*Happy path:*

| Test | Setup | Asserts |
|---|---|---|
| `test_new_route_lifecycle_returns_200` | `POST /_transition/documents/lifecycle/1/published` with `docs.edit` | 200; `response["lifecycle"] == "published"` |
| `test_new_route_approval_returns_200` | `POST /_transition/documents/approval_status/1/approved` with `docs.approve` | 200; `response["approval_status"] == "approved"` |
| `test_ajax_response_key_is_machine_name` | Same with `X-Requested-With: XMLHttpRequest` | JSON key is `lifecycle`, not `status` |
| `test_multi_word_state_in_route_underscore` | `POST /_transition/.../in_progress` | Underscore converted to space; transition `→ "in progress"` applied |
| `test_feedback_key_includes_machine_name` | Transition with feedback defined | Flash params present in redirect / AJAX response |

---

### Step 10 — Renderer (update `tests/test_e2e.py` + new browser tests)

**New class: `TestMultiSMPresentation`**

*Failure cases:*

| Test | Setup | Asserts |
|---|---|---|
| `test_action_button_url_contains_machine_name` | Render page with action buttons | `href` or `hx-post` contains `/_transition/documents/lifecycle/` not `/_transition/documents/` |
| `test_wrong_machine_button_not_shown_for_scope` | User has only `docs.edit`; render approval buttons | `approval_status` action buttons absent or disabled; `lifecycle` buttons present |

*Happy path:*

| Test | Setup | Asserts |
|---|---|---|
| `test_edit_modal_has_two_state_dropdowns` | GET edit modal for `documents` record | Two `<select>` elements; names/ids are `lifecycle` and `approval_status` |
| `test_lifecycle_dropdown_shows_valid_next_states_only` | Record in `draft`; user has `docs.edit` | `<select name="lifecycle">` options = `["published"]` only (not `archived`, not `draft`) |
| `test_approval_dropdown_shows_valid_next_states_only` | Record in `pending`; user has `docs.approve` | `<select name="approval_status">` options = `["approved", "rejected"]` |
| `test_self_transition_button_visible_when_permitted` | Record in `pending`; `pending → pending` transition defined with `ops.resubmit` scope; user has scope | Button rendered with correct route |

---

### Step 12 — Conformance (`termin-conformance/tests/`)

These tests run against the reference runtime via the conformance adapter.
Full specifications are in §12 of the main doc. Summary of test IDs for
tracking:

| ID | Category | File |
|---|---|---|
| `v09_sm_01` | Schema: no `status` column | `test_v09_multi_state_machine.py` |
| `v09_sm_02` | Schema: `lifecycle` column with correct default | same |
| `v09_sm_03` | Schema: `approval_status` column with correct default | same |
| `v09_sm_04` | Transition: lifecycle advances independently | same |
| `v09_sm_05` | Transition: approval advances independently | same |
| `v09_sm_06` | Scope isolation: `docs.edit` rejected on `approval_status` | same |
| `v09_sm_07` | Scope isolation: `docs.approve` rejected on `lifecycle` | same |
| `v09_sm_08` | Invalid transition returns 409 on each machine | same |
| `v09_sm_09` | New route format returns 200 | same |
| `v09_sm_10` | Old route format returns 404 | same |
| `v09_sm_11` | AJAX response key = machine name, not `status` | same |
| `v09_sm_12` | WebSocket push includes both state columns | same |
| `v09_sm_13` | Edit modal has two state dropdowns | same |
| `v09_sm_14` | Self-transition succeeds; event fires | same |
| `v09_gate_*` | All 8 existing gate tests with new route format | `test_v08_put_state_machine_gate.py` |

---

### Definition of done for the TDD plan — status as of 2026-04-24

The v0.9 multi-state machine feature is **complete**:

- [x] All tests in the plan above are written and green.
- [x] `python -m pytest tests/ -v` — **1562 passed, 1 skipped, 0 failed.**
- [x] `termin compile examples/approval_workflow.termin` succeeds.
- [x] `termin compile examples/warehouse.termin` succeeds (regression check).
- [x] All migrated examples compile (14 total — 13 originals + new
  `approval_workflow.termin`).
- [x] Conformance suite green via `util/release.py --skip-tests` —
  fixtures regenerated, IR schema synced, approval_workflow fixture
  in place.
- [x] No legacy `"status"` column reference remains in `termin_runtime/`
  state-machine code paths (helper functions and renderer fallbacks
  retain a documented `"status"` default for migration ergonomics on
  legacy IRs that pre-date v0.9 — these paths are inert when v0.9 IRs
  are loaded).
