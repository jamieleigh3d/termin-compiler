# Termin Compute Provider Specification

**Version:** 0.3.0
**Date:** April 2026
**Status:** Open for iteration

---

## Overview

A Compute Provider is an execution engine for Termin Compute nodes. The runtime ships with built-in providers. External integrations use Channels (the existing primitive for cross-boundary communication), not custom providers.

---

## Terminology

| Term | Meaning |
|------|---------|
| **Runtime** | The Termin execution engine |
| **AppFabric** | The deployed application environment (all content, state, configuration) |
| **Provider** | A built-in execution engine (CEL or ai-agent) |
| **Channel** | The primitive for external communication (HTTP, WebSocket, webhook) |
| **ComputeContext** | The scoped API a provider uses to interact with the AppFabric |

---

## Provider Model

There are exactly two provider types. Both are built into the runtime.

| Provider | DSL | Execution | Use case |
|----------|-----|-----------|----------|
| **CEL** | `Provider is "cel"` (default) | In-process CEL evaluation | Data transforms, aggregations, derivations |
| **AI Agent** | `Provider is "ai-agent"` | In-process LLM reasoning with runtime API access | Autonomous workflows, scanning, remediation |

External integrations (Slack notifications, AWS API calls, third-party services) are not providers — they are **Channels**:

```termin
Channel called "slack-alerts":
  Carries findings
  Direction: outbound
  Delivery: reliable
  Endpoint: https://slack-notify.internal/webhook
  Requires "alerts.send" to send
```

An AI agent that needs to call Slack does: `ctx.channel_send("slack-alerts", data)`. The runtime handles HTTP, authentication, retries. The agent never executes arbitrary code or spawns processes.

### Why Not Custom Providers?

Custom providers would require either:
- **Local executables** — arbitrary code execution risk, supply chain attacks, OS-specific binaries
- **Remote callback protocol** — reinventing what Channels already do

Channels are the structural answer to external integration. They are declared in the DSL, enforced at compile time, scoped by identity, and delivered via the runtime's transport layer. A Channel with `Endpoint: https://...` is a webhook. A Channel with `Delivery: realtime` is a WebSocket. No new abstraction needed.

---

## Provider Interface

Both providers implement the same interface:

```python
class ComputeProvider:
    """Base class for built-in Compute providers."""

    def __init__(self, config: dict = None):
        """Initialize with runtime-provided configuration.

        Args:
            config: Provider-specific settings from deployment config.
              CEL: {} (none needed)
              AI Agent: {"model": "claude-sonnet-4-20250514", "region": "us-west-2"}
        """
        self.config = config or {}

    async def execute(self, spec: dict, ctx: 'ComputeContext') -> dict:
        """Execute a Compute node.

        Args:
            spec: The ComputeSpec IR dict
            ctx: Transaction-scoped API for interacting with the AppFabric

        Returns:
            Arbitrary dict — the Compute's output.
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
4. **Writing to a confidential field the identity cannot access terminates execution.** Rollback + TerminAtor error.
5. **No direct storage access.** The ComputeContext is the only interface.

```python
class ComputeContext:

    # ── Input ──

    @property
    def input(self) -> dict:
        """The input data provided by the caller."""

    # ── Identity ──

    @property
    def user(self) -> dict:
        """The effective identity (delegate or service)."""

    @property
    def compute(self) -> dict:
        """The Compute execution metadata (Name, Provider, ExecutionId, Trigger, etc.)."""

    # ── Content Operations ──

    async def content_query(self, content_name: str,
                            filters: dict = None,
                            limit: int = None,
                            offset: int = 0) -> list[dict]:
        """Query records (transaction-aware, redacted per identity)."""

    async def content_get(self, content_name: str, record_id: int) -> dict | None:
        """Get a single record by ID."""

    async def content_create(self, content_name: str, data: dict) -> dict:
        """Create a record (staged). Terminates on confidentiality violation."""

    async def content_update(self, content_name: str, record_id: int, data: dict) -> dict:
        """Update a record (staged). Terminates on confidentiality violation."""

    async def content_delete(self, content_name: str, record_id: int) -> bool:
        """Delete a record (staged)."""

    # ── State Machine ──

    async def state_transition(self, content_name: str,
                               record_id: int,
                               target_state: str) -> dict:
        """Transition a record's state (staged, scope-checked)."""

    # ── Events ──

    async def event_emit(self, event_name: str, payload: dict = None):
        """Emit an event (staged — handlers run after commit)."""

    # ── Channels ──

    async def channel_send(self, channel_name: str, data: dict):
        """Send data through a Channel (scope-checked, delivery-enforced).

        This is how providers interact with external services.
        The Channel's endpoint, authentication, and retry policy
        are configured in the DSL and deployment config.
        """

    # ── Reflection (read-only) ──

    def reflect_app(self) -> dict:
        """Application metadata (name, description, id, version)."""

    def reflect_content(self, name: str) -> dict:
        """Schema metadata for a content type."""

    def reflect_compute(self, name: str) -> dict:
        """Compute metadata (shape, inputs, outputs, provider)."""

    def reflect_role(self, name: str) -> dict:
        """Role definition (name + scopes)."""

    def reflect_roles(self) -> list[str]:
        """All role names."""

    def reflect_channels(self) -> list[str]:
        """All channel names."""

    def reflect_boundaries(self) -> list[str]:
        """All boundary names."""

    # ── Expression Evaluation (read-only, no side effects) ──

    def evaluate(self, expression: str, extra_ctx: dict = None) -> any:
        """Evaluate a CEL expression. Non-Turing-complete, no side effects.
        Transaction-aware reads. String injection = syntax error, not execution."""

    # ── Transaction Control ──

    async def commit(self) -> bool:
        """Commit: evaluate postconditions, write to AppFabric if pass, rollback if fail.
        After commit, a new transaction begins for subsequent operations."""

    def rollback(self):
        """Rollback: discard all staged changes."""

    # ── Logging ──

    def log(self, level: str, message: str):
        """Log through TerminAtor. Levels: TRACE, DEBUG, INFO, WARN, ERROR."""
```

---

## CEL Provider (Default)

```python
class CELProvider(ComputeProvider):
    async def execute(self, spec: dict, ctx: ComputeContext) -> dict:
        body = spec.get("body_lines", [])[0]
        cel_ctx = {}
        for content_name in spec.get("input_content", []):
            cel_ctx[content_name] = await ctx.content_query(content_name)
        result = ctx.evaluate(body, cel_ctx)
        return {"result": result}
```

---

## AI Agent Provider

```python
class AIAgentProvider(ComputeProvider):
    async def execute(self, spec: dict, ctx: ComputeContext) -> dict:
        objective = spec.get("objective", "")
        strategy = spec.get("strategy", "")

        # Provider manages its own LLM client using self.config
        # Could be Anthropic API, Bedrock via boto3, local model, etc.
        llm = self._make_client()

        # Runtime API verbs available to the agent
        verbs = {
            "content.query": lambda a: ctx.content_query(a["content_name"], a.get("filters"), a.get("limit")),
            "content.create": lambda a: ctx.content_create(a["content_name"], a["data"]),
            "content.update": lambda a: ctx.content_update(a["content_name"], a["record_id"], a["data"]),
            "state.transition": lambda a: ctx.state_transition(a["content_name"], a["record_id"], a["target_state"]),
            "channel.send": lambda a: ctx.channel_send(a["channel_name"], a["data"]),
            "event.emit": lambda a: ctx.event_emit(a["event_name"], a.get("payload")),
            "reflect.app": lambda a: ctx.reflect_app(),
            "reflect.content": lambda a: ctx.reflect_content(a["name"]),
            "transaction.commit": lambda a: ctx.commit(),
        }

        # Agent loop
        messages = [{"role": "system", "content": f"{objective}\n\nStrategy:\n{strategy}"}]
        for turn in range(50):
            response = await llm.generate(messages=messages, tools=list(verbs.keys()))
            if response.done:
                return {"result": response.content, "turns": turn + 1}
            for call in response.calls:
                handler = verbs.get(call.name)
                if handler:
                    try:
                        result = await handler(call.args)
                        messages.append({"role": "result", "id": call.id, "content": str(result)})
                    except Exception as e:
                        messages.append({"role": "result", "id": call.id, "content": f"Error: {e}", "is_error": True})

        ctx.log("WARN", "Agent reached max turns")
        return {"result": "max turns reached", "turns": 50}
```

---

## Internal API Security

| Path | Audience | Authentication |
|------|----------|---------------|
| `/api/v1/*` | Users, external clients | Identity provider (stub, OAuth, JWT) |
| `/api/internal/*` | Scheduler, workers | Service token (`X-Termin-Service-Token` header) |

Internal endpoints:
- `GET /api/internal/schedules` — list `Trigger on schedule` Computes
- `POST /api/internal/compute/{name}` — invoke as service identity

Service token is a deployment-time secret (env var, Secrets Manager). Never in the `.termin` file.

---

## Before / After Snapshots

Postconditions compare AppFabric state before and after execution. `Before` and `After` are query-able objects with the same interface as `ctx`:

```
Before.content_query("findings").size()   # count before execution
After.content_query("findings").size()    # count after (includes staged)
```

- **Before**: frozen snapshot at precondition pass
- **After**: production + staged writes - staged deletes

---

## Security Properties

1. **Every runtime API verb is access-controlled.**
2. **Every read is redacted per effective identity.**
3. **Writing to a confidential field terminates execution.**
4. **All writes are staged until commit.**
5. **Postconditions are enforced with rollback.**
6. **CEL evaluation has no side effects.**
7. **External I/O only through Channels (declared, scoped, authenticated).**
8. **No arbitrary code execution.** Only two providers, both built into the runtime.

---

## Scope Naming Convention

Dot notation: `resource.verb` or `resource.qualifier`.

```
employees.view       — can view employee records
salary.access        — can see salary fields
findings.triage      — can create/manage findings
```

Dot notation is a convention — scopes are opaque strings to the runtime.
