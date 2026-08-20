"""Gap detection: identify data integrity issues in raw events."""

from __future__ import annotations

from typing import Any

from agent_memory_shared.models import Gap, GapType


def detect_gaps_in_events(events: list[dict[str, Any]]) -> list[Gap]:
    """Detect data integrity issues in a list of raw events.

    Checks:
    - MISSING_FUNCTION_RESULT: function_call without a matching function_call_result
    - TIMESTAMP_DISORDER: events not in non-decreasing timestamp order
    - EMPTY_SESSION: no events at all
    """
    gaps: list[Gap] = []

    if not events:
        gaps.append(Gap(gap_type=GapType.EMPTY_SESSION, details="No events found in session."))
        return gaps

    # Check timestamp ordering
    prev_ts = None
    for evt in events:
        ts = evt.get("timestamp_dt")
        if ts is None:
            continue
        if prev_ts is not None and ts < prev_ts:
            gaps.append(
                Gap(
                    gap_type=GapType.TIMESTAMP_DISORDER,
                    details=f"Event {evt.get('id', '?')} timestamp {ts} is before previous {prev_ts}",
                    event_id=evt.get("id"),
                )
            )
        prev_ts = ts

    # Check function_call → function_call_result matching
    call_ids: set[str] = set()
    call_event_ids: set[str] = set()
    result_call_ids: set[str] = set()
    result_parent_ids: set[str] = set()

    for evt in events:
        evt_type = evt.get("type", "")
        if evt_type == "function_call":
            if evt.get("callId"):
                call_ids.add(evt["callId"])
            if evt.get("id"):
                call_event_ids.add(evt["id"])
        elif evt_type == "function_call_result":
            if evt.get("callId"):
                result_call_ids.add(evt["callId"])
            if evt.get("parentId"):
                result_parent_ids.add(evt["parentId"])

    # function_calls without matching results
    for evt in events:
        if evt.get("type") != "function_call":
            continue
        call_id = evt.get("callId")
        evt_id = evt.get("id")
        has_result = False
        if call_id and call_id in result_call_ids:
            has_result = True
        if evt_id and evt_id in result_parent_ids:
            has_result = True
        if not has_result:
            gaps.append(
                Gap(
                    gap_type=GapType.MISSING_FUNCTION_RESULT,
                    details=f"function_call '{evt.get('name', '?')}' (id={evt_id}, callId={call_id}) has no matching result",
                    event_id=evt_id,
                )
            )

    return gaps
