# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 2.x (c) — idempotency-key dedup for storage create().

Per BRD §6.2: a second create() call with the same idempotency_key is
a silent no-op that returns the originally-persisted record. This
test pack covers:

  - first call inserts and records the key
  - second call with same key returns the original record (no insert)
  - different keys create separate records
  - no key → no dedup (legacy path)
  - replay AFTER underlying record is deleted starts a fresh insert
    (stale entry cleaned up at lookup time)
  - keys are isolated per content type (same key, different type =
    different records)
"""

from __future__ import annotations

import os
import tempfile

import pytest

from termin_runtime.providers.builtins.storage_sqlite import SqliteStorageProvider
from termin_runtime.providers.storage_contract import (
    initial_deploy_diff, CascadeMode,
)


def _content(name: str, *, fields=()) -> dict:
    return {
        "name": {"snake": name, "display": name, "pascal": name.capitalize()},
        "fields": tuple(fields),
        "state_machines": (),
    }


def _field(name: str, *, business_type: str = "text", required: bool = False) -> dict:
    return {
        "name": name,
        "business_type": business_type,
        "column_type": "TEXT",
        "required": required,
        "unique": False,
        "minimum": None,
        "maximum": None,
        "enum_values": (),
        "foreign_key": None,
        "cascade_mode": None,
        "default_expr": None,
    }


@pytest.fixture
def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


async def _make_things_provider(tmp_db):
    """Async helper: provider with a single 'things' content type."""
    p = SqliteStorageProvider({"db_path": tmp_db})
    schema = _content("things", fields=(
        _field("label", required=True),
        _field("note"),
    ))
    await p.migrate(initial_deploy_diff([schema]))
    return p


async def _make_two_type_provider(tmp_db):
    """Async helper: provider with two unrelated content types."""
    p = SqliteStorageProvider({"db_path": tmp_db})
    schemas = [
        _content("alphas", fields=(_field("label", required=True),)),
        _content("betas", fields=(_field("name", required=True),)),
    ]
    await p.migrate(initial_deploy_diff(schemas))
    return p


# ── Core idempotency contract ───────────────────────────────────────


class TestIdempotencyContract:
    @pytest.mark.asyncio
    async def test_no_key_no_dedup(self, tmp_db):
        provider = await _make_things_provider(tmp_db)
        # Two creates with no key → two distinct records.
        r1 = await provider.create("things", {"label": "a"})
        r2 = await provider.create("things", {"label": "a"})
        assert r1["id"] != r2["id"]

    @pytest.mark.asyncio
    async def test_first_call_with_key_inserts(self, tmp_db):
        provider = await _make_things_provider(tmp_db)
        r = await provider.create(
            "things", {"label": "first"}, idempotency_key="key-1")
        assert r["id"] is not None
        assert r["label"] == "first"
        # Verify the record is actually in storage.
        fetched = await provider.read("things", r["id"])
        assert fetched is not None
        assert fetched["label"] == "first"

    @pytest.mark.asyncio
    async def test_replay_returns_original_record(self, tmp_db):
        provider = await _make_things_provider(tmp_db)
        # First call: insert.
        r1 = await provider.create(
            "things", {"label": "original"}, idempotency_key="key-1")
        # Second call: same key, different payload — still returns
        # the ORIGINAL record. The replay's payload is ignored.
        r2 = await provider.create(
            "things", {"label": "different"}, idempotency_key="key-1")
        assert r1["id"] == r2["id"]
        assert r2["label"] == "original"

    @pytest.mark.asyncio
    async def test_replay_does_not_create_duplicate_row(self, tmp_db):
        provider = await _make_things_provider(tmp_db)
        await provider.create(
            "things", {"label": "first"}, idempotency_key="key-1")
        await provider.create(
            "things", {"label": "first"}, idempotency_key="key-1")
        # Only one row in storage despite two calls.
        from termin_runtime.providers.storage_contract import QueryOptions
        page = await provider.query("things", None, QueryOptions(limit=10))
        assert len(page.records) == 1

    @pytest.mark.asyncio
    async def test_different_keys_create_separate_records(self, tmp_db):
        provider = await _make_things_provider(tmp_db)
        r1 = await provider.create(
            "things", {"label": "a"}, idempotency_key="key-1")
        r2 = await provider.create(
            "things", {"label": "b"}, idempotency_key="key-2")
        assert r1["id"] != r2["id"]
        assert r1["label"] == "a"
        assert r2["label"] == "b"


# ── Stale-entry cleanup ─────────────────────────────────────────────


class TestStaleEntryCleanup:
    @pytest.mark.asyncio
    async def test_replay_after_delete_creates_fresh(self, tmp_db):
        provider = await _make_things_provider(tmp_db)
        # First call: insert.
        r1 = await provider.create(
            "things", {"label": "first"}, idempotency_key="key-1")
        original_id = r1["id"]
        # Delete the record (cascade_mode is RESTRICT default but
        # 'things' has no FK referrers, so delete succeeds).
        deleted = await provider.delete(
            "things", original_id, cascade_mode=CascadeMode.RESTRICT)
        assert deleted is True
        # Replay with the same key: should produce a NEW record,
        # not return the deleted one.
        r2 = await provider.create(
            "things", {"label": "fresh"}, idempotency_key="key-1")
        assert r2["id"] != original_id
        assert r2["label"] == "fresh"
        # Verify the fresh record is queryable.
        fetched = await provider.read("things", r2["id"])
        assert fetched is not None
        assert fetched["label"] == "fresh"


# ── Per-content-type isolation ──────────────────────────────────────


class TestPerContentTypeIsolation:
    @pytest.mark.asyncio
    async def test_same_key_different_content_types_create_separate(
            self, tmp_db):
        p = await _make_two_type_provider(tmp_db)
        a = await p.create(
            "alphas", {"label": "a-1"}, idempotency_key="shared-key")
        b = await p.create(
            "betas", {"name": "b-1"}, idempotency_key="shared-key")
        # Both succeed independently; same key in different content
        # types is treated as two separate idempotency contexts.
        assert a["id"] is not None
        assert b["id"] is not None
        a_fetch = await p.read("alphas", a["id"])
        b_fetch = await p.read("betas", b["id"])
        assert a_fetch["label"] == "a-1"
        assert b_fetch["name"] == "b-1"

    @pytest.mark.asyncio
    async def test_replay_in_one_type_doesnt_affect_other(
            self, tmp_db):
        p = await _make_two_type_provider(tmp_db)
        a1 = await p.create(
            "alphas", {"label": "a-original"}, idempotency_key="k")
        # Replay in alphas → returns original.
        a2 = await p.create(
            "alphas", {"label": "a-replay"}, idempotency_key="k")
        assert a1["id"] == a2["id"]
        # First create in betas with the same key still fires.
        b1 = await p.create(
            "betas", {"name": "b-first"}, idempotency_key="k")
        assert b1["id"] is not None
        # Verify both content types have independent records.
        from termin_runtime.providers.storage_contract import QueryOptions
        ap = await p.query("alphas", None, QueryOptions(limit=10))
        bp = await p.query("betas", None, QueryOptions(limit=10))
        assert len(ap.records) == 1
        assert len(bp.records) == 1


# ── Internal table & schema ─────────────────────────────────────────


class TestIdempotencyTable:
    @pytest.mark.asyncio
    async def test_idempotency_table_lazy_created(self, tmp_db):
        """The _termin_idempotency table is lazy-created on first
        create() with a key — not on initial_deploy."""
        provider = await _make_things_provider(tmp_db)
        # After provider.migrate, no key has been used,
        # so the idempotency table shouldn't exist yet.
        import aiosqlite
        db = await aiosqlite.connect(tmp_db)
        try:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name = '_termin_idempotency'"
            )
            row = await cursor.fetchone()
            assert row is None, (
                "_termin_idempotency table existed before any "
                "idempotency-key create — should be lazy-created")
        finally:
            await db.close()
        # First create with a key creates the table.
        await provider.create(
            "things", {"label": "x"}, idempotency_key="k")
        db = await aiosqlite.connect(tmp_db)
        try:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name = '_termin_idempotency'"
            )
            row = await cursor.fetchone()
            assert row is not None
        finally:
            await db.close()
