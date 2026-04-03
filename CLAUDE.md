# Termin Project - Claude Memory

## What This Is

Termin is an open-source secure-by-construction application compiler. It takes `.termin` DSL files (formulaic English) and compiles them through a pluggable backend into running applications. The reference backend generates a single self-contained `app.py` (FastAPI + SQLite + HTMX + Tailwind). The security thesis: applications that compile are structurally immune to OWASP Top 10 vulnerability classes.

The name derives from Determine, Terminus, Terminal, and Lev Termen (inventor of the Theremin).

## Architecture

```
.termin file -> lexer -> parser -> AST -> analyzer -> lower() -> AppSpec (IR) -> Backend -> output
```

The compiler has a pluggable architecture with an explicit Intermediate Representation (IR):

- **Frontend** (termin/): lexer, parser, analyzer — produces a validated AST
- **IR** (termin/ir.py + termin/lower.py): Lowers AST to AppSpec — fully resolved, immutable, backend-agnostic
- **Backend** (termin/backends/): Reads AppSpec and generates output. Reference backend: FastAPI+SQLite+HTMX

## Project Layout

```
termin/                     # Compiler package (pip install -e .)
  cli.py                    # Click CLI: termin compile <file> [-o output] [--backend fastapi]
  lexer.py                  # Line-oriented tokenizer -> Token stream
  parser.py                 # Recursive descent -> AST (Program node)
  ast_nodes.py              # Dataclass AST nodes (Program, Content, Role, etc.)
  analyzer.py               # Semantic analysis + security invariant checks
  errors.py                 # TerminError, ParseError, SemanticError, SecurityError
  ir.py                     # IR dataclasses: AppSpec, Table, Column, RouteSpec, PageSpec, etc.
  lower.py                  # Lowering pass: Program AST -> AppSpec IR
  backend.py                # Backend protocol + plugin discovery via entry points
  backends/
    fastapi.py              # Reference backend: AppSpec -> single Python file
examples/
  warehouse.termin          # Inventory management (full PRFAQ example)
  helpdesk.termin           # Support ticket tracker (multi-word states)
  projectboard.termin       # Project management board (5 content types, deep FK chains)
tests/
  test_lexer.py             # Token stream tests
  test_parser.py            # AST construction tests
  test_analyzer.py          # Semantic + security invariant tests
  test_ir.py                # IR lowering tests (28 tests across all 3 examples)
  test_codegen.py           # Generated code validity tests
  test_e2e.py               # Warehouse end-to-end (Spec Section 8 validation)
  test_helpdesk.py          # Help desk end-to-end (30 tests)
  test_projectboard.py      # Project board end-to-end (37 tests)
docs/
  termin-primitives.md      # The 8 Termin primitives (Content, Compute, Channels, etc.)
  an AWS-native Termin runtime_MVP_Spec.md         # Original MVP specification (reference)
  an AWS-native Termin runtime_PRFAQ_v2.md         # Original PR/FAQ (reference)
  UI-testing.md             # Browser UI testing guide
setup.py                    # Package config, entry_points -> termin CLI
```

## Key Commands

```bash
# Install compiler in dev mode
pip install -e .

# Compile a .termin file
termin compile examples/warehouse.termin -o app.py
termin compile examples/helpdesk.termin -o helpdesk_app.py --backend fastapi

# Run the generated app
pip install fastapi uvicorn aiosqlite jinja2 python-multipart
python app.py                    # default port 8000
python helpdesk_app.py -p 8001   # custom port

# Run all tests (fast, ~2s)
python -m pytest tests/ -v

# Run just unit tests (no compilation needed)
python -m pytest tests/ -v --ignore=tests/test_e2e.py --ignore=tests/test_helpdesk.py --ignore=tests/test_projectboard.py

# Run e2e tests (compiles fresh, uses FastAPI TestClient in-process)
python -m pytest tests/test_e2e.py tests/test_helpdesk.py tests/test_projectboard.py -v
```

## Compiler Pipeline

```
.termin file -> lexer.py (tokenize) -> parser.py (AST) -> analyzer.py (validate) -> lower.py (IR) -> backend (emit) -> app.py
```

1. **Lexer**: Line-oriented. Classifies lines by keyword patterns (regex). Strips `---` comment lines and blanks. Returns `list[Token]`.
2. **Parser**: Recursive descent. Each DSL section has a `_parse_*` method. Builds a `Program` AST node. Supports multi-word state names.
3. **Analyzer**: Two passes - semantic validation then security invariants. Returns `CompileResult` with errors.
4. **Lower**: Transforms validated AST into AppSpec IR. All inference happens here: name resolution, type mapping, scope resolution, verb-to-state mapping, reference display column resolution, filter type classification.
5. **Backend**: Reads AppSpec IR and emits output. Reference backend generates FastAPI+SQLite+HTMX single-file app.

## IR (Intermediate Representation)

The IR (`AppSpec` in ir.py) is immutable (`@dataclass(frozen=True)`) and fully resolved. Key types:

- `Table` with `Column` (type, constraints, FK references)
- `AuthSpec` with `RoleSpec` (scopes)
- `StateMachineSpec` with `TransitionSpec`
- `RouteSpec` with resolved `RouteKind`, `target_state`, `required_scope`
- `PageSpec` with resolved `FormField`, `FilterField`, `AggregationSpec`
- `EventSpec` with resolved `column_mapping`

Backends never need to do inference — they read pre-resolved data.

## Backend Protocol

```python
class Backend(Protocol):
    name: str
    def generate(self, spec: AppSpec, source_file: str = "") -> str: ...
    def required_dependencies(self) -> list[str]: ...
```

Backends register via Python entry points (`termin.backends` group).

## Testing Approach

- **Unit tests** (lexer, parser, analyzer, codegen): Test each compiler stage in isolation.
- **IR tests** (test_ir.py): Verify lowering produces correct IR for all 3 examples.
- **E2E tests** (test_e2e.py, test_helpdesk.py, test_projectboard.py): Compile examples, import generated module, use FastAPI TestClient for in-process HTTP testing.
- **IMPORTANT on Windows**: Never use `subprocess.Popen` with `stdout=PIPE` to start the uvicorn server — the pipe buffer fills and deadlocks. Always use `TestClient` for in-process testing.

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

When a {content} is {created|updated|deleted} and {condition}:
  {action}

As a {role}, I want to {action}
  so that {objective}:
    Show a page called "{name}"
    Display a table of {content} with columns: {fields}
    Display text "{static text}"
    Accept input for {fields}
    ...

As anonymous, I want to see a page "{name}" so that {objective}:
  Display text "{greeting}"

Compute called "{name}":
  {Shape}: takes {content}, produces {content}
  {body description}
  Anyone with "{scope}" can execute this

Channel called "{name}":
  Carries {content}
  Protocol: {REST|SSE|WebSocket|webhook|pubsub|internal}
  From {source} to {destination}
  Endpoint: {path}
  Requires "{scope}" to {send|receive}

Boundary called "{name}":
  Contains {content1}, {content2}, and {content3}
  Identity inherits from {parent}
  Identity restricts to "{scope}"

Navigation bar:
  "{label}" links to "{page}" visible to {roles}

Expose a REST API at {path}:
  {METHOD} {path} {description}

Stream {description} at {path}
```

## Related Projects

- **an AWS-native Termin runtime** (enterprise internal, separate repo): enterprise's backend implementation using the Termin IR.
- `docs/termin-primitives.md`: The full 8-primitive model (Content, Compute, Channels, Boundaries, State, Events, Identity, Presentation).
