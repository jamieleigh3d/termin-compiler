# Termin DSL Grammar Specification

**Version:** 0.2.1
**Status:** Authoritative grammar is `termin.peg` in the compiler source; this document is a human-readable companion. Updated 2026-04-18 to reflect the v0.4.0 bracket-to-backtick migration, v0.7.0 verb/API changes, and current Channel and Compute syntax.

---

## Design Principles

The Termin DSL is formulaic English. It is structured enough for a deterministic compiler to parse and readable enough for a domain expert to review and modify. There is no AI in the compilation pipeline.

The grammar prioritizes:

- **Readability over conciseness.** A Termin file should read like a requirements document.
- **Determinism over flexibility.** Each construct has one way to express it. The compiler never guesses intent.
- **Declarative over imperative.** You describe what the application does, not how it does it.
- **Accessibility over grammatical purity.** The DSL is not English. It uses English-like structure but does not require perfect English grammar. Non-native speakers should not be disadvantaged. Articles (`a`/`an`) are interchangeable and never cause a parse error.
- **Expressions are code, not prose.** All executable expressions, conditions, and Compute bodies use CEL syntax inside backticks: single backticks for inline expressions, triple backticks for multi-line directive and objective blocks. Natural language is for declarations and structure. CEL is for logic.

---

## File Structure

A `.termin` file is a UTF-8 text document. It consists of a **header** followed by **sections**. Sections may appear in any order. Sections may be omitted.

Minimal valid file:

```
Application: Hello World
  Description: The simplest possible Termin application

As anonymous, I want to see a page "Hello" so that I can be greeted:
  Display text "Hello, World"
```

---

## Lexical Rules

### Encoding and Line Endings

Files must be UTF-8. Both LF and CRLF line endings are accepted.

### Indentation

Indentation is significant. Each level of nesting is two spaces. Tabs are not permitted.

### Comments

Comments are enclosed in parentheses `()`. They may appear on their own line or at the end of a line.

```
(This is a standalone comment.)
Users authenticate with stub (Inline comment here.)
```

Comments cannot appear inside backtick-delimited expressions. Inside backticks, use CEL comment syntax: `//` for line comments.

### Expressions (CEL Blocks)

All executable expressions, conditions, and Compute bodies are enclosed in backticks. Content inside backticks is parsed as CEL, not as Termin DSL.

Single backticks are used for inline expressions:

```
Display text `SayHelloTo(User.Name)`
When `stockLevel.updated && stockLevel.quantity <= stockLevel.reorderThreshold`:
```

Triple backticks are used for multi-line blocks, such as a Compute's `Directive is` or `Objective is`:

````
Directive is ```
  You are a helpful assistant. Be concise.
```
````

This cleanly separates comments `()` from CEL expressions (backticks). Parentheses are always comments. Backticks are always CEL.

Inside backticks, CEL syntax applies: `//` for line comments, `/* */` for block comments, standard JS-like operators and property access.

**Migration note.** Earlier drafts of the DSL used square brackets `[]` for CEL expressions. The v0.4.0 release migrated to backticks so that array index syntax (`items[0]`) is unambiguous inside expressions. All examples and tests in the current compiler use backticks.

### String Literals

String literals are enclosed in double quotes `"`. They are used for names, scope identifiers, enum values, and display text.

### Quotation Mark Rules

Quotation marks are used consistently for **all named identifiers** when declaring or referencing them in structural positions:

- Declaring a primitive: `Content called "products":`
- Referencing a scope: `Anyone with "read inventory" can view products`
- Declaring a role: `"warehouse clerk" has "read inventory"`
- Enum values: `one of: "raw material", "finished good", "packaging"`
- Page names: `Show a page called "Hello"`

Quotation marks are **optional on references** in casual positions where ambiguity is impossible: navigation visibility lists, role names in user story headers, etc. The compiler strips balanced quotes and surrounding whitespace from identifiers.

Quotation marks **must not** span across structural boundaries. For example, `Anyone "with read inventory" can view` is a parse error.

### Section Dividers

Optional section dividers use three dashes, a space, a section name, a space, and three dashes. They are cosmetic and ignored by the parser.

```
--- Identity ---
--- Content ---
```

### Articles

Articles (`a`, `an`, `the`) are grammatically interchangeable in the DSL. The compiler accepts any article in any position. `A order` and `An order` are both valid. This is a deliberate accessibility decision for non-native English speakers.

### Keywords and Reserved Words

The Termin DSL has a moderate number of structural keywords. For comparison: SQL has approximately 90 reserved words, CSS has 200+ properties, and Python has 35 keywords. Termin's keyword count is comparable to SQL, which is appropriate for a declarative DSL targeting a similar audience (business data operations).

Keywords are case-insensitive. They are only reserved in their structural context — the word `with` is a keyword in `Anyone with "scope"` but can appear freely inside quoted identifiers like `"deal with returns"`. Any identifier that might collide with a keyword should be quoted.

---

## Header

```
Application: <name>
  Description: <text>
```

Both fields are required. Name and text are free text to end of line.

---

## Identity

### Authentication

```
Users authenticate with <provider>
```

Provider `stub` is built-in for development. Production providers are deployment-specific.

When authentication is declared, `Anonymous` (unauthenticated role) and `CurrentUser` (authenticated principal, accessible as `<RoleName>.CurrentUser`) become available.

### Scopes

```
Scopes are <scope-list>
```

Scope list is comma-separated quoted strings. The final `and` or `or` before the last item is **optional** in all list constructs throughout the DSL.

```
Scopes are "read inventory", "write inventory", "admin inventory"
Scopes are "read inventory", "write inventory", and "admin inventory"
```

Both forms are valid.

---

## Roles

```
<article> "<role-name>" has <scope-list>
```

Article (`A`, `An`, or omitted) is optional and ignored by the parser.

```
"warehouse clerk" has "read inventory" and "write inventory"
A "warehouse clerk" has "read inventory" and "write inventory"
An "executive" has "read inventory"
Anonymous has "view app"
```

All forms are valid.

### Aliases

```
"<short-name>" is alias for "<full-name>"
```

Aliases can be used anywhere the full role name would appear. This replaces the implicit suffix-matching behavior in v1.

```
"clerk" is alias for "warehouse clerk"
"manager" is alias for "warehouse manager"
```

---

## Content

### Content Block

```
Content called "<name>":
  <field declarations>
  <access rules>
```

### Field Declarations

```
Each <entity> has a <field-name> which is <type-spec>
```

Entity is the singular form of the Content name. The DSL supports simple pluralization: if the Content is named `"products"`, the entity is `product` (strip trailing `-s` or `-es`). Content named `"buses"` resolves to entity `bus`. Content named `"buss"` also resolves to `bus`.

For irregular plurals, use the convention of the regular form: write `"persons"` not `"people"`, `"gooses"` not `"geese"`.

**Type specifications:**

| Syntax | Type | Notes | Excel/Sheets Equivalent |
|---|---|---|---|
| `text` | String | | Text format |
| `unique text` | String, unique | | Text format |
| `number` | Decimal | | Number format |
| `whole number` | Integer | | Number, 0 decimal places |
| `currency` | Decimal, 2 places | | Currency format |
| `percentage` | Decimal 0-1, displayed 0%-100% | | Percentage format |
| `true/false` | Boolean | | TRUE/FALSE |
| `date` | Date without time | | Date format |
| `date and time` | Timestamp | | Date time format |
| `one of: <values>` | Enumeration | Quoted, comma-separated | Data validation dropdown |
| `automatic` | System-generated | Timestamps, IDs | N/A |
| `references <content>` | Foreign key | | N/A |
| `list of <type>` | Ordered collection | Composable with any type | N/A |

**Constraints** are appended after the type, comma-separated:

| Constraint | Syntax | Example |
|---|---|---|
| Required | `required` | `text, required` |
| Unique | `unique` | `unique text` or `text, unique` |
| Minimum | `minimum <n>` | `whole number, minimum 0` |
| Maximum | `maximum <n>` | `whole number, maximum 100` |
| Range | both | `whole number, minimum 1, maximum 10` |

```
Each product has a SKU which is unique text, required
Each product has a unit cost which is currency
Each product has a quantity which is whole number, minimum 0, maximum 9999
Each product has a category which is one of: "raw material", "finished good", "packaging"
Each product has a tags which is list of text
Each order has a line items which is list of "order lines"
```

### Access Rules

```
Anyone with "<scope>" can <verb> <content-name>
Anyone with "<scope>" can <verb> or <verb> <content-name>
Anyone with "<scope>" can <verb>, <verb>, or <verb> <content-name>
```

Verbs: `view`, `create`, `update`, `delete`, `audit`. The `audit` verb (added in v0.7.0) grants access to the generated audit-log content type for a Compute. Verbs can be combined in compound form — two-verb (`create or delete`) and three-verb (`create, update, or delete`) compounds are both valid.

---

## State

State machines can be declared for **any named primitive**: Content, Channels, Compute nodes, or application-level concerns. This enables lifecycle management across the system.

### State Block

```
State for <primitive-name> called "<state-machine-name>":
  <entity> starts as "<initial-state>"
  <entity> can also be "<state>", "<state>", "<state>"
  <transition declarations>
```

### Transitions

```
<article> "<state>" <entity> can become "<state>" if the user has "<scope>"
<article> "<state>" <entity> can become "<state>" if `<cel-condition>`
```

The pattern `if the user has "<scope>"` is syntactic sugar that the compiler expands to a scope check. For complex conditions, use CEL in backticks.

The word `again` may optionally appear for re-entry transitions.

### State on Non-Content Primitives

```
State for channel "order webhook" called "webhook lifecycle":
  channel starts as "active"
  channel can also be "paused" or "error"
  A "active" channel can become "paused" if the user has "admin orders"
  A "error" channel can become "active" if the user has "admin orders"
```

---

## Events

```
When `<cel-trigger-condition>`:
  <action>
```

The trigger condition is a CEL expression in backticks. This ensures deterministic parsing regardless of how the condition is phrased.

```
When `stockLevel.updated && stockLevel.quantity <= stockLevel.reorderThreshold`:
  Create a "reorder alert" with the product, warehouse, current quantity, threshold
```

The action line remains in Termin DSL syntax.

Named-event triggers are also valid when the condition is a standard runtime event:

```
Trigger on event "completion.created"
Trigger on event "message.created" where `message.role == "user"`
```

The named-event form is typically used inside a Compute's trigger declaration rather than as a stand-alone `When` block.

---

## Compute

### Compute Block

```
Compute called "<name>":
  [Provider is "<provider>"]       (optional; defaults to "cel")
  <shape declaration>              (required for "cel" provider)
  Accesses <content-list>          (required)
  <provider-specific wiring>
  `<cel body>`                     (for "cel" provider)
  <access rule>
  Audit level: <none|actions|debug>
```

### Providers

Three built-in Compute providers:

- **`"cel"`** (default) — pure CEL expression evaluation. Used when no `Provider is` line is present.
- **`"llm"`** — a large language model with explicit field wiring (`Input from field`, `Output into field`) and a directive plus objective. Field-to-field completion, no tool use.
- **`"ai-agent"`** — an autonomous agent that operates on declared content through a runtime-provided API surface. Uses a directive and objective; does not use field wiring.

### Shape Declaration (CEL provider)

```
Transform: takes <input-spec>, produces <output-spec>
Reduce: takes <content-name>, produces <output-spec>
Expand: takes <input-spec>, produces <content-name>
Correlate: takes <content-name> and <content-name>, produces <content-name>
Route: takes <input-spec>, produces one of <content-name> or <content-name>
```

Shape declarations are required for the `"cel"` provider. LLM and agent providers do not use shape declarations; they use field wiring (for LLM) or trigger + directive + objective (for agents).

### Compute Body — CEL Only

CEL-provider Compute bodies are CEL expressions inside backticks. Natural language Compute bodies are **not supported** — they are non-deterministic and fragile in practice.

```
Compute called "calculate order total":
  Transform: takes an order, produces an order
  `order.total = order.lines.reduce((acc, line) => acc + line.quantity * line.unit_price, 0)`
  Anyone with "orders.write" can execute this
  Audit level: actions
  Anyone with "orders.admin" can audit
```

For an `"llm"` provider Compute, the shape is replaced by field wiring and prompt blocks:

````
Compute called "complete":
  Provider is "llm"
  Accesses completions
  Input from field completion.prompt
  Output into field completion.response
  Trigger on event "completion.created"
  Directive is ```
    You are a helpful assistant. Be concise and clear.
  ```
  Objective is ```
    Answer the following prompt from the user.
  ```
  Anyone with "agent.use" can execute this
  Audit level: actions
````

An `"ai-agent"` Compute uses the same directive and objective pattern without field wiring; the agent reads and writes declared content through the runtime content API.

### Compute Access Rule

```
<role-name> can execute this
Anyone with "<scope>" can execute this
```

---

## Channels

```
Channel called "<name>":
  Carries <content-name>
  Direction: <outbound|inbound|bidirectional|internal>
  Delivery: <reliable|realtime|batch|auto>
  Requires "<scope>" to send
```

**Direction** values: `outbound` (app sends to external), `inbound` (external sends to app), `bidirectional` (both), `internal` (within-app only).

**Delivery** values: `reliable` (guaranteed, ordered, acknowledged), `realtime` (best-effort, low-latency), `batch` (buffered and flushed periodically), `auto` (runtime chooses).

**Endpoints.** The concrete network endpoint for a Channel — URL, webhook path, authentication credentials — is supplied at deploy time in an application-specific deploy-config file (`{app}.deploy.json`), not in the `.termin` source. The DSL declares *that* a Channel exists and *what* it carries; deployment wires it to a specific external service.

**Sending content to a Channel** is done from an Event block, not the Channel declaration:

```
When `note.created`:
  Send note to "note-sync"
  Log level: INFO
```

**Actions.** A Channel may optionally declare named actions for RPC-style verbs (added in v0.5.0). See the `security_agent.termin` example for the full pattern.

---

## Boundaries

```
Boundary called "<name>":
  Contains <primitive-list>
  Identity inherits from application
  Identity restricts to "<scope>"
```

Boundaries are recursive and uniform. See `primitives.md` for the full theoretical model.

---

## User Stories (Presentation)

```
As <role>, I want to <action>
  so that <motivation>:
    <presentation declarations>
```

### Presentation Declarations

| Pattern | Syntax |
|---|---|
| Page | `Show a page called "<name>"` |
| Table | `Display a table of <content> with columns: <field>, <field>` |
| Text (literal) | `Display text "<literal>"` |
| Text (expression) | `` Display text `<cel-expression>` `` |
| Aggregation | `Display total <field> with <state> vs <state> breakdown` |
| Chart | `Show a chart of <content> over the past <n> days` |
| Input | `Accept input for <field>, <field>, <field>` |
| Filter | `Allow filtering by <field>, <field>, <field>` |
| Search | `Allow searching by <field> or <field>` |
| Highlight | `` Highlight rows where `<cel-condition>` `` |
| Subscription | `This table subscribes to <content> changes` |
| Create | `Create the <entity> as "<state>"` |
| Navigate | `After saving, return to the "<page>"` |
| Validate | `` Validate that `<cel-condition>` before saving `` |
| Grouped | `For each <entity>, show <content> grouped by <field>` |

---

## Navigation

```
Navigation bar:
  "<label>" links to "<page-name>" visible to <visibility>
```

**Visibility**: `all`, a role name (full or alias), or comma-separated role names. Badge: `` visible to all, badge: `<cel-expression>` ``

---

## API

### REST

As of v0.7.0, every Content automatically gets a CRUD REST API at `/api/v1/{content-snake-name}`. The `Expose a REST API at ...:` syntax that existed in earlier drafts was removed because it duplicated what the compiler could derive from Content declarations. Headless services (applications with no user stories) are fully supported by the auto-generated API.

Access to each endpoint is gated by the normal access-control rules on the Content type. If a role has `view` on a Content, its REST GET endpoints are reachable; if it does not, they return 403.

### Streaming

Content changes are available as WebSocket subscriptions at `/runtime/ws`, filtered by Content name. The subscription protocol is specified in the runtime implementer's guide in the conformance suite repository.

Example subscription message (sent by the client to `/runtime/ws` after connection):

```
{ "v": 1, "ch": "content.products", "op": "subscribe", "ref": "sub1", "payload": {} }
```

Presentation-layer tables subscribe to changes with a DSL clause:

```
This table subscribes to stock level changes
```

---

## Type System

### Built-in Types

`Text`, `whole number`, `number`, `currency`, `percentage`, `true/false`, `date`, `date and time`, `automatic`, `UserProfile` (with Identity), `Role` (with Identity).

### Composite Types

`list of <type>` composes with any type, including Content names:

```
list of Text
list of "products"
list of whole number
```

### Duck Typing

The runtime uses structural typing. A value conforms to a type if it has the required fields with compatible types. Schema validation checks shape, not class identity.

### Type Annotations

The colon syntax `name : Type` is available in all declaration contexts: Compute parameters, Content fields (as alternative syntax), Channel payloads.

### Simple Pluralization

When referencing types in lists or collections, the DSL applies simple pluralization: strip trailing `-s` or `-es` to resolve to the base type. `list of Texts` resolves to `list of Text`. `list of "order lines"` resolves to a list of the `"order line"` type.

---

## CEL Security Model

CEL expressions are sandboxed by the Termin runtime. The runtime constructs a restricted evaluation context containing only:

- Declared Content schemas accessible to the current Identity
- Registered Compute functions callable by the current Identity
- Current Identity context (CurrentUser, roles, scopes)
- Built-in operators (arithmetic, string, comparison, logical)

CEL **cannot access**: the filesystem, the network, `require`/`import`, `process`, `eval`, `Function` constructor, or any global not explicitly placed in the context. The runtime enforces this by never placing dangerous objects in the evaluation context.

This is a defense-in-depth model: CEL (@marcbachmann/cel-js) is already a context-based evaluator with no access to Node.js globals. The Termin runtime adds a second layer by constructing a minimal context. Server-side CEL expressions are evaluated against the same restricted context — no user-provided expression can escape the context boundary.

Additionally, CEL expressions in `.termin` files are authored by the application developer, not by end users. End-user input flows through Content schemas (which are validated) and never into the expression evaluator directly. This eliminates the EL injection vector described by OWASP.

---

## Formal Grammar Verification

The Termin DSL grammar can be formally specified as a PEG (Parsing Expression Grammar) and used to generate both a linter and a parser independently of the compiler.

**Recommended toolchain:**

| Tool | Type | Notes |
|---|---|---|
| **TatSu** | EBNF → PEG parser (Python) | Compiles grammar strings into Grammar objects at runtime. Supports left recursion. Actively maintained. |
| **Lark** | EBNF → Earley/LALR (Python) | Clean EBNF format, automatic AST generation. Good documentation. |
| **pegen** | PEG → parser (Python) | The parser generator used by CPython itself. Generates Python parsers from `.gram` files. |

A PEG grammar for Termin serves two purposes:

1. **Independent linter:** Validate `.termin` files against the grammar without invoking the compiler. This verifies compiler behavior during development — correct files lint, bad files don't.
2. **Compiler-compiler:** Generate the parser module from the grammar spec. Changes to the grammar automatically produce an updated parser.

A formal `.peg` or `.gram` file for the Termin grammar is a planned deliverable.

---

## Termin Reflection

The Termin runtime exposes a read-only reflection API that allows applications to introspect on their own structure. Analogous to .NET's `System.Reflection`.

**Use cases:**

- **Dynamic UI generation:** Enumerate Content schemas to generate forms/tables without hardcoding field names.
- **Schema-driven validation:** Inspect input schema at runtime for dynamic validation rules.
- **Admin tooling:** List all Content, State machines, Channels, Boundaries in a running application.
- **Plugin discovery:** A Compute function discovers and composes with other registered functions.
- **Self-documenting applications:** Generate API documentation from schema and Channel declarations.

Reflection access is scoped by Identity — you can only reflect on primitives your role has access to. Reflection is read-only and cannot modify structure at runtime.

---

## Open Questions

1. **Canonical spec authority:** Should the PEG grammar file or this document be authoritative when they diverge?
2. **Multi-line CEL:** Should `[[ ... ]]` syntax support multi-line expression blocks?
3. **List nesting depth:** Should `list of list of <type>` have a maximum nesting depth?
4. **Channel state transitions:** Should some State transitions on Channels be runtime-managed (automatic error state on connection failure) vs. user-initiated?
