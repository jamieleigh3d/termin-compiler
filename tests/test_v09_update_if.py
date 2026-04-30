# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 2.x (d) — conditional update (CAS) for the storage
contract.

Tests cover:
  - applied case: condition matches → update lands, returns
    post-update record
  - not_found: id doesn't match → returns reason="not_found"
  - condition_failed: id matches but predicate doesn't → returns
    reason="condition_failed" with the CURRENT (pre-update) record
  - empty patch with matching condition → reason="applied"
    (no-op vacuously applied)
  - compound predicates (And, Or, Not) work
  - the canonical state-machine transition shape:
    Eq("status", "draft") → patch={"status": "in_review"}
  - optimistic-concurrency shape: Eq("version", N)
  - claim-only-if-unclaimed: Eq("assignee", None)
"""

from __future__ import annotations

import os
import tempfile

import pytest

from termin_server.providers.builtins.storage_sqlite import SqliteStorageProvider
from termin_server.providers.storage_contract import (
    initial_deploy_diff, Eq, Ne, Gt, And, Or, Not, UpdateResult,
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


async def _make_provider_with_record(tmp_db, label="A", status="draft", version=1):
    """Provider with one content type, one record."""
    p = SqliteStorageProvider({"db_path": tmp_db})
    schema = _content("docs", fields=(
        _field("label", required=True),
        _field("status", required=True),
        _field("version", business_type="whole_number", required=True),
        _field("assignee"),
    ))
    await p.migrate(initial_deploy_diff([schema]))
    rec = await p.create("docs", {
        "label": label, "status": status,
        "version": version,
    })
    return p, rec["id"]


# ── Result shape ────────────────────────────────────────────────────


class TestResultShape:
    def test_three_reasons_validated(self):
        with pytest.raises(ValueError, match="reason"):
            UpdateResult(applied=False, record=None, reason="bogus")

    def test_applied_carries_record(self):
        r = UpdateResult(applied=True, record={"id": 1}, reason="applied")
        assert r.applied is True
        assert r.record == {"id": 1}


# ── Applied path ────────────────────────────────────────────────────


class TestUpdateIfApplied:
    @pytest.mark.asyncio
    async def test_condition_matches_update_applied(self, tmp_db):
        p, rec_id = await _make_provider_with_record(tmp_db, status="draft")
        result = await p.update_if(
            "docs", rec_id,
            condition=Eq(field="status", value="draft"),
            patch={"status": "in_review"},
        )
        assert result.applied is True
        assert result.reason == "applied"
        assert result.record["status"] == "in_review"

    @pytest.mark.asyncio
    async def test_compound_and_predicate(self, tmp_db):
        p, rec_id = await _make_provider_with_record(
            tmp_db, status="draft", version=5)
        result = await p.update_if(
            "docs", rec_id,
            condition=And(predicates=(
                Eq(field="status", value="draft"),
                Eq(field="version", value=5),
            )),
            patch={"status": "in_review", "version": 6},
        )
        assert result.applied is True
        assert result.record["status"] == "in_review"
        assert result.record["version"] == 6

    @pytest.mark.asyncio
    async def test_optimistic_concurrency_via_version(self, tmp_db):
        p, rec_id = await _make_provider_with_record(tmp_db, version=1)
        # First updater: sees version=1, increments.
        r1 = await p.update_if(
            "docs", rec_id,
            condition=Eq(field="version", value=1),
            patch={"version": 2, "label": "first-update"},
        )
        assert r1.applied is True
        # Second updater: still thinks version=1; gets condition_failed.
        r2 = await p.update_if(
            "docs", rec_id,
            condition=Eq(field="version", value=1),
            patch={"version": 2, "label": "second-update"},
        )
        assert r2.applied is False
        assert r2.reason == "condition_failed"
        assert r2.record["version"] == 2  # current state
        assert r2.record["label"] == "first-update"


# ── Not-found path ──────────────────────────────────────────────────


class TestUpdateIfNotFound:
    @pytest.mark.asyncio
    async def test_unknown_id_returns_not_found(self, tmp_db):
        p, _ = await _make_provider_with_record(tmp_db)
        result = await p.update_if(
            "docs", 99999,
            condition=Eq(field="status", value="draft"),
            patch={"status": "in_review"},
        )
        assert result.applied is False
        assert result.reason == "not_found"
        assert result.record is None


# ── Condition-failed path ───────────────────────────────────────────


class TestUpdateIfConditionFailed:
    @pytest.mark.asyncio
    async def test_condition_doesnt_match_returns_current_record(self, tmp_db):
        p, rec_id = await _make_provider_with_record(
            tmp_db, status="approved")
        # Try to transition from "draft" but record is in "approved"
        result = await p.update_if(
            "docs", rec_id,
            condition=Eq(field="status", value="draft"),
            patch={"status": "in_review"},
        )
        assert result.applied is False
        assert result.reason == "condition_failed"
        # Caller gets the CURRENT record so it can show "actually
        # already approved" in the UI.
        assert result.record["status"] == "approved"

    @pytest.mark.asyncio
    async def test_record_not_modified_when_condition_failed(self, tmp_db):
        p, rec_id = await _make_provider_with_record(
            tmp_db, status="approved")
        await p.update_if(
            "docs", rec_id,
            condition=Eq(field="status", value="draft"),
            patch={"status": "in_review"},
        )
        # Verify storage state unchanged.
        current = await p.read("docs", rec_id)
        assert current["status"] == "approved"


# ── Empty patch (no-op with condition gate) ─────────────────────────


class TestEmptyPatch:
    @pytest.mark.asyncio
    async def test_empty_patch_matching_condition_returns_applied(self, tmp_db):
        p, rec_id = await _make_provider_with_record(tmp_db, status="draft")
        result = await p.update_if(
            "docs", rec_id,
            condition=Eq(field="status", value="draft"),
            patch={},
        )
        assert result.applied is True
        assert result.reason == "applied"

    @pytest.mark.asyncio
    async def test_empty_patch_failing_condition_returns_condition_failed(
            self, tmp_db):
        p, rec_id = await _make_provider_with_record(tmp_db, status="draft")
        result = await p.update_if(
            "docs", rec_id,
            condition=Eq(field="status", value="approved"),
            patch={},
        )
        assert result.applied is False
        assert result.reason == "condition_failed"
        assert result.record["status"] == "draft"


# ── State machine transition shape ──────────────────────────────────


class TestStateMachineTransition:
    """The canonical motivating use case."""

    @pytest.mark.asyncio
    async def test_draft_to_in_review_succeeds(self, tmp_db):
        p, rec_id = await _make_provider_with_record(tmp_db, status="draft")
        result = await p.update_if(
            "docs", rec_id,
            condition=Eq(field="status", value="draft"),
            patch={"status": "in_review"},
        )
        assert result.applied is True
        assert result.record["status"] == "in_review"

    @pytest.mark.asyncio
    async def test_double_transition_blocked(self, tmp_db):
        p, rec_id = await _make_provider_with_record(tmp_db, status="draft")
        # First user: draft → in_review.
        r1 = await p.update_if(
            "docs", rec_id,
            condition=Eq(field="status", value="draft"),
            patch={"status": "in_review"},
        )
        assert r1.applied is True
        # Second user races: also tries draft → in_review. Already in
        # in_review, so condition fails.
        r2 = await p.update_if(
            "docs", rec_id,
            condition=Eq(field="status", value="draft"),
            patch={"status": "in_review"},
        )
        assert r2.applied is False
        assert r2.reason == "condition_failed"


# ── Claim-only-if-unclaimed (Eq with None) ──────────────────────────


class TestClaimUnclaimed:
    @pytest.mark.asyncio
    async def test_eq_none_matches_null_field(self, tmp_db):
        p, rec_id = await _make_provider_with_record(tmp_db)
        # First worker claims the unclaimed record.
        r1 = await p.update_if(
            "docs", rec_id,
            condition=Eq(field="assignee", value=None),
            patch={"assignee": "alice"},
        )
        assert r1.applied is True
        assert r1.record["assignee"] == "alice"
        # Second worker also tries; fails because already claimed.
        r2 = await p.update_if(
            "docs", rec_id,
            condition=Eq(field="assignee", value=None),
            patch={"assignee": "bob"},
        )
        assert r2.applied is False
        assert r2.reason == "condition_failed"
        assert r2.record["assignee"] == "alice"


# ── Compound predicate variants ─────────────────────────────────────


class TestCompoundPredicates:
    @pytest.mark.asyncio
    async def test_or_predicate_matches_either(self, tmp_db):
        p, rec_id = await _make_provider_with_record(tmp_db, status="draft")
        result = await p.update_if(
            "docs", rec_id,
            condition=Or(predicates=(
                Eq(field="status", value="draft"),
                Eq(field="status", value="in_review"),
            )),
            patch={"status": "approved"},
        )
        assert result.applied is True

    @pytest.mark.asyncio
    async def test_not_predicate(self, tmp_db):
        p, rec_id = await _make_provider_with_record(tmp_db, status="draft")
        result = await p.update_if(
            "docs", rec_id,
            condition=Not(predicate=Eq(field="status", value="approved")),
            patch={"status": "in_review"},
        )
        assert result.applied is True

    @pytest.mark.asyncio
    async def test_gt_predicate(self, tmp_db):
        p, rec_id = await _make_provider_with_record(tmp_db, version=5)
        # Update only if version > 0.
        result = await p.update_if(
            "docs", rec_id,
            condition=Gt(field="version", value=0),
            patch={"label": "updated"},
        )
        assert result.applied is True
