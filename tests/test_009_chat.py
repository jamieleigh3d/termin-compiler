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
from termin_core.ir.types import ComponentNode, PropValue

from termin_server import create_termin_app
from fastapi.testclient import TestClient


import json as _json_mod
from helpers import extract_ir_from_pkg

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


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


def _ir_json(pkg_path):
    return _json_mod.dumps(extract_ir_from_pkg(pkg_path))


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

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

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

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

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

Identity:
  Scopes are "use"
  Anonymous has "use"

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

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

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

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

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

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

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

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

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

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

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

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

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
    """v0.9.1-shape (`messages` collection + chat clauses) compilation
    is preserved at examples/agent_chatbot_legacy.termin. The v0.9.2
    refresh of agent_chatbot.termin uses the conversation-mode
    binding (`Show a chat for chat_threads.conversation`) — covered
    by TestAgentChatbotV092Migration below."""

    def test_compiles_without_error(self):
        """agent_chatbot_legacy.termin should compile cleanly."""
        spec = _compile_file("agent_chatbot_legacy.termin")
        assert spec is not None
        assert spec.name == "Agent Chatbot Legacy"

    def test_chat_page_has_chat_component(self):
        spec = _compile_file("agent_chatbot_legacy.termin")
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat is not None, "Expected a 'chat' component on the Chat page"

    def test_chat_component_source(self):
        """Legacy chat component references the `messages` collection."""
        spec = _compile_file("agent_chatbot_legacy.termin")
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat.props["source"] == "messages"

    def test_chat_component_field_mapping(self):
        """Legacy maps role='role', content='body' explicitly."""
        spec = _compile_file("agent_chatbot_legacy.termin")
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat.props["role_field"] == "role"
        assert chat.props["content_field"] == "body"

    def test_no_data_table_in_chat_page(self):
        spec = _compile_file("agent_chatbot_legacy.termin")
        page = next(p for p in spec.pages if p.slug == "chat")
        tables = _find_all_components(page, "data_table")
        assert len(tables) == 0, "Chat page should not have data_table after migration"

    def test_messages_content_still_exists(self):
        spec = _compile_file("agent_chatbot_legacy.termin")
        content_names = {c.name.snake for c in spec.content}
        assert "messages" in content_names


class TestAgentChatbotV092Migration:
    """v0.9.2 L11 refresh of agent_chatbot.termin: conversation-mode
    binding (`Show a chat for chat_threads.conversation`) per §14
    of the v0.9.2 conversation-field tech design."""

    def test_chat_page_uses_conversation_field_binding(self):
        spec = _compile_file("agent_chatbot.termin")
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat is not None
        assert chat.props["source"] == "chat_threads"
        assert chat.props["conversation_field"] == "conversation"
        # No legacy role/content_field props on the v0.9.2 binding.
        assert "role_field" not in chat.props
        assert "content_field" not in chat.props

    def test_chat_threads_content_carries_conversation_field(self):
        spec = _compile_file("agent_chatbot.termin")
        chat_threads = next(
            c for c in spec.content if c.name.snake == "chat_threads"
        )
        assert any(f.name == "conversation" for f in chat_threads.fields)


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
        from termin_server.presentation import render_component
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
        from termin_server.presentation import render_component
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
        from termin_server.presentation import render_component
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
        from termin_server.presentation import render_component
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
        from termin_server.presentation import render_component
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
        from termin_server.presentation import render_component
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
        from termin_server.presentation import render_component
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

    @pytest.fixture(autouse=True)
    def _pkgs(self, compiled_packages):
        self.pkgs = compiled_packages

    def test_chat_page_renders_chat_ui(self):
        """The Chat page should render with chat UI elements."""
        ir_json = _ir_json(self.pkgs["agent_chatbot"])
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            r = client.get("/chat")
            assert r.status_code == 200
            assert 'data-termin-chat' in r.text

    def test_chat_input_area_present(self):
        """The Chat page should have an input area."""
        ir_json = _ir_json(self.pkgs["agent_chatbot"])
        app = create_termin_app(ir_json, strict_channels=False, deploy_config={})
        with TestClient(app) as client:
            client.cookies.set("termin_role", "anonymous")
            r = client.get("/chat")
            assert r.status_code == 200
            assert 'data-termin-chat-input' in r.text

    def test_post_message_via_api(self):
        """v0.9.1 collection shape: POST to /api/v1/messages creates
        a record. Preserved at agent_chatbot_legacy.termin.

        v0.9.2 equivalent (POST to
        /api/v1/chat_threads/{id}/conversation:append) is exercised
        in test_agents.py::TestEventTriggeredCompute::
        test_chatbot_creates_thread_then_appends and end-to-end in
        test_l7_conversation_materialization.py."""
        ir_json = _ir_json(self.pkgs["agent_chatbot_legacy"])
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

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

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
        # Serialize to JSON via the shared serializer (post Phase
        # 2.x retirement of RuntimeBackend).
        from termin_core.ir.serialize import serialize_ir
        ir_json = serialize_ir(spec)
        ir_data = json.loads(ir_json)
        # Find the chat page
        chat_page = next(p for p in ir_data["pages"] if p["slug"] == "chat")
        chat_components = [c for c in chat_page["children"] if c["type"] == "chat"]
        assert len(chat_components) == 1
        chat = chat_components[0]
        assert chat["props"]["source"] == "messages"
        assert chat["props"]["role_field"] == "role"
        assert chat["props"]["content_field"] == "content"


# ============================================================
# v0.9.2 L9: bare `Show a chat for <content>.<field>` binding
# ============================================================

# Per the v0.9.2 conversation field type tech design §14, the new
# binding form is pure semantics — no clause sub-block, no
# modifiers — and parses alongside the legacy messages-collection
# forms. The chat ComponentNode discriminates on the
# `conversation_field` prop being present.


_CONV_SOURCE = '''Application: Chat Conversation Test
  Description: v0.9.2 L9 — bare conversation-field binding
Id: 00000000-0000-0000-0000-000000000091

Identity:
  Scopes are "chat.use"
  An "anonymous" has "chat.use"

Content called "chat_threads":
  Each chat_thread has a title which is text, default "Conversation"
  Each chat_thread has a conversation which is conversation
  Anyone with "chat.use" can view chat_threads
  Anyone with "chat.use" can create chat_threads
  Anyone with "chat.use" can append to chat_threads.conversation

As an anonymous, I want to chat
  so that I can talk:
    Show a page called "Chat"
    Show a chat for chat_threads.conversation
'''


class TestChatConversationFieldParsing:
    """The dotted form parses into a ChatDirective whose
    ``conversation_field`` is the (content, field) pair."""

    def test_conversation_form_parses(self):
        from termin.ast_nodes import ChatDirective
        program, errors = parse(_CONV_SOURCE)
        assert errors.ok, errors.format()
        story = program.stories[0]
        chat_directives = [d for d in story.directives if isinstance(d, ChatDirective)]
        assert len(chat_directives) == 1
        cd = chat_directives[0]
        assert cd.conversation_field == ("chat_threads", "conversation")
        # Source carries the content half forward — provider renderers
        # use it to discover the parent record's content type.
        assert cd.source == "chat_threads"

    def test_legacy_form_does_not_set_conversation_field(self):
        """The pre-L9 messages-collection forms must leave
        ``conversation_field`` as None so renderers can branch on it."""
        from termin.ast_nodes import ChatDirective
        legacy = '''Application: Legacy Chat
  Description: Test
Id: 00000000-0000-0000-0000-000000000092

Identity:
  Scopes are "chat.use"
  Anonymous has "chat.use"

Content called "messages":
  Each message has a role which is one of: "user", "assistant"
  Each message has a content which is text
  Anyone with "chat.use" can view or create messages

As an anonymous, I want to chat
  so that I can talk:
    Show a page called "Chat"
    Show a chat for messages with role "role", content "content"
'''
        program, errors = parse(legacy)
        assert errors.ok, errors.format()
        story = program.stories[0]
        cd = next(d for d in story.directives if isinstance(d, ChatDirective))
        assert cd.conversation_field is None
        assert cd.role_field == "role"
        assert cd.content_field == "content"


class TestChatConversationFieldLowering:
    """The dotted form lowers to a chat ComponentNode whose props are
    `source` (parent content snake-case) + `conversation_field` (field
    name). No `role_field`/`content_field`; no implicit subscribe child.
    """

    def test_chat_node_props(self):
        spec = _compile(_CONV_SOURCE)
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat is not None
        assert chat.props["source"] == "chat_threads"
        assert chat.props["conversation_field"] == "conversation"
        # Legacy props must NOT leak in — the renderer keys on prop
        # presence to discriminate, so an accidental default would put
        # the chat in the wrong rendering branch.
        assert "role_field" not in chat.props
        assert "content_field" not in chat.props

    def test_chat_node_has_no_subscribe_child_for_conversation_form(self):
        """The conversation-field form subscribes to
        `<content>.<field>.appended` — a different channel than the legacy
        `content.<name>` CRUD prefix the subscribe child carries. The
        runtime hydrator resolves the channel from
        `data-termin-conversation-field`; no subscribe child is needed."""
        spec = _compile(_CONV_SOURCE)
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert all(c.type != "subscribe" for c in chat.children)

    def test_legacy_form_lowering_unchanged(self):
        """Regression: the legacy collection form lowers as before — same
        props (`source`, `role_field`, `content_field`) and the implicit
        subscribe child. Verified against agent_chatbot_legacy.termin
        (the v0.9.1-shape preserved alongside the L11-refreshed
        agent_chatbot.termin)."""
        spec = _compile_file("agent_chatbot_legacy.termin")
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat.props["source"] == "messages"
        assert chat.props["role_field"] == "role"
        assert chat.props["content_field"] == "body"
        assert "conversation_field" not in chat.props
        assert any(c.type == "subscribe" for c in chat.children)

    def test_serialized_ir_carries_conversation_field(self):
        """The IR JSON shape is the over-the-wire contract for runtimes;
        a typo in the prop key would silently regress every conformance
        runtime."""
        spec = _compile(_CONV_SOURCE)
        from termin_core.ir.serialize import serialize_ir
        ir_data = json.loads(serialize_ir(spec))
        chat_page = next(p for p in ir_data["pages"] if p["slug"] == "chat")
        chat = next(c for c in chat_page["children"] if c["type"] == "chat")
        assert chat["props"]["source"] == "chat_threads"
        assert chat["props"]["conversation_field"] == "conversation"


class TestChatConversationSmokeExample:
    """The L9 smoke example in examples-dev/ must compile (mirrors the
    pattern §16.2 will land in examples/ at L11)."""

    def test_smoke_example_compiles(self):
        smoke_path = (
            EXAMPLES_DIR.parent / "examples-dev" / "chat_conversation_smoke.termin"
        )
        assert smoke_path.exists(), f"missing smoke example at {smoke_path}"
        source = smoke_path.read_text(encoding="utf-8")
        spec = _compile(source)
        page = next(p for p in spec.pages if p.slug == "chat")
        chat = _find_component(page, "chat")
        assert chat is not None
        assert chat.props["conversation_field"] == "conversation"
