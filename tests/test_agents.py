"""Tests for AI agent Computes — provider integration, event triggers, field wiring.

Uses mocked AI provider to avoid requiring actual API keys.
"""

import json
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from termin_runtime import create_termin_app
from termin_runtime.ai_provider import AIProvider, build_output_tool, build_agent_tools


IR_DIR = Path(__file__).parent.parent / "ir_dumps"


def _load_ir(name: str) -> str:
    return (IR_DIR / f"{name}_ir.json").read_text(encoding="utf-8")


MOCK_DEPLOY = {
    "ai_provider": {
        "service": "anthropic",
        "model": "claude-test",
        "api_key": "test-key-not-real",
    },
}


# ── Compiler tests: IR validation ──

class TestAgentSimpleIR:
    @classmethod
    def setup_class(cls):
        cls.ir = json.loads(_load_ir("agent_simple"))

    def test_compute_provider_llm(self):
        comp = self.ir["computes"][0]
        assert comp["provider"] == "llm"

    def test_compute_shape_none(self):
        comp = self.ir["computes"][0]
        assert comp["shape"] == "NONE"

    def test_compute_accesses(self):
        comp = self.ir["computes"][0]
        assert "completions" in comp["accesses"]

    def test_compute_input_fields(self):
        comp = self.ir["computes"][0]
        assert ["completion", "prompt"] in comp["input_fields"]

    def test_compute_output_fields(self):
        comp = self.ir["computes"][0]
        assert ["completion", "response"] in comp["output_fields"]

    def test_compute_directive(self):
        comp = self.ir["computes"][0]
        assert "helpful assistant" in comp["directive"]

    def test_compute_objective(self):
        comp = self.ir["computes"][0]
        assert "Answer" in comp["objective"]

    def test_compute_trigger(self):
        comp = self.ir["computes"][0]
        assert comp["trigger"] == 'event "completion.created"'


class TestAgentChatbotIR:
    @classmethod
    def setup_class(cls):
        cls.ir = json.loads(_load_ir("agent_chatbot"))

    def test_compute_provider_agent(self):
        comp = self.ir["computes"][0]
        assert comp["provider"] == "ai-agent"

    def test_compute_accesses_messages(self):
        comp = self.ir["computes"][0]
        assert "messages" in comp["accesses"]

    def test_compute_trigger_where(self):
        comp = self.ir["computes"][0]
        assert comp["trigger_where"] == 'message.role == "user"'

    def test_compute_directive(self):
        comp = self.ir["computes"][0]
        assert "conversational" in comp["directive"]

    def test_message_role_default(self):
        messages = next(c for c in self.ir["content"] if c["name"]["snake"] == "messages")
        role_field = next(f for f in messages["fields"] if f["name"] == "role")
        assert role_field["default_expr"] == '"user"'
        assert "user" in role_field["enum_values"]
        assert "assistant" in role_field["enum_values"]


# ── Tool schema generation ──

class TestToolSchemaGeneration:
    def test_build_output_tool_single_field(self):
        content_lookup = {
            "completions": {
                "singular": "completion",
                "fields": [
                    {"name": "prompt", "column_type": "TEXT"},
                    {"name": "response", "column_type": "TEXT"},
                ],
            }
        }
        tool = build_output_tool([("completion", "response")], content_lookup)
        assert tool["name"] == "set_output"
        props = tool["input_schema"]["properties"]
        assert "thinking" in props
        assert "response" in props
        assert props["response"]["type"] == "string"
        assert "thinking" in tool["input_schema"]["required"]
        assert "response" in tool["input_schema"]["required"]

    def test_build_output_tool_with_enum(self):
        content_lookup = {
            "tickets": {
                "singular": "ticket",
                "fields": [
                    {"name": "category", "column_type": "TEXT", "enum_values": ["hardware", "software"]},
                    {"name": "priority", "column_type": "TEXT", "enum_values": ["low", "high"]},
                ],
            }
        }
        tool = build_output_tool([("ticket", "category"), ("ticket", "priority")], content_lookup)
        props = tool["input_schema"]["properties"]
        assert props["category"]["enum"] == ["hardware", "software"]
        assert props["priority"]["enum"] == ["low", "high"]

    def test_build_output_tool_thinking_is_first(self):
        content_lookup = {"completions": {"singular": "completion", "fields": [{"name": "response", "column_type": "TEXT"}]}}
        tool = build_output_tool([("completion", "response")], content_lookup)
        required = tool["input_schema"]["required"]
        assert required[0] == "thinking"

    def test_build_agent_tools(self):
        content_lookup = {"messages": {"singular": "message", "fields": []}}
        tools = build_agent_tools(["messages"], content_lookup)
        tool_names = {t["name"] for t in tools}
        assert "content_query" in tool_names
        assert "content_create" in tool_names
        assert "content_update" in tool_names
        assert "state_transition" in tool_names

    def test_agent_tools_scoped_to_accesses(self):
        tools = build_agent_tools(["messages"], {})
        query_tool = next(t for t in tools if t["name"] == "content_query")
        assert query_tool["input_schema"]["properties"]["content_name"]["enum"] == ["messages"]


# ── Runtime integration: event triggers ──

class TestEventTriggeredCompute:
    def test_agent_simple_creates_record_without_ai(self):
        """Without AI provider configured, the record is created but LLM is skipped."""
        ir_json = _load_ir("agent_simple")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            r = client.post("/api/v1/completions", json={"prompt": "What is 2+2?"})
            assert r.status_code == 201
            data = r.json()
            assert data["prompt"] == "What is 2+2?"
            # Response is empty because AI provider not configured
            assert data.get("response") is None or data.get("response") == ""

    def test_chatbot_creates_message_with_default_role(self):
        """Message created without explicit role should default to 'user'."""
        ir_json = _load_ir("agent_chatbot")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            r = client.post("/api/v1/messages", json={"body": "Hello!"})
            assert r.status_code == 201
            data = r.json()
            assert data["body"] == "Hello!"
            # Default role should be "user" from defaults to "user"
            # (depends on runtime evaluating default_expr)

    def test_agent_simple_page_renders(self):
        """The Agent page should render with a form and table."""
        ir_json = _load_ir("agent_simple")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            r = client.get("/agent")
            assert r.status_code == 200
            assert "prompt" in r.text.lower()

    def test_chatbot_page_renders(self):
        """The Chat page should render with a form and table."""
        ir_json = _load_ir("agent_chatbot")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            r = client.get("/chat")
            assert r.status_code == 200
            assert "body" in r.text.lower()


# ── Form submit should not cause page reload ──

class TestFormSubmitNoRedirect:
    """Form submissions should use AJAX, not 303 redirect.

    The 303 redirect kills the WebSocket connection, causing the client
    to miss real-time updates (like LLM responses). The form should
    submit via fetch() and the server should return JSON, not redirect.
    """

    def test_form_post_returns_json_not_redirect(self):
        """POST to the form page with Accept: application/json should return JSON."""
        ir_json = _load_ir("agent_simple")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app, raise_server_exceptions=False) as client:
            client.cookies.set("termin_role", "anonymous")
            r = client.post(
                "/agent",
                data={"prompt": "test prompt"},
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
            # Should NOT be a 303 redirect — should be 200 with JSON
            # or at minimum, the record should be created
            if r.status_code == 303:
                pytest.fail(
                    "Form POST returned 303 redirect — this kills the WebSocket connection. "
                    "The form should submit via AJAX and return JSON."
                )

    def test_form_post_creates_record_via_api(self):
        """The form can alternatively POST to the API endpoint for AJAX submission."""
        ir_json = _load_ir("agent_simple")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            # API endpoint always returns JSON, no redirect
            r = client.post("/api/v1/completions", json={"prompt": "test via API"})
            assert r.status_code == 201
            data = r.json()
            assert data["prompt"] == "test via API"
            assert "id" in data


# ── WebSocket real-time update tests ──

class TestWebSocketUpdates:
    """Verify that record creation and updates are pushed via WebSocket."""

    def test_form_ajax_returns_created_record(self):
        """AJAX form POST should return the created record with id."""
        ir_json = _load_ir("agent_simple")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            r = client.post(
                "/agent",
                data={"prompt": "hello"},
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            )
            assert r.status_code == 200
            data = r.json()
            assert "id" in data, f"Response should contain record id, got: {data}"
            assert data.get("prompt") == "hello"

    def test_form_ajax_record_visible_in_api(self):
        """Record created via AJAX form should be visible in the API."""
        ir_json = _load_ir("agent_simple")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            # Create via AJAX form
            r = client.post(
                "/agent",
                data={"prompt": "test prompt"},
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            )
            assert r.status_code == 200
            created = r.json()
            # Verify via API
            r2 = client.get("/api/v1/completions")
            records = r2.json()
            found = [rec for rec in records if rec.get("id") == created.get("id")]
            assert len(found) == 1
            assert found[0]["prompt"] == "test prompt"

    def _receive_until(self, ws, op, max_messages=5):
        """Receive WebSocket messages until we get one with the specified op."""
        for _ in range(max_messages):
            msg = ws.receive_json()
            if msg.get("op") == op:
                return msg
        pytest.fail(f"Never received message with op='{op}' after {max_messages} messages")

    def test_websocket_subscribe_gets_current_data(self):
        """WebSocket subscribe should return current records."""
        ir_json = _load_ir("agent_simple")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            # Create a record first
            client.post("/api/v1/completions", json={"prompt": "existing"})
            # Connect WebSocket and subscribe
            with client.websocket_connect("/runtime/ws") as ws:
                ws.send_json({
                    "v": 1, "ch": "content.completions", "op": "subscribe", "ref": "sub1", "payload": {}
                })
                # May receive push events before the subscribe response
                resp = self._receive_until(ws, "response")
                assert resp["ref"] == "sub1"
                assert "current" in resp["payload"]
                records = resp["payload"]["current"]
                assert any(r["prompt"] == "existing" for r in records)

    def test_websocket_receives_push_on_api_create(self):
        """Creating a record via API should push an event to WebSocket subscribers."""
        ir_json = _load_ir("agent_simple")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            # Connect and subscribe FIRST, before creating any records
            with client.websocket_connect("/runtime/ws") as ws:
                ws.send_json({
                    "v": 1, "ch": "content.completions", "op": "subscribe", "ref": "sub1", "payload": {}
                })
                self._receive_until(ws, "response")  # consume subscribe response

                # Now create a record via API
                client.post("/api/v1/completions", json={"prompt": "new record"})

                # Should receive a push event
                push = self._receive_until(ws, "push")
                assert "completions" in push["ch"]

    def test_websocket_receives_push_on_form_create(self):
        """Creating a record via AJAX form should push an event to WebSocket subscribers."""
        ir_json = _load_ir("agent_simple")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            with client.websocket_connect("/runtime/ws") as ws:
                ws.send_json({
                    "v": 1, "ch": "content.completions", "op": "subscribe", "ref": "sub1", "payload": {}
                })
                self._receive_until(ws, "response")

                # Create via AJAX form POST
                client.post(
                    "/agent",
                    data={"prompt": "form created"},
                    headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                )

                # Should receive a push event
                push = self._receive_until(ws, "push")
                assert "completions" in push["ch"]

    def test_websocket_push_payload_contains_record_fields(self):
        """Push payload should be the record dict with field values, not a wrapper."""
        ir_json = _load_ir("agent_simple")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            with client.websocket_connect("/runtime/ws") as ws:
                ws.send_json({
                    "v": 1, "ch": "content.completions", "op": "subscribe", "ref": "sub1", "payload": {}
                })
                self._receive_until(ws, "response")

                client.post("/api/v1/completions", json={"prompt": "payload test"})

                push = self._receive_until(ws, "push")
                payload = push["payload"]
                # Payload should be the record itself, not {"channel_id": ..., "data": ...}
                assert "id" in payload, f"Payload should have 'id', got: {list(payload.keys())}"
                assert "prompt" in payload, f"Payload should have 'prompt', got: {list(payload.keys())}"
                assert payload["prompt"] == "payload test"
                # Should NOT have nested wrapper keys
                assert "channel_id" not in payload, "Payload should not be the raw event wrapper"
                assert "data" not in payload or isinstance(payload.get("data"), str), \
                    "Payload should not have nested 'data' dict"


# ── Mark...as semantic emphasis ──

class TestMarkAs:
    def test_parse_mark_rows(self):
        from termin.peg_parser import parse_peg
        src = '''As a user, I want to see data:
  Show a page called "Dashboard"
  Display a table of incidents with columns: title, severity
  Mark rows where `severity == "critical"` as "urgent"'''
        prog, errors = parse_peg(src)
        assert errors.ok
        from termin.ast_nodes import MarkAs
        marks = [d for d in prog.stories[0].directives if isinstance(d, MarkAs)]
        assert len(marks) == 1
        assert marks[0].condition_expr == 'severity == "critical"'
        assert marks[0].label == "urgent"
        assert marks[0].scope == "row"

    def test_parse_mark_field(self):
        from termin.peg_parser import parse_peg
        src = '''As a user, I want to see data:
  Show a page called "Dashboard"
  Display a table of employees with columns: name, salary
  Mark salary where `salary > 200000` as "high-earner"'''
        prog, errors = parse_peg(src)
        assert errors.ok
        from termin.ast_nodes import MarkAs
        marks = [d for d in prog.stories[0].directives if isinstance(d, MarkAs)]
        assert len(marks) == 1
        assert marks[0].scope == "salary"
        assert marks[0].label == "high-earner"


# ── G1: Compute system type in CEL context ──

def _minimal_ir_with_compute(preconditions=None, postconditions=None, body_lines=None,
                             scopes=None, role_scopes=None):
    """Build a minimal IR dict with a single Compute for testing."""
    scopes = scopes or ["admin", "basic"]
    role_scopes = role_scopes or {"admin_user": ["admin", "basic"], "basic_user": ["basic"]}
    return json.dumps({
        "ir_version": "0.4.0",
        "reflection_enabled": False,
        "app_id": "test-compute-ctx",
        "name": "Test Compute",
        "description": "Test app for Compute context",
        "auth": {
            "provider": "stub",
            "scopes": scopes,
            "roles": [
                {"name": role, "scopes": s}
                for role, s in role_scopes.items()
            ],
        },
        "content": [{
            "name": {"display": "items", "snake": "items", "pascal": "Items"},
            "singular": "item",
            "fields": [
                {"name": "title", "column_type": "TEXT", "nullable": False,
                 "unique": False, "business_type": "short_text", "confidentiality_scope": None,
                 "default_expr": None, "enum_values": []},
            ],
            "confidentiality_scope": None,
            "audit": "content",
        }],
        "access_grants": [
            {"content": "items", "verb": "read", "scope": "basic"},
            {"content": "items", "verb": "create", "scope": "basic"},
        ],
        "state_machines": [],
        "events": [],
        "routes": [],
        "pages": [],
        "nav_items": [],
        "streams": [],
        "computes": [{
            "name": {"display": "test compute", "snake": "test_compute", "pascal": "TestCompute"},
            "shape": "TRANSFORM",
            "input_content": ["items"],
            "output_content": ["items"],
            "body_lines": body_lines or ["size(items)"],
            "required_scope": "basic",
            "required_role": None,
            "input_params": [],
            "output_params": [],
            "client_safe": False,
            "identity_mode": "delegate",
            "required_confidentiality_scopes": [],
            "output_confidentiality_scope": None,
            "field_dependencies": [],
            "provider": None,
            "preconditions": preconditions or [],
            "postconditions": postconditions or [],
            "directive": None,
            "objective": None,
            "strategy": None,
            "trigger": None,
            "trigger_where": None,
            "accesses": ["items"],
            "input_fields": [],
            "output_fields": [],
            "output_creates": None,
        }],
        "channels": [],
        "boundaries": [],
        "error_handlers": [],
        "reclassification_points": [],
    })


class TestComputeCelContext:
    """G1: Verify the Compute system type is available in CEL precondition/postcondition context."""

    def test_precondition_accesses_compute_scopes(self):
        """Precondition using Compute.Scopes should pass when user has the required scope."""
        ir_json = _minimal_ir_with_compute(
            preconditions=['Compute.Scopes.exists(s, s == "admin")'],
        )
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "admin_user")
            r = client.post("/api/v1/compute/test_compute", json={"input": {}})
            # Should pass precondition (admin_user has "admin" scope)
            assert r.status_code != 412, f"Precondition should pass for admin_user: {r.text}"

    def test_precondition_rejects_missing_scope(self):
        """Precondition using Compute.Scopes should fail (412) when scope is absent."""
        ir_json = _minimal_ir_with_compute(
            preconditions=['Compute.Scopes.exists(s, s == "admin")'],
        )
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "basic_user")
            r = client.post("/api/v1/compute/test_compute", json={"input": {}})
            assert r.status_code == 412, f"Expected 412 for missing scope, got {r.status_code}: {r.text}"

    def test_precondition_accesses_compute_name(self):
        """Precondition can reference Compute.Name."""
        ir_json = _minimal_ir_with_compute(
            preconditions=['Compute.Name == "test compute"'],
        )
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "basic_user")
            r = client.post("/api/v1/compute/test_compute", json={"input": {}})
            assert r.status_code != 412, f"Precondition on Compute.Name should pass: {r.text}"

    def test_precondition_accesses_compute_trigger(self):
        """Precondition can reference Compute.Trigger (should be 'api' for direct invocation)."""
        ir_json = _minimal_ir_with_compute(
            preconditions=['Compute.Trigger == "api"'],
        )
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "basic_user")
            r = client.post("/api/v1/compute/test_compute", json={"input": {}})
            assert r.status_code != 412, f"Precondition on Compute.Trigger should pass: {r.text}"

    def test_precondition_accesses_compute_started_at(self):
        """Precondition can reference Compute.StartedAt (ISO timestamp string)."""
        ir_json = _minimal_ir_with_compute(
            preconditions=['size(Compute.StartedAt) > 0'],
        )
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "basic_user")
            r = client.post("/api/v1/compute/test_compute", json={"input": {}})
            assert r.status_code != 412, f"Precondition on Compute.StartedAt should pass: {r.text}"

    def test_postcondition_accesses_compute_context(self):
        """Postconditions also receive the Compute context."""
        ir_json = _minimal_ir_with_compute(
            postconditions=['Compute.Name == "test compute"'],
        )
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "basic_user")
            r = client.post("/api/v1/compute/test_compute", json={"input": {}})
            # Should not fail with 409 (postcondition failure)
            assert r.status_code != 409, f"Postcondition on Compute.Name should pass: {r.text}"


# ── G5: Runtime scheduler for Trigger on schedule ──

from termin_runtime.scheduler import Scheduler, parse_schedule_interval


class TestScheduleParser:
    """Unit tests for schedule trigger parsing."""

    def test_parse_schedule_every_1_hour(self):
        assert parse_schedule_interval("schedule every 1 hour") == 3600

    def test_parse_schedule_every_5_minutes(self):
        assert parse_schedule_interval("schedule every 5 minutes") == 300

    def test_parse_schedule_every_30_seconds(self):
        assert parse_schedule_interval("schedule every 30 seconds") == 30

    def test_parse_schedule_every_2_days(self):
        assert parse_schedule_interval("schedule every 2 days") == 172800

    def test_parse_returns_none_for_event_trigger(self):
        assert parse_schedule_interval('event "order.created"') is None

    def test_parse_returns_none_for_empty(self):
        assert parse_schedule_interval("") is None
        assert parse_schedule_interval(None) is None

    def test_parse_returns_none_for_nonsense(self):
        assert parse_schedule_interval("do something else") is None


class TestSchedulerExecution:
    """Integration test: Compute with schedule trigger executes on timer."""

    @pytest.mark.asyncio
    async def test_scheduled_compute_executes_within_timeout(self):
        """A Compute scheduled every 1 second should execute at least once within 3 seconds."""
        executions = []

        async def mock_execute(comp, record, content_name, main_loop=None):
            executions.append(comp["name"]["display"])

        scheduler = Scheduler()
        comp = {
            "name": {"display": "tick", "snake": "tick", "pascal": "Tick"},
            "provider": None,
            "trigger": "schedule every 1 second",
        }
        scheduler.register(comp, 1.0, mock_execute)
        await scheduler.start()
        try:
            # Wait up to 3 seconds for at least one execution
            for _ in range(30):
                if executions:
                    break
                await asyncio.sleep(0.1)
            assert len(executions) >= 1, f"Expected at least 1 execution, got {len(executions)}"
            assert executions[0] == "tick"
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_scheduler_stop_cancels_tasks(self):
        """After stop(), no more executions should occur."""
        executions = []

        async def mock_execute(comp, record, content_name, main_loop=None):
            executions.append(1)

        scheduler = Scheduler()
        comp = {"name": {"display": "stopper", "snake": "stopper", "pascal": "Stopper"}}
        scheduler.register(comp, 0.5, mock_execute)
        await scheduler.start()
        await asyncio.sleep(0.7)
        await scheduler.stop()
        count_after_stop = len(executions)
        await asyncio.sleep(0.7)
        assert len(executions) == count_after_stop, "No executions should occur after stop()"
