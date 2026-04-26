# Migration Diff Classifier — Technical Design

**Status:** Approved for implementation (2026-04-26)
**Author:** Claude Anthropic
**Anchor:** v0.9 Phase 2.x item (b) per
`docs/termin-provider-system-brd-v0.9.md` §6.2 + §10
**Predecessor:** Phase 2 (storage contract) shipped the type surface;
this design fills in the runtime classifier and the SqliteStorageProvider's
modify/remove migration paths.

**Review notes (2026-04-26, JL):** Acknowledgment surface (A+D),
widen FieldChange.kind enum, schema-metadata protocol method
naming, v0.8→v0.9 cascade as risky-not-blocked, TERMIN-M code
class, fingerprint format, table name — all accepted. **Major
design upgrade adopted:** the single "risky" classification splits
into **low / medium / high** (§3.12). High-risk migrations require
a pre-migration backup, an automated validation step, and
operator acknowledgment. **Field renames brought in scope** with
operator-declared mapping in deploy config (§3.13) — JL's point:
"we should not delete all the data just because someone renamed
the field." Empty-table downgrade target is "low risk," not "safe."

This is a pre-implementation design. Nothing has been written yet.
The goal is to surface every decision before code lands so the
cascading changes (schema introspection, diff computation, change
classification, table-rebuild plumbing for SQLite ALTER limits,
risky-migration acknowledgment surface, conformance test pack) all
happen with one consistent shape.

---

## 1. Goal

When a Termin app is redeployed with an evolved IR (new fields,
removed fields, changed types, changed cascade modes, etc.), the
runtime must:

1. **Read** the current on-disk schema and **compute** a diff against
   the target (IR-declared) schema.
2. **Classify** every change as `safe`, `risky`, or `blocked` per
   BRD §6.2:
   - **Safe:** non-destructive, applies cleanly with zero operator
     interaction. The runtime applies safe diffs at startup.
   - **Risky:** semantics-changing or potentially-data-corrupting,
     but recoverable. Requires explicit operator acknowledgment via
     deploy config; without it, deploy fails closed.
   - **Blocked:** would lose data or break invariants in ways the
     runtime cannot recover from. Refuse the deploy unconditionally;
     the operator must reshape the IR or the data manually.
3. **Apply** the migration atomically — the entire diff lands or
   none of it does. Partial migrations are a contract violation
   (storage contract docstring already says so).
4. **Implement modify/remove** in the SqliteStorageProvider, working
   around SQLite's ALTER TABLE limitations via the canonical
   table-rebuild dance where needed.
5. **Specifically handle the v0.8 → v0.9 cascade migration** so
   apps with existing v0.8 databases can pick up the v0.9 cascade
   grammar without manual DBA work.

This is the second-largest design surface in v0.9 after the
provider system itself. The classifier rules are part of the
language semantics — every conforming runtime must classify the
same way for the same diff, or apps become non-portable.

## 2. Current state

### Contract surface (Phase 2, shipped)

`termin_runtime/providers/storage_contract.py`:
- `FieldChange(kind, field_name, detail)` — `kind` is one of
  `"added"`, `"removed"`, `"type_changed"`, `"constraint_changed"`.
  No classification at the field level today; it lives on
  ContentChange.
- `ContentChange(kind, content_name, classification, schema, field_changes)`
  — `kind` is `"added"` | `"removed"` | `"modified"`; `classification`
  is `"safe"` | `"risky"` | `"blocked"`. Validates in `__post_init__`.
- `MigrationDiff(changes)` — tuple of ContentChange. Has
  `is_blocked` and `has_risky` properties for runtime gating.
- `initial_deploy_diff(content_schemas)` — builds a "create everything
  fresh" diff with all changes classified safe. Used at first deploy.

### Provider implementation (Phase 2, partial)

`termin_runtime/providers/builtins/storage_sqlite.py:77-122`:
- `migrate(diff)` handles `"added"` changes by delegating to
  `init_db()` (which uses `CREATE TABLE IF NOT EXISTS`).
- `"modified"` and `"removed"` raise `NotImplementedError` —
  documented as "Phase 2.x completes the modify/remove paths after
  the runtime's diff classifier lands."
- `is_blocked` defensive check raises `ValueError` if the runtime
  passes a blocked diff (defense in depth — the runtime should
  reject before invoking).

### Schema introspection

**Does not exist.** `init_db()` only writes; nothing reads on-disk
schema today. We need a new helper that queries `sqlite_master` and
`PRAGMA table_info(...)` to reconstruct a comparable form.

### Diff classifier

**Does not exist.** Nothing today classifies a change as
safe/risky/blocked beyond `initial_deploy_diff`'s blanket "safe."

### Risky migration acknowledgment

**Does not exist.** Today's deploy config has no migration knobs.

### Cascade-mode migration

**Stub only.** The cascade grammar lands `cascade_mode` in IR but
the runtime can't apply it to existing tables — `init_db` uses
`CREATE TABLE IF NOT EXISTS`, which keeps the v0.8 schema. Phase
2.x (b) fills in the rebuild path.

## 3. Design decisions

### 3.1 Where does classification happen?

**Option A: Provider classifies.** Each provider (SqliteStorageProvider,
future PostgresStorageProvider, etc.) classifies changes itself
based on its own DDL capabilities.

**Option B: Runtime classifies; provider executes.** The runtime
owns the classification rules; providers just apply diffs.

**Recommendation: Option B.** Reasons:
1. **Portability of classification.** A change classified `risky`
   for SQLite must also be `risky` for Postgres, otherwise the
   user gets surprised when migrating between backends. The rules
   are language-level invariants, not backend implementation
   details.
2. **Conformance testability.** The classifier is a pure function
   from `(current_schema, target_schema) → MigrationDiff` —
   trivially unit-testable, no DB needed.
3. **Provider boundary discipline (BRD §6.2).** Providers do
   storage; the runtime does policy. Classification is policy.
4. **Phase 2's storage contract docstring already states this**:
   "providers do not classify."

The provider's job: given a classified MigrationDiff, apply it
atomically. The classification is the *runtime's* judgment about
what can/should/can't be applied; the provider is told "do this,
the runtime has already decided it's OK."

### 3.2 Schema introspection: where does the "current" schema come from?

The classifier needs `(current_schema, target_schema) → diff`.
Target is the IR's content schemas. Current must be read from the
deployed database.

**Option A: Re-derive from `sqlite_master` + `PRAGMA table_info`.**
Each table's CREATE TABLE statement is stored in `sqlite_master.sql`;
column types, defaults, NOT NULL come from `PRAGMA table_info(<table>)`;
foreign keys from `PRAGMA foreign_key_list(<table>)`; CHECK
constraints have to be parsed out of `sqlite_master.sql` since SQLite
doesn't expose them through PRAGMAs.

**Option B: Store the IR alongside the DB.** When `init_db` runs,
write the full IR to a metadata table (e.g., `_termin_schema`); on
subsequent boot, read that table to get the prior schema.

**Recommendation: Option B for the v0.9 reference SQLite provider,
with Option A as a fallback.**

Reasons:
1. **CHECK constraints are a pain to round-trip via sqlite_master**.
   Parsing the SQL text to recover them is fragile.
2. **The IR is the source of truth.** Storing the v0.8 IR as JSON
   in a `_termin_schema` table is straightforward, reliable, and
   gives us a clean comparison base.
3. **Migration from existing v0.8 DBs that don't have the metadata
   table yet** is the case where Option A matters. For first-time
   v0.9 boot on a v0.8 DB:
   - The metadata table doesn't exist → fall back to Option A
     introspection (`sqlite_master` + `PRAGMA`).
   - Compute the v0.8-shaped IR from introspection.
   - Use that as the "current" for the diff.
   - On successful migration, write the new IR to `_termin_schema`.

So the introspector has two implementations behind a single
interface: `read_metadata_table()` (preferred) and
`introspect_via_pragma()` (fallback).

The `_termin_schema` table:
```sql
CREATE TABLE _termin_schema (
    id INTEGER PRIMARY KEY,
    ir_version TEXT NOT NULL,
    deployed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    schema_json TEXT NOT NULL  -- the IR.content as JSON
);
```

After every successful migration, append a row. The latest row is
the current state. Old rows form a deploy history (useful for
support / debugging).

### 3.3 Classification rules (5-tier model — adopted 2026-04-26)

Per JL's review, "risky" splits into **low / medium / high** so
operators see proportionate operational expectations. High-risk
changes get a backup + validation step (§3.13). The five tiers,
worst-to-best:

- **blocked** — refuse the deploy. Data loss, impossible operation,
  or invariant that can't be expressed.
- **high** — rebuild required AND either data semantics change for
  existing records OR referential integrity briefly broken. Backup
  REQUIRED. Operator must ack each change. Validation step gates
  commit.
- **medium** — rebuild required but data preserved by INSERT
  SELECT, OR data values may need transformation/validation.
  Operator must ack. Validation step gates commit. No backup
  required (transaction rollback is sufficient).
- **low** — in-place ALTER, no data touched, easily reversible.
  Operator must ack (audit trail), but no backup or extended
  validation needed.
- **safe** — no operator interaction. Applies at startup.

**SQLite ALTER capability map (drives low vs higher tiers):**
- ADD COLUMN, RENAME COLUMN (3.25+), RENAME TO, DROP COLUMN
  (3.35+) — *in-place*, can be **low** or **safe**.
- Anything else (type change, default change, NOT NULL/UNIQUE/CHECK
  change, FK declaration change) — *requires rebuild*, cannot be
  lower than **medium**.

**Field-level changes** (per `FieldChange`):

| Change | Classification | Reasoning |
|--------|---------------|-----------|
| Add field, optional, no default | safe | ADD COLUMN; existing rows get NULL |
| Add field, optional, with default | safe | ADD COLUMN; new rows use default |
| Add field, required, with default | safe | ADD COLUMN with default; backfill is implicit |
| Add field, required, no default | **medium** | Rebuild not strictly required (ADD COLUMN with default works), but operator should know existing rows take default; treat as a values question |
| Add field, with foreign_key | **blocked** | Existing rows have NULL refs; violates the cascade-or-restrict invariant from grammar |
| Add field, with UNIQUE | **medium** | Backfill with NULL works (NULLs don't violate UNIQUE in SQLite); existing data validation needed for non-null defaults |
| Remove field, table empty | safe | Empty-table downgrade (target tier: low; auto-downgraded to safe only if change-kind itself is trivial — see §3.9) |
| Remove field, table non-empty | **blocked** | Data loss |
| Rename field (operator-declared mapping in deploy config) | **low** if same type, **medium** if type differs | RENAME COLUMN in-place when types match; rebuild + cast otherwise. See §3.13 |
| Change business_type, lossless widening (whole_number → number, number → text) | **medium** | Rebuild required for type change in SQLite; data preserved |
| Change business_type, lossy (text → whole_number) | **blocked** | Values may not parse |
| Add NOT NULL to nullable field | **high** | Rebuild + existing NULLs would violate; backfill required |
| Remove NOT NULL | **medium** | Rebuild required (SQLite ALTER doesn't support); data preserved |
| Add UNIQUE to existing field | **high** | Rebuild + existing duplicates would fail; data validation needed |
| Remove UNIQUE | **medium** | Rebuild required; data preserved |
| Add CHECK (min/max) | **high** | Rebuild + existing rows may violate |
| Remove CHECK | **medium** | Rebuild required; data preserved |
| Tighten min/max bounds | **high** | Rebuild + existing values may violate |
| Loosen min/max | **medium** | Rebuild required; loosening can't violate |
| Add enum value | **medium** | Rebuild required (CHECK changes); existing data preserved |
| Remove enum value | **high** | Rebuild + existing rows with that value violate; values need to be remapped (operator's job) |
| Add foreign_key to existing field | **blocked** | Existing values may not exist in target |
| Remove foreign_key | **medium** | Rebuild required; loosening |
| Change foreign_key target | **blocked** | Old values may not exist in new target |
| Change cascade_mode (any direction, including v0.8 NULL → v0.9 cascade/restrict) | **high** | Rebuild required to update ON DELETE clause; future delete behavior changes for existing records |

**Content-level changes** (per `ContentChange`):

| Change | Classification | Reasoning |
|--------|---------------|-----------|
| Add content (new table) | safe | CREATE TABLE |
| Remove content, table empty | safe | DROP TABLE |
| Remove content, table non-empty | **blocked** | Data loss |
| Rename content (operator-declared mapping) | **low** | ALTER TABLE RENAME TO; FK references update automatically in SQLite 3.26+ with `legacy_alter_table=OFF`. See §3.13 |
| Add state machine to existing content | **high** | New NOT NULL state column; backfill with initial state; rebuild |
| Remove state machine | **medium** | Column stays as-is (loose: orphaned column) or rebuild to drop; transition gating disappears |
| Change initial state of state machine | safe | Affects new records only |

**Aggregation rule (worst-case propagation):**

`blocked > high > medium > low > safe`

- `ContentChange.classification` = max(content_kind_classification, max(field_change_classifications))
- `MigrationDiff` overall classification = max across all ContentChange.classifications.

The existing `is_blocked` / `has_risky` properties on MigrationDiff
extend with `has_high_risk`, `has_medium_risk`, `has_low_risk` for
runtime gating (§3.12 details the gating).

### 3.4 Risky migration acknowledgment surface

When the diff has `has_risky == True`, the runtime must refuse to
apply unless the operator has explicitly acknowledged.

**Option A: Deploy config flag.**
```yaml
# deploy.yaml or deploy.json
migrations:
  acknowledge_risky: true
```

**Option B: Environment variable** (`TERMIN_ACK_RISKY_MIGRATION=1`).

**Option C: CLI flag on runtime invocation.**

**Option D: Per-change acknowledgment** — operator lists specific
changes they accept.

**Recommendation: Option A (deploy config) with Option D shape inside.**

```yaml
migrations:
  # Boolean blanket ack — accept any risky migration in this deploy.
  # Convenient for dev; risky in prod (no audit trail of what was
  # accepted).
  accept_any_risky: false

  # Per-change ack — list specific changes the operator has reviewed.
  # The runtime fingerprints each risky change (kind + content + field
  # + before/after detail) into a short hash; operator includes the
  # hashes here. If the IR drifts after acceptance, the hash changes
  # and the deploy refuses again.
  accepted_changes:
    - "tickets.priority:add_required_field:b3f2a"
    - "comments.ticket:cascade_mode_change:cascade:7d8e1"
```

Reasons:
1. **Audit trail.** The deploy config is checked into git; the ack
   is reviewable in PR history.
2. **Fingerprinting catches drift.** If the IR changes after the
   ack lands, the fingerprint changes and the migration refuses
   again — no accidental rubber-stamping of a different change.
3. **Blanket flag is a footgun, but useful in dev.** Provide it
   for local-dev convenience; document the risk.
4. **Per-change ack scales.** Operators reviewing a 50-content
   migration don't have to take all-or-nothing; they accept what
   they understand and the runtime tells them what's left.

The runtime emits a clear startup error when a risky diff is
unack'd:
```
Termin migration refused — 3 risky changes need explicit acknowledgment.

  [risky] tickets — add required field "priority" with no default
    fingerprint: b3f2a
    Add to deploy config: migrations.accepted_changes: ["tickets.priority:add_required_field:b3f2a"]

  [risky] comments — change cascade_mode of "ticket" from null (v0.8) to "cascade" (v0.9)
    fingerprint: 7d8e1
    ...

Or set migrations.accept_any_risky: true to accept all (dev only).
```

**Decision: Option A+D** (JL accepted, 2026-04-26). Deploy-config
flag with optional per-change fingerprint ack list.

### 3.5 Atomic rollback strategy

SQLite supports transactional DDL. The natural approach:

```python
async def migrate(self, diff):
    db = await self._connect()
    try:
        await db.execute("BEGIN")
        # ... apply every change ...
        await db.execute("COMMIT")
    except Exception:
        await db.execute("ROLLBACK")
        raise
```

Caveat: `PRAGMA foreign_keys` cannot change inside a transaction.
The table-rebuild dance (§3.6) requires temporarily disabling FK
checks. Solution: split the migrate() into two phases:
1. Pre-transaction: disable FKs.
2. Transaction: apply all changes.
3. Post-transaction: re-enable FKs, verify FK integrity (`PRAGMA foreign_key_check`).

If the post-transaction FK check fails, the data is in a
referentially broken state — rare, but recoverable only by
reverting to a backup. Document this as a known limitation;
classify any change that risks it as risky-or-blocked.

For SQLite ≥ 3.26, there's a less-invasive option: `PRAGMA
legacy_alter_table = OFF` + `PRAGMA defer_foreign_keys = ON`
inside the transaction. This defers FK enforcement to commit
time, which is exactly what we want. Use that when available.

### 3.6 Table-rebuild for changes ALTER TABLE can't handle

SQLite's ALTER TABLE supports:
- ADD COLUMN (any version)
- RENAME TO (any version)
- RENAME COLUMN (3.25+, 2018-09)
- DROP COLUMN (3.35+, 2021-03)

Things SQLite ALTER cannot do:
- Change a column's type
- Change a column's default
- Change NOT NULL/UNIQUE/CHECK constraints
- Change FK declarations (including ON DELETE clause — relevant for
  v0.8 → v0.9 cascade migration)

The canonical workaround is the **12-step dance** from the SQLite
docs (https://sqlite.org/lang_altertable.html), simplified to its
relevant steps:

```sql
PRAGMA foreign_keys = OFF;
BEGIN;
  -- 1. Create new table with new schema
  CREATE TABLE new_tickets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      priority TEXT NOT NULL DEFAULT 'medium',  -- new column
      ...
  );
  -- 2. Copy data, applying any value transformations
  INSERT INTO new_tickets (id, title, ...)
    SELECT id, title, ... FROM tickets;
  -- 3. Drop old table
  DROP TABLE tickets;
  -- 4. Rename new table to old name
  ALTER TABLE new_tickets RENAME TO tickets;
  -- 5. Recreate any indexes / triggers (we have none in v0.9)
COMMIT;
PRAGMA foreign_key_check;  -- verify referential integrity
PRAGMA foreign_keys = ON;
```

Caveats:
- Other tables' FK declarations referencing `tickets` survive the
  rebuild *as long as* the new table has the same name and the
  referenced columns have the same names + types. The drop-then-
  rename keeps the name; we just need to keep the `id` column
  shape stable. Always preserve `id INTEGER PRIMARY KEY
  AUTOINCREMENT` exactly.
- View definitions, triggers — none in v0.9.

For v0.9 cascade-mode change specifically: the rebuild is the
only way to update `ON DELETE CASCADE` / `ON DELETE RESTRICT`.
Every cascade-mode change goes through this path.

### 3.7 Cascade-mode migration — the v0.8 → v0.9 path

This is the specific case JL flagged for an explicit migration
note. v0.8 DBs have FK declarations without `ON DELETE` clauses.
v0.9 IR adds explicit cascade_mode. On first v0.9 boot:

1. **Introspect** existing tables. For each FK column, read
   `PRAGMA foreign_key_list(<table>)`; the `on_delete` field is
   `"NO ACTION"` (v0.8 default).
2. **Compute diff.** Every FK column has a "cascade_mode change"
   FieldChange: NO ACTION → cascade or restrict (per the new IR).
3. **Classify.** All cascade_mode changes are risky (§3.3). Without
   ack, deploy refuses.
4. **Acknowledge.** Operator either sets `accept_any_risky: true`
   (dev-friendly) or fingerprints each cascade-mode change.
5. **Apply.** For each affected table, run the rebuild dance with
   the new ON DELETE clauses.

This is one of the highest-value migration paths — every existing
v0.8 app needs it. The conformance suite gets dedicated tests for
it.

**Special-case: unblocking the migration.** v0.8 had no
cascade_mode field, so the IR comparison literally sees "added
field cascade_mode" rather than "changed cascade_mode." The
classifier needs to know that `null → "cascade"` and `null →
"restrict"` on a foreign-key column is the v0.8 → v0.9 migration,
not a fresh-add. Recognize via: `current FK has on_delete=NO ACTION
AND target FieldSpec has cascade_mode set`. Treat as `risky`,
emit the FK-rebuild plan.

### 3.8 Where does the diff get computed?

The classifier is a pure function. Where does it live in the
runtime?

**Recommendation: a new module
`termin_runtime/migrations/classifier.py`** (or
`termin_runtime/providers/migration_classifier.py`). The runtime
calls it from `lifespan` startup before invoking
`ctx.storage.migrate()`.

```python
# termin_runtime/app.py lifespan
current = await ctx.storage.read_schema_metadata()  # NEW provider method
diff = compute_migration_diff(current, target_schemas)  # classifier
if diff.is_blocked:
    raise RuntimeError(format_blocked_diff_error(diff))
if diff.has_risky and not _ack_covers(diff, deploy_config):
    raise RuntimeError(format_unacked_risky_error(diff))
await ctx.storage.migrate(diff)
await ctx.storage.write_schema_metadata(target_schemas)  # NEW provider method
```

`read_schema_metadata` and `write_schema_metadata` extend the
StorageProvider Protocol. For SQLite they read/write the
`_termin_schema` table; for first-time-v0.9-boot they fall back to
PRAGMA introspection (read) / no-op (write happens unconditionally
post-migration).

**Decision: as recommended** (JL accepted, 2026-04-26). Method
names `read_schema_metadata` / `write_schema_metadata` stay.

### 3.9 Empty-table classification — when does row count matter?

Per §3.3, several "blocked" or "risky" changes downgrade to "safe"
when the affected table is empty. The classifier needs to query
row counts.

This adds an asynchronous step (the classifier needs to await
counts from the DB) and couples the classifier to the live database
— not a pure function anymore.

**Option A: Classifier queries the DB.** Async classifier; takes
a connection.

**Option B: Two-phase classifier.** Pure function classifies
without empty-check; runtime then queries counts and downgrades.

**Option C: Always assume non-empty.** Operator pays the
acknowledgment tax even when the change is harmless on an empty
table. Easy.

**Decision: Option B** (JL accepted, 2026-04-26). Two-phase: pure
classifier first, async empty-check downgrade pass second.

**Downgrade target: low risk, NOT safe.** Per JL:

> "the downgrades should not be too safe. It should be to low risk
> instead of medium or high risk when the affected table is empty."

Reasoning: an empty-table change on a deployed app is unusual
("you've deployed the app and then never used it and then deploy
a different version of the app, that seems like a weird edge
case"). Keeping it at low (operator ack required, audit trail in
deploy config) preserves the operational expectation that any
change to an existing deployment is intentional. Downgrading all
the way to "safe" would silently apply changes the operator might
not have noticed.

Specifically, the downgrade rules:
- "Remove field, table non-empty" (blocked) → on empty: **low**.
- "Remove content, table non-empty" (blocked) → on empty: **low**.
- "Add UNIQUE" / "Add CHECK" / "Add NOT NULL" (high) → on empty:
  **low**.
- "Remove enum value" / "Tighten min/max" (high) → on empty: **low**.

(Genuinely-trivial changes like ADD COLUMN remain "safe" because
they're safe regardless of table contents.)

```python
diff = compute_migration_diff(current, target)  # pure
diff = await downgrade_for_empty_tables(diff, db)  # touches DB
```

### 3.10 Drop the "constraint_changed" FieldChange.kind?

Phase 2 storage_contract has `kind ∈ {added, removed, type_changed,
constraint_changed}`. In §3.3 I'm classifying many specific
constraint changes (NOT NULL, UNIQUE, min/max, enum values)
differently. Lumping them under `constraint_changed` loses
information — the classifier needs the specifics.

**Recommendation:** widen FieldChange.kind to:
- `added` (with full new field spec in detail)
- `removed`
- `type_changed` (business_type before/after)
- `required_added`, `required_removed`
- `unique_added`, `unique_removed`
- `bounds_changed` (min/max before/after)
- `enum_values_changed` (added/removed sets)
- `cascade_mode_changed` (before/after)
- `foreign_key_changed`

This is a breaking change to Phase 2's contract — but the contract
isn't externally consumed yet (only the SQLite provider sees it,
and SQLite ignores the kind). Land it now.

**Decision: widen the enum** (JL accepted, 2026-04-26).

### 3.11 What about indexes?

v0.9 doesn't generate indexes from the DSL (the auto-CRUD GET
filtering is full-table-scan; we'll add index inference later).
**Out of scope** for Phase 2.x (b). Document as Phase 3 territory
or later.

### 3.12 Risk gating, backup, and validation (NEW shape — JL, 2026-04-26)

**Decision: backup + validation step in scope for Phase 2.x (b),
gated by risk tier.** Per JL's review, migrations are not just
"apply schema changes"; they're "apply changes + validate result
+ commit or rollback." Each risk tier gets a proportionate
operational surface.

#### 3.12.1 Per-tier gating

| Tier | Operator ack required | Backup created | Validation step |
|------|----------------------|----------------|-----------------|
| safe | no | no | basic post-migration FK check |
| low | yes (per-change fingerprint OR `accept_any_risky`) | no | basic post-migration FK check |
| medium | yes (same surface as low) | no | full validation step (§3.12.3) |
| high | yes (same surface) | **yes** (§3.12.2) | full validation step (§3.12.3); backup is the recovery path on validation failure |
| blocked | n/a — refuse | n/a | n/a |

#### 3.12.2 Backup strategy for high-risk migrations (provider-specific)

**Backup is a provider responsibility** — different storage
backends have different primitives (filesystem copy for SQLite,
`pg_dump` or RDS PITR snapshot for Postgres, on-demand backup for
DynamoDB). The runtime knows *when* a backup is needed (high-risk
migration); the provider knows *how* to make one.

**StorageProvider Protocol addition:**

```python
async def create_backup(self) -> Optional[str]:
    """Create a backup of the storage state, suitable for
    recovering from a failed high-risk migration.

    Returns an operator-visible identifier for the backup (a file
    path for SQLite, a snapshot ARN for cloud DBs, etc.) — used
    to point the operator at the recovery path on validation
    failure.

    Returns None if this provider cannot create a backup in the
    current configuration (e.g., a cloud provider whose IAM
    policy blocks snapshot creation). The runtime treats None as
    a fail-closed signal: high-risk migrations refuse to proceed
    and the operator is told to back up externally first.

    Raises BackupFailedError on attempted-but-failed backup.
    """
```

**SqliteStorageProvider implementation:**

1. **Filesystem-level copy** of the SQLite database file.
   - Source: `self._db_path`
   - Destination: `<db_path>.pre-<iso-timestamp>.bak`
     (e.g., `app.db.pre-2026-04-26T14-32-15.bak`)
2. **Sync to disk** (`os.fsync` after copy).
3. **Verify the copy** with `PRAGMA integrity_check` on the
   backup file (cheap, fast, catches truncated copy).
4. **Return the backup path.**

If any step fails, raises BackupFailedError; the runtime turns
that into TERMIN-M004 with the underlying cause.

**Why filesystem copy and not SQLite VACUUM INTO?** `VACUUM INTO`
produces a defragmented copy but takes a write lock for the
duration. The filesystem copy is faster on idle DBs (most cases)
and works without coordinating with any other connection. For
deployments where the DB is bigger than RAM, both are fine.

#### 3.12.2.1 Backup retention messaging

**Decision (JL, 2026-04-26): operator's responsibility, with one
runtime-side touch — tell the operator a backup exists.**

Each provider's `create_backup()` returns an operator-visible
identifier. After a successful high-risk migration, the runtime
emits a one-line startup-log entry:

```
[termin] Migration committed. Backup created: app.db.pre-2026-04-26T14-32-15.bak
[termin] Backup retention is your responsibility — delete or archive once you're confident in the new app behavior.
```

After a failed migration, the same backup identifier appears in
the TERMIN-M003 error message as the recovery path.

Different providers will have different retention semantics:
- SQLite filesystem copy: stays until operator deletes.
- Postgres `pg_dump`: stays in the dump file until operator deletes.
- DynamoDB on-demand backup: subject to AWS retention policies.
- RDS PITR snapshot: managed by RDS retention window.

**No `cleanup_backups_older_than` config flag in v0.9** — the
runtime doesn't know what's safe to delete (different providers,
different operator preferences). Document the SQLite-specific
cleanup pattern in release notes: "to remove old backups,
`rm app.db.pre-*.bak` once you're ready."

#### 3.12.3 Validation step

After the migration's main transaction has applied all changes
(but before COMMIT for medium/high tier — see §3.12.4):

1. **Foreign-key integrity:** `PRAGMA foreign_key_check` returns
   no rows. (Critical when FK semantics changed; tightening
   constraints could expose previously-tolerated dangling refs.)
2. **Row count preservation** (high tier only): for every
   rebuilt table, `count(*)` matches the pre-migration count.
   Any divergence means INSERT SELECT lost rows — should never
   happen, but if it does we want to know before COMMIT.
3. **Schema metadata** matches: the new IR has been written to
   `_termin_schema` and reads back cleanly.
4. **Smoke read on each migrated content type:** `SELECT * FROM
   <table> LIMIT 1` succeeds (catches catastrophic structural
   issues that wouldn't fail a row count).

If any validation fails:
- Transaction is ROLLED BACK (the rebuild is undone).
- Backup file remains as recovery insurance for high-tier.
- Operator sees a precise error: which validation failed, on
  which table, and the path to the backup file (if applicable).
- Deploy fails — runtime exits non-zero.

Validation hooks for operator-supplied custom checks are a future
extension (post-v0.9). v0.9 ships only the auto checks above.

#### 3.12.4 Atomic-rollback shape

Wrapping the entire migration in a single transaction ensures
atomicity. SQLite supports transactional DDL, so `BEGIN;
... ROLLBACK;` undoes table creates, drops, renames, INSERT
SELECTs, the lot.

Sequence for a migration that includes high-risk changes:

```python
# 1. Backup (high tier only)
backup_path = await create_backup(db_path)

# 2. Open transaction with deferred FK checks
async with db.transaction():
    await db.execute("PRAGMA defer_foreign_keys = ON")  # if available
    # else: PRAGMA foreign_keys = OFF + manual foreign_key_check at end

    # 3. Apply each change in dependency order
    for change in diff.changes:
        await apply_change(db, change)

    # 4. Validate before commit
    validation_result = await run_validation(db, diff)
    if not validation_result.ok:
        raise MigrationValidationError(validation_result.failures, backup_path)

    # 5. Write schema metadata
    await write_schema_metadata(db, target_schemas)

# (transaction commits on context exit if no exception)

# 6. Re-enable FKs (outside transaction)
await db.execute("PRAGMA foreign_keys = ON")
```

If MigrationValidationError raises inside the transaction, the
context manager triggers ROLLBACK — every DDL change reverts.

#### 3.12.5 What about partial failure during the rebuild itself?

The 12-step table rebuild dance from §3.6 (CREATE new + INSERT
SELECT + DROP old + RENAME) executes inside the transaction. If
anything fails midway, the transaction rolls back the partial
work. The on-disk schema is untouched until COMMIT lands.

The only failure mode the rollback can't fix is OS-level disk
loss (file deleted, disk full mid-fsync). For high-tier, the
backup is the recovery; for medium-tier, the operator's last-known-
good is the previous deploy's backup or external snapshot.

### 3.13 Field and content renames (NEW — JL, 2026-04-26)

**JL flagged in review:**

> "If it looks like a remove and add, does that delete all the
> data in the field? Or can we migrate the data from the existing
> field into the new field in some cases? We should not delete
> all the data just because someone renamed the field."

The point is correct. The §3.3 default classification of rename
as "blocked" is the *protective* failure mode (deploy refuses, no
data lost) — but it doesn't help an operator who legitimately
wants to rename. They'd have to either reset the DB (data loss)
or do the rename manually outside Termin.

**Decision: bring renames in scope** for Phase 2.x (b) via
operator-declared rename mappings in deploy config.

#### 3.13.1 Deploy config mapping

```yaml
migrations:
  rename_fields:
    - content: "tickets"
      from: "old_priority"
      to: "priority"
  rename_contents:
    - from: "old_tickets"
      to: "tickets"
```

The config schema validates: each entry has the required keys;
`from` and `to` must be different; targets must exist in the
target IR.

#### 3.13.2 Classifier integration

1. Read rename mappings from deploy config before running the
   diff.
2. In the differ: if a remove + add pair matches a declared
   rename (same content for field renames; or same target IR
   shape for content renames), fold the two `FieldChange` /
   `ContentChange` entries into one `renamed` entry.
3. Classify the rename per type matching:
   - **Field rename, types match exactly:** **low risk** (in-place
     `ALTER TABLE RENAME COLUMN`, SQLite 3.25+).
   - **Field rename, types differ but lossless:** **medium risk**
     (rebuild + INSERT SELECT with cast).
   - **Field rename, types differ lossy:** **high risk** (rebuild
     + parsed cast; backup needed; values may not parse).
   - **Content rename, schema otherwise unchanged:** **low risk**
     (in-place `ALTER TABLE RENAME TO`; SQLite 3.26+ updates FK
     references in other tables automatically when
     `legacy_alter_table=OFF`).
   - **Content rename combined with field changes:** the
     classification is the worst-case across the rename and the
     field changes.

Unmatched remove+add pairs are NOT auto-detected as renames.
Without explicit mapping, the differ keeps them as separate
remove and add entries (and the standard classification rules
apply: remove-from-non-empty-table is blocked, etc.).

#### 3.13.3 New FieldChange and ContentChange kinds

Adds to the kind enum (§3.10):
- `FieldChange.kind = "renamed"` with detail `{from: str, to: str,
  type_changed: bool}`.
- `ContentChange.kind = "renamed"` with `content_name` set to
  the new name and detail `{from: str}`.

#### 3.13.4 Provider implementation

For SQLite, the renaming primitives are:
- Field, types match: `ALTER TABLE <content> RENAME COLUMN <from> TO <to>`
- Field, types differ: full rebuild dance with column rename + cast in
  the SELECT.
- Content: `ALTER TABLE <from> RENAME TO <to>`

Other tables' FK references update automatically with SQLite 3.26+
(`legacy_alter_table=OFF`); on older SQLite, the rebuild dance
applies to the referencing tables too. Detect via `sqlite_version()`.

#### 3.13.5 Edge cases

- **Rename then remove**: operator declares rename A→B then
  separately removes B. The classifier folds the rename first;
  then the remove gets classified normally. If B was non-empty
  after the rename, remove is blocked. (Operationally: do the
  rename in one deploy, the remove in the next.)
- **Cycle of renames** (A→B AND B→A): refuse at config-validation
  time. Cycles can't be applied atomically without a temporary
  name.
- **Multiple sources renamed to the same target**: refuse at
  config-validation. Ambiguous.
- **Content rename that conflicts with a separate add**: refuse.

## 4. Layer-by-layer plan

### 4.1 Storage contract (`termin_runtime/providers/storage_contract.py`)

- Widen `FieldChange.kind` per §3.10.
- Add to StorageProvider Protocol:
  - `async def read_schema_metadata(self) -> Optional[Mapping[str, Any]]`
    returns the last-stored schema; returns None on first-ever boot.
  - `async def write_schema_metadata(self, content_schemas) -> None`
    persists post-migration.
- (No change to MigrationDiff/ContentChange; the existing shape
  carries the new field-change variants fine.)

### 4.2 Schema introspector (NEW: `termin_runtime/migrations/introspect.py`)

```python
async def introspect_sqlite_schema(db, db_path) -> Mapping[str, Any]:
    """Return a dict mirroring the IR `content` shape, derived from
    sqlite_master and PRAGMA table_info / foreign_key_list.
    Used as the "current schema" when the _termin_schema metadata
    table is absent (v0.8 → v0.9 first boot)."""
```

Output mirrors the IR's content schema list. CHECK constraints are
parsed out of `sqlite_master.sql` text best-effort; if parsing fails,
the introspector emits a warning and skips that constraint (the
classifier will then show it as "added" and require ack).

### 4.3 Diff computer + classifier (NEW: `termin_runtime/migrations/classifier.py`)

```python
def compute_migration_diff(
    current: Mapping[str, Any] | None,
    target: Sequence[Mapping[str, Any]],
) -> MigrationDiff:
    """Pure function. Compares current and target schemas and
    returns a classified MigrationDiff. None current → all
    additions (initial-deploy shape)."""

def classify_field_change(change: FieldChange, content_kind: str) -> str:
    """Per §3.3 rules. Returns 'safe' | 'risky' | 'blocked'."""

def classify_content_change(change: ContentChange) -> str:
    """Aggregates field-level classifications + content-level kind
    rules. Returns 'safe' | 'risky' | 'blocked'."""

async def downgrade_for_empty_tables(
    diff: MigrationDiff, db
) -> MigrationDiff:
    """Per §3.9. For each modified/removed content with empty
    table, downgrade classification toward 'safe' where the rule
    allows."""
```

### 4.4 Acknowledgment surface (NEW: `termin_runtime/migrations/ack.py`)

```python
def fingerprint_change(change: ContentChange | FieldChange) -> str:
    """Stable short hash of a change. Format:
       <content>.<field>:<change-kind>:<short-hash> (for FieldChange)
       <content>:<change-kind>:<short-hash>          (for ContentChange)
    """

def ack_covers(diff: MigrationDiff, deploy_config) -> bool:
    """Returns True iff the deploy config has either
    accept_any_risky=true OR every low/medium/high risk change's
    fingerprint in accepted_changes."""

def format_blocked_error(diff): ...
def format_unacked_error(diff): ...  # covers low/medium/high
```

### 4.4.5 Backup as provider method (NOT a runtime module)

Per §3.12.2, backup is provider-specific. No `migrations/backup.py`
module — instead extend the StorageProvider Protocol:

```python
# storage_contract.py addition
async def create_backup(self) -> Optional[str]: ...
```

SqliteStorageProvider implements with a filesystem copy + fsync +
integrity_check.

The runtime calls `await ctx.storage.create_backup()` before
applying any high-risk diff. None return → fail closed with
TERMIN-M004 ("provider cannot create backup; back up externally
before retrying").

### 4.4.6 Validation module (NEW: `termin_runtime/migrations/validate.py`)

```python
@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    failures: tuple[str, ...]

async def run_validation(
    db, diff: MigrationDiff, target_schemas
) -> ValidationResult:
    """Run all auto-validation checks per §3.12.3:
      1. PRAGMA foreign_key_check
      2. Row-count preservation for high-tier rebuilt tables
      3. Schema metadata round-trip
      4. SELECT 1 from each migrated content type
    Returns ValidationResult with structured failures for the
    error formatter."""
```

### 4.4.7 Rename support (NEW: extension in `classifier.py`)

```python
def apply_rename_mappings(
    diff: MigrationDiff, deploy_config
) -> MigrationDiff:
    """Pre-classification pass: for each rename declared in the
    deploy config, fold matching remove+add into one renamed
    change. Validate the mapping (no cycles, no duplicate
    targets). Returns a new MigrationDiff with renames as
    first-class entries."""
```

### 4.5 SqliteStorageProvider extensions

`termin_runtime/providers/builtins/storage_sqlite.py`:
- Implement `read_schema_metadata` / `write_schema_metadata`
  (stores in `_termin_schema` table; auto-creates the table on
  first write).
- Replace the `NotImplementedError` for `modified` and `removed`
  with the table-rebuild dance per §3.6.
- Wire in the FK-checking PRAGMAs per §3.5.
- For `cascade_mode_changed` FieldChange, recompute the FK
  declaration's ON DELETE clause and rebuild the affected table.

### 4.6 Runtime wire-up

`termin_runtime/app.py` lifespan:
- Replace today's `await ctx.storage.migrate(initial_deploy_diff(schemas))`
  with the full flow:

```python
# 1. Read current schema
current = await ctx.storage.read_schema_metadata()
# (may be None on first-ever boot; provider falls back to PRAGMA
#  introspection for v0.8 → v0.9 case)

# 2. Compute diff (pure)
diff = compute_migration_diff(current, schemas)

# 3. Apply rename mappings (folds remove+add → renamed)
diff = apply_rename_mappings(diff, deploy_config.migrations)

# 4. Empty-table downgrade pass (touches DB)
diff = await downgrade_for_empty_tables(diff, ctx.storage)

# 5. Block / ack gating
if diff.is_blocked:
    raise MigrationBlockedError(format_blocked_error(diff))
if (diff.has_low_risk or diff.has_medium_risk or diff.has_high_risk) \
        and not ack_covers(diff, deploy_config.migrations):
    raise MigrationAckRequiredError(format_unacked_error(diff))

# 6. Backup if any high-risk (provider-specific)
backup_id = None
if diff.has_high_risk:
    backup_id = await ctx.storage.create_backup()
    if backup_id is None:
        raise MigrationBackupRefusedError(  # TERMIN-M004
            "Provider cannot create a backup. High-risk migration "
            "refused. Back up externally before retrying.")

# 7. Apply atomically + validate + write metadata + commit
try:
    await ctx.storage.migrate(diff)
    # provider runs its own validation step before COMMIT;
    # raises MigrationValidationError if anything fails.
except MigrationValidationError as e:
    if backup_id:
        e.backup_id = backup_id  # tag the error with backup ref
    raise
```

### 4.7 Deploy config schema

`docs/termin-deploy-schema.json`:
- Add `migrations` block per §3.4.

### 4.8 CHANGELOG note

Drafted in cascade-grammar-design §3.5 will land here for real:
the v0.8 → v0.9 cascade migration is now operator-driven via
`accept_any_risky` or per-change fingerprint ack.

## 5. Conformance test plan (TDD: red first)

Test pack `tests/test_v09_migration_classifier.py` (compiler-side)
and `tests/test_v09_migration.py` (conformance-side).

### 5.1 Compiler-side classifier unit tests

Pure-function tests with synthetic before/after schemas:

**Field-level (one test per row of the §3.3 table):**
- `test_add_optional_field_safe`
- `test_add_required_field_no_default_risky`
- `test_add_required_field_with_default_safe`
- `test_remove_field_empty_table_safe` (after empty-downgrade)
- `test_remove_field_non_empty_blocked`
- `test_change_business_type_lossless_safe`
- `test_change_business_type_lossy_blocked`
- `test_add_not_null_risky`
- `test_remove_not_null_safe`
- `test_add_unique_risky`
- `test_remove_unique_safe`
- `test_tighten_bounds_risky`
- `test_loosen_bounds_safe`
- `test_remove_enum_value_risky`
- `test_change_cascade_mode_risky` (the v0.8→v0.9 case)
- `test_change_foreign_key_target_blocked`

**Content-level:**
- `test_add_content_safe`
- `test_remove_empty_content_safe`
- `test_remove_non_empty_content_blocked`
- `test_add_state_machine_risky`

**Aggregation:**
- `test_diff_classification_uses_worst_case`
- `test_is_blocked_propagates`
- `test_has_risky_propagates`

**Empty-table downgrade:**
- `test_empty_downgrade_remove_field`
- `test_empty_downgrade_remove_content`
- `test_empty_downgrade_add_required_no_default`

**Fingerprinting:**
- `test_fingerprint_stable_across_runs`
- `test_fingerprint_changes_when_change_changes`
- `test_ack_covers_per_change`
- `test_ack_covers_blanket`

### 5.2 Conformance-side migration behavior tests

Need fixtures that simulate "v0.8 DB" and exercise the migration:

- **Fixture: `migration_v08_to_v09.termin`** — a small app with
  references; deployed once with v0.8-shape FK (no ON DELETE).
  Then: re-deploy with the v0.9 IR (cascade_mode populated).
  Test: deploy refuses without ack; deploy succeeds with ack;
  post-migration, FK declarations carry ON DELETE.
- **Fixture: `migration_add_field.termin`** — start with simple
  schema, redeploy with new optional field. Safe migration.
- **Fixture: `migration_blocked.termin`** — redeploy that drops a
  non-empty content. Refuses.
- **Test: rollback on failure** — patch the provider to raise
  mid-rebuild; verify all data still readable post-failure.

### 5.3 IR schema test

- `test_termin_schema_metadata_table_exists_post_migration`
- `test_metadata_table_records_ir_version`

## 6. Risks and known gaps

1. **CHECK constraint round-trip.** Parsing CHECK from
   `sqlite_master.sql` text is fragile. Failure mode: introspector
   misses a constraint; classifier sees "constraint_added";
   migration acks then re-adds it (idempotent if already present).
   Should be fine in practice but worth a test.
2. **State machine columns.** State columns are name-collision-prone
   with field columns. The IR's `state_machines` list is the source
   of truth, but a v0.8 DB might have a state column that the v0.9
   IR doesn't know about. Document as "v0.8 → v0.9 migration
   requires no state-column collisions" — practically always true.
3. **Concurrent migrations.** Two runtime processes booting against
   the same DB simultaneously could both try to migrate. SQLite's
   default rollback journal serializes them, but the second one
   might fail. Document as a single-process operational requirement;
   real backends will need lock-coordination.
4. **`PRAGMA defer_foreign_keys` not supported on every SQLite
   build.** Need version-detect at runtime. Fallback: use the
   "FK off" approach with `PRAGMA foreign_key_check` after.
5. **Backup before destructive migration is operator's job.** Phase
   2.x (b) does NOT make automatic backups. Document this.
6. **Per-change fingerprint stability.** Hashing the change shape
   is straightforward, but if we add new FieldChange.detail fields
   later, fingerprints will shift even when the user didn't change
   anything. Mitigation: hash only the load-bearing fields, not
   the whole detail dict.

## 7. Out of scope (explicitly deferred)

- **DSL-level rename syntax.** Operator-declared mappings in
  deploy config (§3.13) cover the migration case. Inline DSL
  rename (`renamed from "old name"` in the .termin file itself)
  is post-1.0 territory and would require its own design pass.
- **Index migrations.** v0.9 doesn't generate indexes from the
  DSL.
- **View / trigger migrations.** v0.9 doesn't use them.
- **Multi-step migration plans** (e.g., "add nullable, backfill,
  set NOT NULL"). Operator does this manually with two deploys.
- **Migration of state machine transition tables.** State machines
  are config-driven (transitions encoded in IR + runtime maps);
  they don't have a SQL representation that needs migration.
- **Postgres / other backend implementations.** Phase 2.x (b)
  ships the SQLite implementation only; the contract is shaped to
  permit other backends but their concrete migrate() lives in
  follow-on work.
- **Automatic backups.** Operator's responsibility.
- **Cross-process migration locking.** Single-process operational
  assumption.

## 8. Open questions

### Resolved (2026-04-26)

1. ~~§3.4 acknowledgment surface~~ — **Option A+D**. Deploy-config
   flag with per-change fingerprint ack list.
2. ~~§3.10 widen FieldChange.kind enum~~ — **accepted**.
3. ~~§3.8 protocol method names~~ — **`read_schema_metadata` /
   `write_schema_metadata`** stay.
4. ~~§3.7 v0.8 → v0.9 cascade as risky-not-blocked~~ — **accepted**;
   classifies as **high risk** under the new 5-tier model.
5. ~~§3.12 backup-before-risky out of scope~~ — **REVERSED**.
   In-scope; replaced with the 5-tier model + backup + validation
   step (§3.12 + §3.13).
6. ~~§3.5 PRAGMA foreign_keys interaction~~ — `defer_foreign_keys`
   when available, FK-off + check otherwise.
7. ~~TERMIN-M code class~~ — **TERMIN-M001+** for migration errors.
8. ~~Empty-table downgrade~~ — Option B (two-phase classifier).
   Downgrade target is **low risk**, NOT safe.
9. ~~Fingerprint format~~ — `<content>.<field>:<change-kind>:<short-hash>`
   for FieldChange; `<content>:<change-kind>:<short-hash>` for
   ContentChange. Short-hash is 5 hex chars from SHA-256 of the
   change's structured detail.
10. ~~Table name~~ — `_termin_schema` (matches `_termin_*` prefix).

### Field/content renames (NEW from JL's review)

11. **In scope** via operator-declared mappings in deploy config
    (§3.13). Field rename: low risk if same type, medium if type
    differs lossless, high if lossy. Content rename: low risk.

### Risk-tier model (NEW from JL's review)

12. **5-tier model adopted** (§3.12.1): blocked / high / medium /
    low / safe. High requires backup + validation; medium
    requires validation; low is operationally minimal.

### Resolved (2026-04-26 follow-up)

1. **TERMIN-M code allocation** — accepted as drafted (M001
   blocked, M002 unack'd, M003 validation-failed, M004 backup-
   creation-failed/refused, M005 rename-mapping-cycle, M006
   rename-mapping-mismatch).
2. **Backup retention** — operator's responsibility per provider.
   Runtime emits a one-line log entry naming the backup
   identifier; no auto-cleanup, no `cleanup_backups_older_than`
   flag (provider semantics differ — DynamoDB doesn't have a
   filesystem path; cloud providers may have their own retention
   windows). The backup mechanism itself moves to the
   StorageProvider Protocol (§3.12.2).
3. **Validation step extensibility** — leave unspecified for
   v0.9. Add a v1.0 roadmap note to consider operator-supplied
   custom validators.
4. **Implementation scope** — single commit, as I leaned.
5. **`accept_any_risky` default** — false everywhere. No dev-mode
   auto-true.

---

End of design.
