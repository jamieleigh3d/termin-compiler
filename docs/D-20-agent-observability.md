# D-20: Agent Observability (G8)

**Status:** Design complete, implementation pending
**Date:** April 2026
**Authors:** Jamie-Leigh Blake & Claude Anthropic
**Depends on:** D-18 (Audit declarations), D-05 (Compute access)

---

## Summary

Standardized trace logging for Compute executions (AI agents, CEL functions, transforms). Every Compute gets a compiler-generated audit log Content table. Traces capture the full invocation lifecycle including the complete system prompt. Access is gated by a new `AUDIT` verb. Trace data is encrypted at rest; redaction is applied in flight based on caller scopes.

---

## Design Decisions

### D-20.1: The `AUDIT` Verb

A fifth verb alongside VIEW, CREATE, UPDATE, DELETE. Gates read access to execution traces.

**DSL syntax (inside a Compute block):**
```
Compute called "order summary":
  ...
  Anyone with "compute.audit" can audit
```

The `can audit` declaration is only valid inside a Compute block — it implicitly references the current Compute's audit log. Using `can audit` outside a Compute block is a compiler error.

**IR:** `Verb.AUDIT` in the `access_grants` verbs set for the auto-generated audit log Content table.

**Rationale:** "Audit" is a real verb that describes what you're doing. Separate from VIEW because viewing a compute's configuration/results is different from viewing its execution traces, which may contain sensitive intermediate data.

### D-20.2: Auto-Generated Audit Content Per Compute

The compiler creates a companion Content table for each Compute:

```
compute_audit_log_{compute_name}
```

Examples:
- `Compute called "order summary"` gets `compute_audit_log_order_summary`
- `Compute called "security scanner"` gets `compute_audit_log_security_scanner`

The `compute_audit_log_` prefix visually groups audit tables together. The author does not declare these tables — the compiler generates them with a standard schema. They inherit the boundary of their parent Compute.

**Standard fields:**
| Field | Type | Description |
|-------|------|-------------|
| id | auto | Primary key |
| compute_name | text | Qualified name of the Compute |
| invocation_id | text | UUID, unique per invocation |
| trigger | text | What triggered the invocation (event, schedule, API call) |
| started_at | datetime | Invocation start timestamp |
| completed_at | datetime | Invocation end timestamp |
| duration_ms | number | Wall-clock duration |
| outcome | enum | "success", "error", "timeout", "cancelled" |
| total_input_tokens | number | Aggregate input tokens across all LLM calls |
| total_output_tokens | number | Aggregate output tokens across all LLM calls |
| trace | text (JSON) | Full structured trace (see D-20.3) |
| error_message | text | Error details if outcome != "success" |

### D-20.3: One Trace Record Per Invocation

**Decision:** Option A — one record per invocation, with the full trace as a structured JSON field.

**Rationale:** The user's mental model is "what did the agent do when I clicked that button?" — that's one invocation. Splitting into per-LLM-call records requires correlation IDs and parent-child joins that add complexity without matching the user's question.

**System prompt storage:** The full system prompt MUST be stored in the trace, not just a hash. Traces must allow complete reconstruction of what was asked of the LLM and what the LLM did. System prompts may vary based on compute configuration, so each invocation captures the actual prompt used.

**Trace JSON structure:**
```json
{
  "calls": [
    {
      "sequence": 1,
      "model": "claude-sonnet-4-20250514",
      "input_tokens": 1523,
      "output_tokens": 412,
      "system_prompt": "You are an order processing assistant...",
      "user_input": "Process order #1234",
      "thinking": "The user wants to process order 1234. I need to...",
      "response": "Created order #42 for customer Acme Corp",
      "tool_calls": [
        {
          "tool": "execute_tool",
          "name": "create_record",
          "params": {"content": "orders", "data": {"...": "..."}},
          "result": {"id": 42, "status": "created"},
          "duration_ms": 15
        }
      ],
      "duration_ms": 2340
    }
  ],
  "context": {
    "user": "clerk@example.com",
    "role": "warehouse clerk",
    "boundary": "order_processing",
    "trigger_event": "order_created"
  }
}
```

**Note on storage:** The logical model is a Content table row with a `trace` text field containing JSON. How runtimes physically store the trace blob is an implementation detail:
- **Reference runtime:** SQLite (the trace JSON is stored inline in the text column). Durable, debuggable, queryable with `json_extract()`.
- **AWS-native Termin runtime:** Trace blob in S3 (zipped), DynamoDB row stores metadata + S3 pointer. Cost-efficient for large traces.
- **Other runtimes:** Could use any durable store. The conformance contract only tests the logical Content API.

### D-20.4: Encryption at Rest, Redaction in Flight

Trace data contains sensitive information: LLM reasoning, tool call inputs/outputs, user inputs, system prompts, and potentially leaked confidential field values. The security model has two layers:

**Layer 1 — Encryption at rest:** Trace data MUST be encrypted at rest. This is a runtime implementation concern (SQLite encryption, S3 SSE-KMS, disk encryption, etc.). This protects against unauthorized physical access (e.g., a developer with production AWS access manually downloading trace files).

**Layer 2 — Redaction in flight:** When a trace is served to a caller via the API, the runtime redacts confidential field values based on the caller's scopes. A caller with `AUDIT` scope but lacking a field's `confidentiality_scope` sees redacted output. A caller with both `AUDIT` and the field's `confidentiality_scope` sees the full trace.

**What gets redacted:** Any LLM-generated content that could contain leaked field values:
- LLM thinking/reasoning
- LLM response text
- Tool call results (return values from execute_tool)
- Tool call input parameters
- User input (which may quote or reference confidential values)

**What is NEVER redacted (structural elements):**
- JSON keys and field names
- Tool names and action names
- Timestamps, sequence numbers, durations
- Model identifiers
- Token counts
- Outcome status and error types
- The structure of the trace itself

**Conformance contract (minimum guarantee):**

> When a trace record is returned to a caller who holds the `AUDIT` scope but lacks a field's `confidentiality_scope`, the runtime MUST scan all LLM-generated content in the trace (thinking, response, tool call inputs, tool call results, user input) for exact substrings of that field's current value. Matches of 4+ characters MUST be replaced with `[REDACTED:{field_name}]`.

**Why the 4-character minimum:** Short values like "a", "the", "42", "yes" would cause massive over-redaction, making traces unreadable. The minimum length limits false positives while still catching meaningful PII (names, emails, account numbers).

**Why only exact substrings as the conformance minimum:** Fuzzy matching (paraphrase detection, semantic similarity) is beyond what a conformance test can verify deterministically. Production runtimes SHOULD use more sophisticated detection (e.g., AWS Bedrock Guardrails, Azure Content Safety) but this is not a conformance requirement.

**Runtime-specific approaches:**
- **Reference runtime:** Exact substring replacement with 4-char minimum. Simple, testable, good enough for development. Encryption at rest via SQLite WAL mode (no built-in encryption — acceptable for local development).
- **AWS-native Termin runtime:** Bedrock Guardrails PII detection on trace content before serving. S3 SSE-KMS for encryption at rest.
- **Other production runtimes:** Can use any PII detection and encryption mechanism. The conformance test only verifies the minimum exact-match redaction contract.

### D-20.5: Over-Redaction as an Attack Vector

**Threat:** A user with write access to a confidential field could set its value to a structural trace keyword (e.g., `"execute_tool"`, `"CREATE"`, a tool name). The redaction mechanism would then replace every occurrence of that string in the LLM-generated portions of the trace, hiding what the agent actually did.

**Severity:** Low. Requires:
1. Write access to a confidential field (already privileged)
2. Knowledge of trace structure and target keywords
3. Intent to hide audit evidence

**Mitigations (all included in the design):**
1. **Replacement, not removal:** Redacted values become `[REDACTED:field_name]`, making over-redaction visible. An auditor seeing `[REDACTED:salary]` where a tool name should be will investigate.
2. **4-character minimum:** Prevents trivially common values from triggering redaction.
3. **Structural elements are exempt:** Only LLM-generated content is redacted. JSON keys, tool names, timestamps, sequence numbers, and trace structure are never touched regardless of field values. An attacker cannot hide which tools were called or when.
4. **Content audit trail (D-18):** The field write that set the suspicious value is itself audited. The attacker's manipulation is recorded.
5. **Boundary enforcement:** The attacker must be within the same boundary as the confidential content to write to it.
6. **`AUDIT` verb is the primary defense:** If you lack the AUDIT scope, you can't see any trace content at all. Redaction is defense-in-depth for callers who CAN audit but shouldn't see specific confidential values.

**Accepted residual risk:** Sophisticated over-redaction attacks (setting values to common English phrases that appear naturally in agent reasoning) are theoretically possible but require significant effort and are detectable through audit trail correlation.

---

## Implementation Plan

1. **Compiler:** Add `AUDIT` to Verb enum. Add `can audit` syntax to compute blocks. Auto-generate `compute_audit_log_{name}` ContentSchema in lowering for each Compute. Emit access grants from compute's audit access rules.
2. **IR Schema:** Add `AUDIT` to Verb enum. Add `audit_content_ref` field to ComputeSpec pointing to the generated audit table.
3. **Runtime:** After each compute invocation, write a trace record to the audit table (with full system prompt). Apply scope-based redaction on read. Encrypt at rest (runtime-specific).
4. **Conformance:** Test that audit tables exist, AUDIT verb gates access, exact-substring redaction works on LLM content, structural elements are never redacted.

---

## Open Questions (deferred)

- **Trace retention policy:** How long are traces kept? Configurable per-compute or global? (Deferred to implementation)
- **Trace UI:** Display format for traces in the presentation layer. (Related to D-09: Chat component, but traces are broader than chat)
