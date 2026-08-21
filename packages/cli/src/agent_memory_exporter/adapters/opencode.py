"""OpenCode data source adapter.

Reads session metadata and conversation events from the OpenCode local SQLite
database at ~/.local/share/opencode/opencode.db.

Data model:
  - session table: id, directory, title, model, agent, time_created, time_updated
  - message table: id, session_id, time_created, data (JSON with role/time/agent/model)
  - part table: id, message_id, session_id, time_created, data (JSON with type/text/tool)

Mapping to RawSession events:
  - message.data.role == "user"  → turn boundary (user message)
  - message.data.role == "assistant" → assistant event
  - part.data.type == "text"      → assistant content (within assistant message)
  - part.data.type == "tool"       → tool event (tool name, input, output)
  - part.data.type == "step-start" → ignored (metadata)
  - part.data.type == "step-finish" → ignored (metadata)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_memory_shared.models import (
    Event,
    EventRole,
    Gap,
    GapType,
    RawSession,
    SessionMeta,
    SessionRef,
)

from agent_memory_exporter.assembler import assemble_turns
from agent_memory_exporter.gap_detector import detect_gaps_in_events


def _ms_to_datetime(ms: int) -> datetime:
    """Convert Unix epoch milliseconds to datetime."""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def _extract_user_text(message_data: dict[str, Any], parts: list[dict[str, Any]]) -> str:
    """Extract user message text from message data and its parts.

    OpenCode user messages may store text in parts (type=text) or not at all
    (the session_input table has the prompt, but message data may lack it).
    We check parts first, then fall back to session_input.
    """
    for part in parts:
        pdata = part.get("_data_parsed", {})
        if pdata.get("type") == "text" and pdata.get("text"):
            return str(pdata["text"])
    return ""


class OpenCodeAdapter:
    """Read-only adapter for OpenCode local data."""

    def __init__(self, home_dir: str | Path | None = None) -> None:
        if home_dir is None:
            home_dir = Path.home()
        self.home_dir = Path(home_dir)
        self.db_path = self.home_dir / ".local" / "share" / "opencode" / "opencode.db"

    @property
    def name(self) -> str:
        return "opencode"

    def detect(self) -> bool:
        return self.db_path.exists()

    def _connect_db(self) -> sqlite3.Connection:
        """Open a read-only connection to opencode.db."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def list_sessions(self, since: datetime | None = None) -> list[SessionRef]:
        """List sessions from the DB, optionally filtered by time_updated > since."""
        if not self.db_path.exists():
            return []

        conn = self._connect_db()
        try:
            if since is not None:
                since_ms = int(since.timestamp() * 1000)
                cursor = conn.execute(
                    """
                    SELECT id, directory, title, time_created, time_updated
                    FROM session
                    WHERE time_archived IS NULL AND time_updated > ?
                    ORDER BY time_updated ASC
                    """,
                    (since_ms,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT id, directory, title, time_created, time_updated
                    FROM session
                    WHERE time_archived IS NULL
                    ORDER BY time_updated ASC
                    """,
                )

            refs: list[SessionRef] = []
            for row in cursor.fetchall():
                refs.append(
                    SessionRef(
                        session_id=row["id"],
                        updated_at=_ms_to_datetime(row["time_updated"]),
                        cwd=row["directory"],
                        title=row["title"],
                    )
                )
            return refs
        finally:
            conn.close()

    def read_session(self, session_id: str) -> RawSession:
        """Read a session: DB metadata + message/part events → assembled RawSession."""
        meta = self._read_session_meta(session_id)

        events, gaps = self._read_events(session_id)

        # Sort by time_created
        events.sort(key=lambda e: e["timestamp_dt"])

        # Detect gaps
        all_gaps = gaps + detect_gaps_in_events(events)

        # Assemble turns
        turns = assemble_turns(events)

        # Update meta counts
        meta.turn_count = len([t for t in turns if t.turn_index >= 0])
        meta.event_count = len(events)

        return RawSession(
            session_id=session_id,
            source="opencode",
            session_meta=meta,
            turns=turns,
            gaps=all_gaps,
        )

    def _read_session_meta(self, session_id: str) -> SessionMeta:
        """Read session metadata from the database."""
        conn = self._connect_db()
        try:
            cursor = conn.execute(
                """
                SELECT id, directory, title, model, agent, time_created, time_updated
                FROM session
                WHERE id = ?
                """,
                (session_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return SessionMeta()

            # model is stored as JSON like {"id":"GLM-5.2","providerID":"tengen"}
            model_str = row["model"] or ""
            try:
                model_data = json.loads(model_str)
                model_name = model_data.get("id", model_str)
            except (json.JSONDecodeError, TypeError):
                model_name = model_str

            return SessionMeta(
                title=row["title"],
                cwd=row["directory"],
                model=model_name,
                mode=row["agent"],
                created_at=_ms_to_datetime(row["time_created"]),
                updated_at=_ms_to_datetime(row["time_updated"]),
            )
        finally:
            conn.close()

    def _read_events(
        self, session_id: str
    ) -> tuple[list[dict[str, Any]], list[Gap]]:
        """Read messages and parts for a session, returning raw event dicts.

        Each event dict has at least:
          - type: "message" | "tool"
          - role: "user" | "assistant" (for messages)
          - timestamp_dt: datetime
          - content: str (for text parts)
          - name: str (for tool parts)
          - arguments: str (for tool parts)
          - result: str (for tool parts)
          - id: str
        """
        events: list[dict[str, Any]] = []
        gaps: list[Gap] = []

        conn = self._connect_db()
        try:
            # Read all messages for this session, ordered by time_created
            cursor = conn.execute(
                """
                SELECT id, time_created, data
                FROM message
                WHERE session_id = ?
                ORDER BY time_created
                """,
                (session_id,),
            )
            messages = cursor.fetchall()

            for msg_row in messages:
                msg_id = msg_row["id"]
                msg_time = msg_row["time_created"]

                try:
                    msg_data = json.loads(msg_row["data"])
                except (json.JSONDecodeError, TypeError) as e:
                    gaps.append(
                        Gap(
                            gap_type=GapType.PARSE_ERROR,
                            details=f"message {msg_id}: bad JSON: {e}",
                            event_id=msg_id,
                        )
                    )
                    continue

                role = msg_data.get("role", "")

                # Read parts for this message
                part_cursor = conn.execute(
                    """
                    SELECT id, time_created, data
                    FROM part
                    WHERE message_id = ?
                    ORDER BY time_created
                    """,
                    (msg_id,),
                )
                parts = part_cursor.fetchall()

                if role == "user":
                    # User message: extract text from parts
                    user_text = ""
                    for part_row in parts:
                        try:
                            pdata = json.loads(part_row["data"])
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if pdata.get("type") == "text" and pdata.get("text"):
                            user_text = str(pdata["text"])
                            break

                    # If no text in parts, try session_input
                    if not user_text:
                        si_cursor = conn.execute(
                            """
                            SELECT prompt FROM session_input
                            WHERE session_id = ?
                            ORDER BY time_created
                            """,
                            (session_id,),
                        )
                        for si_row in si_cursor.fetchall():
                            if si_row["prompt"]:
                                user_text = si_row["prompt"]
                                break

                    events.append(
                        {
                            "id": msg_id,
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": user_text}],
                            "timestamp_dt": _ms_to_datetime(msg_time),
                        }
                    )

                elif role == "assistant":
                    # Assistant message: collect text and tool parts
                    for part_row in parts:
                        try:
                            pdata = json.loads(part_row["data"])
                        except (json.JSONDecodeError, TypeError) as e:
                            gaps.append(
                                Gap(
                                    gap_type=GapType.PARSE_ERROR,
                                    details=f"part {part_row['id']}: bad JSON: {e}",
                                    event_id=part_row["id"],
                                )
                            )
                            continue

                        part_type = pdata.get("type", "")
                        part_time = part_row["time_created"]

                        if part_type == "text":
                            events.append(
                                {
                                    "id": part_row["id"],
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": str(pdata.get("text", ""))}],
                                    "timestamp_dt": _ms_to_datetime(part_time),
                                }
                            )

                        elif part_type == "tool":
                            tool_name = pdata.get("tool", "")
                            call_id = pdata.get("callID", "")
                            state = pdata.get("state", {})
                            tool_input = state.get("input", {})
                            tool_output = state.get("output", "")

                            # Serialize input/output to strings
                            if isinstance(tool_input, dict):
                                tool_input_str = json.dumps(tool_input, ensure_ascii=False)
                            else:
                                tool_input_str = str(tool_input) if tool_input else ""

                            if isinstance(tool_output, (dict, list)):
                                tool_output_str = json.dumps(tool_output, ensure_ascii=False)
                            else:
                                tool_output_str = str(tool_output) if tool_output else ""

                            events.append(
                                {
                                    "id": part_row["id"],
                                    "type": "function_call",
                                    "name": tool_name,
                                    "callId": call_id,
                                    "arguments": tool_input_str,
                                    "timestamp_dt": _ms_to_datetime(part_time),
                                }
                            )
                            # Also emit the result (tool state has both input and output)
                            if tool_output_str:
                                events.append(
                                    {
                                        "id": f"{part_row['id']}-result",
                                        "type": "function_call_result",
                                        "callId": call_id,
                                        "parentId": part_row["id"],
                                        "result": tool_output_str,
                                        "timestamp_dt": _ms_to_datetime(part_time + 1),
                                    }
                                )

                        # step-start, step-finish: ignored (metadata only)

        finally:
            conn.close()

        return events, gaps
