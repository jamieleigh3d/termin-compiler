"""SQLite storage adapter for the Termin runtime.

Provides get_db(), init_db(), and generic CRUD helpers that work with
any Content schema defined in the IR.
"""

import aiosqlite
from pathlib import Path


_db_path: str = "app.db"


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
    btype = field.get("business_type", "text")

    if field.get("is_auto"):
        return f"{name} TIMESTAMP DEFAULT CURRENT_TIMESTAMP"

    sql_type = _SQL_TYPES.get(btype, "TEXT")
    parts = [f"{name} {sql_type}"]

    if field.get("enum_values"):
        vals = ", ".join(f"'{v}'" for v in field["enum_values"])
        parts = [f"{name} TEXT CHECK({name} IN ({vals}))"]

    if field.get("required"):
        parts.append("NOT NULL")
    if field.get("unique"):
        parts.append("UNIQUE")

    min_v = field.get("minimum")
    max_v = field.get("maximum")
    if min_v is not None and max_v is not None:
        parts.append(f"CHECK({name} >= {min_v} AND {name} <= {max_v})")
    elif min_v is not None:
        parts.append(f"CHECK({name} >= {min_v})")
    elif max_v is not None:
        parts.append(f"CHECK({name} <= {max_v})")

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
            col_defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]

            # Status column if has state machine
            if cs.get("has_state_machine"):
                initial = cs.get("initial_state", "")
                col_defs.append(f"status TEXT NOT NULL DEFAULT '{initial}'")

            # Fields
            fk_defs = []
            for field in cs.get("fields", []):
                col_defs.append(_field_to_sql(field))
                if field.get("foreign_key"):
                    fk_defs.append(f"FOREIGN KEY ({field['name']}) REFERENCES {field['foreign_key']}(id)")

            all_defs = col_defs + fk_defs
            cols_sql = ",\n                ".join(all_defs)
            await db.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    {cols_sql}
                )
            """)
        await db.commit()
    finally:
        await db.close()


async def create_record(db, content_name: str, data: dict, schema: dict = None,
                        sm_info: dict = None, terminator=None):
    """Insert a new record. Returns the created record dict with id."""
    d = dict(data)
    # Remove empty strings for optional fields
    d = {k: v for k, v in d.items() if v != "" or k == "status"}

    columns = list(d.keys())
    if not columns:
        return {"id": None}

    placeholders = ", ".join(["?"] * len(columns))
    col_str = ", ".join(columns)
    values = [d[k] for k in columns]

    try:
        cursor = await db.execute(
            f'INSERT INTO {content_name} ({col_str}) VALUES ({placeholders})',
            tuple(values)
        )
        await db.commit()
        record_id = cursor.lastrowid
        record = dict(d)
        record["id"] = record_id
        return record
    except Exception as e:
        if terminator:
            from .errors import TerminError
            terminator.route(TerminError(source=content_name, kind="validation", message=str(e)))
        raise


async def list_records(db, content_name: str):
    """List all records from a content table."""
    cursor = await db.execute(f"SELECT * FROM {content_name}")
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_record(db, content_name: str, id_value, lookup_col: str = "id"):
    """Get a single record by lookup column."""
    cursor = await db.execute(
        f"SELECT * FROM {content_name} WHERE {lookup_col} = ?", (id_value,)
    )
    row = await cursor.fetchone()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not found")
    return dict(row)


async def update_record(db, content_name: str, id_value, data: dict,
                        lookup_col: str = "id", terminator=None):
    """Update a record. Returns the updated record."""
    d = {k: v for k, v in data.items() if v is not None and v != ""}
    if not d:
        return {"message": "No fields to update"}

    set_clause = ", ".join(f"{k} = ?" for k in d.keys())
    values = list(d.values()) + [id_value]

    try:
        await db.execute(
            f'UPDATE {content_name} SET {set_clause} WHERE {lookup_col} = ?',
            tuple(values)
        )
        await db.commit()
        return await get_record(db, content_name, id_value, lookup_col)
    except Exception as e:
        if terminator:
            from .errors import TerminError
            terminator.route(TerminError(source=content_name, kind="validation", message=str(e)))
        raise


async def delete_record(db, content_name: str, id_value,
                        lookup_col: str = "id", terminator=None):
    """Delete a record."""
    await db.execute(
        f'DELETE FROM {content_name} WHERE {lookup_col} = ?', (id_value,)
    )
    await db.commit()
