# D-03: Implicit Channels in IR

**Status:** DECIDED 2026-04-10
**Decided by:** JL + Claude
**Affects:** IR, runtime (enforcement), future: compiler (boundary paths)

---

## Decision

**Option B: Runtime infers from Accesses.** Implicit channels are NOT materialized in the IR.

### Same-boundary data flow (v0.6)

`Accesses X` (no `from` clause) = local content access. Enforced by:
1. Scope check → Content access grants (who can view/create/update/delete)
2. Confidentiality redaction → existing system
3. Type validation → Content schema from IR
4. Taint propagation → ComputeSpec confidentiality fields
5. Audit logging → Content audit level (D-18)

No ChannelSpec needed. All five enforcement checks use existing primitives.

### Cross-boundary data flow (v1.0)

`Accesses X from "boundary/channel"` = cross-boundary access through an explicit channel.

The target boundary declares what it exposes:
- Channels: `Direction: outbound`, with scope requirements
- Properties: read-only computed projections

The consuming Compute references via `from` clause. The runtime:
1. Resolves the boundary path to the declared channel
2. Checks channel scope requirements against the Compute's identity
3. Applies full channel crossing enforcement (redaction, taint, audit)
4. Data is read-only unless target exposes an inbound channel

### Summary

| Data flow | Mechanism | Enforcement |
|-----------|-----------|-------------|
| Same boundary | `Accesses X` | Content access grants + confidentiality |
| Cross boundary, read | `Accesses X from "boundary/channel"` | Channel scope + redaction + taint |
| Cross boundary, write | Inbound channel on target | Channel scope + validation |
| Cross boundary, property | `Accesses X from "boundary/property"` | Read-only computed value |

### Explicit channels remain for:
- External service integration (webhooks, HTTP, WebSocket)
- Cross-boundary transport (between modules/apps)
- Non-default delivery semantics (reliable, batch)
- Scope requirements that differ from Content access grants

For v0.6: implement same-boundary enforcement only. `from` clauses accepted by parser but rejected by runtime until v1.0.
