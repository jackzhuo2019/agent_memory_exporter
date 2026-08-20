"""Tests for the WorkBuddy adapter."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent_memory_exporter.adapters.workbuddy import (
    WorkBuddyAdapter,
    _escape_cwd_to_dirname,
    _extract_text_from_content,
    _ts_to_datetime,
)


class TestWorkBuddyAdapter:
    def test_escape_cwd_to_dirname(self):
        """cwd path escaping matches the observed WorkBuddy convention."""
        assert (
            _escape_cwd_to_dirname("C:\\Users\\Admin\\WorkBuddy\\2026-06-17")
            == "c-Users-Admin-WorkBuddy-2026-06-17"
        )
        assert (
            _escape_cwd_to_dirname("D:\\projects\\my-app")
            == "d-projects-my-app"
        )
        # Drive letter without backslash
        assert _escape_cwd_to_dirname("C:\\test") == "c-test"

    def test_extract_text_from_content_list(self):
        """Content as a list of dicts."""
        content = [{"type": "input_text", "text": "Hello"}, {"type": "output_text", "text": "World"}]
        assert _extract_text_from_content(content) == "Hello\nWorld"

    def test_extract_text_from_content_string(self):
        """Content as a plain string."""
        assert _extract_text_from_content("Hello") == "Hello"

    def test_extract_text_from_content_none(self):
        assert _extract_text_from_content(None) == ""

    def test_extract_text_from_content_empty_list(self):
        assert _extract_text_from_content([]) == ""

    def test_ts_to_datetime_ms(self):
        """WorkBuddy uses millisecond epoch."""
        dt = _ts_to_datetime(1781675942999)
        assert dt.year == 2026

    def test_ts_to_datetime_string(self):
        """Timestamps can be strings."""
        dt = _ts_to_datetime("1781675942999")
        assert dt.year == 2026

    def test_detect_no_data(self, tmp_path):
        """detect() returns False when workbuddy.db doesn't exist."""
        adapter = WorkBuddyAdapter(home_dir=tmp_path)
        assert adapter.detect() is False

    def test_detect_with_data(self, tmp_path, workbuddy_db, sample_jsonl_content):
        """detect() returns True when db and projects dir exist."""
        # Create projects dir with session jsonl
        projects_dir = tmp_path / ".workbuddy" / "projects" / "c-test"
        projects_dir.mkdir(parents=True)
        (projects_dir / "test-session-001.jsonl").write_text(sample_jsonl_content, encoding="utf-8")

        # Move db to the right place
        db_path = tmp_path / "workbuddy.db"
        workbuddy_db.rename(db_path)
        wb_dir = tmp_path / ".workbuddy"
        wb_dir.mkdir(exist_ok=True)
        db_path = db_path.rename(wb_dir / "workbuddy.db")

        adapter = WorkBuddyAdapter(home_dir=tmp_path)
        assert adapter.detect() is True

    def test_list_sessions(self, tmp_path, workbuddy_db, sample_jsonl_content):
        """list_sessions returns sessions from DB."""
        # Set up directory structure
        wb_dir = tmp_path / ".workbuddy"
        wb_dir.mkdir()
        workbuddy_db.rename(wb_dir / "workbuddy.db")

        # Create projects dir with escaped cwd
        projects_dir = wb_dir / "projects" / "c-test"
        projects_dir.mkdir(parents=True)
        (projects_dir / "test-session-001.jsonl").write_text(sample_jsonl_content, encoding="utf-8")

        adapter = WorkBuddyAdapter(home_dir=tmp_path)
        sessions = adapter.list_sessions()
        assert len(sessions) == 2  # Two sessions in the test DB
        session_ids = [s.session_id for s in sessions]
        assert "test-session-001" in session_ids
        assert "test-session-002" in session_ids

    def test_list_sessions_with_since_filter(self, tmp_path, workbuddy_db, sample_jsonl_content):
        """list_sessions filters by updated_at."""
        wb_dir = tmp_path / ".workbuddy"
        wb_dir.mkdir()
        workbuddy_db.rename(wb_dir / "workbuddy.db")

        projects_dir = wb_dir / "projects" / "c-test"
        projects_dir.mkdir(parents=True)
        (projects_dir / "test-session-001.jsonl").write_text(sample_jsonl_content, encoding="utf-8")

        adapter = WorkBuddyAdapter(home_dir=tmp_path)
        from datetime import datetime, timezone

        # session-001 updated_at=1781675943000, session-002 updated_at=1781675960000
        since = datetime.fromtimestamp(1781675943000 / 1000.0, tz=timezone.utc)
        sessions = adapter.list_sessions(since=since)
        # Only session-002 (updated_at > since)
        session_ids = [s.session_id for s in sessions]
        assert "test-session-002" in session_ids
        assert "test-session-001" not in session_ids

    def test_read_session(self, tmp_path, workbuddy_db, sample_jsonl_content):
        """read_session returns a fully assembled RawSession."""
        wb_dir = tmp_path / ".workbuddy"
        wb_dir.mkdir()
        workbuddy_db.rename(wb_dir / "workbuddy.db")

        projects_dir = wb_dir / "projects" / "c-test"
        projects_dir.mkdir(parents=True)
        (projects_dir / "test-session-001.jsonl").write_text(sample_jsonl_content, encoding="utf-8")

        adapter = WorkBuddyAdapter(home_dir=tmp_path)
        session = adapter.read_session("test-session-001")

        assert session.session_id == "test-session-001"
        assert session.source == "workbuddy"
        assert session.session_meta.cwd == "C:\\test"
        assert session.session_meta.title == "Test Session"
        # Should have at least 1 turn (user message)
        assert len(session.turns) >= 1
        # First non-preamble turn should have user_message
        user_turns = [t for t in session.turns if t.turn_index >= 0]
        assert len(user_turns) >= 1
        assert "2+2" in user_turns[0].user_message

    def test_read_session_missing_jsonl(self, tmp_path, workbuddy_db):
        """read_session returns gaps when jsonl is missing."""
        wb_dir = tmp_path / ".workbuddy"
        wb_dir.mkdir()
        workbuddy_db.rename(wb_dir / "workbuddy.db")
        # Don't create projects dir — session exists in DB but no jsonl

        adapter = WorkBuddyAdapter(home_dir=tmp_path)
        session = adapter.read_session("test-session-001")

        assert len(session.turns) == 0
        assert len(session.gaps) >= 1
        assert any(g.gap_type.value == "missing_jsonl" for g in session.gaps)

    def test_find_conversation_file_traversal_fallback(self, tmp_path, workbuddy_db, sample_jsonl_content):
        """_find_conversation_file falls back to traversal when escaped dir doesn't match."""
        wb_dir = tmp_path / ".workbuddy"
        wb_dir.mkdir()
        workbuddy_db.rename(wb_dir / "workbuddy.db")

        # Create projects dir with a different name (simulating different escaping)
        projects_dir = wb_dir / "projects" / "some-other-name"
        projects_dir.mkdir(parents=True)
        (projects_dir / "test-session-001.jsonl").write_text(sample_jsonl_content, encoding="utf-8")

        adapter = WorkBuddyAdapter(home_dir=tmp_path)
        file_path = adapter._find_conversation_file("test-session-001", "C:\\nonexistent")
        assert file_path is not None
        assert file_path.name == "test-session-001.jsonl"
