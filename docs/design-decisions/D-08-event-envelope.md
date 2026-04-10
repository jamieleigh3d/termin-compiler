# D-08: Event Envelope vs Raw Record

**Status:** DECIDED 2026-04-10
**Decided by:** JL + Claude
**Affects:** Runtime (event handlers, CEL context), IR (event metadata)

---

## Decision

### Hybrid envelope with record promotion

Events carry a structured envelope internally. The CEL context for evaluating where clauses, event conditions, and Compute triggers promotes the record fields to the top level via the singular prefix AND provides the full envelope under `event.*`.

### CEL Context Shape

When a `message.created` event fires for a record `{"id": 1, "role": "user", "body": "Hello"}`:

```
message.role                → "user"           (record field via singular prefix)
message.body                → "Hello"          (record field)
message.created             → true             (synthetic: this was a create event)

event.type                  → "message.created" (envelope metadata)
event.content               → "messages"        (content type name)
event.record.id             → 1                 (full record via envelope)
event.timestamp             → "2026-04-10..."   (when the record was created)
event.identity.role         → "anonymous"       (who caused the event)
event.identity.scopes       → ["chat.use"]      (their scopes)
```

### Envelope Structure

```json
{
  "type": "message.created",
  "content": "messages",
  "singular": "message",
  "trigger": "created",
  "record": {"id": 1, "role": "user", "body": "Hello"},
  "timestamp": "2026-04-10T12:00:00Z",
  "identity": {
    "role": "anonymous",
    "name": "JL",
    "scopes": ["chat.use"]
  }
}
```

### Timestamp Semantics

`event.timestamp` reflects when the record was created/updated/deleted, not when the event was dispatched. If there's a lag between the database write and the event emission, the timestamp is still the record's creation time. This means the timestamp is stable — replaying the event produces the same timestamp regardless of system load.

### Identity Semantics

`event.identity` reflects the user who caused the event — the person who created or updated the record. NOT the Compute's identity (which may be different if a service-identity agent processes the event). The Compute's own identity is available via `Compute.Scopes` in the CEL context.

---

## Examples

### Simple where clause (uses record promotion)
```
Trigger on event "message.created" where `message.role == "user"`
```

### Advanced where clause (uses envelope metadata)
```
Trigger on event "finding.created" where `event.identity.role != "scanner"`
```
(Don't trigger when the scanner agent creates findings — only human-created ones.)

### Event condition with timestamp
```
When `ticket.created && event.timestamp > "2026-01-01"`:
  Send ticket to "alerts"
```

---

## Runtime Changes

1. `run_event_handlers()` builds the envelope from the record + request context
2. CEL context includes both promoted record fields (via `singular_lookup`) and `event.*` namespace
3. `create_record()` and `update_record()` pass the current user identity to event handlers
4. Event bus publishes the full envelope (not just the record) for WebSocket forwarding

## IR Changes

None. The envelope is a runtime concern. The IR already carries `source_content`, `trigger`, and `condition_expr` which is sufficient. The envelope structure is part of the runtime implementer's guide.
