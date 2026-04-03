# Termin Reflection and Error Handling Specification

**Version:** 0.1.0-draft
**Status:** Formative

---

## Overview

This document specifies two closely related systems: **Reflection** (how primitives observe the state of other primitives at runtime) and **Error Handling in the DSL** (how application authors declare what happens when things go wrong).

Both systems answer the same fundamental question: how do you observe and react to what's happening in the graph?

Reflection provides the observation. Error handling provides the reaction.

---

# Part 1: Reflection

## Design Principles

Reflection in Termin is **read-only introspection** on the runtime state of any primitive. It is the mechanism by which Presentation decides which buttons to show, Event conditions check system state, ScopeGrants evaluate eligibility, and administrators monitor application health.

Reflection is:

- **Read-only.** Reflection observes. It never mutates. You cannot change a State, invoke a Compute, or send through a Channel via Reflection.
- **Identity-scoped.** Reflection respects the same access rules as everything else. You can only reflect on primitives your identity context has scope to see.
- **Boundary-aware.** You can reflect on primitives within your Boundary and on the exposed surface (Properties, Channel state, lifecycle) of other Boundaries. You cannot reflect on the internals of another Boundary.
- **Accessible via JEXL.** All Reflection data is available in JEXL expressions through a consistent accessor syntax.

## Reflection Accessors

Every primitive type exposes a Reflection interface. Accessors follow a consistent pattern: `<primitiveName>.reflect.<property>`.

The `.reflect` namespace is reserved. It is always read-only and always returns typed values.

### Content Reflection

```
Content.reflect.schemas                    → list of schema names
Content.reflect.schema("products")         → schema definition (fields, types, constraints)
Content.reflect.schema("products").fields   → list of field names
Content.reflect.schema("products").field("SKU").type  → "text"
Content.reflect.schema("products").field("SKU").constraints  → ["unique", "required"]
Content.reflect.count("products")          → record count (identity-scoped)
Content.reflect.exists("products")         → boolean
```

**Use cases:**
- Dynamic form generation: iterate schema fields to build input forms
- Admin dashboards: display record counts per Content type
- Validation: check whether a Content type exists before referencing it

### Compute Reflection

```
Compute.reflect.functions                  → list of registered function names
Compute.reflect.function("SayHelloTo")     → function metadata
Compute.reflect.function("SayHelloTo").shape  → "Transform"
Compute.reflect.function("SayHelloTo").inputs  → [{name: "u", type: "UserProfile"}]
Compute.reflect.function("SayHelloTo").outputs → [{name: "greeting", type: "Text"}]
Compute.reflect.function("SayHelloTo").canExecute(identity)  → boolean
```

**Use cases:**
- Self-documenting APIs: auto-generate endpoint documentation from registered Compute
- Dynamic dispatch: select a Compute function based on runtime conditions
- Admin views: show which Compute functions are available and their shapes

### State Reflection

```
<primitiveName>.state.current              → current state name
<primitiveName>.state.initial              → initial state name
<primitiveName>.state.all                  → list of all declared states
<primitiveName>.state.transitions          → list of states reachable from current
<primitiveName>.state.canTransition("active")  → boolean (checks both declaration and identity scope)
<primitiveName>.state.history              → list of past transitions (if auditing enabled)
<primitiveName>.state.machine              → full state machine definition (states, transitions, conditions)
```

For Content with per-record State:
```
products.state.current(recordId)           → state of specific record
products.state.transitions(recordId)       → transitions available for specific record
```

**Use cases:**
- Conditional UI: show "Approve" button only if `[order.state.canTransition("approved")]`
- Workflow dashboards: display counts per state
- Audit trails: inspect transition history

### Channel Reflection

```
Channel.reflect.channels                   → list of registered channel names
Channel.reflect.channel("order webhook")   → channel metadata
Channel.reflect.channel("order webhook").carries  → "orders" (Content schema name)
Channel.reflect.channel("order webhook").direction  → "inbound"
Channel.reflect.channel("order webhook").delivery   → "reliable"
Channel.reflect.channel("order webhook").state.current  → "open" | "closed" | "error"
Channel.reflect.channel("order webhook").state.canTransition("closed")  → boolean
Channel.reflect.channel("order webhook").metrics.sent       → count of messages sent
Channel.reflect.channel("order webhook").metrics.errors     → count of errors
Channel.reflect.channel("order webhook").metrics.lastActive → datetime
```

**Use cases:**
- Health dashboards: show Channel states across the application
- Circuit breaker UI: display which Channels are in error state
- Monitoring: alert when error count exceeds threshold

### Boundary Reflection

```
Boundary.reflect.boundaries                → list of registered boundary names
Boundary.reflect.boundary("order processing")  → boundary metadata
Boundary.reflect.boundary("order processing").contains  → list of primitive names
Boundary.reflect.boundary("order processing").state.current  → "active" | "maintenance" | "decommissioned"
Boundary.reflect.boundary("order processing").scopes  → list of available scopes
Boundary.reflect.boundary("order processing").properties  → list of exposed property names
Boundary.reflect.boundary("order processing").property("order count")  → current value
Boundary.reflect.boundary("order processing").channels  → list of Channel names on surface
```

**Use cases:**
- Admin dashboards: show Boundary lifecycle states
- Deployment tooling: check which Boundaries are in maintenance
- Capacity planning: inspect what primitives are inside each Boundary

### Identity Reflection

```
Identity.reflect.currentUser               → principal object
Identity.reflect.role                      → current role name
Identity.reflect.scopes                    → list of effective scopes
Identity.reflect.hasScope("admin orders")  → boolean
Identity.reflect.isAnonymous               → boolean
Identity.reflect.grants                    → list of active ScopeGrants (if any)
Identity.reflect.grant("emergency override").expiresAt  → datetime
```

**Use cases:**
- Presentation: conditionally render UI elements based on scopes
- ScopeGrant monitoring: show active escalations and their expiry
- Debugging: inspect the resolved identity context

### Event Reflection

```
Event.reflect.events                       → list of registered event names
Event.reflect.event("reorder alert").schema  → payload schema
Event.reflect.event("reorder alert").subscribers  → count of active subscribers
Event.reflect.event("reorder alert").lastEmitted  → datetime
Event.reflect.event("reorder alert").logLevel  → "WARN"
```

**Use cases:**
- Event monitoring dashboards
- Debugging: verify events are firing and have subscribers
- Configuration: check log levels per event type

## Reflection in the DSL

Reflection accessors are available anywhere a JEXL expression is accepted:

```
(In Presentation:)
As an order manager, I want to see system health
  so that I can monitor operations:
    Show a page called "System Health"
    Display a table of [Channel.reflect.channels] with columns: name, state, errors
    Display text ["Active boundaries: " + Boundary.reflect.boundaries.filter(.state.current == "active").length]
    Highlight rows where [.state.current == "error"]

(In Events:)
When [Channel.reflect.channel("order webhook").metrics.errors > 100]:
  Create alert with ["order webhook error threshold exceeded"]
  Log level: ERROR

(In State conditions:)
A draft product can become active if [Identity.reflect.hasScope("write inventory") && Content.reflect.count("stock levels") > 0]

(In Compute:)
Compute called "system report":
  Reduce: takes nothing, produces report : reports
  [report.totalContent = Content.reflect.schemas.length]
  [report.totalCompute = Compute.reflect.functions.length]
  [report.channelsInError = Channel.reflect.channels.filter(.state.current == "error").length]
  Anyone with "admin" can execute this
```

## Reflection Runtime Contract

The runtime must implement:

**`reflect(primitiveType, path, identityContext) → value | error`**

Resolves a Reflection accessor path against the current runtime state. The identity context determines what is visible — the accessor returns only information the caller has scope to see.

If the caller lacks scope to see a primitive, the Reflection accessor returns `undefined` for that primitive (not an error — the primitive is simply invisible to this identity).

---

# Part 2: Error Handling in the DSL

## Design Principles

Error handling in Termin follows the same principles as everything else in the graph: it's **declarative**, it flows through **Channels**, it respects **Boundaries**, and it's expressed in the **DSL**.

The TerminAtor routes errors through the Boundary hierarchy. But the application author needs to be able to declare:

- What to do when specific errors occur
- Whether to retry, transform, escalate, or disable
- How to recover from transient failures
- How to surface errors to users

Error handling is not exception handling. There is no try/catch. Errors are Events that arrive on error Channels, and you subscribe to them the same way you subscribe to any other Event.

## Error Subscription Syntax

Error Channels are subscribable in the DSL using the `On error` keyword:

```
On error from "<primitive-name>":
  <handler declarations>

On error from "<primitive-name>" where [<condition>]:
  <handler declarations>
```

The `where` clause filters errors by JEXL condition, typically on `error.kind`:

```
On error from "order webhook" where [error.kind == "external"]:
  Retry 3 times with backoff
  Then disable channel
  Log level: ERROR

On error from "calculate order total" where [error.kind == "timeout"]:
  Retry 1 time
  Then escalate

On error from "orders" where [error.kind == "validation"]:
  Create "validation failure" event with [error.source, error.message, error.originalPayload]
  Log level: WARN
```

### Handler Actions

Error handlers support a defined set of recovery actions:

**Retry:**
```
Retry <n> times
Retry <n> times with backoff
Retry <n> times with backoff, maximum delay <duration>
```

`with backoff` uses exponential backoff. The runtime doubles the delay between each retry starting from a deployment-configurable base (default: 1 second). `maximum delay` caps the backoff.

After all retries are exhausted, the handler falls through to the next action (the `Then` clause).

**Disable:**
```
Then disable channel
Then disable compute
```

Transitions the primitive to a disabled/error State. For Channels, this moves the Channel's state machine to `closed` or `error`. The primitive stops accepting new operations until manually re-enabled or until a health check restores it.

**Escalate:**
```
Then escalate
```

Passes the error to the parent Boundary's error Channel. This is the explicit version of what the TerminAtor does automatically for unhandled errors — but declaring it in a handler lets you retry first, then escalate only if retries fail.

**Transform:**
```
Then create <event-or-content> with [<expression>]
```

Converts the error into a different Event or Content record. This is how errors become user-visible notifications, audit records, or alerts.

**Notify:**
```
Then notify "<role>" with [<expression>]
```

Creates a notification Event targeted at a specific role. The Presentation layer can subscribe to notification Events and render them as alerts, toasts, or dashboard warnings.

**Recover:**
```
Then set [<expression>]
```

Applies a corrective action via a JEXL expression. For example, setting a default value or clearing a flag that caused the error.

### Combining Actions

Handler actions can be combined in sequence using `Then`:

```
On error from "vendor API" where [error.kind == "external"]:
  Retry 3 times with backoff, maximum delay 30 seconds
  Then notify "order manager" with ["Vendor API failing: " + error.message]
  Then disable channel
  Log level: ERROR
```

The actions execute in order. If `Retry` succeeds on any attempt, the subsequent `Then` actions do not execute.

### Catch-All Handlers

A Boundary-level catch-all handles any error that reaches the Boundary without being caught by a more specific handler:

```
Boundary called "order processing":
  Contains orders, order lines
  
  On any error:
    Create "system alert" event with [error.source, error.kind, error.message]
    Then escalate
    Log level: ERROR
```

The application-level catch-all is the global handler — the last stop before the TerminAtor's built-in logging:

```
Application: Warehouse Inventory Manager
  Description: Track products and stock levels

  On any error:
    Create "application error" event with [error]
    Log level: ERROR
```

## Error Handling on Specific Primitives

### Content Errors

```
On error from "products" where [error.kind == "validation"]:
  Then notify "warehouse clerk" with ["Invalid product data: " + error.message]
  Log level: WARN

On error from "products" where [error.kind == "authorization"]:
  Then notify "warehouse manager" with ["Unauthorized access attempt on products"]
  Log level: WARN
```

### Compute Errors

```
On error from "calculate order total" where [error.kind == "timeout"]:
  Retry 2 times
  Then create "failed calculation" event with [error.originalPayload]
  Log level: ERROR
```

### Channel Errors

```
On error from "order webhook" where [error.kind == "external"]:
  Retry 3 times with backoff
  Then disable channel
  Then notify "order manager" with ["Order webhook is down"]
  Log level: ERROR

On error from "order webhook" where [error.kind == "schema"]:
  (Payload didn't match expected schema — don't retry, it'll fail again)
  Create "malformed webhook" event with [error.originalPayload]
  Log level: WARN
```

### State Errors

```
On error from "product lifecycle" where [error.kind == "state"]:
  (Someone tried an invalid state transition)
  Then notify [Identity.reflect.role] with ["Cannot transition: " + error.message]
  Log level: INFO
```

## Error Handling in Presentation

Presentation can subscribe to error Events for user-facing error display:

```
As a warehouse clerk, I want to see errors related to my work
  so that I can fix issues:
    Show a page called "My Errors"
    Display a table of [errors from "products", "stock levels"] with columns: source, kind, message, timestamp
    Allow filtering by kind
    This table subscribes to error changes
```

The `errors from` syntax is a Presentation-specific accessor that reads from the specified primitives' error Channels.

## Circuit Breaker Pattern

The combination of Channel Reflection, error handling, and State machines enables a declarative circuit breaker:

```
Channel called "vendor API":
  Carries orders
  Direction: outbound
  Delivery: reliable

State for "vendor API" called "circuit":
  Channel starts as "closed"
  Channel can also be "open", "half-open"
  A closed channel can become open if [error.count > 5 within 60 seconds]
  An open channel can become half-open after [60 seconds]
  A half-open channel can become closed if [healthCheck() == true]
  A half-open channel can become open if [healthCheck() == false]

On error from "vendor API" where [error.kind == "external"]:
  Retry 2 times with backoff
  Then escalate
  Log level: WARN
```

Note: The circuit breaker uses standard Termin primitives (State machine on a Channel, error handlers, Reflection) rather than a special-purpose circuit breaker construct. The pattern emerges from the composition.

## Runtime Contract for Error Handling

**`registerErrorHandler(primitiveRef, condition, actions) → void`**

Registers an error handler from the IR. The condition is a compiled JEXL expression (or null for catch-all). Actions are a sequence of typed recovery operations.

**`handleError(error, primitiveRef) → handled | escalate`**

When an error arrives at a primitive's error Channel:

1. Check registered handlers for the primitive, in declaration order
2. For each handler, evaluate the `where` condition against the error
3. First matching handler executes its actions
4. If the handler's actions include `escalate`, or if no handler matches, the error passes to the parent Boundary
5. Continue up the hierarchy until handled or reaching the global catch-all

**`executeRetry(originalOperation, retryConfig) → success | error`**

Re-executes the operation that produced the error. The retry config includes count, backoff strategy, and maximum delay.

---

# Appendix: Reflection Quick Reference

| Accessor | Returns | Example |
|---|---|---|
| `Content.reflect.schemas` | list of string | `["products", "stock levels"]` |
| `Content.reflect.count(name)` | whole number | `42` |
| `Content.reflect.schema(name).fields` | list of string | `["SKU", "name", "category"]` |
| `Compute.reflect.functions` | list of string | `["SayHelloTo", "calculateTotal"]` |
| `Compute.reflect.function(name).shape` | string | `"Transform"` |
| `Compute.reflect.function(name).canExecute(identity)` | boolean | `true` |
| `<name>.state.current` | string | `"active"` |
| `<name>.state.transitions` | list of string | `["discontinued"]` |
| `<name>.state.canTransition(target)` | boolean | `true` |
| `<name>.state.history` | list of transitions | `[{from: "draft", to: "active", at: ...}]` |
| `Channel.reflect.channel(name).state.current` | string | `"open"` |
| `Channel.reflect.channel(name).metrics.errors` | whole number | `3` |
| `Boundary.reflect.boundary(name).state.current` | string | `"active"` |
| `Boundary.reflect.boundary(name).scopes` | list of string | `["read orders", "write orders"]` |
| `Boundary.reflect.boundary(name).property(name)` | any | `150` |
| `Identity.reflect.role` | string | `"warehouse clerk"` |
| `Identity.reflect.scopes` | list of string | `["read inventory", "write inventory"]` |
| `Identity.reflect.hasScope(scope)` | boolean | `true` |
| `Identity.reflect.grants` | list of grants | `[{scope: "admin", expiresAt: ...}]` |
| `Event.reflect.event(name).subscribers` | whole number | `3` |
| `Event.reflect.event(name).lastEmitted` | datetime | `2026-04-03T...` |
