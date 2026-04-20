# Termin Streaming Protocol — Token-by-Token Delivery

**Status:** v0.8 shipping.

## Goal

When a Compute calls an LLM provider that supports streaming, the
relevant UI (chat bubble, form field, inline value) should render
output token-by-token as it arrives, rather than waiting for the
full response.

This protocol is runtime-agnostic and covers two streaming modes:

1. **Text streaming** — the provider emits raw text chunks. Used by
   "LLM" Computes that produce a single text output.
2. **Tool-use field streaming** — the provider emits JSON fragments
   for a tool call (e.g., `set_output`), and the runtime extracts
   per-field deltas. Used by "AI-agent" Computes, which respond to
   users exclusively via the `set_output` tool call. Tool-use
   streaming is the **primary path** for chat-style agents; text
   streaming is the narrower case.

## Why two modes

Agent Computes never emit free-form text as their response — they
produce structured output via a tool call. The tool's `input` is a
JSON object whose fields map to Content fields (or returned values).
For a chat-style agent, the tool call looks like:

```json
{
  "name": "set_output",
  "input": {
    "message": "Hello, world.",
    "confidence": 0.92
  }
}
```

Streaming tool-use means streaming this JSON object as it is being
generated. The runtime parses the incremental JSON and emits per-field
deltas on the wire.

Text streaming remains useful for non-agent LLM computes that produce
a single text blob.

## Actors

- **Provider** — an `AIProvider` implementation (Anthropic, OpenAI).
  Produces either a sync stream of text deltas or a sync stream of
  provider-specific events (content blocks, JSON fragments, tool
  calls). The runtime bridges sync→async with a thread + queue.
- **Compute runner** — picks streaming vs non-streaming per-Compute
  based on the Compute's level (LLM vs agent) and provider support.
- **Event bus** — pub/sub with prefix-match subscriptions.
- **WebSocket manager** — forwards event-bus events to WS subscribers.
- **Client component** — subscribes to the relevant stream channel
  and renders deltas in place.

## Channel naming

### Text streaming

```
compute.stream.<invocation_id>
```

Single channel per invocation. Delta events carry raw text.

### Tool-use field streaming

```
compute.stream.<invocation_id>
compute.stream.<invocation_id>.field.<field_name>
```

The base channel receives every event; the `.field.<name>` suffix
channel receives only events for that specific field. Clients that
care about a single output field (a chat bubble tracking only the
`message` field) subscribe to the field-specific channel.

Prefix subscriptions (`compute.stream.`) receive events for every
in-flight invocation and every field.

## Event payloads

### Text streaming — delta event

```json
{
  "channel_id": "compute.stream.<invocation_id>",
  "data": {
    "invocation_id": "<uuid>",
    "compute": "<compute_snake_name>",
    "mode": "text",
    "delta": "Hello",
    "done": false
  }
}
```

### Text streaming — terminal event

```json
{
  "channel_id": "compute.stream.<invocation_id>",
  "data": {
    "invocation_id": "<uuid>",
    "compute": "<compute_snake_name>",
    "mode": "text",
    "delta": "",
    "done": true,
    "final_text": "Hello, world."
  }
}
```

### Tool-use — field delta event

Emitted for each new chunk of a string-valued field.

```json
{
  "channel_id": "compute.stream.<invocation_id>.field.message",
  "data": {
    "invocation_id": "<uuid>",
    "compute": "<compute_snake_name>",
    "mode": "tool_use",
    "tool": "set_output",
    "field": "message",
    "delta": "Hello",
    "done": false
  }
}
```

### Tool-use — field done event

Emitted once when a string field closes (JSON quote character reached),
or when the enclosing content block ends for non-string fields. Carries
the final value of the field.

```json
{
  "channel_id": "compute.stream.<invocation_id>.field.message",
  "data": {
    "invocation_id": "<uuid>",
    "compute": "<compute_snake_name>",
    "mode": "tool_use",
    "tool": "set_output",
    "field": "message",
    "done": true,
    "value": "Hello, world."
  }
}
```

### Tool-use — invocation done event

Emitted after the last field completes. Carries the full tool-call
input for convenience.

```json
{
  "channel_id": "compute.stream.<invocation_id>",
  "data": {
    "invocation_id": "<uuid>",
    "compute": "<compute_snake_name>",
    "mode": "tool_use",
    "tool": "set_output",
    "done": true,
    "output": {"message": "Hello, world.", "confidence": 0.92}
  }
}
```

### Error event

```json
{
  "channel_id": "compute.stream.<invocation_id>",
  "data": {
    "invocation_id": "<uuid>",
    "compute": "<compute_snake_name>",
    "error": "<human-readable message>",
    "done": true
  }
}
```

## Scope of field-level parsing (v0.8)

Only **string-valued fields** are streamed character-by-character.
Numeric, boolean, and structured fields arrive as a single
`field_done` event when their content block completes.

This scope covers the dominant agent-chat case (a `message` or
`response` string field on `set_output`) while avoiding the complexity
of nested-object streaming. Richer field types can stream in a future
iteration without a protocol change — only the provider's
extract-deltas logic needs to grow.

## Lifecycle

### Text streaming

1. Compute runner creates `invocation_id`.
2. Runner calls `AIProvider.stream_complete(system, user_message)`.
3. Runner pumps each `(delta, done)` tuple onto
   `compute.stream.<invocation_id>` via `publish_stream_deltas`.
4. Runner persists the final text into the content table (normal
   path). The existing `content.*.created` event follows.

### Tool-use streaming

1. Compute runner creates `invocation_id`.
2. Runner calls `AIProvider.stream_agent_response(system, user, output_tool)`.
3. Runner publishes each event the provider yields:
    - `field_delta` → `compute.stream.<id>.field.<name>`
    - `field_done` → `compute.stream.<id>.field.<name>`
    - `done` → `compute.stream.<id>`
4. Runner persists final record using the `output` dict from the
   `done` event. Normal `content.*.created` follows.

Clients do not need to wait for `done` to render — they assemble
deltas in real time and trust the persisted record event for
finalization.

## Non-streaming fallback

A provider that does not support streaming returns the full result
from `complete()` / `agent_loop()`. The runtime publishes a single
terminal event (text mode) or a single `done` event (tool-use mode)
with `done: true` and the full payload. Clients handle both modes
identically — they just don't see intermediate deltas.

## Scope gating

WebSocket subscribers must carry the same scope as the Compute's
caller to receive stream events. Deltas do not bypass confidentiality.
If the final field value would be redacted for a given user, the
matching stream channel's events are not forwarded to that user's
WS connection.

## Client assembly — text streaming

Chat component maintains `pendingBubbles: Map<invocation_id, HTMLElement>`.

- On `{mode: "text", delta, done: false}`: get-or-create pending bubble
  for the invocation; append `delta`.
- On `{mode: "text", done: true, final_text}`: update the bubble's
  content to `final_text` (idempotent safety net), keep the bubble
  in a "settling" state until the matching `content.*.created`
  event arrives, then remove and let the persisted message render.

## Client assembly — tool-use streaming

Chat component maintains `pendingBubbles: Map<invocation_id, HTMLElement>`
and tracks `currentField` within each bubble.

- On `{mode: "tool_use", field, delta, done: false}`: get-or-create
  pending bubble; append `delta` if `field` matches the chat's
  `content_field` (the field the chat component displays).
  Other fields are ignored by the chat component (they may be
  relevant to other subscribers — filters, dashboards, etc.).
- On `{mode: "tool_use", field, done: true, value}`: if `field`
  matches, set the bubble's content to `value` (safety net), mark
  the bubble "settling".
- On `{mode: "tool_use", done: true, output}`: no-op for the chat
  component; the persisted record event will take over.

## Testing

All parts of the protocol are testable without a live LLM API:

- `AIProvider.simulate_stream(deltas)` — scripted deltas for text mode.
- `AIProvider.simulate_agent_response(events)` — scripted events for
  tool-use mode.
- Event bus tests assert the sequence of published events for a
  mocked Compute invocation.
- WebSocket integration tests subscribe a real WS client to the
  stream channel and assert deltas arrive in order.
- Provider-specific stream unit tests mock the SDK clients to feed
  canned JSON-fragment sequences through the real extraction logic.

Live API validation is a manual step with `ANTHROPIC_API_KEY` or
`OPENAI_API_KEY` set.
