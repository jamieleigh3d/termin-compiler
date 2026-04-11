# Termin Roadmap Archive

Historical planning artifacts, dependency analyses, and resolved design decisions. Moved from `termin-roadmap.md` to keep the active roadmap focused.

---

## v0.5.0 Dependency Analysis (Resolved April 10, 2026)

What was resolved to ship v0.5.0. All items complete.

### Critical Path (All Done)

```
agent_simple.termin
  ├── D-02: LLM field wiring syntax (DONE)
  ├── D-10: defaults to "user" for enum fields (DONE)
  ├── D-12: LLM structured output convention (DONE)
  ├── G1: Compute system type in CEL (DONE — v0.6)
  └── G4: AI provider integration (DONE)

agent_chatbot.termin
  ├── D-05: Compute access declarations (DONE)
  ├── D-08: Event envelope vs raw record (DONE)
  ├── G3: ComputeContext tool API (DONE)
  ├── G2: Before/After snapshots (DONE — v0.6)
  └── G6: Event trigger for Computes (DONE)
```

---

## Resolved Design Decisions

### D-01: Provider Taxonomy and Access Levels (DECIDED)

Four levels: Level 1 (LLM field-to-field), Level 2 (LLM+context), Level 3 (agent app-scoped), Level 4 (agent config-boundary). Levels 1 and 3 implemented in v0.5.0.

### D-02: LLM Field Wiring Syntax (DECIDED)

`Input from field X.Y` / `Output into field X.Y`. Explicit wiring, no magic inference. See `docs/design-decisions/D-02-llm-field-wiring.md`.

### D-03: Implicit Channels in IR (DECIDED)

Implicit channels inferred from Accesses. Cross-boundary access uses `from` clause. See `docs/design-decisions/D-03-implicit-channels.md`.

### D-04: Events vs Channels (DECIDED)

Distinct composable primitives. Events fire on content changes, channels carry data between boundaries. See `docs/design-decisions/D-04-events-vs-channels.md`.

### D-05: Compute Access Declarations (DECIDED)

`Accesses` replaces Transform shapes for agents. See `docs/design-decisions/D-05-compute-access-declarations.md`.

### D-08: Event Envelope vs Raw Record (DECIDED)

Hybrid approach: record promotion + event.* namespace. See `docs/design-decisions/D-08-event-envelope.md`.

### D-10: Default Field Values for Enums (DECIDED)

`defaults to "user"` works with enum fields. Implemented in v0.4.

### D-12: LLM Response Structured Output (DECIDED)

Forced tool_use with auto-generated schema from output fields. See `docs/design-decisions/D-12-llm-structured-output.md`.

### D-17: Block C Architectural Inputs (DECIDED)

Single process, logical isolation, async queue delivery, enforce channel-only crossing. App is always a boundary. See `docs/design-decisions/D-17-block-c-architecture.md`.

### D-18: Audit Declaration on ContentSchema (DECIDED)

Three levels: actions (default), debug, none. See `docs/design-decisions/D-18-audit-declarations.md`.

### D-19: Dependent Field Values (DECIDED)

When clauses, must be one of/must be/defaults to, unified is-one-of constraint. See `docs/design-decisions/D-19-dependent-field-values.md`.
