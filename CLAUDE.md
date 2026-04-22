# Termin Compiler — Developer Context

This file is for Claude Code sessions (and human contributors) working in this
repository. It captures architectural context, release process, and the
hard-won lessons about what "ready to ship" actually requires for the Termin
compiler and reference runtime.

See `CONTRIBUTING.md` for the DCO sign-off requirement and general
contribution workflow.

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

# Run all tests (v0.8.1: 1525 tests)
python -m pytest tests/ -v

# Run just compiler tests (no e2e)
python -m pytest tests/test_parser.py tests/test_analyzer.py tests/test_ir.py -v

# Run runtime tests
python -m pytest tests/test_runtime.py -v

# Coverage (target 95% on new code)
python -m pytest tests/ --cov --cov-report=term-missing
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

> **Note:** The snippet below is a v0.5-era cheat sheet. For the authoritative grammar including v0.6+ additions (compound verbs, AUDIT, trigger-where, streaming, deletes/edits/inline-editing, agent primitives with Directive/Objective, confidentiality), read `docs/termin-dsl-grammar-v2.md` and `termin/termin.peg`. When in doubt, open an actual file in `examples/` and copy the pattern — do not invent syntax from memory.

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

**"Tests pass" is not "it works."** Before declaring a milestone ready, complete this checklist yourself. Not optional. Not delegatable.

1. **Compile smoke test:** `termin compile examples/warehouse.termin` — must succeed with 0 errors. If this fails, nothing else matters.
2. **All examples compile:** Loop through every `.termin` file in `examples/` and verify compilation. (The release script does this; see below.)
3. **Full test suite:** `python -m pytest tests/ -v` — 0 failures, 0 skips, 0 xfails.
4. **Git status clean:** Both repos, nothing untracked or uncommitted.
5. **Verify background agent work independently.** Agent says "1525 tests pass" — you still compile an example yourself. Agent output is a claim, not a fact, until you verify it.

Do NOT report readiness based on background agent summaries alone. The fail-loud fallbacks incident (v0.7) proved that passing tests can coexist with a broken compiler when tests use pre-compiled artifacts instead of live compilation.

## Release Process (CRITICAL — the v0.8.0 lesson)

**Never create a tag before `util/release.py` has run end-to-end and every test suite is green.** v0.8.0 shipped with stale conformance `fixtures/ir/*.json` (warehouse was missing 132 lines) and a missing `edit_modal` entry in `docs/termin-ir-schema.json` because the release script wasn't run before tagging. v0.8.1 is the correction. The CHANGELOG v0.8.0 "Release-process note" documents this publicly so the history carries the lesson.

Correct order:
1. Manual updates first: `CHANGELOG.md` (both repos), `README.md` test counts, `docs/termin-roadmap.md`.
2. Run `python util/release.py --compiler-version X.Y.Z --ir-version X.Y.Z` — this compiles all examples, extracts IR, regenerates `ir_dumps/`, repackages `.termin.pkg` files, syncs fixtures and the IR schema to the conformance repo.
3. Run all three suites **directly** (not through the release script):
   - Compiler: `python -m pytest tests/ -v`
   - Conformance (HTTP): `cd ../termin-conformance && TERMIN_ADAPTER=reference pytest tests/ -v`
   - Conformance (browser): `cd ../termin-conformance && TERMIN_ADAPTER=served-reference pytest tests/test_v08_browser.py -v` (requires Playwright)
4. Review diffs, commit both repos on feature branch.
5. Merge to main with `git merge --ff-only` (linear history).
6. Tag only after all of the above is green and both working trees are clean.
7. Push main + tags to both remotes.

**Windows contributors:** pass `--skip-tests` to the release script. Its internal pytest run hangs when wrapped in `subprocess.run(..., capture_output=True)` on Windows — the subprocess completes (CPU time matches a clean run) but the parent never sees exit. Kill the subprocess to flush the output. Run the test suites directly as in step 3 instead. Tracked as a v0.8.2 backlog item.

## Bug Investigation Workflow

When a user reports a bug:
1. **Trace the full pipeline** — check output at each stage: DSL → parser → AST → lowering → IR → renderer → HTML
2. **Check for stale compiled artifacts** — the running app reads compiled JSON, not source code
3. **Root cause before fixing** — find the exact line and mechanism
4. **Write tests that catch the CLASS of error**, parametrized across all examples
5. **Test end-to-end** — regenerate artifacts, verify rendered output, full test suite

## PEG Grammar Pitfalls

The `words` terminal is greedy — it consumes keywords like "or", "with", "as". Use `words_before_X` terminals with negative lookahead. Example: `words_before_or = /\w+(?:\s+(?!or\b)\w+)*/`. Never use magic numbers for string slicing in fallback paths — use `len("prefix")` instead.

## Git Discipline

- **Linear history.** Use `git rebase` to integrate and `git merge --ff-only`
  for feature → main. No merge commits.
- **DCO sign-off** required on every commit (`git commit -s`). See
  `CONTRIBUTING.md`.
- **Verify the current branch before destructive operations.**
  `git branch --show-current` before `git commit --amend`, `git reset`,
  `git rebase`. Amending on the wrong branch is easy to do when multiple
  branches share a HEAD content after a fast-forward merge.
- **`--theirs` during a feature-branch → main rebase** is almost always what
  you want for version-string conflicts (the feature branch's higher
  version wins over main's lower one). This is counter-intuitive: `--theirs`
  means "the commit being replayed" during rebase, not "the other side of a
  merge."
- **Delete feature branches locally AND remotely after merge**:
  `git branch -D feature/vX && git push origin --delete feature/vX`.
- **Never skip hooks** (`--no-verify`) without a concrete reason. If a
  pre-commit hook fails, fix the underlying issue rather than bypassing.

## Seed Data

Examples can have companion `_seed.json` files (e.g., `examples/projectboard_seed.json`). The compiler copies them alongside output. The runtime auto-seeds empty tables on first run. Use `--seed custom.json` for explicit seed files.

## Related Repositories

- **`github.com/jamieleigh3d/termin-conformance`** — conformance test suite,
  IR JSON schema, Runtime Implementer's Guide, and `.termin.pkg` fixtures.
  Any Termin runtime must pass this suite. The compiler's `util/release.py`
  regenerates fixtures in that repo as part of each release.
- **`termin.dev`** — project website with overview, guarantees, and roadmap.

## See Also

- `CONTRIBUTING.md` — DCO sign-off, local dev setup, PR workflow.
- `CODE_OF_CONDUCT.md` — contributor expectations.
- `SECURITY.md` — security disclosure policy.
- `docs/termin-runtime-implementers-guide.md` — how to build a conforming
  runtime.
- `docs/termin-roadmap.md` — active backlog and version history.
