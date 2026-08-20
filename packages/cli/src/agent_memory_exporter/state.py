"""Incremental export state management.

Tracks which sessions have been exported and when, to support
incremental export (only re-export sessions updated since last run).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_memory_shared.models import ExportState, ExportedSessionInfo
from agent_memory_shared.paths import ExportPaths


def load_state(paths: ExportPaths) -> ExportState:
    """Load export state from state.json, or return empty state if not found."""
    if paths.state_file.exists():
        return ExportState.from_json_file(paths.state_file)
    return ExportState()


def save_state(state: ExportState, paths: ExportPaths) -> None:
    """Persist export state to state.json."""
    state.to_json_file(paths.state_file)


def update_state(
    state: ExportState,
    session_id: str,
    updated_at: datetime,
    turn_count: int,
    content_hash: str,
    file_path: str,
) -> None:
    """Update state after exporting a session."""
    state.exported_sessions[session_id] = ExportedSessionInfo(
        updated_at=updated_at,
        turn_count=turn_count,
        exported_at=datetime.now(timezone.utc),
        file=file_path,
        content_hash=content_hash,
    )
    state.export_count += 1
    # Update last_export_ts to the latest updated_at seen
    if state.last_export_ts is None or updated_at > state.last_export_ts:
        state.last_export_ts = updated_at


def needs_reexport(
    state: ExportState,
    session_id: str,
    updated_at: datetime,
    content_hash: str | None = None,
) -> bool:
    """Check if a session needs re-export.

    A session needs re-export if:
    1. It's not in state (never exported), OR
    2. Its updated_at is newer than the recorded updated_at, OR
    3. Its content_hash differs from the recorded hash
    """
    info = state.exported_sessions.get(session_id)
    if info is None:
        return True
    if updated_at > info.updated_at:
        return True
    if content_hash and content_hash != info.content_hash:
        return True
    return False


def reset_state(paths: ExportPaths) -> ExportState:
    """Reset state to empty (for --full re-export). Returns the new empty state."""
    state = ExportState()
    save_state(state, paths)
    return state
