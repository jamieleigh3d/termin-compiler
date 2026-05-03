# D-01: Provider Taxonomy and Access Levels

**Status:** DECIDED — foundational (pre-v0.5.0). Promoted from `termin-roadmap-archive.md` to its own file 2026-05-03 to make the four-level taxonomy discoverable alongside D-02 onward.
**Decided by:** JL + Claude
**Affects:** Provider design, IR schema, compiler, runtime, all LLM/agent compute work

---

## Decision

Compute providers are organized in a four-level taxonomy by access scope and invocation shape. The levels describe a *spectrum of agent autonomy and reach*, from a single deterministic field-completion call (Level 1) up to a cross-boundary agent that operates across multiple apps in a deployment (Level 4).

| Level | Name | Shape | Provider string |
|-------|------|-------|----------------|
| **L1** | LLM, field-to-field | Single-shot prompt → completion. Explicit `Input from field` / `Output into field` wiring. One API call per invocation. No tools. | `Provider is "llm"` |
| **L2** | LLM, with context | LLM call with additional context beyond the explicit input fields (e.g., related records, conversation history, deployment metadata). One API call per invocation. No tools. | *(reserved; never implemented as a distinct provider — see status below)* |
| **L3** | Agent, app-scoped | Multi-turn agent with a tool surface bounded to one application's content. Tool-use loop happens internally to one provider lifecycle. Closed tool surface from `Accesses` / `Reads` / `Sends to` / `Emits` / `Invokes` declarations. | `Provider is "ai-agent"` |
| **L4** | Agent, config-boundary | Agent with cross-boundary access — can reach across multiple apps within a deployment via reflection (`reflect.apps()`) or cross-boundary channel invocation. Same agent loop semantics as L3, but the tool surface includes deployment-level operations. | `Provider is "ai-agent"` with cross-boundary `Accesses` |

---

## Implementation status

| Level | Status | Evidence |
|-------|--------|----------|
| L1 | Implemented in v0.5.0. | `examples/agent_simple.termin`. The `Input from field` / `Output into field` syntax + `Provider is "llm"` are first-class grammar. |
| L2 | **Never implemented as a distinct provider.** Most use cases that motivated L2 are now served by L1's multi-input pattern (multiple `Input from field` declarations, with the runtime composing them into the user message) or by L3 agents that load context via tool calls. The L2 slot remains in the taxonomy as a conceptual placeholder; if a future use case genuinely needs LLM-with-implicit-context-but-no-tools, it can be added without renumbering. |
| L3 | Implemented in v0.5.0. | `examples/agent_chatbot.termin`, the v0.9.3 ARIA design (`termin-v0.9.3-airlock-on-termin-tech-design.md`). Provider string `"ai-agent"`, agent loop with tool-use iteration internal to one provider call, closed tool surface from `Accesses` declarations. |
| L4 | Implemented as a usage pattern of L3, not a distinct provider. | `examples/security_agent.termin` — uses `reflect.apps()` to enumerate all deployed apps and `channel.invoke(...)` for cross-boundary operations. The provider string is still `"ai-agent"`; L4 is what you get when an `"ai-agent"` compute declares cross-boundary `Accesses`. |

**Provider strings in the IR schema:** [`docs/termin-ir-schema.json`](../termin-ir-schema.json) currently enumerates only `"llm"` (L1), `"ai-agent"` (L3), and `null`/`"cel"` (the default deterministic CEL evaluator, which sits *outside* this taxonomy — CEL is not an AI provider). L2 and L4 are conceptual taxonomy slots, not separate provider strings. This is a deliberate choice: the level distinction is informative (it helps authors and reviewers reason about an agent's reach), but the runtime behavior is fully determined by the provider string + the compute's `Accesses` declarations.

---

## Why levels matter (even when only L1 and L3 are real provider strings)

The taxonomy is useful for three audiences:

1. **Authors** asking "what shape do I need for this compute?" — the levels frame the choice. A field summarizer is L1. A chatbot is L3. A deployment-wide security scanner is L4 (which compiles to L3 with broader `Accesses`).
2. **Reviewers** auditing a `.termin` source. The level helps them decide how much scrutiny the compute warrants. L1 is bounded; L4 has the deployment as its tool surface and deserves the most attention.
3. **Conformance authors** specifying compute behavior. The contract surface for L1 (single LLM call, structured output, no tool loop) is genuinely different from L3 (agent loop, tool surface, refusal as termination). [`compute-contract.md`](../../../termin-conformance/specs/compute-contract.md) §2 names `default-CEL`, `llm`, and `ai-agent` as its three contracts — the level taxonomy is what makes those names mean something.

---

## Relationship to other design decisions

- **[D-02 — LLM Field Wiring, Prompt Syntax, and Trigger Filtering](D-02-llm-field-wiring.md)** — defines the `Input from field` / `Output into field` syntax that L1 uses, and the `Directive` / `Objective` prompt fields shared by L1 and L3.
- **[D-05 — Compute Access Declarations](D-05-compute-access-declarations.md)** — defines the `Accesses` declarations that determine L3's tool surface (and, when cross-boundary, L4's broader reach).
- **[D-12 — LLM Structured Output](D-12-llm-structured-output.md)** — applies to both L1 and L3; both use the auto-generated `set_output` tool for structured field assignment.
- **[D-20 — Agent Observability](D-20-agent-observability.md)** — the audit surface; trace shape differs between L1 (single completion) and L3/L4 (agent loop with tool calls). The polymorphic trace envelope handles both.
- **`termin-v0.9.2-conversation-field-type-tech-design.md`** — the conversation field type is an L3-shaped affordance; L1 doesn't use it. The provider Protocol updates in v0.9.2 only affect L3 (and any future L2/L4 implementations).

---

## What this does NOT cover

- **The `default-CEL` provider.** CEL is the deterministic expression evaluator and sits outside the AI provider taxonomy entirely. It is its own thing. L1–L4 specifically describe AI providers.
- **Choice of model within a level.** Which Claude / OpenAI / Bedrock model an `"llm"` or `"ai-agent"` compute uses is a deploy-config concern, not a level distinction. Sonnet vs Opus vs Haiku does not change the level.
- **Streaming vs request-response.** Both are supported within both L1 and L3 per the streaming protocol; not a level distinction.
- **Single-tenant vs multi-tenant deployments.** The taxonomy is per-compute; the deployment model is orthogonal. An L3 agent in a multi-tenant deployment is still L3.

---

## Reserved: when L2 might become a real provider

If a future use case genuinely needs *LLM call + automatic context loading + no tool loop*, a `"llm-with-context"` provider could be added at L2. Candidates that have come up in informal discussion:

- A summarizer that needs to read several related records but doesn't need agent autonomy.
- A classifier that needs deployment-level configuration injected as context.

The current workarounds (multi-input L1, or simple L3 with a single tool call) are good enough for these cases. L2 stays a slot, not a feature.

---

## Reserved: when L4 might become a distinct provider

If cross-boundary agents grow features that L3 agents shouldn't have (deployment-wide quotas, cross-app rate limits, special audit semantics for cross-boundary writes), a separate `"ai-agent-cross-boundary"` provider could fork off. Today the distinction is policy-only — a cross-boundary `Accesses` declaration is the marker — and that is fine.
