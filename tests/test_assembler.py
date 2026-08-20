"""Tests for the turn assembler."""

from __future__ import annotations

from agent_memory_exporter.assembler import assemble_turns


class TestAssembleTurns:
    def test_basic_two_turns(self, sample_events):
        """Two user messages → two turns."""
        # Add timestamp_dt for sorting
        from datetime import datetime, timezone

        events = []
        for evt in sample_events:
            evt = dict(evt)
            ts = evt["timestamp"]
            if ts > 1e12:
                ts = ts / 1000.0
            evt["timestamp_dt"] = datetime.fromtimestamp(ts, tz=timezone.utc)
            events.append(evt)

        turns = assemble_turns(events)

        # Should have 2 turns (turn_index 0 and 1)
        assert len(turns) == 2
        assert turns[0].turn_index == 0
        assert turns[0].user_message == "What is 2+2?"
        assert turns[1].turn_index == 1
        assert turns[1].user_message == "Thanks!"

    def test_turn_events(self, sample_events):
        """Turn 0 should have reasoning, tool, and assistant events."""
        from datetime import datetime, timezone

        events = []
        for evt in sample_events:
            evt = dict(evt)
            ts = evt["timestamp"]
            if ts > 1e12:
                ts = ts / 1000.0
            evt["timestamp_dt"] = datetime.fromtimestamp(ts, tz=timezone.utc)
            events.append(evt)

        turns = assemble_turns(events)

        # Turn 0: reasoning + tool + assistant
        turn0 = turns[0]
        assert len(turn0.events) == 3  # reasoning, tool, assistant
        assert turn0.events[0].role.value == "reasoning"
        assert turn0.events[1].role.value == "tool"
        assert turn0.events[1].tool_name == "calculator"
        assert turn0.events[1].tool_input == '{"expression": "2+2"}'
        assert turn0.events[1].tool_output == "4"  # matched from function_call_result
        assert turn0.events[2].role.value == "assistant"

    def test_preamble(self, sample_events_with_preamble):
        """Events before first user message go to turn_index=-1."""
        from datetime import datetime, timezone

        events = []
        for evt in sample_events_with_preamble:
            evt = dict(evt)
            ts = evt["timestamp"]
            if ts > 1e12:
                ts = ts / 1000.0
            evt["timestamp_dt"] = datetime.fromtimestamp(ts, tz=timezone.utc)
            events.append(evt)

        turns = assemble_turns(events)

        # Should have 2 turns: preamble (-1) and first user turn (0)
        assert len(turns) == 2
        assert turns[0].turn_index == -1
        assert turns[0].user_message == ""
        assert turns[0].user_timestamp is None
        assert len(turns[0].events) == 1  # file-history-snapshot
        assert turns[1].turn_index == 0
        assert turns[1].user_message == "Hello"

    def test_tool_output_matching(self, sample_events):
        """function_call_result should be matched to its function_call."""
        from datetime import datetime, timezone

        events = []
        for evt in sample_events:
            evt = dict(evt)
            ts = evt["timestamp"]
            if ts > 1e12:
                ts = ts / 1000.0
            evt["timestamp_dt"] = datetime.fromtimestamp(ts, tz=timezone.utc)
            events.append(evt)

        turns = assemble_turns(events)
        tool_events = [e for e in turns[0].events if e.role.value == "tool"]
        assert len(tool_events) == 1
        assert tool_events[0].tool_output == "4"

    def test_empty_events(self):
        """Empty event list → empty turns."""
        turns = assemble_turns([])
        assert turns == []

    def test_only_user_message(self):
        """Single user message → single turn with no events."""
        from datetime import datetime, timezone

        events = [
            {
                "id": "msg-1",
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Hello"}],
                "timestamp_dt": datetime.fromtimestamp(1781675942, tz=timezone.utc),
            }
        ]
        turns = assemble_turns(events)
        assert len(turns) == 1
        assert turns[0].turn_index == 0
        assert turns[0].user_message == "Hello"
        assert len(turns[0].events) == 0
