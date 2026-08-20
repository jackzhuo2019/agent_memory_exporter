"""Path constants for the agent_export directory structure."""

from __future__ import annotations

from pathlib import Path


class ExportPaths:
    """Manages the export directory layout.

    Default structure:
        <root>/
        ├── raw/                    # CLI output: raw session JSON
        ├── processed/              # MCP output: cleaned markdown
        ├── state.json              # Incremental export state
        ├── upload_state.json       # WeKnora upload tracking
        ├── redaction_report.json   # PII redaction statistics
        ├── gaps.json               # Data integrity report
        └── manifest.json           # Export index
    """

    def __init__(self, root: str | Path = "./agent_export") -> None:
        self.root = Path(root).resolve()

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.root / "processed"

    @property
    def state_file(self) -> Path:
        return self.root / "state.json"

    @property
    def upload_state_file(self) -> Path:
        return self.root / "upload_state.json"

    @property
    def redaction_report_file(self) -> Path:
        return self.root / "redaction_report.json"

    @property
    def gaps_file(self) -> Path:
        return self.root / "gaps.json"

    @property
    def manifest_file(self) -> Path:
        return self.root / "manifest.json"

    def raw_session_path(self, session_id: str) -> Path:
        """Path for a single session's raw JSON file."""
        return self.raw_dir / f"{session_id}.json"

    def processed_session_path(self, session_id: str) -> Path:
        """Path for a single session's processed markdown file."""
        return self.processed_dir / f"{session_id}.md"

    def ensure_dirs(self) -> None:
        """Create all required directories."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
