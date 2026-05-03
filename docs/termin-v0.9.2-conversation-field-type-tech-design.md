# Termin v0.9.2 — Conversation Field Type Technical Design

**Status:** Draft v1 for JL review.
**Date:** 2026-05-03.
**Author:** JL + Claude.
**Companion:** `termin-v0.9.3-airlock-on-termin-tech-design.md` — the application-layer consumer that motivated this work.
**Aligns with:** `airlock-termin-sketch.md` §4–§5 (the v0.9-era design that established sessions-as-content + a separate `messages` content type, which v0.9.2 supersedes for conversation modeling).

---

## 1. Purpose & Scope

This document specifies the **conversation field type** and supporting language work landing in Termin v0.9.2. The motivation is the v0.9.3 Airlock-on-Termin port, but the work itself is general — any agent-shaped Termin app benefits.

The current `agent_chatbot.termin` pattern reconstructs conversation history per agent invocation via `content.query("messages")` and writes responses via `content.create("messages", ...)`. This is three runtime-mediated round-trips per turn (query → Anthropic call → create), doesn't match Anthropic's native conversation API shape (a `messages` array passed on every request), and prevents the runtime from making the prompt-cache hits that native conversation handling enables.

v0.9.2 introduces a **`conversation` field type** on Content. Conceptually:

> Conversation is to ai-agent computes what `principal` is to identity. Stored as an opaque structured value internally (effectively a JSON list of entries), but typed in source so the system can read it and render it natively for LLM providers.

**This document specifies:**

- The `conversation` field type (storage shape, per-entry structure, auto-generated IDs).
- A new `Append` CRUD verb + REST mapping.
- A new event class `<content>.<field>.appended` with trigger predicates.
- Updates to the ai-agent provider Protocol so providers receive a structured conversation as input and the runtime auto-appends agent responses.
- New compute source grammar (`AI role is`, `Map ... to ... with <CEL-expr>`).
- `When` rule semantics for non-LLM listeners that subscribe to conversation-appended events and can append back (the OVERSEER-shaped pattern).
- Chat presentation contract update so `presentation-base.chat` binds to a conversation field.
- Multi-row ownership work (BRD #3 Appendix B partial) — included in v0.9.2 because v0.9.3 sessions need it and it's a small extension.
- A side-by-side refresh of `examples/agent_chatbot.termin` showing the migration.

**This document does NOT:**

- Specify the Airlock-on-Termin app. That's the v0.9.3 companion doc.
- Specify composite or transitive ownership (a record owned by multiple principals, or a child owned by its parent's owner). Those stay deferred to v0.10. Only the multi-row case lands in v0.9.2.
- Specify per-entry update or delete on conversation fields. Conversation entries are append-only-immutable by design — they are an event-log shape, not a mutable collection.
- Touch backwards compatibility removal. The existing `agent_chatbot` content-query pattern continues to compile and run; v0.9.2 only *adds* the new path. Migration of the parked `agent_chatbot2.termin` example is voluntary.

---

## 2. Design Goals

1. **Native to Anthropic's conversation API.** The runtime materializes the conversation as a `messages` array (in Anthropic's case) or the equivalent native shape for other providers. Provider-agnostic in source; provider-native at the wire.
2. **One agent invocation per user turn.** No three-step query → call → create round-trip. Tool-use loops live inside one Anthropic request lifecycle, sharing the same growing messages array. Matches the production Airlock pattern (see v0.9.3 §3.5).
3. **Composable.** A content type can have multiple conversation fields (a primary chat plus a debug log, etc.). Multiple content types can each have their own conversation fields. Nothing about the type forces one-conversation-per-app.
4. **Per-entry identity.** Auto-generated entry IDs so the meta-evaluator (or any audit consumer) can cite specific entries as evidence.
5. **Event-driven, not poll-driven.** Appends fire `conversation.appended` events. Agents and `When` rules subscribe; the runtime delivers the appended entry as part of the event payload.
6. **Provider-agnostic.** Anthropic, OpenAI, Bedrock — different providers have different role conventions and different message shapes. The mapping from source-roles to provider-roles is declared per-compute via CEL expressions, not hardcoded into the field type.

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│  Source (.termin file)                                        │
│  ────────────────────                                         │
│  Content called "X":                                          │
│    Each X has a Y which is conversation:                      │
│      role is one of "...", "..."                              │
│      body is text                                             │
│      ... (optional metadata fields)                           │
│                                                               │
│  Compute called "agent":                                      │
│    Provider is "ai-agent"                                     │
│    Trigger on event "X.Y.appended" where ...                  │
│    Accesses X, X.Y                                            │
│    AI role is "..."                                           │
│    Map "<src-role>" to <provider-role> with `<CEL>`           │
│    Directive is ```...```                                     │
│    Objective is ```...```                                     │
└────────────────────────────┬─────────────────────────────────┘
                             │ compile
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  IR + Runtime                                                 │
│  ────────────                                                 │
│  - Storage: opaque structured field on the record             │
│    (effectively a JSON list of entries)                       │
│  - REST: POST /<resource>/{id}/Y:append → entry+id            │
│  - Events: X.Y.appended fired on every successful append      │
│  - Compute trigger evaluates against appended_entry payload   │
│  - On agent invocation:                                       │
│    1. Runtime materializes the conversation                   │
│    2. Runtime applies the per-compute Map rules to translate  │
│       to provider-native shape (Anthropic messages array)     │
│    3. Provider receives native shape + tools + system prompt  │
│    4. Provider returns response (with tool-use loop internal) │
│    5. Runtime auto-appends response as a new entry,           │
│       role = the compute's "AI role is"                       │
└──────────────────────────────────────────────────────────────┘
```

**Key choices, justified inline below:**

- **Field type, not a primitive.** Conversation is *data on a record*, not a special agent feature. Treating it as a typed field aligns with Termin's content-centric model and lets it compose naturally.
- **Append-only.** Conversation entries are immutable artifacts (like audit rows). No update or delete on individual entries. If a record needs editing, that's a different content shape.
- **Per-compute role mapping.** Different agents see the same conversation differently — ARIA authors as "aria"; an evaluator just reads. The mapping rules belong on the compute, not on the field, so the field stays general.
- **Backwards compatible.** Existing `content.query` / `content.create` agent patterns continue to work. v0.9.2 *adds* the conversation path; it does not remove the old path. Migration is opt-in per-app.

---

## 4. Reference: How v0.9.1 ai-agent computes work today

The current `examples/agent_chatbot.termin`:

````
Content called "messages":
  Each message has a role which is one of: "user", "assistant",
                  defaults to "user"
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
````

Per agent invocation, the runtime does:

1. `content.query("messages")` — full collection scan to reconstruct conversation. The agent reads this as part of its work.
2. Anthropic API call (with internal tool-use loop). The agent assembles a `messages` array from the queried records and includes it in the request.
3. `content.create("messages", {role: "assistant", body: response})` — write the response.

Three runtime-mediated operations per turn. The Anthropic call is one of them, but the surrounding query + create are unnecessary in the native pattern (see v0.9.3 §3.5 for what production Airlock does instead). The cost is paid in latency, in token usage (the messages array is rebuilt from a database query each turn instead of being maintained as the canonical state), and in lost prompt-cache hits.

---

## 5. The `conversation` field type

### 5.1 Source declaration

````
Each <singular> has a <field-name> which is conversation:
  role is one of "<role-1>", "<role-2>", ...
  body is text, required
  [optional metadata fields, e.g.:]
  tool_calls is structured
  event_id is text
  [...]
````

The `role`, `body`, and any optional metadata fields are declared **at the field-type usage site**. The set of valid roles is application-defined — `"player", "aria", "overseer"` for Airlock; `"user", "assistant"` for chat; `"customer", "agent", "system"` for a support tool.

`role` is required; `body` is required and must be text. Other metadata fields are optional and can be any structured Termin field type.

### 5.2 Per-entry shape

Every entry the runtime stores has an auto-generated identity envelope plus the application-declared content:

| Field | Type | Source |
|-------|------|--------|
| `id` | text (UUID v7 or monotonic) | Auto-generated by the runtime on append. Returned in the append response. Sortable by creation order. |
| `created_at` | timestamp | Auto-set by the runtime on append. |
| `appended_by_principal_id` | text | The principal whose action caused the append (the compute's effective principal, or the player who triggered the CRUD call). |
| `role` | text (one of the declared values) | From the append payload. |
| `body` | text | From the append payload. |
| `<other declared metadata fields>` | (per declaration) | From the append payload. |

The id, created_at, and appended_by_principal_id are added by the runtime; the application supplies role + body + any declared metadata.

### 5.3 Storage and read shape

Internally, the conversation is stored as an opaque structured value on the record (in the SQLite reference runtime, that's a JSON column; in other runtimes, whatever native structured-blob facility exists). The runtime treats the column opaquely except for append operations and read materialization.

Reading the field via standard CRUD returns the full conversation as an ordered list of entries — most recent last. Pagination of the conversation read is **not** in v0.9.2 (every agent invocation reads the whole thing); it can be added later if conversations get long enough to matter.

### 5.4 Constraints on the field

- **Append-only.** No CRUD verb to update or delete a specific entry. The conversation grows monotonically over the life of the record.
- **No null entries.** Every entry has at least `id`, `role`, `body`.
- **Bounded entry size.** Body content has the same text-field bounds as any other Termin text field; metadata structured fields are bounded by their respective types.

---

## 6. Append CRUD verb

### 6.1 Source-level verb

````
Append to <record>.<field> as "<role>" with body `<CEL-expression>`
  [, <metadata-field>: `<CEL-expression>`]
  [, ...]
````

Examples:

````
Append to sessions.conversation_log as "player" with body `request.body.text`
````

````
Append to sessions.conversation_log as "overseer" with body
  `"[OVERSEER] Airlock 7 status: decompression in approximately " +
   string(remaining_seconds) + "s. Recommend expediting diagnosis."`,
  event_id: "time_warning_1"
````

The verb is usable in:

- **Action lists** of `When` event-rules (the OVERSEER-shaped pattern).
- **Source pages** as a user-driven action (a chat input form's submit handler).
- **Compute bodies** for `default-CEL` computes that synthesize entries.
- The agent's auto-write-back when an `ai-agent` compute returns its response (no source verb needed; the runtime handles it implicitly per §8.5).

### 6.2 REST mapping

```
POST /<resource>/{id}/<field>:append
Content-Type: application/json
Authorization: <as appropriate>

Body:
{
  "role": "<one of the declared roles>",
  "body": "<text>",
  "<metadata-field-1>": <value>,
  ...
}

Response: 201 Created
{
  "id": "<auto-generated entry id>",
  "created_at": "<ISO-8601 timestamp>",
  "appended_by_principal_id": "<principal id>",
  "role": "<as supplied>",
  "body": "<as supplied>",
  ...
}
```

The `:append` suffix is a convention for action-on-resource; the verb is `POST`. Permission semantics: the access rule for the parent record's *append* permission applies (see §6.3).

### 6.3 Permission semantics

Append is a write operation, distinct from the standard `update` permission. New permission verb on the parent record:

````
Anyone with "<scope>" can append to <field>
Anyone with "<scope>" can append to their own <plural>' <field>
````

Examples:

````
Anyone with "chat.use" can append to chat_threads' conversation
Anyone with "airlock.session.read" can append to their own sessions'
                                          conversation_log
````

Append permission is independent of read/update permissions on the same record. A consumer might be able to append messages to a public thread without being able to read or update the thread itself; or vice versa.

---

## 7. Event class: `<content>.<field>.appended`

Every successful append fires an event. The event name follows the existing state-machine event pattern (`<content>.<field>.<state>.<verb>` per BRD #3 §5):

```
<content>.<field>.appended
```

Examples: `sessions.conversation_log.appended`, `chat_threads.conversation.appended`.

### 7.1 Event payload

| Field | Type | Source |
|-------|------|--------|
| `record_id` | text | The id of the record whose field was appended to. |
| `record` | (the record) | The full record after the append. |
| `appended_entry` | (the entry, see §5.2) | The new entry, including auto-generated id, role, body, metadata. |
| `triggered_at` | timestamp | When the append occurred. |
| `invoked_by_principal_id` | text | Who caused the append (the appending compute's principal, or the user). |
| `trigger_kind` | text | `"crud-append"` for direct REST appends; `"compute-write-back"` for ai-agent auto-appends; `"when-rule-action"` for When-rule appends. |

### 7.2 Trigger predicates

Computes and `When` rules can filter by predicate. The predicate operates on the **pre-mapping** entry shape — the role is the source role declared in the field, not any provider-mapped role.

````
Trigger on event "sessions.conversation_log.appended"
  where `appended_entry.role == "player"`
````

````
When `appended_entry.role == "player"
      && session.message_count >= 3
      && !session.overseer_time_warning_1_fired`:
  Append to sessions.conversation_log as "overseer" with body `...`
  Update sessions: overseer_time_warning_1_fired = true
````

Predicates can reference `record` (the record), `appended_entry` (the new entry), and any standard envelope context.

### 7.3 Discriminating role-based triggers

Multiple agents and When-rules can subscribe to the same `*.appended` event with different role predicates. ARIA fires only on `appended_entry.role == "player"`; OVERSEER's When-rules fire on the same predicate but for different message-count/elapsed conditions; an evaluator might subscribe to a different field's appended event entirely.

**ARIA's appends do not retrigger ARIA** — the `where` clause filters them out. OVERSEER's appends do not trigger ARIA either, for the same reason. This is the intended discrimination.

---

## 8. ai-agent provider Protocol updates

### 8.1 New field on `AgentContext`

The `AgentContext` (the input shape passed to `AIProvider.invoke()`) gains:

```python
class AgentContext:
    # existing fields...
    conversation: Optional[ConversationContext] = None

class ConversationContext:
    """Pre-mapped, native-shape conversation passed to the provider.
    The runtime translates from the source field's role naming to the
    provider's native role names per the compute's Map declarations."""
    
    messages: List[NativeMessage]   # provider-native shape
    # for Anthropic: List[{role: "user"|"assistant", content: str | List[Block]}]
    
    source_field: str   # e.g., "sessions.conversation_log" — for audit
    source_record_id: str
```

Providers that support conversation (`ai-agent` providers, and optionally `llm` providers) read the structured conversation directly. Providers that don't support it (older provider implementations) ignore the field.

### 8.2 Runtime materialization pipeline

On agent compute invocation:

1. Runtime resolves the compute's `Accesses <content>.<field>` declarations. If a conversation field is among them, it loads the field value from the triggering record.
2. Runtime applies the compute's `Map "<src-role>" to <provider-role> with <CEL-expr>` rules to translate each entry to the provider's native shape.
3. Runtime constructs the `ConversationContext` and includes it in the `AgentContext`.
4. Provider runs its native conversation flow (Anthropic's `messages.create` with the messages array; OpenAI's chat completions; etc.). Tool-use iteration loops happen inside this single provider call.
5. Provider returns its response.

### 8.3 Auto-write-back

When the agent's response is text, the runtime auto-appends a new entry to the conversation field with:

- `role` = the compute's `AI role is "<role>"` declaration.
- `body` = the agent's text response.
- Optional metadata can be set if the compute declares mapping rules on the way out (out of v0.9.2 scope; default is "no metadata, just role+body").

The auto-appended entry fires `<content>.<field>.appended` like any other append, with `trigger_kind: "compute-write-back"`.

### 8.4 Backwards compatibility

Computes that do not have a conversation field in their `Accesses` continue to work via the existing `content.query` / `content.create` pattern in the Objective. The runtime detects which model the compute is using by inspecting its `Accesses` declarations.

### 8.5 Refusal and error handling

If the agent calls `system.refuse(reason)`, the runtime does NOT auto-append. The refusal is audited per the existing compute-contract semantics. Same for `system.error`.

---

## 9. Compute source syntax additions

### 9.1 `AI role is "<role>"`

Required on any `ai-agent` compute that has a conversation field in its `Accesses`. Declares which source role the compute authors as. The auto-write-back uses this role.

````
AI role is "aria"
````

The role must be one of the source-role values declared on the conversation field.

### 9.2 `Map "<src-role>" to <provider-role> with <CEL-expression>`

One per source role that can appear in the conversation. The CEL expression has access to the entry's fields (`body`, plus any declared metadata) and produces the rendered content for the provider.

````
Map "player" to user with `body`
Map "aria" to assistant with `body`
Map "overseer" to user with `"[OVERSEER]: " + body`
````

Provider-role values (the right-hand side of `to`) are provider-defined. For Anthropic-shaped providers: `user` and `assistant`. For OpenAI-shaped providers: `user`, `assistant`, `system`. For other providers: as documented by the provider.

The CEL expression must evaluate to a string. Future-version: structured content blocks (images, tool-use chains as in-message content). v0.9.2 ships text-only.

**Validation at compile time:**

- Every source role value declared on the conversation field must have a `Map` rule in any compute that `Accesses` that field. Missing roles are a compile error.
- The provider-role on the right-hand side must be a value the configured ai-agent provider knows. Validation happens at deploy-config time when the provider binding is resolved (since different providers expose different role enums).

---

## 10. `When` rule semantics for non-LLM listeners

`When` rules already support CEL trigger expressions and a small action vocabulary. v0.9.2 extends both:

### 10.1 Triggering on conversation appended events

`When` rules can fire on `<content>.<field>.appended` events the same way they fire on existing event types. The CEL trigger expression has access to `appended_entry`, `record`, and standard envelope fields (per §7).

````
When `appended_entry.role == "player" && session.message_count >= 3
      && !session.overseer_time_warning_1_fired`:
  ...
````

### 10.2 Append as an action

The `Append to ...` verb (§6.1) is available in `When` rule action lists, alongside the existing actions like `Update`, `Send to channel`, etc.

````
When `<trigger-cel>`:
  Append to <record>.<conversation-field> as "<role>" with body `<cel-expr>`
  Update <record>: <field> = <value>, ...
  Send <record>.<field> to "<channel>"
  ...
````

Action ordering: actions in a `When` rule body execute sequentially in source order. The append fires its own `<content>.<field>.appended` event, but the `where`-clause discrimination in subscribing computes/When-rules prevents accidental re-firing (e.g., OVERSEER's append doesn't trigger ARIA because ARIA filters on `role == "player"`).

This is exactly the OVERSEER pattern v0.9.3 §4.7 uses: each overseer event is a `When` rule with a CEL trigger condition + an `Append to` action + an `Update` action to set the once-per-session flag.

---

## 11. Presentation contract update: `presentation-base.chat`

The existing `presentation-base.chat` contract binds to a messages collection:

````
Show a chat for messages with role "role", content "body"
````

v0.9.2 adds binding to a conversation field:

````
Show a chat for <record>.<conversation-field>
````

The contract auto-discovers the entry shape (role, body, declared metadata) from the field type and renders accordingly. Per-role styling (e.g., Airlock's distinct USER/ARIA/OVERSEER styling) can be configured via additional modifiers on the `Show a chat ...` invocation:

````
Show a chat for sessions.conversation_log:
  role "player" styles as "user-message"
  role "aria" styles as "agent-message"
  role "overseer" styles as "system-message"
````

(Exact modifier syntax to be finalized during implementation; the shape above is illustrative.)

The chat component uses the conversation field's append-event subscription mechanism for live updates: when an entry is appended, the chat receives the new entry via WebSocket and renders it without a full reload.

The existing messages-collection binding continues to work for backwards compatibility. Apps can migrate their chat surface independently of any agent migration.

---

## 12. Multi-row ownership extension

### 12.1 The constraint as it stands in v0.9.1

Per BRD #3 §3.3 (resolved), `Each <singular> is owned by <field>` requires the field to be `unique`, limiting ownership to single-row content (one record per principal). Multi-row content (e.g., sessions, where each player has many) cannot declare ownership.

This constraint is documented in `airlock-termin-sketch.md` §4 with a NOTE that defers resolution to v0.10.

### 12.2 Why v0.9.2 unblocks part of it

The v0.9.3 sessions content type needs `is owned by player_principal` where `player_principal` is **not** unique (each player has many sessions). Without it, all "their own sessions" access predicates fall back to verbose CEL-expression rules, which works but is noisier.

v0.9.2 extends `is owned by` to support **non-unique fields**:

- The field is interpreted as a "scoping key" — the set of records owned by a principal is `{r ∈ Content : r.<field> == principal.id}`.
- `their own <plural>` permission predicates resolve to that set.
- `the user's <singular>` continues to require uniqueness (it returns at most one record); on multi-row content the form is `the user's <plural>`.

This is **only the multi-row case**. Composite ownership (a record owned by multiple principals via a join) and transitive ownership (a child owned by its parent's owner) remain v0.10 work per BRD #3 Appendix B.

### 12.3 Compile-time semantics

- `Each <singular> is owned by <field>` no longer requires `unique` on the field.
- If the field is unique, behavior is unchanged from v0.9.1.
- If the field is non-unique, `the user's <singular>` is a compile error (use `the user's <plural>` to read the set, or filter by id with a CEL predicate).
- `their own <plural>` resolves to the set; `their own <singular>` is a compile error on non-unique ownership.

---

## 13. Side-by-side: `examples/agent_chatbot.termin` refresh

### 13.1 Current syntax (v0.9.1)

````
Application: Agent Chatbot
  Description: Conversational AI chatbot with message history
Id: 0d0e2358-ffc7-4f3f-bc89-1af5ca363b1f

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

Content called "messages":
  Each message has a role which is one of: "user", "assistant",
                  defaults to "user"
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

As an anonymous, I want to chat with the AI
  so that I can have a conversation:
    Show a page called "Chat"
    Show a chat for messages with role "role", content "body"
````

### 13.2 New syntax (v0.9.2)

````
Application: Agent Chatbot
  Description: Conversational AI chatbot with message history
Id: 0d0e2358-ffc7-4f3f-bc89-1af5ca363b1f

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

Content called "chat_threads":
  Each chat_thread has a title which is text, default "Conversation"
  Each chat_thread has a conversation which is conversation:
    role is one of "user", "assistant"
    body is text, required
  Anyone with "chat.use" can view chat_threads
  Anyone with "chat.use" can create chat_threads
  Anyone with "chat.use" can append to chat_threads' conversation

Compute called "reply":
  Provider is "ai-agent"
  Trigger on event "chat_threads.conversation.appended"
                where `appended_entry.role == "user"`
  Accesses chat_threads, chat_threads.conversation

  AI role is "assistant"
  Map "user" to user with `body`
  Map "assistant" to assistant with `body`

  Anyone with "chat.use" can execute this
  Audit level: actions
  Anyone with "chat.use" can audit
  Directive is ```
    You are a helpful conversational assistant. Be natural and
    helpful. Never fabricate information. If asked to do something
    outside your capabilities, explain what you can do instead.
  ```
  Objective is ```
    Reply to the user's most recent message. The conversation
    context is provided to you natively by the runtime; the
    Anthropic messages array is materialized from
    chat_threads.conversation per the Map rules. Respond with
    your reply, which the runtime will append as a new entry
    with role "assistant" — no content.create call needed.
  ```

As an anonymous, I want to chat with the AI
  so that I can have a conversation:
    Show a page called "Chat"
    Show a chat for chat_threads.conversation:
      role "user" styles as "user-message"
      role "assistant" styles as "agent-message"
````

### 13.3 What changed

| Concern | v0.9.1 | v0.9.2 |
|---------|--------|--------|
| Conversation storage | Standalone `messages` content type, one row per turn | `conversation` field on `chat_threads` records, one entry per turn |
| Triggering ARIA | `message.created where role == "user"` | `chat_threads.conversation.appended where appended_entry.role == "user"` |
| Loading history | `content.query("messages")` in Objective | Auto-materialized by the runtime |
| Writing the reply | `content.create("messages", ...)` in Objective | Auto-appended by the runtime via `AI role is` |
| Provider call shape | Runtime builds messages array from query results | Runtime materializes natively from the conversation field; provider gets Anthropic-native messages |
| Per-turn round trips | 3 (query → call → create) | 1 (call) |
| Prompt caching | Not possible (messages array is rebuilt per turn) | Possible (the conversation field is the canonical state) |
| Per-entry IDs | Standard content record IDs | Auto-generated entry IDs within the field |
| Chat presentation | `Show a chat for messages with role "role", content "body"` | `Show a chat for chat_threads.conversation` (auto-discovers shape) |

The behavioral surface is the same; the implementation is more efficient and aligns with the provider's native model.

---

## 14. Conformance Targets

v0.9.2 must satisfy:

- **Existing `compute-contract.md`** — no break to the existing surface. Computes that don't use conversation fields continue to pass.
- **New `conversation-field-contract.md`** (to be authored as part of v0.9.2) — specifies:
  - Field type semantics (per-entry shape, append-only, ID generation)
  - Append verb behavior (REST + source verb)
  - Event payload shape and ordering (`appended` event)
  - ai-agent provider Protocol contract (conversation context delivery, role mapping, auto-write-back)
  - When-rule action semantics (`Append to` action)
  - Backwards compatibility (existing computes continue to work)
- **agent_chatbot end-to-end test** — both the v0.9.1 and v0.9.2 versions of the example compile and run; the v0.9.2 version completes a multi-turn conversation with at least one tool call (verifying the tool-use loop is internal to one provider invocation).

---

## 15. Risks & Open Questions

### 15.1 Risks

- **Provider Protocol change is invasive.** Any external ai-agent provider implementations need updating to consume `ConversationContext`. Mitigation: backwards compat on the AgentContext surface — providers that ignore the new field continue to work via the legacy content.query/content.create path.
- **Storage encoding choices.** Reference SQLite stores the conversation as a JSON column. Other runtimes may prefer different encodings. Mitigation: the field type is opaque-by-default; runtime implementers choose their storage shape as long as the contract holds (read returns ordered list, append returns new entry with id, conversation-appended event fires).
- **CEL expressiveness for Map clauses.** The CEL expressions in `Map ... with <expr>` need to handle role-prefix concatenation, structured-content references, and potentially future shapes (images, structured tool calls). v0.9.2 ships text-only; extending requires care.
- **Multi-row ownership scope creep.** Resisting the temptation to do composite + transitive ownership in v0.9.2. Stay disciplined: only the multi-row case lands; everything else is v0.10.
- **Migration ambiguity.** Existing apps with messages-collection patterns won't break, but the docs should be clear that the old pattern is the legacy path. Authors of new agent apps should default to the conversation field.

### 15.2 Open questions

| Q | Question | Recommendation |
|---|----------|----------------|
| Q1 | Pagination on conversation read for very long conversations (>100 entries)? | Defer. Add only when needed; v0.9.2 reads whole field. Most ai-agent applications cap conversation length anyway. |
| Q2 | Do we need a CRUD verb to truncate or summarize a conversation? (e.g., for context-window management on long-running threads) | Defer. v0.9.2 conversations are append-only-immutable. Summary patterns can be implemented application-side via separate fields. |
| Q3 | Should the `Map` clauses support structured content blocks (images, tool calls embedded in messages)? | v0.9.2: text only. Future version: structured. The grammar should be designed so structured doesn't break text-only callers. |
| Q4 | Should the conversation field surface a "subscribe to changes" affordance for the chat presentation contract, or piggyback on the standard event bus? | Piggyback. The standard `appended` event is already on the bus; the chat component subscribes to it. No new surface needed. |
| Q5 | Auto-write-back metadata: can the agent supply tool_calls or other metadata fields when writing back? | Defer. v0.9.2 auto-write-back is body-only. Metadata can be set later via a separate field-update verb (or by escalating from ai-agent to a more structured pattern). |
| Q6 | Which providers ship with conversation support in v0.9.2? | At minimum the Anthropic provider in `termin-server`. OpenAI/Bedrock are out of scope unless someone needs them. |
| Q7 | Should the `Append to` verb be permitted as a user-driven action without compute mediation? (e.g., a form submit handler that appends) | Yes — the REST verb is callable by any principal with the append permission. Source-level form-action support is fine. |

---

## 16. Slice Breakdown

| Slice | Owner | Scope | Effort |
|-------|-------|-------|--------|
| **L1 — Field type definition** | Termin compiler | Add `conversation` to the type system. Schema validation of role/body/metadata declarations. Storage layer encodes as JSON column in reference SQLite. IR carries the entry-shape metadata. | 0.5–1 day |
| **L2 — Append CRUD verb** | Termin compiler + termin-server | New REST endpoint `POST /<resource>/{id}/<field>:append`. New Termin source verb `Append to ... as ... with body ...`. Auto-generated entry IDs (UUID v7 or monotonic). Permission semantics (`can append to <field>`, `can append to their own <plural>' <field>`). | 1 day |
| **L3 — Event class** | termin-core + termin-server | New event class `<content>.<field>.appended`. Payload shape per §7.1. Trigger predicate parsing (`appended_entry.role == ...`). Event envelope fields. | 0.5–1 day |
| **L4 — ai-agent provider Protocol updates** | termin-core + termin-server | `AgentContext` gains `conversation: ConversationContext`. Runtime applies role mapping per compute declaration. Provider receives Anthropic-native messages array (or other native shape). Auto-write-back of agent response as new conversation entry. Backwards compat for computes without conversation fields. | 1–1.5 days |
| **L5 — Compute source syntax** | Termin compiler | Grammar additions: `AI role is "<role>"`, `Map "<src-role>" to <provider-role> with <CEL>`. Validation: every source role must have a mapping; AI role must be a declared role; provider role values validated at deploy-config time against the bound provider. | 0.5 day |
| **L6 — When-rule semantics for non-LLM listeners** | Termin compiler + termin-server | When rules can subscribe to `*.appended` events. New `Append to` action available in When-rule action lists alongside existing `Update`, `Send to`, etc. | 0.5 day |
| **L7 — Chat presentation contract update** | termin-core + termin-server (Tailwind built-in) | `presentation-base.chat` binding accepts a conversation field (`Show a chat for <record>.<field>`) in addition to the existing messages-collection binding. Auto-discovery of entry shape. Per-role styling modifiers. WebSocket subscription to the appended event. | 0.5–1 day |
| **L8 — Multi-row ownership** | Termin compiler | Extend `is owned by` to support non-unique fields. Compile-time semantics per §12. `their own <plural>` resolves to a set; `the user's <singular>` requires uniqueness. | 1 day |
| **L9 — agent_chatbot refresh** | Examples | Update `examples/agent_chatbot.termin` to the new pattern. Verify it compiles + runs end-to-end with a multi-turn exchange. Update `examples-dev/agent_chatbot2.termin` similarly. The original v0.9.1 file is preserved as `examples/agent_chatbot_legacy.termin` for backwards-compat documentation purposes. | 0.5 day |
| **L10 — Conformance: `conversation-field-contract.md`** | termin-conformance | Author the new contract spec. Add cross-runtime tests. Verify against the reference runtime. | 0.5–1 day |

**Realistic v0.9.2 total:** 6–8.5 days. Mid-range ~7 days.

**Parallelism:** L1, L2, L3 must serialize (each builds on the prior). L4 depends on L1+L3. L5, L6, L7 can run in parallel after L1+L2+L3 land. L8 is independent and can run any time. L9 + L10 are post-everything.

With two parallel agents: ~5 days clock time.

---

## 17. Out of Scope (v0.9.2 boundary)

- **Composite ownership** (a record owned by multiple principals via a join). v0.10.
- **Transitive ownership** (a child record inheriting ownership from its parent). v0.10.
- **Per-entry update or delete** on conversation fields. Conversations are append-only-immutable. Future-version may add archival semantics; v0.9.2 does not.
- **Pagination** on conversation reads. Whole-field read only.
- **Structured content** in `Map` expressions (images, embedded tool calls). Text-only in v0.9.2.
- **Auto-write-back metadata** beyond role+body. Metadata-on-agent-response is a future-version concern.
- **Conversation summarization or truncation** verbs. Application-side responsibility.
- **OpenAI / Bedrock provider conversation support** unless someone's actively using them. Anthropic support ships in v0.9.2; others can follow as needed.

---

## 18. References

- `airlock-termin-sketch.md` — v0.9-era design exercise; contains the original "messages content type" pattern that v0.9.2 supersedes for conversation modeling.
- `termin-v0.9.3-airlock-on-termin-tech-design.md` — companion application doc; consumes everything specified here.
- `termin-source-refinements-brd-v0.9.md` — BRD #3; the resolved ownership and `the user` semantics this document extends (multi-row ownership in §12).
- `termin-streaming-protocol.md` — existing streaming protocol; ai-agent computes producing conversation appends still stream per this protocol.
- `examples/agent_chatbot.termin` — existing pattern (v0.9.1).
- `examples-dev/agent_chatbot2.termin` — parked exploration; will migrate as part of L9.
- `termin-cel-types.md` — CEL surface; `Map` expressions and trigger predicates evaluate against this surface.
- `tenets.md` — Termin's five standing tenets; the conversation field type is justified primarily by Tenet 4 (providers over primitives — the field is a value, not a special agent feature) and Tenet 1 (audit over authorship — per-entry IDs make audit citations sharp).

---

*Draft v1. Hand back to JL for review.*
