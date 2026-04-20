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

# ── Partial-JSON field extractor (unit tests) ───────────────────────

class TestStreamingFieldExtractor:
    """The regex-based partial-JSON extractor emits per-string-field
    deltas as chunks arrive. It underpins stream_agent_response.
    """

    def _feed(self, chunks):
        """Feed a sequence of JSON chunks and return all emitted events."""
        from termin_runtime.ai_provider import StreamingJsonFieldExtractor
        ex = StreamingJsonFieldExtractor()
        events = []
        for c in chunks:
            events.extend(ex.feed(c))
        events.extend(ex.finish())
        return events

    def test_single_string_field_streams_char_by_char(self):
        # set_output {"message": "Hello, world."}
        events = self._feed([
            '{"', 'message', '": "', 'Hello', ', ', 'world', '."', '}',
        ])
        # Expect at least one field_delta per non-empty text chunk
        # and a field_done at the end.
        deltas = [e for e in events if e["type"] == "field_delta"
                  and e["field"] == "message"]
        assert deltas, events
        assembled = "".join(e["delta"] for e in deltas)
        assert assembled == "Hello, world."
        done_events = [e for e in events if e["type"] == "field_done"
                       and e["field"] == "message"]
        assert len(done_events) == 1
        assert done_events[0]["value"] == "Hello, world."

    def test_two_string_fields_stream_in_order(self):
        events = self._feed([
            '{"message": "hi", "note": "there"}',
        ])
        msg_deltas = [e for e in events if e["type"] == "field_delta"
                      and e["field"] == "message"]
        note_deltas = [e for e in events if e["type"] == "field_delta"
                       and e["field"] == "note"]
        assert "".join(d["delta"] for d in msg_deltas) == "hi"
        assert "".join(d["delta"] for d in note_deltas) == "there"
        # Each field gets exactly one done event.
        assert [e for e in events if e["type"] == "field_done"
                and e["field"] == "message"][0]["value"] == "hi"
        assert [e for e in events if e["type"] == "field_done"
                and e["field"] == "note"][0]["value"] == "there"

    def test_non_string_field_not_streamed_chars_but_emitted_at_finish(self):
        """Numeric/boolean fields can't stream meaningfully — they land
        as a single field_done event at finish() with the parsed value."""
        events = self._feed([
            '{"message": "hi", "confidence": 0.92}',
        ])
        # Confidence shouldn't appear in field_delta events.
        conf_deltas = [e for e in events if e["type"] == "field_delta"
                       and e["field"] == "confidence"]
        assert conf_deltas == []
        # It should appear once in field_done at finish().
        conf_done = [e for e in events if e["type"] == "field_done"
                     and e["field"] == "confidence"]
        assert len(conf_done) == 1
        assert conf_done[0]["value"] == 0.92

    def test_escaped_quote_in_string_does_not_close_field(self):
        """A backslash-escaped quote inside a string value must not be
        mistaken for the end-of-string terminator."""
        events = self._feed([
            '{"message": "she said \\"hi\\""}',
        ])
        msg_done = [e for e in events if e["type"] == "field_done"
                    and e["field"] == "message"]
        assert len(msg_done) == 1
        assert msg_done[0]["value"] == 'she said "hi"'


# ── Real provider streaming (mocked SDK clients) ────────────────────

class TestAnthropicStreamComplete:
    """AIProvider.stream_complete() wired to an Anthropic client.

    These tests mock the Anthropic SDK's messages.stream context manager
    so we exercise the sync-iterator-to-async-generator bridge without
    a live API. Live-API verification is a manual step.
    """

    def _make_provider_with_mock(self, deltas, raise_at=None):
        """Build an AIProvider with service='anthropic' and a mock client
        whose messages.stream yields the given deltas. If raise_at is set
        (an int index), the mock raises at that chunk."""
        provider = AIProvider({"ai_provider": {
            "service": "anthropic", "model": "claude-test",
            "api_key": "sk-fake",
        }})
        provider._service = "anthropic"

        class _MockStream:
            def __init__(self, chunks):
                self._chunks = chunks
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
            @property
            def text_stream(self):
                for i, c in enumerate(self._chunks):
                    if raise_at is not None and i == raise_at:
                        raise RuntimeError("simulated upstream error")
                    yield c

        class _MockMessages:
            def stream(self_, **kwargs):
                return _MockStream(deltas)

        class _MockClient:
            def __init__(self_):
                self_.messages = _MockMessages()

        provider._client = _MockClient()
        return provider

    def test_yields_each_delta_with_final_marked_done(self):
        async def run():
            provider = self._make_provider_with_mock(
                ["Hello", ", ", "world", "."])
            events = []
            async for delta, done in provider.stream_complete(
                    "be friendly", "say hi"):
                events.append((delta, done))
            return events
        events = asyncio.run(run())
        assert events == [
            ("Hello", False),
            (", ", False),
            ("world", False),
            (".", True),
        ]

    def test_empty_response_yields_single_empty_terminal(self):
        async def run():
            provider = self._make_provider_with_mock([])
            events = []
            async for delta, done in provider.stream_complete("s", "u"):
                events.append((delta, done))
            return events
        assert asyncio.run(run()) == [("", True)]

    def test_single_chunk_yields_one_terminal_event(self):
        async def run():
            provider = self._make_provider_with_mock(["just one"])
            events = []
            async for delta, done in provider.stream_complete("s", "u"):
                events.append((delta, done))
            return events
        assert asyncio.run(run()) == [("just one", True)]

    def test_mid_stream_error_raises_ai_provider_error(self):
        from termin_runtime.ai_provider import AIProviderError
        async def run():
            provider = self._make_provider_with_mock(
                ["A", "B", "C"], raise_at=1)
            events = []
            async for item in provider.stream_complete("s", "u"):
                events.append(item)
            return events
        with pytest.raises(AIProviderError):
            asyncio.run(run())


class TestOpenAIStreamComplete:
    """AIProvider.stream_complete() wired to an OpenAI client.

    OpenAI chunks expose .choices[0].delta.content, which may be None
    (role-only chunk at stream start) or a string. We filter None and
    preserve ordering of string deltas. Same sync-to-async bridge
    pattern as Anthropic.
    """

    def _make_provider_with_mock(self, delta_contents):
        """Build an AIProvider with service='openai'. delta_contents is
        a list where None simulates a role-only chunk with no text."""
        provider = AIProvider({"ai_provider": {
            "service": "openai", "model": "gpt-test", "api_key": "sk-fake",
        }})
        provider._service = "openai"

        class _MockDelta:
            def __init__(self, content): self.content = content
        class _MockChoice:
            def __init__(self, content): self.delta = _MockDelta(content)
        class _MockChunk:
            def __init__(self, content): self.choices = [_MockChoice(content)]

        class _MockCompletions:
            def create(self_, **kwargs):
                # stream=True -> iterator of chunks
                return iter([_MockChunk(c) for c in delta_contents])
        class _MockChat:
            def __init__(self_): self_.completions = _MockCompletions()
        class _MockClient:
            def __init__(self_): self_.chat = _MockChat()

        provider._client = _MockClient()
        return provider

    def test_yields_each_delta_with_final_marked_done(self):
        async def run():
            # None chunk at the start (role-only opening frame) is filtered.
            provider = self._make_provider_with_mock(
                [None, "Hello", ", ", "world", "."])
            events = []
            async for delta, done in provider.stream_complete("s", "u"):
                events.append((delta, done))
            return events
        events = asyncio.run(run())
        # The None chunk is filtered; four text chunks remain.
        assert events == [
            ("Hello", False),
            (", ", False),
            ("world", False),
            (".", True),
        ]

    def test_all_none_chunks_yields_single_empty_terminal(self):
        async def run():
            provider = self._make_provider_with_mock([None, None])
            events = []
            async for delta, done in provider.stream_complete("s", "u"):
                events.append((delta, done))
            return events
        assert asyncio.run(run()) == [("", True)]


class TestAnthropicStreamAgentResponse:
    """stream_agent_response wraps client.messages.stream() with a tool
    argument (set_output) and parses input_json_delta events into
    field-level deltas."""

    def _make_provider_with_mock_events(self, events_to_emit):
        """Build an AIProvider whose messages.stream yields the given
        event objects when iterated."""
        provider = AIProvider({"ai_provider": {
            "service": "anthropic", "model": "claude-test", "api_key": "sk-fake",
        }})
        provider._service = "anthropic"

        class _MockStream:
            def __init__(self, events):
                self._events = events
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
            def __iter__(self):
                for e in self._events:
                    yield e

        class _MockMessages:
            def stream(self_, **kwargs):
                return _MockStream(events_to_emit)

        class _MockClient:
            messages = _MockMessages()

        provider._client = _MockClient()
        return provider

    def _evt(self, event_type, **kwargs):
        """Build a SimpleNamespace with attribute access matching the
        Anthropic SDK's stream event shape."""
        from types import SimpleNamespace
        return SimpleNamespace(type=event_type, **kwargs)

    def _input_json_delta(self, partial):
        from types import SimpleNamespace
        return self._evt(
            "content_block_delta",
            delta=SimpleNamespace(type="input_json_delta", partial_json=partial),
        )

    def test_agent_chat_response_streams_message_field(self):
        """Agent produces set_output({message: "Hello, world."}). The
        stream_agent_response yields field_delta events for 'message'
        followed by a field_done and a top-level done."""
        from types import SimpleNamespace
        events = [
            self._evt("content_block_start",
                      content_block=SimpleNamespace(type="tool_use",
                                                     name="set_output")),
            self._input_json_delta('{"message": "'),
            self._input_json_delta('Hello'),
            self._input_json_delta(', world'),
            self._input_json_delta('."}'),
            self._evt("content_block_stop"),
            self._evt("message_stop"),
        ]

        async def run():
            provider = self._make_provider_with_mock_events(events)
            output_tool = {"name": "set_output",
                           "input_schema": {"type": "object",
                                            "properties": {"message": {"type": "string"}}}}
            emitted = []
            async for e in provider.stream_agent_response(
                    "be brief", "greet", output_tool):
                emitted.append(e)
            return emitted

        emitted = asyncio.run(run())
        # Extract message field deltas
        message_deltas = [e for e in emitted
                          if e["type"] == "field_delta"
                          and e["field"] == "message"]
        assert message_deltas, emitted
        assembled = "".join(d["delta"] for d in message_deltas)
        assert assembled == "Hello, world."

        # One field_done for message with final value
        msg_done = [e for e in emitted
                    if e["type"] == "field_done"
                    and e["field"] == "message"]
        assert len(msg_done) == 1
        assert msg_done[0]["value"] == "Hello, world."

        # One final 'done' event carrying the full output dict
        final = [e for e in emitted if e["type"] == "done"]
        assert len(final) == 1
        assert final[0]["output"] == {"message": "Hello, world."}


class TestAgentStreamPublisher:
    """publish_agent_stream_events pumps field-level event dicts onto
    the tool-use stream channels. Subscribers to a specific field get
    only that field's events; subscribers to the base channel get the
    invocation-done event."""

    def test_per_field_and_base_channels_populated(self):
        async def run():
            from termin_runtime.compute_runner import publish_agent_stream_events

            bus = EventBus()
            inv_id = "inv-agent-1"
            field_channel = f"compute.stream.{inv_id}.field.message"
            base_channel = f"compute.stream.{inv_id}"
            msg_q = bus.subscribe(field_channel)
            base_q = bus.subscribe(base_channel)

            # Scripted events matching what stream_agent_response yields.
            async def events():
                yield {"type": "field_delta", "field": "message", "delta": "Hi"}
                yield {"type": "field_delta", "field": "message", "delta": " there"}
                yield {"type": "field_done", "field": "message", "value": "Hi there"}
                yield {"type": "done", "output": {"message": "Hi there"}}

            output = await publish_agent_stream_events(
                bus, inv_id, "greet", events())

            msg_events = []
            base_events = []
            while not msg_q.empty():
                msg_events.append(msg_q.get_nowait())
            while not base_q.empty():
                base_events.append(base_q.get_nowait())
            return output, msg_events, base_events, base_channel

        output, msg_events, base_events, base_channel = asyncio.run(run())
        # Field channel: 2 deltas + 1 done
        # Base channel: field-channel events DO bubble up because prefix
        # matching in EventBus subscribes via startswith().
        # But the base channel subscription should also include the
        # invocation-done event.
        assert len(msg_events) == 3
        deltas = [e for e in msg_events if not e["data"]["done"]]
        dones = [e for e in msg_events if e["data"]["done"]]
        assert len(deltas) == 2
        assert len(dones) == 1
        assert "".join(d["data"]["delta"] for d in deltas) == "Hi there"
        assert dones[0]["data"]["value"] == "Hi there"

        # Base subscriber sees ALL matching events (prefix: "compute.stream.inv-agent-1")
        # including the 3 field events and the 1 invocation-done event.
        assert len(base_events) == 4
        invocation_done = [e for e in base_events
                           if e["channel_id"] == base_channel]
        assert len(invocation_done) == 1
        assert invocation_done[0]["data"]["output"] == {"message": "Hi there"}

        # Return value from the publisher is the final output dict.
        assert output == {"message": "Hi there"}


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
