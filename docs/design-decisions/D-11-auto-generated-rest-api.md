# D-11: Auto-Generated REST API

**Status:** Design complete, implementation pending
**Date:** April 2026
**Authors:** Jamie-Leigh Blake & Claude Anthropic
**Depends on:** Access grants (Verb enum), State machines, Compute invocation API

---

## Summary

Every Content type automatically gets a full CRUD REST API at `/api/v1/{content}`. No user stories required. This enables headless services — `.termin` files with no presentation layer that are pure API+business logic. The presentation layer is optional; the data layer, access control, state machines, computes, channels, and boundaries work without it.

The `Expose a REST API` DSL syntax is removed — it's redundant boilerplate now that routes are automatic.

---

## Design Decisions

### D-11.1: Automatic CRUD for Every Content

Every Content type declared in a `.termin` file gets these routes automatically:

| Method | Path | Verb Required | Description |
|--------|------|---------------|-------------|
| GET | `/api/v1/{content}` | VIEW | List all records |
| POST | `/api/v1/{content}` | CREATE | Create a record |
| GET | `/api/v1/{content}/{id}` | VIEW | Get one record |
| PUT | `/api/v1/{content}/{id}` | UPDATE | Update a record |
| DELETE | `/api/v1/{content}/{id}` | DELETE | Delete a record |

The runtime enforces:
- **Access control:** Caller must hold a scope that grants the required verb for this content. 403 if not.
- **Schema validation:** Required fields, type checking, enum values, min/max constraints. 422 on violation.
- **Dependent values:** When-clause constraints (D-19) enforced on create/update. 422 on violation.
- **Confidentiality:** Fields with `confidentiality_scopes` are redacted in responses for callers who lack the scope.
- **Audit:** Create/update/delete operations logged per D-18 audit level.

These routes exist whether or not the app has user stories, pages, or any presentation layer.

### D-11.2: Automatic State Transition Endpoints

Every Content with a state machine gets transition endpoints:

| Method | Path | Scope Required | Description |
|--------|------|----------------|-------------|
| POST | `/api/v1/{content}/{id}/_transition/{target_state}` | Transition's required_scope | Execute a state transition |
| GET | `/api/v1/{content}/{id}/_transitions` | VIEW | List valid transitions from current state |

The runtime enforces the state machine rules: only declared transitions are allowed, only with the required scope.

### D-11.3: Compute Invocation API (Already Exists)

Computes already have invocation endpoints. No change needed:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/compute/{name}/invoke` | Invoke a compute |

### D-11.4: Headless Services

A `.termin` file with no user stories is a headless service. It compiles, deploys, and runs — serving only the auto-generated API. No pages, no nav, no HTML rendering.

**Example — a pure order processing service:**
```
Application: Order Service
  Description: A headless order processing service

Users authenticate with stub
Scopes are "orders.read", "orders.write", and "orders.admin"
A "writer" has "orders.read" and "orders.write"

Content called "orders":
  Each order has a customer which is text, required
  Each order has a total which is currency
  Each order has a status which is one of: "pending", "confirmed", "shipped"
  Anyone with "orders.read" can view orders
  Anyone with "orders.write" can create or update orders
  Anyone with "orders.admin" can delete orders

State for orders called "order lifecycle":
  An order starts as "pending"
  An order can also be "confirmed" or "shipped"
  A pending order can become confirmed if the user has "orders.write"
  A confirmed order can become shipped if the user has "orders.admin"
```

This produces a fully functional REST API with schema validation, access control, and state machine enforcement — zero presentation code.

### D-11.5: Presentation Routes Are Separate

User stories continue to generate presentation routes at `/{slug}` (HTML pages). The auto-API routes live at `/api/v1/` and never conflict.

**Reserved word:** `api` is reserved — the compiler emits an error if a page slug would be "api". This prevents route conflicts.

**Both can coexist:** An app can have both presentation routes (for browser users) and API routes (for programmatic consumers). This is the normal case for apps with user stories.

### D-11.6: Remove `Expose a REST API` Syntax

The `Expose a REST API at "/path":` syntax is removed from the DSL. It was useful as scaffolding when routes weren't automatic, but now:

- Every Content already gets CRUD routes automatically
- Computes already have invocation endpoints
- Channels already have webhook/action endpoints
- The manual API section is boilerplate that can drift out of sync

Pre-v1.0, no backward compatibility needed. Existing examples that use `Expose a REST API` (e.g., `security_agent.termin`) will have that section removed.

### D-11.7: Reflection Endpoint

The existing `/runtime/registry` endpoint already documents the available API. With auto-CRUD, it will automatically include all Content endpoints. This replaces the documentation purpose of `Expose a REST API`.

Additionally, each Content gets a schema endpoint:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/{content}/_schema` | Returns field definitions, types, constraints, valid transitions |

This enables API consumers to discover the schema programmatically.

---

## Implementation Plan

1. **Compiler:** Remove `Expose a REST API` and `ApiSection`/`ApiEndpoint` from grammar, parser, AST, and IR. Add `api` as reserved page slug. Generate auto-CRUD RouteSpecs in lowering for every Content.
2. **IR:** RouteSpecs now always generated for Content (not just from user stories). Remove `ApiSection` from IR if present.
3. **Runtime:** The CRUD routes already exist in `app.py` — they're generated from RouteSpecs. The change is that RouteSpecs are always present, not conditional on user stories. Add `/_transitions` and `/_schema` endpoints.
4. **Examples:** Remove `Expose a REST API` sections from `security_agent.termin` and any other examples. Add a new `headless_service.termin` example demonstrating a pure API service.
5. **Conformance:** Test that headless apps (no user stories) have working CRUD endpoints. Test schema validation, access control, and state machine enforcement via API.

---

## Open Questions (deferred)

- **Pagination:** Auto-CRUD list endpoint currently returns all records. Pagination (`?limit=20&offset=0`) is needed for production but deferred.
- **Filtering/sorting:** Query params for filtering (`?status=active`) and sorting (`?sort=created_at:desc`). Deferred.
- **Bulk operations:** `POST /api/v1/{content}/_bulk` for batch create/update. Deferred — use Compute for now.
- **API versioning:** Currently hardcoded to `/api/v1/`. Strategy for v2 deferred until we have a breaking change.
