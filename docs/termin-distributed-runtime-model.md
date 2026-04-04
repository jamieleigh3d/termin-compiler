# Termin Distributed Runtime Model

**Version:** 0.2.0-draft
**Status:** Formative — design decisions resolved for registry, auth, client compute, offline, deployment

---

## Core Principle

A distributed Termin application is just Boundaries connected by Channels. Whether two Boundaries are in the same process, across a network, or in different continents doesn't change the application model. It only changes the Channel's protocol adapter. The application is topology-agnostic. The deployment is topology-specific.

A Termin application doesn't know it's a web app, or a distributed backend, or a Lambda pipeline. It's a graph of Boundaries connected by Channels. The deployment decides where each Boundary runs. The runtime handles everything between.

---

## The Three-Layer Channel Model

Channels exist at three layers. The application author only sees the top layer. The runtime manages the middle. The protocol adapter manages the bottom.

### Layer 1: Logical Channels

What the DSL and IR express. A logical Channel is a typed, identity-scoped, schema-validated message stream between two Boundaries. It carries Content conforming to a declared schema. It has a direction and delivery intent. It's what the application author thinks about.

```
Boundary called "Alice":
  Exposes property "orderCount" : whole number = [orders.length]

Boundary called "Bob":
  Display text [Alice.orderCount]
  This subscribes to Alice.orderCount changes
```

Bob has a logical Channel to Alice carrying order count updates. Bob doesn't know or care how that Channel is implemented.

A single logical Channel carries one Content schema in one direction. A bidirectional Channel is two logical Channels (one each way) sharing configuration.

### Layer 2: Multiplexing

What the runtime manages. Multiple logical Channels between the same pair of Boundaries are multiplexed over a shared physical connection. Bob might have fifty subscriptions to Alice — order count, product updates, alert streams, error feeds. These are fifty logical Channels carried over one multiplexed connection.

The multiplexing layer:

- Groups logical Channels by endpoint pair (source Boundary + destination Boundary)
- Maintains one physical connection per endpoint pair (or a small pool)
- Frames each message with a channel identifier so the receiving runtime demultiplexes to the correct logical Channel
- Handles reconnection transparently — if the physical connection drops, the multiplexer reconnects and re-subscribes all logical Channels

A multiplexed message frame:

```json
{
  "channel_id": "alice.orderCount.subscribe",
  "content_schema": "whole_number",
  "payload": 42,
  "identity": { "role": "viewer", "scopes": ["read orders"] },
  "timestamp": "2026-04-03T..."
}
```

The application author never sees this. The logical Channel abstraction hides it completely.

### Layer 3: Physical Connections

What the protocol adapter manages. TCP sockets, WebSocket connections, HTTP request/response cycles, Lambda invocations, in-process function calls. The protocol adapter implements the transport.

| Deployment Topology | Physical Connection |
|---|---|
| Same process | Function call (zero overhead) |
| Client → Server | WebSocket (persistent, multiplexed) |
| Server → Server | gRPC, HTTP/2, or WebSocket |
| Server → Lambda | Lambda invoke API |
| Server → Queue | SQS/SNS publish |

The protocol adapter is selected based on the Channel's delivery intent and the deployment configuration. The logical Channel says `Delivery: realtime`. The deployment says "Bob is a browser, Alice is a server." The runtime selects WebSocket.

---

## Runtime Resolution: How Boundaries Find Each Other

When a Boundary needs to reach another Boundary, it consults the **runtime registry**.

### The Registry

The registry maps Boundary names to connection information. It is a simple lookup service, not a complex distributed system.

For a standard web application, the **server IS the registry**. The server runtime knows where all server-side Boundaries live (they're in its process or in known service endpoints). The client runtime discovers the registry by connecting to the server that served the application.

### Registry API

The server exposes a default REST endpoint:

```
GET /runtime/registry
```

Response:

```json
{
  "runtime_version": "0.2.0",
  "application": "Warehouse Inventory Manager",
  "boundaries": {
    "order_processing": {
      "location": "local",
      "channels": {
        "realtime": "ws://localhost:8080/runtime/ws",
        "reliable": "http://localhost:8080/runtime/api"
      }
    },
    "order_reporting": {
      "location": "local",
      "channels": {
        "realtime": "ws://localhost:8080/runtime/ws",
        "reliable": "http://localhost:8080/runtime/api"
      }
    },
    "presentation": {
      "location": "client",
      "channels": {
        "realtime": "ws://localhost:8080/runtime/ws",
        "reliable": "http://localhost:8080/runtime/api"
      }
    }
  },
  "protocols": {
    "realtime": "websocket",
    "reliable": "rest"
  }
}
```

When multiple Boundaries are in the same server process, they share the same connection endpoints — the multiplexing layer sorts out which logical Channel goes where.

### Client Discovery

The client runtime discovers the registry through the URL that served the application. The browser knows its own origin URL. The client runtime:

1. Takes the application's origin URL (e.g., `https://warehouse.example.com`)
2. Appends `/runtime/registry`
3. Fetches the registry
4. Now knows how to reach every Boundary

```javascript
// Client runtime bootstrap
const origin = window.location.origin;
const registry = await fetch(`${origin}/runtime/registry`).then(r => r.json());
// registry.boundaries["order_processing"].channels.realtime → "ws://warehouse.example.com/runtime/ws"
```

### Resolution Cases

**Same process (development, simple apps):**

All Boundaries are in one Python process. The registry returns `"location": "local"` for everything. The runtime resolves cross-boundary calls to direct function invocations. No network, no serialization, no WebSocket. The multiplexing layer degrades to a simple event dispatcher.

```json
{
  "order_processing": { "location": "local" },
  "order_reporting": { "location": "local" }
}
```

**Client-server (standard web app):**

The browser is one Boundary ("presentation"). The server hosts the rest. The client connects to the server's WebSocket endpoint for realtime Channels and the REST endpoint for reliable Channels.

```json
{
  "order_processing": {
    "location": "server",
    "channels": { "realtime": "ws://server:8080/runtime/ws" }
  },
  "presentation": {
    "location": "client"
  }
}
```

**Multi-service (distributed backend):**

Several server-side Boundaries run in different processes or containers. Each registers with a shared registry service (or a config file).

```json
{
  "order_processing": {
    "location": "remote",
    "channels": { "reliable": "https://orders-service:8081/runtime/api" }
  },
  "order_reporting": {
    "location": "remote",
    "channels": { "reliable": "https://reports-service:8082/runtime/api" }
  },
  "analytics_compute": {
    "location": "lambda",
    "channels": { "reliable": "lambda://analytics-function" }
  }
}
```

**Different strata (mixed persistent + ephemeral):**

Some Boundaries are always-on servers. Some are ephemeral Lambdas. Some are transient browser tabs. The registry handles heterogeneous runtimes by advertising connection information appropriate to each Boundary's nature.

Persistent runtimes register at startup and maintain their registration. Ephemeral runtimes (Lambda) don't register — instead, the registry holds a static invocation endpoint. Transient runtimes (browsers) don't register at all — they're purely consumers that discover the registry on connection.

---

## Implicit Channel Opening

Channels are opened implicitly by subscription and Property access. The DSL author never explicitly opens a Channel.

### Property Access

When Bob's renderer encounters `[Alice.myData]`:

1. Runtime checks: is Alice local? If yes → direct call, done.
2. Runtime checks registry: where is Alice?
3. Runtime checks existing connections: is there a connection to Alice's host? If yes → send Property read request on existing connection. If no → open a connection, then send.
4. Response arrives → renderer uses the value.

This is a one-shot request-response. The physical connection may persist (WebSocket stays open for future requests) or not (HTTP request-response completes), depending on the protocol adapter.

### Subscription

When Bob declares `subscribes to Alice.myData changes`:

1. Runtime resolves Alice (same steps as above).
2. Runtime ensures a persistent connection to Alice (WebSocket for realtime).
3. Runtime sends a subscribe request over the connection: `{ "action": "subscribe", "boundary": "Alice", "property": "myData" }`.
4. Alice's runtime registers the subscription.
5. When Alice.myData changes, Alice's runtime pushes through the connection: `{ "channel_id": "alice.myData.subscribe", "payload": newValue }`.
6. Bob's multiplexer demultiplexes to the correct logical Channel.
7. Bob's local cache updates.
8. Bob's renderer re-evaluates reactive expressions.

The channel was never explicitly declared in the DSL. It was implied by the cross-boundary subscription. The runtime created the logical Channel, established the physical connection, and wired the event flow — all from a single declarative line.

### Explicit Channels

Explicit Channel declarations (the `Channel called "order webhook":` syntax) are for Channels that cross the **platform Boundary** — connections to external systems, incoming webhooks, outbound API calls. These are declared because the application author needs to specify the Content schema, Identity requirements, and delivery intent for something outside the Termin graph.

Internal Channels (between Boundaries within the same application) are mostly implicit — created by subscription, Property access, and Compute invocation. The runtime handles them.

---

## Client and Server Runtime Responsibilities

### Server Runtime (Authoritative)

The server runtime is the source of truth. It runs the full set of subsystems:

| Subsystem | Server Role |
|---|---|
| Identity Binding | Authenticates principals, resolves roles |
| Content Storage | Stores and retrieves all Content |
| Compute Registry | Executes all Compute functions |
| State Engine | Enforces all State machines (single writer) |
| Channel Enforcer | Validates all cross-boundary data flow |
| Boundary Isolator | Enforces scope containment |
| Event Bus | Emits and routes all Events |
| Error Router (TerminAtor) | Handles all errors and escalation |
| Expression Evaluator | Evaluates server-side expressions |
| Registry | Serves the runtime registry API |
| Multiplexer | Manages physical connections to clients and other services |

### Client Runtime (Projection)

The client runtime is a lightweight projection. It renders the Presentation layer and maintains a reactive cache of data received from the server.

| Subsystem | Client Role |
|---|---|
| Expression Evaluator | Evaluates Presentation expressions (JEXL) |
| Presentation Renderer | Walks the component tree, renders UI |
| Local Cache | In-memory store of Content received from server |
| Channel Client | Sends requests, receives pushes through multiplexed connection |
| Client Compute Registry | Executes client-safe Compute (pure transforms on cached data) |
| Reflection Accessor | Read-only, populated from server pushes |
| Identity Context | Received from server during handshake authentication |

The client does NOT run:

- Content Storage (no local database — the cache is ephemeral)
- State Engine (the client requests transitions, the server decides)
- Mutating Compute (the client requests execution, the server executes)
- TerminAtor (errors route on the server; the client receives error Events for display)
- Boundary Isolator (the server enforces; the client trusts what it receives)

### Data Flow Patterns

**Client reads data:**

```
Client renderer needs products table
  → Client sends read request through Channel
  → Server receives, checks Identity, queries Storage
  → Server sends Content through Channel
  → Client cache stores it
  → Client renderer displays it
```

**Client requests mutation:**

```
User clicks "Activate" on a product
  → Client sends transition request through Channel: { action: "transition", target: "products", id: 42, state: "active" }
  → Server receives, checks Identity, checks State Engine
  → Server executes transition
  → Server emits change Event
  → Server pushes change through subscription Channel
  → Client receives, updates cache
  → Client re-renders affected components
  → Action button visibility re-evaluates
```

**Server pushes update (another user made a change):**

```
User B updates a product on a different browser tab
  → Server processes the update
  → Server emits change Event
  → Server pushes to ALL subscribed clients through their Channels
  → Client A receives, updates cache
  → Client A re-renders (table row updates, aggregations recalculate)
```

---

## Multiplexing Protocol

The multiplexing layer wraps logical Channel messages in a simple frame for transport over a shared physical connection.

### WebSocket Frame Format

```json
{
  "v": 1,
  "ch": "string",
  "op": "string",
  "ref": "string|null",
  "payload": { ... }
}
```

| Field | Description |
|---|---|
| `v` | Protocol version (for forward compatibility) |
| `ch` | Logical channel identifier (e.g., `"boundary.alice.property.myData"`) |
| `op` | Operation: `subscribe`, `unsubscribe`, `push`, `request`, `response`, `error` |
| `ref` | Request reference ID for correlating request/response pairs. Null for pushes. |
| `payload` | The Content, typed and schema-validated |

### Example: Subscribe Flow

Client sends:
```json
{ "v": 1, "ch": "boundary.alice.property.orderCount", "op": "subscribe", "ref": "sub-1", "payload": {} }
```

Server acknowledges:
```json
{ "v": 1, "ch": "boundary.alice.property.orderCount", "op": "response", "ref": "sub-1", "payload": { "current": 42 } }
```

Server pushes update (later):
```json
{ "v": 1, "ch": "boundary.alice.property.orderCount", "op": "push", "ref": null, "payload": { "current": 43 } }
```

### Example: Property Read (One-Shot)

Client sends:
```json
{ "v": 1, "ch": "boundary.alice.property.myData", "op": "request", "ref": "req-7", "payload": {} }
```

Server responds:
```json
{ "v": 1, "ch": "boundary.alice.property.myData", "op": "response", "ref": "req-7", "payload": { "value": "Hello from Alice" } }
```

### Example: Mutation Request

Client sends:
```json
{ "v": 1, "ch": "content.products.transition", "op": "request", "ref": "req-12", "payload": { "id": 42, "target_state": "active" } }
```

Server responds (success):
```json
{ "v": 1, "ch": "content.products.transition", "op": "response", "ref": "req-12", "payload": { "id": 42, "new_state": "active" } }
```

Server responds (error):
```json
{ "v": 1, "ch": "content.products.transition", "op": "error", "ref": "req-12", "payload": { "kind": "state", "message": "Cannot transition from draft to active without write inventory scope" } }
```

### Reconnection

When a physical connection drops:

1. Client multiplexer detects disconnection
2. Client attempts reconnection with exponential backoff
3. On reconnection, client re-sends all active subscribe operations
4. Server re-establishes subscriptions and pushes current state for each
5. Client cache updates, renderer re-evaluates

This is transparent to the application. Logical Channels survive physical disconnections.

---

## Consistency Model

**Single writer per Content type.** The Boundary that `Contains` a Content type is its authoritative writer. All mutations route to the owning Boundary. Other Boundaries read through Properties and Channels.

**Eventual consistency for reads.** Client caches are eventually consistent with the server. After a mutation, the server pushes the update, and the client cache converges. The gap between mutation and push is typically milliseconds (WebSocket latency). For the vast majority of business applications, this is indistinguishable from strong consistency.

**Strong consistency for mutations.** When the client requests a mutation (create, update, delete, transition), the server processes it synchronously and responds with the result. The client knows whether the mutation succeeded before updating its cache. If the server rejects the mutation (State violation, scope check failure, validation error), the client receives an error and does not update.

**Optimistic UI is opt-in.** By default, the client waits for the server response before updating. If an application wants optimistic updates (update the UI immediately, roll back on error), the Presentation layer can express this through an action button prop: `"optimistic": true`. The renderer updates the local cache immediately on user action, then reconciles when the server response arrives.

---

## Runtime Lifecycle in Distributed Context

### Server Startup

Standard Phase 0-5 from the runtime interface spec, plus:

- **Phase 5a:** Start the registry API (`/runtime/registry`)
- **Phase 5b:** Start the WebSocket multiplexer (`/runtime/ws`)
- **Phase 5c:** Start the REST API (`/runtime/api`)
- **Phase 5d:** If multi-service, register with shared registry

### Client Startup

1. Load the application shell (HTML served by server)
2. Fetch the registry: `GET /runtime/registry`
3. Initialize the Expression Evaluator (load JEXL)
4. Initialize the Channel client (open WebSocket with auth token in handshake)
5. Receive resolved IdentityContext in the handshake response
6. Load client-safe Compute functions into local Compute Registry
7. Load the component tree for the user's role
8. Begin rendering — implicit Channels open as needed

### Graceful Degradation

If the WebSocket connection fails and cannot reconnect:

- Reactive subscriptions pause (data stops updating)
- The renderer shows a connection status indicator
- Property reads fall back to REST (reliable but not realtime)
- Mutation requests fall back to REST (still work, just without push updates)
- When the WebSocket reconnects, subscriptions resume and the cache catches up

The application doesn't crash. It degrades from realtime to request-response and recovers automatically.

---

## Summary

| Concept | What It Means |
|---|---|
| Logical Channel | Typed, identity-scoped message stream between Boundaries |
| Physical Connection | TCP socket, WebSocket, HTTP, Lambda invoke — the transport |
| Multiplexing | Many logical Channels over one physical connection |
| Registry | Single shared registry — maps Boundary names to connection endpoints |
| Implicit Channel | Created automatically by subscription or Property access |
| Explicit Channel | Declared in DSL for external system connections |
| Server Runtime | Authoritative — owns storage, state, compute, identity |
| Client Runtime | Projection — owns presentation, client-safe compute, local cache |
| Single Writer | Each Content type has one owning Boundary for mutations |
| Eventual Consistency | Client caches converge after server pushes |
| Strong Mutations | Mutation results are synchronous request/response |
| Handshake Auth | WebSocket connections authenticate during upgrade, not after |
| No Offline | Graceful degradation on disconnect; no offline mutations |
| Deployment Manifest | JSON file mapping Boundaries to infrastructure targets |

---

## Design Decisions

### 1. Single Shared Registry (No Federation)

A Termin application is deployed as a unit. Even when Boundaries run in separate processes or containers, they are parts of one application with one compilation. The registry is a **single source of truth** — either a config file generated at deploy time or a simple endpoint on the primary server.

Federation (where each service maintains a local registry that syncs with a central one) is unnecessary complexity for the deployment model. A Termin app is not a microservices mesh with independent release cycles — it's a compiled graph with known topology. If two teams need independent deployment, they are building two Termin applications connected by explicit Channels at the platform boundary, not one federated application.

Future: if genuine multi-team federation becomes necessary, it can be added without changing the application model — the registry API contract stays the same, only the backing implementation changes.

### 2. Handshake Authentication

WebSocket connections authenticate during the **upgrade handshake**, not after connection. The auth token is sent in the connection URL query parameter or upgrade headers.

```
GET /runtime/ws?token=<jwt_or_session_token> HTTP/1.1
Upgrade: websocket
```

Rationale:

- **Resource protection.** Unauthenticated connections are rejected before they consume multiplexer resources. A bad actor cannot open thousands of connections that each need an auth exchange.
- **Identity binding.** The connection itself is identity-scoped from the first frame. The multiplexer never needs to handle "pre-auth" messages or buffer frames while waiting for authentication.
- **Alignment with Identity subsystem.** "Bind once, enforce everywhere" — the connection carries the resolved IdentityContext for its entire lifetime. Re-authentication (token refresh) can happen as a control frame without re-establishing the connection.
- **Development mode.** Stub auth still works — the dev server accepts connections without tokens and assigns the default role from the cookie.

### 3. Client-Side Compute (Narrowly Scoped)

The client runtime supports a **read-only subset** of the Compute Registry for performance-critical operations on cached data.

A Compute function is **client-safe** when:

- Its shape is Transform (one input, one output, no side effects)
- It operates only on Content already present in the client cache
- It performs no mutations (no create, update, delete, transition)

The compiler infers client-safety statically at IR generation time. The IR marks client-safe Computes with a `"client_safe": true` flag. The client runtime loads these into its local Compute Registry. The server always retains the authoritative copy.

```json
{
  "name": { "display": "FormatCurrency", "snake": "format_currency" },
  "shape": "TRANSFORM",
  "client_safe": true,
  "body_lines": ["result = '$' + amount.toFixed(2)"]
}
```

Client-safe Compute enables:

- Client-side sorting and filtering without server round-trips
- Display formatting (currency, dates, percentages)
- Derived values for Presentation expressions

Client-safe Compute does NOT enable:

- Mutations of any kind
- Cross-boundary data access
- Compute that requires data not in the client cache

### 4. No Offline Support

Offline mutation support is **out of scope**. The single-writer consistency model is a core guarantee that offline mutations would compromise. Conflict resolution strategies (CRDTs, operational transforms, last-writer-wins) add fundamental complexity and introduce classes of bugs that the "secure by construction" thesis is designed to eliminate.

The runtime handles connectivity loss through **graceful degradation** (described in the Graceful Degradation section above): reactive subscriptions pause, property reads fall back to REST, and the UI shows a connection status indicator. When connectivity returns, the client reconnects and catches up. This is sufficient for the vast majority of business applications.

If a specific domain requires offline-capable mutations, it should be modeled as an explicit pattern: a local "draft" Content type that syncs to the server via an explicit Channel with declared conflict strategy. This keeps the complexity visible in the DSL rather than hidden in the runtime.

### 5. Deployment Manifest

The deployment manifest maps Boundaries to infrastructure targets. It is a JSON file read at compile time or deploy time.

**Format:**

```json
{
  "version": "0.1.0",
  "targets": {
    "order_processing": {
      "location": "local"
    },
    "order_reporting": {
      "location": "remote",
      "endpoint": "https://reports-service:8082"
    },
    "analytics_compute": {
      "location": "lambda",
      "function": "analytics-fn",
      "region": "us-east-1"
    },
    "presentation": {
      "location": "client"
    }
  },
  "defaults": {
    "location": "local",
    "realtime_protocol": "websocket",
    "reliable_protocol": "rest"
  }
}
```

**Location values:**

| Location | Meaning | Protocol Selection |
|---|---|---|
| `local` | Same process as the primary server | Direct function call (zero overhead) |
| `remote` | Separate process/container at a known endpoint | gRPC, HTTP/2, or WebSocket |
| `lambda` | Ephemeral function invocation | Lambda invoke API |
| `client` | Browser/native client | WebSocket (realtime) + REST (reliable) |

**Usage:**

```bash
# Development (all local, no manifest needed)
termin compile app.termin -o app.py --backend runtime

# Production (with deployment manifest)
termin compile app.termin -o app.py --backend runtime --deploy deploy.json
```

When no manifest is provided, all Boundaries default to `local` and `presentation` defaults to `client`. The registry response is generated from the manifest at startup.

---

## Cross-Boundary Identity Propagation

When a Boundary contains another Boundary and they are deployed to different processes, the containment relationship has security implications. Boundary A's Identity scopes gate Boundary B's access — this is the Boundary Isolator's job.

In the distributed case, Identity context must propagate through the Channel when crossing a containment boundary. The multiplexing layer includes the resolved IdentityContext in each frame (the `identity` field in the frame format). The receiving Boundary's runtime validates that the incoming Identity satisfies the containment scope before processing the request.

```
Client requests Alice.updateOrder(42)
  → Client Channel includes IdentityContext { role: "clerk", scopes: ["write orders"] }
  → Alice's runtime checks: does this Identity satisfy Alice's containment scope?
  → Alice processes the request
  → Alice needs to call Bob.generateReport() (Bob is contained within Alice)
  → Alice's Channel to Bob includes the ORIGINAL client IdentityContext
  → Bob's runtime checks: does this Identity satisfy Bob's containment scope?
  → Bob processes the request
```

Identity context flows **downward** through containment. A child Boundary never sees broader scopes than its parent allows. This is the distributed equivalent of the in-process Boundary Isolator — the Channel Enforcer validates the same invariants, just across a network boundary.

---

## Open Questions

1. **Connection pooling.** For `remote` Boundaries with high throughput, should the multiplexing layer maintain a connection pool (multiple physical connections per endpoint pair) rather than a single connection? This trades multiplexing simplicity for throughput. Likely needed for production but not for v1.

2. **Schema evolution.** When the application is recompiled with Content schema changes, how do existing client connections handle the schema mismatch? The server should version its registry and push a "schema changed, reload required" event through all active connections. Clients that receive this event reload the application shell.
