"""Tests for the MCP Server clean tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_memory_shared.models import Event, EventRole, RawSession, SessionMeta, Turn
from agent_memory_mcp.llm_client import LLMResponse
from agent_memory_mcp.tools.clean_tool import (
    CleanTool,
    build_processed_markdown,
    parse_llm_response,
    session_to_user_prompt,
)
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session(
    session_id: str = "clean-test-001",
    source: str = "workbuddy",
    title: str = "Test Session",
    turns: int = 1,
) -> RawSession:
    """Build a minimal RawSession for testing."""
    turn_list = []
    for i in range(turns):
        turn = Turn(
            turn_index=i,
            user_message=f"How do I fix bug {i}?",
            user_timestamp=datetime(2025, 1, 15, 12, 0, i, tzinfo=timezone.utc),
            events=[
                Event(
                    role=EventRole.ASSISTANT,
                    content=f"Here is the solution to bug {i}.",
                    timestamp=datetime(2025, 1, 15, 12, 0, i + 1, tzinfo=timezone.utc),
                ),
            ],
        )
        turn_list.append(turn)

    return RawSession(
        schema_version="1.0.0",
        session_id=session_id,
        source=source,
        session_meta=SessionMeta(
            title=title,
            cwd="/test",
            model="test-model",
            created_at=datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            turn_count=turns,
            event_count=turns,
        ),
        turns=turn_list,
    )


def write_raw_session(paths_root: Path, session: RawSession) -> Path:
    """Write a RawSession to raw/<session_id>.json."""
    raw_dir = paths_root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{session.session_id}.json"
    session.to_json_file(path)
    return path


def make_mock_llm(
    quality: str = "high",
    title: str = "Mock Title",
    markdown: str = "# Mock Title\n\nMock content.",
) -> MagicMock:
    """Create a mock LLMClient that returns a fixed response."""
    client = MagicMock()
    client.chat = AsyncMock(
        return_value=LLMResponse(
            content=json.dumps({"quality": quality, "title": title, "markdown": markdown}),
            model="mock-model",
            usage={"total_tokens": 100},
        )
    )
    client.close = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Unit tests: parse_llm_response
# ---------------------------------------------------------------------------


class TestParseLLMResponse:
    def test_valid_json(self):
        content = json.dumps({
            "quality": "high",
            "title": "Bug Fix Guide",
            "markdown": "# Bug Fix Guide\n\nContent here.",
        })
        result = parse_llm_response(content)
        assert result["quality"] == "high"
        assert result["title"] == "Bug Fix Guide"
        assert "# Bug Fix Guide" in result["markdown"]

    def test_markdown_fences(self):
        content = "```json\n" + json.dumps({
            "quality": "medium",
            "title": "Test",
            "markdown": "# Test\n\nContent.",
        }) + "\n```"
        result = parse_llm_response(content)
        assert result["quality"] == "medium"
        assert result["title"] == "Test"

    def test_extra_text_around_json(self):
        content = "Here is the result:\n" + json.dumps({
            "quality": "low",
            "title": "Extra",
            "markdown": "# Extra",
        }) + "\nDone."
        result = parse_llm_response(content)
        assert result["quality"] == "low"
        assert result["title"] == "Extra"

    def test_invalid_quality_defaults_to_low(self):
        content = json.dumps({"quality": "excellent", "title": "T", "markdown": "# T"})
        result = parse_llm_response(content)
        assert result["quality"] == "low"

    def test_missing_quality_defaults_to_low(self):
        content = json.dumps({"title": "T", "markdown": "# T"})
        result = parse_llm_response(content)
        assert result["quality"] == "low"

    def test_empty_markdown_gets_placeholder(self):
        content = json.dumps({"quality": "high", "title": "T", "markdown": ""})
        result = parse_llm_response(content)
        assert "# T" in result["markdown"]

    def test_completely_invalid_returns_low(self):
        result = parse_llm_response("This is not JSON at all")
        assert result["quality"] == "low"

    def test_trash_quality(self):
        content = json.dumps({"quality": "trash", "title": "Trash", "markdown": ""})
        result = parse_llm_response(content)
        assert result["quality"] == "trash"


# ---------------------------------------------------------------------------
# Unit tests: session_to_user_prompt
# ---------------------------------------------------------------------------


class TestSessionToUserPrompt:
    def test_basic_conversion(self):
        session = make_session()
        prompt = session_to_user_prompt(session)
        data = json.loads(prompt)
        assert data["session_id"] == "clean-test-001"
        assert data["source"] == "workbuddy"
        assert len(data["turns"]) == 1
        assert "How do I fix bug" in data["turns"][0]["user_message"]

    def test_truncates_long_tool_output(self):
        session = make_session()
        long_output = "x" * 5000
        session.turns[0].events[0] = Event(
            role=EventRole.TOOL,
            content="tool result",
            tool_name="bash",
            tool_output=long_output,
            timestamp=datetime(2025, 1, 15, 12, 0, 1, tzinfo=timezone.utc),
        )
        prompt = session_to_user_prompt(session)
        data = json.loads(prompt)
        tool_output = data["turns"][0]["events"][0]["tool_output"]
        assert len(tool_output) < len(long_output)
        assert "[truncated]" in tool_output


# ---------------------------------------------------------------------------
# Unit tests: build_processed_markdown
# ---------------------------------------------------------------------------


class TestBuildProcessedMarkdown:
    def test_front_matter(self):
        session = make_session()
        md = build_processed_markdown("high", "Bug Fix", "# Bug Fix\n\nContent", session)
        assert md.startswith("---\n")
        assert "quality: high" in md
        assert "source: workbuddy" in md
        assert "session_id: clean-test-001" in md
        assert "title: Bug Fix" in md
        assert "---\n\n# Bug Fix" in md

    def test_ends_with_newline(self):
        session = make_session()
        md = build_processed_markdown("medium", "T", "# T\n\nC", session)
        assert md.endswith("\n")


# ---------------------------------------------------------------------------
# Integration tests: CleanTool.run
# ---------------------------------------------------------------------------


class TestCleanTool:
    def test_tool_definition(self):
        tool = CleanTool()
        defn = tool.get_tool_definition()
        assert defn.name == "clean"
        assert "output" in defn.input_schema["properties"]
        assert "limit" in defn.input_schema["properties"]
        assert "force" in defn.input_schema["properties"]
        assert "config_path" in defn.input_schema["properties"]

    def test_clean_high_quality(self, tmp_path: Path):
        """Clean a session that LLM rates as high quality → processed/*.md written."""
        session = make_session(session_id="hq-001", title="High Quality Session")
        write_raw_session(tmp_path, session)

        mock_llm = make_mock_llm(quality="high", title="Knowledge Article", markdown="# Knowledge\n\nUseful info.")
        tool = CleanTool(llm_client=mock_llm)

        result = asyncio.run(tool.run(output=str(tmp_path)))

        assert result.cleaned == 1
        assert result.skipped == 0
        assert result.errors == 0
        assert result.trash == 0
        assert result.total_sessions == 1
        assert result.quality_counts["high"] == 1

        processed_file = tmp_path / "processed" / "hq-001.md"
        assert processed_file.exists()
        content = processed_file.read_text(encoding="utf-8")
        assert "quality: high" in content
        assert "session_id: hq-001" in content
        assert "# Knowledge" in content

    def test_clean_trash_skipped(self, tmp_path: Path):
        """Sessions rated trash should not produce a processed file."""
        session = make_session(session_id="trash-001")
        write_raw_session(tmp_path, session)

        mock_llm = make_mock_llm(quality="trash", title="Trash", markdown="")
        tool = CleanTool(llm_client=mock_llm)

        result = asyncio.run(tool.run(output=str(tmp_path)))

        assert result.trash == 1
        assert result.cleaned == 0
        processed_file = tmp_path / "processed" / "trash-001.md"
        assert not processed_file.exists()

    def test_clean_skip_existing(self, tmp_path: Path):
        """Sessions with existing processed/*.md are skipped unless force=True."""
        session = make_session(session_id="skip-001")
        write_raw_session(tmp_path, session)

        # Pre-create the processed file
        processed_dir = tmp_path / "processed"
        processed_dir.mkdir(parents=True, exist_ok=True)
        (processed_dir / "skip-001.md").write_text("# Existing\n", encoding="utf-8")

        mock_llm = make_mock_llm()
        tool = CleanTool(llm_client=mock_llm)

        result = asyncio.run(tool.run(output=str(tmp_path)))

        assert result.skipped == 1
        assert result.cleaned == 0
        mock_llm.chat.assert_not_called()

    def test_clean_force_overwrites(self, tmp_path: Path):
        """With force=True, existing processed files are re-cleaned."""
        session = make_session(session_id="force-001")
        write_raw_session(tmp_path, session)

        processed_dir = tmp_path / "processed"
        processed_dir.mkdir(parents=True, exist_ok=True)
        (processed_dir / "force-001.md").write_text("# Old\n", encoding="utf-8")

        mock_llm = make_mock_llm(quality="high", title="New", markdown="# New Content")
        tool = CleanTool(llm_client=mock_llm)

        result = asyncio.run(tool.run(output=str(tmp_path), force=True))

        assert result.cleaned == 1
        assert result.skipped == 0
        content = (processed_dir / "force-001.md").read_text(encoding="utf-8")
        assert "# New Content" in content
        assert "# Old" not in content

    def test_clean_limit(self, tmp_path: Path):
        """Limit caps the number of sessions cleaned."""
        for i in range(5):
            session = make_session(session_id=f"lim-{i:03d}")
            write_raw_session(tmp_path, session)

        mock_llm = make_mock_llm()
        tool = CleanTool(llm_client=mock_llm)

        result = asyncio.run(tool.run(output=str(tmp_path), limit=2))

        assert result.cleaned == 2
        assert result.total_sessions == 5

    def test_clean_no_raw_dir(self, tmp_path: Path):
        """Raises RuntimeError if raw/ doesn't exist."""
        tool = CleanTool(llm_client=make_mock_llm())
        with pytest.raises(RuntimeError, match="Raw directory not found"):
            asyncio.run(tool.run(output=str(tmp_path / "nonexistent")))

    def test_clean_multiple_qualities(self, tmp_path: Path):
        """Mixed quality sessions produce correct counts."""
        sessions = [
            ("multi-high", "high", "High Content"),
            ("multi-med", "medium", "Medium Content"),
            ("multi-low", "low", "Low Content"),
            ("multi-trash", "trash", ""),
        ]
        for sid, _, _ in sessions:
            write_raw_session(tmp_path, make_session(session_id=sid))

        # Create a mock that returns different quality per session
        responses = iter([
            LLMResponse(
                content=json.dumps({"quality": q, "title": t, "markdown": f"# {t}\n\nContent."}),
                model="mock-model",
                usage={},
            )
            for sid, q, t in sessions
        ])

        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=lambda sys, user, **kw: next(responses))
        mock_llm.close = AsyncMock()

        tool = CleanTool(llm_client=mock_llm)
        result = asyncio.run(tool.run(output=str(tmp_path)))

        assert result.cleaned == 3  # high + medium + low
        assert result.trash == 1
        assert result.quality_counts["high"] == 1
        assert result.quality_counts["medium"] == 1
        assert result.quality_counts["low"] == 1
        assert result.quality_counts["trash"] == 1

    def test_clean_llm_error_continues(self, tmp_path: Path):
        """LLM error on one session should not stop processing others."""
        write_raw_session(tmp_path, make_session(session_id="err-001"))
        write_raw_session(tmp_path, make_session(session_id="ok-001"))

        good_response = LLMResponse(
            content=json.dumps({"quality": "high", "title": "OK", "markdown": "# OK"}),
            model="mock",
            usage={},
        )

        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(
            side_effect=[RuntimeError("LLM timeout"), good_response]
        )
        mock_llm.close = AsyncMock()

        tool = CleanTool(llm_client=mock_llm)
        result = asyncio.run(tool.run(output=str(tmp_path)))

        assert result.errors == 1
        assert result.cleaned == 1
        assert any("err-001" in d for d in result.error_details)
        assert (tmp_path / "processed" / "ok-001.md").exists()

    def test_clean_llm_model_recorded(self, tmp_path: Path):
        """The LLM model name is recorded in the result."""
        write_raw_session(tmp_path, make_session(session_id="model-001"))
        mock_llm = make_mock_llm()
        tool = CleanTool(llm_client=mock_llm)

        result = asyncio.run(tool.run(output=str(tmp_path)))

        assert result.llm_model == "mock-model"
