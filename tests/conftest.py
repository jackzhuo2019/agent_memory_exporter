"""Shared test fixtures for agent-memory-exporter tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def sample_events() -> list[dict]:
    """A minimal set of raw events for testing the assembler.

    Represents a simple conversation:
    - user asks a question
    - assistant reasons, calls a tool, gets result, responds
    - user asks a follow-up
    - assistant responds
    """
    return [
        {
            "id": "msg-1",
            "timestamp": 1781675942999,
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "What is 2+2?"}],
        },
        {
            "id": "reasoning-1",
            "timestamp": 1781675943000,
            "type": "reasoning",
            "content": "The user wants to know 2+2. I should calculate this.",
        },
        {
            "id": "call-1",
            "callId": "call-1",
            "timestamp": 1781675943001,
            "type": "function_call",
            "name": "calculator",
            "arguments": '{"expression": "2+2"}',
        },
        {
            "id": "result-1",
            "callId": "call-1",
            "parentId": "call-1",
            "timestamp": 1781675943002,
            "type": "function_call_result",
            "result": "4",
        },
        {
            "id": "msg-2",
            "timestamp": 1781675943003,
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "2+2 equals 4."}],
        },
        {
            "id": "msg-3",
            "timestamp": 1781675943004,
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Thanks!"}],
        },
        {
            "id": "msg-4",
            "timestamp": 1781675943005,
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "You're welcome!"}],
        },
    ]


@pytest.fixture
def sample_events_with_preamble() -> list[dict]:
    """Events that start with non-user messages (preamble)."""
    return [
        {
            "id": "file-snap-1",
            "timestamp": 1781675942990,
            "type": "file-history-snapshot",
        },
        {
            "id": "msg-1",
            "timestamp": 1781675942999,
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Hello"}],
        },
        {
            "id": "msg-2",
            "timestamp": 1781675943000,
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hi there!"}],
        },
    ]


@pytest.fixture
def sample_events_with_gap() -> list[dict]:
    """Events with a missing function_call_result."""
    return [
        {
            "id": "msg-1",
            "timestamp": 1781675942999,
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Read file X"}],
        },
        {
            "id": "call-1",
            "callId": "call-1",
            "timestamp": 1781675943001,
            "type": "function_call",
            "name": "Read",
            "arguments": '{"file_path": "X"}',
        },
        # Missing: function_call_result for call-1
        {
            "id": "msg-2",
            "timestamp": 1781675943003,
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "I couldn't read the file."}],
        },
    ]


@pytest.fixture
def sample_jsonl_content() -> str:
    """Sample jsonl content matching WorkBuddy format."""
    lines = [
        json.dumps({
            "id": "msg-1",
            "timestamp": 1781675942999,
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "What is 2+2?"}],
            "sessionId": "test-session-001",
            "cwd": "C:\\test",
        }),
        json.dumps({
            "id": "reasoning-1",
            "timestamp": 1781675943000,
            "type": "reasoning",
            "content": "Need to calculate 2+2.",
            "sessionId": "test-session-001",
        }),
        json.dumps({
            "id": "call-1",
            "callId": "call-1",
            "timestamp": 1781675943001,
            "type": "function_call",
            "name": "calculator",
            "arguments": '{"expression": "2+2"}',
            "sessionId": "test-session-001",
        }),
        json.dumps({
            "id": "result-1",
            "callId": "call-1",
            "parentId": "call-1",
            "timestamp": 1781675943002,
            "type": "function_call_result",
            "result": "4",
            "sessionId": "test-session-001",
        }),
        json.dumps({
            "id": "msg-2",
            "timestamp": 1781675943003,
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "2+2 equals 4."}],
            "sessionId": "test-session-001",
        }),
    ]
    return "\n".join(lines) + "\n"


@pytest.fixture
def workbuddy_db(tmp_path: Path) -> Path:
    """Create a minimal WorkBuddy-style SQLite DB for testing."""
    db_path = tmp_path / "workbuddy.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            cwd TEXT NOT NULL,
            user_id TEXT NOT NULL,
            title TEXT,
            status TEXT NOT NULL DEFAULT 'Completed',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            deleted_at INTEGER,
            mode TEXT,
            model TEXT
        )
    """)
    conn.execute("""
        INSERT INTO sessions (id, cwd, user_id, title, status, created_at, updated_at, mode, model)
        VALUES
            ('test-session-001', 'C:\\test', 'user1', 'Test Session', 'completed', 1781675942000, 1781675943000, 'craft', 'test-model'),
            ('test-session-002', 'C:\\test2', 'user1', 'Another Session', 'completed', 1781675950000, 1781675960000, 'craft', 'test-model')
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def tmp_export_dir(tmp_path: Path) -> Path:
    """Create a temporary export directory."""
    export_dir = tmp_path / "agent_export"
    export_dir.mkdir()
    (export_dir / "raw").mkdir()
    (export_dir / "processed").mkdir()
    return export_dir
