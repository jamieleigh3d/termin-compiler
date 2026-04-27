# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 6a.6: channel-subscription ownership cascade.

Per BRD #3 §3.6: channels carrying owned content (and content
subscriptions in general) automatically filter to records the
subscriber owns. Players subscribed to `content.profiles` only
receive their own profile updates; subscribed to `content.messages`
only receive messages on sessions they own; etc.

The cascade is intrinsic to the content type's ownership
declaration — no additional source-level construct is required.
A subscription to non-owned content fans out to all subscribers
unchanged.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from termin_runtime.websocket_manager import ConnectionManager


# ── Helpers ──

def _make_user(user_id: str, anonymous: bool = False, system: bool = False):
    """Build a user dict shaped like identity._build_user_dict output."""
    return {
        "role": user_id,
        "scopes": ["x.read"],
        "the_user": {
            "id": user_id,
            "display_name": user_id.title(),
            "is_anonymous": anonymous,
            "is_system": system,
            "scopes": ["x.read"],
            "preferences": {},
        },
    }


def _add_conn(cm: ConnectionManager, conn_id: str, user: dict, *, sub: str):
    """Inject a fake connection into a ConnectionManager for testing."""
    ws = MagicMock()
    ws.send_json = AsyncMock()
    cm.active[conn_id] = {"ws": ws, "user": user, "subscriptions": {sub}}
    return ws


# ── Broadcast cascade ──

@pytest.mark.asyncio
async def test_broadcast_filters_owned_content_to_owner_only():
    """When a record on an owned content type fires a content.X
    event, only subscribers whose user.id matches the record's
    ownership-field value receive the push."""
    cm = ConnectionManager()
    cm.set_content_ownership({"profiles": "principal_id"})

    alice_ws = _add_conn(cm, "c1", _make_user("alice-id"), sub="content.profiles")
    bob_ws = _add_conn(cm, "c2", _make_user("bob-id"), sub="content.profiles")

    await cm.broadcast_to_subscribers(
        "content.profiles.updated",
        {"data": {"id": 1, "principal_id": "alice-id", "best": 4}},
    )

    alice_ws.send_json.assert_awaited_once()
    bob_ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_does_not_filter_non_owned_content():
    """Content types without an ownership declaration broadcast to
    every subscriber — the cascade only kicks in for owned content."""
    cm = ConnectionManager()
    cm.set_content_ownership({})  # no owned content

    alice_ws = _add_conn(cm, "c1", _make_user("alice-id"), sub="content.tickets")
    bob_ws = _add_conn(cm, "c2", _make_user("bob-id"), sub="content.tickets")

    await cm.broadcast_to_subscribers(
        "content.tickets.updated",
        {"data": {"id": 1, "title": "Bug"}},
    )

    alice_ws.send_json.assert_awaited_once()
    bob_ws.send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_filters_on_created_and_deleted_events_too():
    """The cascade applies to all three content lifecycle verbs —
    created, updated, deleted — not just `updated`."""
    cm = ConnectionManager()
    cm.set_content_ownership({"profiles": "principal_id"})

    alice_ws = _add_conn(cm, "c1", _make_user("alice-id"), sub="content.profiles")
    bob_ws = _add_conn(cm, "c2", _make_user("bob-id"), sub="content.profiles")

    for verb in ("created", "updated", "deleted"):
        alice_ws.send_json.reset_mock()
        bob_ws.send_json.reset_mock()
        await cm.broadcast_to_subscribers(
            f"content.profiles.{verb}",
            {"data": {"id": 1, "principal_id": "alice-id"}},
        )
        alice_ws.send_json.assert_awaited_once()
        bob_ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_drops_when_payload_missing_owner_field():
    """If the event payload doesn't carry the ownership field at
    all, the cascade is conservative — drop rather than over-share.
    This prevents a malformed payload from leaking unrelated rows."""
    cm = ConnectionManager()
    cm.set_content_ownership({"profiles": "principal_id"})

    alice_ws = _add_conn(cm, "c1", _make_user("alice-id"), sub="content.profiles")

    await cm.broadcast_to_subscribers(
        "content.profiles.updated",
        {"data": {"id": 1, "best": 4}},  # no principal_id
    )

    alice_ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_unfiltered_state_machine_events_pass_through():
    """v0.9 Phase 6b state-machine transition events
    (`<content>.<machine>.<state>.{entered,exited}`) and other
    non-content channels are not subject to the cascade — they
    broadcast to every subscriber whose pattern prefix matches."""
    cm = ConnectionManager()
    cm.set_content_ownership({"profiles": "principal_id"})

    alice_ws = _add_conn(cm, "c1", _make_user("alice-id"),
                         sub="profiles.lifecycle.draft.entered")
    bob_ws = _add_conn(cm, "c2", _make_user("bob-id"),
                       sub="profiles.lifecycle.draft.entered")

    await cm.broadcast_to_subscribers(
        "profiles.lifecycle.draft.entered",
        {"data": {"record_id": 1, "from_state": "init", "to_state": "draft"}},
    )

    alice_ws.send_json.assert_awaited_once()
    bob_ws.send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_anonymous_subscribers_get_nothing_for_owned_content():
    """Anonymous principals can't own a row — so an anonymous
    subscription to owned content receives nothing."""
    cm = ConnectionManager()
    cm.set_content_ownership({"profiles": "principal_id"})

    anon_ws = _add_conn(cm, "c1",
                        _make_user("", anonymous=True),
                        sub="content.profiles")

    await cm.broadcast_to_subscribers(
        "content.profiles.updated",
        {"data": {"id": 1, "principal_id": "alice-id"}},
    )

    anon_ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_broadcast_dead_connection_pruned_after_send_failure():
    """Pre-existing behavior preserved: a connection whose send
    raises gets pruned. The cascade filter shouldn't change that."""
    cm = ConnectionManager()
    cm.set_content_ownership({"profiles": "principal_id"})

    alice_ws = _add_conn(cm, "c1", _make_user("alice-id"), sub="content.profiles")
    alice_ws.send_json.side_effect = ConnectionError("boom")

    await cm.broadcast_to_subscribers(
        "content.profiles.updated",
        {"data": {"id": 1, "principal_id": "alice-id"}},
    )

    assert "c1" not in cm.active


# ── Initial-data load on subscribe (filtered list_records) ──

def test_filter_owned_records_helper_returns_only_owned_rows():
    """The same filter applied at subscribe time uses the same
    ownership lookup as the broadcast cascade. Pure helper test."""
    from termin_runtime.websocket_manager import _filter_owned_rows

    rows = [
        {"id": 1, "principal_id": "alice-id", "best": 4},
        {"id": 2, "principal_id": "bob-id", "best": 3},
        {"id": 3, "principal_id": "alice-id", "best": 5},
    ]
    user = _make_user("alice-id")

    result = _filter_owned_rows(rows, "principal_id", user)
    assert [r["id"] for r in result] == [1, 3]


def test_filter_owned_records_anonymous_gets_empty():
    """Anonymous principals see no owned rows."""
    from termin_runtime.websocket_manager import _filter_owned_rows

    rows = [{"id": 1, "principal_id": "alice-id"}]
    user = _make_user("", anonymous=True)

    result = _filter_owned_rows(rows, "principal_id", user)
    assert result == []


def test_filter_owned_records_no_ownership_returns_unchanged():
    """When ownership_field is None (non-owned content), the
    helper passes rows through unchanged."""
    from termin_runtime.websocket_manager import _filter_owned_rows

    rows = [{"id": 1}, {"id": 2}]
    user = _make_user("alice-id")

    result = _filter_owned_rows(rows, None, user)
    assert result == rows
