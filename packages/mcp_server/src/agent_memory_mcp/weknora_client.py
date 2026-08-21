"""WeKnora REST client for knowledge base operations.

Calls the WeKnora HTTP API directly (not via MCP) to avoid double-hop.

Endpoints used:
    GET  /knowledge-bases                          — list KBs
    POST /knowledge-bases/{kb_id}/knowledge/file   — upload file (multipart)
    GET  /knowledge-bases/{kb_id}/knowledge        — list knowledge (paginated)
    GET  /knowledge/{knowledge_id}                 — get knowledge detail
    DELETE /knowledge/{knowledge_id}               — delete knowledge
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel


class WeKnoraConfig(BaseModel):
    """Configuration for the WeKnora client."""

    base_url: str = "http://localhost:8088/api/v1"
    api_key_env: str = "WEKNORA_API_KEY"
    default_kb_id: str = ""
    timeout: float = 60.0


class KnowledgeBase(BaseModel):
    """A WeKnora knowledge base."""

    id: str
    name: str = ""
    description: str = ""


class KnowledgeItem(BaseModel):
    """A knowledge item in a WeKnora KB."""

    id: str
    name: str = ""
    kb_id: str = ""
    status: str = ""


class UploadResult(BaseModel):
    """Result of a file upload to WeKnora."""

    knowledge_id: str = ""
    success: bool = True
    status_code: int = 200
    error: str = ""


class WeKnoraClient:
    """Async client for the WeKnora REST API."""

    def __init__(
        self,
        config: WeKnoraConfig | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        kb_id: str | None = None,
    ) -> None:
        if config is None:
            config = WeKnoraConfig()
        if base_url:
            config.base_url = base_url
        if kb_id:
            config.default_kb_id = kb_id
        self.config = config
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_config_file(cls, config_path: str | Path) -> WeKnoraClient:
        """Load WeKnora config from a YAML file (mcp_config.yaml).

        Expected structure:
            weknora:
              base_url: "http://172.17.20.109:8088/api/v1"
              api_key_env: "WEKNORA_API_KEY"
              default_kb_id: "..."
        """
        config_path = Path(config_path)
        if not config_path.exists():
            return cls()
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        weknora_data = data.get("weknora") or {}
        if not weknora_data:
            return cls()
        config = WeKnoraConfig(
            base_url=weknora_data.get("base_url", "http://localhost:8088/api/v1"),
            api_key_env=weknora_data.get("api_key_env", "WEKNORA_API_KEY"),
            default_kb_id=weknora_data.get("default_kb_id", ""),
            timeout=weknora_data.get("timeout", 60.0),
        )
        return cls(config)

    def _get_api_key(self) -> str:
        """Resolve API key from constructor arg or environment variable."""
        if self._api_key:
            return self._api_key
        return os.environ.get(self.config.api_key_env, "")

    def _get_headers(self) -> dict[str, str]:
        """Build request headers with auth."""
        api_key = self._get_api_key()
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout)
        return self._client

    async def list_knowledge_bases(self) -> list[KnowledgeBase]:
        """List all knowledge bases.

        Returns:
            List of KnowledgeBase objects.
        """
        client = await self._ensure_client()
        url = f"{self.config.base_url.rstrip('/')}/knowledge-bases"
        resp = await client.get(url, headers=self._get_headers())
        if resp.status_code != 200:
            raise RuntimeError(
                f"list_knowledge_bases failed ({resp.status_code}): {resp.text[:500]}"
            )
        data = resp.json()
        items = data if isinstance(data, list) else data.get("items", data.get("data", []))
        return [
            KnowledgeBase(
                id=str(item.get("id", "")),
                name=item.get("name", ""),
                description=item.get("description", ""),
            )
            for item in items
        ]

    async def upload_file(
        self,
        kb_id: str,
        file_path: str | Path,
        *,
        enable_multimodel: bool = False,
    ) -> UploadResult:
        """Upload a file to a WeKnora knowledge base.

        Args:
            kb_id: Target knowledge base ID.
            file_path: Path to the file to upload.
            enable_multimodel: Whether to enable multimodel processing.

        Returns:
            UploadResult with knowledge_id and success status.
        """
        client = await self._ensure_client()
        url = f"{self.config.base_url.rstrip('/')}/knowledge-bases/{kb_id}/knowledge/file"
        file_path = Path(file_path)

        if not file_path.exists():
            return UploadResult(success=False, status_code=0, error=f"File not found: {file_path}")

        headers = self._get_headers()
        # Don't set Content-Type for multipart — httpx sets it with boundary automatically
        try:
            with open(file_path, "rb") as f:
                files = {"file": (file_path.name, f, "text/markdown")}
                data: dict[str, Any] = {}
                if enable_multimodel:
                    data["enable_multimodel"] = "true"
                resp = await client.post(url, headers=headers, files=files, data=data)
        except Exception as e:
            return UploadResult(success=False, status_code=0, error=str(e))

        if resp.status_code not in (200, 201):
            return UploadResult(
                success=False,
                status_code=resp.status_code,
                error=resp.text[:500],
            )

        resp_data = resp.json()
        knowledge_id = str(resp_data.get("id", resp_data.get("knowledge_id", "")))
        return UploadResult(knowledge_id=knowledge_id, success=True, status_code=resp.status_code)

    async def list_knowledge(self, kb_id: str, page: int = 1, page_size: int = 50) -> list[KnowledgeItem]:
        """List knowledge items in a KB (paginated)."""
        client = await self._ensure_client()
        url = f"{self.config.base_url.rstrip('/')}/knowledge-bases/{kb_id}/knowledge"
        params = {"page": page, "page_size": page_size}
        resp = await client.get(url, headers=self._get_headers(), params=params)
        if resp.status_code != 200:
            raise RuntimeError(
                f"list_knowledge failed ({resp.status_code}): {resp.text[:500]}"
            )
        data = resp.json()
        items = data if isinstance(data, list) else data.get("items", data.get("data", []))
        return [
            KnowledgeItem(
                id=str(item.get("id", "")),
                name=item.get("name", ""),
                kb_id=kb_id,
                status=item.get("status", ""),
            )
            for item in items
        ]

    async def get_knowledge(self, knowledge_id: str) -> dict[str, Any]:
        """Get knowledge item detail by ID."""
        client = await self._ensure_client()
        url = f"{self.config.base_url.rstrip('/')}/knowledge/{knowledge_id}"
        resp = await client.get(url, headers=self._get_headers())
        if resp.status_code != 200:
            raise RuntimeError(
                f"get_knowledge failed ({resp.status_code}): {resp.text[:500]}"
            )
        return resp.json()

    async def delete_knowledge(self, knowledge_id: str) -> bool:
        """Delete a knowledge item by ID."""
        client = await self._ensure_client()
        url = f"{self.config.base_url.rstrip('/')}/knowledge/{knowledge_id}"
        resp = await client.delete(url, headers=self._get_headers())
        return resp.status_code in (200, 204)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> WeKnoraClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
