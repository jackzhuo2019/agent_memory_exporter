"""WorkBuddy data source adapter.

Reads session metadata from ~/.workbuddy/workbuddy.db (sessions table)
and conversation events from ~/.workbuddy/projects/<cwd-hash>/*.jsonl.

The jsonl files contain one JSON event per line with types:
  message (role=user|assistant), function_call, function_call_result,
  reasoning, file-history-snapshot, ai-title

Turn assembly is delegated to assembler.py; this adapter focuses on
reading raw events and session metadata.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from agent_memory_shared.models import (
    Event,
    EventRole,
    Gap,
    GapType,
    RawSession,
    SessionMeta,
    SessionRef,
)

from agent_memory_exporter.assembler import assemble_turns
from agent_memory_exporter.gap_detector import detect_gaps_in_events


def _ts_to_datetime(ts: int | str | float) -> datetime:
    """Convert a Unix epoch millisecond timestamp (int/float/str) to datetime."""
    if isinstance(ts, str):
        ts = float(ts)
    # WorkBuddy uses millisecond epoch
    if ts > 1e12:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _escape_cwd_to_dirname(cwd: str) -> str:
    """Escape a cwd path to the projects/ directory name convention.

    Observed rule: lowercase drive letter, replace backslashes with hyphens,
    remove the colon after the drive letter.

    Example: C:\\Users\\Admin\\WorkBuddy\\2026-06-17 → c-Users-Admin-WorkBuddy-2026-06-17
    """
    result = cwd.replace("\\", "-").replace("/", "-")
    # Remove colon (e.g. "C:" → "C" then lowercase)
    result = result.replace(":", "")
    # Lowercase only the drive letter prefix (e.g. "C..." → "c...")
    if len(result) >= 1 and result[0].isupper():
        result = result[0].lower() + result[1:]
    return result


def _extract_text_from_content(content: Any) -> str:
    """Extract plain text from a message content field.

    WorkBuddy content can be:
    - A list of dicts: [{"type": "input_text", "text": "..."}, ...]
    - A string (legacy)
    - None
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)


class WorkBuddyAdapter:
    """Read-only adapter for WorkBuddy local data."""

    def __init__(
        self,
        home_dir: str | Path | None = None,
    ) -> None:
        if home_dir is None:
            home_dir = Path.home()
        self.home_dir = Path(home_dir)
        self.workbuddy_dir = self.home_dir / ".workbuddy"
        self.db_path = self.workbuddy_dir / "workbuddy.db"
        self.projects_dir = self.workbuddy_dir / "projects"

    @property
    def name(self) -> str:
        return "workbuddy"

    def detect(self) -> bool:
        """Check if WorkBuddy data exists."""
        return self.db_path.exists() and self.projects_dir.exists()

    def _connect_db(self) -> sqlite3.Connection:
        """Open a read-only connection to workbuddy.db."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def list_sessions(self, since: datetime | None = None) -> list[SessionRef]:
        """List sessions from the DB, optionally filtered by updated_at > since."""
        if not self.db_path.exists():
            return []

        conn = self._connect_db()
        try:
            if since is not None:
                since_ms = int(since.timestamp() * 1000)
                cursor = conn.execute(
                    """
                    SELECT id, cwd, title, updated_at, created_at
                    FROM sessions
                    WHERE deleted_at IS NULL AND updated_at > ?
                    ORDER BY updated_at ASC
                    """,
                    (since_ms,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT id, cwd, title, updated_at, created_at
                    FROM sessions
                    WHERE deleted_at IS NULL
                    ORDER BY updated_at ASC
                    """,
                )

            refs: list[SessionRef] = []
            for row in cursor.fetchall():
                session_id = row["id"]
                cwd = row["cwd"] or ""
                conversation_file = self._find_conversation_file(session_id, cwd)
                refs.append(
                    SessionRef(
                        session_id=session_id,
                        updated_at=_ts_to_datetime(row["updated_at"]),
                        cwd=cwd,
                        title=row["title"],
                        conversation_file=str(conversation_file) if conversation_file else None,
                    )
                )
            return refs
        finally:
            conn.close()

    def _find_conversation_file(self, session_id: str, cwd: str) -> Path | None:
        """Find the jsonl file for a session.

        Strategy:
        1. Try escaped cwd → projects/<escaped-cwd>/<session_id>.jsonl
        2. Fallback: traverse projects/ subdirs looking for <session_id>.jsonl
        """
        # Strategy 1: escaped cwd
        escaped = _escape_cwd_to_dirname(cwd)
        candidate = self.projects_dir / escaped / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate

        # Strategy 2: traverse all project dirs
        if self.projects_dir.exists():
            for proj_dir in self.projects_dir.iterdir():
                if not proj_dir.is_dir():
                    continue
                candidate = proj_dir / f"{session_id}.jsonl"
                if candidate.exists():
                    return candidate

        return None

    def read_session(self, session_id: str) -> RawSession:
        """Read a session: DB metadata + jsonl events → assembled RawSession."""
        # 1. Read session metadata from DB
        meta = self._read_session_meta(session_id)

        # 2. Find and read the main conversation jsonl
        conversation_file = self._find_conversation_file(session_id, meta.cwd or "")
        if conversation_file is None:
            return RawSession(
                session_id=session_id,
                source="workbuddy",
                session_meta=meta,
                turns=[],
                gaps=[Gap(gap_type=GapType.MISSING_JSONL, details=f"No jsonl file found for session {session_id}")],
            )

        # 3. Read main conversation events
        events, parse_gaps = self._read_jsonl_events(conversation_file, session_id)

        # 4. Read sub-agent jsonl files in the same directory
        sub_agent_files: list[str] = []
        agent_events, agent_gaps = self._read_sub_agent_files(conversation_file.parent, session_id)
        events.extend(agent_events)
        sub_agent_files = [f.name for f in self._find_sub_agent_files(conversation_file.parent)]
        meta.sub_agents = sub_agent_files

        # 5. Sort all events by timestamp
        events.sort(key=lambda e: e["timestamp_dt"])

        # 6. Detect gaps
        all_gaps = parse_gaps + agent_gaps + detect_gaps_in_events(events)

        # 7. Assemble turns
        turns = assemble_turns(events)

        # 8. Update meta counts
        meta.turn_count = len([t for t in turns if t.turn_index >= 0])
        meta.event_count = len(events)

        return RawSession(
            session_id=session_id,
            source="workbuddy",
            session_meta=meta,
            turns=turns,
            gaps=all_gaps,
        )

    def _read_session_meta(self, session_id: str) -> SessionMeta:
        """Read session metadata from the database."""
        conn = self._connect_db()
        try:
            cursor = conn.execute(
                """
                SELECT id, cwd, title, mode, model, created_at, updated_at
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return SessionMeta()
            return SessionMeta(
                title=row["title"],
                cwd=row["cwd"],
                mode=row["mode"],
                model=row["model"],
                created_at=_ts_to_datetime(row["created_at"]),
                updated_at=_ts_to_datetime(row["updated_at"]),
            )
        finally:
            conn.close()

    def _read_jsonl_events(
        self, file_path: Path, session_id: str
    ) -> tuple[list[dict[str, Any]], list[Gap]]:
        """Read a jsonl file and return a list of raw event dicts (with parsed timestamp_dt).

        Returns (events, gaps) where each event dict has a 'timestamp_dt' key
        added for sorting.
        """
        events: list[dict[str, Any]] = []
        gaps: list[Gap] = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError as e:
                        gaps.append(
                            Gap(
                                gap_type=GapType.PARSE_ERROR,
                                details=f"{file_path.name}:{line_num}: {e}",
                            )
                        )
                        continue

                    # Skip non-conversation events
                    evt_type = evt.get("type", "")
                    if evt_type == "ai-title":
                        continue

                    # Parse timestamp
                    ts_raw = evt.get("timestamp")
                    if ts_raw is None:
                        gaps.append(
                            Gap(
                                gap_type=GapType.PARSE_ERROR,
                                details=f"{file_path.name}:{line_num}: missing timestamp",
                                event_id=evt.get("id"),
                            )
                        )
                        continue

                    try:
                        evt["timestamp_dt"] = _ts_to_datetime(ts_raw)
                    except (ValueError, TypeError, OSError) as e:
                        gaps.append(
                            Gap(
                                gap_type=GapType.PARSE_ERROR,
                                details=f"{file_path.name}:{line_num}: bad timestamp {ts_raw}: {e}",
                                event_id=evt.get("id"),
                            )
                        )
                        continue

                    events.append(evt)
        except FileNotFoundError:
            gaps.append(
                Gap(
                    gap_type=GapType.MISSING_JSONL,
                    details=f"File not found: {file_path}",
                )
            )
        except PermissionError:
            gaps.append(
                Gap(
                    gap_type=GapType.MISSING_JSONL,
                    details=f"Permission denied: {file_path}",
                )
            )

        return events, gaps

    def _find_sub_agent_files(self, project_dir: Path) -> list[Path]:
        """Find agent-*.jsonl files in a project directory."""
        return sorted(project_dir.glob("agent-*.jsonl"))

    def _read_sub_agent_files(
        self, project_dir: Path, main_session_id: str
    ) -> tuple[list[dict[str, Any]], list[Gap]]:
        """Read sub-agent jsonl files. Each event gets role='sub_agent' marker via a flag.

        Sub-agent events are returned as a list of dicts with the same structure
        as main events but with an extra '_is_sub_agent' flag and '_sub_agent_session_id'.
        The assembler will treat sub-agent messages as assistant events.
        """
        events: list[dict[str, Any]] = []
        gaps: list[Gap] = []

        for agent_file in self._find_sub_agent_files(project_dir):
            agent_events, agent_gaps = self._read_jsonl_events(agent_file, main_session_id)
            for evt in agent_events:
                evt["_is_sub_agent"] = True
                evt["_sub_agent_session_id"] = evt.get("sessionId", "")
            events.extend(agent_events)
            gaps.extend(agent_gaps)

        return events, gaps
