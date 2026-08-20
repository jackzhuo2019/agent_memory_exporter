"""Exporter: write raw session JSON files and manifest."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from agent_memory_shared.models import RawSession
from agent_memory_shared.paths import ExportPaths


def compute_content_hash(session: RawSession) -> str:
    """Compute a deterministic SHA-256 hash of the session content."""
    # Use a canonical JSON representation (sorted keys, no whitespace)
    data = session.model_dump(mode="json", exclude_none=True)
    # Remove exported_at from hash (it changes every export)
    data.pop("exported_at", None)
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode("utf-8")).hexdigest()}"


def export_session(
    session: RawSession,
    paths: ExportPaths,
    validate: bool = True,
) -> Path:
    """Write a single session to raw/<session_id>.json.

    Args:
        session: The assembled RawSession.
        paths: ExportPaths manager.
        validate: If True, validate against JSON Schema before writing.

    Returns:
        Path to the written file.
    """
    paths.ensure_dirs()
    file_path = paths.raw_session_path(session.session_id)

    if validate:
        from agent_memory_shared.schema_loader import validate_raw_session

        data = session.model_dump(mode="json", exclude_none=True)
        errors = validate_raw_session(data)
        if errors:
            # Log errors but still write (don't block export)
            # In production, this could be a strict mode that raises
            import sys

            print(
                f"WARNING: Schema validation errors for session {session.session_id}:",
                file=sys.stderr,
            )
            for err in errors:
                print(f"  {err}", file=sys.stderr)

    session.to_json_file(file_path)
    return file_path


def write_manifest(
    sessions: list[RawSession],
    paths: ExportPaths,
) -> Path:
    """Write a manifest.json summarizing all exported sessions.

    The manifest is an index file that the MCP Server can read to know
    which sessions are available without scanning the raw/ directory.
    """
    paths.ensure_dirs()
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_count": len(sessions),
        "sessions": [
            {
                "session_id": s.session_id,
                "source": s.source,
                "title": s.session_meta.title,
                "cwd": s.session_meta.cwd,
                "turn_count": s.session_meta.turn_count,
                "event_count": s.session_meta.event_count,
                "gap_count": len(s.gaps),
                "created_at": s.session_meta.created_at.isoformat() if s.session_meta.created_at else None,
                "updated_at": s.session_meta.updated_at.isoformat() if s.session_meta.updated_at else None,
                "file": f"raw/{s.session_id}.json",
            }
            for s in sessions
        ],
    }
    manifest_path = paths.manifest_file
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def write_gaps_report(
    sessions: list[RawSession],
    paths: ExportPaths,
) -> Path:
    """Write a gaps.json report aggregating all data integrity issues."""
    paths.ensure_dirs()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_gaps": sum(len(s.gaps) for s in sessions),
        "sessions_with_gaps": sum(1 for s in sessions if s.gaps),
        "details": [
            {
                "session_id": s.session_id,
                "gaps": [g.model_dump(mode="json") for g in s.gaps],
            }
            for s in sessions
            if s.gaps
        ],
    }
    gaps_path = paths.gaps_file
    gaps_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return gaps_path
