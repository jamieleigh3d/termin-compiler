# Termin Runtime Implementer's Guide

**Version:** 0.8.0
**Date:** April 2026
**Companion to:** `termin-ir-schema.json` (JSON Schema draft 2020-12)

---

## Purpose

This guide is for developers building a Termin runtime — a system that reads a compiled Termin IR (AppSpec JSON) and runs it as a working application. The IR is the contract between the Termin compiler and any conforming runtime. If you can consume the JSON Schema, you can build a runtime in any language.

The reference runtime is `termin_runtime` (Python, FastAPI, SQLite). This guide documents the semantic contracts that any runtime must honor, regardless of implementation language or technology stack.

---

## 1. Architecture Overview

A Termin application compiles to a single JSON file conforming to `termin-ir-schema.json`. The runtime reads this JSON and produces a running application with:

- **Persistent storage** for each Content schema
- **HTTP API** for CRUD operations with scope-checked access control
- **State machine enforcement** for lifecycle transitions
- **Event processing** for reactive side-effects
- **Presentation rendering** for UI pages (component trees)
- **Real-time subscriptions** for live data updates
- **Identity resolution** for role/scope-based access

```
                    ┌──────────────┐
  .termin file ───► │   Compiler   │ ───► AppSpec JSON
                    └──────────────┘
                                            │
                    ┌──────────────┐         ▼
                    │   Runtime    │ ◄─── reads JSON
                    │              │
                    │  ┌─Storage─┐ │
                    │  ┌─API─────┐ │
                    │  ┌─State───┐ │
                    │  ┌─Events──┐ │
                    │  ┌─UI──────┐ │
                    │  ┌─Auth────┐ │
                    └──────────────┘
```

---

## 2. Content and Storage

### 2.1 Schema Mapping

Each entry in `content[]` becomes a persistent storage table.

**Implicit columns** (not in the IR, added by the runtime):
- `id` — auto-increment integer primary key
- `status` — TEXT column, only when `has_state_machine` is `true`

**Field type mapping:**

| `column_type` | SQL Type | Notes |
|---|---|---|
| `TEXT` | TEXT / VARCHAR | Default |
| `INTEGER` | INTEGER | For `whole_number`, `reference` (FK) |
| `REAL` | REAL / FLOAT | For `currency`, `number`, `percentage` |
| `BOOLEAN` | BOOLEAN / INTEGER | Platform-dependent |
| `DATE` | TEXT / DATE | ISO 8601 date string |
| `TIMESTAMP` | TEXT / TIMESTAMP | ISO 8601 datetime string |
| `JSON` | TEXT / JSON | For `list` types, stored as JSON array |

### 2.2 Business Type Semantics

The `business_type` field carries semantic meaning beyond storage. Runtimes should use it for:

| `business_type` | Validation | Formatting | UI Widget |
|---|---|---|---|
| `text` | String | As-is | Text input |
| `whole_number` | Integer, >= minimum, <= maximum | Locale number | Number input (step=1) |
| `number` | Float | Locale number | Number input |
| `currency` | Float >= 0 | Currency symbol + 2 decimals | Number input (step=0.01) |
| `percentage` | Float 0-100 (or 0-1) | X% | Number input + % |
| `date` | ISO 8601 date | Locale date | Date picker |
| `datetime` | ISO 8601 datetime | Locale datetime | Datetime picker |
| `automatic` | System-managed | Locale datetime | Read-only (excluded from forms) |
| `boolean` | true/false | Yes/No | Checkbox |
| `enum` | Value in `enum_values` | As-is | Dropdown select |
| `reference` | Valid ID in `foreign_key` target | Display col from target | Lookup selector |
| `list` | JSON array of `list_type` items | Comma-separated | Multi-value input |

### 2.3 Foreign Keys

When `foreign_key` is set (non-null), the field is a reference to another Content's `id` column:

```json
{
  "name": "parent_order",
  "business_type": "reference",
  "column_type": "INTEGER",
  "foreign_key": "orders"
}
```

The runtime should:
1. Create a foreign key constraint (or validate referential integrity)
2. In forms, render a lookup selector showing records from the target Content
3. Support cascading behavior appropriate to the platform

### 2.4 Enum Fields

When `enum_values` is non-empty, the field only accepts those values:

```json
{
  "name": "priority",
  "business_type": "enum",
  "enum_values": ["low", "medium", "high"]
}
```

The runtime must reject values not in the enum list on create and update.

### 2.5 Initial State

When `has_state_machine` is `true`, new records must be created with `status` set to `initial_state`. The runtime must not allow clients to set the initial status directly — it is system-assigned.

### 2.6 Default Expressions

When `default_expr` is set (non-null), the runtime evaluates it at record creation time for fields not provided by the caller.

```json
{ "name": "submitted_by", "default_expr": "User.Name" }
{ "name": "priority", "default_expr": "\"normal\"" }
{ "name": "count", "default_expr": "0" }
{ "name": "created_at", "default_expr": "now" }
```

The evaluation context must include:
- **`User`** — the standard User identity object (see § 3.2)
- **`now`** — current UTC timestamp as ISO 8601 string
- **`today`** — current date as ISO 8601 string

DSL authors write `defaults to [User.Name]` (CEL expression) or `defaults to "literal"` (literal string). The compiler normalizes both into a CEL expression string in `default_expr`. Literals become CEL string literals: `"normal"` in DSL becomes `'"normal"'` in the IR.

**Evaluation rules:**
1. Only evaluate on **create**, not update
2. Only populate fields **not provided by the caller** (caller values take precedence)
3. If evaluation fails (expression error), skip the default silently
4. `is_auto` fields (like `automatic` timestamps) may also use `default_expr` — the runtime should apply `default_expr` even for auto fields when they carry an explicit expression

---

## 3. Access Control

### 3.1 Identity Model

Termin uses a scope-based access model. Scopes are flat strings (not hierarchical). An identity is a set of scopes, typically obtained via a role.

```json
{
  "auth": {
    "provider": "stub",
    "scopes": ["read orders", "write orders", "admin orders"],
    "roles": [
      { "name": "order clerk", "scopes": ["read orders", "write orders"] },
      { "name": "order manager", "scopes": ["read orders", "write orders", "admin orders"] }
    ]
  }
}
```

**Scope resolution:** The runtime resolves the current user's role to a set of scopes, then checks those scopes against the `required_scope` on routes, pages, transitions, and grants.

**The `stub` provider** is for development. It presents a role picker UI and stores the selection in a cookie. Production runtimes should implement real identity providers (SSO, OIDC, SAML) but the scope-checking logic remains the same.

### 3.2 The User Object

Every auth provider must produce a standard `User` object available in CEL expressions. This is the identity contract between the auth system and the expression evaluator.

| Field | Type | Description |
|---|---|---|
| `User.Username` | string | Login identifier. `"anonymous"` for unauthenticated users. |
| `User.Name` | string | Display name. `"Anonymous"` fallback. |
| `User.FirstName` | string | First name, derived from Name if provider doesn't supply it. |
| `User.Role` | string | Current role name. |
| `User.Scopes` | array | Scopes granted by the current role. |
| `User.Authenticated` | boolean | `true` if the user has a real identity, `false` for anonymous. |

**PascalCase convention:** System objects in CEL use PascalCase (`User.Name`, `User.Role`). Data fields use snake_case (`item.submitted_by`). This is intentional — it visually distinguishes system-provided values from row data. The DSL is case-insensitive, but CEL inside `[brackets]` is case-sensitive.

**No dedicated email field:** The standard User object does not include a `User.Email` field. Email is PII, provider-dependent, and not needed for runtime operations. Applications that need email should declare it as a Content field. Note: `User.Username` may be an email address (many auth providers use email as the login identifier) — the runtime treats it as an opaque string regardless of its format.

### 3.3 Access Grant Enforcement

Each `AccessGrant` says: "identity holding scope X may perform verbs Y on Content Z."

```json
{ "content": "orders", "scope": "write orders", "verbs": ["CREATE", "UPDATE"] }
```

On every API request, the runtime must:
1. Resolve the caller's identity to scopes
2. Find the AccessGrant matching the Content and verb
3. Check that the caller holds the required scope
4. Reject with 403 if not

**Important:** If no AccessGrant matches a Content+verb combination, access is denied by default. Termin is deny-by-default.

### 3.4 Route Scope Checking

Routes may have their own `required_scope` in addition to AccessGrant checks:

```json
{ "method": "POST", "path": "/api/v1/orders/{id}/transition/confirmed", "kind": "TRANSITION", "required_scope": "write orders" }
```

The runtime checks the route-level scope first, then the AccessGrant for the specific verb.

---

## 4. State Machines

### 4.1 Enforcement Rules

State machines are the lifecycle governance primitive. The runtime must enforce:

1. **Only declared transitions are allowed.** If there is no `TransitionSpec` with `from_state=X, to_state=Y`, the transition from X to Y is forbidden.
2. **Scope checking.** The caller must hold the `required_scope` for the transition.
3. **Atomic transitions.** A transition updates the `status` column atomically. No partial state.

### 4.2 Transition API

The typical pattern is a POST to a transition endpoint:

```
POST /api/v1/{content}/{id}/transition/{target_state}
```

The runtime:
1. Loads the record, reads current `status`
2. Finds a TransitionSpec matching `from_state=current, to_state=target`
3. Checks caller scope against `required_scope`
4. Updates `status` to `target_state`
5. Emits a state change event

### 4.3 Multi-Word States

States can contain spaces: `"in progress"`, `"under review"`, `"pending approval"`. The runtime must handle these in URL encoding, storage, and display. In URLs, spaces are typically encoded or hyphenated — the runtime chooses a convention and applies it consistently.

### 4.4 Primitive Type

The `primitive_type` field indicates what the state machine is attached to. Usually `"content"`. State machines can also govern Channels, Computes, and Boundaries (for lifecycle management), but Content state machines are the most common.

---

## 5. Events

### 5.1 Event Processing

Events are reactive rules: when something happens to Content, do something.

**Trigger types:**
- `created` — fires after a new record is inserted
- `updated` — fires after a record is modified
- `deleted` — fires after a record is removed
- `cel` — fires when `condition_expr` evaluates to truthy against the record

### 5.2 Event Actions

When an event fires, the runtime executes its `action`:

```json
{
  "target_content": "alerts",
  "column_mapping": [["product_id", "id"], ["alert_type", "'low_stock'"]]
}
```

This creates a record in `alerts` with `product_id` copied from the source record's `id` and `alert_type` set to the literal `'low_stock'`.

### 5.3 CEL Conditions

When `trigger` is `"cel"`, the runtime evaluates `condition_expr` against the record after each create/update:

```json
{ "trigger": "cel", "condition_expr": "record.quantity <= record.reorder_threshold" }
```

The event fires when the expression is truthy.

### 5.4 Log Levels

Events have a `log_level` that controls the verbosity of event bus logging. The runtime should support configurable filtering (e.g., suppress TRACE and DEBUG in production).

---

## 6. HTTP API (Routes)

### 6.1 Route Generation

The `routes[]` array contains pre-resolved routes. Each route has:
- `method` + `path` — the HTTP endpoint
- `kind` — semantic purpose (determines handler behavior)
- `content_ref` — which Content this operates on
- `required_scope` — access check

### 6.2 Route Kind Handlers

| Kind | Behavior |
|---|---|
| `LIST` | Query all records, apply filters, return array |
| `GET_ONE` | Fetch single record by `lookup_column` |
| `CREATE` | Insert new record, validate constraints, set initial state |
| `UPDATE` | Modify existing record by `lookup_column` |
| `DELETE` | Remove record by `lookup_column` |
| `TRANSITION` | Change `status` to `target_state`, enforce state machine |
| `STREAM` | Server-Sent Events endpoint for real-time updates |

### 6.3 Routes May Be Empty

If `routes[]` is empty, the runtime should auto-generate CRUD routes for each Content based on AccessGrants. The reference runtime does this. Explicit routes override auto-generation.

---

## 7. Presentation (Component Trees)

### 7.1 Page Selection

Each `PageEntry` has a `role`. When a user requests a page by slug, the runtime selects the entry matching the user's role. If multiple entries share a slug with different roles, the user sees only their role's version.

If `required_scope` is set, the runtime must verify the user holds that scope before rendering.

### 7.2 Component Tree Rendering

The runtime walks the component tree depth-first and renders each node:

```
renderPage(page, identity):
  for each child in page.children:
    renderComponent(child, context={identity, data={}})

renderComponent(node, context):
  1. Evaluate visible_when if present → skip/disable if false
  2. Resolve data requirements (fetch Content for data_table, aggregation, etc.)
  3. Evaluate is_expr props against context using CEL
  4. Render the component type
  5. Recursively render children
```

### 7.3 Props: Literals vs Expressions

Props can be:
- **Bare strings/numbers/booleans/arrays** — literal values, use directly
- **PropValue objects** `{"value": "expr", "is_expr": true}` — CEL expressions, evaluate at render time

The compiler's serializer simplifies literal PropValues to bare values. So in practice:
```json
"content": "Hello"                                          // literal
"content": {"value": "greet(user.name)", "is_expr": true}   // expression
```

A runtime implementer should check: if a prop value is an object with `is_expr: true`, evaluate `value` as CEL. Otherwise, use the value as-is.

### 7.4 Component Type Reference

#### `text`
Renders static or dynamic text content.

| Prop | Type | Description |
|---|---|---|
| `content` | string or PropValue | The text to display |

#### `data_table`
Renders a table of Content records with optional sub-components.

| Prop | Type | Description |
|---|---|---|
| `source` | string | Snake_case Content name to query |
| `columns` | array | `[{field, label}]` — columns to display |
| `row_actions` | array | Action button definitions (rendered per-row) |

**Data requirement:** Query all records from `source` Content. Apply filters/search from child components.

**Children:** `filter`, `search`, `highlight`, `subscribe`, `related` — these modify the table's behavior.

#### `form`
Renders a data entry form.

| Prop | Type | Description |
|---|---|---|
| `target` | string | Snake_case Content to create/update records in |
| `create_as` | string? | Initial status for new records (if Content has state machine) |
| `submit_scope` | string? | Scope required to submit the form |
| `after_save` | string? | Instruction after successful save (e.g., `"return_to:page_slug"`) |

**Children:** `field_input` components defining the form fields.

#### `field_input`
A single form field. Must be a child of `form`.

| Prop | Type | Description |
|---|---|---|
| `field` | string | Snake_case field name from the target Content |
| `label` | string | Display label |
| `input_type` | string | Widget type (text, number, currency, enum, reference, etc.) |
| `required` | boolean | Whether the field is required |
| `minimum` | integer? | Minimum value for numeric fields |
| `step` | string? | Input step (e.g., "0.01" for currency) |
| `enum_values` | array? | Options for enum dropdowns |
| `reference_content` | string? | Target Content for reference lookups |
| `reference_display_col` | string? | Column to display in lookup selector |
| `reference_unique_col` | string? | Column for unique matching |
| `validate_unique` | boolean? | Whether to validate uniqueness before save |

#### `filter`
Adds a filter control to a parent `data_table`.

| Prop | Type | Description |
|---|---|---|
| `field` | string | Snake_case field to filter on |
| `mode` | string | `"enum"`, `"state"`, `"distinct"`, `"reference"` |
| `options` | array? | Explicit options (for enum/state modes) |

**Filter modes:**
- `enum` — dropdown with values from the field's `enum_values`
- `state` — dropdown with values from the Content's state machine states
- `distinct` — dropdown populated by `SELECT DISTINCT field FROM content`
- `reference` — dropdown populated from the referenced Content

#### `search`
Adds a search box to a parent `data_table`.

| Prop | Type | Description |
|---|---|---|
| `fields` | array | Snake_case field names to search across |

**Implementation:** Case-insensitive substring match across all listed fields. SQL: `WHERE field1 LIKE '%query%' OR field2 LIKE '%query%'`.

#### `highlight`
Conditional row highlighting on a parent `data_table`.

| Prop | Type | Description |
|---|---|---|
| `condition` | PropValue | CEL expression evaluated per-row |

Rows where the expression is truthy should be visually highlighted (e.g., red background, bold text).

#### `subscribe`
Activates real-time updates for a parent `data_table`.

| Prop | Type | Description |
|---|---|---|
| `content` | string | Snake_case Content name to subscribe to |

When a record in the subscribed Content changes, the runtime should push an update to the client, which re-renders the table without a page reload.

#### `related`
Shows related records from another Content, grouped by a field.

| Prop | Type | Description |
|---|---|---|
| `content` | string | Related Content to query |
| `join` | string | Field in the related Content that references this table |
| `group_by` | string? | Field to group related records by |

#### `aggregation`
A computed summary value.

| Prop | Type | Description |
|---|---|---|
| `label` | string | Display text |
| `agg_type` | string | `"count"`, `"sum"`, `"average"`, `"minimum"`, `"maximum"` |
| `source` | string | Snake_case Content to aggregate |
| `expression` | PropValue? | CEL expression to aggregate (for sum/average/min/max) |
| `format` | string? | Display format: `"currency"`, `"number"`, `"percentage"` |

**SQL mapping:**
- `count` → `SELECT COUNT(*) FROM source`
- `sum` → `SELECT SUM(expression) FROM source` (expression may reference joins)
- `average` → `SELECT AVG(expression) FROM source`
- `minimum` → `SELECT MIN(expression) FROM source`
- `maximum` → `SELECT MAX(expression) FROM source`

#### `stat_breakdown`
Count of records grouped by a field (typically status).

| Prop | Type | Description |
|---|---|---|
| `source` | string | Snake_case Content to query |
| `label` | string | Display text |
| `group_by` | string | Field to group by |

**SQL:** `SELECT group_by, COUNT(*) FROM source GROUP BY group_by`

#### `chart`
Data visualization.

| Prop | Type | Description |
|---|---|---|
| `source` | string | Snake_case Content to visualize |
| `chart_type` | string | `"line"`, `"bar"`, `"pie"` |
| `period_days` | integer | Time window in days |
| `label` | string? | Chart title |

#### `section`
A labeled grouping container. Can nest arbitrarily.

| Prop | Type | Description |
|---|---|---|
| `title` | string | Section heading |
| `collapsible` | boolean? | Whether the section can collapse |
| `visible_when` | PropValue? | Conditional visibility |

**Children:** Any component types, including other sections.

#### `action_button`
Triggers a state transition on a record.

| Prop | Type | Description |
|---|---|---|
| `label` | string | Button text |
| `action` | string | `"transition"` |
| `target_state` | string | State to transition to |
| `visible_when` | PropValue? | CEL expression for conditional visibility |
| `unavailable_behavior` | string | `"disable"` (default) or `"hide"` |

Action buttons appear either as standalone components or in `row_actions` on a `data_table`. In `row_actions`, the `visible_when` expression evaluates per-row with `.` prefix accessing the current row's fields.

### 7.5 Component Nesting Rules

| Parent | Valid Children |
|---|---|
| `PageEntry` | Any top-level component |
| `data_table` | `filter`, `search`, `highlight`, `subscribe`, `related` |
| `form` | `field_input` |
| `section` | Any component |
| All others | No children (leaf nodes) |

---

## 8. Navigation

The `nav_items[]` array defines the application's navigation menu.

```json
{ "label": "Orders", "page_slug": "order_dashboard", "visible_to": ["all"], "badge_content": null }
{ "label": "Analytics", "page_slug": "order_analytics", "visible_to": ["manager"], "badge_content": "orders" }
```

- `visible_to: ["all"]` — shown to everyone
- `visible_to: ["manager", "admin"]` — shown only to users with those roles
- `badge_content` — if set, show a count badge: `SELECT COUNT(*) FROM badge_content`

---

## 9. Compute Modules

### 9.1 Shape Semantics

Compute modules are declared data transformations. They don't execute automatically — they are invoked through Channels or API calls.

| Shape | Input → Output | Description |
|---|---|---|
| `TRANSFORM` | 1 Content → 1 Content | Map: transform each record |
| `REDUCE` | N Content → 1 Content | Aggregate: combine many into summary |
| `EXPAND` | 1 Content → N Content | Split: produce multiple outputs per input |
| `CORRELATE` | N Content → 1 Content | Join: combine records from multiple sources |
| `ROUTE` | 1 Content → conditional | Route: send to different outputs based on conditions |

### 9.2 Body Evaluation

`body_lines` contains CEL expressions. The runtime evaluates them with input Content records in scope. The variable names in the CEL match the input/output Content names.

### 9.3 Client Safety

When `client_safe` is `true`, the Compute's CEL body can be evaluated on the client without server involvement. This is useful for optimistic UI updates. The server always retains the authoritative copy and re-evaluates server-side.

---

## 10. Channels

Channels are declared data flow paths. In the current runtime, Channels are primarily metadata — they describe the intended data flow topology. Future runtimes will implement Channel enforcement (data can only cross Boundaries through Channels).

Key fields:
- `carries_content` — the Content schema this Channel transports
- `direction` — INBOUND (external→app), OUTBOUND (app→external), BIDIRECTIONAL, INTERNAL
- `delivery` — REALTIME (WebSocket/SSE), RELIABLE (queued), BATCH, AUTO
- `endpoint` — explicit URL path for the Channel (null = internal routing)
- `requirements` — scope checks for send/receive access

---

## 11. Boundaries

Boundaries are trust and isolation zones. In Phase 0, Boundaries are primarily metadata — they declare which Content belongs to which isolation zone. Future phases enforce that data cannot cross Boundaries except through Channels.

Key fields:
- `boundary_type` — `"application"` (deployable unit), `"library"` (reusable definitions), `"module"` (sub-unit), `"configuration"` (settings)
- `contains_content` — Content schemas inside this Boundary
- `contains_boundaries` — nested Boundaries
- `identity_mode` — `"inherit"` (caller identity flows through) or `"restrict"` (only listed scopes allowed)

---

## 12. Error Handling

Error handlers match errors from specific sources and apply recovery actions.

```json
{
  "source": "order_webhook",
  "source_type": "channel",
  "actions": [
    { "kind": "retry", "retry_count": 3, "retry_backoff": true, "retry_max_delay": "30s" },
    { "kind": "notify", "target": "admin_alerts" }
  ]
}
```

**Action kinds:**
- `retry` — re-attempt the failed operation
- `disable` — stop the erroring primitive
- `escalate` — promote error severity
- `create` — create a record in a Content (for audit trails)
- `notify` — send a notification
- `set` — set a value using a CEL expression

Catch-all handlers (`is_catch_all: true`, `source: ""`) match any error within their boundary scope.

---

## 13. Real-Time Updates

### 13.1 WebSocket Protocol

The reference runtime uses WebSocket multiplexing for real-time updates:

1. **Client connects** to `/runtime/ws` with identity cookie
2. **Client subscribes** to Content changes:
   ```json
   {"type": "subscribe", "content": "orders"}
   ```
3. **Server pushes** on CRUD events:
   ```json
   {"type": "push", "channel_id": "content.orders.updated", "payload": {"id": 5, "status": "confirmed", ...}}
   ```
4. **Client updates** the local cache and re-renders affected components

### 13.2 WebSocket Behavioral Contract

A conforming runtime MUST satisfy these behavioral requirements:

1. **Push on create:** When a record is created (via any path — API, form, webhook, Compute), all WebSocket subscribers to that Content type MUST receive a push event within 2 seconds. The push payload MUST be the record dict with `id` and all field values — not an event wrapper.

2. **Push on update:** When a record is updated, all subscribers MUST receive a push event with the updated field values within 2 seconds.

3. **Subscribe returns current data:** When a client subscribes to a Content type, the subscribe response MUST include a `current` array of all existing records for that Content type.

4. **No cross-content leakage:** Creating or updating Content type A MUST NOT produce a push event for subscribers of Content type B.

5. **No duplicate pushes:** One create or update operation MUST produce exactly one push event per subscriber. Duplicate pushes break client-side idempotency.

6. **Push payload shape:** The push frame MUST have this structure:
   ```json
   {"v": 1, "ch": "content.{name}.created", "op": "push", "ref": null, "payload": {"id": 1, "field1": "value1", ...}}
   ```
   The `payload` is the record itself. It MUST contain `id` and all Content field values. It MUST NOT contain event bus wrapper keys like `channel_id`, `type`, or `record`.

7. **Background Compute updates:** When a Compute (including LLM/agent Computes running in background threads) updates a record, the push MUST be delivered to WebSocket subscribers on the main event loop. Cross-thread event delivery MUST work correctly.

These requirements are validated by the behavioral conformance tests in `test_behavioral_ws.py`.

### 13.3 Event Bus

Internally, the runtime uses an Event Bus to propagate changes:

1. Storage layer emits events on create/update/delete
2. Event Bus routes events to subscribers (WebSocket connections, event handlers)
3. Channel ID format: `content.{snake_name}.{verb}` (e.g., `content.orders.created`)

---

## 14. Reflection

When `reflection_enabled` is `true`, the runtime should expose introspection endpoints:

- `/api/reflect` — application metadata (name, description, IR version, full IR)
- `/api/reflect/content` — Content schemas
- `/api/reflect/compute` — Compute definitions
- `/api/reflect/channels` — Channel topology (future)
- `/api/reflect/boundaries` — Boundary hierarchy (future)
- `/api/reflect/state_machines` — State machine definitions (future)
- `/api/reflect/error_handlers` — Error handling rules (future)

Reflection is read-only and used for operational visibility, debugging, and AppSec review.

---

## 15. Seed Data

The runtime may accept seed data — a JSON object mapping Content names to arrays of records:

```json
{
  "projects": [
    {"name": "Project Alpha", "status": "active"},
    {"name": "Project Beta", "status": "planning"}
  ],
  "team_members": [
    {"name": "Alice", "role": "developer"}
  ]
}
```

Seed data is loaded only when the target Content table is empty (first run). This enables demo and testing scenarios without manual data entry.

---

## 16. Implementation Checklist

A conforming Termin runtime must:

- [ ] Read and validate IR JSON against `termin-ir-schema.json`
- [ ] Create storage tables from `content[]` with implicit `id` and optional `status`
- [ ] Enforce field constraints: `required`, `unique`, `minimum`, `maximum`, `enum_values`
- [ ] Enforce foreign key integrity for `reference` fields
- [ ] Resolve identity to scopes via `auth` configuration
- [ ] Check AccessGrants on every CRUD operation (deny-by-default)
- [ ] Enforce state machine transitions (only declared transitions, scope-checked)
- [ ] Set `initial_state` on record creation when `has_state_machine` is true
- [ ] Process events on Content changes (CRUD triggers + CEL conditions)
- [ ] Render pages by walking component trees
- [ ] Evaluate CEL expressions in props marked with `is_expr: true`
- [ ] Support real-time subscriptions for `subscribe` components
- [ ] Expose Reflection endpoints when `reflection_enabled` is true

A conforming runtime **may** additionally:
- Auto-generate routes when `routes[]` is empty
- Support seed data loading
- Implement client-side Compute evaluation for `client_safe` modules
- Implement Channel enforcement for Boundary isolation
- Support custom component types with a plugin system

---

## 17. QualifiedName Convention

Every named primitive uses `QualifiedName` with three forms:

| Form | Use | Example |
|---|---|---|
| `display` | UI rendering, page titles, labels | `"stock levels"` |
| `snake` | Storage tables, API paths, internal keys, foreign keys | `"stock_levels"` |
| `pascal` | Class names, type identifiers | `"StockLevels"` |

All cross-references in the IR use `snake` form. When you see a string field like `content_ref`, `carries_content`, `foreign_key`, `source`, or `target` — it's always snake_case matching a `QualifiedName.snake` somewhere else in the IR.

---

## 18. Security Properties

Termin's security thesis is structural enforcement. The IR encodes security decisions made at compile time. The runtime enforces them at execution time. The key properties:

1. **No SQL injection.** Content access is parameterized. The IR doesn't contain SQL — the runtime generates it from schema definitions. There is no mechanism for arbitrary query construction.

2. **No broken access control.** Every CRUD operation checks AccessGrants. Every transition checks scopes. Every page checks role membership. Deny-by-default.

3. **No mass assignment.** The Content schema defines exactly which fields exist. The runtime rejects unknown fields on create/update.

4. **No broken state transitions.** Only declared transitions are allowed. The state machine is the source of truth.

5. **No information leakage through error messages.** The TerminAtor error router (in the reference runtime) sanitizes error responses — internal details are logged, not returned to callers.

6. **No field leakage through API.** Every API response passes through field redaction. Fields with `confidentiality_scopes` not held by the caller are replaced with `{"__redacted": true, "scope": "..."}` markers.

7. **No field leakage through Compute.** The CEL redaction guard catches redacted markers that leak into expression evaluation. Compile-time analysis catches missing scope declarations. Output taint enforcement blocks entire Compute output when the delegate lacks required scopes.

These properties hold if and only if the runtime correctly implements the enforcement rules described in this guide.

---

## 19. Confidentiality System

The confidentiality system provides field-level and content-level data protection. Restricted fields are **redacted** — replaced with markers that preserve schema shape while withholding values. This happens automatically at every API boundary.

### 19.1 IR Fields

| Location | Field | Type | Description |
|----------|-------|------|-------------|
| `FieldSpec` | `confidentiality_scopes` | `string[]` | Scopes required to see this field (AND semantics) |
| `ContentSchema` | `confidentiality_scopes` | `string[]` | Inherited by fields without their own scopes |
| `ComputeSpec` | `identity_mode` | `"delegate" \| "service"` | Execution identity mode |
| `ComputeSpec` | `required_confidentiality_scopes` | `string[]` | Confidential field scopes the Compute accesses |
| `ComputeSpec` | `output_confidentiality_scope` | `string?` | Explicit reclassification scope |
| `ComputeSpec` | `field_dependencies` | `FieldDependency[]` | Compiler-resolved field access metadata |
| `AppSpec` | `reclassification_points` | `ReclassificationPoint[]` | Audit trail of scope changes |

### 19.2 Effective Scopes (AND Semantics)

A field's effective scope is the **union** of its content-level scopes and its own scopes. The caller must hold **all** scopes in the set:

```
effective_scopes(field) = content.confidentiality_scopes ∪ field.confidentiality_scopes
redact if: effective_scopes ≠ ∅ AND NOT effective_scopes ⊆ caller_scopes
```

### 19.3 Redaction Marker Format

```json
{"__redacted": true, "scope": "access_salary"}
```

- `__redacted: true` — the canonical detection key
- `scope` — informational, the first missing scope (for error messages)
- System fields (`id`) always pass through — record existence is never hidden
- `status` and other auto fields **can** be redacted if they carry scopes

### 19.4 API Endpoint Behavior

| Endpoint | Redaction |
|----------|-----------|
| `GET /list` | Redact each field in each record |
| `GET /{id}` | Redact each field in the single record |
| `POST /create` | Redact fields in the **response** (not the input) |
| `PUT /{id}` | Check write access first, then redact response |

**Write protection:** If a PUT/PATCH request includes a field the caller lacks scopes for, reject with 403. Partial updates preserve existing values for fields not in the request body.

### 19.5 Compute Invocation (Checks 1-4)

Server-side Compute execution at `POST /api/v1/compute/{name}`:

| Check | When | What | HTTP |
|-------|------|------|------|
| 1. Identity gate | Before execution | Delegate must hold `required_confidentiality_scopes` | 403 |
| 2. Taint integrity | Input arrives | Detect unredacted fields for unauthorized delegate | 500 |
| 3. CEL guard | During evaluation | Detect `__redacted` markers in expression context | 500 |
| 4. Output taint | After execution | Block output if delegate lacks taint/reclassification scope | 403 |

**Service mode:** Check 1 passes automatically (service identity is auto-provisioned). Output taint (Check 4) is checked against the **delegate's** scopes, not the service's.

**Reclassification:** If `output_confidentiality_scope` is set, the output carries that scope instead of the input taint. The delegate must hold the reclassified scope.

### 19.6 Presentation Rendering

- **Table cells:** Show `[REDACTED]` in gray italic for redacted values
- **Form fields:** Omit fields the user cannot see (or show disabled)
- **Text components:** Show `[REDACTED]` for redacted expression results
- **Aggregations:** Show `[RESTRICTED]` if source data contains redacted fields

### 19.7 Audit Levels

| Event | Log Level | Description |
|-------|-----------|-------------|
| Field redacted | TRACE | Normal operation, off by default |
| Check 1 (gate rejected) | INFO | Normal access control |
| Check 2 (taint violation) | ERROR | Upstream bug or compromise |
| Check 3 (redacted access) | ERROR | Static analysis gap |
| Check 4 (output blocked) | INFO | Normal taint enforcement |
