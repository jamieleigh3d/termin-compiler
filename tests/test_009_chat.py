# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for D-09: Chat Presentation Component.

TDD: These tests are written FIRST, before implementation.
They cover:
  - PEG grammar: chat_line rule parses both default and mapped forms
  - Parser: ChatDirective AST node produced correctly
  - IR lowering: ChatDirective -> chat ComponentNode
  - Runtime rendering: chat component produces correct HTML structure
  - Integration: agent_chatbot.termin compiles with new chat syntax
  - All existing examples still compile
"""

from pathlib import Path

import json
import pytest

from termin.peg_parser import parse_peg as parse
from termin.analyzer import analyze
from termin.lower import lower
from termin.ir import ComponentNode, PropValue

from termin_runtime import create_termin_app
from fastapi.testclient import TestClient


EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
IR_DIR = Path(__file__).parent.parent / "ir_dumps"


def _compile(source: str):
    """Parse, analyze, and lower a DSL source string."""
    program, errors = parse(source)
    assert errors.ok, errors.format()
    result = analyze(program)
    assert result.ok, result.format()
    return lower(program)


def _compile_file(name: str):
    """Compile an example file by name."""
    source = (EXAMPLES_DIR / name).read_text(encoding="utf-8")
    return _compile(source)


def _load_ir(name: str) -> str:
    return (IR_DIR / f"{name}_ir.json").read_text(encoding="utf-8")


def _find_component(page, comp_type):
    """Find first child component of given type in a page."""
    for ch in page.children:
        if ch.type == comp_type:
            return ch
    return None


def _find_all_components(page, comp_type):
    """Find all children of given type in a page."""
    return [ch for ch in page.children if ch.type == comp_type]


# ============================================================
# PEG Grammar + Parser: ChatDirective AST
# ============================================================

class TestChatDSLParsing:
    """Test that 'Show a chat for X' lines parse into ChatDirective AST nodes."""

    def test_chat_default_parses(self):
        """'Show a chat for messages' should produce a ChatDirective."""
        from termin.ast_nodes import ChatDirective
        source = '''Application: Chat Test
  Description: Test chat parsing
Id: 00000000-0000-0000-0000-000000000000

Users authenticate with stub
Scopes are "chat.use"
An "anonymous" has "chat.use"

Content called "messages":
  Each message has a role which is one of: "user", "assistant"
  Each message has a content which is text
  Anyone with "chat.use" can view or create messages

As an anonymous, I want to chat
  so that I can talk:
    Show a page called "Chat"
    Show a chat for messages
'''
        program, errors = parse(source)
        assert errors.ok, errors.format()
        # Find the story
        assert len(program.stories) >= 1
        story = program.stories[0]
        chat_directives = [d for d in story.directives if isinstance(d, ChatDirective)]
        assert len(chat_directives) == 1
        cd = chat_directives[0]
        assert cd.source == "messages"
        assert cd.role_field == "role"      # default
        assert cd.content_field == "content"  # default

    def test_chat_mapped_parses(self):
        """'Show a chat for X with role "Y", content "Z"' should set field mapping."""
        from termin.ast_nodes import ChatDirective
        source = '''Application: Chat Mapped Test
  Description: Test mapped chat
Id: 00000000-0000-0000-0000-000000000001

Users authenticate with stub
Scopes are "chat.use"
An "anonymous" has "chat.use"

Content called "chat messages":
  Each chat message has a sender which is text
  Each chat message has a body which is text
  Anyone with "chat.use" can view or create chat messages

As an anonymous, I want to chat
  so that I can talk:
    Show a page called "Chat"
    Show a chat for chat messages with role "sender", content "body"
'''
        program, errors = parse(source)
        assert errors.ok, errors.format()
        story = program.stories[0]
        chat_directives = [d for d in story.directives if isinstance(d, ChatDirective)]
        assert len(chat_directives) == 1
        cd = chat_directives[0]
        assert cd.source == "chat messages"
        assert cd.role_field == "sender"
        assert cd.content_field == "body"

    def test_chat_line_classification(self):
        """Lines starting with 'Show a chat' should be classified correctly."""
        source = '''Application: Classify Test
  Description: Test
Id: 00000000-0000-0000-0000-000000000002

Users authenticate with stub
Scopes are "use"
An "anonymous" has "use"

Content called "msgs":
  Each msg has a role which is text
  Each msg has a content which is text
  Anyone with "use" can view or create msgs

As an anonymous, I want to chat
  so that I can talk:
    Show a page called "Test"
    Show a chat for msgs
'''
        program, errors = parse(source)
        assert errors.ok, errors.format()


# ============================================================
# IR Lowering: ChatDirective -> chat ComponentNode
# ============================================================

class TestChatIRLowering:
    """Test that ChatDirective lowers to a 'chat' ComponentNode."""

    def test_chat_component_node_type(self):
        """ChatDirective should produce a component with type='chat'."""
        source = '''Application: Chat IR Test
  Description: Test chat IR
Id: 00000000-0000-0000-0000-000000000010

Users authenticate with stub
Scopes are "chat.use"
An "anonymous" has "chat.use"

Content called "messages":
  Each message has a role which is one of: "user", "assistant"
  Each message has a content which is text
  Anyone with "chat.use" can view or create messages

As an anonymous, I want to chat
  so that I can talk:
    Show a page called "Chat"
    Show a chat for messages
'''
        spec = _compile(source)
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat is not None, "Expected a 'chat' ComponentNode in the page"

    def test_chat_props_source(self):
        """Chat component should have source prop set to snake_case content name."""
        source = '''Application: Chat Props Test
  Description: Test chat props
Id: 00000000-0000-0000-0000-000000000011

Users authenticate with stub
Scopes are "chat.use"
An "anonymous" has "chat.use"

Content called "messages":
  Each message has a role which is one of: "user", "assistant"
  Each message has a content which is text
  Anyone with "chat.use" can view or create messages

As an anonymous, I want to chat
  so that I can talk:
    Show a page called "Chat"
    Show a chat for messages
'''
        spec = _compile(source)
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat.props["source"] == "messages"

    def test_chat_default_field_mapping(self):
        """Default field mapping should be role='role', content='content'."""
        source = '''Application: Default Mapping Test
  Description: Test
Id: 00000000-0000-0000-0000-000000000012

Users authenticate with stub
Scopes are "chat.use"
An "anonymous" has "chat.use"

Content called "messages":
  Each message has a role which is one of: "user", "assistant"
  Each message has a content which is text
  Anyone with "chat.use" can view or create messages

As an anonymous, I want to chat
  so that I can talk:
    Show a page called "Chat"
    Show a chat for messages
'''
        spec = _compile(source)
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat.props["role_field"] == "role"
        assert chat.props["content_field"] == "content"

    def test_chat_custom_field_mapping(self):
        """Custom field mapping should be preserved in IR props."""
        source = '''Application: Custom Mapping Test
  Description: Test
Id: 00000000-0000-0000-0000-000000000013

Users authenticate with stub
Scopes are "chat.use"
An "anonymous" has "chat.use"

Content called "chat messages":
  Each chat message has a sender which is text
  Each chat message has a body which is text
  Anyone with "chat.use" can view or create chat messages

As an anonymous, I want to chat
  so that I can talk:
    Show a page called "Chat"
    Show a chat for chat messages with role "sender", content "body"
'''
        spec = _compile(source)
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat.props["source"] == "chat_messages"
        assert chat.props["role_field"] == "sender"
        assert chat.props["content_field"] == "body"

    def test_chat_no_separate_form_or_table(self):
        """Chat component replaces table+form — no separate data_table or form components."""
        source = '''Application: No Table Test
  Description: Test
Id: 00000000-0000-0000-0000-000000000014

Users authenticate with stub
Scopes are "chat.use"
An "anonymous" has "chat.use"

Content called "messages":
  Each message has a role which is one of: "user", "assistant"
  Each message has a content which is text
  Anyone with "chat.use" can view or create messages

As an anonymous, I want to chat
  so that I can talk:
    Show a page called "Chat"
    Show a chat for messages
'''
        spec = _compile(source)
        page = next(p for p in spec.pages if p.slug == "chat")
        data_tables = _find_all_components(page, "data_table")
        forms = _find_all_components(page, "form")
        # Chat should NOT produce separate data_table or form
        assert len(data_tables) == 0, "Chat should not produce a data_table component"
        assert len(forms) == 0, "Chat should not produce a form component"

    def test_chat_has_subscribe_child(self):
        """Chat component should automatically include a subscribe child for live updates."""
        source = '''Application: Subscribe Test
  Description: Test
Id: 00000000-0000-0000-0000-000000000015

Users authenticate with stub
Scopes are "chat.use"
An "anonymous" has "chat.use"

Content called "messages":
  Each message has a role which is one of: "user", "assistant"
  Each message has a content which is text
  Anyone with "chat.use" can view or create messages

As an anonymous, I want to chat
  so that I can talk:
    Show a page called "Chat"
    Show a chat for messages
'''
        spec = _compile(source)
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        # The chat node itself should have a subscribe child
        subscribe_children = [c for c in chat.children if c.type == "subscribe"]
        assert len(subscribe_children) >= 1, "Chat should have an implicit subscribe child"


# ============================================================
# agent_chatbot.termin: Migration to chat syntax
# ============================================================

class TestAgentChatbotMigration:
    """Test that agent_chatbot.termin compiles with the new chat syntax."""

    def test_compiles_without_error(self):
        """agent_chatbot.termin should compile cleanly."""
        spec = _compile_file("agent_chatbot.termin")
        assert spec is not None
        assert spec.name == "Agent Chatbot"

    def test_chat_page_has_chat_component(self):
        """The Chat page should have a chat component instead of data_table+form."""
        spec = _compile_file("agent_chatbot.termin")
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat is not None, "Expected a 'chat' component on the Chat page"

    def test_chat_component_source(self):
        """Chat component should reference 'messages' content."""
        spec = _compile_file("agent_chatbot.termin")
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat.props["source"] == "messages"

    def test_chat_component_field_mapping(self):
        """agent_chatbot maps role='role', content='body' explicitly
        because the Content field is named 'body', not 'content'."""
        spec = _compile_file("agent_chatbot.termin")
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat.props["role_field"] == "role"
        assert chat.props["content_field"] == "body"

    def test_no_data_table_in_chat_page(self):
        """The old data_table should be gone after migration."""
        spec = _compile_file("agent_chatbot.termin")
        page = next(p for p in spec.pages if p.slug == "chat")
        tables = _find_all_components(page, "data_table")
        assert len(tables) == 0, "Chat page should not have data_table after migration"

    def test_messages_content_still_exists(self):
        """The Content declaration stays — chat reads from it."""
        spec = _compile_file("agent_chatbot.termin")
        content_names = {c.name.snake for c in spec.content}
        assert "messages" in content_names


# ============================================================
# All examples still compile
# ============================================================

ALL_EXAMPLES = [f.name for f in EXAMPLES_DIR.glob("*.termin")]


@pytest.mark.parametrize("example", ALL_EXAMPLES)
def test_all_examples_still_compile(example):
    """Every example should compile without errors after D-09 changes."""
    spec = _compile_file(example)
    assert spec is not None
    assert spec.name != ""


# ============================================================
# Runtime rendering
# ============================================================

class TestChatRuntimeRendering:
    """Test that the chat component renders correct HTML with data-termin attributes."""

    def test_chat_renders_data_termin_chat(self):
        """The chat component should produce a data-termin-chat container."""
        from termin_runtime.presentation import render_component
        node = {
            "type": "chat",
            "props": {
                "source": "messages",
                "role_field": "role",
                "content_field": "content",
            },
            "children": [],
        }
        html = render_component(node)
        assert 'data-termin-chat' in html

    def test_chat_renders_input_area(self):
        """The chat should include an input area with data-termin-chat-input."""
        from termin_runtime.presentation import render_component
        node = {
            "type": "chat",
            "props": {
                "source": "messages",
                "role_field": "role",
                "content_field": "content",
            },
            "children": [],
        }
        html = render_component(node)
        assert 'data-termin-chat-input' in html

    def test_chat_renders_send_button(self):
        """The chat should include a send button."""
        from termin_runtime.presentation import render_component
        node = {
            "type": "chat",
            "props": {
                "source": "messages",
                "role_field": "role",
                "content_field": "content",
            },
            "children": [],
        }
        html = render_component(node)
        assert 'send' in html.lower() or 'Send' in html

    def test_chat_message_template(self):
        """The chat should have a message loop with data-termin-chat-message."""
        from termin_runtime.presentation import render_component
        node = {
            "type": "chat",
            "props": {
                "source": "messages",
                "role_field": "role",
                "content_field": "content",
            },
            "children": [],
        }
        html = render_component(node)
        assert 'data-termin-chat-message' in html

    def test_chat_posts_to_api(self):
        """Chat input should POST to /api/v1/{source}."""
        from termin_runtime.presentation import render_component
        node = {
            "type": "chat",
            "props": {
                "source": "messages",
                "role_field": "role",
                "content_field": "content",
            },
            "children": [],
        }
        html = render_component(node)
        assert '/api/v1/messages' in html

    def test_chat_renders_subscribe(self):
        """Chat should include a data-termin-subscribe attribute for live updates."""
        from termin_runtime.presentation import render_component
        node = {
            "type": "chat",
            "props": {
                "source": "messages",
                "role_field": "role",
                "content_field": "content",
            },
            "children": [{"type": "subscribe", "props": {"content": "messages"}}],
        }
        html = render_component(node)
        assert 'data-termin-subscribe' in html

    def test_chat_custom_fields_in_template(self):
        """Custom role_field/content_field should appear in the template rendering."""
        from termin_runtime.presentation import render_component
        node = {
            "type": "chat",
            "props": {
                "source": "chat_messages",
                "role_field": "sender",
                "content_field": "body",
            },
            "children": [],
        }
        html = render_component(node)
        # The template should use the custom field names
        assert 'sender' in html
        assert 'body' in html


# ============================================================
# Runtime integration: full page render + POST
# ============================================================

class TestChatRuntimeIntegration:
    """Full integration tests using agent_chatbot IR + runtime."""

    def test_chat_page_renders_chat_ui(self):
        """The Chat page should render with chat UI elements."""
        ir_json = _load_ir("agent_chatbot")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            r = client.get("/chat")
            assert r.status_code == 200
            assert 'data-termin-chat' in r.text

    def test_chat_input_area_present(self):
        """The Chat page should have an input area."""
        ir_json = _load_ir("agent_chatbot")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            r = client.get("/chat")
            assert r.status_code == 200
            assert 'data-termin-chat-input' in r.text

    def test_post_message_via_api(self):
        """POST to /api/v1/messages should create a message record."""
        ir_json = _load_ir("agent_chatbot")
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            r = client.post("/api/v1/messages", json={"body": "Hello from chat!"})
            assert r.status_code == 201
            data = r.json()
            assert data["body"] == "Hello from chat!"


# ============================================================
# IR JSON serialization
# ============================================================

class TestChatIRSerialization:
    """Test that the chat component serializes correctly to IR JSON."""

    def test_chat_in_json_ir(self):
        """Chat component should appear as type='chat' in serialized IR JSON."""
        source = '''Application: Chat Serial Test
  Description: Test
Id: 00000000-0000-0000-0000-000000000020

Users authenticate with stub
Scopes are "chat.use"
An "anonymous" has "chat.use"

Content called "messages":
  Each message has a role which is one of: "user", "assistant"
  Each message has a content which is text
  Anyone with "chat.use" can view or create messages

As an anonymous, I want to chat
  so that I can talk:
    Show a page called "Chat"
    Show a chat for messages
'''
        spec = _compile(source)
        # Serialize to JSON using the RuntimeBackend
        from termin.backends.runtime import RuntimeBackend
        backend = RuntimeBackend()
        backend.generate(spec)
        ir_json = backend._ir_json
        ir_data = json.loads(ir_json)
        # Find the chat page
        chat_page = next(p for p in ir_data["pages"] if p["slug"] == "chat")
        chat_components = [c for c in chat_page["children"] if c["type"] == "chat"]
        assert len(chat_components) == 1
        chat = chat_components[0]
        assert chat["props"]["source"] == "messages"
        assert chat["props"]["role_field"] == "role"
        assert chat["props"]["content_field"] == "content"
