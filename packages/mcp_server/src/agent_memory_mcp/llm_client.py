"""OpenAI-compatible LLM client for the MCP server.

Supports any endpoint that implements the OpenAI Chat Completions API:
    POST {base_url}/chat/completions
    {
        "model": "...",
        "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
        "temperature": 0.0
    }

Configuration is loaded from config/mcp_config.yaml (llm section) or passed
directly to the constructor for testing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel


class LLMConfig(BaseModel):
    """Configuration for the LLM client."""

    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "LLM_API_KEY"
    temperature: float = 0.0
    max_tokens: int | None = None
    timeout: float = 120.0


class LLMResponse(BaseModel):
    """Response from the LLM."""

    content: str
    model: str
    usage: dict[str, int] = {}


class LLMClient:
    """Async client for OpenAI-compatible chat completions."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        if config is None:
            config = LLMConfig()
        if base_url:
            config.base_url = base_url
        if model:
            config.model = model
        self.config = config
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_config_file(cls, config_path: str | Path) -> LLMClient:
        """Load LLM config from a YAML file (mcp_config.yaml).

        Expected structure:
            llm:
              base_url: "https://api.openai.com/v1"
              model: "gpt-4o-mini"
              api_key_env: "LLM_API_KEY"
              temperature: 0.0
        """
        config_path = Path(config_path)
        if not config_path.exists():
            return cls()
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        llm_data = data.get("llm") or {}
        if not llm_data:
            return cls()
        config = LLMConfig(
            base_url=llm_data.get("base_url", "https://api.openai.com/v1"),
            model=llm_data.get("model", "gpt-4o-mini"),
            api_key_env=llm_data.get("api_key_env", "LLM_API_KEY"),
            temperature=llm_data.get("temperature", 0.0),
            max_tokens=llm_data.get("max_tokens"),
            timeout=llm_data.get("timeout", 120.0),
        )
        return cls(config)

    def _get_api_key(self) -> str:
        """Resolve API key from constructor arg or environment variable."""
        if self._api_key:
            return self._api_key
        return os.environ.get(self.config.api_key_env, "")

    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send a chat completion request and return the response.

        Args:
            system_prompt: System message content.
            user_prompt: User message content.
            temperature: Override config temperature.
            max_tokens: Override config max_tokens.

        Raises:
            RuntimeError: If API key is missing or the request fails.
        """
        api_key = self._get_api_key()
        if not api_key:
            raise RuntimeError(
                f"LLM API key not found. Set {self.config.api_key_env} env var "
                f"or pass api_key to LLMClient."
            )

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout)

        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        elif self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        resp = await self._client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(
                f"LLM request failed ({resp.status_code}): {resp.text[:500]}"
            )

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        model = data.get("model", self.config.model)
        usage = data.get("usage", {})

        return LLMResponse(content=content, model=model, usage=usage)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
