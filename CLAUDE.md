# Termin Project - Claude Memory

## What This Is

Termin is an open-source secure-by-construction application compiler. It takes `.termin` DSL files (formulaic English) and compiles them through a pluggable backend into running applications. The security thesis: applications that compile are structurally immune to OWASP Top 10 vulnerability classes.

The name derives from Determine, Terminus, Terminal, and Lev Termen (inventor of the Theremin).

## Architecture

```
.termin file -> peg_parser.py (TatSu PEG) -> AST -> analyzer -> lower() -> AppSpec (IR) -> Backend -> output
```

The compiler has a pluggable architecture with an explicit Intermediate Representation (IR):

- **Frontend** (termin/): PEG parser (TatSu), analyzer — produces a validated AST
- **IR** (termin/ir.py + termin/lower.py): Lowers AST to AppSpec — fully resolved, immutable, backend-agnostic
- **Backends** (termin/backends/): Read AppSpec and generate output
  - `runtime.py` — Runtime backend: generates slim 24-line app.py + companion IR JSON, uses termin_runtime package
- **Runtime** (termin_runtime/): Python package with 9 modules implementing the runtime subsystems

## Project Layout

```
termin/                     # Compiler package (pip install -e .)
  cli.py                    # Click CLI: termin compile <file> [-o output] [--backend runtime]
  termin.peg                # AUTHORITATIVE PEG grammar (TatSu) — update FIRST before parser changes
  peg_parser.py             # Two-level parser: line classify + TatSu per-line rules
  ast_nodes.py              # Dataclass AST nodes (Program, Content, Role, Directives, etc.)
  analyzer.py               # Semantic analysis + security invariant checks
  errors.py                 # TerminError, ParseError, SemanticError, SecurityError
  ir.py                     # IR dataclasses: AppSpec, ContentSchema, FieldSpec, ComponentNode, PageEntry, etc.
  lower.py                  # Lowering pass: Program AST -> AppSpec IR (component trees)
  backend.py                # Backend protocol + plugin discovery via entry points
  backends/
    runtime.py              # Runtime backend: AppSpec -> slim app.py + companion .json
termin_runtime/             # Runtime package — reads IR JSON, serves the app
  __init__.py               # Exports create_termin_app()
  app.py                    # FastAPI app factory from IR JSON (~400 lines)
  presentation.py           # Component tree renderer (dispatch table of Jinja2 renderers)
  storage.py                # SQLite schema creation + generic CRUD
  expression.py             # Server-side CEL evaluator (cel-python)
  errors.py                 # TerminAtor error router
  events.py                 # EventBus with async queues and log levels
  identity.py               # Role/scope resolution from cookies
  state.py                  # Config-driven state transitions
  reflection.py             # ReflectionEngine from IR JSON
examples/
  hello.termin              # Simplest hello world
  hello_user.termin         # Role-conditional pages + Compute
  warehouse.termin          # Inventory management (full PRFAQ example)
  helpdesk.termin           # Support ticket tracker (multi-word states)
  projectboard.termin       # Project management board (5 content types, deep FK chains)
  compute_demo.termin       # Compute functions + error handling demo
ir_dumps/                   # Pre-compiled IR JSON for each example (used by runtime tests)
tests/
  test_parser.py            # PEG parser tests
  test_analyzer.py          # Semantic + security invariant tests
  test_ir.py                # IR lowering tests (71 tests — component tree assertions)
  test_codegen.py           # Generated code validity tests
  test_runtime.py           # Runtime package tests (19 tests)
  test_e2e.py               # Warehouse end-to-end
  test_helpdesk.py          # Help desk end-to-end
  test_projectboard.py      # Project board end-to-end
docs/
  termin-primitives.md      # The 8 Termin primitives
  termin-presentation-ir-spec-v2.md  # Component tree IR spec
  termin-distributed-runtime-model.md  # Distributed runtime architecture
  termin-ir-schema.json     # JSON Schema (2020-12) — machine-readable IR contract
  termin-runtime-implementers-guide.md  # How to build a Termin runtime from the schema
setup.py                    # Package config, entry_points -> termin CLI
```

## Key Commands

```bash
# Install compiler in dev mode
pip install -e .

# Compile
termin compile examples/warehouse.termin -o app.py

# Dump IR
termin compile examples/warehouse.termin -o app.py --emit-ir warehouse_ir.json

# Run all tests (~11s, 373 tests)
python -m pytest tests/ -v

# Run just compiler tests (no e2e)
python -m pytest tests/test_parser.py tests/test_analyzer.py tests/test_ir.py -v

# Run runtime tests
python -m pytest tests/test_runtime.py -v
```

## Parser: Two-Level PEG Approach

The parser (`peg_parser.py`) uses a two-level design:
1. **Level 1 (Python):** `_preprocess()` strips comments/blanks, `_classify_line()` uses prefix matching to determine which PEG rule applies
2. **Level 2 (TatSu):** `_try_parse()` runs the classified PEG rule against the line text

The PEG grammar (`termin.peg`) is **authoritative** — always update it FIRST before changing the parser.

## IR (Intermediate Representation)

The IR (`AppSpec` in ir.py) is immutable and fully resolved. Key types:

- `ContentSchema` (was Table) with `FieldSpec` (was Column) — `business_type` preserves semantic type
- `AuthSpec` with `RoleSpec` (scopes)
- `StateMachineSpec` with `TransitionSpec`
- `RouteSpec` with resolved `RouteKind`, `target_state`, `required_scope`
- **Presentation v2:** `PageEntry` with `children: tuple[ComponentNode, ...]`
  - `ComponentNode` — typed tree node: `type`, `props`, `style`, `layout`, `children`
  - Component types: text, data_table, form, field_input, section, aggregation, stat_breakdown, chart, filter, search, highlight, subscribe, related, action_button
  - Props use `PropValue(value, is_expr)` for CEL expressions; bare strings for literals
- `EventSpec` with CEL conditions and log levels
- `ComputeSpec`, `ChannelSpec` (Direction/Delivery intents), `BoundarySpec`

Legacy `PageSpec` and `page_entry_to_pagespec()` shim removed in v0.6.

## Runtime Backend

The **runtime backend** is the primary target. It generates:
1. A slim `app.py` (~24 lines) that calls `create_termin_app(IR_JSON)`
2. A companion `.json` file containing the full IR

The `termin_runtime` package reads IR JSON directly and:
- Creates SQLite tables from ContentSchema
- Registers API routes from RouteSpec
- Renders pages by walking the component tree (dispatch-table renderer)
- Evaluates CEL expressions (server-side via cel-python, client-side via CDN)
- Routes errors through TerminAtor

## DSL Grammar Quick Reference

```
Application: {name}
  Description: {text}

Users authenticate with {provider}
Scopes are "scope1", "scope2", and "scope3"
A "{role}" has "scope1" and "scope2"

Content called "{name}":
  Each {singular} has a {field} which is {type_expr}
  Anyone with "{scope}" can {verb} {content}

State for {content} called "{name}":
  A {singular} starts as "{state}"
  A {singular} can also be "{state1}" or "{state2}"
  A {state} {singular} can become {target} if the user has "{scope}"

When [{condition_expr}]:       # v2 CEL event trigger
  Create a {content} with {fields}
  Log level: {TRACE|DEBUG|INFO|WARN|ERROR}

As a {role}, I want to {action} so that {objective}:
  Show a page called "{name}"
  Display a table of {content} with columns: {fields}
  Display text "{literal}" | Display text [{cel}]
  Display count of {content} grouped by {field}    # structured aggregation
  Display sum of [{expr}] from {content} as {format}
  Section "{title}":                                # nesting container
  For each {item}, show actions:                    # action buttons
    "{Label}" transitions to "{state}" if available [, hide otherwise]
  Accept input for {fields}
  Allow filtering by {fields}
  Allow searching by {fields}

Compute called "{name}":
  {Shape}: takes {params}, produces {params}
  [{cel_body}]
  Anyone with "{scope}" can execute this

Channel called "{name}":
  Carries {content}
  Direction: {inbound|outbound|bidirectional|internal}
  Delivery: {realtime|reliable|batch|auto}

Boundary called "{name}":
  Contains {content1}, {content2}, and {content3}
  Exposes property "{name}" : {type} = [{cel}]
```

## Testing Approach

- **Unit tests** (parser, analyzer): Test each compiler stage in isolation
- **IR tests** (test_ir.py): 91 tests verifying component tree structure from all examples
- **Runtime tests** (test_runtime.py): 39 tests for the termin_runtime package (uses pre-compiled IR dumps)
- **Dependency tests** (test_dependencies.py): Scans imports via AST, verifies all third-party packages are in setup.py
- **String iteration guards** (TestNoStringIterationBugs in test_ir.py): Parametrized across all examples — catches single-char field names from string-as-list bugs
- **E2E tests** (test_e2e.py, test_helpdesk.py, test_projectboard.py): Compile + run via FastAPI TestClient
- **IMPORTANT on Windows**: Never use `subprocess.Popen` with `stdout=PIPE` for uvicorn — deadlocks. Always use `TestClient`.
- **IMPORTANT**: After parser/lowering changes, regenerate all IR dumps in ir_dumps/ — runtime tests depend on them.

## Verification Before Declaring Readiness

**"Tests pass" is not "it works."** Before telling JL that a milestone is ready, complete this checklist yourself. Not optional. Not delegatable.

1. **Compile smoke test:** `termin compile examples/warehouse.termin` — must succeed with 0 errors. If this fails, nothing else matters.
2. **All examples compile:** Loop through every `.termin` file in `examples/` and verify compilation.
3. **Full test suite:** `python -m pytest tests/ -v` — 0 failures, 0 skips, 0 xfails.
4. **Git status clean:** Both repos, nothing untracked or uncommitted.
5. **Verify background agent work independently.** Agent says "1399 tests pass" — you still compile an example yourself. Agent output is a claim, not a fact, until you verify it.

Do NOT report readiness based on background agent summaries alone. The fail-loud fallbacks incident (v0.7) proved that passing tests can coexist with a broken compiler when tests use pre-compiled artifacts instead of live compilation.

## Bug Investigation Workflow

When a user reports a bug:
1. **Trace the full pipeline** — check output at each stage: DSL → parser → AST → lowering → IR → renderer → HTML
2. **Check for stale compiled artifacts** — the running app reads compiled JSON, not source code
3. **Root cause before fixing** — find the exact line and mechanism
4. **Write tests that catch the CLASS of error**, parametrized across all examples
5. **Test end-to-end** — regenerate artifacts, verify rendered output, full test suite

## PEG Grammar Pitfalls

The `words` terminal is greedy — it consumes keywords like "or", "with", "as". Use `words_before_X` terminals with negative lookahead. Example: `words_before_or = /\w+(?:\s+(?!or\b)\w+)*/`. Never use magic numbers for string slicing in fallback paths — use `len("prefix")` instead.

## Git Safety Practices

Linear history always — use `git rebase` and `git merge --ff-only`. No merge commits.

1. **Always verify the current branch before destructive operations.** `git branch --show-current` before `git commit --amend`, `git reset`, or `git rebase`. I once amended on main when I meant to amend on a feature branch. The branches had identical HEAD content (post-FF-merge), so the amend succeeded silently on main.
2. **`--theirs` vs `--ours` during rebase is inverted.** During rebase, `--theirs` = the commit being replayed. `--ours` = the branch you're rebasing onto. When resolving version-string conflicts, you typically want the feature branch version = `--theirs`.
3. **Never mention remote AI collaborators by name in commit messages, tag messages, or PR descriptions.** These ship to the public repo. IP boundary. The messages branch is the designated communication channel — attribution belongs there, not in public artifacts.
4. **Delete feature branches locally AND remotely** after merge. `git branch -D feature/vX` then `git push origin --delete feature/vX`.
5. **Rebase the messages branch onto main after every release push.** Otherwise it drifts from main and rebases get harder over time.

## Seed Data

Examples can have companion `_seed.json` files (e.g., `examples/projectboard_seed.json`). The compiler copies them alongside output. The runtime auto-seeds empty tables on first run. Use `--seed custom.json` for explicit seed files.

## Related Projects

- **Termin Studio** (sibling directory termin-studio/): React + Vite visual editor with React Flow
- **Seedling** (Clarity Intelligence): Autonomous AI daemon that uses Termin as its application substrate
