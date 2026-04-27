# Termin

**Secure-by-construction application platform.**

Termin is a DSL (Domain-Specific Language) compiler and runtime that produces working business applications from natural-language-like specifications. Security properties --- access control, state machine enforcement, field-level confidentiality --- are structural, not bolted on. If it compiles, it's enforced.

```termin
Content called "employees":
  Each employee has a name which is text, required
  Each employee has a salary which is currency, confidentiality is "access_salary"
  Each employee has a ssn which is text, confidentiality is "access_pii"
  Anyone with "view_employees" can view employees
  Anyone with "manage_hr" can create or update employees
```

A manager viewing this content sees names and departments. Salary and SSN fields are automatically redacted to `{"__redacted": true, "scope": "access_salary"}` --- no application code, no forgotten access checks.

## Quick Start

```bash
pip install -r requirements.txt

# Compile a .termin file to a deployable package
python -m termin.cli compile examples/helpdesk.termin

# Serve the compiled package
python -m termin.cli serve helpdesk.termin.pkg
```

The app runs at `http://localhost:8100` with stub authentication (role selector in the UI).

## What Termin Does

You write a `.termin` file describing your application in structured English. The compiler:

1. **Parses** the DSL via a PEG grammar (TatSu)
2. **Analyzes** for semantic correctness and security invariants
3. **Lowers** to an intermediate representation (IR) --- a fully-resolved JSON structure
4. **Packages** into a `.termin.pkg` (ZIP archive with manifest, IR, source, checksums)

Any conforming runtime reads the IR and produces a running application with:

- **Persistent storage** (SQLite in the reference runtime)
- **REST API** with scope-checked CRUD
- **State machines** with transition enforcement
- **Field-level confidentiality** with automatic redaction
- **Server-side Compute** (CEL expressions) with taint tracking
- **Presentation layer** (server-rendered HTML with Jinja2)
- **Real-time updates** (WebSocket + SSE)
- **Reflection API** for operational visibility

## What Termin is not

- **Not a general-purpose programming language.** The expressive surface is deliberately narrow. Anything outside that surface is either not expressible or has to cross a Channel to an external system.
- **Not a code generator you then extend by hand.** The runtime consumes the compiled IR directly; there is no per-application scaffolding to edit.
- **Not a database.** The reference runtime uses SQLite; alternative runtimes can use whatever storage is appropriate for the deployment.
- **Not a UI toolkit.** The presentation layer is declarative and rendered server-side. Rich client-side interactivity is outside its scope.
- **Not a commercial product.** Apache 2.0, no paid tier, no hosted offering. See [termin.dev](https://termin.dev) for the project's guarantees and non-monetization posture.

## Project Structure

```
termin/                  Compiler package
  termin.peg               PEG grammar (authoritative)
  peg_parser.py            Two-level parser (line classification + PEG)
  ast_nodes.py             AST node definitions
  analyzer.py              Semantic analysis + security invariant checks
  lower.py                 AST -> IR lowering pass
  ir.py                    IR dataclass definitions
  cli.py                   CLI (compile, serve)

termin_runtime/          Reference runtime
  app.py                   FastAPI app factory
  confidentiality.py       Field redaction + Compute checks (Checks 1-4)
  expression.py            CEL expression evaluator (cel-python)
  presentation.py          Component tree -> Jinja2 HTML renderer
  identity.py              User identity + scope resolution
  storage.py               SQLite storage adapter
  state.py                 State machine transition enforcement
  errors.py                TerminAtor error router
  events.py                Event bus for reactive side-effects
  reflection.py            IR introspection API

examples/                Example .termin applications
  hello.termin               Minimal "Hello World"
  warehouse.termin           Inventory management with state machine
  helpdesk.termin            Ticket tracker with roles and transitions
  projectboard.termin        Kanban board with seed data
  hrportal.termin            HR system with field-level confidentiality

docs/                    Specifications and design documents
  termin-ir-schema.json      JSON Schema (draft 2020-12) for IR v0.9.0
  termin-runtime-implementers-guide.md
  termin-confidentiality-brd.md
  termin-confidentiality-spec.md
  termin-confidentiality-runtime-design.md
  termin-package-format.md
  termin-roadmap.md
  ...and more

tests/                   pytest suite
```

## The Confidentiality System

Termin's headline security feature. Fields declare their sensitivity in the DSL:

```termin
Each employee has a salary which is currency, confidentiality is "access_salary"
```

The runtime automatically:
- **Redacts** restricted fields in API responses (`{"__redacted": true, "scope": "access_salary"}`)
- **Gates** Compute invocations (4 defense-in-depth checks)
- **Enforces** output taint propagation through service-identity Computes
- **Blocks** writes to fields the caller can't see
- **Renders** `[REDACTED]` in HTML table cells

Content-level scoping (`Scoped to "access_salary"`) gates entire content types. Field-level and content-level scopes combine with AND semantics. See `docs/termin-confidentiality-brd.md` for the full design.

## Conformance Suite

The [termin-conformance](https://github.com/jamieleigh3d/termin-conformance) repository contains 788 behavioral tests (778 HTTP + 10 Playwright browser) that validate any conforming runtime. Tests use an adapter pattern --- swap the adapter to test your runtime without changing a single test. The `served-reference` adapter launches the runtime on a real localhost port for browser-automation tests; the default in-process `reference` adapter runs the HTTP suite in under 30 seconds.

## IR and Package Format

The compiled IR (`AppSpec`) is a JSON document conforming to `docs/termin-ir-schema.json`. It contains everything a runtime needs: content schemas, access grants, state machines, routes, component trees, compute definitions, and confidentiality metadata.

The `.termin.pkg` is a ZIP archive containing:
- `manifest.json` --- metadata, versioning, checksums
- `*.ir.json` --- compiled IR
- `*.termin` --- source DSL
- Optional seed data and static assets

See `docs/termin-package-format.md` for the full specification.

## Expression Language

Termin uses [CEL (Common Expression Language)](https://github.com/google/cel-spec) for all expressions --- highlights, defaults, compute bodies, event conditions. CEL is non-Turing-complete, formally specified, and has matching implementations in Python, JavaScript, Rust, and Go.

```termin
Each ticket has a submitted by which is text, defaults to [User.Name]
Highlight rows where [priority == "critical" || priority == "high"]
[team_bonus = sum(employees.salary * employees.bonus_rate)]
```

## Development

```bash
# Install runtime + test deps (adds pytest, pytest-asyncio, pytest-cov)
pip install -e ".[test]"

# Run tests
python -m pytest tests/ -v

# Run a specific example
python -m termin.cli compile examples/warehouse.termin
python -m termin.cli serve warehouse.termin.pkg
```

`requirements.txt` carries runtime dependencies only — sufficient to
compile and serve apps. The test suite needs additional packages
(notably `pytest-asyncio` for the async runtime / WebSocket tests),
which live in the `[test]` extras of `setup.py`. The editable install
above pulls them in along with the package itself. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the full developer setup.

## Learn more

- **[termin.dev](https://termin.dev)** --- project overview, guarantees, roadmap, changelog
- **[Conformance suite](https://github.com/jamieleigh3d/termin-conformance)** --- tests any conforming runtime must pass
- **[Issues](https://github.com/jamieleigh3d/termin-compiler/issues)** --- bug reports, feature requests, design discussion

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Contributions require a Developer Certificate of Origin (DCO) sign-off on each commit.

## Authors

**Jamie-Leigh Blake**
**Claude Anthropic** --- coauthor

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for the list of everyone who has contributed to the project.

## License

Apache License 2.0. See [LICENSE](LICENSE) for the full text and [NOTICE](NOTICE) for attribution.
