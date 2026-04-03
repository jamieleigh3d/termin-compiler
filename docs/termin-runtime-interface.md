# Termin Runtime Interface Specification

**Version:** 0.1.0-draft
**Status:** Formative

---

## Overview

The Termin runtime is the execution environment that enforces security invariants and provides the operational substrate for compiled Termin applications. This document defines the **contracts** that any runtime implementation must satisfy, regardless of implementation language, deployment topology, or infrastructure target.

A Termin runtime could be as simple as a single Python process with a web server, or as distributed as a multi-service cloud deployment. The contracts are the same. What changes is the implementation behind each interface.

The compiler produces an **Intermediate Representation (IR)** — a validated, language-agnostic data structure describing the application's primitives, channels, boundaries, and expressions. The runtime receives this IR and executes it.

---

## Architecture

The runtime is composed of eight subsystems, one per primitive, plus two cross-cutting concerns (Expression Evaluation and Error Handling).

```
┌─────────────────────────────────────────────────────┐
│                   Termin Runtime                     │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ Identity │  │ Content  │  │  Expression      │   │
│  │ Binding  │  │ Storage  │  │  Evaluator (JEXL)│   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ Compute  │  │  State   │  │  Channel         │   │
│  │ Registry │  │  Engine  │  │  Enforcer        │   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ Boundary │  │  Event   │  │  Error           │   │
│  │ Isolator │  │  Bus     │  │  Router          │   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  Presentation Renderer                        │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  Property Accessor                            │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

---

## Identity Binding Interface

The Identity subsystem authenticates principals and resolves their scopes and roles. Identity is pluggable — the runtime defines the interface, deployments provide the implementation.

### Contract

Any Identity binding must implement:

**`authenticate(request) → IdentityContext | Anonymous`**

Takes an incoming request (however the deployment represents requests) and returns an IdentityContext containing the authenticated principal, their resolved role, and their effective scopes. If authentication fails or is not provided, returns the Anonymous context with whatever scopes Anonymous has been granted in the application's Identity declaration.

**`resolveRole(principal, applicationRoles) → Role`**

Maps an authenticated principal to one of the application's declared roles. The mapping logic is deployment-specific (group membership, claims, attributes, etc.).

**`hasScope(identityContext, scope) → boolean`**

Checks whether the given identity context includes the specified scope. This is called by every other subsystem before granting access.

### IdentityContext Structure

```
IdentityContext:
  principal: object        (the authenticated user — shape is deployment-specific)
  role: string             (the matched application role name)
  scopes: list of string   (effective scopes for this role)
  isAnonymous: boolean
```

The `principal` object is opaque to the runtime — its shape depends on the Identity binding (OAuth token claims, SAML attributes, etc.). The runtime only accesses it through the `CurrentUser` property, which the binding exposes.

### Built-in Bindings

The runtime must ship with a `stub` binding for development and testing. The stub binding accepts a user identity from a configuration file or request header without real authentication. It supports defining test users with specific roles.

---

## Content Storage Adapter Interface

The Content subsystem stores and retrieves typed data. Storage is pluggable — the runtime defines the interface, deployments provide the implementation.

### Contract

Any storage adapter must implement:

**`createSchema(contentDeclaration) → void`**

Accepts a Content declaration from the IR and initializes whatever storage structures are needed (tables, collections, indices, etc.).

**`create(contentName, record, identityContext) → record | error`**

Creates a new record conforming to the named Content schema. The adapter must validate that the record conforms to the schema, enforce unique constraints, resolve references, set automatic fields, and verify that the identity context has the required scope for creation. Returns the created record (with any auto-generated fields populated) or an error.

**`read(contentName, query, identityContext) → list of records | error`**

Reads records from the named Content. The query is a structured filter object (not raw SQL or any backend-specific query language). The adapter must verify that the identity context has the required scope for viewing.

**`update(contentName, id, changes, identityContext) → record | error`**

Updates an existing record. The adapter must validate that the changes conform to the schema, enforce constraints, and verify scope.

**`delete(contentName, id, identityContext) → void | error`**

Deletes a record. The adapter must verify scope.

**`validate(contentName, record) → validationResult`**

Validates a record against its Content schema without storing it. Used by Channel enforcement and Compute sandboxing to validate data at boundaries.

### Query Structure

Queries are expressed as a structured filter object, never as raw backend queries. This is the structural basis for injection prevention — the storage adapter receives parameterized operations, not constructed query strings.

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

SortField:
  field: string
  direction: asc | desc
```

### Built-in Adapters

The runtime should ship with at least an in-memory adapter (for development, testing, and preview) and one persistent adapter (SQLite is recommended as the simplest portable option).

### Migration

When Content schemas change between application versions, the **compiler** is responsible for emitting migration scripts. The storage adapter implements a `migrate(migrationScript) → void` method that applies schema changes. The adapter is not responsible for determining what changed — only for executing the migration.

---

## Expression Evaluator

The Expression Evaluator executes JEXL expressions in a controlled context. It is the engine behind Compute bodies, Event conditions, Presentation expressions, Property definitions, and State transition conditions.

### Contract

**`evaluate(expression, context) → value | error`**

Evaluates a JEXL expression string against a provided context object. The context contains only explicitly provided values — no ambient globals.

**`compile(expression) → compiledExpression | error`**

Parses and validates a JEXL expression at compile time. Returns a compiled representation for efficient repeated evaluation, or an error if the expression references undeclared variables, functions, or Content fields.

**`registerFunction(name, function) → void`**

Registers a named function that JEXL expressions can invoke. Registered functions are the only callable functions in JEXL context.

**`registerTransform(name, function) → void`**

Registers a named transform for the JEXL pipe operator (`value|transform`).

### Expression Context

Every JEXL expression evaluates against an **ExpressionContext** assembled by the runtime for each request or evaluation cycle. The context contains:

```
ExpressionContext:
  identity: IdentityContext           (who is acting)
  content: map of ContentAccessors    (typed access to stored Content)
  compute: map of ComputeFunctions    (registered Compute functions)
  locals: map of values               (named inputs within a Compute body)
  properties: map of PropertyValues   (exposed Boundary properties)
  state: map of StateAccessors        (reflection on State machines)
```

### Scope Resolution Chain

When an expression references a name, the evaluator resolves it in this order:

1. **Locals** — named inputs within the current Compute body
2. **Identity** — CurrentUser, role name, scopes
3. **Content** — declared Content schemas and their records
4. **Compute** — registered Compute functions
5. **Properties** — exposed Boundary properties
6. **State** — State machine reflection accessors

The first match wins. Shadowing is permitted but the compiler emits a warning.

### Sandbox Restrictions

The evaluator must enforce:

- No access to host globals (process, require, eval, Function, window, global, etc.)
- No object construction via `new`
- No property assignment outside Compute output declarations
- No async/Promise features
- Maximum expression depth of 20 nesting levels
- Registered functions only — unregistered function calls fail

---

## Compute Registry

The Compute Registry manages built-in verbs and custom Compute functions.

### Contract

**`register(name, declaration, implementation) → void`**

Registers a Compute function with its name, typed declaration (input/output Channel schemas and shape), and implementation.

**`invoke(name, inputs, identityContext) → outputs | error`**

Invokes a registered Compute function. The registry must:
1. Verify the identity context has execute permission
2. Validate all inputs against declared input schemas
3. Execute the implementation in a sandboxed context
4. Validate all outputs against declared output schemas
5. Return validated outputs or route errors to the error channel

### Compute Shapes

The registry recognizes five Compute shapes:

| Shape | Input Cardinality | Output Cardinality |
|---|---|---|
| Transform | 1 record | 1 record |
| Reduce | N records | 1 record |
| Expand | 1 record | N records |
| Correlate | N + M records | K records |
| Route | 1 record | 1 record (type varies) |

The shape declaration determines how the runtime marshals inputs and outputs through Channels.

### Built-in Verbs

Every runtime must provide these built-in Compute functions:

- `query` — reads Content (delegates to storage adapter)
- `create` — creates Content (delegates to storage adapter)
- `update` — updates Content (delegates to storage adapter)
- `delete` — deletes Content (delegates to storage adapter)
- `transition` — requests a State transition (delegates to State engine)

### Custom Compute Sandboxing

Custom Compute functions execute in a restricted context. The runtime must ensure:

- The function receives only its declared inputs
- The function can only produce its declared outputs
- The function has no access to the filesystem, network, or any resource outside its declared Channels
- The function's execution time is bounded (runtime-configurable timeout)

---

## State Engine

The State Engine enforces finite state machines on any named primitive.

### Contract

**`registerStateMachine(primitiveRef, declaration) → void`**

Registers a State machine from the IR. The declaration includes the initial state, valid states, valid transitions, and conditions for each transition.

**`getCurrentState(primitiveRef, recordId) → stateName`**

Returns the current state of a specific record or primitive instance.

**`requestTransition(primitiveRef, recordId, targetState, identityContext) → newState | error`**

Attempts a state transition. The engine must:
1. Verify the transition is declared (source → target exists)
2. Evaluate the transition condition (scope check or JEXL expression)
3. If valid, update the state and emit a transition Event
4. If invalid, route an error to the primitive's error Channel

**`getAvailableTransitions(primitiveRef, recordId, identityContext) → list of stateName`**

Returns the transitions available from the current state for the given identity. Used by Presentation for conditional UI rendering and by the Reflection system.

### Runtime-Managed State

The runtime automatically maintains State for infrastructure primitives:

- **Channels:** open, closed, error (managed by the Channel Enforcer based on health checks)
- **Boundaries:** active, maintenance, decommissioned (managed by deployment configuration)

These states are exposed through the Reflection/Property system and can trigger Events.

---

## Channel Enforcer

The Channel Enforcer authenticates, validates, and routes data flowing through Channels.

### Contract

**`registerChannel(declaration) → void`**

Registers a Channel from the IR with its Content schema, protocol, source/destination Boundaries, endpoint, and access requirements.

**`send(channelName, payload, identityContext) → void | error`**

Sends a payload through a Channel. The enforcer must:
1. Verify the identity context has the required scope
2. Validate the payload against the Channel's declared Content schema
3. Authenticate the destination endpoint (for external Channels)
4. Deliver the payload
5. On failure, route to the Channel's error Channel

**`receive(channelName, handler) → void`**

Registers a handler for incoming data on a Channel.

### Internal vs External Channels

**Internal Channels** (both endpoints within the same Boundary) carry typed Content but do not require per-message authentication — both endpoints share a security scope.

**External Channels** (crossing a Boundary) require authentication at both endpoints and schema validation on every payload.

### Protocol Adapters

The Channel Enforcer delegates protocol-specific behavior to protocol adapters. Each protocol adapter implements:

```
ProtocolAdapter:
  send(endpoint, payload) → void | error
  listen(endpoint, handler) → void
  healthCheck(endpoint) → boolean
```

Built-in protocol adapters: `internal` (in-process), `REST` (HTTP), `websocket`, `SSE` (server-sent events), `webhook` (outbound HTTP). Additional protocols are pluggable.

---

## Boundary Isolator

The Boundary Isolator enforces scope containment and access control at the Boundary layer.

### Contract

**`registerBoundary(declaration) → void`**

Registers a Boundary from the IR with its contained primitives, identity inheritance rules, exposed Channels, and exposed Properties.

**`enforceIsolation(boundaryName, accessAttempt) → allow | deny`**

Checks whether an access attempt (from inside or outside the Boundary) is permitted. The rules:
- Primitives inside a Boundary can access other primitives in the same Boundary
- Nothing inside a Boundary can access anything outside except through a declared Channel
- Nothing outside a Boundary can access anything inside except through a declared Channel or exposed Property
- Nested Boundaries inherit the parent's Identity context but can restrict it (narrower scopes only)

### Property Exposure

Boundaries expose Properties — typed, identity-scoped, read-only accessors. Properties are the mechanism for cross-boundary state queries.

**`getProperty(boundaryName, propertyName, identityContext) → value | error`**

Returns the current value of an exposed Property. The Boundary must verify the identity context has the required scope.

Properties are defined on the Boundary and may be:
- **Stored:** a direct reference to a value inside the Boundary
- **Computed:** a JEXL expression evaluated on demand against the Boundary's internal state

```
Boundary "order processing" exposes:
  property "order count" : whole number = [orders.length]
  property "health" : text = [state.current]
```

---

## Event Bus

The Event Bus handles emission, routing, and subscription of Events.

### Contract

**`emit(eventName, payload, sourceContext) → void`**

Emits an Event with a typed payload. The Event Bus must:
1. Validate the payload against the Event's declared schema
2. Log the Event with payload, timestamp, source, and identity (audit trail)
3. Route the Event to all registered subscribers through their Channels

**`subscribe(eventName, handler) → subscription`**

Registers a handler for a named Event type.

**`unsubscribe(subscription) → void`**

Removes a subscription.

### Event Payload Structure

```
EventPayload:
  eventName: string
  timestamp: datetime
  source: string (primitive name that emitted)
  identity: IdentityContext (who caused it)
  data: Content (conforming to declared schema)
```

All Events are immutable and tamper-evident. The runtime must store Events in an append-only log.

---

## Error Router

Every primitive has an **error Channel** — a typed Channel that carries error Content when something goes wrong. Errors are Content, they flow through the graph like everything else, and they never tear down the runtime.

### Contract

**`routeError(sourcePrimitive, errorContent) → void`**

Routes an error from a primitive to its error Channel. Error Content conforms to a standard error schema:

```
Content called "TerminError":
  Each error has a source which is text, required
  Each error has a kind which is one of: validation, authorization, state, timeout, schema, internal
  Each error has a message which is text, required
  Each error has a timestamp which is automatic
  Each error has a context which is text
  Each error has an original payload which is text
```

**`onError(primitiveName, handler) → subscription`**

Subscribes to errors from a specific primitive's error Channel.

**`onAnyError(handler) → subscription`**

Subscribes to errors from all primitives (global error Channel).

### Error Flow

When an error occurs:

1. The subsystem that detected the error constructs a TerminError record
2. The Error Router sends the error to the source primitive's error Channel
3. If any Event triggers or Compute functions are subscribed to the error Channel, they execute
4. If no subscriber handles the error, the Error Router sends it to the global error Channel
5. The runtime logs the error regardless of whether it is handled

Errors never halt the pipeline. The pattern is identical to a dead letter queue — the runtime continues processing other work while errors accumulate for observation and handling.

### Error Channels on Primitives

Every primitive automatically gets an error Channel:

- **Content:** schema validation failures, constraint violations
- **Compute:** execution errors, timeout, input/output schema mismatches
- **Channel:** authentication failures, schema validation on payload, delivery failures, protocol errors
- **State:** rejected transitions (undeclared transition, failed condition)
- **Event:** emission failures, subscriber errors
- **Boundary:** isolation violations, property access errors
- **Identity:** authentication failures, role resolution failures

---

## Presentation Renderer

The Presentation Renderer transforms the IR's Presentation declarations into user-facing interfaces.

### Contract

**`render(presentationDeclaration, expressionContext) → output`**

Takes a Presentation declaration from the IR and an expression context, and produces the rendered output. The output format is renderer-specific — HTML for web, JSON for API, native views for mobile, etc.

**`selectPresentation(pageName, identityContext) → presentationDeclaration`**

When multiple Presentation declarations exist for the same page (role-scoped views), selects the correct one based on the identity context's role. If no role-specific declaration matches, falls back to the most general match.

### Rendering Rules

- The renderer only includes UI elements that the current identity context has scope to access
- Display expressions (`Display text [expr]`) are evaluated through the Expression Evaluator
- Tables are populated by querying Content through the Storage Adapter with the current identity context (so access rules are enforced)
- Real-time subscriptions (`subscribes to <content> changes`) register with the Event Bus

---

## Lifecycle

### Startup Sequence

When a runtime boots with a compiled IR:

1. **Register Identity binding** — initialize the configured authentication provider
2. **Initialize storage** — create/verify schemas via the storage adapter
3. **Register State machines** — load all State declarations and set initial states
4. **Register Compute functions** — load built-in verbs and custom Compute
5. **Register Channels** — initialize protocol adapters, open listeners
6. **Register Boundaries** — establish isolation rules and Property accessors
7. **Register Events** — wire Event triggers to their subscribers
8. **Start error routing** — initialize error Channels on all primitives
9. **Start serving** — begin accepting requests and rendering Presentation

### Shutdown Sequence

1. Stop accepting new requests
2. Drain in-flight Channel messages
3. Flush Event log
4. Close protocol adapters
5. Close storage connections

---

## Conformance

A Termin runtime implementation is conformant if:

1. It implements all contracts defined in this document
2. All Content access is parameterized (no constructed queries)
3. All Channel crossings enforce schema validation and identity checking
4. All State transitions are explicitly declared; undeclared transitions are rejected
5. All Compute execution is sandboxed per the defined restrictions
6. All errors route to error Channels; no errors halt the pipeline
7. The Identity binding interface is pluggable
8. The Storage Adapter interface is pluggable
9. Passing the **Termin Conformance Test Suite** (see below)

### Conformance Test Suite

The Termin project will provide a language-agnostic conformance test suite. The test suite is a set of `.termin` applications and expected behaviors. A conformant runtime must:

- Compile and execute all test applications
- Produce the expected outputs for given inputs
- Reject all invalid operations (undeclared transitions, unauthorized access, schema violations)
- Route all errors to error Channels without halting

The test suite is the authoritative definition of runtime behavior. Where this document and the test suite disagree, the test suite wins.
