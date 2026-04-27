# Changelog

## Unreleased — v0.9 in progress (feature/v0.9)

### Phase 3 slice (c): full access-grant grammar (2026-04-26)

Per resolved Q2 (JL pushed to ship the whole tool surface in
Phase 3 rather than splitting across phases),
docs/compute-provider-design.md §3.4 + §6 slice (c) lands the
four sibling access-grant lines on the Compute block:

```
Compute called "moderator":
  Provider is "ai-agent"
  Accesses messages
  Reads users, organizations
  Sends to "supplier alerts" channel
  Emits "moderation.flagged"
  Invokes "audit"
  Directive is ```...```
  Objective is ```...```
  Anyone with "x.write" can execute this
```

#### Grammar

`Reads`, `Sends to`, `Emits`, `Invokes` join `Accesses` as
sibling lines on Compute. PEG additions plus a shared
`quoted_or_word_list` rule for the three quoted-name lists
(channels, events, computes). Reads uses bare-word `accesses_list`
to mirror Accesses.

The `Sends to "<name>" channel` shape carries a trailing
`channel` keyword for disambiguation with other `to`
constructs.

#### AST + IR

`ComputeNode.{reads, sends_to, emits, invokes}: list[str]`
populated by the parser child-collector in `peg_parser.py`.
Lowering threads each through to the IR `ComputeSpec` as a
frozen tuple.

#### Analyzer rules

- **TERMIN-S044** — `Accesses ∩ Reads = ∅`. A content type
  appearing on both lines is a parse error: the grant is
  contradictory (Accesses already includes read access).
  Canonicalizes via lowercased-trim so case-different spellings
  still trigger.
- **TERMIN-S045** — `Reads <T>` where `<T>` is undefined Content.
  Fuzzy-match suggestion when applicable.
- **TERMIN-S046** — `Sends to "<C>"` where `<C>` is undeclared
  Channel.
- **TERMIN-S047** — `Invokes "<X>"` where `<X>` is undeclared
  Compute.
- `Emits` does not have a resolution rule — events are open in
  v0.9 (anything declared elsewhere or emitted ad-hoc).

#### ToolSurface construction

Per ai-agent compute, app startup builds a frozen
`ToolSurface(content_rw, content_ro, channels, events,
computes)` from the source-declared grants and stashes it on
`ctx.compute_tool_surfaces[<snake>]`. Slice (d)'s tool-dispatch
rewrite consumes this; slice (c) just builds it so the data is
ready.

The `_execute_tool` helper inside compute_runner's agent path
gains immediate use of `Reads` — read tools (`content_query`)
accept either Accesses or Reads as the source-side grant; write
tools (`content_create`, `content_update`) and state tools
(`state_transition`) require Accesses only. Per BRD §6.3.3
explicit: state tools come from Accesses, never from Reads.

#### Phase 4 split

Phase 3 ships the source-side grant grammar and the analyzer
rules. The runtime implementations of `channel.send`,
`channel.invoke_action`, `event.emit`, and `compute.invoke`
tools are Phase 4's territory — those need the channel contract
runtime which lands separately. Until Phase 4 lands, an agent
that source-declares `Sends to "X" channel` lowers correctly
and gates correctly; calling `channel.send` returns a
"contract not yet implemented" runtime error. The audit story
still works because the gate's denial path is structurally
identical.

#### Tests

`tests/test_v09_compute_grants.py` — 14 new tests across:
- Grammar / AST: each of the four grant kinds parses and lands
  on `ComputeNode`.
- Analyzer rules: S044 dual-grant, S045 reads-undefined-content,
  S046 sends-to-undefined-channel, S047 invokes-undefined-compute,
  plus disjoint-passes regression.
- IR lowering: ComputeSpec carries `reads`, `sends_to`, `emits`,
  `invokes`; computes without grants lower to empty tuples.
- ToolSurface construction: end-to-end create_termin_app builds
  ctx.compute_tool_surfaces entries with content_rw + content_ro
  + channels + events + computes split correctly per source
  declarations.

#### Tests: 2006 pass / 0 fail / 0 skip / 0 xfail.

All 14 examples compile clean.

### Phase 3 slice (b): runtime cut-over to compute provider registry (2026-04-26)

The "great cut-over" for v0.9 Phase 3 per
`docs/compute-provider-design.md` §6 slice (b). Routes compute
dispatch through the per-compute provider registry instead of
the singleton `ctx.ai_provider`; retires the v0.8-shape
top-level `ai_provider` deploy block in favor of the v0.9
`bindings.compute` keying layer.

#### `RuntimeContext` reshape

- **Removed:** `ai_provider: Any = None`. The single global
  AIProvider attached at app construction is gone.
- **Added:** `provider_registry`, `contract_registry`,
  `compute_providers: dict`. Per-compute provider instances live
  in `compute_providers` keyed by snake-name; only LLM/agent
  computes get an entry (default-CEL routes through
  `expr_eval`).

#### `app.py` provider resolution at startup

After identity and storage providers are constructed, the app
factory walks `ir.computes` and resolves any LLM/agent compute's
`bindings.compute["<snake>"]` entry through the registry:

```python
record = ctx.provider_registry.get(Category.COMPUTE, contract, product)
ctx.compute_providers[comp_snake] = record.factory(cfg)
```

Env-var interpolation of `${VAR}` placeholders happens at
factory-call time so providers see resolved secrets.

Fail-closed: an LLM/agent compute that source-declares
`Provider is "llm"` but has no `bindings.compute` entry is
silently skipped at runtime (matches v0.8 "AI provider not
configured" behavior). A binding pointing at an unregistered
product raises at startup with a clear error listing available
products.

The lifespan log at Phase 4b now reports per-compute provider
counts instead of a singleton service/model identifier:

```
[Termin] Phase 4b: 2 compute provider(s) bound: reply, summarize
```

or, when bindings are missing:

```
[Termin] Phase 4b: 1 LLM/agent Compute(s) have no deploy
binding and will be skipped: complete
```

#### `compute_runner.execute_compute` cut-over

The dispatch in `_execute_llm_compute` and
`_execute_agent_compute` now looks up `provider =
ctx.compute_providers.get(comp_snake)` instead of using
`ctx.ai_provider`. SDK calls flow through `provider.legacy`
— a transitional accessor on the Anthropic providers that
returns an embedded `AIProvider` instance configured for that
specific compute. Slice (d) ports the legacy logic into the
contract methods proper and deletes `.legacy`.

This keeps slice (b)'s diff small: prompt building, audit
writing, output write-back, and streaming-event publication
are byte-identical with v0.8. Only the *who-calls-the-SDK*
indirection layer changed.

#### Deploy config: hard-cut to v0.9 shape

Per design Q1 (resolved JL 2026-04-26: hard-cut, no
back-compat shim), the deploy template generator emits the v0.9
shape exclusively:

```json
{
  "version": "0.1.0",
  "bindings": {
    "identity":      { "provider": "stub",   "config": {} },
    "storage":       { "provider": "sqlite", "config": {} },
    "presentation":  { "provider": "default","config": {} },
    "compute": {
      "<compute-snake>": {
        "provider": "anthropic",
        "config": { "model": "...", "api_key": "${ANTHROPIC_API_KEY}" }
      }
    },
    "channels": { "<name>": { ... } }
  },
  "runtime": {}
}
```

The legacy top-level `ai_provider`, `channels`, `identity` keys
are no longer emitted. The deploy_config.py *parser* still
supports both shapes; only the generator changed. All six
example deploy configs in the repo regenerated to the v0.9
shape.

`ChannelDispatcher` reads from `bindings.channels` first,
falling back to top-level `channels` for one phase as a quiet
back-compat for unmigrated test fixtures.

#### CLI deploy template

`termin/cli.py:_generate_deploy_template` now constructs the
v0.9 `bindings.{identity,storage,presentation,compute,channels}`
nested shape. One entry per LLM/agent compute (CEL computes are
absent — implicit default-CEL contract).

#### Anthropic provider transitional accessors

Both `AnthropicLlmProvider` and `AnthropicAgentProvider` gain a
`.legacy` property that lazily constructs an internal
`AIProvider` from the per-compute config. Plus passthrough
properties (`is_configured`, `service`, `model`) so
compute_runner's ops-style log lines work unchanged.

This is documented as slice (b) interim. The proper port —
moving prompt building, tool schema construction, and SDK
calls *into* the contract methods (`complete`, `invoke`,
`invoke_streaming`) — happens in slice (d) alongside the
audit-record reshape.

#### Tests

- `tests/test_cli.py::test_compile_deploy_template_for_llm`
  rewritten to assert the v0.9 shape: no top-level
  `ai_provider`, `bindings.compute["<snake>"].provider ==
  "anthropic"`, `config.api_key` carries env-var placeholder.
- `tests/test_cli.py::test_generate_deploy_template` rewritten
  to assert the new `bindings.{identity,storage,presentation,
  compute,channels}` nesting.
- New `test_generate_deploy_template_compute_entries` confirming
  one entry per LLM/agent compute and that CEL computes are
  excluded.
- `tests/test_websocket_integration.py::test_ws_push_from_
  background_compute` mock deploy config rewritten to v0.9
  shape so the agent_simple compute resolves through the
  registry.

All other LLM/agent/streaming/manual-trigger tests pass
unchanged — the cut-over preserves call-shape end-to-end.

#### Tests: 1988 pass / 0 fail / 0 skip / 0 xfail.

#### What slice (b) deliberately defers

- **Source-level access grants** (Reads, Sends to, Emits,
  Invokes grammar) — slice (c). Tool-surface authorization in
  the agent loop still uses the existing `comp["accesses"]`
  field; ToolSurface remains empty until slice (c) populates it.
- **Audit record reshape to BRD §6.3.4** — slice (d).
  `write_audit_trace` keeps its v0.8 column shape.
- **Refusal / Acts as / AgentEvent translation in compute_runner
  streaming events** — slice (e). The `compute.stream.<inv_id>.*`
  events keep their v0.8 shape.
- **Deletion of `ai_provider.py` and `.legacy` accessors** —
  slice (d) port consolidates the SDK code into the contract
  methods, then this file goes away.

### Phase 3 slice (a): compute contract surface + provider modules (2026-04-26)

Lands the contract layer for v0.9 Phase 3 per
`docs/compute-provider-design.md`. **No behavior change yet** —
the runtime still uses `ctx.ai_provider`; this slice adds the
contract surface and registers the providers so slice (b)'s
cut-over has somewhere to land.

#### New: `termin_runtime/providers/compute_contract.py`

Three runtime-checkable Protocols matching BRD §6.3:
  - `DefaultCelComputeProvider` — pure CEL evaluation (synchronous,
    deterministic). Implicit contract.
  - `LlmComputeProvider` — single-shot prompt → completion. Async.
  - `AiAgentComputeProvider` — multi-action agent loop with closed
    tool surface. Async + `invoke_streaming` yielding `AgentEvent`
    variants.

Plus contract data shapes: `CompletionResult`, `AgentResult`,
`AgentContext`, `ToolSurface`, `AuditRecord` (BRD §6.3.4 shape),
`ToolCall`, `Cost`, `AuditableAction`, and the `AgentEvent` union
(`TokenEmitted | ToolCalled | ToolResult | Completed | Failed`).
Gate exceptions: `ToolNotDeclared` (TERMIN-A001), `NotAuthorized`
(TERMIN-A002).

`AuditRecord` validates outcome ∈ {success, refused, error} and
requires `refusal_reason` when refused, `error_detail` when error.

`ToolSurface` carries five tuples (`content_rw`, `content_ro`,
`channels`, `events`, `computes`) plus always-available tools
(`identity.self`, `system.refuse`). Slice (c) populates these
from source declarations; slice (a) just defines the shape.

#### New: five first-party provider modules

  - `compute_default_cel.py` — wraps the existing
    `ExpressionEvaluator`; registers as
    `(compute, "default-CEL", "default-cel")`.
  - `compute_llm_stub.py` — scripted-response stub for tests;
    matches directive+objective substrings against a configured
    response map. Registers as `(compute, "llm", "stub")`.
  - `compute_agent_stub.py` — scripted stub replaying tool call
    sequences through `context.tool_callback` (so the gate is
    exercised even with the stub). Registers as
    `(compute, "ai-agent", "stub")`.
  - `compute_llm_anthropic.py` — Anthropic Messages API
    single-shot completion. Lazy SDK client construction; clear
    error if `api_key` is unresolved (env-var interpolation
    must run before factory invocation). Registers as
    `(compute, "llm", "anthropic")`.
  - `compute_agent_anthropic.py` — Anthropic agent loop with
    `system.refuse` tool always available. Slice (a) ships
    minimal tool schemas (always-available tools only); slice
    (c) widens to source-declared tool surface. Registers as
    `(compute, "ai-agent", "anthropic")`.

Plus a shared `_provider_hash.py` helper implementing the
secret-redacted-then-hashed config-hash strategy (Q3 resolved):
canonical-JSON hash of the config dict with secret-shaped key
values replaced by their key paths. API-key rotation does not
change the hash; surrounding-config changes do.

#### Wired into `register_builtins`

`termin_runtime.providers.builtins.register_builtins(registry,
contracts)` now registers all five compute products in addition
to identity-stub and storage-sqlite. Slice (b) adds the
runtime dispatch through the registry.

#### New tests: 67 in two files

`tests/test_v09_compute_contract.py` — 33 tests covering
ToolSurface semantics, AuditRecord validation, outcome
constraints on CompletionResult / AgentResult, AgentEvent
variants, gate exceptions, AgentContext, and structural Protocol
conformance for all five built-in providers.

`tests/test_v09_compute_providers.py` — 34 tests covering
registry registration, factory output type, default-CEL
evaluation, stub-LLM scripted matches / refusals / errors,
stub-agent tool-callback wiring + scripted refusals + streaming
event emission, Anthropic construction-time behavior (without
hitting the SDK), and provider-config-hash redaction (10 tests
including nested-secret cases).

#### Tests: 1987 pass / 0 fail / 0 skip / 0 xfail.

#### Why this lands as its own commit

Slice (b)'s cut-over rewrites `compute_runner.execute_compute`
and deletes `ctx.ai_provider`; reviewing it as one diff would
mix "new contract layer" with "delete old path." Splitting
keeps each side reviewable on its own.

### Phase 2.x cleanup: retire legacy codegen + `?offset=` URL (2026-04-26)

**Two pre-v1.0 cleanups** flagged by JL after the (c)–(g) sweep:
ancient codepaths that lingered for backward-compat-with-self
and now go away cleanly because we have no v1.0 to break.

#### Removed: legacy `.py + .json` codegen path

The historical first-party "runtime" backend
(`termin.backends.runtime.RuntimeBackend`) emitted a slim `app.py`
shell that loaded a companion `.json` IR and called
`create_termin_app()`. Pre-v0.5 that was the only deploy shape;
since v0.5 `.termin.pkg` has been the canonical output and
`termin serve <pkg>` the canonical run command. The dual path
was never reconciled — until now.

  - `termin/backends/runtime.py` deleted.
  - `termin compile foo.termin -o app.py` now exits with a clear
    pointer at `.termin.pkg` + `termin serve` rather than silently
    switching modes.
  - `--legacy` CLI flag removed.
  - IR-serialization helpers (`_ir_json_default`, `_simplify_props`)
    extracted from `cli.py` into a shared `termin/ir_serialize.py`
    module. CLI and tests use the new `serialize_ir(spec) → str`
    entry point.
  - 8 test files migrated from "subprocess `compile -o app.py` +
    importlib load" to a uniform `make_app_from_pkg(pkg, db_path)`
    helper backed by the session-scoped `compiled_packages`
    fixture. Same as the v0.9 conformance pattern; finally one
    way to set up an app under test.

#### Removed: `?offset=N` URL parameter on auto-CRUD list routes

Phase 2.x (e) shipped keyset cursors and kept `?offset=N` as a
fetch-and-slice fallback for legacy callers. Pre-v1.0 we don't
need that fallback. The route now rejects `?offset=` with a 400
error pointing at `?cursor=`. Cursor-based pagination is the
single shape; cursors are opaque per the contract.

#### Tests: 1920 pass / 0 fail / 0 skip / 0 xfail

Each migration is a separate logical change so the diff is
auditable file-by-file. No production behavior change for users
who already deploy via `.termin.pkg`; users still on the legacy
`-o app.py` path get a clear migration message at compile time.

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
