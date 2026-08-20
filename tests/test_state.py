"""Tests for incremental export state management."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_memory_shared.models import ExportState, ExportedSessionInfo
from agent_memory_shared.paths import ExportPaths
from agent_memory_exporter.state import (
    load_state,
    needs_reexport,
    reset_state,
    save_state,
    update_state,
)


class TestState:
    def test_load_empty_state(self, tmp_export_dir):
        """Loading state when no state.json exists returns empty state."""
        paths = ExportPaths(tmp_export_dir)
        state = load_state(paths)
        assert state.schema_version == "1.0.0"
        assert state.last_export_ts is None
        assert state.export_count == 0
        assert len(state.exported_sessions) == 0

    def test_save_and_load_state(self, tmp_export_dir):
        paths = ExportPaths(tmp_export_dir)
        state = ExportState()
        ts = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
        update_state(
            state,
            session_id="s1",
            updated_at=ts,
            turn_count=5,
            content_hash="sha256:abc123",
            file_path="raw/s1.json",
        )
        save_state(state, paths)

        loaded = load_state(paths)
        assert loaded.export_count == 1
        assert "s1" in loaded.exported_sessions
        assert loaded.exported_sessions["s1"].turn_count == 5
        assert loaded.exported_sessions["s1"].content_hash == "sha256:abc123"
        assert loaded.last_export_ts == ts

    def test_needs_reexport_new_session(self, tmp_export_dir):
        paths = ExportPaths(tmp_export_dir)
        state = load_state(paths)
        ts = datetime.now(timezone.utc)
        assert needs_reexport(state, "new-session", ts) is True

    def test_needs_reexport_up_to_date(self, tmp_export_dir):
        paths = ExportPaths(tmp_export_dir)
        state = ExportState()
        ts = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
        update_state(state, "s1", ts, 5, "sha256:abc", "raw/s1.json")
        # Same timestamp, same hash → no re-export needed
        assert needs_reexport(state, "s1", ts, "sha256:abc") is False

    def test_needs_reexport_updated(self, tmp_export_dir):
        paths = ExportPaths(tmp_export_dir)
        state = ExportState()
        old_ts = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
        update_state(state, "s1", old_ts, 5, "sha256:abc", "raw/s1.json")
        # Newer timestamp → needs re-export
        new_ts = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
        assert needs_reexport(state, "s1", new_ts) is True

    def test_needs_reexport_hash_changed(self, tmp_export_dir):
        paths = ExportPaths(tmp_export_dir)
        state = ExportState()
        ts = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
        update_state(state, "s1", ts, 5, "sha256:abc", "raw/s1.json")
        # Same timestamp, different hash → needs re-export
        assert needs_reexport(state, "s1", ts, "sha256:xyz") is True

    def test_reset_state(self, tmp_export_dir):
        paths = ExportPaths(tmp_export_dir)
        state = ExportState()
        ts = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
        update_state(state, "s1", ts, 5, "sha256:abc", "raw/s1.json")
        save_state(state, paths)

        reset = reset_state(paths)
        assert reset.export_count == 0
        assert len(reset.exported_sessions) == 0
        assert reset.last_export_ts is None

        # Verify persisted
        loaded = load_state(paths)
        assert loaded.export_count == 0
