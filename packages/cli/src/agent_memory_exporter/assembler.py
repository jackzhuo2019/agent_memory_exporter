"""Turn assembly: reassemble raw events into user↔assistant turns.

A turn is defined as: one user message + all subsequent events until the
next user message. Events before the first user message are stored as a
preamble turn (turn_index = -1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agent_memory_shared.models import Event, EventRole, Turn


def _extract_text(content: Any) -> str:
    """Extract plain text from a message content field."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)


def _make_event(evt: dict[str, Any]) -> Event:
    """Convert a raw jsonl event dict into an Event model."""
    evt_type = evt.get("type", "")
    ts: datetime = evt.get("timestamp_dt") or datetime.fromtimestamp(0, tz=None)

    is_sub_agent = evt.get("_is_sub_agent", False)
    sub_agent_id = evt.get("_sub_agent_session_id") if is_sub_agent else None

    if evt_type == "message":
        role_str = evt.get("role", "")
        if is_sub_agent:
            # Sub-agent messages are treated as assistant events
            return Event(
                role=EventRole.SUB_AGENT,
                content=_extract_text(evt.get("content")),
                timestamp=ts,
                sub_agent_id=sub_agent_id,
            )
        if role_str == "user":
            # User messages are turn boundaries, not events within a turn.
            # This function should not be called for user messages;
            # the assembler handles them separately.
            # But just in case, return as assistant (shouldn't happen).
            return Event(
                role=EventRole.ASSISTANT,
                content=_extract_text(evt.get("content")),
                timestamp=ts,
            )
        # assistant
        return Event(
            role=EventRole.ASSISTANT,
            content=_extract_text(evt.get("content")),
            timestamp=ts,
        )

    if evt_type == "function_call":
        return Event(
            role=EventRole.TOOL,
            content="",
            tool_name=evt.get("name", ""),
            tool_input=evt.get("arguments", ""),
            tool_output=None,  # Will be filled by matching function_call_result
            timestamp=ts,
        )

    if evt_type == "function_call_result":
        # This is matched to a preceding function_call by the assembler,
        # not emitted as a standalone event.
        # However, if we encounter an orphan result, emit it as a tool event
        # with tool_output set and no tool_name.
        return Event(
            role=EventRole.TOOL,
            content="",
            tool_name=None,
            tool_input=None,
            tool_output=str(evt.get("result", "")),
            timestamp=ts,
        )

    if evt_type == "reasoning":
        return Event(
            role=EventRole.REASONING,
            content=_extract_text(evt.get("content")) or str(evt.get("providerData", {}).get("reasoning", "")),
            timestamp=ts,
        )

    if evt_type == "file-history-snapshot":
        return Event(
            role=EventRole.FILE_SNAPSHOT,
            content="",  # Snapshots are large; skip content by default
            timestamp=ts,
        )

    # Unknown event type — treat as assistant with raw content
    return Event(
        role=EventRole.ASSISTANT,
        content=_extract_text(evt.get("content")),
        timestamp=ts,
    )


def _match_tool_results(events: list[dict[str, Any]]) -> None:
    """Match function_call_result events to their parent function_call events.

    Modifies events in-place: sets '_tool_output' on matching function_call events.
    """
    # Build a map from event id → function_call_result text
    result_by_call_id: dict[str, str] = {}
    result_by_parent_id: dict[str, str] = {}

    for evt in events:
        if evt.get("type") == "function_call_result":
            result_text = str(evt.get("result", ""))
            call_id = evt.get("callId")
            parent_id = evt.get("parentId")
            if call_id:
                result_by_call_id[call_id] = result_text
            if parent_id:
                result_by_parent_id[parent_id] = result_text

    # Attach results to function_call events
    for evt in events:
        if evt.get("type") == "function_call":
            call_id = evt.get("callId")
            evt_id = evt.get("id")
            if call_id and call_id in result_by_call_id:
                evt["_tool_output"] = result_by_call_id[call_id]
            elif evt_id and evt_id in result_by_parent_id:
                evt["_tool_output"] = result_by_parent_id[evt_id]


def assemble_turns(events: list[dict[str, Any]]) -> list[Turn]:
    """Assemble raw events into a list of Turn objects.

    Turn boundary: each 'message' event with role='user' starts a new turn.
    Events before the first user message go into a preamble turn (turn_index=-1).

    Args:
        events: List of raw event dicts, each with at least 'type', 'timestamp_dt'.
                Should be sorted by timestamp ascending.

    Returns:
        List of Turn objects, ordered by turn_index.
    """
    # Match function_call → function_call_result
    _match_tool_results(events)

    turns: list[Turn] = []
    current_turn: Turn | None = None
    turn_index = -1  # -1 for preamble

    for evt in events:
        evt_type = evt.get("type", "")
        is_sub_agent = evt.get("_is_sub_agent", False)

        # Check for user message (turn boundary) — only for main conversation, not sub-agents
        if evt_type == "message" and evt.get("role") == "user" and not is_sub_agent:
            # Start a new turn
            turn_index += 1
            if turn_index == 0:
                current_turn = Turn(
                    turn_index=0,
                    user_message=_extract_text(evt.get("content")),
                    user_timestamp=evt.get("timestamp_dt"),
                    events=[],
                )
            else:
                current_turn = Turn(
                    turn_index=turn_index,
                    user_message=_extract_text(evt.get("content")),
                    user_timestamp=evt.get("timestamp_dt"),
                    events=[],
                )
            turns.append(current_turn)
            continue

        # For function_call_result events that were already matched, skip them
        # (they are attached to their parent function_call via _tool_output)
        if evt_type == "function_call_result":
            # Only emit as standalone event if it's an orphan (no matching function_call)
            call_id = evt.get("callId")
            parent_id = evt.get("parentId")
            matched = False
            if call_id:
                for other in events:
                    if (
                        other.get("type") == "function_call"
                        and other.get("callId") == call_id
                    ):
                        matched = True
                        break
            if not matched and parent_id:
                for other in events:
                    if (
                        other.get("type") == "function_call"
                        and other.get("id") == parent_id
                    ):
                        matched = True
                        break
            if matched:
                continue  # Skip — already attached to function_call

        # All other events go into the current turn
        if current_turn is None:
            # Preamble: events before first user message
            current_turn = Turn(
                turn_index=-1,
                user_message="",
                user_timestamp=None,
                events=[],
            )
            turns.append(current_turn)

        # Build Event from raw dict
        event_obj = _make_event(evt)

        # Attach matched tool output
        if evt_type == "function_call" and "_tool_output" in evt:
            event_obj.tool_output = evt["_tool_output"]

        current_turn.events.append(event_obj)

    return turns
