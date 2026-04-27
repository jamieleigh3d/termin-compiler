# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""SQLite storage adapter for the Termin runtime.

Provides get_db(), init_db(), and generic CRUD helpers that work with
any Content schema defined in the IR.
"""

import re

import aiosqlite
from pathlib import Path


# Default database path used when no explicit db_path is provided
# AND no TERMIN_DB_PATH env var is set. This is a constant —
# historically a mutable module global that init_db() rewrote
# per-app, causing cross-app contamination when multiple apps
# booted in the same process (one app's init_db rewrote the
# global; another app's get_db(None) read the rewritten value).
# v0.9 made it a constant.
#
# Resolution precedence in app.create_termin_app (highest first):
#   1. Explicit db_path argument.
#   2. TERMIN_DB_PATH environment variable (Phase 2.x g).
#   3. This constant — "app.db" relative to cwd.
#
# Production deployments should pass an explicit db_path or set
# TERMIN_DB_PATH; the cwd-relative fallback exists for the
# `python app.py` shape historically supported by v0.8.
DEFAULT_DB_PATH: str = "app.db"

# Safe identifier pattern: lowercase letters, digits, underscores. Must start with a letter.
_SAFE_IDENTIFIER = re.compile(r'^[a-z][a-z0-9_]*$')


def validate_identifier(name: str) -> bool:
    """Check if a string is safe to use as a SQL identifier.

    Only lowercase snake_case identifiers are allowed. This prevents SQL
    injection through malicious IR payloads — the .termin.pkg is a user-
    provided ZIP file and cannot be trusted implicitly.
    """
    return bool(name and _SAFE_IDENTIFIER.match(name))


def _assert_safe(name: str, context: str = "identifier") -> str:
    """Validate and return a SQL identifier. Raises ValueError if unsafe."""
    if not validate_identifier(name):
        raise ValueError(
            f"Unsafe SQL {context}: {name!r}. "
            f"Identifiers must match [a-z][a-z0-9_]* (lowercase snake_case)."
        )
    return name


def _q(name: str) -> str:
    """Quote a SQL identifier safely.

    Layer 2 defense: even if validation is bypassed, embedded double quotes
    are escaped by doubling (SQLite standard). This prevents quote breakout.
    """
    # Escape any embedded double quotes by doubling them
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


async def get_db(db_path: str = None) -> aiosqlite.Connection:
    """Get an async SQLite connection.

    Production callers pass ctx.db_path; the None fallback exists for
    tests and direct CLI invocations of init_db. There is no module-
    global state — falling through to None routes to DEFAULT_DB_PATH
    deterministically, no matter what other apps in the same process
    are doing.
    """
    path = db_path or DEFAULT_DB_PATH
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    return db


# ── SQL type mapping from IR business_type ──

_SQL_TYPES = {
    "text": "TEXT",
    "currency": "REAL",
    "number": "REAL",
    "percentage": "REAL",
    "whole_number": "INTEGER",
    "boolean": "INTEGER",
    "date": "TEXT",
    "datetime": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    "automatic": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    "reference": "INTEGER",
    "enum": "TEXT",
    "list": "TEXT",
}


def _field_to_sql(field: dict) -> str:
    """Convert an IR FieldSpec dict to a SQL column definition."""
    name = field["name"]
    qname = _q(name)
    btype = field.get("business_type", "text")

    if field.get("is_auto"):
        return f"{qname} TIMESTAMP DEFAULT CURRENT_TIMESTAMP"

    sql_type = _SQL_TYPES.get(btype, "TEXT")
    parts = [f"{qname} {sql_type}"]

    if field.get("enum_values"):
        vals = ", ".join(f"'{v}'" for v in field["enum_values"])
        parts = [f"{qname} TEXT CHECK({qname} IN ({vals}))"]

    if field.get("required"):
        parts.append("NOT NULL")
    if field.get("unique"):
        parts.append("UNIQUE")

    min_v = field.get("minimum")
    max_v = field.get("maximum")
    if min_v is not None and max_v is not None:
        parts.append(f"CHECK({qname} >= {min_v} AND {qname} <= {max_v})")
    elif min_v is not None:
        parts.append(f"CHECK({qname} >= {min_v})")
    elif max_v is not None:
        parts.append(f"CHECK({qname} <= {max_v})")

    return " ".join(parts)


async def init_db(content_schemas: list[dict], db_path: str = None):
    """Initialize the database from IR ContentSchema dicts.

    Each schema has: name.snake, fields[], has_state_machine, initial_state.
    """
    # Validate all identifiers up front, before any side effects (DB
    # open). Malicious IR must fail without leaving state.
    for cs in content_schemas:
        table_name = cs["name"]["snake"]
        _assert_safe(table_name, "table name")
        for field in cs.get("fields", []):
            _assert_safe(field["name"], f"field name in {table_name}")
            if field.get("foreign_key"):
                _assert_safe(field["foreign_key"], f"foreign key target in {table_name}")

    # No global mutation — db_path resolves to either the caller's
    # explicit path or DEFAULT_DB_PATH. Each app's RuntimeContext
    # carries its own db_path and runtime code passes ctx.db_path
    # explicitly to every get_db() call. The previous module-global
    # _db_path caused cross-app contamination when one app's init_db
    # rewrote the global and another app's get_db(None) read it.
    db = await get_db(db_path)
    try:
        for cs in content_schemas:
            table_name = cs["name"]["snake"]
            _assert_safe(table_name, "table name")

            col_defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]

            # State machine columns: one TEXT NOT NULL column per state
            # machine declared on this content. The column name is the
            # machine_name (already snake_case in the IR — same as the
            # underlying state-typed field's snake name). Each column's
            # SQL DEFAULT is the machine's initial state, so records
            # created without explicit values still land in the right
            # initial state for every machine on the content.
            for sm in cs.get("state_machines", []):
                col = sm["machine_name"]
                initial = sm.get("initial", "")
                _assert_safe(col, f"state machine column on {table_name}")
                col_defs.append(
                    f'"{col}" TEXT NOT NULL DEFAULT \'{initial}\'')

            # Fields. State-typed fields are skipped here because each
            # state machine on this content already emitted its column
            # in the state-machines block above. The lowering pass keeps
            # the field in `fields` for analyzer/renderer purposes (so
            # the edit modal can include it as input_type=state) but the
            # storage layer must not emit a duplicate column.
            sm_col_names = {sm["machine_name"]
                            for sm in cs.get("state_machines", [])}
            fk_defs = []
            for field in cs.get("fields", []):
                _assert_safe(field["name"], f"field name in {table_name}")
                if (field.get("business_type") == "state"
                        or field["name"] in sm_col_names):
                    continue
                col_defs.append(_field_to_sql(field))
                if field.get("foreign_key"):
                    _assert_safe(field["foreign_key"], f"foreign key target in {table_name}")
                    # v0.9: emit explicit ON DELETE clause from the
                    # schema-declared cascade_mode. The compiler
                    # enforces that every reference field has a
                    # cascade_mode (TERMIN-S039), so reaching this
                    # branch with a missing cascade_mode means the
                    # IR was hand-edited or produced by a non-
                    # conforming compiler — fail loudly.
                    cm = field.get("cascade_mode")
                    if cm not in ("cascade", "restrict"):
                        raise ValueError(
                            f"Field {field['name']!r} on {table_name!r}: "
                            f"cascade_mode must be 'cascade' or 'restrict' "
                            f"on a foreign-key column, got {cm!r}"
                        )
                    on_delete = cm.upper()
                    fk_defs.append(
                        f'FOREIGN KEY ({_q(field["name"])}) REFERENCES '
                        f'{_q(field["foreign_key"])}(id) ON DELETE {on_delete}'
                    )

            all_defs = col_defs + fk_defs
            cols_sql = ",\n                ".join(all_defs)
            await db.execute(f"""
                CREATE TABLE IF NOT EXISTS {_q(table_name)} (
                    {cols_sql}
                )
            """)
        await db.commit()
    finally:
        await db.close()


async def create_record(db, content_name: str, data: dict, schema: dict = None,
                        sm_info=None, terminator=None, event_bus=None):
    """Insert a new record. Returns the created record dict with id.

    `sm_info` accepts either:
      - the new v0.9 shape: a list of {"machine_name", "column", "initial",
        "transitions"} dicts (one per state machine on this content), OR
      - the legacy single-SM dict shape from earlier internal callers.

    State-machine columns are not stripped from `data` even when their value
    is the empty string, so an explicit empty-string write does not silently
    fall through to the SQL default. Empty strings on regular optional
    fields are still stripped (existing behavior)."""
    d = dict(data)
    # Identify all state-machine column names on this content and seed
    # any missing/empty ones with the machine's initial state. This
    # ensures the returned record dict carries the state column even
    # when the caller didn't supply it (the SQL DEFAULT also covers
    # the persisted row but the dict we hand back is built from `d`).
    state_cols: set[str] = set()
    if isinstance(sm_info, list):
        for sm in sm_info:
            if isinstance(sm, dict) and sm.get("machine_name"):
                col = sm["machine_name"]
                state_cols.add(col)
                if not d.get(col):
                    d[col] = sm.get("initial", "")
    elif isinstance(sm_info, dict):
        # Legacy: single SM dict, status column
        state_cols.add("status")
        if not d.get("status"):
            d["status"] = sm_info.get("initial", "")
    # Remove empty strings for optional fields, but preserve state columns.
    d = {k: v for k, v in d.items() if v != "" or k in state_cols}

    columns = list(d.keys())
    if not columns:
        return {"id": None}

    placeholders = ", ".join(["?"] * len(columns))
    col_str = ", ".join(_q(c) for c in columns)
    values = [d[k] for k in columns]

    try:
        cursor = await db.execute(
            f'INSERT INTO {_q(content_name)} ({col_str}) VALUES ({placeholders})',
            tuple(values)
        )
        await db.commit()
        record_id = cursor.lastrowid
        record = dict(d)
        record["id"] = record_id
        if event_bus:
            await event_bus.publish({
                "type": f"{content_name}_created",
                "channel_id": f"content.{content_name}.created",
                "content_name": content_name,
                "data": record,
            })
        return record
    except Exception as e:
        if terminator:
            from .errors import TerminError
            terminator.route(TerminError(source=content_name, kind="validation", message=str(e)))
        raise


async def list_records(db, content_name: str, *,
                       limit: int = None, offset: int = None,
                       filters: dict = None,
                       sort_by: str = None, sort_dir: str = None,
                       schema: dict = None):
    """List records from a content table.

    Optional keyword arguments support pagination, filtering, and sorting:

        limit, offset        — non-negative integers. If limit is None, all
                               records are returned (backward-compatible).
        filters              — dict of {field: value}. Every field is
                               validated against the provided schema before
                               being composed into the WHERE clause. Values
                               are always parameterized (never concatenated).
        sort_by, sort_dir    — field name (validated against schema) and
                               either "asc" or "desc".
        schema               — the ContentSchema for this content, used to
                               validate filter/sort field names. Required
                               if filters or sort_by are provided.

    Raises ValueError on any unsafe identifier (filter key or sort column
    not in schema; sort_dir outside {asc, desc}; limit/offset negative).
    """
    # Base query.
    sql = f"SELECT * FROM {_q(content_name)}"
    params = []

    # Validate & compose filters.
    if filters:
        if schema is None:
            raise ValueError("schema is required when filters are provided")
        schema_fields = {f["name"] for f in schema.get("fields", [])}
        schema_fields.update({"id", "status"})  # implicit columns
        where_clauses = []
        for field, value in filters.items():
            if field not in schema_fields:
                raise ValueError(
                    f"unknown filter field '{field}' for {content_name}")
            _assert_safe(field, "filter field")
            where_clauses.append(f"{_q(field)} = ?")
            params.append(value)
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)

    # Validate & compose sort.
    if sort_by:
        if schema is None:
            raise ValueError("schema is required when sort_by is provided")
        schema_fields = {f["name"] for f in schema.get("fields", [])}
        schema_fields.update({"id", "status"})
        if sort_by not in schema_fields:
            raise ValueError(
                f"unknown sort field '{sort_by}' for {content_name}")
        _assert_safe(sort_by, "sort field")
        direction = (sort_dir or "asc").lower()
        if direction not in ("asc", "desc"):
            raise ValueError(
                f"sort direction must be 'asc' or 'desc', got {sort_dir!r}")
        sql += f" ORDER BY {_q(sort_by)} {direction.upper()}"

    # Validate & apply pagination.
    if limit is not None:
        if not isinstance(limit, int) or limit < 0:
            raise ValueError(f"limit must be non-negative integer, got {limit!r}")
        sql += " LIMIT ?"
        params.append(limit)
        if offset is not None:
            if not isinstance(offset, int) or offset < 0:
                raise ValueError(
                    f"offset must be non-negative integer, got {offset!r}")
            sql += " OFFSET ?"
            params.append(offset)
    elif offset is not None:
        # LIMIT -1 in SQLite means no limit, required to use OFFSET without LIMIT.
        if not isinstance(offset, int) or offset < 0:
            raise ValueError(
                f"offset must be non-negative integer, got {offset!r}")
        sql += " LIMIT -1 OFFSET ?"
        params.append(offset)

    cursor = await db.execute(sql, params)
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_record(db, content_name: str, id_value, lookup_col: str = "id"):
    """Get a single record by lookup column."""
    cursor = await db.execute(
        f"SELECT * FROM {_q(content_name)} WHERE {_q(lookup_col)} = ?", (id_value,)
    )
    row = await cursor.fetchone()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not found")
    return dict(row)


async def update_record(db, content_name: str, id_value, data: dict,
                        lookup_col: str = "id", terminator=None, event_bus=None):
    """Update a record. Returns the updated record."""
    d = {k: v for k, v in data.items() if v is not None and v != ""}
    if not d:
        return {"message": "No fields to update"}

    set_clause = ", ".join(f"{_q(k)} = ?" for k in d.keys())
    values = list(d.values()) + [id_value]

    try:
        await db.execute(
            f'UPDATE {_q(content_name)} SET {set_clause} WHERE {_q(lookup_col)} = ?',
            tuple(values)
        )
        await db.commit()
        record = await get_record(db, content_name, id_value, lookup_col)
        if event_bus:
            await event_bus.publish({
                "type": f"{content_name}_updated",
                "channel_id": f"content.{content_name}.updated",
                "content_name": content_name,
                "data": record,
            })
        return record
    except Exception as e:
        if terminator:
            from .errors import TerminError
            terminator.route(TerminError(source=content_name, kind="validation", message=str(e)))
        raise


async def delete_record(db, content_name: str, id_value,
                        lookup_col: str = "id", terminator=None, event_bus=None):
    """Delete a record. Raises 404 if not found, 409 if a referential
    integrity constraint blocks the delete (SQL RESTRICT semantics — the
    default and safest behavior when other records reference this one).
    """
    import sqlite3
    from fastapi import HTTPException
    try:
        cursor = await db.execute(
            f'DELETE FROM {_q(content_name)} WHERE {_q(lookup_col)} = ?',
            (id_value,),
        )
        await db.commit()
    except sqlite3.IntegrityError as e:
        msg = str(e)
        if "FOREIGN KEY" in msg.upper():
            # Referential integrity — another content type references this row.
            # Surface a clean 409 rather than an opaque 500.
            detail = (
                f"Cannot delete this {content_name[:-1] if content_name.endswith('s') else content_name}: "
                f"other records reference it. Remove or reassign those first."
            )
            if terminator:
                from .errors import TerminError
                terminator.route(TerminError(
                    source=content_name, kind="validation", message=detail))
            raise HTTPException(status_code=409, detail=detail)
        raise
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Record not found")
    if event_bus:
        await event_bus.publish({
            "type": f"{content_name}_deleted",
            "channel_id": f"content.{content_name}.deleted",
            "content_name": content_name,
            "record_id": id_value,
        })


# ── Additional query functions (used by other runtime modules) ──

async def count_records(db, content_name: str) -> int:
    """Count all records in a content table."""
    cursor = await db.execute(f"SELECT COUNT(*) as cnt FROM {_q(content_name)}")
    row = await cursor.fetchone()
    return row["cnt"] if row else 0


async def filtered_query(db, content_name: str, filters: dict = None) -> list[dict]:
    """Query records with optional column-value filters.

    Args:
        filters: Dict of {column_name: value}. All conditions are AND'd.
    """
    if filters:
        where = " AND ".join(f"{_q(k)} = ?" for k in filters)
        cursor = await db.execute(
            f"SELECT * FROM {_q(content_name)} WHERE {where}",
            tuple(filters.values()))
    else:
        cursor = await db.execute(f"SELECT * FROM {_q(content_name)}")
    return [dict(r) for r in await cursor.fetchall()]


async def find_by_field(db, content_name: str, field: str, value) -> dict | None:
    """Find a single record by a specific field value. Returns None if not found."""
    cursor = await db.execute(
        f"SELECT * FROM {_q(content_name)} WHERE {_q(field)} = ?", (value,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def select_column(db, content_name: str, column: str) -> list:
    """Select a single column from all records. Returns list of values."""
    cursor = await db.execute(f"SELECT {_q(column)} FROM {_q(content_name)}")
    rows = await cursor.fetchall()
    return [dict(r).get(column) for r in rows]


async def update_fields(db, content_name: str, record_id, fields: dict) -> None:
    """Update specific fields on a record by id. No event publishing."""
    if not fields:
        return
    sets = ", ".join(f"{_q(k)} = ?" for k in fields)
    vals = list(fields.values()) + [record_id]
    await db.execute(f"UPDATE {_q(content_name)} SET {sets} WHERE id = ?", tuple(vals))
    await db.commit()


async def insert_raw(db, content_name: str, data: dict) -> int | None:
    """Insert a record without event publishing or validation. Returns lastrowid."""
    columns = list(data.keys())
    if not columns:
        return None
    col_str = ", ".join(_q(c) for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    vals = [data[k] for k in columns]
    cursor = await db.execute(
        f"INSERT INTO {_q(content_name)} ({col_str}) VALUES ({placeholders})",
        tuple(vals))
    await db.commit()
    return cursor.lastrowid


async def get_record_by_id(db, content_name: str, record_id) -> dict | None:
    """Get a record by id, returning None instead of raising 404."""
    cursor = await db.execute(
        f"SELECT * FROM {_q(content_name)} WHERE id = ?", (record_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None
