# D-18: Audit Declaration on ContentSchema

**Status:** DECIDED 2026-04-10
**Decided by:** JL + Claude
**Affects:** Grammar, parser, AST, IR, lowering, runtime (logging)

---

## Decision

### Three levels named by intent

| Level | Default? | What gets logged | Who it's for |
|-------|----------|-----------------|-------------|
| `actions` | YES | Event type, record ID, field names changed, identity, timestamp. Never field values. | Compliance officers, security investigators |
| `debug` | No | Full field values + everything from actions | Builders during development/investigation |
| `none` | No | Nothing | Ephemeral/scratch content |

### Pit of success

The default is `actions` — the safe behavior requires zero configuration. A builder who never thinks about audit logging gets the safe behavior automatically. Field values never appear in production logs unless the builder explicitly writes `Audit level: debug`.

The name `debug` (not `content` or `full`) signals intent: "I am actively debugging, this is not for production with sensitive data."

### DSL

Optional line on Content blocks. Omitting it defaults to `actions`.

```
Content called "cases":
  Each case has a title which is text
  Each case has a description which is text
  Audit level: actions
  ...
```

### IR

```json
{
  "name": {"display": "cases", "snake": "cases", "pascal": "Cases"},
  "singular": "case",
  "audit": "actions",
  "fields": [...]
}
```

### Runtime behavior

**`actions` level logging:**
```json
{
  "event": "cases.created",
  "record_id": 42,
  "fields_changed": ["title", "description", "status"],
  "identity": {"role": "manager", "name": "JL"},
  "timestamp": "2026-04-10T14:30:00Z"
}
```

**`debug` level logging:**
```json
{
  "event": "cases.updated",
  "record_id": 42,
  "fields_changed": ["status"],
  "field_values": {"status": "investigating"},
  "previous_values": {"status": "opened"},
  "identity": {"role": "manager", "name": "JL"},
  "timestamp": "2026-04-10T14:35:00Z"
}
```

**`none` level:** No log entry emitted.

---

## Design rationale

Five audit consumers identified:
1. **Compliance officer** — needs access patterns, not content. `actions` level.
2. **Security investigator** — needs timeline + what changed. `actions` gives field names without values.
3. **Builder / debugger** — needs everything. `debug` level, development only.
4. **Data subject (GDPR)** — needs "what records exist about me." Separate feature (v1.0 subject access).
5. **Platform operator** — needs metrics. Separate concern (reflection API).

## What's NOT in this decision

- **Agent trace redaction** — when an agent reads a field from `audit: "actions"` content, should the agent's trace redact that value? Deferred to v1.0. For now, agent traces are independent of content audit level.
- **Subject access / GDPR export** — per-identity "what data exists about me" queries. Deferred to v1.0.
- **Audit log access scoping** — who can read the audit log. JL proposed a `logs` verb alongside read/write/update/delete. Deferred to v1.0.
