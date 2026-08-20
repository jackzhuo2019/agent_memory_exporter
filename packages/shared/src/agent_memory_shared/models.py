"""Pydantic models shared between CLI and MCP Server.

These models mirror schemas/raw_session.schema.json and serve as the
single source of truth for the raw JSON contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class EventRole(str, Enum):
    ASSISTANT = "assistant"
    TOOL = "tool"
    REASONING = "reasoning"
    FILE_SNAPSHOT = "file_snapshot"
    SUB_AGENT = "sub_agent"


class GapType(str, Enum):
    MISSING_JSONL = "missing_jsonl"
    MISSING_FUNCTION_RESULT = "missing_function_result"
    TIMESTAMP_DISORDER = "timestamp_disorder"
    EMPTY_SESSION = "empty_session"
    PARSE_ERROR = "parse_error"


class Event(BaseModel):
    """A single event within a turn (assistant message, tool call, reasoning, etc.)."""

    role: EventRole
    content: str = ""
    tool_name: str | None = None
    tool_input: str | None = None
    tool_output: str | None = None
    timestamp: datetime
    error: str | None = None
    sub_agent_id: str | None = None


class Turn(BaseModel):
    """A conversation turn: one user message + all subsequent events until the next user message."""

    turn_index: int = Field(description="0-based. -1 for preamble (events before first user message).")
    user_message: str = ""
    user_timestamp: datetime | None = None
    events: list[Event] = Field(default_factory=list)


class Gap(BaseModel):
    """A data integrity issue detected during export."""

    gap_type: GapType
    details: str
    event_id: str | None = None
    turn_index: int | None = None


class SessionMeta(BaseModel):
    """Session-level metadata from the source database."""

    title: str | None = None
    cwd: str | None = None
    model: str | None = None
    mode: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    turn_count: int = 0
    event_count: int = 0
    token_total: int = 0
    sub_agents: list[str] = Field(default_factory=list)


class RawSession(BaseModel):
    """The complete exported session, written to raw/<session_id>.json."""

    schema_version: str = "1.0.0"
    session_id: str
    source: Literal["workbuddy", "opencode"]
    exported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_meta: SessionMeta = Field(default_factory=SessionMeta)
    turns: list[Turn] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)

    def to_json_file(self, path: str | Path, indent: int = 2) -> None:
        """Write this session to a JSON file with deterministic formatting."""
        import json

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump_json(indent=indent, exclude_none=True)
        path.write_text(data, encoding="utf-8")

    @classmethod
    def from_json_file(cls, path: str | Path) -> RawSession:
        """Read a session from a JSON file."""
        import json

        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)


class SessionRef(BaseModel):
    """A lightweight reference to a session for incremental export listing."""

    session_id: str
    updated_at: datetime
    cwd: str | None = None
    title: str | None = None
    conversation_file: str | None = None


class ExportedSessionInfo(BaseModel):
    """Per-session metadata stored in state.json."""

    updated_at: datetime
    turn_count: int
    exported_at: datetime
    file: str
    content_hash: str


class ExportState(BaseModel):
    """Incremental export state, persisted to state.json."""

    schema_version: str = "1.0.0"
    last_export_ts: datetime | None = None
    exported_sessions: dict[str, ExportedSessionInfo] = Field(default_factory=dict)
    export_count: int = 0

    def to_json_file(self, path: str | Path) -> None:
        import json

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump_json(indent=2, exclude_none=True)
        path.write_text(data, encoding="utf-8")

    @classmethod
    def from_json_file(cls, path: str | Path) -> ExportState:
        import json

        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)
