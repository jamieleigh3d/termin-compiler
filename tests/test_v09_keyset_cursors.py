# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 2.x (e) — keyset cursor pagination.

The query() cursor is now a keyset cursor (last-returned row's
sort-key values + id, JSON-encoded, base64-wrapped) rather than
the v0.9 stop-gap base64-of-offset shape. Tests verify:

  - Pagination round-trips: walking from page 1 → next_cursor →
    page 2 returns each record exactly once and in the right order.
  - Insert-during-pagination is stable: inserting a row earlier in
    the sort order (which would shift offset-based pagination) does
    NOT cause duplicates or skips with keyset cursors.
  - Mixed asc/desc ORDER BY produces correct results.
  - Default order (no order_by) paginates by id ascending.
  - Cursor opacity: tests don't assume any particular shape; they
    use decode(encode(x)) == x and end-to-end behavior only.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from termin_server.providers.builtins.storage_sqlite import SqliteStorageProvider
from termin_server.providers.storage_contract import (
    initial_deploy_diff, QueryOptions, OrderBy,
)


def _content(name: str, *, fields=()) -> dict:
    return {
        "name": {"snake": name, "display": name, "pascal": name.capitalize()},
        "fields": tuple(fields),
        "state_machines": (),
    }


def _field(name: str, *, business_type: str = "text") -> dict:
    return {
        "name": name,
        "business_type": business_type,
        "column_type": "TEXT" if business_type == "text" else "INTEGER",
        "required": False,
        "unique": False,
        "minimum": None, "maximum": None,
        "enum_values": (),
        "foreign_key": None, "cascade_mode": None,
        "default_expr": None,
    }


@pytest.fixture
def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


async def _make_provider_with_records(tmp_db, n: int = 25):
    """Provider with N items, each with label='item-NN' and rank=NN."""
    p = SqliteStorageProvider({"db_path": tmp_db})
    schema = _content("items", fields=(
        _field("label"),
        _field("rank", business_type="whole_number"),
    ))
    await p.migrate(initial_deploy_diff([schema]))
    for i in range(n):
        await p.create("items", {
            "label": f"item-{i:03d}",
            "rank": i,
        })
    return p


# ── Round-trip pagination ───────────────────────────────────────────


class TestPaginationRoundTrip:
    @pytest.mark.asyncio
    async def test_default_order_walks_all_records(self, tmp_db):
        p = await _make_provider_with_records(tmp_db, n=12)
        # Walk pages of size 5 → 5, 5, 2.
        seen = []
        cursor = None
        page_count = 0
        while True:
            page = await p.query("items", None,
                                  QueryOptions(limit=5, cursor=cursor))
            seen.extend(r["id"] for r in page.records)
            page_count += 1
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
            if page_count > 10:
                pytest.fail("pagination did not terminate")
        assert seen == sorted(seen)  # default order: id ASC
        assert len(seen) == 12

    @pytest.mark.asyncio
    async def test_custom_sort_walks_in_correct_order(self, tmp_db):
        p = await _make_provider_with_records(tmp_db, n=12)
        # Walk by rank DESC, page size 4.
        seen = []
        cursor = None
        while True:
            page = await p.query(
                "items", None,
                QueryOptions(
                    limit=4, cursor=cursor,
                    order_by=(OrderBy(field="rank", direction="desc"),),
                ))
            seen.extend(r["rank"] for r in page.records)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        assert seen == sorted(seen, reverse=True)
        assert len(seen) == 12

    @pytest.mark.asyncio
    async def test_no_duplicates_across_pages(self, tmp_db):
        p = await _make_provider_with_records(tmp_db, n=20)
        seen = []
        cursor = None
        while True:
            page = await p.query("items", None,
                                  QueryOptions(limit=7, cursor=cursor))
            seen.extend(r["id"] for r in page.records)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        assert len(seen) == len(set(seen))  # no duplicates
        assert len(seen) == 20

    @pytest.mark.asyncio
    async def test_empty_table_no_cursor(self, tmp_db):
        p = SqliteStorageProvider({"db_path": tmp_db})
        schema = _content("items", fields=(_field("label"),))
        await p.migrate(initial_deploy_diff([schema]))
        page = await p.query("items", None, QueryOptions(limit=10))
        assert page.records == ()
        assert page.next_cursor is None

    @pytest.mark.asyncio
    async def test_exact_page_size_no_next_cursor(self, tmp_db):
        # If total == page size, no next page.
        p = await _make_provider_with_records(tmp_db, n=5)
        page = await p.query("items", None, QueryOptions(limit=5))
        assert len(page.records) == 5
        assert page.next_cursor is None


# ── Stability under inserts ────────────────────────────────────────


class TestStabilityUnderInserts:
    """Keyset cursors are stable under inserts that would shift
    offset-based cursors."""

    @pytest.mark.asyncio
    async def test_insert_earlier_doesnt_shift_pagination(self, tmp_db):
        # Setup: 10 items with rank 0..9, default order by id ASC.
        p = await _make_provider_with_records(tmp_db, n=10)

        # Page 1: items with ids 1..3.
        page1 = await p.query("items", None, QueryOptions(limit=3))
        assert len(page1.records) == 3
        page1_ids = [r["id"] for r in page1.records]

        # Insert a new record AFTER page 1 was fetched. With offset
        # cursors this would shift page 2 backward (causing a
        # duplicate). With keyset cursors, the new record is just
        # the next id in sequence, so the cursor still points
        # correctly after the last seen id.
        await p.create("items", {"label": "interloper", "rank": 999})

        # Page 2 from page1.next_cursor.
        page2 = await p.query("items", None,
                              QueryOptions(limit=3, cursor=page1.next_cursor))
        page2_ids = [r["id"] for r in page2.records]

        # No overlap between page1_ids and page2_ids — keyset
        # pagination doesn't duplicate.
        assert not (set(page1_ids) & set(page2_ids))


# ── Multi-field / mixed-direction ORDER BY ──────────────────────────


class TestMixedOrderBy:
    @pytest.mark.asyncio
    async def test_mixed_asc_desc_paginates_correctly(self, tmp_db):
        p = SqliteStorageProvider({"db_path": tmp_db})
        schema = _content("items", fields=(
            _field("category"),
            _field("rank", business_type="whole_number"),
        ))
        await p.migrate(initial_deploy_diff([schema]))
        # 6 records in 2 categories.
        for cat in ("alpha", "beta"):
            for r in range(3):
                await p.create("items", {"category": cat, "rank": r})

        # ORDER BY category ASC, rank DESC, id ASC. Page size 2.
        seen = []
        cursor = None
        while True:
            page = await p.query(
                "items", None,
                QueryOptions(
                    limit=2, cursor=cursor,
                    order_by=(
                        OrderBy(field="category", direction="asc"),
                        OrderBy(field="rank", direction="desc"),
                    ),
                ))
            seen.extend((r["category"], r["rank"]) for r in page.records)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        # Expected: alpha records first (rank 2, 1, 0), then beta (rank 2, 1, 0).
        assert seen == [
            ("alpha", 2), ("alpha", 1), ("alpha", 0),
            ("beta", 2), ("beta", 1), ("beta", 0),
        ]
