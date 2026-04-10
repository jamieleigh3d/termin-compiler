# D-12: LLM Structured Output Convention

**Status:** DECIDED 2026-04-10
**Decided by:** JL + Claude
**Affects:** Runtime (AI provider module), deploy config schema

---

## Decision

### Always use tool_use with forced tool calling

Every LLM invocation (Level 1 and Level 3) uses a tool-based output schema. The runtime auto-generates a `set_output` tool from the Compute's output field declarations. The LLM is forced to call this tool via provider-specific settings (`tool_choice`).

This is consistent for all cases — single field, multi-field, agent. No special cases, no raw-text parsing.

### Thinking as implicit output

Every `set_output` tool includes a `thinking` property listed first in the schema. The system prompt instructs the LLM to use it for a brief explanation (one sentence) of its reasoning. Because `thinking` is first in the schema, it acts as a chain-of-thought prior — the model reasons before emitting the structured output fields.

### Tool Schema (auto-generated from output declarations)

For a Compute with:
```
Output into field ticket.category
Output into field ticket.priority
```

The runtime generates:
```json
{
  "name": "set_output",
  "description": "Set the output fields for this computation.",
  "input_schema": {
    "type": "object",
    "properties": {
      "thinking": {
        "type": "string",
        "description": "Brief explanation (one sentence) of your reasoning."
      },
      "category": {
        "type": "string",
        "enum": ["hardware", "software", "network", "access"],
        "description": "Field: ticket.category"
      },
      "priority": {
        "type": "string",
        "enum": ["low", "medium", "high", "critical"],
        "description": "Field: ticket.priority"
      }
    },
    "required": ["thinking", "category", "priority"]
  }
}
```

Field types, enum constraints, and required status are derived from the Content field definitions in the IR. The `thinking` field is always present and always first.

### Forced tool calling

The runtime sets provider-specific parameters to force the model to call the tool:

**Anthropic:**
```json
{
  "tool_choice": {"type": "tool", "name": "set_output"}
}
```

**OpenAI:**
```json
{
  "tool_choice": {"type": "function", "function": {"name": "set_output"}}
}
```

This guarantees structured output without parsing heuristics. The model must call `set_output` before the turn ends.

### For agents (Level 3)

Agents use ComputeContext tools (content.query, content.create, etc.) in addition to `set_output`. The agent loop continues until the agent calls `set_output`, which signals completion. The `thinking` field in `set_output` captures the agent's final summary of what it did.

The agent's intermediate tool calls (content operations) are captured in the execution trace (D-07).

---

## Thinking Capture

The `thinking` field from every `set_output` call is stored alongside the output. It's available through:

1. **Reflection API** — `GET /api/reflect/traces` returns thinking for each Compute invocation
2. **Field on the record** — if the Content has a field that maps to thinking, the runtime can optionally write it there (future: explicit wiring like `Output into field ticket.ai_reasoning`)
3. **Runtime log** — printed at INFO level: `[Termin] [INFO] Compute 'categorize': "Software issue based on description mentioning app crash"`

For v0.5, thinking goes to the log and reflection. Explicit field wiring for thinking is a future enhancement.

---

## Built-in Providers

The runtime ships with two LLM providers:

| Provider | SDK | Notes |
|----------|-----|-------|
| `anthropic` | anthropic Python SDK | Claude models. Default for `Provider is "llm"` and `"ai-agent"`. |
| `openai` | openai Python SDK | GPT models. |

The deploy config specifies which to use:

```json
{
  "ai_provider": {
    "service": "anthropic",
    "model": "claude-sonnet-4-20250514",
    "api_key": "${ANTHROPIC_API_KEY}"
  }
}
```

Third-party providers (e.g., Bedrock) can be added by runtime implementers. The provider interface is: given a system message, user message, and tools array, return the tool call results. An AWS-native runtime can implement Bedrock for their runtime.

---

## Examples

### Single output field
```
Compute called "complete":
  Provider is "llm"
  Accesses completions
  Input from field completion.prompt
  Output into field completion.response
  ...
```

Generated tool:
```json
{
  "name": "set_output",
  "input_schema": {
    "type": "object",
    "properties": {
      "thinking": {"type": "string", "description": "Brief explanation of your reasoning."},
      "response": {"type": "string", "description": "Field: completion.response"}
    },
    "required": ["thinking", "response"]
  }
}
```

### Multi-field output with enum constraints
```
Compute called "categorize":
  Provider is "llm"
  Accesses tickets
  Input from field ticket.title
  Input from field ticket.description
  Output into field ticket.category
  Output into field ticket.priority
  ...
```

Generated tool: (as shown above — enum values from field definitions included in schema)

### Agent completion signal
```
Compute called "reply":
  Provider is "ai-agent"
  Accesses messages
  ...
```

The agent gets ComputeContext tools PLUS `set_output`:
```json
[
  {"name": "content_query", ...},
  {"name": "content_create", ...},
  {"name": "content_update", ...},
  {"name": "state_transition", ...},
  {"name": "set_output", "input_schema": {
    "properties": {
      "thinking": {"type": "string"},
      "summary": {"type": "string", "description": "Summary of actions taken"}
    }
  }}
]
```

The agent loop ends when `set_output` is called.
