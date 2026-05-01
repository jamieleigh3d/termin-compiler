# Channel Provider System — Phase 4 Technical Design

**Version:** 0.9.0-draft (Phase 4)
**Status:** Pre-implementation design — awaiting JL sign-off before coding starts.
**Branch:** `feature/v0.9-phase4` in `termin-compiler-p4`
**Depends on:** Phase 3c (access-grant grammar: `Reads`/`SendsTo`/`Emits`/`Invokes`) — now merged.

This document specifies the implementation plan for BRD #1 §10 Phase 4:
the channel provider model. It covers protocol design, stub implementations,
compiler changes, the runtime cut-over strategy, and the open design questions
that need JL's answers before coding starts.

---

## 1. What Phase 4 Delivers

Per BRD §10 Phase 4:

> *Channel provider model. `Channel called` grammar augmented with `Provider is "X"`.
> Action sub-block validation against contract action vocabulary.
> Built-in providers: `webhook`, `email`, `messaging` (Slack as first product).
> Stub products for each. Internal real-time (channel_ws.py) untouched.
> Three providers because one isn't enough to prove the contract abstraction holds.*

**In scope:**
- `providers/channel_contract.py` — Protocol surfaces + data shapes
- `providers/builtins/channel_webhook_stub.py` — records sends without HTTPing
- `providers/builtins/channel_email_stub.py` — captures to queryable inbox
- `providers/builtins/channel_messaging_stub.py` — scriptable inbound/outbound
- Compiler: `Provider is "X"` grammar in Channel blocks; `provider_contract`
  IR field; action vocabulary validation against contract tables
- Runtime: `channels.py` routes through provider registry
- Deploy config: v0.9 `bindings.channels.<name>.{provider, config}` shape
- Tests: `test_v09_channel_contract.py`, `test_v09_channel_providers.py`

**Explicitly out of scope (BRD §6.4 / §11):**
- `channel_ws.py` — internal real-time, not touched
- `event-stream` provider stub (external SSE/WebSocket) — deferred
- SMS / phone-call contracts
- Real Slack/Teams products (stub sufficient for Phase 4)
- `Failure mode is surface-as-error` — grammar placeholder in Phase 4;
  reference-runtime implementation lands in v0.9.1 (re-raises ChannelError).
- `Failure mode is queue-and-retry` — grammar placeholder in Phase 4 and v0.9.x;
  full implementation (exponential backoff + dead-letter queue + configurable
  max-retry-hours, default reasonable, 24h cap) lands v0.10. v0.9.x runtime
  falls back to log-and-drop.
- Conformance tests in `termin-conformance` — noted for final report; a
  subsequent check-in with JL determines the conformance agent's handoff

---

## 2. What's Already in Place

| Artifact | Status | Notes |
|---|---|---|
| `providers/contracts.py` | ✅ Phase 0 | All four channel `ContractDefinition`s (`webhook`, `email`, `messaging`, `event-stream`) are registered |
| `providers/registry.py` | ✅ Phase 0 | `ProviderRegistry` + `ProviderRecord` with `features: tuple[str, ...]` — used by Phase 4 for action vocab declaration |
| `providers/compute_contract.py` | ✅ Phase 3a | The named-contract Protocol pattern Phase 4 mirrors |
| `channels.py` `ChannelDispatcher` | ⚠️ Pre-provider | Direct HTTP + WebSocket dispatch; Phase 4 routes external channels through providers |
| `channel_ws.py` | ✅ Untouched | Internal realtime; Phase 4 does not modify |
| `channel_config.py` | ⚠️ Partial | Reads `bindings.channels`; needs `provider` key support |

---

## 3. New Files

```
termin_runtime/
  providers/
    channel_contract.py               ← NEW
    builtins/
      channel_webhook_stub.py         ← NEW
      channel_email_stub.py           ← NEW
      channel_messaging_stub.py       ← NEW

tests/
  test_v09_channel_contract.py        ← NEW
  test_v09_channel_providers.py       ← NEW
```

Modified files: `channels.py`, `channel_config.py`, `providers/builtins/__init__.py`,
`termin/ir.py`, `termin/lower.py`, `termin/analyzer.py`, `termin/termin.peg`,
`tests/test_channels.py` (update existing deploy config shape in fixtures).

---

## 4. Contract Protocol Design (`channel_contract.py`)

### 4.1 Pattern: per-contract Protocols, no common base

Following `compute_contract.py` exactly. Each of the four channel contracts has a
distinct operation signature (BRD §6.4.1–4); there is no useful common method
they all share. The registry returns the right Protocol type; the runtime
dispatches by the contract name key, not by `isinstance` checks.

```
(channels, "webhook")      → WebhookChannelProvider
(channels, "email")        → EmailChannelProvider
(channels, "messaging")    → MessagingChannelProvider
(channels, "event-stream") → EventStreamChannelProvider   (stub deferred)
```

**Why not a common base?** The temptation is a `ChannelProvider.send(**kwargs)` base.
But the kwarg shapes are so different (webhook takes `body + headers`; email takes
`recipients + subject + body`; messaging takes `target + message_text + thread_ref`)
that a common base would need `**kwargs` — which throws away type safety and makes
conformance tests vague. The per-contract pattern from compute is the right call.

**Why not a single dispatch method?** An alternative is `invoke(action_name, params: dict) ->
ChannelSendResult` on all providers — flexible but untyped. Rejected for the same
reason compute didn't go that way: providers should get structured args at the
language level, not a dict. Conformance tests can assert specific argument types.

### 4.2 Shared data shapes

```python
@dataclass(frozen=True)
class ChannelSendResult:
    outcome: str           # "delivered" | "failed" | "queued"
    attempt_count: int = 1
    latency_ms: int = 0
    error_detail: Optional[str] = None
    audit_record: Optional["ChannelAuditRecord"] = None

@dataclass(frozen=True)
class ChannelAuditRecord:
    """Per BRD §6.4.5 audit requirement."""
    channel_name: str
    provider_product: str
    direction: str                     # "outbound" | "inbound"
    action: str                        # e.g., "send", "post", "send_message"
    target: str                        # resolved target (URL, channel name, recipient)
    payload_summary: str               # truncated/redacted body
    outcome: str                       # "delivered" | "failed" | "queued"
    attempt_count: int
    latency_ms: int
    invoked_by: Optional[str] = None   # principal id; None for inbound+system
    cost: Optional[Mapping[str, Any]] = None
```

### 4.3 WebhookChannelProvider Protocol

```python
@runtime_checkable
class WebhookChannelProvider(Protocol):
    async def send(
        self,
        body: Any,
        headers: Optional[Mapping[str, str]] = None,
    ) -> ChannelSendResult:
        """Post body to the configured target URL.

        Target URL, timeout, retry policy, and auth headers live in
        provider config — never in the call args (leak-free principle,
        BRD §5.1).
        """
        ...
```

### 4.4 EmailChannelProvider Protocol

```python
@runtime_checkable
class EmailChannelProvider(Protocol):
    async def send(
        self,
        recipients: Sequence[str],
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        attachments: Optional[Sequence[Any]] = None,
    ) -> ChannelSendResult:
        """Send an email. SMTP/API credentials and default-from in
        provider config. Recipients resolved from principal claims by
        the runtime before calling the provider."""
        ...
```

### 4.5 MessagingChannelProvider Protocol

```python
@dataclass(frozen=True)
class MessageRef:
    id: str
    channel: str
    thread_id: Optional[str] = None

@runtime_checkable
class MessagingChannelProvider(Protocol):
    async def send(
        self,
        target: str,
        message_text: str,
        thread_ref: Optional[str] = None,
    ) -> MessageRef:
        """Send a message. Target is the platform-internal channel
        identifier resolved from deploy config — not the source logical
        name. Runtime resolves logical → physical before calling."""
        ...

    async def update(self, message_ref: str, new_text: str) -> None:
        """Update an existing message by ref."""
        ...

    async def react(self, message_ref: str, emoji: str) -> None:
        """Add a reaction emoji to a message."""
        ...

    async def subscribe(
        self,
        target: str,
        message_handler: Callable,
        reaction_handler: Optional[Callable] = None,
    ) -> "Subscription":
        """Subscribe to inbound messages. The subscription target
        (Slack channel name, Discord channel ID, etc.) is resolved
        from deploy config; source declares only the trigger shape."""
        ...
```

**Design question A (open):** Should messaging provider declare `features` at
construction time and have the runtime validate the source's action vocabulary
against the declared subset? BRD §6.4.3 says "Provider declares which actions it
implements; host validates source against the declared subset." Two options:
- **A1**: Provider object has a `supported_actions: frozenset[str]` attribute that
  the runtime reads at startup. Simple; no Protocol method needed.
- **A2**: ProviderRecord.features (already exists) carries the action list; the
  runtime reads it at bind time. Doesn't require a live provider instance.

**My recommendation: A2 (ProviderRecord.features).** It's already in the registry
design and keeps the validation in the bind/startup phase without needing a warm
provider instance. The messaging-stub's registration would pass
`features=["send", "update", "react", "subscribe"]`, and the runtime validates IR
action vocab against this list at startup. A real Slack provider that doesn't
implement thread replies would pass `features=["send", "update", "react"]`.

### 4.6 EventStreamChannelProvider Protocol (stub deferred)

Defined in the Protocol module for completeness; stub implementation deferred.
Phase 4 only defines the interface; no runtime binding. External SSE/WebSocket for
non-Termin consumers is the use case, and there's no test fixture that requires it
in Phase 4.

```python
@runtime_checkable
class EventStreamChannelProvider(Protocol):
    async def register_stream(
        self,
        name: str,
        content_types: Sequence[str],
        filter_predicate: Optional[Any] = None,
    ) -> str:
        """Register a named stream and return its endpoint path."""
        ...

    async def publish(self, stream_endpoint: str, event: Any) -> None:
        """Publish an event to a registered stream endpoint."""
        ...
```

---

## 5. Stub Provider Designs

The stubs are the default products for each contract in dev/test. Every contract
must have a stub (BRD §6.4.5: "Stub providers required for every contract").
Deploy configs in examples bind to stubs; production deploy configs bind to real
products.

### 5.1 WebhookChannelStub

Records every `send()` call without making any HTTP request.

```python
class WebhookChannelStub:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.sent_calls: list[dict] = []          # queryable by tests

    async def send(self, body, headers=None) -> ChannelSendResult:
        record = {"body": body, "headers": dict(headers or {}),
                  "sent_at": _now_iso()}
        self.sent_calls.append(record)
        return ChannelSendResult(outcome="delivered", attempt_count=1,
                                 latency_ms=0,
                                 audit_record=_audit("webhook-stub", "post",
                                                     self.config.get("target", "stub"),
                                                     str(body)[:200]))
```

Registration: `(Category.CHANNELS, "webhook", "stub")`.

### 5.2 EmailChannelStub

Captures every `send()` call to a queryable inbox.

```python
@dataclass
class CapturedEmail:
    recipients: list[str]
    subject: str
    body: str
    html_body: Optional[str]
    attachments: list
    sent_at: str

class EmailChannelStub:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.inbox: list[CapturedEmail] = []      # queryable by tests

    async def send(self, recipients, subject, body,
                   html_body=None, attachments=None) -> ChannelSendResult:
        self.inbox.append(CapturedEmail(
            recipients=list(recipients), subject=subject, body=body,
            html_body=html_body, attachments=list(attachments or []),
            sent_at=_now_iso(),
        ))
        return ChannelSendResult(outcome="delivered", ...)
```

Registration: `(Category.CHANNELS, "email", "stub")`.

### 5.3 MessagingChannelStub

Supports outbound call logging AND inbound message injection for test setup.

```python
class MessagingChannelStub:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.sent_messages: list[dict] = []           # call log
        self.reactions: list[dict] = []
        self.updates: list[dict] = []
        self._subscriptions: dict[str, list[Callable]] = {}
        self._ref_counter: int = 0

    async def send(self, target, message_text, thread_ref=None) -> MessageRef:
        self._ref_counter += 1
        ref = f"stub-msg-{self._ref_counter}"
        self.sent_messages.append({"target": target, "text": message_text,
                                   "thread_ref": thread_ref, "ref": ref})
        return MessageRef(id=ref, channel=target)

    async def update(self, message_ref, new_text) -> None:
        self.updates.append({"ref": message_ref, "new_text": new_text})

    async def react(self, message_ref, emoji) -> None:
        self.reactions.append({"ref": message_ref, "emoji": emoji})

    async def subscribe(self, target, message_handler, reaction_handler=None):
        handlers = self._subscriptions.setdefault(target, [])
        handlers.append(message_handler)
        return _StubSubscription(target, handlers)

    # Test helper — inject an inbound message
    async def inject_message(self, target: str, text: str,
                             sender_id: str = "stub-sender") -> None:
        for handler in self._subscriptions.get(target, []):
            await handler({"text": text, "sender_id": sender_id,
                           "channel": target})
```

Registration: `(Category.CHANNELS, "messaging", "stub")`.
Features declared: `["send", "update", "react", "subscribe"]`.

---

## 6. Runtime Cut-Over Strategy (`channels.py`)

### 6.1 What stays

- `channel_ws.py` — **completely unchanged**. The WebSocket realtime path for
  internal Termin-to-Termin events is not a channel contract; it's the distributed
  runtime layer. Phase 4 does not touch it.
- `ChannelDispatcher.__init__`, `startup()`, `shutdown()`, `get_spec()`,
  `get_config()`, `_check_scope()`, `_check_action_scope()`, `get_metrics()`
  — these are plumbing, not dispatch; they stay with minimal changes.

### 6.2 What changes

`ChannelDispatcher` gains a `_channel_providers: dict[str, object]` store,
populated at startup. The current `_http_send` / `_http_invoke` / `_ws_send`
private methods are replaced by `_dispatch_via_provider`. The public-facing
`channel_send()` and `channel_invoke()` API surface is **unchanged** — callers
(primarily `app.py` event handlers) don't need updates.

```python
class ChannelDispatcher:
    def __init__(self, ir, deploy_config, provider_registry, contract_registry):
        # ... existing init ...
        self._registry = provider_registry
        self._channel_providers: dict[str, object] = {}

    async def startup(self, strict=True):
        # ... existing WebSocket connect for internal channels ...
        # NEW: construct providers for external channels
        for ch in self._ir.get("channels", []):
            display = ch["name"]["display"]
            contract = ch.get("provider_contract")  # NEW IR field
            if not contract:
                continue   # internal channel — no provider
            binding = self._get_channel_binding(display)
            if not binding:
                if strict:
                    raise ChannelConfigError(...)
                continue
            product = binding.get("provider", "stub")
            record = self._registry.get(Category.CHANNELS, contract, product)
            if record is None:
                record = self._registry.get(Category.CHANNELS, contract, "stub")
            if record:
                self._channel_providers[display] = record.factory(
                    binding.get("config", {}))

    async def channel_send(self, channel_name, data, user_scopes=None):
        spec = self.get_spec(channel_name)
        display = spec["name"]["display"]
        # scope check unchanged
        provider = self._channel_providers.get(display)
        if provider is None:
            logger.info(f"Channel '{display}': no provider, send skipped (log-and-drop)")
            return {"ok": True, "status": "not_configured", "channel": display}
        return await self._dispatch_send(display, spec, provider, data)

    async def _dispatch_send(self, display, spec, provider, data):
        contract = spec.get("provider_contract")
        if contract == "webhook":
            result = await provider.send(body=data)
        elif contract == "email":
            # runtime resolves recipients from principal claims before here
            result = await provider.send(**data)
        elif contract == "messaging":
            result = await provider.send(
                target=self._resolve_target(display),
                message_text=data.get("text", str(data)),
            )
        else:
            raise ChannelError(f"Unknown contract {contract!r} on channel '{display}'")
        return {"ok": result.outcome == "delivered", "outcome": result.outcome,
                "channel": display}
```

### 6.3 Failure mode

BRD §6.4.5: default is **log-and-drop**. Provider exceptions are caught; the
channel returns `{"ok": False, "outcome": "failed"}` without propagating.

**`surface-as-error`** — implemented in v0.9.1. The dispatcher re-raises a
`ChannelError` to the caller when the provider's `send()` raises. Source
authors choose this when a send failure should fail the calling Compute /
event handler / agent step rather than be silently swallowed.

**`queue-and-retry`** — grammar placeholder in v0.9.x; the PEG accepts the
line, the IR records it, the analyzer validates, and the v0.9.1 dispatcher
falls back to log-and-drop with a logged warning. Full implementation in
v0.10 introduces a `_termin_channel_queue` SQLite table, an async retry
worker with exponential backoff, and a `_termin_channel_dead_letter` table
that receives payloads still failing after the configured max-retry-hours
window (default reasonable, max 24h). See the v0.10 backlog entry for the
worker design.

---

## 7. Compiler Changes

### 7.1 PEG grammar (`termin.peg`)

New rule in the Channel block:

```
channel_provider_line
    =
    | 'Provider' 'is' quoted_string
    ;
```

This sits alongside `channel_direction_line`, `channel_delivery_line`, etc. in the
Channel block's body line dispatch. The `words_before_is` terminal (or a fixed
keyword prefix) is appropriate here since `is` appears in other channel lines.

**Design question B (open):** The existing channel block body has `Provider is "X"`
in the v0.9 BRD examples. But the current PEG grammar has no concept of this line
for channels (it exists for Compute already: `Provider is "X"` in the Compute block
parses via `compute_provider_line`). Should Phase 4 reuse the same terminal/rule name
or define a channel-specific variant? **My recommendation:** reuse the same
`provider_is_line` rule that already exists for Compute — same syntax, different
block context. The parser's line classifier already dispatches by block type.

### 7.2 IR change (`ir.py`)

Add to `ChannelSpec`:

```python
@dataclass(frozen=True)
class ChannelSpec:
    name: NameSpec
    direction: str
    delivery: str
    carries_content: Optional[str] = None
    actions: tuple[ChannelActionSpec, ...] = ()
    requirements: tuple[RequirementSpec, ...] = ()
    provider_contract: Optional[str] = None    # NEW — e.g., "webhook", "messaging"
    failure_mode: str = "log-and-drop"         # NEW — placeholder; always log-and-drop in Phase 4
```

Channels without `Provider is "X"` in source have `provider_contract = None` —
they're internal (realtime) channels handled by the distributed runtime.

**Design question C (open):** Should channels without `Provider is "X"` be
rejected at compile time in v0.9 if they're declared `Direction: outbound`? A
channel that is outbound but has no provider can't actually send anywhere. The
BRD implies external channels MUST declare a provider; internal channels use the
distributed runtime implicitly. **My recommendation:** yes — compile error if
`direction == OUTBOUND` and no `provider_contract`. Internal channels
(`direction == INTERNAL`) never need a provider. Bidirectional channels that don't
specify a provider are also a compile error in v0.9 — the declaration is
ambiguous without a provider.

### 7.3 Lowering pass (`lower.py`)

Populate `provider_contract` from the AST's `ChannelNode.provider_is` field
(after PEG parse). Also propagate `failure_mode` from source if declared.

### 7.4 Action vocabulary validation (`analyzer.py`)

Add a static table:

```python
_CHANNEL_CONTRACT_ACTION_VOCAB: dict[str, frozenset[str]] = {
    "webhook":   frozenset({"Post"}),
    "email":     frozenset({"Subject is", "Body is", "HTML body is",
                            "Attachments are", "Recipients are"}),
    "messaging": frozenset({"Send a message", "Reply in thread to",
                            "Update message", "React with",
                            "When a message is received",
                            "When a reaction is added",
                            "When a thread reply is received"}),
    "event-stream": frozenset({"register_stream", "publish"}),
}
```

In the `_check_channel` analyzer method: if `provider_contract` is set, validate
that every declared action verb (from `Action called "X"` body lines) appears in
the contract's vocab table. Emit `SemanticError` on mismatch.

**Design question D (open):** How granular should action vocab validation be in
Phase 4? The BRD action vocabularies are expressed in source-level English phrases
(`"Send a message"`, `"Reply in thread to"`). The current channel IR stores action
names as display strings. Do we validate at the display-string level (fuzzy, easy
to implement) or do we normalize to verb tokens first? **My recommendation:**
display string prefix matching is sufficient for Phase 4 — `"Send a message"`
matches if the action body starts with that phrase. Exact tokenization can harden
in a later phase when more real examples exist.

---

## 8. Deploy Config Schema (v0.9)

The v0.9 channel binding shape per BRD §7.2:

```json
{
  "bindings": {
    "channels": {
      "<logical-channel-name>": {
        "provider": "<product-name>",
        "config": {
          // provider-specific — opaque to runtime except $VAR expansion
          "target": "supplier-team-prod",
          "workspace_token_ref": "${SLACK_BOT_TOKEN}"
        }
      }
    }
  }
}
```

`channel_config.py` change: `_get_channel_binding(display_name)` reads
`deploy["bindings"]["channels"][display]` and returns the raw dict. The
`ChannelConfig` dataclass is **retired** for external channels — the provider's
`factory(config_dict)` receives the raw `config` subdict directly (same pattern
as compute providers). The `ChannelConfig` dataclass continues to be used for
internal WebSocket channels (it carries `url`, `protocol`, `auth` — all
WebSocket-specific) — those are the only callers left.

**Design question E (open):** The existing `test_channels.py` fixtures use the old
`{"url": "http://...", "protocol": "http"}` shape in a `channels:` top-level key.
Phase 4 should update those to v0.9 shape. But `channels.py` currently has a
one-phase quiet fallback that reads both `bindings.channels` and top-level
`channels` (added when Phase 3b landed, per the comment in the source). Do we:
- **E1**: Remove the fallback in Phase 4 (clean cut; pre-v1.0 no compat)
- **E2**: Keep it for one more phase because the conformance agent may not have
  updated all fixtures yet

**My recommendation: E1, remove the fallback.** The no-backward-compat policy
is explicit pre-v1.0. The conformance fixtures are regenerated by `util/release.py`;
the existing compiler examples will be updated as part of Phase 4 anyway (they
need `Provider is "X"` added). The quiet fallback was only a one-phase bridge.

---

## 9. Examples

The existing `channel_simple.termin` and `channel_demo.termin` examples need
`Provider is "X"` added to each `Channel called "..."` block, and their deploy
configs updated to the v0.9 binding shape. The stubs are the target product.
`security_agent.termin` channels also need migration.

Example migration for `channel_simple.termin`:

```diff
 Channel called "hello":
+  Provider is "webhook"
   Direction: outbound
   Delivery: reliable
```

Deploy config (`channel_simple.deploy.json`):

```diff
-{ "channels": { "hello": { "url": "http://localhost:9999/hook", "protocol": "http" } } }
+{
+  "bindings": {
+    "channels": {
+      "hello": { "provider": "stub", "config": {} }
+    }
+  }
+}
```

---

## 10. Test Plan

### Slice 4a tests (`test_v09_channel_contract.py`)

Contract Protocol conformance — written TDD-first before stub implementations:

- `test_webhook_stub_implements_protocol` — `isinstance(WebhookChannelStub({}), WebhookChannelProvider)`
- `test_email_stub_implements_protocol`
- `test_messaging_stub_implements_protocol`
- `test_webhook_stub_records_send` — `stub.send({"event": "x"})` → `stub.sent_calls` has 1 entry
- `test_email_stub_captures_to_inbox` — send → `stub.inbox[0].subject == "..."`
- `test_messaging_stub_logs_send` — `stub.sent_messages` populated
- `test_messaging_stub_inject_message` — inject → subscribed handler called
- `test_channel_audit_record_outcome_validated` — bad outcome raises
- `test_channel_send_result_outcome_validated`

### Slice 4b tests (`test_v09_channel_providers.py`)

Runtime dispatch integration (TestClient + stub provider):

- `test_channel_dispatch_webhook_stub_on_event` — trigger event that fires outbound channel; assert stub.sent_calls += 1
- `test_channel_dispatch_email_stub` — same pattern for email
- `test_channel_dispatch_messaging_stub`
- `test_channel_log_and_drop_on_missing_provider` — no binding → not an exception, logged
- `test_channel_startup_registers_providers`
- `test_channel_action_vocabulary_validated_by_compiler` — compile source with wrong verb for messaging → SemanticError

### Updated existing tests (`test_channels.py`)

- Migrate all fixture deploy configs to v0.9 `bindings.channels` shape
- Remove tests that covered the old v0.8 `url`/`protocol` paths (those paths gone)

---

## 11. Implementation Slices

**Slice 4a — Contract surface + stubs (write tests first)**
1. Write `test_v09_channel_contract.py` (Protocol conformance + stub behavior tests)
2. Implement `providers/channel_contract.py` (data shapes + 4 Protocols)
3. Implement 3 stub providers
4. Register stubs in `providers/builtins/__init__.py`
5. All 4a tests green

**Slice 4b — Compiler changes (write tests first)**
1. Write compiler tests: source with `Provider is "messaging"` → IR has `provider_contract="messaging"`;
   wrong action vocab → SemanticError
2. Update `termin.peg` first (grammar is authoritative)
3. Update `ir.py`, `lower.py` to populate `provider_contract` + `failure_mode`
4. Update `analyzer.py` with action vocab table + validation
5. Migrate example `.termin` files and deploy configs
6. All examples compile; all 4b compiler tests green

**Slice 4c — Runtime cut-over (write tests first)**
1. Write `test_v09_channel_providers.py` dispatch integration tests
2. Update `channels.py` to route through provider registry
3. Update `channel_config.py` — retire `ChannelConfig` for external channels;
   remove v0.8 quiet fallback
4. Update `app.py` — pass registry to ChannelDispatcher at construction
5. Update `tests/test_channels.py` deploy config fixtures to v0.9 shape
6. Full test suite green (0 failures)

**Slice 4d — Verification**
1. Compile smoke test: `termin compile examples/warehouse.termin` ✓
2. All examples compile clean
3. `python -m pytest tests/ -v` → 0 fail, 0 skip
4. Push `feature/v0.9-phase4`, rebase onto Phase 3 d/e when they land

---

## 12. Open Design Questions

I need JL's answers on these before implementation starts. Presenting all four
in priority order — the first two are blocking; the last two are unblocking but
can be resolved with a default if JL is heads-down.

**Question A (blocking): Provider features validation — ProviderRecord.features or provider.supported_actions?**
Recommendation: ProviderRecord.features (answered above in §4.5). Confirm?

**Question C (blocking): Compile error for outbound channel without `Provider is "X"`?**
Recommendation: yes, SemanticError. `Direction: outbound` without provider is
a compile-time error. Internal channels implicitly need no provider. Confirm?

**Question B (can default): Reuse `provider_is_line` PEG rule from Compute, or a channel-specific rule?**
Recommendation: reuse. Will proceed with this unless JL says otherwise.

**Question D (can default): Action vocab validation — display-string prefix match?**
Recommendation: yes, display-string prefix match in Phase 4. Will proceed unless JL wants exact tokenization.

**Question E (can default): Remove the v0.8 quiet fallback in `channels.py`?**
Recommendation: yes, remove it. Will proceed unless JL wants it preserved another phase.

---

## 13. Conformance Note

Per JL's direction: conformance tests for channel providers are **not** in Phase 4
scope. This will be noted in the Phase 4 final report and handed back to JL for
scheduling with the conformance agent once Phase 4 and Phase 3 d/e both land on
`feature/v0.9`.
