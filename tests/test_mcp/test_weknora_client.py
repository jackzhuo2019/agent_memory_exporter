"""Tests for the WeKnora REST client."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent_memory_mcp.weknora_client import (
    KnowledgeBase,
    UploadResult,
    WeKnoraClient,
    WeKnoraConfig,
)


class TestWeKnoraConfig:
    def test_defaults(self):
        config = WeKnoraConfig()
        assert config.base_url == "http://localhost:8088/api/v1"
        assert config.api_key_env == "WEKNORA_API_KEY"
        assert config.default_kb_id == ""

    def test_custom_config(self):
        config = WeKnoraConfig(
            base_url="http://example.com/api/v1",
            api_key_env="MY_KEY",
            default_kb_id="kb-123",
        )
        assert config.base_url == "http://example.com/api/v1"
        assert config.default_kb_id == "kb-123"


class TestWeKnoraClient:
    def test_api_key_from_env(self):
        client = WeKnoraClient(api_key="secret-key")
        assert client._get_api_key() == "secret-key"

    def test_api_key_from_env_var(self, monkeypatch):
        monkeypatch.setenv("TEST_WEKNORA_KEY", "env-key")
        config = WeKnoraConfig(api_key_env="TEST_WEKNORA_KEY")
        client = WeKnoraClient(config)
        assert client._get_api_key() == "env-key"

    def test_no_api_key(self, monkeypatch):
        monkeypatch.delenv("WEKNORA_API_KEY", raising=False)
        client = WeKnoraClient()
        assert client._get_api_key() == ""

    def test_headers_with_key(self):
        client = WeKnoraClient(api_key="secret")
        headers = client._get_headers()
        assert headers["Authorization"] == "Bearer secret"

    def test_headers_without_key(self, monkeypatch):
        monkeypatch.delenv("WEKNORA_API_KEY", raising=False)
        client = WeKnoraClient()
        headers = client._get_headers()
        assert "Authorization" not in headers

    def test_from_config_file_not_found(self, tmp_path):
        client = WeKnoraClient.from_config_file(tmp_path / "nonexistent.yaml")
        assert client.config.base_url == "http://localhost:8088/api/v1"

    def test_from_config_file_with_weknora_section(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "weknora:\n  base_url: 'http://172.17.20.109:8088/api/v1'\n  default_kb_id: 'kb-test'\n  api_key_env: 'MY_KEY'\n",
            encoding="utf-8",
        )
        client = WeKnoraClient.from_config_file(config_path)
        assert client.config.base_url == "http://172.17.20.109:8088/api/v1"
        assert client.config.default_kb_id == "kb-test"
        assert client.config.api_key_env == "MY_KEY"

    def test_from_config_file_no_weknora_section(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("export:\n  root_dir: './agent_export'\n", encoding="utf-8")
        client = WeKnoraClient.from_config_file(config_path)
        assert client.config.base_url == "http://localhost:8088/api/v1"


class TestWeKnoraClientAPI:
    """Test API methods with mocked httpx."""

    @pytest.fixture
    def mock_response(self):
        """Build a mock httpx.Response."""
        def _make(status_code=200, json_data=None, text=""):
            resp = MagicMock()
            resp.status_code = status_code
            resp.text = text or ""
            if json_data is not None:
                resp.json.return_value = json_data
            return resp
        return _make

    def test_list_knowledge_bases(self, mock_response):
        client = WeKnoraClient(api_key="test")
        client._client = MagicMock()
        client._client.get = AsyncMock(
            return_value=mock_response(
                200,
                json_data=[
                    {"id": "kb-1", "name": "Source-KB", "description": "Raw sessions"},
                    {"id": "kb-2", "name": "Auto-Wiki", "description": "Distilled"},
                ],
            )
        )

        result = asyncio.run(client.list_knowledge_bases())
        assert len(result) == 2
        assert result[0].id == "kb-1"
        assert result[0].name == "Source-KB"
        assert result[1].id == "kb-2"

    def test_list_knowledge_bases_error(self, mock_response):
        client = WeKnoraClient(api_key="test")
        client._client = MagicMock()
        client._client.get = AsyncMock(
            return_value=mock_response(401, text="Unauthorized")
        )

        with pytest.raises(RuntimeError, match="list_knowledge_bases failed"):
            asyncio.run(client.list_knowledge_bases())

    def test_upload_file_success(self, tmp_path, mock_response):
        # Create a test file
        test_file = tmp_path / "test.md"
        test_file.write_text("# Test\n\nContent", encoding="utf-8")

        client = WeKnoraClient(api_key="test")
        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=mock_response(201, json_data={"id": "knowledge-123"})
        )

        result = asyncio.run(client.upload_file("kb-1", test_file))
        assert result.success is True
        assert result.knowledge_id == "knowledge-123"
        assert result.status_code == 201

    def test_upload_file_not_found(self):
        client = WeKnoraClient(api_key="test")
        result = asyncio.run(client.upload_file("kb-1", "/nonexistent/file.md"))
        assert result.success is False
        assert "File not found" in result.error

    def test_upload_file_server_error(self, tmp_path, mock_response):
        test_file = tmp_path / "test.md"
        test_file.write_text("# Test", encoding="utf-8")

        client = WeKnoraClient(api_key="test")
        client._client = MagicMock()
        client._client.post = AsyncMock(
            return_value=mock_response(500, text="Internal Server Error")
        )

        result = asyncio.run(client.upload_file("kb-1", test_file))
        assert result.success is False
        assert result.status_code == 500

    def test_list_knowledge(self, mock_response):
        client = WeKnoraClient(api_key="test")
        client._client = MagicMock()
        client._client.get = AsyncMock(
            return_value=mock_response(
                200,
                json_data=[
                    {"id": "k-1", "name": "doc1", "status": "ready"},
                    {"id": "k-2", "name": "doc2", "status": "processing"},
                ],
            )
        )

        result = asyncio.run(client.list_knowledge("kb-1"))
        assert len(result) == 2
        assert result[0].id == "k-1"
        assert result[0].kb_id == "kb-1"

    def test_get_knowledge(self, mock_response):
        client = WeKnoraClient(api_key="test")
        client._client = MagicMock()
        client._client.get = AsyncMock(
            return_value=mock_response(200, json_data={"id": "k-1", "name": "doc1"})
        )

        result = asyncio.run(client.get_knowledge("k-1"))
        assert result["id"] == "k-1"
        assert result["name"] == "doc1"

    def test_delete_knowledge_success(self, mock_response):
        client = WeKnoraClient(api_key="test")
        client._client = MagicMock()
        client._client.delete = AsyncMock(return_value=mock_response(204))

        result = asyncio.run(client.delete_knowledge("k-1"))
        assert result is True

    def test_delete_knowledge_failure(self, mock_response):
        client = WeKnoraClient(api_key="test")
        client._client = MagicMock()
        client._client.delete = AsyncMock(return_value=mock_response(404, text="Not Found"))

        result = asyncio.run(client.delete_knowledge("k-1"))
        assert result is False

    def test_close(self):
        client = WeKnoraClient(api_key="test")
        client._client = MagicMock()
        client._client.aclose = AsyncMock()
        asyncio.run(client.close())
        # After close, _client is set to None
        assert client._client is None
