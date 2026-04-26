# Cascade Grammar — Technical Design

**Status:** Approved for implementation (2026-04-26)
**Author:** Claude Anthropic
**Anchor:** v0.9 Phase 2.x item (a) per
`docs/termin-provider-system-brd-v0.9.md` §6.2

**Review notes (2026-04-26, JL):** Grammar shape Option C accepted.
Structural JSON schema enforcement accepted. Example-migration picks
accepted with the projectboard caveat below. **New design decision
added: static cascade-restrict mix detection** (§3.12) — JL proposed
making transitive cascade→restrict deadlocks a compile-time error
rather than a runtime gotcha. Adopted; this changes the projectboard
example design (§4.11) and adds new conformance fixtures (§5).
Cascade-mode field name accepted as `cascade_mode`.

This is a pre-implementation design. Nothing has been written yet. The
goal is to surface every decision before code lands so the cascading
changes (PEG, parser, analyzer, lowering, IR, runtime, conformance,
all `references`-using examples) happen with one consistent shape
instead of evolving across the implementation.

---

## 1. Goal

v0.9 BRD §6.2 mandates: every `references X` field must declare
cascade behavior explicitly. Bare `references X` is a parse error.
Cascade is the safer default semantically (referential integrity) but
is too consequential to be implicit; the audit-over-authorship tenet
says deletion blast radius must be visible in source review, not
inferred.

Two new clauses:
- `references X, cascade on delete` — when the parent is deleted,
  every record that references it via this field is also deleted.
- `references X, restrict on delete` — when the parent is deleted
  while any record references it via this field, the delete is
  refused (HTTP 409).

This is a v0.9 grammar break. v0.8-syntax `.termin` files using
`references` will fail to compile until migrated. We are pre-1.0; no
backward-compat shim.

## 2. Current state

### Grammar (`termin/termin.peg:97`)

```peg
field_clause
    = 'is' 'state:'                                  #FieldIsStateBlock
    | 'is' 'state' ':'                               #FieldIsStateBlockSpaced
    | 'is' te:type_expr                              #FieldIsType
    | 'references' ref:words_or_quoted constraints:constraints  #FieldReferences
    ;
```

`constraints` is a comma-separated list of `constraint` alternatives:
`required`, `unique`, `minimum N`, `maximum N`, `is one of: ...`,
`defaults to ...`, `confidentiality is ...`. Today `required` and
`unique` are the only constraints any reference field uses.

### Python parse helper (`termin/parse_helpers.py:245`)

The Python-level fallback path (used when TatSu fails or for the
substring-based path) does:

```python
if text.startswith("references "):
    rt = text[len("references "):].strip(); ci = rt.find(","); ct = ""
    if ci >= 0: ct = rt[ci:]; rt = rt[:ci].strip()
    te = TypeExpr(base_type="reference", references=rt.strip('"'), line=ln)
    if "required" in ct: te.required = True
    if "unique" in ct: te.unique = True
    return te
```

Constraint detection is `in`-substring on the comma-tail.

### AST (`termin/ast_nodes.py:28`)

```python
@dataclass
class TypeExpr:
    base_type: str
    required: bool = False
    unique: bool = False
    ...
    references: Optional[str] = None  # name of referenced Content
```

No cascade field today.

### Analyzer (`termin/analyzer.py:240-249`)

```python
def _check_content_references(self) -> None:
    for content in self.program.contents:
        for field in content.fields:
            if field.type_expr.references:
                if field.type_expr.references not in self.content_names:
                    ...  # undefined-content error
```

Resolves the reference target. No cascade-related checks.

### Lowering (`termin/lower.py:135`)

```python
foreign_key=_snake(f.type_expr.references) if f.type_expr.references else None,
```

`FieldSpec.foreign_key` carries the snake-cased target table. Nothing
about cascade.

### IR (`termin/ir.py:48-63` and `docs/termin-ir-schema.json:188`)

```python
@dataclass(frozen=True)
class FieldSpec:
    ...
    foreign_key: Optional[str] = None  # target table snake name
```

The IR schema's `foreign_key` description: "Snake_case name of the
target Content when business_type is 'reference'. The runtime creates
a foreign key constraint to that Content's 'id' column." No cascade
mention.

### Runtime SQL (`termin_runtime/storage.py:188-190`)

```python
if field.get("foreign_key"):
    _assert_safe(field["foreign_key"], f"foreign key target in {table_name}")
    fk_defs.append(f'FOREIGN KEY ({_q(field["name"])}) REFERENCES {_q(field["foreign_key"])}(id)')
```

No `ON DELETE` clause. SQLite defaults to NO ACTION when `PRAGMA
foreign_keys=ON` (storage.py:75). NO ACTION means the delete
proceeds and triggers a constraint violation only at commit time —
in practice indistinguishable from RESTRICT for our pattern of one
delete per transaction. The runtime translates the resulting
`sqlite3.IntegrityError` to HTTP 409 in `routes.py:475`.

### Provider (`termin_runtime/providers/builtins/storage_sqlite.py:267-300`)

The `delete()` method accepts a `cascade_mode` arg from Phase 2 but
ignores it (`del cascade_mode  # acknowledged, not yet effective`).
The behavior is whatever SQLite enforces from the FK declarations.

### Provider contract (`termin_runtime/providers/storage_contract.py:262`)

`CascadeMode` enum already defined: `CASCADE = "cascade"`,
`RESTRICT = "restrict"`. Threaded through `delete(content_type, id,
cascade_mode)` per BRD §6.2.

### Examples using `references` today

13 occurrences across 7 example files (all currently bare `references`):

```
examples/compute_demo.termin:27   references orders, required
examples/headless_service.termin:25 references orders
examples/helpdesk.termin:42       references tickets, required
examples/hrportal.termin:37       references employees, required
examples/projectboard.termin:25   references projects, required
examples/projectboard.termin:31   references projects, required
examples/projectboard.termin:40   references projects, required
examples/projectboard.termin:41   references sprints
examples/projectboard.termin:42   references team members
examples/projectboard.termin:59   references tasks, required
examples/projectboard.termin:60   references team members, required
examples/warehouse.termin:34      references products, required
examples/warehouse.termin:42      references products, required
```

Plus 1 dev example already on the new syntax:
`examples-dev/v09_moderation_agent.termin:52` —
`references messages, restrict on delete`.

### Conformance suite

No existing cascade tests. `test_crud.py` has delete tests but they
are single-record, no FK dependencies. Fresh ground. Suite expects
fixtures as `.termin.pkg` zips with `manifest.json + <name>.ir.json
+ <name>.termin + <name>_seed.json`.

## 3. Design decisions (with recommendations)

### 3.1 Where in the grammar does cascade live?

Three options:

**Option A: Add cascade as new constraint alternatives.**

```peg
constraint
    = 'required'
    | 'unique'
    | 'cascade' 'on' 'delete'   #CascadeOnDelete
    | 'restrict' 'on' 'delete'  #RestrictOnDelete
    | 'minimum' val:number
    | 'maximum' val:number
    | ...
```

- Grammar is small. Constraint list grows by two.
- `Each X has Y which is text, cascade on delete` would parse and
  the analyzer must reject. Late binding of error.
- Order-flexible: `references X, required, cascade on delete` AND
  `references X, cascade on delete, required` both work.

**Option B: Reference-specific constraint list.**

```peg
field_clause
    = ...
    | 'references' ref:words_or_quoted ',' cm:cascade_mode rest:constraints  #FieldReferencesCascade
    ;
cascade_mode
    = 'cascade' 'on' 'delete'  #Cascade
    | 'restrict' 'on' 'delete' #Restrict
    ;
```

- Grammar enforces the requirement structurally. `references X` with
  no cascade fails parse.
- Order-rigid: cascade must be the first thing after the reference
  target. `references X, required, cascade on delete` would not
  parse — only `references X, cascade on delete, required` would.
- Existing examples that use `references X, required` need
  reordering (every one of the 13).

**Option C: Hybrid — Option A grammar, but bare-references is
explicitly checked in analyzer with a specific actionable error.**

- Same grammar as Option A.
- Analyzer enforces:
  1. `cascade on delete` / `restrict on delete` may only appear on
     `references` fields. (TERMIN-S0XX)
  2. Every `references` field must declare exactly one of the two.
     (TERMIN-S0XX)
  3. A field may not declare both. (TERMIN-S0XX)
- Error messages are line-precise and tell the user exactly what to
  add.

**Decision: Option C** (JL accepted, 2026-04-26).

Reasons:
1. **Error quality** is the main reason. Audit-over-authorship is
   served when a PM reading source can see "cascade on delete" right
   there in the line. Audit-over-authorship is also served when the
   compiler tells a developer who forgot it *exactly* what to write.
   PEG parse errors are notoriously generic ("expected ','"). Analyzer
   errors are bespoke ("Field "ticket" in "comments" must declare
   cascade behavior — add ", cascade on delete" or ", restrict on
   delete" at line 42.").
2. **Order flexibility** matches existing reading patterns. The
   13 examples currently read `references X, required`; promoting
   that to `references X, required, restrict on delete` keeps the
   diff minimal and the reading order natural ("references X,
   which is required, and is restrict on delete").
3. **Same grammar shape** as `required`/`unique`. No special-cased
   reference constraint list. Easy to extend if v1.0 introduces
   more reference modifiers (e.g., `nullable on delete`, deferred
   FK semantics, etc.).
4. **The BRD says "parse error,"** but the user-facing experience
   that matters is "compilation fails with a useful message at the
   right line." That's what Option C delivers; Option B's strict-grammar
   approach just produces uglier errors for the same outcome.

JL accepted Option C (2026-04-26).

### 3.2 Cascade vs Restrict — is there a default?

**No default.** BRD §6.2 is explicit. Bare `references X` fails.

This is a hard rule, not a recommendation.

### 3.3 SQL semantics in SQLite

Today: `FOREIGN KEY (col) REFERENCES other(id)` (no `ON DELETE`
clause). SQLite default is NO ACTION which behaves like RESTRICT for
single-statement deletes.

Proposal: emit explicit `ON DELETE` clauses based on the IR's
cascade mode:

```python
on_delete = "CASCADE" if field["cascade_mode"] == "cascade" else "RESTRICT"
fk_defs.append(
    f'FOREIGN KEY ({_q(field["name"])}) REFERENCES {_q(field["foreign_key"])}(id) '
    f'ON DELETE {on_delete}'
)
```

This makes the cascade behavior visible in `.schema` output and in
any DB inspection tooling. Restricted deletes still raise
`sqlite3.IntegrityError`; cascade deletes silently propagate to
referrers. The runtime route handler's existing 409 translation
still works for RESTRICT.

### 3.4 Provider `delete(cascade_mode=...)` behavior

Today the SqliteStorageProvider ignores `cascade_mode`. After this
change:

**Option D1: Schema is single source of truth.** Provider continues
to ignore `cascade_mode`; SQL FK declarations enforce. The arg
becomes vestigial in v0.9.

**Option D2: Schema declares, provider validates.** Provider
inspects the FK declarations on the table being deleted from and
asserts they match the requested `cascade_mode`. Mismatch raises
`ValueError`. Defense in depth.

**Option D3: Per-call override.** Provider honors `cascade_mode`
even if it disagrees with the schema. Useful for blast-radius
override scenarios.

**Recommendation: D1 for v0.9.** Schema is source of truth;
provider arg is plumbed for forward-compatibility but unused.
D2 is appealing but introduces a SQL-introspection round-trip on
every delete, and the schema declaration is already enforced by
SQLite. D3 is a v1.0 conversation about admin-override semantics —
there's no v0.9 caller that wants it.

The runtime call sites pass `cascade_mode=CascadeMode.RESTRICT`
to `delete()` today (see `routes.py:469`). I'll change those to
pass the schema-declared mode (looked up from the FieldSpec) so
the contract argument carries the actual intent, even though the
SqliteStorageProvider ignores it.

### 3.5 Migration of existing tables

The `init_db` function uses `CREATE TABLE IF NOT EXISTS`. Existing
v0.8 tables will keep their no-`ON DELETE` FK declarations. SQLite
does not support changing FK constraints via `ALTER TABLE` —
changing them requires a table rebuild (CREATE new, INSERT SELECT,
DROP old, RENAME).

For v0.9 cascade grammar in isolation:
- New deploys: get the explicit FK clauses correctly.
- Existing app.db files from v0.8 deploys: keep their old FK
  semantics (NO ACTION ≈ RESTRICT). Bare `references X` will fail
  at *compile* time in v0.9, so the app can't redeploy without
  migration anyway.

The migration story belongs to Phase 2.x item (b) — the diff
classifier. When a redeploy detects that an FK has gained an
explicit cascade mode that disagrees with the on-disk schema,
classify as "risky" and require an explicit migration acknowledgment.

For v0.9 Phase 2.x (a) (cascade grammar): document the limitation in
the cascade design doc itself and in the v0.9 release notes. Tests
should always start from a fresh DB.

### 3.6 IR shape

Add a single field to FieldSpec:

```python
@dataclass(frozen=True)
class FieldSpec:
    ...
    foreign_key: Optional[str] = None
    cascade_mode: Optional[str] = None  # "cascade" | "restrict" | None
```

Invariants:
- `cascade_mode is not None` ⟺ `foreign_key is not None`.
- When `foreign_key is not None`, `cascade_mode` MUST be either
  `"cascade"` or `"restrict"`. Lowering is responsible for
  enforcing this; analyzer raises before lowering can fail.

Schema bump in `docs/termin-ir-schema.json`:

```json
"cascade_mode": {
  "type": ["string", "null"],
  "enum": ["cascade", "restrict", null],
  "default": null,
  "description": "Cascade behavior on delete of the referenced parent. Required when business_type is 'reference'. 'cascade' = delete this row when the parent is deleted. 'restrict' = refuse parent delete if any rows reference it. Null when business_type is not 'reference'."
}
```

The conditional ("required when business_type is 'reference'")
isn't directly expressible in JSON schema's natural shape, but I
can encode it via a top-level `if/then` per FieldSpec — or leave it
as a documented invariant and rely on the conformance test pack to
enforce it.

**Decision: structural** (JL accepted, 2026-04-26). The JSON schema
will encode the invariant via top-level `if/then/else` per FieldSpec:

```json
"if": { "properties": { "business_type": { "const": "reference" } } },
"then": {
  "properties": {
    "cascade_mode": { "type": "string", "enum": ["cascade", "restrict"] }
  },
  "required": ["cascade_mode"]
},
"else": {
  "properties": {
    "cascade_mode": { "type": "null" }
  }
}
```

Any conforming runtime parsing the IR JSON gets the invariant for
free at the schema validation layer.

### 3.7 Error message shape

Three new analyzer errors, in the TERMIN-S series (semantic). I'll
allocate codes during implementation; placeholders here:

**TERMIN-S0XXa: Reference field missing cascade declaration.**
```
Reference field "ticket" in "comments" must declare cascade behavior.
  At examples/helpdesk.termin:42
  Add ", cascade on delete" or ", restrict on delete" to the line.

  - "cascade on delete" → when the parent ticket is deleted, this comment is deleted too.
  - "restrict on delete" → refuse to delete a ticket while comments reference it.

  See docs/cascade-grammar-design.md §1 for guidance on which to choose.
```

**TERMIN-S0XXb: Cascade declaration on non-reference field.**
```
"cascade on delete" only applies to reference fields.
  At examples/X.termin:42
  Field "Y" has type "text". Cascade is only meaningful for fields
  declared with "references <content>".
```

**TERMIN-S0XXc: Conflicting cascade declarations.**
```
Reference field "ticket" in "comments" declares both "cascade on delete"
and "restrict on delete". Choose exactly one.
  At examples/helpdesk.termin:42
```

The TERMIN-S0XXa text is the most important; it's the migration
prompt every existing app developer will see. I'll keep it
actionable, short, and link to the design doc.

### 3.8 Confidentiality interaction

Confidentiality and cascade are independent declarations on a
reference field. Cascade behavior must work regardless of whether
the deleting user can see the cascade-target rows.

For `cascade on delete` with confidentiality-restricted children:
the parent delete must remove ALL referrers, including ones the
user can't see. Otherwise we'd leave dangling FK references — a
referential integrity bug. The audit log records the actual cascade
count (visible AND invisible) with a redaction marker for the
invisible ones.

For `restrict on delete` with confidentiality-restricted children:
if any referrer exists, the delete is refused. The 409 error
SHOULD NOT leak the count of confidentiality-restricted referrers
to the deleting user. Runtime should distinguish "blocked because
of N visible referrers" from "blocked because referrers exist
(some redacted)."

This intersection is a known concern but not a v0.9 Phase 2.x
blocker. Document the principle ("cascade and restrict take
precedence over confidentiality at the storage layer; redaction
applies to the *report* of blast radius, not to whether cascade
actually happens") and defer the redaction-aware blast radius
report to Phase 4 or later.

### 3.9 Multi-hop cascade (transitive)

If A→B→C and both edges are `cascade on delete`, deleting A removes
A's children in B AND those children's children in C. SQLite handles
this natively when `PRAGMA foreign_keys=ON`. No special runtime
logic needed; conformance tests should cover the case so we know
it stays working under Postgres / DynamoDB providers in the future.

If A→B is cascade and B→C is restrict, deleting A still cascades
A→B successfully even if B has C-referrers — because the B→C
restrict applies to *direct* B-deletes, not cascade-driven
B-deletes. Wait, actually SQLite's behavior here: ON DELETE CASCADE
on A→B propagates the delete to B, which then triggers any FKs
*from* B. If B→C is RESTRICT, the cascade-induced B-delete will
fail under SQLite, which fails the whole transaction. This is a
gotcha worth a conformance test and documentation.

### 3.10 Self-references

No example uses self-references today. Out of scope to test
exhaustively, but the grammar/analyzer/lowering should handle
`Each X has a parent which references X, restrict on delete`
correctly. Add one minimal test case in the analyzer.

### 3.11.5 Static cascade-restrict mix detection (NEW — JL, 2026-04-26)

**Decision: enforce at compile time via a new analyzer check.**

The §3.9 cascade-then-restrict gotcha — where a cascade chain hits a
restrict edge mid-flight and aborts the whole transaction — is a
*structural* defect of the cascade graph, not a runtime problem.
Termin's tenets (Tenet 2: enforcement over vigilance; Tenet 1: audit
over authorship) say the platform should reject the structurally
broken graph rather than ship it and let the user discover the
deadlock at first delete.

**Cascade graph definition.** Treat each `references P, cascade on
delete` field on content C as a directed edge `P --cascade→ C`
(deleting P propagates to C). Treat each `references P, restrict on
delete` field on content D as a directed edge `P --restricted-by→ D`
(deleting P fails if D exists).

**The static rule.** For every content node `N` in the cascade
graph, `N` cannot simultaneously be:

1. A **cascade-target** (some content `P` declares
   `references N, cascade on delete` somewhere — equivalently, an
   edge `N --cascade→ P` exists with N as target)¹, AND
2. A **restrict-protector** (some content `D` declares
   `references N, restrict on delete` — i.e., the edge
   `N --restricted-by→ D` exists).

¹ *Wait — re-checking direction.* A `references P, cascade on delete`
field declared on content `C` makes `C` the cascade-target (delete-P
propagates delete-C). The edge points P→C in the "what gets deleted
when" graph. So the rule is:

- **Cascade-target N** = some other content C declares
  `references N, cascade on delete` (a field on C pointing at N with
  cascade) — N has no such fields; N is the *referee* whose deletion
  triggers cascading.
- **Restrict-protector N** = some content D declares
  `references N, restrict on delete` (a field on D pointing at N
  with restrict) — N has no such fields; N is the *referee* whose
  deletion is refused while D exists.

Hmm, that doesn't quite work either — let me restate cleanly:

A `references P` field declared on content C creates a parent-child
relationship where `P is the parent and C is the child`. The cascade
mode says what happens to C when P is deleted:
- `cascade on delete` → C is deleted alongside P.
- `restrict on delete` → P's deletion is refused while any C exists.

The deadlock arises in chains of *parent* relationships:
- A is parent of B (cascade): delete-A → delete-B propagates.
- B is parent of C (restrict): delete-B → fails if C exists.
- delete-A → cascade-delete-B → fails because C exists → entire
  transaction rolled back.

So the structural defect is: **content B is a parent in two ways
that conflict** — B is cascade-deleted when A goes (so B's deletion
is not under user control), AND B's deletion is restricted by C
(so unless C is empty, B can't be deleted at all).

**Reformulated static rule:** For every content `B` in the spec, if
`B` is the *target* of any `cascade on delete` reference (some C
declares `references B, cascade on delete`) AND `B` is the *target*
of any `restrict on delete` reference (some D declares
`references B, restrict on delete`), reject with TERMIN-S0XXd.

Equivalently: for every reference edge `D --references B,restrict on delete`,
no `C --references B,cascade on delete` edge may exist for the same B.

**Error message (TERMIN-S0XXd):**

```
TERMIN-S0XXd: Transitive cascade-restrict deadlock involving "sprints".

  "sprints" is cascade-deleted when "projects" is deleted:
    examples/projectboard.termin:31  references projects, cascade on delete

  "sprints" is restrict-protected by "tasks":
    examples/projectboard.termin:41  references sprints, restrict on delete

  This combination is a runtime deadlock: deleting a "projects"
  record would cascade to "sprints", which would fail because of
  "tasks", aborting the entire transaction. Any "projects" with
  related "sprints" with related "tasks" can never be deleted.

  Resolve one of:
    (a) Change the projects→sprints reference to "restrict on delete"
        (require explicit sprint cleanup before project delete).
    (b) Change the tasks→sprints reference to "cascade on delete"
        (project delete propagates all the way to tasks).
```

The error names every contributing edge with file:line so the user
can navigate directly to the choice they need to make.

**Cycles (TERMIN-S0XXe).** Decision: detect and reject at compile
time, in this pass, with one caveat for self-references.

- **Multi-content cascade cycle** — A.fb references B cascade AND
  B.fa references A cascade. REJECTED. JL's observation: a cycle
  with all-required FKs is structurally impossible to populate
  (inserts deadlock — each row needs the other to exist first), so
  any real-data cycle requires at least one optional FK in the
  cycle. The compiler rejects the *schema* before any insert is
  ever attempted; behavior under cascade cycles varies across
  SQLite versions and other backends, so static rejection makes
  cascade graphs portable.
- **Self-cascade** (A.parent references A, cascade on delete) —
  ALLOWED. Common pattern for tree structures where "delete subtree"
  is the desired semantics. SQLite handles correctly. Excluded from
  cycle detection.

Algorithm: build the cascade-edge subgraph (only `cascade on delete`
edges, ignoring `restrict`), DFS for back-edges that don't terminate
in a self-loop. Single pass, ~30 lines.

Error message TERMIN-S0XXe cites every edge in the cycle:

```
TERMIN-S0XXe: Cascade cycle detected.

  "orders" cascade-deletes "shipments":
    examples/X.termin:N  references orders, cascade on delete
  "shipments" cascade-deletes "orders":
    examples/X.termin:M  references shipments, cascade on delete

  Cascade cycles produce undefined or backend-dependent behavior at
  delete time. Self-references (a content referencing itself) are
  the only allowed form of cyclic cascade.

  Resolve by changing one edge in the cycle to "restrict on delete".
```

**Algorithm.** Single pass after `_check_content_references`
resolves targets:

```python
cascade_targets: dict[str, list[tuple[content_name, field_name, line]]] = {}
restrict_targets: dict[str, list[tuple[content_name, field_name, line]]] = {}
for c in self.program.contents:
    for f in c.fields:
        if not f.type_expr.references:
            continue
        target = f.type_expr.references
        if f.type_expr.cascade_mode == "cascade":
            cascade_targets.setdefault(target, []).append((c.name, f.name, f.line))
        elif f.type_expr.cascade_mode == "restrict":
            restrict_targets.setdefault(target, []).append((c.name, f.name, f.line))

for target in cascade_targets.keys() & restrict_targets.keys():
    self._error(SemanticError(...))  # cite all cascade and restrict edges to target
```

O(n) in number of references. Trivial cost.

### 3.11 Optional reference fields

A non-required reference field (foreign_key column NULL) can have
a cascade mode. Semantics:
- `cascade on delete`: when the parent is deleted, NULL referrers
  are unaffected (no delete since they don't reference anything).
  Non-NULL referrers are deleted.
- `restrict on delete`: parent delete refused if any non-NULL
  referrer exists.

SQLite's ON DELETE CASCADE / RESTRICT honor NULL correctly out of
the box. Tests should cover this.

## 4. Layer-by-layer plan

### 4.1 Grammar (`termin/termin.peg`)

Add cascade alternatives to the constraint rule (Option C):

```peg
constraint
    = 'required'              #Required
    | 'unique'                #Unique
    | 'cascade' 'on' 'delete' #CascadeOnDelete
    | 'restrict' 'on' 'delete' #RestrictOnDelete
    | 'minimum' val:number    #Minimum
    | 'maximum' val:number    #Maximum
    | 'is' 'one' 'of:' vals:literal_list  #IsOneOf
    | 'defaults' 'to' expr:expr        #DefaultExpr
    | 'defaults' 'to' lit:quoted_string    #DefaultLiteral
    | 'confidentiality' 'is' scopes:quoted_list  #Confidentiality
    ;
```

Tests: parser tests for each cascade syntax; verify the cascade
clauses can appear before, after, or interleaved with `required`.

### 4.2 Python parse helper (`termin/parse_helpers.py`)

Update `_parse_field_type`:

```python
if text.startswith("references "):
    rt = text[len("references "):].strip(); ci = rt.find(","); ct = ""
    if ci >= 0: ct = rt[ci:]; rt = rt[:ci].strip()
    te = TypeExpr(base_type="reference", references=rt.strip('"'), line=ln)
    if "required" in ct: te.required = True
    if "unique" in ct: te.unique = True
    # v0.9: cascade declarations. Both check separately so the
    # analyzer can detect duplicates.
    if "cascade on delete" in ct:
        te.cascade_mode = "cascade"
    elif "restrict on delete" in ct:
        te.cascade_mode = "restrict"
    return te
```

(The fallback parser is forgiving — duplicates only show one mode
here; the TatSu path will emit both alternatives and the analyzer
checks for that. Worth a comment.)

### 4.3 AST (`termin/ast_nodes.py`)

Add to TypeExpr:

```python
cascade_mode: Optional[str] = None  # "cascade" | "restrict" | None; only meaningful for base_type=="reference"
```

### 4.4 Analyzer (`termin/analyzer.py`)

Extend `_check_content_references` to enforce the three new
errors (S0XXa/b/c). New TERMIN-S codes — I'll claim the next three
unused codes during implementation.

Also: every `cascade on delete` / `restrict on delete` constraint
that lands on a non-reference field needs an error (S0XXb).

### 4.5 Lowering (`termin/lower.py`)

```python
fields.append(FieldSpec(
    ...
    foreign_key=_snake(f.type_expr.references) if f.type_expr.references else None,
    cascade_mode=f.type_expr.cascade_mode,  # validated by analyzer
    ...
))
```

### 4.6 IR (`termin/ir.py`)

Add `cascade_mode: Optional[str] = None` to FieldSpec.

### 4.7 IR JSON schema (`docs/termin-ir-schema.json`)

Add the `cascade_mode` property (see §3.6 for the shape decision).

### 4.8 Runtime SQL (`termin_runtime/storage.py`)

Update `init_db` FK emission:

```python
if field.get("foreign_key"):
    _assert_safe(field["foreign_key"], f"foreign key target in {table_name}")
    cm = field.get("cascade_mode")
    if cm not in ("cascade", "restrict"):
        # Should never happen if compiler did its job, but defense in depth.
        raise ValueError(
            f"Field {field['name']} on {table_name}: cascade_mode must be "
            f"'cascade' or 'restrict' on a foreign-key column, got {cm!r}"
        )
    on_delete = cm.upper()
    fk_defs.append(
        f'FOREIGN KEY ({_q(field["name"])}) REFERENCES {_q(field["foreign_key"])}(id) '
        f'ON DELETE {on_delete}'
    )
```

### 4.9 Runtime route handler (`termin_runtime/routes.py`)

Look up the schema-declared cascade mode for the deleting content
type when invoking `ctx.storage.delete()`. Pass that mode in the
call. The provider currently ignores it (D1) but having the
correct value at the contract boundary is right for the future
(third-party providers may consult it).

The 409 translation path stays unchanged; it kicks in for both
`ON DELETE NO ACTION` (legacy) and `ON DELETE RESTRICT` (new).

### 4.10 Examples migration

13 references-usages need cascade declarations. With the static
check (§3.11.5), the original picks needed projectboard
restructuring. Final picks (JL accepted, 2026-04-26):

| File | Line | Current | Migration | Rationale |
|------|------|---------|-----------|-----------|
| compute_demo.termin | 27 | `references orders, required` | `, restrict on delete` | Order lines protect order from accidental deletion. |
| headless_service.termin | 25 | `references orders` | `, restrict on delete` | Same. |
| helpdesk.termin | 42 | `references tickets, required` | `, cascade on delete` | Comments belong to tickets; ticket deletion removes them. Comments aren't restrict-protectors of anything → safe. |
| hrportal.termin | 37 | `references employees, required` | `, restrict on delete` | Salary review is audit data; never auto-delete. |
| projectboard.termin | 25 | (team members → projects) | `, restrict on delete` | All-restrict design (see §4.11). |
| projectboard.termin | 31 | (sprints → projects) | `, restrict on delete` | All-restrict design. |
| projectboard.termin | 40 | (tasks → projects) | `, restrict on delete` | All-restrict design. |
| projectboard.termin | 41 | (tasks → sprints) | `, restrict on delete` | All-restrict design. |
| projectboard.termin | 42 | (tasks → team members) | `, restrict on delete` | Don't lose tasks when assignee leaves. |
| projectboard.termin | 59 | (time logs → tasks) | `, cascade on delete` | Time logs are subordinate to their task. Tasks are NOT restrict-protectors of anything (per all-restrict design above) → safe. |
| projectboard.termin | 60 | (time logs → team members) | `, restrict on delete` | Time logs are audit data per team member; preserve. |
| warehouse.termin | 34 | (stock levels → products) | `, restrict on delete` | Stock data is auditable; require explicit cleanup. |
| warehouse.termin | 42 | (reorder alerts → products) | `, cascade on delete` | Alerts are derived; new product = recompute. Products aren't cascade-targets → safe. |

**Static check verification** for the final picks:

- **products:** restrict-protector (stock levels), no cascade-incoming → ✓
- **orders:** restrict-protector (compute_demo + headless), no cascade-incoming → ✓
- **tickets:** cascade-source (comments cascade), no restrict-incoming → ✓
- **employees:** restrict-protector (salary reviews), no cascade-incoming → ✓
- **projects/sprints/tasks/team members:** all restrict-protectors, no cascade-incoming anywhere → ✓
- **time logs / comments / order lines / order items / salary reviews / stock levels / reorder alerts:** none of them are referenced by anything → not in either set → ✓

No deadlocks. The static check passes for every example.

### 4.11 Projectboard cascade graph (resolved)

The original draft had Project→Sprints cascade with Sprints→Tasks
restrict — the canonical deadlock pattern. With §3.11.5 enforced at
compile time, that draft becomes a **compiler error**, not a
runtime gotcha.

**Final design: all-restrict, except Task→TimeLog cascade.**

Cascade graph after migration:

```
projects ──restrict→ sprints
projects ──restrict→ team_members
projects ──restrict→ tasks
sprints  ──restrict→ tasks
team_members ──restrict→ tasks (assignee)
team_members ──restrict→ time_logs
tasks    ──cascade→ time_logs
```

**Product statement this expresses:**
- Deleting a project, sprint, team member, or task is a deliberate
  cleanup operation — the user must explicitly remove subordinates
  first. The compiler-enforced graph guarantees no surprise
  data-loss cascades.
- Time logs are the only "fully subordinate" entity — they have no
  independent existence outside their task. When a task goes, its
  time logs go.
- This matches what JL described as "the right cleanup": "remove
  all the tasks and then you can delete the sprint, or you could
  delete the project, and that also deletes the sprint." With this
  design: project delete needs sprints empty AND team members empty
  AND tasks empty; only after that does the project go. No surprise.

**Alternative considered: full cascade.** Project cascades to
everything underneath. Aggressive auto-cleanup. Rejected for the
example because (a) data loss surprise risk is real, (b) the
all-restrict design demonstrates the static-check-aware design
discipline by inviting users to redesign their cascade graphs
rather than hoping cascade chains work out at runtime, (c) it's
the "boring" choice that highlights the cascade demonstration in
the test fixtures rather than the example pedagogy.

This decision is example-design judgment, not a Termin platform
constraint. Real apps may pick differently; the compiler accepts
any cascade graph that passes §3.11.5.

## 5. Conformance test plan (TDD: red first)

Three new test packs in `termin-conformance/tests/` plus a set of
purpose-built `.termin` fixtures (NOT in `examples/` per JL — these
are test-suite-only).

### 5.0 Purpose-built test fixtures

Living in `termin-conformance/fixtures-cascade/` (new directory),
generated as `.termin.pkg` by a small extension to the release
script (or by a dedicated test conftest). The compiler-side mirror
lives in `termin-compiler/examples-test/cascade/` so the compiler
can run them as part of its own test suite without involving the
conformance repo.

- **`cascade_demo.termin`** — happy path. Two contents
  (`parents`, `cascade children`, `restrict children`) showing
  both modes side by side. Used by §5.2 runtime tests.
- **`cascade_self_ref.termin`** — `tree nodes` with self-reference
  `parent which references tree nodes, cascade on delete`. Tests
  cascade on a recursive structure.
- **`cascade_optional.termin`** — child with non-required cascade
  reference. Demonstrates NULL-FK behavior under cascade.
- **`cascade_multihop_ok.termin`** — A→B→C all cascade. Static
  check passes (no restrict involved). Runtime test verifies
  multi-hop propagation.

Negative fixtures (compile-time errors):

- **`cascade_bare_rejected.termin`** — `references X` with no
  cascade clause. Must produce TERMIN-S0XXa.
- **`cascade_on_text_rejected.termin`** — `is text, cascade on delete`.
  Must produce TERMIN-S0XXb.
- **`cascade_double_rejected.termin`** — `references X, cascade on delete, restrict on delete`.
  Must produce TERMIN-S0XXc.
- **`cascade_deadlock_simple_rejected.termin`** — A cascade B
  restrict C deadlock. Must produce TERMIN-S0XXd citing both edges.
- **`cascade_deadlock_diamond_rejected.termin`** — diamond pattern
  where two paths converge on a content with a restrict-protector.
  Must produce TERMIN-S0XXd.
- **`cascade_cycle_rejected.termin`** *(stretch — defer if cycle
  detection is descoped to a follow-on)* — A cascade B cascade A.
  Must produce TERMIN-S0XXe.

The negative fixtures are loaded by the test as raw `.termin` text
(not pre-compiled), since the compile step is what's being tested.

### 5.1 `test_v09_cascade_grammar.py` (parse-time)

- `test_bare_references_rejected` — TERMIN-S0XXa.
- `test_cascade_on_non_reference_rejected` — TERMIN-S0XXb.
- `test_duplicate_cascade_modes_rejected` — TERMIN-S0XXc.
- `test_cascade_position_flexible` — `references X, required, cascade on delete`
  AND `references X, cascade on delete, required` AND
  `references X, cascade on delete` all produce identical IR
  for the field.
- `test_cascade_clause_appears_in_ir` — both modes produce
  FieldSpec entries with the correct `cascade_mode` value.

### 5.1.5 `test_v09_cascade_static_check.py` (NEW — §3.11.5)

- `test_simple_cascade_restrict_deadlock_rejected` — TERMIN-S0XXd.
  Error message names every contributing edge.
- `test_diamond_cascade_restrict_deadlock_rejected` — TERMIN-S0XXd.
- `test_pure_cascade_chain_accepted` — A cascade B cascade C
  compiles cleanly.
- `test_pure_restrict_chain_accepted` — A restrict B restrict C
  compiles cleanly.
- `test_mixed_graph_no_shared_node_accepted` — cascade and
  restrict edges that don't share a target node compile cleanly.
- `test_self_cascade_accepted` — content with cascade self-ref
  is fine (target node is itself, but no cross with restrict).
- `test_cascade_target_with_unrelated_restrict_protector_rejected` —
  even when the cascade and restrict edges seem "unrelated" in the
  product domain, if they share a target node it's still a deadlock.
  The error message clarifies why.

### 5.2 `test_v09_cascade_runtime.py` (HTTP behavior)

Uses `cascade_demo.termin` + variants from §5.0:

- `test_delete_parent_with_no_children_succeeds`
- `test_delete_parent_cascades_to_cascade_children`
- `test_delete_parent_restricted_by_restrict_children` — 409.
- `test_delete_parent_with_mixed_children_blocked_by_restrict` —
  restrict wins, NOTHING deleted (transactional).
- `test_delete_after_clearing_restrict_children` — succeeds and
  cascades remaining cascade-children.
- `test_optional_reference_with_cascade` — NULL-FK children
  unaffected.
- `test_self_reference_cascade` — delete root → subtree gone.
- `test_multi_hop_cascade` — A→B→C all cascade; delete A → B
  and C gone.

### 5.3 `test_v09_cascade_ir_shape.py` (schema)

- `test_field_spec_has_cascade_mode_for_references` — every
  reference field in every fixture has `cascade_mode in
  {"cascade", "restrict"}`.
- `test_field_spec_no_cascade_mode_for_non_references` — non-
  reference fields have `cascade_mode is None`.
- `test_ir_schema_validates_cascade_mode` — fixtures pass JSON
  schema validation including the new `if/then/else` invariant.

### 5.4 TDD sequence

1. Land conformance test files (§5.1, §5.1.5, §5.2, §5.3) plus
   purpose-built fixtures (§5.0). Run against current
   compiler+runtime → all fail (RED). Bare-references tests fail
   because parser accepts; static-check tests fail because no
   such check exists; runtime tests fail because storage doesn't
   honor cascade; IR-shape tests fail because no `cascade_mode`
   field exists.
2. Land grammar+parser+analyzer changes (including §3.11.5 static
   check) → §5.1, §5.1.5, §5.3 grammar/static/IR tests pass.
   Runtime tests still RED.
3. Land runtime SQL `ON DELETE` emission → §5.2 runtime tests pass.
4. Migrate examples (§4.10) → existing fixtures regenerate clean
   under `util/release.py`.

## 6. Risks and known gaps

1. **Existing app.db files** survive cascade-grammar deploy with
   their old NO ACTION FK semantics. Documented; full fix lives in
   Phase 2.x (b).
2. **Cascade chain transactional gotcha** (§3.9): cascade →
   restrict mid-chain fails the whole transaction. Conformance
   test will document. Real-world apps need to design cascade
   graphs with this in mind.
3. **Confidentiality-aware blast radius** is not addressed.
   Documented as Phase 4+ work.
4. **Bare-references error message** depends on getting the
   actionable text right. Worth review during implementation.
5. **TatSu vs fallback parser** must agree on cascade detection.
   The fallback is substring-based and forgiving; the TatSu path
   is grammar-strict. Add a fidelity test in
   `tests/test_compiler_fidelity.py` covering all cascade-related
   field syntaxes.

## 7. Out of scope (explicitly deferred)

- Cascade migration story (Phase 2.x b)
- Idempotency keys for create() (Phase 2.x c)
- SM transitions through ctx.storage (Phase 2.x d)
- Keyset cursors (Phase 2.x e)
- CEL → Predicate AST compiler (Phase 2.x f)
- app.db-in-cwd cleanup (Phase 2.x g)
- Confidentiality-aware blast radius (Phase 4+)
- Admin override of schema-declared cascade mode (post-1.0)
- Postgres / DynamoDB cascade semantics (handled when those
  providers land)

## 8. Open questions

### Resolved (2026-04-26)

1. ~~§3.1 grammar shape~~ — **Option C** (cascade as new constraint
   alternatives + analyzer enforcement). Accepted.
2. ~~§3.6 IR schema enforcement~~ — **Structural** via JSON schema
   `if/then/else` per FieldSpec. Accepted.
3. ~~§4.10 example migration~~ — **Picks accepted**, with
   projectboard restructured for the static check (§4.11).
4. ~~§4.11 projectboard cascade graph~~ — **All-restrict design**
   (only Task→TimeLog cascades). New design decision §3.11.5
   adopted: static cascade-restrict mix detection at compile time.
5. ~~conformance test fixtures~~ — **Purpose-built**, in
   `termin-conformance/fixtures-cascade/` and
   `termin-compiler/examples-test/cascade/`. Includes both happy-
   path and deadlock-rejection fixtures so the static check is
   tested by example as well as unit tests.
6. ~~IR field naming~~ — **`cascade_mode`**. Accepted.

### Resolved (2026-04-26 follow-up)

1. **TERMIN-S codes** — no preference; allocate next available.
2. **Cascade cycle detection** — yes in this pass, self-refs
   allowed.
3. **Static check error citation depth** — cite all edges; over-
   cite is clearer than under-cite.
4. **Test fixture location** — `tests/fixtures/cascade/` on
   compiler side; `fixtures-cascade/` in conformance repo.
5. **Cascade fixture generation** — extend `util/release.py` to
   walk both `examples/` and `tests/fixtures/cascade/`.
6. **v0.8 → v0.9 DB migration story** — brief CHANGELOG note now
   as part of cascade implementation; full migration design doc is
   Phase 2.x (b) territory and will be drafted-then-reviewed before
   any Phase 2.x (b) implementation starts (per JL standing
   guidance).

---

End of design.
