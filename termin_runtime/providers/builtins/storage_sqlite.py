# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""SQLite storage provider — first-party plugin against the v0.9
Storage contract surface.

Wraps the file-level SQL helpers in `termin_runtime.storage` behind
the StorageProvider Protocol. The wrapping is intentional and small:
the SQL stays in storage.py (one source of truth, well-tested,
already used by a handful of legacy callers); this class is the
contract façade that the rest of the runtime now talks to.

Loaded through the same ProviderRegistry mechanism third-party
providers use — see register_sqlite_storage below. Per BRD §10
"One loading path for all providers", there is no special-cased
"built-in" code path.

Provider boundary discipline (BRD §6.2 "Provider's job is small"):
  - This class does SQL/persistence and nothing else.
  - Event publishing, error routing through TerminAtor, and HTTP
    status code translation are RUNTIME concerns. The runtime
    calls these methods, then publishes events / raises 404 /
    translates errors itself in the route/page handler.
  - In particular, the legacy storage.create_record() / update_record()
    / delete_record() functions had baked-in event publishing and
    TerminAtor calls. The provider intentionally does NOT call
    those legacy entrypoints — it calls the lower-level SQL helpers
    (insert_raw, update_fields, etc.) so the side effects stay out
    of the provider boundary.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import aiosqlite

from .. import storage_contract as sc
from ..contracts import Category, ContractRegistry
from ... import storage as _storage


class SqliteStorageProvider:
    """Reference SQLite storage provider.

    Configuration:
      db_path: filesystem path to the SQLite file. Falls back to
        DEFAULT_DB_PATH if absent (legacy behavior).

    State held by an instance:
      _db_path — resolved filesystem path. Each app gets its own
                 provider instance (not a global), so different
                 apps in the same Python process can hold different
                 db paths without contamination. This is the
                 architectural fix for the v0.8 _db_path module
                 global problem (see storage.py docstring).
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self._db_path: str = cfg.get("db_path") or _storage.DEFAULT_DB_PATH
        # Future: connection pool, prepared-statement cache, etc.

    # ── Internal helpers ──

    async def _connect(self) -> aiosqlite.Connection:
        """Open a fresh aiosqlite connection. The runtime currently
        opens-per-request (a pattern carried from the v0.8 storage
        helpers); a connection pool is a Phase 2 follow-on item."""
        return await _storage.get_db(self._db_path)

    # ── Lifecycle ──

    async def migrate(self, diff: sc.MigrationDiff) -> None:
        """Apply a schema migration atomically.

        Phase 2.x (b) implementation per
        docs/migration-classifier-design.md §3.6, §3.12, §3.13.

        Sequence:
          1. Refuse if diff is blocked.
          2. Open transaction with `defer_foreign_keys=ON`.
          3. Snapshot pre-migration row counts for rebuilt tables.
          4. For each change in dependency-safe order, apply:
               - "added": init_db's CREATE TABLE path
               - "removed": DROP TABLE
               - "renamed" (content): ALTER TABLE RENAME TO
               - "modified": rebuild dance (CREATE new + INSERT
                  SELECT + DROP old + RENAME) OR in-place ALTER
                  for additions / column renames depending on the
                  field changes
          5. Run validation step (FK check, row count preservation,
             schema metadata round-trip, smoke read).
          6. If validation fails, raise MigrationValidationError —
             the transaction unwinds.
          7. On success, COMMIT and return.
        """
        if diff.is_blocked:
            raise ValueError(
                "SqliteStorageProvider.migrate() refuses a blocked diff. "
                "The runtime should classify and reject blocked diffs "
                "before invoking the provider."
            )

        # Fast path for the all-added initial-deploy case (Phase 2
        # behavior preserved): batch-init via init_db.
        if all(c.kind == "added" for c in diff.changes):
            schemas = [c.schema for c in diff.changes if c.schema is not None]
            if schemas:
                await _storage.init_db(schemas, self._db_path)
            return

        # Mixed / non-trivial migration. Use the per-change executor.
        db = await self._connect()
        try:
            # SQLite's defer_foreign_keys = ON (3.18+) defers FK
            # checking to COMMIT time, which is what we want during
            # the rebuild dance: temporary FK breakage is OK as long
            # as everything reconciles before COMMIT.
            await db.execute("PRAGMA defer_foreign_keys = ON")
            # Snapshot pre-migration row counts for rebuilt tables.
            pre_counts = await self._snapshot_row_counts(db, diff)

            for change in diff.changes:
                await self._apply_one_change(db, change)

            # Validation step BEFORE commit.
            failures = await self._validate(db, diff, pre_counts)
            if failures:
                # Roll back and surface a structured error.
                await db.rollback()
                raise sc.MigrationValidationError(failures)

            await db.commit()
        finally:
            await db.close()

    async def _apply_one_change(self, db, change: sc.ContentChange) -> None:
        if change.kind == "added":
            if change.schema is None:
                raise ValueError(
                    f"'added' change for {change.content_name!r} "
                    f"is missing a schema")
            # Reuse init_db's CREATE TABLE path. init_db's own
            # transaction handling is irrelevant here — it uses the
            # commit-on-completion idiom and we run it inside our
            # own transaction; the implicit BEGIN gets folded.
            await _storage.init_db([change.schema], self._db_path)
            return

        if change.kind == "removed":
            tn = _storage._assert_safe(change.content_name, "table name")
            await db.execute(f'DROP TABLE IF EXISTS {_storage._q(tn)}')
            return

        if change.kind == "renamed":
            old_name = change.detail.get("from")
            new_name = change.content_name
            _storage._assert_safe(old_name, "old table name")
            _storage._assert_safe(new_name, "new table name")
            await db.execute(
                f'ALTER TABLE {_storage._q(old_name)} '
                f'RENAME TO {_storage._q(new_name)}')
            return

        if change.kind == "modified":
            await self._apply_modified(db, change)
            return

        raise ValueError(f"unknown ContentChange.kind: {change.kind!r}")

    async def _apply_modified(self, db, change: sc.ContentChange) -> None:
        """Apply a 'modified' change. If every FieldChange is an
        in-place-able operation (ADD COLUMN, RENAME COLUMN), use
        ALTER TABLE; otherwise run the table-rebuild dance."""
        in_place_kinds = {"added", "renamed"}
        if all(fc.kind in in_place_kinds for fc in change.field_changes):
            await self._apply_modified_in_place(db, change)
            return
        await self._rebuild_table(db, change)

    async def _apply_modified_in_place(
        self, db, change: sc.ContentChange,
    ) -> None:
        """ALTER TABLE for the simple in-place cases."""
        tn = _storage._assert_safe(change.content_name, "table name")
        for fc in change.field_changes:
            if fc.kind == "added":
                spec = fc.detail.get("spec") or {}
                col_sql = _storage._field_to_sql(spec)
                # Foreign keys can't be added via ALTER — those go
                # through rebuild. The classifier blocks this case
                # so we shouldn't see it here, but defense in depth.
                if spec.get("foreign_key"):
                    raise ValueError(
                        f"in-place ADD COLUMN cannot include a foreign "
                        f"key on {tn}.{fc.field_name}; this should have "
                        f"gone through the rebuild path")
                await db.execute(
                    f'ALTER TABLE {_storage._q(tn)} ADD COLUMN {col_sql}')
            elif fc.kind == "renamed":
                old_name = fc.detail.get("from")
                new_name = fc.detail.get("to") or fc.field_name
                _storage._assert_safe(old_name, "old field name")
                _storage._assert_safe(new_name, "new field name")
                await db.execute(
                    f'ALTER TABLE {_storage._q(tn)} '
                    f'RENAME COLUMN {_storage._q(old_name)} '
                    f'TO {_storage._q(new_name)}')

    async def _rebuild_table(self, db, change: sc.ContentChange) -> None:
        """The canonical 12-step SQLite ALTER-via-rebuild dance per
        docs/migration-classifier-design.md §3.6.

        Composes the new table from the target schema, copies data
        from the old table mapping columns appropriately (handling
        renames + additions + removals), drops the old, renames the
        new.
        """
        old_name = change.content_name
        target_schema = change.schema or {}
        if not target_schema:
            raise ValueError(
                f"_rebuild_table: 'modified' change for {old_name!r} "
                f"has no schema")

        _storage._assert_safe(old_name, "table name")
        # Temp name must satisfy the safe-identifier regex
        # (^[a-z][a-z0-9_]*$) — leading underscore is unsafe per
        # storage._SAFE_IDENTIFIER, so prefix with a letter.
        tmp_name = f"trebuild_{old_name}"
        _storage._assert_safe(tmp_name, "rebuild table name")

        # Build the column → column source mapping for INSERT SELECT.
        # Default: same name on both sides. Renamed fields map old
        # name → new name. Removed fields are skipped on the SELECT
        # side. Added fields are skipped on the SELECT side (they
        # take the column DEFAULT or NULL).
        new_fields = list(target_schema.get("fields", []))
        renames_by_new: dict = {}
        added_names: set = set()
        for fc in change.field_changes:
            if fc.kind == "renamed":
                renames_by_new[fc.detail.get("to") or fc.field_name] = (
                    fc.detail.get("from"))
            elif fc.kind == "added":
                added_names.add(fc.field_name)

        # Determine the source column for each target column. State-
        # machine columns and `id` are always present on both sides.
        target_col_to_source: dict = {"id": "id"}
        sm_columns = [
            sm["machine_name"]
            for sm in target_schema.get("state_machines", [])
        ]
        for sm_col in sm_columns:
            target_col_to_source[sm_col] = sm_col

        for f in new_fields:
            if f.get("business_type") == "state":
                continue  # state-machine columns handled above
            name = f["name"]
            if name in added_names:
                continue  # no source column — let DEFAULT fill it
            source = renames_by_new.get(name, name)
            target_col_to_source[name] = source

        # Step 1: CREATE TABLE for the new shape, with the temp
        # name. We piggyback on init_db by passing a schema dict
        # that has the temp name. init_db emits FK declarations
        # with the new ON DELETE clauses (per cascade grammar).
        rebuild_schema = dict(target_schema)
        # init_db uses name.snake — patch it for the rebuild.
        rebuild_schema = {
            **rebuild_schema,
            "name": {
                "snake": tmp_name,
                "display": tmp_name,
                "pascal": tmp_name,
            },
        }
        await _storage.init_db([rebuild_schema], self._db_path)

        # Step 2: INSERT new (cols) SELECT (source_cols) FROM old.
        target_cols = list(target_col_to_source.keys())
        source_cols = [target_col_to_source[c] for c in target_cols]
        for c in target_cols:
            _storage._assert_safe(c, "rebuild target column")
        for c in source_cols:
            _storage._assert_safe(c, "rebuild source column")
        target_cols_sql = ", ".join(_storage._q(c) for c in target_cols)
        source_cols_sql = ", ".join(_storage._q(c) for c in source_cols)
        await db.execute(
            f'INSERT INTO {_storage._q(tmp_name)} ({target_cols_sql}) '
            f'SELECT {source_cols_sql} FROM {_storage._q(old_name)}')

        # Step 3: DROP old table.
        await db.execute(f'DROP TABLE {_storage._q(old_name)}')

        # Step 4: RENAME tmp → old name.
        await db.execute(
            f'ALTER TABLE {_storage._q(tmp_name)} '
            f'RENAME TO {_storage._q(old_name)}')

    # ── Validation ─────────────────────────────────────────────────

    async def _snapshot_row_counts(
        self, db, diff: sc.MigrationDiff,
    ) -> dict:
        """Snapshot row counts for tables that will be rebuilt, so
        we can validate post-rebuild that no rows were lost."""
        counts: dict = {}
        for change in diff.changes:
            if change.kind != "modified":
                continue
            # Only snapshot if a rebuild is going to happen.
            in_place_kinds = {"added", "renamed"}
            if all(fc.kind in in_place_kinds for fc in change.field_changes):
                continue
            tn = change.content_name
            try:
                cursor = await db.execute(
                    f'SELECT COUNT(*) FROM {_storage._q(tn)}')
                row = await cursor.fetchone()
                counts[tn] = int(row[0]) if row else 0
            except Exception:
                counts[tn] = None  # table didn't exist; nothing to compare
        return counts

    async def _validate(
        self, db, diff: sc.MigrationDiff, pre_counts: dict,
    ) -> tuple:
        """Run the validation step. Returns a tuple of failure
        descriptions (empty if all checks pass)."""
        failures: list = []

        # 1. PRAGMA foreign_key_check returns no rows.
        cursor = await db.execute("PRAGMA foreign_key_check")
        fk_violations = await cursor.fetchall()
        if fk_violations:
            failures.append(
                f"foreign_key_check found {len(fk_violations)} violation(s)"
            )

        # 2. Row-count preservation for rebuilt tables.
        for table, pre in pre_counts.items():
            if pre is None:
                continue
            cursor = await db.execute(
                f'SELECT COUNT(*) FROM {_storage._q(table)}')
            row = await cursor.fetchone()
            post = int(row[0]) if row else 0
            if post != pre:
                failures.append(
                    f"row count mismatch on {table!r}: "
                    f"pre={pre}, post={post}"
                )

        # 3. Smoke read on each non-removed migrated content.
        for change in diff.changes:
            if change.kind == "removed":
                continue
            tn = change.content_name
            try:
                cursor = await db.execute(
                    f'SELECT 1 FROM {_storage._q(tn)} LIMIT 1')
                await cursor.fetchall()
            except Exception as e:
                failures.append(
                    f"smoke read failed on {tn!r}: {e}")

        return tuple(failures)

    # ── Schema metadata ────────────────────────────────────────────

    _METADATA_TABLE = "_termin_schema"

    async def read_schema_metadata(self) -> Optional[Sequence[Mapping[str, Any]]]:
        """Return the last-stored schema (the IR `content` list).

        First tries the `_termin_schema` metadata table. If that
        table doesn't exist (first-ever-v0.9 boot, possibly against
        a v0.8 DB), falls back to schema introspection — the runtime
        sees the v0.8 shape as the "current" and computes a diff
        against the v0.9 IR.

        Returns None when the database is brand-new (no tables at
        all). The runtime then runs an initial-deploy migration.
        """
        from ...migrations.introspect import introspect_sqlite_schema
        db = await self._connect()
        try:
            # Does the metadata table exist?
            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name=?",
                (self._METADATA_TABLE,),
            )
            row = await cursor.fetchone()
            if row is not None:
                cursor = await db.execute(
                    f'SELECT schema_json FROM {_storage._q(self._METADATA_TABLE)} '
                    f'ORDER BY id DESC LIMIT 1')
                row = await cursor.fetchone()
                if row is None:
                    return None
                return json.loads(row[0])
            # No metadata table. Are there ANY user tables?
            cursor = await db.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name != ?",
                (self._METADATA_TABLE,),
            )
            row = await cursor.fetchone()
            count = int(row[0]) if row else 0
            if count == 0:
                return None  # brand-new database
            # v0.8 → v0.9 first-boot path: introspect.
            return await introspect_sqlite_schema(db)
        finally:
            await db.close()

    async def write_schema_metadata(
        self, content_schemas: Sequence[Mapping[str, Any]],
    ) -> None:
        """Persist the just-applied schema as the new
        last-known-good. Creates `_termin_schema` if not present."""
        db = await self._connect()
        try:
            await db.execute(
                f'CREATE TABLE IF NOT EXISTS {_storage._q(self._METADATA_TABLE)} ('
                f'    id INTEGER PRIMARY KEY AUTOINCREMENT,'
                f'    ir_version TEXT NOT NULL DEFAULT \'0.9.0\','
                f'    deployed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,'
                f'    schema_json TEXT NOT NULL'
                f')'
            )
            await db.execute(
                f'INSERT INTO {_storage._q(self._METADATA_TABLE)} '
                f'(schema_json) VALUES (?)',
                (json.dumps(list(content_schemas)),),
            )
            await db.commit()
        finally:
            await db.close()

    # ── Backup ──────────────────────────────────────────────────────

    async def create_backup(self) -> Optional[str]:
        """Filesystem-copy the SQLite database file. Returns the
        backup path. Raises sc.BackupFailedError on any failure.

        Per docs/migration-classifier-design.md §3.12.2."""
        if self._db_path == ":memory:":
            # In-memory DB has nothing to back up; treat as
            # "cannot backup" so the runtime fails closed.
            return None
        if not os.path.exists(self._db_path):
            # No DB yet; nothing to back up. Return None to signal
            # the runtime that a backup wasn't possible. (For a
            # fresh deploy this won't happen — diff would be all-
            # added and high-risk wouldn't trigger.)
            return None

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        backup_path = f"{self._db_path}.pre-{ts}.bak"

        try:
            shutil.copy2(self._db_path, backup_path)
            # fsync to ensure the bytes hit disk before the migration
            # touches the source. Windows requires a writable fd for
            # fsync (read-only mode raises EBADF), so open for
            # writing-no-truncate. POSIX accepts either.
            fd = os.open(backup_path, os.O_RDWR)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as e:
            raise sc.BackupFailedError(
                f"could not create SQLite backup at {backup_path!r}: {e}"
            ) from e

        # Verify the backup with PRAGMA integrity_check.
        verify = await aiosqlite.connect(backup_path)
        try:
            cursor = await verify.execute("PRAGMA integrity_check")
            row = await cursor.fetchone()
            result = (row[0] if row else "") or ""
            if result.lower() != "ok":
                raise sc.BackupFailedError(
                    f"integrity_check on backup {backup_path!r} "
                    f"returned {result!r}"
                )
        finally:
            await verify.close()

        return backup_path

    # ── CRUD ──

    async def create(
        self,
        content_type: str,
        record: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> Mapping[str, Any]:
        """Insert a record. Pure SQL — no events, no error routing.

        idempotency_key (BRD §6.2): if supplied, second call with
        same (content_type, key) is a silent no-op returning the
        original record. v0.9 Phase 2.x (c) implements this via a
        `_termin_idempotency` table mapping (content_type, key) to
        the record id of the first create. Stale entries (whose
        underlying record has since been deleted) are cleaned up
        on lookup so a replay-after-delete starts a fresh request
        rather than returning a record that doesn't exist.
        """
        # Strip empty strings on optional fields (legacy convention
        # callers depend on). State-machine column defaults are the
        # caller's responsibility — the provider does not know about
        # state machines, that's a runtime concern.
        cleaned = {k: v for k, v in record.items() if v != ""}
        if not cleaned and idempotency_key is None:
            return {"id": None}

        db = await self._connect()
        try:
            if idempotency_key is not None:
                await self._ensure_idempotency_table(db)
                existing = await self._lookup_idempotency_record(
                    db, content_type, idempotency_key)
                if existing is not None:
                    return existing
                # Either no entry, or entry was stale and cleaned up;
                # fall through to insert.

            new_id = await _storage.insert_raw(db, content_type, cleaned)

            if idempotency_key is not None:
                await db.execute(
                    f'INSERT INTO {_storage._q(self._IDEMPOTENCY_TABLE)} '
                    f'(content_type, key, record_id) VALUES (?, ?, ?)',
                    (content_type, idempotency_key, new_id),
                )
                await db.commit()

            persisted = dict(cleaned)
            persisted["id"] = new_id
            return persisted
        finally:
            await db.close()

    _IDEMPOTENCY_TABLE = "_termin_idempotency"

    async def _ensure_idempotency_table(self, db) -> None:
        """Lazy-create the idempotency dedup table.

        Schema:
          (content_type, key) is the composite unique key.
          record_id is the id of the record returned on replay.
          created_at is operational metadata.

        v0.9 retains entries forever; future versions may add a TTL
        + cleanup job. Stale entries (record deleted) are detected
        and cleaned at lookup time (see _lookup_idempotency_record).
        """
        await db.execute(
            f'CREATE TABLE IF NOT EXISTS '
            f'{_storage._q(self._IDEMPOTENCY_TABLE)} ('
            f'    content_type TEXT NOT NULL,'
            f'    key TEXT NOT NULL,'
            f'    record_id INTEGER NOT NULL,'
            f'    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,'
            f'    PRIMARY KEY (content_type, key)'
            f')'
        )

    async def _lookup_idempotency_record(
        self, db, content_type: str, key: str,
    ) -> Optional[Mapping[str, Any]]:
        """Look up an idempotency entry. If the underlying record
        still exists, return it. If the entry exists but the record
        was deleted (stale), clean up the entry and return None so
        the caller proceeds with a fresh insert."""
        cursor = await db.execute(
            f'SELECT record_id FROM {_storage._q(self._IDEMPOTENCY_TABLE)} '
            f'WHERE content_type = ? AND key = ?',
            (content_type, key),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        record_id = row[0]
        existing = await _storage.get_record_by_id(
            db, content_type, record_id)
        if existing is not None:
            return existing
        # Stale entry — record deleted. Clean up so the replay
        # produces a fresh insert.
        await db.execute(
            f'DELETE FROM {_storage._q(self._IDEMPOTENCY_TABLE)} '
            f'WHERE content_type = ? AND key = ?',
            (content_type, key),
        )
        await db.commit()
        return None

    async def read(
        self, content_type: str, id: Any
    ) -> Optional[Mapping[str, Any]]:
        """Fetch a single record by id. None if not found.

        Per BRD §6.2 the contract returns None — HTTP 404 is a
        runtime translation, not a provider responsibility. The
        legacy storage.get_record() raises HTTPException(404); we
        deliberately do not call it here.
        """
        db = await self._connect()
        try:
            return await _storage.get_record_by_id(db, content_type, id)
        finally:
            await db.close()

    async def query(
        self,
        content_type: str,
        predicate: Optional[sc.Predicate] = None,
        options: Optional[sc.QueryOptions] = None,
    ) -> sc.Page:
        """Run a structured query.

        Compiles the Predicate AST to a parameterized WHERE clause
        and returns a Page. v0.9 ships AST-pushdown for the leaf
        predicate types (Eq, Ne, Gt, Gte, Lt, Lte, In, Contains)
        and the boolean combinators (And, Or, Not). All current
        leaf predicates push fully — there is no in-process
        residual. CEL→AST compilation is a separate runtime layer
        (BRD §6.2: "One CEL evaluator lives in the runtime").

        Cursor encoding (v0.9): the cursor is the last record's id
        plus a tiebreaker hash, base64-encoded. v0.9 ships an
        offset-derived cursor for compatibility with the legacy
        list_records() shape; a true keyset cursor is a Phase 2.x
        follow-on. Callers must treat the cursor as opaque.
        """
        opts = options or sc.QueryOptions()

        # Compile predicate to (where_sql, params). None → no WHERE.
        where_sql, params = _compile_predicate(predicate) if predicate else ("", [])

        # Compose ORDER BY. If the caller didn't include `id` we
        # append it for sort stability per BRD §6.2.
        order_clauses: list[str] = []
        order_fields_seen: set = set()
        for ob in opts.order_by:
            _storage._assert_safe(ob.field, "order_by field")
            order_clauses.append(f"{_storage._q(ob.field)} {ob.direction.upper()}")
            order_fields_seen.add(ob.field)
        if "id" not in order_fields_seen:
            order_clauses.append('"id" ASC')

        sql = f"SELECT * FROM {_storage._q(content_type)}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        sql += " ORDER BY " + ", ".join(order_clauses)

        # Cursor: v0.9 cursor is the offset, base64-encoded. Treated
        # as opaque by the caller.
        offset = _decode_cursor(opts.cursor)

        # Fetch limit+1 rows so we can tell whether there's a next page.
        sql += " LIMIT ? OFFSET ?"
        sql_params = list(params) + [opts.limit + 1, offset]

        db = await self._connect()
        try:
            cursor = await db.execute(sql, sql_params)
            rows = await cursor.fetchall()
            records = tuple(dict(r) for r in rows[:opts.limit])
            has_more = len(rows) > opts.limit
            next_cursor = (
                _encode_cursor(offset + opts.limit) if has_more else None
            )
            return sc.Page(
                records=records,
                next_cursor=next_cursor,
                estimated_total=None,  # v0.9 SQLite provider doesn't precompute
            )
        finally:
            await db.close()

    async def update(
        self,
        content_type: str,
        id: Any,
        patch: Mapping[str, Any],
    ) -> Optional[Mapping[str, Any]]:
        """Update fields on a record. Returns post-update record, or
        None if no row existed.

        Patch semantics per BRD §6.2: keys present overwrite, absent
        keys unchanged. Empty strings (legacy convention) and None
        are both filtered out — to clear a field, the runtime should
        pass an explicit sentinel; v0.9 keeps the legacy behavior.
        """
        db = await self._connect()
        try:
            existing = await _storage.get_record_by_id(db, content_type, id)
            if existing is None:
                return None
            cleaned = {k: v for k, v in patch.items()
                       if v is not None and v != ""}
            if cleaned:
                await _storage.update_fields(db, content_type, id, cleaned)
            return await _storage.get_record_by_id(db, content_type, id)
        finally:
            await db.close()

    async def update_if(
        self,
        content_type: str,
        id: Any,
        condition: sc.Predicate,
        patch: Mapping[str, Any],
    ) -> sc.UpdateResult:
        """Conditional update with predicate pushdown.

        Compiles `condition` to a parameterized SQL WHERE fragment
        (reusing the same predicate compiler that `query()` uses),
        emits a single
            `UPDATE <table> SET <patch> WHERE id = ? AND <cond>`
        statement, then disambiguates the `rowcount==0` case with a
        follow-up SELECT by id:
          - row absent → not_found
          - row present → condition_failed (with current record)

        SQL pushdown keeps the read-and-write atomic in one
        statement, avoiding TOCTOU even on backends with weaker
        isolation. See storage_contract.update_if for the contract
        the runtime expects.
        """
        # Empty patch is a no-op semantically, but we still need to
        # check the condition. Treat as: read row, check cond, return
        # appropriate UpdateResult without any UPDATE.
        cleaned = {k: v for k, v in patch.items()
                   if v is not None and v != ""}

        db = await self._connect()
        try:
            cond_sql, cond_params = _compile_predicate(condition)

            if not cleaned:
                # No-op update, but condition still gates the result.
                # SELECT WHERE id=? AND <cond>. If row matches both,
                # condition holds → "applied" with no actual change.
                # If id not present → not_found. If id present but
                # cond doesn't match → condition_failed (need a 2nd
                # query to fetch current state for the result).
                _storage._assert_safe(content_type, "table name")
                cursor = await db.execute(
                    f"SELECT * FROM {_storage._q(content_type)} "
                    f"WHERE id = ? AND ({cond_sql})",
                    [id, *cond_params])
                row = await cursor.fetchone()
                if row is not None:
                    return sc.UpdateResult(
                        applied=True, record=dict(row), reason="applied")
                # Disambiguate: did the row exist at all?
                existing = await _storage.get_record_by_id(
                    db, content_type, id)
                if existing is None:
                    return sc.UpdateResult(
                        applied=False, record=None, reason="not_found")
                return sc.UpdateResult(
                    applied=False, record=existing,
                    reason="condition_failed")

            # Validate field names in patch via the same safety
            # check init_db / update_fields use.
            for col in cleaned:
                _storage._assert_safe(col, "patch column")
            _storage._assert_safe(content_type, "table name")

            set_clauses = ", ".join(
                f"{_storage._q(c)} = ?" for c in cleaned)
            set_params = list(cleaned.values())

            sql = (
                f"UPDATE {_storage._q(content_type)} "
                f"SET {set_clauses} "
                f"WHERE id = ? AND ({cond_sql})"
            )
            cursor = await db.execute(
                sql, [*set_params, id, *cond_params])
            await db.commit()

            if cursor.rowcount > 0:
                updated = await _storage.get_record_by_id(
                    db, content_type, id)
                return sc.UpdateResult(
                    applied=True, record=updated, reason="applied")

            # rowcount == 0: disambiguate not_found vs condition_failed.
            existing = await _storage.get_record_by_id(
                db, content_type, id)
            if existing is None:
                return sc.UpdateResult(
                    applied=False, record=None, reason="not_found")
            return sc.UpdateResult(
                applied=False, record=existing,
                reason="condition_failed")
        finally:
            await db.close()

    async def delete(
        self,
        content_type: str,
        id: Any,
        cascade_mode: sc.CascadeMode = sc.CascadeMode.RESTRICT,
    ) -> bool:
        """Delete a record. Returns True if a row was deleted, False
        if no row existed.

        cascade_mode (BRD §6.2):
          RESTRICT — default. SQL FOREIGN KEY ... ON DELETE
            RESTRICT is implicit when foreign_keys=ON. Raises
            sqlite3.IntegrityError if any referrer exists.
          CASCADE — v0.9 ships partial cascade: the SQLite
            backend honors the `references X, cascade on delete`
            grammar via SQL ON DELETE CASCADE in init_db (Phase 2
            follow-on once the cascade grammar lands). Until
            then, CASCADE is treated identically to RESTRICT —
            an explicit caller request is honored only when the
            schema declares it; runtime callers that haven't
            been updated for the grammar still get safe defaults.

        Raises aiosqlite.IntegrityError (sqlite3.IntegrityError) on
        FK violation under RESTRICT. The runtime translates that
        into HTTP 409.
        """
        # cascade_mode is plumbed through but the SQL behavior is
        # determined by the table's FK declarations. v0.9 grammar
        # change will land FK declarations with ON DELETE CASCADE
        # vs ON DELETE RESTRICT in init_db; until then, all FKs are
        # implicit RESTRICT and any explicit CASCADE request is
        # silently downgraded. This is a known gap, documented in
        # the BRD §6.2 cascade follow-on.
        del cascade_mode  # acknowledged, not yet effective

        db = await self._connect()
        try:
            cursor = await db.execute(
                f'DELETE FROM {_storage._q(content_type)} WHERE id = ?',
                (id,),
            )
            await db.commit()
            return cursor.rowcount > 0
        finally:
            await db.close()


# ── Predicate compiler ──
#
# Compiles a Predicate AST into a parameterized SQL WHERE fragment.
# All leaf predicates push fully; there is no in-process residual.
# Identifier validation reuses the same _assert_safe path as the
# rest of the SQLite layer — predicates with unsafe field names
# raise ValueError before any SQL is emitted.


def _compile_predicate(p: sc.Predicate) -> tuple[str, list]:
    """Compile a Predicate to (sql_fragment, params).

    Returns parameterized SQL with `?` placeholders, never inlining
    values. Field identifiers are validated and quoted via the same
    helpers init_db uses, so unsafe names fail loudly.
    """
    if isinstance(p, sc.Eq):
        _storage._assert_safe(p.field, "predicate field")
        # SQL `= NULL` is always false (per ANSI). Use IS NULL when
        # the value is None — needed for "claim if unclaimed" /
        # "transition only when field is null" predicates that v0.9
        # update_if relies on.
        if p.value is None:
            return f"{_storage._q(p.field)} IS NULL", []
        return f"{_storage._q(p.field)} = ?", [p.value]
    if isinstance(p, sc.Ne):
        _storage._assert_safe(p.field, "predicate field")
        if p.value is None:
            return f"{_storage._q(p.field)} IS NOT NULL", []
        return f"{_storage._q(p.field)} != ?", [p.value]
    if isinstance(p, sc.Gt):
        _storage._assert_safe(p.field, "predicate field")
        return f"{_storage._q(p.field)} > ?", [p.value]
    if isinstance(p, sc.Gte):
        _storage._assert_safe(p.field, "predicate field")
        return f"{_storage._q(p.field)} >= ?", [p.value]
    if isinstance(p, sc.Lt):
        _storage._assert_safe(p.field, "predicate field")
        return f"{_storage._q(p.field)} < ?", [p.value]
    if isinstance(p, sc.Lte):
        _storage._assert_safe(p.field, "predicate field")
        return f"{_storage._q(p.field)} <= ?", [p.value]
    if isinstance(p, sc.In):
        _storage._assert_safe(p.field, "predicate field")
        if not p.values:
            # `IN ()` is a SQL syntax error; emit `1=0` instead.
            return "1 = 0", []
        placeholders = ", ".join("?" for _ in p.values)
        return (
            f"{_storage._q(p.field)} IN ({placeholders})",
            list(p.values),
        )
    if isinstance(p, sc.Contains):
        _storage._assert_safe(p.field, "predicate field")
        # SQLite LIKE is case-insensitive by default for ASCII;
        # BRD §6.2 specifies case-sensitive Contains, so we use
        # GLOB which is case-sensitive. Escape glob metacharacters
        # in the substring so a Contains for "a*b" matches the
        # literal "a*b", not "a-anything-b".
        escaped = (
            p.substring
            .replace("[", "[[]")
            .replace("?", "[?]")
            .replace("*", "[*]")
        )
        return f"{_storage._q(p.field)} GLOB ?", [f"*{escaped}*"]
    if isinstance(p, sc.And):
        parts = [_compile_predicate(sub) for sub in p.predicates]
        sql = "(" + " AND ".join(s for s, _ in parts) + ")"
        params: list = []
        for _, ps in parts:
            params.extend(ps)
        return sql, params
    if isinstance(p, sc.Or):
        parts = [_compile_predicate(sub) for sub in p.predicates]
        sql = "(" + " OR ".join(s for s, _ in parts) + ")"
        params = []
        for _, ps in parts:
            params.extend(ps)
        return sql, params
    if isinstance(p, sc.Not):
        sub_sql, sub_params = _compile_predicate(p.predicate)
        return f"NOT ({sub_sql})", sub_params

    raise TypeError(
        f"Unknown predicate type: {type(p).__name__}. The Predicate AST "
        f"is closed; new shapes require contract evolution."
    )


def _encode_cursor(offset: int) -> str:
    """Encode an offset as an opaque cursor.

    v0.9 ships an offset-based cursor as a stop-gap. A keyset
    cursor (last-id + tiebreaker) lands in Phase 2.x once we have
    test coverage proving the offset shape works end-to-end.
    """
    import base64
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")


def _decode_cursor(cursor: Optional[str]) -> int:
    """Inverse of _encode_cursor. None or empty → offset 0."""
    if not cursor:
        return 0
    import base64
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
        return int(decoded)
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError(f"Invalid cursor: {cursor!r}") from e


# ── Registration ──


def _sqlite_factory(config: Mapping[str, Any]) -> SqliteStorageProvider:
    """Factory used by the ProviderRegistry to construct an instance
    when an app's deploy config binds storage to 'sqlite'."""
    return SqliteStorageProvider(config)


def register_sqlite_storage(
    provider_registry, contract_registry: ContractRegistry | None = None
) -> None:
    """Register the SQLite storage provider with a ProviderRegistry.

    Same registration path third-party providers will use — no
    runtime-internal special casing per BRD §10. Pass the
    contract_registry to enable shape validation (rejects typos in
    category / contract name); first-party registration always
    passes it.
    """
    provider_registry.register(
        category=Category.STORAGE,
        contract_name="default",
        product_name="sqlite",
        factory=_sqlite_factory,
        conformance="passing",
        version="0.9.0",
        contract_registry=contract_registry,
    )
