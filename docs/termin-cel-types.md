# Termin CEL Types Reference

**Version:** 0.1.0 (draft)
**Date:** April 2026
**Status:** Open for iteration

---

## Overview

Termin uses [CEL (Common Expression Language)](https://github.com/google/cel-spec) for all expressions in the DSL. This document defines the system types available in the CEL evaluation context. These types are injected by the runtime and are available in all CEL expressions unless otherwise scoped.

CEL is non-Turing-complete, formally specified, and has matching implementations in Python, JavaScript, Rust, and Go. Termin expressions use standard function-call syntax: `sum(items)`, `upper(User.Name)`, `size(employees)`.

---

## 1. System Types

### 1.1 User

The current caller's identity. Available in all CEL expressions. Injected by the runtime from the authentication provider.

| Field | Type | Description |
|-------|------|-------------|
| `User.Username` | string | Opaque identifier (may be email, login name, etc.) |
| `User.Name` | string | Display name (e.g., "Jamie-Leigh Blake") |
| `User.FirstName` | string | First name portion of display name |
| `User.Role` | string | Current role name (e.g., "hr business partner") |
| `User.Scopes` | list[string] | Scopes granted by the current role |
| `User.Authenticated` | bool | `true` if the user has authenticated, `false` for anonymous |

**Usage:**
```termin
Each ticket has a submitted by which is text, defaults to [User.Name]
Display text [upper(User.FirstName)]
```

**Provider contract:** Every auth provider (stub, OAuth, JWT, OIDC, etc.) must produce a `User` object with all six fields. The runtime normalizes provider-specific identity into this shape. `User.Username` is opaque — it may be an email, a login name, or an internal ID depending on the provider.

**Replaces:** `CurrentUser`, `LoggedInUser.CurrentUser`, `UserProfile`. These are deprecated and will be removed.

### 1.2 Agent

The currently executing Compute's identity. Available in Compute CEL bodies, preconditions, and postconditions. Not available in non-Compute expressions (field defaults, highlights, etc.).

| Field | Type | Description |
|-------|------|-------------|
| `Agent.Name` | string | Compute name (e.g., "scanner") |
| `Agent.Provider` | string | Provider type: "cel", "ai-agent", or CCP package name |
| `Agent.IdentityMode` | string | "delegate" or "service" |
| `Agent.Scopes` | list[string] | Effective scopes for this execution |
| `Agent.ExecutionId` | string | Unique ID for this invocation (UUID, for audit/correlation) |

**Usage:**
```termin
Preconditions are:
  [Agent.Scopes.contains("triage")]
  [Agent.IdentityMode == "service"]
```

### 1.3 Before / After (Postcondition-scoped)

Snapshots of the environment before and after Compute execution. **Only available inside `Postconditions are:` blocks.** The compiler rejects `Before` or `After` references in any other context.

`Before` is captured when the Compute's preconditions pass (just before execution begins). `After` is captured when the Compute signals completion (before the transaction commits).

Both `Before` and `After` wrap the same shape — the full environment accessible via the Compute's declared inputs and outputs. Property access follows content names:

| Access Pattern | Type | Description |
|---------------|------|-------------|
| `Before.{content}.{field}` | varies | Field value before execution |
| `After.{content}.{field}` | varies | Field value after execution |
| `Before.App.IR` | object | Application IR before execution |
| `Before.App.Permissions` | object | IAM/scope configuration before execution |
| `After.App.IR` | object | Application IR after execution |

**Usage:**
```termin
Postconditions are:
  [Before.App.IR == After.App.IR]
  [Before.App.Permissions >= After.App.Permissions]
  [After.findings.size() <= Before.findings.size() + 100]
```

**Comparison semantics:** `Before.X >= After.X` means "the before value is a superset of or equal to the after value." For permissions, this means the agent cannot add permissions it didn't have before. For content collections, `size()` comparisons bound the number of records created.

---

## 2. Dynamic Context Variables

These are plain values (not structured types) injected fresh on each evaluation.

| Variable | Type | Description |
|----------|------|-------------|
| `now` | string (ISO 8601) | Current UTC timestamp (`2026-04-08T12:00:00Z`) |
| `today` | string (ISO 8601) | Current date (`2026-04-08`) |

---

## 3. System Functions

Registered on the CEL environment by the runtime. Available in all expressions.

### Aggregation
| Function | Signature | Description |
|----------|-----------|-------------|
| `sum(items)` | `list[number] -> number` | Sum of all values |
| `avg(items)` | `list[number] -> number` | Arithmetic mean |
| `min(items)` | `list[number] -> number` | Minimum value |
| `max(items)` | `list[number] -> number` | Maximum value |

### Collection
| Function | Signature | Description |
|----------|-----------|-------------|
| `flatten(items)` | `list[list[T]] -> list[T]` | Flatten nested lists |
| `unique(items)` | `list[T] -> list[T]` | Remove duplicates (preserves order) |
| `first(items)` | `list[T] -> T` | First element |
| `last(items)` | `list[T] -> T` | Last element |
| `sort(items)` | `list[T] -> list[T]` | Ascending sort |

### Temporal
| Function | Signature | Description |
|----------|-----------|-------------|
| `daysBetween(a, b)` | `(string, string) -> int` | Days between two ISO dates |
| `daysUntil(date)` | `string -> int` | Days from today to date |
| `addDays(date, n)` | `(string, int) -> string` | Add days to an ISO date |

### String
| Function | Signature | Description |
|----------|-----------|-------------|
| `upper(s)` | `string -> string` | Uppercase |
| `lower(s)` | `string -> string` | Lowercase |
| `trim(s)` | `string -> string` | Strip whitespace |
| `replace(s, old, new)` | `(string, string, string) -> string` | Replace all occurrences |

### Math
| Function | Signature | Description |
|----------|-----------|-------------|
| `round(n, digits)` | `(number, int?) -> number` | Round to N digits (default 0) |
| `floor(n)` | `number -> int` | Floor |
| `ceil(n)` | `number -> int` | Ceiling |
| `abs(n)` | `number -> number` | Absolute value |
| `clamp(n, lo, hi)` | `(number, number, number) -> number` | Clamp to range |

### CEL Built-ins (no registration needed)
| Function | Description |
|----------|-------------|
| `size(x)` | Length of string, list, or map |
| `x.contains(y)` | String/list contains |
| `x.startsWith(y)` | String starts with |
| `x.endsWith(y)` | String ends with |
| `has(x.field)` | Field existence check |

---

## 4. Content Context

In expressions that operate on content data (highlights, compute bodies, event conditions), the content records are available by their snake_case name:

```termin
Highlight rows where [priority == "critical" || priority == "high"]
[team_bonus = sum(employees.salary * employees.bonus_rate)]
When [findings.severity == "critical"]:
```

Field access uses dot notation. The runtime injects content records into the CEL context before evaluation.

---

## 5. Deprecated Syntax

The following legacy syntax is deprecated and will be removed:

| Deprecated | Replacement | Notes |
|-----------|-------------|-------|
| `LoggedInUser.CurrentUser` | `User` | Direct access via `User.Name`, `User.FirstName` |
| `CurrentUser` | `User` | Same object, cleaner name |
| `UserProfile` (as type name) | `User` | Not a separate type |
| `SayHelloTo(LoggedInUser.CurrentUser)` | `SayHelloTo(User.FirstName)` | Pass specific field, not entire profile |

---

## 6. Future Types (Planned)

These types are referenced in the agent design (thread 001) but not yet implemented:

| Type | Scope | Description |
|------|-------|-------------|
| `App` | Reflection | Current application metadata (`App.Status`, `App.IR`) |
| `Fabric` | Reflection | Application fabric metadata (multi-app environments) |

These will likely be accessed via reflection calls (`reflect.currentApp()`, `reflect.fabric()`) rather than reserved global names. Design is open.
