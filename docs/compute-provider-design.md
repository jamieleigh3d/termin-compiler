# Compute Provider Model — Technical Design

**Status:** Approved for implementation (2026-04-26)
**Author:** Claude Anthropic
**Anchor:** v0.9 Phase 3 per
`docs/termin-provider-system-brd-v0.9.md` §6.3 and §10

**Review notes (2026-04-26, JL):** All ten open questions resolved (see §7).
Two reshapes from review:
- **Whole tool surface in Phase 3, not just content tools** (Q2). Adds
  `Sends to`, `Emits`, `Invokes` grammar in addition to `Reads`, expanding
  slice (c) accordingly. Phase 4 owns the channel/event runtime
  implementation; Phase 3 owns the source-level grant grammar and the
  tool surface widening.
- **Refusal exposed as a tool call** (Q6). The sidecar `compute_refusals`
  Content stays; the agent reaches it through a `system.refuse(reason)`
  always-available tool rather than through a post-hoc "I returned
  outcome=refused" determination. See §3.7.

Two additions from review:
- **Future AI-agent contract levels** flagged in §3.3 (different agent
  capability tiers may want separate contracts post-v0.9).
- **Escalation = state transition, not a language construct** (§3.13).
- **v1.0 roadmap entry** for alternate LLM products (Bedrock / OpenAI /
  Gemini / local) added to `docs/termin-roadmap.md` per Q9.
- **Parallelism analysis** added at §6.1 per Q7.

This is a pre-implementation design for the v0.9 Compute Provider model. Nothing
has been written yet. The goal is to surface every design decision before code
lands so the cascading changes (PEG, parser, analyzer, lowering, IR, runtime,
deploy config, conformance, all examples that use compute) happen with one
consistent shape instead of evolving across the implementation.

Phase 3 has the largest surface of any provider phase so far — three named
contracts in one category, a closed tool surface with double-gated
authorization, audit-record formalization, refusal semantics, streaming
protocol formalization, and the first introduction of the contract-name
keying layer to the deploy config. It is approximately the size of Phases 1
and 2 combined. The slice strategy in §6 is therefore load-bearing — getting
a clean order matters.

---

## 1. Goal

Per BRD §6.3 and §10, Phase 3 routes Compute through the provider registry the
way Phases 1 and 2 routed Identity and Storage. This means:

- **Three named contracts** become first-class via the registry: `default-CEL`
  (implicit, no `Provider is` clause), `llm`, and `ai-agent`.
- **`Provider is "X"` becomes a formal resolution step.** The registry maps
  `(Category.COMPUTE, contract_name, product_name) → factory`, where
  `contract_name` comes from the source's `Provider is` line (or `default-CEL`
  if absent) and `product_name` comes from the deploy config.
- **The contract-name keying layer lands in deploy config.** This is the first
  category that needs it (Phases 1 and 2 were single-contract categories).
  `bindings.compute = {"<compute-name>": {"provider": "<product>", "config": {...}}}`
  was already plumbed through the parser in Phase 0 — Phase 3 populates it.
- **Closed tool surface for ai-agent.** Per BRD §6.3.3: `content.{query,read,
  create,update,delete}`, `state.{read,transition}`, `event.emit`,
  `channel.{send,invoke_action}`, `compute.invoke`, `identity.self`. The tool
  set the provider sees is computed at runtime from source declarations
  (`Accesses` / `Reads` / `Emits` / `Sends to` / `Invokes`).
- **Double-gated authorization.** Each tool call is checked against (1) the
  effective principal's scopes and (2) the source's declarations. The provider
  can never extend the surface beyond what source declares.
- **Audit record matches BRD §6.3.4.** `provider_product`, `model_identifier`,
  `provider_config_hash`, `prompt_as_sent`, `sampling_params`, structured
  `tool_calls` list, `outcome` ∈ {success, refused, error}, `refusal_reason`,
  `cost`, `latency_ms`, `invoked_by` (with `on_behalf_of` for delegate mode).
- **Refusal semantics.** Provider may return `outcome: "refused"` with
  structured `refusal_reason`. Runtime treats refusal as an error subtype:
  logs to audit, propagates to caller. The caller's source decides what to
  do with it.
- **Streaming formalized at the contract level.** Today's
  `compute.stream.<inv_id>.field.<name>` event mechanism keeps working;
  Phase 3 adds the contract-level `Stream<AgentEvent>` shape with declared
  variants (`TokenEmitted`, `ToolCalled`, `ToolResult`, `Completed`, `Failed`).

This is a v0.9 grammar-and-deploy break. v0.8-syntax `.termin` files using
compute should mostly survive (Compute block grammar is "unchanged from v0.8
except for context" per BRD §5.5), but every existing `.deploy.json` that
binds an LLM/agent compute will need to be regenerated to the v0.9
`bindings.compute` shape. We are pre-1.0; no backward-compat shim.

---

## 2. Current state

### Contract registry (`termin_runtime/providers/contracts.py:120`)

The three named compute contracts already exist as `ContractDefinition`
entries in `_BUILTIN_CONTRACTS`:

```python
ContractDefinition(name="default-CEL", category=Category.COMPUTE, ...),
ContractDefinition(name="llm", category=Category.COMPUTE, ...),
ContractDefinition(name="ai-agent", category=Category.COMPUTE, ...),
```

So the catalog is already declared. Phase 3 makes it operational.

### Provider registry (`termin_runtime/providers/registry.py`)

The registry indexes by `(Category, contract_name, product_name) →
ProviderRecord(factory, conformance, version, features)`. Phase 1 used it for
identity (`stub`); Phase 2 used it for storage (`sqlite`). Phase 3 mirrors the
pattern with three new built-in factories.

### Deploy config (`termin_runtime/providers/deploy_config.py:79-86`)

```python
@dataclass(frozen=True)
class Bindings:
    identity: IdentityBinding
    storage: StorageBinding
    presentation: PresentationBinding
    compute: dict        # name -> NamedBinding
    channels: dict       # name -> NamedBinding
```

`bindings.compute` is already a name-keyed dict. The parser populates it in
Phase 0; the runtime ignores it today. Phase 3 wires it through to provider
construction.

### Deploy template generator (`termin/cli.py:34-78`)

**Currently emits the v0.8-shape `.deploy.json`** with top-level `version`,
`channels`, `identity`, `runtime`, plus an out-of-spec `ai_provider` block
appended at line 287. This is a stale path — the deploy_config.py *parser*
supports the v0.9-shape `{version, bindings, runtime}` but the generator
hasn't caught up. Phase 3 must regenerate the template to the v0.9 shape
with `bindings.compute` populated from the IR's ComputeSpec list.

### Compute runner (`termin_runtime/compute_runner.py:155-167`)

Today's dispatch:

```python
async def execute_compute(ctx, comp, record, content_name, main_loop):
    if not ctx.ai_provider.is_configured:
        return
    provider = comp.get("provider")
    if provider == "llm":
        await _execute_llm_compute(...)
    elif provider == "ai-agent":
        await _execute_agent_compute(...)
```

The runner constructs prompts, builds tools from `comp["accesses"]`, and
dispatches via `ctx.ai_provider.complete()` / `ctx.ai_provider.agent_loop()`.
`ctx.ai_provider` is a single global AnthropicProvider instance constructed
from the `ai_provider` deploy block. **Phase 3 replaces this** with
per-compute provider construction through the registry, looked up by
`bindings.compute["<compute-name>"].provider`.

### IR ComputeSpec (`termin/ir.py:317`)

Already carries: `provider`, `directive`, `objective`, `accesses`,
`identity_mode` (defaults to "delegate", no source grammar yet — see §3.6),
`audit_level`, `audit_scope`, `audit_content_ref`, plus 27 fields total.
Phase 3 adds a small number of fields (see §3.5) and re-purposes none.

### Compute grammar (`termin/termin.peg:578-637`)

Existing rules: `Trigger on`, `Preconditions are:`, `Postconditions are:`,
`Objective is`, `Accesses`, `Directive is`, `Anyone with X can execute this`,
`Anyone with X can audit`. **Missing**: `Reads`, `Sends to`, `Emits`,
`Invokes`, identity-mode declaration. §3.4 covers what to add.

### Analyzer (`termin/analyzer.py:929-1030`)

Validates: shape, accesses-resolves-to-content, scope-existence, access-rule
presence. **Missing**: `Reads` validation, `Accesses ∩ Reads = ∅` rejection,
declared-vs-tool-surface consistency. §3.4 covers what to add.

### Audit (`termin_runtime/compute_runner.py:557-614`)

`write_audit_trace` emits a record with `compute_name`, `invocation_id`,
`trigger`, `started_at`, `completed_at`, `duration_ms`, `outcome`, `trace`
(JSON blob), `error_message`, `invoked_by_principal_id`,
`invoked_by_display_name`, `on_behalf_of_principal_id`. Sufficient for
v0.8; **missing the BRD §6.3.4 reproducibility fields** (provider_product,
model_identifier, provider_config_hash, prompt_as_sent, sampling_params,
structured tool_calls, refusal_reason, cost). §3.5 covers extension.

---

## 3. Design decisions

### 3.1 One Protocol per contract (three Protocols)

**Decision: declare three Protocols** — `DefaultCelComputeProvider`,
`LlmComputeProvider`, `AiAgentComputeProvider` — under
`providers/compute_contract.py`, mirroring the BRD §6.3 surfaces.

```python
@runtime_checkable
class DefaultCelComputeProvider(Protocol):
    def evaluate(self, expression: str, bound_symbols: Mapping[str, Any]) -> Any: ...

@runtime_checkable
class LlmComputeProvider(Protocol):
    async def complete(
        self,
        directive: str,
        objective: str,
        input_value: Any,
        sampling_params: Mapping[str, Any] | None = None,
    ) -> CompletionResult: ...

@runtime_checkable
class AiAgentComputeProvider(Protocol):
    async def invoke(
        self,
        directive: str,
        objective: str,
        context: AgentContext,
        tools: ToolSurface,
    ) -> AgentResult: ...

    async def invoke_streaming(
        self,
        directive: str,
        objective: str,
        context: AgentContext,
        tools: ToolSurface,
    ) -> AsyncIterator[AgentEvent]: ...
```

**Rationale:** BRD §6.3 declares different operation signatures per named
contract, not a unified compute Protocol. Identity and storage were each one
contract; compute is the first multi-contract category, so it is the first
that needs one Protocol per contract. The shared base would be
`ContractProvider` (informational only) — there's no shared method shape
worth abstracting.

**Alternative considered:** one `ComputeProvider` Protocol with optional
methods. Rejected — runtime-checkable Protocols don't compose well with
optional members, and "a default-CEL provider that supports `invoke`" is
nonsense. Three Protocols match the three contracts.

### 3.2 Three first-party provider products

**Decision: ship three first-party providers as separate modules** under
`providers/builtins/`:

- `compute_default_cel.py` — wraps cel-python, registers as
  `(COMPUTE, "default-CEL", "default-cel")`. The product name is the same as
  the contract name because there is exactly one product for default-CEL.
- `compute_llm_anthropic.py` — Anthropic single-shot `complete()`, registers
  as `(COMPUTE, "llm", "anthropic")`.
- `compute_agent_anthropic.py` — Anthropic agent loop with streaming,
  registers as `(COMPUTE, "ai-agent", "anthropic")`.

**Rationale:** matches the existing pattern (`identity_stub.py`,
`storage_sqlite.py`). Each named contract has at least one product;
Anthropic is the only LLM/agent product the runtime ships, but the
contract-vs-product split keeps the door open for `bedrock`, `openai`,
`local-llm`, etc. without runtime changes.

**LLM/Agent code consolidation:** the bulk of `termin_runtime/ai_provider.py`
(prompt building, streaming JSON extractor, agent loop) folds into the two
new product modules. The current `AnthropicProvider` class becomes the
internals of `compute_llm_anthropic.AnthropicLlmProvider` and
`compute_agent_anthropic.AnthropicAgentProvider`. Streaming JSON extractor
moves to a shared helper (`providers/builtins/_streaming_json.py`) since
both products use it.

### 3.3 Deploy-config keying — full cut-over (no `ai_provider` shim)

**Decision: hard-cut to `bindings.compute["<compute-name>"]` keying.** No
back-compat for the top-level `ai_provider` block. We are pre-1.0; the
leak-free principle (BRD §5.1) says the same source must deploy unchanged
across environments and that source must not name product internals.
`ai_provider` was a v0.8 shortcut; v0.9 retires it.

**Generation:** the deploy template generator emits a v0.9-shape config with
one entry under `bindings.compute` per LLM-or-agent compute in the source:

```json
{
  "version": "0.1.0",
  "bindings": {
    "identity": { "provider": "stub", "config": {} },
    "storage":  { "provider": "sqlite", "config": {} },
    "presentation": { "provider": "default", "config": {} },
    "compute": {
      "reply":      { "provider": "anthropic", "config": { "model": "${ANTHROPIC_MODEL}", "api_key": "${ANTHROPIC_API_KEY}" } },
      "moderator":  { "provider": "anthropic", "config": { "model": "${ANTHROPIC_MODEL}", "api_key": "${ANTHROPIC_API_KEY}" } }
    },
    "channels": {}
  },
  "runtime": {}
}
```

CEL computes do not appear in `bindings.compute` — the `default-CEL`
contract is implicitly bound to the only registered product (`default-cel`)
because the contract is implicit. (The registry only requires keying when
source uses `Provider is`.)

**Migration:** every existing `.deploy.json` in `examples/` that has
`ai_provider` gets regenerated. The deploy-template `test_compile_deploy_template_for_llm`
test (and similar) already verify the LLM-compute case; those tests get
updated to assert the v0.9 shape. There are six existing deploy configs:
`agent_chatbot`, `agent_simple`, `compute_demo`, `security_agent`,
`channel_demo`, `channel_simple`. The first four need compute keys; the
last two don't.

**Resolved (Q1, JL 2026-04-26):** Hard cut-over confirmed. One entry per
`Compute called "X"` with `Provider is "<llm|ai-agent>"`. CEL computes
absent from `bindings.compute`.

**Future-direction note (JL 2026-04-26):** `Provider is "llm"` and
`Provider is "ai-agent"` are the two named compute contracts in v0.9.
This catalog can expand post-v0.9 for tiered AI-agent capabilities — e.g.,
`Provider is "ai-agent-restricted"` for sandboxed / read-only agents,
`Provider is "ai-agent-orchestrator"` for multi-step orchestration with
broader tool surfaces, etc. The contract registry already supports adding
new contracts within an existing primitive category at runtime
(`ContractRegistry.register_contract`), so adding new compute contracts
is provider-author-friendly without spec evolution. Any new contract
must come with its own conformance pack and provider Protocol — the
three Phase 3 contracts establish the pattern.

### 3.4 Full access-grant grammar + tool-surface widening

**Decision (resolved Q2, JL 2026-04-26): ship the entire tool surface in
Phase 3.** Four sibling lines to `Accesses`: `Reads`, `Sends to`, `Emits`,
`Invokes`. Phase 4 implements the channel-side runtime; Phase 3 declares
the source-level grants and the gate semantics so the tool surface is
complete on day one of Phase 4.

The argument that won: the closed-tool-surface check is most useful when
complete. Splitting grants across phases means agents in Phase 3 have a
half-empty surface that has to be re-validated when Phase 4 lands. Doing
both phases back-to-back (or in parallel — see §6.1) is cleaner.

**Grammar additions (PEG in termin.peg):**

```peg
compute_reads_line     = 'Reads'    content_list:reads_list     $ ;
compute_sends_to_line  = 'Sends'  'to' channel_list:sends_to_list 'channel' $ ;
compute_emits_line     = 'Emits'   event_list:emits_list         $ ;
compute_invokes_line   = 'Invokes' compute_list:invokes_list     $ ;
```

The `Sends to ... channel` shape mirrors BRD §6.3.3 example. `channel`
keyword at the end disambiguates from other `to` constructs.

**Channel block sibling lines (channel grammar — note: this means Phase 4
inherits a channel block grammar that already accommodates these grants on
the calling side; the channel block's own `Provider is` and action grammar
remain Phase 4's territory).**

**Analyzer rules:**
- Each item in `Reads` must resolve to a defined Content type.
- Each item in `Sends to` must resolve to a declared Channel name.
- Each item in `Emits` must resolve to a declared Event name (or be a
  fresh event name being declared by the compute — same rule as today's
  `Emit` syntax).
- Each item in `Invokes` must resolve to a declared Compute name.
- A type appearing in BOTH `Accesses` and `Reads` is a parse error.
  Error code: `TERMIN-S044`.
- Self-reference in `Invokes` (a compute that invokes itself) is allowed
  but flagged as a warning since the runtime caps recursion at a fixed
  depth.

**Tool-surface mapping** (computed at lowering time, frozen as a
`ToolSurface` dataclass passed into the agent loop):

| Source declaration | Tools granted |
|---|---|
| `Accesses <T>` | `content.{query,read,create,update,delete}` on `<T>`, `state.{read,transition}` on `<T>` |
| `Reads <T>` | `content.{query,read}` on `<T>` |
| `Sends to "<C>" channel` | `channel.send` to `<C>`, `channel.invoke_action` if `<C>` has named actions |
| `Emits "<E>"` | `event.emit` for `<E>` only |
| `Invokes "<X>"` | `compute.invoke` for `<X>` only |
| (always granted) | `identity.self`, `system.refuse` |

State tools come from `Accesses` only — never from `Reads`. BRD §6.3.3
explicit.

**Phase 3 vs Phase 4 split for Sends to / Emits / Invokes:**

Phase 3 ships:
- The grammar and analyzer (above).
- The lowering pass that emits the ToolSurface IR.
- The gate function that REJECTS undeclared invocations of the channel /
  event / compute tools (TERMIN-A001).

Phase 4 ships:
- The channel runtime that implements `channel.send` / `channel.invoke_action`.
- The four channel contracts (webhook, email, messaging, event-stream).

`event.emit` and `compute.invoke` runtime implementations already exist
(EventBus and the compute_runner respectively). Phase 3 wires them into the
gate function; Phase 4 only adds the channel surface.

Until Phase 4 lands, agents that source-declare `Sends to "X" channel` can
parse and lower fine but get a runtime error if they actually call
`channel.send` — the gate passes (declared in source), but the runtime
returns "channel contract not yet implemented." This is acceptable because
the alternative — splitting the grant grammar across phases — leaves
authorization semantics inconsistent for a release.

IR fields added to `ComputeSpec`:
- `reads: tuple[str, ...] = ()`
- `sends_to: tuple[str, ...] = ()`
- `emits: tuple[str, ...] = ()`
- `invokes: tuple[str, ...] = ()`

**Commit-shape note (resolved Q2):** the access-grant grammar lands as one
slice (slice (c) — see §6). Within that slice, each grant kind can be
implemented in its own commit if useful for review, but the slice as a
whole is one Phase 3 commit boundary.

### 3.5 Audit record extension

**Decision: extend `write_audit_trace` to emit BRD §6.3.4 fields** as
top-level columns in the audit Content type, not buried in the `trace` JSON
blob. This makes them queryable by the audit-reader role.

New columns on the auto-generated audit Content for each LLM/agent compute:

| Column | Type | Source |
|---|---|---|
| `provider_product` | text | `bindings.compute["<name>"].provider` |
| `model_identifier` | text | provider returns it (e.g., from response.model) |
| `provider_config_hash` | text | sha256 of canonicalized config dict |
| `prompt_as_sent` | text | full assembled prompt the provider sent |
| `sampling_params` | text (JSON) | temperature, top_p, seed if known |
| `tool_calls` | text (JSON list) | structured per-call records |
| `refusal_reason` | text \| null | when outcome=refused |
| `cost_units` | whole number \| null | provider-reported |
| `cost_unit_type` | text \| null | "tokens", "requests", etc. |
| `cost_currency_amount` | text \| null | numeric string for currency |
| `latency_ms` | whole number | already exists as duration_ms — rename? |

`outcome` already exists; **its allowed values become** `success | refused |
error` (was `success | error`).

**`provider_config_hash` security:** config dict may contain secrets. The
hash is the canonical-JSON hash of the *resolved* config dict (env vars
substituted). **Important:** secrets in config (e.g., `api_key`) must be
redacted-but-included in the hash input — otherwise rotating an API key
changes the hash and the operator can't tell if the *behavior-affecting*
config changed. Proposal: hash the config with secret values replaced by
their key paths (`{"api_key": "<api_key>", "model": "claude-haiku-4-5"}`).
This gives "same vs different *operational* config" without leaking
secrets and without false-positives on rotation.

**Resolved (Q3, JL 2026-04-26):** secret-redacted-then-hashed approach
confirmed acceptable.

**`prompt_as_sent`:** this field can be large (many KB). Current schema has no
size limit on text fields. Proposal: store unconditionally; readers paginate.
If audit DB size becomes a concern, post-v0.9 we can add a retention policy.

**Rename `duration_ms` → `latency_ms`:** the BRD §6.3.4 field is named
`latency_ms`. The current column is `duration_ms`. **Resolved (Q4, JL
2026-04-26): rename via the Phase 2.x rename-mapping path.** This is the
first real production use of that path; ship a one-shot deploy-config
rename mapping for existing audit tables. The migration classifier was
built for exactly this kind of disciplined evolution; using it on
ourselves is the right test.

### 3.6 Identity mode declaration in source

**Current state:** `ComputeSpec.identity_mode` defaults to `"delegate"`. There
is no source grammar to set it to `"service"`. Service mode is needed for
scheduled jobs and cross-app integrations per BRD §6.3.3.

**Decision: add `Acts as service` as an optional Compute block line.**

```peg
compute_acts_as_line
    = 'Acts' 'as' mode:('service' | 'delegate') $
    ;
```

If absent, mode defaults to `delegate`. Service mode requires a
`role_mappings` entry in the deploy config's identity binding mapping the
agent's principal id to a list of roles; this is the same `role_mappings`
mechanism the identity contract already supports.

**Rationale:** the BRD says "the compute (or other invocation site) declares
which mode" — this is the source-side declaration. Today the IR has the
field but no grammar to set it. Service mode is rare; defaulting to delegate
is correct.

**Resolved (Q5, JL 2026-04-26):** `Acts as service` confirmed.

### 3.7 Refusal semantics — agent-driven via `system.refuse` tool

**Decision (resolved Q6, JL 2026-04-26): refusal is a first-class tool call,
not a post-hoc determination.**

The agent has an always-available tool `system.refuse(reason: str)` that it
can invoke when a request conflicts with its system prompt, training-time
restrictions, or compliance directives. JL's framing: "an LLM might be told
it can't give legal/financial advice in this enterprise, and so it could
refuse to do that part of the request." The refuse tool makes this an
explicit, audited action — not a black-box "model returned weird output"
event.

**Tool surface:**
```python
system.refuse(reason: str) -> RefusalAck
```

When called:
1. Runtime captures the call in the agent's `tool_calls` list with full
   args (the reason).
2. Runtime writes a record to the sidecar `compute_refusals` Content type
   (runtime-managed, schema below).
3. Runtime aborts the agent loop — no further tool calls accepted; any
   in-flight ones rolled back.
4. Provider's invocation result returns `outcome="refused"` with
   `refusal_reason=<reason>` populated from the tool args.

The agent CAN call `system.refuse` at any point — including after partial
work is done. The runtime does not commit any staged writes from the
refused invocation; rollback is the contract guarantee.

**`compute_refusals` sidecar Content type (runtime-managed):**

| Field | Type | Notes |
|---|---|---|
| `id` | automatic | |
| `compute_name` | text | which compute refused |
| `invocation_id` | text | links to audit record |
| `reason` | text | exactly what the agent passed to `system.refuse` |
| `refused_at` | automatic timestamp | |
| `invoked_by_principal_id` | text | the principal whose request was refused |
| `on_behalf_of_principal_id` | text \| null | delegate-mode chain |

**Source-level access:** the sidecar is queryable by any role with the
compute's `audit_scope` (the same scope that gates the auto-generated
audit Content). The compute's source must declare:

```
Compute called "moderator":
  Provider is "ai-agent"
  ...
  Anyone with "compute.audit" can audit
```

Both the audit log AND the refusal sidecar surface to readers with that
scope. They join on `invocation_id`.

**Outcome flow in the runner:**

```python
match result.outcome:
    case "success":  # write outputs, fire content.updated event
    case "refused":  # refusal_reason already in audit + sidecar via the
                     # tool call. Runner does NOT write outputs; emits
                     # compute.<name>.refused event; this is NOT an error
                     # for the caller's source code (no exception, no 500)
    case "error":    # write audit with error_detail; emit error event;
                     # propagate as before
```

**Audit invariant:** every refusal is logged regardless of `audit_level`
setting. Auditors must always see refusals — this is a contract-level
invariant, not a per-compute setting.

**Why a tool call rather than a return-value annotation:** treating refusal
as a tool call makes it visible in the agent's reasoning trace —
auditors can see exactly when in the agent's run the refusal happened,
what tool calls preceded it, and what reasoning_summary the agent
produced before refusing. A pure return-value approach loses that
sequence. Refusal becomes structurally indistinguishable from any other
audited agent action, which is the right model.

### 3.8 Tool-surface authorization (double-gating)

**Decision: implement gate at tool-dispatch time in the agent loop**, not at
provider-construction time. Each tool invocation goes through a single
gate function:

```python
def gate_tool_call(
    tool_name: str,
    tool_args: dict,
    declared_grants: ToolSurface,           # from source: accesses, reads, ...
    effective_principal: Principal,         # delegate's principal or agent's
) -> Allow | Deny:
    # Gate 1: declared in source?
    if not declared_grants.permits(tool_name, tool_args):
        return Deny(reason="not_declared", code="TERMIN-A001")
    # Gate 2: principal has scope?
    required_scope = SCOPE_FOR_TOOL[tool_name]
    if not effective_principal.has_scope(required_scope, tool_args):
        return Deny(reason="not_authorized", code="TERMIN-A002")
    return Allow()
```

`declared_grants` is computed at lower-time from the ComputeSpec
(`accesses`, `reads`, `emits`, `sends_to`, `invokes` — only the first two
are populated in Phase 3). It's frozen and passed into the agent loop with
the rest of the context.

The gate is *the* authorization point. Providers cannot bypass it because
they go through the runtime's tool-execution callback, not direct DB access.
The existing pattern (compute_runner.py `_execute_tool`) already has this
shape; Phase 3 adds the gate function and tightens it.

**Audit:** every Deny goes to audit log with the deny reason and code, even
under `audit_level: actions` (denied tool calls are actions, just refused
ones).

**Phase 3 codes:** `TERMIN-A001` (tool not declared in source), `TERMIN-A002`
(principal lacks required scope). Continuing the A-series for authorization
errors.

### 3.9 Streaming protocol formalization

**Decision: keep the existing event-bus mechanism, formalize the `AgentEvent`
union as a closed sum type at the contract level.**

```python
@dataclass(frozen=True)
class TokenEmitted:
    text: str

@dataclass(frozen=True)
class ToolCalled:
    tool_name: str
    args: Mapping[str, Any]
    call_id: str

@dataclass(frozen=True)
class ToolResult:
    call_id: str
    result: Any
    is_error: bool

@dataclass(frozen=True)
class Completed:
    result: AgentResult

@dataclass(frozen=True)
class Failed:
    error: str

AgentEvent = Union[TokenEmitted, ToolCalled, ToolResult, Completed, Failed]
```

`invoke_streaming` yields `AgentEvent` instances. The runtime translates each
to the existing event-bus shape (`compute.stream.<inv_id>.field.<name>` for
TokenEmitted on a streamed field; `compute.stream.<inv_id>.tool` for
ToolCalled/ToolResult; `compute.stream.<inv_id>.completed` for Completed;
`compute.stream.<inv_id>.failed` for Failed).

The existing `StreamingJsonFieldExtractor` (in ai_provider.py) lives on as a
helper inside `compute_agent_anthropic.py` because it's an Anthropic-specific
concern — different LLM providers may stream differently.

**Resolved (Q10, JL 2026-04-26):** AgentEvent home in `compute_contract.py`
confirmed; five variants confirmed complete.

### 3.10 Audit-content auto-generation revisit

The current audit Content auto-generation (lower.py creates a Content type
per non-`audit_level: none` compute) keeps working. Phase 3 just adds the
new fields from §3.5 to the schema generated for `llm` and `ai-agent`
contracts. `default-CEL` doesn't get the LLM-specific fields
(`prompt_as_sent`, `model_identifier`, etc. don't apply).

**Implementation:** the audit-Content schema generator in lower.py becomes
contract-aware. For `provider="cel"` (or absent), generates the v0.8 shape.
For `provider in {"llm", "ai-agent"}`, generates the v0.9 shape with the new
fields.

### 3.11 Conformance Tier 1 vs Tier 2

Per BRD §9.1, Tier 1 = built-in providers must pass full contract conformance
suite; Tier 2 = third-party providers self-certify with the same suite.
Phase 3's three new providers are Tier 1 → must pass the Phase 3 conformance
pack.

**Conformance scope for Phase 3:**
- Behavioral tests for `default-CEL`: `evaluate` results match cel-python
  reference, error handling on syntax-invalid expressions, bound-symbol
  honoring.
- Behavioral tests for `llm`: `complete` returns CompletionResult with all
  required fields, refusal path returns `outcome="refused"`, audit record
  has all BRD §6.3.4 fields.
- Behavioral tests for `ai-agent`: tool calls go through the gate, denied
  calls produce TERMIN-A001/A002, streaming yields AgentEvent variants in
  expected order, audit records every action.
- **Stub providers required.** Per BRD §10 "stub providers required for every
  contract" — Phase 3 ships a `compute_llm_stub` and `compute_agent_stub`
  alongside Anthropic. The stubs return scripted responses (input → fixed
  output), supporting deterministic tests in conformance + downstream
  consumer tests. The Anthropic providers stay first-party but
  not-test-default; tests bind to stub products.

**Out of scope for Phase 3 conformance:**
- Cross-version migration of audit content (v0.8 audit schema → v0.9 audit
  schema) — already deferred to Phase 2.x cross-version pack.
- Performance/latency conformance — BRD doesn't require it.

### 3.12 Refusal-vs-error vs failed (provider-side errors)

These three outcomes are distinct and the audit fields capture different
content for each:

| Outcome | Trigger | Who decides | What's in audit |
|---|---|---|---|
| `success` | Agent completed work successfully | Agent / runtime | tool_calls, completed result |
| `refused` | Agent declined to perform some part of the task | Agent (via `system.refuse`) | refusal_reason, partial tool_calls before refusal |
| `error` | Provider/network/SDK failure, malformed model output, etc. | Runtime | error_detail, partial tool_calls before failure |

**Refused** = the agent worked but said no.
**Error** = the agent tried but couldn't complete due to system-level
issue.

These are NOT interchangeable. Source code that handles "completion
unsuccessful" must distinguish them — refused requests should typically
not auto-retry (the agent will refuse again); errors might.

### 3.13 Escalation: not a new language construct

JL's question (2026-04-26): "we want a way for agents to escalate something
to their user. But I think that can be expressed in Termin, and I'm not
sure it needs to be expressed as a language construct. What do you think?"

**Recommendation: escalation is a state-machine transition, not a new
language construct.**

The audit-over-authorship tenet says structural facts should be visible in
source review. Escalation is structurally a hand-off: a piece of work
moves from "agent owns it" to "human owns it." The state machine primitive
already expresses this exactly:

```
Content called "consultation requests":
  Each request has a question which is text, required
  Each request has an answer which is text
  Each request has a lifecycle which is state:
    lifecycle starts as pending
    lifecycle can also be answered or needs human review
    pending can become answered if the user has agent.respond
    pending can become needs human review if the user has agent.respond
    needs human review can become answered if the user has human.review
  Anyone with "agent.respond" can view, create, or update requests
  Anyone with "human.review" can view or update requests

Compute called "respond":
  Provider is "ai-agent"
  Accesses consultation requests
  Trigger on event "consultation_requests.created"
  Directive is ```You are a financial advisor. If asked about specific
investments, transition the request's lifecycle to needs human review
rather than answering directly.```
  Objective is ```Answer the user's question or escalate.```
  Anyone with "agent.respond" can execute this
```

The agent already has `state.transition` in its tool surface (granted by
`Accesses consultation request`). When it judges that the question is out
of scope for autonomous handling, it transitions to `needs human review`.
A page declared elsewhere in the source surfaces all `needs human review`
requests to the human reviewer role for action.

This pattern works because:
1. **Escalation is auditable.** State transitions are first-class audit
   events. The audit log shows the exact moment the agent escalated and
   what reasoning_summary it produced.
2. **Escalation has a target.** State transitions have declared scopes —
   the source declares who can move requests INTO the escalated state and
   who can act on them. No ambient escalation.
3. **The reviewer experience is declarative.** The page-rendering primitive
   already handles "show me a list of records in state X" with full
   filtering / search / per-record actions. No new UI work needed.
4. **It composes with refusal.** An agent can BOTH transition to
   `needs human review` AND call `system.refuse` if it doesn't want the
   refused-via-state-transition reasoning to be conflated with a
   guardrail-driven refusal. The two are independent.

**For one-shot consultations without a record to transition:** if the work
is worth escalating, it's worth tracking. Wrap the consultation in a
Content type. The "ephemeral chat with no persistent record" pattern is
not Termin-native — every audit-relevant interaction has structural
identity.

**No new language construct in Phase 3.** State machines + scopes already
express escalation. If a user pattern emerges in practice that's awkward
to express via state machines (e.g., "escalate to a specific named human
rather than a role"), that's a future grammar enhancement, not a Phase 3
scope item.

---

## 4. Layer-by-layer plan

Order is from inside out: contract surface first, then registry registrations,
then runtime wire-up, then grammar/analyzer/lower, then deploy generator,
then existing-app updates.

### 4.1 New module: `termin_runtime/providers/compute_contract.py`

Mirror of `identity_contract.py` and `storage_contract.py`. Contains:
- Three Protocols (§3.1).
- `CompletionResult`, `AgentResult`, `AgentContext`, `AgentEvent` (sum type),
  `ToolSurface` data classes.
- `RefusalReason` text type alias / structured form (TBD — see §3.7).
- `AuditRecord` dataclass for the contract-level shape (provider stamps it,
  runtime persists it).

### 4.2 New modules: three first-party providers

- `providers/builtins/compute_default_cel.py` — wraps the existing
  `expression.py` evaluator. Registers as
  `(COMPUTE, "default-CEL", "default-cel")`.
- `providers/builtins/compute_llm_anthropic.py` — wraps Anthropic single-shot
  completion path. Registers as `(COMPUTE, "llm", "anthropic")`.
- `providers/builtins/compute_agent_anthropic.py` — wraps Anthropic agent
  loop. Registers as `(COMPUTE, "ai-agent", "anthropic")`.
- `providers/builtins/compute_llm_stub.py` — scripted-response stub.
  Registers as `(COMPUTE, "llm", "stub")`.
- `providers/builtins/compute_agent_stub.py` — scripted-response stub.
  Registers as `(COMPUTE, "ai-agent", "stub")`.

Existing `termin_runtime/ai_provider.py` either deletes (if everything moves
out) or shrinks to shared helpers. Streaming JSON extractor moves to
`providers/builtins/_streaming_json.py`.

### 4.3 Runtime wire-up: replace `ctx.ai_provider` with per-compute providers

`compute_runner.execute_compute` looks up provider via:

```python
binding = ctx.deploy_config.bindings.compute.get(comp["name"]["snake"])
if binding is None and comp["provider"] in (None, "default-CEL"):
    # Implicit default-CEL — use the lone product
    record = ctx.provider_registry.get(Category.COMPUTE, "default-CEL", "default-cel")
elif binding is None:
    raise DeployConfigError(f"Compute '{comp['name']['display']}' has Provider is "
                            f"'{comp['provider']}' but no binding in deploy config")
else:
    record = ctx.provider_registry.get(Category.COMPUTE, comp["provider"], binding.provider)
provider = record.factory(binding.config if binding else {})
```

Then dispatches to `provider.evaluate` / `complete` / `invoke[_streaming]`.

`ctx.ai_provider` and the top-level `ai_provider` deploy field go away
entirely. CEL evaluation in route handlers (form precondition checks, etc.)
also routes through `ctx.provider_registry`'s default-CEL provider —
removes the last hardcoded `cel-python` import outside the provider module.

### 4.4 Grammar additions: `Reads`, `Acts as`

PEG (termin.peg):

```peg
compute_reads_line   = 'Reads' content_list:reads_list $ ;
compute_acts_as_line = 'Acts' 'as' mode:('service' | 'delegate') $ ;
```

Classifier (parse_helpers.py): two new prefixes (`Reads `, `Acts as `).

### 4.5 Analyzer additions

- New rule: `Accesses ∩ Reads = ∅` → TERMIN-S044.
- New rule: each `Reads` item must resolve to a defined Content type
  (TERMIN-S009 reused or new TERMIN-S045 — TBD).
- Existing rule extended: declared-grants check considers both Accesses and
  Reads when the source-level scope checks happen.

### 4.6 Lowering additions

- IR fields added to ComputeSpec: `reads: tuple[str, ...] = ()`. (`identity_mode`
  already exists; the Acts-as line just sets it.)
- Audit-Content schema generator becomes contract-aware (§3.10).
- Tool-surface computation: new helper builds `ToolSurface` from
  ComputeSpec → frozen dataclass, fed into the runtime context for the
  agent loop.

### 4.7 Deploy template generator update

`termin/cli.py:34-78` `_generate_deploy_template` rewrites to emit the v0.9
shape: `{version, bindings: {identity, storage, presentation, compute,
channels}, runtime}`. Per-compute keys populated from the IR's
ComputeSpec list, only including computes with `provider in {"llm",
"ai-agent"}`.

The template generation tests in `tests/test_cli.py` get updated to assert
the new shape; existing `.deploy.json` regression checks remain.

### 4.8 Audit record fields

Audit Content schema (lower.py audit-generation path) extended with the §3.5
columns for `llm` and `ai-agent` computes. Compiler-side test pack verifies
the schema matches BRD §6.3.4 exactly.

`compute_runner.write_audit_trace` extended to populate the new columns.
Provider modules return the data needed (their `complete` / `invoke`
results carry an `audit_record` field per BRD §6.3 — already in scope).

### 4.9 Existing example regeneration

Every example with an LLM/agent compute gets:
- `.deploy.json` regenerated to v0.9 shape (six files, four affected).
- Audit-Content fields verified to match the new schema.

The release script (`util/release.py`) handles regenerating the IR JSON and
the conformance fixtures; the `.deploy.json` regeneration is automatic
because the deploy template generator emits the new shape.

### 4.10 Conformance test pack

New conformance suite under `termin-conformance/tests/test_v09_compute_provider.py`:
- `default-CEL` behavioral conformance (~12 tests).
- `llm` behavioral conformance (~18 tests including refusal, audit shape,
  streaming).
- `ai-agent` behavioral conformance (~30 tests including tool gating,
  streaming events, refusal, audit, double-gate denials).

---

## 5. Test plan

### 5.1 New tests (compiler repo)

Targeting ~100 new tests across:

- `tests/test_compute_contract.py` (new) — Protocol shapes, ToolSurface
  semantics, AgentEvent variants, RefusalReason.
- `tests/test_compute_provider_registry.py` (new) — registration,
  lookup-by-(category, contract, product), error on unregistered product,
  fail-closed on missing binding for non-default contract.
- `tests/test_compute_default_cel.py` (new) — provider conformance.
- `tests/test_compute_llm.py` (new) — provider conformance via stub product.
- `tests/test_compute_agent.py` (new) — provider conformance via stub product
  including tool gating, refusal, audit fields.
- `tests/test_v09_compute_grammar.py` (new) — `Reads`, `Acts as`, dual-grant
  rejection (S044), all parser+analyzer tests.
- `tests/test_v09_compute_audit.py` (new) — audit-Content schema generation,
  field population on success/refused/error, refusal log invariant.
- `tests/test_cli.py` extended — deploy template generator emits v0.9 shape.

### 5.2 Migrated tests

Existing `tests/test_agents.py`, `tests/test_compute_demo.py`,
`tests/test_llm_streaming.py`, `tests/test_manual_compute_trigger.py` get
updated to use the registry-based path. The existing happy paths should
keep passing; the assertion shapes change for the new audit fields.

### 5.3 Conformance tests (conformance repo)

New `test_v09_compute_provider.py` pack — ~60 tests as outlined in §4.10.
Existing v0.8 compute tests likely break on audit-shape changes; those get
regenerated.

### 5.4 Per-fix verification

Same as Phase 2.x: revert each load-bearing change, watch the targeted test
fail, restore. Specifically:

- Revert the registry lookup → tests asserting registry-based dispatch fail.
- Revert the audit-fields generator → schema-shape tests fail.
- Revert the `Reads` analyzer rule → S044 tests fail.
- Revert the gate function → tool-denial tests fail.
- Revert the refused branch → refusal tests fail.

---

## 6. Implementation slice / commit strategy

Phase 3 is the largest provider-phase yet. Slicing is load-bearing.

**Recommended: five commits, one slice per major surface, each TDD.**

### Slice (a) — compute contract surface + provider registry plumb-through (no behavior change yet)

- Adds `compute_contract.py` with three Protocols and supporting types.
- Adds five new built-in provider modules (default-cel, llm-anthropic,
  agent-anthropic, llm-stub, agent-stub). Each registers but is NOT yet
  used by the runtime.
- Adds tests for Protocol shapes and registry lookup.
- `compute_runner.execute_compute` is unchanged. `ctx.ai_provider` still in
  use.
- **End state: all existing tests pass. New tests for the contract surface
  pass.**

This is the smallest non-zero slice. It proves the contract layer in
isolation.

### Slice (b) — runtime cut-over to provider registry

- `compute_runner.execute_compute` now dispatches via the registry.
- `ctx.ai_provider` is removed; replaced with `ctx.provider_registry`.
- `_execute_llm_compute` and `_execute_agent_compute` become thin wrappers
  around `provider.complete` / `provider.invoke[_streaming]`.
- Deploy template generator emits the v0.9 shape with `bindings.compute`
  keying.
- All four LLM/agent example deploy configs regenerated.
- Existing tests migrated to provide a `bindings.compute` deploy config
  rather than top-level `ai_provider`.
- **End state: all existing tests pass against new registry-based path.**

This slice is the "great cut-over" — biggest single commit. After it lands,
Phase 3's structural goal is achieved; remaining slices are surface
additions.

### Slice (c) — Full access-grant grammar + tool-surface widening

Per resolved Q2: ships `Reads` + `Sends to` + `Emits` + `Invokes` together.

- PEG additions:
  - `compute_reads_line`, `reads_list`
  - `compute_sends_to_line`, `sends_to_list`
  - `compute_emits_line`, `emits_list`
  - `compute_invokes_line`, `invokes_list`
- Parse helper: four new prefixes (`Reads `, `Sends to `, `Emits `, `Invokes `).
- Analyzer:
  - TERMIN-S044 (Accesses ∩ Reads = ∅).
  - Reads-resolves-to-Content.
  - Sends-to-resolves-to-declared-Channel.
  - Emits-resolves-to-Event (or declares fresh).
  - Invokes-resolves-to-Compute (warn on self-reference).
- Lower: ComputeSpec gains `reads`, `sends_to`, `emits`, `invokes` tuples;
  `ToolSurface` builder consumes all five (Accesses + four siblings).
- Gate function: full per-tool authorization map per §3.4 table; emits
  TERMIN-A001 / TERMIN-A002.
- Tests for each grant kind: grammar, analyzer, lowering, gate behavior.
- **End state: full source-level tool-surface authorization is in place.
  Agents that source-declare `Sends to "X" channel` lower correctly; the
  gate passes; runtime `channel.send` is a deferred-implementation stub
  until Phase 4.**

### Slice (d) — audit record extension to BRD §6.3.4 shape

- Audit-Content schema generator becomes contract-aware (LLM/agent get the
  new fields).
- `write_audit_trace` extended to populate them.
- Provider modules return the data via the AuditRecord shape.
- Rename `duration_ms` → `latency_ms` in the audit Content schema
  (single-shot rename via the Phase 2.x rename-mapping path).
- Tests verifying schema, population, and field-by-field correctness.
- **End state: audit records are reproducibility-grade per BRD §6.3.4.**

### Slice (e) — refusal semantics + `Acts as` grammar + streaming formalization

- `system.refuse` always-available tool added to ToolSurface.
- `outcome="refused"` flow through runner, audit, and event-bus.
- `compute_refusals` sidecar Content type runtime-managed (auto-created at
  app startup, schema per §3.7).
- `Acts as service|delegate` grammar + analyzer + lowering.
- Service-mode authorization path: agent's own `role_mappings` lookup.
- AgentEvent variants formalized as dataclass union; streaming wrappers
  translate to event-bus events.
- Tests for refusal flow, service mode, AgentEvent variant emission order,
  refuse-tool semantics.
- **End state: BRD §6.3 is fully wired. Phase 3 complete.**

### Conformance suite update — separate commit in conformance repo

After all five slices land in compiler repo and feature/v0.9 is merged or
the conformance repo's feature/v0.9 catches up, push the new
`test_v09_compute_provider.py` pack with regenerated fixtures. Per
resolved Q8: one commit covers the conformance work.

---

## 6.1 Parallelism analysis (resolved Q7)

JL asked: what can be done in parallel by multiple agents to accelerate
this phase?

### Across-slice dependencies

```
(a) contract surface  ──┐
                        ├──>  (b) runtime cut-over  ──┐
                        │                              ├──>  (c) grant grammar
                        │                              ├──>  (d) audit shape
                        │                              └──>  (e) refusal+Acts+stream
                        │
Phase 4 (channels) <────┘  (depends on (a) only — see below)
```

**Hard sequential:** (a) → (b). Slice (b) is the great cut-over and depends
on the contract surface from (a).

**Independent after (b):** (c), (d), (e) touch largely different files and
can land in any order or in parallel:
- (c) grammar/analyzer/lower/gate — touches `termin/`, `lower.py`,
  `analyzer.py`, `parse_helpers.py`, plus tests.
- (d) audit shape — touches `lower.py` audit-Content generator,
  `compute_runner.write_audit_trace`, audit Content schema, plus tests.
- (e) refusal + Acts as + AgentEvent — touches `compute_runner.py` outcome
  branches, `Acts as` adds to grammar/analyzer/lower (small surface),
  AgentEvent dataclass module, plus tests.

The shared file across (c), (d), (e) is `termin/lower.py` — but each slice
touches a different region of it. Mechanical merge conflicts likely
small; semantic conflicts unlikely.

### Within-slice parallelism

**Slice (a) — high.** Five new files (compute_contract.py + 4 provider
modules) plus tests. A second agent can take half the provider modules
and tests while the primary owns the contract module. Estimated 2× speedup.

**Slice (b) — low.** Sequential along the data flow: registry lookup →
runtime dispatcher → deploy template generator → existing example
regeneration. Maybe 1.2× speedup if a sub-agent regenerates examples in
parallel with the runtime-side work.

**Slice (c) — moderate.** Each grant kind (Reads, Sends to, Emits,
Invokes) is structurally similar; could split each as its own commit
inside the slice with different agents handling each. Estimated 1.5×
speedup if 2 agents.

**Slice (d) — moderate.** Audit-Content schema generator + write_audit_trace
+ provider AuditRecord shape are sequential along data flow. But the
v0.8 → v0.9 rename-mapping path is independent (Phase 2.x test). 1.3×
speedup with 2 agents.

**Slice (e) — moderate.** Three sub-concerns (refusal flow, Acts as
grammar, AgentEvent dataclass) are independent. Could split as 3 commits
inside the slice. 1.4× speedup with 2 agents.

### Phase 3 / Phase 4 parallelization

Phase 4 (channels) depends on Phase 3's named-contract pattern. Per BRD §10:

> Phase 4 depends on Phase 3 (the named-contract pattern).

The named-contract pattern is fully proven by **slice (a) — contract
surface** of Phase 3. After (a) lands:

- Phase 4 can start in parallel: another agent on a separate
  `feature/v0.9-channels` branch off `feature/v0.9` post-(a).
- Phase 4's surface (channel_contract.py + 4 channel contract Protocols +
  4 first-party providers + 4 stubs) mirrors what Phase 3 just built for
  compute. The pattern is established.
- Phase 4 will need to consume Phase 3's `Sends to` grant grammar from
  slice (c). Either:
  - **Plan A — wait for (c) to land:** Phase 4 starts after slices (a)–(c)
    of Phase 3. Phase 4 then runs in parallel with (d)+(e). Estimated
    overlap: 60-70% of Phase 4's work in parallel with Phase 3's tail.
  - **Plan B — Phase 4 starts after (a):** Phase 4 builds its channel
    runtime (the harder, larger surface) in parallel with Phase 3 slices
    (b)–(e). Phase 4's grammar work waits for (c) but the runtime
    implementation does not. Higher parallelism, more rebase work later.

**Recommended: Plan A.** Phase 4 starts after Phase 3 (c) lands. By that
point, Phase 3's grant grammar is in place, Phase 4 has its complete
source-side surface to target, and Phase 3 (d)+(e) are surface
extensions that don't conflict with Phase 4's runtime work. Phase 3 and
Phase 4 then race to completion in parallel.

### Repository / branch shape

**Same repository, separate branches.** Each agent runs on its own branch
off `feature/v0.9`:
- Primary agent: `feature/v0.9` (main Phase 3 line)
- Phase 3 sub-agents (during slice (a) and (e)): work on side branches like
  `feature/v0.9-compute-providers` then merge back via `--ff-only` or
  rebase
- Phase 4 agent: `feature/v0.9-channels` once Phase 3 (c) lands

**Daily rebase cadence** keeps the side branches close to `feature/v0.9`'s
HEAD, so the integration cost stays small.

**Conflict surface to watch:**
- `app.py` lifespan / RuntimeContext additions — Phase 3 and Phase 4 both
  add provider construction here.
- `lower.py` — Phase 3 grants and Phase 4 channel block grammar both
  affect lowering.
- Conformance suite tests — both phases add suites; same-folder additions
  are usually clean (different file names).

**Risks:**
- Without strict daily rebase cadence, Phase 4 drifts from Phase 3's
  `Sends to` grammar definition and ships against an outdated assumption.
  Mitigation: Phase 3 (c) freezes the `Sends to` grammar before Phase 4
  starts; any post-(c) refinement is communicated explicitly to the
  Phase 4 agent.
- AgentEvent dataclass union (Phase 3 (e)) and channel inbound event
  shape (Phase 4) might want to share the streaming-event taxonomy.
  Mitigation: declare AgentEvent in Phase 3 (e); Phase 4 reuses it.

### Speedup estimate

If Phase 3 (a) is parallelized 2-agent, (b) is single-agent, (c)/(d)/(e)
run pairwise-parallel after (b), and Phase 4 starts after (c):

- Solo timeline: Phase 3 (5 sequential slices) + Phase 4 (single line) ≈
  2× the work of Phase 3.
- Parallel timeline: Phase 3 (a)+(b) sequential (single agent), then
  (c)+(d)+(e) overlapping with Phase 4 → ≈ 1.3× the work of Phase 3.

**Net speedup: ~1.5× for Phase 3 + Phase 4 combined**, contingent on:
1. Daily rebase discipline.
2. Phase 3 (c) freezing the grant grammar before Phase 4 starts.
3. The primary agent owning runtime-context-shape decisions to keep the
   Phase 4 sub-agent unblocked.

The primary risk to parallelism is design-decision drift between agents
on shared surfaces (RuntimeContext, lower.py, ToolSurface). The mitigation
is clear: design decisions land in this doc and the Phase 4 design doc
*before* implementation starts on the affected slices.

---

## 7. Resolved decisions (2026-04-26)

All ten open questions resolved by JL on 2026-04-26. Recorded for the
audit trail.

| # | Question | Resolution | Section |
|---|---|---|---|
| 1 | Deploy-config keying — full cut-over | **Confirmed.** Hard-cut. | §3.3 |
| 2 | Reads only vs whole tool surface | **Pushed to whole tool surface.** Reads + Sends to + Emits + Invokes all in Phase 3 slice (c). Phase 4 still owns the channel runtime. | §3.4 |
| 3 | `provider_config_hash` redacted-then-hashed | **Confirmed acceptable.** | §3.5 |
| 4 | Rename `duration_ms` → `latency_ms` | **Rename via Phase 2.x rename-mapping path.** First production use of that path. | §3.5 |
| 5 | `Acts as service` grammar shape | **Confirmed.** | §3.6 |
| 6 | Refusal: sidecar vs output-field | **Sidecar.** Plus: refusal exposed to agent as `system.refuse(reason)` always-available tool call. | §3.7 |
| 7 | Five-slice commit strategy | **Confirmed.** Plus parallelism analysis added at §6.1. | §6, §6.1 |
| 8 | Conformance pack release | **One commit after all slices merge.** | §4.10 |
| 9 | Anthropic-only first-party | **Confirmed for v0.9.** Roadmap entry added for v1.0 alternate LLM products (Bedrock, OpenAI, Gemini, local-LLM). | §8 |
| 10 | AgentEvent home + five variants | **Confirmed.** Module: `compute_contract.py`. Variants: TokenEmitted, ToolCalled, ToolResult, Completed, Failed. | §3.9 |

### Additional decisions added during review

- **Future AI-agent contract levels** noted in §3.3. Capability-tiered
  contracts (sandboxed agent, orchestrator agent, etc.) are post-v0.9
  but not blocked — `ContractRegistry.register_contract` already
  supports them at runtime.
- **Escalation = state machine transition, not a language construct**
  (§3.13). The state primitive plus declared scopes already express
  hand-off-to-human. No new grammar.
- **Refusal-vs-error distinction formalized** (§3.12) with field-level
  audit content per outcome.

---

## 8. What's deliberately out of scope

- **Channel contract runtime implementation** — Phase 4 (BRD §10). Phase 3
  ships the source-side `Sends to "X" channel` grant grammar (per
  resolved Q2) and the gate function; Phase 4 implements
  `channel.send` / `channel.invoke_action` / the four channel contracts.
- **Bedrock / OpenAI / Gemini / local-LLM products** — v1.0 roadmap entry
  added per resolved Q9. v0.9 ships Anthropic + stubs only.
- **Tiered AI-agent contracts** (sandboxed agent, orchestrator agent,
  etc.) — post-v0.9 per §3.3 future-direction note.
- **Domain-specific compute contracts** (geospatial, financial, etc.) —
  post-v0.9 per BRD §11.
- **Cost enforcement** — audit captures cost; enforcement is post-v0.9 per
  BRD §11.
- **Provider-side full-CEL pushdown** — runtime keeps doing residual CEL
  evaluation in v0.9.
- **Multi-step orchestration patterns beyond compute.invoke chaining** —
  post-v0.9 per BRD §11.
- **Escalation as a language construct** — not needed; state machine +
  scopes already express it (§3.13).

---

## 9. Migration notes

For users with v0.8 apps using LLM/agent computes:

1. The Compute block source grammar is **unchanged** for the v0.8 lines they
   already write (`Provider is "llm"`, `Accesses messages`, `Directive`,
   `Objective`, etc.).
2. The `.deploy.json` shape **changes**. Their v0.8-shape configs (top-level
   `ai_provider` block) will fail to parse. They regenerate via
   `termin compile <source>` which emits the new shape. Manual migration
   guidance ships in the v0.9 release notes.
3. Existing audit Content data has a column-rename pending (`duration_ms` →
   `latency_ms`) and a set of new columns (`provider_product`,
   `model_identifier`, etc.) — these go through the migration classifier as
   a `low-risk` change (rename) plus `safe` changes (added nullable
   columns).
4. The `Acts as service` and `Reads` grammar additions are pure-additive —
   v0.8 source still parses unchanged.

---

**End of design.** Awaiting JL review on the ten open questions before any
code lands.
