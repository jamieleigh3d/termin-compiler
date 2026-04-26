# Termin CEL Types Reference

**Version:** 0.2.0
**Date:** April 2026
**Status:** Open for iteration

---

## Overview

Termin uses [CEL (Common Expression Language)](https://github.com/google/cel-spec) for all expressions in the DSL. This document defines the expression delimiter syntax, system types available in the CEL evaluation context, and system functions.

CEL is non-Turing-complete, formally specified, and has matching implementations in Python, JavaScript, Rust, and Go. Termin expressions use standard function-call syntax: `sum(items)`, `upper(User.Name)`, `size(employees)`.

---

## 1. Expression Delimiters

### 1.1 Inline Expressions (backtick)

Single backticks delimit inline expressions:

```termin
Each ticket has a submitted by which is text, defaults to `User.Name`
Highlight rows where `priority == "critical" || priority == "high"`
```

The content between backticks is a CEL expression evaluated by the runtime. The compiler captures it as an opaque string.

### 1.2 Multi-Line Expressions (triple backtick)

Triple backticks delimit multi-line sub-language blocks:

```termin
Objective is ```
  You are a security scanning agent for the your application.
  Scan all deployed apps for IAM policy drift, dependency CVEs,
  confidentiality violations, and stale secrets.
```
```

Triple-backtick blocks can appear on the same line or on a following line:

```termin
Strategy is ```multi-line content here```

Strategy is
```
multi-line
content
here
```
```

### 1.3 Sub-Language Semantics

Backticks mean "here is content in a different language." The Termin compiler treats the content as **opaque** — it captures the raw text without parsing it. The **provider** determines how to interpret the content:

| Provider | Inline (`` ` ``) | Multi-line (` ``` `) |
|----------|------------------|-----------------------|
| Default (CEL) | CEL expression | Multi-line CEL |
| `"ai-agent"` | Short prompt | System prompt / strategy |
| CCP package | Config expression | Provider-specific DSL |

The compiler does not validate the content of backtick expressions beyond capturing the text. Validation is the provider's responsibility at runtime.

### 1.4 Compute Body Lines

A line consisting entirely of a backtick expression is a Compute body line:

```termin
Compute called "SayHelloTo":
  Transform: takes name : text, produces greeting : text
  `greeting = "Hello, " + name + "!"`
  Anyone with "app.view" can execute this
```

### 1.5 Array Index Safety

Backticks resolve the parsing ambiguity with array indices that brackets had:

```termin
`User.Scopes[0]`           — unambiguous, no conflict with delimiter
`items.filter(x, x.tags[0] == "urgent")`  — nested brackets work naturally
```

With the old `[bracket]` syntax, `[User.Scopes[0]]` was ambiguous because the parser couldn't distinguish the inner `]` from the outer `]`.

---

## 2. System Types

### 2.1 User

The current caller's identity. Available in **all** CEL expressions. Injected by the runtime from the authentication provider.

| Field | Type | Description |
|-------|------|-------------|
| `User.Username` | string | Opaque identifier (may be email, login name, etc.) |
| `User.Name` | string | Display name (e.g., "Jamie-Leigh Blake") |
| `User.FirstName` | string | First name portion of display name |
| `User.Role` | string | Current role name (e.g., "hr business partner") |
| `User.Scopes` | list[string] | Scopes granted by the current role |
| `User.Authenticated` | bool | `true` if authenticated, `false` for anonymous |

**Usage:**
```termin
Each ticket has a submitted by which is text, defaults to `User.Name`
Display text `upper(User.FirstName)`
```

**Provider contract:** Every auth provider (stub, OAuth, JWT, OIDC, etc.) must produce a `User` object with all six fields. The runtime normalizes provider-specific identity into this shape. `User.Username` is opaque — it may be an email, a login name, or an internal ID depending on the provider.

**Replaces:** `CurrentUser`, `LoggedInUser.CurrentUser`, `UserProfile`. These are deprecated and will be removed.

### 2.2 Compute

The currently executing Compute's execution context. Available in Compute CEL bodies, preconditions, and postconditions. Not available in non-Compute expressions (field defaults, highlights, etc.).

| Field | Type | Description |
|-------|------|-------------|
| `Compute.Name` | string | Compute name (e.g., "scanner") |
| `Compute.Provider` | string | Provider type: "cel", "ai-agent", or CCP package name |
| `Compute.IdentityMode` | string | "delegate" or "service" |
| `Compute.Scopes` | list[string] | Effective scopes for this execution |
| `Compute.ExecutionId` | string | Unique ID for this invocation (UUID, for audit/correlation) |
| `Compute.Trigger` | string | How this execution was initiated: "api", "schedule", "event" |
| `Compute.StartedAt` | string | ISO 8601 timestamp of execution start |

**Usage:**
```termin
Preconditions are:
  `Compute.Scopes.contains("triage")`
  `Compute.IdentityMode == "service"`
  `Compute.Trigger == "schedule"`
```

### 2.3 Before / After (Postcondition-Scoped)

Snapshots of the environment before and after Compute execution. **Only available inside `Postconditions are:` blocks.** The compiler rejects `Before` or `After` references in any other context.

`Before` is captured when the Compute's preconditions pass (just before execution begins). `After` is captured when the Compute signals completion (before the transaction commits).

Both `Before` and `After` wrap the same shape — the full environment accessible via the Compute's declared inputs and outputs. Property access follows content names:

| Access Pattern | Type | Description |
|---------------|------|-------------|
| `Before.{content}.{field}` | varies | Field value before execution |
| `After.{content}.{field}` | varies | Field value after execution |
| `Before.App.IR` | object | Application IR before execution |
| `Before.App.Permissions` | object | IAM/scope configuration before execution |

**Usage:**
```termin
Postconditions are:
  `Before.App.IR == After.App.IR`
  `Before.App.Permissions >= After.App.Permissions`
  `After.findings.size() <= Before.findings.size() + 100`
```

**Comparison semantics:** `>=` for structured types means "superset of or equal to." For permissions, `Before.App.Permissions >= After.App.Permissions` means the Compute cannot add permissions. For content collections, `size()` comparisons bound creation.

**Transaction model:** Compute execution operates on a staging copy of the environment (snapshot isolation). Reads go to production unless the transaction has written that value. Writes go to staging. After completion, postconditions are evaluated against the staging state. If all pass, the staging writes are committed to production in write order (journaling). If any postcondition fails, the entire staging area is discarded — no side effects.

Computes can explicitly commit mid-execution via `transaction.commit()` (evaluates postconditions, writes to prod if pass) or roll back via `transaction.rollback()` (discards staging). This enables long-running Computes to make incremental progress.

---

## 3. Dynamic Context Variables

Plain values (not structured types) injected fresh on each evaluation.

| Variable | Type | Description |
|----------|------|-------------|
| `now` | string (ISO 8601) | Current UTC timestamp (`2026-04-08T12:00:00Z`) |
| `today` | string (ISO 8601) | Current date (`2026-04-08`) |

---

## 4. System Functions

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

## 5. Content Context

In expressions that operate on content data (highlights, compute bodies, event conditions), the content records are available by their snake_case name:

```termin
Highlight rows where `priority == "critical" || priority == "high"`
`team_bonus = sum(employees.salary * employees.bonus_rate)`
When `findings.severity == "critical"`:
```

Field access uses dot notation. The runtime injects content records into the CEL context before evaluation.

---

## 6. Deprecated Syntax

| Deprecated | Replacement | Notes |
|-----------|-------------|-------|
| `[expr]` | `` `expr` `` | Backtick delimiter (IR 0.4.0) |
| `LoggedInUser.CurrentUser` | `User` | Direct access via `User.Name`, `User.FirstName` |
| `CurrentUser` | `User` | Same object, cleaner name |
| `UserProfile` (as type name) | `User` | Not a separate type |

---

## 7. Future Types (Planned)

| Type | Scope | Description |
|------|-------|-------------|
| `App` | Reflection | Current application metadata — likely accessed via `reflect.currentApp()` |
| `Fabric` | Reflection | Application fabric metadata — likely accessed via `reflect.fabric()` |
| Role reflection | Reflection | Query role definitions — `reflect.role("engineer").Scopes` |
