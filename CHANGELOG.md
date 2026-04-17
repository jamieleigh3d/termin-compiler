# Changelog

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
