# Termin Application Server & Ecosystem v2

## 1. Boundary Types

A Boundary is the fundamental container in the Termin model. Every primitive lives inside a Boundary. Every Boundary has a path in the runtime's hierarchy. But not all Boundaries serve the same purpose.

### Applications

An Application is a Boundary that:

- Has **Presentation** — pages, navigation, user-facing interaction
- Has a **lifecycle** — can be started, paused, stopped, restarted; reports health
- Has **routing** — HTTP endpoints, REST API, WebSocket streams
- Is **deployable** — packaged as a `.termin.pkg`, deployed via CLI
- Has an **entry point** — visiting its URL renders its Presentation

An Application can contain sub-Boundaries for internal modularity. An inventory application might have a `receiving` sub-Boundary and a `reporting` sub-Boundary, each with their own Content, Compute, and State, composed through internal Channels. The Application Boundary is the unit of deployment. Its sub-Boundaries are structural, not independently deployable.

DSL declaration:

```
Application: Inventory Tracker
  Description: Track stock levels across warehouses
```

IR flag: `"boundary_type": "application"`

### Libraries

A Library is a Boundary that exports reusable definitions: Content schemas, Compute definitions, State machines, Identity configurations, Channel definitions. A Library has no Presentation, no lifecycle, no routing, and no entry point.

DSL declaration:

```
Library: Common CRM Schemas
  Description: Shared data formats for CRM interoperability
  Version: 2.1.0
```

IR flag: `"boundary_type": "library"`

Libraries are published to a registry as `.termin.pkg` packages (same format as Applications). Applications declare dependencies on Libraries. The compiler resolves those dependencies at compile time and includes the fully-resolved definitions in the Application's IR.

A Library can export any combination of primitives:

```
Library: Healthcare Identity
  Description: Standard roles, scopes, and confidentiality for HIPAA environments
  Version: 1.0.0

Scopes are "view patient records", "create patient records", "view all records",
  "administer patients", and "audit"

A "clinician" has "view patient records" and "create patient records"
A "administrator" has "view patient records" and "administer patients"
An "auditor" has "view all records" and "audit"

Content called "patient":
  Each patient has a name which is text, required
  Each patient has a date of birth which is date, required
  Each patient has a mrn which is unique text, required

State for patient called "care status":
  A patient starts as "registered"
  A patient can also be "active", "discharged", or "deceased"
  A registered patient can become active if the user has "administer patients"
  An active patient can become discharged if the user has "administer patients"
```

An Application references a Library:

```
Application: Clinic Manager
  Description: Patient intake and care tracking
  Uses: healthcare-identity version 1

Content called "visits":
  Each visit has a patient which references healthcare-identity.patient, required
  Each visit has a provider which is text, required
  Each visit has a reason which is text
```

### Configuration Boundaries

Configuration Boundaries are not declared in `.termin` files. They are part of the runtime's deployment topology — structural scaffolding that provides Identity configuration, confidentiality policies, and provider registrations to their descendants. Configuration Boundaries are defined in the runtime's own configuration:

```
/                           (runtime root)
├── corporate/              (configuration boundary — org-wide identity, policies)
│   ├── engineering/                (configuration boundary — org-level policies)
│   │   ├── team-alpha/     (configuration boundary — team-level grants)
│   │   │   ├── app-1/      (Application)
│   │   │   └── app-2/      (Application)
│   │   └── shared/         (Library registrations)
│   └── supply-chain/
│       └── inventory/      (Application)
└── runtime/                (runtime-level config)
```

The runtime administrator configures these boundaries. Applications are deployed into them. Libraries are registered within them. Policies cascade down from parent to child.

---

## 2. Package Format (.termin.pkg)

A `.termin.pkg` is a gzipped tar archive:

```
my-app.termin.pkg/
├── manifest.json
├── source/
│   └── app.termin
├── ir/
│   └── app.ir.json
├── assets/
├── compute/
│   └── *.js / *.py
├── LICENSE
└── NOTICE
```

### manifest.json

```json
{
  "pkg_version": "1.0",
  "name": "inventory-tracker",
  "version": "1.2.0",
  "boundary_type": "application",
  "description": "Track stock levels across warehouses",
  "termin_spec": "0.1.0",
  "compiler": "termin-compiler 0.1.0",
  "compiled_at": "2026-04-05T10:00:00Z",
  "source_hash": "sha256:abcdef...",
  "ir_hash": "sha256:123456...",
  "license": "MIT",
  "author": "Jamie-Leigh Blake <jl@getclarit.ai>",
  "dependencies": [
    {
      "name": "common-crm-schemas",
      "version": "^2.0.0",
      "type": "library"
    }
  ],
  "sub_boundaries": ["receiving", "reporting"],
  "properties_exposed": ["stock_levels", "daily_summary"],
  "channels_required": [
    {
      "logical_name": "supplier-feed",
      "schema": "common-crm-schemas.purchase_order",
      "direction": "inbound"
    }
  ],
  "providers_required": ["ai-agent"],
  "min_runtime": "0.1.0"
}
```

### Why include the source

The `.termin` source is typically 50–200 lines. The IR is 10–100x larger. Including source costs almost nothing and provides:

- **Auditability** — anyone can read what the application does in plain English
- **Reproducibility** — recompile from source with a different compiler version
- **Open ecosystem** — fork, modify, redistribute like open source code
- **AI legibility** — Seedling can read the source to understand the app it maintains

### Why include a license

The Termin ecosystem should be open-source-style from day one. Applications can be shared, forked, composed, and redistributed. The license file establishes terms. Termin's reference applications should ship under Apache 2.0.

---

## 3. Confidentiality System

### Design Principles

Confidentiality levels are **not hardcoded into the Termin spec.** The spec defines the *mechanism* — field-level tagging, boundary-scoped visibility, Channel enforcement. The *policy* — which levels exist, what each level means — is configured per-runtime.

Different deployments define different vocabularies:

- **Enterprise**: Public, Internal, Confidential, Restricted
- **Seedling** (small business): Public, Private
- **Healthcare deployment**: Public, Internal, PHI, Restricted PHI
- **Government deployment**: Unclassified, CUI, Secret, Top Secret

### Confidentiality Policy Configuration

The runtime (or a configuration boundary within the runtime) declares its confidentiality vocabulary:

```json
{
  "confidentiality_levels": [
    {
      "name": "public",
      "description": "Available to any user, authenticated or anonymous",
      "channel_flow": "unrestricted",
      "required_scopes": []
    },
    {
      "name": "private",
      "description": "Available only within the scoped boundary",
      "channel_flow": "scoped",
      "required_scopes": []
    },
    {
      "name": "sensitive",
      "description": "Requires explicit scope grant per Content type",
      "channel_flow": "scoped",
      "required_scopes": ["access_{content_type}_sensitive"]
    },
    {
      "name": "boundary-local",
      "description": "Never leaves the declaring boundary under any circumstances",
      "channel_flow": "none",
      "required_scopes": ["local_admin"]
    }
  ],
  "default_level": "private",
  "pii_descriptors": ["pii", "sensitive_pii", "financial", "health"]
}
```

A configuration boundary at `/corporate/` defines these levels for all descendant boundaries. Child boundaries can reference any level their ancestors define but cannot invent new levels — the vocabulary is controlled by the runtime or org administrator.

### Explicit Boundary Scoping

Confidentiality scope is always an explicit boundary path, never implicit resolution.

```
Content called "employee records":
  Scoped to /corporate/engineering/
  Each employee has a name which is text
  Each employee has a salary which is currency, scoped to ./
  Each employee has a department which is text
  Each employee has a ssn which is text, scoped to ./, confidentiality is sensitive
```

Path semantics:

| Path | Meaning |
|------|---------|
| `./` | This Boundary only. Data never leaves. |
| `../` | Parent Boundary and its children. |
| `/corporate/engineering/` | Everything within this absolute path. |
| `/` | Entire runtime (effectively public to authenticated users). |

The compiler validates scope paths:

- You can scope to `./`, any ancestor, or the ancestor's subtree
- You cannot scope to a sibling or cousin boundary (that's a ScopeGrant, not a scope declaration — the data owner doesn't unilaterally decide to share with a peer)
- Packages that hardcode absolute paths lose portability. Prefer relative paths (`./`, `../`) for content that should stay local regardless of deployment location. Use absolute paths only when the organizational structure is a known constant.

When the Content-level scope is declared (e.g., `Scoped to /corporate/engineering/`), individual fields inherit that scope by default. A field-level `scoped to ./` narrows it further. A field-level scope can never *widen* beyond its Content-level scope.

### Column-Level Confidentiality

Fields can override the Content-level default:

```
Content called "customers":
  Scoped to /corporate/engineering/
  Confidentiality is private
  Each customer has a name which is text
  Each customer has an email which is text, confidentiality is sensitive
  Each customer has a payment token which is text, scoped to ./, confidentiality is boundary-local
```

When this Content flows through a Channel:

- `name` — flows within `/corporate/engineering/` (inherits Content scope)
- `email` — flows within `/corporate/engineering/` only to recipients with `access_customers_sensitive` scope
- `payment_token` — **never transmitted.** `boundary-local` + `scoped to ./` means this field exists only within the declaring Boundary. Period.

### Row-Level Confidentiality

Row-level security uses `where [condition]` clauses on access rules:

```
Content called "medical records":
  Scoped to ./
  Each record has a patient which references patients, required
  Each record has a diagnosis which is text
  Each record has a provider which references providers, required
  Anyone with "view own records" can view records where [record.patient == identity.user_id]
  Anyone with "view all records" can view records
  Anyone with "create records" can create records where [record.provider == identity.user_id]
```

The `where` clause compiles to a filter predicate in the IR. The runtime evaluates it against the current Identity for every query. The storage adapter applies it server-side before returning results.

### PII Descriptors

Fields can carry PII descriptors independently of confidentiality level:

```
Each customer has an email which is text, pii
Each employee has a ssn which is text, sensitive_pii, scoped to ./
```

PII descriptors are metadata tags. The runtime's confidentiality policy defines what operational requirements each descriptor triggers (audit logging, encryption at rest, data retention policies, right-to-deletion support). The Termin spec defines the tagging mechanism. The runtime's policy defines the consequences.

### Channel Enforcement

When a Channel transmits Content across Boundaries:

1. The Channel enforcer resolves the recipient's boundary path
2. For each field, the enforcer checks: is the recipient's boundary within the field's declared scope path?
3. The enforcer checks: does the recipient's Identity have any required scopes for the field's confidentiality level?
4. Fields that fail either check are **stripped** from the payload silently — the recipient gets a clean subset, not an error
5. `boundary-local` fields are never transmitted through any Channel
6. Row-level filter predicates are applied server-side before transmission
7. PII-tagged fields trigger audit log entries when transmitted

### IR Representation

```json
{
  "content_name": "customers",
  "scope_path": "/corporate/engineering/",
  "confidentiality": "private",
  "fields": [
    {
      "name": "name",
      "type": "text",
      "scope_path": null,
      "confidentiality": null,
      "pii_descriptors": []
    },
    {
      "name": "email",
      "type": "text",
      "scope_path": null,
      "confidentiality": "sensitive",
      "pii_descriptors": ["pii"]
    },
    {
      "name": "payment_token",
      "type": "text",
      "scope_path": "./",
      "confidentiality": "boundary-local",
      "pii_descriptors": ["financial"]
    }
  ],
  "access_rules": [
    {
      "scopes": ["view_customers"],
      "operations": ["read"],
      "row_filter": null
    },
    {
      "scopes": ["view_own_customers"],
      "operations": ["read"],
      "row_filter": "record.owner == identity.user_id"
    }
  ]
}
```

---

## 4. Provider System

### The Problem

The Termin DSL defines what an application does. But some operations require external capabilities: calling an LLM, processing a payment, sending a Slack message, querying a third-party API. These capabilities aren't part of the Termin spec — they're integrations that vary by deployment.

### Compute Providers

A Compute Provider is a registered module that implements an external computation. The runtime defines the contract; the provider implements it.

#### Runtime Contract (Implementation Language)

```
ComputeProvider:
  name: string
  version: string
  supported_shapes: which compute shapes this provider can fulfill
  config_schema: what configuration the provider needs
  execute(input, config, identity) -> output
```

The `supported_shapes` declaration tells the runtime which compute shapes (Transform, Reduce, Expand, Correlate, Route) the provider can back. The compiler uses this to validate that an application's usage matches the provider's capabilities. If an application wires a provider into a Reduce position but the provider only supports Transform, the compiler rejects it.

The `execute` function receives:

- **input** — typed Content matching the compute node's declared input schema
- **config** — provider-specific configuration (model name, prompts, API endpoints)
- **identity** — the identity the compute is running under (caller, service, or delegate)

It returns typed Content matching the declared output schema, or an error that routes through TerminAtor.

#### Registration

Providers are registered at a specific boundary in the hierarchy. The registration includes the provider implementation and its configuration:

```
/corporate/                    (registers "stripe-payment" provider for all orgs)
/corporate/engineering/                (registers "ai-agent" provider with org-specific model config)
/corporate/engineering/team-alpha/     (registers "jira-sync" provider for this team only)
```

An application references a provider by name. The runtime resolves it by walking up the boundary tree from the application's boundary to find the nearest ancestor that registers a provider with that name. This is dependency injection, not security — provider resolution is about capability availability, not access control. If an application wants to reference a specific provider at a known boundary, it can use an explicit path:

```
Provider is /corporate/shared/ai-agent
```

#### DSL Syntax

```
Compute called "classify ticket":
  Provider is "ai-agent"
  Model is "claude-sonnet"
  Prompt: "Classify this ticket by severity: low, medium, high, critical"
  Input is the ticket
  Output adds priority to the ticket
```

The compiler infers the shape from the input/output declarations. One ticket in, one modified ticket out — that's a Transform. The author never writes `Shape is transform`. The compiler derives it and records it in the IR.

If provider-specific configuration fields (like `Model` and `Prompt`) don't match any reserved DSL keywords, they pass through to the provider's config object. The compiler validates them against the provider's declared `config_schema`.

### Channel Providers

Same pattern. A Channel Provider implements an external communication protocol.

#### Runtime Contract

```
ChannelProvider:
  name: string
  version: string
  protocol: string
  config_schema: what configuration the provider needs
  connect(config, identity) -> connection
  send(connection, payload) -> receipt
  receive(connection) -> payload
  disconnect(connection)
```

#### DSL Syntax

```
Channel called "order notifications":
  Provider is "slack"
  Workspace is "acme-corp"
  Destination is "#orders"
  On event "order placed" from orders:
    Send [order.summary]
```

```
Channel called "payment processing":
  Provider is "stripe"
  On event "checkout completed" from cart:
    Send [cart.payment_details]
```

#### Built-In Providers

Every conformant Termin runtime ships with these providers, requiring no registration:

**Compute:** CEL (the default — if no `Provider is` line appears, compute bodies are CEL expressions)

**Channels:** HTTP/REST, WebSocket, Internal (intra-boundary, zero-copy)

Everything else is a registered extension.

---

## 5. Package Portability and Deployment Bindings

### The Portability Principle

A `.termin.pkg` should be deployable to different positions in different boundary hierarchies. The same inventory app should work at `/corporate/engineering/team-alpha/inventory/` and at `/corporate/supply-chain/warehouse/inventory/`. This means packages cannot hardcode absolute paths to siblings, cousins, or peers.

### What Packages Can Reference

| Reference | Syntax | Resolution |
|-----------|--------|------------|
| Own boundary | `./` | Always valid |
| Own sub-boundaries | `./receiving/`, `./reporting/` | Always valid (declared in source) |
| Declared dependencies | `healthcare-identity.patient` | Resolved at compile time from manifest |
| Required channels | `supplier-feed` | Logical name, resolved at deployment |
| Required providers | `"ai-agent"` | Ancestor walk at runtime |

### Deployment Manifest

When deploying a `.termin.pkg` to a specific boundary, a deployment manifest binds logical names to concrete paths:

```json
{
  "package": "inventory-tracker-1.2.0.termin.pkg",
  "target_boundary": "/corporate/engineering/team-alpha/inventory/",
  "bindings": {
    "supplier-feed": "/corporate/supply-chain/purchasing/outbound-orders",
    "customer-data": "/corporate/engineering/shared/customer-db/customers"
  },
  "provider_overrides": {
    "ai-agent": "/corporate/engineering/ai-services/claude-agent"
  },
  "confidentiality_policy": "inherit"
}
```

The runtime reads the manifest, validates that all bindings resolve to real boundaries with compatible schemas, and wires the application's logical names to concrete paths. The application code never changes. The deployment configuration is specific to the target environment.

### Libraries and Versioning

Libraries are published to a registry:

```
GET /registry/packages/{name}/versions
GET /registry/packages/{name}/{version}/manifest.json
GET /registry/packages/{name}/{version}/package.termin.pkg
```

Semver compatibility:

- **Patch** (2.0.x): documentation fixes, no schema changes
- **Minor** (2.x.0): new optional fields, new Content types, new Compute definitions — no breaking changes
- **Major** (x.0.0): fields removed, types changed, required fields added — breaking

When two applications communicating through a Channel depend on different minor versions of the same library, the Channel enforcer uses the **intersection** of the two schemas. Fields present in only one version are silently dropped during transmission. Major version mismatches are rejected by the Channel enforcer at connection time.

For production deployments: registry backed by S3 + DynamoDB or PostgreSQL, behind the deployment's identity provider. For the public Termin ecosystem: a registry at `registry.termin.dev` or similar.

---

## 6. Identity Modes for Compute

Every Compute node executes under an identity. Three modes:

### Run as Caller (Default)

The Compute inherits the identity of whoever triggered it. If a user clicks a button that fires an Event that triggers a Compute, the Compute runs with that user's scopes. Every downstream operation (Content queries, State transitions, Channel sends) is evaluated against the caller's identity.

This is the default for user-facing synchronous operations. The Compute can only do what the user could do. No privilege escalation.

```
Compute called "update my profile":
  (no identity declaration — defaults to run as caller)
  Input is the profile update
  Output updates the user record
```

On cloud deployments: the Lambda or container assumes a role derived from the authenticated user's identity. Every downstream API call is made with the user's credentials.

### Run as Service

The Compute has its own declared service identity with its own scopes. For background operations, scheduled tasks, and autonomous agents.

```
Compute called "nightly reconciliation":
  Runs as service "reconciliation-bot"
  Service scopes are "view all orders" and "update order status"
  On event "midnight" from system:
    Reconcile order statuses against payment records
```

The service identity is registered with the Identity subsystem like any other identity. Its actions appear in the Event log attributed to `reconciliation-bot`. It can be audited, rate-limited, and revoked independently.

On cloud deployments: a service-linked IAM role with scoped permissions.

### Run as Delegate

The Compute runs with its service identity's *capabilities* but carries the triggering user's identity as *context*. The service can do things the user can't, but the audit trail records who initiated it.

```
Compute called "process user request":
  Runs as delegate "assistant-agent" on behalf of caller
  Service scopes are "view all tickets", "update tickets", and "call ai-agent"
  On event "user request" from inbox:
    Triage and respond to the user's tickets
```

The delegate mode gives the Compute the service identity's scopes (so it can call the AI provider, access tickets the user might not directly see) while recording the original caller for audit purposes. This is the `sudo` model.

On cloud deployments: IAM role assumption with session tags recording the original caller's identity.

**Which mode for agents?**

- An **autonomous agent** (monitors system health, runs nightly jobs) → Run as Service
- An **assistant agent** (acts on a user's behalf when triggered by the user) → Run as Delegate
- A **user-scoped computation** (form validation, calculations) → Run as Caller

---

## 7. System-Defined Functions

The runtime provides a standard library of CEL functions available in any expression without declaration:

**Aggregation:** `sum()`, `avg()`, `min()`, `max()`, `count()`

**Collection:** `map()`, `filter()`, `reduce()`, `sort()`, `flatten()`, `unique()`, `first()`, `last()`

**Temporal:** `now()`, `today()`, `daysUntil()`, `daysBetween()`, `addDays()`, `formatDate()`

**String:** `uppercase()`, `lowercase()`, `trim()`, `contains()`, `startsWith()`, `endsWith()`, `replace()`

**Math:** `round()`, `floor()`, `ceil()`, `abs()`, `clamp()`

**Identity:** `identity.user_id`, `identity.scopes`, `identity.has_scope()`, `identity.boundary_path`

**Reflection:** `reflect.content_types()`, `reflect.state_of()`, `reflect.channel_status()`

These are baked into the CEL expression evaluator. They are part of the runtime spec. The conformance test suite validates that every compliant runtime implements them correctly.

Custom Compute providers can register additional functions, but those functions are namespaced under the provider name and only available within Compute nodes that use that provider. System-defined functions are global.

---

## 8. AI Agents on the Termin Runtime

### Agents Are Compute Nodes

An AI agent is not a new primitive type. It is a Compute node backed by a registered AI Compute Provider. The provider implements the `ComputeProvider` contract. The agent's "intelligence" comes from the provider's implementation (calling an LLM). The agent's permissions, observability, and lifecycle come from the existing primitive model.

### Agent Tools = Runtime Primitives

The agent's tools, in the LLM tool-calling sense, are exactly the runtime's existing subsystem interfaces:

| Tool | Maps To | What It Does |
|------|---------|--------------|
| `content.query(type, filters)` | Content subsystem | Query any Content the agent's identity can access |
| `content.create(type, record)` | Content subsystem | Create a record if scopes permit |
| `content.update(type, id, changes)` | Content subsystem | Update a record |
| `state.current(type, id)` | State subsystem | Check a record's state machine position |
| `state.transition(type, id, target)` | State subsystem | Trigger a transition if scopes permit |
| `event.create(name, payload)` | Event subsystem | Fire an Event |
| `channel.send(name, payload)` | Channel subsystem | Send through a Channel |
| `reflect.*` | Reflection | Any Reflection query |
| `termin.shell(expression)` | Expression Evaluator | Evaluate arbitrary CEL |

The agent doesn't get a special API. It gets the same API that `termin shell` uses, that the dashboard app uses through Reflection, that any Compute node uses internally. The security model doesn't change. The observability model doesn't change.

### Agent Observability

Because agents operate through standard primitives:

- Every Content query the agent makes is logged
- Every State transition the agent triggers is recorded in the Event log
- Every Channel message the agent sends is observable
- Every error the agent encounters routes through TerminAtor
- Another agent (or Seedling) can monitor this agent's behavior through Reflection

An agent is not a black box. It's a Compute node whose internal decision-making happens to involve an LLM, but whose external behavior is entirely expressed through auditable primitive operations.

### Agent Self-Discovery

An agent with access to `reflect.*` can explore the runtime:

```
> reflect.content_types()
["orders", "customers", "inventory"]

> reflect.state_of("orders", 42)
{ "state_machine": "order_lifecycle", "current": "processing" }

> reflect.channel_status("supplier-feed")
{ "connected": true, "messages_today": 247, "errors": 0 }

> reflect.boundaries()
["./receiving", "./reporting"]
```

The agent builds a model of the system it inhabits using the same Reflection system the admin dashboard uses. It can discover what Content types exist, what State machines are attached, what Channels are available, what other Compute nodes are running, and act on that understanding.

### Agents Deploying Applications

An agent with:

- A service identity that has "deploy" scope for a target boundary
- Access to the compiler (locally or as a registered Compute provider)
- Access to the runtime's package registry

...can write a `.termin` file, compile it, package it, and deploy it to its permitted boundary. This is how Seedling works. But it's not limited to Seedling — any agent registered at an appropriate boundary can do this.

A team-level "builder-agent" could monitor a Slack channel, hear someone say "I need a tool that tracks our on-call rotation," write the `.termin`, compile it, deploy it to the team's boundary, and respond with the URL. The agent's deployment scope limits where it can put things. It can't deploy to `/corporate/` unless its service identity has that scope.

### Compute Shape for Agents

Agents don't need a new compute shape. An agent invocation is a **Transform**: one event in, one set of actions out. The "long-lived" quality of an agent is an illusion created by the State subsystem preserving context between invocations. Each invocation is stateless — the agent reads its state from the State subsystem, makes decisions, writes updated state back. This is the Lambda model. No new shape needed.

```
Compute called "support triage":
  Provider is "ai-agent"
  Runs as service "triage-bot"
  Service scopes are "view tickets", "update tickets", and "create events"
  Model is "claude-sonnet"
  Prompt: "You are a support triage agent. Classify incoming tickets."
  On event "ticket created" from tickets:
    Read the ticket content
    Classify by severity
    Update the ticket priority
    If priority is "critical", create event "critical alert"
```

The compiler infers: one event in (ticket created), actions out (update + conditional event creation). Shape: Transform. Provider: ai-agent. Identity: service "triage-bot". Everything else is standard Termin.

### DSL Variations for AI

Not every AI integration needs a full agent. The DSL should support a spectrum:

**Simple LLM call** (stateless, one-shot):

```
Compute called "summarize feedback":
  Provider is "ai-agent"
  Model is "claude-haiku"
  Prompt: "Summarize this customer feedback in one sentence"
  Input is the feedback text
  Output is the summary text
```

**Autonomous agent** (stateful, event-driven, self-directed):

```
Compute called "system health monitor":
  Provider is "ai-agent"
  Runs as service "health-agent"
  Service scopes are "view all content", "view all channels", "create events", and "reflect"
  Model is "claude-sonnet"
  Prompt: "Monitor system health. Alert on anomalies. Suggest optimizations."
  On event "health check interval" from system:
    Query reflect.channel_status for all channels
    Query reflect.content_metrics for all content types
    If anomalies detected, create event "health alert" with details
```

The difference is scope and triggers, not a different primitive type.

---

## 9. Competitive Analysis

### Termin vs. Industry Comparables

| Dimension | **Termin** | **Wasp** | **Darklang** | **Ballerina** | **Mendix / OutSystems** | **Retool / Appsmith** |
|-----------|-----------|----------|------------|-------------|----------------------|---------------------|
| **Category** | Declarative application platform | Declarative full-stack framework | Deployless backend language | Cloud-native programming language | Enterprise low-code platform | Internal tool builder |
| **Core thesis** | Business intent compiles to secure applications. Security is topological. | Reduce full-stack boilerplate via DSL generating React + Node + Prisma | Eliminate deployment and infrastructure from backend dev | Network-aware type system for cloud integration | Visual drag-and-drop for rapid enterprise apps | Pre-built components + data connectors for internal dashboards |
| **DSL / Language** | External DSL — English-like, ~80 reserved words, zero programming syntax. Expressions use CEL. | External DSL + JS/TS for business logic. Config-like syntax for wiring. | Full functional language (F#-based). Statically typed, immutable. | Full language (C-like). Network primitives in type system. | Proprietary visual modeling language. | No DSL — JS/SQL in drag-and-drop IDE. |
| **Who writes** | AI (Seedling) or a PM | Developers (React/Node) | Developers learning a new language | Developers (Java/Go background) | Citizen devs (visual) + pro devs | Developers (SQL + JS) |
| **Security model** | Structural: Boundaries enforce isolation, Channels enforce data flow, Identity scopes gate every operation, CEL sandbox prevents injection | Conventional: developer-managed, auth built-in | Immutability helps, no structural guarantees | Taint checking, type-safe network calls | Platform RBAC, enterprise certs (SOC 2, ISO 27001) | Component RBAC, SQL injection possible |
| **State machines** | First-class, scope-gated transitions | Manual (implement in JS) | None | Planned (workflow support) | Visual process modeler | None |
| **Multi-app composition** | Application Server with typed, scoped Channels and boundary-path namespace | Single-app model | Single-app model | Network primitives but separate runtimes | App marketplace, inter-app via APIs | Separate apps via APIs |
| **Extensibility** | Registered Compute and Channel providers at any boundary level | npm packages | Standard library | Module system | Marketplace extensions | Plugin ecosystem |
| **AI integration** | First-class: AI agents are Compute nodes with runtime tools, agents can deploy apps | "Perfect for AI" — DSL provides guardrails | Exploring AI code generation | None | AI-assisted development (suggestions) | AI-assisted query writing |
| **Data confidentiality** | Extensible per-runtime policy, explicit boundary-path scoping, column + row level, PII tagging | None (developer responsibility) | None | None | Platform-managed | None (developer responsibility) |
| **Open source** | Yes, Apache 2.0 (planned) | Yes, MIT | Yes, Apache 2.0 (recent) | Yes, Apache 2.0 | No, proprietary | Appsmith: open core. Retool: proprietary. |
| **Vendor lock-in** | None. Open spec, portable packages, pluggable providers. | Low. Eject to standard React/Node. | Medium (classic was hosted-only). Lower now. | Low. JVM bytecode. | High. No code export. | Appsmith: low. Retool: medium. |
| **Cost** | Free runtime. Seedling ~$25/mo. Enterprise: internal.| Free + standard cloud hosting | Free + cloud TBD | Free. Choreo $150/component/mo. | $50K–$700K+/year | $5–$50/user/month |
| **Maturity** | Pre-release. Working prototype. | Production. YC W21. | Beta. Recently reorganized. | Production. Fortune 500 users. | Mature. Gartner Leaders. | Mature. Widely adopted. |

### What Termin Does That Nobody Else Does

1. **Security as topology.** Every other platform adds security as a layer. In Termin, security is a structural consequence of Boundaries, Channels, and Identity. You can't build an insecure app because the primitive model doesn't permit insecure data flow.

2. **AI-first authorship.** The DSL exists for AI legibility and human auditability, not developer productivity. Seedling writes, deploys, and maintains applications. The human is the commissioner, not the coder.

3. **Application mesh.** No comparable platform offers a shared runtime where independently-developed applications compose through typed, scoped, schema-validated Channels with boundary-path namespace resolution.

4. **Extensible confidentiality.** Runtime-configured policy, explicit boundary-path scoping, column/row security, PII tagging — all as structural guarantees enforced by the Channel system.

5. **Agent runtime.** AI agents as first-class Compute nodes with standard runtime tools, full observability through Reflection, and the ability to deploy new applications. Not a chatbot bolted on — an agent that inhabits the application fabric.

---

## 10. The Coolest 50-Line Termin Application

A **neighborhood mutual aid board** — post what you need, claim what you can give. Real-time updates, state machine workflows, role-based access. 49 lines.

```termin
Application: Neighbor Net
  Description: Mutual aid board — post what you need, claim what you can give

Users authenticate with email link
Scopes are "post needs", "claim needs", and "moderate"

A "neighbor" has "post needs" and "claim needs"
A "moderator" has "post needs", "claim needs", and "moderate"

Content called "needs":
  Scoped to ./
  Each need has a what which is text, required
  Each need has a category which is one of: "groceries", "ride", "childcare", "pet care", "repair", "meal", "other"
  Each need has a neighborhood which is text, required
  Each need has a needed by which is date
  Each need has a posted by which is text, required
  Each need has a claimed by which is text
  Each need has a notes which is text
  Anyone with "post needs" can create needs
  Anyone with "claim needs" can view needs
  Anyone with "moderate" can update or delete needs

State for needs called "aid cycle":
  A need starts as "open"
  A need can also be "claimed", "in progress", "fulfilled", or "expired"
  An open need can become claimed if the user has "claim needs"
  A claimed need can become in progress if the user has "claim needs"
  An in progress need can become fulfilled if the user has "post needs"
  An open need can become expired if the user has "moderate"
  A claimed need can become open if the user has "moderate"

As a neighbor, I want to see what my community needs so that I can help:
  Show a page called "Board"
  Display a table of needs with columns: what, category, neighborhood, needed by, status, posted by
  Highlight rows where [needed_by <= today() and status == "open"]
  Allow filtering by category, neighborhood, and status
  This table subscribes to needs changes

As a neighbor, I want to ask for help so that I can get what I need:
  Show a page called "Ask for Help"
  Accept input for what, category, neighborhood, needed by, and notes

As a moderator, I want to see community health so that I can keep things running:
  Show a page called "Dashboard"
  Display total need count with open vs fulfilled breakdown

Navigation bar:
  "Board" links to "Board" visible to all
  "Ask" links to "Ask for Help" visible to neighbor, moderator
  "Dashboard" links to "Dashboard" visible to moderator

Expose a REST API at /api/v1:
  GET /needs lists needs
  POST /needs creates a need
  PUT /needs/{id} updates a need
  POST /needs/{id}/claim transitions to claimed
  POST /needs/{id}/fulfill transitions to fulfilled

Stream need updates at /api/v1/stream
```

In 49 lines of plain English, this defines: authentication, authorization, a data model with 8 typed fields, a 5-state workflow with 6 scope-gated transitions, three pages with real-time updates and conditional highlighting, a REST API with state transition endpoints, a WebSocket stream, and scoped navigation.

A PM describes this to Seedling. Seedling writes the 49 lines. The compiler produces the IR. Seedling deploys the `.termin.pkg`. A neighbor opens a browser, signs in with their email, posts that they need someone to walk their dog Thursday. Another neighbor sees it appear in real-time, claims it, walks the dog, and the first neighbor marks it fulfilled.

Infrastructure cost: ~$15/month on Fargate.
Time from idea to running app: minutes.
Lines of code a human wrote: zero.

---

## Open Items for Future Specs

- **Package deployment manifest format** — full schema for binding logical names to concrete boundary paths
- **Compute Provider SDK** — developer guide for implementing custom providers in Python, JavaScript, Rust
- **Channel Provider SDK** — same for custom channel integrations
- **Runtime configuration schema** — how to declare configuration boundaries, confidentiality policies, provider registrations
- **Boundary lifecycle management** — how configuration boundaries are created, modified, decommissioned by runtime administrators
- **Agent prompt management** — versioning, testing, and rollback for agent prompts
- **Federated registry protocol** — how registries discover and sync with each other across organizational boundaries
- **Conformance test suite for providers** — how to validate that a custom provider correctly implements the contract
- **Offline support and conflict resolution** — client-side caching, merge strategies for eventually-consistent Content
- **Visual editor approach** — how a graphical IDE maps to the DSL and IR
