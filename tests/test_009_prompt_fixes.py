"""Tests for thread 009 fixes: LLM prompt mapping, optional directive, thinking field.

Fix 1: Level 1 LLM prompt mapping — objective belongs in system message, not user turn.
Fix 2: Directive is optional — don't inject default when only Objective is declared.
Fix 3: Compiler-controlled thinking field — set_output only includes 'thinking' when
        the compute's output schema declares it.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from termin_runtime.ai_provider import build_output_tool


# ── Fix 1: Level 1 LLM prompt mapping ──

class TestLLMPromptMapping:
    """The system message should be directive + objective.
    The user message should be input field values only."""

    def test_level1_system_includes_objective(self):
        """For Level 1 computes, system = directive + objective."""
        from termin_runtime.compute_runner import _build_llm_prompts
        comp = {
            "directive": "You are a sentiment analyzer.",
            "objective": "Classify the sentiment of the user's review.",
            "input_fields": [("reviews", "text")],
        }
        record = {"text": "Great product!", "id": 1}
        system, user = _build_llm_prompts(comp, record, "reviews", {})
        assert "sentiment analyzer" in system
        assert "Classify the sentiment" in system
        # User message should NOT contain the objective
        assert "Classify the sentiment" not in user
        # User message SHOULD contain the input field value
        assert "Great product!" in user

    def test_level1_user_message_is_fields_only(self):
        """User turn should be just the input field values."""
        from termin_runtime.compute_runner import _build_llm_prompts
        comp = {
            "directive": "You are a translator.",
            "objective": "Translate the text to French.",
            "input_fields": [("messages", "body")],
        }
        record = {"body": "Hello world", "id": 1}
        system, user = _build_llm_prompts(comp, record, "messages", {})
        assert "Hello world" in user
        assert "Translate" not in user  # objective is in system, not user

    def test_level1_no_input_fields(self):
        """When there are no input fields, user message is empty or minimal."""
        from termin_runtime.compute_runner import _build_llm_prompts
        comp = {
            "directive": "You summarize things.",
            "objective": "Provide a daily summary.",
            "input_fields": [],
        }
        record = {"id": 1}
        system, user = _build_llm_prompts(comp, record, "reports", {})
        assert "summarize" in system
        assert "daily summary" in system


# ── Fix 2: Directive is optional ──

class TestDirectiveOptional:
    """Don't inject 'You are a helpful assistant' when author only writes Objective."""

    def test_no_default_directive_when_objective_present(self):
        """If only objective is set, system = objective only. No injected directive."""
        from termin_runtime.compute_runner import _build_llm_prompts
        comp = {
            "directive": "",  # Author didn't write a directive
            "objective": "Analyze the code quality.",
            "input_fields": [],
        }
        record = {"id": 1}
        system, user = _build_llm_prompts(comp, record, "code", {})
        assert "helpful assistant" not in system
        assert "Analyze the code quality" in system

    def test_no_default_directive_for_agents(self):
        """Agent compute with no directive should not inject a default."""
        from termin_runtime.compute_runner import _build_agent_prompts
        comp = {
            "directive": "",
            "objective": "Process incoming tickets.",
        }
        record = {"id": 1, "title": "Bug report"}
        system, user = _build_agent_prompts(comp, record)
        assert "helpful" not in system.lower()
        assert "Process incoming tickets" in system

    def test_explicit_directive_preserved(self):
        """When author provides both directive and objective, both in system."""
        from termin_runtime.compute_runner import _build_llm_prompts
        comp = {
            "directive": "You are a medical coding expert.",
            "objective": "Assign ICD-10 codes to the diagnosis.",
            "input_fields": [],
        }
        record = {"id": 1}
        system, user = _build_llm_prompts(comp, record, "diagnoses", {})
        assert "medical coding expert" in system
        assert "ICD-10 codes" in system


# ── Fix 3: Compiler-controlled thinking field ──

class TestThinkingFieldControl:
    """set_output tool should only include 'thinking' when the compute's
    output schema declares a thinking field."""

    def test_thinking_excluded_when_not_in_schema(self):
        """Output tool should NOT have 'thinking' when it's not an output field."""
        tool = build_output_tool(
            output_fields=[("completion", "response")],
            content_lookup={
                "completions": {
                    "singular": "completion",
                    "fields": [
                        {"name": "response", "column_type": "TEXT"},
                    ],
                }
            },
        )
        props = tool["input_schema"]["properties"]
        assert "response" in props
        assert "thinking" not in props
        assert "thinking" not in tool["input_schema"]["required"]

    def test_thinking_included_when_in_schema(self):
        """Output tool SHOULD have 'thinking' when it's declared as an output field."""
        tool = build_output_tool(
            output_fields=[("completion", "response"), ("completion", "thinking")],
            content_lookup={
                "completions": {
                    "singular": "completion",
                    "fields": [
                        {"name": "response", "column_type": "TEXT"},
                        {"name": "thinking", "column_type": "TEXT"},
                    ],
                }
            },
        )
        props = tool["input_schema"]["properties"]
        assert "response" in props
        assert "thinking" in props

    def test_agent_set_output_no_thinking_by_default(self):
        """Agent set_output tool should not include thinking unconditionally."""
        from termin_runtime.compute_runner import _build_agent_set_output
        comp = {"output_fields": [], "output_params": []}
        tool = _build_agent_set_output(comp, {})
        props = tool["input_schema"]["properties"]
        assert "summary" in props
        # thinking should not be forced into agent set_output either
        assert "thinking" not in props or "thinking" not in tool["input_schema"].get("required", [])
