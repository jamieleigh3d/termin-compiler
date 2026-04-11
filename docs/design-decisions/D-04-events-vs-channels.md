# D-04: Events vs Channels as Distinct Primitives

**Status:** DECIDED 2026-04-10
**Decided by:** JL + Claude
**Affects:** IR (no change — already separate), runtime (enforcement), compiler (boundary crossing validation)

---

## Decision

Events and Channels are distinct primitives that compose. Neither subsumes the other.

### Channels = pipes

A Channel moves data between points. It has direction, delivery semantics, scope requirements, and a content type. It doesn't decide WHEN to send — something else triggers it.

### Events = reactions

An Event reacts to state changes. It evaluates a condition and dispatches an action. It doesn't know HOW to transport data — it delegates to channels or content operations.

### Composition

Events trigger channel sends. Channels create content which fires events. The two primitives reference each other by name but don't merge.

```
Event fires               Channel transports          Content created
  (WHEN)          →          (HOW)            →         (WHAT)
"status == active"    "employee-changes"           pay run record
```

### Boundary enforcement rule

Data crossing a boundary MUST go through a declared Channel. Events within a boundary fire freely. An event that sends data across a boundary must reference a channel that crosses that boundary.

The compiler validates: if an event's `send_channel` references a channel whose direction crosses a boundary, the channel's scope requirements apply at runtime.

### IR representation

Separate top-level arrays — no change from current:
- `events: [EventSpec, ...]` — conditions + actions
- `channels: [ChannelSpec, ...]` — transport + enforcement

They reference each other: `EventActionSpec.send_channel` names a `ChannelSpec`. The runtime resolves the reference and applies channel enforcement when the event fires.

### Example: cross-boundary data flow

Employee module boundary exposes `Channel called "employee-changes"` (outbound, reliable).
Payroll module boundary declares `Channel called "payroll-intake"` (inbound).
Event: `When employee.status == "active": Send employee to "employee-changes"`.
Compute in payroll: `Accesses employees from "employee-management/employee-changes"`.

The Event decides when. The Channel handles transport + scope + redaction + audit. The Compute receives the data scoped to what the channel permits.
