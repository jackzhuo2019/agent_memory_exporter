"""Upload state tracking for WeKnora ingestion.

Persists per-file upload status to upload_state.json, enabling:
    - Skip already-uploaded files (idempotent ingest)
    - Retry failed uploads (max 3 attempts)
    - Track knowledge_id returned by WeKnora

State file location: <root>/upload_state.json (alongside state.json)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


MAX_RETRIES = 3


class UploadRecord(BaseModel):
    """Per-file upload record."""

    session_id: str
    file_name: str
    quality: str
    knowledge_id: str = ""
    status: str = "pending"  # pending, uploaded, failed, skipped
    retries: int = 0
    uploaded_at: datetime | None = None
    error: str = ""


class UploadState(BaseModel):
    """Upload state persisted to upload_state.json."""

    schema_version: str = "1.0.0"
    last_upload_ts: datetime | None = None
    records: dict[str, UploadRecord] = Field(default_factory=dict)
    total_uploaded: int = 0
    total_failed: int = 0

    def to_json_file(self, path: str | Path) -> None:
        """Write state to JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump_json(indent=2, exclude_none=True)
        path.write_text(data, encoding="utf-8")

    @classmethod
    def from_json_file(cls, path: str | Path) -> UploadState:
        """Load state from JSON file, or return empty state if not found."""
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)


def load_upload_state(path: str | Path) -> UploadState:
    """Load upload state from file."""
    return UploadState.from_json_file(path)


def save_upload_state(state: UploadState, path: str | Path) -> None:
    """Save upload state to file."""
    state.to_json_file(path)


def is_uploaded(state: UploadState, session_id: str) -> bool:
    """Check if a session has been successfully uploaded."""
    record = state.records.get(session_id)
    return record is not None and record.status == "uploaded"


def can_retry(state: UploadState, session_id: str) -> bool:
    """Check if a session can still be retried (retries < MAX_RETRIES)."""
    record = state.records.get(session_id)
    if record is None:
        return True
    return record.retries < MAX_RETRIES


def record_success(
    state: UploadState,
    session_id: str,
    file_name: str,
    quality: str,
    knowledge_id: str,
) -> None:
    """Record a successful upload."""
    state.records[session_id] = UploadRecord(
        session_id=session_id,
        file_name=file_name,
        quality=quality,
        knowledge_id=knowledge_id,
        status="uploaded",
        retries=state.records.get(session_id, UploadRecord(session_id=session_id, file_name=file_name, quality=quality)).retries,
        uploaded_at=datetime.now(timezone.utc),
    )
    state.total_uploaded += 1
    state.last_upload_ts = datetime.now(timezone.utc)


def record_failure(
    state: UploadState,
    session_id: str,
    file_name: str,
    quality: str,
    error: str,
) -> None:
    """Record a failed upload attempt."""
    existing = state.records.get(session_id)
    retries = existing.retries + 1 if existing else 1
    state.records[session_id] = UploadRecord(
        session_id=session_id,
        file_name=file_name,
        quality=quality,
        status="failed",
        retries=retries,
        error=error,
    )
    state.total_failed += 1


def record_skip(state: UploadState, session_id: str, file_name: str, quality: str) -> None:
    """Record a skipped upload (already uploaded or quality too low)."""
    if session_id not in state.records:
        state.records[session_id] = UploadRecord(
            session_id=session_id,
            file_name=file_name,
            quality=quality,
            status="skipped",
        )
