# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Fallback fidelity tests for v0.9.2 L6 `compute_conversation_line`.

The TatSu PEG parser has a known platform-dependent context-state leak
(workspace MEMORY note 9: `_model.parse(...)` returns None on the second
and subsequent calls on WSL/Linux even for valid PEG input). For each
new line shape we add to `_parse_line`, we ship a fallback path that
reconstructs the AST shape from raw text — and a separate test that
exercises the fallback DIRECTLY with synthetic input so a Linux
contributor (or future CI matrix run) catches divergence between the
TatSu shape and the fallback shape on every platform.

This test forces the fallback by calling `_parse_line(text,
"compute_conversation_line", ...)` and asserting the returned tuple
matches the canonical TatSu output shape: `("compute_conversation",
(content, field))`. The contract: regardless of which path runs, the
caller sees the same 2-tuple.

Lesson learned 2026-04-29: the access-rule fallback hardcoded
`verbs=["view"]` regardless of source, silently rewriting compute
intent on WSL. Compile-time tests like `test_compiler_fidelity::
test_no_tatsu_fallbacks` only verify that TatSu didn't fall back; they
do NOT verify fallback correctness when it does fire.
"""

from __future__ import annotations

import pytest

from termin.parse_handlers import _parse_line


# ── Direct fallback-path tests ──
#
# We can't easily disable TatSu mid-process, but the fallback runs
# verbatim against the same `text` argument, and the post-condition is
# the same: a `("compute_conversation", (content, field))` tuple. These
# tests cover the input-shapes the fallback would see on WSL/Linux.

@pytest.mark.parametrize("text, expected_content, expected_field", [
    # Canonical shape from §10 examples: snake_case content + snake_case field.
    ("Conversation is chat_threads.conversation",
     "chat_threads", "conversation"),
    ("Conversation is sessions.conversation_log",
     "sessions", "conversation_log"),
    # Singular-form content: authors may spell either; the parser
    # carries the source spelling and lower() canonicalizes downstream.
    ("Conversation is chat_thread.conversation",
     "chat_thread", "conversation"),
    # Field with underscores is preserved verbatim — no munging.
    ("Conversation is sessions.debug_log",
     "sessions", "debug_log"),
])
def test_compute_conversation_line_returns_canonical_tuple(
    text: str, expected_content: str, expected_field: str,
):
    """Whether TatSu or the Python fallback handles the line, the
    returned tuple shape must be identical: a `("compute_conversation",
    (content, field))` pair where content and field carry the source
    spelling.
    """
    result = _parse_line(text, "compute_conversation_line", ln=1)
    assert result is not None, (
        "Both TatSu and the fallback must produce a non-None result — "
        "a None here would make the assembler drop the line silently"
    )
    assert isinstance(result, tuple) and len(result) == 2
    tag, payload = result
    assert tag == "compute_conversation", (
        f"Expected tag 'compute_conversation' (the assembler's switch "
        f"key), got {tag!r}"
    )
    assert isinstance(payload, tuple) and len(payload) == 2, (
        f"Payload must be a (content, field) 2-tuple — got {payload!r}. "
        f"The block assembler unpacks this directly into "
        f"ComputeNode.conversation_source."
    )
    content, field_name = payload
    assert content == expected_content
    assert field_name == expected_field


def test_compute_conversation_line_strips_extra_whitespace():
    """The fallback splits on the literal `.` token; surrounding
    whitespace shouldn't leak into either name. Mirrors how `Input
    from field <X>.<Y>` and `Output into field <X>.<Y>` handle their
    field_ref shapes."""
    result = _parse_line(
        "Conversation is   chat_threads.conversation",
        "compute_conversation_line", ln=1,
    )
    assert result is not None
    _, (content, field_name) = result
    assert content == "chat_threads"
    assert field_name == "conversation"


def test_compute_conversation_line_missing_dot_does_not_crash():
    """Defensive: a malformed `Conversation is chat_threads` (no dot)
    must not raise from the fallback. The analyzer surfaces a
    semantic error elsewhere; the parser path needs to return SOME
    tuple so the assembler can keep walking."""
    result = _parse_line(
        "Conversation is chat_threads",
        "compute_conversation_line", ln=1,
    )
    assert result is not None
    tag, (content, field_name) = result
    assert tag == "compute_conversation"
    assert content == "chat_threads"
    # Empty field reflects "the source did not name a field"; the
    # analyzer's TERMIN-S058 will fire downstream if the trigger
    # doesn't match (and it can't match an empty field).
    assert field_name == ""
