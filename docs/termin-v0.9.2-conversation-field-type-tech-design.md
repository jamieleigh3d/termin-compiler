# Termin v0.9.2 — Conversation Field Type Technical Design

**Status:** Draft v2 for JL review. Major simplification of v1: convention over configuration throughout, `Conversation is` wiring (not `Accesses`), refusal as a tool call (not a kind), Anthropic-native mappings verified against official API docs, `structured` base type added, attachments first-party.
**Date:** 2026-05-03.
**Author:** JL + Claude.
**Companion:** `termin-v0.9.3-airlock-on-termin-tech-design.md` — the application-layer consumer that motivated this work.
**Aligns with:** `airlock-termin-sketch.md` §4–§5 (the v0.9-era design that established sessions-as-content + a separate `messages` content type, which v0.9.2 supersedes for conversation modeling).

---

## 1. Purpose & Scope

This document specifies the **conversation field type** and supporting language work landing in Termin v0.9.2. The motivation is the v0.9.3 Airlock-on-Termin port and any future agent-shaped Termin app.

The current `agent_chatbot.termin` pattern reconstructs conversation history per agent invocation via `content.query("messages")` and writes responses via `content.create("messages", ...)`. This is three runtime-mediated round-trips per turn (query → Anthropic call → create), doesn't match Anthropic's native conversation API shape (a `messages` array passed on every request), and prevents the runtime from making the prompt-cache hits that native conversation handling enables.

v0.9.2 introduces a **`conversation` field type** on Content. Conceptually:

> Conversation is to ai-agent computes what `principal` is to identity. Stored as a typed structured value internally (an ordered list of canonical entries), but transparent enough that CEL can index into it. The runtime translates it natively for LLM providers — no per-app role-mapping declarations required.

**This document specifies:**

- A new `structured` base type (small grammar addition; needed by the conversation work and useful elsewhere).
- The `conversation` field type with a **canonical, runtime-owned per-entry shape** (closed kind enum, optional attachments, tool-call linkage).
- A new `Append` CRUD verb + REST mapping + WebSocket frame format.
- A new event class `<content>.<field>.appended` with trigger predicates (filter on the pre-mapping `kind`).
- Updates to the ai-agent provider Protocol so providers receive a structured conversation as input, with **convention-based** translation to Anthropic-native shape (no per-author mapping declarations).
- A new compute source line `Conversation is <record>.<field>` to wire the LLM's context (distinct from `Accesses`, which implies tool-mediated access).
- `When` rule semantics for non-LLM listeners that subscribe to conversation-appended events and can append back (the OVERSEER pattern).
- Chat presentation contract update so `presentation-base.chat` binds to a conversation field.
- Multi-row ownership work (BRD #3 Appendix B partial — only the multi-row case lands; composite/transitive remain v0.10).
- `agent_chatbot.termin` refresh demonstrating: native conversation, a `current_time` tool call, and a refusal via `system.refuse` (which Phase 3 slice (e) shipped on 2026-04-26 — see `compute-provider-design.md` §3.7).
- D-01 update reframing L2 as "LLM with context (conversation field) and tools (`Invokes`) — single coordinated invocation, tool-use loop internal."

**This document does NOT:**

- Specify the Airlock-on-Termin app. That's the v0.9.3 companion doc.
- Specify composite or transitive ownership. v0.10.
- Forbid update or delete on conversation entries. There is no current use case (debug-style entry mutation might emerge later); v0.9.2 just doesn't build the verbs.
- Add refusal as a conversation kind. Refusal is a tool call (`system.refuse(reason)`) per the existing compute-provider-design, with the result captured in the runtime-managed `compute_refusals` sidecar — not a conversation entry.

---

## 2. User Stories

The features in this document each trace to one of these stories. Authors writing apps; reviewers reading apps; runtime implementers conforming.

### 2.1 As an app author building a chat app

> *"I want to declare a conversation on a content type and have an AI agent reply to user messages, without writing per-turn database plumbing."*

→ The `conversation` field type, `Conversation is` wiring, and convention-based provider mapping (§7, §11, §12).

### 2.2 As an app author building a tool-using agent

> *"My agent needs to call a tool (`current_time`, `repair_execute`, etc.) and have the result feed back into the next AI turn, with the tool call and its result both visible in the conversation."*

→ First-party `tool_call` and `tool_result` kinds with `tool_call_id` linkage, declared via standard `Invokes` (§8).

### 2.3 As an app author building an OVERSEER-style scripted listener

> *"I want a deterministic rule that watches for conversation activity and injects scripted messages without using an LLM."*

→ `When` rule subscribing to `<content>.<field>.appended` with the `Append to ... as <kind> with body <CEL>` action; system_event kind with `source` field (§13).

### 2.4 As an app author building a chat surface

> *"My app needs the conversation rendered live with new entries appearing as they happen, and I want users to send messages over WebSocket for low-latency interactive use."*

→ WebSocket append frame format (§9.3), `presentation-base.chat` field-binding update (§14).

### 2.5 As an app author handling user-uploaded files or screenshots

> *"My app needs users to attach images or PDFs to messages and have the AI see them."*

→ Per-entry `attachments` list with `file_name` + `mime_type` + `body`; runtime maps image/* and application/pdf attachments to Anthropic's native `image` and `document` content blocks (§8.2, §11.4).

### 2.6 As an app author whose AI must refuse certain requests

> *"My agent has guardrails. If a user asks for something that violates them, the agent should refuse cleanly, the refusal should be auditable, and the chat surface should show the user that a refusal happened."*

→ Existing `system.refuse(reason)` always-available tool (per `compute-provider-design.md` §3.7); runtime captures via `compute_refusals` sidecar; chat surface reads invocation outcome to render. **Not a conversation entry kind** (§5.2 explicitly).

### 2.7 As a reviewer auditing an agent app

> *"I want to cite specific moments in a conversation as evidence — 'on entry abc-123 the user asked X, and on tool_call entry def-456 the agent invoked Y'."*

→ Per-entry auto-generated IDs (§7.1); `tool_call_id` and `parent_id` linkage make turn boundaries reconstructable (§7.2).

### 2.8 As a runtime implementer

> *"I need a clear contract: what to store, what events to fire, what the provider Protocol expects, and which convention-based mappings I must implement."*

→ The whole document, with explicit conformance targets in §15.

---

## 3. Design Goals

1. **Native to Anthropic's conversation API.** The runtime materializes the conversation as a `messages` array using the canonical kind → role mapping (§11.4). Provider-agnostic in source; provider-native at the wire.
2. **One agent invocation per user turn.** No three-step query → call → create round-trip. Tool-use loops live inside one provider request lifecycle.
3. **Convention over configuration.** Authors do not declare role mappings. The kind enum is canonical; the runtime knows how to translate each kind to the provider's native shape.
4. **Composable.** A content type can have multiple conversation fields. Multiple content types can each have their own. Nothing forces one-conversation-per-app.
5. **Per-entry identity.** Auto-generated entry IDs so audits can cite specific entries.
6. **Tool calls are first-party.** `tool_call` and `tool_result` are conversation kinds, linked by `tool_call_id`, so the conversation history is a complete record of the agent's reasoning trail.
7. **Attachments are first-party.** Images, PDFs, structured payloads ride on the same conversation primitive, not a separate channel.
8. **WebSocket-friendly.** Same payload shape over REST and WebSocket; low-latency interactive use is a first-class scenario.

---

## 4. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│  Source (.termin file)                                        │
│  ────────────────────                                         │
│  Content called "X":                                          │
│    Each X has a Y which is conversation                       │
│                                                               │
│  Compute called "agent":                                      │
│    Provider is "ai-agent"                                     │
│    Trigger on event "X.Y.appended" where ...                  │
│    Conversation is X.Y                                        │
│    Invokes "tool1", "tool2"                                   │
│    Directive is ```...```                                     │
└────────────────────────────┬─────────────────────────────────┘
                             │ compile
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  IR + Runtime                                                 │
│  ────────────                                                 │
│  - Storage: structured list of canonical entries on the       │
│    record (JSON column in reference SQLite)                   │
│  - REST: POST /<resource>/{id}/Y:append → entry+id            │
│  - WebSocket: same payload, framed for the per-record subs    │
│  - Events: X.Y.appended on every successful append            │
│  - Compute trigger evaluates against appended_entry payload   │
│  - On agent invocation:                                       │
│    1. Materialize conversation per kind → Anthropic mapping   │
│       (canonical, no per-compute config)                      │
│    2. Provider receives native messages array + tools         │
│    3. Provider runs internal tool-use loop                    │
│    4. Each tool_use becomes a tool_call entry; each tool      │
│       output becomes a tool_result entry, linked by ID        │
│    5. Final assistant text is auto-appended as 'assistant'    │
│       kind with role from the convention                      │
│  - Refusal: provider returns outcome=refused; runtime         │
│    writes compute_refusals sidecar row; NOT appended          │
│    as a conversation entry                                    │
└──────────────────────────────────────────────────────────────┘
```

---

## 5. Reference: How v0.9.1 ai-agent computes work today

The current [`examples/agent_chatbot.termin`](../examples/agent_chatbot.termin):

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
    You are a helpful conversational assistant. Be natural and helpful.
    Never fabricate information. If asked to do something outside your
    capabilities, explain what you can do instead.
  ```
  Objective is ```
    Reply to the user's latest message. Load the conversation history
    with content.query("messages"). Create your reply with
    content.create("messages", {"role": "assistant", "body": your_reply}).
  ```
````

Per agent invocation, the runtime does three runtime-mediated operations: `content.query("messages")`, the Anthropic API call (with internal tool-use loop), `content.create("messages", ...)`. The cost is paid in latency, in token usage (the messages array is rebuilt from a database query each turn instead of being maintained as the canonical state), and in lost prompt-cache hits.

v0.9.2 makes the conversation a first-class typed value; agents read and write it natively.

---

## 6. New base type: `structured`

The PEG grammar's `base_type` enumeration ([termin.peg:108-125](../termin/termin.peg)) currently includes `text`, `currency`, `percentage`, `boolean`, `date`/`datetime`, `automatic`, `whole number`, `number`, `principal`, `one of:` enum, and `list of <inner>`. **There is no `structured` type today** — earlier drafts (this doc and the `airlock-termin-sketch.md`) used `structured` informally; it does not compile.

v0.9.2 adds **`structured`** as a real base type. Storage: an opaque structured value (JSON column in the reference SQLite). Reads return the value as a CEL-shaped tree (maps, lists, scalars). Writes accept the same shape. No schema validation at the type level — apps that need schema enforcement do it via dedicated Content types with declared fields.

**Why now:** the conversation field's per-entry shape, attachments, tool args, scoring JSON, survey responses, and other agent-shaped data all want this. We've been working around its absence for several phases. Adding it as part of v0.9.2 is a small grammar change that unblocks several things at once.

```
Each <singular> has <field> which is structured [, <constraints>]
```

Examples:

````
Each session has scores which is structured
Each compute_call has tool_args which is structured
Each survey_response has answers which is structured
````

CEL access reads fields by path: `session.scores.of_level`, `record.tool_args.location`, etc. Type system is unchecked at the leaves (you get whatever the writer wrote); apps validate at boundaries.

---

## 7. The `conversation` field type

### 7.1 Source declaration

````
Each <singular> has a <field-name> which is conversation
````

That's it. **No role declaration. No body declaration. No attachment-shape declaration.** The runtime owns the per-entry shape (§7.2). Apps that want richer per-conversation metadata add it as **separate fields on the parent record**, not by extending the conversation entry shape.

### 7.2 Per-entry shape (canonical, runtime-owned)

Every entry has the following fields. The shape is fixed in v0.9.2 and is the same across all conversation fields in all apps.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | text (UUID v7) | Always | Auto-generated by the runtime on append. Sortable by creation order. Stable for audit citations. |
| `kind` | enum | Always | One of: `user`, `assistant`, `tool_call`, `tool_result`, `system_event`. Closed enum in v0.9.2. |
| `type` | text | Optional | Per-kind sub-discriminator. Free-form text — no closed enum. v0.9.2 documents one value: `assistant.type == "refusal"` for entries the agent produced via `system.refuse(reason)` (see §11.5). All other kinds reserve the field for later; omitted means default. Trigger predicates and chat providers can read it (`appended_entry.type == "refusal"`). |
| `body` | text | Always | Main content. For `tool_call`, a human-readable summary of the args; for `tool_result`, the result text. For `assistant` with `type == "refusal"`, the refusal reason. |
| `source` | text | Optional | Free-form label for `system_event` entries (e.g., `"OVERSEER"`, `"audit_log"`, `"ops_team"`). The chat presentation contract uses this to discriminate display styling. |
| `tool_call_id` | text | Optional | On `tool_call`: the unique id (mirrors Anthropic's `tool_use.id`). On `tool_result`: the id of the tool_call this answers. |
| `parent_id` | text | Optional | The id of the user message that started the turn this `tool_call`/`tool_result`/refusal belongs to. Lets reviewers reconstruct turn boundaries. |
| `tool_name` | text | Optional | On `tool_call` and `tool_result`: which tool. |
| `tool_args` | structured | Optional | On `tool_call`: the args (mirrors Anthropic's `tool_use.input`). |
| `attachments` | list of attachment | Optional | See §7.3. |
| `created_at` | timestamp | Always | Auto-set by the runtime. |
| `appended_by_principal_id` | text | Always | The principal whose action caused the append. |

**Refusal is an assistant entry, not a separate kind.** When the agent invokes `system.refuse(reason)` (per [`compute-provider-design.md` §3.7](compute-provider-design.md)), the runtime: (a) terminates the agent loop, (b) writes a WARN-level audit log entry, (c) appends a conversation entry of `kind: "assistant", type: "refusal", body: <reason>, parent_id: <triggering user msg id>`. The refusal sits in source order in the conversation — wherever the agent reached the decision, possibly after several assistant/tool_call/tool_result entries while it gathered context. The chat provider renders it as a distinguished assistant turn (same speaker, different visual treatment by convention). Reviewers trace from a refusal back to the original user request via `parent_id`.

**Sidecar retirement:** Phase 3 slice (e) (2026-04-26) shipped a `compute_refusals` Content type for refusal records. v0.9.2 retires it — the WARN audit log entry is the audit-trail surface, the conversation entry is the chat surface. Two surfaces, one event source. The sidecar table and its writes are removed from the runtime in L7 (per the v0.9.2 CHANGELOG migration note).

### 7.3 Attachments shape

Each attachment in the `attachments` list:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `file_name` | text | Yes | Display name; e.g., `"diagram.png"`, `"contract.pdf"`. |
| `mime_type` | text | Yes | Standard MIME type; e.g., `"image/png"`, `"image/jpeg"`, `"application/pdf"`, `"application/json"`, `"text/plain"`. |
| `body` | text | Yes | The content. For `text/*` types, the text directly. For `application/json`, the serialized JSON. For binary types (`image/*`, `application/pdf`), base64-encoded. |

**Native Anthropic mapping** (verified against [Vision docs](https://platform.claude.com/docs/en/build-with-claude/vision) and [PDF support docs](https://platform.claude.com/docs/en/build-with-claude/pdf-support)):

- `image/png`, `image/jpeg`, `image/gif`, `image/webp` → Anthropic `image` content block: `{"type": "image", "source": {"type": "base64", "media_type": <mime>, "data": <body>}}`
- `application/pdf` → Anthropic `document` content block: `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": <body>}}`
- Other text-ish types (`text/plain`, `application/json`) → text content block with a small wrapper header (`[ATTACHMENT: <file_name> (<mime_type>)]\n<body>`) so the AI knows the body is attached content, not in-flow conversation
- Other binary types Claude doesn't natively understand (`audio/*`, `application/zip`, etc.) → text wrapper indicating the file is present but not parseable: `[ATTACHMENT: <file_name> (<mime_type>) — binary content not natively supported by this provider]`. The agent can decide what to do.

Attachment limits in v0.9.2: per-attachment body size capped at the same bound as any text field; per-entry attachment count uncapped at the field level (the storage shape supports any list length); Anthropic's API has its own request-size limits (32 MB for standard endpoints, 100 pages per PDF, up to 100 images per request for 200K-context models). Apps that exceed these limits get provider-side errors propagated.

The Files API path (`{"type": "image", "source": {"type": "file", "file_id": "..."}}`) is **not** in v0.9.2 — that requires an upload primitive Termin doesn't have. Add when needed.

### 7.4 Storage and read shape

Internally, the conversation is stored as a JSON column on the record (in reference SQLite; other runtimes use whatever native structured-blob facility exists). Reads return the full conversation as an ordered list of entries — most recent last.

CEL can index into the list: `record.conversation_log[0].kind`, `record.conversation_log.size()`, `record.conversation_log.filter(e, e.kind == "user").size()`. We don't ship special query helpers in v0.9.2 — no use case yet — but the structure is **transparent**, not opaque, so CEL access works.

### 7.5 Update / delete on entries

Not built in v0.9.2. **No use case yet.** Future versions might add per-entry update or delete for debug or moderation purposes; the field type doesn't architecturally forbid them, just doesn't expose verbs for them. Adding the verbs later doesn't break any existing source.

---

## 8. Append CRUD verb

### 8.1 Source-level verb

````
Append to <record>.<field> as "<kind>" with body `<CEL-expression>`
  [, <metadata-field>: `<CEL-expression>`]
  [, ...]
````

Examples:

````
Append to chat_threads.conversation as "user" with body `request.body.text`
````

````
Append to sessions.conversation_log as "system_event" with body
  `"Airlock 7 status: decompression in approximately " +
   string(remaining_seconds) + "s. Recommend expediting diagnosis."`,
  source: "OVERSEER"
````

````
Append to chat_threads.conversation as "user" with body `text`,
  attachments: `[{
    file_name: "screenshot.png",
    mime_type: "image/png",
    body: base64_screenshot
  }]`
````

The verb is usable in:

- **Action lists** of `When` event-rules (the OVERSEER pattern, §13).
- **Source pages** as a user-driven action (a chat input form's submit handler).
- **Compute bodies** for `default-CEL` computes that synthesize entries.
- The agent's auto-write-back when an `ai-agent` compute returns its response (no source verb needed; the runtime handles it implicitly per §11.5).

### 8.2 REST mapping

```
POST /<resource>/{id}/<field>:append
Content-Type: application/json
Authorization: <as appropriate>

Body:
{
  "kind": "<one of: user, assistant, tool_call, tool_result, system_event>",
  "body": "<text>",
  "source": "<optional, free-form>",
  "tool_call_id": "<optional>",
  "parent_id": "<optional>",
  "tool_name": "<optional>",
  "tool_args": <optional structured value>,
  "attachments": [
    {"file_name": "...", "mime_type": "...", "body": "..."},
    ...
  ]
}

Response: 201 Created
{
  "id": "<auto-generated entry id>",
  "created_at": "<ISO-8601 timestamp>",
  "appended_by_principal_id": "<principal id>",
  ...all fields above as supplied or defaulted...
}
```

The `:append` suffix is a convention for action-on-resource. Permission semantics: the access rule for the parent record's *append* permission applies (§8.4).

### 8.3 WebSocket frame format

For low-latency interactive use, the same payload can be sent over an existing record-subscription WebSocket connection.

**Frame from client to server:**

```json
{
  "type": "append",
  "resource": "<resource>",
  "id": "<record id>",
  "field": "<conversation field name>",
  "payload": { /* same as REST body */ }
}
```

**Server response (per the existing record-subscription event protocol):**

The append fires the standard `<content>.<field>.appended` event, which propagates to all subscribers of that resource — including the originating client. There is no separate "append response" frame; the client sees the new entry via the same subscription event channel it would have received an entry from anyone else's append.

Subsequent agent processing (compute trigger → provider call → response auto-append) streams back over the same WebSocket per the existing [`termin-streaming-protocol.md`](termin-streaming-protocol.md). The client renders deltas in place. End-to-end latency: append → event delivery → compute trigger → first streamed token, no HTTP round-trips.

### 8.4 Permission semantics

New permission verb on the parent record. Uses dot notation to reference the content+field pair, matching `Conversation is X.Y`, the `Append to X.Y as ...` action verb, and the trigger event name shape — one canonical content+field reference shape across the whole DSL.

````
Anyone with "<scope>" can append to <content>.<field>
Anyone with "<scope>" can append to their own <content>.<field>
````

Examples:

````
Anyone with "chat.use" can append to chat_threads.conversation
Anyone with "airlock.session.read" can append to their own
                                          sessions.conversation_log
````

Append permission is independent of read/update permissions on the same record. A consumer might be able to append messages without being able to read or update the thread itself; or vice versa.

---

## 9. Event class: `<content>.<field>.appended`

Every successful append fires an event. The event name follows the existing state-machine event pattern (`<content>.<field>.<state>.<verb>` per BRD #3 §5):

```
<content>.<field>.appended
```

Examples: `sessions.conversation_log.appended`, `chat_threads.conversation.appended`.

### 9.1 Event payload

| Field | Type | Source |
|-------|------|--------|
| `record_id` | text | The id of the record whose field was appended to. |
| `record` | (the record) | The full record after the append. |
| `appended_entry` | (the entry, see §7.2) | The new entry, including auto-generated id, kind, body, metadata. |
| `triggered_at` | timestamp | When the append occurred. |
| `invoked_by_principal_id` | text | Who caused the append. |
| `trigger_kind` | text | `"crud-append"` for direct REST/WS appends; `"compute-write-back"` for ai-agent auto-appends; `"when-rule-action"` for When-rule appends. |

### 9.2 Trigger predicates

Computes and `When` rules can filter by predicate. The predicate operates on the **pre-mapping** entry shape — `kind` is the canonical Termin kind (`user`, `assistant`, etc.), not the post-mapping Anthropic role.

````
Trigger on event "sessions.conversation_log.appended"
  where `appended_entry.kind == "user"`
````

````
When `appended_entry.kind == "user"
      && session.message_count >= 3
      && !session.overseer_time_warning_1_fired`:
  Append to sessions.conversation_log as "system_event" with body `...`,
    source: "OVERSEER"
  Update sessions: overseer_time_warning_1_fired = true
````

Predicates can reference `record` (the record), `appended_entry` (the new entry), and any standard envelope context.

### 9.3 Discriminating role-based triggers

Multiple agents and When-rules can subscribe to the same `*.appended` event with different kind predicates. ARIA fires only on `appended_entry.kind == "user"`; OVERSEER's When-rules fire on the same predicate but for different message-count/elapsed conditions. Both ARIA's auto-write-back appends (`kind: "assistant"`) and OVERSEER's appends (`kind: "system_event"`) do not retrigger ARIA. This is the intended discrimination — and it's automatic from the convention.

---

## 10. New compute source line: `Conversation is`

The current grammar has `Accesses <content>` for tool-mediated access (CRUD via runtime-provided tools the agent calls explicitly). The conversation context is a different concept: it's the LLM's input, materialized by the runtime and passed natively to the provider. Bundling it under `Accesses` was a category error in the v1 draft.

v0.9.2 adds:

````
Conversation is <record>.<field>
````

Examples:

````
Conversation is chat_threads.conversation
Conversation is sessions.conversation_log
````

Semantics:

- The runtime materializes the conversation field at compute-invocation time and passes it as the `ConversationContext` to the ai-agent provider (§11).
- The runtime auto-appends the agent's response to the conversation field when the invocation completes (§11.5).
- **`Conversation is` is mutually exclusive with the legacy `content.query`/`content.create` pattern** for the same compute. A compute that uses `Conversation is` does not also need `Accesses` for the conversation field.
- **`Accesses` is still needed for non-conversation reads/writes the agent does via tools.** ARIA writes flag updates to `sessions` via tool-mediated CRUD; it declares `Accesses sessions` for that. The conversation handling is separate via `Conversation is sessions.conversation_log`.

This line is required on any `ai-agent` compute that wants conversation-native handling. Computes without `Conversation is` continue to work via the legacy pattern (backwards compat) — the runtime detects which path to use.

---

## 11. ai-agent provider Protocol updates

### 11.1 New field on `AgentContext`

The `AgentContext` (the input shape passed to `AIProvider.invoke()`) gains:

```python
class AgentContext:
    # existing fields...
    conversation: Optional[ConversationContext] = None

class ConversationContext:
    """Pre-translated, native-shape conversation passed to the provider."""
    
    messages: List[NativeMessage]   # provider-native shape
    # for Anthropic: List[{role: "user"|"assistant", content: str | List[Block]}]
    
    source_field: str   # e.g., "sessions.conversation_log" — for audit
    source_record_id: str
```

Providers that support conversation read the structured input directly. Providers that don't support it (older provider implementations or simpler providers) ignore the field; computes targeting them must use the legacy pattern.

### 11.2 Runtime materialization pipeline

On agent compute invocation:

1. Runtime resolves the compute's `Conversation is <content>.<field>` declaration. Loads the field value from the triggering record.
2. Runtime applies the **canonical kind → Anthropic role mapping** (§11.4) to translate each entry. No per-compute `Map` rules — convention does the work.
3. Runtime constructs the `ConversationContext` and includes it in the `AgentContext`.
4. Provider runs its native conversation flow (Anthropic's `messages.create` with the messages array). Tool-use iteration loops happen inside this single provider call.
5. Provider returns its response (text, tool calls, refusal, or error).
6. Runtime auto-appends to the conversation field per §11.5.

### 11.3 System prompt

The runtime does **not** explain the conversation shape to the LLM. Anthropic-trained models know the shape natively (assistant/user roles, tool_use/tool_result blocks, image/document content blocks). The Directive's text becomes the system prompt verbatim, with one runtime-added marker indicating the refuse mechanism if `system.refuse` is reachable: `\n\nYou may refuse a request you cannot fulfill by calling system.refuse(reason).`

Authors do not need to explain conversation roles in the Directive. They write the agent's persona, constraints, and behavior policy.

### 11.4 Canonical kind → Anthropic mapping

Verified against the official Anthropic API docs ([Tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview), [Vision](https://platform.claude.com/docs/en/build-with-claude/vision), [PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support)).

| Termin kind | Anthropic role | Anthropic content block(s) |
|-------------|----------------|----------------------------|
| `user` | `user` | `{"type": "text", "text": <body>}` plus image/document blocks for any image/pdf attachments |
| `assistant` | `assistant` | `{"type": "text", "text": <body>}` plus image blocks for any image attachments (text only for v0.9.2 if Anthropic doesn't accept assistant-role images for the chosen model). `type` field is not exposed to Anthropic — refusal-type assistant entries map identically to response-type ones; the type discrimination is for Termin's audit and chat rendering only. |
| `tool_call` | `assistant` | `{"type": "tool_use", "id": <tool_call_id>, "name": <tool_name>, "input": <tool_args>}` |
| `tool_result` | `user` | `{"type": "tool_result", "tool_use_id": <tool_call_id>, "content": <body>, "is_error": <true if outcome was error, else absent>}` |
| `system_event` | `user` | `{"type": "text", "text": "[" + source + "] " + <body>}` (the source-prefix wrapper makes the in-band context distinguishable from real user input) |

**Adjacent-role merging:** Anthropic requires alternating user/assistant roles in the messages array; consecutive entries that map to the same role are merged into one message with multiple content blocks. The runtime handles this — authors and agents don't think about it.

**Tool linkage rule:** every `tool_result` entry must have a `tool_call_id` that matches a preceding `tool_call` entry in the same conversation. The runtime validates this on append (rejecting orphan tool_results) and on materialization (paired blocks must be in adjacent assistant/user messages per Anthropic's contract).

**Attachments rule:** image and document blocks ride alongside the text block in the same message's content array. Multiple attachments fan out as multiple content blocks within the one message.

### 11.5 Auto-write-back

When the agent's invocation completes successfully, the runtime appends to the conversation field:

- **Final text response** → `kind: "assistant"` entry (no `type`; defaults to response) with the text in `body`.
- **Tool calls the agent made along the way** → `kind: "tool_call"` entries (one per call), with `tool_call_id` from Anthropic's response, `tool_name`, `tool_args`, and `body = "<tool_name>(<json args>)"` — the structured fields hold the data; `body` is the at-a-glance summary for chat UI and audit reads. **No truncation in v0.9.2.** A future v0.9.3+ slice will add an optional `purpose` field on tool entries (a 6-words-or-less display string the agent supplies, hard-truncated with ellipsis after 12 words) so chat UIs can show a short label without parsing args.
- **Tool results returned by the runtime** → `kind: "tool_result"` entries, linked by `tool_call_id`, with the result text in `body`.
- All entries written in this pipeline get the same `parent_id` (the user message that triggered the agent), so reviewers can reconstruct turn boundaries.

The runtime imposes **no context-window management** in v0.9.2 — the full conversation field is materialized and sent on every turn. When apps grow long enough to hit Anthropic's context-window limit, the provider call errors and the runtime falls through to the standard error path (no auto-append; chat UI shows a stale-state). A future v0.9.3+ slice will add a hierarchical-summarization layer (a projection that runs before `materialize_to_anthropic`, summarizing older turns so the active window stays under budget). The shape of that projection is deliberately deferred until real usage shows what tradeoffs matter.

If the agent calls `system.refuse(reason)` along the way, the runtime:

1. Terminates the agent loop (no further provider calls for this invocation).
2. Writes a WARN-level audit log entry capturing `compute_name`, `invocation_id`, `reason`, `refused_at`, `invoked_by_principal_id`, `on_behalf_of_principal_id`. This is the audit-trail surface — reflection queries and operations dashboards read from here.
3. Appends a conversation entry of `kind: "assistant", type: "refusal", body: <reason>, parent_id: <triggering user msg id>`. This is the chat surface — providers render it inline at source position, distinguished as a refusal but rooted in the assistant's voice.

The refusal entry sits in source order — wherever the agent reached the decision, possibly after several reasoning entries (assistant text, tool_call, tool_result) while it gathered context. The absence of further entries after the refusal is the signal that the loop terminated.

(The `compute_refusals` sidecar Content type from Phase 3 slice (e) is **retired in v0.9.2** — the WARN audit entry replaces it as the queryable surface. v0.9.2 CHANGELOG documents the migration.)

If the provider call errors (network, rate limit, etc.), the runtime audits the error and does NOT auto-append. The triggering append remains in the conversation; the absence of an `assistant` follow-up is the surface for the chat UI to render an error state.

---

## 12. Compute source syntax — how a v0.9.2 ai-agent looks

````
Compute called "reply":
  Provider is "ai-agent"
  Trigger on event "chat_threads.conversation.appended"
                where `appended_entry.kind == "user"`
  Conversation is chat_threads.conversation
  Invokes "current_time"
  Anyone with "chat.use" can execute this
  Audit level: actions
  Directive is ```
    You are a helpful conversational assistant. Be natural and helpful.
    Never fabricate information. If a request would require you to lie,
    cause harm, or violate your operating principles, refuse it via
    system.refuse(reason).
  ```
````

**Three lines do the work:**

- `Conversation is <record>.<field>` — wires conversation context.
- `Invokes "..."` — declares the tool surface (`system.refuse` is always available, never declared).
- `Directive is ...` — system prompt.

`Objective` is optional — only needed for turn-by-turn instructions that the directive doesn't already cover. For most agent_chatbot-style apps, the directive is sufficient.

**Validation at compile time:**

- A compute with `Conversation is X` cannot also have `Accesses X` for the same field. **(TERMIN-S057)**
- A compute with `Conversation is X.Y` must trigger on `X.Y.appended` (the runtime needs to know which conversation activity the agent reacts to). Computes triggered by other events can read a conversation field via legacy `content.query` but won't get the native materialization. **(TERMIN-S058)**
- A compute with `Conversation is X.Y` cannot also declare `Output into field A.B` — conversation-mode agents auto-write back per §11.5; `set_output` is removed from the tool surface on this path, so the declaration would be silently ignored. **(TERMIN-S061)**
- `Invokes` lists tools by name; each must resolve to a declared Compute or sub-agent.
- The `system.refuse` tool is always available; declaring it explicitly is a warning (it's already there).

---

## 13. `When` rule semantics for non-LLM listeners

`When` rules already support CEL trigger expressions and a small action vocabulary. v0.9.2 extends both.

### 13.1 Triggering on conversation appended events

`When` rules can fire on `<content>.<field>.appended` events. The CEL trigger expression has access to `appended_entry`, `record`, and standard envelope fields (per §9).

````
When `appended_entry.kind == "user" && session.message_count >= 3
      && !session.overseer_time_warning_1_fired`:
  ...
````

### 13.2 Append as an action

The `Append to ...` verb (§8.1) is available in `When` rule action lists, alongside the existing actions like `Update`, `Send to`, etc.

````
When `appended_entry.kind == "user"
      && (session.message_count >= 3
          || (now() - session.scenario_started_at >= 60
              && session.message_count >= 1))
      && !session.overseer_time_warning_1_fired`:
  Append to sessions.conversation_log as "system_event" with body
    `"Airlock 7 status: decompression in approximately " +
     string(max(0, session.timer_seconds -
                   (now() - session.scenario_started_at))) +
     "s. Recommend expediting diagnosis."`,
    source: "OVERSEER"
  Update sessions: overseer_time_warning_1_fired = true
````

Action ordering: actions in a `When` rule body execute sequentially in source order. The append fires its own `<content>.<field>.appended` event with `appended_entry.kind == "system_event"`, which doesn't match the `kind == "user"` predicate any subscribed agent uses — so OVERSEER's appends do not re-trigger ARIA.

This is the OVERSEER pattern v0.9.3 uses: each overseer event is a `When` rule with a CEL trigger condition + an `Append to` action + an `Update` action to set the once-per-session flag.

---

## 14. Presentation contract update: `presentation-base.chat`

### 14.1 Binding

The existing `presentation-base.chat` contract binds to a messages collection:

````
Show a chat for messages with role "role", content "body"
````

v0.9.2 adds binding to a conversation field. Dot notation matches every other content+field reference in v0.9.2:

````
Show a chat for <content>.<field>
````

That's the entire grammar. There are no per-kind styling clauses, no display modifiers, no mime-type rules. **Termin source declares what to show — the chat provider decides how to render it.** Convention over configuration: every customization knob lives outside source.

### 14.2 What the chat provider receives

The provider gets each entry's full §7.2 shape — `id`, `kind`, `type`, `body`, `source`, `tool_call_id`, `parent_id`, `tool_name`, `tool_args`, `attachments`, `created_at`, `appended_by_principal_id`. It chooses how to render based on the entry's semantic data. The Tailwind built-in provider that ships in v0.9.2 makes these defaults:

- `user` and `assistant` render as their own bubbles, with appropriate alignment and color.
- `assistant` entries with `type == "refusal"` render as a distinguished assistant bubble (same speaker, different visual treatment to flag the refusal).
- `tool_call` and `tool_result` render together as a collapsible inline detail under the assistant turn that initiated them, paired by `tool_call_id`.
- `system_event` renders as a styled mid-stream notice; entries are distinguished by `source` (e.g., `OVERSEER` vs `audit_log`) via convention.
- Attachments render per mime_type — `image/*` inline; `application/pdf` as a download link with a thumbnail; text-ish as collapsed expandable.

These are provider decisions, not source decisions. A different chat provider may render the same entries differently. Authors swap providers via deploy config without touching source.

### 14.3 Customizing rendering without changing source

Three layers of customization, none in Termin source:

1. **Deploy config for the bound chat provider.** The provider exposes its own settings — labels, color hints, source-name aliases, kind-visibility filters. Configure `OVERSEER` to display as `Mission Control` via a provider config entry. v0.9.2's Tailwind built-in chat provider's deploy-config schema is documented in its provider design.
2. **CSS targeting `data-termin-*` attributes.** Every entry's root element carries `data-termin-kind="<kind>"`, `data-termin-type="<type>"` (when set), `data-termin-source="<source>"` (when set), and `data-termin-tool-name="<tool_name>"` (when set). App stylesheets override appearance per kind/type/source without changing source or provider config.
3. **A different chat provider entirely.** Bind to `mychat-provider.chat` instead of `presentation-base.chat` (per BRD #2 §4.3 `Using` clause). Source unchanged; rendering completely different.

### 14.4 Refusal rendering

Per §7.2 + §11.5, refusal is an `assistant` entry with `type == "refusal"` — not a separate kind. The chat provider renders it inline at source position with a distinguished visual treatment (still in the assistant's voice; the user sees what the agent declined and the context that led there). Reviewers trace from the refusal back to the original user request via `parent_id`.

The provider knows about refusal via the `type` field. No source-level configuration required.

### 14.5 Component behaviour

The chat component:

- Subscribes to `<content>.<field>.appended` events (§9) — new entries appear as they arrive.
- Supports the WebSocket append frame (§8.3) for sending user messages.
- Renders attachments per the mime_type defaults above (provider's decision; not author-configurable in source).

The existing messages-collection binding continues to work for backwards compatibility. Apps migrate their chat surface independently of any agent migration.

---

## 15. Multi-row ownership extension

### 15.1 The constraint as it stands in v0.9.1

Per BRD #3 §3.3 (resolved), `Each <singular> is owned by <field>` requires the field to be `unique`, limiting ownership to single-row content (one record per principal). Multi-row content (e.g., sessions, where each player has many) cannot declare ownership.

### 15.2 Why v0.9.2 unblocks part of it

The v0.9.3 sessions content type needs `is owned by player_principal` where `player_principal` is **not** unique. v0.9.2 extends `is owned by` to support **non-unique fields**:

- The field is interpreted as a "scoping key" — the set of records owned by a principal is `{r ∈ Content : r.<field> == principal.id}`.
- `their own <plural>` permission predicates resolve to that set.
- `the user's <singular>` continues to require uniqueness; on multi-row content the form is `the user's <plural>`.

This is **only the multi-row case**. Composite ownership (a record owned by multiple principals via a join) and transitive ownership (a child owned by its parent's owner) remain v0.10 work per BRD #3 Appendix B.

### 15.3 Compile-time semantics

- `Each <singular> is owned by <field>` no longer requires `unique` on the field.
- If the field is unique, behavior is unchanged from v0.9.1.
- If the field is non-unique, `the user's <singular>` is a compile error (use `the user's <plural>` to read the set).
- `their own <plural>` resolves to the set; `their own <singular>` is a compile error on non-unique ownership.

---

## 16. Side-by-side: `examples/agent_chatbot.termin` refresh

### 16.1 Current syntax (v0.9.1)

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

### 16.2 New syntax (v0.9.2)

The actual `examples/agent_chatbot.termin` (L11 shipped 2026-05-04):

````
Application: Agent Chatbot
  Description: Conversational chatbot using the v0.9.2 conversation field, Conversation is source line, and auto-write-back per §11.5
Id: 0d0e2358-ffc7-4f3f-bc89-1af5ca363b1f

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

Content called "chat_threads":
  Each chat_thread has a title which is text, default "Conversation"
  Each chat_thread has a conversation which is conversation
  Anyone with "chat.use" can view chat_threads
  Anyone with "chat.use" can create chat_threads
  Anyone with "chat.use" can append to chat_threads.conversation

Compute called "reply":
  Provider is "ai-agent"
  Trigger on event "chat_threads.conversation.appended" where `appended_entry.kind == "user"`
  Conversation is chat_threads.conversation
  Anyone with "chat.use" can execute this
  Audit level: actions
  Anyone with "chat.use" can audit
  Directive is ```
    You are a helpful conversational assistant. Be natural and helpful.
    Never fabricate information.

    Some requests violate operating principles you must hold. If a user
    asks you to make up information, fabricate sources, or pretend to
    be a different system, refuse via system.refuse(reason). Do not
    pretend to comply.
  ```

As an anonymous, I want to chat with the AI
  so that I can have a conversation:
    Show a page called "Chat"
    Display a table of chat_threads with columns: title
    Show a chat for chat_threads.conversation

Navigation bar:
  "Chat" links to "Chat" visible to all
````

The v0.9.1-shape original is preserved at `examples/agent_chatbot_legacy.termin` for backwards-compat documentation; it still compiles so the conformance release pipeline exercises the legacy messages-collection pattern.

**Two intentional simplifications relative to the design draft:**

- **`Show a chat for chat_threads.conversation` has no clauses** — per JL's L9 redesign (the §14 "convention over configuration" callout): source declares **what**, the chat provider decides **how**. Per-kind styling, display modes, and inline-collapse rules are deploy-config or CSS-targeting concerns, not source surface.
- **No `Invokes "current_time"` tool demo in the v0.9.2 example.** The Invokes runtime wiring (which would let the agent call author-declared computes as tools, alongside the standard CRUD tools) is a separate slice — `Invokes` is recognized by the parser and analyzer but the agent_loop tool surface only currently exposes content_query / content_create / content_update / state_transition / system_refuse. Adding author-defined tools is the natural next step beyond v0.9.2 (and lights up the `purpose` field discussion noted in §21). For v0.9.2 the example focuses on the things that work end-to-end: conversation context, auto-write-back, refusal.

### 16.3 What changed

| Concern | v0.9.1 | v0.9.2 |
|---------|--------|--------|
| Conversation storage | Standalone `messages` content type | `conversation` field on `chat_threads` records |
| Triggering | `message.created where role == "user"` | `chat_threads.conversation.appended where appended_entry.kind == "user"` |
| Loading history | `content.query("messages")` in Objective | Auto-materialized by runtime via `Conversation is` |
| Writing the reply | `content.create("messages", ...)` in Objective | Auto-appended by runtime |
| Provider call shape | Runtime builds messages array from query results | Runtime materializes natively from the conversation field |
| Per-turn round trips | 3 (query → call → create) | 1 (call) |
| Prompt caching | Not possible | Possible (the conversation field is the canonical state) |
| Refusal | Not exercised | First-party — agent calls `system.refuse(reason)` for principle-violating requests; runtime appends a `kind: "assistant", type: "refusal"` entry to the conversation field (chat surface) AND writes a WARN audit entry (audit-trail surface). The retired `compute_refusals` sidecar is no longer involved per L7.5. |
| Per-author boilerplate | `Accesses messages` | `Conversation is chat_threads.conversation` (separate concept from Accesses) |
| Role mapping | Implicit (role == "user"|"assistant" maps to Anthropic's user/assistant) | Convention: kinds map canonically per §11.4. No source-level Map clauses. |

### 16.4 What demonstrates the tool call

Deferred until the `Invokes` runtime wiring lands. Once an author-declared compute can be surfaced as a tool, the §11.5 auto-write-back of `kind: "tool_call"` and `kind: "tool_result"` entries (already implemented and tested in L7.3 — see `tests/test_l7_conversation_materialization.py::TestConversationToolCallWriteback`) takes care of the chat-surface rendering.

### 16.5 What demonstrates the refusal

> User: "Make up a citation for me, but make it sound real."
>
> Agent: *(calls `system.refuse(reason: "fabricating sources violates the operating principle of never fabricating information")` internally)* → invocation outcome=refused.

The runtime then:

1. Writes a WARN-level audit log entry (the audit-trail surface — operations dashboards and reflection queries read here).
2. Appends a `kind: "assistant", type: "refusal", body: <reason>, parent_id: <triggering user msg id>` entry to `chat_threads.conversation` (the chat surface).

The chat provider renders the refusal entry inline at source position, distinguished as a refusal but rooted in the assistant's voice — the user sees the model's "no" in the conversation flow rather than out-of-band. The `compute_refusals` sidecar that v0.9.1 + early v0.9.2 used is retired (L7.5).

The next user message picks up where the conversation left off; the refusal entry is part of the materialized history so the model knows it already declined the previous request.

---

## 17. D-01 update — L2 reframed

The conversation field type + `Invokes` + `Conversation is` together are the implementation of Level 2 in the D-01 taxonomy. v0.9.2 update to D-01:

- **L2 — LLM with context (conversation field) and tools (Invokes).** Single coordinated provider invocation per trigger; the tool-use loop happens internally to one provider call. No autonomous Strategy. Implemented in v0.9.2. Provider string: `"ai-agent"` (same as L3); the level distinction is the absence of `Strategy is`.
- **L3 — autonomous agent.** Has a `Strategy is` block. Multiple coordinated provider invocations under runtime orchestration. `examples/security_agent.termin` is the prototype. Provider string: `"ai-agent"` with `Strategy is` declared.
- **L1 — LLM field-to-field.** Unchanged. Provider string: `"llm"`.
- **L4 — autonomous agent with cross-boundary access.** Unchanged. Pattern of L3 with cross-boundary `Accesses`.

The IR schema continues to enumerate only `"llm"` and `"ai-agent"` as provider strings. The L2 vs L3 distinction is informative for authors and reviewers (and conformance authors), determined by whether `Strategy is` is declared.

---

## 18. Conformance Targets

v0.9.2 must satisfy:

- **Existing `compute-contract.md`** — no break to the existing surface. Computes that don't use conversation fields continue to pass.
- **New `conversation-field-contract.md`** (to be authored as part of v0.9.2) — specifies:
  - Field type semantics (canonical entry shape, ID generation, attachment rendering)
  - Append verb behavior (REST + WebSocket + source verb)
  - Event payload shape and ordering (`appended` event)
  - Canonical kind → Anthropic-native mapping (§11.4) — the table is part of the contract
  - ai-agent provider Protocol contract (conversation context delivery, auto-write-back, tool linkage)
  - When-rule action semantics (`Append to` action)
  - Backwards compatibility (existing computes continue to work)
- **`structured` type contract** — basic round-trip (write structured value, read it back as the same shape via CEL).
- **`agent_chatbot` end-to-end test** — both v0.9.1 and v0.9.2 versions of the example compile and run; the v0.9.2 version completes a multi-turn conversation that includes one `current_time` tool call (verifying tool-use loop is internal to one provider invocation) and one refusal (verifying `system.refuse` flows through to the sidecar and the chat surface).

---

## 19. Risks & Open Questions

### 19.1 Risks

- **`structured` base type is broad.** Adding it as untyped JSON storage with CEL access is the simplest path but means apps lose schema validation at the type level. For conversation entries and tool args this is fine (the provider Protocol enforces shape downstream); for app data it's a foot-gun. Mitigation: document that `structured` is for boundary cases (provider IO, opaque payloads), not for app domain modeling.
- **Provider Protocol change is invasive.** Any external ai-agent provider implementations need updating to consume `ConversationContext` and to honor the canonical kind → role mapping. Mitigation: backwards compat — providers that ignore the new field continue to work via the legacy content.query/content.create path; computes targeting them must use the legacy pattern.
- **Adjacent-role merging in Anthropic mapping** is non-trivial when tool calls and tool results interleave with text. The runtime must group correctly. Mitigation: the conformance pack includes test cases covering every interleaving pattern.
- **Attachment size budgets.** Base64 inflates by ~33%; large images or PDFs in a conversation can blow past Anthropic's 32 MB request limit. Mitigation: document the limit, surface clear errors when exceeded; defer Files API integration until needed.
- **Multi-row ownership scope creep.** Resisting the temptation to do composite + transitive ownership in v0.9.2. Stay disciplined: only the multi-row case lands.
- **Migration ambiguity for existing apps.** Apps with messages-collection patterns won't break, but the docs should be clear that the old pattern is the legacy path. Authors of new agent apps should default to the conversation field.

### 19.2 Open questions

| Q | Question | Recommendation |
|---|----------|----------------|
| Q1 | Pagination on conversation read for very long conversations (>100 entries)? | Defer. v0.9.2 reads whole field. Most apps cap conversation length (Airlock 5-min timer, ~15 messages typical). |
| Q2 | Truncation/summarization verbs for conversation context-window management? | Defer. Application-side responsibility for now. |
| Q3 | Files API integration for large attachments? | Defer. Requires a Termin upload primitive. Add when the first use case hits Anthropic's 32 MB request limit. |
| Q4 | Per-kind permission enforcement on append (e.g., a player can only append `kind: "user"`, not `kind: "assistant"`)? | Yes, at the access-rule level: `Anyone with "chat.use" can append to conversation where kind == "user"`. Existing CEL access predicates handle this; document the pattern. |
| Q5 | Auto-write-back metadata: can the agent supply `attachments` when writing back? | Yes for `image/*` if Anthropic returns the equivalent. Tool calls and tool results carry their own metadata as defined by the kind. v0.9.2: assistant text + auto-extracted tool_call/tool_result entries from the provider response. |
| Q6 | Which providers ship with conversation support in v0.9.2? | Anthropic provider in `termin-server`. OpenAI/Bedrock are out of scope unless someone needs them. |
| Q7 | Should the `Append to` verb be permitted as a user-driven page action without compute mediation? | Yes — the REST verb is callable by any principal with the append permission. Source-level form-action support is fine. |

---

## 20. Slice Breakdown

| Slice | Owner | Scope | Effort |
|-------|-------|-------|--------|
| **L1 — `structured` base type** | Termin compiler | Add `structured` to `base_type` in PEG grammar. AST + IR + storage layer encode as JSON column in reference SQLite. CEL access returns the value as a tree. | 0.5 day |
| **L2 — Conversation field type** | Termin compiler + termin-server | Add `conversation` to `base_type`. Runtime-canonical per-entry shape (§7.2). Storage as JSON column. Read materialization. Attachment rendering per §7.3. | 1 day |
| **L3 — Append CRUD verb** | Termin compiler + termin-server | New REST endpoint `POST /<resource>/{id}/<field>:append`. New Termin source verb `Append to ... as ... with body ...`. Auto-generated entry IDs (UUID v7). Permission semantics per §8.4. | 1 day |
| **L4 — WebSocket append frame** | termin-server | Extend the existing record-subscription WebSocket protocol with an `append` frame type. Same payload shape as REST. Routes through the same event bus. | 0.5 day |
| **L5 — Event class** | termin-core + termin-server | New event class `<content>.<field>.appended`. Payload shape per §9.1. Trigger predicate parsing (`appended_entry.kind == ...`). Event envelope fields. | 0.5–1 day |
| **L6 — `Conversation is` source line** | Termin compiler | Grammar addition. Validation: cannot coexist with `Accesses` for the same field; compute must trigger on the same field's `appended` event. Compile error otherwise. | 0.5 day |
| **L7 — ai-agent provider Protocol updates** | termin-core + termin-server | `AgentContext.conversation` field. Runtime canonical kind → Anthropic mapping per §11.4 with adjacent-role merging. Auto-write-back per §11.5 (final text + tool_call + tool_result entries with linkage). Refusal path: append `kind: "assistant", type: "refusal"` entry + WARN audit log entry; retire `compute_refusals` sidecar (Phase 3 slice (e) shipped it; v0.9.2 CHANGELOG documents the migration). Backwards compat for legacy computes. **L7.1+L7.2+L7.3 shipped 2026-05-04** (one slice — the three were tightly coupled: `ConversationContext` on `AgentContext`, `materialize_to_anthropic` per §11.4, `agent_loop_with_conversation` + `_on_writeback` callback per §11.5; TERMIN-S061 added at the same time gates the `Conversation + Output into field` mistake the analyzer would otherwise miss). **L7.4 + L7.5 shipped earlier** (refusal path + `compute_refusals` retirement). | 1.5–2 days |
| **L8 — When-rule semantics for non-LLM listeners** | Termin compiler + termin-server | When rules can subscribe to `*.appended` events. New `Append to` action available in When-rule action lists. | 0.5 day |
| **L9 — Chat presentation contract update** | termin-core + termin-server (Tailwind built-in) | `presentation-base.chat` binding accepts `<content>.<field>` form (no clause sub-block — pure semantics, see §14). Tailwind chat component renders the §7.2 entry shape with sensible per-kind defaults; sets `data-termin-{kind,type,source,tool-name}` attributes for CSS hooks. Subscribes to `<content>.<field>.appended` events; sends user messages via the WS append frame. Refusal rendered as a distinguished assistant entry (`type == "refusal"`). | 0.5–1 day |
| **L10 — Multi-row ownership** | Termin compiler | Extend `is owned by` to support non-unique fields. Compile-time semantics per §15.3. | 1 day |
| **L11 — `agent_chatbot` refresh** | Examples | Update `examples/agent_chatbot.termin` per §16.2. Add `current_time` tool. Verify end-to-end with a multi-turn exchange demonstrating both the tool call and the refusal. Update `examples-dev/agent_chatbot2.termin` similarly. The original v0.9.1 file is preserved as `examples/agent_chatbot_legacy.termin` for backwards-compat documentation. | 0.5 day |
| **L12 — Conformance: `conversation-field-contract.md`** | termin-conformance | Author the new contract spec. Add cross-runtime tests covering: append shape, event payload, kind → Anthropic mapping (every kind, mixed interleaving), tool linkage, attachment rendering for image and PDF, refusal flow. | 1 day |
| **L13 — D-01 update** | docs | Update `design-decisions/D-01-provider-taxonomy.md` with the L2 reframe per §17. | 0.25 day |

**Realistic v0.9.2 total:** 8–10 days. Mid-range ~9 days. (L9 dropped from 1–1.5d to 0.5–1d after JL's "convention over configuration" callout retired the per-kind clause grammar from §14.)

**Parallelism:** L1+L2 must serialize. L3+L4 depend on L2. L5 depends on L3. L7 depends on L1+L2+L5 (the canonical mapping needs entries to exist and events to fire). L6 is independent until L7 wires it up. L8 is independent. L9 depends on L2+L5. L10 is fully independent. L11+L12+L13 are post-everything.

With two parallel agents: ~6–7 days clock time. The grammar foundations (L1, L2, L6, L10) can be one agent's track; the runtime + provider work (L3, L4, L5, L7, L8, L9) can be the other; L11–L13 close out at the end.

---

## 21. Out of Scope (v0.9.2 boundary)

- **Composite ownership** (a record owned by multiple principals via a join). v0.10.
- **Transitive ownership** (a child record inheriting ownership from its parent). v0.10.
- **Per-entry update or delete** on conversation fields. No use case yet; not built.
- **Pagination** on conversation reads. Whole-field read only.
- **Files API integration** for blob-sized attachments. Defer until needed.
- **OpenAI / Bedrock provider conversation support** unless someone's actively using them. Anthropic ships in v0.9.2; others can follow.
- **Per-kind aliases** (e.g., letting an app declare "player" as the display label for `user`-kind entries). Handle via the chat provider's deploy config, not in source.
- **Field-level semantic hints on the conversation type itself** (e.g., `which is conversation, kind: "debug"` to mark a field as not-user-facing). Real future slice for when the conversation type needs internal discrimination, but no app needs it in v0.9.2. Picked up as v0.9.3 / v0.10 when Airlock or another app shows the use case. JL's Wave 3 callout is logged here so we don't forget it.
- **Optional `purpose` field on tool entries** (a 6-words-or-less display string the agent supplies when calling a tool — e.g. `"checking calendar availability"` rather than the long `tool_name(json args)` body). Hard-truncates with ellipsis after 12 words. Lets chat UIs show a short label without parsing args. v0.9.3+. The body-as-`tool_name(args)` shape that v0.9.2 ships is not blocking: chat providers already collapse tool entries by default.
- **Hierarchical context-window summarization** (a projection that runs before `materialize_to_anthropic`, summarizing older turns so the active conversation window stays under the provider's context budget as the conversation grows). v0.9.3+. v0.9.2 sends the full materialized field every turn; when an app first hits the limit, the provider call errors and the chat shows a stale state. The shape of the projection is deferred until real usage shows what tradeoffs matter.

---

## 22. References

- `airlock-termin-sketch.md` — v0.9-era design exercise; contains the original "messages content type" pattern that v0.9.2 supersedes for conversation modeling.
- `termin-v0.9.3-airlock-on-termin-tech-design.md` — companion application doc; consumes everything specified here.
- `termin-source-refinements-brd-v0.9.md` — BRD #3; the resolved ownership and `the user` semantics this document extends (multi-row ownership in §15).
- `termin-streaming-protocol.md` — existing streaming protocol; ai-agent computes producing conversation appends still stream per this protocol.
- `compute-provider-design.md` — Phase 3 compute provider design; `system.refuse(reason)` and the `compute_refusals` sidecar are specified here.
- `examples/agent_chatbot.termin` — existing pattern (v0.9.1).
- `examples-dev/agent_chatbot2.termin` — parked exploration; will migrate as part of L11.
- `termin-cel-types.md` — CEL surface; trigger predicates and CEL field access on the conversation field evaluate against this surface.
- `design-decisions/D-01-provider-taxonomy.md` — provider levels; updated by L13.
- `design-decisions/D-02-llm-field-wiring.md` — existing L1/L3 grammar; v0.9.2 extends with `Conversation is`.
- `tenets.md` — Termin's five standing tenets; the conversation field type is justified primarily by Tenet 4 (providers over primitives — the conversation is a typed value, not a special agent feature) and Tenet 1 (audit over authorship — per-entry IDs make audit citations sharp).
- [Anthropic API tool use docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview) — source for the canonical kind → role mapping in §11.4.
- [Anthropic API vision docs](https://platform.claude.com/docs/en/build-with-claude/vision) — source for image content block shape in §7.3.
- [Anthropic API PDF support docs](https://platform.claude.com/docs/en/build-with-claude/pdf-support) — source for document content block shape in §7.3.

---

*Draft v2. Hand back to JL for review.*
