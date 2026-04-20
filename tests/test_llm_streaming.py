# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""v0.8 item #7: LLM streaming protocol — delta push over the event bus.

Full design in docs/termin-streaming-protocol.md. This file tests:

  1. The AIProvider.simulate_stream test helper yields deltas in order,
     with done=False on intermediates and done=True on the terminal.
  2. An event-bus subscriber to compute.stream.<id> receives delta
     events in order, followed by a terminal event carrying final_text.
  3. A prefix subscription on compute.stream.* receives deltas for
     multiple concurrent invocations.

The Anthropic-specific stream_complete() integration is v0.8.1.
Tests here do not require any LLM API credentials — they use the
simulate helper which yields from a scripted list.
"""

import asyncio
import uuid

import pytest

from termin_runtime.ai_provider import AIProvider
from termin_runtime.events import EventBus


# ── Provider simulate_stream helper ─────────────────────────────────

class TestSimulateStream:
    """AIProvider.simulate_stream yields scripted deltas for CI tests."""

    def test_empty_list_yields_single_terminal(self):
        async def run():
            provider = AIProvider({"ai_provider": {}})
            events = []
            async for delta, done in provider.simulate_stream([]):
                events.append((delta, done))
            return events
        events = asyncio.run(run())
        # Empty input produces a single terminal event with empty text.
        assert events == [("", True)]

    def test_yields_deltas_in_order_then_terminal(self):
        async def run():
            provider = AIProvider({"ai_provider": {}})
            events = []
            async for delta, done in provider.simulate_stream(
                    ["Hello", ", ", "world", "."]):
                events.append((delta, done))
            return events
        events = asyncio.run(run())
        # Four deltas, done=False on the intermediates, done=True on the last.
        assert events == [
            ("Hello", False),
            (", ", False),
            ("world", False),
            (".", True),
        ]


# ── Event bus delta propagation ─────────────────────────────────────

class TestStreamChannelPropagation:
    """A subscriber to compute.stream.<id> receives deltas in order and
    a terminal event. A subscriber to compute.stream.* (prefix) also
    receives events from multiple concurrent invocations."""

    def test_subscriber_receives_deltas_then_terminal(self):
        async def run():
            bus = EventBus()
            inv_id = str(uuid.uuid4())
            channel = f"compute.stream.{inv_id}"
            q = bus.subscribe(channel)

            # Publish three deltas and a terminal event.
            for delta in ["Hi", " ", "there"]:
                await bus.publish({
                    "channel_id": channel,
                    "data": {
                        "invocation_id": inv_id,
                        "compute": "greet",
                        "delta": delta,
                        "done": False,
                    },
                })
            await bus.publish({
                "channel_id": channel,
                "data": {
                    "invocation_id": inv_id,
                    "compute": "greet",
                    "delta": "",
                    "done": True,
                    "final_text": "Hi there",
                },
            })

            received = []
            for _ in range(4):
                received.append(await asyncio.wait_for(q.get(), timeout=1.0))
            return received

        received = asyncio.run(run())
        assert len(received) == 4
        # Order preserved.
        assert [r["data"]["delta"] for r in received] == ["Hi", " ", "there", ""]
        # Only the last is done.
        assert [r["data"]["done"] for r in received] == [False, False, False, True]
        # Terminal carries final_text.
        assert received[-1]["data"]["final_text"] == "Hi there"

    def test_prefix_subscriber_receives_multiple_invocations(self):
        async def run():
            bus = EventBus()
            q = bus.subscribe("compute.stream.")  # prefix match

            inv1, inv2 = str(uuid.uuid4()), str(uuid.uuid4())
            await bus.publish({
                "channel_id": f"compute.stream.{inv1}",
                "data": {"invocation_id": inv1, "delta": "A", "done": False},
            })
            await bus.publish({
                "channel_id": f"compute.stream.{inv2}",
                "data": {"invocation_id": inv2, "delta": "X", "done": False},
            })
            await bus.publish({
                "channel_id": f"compute.stream.{inv1}",
                "data": {"invocation_id": inv1, "delta": "B", "done": True,
                         "final_text": "AB"},
            })

            received = []
            for _ in range(3):
                received.append(await asyncio.wait_for(q.get(), timeout=1.0))
            return received, inv1, inv2

        received, inv1, inv2 = asyncio.run(run())
        # The prefix subscriber sees interleaved events from both invocations.
        assert len(received) == 3
        invocation_ids = [r["data"]["invocation_id"] for r in received]
        assert inv1 in invocation_ids and inv2 in invocation_ids

    def test_non_stream_subscriber_does_not_receive_deltas(self):
        """A subscriber listening to content.* must NOT receive stream
        events — the protocol keeps the two channel namespaces disjoint."""
        async def run():
            bus = EventBus()
            content_q = bus.subscribe("content.")  # prefix

            await bus.publish({
                "channel_id": "compute.stream.abc123",
                "data": {"delta": "hello", "done": True, "final_text": "hello"},
            })
            # Give the bus a tick to deliver.
            await asyncio.sleep(0.01)
            return content_q.qsize()

        assert asyncio.run(run()) == 0


# ── Compute-runner helper (publishes deltas during stream_complete) ──

class TestComputeRunnerStreamPublish:
    """The compute-runner publishes each delta from the provider to the
    event bus as it arrives. Tested with the simulate helper so no live
    LLM API is needed."""

    def test_runner_publishes_each_delta_then_terminal(self):
        async def run():
            from termin_runtime.compute_runner import publish_stream_deltas

            bus = EventBus()
            provider = AIProvider({"ai_provider": {}})
            inv_id = "test-inv-1"
            channel = f"compute.stream.{inv_id}"
            q = bus.subscribe(channel)

            # Drive the helper with scripted deltas.
            deltas = ["one", " two", " three"]
            final_text = await publish_stream_deltas(
                bus, inv_id, "my_compute",
                provider.simulate_stream(deltas),
            )
            return q, final_text

        async def collect():
            q, final_text = await run()
            received = []
            # Drain without blocking.
            while not q.empty():
                received.append(q.get_nowait())
            return received, final_text

        received, final_text = asyncio.run(collect())
        # simulate_stream yields exactly one event per input delta, with
        # done=True on the last — so 3 inputs -> 3 events. The last event
        # carries both the final delta content AND the terminal signal,
        # which matches the protocol's shape: the last delta IS the
        # terminal. The separate-empty-terminal shape is also valid
        # (per the protocol doc); publish_stream_deltas emits one if the
        # generator exits without signalling done.
        assert len(received) == 3
        assert [r["data"]["delta"] for r in received] == \
            ["one", " two", " three"]
        # Only the last has done=True.
        assert [r["data"]["done"] for r in received] == [False, False, True]
        # The terminal carries final_text.
        assert received[-1]["data"]["final_text"] == "one two three"
        assert final_text == "one two three"
