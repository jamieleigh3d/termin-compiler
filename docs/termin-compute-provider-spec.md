# Termin Compute Provider Specification

**Version:** 0.2.0 (draft)
**Date:** April 2026
**Status:** Open for iteration

---

## Overview

A Compute Provider is a pluggable execution engine for Termin Compute nodes. The runtime dispatches to the appropriate provider based on the `provider` field in the ComputeSpec. Providers execute within the runtime's confidentiality, access control, transaction, and pre/postcondition framework.

---

## Terminology

| Term | Meaning |
|------|---------|
| **Runtime** | The Termin execution engine (creates apps, enforces rules) |
| **AppFabric** | The deployed application environment (all content, state, configuration) |
| **Transaction** | A snapshot-isolated execution boundary with journal and rollback |
| **Provider** | A pluggable execution engine (CEL, AI agent, CCP package) |
| **ComputeContext** | The scoped API a provider uses to interact with the AppFabric |

---

## Provider Interface

Every provider implements a single method:

```python
class ComputeProvider:
    """Base class for Compute providers."""

    def __init__(self, state: dict = None):
        """Initialize with opaque provider configuration.

        The `state` dict is provider-specific. Examples:
        - CEL provider: {} (no config needed)
        - AI agent: {"model": "claude-sonnet-4-20250514", "region": "us-west-2"}
        - CCP package: {"endpoint": "https://...", "credentials": "..."}

        The runtime does not inspect state. The provider decides how to use it.
        """
        self.state = state or {}

    async def execute(self, spec: dict, ctx: 'ComputeContext') -> dict:
        """Execute a Compute node.

        Args:
            spec: The ComputeSpec IR dict (name, shape, body_lines,
                  objective, strategy, preconditions, postconditions, etc.)
            ctx: Transaction-scoped API for interacting with the AppFabric.
                 All reads and writes go through this context.

        Returns:
            Arbitrary dict — the Compute's output. Returned to the caller
            after output taint enforcement. The shape is provider-defined.

        The runtime handles pre/postconditions, transaction commit/rollback,
        and output taint enforcement around this call. The provider does not
        need to manage these — just execute and return.
        """
        raise NotImplementedError
```

---

## ComputeContext — Runtime API

The `ctx` object is the provider's scoped window into the AppFabric. Every operation respects the transaction boundary and the caller's identity.

### Design Principles

1. **All writes are staged.** Nothing touches the AppFabric until `commit()`.
2. **All reads are transaction-aware.** Staged writes are visible, staged deletes are hidden.
3. **All operations are access-controlled.** Scopes and confidentiality enforced on every call.
4. **Writing to a confidential field the identity cannot access terminates execution.** This is a hard stop — rollback + TerminAtor error. Not a 403, a termination.
5. **No direct storage access.** The only way to interact with the AppFabric is through these methods.

```python
class ComputeContext:

    # ── Input ──

    @property
    def input(self) -> dict:
        """The input data provided by the caller (from the HTTP request body)."""

    # ── Identity ──

    @property
    def user(self) -> dict:
        """The effective identity (delegate = caller, service = auto-provisioned).
        {"Username": "...", "Name": "...", "Role": "...", "Scopes": [...]}"""

    @property
    def compute(self) -> dict:
        """The Compute execution metadata.
        {"Name": "...", "Provider": "...", "IdentityMode": "...",
         "Scopes": [...], "ExecutionId": "...", "Trigger": "...",
         "StartedAt": "..."}"""

    # ── Content Operations ──

    async def content_query(self, content_name: str,
                            filters: dict = None,
                            limit: int = None,
                            offset: int = 0) -> list[dict]:
        """Query records from a content type (transaction-aware).

        Staged writes are visible, staged deletes are hidden.
        Confidential fields are redacted based on effective identity.

        Args:
            content_name: snake_case content type name
            filters: field equality filters {"status": "active", "severity": "critical"}
            limit: max records to return (None = all)
            offset: skip N records (for pagination)
        """

    async def content_get(self, content_name: str, record_id: int) -> dict | None:
        """Get a single record by ID (transaction-aware, redacted)."""

    async def content_create(self, content_name: str, data: dict) -> dict:
        """Create a record (staged).

        Checks: access grants, confidentiality write access, required fields,
        enum constraints, default expressions. Sets initial state if state
        machine is attached. Returns the created record with generated id.

        Raises ComputeTerminated if writing to a confidential field
        the identity cannot access.
        """

    async def content_update(self, content_name: str, record_id: int, data: dict) -> dict:
        """Update a record (staged).

        Raises ComputeTerminated if writing to a confidential field
        the identity cannot access. Partial update — only fields in `data`
        are modified, others are preserved.
        """

    async def content_delete(self, content_name: str, record_id: int) -> bool:
        """Delete a record (staged)."""

    # ── State Machine ──

    async def state_transition(self, content_name: str,
                               record_id: int,
                               target_state: str) -> dict:
        """Transition a record's state (staged).

        Validates the transition is legal from the current state.
        Checks the required scope for the transition.
        Returns the updated record.
        """

    # ── Events ──

    async def event_emit(self, event_name: str, payload: dict = None):
        """Emit an event to the EventBus.

        Triggers any matching event handlers defined in the application.
        The event is staged — handlers run after transaction commit.
        """

    # ── Channels ──

    async def channel_send(self, channel_name: str, data: dict):
        """Send data through a Channel (staged).

        The Channel's direction, delivery, and scope requirements are enforced.
        """

    # ── Reflection (read-only) ──

    def reflect_app(self) -> dict:
        """Return application metadata (not raw IR).
        {"name": "...", "description": "...", "id": "...", "version": "..."}"""

    def reflect_content(self, name: str) -> dict:
        """Return schema metadata for a content type.
        {"fields": [...], "has_state_machine": bool, "confidentiality_scopes": [...]}"""

    def reflect_compute(self, name: str) -> dict:
        """Return Compute metadata (shape, inputs, outputs, provider)."""

    def reflect_role(self, name: str) -> dict:
        """Return role definition {"Name": "...", "Scopes": [...]}."""

    def reflect_roles(self) -> list[str]:
        """Return all role names."""

    def reflect_channels(self) -> list[str]:
        """Return all channel names."""

    def reflect_boundaries(self) -> list[str]:
        """Return all boundary names."""

    # ── Expression Evaluation (read-only, no side effects) ──

    def evaluate(self, expression: str, extra_ctx: dict = None) -> any:
        """Evaluate a CEL expression.

        CEL is non-Turing-complete and has no side effects by design.
        It cannot write to storage, make network calls, or modify state.
        A malformed expression raises a syntax error, not arbitrary execution.

        The evaluation context includes: User, Compute, and any extra_ctx
        provided. Reads are transaction-aware (sees staged data).
        Registered functions are all read-only.

        String injection produces a CEL syntax error, not code execution.
        """

    # ── Transaction Control ──

    async def commit(self) -> bool:
        """Commit the current transaction.

        Evaluates postconditions against Before/After snapshots.
        If all pass: writes staged changes to the AppFabric in journal order.
        If any fail: rolls back all staged changes, raises PostconditionError.

        After a successful commit, a new transaction begins automatically
        for subsequent operations. Use this for long-running providers
        that want to commit intermediate progress.
        """

    def rollback(self):
        """Rollback the current transaction. Discards all staged changes."""

    # ── Logging ──

    def log(self, level: str, message: str):
        """Log a message through the TerminAtor event bus.

        Levels: TRACE, DEBUG, INFO, WARN, ERROR.
        ERROR-level logs are always persisted and trigger TerminAtor routing.
        """
```

---

## Before / After Snapshots

Postconditions compare the AppFabric state before and after execution. `Before` and `After` are **not** materialized data dicts — they are query-able objects with the same interface as `ctx`:

```python
# In postcondition CEL expressions:
Before.content_query("findings").size()   # count before execution
After.content_query("findings").size()    # count after (includes staged creates)
```

- **Before** is a frozen snapshot captured when preconditions pass (just before `execute()`)
- **After** is the transaction's staged view (production + staged writes - staged deletes)

The postcondition evaluator wraps both as CEL-accessible objects. The provider never accesses Before/After directly — only postcondition expressions do.

---

## Example: CEL Provider (Default)

```python
class CELProvider(ComputeProvider):
    """Default: evaluates the CEL body line."""

    async def execute(self, spec: dict, ctx: ComputeContext) -> dict:
        body = spec.get("body_lines", [])[0]
        cel_ctx = {}
        for content_name in spec.get("input_content", []):
            cel_ctx[content_name] = await ctx.content_query(content_name)
        result = ctx.evaluate(body, cel_ctx)
        return {"result": result}
```

---

## Example: AI Agent Provider

```python
class AIAgentProvider(ComputeProvider):
    """Autonomous reasoning with runtime API access."""

    async def execute(self, spec: dict, ctx: ComputeContext) -> dict:
        objective = spec.get("objective", "")
        strategy = spec.get("strategy", "")

        # The provider manages its own LLM client using opaque state.
        # This could be Anthropic API, Bedrock via boto3, a local model, etc.
        # The runtime does not provide LLM services — the provider brings its own.
        llm = self._make_client()  # uses self.state for config

        # Define runtime API verbs available to the agent
        verbs = [
            {"name": "content_query", "params": {"content_name": "str", "filters": "dict?", "limit": "int?"}},
            {"name": "content_create", "params": {"content_name": "str", "data": "dict"}},
            {"name": "content_update", "params": {"content_name": "str", "record_id": "int", "data": "dict"}},
            {"name": "state_transition", "params": {"content_name": "str", "record_id": "int", "target_state": "str"}},
            {"name": "event_emit", "params": {"event_name": "str", "payload": "dict?"}},
            {"name": "commit", "params": {}},
            {"name": "reflect_app", "params": {}},
            {"name": "reflect_content", "params": {"name": "str"}},
        ]

        # Verb dispatch
        dispatch = {
            "content_query": lambda a: ctx.content_query(a["content_name"], a.get("filters"), a.get("limit")),
            "content_create": lambda a: ctx.content_create(a["content_name"], a["data"]),
            "content_update": lambda a: ctx.content_update(a["content_name"], a["record_id"], a["data"]),
            "state_transition": lambda a: ctx.state_transition(a["content_name"], a["record_id"], a["target_state"]),
            "event_emit": lambda a: ctx.event_emit(a["event_name"], a.get("payload")),
            "commit": lambda a: ctx.commit(),
            "reflect_app": lambda a: ctx.reflect_app(),
            "reflect_content": lambda a: ctx.reflect_content(a["name"]),
        }

        # Agent execution loop — provider-managed, not runtime-managed
        messages = [{"role": "system", "content": f"{objective}\n\nStrategy:\n{strategy}"}]

        for turn in range(50):
            response = await llm.generate(messages=messages, verbs=verbs)

            if response.done:
                return {"result": response.content, "turns": turn + 1}

            for verb_call in response.verb_calls:
                handler = dispatch.get(verb_call.name)
                if not handler:
                    ctx.log("WARN", f"Unknown verb: {verb_call.name}")
                    continue
                try:
                    result = await handler(verb_call.arguments)
                    messages.append({"role": "verb_result", "verb_id": verb_call.id, "content": str(result)})
                except Exception as e:
                    messages.append({"role": "verb_result", "verb_id": verb_call.id, "content": f"Error: {e}", "is_error": True})

        ctx.log("WARN", "Agent reached max turns")
        return {"result": "max turns reached", "turns": 50}
```

---

## Provider Registration

Providers are registered at runtime startup with opaque state:

```python
app.compute_providers = {
    "cel": CELProvider(),
    "ai-agent": AIAgentProvider(state={
        "model": "claude-sonnet-4-20250514",
        "region": "us-west-2",
        "client_type": "bedrock",  # or "anthropic", "local"
    }),
    "an AWS-native runtime-security-tools": SecurityToolsProvider(state={
        "endpoint": "https://security-tools.internal",
    }),
}
```

The runtime dispatches: `provider = providers[spec.get("provider") or "cel"]`.

---

## Internal API Security

The runtime exposes two API surfaces:

| Path | Audience | Authentication |
|------|----------|---------------|
| `/api/v1/*` | Users, external clients | Identity provider (stub, OAuth, JWT, OIDC) |
| `/api/internal/*` | Scheduler, workers, services | Service token (`X-Termin-Service-Token` header) |

Internal endpoints:
- `GET /api/internal/schedules` — list all `Trigger on schedule` Computes
- `POST /api/internal/compute/{name}` — invoke a Compute as service identity

The service token is a deployment-time secret (environment variable, Secrets Manager). It is never in the `.termin` file. The runtime rejects requests to `/api/internal/*` without a valid token.

Rationale: `/api/internal/schedules` lists all scheduled Computes with their intervals and providers — this leaks implementation details. Only the innermost trusted services should have this access.

---

## Security Properties

1. **Every runtime API verb is access-controlled.** `ctx.content_create("findings", ...)` checks the identity's scopes against the access grants for "findings".

2. **Every read is redacted.** `ctx.content_query("employees")` returns records with confidential fields redacted based on the effective identity.

3. **Writing to a confidential field the identity cannot access terminates execution.** Rollback + TerminAtor error. Not a soft failure.

4. **All writes are staged.** Nothing touches the AppFabric until `commit()` or successful postcondition evaluation.

5. **Postconditions are enforced.** The runtime checks postconditions against Before/After snapshots. Failure rolls back everything since the last commit.

6. **CEL evaluation has no side effects.** `ctx.evaluate()` cannot write to storage, make network calls, or modify state. String injection produces a syntax error.

7. **The provider cannot bypass the runtime.** No direct database access, no raw SQL, no file I/O. The ComputeContext is the only interface.

---

## Open Design Questions

### Scope Naming Convention

Should scopes use a naming convention for namespacing?

Options:
- Dot notation: `findings.view`, `findings.create`, `salary.access`
- Underscores: `view_findings`, `create_findings`, `access_salary` (current)
- Hierarchical: `content.findings.view`, `field.salary.access`

Current examples use flat underscored names. A convention should be decided and documented.

### Read-Only Scopes

The current scope system is verb-based (`view`, `create`, `update`, `delete`). There is no explicit "read-only" scope modifier. A scope that grants only `view` is effectively read-only. Should we formalize this with a `readonly` flag on access grants, or is the verb-based system sufficient?
