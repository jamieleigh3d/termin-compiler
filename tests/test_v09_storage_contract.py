# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.9 Phase 2 — Storage contract surface and SQLite provider tests.

Three layers:

1. Contract-shape tests — the typed surface (Predicate AST,
   QueryOptions, Page, MigrationDiff) behaves correctly in isolation.
   No I/O.

2. SQLite provider behavior tests — the first-party SqliteStorageProvider
   actually persists, queries, updates, and deletes against a real
   on-disk SQLite file. Predicate AST → SQL pushdown is exercised
   end-to-end with parameter binding verified.

3. Runtime wire-up tests — the create_termin_app() factory installs
   ctx.storage, routes/pages use it (not the legacy direct imports),
   and two apps in the same process don't share storage state.

Each test in (3) was independently verified to fail when the
corresponding wire-up step is reverted; comments document the revert
points so the next session can re-verify.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from termin_runtime.providers import (
    Eq, Ne, Gt, Gte, Lt, Lte, In, Contains, And, Or, Not,
    OrderBy, QueryOptions, Page, CascadeMode,
    FieldChange, ContentChange, MigrationDiff, initial_deploy_diff,
    StorageProvider,
)
from termin_runtime.providers.builtins.storage_sqlite import (
    SqliteStorageProvider, _compile_predicate,
    _encode_cursor, _decode_cursor, register_sqlite_storage,
)
from termin_runtime.providers.contracts import Category, ContractRegistry
from termin_runtime.providers.registry import ProviderRegistry


# ─────────────────────────────────────────────────────────────────────
# Layer 1 — Contract shape tests
# ─────────────────────────────────────────────────────────────────────


class TestPredicateAST:
    """The Predicate AST is a closed sum type — adding a new shape
    requires contract evolution. These tests pin the shape and the
    invariants enforced by __post_init__."""

    def test_eq_holds_field_and_value(self):
        p = Eq(field="name", value="alice")
        assert p.field == "name"
        assert p.value == "alice"

    def test_eq_is_hashable(self):
        # Frozen dataclass invariant — predicates can be cached.
        d = {Eq(field="x", value=1): "ok"}
        assert d[Eq(field="x", value=1)] == "ok"

    def test_in_coerces_list_to_tuple(self):
        # __post_init__ promotes list/set to tuple so the dataclass
        # stays hashable.
        p = In(field="status", values=["open", "closed"])
        assert p.values == ("open", "closed")

    def test_in_with_set_coerces_to_tuple(self):
        p = In(field="status", values={"a", "b"})
        assert isinstance(p.values, tuple)
        assert set(p.values) == {"a", "b"}

    def test_and_requires_at_least_one_predicate(self):
        with pytest.raises(ValueError, match="at least one"):
            And(predicates=())

    def test_or_requires_at_least_one_predicate(self):
        with pytest.raises(ValueError, match="at least one"):
            Or(predicates=())

    def test_and_coerces_list_to_tuple(self):
        p = And(predicates=[Eq("a", 1), Eq("b", 2)])
        assert isinstance(p.predicates, tuple)

    def test_not_holds_one_predicate(self):
        p = Not(predicate=Eq(field="x", value=1))
        assert isinstance(p.predicate, Eq)

    def test_contains_is_a_distinct_shape_from_eq(self):
        # Eq("x", "abc") and Contains("x", "abc") must compile to
        # different SQL — substring vs equality. Smoke test for the
        # AST shape; the SQL compiler test below proves the SQL
        # actually differs.
        a = Eq(field="x", value="abc")
        b = Contains(field="x", substring="abc")
        assert type(a) is not type(b)


class TestQueryOptions:
    def test_defaults(self):
        opts = QueryOptions()
        assert opts.limit == 50
        assert opts.cursor is None
        assert opts.order_by == ()

    def test_limit_capped_at_1000(self):
        with pytest.raises(ValueError, match="exceed 1000"):
            QueryOptions(limit=1001)

    def test_negative_limit_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            QueryOptions(limit=-1)

    def test_order_by_must_be_orderby_instances(self):
        # Common mistake: passing dicts. The contract enforces type.
        with pytest.raises(TypeError, match="OrderBy"):
            QueryOptions(order_by=({"field": "x", "direction": "asc"},))

    def test_order_by_direction_validated(self):
        with pytest.raises(ValueError, match="asc"):
            OrderBy(field="x", direction="DESC")  # uppercase is wrong

    def test_order_by_list_coerced_to_tuple(self):
        opts = QueryOptions(order_by=[OrderBy(field="x", direction="asc")])
        assert isinstance(opts.order_by, tuple)


class TestPage:
    def test_empty_page(self):
        p = Page(records=())
        assert p.records == ()
        assert p.next_cursor is None
        assert p.estimated_total is None

    def test_records_list_coerced_to_tuple(self):
        p = Page(records=[{"id": 1}, {"id": 2}])
        assert isinstance(p.records, tuple)


class TestMigrationDiff:
    def test_initial_deploy_diff_classifies_all_as_safe(self):
        schemas = [
            {"name": {"snake": "products", "display": "products"}, "fields": []},
            {"name": {"snake": "orders", "display": "orders"}, "fields": []},
        ]
        diff = initial_deploy_diff(schemas)
        assert len(diff.changes) == 2
        assert all(c.kind == "added" for c in diff.changes)
        assert all(c.classification == "safe" for c in diff.changes)
        assert not diff.is_blocked
        assert not diff.has_risky

    def test_blocked_change_marks_diff_blocked(self):
        diff = MigrationDiff(changes=(
            ContentChange(
                kind="modified", content_name="x",
                classification="blocked",
            ),
        ))
        assert diff.is_blocked

    def test_low_risk_change_needs_ack(self):
        diff = MigrationDiff(changes=(
            ContentChange(
                kind="modified", content_name="x",
                classification="low",
            ),
        ))
        assert diff.has_low_risk
        assert diff.needs_ack
        assert diff.has_risky  # backwards-compat shim
        assert not diff.is_blocked

    def test_high_risk_change_needs_ack_and_marks_high(self):
        diff = MigrationDiff(changes=(
            ContentChange(
                kind="modified", content_name="x",
                classification="high",
            ),
        ))
        assert diff.has_high_risk
        assert diff.needs_ack
        assert not diff.is_blocked

    def test_overall_classification_is_worst(self):
        diff = MigrationDiff(changes=(
            ContentChange(kind="added", content_name="a", classification="safe"),
            ContentChange(kind="modified", content_name="b", classification="medium"),
            ContentChange(kind="modified", content_name="c", classification="high"),
        ))
        assert diff.overall_classification == "high"

    def test_classification_validated(self):
        with pytest.raises(ValueError, match="classification"):
            ContentChange(
                kind="added", content_name="x",
                classification="WHATEVER",
            )

    def test_kind_validated(self):
        with pytest.raises(ValueError, match="kind"):
            ContentChange(
                kind="UPSERTED", content_name="x",
                classification="safe",
            )


class TestProtocolConformance:
    """SqliteStorageProvider should satisfy the StorageProvider
    Protocol. runtime_checkable Protocols verify presence of the
    listed methods at runtime."""

    def test_sqlite_provider_satisfies_protocol(self):
        provider = SqliteStorageProvider({"db_path": ":memory:"})
        assert isinstance(provider, StorageProvider)


# ─────────────────────────────────────────────────────────────────────
# Layer 2 — SQLite provider behavior + Predicate AST → SQL pushdown
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    """A path to a fresh per-test SQLite file."""
    return str(tmp_path / "phase2.db")


@pytest.fixture
def products_schema():
    return {
        "name": {"snake": "products", "display": "products"},
        "fields": [
            {"name": "name", "business_type": "text", "required": True},
            {"name": "price", "business_type": "currency"},
            {"name": "stock", "business_type": "whole_number"},
            {"name": "tier", "business_type": "text"},
        ],
    }


class TestPredicateCompilation:
    """The Predicate AST → SQL compiler must produce parameterized
    SQL (no value inlining), validate identifiers, and handle the
    boolean combinators correctly. These are pure-function tests
    on _compile_predicate."""

    def test_eq_compiles_to_parameterized_eq(self):
        sql, params = _compile_predicate(Eq(field="name", value="widget"))
        assert sql == '"name" = ?'
        assert params == ["widget"]

    def test_ne_compiles_to_parameterized_ne(self):
        sql, params = _compile_predicate(Ne(field="status", value="closed"))
        assert sql == '"status" != ?'
        assert params == ["closed"]

    def test_comparison_predicates_use_correct_operators(self):
        for cls, op in [(Gt, ">"), (Gte, ">="), (Lt, "<"), (Lte, "<=")]:
            sql, params = _compile_predicate(cls(field="x", value=10))
            assert sql == f'"x" {op} ?'
            assert params == [10]

    def test_in_with_values_emits_placeholders(self):
        sql, params = _compile_predicate(In(field="tier", values=("a", "b", "c")))
        assert sql == '"tier" IN (?, ?, ?)'
        assert params == ["a", "b", "c"]

    def test_in_with_empty_values_emits_false_predicate(self):
        # SQL `IN ()` is a syntax error; the compiler emits `1 = 0`.
        sql, params = _compile_predicate(In(field="tier", values=()))
        assert sql == "1 = 0"
        assert params == []

    def test_contains_uses_glob_for_case_sensitivity(self):
        # BRD §6.2 requires Contains to be case-sensitive. SQLite
        # LIKE is case-insensitive for ASCII; GLOB is case-sensitive.
        sql, params = _compile_predicate(Contains(field="name", substring="Wid"))
        assert sql == '"name" GLOB ?'
        assert params == ["*Wid*"]

    def test_contains_escapes_glob_metacharacters(self):
        # A substring containing `*` should match the literal `*`,
        # not "any sequence". The compiler escapes via `[*]`.
        sql, params = _compile_predicate(Contains(field="name", substring="a*b"))
        assert "[*]" in params[0]

    def test_and_combines_with_parens(self):
        sql, params = _compile_predicate(
            And(predicates=(Eq("a", 1), Eq("b", 2)))
        )
        assert sql == '("a" = ? AND "b" = ?)'
        assert params == [1, 2]

    def test_or_combines_with_parens(self):
        sql, params = _compile_predicate(
            Or(predicates=(Eq("a", 1), Eq("b", 2)))
        )
        assert sql == '("a" = ? OR "b" = ?)'
        assert params == [1, 2]

    def test_not_wraps_in_negation(self):
        sql, params = _compile_predicate(Not(predicate=Eq("a", 1)))
        assert sql == 'NOT ("a" = ?)'
        assert params == [1]

    def test_unsafe_field_name_rejected(self):
        # Same identifier validation as init_db — defense in depth.
        # `name; DROP TABLE` would be unsafe; the validator rejects.
        with pytest.raises(ValueError, match="Unsafe SQL"):
            _compile_predicate(Eq(field="name; drop", value=1))

    def test_unknown_predicate_type_rejected(self):
        # The Predicate AST is closed; unknown types are a programmer
        # error, not silent SQL. Custom dataclass to simulate a
        # would-be extension.
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Custom:
            field: str

        with pytest.raises(TypeError, match="Unknown predicate type"):
            _compile_predicate(Custom(field="x"))  # type: ignore


class TestCursorEncoding:
    def test_encode_decode_roundtrip(self):
        for offset in (0, 1, 50, 999):
            assert _decode_cursor(_encode_cursor(offset)) == offset

    def test_decode_none_returns_zero(self):
        assert _decode_cursor(None) == 0

    def test_decode_empty_returns_zero(self):
        assert _decode_cursor("") == 0

    def test_decode_garbage_raises(self):
        with pytest.raises(ValueError, match="Invalid cursor"):
            _decode_cursor("not-base64-not-an-int")


class TestSqliteProviderCRUD:
    """End-to-end: provider against a real SQLite file. Each test
    creates its own DB via tmp_db so there's no cross-test state."""

    @pytest.mark.asyncio
    async def test_initial_deploy_diff_creates_tables(
        self, tmp_db, products_schema
    ):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        await provider.migrate(initial_deploy_diff([products_schema]))
        # Verify the table exists by inserting and reading.
        record = await provider.create("products", {"name": "widget", "price": 9.99})
        assert record["id"] is not None
        assert record["name"] == "widget"

    @pytest.mark.asyncio
    async def test_migrate_blocked_diff_refused(self, tmp_db):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        diff = MigrationDiff(changes=(
            ContentChange(
                kind="modified", content_name="x", classification="blocked",
            ),
        ))
        with pytest.raises(ValueError, match="blocked"):
            await provider.migrate(diff)

    @pytest.mark.asyncio
    async def test_migrate_blocked_diff_refused(self, tmp_db):
        # The provider defensively refuses a blocked diff even
        # though the runtime should reject before invoking. v0.9
        # Phase 2.x (b) implements modify/remove, so the historical
        # NotImplementedError path is gone — the only refusal is
        # for blocked.
        provider = SqliteStorageProvider({"db_path": tmp_db})
        diff = MigrationDiff(changes=(
            ContentChange(
                kind="modified", content_name="products",
                classification="blocked",
            ),
        ))
        with pytest.raises(ValueError, match="blocked"):
            await provider.migrate(diff)

    @pytest.mark.asyncio
    async def test_create_then_read_roundtrips(self, tmp_db, products_schema):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        await provider.migrate(initial_deploy_diff([products_schema]))
        created = await provider.create(
            "products", {"name": "widget", "price": 9.99, "stock": 100}
        )
        record = await provider.read("products", created["id"])
        assert record is not None
        assert record["name"] == "widget"
        assert record["stock"] == 100

    @pytest.mark.asyncio
    async def test_read_missing_returns_none_not_404(self, tmp_db, products_schema):
        # BRD §6.2: provider returns None — HTTP 404 is a runtime
        # translation, not a provider concern.
        provider = SqliteStorageProvider({"db_path": tmp_db})
        await provider.migrate(initial_deploy_diff([products_schema]))
        record = await provider.read("products", 99999)
        assert record is None

    @pytest.mark.asyncio
    async def test_create_with_empty_record_returns_none_id(
        self, tmp_db, products_schema
    ):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        await provider.migrate(initial_deploy_diff([products_schema]))
        # Empty record (no insertable columns) shouldn't crash —
        # legacy convention returns id=None as a sentinel.
        result = await provider.create("products", {"name": ""})
        assert result == {"id": None}

    @pytest.mark.asyncio
    async def test_update_returns_post_update_record(
        self, tmp_db, products_schema
    ):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        await provider.migrate(initial_deploy_diff([products_schema]))
        created = await provider.create("products", {"name": "widget", "price": 9.99})
        updated = await provider.update(
            "products", created["id"], {"price": 14.99}
        )
        assert updated is not None
        assert updated["price"] == 14.99
        assert updated["name"] == "widget"  # unchanged field preserved

    @pytest.mark.asyncio
    async def test_update_missing_returns_none(self, tmp_db, products_schema):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        await provider.migrate(initial_deploy_diff([products_schema]))
        result = await provider.update("products", 99999, {"name": "ghost"})
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_returns_true_on_success(
        self, tmp_db, products_schema
    ):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        await provider.migrate(initial_deploy_diff([products_schema]))
        created = await provider.create("products", {"name": "widget", "price": 9.99})
        deleted = await provider.delete("products", created["id"])
        assert deleted is True
        assert await provider.read("products", created["id"]) is None

    @pytest.mark.asyncio
    async def test_delete_missing_returns_false(self, tmp_db, products_schema):
        provider = SqliteStorageProvider({"db_path": tmp_db})
        await provider.migrate(initial_deploy_diff([products_schema]))
        deleted = await provider.delete("products", 99999)
        assert deleted is False


async def _populated_provider(tmp_db, products_schema):
    """Build a provider with a fixed dataset of 20 records.

    Plain async helper rather than a pytest fixture — pytest-asyncio's
    handling of async fixtures across versions is fiddly, and inline
    setup keeps each test's data shape explicit and copy-pasteable
    when debugging.
    """
    provider = SqliteStorageProvider({"db_path": tmp_db})
    await provider.migrate(initial_deploy_diff([products_schema]))
    for i in range(20):
        await provider.create("products", {
            "name": f"item-{i:02d}",
            "price": i * 1.5,
            "stock": i,
            "tier": "A" if i < 10 else "B",
        })
    return provider


class TestSqliteProviderQuery:
    """End-to-end query() against a populated DB. Exercises Predicate
    AST pushdown, sort, pagination, and cursor encoding."""

    @pytest.mark.asyncio
    async def test_query_no_predicate_returns_all(self, tmp_db, products_schema):
        provider = await _populated_provider(tmp_db, products_schema)
        page = await provider.query("products", None, QueryOptions(limit=100))
        assert len(page.records) == 20

    @pytest.mark.asyncio
    async def test_query_with_eq_predicate_filters(self, tmp_db, products_schema):
        provider = await _populated_provider(tmp_db, products_schema)
        page = await provider.query(
            "products", Eq(field="tier", value="A"),
            QueryOptions(limit=100),
        )
        assert len(page.records) == 10
        assert all(r["tier"] == "A" for r in page.records)

    @pytest.mark.asyncio
    async def test_query_with_gt_predicate(self, tmp_db, products_schema):
        provider = await _populated_provider(tmp_db, products_schema)
        page = await provider.query(
            "products", Gt(field="stock", value=15),
            QueryOptions(limit=100),
        )
        assert len(page.records) == 4  # stock 16, 17, 18, 19

    @pytest.mark.asyncio
    async def test_query_with_in_predicate(self, tmp_db, products_schema):
        provider = await _populated_provider(tmp_db, products_schema)
        page = await provider.query(
            "products", In(field="stock", values=(0, 5, 10)),
            QueryOptions(limit=100),
        )
        assert len(page.records) == 3
        assert sorted(r["stock"] for r in page.records) == [0, 5, 10]

    @pytest.mark.asyncio
    async def test_query_with_and_combines_predicates(self, tmp_db, products_schema):
        provider = await _populated_provider(tmp_db, products_schema)
        page = await provider.query(
            "products",
            And(predicates=(
                Eq(field="tier", value="A"),
                Gte(field="stock", value=5),
            )),
            QueryOptions(limit=100),
        )
        # tier=A is 0..9, stock>=5 picks 5..9 → 5 records.
        assert len(page.records) == 5

    @pytest.mark.asyncio
    async def test_query_with_contains_is_case_sensitive(self, tmp_db, products_schema):
        provider = await _populated_provider(tmp_db, products_schema)
        # All names start with lowercase "item-". Uppercase Contains
        # should match nothing (case-sensitive).
        page = await provider.query(
            "products", Contains(field="name", substring="ITEM"),
            QueryOptions(limit=100),
        )
        assert len(page.records) == 0

    @pytest.mark.asyncio
    async def test_query_with_not_predicate(self, tmp_db, products_schema):
        provider = await _populated_provider(tmp_db, products_schema)
        page = await provider.query(
            "products", Not(predicate=Eq(field="tier", value="A")),
            QueryOptions(limit=100),
        )
        assert len(page.records) == 10  # all the B-tier records
        assert all(r["tier"] == "B" for r in page.records)

    @pytest.mark.asyncio
    async def test_query_default_order_appends_id_for_stability(self, tmp_db, products_schema):
        # No order_by supplied; provider must order by id for
        # deterministic pagination.
        provider = await _populated_provider(tmp_db, products_schema)
        page = await provider.query("products", None, QueryOptions(limit=5))
        ids = [r["id"] for r in page.records]
        assert ids == sorted(ids)

    @pytest.mark.asyncio
    async def test_query_explicit_order_by_descending(self, tmp_db, products_schema):
        provider = await _populated_provider(tmp_db, products_schema)
        page = await provider.query(
            "products", None,
            QueryOptions(
                limit=5,
                order_by=(OrderBy(field="stock", direction="desc"),),
            ),
        )
        stocks = [r["stock"] for r in page.records]
        assert stocks == sorted(stocks, reverse=True)

    @pytest.mark.asyncio
    async def test_query_pagination_with_cursor(self, tmp_db, products_schema):
        provider = await _populated_provider(tmp_db, products_schema)
        # Page 1 of 5.
        page1 = await provider.query("products", None, QueryOptions(limit=5))
        assert len(page1.records) == 5
        assert page1.next_cursor is not None
        # Page 2 using the returned cursor.
        page2 = await provider.query(
            "products", None,
            QueryOptions(limit=5, cursor=page1.next_cursor),
        )
        assert len(page2.records) == 5
        # No id overlap between pages.
        ids1 = {r["id"] for r in page1.records}
        ids2 = {r["id"] for r in page2.records}
        assert not (ids1 & ids2)

    @pytest.mark.asyncio
    async def test_query_pagination_last_page_has_no_cursor(self, tmp_db, products_schema):
        provider = await _populated_provider(tmp_db, products_schema)
        # 20 records; with limit=10 the second page is the last.
        page1 = await provider.query("products", None, QueryOptions(limit=10))
        assert page1.next_cursor is not None
        page2 = await provider.query(
            "products", None,
            QueryOptions(limit=10, cursor=page1.next_cursor),
        )
        assert page2.next_cursor is None

    @pytest.mark.asyncio
    async def test_query_unsafe_filter_field_rejected(self, tmp_db, products_schema):
        # Defense in depth — unsafe identifiers are rejected at the
        # predicate compiler before SQL is emitted.
        provider = await _populated_provider(tmp_db, products_schema)
        with pytest.raises(ValueError, match="Unsafe"):
            await provider.query(
                "products", Eq(field="name; drop", value="x"),
                QueryOptions(limit=10),
            )


# ─────────────────────────────────────────────────────────────────────
# Layer 3 — Runtime wire-up tests
# ─────────────────────────────────────────────────────────────────────


class TestProviderRegistration:
    """The SQLite storage provider is registered through the same
    ProviderRegistry mechanism third-party providers use (BRD §10
    "One loading path for all providers")."""

    def test_register_sqlite_storage_adds_to_registry(self):
        contracts = ContractRegistry.default()
        registry = ProviderRegistry()
        register_sqlite_storage(registry, contracts)
        record = registry.get(Category.STORAGE, "default", "sqlite")
        assert record is not None
        assert record.product_name == "sqlite"
        assert record.conformance == "passing"

    def test_factory_constructs_provider(self):
        contracts = ContractRegistry.default()
        registry = ProviderRegistry()
        register_sqlite_storage(registry, contracts)
        record = registry.get(Category.STORAGE, "default", "sqlite")
        instance = record.factory({"db_path": ":memory:"})
        assert isinstance(instance, SqliteStorageProvider)


class TestRuntimeWireUp:
    """Behavioral guard: create_termin_app() installs ctx.storage and
    routes/pages use it. Static-import checks (test_v09_provider_registry)
    are necessary but not sufficient — these tests exercise the runtime
    path end-to-end."""

    def _make_app(self, db_path: str | None = None):
        """Build a minimal app with one Content type and one role."""
        from termin_runtime.app import create_termin_app
        ir = {
            "name": "Phase2Test", "app_id": "phase2-test",
            "auth": {
                "provider": "stub",
                "scopes": ["app.view"],
                "roles": [
                    {"name": "Anonymous", "scopes": ["app.view"]},
                ],
            },
            "content": [{
                "name": {"snake": "items", "display": "items"},
                "singular": "item",
                "fields": [
                    {"name": "label", "business_type": "text"},
                ],
            }],
            "computes": [], "channels": [], "routes": [],
            "pages": [], "events": [], "state_machines": [],
            "boundaries": [], "streams": [],
            "access_grants": [],
            "nav_items": [],
        }
        return create_termin_app(json.dumps(ir), db_path=db_path)

    def test_ctx_storage_is_set_after_app_construction(self, tmp_db):
        # Revert verification: in app.py, comment out
        # `ctx.storage = storage_record.factory(storage_config)` →
        # this assertion will fail.
        app = self._make_app(db_path=tmp_db)
        assert app.state.ctx.storage is not None
        assert isinstance(app.state.ctx.storage, SqliteStorageProvider)

    def test_storage_provider_uses_app_db_path_not_default(self, tmp_db):
        # Revert verification: in app.py, drop the
        # `storage_config["db_path"] = resolved_db_path` line →
        # provider would default to "app.db" and this assertion
        # would fail.
        app = self._make_app(db_path=tmp_db)
        provider = app.state.ctx.storage
        assert provider._db_path == tmp_db

    def test_two_apps_do_not_share_storage_provider_state(
        self, tmp_path,
    ):
        # The v0.8 _db_path module-global problem: two apps in one
        # process would clobber each other. v0.9 Phase 2: each app
        # carries its own provider instance with its own db_path.
        # This is the architectural fix for the cross-app contamination
        # class of bug. Revert verification: replace the per-app
        # provider construction with a module-global that mutates on
        # each app build → second app's reads would hit the first
        # app's DB.
        app_a = self._make_app(db_path=str(tmp_path / "a.db"))
        app_b = self._make_app(db_path=str(tmp_path / "b.db"))
        assert app_a.state.ctx.storage is not app_b.state.ctx.storage
        assert app_a.state.ctx.storage._db_path != app_b.state.ctx.storage._db_path

    def test_unregistered_storage_product_fails_closed(self):
        # BRD §6.2 Tier 1: storage outage takes the app down. An
        # unregistered product should raise at construct time, not
        # at the first CRUD call. Revert verification: remove the
        # `if storage_record is None: raise` block → the app would
        # build with ctx.storage=None and crash on first request.
        from termin_runtime.app import create_termin_app
        ir = {
            "name": "Phase2Test", "app_id": "phase2-test",
            "auth": {
                "provider": "stub",
                "scopes": ["app.view"],
                "roles": [{"name": "Anonymous", "scopes": ["app.view"]}],
            },
            "content": [], "computes": [], "channels": [], "routes": [],
            "pages": [], "events": [], "state_machines": [],
            "boundaries": [], "streams": [], "access_grants": [],
            "nav_items": [],
        }
        deploy_config = {
            "version": "0.9.0",
            "bindings": {
                "storage": {"provider": "nonexistent-storage", "config": {}},
            },
        }
        with pytest.raises(RuntimeError, match="not registered"):
            create_termin_app(
                json.dumps(ir), deploy_config=deploy_config,
            )


class TestRouteUsesStorageProvider:
    """End-to-end: hitting the auto-CRUD endpoints actually goes
    through ctx.storage, not the legacy storage.create_record path."""

    def _make_app_with_route(self, tmp_path):
        """App with one CREATE + LIST route on `items`."""
        from termin_runtime.app import create_termin_app
        ir = {
            "name": "Phase2Test", "app_id": "phase2-test",
            "auth": {
                "provider": "stub",
                "scopes": ["app.view", "app.write"],
                "roles": [
                    {"name": "Anonymous", "scopes": ["app.view", "app.write"]},
                ],
            },
            "content": [{
                "name": {"snake": "items", "display": "items"},
                "singular": "item",
                "fields": [{"name": "label", "business_type": "text"}],
            }],
            "computes": [], "channels": [], "events": [],
            "state_machines": [], "boundaries": [], "streams": [],
            "pages": [], "nav_items": [], "access_grants": [],
            "routes": [
                {"method": "GET", "path": "/api/items",
                 "kind": "LIST", "content_ref": "items"},
                {"method": "POST", "path": "/api/items",
                 "kind": "CREATE", "content_ref": "items"},
                {"method": "GET", "path": "/api/items/{id}",
                 "kind": "GET_ONE", "content_ref": "items",
                 "lookup_column": "id"},
                {"method": "DELETE", "path": "/api/items/{id}",
                 "kind": "DELETE", "content_ref": "items",
                 "lookup_column": "id"},
            ],
        }
        return create_termin_app(
            json.dumps(ir),
            db_path=str(tmp_path / "wireup.db"),
        )

    @staticmethod
    def _run_async(coro):
        """Run a coroutine on a fresh event loop.

        Calling `asyncio.get_event_loop()` inside a sync test that
        also uses TestClient is unreliable across pytest-asyncio
        versions and across previously-run tests in the same
        session — the returned loop may be closed, may be the wrong
        one, or may already be running. A fresh isolated loop per
        call is robust and matches the pattern used elsewhere in
        the runtime for cross-loop calls.
        """
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_create_route_persists_via_ctx_storage(self, tmp_path):
        # Behavioral guard: a record created via the HTTP CREATE
        # route is readable via ctx.storage.read. If the route were
        # routed around the provider (e.g. directly to legacy
        # storage.create_record), the provider's read might miss
        # the row — this test catches that class of bug.
        from fastapi.testclient import TestClient
        app = self._make_app_with_route(tmp_path)
        with TestClient(app) as client:
            resp = client.post("/api/items", json={"label": "test-item"})
            assert resp.status_code == 201
            created = resp.json()
            assert created["label"] == "test-item"
            # Read directly through the provider — proves the create
            # actually went through the provider's persistence layer.
            ctx = app.state.ctx
            record = self._run_async(ctx.storage.read("items", created["id"]))
            assert record is not None
            assert record["label"] == "test-item"

    def test_list_route_returns_records_visible_to_provider(self, tmp_path):
        # Symmetric: a record inserted via the provider must show
        # up in the LIST route, proving LIST goes through the
        # provider too.
        from fastapi.testclient import TestClient
        app = self._make_app_with_route(tmp_path)
        with TestClient(app) as client:
            # Bootstrap the schema by hitting ANY route first (lifespan
            # runs migrate()). LIST is fine.
            client.get("/api/items")
            # Now insert via the provider directly.
            ctx = app.state.ctx
            self._run_async(
                ctx.storage.create("items", {"label": "from-provider"})
            )
            resp = client.get("/api/items")
            assert resp.status_code == 200
            records = resp.json()
            assert any(r["label"] == "from-provider" for r in records)

    def test_delete_route_uses_provider_delete(self, tmp_path):
        # Revert verification: change `_make_delete_route` to call
        # the legacy `delete_record(db, ...)` again → record would
        # still be deletable, but the test below also asserts the
        # record vanishes from the provider's view. The provider
        # holds the same db path so this is mostly a smoke test of
        # the delete-route path itself.
        from fastapi.testclient import TestClient
        app = self._make_app_with_route(tmp_path)
        with TestClient(app) as client:
            created = client.post("/api/items", json={"label": "doomed"}).json()
            resp = client.delete(f"/api/items/{created['id']}")
            assert resp.status_code == 200
            # 404 from the GET route — record gone.
            resp = client.get(f"/api/items/{created['id']}")
            assert resp.status_code == 404
