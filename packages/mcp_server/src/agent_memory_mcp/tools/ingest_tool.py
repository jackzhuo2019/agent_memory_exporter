"""MCP ingest tool: upload processed/*.md to WeKnora Source-KB.

Reads processed/*.md files (only high/medium quality), uploads to a WeKnora
knowledge base via HTTP API, and tracks upload state in upload_state.json.

Only high/medium quality files are ingested. Low and trash are skipped.
Failed uploads are retried up to MAX_RETRIES (3) times on subsequent runs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from mcp import Tool
from pydantic import BaseModel, Field

from agent_memory_shared.paths import ExportPaths
from agent_memory_mcp.upload_state import (
    MAX_RETRIES,
    UploadState,
    can_retry,
    is_uploaded,
    load_upload_state,
    record_failure,
    record_skip,
    record_success,
    save_upload_state,
)
from agent_memory_mcp.weknora_client import WeKnoraClient


# Quality levels that are ingested to Source-KB
INGESTIBLE_QUALITIES = {"high", "medium"}


def parse_front_matter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML front-matter from a markdown file.

    Returns (front_matter_dict, body) where front_matter_dict has keys
    like quality, source, session_id, cleaned_at, title.
    If no front-matter, returns ({}, content).
    """
    if not content.startswith("---"):
        return {}, content

    # Find the closing ---
    lines = content.split("\n")
    if len(lines) < 2:
        return {}, content

    # Find closing --- (starting from line 1, since line 0 is opening ---)
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, content

    front_matter_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])

    try:
        front_matter = yaml.safe_load(front_matter_text)
        if not isinstance(front_matter, dict):
            return {}, content
    except yaml.YAMLError:
        return {}, content

    return front_matter, body


class IngestResult(BaseModel):
    """Result of the ingest tool."""

    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    total_files: int = 0
    kb_id: str = ""
    error_details: list[str] = Field(default_factory=list)
    quality_filtered: int = 0  # Files skipped due to low/trash quality

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


class IngestTool:
    """MCP tool that uploads processed markdown to WeKnora.

    Parameters:
        kb_id: WeKnora knowledge base ID (required, or use config default)
        output: output directory containing processed/ (default: "./agent_export")
        limit: maximum number of files to ingest (default: none)
        force: re-ingest files that already have uploaded status (default: false)
        dry_run: preview without uploading (default: false)
        config_path: path to mcp_config.yaml (default: config/mcp_config.yaml)
    """

    def __init__(self, weknora_client: WeKnoraClient | None = None) -> None:
        self._weknora_client = weknora_client

    def get_tool_definition(self) -> Tool:
        return Tool(
            name="ingest",
            description=(
                "Upload cleaned agent session markdown files (processed/*.md) "
                "to a WeKnora knowledge base. Only high/medium quality files "
                "are ingested; low/trash are skipped. Tracks upload state in "
                "upload_state.json for idempotent retries (max 3 attempts)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "kb_id": {
                        "type": "string",
                        "description": "WeKnora knowledge base ID. Required if no default in config.",
                    },
                    "output": {
                        "type": "string",
                        "default": "./agent_export",
                        "description": "Output directory containing processed/ subdir.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of files to ingest.",
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": "Re-ingest files that already have uploaded status.",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "Preview without uploading. Reports what would be ingested.",
                    },
                    "config_path": {
                        "type": "string",
                        "default": "config/mcp_config.yaml",
                        "description": "Path to MCP config YAML for WeKnora settings.",
                    },
                },
            },
        )

    def _get_weknora_client(self, config_path: str | None = None, kb_id: str | None = None) -> WeKnoraClient:
        """Get or create the WeKnora client."""
        if self._weknora_client is not None:
            client = self._weknora_client
            if kb_id:
                client.config.default_kb_id = kb_id
            return client
        if config_path:
            client = WeKnoraClient.from_config_file(config_path)
        else:
            client = WeKnoraClient()
        if kb_id:
            client.config.default_kb_id = kb_id
        return client

    async def run(
        self,
        kb_id: str = "",
        output: str = "./agent_export",
        limit: int | None = None,
        force: bool = False,
        dry_run: bool = False,
        config_path: str | None = None,
        **kwargs: Any,
    ) -> IngestResult:
        """Execute the ingest pipeline.

        Reads processed/*.md, filters by quality, uploads to WeKnora.
        """
        paths = ExportPaths(output)
        processed_dir = paths.processed_dir

        if not processed_dir.exists():
            raise RuntimeError(
                f"Processed directory not found: {processed_dir}. Run 'clean' first."
            )

        # Resolve KB ID
        owns_client = self._weknora_client is None
        weknora = self._get_weknora_client(config_path, kb_id if kb_id else None)
        effective_kb_id = kb_id or weknora.config.default_kb_id
        if not effective_kb_id:
            raise RuntimeError(
                "No kb_id provided. Set kb_id parameter or default_kb_id in config."
            )

        # Load upload state
        state = load_upload_state(paths.upload_state_file)

        # Find processed files
        md_files = sorted(processed_dir.glob("*.md"))
        result = IngestResult(total_files=len(md_files), kb_id=effective_kb_id)
        error_details: list[str] = []

        try:
            for md_file in md_files:
                session_id = md_file.stem

                # Parse front-matter to get quality
                content = md_file.read_text(encoding="utf-8")
                front_matter, _ = parse_front_matter(content)
                quality = str(front_matter.get("quality", "")).lower().strip()

                # Filter by quality (only high/medium)
                if quality not in INGESTIBLE_QUALITIES:
                    result.quality_filtered += 1
                    record_skip(state, session_id, md_file.name, quality or "unknown")
                    continue

                # Skip already uploaded (unless force)
                if not force and is_uploaded(state, session_id):
                    result.skipped += 1
                    continue

                # Skip if max retries exceeded
                if not can_retry(state, session_id):
                    result.skipped += 1
                    error_details.append(f"{session_id}: max retries ({MAX_RETRIES}) exceeded")
                    continue

                # Apply limit (only counts files we actually attempt)
                if limit is not None and (result.ingested + result.failed) >= limit:
                    result.skipped += 1
                    continue

                # Dry run — just count
                if dry_run:
                    result.ingested += 1
                    continue

                # Upload to WeKnora
                try:
                    upload = await weknora.upload_file(effective_kb_id, md_file)
                    if upload.success:
                        record_success(
                            state, session_id, md_file.name, quality, upload.knowledge_id
                        )
                        result.ingested += 1
                    else:
                        record_failure(state, session_id, md_file.name, quality, upload.error)
                        result.failed += 1
                        error_details.append(f"{session_id}: {upload.error}")
                except Exception as e:
                    record_failure(state, session_id, md_file.name, quality, str(e))
                    result.failed += 1
                    error_details.append(f"{session_id}: {e}")

        finally:
            # Save state regardless of success/failure
            save_upload_state(state, paths.upload_state_file)
            if owns_client:
                await weknora.close()

        result.error_details = error_details
        return result
