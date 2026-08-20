"""SourceAdapter protocol — the interface all data source adapters must implement."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from agent_memory_shared.models import RawSession, SessionRef


@runtime_checkable
class SourceAdapter(Protocol):
    """Read-only adapter for a specific agent platform's local data.

    Implementations:
    - WorkBuddyAdapter: reads ~/.workbuddy/workbuddy.db + projects/*.jsonl
    - OpenCodeAdapter: reads ~/.local/share/opencode/opencode.db
    """

    @property
    def name(self) -> str:
        """Adapter identifier: 'workbuddy' or 'opencode'."""
        ...

    def detect(self) -> bool:
        """Return True if the data source exists and is readable."""
        ...

    def list_sessions(self, since: datetime | None = None) -> list[SessionRef]:
        """List sessions, optionally filtered by updated_at > since.

        Args:
            since: If provided, only return sessions updated after this timestamp.

        Returns:
            List of SessionRef, sorted by updated_at ascending.
        """
        ...

    def read_session(self, session_id: str) -> RawSession:
        """Read a single session and return a fully assembled RawSession.

        This includes reading the raw event stream, assembling turns,
        and returning the complete session with gaps filled in.

        Args:
            session_id: The session identifier (WorkBuddy conversation-id
                        or OpenCode session-id).

        Returns:
            RawSession with turns, session_meta, and any detected gaps.
        """
        ...
