# D-09: Chat Presentation Component

**Status:** Design complete, implementation pending
**Date:** April 2026
**Authors:** Jamie-Leigh Blake & Claude Anthropic
**Depends on:** Presentation IR v2 (component trees), WebSocket infrastructure

---

## Summary

A new `chat` component type in the IR for conversational interfaces. Not AI-specific — works for multi-user chat rooms, AI assistant conversations, mixed human+AI threads, or any Content that implements the chat interface. Includes integrated input with file attachment support and streaming responses via WebSocket.

---

## Design Decisions

### D-09.1: New `chat` Component Type

The chat component is a first-class type in the component tree IR, alongside `data_table`, `form`, `text`, etc.

**DSL syntax:**
```
As a user, I want to chat with the assistant
  so that I can get help:
    Show a page called "Chat"
    Show a chat for messages
```

**IR:**
```json
{
  "type": "chat",
  "props": {
    "source": "messages",
    "role_field": "role",
    "content_field": "content",
    "timestamp_field": "created_at",
    "sender_field": "submitted_by",
    "file_field": "attachments"
  }
}
```

**Not AI-specific.** The chat component renders any Content that provides the chat interface fields. Examples:
- `agent_chatbot`: AI assistant conversation (messages with role "user"/"assistant")
- Multi-user chat room: messages with role = username, no AI involved
- Mixed thread: helpdesk comments where both humans and an AI triage agent post
- Support chat: customer + agent conversation with optional AI suggestions

### D-09.2: Chat Interface Contract

Any Content can be used with the `chat` component if it provides these fields:

**Required fields:**
| Field | Type | Description |
|-------|------|-------------|
| `role` or equivalent | text/enum | Who sent the message (user, assistant, system, or a username) |
| `content` | text | Message body |

**Optional fields:**
| Field | Type | Description |
|-------|------|-------------|
| `created_at` | datetime/automatic | Timestamp for ordering |
| `submitted_by` | text | Identity of the sender (for multi-user) |
| `attachments` | text/list | File references or URLs |

The compiler validates that the `source` Content has the required fields. Field names are configurable via props (not hardcoded to "role"/"content").

### D-09.3: Integrated Input

The chat component includes its own input area as part of the contract. This is not a separate `form` component — it's integrated into the chat UI.

**Contract-level features (any conforming runtime MUST support):**
- Text input area for composing messages
- Send button (and Enter key to send)
- File attachment button (uploads stored as references)

**Recommended features (SHOULD support, not conformance-tested):**
- Image paste from clipboard
- Drag-and-drop file attachment
- Markdown rendering in messages
- Typing indicators for multi-user
- Message editing/deletion
- Emoji picker

**Reference runtime:** Simple text input + send button + basic file upload. No clipboard paste, no markdown, no typing indicators.

### D-09.4: Streaming via WebSocket

The chat component supports streaming responses using the existing WebSocket infrastructure.

**Behavior:**
1. User submits a message → POST to create the user message record
2. If the Content is associated with an AI Compute (agent), the Compute triggers
3. The Compute streams tokens via WebSocket as they're generated
4. The chat UI renders tokens incrementally in the assistant's message bubble
5. On completion, the full assistant message is written to the Content table

**WebSocket message format (streaming):**
```json
{
  "type": "chat_stream",
  "content": "messages",
  "record_id": 42,
  "delta": "Here is the next chunk of",
  "done": false
}
```

```json
{
  "type": "chat_stream",
  "content": "messages",
  "record_id": 42,
  "delta": "",
  "done": true
}
```

**Fallback for non-streaming:** If the Compute doesn't support streaming (CEL functions, transforms), the chat component falls back to: show "thinking" indicator → wait for completion → render full response. No WebSocket needed.

### D-09.5: Content Compatibility

Any Content with the right fields can use the `chat` component. The compiler checks compatibility:

**Valid:**
```
Content called "messages":
  Each message has a role which is one of: "user", "assistant"
  Each message has a content which is text
  Each message has a created at which is automatic
```

**Also valid (multi-user chat):**
```
Content called "chat messages":
  Each chat message has a sender which is text, defaults to `the user.display_name`
  Each chat message has a body which is text
  Each chat message has a sent at which is automatic

...
  Show a chat for chat messages with role "sender", content "body"
```

The `with role "field", content "field"` syntax allows mapping custom field names to the chat interface. If omitted, the compiler looks for fields named "role" and "content" by convention.

---

## Implementation Plan

1. **DSL:** Add `Show a chat for {content}` syntax with optional field mapping.
2. **PEG Grammar:** New `chat_line` rule in story blocks.
3. **IR:** New `chat` ComponentNode type with source, field mapping props.
4. **Compiler:** Validate that source Content has required chat interface fields. Error if missing.
5. **Runtime:** Chat renderer in presentation.py — scrolling message list, input box, send POST, WebSocket subscription for streaming.
6. **Conformance:** Test that chat component renders, messages display, input submits, streaming works (if Compute-backed).

---

## Migration

The `agent_chatbot` example currently uses:
```
Display a table of messages with columns: role, content
Accept input for role, content
```

This will become:
```
Show a chat for messages
```

The table+form pattern still works for non-chat Content. The `chat` component is an alternative presentation, not a replacement for data_table.

---

## Open Questions (deferred)

- **Message threading:** Reply-to / thread support. Requires a `parent_id` field reference. (Deferred — flat chat first)
- **Read receipts:** For multi-user chat, tracking who has read which messages. (Deferred)
- **Message reactions:** Emoji reactions on messages. (Deferred — cosmetic)
- **Chat history pagination:** Loading older messages on scroll. Currently loads all. (Deferred — performance optimization)
