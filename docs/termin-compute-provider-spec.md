# Termin Compute Provider Specification

**Version:** 0.1.0 (draft)
**Date:** April 2026
**Status:** Open for iteration

---

## Overview

A Compute Provider is a pluggable execution engine for Termin Compute nodes. The runtime dispatches to the appropriate provider based on the `provider` field in the ComputeSpec. Providers receive a transaction-scoped tool context and execute within the runtime's confidentiality, access control, and pre/postcondition framework.

---

## Provider Interface

Every provider implements a single method:

```python
class ComputeProvider:
    """Base class for Compute providers."""

    async def execute(self, spec: dict, ctx: ComputeContext) -> dict:
        """Execute a Compute and return the result.

        Args:
            spec: The ComputeSpec IR dict (name, shape, body_lines, etc.)
            ctx: Transaction-scoped tool context for interacting with the runtime

        Returns:
            Dict with the compute result. Postconditions are evaluated
            against this result and the transaction state.

        Raises:
            ComputeError: If execution fails (triggers rollback)
        """
        raise NotImplementedError
```

---

## ComputeContext — The Tool API

The `ctx` object is the agent's window into the Termin runtime. Every operation goes through the transaction staging layer — writes go to staging, reads check staging first then production (read-your-writes). The runtime enforces access control and confidentiality on every call.

```python
class ComputeContext:
    """Transaction-scoped tool context for Compute execution.

    All operations respect the transaction boundary:
    - Writes go to staging (not production until commit)
    - Reads check staging first, fall through to production
    - Rollback discards all staged writes

    All operations respect the caller's identity:
    - Access grants are checked on every CRUD call
    - Confidentiality scopes are enforced (redaction on reads, write gate on writes)
    """

    # ── Identity ──

    @property
    def user(self) -> dict:
        """The User identity context (delegate or service)."""
        # {"Username": "...", "Name": "...", "Role": "...", "Scopes": [...]}

    @property
    def compute(self) -> dict:
        """The Compute execution context."""
        # {"Name": "scanner", "Provider": "ai-agent", "ExecutionId": "...", ...}

    # ── Content Operations ──

    async def content_query(self, content_name: str, filters: dict = None) -> list[dict]:
        """Query records from a content type.

        Reads through the transaction: staged writes are visible,
        staged deletes are hidden. Confidential fields are redacted
        based on the effective identity.

        Args:
            content_name: snake_case content type name
            filters: optional field filters {"status": "active"}

        Returns:
            List of record dicts (redacted per caller's scopes)
        """

    async def content_get(self, content_name: str, record_id: int) -> dict | None:
        """Get a single record by ID (transaction-aware)."""

    async def content_create(self, content_name: str, data: dict) -> dict:
        """Create a record (staged, not committed until transaction commit).

        Access grants are checked. Default expressions are evaluated.
        Initial state is set if a state machine is attached.
        Returns the created record (with generated id).
        """

    async def content_update(self, content_name: str, record_id: int, data: dict) -> dict:
        """Update a record (staged). Write access to confidential fields is checked."""

    async def content_delete(self, content_name: str, record_id: int) -> bool:
        """Delete a record (staged)."""

    # ── State Machine ──

    async def state_transition(self, content_name: str, record_id: int, target_state: str) -> dict:
        """Transition a record's state (staged).

        Validates the transition is legal from the current state.
        Checks the required scope for the transition.
        """

    # ── Reflection ──

    def reflect_app(self) -> dict:
        """Return the current application's IR metadata."""

    def reflect_content(self, name: str) -> dict:
        """Return schema metadata for a content type."""

    def reflect_role(self, name: str) -> dict:
        """Return role definition (name + scopes)."""

    def reflect_roles(self) -> list[str]:
        """Return all role names."""

    # ── Expression Evaluation ──

    def evaluate(self, expression: str, extra_ctx: dict = None) -> any:
        """Evaluate a CEL expression with the full runtime context."""

    # ── Transaction Control ──

    async def commit(self) -> bool:
        """Commit the current transaction (evaluate postconditions, write to prod).

        Returns True if committed successfully.
        Raises PostconditionError if postconditions fail (rolls back).

        Use this for long-running agents that want to commit intermediate
        progress. After commit, a new transaction begins automatically.
        """

    def rollback(self):
        """Rollback the current transaction (discard staging)."""

    # ── Logging ──

    def log(self, level: str, message: str):
        """Log a message through the TerminAtor event bus."""
```

---

## Example: CEL Provider (Default)

The simplest provider — evaluates the CEL body line and returns:

```python
class CELProvider(ComputeProvider):
    """Default Compute provider: evaluates a CEL expression."""

    async def execute(self, spec: dict, ctx: ComputeContext) -> dict:
        body = spec.get("body_lines", [])[0]

        # Build CEL context from input content
        cel_ctx = {}
        for content_name in spec.get("input_content", []):
            cel_ctx[content_name] = await ctx.content_query(content_name)

        result = ctx.evaluate(body, cel_ctx)
        return {"result": result}
```

No transaction writes, no tool calls — just pure expression evaluation.

---

## Example: AI Agent Provider

A provider that dispatches to an LLM with tool-use capabilities:

```python
class AIAgentProvider(ComputeProvider):
    """AI agent provider: autonomous reasoning with tool access."""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def execute(self, spec: dict, ctx: ComputeContext) -> dict:
        # Build the system prompt from Objective + Strategy
        objective = spec.get("objective", "")
        strategy = spec.get("strategy", "")
        system_prompt = f"{objective}\n\nStrategy:\n{strategy}"

        # Define tools the agent can call
        tools = [
            {
                "name": "content_query",
                "description": "Query records from a content type",
                "parameters": {"content_name": "string", "filters": "object"},
            },
            {
                "name": "content_create",
                "description": "Create a new record (staged until commit)",
                "parameters": {"content_name": "string", "data": "object"},
            },
            {
                "name": "state_transition",
                "description": "Transition a record's state",
                "parameters": {"content_name": "string", "record_id": "integer", "target_state": "string"},
            },
            {
                "name": "commit",
                "description": "Commit staged changes (evaluates postconditions)",
                "parameters": {},
            },
            {
                "name": "reflect_app",
                "description": "Get application metadata",
                "parameters": {},
            },
        ]

        # Tool dispatch map
        tool_handlers = {
            "content_query": lambda args: ctx.content_query(args["content_name"], args.get("filters")),
            "content_create": lambda args: ctx.content_create(args["content_name"], args["data"]),
            "state_transition": lambda args: ctx.state_transition(args["content_name"], args["record_id"], args["target_state"]),
            "commit": lambda args: ctx.commit(),
            "reflect_app": lambda args: ctx.reflect_app(),
        }

        # Run the agent loop
        messages = [{"role": "system", "content": system_prompt}]

        for turn in range(50):  # max 50 turns
            response = await self.llm.chat(messages=messages, tools=tools)

            if response.stop_reason == "end_turn":
                # Agent is done
                return {"result": response.content, "turns": turn + 1}

            # Process tool calls
            for tool_call in response.tool_calls:
                handler = tool_handlers.get(tool_call.name)
                if not handler:
                    ctx.log("WARN", f"Unknown tool: {tool_call.name}")
                    continue

                try:
                    result = await handler(tool_call.arguments)
                    messages.append({
                        "role": "tool",
                        "tool_use_id": tool_call.id,
                        "content": str(result),
                    })
                except Exception as e:
                    messages.append({
                        "role": "tool",
                        "tool_use_id": tool_call.id,
                        "content": f"Error: {e}",
                        "is_error": True,
                    })

        # Max turns reached
        ctx.log("WARN", f"Agent hit max turns (50)")
        return {"result": "max turns reached", "turns": 50}
```

---

## Example: Security Scanner (from security-agent.termin)

How the security-agent.termin scanner maps to this interface:

```termin
Compute called "scanner":
  Transform: takes findings, produces findings
  Provider is "ai-agent"
  Identity: service
  Trigger on schedule every 1 hour
  Objective is ```
    You are a security scanning agent for the your application.
    Scan all deployed apps for IAM policy drift, dependency CVEs,
    confidentiality violations, and stale secrets.
  ```
  Strategy is ```
    1. Use reflect.apps() to list all active apps
    2. For each app, check drift, CVEs, secrets, channels
    3. Use content.create("findings", ...) for each issue
    4. Use content.create("scan-runs", ...) to record this execution
  ```
  Preconditions are:
    `Compute.Scopes.contains("triage")`
  Postconditions are:
    `After.findings.size() <= Before.findings.size() + 100`
  "engineer" can execute this
```

At runtime, this becomes:

1. Scheduler fires `POST /api/internal/compute/scanner` (service token auth)
2. Runtime creates Transaction, builds ComputeContext
3. Runtime evaluates precondition: `Compute.Scopes.contains("triage")` → true
4. Runtime dispatches to AIAgentProvider.execute(spec, ctx)
5. Agent loops: calls `ctx.content_query("findings")`, creates new findings via `ctx.content_create("findings", {...})`, calls `ctx.commit()` after each batch
6. Agent finishes, runtime evaluates postcondition: `After.findings.size() <= Before.findings.size() + 100`
7. If pass: final commit. If fail: rollback + TerminAtor error.

---

## Provider Registration

Providers are registered by name at runtime startup:

```python
# In create_termin_app() or deployment config
app.compute_providers = {
    "cel": CELProvider(),
    "ai-agent": AIAgentProvider(llm_client=anthropic.Client()),
    "an AWS-native runtime-security-tools": an AWS-native Termin runtimeSecurityToolsProvider(...),
}
```

The runtime dispatches based on `ComputeSpec.provider`:

```python
provider = app.compute_providers.get(comp.get("provider") or "cel")
result = await provider.execute(comp, ctx)
```

---

## Security Properties

1. **Every tool call goes through access control.** `ctx.content_create("findings", ...)` checks the agent's scopes against the access grants for "findings". An agent with "triage" scope can create findings; one without it cannot.

2. **Every read is redacted.** `ctx.content_query("employees")` returns records with confidential fields redacted based on the agent's effective identity (service or delegate).

3. **Every write is staged.** Nothing touches production until `ctx.commit()` or the agent finishes and postconditions pass.

4. **Postconditions are enforced.** The runtime checks postconditions against the transaction state. A failing postcondition rolls back everything the agent did since the last commit.

5. **The agent cannot bypass the runtime.** There is no direct database access, no raw SQL, no file I/O. The only way to interact with the environment is through the ComputeContext methods, which are all enforcement points.
