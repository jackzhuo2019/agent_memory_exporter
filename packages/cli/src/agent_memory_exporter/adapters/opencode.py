"""OpenCode data source adapter (stub — full implementation in Phase 4)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agent_memory_shared.models import RawSession, SessionRef


class OpenCodeAdapter:
    """Read-only adapter for OpenCode local data.

    Reads ~/.local/share/opencode/opencode.db:
      session → session_message (ordered by seq) → part (ordered by time_created)

    Full implementation deferred to Phase 4.
    """

    def __init__(self, home_dir: str | Path | None = None) -> None:
        if home_dir is None:
            home_dir = Path.home()
        self.home_dir = Path(home_dir)
        self.db_path = self.home_dir / ".local" / "share" / "opencode" / "opencode.db"

    @property
    def name(self) -> str:
        return "opencode"

    def detect(self) -> bool:
        return self.db_path.exists()

    def list_sessions(self, since: datetime | None = None) -> list[SessionRef]:
        raise NotImplementedError("OpenCode adapter will be implemented in Phase 4")

    def read_session(self, session_id: str) -> RawSession:
        raise NotImplementedError("OpenCode adapter will be implemented in Phase 4")
