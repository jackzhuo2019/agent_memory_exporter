"""Tests for the gap detector."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_memory_exporter.gap_detector import detect_gaps_in_events


def _ts(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


class TestGapDetector:
    def test_empty_session(self):
        gaps = detect_gaps_in_events([])
        assert len(gaps) == 1
        assert gaps[0].gap_type.value == "empty_session"

    def test_no_gaps(self):
        events = [
            {"id": "m1", "type": "message", "role": "user", "timestamp_dt": _ts(1000)},
            {"id": "c1", "type": "function_call", "callId": "call-1", "name": "Read", "timestamp_dt": _ts(1001)},
            {"id": "r1", "type": "function_call_result", "callId": "call-1", "parentId": "c1", "result": "ok", "timestamp_dt": _ts(1002)},
            {"id": "m2", "type": "message", "role": "assistant", "timestamp_dt": _ts(1003)},
        ]
        gaps = detect_gaps_in_events(events)
        assert len(gaps) == 0

    def test_missing_function_result(self):
        events = [
            {"id": "m1", "type": "message", "role": "user", "timestamp_dt": _ts(1000)},
            {"id": "c1", "type": "function_call", "callId": "call-1", "name": "Read", "timestamp_dt": _ts(1001)},
            {"id": "m2", "type": "message", "role": "assistant", "timestamp_dt": _ts(1002)},
        ]
        gaps = detect_gaps_in_events(events)
        assert len(gaps) == 1
        assert gaps[0].gap_type.value == "missing_function_result"
        assert gaps[0].event_id == "c1"

    def test_timestamp_disorder(self):
        events = [
            {"id": "m1", "type": "message", "role": "user", "timestamp_dt": _ts(1000)},
            {"id": "m2", "type": "message", "role": "assistant", "timestamp_dt": _ts(500)},  # earlier!
            {"id": "m3", "type": "message", "role": "assistant", "timestamp_dt": _ts(1001)},
        ]
        gaps = detect_gaps_in_events(events)
        disorder_gaps = [g for g in gaps if g.gap_type.value == "timestamp_disorder"]
        assert len(disorder_gaps) == 1
        assert disorder_gaps[0].event_id == "m2"

    def test_result_matched_by_parent_id(self):
        """function_call_result can match by parentId even without callId."""
        events = [
            {"id": "m1", "type": "message", "role": "user", "timestamp_dt": _ts(1000)},
            {"id": "c1", "type": "function_call", "callId": None, "name": "Read", "timestamp_dt": _ts(1001)},
            {"id": "r1", "type": "function_call_result", "callId": None, "parentId": "c1", "result": "ok", "timestamp_dt": _ts(1002)},
        ]
        gaps = detect_gaps_in_events(events)
        missing_result = [g for g in gaps if g.gap_type.value == "missing_function_result"]
        assert len(missing_result) == 0
