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

from typing import Any, Mapping, Optional

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
        """Apply a schema migration.

        v0.9 Phase 2 implements initial-deploy migration (all changes
        are 'added'). Modify and remove changes are recognized but
        not yet executed — they raise NotImplementedError so callers
        get a clear signal rather than silent data loss. Phase 2.x
        completes the modify/remove paths after the runtime's diff
        classifier lands.
        """
        if diff.is_blocked:
            raise ValueError(
                "SqliteStorageProvider.migrate() refuses a blocked diff. "
                "The runtime should classify and reject blocked diffs "
                "before invoking the provider."
            )

        added_schemas: list[Mapping[str, Any]] = []
        for change in diff.changes:
            if change.kind == "added":
                if change.schema is None:
                    raise ValueError(
                        f"MigrationDiff 'added' change for "
                        f"{change.content_name!r} is missing a schema"
                    )
                added_schemas.append(change.schema)
            elif change.kind in ("modified", "removed"):
                # Phase 2 ships the contract surface; the modify/remove
                # paths land with the runtime's diff classifier in a
                # follow-on. Raising NotImplementedError preserves data
                # integrity — the deploy fails fast rather than partially
                # migrating.
                raise NotImplementedError(
                    f"SqliteStorageProvider does not yet implement "
                    f"{change.kind!r} migrations (content "
                    f"{change.content_name!r}). v0.9 Phase 2 ships "
                    f"initial-deploy only; modify/remove land with "
                    f"the diff-classifier follow-on."
                )

        if added_schemas:
            # Delegate to the existing init_db helper so the SQL stays
            # in one place. init_db is idempotent (CREATE TABLE IF NOT
            # EXISTS), so running it on every startup is safe even when
            # the diff is empty.
            await _storage.init_db(added_schemas, self._db_path)

    # ── CRUD ──

    async def create(
        self,
        content_type: str,
        record: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> Mapping[str, Any]:
        """Insert a record. Pure SQL — no events, no error routing.

        v0.9 idempotency_key is accepted for forward-compat but not
        yet honored. The contract surface is stable; the dedup
        implementation lands in a Phase 2.x follow-on.
        """
        # Strip empty strings on optional fields (legacy convention
        # callers depend on). State-machine column defaults are the
        # caller's responsibility — the provider does not know about
        # state machines, that's a runtime concern.
        cleaned = {k: v for k, v in record.items() if v != ""}
        if not cleaned:
            return {"id": None}

        db = await self._connect()
        try:
            new_id = await _storage.insert_raw(db, content_type, cleaned)
            persisted = dict(cleaned)
            persisted["id"] = new_id
            return persisted
        finally:
            await db.close()

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
        return f"{_storage._q(p.field)} = ?", [p.value]
    if isinstance(p, sc.Ne):
        _storage._assert_safe(p.field, "predicate field")
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
