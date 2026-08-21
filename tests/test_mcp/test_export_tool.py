"""Tests for the MCP Server export tool."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from agent_memory_mcp.tools.export_tool import ExportTool


@pytest.fixture
def workbuddy_setup(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal WorkBuddy setup with DB and jsonl under tmp_path."""
    wb_dir = tmp_path / ".workbuddy"
    wb_dir.mkdir()
    projects_dir = wb_dir / "projects" / "c-test"
    projects_dir.mkdir(parents=True)

    # Create DB
    db_path = wb_dir / "workbuddy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
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
        );
    """)
    conn.execute(
        "INSERT INTO sessions (id, cwd, user_id, title, status, created_at, updated_at, mode, model) VALUES (?,?,?,?,?,?,?,?,?)",
        ("mcp-test-001", "C:\\test", "user1", "MCP Test Session", "completed", 1781675942000, 1781675943000, "craft", "test-model"),
    )
    conn.commit()
    conn.close()

    # Create jsonl
    jsonl_content = "\n".join([
        json.dumps({
            "id": "msg-1",
            "timestamp": 1781675942999,
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Hello from MCP test"}],
            "sessionId": "mcp-test-001",
            "cwd": "C:\\test",
        }),
        json.dumps({
            "id": "msg-2",
            "timestamp": 1781675943000,
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hi! How can I help you?"}],
            "sessionId": "mcp-test-001",
        }),
    ]) + "\n"
    (projects_dir / "mcp-test-001.jsonl").write_text(jsonl_content, encoding="utf-8")

    return tmp_path, tmp_path / "agent_export"


class TestExportTool:
    def test_tool_definition(self):
        tool = ExportTool()
        defn = tool.get_tool_definition()
        assert defn.name == "export"
        assert "source" in defn.input_schema["properties"]
        assert "incremental" in defn.input_schema["properties"]
        assert "full" in defn.input_schema["properties"]
        assert "limit" in defn.input_schema["properties"]
        assert "no_redact" in defn.input_schema["properties"]

    def test_export_workbuddy(self, workbuddy_setup):
        """Export via ExportTool.run() — in-process, no subprocess."""
        home_dir, export_dir = workbuddy_setup
        tool = ExportTool()

        result = asyncio.run(
            tool.run(source="workbuddy", output=str(export_dir), full=True, limit=1, home_dir=str(home_dir))
        )

        assert result.exported == 1
        assert result.errors == 0
        assert result.total_sessions >= 1

        # Check raw JSON was written
        raw_files = list((export_dir / "raw").glob("*.json"))
        assert len(raw_files) == 1
        assert raw_files[0].name == "mcp-test-001.json"

        # Verify content
        data = json.loads(raw_files[0].read_text(encoding="utf-8"))
        assert data["schema_version"] == "1.0.0"
        assert data["source"] == "workbuddy"
        assert data["session_id"] == "mcp-test-001"
        assert len(data["turns"]) >= 1
        assert "Hello from MCP test" in data["turns"][0]["user_message"]

        # Check state.json
        state_path = export_dir / "state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "mcp-test-001" in state["exported_sessions"]

    def test_export_incremental_skip(self, workbuddy_setup):
        """Second export with incremental should find no new sessions."""
        home_dir, export_dir = workbuddy_setup
        tool = ExportTool()

        # First export (full)
        result1 = asyncio.run(
            tool.run(source="workbuddy", output=str(export_dir), full=True, limit=1, home_dir=str(home_dir))
        )
        assert result1.exported == 1

        # Second export (incremental) — no new sessions since last_export_ts
        result2 = asyncio.run(
            tool.run(source="workbuddy", output=str(export_dir), incremental=True, limit=1, home_dir=str(home_dir))
        )
        assert result2.exported == 0
        assert result2.total_sessions == 0  # filtered out by since=last_export_ts

    def test_export_full_reexports(self, workbuddy_setup):
        """Full mode re-exports everything."""
        home_dir, export_dir = workbuddy_setup
        tool = ExportTool()

        # First export
        result1 = asyncio.run(
            tool.run(source="workbuddy", output=str(export_dir), full=True, limit=1, home_dir=str(home_dir))
        )
        assert result1.exported == 1

        # Full re-export
        result2 = asyncio.run(
            tool.run(source="workbuddy", output=str(export_dir), full=True, limit=1, home_dir=str(home_dir))
        )
        assert result2.exported == 1
        assert result2.skipped == 0

    def test_export_no_redact(self, workbuddy_setup):
        """Export with no_redact=True should not apply redaction."""
        home_dir, export_dir = workbuddy_setup
        tool = ExportTool()

        result = asyncio.run(
            tool.run(source="workbuddy", output=str(export_dir), full=True, limit=1, no_redact=True, home_dir=str(home_dir))
        )
        assert result.exported == 1

    def test_export_source_not_found(self, tmp_path):
        """Export with a non-existent source should raise RuntimeError."""
        tool = ExportTool()
        with pytest.raises(RuntimeError, match="data source not found"):
            asyncio.run(
                tool.run(source="workbuddy", output=str(tmp_path / "export"), home_dir=str(tmp_path))
            )

    def test_export_unknown_source(self, tmp_path):
        """Export with an unknown source should raise ValueError."""
        tool = ExportTool()
        with pytest.raises(ValueError, match="Unknown source"):
            asyncio.run(
                tool.run(source="nonexistent", output=str(tmp_path / "export"))
            )
