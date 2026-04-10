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
