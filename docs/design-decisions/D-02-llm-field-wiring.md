# D-02: LLM Field Wiring, Prompt Syntax, and Trigger Filtering

**Status:** DECIDED 2026-04-10
**Decided by:** JL + Claude
**Affects:** Grammar, parser, AST, IR, lowering, runtime

---

## Decision

### Provider Levels

Two LLM-related providers:

- `Provider is "llm"` — Level 1. Field-to-field completion. No tools. One API call.
- `Provider is "ai-agent"` — Level 3. Autonomous agent with ComputeContext tools.

Both use the same two prompt fields: **Directive** and **Objective**.

### Field Wiring (Level 1 LLM)

Explicit `Input from field` and `Output into field` lines wire specific Content fields to the LLM.

```
Input from field completion.prompt
Output into field completion.response
```

For creating a new record instead of updating the input record:

```
Output creates message
```

Multiple inputs are supported — each becomes part of the LLM's user message context:

```
Input from field ticket.title
Input from field ticket.description
Output into field ticket.summary
```

The `content.field` dot notation is a field reference, not a CEL expression. No backticks. The compiler resolves it to a Content name and field name at compile time.

### Prompt Fields

Two fields, consistent across all LLM/agent providers:

**Directive** — maps to the LLM system prompt. The agent's identity, rules, and constraints. Strong prior. Persists across invocations. Think Star Trek's Prime Directive — always followed unless there's an extraordinary reason.

```
Directive is ```
  You are a helpful assistant. Be concise and clear.
  If you don't know the answer, say so.
```
```

**Objective** — maps to the task-level prompt. What to accomplish in this invocation. For Level 1, this is the task description. For Level 3 agents, this includes the strategy (tool usage plan). Weaker prior than Directive.

```
Objective is ```
  Answer the user's prompt thoughtfully.
```
```

Objective supports inline CEL expressions using single backticks within the triple-backtick block. This allows the objective to reference input fields by name:

```
Objective is ```
  The user asked: `completion.prompt`
  
  Provide a detailed answer to their question.
```
```

The runtime evaluates the inline expressions against the triggering record before sending the prompt to the LLM. This is the same inline expression syntax used elsewhere in Termin.

### Trigger Filtering

`Trigger on event` supports an optional `where` clause with a CEL expression to filter which events actually invoke the Compute:

```
Trigger on event "message.created" where `message.role == "user"`
```

This is belt-and-suspenders with the Strategy's "if role is assistant, stop" — the filter prevents the Compute from even being invoked, rather than relying on the LLM to check. The CEL expression is evaluated by the runtime before invoking the provider.

Without the where clause, every matching event triggers the Compute:

```
Trigger on event "completion.created"
```

---

## Examples

### Level 1: Simple completion (agent_simple)

```
Compute called "complete":
  Provider is "llm"
  Input from field completion.prompt
  Output into field completion.response
  Trigger on event "completion.created"
  Directive is ```
    You are a helpful assistant. Be concise and clear.
    If you don't know the answer, say so honestly.
  ```
  Objective is ```
    Answer the following prompt from the user.
  ```
  Anyone with "agent.use" can execute this
```

### Level 3: Chatbot agent (agent_chatbot)

```
Compute called "reply":
  Provider is "ai-agent"
  Trigger on event "message.created" where `message.role == "user"`
  Directive is ```
    You are a conversational assistant. Be helpful and natural.
    Never fabricate information. If asked to do something outside
    your capabilities, explain what you can do instead.
  ```
  Objective is ```
    Reply to the user's latest message. Load the conversation
    history with content.query("messages"). Create your reply
    with content.create("messages", {"role": "assistant",
    "body": your_reply}).
  ```
  Anyone with "chat.use" can execute this
```

### Level 1: Multi-field with inline expressions

```
Compute called "categorize":
  Provider is "llm"
  Input from field ticket.title
  Input from field ticket.description
  Output into field ticket.category
  Output into field ticket.priority
  Trigger on event "ticket.created"
  Directive is ```
    You are an IT helpdesk triage system. You categorize
    and prioritize incoming support tickets.
  ```
  Objective is ```
    Categorize this ticket and assign a priority.
    
    Title: `ticket.title`
    Description: `ticket.description`
    
    Set category to one of: hardware, software, network, access.
    Set priority to one of: low, medium, high, critical.
  ```
  Anyone with "helpdesk.manage" can execute this
```

---

## Runtime Behavior

### Level 1 (Provider is "llm")

1. Event fires, where clause evaluated (if present) — skip if false
2. Runtime reads input fields from the triggering record
3. Runtime evaluates inline CEL expressions in Directive and Objective
4. System message = evaluated Directive
5. User message = evaluated Objective (with input field values interpolated)
6. Single LLM API call (model from deploy config `ai_provider` section)
7. Parse response — for single output field, the full response text goes into the field. For multi-field, the runtime uses structured output (tool_use with a schema matching the output fields)
8. Update the record (or create new record if `Output creates`)
9. Done. No agent loop.

### Level 3 (Provider is "ai-agent")

1. Event fires, where clause evaluated — skip if false
2. Runtime builds ComputeContext with tools scoped to the application
3. System message = evaluated Directive + tool descriptions
4. User message = evaluated Objective + triggering record as context
5. Agent loop: LLM responds with text or tool_use calls
6. Runtime executes tool calls via ComputeContext (content.query, content.create, etc.)
7. Results fed back to LLM for next turn
8. Loop until LLM responds without tool calls (signals completion)
9. Done.

---

## What This Replaces

- `Strategy is` — folded into Objective for agents. Two fields, not three.
- Magic field inference — replaced by explicit Input/Output wiring.
- Model field auto-population — removed. If the author wants a model field, they wire it.

## Grammar Changes Needed

New line types:
- `input_from_field_line = 'Input' 'from' 'field' ref:field_ref $`
- `output_into_field_line = 'Output' 'into' 'field' ref:field_ref $`
- `output_creates_line = 'Output' 'creates' content:word_or_quoted $`
- `directive_line = 'Directive' 'is' rest:rest_of_line $` (reuses triple-backtick merging)
- `trigger_where_clause` on existing trigger line

New terminal:
- `field_ref = content:word '.' field:word` — e.g., `completion.prompt`

Existing lines modified:
- `compute_trigger_line` extended with optional `where` + backtick expression
