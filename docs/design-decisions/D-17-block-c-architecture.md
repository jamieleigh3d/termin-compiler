# D-17: Block C Architectural Inputs

**Status:** DECIDED 2026-04-10
**Decided by:** JL + Claude
**Affects:** Runtime (boundary enforcement, storage, channels)

---

## Three Decisions

### 1. Multiple boundaries in one process — YES (Option A)

The reference runtime hosts all boundaries in a single FastAPI process with a single SQLite database. Boundary isolation is logical, not physical. The runtime enforces channel-only crossing in code.

Physical isolation (separate processes per boundary) is a deployment concern. An AWS-native distributed runtime can implement that. The reference runtime proves the enforcement model works.

### 2. Inter-boundary channel materialization — In-process via async queue

Same-process channels use in-process function calls for enforcement (scope check, redaction, taint, audit) but deliver data through an async queue to avoid synchronous chain reactions. If a channel send triggers an event which triggers another channel send, the queue prevents deadlocks — same pattern as the WebSocket push fix.

Enforcement is synchronous: reject immediately if scope check fails.
Delivery is async: data goes through the event bus queue.

### 3. Enforce "only through Channels" — YES

When a Compute accesses content in a different boundary, the runtime requires an explicit channel (via `Accesses X from "boundary/channel"`). Direct access to cross-boundary content is rejected, even though the data is in the same database.

For v0.6: `Accesses X` (no `from` clause) is checked against the Compute's containing boundary. If X is in a different boundary, the runtime returns 403.

---

## Implementation approach for v0.6

1. Build a boundary containment map at startup: for each Content type, which Boundary contains it
2. On every content operation from a Compute, check: is the target Content in the same boundary as the Compute?
3. Same boundary → allow (enforced by Accesses + Content access grants)
4. Different boundary → require `from` clause with explicit channel → v0.6 rejects with "cross-boundary access requires a channel" error (cross-boundary channels implemented in v1.0)
5. Content NOT in any boundary → unrestricted (backward compat with apps that don't use boundaries)
