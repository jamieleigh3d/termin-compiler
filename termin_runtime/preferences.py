# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Per-principal preference store (v0.9 Phase 5a.3).

Per BRD #2 §6.2 and presentation-provider-design.md §3.3: a runtime-
managed key-value table holds principal-scoped preferences (e.g.,
`theme`). Storage is **not** visible to applications via the Storage
primitive's normal surface — the table sits alongside other runtime-
private tables like `_termin_idempotency` and `_termin_schema`.

The module exposes sync helpers using the stdlib `sqlite3` module
because every caller (request handler, FastAPI dependency, lifespan
startup) is fast and small enough to not need async overhead. Direct
`sqlite3` connections share the file with the async aiosqlite path
used elsewhere in the runtime — SQLite supports concurrent readers
and a single writer at the OS level.

Theme operations are **not audit-logged** per BRD §6.2 — high-frequency
low-stakes UI preference, auditing every change adds noise without
value. If a future BRD revives audit, route through the audit bus at
that point.

The `value` column stores text. Theme values are validated against
the BRD §6.2 enum (`light | dark | auto | high-contrast`) on write;
other future preference keys (font size, density) will declare their
own validators when they land.
"""

from __future__ import annotations

import sqlite3
from typing import Mapping, Optional


PREFERENCES_TABLE: str = "_termin_principal_preferences"

# BRD #2 §6.2 — the closed set of theme values. Ordering is not
# significant.
VALID_THEMES: tuple[str, ...] = (
    "light",
    "dark",
    "auto",
    "high-contrast",
)

# Default theme when no `theme_default` is configured at the
# boundary level (BRD §6.2: "auto follows the operating-system or
# browser preference"). The runtime returns this when a principal
# has not stored a theme and the deploy config is silent.
RUNTIME_FALLBACK_THEME: str = "auto"

THEME_KEY: str = "theme"


class InvalidThemeValueError(ValueError):
    """Raised by set_theme_preference when the value is not in
    VALID_THEMES. Surfaces as 422 at the HTTP boundary."""


def ensure_preferences_table(conn: sqlite3.Connection) -> None:
    """Lazy-create `_termin_principal_preferences`.

    Schema:
      principal_id  TEXT NOT NULL
      key           TEXT NOT NULL
      value         TEXT NOT NULL
      updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      PRIMARY KEY (principal_id, key)

    Idempotent — `CREATE TABLE IF NOT EXISTS` is the standard pattern
    used by the other runtime-private tables (`_termin_schema`,
    `_termin_idempotency`).
    """
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {PREFERENCES_TABLE} ("
        f"  principal_id TEXT NOT NULL,"
        f"  key TEXT NOT NULL,"
        f"  value TEXT NOT NULL,"
        f"  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        f"  PRIMARY KEY (principal_id, key)"
        f")"
    )


def set_theme_preference(
    conn: sqlite3.Connection,
    principal_id: str,
    value: str,
) -> None:
    """Store a theme preference. Caller is responsible for `commit`.

    Per BRD §6.2: writes always succeed (including under
    `theme_locked`) so the user's stored preference is preserved
    against future lock removal. The lock check happens at READ time
    (`get_theme_preference`), not at write time.
    """
    if value not in VALID_THEMES:
        raise InvalidThemeValueError(
            f"Theme value must be one of {VALID_THEMES!r}; got {value!r}"
        )
    ensure_preferences_table(conn)
    conn.execute(
        f"INSERT INTO {PREFERENCES_TABLE} "
        f"(principal_id, key, value) VALUES (?, ?, ?) "
        f"ON CONFLICT(principal_id, key) DO UPDATE SET "
        f"  value = excluded.value, updated_at = CURRENT_TIMESTAMP",
        (principal_id, THEME_KEY, value),
    )


def get_theme_preference(
    conn: sqlite3.Connection,
    principal_id: str,
    theme_default: Optional[str] = None,
    theme_locked: Optional[str] = None,
) -> str:
    """Return the **effective** theme value for a principal.

    Resolution order (BRD §6.2):
      1. If `theme_locked` is set at the boundary, return it
         regardless of stored preference.
      2. Otherwise, if the principal has a stored value, return it.
      3. Otherwise, return `theme_default`.
      4. If `theme_default` is None, fall back to RUNTIME_FALLBACK_THEME.
    """
    if theme_locked is not None:
        return theme_locked
    ensure_preferences_table(conn)
    cursor = conn.execute(
        f"SELECT value FROM {PREFERENCES_TABLE} "
        f"WHERE principal_id = ? AND key = ?",
        (principal_id, THEME_KEY),
    )
    row = cursor.fetchone()
    if row is not None:
        return row[0]
    if theme_default is not None:
        return theme_default
    return RUNTIME_FALLBACK_THEME


def get_all_preferences(
    conn: sqlite3.Connection,
    principal_id: str,
) -> Mapping[str, str]:
    """Return the full `(key → value)` map for one principal.

    Used by identity hydration to populate `Principal.preferences`
    on every request without a per-key round-trip.
    """
    ensure_preferences_table(conn)
    cursor = conn.execute(
        f"SELECT key, value FROM {PREFERENCES_TABLE} "
        f"WHERE principal_id = ?",
        (principal_id,),
    )
    return {key: value for (key, value) in cursor.fetchall()}
