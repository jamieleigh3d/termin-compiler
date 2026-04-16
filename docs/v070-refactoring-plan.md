# v0.7.0 Refactoring Plan

**Goal:** Every source file under 500 lines, ideally under 300. Test files are excluded — large test files are acceptable because they're flat lists of independent test cases.

**Principle:** Readability matters for AI and humans. Even with 1M context, smaller files mean fewer tokens to edit, fewer merge conflicts, clearer separation of concerns, and fewer errors from editing the wrong section of a 2000-line function.

---

## Priority 1: Critical (over 1000 lines)

### `termin_runtime/app.py` — 2105 lines

The worst offender. One massive `create_termin_app()` function with 25+ inline sections. Everything from WebSocket management to page rendering to state transitions to audit logging lives in one function.

**Proposed split:**

| New module | Lines (est.) | Responsibility |
|------------|-------------|----------------|
| `app.py` | ~200 | `create_termin_app()` shell: subsystem init, lifespan, app return |
| `routes.py` | ~300 | Auto-CRUD route generation (D-11), API route registration |
| `transitions.py` | ~150 | `/_transition` endpoint, feedback lookup, flash params |
| `compute_runner.py` | ~250 | Compute invocation, agent execute_tool, audit trace writing |
| `websocket.py` | ~200 | ConnectionManager, WS multiplexer, subscription handling |
| `pages.py` | ~300 | Page route generation, data requirements extraction, form POST handling |
| `boundaries.py` | ~150 | Boundary containment map, identity checks, boundary access |
| `validation.py` | ~150 | D-19 dependent values, enum/min/max constraints, mass assignment protection |

**Approach:** Extract each `# ──` section into its own module. The main `create_termin_app()` calls setup functions from each module, passing shared state (db_path, ir, sm_lookup, event_bus, etc.) via a context object or function parameters.

### `termin/peg_parser.py` — 1345 lines

Two-level parser with line classification, TatSu helpers, AST builders, per-rule handlers, and block assembly.

**Proposed split:**

| New module | Lines (est.) | Responsibility |
|------------|-------------|----------------|
| `peg_parser.py` | ~150 | Public API: `parse_peg()`, preprocessing, block assembly |
| `classify.py` | ~100 | `_classify_line()` — line-to-rule mapping |
| `parse_helpers.py` | ~150 | TatSu helpers (`_qs`, `_ql`, `_cl`, `_fq`, etc.), type parsing |
| `parse_builders.py` | ~200 | `_build_access`, `_build_trans`, `_build_feedback`, `_build_story`, etc. |
| `parse_handlers.py` | ~350 | `_parse_line()` — the big dispatch function |
| `parse_content.py` | ~150 | Content-specific parsing: When clauses, dependent values, constraints |

### `termin/lower.py` — 1096 lines

One massive `lower()` function with 15+ inline sections.

**Proposed split:**

| New module | Lines (est.) | Responsibility |
|------------|-------------|----------------|
| `lower.py` | ~200 | `lower()` shell, naming helpers, type mapping |
| `lower_content.py` | ~200 | Content schema lowering, field specs, access grants |
| `lower_routes.py` | ~200 | Auto-CRUD route generation, state transition routes |
| `lower_pages.py` | ~250 | Page/component tree lowering, directive → ComponentNode |
| `lower_compute.py` | ~150 | Compute lowering, audit log generation |

---

## Priority 2: High (500–1000 lines)

### `termin_runtime/channels.py` — 826 lines

WebSocket client, HTTP client, channel lifecycle, metrics.

**Proposed split:**

| New module | Lines (est.) | Responsibility |
|------------|-------------|----------------|
| `channels.py` | ~150 | Channel manager, lifecycle, dispatch |
| `channel_ws.py` | ~200 | WebSocket channel client (connect, reconnect, send/receive) |
| `channel_http.py` | ~150 | HTTP channel client (webhook send, retry) |
| `channel_actions.py` | ~150 | Action invocation, typed RPC |

### `termin/analyzer.py` — 780 lines

Semantic analysis with 30+ check functions.

**Proposed split:**

| New module | Lines (est.) | Responsibility |
|------------|-------------|----------------|
| `analyzer.py` | ~150 | `analyze()` entry point, check orchestration |
| `checks_content.py` | ~200 | Content checks: access rules, fields, boundaries, duplicates |
| `checks_compute.py` | ~150 | Compute checks: shape, accesses, providers |
| `checks_state.py` | ~100 | State machine checks: reachability, transition validity |
| `checks_presentation.py` | ~100 | Page checks: slug uniqueness, reserved words |

### `termin_runtime/presentation.py` — 599 lines

Component renderers and template builders.

**Proposed split:**

| New module | Lines (est.) | Responsibility |
|------------|-------------|----------------|
| `presentation.py` | ~100 | `render_component()` dispatch, base template |
| `renderers.py` | ~300 | Individual component renderers (data_table, form, chat, etc.) |
| `templates.py` | ~150 | Page template building, merged templates |

---

## Priority 3: Medium (under 500 but worth noting)

| File | Lines | Notes |
|------|-------|-------|
| `termin_runtime/ai_provider.py` | 475 | Could split Anthropic vs OpenAI into separate files |
| `termin/ast_nodes.py` | 473 | All dataclasses — flat structure is fine, no split needed |
| `termin/ir.py` | 443 | All dataclasses — flat structure is fine, no split needed |
| `termin/cli.py` | 412 | Single CLI module — borderline, could extract package building |

**Recommendation:** `ast_nodes.py` and `ir.py` are pure dataclass definitions. Large but simple. Leave them. `ai_provider.py` and `cli.py` are close to the line — address if they grow.

---

## Test files (not in scope for refactoring)

Test files over 500 lines are normal — they're flat lists of independent test cases grouped by feature. Large test files don't have the same readability/editability issues as large source files because each test is self-contained. No refactoring needed.

---

## Implementation approach

1. **One module at a time.** Extract, test, commit. Don't refactor everything at once.
2. **Preserve public API.** `create_termin_app()` stays as the entry point. `parse_peg()` stays as the parser entry. `lower()` stays as the lowering entry. Internal modules are implementation details.
3. **Shared state via parameters, not globals.** The extracted modules receive what they need as arguments. No module-level mutable state.
4. **Test after every extraction.** Run full suite. If tests break, the extraction changed behavior.
5. **Start with app.py.** It's the worst and most impactful. The others can wait for v0.8 if needed.
