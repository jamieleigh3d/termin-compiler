# Changelog

## v0.7.0 (unreleased)

### Theme: Polish, Observability, Developer Experience

### Compiler
- **Compound verb fix (#007):** PEG grammar now handles all verb combinations (`view or create`, `create, update, or delete`, etc.). Previously only `create or update` worked; all others silently dropped verbs. Security bug affecting 6/12 examples.
- **TERMIN-S031 safety net:** Lowering raises `SemanticError` if any access grant has zero recognized verbs after mapping.
- **Transition feedback (#006):** New DSL syntax for toast/banner notifications on state transitions. CEL or literal messages, configurable dismiss timers.
- **`--emit-ir` standalone:** When used without `-o`, dumps IR JSON and exits without building a package.

### IR (0.5.0 → 0.7.0)
- **TransitionFeedbackSpec:** New IR type for transition feedback (trigger, style, message, is_expr, dismiss_seconds).
- **TransitionSpec.feedback:** Array of feedback specs on each transition (empty when not declared).

### Runtime (0.3.0 → 0.7.0)
- **Toast/banner rendering:** `data-termin-toast` and `data-termin-banner` HTML elements with auto-dismiss JS. Flash data via `_flash` query params on redirect.

### Conformance
- **Verb completeness tests:** `test_access_grants_have_nonempty_verbs`, `test_access_grants_reference_declared_scopes`.
- **Transition feedback tests:** 14 tests for IR structure across all fixtures.

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
