# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for v0.9 Phase 6b: state-machine transition events.

Per BRD #3 §5: every state-machine transition emits two events with
typed payloads.

Event names: `<content>.<machine>.<state>.<verb>` where `<verb>` is
either `entered` (after the new state lands) or `exited` (for the
prior state). Multi-word state names are preserved verbatim — state
names with spaces appear with spaces (e.g.,
`tickets.lifecycle.in progress.entered`).

Payload (BRD §5.3):
  - record_id
  - from_state, to_state
  - on_behalf_of (Principal)
  - invoked_by (Principal)
  - triggered_at (timestamp)
  - trigger_kind ('user_action' | 'cel_expression' | 'agent_action' |
    'system')

For v0.9 5b/6b: every direct user transition is `user_action`.
CEL-expression transitions still emit (when the CEL fires from a
user write); agent_action is reserved for 6c.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def captured_events():
    """A bare-minimum EventBus stand-in that captures published
    events into a list for assertion. Avoids the full runtime
    bring-up so tests stay focused on the state.py emission."""
    captured = []

    class _Bus:
        async def publish(self, event):
            captured.append(event)

    bus = _Bus()
    bus.captured = captured
    return bus


@pytest.fixture
def fake_storage():
    """Async storage stub with read/update_if returning controllable
    results. update_if always 'applies' unless told otherwise."""
    class _Storage:
        def __init__(self):
            self.records = {}
            self.update_calls = []

        async def read(self, table, rid):
            return self.records.get((table, rid))

        async def update_if(self, table, rid, condition, patch):
            self.update_calls.append((table, rid, condition, patch))
            rec = self.records.get((table, rid), {})
            new = {**rec, **patch, "id": rid}
            self.records[(table, rid)] = new

            class _R:
                def __init__(self, applied, record, reason=None):
                    self.applied = applied
                    self.record = record
                    self.reason = reason
            return _R(applied=True, record=new)

    return _Storage()


@pytest.fixture
def alice_user():
    """A direct-user dict shaped like what identity._build_user_dict
    produces for an authenticated request — has the `the_user`,
    `Principal`, and `scopes` keys needed for transition events."""
    return {
        "role": "alice",
        "scopes": ["x.write"],
        "User": {"Authenticated": True, "Name": "Alice"},
        "Principal": MagicMock(
            id="alice-id", display_name="Alice",
            type="human", is_anonymous=False, is_system=False,
        ),
        "the_user": {
            "id": "alice-id",
            "display_name": "Alice",
            "is_anonymous": False,
            "is_system": False,
            "scopes": ["x.write"],
            "preferences": {},
        },
    }


def _sm(machine_name="lifecycle", column="lifecycle",
        transitions=None, initial="draft"):
    return {
        "machine_name": machine_name,
        "column": column,
        "initial": initial,
        "transitions": transitions or {
            ("draft", "published"): "x.write",
            ("published", "archived"): "x.write",
        },
    }


# ── Event emission ──

@pytest.mark.asyncio
async def test_transition_emits_exited_then_entered(
    captured_events, fake_storage, alice_user
):
    """A successful transition emits both `<...>.<from>.exited` and
    `<...>.<to>.entered` events, in that order."""
    from termin_core.state import do_state_transition
    fake_storage.records[("tickets", 1)] = {"id": 1, "lifecycle": "draft"}
    sm_map = {"tickets": [_sm()]}

    await do_state_transition(
        fake_storage, "tickets", 1, "lifecycle", "published",
        alice_user, sm_map, terminator=None, event_bus=captured_events,
    )

    names = [e.get("channel_id") for e in captured_events.captured]
    # entered + exited at minimum, plus the legacy content.X.updated.
    assert "tickets.lifecycle.draft.exited" in names
    assert "tickets.lifecycle.published.entered" in names
    # Order: exited before entered.
    exited_idx = names.index("tickets.lifecycle.draft.exited")
    entered_idx = names.index("tickets.lifecycle.published.entered")
    assert exited_idx < entered_idx


@pytest.mark.asyncio
async def test_transition_event_payload_carries_brd_fields(
    captured_events, fake_storage, alice_user
):
    """Per BRD §5.3 the payload has record_id, from_state, to_state,
    on_behalf_of, invoked_by, triggered_at, trigger_kind."""
    from termin_core.state import do_state_transition
    fake_storage.records[("tickets", 7)] = {"id": 7, "lifecycle": "draft"}
    sm_map = {"tickets": [_sm()]}

    await do_state_transition(
        fake_storage, "tickets", 7, "lifecycle", "published",
        alice_user, sm_map, terminator=None, event_bus=captured_events,
    )

    entered = next(
        e for e in captured_events.captured
        if e.get("channel_id") == "tickets.lifecycle.published.entered"
    )
    data = entered["data"]
    assert data["record_id"] == 7
    assert data["from_state"] == "draft"
    assert data["to_state"] == "published"
    assert "on_behalf_of" in data
    assert "invoked_by" in data
    assert "triggered_at" in data
    assert data["trigger_kind"] == "user_action"


@pytest.mark.asyncio
async def test_transition_payload_principals_match_for_user_action(
    captured_events, fake_storage, alice_user
):
    """For direct user actions, on_behalf_of == invoked_by, both
    pointing at the user's principal id."""
    from termin_core.state import do_state_transition
    fake_storage.records[("tickets", 1)] = {"id": 1, "lifecycle": "draft"}
    sm_map = {"tickets": [_sm()]}

    await do_state_transition(
        fake_storage, "tickets", 1, "lifecycle", "published",
        alice_user, sm_map, terminator=None, event_bus=captured_events,
    )

    entered = next(
        e for e in captured_events.captured
        if e.get("channel_id") == "tickets.lifecycle.published.entered"
    )
    data = entered["data"]
    assert data["on_behalf_of"]["id"] == "alice-id"
    assert data["invoked_by"]["id"] == "alice-id"


@pytest.mark.asyncio
async def test_multiword_state_preserved_in_event_name(
    captured_events, fake_storage, alice_user
):
    """Per BRD §5.2: state names with spaces (e.g., `in progress`)
    appear in the event name verbatim — no underscore translation."""
    from termin_core.state import do_state_transition
    fake_storage.records[("tickets", 1)] = {"id": 1, "lifecycle": "draft"}
    sm_map = {"tickets": [_sm(transitions={
        ("draft", "in progress"): "x.write",
    })]}

    await do_state_transition(
        fake_storage, "tickets", 1, "lifecycle", "in progress",
        alice_user, sm_map, terminator=None, event_bus=captured_events,
    )

    names = [e.get("channel_id") for e in captured_events.captured]
    assert "tickets.lifecycle.in progress.entered" in names
    assert "tickets.lifecycle.draft.exited" in names


@pytest.mark.asyncio
async def test_machine_name_appears_in_event_name(
    captured_events, fake_storage, alice_user
):
    """Multi-state-machine: the machine name (not the column name,
    which is the same for v0.9) sits in the second slot of the
    dotted name."""
    from termin_core.state import do_state_transition
    fake_storage.records[("documents", 1)] = {
        "id": 1, "approval_status": "pending",
    }
    sm_map = {"documents": [_sm(
        machine_name="approval status",
        column="approval_status",
        transitions={("pending", "approved"): "x.write"},
        initial="pending",
    )]}

    await do_state_transition(
        fake_storage, "documents", 1, "approval status", "approved",
        alice_user, sm_map, terminator=None, event_bus=captured_events,
    )

    names = [e.get("channel_id") for e in captured_events.captured]
    assert "documents.approval status.pending.exited" in names
    assert "documents.approval status.approved.entered" in names


# ── Compile-time validation ──

_BASE_APP = '''Application: Trigger Test
  Description: trigger event validation

Identity:
  Scopes are "x.read", "x.write"
  Anonymous has "x.read"

Content called "tickets":
  Each ticket has a title which is text, required
  Each ticket has a lifecycle which is state:
    lifecycle starts as draft
    lifecycle can also be published or archived
    draft can become published if the user has x.write
    published can become archived if the user has x.write
  Anyone with "x.read" can view tickets
'''


def _src_with_compute_trigger(event_name: str) -> str:
    return _BASE_APP + f'''
Compute called "on publish":
  Accesses tickets
  Trigger on event "{event_name}"
  [1 + 1]
'''


def test_unknown_state_in_trigger_event_is_error():
    from termin.peg_parser import parse_peg as parse
    from termin.analyzer import Analyzer
    src = _src_with_compute_trigger("tickets.lifecycle.scoring.entered")
    prog, _ = parse(src)
    res = Analyzer(prog).analyze()
    # `scoring` is not a declared state on `tickets.lifecycle` —
    # should be flagged TERMIN-S056.
    assert any(
        "TERMIN-S056" in str(e) or "scoring" in str(e)
        for e in res.errors
    ), f"expected unknown-state error; got {res.errors}"


def test_known_state_entered_event_accepted():
    from termin.peg_parser import parse_peg as parse
    from termin.analyzer import Analyzer
    src = _src_with_compute_trigger("tickets.lifecycle.published.entered")
    prog, _ = parse(src)
    res = Analyzer(prog).analyze()
    assert not any(
        "TERMIN-S056" in str(e) for e in res.errors
    ), f"valid event should pass; errors: {res.errors}"


def test_unknown_machine_in_trigger_event_is_error():
    from termin.peg_parser import parse_peg as parse
    from termin.analyzer import Analyzer
    src = _src_with_compute_trigger("tickets.no_such_machine.draft.entered")
    prog, _ = parse(src)
    res = Analyzer(prog).analyze()
    assert any(
        "TERMIN-S056" in str(e) or "no_such_machine" in str(e)
        for e in res.errors
    ), f"expected unknown-machine error; got {res.errors}"


def test_invalid_verb_in_trigger_event_is_error():
    from termin.peg_parser import parse_peg as parse
    from termin.analyzer import Analyzer
    # `started` is not a valid verb; only `entered` and `exited` are.
    src = _src_with_compute_trigger("tickets.lifecycle.draft.started")
    prog, _ = parse(src)
    res = Analyzer(prog).analyze()
    assert any(
        "TERMIN-S056" in str(e) or "started" in str(e) or "verb" in str(e)
        for e in res.errors
    ), f"expected invalid-verb error; got {res.errors}"


def test_unknown_content_in_trigger_event_is_error():
    from termin.peg_parser import parse_peg as parse
    from termin.analyzer import Analyzer
    src = _src_with_compute_trigger("orders.lifecycle.draft.entered")
    prog, _ = parse(src)
    res = Analyzer(prog).analyze()
    assert any(
        "TERMIN-S056" in str(e) or "orders" in str(e)
        for e in res.errors
    ), f"expected unknown-content error; got {res.errors}"


def test_non_state_machine_event_passes():
    """Trigger on event "<arbitrary>" without dot structure of a
    state-machine event — passes (these are non-SM events that
    other parts of the system may emit). Only events matching the
    SM-event shape are validated against state machines."""
    from termin.peg_parser import parse_peg as parse
    from termin.analyzer import Analyzer
    src = _src_with_compute_trigger("custom.event.name")
    prog, _ = parse(src)
    res = Analyzer(prog).analyze()
    assert not any(
        "TERMIN-S056" in str(e) for e in res.errors
    ), f"non-SM events should pass; errors: {res.errors}"
