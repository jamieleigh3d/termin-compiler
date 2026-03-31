# Termin Primitives

**Version:** 0.1.0-draft
**Status:** Formative — seeking feedback on completeness and composability

---

## Overview

A Termin application is a directed graph. The nodes are primitives. The edges are Channels. The containers are Boundaries. The type system is Content.

This document defines the eight Termin primitives, their roles in the graph, and their composition rules.

---

## Graph Structure

Every Termin application can be described as three kinds of things:

**Nodes** — the operational primitives that do work. Compute, State, Events, Presentation, and Identity are nodes. They live inside Boundaries. They receive input through Channels and produce output through Channels.

**Edges** — Channels. Every connection between nodes is a Channel. Every Channel carries Content conforming to a declared schema. Channels are the only mechanism by which information crosses a Boundary. There are no backdoors, no direct calls, no ambient access.

**Containers** — Boundaries. A Boundary is a rectangle around a set of nodes. Boundaries are recursive — a Boundary can contain other Boundaries. The innermost Boundary wraps a single Compute node. The outermost Boundary is the Termin platform itself. Application and module boundaries sit between these extremes. There is no fundamental distinction between boundary types. A Boundary is a Boundary. What varies is scope and the security policy attached to it.

In a visual editor, this looks like: rectangles (Boundaries) containing shapes (nodes), connected by arrows (Channels). You can zoom into any Boundary and see its internal graph. You can zoom out and see the Boundary as a single node with input and output Channels.

---

## The Primitives

### Content

**Role in the graph:** The type system. Content is not a node or an edge — it is the schema language that types everything else.

A Content declaration defines the shape of data: fields, types, constraints, references, and access rules. Content schemas type the Channels (every Channel carries Content conforming to a declared schema), type the Compute inputs and outputs, type the State data, and type the Event payloads.

Content is data at rest when stored, and data in motion when carried by a Channel. The schema is the same in both cases. A Content declaration simultaneously defines validation rules, encryption configuration, and access control policy.

```
Content called "products":
  Each product has a SKU which is unique text, required
  Each product has a name which is text, required
  Each product has a category which is one of: raw material, finished good, packaging
  Anyone with "read inventory" can view products
  Anyone with "write inventory" can create or update products
```

**Key constraint:** Content access is always parameterized. Arbitrary query construction is not expressible. This is the structural basis for injection prevention.

---

### Compute

**Role in the graph:** Transformation nodes. Compute is where work happens.

A Compute node has N input Channels and M output Channels. Each Channel carries Content conforming to a declared schema. The Compute node transforms input Content into output Content. In its pure form, Compute is stateless and side-effect-free — Content in, Content out.

A Compute node is always wrapped in an innermost Boundary. It cannot access the filesystem, the network, or any resource outside its declared input and output Channels. If a Compute node needs to reach an external system, that communication routes through a Channel that crosses a Boundary. This is not a rule bolted onto Compute — it is a consequence of the architecture. Compute lives inside a Boundary. Only Channels cross Boundaries.

Termin defines six Compute shapes based on the cardinality and schema relationships of input and output:

| Shape | Input | Output | Archetype |
|---|---|---|---|
| **Transform** | 1 record, schema A | 1 record, schema B (or A) | map, convert, validate, enrich |
| **Reduce** | N records, schema A | 1 record, schema B | aggregate, summarize, fold |
| **Expand** | 1 record, schema A | N records, schema B | decompose, generate, unfold |
| **Correlate** | N records, schema A + M records, schema B | K records, schema C | join, match, reconcile |
| **Route** | 1 record, schema A | 1 record, one of B₁ ... Bₙ | classify, triage, branch |
| **Chain** | ordered sequence of any of the above | | pipeline, flow, saga |

Built-in verbs (query, create, update, delete, transition) are Compute nodes provided by the runtime. Custom Compute functions extend the set. Custom Compute is the escape hatch — anything the compiler cannot verify the shape of. The review requirement for custom Compute flows from that lack of verifiability.

**Reduce** has a sub-dimension: commutative (order-independent, e.g., sum) vs. ordered (sequence-dependent, e.g., running balance).

**Correlate** implies the Compute has a window — it sees across a set of records, not a single record. This is where joins, group-bys, and cross-reference operations live.

**Route** produces one output, but the output type varies based on the data. This is the classification/branching primitive.

**Chain** preserves intermediate schemas as observability points. Each step in a Chain emits Content conforming to a declared schema that can be inspected, validated, branched on, or replayed from. A Chain is not merely syntactic sugar for function composition — it makes the intermediate states visible.

---

### Channels

**Role in the graph:** Edges. Channels are the connective tissue of the entire system.

A Channel is a typed, authenticated, schema-validated communication path. Every Channel declaration specifies:

- The **Content schema** it carries
- The **source Boundary** and **destination Boundary** it connects
- The **Identity requirements** for both endpoints
- The **protocol** (REST, WebSocket, SSE, webhook, pub/sub, internal)

Channels are the only mechanism by which information crosses a Boundary. This is the single most important architectural constraint in Termin. It means:

- Compute cannot reach the network (SSRF prevention is structural, not configured)
- Cross-application communication is always declared, typed, and authenticated
- Every data flow is visible in the graph — there are no hidden dependencies
- The attack surface of any Boundary is exactly its declared inbound Channels

A Channel between two nodes within the same Boundary is an **internal Channel** — it carries typed Content but doesn't require authentication because both endpoints share a security scope. A Channel that crosses a Boundary is an **external Channel** — it requires authentication at both endpoints and schema validation on every payload.

The outermost Boundary (the Termin platform) has Channels that connect to the outside world. These are the application's external API, webhooks, integrations, and user-facing interfaces. The security properties of these Channels are enforced by the runtime.

In a visual editor, Channels are the arrows. They always connect two things. They always carry a declared schema. They always respect the Boundary they cross.

---

### Boundaries

**Role in the graph:** Containers. Boundaries define scope, isolation, and security policy.

A Boundary is a rectangle that contains nodes and possibly other Boundaries. Boundaries are recursive and uniform — there is no fundamental difference between a Boundary around a single Compute node, a Boundary around an application module, a Boundary around an entire application, and the Termin platform Boundary. They all follow the same rules:

- Everything inside a Boundary shares a security scope
- The only way in or out of a Boundary is through a declared Channel
- Security policy is attached at the Boundary layer

The Boundary hierarchy from innermost to outermost:

| Level | Contains | Example |
|---|---|---|
| Compute Boundary | A single Compute node and its input/output Channel declarations | A barcode scanning function |
| Module Boundary | A set of related nodes and their internal Channels | The inventory management module |
| Application Boundary | A set of modules and their inter-module Channels | The warehouse management application |
| Platform Boundary | All applications and their external Channels | The Termin deployment |

From outside, a Boundary looks like a single node with input and output Channels. You don't need to know what's inside. From inside, a Boundary is a graph of nodes and Channels. This is the encapsulation property — Boundaries hide internal structure while exposing a typed interface.

**Security scoping:** Identity, access control, and enforcement policy are defined at the Boundary level. A Boundary declares what scopes exist within it, what roles map to those scopes, and what Content is accessible to each scope. Nested Boundaries inherit the outer Boundary's Identity context but can further restrict it.

---

### State

**Role in the graph:** Lifecycle nodes. State governs how Content transitions through a declared set of phases.

A State node is a finite state machine attached to a Content schema. It declares:

- The set of valid states (e.g., draft, active, discontinued)
- The valid transitions between states (e.g., draft → active)
- The conditions for each transition (e.g., "if the user has write inventory")
- The initial state for new Content

Every transition is explicit. The runtime rejects any transition not declared in the State node. There are no implicit state changes, no undeclared transitions, no orphan states.

State nodes receive input through Channels (transition requests) and produce output through Channels (transition events). A transition request that violates the State machine is rejected at the runtime layer — it never reaches the Content.

```
State for products called "product lifecycle":
  A product starts as "draft"
  A draft product can become active if the user has "write inventory"
  An active product can become discontinued if the user has "admin inventory"
  A discontinued product can become active again if the user has "admin inventory"
```

---

### Events

**Role in the graph:** Signal nodes. Events connect primitives reactively without coupling them directly.

An Event is a signal that something happened. Events carry a Content payload conforming to a declared schema. Events travel through Channels — they follow the same rules as all other data in the system.

An Event declaration specifies:

- The **trigger condition** (what happened)
- The **payload schema** (what data the event carries)
- The **output Channel** (where the event goes)

Events are auditable and tamper-evident. The runtime logs every Event with its payload, timestamp, and the Identity that caused it.

```
When a stock level is updated and its quantity is at or below its reorder threshold:
  Create a reorder alert with the product, warehouse, current quantity, and threshold
```

Events decouple the trigger from the response. The node that emits an Event does not know or care what consumes it. This is how reactive behavior emerges from the graph without creating hidden dependencies.

---

### Identity

**Role in the graph:** The authentication and authorization substrate. Identity is scoped at the Boundary layer and enforced by the runtime.

Every Channel invocation, every Content access, every State transition, and every Compute execution occurs in the context of an Identity. There is no anonymous access. There is no way to bypass Identity enforcement from within a Termin application.

An Identity declaration specifies:

- The **authentication mechanism** (pluggable — OAuth, SAML, SSO, API key, etc.)
- The **scopes** available within a Boundary (e.g., "read inventory", "write inventory")
- The **roles** that map to sets of scopes (e.g., "warehouse clerk" has "read inventory" and "write inventory")

Identity bindings are provided by the deployment environment, not by Termin itself. Termin defines the Identity interface — what Identity must provide (authenticated principal, scopes, role resolution). The deployment provides the implementation (Okta, Auth0, SAML, LDAP, etc.).

```
Users authenticate with [deployment-specific binding]
Scopes are "read inventory", "write inventory", and "admin inventory"

A "warehouse clerk" has "read inventory" and "write inventory"
A "warehouse manager" has "read inventory", "write inventory", and "admin inventory"
```

**Key constraint:** Identity is enforced at the runtime layer. Applications declare scopes and roles. The runtime enforces them on every access path — API, UI, events, internal Channels. An application cannot override, disable, or circumvent Identity enforcement.

---

### Presentation

**Role in the graph:** Interface nodes. Presentation makes the graph visible and interactive to humans.

A Presentation node describes how Content becomes a user interface. Presentation is specified through user stories that declare what a user with a given role should see and do.

Presentation nodes consume Content through input Channels and produce rendered interfaces. The rendering layer only shows elements that the current Identity has access to — visibility is a consequence of the Content access rules and Identity scopes, not a separate permission system.

```
As a warehouse clerk, I want to see all products and their current stock levels
  so that I know what we have on hand:
    Show a page called "Inventory Dashboard"
    Display a table of products with columns: SKU, name, category, status
    Highlight rows where quantity is at or below reorder threshold
    Allow filtering by category, warehouse, and status
```

Presentation is the outermost visible layer of an application, but in the graph it follows the same rules as everything else — it receives typed Content through Channels and operates within a Boundary.

---

## Composition Rules

1. **All data flows through Channels.** There are no implicit connections, ambient access, or hidden dependencies.
2. **All Channels carry typed Content.** The compiler verifies that every Channel's source produces Content conforming to the declared schema and every Channel's destination consumes Content conforming to the same schema.
3. **All Boundary crossings go through Channels.** A node inside a Boundary cannot reach anything outside that Boundary except through a declared Channel.
4. **Identity is inherited inward and restrictable.** A nested Boundary inherits its parent's Identity context. It can further restrict access (narrower scopes) but cannot widen it (grant scopes the parent doesn't have).
5. **Security policy attaches to Boundaries.** Scopes, roles, access rules, and enforcement policy are declared at the Boundary level, not at individual nodes.
6. **Boundaries are opaque from outside.** The internal graph of a Boundary is not visible to anything outside it. Only the Boundary's declared input and output Channels are visible.
7. **The graph is fully inspectable.** Every node, Channel, Boundary, Content schema, Identity scope, State machine, and Event declaration is visible to the compiler. There are no runtime-only constructs that escape static analysis.

---

## Exhaustiveness

The eight primitives decompose business applications along eight dimensions:

| Primitive | Dimension | What it adds |
|---|---|---|
| Content | Shape | Typed, structured data — the noun system |
| Compute | Transformation | Typed functions — the verb system |
| Channels | Connection | Typed, authenticated edges — the connective tissue |
| Boundaries | Scope | Recursive containers — isolation and security policy |
| State | Lifecycle | Declared phase transitions — temporal progression |
| Events | Reactivity | Decoupled signals — cause and effect without coupling |
| Identity | Ownership | Who is acting — authentication and authorization |
| Presentation | Visibility | How the graph becomes an interface — the human layer |

**Open question:** Is this set exhaustive? Are there dimensions of business application behavior that none of these primitives cover? If you can describe a business application requirement that cannot be expressed as a composition of these eight primitives, that is valuable feedback.
