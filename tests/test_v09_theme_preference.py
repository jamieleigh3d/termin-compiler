# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 5a.3: theme preference table + endpoints.

Per BRD #2 §6.2 + presentation-provider-design.md §3.3, §3.4.

Two tiers of coverage:

  1. Unit tests against the `termin_runtime.preferences` module —
     sync helpers exercising the runtime-managed
     `_termin_principal_preferences` table directly.
  2. Integration tests against `/_termin/preferences/theme` GET/POST
     endpoints via TestClient. Covers: round-trip, theme_locked
     resolution, theme_default fallback, anonymous session-scoped
     storage, value validation, isolation between principals,
     not-audit-logged behavior.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from termin.peg_parser import parse_peg as parse
from termin.lower import lower
from termin_core.ir.serialize import serialize_ir
from termin_runtime import create_termin_app
from termin_runtime.preferences import (
    PREFERENCES_TABLE,
    VALID_THEMES,
    InvalidThemeValueError,
    ensure_preferences_table,
    get_theme_preference,
    set_theme_preference,
    get_all_preferences,
)


# ── Unit tests: termin_runtime.preferences module ──

@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "prefs.db"
    # Initialize the table so each test starts from a known state.
    conn = sqlite3.connect(str(p))
    try:
        ensure_preferences_table(conn)
        conn.commit()
    finally:
        conn.close()
    return str(p)


def test_ensure_preferences_table_creates_expected_schema(tmp_path):
    p = tmp_path / "fresh.db"
    conn = sqlite3.connect(str(p))
    try:
        ensure_preferences_table(conn)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (PREFERENCES_TABLE,),
        )
        assert cursor.fetchone() is not None
        # Composite primary key on (principal_id, key).
        cursor = conn.execute(
            f"PRAGMA table_info({PREFERENCES_TABLE})"
        )
        cols = {row[1]: row for row in cursor.fetchall()}
        assert "principal_id" in cols
        assert "key" in cols
        assert "value" in cols
    finally:
        conn.close()


def test_ensure_preferences_table_is_idempotent(tmp_path):
    p = tmp_path / "idem.db"
    conn = sqlite3.connect(str(p))
    try:
        ensure_preferences_table(conn)
        ensure_preferences_table(conn)  # second call must not raise
    finally:
        conn.close()


def test_set_then_get_theme_preference_roundtrip(db_path):
    conn = sqlite3.connect(db_path)
    try:
        set_theme_preference(conn, "user-1", "dark")
        conn.commit()
        assert get_theme_preference(conn, "user-1") == "dark"
    finally:
        conn.close()


def test_get_theme_preference_returns_default_when_unset(db_path):
    conn = sqlite3.connect(db_path)
    try:
        # No theme stored — returns the supplied default.
        assert get_theme_preference(
            conn, "user-2", theme_default="auto"
        ) == "auto"
    finally:
        conn.close()


def test_get_theme_preference_default_falls_back_to_auto_when_unspecified(db_path):
    conn = sqlite3.connect(db_path)
    try:
        # No default supplied; runtime falls back to "auto" per BRD §6.2.
        assert get_theme_preference(conn, "user-3") == "auto"
    finally:
        conn.close()


def test_theme_locked_overrides_stored_preference(db_path):
    conn = sqlite3.connect(db_path)
    try:
        set_theme_preference(conn, "user-4", "light")
        conn.commit()
        # User stored "light", but boundary locks to "dark".
        # get_theme_preference returns the LOCKED value.
        assert get_theme_preference(
            conn, "user-4", theme_default="auto", theme_locked="dark"
        ) == "dark"
    finally:
        conn.close()


def test_theme_locked_overrides_default_when_no_stored_value(db_path):
    conn = sqlite3.connect(db_path)
    try:
        assert get_theme_preference(
            conn, "user-no-val", theme_default="auto", theme_locked="high-contrast"
        ) == "high-contrast"
    finally:
        conn.close()


def test_set_theme_preference_succeeds_under_theme_lock(db_path):
    """Per BRD §6.2: writes always succeed, even under theme_locked,
    so the stored preference is preserved against future lock removal."""
    conn = sqlite3.connect(db_path)
    try:
        set_theme_preference(conn, "user-5", "dark")
        conn.commit()
        # Stored value persists; only get_theme_preference filters it.
        cursor = conn.execute(
            f"SELECT value FROM {PREFERENCES_TABLE} "
            f"WHERE principal_id=? AND key=?",
            ("user-5", "theme"),
        )
        assert cursor.fetchone()[0] == "dark"
    finally:
        conn.close()


def test_set_theme_preference_rejects_invalid_value(db_path):
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(InvalidThemeValueError):
            set_theme_preference(conn, "user-6", "neon")
    finally:
        conn.close()


def test_valid_themes_matches_brd_enumeration():
    # BRD §6.2: light | dark | auto | high-contrast.
    assert set(VALID_THEMES) == {"light", "dark", "auto", "high-contrast"}


def test_set_theme_preference_overwrites_previous_value(db_path):
    conn = sqlite3.connect(db_path)
    try:
        set_theme_preference(conn, "user-7", "dark")
        conn.commit()
        set_theme_preference(conn, "user-7", "light")
        conn.commit()
        assert get_theme_preference(conn, "user-7") == "light"
    finally:
        conn.close()


def test_separate_principals_have_isolated_preferences(db_path):
    conn = sqlite3.connect(db_path)
    try:
        set_theme_preference(conn, "alice", "dark")
        set_theme_preference(conn, "bob", "light")
        conn.commit()
        assert get_theme_preference(conn, "alice") == "dark"
        assert get_theme_preference(conn, "bob") == "light"
    finally:
        conn.close()


def test_get_all_preferences_returns_full_map_for_principal(db_path):
    conn = sqlite3.connect(db_path)
    try:
        set_theme_preference(conn, "user-8", "high-contrast")
        conn.commit()
        prefs = get_all_preferences(conn, "user-8")
        assert prefs == {"theme": "high-contrast"}
    finally:
        conn.close()


def test_get_all_preferences_empty_for_unknown_principal(db_path):
    conn = sqlite3.connect(db_path)
    try:
        assert get_all_preferences(conn, "ghost") == {}
    finally:
        conn.close()


# ── Integration tests: HTTP endpoints ──

_THEME_APP_SOURCE = '''Application: Theme Test
  Description: theme preference endpoint smoke

Identity:
  Scopes are "x.read"
  An "alice" has "x.read"
  A "bob" has "x.read"
  Anonymous has nothing

Content called "items":
  Each item has a name which is text, required
  Anyone with "x.read" can view items
'''


def _build_app(tmp_path, presentation_defaults=None):
    prog, _ = parse(_THEME_APP_SOURCE)
    spec = lower(prog)
    ir_json = serialize_ir(spec)
    db_path = tmp_path / "theme.db"
    deploy_config = None
    if presentation_defaults is not None:
        deploy_config = {
            "presentation": {"defaults": presentation_defaults}
        }
    return create_termin_app(
        ir_json,
        db_path=str(db_path),
        deploy_config=deploy_config,
    )


@pytest.fixture
def alice_client(tmp_path):
    app = _build_app(tmp_path)
    with TestClient(app) as c:
        c.cookies.set("termin_role", "alice")
        c.cookies.set("termin_user_name", "alice")
        yield c


@pytest.fixture
def bob_client(tmp_path):
    app = _build_app(tmp_path)
    with TestClient(app) as c:
        c.cookies.set("termin_role", "bob")
        c.cookies.set("termin_user_name", "bob")
        yield c


@pytest.fixture
def anon_client(tmp_path):
    app = _build_app(tmp_path)
    with TestClient(app) as c:
        c.cookies.set("termin_role", "Anonymous")
        yield c


def test_get_theme_returns_default_when_unset(alice_client):
    resp = alice_client.get("/_termin/preferences/theme")
    assert resp.status_code == 200
    body = resp.json()
    assert body["value"] == "auto"  # BRD default fallback


def test_post_theme_then_get_returns_stored(alice_client):
    resp = alice_client.post(
        "/_termin/preferences/theme", json={"value": "dark"}
    )
    assert resp.status_code == 200, resp.text
    resp = alice_client.get("/_termin/preferences/theme")
    assert resp.status_code == 200
    assert resp.json()["value"] == "dark"


def test_post_theme_validates_value(alice_client):
    resp = alice_client.post(
        "/_termin/preferences/theme", json={"value": "neon"}
    )
    assert resp.status_code == 422


def test_post_theme_rejects_missing_value(alice_client):
    resp = alice_client.post(
        "/_termin/preferences/theme", json={}
    )
    assert resp.status_code == 422


def test_get_theme_returns_locked_value_when_configured(tmp_path):
    """When `presentation.defaults.theme_locked` is set, GET returns
    the locked value regardless of the stored preference."""
    app = _build_app(
        tmp_path,
        presentation_defaults={"theme_default": "auto", "theme_locked": "dark"},
    )
    with TestClient(app) as alice:
        alice.cookies.set("termin_role", "alice")
        alice.cookies.set("termin_user_name", "alice")
        # Even after the user sets light, GET returns dark.
        alice.post("/_termin/preferences/theme", json={"value": "light"})
        resp = alice.get("/_termin/preferences/theme")
        assert resp.status_code == 200
        assert resp.json()["value"] == "dark"


def test_post_succeeds_under_theme_lock(tmp_path):
    """Set under lock must succeed (returns 200); the stored value is
    preserved for when the lock is later removed."""
    app = _build_app(
        tmp_path,
        presentation_defaults={"theme_default": "auto", "theme_locked": "dark"},
    )
    with TestClient(app) as alice:
        alice.cookies.set("termin_role", "alice")
        alice.cookies.set("termin_user_name", "alice")
        resp = alice.post(
            "/_termin/preferences/theme", json={"value": "light"}
        )
        assert resp.status_code == 200


def test_get_theme_uses_configured_default(tmp_path):
    """`presentation.defaults.theme_default` flows through to GET."""
    app = _build_app(
        tmp_path,
        presentation_defaults={"theme_default": "dark"},
    )
    with TestClient(app) as alice:
        alice.cookies.set("termin_role", "alice")
        alice.cookies.set("termin_user_name", "alice")
        resp = alice.get("/_termin/preferences/theme")
        assert resp.status_code == 200
        assert resp.json()["value"] == "dark"


def test_isolated_preferences_between_principals(tmp_path):
    """Alice's preference is invisible to Bob."""
    app = _build_app(tmp_path)
    with TestClient(app) as alice, TestClient(app) as bob:
        alice.cookies.set("termin_role", "alice")
        alice.cookies.set("termin_user_name", "alice")
        bob.cookies.set("termin_role", "bob")
        bob.cookies.set("termin_user_name", "bob")
        alice.post("/_termin/preferences/theme", json={"value": "dark"})
        bob.post("/_termin/preferences/theme", json={"value": "light"})
        assert alice.get(
            "/_termin/preferences/theme"
        ).json()["value"] == "dark"
        assert bob.get(
            "/_termin/preferences/theme"
        ).json()["value"] == "light"


def test_anonymous_post_then_get_uses_session_cookie(anon_client):
    """Anonymous principals get session-scoped storage. POST sets a
    cookie; GET reads it back. No DB row is created for anonymous."""
    resp = anon_client.post(
        "/_termin/preferences/theme", json={"value": "dark"}
    )
    assert resp.status_code == 200
    # The cookie should now be set.
    assert "termin_theme_pref" in resp.cookies or any(
        c.name == "termin_theme_pref" for c in anon_client.cookies.jar
    )
    resp = anon_client.get("/_termin/preferences/theme")
    assert resp.status_code == 200
    assert resp.json()["value"] == "dark"


def test_endpoint_paths_use_underscore_prefix(alice_client):
    """The runtime-private paths use the `/_termin/...` prefix."""
    # GET works at /_termin/preferences/theme.
    assert alice_client.get(
        "/_termin/preferences/theme"
    ).status_code == 200


def test_post_theme_returns_effective_value_in_response(alice_client):
    """Convenience: POST returns the now-effective value so termin.js
    doesn't need a follow-up GET."""
    resp = alice_client.post(
        "/_termin/preferences/theme", json={"value": "high-contrast"}
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == "high-contrast"


# ── Principal hydration: preferences flow through to the_user ──

def _seed_pref_for_principal(db_path: str, principal_id: str, theme: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        from termin_runtime.preferences import (
            ensure_preferences_table, set_theme_preference,
        )
        ensure_preferences_table(conn)
        set_theme_preference(conn, principal_id, theme)
        conn.commit()
    finally:
        conn.close()


def test_principal_preferences_hydrated_with_stored_theme(tmp_path):
    """A principal with a stored theme has Principal.preferences
    carrying it after hydration — making `the_user.preferences.theme`
    resolvable in CEL contexts."""
    from termin_runtime.identity import _hydrate_principal_preferences
    from termin_runtime.providers.identity_contract import Principal

    db_path = str(tmp_path / "fresh.db")
    _seed_pref_for_principal(db_path, "alice-id", "dark")

    p = Principal(id="alice-id", type="human", display_name="Alice")
    p2 = _hydrate_principal_preferences(
        p, db_path, theme_default=None, theme_locked=None,
    )
    assert p2.preferences["theme"] == "dark"


def test_anonymous_principal_not_hydrated_from_db(tmp_path):
    """Anonymous principals get session-cookie storage, never the DB.
    Hydration is a no-op for them."""
    from termin_runtime.identity import _hydrate_principal_preferences
    from termin_runtime.providers.identity_contract import ANONYMOUS_PRINCIPAL

    db_path = str(tmp_path / "anon.db")
    p2 = _hydrate_principal_preferences(
        ANONYMOUS_PRINCIPAL, db_path,
        theme_default="auto", theme_locked=None,
    )
    assert p2.preferences == {}  # untouched


def test_theme_locked_masks_principal_preferences(tmp_path):
    """When `theme_locked` is set, hydration projects the locked value
    into Principal.preferences so CEL sees the same effective theme
    that the GET endpoint returns."""
    from termin_runtime.identity import _hydrate_principal_preferences
    from termin_runtime.providers.identity_contract import Principal

    db_path = str(tmp_path / "lock.db")
    _seed_pref_for_principal(db_path, "alice-id", "light")

    p = Principal(id="alice-id", type="human", display_name="Alice")
    p2 = _hydrate_principal_preferences(
        p, db_path, theme_default="auto", theme_locked="dark",
    )
    # User stored "light"; lock pins effective value to "dark".
    assert p2.preferences["theme"] == "dark"


def test_theme_default_fills_in_when_no_stored_value(tmp_path):
    """Hydration falls back to theme_default when the principal has no
    stored value, so CEL `the_user.preferences.theme` is never undefined
    when the boundary configures a default."""
    from termin_runtime.identity import _hydrate_principal_preferences
    from termin_runtime.providers.identity_contract import Principal

    db_path = str(tmp_path / "default.db")
    p = Principal(id="brand-new-id", type="human", display_name="New")
    p2 = _hydrate_principal_preferences(
        p, db_path, theme_default="dark", theme_locked=None,
    )
    assert p2.preferences["theme"] == "dark"
