"""SQLite storage adapter for the Termin runtime.

Provides get_db(), init_db(), and generic CRUD helpers that work with
any Content schema defined in the IR.
"""

import re

import aiosqlite
from pathlib import Path


_db_path: str = "app.db"

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
    """Get an async SQLite connection."""
    path = db_path or _db_path
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
    global _db_path
    if db_path:
        _db_path = db_path

    db = await get_db()
    try:
        for cs in content_schemas:
            table_name = cs["name"]["snake"]
            _assert_safe(table_name, "table name")

            col_defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]

            # Status column if has state machine
            if cs.get("has_state_machine"):
                initial = cs.get("initial_state", "")
                col_defs.append(f'"status" TEXT NOT NULL DEFAULT \'{initial}\'')

            # Fields
            fk_defs = []
            for field in cs.get("fields", []):
                _assert_safe(field["name"], f"field name in {table_name}")
                col_defs.append(_field_to_sql(field))
                if field.get("foreign_key"):
                    _assert_safe(field["foreign_key"], f"foreign key target in {table_name}")
                    fk_defs.append(f'FOREIGN KEY ({_q(field["name"])}) REFERENCES {_q(field["foreign_key"])}(id)')

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
                        sm_info: dict = None, terminator=None, event_bus=None):
    """Insert a new record. Returns the created record dict with id."""
    d = dict(data)
    # Remove empty strings for optional fields
    d = {k: v for k, v in d.items() if v != "" or k == "status"}

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


async def list_records(db, content_name: str):
    """List all records from a content table."""
    cursor = await db.execute(f"SELECT * FROM {_q(content_name)}")
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
    """Delete a record. Returns True if a record was deleted, False if not found."""
    cursor = await db.execute(
        f'DELETE FROM {_q(content_name)} WHERE {_q(lookup_col)} = ?', (id_value,)
    )
    await db.commit()
    if cursor.rowcount == 0:
        from fastapi import HTTPException
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
