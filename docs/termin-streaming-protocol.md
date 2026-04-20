# Termin Streaming Protocol — Chat Token-by-Token Delivery

**Status:** v0.8 scaffolding + v0.8.1 Anthropic integration.

## Goal

When a chat-driven Compute calls an LLM provider that supports streaming
(Anthropic `messages.stream`, OpenAI `chat.completions.create(stream=True)`,
etc.), the chat component should display the LLM's output token-by-token
as the tokens arrive, not as a single final message after the full
response completes.

This protocol defines how deltas flow from the provider through the
runtime to the browser, so any conforming runtime can implement
streaming without ad-hoc coupling between providers and UI.

## Actors

- **Provider** — an `AIProvider` implementation (Anthropic, OpenAI, …).
  Produces a stream of text deltas over a connection to the LLM service.
- **Compute runner** — the runtime code that invokes a Compute. For
  streaming-capable providers, calls `stream_complete()` and publishes
  each delta to the event bus.
- **Event bus** — the existing pub/sub bus. Streaming reuses the
  existing `publish(event)` API with a new channel pattern.
- **WebSocket manager** — forwards event-bus events to WS subscribers.
- **Chat component** — subscribes to the stream channel; renders deltas
  into a pending-message bubble; replaces with the final persisted
  message when the stream terminates.

## Channel naming

Streaming uses a dedicated channel pattern, distinct from `content.*`.

```
compute.stream.<invocation_id>
```

`<invocation_id>` is the UUID assigned when the compute starts executing.
Subscribers on this channel receive delta events for exactly one
invocation. The channel ID is per-invocation so the chat client can
correlate deltas to the in-progress message it is displaying.

For chat UIs, the chat client subscribes to `compute.stream.*`
(wildcard by prefix match, already supported by `EventBus.subscribe`)
so it receives deltas for any compute that emits them, regardless of
invocation id. The client correlates by invocation id inside each
event payload.

## Event payload

### Delta event (intermediate)

```json
{
  "channel_id": "compute.stream.<invocation_id>",
  "data": {
    "invocation_id": "<uuid>",
    "compute": "<compute_snake_name>",
    "delta": "Hello",
    "done": false
  }
}
```

Concatenating all `delta` values in order produces the complete
response text.

### Terminal event

```json
{
  "channel_id": "compute.stream.<invocation_id>",
  "data": {
    "invocation_id": "<uuid>",
    "compute": "<compute_snake_name>",
    "delta": "",
    "done": true,
    "final_text": "Hello, world."
  }
}
```

`done: true` signals end-of-stream. `final_text` is the complete
concatenated response, included for clients that joined mid-stream or
want to verify their own concatenation.

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

If the provider fails mid-stream, emit a terminal event with `error`
set. Clients display the error in place of the pending bubble.

## Lifecycle

1. Compute runner starts invocation. Creates `invocation_id`.
2. Compute runner calls `AIProvider.stream_complete(...)`, which
   returns an async generator yielding `(delta, done)` tuples.
3. For each `(delta, done=False)` yielded:
     - Runner publishes a delta event (see above).
4. When the generator exits or yields `(_, done=True)`:
     - Runner publishes a terminal event with the concatenated
       `final_text`.
     - Runner persists the final message to the content table (normal
       create-record path). This triggers the usual `content.*.created`
       event, which the chat component receives through its existing
       content subscription.
5. Chat UI displays the pending-bubble content until it receives both
   (a) the terminal event and (b) the `content.*.created` event for
   the new message. Then the pending bubble is removed and the real
   message takes its place. (Order of (a) and (b) is not guaranteed;
   clients must tolerate either.)

## Non-streaming fallback

A provider that does not support streaming falls back to `complete()`
and publishes a single terminal event with `done: true` and the
full `final_text` when complete. Chat UIs handle that case the same
way — they just don't see intermediate deltas.

## Scope gating

The existing scope model applies: a WS subscriber must hold the same
scope as the Compute's caller to see the deltas. Deltas do not bypass
confidentiality — if the final message would be redacted for this
user, so are the deltas. Enforcement point: the WS forwarder consults
`identity.scopes` for the subscribed connection and filters events
whose Compute source is not permitted.

## Client assembly

The chat component maintains a `pendingBubbles` map:

```
pendingBubbles: Map<invocation_id, {container: HTMLElement, text: string}>
```

On a delta event:
- If `invocation_id` is not in the map, create a pending bubble styled
  like an assistant turn, append it to the messages container, and
  add to the map.
- Append `delta` to the map's `text`; update the bubble's text content.

On a terminal event:
- Ensure the bubble exists with `final_text` as its content.
- Keep the bubble in a "settling" state until the corresponding
  `content.*.created` event arrives, at which point the real message
  takes over.
- If an error is set, replace the bubble content with the error
  (styled in red), leave it, and remove from the map.

## Testing

The protocol is testable without a live LLM API:

- `AIProvider.simulate_stream(deltas: list[str])` — a test helper that
  yields the provided deltas with `done=False` for all but the last,
  then `done=True` with the joined text.
- Event bus tests subscribe to `compute.stream.<id>` and assert the
  sequence of events produced by a mocked compute invocation.
- WebSocket integration tests subscribe a real WS client to the stream
  channel and assert deltas arrive in order, followed by the terminal
  event.

The Anthropic-specific `stream_complete()` implementation (wrapping
`client.messages.stream(...)`) is a separate concern, scoped to
v0.8.1, and can be tested against a recorded transcript for CI.
