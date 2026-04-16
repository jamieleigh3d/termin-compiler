"""Runtime coverage push: test failure paths across 6 modules.

Target modules:
  - ai_provider.py: Mock LLM, tool building, agent loop
  - channels.py: WebSocket reconnect, metrics, error paths
  - transaction.py: Staging, commit/rollback semantics
  - expression.py: CEL edge cases, bad expressions, missing variables
  - reflection.py: Query paths for Content, Computes, Channels, state machines
  - errors.py: Error router paths, typed handlers, catch-all
"""

import asyncio
import json
import math
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# ═══════════════════════════════════════════════════════════════
# ai_provider.py
# ═══════════════════════════════════════════════════════════════

from termin_runtime.ai_provider import (
    AIProvider, AIProviderError, build_output_tool, build_agent_tools,
)


class TestAIProviderConfiguration:
    """Test AIProvider initialization and configuration detection."""

    def test_unconfigured_no_service(self):
        p = AIProvider({})
        assert not p.is_configured
        assert p.service == ""
        assert p.model == ""

    def test_configured_anthropic(self):
        p = AIProvider({"ai_provider": {"service": "anthropic", "api_key": "sk-test", "model": "claude-haiku-4-5-20251001"}})
        assert p.is_configured
        assert p.service == "anthropic"
        assert p.model == "claude-haiku-4-5-20251001"

    def test_unresolved_env_var_not_configured(self):
        p = AIProvider({"ai_provider": {"service": "anthropic", "api_key": "${ANTHROPIC_API_KEY}"}})
        assert not p.is_configured

    def test_missing_api_key_not_configured(self):
        p = AIProvider({"ai_provider": {"service": "anthropic", "api_key": ""}})
        assert not p.is_configured

    def test_startup_warns_no_service(self, capfd):
        p = AIProvider({})
        p.startup()
        # Should not raise, just log warning

    def test_startup_warns_unresolved_key(self, capfd):
        p = AIProvider({"ai_provider": {"service": "anthropic", "api_key": "${MISSING_KEY}"}})
        p.startup()

    def test_startup_warns_missing_key(self, capfd):
        p = AIProvider({"ai_provider": {"service": "anthropic", "api_key": ""}})
        p.startup()

    def test_startup_unknown_service(self, capfd):
        p = AIProvider({"ai_provider": {"service": "bedrock", "api_key": "key"}})
        p.startup()


class TestAIProviderErrors:
    """Test error paths in complete() and agent_loop()."""

    @pytest.mark.asyncio
    async def test_complete_without_init_raises(self):
        p = AIProvider({})
        with pytest.raises(AIProviderError, match="not initialized"):
            await p.complete("system", "user", {})

    @pytest.mark.asyncio
    async def test_agent_loop_without_init_raises(self):
        p = AIProvider({})
        with pytest.raises(AIProviderError, match="not initialized"):
            await p.agent_loop("system", "user", [], AsyncMock())

    @pytest.mark.asyncio
    async def test_complete_unknown_service_raises(self):
        p = AIProvider({"ai_provider": {"service": "bedrock", "api_key": "key"}})
        p._client = MagicMock()  # bypass startup
        with pytest.raises(AIProviderError, match="Unknown service"):
            await p.complete("system", "user", {})

    @pytest.mark.asyncio
    async def test_agent_loop_unknown_service_raises(self):
        p = AIProvider({"ai_provider": {"service": "bedrock", "api_key": "key"}})
        p._client = MagicMock()
        with pytest.raises(AIProviderError, match="Unknown service"):
            await p.agent_loop("system", "user", [], AsyncMock())


class TestBuildOutputTool:
    """Test build_output_tool() for various field type combinations."""

    def test_string_field(self):
        content_lookup = {"notes": {"singular": "note", "fields": [
            {"name": "body", "column_type": "TEXT"},
        ]}}
        tool = build_output_tool([("note", "body")], content_lookup)
        assert tool["name"] == "set_output"
        props = tool["input_schema"]["properties"]
        # Fix 009.3: thinking not included unless declared
        assert "thinking" not in props
        assert "body" in props
        assert props["body"]["type"] == "string"

    def test_number_field(self):
        content_lookup = {"orders": {"singular": "order", "fields": [
            {"name": "total", "column_type": "REAL"},
        ]}}
        tool = build_output_tool([("order", "total")], content_lookup)
        assert tool["input_schema"]["properties"]["total"]["type"] == "number"

    def test_integer_field(self):
        content_lookup = {"items": {"singular": "item", "fields": [
            {"name": "count", "column_type": "INTEGER"},
        ]}}
        tool = build_output_tool([("item", "count")], content_lookup)
        assert tool["input_schema"]["properties"]["count"]["type"] == "number"

    def test_boolean_field(self):
        content_lookup = {"items": {"singular": "item", "fields": [
            {"name": "active", "column_type": "BOOLEAN"},
        ]}}
        tool = build_output_tool([("item", "active")], content_lookup)
        assert tool["input_schema"]["properties"]["active"]["type"] == "boolean"

    def test_enum_field(self):
        content_lookup = {"items": {"singular": "item", "fields": [
            {"name": "status", "column_type": "TEXT", "enum_values": ["open", "closed"]},
        ]}}
        tool = build_output_tool([("item", "status")], content_lookup)
        props = tool["input_schema"]["properties"]["status"]
        assert props["enum"] == ["open", "closed"]

    def test_unknown_content_falls_back_to_string(self):
        tool = build_output_tool([("nonexistent", "field")], {})
        assert tool["input_schema"]["properties"]["field"]["type"] == "string"

    def test_unknown_field_falls_back_to_string(self):
        content_lookup = {"items": {"singular": "item", "fields": []}}
        tool = build_output_tool([("item", "missing")], content_lookup)
        assert tool["input_schema"]["properties"]["missing"]["type"] == "string"

    def test_required_includes_all_fields(self):
        content_lookup = {"items": {"singular": "item", "fields": [
            {"name": "a", "column_type": "TEXT"},
            {"name": "b", "column_type": "INTEGER"},
        ]}}
        tool = build_output_tool([("item", "a"), ("item", "b")], content_lookup)
        # Fix 009.3: thinking not included unless declared
        assert set(tool["input_schema"]["required"]) == {"a", "b"}


class TestBuildAgentTools:
    """Test build_agent_tools() ComputeContext tool schema generation."""

    def test_produces_four_tools(self):
        tools = build_agent_tools(["orders", "items"], {})
        names = [t["name"] for t in tools]
        assert "content_query" in names
        assert "content_create" in names
        assert "content_update" in names
        assert "state_transition" in names

    def test_accesses_in_enum(self):
        tools = build_agent_tools(["orders", "items"], {})
        query_tool = next(t for t in tools if t["name"] == "content_query")
        assert query_tool["input_schema"]["properties"]["content_name"]["enum"] == ["orders", "items"]

    def test_empty_accesses(self):
        tools = build_agent_tools([], {})
        assert len(tools) == 4
        query_tool = next(t for t in tools if t["name"] == "content_query")
        assert query_tool["input_schema"]["properties"]["content_name"]["enum"] == []


# ═══════════════════════════════════════════════════════════════
# transaction.py
# ═══════════════════════════════════════════════════════════════

from termin_runtime.transaction import Transaction, ContentSnapshot, StagedWrite


class TestContentSnapshot:
    """Test ContentSnapshot for postcondition evaluation."""

    def test_content_query_returns_copy(self):
        snap = ContentSnapshot({"orders": [{"id": 1}, {"id": 2}]})
        result = snap.content_query("orders")
        assert len(result) == 2
        result.append({"id": 3})  # mutating copy
        assert len(snap.content_query("orders")) == 2  # original unchanged

    def test_content_query_missing_returns_empty(self):
        snap = ContentSnapshot({})
        assert snap.content_query("nonexistent") == []

    def test_result_property(self):
        snap = ContentSnapshot({}, result=42)
        assert snap.result == 42

    def test_result_default_none(self):
        snap = ContentSnapshot({})
        assert snap.result is None

    def test_attribute_access(self):
        snap = ContentSnapshot({"findings": [{"id": 1}]})
        assert snap.findings == [{"id": 1}]

    def test_attribute_access_missing_raises(self):
        snap = ContentSnapshot({})
        with pytest.raises(AttributeError, match="no content type"):
            _ = snap.nonexistent

    def test_private_attr_raises_normally(self):
        snap = ContentSnapshot({})
        with pytest.raises(AttributeError):
            _ = snap._internal

    def test_dict_access(self):
        snap = ContentSnapshot({"items": [{"id": 1}]})
        assert snap["items"] == [{"id": 1}]

    def test_dict_access_result(self):
        snap = ContentSnapshot({}, result="done")
        assert snap["result"] == "done"

    def test_dict_access_missing_raises(self):
        snap = ContentSnapshot({})
        with pytest.raises(KeyError):
            _ = snap["missing"]


class TestStagedWrite:
    """Test StagedWrite data class."""

    def test_slots(self):
        sw = StagedWrite("items", 1, {"name": "test"}, "create", 1)
        assert sw.content_name == "items"
        assert sw.record_id == 1
        assert sw.data == {"name": "test"}
        assert sw.operation == "create"
        assert sw.sequence == 1


class TestTransaction:
    """Test Transaction staging, commit, and rollback."""

    def test_new_transaction_is_active(self):
        tx = Transaction()
        assert tx.is_active
        assert tx.write_count == 0

    def test_write_stages_data(self):
        tx = Transaction()
        tx.write("items", 1, {"name": "test"})
        assert tx.write_count == 1

    def test_write_after_commit_raises(self):
        tx = Transaction()
        tx._committed = True
        with pytest.raises(RuntimeError, match="no longer active"):
            tx.write("items", 1, {"name": "test"})

    def test_write_after_rollback_raises(self):
        tx = Transaction()
        tx.rollback()
        with pytest.raises(RuntimeError, match="no longer active"):
            tx.write("items", 1, {"name": "test"})

    @pytest.mark.asyncio
    async def test_read_staged_value(self):
        tx = Transaction()
        tx.write("items", 1, {"name": "staged"})
        result = await tx.read("items", 1)
        assert result == {"name": "staged"}

    @pytest.mark.asyncio
    async def test_read_deleted_returns_none(self):
        tx = Transaction()
        tx.write("items", 1, {"name": "test"}, operation="delete")
        result = await tx.read("items", 1)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_fallthrough_to_storage(self):
        async def storage_read(cn, rid):
            return {"name": "production"}
        tx = Transaction(storage_read_fn=storage_read)
        result = await tx.read("items", 1)
        assert result == {"name": "production"}

    @pytest.mark.asyncio
    async def test_read_no_storage_returns_none(self):
        tx = Transaction()
        result = await tx.read("items", 999)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_all_merges_staged(self):
        tx = Transaction()
        tx.write("items", 1, {"id": 1, "name": "updated"})
        tx.write("items", 99, {"id": 99, "name": "created"}, operation="create")
        prod_records = [{"id": 1, "name": "original"}, {"id": 2, "name": "untouched"}]
        result = await tx.read_all("items", prod_records)
        names = {r["name"] for r in result}
        assert "updated" in names
        assert "untouched" in names
        assert "created" in names
        assert "original" not in names

    @pytest.mark.asyncio
    async def test_read_all_removes_deleted(self):
        tx = Transaction()
        tx.write("items", 1, {}, operation="delete")
        prod_records = [{"id": 1, "name": "doomed"}, {"id": 2, "name": "safe"}]
        result = await tx.read_all("items", prod_records)
        assert len(result) == 1
        assert result[0]["name"] == "safe"

    def test_get_snapshot(self):
        tx = Transaction()
        tx.write("items", 1, {"name": "a"})
        tx.write("orders", 2, {"total": 100})
        snap = tx.get_snapshot()
        assert "items" in snap
        assert "orders" in snap

    def test_get_snapshot_filtered(self):
        tx = Transaction()
        tx.write("items", 1, {"name": "a"})
        tx.write("orders", 2, {"total": 100})
        snap = tx.get_snapshot(content_name="items")
        assert "items" in snap
        assert "orders" not in snap

    @pytest.mark.asyncio
    async def test_commit_calls_storage_write(self):
        tx = Transaction()
        tx.write("items", 1, {"name": "a"}, operation="create")
        tx.write("items", 2, {"name": "b"}, operation="update")

        writes = []
        async def mock_write(db, cn, rid, data, op):
            writes.append((cn, rid, op))
        await tx.commit(None, mock_write)
        assert len(writes) == 2
        assert writes[0] == ("items", 1, "create")
        assert writes[1] == ("items", 2, "update")
        assert tx._committed

    @pytest.mark.asyncio
    async def test_commit_when_not_active_raises(self):
        tx = Transaction()
        tx.rollback()
        with pytest.raises(RuntimeError, match="no longer active"):
            await tx.commit(None, AsyncMock())

    def test_rollback_clears_state(self):
        tx = Transaction()
        tx.write("items", 1, {"name": "a"})
        tx.write("items", 2, {}, operation="delete")
        tx.rollback()
        assert tx.write_count == 0
        assert not tx.is_active

    def test_journal_format(self):
        tx = Transaction()
        tx.write("items", 1, {"name": "a"}, operation="create")
        tx.write("items", 1, {"name": "b"}, operation="update")
        j = tx.journal
        assert len(j) == 2
        assert j[0]["content"] == "items"
        assert j[0]["operation"] == "create"
        assert j[0]["sequence"] == 1
        assert j[1]["sequence"] == 2

    def test_delete_then_create_restores(self):
        tx = Transaction()
        tx.write("items", 1, {}, operation="delete")
        tx.write("items", 1, {"name": "revived"}, operation="create")
        assert ("items", 1) in tx._staging
        assert ("items", 1) not in tx._deleted

    def test_transaction_id_is_uuid(self):
        tx = Transaction()
        assert len(tx.id) == 36  # UUID format


# ═══════════════════════════════════════════════════════════════
# expression.py
# ═══════════════════════════════════════════════════════════════

from termin_runtime.expression import (
    ExpressionEvaluator, _cel_to_python, SYSTEM_FUNCTIONS,
    _cel_sum, _cel_avg, _cel_min, _cel_max, _cel_flatten, _cel_unique,
    _cel_first, _cel_last, _cel_sort, _cel_days_between, _cel_days_until,
    _cel_add_days, _cel_upper, _cel_lower, _cel_trim, _cel_replace,
    _cel_round, _cel_floor, _cel_ceil, _cel_abs, _cel_clamp,
)
from celpy.celtypes import StringType, DoubleType, IntType, BoolType, ListType


class TestCELSystemFunctions:
    """Test system function implementations directly."""

    def test_sum_integers(self):
        assert float(_cel_sum([IntType(1), IntType(2), IntType(3)])) == 6.0

    def test_avg_values(self):
        assert float(_cel_avg([DoubleType(10), DoubleType(20)])) == 15.0

    def test_avg_empty(self):
        assert float(_cel_avg([])) == 0.0

    def test_min_values(self):
        assert float(_cel_min([DoubleType(5), DoubleType(3), DoubleType(8)])) == 3.0

    def test_min_empty(self):
        assert int(_cel_min([])) == 0

    def test_max_values(self):
        assert float(_cel_max([DoubleType(5), DoubleType(3), DoubleType(8)])) == 8.0

    def test_max_empty(self):
        assert int(_cel_max([])) == 0

    def test_flatten_nested(self):
        result = _cel_flatten(ListType([ListType([IntType(1), IntType(2)]), IntType(3)]))
        assert len(result) == 3

    def test_flatten_with_strings(self):
        result = _cel_flatten(ListType([StringType("a"), StringType("b")]))
        assert len(result) == 2

    def test_unique_removes_duplicates(self):
        result = _cel_unique(ListType([IntType(1), IntType(2), IntType(1)]))
        assert len(result) == 2

    def test_first_returns_first(self):
        assert int(_cel_first(ListType([IntType(10), IntType(20)]))) == 10

    def test_first_empty_returns_false(self):
        result = _cel_first(ListType([]))
        assert not bool(result)

    def test_last_returns_last(self):
        assert int(_cel_last(ListType([IntType(10), IntType(20)]))) == 20

    def test_last_empty_returns_false(self):
        result = _cel_last(ListType([]))
        assert not bool(result)

    def test_sort(self):
        result = _cel_sort(ListType([IntType(3), IntType(1), IntType(2)]))
        assert [int(x) for x in result] == [1, 2, 3]

    def test_days_between(self):
        result = _cel_days_between(StringType("2024-01-01"), StringType("2024-01-10"))
        assert int(result) == 9

    def test_days_between_invalid(self):
        result = _cel_days_between(StringType("not-a-date"), StringType("2024-01-10"))
        assert int(result) == 0

    def test_days_until_invalid(self):
        result = _cel_days_until(StringType("not-a-date"))
        assert int(result) == 0

    def test_add_days(self):
        result = _cel_add_days(StringType("2024-01-01"), IntType(5))
        assert str(result) == "2024-01-06"

    def test_add_days_invalid(self):
        result = _cel_add_days(StringType("bad"), IntType(5))
        assert str(result) == "bad"

    def test_upper(self):
        assert str(_cel_upper(StringType("hello"))) == "HELLO"

    def test_upper_none(self):
        assert str(_cel_upper(None)) == ""

    def test_lower(self):
        assert str(_cel_lower(StringType("HELLO"))) == "hello"

    def test_lower_none(self):
        assert str(_cel_lower(None)) == ""

    def test_trim(self):
        assert str(_cel_trim(StringType("  hello  "))) == "hello"

    def test_trim_none(self):
        assert str(_cel_trim(None)) == ""

    def test_replace(self):
        assert str(_cel_replace(StringType("hello world"), StringType("world"), StringType("there"))) == "hello there"

    def test_replace_none(self):
        assert str(_cel_replace(None, StringType("a"), StringType("b"))) == ""

    def test_round_default(self):
        assert float(_cel_round(DoubleType(3.7))) == 4.0

    def test_round_decimals(self):
        assert float(_cel_round(DoubleType(3.456), IntType(2))) == 3.46

    def test_round_none(self):
        assert float(_cel_round(None)) == 0.0

    def test_floor(self):
        assert int(_cel_floor(DoubleType(3.7))) == 3

    def test_floor_none(self):
        assert int(_cel_floor(None)) == 0

    def test_ceil(self):
        assert int(_cel_ceil(DoubleType(3.2))) == 4

    def test_ceil_none(self):
        assert int(_cel_ceil(None)) == 0

    def test_abs_positive(self):
        assert float(_cel_abs(DoubleType(-5.0))) == 5.0

    def test_abs_none(self):
        assert float(_cel_abs(None)) == 0.0

    def test_clamp_within_range(self):
        assert float(_cel_clamp(DoubleType(5), DoubleType(0), DoubleType(10))) == 5.0

    def test_clamp_below(self):
        assert float(_cel_clamp(DoubleType(-5), DoubleType(0), DoubleType(10))) == 0.0

    def test_clamp_above(self):
        assert float(_cel_clamp(DoubleType(15), DoubleType(0), DoubleType(10))) == 10.0


class TestCelToPython:
    """Test CEL type to Python type conversion."""

    def test_bool(self):
        assert _cel_to_python(BoolType(True)) is True

    def test_int(self):
        assert _cel_to_python(IntType(42)) == 42

    def test_double(self):
        assert _cel_to_python(DoubleType(3.14)) == pytest.approx(3.14)

    def test_string(self):
        assert _cel_to_python(StringType("hello")) == "hello"

    def test_list(self):
        result = _cel_to_python(ListType([IntType(1), StringType("a")]))
        assert result == [1, "a"]

    def test_passthrough_unknown(self):
        obj = {"raw": True}
        assert _cel_to_python(obj) == {"raw": True}


class TestExpressionEvaluator:
    """Test ExpressionEvaluator end-to-end."""

    def test_simple_expression(self):
        ev = ExpressionEvaluator()
        assert ev.evaluate("1 + 2") == 3

    def test_string_concatenation(self):
        ev = ExpressionEvaluator()
        result = ev.evaluate('"hello" + " " + "world"')
        assert result == "hello world"

    def test_context_variable(self):
        ev = ExpressionEvaluator()
        result = ev.evaluate("x + y", {"x": 10, "y": 20})
        assert result == 30

    def test_boolean_expression(self):
        ev = ExpressionEvaluator()
        assert ev.evaluate("true && false") is False

    def test_system_function_upper(self):
        ev = ExpressionEvaluator()
        result = ev.evaluate('upper("hello")')
        assert result == "HELLO"

    def test_custom_function(self):
        ev = ExpressionEvaluator()
        ev.register_function("double", lambda x: IntType(int(x) * 2))
        result = ev.evaluate("double(5)")
        assert result == 10

    def test_bad_expression_raises(self):
        ev = ExpressionEvaluator()
        with pytest.raises(Exception):
            ev.evaluate("invalid !!!! syntax")

    def test_dynamic_context_has_now(self):
        ev = ExpressionEvaluator()
        result = ev.evaluate("now")
        assert isinstance(result, str)
        assert "T" in result  # ISO format

    def test_dynamic_context_has_today(self):
        ev = ExpressionEvaluator()
        result = ev.evaluate("today")
        assert isinstance(result, str)
        assert "-" in result  # ISO date format


# ═══════════════════════════════════════════════════════════════
# reflection.py
# ═══════════════════════════════════════════════════════════════

from termin_runtime.reflection import ReflectionEngine


class TestReflectionEngine:
    """Test ReflectionEngine query paths."""

    @pytest.fixture
    def ir_spec(self):
        return {
            "content": [
                {
                    "name": {"display": "orders", "snake": "orders"},
                    "singular": "order",
                    "fields": [
                        {"display_name": "total", "column_type": "REAL", "required": True, "unique": False},
                        {"display_name": "status", "column_type": "TEXT", "required": False, "unique": False},
                    ],
                },
                {
                    "name": {"display": "items", "snake": "items"},
                    "singular": "item",
                    "fields": [
                        {"display_name": "name", "column_type": "TEXT", "required": True, "unique": True},
                    ],
                },
            ],
            "computes": [
                {
                    "name": {"display": "calculate total", "snake": "calculate_total"},
                    "shape": "TRANSFORM",
                    "input_params": [{"name": "order"}],
                    "output_params": [{"name": "total"}],
                },
            ],
            "channels": [
                {"name": {"display": "webhook", "snake": "webhook"}},
                {"name": {"display": "stream", "snake": "stream"}},
            ],
            "boundaries": [
                {"name": {"display": "core", "snake": "core"}, "contains": ["orders", "items"]},
            ],
            "auth": {
                "roles": [
                    {"name": "admin", "scopes": ["read", "write"]},
                    {"name": "viewer", "scopes": ["read"]},
                ],
            },
        }

    @pytest.fixture
    def engine(self, ir_spec):
        return ReflectionEngine(ir_spec)

    def test_content_schemas(self, engine):
        schemas = engine.content_schemas()
        assert "orders" in schemas
        assert "items" in schemas

    def test_content_schema_by_display_name(self, engine):
        schema = engine.content_schema("orders")
        assert schema is not None
        assert "total" in schema["fields"]
        assert schema["field_details"]["total"]["type"] == "REAL"
        assert "required" in schema["field_details"]["total"]["constraints"]

    def test_content_schema_by_snake_name(self, engine):
        schema = engine.content_schema("orders")
        assert schema is not None

    def test_content_schema_missing(self, engine):
        assert engine.content_schema("nonexistent") is None

    def test_content_count(self, engine):
        result = engine.content_count("orders", None)
        assert result == "orders"

    def test_content_count_missing(self, engine):
        assert engine.content_count("nonexistent", None) is None

    def test_compute_functions(self, engine):
        funcs = engine.compute_functions()
        assert "calculate total" in funcs

    def test_compute_function_by_display(self, engine):
        func = engine.compute_function("calculate total")
        assert func is not None
        assert func["shape"] == "TRANSFORM"

    def test_compute_function_by_snake(self, engine):
        func = engine.compute_function("calculate_total")
        assert func is not None

    def test_compute_function_missing(self, engine):
        assert engine.compute_function("nonexistent") is None

    def test_channels(self, engine):
        chs = engine.channels()
        assert "webhook" in chs
        assert "stream" in chs

    def test_channel_state_default(self, engine):
        assert engine.channel_state("webhook") == "open"

    def test_channel_metrics_default(self, engine):
        metrics = engine.channel_metrics("webhook")
        assert metrics["sent"] == 0
        assert metrics["errors"] == 0

    def test_update_channel_metric(self, engine):
        engine.update_channel_metric("webhook", "sent", 5)
        assert engine.channel_metrics("webhook")["sent"] == 5

    def test_update_channel_metric_creates_entry(self, engine):
        engine.update_channel_metric("new_channel", "errors", 3)
        metrics = engine.channel_metrics("new_channel")
        assert metrics["errors"] == 3
        assert metrics["state"] == "open"

    def test_identity_context(self, engine):
        ctx = engine.identity_context({"role": "admin", "scopes": ["read"]})
        assert ctx["role"] == "admin"
        assert ctx["scopes"] == ["read"]
        assert ctx["isAnonymous"] is False

    def test_identity_context_anonymous(self, engine):
        ctx = engine.identity_context({})
        assert ctx["role"] == "anonymous"
        assert ctx["isAnonymous"] is True

    def test_roles(self, engine):
        roles = engine.roles()
        assert "admin" in roles
        assert "viewer" in roles

    def test_role_found(self, engine):
        role = engine.role("admin")
        assert role is not None
        assert role["Name"] == "admin"
        assert "read" in role["Scopes"]

    def test_role_case_insensitive(self, engine):
        role = engine.role("ADMIN")
        assert role is not None

    def test_role_missing(self, engine):
        assert engine.role("nonexistent") is None

    def test_boundaries(self, engine):
        bs = engine.boundaries()
        assert "core" in bs

    def test_boundary_info(self, engine):
        info = engine.boundary_info("core")
        assert info is not None

    def test_boundary_info_missing(self, engine):
        assert engine.boundary_info("nonexistent") is None

    def test_init_from_json_string(self, ir_spec):
        engine = ReflectionEngine(json.dumps(ir_spec))
        assert "orders" in engine.content_schemas()


# ═══════════════════════════════════════════════════════════════
# errors.py
# ═══════════════════════════════════════════════════════════════

from termin_runtime.errors import TerminError, TerminAtor


class TestTerminError:
    """Test TerminError data model."""

    def test_basic_error(self):
        err = TerminError("products", "validation", "Name is required")
        assert err.source == "products"
        assert err.kind == "validation"
        assert err.message == "Name is required"
        assert err.timestamp  # non-empty
        assert err.context == ""
        assert err.boundary_path == []

    def test_error_with_context(self):
        err = TerminError("products", "authorization", "Forbidden", context="user=bob", boundary_path=["app", "core"])
        assert err.context == "user=bob"
        assert err.boundary_path == ["app", "core"]


class TestTerminAtor:
    """Test TerminAtor error router."""

    def test_route_logs_error(self):
        router = TerminAtor()
        err = TerminError("products", "validation", "Name is required")
        router.route(err)
        log = router.get_error_log()
        assert len(log) == 1
        assert log[0]["source"] == "products"

    def test_route_to_boundary_handler(self):
        router = TerminAtor()
        calls = []
        router.register_handler("core", lambda e: calls.append(e.source))
        err = TerminError("products", "validation", "bad", boundary_path=["core"])
        router.route(err)
        assert calls == ["products"]

    def test_route_falls_through_to_global(self, capsys):
        router = TerminAtor()
        err = TerminError("products", "validation", "bad")
        router.route(err)
        out = capsys.readouterr().out
        assert "[TerminAtor]" in out

    def test_typed_handler_source_match(self, capsys):
        router = TerminAtor()
        router.register_handler("spec", {
            "source": "products",
            "actions": [{"kind": "retry", "retry_count": 3}],
        })
        err = TerminError("products", "timeout", "timed out")
        router.route(err)
        out = capsys.readouterr().out
        assert "Retry 3" in out

    def test_typed_handler_source_no_match(self, capsys):
        router = TerminAtor()
        router.register_handler("spec", {
            "source": "orders",
            "actions": [{"kind": "retry", "retry_count": 3}],
        })
        err = TerminError("products", "timeout", "timed out")
        router.route(err)
        # Should fall through to global
        out = capsys.readouterr().out
        assert "timeout" in out

    def test_typed_handler_catch_all(self, capsys):
        router = TerminAtor()
        router.register_handler("spec", {
            "is_catch_all": True,
            "actions": [{"kind": "notify", "target": "admin"}],
        })
        err = TerminError("anything", "any", "any message")
        router.route(err)
        out = capsys.readouterr().out
        assert "Notifying admin" in out

    def test_typed_handler_with_condition(self):
        mock_eval = MagicMock()
        mock_eval.evaluate.return_value = True
        router = TerminAtor(expr_eval=mock_eval)
        router.register_handler("spec", {
            "source": "products",
            "condition": 'error.kind == "timeout"',
            "actions": [{"kind": "retry", "retry_count": 2}],
        })
        err = TerminError("products", "timeout", "slow")
        router.route(err)
        mock_eval.evaluate.assert_called_once()

    def test_typed_handler_condition_false_skips(self, capsys):
        mock_eval = MagicMock()
        mock_eval.evaluate.return_value = False
        router = TerminAtor(expr_eval=mock_eval)
        router.register_handler("spec", {
            "source": "products",
            "condition": 'error.kind == "timeout"',
            "actions": [{"kind": "retry", "retry_count": 2}],
        })
        err = TerminError("products", "validation", "bad")
        router.route(err)
        # Falls through to global
        out = capsys.readouterr().out
        assert "[TerminAtor]" in out

    def test_typed_handler_condition_exception_skips(self, capsys):
        mock_eval = MagicMock()
        mock_eval.evaluate.side_effect = Exception("eval failed")
        router = TerminAtor(expr_eval=mock_eval)
        router.register_handler("spec", {
            "source": "products",
            "condition": "bad_expr",
            "actions": [{"kind": "retry", "retry_count": 2}],
        })
        err = TerminError("products", "validation", "bad")
        router.route(err)
        # Falls through to global
        out = capsys.readouterr().out
        assert "[TerminAtor]" in out

    def test_typed_handler_no_expr_eval_condition_false(self, capsys):
        router = TerminAtor()  # no expr_eval
        router.register_handler("spec", {
            "source": "products",
            "condition": 'error.kind == "timeout"',
            "actions": [{"kind": "retry", "retry_count": 2}],
        })
        err = TerminError("products", "timeout", "slow")
        router.route(err)
        # Condition evaluates to False without expr_eval
        out = capsys.readouterr().out
        assert "[TerminAtor]" in out

    def test_set_expr_eval(self):
        router = TerminAtor()
        mock_eval = MagicMock()
        router.set_expr_eval(mock_eval)
        assert router._expr_eval == mock_eval

    def test_action_kinds(self, capsys):
        """Test all action kinds produce output."""
        router = TerminAtor()
        router.register_handler("spec", {
            "is_catch_all": True,
            "actions": [
                {"kind": "retry", "retry_count": 1},
                {"kind": "disable", "target": "service"},
                {"kind": "create", "target": "alert"},
                {"kind": "notify", "target": "admin"},
                {"kind": "set", "expr": "status = 'down'"},
            ],
        })
        err = TerminError("svc", "timeout", "slow")
        router.route(err)
        out = capsys.readouterr().out
        assert "Retry 1" in out
        assert "Disabling service" in out
        assert "Creating alert" in out
        assert "Notifying admin" in out
        assert "Setting status" in out

    def test_escalate_returns_false(self, capsys):
        router = TerminAtor()
        router.register_handler("spec", {
            "is_catch_all": True,
            "actions": [{"kind": "escalate"}],
        })
        err = TerminError("svc", "critical", "crash")
        # Escalate returns False from handle_error, so route continues to global
        router.route(err)
        out = capsys.readouterr().out
        assert "Escalating" in out

    def test_action_with_log_level(self, capsys):
        router = TerminAtor()
        router.register_handler("spec", {
            "is_catch_all": True,
            "actions": [{"kind": "retry", "retry_count": 1, "log_level": "ERROR"}],
        })
        err = TerminError("svc", "timeout", "slow message")
        router.route(err)
        out = capsys.readouterr().out
        assert "[ERROR]" in out
        assert "slow message" in out

    def test_get_typed_handlers(self):
        router = TerminAtor()
        spec = {"source": "test", "actions": []}
        router.register_handler("spec", spec)
        assert router.get_typed_handlers() == [spec]

    def test_register_callable_handler(self):
        router = TerminAtor()
        fn = lambda e: None
        router.register_handler("boundary", fn)
        assert router._handlers["boundary"] == fn

    def test_register_non_callable_non_dict(self):
        router = TerminAtor()
        router.register_handler("boundary", "some_value")
        assert router._handlers["boundary"] == "some_value"


# ═══════════════════════════════════════════════════════════════
# channels.py — config/validation functions (non-WebSocket)
# ═══════════════════════════════════════════════════════════════

from termin_runtime.channels import (
    _resolve_env_vars, _resolve_config_env, load_deploy_config,
    check_deploy_config_warnings, validate_channel_config,
    _check_unresolved_vars, ChannelError,
)


class TestResolveEnvVars:
    """Test environment variable resolution in config."""

    def test_resolve_set_var(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "resolved")
        assert _resolve_env_vars("prefix-${MY_VAR}-suffix") == "prefix-resolved-suffix"

    def test_unresolved_var_kept(self):
        result = _resolve_env_vars("${MISSING_VAR_12345}")
        assert "${MISSING_VAR_12345}" in result

    def test_non_string_passthrough(self):
        assert _resolve_env_vars(42) == 42

    def test_resolve_config_recursively(self, monkeypatch):
        monkeypatch.setenv("TOKEN", "secret")
        config = {
            "url": "https://api.example.com",
            "auth": {"token": "${TOKEN}"},
            "tags": ["${TOKEN}", "literal"],
        }
        resolved = _resolve_config_env(config)
        assert resolved["auth"]["token"] == "secret"
        assert resolved["tags"][0] == "secret"


class TestCheckUnresolvedVars:
    """Test _check_unresolved_vars for finding missing env vars."""

    def test_finds_unresolved_in_string(self):
        warnings = []
        _check_unresolved_vars("${MISSING_VAR}", "test.path", warnings)
        assert len(warnings) == 1
        assert "MISSING_VAR" in warnings[0]

    def test_finds_unresolved_in_nested_dict(self):
        warnings = []
        _check_unresolved_vars({"key": "${MISSING}"}, "root", warnings)
        assert len(warnings) == 1

    def test_finds_unresolved_in_list(self):
        warnings = []
        _check_unresolved_vars(["${MISSING}"], "root", warnings)
        assert len(warnings) == 1

    def test_no_warnings_for_resolved(self, monkeypatch):
        monkeypatch.setenv("FOUND", "val")
        warnings = []
        _check_unresolved_vars("resolved_string", "root", warnings)
        assert len(warnings) == 0


class TestLoadDeployConfig:
    """Test deploy config loading."""

    def test_missing_file_returns_empty(self):
        result = load_deploy_config(path="/nonexistent/path/config.json")
        assert result == {}

    def test_no_candidates_returns_empty(self):
        result = load_deploy_config()
        assert result == {}


class TestCheckDeployConfigWarnings:
    """Test deploy config warning detection."""

    def test_placeholder_url_warns(self):
        ir = {"channels": [{"name": {"display": "hook", "snake": "hook"}, "direction": "OUTBOUND"}]}
        config = {"channels": {"hook": {"url": "https://TODO-configure-hook.example.com/api"}}}
        warnings = check_deploy_config_warnings(config, ir)
        assert any("placeholder" in w.lower() for w in warnings)

    def test_internal_channel_no_warning(self):
        ir = {"channels": [{"name": {"display": "bus", "snake": "bus"}, "direction": "INTERNAL"}]}
        config = {}
        warnings = check_deploy_config_warnings(config, ir)
        assert len(warnings) == 0


class TestValidateChannelConfig:
    """Test channel config validation."""

    def test_missing_config_for_outbound(self):
        ir = {"channels": [{"name": {"display": "hook", "snake": "hook"}, "direction": "OUTBOUND"}]}
        errors = validate_channel_config(ir, {})
        assert len(errors) > 0

    def test_internal_needs_no_config(self):
        ir = {"channels": [{"name": {"display": "bus", "snake": "bus"}, "direction": "INTERNAL"}]}
        errors = validate_channel_config(ir, {})
        assert len(errors) == 0

    def test_config_with_no_url_errors(self):
        ir = {"channels": [{"name": {"display": "hook", "snake": "hook"}, "direction": "OUTBOUND"}]}
        errors = validate_channel_config(ir, {"channels": {"hook": {"protocol": "http"}}})
        assert any("no 'url'" in e for e in errors)


# ═══════════════════════════════════════════════════════════════
# channels.py — config types and dispatcher
# ═══════════════════════════════════════════════════════════════

from termin_runtime.channels import (
    ChannelAuthConfig, ChannelConfig, ChannelConfigError,
    ChannelDispatcher, ChannelScopeError, WebSocketConnection,
)


class TestChannelAuthConfig:
    """Test ChannelAuthConfig data class."""

    def test_defaults(self):
        cfg = ChannelAuthConfig()
        assert cfg.auth_type == "none"
        assert cfg.token == ""
        assert cfg.header == "Authorization"

    def test_from_dict_bearer(self):
        cfg = ChannelAuthConfig.from_dict({"type": "bearer", "token": "sk-test"})
        assert cfg.auth_type == "bearer"
        assert cfg.token == "sk-test"

    def test_from_dict_api_key(self):
        cfg = ChannelAuthConfig.from_dict({"type": "api_key", "token": "key123", "header": "X-API-Key"})
        assert cfg.auth_type == "api_key"
        assert cfg.header == "X-API-Key"

    def test_from_dict_extras(self):
        cfg = ChannelAuthConfig.from_dict({"type": "hmac", "secret": "s3cr3t", "algorithm": "sha256"})
        assert cfg.secret == "s3cr3t"
        assert cfg.extras.get("algorithm") == "sha256"


class TestChannelConfig:
    """Test ChannelConfig data class."""

    def test_defaults(self):
        cfg = ChannelConfig()
        assert cfg.url == ""
        assert cfg.protocol == "http"
        assert cfg.timeout_ms == 30000
        assert cfg.max_retries == 3
        assert cfg.reconnect is True

    def test_from_dict(self):
        cfg = ChannelConfig.from_dict({
            "url": "https://api.example.com/hook",
            "protocol": "websocket",
            "timeout_ms": 5000,
            "retry": {"max_attempts": 5, "backoff_ms": 2000},
            "reconnect": False,
        })
        assert cfg.url == "https://api.example.com/hook"
        assert cfg.protocol == "websocket"
        assert cfg.max_retries == 5
        assert cfg.backoff_ms == 2000
        assert cfg.reconnect is False


class TestWebSocketConnection:
    """Test WebSocket connection state management (no actual WS)."""

    def test_initial_state(self):
        cfg = ChannelConfig(url="wss://example.com")
        ws = WebSocketConnection("test", cfg)
        assert ws.state == "disconnected"

    @pytest.mark.asyncio
    async def test_send_not_connected_raises(self):
        cfg = ChannelConfig(url="wss://example.com")
        ws = WebSocketConnection("test", cfg)
        with pytest.raises(ChannelError, match="not connected"):
            await ws.send({"data": 1})

    @pytest.mark.asyncio
    async def test_invoke_not_connected_raises(self):
        cfg = ChannelConfig(url="wss://example.com")
        ws = WebSocketConnection("test", cfg)
        with pytest.raises(ChannelError, match="not connected"):
            await ws.invoke("action", {"param": 1})

    @pytest.mark.asyncio
    async def test_close_when_not_connected(self):
        cfg = ChannelConfig(url="wss://example.com")
        ws = WebSocketConnection("test", cfg)
        await ws.close()  # should not raise
        assert ws.state == "disconnected"


class TestChannelDispatcher:
    """Test ChannelDispatcher initialization and methods."""

    def _make_ir(self, channels=None):
        return {
            "channels": channels or [],
            "auth": {"roles": [{"name": "admin", "scopes": ["send.scope"]}]},
        }

    def test_empty_channels(self):
        d = ChannelDispatcher(self._make_ir())
        assert d.validate() == []

    def test_get_spec_by_display(self):
        ir = self._make_ir([{"name": {"display": "hook", "snake": "hook"}, "direction": "OUTBOUND"}])
        d = ChannelDispatcher(ir)
        assert d.get_spec("hook") is not None

    def test_get_spec_missing(self):
        d = ChannelDispatcher(self._make_ir())
        assert d.get_spec("nonexistent") is None

    def test_get_config_not_found(self):
        d = ChannelDispatcher(self._make_ir())
        assert d.get_config("nonexistent") is None

    def test_metrics_initialized(self):
        ir = self._make_ir([{"name": {"display": "hook", "snake": "hook"}, "direction": "OUTBOUND"}])
        d = ChannelDispatcher(ir)
        assert d._metrics["hook"]["sent"] == 0

    def test_build_headers_bearer(self):
        ir = self._make_ir([{"name": {"display": "hook", "snake": "hook"}, "direction": "OUTBOUND"}])
        deploy = {"channels": {"hook": {"url": "https://api.example.com", "auth": {"type": "bearer", "token": "tk"}}}}
        d = ChannelDispatcher(ir, deploy)
        config = d.get_config("hook")
        headers = d._build_headers(config)
        assert headers["Authorization"] == "Bearer tk"

    def test_build_headers_api_key(self):
        ir = self._make_ir([{"name": {"display": "hook", "snake": "hook"}, "direction": "OUTBOUND"}])
        deploy = {"channels": {"hook": {"url": "https://api.example.com", "auth": {"type": "api_key", "token": "key", "header": "X-Key"}}}}
        d = ChannelDispatcher(ir, deploy)
        config = d.get_config("hook")
        headers = d._build_headers(config)
        assert headers["X-Key"] == "key"

    def test_build_headers_none_auth_uses_cookie(self):
        ir = self._make_ir([{"name": {"display": "hook", "snake": "hook"}, "direction": "OUTBOUND"}])
        deploy = {"channels": {"hook": {"url": "http://localhost:8000/webhook", "auth": {"type": "none"}}}}
        d = ChannelDispatcher(ir, deploy)
        config = d.get_config("hook")
        headers = d._build_headers(config)
        assert "termin_role=admin" in headers.get("Cookie", "")

    def test_check_scope_no_spec(self):
        d = ChannelDispatcher(self._make_ir())
        assert d._check_scope("nonexistent", "send", {"scope"}) is False

    def test_check_scope_pass(self):
        ir = self._make_ir([{
            "name": {"display": "hook", "snake": "hook"},
            "direction": "OUTBOUND",
            "requirements": [{"direction": "send", "scope": "s"}],
        }])
        d = ChannelDispatcher(ir)
        assert d._check_scope("hook", "send", {"s"}) is True

    def test_check_scope_fail(self):
        ir = self._make_ir([{
            "name": {"display": "hook", "snake": "hook"},
            "direction": "OUTBOUND",
            "requirements": [{"direction": "send", "scope": "s"}],
        }])
        d = ChannelDispatcher(ir)
        assert d._check_scope("hook", "send", {"other"}) is False

    def test_check_action_scope_no_spec(self):
        d = ChannelDispatcher(self._make_ir())
        assert d._check_action_scope("nonexistent", "act", set()) is False

    def test_get_action_spec_found(self):
        ir = self._make_ir([{
            "name": {"display": "svc", "snake": "svc"},
            "direction": "OUTBOUND",
            "actions": [{"name": {"display": "restart", "snake": "restart"}, "required_scopes": []}],
        }])
        d = ChannelDispatcher(ir)
        assert d.get_action_spec("svc", "restart") is not None

    def test_get_action_spec_missing(self):
        ir = self._make_ir([{
            "name": {"display": "svc", "snake": "svc"},
            "direction": "OUTBOUND",
            "actions": [],
        }])
        d = ChannelDispatcher(ir)
        assert d.get_action_spec("svc", "nonexistent") is None

    @pytest.mark.asyncio
    async def test_channel_send_unknown_raises(self):
        d = ChannelDispatcher(self._make_ir())
        with pytest.raises(ChannelError, match="Unknown channel"):
            await d.channel_send("nonexistent", {})

    @pytest.mark.asyncio
    async def test_channel_send_scope_check_fails(self):
        ir = self._make_ir([{
            "name": {"display": "hook", "snake": "hook"},
            "direction": "OUTBOUND",
            "requirements": [{"direction": "send", "scope": "admin"}],
        }])
        d = ChannelDispatcher(ir)
        with pytest.raises(ChannelScopeError, match="Insufficient scope"):
            await d.channel_send("hook", {}, user_scopes={"viewer"})

    @pytest.mark.asyncio
    async def test_channel_send_no_config_skips(self, capsys):
        ir = self._make_ir([{
            "name": {"display": "hook", "snake": "hook"},
            "direction": "OUTBOUND",
            "requirements": [],
        }])
        d = ChannelDispatcher(ir)
        result = await d.channel_send("hook", {})
        assert result["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_channel_invoke_unknown_raises(self):
        d = ChannelDispatcher(self._make_ir())
        with pytest.raises(ChannelError, match="Unknown channel"):
            await d.channel_invoke("nonexistent", "act", {})

    @pytest.mark.asyncio
    async def test_on_ws_message_handler(self):
        d = ChannelDispatcher(self._make_ir([
            {"name": {"display": "hook", "snake": "hook"}, "direction": "OUTBOUND"}
        ]))
        received = []
        async def handler(ch, data):
            received.append((ch, data))
        d.on_ws_message(handler)
        await d._dispatch_ws_message("hook", {"event": "test"})
        assert len(received) == 1
        assert received[0][1] == {"event": "test"}

    @pytest.mark.asyncio
    async def test_startup_strict_validation_fails(self):
        ir = self._make_ir([{
            "name": {"display": "hook", "snake": "hook"},
            "direction": "OUTBOUND",
        }])
        d = ChannelDispatcher(ir)
        with pytest.raises(ChannelConfigError, match="missing deploy config"):
            await d.startup(strict=True)

    @pytest.mark.asyncio
    async def test_startup_non_strict_ok(self):
        ir = self._make_ir([{
            "name": {"display": "hook", "snake": "hook"},
            "direction": "OUTBOUND",
        }])
        d = ChannelDispatcher(ir)
        await d.startup(strict=False)
        await d.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_cleans_up(self):
        d = ChannelDispatcher(self._make_ir())
        await d.startup(strict=False)
        await d.shutdown()
        assert d._http_client is None


# ═══════════════════════════════════════════════════════════════
# ai_provider.py — Mock LLM SDK tests
# ═══════════════════════════════════════════════════════════════


import sys

# Create a mock anthropic module for tests (the real SDK may not be installed)
_mock_anthropic_module = MagicMock()
_mock_anthropic_module.APIError = type("APIError", (Exception,), {})


@pytest.fixture(autouse=False)
def mock_anthropic():
    """Patch anthropic module into sys.modules for testing."""
    with patch.dict(sys.modules, {"anthropic": _mock_anthropic_module}):
        yield _mock_anthropic_module


class TestAIProviderAnthropicMock:
    """Test Anthropic path with mocked SDK."""

    def _make_provider(self):
        p = AIProvider({"ai_provider": {"service": "anthropic", "api_key": "sk-test", "model": "test-model"}})
        p._client = MagicMock()
        return p

    @pytest.mark.asyncio
    async def test_anthropic_complete_success(self, mock_anthropic):
        p = self._make_provider()
        # Mock response with tool_use block
        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "set_output"
        mock_block.input = {"thinking": "ok", "result": "42"}
        mock_response = MagicMock()
        mock_response.content = [mock_block]
        p._client.messages.create = MagicMock(return_value=mock_response)

        result = await p.complete("system prompt", "user message", {"name": "set_output", "input_schema": {}})
        assert result == {"thinking": "ok", "result": "42"}

    @pytest.mark.asyncio
    async def test_anthropic_complete_no_tool_use_raises(self, mock_anthropic):
        p = self._make_provider()
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "I don't know"
        mock_response = MagicMock()
        mock_response.content = [mock_block]
        p._client.messages.create = MagicMock(return_value=mock_response)

        with pytest.raises(AIProviderError, match="did not call set_output"):
            await p.complete("system", "user", {})

    @pytest.mark.asyncio
    async def test_anthropic_agent_loop_set_output_on_first_turn(self, mock_anthropic):
        p = self._make_provider()
        mock_tool_call = MagicMock()
        mock_tool_call.type = "tool_use"
        mock_tool_call.name = "set_output"
        mock_tool_call.input = {"thinking": "done", "summary": "completed"}
        mock_response = MagicMock()
        mock_response.content = [mock_tool_call]
        mock_response.stop_reason = "end_turn"
        p._client.messages.create = MagicMock(return_value=mock_response)

        result = await p.agent_loop("system", "user", [], AsyncMock())
        assert result == {"thinking": "done", "summary": "completed"}

    @pytest.mark.asyncio
    async def test_anthropic_agent_loop_no_tool_calls(self, mock_anthropic):
        p = self._make_provider()
        mock_text = MagicMock()
        mock_text.type = "text"
        mock_text.text = "I'm done"
        mock_response = MagicMock()
        mock_response.content = [mock_text]
        mock_response.stop_reason = "end_turn"
        p._client.messages.create = MagicMock(return_value=mock_response)

        result = await p.agent_loop("system", "user", [], AsyncMock())
        assert "done" in result["thinking"].lower()

    @pytest.mark.asyncio
    async def test_anthropic_agent_loop_tool_then_output(self, mock_anthropic):
        p = self._make_provider()
        mock_tool_call = MagicMock()
        mock_tool_call.type = "tool_use"
        mock_tool_call.name = "content_query"
        mock_tool_call.input = {"content_name": "items"}
        mock_tool_call.id = "tc_1"
        first_response = MagicMock()
        first_response.content = [mock_tool_call]
        first_response.stop_reason = "tool_use"

        mock_output = MagicMock()
        mock_output.type = "tool_use"
        mock_output.name = "set_output"
        mock_output.input = {"thinking": "found items", "summary": "done"}
        second_response = MagicMock()
        second_response.content = [mock_output]
        second_response.stop_reason = "end_turn"

        p._client.messages.create = MagicMock(side_effect=[first_response, second_response])

        mock_execute = AsyncMock(return_value={"records": [{"id": 1}]})
        result = await p.agent_loop("system", "user", [], mock_execute)
        assert result == {"thinking": "found items", "summary": "done"}
        mock_execute.assert_called_once_with("content_query", {"content_name": "items"})

    @pytest.mark.asyncio
    async def test_anthropic_agent_loop_tool_error_handled(self, mock_anthropic):
        p = self._make_provider()
        mock_tool_call = MagicMock()
        mock_tool_call.type = "tool_use"
        mock_tool_call.name = "content_query"
        mock_tool_call.input = {"content_name": "items"}
        mock_tool_call.id = "tc_1"
        first_response = MagicMock()
        first_response.content = [mock_tool_call]
        first_response.stop_reason = "tool_use"

        mock_output = MagicMock()
        mock_output.type = "tool_use"
        mock_output.name = "set_output"
        mock_output.input = {"thinking": "tool failed", "summary": "error"}
        second_response = MagicMock()
        second_response.content = [mock_output]
        second_response.stop_reason = "end_turn"

        p._client.messages.create = MagicMock(side_effect=[first_response, second_response])
        mock_execute = AsyncMock(side_effect=Exception("tool broke"))
        result = await p.agent_loop("system", "user", [], mock_execute)
        assert result is not None

    @pytest.mark.asyncio
    async def test_anthropic_agent_loop_end_turn_no_tools(self, mock_anthropic):
        p = self._make_provider()
        mock_tool_call = MagicMock()
        mock_tool_call.type = "tool_use"
        mock_tool_call.name = "content_query"
        mock_tool_call.input = {}
        mock_tool_call.id = "tc_1"
        mock_response = MagicMock()
        mock_response.content = [mock_tool_call]
        mock_response.stop_reason = "end_turn"
        p._client.messages.create = MagicMock(return_value=mock_response)
        mock_execute = AsyncMock(return_value={})

        result = await p.agent_loop("system", "user", [], mock_execute)
        assert result is not None
