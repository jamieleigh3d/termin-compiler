# D-20: Agent Observability (G8)

**Status:** Design complete, implementation pending
**Date:** April 2026
**Authors:** Jamie-Leigh Blake & Claude Anthropic
**Depends on:** D-18 (Audit declarations), D-05 (Compute access)

---

## Summary

Standardized trace logging for Compute executions (AI agents, CEL functions, transforms). Every Compute gets a compiler-generated audit log Content table. Traces capture the full invocation lifecycle. Access is gated by a new `AUDIT` verb. Redaction of confidential field values is a runtime concern with a conformance contract for the minimum guarantee.

---

## Design Decisions

### D-20.1: The `AUDIT` Verb

A fifth verb alongside VIEW, CREATE, UPDATE, DELETE. Gates read access to execution traces.

**DSL syntax:**
```
Anyone with "compute.audit" can audit order summaries
```

**IR:** `Verb.AUDIT` in the `access_grants` verbs set for the audit log Content table.

**Rationale:** "Audit" is a real verb that describes what you're doing. Separate from VIEW because viewing a compute's configuration/results is different from viewing its execution traces, which may contain sensitive intermediate data.

### D-20.2: Auto-Generated Audit Content Per Compute

The compiler creates a companion Content table for each Compute:

```
compute_audit_log_{compute_name}
```

Examples:
- `Compute called "order summary"` gets `compute_audit_log_order_summary`
- `Compute called "security scanner"` gets `compute_audit_log_security_scanner`

The `compute_` prefix visually groups audit tables together. The author does not declare these tables — the compiler generates them with a standard schema. They inherit the boundary of their parent Compute.

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

**Trace JSON structure:**
```json
{
  "calls": [
    {
      "sequence": 1,
      "model": "claude-sonnet-4-20250514",
      "input_tokens": 1523,
      "output_tokens": 412,
      "system_prompt_hash": "sha256:abc123...",
      "tool_calls": [
        {
          "tool": "execute_tool",
          "name": "create_record",
          "params": {"content": "orders", "data": {"...": "..."}},
          "result": {"id": 42, "status": "created"},
          "duration_ms": 15
        }
      ],
      "response_summary": "Created order #42 for customer Acme Corp",
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

### D-20.4: Redaction is a Runtime Concern

The IR declares which fields are confidential (via `confidentiality_scopes` on FieldSpec and ContentSchema). The runtime is responsible for redacting confidential field values from trace output before returning it to callers.

**Conformance contract (minimum guarantee):**

> If a trace record is requested by a caller who holds the `AUDIT` scope but lacks a field's `confidentiality_scope`, and the trace text contains that field's value as an exact substring of 4+ characters, the runtime MUST replace it with `[REDACTED:{field_name}]`.

**Why the 4-character minimum:** Short values like "a", "the", "42", "yes" would cause massive over-redaction, making traces unreadable. The minimum length limits false positives while still catching meaningful PII (names, emails, account numbers).

**Why only exact substrings:** Fuzzy matching (paraphrase detection, semantic similarity) is beyond what a conformance test can verify deterministically. Production runtimes SHOULD use more sophisticated detection (e.g., AWS Bedrock Guardrails, Azure Content Safety) but this is not a conformance requirement.

**Runtime-specific approaches:**
- **Reference runtime:** Exact substring replacement with 4-char minimum. Simple, testable, good enough for development.
- **AWS-native Termin runtime:** Bedrock Guardrails PII detection on the trace blob before storage or retrieval. Catches paraphrased and restructured PII.
- **Other production runtimes:** Can use any PII detection mechanism. The conformance test only verifies the minimum exact-match contract.

### D-20.5: Over-Redaction as an Attack Vector

**Threat:** A user with write access to a confidential field could set its value to a structural trace keyword (e.g., `"execute_tool"`, `"CREATE"`, a tool name). The redaction mechanism would then replace every occurrence of that string in the trace, hiding what the agent actually did.

**Severity:** Low. Requires:
1. Write access to a confidential field (already privileged)
2. Knowledge of trace structure and target keywords
3. Intent to hide audit evidence

**Mitigations (all included in the design):**
1. **Replacement, not removal:** Redacted values become `[REDACTED:field_name]`, making over-redaction visible. An auditor seeing `[REDACTED:salary]` where `execute_tool` should be will investigate.
2. **4-character minimum:** Prevents trivially common values from triggering redaction.
3. **Value-only redaction:** Only field *values* are candidates for redaction. Structural trace elements (field names, tool names, timestamps, sequence numbers, model names) are never redacted regardless of field values.
4. **Content audit trail (D-18):** The field write that set the suspicious value is itself audited. The attacker's manipulation is recorded.
5. **Boundary enforcement:** The attacker must be within the same boundary as the confidential content to write to it.

**Accepted residual risk:** Sophisticated over-redaction attacks (setting values to common English phrases that appear naturally in agent reasoning) are theoretically possible but require significant effort and are detectable through audit trail correlation. The `AUDIT` verb as primary access control is the real defense; redaction is defense-in-depth.

---

## Implementation Plan

1. **Compiler:** Add `AUDIT` to Verb enum. Auto-generate `compute_audit_log_{name}` ContentSchema in lowering for each Compute. Emit access grants from compute's access rules.
2. **IR Schema:** Add `AUDIT` to Verb enum. Add `audit_content_ref` field to ComputeSpec pointing to the generated audit table.
3. **Runtime:** After each compute invocation, write a trace record to the audit table. Apply redaction on read based on caller scopes.
4. **Conformance:** Test that audit tables exist, AUDIT verb gates access, exact-substring redaction works, structural elements are never redacted.

---

## Open Questions (deferred)

- **Trace retention policy:** How long are traces kept? Configurable per-compute or global? (Deferred to implementation)
- **Trace UI:** Display format for traces in the presentation layer. (Related to D-09: Chat component, but traces are broader than chat)
- **System prompt storage:** Should the full system prompt be stored in the trace, or just a hash? (Privacy vs debuggability tradeoff)
