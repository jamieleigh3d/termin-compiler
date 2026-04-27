# Changelog

## Unreleased — v0.9 in progress (feature/v0.9)

### Phase 2.x (g): app.db cwd cleanup finishing pass (2026-04-26)

Closes the v0.9.x autouse-fixture-and-friends storage-isolation
work item flagged in the journal. Phase 2.x (b) paid down most
of this with the per-test `_isolated_test_db` fixture; this
commit lands the production-side ergonomics.

**db_path resolution precedence** (now fully documented and
tested):
  1. Explicit `db_path` argument to `create_termin_app()`.
  2. `TERMIN_DB_PATH` environment variable. Useful for ops
     pipelines pointing a deployed app at a specific path
     without editing the compiled app.py.
  3. `DEFAULT_DB_PATH` ("app.db" in cwd). Test conftest
     monkeypatches this; `python app.py` from a deploy dir
     gets the historical v0.8 behavior unchanged.

**Compiled `app.py` template additions:**
- Honors `TERMIN_DB_PATH` env var at module-import time so the
  env-driven path applies even when the module is imported by
  another launcher (e.g., uvicorn via FastAPI ASGI factory).
- New `--db-path` CLI flag for explicit override.
- The CLI flag wins over the env var, which wins over the
  default — same precedence as the runtime resolution.

**Tests:** 2 new in `tests/test_runtime.py::TestDbPathIsolation`:
  - `test_termin_db_path_env_var_used_when_no_explicit_db_path`:
    set TERMIN_DB_PATH, boot an app with no db_path, verify the
    env-pointed file is the one the runtime writes to.
  - `test_explicit_db_path_overrides_env_var`: with both set,
    verify only the explicit path is used.

**No breaking change.** Existing deployments running
`python app.py` from a deploy directory continue to use
`./app.db` exactly as before; the env var and CLI flag are
strictly additive.

Compiler: 1927 pass / 0 fail / 0 skip / 0 xfail.

### Phase 2.x (f): CEL → Predicate AST compiler (2026-04-26)

New module `termin_runtime/cel_predicate.py` compiles a CEL
expression string to a Predicate AST node — the runtime can hand
the result to any storage provider for SQL pushdown via the
existing predicate compiler. Per BRD §6.2:

> "the runtime compiles source-level CEL down to the AST and
>  evaluates the residual in-process."

This module is the source-level CEL → Predicate AST half. The
runtime catches `NotCompilable` to fall back to in-process
cel-python evaluation against fetched records (the contract's
"residual" half).

**Compilable CEL subset:**
- `<field> == <literal>` → Eq (literal: string, int, float, bool, null)
- `<field> != <literal>` → Ne
- `<field> > / < / >= / <= <numeric>` → Gt / Lt / Gte / Lte
- `<field> in [<lit>, ...]` → In
- `<field>.contains(<string>)` → Contains
- `<field> == null` → Eq(field, None) (compiles to SQL `IS NULL`
  via the Phase 2.x (d) predicate compiler enhancement)
- `<expr> && <expr>` → And
- `<expr> || <expr>` → Or
- `!<expr>` → Not

**NotCompilable cases** (runtime falls back to cel-python):
- arithmetic, ternaries, macros (`has`, `all`, `exists`)
- function calls beyond `.contains()`
- identifier references that aren't fields
- LHS of comparison must be a field; RHS must be a literal

**Optional `field_names` arg** to `compile_cel_to_predicate()` —
when supplied (typically the content's field set), identifiers
outside that set raise NotCompilable rather than producing a
predicate the storage provider would reject.

**End-to-end pushdown test**: a CEL filter `'status == "draft"
&& priority > 2'` compiled and handed to the SQLite provider
correctly filters records via the same SQL the Phase 2 query
contract emits.

**No runtime call-sites yet.** The compiler is forward-looking:
v0.9 has no .termin construct that produces CEL filter
expressions on stored rows — URL filter params still construct
Eq predicates directly. Future work (filter-by-CEL UIs,
agent-tool query strings, view definitions) will plumb this
in. Shipping it now means the contract layer is complete and
the runtime's fallback path is testable independently.

**Tests:** 24 in `tests/test_v09_cel_predicate.py`: every
compilable shape, NotCompilable fallback cases, field-name
validation, end-to-end pushdown via SqliteStorageProvider.

Compiler: 1925 pass / 0 fail / 0 skip / 0 xfail.

### Phase 2.x (e): keyset cursors (2026-04-26)

The SqliteStorageProvider's `query()` now uses keyset (seek-style)
cursors instead of the v0.9 stop-gap base64-of-offset shape.

**Cursor format** (opaque to callers — tests assert only that
`decode(encode(x)) == x` and that pagination round-trips produce
correct records):

  cursor = base64(JSON([sort_field_value..., id]))

The next-page query reconstructs an SQL row-comparison filter
that picks up records strictly after the cursor in the declared
ORDER BY direction. Mixed asc/desc directions are supported via
an OR-of-AND chain:

  (f1 > v1) OR
  (f1 = v1 AND f2 < v2) OR
  (f1 = v1 AND f2 = v2 AND id > id_val)

**Why keyset:**
- O(1) skip-ahead vs O(n) for OFFSET.
- Stable under concurrent inserts earlier in the result set:
  offset cursors duplicate or skip records when rows are inserted
  before the cursor; keyset cursors don't.
- `id` is automatically appended as the final sort key (also
  enforced for default order) so cursor uniqueness is guaranteed.

**Predicate compiler enhancement** (carried over from Phase 2.x d):
`Eq(field, None)` compiles to `IS NULL`; `Ne(field, None)` to
`IS NOT NULL`. The keyset filter benefits from this — NULL sort-
key values now pack into the cursor and roundtrip correctly.

**Legacy URL `?offset=N` preserved** in the auto-CRUD route
handler via fetch-and-slice. Callers using `?offset=` see no
behavior change. Callers can also pass `?cursor=` directly to
get the keyset-native path (mutual-exclusive with `?offset=`).

**Tests:** 7 new tests in `tests/test_v09_keyset_cursors.py`:
default-order pagination, custom-sort pagination, no-duplicates
across pages, empty-table → no cursor, exact-page-size → no
cursor, insert-during-pagination stability, mixed asc/desc
order. Cursor codec roundtrip + opacity in
`test_v09_storage_contract.py::TestCursorEncoding`.

Compiler: 1901 pass / 0 fail / 0 skip / 0 xfail.

### Phase 2.x (d): conditional update + state-machine routing (2026-04-26)

The Storage contract gains a CAS-style conditional update,
`update_if(content_type, id, condition: Predicate, patch)`, and
the runtime's state-machine engine now routes through it.
Concurrent transitions on the same record from the same source
state are no longer racy — exactly one CAS wins, the loser sees
HTTP 409 with the post-winner state.

**Naming and shape (JL review):** `update_if` (not
`compare_and_set` / `transition`). Reuses the existing Predicate
AST as the condition, so providers that already implement
`query()` get CAS for free. Returns a three-state `UpdateResult`:

```python
@dataclass(frozen=True)
class UpdateResult:
    applied: bool
    record: Optional[Mapping[str, Any]]  # post-update | current | None
    reason: str  # "applied" | "not_found" | "condition_failed"
```

The three-state result lets route handlers distinguish 404
("record not found") from 409 ("someone else already changed it"
— with the current record so the UI can show the actual state).

**SqliteStorageProvider implementation.** SQL pushdown via the
existing predicate compiler, single-statement
`UPDATE <table> SET <patch> WHERE id = ? AND <cond>`. `rowcount==0`
disambiguates `not_found` vs `condition_failed` via a follow-up
SELECT by id. Atomic per-call without needing a transaction.

**Predicate compiler enhancement.** `Eq(field, None)` now compiles
to `field IS NULL` (not `field = NULL`, which is always false in
SQL). Same for `Ne(field, None)` → `field IS NOT NULL`. This is
what enables the canonical claim-only-if-unclaimed shape:
`Eq("assignee", None)`. Also benefits `query()` callers.

**State machine routing.** `do_state_transition` now takes a
StorageProvider instead of a raw db connection. The function:
  1. Reads the current record (via `storage.read`)
  2. Validates the transition is declared and the user has scope
  3. Issues `storage.update_if(condition=Eq(column, current),
     patch={column: target})` — atomic CAS
  4. On `condition_failed`, surfaces "concurrent transition; record
     is now '<post-race-state>'" with HTTP 409

**Runtime call-site migration:**
- `routes.py` POST-transition route: passes `ctx.storage` instead
  of opening a raw db connection.
- `routes.py` PUT-with-state route: same.
- `transitions.py` user-facing transition endpoint: same; raw db
  connection removed from this path entirely.
- `compute_runner.py` agent-tool `state_transition`: same; service
  identity transitions go through the same atomic CAS.

**Tests:** 21 new tests in `tests/test_v09_update_if.py`:
applied/not_found/condition_failed paths; compound predicates
(And/Or/Not/Gt); state-machine canonical shape; double-transition
race detection; optimistic-concurrency-via-version; claim-only-if-
unclaimed (Eq with None); empty-patch-with-condition-gate; result
shape validation. Plus existing state-machine tests confirm the
migration is behavior-preserving (1893 pass / 0 fail / 0 skip on
the full suite).

### Phase 2.x (c): idempotency-key dedup for create() (2026-04-26)

The Storage contract's `create(content_type, record, *,
idempotency_key=None)` now actually honors the kwarg per BRD §6.2:

> "if supplied, second call with same key is a silent no-op
> returning the original record."

Phase 2 shipped the contract surface; Phase 2.x (c) lands the
SqliteStorageProvider implementation.

**Implementation** — a `_termin_idempotency` table mapping
`(content_type, key)` to the record id of the first successful
create. On replay, the provider fetches the underlying record by
id and returns it; the replay's payload is ignored. Entries are
lazy-created (no table until the first keyed create) and
isolated per content type (same key in two content types =
two separate idempotency contexts).

**Stale-entry cleanup at lookup time.** If a replay finds an
idempotency entry whose underlying record has since been deleted,
the entry is cleaned up and the call proceeds as a fresh insert.
This avoids returning a stale record id pointing at a deleted
row. Documented as the v0.9 retention policy (no TTL; future
versions may add one).

**No retroactive enforcement.** Existing v0.8 → v0.9 deployments
that never used the kwarg behave identically; the table
materializes on first use.

**Tests:** 9 new tests in `tests/test_v09_idempotency.py`
covering: no-key passthrough, first-call insert, replay returns
original (with payload-ignored verification), no duplicate row
in storage, distinct keys → distinct records, replay-after-delete
creates fresh, per-content-type isolation, lazy table creation.

### Phase 2.x (b): migration diff classifier (2026-04-26)

**The runtime now reads, diffs, classifies, gates, and applies
schema migrations.** Per BRD §6.2 and
`docs/migration-classifier-design.md`. Pairs with Phase 2.x (a)
cascade grammar — together they enable v0.8 → v0.9 cascade
migration as a first-class flow.

**Five-tier risk model.** Every diff entry is classified as:
- `safe` — applies at startup without operator interaction
- `low` — in-place ALTER, easily reversible; ack required
- `medium` — rebuild required but data preserved; ack + post-
  migration validation
- `high` — rebuild + semantics shift OR brief FK integrity break;
  **provider-side backup**, ack, and validation gate the COMMIT
- `blocked` — refuses the deploy unconditionally

**Operator acknowledgment via deploy config:**
```yaml
migrations:
  accept_any_risky: false      # blanket override (dev only)
  accepted_changes: []         # per-change fingerprints (audited)
  rename_fields: []            # operator-declared field renames
  rename_contents: []          # operator-declared content renames
```

**Field/content rename support.** Operator-declared mappings let
the differ fold a remove+add pair into a single `renamed` change
so data is preserved through `ALTER TABLE RENAME COLUMN` /
`RENAME TO` (or rebuild + cast for type-changing renames).
Without a mapping, remove+add stays remove+add and the standard
classification rules apply (`removed` is `blocked` until empty).

**TERMIN-M error codes** (new code class):
- `TERMIN-M001` — blocked migration
- `TERMIN-M002` — unack'd low/medium/high risk migration
- `TERMIN-M003` — validation step failed post-migration
- `TERMIN-M004` — backup creation failed or refused
- `TERMIN-M005` — rename mapping cycle / duplicate target
- `TERMIN-M006` — rename mapping target doesn't match IR shape

**Storage Protocol additions:**
- `read_schema_metadata()` — last-known-good schema (or PRAGMA
  introspection fallback for v0.8 → v0.9 first-boot)
- `write_schema_metadata(schemas)` — persists post-migration
- `create_backup() -> Optional[str]` — provider-specific backup;
  None signals fail-closed for high-risk
- Existing `migrate()` now handles add/remove/modify/rename with
  internal validation step before COMMIT

**SqliteStorageProvider implementation:**
- `_termin_schema` metadata table records every successful
  migration (id, ir_version, deployed_at, schema_json)
- Filesystem-copy backup with fsync + integrity_check
- 12-step table-rebuild dance for changes ALTER TABLE can't do
  (FK declarations, type changes, NOT NULL/UNIQUE/CHECK changes)
- `PRAGMA defer_foreign_keys = ON` for atomic-rollback safety
- Validation: FK check, row-count preservation, smoke read

**Backup retention.** When a high-risk migration commits, the
runtime emits a startup-log line naming the backup identifier and
notes that retention is the operator's responsibility. Different
providers have different primitives (filesystem path for SQLite,
snapshot ARN for cloud DBs) so v0.9 doesn't auto-clean. Document
provider-specific cleanup in your operations runbook.

**v0.8 → v0.9 cascade migration.** Existing v0.8 deployments now
migrate cleanly: the introspector reads the no-`ON DELETE` FK
declarations from the live DB; the differ flags every reference
field as `cascade_mode_changed` (high risk); the operator
acknowledges via `accepted_changes`; the provider runs the
table-rebuild dance with the new `ON DELETE CASCADE` /
`ON DELETE RESTRICT` clauses.

**Conformance test pack scope.** Compiler-side test pack ships in
this commit (64 tests covering classifier rules, rename folding,
empty-table downgrade, fingerprinting, ack, schema metadata,
backup, table-rebuild end-to-end). The cross-version migration
conformance pack (a "v0.8-shape DB → v0.9 IR" round-trip
fixture) needs additional fixture-generation infrastructure and
ships in a follow-on session.

**Tests:** compiler 1868 pass / 0 fail / 0 skip. All 14 examples
compile clean.

### Phase 2.x (a): cascade grammar (2026-04-26)

**Breaking grammar change.** Per BRD §6.2, every `references X` field
must now declare cascade behavior explicitly. Bare `references X`
fails compilation. The audit-over-authorship tenet says deletion
blast radius must be visible in source review, not inferred at
runtime.

**New syntax:**
- `references X, cascade on delete` — when the parent is deleted,
  this record is deleted alongside it (`ON DELETE CASCADE`).
- `references X, restrict on delete` — when the parent is deleted
  while any record references it, the delete is refused with HTTP
  409 (`ON DELETE RESTRICT`).

There is no implicit default. Both clauses are equally explicit.

**New compile-time errors:**
- `TERMIN-S039` — reference field missing cascade clause.
- `TERMIN-S040` — `cascade on delete` / `restrict on delete` declared
  on a non-reference field.
- `TERMIN-S041` — both `cascade on delete` AND `restrict on delete`
  declared on the same reference.
- `TERMIN-S042` — *transitive cascade-restrict deadlock*. A content
  cannot simultaneously be cascade-deleted from above AND
  restrict-protected from below; SQLite (and any FK-aware backend)
  aborts the cascade transaction. The compiler now rejects this
  structural defect at compile time, naming every contributing edge
  (file:line) and suggesting two resolutions: change the cascade edge
  to restrict, or change the restrict edge to cascade.
- `TERMIN-S043` — multi-content cascade cycle (`A → B → A` via
  cascade edges). Cycle behavior is backend-dependent and not
  portable. Self-cascade (a content referencing itself) is allowed
  for tree-delete semantics.

**IR change:** `FieldSpec.cascade_mode: "cascade" | "restrict" | null`.
Required when `business_type == "reference"`, null otherwise. The IR
JSON schema enforces the invariant structurally via `if/then/else`,
so any conforming runtime gets the check at validation time.

**Runtime change:** SQLite storage emits explicit `ON DELETE CASCADE`
or `ON DELETE RESTRICT` in `FOREIGN KEY` declarations, derived from
the schema-declared `cascade_mode`. Previously the FK was emitted
with no `ON DELETE` clause (SQLite default `NO ACTION`, which
behaves like RESTRICT at commit time).

**Examples migrated.** All 13 references-using fields across 7
example apps (`compute_demo`, `headless_service`, `helpdesk`,
`hrportal`, `projectboard`, `warehouse`) now carry explicit cascade
declarations:
- `helpdesk` comments → ticket: `cascade on delete` (comments are
  subordinate to their ticket).
- `warehouse` reorder alerts → product: `cascade on delete` (alerts
  are derived).
- All others: `restrict on delete` (deliberate cleanup required).
- `projectboard` redesigned to all-restrict (except `time logs →
  task` cascade) so the cascade graph passes the new S042 check;
  this is a product judgment for the example, not a compiler
  constraint.

**Migration story for existing deployments.** v0.8 `.termin` files
using bare `references` will fail to compile in v0.9 — add the
required clause to each. Existing v0.8 *databases* retain their old
FK declarations (no `ON DELETE` clause) under `CREATE TABLE IF NOT
EXISTS`; a redeploy on top of an existing app.db keeps the v0.8 FK
behavior. The full v0.8-DB → v0.9-cascade migration story (rebuild
tables to pick up the new `ON DELETE` clauses) is Phase 2.x (b)
territory and will land alongside the schema diff classifier per BRD
§6.2.

**Conformance suite:** new test pack `tests/test_v09_cascade.py`
(9 tests) plus 4 new fixtures in `fixtures-cascade/` covering both
modes, self-cascade, optional FK, and multi-hop chains.

**Tests:** compiler 1803 pass / 0 fail. Conformance 172 pass +
21 skipped (browser-only, `served-reference` adapter not active).

## v0.8.1 (2026-04-21)

### Theme: Maintenance release

Non-breaking patch release. Fixes release-artifact drift from the v0.8.0
tag, addresses three post-release GitHub issues, and documents the
process lesson that drove a v0.8.0 → v0.8.1 tag sequence. No new DSL,
IR, or runtime features. IR schema unchanged at 0.8.0.

### Release-artifact fixes
- **`docs/termin-ir-schema.json`**: added `edit_modal` to the
  ComponentNode type enum. The v0.8 #5 edit-action-button commit added
  the new component type to the conformance-side schema but missed the
  compiler's authoritative copy, so strict-validator adapters rejected
  every v0.8 warehouse app IR. Fixed in `bfb7633`.
- **`examples-dev/`**: new directory with README explaining what
  belongs there. `agent_chatbot2.termin` parked here — blocks promotion
  back to `examples/` on the PEG gap for the `Accesses <content>,
  <content>` line shape (logged under v0.8.2).

### GitHub issue responses
- **compiler#1** (857 TatSu fallbacks): unable to reproduce on Python
  3.11 / current main (0 fallbacks on both `main` and the cited commit
  `e64a537`). Commented on the issue requesting environment details
  (Python version, TatSu version, OS). If confirmed a real
  environment-dependent bug, fix ships in v0.8.1 once reproducer is
  available.

### Version
- Compiler: 0.8.0 → 0.8.1
- Runtime: 0.8.0 → 0.8.1
- IR: 0.8.0 (unchanged)

### Known issues (deferred to v0.8.2)
- PEG gap: `Accesses messages, products` line shape (fallback handles
  it correctly; fidelity-only)
- Stale `app_seed.json` between recompiles
- uvicorn `ws="websockets-legacy"` deprecation warnings
- Release script test-run step hangs under `capture_output=True` (tests
  actually complete; main process doesn't see subprocess exit promptly)

---

## v0.8.0 (2026-04-21)

### Theme: Action primitives + LLM streaming

First public-ready release. Ships a complete row-action DSL surface
(Delete / Edit / Inline edit), LLM streaming for both text completions
and tool-use agents, general-purpose streaming hydrator on the client,
and security-hardening fixes surfaced during the sprint.

### DSL (grammar + analyzer)
- **Delete action button**: `"Delete" deletes if available, hide/disable otherwise`
  — row-level delete primitive, scope-gated via the content's `can delete`
  rule. Analyzer error `TERMIN-S020` when declared without a matching rule.
- **Edit action button**: `"Edit" edits if available, hide/disable otherwise`
  — opens a modal dialog pre-populated from the row; saves via
  `PUT /api/v1/{content}/{id}`. State-machine fields render as a dropdown
  filtered to valid transitions by current state + user scopes. Analyzer
  error `TERMIN-S021`.
- **Inline edit**: `Allow inline editing of <fields>` — click-to-edit
  cells committing via single-field PUT. Analyzer errors `TERMIN-S022`
  (missing update rule), `TERMIN-S023` (unknown field), `TERMIN-S024`
  (state-machine column).

### Runtime
- **Auto-CRUD list-endpoint query params** on `GET /api/v1/<content>`:
  `?limit`, `?offset`, `?sort=<field>[:asc|:desc]`, `?<field>=<value>`
  equality filters. Validation rejects negative/non-integer values,
  unknown fields, and caps limit at 1000. SQL injection defense in
  depth via schema-lookup gate plus parameterization.
- **Manual compute trigger endpoint**: `POST /api/v1/compute/<name>/trigger`
  to fire any compute on demand regardless of declared trigger type.
- **PUT state-machine backdoor closed**: the auto-CRUD update route now
  detects state-machine-backed fields in the request body and routes
  them through `do_state_transition`, enforcing both the declared
  transition rules and the transition's `required_scope`. Atomic on
  failure — rejected state changes also reject companion field updates.
- **Delete FK-violation handling**: returns 409 with human-readable
  detail instead of 500 with a raw `sqlite3.IntegrityError`.
- **LLM streaming**: text mode (`AIProvider.stream_complete`) and tool-use
  mode (`AIProvider.stream_agent_response`) for both Anthropic and OpenAI.
  Agent-loop streaming (`agent_loop_streaming`) wires into the agent
  compute path so Level-3 agents (agent_chatbot) stream responses
  token-by-token. LLM-path streaming (agent_simple) reuses
  `stream_agent_response` so `set_output`-shaped Level-1 computes stream
  as well.
- **General streaming hydrator** (client-side `termin.js`): a single
  page-level subscription to `compute.stream.*` dispatches each field
  delta to every DOM element matching `[data-termin-row-id=<id>]
  [data-termin-field=<name>]`. Works for data_table cells, detail
  views, and any rendering that tags its elements with the standard
  attributes — no chat component required.
- **Event payload adds `content_name` + `record_id`** so any component
  can target the right DOM element without presentation-type coupling.
- **Deploy-config resolution**: CLI `serve` resolves the default
  `<stem>.deploy.json` from the `.pkg` filename (matching what the
  compiler writes); `load_deploy_config` also tries a digit-collapsed
  variant for names that snake-case differently from the source filename.

### Presentation / IR
- **New component type**: `edit_modal` with `field_input` children and
  embedded state machine info.
- **New `field_input` input type**: `state` — renders as a `<select>`
  pre-populated with all declared states, filtered client-side by
  valid transitions + user scopes.
- **`data-termin-*` attributes on every input and button** for
  behavioral testing via DOM selectors (no English-text matching).

### Streaming protocol
- New doc: `docs/termin-streaming-protocol.md` — two modes (text,
  tool-use), channel namespace
  `compute.stream.<invocation_id>[.field.<name>]`, event payload with
  invocation/content/record/field/delta/done fields, scope-gating
  requirements.

### Tests
- **Compiler**: 1399 → 1525 passing (1 skipped). +126 tests across the
  v0.8 backlog (pagination, filter/sort, manual trigger, delete, edit,
  inline edit, PUT backdoor, streaming).
- **Conformance**: 729 → 778 non-browser + 10 browser Playwright tests.
  New served-reference adapter for browser tests; remains opt-in
  (default `reference` adapter stays in-process for speed).

### Security posture
- Closed PUT-route state-machine bypass.
- FK-violation on DELETE returns 409 instead of 500.
- Analyzer errors for action buttons without matching access rules.

### Version
- Compiler: 0.7.1 → 0.8.0
- Runtime: 0.7.1 → 0.8.0
- IR: 0.7.0 → 0.8.0

### Release-process note
v0.8.0 was tagged before the release script's full artifact + test
pipeline was verified, which shipped stale `fixtures/ir/*.json` and a
compiler-side schema missing `edit_modal`. v0.8.1 ships the correction.
Lesson recorded in the v0.8.1 release checklist in
`docs/termin-roadmap.md`: run `util/release.py` + both test suites +
browser tests BEFORE tagging.

---

## v0.7.1 (2026-04-17)

### Theme: Conformance Debt Reduction

Patch release fixing conformance suite drift and one runtime behavior bug.
IR schema unchanged (still 0.7.0) — no fixture recompilation needed for
runtimes that already pass v0.7.0.

### Runtime Fixes
- **Transition endpoint error codes**: Non-AJAX requests without a `Referer`
  header (API clients, conformance tests) now receive the actual error status
  code from `do_state_transition` (409 for invalid transition, 403 for scope
  denial, 404 for missing record) instead of a 303 redirect. Browser form
  submits with `Referer` + `Accept: text/html` continue to redirect with
  `_flash` params for toast/banner rendering.
- **Transition on unknown content**: Returns 404 instead of leaking a SQLite
  `OperationalError: no such table` via 500.

### Compiler Fixes
- **Auto-generated audit content**: `compute_audit_log_{name}` content now has
  a non-empty `singular` field (defaults to the snake table name), fixing
  conformance validation that requires every ContentSchema to have a singular.

### Conformance Suite Fixes (bug report 011)
21 test drift issues fixed:
- Added AUDIT verb to valid_verbs in schema validation (6 tests)
- Warehouse access matrix updated for v0.7 access model (clerk cannot CREATE
  products; requires inventory.admin) (3 tests)
- Auto-CRUD paths use content snake names (underscores), not hyphens
  (`/api/v1/salary_reviews`, `/api/v1/stock_levels`, `/api/v1/reorder_alerts`)
  (11+ tests)
- Test helpers return `id` from create response instead of `sku` (routes use
  id lookup in v0.7) (5+ tests)
- Transition paths use `/_transition/{target_state}` format (3 tests)

### Version
- Compiler: 0.7.0 → 0.7.1
- Runtime: 0.7.0 → 0.7.1
- IR: 0.7.0 (unchanged)

### Stats
- 1,412 compiler tests pass
- 712 conformance tests pass (was 651 pre-fix, 66 failures → 0)
- 13 examples compile clean

---

## v0.7.0 (2026-04-16)

### Theme: Polish, Observability, Developer Experience

### Features
- **D-11: Auto-generated REST API** — Every Content gets CRUD at `/api/v1/{content}` automatically. Headless services (no user stories) fully supported. `Expose a REST API` syntax removed.
- **D-20: Agent observability** — AUDIT verb (fifth verb), auto-generated `compute_audit_log_{name}` Content per Compute, trace recording with audit levels (none/actions/debug), redaction in flight.
- **D-09: Chat presentation component** — New `chat` IR type. Not AI-specific — any Content with role+content fields. Integrated input, WebSocket subscription for live updates.
- **Transition feedback (#006):** Toast/banner notifications on state transitions. CEL interpolation with record fields, `from_state`, `to_state` context. Configurable dismiss timers.
- **Compound verb fix (#007):** PEG grammar now handles all verb combinations. Previously only `create or update` worked; all others silently dropped verbs.
- **Thread 009 fixes:** LLM prompt mapping (objective in system, not user turn), optional directive (no default injection), compiler-controlled thinking field in set_output.

### Security
- **SQL injection defense in depth:** Three layers — identifier validation at IR load (rejects unsafe names), proper quote escaping in `_q()`, centralized SQL in `storage.py`. Structural test prevents raw SQL outside storage.py.
- **TERMIN-S031 safety net:** Lowering raises `SemanticError` if any access grant has zero recognized verbs.
- **TERMIN-S032:** "api" is reserved as a page slug.

### Refactoring
- `app.py`: 2105 → 385 lines (8 modules: context, websocket_manager, boundaries, validation, compute_runner, transitions, routes, pages)
- `peg_parser.py`: 1345 → 273 lines (4 modules: classify, parse_helpers, parse_builders, parse_handlers)
- `lower.py`: 1096 → 745 lines (1 module: lower_pages)
- `channels.py`: 826 → 415 lines (2 modules: channel_config, channel_ws)

### IR (0.5.0 → 0.7.0)
- `TransitionFeedbackSpec`: trigger, style, message, is_expr, dismiss_seconds
- `TransitionSpec.feedback`: array of feedback specs per transition
- `Verb.AUDIT`: fifth verb alongside VIEW/CREATE/UPDATE/DELETE
- `ComputeSpec`: audit_level, audit_scope, audit_content_ref
- Chat component type in presentation IR

### Runtime
- Toast/banner rendering with auto-dismiss JS and AJAX-aware transition endpoint
- Chat rendering with scrolling message area, role-based bubbles, WebSocket live updates
- Auto-CRUD route generation from IR content schemas
- Audit trace recording with redaction based on caller scopes
- SQL identifier quoting for reserved words (e.g., "order")
- All SQL centralized in storage.py with validation

### Quality
- 356 compiler fidelity tests (IR property assertions per example)
- PEG grammar gaps closed (39 fallback paths → 0)
- 48 hard-coded string offsets replaced with `len("prefix")`
- Runtime coverage: 82% (up from 77%)

### Stats
- 1420 tests, 0 failures, 0 skips
- 13 examples, all compile cleanly
- ~12,000 lines of source, ~14,000 lines of tests (1.19:1 ratio)

---

## v0.6.0 "Boundaries" (2026-04-11)

### Theme: Enforcement, Quality, Cleanup

### Boundary Enforcement (Block C)
- **Implicit app boundary:** The app itself is always a boundary. Content not in any explicit sub-boundary lives in the implicit `__app__` boundary. No "unrestricted" mode.
- **Containment map:** Built at startup. Every content type and Compute is in exactly one boundary.
- **Channel-only crossing:** Cross-boundary access rejected with 403. `from` clause for explicit channel crossing deferred to v1.0.
- **Duplicate content check:** TERMIN-S030 — content cannot appear in multiple boundaries.

### Cross-Boundary Identity Propagation (C2)
- **Boundary identity_mode: restrict** enforced at CRUD level. Route scope grants API access; boundary scope gates entry to the boundary. Defense-in-depth.
- **Webhook scope enforcement** verified — inbound channels check caller's scopes before accepting data.

### Audit Levels (D-18)
- Three levels: `actions` (default), `debug`, `none` — pit of success design.
- `actions` logs event type, record ID, field names, identity, timestamp. Never field values.
- `audit` field on ContentSchema in IR.

### Dependent Field Values (D-19)
- **When clauses:** `When \`expr\`, field must be one of:` / `must be` / `defaults to`
- **Unified is-one-of:** Constraint modifier on any base type, not just enums
- **Runtime enforcement:** 422 on constraint violation, default application on create/update

### Other Features
- **G1:** Compute system type in CEL precondition context
- **G2:** Before/After snapshots for postcondition evaluation (ContentSnapshot class)
- **G5:** Runtime scheduler for `Trigger on schedule`
- **Structured compiler errors:** Error codes (TERMIN-S/X/W), fuzzy-match suggestions, `--format json`
- **WebSocket reconnect limit:** Max retries (default 3) instead of infinite retry

### Removals (No Backward Compatibility)
- **Legacy pyjexl backend** deleted (~2,100 lines). Only the runtime backend remains.
- **PageSpec + page_entry_to_pagespec** removed from IR — component tree is the only representation.
- All stale xfails removed. All skipped tests removed.

### Quality
- **Code coverage:** pytest-cov with 69% floor. v0.6 new code at 95.2%.
- **Compiler coverage:** 89%. **Runtime coverage:** 73%. Measured separately.
- **Conformance suite:** 671 tests covering HTTP, WebSocket, and Agent tool API.
- **Agent tool conformance:** `deploy_with_agent_mock()` adapter method. Tests content_query, content_create, access control through mock tool calls.
- **Test performance:** Module-scoped WS fixtures, DB isolation. 23% faster suite.

### IR Schema
- `ContentSchema.audit`: actions/debug/none
- `ContentSchema.dependent_values`: array of DependentValueSpec
- `FieldSpec.one_of_values`: field-level constraint
- `DependentValueSpec`: when, field, constraint, values, value
- `BoundarySpec.identity_scopes`: boundary restriction scopes

### Documentation
- Phase 0 roadmap: all items marked DONE
- Design decisions D-03, D-04, D-17, D-18, D-19 decided and documented
- Resolved decisions archived to `termin-roadmap-archive.md`
- IR version references synced (0.2.0/0.3.0 → 0.5.0)
- `termin-ir-spec.md` marked SUPERSEDED
- Sub-agent TDD instructions in CLAUDE.md

### Stats
- 690 tests in main repo, 671 in conformance suite = 1,361 total
- 0 failures, 0 skips, 0 xfails
- 12 examples, all compile cleanly

## v0.5.0 (2026-04-10)

### New DSL Features
- **AI Providers:** `Provider is "llm"` (Level 1 completion) and `Provider is "ai-agent"` (Level 3 autonomous agent)
- **Field Wiring:** `Input from field X.Y` / `Output into field X.Y` / `Output creates X` — explicit LLM input/output mapping
- **Accesses:** Required boundary declaration on all Computes — defines what content types a Compute can touch
- **Directive / Objective:** Two-part prompt system — Directive (system prompt, strong prior) and Objective (task prompt)
- **Trigger Where Clause:** `Trigger on event "X" where \`CEL expr\`` — event routing filter, distinct from preconditions
- **Mark...as:** `Mark rows where \`expr\` as "label"` — semantic emphasis with ARIA attributes, replaces Highlight
- **Channel Actions:** `Action called "name":` with typed Takes/Returns/Requires — RPC verbs on Channels
- **Event Channel Sends:** `Send X to "channel"` in When event handlers

### New IR Fields
- `ComputeSpec`: directive, trigger_where, accesses, input_fields, output_fields, output_creates
- `ComputeShape.NONE` for LLM/agent providers (no Transform shape required)
- `ContentSchema.singular` — authoritative singular from DSL (fixes pluralization)
- `ChannelSpec.actions` — typed RPC verbs with ChannelActionSpec
- `EventActionSpec.send_content/send_channel` — channel sends from events
- `semantic_mark` component type in presentation IR

### Runtime
- **Channel Dispatcher:** Outbound HTTP with retry + WebSocket with auto-reconnect
- **Inbound Webhooks:** `POST /webhooks/{channel}` — validates payload, creates content, fires events
- **Channel Actions:** `POST /api/v1/channels/{name}/actions/{action}` — typed RPC invocation
- **Channel Reflection:** `GET /api/reflect/channels` — live connection state and metrics
- **AI Provider:** Built-in Anthropic + OpenAI support with forced tool_use and thinking-first output schema
- **Event-Triggered Computes:** Automatic invocation when matching events fire, with where clause filtering
- **ComputeContext Tools:** content_query, content_create, content_update, state_transition — scoped to Accesses
- **Deploy Config:** App-specific `{name}.deploy.json`, auto-generated by compiler, strict validation at startup
- **AJAX Forms:** Form submit via fetch() keeps WebSocket alive for real-time updates
- **WebSocket Push Fix:** Background thread Computes correctly publish to main event loop

### Testing
- **Level 1:** Async WebSocket integration tests (real uvicorn + real WS client) — 8 tests
- **Level 2:** Browser automation tests (Playwright, data-termin-* selectors) — 5 tests
- **Level 3:** Behavioral WebSocket conformance specs — 5 tests
- **IR Schema Validation:** All fixtures validated against JSON Schema
- **Mock AI Provider:** Background thread test with mock LLM, verified against pre-fix code
- 501 tests in main repo, 475 in conformance suite

### Examples
- `agent_simple.termin` — Minimal LLM completion (prompt in, response out)
- `agent_chatbot.termin` — Conversational agent with multi-turn message history
- `channel_simple.termin` — Self-contained loopback demo (note → channel → webhook → echo)
- `channel_demo.termin` — All 6 channel patterns (inbound/outbound/bidirectional/internal/action/hybrid)
- `security_agent.termin` — Action channels + agent computes + event sends

### Design Decisions
- D-02: LLM field wiring (Input from / Output into / Output creates)
- D-05: Accesses required on all Computes, Transform shapes superseded
- D-08: Event envelope (hybrid — record promoted + event.* metadata)
- D-10: defaults to "user" verified working on enum fields
- D-12: Forced tool_use with thinking-first schema, Anthropic + OpenAI

### Documentation
- 19 design questions documented (D-01 through D-19)
- Deploy config schema: `docs/termin-deploy-schema.json`
- WebSocket behavioral contract in implementer's guide (§13.2)
- Retrospective: WebSocket sync bug root cause analysis

### Breaking Changes
- IR version bumped from 0.4.0 to 0.5.0
- `ComputeShape` enum adds `NONE` value
- `ContentSchema` adds required `singular` field
- Event publish key standardized to `"data"` (was inconsistent `"record"` vs `"data"`)

---

## v0.4.0 (2026-04-08)

- Expression delimiter: `[bracket]` → backtick
- Confidentiality system (Block B): field-level redaction, taint propagation, CEL guard
- AI agent grammar: Provider, Preconditions, Postconditions, Objective, Strategy, Trigger
- Transaction staging with snapshot isolation
- HR Portal example with confidentiality
- CEL types: User, Compute, Before, After
- 249 conformance tests
