# Termin Provider System — Business Requirements Document

**Version:** 0.9.0-draft (revised 2026-04-25 with JL's review answers)
**Status:** Formative — Identity, Storage, Compute, and Channels resolved. Presentation deferred to BRD #2.
**Audience:** Claude Code instances implementing the v0.9 provider subsystem; reviewers of the architecture; future authors of vetted providers.

**Revision log (2026-04-25):**
- §5.6 / §6.4: removed `Target is "<value>"` from source for all
  channels; targets/URLs/recipient lists live exclusively in deploy
  config. The leak-free principle (§5.1) now applies uniformly to
  channel targets, not just provider product names.
- §6.3.3: tool-grant grammar uses two clean lines
  (`Accesses <list>` for read-write, `Reads <list>` for read-only)
  rather than a per-item `read-only` modifier.
- §6.2: `references <type>` now requires explicit `cascade on delete`
  or `restrict on delete` — bare `references <type>` is a parse error.
- §8: boundary merge clarified as **key-level shallow merge** with
  leaf-wins on conflicting values.
- §10: every provider — including the built-in stubs — loads through
  the same provider registry. No special-cased "built-in" code path.
- §4: clarified primitives-vs-contracts distinction. Primitives stay
  closed (Tenet 4 audit promise); contracts are semi-open within
  each primitive's category — providers can register new contracts
  at runtime by declaring a body-line template (§5.3). The compiler
  owns parsing and hands the provider structured data; provider
  authors don't write parsers.
- §5.3: rewritten to make explicit that providers register a
  **template** describing their body-line shape, not a parser.
  The compiler dispatches sub-language content (CEL, LLM prompts,
  etc.) to the right downstream validator and hands the provider
  extracted, typed values.
- §8 + §11: future enhancement note for `final` markers on boundary
  config entries (enterprise-wide identity provider lockdown).
  Tracked as a v0.10 roadmap item.
- Appendix A: `hello_user.termin` migration uses the canonical
  scope-based form `Anyone with "app.view" can execute this`. The
  v0.8 bare-role form is removed in v0.9 (see termin-roadmap.md
  v0.9 backlog).

---

## 1. Context and Tenets

The Termin v0.9 milestone introduces the **provider system**: the open extension surface through which Termin runtimes integrate with environment-specific implementations of the eight primitives. The eight primitives are closed; the provider surface is open. This BRD specifies the contracts, grammar, deploy configuration, conformance model, and phased implementation plan for the provider system covering Identity, Storage, Compute, and Channels. Presentation is covered in a separate companion BRD due to scope (three customization levels, per-level conformance, distinct contract shape).

The provider system is the load-bearing architectural decision behind Tenet 4 — adoption depends on the provider surface being ergonomic, portable, and structurally bounded. The five tenets, ordered as a priority stack:

1. **Audit over authorship.** The bottleneck on enterprise software is review, not writing. We shrink the audit surface to pre-reviewed structural primitives, leaving only unique business logic for human scrutiny.
2. **Enforcement over vigilance.** Security, access control, and confidentiality are the platform's responsibility, not the developer's attention. Interlocking layers — grammar rules, runtime checks, conformance tests, and provider contracts — enforce what the spec declares.
3. **Audience over capability.** Termin source is reviewable by product managers, security reviewers, compliance officers, and domain experts — not only programmers.
4. **Providers over primitives.** The eight primitives are closed because the audit promise depends on them. The provider surface is open because adoption depends on that. Termin extends through new storage backends, identity systems, design systems, and compute providers — never through new primitives.
5. **Declared agents over ambient agents.** AI participates in Termin applications through typed channels, declared scopes, and audited actions — never as an unbounded caller.

---

## 2. Personas

Seven personas interact with the provider system. They may be the same human in small deployments and different humans (or organizations) in large ones; their interfaces are distinct.

1. **App author** — human + AI co-writing `.termin` files. Declares required contracts in source; never names products. Picks contract names from the catalog (e.g., `ai-agent`, `messaging`).
2. **Package author** — publishes redistributable Termin packages whose required contracts compose into a consuming app's required-provider set. Same source-level grammar as app author.
3. **Provider author** — Termin core team or third-party shipping a Storage / Compute / Presentation / Identity / Channel implementation against a versioned contract surface.
4. **Boundary administrator** — binds contracts to products at some level of the boundary tree, sets allowable provider sets, configures provider products. May be different humans at different levels (root admin, org admin, app deployer).
5. **Runtime operator** — runs a Termin runtime somewhere (an AWS-native runtime, Seedling on AWS, a homelab). Installs providers, advertises per-provider per-level conformance.
6. **Reviewer** — security, compliance, or domain expert. Reads source + effective boundary config as the audit envelope.
7. **The runtime itself** — loads, validates, and enforces contracts at the seam at deploy and at runtime.

---

## 3. Three Customization Levels (Preview)

Provider customization happens at three levels, most fully exercised by the Presentation contract but applicable across categories:

1. **Token / configuration swap** — provider implements the same contract surface with different parameters (color tokens, font choices for Presentation; storage paths for Storage).
2. **Component substitution** — provider replaces a default implementation with a custom one for an existing primitive operation (custom rendering for `data_table`; custom auth flow).
3. **Component extension** — provider adds new primitive-extension types beyond the default vocabulary (Airlock's `cosmic_orb` Presentation component; a domain-specific compute shape).

Per-level conformance advertisement is required (a provider may implement levels 1 and 2 but not 3). Full treatment in BRD #2.

---

## 4. Provider Categories Overview

**Primitives are closed; contracts are semi-open.** The eight primitive
categories (Content, Compute, Channel, Identity, Boundary, Reflection,
Presentation, Audit) are fixed by core spec — Tenet 4's audit promise
depends on the audit grammar locking onto exactly these primitives.
Adding a new primitive requires BRD evolution and a major spec release.

The **contracts within each category** are semi-open. The reference
runtime ships a fixed catalog of built-in contracts (one per implicit
category; three Compute, four Channel for the named categories), but
providers can register new contracts within an existing category at
runtime. The motivating case is Presentation: a Carbon-style
provider may register a new presentation contract whose body lines
have a different shape than the default. **The provider declares
the shape via a template** (see §5.3 three-kinds-of-params model);
the compiler does the parsing and hands the provider extracted,
structured data. Provider authors don't write parsers.

A new contract does NOT extend the primitive — it adds a new shape
WITHIN one primitive's category. The structural audit surface stays
the same: every contract still binds to one of the eight primitive
categories.

| Category | Contract Naming | Source Declaration | Tier | Built-in Contracts |
|---|---|---|---|---|
| **Identity** | Implicit | `Identity:` block (scopes + roles + Anonymous) | **Tier 0** — fabric down if down | (single contract surface) |
| **Storage** | Implicit | Use of `Content called` | **Tier 1** — app down if down | (single contract surface) |
| **Compute** | Named in source for non-default | `Compute called`, `Provider is "X"` | **Tier 1** — app down if down | `default-CEL`, `llm`, `ai-agent` (extensible) |
| **Channels** | Named in source | `Channel called`, `Provider is "X"` | **Tier 2** — integration degraded only | `webhook`, `email`, `messaging`, `event-stream` (extensible) |
| **Presentation** | Implicit | Use of `Display`, `Show`, etc. | **Tier 1** — app down if down | (deferred to BRD #2; extensible) |

**Tier classification rules:**

- **Tier 0** — provider outage takes down the entire AppFabric (every app on the runtime). Operational requirement: HA, monitored, paged on outage. Identity is the only Tier 0 category.
- **Tier 1** — provider outage takes down apps that depend on it; fabric stays up. Storage, Compute, Presentation.
- **Tier 2** — provider outage degrades a specific integration; app keeps running. Channels.

---

## 5. Source Grammar

### 5.1 The leak-free principle

**Product names never appear as bare tokens in source.** Source names contracts (where named at all); deploy config binds contracts to products. The same `.termin` source deploys unchanged across environments (local-dev, beta, integration, production); only the deploy config differs.

### 5.2 Inferred vs named contracts

Using a primitive in source implies a contract requirement. Categories split into:

**Implicit contract requirements** (no source-level naming):
- Identity — implied by presence of any non-anonymous role in the `Identity:` block
- Storage — implied by any `Content called` block
- Presentation — implied by any `Display`/`Show` clause
- Default-CEL Compute — implied by any inline backtick CEL expression
- (Internal real-time channels — auto-handled by the distributed runtime; no contract declaration)

**Explicit contract requirements** (named in source via `Provider is "X"`):
- Non-default Compute (`llm`, `ai-agent`, `geospatial`, `financial`, etc.)
- Channel (`webhook`, `email`, `messaging`, `event-stream`)

### 5.3 The three-kinds-of-params model

**The compiler owns parsing.** Providers do NOT implement parsers
or ASTs for their body lines. Instead, a provider registers a
**template** with the compiler describing the shape of each body
line in its contract — what tokens are expected, which positions
hold symbol-references, which hold literals, which hold backtick
content. The compiler parses the source against that template and
hands the provider already-extracted, structured data.

This is the audit-over-authorship tenet (Tenet 1) applied to the
provider seam: a provider author should describe *what* their
contract's source shape looks like, not implement *how* to parse it.

Provider declarations in source use three kinds of parameters with
distinct validation paths:

1. **Symbol-references** (e.g., `Input from field <path>`,
   `Trigger on event <name>`) — the provider's template names the
   slot and its expected referent type (field, event, content,
   compute, channel). The host compiler resolves the symbol against
   the AST at parse time and hands the provider a typed reference.
2. **Backtick-content** (e.g., `Directive is \`\`\`...\`\`\``, the CEL
   body) — the compiler tokenizes (extracts text between backticks
   or triple-backticks). The provider's template declares the
   expected sub-language so the compiler can dispatch downstream
   validation (CEL: hand to cel-python; LLM prompts: hand to the
   LLM provider's prompt validator if any). The provider receives
   the extracted, sub-language-validated string — never raw source
   bytes it has to re-tokenize.
3. **Literals** (numbers, enums, strings) — the template declares
   the literal kind and any allowed values (enum) or numeric range.
   The compiler enforces; the provider receives a typed value.

A provider's template is itself the source of truth for body-line
shape — not a separate parser. The compiler discovers templates
from the registered provider's metadata at startup and incorporates
them into the grammar dispatch table. Adding a new contract /
provider therefore requires:

  - Declaring the contract surface (existing `ContractDefinition`).
  - Registering the body-line template alongside the provider
    factory at registration time.
  - Implementing the runtime behavior for the contract's operations
    (e.g., `authenticate` / `roles_for` for identity).

There is no provider-side PEG, no provider-side AST type to
implement, and no provider-side string parsing.

`Provider is "<name>"` always lives in an indented sub-block under
the primitive that needs it. Never as a top-level keyword.

### 5.4 Identity block

Replaces the v0.8 top-level `Users authenticate with X` / `Scopes are` / `<role> has` lines. Single block holds all of identity-related declarations:

```
Identity:
  Scopes are "inventory.read", "inventory.write", and "inventory.admin"
  A "warehouse clerk" has "inventory.read" and "inventory.write"
  A "warehouse manager" has "inventory.read", "inventory.write", and "inventory.admin"
  An "executive" has "inventory.read"
  Anonymous has "inventory.read"
```

`Anonymous` is a built-in keyword, not `An "anonymous"`. The block is required even for anonymous-only apps (single line: `Anonymous has "<scope>"`).

The `Users authenticate with X` line is **removed entirely** from grammar. Authentication is implied by the presence of any non-Anonymous role.

### 5.5 Compute block (unchanged from v0.8 except for context)

```
Compute called "reply":
  Provider is "ai-agent"
  Accesses messages
  Trigger on event "message.created" where `message.role == "user"`
  Directive is ```...```
  Objective is ```...```
  Anyone with "chat.use" can execute this
  Audit level: actions
```

### 5.6 Channel block (new in v0.9)

**No targets, URLs, or product-internal addresses appear in source.** A
channel name in source is a logical identifier that the deploy config
binds to a per-environment target. The `Channel called "<name>"` line
gives deploy config a key; everything else (Slack channel name, webhook
URL, email recipient list, inbound subscription target) lives under
that key in the deploy config. This keeps the same source deployable
across local-dev / beta / production unchanged.

External egress example:

```
Channel called "supplier alerts":
  Provider is "messaging"
  Anyone with "warehouse.admin" can invoke this channel

  When reorder alert created:
    Send a message: `"⚠️ " + alert.product.name + " low at " + alert.warehouse`
```

The deploy config supplies the target:

```json
"channels": {
  "supplier alerts": {
    "provider": "slack",
    "config": { "target": "supplier-team-prod" }
  }
}
```

External egress with named actions (always nested under the Channel —
the `Action called` token is only valid inside a Channel block):

```
Channel called "incident bot":
  Provider is "messaging"

  Action called "alert":
    Anyone with "incident.manage" can invoke this
    Takes incident : incident
    Send a message: `"🚨 " + incident.title + " — severity " + incident.severity`

  Action called "resolve":
    Anyone with "incident.admin" can invoke this
    Takes incident : incident
    Send a message: `"✅ " + incident.title + " resolved"`
```

Inbound:

```
Channel called "support inbound":
  Provider is "messaging"

  When a message is received:
    Create a "support ticket" with body = message.text and reporter = message.sender
```

The inbound subscription target (chat channel name, webhook listener
path, IMAP folder, etc.) lives in deploy config as
`config.subscription` or equivalent provider-specific key. Source
declares only the channel's logical identity and the inbound trigger
shape.

### 5.7 Removed grammar

- `Users authenticate with X` — removed; Identity block subsumes
- `Stream <X> at <path>` — removed; internal real-time handled by distributed runtime auto-generation
- Section divider comments (`--- Identity ---`) — these were comment sugar in examples, no grammar impact

---

## 6. Contract Catalog

### 6.1 Identity contract

Single contract surface. Implicit from source.

```
authenticate(credentials) -> Principal
roles_for(principal, app_id) -> Set<RoleName>
```

**Principal record:**
```
Principal:
  id : text                         (stable identifier, never changes)
  type : "human" | "agent" | "service"
  display_name : text
  claims : map<text, any>           (open map, no required keys including email)
  on_behalf_of : Principal | null   (delegation chain)
```

**Provider configuration (deploy config):**
```
identity:
  provider : text                   (product name)
  config : map<text, any>           (provider-specific)
  role_mappings : map<RoleName, List<ExternalGroupRef>>
```

**Behavioral requirements:**
- **Anonymous bypasses the provider entirely.** No-credentials requests never call `authenticate`; runtime treats as Anonymous principal directly.
- **Failure mode: fail closed.** When the identity provider is unreachable, no roles can be resolved, and only Anonymous-permitted operations succeed. Identity is Tier 0; the AppFabric depends on it.
- **Fail-open with cache** is permitted as an opt-in provider config option but is NOT the contract default. Reviewers must be able to see the failure posture in the deploy config.
- **Mid-session role changes MUST be enforced.** The runtime authorizes against the freshest roles the provider can supply. TTLs, webhooks, push vs pull are implementation details; stale roles are a contract violation.
- **Multi-role principals are first-class.** A principal may have multiple roles simultaneously; effective scopes are the union. The runtime computes `roles × source-declared role-to-scope mapping` to derive scopes.
- **Service principals** (`type: "service"`) carry their own roles via `role_mappings`. **Agent principals** in delegate mode have no roles of their own — authorization derives from `on_behalf_of`. In service mode, agents have their own roles, no `on_behalf_of`.

**Caching, refresh cadence, and change-propagation mechanism are runtime/provider implementation details, not contract concerns.**

### 6.2 Storage contract

Single contract surface. Implicit from source.

```
create(content_type, record, idempotency_key?) -> id
read(content_type, id) -> record | null
query(content_type, predicate, options) -> Page<record>
update(content_type, id, patch) -> record
delete(content_type, id, cascade_mode) -> bool

migrate(schema_diff) -> void  (admin-only, runtime-managed)
```

**Predicate language (provider-required vocabulary):**

Providers implement a fixed predicate AST, not raw CEL:

```
Predicate:
  | Eq{field, value}
  | Ne{field, value}
  | Gt{field, value} | Gte{field, value}
  | Lt{field, value} | Lte{field, value}
  | In{field, values}
  | Contains{field, substring}     (case-sensitive substring match)
  | And{predicates}
  | Or{predicates}
  | Not{predicate}
```

The runtime compiles source-level CEL predicates down to this AST where possible. CEL expressions exceeding the AST's expressiveness are split: the runtime issues the largest pushable subset to the provider, evaluates the residual CEL in-process on the candidate set. One CEL evaluator (cel-python) lives in the runtime; no provider needs CEL knowledge.

Future optimization (post-v0.9): providers MAY advertise a `pushdown_capabilities` flag indicating native CEL support, allowing the runtime to skip residual evaluation.

**Query options:**
```
options:
  limit : whole number              (default 50, max 1000)
  cursor : opaque text | null       (cursor-based pagination — no offset)
  order_by : List<{field, direction}>

Page:
  records : List<record>
  next_cursor : text | null
  estimated_total : whole number | null  (provider-optional)
```

Sort stability: if `order_by` doesn't include a unique field, runtime appends record `id` as final sort key.

**Type model (abstract types the contract handles):**
- `text`, `unique text`
- `whole number` (with `minimum`, `maximum`)
- `currency`
- enum (`one of: "<value>", ...`)
- `references <other content>` — **explicit cascade declaration required**:
  `references <other>, cascade on delete` or `references <other>, restrict on delete`.
  A bare `references <other>` line is a parse error in v0.9. Cascade is the safer
  default semantically (referential integrity) but is too consequential to be
  implicit; the audit-over-authorship tenet says deletion blast radius must be
  visible in source review, not inferred.
- `automatic` (timestamps)
- `state` (with declared transitions)
- nested record types

Providers translate to native types and round-trip values faithfully.

**Transactional semantics:**
- Contract requires single-record atomicity only.
- Multi-record consistency is the runtime's responsibility via at-least-once event delivery + idempotency keys.
- The Storage contract's `create` operation accepts an optional `idempotency_key`. Second create with same key is a silent no-op returning the original record.
- Internal `event_log` Content type is runtime-managed; provider doesn't know it's special.

**Cascade and blast radius:**
- Source MUST declare cascade behavior explicitly per reference:
  `cascade on delete` or `restrict on delete`. There is no implicit
  default — a bare `references <type>` line fails compilation.
- Runtime computes blast radius before human-initiated deletes by walking the static reference graph and querying the provider for reference counts. UI surfaces the count for confirmation.
- Programmatic deletes (agent / service principals) skip the UI confirmation but log the blast radius in the audit trail.
- Provider's job is small: support cascading deletes when told to. Blast-radius computation is runtime, using `query` + the static graph.

**Schema migration:**
- Triggered when an app deploys with an existing app ID and a changed schema.
- Runtime computes the diff and classifies each change:
  - **Safe** — silent execution (add nullable field, add field with default, add new Content type, add new index)
  - **Risky** — explicit per-change confirmation required (drop field, drop Content type, change field type, add required field without default, narrow an enum)
  - **Blocked** — deploy fails immediately, before any migration starts (e.g., unique constraint that fails on existing data)
- After confirmation, runtime issues `migrate(diff)` to the provider in a single transaction.
- On any failure, the provider rolls back; deploy fails; previous version stays live.
- On success, the provider commits; new version goes live.
- Providers that cannot honor atomic migrations declare non-conformance for migration; runtime refuses to deploy schema changes against them.
- Deployed schema is recorded in a runtime-managed metadata table within the provider itself.

### 6.3 Compute contracts

**Three built-in Compute contracts:**

| Contract | Source signature | Purpose |
|---|---|---|
| `default-CEL` | (no `Provider is` clause) | Pure expression evaluation. Synchronous, deterministic. |
| `llm` | `Provider is "llm"` + `Input from field X` + `Output into field Y` | Single-shot prompt → completion. Transform shape with model-based body. No tool surface. |
| `ai-agent` | `Provider is "ai-agent"` + `Accesses ...` | Multi-action autonomous behavior with closed tool surface. Streamable. |

**Compute shapes** (all contracts): Transform, Reduce, Expand, Correlate, Route. Provider declares which shapes it supports.

#### 6.3.1 default-CEL contract

```
evaluate(expression, bound_symbols) -> value
```

CEL appears in *families* of contexts (compute body, trigger filter, event handler condition, pre/postcondition), each with different bound symbols. Contract is symbol-environment-agnostic; sites supply environments.

Implicit from source. No deploy-config keying. Runtime ships with cel-python.

#### 6.3.2 `llm` contract

```
complete(directive, objective, input_value, sampling_params?) -> CompletionResult

CompletionResult:
  outcome : "success" | "refused" | "error"
  output_value : any
  refusal_reason : text | null
  error_detail : text | null
  audit_record : AuditRecord  (see 6.3.4)
```

Single-shot. No tool calls. Streaming supported (already implemented through to UI).

#### 6.3.3 `ai-agent` contract

```
invoke(directive, objective, context, tools) -> AgentResult
invoke_streaming(directive, objective, context, tools) -> Stream<AgentEvent>

context:
  principal : Principal           (with on_behalf_of for delegate mode)
  bound_symbols : map<text, any>

AgentEvent:
  | TokenEmitted{text}
  | ToolCalled{tool, args}
  | ToolResult{tool, result}
  | Completed{result}
  | Failed{error}

AgentResult:
  outcome : "success" | "refused" | "error"
  actions_taken : List<AuditableAction>
  reasoning_summary : text | null
  refusal_reason : text | null
  error_detail : text | null
  audit_record : AuditRecord
```

**Tool surface (closed, runtime-defined):**

| Category | Tools | Source declaration that grants access |
|---|---|---|
| Content (read-write) | `content.query`, `content.read`, `content.create`, `content.update`, `content.delete` | `Accesses <type1>, <type2>, ...` |
| Content (read-only) | `content.query`, `content.read` | `Reads <type1>, <type2>, ...` |
| State | `state.read`, `state.transition` | Implied by `Accesses` on the owning Content (not granted by `Reads`) |
| Events | `event.emit` | `Emits "<event-name>"` |
| Channels | `channel.send`, `channel.invoke_action` | `Sends to "<channel-name>" channel` |
| Compute | `compute.invoke` | `Invokes "<compute-name>"` |
| Identity | `identity.self` (returns own Principal + on_behalf_of) | Always available |

**Access-grant grammar.** `Accesses` and `Reads` are separate lines.
A read-only modifier is **not** mixed into the multi-content list — that
shape was considered and rejected because it makes per-item modifiers
visually ambiguous in `Accesses messages, products read-only`. The
canonical form is two clean lines:

```
Compute called "moderator":
  Provider is "ai-agent"
  Accesses moderation_actions          # read-write on moderation_actions
  Reads messages, users                # read-only on messages and users
  ...
```

Either or both lines may appear; declaring neither grants no Content
tools at all. A type that appears on both `Accesses` and `Reads` is a
parse error (the grant is contradictory and almost certainly a typo).

**Authorization is double-gated.** Each tool call is evaluated against:
1. The principal's scopes (delegate mode: `on_behalf_of`'s scopes; service mode: agent's own scopes)
2. The source's `Accesses`/`Sends to`/`Emits`/`Invokes` declarations

Both gates must pass. The provider cannot extend the tool surface beyond what the source declares.

**Notable absences from the tool surface:** filesystem, raw HTTP, shell, environment variables, other apps' content, identity admin operations, schema migration, audit log writes (audit is runtime-only, never agent-modifiable).

**Agent execution modes:**
- **Delegate mode (default).** Agent has no roles of its own; all authorization derives from `on_behalf_of`. Audit log records `agent X acting for user Y did Z`.
- **Service mode.** Agent is its own principal with its own roles via `role_mappings`. No `on_behalf_of`. Used for scheduled jobs, cross-app integrations.
- The compute (or other invocation site) declares which mode. Default is delegate.

**Refusal semantics:** Provider may return `outcome: "refused"` with structured `refusal_reason`. Runtime treats refusal as an error subtype: logs to audit, propagates to caller. Caller's source decides what to do (surface to UI, fall back, retry).

#### 6.3.4 Audit record (llm and ai-agent)

Every invocation produces:

```
provider_product : text                    (e.g., "anthropic")
model_identifier : text                    (e.g., "claude-haiku-4-5-20251001")
provider_config_hash : text                (hash of resolved provider config)

prompt_as_sent : text                      (directive + objective + bound symbols + tool defs, fully assembled)
sampling_params : map<text, any>           (temperature, top_p, seed, etc.)

tool_calls : List<{tool, args, result, latency_ms}>  (ai-agent only)
outcome : "success" | "refused" | "error"
refusal_reason : text | null
error_detail : text | null

cost : { units, unit_type, currency_amount? } | null  (provider-reported, opt-in)
latency_ms : whole number
invoked_at : timestamp
invoked_by : Principal                     (with on_behalf_of if delegate)
```

`provider_config_hash` rather than config contents directly — config may contain secrets; hash gives "same vs different config" without exposure. Runtime stores hash → config mapping in a privileged audit table; secrets redacted.

Cost is data, not enforcement. Runtime stamps cost on the audit record when the provider reports it. Cost-reporting tools are separate apps consuming audit data; not provider/runtime concerns. **No cost enforcement in v0.9.**

### 6.4 Channel contracts

**Four built-in Channel contracts** ship with the reference runtime:

#### 6.4.1 `webhook` — outbound HTTP

```
send(body, headers?) -> { status_code, response_body }
```

Action vocabulary in source: `Post <body>`. The destination URL never
appears in source. Provider config supplies `target` (URL), timeout,
retry policy, and auth headers (HMAC, bearer, mTLS):

```json
"channels": {
  "<channel-name-from-source>": {
    "provider": "webhook",
    "config": {
      "target": "https://hooks.example.com/path",
      "timeout_ms": 10000,
      "retry": { "max_attempts": 5, "backoff": "exponential" },
      "auth": { "type": "hmac", "secret_ref": "${HOOK_SECRET}" }
    }
  }
}
```

#### 6.4.2 `email` — outbound email

```
send(recipients, subject, body, html_body?, attachments?) -> { message_id, accepted }
```

Action vocabulary: `Subject is`, `Body is`, optional `HTML body is`,
optional `Attachments are`. `Recipients are <role>` resolves against
principals; provider receives email addresses from principal claims.
Apps using email implicitly require their identity provider to surface
email claims (compile-time lint when dependency exists but identity
contract doesn't guarantee the claim).

Literal recipient lists never appear in source. Provider config
supplies SMTP/API credentials, default-from address, reply-to, and
any per-environment recipient overrides keyed by source role:

```json
"channels": {
  "weekly digest": {
    "provider": "email",
    "config": {
      "from": "noreply@example.com",
      "reply_to": "support@example.com",
      "api_key": "${SES_API_KEY}"
    }
  }
}
```

#### 6.4.3 `messaging` — chat platforms

Slack, Teams, Discord, Mattermost, etc.

```
send(target, message_text, thread_ref?) -> message_ref
update(message_ref, new_text) -> void
react(message_ref, emoji) -> void
subscribe(target, message_handler, reaction_handler?) -> Subscription
```

Action vocabulary in source:
- `Send a message <text>`
- `Reply in thread to <message-ref> <text>`
- `Update message <message-ref> <text>`
- `React with <emoji> to <message-ref>`
- `When a message is received`, `When a reaction is added`, `When a thread reply is received` (inbound triggers — the subscription target is in deploy config, not source)

Provider declares which actions it implements; host validates source
against the declared subset (e.g., Discord might support reactions but
not thread replies).

Provider config supplies workspace token / bot identity, the per-environment
egress target (Slack channel name, Discord channel ID, Teams team-and-channel
pair), and inbound subscription target:

```json
"channels": {
  "supplier alerts": {
    "provider": "slack",
    "config": {
      "workspace_token_ref": "${SLACK_BOT_TOKEN}",
      "target": "supplier-team-prod",
      "subscription": "supplier-team-prod"
    }
  }
}
```

#### 6.4.4 `event-stream` — server-sent / WebSocket (external consumers)

For consumers that ARE NOT another Termin boundary. Internal Termin-to-Termin event propagation is handled by the distributed runtime, not this contract.

```
register_stream(name, content_types, filter_predicate) -> stream_endpoint
publish(stream_endpoint, event) -> void
```

Source declares what's streamed; runtime exposes SSE/WebSocket endpoint
that auth'd subscribers consume. The endpoint path and any
per-environment transport choice live in deploy config.

Provider config: transport (SSE vs WebSocket), endpoint path, auth
requirements for subscribers, retention/replay window.

#### 6.4.5 Channel contract behavioral requirements

**Authorization:**
- **Per-action authorization** with channel-level defaults. `Anyone with "X" can invoke this channel` sets the channel-wide default; per-action `Anyone with "Y" can invoke this` overrides.
- **Inbound principal resolution** is provider config: `principal_resolution: "anonymous" | "from_sender_email" | "from_sender_id"`. Resolution failure → message rejected and logged.

**Failure handling:**
- Default failure mode: **log-and-drop**. Channel sends that fail are logged; app keeps running.
- Source can override per channel: `Failure mode is surface-as-error` (caller sees failure; v0.9.1 implemented) or `Failure mode is queue-and-retry` (durable retry with exponential backoff + dead-letter after configurable timeout, max 24h; full implementation lands v0.10).
- Idempotency keys flow from runtime through provider for retry safety. Runtime supplies an event-derived idempotency key; provider honors it where applicable.
- Each provider declares retry policy (exponential backoff, max attempts, dead-letter destination).

**Audit record (every channel send and inbound message):**

```
channel_name : text
provider_product : text
direction : "outbound" | "inbound"
action : text                        (e.g., "send_message", "post", "subject_body")
target : text                        (resolved target — channel name, URL, recipient list)
payload_summary : text               (truncated/redacted body)
outcome : "delivered" | "failed" | "queued"
attempt_count : whole number
latency_ms : whole number
invoked_by : Principal | null        (null for inbound + system-triggered)
cost : { ... } | null                (when provider knows cost, e.g., SMS)
```

**Stub providers required for every contract.** webhook-stub records calls without HTTPing; email-stub captures sends to a queryable inbox; messaging-stub provides scriptable inbound messages. Same orthogonality as identity-stub. Dev/test deploy configs bind to stub products; same source.

**Out of scope for v0.9 channel contracts:**
- SMS / phone calls
- Bidirectional persistent connections beyond simple inbound message subscription (MQTT, custom protocols)
- Channel-to-channel routing as a primitive (already expressible via two channel declarations + an event handler bridge)

---

## 7. Deploy Config Schema (v0.9)

### 7.1 Principle

The contract-name keying layer in deploy config exists **only where the source actually names contracts**. Categories the source doesn't name (Identity, Storage, Presentation) flat-bind: category to product. Categories the source names (Compute, Channels) bind through the contract layer: category to contract to product.

### 7.2 Schema (informal — formalize as JSON Schema in implementation)

```json
{
  "version": "0.9.0",
  "bindings": {
    "identity": {
      "provider": "<product-name>",
      "config": { "<provider-specific>": "..." },
      "role_mappings": {
        "<role-name>": ["<external-group-ref>", "..."]
      }
    },
    "storage": {
      "provider": "<product-name>",
      "config": { "<provider-specific>": "..." }
    },
    "presentation": {
      "provider": "<product-name>",
      "config": { "<provider-specific>": "..." }
    },
    "compute": {
      "<contract-name>": {
        "provider": "<product-name>",
        "config": { "<provider-specific>": "..." }
      }
    },
    "channels": {
      "<channel-name-from-source>": {
        "provider": "<product-name>",
        "config": { "<provider-specific>": "..." }
      }
    }
  },
  "runtime": { "<runtime-options>": "..." }
}
```

### 7.3 What's NOT in deploy config

- **`parent`** — the parent boundary is determined by deployment parameters (subdomain, runtime configuration), not by the config itself. The same package + same deploy config can be deployed to org-a or org-b and inherit differently.
- **App ID** — declared in source, not config.
- **Required contracts** — inferred from source by the compiler; deploy config supplies bindings, doesn't declare requirements.

### 7.4 Default empty values

Categories the app doesn't use have empty objects (`"channels": {}`). Categories with implicit contracts (Storage, Presentation, Identity) fall back to root-boundary defaults if not bound at the leaf.

### 7.5 Example: agent_chatbot.deploy.json (v0.9)

```json
{
  "version": "0.9.0",
  "bindings": {
    "identity": {
      "provider": "stub",
      "config": {},
      "role_mappings": {}
    },
    "storage": {
      "provider": "sqlite",
      "config": {}
    },
    "presentation": {
      "provider": "default",
      "config": {}
    },
    "compute": {
      "ai-agent": {
        "provider": "anthropic",
        "config": {
          "model": "claude-haiku-4-5-20251001",
          "api_key": "${ANTHROPIC_API_KEY}"
        }
      }
    },
    "channels": {}
  },
  "runtime": {}
}
```

---

## 8. Boundary Model (v0.9 — Simple)

Three-level hierarchy:

```
root boundary
  ├── org-a boundary
  │     ├── app-a-1 (leaf — also the application)
  │     └── app-a-2 (leaf)
  └── org-b boundary
        ├── app-b-1 (leaf)
        └── app-b-2 (leaf)
```

**Applications are implicitly leaf boundaries.** The deploy config IS the leaf-boundary config. There is no separate "deploy config" concept distinct from "boundary config at the leaf."

**Effective config resolution:**
1. Runtime starts with root boundary config.
2. Walks down to the app's parent boundary (one or more levels), merging.
3. Merges the app's own (leaf) config last.
4. Resolves all required contracts; deploy fails if any are unresolved.

**Conflict resolution: key-level shallow merge, leaf wins.** Override /
augment / forbid semantics are out of scope for v0.9. The merge is:

- For each top-level binding category (`identity`, `storage`,
  `presentation`, `compute`, `channels`), keys present at the leaf
  override keys at the parent; keys absent at the leaf are inherited
  from the parent.
- Inside each binding object (e.g., `identity.config`,
  `identity.role_mappings`, the per-contract `compute.<name>.config`),
  the same shallow rule applies: leaf keys overlay parent keys, leaf
  values replace parent values **at that one key**, and parent keys
  not mentioned at the leaf survive.
- The merge is **shallow at every level** — there is no deep merge
  of nested config objects. If a leaf binding partially specifies
  a complex sub-object (e.g., `auth: { type: "bearer" }`), it
  replaces the parent's `auth` object wholesale, not merge-by-key
  inside it. Reviewers see exactly what each level contributes by
  diffing one level at a time.

This means `role_mappings: {role-A: [...]}` at the leaf inherits
`role_mappings: {role-B: [...], role-C: [...]}` from root; the
effective map has all three roles. But if the leaf says
`auth: { type: "bearer", token: "..." }` and root said
`auth: { type: "hmac", secret: "..." }`, the leaf's `auth` replaces
root's wholesale — no merging of `type` and `token` and `secret`
across levels.

**Per-environment variation.** A single app may have multiple deploy configs (`local-dev`, `beta`, `integration`, `production`). Same source; different leaf-config files. Source never changes between environments.

**Required tooling:**
- `termin show-effective-config <app> <environment>` — produces the resolved view from root to leaf for audit.

**Future enhancement: `final` markers (post-v0.9).** v0.9 has only
"leaf wins." A future enhancement adds a `final` marker on boundary
config entries so that a value set at a higher boundary cannot be
overridden by lower levels. The motivating use case is enterprise-
wide identity: a root boundary administrator binds the company's
SSO product as the identity provider and marks it `final`,
preventing any org / app / team from accidentally (or
intentionally) downgrading to a stub or different SSO. Other plausible
candidates: storage encryption settings, audit-log retention windows,
required confidentiality scopes. Likely shape:

```json
"identity": {
  "provider": "okta",
  "config": { "tenant": "acme.okta.com" },
  "final": true
}
```

Out of scope for v0.9 (see §11) but recorded here so the boundary-
merge implementation in Phase 0 doesn't accidentally preclude it.

---

## 9. Conformance

### 9.1 Two tiers

- **Tier 1 — primitive interior.** Defined by the spec; tested by the conformance suite. Every conforming runtime passes Tier 1 wholesale.
- **Tier 2 — provider seam.** Per-contract conformance. A runtime advertises which contracts it implements at which conformance level (per-level conformance applies primarily to Presentation; see BRD #2).

### 9.2 Per-provider conformance advertisement

Each runtime publishes a machine-readable manifest:

```json
{
  "runtime_version": "0.9.0",
  "tier_1_conformance": "passing",
  "tier_2_providers": {
    "identity": {
      "okta": { "conformance": "passing", "version": "..." },
      "stub": { "conformance": "passing", "version": "..." }
    },
    "storage": {
      "sqlite": { "conformance": "passing", "version": "..." }
    },
    "compute": {
      "ai-agent": {
        "anthropic": { "conformance": "passing", "shapes": ["Transform", "Reduce"] }
      }
    },
    "channels": {
      "messaging": {
        "slack": { "conformance": "passing", "actions": ["send", "react", "thread_reply"] }
      }
    }
  }
}
```

### 9.3 Conformance tests

- **Tier 1 tests** live in the existing conformance suite (`termin-conformance` repo).
- **Tier 2 tests** are per-contract test packs. Each contract specification ships with a conformance test pack that any provider claiming to implement the contract MUST pass.
- A runtime claims to "support contract X" only when its bound provider for X passes the contract's conformance pack against that runtime.

### 9.4 Vetted-provider framework

Providers may be **first-party** (shipped with the reference runtime, conformance-tested by the core team) or **third-party** (independently developed, may or may not pass conformance). The vetted-provider framework — formal review, security audit, certification process for third-party providers seeking "vetted" status — is **out of scope for v0.9**. v0.9 establishes the conformance test mechanism; the governance layer comes later.

---

## 10. Phased Implementation Plan

The risk: Claude Code reorganizes five primitive subsystems at once and the conformance suite goes red across the board. Mitigation: vertical slices, smallest first, all prior tests green at the end of each phase.

**One loading path for all providers.** The reference runtime ships with
first-party providers (stub identity, sqlite storage, default-CEL
compute, anthropic ai-agent, slack messaging, etc.). These are NOT
special-cased — they load through the same provider registry that
third-party providers use. The distinction "first-party" vs
"third-party" is a provenance / governance fact, not an architectural
one. Every provider is a plugin against the contract surface; the
runtime has no built-in shortcuts that bypass the contract.

| Phase | Scope | End-state | Why this order |
|---|---|---|---|
| **0** | Provider scaffolding only. Contract registry (in-process, hardcoded). Provider registry (loadable modules). v0.9 deploy config schema parser. Effective-binding resolver. **No primitive changes.** | All existing tests still green. New tests for binding resolution. | Plumbing must land before any primitive can use it. Zero behavior change keeps smoke test trivial. |
| **1** | Identity extraction. `Identity:` block grammar replaces top-level lines. `Users authenticate with X` removed from grammar. The cookie-based stub identity is **rewritten as a first-party plugin** that loads through the provider registry from Phase 0 — same loading path third-party providers will use. No special-cased "stub" code outside the plugin module. Deploy config flat-binds identity. | Identity contract conformance tests pass. Existing apps' migrated `.termin` files green. | Smallest contract surface. No contract-name keying layer in source — simplest deploy config shape. Proves the provider seam end-to-end, including that built-in providers go through the same plugin loader as third-party ones. |
| **2** | Storage extraction. SQLite provider implements the contract. Content primitive talks to provider, not direct SQLite calls. Predicate AST + CEL pushdown. Cascade with blast radius. Schema migration with diff classification. | Storage contract conformance tests pass. All existing apps with `Content called` work unchanged. | Foundational. Once this works, the pattern is proven for inferred-contract categories. |
| **3** | Compute provider model. `Provider is "X"` formal resolution. Default-CEL stays implicit. `llm` contract. `ai-agent` contract with closed tool surface. Audit record with reproducibility fields. Refusal semantics. Streaming (already implemented in transport — formalize at contract level). | Compute contract conformance tests pass for default-CEL, llm, and ai-agent. | Adds the contract-name keying layer to deploy config (first category that needs it). Proves the named-contract pattern. |
| **4** | Channel provider model. `Channel called` grammar in parser. Action sub-block validation against contract action vocabulary. Stream removal from grammar. Built-in providers: `webhook`, `email`, `messaging` (Slack as first product). Stub products for each. | Channel contract conformance tests pass. Internal real-time still works through distributed runtime layer (untouched). | Most novel grammar. Depends on the named-contract pattern from Phase 3. Three providers because one isn't enough to prove the contract abstraction holds. |
| **5** | (Presentation — see BRD #2.) | | |

**Phase dependencies:**
- Phase 0 blocks all subsequent phases.
- Phases 1 and 2 are independent and could parallelize.
- Phase 3 depends on Phase 0 only.
- Phase 4 depends on Phase 3 (the named-contract pattern).
- Phase 5 (Presentation) depends on Phase 0; sequencing relative to other phases addressed in BRD #2.

**At the end of every phase:** the runtime is strictly more capable than the previous one, with all prior conformance tests green and a new suite added for the new category. No phase introduces a regression in another category's tests.

**Suggested rollout cadence:** one phase per minor release (v0.9.1 = Phase 1 complete; v0.9.2 = Phase 2 complete; etc.). v1.0 ships when all phases including Presentation are complete and the conformance suites are stable.

---

## 11. Out of Scope for v0.9

The following are recognized as future work but not part of the v0.9 milestone:

**Boundary model:**
- Override / augment / forbid semantics for boundary inheritance
- `final` markers on boundary config entries (prevent lower-level override; motivating case is enterprise-wide identity provider — see §8 "Future enhancement")
- Per-environment boundary config variants beyond simple multiple deploy configs
- Boundary-level sub-organization beyond the three-level model

**Provider ecosystem:**
- Vetted-provider certification process (Tier 2 provider review, security audit, governance)
- Third-party provider distribution and discovery mechanisms
- Provider versioning and migration tools

**Advanced contracts:**
- SMS / phone-call channel contracts
- MQTT / custom-protocol channel contracts
- Cost enforcement at the compute layer (audit captures cost; enforcement deferred)
- Provider-side full-CEL pushdown (runtime does residual evaluation in v0.9)

**Compute extensions:**
- Domain-specific compute contracts (`geospatial`, `financial`, `inference`) beyond the three built-ins
- Multi-step orchestration patterns beyond `compute.invoke` chaining

**Identity extensions:**
- Identity admin operations as part of any contract
- Cross-tenant identity federation
- Built-in MFA enforcement at the contract level (provider-level today)

**Tooling:**
- Migration tooling for v0.8 → v0.9 grammar changes (manual migration only in v0.9)
- Visual provider configuration UI
- Provider marketplace

---

## Appendix A: Migrated example sources (v0.9 grammar)

### hello_user.termin

```
Application: Hello User
  Description: A slightly personalized Hello World.
Id: cb4aef7b-1f54-41a2-8999-55d9367a9304

(This is a comment.)

Identity:
  Scopes are "app.view"
  Anonymous has "app.view"
  A "user" has "app.view"

As Anonymous, I want to see a page "Hello" so that I can be greeted:
  Display text "Anon, Hello!"

As user, I want to see a page "Hello" so that I can be greeted:
  Display text `SayHelloTo(the user.display_name)`

Compute called "SayHelloTo":
  Transform: takes name : text, produces greeting : text
  `greeting = "Hello, " + name + "!"`
  Anyone with "app.view" can execute this
  Audit level: none
```

### agent_chatbot.termin

```
Application: Agent Chatbot
  Description: Conversational AI chatbot with message history
Id: 0d0e2358-ffc7-4f3f-bc89-1af5ca363b1f

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

Content called "messages":
  Each message has a role which is one of: "user", "assistant", defaults to "user"
  Each message has a body which is text, required
  Anyone with "chat.use" can view or create messages

Compute called "reply":
  Provider is "ai-agent"
  Accesses messages
  Trigger on event "message.created" where `message.role == "user"`
  Directive is ```
    You are a helpful conversational assistant. Be natural and
    helpful. Never fabricate information. If asked to do something
    outside your capabilities, explain what you can do instead.
  ```
  Objective is ```
    Reply to the user's latest message. Load the conversation
    history with content.query("messages"). Create your reply
    with content.create("messages", {"role": "assistant",
    "body": your_reply}).
  ```
  Anyone with "chat.use" can execute this
  Audit level: actions
  Anyone with "chat.use" can audit

As Anonymous, I want to chat with the AI
  so that I can have a conversation:
    Show a page called "Chat"
    Show a chat for messages with role "role", content "body"

Navigation bar:
  "Chat" links to "Chat" visible to all
```

### warehouse.termin (excerpts — Identity block + cascade modifier)

```
Application: Warehouse Inventory Manager
  Description: Track products and stock levels across warehouses
Id: 3e157422-d10c-4a2b-b6c3-9f80fc80f27b

Identity:
  Scopes are "inventory.read", "inventory.write", and "inventory.admin"
  A "warehouse clerk" has "inventory.read" and "inventory.write"
  A "warehouse manager" has "inventory.read", "inventory.write", and "inventory.admin"
  An "executive" has "inventory.read"

Content called "stock levels":
  Each stock level has a product which references products, restrict on delete
  ...
```

(Full warehouse migration straightforward; `Stream stock updates and alerts at /api/v1/stream` line removed entirely.)

---

## Appendix B: Summary of v0.8 → v0.9 grammar changes

| v0.8 | v0.9 | Reason |
|---|---|---|
| `Users authenticate with stub` (top-level) | (removed) | Authentication implied by Identity block |
| `Scopes are "..."` (top-level) | Inside `Identity:` block | Block grouping |
| `A "role" has "..."` (top-level) | Inside `Identity:` block | Block grouping |
| `An "anonymous" has "..."` (top-level) | `Anonymous has "..."` inside `Identity:` block | Built-in keyword |
| `Stream X at <path>` | (removed) | Internal real-time auto-handled by distributed runtime |
| (no Channel block) | `Channel called "X": Provider is "Y"` for external egress/ingress | Explicit external integration boundary |
| `references X` (default behavior unspecified) | `references X, cascade on delete` or `references X, restrict on delete` (bare `references X` is a parse error) | Cascade behavior must be visible in source review |
| `Provider is "X"` only in Compute | `Provider is "X"` in Compute and Channel (always indented sub-block) | Consistent provider declaration |

---

*End of BRD. Companion document: `termin-presentation-provider-brd-v0.9.md` covers Presentation, the three customization levels, per-level conformance, and Phase 5 implementation specifics.*
