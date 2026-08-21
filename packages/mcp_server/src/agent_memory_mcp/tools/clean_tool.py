"""MCP clean tool: LLM-based cleaning of raw session JSON into knowledge markdown.

Reads raw/*.json files produced by the export tool, sends each session to an
LLM for distillation, and writes processed/*.md files with quality tags.

Quality tags (in YAML front-matter):
    high:    significant reusable knowledge
    medium:  some useful information
    low:     minimal knowledge value
    trash:   no knowledge value (not written to disk)

Only high/medium/low files are written. Trash sessions are skipped.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp import Tool
from pydantic import BaseModel, Field

from agent_memory_shared.models import RawSession
from agent_memory_shared.paths import ExportPaths

from agent_memory_mcp.llm_client import LLMClient, LLMConfig

# Path to the clean prompt template (relative to this file)
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROMPT_PATH = _PROMPTS_DIR / "clean_prompt.md"


def load_clean_prompt() -> str:
    """Load the system prompt from clean_prompt.md."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def session_to_user_prompt(session: RawSession) -> str:
    """Convert a RawSession to the user prompt text for the LLM.

    We serialize the session as compact JSON, omitting large tool_output
    fields that exceed a threshold to avoid token explosion.
    """
    data = session.model_dump(mode="json", exclude_none=True)

    # Truncate very long tool outputs to keep prompt size manageable
    MAX_TOOL_OUTPUT = 2000
    for turn in data.get("turns", []):
        for event in turn.get("events", []):
            for field_name in ("tool_output", "tool_input"):
                val = event.get(field_name)
                if isinstance(val, str) and len(val) > MAX_TOOL_OUTPUT:
                    event[field_name] = val[:MAX_TOOL_OUTPUT] + "\n... [truncated]"

    return json.dumps(data, ensure_ascii=False, indent=2)


def parse_llm_response(content: str) -> dict[str, str]:
    """Parse the LLM JSON response into {quality, title, markdown}.

    The LLM is instructed to return a JSON object. We handle common
    failure modes: markdown fences, extra text, partial JSON.
    """
    text = content.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first fence line
        lines = lines[1:]
        # Remove trailing fence if present
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try to parse as JSON
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from surrounding text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                result = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {"quality": "low", "title": "Parse Error", "markdown": content}
        else:
            return {"quality": "low", "title": "Parse Error", "markdown": content}

    quality = result.get("quality", "low").lower().strip()
    if quality not in ("high", "medium", "low", "trash"):
        quality = "low"

    title = result.get("title", "Untitled").strip() or "Untitled"
    markdown = result.get("markdown", "").strip()
    if not markdown:
        markdown = f"# {title}\n\n(Cleaned content was empty.)"

    return {"quality": quality, "title": title, "markdown": markdown}


def build_processed_markdown(quality: str, title: str, markdown: str, session: RawSession) -> str:
    """Build the final processed markdown with YAML front-matter.

    Front-matter includes:
        ---
        quality: high
        source: workbuddy
        session_id: abc-123
        cleaned_at: 2025-01-15T12:00:00Z
        title: Descriptive Title
        ---
    """
    cleaned_at = datetime.now(timezone.utc).isoformat()
    front_matter = (
        "---\n"
        f"quality: {quality}\n"
        f"source: {session.source}\n"
        f"session_id: {session.session_id}\n"
        f"cleaned_at: {cleaned_at}\n"
        f"title: {title}\n"
        "---\n\n"
    )
    return front_matter + markdown + "\n"


class CleanResult(BaseModel):
    """Result of the clean tool."""

    cleaned: int = 0
    skipped: int = 0
    errors: int = 0
    trash: int = 0
    total_sessions: int = 0
    quality_counts: dict[str, int] = Field(default_factory=lambda: {"high": 0, "medium": 0, "low": 0, "trash": 0})
    error_details: list[str] = Field(default_factory=list)
    llm_model: str = ""

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


class CleanTool:
    """MCP tool that cleans raw session JSON into knowledge markdown using an LLM.

    Parameters:
        output: output directory containing raw/ and processed/ (default: "./agent_export")
        limit: maximum number of sessions to clean (default: none)
        force: re-clean sessions that already have processed/*.md (default: false)
        config_path: path to mcp_config.yaml (default: config/mcp_config.yaml)
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client

    def get_tool_definition(self) -> Tool:
        return Tool(
            name="clean",
            description=(
                "Clean raw agent session JSON files into knowledge-rich markdown "
                "using an LLM. Reads raw/*.json, sends each session to an LLM for "
                "distillation, and writes processed/*.md with quality tags "
                "(high/medium/low/trash). Trash sessions are skipped. "
                "Only sessions not yet cleaned (or with --force) are processed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "output": {
                        "type": "string",
                        "default": "./agent_export",
                        "description": "Output directory containing raw/ and processed/ subdirs.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of sessions to clean.",
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": "Re-clean sessions that already have processed/*.md.",
                    },
                    "config_path": {
                        "type": "string",
                        "default": "config/mcp_config.yaml",
                        "description": "Path to MCP config YAML for LLM settings.",
                    },
                },
            },
        )

    def _get_llm_client(self, config_path: str | None = None) -> LLMClient:
        """Get or create the LLM client."""
        if self._llm_client is not None:
            return self._llm_client
        if config_path:
            return LLMClient.from_config_file(config_path)
        return LLMClient()

    async def run(
        self,
        output: str = "./agent_export",
        limit: int | None = None,
        force: bool = False,
        config_path: str | None = None,
        **kwargs: Any,
    ) -> CleanResult:
        """Execute the clean pipeline.

        Reads raw/*.json, sends each to LLM, writes processed/*.md.
        Skips sessions that already have processed/*.md unless force=True.
        """
        paths = ExportPaths(output)
        raw_dir = paths.raw_dir
        processed_dir = paths.processed_dir

        if not raw_dir.exists():
            raise RuntimeError(f"Raw directory not found: {raw_dir}. Run 'export' first.")

        processed_dir.mkdir(parents=True, exist_ok=True)

        # Find raw session files
        raw_files = sorted(raw_dir.glob("*.json"))
        result = CleanResult(total_sessions=len(raw_files))
        error_details: list[str] = []

        # Load system prompt
        system_prompt = load_clean_prompt()

        # Get LLM client
        owns_client = self._llm_client is None
        llm_client = self._get_llm_client(config_path)

        try:
            for raw_file in raw_files:
                session_id = raw_file.stem

                # Skip if already cleaned (unless force)
                processed_file = paths.processed_session_path(session_id)
                if processed_file.exists() and not force:
                    result.skipped += 1
                    continue

                # Apply limit (only counts sessions we actually process)
                if limit is not None and (result.cleaned + result.trash + result.errors) >= limit:
                    break

                try:
                    # Load raw session
                    session = RawSession.from_json_file(raw_file)

                    # Build user prompt
                    user_prompt = session_to_user_prompt(session)

                    # Call LLM
                    llm_response = await llm_client.chat(system_prompt, user_prompt)
                    result.llm_model = llm_response.model

                    # Parse response
                    parsed = parse_llm_response(llm_response.content)
                    quality = parsed["quality"]
                    title = parsed["title"]
                    markdown = parsed["markdown"]

                    result.quality_counts[quality] = result.quality_counts.get(quality, 0) + 1

                    if quality == "trash":
                        result.trash += 1
                        continue

                    # Build and write processed markdown
                    final_md = build_processed_markdown(quality, title, markdown, session)
                    processed_file.write_text(final_md, encoding="utf-8")
                    result.cleaned += 1

                except Exception as e:
                    result.errors += 1
                    error_details.append(f"{session_id}: {e}")

        finally:
            if owns_client:
                await llm_client.close()

        result.error_details = error_details
        return result
