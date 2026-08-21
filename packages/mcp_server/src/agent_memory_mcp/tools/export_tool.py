"""MCP export tool: triggers CLI export in-process.

Reads the CLI's adapter/assembler/exporter/redactor/state modules directly
(no subprocess overhead), runs the export pipeline, and returns a JSON summary.

This tool does NOT call LLM or HTTP APIs — it is purely the CLI pipeline
invoked from within the MCP server process.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp import Tool
from pydantic import BaseModel, Field

from agent_memory_shared.paths import ExportPaths
from agent_memory_exporter.adapters.workbuddy import WorkBuddyAdapter
from agent_memory_exporter.adapters.opencode import OpenCodeAdapter
from agent_memory_exporter.exporter import (
    compute_content_hash,
    export_session,
    write_gaps_report,
    write_manifest,
)
from agent_memory_exporter.redactor import Redactor
from agent_memory_exporter.state import (
    load_state,
    needs_reexport,
    reset_state,
    save_state,
    update_state,
)


class ExportResult(BaseModel):
    """Result of the export tool."""

    exported: int = 0
    skipped: int = 0
    errors: int = 0
    total_sessions: int = 0
    error_details: list[str] = Field(default_factory=list)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


class ExportTool:
    """MCP tool that triggers CLI export in-process.

    Parameters:
        source: "workbuddy" or "opencode" (default: "workbuddy")
        output: output directory (default: "./agent_export")
        incremental: only export sessions updated since last export (default: true)
        full: re-export all sessions, ignoring state (default: false)
        limit: maximum number of sessions to export (default: none)
        no_redact: skip PII redaction (default: false)
    """

    def get_tool_definition(self) -> Tool:
        return Tool(
            name="export",
            description=(
                "Export agent conversation sessions from a local data source "
                "(WorkBuddy or OpenCode) to raw JSON files. "
                "Reads source databases and jsonl files, reassembles turns, "
                "applies PII redaction, and writes raw/*.json + state.json."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["workbuddy", "opencode"],
                        "default": "workbuddy",
                        "description": "Data source to export from.",
                    },
                    "output": {
                        "type": "string",
                        "default": "./agent_export",
                        "description": "Output directory for exported data.",
                    },
                    "incremental": {
                        "type": "boolean",
                        "default": True,
                        "description": "Only export sessions updated since last export.",
                    },
                    "full": {
                        "type": "boolean",
                        "default": False,
                        "description": "Re-export all sessions, ignoring state.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of sessions to export.",
                    },
                    "no_redact": {
                        "type": "boolean",
                        "default": False,
                        "description": "Skip PII redaction (NOT recommended).",
                    },
                },
            },
        )

    async def run(
        self,
        source: str = "workbuddy",
        output: str = "./agent_export",
        incremental: bool = True,
        full: bool = False,
        limit: int | None = None,
        no_redact: bool = False,
        home_dir: str | None = None,
        **kwargs: Any,
    ) -> ExportResult:
        """Execute the export pipeline.

        This is the in-process equivalent of:
            agent-memory-exporter export --source <source> --output <output> [--incremental|--full] [--limit N] [--no-redact]

        Args:
            home_dir: Override home directory (for testing). Defaults to Path.home().
        """
        # Select adapter
        if source == "workbuddy":
            adapter = WorkBuddyAdapter(home_dir=home_dir) if home_dir else WorkBuddyAdapter()
        elif source == "opencode":
            adapter = OpenCodeAdapter(home_dir=home_dir) if home_dir else OpenCodeAdapter()
        else:
            raise ValueError(f"Unknown source: {source}. Supported: workbuddy, opencode")

        if not adapter.detect():
            raise RuntimeError(f"{source} data source not found")

        paths = ExportPaths(output)
        paths.ensure_dirs()

        # Load or reset state
        if full:
            state = reset_state(paths)
        else:
            state = load_state(paths)

        # Determine filter timestamp
        since = None
        if incremental and not full and state.last_export_ts is not None:
            since = state.last_export_ts

        # List sessions
        try:
            session_refs = adapter.list_sessions(since=since)
        except NotImplementedError as e:
            raise RuntimeError(str(e))

        total = len(session_refs)
        if limit is not None:
            session_refs = session_refs[:limit]

        # Initialize redactor
        redactor = None
        if not no_redact:
            redactor = Redactor()
            if not redactor.rules:
                redactor = None

        # Export each session
        exported_sessions = []
        result = ExportResult(total_sessions=total)
        error_details: list[str] = []

        for ref in session_refs:
            try:
                # Check if re-export needed
                if not full and not needs_reexport(state, ref.session_id, ref.updated_at):
                    result.skipped += 1
                    continue

                # Read session
                session = adapter.read_session(ref.session_id)

                # Apply redaction
                if redactor:
                    redactor.redact_session(session)

                # Export
                export_session(session, paths, validate=True)

                # Compute hash and update state
                content_hash = compute_content_hash(session)
                update_state(
                    state,
                    session_id=ref.session_id,
                    updated_at=ref.updated_at,
                    turn_count=session.session_meta.turn_count,
                    content_hash=content_hash,
                    file_path=f"raw/{ref.session_id}.json",
                )

                exported_sessions.append(session)
                result.exported += 1

            except Exception as e:
                result.errors += 1
                error_details.append(f"{ref.session_id}: {e}")

        # Save state
        save_state(state, paths)

        # Write manifest and gaps report
        if exported_sessions:
            write_manifest(exported_sessions, paths)
            write_gaps_report(exported_sessions, paths)

        result.error_details = error_details
        return result
