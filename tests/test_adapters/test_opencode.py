"""Tests for the OpenCode adapter."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_memory_exporter.adapters.opencode import OpenCodeAdapter


@pytest.fixture
def opencode_db(tmp_path: Path) -> Path:
    """Create a minimal OpenCode-style SQLite DB for testing."""
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            directory TEXT NOT NULL,
            title TEXT NOT NULL,
            model TEXT,
            agent TEXT,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            time_archived INTEGER
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        CREATE TABLE session_input (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            prompt TEXT NOT NULL,
            delivery TEXT NOT NULL,
            admitted_seq INTEGER NOT NULL,
            promoted_seq INTEGER,
            time_created INTEGER NOT NULL
        );
    """)

    # Session 1: simple user → assistant conversation
    session_id = "ses_test_001"
    conn.execute(
        "INSERT INTO session (id, project_id, directory, title, model, agent, time_created, time_updated) VALUES (?,?,?,?,?,?,?,?)",
        (session_id, "proj_001", "D:\\test", "Test Session", '{"id":"GLM-5.2","providerID":"tengen"}', "build", 1787275316000, 1787275325000),
    )

    # User message
    user_msg_id = "msg_user_001"
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        (user_msg_id, session_id, 1787275316935, 1787275316935, json.dumps({"role": "user", "time": {"created": 1787275316935}, "agent": "build"})),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
        ("prt_user_001", user_msg_id, session_id, 1787275316936, 1787275316936, json.dumps({"type": "text", "text": "What is 2+2?"})),
    )

    # Assistant message with text + tool
    asst_msg_id = "msg_asst_001"
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
        (asst_msg_id, session_id, 1787275316968, 1787275324810, json.dumps({"role": "assistant", "time": {"created": 1787275316968, "completed": 1787275324810}, "agent": "build", "modelID": "GLM-5.2"})),
    )
    # step-start part (ignored)
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
        ("prt_step_start", asst_msg_id, session_id, 1787275322383, 1787275322383, json.dumps({"type": "step-start"})),
    )
    # text part (assistant response)
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
        ("prt_text_001", asst_msg_id, session_id, 1787275323957, 1787275323957, json.dumps({"type": "text", "text": "2+2 equals 4."})),
    )
    # tool part (function call + result)
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
        ("prt_tool_001", asst_msg_id, session_id, 1787275324000, 1787275324000, json.dumps({
            "type": "tool",
            "tool": "calculator",
            "callID": "call_test_001",
            "state": {
                "status": "completed",
                "input": {"expression": "2+2"},
                "output": "4",
            },
        })),
    )
    # step-finish part (ignored)
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?,?)",
        ("prt_step_finish", asst_msg_id, session_id, 1787275324801, 1787275324801, json.dumps({"type": "step-finish", "reason": "stop", "tokens": {"total": 100}})),
    )

    # Session 2: archived session (should be filtered out)
    conn.execute(
        "INSERT INTO session (id, project_id, directory, title, model, agent, time_created, time_updated, time_archived) VALUES (?,?,?,?,?,?,?,?,?)",
        ("ses_archived", "proj_001", "D:\\old", "Archived", None, "build", 1787000000000, 1787000001000, 1787000002000),
    )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def opencode_adapter(tmp_path: Path, opencode_db: Path) -> OpenCodeAdapter:
    """Create an OpenCodeAdapter with the test DB."""
    # Create the expected directory structure
    opencode_dir = tmp_path / ".local" / "share" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    db_path = opencode_dir / "opencode.db"
    opencode_db.rename(db_path)
    return OpenCodeAdapter(home_dir=tmp_path)


class TestOpenCodeAdapter:
    def test_detect_no_data(self, tmp_path):
        adapter = OpenCodeAdapter(home_dir=tmp_path)
        assert adapter.detect() is False

    def test_detect_with_data(self, opencode_adapter):
        assert opencode_adapter.detect() is True

    def test_list_sessions(self, opencode_adapter):
        sessions = opencode_adapter.list_sessions()
        assert len(sessions) == 1  # archived session filtered out
        assert sessions[0].session_id == "ses_test_001"
        assert sessions[0].cwd == "D:\\test"
        assert sessions[0].title == "Test Session"

    def test_list_sessions_with_since_filter(self, opencode_adapter):
        from datetime import datetime, timezone

        # time_updated for ses_test_001 is 1787275325000 ms = 2026-08-17
        since = datetime.fromtimestamp(1787275325000 / 1000.0, tz=timezone.utc)
        sessions = opencode_adapter.list_sessions(since=since)
        assert len(sessions) == 0  # nothing after this exact timestamp

        # slightly before
        since = datetime.fromtimestamp(1787275324999 / 1000.0, tz=timezone.utc)
        sessions = opencode_adapter.list_sessions(since=since)
        assert len(sessions) == 1

    def test_read_session(self, opencode_adapter):
        session = opencode_adapter.read_session("ses_test_001")

        assert session.session_id == "ses_test_001"
        assert session.source == "opencode"
        assert session.session_meta.title == "Test Session"
        assert session.session_meta.cwd == "D:\\test"
        assert session.session_meta.model == "GLM-5.2"
        assert session.session_meta.mode == "build"

        # Should have at least 1 user turn
        user_turns = [t for t in session.turns if t.turn_index >= 0]
        assert len(user_turns) >= 1
        assert "2+2" in user_turns[0].user_message

        # Turn 0 should have assistant text and tool events
        turn0 = user_turns[0]
        roles = [e.role.value for e in turn0.events]
        assert "assistant" in roles
        assert "tool" in roles

        # Tool event should have name, input, and output
        tool_events = [e for e in turn0.events if e.role.value == "tool"]
        assert len(tool_events) >= 1
        assert tool_events[0].tool_name == "calculator"
        assert tool_events[0].tool_input is not None
        assert "2+2" in tool_events[0].tool_input
        assert tool_events[0].tool_output is not None
        assert "4" in tool_events[0].tool_output

    def test_read_session_not_found(self, opencode_adapter):
        session = opencode_adapter.read_session("nonexistent")
        assert len(session.turns) == 0
        # No gaps either (no events to check)

    def test_archived_session_filtered(self, opencode_adapter):
        """Archived sessions should not appear in list_sessions."""
        sessions = opencode_adapter.list_sessions()
        session_ids = [s.session_id for s in sessions]
        assert "ses_archived" not in session_ids

    def test_name(self, opencode_adapter):
        assert opencode_adapter.name == "opencode"
