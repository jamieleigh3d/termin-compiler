# Termin Runtime Interface Specification

**Version:** 0.2.0-draft
**Status:** Formative — major revision incorporating feedback

---

## Overview

The Termin runtime is the execution environment that enforces security invariants and provides the operational substrate for compiled Termin applications. This document defines the **contracts** that any runtime implementation must satisfy, regardless of implementation language, deployment topology, or infrastructure target.

A Termin runtime could be as simple as a single Python process with a web server, or as distributed as a multi-service cloud deployment. The contracts are the same. What changes is the implementation behind each interface.

The compiler produces an **Intermediate Representation (IR)** — a validated, language-agnostic data structure describing the application's primitives, channels, boundaries, and expressions. The runtime receives this IR and executes it.

---

## Architecture

The runtime is composed of **eight subsystems** (one per primitive) and **two cross-cutting concerns** (Expression Evaluation and Error Handling).

### Primitive Subsystems

| Subsystem | Primitive | Responsibility |
|---|---|---|
| Identity Binding | Identity | Authenticate principals, resolve roles and scopes |
| Content Storage | Content | Store and retrieve typed data via pluggable adapters |
| Compute Registry | Compute | Register, sandbox, and invoke transformation functions |
| State Engine | State | Enforce finite state machines on any named primitive |
| Channel Enforcer | Channels | Authenticate, validate, and route data through typed edges |
| Boundary Isolator | Boundaries | Enforce scope containment, property exposure, lifecycle |
| Event Bus | Events | Emit, route, and subscribe to typed signals |
| Presentation Renderer | Presentation | Transform IR declarations into user-facing interfaces |

### Cross-Cutting Concerns

| Concern | Responsibility |
|---|---|
| Expression Evaluator | Execute CEL expressions in controlled context |
| Error Router (the **TerminAtor**) | Route errors through boundary hierarchy, catch-all at global scope |

The Expression Evaluator is used by every subsystem that evaluates CEL. The Error Router — affectionately named the **TerminAtor** — manages error Channels on every primitive and handles error escalation through the Boundary hierarchy.

---

## Identity Binding Interface

The Identity subsystem authenticates principals and resolves their scopes and roles. Identity is pluggable — the runtime defines the interface, deployments provide the implementation.

### Contract

**`authenticate(request) → IdentityContext | Anonymous`**

Takes an incoming request and returns an IdentityContext containing the authenticated principal, their resolved role, and their effective scopes. If authentication fails or is not provided, returns the Anonymous context.

**`resolveRole(principal, applicationRoles) → Role`**

Maps an authenticated principal to one of the application's declared roles.

**`hasScope(identityContext, scope) → boolean`**

Checks whether the given identity context includes the specified scope.

### IdentityContext Structure

```
IdentityContext:
  principal: object        (opaque — shape is deployment-specific)
  role: string             (the matched application role name)
  scopes: list of string   (effective scopes for this role)
  isAnonymous: boolean
```

### Built-in Bindings

The runtime must ship with a `stub` binding for development and testing. The stub binding accepts user identity from a configuration file or request header without real authentication.

---

## Content Storage Adapter Interface

The Content subsystem stores and retrieves typed data. Storage is pluggable.

### Selecting a Storage Adapter in the DSL

Storage intent is declared at the application level or per-Content, using intent-based configuration rather than implementation names:

```
Application: Warehouse Inventory Manager
  Description: Track products and stock levels
  Storage: persistent

(Or per-Content override:)
Content called "session cache":
  Storage: ephemeral
  Each session has a token which is text, required
```

**Storage intents:**

| Intent | Meaning | Typical Implementation |
|---|---|---|
| `ephemeral` | In-memory, lost on restart. Fast. For caches, sessions, previews. | In-memory store |
| `persistent` | Survives restarts. For business data. | SQLite, Postgres, DynamoDB |
| `durable` | Persistent + backup/replication guarantees. For critical records. | Replicated database, S3-backed |
| `auto` | Runtime chooses based on deployment context. Default if unspecified. | Deployment-specific |

The runtime maps intents to concrete adapters based on deployment configuration. The `.termin` file never names a specific database.

### Contract

**`createSchema(contentDeclaration) → void`**

Initializes storage structures for a Content declaration.

**`create(contentName, record, identityContext) → record | error`**

Creates a record. Validates schema conformance, enforces constraints, verifies identity scope.

**`read(contentName, query, identityContext) → list of records | error`**

Reads records. Query is a structured filter object, never raw backend query language.

**`update(contentName, id, changes, identityContext) → record | error`**

Updates a record. Validates changes, enforces constraints, verifies scope.

**`delete(contentName, id, identityContext) → void | error`**

Deletes a record. Verifies scope.

**`validate(contentName, record) → validationResult`**

Validates without storing. Used by Channel enforcement and Compute sandboxing.

**`migrate(migrationScript) → void`**

Applies schema changes emitted by the compiler when Content declarations evolve between versions.

### Query Structure

```
Query:
  filters: list of Filter
  sort: list of SortField
  limit: integer (optional)
  offset: integer (optional)

Filter:
  field: string
  operator: eq | neq | gt | gte | lt | lte | in | contains
  value: any
```

Content access is always parameterized. No storage adapter ever receives a constructed query string.

---

## Expression Evaluator

The Expression Evaluator executes CEL expressions in a controlled context. It is used by every subsystem that evaluates expressions.

### Contract

**`evaluate(expression, context) → value | error`**

Evaluates a CEL expression against a provided context object.

**`compile(expression) → compiledExpression | error`**

Parses and validates at compile time. The compiler must validate all expressions before runtime, catching parse errors, undeclared references, and type mismatches. This prevents expression-related startup failures.

**`registerFunction(name, function) → void`**

Registers a callable function for CEL context.

**`registerTransform(name, function) → void`**

Registers a transform for the CEL pipe operator.

### Expression Context

```
ExpressionContext:
  identity: IdentityContext
  content: map of ContentAccessors
  compute: map of ComputeFunctions
  locals: map of values
  properties: map of PropertyValues
  state: map of StateAccessors
```

### Namespace Resolution

Named identifiers are unique **within their primitive type**. A Content called "orders", a Compute called "orders", and a Channel called "orders" do not collide — they live in separate namespaces.

The evaluator resolves names by type context:

- In a Content access expression, "orders" resolves to Content
- In a function call expression, "orders" resolves to Compute
- In a Channel reference, "orders" resolves to Channel
- In an unambiguous context (e.g., a standalone reference), resolution follows the priority chain below

**Scope resolution priority** (for ambiguous references):

1. **Locals** — named inputs within the current Compute body
2. **Content** — declared Content schemas
3. **Identity** — CurrentUser, role, scopes
4. **Compute** — registered functions
5. **Properties** — exposed Boundary properties
6. **State** — State machine reflection accessors

The compiler emits a warning when a name exists in multiple namespaces and is used in an ambiguous context. Authors can disambiguate with type prefixes: `Content.orders`, `Compute.orders`.

### Sandbox Restrictions

- No access to host globals (process, require, eval, Function, window, global)
- No object construction via `new`
- No property assignment outside Compute output declarations
- No async/Promise features
- Maximum expression depth of 20 nesting levels
- Registered functions only

### CEL Dialect

Termin uses full CEL syntax with three restrictions:

- No object literal construction (prevents smuggling past schema validation)
- No async/Promise features (expressions are synchronous and deterministic)
- Compiler-enforced maximum expression depth of 20 levels

All other CEL features — ternary, array filtering, transforms, pipe operator — are permitted.

---

## Compute Registry

### Contract

**`register(name, declaration, implementation) → void`**

Registers a Compute function with typed declaration and implementation.

**`invoke(name, inputs, identityContext) → outputs | error`**

Invokes a Compute function. The registry verifies scope, validates inputs, executes in sandbox, validates outputs, routes errors to the Compute's error Channel.

### Compute Shapes

| Shape | Input | Output | Archetype |
|---|---|---|---|
| Transform | 1 record | 1 record | map, convert, validate, enrich |
| Reduce | N records | 1 record | aggregate, summarize, fold |
| Expand | 1 record | N records | decompose, generate, unfold |
| Correlate | N + M records | K records | join, match, reconcile |
| Route | 1 record | 1 record (type varies) | classify, triage, branch |

### Built-in Verbs

Every runtime must provide:

- `query` — reads Content (delegates to storage adapter)
- `create` — creates Content (delegates to storage adapter)
- `update` — updates Content (delegates to storage adapter)
- `delete` — deletes Content (delegates to storage adapter)
- `transition` — requests a State transition (delegates to State Engine's `requestTransition`; this is the same operation surfaced at two abstraction layers — `transition` is the DSL-facing name, `requestTransition` is the runtime API)

---

## State Engine

The State Engine enforces finite state machines on any named primitive — Content, Channels, Boundaries, or Compute nodes.

### Contract

**`registerStateMachine(primitiveRef, declaration) → void`**

Registers a State machine from the IR.

**`getCurrentState(primitiveRef, recordId) → stateName`**

Returns the current state.

**`requestTransition(primitiveRef, recordId, targetState, identityContext) → newState | error`**

Attempts a transition. Verifies the transition is declared, evaluates the condition (scope check or CEL expression), updates state, emits a transition Event. Invalid transitions route to the primitive's error Channel.

This is the same operation as the built-in `transition` verb — the verb is the DSL-facing interface, `requestTransition` is the runtime API.

**`getAvailableTransitions(primitiveRef, recordId, identityContext) → list of stateName`**

Returns transitions available from current state for the given identity. Used by Presentation and the Reflection system.

### Runtime-Managed State

The runtime automatically maintains State for infrastructure primitives:

**Channel lifecycle:** `open → error → open` (health-check driven), `open → closed` (administrative)

When a Channel enters `error` state, the runtime can automatically retry, back off, or circuit-break based on deployment configuration. Channel state transitions emit Events visible through Reflection.

**Boundary lifecycle:** `active → maintenance → decommissioned`

- **active:** Normal operation. All Channels open, all Compute invocable, all Content accessible.
- **maintenance:** The Boundary accepts reads but rejects writes and new Channel connections. In-flight operations complete. This is analogous to draining a Kubernetes pod — the Boundary is winding down gracefully. Useful for schema migrations, planned updates, or capacity management.
- **decommissioned:** The Boundary is shut down. All Channels closed. Properties return the last known value with a `stale` flag. Access attempts route to the error Channel with kind `decommissioned`. Content may still be readable through an archival adapter if configured.

Boundary lifecycle is managed through deployment configuration (environment variables, config files, admin API) rather than the `.termin` DSL, because these are operational concerns, not application logic.

---

## Channel Enforcer

### Contract

**`registerChannel(declaration) → void`**

Registers a Channel from the IR.

**`send(channelName, payload, identityContext) → void | error`**

Sends a payload. Verifies scope, validates payload schema, authenticates destination, delivers. Failures route to the Channel's error Channel (yes, the Channel's error Channel — errors are just data flowing through the graph).

**`receive(channelName, handler) → void`**

Registers a handler for incoming data.

### Intent-Based Channel Configuration

The `.termin` DSL describes Channels in terms of **communication intent**, not protocol implementation. This keeps business requirements clean of infrastructure decisions.

```
Channel called "order updates":
  Carries orders
  Direction: outbound
  Delivery: realtime
  Requires "read orders" to receive

Channel called "order webhook":
  Carries orders
  Direction: inbound
  Delivery: reliable
  Endpoint: /webhooks/orders
  Requires "write orders" to send

Channel called "internal order bus":
  Carries orders
  Direction: internal
```

**Channel intents:**

| Intent | Property | Values | Meaning |
|---|---|---|---|
| Direction | `Direction` | `inbound`, `outbound`, `bidirectional`, `internal` | Which way data flows relative to the Boundary |
| Delivery | `Delivery` | `realtime`, `reliable`, `batch`, `auto` | Delivery semantics |
| Proximity | `Proximity` | `local`, `remote`, `auto` | Network topology hint |

The runtime maps intents to protocols:

| Direction + Delivery | Typical Protocol |
|---|---|
| outbound + realtime | SSE, WebSocket |
| outbound + reliable | Webhook with retry |
| inbound + reliable | REST with acknowledgment, Webhook |
| inbound + realtime | WebSocket |
| bidirectional + realtime | WebSocket |
| internal | In-process function call |
| any + batch | Queued delivery |

The mapping is deployment-configurable. The `.termin` file expresses what the Channel needs. The deployment decides how.

### Protocol Adapters

Protocol adapters implement the concrete communication. Each adapter satisfies:

```
ProtocolAdapter:
  send(endpoint, payload) → void | error
  listen(endpoint, handler) → void
  healthCheck(endpoint) → boolean
```

Built-in adapters: `internal`, `http` (REST/webhook), `websocket`, `sse`. Additional adapters are pluggable.

---

## Boundary Isolator

### Contract

**`registerBoundary(declaration) → void`**

Registers a Boundary with its contained primitives, identity rules, exposed Channels, exposed Properties, and lifecycle state.

**`enforceIsolation(boundaryName, accessAttempt) → allow | deny`**

Checks whether an access attempt is permitted.

### Access Rules

| From | To | Permitted? |
|---|---|---|
| Primitive inside Boundary A | Other primitive inside Boundary A | Yes (same scope) |
| Primitive inside Boundary A | Anything outside Boundary A | Only through declared Channels |
| Anything outside Boundary A | Anything inside Boundary A | Only through declared Channels or exposed Properties |
| Primitive inside Boundary A | Nested Boundary B's internals | No (B is opaque) |
| Primitive inside Boundary A | Nested Boundary B's Channels/Properties | Yes (B's surface is visible to A) |

A primitive can access a sibling nested Boundary's exposed interface (Channels and Properties) but cannot reach inside it. The nested Boundary is a black box with typed ports.

### Property Exposure

Boundaries expose Properties — typed, identity-scoped, read-only accessors for cross-boundary state queries.

**`getProperty(boundaryName, propertyName, identityContext) → value | error`**

Properties may be:
- **Stored:** direct reference to an internal value
- **Computed:** CEL expression evaluated on demand

```
Boundary called "order processing":
  Contains orders, order lines
  Exposes property "order count" : whole number = [orders.length]
  Exposes property "health" : text = [state.current]
```

### Scope Inheritance and Escalation

Scopes narrow inward through the Boundary hierarchy. The outermost Boundary (the application/platform) has all declared scopes. Each nested Boundary inherits its parent's scopes but can restrict to a subset.

**Privilege escalation** is supported through **ScopeGrants** — declared, auditable, time-bounded escalation requests. An inner Boundary can request a scope from its parent Boundary that it doesn't normally have.

```
Boundary called "emergency override":
  Identity restricts to "read orders"
  Can request "admin orders" from parent
    When [alert.severity == "critical"]
    For duration: 15 minutes
    Requires approval from "order manager"
    Logged as "emergency escalation"
```

A ScopeGrant:
- Must be **declared** in the `.termin` file (no runtime-constructed escalations)
- Must specify a **condition** (CEL expression that must be true)
- Must specify a **duration** (the grant expires automatically)
- May require **approval** from a specific role (the approver's identity is captured)
- Is **logged as an Event** with full audit trail (who requested, who approved, what scope, when, why)

This is analogous to IAM AssumeRole or `sudo` — you don't permanently widen the scope, you get a temporary, auditable credential for a specific action. The TerminAtor logs every escalation.

---

## Event Bus

### Contract

**`emit(eventName, payload, sourceContext) → void`**

Emits an Event. Validates payload schema, logs the Event, routes to subscribers.

**`subscribe(eventName, handler) → subscription`**

Registers a handler.

**`unsubscribe(subscription) → void`**

Removes a subscription.

### Event Payload Structure

```
EventPayload:
  eventName: string
  timestamp: datetime
  source: string (primitive that emitted)
  identity: IdentityContext (who caused it)
  data: Content (conforming to declared schema)
  logLevel: one of: TRACE, DEBUG, INFO, WARN, ERROR
```

### Log Levels

Every Event has a `logLevel`. The default is `INFO` unless overridden in the Event declaration or the emit call.

```
When [stockLevel.quantity <= stockLevel.reorderThreshold]:
  Create reorder alert with [...]
  Log level: WARN

When [stockLevel.quantity > stockLevel.reorderThreshold]:
  (Stock is healthy, log quietly)
  Log level: TRACE
```

The runtime filters, stores, and purges Events based on log level and configuration:

| Level | Meaning | Default Retention |
|---|---|---|
| TRACE | Verbose diagnostic detail | Short (hours), or not logged |
| DEBUG | Debugging information | Short (days) |
| INFO | Normal operational events | Medium (weeks) |
| WARN | Unusual conditions requiring attention | Long (months) |
| ERROR | Errors routed through TerminAtor | Long (months), never auto-purged |

Retention policies are deployment-configurable per application, per Boundary, or per primitive.

---

## Error Router (The TerminAtor)

Every primitive has an **error Channel**. Errors are Content conforming to the TerminError schema. Errors flow through the graph like everything else. Errors never halt the pipeline.

### TerminError Schema

```
Content called "TerminError":
  Each error has a source which is text, required
  Each error has a kind which is text, required
  Each error has a message which is text, required
  Each error has a timestamp which is automatic
  Each error has a context which is text
  Each error has an original payload which is text
  Each error has a boundary path which is list of text
```

**`kind` is a string, not a fixed enum.** Standard kinds are conventions:

| Kind | Meaning |
|---|---|
| `validation` | Schema validation failure |
| `authorization` | Scope or identity check failed |
| `state` | Rejected state transition |
| `timeout` | Execution time exceeded |
| `schema` | Content schema mismatch on Channel |
| `internal` | Runtime-internal error |
| `external` | Error originating from outside the Boundary (remote host closed connection, upstream API failure, etc.) |
| `decommissioned` | Access to a decommissioned Boundary |
| `escalation_denied` | ScopeGrant request was denied |

Because `kind` is a string and TerminError uses duck typing, custom error types can compose it by adding fields. A Compute function that catches vendor API errors could produce:

```
TerminError with:
  kind: "vendor_api"
  message: "Stripe returned 429"
  vendorCode: 429
  retryAfter: 30
```

The additional fields pass through the error Channel alongside the standard TerminError fields.

### Error Escalation Through Boundary Hierarchy

Errors do not jump directly to a global handler. They escalate through the Boundary hierarchy:

1. Error occurs in a primitive inside Boundary B
2. TerminAtor routes to Boundary B's error Channel
3. If a handler in Boundary B processes the error → handled, stops here
4. If no handler in Boundary B → TerminAtor escalates to Boundary B's parent Boundary's error Channel, adding the Boundary name to `boundary path`
5. Repeat up the hierarchy
6. If the error reaches the outermost Boundary (application scope) and no handler processes it → the **global catch-all** handles it (logs, alerts, or whatever the deployment configures)

The `boundary path` field tracks the escalation chain: `["compute:calculateTotal", "boundary:orderProcessing", "boundary:application"]`. This is the stack trace equivalent for Termin's graph architecture.

### Error Channels on Primitives

Every primitive automatically gets an error Channel:

| Primitive | Error Sources |
|---|---|
| Content | Schema validation failures, constraint violations |
| Compute | Execution errors, timeout, input/output schema mismatches |
| Channel | Authentication failures, payload schema violations, delivery failures, protocol errors, remote disconnections (`external` kind) |
| State | Rejected transitions, failed conditions |
| Event | Emission failures, subscriber errors |
| Boundary | Isolation violations, property access errors, lifecycle state violations |
| Identity | Authentication failures, role resolution failures |

---

## Presentation Renderer

### Contract

**`render(presentationDeclaration, expressionContext) → output`**

Takes a Presentation declaration and expression context, produces rendered output (HTML, JSON, native views — renderer-specific).

**`selectPresentation(pageName, identityContext) → presentationDeclaration`**

When multiple Presentation declarations exist for the same page (role-scoped views), selects the correct one based on role.

### Rendering Rules

- Only render elements the current identity has scope to access
- Evaluate display expressions through the Expression Evaluator
- Populate tables by querying Content through the Storage Adapter (access rules enforced)
- Real-time subscriptions register with the Event Bus
- Available State transitions (from Reflection) determine which action buttons to show

---

## Lifecycle

### Startup Sequence

```
Phase 0: Bootstrap
  Initialize bootstrap error handler (writes to stderr — last resort)

Phase 1: Error Infrastructure
  Initialize Error Router (the TerminAtor)
  Create global error Channel
  (From this point, all errors route through TerminAtor)

Phase 2: Core Services
  Initialize Expression Evaluator
  Compile and validate ALL expressions from IR
  (Parse errors caught here, before any runtime execution)
  Initialize Identity binding

Phase 3: Data Layer
  Initialize storage adapters
  Create/verify Content schemas
  Run pending migrations

Phase 4: Primitives
  Register State machines (set initial states)
  Register Compute functions (built-in verbs first, then custom)
  Register Channels (initialize protocol adapters, open listeners)
  Register Boundaries (establish isolation, expose Properties)
  Register Events (wire triggers to subscribers)
  Attach error Channels to all primitives

Phase 5: Serve
  Start Presentation Renderer
  Begin accepting requests
```

The key ordering decisions:

- **TerminAtor initializes first** (Phase 1) so all subsequent initialization errors have somewhere to go
- **Expression validation happens in Phase 2** — the compiler should catch parse errors, but the runtime validates as a defense-in-depth measure. If a custom Compute expression fails validation here, the error routes through the TerminAtor (which is already running) rather than crashing
- **Storage initializes before State** (Phase 3 before Phase 4) because State machines may need to read current state from persisted data

### Shutdown Sequence

1. Stop accepting new requests
2. Transition all Boundaries to `maintenance` state
3. Drain in-flight Channel messages
4. Flush Event log
5. Transition all Boundaries to `decommissioned` state
6. Close protocol adapters
7. Close storage connections
8. Flush TerminAtor error log
9. Shut down TerminAtor

---

## Conformance

A Termin runtime implementation is conformant if:

1. It implements all contracts defined in this document
2. All Content access is parameterized (no constructed queries)
3. All Channel crossings enforce schema validation and identity checking
4. All State transitions are explicitly declared; undeclared transitions are rejected
5. All Compute execution is sandboxed per the defined restrictions
6. All errors route through the TerminAtor; no errors halt the pipeline
7. Errors escalate through the Boundary hierarchy before reaching the global handler
8. The Identity binding interface is pluggable
9. The Storage Adapter interface is pluggable
10. Channel configuration uses intent-based vocabulary, not protocol names
11. ScopeGrants are declared, auditable, time-bounded, and logged
12. Passing the **Termin Conformance Test Suite**

### Conformance Test Suite

The Termin project provides a language-agnostic conformance test suite — a set of `.termin` applications and expected behaviors. A conformant runtime must compile and execute all test applications, produce expected outputs, reject all invalid operations, and route all errors through the TerminAtor.

The test suite is authoritative. Where this document and the test suite disagree, the test suite wins.
