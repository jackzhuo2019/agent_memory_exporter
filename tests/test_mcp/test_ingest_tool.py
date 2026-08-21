"""Tests for the MCP Server ingest tool."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_memory_mcp.tools.ingest_tool import (
    IngestTool,
    IngestResult,
    parse_front_matter,
)
from agent_memory_mcp.weknora_client import UploadResult, WeKnoraClient
from agent_memory_shared.paths import ExportPaths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_processed_md(
    paths_root: Path,
    session_id: str,
    quality: str = "high",
    title: str = "Test Doc",
    body: str = "# Test Doc\n\nUseful content.",
) -> Path:
    """Write a processed/*.md file with YAML front-matter."""
    processed_dir = paths_root / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / f"{session_id}.md"
    content = (
        "---\n"
        f"quality: {quality}\n"
        f"source: workbuddy\n"
        f"session_id: {session_id}\n"
        f"cleaned_at: 2025-01-15T12:00:00Z\n"
        f"title: {title}\n"
        "---\n\n"
        f"{body}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def make_mock_weknora(
    upload_results: list[UploadResult] | None = None,
) -> MagicMock:
    """Create a mock WeKnoraClient."""
    client = MagicMock(spec=WeKnoraClient)
    client.config = MagicMock()
    client.config.default_kb_id = ""
    client.close = AsyncMock()

    if upload_results:
        responses = iter(upload_results)
        client.upload_file = AsyncMock(side_effect=lambda kb_id, file_path, **kw: next(responses))
    else:
        client.upload_file = AsyncMock(
            return_value=UploadResult(knowledge_id="k-default", success=True, status_code=201)
        )

    return client


# ---------------------------------------------------------------------------
# Unit tests: parse_front_matter
# ---------------------------------------------------------------------------


class TestParseFrontMatter:
    def test_basic_front_matter(self):
        content = (
            "---\n"
            "quality: high\n"
            "source: workbuddy\n"
            "session_id: abc-123\n"
            "title: Test\n"
            "---\n\n"
            "# Test\n\nContent."
        )
        fm, body = parse_front_matter(content)
        assert fm["quality"] == "high"
        assert fm["source"] == "workbuddy"
        assert fm["session_id"] == "abc-123"
        assert fm["title"] == "Test"
        assert "# Test" in body

    def test_no_front_matter(self):
        content = "# Just markdown\n\nNo front matter."
        fm, body = parse_front_matter(content)
        assert fm == {}
        assert body == content

    def test_empty_front_matter(self):
        content = "---\n---\n\n# Body"
        fm, body = parse_front_matter(content)
        assert fm == {}
        assert "# Body" in body

    def test_malformed_front_matter(self):
        content = "---\n: invalid: yaml: ---\n---\n\n# Body"
        fm, body = parse_front_matter(content)
        assert fm == {}
        assert content in body or "# Body" in body

    def test_missing_closing_fence(self):
        content = "---\nquality: high\n\n# Body without closing"
        fm, body = parse_front_matter(content)
        assert fm == {}
        assert body == content


# ---------------------------------------------------------------------------
# Integration tests: IngestTool.run
# ---------------------------------------------------------------------------


class TestIngestTool:
    def test_tool_definition(self):
        tool = IngestTool()
        defn = tool.get_tool_definition()
        assert defn.name == "ingest"
        assert "kb_id" in defn.input_schema["properties"]
        assert "output" in defn.input_schema["properties"]
        assert "limit" in defn.input_schema["properties"]
        assert "force" in defn.input_schema["properties"]
        assert "dry_run" in defn.input_schema["properties"]

    def test_ingest_high_quality(self, tmp_path: Path):
        """High quality file should be uploaded."""
        write_processed_md(tmp_path, "hq-001", quality="high")
        mock_client = make_mock_weknora()
        tool = IngestTool(weknora_client=mock_client)

        result = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path)))

        assert result.ingested == 1
        assert result.failed == 0
        assert result.skipped == 0
        assert result.quality_filtered == 0
        assert result.kb_id == "kb-test"
        mock_client.upload_file.assert_called_once()

        # Check upload_state.json
        state_path = tmp_path / "upload_state.json"
        assert state_path.exists()
        import json
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "hq-001" in state["records"]
        assert state["records"]["hq-001"]["status"] == "uploaded"
        assert state["records"]["hq-001"]["knowledge_id"] == "k-default"

    def test_ingest_medium_quality(self, tmp_path: Path):
        """Medium quality file should also be uploaded."""
        write_processed_md(tmp_path, "mq-001", quality="medium")
        mock_client = make_mock_weknora()
        tool = IngestTool(weknora_client=mock_client)

        result = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path)))

        assert result.ingested == 1
        mock_client.upload_file.assert_called_once()

    def test_ingest_low_quality_filtered(self, tmp_path: Path):
        """Low quality file should be skipped (quality_filtered)."""
        write_processed_md(tmp_path, "lq-001", quality="low")
        mock_client = make_mock_weknora()
        tool = IngestTool(weknora_client=mock_client)

        result = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path)))

        assert result.ingested == 0
        assert result.quality_filtered == 1
        mock_client.upload_file.assert_not_called()

    def test_ingest_trash_quality_filtered(self, tmp_path: Path):
        """Trash quality file should be skipped (quality_filtered)."""
        write_processed_md(tmp_path, "trash-001", quality="trash")
        mock_client = make_mock_weknora()
        tool = IngestTool(weknora_client=mock_client)

        result = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path)))

        assert result.ingested == 0
        assert result.quality_filtered == 1
        mock_client.upload_file.assert_not_called()

    def test_ingest_no_quality_filtered(self, tmp_path: Path):
        """File without quality in front-matter should be filtered out."""
        # Write a file without quality field
        processed_dir = tmp_path / "processed"
        processed_dir.mkdir(parents=True)
        (processed_dir / "noq-001.md").write_text(
            "---\nsource: workbuddy\n---\n\n# No Quality\n", encoding="utf-8"
        )
        mock_client = make_mock_weknora()
        tool = IngestTool(weknora_client=mock_client)

        result = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path)))

        assert result.ingested == 0
        assert result.quality_filtered == 1

    def test_ingest_skip_already_uploaded(self, tmp_path: Path):
        """Already uploaded files should be skipped."""
        write_processed_md(tmp_path, "skip-001", quality="high")
        mock_client = make_mock_weknora()
        tool = IngestTool(weknora_client=mock_client)

        # First run — upload
        result1 = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path)))
        assert result1.ingested == 1

        # Reset mock for second run
        mock_client.upload_file.reset_mock()

        # Second run — should skip
        result2 = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path)))
        assert result2.ingested == 0
        assert result2.skipped == 1
        mock_client.upload_file.assert_not_called()

    def test_ingest_force_reuploads(self, tmp_path: Path):
        """With force=True, already uploaded files are re-ingested."""
        write_processed_md(tmp_path, "force-001", quality="high")
        mock_client = make_mock_weknora()
        tool = IngestTool(weknora_client=mock_client)

        # First run
        result1 = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path)))
        assert result1.ingested == 1

        mock_client.upload_file.reset_mock()

        # Second run with force
        result2 = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path), force=True))
        assert result2.ingested == 1
        assert result2.skipped == 0
        mock_client.upload_file.assert_called_once()

    def test_ingest_limit(self, tmp_path: Path):
        """Limit caps the number of files ingested."""
        for i in range(5):
            write_processed_md(tmp_path, f"lim-{i:03d}", quality="high")
        mock_client = make_mock_weknora()
        tool = IngestTool(weknora_client=mock_client)

        result = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path), limit=2))

        assert result.ingested == 2
        assert result.total_files == 5

    def test_ingest_dry_run(self, tmp_path: Path):
        """Dry run counts but does not upload."""
        write_processed_md(tmp_path, "dry-001", quality="high")
        mock_client = make_mock_weknora()
        tool = IngestTool(weknora_client=mock_client)

        result = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path), dry_run=True))

        assert result.ingested == 1
        mock_client.upload_file.assert_not_called()

        # upload_state.json should still be saved but no uploaded records
        state_path = tmp_path / "upload_state.json"
        assert state_path.exists()

    def test_ingest_upload_failure(self, tmp_path: Path):
        """Upload failure should be recorded, not crash."""
        write_processed_md(tmp_path, "fail-001", quality="high")
        mock_client = make_mock_weknora(
            [UploadResult(success=False, status_code=500, error="Server error")]
        )
        tool = IngestTool(weknora_client=mock_client)

        result = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path)))

        assert result.ingested == 0
        assert result.failed == 1
        assert len(result.error_details) == 1
        assert "fail-001" in result.error_details[0]

    def test_ingest_exception_continues(self, tmp_path: Path):
        """Exception during upload should not stop processing."""
        write_processed_md(tmp_path, "err-001", quality="high")
        write_processed_md(tmp_path, "ok-001", quality="high")

        good = UploadResult(knowledge_id="k-ok", success=True, status_code=201)
        mock_client = make_mock_weknora(
            [RuntimeError("Connection timeout"), good]
        )
        tool = IngestTool(weknora_client=mock_client)

        result = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path)))

        assert result.failed == 1
        assert result.ingested == 1
        assert any("err-001" in d for d in result.error_details)

    def test_ingest_mixed_qualities(self, tmp_path: Path):
        """Mixed quality files: only high/medium uploaded, low/trash filtered."""
        write_processed_md(tmp_path, "multi-high", quality="high")
        write_processed_md(tmp_path, "multi-med", quality="medium")
        write_processed_md(tmp_path, "multi-low", quality="low")
        write_processed_md(tmp_path, "multi-trash", quality="trash")

        mock_client = make_mock_weknora()
        tool = IngestTool(weknora_client=mock_client)

        result = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path)))

        assert result.ingested == 2  # high + medium
        assert result.quality_filtered == 2  # low + trash
        assert result.total_files == 4

    def test_ingest_no_processed_dir(self, tmp_path: Path):
        """Raises RuntimeError if processed/ doesn't exist."""
        mock_client = make_mock_weknora()
        tool = IngestTool(weknora_client=mock_client)
        with pytest.raises(RuntimeError, match="Processed directory not found"):
            asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path / "nonexistent")))

    def test_ingest_no_kb_id(self, tmp_path: Path):
        """Raises RuntimeError if no kb_id and no config default."""
        write_processed_md(tmp_path, "nokb-001", quality="high")
        # Mock client with no default kb_id
        mock_client = MagicMock(spec=WeKnoraClient)
        mock_client.config = MagicMock()
        mock_client.config.default_kb_id = ""
        mock_client.close = AsyncMock()
        mock_client.upload_file = AsyncMock()

        tool = IngestTool(weknora_client=mock_client)
        with pytest.raises(RuntimeError, match="No kb_id"):
            asyncio.run(tool.run(output=str(tmp_path)))

    def test_ingest_uses_config_default_kb_id(self, tmp_path: Path):
        """Uses config default_kb_id when kb_id param is empty."""
        write_processed_md(tmp_path, "defkb-001", quality="high")
        mock_client = MagicMock(spec=WeKnoraClient)
        mock_client.config = MagicMock()
        mock_client.config.default_kb_id = "kb-from-config"
        mock_client.close = AsyncMock()
        mock_client.upload_file = AsyncMock(
            return_value=UploadResult(knowledge_id="k-1", success=True, status_code=201)
        )

        tool = IngestTool(weknora_client=mock_client)
        result = asyncio.run(tool.run(output=str(tmp_path)))

        assert result.ingested == 1
        assert result.kb_id == "kb-from-config"
        mock_client.upload_file.assert_called_once()
        call_args = mock_client.upload_file.call_args
        assert call_args[0][0] == "kb-from-config"

    def test_ingest_max_retries_exceeded(self, tmp_path: Path):
        """Files that hit max retries should be skipped, not re-attempted."""
        write_processed_md(tmp_path, "maxret-001", quality="high")

        # Create upload_state with 3 prior failures
        from agent_memory_mcp.upload_state import UploadState, record_failure, save_upload_state
        state = UploadState()
        for _ in range(3):
            record_failure(state, "maxret-001", "maxret-001.md", "high", "prev error")
        save_upload_state(state, tmp_path / "upload_state.json")

        mock_client = make_mock_weknora()
        tool = IngestTool(weknora_client=mock_client)

        result = asyncio.run(tool.run(kb_id="kb-test", output=str(tmp_path)))

        assert result.ingested == 0
        assert result.skipped == 1
        mock_client.upload_file.assert_not_called()
