# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 2.x (b) — migration diff classifier tests.

Pure-function tests covering:
  - Field-level classification rules (§3.3)
  - Content-level classification (aggregation worst-up)
  - Diff computation from (current, target) schema pairs
  - Rename mapping folding (§3.13)
  - Empty-table downgrade (§3.9)
  - Fingerprinting + ack coverage (§3.4)

Async tests cover the empty-table downgrade pass (touches a fake
provider) and the SqliteStorageProvider migration paths
end-to-end (modify/remove/rename + table-rebuild dance).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from termin_core.providers.storage_contract import (
    FieldChange, ContentChange, MigrationDiff, CLASSIFICATIONS,
    worst_classification, BackupFailedError, MigrationValidationError,
)
from termin_server.providers.builtins.storage_sqlite import SqliteStorageProvider
from termin_core.migrations import (
    compute_migration_diff, classify_field_change, classify_content_change,
    apply_rename_mappings, downgrade_for_empty_tables,
    fingerprint_change, ack_covers,
)
from termin_core.migrations.ack import collect_required_fingerprints
from termin_core.migrations.errors import RenameMappingError


# ── Helpers ─────────────────────────────────────────────────────────


def _content(name: str, *, fields=()) -> dict:
    return {
        "name": {"snake": name, "display": name, "pascal": name.capitalize()},
        "fields": tuple(fields),
        "state_machines": (),
    }


def _field(name: str, *, business_type: str = "text", required: bool = False,
           unique: bool = False, foreign_key: str = None,
           cascade_mode: str = None, minimum: int = None, maximum: int = None,
           enum_values: tuple = (), default_expr: str = None) -> dict:
    return {
        "name": name,
        "business_type": business_type,
        "column_type": "TEXT",
        "required": required,
        "unique": unique,
        "minimum": minimum,
        "maximum": maximum,
        "enum_values": enum_values,
        "foreign_key": foreign_key,
        "cascade_mode": cascade_mode,
        "default_expr": default_expr,
    }


# ── Field-level classification ──────────────────────────────────────


class TestFieldClassification:
    def test_add_optional_field_safe(self):
        fc = FieldChange(kind="added", field_name="x",
                         detail={"spec": _field("x")})
        assert classify_field_change(fc) == "safe"

    def test_add_required_field_with_default_safe(self):
        spec = _field("x", required=True, default_expr='"foo"')
        fc = FieldChange(kind="added", field_name="x",
                         detail={"spec": spec})
        assert classify_field_change(fc) == "safe"

    def test_add_required_field_no_default_medium(self):
        spec = _field("x", required=True)
        fc = FieldChange(kind="added", field_name="x",
                         detail={"spec": spec})
        assert classify_field_change(fc) == "medium"

    def test_add_field_with_foreign_key_blocked(self):
        spec = _field("x", business_type="reference", foreign_key="parents",
                      cascade_mode="restrict")
        fc = FieldChange(kind="added", field_name="x",
                         detail={"spec": spec})
        assert classify_field_change(fc) == "blocked"

    def test_add_unique_field_medium(self):
        spec = _field("x", unique=True)
        fc = FieldChange(kind="added", field_name="x",
                         detail={"spec": spec})
        assert classify_field_change(fc) == "medium"

    def test_remove_field_blocked_default(self):
        # Defaults to blocked (data loss); empty-table downgrade
        # may relax this in a separate pass.
        fc = FieldChange(kind="removed", field_name="x")
        assert classify_field_change(fc) == "blocked"

    def test_type_changed_lossless_widening_medium(self):
        fc = FieldChange(kind="type_changed", field_name="x",
                         detail={"from_type": "whole_number",
                                 "to_type": "number"})
        assert classify_field_change(fc) == "medium"

    def test_type_changed_lossy_blocked(self):
        fc = FieldChange(kind="type_changed", field_name="x",
                         detail={"from_type": "text",
                                 "to_type": "whole_number"})
        assert classify_field_change(fc) == "blocked"

    def test_required_added_high(self):
        fc = FieldChange(kind="required_added", field_name="x")
        assert classify_field_change(fc) == "high"

    def test_required_removed_medium(self):
        fc = FieldChange(kind="required_removed", field_name="x")
        assert classify_field_change(fc) == "medium"

    def test_unique_added_high(self):
        fc = FieldChange(kind="unique_added", field_name="x")
        assert classify_field_change(fc) == "high"

    def test_unique_removed_medium(self):
        fc = FieldChange(kind="unique_removed", field_name="x")
        assert classify_field_change(fc) == "medium"

    def test_bounds_tightening_high(self):
        fc = FieldChange(kind="bounds_changed", field_name="x",
                         detail={"from": {"min": 0, "max": 100},
                                 "to": {"min": 10, "max": 100},
                                 "tightening": True})
        assert classify_field_change(fc) == "high"

    def test_bounds_loosening_medium(self):
        fc = FieldChange(kind="bounds_changed", field_name="x",
                         detail={"from": {"min": 10, "max": 100},
                                 "to": {"min": 0, "max": 100},
                                 "tightening": False})
        assert classify_field_change(fc) == "medium"

    def test_enum_values_added_medium(self):
        fc = FieldChange(kind="enum_values_changed", field_name="x",
                         detail={"added": ["new"], "removed": []})
        assert classify_field_change(fc) == "medium"

    def test_enum_values_removed_high(self):
        fc = FieldChange(kind="enum_values_changed", field_name="x",
                         detail={"added": [], "removed": ["old"]})
        assert classify_field_change(fc) == "high"

    def test_cascade_mode_change_high(self):
        # The v0.8 → v0.9 case: NULL → "cascade" or "restrict".
        fc = FieldChange(kind="cascade_mode_changed", field_name="x",
                         detail={"from": None, "to": "cascade"})
        assert classify_field_change(fc) == "high"

    def test_cascade_mode_change_between_v09_modes_high(self):
        fc = FieldChange(kind="cascade_mode_changed", field_name="x",
                         detail={"from": "restrict", "to": "cascade"})
        assert classify_field_change(fc) == "high"

    def test_foreign_key_added_to_existing_field_blocked(self):
        fc = FieldChange(kind="foreign_key_changed", field_name="x",
                         detail={"from": None, "to": "parents"})
        assert classify_field_change(fc) == "blocked"

    def test_foreign_key_removed_medium(self):
        fc = FieldChange(kind="foreign_key_changed", field_name="x",
                         detail={"from": "parents", "to": None})
        assert classify_field_change(fc) == "medium"

    def test_foreign_key_target_changed_blocked(self):
        fc = FieldChange(kind="foreign_key_changed", field_name="x",
                         detail={"from": "old_parents", "to": "new_parents"})
        assert classify_field_change(fc) == "blocked"

    def test_renamed_same_type_low(self):
        fc = FieldChange(kind="renamed", field_name="b",
                         detail={"from": "a", "to": "b",
                                 "type_changed": False})
        assert classify_field_change(fc) == "low"

    def test_renamed_lossless_type_change_medium(self):
        fc = FieldChange(kind="renamed", field_name="b",
                         detail={"from": "a", "to": "b",
                                 "type_changed": True,
                                 "from_type": "whole_number",
                                 "to_type": "number"})
        assert classify_field_change(fc) == "medium"

    def test_renamed_lossy_type_change_high(self):
        fc = FieldChange(kind="renamed", field_name="b",
                         detail={"from": "a", "to": "b",
                                 "type_changed": True,
                                 "from_type": "text",
                                 "to_type": "whole_number"})
        assert classify_field_change(fc) == "high"


# ── Content-level classification (aggregation worst-up) ─────────────


class TestContentClassification:
    def test_add_content_safe(self):
        cc = ContentChange(kind="added", content_name="x",
                           classification="safe")
        assert classify_content_change(cc) == "safe"

    def test_remove_content_blocked(self):
        cc = ContentChange(kind="removed", content_name="x",
                           classification="blocked")
        assert classify_content_change(cc) == "blocked"

    def test_renamed_content_low(self):
        cc = ContentChange(kind="renamed", content_name="newname",
                           classification="low",
                           detail={"from": "oldname"})
        assert classify_content_change(cc) == "low"

    def test_modified_content_aggregates_field_classifications_worst(self):
        # One field change is medium, one is high → content should be high.
        new_schema = _content("x", fields=(
            _field("a", required=True),
            _field("b"),
        ))
        cc = ContentChange(
            kind="modified",
            content_name="x",
            classification="safe",  # placeholder
            schema=new_schema,
            field_changes=(
                FieldChange(kind="required_added", field_name="a"),  # high
                FieldChange(kind="enum_values_changed", field_name="b",
                            detail={"added": ["new"], "removed": []}),  # medium
            ),
        )
        assert classify_content_change(cc) == "high"


# ── Worst-classification helper ─────────────────────────────────────


class TestWorstClassification:
    def test_ordering(self):
        assert CLASSIFICATIONS == ("safe", "low", "medium", "high", "blocked")

    def test_blocked_dominates(self):
        assert worst_classification("safe", "blocked", "medium") == "blocked"

    def test_high_beats_medium(self):
        assert worst_classification("medium", "high") == "high"

    def test_safe_when_empty(self):
        assert worst_classification() == "safe"


# ── Diff computation ────────────────────────────────────────────────


class TestComputeMigrationDiff:
    def test_empty_to_empty_is_empty_diff(self):
        diff = compute_migration_diff([], [])
        assert diff.changes == ()

    def test_none_current_treated_as_empty(self):
        target = [_content("x", fields=(_field("a"),))]
        diff = compute_migration_diff(None, target)
        assert len(diff.changes) == 1
        assert diff.changes[0].kind == "added"
        assert diff.changes[0].classification == "safe"

    def test_added_content_safe(self):
        diff = compute_migration_diff([], [_content("x")])
        assert diff.changes[0].kind == "added"
        assert diff.changes[0].classification == "safe"

    def test_removed_content_blocked(self):
        diff = compute_migration_diff([_content("x")], [])
        assert diff.changes[0].kind == "removed"
        assert diff.changes[0].classification == "blocked"

    def test_field_added_to_existing_content(self):
        old = [_content("x", fields=(_field("a"),))]
        new = [_content("x", fields=(_field("a"), _field("b")))]
        diff = compute_migration_diff(old, new)
        assert len(diff.changes) == 1
        cc = diff.changes[0]
        assert cc.kind == "modified"
        assert cc.classification == "safe"  # optional field add
        assert len(cc.field_changes) == 1
        assert cc.field_changes[0].kind == "added"
        assert cc.field_changes[0].field_name == "b"

    def test_cascade_mode_v08_to_v09_change_high(self):
        # The headline migration: v0.8 IR has no cascade_mode,
        # v0.9 IR adds it. Differ sees cascade_mode_changed (None → cascade).
        old_field = _field("parent", business_type="reference",
                            foreign_key="parents", cascade_mode=None)
        new_field = _field("parent", business_type="reference",
                            foreign_key="parents", cascade_mode="cascade")
        old = [_content("x", fields=(old_field,))]
        new = [_content("x", fields=(new_field,))]
        diff = compute_migration_diff(old, new)
        assert len(diff.changes) == 1
        cc = diff.changes[0]
        assert cc.kind == "modified"
        assert cc.classification == "high"
        assert any(fc.kind == "cascade_mode_changed"
                   for fc in cc.field_changes)


# ── Rename mapping ──────────────────────────────────────────────────


class TestRenameMappings:
    def test_field_rename_same_type_low(self):
        old = [_content("x", fields=(_field("old_name", business_type="text"),))]
        new = [_content("x", fields=(_field("new_name", business_type="text"),))]
        diff = compute_migration_diff(old, new)
        # Without rename mapping: removed + added → blocked overall.
        assert diff.overall_classification == "blocked"

        diff = apply_rename_mappings(
            diff,
            rename_fields=[{"content": "x", "from": "old_name",
                            "to": "new_name"}],
        )
        cc = diff.changes[0]
        assert cc.kind == "modified"
        assert any(fc.kind == "renamed" for fc in cc.field_changes)
        assert cc.classification == "low"

    def test_content_rename_low(self):
        old = [_content("old_name", fields=(_field("a"),))]
        new = [_content("new_name", fields=(_field("a"),))]
        diff = compute_migration_diff(old, new)
        assert diff.overall_classification == "blocked"
        diff = apply_rename_mappings(
            diff,
            rename_contents=[{"from": "old_name", "to": "new_name"}],
        )
        assert len(diff.changes) == 1
        assert diff.changes[0].kind == "renamed"
        assert diff.changes[0].classification == "low"
        assert diff.changes[0].content_name == "new_name"

    def test_rename_cycle_rejected(self):
        diff = MigrationDiff(changes=())
        with pytest.raises(RenameMappingError, match="cycle"):
            apply_rename_mappings(
                diff,
                rename_contents=[
                    {"from": "a", "to": "b"},
                    {"from": "b", "to": "a"},
                ],
            )

    def test_rename_duplicate_target_rejected(self):
        diff = MigrationDiff(changes=())
        with pytest.raises(RenameMappingError, match="duplicate"):
            apply_rename_mappings(
                diff,
                rename_contents=[
                    {"from": "a", "to": "z"},
                    {"from": "b", "to": "z"},
                ],
            )

    def test_rename_mapping_doesnt_match_diff_rejected(self):
        # operator declares a rename but the diff doesn't have a
        # matching remove+add pair.
        old = [_content("x", fields=(_field("a"),))]
        new = [_content("x", fields=(_field("a"),))]
        diff = compute_migration_diff(old, new)
        with pytest.raises(RenameMappingError, match="no modified ContentChange"):
            apply_rename_mappings(
                diff,
                rename_fields=[{"content": "x", "from": "old",
                                "to": "new"}],
            )


# ── Empty-table downgrade ───────────────────────────────────────────


class _FakeProviderEmpty:
    """Minimal stand-in for downgrade_for_empty_tables — query()
    returns an empty page. Treats every table as empty."""
    async def query(self, content_type, predicate, options):
        from termin_core.providers.storage_contract import Page
        return Page(records=(), next_cursor=None, estimated_total=0)


class _FakeProviderNonEmpty:
    """Returns one record so the downgrade pass treats tables as non-empty."""
    async def query(self, content_type, predicate, options):
        from termin_core.providers.storage_contract import Page
        return Page(records=({"id": 1},), next_cursor=None, estimated_total=1)


class TestEmptyTableDowngrade:
    @pytest.mark.asyncio
    async def test_remove_field_empty_table_downgrades_to_low(self):
        old = [_content("x", fields=(_field("a"), _field("b")))]
        new = [_content("x", fields=(_field("a"),))]
        diff = compute_migration_diff(old, new)
        assert diff.overall_classification == "blocked"
        diff = await downgrade_for_empty_tables(diff, _FakeProviderEmpty())
        assert diff.overall_classification == "low"

    @pytest.mark.asyncio
    async def test_remove_field_non_empty_stays_blocked(self):
        old = [_content("x", fields=(_field("a"), _field("b")))]
        new = [_content("x", fields=(_field("a"),))]
        diff = compute_migration_diff(old, new)
        diff = await downgrade_for_empty_tables(diff, _FakeProviderNonEmpty())
        assert diff.overall_classification == "blocked"

    @pytest.mark.asyncio
    async def test_remove_content_empty_downgrades_to_low(self):
        old = [_content("x")]
        new = []
        diff = compute_migration_diff(old, new)
        diff = await downgrade_for_empty_tables(diff, _FakeProviderEmpty())
        assert diff.overall_classification == "low"


# ── Fingerprinting + ack ────────────────────────────────────────────


class TestFingerprinting:
    def test_fingerprint_stable_across_runs(self):
        fc = FieldChange(kind="required_added", field_name="x")
        a = fingerprint_change(fc, content_name="things")
        b = fingerprint_change(fc, content_name="things")
        assert a == b

    def test_fingerprint_changes_when_change_changes(self):
        fc1 = FieldChange(kind="required_added", field_name="x")
        fc2 = FieldChange(kind="required_added", field_name="y")
        assert (fingerprint_change(fc1, content_name="t")
                != fingerprint_change(fc2, content_name="t"))

    def test_fingerprint_format(self):
        fc = FieldChange(kind="cascade_mode_changed", field_name="parent",
                         detail={"from": None, "to": "cascade"})
        fp = fingerprint_change(fc, content_name="comments")
        assert fp.startswith("comments.parent:cascade_mode_changed:")
        suffix = fp.split(":")[-1]
        assert len(suffix) == 5  # short-hash

    def test_content_change_fingerprint(self):
        cc = ContentChange(kind="removed", content_name="orphans",
                           classification="blocked")
        fp = fingerprint_change(cc)
        assert fp.startswith("orphans:removed:")


class TestAckCovers:
    """ack_covers honors per-change fingerprints in any environment,
    and the dev-mode blanket-low flag only when both dev_mode AND
    accept_any_low are set. Medium/high tiers always require per-change
    ack regardless of dev_mode."""

    def _low_diff(self):
        """A diff with a single low-tier change (a renamed field, same type)."""
        return MigrationDiff(changes=(
            ContentChange(kind="modified", content_name="tickets",
                          classification="low",
                          field_changes=(
                              FieldChange(kind="renamed",
                                          field_name="severity",
                                          detail={"from": "priority",
                                                  "to": "severity"}),
                          )),
        ))

    def _high_diff(self):
        """A diff with a single high-tier change."""
        return MigrationDiff(changes=(
            ContentChange(kind="modified", content_name="x",
                          classification="high",
                          field_changes=(
                              FieldChange(kind="required_added",
                                          field_name="y"),
                          )),
        ))

    def test_blanket_low_covers_low_in_dev_mode(self):
        diff = self._low_diff()
        assert ack_covers(diff, {"dev_mode": True, "accept_any_low": True})

    def test_blanket_low_does_not_cover_high(self):
        """The blanket-low flag is low-tier-only, even in dev_mode."""
        diff = self._high_diff()
        assert not ack_covers(diff, {"dev_mode": True, "accept_any_low": True})

    def test_blanket_low_inert_without_dev_mode(self):
        """accept_any_low alone (without dev_mode) is ignored —
        production-strict default."""
        diff = self._low_diff()
        assert not ack_covers(diff, {"accept_any_low": True})

    def test_dev_mode_inert_without_accept_any_low(self):
        """dev_mode alone (without accept_any_low) does not unlock anything."""
        diff = self._low_diff()
        assert not ack_covers(diff, {"dev_mode": True})

    def test_per_change_ack_covers_when_complete(self):
        cc = ContentChange(kind="modified", content_name="x",
                           classification="high",
                           field_changes=(
                               FieldChange(kind="required_added",
                                           field_name="y"),
                           ))
        diff = MigrationDiff(changes=(cc,))
        required = collect_required_fingerprints(diff)
        assert ack_covers(diff, {"accepted_changes": list(required)})

    def test_per_change_ack_misses_when_incomplete(self):
        diff = self._high_diff()
        assert not ack_covers(diff, {"accepted_changes": []})

    def test_per_change_ack_works_in_any_environment(self):
        """Per-change fingerprints are always honored, dev_mode or not."""
        diff = self._high_diff()
        required = collect_required_fingerprints(diff)
        # No dev_mode set; per-change still works.
        assert ack_covers(diff, {"accepted_changes": list(required)})

    def test_safe_diff_doesnt_need_ack(self):
        diff = MigrationDiff(changes=(
            ContentChange(kind="added", content_name="x",
                          classification="safe"),
        ))
        # No required fingerprints → trivially acked.
        assert ack_covers(diff, {})


# ── Provider end-to-end (modify/remove/rename + table-rebuild) ──────


@pytest.fixture
def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _schema(name, *, fields=()):
    return {
        "name": {"snake": name, "display": name, "pascal": name.capitalize()},
        "fields": tuple(fields),
        "state_machines": (),
    }


class TestProviderMigrationPaths:
    @pytest.mark.asyncio
    async def test_initial_deploy_then_add_field_in_place(self, tmp_db):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        # Initial deploy.
        v1 = _schema("things", fields=(
            _field("label", business_type="text", required=True),
        ))
        from termin_core.providers.storage_contract import initial_deploy_diff
        await provider.migrate(initial_deploy_diff([v1]))
        # Add some data.
        await provider.create("things", {"label": "first"})
        # Evolve: add an optional field.
        v2 = _schema("things", fields=(
            _field("label", business_type="text", required=True),
            _field("note", business_type="text"),
        ))
        diff = compute_migration_diff([v1], [v2])
        await provider.migrate(diff)
        # Original record still readable, with NULL note.
        page = await provider.query("things", None, _opts(limit=10))
        assert len(page.records) == 1
        assert page.records[0]["label"] == "first"

    @pytest.mark.asyncio
    async def test_remove_table_drops(self, tmp_db):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        v1 = _schema("things", fields=(_field("label"),))
        from termin_core.providers.storage_contract import initial_deploy_diff
        await provider.migrate(initial_deploy_diff([v1]))
        # Remove (empty) — classification="low" via downgrade.
        diff = MigrationDiff(changes=(
            ContentChange(kind="removed", content_name="things",
                          classification="low"),
        ))
        await provider.migrate(diff)
        # Querying the removed table now fails (table doesn't exist).
        with pytest.raises(Exception):
            await provider.query("things", None, _opts(limit=1))

    @pytest.mark.asyncio
    async def test_rename_field_in_place(self, tmp_db):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        v1 = _schema("things", fields=(_field("old_label",
                                              business_type="text",
                                              required=True),))
        from termin_core.providers.storage_contract import initial_deploy_diff
        await provider.migrate(initial_deploy_diff([v1]))
        await provider.create("things", {"old_label": "value"})
        # Now rename old_label → new_label.
        v2 = _schema("things", fields=(_field("new_label",
                                              business_type="text",
                                              required=True),))
        diff = compute_migration_diff([v1], [v2])
        diff = apply_rename_mappings(
            diff,
            rename_fields=[{"content": "things",
                            "from": "old_label",
                            "to": "new_label"}],
        )
        await provider.migrate(diff)
        page = await provider.query("things", None, _opts(limit=10))
        assert page.records[0]["new_label"] == "value"
        assert "old_label" not in page.records[0]

    @pytest.mark.asyncio
    async def test_cascade_mode_change_via_rebuild(self, tmp_db):
        """The headline v0.8 → v0.9 case: bring an existing FK column
        from no-cascade-mode to explicit cascade-on-delete."""
        provider = SqliteStorageProvider({"db_path": tmp_db})
        # v0.8 shape: parent + child, no cascade_mode declared.
        # In our IR that means cascade_mode=None on the FK field.
        # init_db today emits ON DELETE based on cascade_mode — so
        # to simulate v0.8 we use cascade_mode=restrict (closest
        # equivalent). Then "evolve" to cascade on delete.
        parent_v1 = _schema("parents", fields=(
            _field("name", business_type="text", required=True),
        ))
        child_v1 = _schema("children", fields=(
            _field("label", business_type="text", required=True),
            _field("parent", business_type="reference", required=True,
                   foreign_key="parents", cascade_mode="restrict"),
        ))
        from termin_core.providers.storage_contract import initial_deploy_diff
        await provider.migrate(initial_deploy_diff([parent_v1, child_v1]))

        # Insert data.
        p = await provider.create("parents", {"name": "p1"})
        c = await provider.create("children",
                                  {"label": "c1", "parent": p["id"]})

        # Evolve: same shape but cascade on delete.
        child_v2 = _schema("children", fields=(
            _field("label", business_type="text", required=True),
            _field("parent", business_type="reference", required=True,
                   foreign_key="parents", cascade_mode="cascade"),
        ))
        diff = compute_migration_diff([parent_v1, child_v1],
                                      [parent_v1, child_v2])
        await provider.migrate(diff)

        # Data preserved through the rebuild.
        page = await provider.query("children", None, _opts(limit=10))
        assert len(page.records) == 1
        assert page.records[0]["label"] == "c1"

        # New cascade behavior: deleting parent now removes child.
        from termin_core.providers.storage_contract import CascadeMode
        await provider.delete("parents", p["id"], cascade_mode=CascadeMode.CASCADE)
        page = await provider.query("children", None, _opts(limit=10))
        assert page.records == ()


def _opts(**kwargs):
    from termin_core.providers.storage_contract import QueryOptions
    return QueryOptions(**kwargs)


# ── Schema metadata round-trip ──────────────────────────────────────


class TestSchemaMetadata:
    @pytest.mark.asyncio
    async def test_metadata_round_trip(self, tmp_db):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        schemas = [_schema("things", fields=(_field("label"),))]
        from termin_core.providers.storage_contract import initial_deploy_diff
        await provider.migrate(initial_deploy_diff(schemas))
        await provider.write_schema_metadata(schemas)

        retrieved = await provider.read_schema_metadata()
        assert retrieved is not None
        assert len(retrieved) == 1
        assert retrieved[0]["name"]["snake"] == "things"

    @pytest.mark.asyncio
    async def test_brand_new_db_returns_none(self, tmp_db):
        # Make sure tmp_db doesn't exist yet
        if os.path.exists(tmp_db):
            os.unlink(tmp_db)
        provider = SqliteStorageProvider({"db_path": tmp_db})
        result = await provider.read_schema_metadata()
        assert result is None

    @pytest.mark.asyncio
    async def test_v08_db_falls_back_to_introspection(self, tmp_db):
        """If a DB has tables but no _termin_schema, introspection
        kicks in. This is the v0.8 → v0.9 first-boot path."""
        provider = SqliteStorageProvider({"db_path": tmp_db})
        # Create v0.8-shape via init_db (no metadata table written).
        v08_schema = _schema("things", fields=(
            _field("label", business_type="text", required=True),
        ))
        from termin_server import storage as _storage
        await _storage.init_db([v08_schema], tmp_db)

        retrieved = await provider.read_schema_metadata()
        # Introspection should return something (best-effort) for
        # the existing table.
        assert retrieved is not None
        assert any(c["name"]["snake"] == "things" for c in retrieved)


# ── Backup ──────────────────────────────────────────────────────────


class TestBackup:
    @pytest.mark.asyncio
    async def test_backup_creates_file(self, tmp_db):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        # Initialize with some data so the file exists and isn't empty.
        v1 = _schema("things", fields=(_field("label"),))
        from termin_core.providers.storage_contract import initial_deploy_diff
        await provider.migrate(initial_deploy_diff([v1]))
        await provider.create("things", {"label": "data"})

        backup_path = await provider.create_backup()
        assert backup_path is not None
        assert os.path.exists(backup_path)
        assert backup_path.startswith(tmp_db + ".pre-")
        assert backup_path.endswith(".bak")

        # Cleanup
        os.unlink(backup_path)

    @pytest.mark.asyncio
    async def test_backup_returns_none_for_in_memory_db(self):
        provider = SqliteStorageProvider({"db_path": ":memory:"})
        result = await provider.create_backup()
        assert result is None

    @pytest.mark.asyncio
    async def test_backup_returns_none_when_db_doesnt_exist(self, tmp_db):
        if os.path.exists(tmp_db):
            os.unlink(tmp_db)
        provider = SqliteStorageProvider({"db_path": tmp_db})
        result = await provider.create_backup()
        assert result is None
